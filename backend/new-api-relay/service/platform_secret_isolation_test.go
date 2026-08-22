package service

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"math/big"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/stretchr/testify/require"
)

type platformRelaySecretIsolationTestFixture struct {
	rawByEnvironment map[string][]byte
	directories      map[string]string
	commitDirectory  string
}

type platformRelaySecretIsolationTestBindings struct {
	principals []PlatformRelayServicePrincipalProvisionInput
	relayAPI   PlatformRelayAPIRuntimeSecretsFile
	edge       PlatformDownloadEdgeRuntimeSecretsFile
	platform   map[string][]byte
}

func platformRelaySecretIsolationTestPassword(label string) string {
	digest := sha256.Sum256([]byte("relay-secret-isolation-database:" + label))
	return base64.RawURLEncoding.EncodeToString(digest[:])
}

func platformRelaySecretIsolationTestDSN(user, password string) []byte {
	query := url.Values{"search_path": {"public"}, "sslmode": {"verify-full"}}
	if root := os.Getenv(platformRelaySecretIsolationRelayDatabaseCAEnvironment); root != "" {
		query.Set("sslrootcert", root)
	}
	return []byte("postgresql://" + user + ":" + password + "@postgres.example.test:5432/relay?" + query.Encode())
}

func platformRelaySecretIsolationTestPlatformDSN(role, user string) string {
	query := url.Values{
		"sslmode":     {"verify-full"},
		"sslrootcert": {os.Getenv(platformRelaySecretIsolationPlatformDatabaseCAEnvironment)},
	}
	return "postgresql+psycopg://" + user + ":" + platformRelaySecretIsolationTestPassword("platform-"+role) +
		"@postgres.example.test:5432/platform?" + query.Encode()
}

func platformRelaySecretIsolationTestCA(t *testing.T, commonName string) []byte {
	t.Helper()
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	require.NoError(t, err)
	template := &x509.Certificate{
		SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: commonName},
		NotBefore: time.Unix(1_700_000_000, 0), NotAfter: time.Unix(1_900_000_000, 0),
		IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, publicKey, privateKey)
	require.NoError(t, err)
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
}

func platformRelaySecretIsolationTestBindingsValue(t *testing.T) platformRelaySecretIsolationTestBindings {
	t.Helper()
	tenantID := platformRelayRuntimeSecretsTestTenantA
	clientIDs := map[string]string{
		"platform-api": "client-platform-api", "dispatcher": "client-platform-dispatcher",
		"relay-sync": "client-platform-relay-sync", "timeout-worker": "client-platform-timeout",
	}
	apiKeys := map[string]string{}
	principals := make([]PlatformRelayServicePrincipalProvisionInput, 0, len(clientIDs))
	clients := make([]PlatformRelayAPIRuntimeClientSecret, 0, len(clientIDs))
	roles := []string{"platform-api", "dispatcher", "relay-sync", "timeout-worker"}
	callbackSecret := platformRelayRuntimeSecretsTestValue("platform-new-api-callback")
	for _, role := range roles {
		apiKeys[role] = platformRelayRuntimeSecretsTestValue("platform-new-api-key-" + role)
		principal := PlatformRelayServicePrincipalProvisionInput{
			ClientID: clientIDs[role], TenantID: tenantID,
			UpstreamToken: platformRelayRuntimeSecretsTestToken("platform-" + role),
		}
		client := PlatformRelayAPIRuntimeClientSecret{
			ClientID: clientIDs[role], TenantID: tenantID, APIKey: apiKeys[role],
		}
		if role == "dispatcher" {
			client.CallbackURL = "https://platform.example.test/internal/relay-callbacks/new-api-v1"
			client.CallbackSigningSecret = callbackSecret
		}
		principals = append(principals, principal)
		clients = append(clients, client)
	}
	sort.Slice(principals, func(left, right int) bool { return principals[left].ClientID < principals[right].ClientID })
	sort.Slice(clients, func(left, right int) bool { return clients[left].ClientID < clients[right].ClientID })
	operationsToken := platformRelayRuntimeSecretsTestValue("platform-operations-token")
	operationsDigest := sha256.Sum256([]byte(operationsToken))
	approvalSecret := platformRelayRuntimeSecretsTestValue("platform-approval-secret")
	relayAPI := platformRelayRuntimeSecretsTestDocument()
	relayAPI.Clients = clients
	relayAPI.OperationsCredentials = []platformGenerationOperationsCredential{{
		TenantID: tenantID, TokenSHA256: hex.EncodeToString(operationsDigest[:]),
	}}
	relayAPI.ReconciliationApprovalKeys = []platformGenerationReconciliationApprovalKey{{
		TenantID: tenantID, KeyID: "platform-approval-v1", Secret: approvalSecret,
	}}
	edge := platformDownloadEdgeRuntimeTestDocument()
	attemptDigest := sha256.Sum256([]byte("platform-download-gateway-attempt-key"))
	attemptKey := base64.StdEncoding.EncodeToString(attemptDigest[:])
	apiOBSAccessKey := "AKIDPLATFORMAPI9X7Q5M2N8R4T"
	apiOBSSecret := platformRelayRuntimeSecretsTestValue("platform-api-obs-secret")
	apiOBSToken := platformRelayRuntimeSecretsTestValue("platform-api-obs-token")
	dispatcherOBSAccessKey := "AKIDPLATFORMDISPATCH7Q5M2N8R4T"
	dispatcherOBSSecret := platformRelayRuntimeSecretsTestValue("platform-dispatcher-obs-secret")
	dispatcherOBSToken := platformRelayRuntimeSecretsTestValue("platform-dispatcher-obs-token")
	publishingAdapterCredentials := map[string]any{
		"publishers.tiktok:create": map[string]any{
			"CLIENT_SECRET": platformRelayRuntimeSecretsTestValue("platform-publishing-shared-client"),
		},
	}

	relayValues := func(role string) map[string]any {
		contract, ok := platformProcessSecretContract(role)
		require.True(t, ok)
		return map[string]any{
			"database_url": platformRelaySecretIsolationTestPlatformDSN(role, contract.databaseUser),
			"relay_backends": map[string]any{
				"new-api-v1": map[string]any{
					"base_url": "https://relay.example.test", "client_id": clientIDs[role],
					"api_key": apiKeys[role], "contract_revision": "generations.v1",
				},
			},
		}
	}
	documents := map[string]map[string]any{}
	for _, contract := range platformProcessSecretRoleContracts {
		var secrets map[string]any
		switch contract.role {
		case "migration":
			secrets = map[string]any{"database_url": platformRelaySecretIsolationTestPlatformDSN(contract.role, contract.databaseUser)}
		case "relay-sync", "timeout-worker":
			secrets = relayValues(contract.role)
		case "dispatcher":
			secrets = relayValues(contract.role)
			secrets["huawei_obs_access_key_id"] = dispatcherOBSAccessKey
			secrets["huawei_obs_secret_access_key"] = dispatcherOBSSecret
			secrets["huawei_obs_security_token"] = dispatcherOBSToken
		case "publishing-worker":
			secrets = map[string]any{
				"database_url": platformRelaySecretIsolationTestPlatformDSN(contract.role, contract.databaseUser),
				"publishing_plugin_credentials": map[string]any{
					"adapters": publishingAdapterCredentials,
					"media_resolvers": map[string]any{"publishers.obs:create_resolver": map[string]any{
						"READ_TOKEN": platformRelayRuntimeSecretsTestValue("platform-publishing-worker-read"),
					}},
				},
			}
		case "download-gateway-registration-worker":
			secrets = map[string]any{
				"database_url":                                   platformRelaySecretIsolationTestPlatformDSN(contract.role, contract.databaseUser),
				"download_gateway_service_token":                 edge.RegistrationToken,
				"download_gateway_registration_signing_secret":   edge.RegistrationSigningSecret,
				"download_gateway_attempt_encryption_key_base64": attemptKey,
			}
		case "platform-api":
			secrets = relayValues(contract.role)
			for name, value := range map[string]any{
				"relay_tenant_id":                                   tenantID,
				"relay_operations_token":                            operationsToken,
				"relay_reconciliation_approval_key_id":              "platform-approval-v1",
				"relay_reconciliation_approval_secret":              approvalSecret,
				"relay_callback_signing_secrets":                    map[string]any{"new-api-v1": callbackSecret},
				"internal_service_token":                            relayAPI.PlatformInternalToken,
				"download_edge_completion_service_token":            edge.PlatformEdgeCompletionToken,
				"channel_cost_signing_secret":                       relayAPI.ChannelCostSigningSecret,
				"relay_telemetry_signing_secret":                    relayAPI.TelemetrySigningSecret,
				"provider_alert_signing_secret":                     relayAPI.ProviderAlertSigningSecret,
				"provider_alert_forward_signing_secret":             platformRelayRuntimeSecretsTestValue("platform-alert-forward"),
				"download_completion_edge_gateway_signing_secret":   edge.CompletionSigningSecret,
				"download_completion_obs_access_log_signing_secret": platformRelayRuntimeSecretsTestValue("platform-obs-completion"),
				"download_gateway_service_token":                    edge.RegistrationToken,
				"download_gateway_registration_signing_secret":      edge.RegistrationSigningSecret,
				"download_gateway_attempt_encryption_key_base64":    attemptKey,
				"jwt_signing_secret":                                platformRelayRuntimeSecretsTestValue("platform-jwt"),
				"huawei_obs_access_key_id":                          apiOBSAccessKey,
				"huawei_obs_secret_access_key":                      apiOBSSecret,
				"huawei_obs_security_token":                         apiOBSToken,
				"publishing_plugin_credentials":                     map[string]any{"adapters": publishingAdapterCredentials},
			} {
				secrets[name] = value
			}
		}
		documents[contract.role] = map[string]any{
			"kind": platformProcessSecretKind, "schema_version": platformProcessSecretSchemaVersion,
			"process_role": contract.role, "secrets": secrets,
		}
	}
	platformRaw := make(map[string][]byte, len(documents))
	for role, document := range documents {
		raw, err := json.Marshal(document)
		require.NoError(t, err)
		platformRaw[role] = raw
	}
	return platformRelaySecretIsolationTestBindings{
		principals: principals, relayAPI: relayAPI, edge: edge, platform: platformRaw,
	}
}

