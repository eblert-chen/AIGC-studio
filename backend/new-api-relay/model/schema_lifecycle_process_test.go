package model

import (
	"bufio"
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

const (
	relaySchemaLifecycleSourceRevision = "1212121212121212121212121212121212121212"
	relaySchemaLifecycleSnapshotSHA256 = "sha256:3434343434343434343434343434343434343434343434343434343434343434"
	relaySchemaLifecycleSnapshotFiles  = 321
	relaySchemaLifecycleUpstream       = "0ab02020603d22e5613bc4cf46bfab06f8567769"
	relaySchemaLifecycleImageDigest    = "sha256:7878787878787878787878787878787878787878787878787878787878787878"
	relaySchemaLifecyclePlatformImage  = "registry.example.test/ai-video/platform@sha256:8989898989898989898989898989898989898989898989898989898989898989"
	relaySchemaLifecyclePlatformSource = "9090909090909090909090909090909090909090"
	relaySchemaLifecyclePlatformSHA256 = "sha256:9191919191919191919191919191919191919191919191919191919191919191"
	relaySchemaLifecycleTenantID       = "4f88d0a4-d950-4b42-936e-6adf03aaf62d"
	relaySchemaLifecycleClientID       = "lifecycle-platform-api"
)

type relaySchemaLifecycleProcessFixture struct {
	binaryPath     string
	environment    []string
	secretCanaries []string
}

var relaySchemaLifecycleDiagnosticURL = regexp.MustCompile(`(?i)\b(?:postgres(?:ql)?|rediss?)://[^\s]+`)

func relaySchemaTestSecret(label string) string {
	first := sha256.Sum256([]byte("relay-schema-lifecycle\x00" + label))
	second := sha256.Sum256([]byte("relay-schema-lifecycle-extra\x00" + label))
	return hex.EncodeToString(first[:]) + hex.EncodeToString(second[:])
}

func relaySchemaTestRouteAcceptanceTrustStore(t *testing.T) (string, string) {
	t.Helper()
	// Match the deterministic public-key fixture used by the route-acceptance
	// contract tests. Only the public key enters the protected child runtime.
	seed := bytes.Repeat([]byte{0x42}, ed25519.SeedSize)
	publicKey := ed25519.NewKeyFromSeed(seed).Public().(ed25519.PublicKey)
	clear(seed)
	encodedKeys := map[string]string{
		"release-test-2026": base64.StdEncoding.EncodeToString(publicKey),
	}
	canonicalKeys, err := json.Marshal(encodedKeys)
	require.NoError(t, err)
	trustDigest := sha256.Sum256(canonicalKeys)
	return string(canonicalKeys), fmt.Sprintf("sha256:%x", trustDigest)
}

func relaySchemaTestMinimalProcessEnvironment(temporaryRoot string) []string {
	allowed := map[string]struct{}{
		"PATH": {}, "SystemRoot": {}, "SYSTEMROOT": {}, "WINDIR": {},
		"TEMP": {}, "TMP": {}, "TZ": {},
	}
	environment := make([]string, 0, len(allowed)+3)
	for _, entry := range os.Environ() {
		name, _, ok := strings.Cut(entry, "=")
		if !ok {
			continue
		}
		if _, keep := allowed[name]; keep {
			environment = append(environment, entry)
		}
	}
	environment = append(environment, "HOME="+temporaryRoot, "TMPDIR="+temporaryRoot)
	return environment
}

func relaySchemaTestProtectedLifecycleBaseEnvironment(
	temporaryRoot, runtimeDSNFile, databaseCAFile, routeAcceptanceTrustSHA256 string,
) []string {
	return append(relaySchemaTestMinimalProcessEnvironment(temporaryRoot),
		"APP_ENV=staging",
		"DEPLOYMENT_ENV=staging",
		"ENVIRONMENT=staging",
		"NODE_TYPE=master",
		"SQL_DSN_FILE="+runtimeDSNFile,
		"RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED=true",
		"RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=true",
		"RELAY_DATABASE_SECRET_FILES_REQUIRED=true",
		"RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED=true",
		"RELAY_DATABASE_CA_FILE="+databaseCAFile,
		"RELAY_SCHEMA_OWNER_DATABASE_ROLE="+relaySchemaTestOwnerRole,
		"RELAY_MIGRATION_DATABASE_ROLE="+relaySchemaTestMigratorRole,
		"RELAY_RUNTIME_DATABASE_ROLE="+relaySchemaTestRuntimeRole,
		"RELAY_COMPAT_SOURCE_REVISION="+relaySchemaLifecycleSourceRevision,
		"RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256="+relaySchemaLifecycleSnapshotSHA256,
		fmt.Sprintf("RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT=%d", relaySchemaLifecycleSnapshotFiles),
		"RELAY_COMPAT_UPSTREAM_REVISION="+relaySchemaLifecycleUpstream,
		"RELAY_COMPAT_ROUTE_ACCEPTANCE_TRUST_KEYS_SHA256="+routeAcceptanceTrustSHA256,
		"PLATFORM_IMAGE="+relaySchemaLifecyclePlatformImage,
		"PLATFORM_SOURCE_REVISION="+relaySchemaLifecyclePlatformSource,
		"PLATFORM_SOURCE_SNAPSHOT_SHA256="+relaySchemaLifecyclePlatformSHA256,
		"PLATFORM_DATABASE_NAME=platform",
		"PLATFORM_PUBLIC_BASE_URL=https://platform.test.invalid",
		"NEW_API_RELAY_PUBLIC_BASE_URL=https://relay.test.invalid",
		"DOWNLOAD_GATEWAY_PUBLIC_BASE_URL=https://downloads.test.invalid",
		"PLATFORM_NEW_API_RELAY_CONTRACT_REVISION=generations.v1",
		"RELAY_PROVIDER_ALERT_WEBHOOK_URL=https://platform.test.invalid/internal/relay/provider-alerts",
		"RELAY_PLATFORM_CHANNEL_COST_URL=https://platform.test.invalid/internal/channel-costs",
		"RELAY_PLATFORM_TASK_STAGE_URL=https://platform.test.invalid/internal/relay/task-stages",
		"RELAY_PLATFORM_OPERATIONS_SNAPSHOT_URL=https://platform.test.invalid/internal/relay/operations-snapshots",
		"RELAY_DOWNLOAD_EDGE_PUBLIC_BASE_URL=https://downloads.test.invalid",
		"RELAY_DOWNLOAD_EDGE_PLATFORM_COMPLETION_URL=https://platform.test.invalid/internal/artifact-download-completions/edge-gateway",
		"DOWNLOAD_GATEWAY_REGISTRATION_URL=https://downloads.test.invalid/internal/v1/download-tickets",
	)
}

func relaySchemaTestRunLifecycleOfflineCommand(
	t *testing.T,
	binaryPath string,
	temporaryRoot string,
	baseEnvironment []string,
	argument string,
	extraEnvironment ...string,
) map[string]any {
	t.Helper()
	command := exec.Command(binaryPath, argument)
	command.Dir = temporaryRoot
	command.Env = append(append([]string(nil), baseEnvironment...), extraEnvironment...)
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	command.Stdout = &stdout
	command.Stderr = &stderr
	require.NoError(t, command.Run(), "%s failed: %s", argument, stderr.String())
	decoder := json.NewDecoder(bytes.NewReader(stdout.Bytes()))
	var receipt map[string]any
	require.NoError(t, decoder.Decode(&receipt), "%s did not emit one JSON receipt", argument)
	_, err := decoder.Token()
	require.ErrorIs(t, err, io.EOF, "%s emitted trailing stdout", argument)
	return receipt
}

func relaySchemaTestPrepareProtectedLifecycleProcess(
	t *testing.T,
	adminDSN string,
	migrationDSN string,
	runtimeDSN string,
	expectedRootProvisionState string,
	expectedPrincipalProvisionState string,
) relaySchemaLifecycleProcessFixture {
	t.Helper()
	databaseCAPath := ""
	for label, dsn := range map[string]string{
		"role-admin": adminDSN,
		"migration":  migrationDSN,
		"runtime":    runtimeDSN,
	} {
		parsed, parseErr := url.Parse(dsn)
		require.NoError(t, parseErr, "%s PostgreSQL DSN", label)
		require.Equal(t, "verify-full", parsed.Query().Get("sslmode"), "%s PostgreSQL DSN", label)
		candidate := parsed.Query().Get("sslrootcert")
		require.NotEmpty(t, candidate, "%s PostgreSQL DSN", label)
		require.True(t, filepath.IsAbs(candidate), "%s PostgreSQL CA path", label)
		if databaseCAPath == "" {
			databaseCAPath = candidate
		} else {
			require.Equal(t, databaseCAPath, candidate, "all protected PostgreSQL DSNs must share one CA source")
		}
	}
	databaseCARaw, err := os.ReadFile(databaseCAPath)
	require.NoError(t, err)
	_, relayDatabaseCAFile := relaySchemaTestWriteProtectedFile(
		t, "lifecycle-relay-database-ca.pem", string(databaseCARaw),
	)
	_, platformDatabaseCAFile := relaySchemaTestWriteProtectedFile(
		t, "lifecycle-platform-database-ca.pem", string(databaseCARaw),
	)
	clear(databaseCARaw)
	rewriteCAPath := func(label, dsn, caPath string) string {
		parsed, parseErr := url.Parse(dsn)
		require.NoError(t, parseErr, "%s PostgreSQL DSN", label)
		query := parsed.Query()
		query.Set("sslrootcert", caPath)
		parsed.RawQuery = query.Encode()
		return parsed.String()
	}
	adminDSN = rewriteCAPath("role-admin", adminDSN, relayDatabaseCAFile)
	migrationDSN = rewriteCAPath("migration", migrationDSN, relayDatabaseCAFile)
	runtimeDSN = rewriteCAPath("runtime", runtimeDSN, relayDatabaseCAFile)
	_, runtimeDSNFile := relaySchemaTestWriteProtectedFile(t, "lifecycle-runtime-sql-dsn", runtimeDSN)
	routeAcceptancePublicKeysJSON, routeAcceptanceTrustSHA256 := relaySchemaTestRouteAcceptanceTrustStore(t)
	moduleRoot, err := filepath.Abs("..")
	require.NoError(t, err)
	binaryName := "new-api-lifecycle-test"
	if runtime.GOOS == "windows" {
		binaryName += ".exe"
	}
	binaryPath := filepath.Join(t.TempDir(), binaryName)
	linkerFlags := strings.Join([]string{
		"-X", "github.com/QuantumNous/new-api/service.platformRelayCompiledUpstreamRevision=" + relaySchemaLifecycleUpstream,
		"-X", "github.com/QuantumNous/new-api/service.platformRelayCompiledSourceRevision=" + relaySchemaLifecycleSourceRevision,
		"-X", "github.com/QuantumNous/new-api/service.platformRelayCompiledSnapshotSHA256=" + relaySchemaLifecycleSnapshotSHA256,
		"-X", fmt.Sprintf("github.com/QuantumNous/new-api/service.platformRelayCompiledSnapshotFileCount=%d", relaySchemaLifecycleSnapshotFiles),
		"-X", "github.com/QuantumNous/new-api/service.platformRelayCompiledRouteAcceptanceKeysSHA256=" + routeAcceptanceTrustSHA256,
	}, " ")
	build := exec.Command("go", "build", "-ldflags", linkerFlags, "-o", binaryPath, ".")
	build.Dir = moduleRoot
	buildOutput, err := build.CombinedOutput()
	require.NoError(t, err, "build lifecycle child: %s", string(buildOutput))

	temporaryRoot := t.TempDir()
	baseEnvironment := relaySchemaTestProtectedLifecycleBaseEnvironment(
		temporaryRoot, runtimeDSNFile, relayDatabaseCAFile, routeAcceptanceTrustSHA256,
	)
	clientIDs := []string{
		"lifecycle-platform-api", "lifecycle-platform-dispatcher",
		"lifecycle-platform-relay-sync", "lifecycle-platform-timeout",
	}
	upstreamTokens := make(map[string]string, len(clientIDs))
	clientAPIKeys := make(map[string]string, len(clientIDs))
	principals := make([]map[string]any, 0, len(clientIDs))
	for _, clientID := range clientIDs {
		upstreamTokens[clientID] = "sk-" + relaySchemaTestSecret("upstream-token-" + clientID)[:48]
		clientAPIKeys[clientID] = relaySchemaTestSecret("client-api-key-" + clientID)
		principals = append(principals, map[string]any{
			"client_id": clientID, "tenant_id": relaySchemaLifecycleTenantID,
			"upstream_token": upstreamTokens[clientID],
		})
	}
	principalDocument := map[string]any{
		"kind":           "relay_service_principals",
		"schema_version": 1,
		"principals":     principals,
	}
	principalRaw, err := json.Marshal(principalDocument)
	require.NoError(t, err)
	_, principalFile := relaySchemaTestWriteProtectedFile(t, "lifecycle-service-principals.json", string(principalRaw))
	clear(principalRaw)
	redisPassword := relaySchemaTestSecret("redis-password")
	redisDSN, redisCASourceFile := relaySchemaTestStartTLSRedis(t, redisPassword)
	redisCARaw, err := os.ReadFile(redisCASourceFile)
	require.NoError(t, err)
	_, redisCAFile := relaySchemaTestWriteProtectedFile(t, "lifecycle-redis-tls-ca.pem", string(redisCARaw))
	clear(redisCARaw)
	obsEndpoint, obsBucket := relaySchemaTestStartTLSOBS(t)
	operationsToken := relaySchemaTestSecret("operations-token")
	operationsDigest := sha256.Sum256([]byte(operationsToken))
	callbackSecret := relaySchemaTestSecret("platform-new-api-callback")
	clients := make([]map[string]any, 0, len(clientIDs))
	for _, clientID := range clientIDs {
		client := map[string]any{
			"client_id": clientID, "tenant_id": relaySchemaLifecycleTenantID,
			"api_key": clientAPIKeys[clientID],
		}
		if clientID == "lifecycle-platform-dispatcher" {
			client["callback_url"] = "https://platform.test.invalid/internal/relay-callbacks/new-api-v1"
			client["callback_signing_secret"] = callbackSecret
		}
		clients = append(clients, client)
	}
	runtimeDocument := map[string]any{
		"kind":           "relay_api_runtime_secrets",
		"schema_version": 1,
		"redis_dsn":      redisDSN,
		"application": map[string]any{
			"session_secret": relaySchemaTestSecret("session-secret"),
			"crypto_secret":  relaySchemaTestSecret("crypto-secret"),
		},
		"clients": clients,
		"operations_credentials": []map[string]any{{
			"tenant_id": relaySchemaLifecycleTenantID, "token_sha256": hex.EncodeToString(operationsDigest[:]),
		}},
		"reconciliation_approval_keys": []map[string]any{{
			"tenant_id": relaySchemaLifecycleTenantID, "key_id": "lifecycle-approval",
			"secret": relaySchemaTestSecret("approval-secret"),
		}},
		"internal_admission_token": relaySchemaTestSecret("internal-admission"),
		"artifact_signing_secret":  relaySchemaTestSecret("artifact-signing"),
		"huawei_obs": map[string]any{
			"access_key_id":     relaySchemaTestSecret("obs-access-key")[:24],
			"secret_access_key": relaySchemaTestSecret("obs-secret-key"),
		},
		"provider_alert_signing_secret":   relaySchemaTestSecret("provider-alert"),
		"platform_internal_service_token": relaySchemaTestSecret("platform-internal"),
		"channel_cost_signing_secret":     relaySchemaTestSecret("channel-cost"),
		"telemetry_signing_secret":        relaySchemaTestSecret("telemetry"),
	}
	runtimeRaw, err := json.Marshal(runtimeDocument)
	require.NoError(t, err)
	_, runtimeSecretsFile := relaySchemaTestWriteProtectedFile(t, "lifecycle-api-runtime-secrets.json", string(runtimeRaw))
	clear(runtimeRaw)
	providerKEK := sha256.Sum256([]byte("relay-schema-lifecycle-provider-kek"))
	providerKEKBase64 := base64.StdEncoding.EncodeToString(providerKEK[:])
	providerKeyringRaw, err := json.Marshal(map[string]any{
		"schema_version": 1,
		"active_key_id":  "lifecycle-v1",
		"keys": map[string]string{
			"lifecycle-v1": providerKEKBase64,
			// The same-database legacy bridge fixture has active task/channel
			// references encrypted by the immutable v1 test key. Retain that
			// historical test-only KEK while lifecycle-v1 remains the active key,
			// matching the production rotation contract without rewriting data.
			"test-v1": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
		},
	})
	require.NoError(t, err)
	_, providerKeyringFile := relaySchemaTestWriteProtectedFile(t, "lifecycle-provider-keyring.json", string(providerKeyringRaw))
	clear(providerKeyringRaw)

	edgeKey := func(label string) string {
		digest := sha256.Sum256([]byte("relay-schema-lifecycle-edge-key\x00" + label))
		return base64.StdEncoding.EncodeToString(digest[:])
	}
	edgeDocument := map[string]any{
		"kind":                           "relay_download_edge_runtime_secrets",
		"schema_version":                 1,
		"registration_token":             relaySchemaTestSecret("edge-registration-token"),
		"registration_signing_secret":    relaySchemaTestSecret("edge-registration-signing"),
		"ticket_token_key_base64":        edgeKey("ticket"),
		"source_encryption_key_base64":   edgeKey("source"),
		"platform_edge_completion_token": relaySchemaTestSecret("edge-platform-completion"),
		"completion_signing_secret":      relaySchemaTestSecret("edge-completion-signing"),
		"proof_signing_seed_base64":      edgeKey("proof"),
		"proof_read_token":               relaySchemaTestSecret("edge-proof-read"),
	}
	edgeRaw, err := json.Marshal(edgeDocument)
	require.NoError(t, err)
	_, edgeRuntimeSecretsFile := relaySchemaTestWriteProtectedFile(t, "lifecycle-edge-runtime-secrets.json", string(edgeRaw))
	clear(edgeRaw)

	platformRoles := []struct {
		role, databaseUser, environment, passwordEnvironment string
	}{
		{"migration", "platform_migration", "PLATFORM_MIGRATION_RUNTIME_SECRETS_FILE", "PLATFORM_MIGRATION_DATABASE_PASSWORD_FILE"},
		{"platform-api", "platform_api", "PLATFORM_API_RUNTIME_SECRETS_FILE", "PLATFORM_API_DATABASE_PASSWORD_FILE"},
		{"dispatcher", "platform_dispatcher", "PLATFORM_DISPATCHER_RUNTIME_SECRETS_FILE", "PLATFORM_DISPATCHER_DATABASE_PASSWORD_FILE"},
		{"relay-sync", "platform_relay_sync", "PLATFORM_RELAY_SYNC_RUNTIME_SECRETS_FILE", "PLATFORM_RELAY_SYNC_DATABASE_PASSWORD_FILE"},
		{"timeout-worker", "platform_timeout_worker", "PLATFORM_TIMEOUT_WORKER_RUNTIME_SECRETS_FILE", "PLATFORM_TIMEOUT_WORKER_DATABASE_PASSWORD_FILE"},
		{"publishing-worker", "platform_publishing_worker", "PLATFORM_PUBLISHING_WORKER_RUNTIME_SECRETS_FILE", "PLATFORM_PUBLISHING_WORKER_DATABASE_PASSWORD_FILE"},
		{"download-gateway-registration-worker", "platform_download_gateway_worker", "PLATFORM_DOWNLOAD_GATEWAY_WORKER_RUNTIME_SECRETS_FILE", "PLATFORM_DOWNLOAD_GATEWAY_WORKER_DATABASE_PASSWORD_FILE"},
	}
	platformClientID := map[string]string{
		"platform-api": "lifecycle-platform-api", "dispatcher": "lifecycle-platform-dispatcher",
		"relay-sync": "lifecycle-platform-relay-sync", "timeout-worker": "lifecycle-platform-timeout",
	}
	platformAttemptDigest := sha256.Sum256([]byte("relay-schema-platform-download-attempt-key"))
	platformAttemptKey := base64.StdEncoding.EncodeToString(platformAttemptDigest[:])
	platformAPIOBSAccessKey := relaySchemaTestSecret("platform-api-obs-access-key")[:24]
	platformAPIOBSSecret := relaySchemaTestSecret("platform-api-obs-secret")
	platformAPIOBSToken := relaySchemaTestSecret("platform-api-obs-token")
	platformDispatcherOBSAccessKey := relaySchemaTestSecret("platform-dispatcher-obs-access-key")[:24]
	platformDispatcherOBSSecret := relaySchemaTestSecret("platform-dispatcher-obs-secret")
	platformDispatcherOBSToken := relaySchemaTestSecret("platform-dispatcher-obs-token")
	platformPublishingAdapterCredentials := map[string]any{
		"publishers.tiktok:create": map[string]any{
			"CLIENT_SECRET": relaySchemaTestSecret("platform-publisher-shared-client"),
		},
	}
	platformDatabaseURL := func(role, user string) string {
		query := url.Values{"sslmode": {"verify-full"}, "sslrootcert": {platformDatabaseCAFile}}
		return "postgresql+psycopg://" + user + ":" + relaySchemaTestSecret("platform-database-" + role)[:64] +
			"@postgres.platform.test:5432/platform?" + query.Encode()
	}
	parseDSNPassword := func(name, value string) string {
		parsed, parseErr := url.Parse(value)
		require.NoError(t, parseErr, "%s DSN", name)
		require.NotNil(t, parsed.User, "%s DSN user", name)
		password, present := parsed.User.Password()
		require.True(t, present, "%s DSN password", name)
		return password
	}
	platformRelaySecrets := func(role, databaseUser string) map[string]any {
		return map[string]any{
			"database_url": platformDatabaseURL(role, databaseUser),
			"relay_backends": map[string]any{
				"new-api-v1": map[string]any{
					"base_url": "https://relay.test.invalid", "client_id": platformClientID[role],
					"api_key": clientAPIKeys[platformClientID[role]], "contract_revision": "generations.v1",
				},
			},
		}
	}
	platformSecretFiles := make(map[string]string, len(platformRoles))
	platformSecretCanaries := make([]string, 0, 32)
	for _, item := range platformRoles {
		var secrets map[string]any
		switch item.role {
		case "migration":
			secrets = map[string]any{"database_url": platformDatabaseURL(item.role, item.databaseUser)}
		case "relay-sync", "timeout-worker":
			secrets = platformRelaySecrets(item.role, item.databaseUser)
		case "dispatcher":
			secrets = platformRelaySecrets(item.role, item.databaseUser)
			secrets["huawei_obs_access_key_id"] = platformDispatcherOBSAccessKey
			secrets["huawei_obs_secret_access_key"] = platformDispatcherOBSSecret
			secrets["huawei_obs_security_token"] = platformDispatcherOBSToken
		case "publishing-worker":
			secrets = map[string]any{
				"database_url": platformDatabaseURL(item.role, item.databaseUser),
				"publishing_plugin_credentials": map[string]any{
					"adapters": platformPublishingAdapterCredentials,
					"media_resolvers": map[string]any{"publishers.obs:create_resolver": map[string]any{
						"READ_TOKEN": relaySchemaTestSecret("platform-publisher-read"),
					}},
				},
			}
		case "download-gateway-registration-worker":
			secrets = map[string]any{
				"database_url":                                   platformDatabaseURL(item.role, item.databaseUser),
				"download_gateway_service_token":                 edgeDocument["registration_token"],
				"download_gateway_registration_signing_secret":   edgeDocument["registration_signing_secret"],
				"download_gateway_attempt_encryption_key_base64": platformAttemptKey,
			}
		case "platform-api":
			secrets = platformRelaySecrets(item.role, item.databaseUser)
			for name, value := range map[string]any{
				"relay_tenant_id":                                   relaySchemaLifecycleTenantID,
				"relay_operations_token":                            operationsToken,
				"relay_reconciliation_approval_key_id":              "lifecycle-approval",
				"relay_reconciliation_approval_secret":              relaySchemaTestSecret("approval-secret"),
				"relay_callback_signing_secrets":                    map[string]any{"new-api-v1": callbackSecret},
				"internal_service_token":                            runtimeDocument["platform_internal_service_token"],
				"download_edge_completion_service_token":            edgeDocument["platform_edge_completion_token"],
				"channel_cost_signing_secret":                       runtimeDocument["channel_cost_signing_secret"],
				"relay_telemetry_signing_secret":                    runtimeDocument["telemetry_signing_secret"],
				"provider_alert_signing_secret":                     runtimeDocument["provider_alert_signing_secret"],
				"provider_alert_forward_signing_secret":             relaySchemaTestSecret("platform-alert-forward"),
				"download_completion_edge_gateway_signing_secret":   edgeDocument["completion_signing_secret"],
				"download_completion_obs_access_log_signing_secret": relaySchemaTestSecret("platform-obs-completion"),
				"download_gateway_service_token":                    edgeDocument["registration_token"],
				"download_gateway_registration_signing_secret":      edgeDocument["registration_signing_secret"],
				"download_gateway_attempt_encryption_key_base64":    platformAttemptKey,
				"jwt_signing_secret":                                relaySchemaTestSecret("platform-jwt"),
				"huawei_obs_access_key_id":                          platformAPIOBSAccessKey,
				"huawei_obs_secret_access_key":                      platformAPIOBSSecret,
				"huawei_obs_security_token":                         platformAPIOBSToken,
				"publishing_plugin_credentials":                     map[string]any{"adapters": platformPublishingAdapterCredentials},
			} {
				secrets[name] = value
			}
		}
		document := map[string]any{
			"kind": "platform_process_runtime_secrets", "schema_version": 1,
			"process_role": item.role, "secrets": secrets,
		}
		raw, marshalErr := json.Marshal(document)
		require.NoError(t, marshalErr)
		_, path := relaySchemaTestWriteProtectedFile(t, "lifecycle-platform-"+item.role+"-runtime-secrets.json", string(raw))
		clear(raw)
		platformSecretFiles[item.environment] = path
		platformDatabasePassword := parseDSNPassword("platform-"+item.role, secrets["database_url"].(string))
		_, passwordPath := relaySchemaTestWriteProtectedFile(
			t, "lifecycle-platform-"+item.role+"-database-password", platformDatabasePassword,
		)
		platformSecretFiles[item.passwordEnvironment] = passwordPath
		platformSecretCanaries = append(platformSecretCanaries, secrets["database_url"].(string))
	}
	platformRoleAdminPassword := relaySchemaTestSecret("platform-role-admin-password")[:64]
	_, platformRoleAdminDSNFile := relaySchemaTestWriteProtectedFile(
		t,
		"lifecycle-platform-role-admin-dsn",
		"postgresql+psycopg://platform_role_admin:"+platformRoleAdminPassword+
			"@postgres.platform.test:5432/postgres?"+url.Values{
			"sslmode": {"verify-full"}, "sslrootcert": {platformDatabaseCAFile},
		}.Encode(),
	)
	platformSecretFiles["PLATFORM_DATABASE_ROLE_ADMIN_DSN_FILE"] = platformRoleAdminDSNFile

	adminPassword := parseDSNPassword("admin", adminDSN)
	migrationPassword := parseDSNPassword("migration", migrationDSN)
	runtimePassword := parseDSNPassword("runtime", runtimeDSN)
	adminParsed, err := url.Parse(adminDSN)
	require.NoError(t, err)
	adminQuery := adminParsed.Query()
	adminQuery.Set("search_path", "public")
	adminParsed.RawQuery = adminQuery.Encode()
	protectedAdminDSN := adminParsed.String()
	edgeParsed, err := url.Parse(runtimeDSN)
	require.NoError(t, err)
	edgeParsed.User = url.UserPassword(relaySchemaTestEdgeRole, relaySchemaTestEdgePassword)
	edgeDSN := edgeParsed.String()
	_, adminDSNFile := relaySchemaTestWriteProtectedFile(t, "lifecycle-role-admin-sql-dsn", protectedAdminDSN)
	_, migrationDSNFile := relaySchemaTestWriteProtectedFile(t, "lifecycle-migration-sql-dsn", migrationDSN)
	_, edgeDSNFile := relaySchemaTestWriteProtectedFile(t, "lifecycle-edge-sql-dsn", edgeDSN)
	_, migrationPasswordFile := relaySchemaTestWriteProtectedFile(t, "lifecycle-migration-password", migrationPassword)
	_, runtimePasswordFile := relaySchemaTestWriteProtectedFile(t, "lifecycle-runtime-password", runtimePassword)
	_, edgePasswordFile := relaySchemaTestWriteProtectedFile(t, "lifecycle-edge-password", relaySchemaTestEdgePassword)
	require.NotEqual(t, adminPassword, migrationPassword)

	receiptRoot := t.TempDir()
	commitDirectory := filepath.Join(receiptRoot, "commit")
	require.NoError(t, os.Mkdir(commitDirectory, 0o700))
	receiptDirectories := map[string]string{}
	for _, consumer := range []string{
		"pre", "migrate", "post", "principal", "api", "edge",
		"platform-migration", "platform-api", "platform-dispatcher", "platform-relay-sync",
		"platform-timeout-worker", "platform-publishing-worker", "platform-download-gateway-registration-worker",
		"platform-db-role-pre",
	} {
		directory := filepath.Join(receiptRoot, consumer)
		require.NoError(t, os.Mkdir(directory, 0o700))
		receiptDirectories[consumer] = directory
	}
	proofDirectoryName := "lifecycle-root-proof-" + strings.NewReplacer("/", "-", "\\", "-").Replace(t.Name())
	proofSourceRoot := receiptRoot
	proofReadOnlyRoot := receiptRoot
	if runtime.GOOS == "linux" {
		proofSourceRoot = strings.TrimSpace(os.Getenv("TEST_PROTECTED_SECRET_SOURCE_DIR"))
		proofReadOnlyRoot = strings.TrimSpace(os.Getenv("TEST_PROTECTED_SECRET_READONLY_DIR"))
		require.NotEmpty(t, proofSourceRoot)
		require.NotEmpty(t, proofReadOnlyRoot)
	}
	proofSourceDirectory := filepath.Join(proofSourceRoot, proofDirectoryName)
	proofReadOnlyDirectory := filepath.Join(proofReadOnlyRoot, proofDirectoryName)
	require.NoError(t, os.Mkdir(proofSourceDirectory, 0o700))
	proofLockPath := filepath.Join(proofSourceDirectory, ".proof.lock")
	require.NoError(t, os.WriteFile(proofLockPath, nil, 0o600))
	proofSourceInfo, err := os.Stat(proofSourceDirectory)
	require.NoError(t, err)
	proofReadOnlyInfo, err := os.Stat(proofReadOnlyDirectory)
	require.NoError(t, err)
	require.True(t, os.SameFile(proofSourceInfo, proofReadOnlyInfo))
	t.Cleanup(func() {
		proofPath := filepath.Join(proofSourceDirectory, "proof.json")
		_ = os.Chmod(proofPath, 0o600)
		_ = os.Remove(proofPath)
		_ = os.Remove(proofLockPath)
		_ = os.Remove(proofSourceDirectory)
	})
	proofReadOnlyFile := filepath.Join(proofReadOnlyDirectory, "proof.json")
	databaseReleaseProofDirectoryName := "lifecycle-database-release-proof-" +
		strings.NewReplacer("/", "-", "\\", "-").Replace(t.Name())
	databaseReleaseProofSourceDirectory := filepath.Join(proofSourceRoot, databaseReleaseProofDirectoryName)
	databaseReleaseProofReadOnlyDirectory := filepath.Join(proofReadOnlyRoot, databaseReleaseProofDirectoryName)
	require.NoError(t, os.Mkdir(databaseReleaseProofSourceDirectory, 0o700))
	databaseReleaseProofSourceInfo, err := os.Stat(databaseReleaseProofSourceDirectory)
	require.NoError(t, err)
	databaseReleaseProofReadOnlyInfo, err := os.Stat(databaseReleaseProofReadOnlyDirectory)
	require.NoError(t, err)
	require.True(t, os.SameFile(databaseReleaseProofSourceInfo, databaseReleaseProofReadOnlyInfo))
	t.Cleanup(func() {
		databaseReleaseProofPath := filepath.Join(databaseReleaseProofSourceDirectory, "receipt.json")
		_ = os.Chmod(databaseReleaseProofPath, 0o600)
		_ = os.Remove(databaseReleaseProofPath)
		_ = os.Remove(databaseReleaseProofSourceDirectory)
	})
	databaseReleaseProofReadOnlyFile := filepath.Join(databaseReleaseProofReadOnlyDirectory, "receipt.json")
	isolationExtras := []string{
		"RELAY_COMPAT_IMAGE_DIGEST=" + relaySchemaLifecycleImageDigest,
		"RELAY_SERVICE_PRINCIPALS_FILE=" + principalFile,
		"RELAY_API_RUNTIME_SECRETS_FILE=" + runtimeSecretsFile,
		"RELAY_DOWNLOAD_EDGE_RUNTIME_SECRETS_FILE=" + edgeRuntimeSecretsFile,
		"RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE=" + providerKeyringFile,
		"RELAY_DATABASE_CA_FILE=" + relayDatabaseCAFile,
		"RELAY_REDIS_TLS_CA_FILE=" + redisCAFile,
		"PLATFORM_DATABASE_CA_FILE=" + platformDatabaseCAFile,
		"RELAY_SECRET_ISOLATION_ROLE_ADMIN_SQL_DSN_FILE=" + adminDSNFile,
		"RELAY_SECRET_ISOLATION_MIGRATION_SQL_DSN_FILE=" + migrationDSNFile,
		"RELAY_SECRET_ISOLATION_RUNTIME_SQL_DSN_FILE=" + runtimeDSNFile,
		"RELAY_SECRET_ISOLATION_EDGE_SQL_DSN_FILE=" + edgeDSNFile,
		"RELAY_MIGRATION_DATABASE_PASSWORD_FILE=" + migrationPasswordFile,
		"RELAY_RUNTIME_DATABASE_PASSWORD_FILE=" + runtimePasswordFile,
		"RELAY_DOWNLOAD_EDGE_DATABASE_PASSWORD_FILE=" + edgePasswordFile,
		"RELAY_SECRET_ISOLATION_GENERATION=pre-root",
		"RELAY_ROOT_SECRET_ISOLATION_PROOF_FILE=" + proofReadOnlyFile,
		"RELAY_SECRET_ISOLATION_COMMIT_DIRECTORY=" + commitDirectory,
		"RELAY_SECRET_ISOLATION_RECEIPT_PRE_DIRECTORY=" + receiptDirectories["pre"],
		"RELAY_SECRET_ISOLATION_RECEIPT_MIGRATE_DIRECTORY=" + receiptDirectories["migrate"],
		"RELAY_SECRET_ISOLATION_RECEIPT_POST_DIRECTORY=" + receiptDirectories["post"],
		"RELAY_SECRET_ISOLATION_RECEIPT_PRINCIPAL_DIRECTORY=" + receiptDirectories["principal"],
		"RELAY_SECRET_ISOLATION_RECEIPT_API_DIRECTORY=" + receiptDirectories["api"],
		"RELAY_SECRET_ISOLATION_RECEIPT_EDGE_DIRECTORY=" + receiptDirectories["edge"],
		"RELAY_SECRET_ISOLATION_RECEIPT_PLATFORM_MIGRATION_DIRECTORY=" + receiptDirectories["platform-migration"],
		"RELAY_SECRET_ISOLATION_RECEIPT_PLATFORM_API_DIRECTORY=" + receiptDirectories["platform-api"],
		"RELAY_SECRET_ISOLATION_RECEIPT_PLATFORM_DISPATCHER_DIRECTORY=" + receiptDirectories["platform-dispatcher"],
		"RELAY_SECRET_ISOLATION_RECEIPT_PLATFORM_RELAY_SYNC_DIRECTORY=" + receiptDirectories["platform-relay-sync"],
		"RELAY_SECRET_ISOLATION_RECEIPT_PLATFORM_TIMEOUT_WORKER_DIRECTORY=" + receiptDirectories["platform-timeout-worker"],
		"RELAY_SECRET_ISOLATION_RECEIPT_PLATFORM_PUBLISHING_WORKER_DIRECTORY=" + receiptDirectories["platform-publishing-worker"],
		"RELAY_SECRET_ISOLATION_RECEIPT_PLATFORM_DOWNLOAD_GATEWAY_REGISTRATION_WORKER_DIRECTORY=" + receiptDirectories["platform-download-gateway-registration-worker"],
		"RELAY_SECRET_ISOLATION_RECEIPT_PLATFORM_DB_ROLE_PRE_DIRECTORY=" + receiptDirectories["platform-db-role-pre"],
	}
	for environment, path := range platformSecretFiles {
		isolationExtras = append(isolationExtras, environment+"="+path)
	}
	copyReceiptToProtectedFile := func(phase, consumer string) string {
		raw, readErr := os.ReadFile(filepath.Join(receiptDirectories[consumer], "receipt.json"))
		require.NoError(t, readErr)
		defer clear(raw)
		_, readOnlyPath := relaySchemaTestWriteProtectedFile(
			t,
			"lifecycle-"+phase+"-"+consumer+"-isolation-receipt.json",
			string(raw),
		)
		return readOnlyPath
	}
	copyCommitMarkerToProtectedFile := func(phase string) string {
		raw, readErr := os.ReadFile(filepath.Join(commitDirectory, "receipt.json"))
		require.NoError(t, readErr)
		defer clear(raw)
		_, readOnlyPath := relaySchemaTestWriteProtectedFile(
			t,
			"lifecycle-"+phase+"-secret-isolation-commit.json",
			string(raw),
		)
		return readOnlyPath
	}
	roleAdminEnvironment := relaySchemaTestProtectedLifecycleBaseEnvironment(
		temporaryRoot, adminDSNFile, relayDatabaseCAFile, routeAcceptanceTrustSHA256,
	)
	migrationEnvironment := relaySchemaTestProtectedLifecycleBaseEnvironment(
		temporaryRoot, migrationDSNFile, relayDatabaseCAFile, routeAcceptanceTrustSHA256,
	)
	runDatabaseReleaseChain := func(phase, commitMarkerFile string) {
		preIsolationReceipt := copyReceiptToProtectedFile(phase, "pre")
		migrateIsolationReceipt := copyReceiptToProtectedFile(phase, "migrate")
		postIsolationReceipt := copyReceiptToProtectedFile(phase, "post")
		roleReceipt := relaySchemaTestRunLifecycleOfflineCommand(
			t, binaryPath, temporaryRoot, roleAdminEnvironment, "relay-provision-database-roles",
			"RELAY_COMPAT_IMAGE_DIGEST="+relaySchemaLifecycleImageDigest,
			"RELAY_MIGRATION_DATABASE_PASSWORD_FILE="+migrationPasswordFile,
			"RELAY_RUNTIME_DATABASE_PASSWORD_FILE="+runtimePasswordFile,
			"RELAY_DOWNLOAD_EDGE_DATABASE_PASSWORD_FILE="+edgePasswordFile,
			"RELAY_SECRET_ISOLATION_RECEIPT_FILE="+preIsolationReceipt,
			"RELAY_SECRET_ISOLATION_COMMIT_FILE="+commitMarkerFile,
			"RELAY_DATABASE_RELEASE_PROOF_DIRECTORY="+databaseReleaseProofSourceDirectory,
			"RELAY_DATABASE_RELEASE_PROOF_FILE="+databaseReleaseProofReadOnlyFile,
		)
		require.Equal(t, "relay_database_role_provision", roleReceipt["kind"])
		require.Equal(t, "provisioned", roleReceipt["state"])
		migrationReceipt := relaySchemaTestRunLifecycleOfflineCommand(
			t, binaryPath, temporaryRoot, migrationEnvironment, "relay-migrate",
			"RELAY_COMPAT_IMAGE_DIGEST="+relaySchemaLifecycleImageDigest,
			"RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE="+providerKeyringFile,
			"RELAY_SECRET_ISOLATION_RECEIPT_FILE="+migrateIsolationReceipt,
			"RELAY_SECRET_ISOLATION_COMMIT_FILE="+commitMarkerFile,
			"RELAY_DATABASE_RELEASE_PROOF_FILE="+databaseReleaseProofReadOnlyFile,
		)
		require.Equal(t, "relay_schema_migration", migrationReceipt["kind"])
		postReceipt := relaySchemaTestRunLifecycleOfflineCommand(
			t, binaryPath, temporaryRoot, roleAdminEnvironment, "relay-provision-edge-login",
			"RELAY_COMPAT_IMAGE_DIGEST="+relaySchemaLifecycleImageDigest,
			"RELAY_SECRET_ISOLATION_RECEIPT_FILE="+postIsolationReceipt,
			"RELAY_SECRET_ISOLATION_COMMIT_FILE="+commitMarkerFile,
			"RELAY_DATABASE_RELEASE_PROOF_FILE="+databaseReleaseProofReadOnlyFile,
		)
		require.Equal(t, "relay_download_edge_login_provision", postReceipt["kind"])
		require.Equal(t, "attached", postReceipt["state"])
	}
	isolationReceipt := relaySchemaTestRunLifecycleOfflineCommand(
		t, binaryPath, temporaryRoot, baseEnvironment, "relay-validate-secret-isolation", isolationExtras...,
	)
	require.Equal(t, "relay_secret_isolation", isolationReceipt["kind"])
	require.Equal(t, "validated", isolationReceipt["state"])
	require.EqualValues(t, 14, isolationReceipt["consumers"])
	preRootCommitMarkerFile := copyCommitMarkerToProtectedFile("pre-root")
	runDatabaseReleaseChain("pre-root", preRootCommitMarkerFile)
	rootPassword := relaySchemaTestSecret("root-password")[:64]
	_, rootPasswordFile := relaySchemaTestWriteProtectedFile(t, "lifecycle-root-password", rootPassword)
	rootIsolationDirectory := filepath.Join(receiptRoot, "root-bootstrap")
	require.NoError(t, os.Mkdir(rootIsolationDirectory, 0o700))
	rootIsolationEnvironment := append(append([]string(nil), isolationExtras...),
		"RELAY_PROVISION_ROOT_USERNAME=lifecycle_root",
		"RELAY_PROVISION_ROOT_PASSWORD_FILE="+rootPasswordFile,
		"RELAY_ROOT_SECRET_ISOLATION_PROOF_DIRECTORY="+proofSourceDirectory,
		"RELAY_SECRET_ISOLATION_RECEIPT_ROOT_BOOTSTRAP_DIRECTORY="+rootIsolationDirectory,
	)
	rootIsolationReceipt := relaySchemaTestRunLifecycleOfflineCommand(
		t, binaryPath, temporaryRoot, baseEnvironment,
		"relay-validate-root-secret-isolation-v1", rootIsolationEnvironment...,
	)
	require.Equal(t, "relay_root_secret_isolation", rootIsolationReceipt["kind"])
	require.Equal(t, "validated", rootIsolationReceipt["state"])
	rootIsolationRaw, readRootIsolationErr := os.ReadFile(filepath.Join(rootIsolationDirectory, "receipt.json"))
	require.NoError(t, readRootIsolationErr)
	_, rootIsolationFile := relaySchemaTestWriteProtectedFile(
		t, "lifecycle-root-isolation-receipt.json", string(rootIsolationRaw),
	)
	clear(rootIsolationRaw)
	if expectedRootProvisionState != "preprovisioned" {
		rootReceipt := relaySchemaTestRunLifecycleOfflineCommand(
			t, binaryPath, temporaryRoot, baseEnvironment, "relay-provision-root",
			"RELAY_COMPAT_IMAGE_DIGEST="+relaySchemaLifecycleImageDigest,
			"RELAY_PROVISION_ROOT_USERNAME=lifecycle_root",
			"RELAY_PROVISION_ROOT_PASSWORD_FILE="+rootPasswordFile,
			"RELAY_ROOT_SECRET_ISOLATION_PROOF_FILE="+proofReadOnlyFile,
			"RELAY_SECRET_ISOLATION_RECEIPT_FILE="+rootIsolationFile,
			"RELAY_DATABASE_RELEASE_PROOF_FILE="+databaseReleaseProofReadOnlyFile,
		)
		require.Equal(t, "relay_root_provision", rootReceipt["kind"])
		require.Equal(t, expectedRootProvisionState, rootReceipt["state"])
	}

	// The root proof is now permanent. Revalidate the complete ordinary source
	// set and replace every pre-root receipt/marker before any principal or
	// long-lived consumer is permitted to open the database.
	postRootIsolationExtras := append([]string(nil), isolationExtras...)
	for index, entry := range postRootIsolationExtras {
		if strings.HasPrefix(entry, "RELAY_SECRET_ISOLATION_GENERATION=") {
			postRootIsolationExtras[index] = "RELAY_SECRET_ISOLATION_GENERATION=root-proof-present"
		}
	}
	postRootIsolationReceipt := relaySchemaTestRunLifecycleOfflineCommand(
		t, binaryPath, temporaryRoot, baseEnvironment,
		"relay-validate-secret-isolation", postRootIsolationExtras...,
	)
	require.Equal(t, "relay_secret_isolation", postRootIsolationReceipt["kind"])
	require.Equal(t, "validated", postRootIsolationReceipt["state"])
	commitMarkerFile := copyCommitMarkerToProtectedFile("post-root")
	runDatabaseReleaseChain("post-root", commitMarkerFile)
	principalIsolationReceipt := copyReceiptToProtectedFile("post-root", "principal")
	apiIsolationReceipt := copyReceiptToProtectedFile("post-root", "api")
	if expectedPrincipalProvisionState != "preprovisioned" {
		principalReceipt := relaySchemaTestRunLifecycleOfflineCommand(
			t, binaryPath, temporaryRoot, baseEnvironment, "relay-provision-service-principals",
			"RELAY_COMPAT_IMAGE_DIGEST="+relaySchemaLifecycleImageDigest,
			"RELAY_SERVICE_PRINCIPALS_FILE="+principalFile,
			"RELAY_DATABASE_CA_FILE="+relayDatabaseCAFile,
			"RELAY_SECRET_ISOLATION_RECEIPT_FILE="+principalIsolationReceipt,
			"RELAY_SECRET_ISOLATION_COMMIT_FILE="+commitMarkerFile,
			"RELAY_DATABASE_RELEASE_PROOF_FILE="+databaseReleaseProofReadOnlyFile,
		)
		require.Equal(t, "relay_service_principal_provision", principalReceipt["kind"])
		require.Equal(t, expectedPrincipalProvisionState, principalReceipt["state"])
		require.EqualValues(t, len(clientIDs), principalReceipt["count"])
	}

	environment := append(append([]string(nil), baseEnvironment...),
		"RELAY_COMPAT_ENVIRONMENT=staging",
		"RELAY_COMPAT_IMAGE_DIGEST="+relaySchemaLifecycleImageDigest,
		"RELAY_SERVICE_PRINCIPALS_FILE="+principalFile,
		"RELAY_API_RUNTIME_SECRETS_FILE="+runtimeSecretsFile,
		"RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE="+providerKeyringFile,
		"RELAY_DATABASE_CA_FILE="+relayDatabaseCAFile,
		"RELAY_REDIS_TLS_CA_FILE="+redisCAFile,
		"RELAY_SECRET_ISOLATION_RECEIPT_FILE="+apiIsolationReceipt,
		"RELAY_SECRET_ISOLATION_COMMIT_FILE="+commitMarkerFile,
		"RELAY_DATABASE_RELEASE_PROOF_FILE="+databaseReleaseProofReadOnlyFile,
		"RELAY_COMPAT_ENABLED=true",
		"RELAY_COMPAT_WORKER_ENABLED=true",
		"RELAY_COMPAT_ROUTE_ACCEPTANCE_PUBLIC_KEYS_JSON="+routeAcceptancePublicKeysJSON,
		"RELAY_PLATFORM_CONTROL_TENANT_ID="+relaySchemaLifecycleTenantID,
		"RELAY_PROVIDER_MONITOR_ENABLED=true",
		"CHANNEL_TEST_ENABLED=true",
		"CHANNEL_TEST_FREQUENCY=1",
		"RELAY_ARTIFACT_STORE=huawei_obs",
		"HUAWEI_OBS_ENDPOINT="+obsEndpoint,
		"HUAWEI_OBS_BUCKET="+obsBucket,
		"RELAY_NATIVE_PAID_COMPAT_ENABLED=false",
		"RELAY_COMPAT_MODEL_ROUTES_JSON={}",
		"BATCH_UPDATE_ENABLED=false",
		"UPDATE_TASK=false",
		"RELAY_CODEX_CREDENTIAL_AUTO_REFRESH_ENABLED=false",
		"DEBUG=false",
		"DIFY_DEBUG=false",
		"GIN_MODE=release",
		"GLOBAL_API_RATE_LIMIT_ENABLE=false",
		"SESSION_COOKIE_SECURE=true",
		"SESSION_COOKIE_TRUSTED_URL=https://relay.test.invalid",
	)
	secretCanaries := []string{
		runtimeDSN,
		redisPassword,
		redisDSN,
		hex.EncodeToString(operationsDigest[:]),
		providerKEKBase64,
	}
	for _, clientID := range clientIDs {
		secretCanaries = append(secretCanaries, upstreamTokens[clientID], strings.TrimPrefix(upstreamTokens[clientID], "sk-"), clientAPIKeys[clientID])
	}
	secretCanaries = append(secretCanaries, platformSecretCanaries...)
	for _, label := range []string{
		"session-secret", "crypto-secret", "approval-secret",
		"internal-admission", "artifact-signing", "obs-secret-key", "provider-alert",
		"platform-internal", "channel-cost", "telemetry",
	} {
		secretCanaries = append(secretCanaries, relaySchemaTestSecret(label))
	}
	secretCanaries = append(secretCanaries, relaySchemaTestSecret("obs-access-key")[:24])
	if parsedDSN, parseErr := url.Parse(runtimeDSN); parseErr == nil && parsedDSN.User != nil {
		if password, present := parsedDSN.User.Password(); present {
			secretCanaries = append(secretCanaries, password)
		}
	}
	return relaySchemaLifecycleProcessFixture{
		binaryPath: binaryPath, environment: environment, secretCanaries: secretCanaries,
	}
}

func relaySchemaTestChildDiagnosticEvidence(logDirectory string, output string, secretCanaries []string) (string, bool) {
	combined := output
	entries, err := os.ReadDir(logDirectory)
	if err == nil {
		for _, entry := range entries {
			if entry.IsDir() || !strings.HasSuffix(strings.ToLower(entry.Name()), ".log") {
				continue
			}
			value, readErr := os.ReadFile(filepath.Join(logDirectory, entry.Name()))
			if readErr != nil {
				continue
			}
			if len(value) > 1<<20 {
				value = value[len(value)-(1<<20):]
			}
			combined += "\n" + string(value)
			clear(value)
		}
	}
	leaked := false
	for _, canary := range secretCanaries {
		if len(canary) < 12 || !strings.Contains(combined, canary) {
			continue
		}
		leaked = true
		combined = strings.ReplaceAll(combined, canary, "<redacted>")
	}
	combined = relaySchemaLifecycleDiagnosticURL.ReplaceAllString(combined, "<redacted-url>")
	combined = strings.Map(func(value rune) rune {
		if value == '\n' || value == '\r' || value == '\t' || value >= 0x20 {
			return value
		}
		return -1
	}, combined)
	const diagnosticTailLimit = 16 * 1024
	if len(combined) > diagnosticTailLimit {
		combined = combined[len(combined)-diagnosticTailLimit:]
	}
	if strings.TrimSpace(combined) == "" {
		combined = "<no child diagnostic output>"
	}
	return combined, leaked
}

func relaySchemaTestStartTLSRedis(t *testing.T, password string) (string, string) {
	t.Helper()
	caPrivate, err := rsa.GenerateKey(rand.Reader, 2048)
	require.NoError(t, err)
	caTemplate := &x509.Certificate{
		SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Relay lifecycle test CA"},
		NotBefore: time.Now().Add(-time.Minute), NotAfter: time.Now().Add(time.Hour),
		IsCA: true, BasicConstraintsValid: true,
		KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature,
	}
	caDER, err := x509.CreateCertificate(rand.Reader, caTemplate, caTemplate, &caPrivate.PublicKey, caPrivate)
	require.NoError(t, err)
	caCertificate, err := x509.ParseCertificate(caDER)
	require.NoError(t, err)
	serverPrivate, err := rsa.GenerateKey(rand.Reader, 2048)
	require.NoError(t, err)
	serverTemplate := &x509.Certificate{
		SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "localhost"},
		NotBefore: time.Now().Add(-time.Minute), NotAfter: time.Now().Add(time.Hour),
		KeyUsage:    x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		DNSNames:    []string{"localhost"}, IPAddresses: []net.IP{net.ParseIP("127.0.0.1")},
	}
	serverDER, err := x509.CreateCertificate(rand.Reader, serverTemplate, caCertificate, &serverPrivate.PublicKey, caPrivate)
	require.NoError(t, err)
	serverKey, err := x509.MarshalPKCS8PrivateKey(serverPrivate)
	require.NoError(t, err)
	certificate, err := tls.X509KeyPair(
		pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: serverDER}),
		pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: serverKey}),
	)
	require.NoError(t, err)
	listener, err := tls.Listen("tcp", "127.0.0.1:0", &tls.Config{
		Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12,
	})
	require.NoError(t, err)
	var connections sync.WaitGroup
	stop := make(chan struct{})
	go func() {
		for {
			connection, acceptErr := listener.Accept()
			if acceptErr != nil {
				return
			}
			connections.Add(1)
			go func() {
				defer connections.Done()
				relaySchemaTestServeRedisConnection(connection, password, stop)
			}()
		}
	}()
	t.Cleanup(func() {
		close(stop)
		_ = listener.Close()
		connections.Wait()
	})
	caPath := filepath.Join(t.TempDir(), "relay-lifecycle-redis-ca.pem")
	require.NoError(t, os.WriteFile(caPath, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: caDER}), 0o600))
	port := listener.Addr().(*net.TCPAddr).Port
	return fmt.Sprintf("rediss://:%s@localhost:%d/0", password, port), caPath
}

