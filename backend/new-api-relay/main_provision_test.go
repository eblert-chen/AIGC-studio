package main

import (
	"bytes"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/service"
	"github.com/stretchr/testify/require"
)

const platformRelayRootTestPassword = "correct-horse-battery-staple-root-01"

func setPlatformRelayTestDSNFile(t *testing.T, dsn string) string {
	t.Helper()
	dsnFile := filepath.Join(t.TempDir(), "runtime-dsn")
	require.NoError(t, os.WriteFile(dsnFile, []byte(dsn), 0o400))
	t.Setenv("SQL_DSN", "")
	os.Unsetenv("SQL_DSN")
	t.Setenv("SQL_DSN_FILE", dsnFile)
	if dsn != "" {
		require.NoError(t, common.InstallProtectedSecretFileSnapshots([]common.ProtectedSecretFileSnapshot{{
			Environment: "SQL_DSN_FILE",
			Value:       []byte(dsn),
		}}))
	}
	return dsnFile
}

func setValidPlatformRelayRootProvisionEnvironment(t *testing.T, password string) string {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("protected secret file mode is a Linux container contract")
	}
	passwordFile := filepath.Join(t.TempDir(), "root-password")
	require.NoError(t, os.WriteFile(passwordFile, []byte(password), 0o400))
	t.Setenv("APP_ENV", "production")
	t.Setenv("DEPLOYMENT_ENV", "production")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "true")
	t.Setenv("NODE_TYPE", "master")
	t.Setenv(platformRelayRootUsernameEnvironment, "root-admin")
	t.Setenv(platformRelayRootPasswordFileEnvironment, passwordFile)
	require.NoError(t, common.InstallProtectedSecretFileSnapshots([]common.ProtectedSecretFileSnapshot{{
		Environment: platformRelayRootPasswordFileEnvironment,
		Value:       []byte(password),
	}}))
	caPath := filepath.Join(t.TempDir(), "relay-database-ca.pem")
	t.Setenv("RELAY_DATABASE_CA_FILE", caPath)
	setPlatformRelayTestDSNFile(t, "postgresql://relay_user:database-password@postgres.internal:5432/relay?sslmode=verify-full&search_path=public&sslrootcert="+url.QueryEscape(caPath))
	t.Setenv("RELAY_PROVISION_ROOT_PASSWORD", "")
	os.Unsetenv("RELAY_PROVISION_ROOT_PASSWORD")
	t.Setenv("NEW_API_RELAY_ROOT_PASSWORD", "")
	os.Unsetenv("NEW_API_RELAY_ROOT_PASSWORD")
	return passwordFile
}

func TestPlatformRelayRootProvisionOfflineCommandRejectsNonProductionBeforeDatabaseAccess(t *testing.T) {
	t.Setenv("APP_ENV", "development")
	t.Setenv("DEPLOYMENT_ENV", "production")
	var output bytes.Buffer
	handled, err := handlePlatformRelayOfflineCommand(
		[]string{"/new-api", "relay-provision-root"},
		&output,
	)
	require.True(t, handled)
	require.ErrorContains(t, err, "requires an attested staging or production environment")
	require.Empty(t, output.String())
}

func TestPlatformRelayRootIsolationOfflineCommandRejectsArguments(t *testing.T) {
	var output bytes.Buffer
	handled, err := handlePlatformRelayOfflineCommand(
		[]string{"/new-api", "relay-validate-root-secret-isolation-v1", "unexpected"},
		&output,
	)
	require.True(t, handled)
	require.EqualError(t, err, "relay-validate-root-secret-isolation-v1 does not accept arguments")
	require.Empty(t, output.String())
}

