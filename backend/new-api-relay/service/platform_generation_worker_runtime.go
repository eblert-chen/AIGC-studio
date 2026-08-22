package service

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"sync/atomic"
	"time"

	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
)

const (
	platformGenerationWorkerInterval        = 500 * time.Millisecond
	platformGenerationDispatchFinalizeLimit = 5 * time.Second

	platformGenerationWorkerStageSubmission       = "submission"
	platformGenerationWorkerStageReconciliation   = "reconciliation"
	platformGenerationWorkerStagePoll             = "poll"
	platformGenerationWorkerStageTransfer         = "transfer"
	platformGenerationWorkerStageCallbackBackfill = "callback_backfill"
	platformGenerationWorkerStageCallback         = "callback"
)

var errPlatformGenerationWorkerIterationPanicked = errors.New("platform generation worker iteration panicked")

type PlatformGenerationWorkerRuntimeState string

const (
	PlatformGenerationWorkerStateStopped  PlatformGenerationWorkerRuntimeState = "stopped"
	PlatformGenerationWorkerStateStarting PlatformGenerationWorkerRuntimeState = "starting"
	PlatformGenerationWorkerStateRunning  PlatformGenerationWorkerRuntimeState = "running"
	PlatformGenerationWorkerStateDraining PlatformGenerationWorkerRuntimeState = "draining"
	PlatformGenerationWorkerStateFailed   PlatformGenerationWorkerRuntimeState = "failed"
)

const (
	platformGenerationWorkerStateCodeStopped int32 = iota
	platformGenerationWorkerStateCodeStarting
	platformGenerationWorkerStateCodeRunning
	platformGenerationWorkerStateCodeDraining
	platformGenerationWorkerStateCodeFailed
)

type platformGenerationWorkerOnce func(context.Context) (bool, error)

type platformGenerationDispatchQueue interface {
	AcquireDispatch(context.Context) (*PlatformGenerationDelayQueueLease, error)
	FinalizeDispatch(context.Context, PlatformGenerationDelayQueueLease) error
}

type platformGenerationWorkerDependencies struct {
	queue            platformGenerationDispatchQueue
	submit           func(context.Context, ...int64) (bool, error)
	reconcile        platformGenerationWorkerOnce
	poll             platformGenerationWorkerOnce
	transfer         platformGenerationWorkerOnce
	callbackBackfill platformGenerationWorkerOnce
	callback         platformGenerationWorkerOnce
	reportError      func(string, error)
}

type platformGenerationWorkerStage struct {
	name string
	run  platformGenerationWorkerOnce
}

type platformGenerationWorkerRuntime struct {
	cancel   context.CancelFunc
	done     chan struct{}
	stopOnce sync.Once
}

type platformGenerationWorkerDependencyFactory func() (platformGenerationWorkerDependencies, error)

// platformGenerationWorkerStarter owns the process-local lifecycle. Database
// and Redis claims remain the cross-process concurrency boundary; this guard
// only prevents an accidental second set of six loops in one process.
type platformGenerationWorkerStarter struct {
	mu      sync.Mutex
	started bool
	runtime *platformGenerationWorkerRuntime
	state   atomic.Int32
}

var platformGenerationWorkers platformGenerationWorkerStarter

func (starter *platformGenerationWorkerStarter) State() PlatformGenerationWorkerRuntimeState {
	if starter == nil {
		return PlatformGenerationWorkerStateFailed
	}
	switch starter.state.Load() {
	case platformGenerationWorkerStateCodeStarting:
		return PlatformGenerationWorkerStateStarting
	case platformGenerationWorkerStateCodeRunning:
		return PlatformGenerationWorkerStateRunning
	case platformGenerationWorkerStateCodeDraining:
		return PlatformGenerationWorkerStateDraining
	case platformGenerationWorkerStateCodeFailed:
		return PlatformGenerationWorkerStateFailed
	default:
		return PlatformGenerationWorkerStateStopped
	}
}

