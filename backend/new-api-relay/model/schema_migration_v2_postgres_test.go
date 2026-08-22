package model

import (
	"context"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

// TestRelaySchemaPostgresV1ToV2NoCatalogDelta consumes a database produced by
// an immutable v1 test image. It never invokes the live v1 migration from the
// v2 binary: only the attested v2 bridge and v2 principal manifests execute.
func TestRelaySchemaPostgresV1ToV2NoCatalogDelta(t *testing.T) {
	adminDSN := strings.TrimSpace(os.Getenv("TEST_POSTGRES_V1_UPGRADE_DSN"))
	if adminDSN == "" {
		t.Skip("set TEST_POSTGRES_V1_UPGRADE_DSN to run the PostgreSQL v1 to v2 bridge gate")
	}
	parsed, err := url.Parse(adminDSN)
	require.NoError(t, err)
	databaseName := strings.TrimPrefix(parsed.Path, "/")
	require.Regexp(t, `^[a-z_][a-z0-9_]{0,62}$`, databaseName)

	// Exercise the immutable v2 release contract even when the current binary
	// has advanced. This gate must prove the historical v1-to-v2 bridge without
	// accidentally executing the live v3 definition.
	frozenDefinitions := append([]relaySchemaMigrationDefinition(nil), relaySchemaMigrations()[:2]...)
	originalDefinitions := relaySchemaDefinitionsForRuntime
	originalContract := relaySchemaContractForRuntime
	relaySchemaDefinitionsForRuntime = func() []relaySchemaMigrationDefinition {
		return frozenDefinitions
	}
	relaySchemaContractForRuntime = func() RelaySchemaContract {
		return RelaySchemaContract{
			TargetVersion: relaySchemaV2FrozenVersion,
			MinVersion:    RelaySchemaMinVersion,
			MaxVersion:    relaySchemaV2FrozenVersion,
			Checksums: map[int64]string{
				1: RelaySchemaV1Checksum(),
				2: RelaySchemaV2Checksum(),
			},
		}
	}
	t.Cleanup(func() {
		relaySchemaDefinitionsForRuntime = originalDefinitions
		relaySchemaContractForRuntime = originalContract
	})

	admin, err := gorm.Open(postgres.Open(adminDSN), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	adminSQL, err := admin.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = adminSQL.Close() })
	var serverVersion string
	require.NoError(t, admin.Raw(`SHOW server_version`).Scan(&serverVersion).Error)
	require.True(t, strings.HasPrefix(serverVersion, "16."))

	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("NODE_TYPE", "master")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "true")
	databaseCAPath := parsed.Query().Get("sslrootcert")
	require.NotEmpty(t, databaseCAPath)
	require.True(t, filepath.IsAbs(databaseCAPath))
	databaseCARaw, err := os.ReadFile(databaseCAPath)
	require.NoError(t, err)
	t.Setenv(relayDatabaseCAFileEnvironment, databaseCAPath)
	require.NoError(t, common.InstallProtectedSecretFileSnapshots([]common.ProtectedSecretFileSnapshot{{
		Environment: relayDatabaseCAFileEnvironment,
		Value:       databaseCARaw,
	}}))
	clear(databaseCARaw)
	_, keyringPath := relaySchemaTestWriteProtectedFile(
		t, "v1-to-v2-provider-keyring.json", providerCredentialTestKeyringJSON,
	)
	t.Setenv(providerCredentialKeyringFileEnvironment, keyringPath)
	inlineKeyring, hadInlineKeyring := os.LookupEnv(providerCredentialKeyringJSONEnvironment)
	require.NoError(t, os.Unsetenv(providerCredentialKeyringJSONEnvironment))
	t.Cleanup(func() {
		if hadInlineKeyring {
			_ = os.Setenv(providerCredentialKeyringJSONEnvironment, inlineKeyring)
			return
		}
		_ = os.Unsetenv(providerCredentialKeyringJSONEnvironment)
	})
	t.Setenv(relayMigrationDatabaseRoleEnvironment, relaySchemaTestMigratorRole)
	t.Setenv(relaySchemaOwnerRoleEnvironment, relaySchemaTestOwnerRole)
	t.Setenv(relayRuntimeDatabaseRoleEnvironment, relaySchemaTestRuntimeRole)

	migrationDSN := relaySchemaTestRoleDSN(t, parsed, relaySchemaTestMigratorRole, true)
	runtimeDSN := relaySchemaTestRoleDSN(t, parsed, relaySchemaTestRuntimeRole, false)
	relaySchemaTestUseProtectedDatabaseDSN(t, "v1-to-v2-migration-sql-dsn", migrationDSN)
	t.Setenv("RELAY_COMPAT_SOURCE_REVISION", strings.Repeat("e", 40))
	t.Setenv("RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256", "sha256:"+strings.Repeat("f", 64))
	common.SetMainDatabaseType(common.DatabaseTypePostgreSQL)

	migrationDB, err := gorm.Open(postgres.Open(migrationDSN), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	migrationSQL, err := migrationDB.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = migrationSQL.Close() })
	originalDB, originalLogDB := DB, LOG_DB
	DB, LOG_DB = migrationDB, migrationDB
	t.Cleanup(func() { DB, LOG_DB = originalDB, originalLogDB })

	before, err := GetRelaySchemaStatus(migrationDB)
	require.NoError(t, err)
	require.Equal(t, RelaySchemaStatusCompatible, before.Classification)
	require.True(t, before.Compatible)
	require.False(t, before.Current)
	require.Equal(t, int64(1), before.BaselineVersion)
	require.Equal(t, int64(1), before.CurrentVersion)
	require.Equal(t, relaySchemaV1FrozenChecksumSHA256, before.CurrentChecksum)
	require.Equal(t, relaySchemaV1PostgresCatalogSHA256, before.CatalogSHA256)
	compatibleV1, err := RequireRelaySchemaCompatible(migrationDB)
	require.NoError(t, err)
	require.Equal(t, RelaySchemaStatusCompatible, compatibleV1.Classification)
	require.True(t, compatibleV1.Compatible)
	require.False(t, compatibleV1.Current)
	_, err = RequireRelaySchemaCurrent(migrationDB)
	require.ErrorContains(t, err, "Relay database schema is compatible")
	var v1Before RelaySchemaMigration
	require.NoError(t, migrationDB.First(&v1Before, relaySchemaV1FrozenVersion).Error)
	require.Equal(t, relaySchemaV1FrozenName, v1Before.Name)
	require.Equal(t, relaySchemaV1FrozenPhase, v1Before.Phase)
	require.Equal(t, relaySchemaV1FrozenChecksumSHA256, v1Before.Checksum)
	require.Equal(t, relaySchemaV1PostgresCatalogSHA256, v1Before.CatalogSHA256)
	var ledgerCount int64
	require.NoError(t, migrationDB.Model(&RelaySchemaMigration{}).Count(&ledgerCount).Error)
	require.Equal(t, int64(1), ledgerCount)
	require.NoError(t, verifyRelayRuntimeDatabasePrivilegeManifest(
		migrationDB, relaySchemaTestRuntimeRole, relaySchemaV1FrozenVersion,
	))
	edgeStateAErr := verifyRelayDownloadEdgeCurrentDatabaseRole(
		migrationDB, relaySchemaV1FrozenVersion, true,
	)
	edgeStateBErr := verifyRelayDownloadEdgeCurrentDatabaseRole(
		migrationDB, relaySchemaV1FrozenVersion, false,
	)
	require.True(t, (edgeStateAErr == nil) != (edgeStateBErr == nil),
		"the v1 input must have exactly the deployed or committed frozen edge manifest")
	legacyBefore, err := relayCaptureLegacyCandidateEvidence(migrationDB)
	require.NoError(t, err)
	require.Equal(t, int64(1), legacyBefore.UserCount)
	require.Zero(t, legacyBefore.SetupCount)
	require.Equal(t, int64(1), legacyBefore.ChannelCount)
	require.Equal(t, int64(1), legacyBefore.TaskCount)
	require.Equal(t, int64(1), legacyBefore.RouteCount)
	require.NoError(t, relayVerifyLegacyCredentialMigrationEvidence(migrationDB))
	legacyRowsBefore, err := relaySchemaV2CaptureLegacyDataSnapshot(migrationDB)
	require.NoError(t, err)
	require.Equal(t, int64(1), legacyRowsBefore.RootCount)
	require.Equal(t, int64(1), legacyRowsBefore.SetupCount)
	require.NotEmpty(t, legacyRowsBefore.RootDigest)
	require.NotEmpty(t, legacyRowsBefore.RootSetupDigest)
	require.Zero(t, legacyRowsBefore.ProtectedPrincipalUserCount)
	require.Zero(t, legacyRowsBefore.ProtectedPrincipalTokenCount)

	runtimeBefore, err := gorm.Open(postgres.Open(runtimeDSN), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	runtimeBeforeSQL, err := runtimeBefore.DB()
	require.NoError(t, err)
	require.ErrorContains(t, VerifyRelayRuntimeDatabaseRole(runtimeBefore), "Relay database schema is compatible",
		"protected API readiness must reject compatible-but-not-current v1 at the Current gate")
	require.NoError(t, runtimeBeforeSQL.Close())
	require.EqualError(t,
		VerifyRelayDownloadEdgeDatabaseRole(migrationDB, relaySchemaV2FrozenVersion),
		"Relay download edge requires the exact current schema catalog",
		"protected edge readiness must reject compatible-but-not-current v1 at the Current gate",
	)

	require.NoError(t, ProvisionRelayDatabaseRoles(
		admin,
		[]byte(relaySchemaTestMigrationPassword),
		[]byte(relaySchemaTestRuntimePassword),
		[]byte(relaySchemaTestEdgePassword),
	))
	require.NoError(t, verifyRelayDownloadEdgePreMigrationRole(migrationDB))
	require.NoError(t, verifyRelayRuntimeDatabasePrivilegeManifest(
		migrationDB, relaySchemaTestRuntimeRole, relaySchemaV1FrozenVersion,
	))

	result, err := RunRelaySchemaMigrations(context.Background(), "")
	require.NoError(t, err)
	require.Equal(t, "migrated", result.State)
	require.Equal(t, int64(1), result.FromVersion)
	require.Equal(t, int64(2), result.ToVersion)
	require.True(t, result.Status.Current)
	require.Equal(t, int64(1), result.Status.BaselineVersion)
	require.Equal(t, int64(2), result.Status.CurrentVersion)
	require.Equal(t, relaySchemaV2FrozenChecksumSHA256, result.Status.CurrentChecksum)
	require.Equal(t, relaySchemaV2PostgresCatalogSHA256, result.Status.CatalogSHA256)

	var ledger []RelaySchemaMigration
	require.NoError(t, migrationDB.Order("version ASC").Find(&ledger).Error)
	require.Len(t, ledger, 2)
	require.Equal(t, v1Before, ledger[0], "v2 bridge must not rewrite the append-only v1 event")
	require.Equal(t, relaySchemaV2FrozenVersion, ledger[1].Version)
	require.Equal(t, relaySchemaV2FrozenName, ledger[1].Name)
	require.Equal(t, relaySchemaV2FrozenPhase, ledger[1].Phase)
	require.Equal(t, relaySchemaV2FrozenChecksumSHA256, ledger[1].Checksum)
	require.Equal(t, relaySchemaV2PostgresCatalogSHA256, ledger[1].CatalogSHA256)
	require.Equal(t, relaySchemaV1PostgresCatalogSHA256, ledger[1].CatalogSHA256)
	require.NotEqual(t, ledger[0].SourceRevision, ledger[1].SourceRevision)
	require.NotEqual(t, ledger[0].SnapshotSHA256, ledger[1].SnapshotSHA256)
	legacyAfter, err := relayCaptureLegacyCandidateEvidence(migrationDB)
	require.NoError(t, err)
	require.Equal(t, legacyBefore, legacyAfter, "the no-catalog-delta bridge must not mutate legacy business fixtures")
	legacyRowsAfter, err := relaySchemaV2CaptureLegacyDataSnapshot(migrationDB)
	require.NoError(t, err)
	require.Equal(t, legacyRowsBefore, legacyRowsAfter,
		"the no-catalog-delta bridge must preserve every legacy fixture and encrypted credential row exactly")
	require.NoError(t, relayVerifyLegacyCredentialMigrationEvidence(migrationDB))

	require.NoError(t, FinalizeRelayDownloadEdgeDatabaseRole(admin))
	runtimeDB, err := gorm.Open(postgres.Open(runtimeDSN), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	runtimeSQL, err := runtimeDB.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = runtimeSQL.Close() })
	require.NoError(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.Error(t, migrationDB.Exec(`DELETE FROM relay_schema_migrations WHERE version = 1`).Error)
	require.Error(t, migrationDB.Exec(`DELETE FROM relay_schema_migrations WHERE version = 2`).Error)
}

