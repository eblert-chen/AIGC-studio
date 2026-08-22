package common

import (
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestProtectedRawSecretEnvironmentManifestIsCaseFoldedAndPresentEvenEmpty(t *testing.T) {
	for _, name := range append(
		ProtectedPlatformRawSecretEnvironmentNamesV1(),
		ProtectedRelayRawSecretEnvironmentNamesV1()...,
	) {
		require.True(t, ProtectedRawSecretEnvironmentPresent([]string{strings.ToLower(name) + "="}))
		require.True(t, ProtectedRawSecretEnvironmentPresent([]string{strings.ToUpper(name) + "=value"}))
	}
	for _, name := range []string{"PGOPTIONS", "pgsslkey", "PgSeRvIcE", "pGpAsSwOrD"} {
		require.True(t, ProtectedRawSecretEnvironmentPresent([]string{name + "="}))
	}
	require.False(t, ProtectedRawSecretEnvironmentPresent([]string{
		"PATH=/usr/bin",
		"RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED=true",
		"RELAY_SECRET_ISOLATION_RECEIPT_FILE=/run/receipts/receipt.json",
	}))
}
