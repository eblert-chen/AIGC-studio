package model

import (
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/constant"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func TestClaimPlatformGenerationSubmissionRepairsTerminalJobOutboxWithoutNilSuccess(t *testing.T) {
	preparePlatformGenerationRouteTest(t)
	require.NoError(t, DB.AutoMigrate(&PlatformGenerationOutbox{}))
	require.NoError(t, DB.Exec("DELETE FROM platform_generation_outboxes").Error)
	t.Cleanup(func() { _ = DB.Exec("DELETE FROM platform_generation_outboxes").Error })

	job := PlatformGenerationJob{
		ID:                         uuid.NewString(),
		TenantID:                   uuid.NewString(),
		SourceClientID:             "platform",
		RequestID:                  "terminal-outbox-repair-request",
		IdempotencyKey:             "terminal-outbox-repair-key",
		RequestHash:                strings.Repeat("a", 64),
		RequestJSON:                `{}`,
		Model:                      "terminal-outbox-repair-model",
		Mode:                       "text_to_video",
		ExpectedCapabilityRevision: "sha256:" + strings.Repeat("b", 64),
		CapabilityRevision:         "sha256:" + strings.Repeat("b", 64),
		Status:                     PlatformGenerationStatusFailed,
		OutputsJSON:                `[]`,
		ErrorDetailsJSON:           `{}`,
	}
	require.NoError(t, DB.Create(&job).Error)
	outbox := PlatformGenerationOutbox{
		JobID:       job.ID,
		Topic:       "generation.submit",
		State:       PlatformGenerationOutboxPending,
		AvailableAt: time.Now().UTC().Add(-time.Minute),
	}
	require.NoError(t, DB.Create(&outbox).Error)

	claim, err := ClaimPlatformGenerationSubmission(time.Minute, outbox.ID)
	require.Nil(t, claim)
	require.True(t, errors.Is(err, gorm.ErrRecordNotFound), "stale outbox repair must be reported as an empty queue: %v", err)

	var repaired PlatformGenerationOutbox
	require.NoError(t, DB.First(&repaired, outbox.ID).Error)
	assert.Equal(t, PlatformGenerationOutboxCompleted, repaired.State)
	assert.Empty(t, repaired.ClaimToken)
	assert.True(t, repaired.ClaimExpiresAt.IsZero())
}

