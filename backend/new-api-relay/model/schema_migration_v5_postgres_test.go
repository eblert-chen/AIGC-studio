package model

import (
	"errors"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

// TestRelaySchemaPostgresPinnedV4ReleaseFixture verifies the complete output of
// the immutable v4 binary before current source is allowed to execute v4->v5.
// The release gate supplies a disposable database that it first advanced from
// exact v3 with that binary. This test never bootstraps historical v4 from
// current source and never reads or exports a live application database.
func TestRelaySchemaPostgresPinnedV4ReleaseFixture(t *testing.T) {
	dsn := strings.TrimSpace(os.Getenv("TEST_RELAY_SCHEMA_V4_RELEASE_DSN"))
	if dsn == "" {
		t.Skip("set TEST_RELAY_SCHEMA_V4_RELEASE_DSN to verify the pinned PostgreSQL v4 release fixture")
	}
	expectedSourceRevision := strings.TrimSpace(os.Getenv("TEST_RELAY_SCHEMA_V4_SOURCE_REVISION"))
	expectedSnapshotSHA256 := strings.TrimSpace(os.Getenv("TEST_RELAY_SCHEMA_V4_SOURCE_SNAPSHOT_SHA256"))
	require.True(t, relaySchemaProvenanceValid(expectedSourceRevision, expectedSnapshotSHA256),
		"the pinned v4 fixture must carry exact immutable source provenance")

	db, err := gorm.Open(postgres.Open(dsn), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	sqlDB, err := db.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = sqlDB.Close() })

	common.SetMainDatabaseType(common.DatabaseTypePostgreSQL)
	t.Cleanup(func() { common.SetMainDatabaseType(common.DatabaseTypeSQLite) })
	t.Setenv(relaySchemaOwnerRoleEnvironment, relaySchemaTestOwnerRole)
	t.Setenv(relayMigrationDatabaseRoleEnvironment, relaySchemaTestMigratorRole)
	t.Setenv(relayRuntimeDatabaseRoleEnvironment, relaySchemaTestRuntimeRole)

	actualCatalog, err := getRelaySchemaCatalogFingerprintForVersion(db, relaySchemaV4FrozenVersion)
	require.NoError(t, err)
	require.Equal(t, relaySchemaV4PostgresCatalogSHA256, actualCatalog)

	var state RelaySchemaState
	require.NoError(t, db.Where("id = ?", relaySchemaStateSingletonID).First(&state).Error)
	require.Equal(t, relaySchemaStateSingletonID, state.ID)
	require.Equal(t, int64(1), state.BaselineVersion)
	require.False(t, state.FreshBootstrap)
	require.Equal(t, relaySchemaV4FrozenVersion, state.CurrentVersion)
	require.Equal(t, relaySchemaV4FrozenVersion, state.TargetVersion)
	require.Equal(t, RelaySchemaStateClean, state.State)
	require.False(t, state.Dirty)
	_, err = uuid.Parse(state.AttemptID)
	require.NoError(t, err)
	require.Equal(t, relaySchemaV4FrozenChecksumSHA256, state.CurrentChecksum)
	require.Equal(t, relaySchemaV4FrozenChecksumSHA256, state.TargetChecksum)
	require.Equal(t, relaySchemaV4PostgresCatalogSHA256, state.CurrentCatalogSHA256)
	require.Equal(t, relaySchemaV4PostgresCatalogSHA256, state.TargetCatalogSHA256)
	require.Equal(t, expectedSourceRevision, state.SourceRevision)
	require.Equal(t, expectedSnapshotSHA256, state.SnapshotSHA256)
	require.NotNil(t, state.StartedAt)
	require.NotNil(t, state.FinishedAt)
	require.False(t, state.FinishedAt.Before(*state.StartedAt))
	require.Empty(t, state.ErrorCode)
	require.False(t, state.UpdatedAt.IsZero())

	definitions := relaySchemaMigrations()
	require.GreaterOrEqual(t, len(definitions), int(relaySchemaV4FrozenVersion))
	var ledger []RelaySchemaMigration
	require.NoError(t, db.Order("version ASC").Find(&ledger).Error)
	require.Len(t, ledger, int(relaySchemaV4FrozenVersion))
	for index, row := range ledger {
		definition := definitions[index]
		require.Equal(t, int64(index+1), row.Version)
		require.Equal(t, definition.Name, row.Name)
		require.Equal(t, definition.Phase, row.Phase)
		require.Equal(t, definition.Checksum, row.Checksum)
		require.Equal(t, expectedRelaySchemaCatalogFingerprint("postgres", row.Version), row.CatalogSHA256)
		require.False(t, row.AppliedAt.IsZero())
		require.True(t, relaySchemaProvenanceValid(row.SourceRevision, row.SnapshotSHA256))
	}
	v4Receipt := ledger[len(ledger)-1]
	require.Equal(t, expectedSourceRevision, v4Receipt.SourceRevision)
	require.Equal(t, expectedSnapshotSHA256, v4Receipt.SnapshotSHA256)

	// The frozen v4 migration must have installed the exact runtime and
	// download-edge least-privilege surfaces. The edge remains NOLOGIN until the
	// later post-migration provisioner explicitly attaches its password.
	require.NoError(t, verifyRelayRuntimeDatabasePrivilegeManifest(
		db, relaySchemaTestRuntimeRole, relaySchemaV4FrozenVersion,
	))
	require.NoError(t, verifyRelayDownloadEdgeCurrentDatabaseRole(
		db, relaySchemaV4FrozenVersion, false,
	))

	// Exercise both immutable guard classes against the exact v4 database.
	// PostgreSQL aborts each inner transaction, so no fixture row is changed.
	var route PlatformGenerationProviderRoute
	require.NoError(t, db.Order("id ASC").First(&route).Error)
	require.Error(t, db.Transaction(func(probe *gorm.DB) error {
		return probe.Model(&PlatformGenerationProviderRoute{}).
			Where("id = ?", route.ID).
			Update("capability_profile_id", "tampered-partial-profile").Error
	}))
	require.Error(t, db.Transaction(func(probe *gorm.DB) error {
		return probe.Model(&RelaySchemaMigration{}).
			Where("version = ?", relaySchemaV4FrozenVersion).
			Update("name", "tampered-v4-receipt").Error
	}))
}