func setPlatformRelaySecretIsolationTestRelease(t *testing.T) {
	t.Helper()
	previousUpstream := platformRelayCompiledUpstreamRevision
	previousRevision := platformRelayCompiledSourceRevision
	previousSnapshot := platformRelayCompiledSnapshotSHA256
	previousCount := platformRelayCompiledSnapshotFileCount
	previousTrust := platformRelayCompiledRouteAcceptanceKeysSHA256
	t.Cleanup(func() {
		platformRelayCompiledUpstreamRevision = previousUpstream
		platformRelayCompiledSourceRevision = previousRevision
		platformRelayCompiledSnapshotSHA256 = previousSnapshot
		platformRelayCompiledSnapshotFileCount = previousCount
		platformRelayCompiledRouteAcceptanceKeysSHA256 = previousTrust
	})
	platformRelayCompiledUpstreamRevision = PlatformRelayUpstreamGitRevision
	platformRelayCompiledSourceRevision = strings.Repeat("1", 40)
	platformRelayCompiledSnapshotSHA256 = "sha256:" + strings.Repeat("2", 64)
	platformRelayCompiledSnapshotFileCount = "37"
	platformRelayCompiledRouteAcceptanceKeysSHA256 = "sha256:" + strings.Repeat("3", 64)
	t.Setenv("RELAY_COMPAT_IMAGE_DIGEST", "sha256:"+strings.Repeat("4", 64))
	t.Setenv("RELAY_COMPAT_SOURCE_REVISION", platformRelayCompiledSourceRevision)
	t.Setenv("RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256", platformRelayCompiledSnapshotSHA256)
	t.Setenv("RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT", platformRelayCompiledSnapshotFileCount)
	t.Setenv("RELAY_COMPAT_UPSTREAM_REVISION", PlatformRelayUpstreamGitRevision)
	t.Setenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_TRUST_KEYS_SHA256", platformRelayCompiledRouteAcceptanceKeysSHA256)
	t.Setenv("PLATFORM_IMAGE", "registry.example.test/ai-video/platform@sha256:"+strings.Repeat("5", 64))
	t.Setenv("PLATFORM_SOURCE_REVISION", strings.Repeat("6", 40))
	t.Setenv("PLATFORM_SOURCE_SNAPSHOT_SHA256", "sha256:"+strings.Repeat("7", 64))
	t.Setenv(platformRelaySecretIsolationPlatformOriginEnvironment, "https://platform.example.test")
	t.Setenv(platformRelaySecretIsolationRelayOriginEnvironment, "https://relay.example.test")
	t.Setenv(platformRelaySecretIsolationEdgeOriginEnvironment, "https://downloads.example.test")
	t.Setenv(platformRelaySecretIsolationRelayContractEnvironment, "generations.v1")
	t.Setenv(platformRelaySecretIsolationRelayDatabaseCAEnvironment, filepath.Join(t.TempDir(), "relay-database-ca.pem"))
	t.Setenv(PlatformRelayRedisTLSCAFileEnvironment, filepath.Join(t.TempDir(), "relay-redis-tls-ca.pem"))
	t.Setenv(platformRelaySecretIsolationPlatformDatabaseCAEnvironment, filepath.Join(t.TempDir(), "platform-database-ca.pem"))
	t.Setenv("RELAY_PROVIDER_ALERT_WEBHOOK_URL", "https://platform.example.test/internal/relay/provider-alerts")
	t.Setenv("RELAY_PLATFORM_CHANNEL_COST_URL", "https://platform.example.test/internal/channel-costs")
	t.Setenv("RELAY_PLATFORM_TASK_STAGE_URL", "https://platform.example.test/internal/relay/task-stages")
	t.Setenv("RELAY_PLATFORM_OPERATIONS_SNAPSHOT_URL", "https://platform.example.test/internal/relay/operations-snapshots")
	t.Setenv("RELAY_DOWNLOAD_EDGE_PUBLIC_BASE_URL", "https://downloads.example.test")
	t.Setenv("RELAY_DOWNLOAD_EDGE_PLATFORM_COMPLETION_URL", "https://platform.example.test/internal/artifact-download-completions/edge-gateway")
	t.Setenv("DOWNLOAD_GATEWAY_REGISTRATION_URL", "https://downloads.example.test/internal/v1/download-tickets")
}

