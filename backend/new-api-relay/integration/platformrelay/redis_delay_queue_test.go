//go:build integration

package platformrelay_test

import (
	"context"
	"errors"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/go-redis/redis/v8"
	"gorm.io/gorm"
)

func newIntegrationDelayQueue(t *testing.T, client *redis.Client, namespace string) *service.PlatformGenerationDelayQueue {
	t.Helper()
	queue, err := service.NewPlatformGenerationDelayQueue(service.PlatformGenerationDelayQueueConfig{
		Redis:            client,
		Database:         integrationDB,
		Namespace:        namespace,
		DispatchLease:    30 * time.Second,
		RecoveryInterval: time.Minute,
		RecoveryBatch:    256,
	})
	requireNoError(t, err)
	return queue
}

func TestRealRedisDelayQueueAllowsOneLeaseAcrossIndependentClients(t *testing.T) {
	resetIntegrationState(t)
	_, outbox := createQueuedGeneration(t, "integration.redis.claim", "text_to_video")
	namespace := testRedisNamespace(t)

	const workers = 16
	queues := make([]*service.PlatformGenerationDelayQueue, 0, workers)
	for range workers {
		queues = append(queues, newIntegrationDelayQueue(t, newRedisClient(t), namespace))
	}
	recovered, err := queues[0].RecoverFromDatabase(context.Background())
	requireNoError(t, err)
	if recovered != 1 {
		t.Fatalf("expected one PostgreSQL outbox row to be projected, got %d", recovered)
	}

	type result struct {
		lease *service.PlatformGenerationDelayQueueLease
		err   error
	}
	results := make(chan result, workers)
	start := make(chan struct{})
	var wait sync.WaitGroup
	for _, queue := range queues {
		wait.Add(1)
		go func(queue *service.PlatformGenerationDelayQueue) {
			defer wait.Done()
			<-start
			lease, claimErr := queue.AcquireDispatch(context.Background())
			results <- result{lease: lease, err: claimErr}
		}(queue)
	}
	close(start)
	wait.Wait()
	close(results)

	var winner *service.PlatformGenerationDelayQueueLease
	empty := 0
	for result := range results {
		switch {
		case result.err == nil:
			if winner != nil {
				t.Fatal("more than one Redis client acquired the same dispatch")
			}
			winner = result.lease
		case errors.Is(result.err, gorm.ErrRecordNotFound):
			empty++
		default:
			t.Fatalf("unexpected Redis dispatch error: %v", result.err)
		}
	}
	if winner == nil || winner.OutboxID != outbox.ID || empty != workers-1 {
		t.Fatalf("expected one Redis winner for outbox %d and %d empty workers, winner=%+v empty=%d",
			outbox.ID, workers-1, winner, empty)
	}
	requireNoError(t, queues[0].AcknowledgeDispatch(context.Background(), *winner))
}

func TestRedisLeaseExpiryAndPostgresClaimFenceCloseCrashWindow(t *testing.T) {
	resetIntegrationState(t)
	job, outbox := createQueuedGeneration(t, "integration.redis.pg-fence", "text_to_video")
	namespace := testRedisNamespace(t)
	clientA := newRedisClient(t)
	clientB := newRedisClient(t)
	queueA := newIntegrationDelayQueue(t, clientA, namespace)
	queueB := newIntegrationDelayQueue(t, clientB, namespace)

	_, err := queueA.RecoverFromDatabase(context.Background())
	requireNoError(t, err)
	firstRedisLease, err := queueA.AcquireDispatch(context.Background())
	requireNoError(t, err)
	postgresClaim, err := model.ClaimPlatformGenerationSubmission(30*time.Second, outbox.ID)
	requireNoError(t, err)

	inflightKey := "{" + namespace + ":platform-generation-delay}:inflight:v1"
	requireNoError(t, clientA.ZAdd(context.Background(), inflightKey, &redis.Z{
		Score:  float64(dbNow(t).Add(-time.Second).UnixMilli()),
		Member: strconv.FormatInt(outbox.ID, 10),
	}).Err())
	secondRedisLease, err := queueB.AcquireDispatch(context.Background())
	requireNoError(t, err)
	if firstRedisLease.Token == secondRedisLease.Token {
		t.Fatal("expired Redis dispatch reused its fencing token")
	}
	if err := queueA.AcknowledgeDispatch(context.Background(), *firstRedisLease); !errors.Is(err, service.ErrPlatformGenerationDelayQueueLeaseLost) {
		t.Fatalf("stale Redis worker was not fenced: %v", err)
	}

	_, err = model.ClaimPlatformGenerationSubmission(30*time.Second, outbox.ID)
	if !errors.Is(err, gorm.ErrRecordNotFound) {
		t.Fatalf("replacement Redis worker bypassed the live PostgreSQL claim: %v", err)
	}
	requireNoError(t, queueB.FinalizeDispatch(context.Background(), *secondRedisLease))

	won, err := model.CompletePlatformGenerationSubmission(*postgresClaim, map[string]any{
		"status":        model.PlatformGenerationStatusFailed,
		"error_code":    model.PlatformGenerationErrorGenerationFailed,
		"error_message": "integration terminal state",
	})
	requireNoError(t, err)
	if !won {
		t.Fatal("live PostgreSQL owner could not complete after Redis redelivery was fenced")
	}
	requireNoError(t, queueB.SynchronizeOutbox(context.Background(), outbox.ID))

	var persisted model.PlatformGenerationJob
	requireNoError(t, integrationDB.First(&persisted, "id = ?", job.ID).Error)
	if persisted.Status != model.PlatformGenerationStatusFailed {
		t.Fatalf("unexpected job status after fenced crash window: %s", persisted.Status)
	}
}

