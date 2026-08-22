package service

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"os/exec"
	"sort"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/stretchr/testify/require"
)

const (
	platformRelayRuntimeSecretsTestTenantA = "00000000-0000-4000-8000-000000000011"
	platformRelayRuntimeSecretsTestTenantB = "00000000-0000-4000-8000-000000000012"
)

func platformRelayRuntimeSecretsTestValue(label string) string {
	digest := sha256.Sum256([]byte("relay-runtime-secret-test:" + label))
	return label + "-" + hex.EncodeToString(digest[:])
}

func platformRelayRuntimeSecretsTestToken(label string) string {
	alphabet := "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
	digest := sha256.Sum256([]byte("relay-service-token-test:" + label))
	var body strings.Builder
	for index := 0; index < 48; index++ {
		body.WriteByte(alphabet[(int(digest[index%len(digest)])+index)%len(alphabet)])
	}
	return "sk-" + body.String()
}

func platformRelayRuntimeSecretsTestRedisTLSCA(t *testing.T) common.ProtectedRelayRedisTLSCA {
	t.Helper()
	raw := platformRelaySecretIsolationTestCA(t, "Relay runtime Redis test CA")
	defer clear(raw)
	value, err := common.ParseProtectedRelayRedisTLSCA(raw)
	require.NoError(t, err)
	return value
}

func platformRelayRuntimeSecretsTestDocument() PlatformRelayAPIRuntimeSecretsFile {
	operations := []platformGenerationOperationsCredential{
		{TenantID: platformRelayRuntimeSecretsTestTenantA, TokenSHA256: hex.EncodeToString(sha256.New().Sum(nil))},
		{TenantID: platformRelayRuntimeSecretsTestTenantA, TokenSHA256: hex.EncodeToString(func() []byte {
			digest := sha256.Sum256([]byte("second-operations-token"))
			return digest[:]
		}())},
		{TenantID: platformRelayRuntimeSecretsTestTenantB, TokenSHA256: hex.EncodeToString(func() []byte {
			digest := sha256.Sum256([]byte("third-operations-token"))
			return digest[:]
		}())},
	}
	sort.Slice(operations, func(left, right int) bool {
		leftIdentity := operations[left].TenantID + "\x00" + operations[left].TokenSHA256
		rightIdentity := operations[right].TenantID + "\x00" + operations[right].TokenSHA256
		return leftIdentity < rightIdentity
	})
	return PlatformRelayAPIRuntimeSecretsFile{
		Kind: PlatformRelayAPIRuntimeSecretsFileKind, SchemaVersion: PlatformRelayAPIRuntimeSecretsFileSchemaVersion,
		RedisDSN: "rediss://:" + platformRelayRuntimeSecretsTestValue("redis") + "@redis.example.test:6380/0",
		Application: PlatformRelayAPIRuntimeApplicationSecrets{
			SessionSecret: platformRelayRuntimeSecretsTestValue("session"),
			CryptoSecret:  platformRelayRuntimeSecretsTestValue("crypto"),
		},
		Clients: []PlatformRelayAPIRuntimeClientSecret{
			{ClientID: "client-a", TenantID: platformRelayRuntimeSecretsTestTenantA, APIKey: platformRelayRuntimeSecretsTestValue("api-a")},
			{ClientID: "client-b", TenantID: platformRelayRuntimeSecretsTestTenantA, APIKey: platformRelayRuntimeSecretsTestValue("api-b")},
			{ClientID: "client-c", TenantID: platformRelayRuntimeSecretsTestTenantB, APIKey: platformRelayRuntimeSecretsTestValue("api-c"), CallbackURL: "https://platform.example.test/callback", CallbackSigningSecret: platformRelayRuntimeSecretsTestValue("callback-c")},
		},
		OperationsCredentials: operations,
		ReconciliationApprovalKeys: []platformGenerationReconciliationApprovalKey{
			{TenantID: platformRelayRuntimeSecretsTestTenantA, KeyID: "approval-a", Secret: platformRelayRuntimeSecretsTestValue("approval-a")},
			{TenantID: platformRelayRuntimeSecretsTestTenantA, KeyID: "approval-b", Secret: platformRelayRuntimeSecretsTestValue("approval-b")},
			{TenantID: platformRelayRuntimeSecretsTestTenantB, KeyID: "approval-c", Secret: platformRelayRuntimeSecretsTestValue("approval-c")},
		},
		InternalAdmissionToken: platformRelayRuntimeSecretsTestValue("internal-admission"),
		ArtifactSigningSecret:  platformRelayRuntimeSecretsTestValue("artifact"),
		HuaweiOBS: PlatformRelayAPIRuntimeOBSSecrets{
			AccessKeyID: platformRelayRuntimeSecretsTestValue("obs-access"), SecretAccessKey: platformRelayRuntimeSecretsTestValue("obs-secret"), SecurityToken: platformRelayRuntimeSecretsTestValue("obs-token"),
		},
		ProviderAlertSigningSecret: platformRelayRuntimeSecretsTestValue("provider-alert"),
		PlatformInternalToken:      platformRelayRuntimeSecretsTestValue("platform-internal"),
		ChannelCostSigningSecret:   platformRelayRuntimeSecretsTestValue("channel-cost"),
		TelemetrySigningSecret:     platformRelayRuntimeSecretsTestValue("telemetry"),
	}
}