func TestPlatformRelayRootProvisionRequiresIsolationBeforeDatabaseOrPasswordRead(t *testing.T) {
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "true")
	t.Setenv("NODE_TYPE", "master")
	t.Setenv("SQL_DSN_FILE", filepath.Join(t.TempDir(), "must-not-be-read-dsn"))
	t.Setenv("RELAY_DATABASE_CA_FILE", filepath.Join(t.TempDir(), "must-not-be-read-ca"))
	t.Setenv(platformRelayRootPasswordFileEnvironment, filepath.Join(t.TempDir(), "must-not-be-read-password"))
	t.Setenv("RELAY_SECRET_ISOLATION_RECEIPT_FILE", filepath.Join(t.TempDir(), "must-not-be-read-receipt"))
	previous := verifyPlatformRelayRootSecretIsolationReceipt
	verifyPlatformRelayRootSecretIsolationReceipt = func() (service.PlatformRelayRootProvisionInputs, error) {
		return service.PlatformRelayRootProvisionInputs{}, fmt.Errorf("synthetic isolation failure")
	}
	t.Cleanup(func() { verifyPlatformRelayRootSecretIsolationReceipt = previous })

	var output bytes.Buffer
	err := runPlatformRelayRootProvision(&output)
	require.EqualError(t, err, "Relay root isolation commitment is unavailable or invalid")
	require.Empty(t, output.String())
}

func TestPlatformRelayRootProvisionRejectsCaseFoldedUnrelatedSecretFile(t *testing.T) {
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "true")
	t.Setenv("NODE_TYPE", "master")
	t.Setenv("relay_api_runtime_secrets_file", "")
	require.EqualError(t, validatePlatformRelayRootProvisionExecutionEnvironment(),
		"Relay root provisioner received an unrelated secret file")
}

func TestPlatformRelayServicePrincipalProvisionOfflineCommandRejectsArguments(t *testing.T) {
	var output bytes.Buffer
	handled, err := handlePlatformRelayOfflineCommand(
		[]string{"/new-api", "relay-provision-service-principals", "unexpected"},
		&output,
	)
	require.True(t, handled)
	require.ErrorContains(t, err, "does not accept arguments")
	require.Empty(t, output.String())
}

func TestPlatformRelayServicePrincipalProvisionRejectsUnprotectedOrMismatchedEnvironmentBeforeFileAccess(t *testing.T) {
	for _, environments := range [][2]string{{"development", "development"}, {"staging", "production"}} {
		t.Run(environments[0]+"-"+environments[1], func(t *testing.T) {
			t.Setenv("APP_ENV", environments[0])
			t.Setenv("DEPLOYMENT_ENV", environments[1])
			t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
			var output bytes.Buffer
			err := runPlatformRelayServicePrincipalProvision(&output)
			require.ErrorContains(t, err, "requires an attested staging or production environment")
			require.Empty(t, output.String())
		})
	}
}

func TestPlatformRelayProtectedBootstrapEnvironmentMustBeCanonicalBeforeFileAccess(t *testing.T) {
	for _, environments := range [][2]string{
		{"STAGING", "STAGING"},
		{" staging", " staging"},
		{"production ", "production "},
	} {
		t.Run(strings.NewReplacer(" ", "space-").Replace(environments[0]), func(t *testing.T) {
			t.Setenv("APP_ENV", environments[0])
			t.Setenv("DEPLOYMENT_ENV", environments[1])
			t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
			t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "true")
			require.ErrorContains(t,
				validatePlatformRelayProtectedBootstrapEnvironment("relay-validate-secret-isolation"),
				"requires an attested staging or production environment",
			)
		})
	}
}

func TestPlatformRelaySchemaCommandsRejectRoleAttestedDevelopmentBeforeDatabaseOrReceiptAccess(t *testing.T) {
	t.Setenv("APP_ENV", "development")
	t.Setenv("DEPLOYMENT_ENV", "development")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "true")
	t.Setenv("NODE_TYPE", "master")
	t.Setenv("SQL_DSN_FILE", filepath.Join(t.TempDir(), "must-not-be-read"))

	require.EqualError(t,
		validatePlatformRelaySchemaCommandEnvironment("relay-migrate"),
		"relay-migrate requires exact attested staging or production environments",
	)
	require.Error(t, verifyPlatformRelaySecretIsolationForProtectedConsumer(
		service.PlatformRelaySecretIsolationConsumerMigrate,
	))
}