func newPlatformRelaySecretIsolationTestFixture(t *testing.T) *platformRelaySecretIsolationTestFixture {
	t.Helper()
	setPlatformRelaySecretIsolationTestRelease(t)
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_MIGRATION_DATABASE_ROLE", "relay_schema_migrator")
	t.Setenv("RELAY_SCHEMA_OWNER_DATABASE_ROLE", "relay_schema_owner")
	t.Setenv(platformProcessDatabaseNameEnvironment, "platform")
	bindings := platformRelaySecretIsolationTestBindingsValue(t)
	principalsRaw, err := json.Marshal(PlatformRelayServicePrincipalsFile{
		Kind: PlatformRelayServicePrincipalsFileKind, SchemaVersion: PlatformRelayServicePrincipalsFileSchemaVersion,
		Principals: bindings.principals,
	})
	require.NoError(t, err)
	apiRaw, err := json.Marshal(bindings.relayAPI)
	require.NoError(t, err)
	edgeRaw, err := json.Marshal(bindings.edge)
	require.NoError(t, err)
	kek := make([]byte, 32)
	for index := range kek {
		kek[index] = byte(128 + index)
	}
	kekRaw, err := json.Marshal(map[string]any{
		"schema_version": 1,
		"active_key_id":  "isolation-v1",
		"keys":           map[string]string{"isolation-v1": base64.StdEncoding.EncodeToString(kek)},
	})
	clear(kek)
	require.NoError(t, err)

	roleAdminPassword := platformRelaySecretIsolationTestPassword("role-admin")
	migrationPassword := platformRelaySecretIsolationTestPassword("migration")
	runtimePassword := platformRelaySecretIsolationTestPassword("runtime")
	edgePassword := platformRelaySecretIsolationTestPassword("edge")
	platformRoleAdminPassword := platformRelaySecretIsolationTestPassword("platform-role-admin")
	rootProofPassword := []byte(platformRelaySecretIsolationTestPassword("root-proof"))
	rootProofDigest := sha256.Sum256(rootProofPassword)
	clear(rootProofPassword)
	release, err := platformRelaySecretIsolationReleaseIdentity()
	require.NoError(t, err)
	rootProofRaw, err := json.Marshal(platformRelayRootIsolationProof{
		SchemaVersion:      platformRelayRootProofSchemaVersion,
		Kind:               platformRelayRootProofKind,
		ProofID:            strings.Repeat("9", 64),
		CreatedRelease:     release,
		RootPasswordSHA256: hex.EncodeToString(rootProofDigest[:]),
	})
	require.NoError(t, err)
	t.Setenv(platformRelaySecretIsolationGenerationEnvironment, platformRelaySecretIsolationGenerationRootProofPresent)
	rootProofDirectory := t.TempDir()
	require.NoError(t, os.Chmod(rootProofDirectory, 0o700))
	require.NoError(t, os.WriteFile(
		filepath.Join(rootProofDirectory, platformRelayRootProofLockFileName), nil, 0o600,
	))
	t.Setenv(PlatformRelayRootProofFileEnvironment, filepath.Join(rootProofDirectory, "root-proof.json"))
	raw := map[string][]byte{
		"RELAY_SERVICE_PRINCIPALS_FILE":                           principalsRaw,
		"RELAY_API_RUNTIME_SECRETS_FILE":                          apiRaw,
		"RELAY_DOWNLOAD_EDGE_RUNTIME_SECRETS_FILE":                edgeRaw,
		"RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE":                  kekRaw,
		platformRelaySecretIsolationRoleAdminDSNEnvironment:       platformRelaySecretIsolationTestDSN("role_admin", roleAdminPassword),
		platformRelaySecretIsolationMigrationDSNEnvironment:       append(platformRelaySecretIsolationTestDSN("relay_schema_migrator", migrationPassword), []byte("&options=-c%20role%3Drelay_schema_owner")...),
		platformRelaySecretIsolationRuntimeDSNEnvironment:         platformRelaySecretIsolationTestDSN("relay_runtime", runtimePassword),
		platformRelaySecretIsolationEdgeDSNEnvironment:            platformRelaySecretIsolationTestDSN("relay_download_edge", edgePassword),
		"RELAY_MIGRATION_DATABASE_PASSWORD_FILE":                  []byte(migrationPassword),
		"RELAY_RUNTIME_DATABASE_PASSWORD_FILE":                    []byte(runtimePassword),
		"RELAY_DOWNLOAD_EDGE_DATABASE_PASSWORD_FILE":              []byte(edgePassword),
		platformRelaySecretIsolationRelayDatabaseCAEnvironment:    platformRelaySecretIsolationTestCA(t, "Relay isolation test CA"),
		PlatformRelayRedisTLSCAFileEnvironment:                    platformRelaySecretIsolationTestCA(t, "Relay Redis isolation test CA"),
		platformRelaySecretIsolationPlatformDatabaseCAEnvironment: platformRelaySecretIsolationTestCA(t, "Platform isolation test CA"),
		PlatformRelayRootProofFileEnvironment:                     rootProofRaw,
		platformRelaySecretIsolationPlatformRoleAdminDSNEnvironment: []byte(
			"postgresql+psycopg://platform_role_admin:" + platformRoleAdminPassword +
				"@postgres.example.test:5432/postgres?" + url.Values{
				"sslmode": {"verify-full"}, "sslrootcert": {os.Getenv(platformRelaySecretIsolationPlatformDatabaseCAEnvironment)},
			}.Encode(),
		),
	}
	for _, contract := range platformProcessSecretRoleContracts {
		raw[contract.environment] = bindings.platform[contract.role]
		raw[contract.passwordEnvironment] = []byte(platformRelaySecretIsolationTestPassword("platform-" + contract.role))
	}
	root := t.TempDir()
	for environment := range raw {
		if environment == platformRelaySecretIsolationRelayDatabaseCAEnvironment ||
			environment == platformRelaySecretIsolationPlatformDatabaseCAEnvironment ||
			environment == PlatformRelayRootProofFileEnvironment {
			continue
		}
		t.Setenv(environment, filepath.Join(root, "source-"+strings.ToLower(environment)))
	}
	directories := make(map[string]string, len(platformRelaySecretIsolationConsumers))
	commitDirectory := filepath.Join(root, "commit")
	require.NoError(t, os.Mkdir(commitDirectory, 0o700))
	t.Setenv(platformRelaySecretIsolationCommitDirectoryEnvironment, commitDirectory)
	for _, consumer := range platformRelaySecretIsolationConsumers {
		directory := filepath.Join(root, consumer)
		require.NoError(t, os.Mkdir(directory, 0o700))
		t.Setenv(platformRelaySecretIsolationReceiptDirectoryEnvironment(consumer), directory)
		directories[consumer] = directory
	}
	previousReader := readPlatformRelaySecretIsolationProtectedFile
	previousProofAbsent := platformRelayRootIsolationProofSourceAbsent
	readPlatformRelaySecretIsolationProtectedFile = func(environment string, maximumBytes int64) ([]byte, error) {
		value, ok := raw[environment]
		if !ok || len(value) == 0 || int64(len(value)) > maximumBytes {
			return nil, errors.New("test source unavailable")
		}
		return append([]byte(nil), value...), nil
	}
	platformRelayRootIsolationProofSourceAbsent = func(path string) bool {
		return path == os.Getenv(PlatformRelayRootProofFileEnvironment) &&
			len(raw[PlatformRelayRootProofFileEnvironment]) == 0
	}
	t.Cleanup(func() {
		readPlatformRelaySecretIsolationProtectedFile = previousReader
		platformRelayRootIsolationProofSourceAbsent = previousProofAbsent
		for _, value := range raw {
			clear(value)
		}
	})
	return &platformRelaySecretIsolationTestFixture{
		rawByEnvironment: raw, directories: directories, commitDirectory: commitDirectory,
	}
}

func (fixture *platformRelaySecretIsolationTestFixture) receipt(t *testing.T, consumer string) []byte {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(fixture.directories[consumer], "receipt.json"))
	require.NoError(t, err)
	return raw
}

func (fixture *platformRelaySecretIsolationTestFixture) commitMarker(t *testing.T) []byte {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(fixture.commitDirectory, "receipt.json"))
	require.NoError(t, err)
	return raw
}

func (fixture *platformRelaySecretIsolationTestFixture) installConsumerProof(t *testing.T, consumer string) {
	t.Helper()
	t.Setenv(platformRelaySecretIsolationReceiptFileEnvironment, filepath.Join(t.TempDir(), "receipt.json"))
	t.Setenv(platformRelaySecretIsolationCommitFileEnvironment, filepath.Join(t.TempDir(), "commit.json"))
	fixture.rawByEnvironment[platformRelaySecretIsolationReceiptFileEnvironment] = fixture.receipt(t, consumer)
	fixture.rawByEnvironment[platformRelaySecretIsolationCommitFileEnvironment] = fixture.commitMarker(t)
}

func (fixture *platformRelaySecretIsolationTestFixture) replace(environment string, value []byte) {
	clear(fixture.rawByEnvironment[environment])
	fixture.rawByEnvironment[environment] = value
}

func (fixture *platformRelaySecretIsolationTestFixture) mutatePlatform(
	t *testing.T,
	role string,
	mutate func(map[string]any),
) {
	t.Helper()
	contract, ok := platformProcessSecretContract(role)
	require.True(t, ok)
	var document map[string]any
	require.NoError(t, json.Unmarshal(fixture.rawByEnvironment[contract.environment], &document))
	secrets, ok := document["secrets"].(map[string]any)
	require.True(t, ok)
	mutate(secrets)
	raw, err := json.Marshal(document)
	require.NoError(t, err)
	fixture.replace(contract.environment, raw)
}

func TestValidateAndCommitPlatformRelaySecretIsolationWritesLeastPrivilegeReceipts(t *testing.T) {
	fixture := newPlatformRelaySecretIsolationTestFixture(t)
	require.Len(t, platformRelaySecretIsolationConsumers, PlatformRelaySecretIsolationConsumerCount)
	require.NoError(t, ValidateAndCommitPlatformRelaySecretIsolation())

	expectedFiles := map[string][]string{
		PlatformRelaySecretIsolationConsumerPre:       {"edge_password", "migration_password", "relay_database_ca", "role_admin_dsn", "runtime_password"},
		PlatformRelaySecretIsolationConsumerMigrate:   {"migration_dsn", "provider_kek", "relay_database_ca"},
		PlatformRelaySecretIsolationConsumerPost:      {"relay_database_ca", "role_admin_dsn"},
		PlatformRelaySecretIsolationConsumerPrincipal: {"relay_database_ca", "runtime_dsn", "service_principals"},
		PlatformRelaySecretIsolationConsumerAPI:       {"api_runtime", "provider_kek", "redis_tls_ca", "relay_database_ca", "runtime_dsn", "service_principals"},
		PlatformRelaySecretIsolationConsumerEdge:      {"edge_dsn", "edge_runtime", "relay_database_ca"},
		PlatformRelaySecretIsolationConsumerPlatformDBRolePre: {
			"platform_database_ca", "platform_role_admin_dsn", "platform_migration_password", "platform_api_password",
			"platform_dispatcher_password", "platform_relay_sync_password", "platform_timeout_worker_password",
			"platform_publishing_worker_password", "platform_download_gateway_worker_password",
		},
		PlatformRelaySecretIsolationConsumerPlatformMigration:             {"platform_database_ca", "platform_migration_runtime"},
		PlatformRelaySecretIsolationConsumerPlatformAPI:                   {"platform_api_runtime", "platform_database_ca"},
		PlatformRelaySecretIsolationConsumerPlatformDispatcher:            {"platform_database_ca", "platform_dispatcher_runtime"},
		PlatformRelaySecretIsolationConsumerPlatformRelaySync:             {"platform_database_ca", "platform_relay_sync_runtime"},
		PlatformRelaySecretIsolationConsumerPlatformTimeoutWorker:         {"platform_database_ca", "platform_timeout_worker_runtime"},
		PlatformRelaySecretIsolationConsumerPlatformPublishingWorker:      {"platform_database_ca", "platform_publishing_worker_runtime"},
		PlatformRelaySecretIsolationConsumerPlatformDownloadGatewayWorker: {"platform_database_ca", "platform_download_gateway_worker_runtime"},
	}
	for _, consumer := range platformRelaySecretIsolationConsumers {
		raw := fixture.receipt(t, consumer)
		receipt, err := platformRelaySecretIsolationParseReceipt(raw)
		clear(raw)
		require.NoError(t, err)
		require.Equal(t, consumer, receipt.Consumer)
		require.NotEmpty(t, receipt.Files)
		require.NotEmpty(t, receipt.Semantics)
		fileIDs := make([]string, 0, len(receipt.Files))
		for _, commitment := range receipt.Files {
			fileIDs = append(fileIDs, commitment.ID)
		}
		require.ElementsMatch(t, expectedFiles[consumer], fileIDs)
		entries, err := os.ReadDir(fixture.directories[consumer])
		require.NoError(t, err)
		require.Len(t, entries, 1)
		require.Equal(t, "receipt.json", entries[0].Name())
	}
	markerRaw := fixture.commitMarker(t)
	marker, err := platformRelaySecretIsolationParseCommitMarker(markerRaw)
	clear(markerRaw)
	require.NoError(t, err)
	require.Len(t, marker.Receipts, PlatformRelaySecretIsolationConsumerCount)
	require.Equal(t, platformRelaySecretIsolationGenerationRootProofPresent, marker.Generation)
	require.NotEmpty(t, marker.RootProofID)
}

