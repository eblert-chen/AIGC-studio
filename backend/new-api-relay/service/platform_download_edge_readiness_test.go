package service

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/model"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

type platformDownloadEdgeReadinessTestClock struct {
	mu  sync.Mutex
	now time.Time
}

func (clock *platformDownloadEdgeReadinessTestClock) Now() time.Time {
	clock.mu.Lock()
	defer clock.mu.Unlock()
	return clock.now
}

func (clock *platformDownloadEdgeReadinessTestClock) Set(now time.Time) {
	clock.mu.Lock()
	clock.now = now
	clock.mu.Unlock()
}

type platformDownloadEdgeReadinessFixture struct {
	gateway    *PlatformDownloadEdgeGateway
	clock      *platformDownloadEdgeReadinessTestClock
	proofClock *platformDownloadEdgeReadinessTestClock
	binding    platformRelayDatabaseReleaseProofBinding
}

func newPlatformDownloadEdgeReadinessFixture(t *testing.T) platformDownloadEdgeReadinessFixture {
	t.Helper()
	preparePlatformDownloadEdgeTest(t)
	base := time.Date(2026, time.August, 28, 12, 0, 0, 0, time.UTC)
	clock := &platformDownloadEdgeReadinessTestClock{now: base.Add(time.Second)}
	proofClock := &platformDownloadEdgeReadinessTestClock{now: base}

	previousClock := platformDownloadEdgeClock
	previousVerifiedAt := platformDownloadEdgeDatabaseRoleProofVerifiedAt
	previousValidate := validatePlatformDownloadEdgeDatabaseRoleProof
	previousSameRole := samePlatformDownloadEdgeDatabaseRoleRelease
	previousVerify := verifyPlatformDownloadEdgeDatabaseRoleProof
	previousAttestRole := attestPlatformDownloadEdgeDatabaseRole
	previousAttestRelease := attestPlatformDownloadEdgeDatabaseRelease
	previousSameRelease := samePlatformDownloadEdgeDatabaseRelease
	platformDownloadEdgeClock = clock.Now
	platformDownloadEdgeDatabaseRoleProofVerifiedAt = func(model.RelayDownloadEdgeDatabaseRoleProof) time.Time {
		return proofClock.Now()
	}
	validatePlatformDownloadEdgeDatabaseRoleProof = func(*gorm.DB, model.RelayDownloadEdgeDatabaseRoleProof) error { return nil }
	samePlatformDownloadEdgeDatabaseRoleRelease = func(model.RelayDownloadEdgeDatabaseRoleProof, model.RelayDownloadEdgeDatabaseRoleProof) bool {
		return true
	}
	verifyPlatformDownloadEdgeDatabaseRoleProof = func(*gorm.DB, model.RelayDownloadEdgeDatabaseRoleProof) error { return nil }
	attestPlatformDownloadEdgeDatabaseRole = func(context.Context, *gorm.DB) (model.RelayDownloadEdgeDatabaseRoleProof, error) {
		return model.RelayDownloadEdgeDatabaseRoleProof{}, nil
	}
	pool, err := model.DB.DB()
	require.NoError(t, err)
	binding := platformRelayDatabaseReleaseProofBinding{
		pool: pool, consumer: PlatformRelaySecretIsolationConsumerEdge,
	}
	binding.digest[0] = 1
	attestPlatformDownloadEdgeDatabaseRelease = func(
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
	samePlatformDownloadEdgeDatabaseRelease = samePlatformRelayDatabaseReleaseProofBinding
	t.Cleanup(func() {
		platformDownloadEdgeClock = previousClock
		platformDownloadEdgeDatabaseRoleProofVerifiedAt = previousVerifiedAt
		validatePlatformDownloadEdgeDatabaseRoleProof = previousValidate
		samePlatformDownloadEdgeDatabaseRoleRelease = previousSameRole
		verifyPlatformDownloadEdgeDatabaseRoleProof = previousVerify
		attestPlatformDownloadEdgeDatabaseRole = previousAttestRole
		attestPlatformDownloadEdgeDatabaseRelease = previousAttestRelease
		samePlatformDownloadEdgeDatabaseRelease = previousSameRelease
	})

	lifecycle, cancelLifecycle := context.WithCancel(context.Background())
	gateway, err := NewProtectedPlatformDownloadEdgeGateway(
		lifecycle,
		testProductionPlatformDownloadEdgeConfig(t),
		model.DB,
		model.RelayDownloadEdgeDatabaseRoleProof{},
	)
	require.NoError(t, err)
	t.Cleanup(func() {
		cancelLifecycle()
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		require.NoError(t, gateway.StopProtectedReadinessRefresh(ctx))
		cancel()
	})
	return platformDownloadEdgeReadinessFixture{
		gateway: gateway, clock: clock, proofClock: proofClock, binding: binding,
	}
}

func servePlatformDownloadEdgeReady(gateway *PlatformDownloadEdgeGateway, ctx context.Context) *httptest.ResponseRecorder {
	request := httptest.NewRequest(http.MethodGet, "/health/ready", nil).WithContext(ctx)
	response := httptest.NewRecorder()
	gateway.Handler().ServeHTTP(response, request)
	return response
}

func platformDownloadEdgeReadinessSnapshot(gateway *PlatformDownloadEdgeGateway) (uint64, bool, bool) {
	gateway.readinessMu.Lock()
	defer gateway.readinessMu.Unlock()
	return gateway.readinessGeneration, gateway.readinessProofValid, gateway.readinessRefreshInFlight
}

func TestPlatformDownloadEdgeProtectedReadinessHotPathStaysBelowProbeBudget(t *testing.T) {
	fixture := newPlatformDownloadEdgeReadinessFixture(t)
	var refreshes atomic.Int32
	attestPlatformDownloadEdgeDatabaseRole = func(context.Context, *gorm.DB) (model.RelayDownloadEdgeDatabaseRoleProof, error) {
		refreshes.Add(1)
		return model.RelayDownloadEdgeDatabaseRoleProof{}, nil
	}
	started := time.Now()
	response := servePlatformDownloadEdgeReady(fixture.gateway, context.Background())
	require.Equal(t, http.StatusOK, response.Code, response.Body.String())
	require.Less(t, time.Since(started), 500*time.Millisecond)
	require.Zero(t, refreshes.Load())
}

func TestPlatformDownloadEdgeProtectedReadinessRefreshFailureInvalidatesSingleflight(t *testing.T) {
	fixture := newPlatformDownloadEdgeReadinessFixture(t)
	fixture.clock.Set(fixture.proofClock.Now().Add(6 * time.Minute))
	started := make(chan struct{})
	release := make(chan struct{})
	var calls atomic.Int32
	attestPlatformDownloadEdgeDatabaseRole = func(context.Context, *gorm.DB) (model.RelayDownloadEdgeDatabaseRoleProof, error) {
		if calls.Add(1) == 1 {
			close(started)
		}
		<-release
		return model.RelayDownloadEdgeDatabaseRoleProof{}, errors.New("catalog refresh failed")
	}

	const probes = 16
	var workers sync.WaitGroup
	errorsFound := make(chan string, probes)
	workers.Add(probes)
	for range probes {
		go func() {
			defer workers.Done()
			response := servePlatformDownloadEdgeReady(fixture.gateway, context.Background())
			if response.Code != http.StatusOK {
				errorsFound <- response.Body.String()
			}
		}()
	}
	<-started
	workers.Wait()
	close(errorsFound)
	for body := range errorsFound {
		t.Fatalf("fresh proof rejected while proactive refresh ran: %s", body)
	}
	require.Equal(t, int32(1), calls.Load())
	close(release)
	require.Eventually(t, func() bool {
		_, valid, inFlight := platformDownloadEdgeReadinessSnapshot(fixture.gateway)
		return !valid && !inFlight
	}, time.Second, time.Millisecond)
	response := servePlatformDownloadEdgeReady(fixture.gateway, context.Background())
	require.Equal(t, http.StatusServiceUnavailable, response.Code)
}

func TestPlatformDownloadEdgeProtectedReadinessMaximumAgeFailsClosedAndSingleflights(t *testing.T) {
	fixture := newPlatformDownloadEdgeReadinessFixture(t)
	fixture.clock.Set(fixture.proofClock.Now().Add(platformDownloadEdgeCatalogMaximumAge))
	started := make(chan struct{})
	release := make(chan struct{})
	var calls atomic.Int32
	attestPlatformDownloadEdgeDatabaseRole = func(context.Context, *gorm.DB) (model.RelayDownloadEdgeDatabaseRoleProof, error) {
		if calls.Add(1) == 1 {
			close(started)
		}
		<-release
		return model.RelayDownloadEdgeDatabaseRoleProof{}, errors.New("expired refresh blocked")
	}

	const probes = 16
	var workers sync.WaitGroup
	codes := make(chan int, probes)
	workers.Add(probes)
	for range probes {
		go func() {
			defer workers.Done()
			response := servePlatformDownloadEdgeReady(fixture.gateway, context.Background())
			codes <- response.Code
		}()
	}
	<-started
	workers.Wait()
	close(codes)
	for code := range codes {
		require.Equal(t, http.StatusServiceUnavailable, code)
	}
	require.Equal(t, int32(1), calls.Load())
	close(release)
}

func TestPlatformDownloadEdgeProtectedReadinessClockRollbackLatchesClosed(t *testing.T) {
	fixture := newPlatformDownloadEdgeReadinessFixture(t)
	fixture.clock.Set(fixture.proofClock.Now().Add(-time.Second))
	response := servePlatformDownloadEdgeReady(fixture.gateway, context.Background())
	require.Equal(t, http.StatusServiceUnavailable, response.Code)
	fixture.clock.Set(fixture.proofClock.Now().Add(time.Hour))
	response = servePlatformDownloadEdgeReady(fixture.gateway, context.Background())
	require.Equal(t, http.StatusServiceUnavailable, response.Code)
	fixture.gateway.readinessMu.Lock()
	require.True(t, fixture.gateway.readinessClockFailed)
	require.False(t, fixture.gateway.readinessProofValid)
	fixture.gateway.readinessMu.Unlock()
}

func TestPlatformDownloadEdgeProtectedGatewayRejectsFutureAndExpiredStartupProof(t *testing.T) {
	fixture := newPlatformDownloadEdgeReadinessFixture(t)
	// Stop the valid fixture before exercising constructor-only failures against
	// the same hooked pool and release binding.
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	require.NoError(t, fixture.gateway.StopProtectedReadinessRefresh(ctx))
	cancel()

	for _, test := range []struct {
		name       string
		verifiedAt time.Time
	}{
		{name: "future", verifiedAt: fixture.clock.Now().Add(time.Second)},
		{name: "expired", verifiedAt: fixture.clock.Now().Add(-platformDownloadEdgeCatalogMaximumAge)},
	} {
		t.Run(test.name, func(t *testing.T) {
			fixture.proofClock.Set(test.verifiedAt)
			gateway, err := NewProtectedPlatformDownloadEdgeGateway(
				context.Background(), testProductionPlatformDownloadEdgeConfig(t), model.DB,
				model.RelayDownloadEdgeDatabaseRoleProof{},
			)
			require.Nil(t, gateway)
			require.ErrorContains(t, err, "outside the freshness window")
		})
	}
}

func TestPlatformDownloadEdgeProtectedReadinessRequestCancellationDoesNotPoisonProof(t *testing.T) {
	fixture := newPlatformDownloadEdgeReadinessFixture(t)
	entered := make(chan struct{})
	verifyPlatformDownloadEdgeDatabaseRoleProof = func(db *gorm.DB, _ model.RelayDownloadEdgeDatabaseRoleProof) error {
		close(entered)
		<-db.Statement.Context.Done()
		return db.Statement.Context.Err()
	}
	var refreshes atomic.Int32
	attestPlatformDownloadEdgeDatabaseRole = func(context.Context, *gorm.DB) (model.RelayDownloadEdgeDatabaseRoleProof, error) {
		refreshes.Add(1)
		return model.RelayDownloadEdgeDatabaseRoleProof{}, nil
	}
	beforeGeneration, beforeValid, _ := platformDownloadEdgeReadinessSnapshot(fixture.gateway)
	requestContext, cancelRequest := context.WithCancel(context.Background())
	done := make(chan *httptest.ResponseRecorder, 1)
	go func() { done <- servePlatformDownloadEdgeReady(fixture.gateway, requestContext) }()
	<-entered
	cancelRequest()
	response := <-done
	require.Equal(t, http.StatusServiceUnavailable, response.Code)
	afterGeneration, afterValid, inFlight := platformDownloadEdgeReadinessSnapshot(fixture.gateway)
	require.Equal(t, beforeGeneration, afterGeneration)
	require.Equal(t, beforeValid, afterValid)
	require.False(t, inFlight)
	require.Zero(t, refreshes.Load())
}

func TestPlatformDownloadEdgeProtectedReadinessLiveDriftInvalidatesAndStaleRefreshCannotReopen(t *testing.T) {
	fixture := newPlatformDownloadEdgeReadinessFixture(t)
	fixture.clock.Set(fixture.proofClock.Now().Add(6 * time.Minute))
	refreshStarted := make(chan struct{})
	releaseRefresh := make(chan struct{})
	var refreshCalls atomic.Int32
	attestPlatformDownloadEdgeDatabaseRole = func(context.Context, *gorm.DB) (model.RelayDownloadEdgeDatabaseRoleProof, error) {
		call := refreshCalls.Add(1)
		if call == 1 {
			close(refreshStarted)
			<-releaseRefresh
			fixture.proofClock.Set(fixture.clock.Now())
			return model.RelayDownloadEdgeDatabaseRoleProof{}, nil
		}
		return model.RelayDownloadEdgeDatabaseRoleProof{}, errors.New("second refresh remains closed")
	}
	var drift atomic.Bool
	verifyPlatformDownloadEdgeDatabaseRoleProof = func(*gorm.DB, model.RelayDownloadEdgeDatabaseRoleProof) error {
		if drift.Load() {
			return errors.New("live ACL drift")
		}
		return nil
	}
	response := servePlatformDownloadEdgeReady(fixture.gateway, context.Background())
	require.Equal(t, http.StatusOK, response.Code)
	<-refreshStarted
	drift.Store(true)
	response = servePlatformDownloadEdgeReady(fixture.gateway, context.Background())
	require.Equal(t, http.StatusServiceUnavailable, response.Code)
	close(releaseRefresh)
	require.Eventually(t, func() bool {
		_, valid, inFlight := platformDownloadEdgeReadinessSnapshot(fixture.gateway)
		return !valid && !inFlight
	}, time.Second, time.Millisecond)
	drift.Store(false)
	response = servePlatformDownloadEdgeReady(fixture.gateway, context.Background())
	require.Equal(t, http.StatusServiceUnavailable, response.Code)
	require.Eventually(t, func() bool { return refreshCalls.Load() == 2 }, time.Second, time.Millisecond)
}

func TestPlatformDownloadEdgeProtectedReadinessReleaseBindingDriftFailsClosed(t *testing.T) {
	fixture := newPlatformDownloadEdgeReadinessFixture(t)
	fixture.clock.Set(fixture.proofClock.Now().Add(6 * time.Minute))
	fixture.proofClock.Set(fixture.clock.Now())
	attestPlatformDownloadEdgeDatabaseRole = func(context.Context, *gorm.DB) (model.RelayDownloadEdgeDatabaseRoleProof, error) {
		return model.RelayDownloadEdgeDatabaseRoleProof{}, nil
	}
	releaseStarted := make(chan struct{}, 1)
	releaseRefresh := make(chan struct{})
	attestPlatformDownloadEdgeDatabaseRelease = func(
		_ context.Context, _ *gorm.DB, consumer string, status model.RelaySchemaStatus,
	) (platformRelayDatabaseReleaseProofBinding, error) {
		select {
		case releaseStarted <- struct{}{}:
		default:
		}
		<-releaseRefresh
		drifted := fixture.binding
		drifted.consumer = consumer
		drifted.schemaStatus = status
		drifted.digest[0] = 2
		return drifted, nil
	}
	response := servePlatformDownloadEdgeReady(fixture.gateway, context.Background())
	require.Equal(t, http.StatusOK, response.Code)
	<-releaseStarted
	close(releaseRefresh)
	require.Eventually(t, func() bool {
		_, valid, inFlight := platformDownloadEdgeReadinessSnapshot(fixture.gateway)
		return !valid && !inFlight
	}, time.Second, time.Millisecond)
	response = servePlatformDownloadEdgeReady(fixture.gateway, context.Background())
	require.Equal(t, http.StatusServiceUnavailable, response.Code)
}

func TestPlatformDownloadEdgeProtectedReadinessShutdownCancelsAndJoinsRefresh(t *testing.T) {
	fixture := newPlatformDownloadEdgeReadinessFixture(t)
	fixture.clock.Set(fixture.proofClock.Now().Add(6 * time.Minute))
	started := make(chan struct{})
	exited := make(chan struct{})
	attestPlatformDownloadEdgeDatabaseRole = func(ctx context.Context, _ *gorm.DB) (model.RelayDownloadEdgeDatabaseRoleProof, error) {
		close(started)
		<-ctx.Done()
		close(exited)
		return model.RelayDownloadEdgeDatabaseRoleProof{}, ctx.Err()
	}
	response := servePlatformDownloadEdgeReady(fixture.gateway, context.Background())
	require.Equal(t, http.StatusOK, response.Code)
	<-started
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	require.NoError(t, fixture.gateway.StopProtectedReadinessRefresh(ctx))
	cancel()
	select {
	case <-exited:
	default:
		t.Fatal("readiness refresh did not exit before Stop returned")
	}
}
