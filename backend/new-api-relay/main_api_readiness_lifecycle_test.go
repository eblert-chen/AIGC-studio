package main

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/stretchr/testify/require"
)

func TestClosePlatformRelayDatabaseAfterReadinessOrdersJoinBeforeClose(t *testing.T) {
	var mu sync.Mutex
	steps := make([]string, 0, 2)
	err := closePlatformRelayDatabaseAfterReadiness(
		context.Background(),
		func(context.Context) error {
			mu.Lock()
			steps = append(steps, "readiness-joined")
			mu.Unlock()
			return nil
		},
		func() error {
			mu.Lock()
			steps = append(steps, "database-closed")
			mu.Unlock()
			return nil
		},
	)
	require.NoError(t, err)
	require.Equal(t, []string{"readiness-joined", "database-closed"}, steps)
}

func TestFinalizePlatformRelayNormalDatabaseOwnershipWaitsForHandlersBeforePoolClose(t *testing.T) {
	tracker := service.NewRelayHTTPHandlerTracker()
	require.True(t, tracker.Enter())
	closed := false
	monitorStopped := false
	anchorReleased := false
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	err := finalizePlatformRelayNormalDatabaseOwnership(
		ctx, tracker,
		func(context.Context) error { return nil },
		func() error { closed = true; return nil },
		func() { monitorStopped = true },
		func() bool { return false },
		func() error { anchorReleased = true; return nil },
		func() {},
	)
	cancel()
	require.Error(t, err)
	require.False(t, closed)
	require.False(t, monitorStopped)
	require.False(t, anchorReleased)

	tracker.Leave()
	ctx, cancel = context.WithTimeout(context.Background(), time.Second)
	require.NoError(t, finalizePlatformRelayNormalDatabaseOwnership(
		ctx, tracker,
		func(context.Context) error { return nil },
		func() error { closed = true; return nil },
		func() { monitorStopped = true },
		func() bool { return false },
		func() error { anchorReleased = true; return nil },
		func() {},
	))
	cancel()
	require.True(t, closed)
	require.True(t, monitorStopped)
	require.True(t, anchorReleased)
}

func TestFinalizePlatformRelayNormalDatabaseOwnershipPreservesAnchorOnJoinOrCloseFailure(t *testing.T) {
	for _, test := range []struct {
		name          string
		stopWorkers   func(context.Context) error
		closeDatabase func() error
	}{
		{name: "worker or readiness join", stopWorkers: func(context.Context) error { return errors.New("worker blocked") }, closeDatabase: func() error { return nil }},
		{name: "database close", stopWorkers: func(context.Context) error { return nil }, closeDatabase: func() error { return errors.New("close failed") }},
	} {
		t.Run(test.name, func(t *testing.T) {
			monitorStopped := false
			anchorReleased := false
			anchorClosed := false
			err := finalizePlatformRelayNormalDatabaseOwnership(
				context.Background(), nil, test.stopWorkers, test.closeDatabase,
				func() { monitorStopped = true },
				func() bool { return false },
				func() error { anchorReleased = true; return nil },
				func() { anchorClosed = true },
			)
			require.Error(t, err)
			require.False(t, monitorStopped)
			require.False(t, anchorReleased)
			require.False(t, anchorClosed)
		})
	}
}

func TestFinalizePlatformRelayNormalDatabaseOwnershipClosesAnchorWhenReleaseFails(t *testing.T) {
	steps := make([]string, 0, 5)
	err := finalizePlatformRelayNormalDatabaseOwnership(
		context.Background(), nil,
		func(context.Context) error { steps = append(steps, "workers"); return nil },
		func() error { steps = append(steps, "database"); return nil },
		func() { steps = append(steps, "monitor") },
		func() bool { return false },
		func() error { steps = append(steps, "release"); return errors.New("release failed") },
		func() { steps = append(steps, "anchor-close") },
	)
	require.Error(t, err)
	require.Equal(t, []string{"workers", "database", "monitor", "release", "anchor-close"}, steps)
}

func TestClosePlatformRelayDatabaseAfterReadinessNeverClosesOnJoinFailure(t *testing.T) {
	closed := false
	err := closePlatformRelayDatabaseAfterReadiness(
		context.Background(),
		func(context.Context) error { return errors.New("readiness refresh is still running") },
		func() error { closed = true; return nil },
	)
	require.Error(t, err)
	require.False(t, closed)
}

