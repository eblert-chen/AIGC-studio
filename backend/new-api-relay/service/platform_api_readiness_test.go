package service

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/model"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

type platformRelayAPIReadinessTestClock struct {
	mu  sync.Mutex
	now time.Time
}

func (clock *platformRelayAPIReadinessTestClock) Now() time.Time {
	clock.mu.Lock()
	defer clock.mu.Unlock()
	return clock.now
}

func (clock *platformRelayAPIReadinessTestClock) Set(now time.Time) {
	clock.mu.Lock()
	clock.now = now
	clock.mu.Unlock()
}

type platformRelayAPIReadinessFixture struct {
	readiness  *PlatformRelayAPIReadiness
	clock      *platformRelayAPIReadinessTestClock
	proofClock *platformRelayAPIReadinessTestClock
	binding    platformRelayDatabaseReleaseProofBinding
}

func newPlatformRelayAPIReadinessFixture(t *testing.T) platformRelayAPIReadinessFixture {
	t.Helper()
	preparePlatformDownloadEdgeTest(t)
	base := time.Date(2026, time.August, 28, 12, 0, 0, 0, time.UTC)
	clock := &platformRelayAPIReadinessTestClock{now: base.Add(time.Second)}
	proofClock := &platformRelayAPIReadinessTestClock{now: base}

	previousClock := platformRelayAPIReadinessClock
	previousVerifiedAt := platformRelayAPIDatabaseRoleProofVerifiedAt
	previousValidate := validatePlatformRelayAPIDatabaseRoleProof
	previousSameRole := samePlatformRelayAPIDatabaseRoleRelease
	previousVerify := verifyPlatformRelayAPIDatabaseRoleProof
	previousAttestRole := attestPlatformRelayAPIDatabaseRole
	previousAttestRelease := attestPlatformRelayAPIDatabaseRelease
	previousSameRelease := samePlatformRelayAPIDatabaseRelease
	platformRelayAPIReadinessClock = clock.Now
	platformRelayAPIDatabaseRoleProofVerifiedAt = func(model.RelayRuntimeDatabaseRoleProof) time.Time {
		return proofClock.Now()
	}
	validatePlatformRelayAPIDatabaseRoleProof = func(*gorm.DB, model.RelayRuntimeDatabaseRoleProof) error { return nil }
	samePlatformRelayAPIDatabaseRoleRelease = func(model.RelayRuntimeDatabaseRoleProof, model.RelayRuntimeDatabaseRoleProof) bool {
		return true
	}
	verifyPlatformRelayAPIDatabaseRoleProof = func(*gorm.DB, model.RelayRuntimeDatabaseRoleProof) (model.RelayRuntimeDatabaseRoleStatus, error) {
		return model.RelayRuntimeDatabaseRoleStatus{Required: true, State: "healthy", Role: "relay_runtime"}, nil
	}
	attestPlatformRelayAPIDatabaseRole = func(context.Context, *gorm.DB) (model.RelayRuntimeDatabaseRoleProof, error) {
		return model.RelayRuntimeDatabaseRoleProof{}, nil
	}
	pool, err := model.DB.DB()
	require.NoError(t, err)
	binding := platformRelayDatabaseReleaseProofBinding{pool: pool, consumer: PlatformRelaySecretIsolationConsumerAPI}
	binding.digest[0] = 1
	attestPlatformRelayAPIDatabaseRelease = func(
		_ context.Context,
		_ *gorm.DB,
		consumer string,
		status model.RelaySchemaStatus,
	) (platformRelayDatabaseReleaseProofBinding, error) {
		candidate := binding
		candidate.consumer = consumer
		candidate.schemaStatus = status
		return candidate, nil
	}
	samePlatformRelayAPIDatabaseRelease = samePlatformRelayDatabaseReleaseProofBinding
	t.Cleanup(func() {
		platformRelayAPIReadinessClock = previousClock
		platformRelayAPIDatabaseRoleProofVerifiedAt = previousVerifiedAt
		validatePlatformRelayAPIDatabaseRoleProof = previousValidate
		samePlatformRelayAPIDatabaseRoleRelease = previousSameRole
		verifyPlatformRelayAPIDatabaseRoleProof = previousVerify
		attestPlatformRelayAPIDatabaseRole = previousAttestRole
		attestPlatformRelayAPIDatabaseRelease = previousAttestRelease
		samePlatformRelayAPIDatabaseRelease = previousSameRelease
	})

	lifecycle, cancelLifecycle := context.WithCancel(context.Background())
	readiness, err := NewProtectedPlatformRelayAPIReadiness(lifecycle, model.DB, model.RelayRuntimeDatabaseRoleProof{})
	require.NoError(t, err)
	t.Cleanup(func() {
		cancelLifecycle()
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		require.NoError(t, readiness.Stop(ctx))
		cancel()
	})
	return platformRelayAPIReadinessFixture{
		readiness: readiness, clock: clock, proofClock: proofClock, binding: binding,
	}
}

