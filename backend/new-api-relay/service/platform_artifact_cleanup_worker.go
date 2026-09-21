package service

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
)

const (
	platformArtifactCleanupWorkerInitialRetry = time.Second
	platformArtifactCleanupWorkerMaxRetry     = 30 * time.Second
	// A healthy delete may legitimately consume the store operation timeout.
	// Allow that bound plus one scheduling margin before declaring the loop
	// stale, while still detecting a hung DB/store iteration independently of
	// queue depth.
	platformArtifactCleanupWorkerStaleAfter = platformArtifactCleanupTimeout + 15*time.Second
)

type PlatformArtifactCleanupWorkerStatus struct {
	Started                     bool
	Running                     bool
	Stale                       bool
	StartedAt                   time.Time
	LastHeartbeatAt             time.Time
	LastSuccessAt               time.Time
	LastErrorAt                 time.Time
	CurrentErrorCode            string
	ConsecutiveErrors           int
	StoreConfigured             bool
	StoreKind                   string
	StoreBindingID              string
	StorePersistent             bool
	StoreHealthHealthy          bool
	StoreHealthFresh            bool
	LastStoreHealthAt           time.Time
	LastStoreHealthCheckAt      time.Time
	StoreHealthCurrentErrorCode string
}

type platformArtifactCleanupWorkerRuntimeState struct {
	sync.RWMutex
	PlatformArtifactCleanupWorkerStatus
}

type platformArtifactCleanupWorkerOptions struct {
	interval       time.Duration
	initialRetry   time.Duration
	maxRetry       time.Duration
	healthInterval time.Duration
	healthTimeout  time.Duration
	now            func() time.Time
}

type platformArtifactCleanupWorkerCoordinator struct {
	mu      sync.Mutex
	cancel  context.CancelFunc
	done    chan struct{}
	running bool
}

var (
	platformArtifactCleanupWorkerRuntime platformArtifactCleanupWorkerCoordinator
	platformArtifactCleanupWorkerState   platformArtifactCleanupWorkerRuntimeState
)

// ValidatePlatformArtifactCleanupMaintenanceConfiguration keeps historical
// cleanup obligations alive even when new generation admission is disabled.
// A brand-new database with no intent rows does not require artifact storage,
// preserving the upstream new-api deployment default.
func ValidatePlatformArtifactCleanupMaintenanceConfiguration() error {
	ctx, cancel := context.WithTimeout(context.Background(), platformArtifactCleanupTimeout)
	defer cancel()
	database := platformArtifactReadinessDatabase()
	required, err := platformArtifactReadinessMaintenanceRequired(ctx, database)
	if err != nil {
		return fmt.Errorf("artifact cleanup maintenance could not be determined: %w", err)
	}
	if !required {
		// When generation workers are enabled their validator or supervised
		// cleanup-store constructor publishes the required=true proof. Do not
		// install a conflicting optional proof first.
		if PlatformGenerationWorkersEnabled() {
			return nil
		}
		return publishPlatformArtifactReadinessProof(false, nil, database)
	}
	store, err := platformArtifactReadinessStoreFactory()
	if err != nil {
		return fmt.Errorf("artifact cleanup maintenance store is unavailable: %w", err)
	}
	defer safeClosePlatformArtifactStore(store)
	expectedGeneration, err := capturePlatformArtifactReadinessGeneration(store, database)
	if err != nil {
		return fmt.Errorf("artifact cleanup maintenance store proof is invalid: %w", err)
	}
	if !store.Persistent() {
		return errors.New("artifact cleanup maintenance requires persistent storage")
	}
	if err := store.Healthcheck(ctx); err != nil {
		return fmt.Errorf("artifact cleanup maintenance store healthcheck failed: %w", err)
	}
	return publishPlatformArtifactReadinessProofAfterLiveHealth(ctx, store, expectedGeneration)
}

func platformArtifactCleanupWorkerRequired(
	ctx context.Context,
	generationWorkersEnabled bool,
	database *gorm.DB,
) (bool, error) {
	if generationWorkersEnabled {
		return true, nil
	}
	return platformArtifactReadinessMaintenanceRequired(ctx, database)
}

