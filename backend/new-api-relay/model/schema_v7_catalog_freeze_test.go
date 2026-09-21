package model

import (
	"os"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

// TestRelayPostgresV7CatalogFreeze derives the v7 catalog from a disposable,
// empty PostgreSQL 16 database. Release engineering runs it explicitly when
// freezing this schema; ordinary test runs skip without a DSN.
func TestRelayPostgresV7CatalogFreeze(t *testing.T) {
	dsn := os.Getenv("TEST_RELAY_SCHEMA_V7_FREEZE_DSN")
	if dsn == "" {
		t.Skip("set TEST_RELAY_SCHEMA_V7_FREEZE_DSN to derive the PostgreSQL v7 catalog")
	}
	db, err := gorm.Open(postgres.Open(dsn), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	sqlDB, err := db.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = sqlDB.Close() })
	originalDatabaseType := common.MainDatabaseType()
	common.SetMainDatabaseType(common.DatabaseTypePostgreSQL)
	t.Cleanup(func() { common.SetMainDatabaseType(originalDatabaseType) })
	t.Setenv(relayRuntimeDatabaseRoleEnvironment, "relay_runtime")
	require.NoError(t, db.Exec(`DROP SCHEMA public CASCADE`).Error)
	require.NoError(t, db.Exec(`CREATE SCHEMA public`).Error)
	require.NoError(t, db.Exec(`DO $roles$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_runtime') THEN
    CREATE ROLE relay_runtime NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_download_edge') THEN
    CREATE ROLE relay_download_edge NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  END IF;
END
$roles$`).Error)
	require.NoError(t, db.Exec(`DO $database_acl$
BEGIN
  EXECUTE format('REVOKE CREATE, TEMPORARY ON DATABASE %I FROM PUBLIC, relay_runtime, relay_download_edge', current_database());
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO relay_runtime, relay_download_edge', current_database());
END
$database_acl$`).Error)
	require.NoError(t, db.Exec(`REVOKE ALL ON SCHEMA public FROM PUBLIC, relay_runtime, relay_download_edge`).Error)
	require.NoError(t, db.Exec(`GRANT USAGE ON SCHEMA public TO relay_runtime, relay_download_edge`).Error)
	require.NoError(t, db.Exec(`ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC`).Error)
	require.NoError(t, ensureRelaySchemaMetadata(db))
	require.NoError(t, migrateRelaySchemaV7Bootstrap(db))
	relaySchemaV7RequirePersonalCostScope(t, db)
	for _, constraint := range []string{
		"ck_platform_channel_control_route_projection_v6",
		"ck_platform_channel_control_transport_pair_v6",
		"ck_platform_channel_control_submission_state_v6",
		"ck_platform_channel_control_blocker_code_v6",
		"ck_platform_channel_control_reconciliation_v6",
		"ck_platform_channel_control_artifact_evidence_v7",
	} {
		var count int64
		require.NoError(t, db.Raw(`SELECT COUNT(*) FROM pg_constraint
WHERE conrelid = 'platform_channel_control_operations'::regclass AND conname = ?`, constraint).Scan(&count).Error)
		require.Equal(t, int64(1), count, "fresh v7 is missing lifecycle constraint %s", constraint)
	}
	var oldArtifactConstraintCount int64
	require.NoError(t, db.Raw(`SELECT COUNT(*) FROM pg_constraint
WHERE conrelid = 'platform_channel_control_operations'::regclass
  AND conname = 'ck_platform_channel_control_artifact_evidence_v6'`).Scan(&oldArtifactConstraintCount).Error)
	require.Zero(t, oldArtifactConstraintCount)
	var costScopeConstraintCount int64
	require.NoError(t, db.Raw(`SELECT COUNT(*) FROM pg_constraint
WHERE conrelid = 'platform_channel_cost_events'::regclass
  AND conname = ?`, platformChannelCostBillingScopeConstraintV7).Scan(&costScopeConstraintCount).Error)
	require.Equal(t, int64(1), costScopeConstraintCount,
		"fresh v7 is missing the personal provider-cost scope constraint")
	routeIndexMatches, err := platformChannelControlRouteIndexMatchesV7(db)
	require.NoError(t, err)
	require.True(t, routeIndexMatches, "fresh v7 route index must match the frozen v6 lifecycle projection")
	actual, err := getRelaySchemaCatalogFingerprintForVersion(db, relaySchemaV7FrozenVersion)
	require.NoError(t, err)
	if relaySchemaV7PostgresCatalogSHA256 == "sha256:pending" {
		t.Fatalf("freeze Relay PostgreSQL v7 catalog baseline as %s", actual)
	}
	require.Equal(t, relaySchemaV7PostgresCatalogSHA256, actual)
}