func TestRenewPlatformGenerationSubmissionFencesExpiredAndReplacementOwners(t *testing.T) {
	preparePlatformGenerationRouteTest(t)
	require.NoError(t, DB.AutoMigrate(&PlatformGenerationOutbox{}))
	require.NoError(t, DB.Exec("DELETE FROM platform_generation_outboxes").Error)
	t.Cleanup(func() { _ = DB.Exec("DELETE FROM platform_generation_outboxes").Error })

	job := PlatformGenerationJob{
		ID:                         uuid.NewString(),
		TenantID:                   uuid.NewString(),
		SourceClientID:             "platform",
		RequestID:                  "submission-renewal-request",
		IdempotencyKey:             "submission-renewal-key",
		RequestHash:                strings.Repeat("c", 64),
		RequestJSON:                `{}`,
		Model:                      "submission-renewal-model",
		Mode:                       "text_to_video",
		ExpectedCapabilityRevision: "sha256:" + strings.Repeat("d", 64),
		CapabilityRevision:         "sha256:" + strings.Repeat("d", 64),
		Status:                     PlatformGenerationStatusQueued,
		OutputsJSON:                `[]`,
		ErrorDetailsJSON:           `{}`,
	}
	require.NoError(t, DB.Create(&job).Error)
	outbox := PlatformGenerationOutbox{
		JobID:       job.ID,
		Topic:       "generation.submit",
		State:       PlatformGenerationOutboxPending,
		AvailableAt: time.Now().UTC().Add(-time.Minute),
	}
	require.NoError(t, DB.Create(&outbox).Error)

	first, err := ClaimPlatformGenerationSubmission(time.Minute, outbox.ID)
	require.NoError(t, err)
	renewed, err := RenewPlatformGenerationSubmission(*first, 2*time.Minute)
	require.NoError(t, err)
	assert.True(t, renewed)
	var renewedJob PlatformGenerationJob
	var renewedOutbox PlatformGenerationOutbox
	require.NoError(t, DB.First(&renewedJob, "id = ?", job.ID).Error)
	require.NoError(t, DB.First(&renewedOutbox, outbox.ID).Error)
	assert.Equal(t, renewedJob.SubmissionLeaseExpiresAt, renewedOutbox.ClaimExpiresAt)
	assert.True(t, renewedJob.SubmissionLeaseExpiresAt.After(first.Job.SubmissionLeaseExpiresAt))

	past := time.Now().UTC().Add(-time.Minute)
	require.NoError(t, DB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Model(&PlatformGenerationOutbox{}).Where("id = ?", outbox.ID).
			UpdateColumn("claim_expires_at", past).Error; err != nil {
			return err
		}
		return tx.Model(&PlatformGenerationJob{}).Where("id = ?", job.ID).
			UpdateColumn("submission_lease_expires_at", past).Error
	}))
	renewed, err = RenewPlatformGenerationSubmission(*first, time.Minute)
	require.NoError(t, err)
	assert.False(t, renewed, "an expired owner must not revive itself")

	replacement, err := ClaimPlatformGenerationSubmission(time.Minute, outbox.ID)
	require.NoError(t, err)
	assert.NotEqual(t, first.Token, replacement.Token)
	renewed, err = RenewPlatformGenerationSubmission(*first, time.Minute)
	require.NoError(t, err)
	assert.False(t, renewed, "a stale owner must not extend its replacement's lease")
	renewed, err = RenewPlatformGenerationSubmission(*replacement, time.Minute)
	require.NoError(t, err)
	assert.True(t, renewed)
}

func TestPlatformGenerationTransferLeaseRejectsExpiredAndStaleOwners(t *testing.T) {
	preparePlatformGenerationRouteTest(t)
	job := PlatformGenerationJob{
		ID:                         uuid.NewString(),
		TenantID:                   uuid.NewString(),
		SourceClientID:             "platform",
		RequestID:                  "transfer-fence-request",
		IdempotencyKey:             "transfer-fence-key",
		RequestHash:                strings.Repeat("a", 64),
		RequestJSON:                `{}`,
		Model:                      "transfer-model",
		Mode:                       "text_to_video",
		ExpectedCapabilityRevision: "sha256:" + strings.Repeat("b", 64),
		CapabilityRevision:         "sha256:" + strings.Repeat("b", 64),
		Status:                     PlatformGenerationStatusTransferring,
		Progress:                   95,
		OutputsJSON:                `[]`,
		ErrorDetailsJSON:           `{}`,
		NextTransferAt:             time.Now().UTC().Add(-time.Minute),
	}
	require.NoError(t, DB.Create(&job).Error)

	first, firstToken, err := ClaimPlatformGenerationTransfer(time.Minute)
	require.NoError(t, err)
	assert.Equal(t, job.ID, first.ID)
	firstObjectKey := "outputs/" + job.TenantID + "/" + job.ID + "/" + uuid.NewString()
	_, err = CreatePlatformArtifactUploadIntent(job.ID, firstToken, firstObjectKey, "test_store", strings.Repeat("c", 64))
	require.NoError(t, err)
	renewed, err := RenewPlatformGenerationTransfer(job.ID, firstToken, 2*time.Minute)
	require.NoError(t, err)
	assert.True(t, renewed)
	var afterRenewal PlatformGenerationJob
	require.NoError(t, DB.First(&afterRenewal, "id = ?", job.ID).Error)
	assert.True(t, afterRenewal.TransferLeaseExpiresAt.After(first.TransferLeaseExpiresAt))
	require.NoError(t, DB.Model(&PlatformGenerationJob{}).Where("id = ?", job.ID).
		Update("transfer_lease_expires_at", time.Now().UTC().Add(-time.Second)).Error)
	renewed, err = RenewPlatformGenerationTransfer(job.ID, firstToken, time.Minute)
	require.NoError(t, err)
	assert.False(t, renewed, "an expired worker must not revive its own lease")
	won, err := CompletePlatformGenerationTransfer(
		job.ID,
		firstToken,
		firstObjectKey,
		`[{"asset_id":"stale","object_key":"`+firstObjectKey+`"}]`,
	)
	require.NoError(t, err)
	assert.False(t, won)

	second, secondToken, err := ClaimPlatformGenerationTransfer(time.Minute)
	require.NoError(t, err)
	assert.Equal(t, job.ID, second.ID)
	assert.NotEqual(t, firstToken, secondToken)
	secondObjectKey := "outputs/" + job.TenantID + "/" + job.ID + "/" + uuid.NewString()
	_, err = CreatePlatformArtifactUploadIntent(job.ID, secondToken, secondObjectKey, "test_store", strings.Repeat("c", 64))
	require.NoError(t, err)
	renewed, err = RenewPlatformGenerationTransfer(job.ID, firstToken, time.Minute)
	require.NoError(t, err)
	assert.False(t, renewed, "a stale token must not extend the replacement owner")
	won, err = ReleasePlatformGenerationTransfer(
		job.ID,
		firstToken,
		time.Second,
		PlatformGenerationErrorArtifactTransferRetrying,
		"stale worker",
	)
	require.NoError(t, err)
	assert.False(t, won)
	won, err = CompletePlatformGenerationTransfer(
		job.ID,
		secondToken,
		secondObjectKey,
		`[{"asset_id":"current","object_key":"`+secondObjectKey+`"}]`,
	)
	require.NoError(t, err)
	assert.True(t, won)

	var persisted PlatformGenerationJob
	require.NoError(t, DB.First(&persisted, "id = ?", job.ID).Error)
	assert.Equal(t, PlatformGenerationStatusSucceeded, persisted.Status)
	assert.Contains(t, persisted.OutputsJSON, "current")
	assert.NotContains(t, persisted.OutputsJSON, "stale")
	assert.Equal(t, 2, persisted.ArtifactTransferAttempts)
}