func platformRelayAPIReadinessState(readiness *PlatformRelayAPIReadiness) (uint64, bool, bool) {
	readiness.mu.Lock()
	defer readiness.mu.Unlock()
	return readiness.generation, readiness.proofValid, readiness.refreshInFlight
}

func TestPlatformRelayAPIReadinessHotPathDoesNotRunFullAttestation(t *testing.T) {
	fixture := newPlatformRelayAPIReadinessFixture(t)
	var refreshes atomic.Int32
	attestPlatformRelayAPIDatabaseRole = func(context.Context, *gorm.DB) (model.RelayRuntimeDatabaseRoleProof, error) {
		refreshes.Add(1)
		return model.RelayRuntimeDatabaseRoleProof{}, nil
	}
	started := time.Now()
	_, role, err := fixture.readiness.Verify(context.Background())
	require.NoError(t, err)
	require.Equal(t, "healthy", role.State)
	require.Less(t, time.Since(started), 500*time.Millisecond)
	require.Zero(t, refreshes.Load())
}

func TestPlatformRelayAPIReadinessRefreshFailureInvalidatesSingleflight(t *testing.T) {
	fixture := newPlatformRelayAPIReadinessFixture(t)
	fixture.clock.Set(fixture.proofClock.Now().Add(6 * time.Minute))
	started := make(chan struct{})
	release := make(chan struct{})
	var calls atomic.Int32
	attestPlatformRelayAPIDatabaseRole = func(context.Context, *gorm.DB) (model.RelayRuntimeDatabaseRoleProof, error) {
		if calls.Add(1) == 1 {
			close(started)
		}
		<-release
		return model.RelayRuntimeDatabaseRoleProof{}, errors.New("catalog refresh failed")
	}

	const probes = 16
	var workers sync.WaitGroup
	errorsFound := make(chan error, probes)
	workers.Add(probes)
	for range probes {
		go func() {
			defer workers.Done()
			_, _, err := fixture.readiness.Verify(context.Background())
			errorsFound <- err
		}()
	}
	<-started
	workers.Wait()
	close(errorsFound)
	for err := range errorsFound {
		require.NoError(t, err)
	}
	require.Equal(t, int32(1), calls.Load())
	close(release)
	require.Eventually(t, func() bool {
		_, valid, inFlight := platformRelayAPIReadinessState(fixture.readiness)
		return !valid && !inFlight
	}, time.Second, time.Millisecond)
	_, _, err := fixture.readiness.Verify(context.Background())
	require.Error(t, err)
}

func TestPlatformRelayAPIReadinessMaximumAgeFailsClosedAndSingleflights(t *testing.T) {
	fixture := newPlatformRelayAPIReadinessFixture(t)
	fixture.clock.Set(fixture.proofClock.Now().Add(platformRelayAPICatalogMaximumAge))
	started := make(chan struct{})
	release := make(chan struct{})
	var calls atomic.Int32
	attestPlatformRelayAPIDatabaseRole = func(context.Context, *gorm.DB) (model.RelayRuntimeDatabaseRoleProof, error) {
		if calls.Add(1) == 1 {
			close(started)
		}
		<-release
		return model.RelayRuntimeDatabaseRoleProof{}, errors.New("expired refresh blocked")
	}

	const probes = 16
	var workers sync.WaitGroup
	errorsFound := make(chan error, probes)
	workers.Add(probes)
	for range probes {
		go func() {
			defer workers.Done()
			_, _, err := fixture.readiness.Verify(context.Background())
			errorsFound <- err
		}()
	}
	<-started
	workers.Wait()
	close(errorsFound)
	for err := range errorsFound {
		require.Error(t, err)
	}
	require.Equal(t, int32(1), calls.Load())
	close(release)
}

