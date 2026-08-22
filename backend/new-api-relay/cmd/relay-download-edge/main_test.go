package main

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/service"
	"github.com/stretchr/testify/require"
)

func unsetDownloadEdgeDatabaseEnvironmentForTest(t *testing.T, name string) {
	t.Helper()
	value, present := os.LookupEnv(name)
	require.NoError(t, os.Unsetenv(name))
	t.Cleanup(func() {
		if present {
			require.NoError(t, os.Setenv(name, value))
			return
		}
		require.NoError(t, os.Unsetenv(name))
	})
}

func setProtectedDownloadEdgeDatabaseFileForTest(t *testing.T, dsn string) string {
	t.Helper()
	for _, name := range []string{"RELAY_DOWNLOAD_EDGE_SQL_DSN", "SQL_DSN", "SQL_DSN_FILE"} {
		unsetDownloadEdgeDatabaseEnvironmentForTest(t, name)
	}
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_SECRET_FILES_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_CA_FILE", "/run/secrets/relay-database-ca.pem")
	fileName := "/run/secrets/relay-download-edge-sql-dsn"
	t.Setenv("RELAY_DOWNLOAD_EDGE_SQL_DSN_FILE", fileName)
	previousResolver := resolveDownloadEdgeDatabaseDSN
	resolveDownloadEdgeDatabaseDSN = func(environment string) (string, error) {
		require.Equal(t, "RELAY_DOWNLOAD_EDGE_SQL_DSN", environment)
		return dsn, nil
	}
	t.Cleanup(func() { resolveDownloadEdgeDatabaseDSN = previousResolver })
	return fileName
}

func TestConfigureDownloadEdgeDatabaseProductionRequiresDedicatedDSNAndDisablesMigrations(t *testing.T) {
	previousMaster := common.IsMasterNode
	t.Cleanup(func() { common.IsMasterNode = previousMaster })

	t.Run("generic SQL DSN is not a production fallback", func(t *testing.T) {
		t.Setenv("RELAY_DOWNLOAD_EDGE_SQL_DSN", "")
		t.Setenv("SQL_DSN", "postgresql://shared-admin:secret@db/new_api")
		require.Error(t, configureDownloadEdgeDatabase(true))
	})

	t.Run("generic SQL DSN is not inherited alongside the dedicated DSN", func(t *testing.T) {
		setProtectedDownloadEdgeDatabaseFileForTest(t, "postgresql://relay_download_edge:dedicated-secret@db/new_api?sslmode=verify-full&search_path=public&sslrootcert=/run/secrets/relay-database-ca.pem")
		t.Setenv("SQL_DSN", "postgresql://shared-admin:secret@db/new_api")
		require.Error(t, configureDownloadEdgeDatabase(true))
	})

	t.Run("placeholder and non PostgreSQL DSNs fail closed", func(t *testing.T) {
		setProtectedDownloadEdgeDatabaseFileForTest(t, "postgresql://relay_download_edge:local-new-api-postgres-password@db/new_api?sslmode=verify-full&search_path=public&sslrootcert=/run/secrets/relay-database-ca.pem")
		require.Error(t, configureDownloadEdgeDatabase(true))
	})

	t.Run("arbitrary admin-looking usernames are not accepted", func(t *testing.T) {
		setProtectedDownloadEdgeDatabaseFileForTest(t, "postgresql://edge_admin:4b9c9f1f0ec8432a9ce0b95f93bfbc5f@db/new_api?sslmode=verify-full&search_path=public&sslrootcert=/run/secrets/relay-database-ca.pem")
		require.Error(t, configureDownloadEdgeDatabase(true))
	})

	t.Run("dedicated PostgreSQL DSN file is mapped without exposing its value", func(t *testing.T) {
		dsn := "postgresql://relay_download_edge:4b9c9f1f0ec8432a9ce0b95f93bfbc5f@db/new_api?sslmode=verify-full&search_path=public&sslrootcert=/run/secrets/relay-database-ca.pem"
		fileName := setProtectedDownloadEdgeDatabaseFileForTest(t, dsn)
		common.IsMasterNode = true
		require.NoError(t, configureDownloadEdgeDatabase(true))
		_, rawPresent := os.LookupEnv("SQL_DSN")
		require.False(t, rawPresent)
		require.Equal(t, fileName, os.Getenv("SQL_DSN_FILE"))
		require.False(t, common.IsMasterNode)
	})
}

func TestConfigureDownloadEdgeDatabaseSecureStagingRequiresDedicatedPostgresRole(t *testing.T) {
	previousMaster := common.IsMasterNode
	t.Cleanup(func() { common.IsMasterNode = previousMaster })
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")

	t.Run("generic SQL DSN cannot bypass the staging attestation gate", func(t *testing.T) {
		t.Setenv("RELAY_DOWNLOAD_EDGE_SQL_DSN", "")
		t.Setenv("SQL_DSN", "postgresql://shared-admin:secret@db/new_api")
		require.Error(t, configureDownloadEdgeDatabase(false))
	})

	t.Run("dedicated edge DSN is required even when config production is false", func(t *testing.T) {
		dsn := "postgresql://relay_download_edge:4b9c9f1f0ec8432a9ce0b95f93bfbc5f@db/new_api?sslmode=verify-full&search_path=public&sslrootcert=/run/secrets/relay-database-ca.pem"
		fileName := setProtectedDownloadEdgeDatabaseFileForTest(t, dsn)
		common.IsMasterNode = true
		require.NoError(t, configureDownloadEdgeDatabase(false))
		require.Equal(t, fileName, os.Getenv("SQL_DSN_FILE"))
		require.False(t, common.IsMasterNode)
	})
}

func TestConfigureDownloadEdgeDatabaseProtectedConfigDoesNotDependOnRoleFlag(t *testing.T) {
	previousMaster := common.IsMasterNode
	t.Cleanup(func() { common.IsMasterNode = previousMaster })
	t.Setenv("APP_ENV", "development")
	t.Setenv("DEPLOYMENT_ENV", "development")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
	t.Setenv("RELAY_DOWNLOAD_EDGE_SQL_DSN", "")
	t.Setenv("SQL_DSN", "postgresql://shared-admin:secret@db/new_api")

	config := service.PlatformDownloadEdgeConfig{Protected: true}
	require.True(t, config.ProtectedSecurityRequired())
	require.Error(t, configureDownloadEdgeDatabase(config.ProtectedSecurityRequired()))
}

func TestWaitForDownloadEdgeWorkerHonorsDrainDeadline(t *testing.T) {
	done := make(chan error, 1)
	deadlineContext, cancelDeadline := context.WithTimeout(context.Background(), 20*time.Millisecond)
	err := waitForDownloadEdgeWorker(deadlineContext, done)
	cancelDeadline()
	require.Error(t, err)

	done <- context.Canceled
	joinContext, cancelJoin := context.WithTimeout(context.Background(), time.Second)
	defer cancelJoin()
	err = waitForDownloadEdgeWorker(joinContext, done)
	require.ErrorIs(t, err, context.Canceled)
}