func TestStagePlatformGenerationNativeTaskRecoveryRejectsExpiredWorkerAfterTakeover(t *testing.T) {
	preparePlatformGenerationRouteTest(t)
	require.NoError(t, DB.AutoMigrate(&PlatformGenerationOutbox{}, &Task{}))
	require.NoError(t, DB.Exec("DELETE FROM platform_generation_outboxes").Error)
	t.Cleanup(func() { _ = DB.Exec("DELETE FROM platform_generation_outboxes").Error })
	route := createPlatformGenerationRouteFixture(t, "native-stage-fence", 10, 1)
	jobID := uuid.NewString()
	job := PlatformGenerationJob{
		ID: jobID, TenantID: uuid.NewString(), SourceClientID: "platform",
		RequestID: "native-stage-fence-request", IdempotencyKey: "native-stage-fence-key",
		RequestHash: strings.Repeat("c", 64), RequestJSON: `{}`,
		Model: route.Model, Mode: route.Mode,
		ExpectedCapabilityRevision: "sha256:" + strings.Repeat("d", 64),
		CapabilityRevision:         "sha256:" + strings.Repeat("d", 64),
		Status:                     PlatformGenerationStatusQueued,
	}
	require.NoError(t, DB.Create(&job).Error)
	outbox := PlatformGenerationOutbox{
		JobID: jobID, Topic: "generation.submit", State: PlatformGenerationOutboxPending,
		AvailableAt: time.Now().UTC().Add(-time.Minute),
	}
	require.NoError(t, DB.Create(&outbox).Error)

	ownerA, err := ClaimPlatformGenerationSubmission(time.Minute, outbox.ID)
	require.NoError(t, err)
	routeClaim, err := ClaimPlatformGenerationProviderRoute(jobID, route.Model, route.Mode)
	require.NoError(t, err)
	_, err = BeginPlatformGenerationRouteSubmission(jobID, route.ID, ownerA.Token, routeClaim.SubmissionToken)
	require.NoError(t, err)

	expiredAt := time.Now().UTC().Add(-time.Minute)
	require.NoError(t, DB.Model(&PlatformGenerationJob{}).Where("id = ?", jobID).
		Update("submission_lease_expires_at", expiredAt).Error)
	require.NoError(t, DB.Model(&PlatformGenerationOutbox{}).Where("id = ?", outbox.ID).
		Update("claim_expires_at", expiredAt).Error)
	ownerB, err := ClaimPlatformGenerationSubmission(time.Minute, outbox.ID)
	require.NoError(t, err)
	require.NotEqual(t, ownerA.Token, ownerB.Token)

	nativeTaskID, err := PlatformGenerationNativeTaskID(jobID)
	require.NoError(t, err)
	keyIndex := route.KeyIndex
	recoveryTemplate := &Task{
		TaskID: nativeTaskID, ChannelId: route.ChannelID, Platform: constant.TaskPlatform("fence-test"),
		UserId: 42, Group: "service", Action: constant.TaskActionTextGenerate, SubmitTime: time.Now().Unix(),
		Properties: Properties{UpstreamModelName: route.UpstreamModel, OriginModelName: route.Model},
		PrivateData: TaskPrivateData{
			PinnedKeyIndex: &keyIndex, PinnedKeyFingerprint: route.KeyFingerprint,
			TransientProviderKey: "provider-key-native-stage-fence",
			BillingSource:        TaskBillingSourcePlatformExternal,
		},
	}
	err = StagePlatformGenerationNativeTaskRecovery(jobID, ownerA.Token, recoveryTemplate)
	require.ErrorContains(t, err, "worker lease is stale")
	var afterStale PlatformGenerationJob
	require.NoError(t, DB.First(&afterStale, "id = ?", jobID).Error)
	require.Empty(t, afterStale.NativeTaskRecoveryJSON)
	require.Empty(t, afterStale.NativeTaskID)

	require.NoError(t, StagePlatformGenerationNativeTaskRecovery(jobID, ownerB.Token, recoveryTemplate))
	var afterCurrent PlatformGenerationJob
	require.NoError(t, DB.First(&afterCurrent, "id = ?", jobID).Error)
	require.NotEmpty(t, afterCurrent.NativeTaskRecoveryJSON)
	require.Equal(t, nativeTaskID, afterCurrent.NativeTaskID)
}