func TestSubmissionLeaseRenewalFencesRedisRedeliveryAndPostgresTakeover(t *testing.T) {
	resetIntegrationState(t)
	job, outbox := createQueuedGeneration(t, "integration.redis.pg-renewal", "text_to_video")
	namespace := testRedisNamespace(t)
	clientA := newRedisClient(t)
	clientB := newRedisClient(t)
	queueA := newIntegrationDelayQueue(t, clientA, namespace)
	queueB := newIntegrationDelayQueue(t, clientB, namespace)

	_, err := queueA.RecoverFromDatabase(context.Background())
	requireNoError(t, err)
	firstRedisLease, err := queueA.AcquireDispatch(context.Background())
	requireNoError(t, err)
	firstPostgresClaim, err := model.ClaimPlatformGenerationSubmission(time.Second, outbox.ID)
	requireNoError(t, err)

	stopRenewal := make(chan struct{})
	renewalDone := make(chan error, 1)
	go func() {
		ticker := time.NewTicker(100 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-stopRenewal:
				renewalDone <- nil
				return
			case <-ticker.C:
				won, renewErr := model.RenewPlatformGenerationSubmission(*firstPostgresClaim, time.Second)
				if renewErr != nil {
					renewalDone <- renewErr
					return
				}
				if !won {
					renewalDone <- errors.New("live PostgreSQL submission renewal was fenced")
					return
				}
			}
		}
	}()
	// Wait beyond the original PostgreSQL lease. Only periodic renewal can
	// keep the durable owner live across this interval.
	time.Sleep(1500 * time.Millisecond)

	inflightKey := "{" + namespace + ":platform-generation-delay}:inflight:v1"
	requireNoError(t, clientA.ZAdd(context.Background(), inflightKey, &redis.Z{
		Score:  float64(dbNow(t).Add(-time.Second).UnixMilli()),
		Member: strconv.FormatInt(outbox.ID, 10),
	}).Err())
	secondRedisLease, err := queueB.AcquireDispatch(context.Background())
	requireNoError(t, err)
	if firstRedisLease.Token == secondRedisLease.Token {
		t.Fatal("expired Redis dispatch reused its fencing token")
	}
	if err := queueA.AcknowledgeDispatch(context.Background(), *firstRedisLease); !errors.Is(err, service.ErrPlatformGenerationDelayQueueLeaseLost) {
		t.Fatalf("stale Redis worker was not fenced: %v", err)
	}
	if _, err := model.ClaimPlatformGenerationSubmission(time.Second, outbox.ID); !errors.Is(err, gorm.ErrRecordNotFound) {
		t.Fatalf("Redis redelivery bypassed the renewed PostgreSQL owner: %v", err)
	}
	requireNoError(t, queueB.FinalizeDispatch(context.Background(), *secondRedisLease))

	close(stopRenewal)
	requireNoError(t, <-renewalDone)
	forceSubmissionLeaseExpired(t, job.ID, outbox.ID)
	replacement, err := model.ClaimPlatformGenerationSubmission(time.Second, outbox.ID)
	requireNoError(t, err)
	if replacement.Token == firstPostgresClaim.Token {
		t.Fatal("PostgreSQL takeover reused the stale submission token")
	}
	renewed, err := model.RenewPlatformGenerationSubmission(*firstPostgresClaim, time.Second)
	requireNoError(t, err)
	if renewed {
		t.Fatal("stale PostgreSQL owner renewed after takeover")
	}
	won, err := model.CompletePlatformGenerationSubmission(*firstPostgresClaim, map[string]any{
		"status": model.PlatformGenerationStatusProcessing,
	})
	requireNoError(t, err)
	if won {
		t.Fatal("stale PostgreSQL owner completed after takeover")
	}
	won, err = model.CompletePlatformGenerationSubmission(*replacement, map[string]any{
		"status": model.PlatformGenerationStatusFailed,
	})
	requireNoError(t, err)
	if !won {
		t.Fatal("replacement PostgreSQL owner could not complete its live lease")
	}
}

func TestRealRedisCanRebuildLostScheduleFromPostgres(t *testing.T) {
	resetIntegrationState(t)
	_, outbox := createQueuedGeneration(t, "integration.redis.recovery", "text_to_video")
	namespace := testRedisNamespace(t)
	client := newRedisClient(t)
	queue := newIntegrationDelayQueue(t, client, namespace)

	_, err := queue.RecoverFromDatabase(context.Background())
	requireNoError(t, err)
	deleteRedisNamespace(context.Background(), client, namespace)
	recovered, err := queue.RecoverFromDatabase(context.Background())
	requireNoError(t, err)
	if recovered != 1 {
		t.Fatalf("expected PostgreSQL recovery to restore one outbox, got %d", recovered)
	}
	lease, err := queue.AcquireDispatch(context.Background())
	requireNoError(t, err)
	if lease.OutboxID != outbox.ID {
		t.Fatalf("recovered wrong outbox: got %d want %d", lease.OutboxID, outbox.ID)
	}
	requireNoError(t, queue.AcknowledgeDispatch(context.Background(), *lease))
}
