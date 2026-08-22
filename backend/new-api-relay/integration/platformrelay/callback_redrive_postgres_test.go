//go:build integration

package platformrelay_test

import (
	"errors"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestPostgresCallbackDeadLetterRedriveIsTenantFencedAndExactlyOnce(t *testing.T) {
	resetIntegrationState(t)
	tenantID := uuid.NewString()
	otherTenantID := uuid.NewString()
	now := time.Now().UTC().Truncate(time.Microsecond)
	delivery := model.PlatformGenerationCallbackDelivery{
		ID:             uuid.NewString(),
		TenantID:       tenantID,
		SourceClientID: "customer-platform",
		JobID:          uuid.NewString(),
		CallbackURL:    "https://callbacks.example.test/internal/relay?secret=hidden",
		RequestID:      "original-callback-request",
		PayloadJSON:    `{"api_version":"v1","immutable":true}`,
		PayloadSHA256:  strings.Repeat("a", 64),
		State:          model.PlatformGenerationCallbackDeadLetter,
		Attempts:       8,
		MaxAttempts:    8,
		AvailableAt:    now,
		ResponseStatus: 503,
		LastError:      model.PlatformGenerationCallbackFailureEndpoint,
		DeadLetteredAt: &now,
		CreatedAt:      now.Add(-time.Hour),
		UpdatedAt:      now,
	}
	require.NoError(t, integrationDB.Create(&delivery).Error)

	request := model.PlatformGenerationCallbackRedriveRequest{
		OperationID: "postgres-callback-redrive-operation",
		RequestID:   "postgres-callback-redrive-request",
		Actor:       "platform-owner-integration",
		Reason:      "Destination incident is resolved",
	}
	type result struct {
		receipt  *model.PlatformGenerationCallbackRedriveReceipt
		replayed bool
		err      error
	}
	const workers = 8
	results := make(chan result, workers)
	start := make(chan struct{})
	var wait sync.WaitGroup
	for range workers {
		wait.Add(1)
		go func() {
			defer wait.Done()
			<-start
			receipt, replayed, err := model.RedrivePlatformGenerationCallbackDelivery(
				delivery.ID,
				tenantID,
				request,
			)
			results <- result{receipt: receipt, replayed: replayed, err: err}
		}()
	}
	close(start)
	wait.Wait()
	close(results)

	successes := 0
	replays := 0
	eventID := ""
	for result := range results {
		require.NoError(t, result.err)
		require.NotNil(t, result.receipt)
		successes++
		if result.replayed {
			replays++
		}
		if eventID == "" {
			eventID = result.receipt.Event.ID
		} else {
			assert.Equal(t, eventID, result.receipt.Event.ID)
		}
	}
	assert.Equal(t, workers, successes)
	assert.Equal(t, workers-1, replays)

	var persisted model.PlatformGenerationCallbackDelivery
	require.NoError(t, integrationDB.First(&persisted, "id = ? AND tenant_id = ?", delivery.ID, tenantID).Error)
	assert.Equal(t, model.PlatformGenerationCallbackPending, persisted.State)
	assert.Zero(t, persisted.Attempts)
	assert.Nil(t, persisted.DeadLetteredAt)
	assert.Equal(t, delivery.PayloadJSON, persisted.PayloadJSON)
	assert.Equal(t, delivery.PayloadSHA256, persisted.PayloadSHA256)
	assert.Equal(t, delivery.CallbackURL, persisted.CallbackURL)
	assert.Equal(t, delivery.RequestID, persisted.RequestID)

	var eventCount int64
	require.NoError(t, integrationDB.Model(&model.PlatformGenerationCallbackRedriveEvent{}).
		Where("tenant_id = ? AND operation_id = ?", tenantID, request.OperationID).
		Count(&eventCount).Error)
	assert.EqualValues(t, 1, eventCount)
	receipt, err := model.GetPlatformGenerationCallbackRedriveReceipt(delivery.ID, tenantID, request.OperationID)
	require.NoError(t, err)
	assert.Equal(t, eventID, receipt.Event.ID)
	assert.Equal(t, request.RequestID, receipt.Event.RequestID)
	assert.Equal(t, request.Actor, receipt.Event.Actor)
	assert.Equal(t, request.Reason, receipt.Event.Reason)
	assert.Equal(t, model.PlatformGenerationCallbackDeadLetter, receipt.Event.PreviousState)
	assert.Equal(t, model.PlatformGenerationCallbackPending, receipt.CurrentState)
	assert.Regexp(t, `^[0-9a-f]{64}$`, receipt.Event.CallbackURLSHA256)
	assert.Regexp(t, `^[0-9a-f]{64}$`, receipt.Event.ReceiptSHA256)

	_, _, err = model.RedrivePlatformGenerationCallbackDelivery(
		delivery.ID,
		otherTenantID,
		request,
	)
	assert.Error(t, err)

	mutated := request
	mutated.Reason = "A changed reason must conflict"
	_, _, err = model.RedrivePlatformGenerationCallbackDelivery(delivery.ID, tenantID, mutated)
	assert.ErrorIs(t, err, model.ErrPlatformGenerationCallbackRedriveConflict)

	assert.Error(t, integrationDB.Exec(
		"UPDATE platform_generation_callback_redrive_events SET reason = ? WHERE id = ?",
		"tampered",
		eventID,
	).Error)
	assert.Error(t, integrationDB.Exec(
		"DELETE FROM platform_generation_callback_redrive_events WHERE id = ?",
		eventID,
	).Error)
	assert.Error(t, integrationDB.Exec("TRUNCATE TABLE platform_generation_callback_redrive_events").Error)

	nonDeadLetter := delivery
	nonDeadLetter.ID = uuid.NewString()
	nonDeadLetter.State = model.PlatformGenerationCallbackDelivered
	nonDeadLetter.DeadLetteredAt = nil
	require.NoError(t, integrationDB.Create(&nonDeadLetter).Error)
	otherOperation := request
	otherOperation.OperationID = "postgres-callback-redrive-non-dead-letter"
	_, _, err = model.RedrivePlatformGenerationCallbackDelivery(nonDeadLetter.ID, tenantID, otherOperation)
	assert.True(t, errors.Is(err, model.ErrPlatformGenerationCallbackNotDeadLetter))
}