func TestManualUnknownResolutionNeverChangesTheStickyRoute(t *testing.T) {
	t.Run("not created releases only the original slot and terminates", func(t *testing.T) {
		preparePlatformGenerationRouteTest(t)
		route := createPlatformGenerationRouteFixture(t, "manual.not-created", 10, 1)
		jobID := uuid.NewString()
		claim, err := ClaimPlatformGenerationProviderRoute(jobID, route.Model, route.Mode)
		require.NoError(t, err)
		marked, err := MarkPlatformGenerationRouteSubmissionUnknown(jobID, claim.SubmissionToken)
		require.NoError(t, err)
		require.True(t, marked)
		job := manualUnknownGenerationJob(jobID, route, claim.Attempt)
		require.NoError(t, DB.Create(&job).Error)
		reconciliationToken := requireUnknownReconciliationToken(t, job.ID, job.TenantID)
		resolution := manualUnknownReconciliationResolution(false, "", route.ID, claim.Attempt, reconciliationToken)
		resolution.ExpectedReconciliationToken = "sha256:" + strings.Repeat("0", 64)
		_, _, _, err = ResolvePlatformGenerationSubmissionUnknown(
			job.ID,
			job.TenantID,
			resolution,
		)
		assert.ErrorIs(t, err, ErrPlatformGenerationReconciliationConflict)
		candidateAfterStaleToken, err := GetPlatformGenerationSubmissionUnknown(job.ID, job.TenantID)
		require.NoError(t, err)
		assert.Equal(t, reconciliationToken, candidateAfterStaleToken.ReconciliationToken)

		resolution.ExpectedReconciliationToken = reconciliationToken
		resolved, event, replayed, err := ResolvePlatformGenerationSubmissionUnknown(
			job.ID,
			job.TenantID,
			resolution,
		)
		require.NoError(t, err)
		require.NotNil(t, event)
		assert.False(t, replayed)
		assert.Equal(t, PlatformGenerationStatusFailed, resolved.Status)
		assert.Equal(t, PlatformGenerationErrorSubmissionConfirmedNotCreated, resolved.ErrorCode)
		admission, persistedRoute, err := GetPlatformGenerationProviderRouteAssignment(job.ID)
		require.NoError(t, err)
		assert.Equal(t, route.ID, persistedRoute.ID)
		assert.Zero(t, persistedRoute.ActiveCount)
		assert.Equal(t, PlatformGenerationRouteAdmissionReleased, admission.State)
		assert.False(t, admission.SlotHeld)

		replayedJob, replayedEvent, replayed, err := ResolvePlatformGenerationSubmissionUnknown(
			job.ID,
			job.TenantID,
			resolution,
		)
		require.NoError(t, err)
		assert.True(t, replayed)
		assert.Equal(t, resolved.ID, replayedJob.ID)
		assert.Equal(t, event.ID, replayedEvent.ID)
		rotatedKey := resolution
		rotatedKey.ApprovalKeyID = "platform-approval-v2"
		rotatedKey.ApprovalSignature = "hmac-sha256:" + strings.Repeat("b", 64)
		_, rotatedReceipt, replayed, err := ResolvePlatformGenerationSubmissionUnknown(
			job.ID,
			job.TenantID,
			rotatedKey,
		)
		require.NoError(t, err)
		assert.True(t, replayed)
		assert.Equal(t, event.ApprovalKeyID, rotatedReceipt.ApprovalKeyID)
		assert.Equal(t, event.ApprovalSignature, rotatedReceipt.ApprovalSignature)
		mutated := resolution
		mutated.ApprovalReason = "A conflicting proof must not overwrite the receipt"
		_, _, _, err = ResolvePlatformGenerationSubmissionUnknown(job.ID, job.TenantID, mutated)
		assert.ErrorIs(t, err, ErrPlatformGenerationReconciliationConflict)
	})

	t.Run("created restores polling on the exact original route", func(t *testing.T) {
		preparePlatformGenerationRouteTest(t)
		require.NoError(t, DB.AutoMigrate(&Task{}))
		require.NoError(t, DB.Exec("DELETE FROM tasks").Error)
		t.Cleanup(func() { _ = DB.Exec("DELETE FROM tasks").Error })
		route := createPlatformGenerationRouteFixture(t, "manual.created", 10, 1)
		jobID := uuid.NewString()
		job := manualUnknownGenerationJob(jobID, route, 0)
		job.Status = PlatformGenerationStatusSubmitting
		job.ProviderRouteID = 0
		job.ProviderChannelID = 0
		job.ProviderSubmissionAttempt = 0
		job.SubmissionLeaseToken = uuid.NewString()
		job.SubmissionLeaseExpiresAt = time.Now().UTC().Add(time.Minute)
		require.NoError(t, DB.Create(&job).Error)

		claim, err := ClaimPlatformGenerationProviderRoute(jobID, route.Model, route.Mode)
		require.NoError(t, err)
		_, err = BeginPlatformGenerationRouteSubmission(jobID, route.ID, job.SubmissionLeaseToken, claim.SubmissionToken)
		require.NoError(t, err)
		nativeTaskID, err := PlatformGenerationNativeTaskID(jobID)
		require.NoError(t, err)
		keyIndex := route.KeyIndex
		pinnedKey := "provider-key-manual.created"
		recoveryTemplate := &Task{
			TaskID:     nativeTaskID,
			ChannelId:  route.ChannelID,
			Platform:   constant.TaskPlatform("manual-test"),
			UserId:     42,
			Group:      "service",
			Action:     constant.TaskActionTextGenerate,
			SubmitTime: time.Now().Unix(),
			Properties: Properties{UpstreamModelName: route.UpstreamModel, OriginModelName: route.Model},
			PrivateData: TaskPrivateData{
				PinnedKeyIndex:       &keyIndex,
				PinnedKeyFingerprint: route.KeyFingerprint,
				TransientProviderKey: pinnedKey,
				BillingSource:        TaskBillingSourcePlatformExternal,
			},
		}
		require.NoError(t, StagePlatformGenerationNativeTaskRecovery(jobID, job.SubmissionLeaseToken, recoveryTemplate))
		marked, err := MarkPlatformGenerationRouteSubmissionUnknown(jobID, claim.SubmissionToken)
		require.NoError(t, err)
		require.True(t, marked)
		require.NoError(t, DB.Model(&PlatformGenerationJob{}).Where("id = ?", jobID).Updates(map[string]any{
			"status":                      PlatformGenerationStatusReconciliationRequired,
			"submission_lease_token":      "",
			"submission_lease_expires_at": nil,
			"error_code":                  PlatformGenerationErrorSubmissionReconciliationRequired,
		}).Error)
		reconciliationToken := requireUnknownReconciliationToken(t, job.ID, job.TenantID)
		resolution := manualUnknownReconciliationResolution(true, "provider-task-definitive-123", route.ID, claim.Attempt, reconciliationToken)
		_, err = GetPlatformGenerationNativeTask(nativeTaskID, route.ChannelID)
		assert.ErrorIs(t, err, gorm.ErrRecordNotFound, "the recovery path must cover a wholly missing native Task row")

		staleAttempt := resolution
		staleAttempt.ExpectedSubmissionAttempt++
		_, _, _, err = ResolvePlatformGenerationSubmissionUnknown(
			job.ID,
			job.TenantID,
			staleAttempt,
		)
		assert.ErrorIs(t, err, ErrPlatformGenerationReconciliationConflict, "a stale attempt cannot materialize a polling task")
		_, err = GetPlatformGenerationNativeTask(nativeTaskID, route.ChannelID)
		assert.ErrorIs(t, err, gorm.ErrRecordNotFound)
		collision := Task{
			TaskID:     nativeTaskID,
			ChannelId:  route.ChannelID + 1,
			Platform:   constant.TaskPlatform("wrong-channel"),
			SubmitTime: time.Now().Unix(),
			Status:     TaskStatusSubmitted,
			Progress:   "0%",
		}
		require.NoError(t, DB.Create(&collision).Error)
		_, _, _, err = ResolvePlatformGenerationSubmissionUnknown(
			job.ID,
			job.TenantID,
			resolution,
		)
		assert.ErrorIs(t, err, ErrPlatformGenerationReconciliationConflict, "a task-id collision on another channel must never authorize polling there")
		require.NoError(t, DB.Delete(&collision).Error)

		resolved, event, replayed, err := ResolvePlatformGenerationSubmissionUnknown(
			job.ID,
			job.TenantID,
			resolution,
		)
		require.NoError(t, err)
		require.NotNil(t, event)
		assert.False(t, replayed)
		assert.Equal(t, PlatformGenerationStatusProcessing, resolved.Status)
		assert.Equal(t, route.ID, resolved.ProviderRouteID)
		assert.Equal(t, route.ChannelID, resolved.ProviderChannelID)
		assert.Equal(t, "provider-task-definitive-123", resolved.UpstreamTaskID)
		assert.False(t, resolved.NativeBillingReconciliationNeeded)
		admission, persistedRoute, err := GetPlatformGenerationProviderRouteAssignment(job.ID)
		require.NoError(t, err)
		assert.Equal(t, route.ID, persistedRoute.ID)
		assert.Equal(t, 1, persistedRoute.ActiveCount)
		assert.Equal(t, PlatformGenerationRouteAdmissionPosting, admission.State)
		assert.True(t, admission.SlotHeld)
		persistedNative, err := GetPlatformGenerationNativeTask(nativeTaskID, route.ChannelID)
		require.NoError(t, err)
		assert.Equal(t, "provider-task-definitive-123", persistedNative.PrivateData.UpstreamTaskID)
		assert.EqualValues(t, TaskStatusSubmitted, persistedNative.Status)
		assert.Empty(t, persistedNative.PrivateData.TransientProviderKey)
		assert.NotEmpty(t, persistedNative.PrivateData.ProviderCredentialVersion)
		assert.Equal(t, job.TenantID, persistedNative.PrivateData.ProviderCredentialTenantID)
		resolvedKey, resolveErr := ResolveTaskProviderCredential(persistedNative)
		require.NoError(t, resolveErr)
		assert.Equal(t, pinnedKey, resolvedKey)
		assert.Equal(t, route.KeyFingerprint, persistedNative.PrivateData.PinnedKeyFingerprint)
		require.NotNil(t, persistedNative.PrivateData.PinnedKeyIndex)
		assert.Equal(t, route.KeyIndex, *persistedNative.PrivateData.PinnedKeyIndex)
		assert.Equal(t, constant.TaskActionTextGenerate, persistedNative.Action)
		assert.Zero(t, persistedNative.Quota, "recovery must not invent an unproven billing settlement")
		assert.Equal(t, TaskBillingSourcePlatformExternal, persistedNative.PrivateData.BillingSource)
		assert.Nil(t, persistedNative.PrivateData.BillingContext)
	})
}