func TestPlatformRelaySecretIsolationMarkerV2CrossLanguageGolden(t *testing.T) {
	setPlatformRelaySecretIsolationTestRelease(t)
	release, err := platformRelaySecretIsolationReleaseIdentity()
	require.NoError(t, err)
	marker := platformRelaySecretIsolationCommitMarker{
		SchemaVersion: platformRelaySecretIsolationCommitSchemaVersion,
		Kind:          platformRelaySecretIsolationCommitKind,
		RunID:         strings.Repeat("a", 64),
		Generation:    platformRelaySecretIsolationGenerationRootProofPresent,
		RootProofID:   strings.Repeat("b", 64),
		Release:       release,
	}
	for _, consumer := range platformRelaySecretIsolationConsumers {
		digest := sha256.Sum256([]byte("receipt:" + consumer))
		marker.Receipts = append(marker.Receipts, platformRelaySecretIsolationCommitment{
			ID: consumer, SHA256: hex.EncodeToString(digest[:]),
		})
	}
	sort.Slice(marker.Receipts, func(i, j int) bool { return marker.Receipts[i].ID < marker.Receipts[j].ID })
	raw, err := platformRelaySecretIsolationCanonicalCommitMarker(marker)
	require.NoError(t, err)
	digest := sha256.Sum256(raw)
	require.Equal(t, "80df3044f8b70c36b106ebf3b32c50159e25bc8d7f97e682dcf82d1918729c17",
		hex.EncodeToString(digest[:]), string(raw))
}

func TestPreRootSecretIsolationGenerationCannotStartLongLivedConsumers(t *testing.T) {
	fixture := newPlatformRelaySecretIsolationTestFixture(t)
	t.Setenv(platformRelaySecretIsolationGenerationEnvironment, platformRelaySecretIsolationGenerationPreRoot)
	delete(fixture.rawByEnvironment, PlatformRelayRootProofFileEnvironment)
	require.NoError(t, ValidateAndCommitPlatformRelaySecretIsolation())

	markerRaw := fixture.commitMarker(t)
	marker, err := platformRelaySecretIsolationParseCommitMarker(markerRaw)
	clear(markerRaw)
	require.NoError(t, err)
	require.Equal(t, platformRelaySecretIsolationGenerationPreRoot, marker.Generation)
	require.Empty(t, marker.RootProofID)
	for _, consumer := range []string{
		PlatformRelaySecretIsolationConsumerPre,
		PlatformRelaySecretIsolationConsumerMigrate,
		PlatformRelaySecretIsolationConsumerPost,
	} {
		require.True(t, platformRelaySecretIsolationConsumerAcceptsGeneration(
			consumer, marker.Generation, marker.RootProofID,
		))
	}
	for _, consumer := range []string{
		PlatformRelaySecretIsolationConsumerPrincipal,
		PlatformRelaySecretIsolationConsumerAPI,
		PlatformRelaySecretIsolationConsumerEdge,
		PlatformRelaySecretIsolationConsumerPlatformDBRolePre,
		PlatformRelaySecretIsolationConsumerPlatformAPI,
	} {
		require.False(t, platformRelaySecretIsolationConsumerAcceptsGeneration(
			consumer, marker.Generation, marker.RootProofID,
		))
	}

	fixture.installConsumerProof(t, PlatformRelaySecretIsolationConsumerPrincipal)
	require.EqualError(t, VerifyPlatformRelaySecretIsolationReceipt(
		PlatformRelaySecretIsolationConsumerPrincipal,
	), "Relay secret isolation generation is not valid for this consumer")
}

func TestPreRootSecretIsolationGenerationCannotDowngradeExistingProof(t *testing.T) {
	newPlatformRelaySecretIsolationTestFixture(t)
	t.Setenv(platformRelaySecretIsolationGenerationEnvironment, platformRelaySecretIsolationGenerationPreRoot)
	require.EqualError(t, ValidateAndCommitPlatformRelaySecretIsolation(),
		"Relay secret isolation cannot downgrade an existing root proof")
}

func TestSecretIsolationGenerationAlwaysRequiresTheFixedProofMount(t *testing.T) {
	newPlatformRelaySecretIsolationTestFixture(t)
	for _, generation := range []string{
		platformRelaySecretIsolationGenerationPreRoot,
		platformRelaySecretIsolationGenerationRootProofPresent,
	} {
		t.Run(generation, func(t *testing.T) {
			t.Setenv(platformRelaySecretIsolationGenerationEnvironment, generation)
			t.Setenv(PlatformRelayRootProofFileEnvironment, "")
			require.EqualError(t, ValidateAndCommitPlatformRelaySecretIsolation(),
				"Relay root isolation proof path is invalid")
		})
	}
}

func TestProofPresentSecretIsolationGenerationRequiresCurrentProof(t *testing.T) {
	fixture := newPlatformRelaySecretIsolationTestFixture(t)
	delete(fixture.rawByEnvironment, PlatformRelayRootProofFileEnvironment)
	require.EqualError(t, ValidateAndCommitPlatformRelaySecretIsolation(),
		"Relay root isolation proof is unavailable")
}

func TestValidateAndCommitPlatformRelaySecretIsolationRejectsCrossBundleReuseAndClearsOldReceipts(t *testing.T) {
	fixture := newPlatformRelaySecretIsolationTestFixture(t)
	require.NoError(t, ValidateAndCommitPlatformRelaySecretIsolation())

	principals, err := ParsePlatformRelayServicePrincipalsFile(fixture.rawByEnvironment["RELAY_SERVICE_PRINCIPALS_FILE"])
	require.NoError(t, err)
	edgeDocument := platformDownloadEdgeRuntimeTestDocument()
	edgeDocument.RegistrationToken = principals[0].UpstreamToken
	mutated, err := json.Marshal(edgeDocument)
	require.NoError(t, err)
	fixture.replace("RELAY_DOWNLOAD_EDGE_RUNTIME_SECRETS_FILE", mutated)

	err = ValidateAndCommitPlatformRelaySecretIsolation()
	require.EqualError(t, err, "Relay secret isolation found cross-domain secret reuse")
	for _, consumer := range platformRelaySecretIsolationConsumers {
		_, statErr := os.Stat(filepath.Join(fixture.directories[consumer], "receipt.json"))
		require.ErrorIs(t, statErr, os.ErrNotExist)
	}
}

