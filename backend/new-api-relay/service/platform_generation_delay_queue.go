package service

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/go-redis/redis/v8"
	"github.com/google/uuid"
	"gorm.io/gorm"
)

const (
	platformGenerationDelayQueueDefaultNamespace        = "new-api-relay"
	platformGenerationDelayQueueDefaultLease            = 30 * time.Second
	platformGenerationDelayQueueDefaultRecoveryInterval = time.Second
	platformGenerationDelayQueueDefaultRecoveryBatch    = 256
	platformGenerationDelayQueueExpiredBatch            = 64
)

var ErrPlatformGenerationDelayQueueLeaseLost = errors.New("generation delay queue lease was fenced")

// PlatformGenerationDelayQueueConfig keeps Redis as a derived, durable
// scheduler while PostgreSQL remains the transactional source of truth. All
// keys share one Redis cluster hash slot so the fencing scripts stay atomic.
type PlatformGenerationDelayQueueConfig struct {
	Redis            *redis.Client
	Database         *gorm.DB
	Namespace        string
	DispatchLease    time.Duration
	RecoveryInterval time.Duration
	RecoveryBatch    int
}

type PlatformGenerationDelayQueue struct {
	redis            *redis.Client
	database         *gorm.DB
	scheduledKey     string
	inflightKey      string
	leaseTokenKey    string
	recoveryKey      string
	dispatchLease    time.Duration
	recoveryInterval time.Duration
	recoveryBatch    int
}

type PlatformGenerationDelayQueueLease struct {
	OutboxID  int64
	Token     string
	ExpiresAt time.Time
}

func PlatformGenerationDelayQueueRuntimeConfig() PlatformGenerationDelayQueueConfig {
	return PlatformGenerationDelayQueueConfig{
		Redis:            common.RDB,
		Database:         model.DB,
		Namespace:        common.GetEnvOrDefaultString("RELAY_COMPAT_DELAY_QUEUE_NAMESPACE", platformGenerationDelayQueueDefaultNamespace),
		DispatchLease:    time.Duration(common.GetEnvOrDefault("RELAY_COMPAT_DELAY_QUEUE_LEASE_SECONDS", int(platformGenerationDelayQueueDefaultLease/time.Second))) * time.Second,
		RecoveryInterval: time.Duration(common.GetEnvOrDefault("RELAY_COMPAT_DELAY_QUEUE_RECOVERY_SECONDS", int(platformGenerationDelayQueueDefaultRecoveryInterval/time.Second))) * time.Second,
		RecoveryBatch:    common.GetEnvOrDefault("RELAY_COMPAT_DELAY_QUEUE_RECOVERY_BATCH", platformGenerationDelayQueueDefaultRecoveryBatch),
	}
}

func NewPlatformGenerationDelayQueue(config PlatformGenerationDelayQueueConfig) (*PlatformGenerationDelayQueue, error) {
	if config.Redis == nil {
		return nil, errors.New("generation delay queue requires Redis")
	}
	if config.Database == nil {
		return nil, errors.New("generation delay queue requires the main database")
	}
	namespace := strings.TrimSpace(config.Namespace)
	if namespace == "" {
		namespace = platformGenerationDelayQueueDefaultNamespace
	}
	if len(namespace) > 96 || strings.ContainsAny(namespace, "{}\r\n\t ") {
		return nil, errors.New("generation delay queue namespace is invalid")
	}
	if config.DispatchLease == 0 {
		config.DispatchLease = platformGenerationDelayQueueDefaultLease
	}
	if config.DispatchLease < time.Second || config.DispatchLease > 10*time.Minute {
		return nil, errors.New("generation delay queue dispatch lease must be between 1 second and 10 minutes")
	}
	if config.RecoveryInterval == 0 {
		config.RecoveryInterval = platformGenerationDelayQueueDefaultRecoveryInterval
	}
	if config.RecoveryInterval < 100*time.Millisecond || config.RecoveryInterval > time.Minute {
		return nil, errors.New("generation delay queue recovery interval must be between 100 milliseconds and 1 minute")
	}
	if config.RecoveryBatch == 0 {
		config.RecoveryBatch = platformGenerationDelayQueueDefaultRecoveryBatch
	}
	if config.RecoveryBatch < 1 || config.RecoveryBatch > 4096 {
		return nil, errors.New("generation delay queue recovery batch must be between 1 and 4096")
	}

	prefix := "{" + namespace + ":platform-generation-delay}:"
	return &PlatformGenerationDelayQueue{
		redis:            config.Redis,
		database:         config.Database,
		scheduledKey:     prefix + "scheduled:v1",
		inflightKey:      prefix + "inflight:v1",
		leaseTokenKey:    prefix + "lease-tokens:v1",
		recoveryKey:      prefix + "recovery:v1",
		dispatchLease:    config.DispatchLease,
		recoveryInterval: config.RecoveryInterval,
		recoveryBatch:    config.RecoveryBatch,
	}, nil
}