func TestPlatformRelayAPIReadinessClockRollbackLatchesClosed(t *testing.T) {
	fixture := newPlatformRelayAPIReadinessFixture(t)
	fixture.clock.Set(fixture.proofClock.Now().Add(-time.Second))
	_, _, err := fixture.readiness.Verify(context.Background())
	require.Error(t, err)
	fixture.clock.Set(fixture.proofClock.Now().Add(time.Hour))
	_, _, err = fixture.readiness.Verify(context.Background())
	require.Error(t, err)
	fixture.readiness.mu.Lock()
	require.True(t, fixture.readiness.clockFailed)
	require.False(t, fixture.readiness.proofValid)
	fixture.readiness.mu.Unlock()
}

func TestPlatformRelayAPIReadinessRejectsFutureAndExpiredStartupProof(t *testing.T) {
	fixture := newPlatformRelayAPIReadinessFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	require.NoError(t, fixture.readiness.Stop(ctx))
	cancel()

	for _, test := range []struct {
		name       string
		verifiedAt time.Time
	}{
		{name: "future", verifiedAt: fixture.clock.Now().Add(time.Second)},
		{name: "expired", verifiedAt: fixture.clock.Now().Add(-platformRelayAPICatalogMaximumAge)},
	} {
		t.Run(test.name, func(t *testing.T) {
			fixture.proofClock.Set(test.verifiedAt)
			readiness, err := NewProtectedPlatformRelayAPIReadiness(
				context.Background(), model.DB, model.RelayRuntimeDatabaseRoleProof{},
			)
			require.Nil(t, readiness)
			require.ErrorContains(t, err, "outside the freshness window")
		})
	}
}

func TestPlatformRelayAPIReadinessRequestCancellationDoesNotPoisonProof(t *testing.T) {
	fixture := newPlatformRelayAPIReadinessFixture(t)
	entered := make(chan struct{})
	verifyPlatformRelayAPIDatabaseRoleProof = func(db *gorm.DB, _ model.RelayRuntimeDatabaseRoleProof) (model.RelayRuntimeDatabaseRoleStatus, error) {
		close(entered)
		<-db.Statement.Context.Done()
		return model.RelayRuntimeDatabaseRoleStatus{Required: true, State: "unavailable"}, db.Statement.Context.Err()
	}
	var refreshes atomic.Int32
	attestPlatformRelayAPIDatabaseRole = func(context.Context, *gorm.DB) (model.RelayRuntimeDatabaseRoleProof, error) {
		refreshes.Add(1)
		return model.RelayRuntimeDatabaseRoleProof{}, nil
	}
	beforeGeneration, beforeValid, _ := platformRelayAPIReadinessState(fixture.readiness)
	requestContext, cancelRequest := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		_, _, err := fixture.readiness.Verify(requestContext)
		done <- err
	}()
	<-entered
	cancelRequest()
	require.ErrorIs(t, <-done, context.Canceled)
	afterGeneration, afterValid, inFlight := platformRelayAPIReadinessState(fixture.readiness)
	require.Equal(t, beforeGeneration, afterGeneration)
	require.Equal(t, beforeValid, afterValid)
	require.False(t, inFlight)
	require.Zero(t, refreshes.Load())
}

