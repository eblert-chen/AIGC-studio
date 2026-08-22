package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/service"
	"github.com/stretchr/testify/require"
)

func TestPlatformRelayPrincipalRotationOfflineCommandsAreRegistered(t *testing.T) {
	for _, command := range []string{
		platformRelayPrincipalRotationIsolationCommand,
		platformRelayPrincipalRotationCommand,
	} {
		t.Run(command, func(t *testing.T) {
			require.True(t, platformRelayOfflineCommandRequested([]string{"/new-api", command}))
			var output bytes.Buffer
			handled, err := handlePlatformRelayOfflineCommand(
				[]string{"/new-api", command, "unexpected"},
				&output,
			)
			require.True(t, handled)
			require.EqualError(t, err, command+" does not accept arguments")
			require.Empty(t, output.String())
		})
	}
}

func TestPlatformRelayPrincipalRotationRejectsRawAndUnrelatedSecretSources(t *testing.T) {
	t.Run("raw secret", func(t *testing.T) {
		t.Setenv("RELAY_CURRENT_SERVICE_PRINCIPALS_JSON", "present-even-when-not-a-valid-secret")
		require.ErrorContains(t, validatePlatformRelayPrincipalRotationNoRawSecrets(), "file-only")
	})

	t.Run("Platform raw secret casefold present even empty", func(t *testing.T) {
		t.Setenv("jwt_signing_secret", "")
		require.ErrorContains(t, validatePlatformRelayPrincipalRotationNoRawSecrets(), "file-only")
	})

	t.Run("Platform database raw secret", func(t *testing.T) {
		t.Setenv("DATABASE_URL", "")
		require.ErrorContains(t, validatePlatformRelayPrincipalRotationNoRawSecrets(), "file-only")
	})

	t.Run("libpq raw secret namespace", func(t *testing.T) {
		t.Setenv("PgPaSsWoRd", "")
		require.ErrorContains(t, validatePlatformRelayPrincipalRotationNoRawSecrets(), "file-only")
	})

	t.Run("unrelated file", func(t *testing.T) {
		t.Setenv("RELAY_API_RUNTIME_SECRETS_FILE", "/run/secrets/unrelated.json")
		require.ErrorContains(t, validatePlatformRelayPrincipalRotationLeastPrivilegeFiles(), "unrelated secret file")
	})

	t.Run("other receipt directory", func(t *testing.T) {
		t.Setenv("RELAY_SECRET_ISOLATION_RECEIPT_API_DIRECTORY", "/run/receipts/api")
		require.ErrorContains(t, validatePlatformRelayPrincipalRotationLeastPrivilegeFiles(), "unrelated receipt source")
	})

	t.Run("future protected file", func(t *testing.T) {
		t.Setenv("PLATFORM_FUTURE_WORKER_RUNTIME_SECRETS_FILE", "/run/secrets/future.json")
		require.ErrorContains(t, validatePlatformRelayPrincipalRotationLeastPrivilegeFiles(), "unrelated secret file")
	})

	t.Run("casefolded unrelated protected file", func(t *testing.T) {
		t.Setenv("platform_api_runtime_secrets_file", "/run/secrets/unrelated.json")
		require.ErrorContains(t, validatePlatformRelayPrincipalRotationLeastPrivilegeFiles(), "unrelated secret file")
	})

	t.Run("relay CA is the only additional database file", func(t *testing.T) {
		t.Setenv("RELAY_DATABASE_CA_FILE", "/run/secrets/relay-database-ca.pem")
		require.NoError(t, validatePlatformRelayPrincipalRotationLeastPrivilegeFiles())
	})

	t.Run("database release proof is required release state", func(t *testing.T) {
		t.Setenv(service.RelayDatabaseReleaseProofFileEnvironment, "/run/relay-database-release/receipt.json")
		require.NoError(t, validatePlatformRelayPrincipalRotationLeastPrivilegeFiles())
	})

	t.Run("Platform CA remains validator only", func(t *testing.T) {
		t.Setenv("PLATFORM_DATABASE_CA_FILE", "/run/secrets/platform-database-ca.pem")
		require.ErrorContains(t, validatePlatformRelayPrincipalRotationLeastPrivilegeFiles(), "unrelated secret file")
	})
}

func TestClearPlatformRelayPrincipalRotationInputsDropsBothTokenSets(t *testing.T) {
	inputs := service.PlatformRelayServicePrincipalRotationInputs{
		AttemptID: strings.Repeat("a", 64),
		Current: []service.PlatformRelayServicePrincipalProvisionInput{{
			ClientID: "client-a", TenantID: "00000000-0000-4000-8000-000000000001",
			UpstreamToken: "sk-current-token-canary",
		}},
		Desired: []service.PlatformRelayServicePrincipalProvisionInput{{
			ClientID: "client-a", TenantID: "00000000-0000-4000-8000-000000000001",
			UpstreamToken: "sk-desired-token-canary",
		}},
	}
	clearPlatformRelayPrincipalRotationInputs(&inputs)
	require.Empty(t, inputs.AttemptID)
	require.Nil(t, inputs.Current)
	require.Nil(t, inputs.Desired)
}

func TestPlatformRelayPrincipalRotationOutputIsOneSecretFreeJSONReceipt(t *testing.T) {
	var output bytes.Buffer
	require.NoError(t, writePlatformRelayServicePrincipalRotationOutput(
		&output,
		service.PlatformRelayServicePrincipalRotationResult{
			AttemptID: strings.Repeat("d", 64),
			State:     service.PlatformRelayServicePrincipalRotationStateRotated,
			Count:     2, RotatedCount: 2,
		},
	))
	decoder := json.NewDecoder(bytes.NewReader(output.Bytes()))
	var receipt platformRelayServicePrincipalRotationOutput
	require.NoError(t, decoder.Decode(&receipt))
	require.ErrorIs(t, decoder.Decode(&struct{}{}), io.EOF)
	require.Equal(t, strings.Repeat("d", 64), receipt.AttemptID)
	require.Equal(t, "token_rotation_only", receipt.CredentialOperation)
	require.Equal(t, "immutable", receipt.IdentitySet)

	for _, token := range []string{
		"sk-A1b2C3d4E5f6G7h8J9k0L1m2N3p4Q5r6S7t8U9v0W1x2Y3z4",
		"sk-C7d8E9f0G1h2J3k4L5m6N7p8Q9r0S1t2U3v4W5x6Y7z8A9b0",
	} {
		bare := strings.TrimPrefix(token, "sk-")
		canonicalDigest := sha256.Sum256([]byte(token))
		bareDigest := sha256.Sum256([]byte(bare))
		for _, forbidden := range []string{
			token, bare, hex.EncodeToString(canonicalDigest[:]), hex.EncodeToString(bareDigest[:]),
		} {
			if strings.Contains(output.String(), forbidden) {
				t.Fatal("rotation stdout exposes credential material or a credential digest")
			}
		}
	}
}