func TestPlatformRelayLocalDatabaseRoleRehearsalRequiresExactDevelopmentOptIn(t *testing.T) {
	t.Setenv("APP_ENV", "development")
	t.Setenv("DEPLOYMENT_ENV", "development")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "false")
	t.Setenv("RELAY_LOCAL_DATABASE_ROLE_REHEARSAL", "true")
	require.True(t, platformRelayLocalDatabaseRoleRehearsalEnabled())

	for _, override := range [][2]string{
		{"APP_ENV", "staging"},
		{"DEPLOYMENT_ENV", "production"},
		{"RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false"},
		{"RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "true"},
		{"RELAY_LOCAL_DATABASE_ROLE_REHEARSAL", "false"},
	} {
		t.Run(override[0]+"="+override[1], func(t *testing.T) {
			t.Setenv(override[0], override[1])
			require.False(t, platformRelayLocalDatabaseRoleRehearsalEnabled())
		})
	}
}

func TestPlatformRelayServicePrincipalProvisionRejectsUnrelatedSecretEnvironmentBeforeFileAccess(t *testing.T) {
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON", "must-not-enter-provisioner")
	var output bytes.Buffer
	err := runPlatformRelayServicePrincipalProvision(&output)
	require.ErrorContains(t, err, "forbidden raw secret environment variable")
	require.NotContains(t, err.Error(), "must-not-enter-provisioner")
	require.Empty(t, output.String())
}

func TestPlatformRelayServicePrincipalFileUsesStrictSecretBoundary(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("protected secret file mode is a Linux container contract")
	}
	fileName := filepath.Join(t.TempDir(), "service-principals.json")
	token := "sk-0eafe19482055d9fd8f6f08c60353eb5adb1a5311f6a7fe4"
	document := fmt.Sprintf(`{"kind":%q,"schema_version":1,"principals":[{"client_id":"platform","tenant_id":"00000000-0000-4000-8000-000000000001","upstream_token":%q}]}`,
		service.PlatformRelayServicePrincipalsFileKind, token)
	require.NoError(t, os.WriteFile(fileName, []byte(document), 0o400))
	t.Setenv(platformRelayServicePrincipalsFileEnvironment, fileName)
	_, err := readPlatformRelayOwnerOnlyReadOnlySecretFile(
		platformRelayServicePrincipalsFileEnvironment,
		platformRelayServicePrincipalsFileMaxBytes,
	)
	require.ErrorContains(t, err, "read-only filesystem")

	require.NoError(t, os.Mkdir(filepath.Join(filepath.Dir(fileName), "sub"), 0o700))
	uncleanPath := filepath.Dir(fileName) + string(os.PathSeparator) + "sub" + string(os.PathSeparator) + ".." + string(os.PathSeparator) + filepath.Base(fileName)
	t.Setenv(platformRelayServicePrincipalsFileEnvironment, uncleanPath)
	_, err = readPlatformRelayOwnerOnlyReadOnlySecretFile(platformRelayServicePrincipalsFileEnvironment, platformRelayServicePrincipalsFileMaxBytes)
	require.ErrorContains(t, err, "path is invalid")
	t.Setenv(platformRelayServicePrincipalsFileEnvironment, fileName)

	require.NoError(t, os.Chmod(fileName, 0o644))
	_, err = readPlatformRelayOwnerOnlyReadOnlySecretFile(platformRelayServicePrincipalsFileEnvironment, platformRelayServicePrincipalsFileMaxBytes)
	require.ErrorContains(t, err, "group or world readable")
	require.NotContains(t, err.Error(), strings.TrimPrefix(token, "sk-"))
}

func TestLoadPlatformRelayRootProvisionInputAcceptsProtectedStaging(t *testing.T) {
	setValidPlatformRelayRootProvisionEnvironment(t, platformRelayRootTestPassword)
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	input, err := loadPlatformRelayRootProvisionInput()
	require.NoError(t, err)
	require.Equal(t, "root-admin", input.Username)
}

func TestLoadPlatformRelayRootProvisionInputUsesOnlyRegularAbsolutePasswordFile(t *testing.T) {
	passwordFile := setValidPlatformRelayRootProvisionEnvironment(t, platformRelayRootTestPassword)
	input, err := loadPlatformRelayRootProvisionInput()
	require.NoError(t, err)
	require.Equal(t, "root-admin", input.Username)
	require.Equal(t, platformRelayRootTestPassword, input.Password)
	require.True(t, filepath.IsAbs(passwordFile))
	require.NoError(t, validatePlatformRelayRootProvisionDatabase())
}