type relaySchemaV2LegacyDataSnapshot struct {
	UserDigest                   string `gorm:"column:user_digest"`
	SetupDigest                  string `gorm:"column:setup_digest"`
	RootDigest                   string `gorm:"column:root_digest"`
	RootSetupDigest              string `gorm:"column:root_setup_digest"`
	ChannelDigest                string `gorm:"column:channel_digest"`
	TaskDigest                   string `gorm:"column:task_digest"`
	RouteDigest                  string `gorm:"column:route_digest"`
	AccountStateDigest           string `gorm:"column:account_state_digest"`
	OptionDigest                 string `gorm:"column:option_digest"`
	ChannelCredentialDigest      string `gorm:"column:channel_credential_digest"`
	TaskCredentialDigest         string `gorm:"column:task_credential_digest"`
	RootCount                    int64  `gorm:"column:root_count"`
	SetupCount                   int64  `gorm:"column:setup_count"`
	ProtectedPrincipalUserCount  int64  `gorm:"column:protected_principal_user_count"`
	ProtectedPrincipalTokenCount int64  `gorm:"column:protected_principal_token_count"`
}

func relaySchemaV2CaptureLegacyDataSnapshot(db *gorm.DB) (relaySchemaV2LegacyDataSnapshot, error) {
	var snapshot relaySchemaV2LegacyDataSnapshot
	if db == nil || db.Dialector.Name() != "postgres" {
		return snapshot, fmt.Errorf("legacy v2 bridge data snapshot requires PostgreSQL")
	}
	err := db.Raw(`
SELECT COALESCE((SELECT md5(to_jsonb(fixture)::text) FROM users fixture WHERE id = 91001), '') AS user_digest,
       COALESCE((SELECT md5(to_jsonb(fixture)::text) FROM setups fixture WHERE id = 91001), '') AS setup_digest,
	   COALESCE((SELECT md5(to_jsonb(fixture)::text) FROM users fixture
	              WHERE role = 100 AND username = 'lifecycle_root'), '') AS root_digest,
	   COALESCE((SELECT md5(to_jsonb(fixture)::text) FROM setups fixture
	              ORDER BY id ASC LIMIT 1), '') AS root_setup_digest,
       COALESCE((SELECT md5(to_jsonb(fixture)::text) FROM channels fixture WHERE id = 91001), '') AS channel_digest,
       COALESCE((SELECT md5(to_jsonb(fixture)::text) FROM tasks fixture WHERE id = 91001), '') AS task_digest,
       COALESCE((SELECT md5(to_jsonb(fixture)::text) FROM platform_generation_provider_routes fixture WHERE id = 91001), '') AS route_digest,
       COALESCE((SELECT md5(to_jsonb(fixture)::text)
                   FROM platform_generation_provider_account_states fixture
                  WHERE fixture.id = (SELECT account_state_id FROM platform_generation_provider_routes WHERE id = 91001)), '') AS account_state_digest,
       COALESCE((SELECT md5(to_jsonb(fixture)::text) FROM options fixture WHERE key = 'console_setting.api_info'), '') AS option_digest,
       COALESCE((SELECT md5(to_jsonb(fixture)::text)
                   FROM provider_channel_credential_set_versions fixture
                  WHERE fixture.credential_set_version = (SELECT credential_set_version FROM channels WHERE id = 91001)), '') AS channel_credential_digest,
       COALESCE((SELECT md5(to_jsonb(fixture)::text)
                   FROM provider_credential_versions fixture
                  WHERE fixture.credential_version =
                        (SELECT private_data::jsonb ->> 'provider_credential_version' FROM tasks WHERE id = 91001)), '') AS task_credential_digest,
       (SELECT count(*) FROM users WHERE role = 100) AS root_count,
       (SELECT count(*) FROM setups) AS setup_count,
       (SELECT count(*) FROM users
         WHERE remark = 'platform-relay-service-v1'
            OR left(lower(username), 5) = 'rsvc_') AS protected_principal_user_count,
       (SELECT count(*) FROM tokens
         WHERE left(name, 15) = 'platform-relay:') AS protected_principal_token_count`).Scan(&snapshot).Error
	return snapshot, err
}
