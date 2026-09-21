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

// TestRelaySchemaPostgresV6ToV7ArtifactContentTypes proves that the isolated
// incremental path retains the five unchanged v6 lifecycle checks while
// replacing only the artifact MIME check and transition guard. The release
// gate supplies an exact disposable v6 database and this test rolls back every
// probe.
func TestRelaySchemaPostgresV6ToV7ArtifactContentTypes(t *testing.T) {
	dsn := strings.TrimSpace(os.Getenv("TEST_RELAY_SCHEMA_V6_RELEASE_DSN"))
	if dsn == "" {
		t.Skip("set TEST_RELAY_SCHEMA_V6_RELEASE_DSN to run the PostgreSQL v6-to-v7 migration gate")
	}
	database, err := gorm.Open(postgres.Open(dsn), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	sqlDB, err := database.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = sqlDB.Close() })

	originalDatabaseType := common.MainDatabaseType()
	common.SetMainDatabaseType(common.DatabaseTypePostgreSQL)
	t.Cleanup(func() { common.SetMainDatabaseType(originalDatabaseType) })
	relaySchemaV7ExercisePostgresV6ToV7(t, database)
}

// TestRelaySchemaPostgresConstructedV6ToV7ArtifactContentTypes independently
// builds a fresh exact-v6 release on PostgreSQL 16 before exercising the same
// incremental boundary. This protects the release gate itself: it does not
// rely solely on an externally prepared v6 fixture.
func TestRelaySchemaPostgresConstructedV6ToV7ArtifactContentTypes(t *testing.T) {
	dsn := strings.TrimSpace(os.Getenv("TEST_RELAY_SCHEMA_V6_TO_V7_DSN"))
	if dsn == "" {
		t.Skip("set TEST_RELAY_SCHEMA_V6_TO_V7_DSN to construct the PostgreSQL v6-to-v7 migration gate")
	}
	database, err := gorm.Open(postgres.Open(dsn), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	sqlDB, err := database.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = sqlDB.Close() })

	originalDatabaseType := common.MainDatabaseType()
	common.SetMainDatabaseType(common.DatabaseTypePostgreSQL)
	t.Cleanup(func() { common.SetMainDatabaseType(originalDatabaseType) })
	t.Setenv(relayRuntimeDatabaseRoleEnvironment, "relay_runtime")
	require.NoError(t, database.Exec(`DROP SCHEMA public CASCADE`).Error)
	require.NoError(t, database.Exec(`CREATE SCHEMA public`).Error)
	require.NoError(t, database.Exec(`DO $roles$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_runtime') THEN
    CREATE ROLE relay_runtime NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_download_edge') THEN
    CREATE ROLE relay_download_edge NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  END IF;
END
$roles$`).Error)
	require.NoError(t, database.Exec(`DO $database_acl$
BEGIN
  EXECUTE format('REVOKE CREATE, TEMPORARY ON DATABASE %I FROM PUBLIC, relay_runtime, relay_download_edge', current_database());
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO relay_runtime, relay_download_edge', current_database());
END
$database_acl$`).Error)
	require.NoError(t, database.Exec(`REVOKE ALL ON SCHEMA public FROM PUBLIC, relay_runtime, relay_download_edge`).Error)
	require.NoError(t, database.Exec(`GRANT USAGE ON SCHEMA public TO relay_runtime, relay_download_edge`).Error)
	require.NoError(t, database.Exec(`ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC`).Error)

	require.NoError(t, ensureRelaySchemaMetadata(database))
	definitions := relaySchemaMigrations()
	require.GreaterOrEqual(t, len(definitions), 7)
	v6Attempt := uuid.NewString()
	require.NoError(t, markRelaySchemaApplying(database, definitions[5], v6Attempt))
	require.NoError(t, runRelaySchemaBootstrapTransaction(
		database,
		definitions[:6],
		definitions[5],
		v6Attempt,
	))
	relaySchemaV7ExercisePostgresV6ToV7(t, database)
}