// TestRelaySchemaPostgresV4ToV5DiagnosticTaxonomy proves the isolated
// incremental boundary on a real PostgreSQL catalog. Its DSN must already be
// an exact database produced by the pinned immutable v4 binary. The complete
// test runs in a rollback-only transaction so it cannot rewrite that fixture;
// the legacy release gate separately advances another exact v4 database with
// the current v5 binary and validates the committed ledger.
func TestRelaySchemaPostgresV4ToV5DiagnosticTaxonomy(t *testing.T) {
	dsn := strings.TrimSpace(os.Getenv("TEST_RELAY_SCHEMA_V5_POSTGRES_DSN"))
	if dsn == "" {
		t.Skip("set TEST_RELAY_SCHEMA_V5_POSTGRES_DSN to run the PostgreSQL v4-to-v5 migration gate")
	}
	db, err := gorm.Open(postgres.Open(dsn), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	sqlDB, err := db.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = sqlDB.Close() })

	common.SetMainDatabaseType(common.DatabaseTypePostgreSQL)
	t.Cleanup(func() { common.SetMainDatabaseType(common.DatabaseTypeSQLite) })
	tx := db.Begin()
	require.NoError(t, tx.Error)
	t.Cleanup(func() { _ = tx.Rollback().Error })

	v4Catalog, err := getRelaySchemaCatalogFingerprintForVersion(tx, relaySchemaV4FrozenVersion)
	require.NoError(t, err)
	require.Equal(t, relaySchemaV4PostgresCatalogSHA256, v4Catalog)
	var state RelaySchemaState
	require.NoError(t, tx.Where("id = ?", relaySchemaStateSingletonID).First(&state).Error)
	require.Equal(t, relaySchemaV4FrozenVersion, state.CurrentVersion)
	require.Equal(t, relaySchemaV4FrozenVersion, state.TargetVersion)
	require.Equal(t, RelaySchemaStateClean, state.State)
	require.False(t, state.Dirty)
	require.Equal(t, relaySchemaV4FrozenChecksumSHA256, state.CurrentChecksum)
	require.Equal(t, relaySchemaV4FrozenChecksumSHA256, state.TargetChecksum)
	require.Equal(t, relaySchemaV4PostgresCatalogSHA256, state.CurrentCatalogSHA256)
	require.Equal(t, relaySchemaV4PostgresCatalogSHA256, state.TargetCatalogSHA256)
	var ledger []RelaySchemaMigration
	require.NoError(t, tx.Order("version ASC").Find(&ledger).Error)
	require.NotEmpty(t, ledger)
	require.Equal(t, state.BaselineVersion, ledger[0].Version)
	require.Equal(t, relaySchemaV4FrozenVersion, ledger[len(ledger)-1].Version)
	for index, row := range ledger {
		require.Equal(t, state.BaselineVersion+int64(index), row.Version)
	}
	v4Receipt := ledger[len(ledger)-1]
	require.Equal(t, relaySchemaV4FrozenName, v4Receipt.Name)
	require.Equal(t, relaySchemaV4FrozenPhase, v4Receipt.Phase)
	require.Equal(t, relaySchemaV4FrozenChecksumSHA256, v4Receipt.Checksum)
	require.Equal(t, relaySchemaV4PostgresCatalogSHA256, v4Receipt.CatalogSHA256)

	now := time.Now().UTC()
	require.Equal(t, int64(1), tx.Model(&RelaySchemaState{}).
		Where("id = ?", relaySchemaStateSingletonID).
		Updates(map[string]any{
			"target_version":         relaySchemaV5FrozenVersion,
			"state":                  RelaySchemaStateApplying,
			"dirty":                  true,
			"attempt_id":             uuid.NewString(),
			"current_checksum":       relaySchemaV4FrozenChecksumSHA256,
			"target_checksum":        relaySchemaV5FrozenChecksumSHA256,
			"current_catalog_sha256": relaySchemaV4PostgresCatalogSHA256,
			"target_catalog_sha256":  relaySchemaV5PostgresCatalogSHA256,
			"started_at":             now,
			"finished_at":            nil,
			"error_code":             "",
			"updated_at":             now,
		}).RowsAffected)

	exactState := map[string]any{
		"current_version":        relaySchemaV4FrozenVersion,
		"target_version":         relaySchemaV5FrozenVersion,
		"current_checksum":       relaySchemaV4FrozenChecksumSHA256,
		"target_checksum":        relaySchemaV5FrozenChecksumSHA256,
		"current_catalog_sha256": relaySchemaV4PostgresCatalogSHA256,
		"target_catalog_sha256":  relaySchemaV5PostgresCatalogSHA256,
	}
	for _, test := range []struct {
		name   string
		column string
		value  any
	}{
		{name: "wrong current version", column: "current_version", value: relaySchemaV3FrozenVersion},
		{name: "wrong target version", column: "target_version", value: relaySchemaV4FrozenVersion},
		{name: "tampered current checksum", column: "current_checksum", value: "sha256:" + strings.Repeat("0", 64)},
		{name: "tampered target checksum", column: "target_checksum", value: "sha256:" + strings.Repeat("1", 64)},
		{name: "tampered current catalog receipt", column: "current_catalog_sha256", value: "sha256:" + strings.Repeat("2", 64)},
		{name: "tampered target catalog receipt", column: "target_catalog_sha256", value: "sha256:" + strings.Repeat("3", 64)},
	} {
		t.Run(test.name, func(t *testing.T) {
			require.NoError(t, tx.Model(&RelaySchemaState{}).
				Where("id = ?", relaySchemaStateSingletonID).Update(test.column, test.value).Error)
			require.Error(t, migrateRelaySchemaV5ChannelTestDiagnosticTaxonomy(tx))
			actual, fingerprintErr := getRelaySchemaCatalogFingerprintForVersion(tx, relaySchemaV4FrozenVersion)
			require.NoError(t, fingerprintErr)
			require.Equal(t, relaySchemaV4PostgresCatalogSHA256, actual,
				"a non-exact receipt must fail before changing the v4 catalog")
			require.NoError(t, tx.Model(&RelaySchemaState{}).
				Where("id = ?", relaySchemaStateSingletonID).Updates(exactState).Error)
		})
	}

	// A forged live guard must fail the independent v4 catalog attestation even
	// when every state and ledger field still looks exact. Roll back the probe
	// savepoint instead of asking current source to reconstruct historical v4.
	tamperRollback := errors.New("rollback forged v5 guard probe")
	err = tx.Transaction(func(probe *gorm.DB) error {
		require.NoError(t, installPlatformChannelControlDiagnosticTaxonomyV5WithDB(probe))
		require.Error(t, migrateRelaySchemaV5ChannelTestDiagnosticTaxonomy(probe))
		return tamperRollback
	})
	require.ErrorIs(t, err, tamperRollback)
	restoredV4Catalog, err := getRelaySchemaCatalogFingerprintForVersion(tx, relaySchemaV4FrozenVersion)
	require.NoError(t, err)
	require.Equal(t, relaySchemaV4PostgresCatalogSHA256, restoredV4Catalog)

	require.NoError(t, migrateRelaySchemaV5ChannelTestDiagnosticTaxonomy(tx))
	v5Catalog, err := getRelaySchemaCatalogFingerprintForVersion(tx, relaySchemaV5FrozenVersion)
	require.NoError(t, err)
	require.Equal(t, relaySchemaV5PostgresCatalogSHA256, v5Catalog)

	newDiagnostic := relaySchemaV5PendingChannelTestReceipt("new-diagnostic")
	require.NoError(t, tx.Create(&newDiagnostic).Error)
	relaySchemaV5RequireRejectedStatement(t, tx, func(probe *gorm.DB) error {
		return relaySchemaV5CompleteChannelTestReceipt(probe, newDiagnostic.ID, "PROVIDER_MESSAGE")
	})
	require.NoError(t, relaySchemaV5CompleteChannelTestReceipt(tx, newDiagnostic.ID, PlatformChannelControlErrorTestAuth))

	legacyDiagnostic := relaySchemaV5PendingChannelTestReceipt("legacy-diagnostic")
	require.NoError(t, tx.Create(&legacyDiagnostic).Error)
	require.NoError(t, relaySchemaV5CompleteChannelTestReceipt(tx, legacyDiagnostic.ID, PlatformChannelControlErrorTestFailed),
		"v5 must preserve the previously accepted v4 diagnostic taxonomy")

	tampered := relaySchemaV5PendingChannelTestReceipt("tampered-intent")
	require.NoError(t, tx.Create(&tampered).Error)
	completedAt := time.Now().UTC()
	relaySchemaV5RequireRejectedStatement(t, tx, func(probe *gorm.DB) error {
		return probe.Exec(`UPDATE platform_channel_control_operations
SET state = 'failed', actor = 'tampered', result_success = false,
    result_response_ms = 1, result_error_code = ?, completed_at = ?
WHERE id = ?`, PlatformChannelControlErrorTestQuota, completedAt, tampered.ID).Error
	})
	relaySchemaV5RequireRejectedStatement(t, tx, func(probe *gorm.DB) error {
		return probe.Exec(`DELETE FROM platform_channel_control_operations WHERE id = ?`, legacyDiagnostic.ID).Error
	})
	relaySchemaV5RequireRejectedStatement(t, tx, func(probe *gorm.DB) error {
		return probe.Exec(`TRUNCATE TABLE platform_channel_control_operations`).Error
	})
}

