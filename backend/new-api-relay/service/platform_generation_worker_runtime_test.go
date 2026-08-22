package service

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

type platformGenerationDispatchQueueStub struct {
	acquire  func(context.Context) (*PlatformGenerationDelayQueueLease, error)
	finalize func(context.Context, PlatformGenerationDelayQueueLease) error
}

func (stub *platformGenerationDispatchQueueStub) AcquireDispatch(
	ctx context.Context,
) (*PlatformGenerationDelayQueueLease, error) {
	return stub.acquire(ctx)
}

func (stub *platformGenerationDispatchQueueStub) FinalizeDispatch(
	ctx context.Context,
	lease PlatformGenerationDelayQueueLease,
) error {
	return stub.finalize(ctx, lease)
}

func idlePlatformGenerationDispatchQueue() platformGenerationDispatchQueue {
	return &platformGenerationDispatchQueueStub{
		acquire: func(context.Context) (*PlatformGenerationDelayQueueLease, error) {
			return nil, gorm.ErrRecordNotFound
		},
		finalize: func(context.Context, PlatformGenerationDelayQueueLease) error {
			return nil
		},
	}
}

func idlePlatformGenerationWorkerDependencies() platformGenerationWorkerDependencies {
	idle := func(context.Context) (bool, error) { return false, gorm.ErrRecordNotFound }
	return platformGenerationWorkerDependencies{
		queue:            idlePlatformGenerationDispatchQueue(),
		submit:           func(context.Context, ...int64) (bool, error) { return false, gorm.ErrRecordNotFound },
		reconcile:        idle,
		poll:             idle,
		transfer:         idle,
		callbackBackfill: idle,
		callback:         idle,
		reportError:      func(string, error) {},
	}
}

func TestPlatformGenerationWorkerRuntimeSubmissionDoesNotStarveOtherStages(t *testing.T) {
	submissionStarted := make(chan struct{})
	var submissionStartedOnce sync.Once
	var acquired atomic.Bool
	queue := &platformGenerationDispatchQueueStub{
		acquire: func(context.Context) (*PlatformGenerationDelayQueueLease, error) {
			if !acquired.CompareAndSwap(false, true) {
				return nil, gorm.ErrRecordNotFound
			}
			return &PlatformGenerationDelayQueueLease{OutboxID: 41, Token: "dispatch-token"}, nil
		},
		finalize: func(context.Context, PlatformGenerationDelayQueueLease) error { return nil },
	}

	stageNames := []string{
		platformGenerationWorkerStageReconciliation,
		platformGenerationWorkerStagePoll,
		platformGenerationWorkerStageTransfer,
		platformGenerationWorkerStageCallbackBackfill,
		platformGenerationWorkerStageCallback,
	}
	stageRan := make(map[string]chan struct{}, len(stageNames))
	stageRun := make(map[string]platformGenerationWorkerOnce, len(stageNames))
	for _, configuredName := range stageNames {
		name := configuredName
		ran := make(chan struct{})
		stageRan[name] = ran
		var once sync.Once
		stageRun[name] = func(context.Context) (bool, error) {
			once.Do(func() { close(ran) })
			return false, gorm.ErrRecordNotFound
		}
	}

	dependencies := platformGenerationWorkerDependencies{
		queue: queue,
		submit: func(ctx context.Context, outboxIDs ...int64) (bool, error) {
			require.Equal(t, []int64{41}, outboxIDs)
			submissionStartedOnce.Do(func() { close(submissionStarted) })
			<-ctx.Done()
			return true, ctx.Err()
		},
		reconcile:        stageRun[platformGenerationWorkerStageReconciliation],
		poll:             stageRun[platformGenerationWorkerStagePoll],
		transfer:         stageRun[platformGenerationWorkerStageTransfer],
		callbackBackfill: stageRun[platformGenerationWorkerStageCallbackBackfill],
		callback:         stageRun[platformGenerationWorkerStageCallback],
		reportError:      func(string, error) {},
	}
	runtime, err := startPlatformGenerationWorkerRuntime(context.Background(), 2*time.Millisecond, dependencies)
	require.NoError(t, err)

	select {
	case <-submissionStarted:
	case <-time.After(time.Second):
		t.Fatal("submission stage did not start")
	}
	for _, name := range stageNames {
		select {
		case <-stageRan[name]:
		case <-time.After(time.Second):
			t.Fatalf("%s stage was starved by a blocked submission", name)
		}
	}

	stopContext, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	require.NoError(t, runtime.Stop(stopContext))
}

