package service

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestRelayHTTPHandlerTrackerGatesAndJoinsActiveHandlers(t *testing.T) {
	tracker := NewRelayHTTPHandlerTracker()
	require.True(t, tracker.Enter())
	tracker.BeginDrain()
	require.False(t, tracker.Enter())

	shortContext, cancelShort := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancelShort()
	require.ErrorIs(t, tracker.Wait(shortContext), context.DeadlineExceeded)

	tracker.Leave()
	joinContext, cancelJoin := context.WithTimeout(context.Background(), time.Second)
	defer cancelJoin()
	require.NoError(t, tracker.Wait(joinContext))
	tracker.BeginDrain()
	require.NoError(t, tracker.Wait(joinContext))
}

func TestDrainRelayHTTPAndWorkersEscalatesNormalDrainExactlyOnce(t *testing.T) {
	tracker := NewRelayHTTPHandlerTracker()
	require.True(t, tracker.Enter())
	lifecycleFailures := make(chan error, 1)
	lifecycleFailure := errors.New("test lifecycle anchor lost")
	shutdownStarted := make(chan struct{})
	workerStarted := make(chan struct{})
	var stopCalls atomic.Int32
	var shutdownCalls atomic.Int32
	var closeCalls atomic.Int32

	type result struct {
		outcome RelayRuntimeDrainOutcome
		err     error
	}
	resultReady := make(chan result, 1)
	go func() {
		outcome, err := DrainRelayHTTPAndWorkersWithLifecycleEscalation(
			2*time.Second,
			time.Second,
			200*time.Millisecond,
			50*time.Millisecond,
			nil,
			lifecycleFailures,
			func() bool { return false },
			tracker,
			func(ctx context.Context) error {
				shutdownCalls.Add(1)
				close(shutdownStarted)
				<-ctx.Done()
				return ctx.Err()
			},
			func() error {
				closeCalls.Add(1)
				tracker.Leave()
				return nil
			},
			func(ctx context.Context) error {
				stopCalls.Add(1)
				close(workerStarted)
				<-ctx.Done()
				return ctx.Err()
			},
		)
		resultReady <- result{outcome: outcome, err: err}
	}()
	<-shutdownStarted
	<-workerStarted
	lossObservedAt := time.Now()
	lifecycleFailures <- lifecycleFailure

	select {
	case drainResult := <-resultReady:
		require.ErrorIs(t, drainResult.outcome.LifecycleLoss, lifecycleFailure)
		require.False(t, drainResult.outcome.LifecycleLossDeadline.IsZero())
		require.Less(t, time.Since(lossObservedAt), 180*time.Millisecond)
		require.Error(t, drainResult.err)
	case <-time.After(500 * time.Millisecond):
		t.Fatal("normal drain was not upgraded after lifecycle loss")
	}
	require.Equal(t, int32(1), stopCalls.Load())
	require.Equal(t, int32(1), shutdownCalls.Load())
	require.Equal(t, int32(1), closeCalls.Load())
}

func TestDrainRelayHTTPAndWorkersCompletesNormallyWithoutLifecycleLoss(t *testing.T) {
	tracker := NewRelayHTTPHandlerTracker()
	var stopCalls atomic.Int32
	outcome, err := DrainRelayHTTPAndWorkersWithLifecycleEscalation(
		time.Second,
		500*time.Millisecond,
		200*time.Millisecond,
		50*time.Millisecond,
		nil,
		nil,
		func() bool { return false },
		tracker,
		func(context.Context) error { return nil },
		func() error { return nil },
		func(context.Context) error {
			stopCalls.Add(1)
			return nil
		},
	)
	require.NoError(t, err)
	require.Nil(t, outcome.LifecycleLoss)
	require.True(t, outcome.LifecycleLossDeadline.IsZero())
	require.Equal(t, int32(1), stopCalls.Load())
}

func TestDrainRelayHTTPAndWorkersForceClosesThenJoinsBeforePoolBoundary(t *testing.T) {
	tracker := NewRelayHTTPHandlerTracker()
	require.True(t, tracker.Enter())
	requestCanceled := make(chan struct{})
	workerCanceled := make(chan struct{})
	closeCalled := make(chan struct{})

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	err := DrainRelayHTTPAndWorkers(
		ctx,
		20*time.Millisecond,
		tracker,
		func(shutdownContext context.Context) error {
			<-shutdownContext.Done()
			return shutdownContext.Err()
		},
		func() error {
			close(closeCalled)
			tracker.Leave()
			close(requestCanceled)
			return nil
		},
		func(stopContext context.Context) error {
			select {
			case <-closeCalled:
				close(workerCanceled)
				return nil
			case <-stopContext.Done():
				return stopContext.Err()
			}
		},
	)
	require.ErrorIs(t, err, context.DeadlineExceeded)
	select {
	case <-requestCanceled:
	default:
		t.Fatal("active HTTP handler was not force-canceled and joined")
	}
	select {
	case <-workerCanceled:
	default:
		t.Fatal("database worker was not joined before the drain returned")
	}
}
