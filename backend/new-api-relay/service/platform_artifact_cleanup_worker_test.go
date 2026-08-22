package service

import (
	"context"
	"errors"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestPlatformArtifactCleanupWorkerRecoversFromTransientStoreInitFailureAndPanic(t *testing.T) {
	truncate(t)
	resetPlatformArtifactCleanupWorkerStatusForTest()
	t.Cleanup(resetPlatformArtifactCleanupWorkerStatusForTest)

	store := &cleanupRecordingArtifactStore{objects: map[string]bool{}}
	var attempts atomic.Int32
	factory := func() (PlatformArtifactStore, error) {
		switch attempts.Add(1) {
		case 1:
			return nil, errors.New("temporary OBS discovery failure")
		case 2:
			panic("temporary constructor panic")
		default:
			return store, nil
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		runPlatformArtifactCleanupWorker(ctx, factory, platformArtifactCleanupWorkerOptions{
			interval:     5 * time.Millisecond,
			initialRetry: 5 * time.Millisecond,
			maxRetry:     10 * time.Millisecond,
			now:          time.Now,
		})
	}()

	require.Eventually(t, func() bool {
		status := GetPlatformArtifactCleanupWorkerStatus()
		return attempts.Load() >= 3 && status.Running && !status.LastSuccessAt.IsZero()
	}, time.Second, 5*time.Millisecond)
	status := GetPlatformArtifactCleanupWorkerStatus()
	assert.True(t, status.Started)
	assert.True(t, status.Running)
	assert.False(t, status.Stale)
	assert.Empty(t, status.CurrentErrorCode)
	assert.Zero(t, status.ConsecutiveErrors)
	assert.False(t, status.LastErrorAt.IsZero())
	assert.True(t, status.LastSuccessAt.After(status.LastErrorAt) || status.LastSuccessAt.Equal(status.LastErrorAt))

	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("artifact cleanup worker did not stop after cancellation")
	}
	assert.False(t, GetPlatformArtifactCleanupWorkerStatus().Running)
}

func TestPlatformArtifactCleanupWorkerStatusDetectsStaleHeartbeatAndRecovers(t *testing.T) {
	resetPlatformArtifactCleanupWorkerStatusForTest()
	t.Cleanup(resetPlatformArtifactCleanupWorkerStatusForTest)

	baseline := time.Date(2026, time.August, 12, 8, 0, 0, 0, time.UTC)
	markPlatformArtifactCleanupWorkerStarted(baseline)
	markPlatformArtifactCleanupWorkerSuccess(baseline)

	stale := getPlatformArtifactCleanupWorkerStatusAt(
		baseline.Add(platformArtifactCleanupWorkerStaleAfter + time.Nanosecond),
	)
	assert.True(t, stale.Started)
	assert.True(t, stale.Running)
	assert.True(t, stale.Stale)

	recoveredAt := baseline.Add(platformArtifactCleanupWorkerStaleAfter + time.Second)
	markPlatformArtifactCleanupWorkerSuccess(recoveredAt)
	recovered := getPlatformArtifactCleanupWorkerStatusAt(recoveredAt)
	assert.False(t, recovered.Stale)
	assert.Empty(t, recovered.CurrentErrorCode)
}

func TestPlatformArtifactCleanupSupervisorDynamicallyActivatesForDisabledCompat(t *testing.T) {
	truncate(t)
	resetPlatformArtifactCleanupWorkerStatusForTest()
	t.Cleanup(resetPlatformArtifactCleanupWorkerStatusForTest)

	store := &cleanupRecordingArtifactStore{objects: map[string]bool{}}
	var factoryCalls atomic.Int32
	factory := func() (PlatformArtifactStore, error) {
		factoryCalls.Add(1)
		return store, nil
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		runPlatformArtifactCleanupSupervisor(
			ctx,
			factory,
			platformArtifactCleanupWorkerOptions{
				interval:     5 * time.Millisecond,
				initialRetry: 5 * time.Millisecond,
				maxRetry:     10 * time.Millisecond,
				now:          time.Now,
			},
			func() bool { return false },
		)
	}()

	require.Eventually(t, func() bool {
		status := GetPlatformArtifactCleanupWorkerStatus()
		return status.Started && status.Running && !status.LastSuccessAt.IsZero()
	}, time.Second, 5*time.Millisecond)
	assert.Zero(t, factoryCalls.Load(), "idle cleanup must not require artifact credentials")

	// Model an older enabled pod creating its first upload intent after this
	// disabled pod's initial empty snapshot. The supervisor must observe the
	// durable row and activate without a restart.
	_, _, _, _ = appendPlatformArtifactCleanupServiceIntent(t, store)
	require.Eventually(t, func() bool {
		return factoryCalls.Load() > 0
	}, time.Second, 5*time.Millisecond)

	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("artifact cleanup supervisor did not stop after cancellation")
	}
}

func TestArtifactCleanupMaintenanceValidationRequiresStoreOnlyForHistoricalIntent(t *testing.T) {
	truncate(t)
	t.Setenv("RELAY_COMPAT_ENABLED", "false")
	t.Setenv("RELAY_COMPAT_WORKER_ENABLED", "false")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("RELAY_ARTIFACT_STORE", "")

	require.NoError(t, ValidatePlatformArtifactCleanupMaintenanceConfiguration())
	store := &cleanupRecordingArtifactStore{objects: map[string]bool{}}
	_, _, _, _ = appendPlatformArtifactCleanupServiceIntent(t, store)
	require.Error(t, ValidatePlatformArtifactCleanupMaintenanceConfiguration())

	t.Setenv("RELAY_ARTIFACT_STORE", PlatformArtifactFilesystemKind)
	t.Setenv("RELAY_ARTIFACT_FILESYSTEM_ROOT", t.TempDir())
	t.Setenv("RELAY_ARTIFACT_PUBLIC_BASE_URL", "https://relay-artifacts.example.test")
	t.Setenv("RELAY_ARTIFACT_SIGNING_SECRET", strings.Repeat("s", 32))
	require.NoError(t, ValidatePlatformArtifactCleanupMaintenanceConfiguration())
}

func TestPlatformArtifactCleanupCoordinatorCancelsAndJoinsConcurrentStops(t *testing.T) {
	truncate(t)
	resetPlatformArtifactCleanupWorkerStatusForTest()
	t.Cleanup(resetPlatformArtifactCleanupWorkerStatusForTest)
	var coordinator platformArtifactCleanupWorkerCoordinator
	require.NoError(t, coordinator.start(
		func() (PlatformArtifactStore, error) {
			return nil, errors.New("store must not be opened for an empty maintenance queue")
		},
		platformArtifactCleanupWorkerOptions{
			interval:     5 * time.Millisecond,
			initialRetry: 5 * time.Millisecond,
			maxRetry:     10 * time.Millisecond,
			now:          time.Now,
		},
		func() bool { return false },
	))
	require.Eventually(t, func() bool {
		return GetPlatformArtifactCleanupWorkerStatus().Running
	}, time.Second, 5*time.Millisecond)
	results := make(chan error, 2)
	for range 2 {
		go func() {
			stopContext, cancelStop := context.WithTimeout(context.Background(), time.Second)
			defer cancelStop()
			results <- coordinator.stop(stopContext)
		}()
	}
	for range 2 {
		require.NoError(t, <-results)
	}
	require.False(t, GetPlatformArtifactCleanupWorkerStatus().Running)
	require.NoError(t, coordinator.stop(context.Background()))
}