func TestPlatformRelayAPIReadinessLiveDriftInvalidatesAndStaleRefreshCannotReopen(t *testing.T) {
	fixture := newPlatformRelayAPIReadinessFixture(t)
	fixture.clock.Set(fixture.proofClock.Now().Add(6 * time.Minute))
	refreshStarted := make(chan struct{})
	releaseRefresh := make(chan struct{})
	var refreshCalls atomic.Int32
	attestPlatformRelayAPIDatabaseRole = func(context.Context, *gorm.DB) (model.RelayRuntimeDatabaseRoleProof, error) {
		call := refreshCalls.Add(1)
		if call == 1 {
			close(refreshStarted)
			<-releaseRefresh
			fixture.proofClock.Set(fixture.clock.Now())
			return model.RelayRuntimeDatabaseRoleProof{}, nil
		}
		return model.RelayRuntimeDatabaseRoleProof{}, errors.New("second refresh remains closed")
	}
	var drift atomic.Bool
	verifyPlatformRelayAPIDatabaseRoleProof = func(*gorm.DB, model.RelayRuntimeDatabaseRoleProof) (model.RelayRuntimeDatabaseRoleStatus, error) {
		if drift.Load() {
			return model.RelayRuntimeDatabaseRoleStatus{Required: true, State: "unavailable"}, errors.New("live ACL drift")
		}
		return model.RelayRuntimeDatabaseRoleStatus{Required: true, State: "healthy", Role: "relay_runtime"}, nil
	}
	_, _, err := fixture.readiness.Verify(context.Background())
	require.NoError(t, err)
	<-refreshStarted
	drift.Store(true)
	_, _, err = fixture.readiness.Verify(context.Background())
	require.Error(t, err)
	close(releaseRefresh)
	require.Eventually(t, func() bool {
		_, valid, inFlight := platformRelayAPIReadinessState(fixture.readiness)
		return !valid && !inFlight
	}, time.Second, time.Millisecond)
	drift.Store(false)
	_, _, err = fixture.readiness.Verify(context.Background())
	require.Error(t, err)
	require.Eventually(t, func() bool { return refreshCalls.Load() == 2 }, time.Second, time.Millisecond)
}

func TestPlatformRelayAPIReadinessLongRefreshCannotReviveStaleProof(t *testing.T) {
	fixture := newPlatformRelayAPIReadinessFixture(t)
	fixture.clock.Set(fixture.proofClock.Now().Add(6 * time.Minute))
	started := make(chan struct{})
	release := make(chan struct{})
	attestPlatformRelayAPIDatabaseRole = func(context.Context, *gorm.DB) (model.RelayRuntimeDatabaseRoleProof, error) {
		close(started)
		<-release
		return model.RelayRuntimeDatabaseRoleProof{}, nil
	}
	_, _, err := fixture.readiness.Verify(context.Background())
	require.NoError(t, err)
	<-started
	fixture.clock.Set(fixture.proofClock.Now().Add(platformRelayAPICatalogMaximumAge + time.Second))
	close(release)
	require.Eventually(t, func() bool {
		_, valid, inFlight := platformRelayAPIReadinessState(fixture.readiness)
		return !valid && !inFlight
	}, time.Second, time.Millisecond)
}

func TestPlatformRelayAPIReadinessShutdownCancelsAndJoinsRefresh(t *testing.T) {
	fixture := newPlatformRelayAPIReadinessFixture(t)
	fixture.clock.Set(fixture.proofClock.Now().Add(6 * time.Minute))
	started := make(chan struct{})
	exited := make(chan struct{})
	attestPlatformRelayAPIDatabaseRole = func(ctx context.Context, _ *gorm.DB) (model.RelayRuntimeDatabaseRoleProof, error) {
		close(started)
		<-ctx.Done()
		close(exited)
		return model.RelayRuntimeDatabaseRoleProof{}, ctx.Err()
	}
	_, _, err := fixture.readiness.Verify(context.Background())
	require.NoError(t, err)
	<-started
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	require.NoError(t, fixture.readiness.Stop(ctx))
	cancel()
	select {
	case <-exited:
	default:
		t.Fatal("Relay API readiness refresh did not exit before Stop returned")
	}
}

func TestPlatformRelayAPIReadinessRegistryHasExplicitInstallAndCleanup(t *testing.T) {
	fixture := newPlatformRelayAPIReadinessFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	require.NoError(t, StopInstalledProtectedPlatformRelayAPIReadiness(ctx))
	cancel()
	require.NoError(t, InstallProtectedPlatformRelayAPIReadiness(fixture.readiness))
	require.Error(t, InstallProtectedPlatformRelayAPIReadiness(fixture.readiness))
	_, role, err := GetProtectedPlatformRelayAPIReadiness(context.Background())
	require.NoError(t, err)
	require.Equal(t, "healthy", role.State)
	ctx, cancel = context.WithTimeout(context.Background(), time.Second)
	require.NoError(t, StopInstalledProtectedPlatformRelayAPIReadiness(ctx))
	cancel()
	_, _, err = GetProtectedPlatformRelayAPIReadiness(context.Background())
	require.ErrorContains(t, err, "not installed")
}