func (starter *platformGenerationWorkerStarter) setState(state PlatformGenerationWorkerRuntimeState) {
	if starter == nil {
		return
	}
	code := platformGenerationWorkerStateCodeStopped
	switch state {
	case PlatformGenerationWorkerStateStarting:
		code = platformGenerationWorkerStateCodeStarting
	case PlatformGenerationWorkerStateRunning:
		code = platformGenerationWorkerStateCodeRunning
	case PlatformGenerationWorkerStateDraining:
		code = platformGenerationWorkerStateCodeDraining
	case PlatformGenerationWorkerStateFailed:
		code = platformGenerationWorkerStateCodeFailed
	}
	starter.state.Store(code)
}

func GetPlatformGenerationWorkerRuntimeState() PlatformGenerationWorkerRuntimeState {
	return platformGenerationWorkers.State()
}

func PlatformGenerationWorkersAcceptingNewWork() bool {
	return GetPlatformGenerationWorkerRuntimeState() == PlatformGenerationWorkerStateRunning
}

func defaultPlatformGenerationWorkerDependencies(
	queue platformGenerationDispatchQueue,
) platformGenerationWorkerDependencies {
	return platformGenerationWorkerDependencies{
		queue:            queue,
		submit:           RunPlatformGenerationSubmissionOnce,
		reconcile:        RunPlatformGenerationReconciliationOnce,
		poll:             RunPlatformGenerationPollOnce,
		transfer:         RunPlatformGenerationTransferOnce,
		callbackBackfill: RunPlatformGenerationCallbackBackfillOnce,
		callback:         RunPlatformGenerationCallbackOnce,
		reportError: func(stage string, err error) {
			common.SysError(platformGenerationWorkerLogPrefix(stage) + err.Error())
		},
	}
}

func platformGenerationWorkerLogPrefix(stage string) string {
	switch stage {
	case platformGenerationWorkerStageSubmission:
		return "platform generation submission worker: "
	case platformGenerationWorkerStageReconciliation:
		return "platform generation reconciliation worker: "
	case platformGenerationWorkerStagePoll:
		return "platform generation poll worker: "
	case platformGenerationWorkerStageTransfer:
		return "platform generation artifact worker: "
	case platformGenerationWorkerStageCallbackBackfill:
		return "platform generation callback backfill: "
	case platformGenerationWorkerStageCallback:
		return "platform generation callback worker: "
	default:
		return "platform generation worker: "
	}
}

func (starter *platformGenerationWorkerStarter) Start(
	parent context.Context,
	interval time.Duration,
	factory platformGenerationWorkerDependencyFactory,
) error {
	if starter == nil {
		return errors.New("platform generation worker starter is required")
	}
	starter.mu.Lock()
	defer starter.mu.Unlock()
	if starter.started {
		return nil
	}
	if parent == nil {
		starter.setState(PlatformGenerationWorkerStateFailed)
		return errors.New("platform generation worker parent context is required")
	}
	if factory == nil {
		starter.setState(PlatformGenerationWorkerStateFailed)
		return errors.New("platform generation worker dependency factory is required")
	}
	starter.setState(PlatformGenerationWorkerStateStarting)

	dependencies, err := factory()
	if err != nil {
		// Do not mark the starter as consumed: dependency construction may fail
		// after startup validation because Redis disappeared in between.
		starter.setState(PlatformGenerationWorkerStateFailed)
		return err
	}
	runtime, err := startPlatformGenerationWorkerRuntime(parent, interval, dependencies)
	if err != nil {
		starter.setState(PlatformGenerationWorkerStateFailed)
		return err
	}
	starter.runtime = runtime
	starter.started = true
	// The public admission gate opens only after all six loops have been
	// constructed and their shared runtime exists.
	starter.setState(PlatformGenerationWorkerStateRunning)
	go starter.observeRuntimeExit(runtime)
	return nil
}

