package service

import (
	"context"
	"errors"
	"sync"
	"time"
)

// RelayHTTPHandlerTracker makes lifecycle-loss draining explicit and bounded.
// BeginDrain atomically prevents any new handler from entering; Wait then joins
// every handler that entered before the gate closed. It does not depend on
// net/http's connection bookkeeping, so a forced Server.Close can still be
// followed by a proof that request goroutines no longer use the database pool.
type RelayHTTPHandlerTracker struct {
	mu        sync.Mutex
	accepting bool
	active    int
	drained   chan struct{}
}

// RelayRuntimeDrainOutcome records whether a normal signal-driven drain was
// upgraded after the PostgreSQL lifecycle anchor was lost. The deadline starts
// at the first observed loss, not at the earlier SIGTERM, so callers can spend
// only the remaining hard-loss budget closing the database pool.
type RelayRuntimeDrainOutcome struct {
	LifecycleLoss         error
	LifecycleLossDeadline time.Time
}

// DrainRelayHTTPAndWorkersWithLifecycleEscalation starts exactly one HTTP and
// worker drain. While a normal graceful drain is in progress it continues to
// consume the lifecycle-loss channel and inspect the server-owned loss bit. A
// loss dynamically shortens that same drain; it never starts a second Stop or
// Shutdown invocation. This closes the SIGTERM-first race where an anchor could
// disappear during a long normal drain and leave the process waiting for the
// normal timeout instead of the lifecycle-loss hard deadline.
func DrainRelayHTTPAndWorkersWithLifecycleEscalation(
	normalTimeout time.Duration,
	gracefulWindow time.Duration,
	lossHardWindow time.Duration,
	lossDrainWindow time.Duration,
	initialLifecycleLoss error,
	lifecycleFailures <-chan error,
	lifecycleLost func() bool,
	tracker *RelayHTTPHandlerTracker,
	shutdownServer func(context.Context) error,
	closeServer func() error,
	stopWorkers func(context.Context) error,
) (RelayRuntimeDrainOutcome, error) {
	var outcome RelayRuntimeDrainOutcome
	if normalTimeout <= 0 || gracefulWindow <= 0 || lossHardWindow <= 0 || lossDrainWindow <= 0 ||
		lossDrainWindow >= lossHardWindow || lifecycleLost == nil {
		return outcome, errors.New("Relay runtime drain escalation configuration is invalid")
	}

	normalContext, cancelNormal := context.WithTimeout(context.Background(), normalTimeout)
	defer cancelNormal()
	drainContext, cancelDrain := context.WithCancel(normalContext)
	defer cancelDrain()

	var lossCancelTimer *time.Timer
	recordLifecycleLoss := func(failure error) {
		if outcome.LifecycleLoss != nil {
			return
		}
		if failure == nil {
			failure = errors.New("Relay runtime lifecycle lock was lost")
		}
		outcome.LifecycleLoss = failure
		observedAt := time.Now()
		outcome.LifecycleLossDeadline = observedAt.Add(lossHardWindow)
		lossCancelTimer = time.AfterFunc(lossDrainWindow, cancelDrain)
	}
	if initialLifecycleLoss != nil {
		recordLifecycleLoss(initialLifecycleLoss)
	} else if lifecycleLost() {
		recordLifecycleLoss(nil)
	}

	drainDone := make(chan error, 1)
	go func() {
		drainDone <- DrainRelayHTTPAndWorkers(
			drainContext,
			gracefulWindow,
			tracker,
			shutdownServer,
			closeServer,
			stopWorkers,
		)
	}()

	pollInterval := 100 * time.Millisecond
	if candidate := lossDrainWindow / 4; candidate > 0 && candidate < pollInterval {
		pollInterval = candidate
	}
	lossPoll := time.NewTicker(pollInterval)
	defer lossPoll.Stop()
	for {
		select {
		case drainErr := <-drainDone:
			if outcome.LifecycleLoss == nil && lifecycleLost() {
				recordLifecycleLoss(nil)
			}
			if lossCancelTimer != nil {
				lossCancelTimer.Stop()
			}
			return outcome, drainErr
		case failure, open := <-lifecycleFailures:
			if !open {
				lifecycleFailures = nil
				continue
			}
			recordLifecycleLoss(failure)
		case <-lossPoll.C:
			if lifecycleLost() {
				recordLifecycleLoss(nil)
			}
		}
	}
}

