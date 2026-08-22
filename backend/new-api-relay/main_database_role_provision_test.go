package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestLoadPlatformRelayDatabaseRoleProvisionPasswordsValidatesBothBeforeDatabaseAccess(t *testing.T) {
	originalReader := readPlatformRelayDatabasePasswordSecretFile
	readPlatformRelayDatabasePasswordSecretFile = func(environment string, maximumBytes int64) ([]byte, error) {
		return os.ReadFile(os.Getenv(environment))
	}
	t.Cleanup(func() { readPlatformRelayDatabasePasswordSecretFile = originalReader })
	directory := t.TempDir()
	migrationFile := filepath.Join(directory, "migration-password")
	runtimeFile := filepath.Join(directory, "runtime-password")
	edgeFile := filepath.Join(directory, "edge-password")
	const migrationPassword = "migration-role-password-0123456789"
	const runtimePassword = "runtime-role-password-0123456789-ab"
	const edgePassword = "edge-role-password-0123456789-abcdef"
	require.NoError(t, os.WriteFile(migrationFile, []byte(migrationPassword), 0o600))
	require.NoError(t, os.WriteFile(runtimeFile, []byte(runtimePassword), 0o600))
	require.NoError(t, os.WriteFile(edgeFile, []byte(edgePassword), 0o600))
	t.Setenv("APP_ENV", "development")
	t.Setenv("DEPLOYMENT_ENV", "development")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
	t.Setenv("RELAY_MIGRATION_DATABASE_PASSWORD_FILE", migrationFile)
	t.Setenv("RELAY_RUNTIME_DATABASE_PASSWORD_FILE", runtimeFile)
	t.Setenv("RELAY_DOWNLOAD_EDGE_DATABASE_PASSWORD_FILE", edgeFile)

	migration, runtime, edge, err := loadPlatformRelayDatabaseRoleProvisionPasswords()
	require.NoError(t, err)
	require.Equal(t, migrationPassword, string(migration))
	require.Equal(t, runtimePassword, string(runtime))
	require.Equal(t, edgePassword, string(edge))
	clear(migration)
	clear(runtime)
	clear(edge)

	require.NoError(t, os.WriteFile(runtimeFile, []byte("invalid-second-password"), 0o600))
	migration, runtime, edge, err = loadPlatformRelayDatabaseRoleProvisionPasswords()
	require.Error(t, err)
	require.Nil(t, migration)
	require.Nil(t, runtime)
	require.Nil(t, edge)
	require.NotContains(t, err.Error(), "invalid-second-password")
}

func TestReadPlatformRelayDatabasePasswordFileRejectsEveryNewlineByte(t *testing.T) {
	originalReader := readPlatformRelayDatabasePasswordSecretFile
	readPlatformRelayDatabasePasswordSecretFile = func(environment string, maximumBytes int64) ([]byte, error) {
		return os.ReadFile(os.Getenv(environment))
	}
	t.Cleanup(func() { readPlatformRelayDatabasePasswordSecretFile = originalReader })
	passwordFile := filepath.Join(t.TempDir(), "database-password")
	t.Setenv("RELAY_RUNTIME_DATABASE_PASSWORD_FILE", passwordFile)
	for _, suffix := range []string{"\n", "\r\n", "\r"} {
		t.Run(strings.NewReplacer("\r", "CR", "\n", "LF").Replace(suffix), func(t *testing.T) {
			value := "runtime-role-password-0123456789-ab" + suffix
			require.NoError(t, os.WriteFile(passwordFile, []byte(value), 0o600))
			password, err := readPlatformRelayDatabasePasswordFile("RELAY_RUNTIME_DATABASE_PASSWORD_FILE")
			require.ErrorContains(t, err, "content is invalid")
			require.Nil(t, password)
			require.NotContains(t, err.Error(), "runtime-role-password")
		})
	}
}

func TestReadPlatformRelayDatabasePasswordFileFailsClosedForAttestedStagingMode(t *testing.T) {
	passwordFile := filepath.Join(t.TempDir(), "database-password")
	require.NoError(t, os.WriteFile(passwordFile, []byte("runtime-role-password-0123456789-ab"), 0o644))
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_RUNTIME_DATABASE_PASSWORD_FILE", passwordFile)

	password, err := readPlatformRelayDatabasePasswordFile("RELAY_RUNTIME_DATABASE_PASSWORD_FILE")
	require.Error(t, err)
	require.Nil(t, password)
	require.NotContains(t, err.Error(), "runtime-role-password")
}
