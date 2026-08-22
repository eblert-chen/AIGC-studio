//go:build integration

package platformrelay_test

import (
	"errors"
	"strings"
	"sync"
	"testing"

	"github.com/QuantumNous/new-api/model"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestPostgresUnknownReconciliationIsDiscoveredAndFencedExactlyOnce(t *testing.T) {
	resetIntegrationState(t)
	route := createProviderRoute(t, "unknown-reconciliation-model", "text_to_video", 10, 1)
	job, _ := createQueuedGeneration(t, route.Model, route.Mode)
	claim, err := model.ClaimPlatformGenerationProviderRoute(job.ID, route.Model, route.Mode)
	require.NoError(t, err)
	marked, err := model.MarkPlatformGenerationRouteSubmissionUnknown(job.ID, claim.SubmissionToken)
	require.NoError(t, err)
	require.True(t, marked)
	require.NoError(t, integrationDB.Model(&model.PlatformGenerationJob{}).
		Where("id = ?", job.ID).
		Updates(map[string]any{
			"status":                      model.PlatformGenerationStatusReconciliationRequired,
			"provider_route_id":           route.ID,
			"provider_channel_id":         route.ChannelID,
			"provider_key_index":          route.KeyIndex,
			"provider_submission_attempt": claim.Attempt,
			"error_code":                  model.PlatformGenerationErrorSubmissionReconciliationRequired,
			"error_message":               "Provider response was lost",
		}).Error)

	candidates, total, err := model.ListPlatformGenerationSubmissionUnknown(job.TenantID, 1, 50)
	require.NoError(t, err)
	require.EqualValues(t, 1, total)
	require.Len(t, candidates, 1)
	proof := candidates[0]
	assert.Equal(t, route.ID, proof.Route.ID)
	assert.Equal(t, claim.Attempt, proof.Admission.Attempt)
	assert.Regexp(t, `^sha256:[0-9a-f]{64}$`, proof.ReconciliationToken)

	type result struct {
		job      *model.PlatformGenerationJob
		event    *model.PlatformGenerationReconciliationEvent
		replayed bool
		err      error
	}
	resolution := model.PlatformGenerationReconciliationResolution{
		Created:                     false,
		ExpectedRouteID:             route.ID,
		ExpectedSubmissionAttempt:   claim.Attempt,
		ExpectedReconciliationToken: proof.ReconciliationToken,
		OperationID:                 "postgres-reconciliation-operation",
		RequestID:                   "postgres-reconciliation-request",
		VerificationReference:       "provider-console-postgres-case",
		ApprovedBy:                  "platform-admin-integration",
		ApprovalReason:              "Provider console confirmed no task exists",
		ApprovalKeyID:               "platform-approval-v1",
		ApprovalSignature:           "hmac-sha256:" + strings.Repeat("a", 64),
	}
	start := make(chan struct{})
	results := make(chan result, 2)
	var workers sync.WaitGroup
	for range 2 {
		workers.Add(1)
		go func() {
			defer workers.Done()
			<-start
			resolved, event, replayed, resolveErr := model.ResolvePlatformGenerationSubmissionUnknown(
				job.ID,
				job.TenantID,
				resolution,
			)
			results <- result{job: resolved, event: event, replayed: replayed, err: resolveErr}
		}()
	}
	close(start)
	workers.Wait()
	close(results)

	successes := 0
	conflicts := 0
	replays := 0
	eventID := ""
	for result := range results {
		switch {
		case result.err == nil:
			successes++
			require.NotNil(t, result.job)
			require.NotNil(t, result.event)
			assert.Equal(t, model.PlatformGenerationStatusFailed, result.job.Status)
			if eventID == "" {
				eventID = result.event.ID
			} else {
				assert.Equal(t, eventID, result.event.ID)
			}
			if result.replayed {
				replays++
			}
		case errors.Is(result.err, model.ErrPlatformGenerationReconciliationConflict):
			conflicts++
		default:
			require.NoError(t, result.err)
		}
	}
	assert.Equal(t, 2, successes)
	assert.Equal(t, 1, replays)
	assert.Zero(t, conflicts)

	var eventCount int64
	require.NoError(t, integrationDB.Model(&model.PlatformGenerationReconciliationEvent{}).
		Where("tenant_id = ? AND operation_id = ?", job.TenantID, resolution.OperationID).
		Count(&eventCount).Error)
	assert.EqualValues(t, 1, eventCount)
	receipt, err := model.GetPlatformGenerationReconciliationReceipt(job.ID, job.TenantID, resolution.OperationID)
	require.NoError(t, err)
	assert.Equal(t, eventID, receipt.Event.ID)
	assert.Equal(t, model.PlatformGenerationStatusFailed, receipt.CurrentStatus)
	assert.Equal(t, resolution.VerificationReference, receipt.Event.VerificationReference)
	assert.Equal(t, resolution.ApprovedBy, receipt.Event.ApprovedBy)
	assert.Equal(t, resolution.ApprovalReason, receipt.Event.ApprovalReason)
	assert.Equal(t, resolution.RequestID, receipt.Event.RequestID)

	mutated := resolution
	mutated.ApprovalReason = "A changed proof must conflict with the committed operation"
	_, _, _, err = model.ResolvePlatformGenerationSubmissionUnknown(job.ID, job.TenantID, mutated)
	assert.ErrorIs(t, err, model.ErrPlatformGenerationReconciliationConflict)
	assert.Error(t, integrationDB.Exec(
		"UPDATE platform_generation_reconciliation_events SET approval_reason = ? WHERE id = ?",
		"tampered",
		eventID,
	).Error)
	assert.Error(t, integrationDB.Exec(
		"DELETE FROM platform_generation_reconciliation_events WHERE id = ?",
		eventID,
	).Error)

	admission, persistedRoute, err := model.GetPlatformGenerationProviderRouteAssignment(job.ID)
	require.NoError(t, err)
	assert.Equal(t, model.PlatformGenerationRouteAdmissionReleased, admission.State)
	assert.False(t, admission.SlotHeld)
	assert.Zero(t, persistedRoute.ActiveCount)
}
