package model

import (
	"context"
	"testing"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func TestRelaySchemaSQLiteFreshV8AndV7UpgradeCreateImmutableProviderCostEvidence(t *testing.T) {
	t.Run("fresh v8", func(t *testing.T) {
		database := newRelaySchemaSQLite(t)
		result, err := RunRelaySchemaMigrations(context.Background(), "")
		require.NoError(t, err)
		require.Equal(t, relaySchemaV8FrozenVersion, result.Status.BaselineVersion)
		require.Equal(t, relaySchemaV8FrozenVersion, result.Status.CurrentVersion)
		relaySchemaV8RequireProviderCostEvidence(t, database)
		var ledger []RelaySchemaMigration
		require.NoError(t, database.Order("version ASC").Find(&ledger).Error)
		require.Len(t, ledger, 1)
		require.Equal(t, relaySchemaV8FrozenVersion, ledger[0].Version)
	})

	t.Run("exact v7 upgrade", func(t *testing.T) {
		database := newRelaySchemaSQLite(t)
		require.NoError(t, ensureRelaySchemaMetadata(database))
		definitions := relaySchemaMigrations()
		require.Len(t, definitions, 8)
		v7Attempt := uuid.NewString()
		require.NoError(t, markRelaySchemaApplying(database, definitions[6], v7Attempt))
		require.NoError(t, runRelaySchemaBootstrapTransaction(database, definitions[:7], definitions[6], v7Attempt))
		require.False(t, database.Migrator().HasTable(&PlatformProviderCostAllocationEvidence{}),
			"the frozen v7 release must not absorb the v8 provider-cost ledger")

		result, err := RunRelaySchemaMigrations(context.Background(), "")
		require.NoError(t, err)
		require.Equal(t, relaySchemaV7FrozenVersion, result.FromVersion)
		require.Equal(t, relaySchemaV8FrozenVersion, result.ToVersion)
		relaySchemaV8RequireProviderCostEvidence(t, database)
		var ledger []RelaySchemaMigration
		require.NoError(t, database.Order("version ASC").Find(&ledger).Error)
		require.Len(t, ledger, 2)
		require.Equal(t, relaySchemaV7FrozenVersion, ledger[0].Version)
		require.Equal(t, relaySchemaV8FrozenVersion, ledger[1].Version)
	})
}

func TestRelaySchemaV8IncrementalRejectsAnythingExceptExactApplyingV7(t *testing.T) {
	database := newRelaySchemaSQLite(t)
	require.NoError(t, ensureRelaySchemaMetadata(database))
	require.ErrorContains(t, migrateRelaySchemaV8ProviderCostAllocationEvidence(database), "exact v7 state")

	definitions := relaySchemaMigrations()
	v7Attempt := uuid.NewString()
	require.NoError(t, markRelaySchemaApplying(database, definitions[6], v7Attempt))
	require.NoError(t, runRelaySchemaBootstrapTransaction(database, definitions[:7], definitions[6], v7Attempt))
	v8Attempt := uuid.NewString()
	require.NoError(t, markRelaySchemaApplying(database, definitions[7], v8Attempt))
	require.NoError(t, database.AutoMigrate(&PlatformProviderCostAllocationEvidence{}))
	require.ErrorContains(t, migrateRelaySchemaV8ProviderCostAllocationEvidence(database), "already exists")
}

func relaySchemaV8RequireProviderCostEvidence(t *testing.T, database *gorm.DB) {
	t.Helper()
	require.True(t, database.Migrator().HasTable(&PlatformProviderCostAllocationEvidence{}))
	for _, trigger := range []string{
		"trg_platform_provider_cost_allocation_evidence_no_update",
		"trg_platform_provider_cost_allocation_evidence_no_delete",
	} {
		var count int64
		require.NoError(t, database.Raw(
			`SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name = ?`, trigger,
		).Scan(&count).Error)
		require.Equal(t, int64(1), count, "missing v8 trigger %s", trigger)
	}
	evidence := platformProviderCostEvidenceFixture(t)
	created, err := CreatePlatformProviderCostAllocationEvidenceTx(database, &evidence)
	require.NoError(t, err)
	require.True(t, created)
	require.Error(t, database.Exec(
		"UPDATE platform_provider_cost_allocation_evidence SET amount_cents = amount_cents + 1 WHERE cost_event_id = ?",
		evidence.CostEventID,
	).Error)
	require.Error(t, database.Exec(
		"DELETE FROM platform_provider_cost_allocation_evidence WHERE cost_event_id = ?", evidence.CostEventID,
	).Error)
}
