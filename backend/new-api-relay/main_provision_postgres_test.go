package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

const platformRelayRootProvisionChildEnvironment = "TEST_RELAY_ROOT_PROVISION_CHILD"

func TestPlatformRelayRootProvisionPostgresProcess(t *testing.T) {
	if os.Getenv(platformRelayRootProvisionChildEnvironment) != "1" {
		return
	}
	var output bytes.Buffer
	handled, err := handlePlatformRelayOfflineCommand(
		[]string{"/new-api", "relay-provision-root"},
		&output,
	)
	if !handled || err != nil {
		fmt.Fprintln(os.Stderr, "Relay offline command failed")
		os.Exit(1)
	}
	_, _ = os.Stdout.Write(output.Bytes())
}

type platformRelayRootProvisionProcessResult struct {
	stdout string
	stderr string
	err    error
}

const (
	platformRelayRootProvisionTestSourceRevision = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
	platformRelayRootProvisionTestSnapshotSHA256 = "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
	platformRelayRootProvisionTestSnapshotFiles  = 123
	platformRelayRootProvisionTestImageDigest    = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	platformRelayRootProvisionTestPlatformImage  = "registry.example.test/ai-video/platform@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	platformRelayRootProvisionTestPlatformSource = "cccccccccccccccccccccccccccccccccccccccc"
	platformRelayRootProvisionTestPlatformSnap   = "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
	platformRelayRootProvisionTestTrustKeys      = "sha256:6666666666666666666666666666666666666666666666666666666666666666"
)

func buildPlatformRelayRootProvisionBinary(t *testing.T) string {
	t.Helper()
	binaryName := "new-api-root-provision-test"
	if runtime.GOOS == "windows" {
		binaryName += ".exe"
	}
	binaryPath := filepath.Join(t.TempDir(), binaryName)
	linkerFlags := strings.Join([]string{
		"-X", "github.com/QuantumNous/new-api/service.platformRelayCompiledUpstreamRevision=" + service.PlatformRelayUpstreamGitRevision,
		"-X", "github.com/QuantumNous/new-api/service.platformRelayCompiledSourceRevision=" + platformRelayRootProvisionTestSourceRevision,
		"-X", "github.com/QuantumNous/new-api/service.platformRelayCompiledSnapshotSHA256=" + platformRelayRootProvisionTestSnapshotSHA256,
		"-X", fmt.Sprintf("github.com/QuantumNous/new-api/service.platformRelayCompiledSnapshotFileCount=%d", platformRelayRootProvisionTestSnapshotFiles),
		"-X", "github.com/QuantumNous/new-api/service.platformRelayCompiledRouteAcceptanceKeysSHA256=" + platformRelayRootProvisionTestTrustKeys,
	}, " ")
	command := exec.Command("go", "build", "-ldflags", linkerFlags, "-o", binaryPath, ".")
	output, err := command.CombinedOutput()
	require.NoError(t, err, "build real root provision binary: %s", string(output))
	return binaryPath
}