func TestPlatformRelayAPIReadinessRegistryRetainsStoppingManagerUntilJoin(t *testing.T) {
	fixture := newPlatformRelayAPIReadinessFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	require.NoError(t, StopInstalledProtectedPlatformRelayAPIReadiness(ctx))
	cancel()
	require.NoError(t, InstallProtectedPlatformRelayAPIReadiness(fixture.readiness))
	fixture.clock.Set(fixture.proofClock.Now().Add(6 * time.Minute))
	started := make(chan struct{})
	release := make(chan struct{})
	attestPlatformRelayAPIDatabaseRole = func(context.Context, *gorm.DB) (model.RelayRuntimeDatabaseRoleProof, error) {
		close(started)
		<-release
		return model.RelayRuntimeDatabaseRoleProof{}, context.Canceled
	}
	_, _, err := fixture.readiness.Verify(context.Background())
	require.NoError(t, err)
	<-started
	ctx, cancel = context.WithTimeout(context.Background(), 10*time.Millisecond)
	err = StopInstalledProtectedPlatformRelayAPIReadiness(ctx)
	cancel()
	require.Error(t, err)
	platformRelayAPIReadinessRegistry.RLock()
	require.Same(t, fixture.readiness, platformRelayAPIReadinessRegistry.current)
	platformRelayAPIReadinessRegistry.RUnlock()
	close(release)
	ctx, cancel = context.WithTimeout(context.Background(), time.Second)
	require.NoError(t, StopInstalledProtectedPlatformRelayAPIReadiness(ctx))
	cancel()
	platformRelayAPIReadinessRegistry.RLock()
	require.Nil(t, platformRelayAPIReadinessRegistry.current)
	platformRelayAPIReadinessRegistry.RUnlock()
}

func TestPlatformRelayAPIReadinessInstallConflictKeepsNewcomerHandleJoinable(t *testing.T) {
	previousTimeout := platformRelayAPIReadinessInstallStopTimeout
	platformRelayAPIReadinessInstallStopTimeout = 10 * time.Millisecond
	t.Cleanup(func() { platformRelayAPIReadinessInstallStopTimeout = previousTimeout })

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	require.NoError(t, StopInstalledProtectedPlatformRelayAPIReadiness(ctx))
	cancel()

	newReadiness := func(block <-chan struct{}) *PlatformRelayAPIReadiness {
		lifecycle, cancelLifecycle := context.WithCancel(context.Background())
		readiness := &PlatformRelayAPIReadiness{
			clock:     time.Now,
			lifecycle: lifecycle,
			cancel:    cancelLifecycle,
			stopDone:  make(chan struct{}),
		}
		if block != nil {
			readiness.workers.Add(1)
			go func() {
				defer readiness.workers.Done()
				<-block
			}()
		}
		return readiness
	}
	existing := newReadiness(nil)
	releaseNewcomer := make(chan struct{})
	var releaseOnce sync.Once
	t.Cleanup(func() { releaseOnce.Do(func() { close(releaseNewcomer) }) })
	newcomer := newReadiness(releaseNewcomer)
	require.NoError(t, InstallProtectedPlatformRelayAPIReadiness(existing))
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		require.NoError(t, StopInstalledProtectedPlatformRelayAPIReadiness(ctx))
		cancel()
	})

	err := InstallProtectedPlatformRelayAPIReadiness(newcomer)
	require.Error(t, err)
	platformRelayAPIReadinessRegistry.RLock()
	require.Same(t, existing, platformRelayAPIReadinessRegistry.current)
	platformRelayAPIReadinessRegistry.RUnlock()

	ctx, cancel = context.WithTimeout(context.Background(), 10*time.Millisecond)
	require.Error(t, newcomer.Stop(ctx), "the exact uninstalled newcomer must still report its active refresh")
	cancel()
	releaseOnce.Do(func() { close(releaseNewcomer) })
	ctx, cancel = context.WithTimeout(context.Background(), time.Second)
	require.NoError(t, newcomer.Stop(ctx), "the retained newcomer handle must support a later join")
	cancel()
}