// DrainRelayHTTPAndWorkers closes admission, gives active handlers a graceful
// window, force-closes their connections if needed, joins the explicit handler
// tracker and database workers, and returns before ctx's single hard deadline.
// The caller closes the database pool only after this function returns.
func DrainRelayHTTPAndWorkers(
	ctx context.Context,
	gracefulWindow time.Duration,
	tracker *RelayHTTPHandlerTracker,
	shutdownServer func(context.Context) error,
	closeServer func() error,
	stopWorkers func(context.Context) error,
) error {
	if ctx == nil || gracefulWindow <= 0 || tracker == nil || shutdownServer == nil || closeServer == nil || stopWorkers == nil {
		return errors.New("Relay HTTP lifecycle drain configuration is invalid")
	}
	tracker.BeginDrain()

	workerDone := make(chan error, 1)
	go func() { workerDone <- stopWorkers(ctx) }()
	graceContext, cancelGrace := context.WithTimeout(ctx, gracefulWindow)
	defer cancelGrace()
	serverDone := make(chan error, 1)
	go func() { serverDone <- shutdownServer(graceContext) }()
	handlersDone := make(chan error, 1)
	go func() { handlersDone <- tracker.Wait(ctx) }()

	var combined error
	serverJoined := false
	handlersJoined := false
	forcedClosed := false
	graceDone := graceContext.Done()
	for !serverJoined || !handlersJoined {
		select {
		case err := <-serverDone:
			serverJoined = true
			combined = errors.Join(combined, err)
			if err != nil && !forcedClosed {
				forcedClosed = true
				combined = errors.Join(combined, closeServer())
			}
		case err := <-handlersDone:
			handlersJoined = true
			combined = errors.Join(combined, err)
		case <-graceDone:
			graceDone = nil
			combined = errors.Join(combined, graceContext.Err())
			if !forcedClosed {
				forcedClosed = true
				combined = errors.Join(combined, closeServer())
			}
		case <-ctx.Done():
			if !forcedClosed {
				combined = errors.Join(combined, closeServer())
			}
			return errors.Join(combined, ctx.Err())
		}
	}
	select {
	case err := <-workerDone:
		combined = errors.Join(combined, err)
	case <-ctx.Done():
		combined = errors.Join(combined, ctx.Err())
	}
	return combined
}

func NewRelayHTTPHandlerTracker() *RelayHTTPHandlerTracker {
	return &RelayHTTPHandlerTracker{accepting: true, drained: make(chan struct{})}
}

func (tracker *RelayHTTPHandlerTracker) Enter() bool {
	if tracker == nil {
		return false
	}
	tracker.mu.Lock()
	defer tracker.mu.Unlock()
	if !tracker.accepting {
		return false
	}
	tracker.active++
	return true
}

func (tracker *RelayHTTPHandlerTracker) Leave() {
	if tracker == nil {
		return
	}
	tracker.mu.Lock()
	defer tracker.mu.Unlock()
	if tracker.active <= 0 {
		return
	}
	tracker.active--
	if !tracker.accepting && tracker.active == 0 {
		select {
		case <-tracker.drained:
		default:
			close(tracker.drained)
		}
	}
}

func (tracker *RelayHTTPHandlerTracker) BeginDrain() {
	if tracker == nil {
		return
	}
	tracker.mu.Lock()
	defer tracker.mu.Unlock()
	if !tracker.accepting {
		return
	}
	tracker.accepting = false
	if tracker.active == 0 {
		close(tracker.drained)
	}
}

func (tracker *RelayHTTPHandlerTracker) Wait(ctx context.Context) error {
	if tracker == nil || ctx == nil {
		return errors.New("Relay HTTP drain context is required")
	}
	tracker.mu.Lock()
	drained := tracker.drained
	accepting := tracker.accepting
	tracker.mu.Unlock()
	if accepting {
		return errors.New("Relay HTTP admission must be drained before waiting")
	}
	select {
	case <-drained:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