func relaySchemaTestStartTLSOBS(t *testing.T) (string, string) {
	t.Helper()
	const (
		endpointHost = "obs.lifecycle-gate.myhuaweicloud.com"
		bucket       = "relay-lifecycle-artifacts"
		bucketHost   = bucket + "." + endpointHost
	)
	certificatePath := strings.TrimSpace(os.Getenv("TEST_OBS_CERT"))
	privateKeyPath := strings.TrimSpace(os.Getenv("TEST_OBS_KEY"))
	require.NotEmpty(t, certificatePath)
	require.NotEmpty(t, privateKeyPath)
	require.True(t, filepath.IsAbs(certificatePath))
	require.True(t, filepath.IsAbs(privateKeyPath))
	certificate, err := tls.LoadX509KeyPair(certificatePath, privateKeyPath)
	require.NoError(t, err)
	listener, err := tls.Listen("tcp", "127.0.0.2:443", &tls.Config{
		Certificates: []tls.Certificate{certificate},
		MinVersion:   tls.VersionTLS12,
	})
	require.NoError(t, err)
	server := &http.Server{
		ReadHeaderTimeout: time.Second,
		Handler: http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
			requestHost := request.Host
			if host, port, splitErr := net.SplitHostPort(requestHost); splitErr == nil {
				if port != "443" {
					response.WriteHeader(http.StatusMisdirectedRequest)
					return
				}
				requestHost = host
			}
			if requestHost != bucketHost {
				response.WriteHeader(http.StatusMisdirectedRequest)
				return
			}
			if strings.TrimSpace(request.Header.Get("Authorization")) == "" {
				response.WriteHeader(http.StatusUnauthorized)
				return
			}
			switch {
			case request.Method == http.MethodGet && request.URL.Path == "/" && request.URL.RawQuery == "versioning":
				response.Header().Set("Content-Type", "application/xml")
				response.WriteHeader(http.StatusOK)
				_, _ = io.WriteString(response, "<VersioningConfiguration></VersioningConfiguration>")
			case request.Method == http.MethodHead && request.URL.Path == "/" && request.URL.RawQuery == "":
				response.WriteHeader(http.StatusOK)
			default:
				response.WriteHeader(http.StatusNotFound)
			}
		}),
	}
	serveDone := make(chan error, 1)
	go func() {
		serveDone <- server.Serve(listener)
	}()
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		require.NoError(t, server.Shutdown(ctx))
		require.ErrorIs(t, <-serveDone, http.ErrServerClosed)
	})
	return "https://" + endpointHost, bucket
}

