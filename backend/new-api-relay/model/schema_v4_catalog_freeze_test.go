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

// TestRelayPostgresV4CatalogFreeze derives the v4 catalog from a disposable,
// empty PostgreSQL 16 database. CI normally skips it; release engineering runs
// it explicitly while freezing a new schema version.
func TestRelayPostgresV4CatalogFreeze(t *testing.T) {
	dsn := os.Getenv("TEST_RELAY_SCHEMA_V4_FREEZE_DSN")
	if dsn == "" {
		t.Skip("set TEST_RELAY_SCHEMA_V4_FREEZE_DSN to derive the PostgreSQL v4 catalog")
	}
	db, err := gorm.Open(postgres.Open(dsn), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	sqlDB, err := db.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = sqlDB.Close() })
	common.SetMainDatabaseType(common.DatabaseTypePostgreSQL)
	t.Cleanup(func() { common.SetMainDatabaseType(common.DatabaseTypeSQLite) })
	t.Setenv(relayRuntimeDatabaseRoleEnvironment, "relay_runtime")
	require.NoError(t, db.Exec(`DROP SCHEMA public CASCADE`).Error)
	require.NoError(t, db.Exec(`CREATE SCHEMA public`).Error)
	// The versioned bootstrap contains RLS policies and least-privilege
	// manifests whose object definitions name these principals.  The catalog
	// freeze fixture therefore creates the same isolated, credential-free role
	// topology as role-pre, without provisioning any reusable password in this
	// disposable database.
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
	// Production creates and guards the schema state/ledger before entering the
	// fresh bootstrap transaction.  They are part of the attested application
	// catalog even though relaySchemaV4Models intentionally lists only business
	// tables.  Omitting this pre-bootstrap step would freeze an incomplete
	// catalog which production can never produce.
	require.NoError(t, ensureRelaySchemaMetadata(db))
	require.NoError(t, migrateRelaySchemaV4Bootstrap(db))
	actual, err := getRelaySchemaCatalogFingerprintForVersion(db, relaySchemaV4FrozenVersion)
	require.NoError(t, err)
	if relaySchemaV4PostgresCatalogSHA256 == "sha256:pending" {
		t.Fatalf("freeze Relay PostgreSQL v4 catalog baseline as %s", actual)
	}
	require.Equal(t, relaySchemaV4PostgresCatalogSHA256, actual)
}