func (starter *platformGenerationWorkerStarter) observeRuntimeExit(runtime *platformGenerationWorkerRuntime) {
	if starter == nil || runtime == nil {
		return
	}
	<-runtime.done
	starter.mu.Lock()
	defer starter.mu.Unlock()
	if starter.runtime == runtime {
		starter.setState(PlatformGenerationWorkerStateStopped)
	}
}

func (starter *platformGenerationWorkerStarter) BeginDrain() {
	if starter == nil {
		return
	}
	starter.mu.Lock()
	defer starter.mu.Unlock()
	switch starter.State() {
	case PlatformGenerationWorkerStateStarting, PlatformGenerationWorkerStateRunning:
		starter.setState(PlatformGenerationWorkerStateDraining)
	}
}

func (starter *platformGenerationWorkerStarter) Stop(ctx context.Context) error {
	if starter == nil {
		return nil
	}
	starter.BeginDrain()
	starter.mu.Lock()
	runtime := starter.runtime
	starter.mu.Unlock()
	if runtime == nil {
		return nil
	}
	err := runtime.Stop(ctx)
	if err == nil {
		starter.setState(PlatformGenerationWorkerStateStopped)
	}
	return err
}

// StopPlatformGenerationWorkers is intentionally separate from HTTP-server
// shutdown wiring. Callers can cancel new claims first and then drain the
// server; all accepted work remains recoverable through durable leases.
func StopPlatformGenerationWorkers(ctx context.Context) error {
	if ctx == nil {
		return errors.New("platform generation worker stop context is required")
	}
	results := make(chan error, 2)
	go func() { results <- platformGenerationWorkers.Stop(ctx) }()
	go func() { results <- platformArtifactCleanupWorkerRuntime.stop(ctx) }()
	var combined error
	for range 2 {
		select {
		case err := <-results:
			combined = errors.Join(combined, err)
		case <-ctx.Done():
			return errors.Join(combined, ctx.Err())
		}
	}
	return combined
}

// BeginPlatformGenerationWorkerDrain closes only public generation admission.
// Existing GETs and the internal native-submit bridge stay available while the
// worker runtime is cancelled and the HTTP server drains.
func BeginPlatformGenerationWorkerDrain() {
	platformGenerationWorkers.BeginDrain()
}

func startPlatformGenerationWorkerRuntime(
	parent context.Context,
	interval time.Duration,
	dependencies platformGenerationWorkerDependencies,
) (*platformGenerationWorkerRuntime, error) {
	if parent == nil {
		return nil, errors.New("platform generation worker parent context is required")
	}
	if err := parent.Err(); err != nil {
		return nil, err
	}
	if interval <= 0 {
		return nil, errors.New("platform generation worker interval must be positive")
	}
	if err := validatePlatformGenerationWorkerDependencies(dependencies); err != nil {
		return nil, err
	}

	stages := []platformGenerationWorkerStage{
		{
			name: platformGenerationWorkerStageSubmission,
			run: func(ctx context.Context) (bool, error) {
				return runPlatformGenerationSubmissionDispatchOnce(
					ctx,
					dependencies.queue,
					dependencies.submit,
				)
			},
		},
		{name: platformGenerationWorkerStageReconciliation, run: dependencies.reconcile},
		{name: platformGenerationWorkerStagePoll, run: dependencies.poll},
		{name: platformGenerationWorkerStageTransfer, run: dependencies.transfer},
		{name: platformGenerationWorkerStageCallbackBackfill, run: dependencies.callbackBackfill},
		{name: platformGenerationWorkerStageCallback, run: dependencies.callback},
	}

	runtimeContext, cancel := context.WithCancel(parent)
	runtime := &platformGenerationWorkerRuntime{
		cancel: cancel,
		done:   make(chan struct{}),
	}
	var workers sync.WaitGroup
	workers.Add(len(stages))
	for _, configured := range stages {
		stage := configured
		go func() {
			defer workers.Done()
			runPlatformGenerationWorkerLoop(
				runtimeContext,
				interval,
				stage,
				dependencies.reportError,
			)
		}()
	}
	go func() {
		workers.Wait()
		close(runtime.done)
	}()
	return runtime, nil
}