func TestPlatformGenerationWorkerRuntimeStopCancelsAndWaitsForEveryStage(t *testing.T) {
	var started atomic.Int32
	allStarted := make(chan struct{})
	markAndBlock := func(ctx context.Context) (bool, error) {
		if started.Add(1) == 6 {
			close(allStarted)
		}
		<-ctx.Done()
		return false, ctx.Err()
	}
	queue := &platformGenerationDispatchQueueStub{
		acquire: func(context.Context) (*PlatformGenerationDelayQueueLease, error) {
			return &PlatformGenerationDelayQueueLease{OutboxID: 51, Token: "dispatch-token"}, nil
		},
		finalize: func(context.Context, PlatformGenerationDelayQueueLease) error { return nil },
	}
	dependencies := platformGenerationWorkerDependencies{
		queue:            queue,
		submit:           func(ctx context.Context, _ ...int64) (bool, error) { return markAndBlock(ctx) },
		reconcile:        markAndBlock,
		poll:             markAndBlock,
		transfer:         markAndBlock,
		callbackBackfill: markAndBlock,
		callback:         markAndBlock,
		reportError:      func(string, error) {},
	}
	runtime, err := startPlatformGenerationWorkerRuntime(context.Background(), time.Millisecond, dependencies)
	require.NoError(t, err)
	select {
	case <-allStarted:
	case <-time.After(time.Second):
		t.Fatal("not every worker stage started")
	}

	stopContext, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	require.NoError(t, runtime.Stop(stopContext))
	select {
	case <-runtime.done:
	default:
		t.Fatal("runtime Stop returned before every stage exited")
	}
}

func TestPlatformGenerationWorkerRuntimeKeepsSingleLocalConcurrencyPerStage(t *testing.T) {
	type stageCounter struct {
		active atomic.Int32
		max    atomic.Int32
		calls  atomic.Int32
	}
	counters := make([]*stageCounter, 6)
	makeRun := func(index int) platformGenerationWorkerOnce {
		counter := &stageCounter{}
		counters[index] = counter
		return func(ctx context.Context) (bool, error) {
			current := counter.active.Add(1)
			for {
				maximum := counter.max.Load()
				if current <= maximum || counter.max.CompareAndSwap(maximum, current) {
					break
				}
			}
			counter.calls.Add(1)
			select {
			case <-ctx.Done():
			case <-time.After(5 * time.Millisecond):
			}
			counter.active.Add(-1)
			return true, nil
		}
	}
	queue := &platformGenerationDispatchQueueStub{
		acquire: func(context.Context) (*PlatformGenerationDelayQueueLease, error) {
			return &PlatformGenerationDelayQueueLease{OutboxID: 61, Token: "dispatch-token"}, nil
		},
		finalize: func(context.Context, PlatformGenerationDelayQueueLease) error { return nil },
	}
	submitRun := makeRun(0)
	dependencies := platformGenerationWorkerDependencies{
		queue:            queue,
		submit:           func(ctx context.Context, _ ...int64) (bool, error) { return submitRun(ctx) },
		reconcile:        makeRun(1),
		poll:             makeRun(2),
		transfer:         makeRun(3),
		callbackBackfill: makeRun(4),
		callback:         makeRun(5),
		reportError:      func(string, error) {},
	}
	runtime, err := startPlatformGenerationWorkerRuntime(context.Background(), time.Millisecond, dependencies)
	require.NoError(t, err)
	time.Sleep(30 * time.Millisecond)
	stopContext, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	require.NoError(t, runtime.Stop(stopContext))
	for index, counter := range counters {
		require.NotNil(t, counter)
		assert.Greater(t, counter.calls.Load(), int32(0), "stage %d never ran", index)
		assert.Equal(t, int32(1), counter.max.Load(), "stage %d ran concurrently with itself", index)
	}
}

