package main

import (
	"context"
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/service"
	"github.com/stretchr/testify/require"
)

type functionalDownloadEdgeReadinessStopper func(context.Context) error

func (stopper functionalDownloadEdgeReadinessStopper) StopProtectedReadinessRefresh(ctx context.Context) error {
	return stopper(ctx)
}

type blockingDownloadEdgeReadinessStopper struct {
	started chan struct{}
	release chan struct{}
	exited  atomic.Bool
}

func (stopper *blockingDownloadEdgeReadinessStopper) StopProtectedReadinessRefresh(ctx context.Context) error {
	close(stopper.started)
	select {
	case <-stopper.release:
		stopper.exited.Store(true)
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

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
	secretDirectory := t.TempDir()
	caFile := filepath.Join(secretDirectory, "relay-database-ca.pem")
	fileName := filepath.Join(secretDirectory, "relay-download-edge-sql-dsn")
	parsedDSN, err := url.Parse(dsn)
	require.NoError(t, err)
	query := parsedDSN.Query()
	query.Set("sslrootcert", caFile)
	parsedDSN.RawQuery = query.Encode()
	dsn = parsedDSN.String()
	t.Setenv("RELAY_DATABASE_CA_FILE", caFile)
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
	require.NoError(t, err)

	workerFailure := errors.New("delivery worker failed")
	done <- workerFailure
	err = waitForDownloadEdgeWorker(joinContext, done)
	require.ErrorIs(t, err, workerFailure)
}

func TestLifecycleLossJoinsReadinessRefreshBeforeClosingDatabase(t *testing.T) {
	stopper := &blockingDownloadEdgeReadinessStopper{started: make(chan struct{}), release: make(chan struct{})}
	closeCalled := make(chan struct{}, 1)
	done := make(chan error, 1)
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	go func() {
		done <- stopDownloadEdgeReadinessAndCloseDatabase(ctx, stopper, func() error {
			if !stopper.exited.Load() {
				return errors.New("database close ran before readiness refresh exited")
			}
			closeCalled <- struct{}{}
			return nil
		})
	}()

	<-stopper.started
	select {
	case <-closeCalled:
		t.Fatal("database closed while readiness refresh was still running")
	case <-time.After(20 * time.Millisecond):
	}
	close(stopper.release)
	require.NoError(t, <-done)
	select {
	case <-closeCalled:
	default:
		t.Fatal("database was not closed after readiness refresh exited")
	}
}

func TestFinalizeDownloadEdgeDatabaseShutdownOrdersNormalAndLifecycleLoss(t *testing.T) {
	for _, test := range []struct {
		name       string
		anchorStep string
	}{
		{name: "normal", anchorStep: "anchor-release"},
		{name: "lifecycle-loss", anchorStep: "anchor-close"},
	} {
		t.Run(test.name, func(t *testing.T) {
			var mu sync.Mutex
			sequence := make([]string, 0, 5)
			record := func(step string) {
				mu.Lock()
				sequence = append(sequence, step)
				mu.Unlock()
			}
			started := make(chan struct{})
			release := make(chan struct{})
			stopper := functionalDownloadEdgeReadinessStopper(func(ctx context.Context) error {
				record("readiness-start")
				close(started)
				select {
				case <-release:
					record("readiness-end")
					return nil
				case <-ctx.Done():
					return ctx.Err()
				}
			})
			done := make(chan error, 1)
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			defer cancel()
			go func() {
				done <- finalizeDownloadEdgeDatabaseShutdown(
					ctx,
					stopper,
					func() error { record("database-close"); return nil },
					func() { record("monitor-stop") },
					func() error { record(test.anchorStep); return nil },
					func() error { record("anchor-close"); return nil },
				)
			}()
			<-started
			mu.Lock()
			require.Equal(t, []string{"readiness-start"}, append([]string(nil), sequence...))
			mu.Unlock()
			close(release)
			require.NoError(t, <-done)
			mu.Lock()
			require.Equal(t, []string{
				"readiness-start", "readiness-end", "database-close", "monitor-stop", test.anchorStep,
			}, sequence)
			mu.Unlock()
		})
	}
}

func TestFinalizeDownloadEdgeDatabaseShutdownSkipsPoolCloseWhenRefreshCannotJoin(t *testing.T) {
	sequence := make([]string, 0, 3)
	closeDatabaseCalled := false
	err := finalizeDownloadEdgeDatabaseShutdown(
		context.Background(),
		functionalDownloadEdgeReadinessStopper(func(context.Context) error {
			sequence = append(sequence, "readiness-failed")
			return errors.New("refresh did not join")
		}),
		func() error { closeDatabaseCalled = true; return nil },
		func() { sequence = append(sequence, "monitor-stop") },
		func() error { sequence = append(sequence, "anchor-release"); return nil },
		func() error { sequence = append(sequence, "anchor-close"); return nil },
	)
	require.ErrorContains(t, err, "refresh did not join")
	require.False(t, closeDatabaseCalled)
	require.Equal(t, []string{"readiness-failed", "monitor-stop", "anchor-close"}, sequence)
}