func relaySchemaTestServeRedisConnection(connection net.Conn, password string, stop <-chan struct{}) {
	defer connection.Close()
	reader := bufio.NewReader(connection)
	authorized := false
	for {
		_ = connection.SetReadDeadline(time.Now().Add(time.Second))
		command, err := relaySchemaTestReadRedisCommand(reader)
		if err != nil {
			if timeout, ok := err.(net.Error); ok && timeout.Timeout() {
				select {
				case <-stop:
					return
				default:
					continue
				}
			}
			return
		}
		if len(command) == 0 {
			return
		}
		name := strings.ToUpper(command[0])
		if name == "AUTH" {
			provided := command[len(command)-1]
			if provided != password {
				_, _ = io.WriteString(connection, "-ERR invalid password\r\n")
				return
			}
			authorized = true
			_, _ = io.WriteString(connection, "+OK\r\n")
			continue
		}
		if !authorized {
			_, _ = io.WriteString(connection, "-NOAUTH Authentication required\r\n")
			continue
		}
		switch name {
		case "PING":
			_, _ = io.WriteString(connection, "+PONG\r\n")
		case "GET":
			_, _ = io.WriteString(connection, "$-1\r\n")
		case "HGETALL":
			_, _ = io.WriteString(connection, "*0\r\n")
		case "INFO":
			if len(command) != 2 || !strings.EqualFold(command[1], "persistence") {
				_, _ = io.WriteString(connection, "-ERR unsupported INFO section\r\n")
				continue
			}
			payload := "# Persistence\r\naof_enabled:1\r\n"
			_, _ = fmt.Fprintf(connection, "$%d\r\n%s\r\n", len(payload), payload)
		case "DEL", "EXISTS":
			_, _ = io.WriteString(connection, ":0\r\n")
		case "TTL", "PTTL":
			_, _ = io.WriteString(connection, ":-2\r\n")
		default:
			_, _ = io.WriteString(connection, "+OK\r\n")
		}
	}
}