func validatePlatformGenerationWorkerDependencies(dependencies platformGenerationWorkerDependencies) error {
	if dependencies.queue == nil || dependencies.submit == nil || dependencies.reconcile == nil ||
		dependencies.poll == nil || dependencies.transfer == nil || dependencies.callbackBackfill == nil ||
		dependencies.callback == nil || dependencies.reportError == nil {
		return errors.New("platform generation worker dependencies are incomplete")
	}
	return nil
}

func (runtime *platformGenerationWorkerRuntime) Stop(ctx context.Context) error {
	if runtime == nil {
		return nil
	}
	if ctx == nil {
		return errors.New("platform generation worker stop context is required")
	}
	runtime.stopOnce.Do(runtime.cancel)
	select {
	case <-runtime.done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func runPlatformGenerationWorkerLoop(
	ctx context.Context,
	interval time.Duration,
	stage platformGenerationWorkerStage,
	reportError func(string, error),
) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			_, err := safelyRunPlatformGenerationWorkerStage(ctx, stage.run)
			if platformGenerationWorkerErrorRequiresReport(ctx, err) {
				safelyReportPlatformGenerationWorkerError(reportError, stage.name, err)
			}
		}
	}
}

func safelyRunPlatformGenerationWorkerStage(
	ctx context.Context,
	run platformGenerationWorkerOnce,
) (processed bool, err error) {
	defer func() {
		if recover() != nil {
			processed = false
			err = errPlatformGenerationWorkerIterationPanicked
		}
	}()
	return run(ctx)
}

func platformGenerationWorkerErrorRequiresReport(ctx context.Context, err error) bool {
	if err == nil || errors.Is(err, gorm.ErrRecordNotFound) {
		return false
	}
	if ctx != nil && ctx.Err() != nil && (errors.Is(err, context.Canceled) || errors.Is(err, ctx.Err())) {
		return false
	}
	return true
}

func safelyReportPlatformGenerationWorkerError(
	reportError func(string, error),
	stage string,
	err error,
) {
	defer func() { _ = recover() }()
	reportError(stage, err)
}

func runPlatformGenerationSubmissionDispatchOnce(
	ctx context.Context,
	queue platformGenerationDispatchQueue,
	submit func(context.Context, ...int64) (bool, error),
) (processed bool, err error) {
	if ctx == nil {
		return false, errors.New("platform generation submission dispatch context is required")
	}
	if queue == nil || submit == nil {
		return false, errors.New("platform generation submission dispatch dependencies are incomplete")
	}
	if err := ctx.Err(); err != nil {
		return false, err
	}

	lease, err := queue.AcquireDispatch(ctx)
	if err != nil {
		return false, err
	}
	if lease == nil {
		return false, gorm.ErrRecordNotFound
	}

	// Finalization is a lease reconciliation operation, not new business work.
	// Give it a short cancellation-independent window so SIGTERM or a panicking
	// submitter does not unnecessarily leave an inflight Redis member behind.
	defer func() {
		finalizeContext, cancel := context.WithTimeout(
			context.WithoutCancel(ctx),
			platformGenerationDispatchFinalizeLimit,
		)
		finalizeErr := queue.FinalizeDispatch(finalizeContext, *lease)
		cancel()
		if finalizeErr == nil || errors.Is(finalizeErr, ErrPlatformGenerationDelayQueueLeaseLost) {
			return
		}
		finalizeErr = fmt.Errorf("generation delay queue finalization: %w", finalizeErr)
		if err == nil || errors.Is(err, gorm.ErrRecordNotFound) {
			err = finalizeErr
			return
		}
		err = errors.Join(err, finalizeErr)
	}()

	return submit(ctx, lease.OutboxID)
}
