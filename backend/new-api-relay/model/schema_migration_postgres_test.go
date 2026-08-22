package model

import (
	"bytes"
	"context"
	"database/sql"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

type relaySchemaTestSynchronizedBuffer struct {
	mu     sync.Mutex
	buffer bytes.Buffer
}

type relaySchemaTestRuntimeActivity struct {
	State         string
	WaitEventType string
	WaitEvent     string
	Count         int64
}

func relaySchemaTestRuntimeActivityEvidence(admin *gorm.DB) string {
	var rows []relaySchemaTestRuntimeActivity
	err := admin.Raw(`SELECT COALESCE(state, '') AS state,
       COALESCE(wait_event_type, '') AS wait_event_type,
       COALESCE(wait_event, '') AS wait_event,
       count(*) AS count
  FROM pg_catalog.pg_stat_activity
 WHERE datname = pg_catalog.current_database()
   AND usename = ?
 GROUP BY state, wait_event_type, wait_event
 ORDER BY state, wait_event_type, wait_event`, relaySchemaTestRuntimeRole).Scan(&rows).Error
	if err != nil {
		return "<runtime activity unavailable>"
	}
	if len(rows) == 0 {
		return "<no runtime database sessions>"
	}
	parts := make([]string, 0, len(rows))
	for _, row := range rows {
		parts = append(parts, fmt.Sprintf("state=%q wait_type=%q wait=%q count=%d",
			row.State, row.WaitEventType, row.WaitEvent, row.Count))
	}
	return strings.Join(parts, "; ")
}

func relaySchemaTestProcessExitEvidence(err error) string {
	if err == nil {
		return "exit_code=0"
	}
	var exitErr *exec.ExitError
	if errors.As(err, &exitErr) {
		return fmt.Sprintf("exit_code=%d", exitErr.ExitCode())
	}
	return "wait_failed"
}

func (buffer *relaySchemaTestSynchronizedBuffer) Write(value []byte) (int, error) {
	buffer.mu.Lock()
	defer buffer.mu.Unlock()
	return buffer.buffer.Write(value)
}

func (buffer *relaySchemaTestSynchronizedBuffer) String() string {
	buffer.mu.Lock()
	defer buffer.mu.Unlock()
	return buffer.buffer.String()
}

const (
	relaySchemaTestOwnerRole         = "relay_schema_owner"
	relaySchemaTestMigratorRole      = "relay_schema_migrator"
	relaySchemaTestRuntimeRole       = "relay_runtime"
	relaySchemaTestEdgeRole          = "relay_download_edge"
	relaySchemaTestMigrationPassword = "relay-migration-password-0123456789"
	relaySchemaTestRuntimePassword   = "relay-runtime-password-0123456789-ab"
	relaySchemaTestEdgePassword      = "relay-edge-password-0123456789-abcdef"
	relaySchemaTestRoguePassword     = "relay-rogue-password-0123456789-abcdef"
)

func relaySchemaTestWriteProtectedFile(t *testing.T, name string, value string) (string, string) {
	t.Helper()
	sourceDirectory := ""
	readOnlyDirectory := ""
	if runtime.GOOS == "linux" {
		sourceDirectory = strings.TrimSpace(os.Getenv("TEST_PROTECTED_SECRET_SOURCE_DIR"))
		readOnlyDirectory = strings.TrimSpace(os.Getenv("TEST_PROTECTED_SECRET_READONLY_DIR"))
		require.NotEmpty(t, sourceDirectory,
			"real Linux schema gate requires TEST_PROTECTED_SECRET_SOURCE_DIR mounted read-write")
		require.NotEmpty(t, readOnlyDirectory,
			"real Linux schema gate requires the same directory at TEST_PROTECTED_SECRET_READONLY_DIR mounted read-only")
	} else {
		sourceDirectory = t.TempDir()
		readOnlyDirectory = sourceDirectory
	}
	require.True(t, filepath.IsAbs(sourceDirectory))
	require.True(t, filepath.IsAbs(readOnlyDirectory))
	sourcePath := filepath.Join(sourceDirectory, name)
	readOnlyPath := filepath.Join(readOnlyDirectory, name)
	require.NoError(t, os.WriteFile(sourcePath, []byte(value), 0o400))
	require.NoError(t, os.Chmod(sourcePath, 0o400))
	sourceInfo, err := os.Stat(sourcePath)
	require.NoError(t, err)
	readOnlyInfo, err := os.Stat(readOnlyPath)
	require.NoError(t, err)
	require.True(t, os.SameFile(sourceInfo, readOnlyInfo), "protected source and read-only alias must identify one inode")
	t.Cleanup(func() {
		_ = os.Chmod(sourcePath, 0o600)
		_ = os.Remove(sourcePath)
	})
	return sourcePath, readOnlyPath
}

func relaySchemaTestUseProtectedDatabaseDSN(t *testing.T, name string, dsn string) string {
	t.Helper()
	_, readOnlyPath := relaySchemaTestWriteProtectedFile(t, name, dsn)
	rawDSN, rawPresent := os.LookupEnv("SQL_DSN")
	require.NoError(t, os.Unsetenv("SQL_DSN"))
	t.Cleanup(func() {
		if rawPresent {
			_ = os.Setenv("SQL_DSN", rawDSN)
		} else {
			_ = os.Unsetenv("SQL_DSN")
		}
	})
	t.Setenv("SQL_DSN_FILE", readOnlyPath)
	t.Setenv("RELAY_DATABASE_SECRET_FILES_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED", "true")
	return readOnlyPath
}

func TestRelaySchemaPostgresFreshCatalogRoleAndLock(t *testing.T) {
	adminDSN := strings.TrimSpace(os.Getenv("TEST_POSTGRES_DSN"))
	if adminDSN == "" {
		t.Skip("set TEST_POSTGRES_DSN to run PostgreSQL schema migration integration")
	}
	parsed, err := url.Parse(adminDSN)
	require.NoError(t, err)
	databaseName := strings.TrimPrefix(parsed.Path, "/")
	require.Regexp(t, `^[a-z_][a-z0-9_]{0,62}$`, databaseName)

	admin, err := gorm.Open(postgres.Open(adminDSN), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	adminSQL, err := admin.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = adminSQL.Close() })
	var serverVersion string
	require.NoError(t, admin.Raw(`SHOW server_version`).Scan(&serverVersion).Error)
	require.True(t, strings.HasPrefix(serverVersion, "16."), "catalog artifact must be verified on PostgreSQL 16, got %s", serverVersion)
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
	}}), "the in-process lifecycle fixture must pin the same CA bytes as the release validator")
	clear(databaseCARaw)
	t.Setenv(relayMigrationDatabaseRoleEnvironment, relaySchemaTestMigratorRole)
	t.Setenv(relaySchemaOwnerRoleEnvironment, relaySchemaTestOwnerRole)
	t.Setenv(relayRuntimeDatabaseRoleEnvironment, relaySchemaTestRuntimeRole)
	// Protected roles are cluster-global. This disposable cluster models the
	// required dedicated-instance bootstrap by removing PUBLIC database access
	// outside the application database before any protected role exists.
	require.NoError(t, admin.Exec(`DO $dedicated_cluster$
DECLARE other_database record;
BEGIN
  FOR other_database IN SELECT datname FROM pg_database WHERE datname <> current_database()
  LOOP
    EXECUTE format('REVOKE CONNECT, CREATE, TEMPORARY ON DATABASE %I FROM PUBLIC', other_database.datname);
  END LOOP;
END
$dedicated_cluster$`).Error)
	require.NoError(t, admin.Exec(`CREATE ROLE relay_runtime NOLOGIN`).Error)
	err = ProvisionRelayDatabaseRoles(admin, []byte(relaySchemaTestMigrationPassword), []byte(relaySchemaTestRuntimePassword), []byte(relaySchemaTestEdgePassword))
	require.Error(t, err, "an unbound cluster-global role must never be silently taken over")
	require.NoError(t, admin.Exec(`DROP ROLE relay_runtime`).Error)
	lockHolder, err := adminSQL.Conn(context.Background())
	require.NoError(t, err)
	_, err = lockHolder.ExecContext(context.Background(), `SELECT pg_advisory_lock_shared($1)`, relayLifecycleAdvisoryLock)
	require.NoError(t, err)
	err = provisionRelayDatabaseRoles(admin, []byte(relaySchemaTestMigrationPassword), []byte(relaySchemaTestRuntimePassword), []byte(relaySchemaTestEdgePassword), "200ms")
	require.Error(t, err)
	var protectedRoleCount int64
	require.NoError(t, admin.Raw(`SELECT count(*) FROM pg_roles WHERE rolname IN ('relay_schema_owner','relay_schema_migrator','relay_runtime','relay_download_edge')`).Scan(&protectedRoleCount).Error)
	require.Zero(t, protectedRoleCount, "lock timeout must leave role topology untouched")
	_, err = lockHolder.ExecContext(context.Background(), `SELECT pg_advisory_unlock_shared($1)`, relayLifecycleAdvisoryLock)
	require.NoError(t, err)
	require.NoError(t, lockHolder.Close())
	err = ProvisionRelayDatabaseRoles(admin, []byte(relaySchemaTestMigrationPassword), []byte("invalid-runtime-password"), []byte(relaySchemaTestEdgePassword))
	require.Error(t, err)
	require.NoError(t, admin.Raw(`SELECT count(*) FROM pg_roles WHERE rolname IN ('relay_schema_owner','relay_schema_migrator','relay_runtime','relay_download_edge')`).Scan(&protectedRoleCount).Error)
	require.Zero(t, protectedRoleCount, "invalid second password must fail before any database mutation")
	err = ProvisionRelayDatabaseRoles(admin, []byte(relaySchemaTestMigrationPassword), []byte(relaySchemaTestMigrationPassword), []byte(relaySchemaTestEdgePassword))
	require.Error(t, err)
	require.NoError(t, admin.Raw(`SELECT count(*) FROM pg_roles WHERE rolname IN ('relay_schema_owner','relay_schema_migrator','relay_runtime','relay_download_edge')`).Scan(&protectedRoleCount).Error)
	require.Zero(t, protectedRoleCount, "duplicate protected-role passwords must fail before any database mutation")
	require.NoError(t, admin.Exec(`CREATE FUNCTION public.relay_hostile_role_pre_event()
RETURNS event_trigger LANGUAGE plpgsql AS $function$ BEGIN RETURN; END $function$`).Error)
	require.NoError(t, admin.Exec(`CREATE EVENT TRIGGER relay_hostile_role_pre_event
ON ddl_command_start EXECUTE FUNCTION public.relay_hostile_role_pre_event()`).Error)
	var hostileEventCount int64
	require.NoError(t, admin.Raw(`SELECT count(*) FROM pg_event_trigger WHERE evtname = 'relay_hostile_role_pre_event'`).Scan(&hostileEventCount).Error)
	require.Equal(t, int64(1), hostileEventCount)
	err = ProvisionRelayDatabaseRoles(admin, []byte(relaySchemaTestMigrationPassword), []byte(relaySchemaTestRuntimePassword), []byte(relaySchemaTestEdgePassword))
	require.Error(t, err, "role-pre must reject an untrusted event trigger before topology DDL")
	require.NoError(t, admin.Raw(`SELECT count(*) FROM pg_roles WHERE rolname IN ('relay_schema_owner','relay_schema_migrator','relay_runtime','relay_download_edge')`).Scan(&protectedRoleCount).Error)
	require.Zero(t, protectedRoleCount, "event-trigger preflight must leave protected role count unchanged")
	require.NoError(t, admin.Raw(`SELECT count(*) FROM pg_event_trigger WHERE evtname = 'relay_hostile_role_pre_event'`).Scan(&hostileEventCount).Error)
	require.Equal(t, int64(1), hostileEventCount, "event-trigger preflight must not rewrite hostile metadata")
	require.False(t, admin.Migrator().HasTable(&RelaySchemaState{}))
	require.NoError(t, admin.Exec(`DROP EVENT TRIGGER relay_hostile_role_pre_event`).Error)
	require.NoError(t, admin.Exec(`DROP FUNCTION public.relay_hostile_role_pre_event()`).Error)
	require.NoError(t, admin.Exec(`CREATE COLLATION public.relay_role_pre_failure FROM "C"`).Error)
	err = ProvisionRelayDatabaseRoles(admin, []byte(relaySchemaTestMigrationPassword), []byte(relaySchemaTestRuntimePassword), []byte(relaySchemaTestEdgePassword))
	require.Error(t, err)
	require.NoError(t, admin.Raw(`SELECT count(*) FROM pg_roles WHERE rolname IN ('relay_schema_owner','relay_schema_migrator','relay_runtime','relay_download_edge')`).Scan(&protectedRoleCount).Error)
	require.Zero(t, protectedRoleCount, "post-topology attestation failure must roll back the entire transaction")
	require.NoError(t, admin.Exec(`DROP COLLATION public.relay_role_pre_failure`).Error)
	require.NoError(t, ProvisionRelayDatabaseRoles(admin, []byte(relaySchemaTestMigrationPassword), []byte(relaySchemaTestRuntimePassword), []byte(relaySchemaTestEdgePassword)))
	require.NoError(t, admin.Exec(`ALTER ROLE relay_schema_migrator VALID UNTIL '2000-01-01'`).Error)
	require.NoError(t, admin.Exec(`ALTER ROLE relay_runtime SET statement_timeout = '1ms'`).Error)
	require.NoError(t, admin.Exec(`ALTER ROLE relay_runtime IN DATABASE "`+databaseName+`" SET default_transaction_read_only = on`).Error)
	require.NoError(t, admin.Exec(`GRANT SET ON PARAMETER session_replication_role TO relay_runtime`).Error)
	require.NoError(t, admin.Exec(`ALTER DATABASE "`+databaseName+`" SET session_replication_role = replica`).Error)
	require.NoError(t, ProvisionRelayDatabaseRoles(admin, []byte(relaySchemaTestMigrationPassword), []byte(relaySchemaTestRuntimePassword), []byte(relaySchemaTestEdgePassword)), "role provisioning must atomically normalize hostile residual settings")
	require.NoError(t, verifyRelayDatabaseRoleTopology(admin, relaySchemaTestOwnerRole, relaySchemaTestMigratorRole, relaySchemaTestRuntimeRole))

	migrationDSN := relaySchemaTestRoleDSN(t, parsed, relaySchemaTestMigratorRole, true)
	runtimeDSN := relaySchemaTestRoleDSN(t, parsed, relaySchemaTestRuntimeRole, false)
	relaySchemaTestUseProtectedDatabaseDSN(t, "fresh-migration-sql-dsn", migrationDSN)
	t.Setenv("RELAY_COMPAT_SOURCE_REVISION", strings.Repeat("a", 40))
	t.Setenv("RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256", "sha256:"+strings.Repeat("b", 64))
	common.SetMainDatabaseType(common.DatabaseTypePostgreSQL)

	migrationDB, err := gorm.Open(postgres.Open(migrationDSN), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	migrationSQL, err := migrationDB.DB()
	require.NoError(t, err)
	defer migrationSQL.Close()
	require.NoError(t, verifyRelayMigrationDatabaseRole(migrationDB))
	if relaySchemaV1PostgresCatalogSHA256 == "sha256:pending" {
		require.NoError(t, ensureRelaySchemaMetadata(migrationDB))
		var generatedCatalogSHA256 string
		rollbackProbe := errors.New("rollback catalog probe")
		err = migrationDB.Transaction(func(tx *gorm.DB) error {
			if err := setRelaySchemaMigrationLocalSearchPath(tx); err != nil {
				return err
			}
			if err := migrateRelaySchemaV1(tx); err != nil {
				return err
			}
			generatedCatalogSHA256, err = getRelaySchemaCatalogFingerprintForVersion(tx, 1)
			if err != nil {
				return err
			}
			return rollbackProbe
		})
		require.ErrorIs(t, err, rollbackProbe)
		t.Fatalf("freeze PostgreSQL v1 catalog artifact as %s", generatedCatalogSHA256)
	}

	originalDB, originalLogDB := DB, LOG_DB
	DB, LOG_DB = migrationDB, migrationDB
	defer func() { DB, LOG_DB = originalDB, originalLogDB }()
	require.NoError(t, admin.Exec(`CREATE FUNCTION public.relay_hostile_migration_event()
RETURNS event_trigger LANGUAGE plpgsql AS $function$ BEGIN RETURN; END $function$`).Error)
	require.NoError(t, admin.Exec(`CREATE EVENT TRIGGER relay_hostile_migration_event
ON ddl_command_start EXECUTE FUNCTION public.relay_hostile_migration_event()`).Error)
	_, err = RunRelaySchemaMigrations(context.Background(), "")
	require.Error(t, err, "migration must reject an untrusted event trigger before schema DDL")
	require.False(t, admin.Migrator().HasTable(&RelaySchemaState{}))
	require.False(t, admin.Migrator().HasTable(&RelaySchemaMigration{}))
	require.NoError(t, admin.Raw(`SELECT count(*) FROM pg_event_trigger WHERE evtname = 'relay_hostile_migration_event'`).Scan(&hostileEventCount).Error)
	require.Equal(t, int64(1), hostileEventCount, "migration preflight must leave hostile metadata unchanged")
	require.NoError(t, admin.Exec(`DROP EVENT TRIGGER relay_hostile_migration_event`).Error)
	require.NoError(t, admin.Exec(`DROP FUNCTION public.relay_hostile_migration_event()`).Error)
	require.NoError(t, admin.Exec(`ALTER ROLE relay_download_edge LOGIN`).Error)
	_, err = RunRelaySchemaMigrations(context.Background(), "")
	require.Error(t, err)
	require.False(t, admin.Migrator().HasTable(&RelaySchemaState{}))
	require.False(t, admin.Migrator().HasTable(&RelaySchemaMigration{}))
	require.NoError(t, admin.Exec(`ALTER ROLE relay_download_edge NOLOGIN`).Error)
	require.NoError(t, admin.Exec(`CREATE ROLE relay_pre_edge_assumer NOLOGIN`).Error)
	require.NoError(t, admin.Exec(`GRANT relay_download_edge TO relay_pre_edge_assumer`).Error)
	_, err = RunRelaySchemaMigrations(context.Background(), "")
	require.Error(t, err)
	require.False(t, admin.Migrator().HasTable(&RelaySchemaState{}))
	require.NoError(t, admin.Exec(`REVOKE relay_download_edge FROM relay_pre_edge_assumer`).Error)
	require.NoError(t, admin.Exec(`DROP ROLE relay_pre_edge_assumer`).Error)
	for _, privilege := range []string{"CREATE", "TEMPORARY"} {
		require.NoError(t, admin.Exec(`GRANT `+privilege+` ON DATABASE "`+databaseName+`" TO relay_download_edge`).Error)
		_, err = RunRelaySchemaMigrations(context.Background(), "")
		require.Error(t, err)
		require.False(t, admin.Migrator().HasTable(&RelaySchemaState{}))
		require.NoError(t, admin.Exec(`REVOKE `+privilege+` ON DATABASE "`+databaseName+`" FROM relay_download_edge`).Error)
	}
	require.NoError(t, admin.Exec(`GRANT CREATE ON SCHEMA public TO relay_download_edge`).Error)
	_, err = RunRelaySchemaMigrations(context.Background(), "")
	require.Error(t, err)
	require.False(t, admin.Migrator().HasTable(&RelaySchemaState{}))
	require.NoError(t, admin.Exec(`REVOKE CREATE ON SCHEMA public FROM relay_download_edge`).Error)
	require.NoError(t, admin.Exec(`CREATE TABLE public.relay_edge_preflight_acl_probe (id bigint)`).Error)
	require.NoError(t, admin.Exec(`ALTER TABLE public.relay_edge_preflight_acl_probe OWNER TO relay_schema_owner`).Error)
	require.NoError(t, admin.Exec(`GRANT SELECT ON public.relay_edge_preflight_acl_probe TO relay_download_edge`).Error)
	_, err = RunRelaySchemaMigrations(context.Background(), "")
	require.Error(t, err)
	require.False(t, admin.Migrator().HasTable(&RelaySchemaState{}))
	require.NoError(t, admin.Exec(`DROP TABLE public.relay_edge_preflight_acl_probe`).Error)
	require.NoError(t, admin.Exec(`CREATE SCHEMA relay_rogue_schema`).Error)
	require.NoError(t, admin.Exec(`GRANT CREATE ON SCHEMA relay_rogue_schema TO relay_schema_migrator`).Error)
	_, err = RunRelaySchemaMigrations(context.Background(), "")
	require.Error(t, err)
	require.False(t, admin.Migrator().HasTable(&RelaySchemaState{}))
	require.NoError(t, admin.Exec(`REVOKE CREATE ON SCHEMA relay_rogue_schema FROM relay_schema_migrator`).Error)
	require.NoError(t, admin.Exec(`CREATE TABLE relay_rogue_schema.owner_probe (id bigint)`).Error)
	require.NoError(t, admin.Exec(`ALTER TABLE relay_rogue_schema.owner_probe OWNER TO relay_schema_owner`).Error)
	_, err = RunRelaySchemaMigrations(context.Background(), "")
	require.Error(t, err)
	require.False(t, admin.Migrator().HasTable(&RelaySchemaState{}))
	require.NoError(t, admin.Exec(`DROP SCHEMA relay_rogue_schema CASCADE`).Error)
	result, err := RunRelaySchemaMigrations(context.Background(), "")
	require.NoError(t, err)
	require.True(t, result.Status.Current)
	require.Equal(t, int64(0), result.FromVersion)
	require.Equal(t, int64(2), result.ToVersion)
	require.Equal(t, int64(2), result.Status.BaselineVersion)
	require.Equal(t, int64(2), result.Status.CurrentVersion)
	require.Equal(t, int64(2), result.Status.TargetVersion)
	require.Equal(t, int64(1), result.Status.MinVersion)
	require.Equal(t, int64(2), result.Status.MaxVersion)
	var freshLedger []RelaySchemaMigration
	require.NoError(t, migrationDB.Order("version ASC").Find(&freshLedger).Error)
	require.Len(t, freshLedger, 1, "fresh v2 must not fabricate an unexecuted v1 ledger event")
	require.Equal(t, int64(2), freshLedger[0].Version)
	require.Equal(t, relaySchemaV2FrozenChecksumSHA256, freshLedger[0].Checksum)
	commitRecovery, err := RunRelaySchemaMigrations(context.Background(), "")
	require.NoError(t, err)
	require.Equal(t, "current", commitRecovery.State)
	require.NoError(t, resetRelayApplicationObjectACLs(migrationDB, relaySchemaTestOwnerRole))
	require.NoError(t, migrationDB.Exec(`GRANT SELECT ON TABLE public.relay_schema_state TO relay_download_edge`).Error)
	_, err = RunRelaySchemaMigrations(context.Background(), "")
	require.Error(t, err, "a partial current edge ACL must not be mistaken for a safe pre-stub")
	stillCurrent, statusErr := GetRelaySchemaStatus(migrationDB)
	require.NoError(t, statusErr)
	require.True(t, stillCurrent.Current)
	require.NoError(t, resetRelayApplicationObjectACLs(migrationDB, relaySchemaTestOwnerRole))
	stubRecovery, err := RunRelaySchemaMigrations(context.Background(), "")
	require.NoError(t, err)
	require.Equal(t, "current", stubRecovery.State)
	require.NoError(t, verifyRelayDownloadEdgeCurrentDatabaseRole(migrationDB, RelaySchemaTargetVersion, false))
	require.NoError(t, verifyRelayProtectedLoginRoleSCRAMCredentials(
		admin, relaySchemaTestMigratorRole, relaySchemaTestRuntimeRole, relaySchemaTestEdgeRole,
	))
	// Post may only attach LOGIN when every protected login role still has the
	// exact SCRAM verifier shape established by pre. NULL and legacy MD5 drift
	// must leave edge in B and require a complete, atomic pre rerun.
	require.NoError(t, admin.Exec(`ALTER ROLE relay_download_edge PASSWORD NULL`).Error)
	require.Error(t, FinalizeRelayDownloadEdgeDatabaseRole(admin))
	var edgeCanLogin bool
	require.NoError(t, admin.Raw(`SELECT rolcanlogin FROM pg_catalog.pg_roles WHERE rolname = 'relay_download_edge'`).Scan(&edgeCanLogin).Error)
	require.False(t, edgeCanLogin)
	require.NoError(t, ProvisionRelayDatabaseRoles(admin, []byte(relaySchemaTestMigrationPassword), []byte(relaySchemaTestRuntimePassword), []byte(relaySchemaTestEdgePassword)))
	stubRecovery, err = RunRelaySchemaMigrations(context.Background(), "")
	require.NoError(t, err)
	require.Equal(t, "current", stubRecovery.State)
	require.NoError(t, admin.Exec(`ALTER ROLE relay_runtime PASSWORD 'md5aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'`).Error)
	require.Error(t, FinalizeRelayDownloadEdgeDatabaseRole(admin))
	require.NoError(t, ProvisionRelayDatabaseRoles(admin, []byte(relaySchemaTestMigrationPassword), []byte(relaySchemaTestRuntimePassword), []byte(relaySchemaTestEdgePassword)))
	stubRecovery, err = RunRelaySchemaMigrations(context.Background(), "")
	require.NoError(t, err)
	require.Equal(t, "current", stubRecovery.State)
	require.NoError(t, FinalizeRelayDownloadEdgeDatabaseRole(admin))
	require.NoError(t, FinalizeRelayDownloadEdgeDatabaseRole(admin), "post finalization must be commit-ack retry safe")
	repeated, err := RunRelaySchemaMigrations(context.Background(), "")
	require.NoError(t, err)
	require.Equal(t, "current", repeated.State)
	require.True(t, repeated.Status.Current)

	runtimeDB, err := gorm.Open(postgres.Open(runtimeDSN), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	runtimeSQL, err := runtimeDB.DB()
	require.NoError(t, err)
	defer runtimeSQL.Close()
	status, err := RequireRelaySchemaCurrent(runtimeDB)
	require.NoError(t, err)
	require.Equal(t, relaySchemaV2PostgresCatalogSHA256, status.CatalogSHA256)
	require.NoError(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.Error(t, runtimeDB.Exec(`SET session_replication_role = replica`).Error)
	require.Error(t, migrationDB.Exec(`DELETE FROM relay_schema_migrations WHERE version = 2`).Error, "v2 ledger trigger must remain active after role hardening")
	require.NoError(t, admin.Exec(`ALTER ROLE relay_download_edge NOLOGIN`).Error)
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB), "edge commit state B must keep API readiness closed")
	require.NoError(t, resetRelayApplicationObjectACLs(migrationDB, relaySchemaTestOwnerRole))
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB), "edge pre-stub state C must keep API readiness closed")
	require.NoError(t, applyRelayDatabasePrivilegeManifestForVersion(migrationDB, status.CurrentVersion))
	require.NoError(t, applyRelayDownloadEdgeDatabasePrivilegeManifestForVersion(migrationDB, status.CurrentVersion))
	require.NoError(t, FinalizeRelayDownloadEdgeDatabaseRole(admin))
	require.NoError(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.NoError(t, admin.Exec(`GRANT CONNECT ON DATABASE postgres TO relay_runtime`).Error)
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB), "cross-database access must close API readiness")
	require.NoError(t, admin.Exec(`REVOKE CONNECT ON DATABASE postgres FROM relay_runtime`).Error)
	require.NoError(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	const foreignDatabase = "relay_role_foreign"
	require.NoError(t, admin.Exec(`CREATE DATABASE `+foreignDatabase).Error)
	t.Cleanup(func() { _ = admin.Exec(`DROP DATABASE IF EXISTS ` + foreignDatabase).Error })
	var foreignDatabaseOwner string
	require.NoError(t, admin.Raw(`SELECT pg_catalog.pg_get_userbyid(datdba)
FROM pg_catalog.pg_database WHERE datname = ?`, foreignDatabase).Scan(&foreignDatabaseOwner).Error)
	require.NotEmpty(t, foreignDatabaseOwner)
	require.NoError(t, admin.Exec(`REVOKE CONNECT, CREATE, TEMPORARY ON DATABASE `+foreignDatabase+` FROM PUBLIC`).Error)
	require.NoError(t, admin.Exec(`ALTER DATABASE `+foreignDatabase+` OWNER TO relay_runtime`).Error)
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB), "cross-database ownership must close API readiness")
	require.NoError(t, admin.Exec(`ALTER DATABASE `+foreignDatabase+` OWNER TO `+relayQuoteDatabaseIdentifier(foreignDatabaseOwner)).Error)
	require.NoError(t, admin.Exec(`DROP DATABASE `+foreignDatabase).Error)
	require.NoError(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	edgeDSN := relaySchemaTestRoleDSN(t, parsed, relaySchemaTestEdgeRole, false)
	edgeDB, err := gorm.Open(postgres.Open(edgeDSN), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	edgeSQL, err := edgeDB.DB()
	require.NoError(t, err)
	defer edgeSQL.Close()
	require.NoError(t, VerifyRelayDownloadEdgeDatabaseRole(edgeDB, status.CurrentVersion))
	requireSurfaceClosed := func(message string) {
		t.Helper()
		require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB), message+" must close runtime readiness")
		require.Error(t, VerifyRelayDownloadEdgeDatabaseRole(edgeDB, status.CurrentVersion), message+" must close edge readiness")
	}
	requireSurfaceHealthy := func(message string) {
		t.Helper()
		require.NoError(t, VerifyRelayRuntimeDatabaseRole(runtimeDB), message+" must restore runtime readiness")
		require.NoError(t, VerifyRelayDownloadEdgeDatabaseRole(edgeDB, status.CurrentVersion), message+" must restore edge readiness")
	}

	require.NoError(t, admin.Exec(`CREATE SCHEMA relay_surface_rogue_schema`).Error)
	requireSurfaceClosed("a rogue non-system schema")
	require.NoError(t, admin.Exec(`DROP SCHEMA relay_surface_rogue_schema`).Error)
	requireSurfaceHealthy("dropping the rogue schema")

	require.NoError(t, admin.Exec(`CREATE ROLE relay_surface_rogue NOLOGIN`).Error)
	require.NoError(t, admin.Exec(`GRANT CONNECT ON DATABASE "`+databaseName+`" TO relay_surface_rogue`).Error)
	requireSurfaceClosed("an unexpected database ACL grantee")
	require.NoError(t, admin.Exec(`REVOKE CONNECT ON DATABASE "`+databaseName+`" FROM relay_surface_rogue`).Error)
	require.NoError(t, admin.Exec(`GRANT USAGE ON SCHEMA public TO relay_surface_rogue`).Error)
	requireSurfaceClosed("an unexpected public schema ACL grantee")
	require.NoError(t, admin.Exec(`REVOKE USAGE ON SCHEMA public FROM relay_surface_rogue`).Error)
	requireSurfaceHealthy("revoking rogue database and schema ACLs")
	require.NoError(t, admin.Exec(`GRANT USAGE ON TYPE public.relay_schema_state TO relay_surface_rogue`).Error)
	requireSurfaceClosed("an unexpected public type ACL grantee")
	require.NoError(t, admin.Exec(`REVOKE USAGE ON TYPE public.relay_schema_state FROM relay_surface_rogue`).Error)
	require.NoError(t, admin.Transaction(func(tx *gorm.DB) error {
		if err := tx.Exec(`SET LOCAL ROLE relay_schema_owner`).Error; err != nil {
			return err
		}
		return tx.Exec(`REVOKE USAGE ON TYPE public.relay_schema_state FROM PUBLIC`).Error
	}))
	requireSurfaceClosed("a revoked public type ACL baseline")
	require.NoError(t, admin.Transaction(func(tx *gorm.DB) error {
		if err := tx.Exec(`SET LOCAL ROLE relay_schema_owner`).Error; err != nil {
			return err
		}
		return tx.Exec(`GRANT USAGE ON TYPE public.relay_schema_state TO PUBLIC`).Error
	}))
	requireSurfaceHealthy("restoring exact public type ACLs")
	require.NoError(t, admin.Exec(`ALTER TABLE public.custom_oauth_providers
ALTER COLUMN scopes SET DEFAULT 'openid  profile email'`).Error)
	requireSurfaceClosed("a whitespace-only semantic change to an application column default")
	require.NoError(t, admin.Exec(`ALTER TABLE public.custom_oauth_providers
ALTER COLUMN scopes SET DEFAULT 'openid profile email'`).Error)
	requireSurfaceHealthy("restoring the application column default")

	require.NoError(t, admin.Exec(`GRANT SELECT ON TABLE pg_catalog.pg_authid TO relay_surface_rogue`).Error)
	requireSurfaceClosed("a rogue pg_authid ACL")
	require.NoError(t, admin.Exec(`REVOKE SELECT ON TABLE pg_catalog.pg_authid FROM relay_surface_rogue`).Error)
	require.NoError(t, admin.Exec(`GRANT EXECUTE ON FUNCTION pg_catalog.pg_read_file(text) TO relay_surface_rogue`).Error)
	requireSurfaceClosed("a rogue pg_read_file ACL")
	require.NoError(t, admin.Exec(`REVOKE EXECUTE ON FUNCTION pg_catalog.pg_read_file(text) FROM relay_surface_rogue`).Error)
	requireSurfaceHealthy("revoking rogue system ACLs")

	require.NoError(t, admin.Exec(`CREATE FUNCTION pg_catalog.relay_surface_rogue_system_owner_probe()
RETURNS integer LANGUAGE sql IMMUTABLE AS 'SELECT 1'`).Error)
	require.NoError(t, admin.Exec(`ALTER FUNCTION pg_catalog.relay_surface_rogue_system_owner_probe()
OWNER TO relay_surface_rogue`).Error)
	requireSurfaceClosed("a rogue system object owner")
	require.NoError(t, admin.Exec(`DROP FUNCTION pg_catalog.relay_surface_rogue_system_owner_probe()`).Error)
	requireSurfaceHealthy("dropping the rogue-owned system object")
	require.NoError(t, admin.Exec(`CREATE FUNCTION pg_catalog.relay_surface_unversioned_system_probe()
RETURNS integer LANGUAGE sql IMMUTABLE AS 'SELECT 1'`).Error)
	requireSurfaceClosed("an unversioned pg_catalog object with the catalog owner and default ACL")
	require.NoError(t, admin.Exec(`DROP FUNCTION pg_catalog.relay_surface_unversioned_system_probe()`).Error)
	requireSurfaceHealthy("dropping the unversioned pg_catalog object")
	require.NoError(t, admin.Exec(`ALTER FUNCTION pg_catalog.abs(integer) SECURITY DEFINER`).Error)
	requireSurfaceClosed("an in-place security change to an existing pg_catalog function")
	require.NoError(t, admin.Exec(`ALTER FUNCTION pg_catalog.abs(integer) SECURITY INVOKER`).Error)
	requireSurfaceHealthy("restoring the existing pg_catalog function")
	require.NoError(t, admin.Exec(`ALTER FUNCTION pg_catalog.plpgsql_validator(oid) COST 2`).Error)
	requireSurfaceClosed("an in-place cost change to an allowed extension function")
	require.NoError(t, admin.Exec(`ALTER FUNCTION pg_catalog.plpgsql_validator(oid) COST 1`).Error)
	requireSurfaceHealthy("restoring the allowed extension function")
	require.NoError(t, admin.Exec(`REVOKE EXECUTE ON FUNCTION pg_catalog.abs(integer) FROM PUBLIC`).Error)
	requireSurfaceHealthy("restricting a system function ACL to a baseline subset")
	require.NoError(t, admin.Exec(`GRANT EXECUTE ON FUNCTION pg_catalog.abs(integer) TO PUBLIC`).Error)
	requireSurfaceHealthy("restoring a semantically exact system function ACL")
	require.NoError(t, admin.Exec(`ALTER TYPE pg_catalog.int4 RENAME TO " int4"`).Error)
	requireSurfaceClosed("a whitespace-bearing in-place identity change to an existing pg_catalog type")
	require.NoError(t, admin.Exec(`ALTER TYPE pg_catalog." int4" RENAME TO int4`).Error)
	requireSurfaceHealthy("restoring the existing pg_catalog type")
	require.NoError(t, admin.Exec(`ALTER COLLATION pg_catalog."C" RENAME TO relay_surface_c_collation_drift`).Error)
	requireSurfaceClosed("an in-place identity change to an existing pg_catalog collation")
	require.NoError(t, admin.Exec(`ALTER COLLATION pg_catalog.relay_surface_c_collation_drift RENAME TO "C"`).Error)
	requireSurfaceHealthy("restoring the existing pg_catalog collation")
	require.NoError(t, admin.Exec(`ALTER VIEW information_schema.columns RENAME TO relay_surface_columns_drift`).Error)
	requireSurfaceClosed("an in-place identity change to an existing information_schema view")
	require.NoError(t, admin.Exec(`ALTER VIEW information_schema.relay_surface_columns_drift RENAME TO columns`).Error)
	requireSurfaceHealthy("restoring the existing information_schema view")
	require.NoError(t, admin.Exec(`ALTER OPERATOR FAMILY pg_catalog.integer_ops USING btree
ADD OPERATOR 1 pg_catalog.< (text, text)`).Error)
	requireSurfaceClosed("an extra member of an existing pg_catalog operator family")
	require.NoError(t, admin.Exec(`ALTER OPERATOR FAMILY pg_catalog.integer_ops USING btree
DROP OPERATOR 1 (text, text)`).Error)
	requireSurfaceHealthy("restoring the pg_catalog operator family")
	require.NoError(t, admin.Exec(`ALTER TEXT SEARCH CONFIGURATION pg_catalog.english
ALTER MAPPING FOR asciiword WITH pg_catalog.simple`).Error)
	requireSurfaceClosed("a changed mapping of an existing pg_catalog text search configuration")
	require.NoError(t, admin.Exec(`ALTER TEXT SEARCH CONFIGURATION pg_catalog.english
ALTER MAPPING FOR asciiword WITH pg_catalog.english_stem`).Error)
	requireSurfaceHealthy("restoring the pg_catalog text search configuration")

	require.NoError(t, admin.Exec(`ALTER DEFAULT PRIVILEGES FOR ROLE relay_schema_owner IN SCHEMA public
GRANT SELECT ON TABLES TO relay_surface_rogue`).Error)
	requireSurfaceClosed("a protected-owner default ACL")
	require.NoError(t, admin.Exec(`ALTER DEFAULT PRIVILEGES FOR ROLE relay_schema_owner IN SCHEMA public
REVOKE SELECT ON TABLES FROM relay_surface_rogue`).Error)
	requireSurfaceHealthy("removing the protected-owner default ACL")

	require.NoError(t, admin.Exec(`GRANT CREATE ON TABLESPACE pg_default TO relay_runtime`).Error)
	requireSurfaceClosed("protected-role tablespace CREATE")
	require.NoError(t, admin.Exec(`REVOKE CREATE ON TABLESPACE pg_default FROM relay_runtime`).Error)
	var pgDefaultOwner string
	require.NoError(t, admin.Raw(`SELECT pg_catalog.pg_get_userbyid(spcowner)
FROM pg_catalog.pg_tablespace WHERE spcname = 'pg_default'`).Scan(&pgDefaultOwner).Error)
	require.NotEmpty(t, pgDefaultOwner)
	require.NoError(t, admin.Exec(`ALTER TABLESPACE pg_default OWNER TO relay_schema_owner`).Error)
	requireSurfaceClosed("protected-role tablespace ownership")
	require.NoError(t, admin.Exec(`ALTER TABLESPACE pg_default OWNER TO `+relayQuoteDatabaseIdentifier(pgDefaultOwner)).Error)
	requireSurfaceHealthy("restoring the tablespace surface")

	surfaceTablespaceName := "relay_surface_rogue_tablespace"
	surfaceTablespacePath := "/tmp/relay_surface_tablespace_" + databaseName
	require.NoError(t, admin.Exec(`COPY (SELECT '') TO PROGRAM 'mkdir -p `+surfaceTablespacePath+`'`).Error)
	moveOptionsToastIndex := func(tablespace string) error {
		return admin.Transaction(func(tx *gorm.DB) error {
			if err := tx.Exec(`SET LOCAL allow_system_table_mods = on`).Error; err != nil {
				return err
			}
			return tx.Exec(`DO $do$
DECLARE target_index regclass;
BEGIN
  SELECT index_object.indexrelid::regclass
    INTO target_index
    FROM pg_catalog.pg_index index_object
    JOIN pg_catalog.pg_class parent_relation ON parent_relation.reltoastrelid = index_object.indrelid
    JOIN pg_catalog.pg_namespace parent_namespace ON parent_namespace.oid = parent_relation.relnamespace
   WHERE parent_namespace.nspname = 'public' AND parent_relation.relname = 'options';
  IF target_index IS NULL THEN
    RAISE EXCEPTION 'options TOAST index is missing';
  END IF;
  EXECUTE pg_catalog.format('ALTER INDEX %s SET TABLESPACE ` +
				relayQuoteDatabaseIdentifier(tablespace) + `', target_index);
END $do$`).Error
		})
	}
	t.Cleanup(func() {
		_ = admin.Exec(`ALTER TABLE public.options SET TABLESPACE pg_default`).Error
		_ = moveOptionsToastIndex("pg_default")
		_ = admin.Exec(`DROP TABLESPACE IF EXISTS ` + surfaceTablespaceName).Error
		_ = admin.Exec(`COPY (SELECT '') TO PROGRAM 'rmdir ` + surfaceTablespacePath + `'`).Error
	})
	require.NoError(t, admin.Exec(`CREATE TABLESPACE `+surfaceTablespaceName+` LOCATION '`+surfaceTablespacePath+`'`).Error)
	require.NoError(t, admin.Exec(`ALTER TABLE public.options SET TABLESPACE `+surfaceTablespaceName).Error)
	requireSurfaceClosed("a public relation moved outside the database default tablespace")
	require.NoError(t, admin.Exec(`ALTER TABLE public.options SET TABLESPACE pg_default`).Error)
	require.NoError(t, moveOptionsToastIndex(surfaceTablespaceName))
	requireSurfaceClosed("a public relation TOAST index moved outside the database default tablespace")
	require.NoError(t, moveOptionsToastIndex("pg_default"))
	require.NoError(t, admin.Exec(`DROP TABLESPACE `+surfaceTablespaceName).Error)
	require.NoError(t, admin.Exec(`COPY (SELECT '') TO PROGRAM 'rmdir `+surfaceTablespacePath+`'`).Error)
	requireSurfaceHealthy("restoring public relation tablespaces")

	require.NoError(t, admin.Exec(`GRANT SELECT (value) ON TABLE public.options TO relay_runtime`).Error)
	requireSurfaceClosed("a redundant runtime column SELECT ACL")
	require.NoError(t, admin.Exec(`REVOKE SELECT (value) ON TABLE public.options FROM relay_runtime`).Error)
	requireSurfaceHealthy("removing the runtime column ACL")

	require.NoError(t, admin.Exec(`CREATE PUBLICATION relay_surface_rogue_publication`).Error)
	requireSurfaceClosed("an unused publication")
	require.NoError(t, admin.Exec(`DROP PUBLICATION relay_surface_rogue_publication`).Error)
	require.NoError(t, admin.Exec(`CREATE SUBSCRIPTION relay_surface_rogue_subscription
CONNECTION 'host=127.0.0.1 port=1 dbname=`+databaseName+` user=postgres'
PUBLICATION relay_surface_missing
WITH (connect = false, create_slot = false, enabled = false)`).Error)
	requireSurfaceClosed("an unused subscription")
	require.NoError(t, admin.Exec(`ALTER SUBSCRIPTION relay_surface_rogue_subscription
SET (slot_name = NONE)`).Error)
	require.NoError(t, admin.Exec(`DROP SUBSCRIPTION relay_surface_rogue_subscription`).Error)
	require.NoError(t, admin.Exec(`CREATE FOREIGN DATA WRAPPER relay_surface_rogue_fdw
NO HANDLER NO VALIDATOR`).Error)
	require.NoError(t, admin.Exec(`CREATE SERVER relay_surface_rogue_server
FOREIGN DATA WRAPPER relay_surface_rogue_fdw`).Error)
	require.NoError(t, admin.Exec(`CREATE USER MAPPING FOR relay_surface_rogue
SERVER relay_surface_rogue_server OPTIONS (user 'probe')`).Error)
	requireSurfaceClosed("an unused FDW, server, and user mapping")
	require.NoError(t, admin.Exec(`DROP SERVER relay_surface_rogue_server CASCADE`).Error)
	require.NoError(t, admin.Exec(`DROP FOREIGN DATA WRAPPER relay_surface_rogue_fdw`).Error)
	var rogueLargeObjectOID int64
	require.NoError(t, admin.Raw(`SELECT pg_catalog.lo_create(0)`).Scan(&rogueLargeObjectOID).Error)
	require.Greater(t, rogueLargeObjectOID, int64(0))
	requireSurfaceClosed("an unused large object")
	require.NoError(t, admin.Exec(`SELECT pg_catalog.lo_unlink(?)`, rogueLargeObjectOID).Error)
	requireSurfaceHealthy("removing unused global objects")

	require.NoError(t, admin.Exec(`CREATE STATISTICS public.relay_surface_rogue_statistics
ON id, username FROM public.users`).Error)
	requireSurfaceClosed("unsupported public extended statistics")
	require.NoError(t, admin.Exec(`DROP STATISTICS public.relay_surface_rogue_statistics`).Error)
	require.NoError(t, admin.Exec(`CREATE RULE relay_surface_rogue_rule AS
ON INSERT TO public.abilities DO INSTEAD NOTHING`).Error)
	requireSurfaceClosed("an unexpected public rewrite rule")
	require.NoError(t, admin.Exec(`DROP RULE relay_surface_rogue_rule ON public.abilities`).Error)
	require.NoError(t, admin.Exec(`CREATE TYPE public.relay_surface_rogue_composite AS (value text)`).Error)
	requireSurfaceClosed("an unsupported standalone composite type")
	require.NoError(t, admin.Exec(`DROP TYPE public.relay_surface_rogue_composite`).Error)
	require.NoError(t, admin.Exec(`CREATE CAST (text AS uuid) WITH INOUT AS ASSIGNMENT`).Error)
	requireSurfaceClosed("an unsupported user-defined cast")
	require.NoError(t, admin.Exec(`DROP CAST (text AS uuid)`).Error)
	require.NoError(t, admin.Exec(`CREATE TABLE public.relay_surface_fk_parent (id bigint PRIMARY KEY)`).Error)
	require.NoError(t, admin.Exec(`CREATE TABLE public.relay_surface_fk_child (
id bigint PRIMARY KEY,
parent_id bigint REFERENCES public.relay_surface_fk_parent(id)
)`).Error)
	require.NoError(t, verifyRelayUnusedDatabaseObjectSurface(admin),
		"an enabled, catalog-backed internal constraint trigger is structurally valid")
	require.NoError(t, admin.Exec(`ALTER TABLE public.relay_surface_fk_child DISABLE TRIGGER ALL`).Error)
	require.Error(t, verifyRelayUnusedDatabaseObjectSurface(admin),
		"a disabled internal constraint trigger must fail the exact unused-object surface")
	require.NoError(t, admin.Exec(`ALTER TABLE public.relay_surface_fk_child ENABLE TRIGGER ALL`).Error)
	require.NoError(t, verifyRelayUnusedDatabaseObjectSurface(admin))
	require.NoError(t, admin.Exec(`DROP TABLE public.relay_surface_fk_child, public.relay_surface_fk_parent`).Error)
	requireSurfaceHealthy("removing the internal-trigger probe")
	require.NoError(t, admin.Exec(`CREATE EXTENSION pgcrypto WITH SCHEMA public`).Error)
	requireSurfaceClosed("an unversioned extension")
	require.NoError(t, admin.Exec(`DROP EXTENSION pgcrypto`).Error)
	require.NoError(t, admin.Exec(`CREATE FUNCTION pg_catalog.relay_surface_rogue_extension_member()
RETURNS integer LANGUAGE sql IMMUTABLE AS 'SELECT 1'`).Error)
	require.NoError(t, admin.Exec(`ALTER EXTENSION plpgsql
ADD FUNCTION pg_catalog.relay_surface_rogue_extension_member()`).Error)
	requireSurfaceClosed("an unexpected member attached to an allowed extension")
	require.NoError(t, admin.Exec(`ALTER EXTENSION plpgsql
DROP FUNCTION pg_catalog.relay_surface_rogue_extension_member()`).Error)
	require.NoError(t, admin.Exec(`DROP FUNCTION pg_catalog.relay_surface_rogue_extension_member()`).Error)
	requireSurfaceHealthy("removing unsupported public objects")
	require.NoError(t, admin.Exec(`DROP ROLE relay_surface_rogue`).Error)
	require.NoError(t, migrationDB.Exec(`GRANT SELECT ON TABLE public.options TO relay_download_edge`).Error)
	require.Error(t, VerifyRelayDownloadEdgeDatabaseRole(edgeDB, status.CurrentVersion))
	require.NoError(t, migrationDB.Exec(`REVOKE SELECT ON TABLE public.options FROM relay_download_edge`).Error)
	require.NoError(t, migrationDB.Exec(`GRANT SELECT (value) ON TABLE public.options TO relay_download_edge`).Error)
	require.Error(t, VerifyRelayDownloadEdgeDatabaseRole(edgeDB, status.CurrentVersion))
	require.NoError(t, migrationDB.Exec(`REVOKE SELECT (value) ON TABLE public.options FROM relay_download_edge`).Error)
	require.NoError(t, migrationDB.Exec(`GRANT DELETE ON TABLE public.platform_download_edge_tickets TO relay_download_edge`).Error)
	require.Error(t, VerifyRelayDownloadEdgeDatabaseRole(edgeDB, status.CurrentVersion))
	require.NoError(t, migrationDB.Exec(`REVOKE DELETE ON TABLE public.platform_download_edge_tickets FROM relay_download_edge`).Error)
	require.NoError(t, admin.Exec(`CREATE ROLE relay_edge_parent NOLOGIN`).Error)
	require.NoError(t, admin.Exec(`GRANT relay_edge_parent TO relay_download_edge`).Error)
	require.Error(t, VerifyRelayDownloadEdgeDatabaseRole(edgeDB, status.CurrentVersion))
	require.NoError(t, admin.Exec(`REVOKE relay_edge_parent FROM relay_download_edge`).Error)
	require.NoError(t, admin.Exec(`DROP ROLE relay_edge_parent`).Error)
	require.NoError(t, VerifyRelayDownloadEdgeDatabaseRole(edgeDB, status.CurrentVersion))
	require.NoError(t, admin.Exec(`CREATE ROLE relay_edge_assumer NOLOGIN`).Error)
	require.NoError(t, admin.Exec(`GRANT relay_download_edge TO relay_edge_assumer`).Error)
	require.Error(t, VerifyRelayDownloadEdgeDatabaseRole(edgeDB, status.CurrentVersion))
	require.NoError(t, admin.Exec(`REVOKE relay_download_edge FROM relay_edge_assumer`).Error)
	require.NoError(t, admin.Exec(`GRANT relay_runtime TO relay_edge_assumer`).Error)
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.NoError(t, admin.Exec(`REVOKE relay_runtime FROM relay_edge_assumer`).Error)
	require.NoError(t, admin.Exec(`DROP ROLE relay_edge_assumer`).Error)
	require.NoError(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.NoError(t, VerifyRelayDownloadEdgeDatabaseRole(edgeDB, status.CurrentVersion))
	require.NoError(t, admin.Exec(`CREATE ROLE relay_rogue_acl LOGIN PASSWORD '`+relaySchemaTestRoguePassword+`' NOINHERIT`).Error)
	require.NoError(t, migrationDB.Exec(`GRANT UPDATE ON TABLE public.relay_schema_state TO relay_rogue_acl`).Error)
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.Error(t, VerifyRelayDownloadEdgeDatabaseRole(edgeDB, status.CurrentVersion))
	require.NoError(t, migrationDB.Exec(`REVOKE UPDATE ON TABLE public.relay_schema_state FROM relay_rogue_acl`).Error)
	require.NoError(t, migrationDB.Exec(`GRANT SELECT (value) ON TABLE public.options TO relay_rogue_acl`).Error)
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.NoError(t, applyRelayDatabasePrivilegeManifestForVersion(migrationDB, status.CurrentVersion))
	require.NoError(t, applyRelayDownloadEdgeDatabasePrivilegeManifestForVersion(migrationDB, status.CurrentVersion))
	require.NoError(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.NoError(t, VerifyRelayDownloadEdgeDatabaseRole(edgeDB, status.CurrentVersion))
	require.NoError(t, admin.Exec(`DROP ROLE relay_rogue_acl`).Error)

	require.NoError(t, admin.Exec(`ALTER ROLE relay_schema_owner CREATEROLE`).Error)
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.NoError(t, admin.Exec(`ALTER ROLE relay_schema_owner NOCREATEROLE`).Error)
	require.NoError(t, admin.Exec(`GRANT pg_read_server_files TO relay_schema_migrator`).Error)
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.NoError(t, admin.Exec(`REVOKE pg_read_server_files FROM relay_schema_migrator`).Error)
	require.NoError(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.NoError(t, admin.Exec(`CREATE SCHEMA relay_role_topology_drift`).Error)
	require.NoError(t, admin.Exec(`GRANT CREATE ON SCHEMA relay_role_topology_drift TO relay_schema_owner`).Error)
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.NoError(t, admin.Exec(`REVOKE CREATE ON SCHEMA relay_role_topology_drift FROM relay_schema_owner`).Error)
	require.NoError(t, admin.Exec(`GRANT CREATE ON SCHEMA relay_role_topology_drift TO relay_schema_migrator`).Error)
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.NoError(t, admin.Exec(`REVOKE CREATE ON SCHEMA relay_role_topology_drift FROM relay_schema_migrator`).Error)
	require.NoError(t, admin.Exec(`DROP SCHEMA relay_role_topology_drift`).Error)
	require.NoError(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	manifest, err := relayRuntimeDatabasePrivilegeManifestForVersion(status.CurrentVersion)
	require.NoError(t, err)
	for table := range manifest {
		var schemaName string
		require.NoError(t, admin.Raw(`
SELECT namespace.nspname
FROM pg_class relation
JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
WHERE relation.relname = ? AND relation.relkind IN ('r', 'p')`, table).Scan(&schemaName).Error)
		require.Equal(t, "public", schemaName, "application table %s must be created only in public", table)
	}
	require.Error(t, runtimeDB.Exec(`CREATE TABLE runtime_ddl_must_fail (id bigint)`).Error)
	require.Error(t, runtimeDB.Exec(`INSERT INTO relay_schema_state (id, state, updated_at) VALUES (2, 'clean', now())`).Error)
	channelProbe := Channel{
		Type: 1, Name: "runtime ability rebuild probe", Status: common.ChannelStatusEnabled,
		Models: "runtime-ability-probe", Group: "default", LegacyKey: "",
	}
	require.NoError(t, runtimeDB.Create(&channelProbe).Error)
	previousGlobalDB := DB
	DB = runtimeDB
	_, _, err = FixAbility()
	DB = previousGlobalDB
	require.NoError(t, err)
	var rebuiltAbilityCount int64
	require.NoError(t, runtimeDB.Model(&Ability{}).Where("channel_id = ?", channelProbe.Id).Count(&rebuiltAbilityCount).Error)
	require.Greater(t, rebuiltAbilityCount, int64(0))
	require.Error(t, runtimeDB.Exec(`TRUNCATE TABLE abilities`).Error)
	// This channel exists only to prove runtime DML can rebuild the derived
	// ability rows. Remove both synthetic rows once that assertion is complete;
	// the later protected-process gate intentionally validates a production-like
	// vault where every remaining native channel has an encrypted credential set.
	require.NoError(t, runtimeDB.Where("channel_id = ?", channelProbe.Id).Delete(&Ability{}).Error)
	require.NoError(t, runtimeDB.Where("id = ?", channelProbe.Id).Delete(&Channel{}).Error)

	var ownedSequences []relayCatalogSequence
	require.NoError(t, migrationDB.Raw(relayCatalogSequencesSQL).Scan(&ownedSequences).Error)
	var sequenceProbe relayCatalogSequence
	for _, sequence := range ownedSequences {
		if sequence.OwnedTable != "" && sequence.OwnedColumn != "" {
			sequenceProbe = sequence
			break
		}
	}
	require.NotEmpty(t, sequenceProbe.Name)
	qualifiedSequence := `public.` + quoteRelayDatabaseIdentifier(sequenceProbe.Name)
	require.NoError(t, migrationDB.Exec(`ALTER SEQUENCE `+qualifiedSequence+` OWNED BY NONE`).Error)
	require.NoError(t, migrationDB.Exec(`REVOKE USAGE ON SEQUENCE `+qualifiedSequence+` FROM relay_runtime`).Error)
	status, err = GetRelaySchemaStatus(runtimeDB)
	require.NoError(t, err)
	require.Equal(t, RelaySchemaStatusCorrupt, status.Classification)
	require.NoError(t, migrationDB.Exec(`ALTER SEQUENCE `+qualifiedSequence+` OWNED BY public.`+
		quoteRelayDatabaseIdentifier(sequenceProbe.OwnedTable)+`.`+quoteRelayDatabaseIdentifier(sequenceProbe.OwnedColumn)).Error)
	require.NoError(t, migrationDB.Exec(`GRANT USAGE ON SEQUENCE `+qualifiedSequence+` TO relay_runtime`).Error)
	_, err = RequireRelaySchemaCurrent(runtimeDB)
	require.NoError(t, err)

	require.NoError(t, migrationDB.Exec(`GRANT EXECUTE ON FUNCTION public.reject_relay_schema_migration_mutation() TO PUBLIC`).Error)
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.NoError(t, migrationDB.Exec(`REVOKE EXECUTE ON FUNCTION public.reject_relay_schema_migration_mutation() FROM PUBLIC`).Error)
	require.NoError(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))

	require.NoError(t, admin.Exec(`GRANT pg_read_server_files TO relay_runtime`).Error)
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.NoError(t, admin.Exec(`REVOKE pg_read_server_files FROM relay_runtime`).Error)
	require.NoError(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))

	require.NoError(t, admin.Exec(`CREATE ROLE relay_extra_schema_creator NOLOGIN`).Error)
	require.NoError(t, admin.Exec(`GRANT CREATE ON SCHEMA public TO relay_extra_schema_creator`).Error)
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.NoError(t, admin.Exec(`REVOKE CREATE ON SCHEMA public FROM relay_extra_schema_creator`).Error)
	require.NoError(t, admin.Exec(`DROP ROLE relay_extra_schema_creator`).Error)
	require.NoError(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))

	require.NoError(t, admin.Exec(`CREATE ROLE relay_extra_owner_member NOLOGIN`).Error)
	require.NoError(t, admin.Exec(`GRANT relay_schema_owner TO relay_extra_owner_member WITH INHERIT TRUE, SET FALSE`).Error)
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.NoError(t, admin.Exec(`REVOKE relay_schema_owner FROM relay_extra_owner_member`).Error)
	require.NoError(t, admin.Exec(`DROP ROLE relay_extra_owner_member`).Error)
	require.NoError(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))

	require.NoError(t, admin.Exec(`CREATE ROLE relay_intermediate_owner_member NOLOGIN`).Error)
	require.NoError(t, admin.Exec(`CREATE ROLE relay_indirect_owner_member NOLOGIN`).Error)
	require.NoError(t, admin.Exec(`GRANT relay_schema_owner TO relay_intermediate_owner_member WITH INHERIT FALSE, SET TRUE`).Error)
	require.NoError(t, admin.Exec(`GRANT relay_intermediate_owner_member TO relay_indirect_owner_member WITH INHERIT FALSE, SET TRUE`).Error)
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.NoError(t, admin.Exec(`REVOKE relay_intermediate_owner_member FROM relay_indirect_owner_member`).Error)
	require.NoError(t, admin.Exec(`REVOKE relay_schema_owner FROM relay_intermediate_owner_member`).Error)
	require.NoError(t, admin.Exec(`DROP ROLE relay_indirect_owner_member, relay_intermediate_owner_member`).Error)
	require.NoError(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))

	require.NoError(t, admin.Exec(`CREATE ROLE relay_rogue_object_owner NOLOGIN`).Error)
	require.NoError(t, admin.Exec(`ALTER TABLE public.users OWNER TO relay_rogue_object_owner`).Error)
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.NoError(t, admin.Exec(`ALTER TABLE public.users OWNER TO relay_schema_owner`).Error)
	require.NoError(t, admin.Exec(`ALTER FUNCTION public.reject_relay_schema_migration_mutation() OWNER TO relay_rogue_object_owner`).Error)
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.NoError(t, admin.Exec(`ALTER FUNCTION public.reject_relay_schema_migration_mutation() OWNER TO relay_schema_owner`).Error)
	require.NoError(t, admin.Exec(`DROP ROLE relay_rogue_object_owner`).Error)
	require.NoError(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))

	require.NoError(t, admin.Exec(`ALTER SCHEMA public OWNER TO `+relayQuoteDatabaseIdentifier(parsed.User.Username())).Error)
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.NoError(t, admin.Exec(`ALTER SCHEMA public OWNER TO relay_schema_owner`).Error)
	require.NoError(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))

	require.NoError(t, runtimeDB.Exec(`SET search_path = pg_catalog, public`).Error)
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.NoError(t, runtimeDB.Exec(`SET search_path = public`).Error)
	require.NoError(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))

	require.NoError(t, admin.Exec(`CREATE TYPE public.runtime_owned_schema_probe AS ENUM ('probe')`).Error)
	require.NoError(t, admin.Exec(`ALTER TYPE public.runtime_owned_schema_probe OWNER TO relay_runtime`).Error)
	require.Error(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))
	require.NoError(t, admin.Exec(`DROP TYPE public.runtime_owned_schema_probe`).Error)
	require.NoError(t, VerifyRelayRuntimeDatabaseRole(runtimeDB))

	relayRuntimeLifecycleDSNMu.Lock()
	previousRuntimeLifecycleDSN := relayRuntimeLifecycleDSN
	relayRuntimeLifecycleDSN = runtimeDSN
	relayRuntimeLifecycleDSNMu.Unlock()
	t.Cleanup(func() {
		relayRuntimeLifecycleDSNMu.Lock()
		relayRuntimeLifecycleDSN = previousRuntimeLifecycleDSN
		relayRuntimeLifecycleDSNMu.Unlock()
	})
	runtimeLock, err := AcquireRelayRuntimeLifecycleLock(context.Background(), runtimeDB)
	require.NoError(t, err)
	blockedContext, cancel := context.WithTimeout(context.Background(), 250*time.Millisecond)
	_, err = AcquireRelayLifecycleLock(blockedContext, migrationDB)
	cancel()
	require.Error(t, err)
	require.NoError(t, ReleaseRelayLifecycleLockBounded(runtimeLock))
	exclusiveLock, err := AcquireRelayLifecycleLock(context.Background(), migrationDB)
	require.NoError(t, err)
	require.NoError(t, ReleaseRelayLifecycleLockBounded(exclusiveLock))

	previousFencingEnabled := relayRuntimeDatabaseLifecycleFencing.Load()
	previousFencingHealthy := relayRuntimeDatabaseLifecycleHealthy.Load()
	t.Cleanup(func() {
		relayRuntimeDatabaseLifecycleFencing.Store(previousFencingEnabled)
		relayRuntimeDatabaseLifecycleHealthy.Store(previousFencingHealthy)
	})
	EnableRelayRuntimeDatabaseLifecycleFencing()
	relaySchemaTestUseProtectedDatabaseDSN(t, "fresh-runtime-sql-dsn", runtimeDSN)
	fencedRuntimeDB, fencedDatabaseType, err := chooseDB("SQL_DSN", false)
	require.NoError(t, err)
	require.Equal(t, common.DatabaseTypePostgreSQL, fencedDatabaseType)
	fencedRuntimeSQL, err := fencedRuntimeDB.DB()
	require.NoError(t, err)
	require.Zero(t, fencedRuntimeSQL.Stats().OpenConnections, "runtime pool must not connect before process lock A")
	fencedAnchor, err := AcquireRelayRuntimeLifecycleLock(context.Background(), fencedRuntimeDB)
	require.NoError(t, err)
	fencedFailures, stopFencedMonitor, err := MonitorRelayLifecycleLock(context.Background(), fencedAnchor, time.Hour)
	require.NoError(t, err)
	require.True(t, RelayRuntimeDatabaseLifecycleHealthy())
	fencedRuntimeSQL.SetMaxOpenConns(4)
	fencedRuntimeSQL.SetMaxIdleConns(4)
	var fencedAnchorPID int
	require.NoError(t, fencedAnchor.connection.QueryRowContext(context.Background(), `SELECT pg_catalog.pg_backend_pid()`).Scan(&fencedAnchorPID))
	poolConnections := make([]*sql.Conn, 0, 3)
	poolPIDs := make([]int, 0, 3)
	for range 3 {
		connection, connectionErr := fencedRuntimeSQL.Conn(context.Background())
		require.NoError(t, connectionErr)
		var pid int
		require.NoError(t, connection.QueryRowContext(context.Background(), `SELECT pg_catalog.pg_backend_pid()`).Scan(&pid))
		require.NotEqual(t, fencedAnchorPID, pid)
		poolConnections = append(poolConnections, connection)
		poolPIDs = append(poolPIDs, pid)
	}
	uniquePoolPIDs := make(map[int]struct{}, len(poolPIDs))
	for _, pid := range poolPIDs {
		uniquePoolPIDs[pid] = struct{}{}
	}
	require.Len(t, uniquePoolPIDs, 3)
	for _, pid := range poolPIDs {
		var poolFenceCount int64
		require.NoError(t, admin.Raw(`SELECT count(*)
  FROM pg_catalog.pg_locks
 WHERE pid = ? AND locktype = 'advisory'
   AND classid = ? AND objid IN (?, ?) AND objsubid = 1
   AND mode = 'ShareLock' AND granted`, pid, int64(0x41564944), int64(0x524f4f4c), int64(0x524f4f4d)).Scan(&poolFenceCount).Error)
		require.Equal(t, int64(2), poolFenceCount, "every physical runtime connection must hold shared A then B")
	}
	for _, connection := range poolConnections {
		require.NoError(t, connection.Close())
	}

	// Force disposal/recreation and prove the replacement physical session is
	// fenced identically. MaxIdle=0 is stricter than an ordinary lifetime expiry.
	fencedRuntimeSQL.SetMaxIdleConns(0)
	replacementConnection, err := fencedRuntimeSQL.Conn(context.Background())
	require.NoError(t, err)
	var replacementPID int
	require.NoError(t, replacementConnection.QueryRowContext(context.Background(), `SELECT pg_catalog.pg_backend_pid()`).Scan(&replacementPID))
	require.NotContains(t, poolPIDs, replacementPID)
	var replacementFenceCount int64
	require.NoError(t, admin.Raw(`SELECT count(*)
  FROM pg_catalog.pg_locks
 WHERE pid = ? AND locktype = 'advisory'
   AND classid = ? AND objid IN (?, ?) AND objsubid = 1
   AND mode = 'ShareLock' AND granted`, replacementPID, int64(0x41564944), int64(0x524f4f4c), int64(0x524f4f4d)).Scan(&replacementFenceCount).Error)
	require.Equal(t, int64(2), replacementFenceCount)
	// A pooled backend that loses its session locks must never be reused
	// unfenced. ResetSession discards it and the replacement re-enters A -> B.
	fencedRuntimeSQL.SetMaxIdleConns(1)
	_, err = replacementConnection.ExecContext(context.Background(), `SELECT pg_catalog.pg_advisory_unlock_all()`)
	require.NoError(t, err)
	require.NoError(t, replacementConnection.Close())
	replacementConnection, err = fencedRuntimeSQL.Conn(context.Background())
	require.NoError(t, err)
	var refencedPID int
	require.NoError(t, replacementConnection.QueryRowContext(context.Background(), `SELECT pg_catalog.pg_backend_pid()`).Scan(&refencedPID))
	require.NotEqual(t, replacementPID, refencedPID)
	require.NoError(t, admin.Raw(`SELECT count(*)
  FROM pg_catalog.pg_locks
 WHERE pid = ? AND locktype = 'advisory'
   AND classid = ? AND objid IN (?, ?) AND objsubid = 1
   AND mode = 'ShareLock' AND granted`, refencedPID, int64(0x41564944), int64(0x524f4f4c), int64(0x524f4f4d)).Scan(&replacementFenceCount).Error)
	require.Equal(t, int64(2), replacementFenceCount)
	var anchorTerminated bool
	require.NoError(t, admin.Raw(`SELECT pg_catalog.pg_terminate_backend(?)`, fencedAnchorPID).Scan(&anchorTerminated).Error)
	require.True(t, anchorTerminated)
	// Keep the heartbeat deliberately paused. The pool must reject a fresh
	// physical connection from the server-side pid+epoch proof even while the
	// local health bit is stale true.
	require.True(t, RelayRuntimeDatabaseLifecycleHealthy())
	probeContext, cancelProbe := context.WithTimeout(context.Background(), time.Second)
	require.Error(t, fencedRuntimeSQL.PingContext(probeContext), "anchor loss must reject every new/reused pool checkout")
	cancelProbe()
	require.True(t, RelayRuntimeDatabaseLifecycleHealthy(), "server-side anchor proof must not depend on heartbeat timing")
	stopFencedMonitor()
	select {
	case unexpectedFailure, open := <-fencedFailures:
		if open {
			t.Fatalf("paused heartbeat unexpectedly supplied a failure: %v", unexpectedFailure)
		}
	default:
	}
	relayRuntimeDatabaseLifecycleHealthy.Store(false)

	// Every physical pool session also owns A, so the offline command cannot
	// acquire process-exclusive A until the zombie pool has been closed.
	relaySchemaTestUseProtectedDatabaseDSN(t, "fresh-offline-migration-sql-dsn", migrationDSN)
	offlineContext, cancelOffline := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancelOffline()
	offlineDone := make(chan struct {
		lock *RelayLifecycleLock
		err  error
	}, 1)
	go func() {
		lock, lockErr := AcquireRelayLifecycleLock(offlineContext, migrationDB)
		offlineDone <- struct {
			lock *RelayLifecycleLock
			err  error
		}{lock: lock, err: lockErr}
	}()
	select {
	case outcome := <-offlineDone:
		t.Fatalf("offline process gate A passed while runtime pool remained open: %v", outcome.err)
	case <-time.After(250 * time.Millisecond):
	}
	require.NoError(t, replacementConnection.Close())
	require.NoError(t, fencedRuntimeSQL.Close())
	fencedAnchor.Close()
	require.Eventually(t, func() bool {
		var remainingRuntimeProcessHolders int64
		queryErr := admin.Raw(`SELECT count(*)
  FROM pg_catalog.pg_locks
 WHERE locktype = 'advisory'
   AND database = (SELECT oid FROM pg_catalog.pg_database WHERE datname = pg_catalog.current_database())
   AND classid = ? AND objid = ? AND objsubid = 1
   AND mode = 'ShareLock' AND granted`, int64(0x41564944), int64(0x524f4f4c)).Scan(&remainingRuntimeProcessHolders).Error
		return queryErr == nil && remainingRuntimeProcessHolders == 0
	}, 5*time.Second, 25*time.Millisecond, "closing the pool must synchronously converge to zero runtime process-lock holders")
	var offlineAfterAnchorLoss *RelayLifecycleLock
	select {
	case outcome := <-offlineDone:
		require.NoError(t, outcome.err)
		offlineAfterAnchorLoss = outcome.lock
	case <-time.After(10 * time.Second):
		t.Fatal("offline process gate did not pass after all runtime A/B holders closed")
	}
	require.NoError(t, migrationDB.Transaction(func(tx *gorm.DB) error {
		return acquireRelayLifecycleTransactionLock(tx)
	}))
	offlineAfterAnchorLoss.Close()

	require.NoError(t, migrationDB.Exec(`ALTER TABLE public.users ALTER COLUMN username TYPE text COLLATE "C" USING username::text`).Error)
	status, err = GetRelaySchemaStatus(runtimeDB)
	require.NoError(t, err)
	require.Equal(t, RelaySchemaStatusCorrupt, status.Classification)
	require.NoError(t, migrationDB.Exec(`ALTER TABLE public.users ALTER COLUMN username TYPE text COLLATE "default" USING username::text`).Error)
	_, err = RequireRelaySchemaCurrent(runtimeDB)
	require.NoError(t, err)

	require.NoError(t, migrationDB.Exec(`ALTER TABLE relay_schema_migrations DISABLE TRIGGER trg_relay_schema_migrations_no_mutation`).Error)
	status, err = GetRelaySchemaStatus(runtimeDB)
	require.NoError(t, err)
	require.Equal(t, RelaySchemaStatusCorrupt, status.Classification)
	require.NoError(t, migrationDB.Exec(`ALTER TABLE relay_schema_migrations ENABLE TRIGGER trg_relay_schema_migrations_no_mutation`).Error)
	_, err = RequireRelaySchemaCurrent(runtimeDB)
	require.NoError(t, err)
	relaySchemaTestMainProcessLifecycleLoss(t, admin, migrationDB, adminDSN, migrationDSN, runtimeDSN)
}