func TestPlatformGenerationWorkerRuntimeRecoversStagePanic(t *testing.T) {
	var pollCalls atomic.Int32
	pollRecovered := make(chan struct{})
	reported := make(chan string, 1)
	dependencies := idlePlatformGenerationWorkerDependencies()
	dependencies.poll = func(context.Context) (bool, error) {
		if pollCalls.Add(1) == 1 {
			panic("do not log panic payload")
		}
		select {
		case <-pollRecovered:
		default:
			close(pollRecovered)
		}
		return false, gorm.ErrRecordNotFound
	}
	dependencies.reportError = func(stage string, err error) {
		if errors.Is(err, errPlatformGenerationWorkerIterationPanicked) {
			reported <- stage
		}
	}
	runtime, err := startPlatformGenerationWorkerRuntime(context.Background(), time.Millisecond, dependencies)
	require.NoError(t, err)
	select {
	case stage := <-reported:
		assert.Equal(t, platformGenerationWorkerStagePoll, stage)
	case <-time.After(time.Second):
		t.Fatal("panicked stage was not reported")
	}
	select {
	case <-pollRecovered:
	case <-time.After(time.Second):
		t.Fatal("poll stage did not run again after panic")
	}
	stopContext, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	require.NoError(t, runtime.Stop(stopContext))
}

func TestPlatformGenerationSubmissionDispatchFinalizesOnEveryExit(t *testing.T) {
	submitError := errors.New("submit failed")
	tests := []struct {
		name      string
		submit    func(context.Context, ...int64) (bool, error)
		wantError error
		panics    bool
	}{
		{
			name:   "success",
			submit: func(context.Context, ...int64) (bool, error) { return true, nil },
		},
		{
			name:      "error",
			submit:    func(context.Context, ...int64) (bool, error) { return true, submitError },
			wantError: submitError,
		},
		{
			name:   "panic",
			submit: func(context.Context, ...int64) (bool, error) { panic("submission panic") },
			panics: true,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			var finalized atomic.Int32
			queue := &platformGenerationDispatchQueueStub{
				acquire: func(context.Context) (*PlatformGenerationDelayQueueLease, error) {
					return &PlatformGenerationDelayQueueLease{OutboxID: 71, Token: "dispatch-token"}, nil
				},
				finalize: func(context.Context, PlatformGenerationDelayQueueLease) error {
					finalized.Add(1)
					return nil
				},
			}
			call := func() error {
				_, err := runPlatformGenerationSubmissionDispatchOnce(context.Background(), queue, test.submit)
				return err
			}
			if test.panics {
				assert.Panics(t, func() { _ = call() })
			} else {
				assert.ErrorIs(t, call(), test.wantError)
			}
			assert.Equal(t, int32(1), finalized.Load())
		})
	}
}

func TestPlatformGenerationSubmissionDispatchFinalizesAfterCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	var finalizeContextError error
	queue := &platformGenerationDispatchQueueStub{
		acquire: func(context.Context) (*PlatformGenerationDelayQueueLease, error) {
			return &PlatformGenerationDelayQueueLease{OutboxID: 81, Token: "dispatch-token"}, nil
		},
		finalize: func(ctx context.Context, _ PlatformGenerationDelayQueueLease) error {
			finalizeContextError = ctx.Err()
			return nil
		},
	}
	_, err := runPlatformGenerationSubmissionDispatchOnce(ctx, queue, func(context.Context, ...int64) (bool, error) {
		cancel()
		return true, context.Canceled
	})
	assert.ErrorIs(t, err, context.Canceled)
	assert.NoError(t, finalizeContextError, "finalization must have a cancellation-independent cleanup window")
}

func TestPlatformGenerationSubmissionDispatchIgnoresLostRedisLease(t *testing.T) {
	queue := &platformGenerationDispatchQueueStub{
		acquire: func(context.Context) (*PlatformGenerationDelayQueueLease, error) {
			return &PlatformGenerationDelayQueueLease{OutboxID: 91, Token: "dispatch-token"}, nil
		},
		finalize: func(context.Context, PlatformGenerationDelayQueueLease) error {
			return ErrPlatformGenerationDelayQueueLeaseLost
		},
	}
	processed, err := runPlatformGenerationSubmissionDispatchOnce(
		context.Background(),
		queue,
		func(context.Context, ...int64) (bool, error) { return true, nil },
	)
	require.NoError(t, err)
	assert.True(t, processed)
}