func relaySchemaV7ExercisePostgresV6ToV7(t *testing.T, database *gorm.DB) {
	t.Helper()
	tx := database.Begin()
	require.NoError(t, tx.Error)
	t.Cleanup(func() { _ = tx.Rollback().Error })

	v6Catalog, err := getRelaySchemaCatalogFingerprintForVersion(tx, relaySchemaV6FrozenVersion)
	require.NoError(t, err)
	require.Equal(t, relaySchemaV6PostgresCatalogSHA256, v6Catalog)
	var state RelaySchemaState
	require.NoError(t, tx.Where("id = ?", relaySchemaStateSingletonID).First(&state).Error)
	require.Equal(t, relaySchemaV6FrozenVersion, state.CurrentVersion)
	require.Equal(t, relaySchemaV6FrozenVersion, state.TargetVersion)
	require.Equal(t, RelaySchemaStateClean, state.State)
	require.False(t, state.Dirty)
	require.Equal(t, relaySchemaV6FrozenChecksumSHA256, state.CurrentChecksum)
	require.Equal(t, relaySchemaV6PostgresCatalogSHA256, state.CurrentCatalogSHA256)
	require.False(t, tx.Migrator().HasColumn(&PlatformChannelCostEvent{}, "personal_workspace_id"),
		"the exact v6 release must not contain the v7 personal cost scope")
	var ledgerBefore []RelaySchemaMigration
	require.NoError(t, tx.Order("version ASC").Find(&ledgerBefore).Error)
	require.NotEmpty(t, ledgerBefore)
	require.Equal(t, relaySchemaV6FrozenVersion, ledgerBefore[len(ledgerBefore)-1].Version)
	unchangedObjectOIDsBefore := relaySchemaV7PostgresUnchangedObjectOIDs(t, tx)

	now := time.Now().UTC()
	require.Equal(t, int64(1), tx.Model(&RelaySchemaState{}).
		Where("id = ?", relaySchemaStateSingletonID).
		Updates(map[string]any{
			"target_version":         relaySchemaV7FrozenVersion,
			"state":                  RelaySchemaStateApplying,
			"dirty":                  true,
			"attempt_id":             uuid.NewString(),
			"current_checksum":       relaySchemaV6FrozenChecksumSHA256,
			"target_checksum":        relaySchemaV7FrozenChecksumSHA256,
			"current_catalog_sha256": relaySchemaV6PostgresCatalogSHA256,
			"target_catalog_sha256":  relaySchemaV7PostgresCatalogSHA256,
			"started_at":             now,
			"finished_at":            nil,
			"error_code":             "",
			"updated_at":             now,
		}).RowsAffected)

	require.NoError(t, migrateRelaySchemaV7ChannelTestArtifactContentTypes(tx))
	require.NoError(t, MigratePlatformChannelCostPersonalScopeV7WithDB(tx))
	relaySchemaV7RequirePersonalCostScope(t, tx)
	unchangedObjectOIDsAfter := relaySchemaV7PostgresUnchangedObjectOIDs(t, tx)
	require.Equal(t, unchangedObjectOIDsBefore, unchangedObjectOIDsAfter,
		"v7 must not drop or recreate unchanged v6 constraints or indexes")
	var ledgerAfter []RelaySchemaMigration
	require.NoError(t, tx.Order("version ASC").Find(&ledgerAfter).Error)
	require.Equal(t, ledgerBefore, ledgerAfter, "v7 transform must not rewrite the frozen v1-v6 ledger")
	v7Catalog, err := getRelaySchemaCatalogFingerprintForVersion(tx, relaySchemaV7FrozenVersion)
	require.NoError(t, err)
	require.Equal(t, relaySchemaV7PostgresCatalogSHA256, v7Catalog,
		"v6-to-v7 and fresh-v7 must converge to the frozen v7 catalog")

	for _, constraint := range []string{
		"ck_platform_channel_control_route_projection_v6",
		"ck_platform_channel_control_transport_pair_v6",
		"ck_platform_channel_control_submission_state_v6",
		"ck_platform_channel_control_blocker_code_v6",
		"ck_platform_channel_control_reconciliation_v6",
		"ck_platform_channel_control_artifact_evidence_v7",
	} {
		var count int64
		require.NoError(t, tx.Raw(`SELECT COUNT(*) FROM pg_constraint WHERE conname = ?`, constraint).Scan(&count).Error)
		require.Equal(t, int64(1), count, "missing v7 lifecycle constraint %s", constraint)
	}
	var oldArtifactConstraintCount int64
	require.NoError(t, tx.Raw(
		`SELECT COUNT(*) FROM pg_constraint WHERE conname = 'ck_platform_channel_control_artifact_evidence_v6'`,
	).Scan(&oldArtifactConstraintCount).Error)
	require.Zero(t, oldArtifactConstraintCount, "v7 must replace the v6 artifact constraint")
	var costScopeConstraintCount int64
	require.NoError(t, tx.Raw(`SELECT COUNT(*) FROM pg_constraint
WHERE conrelid = 'platform_channel_cost_events'::regclass
  AND conname = ?`, platformChannelCostBillingScopeConstraintV7).Scan(&costScopeConstraintCount).Error)
	require.Equal(t, int64(1), costScopeConstraintCount)
	relaySchemaV5RequireRejectedStatement(t, tx, func(probe *gorm.DB) error {
		return probe.Exec(`INSERT INTO platform_channel_cost_events (
id, amount_cents, idempotency_key, channel_key, channel_type, occurred_at,
external_reference, company_id, personal_workspace_id, task_id, relay_job_id,
note, evidence_source, evidence_reference, source_document_sha256,
payload_json, payload_sha256, created_at
) VALUES (?, 1, ?, 'route-v7-scope', 'official', ?, 'provider-v7-scope',
?, ?, ?, ?, '', 'provider_reported', '', '', '{}', ?, ?)`,
			uuid.NewString(), "invalid-dual-scope-"+uuid.NewString(), time.Now().UTC(),
			uuid.NewString(), uuid.NewString(), uuid.NewString(), uuid.NewString(),
			strings.Repeat("a", 64), time.Now().UTC()).Error
	})

	relaySchemaV7RequirePNGCompletion(t, tx, "postgres-upgrade", 74)
	unsupported := relaySchemaV6PendingRouteTest("v7-postgres-jpeg", 74)
	require.NoError(t, tx.Session(&gorm.Session{SkipHooks: true}).Create(&unsupported).Error)
	relaySchemaV7RequireSubmitted(t, tx, unsupported.ID, "provider-task-v7-postgres-jpeg")
	relaySchemaV5RequireRejectedStatement(t, tx, func(probe *gorm.DB) error {
		return relaySchemaV7CompleteArtifact(probe, unsupported.ID, "image/jpeg")
	})
	relaySchemaV7RequireUnchangedLifecycleGuards(t, tx, "postgres-upgrade", 74)
}