func TestValidateAndCommitPlatformRelaySecretIsolationRejectsEveryCrossDomainRepresentationClass(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*testing.T, *platformRelaySecretIsolationTestFixture)
	}{
		{
			name: "bare service token versus edge text",
			mutate: func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
				principals, err := ParsePlatformRelayServicePrincipalsFile(fixture.rawByEnvironment["RELAY_SERVICE_PRINCIPALS_FILE"])
				require.NoError(t, err)
				edge := platformDownloadEdgeRuntimeTestDocument()
				edge.RegistrationToken = strings.TrimPrefix(principals[0].UpstreamToken, "sk-")
				raw, err := json.Marshal(edge)
				require.NoError(t, err)
				fixture.replace("RELAY_DOWNLOAD_EDGE_RUNTIME_SECRETS_FILE", raw)
			},
		},
		{
			name: "API text versus edge text",
			mutate: func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
				api := platformRelayRuntimeSecretsTestDocument()
				edge := platformDownloadEdgeRuntimeTestDocument()
				edge.RegistrationToken = api.Application.SessionSecret
				raw, err := json.Marshal(edge)
				require.NoError(t, err)
				fixture.replace("RELAY_DOWNLOAD_EDGE_RUNTIME_SECRETS_FILE", raw)
			},
		},
		{
			name: "operations digest versus edge text",
			mutate: func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
				bindings := platformRelaySecretIsolationTestBindingsValue(t)
				api := bindings.relayAPI
				edge := bindings.edge
				digest := sha256.Sum256([]byte(edge.RegistrationToken))
				api.OperationsCredentials[0].TokenSHA256 = hex.EncodeToString(digest[:])
				raw, err := json.Marshal(api)
				require.NoError(t, err)
				fixture.replace("RELAY_API_RUNTIME_SECRETS_FILE", raw)
				var platformAPI map[string]any
				require.NoError(t, json.Unmarshal(fixture.rawByEnvironment["PLATFORM_API_RUNTIME_SECRETS_FILE"], &platformAPI))
				platformAPI["secrets"].(map[string]any)["relay_operations_token"] = edge.RegistrationToken
				raw, err = json.Marshal(platformAPI)
				require.NoError(t, err)
				fixture.replace("PLATFORM_API_RUNTIME_SECRETS_FILE", raw)
			},
		},
		{
			name: "edge encoded and decoded key versus provider KEK",
			mutate: func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
				edge := platformDownloadEdgeRuntimeTestDocument()
				raw, err := json.Marshal(map[string]any{
					"schema_version": 1,
					"active_key_id":  "edge-reuse-v1",
					"keys": map[string]string{
						"edge-reuse-v1": edge.TicketTokenKeyBase64,
					},
				})
				require.NoError(t, err)
				fixture.replace("RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE", raw)
			},
		},
		{
			name: "role admin password versus migration password",
			mutate: func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
				fixture.replace(platformRelaySecretIsolationRoleAdminDSNEnvironment,
					platformRelaySecretIsolationTestDSN("role_admin", platformRelaySecretIsolationTestPassword("migration")))
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			fixture := newPlatformRelaySecretIsolationTestFixture(t)
			test.mutate(t, fixture)
			require.EqualError(t, ValidateAndCommitPlatformRelaySecretIsolation(),
				"Relay secret isolation found cross-domain secret reuse")
		})
	}
}

func TestValidateAndCommitPlatformRelaySecretIsolationRejectsDSNQuerySecretOverride(t *testing.T) {
	fixture := newPlatformRelaySecretIsolationTestFixture(t)
	value := append([]byte(nil), fixture.rawByEnvironment[platformRelaySecretIsolationRoleAdminDSNEnvironment]...)
	value = append(value, []byte("&password=uncommitted-query-password")...)
	fixture.replace(platformRelaySecretIsolationRoleAdminDSNEnvironment, value)
	require.EqualError(t, ValidateAndCommitPlatformRelaySecretIsolation(),
		"Relay secret isolation database source is invalid")
}

func TestPlatformRelaySecretIsolationDatabaseEndpointAndTargetCanonicalForms(t *testing.T) {
	fixture := newPlatformRelaySecretIsolationTestFixture(t)
	parsed, err := url.Parse(string(fixture.rawByEnvironment[platformRelaySecretIsolationRuntimeDSNEnvironment]))
	require.NoError(t, err)

	endpoint, err := platformRelaySecretIsolationDatabaseEndpoint(parsed, "")
	require.NoError(t, err)
	require.Equal(t,
		"postgres-endpoint-v1\nhost=postgres.example.test\nport=5432\ndatabase=relay",
		endpoint,
	)
	target, err := platformRelaySecretIsolationDatabaseTarget(parsed, "")
	require.NoError(t, err)
	require.Equal(t,
		"postgres-target-v1\nhost=postgres.example.test\nport=5432\ndatabase=relay\nsslmode=verify-full\nsslrootcert="+
			os.Getenv(platformRelaySecretIsolationRelayDatabaseCAEnvironment),
		target,
	)

	// The supported production shape deliberately has four Relay roles sharing
	// one database and eight Platform roles sharing a different database.
	require.NoError(t, ValidateAndCommitPlatformRelaySecretIsolation())
}

func TestValidateAndCommitPlatformRelaySecretIsolationRejectsDatabaseTargetDrift(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*testing.T, *platformRelaySecretIsolationTestFixture)
	}{
		{
			name: "Relay host",
			mutate: func(_ *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
				value := strings.Replace(
					string(fixture.rawByEnvironment[platformRelaySecretIsolationRuntimeDSNEnvironment]),
					"postgres.example.test", "attacker.example.test", 1,
				)
				fixture.replace(platformRelaySecretIsolationRuntimeDSNEnvironment, []byte(value))
			},
		},
		{
			name: "Relay port",
			mutate: func(_ *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
				value := strings.Replace(
					string(fixture.rawByEnvironment[platformRelaySecretIsolationEdgeDSNEnvironment]),
					":5432/relay", ":5433/relay", 1,
				)
				fixture.replace(platformRelaySecretIsolationEdgeDSNEnvironment, []byte(value))
			},
		},
		{
			name: "Relay database",
			mutate: func(_ *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
				value := strings.Replace(
					string(fixture.rawByEnvironment[platformRelaySecretIsolationMigrationDSNEnvironment]),
					"/relay?", "/relay_clone?", 1,
				)
				fixture.replace(platformRelaySecretIsolationMigrationDSNEnvironment, []byte(value))
			},
		},
		{
			name: "Relay TLS root policy",
			mutate: func(_ *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
				value := append(
					append([]byte(nil), fixture.rawByEnvironment[platformRelaySecretIsolationRoleAdminDSNEnvironment]...),
					[]byte("&sslrootcert=/run/secrets/alternate-ca.pem")...,
				)
				fixture.replace(platformRelaySecretIsolationRoleAdminDSNEnvironment, value)
			},
		},
		{
			name: "Platform host",
			mutate: func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
				fixture.mutatePlatform(t, "platform-api", func(secrets map[string]any) {
					secrets["database_url"] = strings.Replace(
						secrets["database_url"].(string), "postgres.example.test", "attacker.example.test", 1,
					)
				})
			},
		},
		{
			name: "Platform port",
			mutate: func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
				fixture.mutatePlatform(t, "dispatcher", func(secrets map[string]any) {
					secrets["database_url"] = strings.Replace(
						secrets["database_url"].(string), ":5432/platform", ":5433/platform", 1,
					)
				})
			},
		},
		{
			name: "Platform database",
			mutate: func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
				fixture.mutatePlatform(t, "relay-sync", func(secrets map[string]any) {
					secrets["database_url"] = strings.Replace(
						secrets["database_url"].(string), "/platform?", "/platform_clone?", 1,
					)
				})
			},
		},
		{
			name: "Platform role-admin cluster",
			mutate: func(_ *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
				value := strings.Replace(
					string(fixture.rawByEnvironment[platformRelaySecretIsolationPlatformRoleAdminDSNEnvironment]),
					"postgres.example.test", "attacker.example.test", 1,
				)
				fixture.replace(platformRelaySecretIsolationPlatformRoleAdminDSNEnvironment, []byte(value))
			},
		},
		{
			name: "Platform TLS root policy",
			mutate: func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
				fixture.mutatePlatform(t, "platform-api", func(secrets map[string]any) {
					secrets["database_url"] = strings.Replace(
						secrets["database_url"].(string),
						url.QueryEscape(os.Getenv(platformRelaySecretIsolationPlatformDatabaseCAEnvironment)),
						url.QueryEscape(filepath.Join(t.TempDir(), "alternate-ca.pem")),
						1,
					)
				})
			},
		},
		{
			name: "Platform target database declaration",
			mutate: func(t *testing.T, _ *platformRelaySecretIsolationTestFixture) {
				t.Setenv(platformProcessDatabaseNameEnvironment, "platform_clone")
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			fixture := newPlatformRelaySecretIsolationTestFixture(t)
			test.mutate(t, fixture)
			require.Error(t, ValidateAndCommitPlatformRelaySecretIsolation())
		})
	}
}

func TestValidateAndCommitPlatformRelaySecretIsolationRejectsRelayPlatformDatabaseCollapseAcrossDifferentCAPaths(t *testing.T) {
	fixture := newPlatformRelaySecretIsolationTestFixture(t)
	t.Setenv(platformProcessDatabaseNameEnvironment, "relay")
	for _, contract := range platformProcessSecretRoleContracts {
		fixture.mutatePlatform(t, contract.role, func(secrets map[string]any) {
			secrets["database_url"] = strings.Replace(
				secrets["database_url"].(string), "/platform?", "/relay?", 1,
			)
		})
	}
	require.EqualError(t, ValidateAndCommitPlatformRelaySecretIsolation(),
		"Relay secret isolation found cross-domain secret reuse")
}