func startPlatformArtifactCleanupWorker() error {
	return platformArtifactCleanupWorkerRuntime.start(
		NewPlatformArtifactStoreFromEnvironment,
		platformArtifactCleanupWorkerOptions{
			interval:       platformArtifactCleanupWorkerInterval,
			initialRetry:   platformArtifactCleanupWorkerInitialRetry,
			maxRetry:       platformArtifactCleanupWorkerMaxRetry,
			healthInterval: platformArtifactStoreHealthInterval,
			healthTimeout:  platformArtifactStoreHealthTimeout,
			now:            time.Now,
		},
		PlatformGenerationWorkersEnabled,
	)
}

func (coordinator *platformArtifactCleanupWorkerCoordinator) start(
	factory func() (PlatformArtifactStore, error),
	options platformArtifactCleanupWorkerOptions,
	generationWorkersEnabled func() bool,
) error {
	options = normalizePlatformArtifactCleanupWorkerOptions(options)
	if factory == nil || generationWorkersEnabled == nil || options.interval <= 0 ||
		options.initialRetry <= 0 || options.maxRetry < options.initialRetry ||
		options.healthInterval <= 0 || options.healthTimeout <= 0 || options.now == nil {
		return errors.New("artifact cleanup worker coordinator configuration is invalid")
	}
	coordinator.mu.Lock()
	defer coordinator.mu.Unlock()
	if coordinator.running {
		return nil
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	coordinator.cancel = cancel
	coordinator.done = done
	coordinator.running = true
	go func() {
		defer func() {
			coordinator.mu.Lock()
			if coordinator.done == done {
				coordinator.running = false
				coordinator.cancel = nil
				close(done)
			}
			coordinator.mu.Unlock()
		}()
		for {
			if ctx.Err() != nil {
				return
			}
			func() {
				defer func() {
					if recover() != nil {
						markPlatformArtifactCleanupWorkerError(time.Now().UTC(), "supervisor_panicked")
					}
				}()
				runPlatformArtifactCleanupSupervisor(ctx, factory, options, generationWorkersEnabled)
			}()
			// A background supervisor should never return while its parent is
			// healthy. The retry is context-aware so shutdown can join it.
			if !waitPlatformArtifactCleanupWorker(ctx, options.initialRetry) {
				return
			}
		}
	}()
	return nil
}

func (coordinator *platformArtifactCleanupWorkerCoordinator) stop(ctx context.Context) error {
	if ctx == nil {
		return errors.New("artifact cleanup worker stop context is required")
	}
	coordinator.mu.Lock()
	if !coordinator.running {
		coordinator.mu.Unlock()
		return nil
	}
	cancel := coordinator.cancel
	done := coordinator.done
	coordinator.mu.Unlock()
	cancel()
	select {
	case <-done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func runPlatformArtifactCleanupSupervisor(
	ctx context.Context,
	factory func() (PlatformArtifactStore, error),
	options platformArtifactCleanupWorkerOptions,
	generationWorkersEnabled func() bool,
) {
	options = normalizePlatformArtifactCleanupWorkerOptions(options)
	if factory == nil || generationWorkersEnabled == nil || options.interval <= 0 ||
		options.initialRetry <= 0 || options.maxRetry < options.initialRetry ||
		options.healthInterval <= 0 || options.healthTimeout <= 0 || options.now == nil {
		markPlatformArtifactCleanupWorkerError(time.Now().UTC(), "invalid_supervisor_configuration")
		return
	}
	markPlatformArtifactCleanupWorkerStarted(options.now().UTC())
	defer markPlatformArtifactCleanupWorkerStopped(options.now)
	for {
		if err := ctx.Err(); err != nil {
			return
		}
		database := platformArtifactReadinessDatabase()
		required, err := platformArtifactCleanupWorkerRequired(ctx, generationWorkersEnabled(), database)
		if err != nil {
			markPlatformArtifactCleanupWorkerError(options.now().UTC(), "maintenance_query_failed")
			if !waitPlatformArtifactCleanupWorker(ctx, options.initialRetry) {
				return
			}
			continue
		}
		if !required {
			// No historical intent means no storage dependency. Keep polling so a
			// rolling upgrade where an older pod creates the first intent after
			// this snapshot still activates cleanup on this pod.
			markPlatformArtifactCleanupWorkerSuccess(options.now().UTC())
			if !waitPlatformArtifactCleanupWorker(ctx, options.interval) {
				return
			}
			continue
		}
		runPlatformArtifactCleanupWorker(ctx, factory, options, database)
		return
	}
}

func runPlatformArtifactCleanupWorker(
	ctx context.Context,
	factory func() (PlatformArtifactStore, error),
	options platformArtifactCleanupWorkerOptions,
	databases ...*gorm.DB,
) {
	options = normalizePlatformArtifactCleanupWorkerOptions(options)
	if factory == nil || options.interval <= 0 || options.initialRetry <= 0 ||
		options.maxRetry < options.initialRetry || options.healthInterval <= 0 ||
		options.healthTimeout <= 0 || options.now == nil {
		markPlatformArtifactCleanupWorkerError(time.Now().UTC(), "invalid_worker_configuration")
		return
	}
	database, err := selectPlatformArtifactReadinessDatabase(databases...)
	if err != nil {
		markPlatformArtifactCleanupWorkerError(time.Now().UTC(), "invalid_worker_database")
		return
	}
	markPlatformArtifactCleanupWorkerStarted(options.now().UTC())

	var store PlatformArtifactStore
	var nextStoreHealthAt time.Time
	retryDelay := options.initialRetry
	defer func() {
		// Remove the process-visible store/health evidence before closing the
		// process-owned store. A readiness request must never observe a fresh
		// proof for an instance that has already been closed during shutdown.
		markPlatformArtifactCleanupWorkerStopped(options.now)
		safeClosePlatformArtifactStore(store)
	}()
	for {
		if err := ctx.Err(); err != nil {
			return
		}
		if store == nil {
			candidate, err := safeCreatePlatformArtifactStore(factory)
			if err != nil || candidate == nil {
				safeClosePlatformArtifactStore(candidate)
				markPlatformArtifactCleanupWorkerStoreUnavailable()
				markPlatformArtifactCleanupWorkerError(options.now().UTC(), "store_init_failed")
				common.SysError("platform artifact cleanup worker store initialization failed")
				if !waitPlatformArtifactCleanupWorker(ctx, retryDelay) {
					return
				}
				retryDelay *= 2
				if retryDelay > options.maxRetry {
					retryDelay = options.maxRetry
				}
				continue
			}
			store = candidate
			markPlatformArtifactCleanupWorkerStoreReady(candidate)
			nextStoreHealthAt = time.Time{}
			retryDelay = options.initialRetry
		}

		now := options.now()
		if nextStoreHealthAt.IsZero() || !now.Before(nextStoreHealthAt) {
			expectedGeneration, generationErr := capturePlatformArtifactReadinessGeneration(store, database)
			if generationErr != nil {
				markPlatformArtifactCleanupWorkerStoreHealthFailure(now, "store_proof_failed")
				safeClosePlatformArtifactStore(store)
				store = nil
				markPlatformArtifactCleanupWorkerStoreUnavailable()
				if !waitPlatformArtifactCleanupWorker(ctx, retryDelay) {
					return
				}
				continue
			}
			healthContext, cancelHealth := context.WithTimeout(ctx, options.healthTimeout)
			healthErr, healthPanicked := safeHealthcheckPlatformArtifactStore(healthContext, store)
			healthObservedAt := options.now()
			if ctx.Err() != nil {
				cancelHealth()
				return
			}
			if healthErr != nil {
				cancelHealth()
				code := "store_health_failed"
				if healthPanicked {
					code = "store_health_panicked"
				}
				markPlatformArtifactCleanupWorkerStoreHealthFailure(healthObservedAt, code)
				common.SysError("platform artifact cleanup worker store healthcheck failed: " + healthErr.Error())
				safeClosePlatformArtifactStore(store)
				store = nil
				markPlatformArtifactCleanupWorkerStoreUnavailable()
				if !waitPlatformArtifactCleanupWorker(ctx, retryDelay) {
					return
				}
				retryDelay *= 2
				if retryDelay > options.maxRetry {
					retryDelay = options.maxRetry
				}
				continue
			}
			proofErr := publishPlatformArtifactReadinessProofAfterLiveHealth(
				healthContext,
				store,
				expectedGeneration,
			)
			cancelHealth()
			if proofErr != nil {
				markPlatformArtifactCleanupWorkerStoreHealthFailure(healthObservedAt, "store_proof_failed")
				common.SysError("platform artifact cleanup worker store proof failed: " + proofErr.Error())
				safeClosePlatformArtifactStore(store)
				store = nil
				markPlatformArtifactCleanupWorkerStoreUnavailable()
				if !waitPlatformArtifactCleanupWorker(ctx, retryDelay) {
					return
				}
				retryDelay *= 2
				if retryDelay > options.maxRetry {
					retryDelay = options.maxRetry
				}
				continue
			}
			healthConfirmedAt := options.now()
			markPlatformArtifactCleanupWorkerStoreHealthSuccess(
				healthConfirmedAt,
				expectedGeneration.healthEpoch,
			)
			nextStoreHealthAt = healthConfirmedAt.Add(options.healthInterval)
			retryDelay = options.initialRetry
		}

		_, runErr, panicked := safeRunPlatformArtifactCleanupOnce(ctx, store)
		now = options.now().UTC()
		switch {
		case runErr == nil || errors.Is(runErr, gorm.ErrRecordNotFound):
			markPlatformArtifactCleanupWorkerSuccess(now)
		case errors.Is(runErr, context.Canceled) && ctx.Err() != nil:
			return
		default:
			markPlatformArtifactCleanupWorkerError(now, "cleanup_iteration_failed")
			common.SysError("platform artifact cleanup worker: " + runErr.Error())
		}
		if panicked {
			safeClosePlatformArtifactStore(store)
			store = nil
			markPlatformArtifactCleanupWorkerStoreUnavailable()
			if !waitPlatformArtifactCleanupWorker(ctx, retryDelay) {
				return
			}
			continue
		}
		if !waitPlatformArtifactCleanupWorker(ctx, options.interval) {
			return
		}
	}
}

func normalizePlatformArtifactCleanupWorkerOptions(options platformArtifactCleanupWorkerOptions) platformArtifactCleanupWorkerOptions {
	if options.healthInterval <= 0 {
		options.healthInterval = platformArtifactStoreHealthInterval
	}
	if options.healthTimeout <= 0 {
		options.healthTimeout = platformArtifactStoreHealthTimeout
	}
	return options
}

func safeCreatePlatformArtifactStore(
	factory func() (PlatformArtifactStore, error),
) (store PlatformArtifactStore, err error) {
	defer func() {
		if recover() != nil {
			store = nil
			err = errors.New("artifact store factory panicked")
		}
	}()
	return factory()
}

func safeRunPlatformArtifactCleanupOnce(
	ctx context.Context,
	store PlatformArtifactStore,
) (processed bool, err error, panicked bool) {
	defer func() {
		if recover() != nil {
			processed = false
			err = errors.New("artifact cleanup iteration panicked")
			panicked = true
		}
	}()
	processed, err = runPlatformArtifactCleanupOnce(ctx, store, platformArtifactCleanupMaxAttempts)
	return processed, err, false
}

func safeHealthcheckPlatformArtifactStore(
	ctx context.Context,
	store PlatformArtifactStore,
) (err error, panicked bool) {
	defer func() {
		if recover() != nil {
			err = errors.New("artifact store healthcheck panicked")
			panicked = true
		}
	}()
	if store == nil {
		return errors.New("artifact store is unavailable"), false
	}
	return store.Healthcheck(ctx), false
}

func safeClosePlatformArtifactStore(store PlatformArtifactStore) {
	defer func() { _ = recover() }()
	closePlatformGenerationArtifactStore(store)
}

func waitPlatformArtifactCleanupWorker(ctx context.Context, delay time.Duration) bool {
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}

func GetPlatformArtifactCleanupWorkerStatus() PlatformArtifactCleanupWorkerStatus {
	return getPlatformArtifactCleanupWorkerStatusWithClock(func() time.Time { return time.Now().UTC() })
}

func getPlatformArtifactCleanupWorkerStatusAt(now time.Time) PlatformArtifactCleanupWorkerStatus {
	platformArtifactCleanupWorkerState.RLock()
	status := platformArtifactCleanupWorkerState.PlatformArtifactCleanupWorkerStatus
	platformArtifactCleanupWorkerState.RUnlock()
	return classifyPlatformArtifactCleanupWorkerStatusAt(status, now)
}

// getPlatformArtifactCleanupWorkerStatusWithClock samples the clock while the
// worker snapshot is read-locked. A heartbeat writer therefore cannot publish
// T2 after the caller sampled T1 but before the snapshot copy and make normal
// goroutine scheduling look like a future timestamp/clock rollback.
func getPlatformArtifactCleanupWorkerStatusWithClock(clock func() time.Time) PlatformArtifactCleanupWorkerStatus {
	platformArtifactCleanupWorkerState.RLock()
	status := platformArtifactCleanupWorkerState.PlatformArtifactCleanupWorkerStatus
	now := time.Time{}
	if clock != nil {
		now = clock()
	}
	platformArtifactCleanupWorkerState.RUnlock()
	return classifyPlatformArtifactCleanupWorkerStatusAt(status, now)
}

func classifyPlatformArtifactCleanupWorkerStatusAt(
	status PlatformArtifactCleanupWorkerStatus,
	now time.Time,
) PlatformArtifactCleanupWorkerStatus {
	status.Stale = !status.Started || !status.Running || status.LastHeartbeatAt.IsZero() ||
		now.Before(status.LastHeartbeatAt) || now.Sub(status.LastHeartbeatAt) > platformArtifactCleanupWorkerStaleAfter
	status.StoreHealthFresh = status.StoreConfigured && status.StoreHealthHealthy &&
		!status.LastStoreHealthAt.IsZero() && !now.Before(status.LastStoreHealthAt) &&
		now.Sub(status.LastStoreHealthAt) <= platformArtifactStoreHealthFreshAfter
	return status
}

func markPlatformArtifactCleanupWorkerStarted(now time.Time) {
	platformArtifactCleanupWorkerState.Lock()
	defer platformArtifactCleanupWorkerState.Unlock()
	if !platformArtifactCleanupWorkerState.Started {
		platformArtifactCleanupWorkerState.StartedAt = now
	}
	platformArtifactCleanupWorkerState.Started = true
	platformArtifactCleanupWorkerState.Running = true
	platformArtifactCleanupWorkerState.LastHeartbeatAt = now
}

func markPlatformArtifactCleanupWorkerSuccess(now time.Time) {
	platformArtifactCleanupWorkerState.Lock()
	defer platformArtifactCleanupWorkerState.Unlock()
	platformArtifactCleanupWorkerState.LastHeartbeatAt = now
	platformArtifactCleanupWorkerState.LastSuccessAt = now
	if !platformArtifactCleanupWorkerState.StoreConfigured ||
		(platformArtifactCleanupWorkerState.StoreHealthHealthy && platformArtifactCleanupWorkerState.StoreHealthCurrentErrorCode == "") {
		platformArtifactCleanupWorkerState.CurrentErrorCode = ""
		platformArtifactCleanupWorkerState.ConsecutiveErrors = 0
	}
}

func markPlatformArtifactCleanupWorkerError(now time.Time, code string) {
	platformArtifactCleanupWorkerState.Lock()
	defer platformArtifactCleanupWorkerState.Unlock()
	platformArtifactCleanupWorkerState.LastHeartbeatAt = now
	platformArtifactCleanupWorkerState.LastErrorAt = now
	platformArtifactCleanupWorkerState.CurrentErrorCode = code
	platformArtifactCleanupWorkerState.ConsecutiveErrors++
}

func markPlatformArtifactCleanupWorkerStoreReady(store PlatformArtifactStore) {
	platformArtifactCleanupWorkerState.Lock()
	defer platformArtifactCleanupWorkerState.Unlock()
	platformArtifactCleanupWorkerState.StoreConfigured = store != nil
	platformArtifactCleanupWorkerState.StoreHealthHealthy = false
	platformArtifactCleanupWorkerState.StoreHealthFresh = false
	platformArtifactCleanupWorkerState.LastStoreHealthAt = time.Time{}
	platformArtifactCleanupWorkerState.LastStoreHealthCheckAt = time.Time{}
	platformArtifactCleanupWorkerState.StoreHealthCurrentErrorCode = ""
	if store == nil {
		platformArtifactCleanupWorkerState.StoreKind = ""
		platformArtifactCleanupWorkerState.StoreBindingID = ""
		platformArtifactCleanupWorkerState.StorePersistent = false
		return
	}
	platformArtifactCleanupWorkerState.StoreKind = store.Kind()
	platformArtifactCleanupWorkerState.StoreBindingID = store.BindingID()
	platformArtifactCleanupWorkerState.StorePersistent = store.Persistent()
}

func markPlatformArtifactCleanupWorkerStoreHealthSuccess(now time.Time, expectedEpochs ...uint64) {
	platformArtifactCleanupWorkerState.Lock()
	platformArtifactCleanupWorkerState.LastHeartbeatAt = now
	platformArtifactCleanupWorkerState.LastSuccessAt = now
	platformArtifactCleanupWorkerState.LastStoreHealthAt = now
	platformArtifactCleanupWorkerState.LastStoreHealthCheckAt = now
	platformArtifactCleanupWorkerState.StoreHealthHealthy = true
	platformArtifactCleanupWorkerState.StoreHealthFresh = true
	platformArtifactCleanupWorkerState.StoreHealthCurrentErrorCode = ""
	platformArtifactCleanupWorkerState.CurrentErrorCode = ""
	platformArtifactCleanupWorkerState.ConsecutiveErrors = 0
	platformArtifactCleanupWorkerState.Unlock()
	acknowledgePlatformArtifactReadinessStoreHealth(now, expectedEpochs...)
}

func markPlatformArtifactCleanupWorkerStoreHealthFailure(now time.Time, code string) {
	platformArtifactCleanupWorkerState.Lock()
	platformArtifactCleanupWorkerState.LastHeartbeatAt = now
	platformArtifactCleanupWorkerState.LastErrorAt = now
	platformArtifactCleanupWorkerState.LastStoreHealthCheckAt = now
	platformArtifactCleanupWorkerState.StoreHealthHealthy = false
	platformArtifactCleanupWorkerState.StoreHealthFresh = false
	platformArtifactCleanupWorkerState.StoreHealthCurrentErrorCode = code
	platformArtifactCleanupWorkerState.CurrentErrorCode = code
	platformArtifactCleanupWorkerState.ConsecutiveErrors++
	platformArtifactCleanupWorkerState.Unlock()
	invalidatePlatformArtifactReadinessHealthUnconditionally()
}

func markPlatformArtifactCleanupWorkerStoreUnavailable() {
	markPlatformArtifactCleanupWorkerStoreReady(nil)
}

func markPlatformArtifactCleanupWorkerStopped(now func() time.Time) {
	platformArtifactCleanupWorkerState.Lock()
	platformArtifactCleanupWorkerState.Running = false
	platformArtifactCleanupWorkerState.LastHeartbeatAt = now().UTC()
	platformArtifactCleanupWorkerState.StoreConfigured = false
	platformArtifactCleanupWorkerState.StoreKind = ""
	platformArtifactCleanupWorkerState.StoreBindingID = ""
	platformArtifactCleanupWorkerState.StorePersistent = false
	platformArtifactCleanupWorkerState.StoreHealthHealthy = false
	platformArtifactCleanupWorkerState.StoreHealthFresh = false
	platformArtifactCleanupWorkerState.LastStoreHealthAt = time.Time{}
	platformArtifactCleanupWorkerState.LastStoreHealthCheckAt = time.Time{}
	platformArtifactCleanupWorkerState.StoreHealthCurrentErrorCode = ""
	platformArtifactCleanupWorkerState.Unlock()
	invalidatePlatformArtifactReadinessHealthUnconditionally()
}

func resetPlatformArtifactCleanupWorkerStatusForTest() {
	platformArtifactCleanupWorkerState.Lock()
	platformArtifactCleanupWorkerState.PlatformArtifactCleanupWorkerStatus = PlatformArtifactCleanupWorkerStatus{}
	platformArtifactCleanupWorkerState.Unlock()
}