func relaySchemaV5PendingChannelTestReceipt(suffix string) platformChannelControlOperationArtifactV5 {
	return platformChannelControlOperationArtifactV5{
		ID: uuid.NewString(), TenantID: uuid.NewString(), OperationID: "schema-v5-" + suffix,
		ChannelID: 1, Kind: PlatformChannelControlOperationKindTest,
		State: PlatformChannelControlOperationPending, RequestID: uuid.NewString(),
		Actor: "schema-v5-test", Reason: "verify closed diagnostic taxonomy",
		IntentSHA256: strings.Repeat("a", 64), IntentJSON: `{}`,
		CreatedAt: time.Now().UTC(),
	}
}

func relaySchemaV5CompleteChannelTestReceipt(db *gorm.DB, id string, code string) error {
	return db.Exec(`UPDATE platform_channel_control_operations
SET state = 'failed', result_success = false, result_response_ms = 1,
    result_error_code = ?, completed_at = ?
WHERE id = ?`, code, time.Now().UTC(), id).Error
}

func relaySchemaV5RequireRejectedStatement(t *testing.T, tx *gorm.DB, statement func(*gorm.DB) error) {
	t.Helper()
	rollback := errors.New("rollback rejected v5 guard statement")
	err := tx.Transaction(func(probe *gorm.DB) error {
		require.Error(t, statement(probe),
			"the v5 guard must reject provider-controlled diagnostics and immutable receipt mutations")
		return rollback
	})
	require.ErrorIs(t, err, rollback)
}