func platformRelayRuntimeSecretsTestPrincipals() []PlatformRelayServicePrincipalProvisionInput {
	return []PlatformRelayServicePrincipalProvisionInput{
		{ClientID: "client-a", TenantID: platformRelayRuntimeSecretsTestTenantA, UpstreamToken: platformRelayRuntimeSecretsTestToken("client-a")},
		{ClientID: "client-b", TenantID: platformRelayRuntimeSecretsTestTenantA, UpstreamToken: platformRelayRuntimeSecretsTestToken("client-b")},
		{ClientID: "client-c", TenantID: platformRelayRuntimeSecretsTestTenantB, UpstreamToken: platformRelayRuntimeSecretsTestToken("client-c")},
	}
}

func parsePlatformRelayRuntimeSecretsTestDocument(t *testing.T, document PlatformRelayAPIRuntimeSecretsFile) error {
	t.Helper()
	raw, err := json.Marshal(document)
	require.NoError(t, err)
	defer clear(raw)
	_, err = ParsePlatformRelayAPIRuntimeSecretsFile(raw)
	return err
}

func TestParsePlatformRelayAPIRuntimeSecretsAllowsCanonicalMultiCredentialTenants(t *testing.T) {
	document := platformRelayRuntimeSecretsTestDocument()
	require.NoError(t, parsePlatformRelayRuntimeSecretsTestDocument(t, document))
}

func TestParsePlatformRelayAPIRuntimeSecretsRejectsNonCanonicalAndReusedSecrets(t *testing.T) {
	tests := map[string]func(*PlatformRelayAPIRuntimeSecretsFile){
		"Redis fragment": func(document *PlatformRelayAPIRuntimeSecretsFile) { document.RedisDSN += "#fragment" },
		"Redis query":    func(document *PlatformRelayAPIRuntimeSecretsFile) { document.RedisDSN += "?db=0" },
		"Redis decoded control": func(document *PlatformRelayAPIRuntimeSecretsFile) {
			document.RedisDSN = "rediss://:AbCdEf0123456789%0Azyxwvutsrqponmlk@redis.example.test:6380/0"
		},
		"embedded control": func(document *PlatformRelayAPIRuntimeSecretsFile) {
			document.InternalAdmissionToken = "AbCdEf0123456789\nzyxwvutsrqponmlk9876543210"
		},
		"unsorted operations": func(document *PlatformRelayAPIRuntimeSecretsFile) {
			document.OperationsCredentials[0], document.OperationsCredentials[1] = document.OperationsCredentials[1], document.OperationsCredentials[0]
		},
		"duplicate approval identity": func(document *PlatformRelayAPIRuntimeSecretsFile) {
			document.ReconciliationApprovalKeys[1].KeyID = document.ReconciliationApprovalKeys[0].KeyID
		},
		"nil client tenant": func(document *PlatformRelayAPIRuntimeSecretsFile) {
			document.Clients[0].TenantID = "00000000-0000-0000-0000-000000000000"
		},
		"nil operations tenant": func(document *PlatformRelayAPIRuntimeSecretsFile) {
			document.OperationsCredentials[0].TenantID = "00000000-0000-0000-0000-000000000000"
		},
		"nil approval tenant": func(document *PlatformRelayAPIRuntimeSecretsFile) {
			document.ReconciliationApprovalKeys[0].TenantID = "00000000-0000-0000-0000-000000000000"
		},
		"cross-domain secret reuse": func(document *PlatformRelayAPIRuntimeSecretsFile) {
			document.TelemetrySigningSecret = document.Application.SessionSecret
		},
	}
	for name, mutate := range tests {
		t.Run(name, func(t *testing.T) {
			document := platformRelayRuntimeSecretsTestDocument()
			mutate(&document)
			require.Error(t, parsePlatformRelayRuntimeSecretsTestDocument(t, document))
		})
	}
}

