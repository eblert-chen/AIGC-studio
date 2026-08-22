package model

import (
	"os"
	"testing"

	"github.com/stretchr/testify/require"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

func TestRelayPostgres16SystemSemanticBaseline(t *testing.T) {
	dsn := os.Getenv("TEST_RELAY_SYSTEM_SEMANTIC_DSN")
	if dsn == "" {
		t.Skip("set TEST_RELAY_SYSTEM_SEMANTIC_DSN to verify the PostgreSQL 16 system semantic baseline")
	}
	db, err := gorm.Open(postgres.Open(dsn), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	sqlDB, err := db.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = sqlDB.Close() })
	actual, err := relayPostgres16SystemSemanticFingerprint(db)
	require.NoError(t, err)
	require.Contains(t, []string{
		relayPostgres16AlpineSystemSemanticSHA256,
		relayPostgres16DebianSystemSemanticSHA256,
	}, actual)
	require.NoError(t, verifyRelayPostgres16SystemSemanticBaseline(db))
}

func TestRelayPostgresV1CatalogBaseline(t *testing.T) {
	dsn := os.Getenv("TEST_RELAY_SCHEMA_CATALOG_DSN")
	if dsn == "" {
		t.Skip("set TEST_RELAY_SCHEMA_CATALOG_DSN to verify the PostgreSQL v1 catalog baseline")
	}
	db, err := gorm.Open(postgres.Open(dsn), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	sqlDB, err := db.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = sqlDB.Close() })
	actual, err := getRelaySchemaCatalogFingerprintForVersion(db, 1)
	require.NoError(t, err)
	if relaySchemaV1PostgresCatalogSHA256 == "sha256:pending" {
		t.Fatalf("freeze Relay PostgreSQL v1 catalog baseline as %s", actual)
	}
	require.Equal(t, relaySchemaV1PostgresCatalogSHA256, actual)
}

func TestRelayPostgresV2CatalogBaseline(t *testing.T) {
	dsn := os.Getenv("TEST_RELAY_SCHEMA_CATALOG_DSN")
	if dsn == "" {
		t.Skip("set TEST_RELAY_SCHEMA_CATALOG_DSN to verify the PostgreSQL v2 catalog baseline")
	}
	db, err := gorm.Open(postgres.Open(dsn), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	sqlDB, err := db.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = sqlDB.Close() })
	actual, err := getRelaySchemaCatalogFingerprintForVersion(db, 2)
	require.NoError(t, err)
	require.Equal(t, relaySchemaV2PostgresCatalogSHA256, actual)
	require.Equal(t, relaySchemaV1PostgresCatalogSHA256, actual, "v2 is an explicit no-catalog-delta release")
}

func TestRelayProtectedDatabaseExactSurfaceBaseline(t *testing.T) {
	dsn := os.Getenv("TEST_RELAY_EXACT_SURFACE_DSN")
	if dsn == "" {
		t.Skip("set TEST_RELAY_EXACT_SURFACE_DSN to verify the Relay protected database surface")
	}
	db, err := gorm.Open(postgres.Open(dsn), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	sqlDB, err := db.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = sqlDB.Close() })
	require.NoError(t, verifyRelayProtectedDatabaseSurfacePreflight(
		db,
		"relay_schema_owner",
		"relay_migrator",
		"relay_runtime",
		true,
	))
}
