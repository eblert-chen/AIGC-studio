package main

import (
	"os"
	"testing"

	"github.com/stretchr/testify/require"
)

func setPlatformRelayRuntimeEnvironment(
	t *testing.T,
	appEnvironment string,
	deploymentEnvironment string,
	compatEnvironment string,
	outerEnvironment string,
	roleAttestation string,
) {
	t.Helper()
	t.Setenv("APP_ENV", appEnvironment)
	t.Setenv("DEPLOYMENT_ENV", deploymentEnvironment)
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", compatEnvironment)
	t.Setenv("ENVIRONMENT", outerEnvironment)
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", roleAttestation)
	t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_COMPAT_ENABLED", "true")
	t.Setenv("RELAY_COMPAT_WORKER_ENABLED", "true")
	t.Setenv("DEBUG", "false")
	t.Setenv("DIFY_DEBUG", "false")
	t.Setenv("GIN_MODE", "release")
}

func TestValidatePlatformRelayProtectedRuntimeEnvironmentRequiresNativeGenerationDataPlane(t *testing.T) {
	for _, test := range []struct {
		name     string
		variable string
		value    *string
	}{
		{name: "generation admission is missing", variable: "RELAY_COMPAT_ENABLED"},
		{name: "generation admission is disabled", variable: "RELAY_COMPAT_ENABLED", value: func() *string { value := "false"; return &value }()},
		{name: "generation admission is non-canonical", variable: "RELAY_COMPAT_ENABLED", value: func() *string { value := "TRUE"; return &value }()},
		{name: "generation worker is missing", variable: "RELAY_COMPAT_WORKER_ENABLED"},
		{name: "generation worker is disabled", variable: "RELAY_COMPAT_WORKER_ENABLED", value: func() *string { value := "false"; return &value }()},
		{name: "generation worker is non-canonical", variable: "RELAY_COMPAT_WORKER_ENABLED", value: func() *string { value := " true "; return &value }()},
	} {
		t.Run(test.name, func(t *testing.T) {
			setPlatformRelayRuntimeEnvironment(t, "staging", "staging", "staging", "staging", "true")
			if test.value == nil {
				require.NoError(t, os.Unsetenv(test.variable))
			} else {
				t.Setenv(test.variable, *test.value)
			}

			protected, err := validatePlatformRelayProtectedRuntimeEnvironment()
			require.True(t, protected)
			require.ErrorContains(t, err, "protected Relay runtime requires")
		})
	}
}

func TestValidatePlatformRelayProtectedRuntimeEnvironment(t *testing.T) {
	tests := []struct {
		name       string
		app        string
		deployment string
		compat     string
		outer      string
		attest     string
		protected  bool
		wantError  bool
	}{
		{name: "development remains unprotected", app: "development", deployment: "development", compat: "development"},
		{name: "attested development is rejected", app: "development", deployment: "development", compat: "development", attest: "true", protected: true, wantError: true},
		{name: "protected staging is exact", app: "staging", deployment: "staging", compat: "staging", outer: "staging", attest: "true", protected: true},
		{name: "production requires exact TLS mode", app: "production", deployment: "production", compat: "production", outer: "production", protected: true},
		{name: "mismatched protected environments are rejected", app: "staging", deployment: "production", compat: "staging", attest: "true", protected: true, wantError: true},
		{name: "outer production cannot downgrade", app: "development", deployment: "development", compat: "development", outer: "production", protected: true, wantError: true},
		{name: "invalid attestation flag is rejected", app: "staging", deployment: "staging", compat: "staging", attest: "sometimes", protected: true, wantError: true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			setPlatformRelayRuntimeEnvironment(t, test.app, test.deployment, test.compat, test.outer, test.attest)
			protected, err := validatePlatformRelayProtectedRuntimeEnvironment()
			require.Equal(t, test.protected, protected)
			if test.wantError {
				require.Error(t, err)
			} else {
				require.NoError(t, err)
			}
		})
	}
}