func TestInstallPlatformRelayAPIRuntimeSecretsIsAtomicAndImmutable(t *testing.T) {
	if os.Getenv("RELAY_RUNTIME_SECRETS_INSTALL_HELPER") == "1" {
		principals := platformRelayRuntimeSecretsTestPrincipals()
		document := platformRelayRuntimeSecretsTestDocument()
		redisTLSCA := platformRelayRuntimeSecretsTestRedisTLSCA(t)
		require.NoError(t, InstallPlatformRelayAPIRuntimeSecrets(principals, document, redisTLSCA))
		require.NoError(t, InstallPlatformRelayAPIRuntimeSecrets(principals, document, redisTLSCA))
		require.Error(t, InstallPlatformRelayAPIRuntimeSecrets(
			principals, document, platformRelayRuntimeSecretsTestRedisTLSCA(t),
		))
		document.TelemetrySigningSecret = platformRelayRuntimeSecretsTestValue("rotated-telemetry")
		require.Error(t, InstallPlatformRelayAPIRuntimeSecrets(principals, document, redisTLSCA))
		installed, _, ok := platformRelayAPIRuntimeCredentials()
		require.True(t, ok)
		require.Equal(t, platformRelayRuntimeSecretsTestValue("api-a"), installed["client-a"].APIKey)
		return
	}
	command := exec.Command(os.Args[0], "-test.run=^TestInstallPlatformRelayAPIRuntimeSecretsIsAtomicAndImmutable$")
	command.Env = append(os.Environ(), "RELAY_RUNTIME_SECRETS_INSTALL_HELPER=1")
	output, err := command.CombinedOutput()
	require.NoError(t, err, string(output))
}

func TestInstallPlatformRelayAPIRuntimeSecretsRejectsPrincipalSecretReuse(t *testing.T) {
	if os.Getenv("RELAY_RUNTIME_SECRETS_REUSE_HELPER") == "1" {
		principals := platformRelayRuntimeSecretsTestPrincipals()
		document := platformRelayRuntimeSecretsTestDocument()
		document.TelemetrySigningSecret = strings.TrimPrefix(principals[0].UpstreamToken, "sk-")
		require.Error(t, InstallPlatformRelayAPIRuntimeSecrets(
			principals, document, platformRelayRuntimeSecretsTestRedisTLSCA(t),
		))
		_, _, installed := platformRelayAPIRuntimeCredentials()
		require.False(t, installed)
		return
	}
	command := exec.Command(os.Args[0], "-test.run=^TestInstallPlatformRelayAPIRuntimeSecretsRejectsPrincipalSecretReuse$")
	command.Env = append(os.Environ(), "RELAY_RUNTIME_SECRETS_REUSE_HELPER=1")
	output, err := command.CombinedOutput()
	require.NoError(t, err, string(output))
}