func relaySchemaTestReadRedisCommand(reader *bufio.Reader) ([]string, error) {
	line, err := reader.ReadString('\n')
	if err != nil {
		return nil, err
	}
	line = strings.TrimSuffix(strings.TrimSuffix(line, "\n"), "\r")
	if !strings.HasPrefix(line, "*") {
		return nil, fmt.Errorf("unexpected Redis request")
	}
	count, err := strconv.Atoi(strings.TrimPrefix(line, "*"))
	if err != nil || count < 1 || count > 64 {
		return nil, fmt.Errorf("invalid Redis request")
	}
	command := make([]string, count)
	for index := range count {
		lengthLine, readErr := reader.ReadString('\n')
		if readErr != nil {
			return nil, readErr
		}
		lengthLine = strings.TrimSuffix(strings.TrimSuffix(lengthLine, "\n"), "\r")
		if !strings.HasPrefix(lengthLine, "$") {
			return nil, fmt.Errorf("invalid Redis bulk request")
		}
		length, parseErr := strconv.Atoi(strings.TrimPrefix(lengthLine, "$"))
		if parseErr != nil || length < 0 || length > 1<<20 {
			return nil, fmt.Errorf("invalid Redis bulk length")
		}
		value := make([]byte, length+2)
		if _, readErr := io.ReadFull(reader, value); readErr != nil {
			return nil, readErr
		}
		if string(value[length:]) != "\r\n" {
			return nil, fmt.Errorf("invalid Redis bulk terminator")
		}
		command[index] = string(value[:length])
	}
	return command, nil
}