func requireUnknownReconciliationToken(t *testing.T, jobID string, tenantID string) string {
	t.Helper()
	candidate, err := GetPlatformGenerationSubmissionUnknown(jobID, tenantID)
	require.NoError(t, err)
	require.Regexp(t, `^sha256:[0-9a-f]{64}$`, candidate.ReconciliationToken)
	return candidate.ReconciliationToken
}

func manualUnknownReconciliationResolution(
	created bool,
	upstreamTaskID string,
	routeID int64,
	attempt int,
	reconciliationToken string,
) PlatformGenerationReconciliationResolution {
	return PlatformGenerationReconciliationResolution{
		Created:                     created,
		UpstreamTaskID:              upstreamTaskID,
		ExpectedRouteID:             routeID,
		ExpectedSubmissionAttempt:   attempt,
		ExpectedReconciliationToken: reconciliationToken,
		OperationID:                 "manual-reconciliation-operation",
		RequestID:                   "manual-reconciliation-request",
		VerificationReference:       "provider-console-manual-check",
		ApprovedBy:                  "platform-admin-test",
		ApprovalReason:              "Provider console evidence was reviewed",
		ApprovalKeyID:               "platform-approval-v1",
		ApprovalSignature:           "hmac-sha256:" + strings.Repeat("a", 64),
	}
}