func relaySchemaV7PostgresUnchangedObjectOIDs(t *testing.T, database *gorm.DB) map[string]int64 {
	t.Helper()
	var rows []struct {
		Identity string `gorm:"column:identity"`
		OID      int64  `gorm:"column:oid"`
	}
	require.NoError(t, database.Raw(`
SELECT 'constraint:' || candidate.conname AS identity, candidate.oid::bigint AS oid
  FROM pg_constraint candidate
 WHERE candidate.conrelid = 'platform_channel_control_operations'::regclass
   AND candidate.conname IN (
       'ck_platform_channel_control_route_projection_v6',
       'ck_platform_channel_control_transport_pair_v6',
       'ck_platform_channel_control_submission_state_v6',
       'ck_platform_channel_control_blocker_code_v6',
       'ck_platform_channel_control_reconciliation_v6'
   )
UNION ALL
SELECT 'index:' || index_relation.relname AS identity, index_relation.oid::bigint AS oid
  FROM pg_class index_relation
  JOIN pg_namespace namespace ON namespace.oid = index_relation.relnamespace
 WHERE namespace.nspname = current_schema()
   AND index_relation.relname = 'idx_platform_channel_control_route'
UNION ALL
SELECT 'trigger:' || candidate.tgname AS identity, candidate.oid::bigint AS oid
  FROM pg_trigger candidate
 WHERE candidate.tgrelid = 'platform_channel_cost_events'::regclass
   AND NOT candidate.tgisinternal
   AND candidate.tgname IN (
       'trg_platform_channel_cost_events_no_mutation',
       'trg_platform_channel_cost_events_no_truncate'
   )
ORDER BY identity`).Scan(&rows).Error)
	require.Len(t, rows, 8, "exact v6 fixture must expose all unchanged v7 boundary objects")
	objects := make(map[string]int64, len(rows))
	for _, row := range rows {
		require.NotEmpty(t, row.Identity)
		require.Positive(t, row.OID)
		_, duplicate := objects[row.Identity]
		require.False(t, duplicate)
		objects[row.Identity] = row.OID
	}
	return objects
}