func relaySchemaTestMainProcessLifecycleLoss(
	t *testing.T,
	admin, migrationDB *gorm.DB,
	adminDSN, migrationDSN, runtimeDSN string,
) {
	t.Helper()
	fixture := relaySchemaTestPrepareProtectedLifecycleProcess(t, adminDSN, migrationDSN, runtimeDSN, "created", "created")
	relaySchemaTestMainProcessLifecycleLossWithFixture(t, admin, migrationDB, fixture)
}

func relaySchemaTestMainProcessLifecycleLossWithFixture(
	t *testing.T,
	admin, migrationDB *gorm.DB,
	fixture relaySchemaLifecycleProcessFixture,
) {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	require.NoError(t, err)
	port := listener.Addr().(*net.TCPAddr).Port
	require.NoError(t, listener.Close())
	temporaryRoot := t.TempDir()
	logDirectory := filepath.Join(temporaryRoot, "logs")
	require.NoError(t, os.Mkdir(logDirectory, 0o700))
	child := exec.Command(fixture.binaryPath,
		"--port", strconv.Itoa(port),
		"--log-dir", logDirectory,
	)
	child.Dir = temporaryRoot
	child.Env = append(append([]string(nil), fixture.environment...),
		"HOME="+temporaryRoot,
		"TMPDIR="+temporaryRoot,
		"TRUSTED_PROXIES=none",
	)
	var childOutput relaySchemaTestSynchronizedBuffer
	child.Stdout = &childOutput
	child.Stderr = &childOutput
	require.NoError(t, child.Start())
	childDone := make(chan error, 1)
	go func() { childDone <- child.Wait() }()
	childWaited := false
	t.Cleanup(func() {
		if childWaited {
			return
		}
		_ = child.Process.Kill()
		select {
		case <-childDone:
		case <-time.After(5 * time.Second):
		}
	})

	client := &http.Client{Timeout: 500 * time.Millisecond}
	statusURL := fmt.Sprintf("http://127.0.0.1:%d/api/status", port)
	ready := false
	readyDeadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(readyDeadline) {
		select {
		case waitErr := <-childDone:
			childWaited = true
			diagnostics, leaked := relaySchemaTestChildDiagnosticEvidence(
				logDirectory, childOutput.String(), fixture.secretCanaries,
			)
			if leaked {
				t.Fatal("real new-api child leaked a synthetic secret canary before HTTP admission")
			}
			t.Fatalf("real new-api child exited before HTTP admission (%s); activity=%s; diagnostics=%s",
				relaySchemaTestProcessExitEvidence(waitErr), relaySchemaTestRuntimeActivityEvidence(admin), diagnostics)
		default:
		}
		response, requestErr := client.Get(statusURL)
		if requestErr == nil {
			_ = response.Body.Close()
			if response.StatusCode == http.StatusOK {
				ready = true
				break
			}
		}
		time.Sleep(100 * time.Millisecond)
	}
	if !ready {
		diagnostics, leaked := relaySchemaTestChildDiagnosticEvidence(
			logDirectory, childOutput.String(), fixture.secretCanaries,
		)
		if leaked {
			t.Fatal("real new-api child leaked a synthetic secret canary while HTTP admission remained closed")
		}
		t.Fatalf("real new-api child did not open HTTP admission; activity=%s; diagnostics=%s",
			relaySchemaTestRuntimeActivityEvidence(admin), diagnostics)
	}
	var anchorPID int
	require.Eventually(t, func() bool {
		queryErr := admin.Raw(`SELECT process_lock.pid
  FROM pg_catalog.pg_locks process_lock
 WHERE process_lock.locktype = 'advisory'
   AND process_lock.database = (SELECT oid FROM pg_catalog.pg_database WHERE datname = pg_catalog.current_database())
   AND process_lock.classid = ? AND process_lock.objid = ? AND process_lock.objsubid = 1
   AND process_lock.mode = 'ShareLock' AND process_lock.granted
   AND EXISTS (
     SELECT 1 FROM pg_catalog.pg_locks epoch_lock
      WHERE epoch_lock.pid = process_lock.pid
        AND epoch_lock.locktype = 'advisory'
        AND epoch_lock.database = process_lock.database
        AND epoch_lock.mode = 'ExclusiveLock' AND epoch_lock.granted
        AND NOT (epoch_lock.classid = ? AND epoch_lock.objid IN (?, ?))
   )
 ORDER BY process_lock.pid LIMIT 1`, int64(0x41564944), int64(0x524f4f4c), int64(0x41564944), int64(0x524f4f4c), int64(0x524f4f4d)).Scan(&anchorPID).Error
		return queryErr == nil && anchorPID > 0
	}, 5*time.Second, 50*time.Millisecond, "real child lifecycle anchor was not observable")
	var terminated bool
	require.NoError(t, admin.Raw(`SELECT pg_catalog.pg_terminate_backend(?)`, anchorPID).Scan(&terminated).Error)
	require.True(t, terminated)

	select {
	case waitErr := <-childDone:
		childWaited = true
		diagnostics, leaked := relaySchemaTestChildDiagnosticEvidence(
			logDirectory, childOutput.String(), fixture.secretCanaries,
		)
		if leaked {
			t.Fatal("lifecycle-loss child leaked a synthetic secret canary")
		}
		var exitErr *exec.ExitError
		require.ErrorAs(t, waitErr, &exitErr, "lifecycle-loss child must exit non-zero; diagnostics=%s", diagnostics)
		require.NotZero(t, exitErr.ExitCode())
		require.Contains(t, diagnostics, "Relay runtime lifecycle lock was lost")
	case <-time.After(15 * time.Second):
		diagnostics, leaked := relaySchemaTestChildDiagnosticEvidence(
			logDirectory, childOutput.String(), fixture.secretCanaries,
		)
		if leaked {
			t.Fatal("lifecycle-loss child leaked a synthetic secret canary before its hard deadline")
		}
		t.Fatalf("lifecycle-loss child did not exit within the hard deadline; diagnostics=%s", diagnostics)
	}
	response, requestErr := client.Get(statusURL)
	if requestErr == nil {
		_ = response.Body.Close()
		require.Equal(t, http.StatusServiceUnavailable, response.StatusCode)
	}
	require.Eventually(t, func() bool {
		var holders int64
		queryErr := admin.Raw(`SELECT count(*) FROM pg_catalog.pg_locks
WHERE locktype = 'advisory'
  AND database = (SELECT oid FROM pg_catalog.pg_database WHERE datname = pg_catalog.current_database())
  AND classid = ? AND objid IN (?, ?) AND granted`,
			int64(0x41564944), int64(0x524f4f4c), int64(0x524f4f4d)).Scan(&holders).Error
		return queryErr == nil && holders == 0
	}, 5*time.Second, 50*time.Millisecond, "real child left lifecycle A/B holders after exit")

	offlineLock, err := AcquireRelayLifecycleLock(context.Background(), migrationDB)
	require.NoError(t, err)
	require.NoError(t, migrationDB.Transaction(func(tx *gorm.DB) error {
		return acquireRelayLifecycleTransactionLock(tx)
	}))
	require.NoError(t, ReleaseRelayLifecycleLockBounded(offlineLock))
}