func TestFinalizePlatformRelayInitializationOwnershipPreservesAnchorUntilJoinAndClose(t *testing.T) {
	for _, test := range []struct {
		name          string
		stopReadiness func(context.Context) error
		closeDatabase func() error
	}{
		{
			name:          "readiness newcomer is not joined",
			stopReadiness: func(context.Context) error { return errors.New("readiness refresh is still running") },
			closeDatabase: func() error { return nil },
		},
		{
			name:          "database close failed",
			stopReadiness: func(context.Context) error { return nil },
			closeDatabase: func() error { return errors.New("database close failed") },
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			monitorStopped := false
			anchorClosed := false
			err := finalizePlatformRelayInitializationDatabaseOwnership(
				context.Background(),
				test.stopReadiness,
				test.closeDatabase,
				func() { monitorStopped = true },
				func() { anchorClosed = true },
			)
			require.Error(t, err)
			require.False(t, monitorStopped)
			require.False(t, anchorClosed)
		})
	}

	steps := make([]string, 0, 4)
	require.NoError(t, finalizePlatformRelayInitializationDatabaseOwnership(
		context.Background(),
		func(context.Context) error { steps = append(steps, "readiness"); return nil },
		func() error { steps = append(steps, "database"); return nil },
		func() { steps = append(steps, "monitor") },
		func() { steps = append(steps, "anchor") },
	))
	require.Equal(t, []string{"readiness", "database", "monitor", "anchor"}, steps)
}