// ValidatePlatformGenerationDelayQueueRuntime is intended for worker startup.
// Production workers fail closed unless Redis is live and AOF persistence is
// enabled; a database-only polling path would silently weaken the contract.
func ValidatePlatformGenerationDelayQueueRuntime(ctx context.Context, workersEnabled bool, production bool) error {
	if !workersEnabled {
		return nil
	}
	if !common.RedisEnabled || common.RDB == nil {
		return errors.New("generation workers require Redis delayed-queue scheduling")
	}
	if _, err := NewPlatformGenerationDelayQueue(PlatformGenerationDelayQueueRuntimeConfig()); err != nil {
		return err
	}
	if err := common.RDB.Ping(ctx).Err(); err != nil {
		return fmt.Errorf("generation delay queue Redis ping failed: %w", err)
	}
	if !production {
		return nil
	}
	info, err := common.RDB.Info(ctx, "persistence").Result()
	if err != nil {
		return fmt.Errorf("generation delay queue cannot verify Redis AOF persistence: %w", err)
	}
	aofEnabled := false
	for _, line := range strings.Split(info, "\n") {
		if strings.TrimSpace(line) == "aof_enabled:1" {
			aofEnabled = true
			break
		}
	}
	if !aofEnabled {
		return errors.New("production generation delay queue requires Redis AOF persistence")
	}
	return nil
}

// AcquireDispatch synchronizes active database outbox rows into the Redis
// ZSET and atomically leases one due dispatch. The caller must obtain its
// fenced PostgreSQL submission claim only after this method succeeds.
func (queue *PlatformGenerationDelayQueue) AcquireDispatch(ctx context.Context) (*PlatformGenerationDelayQueueLease, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if _, err := queue.recoverIfDue(ctx); err != nil {
		return nil, err
	}
	now, err := queue.databaseClock(ctx)
	if err != nil {
		return nil, err
	}
	return queue.claimDueAt(ctx, now)
}

// RecoverFromDatabase rebuilds the Redis scheduler from every active outbox
// row. It is safe to run concurrently and is the recovery path after Redis
// restart, key eviction, or a failed post-transaction enqueue.
func (queue *PlatformGenerationDelayQueue) RecoverFromDatabase(ctx context.Context) (int, error) {
	if err := ctx.Err(); err != nil {
		return 0, err
	}
	now, err := queue.databaseClock(ctx)
	if err != nil {
		return 0, err
	}
	scheduled := 0
	lastID := int64(0)
	for {
		if err := ctx.Err(); err != nil {
			return scheduled, err
		}
		rows := make([]model.PlatformGenerationOutbox, 0, queue.recoveryBatch)
		err := queue.database.WithContext(ctx).
			Where("topic = ? AND state IN ? AND id > ?", "generation.submit", []string{
				model.PlatformGenerationOutboxPending,
				model.PlatformGenerationOutboxClaimed,
			}, lastID).
			Order("id ASC").
			Limit(queue.recoveryBatch).
			Find(&rows).Error
		if err != nil {
			return scheduled, fmt.Errorf("scan generation outbox for Redis recovery: %w", err)
		}
		for index := range rows {
			row := rows[index]
			availableAt := platformGenerationOutboxRedisAvailableAt(row)
			added, scheduleErr := queue.scheduleAt(ctx, row.ID, availableAt, now)
			if scheduleErr != nil {
				return scheduled, scheduleErr
			}
			if added {
				scheduled++
			}
			lastID = row.ID
		}
		if len(rows) < queue.recoveryBatch {
			return scheduled, nil
		}
	}
}

// SynchronizeOutbox projects one committed outbox row into Redis. Call this
// after a local busy/RPM release; it never updates provider-attempt state.
func (queue *PlatformGenerationDelayQueue) SynchronizeOutbox(ctx context.Context, outboxID int64) error {
	if outboxID <= 0 {
		return errors.New("generation delay queue outbox id must be positive")
	}
	now, err := queue.databaseClock(ctx)
	if err != nil {
		return err
	}
	var outbox model.PlatformGenerationOutbox
	err = queue.database.WithContext(ctx).Where("id = ? AND topic = ?", outboxID, "generation.submit").First(&outbox).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return queue.redis.ZRem(ctx, queue.scheduledKey, strconv.FormatInt(outboxID, 10)).Err()
	}
	if err != nil {
		return fmt.Errorf("read generation outbox for Redis synchronization: %w", err)
	}
	if outbox.State != model.PlatformGenerationOutboxPending && outbox.State != model.PlatformGenerationOutboxClaimed {
		return queue.redis.ZRem(ctx, queue.scheduledKey, strconv.FormatInt(outboxID, 10)).Err()
	}
	_, err = queue.scheduleAt(ctx, outbox.ID, platformGenerationOutboxRedisAvailableAt(outbox), now)
	return err
}