func manualUnknownGenerationJob(
	jobID string,
	route *PlatformGenerationProviderRoute,
	attempt int,
) PlatformGenerationJob {
	return PlatformGenerationJob{
		ID:                         jobID,
		TenantID:                   uuid.NewString(),
		SourceClientID:             "platform",
		RequestID:                  "manual-reconciliation-request",
		IdempotencyKey:             "manual-reconciliation-key-" + jobID,
		RequestHash:                strings.Repeat("c", 64),
		RequestJSON:                `{}`,
		Model:                      route.Model,
		Mode:                       route.Mode,
		ExpectedCapabilityRevision: "sha256:" + strings.Repeat("d", 64),
		CapabilityRevision:         "sha256:" + strings.Repeat("d", 64),
		Status:                     PlatformGenerationStatusReconciliationRequired,
		Progress:                   0,
		ProviderRouteID:            route.ID,
		ProviderChannelID:          route.ChannelID,
		ProviderKeyIndex:           route.KeyIndex,
		ProviderSubmissionAttempt:  attempt,
		OutputsJSON:                `[]`,
		ErrorCode:                  PlatformGenerationErrorSubmissionReconciliationRequired,
		ErrorMessage:               "manual reconciliation required",
		ErrorRetryable:             true,
		ErrorDetailsJSON:           `{}`,
	}
}
