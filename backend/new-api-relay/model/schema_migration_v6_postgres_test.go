package model

import (
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

// TestRelaySchemaPostgresV5ToV6DurableChannelTestLifecycle proves the isolated
// incremental boundary against a disposable exact-v5 release database. The
// release gate owns that fixture; this test performs every probe inside one
// rollback-only transaction and never reconstructs or rewrites frozen v1-v5.
func TestRelaySchemaPostgresV5ToV6DurableChannelTestLifecycle(t *testing.T) {
	dsn := strings.TrimSpace(os.Getenv("TEST_RELAY_SCHEMA_V5_RELEASE_DSN"))
	if dsn == "" {
		t.Skip("set TEST_RELAY_SCHEMA_V5_RELEASE_DSN to run the PostgreSQL v5-to-v6 migration gate")
	}
	database, err := gorm.Open(postgres.Open(dsn), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	sqlDB, err := database.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = sqlDB.Close() })

	originalDatabaseType := common.MainDatabaseType()
	common.SetMainDatabaseType(common.DatabaseTypePostgreSQL)
	t.Cleanup(func() { common.SetMainDatabaseType(originalDatabaseType) })
	tx := database.Begin()
	require.NoError(t, tx.Error)
	t.Cleanup(func() { _ = tx.Rollback().Error })

	v5Catalog, err := getRelaySchemaCatalogFingerprintForVersion(tx, relaySchemaV5FrozenVersion)
	require.NoError(t, err)
	require.Equal(t, relaySchemaV5PostgresCatalogSHA256, v5Catalog)
	var state RelaySchemaState
	require.NoError(t, tx.Where("id = ?", relaySchemaStateSingletonID).First(&state).Error)
	require.Equal(t, relaySchemaV5FrozenVersion, state.CurrentVersion)
	require.Equal(t, relaySchemaV5FrozenVersion, state.TargetVersion)
	require.Equal(t, RelaySchemaStateClean, state.State)
	require.False(t, state.Dirty)
	require.Equal(t, relaySchemaV5FrozenChecksumSHA256, state.CurrentChecksum)
	require.Equal(t, relaySchemaV5FrozenChecksumSHA256, state.TargetChecksum)
	require.Equal(t, relaySchemaV5PostgresCatalogSHA256, state.CurrentCatalogSHA256)
	require.Equal(t, relaySchemaV5PostgresCatalogSHA256, state.TargetCatalogSHA256)
	var v5LedgerBefore []RelaySchemaMigration
	require.NoError(t, tx.Order("version ASC").Find(&v5LedgerBefore).Error)
	require.NotEmpty(t, v5LedgerBefore, "the exact v5 release fixture must retain its immutable ledger evidence")
	require.Equal(t, relaySchemaV5FrozenVersion, v5LedgerBefore[len(v5LedgerBefore)-1].Version)
	for _, field := range []string{
		"IntentPublicModelID", "IntentRouteID", "IntentTransportRevision", "IntentTransportSHA256",
		"ProviderSubmissionState", "ProviderBlockerCode", "ProviderTaskID", "ProviderArtifactSHA256",
		"ProviderArtifactSizeBytes", "ProviderArtifactContentType",
		"ReconciliationActor", "ReconciliationReason", "ReconciledAt",
	} {
		require.False(t, tx.Migrator().HasColumn(&PlatformChannelControlOperation{}, field),
			"exact v5 fixture unexpectedly contains v6 field %s", field)
	}

	now := time.Now().UTC()
	require.Equal(t, int64(1), tx.Model(&RelaySchemaState{}).
		Where("id = ?", relaySchemaStateSingletonID).
		Updates(map[string]any{
			"target_version":         relaySchemaV6FrozenVersion,
			"state":                  RelaySchemaStateApplying,
			"dirty":                  true,
			"attempt_id":             uuid.NewString(),
			"current_checksum":       relaySchemaV5FrozenChecksumSHA256,
			"target_checksum":        relaySchemaV6FrozenChecksumSHA256,
			"current_catalog_sha256": relaySchemaV5PostgresCatalogSHA256,
			"target_catalog_sha256":  relaySchemaV6PostgresCatalogSHA256,
			"started_at":             now,
			"finished_at":            nil,
			"error_code":             "",
			"updated_at":             now,
		}).RowsAffected)

	require.NoError(t, migrateRelaySchemaV6ChannelTestLifecycle(tx))
	var v5LedgerAfter []RelaySchemaMigration
	require.NoError(t, tx.Order("version ASC").Find(&v5LedgerAfter).Error)
	require.Equal(t, v5LedgerBefore, v5LedgerAfter,
		"the v5-to-v6 lifecycle step must not rewrite any immutable v1-v5 ledger row")
	v6Catalog, err := getRelaySchemaCatalogFingerprintForVersion(tx, relaySchemaV6FrozenVersion)
	require.NoError(t, err)
	require.Equal(t, relaySchemaV6PostgresCatalogSHA256, v6Catalog)
	for _, constraint := range []string{
		"ck_platform_channel_control_route_projection_v6",
		"ck_platform_channel_control_transport_pair_v6",
		"ck_platform_channel_control_submission_state_v6",
		"ck_platform_channel_control_blocker_code_v6",
		"ck_platform_channel_control_artifact_evidence_v6",
		"ck_platform_channel_control_reconciliation_v6",
	} {
		var count int64
		require.NoError(t, tx.Raw(`SELECT COUNT(*) FROM pg_constraint WHERE conname = ?`, constraint).Scan(&count).Error)
		require.Equal(t, int64(1), count, "missing v6 constraint %s", constraint)
	}
	for _, index := range []string{
		"idx_platform_channel_control_route",
		"idx_platform_channel_control_submission",
		"ux_platform_channel_control_unresolved_route",
		"ux_platform_channel_control_provider_task",
	} {
		var count int64
		require.NoError(t, tx.Raw(`SELECT COUNT(*) FROM pg_indexes
WHERE schemaname = current_schema() AND tablename = 'platform_channel_control_operations' AND indexname = ?`, index).Scan(&count).Error)
		require.Equal(t, int64(1), count, "missing v6 index %s", index)
	}

	for _, testCase := range []struct {
		name   string
		mutate func(*PlatformChannelControlOperation)
	}{
		{name: "missing-revision", mutate: func(operation *PlatformChannelControlOperation) { operation.IntentTransportRevision = "" }},
		{name: "missing-digest", mutate: func(operation *PlatformChannelControlOperation) { operation.IntentTransportSHA256 = "" }},
		{name: "malformed-revision", mutate: func(operation *PlatformChannelControlOperation) { operation.IntentTransportRevision = "sha256:xyz" }},
		{name: "malformed-digest", mutate: func(operation *PlatformChannelControlOperation) { operation.IntentTransportSHA256 = "sha256:xyz" }},
	} {
		invalid := relaySchemaV6PendingRouteTest("postgres-invalid-transport-"+testCase.name, 61)
		testCase.mutate(&invalid)
		relaySchemaV5RequireRejectedStatement(t, tx, func(probe *gorm.DB) error {
			return probe.Session(&gorm.Session{SkipHooks: true}).Create(&invalid).Error
		})
	}

	providerProvenNoCreation := relaySchemaV6PendingRouteTest("postgres-provider-proven-no-creation", 61)
	require.NoError(t, tx.Session(&gorm.Session{SkipHooks: true}).Create(&providerProvenNoCreation).Error)
	require.NoError(t, tx.Exec(`UPDATE platform_channel_control_operations
SET provider_submission_state = 'submission_unknown' WHERE id = ?`, providerProvenNoCreation.ID).Error)
	relaySchemaV5RequireRejectedStatement(t, tx, func(probe *gorm.DB) error {
		return probe.Exec(`UPDATE platform_channel_control_operations
SET state = 'failed', provider_submission_state = 'provider_terminal',
    result_success = false, result_response_ms = 23,
    result_error_code = 'CHANNEL_TEST_PROVIDER_VALIDATION', completed_at = ?
WHERE id = ?`, time.Now().UTC(), providerProvenNoCreation.ID).Error
	})

	for _, errorCode := range []string{
		"CHANNEL_TEST_FAILED",
		"CHANNEL_TEST_UNAVAILABLE",
		"CHANNEL_TEST_ARTIFACT_INVALID",
		"CHANNEL_TEST_ROUTE_DRIFT",
	} {
		relaySchemaV5RequireRejectedStatement(t, tx, func(probe *gorm.DB) error {
			return probe.Exec(`UPDATE platform_channel_control_operations
SET state = 'failed', provider_submission_state = 'provider_rejected',
    result_success = false, result_response_ms = 23,
    result_error_code = ?, completed_at = ?
WHERE id = ?`, errorCode, time.Now().UTC(), providerProvenNoCreation.ID).Error
		})
	}

	providerRejected := relaySchemaV6PendingRouteTest("postgres-provider-rejected", 61)
	require.NoError(t, tx.Session(&gorm.Session{SkipHooks: true}).Create(&providerRejected).Error)
	require.NoError(t, tx.Exec(`UPDATE platform_channel_control_operations
SET provider_submission_state = 'submission_unknown' WHERE id = ?`, providerRejected.ID).Error)
	require.NoError(t, tx.Exec(`UPDATE platform_channel_control_operations
SET state = 'failed', provider_submission_state = 'provider_rejected',
    result_success = false, result_response_ms = 23,
    result_error_code = 'CHANNEL_TEST_PROVIDER_VALIDATION', completed_at = ?
WHERE id = ?`, time.Now().UTC(), providerRejected.ID).Error,
		"a deterministic provider rejection may close submission_unknown without a task id")

	providerTerminal := relaySchemaV6PendingRouteTest("postgres-provider-terminal", 61)
	require.NoError(t, tx.Session(&gorm.Session{SkipHooks: true}).Create(&providerTerminal).Error)
	require.NoError(t, tx.Exec(`UPDATE platform_channel_control_operations
SET provider_submission_state = 'submission_unknown' WHERE id = ?`, providerTerminal.ID).Error)
	require.NoError(t, tx.Exec(`UPDATE platform_channel_control_operations
SET provider_submission_state = 'submitted', provider_task_id = 'postgres-provider-task-v6-terminal'
WHERE id = ?`, providerTerminal.ID).Error)
	require.NoError(t, tx.Exec(`UPDATE platform_channel_control_operations
SET state = 'failed', provider_submission_state = 'provider_terminal',
    result_success = false, result_response_ms = 23,
    result_error_code = 'CHANNEL_TEST_PROVIDER_TERMINAL', completed_at = ?
WHERE id = ?`, time.Now().UTC(), providerTerminal.ID).Error,
		"provider_terminal is valid only after a durable provider task was bound")

	reconciled := relaySchemaV6PendingRouteTest("postgres-reconciled", 61)
	require.NoError(t, tx.Session(&gorm.Session{SkipHooks: true}).Create(&reconciled).Error)
	require.NoError(t, tx.Exec(`UPDATE platform_channel_control_operations
SET provider_submission_state = 'submission_unknown' WHERE id = ?`, reconciled.ID).Error)
	reconciledAt := time.Now().UTC()
	require.NoError(t, tx.Exec(`UPDATE platform_channel_control_operations
SET state = 'failed', provider_submission_state = 'reconciled_no_creation',
    result_success = false, result_response_ms = 0,
    result_error_code = 'CHANNEL_TEST_RECONCILED_NO_CREATION',
    reconciliation_actor = 'platform-owner', reconciliation_reason = 'provider console proves no task',
    reconciled_at = ?, completed_at = ?
WHERE id = ?`, reconciledAt, reconciledAt, reconciled.ID).Error)
	relaySchemaV5RequireRejectedStatement(t, tx, func(probe *gorm.DB) error {
		return probe.Exec(`DELETE FROM platform_channel_control_operations WHERE id = ?`, reconciled.ID).Error
	})
}
