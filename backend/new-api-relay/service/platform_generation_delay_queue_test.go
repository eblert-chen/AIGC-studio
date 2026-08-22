package service

import (
	"context"
	"errors"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/alicebob/miniredis/v2"
	"github.com/go-redis/redis/v8"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func newPlatformGenerationDelayQueueTest(
	t *testing.T,
	namespace string,
) (*PlatformGenerationDelayQueue, *miniredis.Miniredis, *redis.Client) {
	t.Helper()
	truncate(t)
	require.NoError(t, model.DB.Exec("DELETE FROM platform_generation_route_admissions").Error)
	require.NoError(t, model.DB.Exec("DELETE FROM platform_generation_outboxes").Error)
	require.NoError(t, model.DB.Exec("DELETE FROM platform_generation_jobs").Error)
	server := miniredis.RunT(t)
	client := redis.NewClient(&redis.Options{
		Addr:         server.Addr(),
		MaxRetries:   -1,
		DialTimeout:  100 * time.Millisecond,
		ReadTimeout:  100 * time.Millisecond,
		WriteTimeout: 100 * time.Millisecond,
	})
	t.Cleanup(func() {
		_ = client.Close()
	})
	queue, err := NewPlatformGenerationDelayQueue(PlatformGenerationDelayQueueConfig{
		Redis:            client,
		Database:         model.DB,
		Namespace:        namespace,
		DispatchLease:    30 * time.Second,
		RecoveryInterval: time.Second,
		RecoveryBatch:    2,
	})
	require.NoError(t, err)
	return queue, server, client
}

func createPlatformGenerationDelayOutbox(t *testing.T, availableAt time.Time) model.PlatformGenerationOutbox {
	t.Helper()
	outbox := model.PlatformGenerationOutbox{
		JobID:       uuid.NewString(),
		Topic:       "generation.submit",
		State:       model.PlatformGenerationOutboxPending,
		AvailableAt: availableAt,
	}
	require.NoError(t, model.DB.Create(&outbox).Error)
	return outbox
}

func TestPlatformGenerationDelayQueueUsesFencedRedisZSets(t *testing.T) {
	queue, _, client := newPlatformGenerationDelayQueueTest(t, "delay-zset-test")
	outbox := createPlatformGenerationDelayOutbox(t, time.Now().UTC().Add(-time.Minute))
	member := strconv.FormatInt(outbox.ID, 10)

	recovered, err := queue.RecoverFromDatabase(context.Background())
	require.NoError(t, err)
	assert.Equal(t, 1, recovered)
	_, err = client.ZScore(context.Background(), queue.scheduledKey, member).Result()
	require.NoError(t, err, "the PostgreSQL outbox must be projected into the scheduled ZSET")

	lease, err := queue.AcquireDispatch(context.Background())
	require.NoError(t, err)
	assert.Equal(t, outbox.ID, lease.OutboxID)
	assert.NotEmpty(t, lease.Token)
	_, err = client.ZScore(context.Background(), queue.scheduledKey, member).Result()
	assert.ErrorIs(t, err, redis.Nil)
	_, err = client.ZScore(context.Background(), queue.inflightKey, member).Result()
	require.NoError(t, err)
	token, err := client.HGet(context.Background(), queue.leaseTokenKey, member).Result()
	require.NoError(t, err)
	assert.Equal(t, lease.Token, token)

	require.NoError(t, queue.AcknowledgeDispatch(context.Background(), *lease))
	_, err = client.ZScore(context.Background(), queue.inflightKey, member).Result()
	assert.ErrorIs(t, err, redis.Nil)
	_, err = client.HGet(context.Background(), queue.leaseTokenKey, member).Result()
	assert.ErrorIs(t, err, redis.Nil)
}

func TestPlatformGenerationDelayQueueAllowsOnlyOneWorkerLease(t *testing.T) {
	queueA, _, client := newPlatformGenerationDelayQueueTest(t, "delay-multi-worker-test")
	queueB, err := NewPlatformGenerationDelayQueue(PlatformGenerationDelayQueueConfig{
		Redis:            client,
		Database:         model.DB,
		Namespace:        "delay-multi-worker-test",
		DispatchLease:    30 * time.Second,
		RecoveryInterval: time.Second,
		RecoveryBatch:    2,
	})
	require.NoError(t, err)
	createPlatformGenerationDelayOutbox(t, time.Now().UTC().Add(-time.Minute))
	_, err = queueA.RecoverFromDatabase(context.Background())
	require.NoError(t, err)

	start := make(chan struct{})
	results := make(chan error, 2)
	var wait sync.WaitGroup
	for _, queue := range []*PlatformGenerationDelayQueue{queueA, queueB} {
		wait.Add(1)
		go func(candidate *PlatformGenerationDelayQueue) {
			defer wait.Done()
			<-start
			_, claimErr := candidate.AcquireDispatch(context.Background())
			results <- claimErr
		}(queue)
	}
	close(start)
	wait.Wait()
	close(results)

	claimed := 0
	empty := 0
	for result := range results {
		switch {
		case result == nil:
			claimed++
		case errors.Is(result, gorm.ErrRecordNotFound):
			empty++
		default:
			require.NoError(t, result)
		}
	}
	assert.Equal(t, 1, claimed)
	assert.Equal(t, 1, empty)
}

func TestPlatformGenerationDelayQueueFencesExpiredWorker(t *testing.T) {
	queue, _, client := newPlatformGenerationDelayQueueTest(t, "delay-fence-test")
	outbox := createPlatformGenerationDelayOutbox(t, time.Now().UTC().Add(-time.Minute))
	_, err := queue.RecoverFromDatabase(context.Background())
	require.NoError(t, err)
	first, err := queue.AcquireDispatch(context.Background())
	require.NoError(t, err)

	now, err := queue.databaseClock(context.Background())
	require.NoError(t, err)
	member := strconv.FormatInt(outbox.ID, 10)
	require.NoError(t, client.ZAdd(context.Background(), queue.inflightKey, &redis.Z{
		Score:  float64(now.Add(-time.Second).UnixMilli()),
		Member: member,
	}).Err())
	assert.ErrorIs(t, queue.AcknowledgeDispatch(context.Background(), *first), ErrPlatformGenerationDelayQueueLeaseLost)
	second, err := queue.claimDueAt(context.Background(), now)
	require.NoError(t, err)
	assert.Equal(t, outbox.ID, second.OutboxID)
	assert.NotEqual(t, first.Token, second.Token)
	require.NoError(t, queue.AcknowledgeDispatch(context.Background(), *second))
}

func TestPlatformGenerationDelayQueueRecoversAfterRedisLoss(t *testing.T) {
	queue, server, _ := newPlatformGenerationDelayQueueTest(t, "delay-redis-loss-test")
	outbox := createPlatformGenerationDelayOutbox(t, time.Now().UTC().Add(-time.Minute))
	server.Close()

	_, err := queue.AcquireDispatch(context.Background())
	require.Error(t, err)
	assert.False(t, errors.Is(err, gorm.ErrRecordNotFound), "Redis loss must fail closed instead of bypassing the ZSET")
	var persisted model.PlatformGenerationOutbox
	require.NoError(t, model.DB.First(&persisted, outbox.ID).Error)
	assert.Equal(t, model.PlatformGenerationOutboxPending, persisted.State)

	replacement := miniredis.RunT(t)
	replacementClient := redis.NewClient(&redis.Options{Addr: replacement.Addr(), MaxRetries: -1})
	t.Cleanup(func() { _ = replacementClient.Close() })
	recoveredQueue, err := NewPlatformGenerationDelayQueue(PlatformGenerationDelayQueueConfig{
		Redis:            replacementClient,
		Database:         model.DB,
		Namespace:        "delay-redis-loss-test",
		DispatchLease:    30 * time.Second,
		RecoveryInterval: time.Second,
		RecoveryBatch:    2,
	})
	require.NoError(t, err)
	lease, err := recoveredQueue.AcquireDispatch(context.Background())
	require.NoError(t, err)
	assert.Equal(t, outbox.ID, lease.OutboxID, "the database recovery scan must restore a lost Redis schedule")
}

func TestPlatformGenerationDelayQueueLocalWaitDoesNotSpendProviderAttempt(t *testing.T) {
	queue, _, _ := newPlatformGenerationDelayQueueTest(t, "delay-local-wait-test")
	job := model.PlatformGenerationJob{
		ID:                         uuid.NewString(),
		TenantID:                   uuid.NewString(),
		SourceClientID:             "platform",
		RequestID:                  "delay-local-wait-request",
		IdempotencyKey:             "delay-local-wait-idempotency",
		RequestHash:                "request-hash",
		RequestJSON:                "{}",
		Model:                      "video-model",
		Mode:                       "text_to_video",
		ExpectedCapabilityRevision: "sha256:revision",
		CapabilityRevision:         "sha256:revision",
		Status:                     model.PlatformGenerationStatusQueued,
	}
	_, replayed, conflict, err := model.CreatePlatformGenerationJob(&job)
	require.NoError(t, err)
	assert.False(t, replayed)
	assert.False(t, conflict)
	dispatchLease, err := queue.AcquireDispatch(context.Background())
	require.NoError(t, err)
	claim, err := model.ClaimPlatformGenerationSubmission(time.Minute)
	require.NoError(t, err)
	assert.Equal(t, claim.OutboxID, dispatchLease.OutboxID)
	released, err := model.ReleasePlatformGenerationSubmission(*claim, time.Minute, "provider account pool is locally busy")
	require.NoError(t, err)
	assert.True(t, released)
	require.NoError(t, queue.FinalizeDispatch(context.Background(), *dispatchLease))

	_, err = queue.AcquireDispatch(context.Background())
	assert.ErrorIs(t, err, gorm.ErrRecordNotFound, "the Redis due score must enforce the local wait")
	var persisted model.PlatformGenerationJob
	require.NoError(t, model.DB.First(&persisted, "id = ?", job.ID).Error)
	assert.Zero(t, persisted.ProviderSubmissionAttempt)
	var admissionCount int64
	require.NoError(t, model.DB.Model(&model.PlatformGenerationRouteAdmission{}).Where("job_id = ?", job.ID).Count(&admissionCount).Error)
	assert.Zero(t, admissionCount, "a Redis/local wait must happen before provider route admission")

	require.NoError(t, model.DB.Model(&model.PlatformGenerationOutbox{}).
		Where("id = ?", claim.OutboxID).
		Update("available_at", time.Now().UTC().Add(-time.Minute)).Error)
	require.NoError(t, queue.SynchronizeOutbox(context.Background(), claim.OutboxID))
	lease, err := queue.AcquireDispatch(context.Background())
	require.NoError(t, err)
	assert.Equal(t, claim.OutboxID, lease.OutboxID)
	require.NoError(t, model.DB.First(&persisted, "id = ?", job.ID).Error)
	assert.Zero(t, persisted.ProviderSubmissionAttempt)
}

func TestPlatformGenerationDelayQueueRuntimeFailsClosedWithoutDurableRedis(t *testing.T) {
	previousEnabled := common.RedisEnabled
	previousClient := common.RDB
	t.Cleanup(func() {
		common.RedisEnabled = previousEnabled
		common.RDB = previousClient
	})

	common.RedisEnabled = false
	common.RDB = nil
	assert.NoError(t, ValidatePlatformGenerationDelayQueueRuntime(context.Background(), false, true))
	err := ValidatePlatformGenerationDelayQueueRuntime(context.Background(), true, true)
	require.Error(t, err)
	assert.Contains(t, err.Error(), "require Redis")

	server := miniredis.RunT(t)
	client := redis.NewClient(&redis.Options{Addr: server.Addr(), MaxRetries: -1})
	t.Cleanup(func() { _ = client.Close() })
	common.RedisEnabled = true
	common.RDB = client
	require.NoError(t, ValidatePlatformGenerationDelayQueueRuntime(context.Background(), true, false))
	err = ValidatePlatformGenerationDelayQueueRuntime(context.Background(), true, true)
	require.Error(t, err)
	assert.Contains(t, err.Error(), "AOF", "production cannot run on a non-AOF Redis scheduler")
}
