package model

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestResolveDatabaseDSNUsesFileAndRejectsUnsafeAttestedStagingMode(t *testing.T) {
	fileName := filepath.Join(t.TempDir(), "runtime-dsn")
	require.NoError(t, os.WriteFile(fileName, []byte("postgresql://relay_runtime:test-password@postgres.invalid:5432/relay?search_path=public"), 0o600))
	t.Setenv("APP_ENV", "development")
	t.Setenv("DEPLOYMENT_ENV", "development")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
	t.Setenv("RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED", "false")
	t.Setenv("SQL_DSN", "")
	t.Setenv("SQL_DSN_FILE", fileName)

	dsn, err := ResolveDatabaseDSN("SQL_DSN")
	require.NoError(t, err)
	require.Equal(t, "postgresql://relay_runtime:test-password@postgres.invalid:5432/relay?search_path=public", dsn)

	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	require.NoError(t, os.Unsetenv("SQL_DSN"))
	require.NoError(t, os.Chmod(fileName, 0o644))
	dsn, err = ResolveDatabaseDSN("SQL_DSN")
	require.Error(t, err)
	require.Empty(t, dsn)
	require.NotContains(t, err.Error(), "test-password")
}

func TestResolveDatabaseDSNAttestedModeRejectsEveryRawEnvironmentPresentation(t *testing.T) {
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_SECRET_FILES_REQUIRED", "false")
	t.Setenv("RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED", "false")

	for _, test := range []struct {
		name string
		raw  string
	}{
		{name: "present empty", raw: ""},
		{name: "present secret", raw: "postgresql://relay_runtime:must-not-enter-env@postgres.invalid/relay"},
	} {
		t.Run(test.name, func(t *testing.T) {
			t.Setenv("SQL_DSN", test.raw)
			_, err := ResolveDatabaseDSN("SQL_DSN")
			require.ErrorContains(t, err, "environment value is forbidden")
			require.NotContains(t, err.Error(), "must-not-enter-env")
		})
	}
}

func TestResolveDatabaseDSNRejectsAmbiguousAndSymlinkSources(t *testing.T) {
	directory := t.TempDir()
	fileName := filepath.Join(directory, "runtime-dsn")
	require.NoError(t, os.WriteFile(fileName, []byte("postgresql://relay_runtime:test-password@postgres.invalid:5432/relay"), 0o600))
	t.Setenv("SQL_DSN", "postgresql://other:password@postgres.invalid:5432/relay")
	t.Setenv("SQL_DSN_FILE", fileName)
	dsn, err := ResolveDatabaseDSN("SQL_DSN")
	require.Error(t, err)
	require.Empty(t, dsn)

	linkName := filepath.Join(directory, "runtime-dsn-link")
	if err := os.Symlink(fileName, linkName); err != nil {
		t.Skipf("symbolic links are unavailable in this test environment: %v", err)
	}
	t.Setenv("SQL_DSN", "")
	t.Setenv("SQL_DSN_FILE", linkName)
	dsn, err = ResolveDatabaseDSN("SQL_DSN")
	require.Error(t, err)
	require.Empty(t, dsn)
}