func TestValidatePlatformRelayProtectedRuntimeEnvironmentRejectsTLSModeDowngrade(t *testing.T) {
	for _, test := range []struct {
		name  string
		value *string
	}{
		{name: "missing"},
		{name: "false", value: func() *string { value := "false"; return &value }()},
		{name: "empty", value: func() *string { value := ""; return &value }()},
	} {
		t.Run(test.name, func(t *testing.T) {
			setPlatformRelayRuntimeEnvironment(t, "staging", "staging", "staging", "staging", "true")
			if test.value == nil {
				require.NoError(t, os.Unsetenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED"))
			} else {
				t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", *test.value)
			}
			protected, err := validatePlatformRelayProtectedRuntimeEnvironment()
			require.True(t, protected)
			require.Error(t, err)
		})
	}
}

func TestValidatePlatformRelayProtectedRuntimeEnvironmentRejectsAmbientPostgresConfiguration(t *testing.T) {
	for _, environment := range []string{"PGPASSWORD", "pgpassfile", "PgService", "PGOPTIONS", "PGSSLKEY"} {
		t.Run(environment, func(t *testing.T) {
			setPlatformRelayRuntimeEnvironment(t, "staging", "staging", "staging", "staging", "true")
			t.Setenv(environment, "")
			protected, err := validatePlatformRelayProtectedRuntimeEnvironment()
			require.True(t, protected)
			require.ErrorContains(t, err, "forbidden raw secret environment variable")
		})
	}
}

func TestInstallPlatformRelayProtectedRuntimeSecretsRejectsLegacyRawEnvironmentBeforeFiles(t *testing.T) {
	for _, forbidden := range []string{
		"REDIS_CONN_STRING", "RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON",
		"RELAY_DOWNLOAD_EDGE_RUNTIME_SECRETS_FILE", "RELAY_COMPAT_INTERNAL_BASE_URL",
		"relay_artifact_signing_secret",
	} {
		t.Run(forbidden, func(t *testing.T) {
			setPlatformRelayRuntimeEnvironment(t, "staging", "staging", "staging", "staging", "true")
			t.Setenv(forbidden, "")
			t.Setenv(platformRelayServicePrincipalsFileEnvironment, "not-an-absolute-path")
			t.Setenv(platformRelayAPIRuntimeSecretsFileEnvironment, "not-an-absolute-path")
			protected, err := installPlatformRelayProtectedRuntimeSecrets()
			require.True(t, protected)
			require.ErrorContains(t, err, "raw secret environment variable")
		})
	}
}

func TestInstallPlatformRelayProtectedRuntimeSecretsRejectsDisabledNativeDataPlaneBeforeFiles(t *testing.T) {
	setPlatformRelayRuntimeEnvironment(t, "production", "production", "production", "production", "true")
	t.Setenv("RELAY_COMPAT_ENABLED", "false")
	t.Setenv(platformRelayServicePrincipalsFileEnvironment, "not-an-absolute-path")
	t.Setenv(platformRelayAPIRuntimeSecretsFileEnvironment, "not-an-absolute-path")

	protected, err := installPlatformRelayProtectedRuntimeSecrets()
	require.True(t, protected)
	require.EqualError(t, err, "protected Relay runtime requires RELAY_COMPAT_ENABLED=true")
}

func TestInstallPlatformRelayProtectedRuntimeSecretsRejectsDebugBeforeFiles(t *testing.T) {
	setPlatformRelayRuntimeEnvironment(t, "staging", "staging", "staging", "staging", "true")
	t.Setenv("DEBUG", "true")
	t.Setenv(platformRelayServicePrincipalsFileEnvironment, "not-an-absolute-path")
	t.Setenv(platformRelayAPIRuntimeSecretsFileEnvironment, "not-an-absolute-path")
	protected, err := installPlatformRelayProtectedRuntimeSecrets()
	require.True(t, protected)
	require.EqualError(t, err, "protected Relay runtime requires non-debug logging")
}