// TestRelaySchemaPostgresProtectedLifecycleProcess is the focused real-process
// gate for an already provisioned disposable PostgreSQL database. The full
// fresh-schema gate invokes the same helper with an expected "created" receipt;
// this entry point avoids repeating the catalog drift matrix while diagnosing
// or re-running HTTP admission and anchor-loss behavior.
func TestRelaySchemaPostgresProtectedLifecycleProcess(t *testing.T) {
	adminDSN := strings.TrimSpace(os.Getenv("TEST_POSTGRES_LIFECYCLE_ADMIN_DSN"))
	migrationDSN := strings.TrimSpace(os.Getenv("TEST_POSTGRES_LIFECYCLE_MIGRATION_DSN"))
	runtimeDSN := strings.TrimSpace(os.Getenv("TEST_POSTGRES_LIFECYCLE_RUNTIME_DSN"))
	if adminDSN == "" && migrationDSN == "" && runtimeDSN == "" {
		t.Skip("set the three TEST_POSTGRES_LIFECYCLE_*_DSN values for the disposable focused gate")
	}
	require.NotEmpty(t, adminDSN)
	require.NotEmpty(t, migrationDSN)
	require.NotEmpty(t, runtimeDSN)
	provisionState := strings.TrimSpace(os.Getenv("TEST_POSTGRES_LIFECYCLE_PROVISION_STATE"))
	if provisionState == "" {
		provisionState = "preprovisioned"
	}
	require.Contains(t, []string{"created", "unchanged", "preprovisioned"}, provisionState)
	rootProvisionState := strings.TrimSpace(os.Getenv("TEST_POSTGRES_LIFECYCLE_ROOT_PROVISION_STATE"))
	if rootProvisionState == "" {
		rootProvisionState = provisionState
	}
	require.Contains(t, []string{"created", "unchanged", "preprovisioned"}, rootProvisionState)
	principalProvisionState := strings.TrimSpace(os.Getenv("TEST_POSTGRES_LIFECYCLE_PRINCIPAL_PROVISION_STATE"))
	if principalProvisionState == "" {
		principalProvisionState = provisionState
	}
	require.Contains(t, []string{"created", "unchanged", "preprovisioned"}, principalProvisionState)

	admin, err := gorm.Open(postgres.Open(adminDSN), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	adminSQL, err := admin.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = adminSQL.Close() })
	migrationDB, err := gorm.Open(postgres.Open(migrationDSN), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	migrationSQL, err := migrationDB.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = migrationSQL.Close() })
	requireLegacyFixtures := strings.EqualFold(
		strings.TrimSpace(os.Getenv("TEST_POSTGRES_LIFECYCLE_REQUIRE_LEGACY_FIXTURES")), "true",
	)
	legacyBefore, err := relaySchemaV2CaptureLegacyDataSnapshot(admin)
	require.NoError(t, err)
	if requireLegacyFixtures {
		require.NotEmpty(t, legacyBefore.UserDigest)
		require.NotEmpty(t, legacyBefore.RootDigest)
		require.NotEmpty(t, legacyBefore.RootSetupDigest)
		require.NotEmpty(t, legacyBefore.ChannelDigest)
		require.NotEmpty(t, legacyBefore.TaskDigest)
		require.NotEmpty(t, legacyBefore.RouteDigest)
		require.NotEmpty(t, legacyBefore.OptionDigest)
		require.NotEmpty(t, legacyBefore.ChannelCredentialDigest)
		require.NotEmpty(t, legacyBefore.TaskCredentialDigest)
		require.Equal(t, int64(1), legacyBefore.RootCount)
		require.Equal(t, int64(1), legacyBefore.SetupCount)
	}

	relaySchemaTestUseProtectedDatabaseDSN(t, "focused-lifecycle-migration-sql-dsn", migrationDSN)
	fixture := relaySchemaTestPrepareProtectedLifecycleProcess(
		t, adminDSN, migrationDSN, runtimeDSN, rootProvisionState, principalProvisionState,
	)
	relaySchemaTestMainProcessLifecycleLossWithFixture(t, admin, migrationDB, fixture)
	legacyAfter, err := relaySchemaV2CaptureLegacyDataSnapshot(admin)
	require.NoError(t, err)
	require.Equal(t, legacyBefore.UserDigest, legacyAfter.UserDigest)
	require.Equal(t, legacyBefore.SetupDigest, legacyAfter.SetupDigest)
	require.Equal(t, legacyBefore.ChannelDigest, legacyAfter.ChannelDigest)
	require.Equal(t, legacyBefore.TaskDigest, legacyAfter.TaskDigest)
	require.Equal(t, legacyBefore.RouteDigest, legacyAfter.RouteDigest)
	require.Equal(t, legacyBefore.AccountStateDigest, legacyAfter.AccountStateDigest)
	require.Equal(t, legacyBefore.OptionDigest, legacyAfter.OptionDigest)
	require.Equal(t, legacyBefore.ChannelCredentialDigest, legacyAfter.ChannelCredentialDigest)
	require.Equal(t, legacyBefore.TaskCredentialDigest, legacyAfter.TaskCredentialDigest)
	if rootProvisionState == "unchanged" {
		require.Equal(t, legacyBefore.RootDigest, legacyAfter.RootDigest)
		require.Equal(t, legacyBefore.RootSetupDigest, legacyAfter.RootSetupDigest)
		require.Equal(t, legacyBefore.RootCount, legacyAfter.RootCount)
		require.Equal(t, legacyBefore.SetupCount, legacyAfter.SetupCount)
	}
}

