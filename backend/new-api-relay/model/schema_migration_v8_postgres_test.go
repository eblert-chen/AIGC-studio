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

func TestRelaySchemaPostgresConstructedV7ToV8ProviderCostEvidence(t *testing.T) {
	dsn := strings.TrimSpace(os.Getenv("TEST_RELAY_SCHEMA_V7_TO_V8_DSN"))
	if dsn == "" {
		t.Skip("set TEST_RELAY_SCHEMA_V7_TO_V8_DSN to construct the PostgreSQL v7-to-v8 migration gate")
	}
	database, err := gorm.Open(postgres.Open(dsn), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	sqlDB, err := database.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = sqlDB.Close() })

	originalDB := DB
	originalDatabaseType := common.MainDatabaseType()
	DB = database
	common.SetMainDatabaseType(common.DatabaseTypePostgreSQL)
	t.Cleanup(func() {
		DB = originalDB
		common.SetMainDatabaseType(originalDatabaseType)
	})
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
	require.Len(t, definitions, 8)
	v7Attempt := uuid.NewString()
	require.NoError(t, markRelaySchemaApplying(database, definitions[6], v7Attempt))
	require.NoError(t, runRelaySchemaBootstrapTransaction(database, definitions[:7], definitions[6], v7Attempt))
	require.False(t, database.Migrator().HasTable(&PlatformProviderCostAllocationEvidence{}))
	v7Catalog, err := getRelaySchemaCatalogFingerprintForVersion(database, relaySchemaV7FrozenVersion)
	require.NoError(t, err)
	require.Equal(t, relaySchemaV7PostgresCatalogSHA256, v7Catalog)
	var v7Ledger []RelaySchemaMigration
	require.NoError(t, database.Order("version ASC").Find(&v7Ledger).Error)
	require.Len(t, v7Ledger, 1)
	require.Equal(t, relaySchemaV7FrozenVersion, v7Ledger[0].Version)

	rollbackSentinel := errors.New("rollback v8 probe")
	err = database.Transaction(func(tx *gorm.DB) error {
		now := time.Now().UTC()
		result := tx.Model(&RelaySchemaState{}).Where("id = ?", relaySchemaStateSingletonID).Updates(map[string]any{
			"target_version":         relaySchemaV8FrozenVersion,
			"state":                  RelaySchemaStateApplying,
			"dirty":                  true,
			"attempt_id":             uuid.NewString(),
			"target_checksum":        relaySchemaV8FrozenChecksumSHA256,
			"target_catalog_sha256":  relaySchemaV8PostgresCatalogSHA256,
			"current_catalog_sha256": relaySchemaV7PostgresCatalogSHA256,
			"updated_at":             now,
		})
		if result.Error != nil || result.RowsAffected != 1 {
			return errors.New("could not stage v8 rollback probe")
		}
		if err := migrateRelaySchemaV8ProviderCostAllocationEvidence(tx); err != nil {
			return err
		}
		return rollbackSentinel
	})
	require.ErrorIs(t, err, rollbackSentinel)
	require.False(t, database.Migrator().HasTable(&PlatformProviderCostAllocationEvidence{}),
		"a failed v8 transaction must roll back its table and guards")

	v8Attempt := uuid.NewString()
	require.NoError(t, markRelaySchemaApplying(database, definitions[7], v8Attempt))
	require.NoError(t, runRelaySchemaDefinitionTransaction(database, definitions[7], v8Attempt))
	status, err := GetRelaySchemaStatus(database)
	require.NoError(t, err)
	require.True(t, status.Current)
	require.Equal(t, relaySchemaV8PostgresCatalogSHA256, status.CatalogSHA256)
	require.True(t, database.Migrator().HasTable(&PlatformProviderCostAllocationEvidence{}))
	var ledger []RelaySchemaMigration
	require.NoError(t, database.Order("version ASC").Find(&ledger).Error)
	require.Len(t, ledger, 2)
	require.Equal(t, v7Ledger[0], ledger[0], "v8 must not rewrite the frozen v7 ledger")
	require.Equal(t, relaySchemaV8FrozenVersion, ledger[1].Version)

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