func TestLoadPlatformRelayRootProvisionInputRejectsUnsafeSecretsWithoutEchoingThem(t *testing.T) {
	tests := []struct {
		name     string
		password string
	}{
		{name: "short", password: "short-password"},
		{name: "leading whitespace", password: " " + strings.Repeat("p", 32)},
		{name: "trailing newline", password: strings.Repeat("p", 32) + "\n"},
		{name: "nul", password: strings.Repeat("p", 16) + "\x00" + strings.Repeat("p", 16)},
		{name: "invalid UTF8", password: string(append([]byte{0xff}, []byte(strings.Repeat("p", 32))...))},
		{name: "bcrypt overflow", password: strings.Repeat("p", 73)},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			setValidPlatformRelayRootProvisionEnvironment(t, test.password)
			_, err := loadPlatformRelayRootProvisionInput()
			require.Error(t, err)
			require.NotContains(t, err.Error(), test.password)
		})
	}
}

func TestLoadPlatformRelayRootProvisionInputRejectsPasswordEnvironmentAndNonRegularPath(t *testing.T) {
	setValidPlatformRelayRootProvisionEnvironment(t, platformRelayRootTestPassword)
	t.Setenv("RELAY_PROVISION_ROOT_PASSWORD", "must-never-be-read")
	_, err := loadPlatformRelayRootProvisionInput()
	require.ErrorContains(t, err, "forbidden raw secret environment variable")
	require.NotContains(t, err.Error(), "must-never-be-read")

	t.Setenv("RELAY_PROVISION_ROOT_PASSWORD", "")
	os.Unsetenv("RELAY_PROVISION_ROOT_PASSWORD")
	directory := t.TempDir()
	t.Setenv(platformRelayRootPasswordFileEnvironment, directory)
	_, err = loadPlatformRelayRootProvisionInput()
	require.ErrorContains(t, err, "unavailable or invalid")
	require.NotContains(t, err.Error(), directory)
}

func TestLoadPlatformRelayRootProvisionInputRejectsReadableWritableAndSymlinkSecrets(t *testing.T) {
	setValidPlatformRelayRootProvisionEnvironment(t, platformRelayRootTestPassword)
	passwordFile := filepath.Join(t.TempDir(), "untrusted-root-password")
	require.NoError(t, os.WriteFile(passwordFile, []byte(platformRelayRootTestPassword), 0o644))
	t.Setenv(platformRelayRootPasswordFileEnvironment, passwordFile)
	require.NoError(t, os.Chmod(passwordFile, 0o644))
	_, err := loadPlatformRelayRootProvisionInput()
	require.ErrorContains(t, err, "group or world readable")

	require.NoError(t, os.Chmod(passwordFile, 0o600))
	_, err = loadPlatformRelayRootProvisionInput()
	require.ErrorContains(t, err, "read-only filesystem")

	require.NoError(t, os.Chmod(passwordFile, 0o400))
	linkName := filepath.Join(t.TempDir(), "root-password-link")
	require.NoError(t, os.Symlink(passwordFile, linkName))
	t.Setenv(platformRelayRootPasswordFileEnvironment, linkName)
	_, err = loadPlatformRelayRootProvisionInput()
	require.ErrorContains(t, err, "unavailable or invalid")
}

func TestValidatePlatformRelayRootProvisionDatabaseFailsClosed(t *testing.T) {
	valid := []string{
		"postgres://relay:password@postgres.internal/relay?sslmode=verify-full&search_path=public",
		"postgresql://relay:password@postgres.internal:5432/relay?sslmode=verify-full&search_path=public",
	}
	for _, dsn := range valid {
		setPlatformRelayTestDSNFile(t, dsn)
		require.NoError(t, validatePlatformRelayRootProvisionDatabase())
	}
	invalid := []string{
		"",
		"sqlite:///relay.db",
		"mysql://relay:password@mysql/relay",
		" postgres://relay:password@postgres/relay",
		"postgres://postgres.internal/relay",
		"postgres://relay:password@postgres.internal/",
		"postgres://relay:password@postgres.internal/relay#fragment",
	}
	for _, dsn := range invalid {
		setPlatformRelayTestDSNFile(t, dsn)
		err := validatePlatformRelayRootProvisionDatabase()
		require.Error(t, err)
		if dsn != "" {
			require.NotContains(t, err.Error(), dsn)
		}
	}
}