// TestRelaySchemaPostgresLegacyCandidateUpgrade is fed only a disposable
// PostgreSQL 16 database initialized by the previous immutable Relay image.
// It is intentionally separate from the fresh-catalog test so no real volume
// or operator credential can ever be selected accidentally.
func TestRelaySchemaPostgresLegacyCandidateUpgrade(t *testing.T) {
	if RelaySchemaTargetVersion != relaySchemaV1FrozenVersion {
		t.Skip("raw previous-candidate conversion belongs to the pinned v1 migrator; the release gate must then run the current v1-to-v2 bridge")
	}
	adminDSN := strings.TrimSpace(os.Getenv("TEST_POSTGRES_LEGACY_DSN"))
	if adminDSN == "" {
		t.Skip("set TEST_POSTGRES_LEGACY_DSN for the disposable previous-candidate schema")
	}
	parsed, err := url.Parse(adminDSN)
	require.NoError(t, err)
	databaseName := strings.TrimPrefix(parsed.Path, "/")
	require.Regexp(t, `^[a-z_][a-z0-9_]{0,62}$`, databaseName)

	admin, err := gorm.Open(postgres.Open(adminDSN), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	adminSQL, err := admin.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = adminSQL.Close() })
	var serverVersion string
	require.NoError(t, admin.Raw(`SHOW server_version`).Scan(&serverVersion).Error)
	require.True(t, strings.HasPrefix(serverVersion, "16."))
	require.False(t, admin.Migrator().HasTable(&RelaySchemaState{}), "fixture must be the pre-migration candidate")
	require.True(t, admin.Migrator().HasTable(&Channel{}))
	beforeEvidence, err := relayCaptureLegacyCandidateEvidence(admin)
	require.NoError(t, err)
	require.Equal(t, int64(1), beforeEvidence.UserCount)
	require.Equal(t, int64(1), beforeEvidence.SetupCount)
	require.Equal(t, int64(1), beforeEvidence.ChannelCount)
	require.Equal(t, int64(1), beforeEvidence.TaskCount)
	require.Equal(t, int64(1), beforeEvidence.RouteCount)
	require.NotEmpty(t, beforeEvidence.ChannelCredentialDigest)

	for _, statement := range []string{
		`CREATE ROLE relay_schema_owner NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`,
		`CREATE ROLE relay_schema_migrator LOGIN PASSWORD '` + relaySchemaTestMigrationPassword + `' NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`,
		`CREATE ROLE relay_runtime LOGIN PASSWORD '` + relaySchemaTestRuntimePassword + `' NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`,
		`CREATE ROLE relay_download_edge NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`,
		`GRANT relay_schema_owner TO relay_schema_migrator WITH INHERIT FALSE, SET TRUE`,
	} {
		require.NoError(t, admin.Exec(statement).Error, statement)
	}
	require.NoError(t, relayTransferLegacyPublicObjects(admin, relaySchemaTestOwnerRole))
	for _, statement := range []string{
		`REVOKE CREATE, TEMPORARY ON DATABASE "` + databaseName + `" FROM PUBLIC`,
		`REVOKE CREATE, TEMPORARY ON DATABASE "` + databaseName + `" FROM relay_schema_owner, relay_schema_migrator, relay_runtime`,
		`GRANT CONNECT ON DATABASE "` + databaseName + `" TO relay_schema_migrator, relay_runtime, relay_download_edge`,
		`ALTER SCHEMA public OWNER TO relay_schema_owner`,
		`REVOKE ALL ON SCHEMA public FROM PUBLIC`,
		`GRANT USAGE, CREATE ON SCHEMA public TO relay_schema_owner`,
		`GRANT USAGE ON SCHEMA public TO relay_schema_migrator, relay_runtime, relay_download_edge`,
		`REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC`,
		`REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC`,
		`REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC`,
		`ALTER ROLE relay_schema_migrator SET search_path = public`,
		`ALTER ROLE relay_runtime SET search_path = public`,
	} {
		require.NoError(t, admin.Exec(statement).Error, statement)
	}

	migrationDSN := relaySchemaTestRoleDSN(t, parsed, relaySchemaTestMigratorRole, true)
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("NODE_TYPE", "master")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "true")
	relaySchemaTestUseProtectedDatabaseDSN(t, "legacy-migration-sql-dsn", migrationDSN)
	t.Setenv(relayMigrationDatabaseRoleEnvironment, relaySchemaTestMigratorRole)
	t.Setenv(relaySchemaOwnerRoleEnvironment, relaySchemaTestOwnerRole)
	t.Setenv(relayRuntimeDatabaseRoleEnvironment, relaySchemaTestRuntimeRole)
	require.NoError(t, ProvisionRelayDatabaseRoles(admin, []byte(relaySchemaTestMigrationPassword), []byte(relaySchemaTestRuntimePassword), []byte(relaySchemaTestEdgePassword)))
	t.Setenv("RELAY_COMPAT_SOURCE_REVISION", strings.Repeat("c", 40))
	t.Setenv("RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256", "sha256:"+strings.Repeat("d", 64))
	_, keyringPath := relaySchemaTestWriteProtectedFile(t, "legacy-upgrade-provider-keyring.json", providerCredentialTestKeyringJSON)
	t.Setenv(providerCredentialKeyringFileEnvironment, keyringPath)
	inlineKeyring, hadInlineKeyring := os.LookupEnv(providerCredentialKeyringJSONEnvironment)
	require.NoError(t, os.Unsetenv(providerCredentialKeyringJSONEnvironment))
	t.Cleanup(func() {
		if hadInlineKeyring {
			_ = os.Setenv(providerCredentialKeyringJSONEnvironment, inlineKeyring)
		} else {
			_ = os.Unsetenv(providerCredentialKeyringJSONEnvironment)
		}
	})
	common.SetMainDatabaseType(common.DatabaseTypePostgreSQL)

	migrationDB, err := gorm.Open(postgres.Open(migrationDSN), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	migrationSQL, err := migrationDB.DB()
	require.NoError(t, err)
	defer migrationSQL.Close()
	originalDB, originalLogDB := DB, LOG_DB
	DB, LOG_DB = migrationDB, migrationDB
	defer func() { DB, LOG_DB = originalDB, originalLogDB }()
	referenceDSN := strings.TrimSpace(os.Getenv("TEST_POSTGRES_LEGACY_REFERENCE_DSN"))
	require.NotEmpty(t, referenceDSN, "legacy gate requires an independently migrated fresh PG16 reference")
	var legacySystemIdentifier string
	require.NoError(t, admin.Raw(`SELECT system_identifier::text FROM pg_control_system()`).Scan(&legacySystemIdentifier).Error)
	referenceIdentityDB, err := gorm.Open(postgres.Open(referenceDSN), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	referenceIdentitySQL, err := referenceIdentityDB.DB()
	require.NoError(t, err)
	defer referenceIdentitySQL.Close()
	var referenceSystemIdentifier string
	require.NoError(t, referenceIdentityDB.Raw(`SELECT system_identifier::text FROM pg_control_system()`).Scan(&referenceSystemIdentifier).Error)
	require.NotEmpty(t, legacySystemIdentifier)
	require.NotEqual(t, legacySystemIdentifier, referenceSystemIdentifier, "legacy and reference DSNs must identify different clusters")
	require.False(t, migrationDB.Migrator().HasTable(&RelaySchemaState{}), "public migration path must receive raw legacy metadata state")
	migrationResult, err := RunRelaySchemaMigrations(context.Background(), "")
	require.NoError(t, err)
	require.Equal(t, "migrated", migrationResult.State)
	status, err := RequireRelaySchemaCurrent(migrationDB)
	require.NoError(t, err)
	require.Equal(t, int64(1), status.BaselineVersion)
	require.False(t, status.FreshBootstrap)
	require.NoError(t, relayCompareLegacyCandidateCatalog(migrationDB, referenceDSN))
	afterEvidence, err := relayCaptureLegacyCandidateEvidence(migrationDB)
	require.NoError(t, err)
	require.Equal(t, beforeEvidence.UserDigest, afterEvidence.UserDigest)
	require.Equal(t, beforeEvidence.SetupDigest, afterEvidence.SetupDigest)
	require.Equal(t, beforeEvidence.ChannelDigest, afterEvidence.ChannelDigest)
	require.Equal(t, beforeEvidence.TaskDigest, afterEvidence.TaskDigest)
	require.Equal(t, beforeEvidence.RouteDigest, afterEvidence.RouteDigest)
	var acceptedChannelType int
	require.NoError(t, migrationDB.Raw(`SELECT accepted_channel_type FROM platform_generation_provider_routes WHERE id = 91001`).Scan(&acceptedChannelType).Error)
	require.Zero(t, acceptedChannelType, "legacy routes must receive the v1 accepted-channel default explicitly")
	var routeAccountEvidence struct {
		AccountStateID int64 `gorm:"column:account_state_id"`
		MatchingState  int64 `gorm:"column:matching_state"`
	}
	require.NoError(t, migrationDB.Raw(`
SELECT route.account_state_id,
       (SELECT count(*) FROM platform_generation_provider_account_states state
         WHERE state.id = route.account_state_id
           AND state.channel_id = route.channel_id
           AND state.key_index = route.key_index
           AND state.key_fingerprint = route.key_fingerprint
           AND state.rpm_window_seconds = route.rpm_window_seconds
           AND state.rpm_limit = route.rpm_limit
           AND state.active_limit = route.active_limit) AS matching_state
  FROM platform_generation_provider_routes route WHERE route.id = 91001`).Scan(&routeAccountEvidence).Error)
	require.Positive(t, routeAccountEvidence.AccountStateID, "legacy routes must be linked to the migrated shared account state")
	require.Equal(t, int64(1), routeAccountEvidence.MatchingState)
	require.NoError(t, relayVerifyLegacyCredentialMigrationEvidence(migrationDB))
	var retiredOptionEvidence struct {
		SourceCount int64  `gorm:"column:source_count"`
		TargetJSON  string `gorm:"column:target_json"`
	}
	require.NoError(t, migrationDB.Raw(`
SELECT (SELECT count(*) FROM options WHERE key = 'ApiInfo') AS source_count,
       COALESCE((SELECT value::jsonb::text FROM options WHERE key = 'console_setting.api_info'), '') AS target_json`).
		Scan(&retiredOptionEvidence).Error)
	require.Zero(t, retiredOptionEvidence.SourceCount)
	require.JSONEq(t, `[{"url":"https://api.example.invalid","route":"primary","description":"legacy fixture","color":"blue"}]`, retiredOptionEvidence.TargetJSON)
}

type relayLegacyCandidateEvidence struct {
	UserCount               int64  `gorm:"column:user_count"`
	UserDigest              string `gorm:"column:user_digest"`
	SetupCount              int64  `gorm:"column:setup_count"`
	SetupDigest             string `gorm:"column:setup_digest"`
	ChannelCount            int64  `gorm:"column:channel_count"`
	ChannelDigest           string `gorm:"column:channel_digest"`
	ChannelCredentialDigest string `gorm:"column:channel_credential_digest"`
	TaskCount               int64  `gorm:"column:task_count"`
	TaskDigest              string `gorm:"column:task_digest"`
	RouteCount              int64  `gorm:"column:route_count"`
	RouteDigest             string `gorm:"column:route_digest"`
}

func relayCaptureLegacyCandidateEvidence(db *gorm.DB) (relayLegacyCandidateEvidence, error) {
	var evidence relayLegacyCandidateEvidence
	err := db.Raw(`
SELECT
  (SELECT count(*) FROM users WHERE id = 91001 AND username = 'legacy-migration-owner-fixture') AS user_count,
  COALESCE((SELECT md5(to_jsonb(fixture)::text) FROM users fixture WHERE id = 91001), '') AS user_digest,
  (SELECT count(*) FROM setups WHERE id = 91001) AS setup_count,
  COALESCE((SELECT md5(to_jsonb(fixture)::text) FROM setups fixture WHERE id = 91001), '') AS setup_digest,
  (SELECT count(*) FROM channels WHERE id = 91001) AS channel_count,
  COALESCE((SELECT md5((to_jsonb(fixture) - 'key' - 'credential_set_version' - 'control_revision')::text)
              FROM channels fixture WHERE id = 91001), '') AS channel_digest,
  COALESCE((SELECT md5(key) FROM channels WHERE id = 91001), '') AS channel_credential_digest,
  (SELECT count(*) FROM tasks WHERE id = 91001 AND task_id = 'legacy-task-fixture-0001') AS task_count,
  COALESCE((SELECT md5(jsonb_set(to_jsonb(fixture), '{private_data}',
                         COALESCE(fixture.private_data::jsonb, '{}'::jsonb)
                           - 'key' - 'provider_credential_tenant_id' - 'provider_credential_version')::text)
              FROM tasks fixture WHERE id = 91001), '') AS task_digest,
  (SELECT count(*) FROM platform_generation_provider_routes WHERE id = 91001 AND route_key = 'legacy-route-fixture') AS route_count,
  COALESCE((SELECT md5((to_jsonb(fixture) - 'accepted_channel_type' - 'account_state_id' - 'updated_at')::text)
              FROM platform_generation_provider_routes fixture WHERE id = 91001), '') AS route_digest`).Scan(&evidence).Error
	return evidence, err
}

func relayVerifyLegacyCredentialMigrationEvidence(db *gorm.DB) error {
	var evidence struct {
		LegacyChannelKeyEmpty bool   `gorm:"column:legacy_channel_key_empty"`
		ChannelVersionPresent bool   `gorm:"column:channel_version_present"`
		ChannelFingerprint    string `gorm:"column:channel_fingerprint"`
		ChannelCiphertextSize int64  `gorm:"column:channel_ciphertext_size"`
		TaskLegacyKeyAbsent   bool   `gorm:"column:task_legacy_key_absent"`
		TaskVersionPresent    bool   `gorm:"column:task_version_present"`
		TaskTenant            string `gorm:"column:task_tenant"`
		TaskFingerprint       string `gorm:"column:task_fingerprint"`
		TaskCiphertextSize    int64  `gorm:"column:task_ciphertext_size"`
	}
	if err := db.Raw(`
SELECT channel.key = '' AS legacy_channel_key_empty,
       channel.credential_set_version <> '' AS channel_version_present,
       channel_version.key_set_fingerprint AS channel_fingerprint,
       octet_length(channel_version.ciphertext) AS channel_ciphertext_size,
       NOT (task.private_data::jsonb ? 'key') AS task_legacy_key_absent,
       COALESCE(task.private_data::jsonb ->> 'provider_credential_version', '') <> '' AS task_version_present,
       task.private_data::jsonb ->> 'provider_credential_tenant_id' AS task_tenant,
       task_version.key_fingerprint AS task_fingerprint,
       octet_length(task_version.ciphertext) AS task_ciphertext_size
  FROM channels channel
  JOIN provider_channel_credential_set_versions channel_version
    ON channel_version.credential_set_version = channel.credential_set_version
  JOIN tasks task ON task.id = 91001 AND task.channel_id = channel.id
  JOIN provider_credential_versions task_version
    ON task_version.credential_version = task.private_data::jsonb ->> 'provider_credential_version'
 WHERE channel.id = 91001`).Scan(&evidence).Error; err != nil {
		return err
	}
	if !evidence.LegacyChannelKeyEmpty || !evidence.ChannelVersionPresent ||
		evidence.ChannelFingerprint != "ca41acbc26fc869c3f4e79a15d59e4081e400099ddac028247f226a02d7aad1b" ||
		evidence.ChannelCiphertextSize <= 16 || !evidence.TaskLegacyKeyAbsent || !evidence.TaskVersionPresent ||
		evidence.TaskTenant != ProviderCredentialNativeTenantScope ||
		evidence.TaskFingerprint != "45027b56f8fc0ae3835b9e092baacee3fb286fa857c9e1d339efc78194ab6cdf" ||
		evidence.TaskCiphertextSize <= 16 {
		return errors.New("legacy credential migration evidence is incomplete")
	}
	var channel Channel
	if err := db.Session(&gorm.Session{SkipHooks: true}).Select("id, credential_set_version").Where("id = ?", 91001).Take(&channel).Error; err != nil {
		return errors.New("migrated channel credential reference is unavailable")
	}
	if err := HydrateChannelCredential(db, &channel); err != nil || channel.Key != "sk-legacy-channel-fixture-not-real" {
		return errors.New("migrated channel credential does not decrypt to the original fixture")
	}
	channel.Key = ""
	var task Task
	if err := db.Session(&gorm.Session{SkipHooks: true}).Where("id = ?", 91001).Take(&task).Error; err != nil {
		return errors.New("migrated task credential reference is unavailable")
	}
	plaintext, err := ResolveTaskProviderCredential(&task)
	if err != nil || plaintext != "sk-legacy-task-fixture-not-real" {
		return errors.New("migrated task credential does not decrypt to the original fixture")
	}
	plaintext = ""
	return nil
}

func relayCompareLegacyCandidateCatalog(db *gorm.DB, referenceDSN string) error {
	var candidate []relaySchemaCatalogObject
	if err := db.Raw(relayPostgresSchemaCatalogV1SQL).Scan(&candidate).Error; err != nil {
		return err
	}
	return relayCompareCatalogObjects(candidate, referenceDSN)
}

func relayCompareCatalogObjects(candidate []relaySchemaCatalogObject, referenceDSN string) error {
	referenceDB, err := gorm.Open(postgres.Open(referenceDSN), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	if err != nil {
		return err
	}
	referenceSQL, err := referenceDB.DB()
	if err != nil {
		return err
	}
	defer referenceSQL.Close()
	var reference []relaySchemaCatalogObject
	if err := referenceDB.Raw(relayPostgresSchemaCatalogV1SQL).Scan(&reference).Error; err != nil {
		return err
	}
	canonical := func(object relaySchemaCatalogObject) string {
		return object.Kind + "|" + object.Identity + "|" + strings.Join(strings.Fields(object.Definition), " ")
	}
	candidateSet := make(map[string]bool, len(candidate))
	referenceSet := make(map[string]bool, len(reference))
	for _, object := range candidate {
		candidateSet[canonical(object)] = true
	}
	for _, object := range reference {
		referenceSet[canonical(object)] = true
	}
	differences := make([]string, 0)
	for value := range candidateSet {
		if !referenceSet[value] {
			differences = append(differences, "legacy-only "+value)
		}
	}
	for value := range referenceSet {
		if !candidateSet[value] {
			differences = append(differences, "fresh-only "+value)
		}
	}
	sort.Strings(differences)
	if len(differences) > 0 {
		if len(differences) > 40 {
			differences = append(differences[:40], fmt.Sprintf("... %d additional catalog differences", len(differences)-40))
		}
		return fmt.Errorf("legacy catalog does not converge to v1:\n%s", strings.Join(differences, "\n"))
	}
	return nil
}

func relayTransferLegacyPublicObjects(db *gorm.DB, ownerRole string) error {
	if !databaseRoleNamePattern.MatchString(ownerRole) {
		return errors.New("legacy owner role is invalid")
	}
	quotedOwner := quoteRelayDatabaseIdentifier(ownerRole)
	var statements []string
	if err := db.Raw(`
SELECT CASE relation.relkind
         WHEN 'S' THEN format('ALTER SEQUENCE %I.%I OWNER TO ` + quotedOwner + `', namespace.nspname, relation.relname)
         WHEN 'v' THEN format('ALTER VIEW %I.%I OWNER TO ` + quotedOwner + `', namespace.nspname, relation.relname)
         WHEN 'm' THEN format('ALTER MATERIALIZED VIEW %I.%I OWNER TO ` + quotedOwner + `', namespace.nspname, relation.relname)
         WHEN 'f' THEN format('ALTER FOREIGN TABLE %I.%I OWNER TO ` + quotedOwner + `', namespace.nspname, relation.relname)
         ELSE format('ALTER TABLE %I.%I OWNER TO ` + quotedOwner + `', namespace.nspname, relation.relname)
       END
FROM pg_class relation
JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
WHERE namespace.nspname = 'public' AND relation.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
ORDER BY CASE WHEN relation.relkind = 'S' THEN 2 ELSE 1 END, relation.relkind, relation.relname`).Scan(&statements).Error; err != nil {
		return err
	}
	for _, statement := range statements {
		if err := db.Exec(statement).Error; err != nil {
			return err
		}
	}
	statements = nil
	if err := db.Raw(`
SELECT format('ALTER FUNCTION %s OWNER TO ` + quotedOwner + `', function.oid::regprocedure)
FROM pg_proc function JOIN pg_namespace namespace ON namespace.oid = function.pronamespace
WHERE namespace.nspname = 'public'
ORDER BY function.oid::regprocedure::text`).Scan(&statements).Error; err != nil {
		return err
	}
	for _, statement := range statements {
		if err := db.Exec(statement).Error; err != nil {
			return err
		}
	}
	return nil
}

func relaySchemaTestRoleDSN(t *testing.T, admin *url.URL, role string, assumeOwner bool) string {
	t.Helper()
	copyURL := *admin
	password := relaySchemaTestRoguePassword
	switch role {
	case relaySchemaTestMigratorRole:
		password = relaySchemaTestMigrationPassword
	case relaySchemaTestRuntimeRole:
		password = relaySchemaTestRuntimePassword
	case relaySchemaTestEdgeRole:
		password = relaySchemaTestEdgePassword
	}
	copyURL.User = url.UserPassword(role, password)
	query := copyURL.Query()
	query.Set("search_path", "public")
	if assumeOwner {
		query.Set("options", "-c role="+relaySchemaTestOwnerRole)
	}
	copyURL.RawQuery = query.Encode()
	return copyURL.String()
}