func TestValidateAndCommitPlatformRelaySecretIsolationRejectsInvalidDatabaseCA(t *testing.T) {
	fixture := newPlatformRelaySecretIsolationTestFixture(t)
	fixture.replace(platformRelaySecretIsolationRelayDatabaseCAEnvironment, []byte("not-a-certificate\n"))
	require.EqualError(t, ValidateAndCommitPlatformRelaySecretIsolation(),
		"Relay secret isolation inputs are invalid")
}

func TestVerifyPlatformRelaySecretIsolationReceiptRejectsDatabaseCAContentDrift(t *testing.T) {
	fixture := newPlatformRelaySecretIsolationTestFixture(t)
	require.NoError(t, ValidateAndCommitPlatformRelaySecretIsolation())
	consumer := PlatformRelaySecretIsolationConsumerAPI
	fixture.installConsumerProof(t, consumer)
	t.Setenv("SQL_DSN_FILE", os.Getenv(platformRelaySecretIsolationRuntimeDSNEnvironment))
	fixture.rawByEnvironment["SQL_DSN_FILE"] = append(
		[]byte(nil), fixture.rawByEnvironment[platformRelaySecretIsolationRuntimeDSNEnvironment]...,
	)
	fixture.replace(
		platformRelaySecretIsolationRelayDatabaseCAEnvironment,
		platformRelaySecretIsolationTestCA(t, "Attacker replacement CA"),
	)
	require.EqualError(t, VerifyPlatformRelaySecretIsolationReceipt(consumer),
		"Relay secret isolation receipt does not match mounted sources")
}

func TestValidateAndCommitPlatformRelaySecretIsolationRejectsPlatformPasswordFileDrift(t *testing.T) {
	fixture := newPlatformRelaySecretIsolationTestFixture(t)
	fixture.replace(
		"PLATFORM_API_DATABASE_PASSWORD_FILE",
		[]byte(platformRelaySecretIsolationTestPassword("platform-api-drifted")),
	)
	require.Error(t, ValidateAndCommitPlatformRelaySecretIsolation())
}

func TestValidateAndCommitPlatformRelaySecretIsolationRejectsAliasedReceiptDirectories(t *testing.T) {
	fixture := newPlatformRelaySecretIsolationTestFixture(t)
	preDirectory := fixture.directories[PlatformRelaySecretIsolationConsumerPre]
	aliasParent := filepath.Join(t.TempDir(), "receipt-volume-alias")
	if err := os.Symlink(filepath.Dir(preDirectory), aliasParent); err != nil {
		t.Skipf("directory alias is unavailable: %v", err)
	}
	aliasedDirectory := filepath.Join(aliasParent, filepath.Base(preDirectory))
	t.Setenv(
		platformRelaySecretIsolationReceiptDirectoryEnvironment(PlatformRelaySecretIsolationConsumerMigrate),
		aliasedDirectory,
	)
	require.EqualError(t, ValidateAndCommitPlatformRelaySecretIsolation(),
		"Relay secret isolation receipt directories must be distinct")
}

func TestValidateAndCommitPlatformRelaySecretIsolationPartialRunHasNoReusableCommitMarker(t *testing.T) {
	fixture := newPlatformRelaySecretIsolationTestFixture(t)
	previousHook := platformRelaySecretIsolationAfterReceiptCommit
	platformRelaySecretIsolationAfterReceiptCommit = func(committed int) error {
		if committed == 3 {
			return errors.New("simulated validator termination")
		}
		return nil
	}
	t.Cleanup(func() { platformRelaySecretIsolationAfterReceiptCommit = previousHook })
	require.EqualError(t, ValidateAndCommitPlatformRelaySecretIsolation(), "simulated validator termination")
	_, err := os.Stat(filepath.Join(fixture.commitDirectory, "receipt.json"))
	require.ErrorIs(t, err, os.ErrNotExist)

	consumer := platformRelaySecretIsolationConsumers[0]
	require.FileExists(t, filepath.Join(fixture.directories[consumer], "receipt.json"))
	if consumer == PlatformRelaySecretIsolationConsumerAPI {
		t.Setenv("SQL_DSN_FILE", os.Getenv(platformRelaySecretIsolationRuntimeDSNEnvironment))
		fixture.rawByEnvironment["SQL_DSN_FILE"] = append(
			[]byte(nil), fixture.rawByEnvironment[platformRelaySecretIsolationRuntimeDSNEnvironment]...,
		)
	}
	t.Setenv(platformRelaySecretIsolationReceiptFileEnvironment, filepath.Join(t.TempDir(), "receipt.json"))
	t.Setenv(platformRelaySecretIsolationCommitFileEnvironment, filepath.Join(t.TempDir(), "commit.json"))
	fixture.rawByEnvironment[platformRelaySecretIsolationReceiptFileEnvironment] = fixture.receipt(t, consumer)
	delete(fixture.rawByEnvironment, platformRelaySecretIsolationCommitFileEnvironment)
	require.EqualError(t, VerifyPlatformRelaySecretIsolationReceipt(consumer),
		"Relay secret isolation commit marker is unavailable")

	platformRelaySecretIsolationAfterReceiptCommit = nil
	require.NoError(t, ValidateAndCommitPlatformRelaySecretIsolation())
	require.FileExists(t, filepath.Join(fixture.commitDirectory, "receipt.json"))
}

func TestPlatformRelaySecretIsolationReleaseRejectsZeroPlatformImageDigest(t *testing.T) {
	setPlatformRelaySecretIsolationTestRelease(t)
	t.Setenv("PLATFORM_IMAGE", "registry.example.test/ai-video/platform@sha256:"+strings.Repeat("0", 64))
	_, err := platformRelaySecretIsolationReleaseIdentity()
	require.EqualError(t, err, "Relay secret isolation release identity is invalid")
}

func TestPlatformRelaySecretIsolationReleaseRequiresCanonicalEnvironmentBytes(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*testing.T)
	}{
		{"Relay image uppercase", func(t *testing.T) {
			t.Setenv("RELAY_COMPAT_IMAGE_DIGEST", strings.ToUpper(os.Getenv("RELAY_COMPAT_IMAGE_DIGEST")))
		}},
		{"Relay revision whitespace", func(t *testing.T) {
			t.Setenv("RELAY_COMPAT_SOURCE_REVISION", os.Getenv("RELAY_COMPAT_SOURCE_REVISION")+" ")
		}},
		{"Relay snapshot uppercase", func(t *testing.T) {
			t.Setenv("RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256", strings.ToUpper(os.Getenv("RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256")))
		}},
		{"Relay file count leading zero", func(t *testing.T) {
			t.Setenv("RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT", "0"+os.Getenv("RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT"))
		}},
		{"Relay upstream whitespace", func(t *testing.T) {
			t.Setenv("RELAY_COMPAT_UPSTREAM_REVISION", " "+os.Getenv("RELAY_COMPAT_UPSTREAM_REVISION"))
		}},
		{"Relay trust uppercase", func(t *testing.T) {
			t.Setenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_TRUST_KEYS_SHA256", strings.ToUpper(os.Getenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_TRUST_KEYS_SHA256")))
		}},
		{"Platform image whitespace", func(t *testing.T) {
			t.Setenv("PLATFORM_IMAGE", os.Getenv("PLATFORM_IMAGE")+" ")
		}},
		{"Platform revision uppercase", func(t *testing.T) {
			t.Setenv("PLATFORM_SOURCE_REVISION", "A"+os.Getenv("PLATFORM_SOURCE_REVISION")[1:])
		}},
		{"Platform snapshot whitespace", func(t *testing.T) {
			t.Setenv("PLATFORM_SOURCE_SNAPSHOT_SHA256", " "+os.Getenv("PLATFORM_SOURCE_SNAPSHOT_SHA256"))
		}},
		{"Unicode origin", func(t *testing.T) {
			t.Setenv(platformRelaySecretIsolationPlatformOriginEnvironment, "https://例子.example.test")
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			setPlatformRelaySecretIsolationTestRelease(t)
			test.mutate(t)
			_, err := platformRelaySecretIsolationReleaseIdentity()
			require.EqualError(t, err, "Relay secret isolation release identity is invalid")
		})
	}
}

func TestPlatformRelaySecretIsolationReleaseRejectsUnqualifiedCompiledForkRevision(t *testing.T) {
	for _, revision := range []string{strings.Repeat("0", 40), PlatformRelayUpstreamGitRevision} {
		t.Run(revision[:8], func(t *testing.T) {
			setPlatformRelaySecretIsolationTestRelease(t)
			platformRelayCompiledSourceRevision = revision
			t.Setenv("RELAY_COMPAT_SOURCE_REVISION", revision)
			_, err := platformRelaySecretIsolationReleaseIdentity()
			require.EqualError(t, err, "Relay secret isolation release identity is invalid")
		})
	}
}