func TestPlatformGenerationWorkerStarterRetriesConstructionAndPreventsDuplicateStart(t *testing.T) {
	var starter platformGenerationWorkerStarter
	assert.Equal(t, PlatformGenerationWorkerStateStopped, starter.State())
	assert.False(t, starter.State() == PlatformGenerationWorkerStateRunning)
	constructionError := errors.New("redis unavailable")
	firstCalls := 0
	err := starter.Start(context.Background(), time.Millisecond, func() (platformGenerationWorkerDependencies, error) {
		firstCalls++
		return platformGenerationWorkerDependencies{}, constructionError
	})
	assert.ErrorIs(t, err, constructionError)
	assert.Equal(t, 1, firstCalls)
	assert.Equal(t, PlatformGenerationWorkerStateFailed, starter.State())

	successfulCalls := 0
	require.NoError(t, starter.Start(context.Background(), time.Millisecond, func() (platformGenerationWorkerDependencies, error) {
		successfulCalls++
		return idlePlatformGenerationWorkerDependencies(), nil
	}))
	require.NoError(t, starter.Start(context.Background(), time.Millisecond, func() (platformGenerationWorkerDependencies, error) {
		successfulCalls++
		return platformGenerationWorkerDependencies{}, errors.New("duplicate factory must not run")
	}))
	assert.Equal(t, 1, successfulCalls)
	assert.Equal(t, PlatformGenerationWorkerStateRunning, starter.State())

	stopContext, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	require.NoError(t, starter.Stop(stopContext))
	assert.Equal(t, PlatformGenerationWorkerStateStopped, starter.State())
}

func TestPlatformGenerationWorkerStarterPublishesStartingRunningAndDrainingStates(t *testing.T) {
	var starter platformGenerationWorkerStarter
	factoryEntered := make(chan struct{})
	releaseFactory := make(chan struct{})
	startResult := make(chan error, 1)
	go func() {
		startResult <- starter.Start(context.Background(), time.Millisecond, func() (platformGenerationWorkerDependencies, error) {
			close(factoryEntered)
			<-releaseFactory
			return idlePlatformGenerationWorkerDependencies(), nil
		})
	}()

	select {
	case <-factoryEntered:
	case <-time.After(time.Second):
		t.Fatal("worker dependency factory did not start")
	}
	assert.Equal(t, PlatformGenerationWorkerStateStarting, starter.State())
	close(releaseFactory)
	require.NoError(t, <-startResult)
	assert.Equal(t, PlatformGenerationWorkerStateRunning, starter.State())

	starter.BeginDrain()
	assert.Equal(t, PlatformGenerationWorkerStateDraining, starter.State())
	stopContext, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	require.NoError(t, starter.Stop(stopContext))
	assert.Equal(t, PlatformGenerationWorkerStateStopped, starter.State())
}

func TestPlatformGenerationWorkerStarterRemainsDrainingUntilTimedOutStageExits(t *testing.T) {
	var starter platformGenerationWorkerStarter
	stageStarted := make(chan struct{})
	releaseStage := make(chan struct{})
	var stageStartedOnce sync.Once
	dependencies := idlePlatformGenerationWorkerDependencies()
	dependencies.poll = func(context.Context) (bool, error) {
		stageStartedOnce.Do(func() { close(stageStarted) })
		<-releaseStage
		return true, nil
	}
	require.NoError(t, starter.Start(context.Background(), time.Millisecond, func() (platformGenerationWorkerDependencies, error) {
		return dependencies, nil
	}))
	select {
	case <-stageStarted:
	case <-time.After(time.Second):
		t.Fatal("blocking stage did not start")
	}

	shortContext, cancelShort := context.WithTimeout(context.Background(), 10*time.Millisecond)
	err := starter.Stop(shortContext)
	cancelShort()
	assert.ErrorIs(t, err, context.DeadlineExceeded)
	assert.Equal(t, PlatformGenerationWorkerStateDraining, starter.State())
	close(releaseStage)
	require.Eventually(t, func() bool {
		return starter.State() == PlatformGenerationWorkerStateStopped
	}, time.Second, time.Millisecond)
}

func TestPlatformGenerationWorkerRuntimeStopIsBounded(t *testing.T) {
	release := make(chan struct{})
	started := make(chan struct{})
	var startedOnce sync.Once
	dependencies := idlePlatformGenerationWorkerDependencies()
	dependencies.poll = func(context.Context) (bool, error) {
		startedOnce.Do(func() { close(started) })
		<-release
		return true, nil
	}
	runtime, err := startPlatformGenerationWorkerRuntime(context.Background(), time.Millisecond, dependencies)
	require.NoError(t, err)
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("blocking stage did not start")
	}
	shortContext, cancelShort := context.WithTimeout(context.Background(), 10*time.Millisecond)
	err = runtime.Stop(shortContext)
	cancelShort()
	assert.ErrorIs(t, err, context.DeadlineExceeded)
	close(release)
	stopContext, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	require.NoError(t, runtime.Stop(stopContext))
}