// FinalizeDispatch reconciles a Redis lease with the committed database row:
// terminal rows are acknowledged, while a locally delayed/expired claim is
// rescheduled at its database-owned availability time.
func (queue *PlatformGenerationDelayQueue) FinalizeDispatch(ctx context.Context, lease PlatformGenerationDelayQueueLease) error {
	if lease.OutboxID <= 0 || lease.Token == "" {
		return errors.New("generation delay queue lease is invalid")
	}
	var outbox model.PlatformGenerationOutbox
	err := queue.database.WithContext(ctx).Where("id = ? AND topic = ?", lease.OutboxID, "generation.submit").First(&outbox).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return queue.AcknowledgeDispatch(ctx, lease)
	}
	if err != nil {
		return fmt.Errorf("read generation outbox before Redis finalization: %w", err)
	}
	if outbox.State != model.PlatformGenerationOutboxPending && outbox.State != model.PlatformGenerationOutboxClaimed {
		return queue.AcknowledgeDispatch(ctx, lease)
	}
	return queue.RescheduleDispatch(ctx, lease, platformGenerationOutboxRedisAvailableAt(outbox))
}

func (queue *PlatformGenerationDelayQueue) AcknowledgeDispatch(ctx context.Context, lease PlatformGenerationDelayQueueLease) error {
	if lease.OutboxID <= 0 || lease.Token == "" {
		return errors.New("generation delay queue lease is invalid")
	}
	now, err := queue.databaseClock(ctx)
	if err != nil {
		return err
	}
	result, err := queue.redis.Eval(ctx, `
local current = redis.call('HGET', KEYS[2], ARGV[1])
if not current or current ~= ARGV[2] then
  return 0
end
local expires = redis.call('ZSCORE', KEYS[1], ARGV[1])
if not expires or tonumber(expires) <= tonumber(ARGV[3]) then
  return 0
end
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('HDEL', KEYS[2], ARGV[1])
return 1
`, []string{queue.inflightKey, queue.leaseTokenKey},
		strconv.FormatInt(lease.OutboxID, 10),
		lease.Token,
		strconv.FormatInt(now.UTC().UnixMilli(), 10),
	).Int()
	if err != nil {
		return fmt.Errorf("acknowledge generation Redis delay lease: %w", err)
	}
	if result != 1 {
		return ErrPlatformGenerationDelayQueueLeaseLost
	}
	return nil
}

func (queue *PlatformGenerationDelayQueue) RescheduleDispatch(
	ctx context.Context,
	lease PlatformGenerationDelayQueueLease,
	availableAt time.Time,
) error {
	if lease.OutboxID <= 0 || lease.Token == "" || availableAt.IsZero() {
		return errors.New("generation delay queue reschedule request is invalid")
	}
	now, err := queue.databaseClock(ctx)
	if err != nil {
		return err
	}
	result, err := queue.redis.Eval(ctx, `
local current = redis.call('HGET', KEYS[3], ARGV[1])
if not current or current ~= ARGV[2] then
  return 0
end
local expires = redis.call('ZSCORE', KEYS[2], ARGV[1])
if not expires or tonumber(expires) <= tonumber(ARGV[4]) then
  return 0
end
redis.call('ZREM', KEYS[2], ARGV[1])
redis.call('HDEL', KEYS[3], ARGV[1])
redis.call('ZADD', KEYS[1], ARGV[3], ARGV[1])
return 1
`, []string{queue.scheduledKey, queue.inflightKey, queue.leaseTokenKey},
		strconv.FormatInt(lease.OutboxID, 10),
		lease.Token,
		strconv.FormatInt(availableAt.UTC().UnixMilli(), 10),
		strconv.FormatInt(now.UTC().UnixMilli(), 10),
	).Int()
	if err != nil {
		return fmt.Errorf("reschedule generation Redis delay lease: %w", err)
	}
	if result != 1 {
		return ErrPlatformGenerationDelayQueueLeaseLost
	}
	return nil
}