func TestValidateAndCommitPlatformRelaySecretIsolationRequiresSeparatePlatformOBSIdentities(t *testing.T) {
	fields := []string{
		"huawei_obs_access_key_id",
		"huawei_obs_secret_access_key",
		"huawei_obs_security_token",
	}
	for _, field := range fields {
		t.Run(field, func(t *testing.T) {
			fixture := newPlatformRelaySecretIsolationTestFixture(t)
			var apiDocument map[string]any
			require.NoError(t, json.Unmarshal(fixture.rawByEnvironment["PLATFORM_API_RUNTIME_SECRETS_FILE"], &apiDocument))
			apiValue := apiDocument["secrets"].(map[string]any)[field]
			fixture.mutatePlatform(t, "dispatcher", func(secrets map[string]any) {
				secrets[field] = apiValue
			})
			require.EqualError(t, ValidateAndCommitPlatformRelaySecretIsolation(),
				"Relay secret isolation found cross-domain secret reuse")
		})
	}
}

func TestValidateAndCommitPlatformRelaySecretIsolationRequiresExactPublishingAdapterCredentialPairs(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(map[string]any)
	}{
		{"different credential", func(secrets map[string]any) {
			secrets["publishing_plugin_credentials"].(map[string]any)["adapters"].(map[string]any)["publishers.tiktok:create"].(map[string]any)["CLIENT_SECRET"] =
				platformRelayRuntimeSecretsTestValue("drifted-publishing-client")
		}},
		{"missing adapter", func(secrets map[string]any) {
			secrets["publishing_plugin_credentials"] = map[string]any{"adapters": map[string]any{}}
		}},
		{"missing credential", func(secrets map[string]any) {
			secrets["publishing_plugin_credentials"].(map[string]any)["adapters"].(map[string]any)["publishers.tiktok:create"] = map[string]any{}
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			fixture := newPlatformRelaySecretIsolationTestFixture(t)
			fixture.mutatePlatform(t, "platform-api", test.mutate)
			require.Error(t, ValidateAndCommitPlatformRelaySecretIsolation())
		})
	}
}

func TestValidateAndCommitPlatformRelaySecretIsolationRejectsEveryCrossServiceBindingDrift(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*testing.T, *platformRelaySecretIsolationTestFixture)
	}{
		{"new-api API key", func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
			fixture.mutatePlatform(t, "platform-api", func(secrets map[string]any) {
				secrets["relay_backends"].(map[string]any)["new-api-v1"].(map[string]any)["api_key"] =
					platformRelayRuntimeSecretsTestValue("drifted-platform-api-key")
			})
		}},
		{"tenant identity", func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
			fixture.mutatePlatform(t, "platform-api", func(secrets map[string]any) {
				secrets["relay_tenant_id"] = platformRelayRuntimeSecretsTestTenantB
			})
		}},
		{"operations digest", func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
			fixture.mutatePlatform(t, "platform-api", func(secrets map[string]any) {
				secrets["relay_operations_token"] = platformRelayRuntimeSecretsTestValue("drifted-operations-token")
			})
		}},
		{"approval secret", func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
			fixture.mutatePlatform(t, "platform-api", func(secrets map[string]any) {
				secrets["relay_reconciliation_approval_secret"] = platformRelayRuntimeSecretsTestValue("drifted-approval")
			})
		}},
		{"callback secret", func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
			fixture.mutatePlatform(t, "platform-api", func(secrets map[string]any) {
				secrets["relay_callback_signing_secrets"].(map[string]any)["new-api-v1"] =
					platformRelayRuntimeSecretsTestValue("drifted-callback")
			})
		}},
		{"Platform internal token", func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
			fixture.mutatePlatform(t, "platform-api", func(secrets map[string]any) {
				secrets["internal_service_token"] = platformRelayRuntimeSecretsTestValue("drifted-internal")
			})
		}},
		{"edge completion token", func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
			fixture.mutatePlatform(t, "platform-api", func(secrets map[string]any) {
				secrets["download_edge_completion_service_token"] = platformRelayRuntimeSecretsTestValue("drifted-edge-token")
			})
		}},
		{"gateway three-party token", func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
			fixture.mutatePlatform(t, "download-gateway-registration-worker", func(secrets map[string]any) {
				secrets["download_gateway_service_token"] = platformRelayRuntimeSecretsTestValue("drifted-gateway-token")
			})
		}},
		{"gateway binary key", func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
			fixture.mutatePlatform(t, "download-gateway-registration-worker", func(secrets map[string]any) {
				digest := sha256.Sum256([]byte("drifted-platform-attempt-key"))
				secrets["download_gateway_attempt_encryption_key_base64"] = base64.StdEncoding.EncodeToString(digest[:])
			})
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			fixture := newPlatformRelaySecretIsolationTestFixture(t)
			require.NoError(t, ValidateAndCommitPlatformRelaySecretIsolation())
			test.mutate(t, fixture)
			require.Error(t, ValidateAndCommitPlatformRelaySecretIsolation())
			for _, consumer := range platformRelaySecretIsolationConsumers {
				_, statErr := os.Stat(filepath.Join(fixture.directories[consumer], "receipt.json"))
				require.ErrorIs(t, statErr, os.ErrNotExist)
			}
		})
	}
}

func TestValidateAndCommitPlatformRelaySecretIsolationRejectsCredentialAudienceDrift(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*testing.T, *platformRelaySecretIsolationTestFixture)
	}{
		{
			name: "Platform new-api backend host",
			mutate: func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
				fixture.mutatePlatform(t, "dispatcher", func(secrets map[string]any) {
					secrets["relay_backends"].(map[string]any)["new-api-v1"].(map[string]any)["base_url"] =
						"https://attacker.example.test"
				})
			},
		},
		{
			name: "Platform new-api contract revision",
			mutate: func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
				fixture.mutatePlatform(t, "timeout-worker", func(secrets map[string]any) {
					secrets["relay_backends"].(map[string]any)["new-api-v1"].(map[string]any)["contract_revision"] =
						"generations.v2"
				})
			},
		},
		{
			name: "Relay callback host",
			mutate: func(t *testing.T, fixture *platformRelaySecretIsolationTestFixture) {
				document, err := ParsePlatformRelayAPIRuntimeSecretsFile(fixture.rawByEnvironment["RELAY_API_RUNTIME_SECRETS_FILE"])
				require.NoError(t, err)
				for index := range document.Clients {
					if document.Clients[index].CallbackURL != "" {
						document.Clients[index].CallbackURL = "https://attacker.example.test/internal/relay-callbacks/new-api-v1"
					}
				}
				raw, err := json.Marshal(document)
				require.NoError(t, err)
				fixture.replace("RELAY_API_RUNTIME_SECRETS_FILE", raw)
			},
		},
		{
			name: "provider alert host",
			mutate: func(t *testing.T, _ *platformRelaySecretIsolationTestFixture) {
				t.Setenv("RELAY_PROVIDER_ALERT_WEBHOOK_URL", "https://attacker.example.test/internal/relay/provider-alerts")
			},
		},
		{
			name: "download completion host",
			mutate: func(t *testing.T, _ *platformRelaySecretIsolationTestFixture) {
				t.Setenv("RELAY_DOWNLOAD_EDGE_PLATFORM_COMPLETION_URL", "https://attacker.example.test/internal/artifact-download-completions/edge-gateway")
			},
		},
		{
			name: "download registration host",
			mutate: func(t *testing.T, _ *platformRelaySecretIsolationTestFixture) {
				t.Setenv("DOWNLOAD_GATEWAY_REGISTRATION_URL", "https://attacker.example.test/internal/v1/download-tickets")
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			fixture := newPlatformRelaySecretIsolationTestFixture(t)
			test.mutate(t, fixture)
			require.Error(t, ValidateAndCommitPlatformRelaySecretIsolation())
		})
	}
}

