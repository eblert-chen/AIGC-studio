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

// TestRelayPostgresV8CatalogFreeze derives the v8 catalog from a disposable,
// empty PostgreSQL 16 database. Release engineering runs it explicitly when
// freezing this schema; ordinary test runs skip without a DSN.
func TestRelayPostgresV8CatalogFreeze(t *testing.T) {
	dsn := os.Getenv("TEST_RELAY_SCHEMA_V8_FREEZE_DSN")
	if dsn == "" {
		t.Skip("set TEST_RELAY_SCHEMA_V8_FREEZE_DSN to derive the PostgreSQL v8 catalog")
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
	require.NoError(t, migrateRelaySchemaV8Bootstrap(db))
	require.True(t, db.Migrator().HasTable(&PlatformProviderCostAllocationEvidence{}))
	for _, trigger := range []string{
		"trg_platform_provider_cost_allocation_evidence_no_mutation",
		"trg_platform_provider_cost_allocation_evidence_no_truncate",
	} {
		var count int64
		require.NoError(t, db.Raw(`SELECT COUNT(*) FROM pg_trigger
WHERE tgrelid = 'platform_provider_cost_allocation_evidence'::regclass
  AND NOT tgisinternal AND tgname = ?`, trigger).Scan(&count).Error)
		require.Equal(t, int64(1), count, "fresh v8 is missing append-only trigger %s", trigger)
	}
	actual, err := getRelaySchemaCatalogFingerprintForVersion(db, relaySchemaV8FrozenVersion)
	require.NoError(t, err)
	if relaySchemaV8PostgresCatalogSHA256 == "sha256:pending" {
		t.Fatalf("freeze Relay PostgreSQL v8 catalog baseline as %s", actual)
	}
	require.Equal(t, relaySchemaV8PostgresCatalogSHA256, actual)
}