func TestStopPlatformRelayDatabaseWorkersKeepsCloseLatchFalseUntilAllJoin(t *testing.T) {
	previousReadiness := stopProtectedPlatformRelayAPIReadiness
	previousGeneration := stopPlatformGenerationDatabaseWorkers
	previousProvider := stopPlatformProviderDatabaseWorkers
	previousSystem := stopPlatformSystemTaskDatabaseRunner
	t.Cleanup(func() {
		stopProtectedPlatformRelayAPIReadiness = previousReadiness
		stopPlatformGenerationDatabaseWorkers = previousGeneration
		stopPlatformProviderDatabaseWorkers = previousProvider
		stopPlatformSystemTaskDatabaseRunner = previousSystem
		resetPlatformRelayDatabaseWorkerStopAttempt(true)
	})
	release := make(chan struct{})
	var readinessCalls atomic.Int32
	var generationCalls atomic.Int32
	var providerCalls atomic.Int32
	var systemCalls atomic.Int32
	stopProtectedPlatformRelayAPIReadiness = func(context.Context) error {
		readinessCalls.Add(1)
		return nil
	}
	stopPlatformGenerationDatabaseWorkers = func(ctx context.Context) error {
		generationCalls.Add(1)
		select {
		case <-release:
			return nil
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	stopPlatformProviderDatabaseWorkers = func(context.Context) error {
		providerCalls.Add(1)
		return nil
	}
	stopPlatformSystemTaskDatabaseRunner = func(context.Context) error {
		systemCalls.Add(1)
		return nil
	}
	resetPlatformRelayDatabaseWorkerStopAttempt(false)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	err := stopPlatformRelayDatabaseWorkers(ctx)
	cancel()
	require.Error(t, err)
	require.False(t, platformRelayDatabaseWorkersJoined.Load())
	platformRelayDatabaseWorkerStopMu.Lock()
	firstAttempt := platformRelayDatabaseWorkerStopCurrent
	platformRelayDatabaseWorkerStopMu.Unlock()
	require.NotNil(t, firstAttempt)

	close(release)
	ctx, cancel = context.WithTimeout(context.Background(), time.Second)
	require.NoError(t, stopPlatformRelayDatabaseWorkers(ctx))
	cancel()
	require.True(t, platformRelayDatabaseWorkersJoined.Load())
	platformRelayDatabaseWorkerStopMu.Lock()
	secondAttempt := platformRelayDatabaseWorkerStopCurrent
	platformRelayDatabaseWorkerStopMu.Unlock()
	require.Same(t, firstAttempt, secondAttempt)
	require.EqualValues(t, 1, readinessCalls.Load())
	require.EqualValues(t, 1, generationCalls.Load())
	require.EqualValues(t, 1, providerCalls.Load())
	require.EqualValues(t, 1, systemCalls.Load())
	for index := range platformRelayDatabaseWorkerStopComponentCount {
		require.Truef(t, firstAttempt.joined[index].Load(), "component %d was not joined", index)
	}
}

func TestStopPlatformRelayDatabaseWorkersStartsAllStopsConcurrently(t *testing.T) {
	previousReadiness := stopProtectedPlatformRelayAPIReadiness
	previousGeneration := stopPlatformGenerationDatabaseWorkers
	previousProvider := stopPlatformProviderDatabaseWorkers
	previousSystem := stopPlatformSystemTaskDatabaseRunner
	t.Cleanup(func() {
		stopProtectedPlatformRelayAPIReadiness = previousReadiness
		stopPlatformGenerationDatabaseWorkers = previousGeneration
		stopPlatformProviderDatabaseWorkers = previousProvider
		stopPlatformSystemTaskDatabaseRunner = previousSystem
		resetPlatformRelayDatabaseWorkerStopAttempt(true)
	})

	readinessStarted := make(chan struct{})
	releaseReadiness := make(chan struct{})
	otherStarted := make(chan string, 3)
	stopProtectedPlatformRelayAPIReadiness = func(ctx context.Context) error {
		close(readinessStarted)
		select {
		case <-releaseReadiness:
			return nil
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	stopPlatformGenerationDatabaseWorkers = func(context.Context) error {
		otherStarted <- "generation"
		return nil
	}
	stopPlatformProviderDatabaseWorkers = func(context.Context) error {
		otherStarted <- "provider"
		return nil
	}
	stopPlatformSystemTaskDatabaseRunner = func(context.Context) error {
		otherStarted <- "system"
		return nil
	}
	resetPlatformRelayDatabaseWorkerStopAttempt(false)

	done := make(chan error, 1)
	go func() { done <- stopPlatformRelayDatabaseWorkers(context.Background()) }()
	select {
	case <-readinessStarted:
	case <-time.After(time.Second):
		t.Fatal("readiness Stop did not start")
	}
	seen := map[string]bool{}
	for range 3 {
		select {
		case name := <-otherStarted:
			seen[name] = true
		case <-time.After(time.Second):
			t.Fatal("a database worker Stop was serialized behind readiness")
		}
	}
	require.Equal(t, map[string]bool{"generation": true, "provider": true, "system": true}, seen)
	select {
	case err := <-done:
		t.Fatalf("coordinator returned before readiness joined: %v", err)
	default:
	}
	require.False(t, platformRelayDatabaseWorkersJoined.Load())
	close(releaseReadiness)
	require.NoError(t, <-done)
	require.True(t, platformRelayDatabaseWorkersJoined.Load())
}

func TestStopPlatformRelayDatabaseWorkersPanicFailsClosedWithoutHanging(t *testing.T) {
	previousReadiness := stopProtectedPlatformRelayAPIReadiness
	previousGeneration := stopPlatformGenerationDatabaseWorkers
	previousProvider := stopPlatformProviderDatabaseWorkers
	previousSystem := stopPlatformSystemTaskDatabaseRunner
	t.Cleanup(func() {
		stopProtectedPlatformRelayAPIReadiness = previousReadiness
		stopPlatformGenerationDatabaseWorkers = previousGeneration
		stopPlatformProviderDatabaseWorkers = previousProvider
		stopPlatformSystemTaskDatabaseRunner = previousSystem
		resetPlatformRelayDatabaseWorkerStopAttempt(true)
	})
	stopProtectedPlatformRelayAPIReadiness = func(context.Context) error { return nil }
	stopPlatformGenerationDatabaseWorkers = func(context.Context) error { panic("synthetic stop secret") }
	stopPlatformProviderDatabaseWorkers = func(context.Context) error { return nil }
	stopPlatformSystemTaskDatabaseRunner = func(context.Context) error { return nil }
	resetPlatformRelayDatabaseWorkerStopAttempt(false)

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	err := stopPlatformRelayDatabaseWorkers(ctx)
	cancel()
	require.Error(t, err)
	require.Contains(t, err.Error(), "component 1 panicked")
	require.NotContains(t, err.Error(), "synthetic stop secret")
	require.False(t, platformRelayDatabaseWorkersJoined.Load())
}

func TestFinalizePlatformRelayLostDatabaseOwnershipNeverClosesUnderActiveHandler(t *testing.T) {
	tracker := service.NewRelayHTTPHandlerTracker()
	require.True(t, tracker.Enter())
	workerStopStarted := make(chan struct{}, 1)
	closed := false
	monitorStopped := false
	anchorClosed := false
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Millisecond)
	err := finalizePlatformRelayLostDatabaseOwnership(
		ctx,
		tracker,
		func(context.Context) error { workerStopStarted <- struct{}{}; return nil },
		func() error { closed = true; return nil },
		func() { monitorStopped = true },
		func() { anchorClosed = true },
	)
	cancel()
	require.Error(t, err)
	select {
	case <-workerStopStarted:
	default:
		t.Fatal("worker stop must start while the handler is blocked")
	}
	require.False(t, closed)
	require.False(t, monitorStopped)
	require.False(t, anchorClosed)

	tracker.Leave()
	ctx, cancel = context.WithTimeout(context.Background(), time.Second)
	require.NoError(t, finalizePlatformRelayLostDatabaseOwnership(
		ctx,
		tracker,
		func(context.Context) error { return nil },
		func() error { closed = true; return nil },
		func() { monitorStopped = true },
		func() { anchorClosed = true },
	))
	cancel()
	require.True(t, closed)
	require.True(t, monitorStopped)
	require.True(t, anchorClosed)
}

func TestFinalizePlatformRelayLostDatabaseOwnershipPreservesAnchorOnCloseFailure(t *testing.T) {
	monitorStopped := false
	anchorClosed := false
	err := finalizePlatformRelayLostDatabaseOwnership(
		context.Background(),
		nil,
		func(context.Context) error { return nil },
		func() error { return errors.New("database close failed") },
		func() { monitorStopped = true },
		func() { anchorClosed = true },
	)
	require.Error(t, err)
	require.False(t, monitorStopped)
	require.False(t, anchorClosed)
}

func TestFinalizePlatformRelayNormalDatabaseOwnershipDoesNotReleaseWhileCloseIsBlocked(t *testing.T) {
	releaseClose := make(chan struct{})
	monitorStopped := false
	anchorReleased := false
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Millisecond)
	err := finalizePlatformRelayNormalDatabaseOwnership(
		ctx,
		nil,
		func(context.Context) error { return nil },
		func() error { <-releaseClose; return nil },
		func() { monitorStopped = true },
		func() bool { return false },
		func() error { anchorReleased = true; return nil },
		func() {},
	)
	cancel()
	require.Error(t, err)
	require.False(t, monitorStopped)
	require.False(t, anchorReleased)
	close(releaseClose)
}

func TestFinalizePlatformRelayNormalDatabaseOwnershipClosesAnchorOnLateLoss(t *testing.T) {
	monitorStopped := false
	anchorReleased := false
	anchorClosed := false
	require.NoError(t, finalizePlatformRelayNormalDatabaseOwnership(
		context.Background(),
		nil,
		func(context.Context) error { return nil },
		func() error { return nil },
		func() { monitorStopped = true },
		func() bool { return true },
		func() error { anchorReleased = true; return nil },
		func() { anchorClosed = true },
	))
	require.True(t, monitorStopped)
	require.False(t, anchorReleased)
	require.True(t, anchorClosed)
}

func TestLifecycleAnchorReleaseFailureKeepsHandleForCloseFallback(t *testing.T) {
	previousLock := platformRelayRuntimeLifecycleLock
	previousRelease := releaseRelayRuntimeLifecycleLockBounded
	t.Cleanup(func() {
		platformRelayRuntimeLifecycleLock = previousLock
		releaseRelayRuntimeLifecycleLockBounded = previousRelease
	})

	lock := &model.RelayLifecycleLock{}
	platformRelayRuntimeLifecycleLock = lock
	releaseRelayRuntimeLifecycleLockBounded = func(got *model.RelayLifecycleLock) error {
		require.Same(t, lock, got)
		return errors.New("release failed")
	}
	closeCalls := 0
	err := finalizePlatformRelayNormalDatabaseOwnership(
		context.Background(), nil,
		func(context.Context) error { return nil },
		func() error { return nil },
		func() {},
		func() bool { return false },
		releasePlatformRelayRuntimeLifecycleLock,
		func() {
			closeCalls++
			closePlatformRelayRuntimeLifecycleLock()
		},
	)
	require.Error(t, err)
	require.Equal(t, 1, closeCalls)
	require.Nil(t, platformRelayRuntimeLifecycleLock)

	lock = &model.RelayLifecycleLock{}
	platformRelayRuntimeLifecycleLock = lock
	releaseRelayRuntimeLifecycleLockBounded = func(got *model.RelayLifecycleLock) error {
		require.Same(t, lock, got)
		return nil
	}
	closeCalls = 0
	require.NoError(t, finalizePlatformRelayNormalDatabaseOwnership(
		context.Background(), nil,
		func(context.Context) error { return nil },
		func() error { return nil },
		func() {},
		func() bool { return false },
		releasePlatformRelayRuntimeLifecycleLock,
		func() {
			closeCalls++
			closePlatformRelayRuntimeLifecycleLock()
		},
	))
	require.Zero(t, closeCalls)
	require.Nil(t, platformRelayRuntimeLifecycleLock)
}

func TestProtectedRelayProhibitsUnmanagedDatabaseBackgroundTasks(t *testing.T) {
	t.Setenv("APP_ENV", "development")
	t.Setenv("DEPLOYMENT_ENV", "development")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	require.False(t, platformRelayUnmanagedDatabaseBackgroundTasksAllowed())

	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
	require.True(t, platformRelayUnmanagedDatabaseBackgroundTasksAllowed())

	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "invalid")
	require.False(t, platformRelayUnmanagedDatabaseBackgroundTasksAllowed(), "invalid protection configuration must fail closed")
}

func TestPublishedRuntimeOwnershipMakesImmediateFailureJoinAllWorkersBeforeClose(t *testing.T) {
	previousLock := platformRelayRuntimeLifecycleLock
	previousFailures := platformRelayRuntimeLifecycleFailures
	previousMonitor := stopPlatformRelayRuntimeLifecycleMonitor
	previousReadiness := stopProtectedPlatformRelayAPIReadiness
	previousGeneration := stopPlatformGenerationDatabaseWorkers
	previousProvider := stopPlatformProviderDatabaseWorkers
	previousSystem := stopPlatformSystemTaskDatabaseRunner
	t.Cleanup(func() {
		platformRelayRuntimeLifecycleLock = previousLock
		platformRelayRuntimeLifecycleFailures = previousFailures
		stopPlatformRelayRuntimeLifecycleMonitor = previousMonitor
		stopProtectedPlatformRelayAPIReadiness = previousReadiness
		stopPlatformGenerationDatabaseWorkers = previousGeneration
		stopPlatformProviderDatabaseWorkers = previousProvider
		stopPlatformSystemTaskDatabaseRunner = previousSystem
		resetPlatformRelayDatabaseWorkerStopAttempt(true)
	})

	var stopped [platformRelayDatabaseWorkerStopComponentCount]atomic.Int32
	stopProtectedPlatformRelayAPIReadiness = func(context.Context) error { stopped[0].Add(1); return nil }
	stopPlatformGenerationDatabaseWorkers = func(context.Context) error { stopped[1].Add(1); return nil }
	stopPlatformProviderDatabaseWorkers = func(context.Context) error { stopped[2].Add(1); return nil }
	stopPlatformSystemTaskDatabaseRunner = func(context.Context) error { stopped[3].Add(1); return nil }
	resetPlatformRelayDatabaseWorkerStopAttempt(true)
	publishPlatformRelayRuntimeDatabaseOwnership(nil, nil, func() {})
	require.False(t, platformRelayDatabaseWorkersJoined.Load())

	closed := false
	require.NoError(t, finalizePlatformRelayNormalDatabaseOwnership(
		context.Background(), nil, stopPlatformRelayDatabaseWorkers,
		func() error {
			for index := range stopped {
				require.Equal(t, int32(1), stopped[index].Load(), "stop component %d must join before CloseDB", index)
			}
			closed = true
			return nil
		},
		func() {},
		func() bool { return false },
		func() error { return nil },
		func() {},
	))
	require.True(t, closed)
}

func TestManagedDatabaseTaskMustJoinBeforePoolCloseAndAnchorRelease(t *testing.T) {
	previousChannel := runPlatformRelayManagedChannelSync
	previousOptions := runPlatformRelayManagedOptionSync
	previousPolicy := runPlatformRelayManagedPolicySync
	previousQuota := runPlatformRelayManagedQuotaData
	previousSubscription := runPlatformRelayManagedSubscriptionReset
	previousReporter := runPlatformRelayManagedSystemReporter
	previousReadiness := stopProtectedPlatformRelayAPIReadiness
	previousGeneration := stopPlatformGenerationDatabaseWorkers
	previousProvider := stopPlatformProviderDatabaseWorkers
	previousSystem := stopPlatformSystemTaskDatabaseRunner
	previousMaster := common.IsMasterNode
	releasePolicy := make(chan struct{})
	var releaseOnce sync.Once
	t.Cleanup(func() {
		releaseOnce.Do(func() { close(releasePolicy) })
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		_ = stopPlatformRelayManagedDatabaseTasks(ctx)
		cancel()
		runPlatformRelayManagedChannelSync = previousChannel
		runPlatformRelayManagedOptionSync = previousOptions
		runPlatformRelayManagedPolicySync = previousPolicy
		runPlatformRelayManagedQuotaData = previousQuota
		runPlatformRelayManagedSubscriptionReset = previousSubscription
		runPlatformRelayManagedSystemReporter = previousReporter
		stopProtectedPlatformRelayAPIReadiness = previousReadiness
		stopPlatformGenerationDatabaseWorkers = previousGeneration
		stopPlatformProviderDatabaseWorkers = previousProvider
		stopPlatformSystemTaskDatabaseRunner = previousSystem
		common.IsMasterNode = previousMaster
		resetPlatformRelayDatabaseWorkerStopAttempt(true)
	})

	waitForCancellation := func(ctx context.Context) { <-ctx.Done() }
	runPlatformRelayManagedChannelSync = func(ctx context.Context, _ int) { waitForCancellation(ctx) }
	runPlatformRelayManagedOptionSync = func(ctx context.Context, _ int) { waitForCancellation(ctx) }
	policyStarted := make(chan struct{})
	runPlatformRelayManagedPolicySync = func(context.Context, int) {
		close(policyStarted)
		<-releasePolicy
	}
	runPlatformRelayManagedQuotaData = waitForCancellation
	runPlatformRelayManagedSubscriptionReset = waitForCancellation
	runPlatformRelayManagedSystemReporter = waitForCancellation
	common.IsMasterNode = false
	require.NoError(t, startPlatformRelayManagedDatabaseTasks(1, false))
	<-policyStarted

	stopProtectedPlatformRelayAPIReadiness = func(context.Context) error { return nil }
	stopPlatformGenerationDatabaseWorkers = func(context.Context) error { return nil }
	stopPlatformProviderDatabaseWorkers = func(context.Context) error { return nil }
	stopPlatformSystemTaskDatabaseRunner = stopPlatformRelaySystemDatabaseWorkers
	resetPlatformRelayDatabaseWorkerStopAttempt(false)
	closed := false
	monitorStopped := false
	anchorReleased := false
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Millisecond)
	err := finalizePlatformRelayNormalDatabaseOwnership(
		ctx,
		nil,
		stopPlatformRelayDatabaseWorkers,
		func() error { closed = true; return nil },
		func() { monitorStopped = true },
		func() bool { return false },
		func() error { anchorReleased = true; return nil },
		func() {},
	)
	cancel()
	require.Error(t, err)
	require.False(t, closed)
	require.False(t, monitorStopped)
	require.False(t, anchorReleased)

	releaseOnce.Do(func() { close(releasePolicy) })
	ctx, cancel = context.WithTimeout(context.Background(), time.Second)
	require.NoError(t, finalizePlatformRelayNormalDatabaseOwnership(
		ctx,
		nil,
		stopPlatformRelayDatabaseWorkers,
		func() error { closed = true; return nil },
		func() { monitorStopped = true },
		func() bool { return false },
		func() error { anchorReleased = true; return nil },
		func() {},
	))
	cancel()
	require.True(t, closed)
	require.True(t, monitorStopped)
	require.True(t, anchorReleased)
}