func TestPlatformProcessSecretParserRejectsNonCanonicalProtectedInputs(t *testing.T) {
	setPlatformRelaySecretIsolationTestRelease(t)
	bindings := platformRelaySecretIsolationTestBindingsValue(t)
	valid := bindings.platform["platform-api"]
	_, _, err := platformProcessSecretParse(valid, "platform-api")
	require.NoError(t, err)

	mutate := func(edit func(map[string]any)) []byte {
		var document map[string]any
		require.NoError(t, json.Unmarshal(valid, &document))
		edit(document)
		raw, marshalErr := json.Marshal(document)
		require.NoError(t, marshalErr)
		return raw
	}
	for _, raw := range [][]byte{
		append([]byte(" "), valid...),
		[]byte(strings.Replace(string(valid), `"schema_version":1`, `"schema_version":1,"schema_version":1`, 1)),
		mutate(func(document map[string]any) { document["unknown"] = true }),
		mutate(func(document map[string]any) { document["process_role"] = "dispatcher" }),
		mutate(func(document map[string]any) {
			document["secrets"].(map[string]any)["relay_tenant_id"] = "00000000-0000-0000-0000-000000000000"
		}),
		mutate(func(document map[string]any) {
			document["secrets"].(map[string]any)["database_url"] =
				platformRelaySecretIsolationTestPlatformDSN("platform-api", "platform_api") + "&password=override"
		}),
		mutate(func(document map[string]any) {
			document["secrets"].(map[string]any)["jwt_signing_secret"] = strings.Repeat("A", 32)
		}),
		mutate(func(document map[string]any) {
			document["secrets"].(map[string]any)["jwt_signing_secret"] = strings.Repeat("A1b2C3d4", 4)
		}),
		mutate(func(document map[string]any) {
			document["secrets"].(map[string]any)["jwt_signing_secret"] = "test-" + platformRelayRuntimeSecretsTestValue("otherwise-diverse")
		}),
		mutate(func(document map[string]any) {
			document["secrets"].(map[string]any)["huawei_obs_access_key_id"] = "AKID PLATFORM WITH SPACE"
		}),
		mutate(func(document map[string]any) {
			document["secrets"].(map[string]any)["publishing_plugin_credentials"] = map[string]any{
				"adapters": map[string]any{
					"a" + strings.Repeat("b", 254) + ":f": map[string]any{
						"CLIENT_SECRET": platformRelayRuntimeSecretsTestValue("plugin-too-long"),
					},
				},
			}
		}),
	} {
		_, _, err := platformProcessSecretParse(raw, "platform-api")
		require.EqualError(t, err, "Platform process runtime secret file is invalid")
	}

	maximumSpec := "a" + strings.Repeat("b", 253) + ":f"
	maximumSpecDocument := mutate(func(document map[string]any) {
		document["secrets"].(map[string]any)["publishing_plugin_credentials"] = map[string]any{
			"adapters": map[string]any{maximumSpec: map[string]any{
				"CLIENT_SECRET": platformRelayRuntimeSecretsTestValue("maximum-plugin-spec"),
			}},
		}
	})
	_, _, err = platformProcessSecretParse(maximumSpecDocument, "platform-api")
	require.NoError(t, err)
}

func TestPlatformProcessDatabasePasswordContract(t *testing.T) {
	t.Setenv(platformRelaySecretIsolationPlatformDatabaseCAEnvironment, filepath.Join(t.TempDir(), "platform-database-ca.pem"))
	t.Setenv(platformProcessDatabaseNameEnvironment, "platform")
	contract, ok := platformProcessSecretContract("platform-api")
	require.True(t, ok)
	validDSN := platformRelaySecretIsolationTestPlatformDSN(contract.role, contract.databaseUser)
	parsedValidDSN, err := url.Parse(validDSN)
	require.NoError(t, err)
	validPassword, present := parsedValidDSN.User.Password()
	require.True(t, present)
	require.True(t, platformProcessDatabasePasswordPattern.MatchString(validPassword))
	require.True(t, platformProcessSecretValid(validPassword))
	_, err = platformRelaySecretIsolationDatabaseTarget(parsedValidDSN, "")
	require.NoError(t, err)
	password, _, valid := platformProcessSecretDatabasePassword(validDSN, contract.databaseUser)
	require.True(t, valid)
	require.NotEmpty(t, password)
	clear(password)

	withPassword := func(value string) string {
		parsed, err := url.Parse(validDSN)
		require.NoError(t, err)
		parsed.User = url.UserPassword(contract.databaseUser, value)
		return parsed.String()
	}
	for name, value := range map[string]string{
		"too-short":        "Abcdefghijklmnopqrstuvwxyz01234",
		"too-long":         "Abcdefghijklmnopqrstuvwxyz0123456789_" + strings.Repeat("q", 100),
		"non-base64url":    "Abcdefghijklmnopqrstuvwxyz012345.",
		"short-period":     strings.Repeat("A1b2C3d4", 4),
		"placeholder":      "test-Abcdefghijklmnopqrstuvwxyz0123456789",
		"unicode-password": "Abcdefghijklmnopqrstuvwxyz012345é",
	} {
		t.Run(name, func(t *testing.T) {
			password, _, valid := platformProcessSecretDatabasePassword(
				withPassword(value), contract.databaseUser,
			)
			clear(password)
			require.False(t, valid)
			_, err := platformProcessSecretPasswordFile([]byte(value), contract)
			require.EqualError(t, err, "Platform database password source is invalid")
		})
	}
}

func TestVerifyPlatformRelaySecretIsolationReceiptBindsCurrentFileBytes(t *testing.T) {
	fixture := newPlatformRelaySecretIsolationTestFixture(t)
	require.NoError(t, ValidateAndCommitPlatformRelaySecretIsolation())

	t.Setenv("SQL_DSN_FILE", os.Getenv(platformRelaySecretIsolationRuntimeDSNEnvironment))
	fixture.rawByEnvironment["SQL_DSN_FILE"] = append([]byte(nil), fixture.rawByEnvironment[platformRelaySecretIsolationRuntimeDSNEnvironment]...)
	fixture.installConsumerProof(t, PlatformRelaySecretIsolationConsumerAPI)
	require.NoError(t, VerifyPlatformRelaySecretIsolationReceipt(PlatformRelaySecretIsolationConsumerAPI))

	document, err := ParsePlatformRelayAPIRuntimeSecretsFile(fixture.rawByEnvironment["RELAY_API_RUNTIME_SECRETS_FILE"])
	require.NoError(t, err)
	document.Application.SessionSecret = platformRelayRuntimeSecretsTestValue("rotated-session")
	mutated, err := json.Marshal(document)
	require.NoError(t, err)
	fixture.replace("RELAY_API_RUNTIME_SECRETS_FILE", mutated)
	fixture.replace(
		PlatformRelayRedisTLSCAFileEnvironment,
		platformRelaySecretIsolationTestCA(t, "Host-swapped Relay Redis CA"),
	)
	require.EqualError(t,
		VerifyPlatformRelaySecretIsolationReceipt(PlatformRelaySecretIsolationConsumerAPI),
		"Relay secret isolation receipt does not match mounted sources",
	)
}

func TestVerifyPlatformRelaySecretIsolationReceiptPinsTheVerifiedBytesForUse(t *testing.T) {
	fixture := newPlatformRelaySecretIsolationTestFixture(t)
	require.NoError(t, ValidateAndCommitPlatformRelaySecretIsolation())

	t.Setenv("SQL_DSN_FILE", os.Getenv(platformRelaySecretIsolationRuntimeDSNEnvironment))
	fixture.rawByEnvironment["SQL_DSN_FILE"] = append([]byte(nil), fixture.rawByEnvironment[platformRelaySecretIsolationRuntimeDSNEnvironment]...)
	fixture.installConsumerProof(t, PlatformRelaySecretIsolationConsumerAPI)
	require.NoError(t, VerifyPlatformRelaySecretIsolationReceipt(PlatformRelaySecretIsolationConsumerAPI))
	originalRedisCA := append([]byte(nil), fixture.rawByEnvironment[PlatformRelayRedisTLSCAFileEnvironment]...)
	defer clear(originalRedisCA)

	original := platformRelayRuntimeSecretsTestDocument()
	mutatedDocument := original
	mutatedDocument.Application.SessionSecret = platformRelayRuntimeSecretsTestValue("host-swapped-session")
	mutated, err := json.Marshal(mutatedDocument)
	require.NoError(t, err)
	fixture.replace("RELAY_API_RUNTIME_SECRETS_FILE", mutated)
	fixture.replace(
		PlatformRelayRedisTLSCAFileEnvironment,
		platformRelaySecretIsolationTestCA(t, "Host-swapped pinned Relay Redis CA"),
	)

	// The real consumer reader must consume the verified in-memory snapshot,
	// not reopen the source after receipt verification. The fixture never
	// creates a file at this path, which makes a second open deterministic.
	usedRaw, err := common.ReadProtectedSecretFile("RELAY_API_RUNTIME_SECRETS_FILE", 1024*1024)
	require.NoError(t, err)
	used, err := ParsePlatformRelayAPIRuntimeSecretsFile(usedRaw)
	clear(usedRaw)
	require.NoError(t, err)
	require.Equal(t, original.Application.SessionSecret, used.Application.SessionSecret)
	require.NotEqual(t, mutatedDocument.Application.SessionSecret, used.Application.SessionSecret)
	usedRedisCA, err := common.ReadProtectedSecretFile(
		PlatformRelayRedisTLSCAFileEnvironment, common.ProtectedRelayRedisTLSCAMaximumBytes,
	)
	require.NoError(t, err)
	defer clear(usedRedisCA)
	require.Equal(t, originalRedisCA, usedRedisCA)
}