func writePlatformRelayRootProvisionProtectedFile(t *testing.T, name string, value string) (string, string) {
	t.Helper()
	sourceDirectory := ""
	readOnlyDirectory := ""
	if runtime.GOOS == "linux" {
		sourceDirectory = strings.TrimSpace(os.Getenv("TEST_PROTECTED_SECRET_SOURCE_DIR"))
		readOnlyDirectory = strings.TrimSpace(os.Getenv("TEST_PROTECTED_SECRET_READONLY_DIR"))
		require.NotEmpty(t, sourceDirectory,
			"real Linux process gate requires TEST_PROTECTED_SECRET_SOURCE_DIR mounted read-write")
		require.NotEmpty(t, readOnlyDirectory,
			"real Linux process gate requires the same directory at TEST_PROTECTED_SECRET_READONLY_DIR mounted read-only")
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
	t.Cleanup(func() {
		_ = os.Chmod(sourcePath, 0o600)
		_ = os.Remove(sourcePath)
	})
	return sourcePath, readOnlyPath
}

func replacePlatformRelayRootProvisionProtectedFile(t *testing.T, sourcePath string, value string) {
	t.Helper()
	require.NoError(t, os.Chmod(sourcePath, 0o600))
	require.NoError(t, os.WriteFile(sourcePath, []byte(value), 0o600))
	require.NoError(t, os.Chmod(sourcePath, 0o400))
}

func platformRelayRootProvisionTestSHA256(value []byte) string {
	digest := sha256.Sum256(value)
	return fmt.Sprintf("%x", digest)
}

type platformRelayRootProvisionTestRelease struct {
	ImageDigest                    string `json:"image_digest"`
	SourceRevision                 string `json:"source_revision"`
	SourceSnapshotSHA256           string `json:"source_snapshot_sha256"`
	SourceSnapshotFileCount        int    `json:"source_snapshot_file_count"`
	UpstreamRevision               string `json:"upstream_revision"`
	RouteAcceptanceTrustKeysSHA256 string `json:"route_acceptance_trust_keys_sha256"`
	PlatformImage                  string `json:"platform_image"`
	PlatformSourceRevision         string `json:"platform_source_revision"`
	PlatformSourceSnapshotSHA256   string `json:"platform_source_snapshot_sha256"`
	PlatformOrigin                 string `json:"platform_origin"`
	RelayOrigin                    string `json:"relay_origin"`
	EdgeOrigin                     string `json:"edge_origin"`
	RelayContractRevision          string `json:"relay_contract_revision"`
}

type platformRelayRootProvisionTestProof struct {
	SchemaVersion      int                                   `json:"schema_version"`
	Kind               string                                `json:"kind"`
	ProofID            string                                `json:"proof_id"`
	CreatedRelease     platformRelayRootProvisionTestRelease `json:"created_release"`
	RootPasswordSHA256 string                                `json:"root_password_sha256"`
}

type platformRelayRootProvisionTestDatabaseReleaseProof struct {
	SchemaVersion          int                                   `json:"schema_version"`
	Kind                   string                                `json:"kind"`
	RunID                  string                                `json:"run_id"`
	Generation             string                                `json:"generation"`
	RootProofID            string                                `json:"root_proof_id"`
	Release                platformRelayRootProvisionTestRelease `json:"release"`
	DatabaseEndpointSHA256 string                                `json:"database_endpoint_sha256"`
	Database               model.RelayDatabaseReleaseIdentity    `json:"database"`
}

func preparePlatformRelayRootProvisionIsolationFiles(
	t *testing.T,
	dsnFile string,
	username string,
	passwordFile string,
	databaseIdentity model.RelayDatabaseReleaseIdentity,
) (string, string, string, string, string) {
	t.Helper()
	dsnRaw, err := os.ReadFile(dsnFile)
	require.NoError(t, err)
	parsed, err := url.Parse(string(dsnRaw))
	require.NoError(t, err)
	query := parsed.Query()
	originalCAPath := query.Get("sslrootcert")
	require.NotEmpty(t, originalCAPath)
	caRaw, err := os.ReadFile(originalCAPath)
	require.NoError(t, err)
	name := "root-isolation-" + strings.ReplaceAll(uuid.NewString(), "-", "")
	_, caFile := writePlatformRelayRootProvisionProtectedFile(t, name+"-database-ca.pem", string(caRaw))
	query.Set("sslrootcert", caFile)
	parsed.RawQuery = query.Encode()
	dsnRaw = []byte(parsed.String())
	_, isolatedDSNFile := writePlatformRelayRootProvisionProtectedFile(t, name+"-runtime-dsn", string(dsnRaw))
	passwordRaw, err := os.ReadFile(passwordFile)
	require.NoError(t, err)
	databasePassword, present := parsed.User.Password()
	require.True(t, present)
	database := strings.TrimPrefix(parsed.Path, "/")
	release := platformRelayRootProvisionTestRelease{
		ImageDigest:                    platformRelayRootProvisionTestImageDigest,
		SourceRevision:                 platformRelayRootProvisionTestSourceRevision,
		SourceSnapshotSHA256:           platformRelayRootProvisionTestSnapshotSHA256,
		SourceSnapshotFileCount:        platformRelayRootProvisionTestSnapshotFiles,
		UpstreamRevision:               service.PlatformRelayUpstreamGitRevision,
		RouteAcceptanceTrustKeysSHA256: platformRelayRootProvisionTestTrustKeys,
		PlatformImage:                  platformRelayRootProvisionTestPlatformImage,
		PlatformSourceRevision:         platformRelayRootProvisionTestPlatformSource,
		PlatformSourceSnapshotSHA256:   platformRelayRootProvisionTestPlatformSnap,
		PlatformOrigin:                 "https://platform.example.test",
		RelayOrigin:                    "https://relay.example.test",
		EdgeOrigin:                     "https://downloads.example.test",
		RelayContractRevision:          "generations.v1",
	}
	proofID := strings.Repeat("8", 64)
	proofRaw, err := json.Marshal(platformRelayRootProvisionTestProof{
		SchemaVersion:      1,
		Kind:               "relay_root_secret_isolation_proof",
		ProofID:            proofID,
		CreatedRelease:     release,
		RootPasswordSHA256: platformRelayRootProvisionTestSHA256(passwordRaw),
	})
	require.NoError(t, err)
	proofSource, proofFile := writePlatformRelayRootProvisionProtectedFile(t, name+"-root-proof.json", string(proofRaw))
	if runtime.GOOS != "windows" {
		require.NoError(t, os.Chmod(proofSource, 0o600))
	}
	endpoint := fmt.Sprintf(
		"postgres-endpoint-v1\nhost=%s\nport=%s\ndatabase=%s",
		parsed.Hostname(), parsed.Port(), database,
	)
	target := fmt.Sprintf(
		"postgres-target-v1\nhost=%s\nport=%s\ndatabase=%s\nsslmode=%s\nsslrootcert=%s",
		parsed.Hostname(), parsed.Port(), database, query.Get("sslmode"), caFile,
	)
	receipt := map[string]any{
		"schema_version": 2,
		"kind":           "relay_secret_isolation_commitment",
		"run_id":         strings.Repeat("9", 64),
		"consumer":       service.PlatformRelaySecretIsolationConsumerRootBootstrap,
		"release":        release,
		"files": []map[string]string{
			{"id": "relay_database_ca", "sha256": platformRelayRootProvisionTestSHA256(caRaw)},
			{"id": "root_password", "sha256": platformRelayRootProvisionTestSHA256(passwordRaw)},
			{"id": "root_proof", "sha256": platformRelayRootProvisionTestSHA256(proofRaw)},
			{"id": "runtime_dsn", "sha256": platformRelayRootProvisionTestSHA256(dsnRaw)},
		},
		"semantics": []map[string]string{
			{"id": "database.runtime_dsn.endpoint", "sha256": platformRelayRootProvisionTestSHA256([]byte(endpoint))},
			{"id": "database.runtime_dsn.password", "sha256": platformRelayRootProvisionTestSHA256([]byte(databasePassword))},
			{"id": "database.runtime_dsn.target", "sha256": platformRelayRootProvisionTestSHA256([]byte(target))},
			{"id": "root.proof_id", "sha256": platformRelayRootProvisionTestSHA256([]byte(proofID))},
			{"id": "root.username", "sha256": platformRelayRootProvisionTestSHA256([]byte(username))},
		},
	}
	receiptRaw, err := json.Marshal(receipt)
	require.NoError(t, err)
	_, receiptFile := writePlatformRelayRootProvisionProtectedFile(t, name+"-receipt.json", string(receiptRaw))
	databaseReleaseProofRaw, err := json.Marshal(platformRelayRootProvisionTestDatabaseReleaseProof{
		SchemaVersion:          1,
		Kind:                   "relay_database_release_proof",
		RunID:                  strings.Repeat("7", 64),
		Generation:             "pre-root",
		RootProofID:            "",
		Release:                release,
		DatabaseEndpointSHA256: platformRelayRootProvisionTestSHA256([]byte(endpoint)),
		Database:               databaseIdentity,
	})
	require.NoError(t, err)
	_, databaseReleaseProofFile := writePlatformRelayRootProvisionProtectedFile(
		t,
		name+"-database-release-proof.json",
		string(databaseReleaseProofRaw),
	)
	return isolatedDSNFile, caFile, proofFile, receiptFile, databaseReleaseProofFile
}

func runPlatformRelayRootProvisionProcess(
	t *testing.T,
	binaryPath string,
	dsnFile string,
	username string,
	passwordFile string,
	ownerRole string,
	migrationRole string,
	runtimeRole string,
	databaseIdentity model.RelayDatabaseReleaseIdentity,
) *exec.Cmd {
	t.Helper()
	dsnFile, caFile, proofFile, receiptFile, databaseReleaseProofFile := preparePlatformRelayRootProvisionIsolationFiles(
		t, dsnFile, username, passwordFile, databaseIdentity,
	)
	command := exec.Command(binaryPath, "relay-provision-root")
	allowedHostEnvironment := map[string]struct{}{
		"PATH":       {},
		"SystemRoot": {},
		"SYSTEMROOT": {},
		"WINDIR":     {},
		"TEMP":       {},
		"TMP":        {},
		"TMPDIR":     {},
		"TZ":         {},
	}
	environment := make([]string, 0, len(os.Environ())+17)
	for _, entry := range os.Environ() {
		key, _, found := strings.Cut(entry, "=")
		if _, allowed := allowedHostEnvironment[key]; !found || !allowed {
			continue
		}
		environment = append(environment, entry)
	}
	command.Env = append(environment,
		"APP_ENV=production",
		"DEPLOYMENT_ENV=production",
		"NODE_TYPE=master",
		"SQL_DSN_FILE="+dsnFile,
		"RELAY_DATABASE_CA_FILE="+caFile,
		"RELAY_ROOT_SECRET_ISOLATION_PROOF_FILE="+proofFile,
		"RELAY_SECRET_ISOLATION_RECEIPT_FILE="+receiptFile,
		service.RelayDatabaseReleaseProofFileEnvironment+"="+databaseReleaseProofFile,
		"RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED=true",
		"RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=true",
		"RELAY_DATABASE_SECRET_FILES_REQUIRED=true",
		"RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED=true",
		platformRelayRootUsernameEnvironment+"="+username,
		platformRelayRootPasswordFileEnvironment+"="+passwordFile,
		"RELAY_SCHEMA_OWNER_DATABASE_ROLE="+ownerRole,
		"RELAY_MIGRATION_DATABASE_ROLE="+migrationRole,
		"RELAY_RUNTIME_DATABASE_ROLE="+runtimeRole,
		"RELAY_COMPAT_SOURCE_REVISION="+platformRelayRootProvisionTestSourceRevision,
		"RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256="+platformRelayRootProvisionTestSnapshotSHA256,
		fmt.Sprintf("RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT=%d", platformRelayRootProvisionTestSnapshotFiles),
		"RELAY_COMPAT_IMAGE_DIGEST="+platformRelayRootProvisionTestImageDigest,
		"RELAY_COMPAT_UPSTREAM_REVISION="+service.PlatformRelayUpstreamGitRevision,
		"RELAY_COMPAT_ROUTE_ACCEPTANCE_TRUST_KEYS_SHA256="+platformRelayRootProvisionTestTrustKeys,
		"PLATFORM_IMAGE="+platformRelayRootProvisionTestPlatformImage,
		"PLATFORM_SOURCE_REVISION="+platformRelayRootProvisionTestPlatformSource,
		"PLATFORM_SOURCE_SNAPSHOT_SHA256="+platformRelayRootProvisionTestPlatformSnap,
		"PLATFORM_PUBLIC_BASE_URL=https://platform.example.test",
		"NEW_API_RELAY_PUBLIC_BASE_URL=https://relay.example.test",
		"DOWNLOAD_GATEWAY_PUBLIC_BASE_URL=https://downloads.example.test",
		"PLATFORM_NEW_API_RELAY_CONTRACT_REVISION=generations.v1",
	)
	return command
}

func TestPlatformRelayRootProvisionPostgresSerializesFullMigrationAndProvisioning(t *testing.T) {
	adminDSN := strings.TrimSpace(os.Getenv("TEST_POSTGRES_DSN"))
	if adminDSN == "" {
		t.Skip("set TEST_POSTGRES_DSN to run the real PostgreSQL root-provisioning concurrency test")
	}
	ctx := context.Background()
	admin, err := pgx.Connect(ctx, adminDSN)
	require.NoError(t, err)
	var serverVersion string
	require.NoError(t, admin.QueryRow(ctx, `SHOW server_version`).Scan(&serverVersion))
	require.True(t, strings.HasPrefix(serverVersion, "16."), "root provisioning gate requires PostgreSQL 16")

	suffix := strings.ReplaceAll(uuid.NewString(), "-", "")[:10]
	databaseName := "root_provision_" + suffix
	ownerRole := "relay_root_owner_" + suffix
	migrationRole := "relay_root_migrator_" + suffix
	const (
		runtimeRole = "relay_runtime"
		edgeRole    = "relay_download_edge"
	)
	quoteIdentifier := func(value string) string { return pgx.Identifier{value}.Sanitize() }
	var fixedRoleCount int
	require.NoError(t, admin.QueryRow(ctx, `SELECT count(*) FROM pg_roles WHERE rolname = ANY($1)`, []string{runtimeRole, edgeRole}).Scan(&fixedRoleCount))
	require.Zero(t, fixedRoleCount, "TEST_POSTGRES_DSN must identify a dedicated root-provision gate cluster")

	t.Cleanup(func() {
		_, _ = admin.Exec(ctx, `DROP DATABASE IF EXISTS `+quoteIdentifier(databaseName)+` WITH (FORCE)`)
		for _, role := range []string{migrationRole, runtimeRole, ownerRole, edgeRole} {
			_, _ = admin.Exec(ctx, `DROP ROLE IF EXISTS `+quoteIdentifier(role))
		}
		require.NoError(t, admin.Close(ctx))
	})

	const (
		migrationPassword = "relay-root-migration-password-32-bytes"
		runtimePassword   = "relay-root-runtime-password-32-bytes-xx"
		edgePassword      = "relay-root-edge-password-32-bytes-xxxxxx"
	)
	require.NotEqual(t, migrationPassword, runtimePassword)
	require.NotEqual(t, migrationPassword, edgePassword)
	require.NotEqual(t, runtimePassword, edgePassword)
	_, err = admin.Exec(ctx, `CREATE DATABASE `+quoteIdentifier(databaseName))
	require.NoError(t, err)

	parsed, err := url.Parse(adminDSN)
	require.NoError(t, err)
	require.Equal(t, "verify-full", strings.ToLower(parsed.Query().Get("sslmode")),
		"root provisioning process gate requires a real TLS verify-full PostgreSQL fixture")
	parsed.Path = "/" + databaseName
	parsed.RawPath = ""
	databaseAdminDSN := parsed.String()
	databaseAdmin, err := pgx.Connect(ctx, databaseAdminDSN)
	require.NoError(t, err)
	_, err = databaseAdmin.Exec(ctx, `DO $dedicated_cluster$
DECLARE other_database record;
BEGIN
  FOR other_database IN SELECT datname FROM pg_catalog.pg_database WHERE datname <> current_database()
  LOOP
    EXECUTE format('REVOKE CONNECT, CREATE, TEMPORARY ON DATABASE %I FROM PUBLIC', other_database.datname);
  END LOOP;
END
$dedicated_cluster$`)
	require.NoError(t, err)
	require.NoError(t, databaseAdmin.Close(ctx))

	t.Setenv("APP_ENV", "production")
	t.Setenv("DEPLOYMENT_ENV", "production")
	t.Setenv("NODE_TYPE", "master")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_SECRET_FILES_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED", "true")
	t.Setenv("RELAY_SCHEMA_OWNER_DATABASE_ROLE", ownerRole)
	t.Setenv("RELAY_MIGRATION_DATABASE_ROLE", migrationRole)
	t.Setenv("RELAY_RUNTIME_DATABASE_ROLE", runtimeRole)
	roleAdminDB, err := gorm.Open(postgres.Open(databaseAdminDSN), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	roleAdminSQL, err := roleAdminDB.DB()
	require.NoError(t, err)
	require.NoError(t, model.ProvisionRelayDatabaseRoles(
		roleAdminDB,
		[]byte(migrationPassword),
		[]byte(runtimePassword),
		[]byte(edgePassword),
	))

	roleDSN := func(role string, password string, assumeOwner bool) string {
		copyURL := *parsed
		copyURL.User = url.UserPassword(role, password)
		query := copyURL.Query()
		query.Set("search_path", "public")
		if assumeOwner {
			query.Set("options", "-c role="+ownerRole)
		}
		copyURL.RawQuery = query.Encode()
		return copyURL.String()
	}
	migrationDSN := roleDSN(migrationRole, migrationPassword, true)
	runtimeDSN := roleDSN(runtimeRole, runtimePassword, false)
	secretNamePrefix := "root-provision-" + suffix + "-"
	_, migrationDSNFile := writePlatformRelayRootProvisionProtectedFile(
		t, secretNamePrefix+"migration-dsn", migrationDSN,
	)
	_, runtimeDSNFile := writePlatformRelayRootProvisionProtectedFile(
		t, secretNamePrefix+"runtime-dsn", runtimeDSN,
	)
	if _, present := os.LookupEnv("SQL_DSN"); present {
		require.NoError(t, os.Unsetenv("SQL_DSN"))
	}
	t.Setenv("SQL_DSN_FILE", migrationDSNFile)
	t.Setenv("RELAY_COMPAT_SOURCE_REVISION", platformRelayRootProvisionTestSourceRevision)
	t.Setenv("RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256", platformRelayRootProvisionTestSnapshotSHA256)
	t.Setenv("RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT", fmt.Sprintf("%d", platformRelayRootProvisionTestSnapshotFiles))
	common.SetMainDatabaseType(common.DatabaseTypePostgreSQL)

	migrationDB, err := gorm.Open(postgres.Open(migrationDSN), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	migrationSQL, err := migrationDB.DB()
	require.NoError(t, err)
	require.NoError(t, model.VerifyRelayDatabaseTLS(migrationDB))
	var migrationCurrentUser string
	require.NoError(t, migrationDB.Raw(`SELECT current_user`).Scan(&migrationCurrentUser).Error)
	require.Equal(t, ownerRole, migrationCurrentUser, "TLS inspection must restore the DSN-pinned owner role")
	originalDB, originalLogDB := model.DB, model.LOG_DB
	model.DB, model.LOG_DB = migrationDB, migrationDB
	migrationResult, err := model.RunRelaySchemaMigrations(ctx, "")
	model.DB, model.LOG_DB = originalDB, originalLogDB
	require.NoError(t, err)
	require.Equal(t, "migrated", migrationResult.State)
	require.NoError(t, model.FinalizeRelayDownloadEdgeDatabaseRole(roleAdminDB))
	databaseReleaseIdentity, err := model.InspectRelayDatabaseReleaseIdentity(roleAdminDB)
	require.NoError(t, err)
	require.NoError(t, roleAdminSQL.Close())
	statusBefore, err := model.RequireRelaySchemaCurrent(migrationDB)
	require.NoError(t, err)
	require.NoError(t, migrationSQL.Close())

	evidenceDB, err := pgx.Connect(ctx, databaseAdminDSN)
	require.NoError(t, err)
	defer evidenceDB.Close(ctx)
	var metadataDigestBefore string
	var ledgerCountBefore int
	require.NoError(t, evidenceDB.QueryRow(ctx, `
SELECT md5((SELECT to_jsonb(state)::text FROM relay_schema_state state WHERE id = 1) || '|' ||
           COALESCE((SELECT string_agg(to_jsonb(migration)::text, '|' ORDER BY version)
                       FROM relay_schema_migrations migration), '')),
       (SELECT count(*) FROM relay_schema_migrations)`).Scan(&metadataDigestBefore, &ledgerCountBefore))
	require.NotEmpty(t, metadataDigestBefore)
	require.Positive(t, ledgerCountBefore)

	binaryPath := buildPlatformRelayRootProvisionBinary(t)
	passwordSource, passwordFile := writePlatformRelayRootProvisionProtectedFile(
		t, secretNamePrefix+"root-password", platformRelayRootTestPassword,
	)

	var predictedRootID int
	require.NoError(t, evidenceDB.QueryRow(ctx, `SELECT COALESCE(max(id), 0) + 1 FROM public.users`).Scan(&predictedRootID))
	require.Positive(t, predictedRootID)
	const orphanTokenKey = "root-orphan-token-takeover-probe-000000000000000001"
	require.NoError(t, evidenceDB.QueryRow(ctx, `
INSERT INTO public.tokens (
  user_id, key, status, name, created_time, accessed_time, expired_time,
  remain_quota, unlimited_quota, model_limits_enabled, model_limits,
  allow_ips, used_quota, "group", cross_group_retry, auto_groups
) VALUES ($1, $2, 1, 'root-orphan-takeover-probe', 0, 0, -1, 0, false, false, '', '', 0, '', false, '')
RETURNING id`, predictedRootID, orphanTokenKey).Scan(new(int)))
	orphanCommand := runPlatformRelayRootProvisionProcess(
		t, binaryPath, runtimeDSNFile, "root-admin", passwordFile, ownerRole, migrationRole, runtimeRole,
		databaseReleaseIdentity,
	)
	var orphanStdout bytes.Buffer
	var orphanStderr bytes.Buffer
	orphanCommand.Stdout = &orphanStdout
	orphanCommand.Stderr = &orphanStderr
	orphanErr := orphanCommand.Run()
	require.Error(t, orphanErr, "a pre-seeded future root token must fail the real process gate")
	require.Empty(t, strings.TrimSpace(orphanStdout.String()))
	require.Contains(t, orphanStderr.String(), "Relay offline command failed")
	require.NotContains(t, orphanStderr.String(), orphanTokenKey)
	for _, table := range []string{"users", "setups"} {
		var count int
		require.NoError(t, evidenceDB.QueryRow(ctx, "SELECT count(*) FROM public."+table).Scan(&count), table)
		require.Zero(t, count, "orphan takeover rejection must leave %s empty", table)
	}
	var orphanTokenCount int
	require.NoError(t, evidenceDB.QueryRow(ctx, `SELECT count(*) FROM public.tokens WHERE key = $1`, orphanTokenKey).Scan(&orphanTokenCount))
	require.Equal(t, 1, orphanTokenCount, "the provisioner must not repair or delete hostile pre-existing auth state")
	_, err = evidenceDB.Exec(ctx, `DELETE FROM public.tokens WHERE key = $1`, orphanTokenKey)
	require.NoError(t, err)

	commands := []*exec.Cmd{
		runPlatformRelayRootProvisionProcess(t, binaryPath, runtimeDSNFile, "root-admin", passwordFile, ownerRole, migrationRole, runtimeRole, databaseReleaseIdentity),
		runPlatformRelayRootProvisionProcess(t, binaryPath, runtimeDSNFile, "root-admin", passwordFile, ownerRole, migrationRole, runtimeRole, databaseReleaseIdentity),
	}
	results := make([]platformRelayRootProvisionProcessResult, len(commands))
	var waitGroup sync.WaitGroup
	for index, command := range commands {
		var stdout bytes.Buffer
		var stderr bytes.Buffer
		command.Stdout = &stdout
		command.Stderr = &stderr
		require.NoError(t, command.Start())
		waitGroup.Add(1)
		go func(index int, command *exec.Cmd, stdout *bytes.Buffer, stderr *bytes.Buffer) {
			defer waitGroup.Done()
			waitErr := command.Wait()
			results[index] = platformRelayRootProvisionProcessResult{
				stdout: stdout.String(),
				stderr: stderr.String(),
				err:    waitErr,
			}
		}(index, command, &stdout, &stderr)
	}
	waitGroup.Wait()
	combinedOutput := ""
	for _, result := range results {
		require.NoError(t, result.err, result.stderr)
		var receipt platformRelayRootProvisionOutput
		decoder := json.NewDecoder(strings.NewReader(result.stdout))
		require.NoError(t, decoder.Decode(&receipt), result.stderr)
		require.ErrorIs(t, decoder.Decode(&struct{}{}), io.EOF, "stdout must contain exactly one JSON receipt")
		require.Equal(t, "relay_root_provision", receipt.Kind)
		require.Equal(t, "root-admin", receipt.Username)
		combinedOutput += result.stdout
		require.NotContains(t, result.stderr, platformRelayRootTestPassword)
	}
	require.Equal(t, 1, strings.Count(combinedOutput, `"state":"created"`), combinedOutput)
	require.Equal(t, 1, strings.Count(combinedOutput, `"state":"unchanged"`), combinedOutput)
	require.NotContains(t, combinedOutput, platformRelayRootTestPassword)

	database, err := pgx.Connect(ctx, runtimeDSN)
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, database.Close(ctx)) })
	var userCount int
	var rootID int
	var username string
	var passwordHash string
	var role int
	var status int
	var accessToken *string
	var createdAt int64
	var authVersion int64
	var lastLoginAt int64
	require.NoError(t, database.QueryRow(ctx, `
		SELECT count(*) OVER (), id, username, password, role, status,
		       access_token, created_at, auth_version, last_login_at
		FROM users
	`).Scan(
		&userCount, &rootID, &username, &passwordHash, &role, &status,
		&accessToken, &createdAt, &authVersion, &lastLoginAt,
	))
	require.Equal(t, 1, userCount)
	require.Equal(t, "root-admin", username)
	require.Equal(t, common.RoleRootUser, role)
	require.Equal(t, common.UserStatusEnabled, status)
	require.Nil(t, accessToken)
	require.Equal(t, int64(1), authVersion)
	require.Zero(t, lastLoginAt)
	require.True(t, common.ValidatePasswordAndHash(platformRelayRootTestPassword, passwordHash))
	var canonicalRootShape bool
	require.NoError(t, database.QueryRow(ctx, `
SELECT display_name = 'Root User'
   AND role = $1 AND status = $2
   AND email = '' AND github_id = '' AND discord_id = '' AND oidc_id = ''
   AND wechat_id = '' AND telegram_id = '' AND linux_do_id = ''
   AND access_token IS NULL
   AND quota = 100000000 AND used_quota = 0 AND request_count = 0
   AND "group" = 'default'
   AND aff_code = '' AND aff_count = 0 AND aff_quota = 0 AND aff_history = 0
   AND inviter_id = 0 AND deleted_at IS NULL
   AND setting = '' AND remark = '' AND stripe_customer = ''
   AND last_login_at = 0 AND auth_version = 1
  FROM public.users
 WHERE id = $3`, common.RoleRootUser, common.UserStatusEnabled, rootID).Scan(&canonicalRootShape))
	require.True(t, canonicalRootShape, "the committed root must match the exact canonical bootstrap shape")
	var setupCount int
	var setupID int
	var setupVersion string
	var initializedAt int64
	require.NoError(t, database.QueryRow(ctx, `
		SELECT count(*) OVER (), id, version, initialized_at FROM setups
	`).Scan(&setupCount, &setupID, &setupVersion, &initializedAt))
	require.Equal(t, 1, setupCount)
	require.Equal(t, common.Version, setupVersion)
	require.Positive(t, initializedAt)
	for _, table := range []string{
		"tokens", "user_sessions", "auth_flows", "external_identity_claims",
		"passkey_credentials", "user_oauth_bindings", "two_fas", "two_fa_backup_codes",
	} {
		var count int
		require.NoError(t, database.QueryRow(ctx, "SELECT count(*) FROM "+table).Scan(&count), table)
		require.Zero(t, count, table)
	}

	replay := runPlatformRelayRootProvisionProcess(t, binaryPath, runtimeDSNFile, "root-admin", passwordFile, ownerRole, migrationRole, runtimeRole, databaseReleaseIdentity)
	replayOutput, err := replay.CombinedOutput()
	require.NoError(t, err, string(replayOutput))
	require.Contains(t, string(replayOutput), `"state":"unchanged"`)
	var replayedHash string
	var replayedCreatedAt int64
	var replayedSetupID int
	var replayedInitializedAt int64
	require.NoError(t, database.QueryRow(ctx, `SELECT password, created_at FROM users WHERE id = $1`, rootID).Scan(
		&replayedHash, &replayedCreatedAt,
	))
	require.NoError(t, database.QueryRow(ctx, `SELECT id, initialized_at FROM setups`).Scan(
		&replayedSetupID, &replayedInitializedAt,
	))
	require.Equal(t, passwordHash, replayedHash)
	require.Equal(t, createdAt, replayedCreatedAt)
	require.Equal(t, setupID, replayedSetupID)
	require.Equal(t, initializedAt, replayedInitializedAt)

	differentPassword := platformRelayRootTestPassword + "-different"
	require.LessOrEqual(t, len(differentPassword), 72)
	replacePlatformRelayRootProvisionProtectedFile(t, passwordSource, differentPassword)
	conflict := runPlatformRelayRootProvisionProcess(t, binaryPath, runtimeDSNFile, "root-admin", passwordFile, ownerRole, migrationRole, runtimeRole, databaseReleaseIdentity)
	conflictOutput, conflictErr := conflict.CombinedOutput()
	require.Error(t, conflictErr)
	require.Contains(t, string(conflictOutput), "Relay offline command failed")
	require.NotContains(t, string(conflictOutput), differentPassword)
	var afterConflictHash string
	var afterConflictCreatedAt int64
	require.NoError(t, database.QueryRow(ctx, `SELECT password, created_at FROM users WHERE id = $1`, rootID).Scan(
		&afterConflictHash, &afterConflictCreatedAt,
	))
	require.Equal(t, passwordHash, afterConflictHash)
	require.Equal(t, createdAt, afterConflictCreatedAt)

	var metadataDigestAfter string
	var ledgerCountAfter int
	require.NoError(t, evidenceDB.QueryRow(ctx, `
SELECT md5((SELECT to_jsonb(state)::text FROM relay_schema_state state WHERE id = 1) || '|' ||
           COALESCE((SELECT string_agg(to_jsonb(migration)::text, '|' ORDER BY version)
                       FROM relay_schema_migrations migration), '')),
       (SELECT count(*) FROM relay_schema_migrations)`).Scan(&metadataDigestAfter, &ledgerCountAfter))
	require.Equal(t, metadataDigestBefore, metadataDigestAfter, "root DML must not rewrite schema state or ledger")
	require.Equal(t, ledgerCountBefore, ledgerCountAfter)
	runtimeGORM, err := gorm.Open(postgres.Open(runtimeDSN), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	runtimeSQL, err := runtimeGORM.DB()
	require.NoError(t, err)
	defer runtimeSQL.Close()
	statusAfter, err := model.RequireRelaySchemaCurrent(runtimeGORM)
	require.NoError(t, err)
	require.Equal(t, statusBefore.CatalogSHA256, statusAfter.CatalogSHA256)
	require.NoError(t, model.VerifyRelayRuntimeDatabaseRole(runtimeGORM))
	require.Error(t, runtimeGORM.Exec(`CREATE TABLE public.root_provision_must_not_create_ddl (id bigint)`).Error)
}