func (queue *PlatformGenerationDelayQueue) recoverIfDue(ctx context.Context) (int, error) {
	token := uuid.NewString()
	acquired, err := queue.redis.SetNX(ctx, queue.recoveryKey, token, queue.recoveryInterval).Result()
	if err != nil {
		return 0, fmt.Errorf("acquire generation Redis recovery throttle: %w", err)
	}
	if !acquired {
		return 0, nil
	}
	recovered, err := queue.RecoverFromDatabase(ctx)
	if err == nil {
		return recovered, nil
	}
	_, releaseErr := queue.redis.Eval(ctx, `
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
`, []string{queue.recoveryKey}, token).Result()
	if releaseErr != nil {
		return recovered, fmt.Errorf("%v; release generation Redis recovery throttle: %w", err, releaseErr)
	}
	return recovered, err
}

func (queue *PlatformGenerationDelayQueue) scheduleAt(
	ctx context.Context,
	outboxID int64,
	availableAt time.Time,
	now time.Time,
) (bool, error) {
	if outboxID <= 0 || availableAt.IsZero() || now.IsZero() {
		return false, errors.New("generation delay queue schedule request is invalid")
	}
	result, err := queue.redis.Eval(ctx, `
local inflight = redis.call('ZSCORE', KEYS[2], ARGV[1])
if inflight and tonumber(inflight) > tonumber(ARGV[3]) then
  return 0
end
if inflight then
  redis.call('ZREM', KEYS[2], ARGV[1])
  redis.call('HDEL', KEYS[3], ARGV[1])
end
redis.call('ZADD', KEYS[1], ARGV[2], ARGV[1])
return 1
`, []string{queue.scheduledKey, queue.inflightKey, queue.leaseTokenKey},
		strconv.FormatInt(outboxID, 10),
		strconv.FormatInt(availableAt.UTC().UnixMilli(), 10),
		strconv.FormatInt(now.UTC().UnixMilli(), 10),
	).Int()
	if err != nil {
		return false, fmt.Errorf("schedule generation outbox in Redis: %w", err)
	}
	return result == 1, nil
}

func (queue *PlatformGenerationDelayQueue) claimDueAt(ctx context.Context, now time.Time) (*PlatformGenerationDelayQueueLease, error) {
	token := uuid.NewString()
	expiresAt := now.UTC().Add(queue.dispatchLease)
	result, err := queue.redis.Eval(ctx, `
local expired = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', ARGV[1], 'LIMIT', 0, ARGV[4])
for _, member in ipairs(expired) do
  redis.call('ZREM', KEYS[2], member)
  redis.call('HDEL', KEYS[3], member)
  redis.call('ZADD', KEYS[1], ARGV[1], member)
end
local due = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, 1)
if #due == 0 then
  return nil
end
local member = due[1]
if redis.call('ZREM', KEYS[1], member) ~= 1 then
  return nil
end
redis.call('ZADD', KEYS[2], ARGV[2], member)
redis.call('HSET', KEYS[3], member, ARGV[3])
return member
`, []string{queue.scheduledKey, queue.inflightKey, queue.leaseTokenKey},
		strconv.FormatInt(now.UTC().UnixMilli(), 10),
		strconv.FormatInt(expiresAt.UnixMilli(), 10),
		token,
		strconv.Itoa(platformGenerationDelayQueueExpiredBatch),
	).Result()
	if errors.Is(err, redis.Nil) || result == nil {
		return nil, gorm.ErrRecordNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("claim generation Redis delay lease: %w", err)
	}
	member, err := platformGenerationRedisString(result)
	if err != nil {
		return nil, err
	}
	outboxID, err := strconv.ParseInt(member, 10, 64)
	if err != nil || outboxID <= 0 {
		return nil, errors.New("generation Redis delay queue returned an invalid outbox id")
	}
	return &PlatformGenerationDelayQueueLease{OutboxID: outboxID, Token: token, ExpiresAt: expiresAt}, nil
}

func (queue *PlatformGenerationDelayQueue) databaseClock(ctx context.Context) (time.Time, error) {
	now, err := model.GetDBTimeTx(queue.database.WithContext(ctx))
	if err != nil {
		return time.Time{}, fmt.Errorf("read database clock for generation Redis delay queue: %w", err)
	}
	return now, nil
}

func platformGenerationOutboxRedisAvailableAt(outbox model.PlatformGenerationOutbox) time.Time {
	availableAt := outbox.AvailableAt.UTC()
	if outbox.State == model.PlatformGenerationOutboxClaimed && outbox.ClaimExpiresAt.After(availableAt) {
		availableAt = outbox.ClaimExpiresAt.UTC()
	}
	return availableAt
}

func platformGenerationRedisString(value any) (string, error) {
	switch typed := value.(type) {
	case string:
		return typed, nil
	case []byte:
		return string(typed), nil
	default:
		return "", fmt.Errorf("generation Redis delay queue returned %T instead of a string", value)
	}
}
