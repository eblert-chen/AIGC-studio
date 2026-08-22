package service

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
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
	Started           bool
	Running           bool
	Stale             bool
	StartedAt         time.Time
	LastHeartbeatAt   time.Time
	LastSuccessAt     time.Time
	LastErrorAt       time.Time
	CurrentErrorCode  string
	ConsecutiveErrors int
}

type platformArtifactCleanupWorkerRuntimeState struct {
	sync.RWMutex
	PlatformArtifactCleanupWorkerStatus
}

type platformArtifactCleanupWorkerOptions struct {
	interval     time.Duration
	initialRetry time.Duration
	maxRetry     time.Duration
	now          func() time.Time
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
	required, err := model.PlatformArtifactCleanupMaintenanceRequired()
	if err != nil {
		return fmt.Errorf("artifact cleanup maintenance could not be determined: %w", err)
	}
	if !required {
		return nil
	}
	store, err := NewPlatformArtifactStoreFromEnvironment()
	if err != nil {
		return fmt.Errorf("artifact cleanup maintenance store is unavailable: %w", err)
	}
	defer safeClosePlatformArtifactStore(store)
	if !store.Persistent() {
		return errors.New("artifact cleanup maintenance requires persistent storage")
	}
	ctx, cancel := context.WithTimeout(context.Background(), platformArtifactCleanupTimeout)
	defer cancel()
	if err := store.Healthcheck(ctx); err != nil {
		return fmt.Errorf("artifact cleanup maintenance store healthcheck failed: %w", err)
	}
	return nil
}

func platformArtifactCleanupWorkerRequired(generationWorkersEnabled bool) (bool, error) {
	if generationWorkersEnabled {
		return true, nil
	}
	return model.PlatformArtifactCleanupMaintenanceRequired()
}

func startPlatformArtifactCleanupWorker() error {
	return platformArtifactCleanupWorkerRuntime.start(
		NewPlatformArtifactStoreFromEnvironment,
		platformArtifactCleanupWorkerOptions{
			interval:     platformArtifactCleanupWorkerInterval,
			initialRetry: platformArtifactCleanupWorkerInitialRetry,
			maxRetry:     platformArtifactCleanupWorkerMaxRetry,
			now:          time.Now,
		},
		PlatformGenerationWorkersEnabled,
	)
}

func (coordinator *platformArtifactCleanupWorkerCoordinator) start(
	factory func() (PlatformArtifactStore, error),
	options platformArtifactCleanupWorkerOptions,
	generationWorkersEnabled func() bool,
) error {
	if factory == nil || generationWorkersEnabled == nil || options.interval <= 0 ||
		options.initialRetry <= 0 || options.maxRetry < options.initialRetry || options.now == nil {
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
	if factory == nil || generationWorkersEnabled == nil || options.interval <= 0 ||
		options.initialRetry <= 0 || options.maxRetry < options.initialRetry || options.now == nil {
		markPlatformArtifactCleanupWorkerError(time.Now().UTC(), "invalid_supervisor_configuration")
		return
	}
	markPlatformArtifactCleanupWorkerStarted(options.now().UTC())
	defer markPlatformArtifactCleanupWorkerStopped(options.now)
	for {
		if err := ctx.Err(); err != nil {
			return
		}
		required, err := platformArtifactCleanupWorkerRequired(generationWorkersEnabled())
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
		runPlatformArtifactCleanupWorker(ctx, factory, options)
		return
	}
}

func runPlatformArtifactCleanupWorker(
	ctx context.Context,
	factory func() (PlatformArtifactStore, error),
	options platformArtifactCleanupWorkerOptions,
) {
	if factory == nil || options.interval <= 0 || options.initialRetry <= 0 ||
		options.maxRetry < options.initialRetry || options.now == nil {
		markPlatformArtifactCleanupWorkerError(time.Now().UTC(), "invalid_worker_configuration")
		return
	}
	markPlatformArtifactCleanupWorkerStarted(options.now().UTC())
	defer markPlatformArtifactCleanupWorkerStopped(options.now)

	var store PlatformArtifactStore
	retryDelay := options.initialRetry
	defer func() { safeClosePlatformArtifactStore(store) }()
	for {
		if err := ctx.Err(); err != nil {
			return
		}
		if store == nil {
			candidate, err := safeCreatePlatformArtifactStore(factory)
			if err != nil || candidate == nil {
				safeClosePlatformArtifactStore(candidate)
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
			retryDelay = options.initialRetry
		}

		_, runErr, panicked := safeRunPlatformArtifactCleanupOnce(ctx, store)
		now := options.now().UTC()
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
	return getPlatformArtifactCleanupWorkerStatusAt(time.Now().UTC())
}

func getPlatformArtifactCleanupWorkerStatusAt(now time.Time) PlatformArtifactCleanupWorkerStatus {
	platformArtifactCleanupWorkerState.RLock()
	status := platformArtifactCleanupWorkerState.PlatformArtifactCleanupWorkerStatus
	platformArtifactCleanupWorkerState.RUnlock()
	status.Stale = !status.Started || !status.Running || status.LastHeartbeatAt.IsZero() ||
		now.Before(status.LastHeartbeatAt) || now.Sub(status.LastHeartbeatAt) > platformArtifactCleanupWorkerStaleAfter
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
	platformArtifactCleanupWorkerState.CurrentErrorCode = ""
	platformArtifactCleanupWorkerState.ConsecutiveErrors = 0
}

func markPlatformArtifactCleanupWorkerError(now time.Time, code string) {
	platformArtifactCleanupWorkerState.Lock()
	defer platformArtifactCleanupWorkerState.Unlock()
	platformArtifactCleanupWorkerState.LastHeartbeatAt = now
	platformArtifactCleanupWorkerState.LastErrorAt = now
	platformArtifactCleanupWorkerState.CurrentErrorCode = code
	platformArtifactCleanupWorkerState.ConsecutiveErrors++
}

func markPlatformArtifactCleanupWorkerStopped(now func() time.Time) {
	platformArtifactCleanupWorkerState.Lock()
	defer platformArtifactCleanupWorkerState.Unlock()
	platformArtifactCleanupWorkerState.Running = false
	platformArtifactCleanupWorkerState.LastHeartbeatAt = now().UTC()
}

func resetPlatformArtifactCleanupWorkerStatusForTest() {
	platformArtifactCleanupWorkerState.Lock()
	platformArtifactCleanupWorkerState.PlatformArtifactCleanupWorkerStatus = PlatformArtifactCleanupWorkerStatus{}
	platformArtifactCleanupWorkerState.Unlock()
}
