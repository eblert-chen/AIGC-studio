package model

import (
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/glebarez/sqlite"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

type legacyPlatformArtifactUploadIntentWithoutDeletePasses struct {
	ID             string    `gorm:"type:varchar(36);primaryKey"`
	JobID          string    `gorm:"type:varchar(36);not null"`
	TransferToken  string    `gorm:"type:varchar(36);not null"`
	ObjectKey      string    `gorm:"type:varchar(160);not null"`
	StoreKind      string    `gorm:"type:varchar(32);not null"`
	StoreBindingID string    `gorm:"type:varchar(64);not null"`
	StoreVersionID string    `gorm:"type:varchar(256)"`
	State          string    `gorm:"type:varchar(16);not null"`
	Attempts       int       `gorm:"not null"`
	AvailableAt    time.Time `gorm:"not null"`
	ClaimToken     string    `gorm:"type:varchar(36)"`
	ClaimExpiresAt time.Time
	LastErrorCode  string `gorm:"type:varchar(160)"`
	PutCompletedAt *time.Time
	QuarantinedAt  *time.Time
	CleanedAt      *time.Time
	PublishedAt    *time.Time
	CreatedAt      time.Time
	UpdatedAt      time.Time
}

func (legacyPlatformArtifactUploadIntentWithoutDeletePasses) TableName() string {
	return "platform_artifact_upload_intents"
}

func createPlatformArtifactUploadTestIntent(
	t *testing.T,
) (PlatformGenerationJob, string, string, PlatformArtifactUploadIntent) {
	t.Helper()
	preparePlatformGenerationRouteTest(t)
	route := createPlatformGenerationRouteFixture(t, "artifact-upload-"+uuid.NewString(), 10, 2)
	now := time.Now().UTC()
	job := PlatformGenerationJob{
		ID:                         uuid.NewString(),
		TenantID:                   uuid.NewString(),
		SourceClientID:             "platform",
		RequestID:                  "artifact-upload-intent-request",
		IdempotencyKey:             uuid.NewString(),
		RequestHash:                strings.Repeat("a", 64),
		RequestJSON:                `{}`,
		Model:                      route.Model,
		Mode:                       route.Mode,
		ExpectedCapabilityRevision: "sha256:" + strings.Repeat("b", 64),
		CapabilityRevision:         "sha256:" + strings.Repeat("b", 64),
		Status:                     PlatformGenerationStatusTransferring,
		Progress:                   95,
		ProviderRouteID:            route.ID,
		ProviderChannelID:          route.ChannelID,
		ProviderKeyIndex:           route.KeyIndex,
		ProviderSubmissionAttempt:  1,
		OutputsJSON:                `[]`,
		ErrorDetailsJSON:           `{}`,
		NextTransferAt:             now.Add(-time.Minute),
	}
	require.NoError(t, DB.Create(&job).Error)
	_, transferToken, err := ClaimPlatformGenerationTransfer(time.Minute)
	require.NoError(t, err)
	objectKey := "outputs/" + job.TenantID + "/" + job.ID + "/" + uuid.NewString()
	intent, err := CreatePlatformArtifactUploadIntent(
		job.ID,
		transferToken,
		objectKey,
		"test_store",
		strings.Repeat("c", 64),
	)
	require.NoError(t, err)
	return job, transferToken, objectKey, *intent
}

func TestPlatformArtifactCleanupClaimFencesMultipleWorkersAndExpiredTokens(t *testing.T) {
	job, transferToken, _, intent := createPlatformArtifactUploadTestIntent(t)
	require.NoError(t, DB.Model(&PlatformGenerationJob{}).Where("id = ?", job.ID).
		Update("transfer_lease_expires_at", time.Now().UTC().Add(-time.Second)).Error)
	_, err := SchedulePlatformArtifactUploadCleanup(job.ID, transferToken)
	require.NoError(t, err)

	first, err := ClaimPlatformArtifactCleanup(time.Minute)
	require.NoError(t, err)
	assert.Equal(t, intent.ID, first.Intent.ID)
	assert.Equal(t, 1, first.Intent.Attempts)
	_, err = ClaimPlatformArtifactCleanup(time.Minute)
	assert.ErrorIs(t, err, gorm.ErrRecordNotFound)

	require.NoError(t, DB.Model(&PlatformArtifactUploadIntent{}).Where("id = ?", intent.ID).
		Update("claim_expires_at", time.Now().UTC().Add(-time.Second)).Error)
	second, err := ClaimPlatformArtifactCleanup(time.Minute)
	require.NoError(t, err)
	assert.NotEqual(t, first.Token, second.Token)
	assert.Equal(t, 2, second.Intent.Attempts)

	won, err := CompletePlatformArtifactCleanup(intent.ID, first.Token, time.Minute, 24*time.Hour, 2)
	require.NoError(t, err)
	assert.False(t, won)
	won, _, err = ReleasePlatformArtifactCleanup(
		intent.ID,
		first.Token,
		3,
		time.Second,
		"stale_worker",
		false,
	)
	require.NoError(t, err)
	assert.False(t, won)
	won, err = CompletePlatformArtifactCleanup(intent.ID, second.Token, time.Minute, 24*time.Hour, 2)
	require.NoError(t, err)
	assert.True(t, won)
	var quarantined PlatformArtifactUploadIntent
	require.NoError(t, DB.First(&quarantined, "id = ?", intent.ID).Error)
	assert.Equal(t, PlatformArtifactUploadIntentQuarantined, quarantined.State)
	assert.Equal(t, 1, quarantined.DeletePasses)
	require.NoError(t, DB.Model(&quarantined).Update("available_at", time.Now().UTC().Add(-time.Second)).Error)
	third, err := ClaimPlatformArtifactCleanup(time.Minute)
	require.NoError(t, err)
	won, err = CompletePlatformArtifactCleanup(intent.ID, third.Token, time.Minute, 24*time.Hour, 2)
	require.NoError(t, err)
	assert.True(t, won)
	var cleaned PlatformArtifactUploadIntent
	require.NoError(t, DB.First(&cleaned, "id = ?", intent.ID).Error)
	assert.Equal(t, PlatformArtifactUploadIntentCleaned, cleaned.State)
	assert.Equal(t, 2, cleaned.DeletePasses)
	assert.NotNil(t, cleaned.CleanedAt)
}

func TestPlatformArtifactNormalPutEvidencePreservesLeaseBasedCleanupSchedule(t *testing.T) {
	job, transferToken, objectKey, intent := createPlatformArtifactUploadTestIntent(t)
	originalAvailableAt := intent.AvailableAt

	won, err := RecordPlatformArtifactUploadPut(job.ID, transferToken, objectKey, "normal-put-version")
	require.NoError(t, err)
	assert.True(t, won)
	var persisted PlatformArtifactUploadIntent
	require.NoError(t, DB.First(&persisted, "id = ?", intent.ID).Error)
	assert.Equal(t, PlatformArtifactUploadIntentPending, persisted.State)
	assert.Zero(t, persisted.Attempts)
	assert.Zero(t, persisted.DeletePasses)
	assert.WithinDuration(t, originalAvailableAt, persisted.AvailableAt, time.Millisecond)
	assert.Empty(t, persisted.LastErrorCode)
	assert.Equal(t, "normal-put-version", persisted.StoreVersionID)
	assert.NotNil(t, persisted.PutCompletedAt)
	_, err = ClaimPlatformArtifactCleanup(time.Minute)
	assert.ErrorIs(t, err, gorm.ErrRecordNotFound)
}

func TestPlatformArtifactLatePutRearmsCleanedTombstone(t *testing.T) {
	job, transferToken, objectKey, intent := createPlatformArtifactUploadTestIntent(t)
	require.NoError(t, DB.Model(&PlatformGenerationJob{}).Where("id = ?", job.ID).
		Update("transfer_lease_expires_at", time.Now().UTC().Add(-time.Second)).Error)
	_, err := SchedulePlatformArtifactUploadCleanup(job.ID, transferToken)
	require.NoError(t, err)

	for pass := 0; pass < 2; pass++ {
		claim, claimErr := ClaimPlatformArtifactCleanup(time.Minute)
		require.NoError(t, claimErr)
		won, completeErr := CompletePlatformArtifactCleanup(intent.ID, claim.Token, time.Minute, 24*time.Hour, 2)
		require.NoError(t, completeErr)
		require.True(t, won)
		if pass == 0 {
			require.NoError(t, DB.Model(&PlatformArtifactUploadIntent{}).Where("id = ?", intent.ID).
				Update("available_at", time.Now().UTC().Add(-time.Second)).Error)
		}
	}

	won, err := RecordPlatformArtifactUploadPut(job.ID, transferToken, objectKey, "obs-version-late")
	require.NoError(t, err)
	assert.True(t, won)
	var rearmed PlatformArtifactUploadIntent
	require.NoError(t, DB.First(&rearmed, "id = ?", intent.ID).Error)
	assert.Equal(t, PlatformArtifactUploadIntentPending, rearmed.State)
	assert.Equal(t, 0, rearmed.DeletePasses)
	assert.Equal(t, "obs-version-late", rearmed.StoreVersionID)
	assert.Equal(t, "late_put_after_cleanup", rearmed.LastErrorCode)
	assert.NotNil(t, rearmed.PutCompletedAt)
}

func TestPlatformArtifactLatePutRearmsDeadLetterWithFreshRetryBudget(t *testing.T) {
	job, transferToken, objectKey, intent := createPlatformArtifactUploadTestIntent(t)
	now := time.Now().UTC()
	require.NoError(t, DB.Model(&PlatformGenerationJob{}).Where("id = ?", job.ID).
		Update("transfer_lease_expires_at", now.Add(-time.Second)).Error)
	require.NoError(t, DB.Model(&PlatformArtifactUploadIntent{}).Where("id = ?", intent.ID).
		Updates(map[string]any{
			"state":            PlatformArtifactUploadIntentDeadLetter,
			"attempts":         8,
			"available_at":     now.Add(-time.Hour),
			"claim_token":      uuid.NewString(),
			"claim_expires_at": now.Add(-time.Minute),
			"last_error_code":  "artifact_delete_failed",
		}).Error)

	won, err := RecordPlatformArtifactUploadPut(job.ID, transferToken, objectKey, "obs-version-dead-letter")
	require.NoError(t, err)
	assert.True(t, won)
	var rearmed PlatformArtifactUploadIntent
	require.NoError(t, DB.First(&rearmed, "id = ?", intent.ID).Error)
	assert.Equal(t, PlatformArtifactUploadIntentPending, rearmed.State)
	assert.Zero(t, rearmed.Attempts)
	assert.Zero(t, rearmed.DeletePasses)
	assert.Empty(t, rearmed.ClaimToken)
	assert.True(t, rearmed.ClaimExpiresAt.IsZero())
	assert.Nil(t, rearmed.QuarantinedAt)
	assert.Nil(t, rearmed.CleanedAt)
	assert.Equal(t, "late_put_after_cleanup", rearmed.LastErrorCode)
	assert.Equal(t, "obs-version-dead-letter", rearmed.StoreVersionID)

	claim, err := ClaimPlatformArtifactCleanup(time.Minute)
	require.NoError(t, err)
	assert.Equal(t, intent.ID, claim.Intent.ID)
	assert.Equal(t, 1, claim.Intent.Attempts)
}

func TestPlatformGenerationCompletePublishesIntentAtomically(t *testing.T) {
	job, transferToken, objectKey, intent := createPlatformArtifactUploadTestIntent(t)
	outputsJSON := `[{"asset_id":"asset-one","object_key":"` + objectKey + `"}]`

	won, err := CompletePlatformGenerationTransfer(
		job.ID,
		transferToken,
		objectKey,
		outputsJSON,
	)
	require.NoError(t, err)
	assert.True(t, won)

	var persistedJob PlatformGenerationJob
	require.NoError(t, DB.First(&persistedJob, "id = ?", job.ID).Error)
	assert.Equal(t, PlatformGenerationStatusSucceeded, persistedJob.Status)
	assert.Equal(t, outputsJSON, persistedJob.OutputsJSON)
	var persistedIntent PlatformArtifactUploadIntent
	require.NoError(t, DB.First(&persistedIntent, "id = ?", intent.ID).Error)
	assert.Equal(t, PlatformArtifactUploadIntentPublished, persistedIntent.State)
	assert.NotNil(t, persistedIntent.PublishedAt)
	won, err = RecordPlatformArtifactUploadPut(job.ID, transferToken, objectKey, "published-version")
	require.NoError(t, err)
	assert.True(t, won)
	require.NoError(t, DB.First(&persistedIntent, "id = ?", intent.ID).Error)
	assert.Equal(t, PlatformArtifactUploadIntentPublished, persistedIntent.State)

	// Treat the first successful return as a lost commit acknowledgement. The
	// same operation is idempotent and cleanup can never claim this object.
	won, err = CompletePlatformGenerationTransfer(
		job.ID,
		transferToken,
		objectKey,
		outputsJSON,
	)
	require.NoError(t, err)
	assert.True(t, won)
	_, err = ClaimPlatformArtifactCleanup(time.Minute)
	assert.ErrorIs(t, err, gorm.ErrRecordNotFound)
}

func TestPlatformGenerationCompleteClearsOnlyFencedPlatformNativeResultURL(t *testing.T) {
	job, transferToken, objectKey, _ := createPlatformArtifactUploadTestIntent(t)
	route := createPlatformGenerationRouteFixture(t, "native-result-clear", 10, 2)
	nativeTaskID, err := PlatformGenerationNativeTaskID(job.ID)
	require.NoError(t, err)
	channelID := route.ChannelID
	providerURL := "https://provider.example/result.png?X-Signature=secret"
	temporaryResultJSON := `{"result_url":"` + providerURL + `"}`
	canonicalOutputsJSON := `[{"asset_id":"canonical","object_key":"` + objectKey + `"}]`
	providerData := []byte(`{"content":{"video_url":"` + providerURL + `"},"usage":{"total_tokens":42}}`)
	keyIndex := route.KeyIndex
	require.NoError(t, DB.Model(&PlatformGenerationJob{}).Where("id = ?", job.ID).Updates(map[string]any{
		"native_task_id":        nativeTaskID,
		"provider_route_id":     route.ID,
		"provider_channel_id":   channelID,
		"model":                 route.Model,
		"mode":                  route.Mode,
		"upstream_task_id":      "provider-task-1",
		"upstream_result_url":   providerURL,
		"temporary_result_json": temporaryResultJSON,
	}).Error)
	credentialVersion := createPlatformGenerationProviderCredentialFixture(t, DB, job.TenantID, *route)
	nativeTask := Task{
		CreatedAt:  time.Now().UTC().Unix(),
		UpdatedAt:  time.Now().UTC().Unix(),
		TaskID:     nativeTaskID,
		ChannelId:  channelID,
		Status:     TaskStatusSuccess,
		Progress:   "100%",
		FailReason: providerURL,
		Data:       providerData,
		PrivateData: TaskPrivateData{
			BillingSource:              TaskBillingSourcePlatformExternal,
			UpstreamTaskID:             "provider-task-1",
			ResultURL:                  providerURL,
			PinnedKeyIndex:             &keyIndex,
			PinnedKeyFingerprint:       route.KeyFingerprint,
			ProviderCredentialTenantID: job.TenantID,
			ProviderCredentialVersion:  credentialVersion,
		},
	}
	require.NoError(t, DB.Create(&nativeTask).Error)

	won, err := CompletePlatformGenerationTransfer(
		job.ID,
		uuid.NewString(),
		objectKey,
		`[{"asset_id":"stale","object_key":"`+objectKey+`"}]`,
	)
	require.NoError(t, err)
	assert.False(t, won)
	require.NoError(t, DB.First(&nativeTask, nativeTask.ID).Error)
	assert.Equal(t, providerURL, nativeTask.PrivateData.ResultURL)
	var persistedJob PlatformGenerationJob
	require.NoError(t, DB.First(&persistedJob, "id = ?", job.ID).Error)
	assert.Equal(t, providerURL, persistedJob.UpstreamResultURL)
	assert.Equal(t, temporaryResultJSON, persistedJob.TemporaryResultJSON)

	won, err = CompletePlatformGenerationTransfer(
		job.ID,
		transferToken,
		objectKey,
		canonicalOutputsJSON,
	)
	require.NoError(t, err)
	assert.True(t, won)
	require.NoError(t, DB.First(&nativeTask, nativeTask.ID).Error)
	assert.Empty(t, nativeTask.PrivateData.ResultURL)
	assert.True(t, nativeTask.PrivateData.ProviderResultURLScrubbed)
	assert.Empty(t, nativeTask.FailReason)
	assert.Empty(t, nativeTask.GetResultURL())
	assert.Empty(t, nativeTask.Data)
	assert.Equal(t, "provider-task-1", nativeTask.PrivateData.UpstreamTaskID)
	assert.Equal(t, TaskBillingSourcePlatformExternal, nativeTask.PrivateData.BillingSource)
	encoded, err := nativeTask.PrivateData.Value()
	require.NoError(t, err)
	assert.NotContains(t, encoded, providerURL)
	assert.NotContains(t, encoded, "X-Signature")
	require.NoError(t, DB.First(&persistedJob, "id = ?", job.ID).Error)
	assert.Empty(t, persistedJob.UpstreamResultURL)
	assert.Empty(t, persistedJob.TemporaryResultJSON)
	assert.Equal(t, canonicalOutputsJSON, persistedJob.OutputsJSON)

	// Simulate a historical candidate that committed publication but retained
	// both task- and job-level temporary provider results. Replaying the exact
	// published intent must scrub them without changing canonical outputs.
	nativeTask.PrivateData.ResultURL = providerURL
	require.NoError(t, DB.Model(&Task{}).Where("id = ?", nativeTask.ID).Updates(map[string]any{
		"private_data": nativeTask.PrivateData,
		"fail_reason":  providerURL,
		"data":         providerData,
	}).Error)
	require.NoError(t, DB.Model(&PlatformGenerationJob{}).Where("id = ?", job.ID).Updates(map[string]any{
		"upstream_result_url":   providerURL,
		"temporary_result_json": temporaryResultJSON,
	}).Error)
	won, err = CompletePlatformGenerationTransfer(
		job.ID,
		transferToken,
		objectKey,
		canonicalOutputsJSON,
	)
	require.NoError(t, err)
	assert.True(t, won)
	require.NoError(t, DB.First(&nativeTask, nativeTask.ID).Error)
	assert.Empty(t, nativeTask.PrivateData.ResultURL)
	assert.True(t, nativeTask.PrivateData.ProviderResultURLScrubbed)
	assert.Empty(t, nativeTask.FailReason)
	assert.Empty(t, nativeTask.GetResultURL())
	assert.Empty(t, nativeTask.Data)
	require.NoError(t, DB.First(&persistedJob, "id = ?", job.ID).Error)
	assert.Empty(t, persistedJob.UpstreamResultURL)
	assert.Empty(t, persistedJob.TemporaryResultJSON)
	assert.Equal(t, canonicalOutputsJSON, persistedJob.OutputsJSON)

	// A historical published row can also be replayed while its native-task
	// binding is damaged. Never acknowledge that replay as clean: preserve the
	// canonical artifact, retain all provider material, and add a durable,
	// read-only operations marker that blocks cutover until the row is repaired.
	nativeTask.PrivateData.ResultURL = providerURL
	nativeTask.PrivateData.ProviderResultURLScrubbed = false
	require.NoError(t, DB.Model(&Task{}).Where("id = ?", nativeTask.ID).Updates(map[string]any{
		"private_data": nativeTask.PrivateData,
		"fail_reason":  providerURL,
		"data":         providerData,
	}).Error)
	require.NoError(t, DB.Model(&PlatformGenerationJob{}).Where("id = ?", job.ID).Updates(map[string]any{
		"upstream_result_url":   providerURL,
		"temporary_result_json": temporaryResultJSON,
	}).Error)
	collidingTask := Task{
		CreatedAt: time.Now().UTC().Unix(), UpdatedAt: time.Now().UTC().Unix(),
		TaskID: nativeTaskID, ChannelId: channelID + 1, Status: TaskStatusSuccess, Progress: "100%",
		PrivateData: TaskPrivateData{ResultURL: "https://other.example/result.png?token=must-remain"},
	}
	require.NoError(t, DB.Create(&collidingTask).Error)

	won, err = CompletePlatformGenerationTransfer(job.ID, transferToken, objectKey, canonicalOutputsJSON)
	require.ErrorIs(t, err, ErrPlatformGenerationProviderMaterialReconciliationRequired)
	assert.False(t, won)
	require.NoError(t, DB.First(&persistedJob, "id = ?", job.ID).Error)
	assert.Equal(t, PlatformGenerationStatusSucceeded, persistedJob.Status)
	assert.Equal(t, canonicalOutputsJSON, persistedJob.OutputsJSON)
	assert.Equal(t, PlatformGenerationProviderReconciliationKindProviderMaterial,
		PlatformGenerationProviderReconciliationKind(persistedJob))
	assert.Equal(t, providerURL, persistedJob.UpstreamResultURL)
	assert.Equal(t, temporaryResultJSON, persistedJob.TemporaryResultJSON)
	require.NoError(t, DB.First(&nativeTask, nativeTask.ID).Error)
	assert.Equal(t, providerURL, nativeTask.PrivateData.ResultURL)
	assert.Equal(t, string(providerData), string(nativeTask.Data))
	backlog, err := CountPlatformGenerationProviderResultReconciliationBacklog()
	require.NoError(t, err)
	assert.Equal(t, int64(1), backlog)
	listed, total, err := ListPlatformGenerationProviderResultReconciliations(job.TenantID, 1, 10)
	require.NoError(t, err)
	require.Equal(t, int64(1), total)
	require.Len(t, listed, 1)
	assert.Equal(t, job.ID, listed[0].ID)

	// Once the duplicate row is removed, an exact idempotent replay performs
	// the strict scrub and clears the terminal marker without changing outputs.
	require.NoError(t, DB.Delete(&collidingTask).Error)
	won, err = CompletePlatformGenerationTransfer(job.ID, transferToken, objectKey, canonicalOutputsJSON)
	require.NoError(t, err)
	assert.True(t, won)
	require.NoError(t, DB.First(&persistedJob, "id = ?", job.ID).Error)
	assert.Equal(t, PlatformGenerationStatusSucceeded, persistedJob.Status)
	assert.Equal(t, canonicalOutputsJSON, persistedJob.OutputsJSON)
	assert.Equal(t, PlatformGenerationProviderReconciliationKindUnknown,
		PlatformGenerationProviderReconciliationKind(persistedJob))
	assert.Empty(t, persistedJob.UpstreamResultURL)
	assert.Empty(t, persistedJob.TemporaryResultJSON)
	require.NoError(t, DB.First(&nativeTask, nativeTask.ID).Error)
	assert.Empty(t, nativeTask.PrivateData.ResultURL)
	assert.Empty(t, nativeTask.FailReason)
	assert.Empty(t, nativeTask.Data)
	backlog, err = CountPlatformGenerationProviderResultReconciliationBacklog()
	require.NoError(t, err)
	assert.Zero(t, backlog)
}

func TestPlatformGenerationNativeResultURLScrubHandlesLegacyFallbackSafely(t *testing.T) {
	providerURL := "https://provider.example/result.png?X-Signature=legacy-secret"
	differentProviderURL := "https://provider.example/different.png?X-Signature=must-remain"
	providerData := []byte(`{"content":{"video_url":"` + providerURL + `"},"usage":{"total_tokens":42}}`)
	errorText := "provider render failed before artifact creation"
	tests := []struct {
		name                 string
		billingSource        string
		resultURL            string
		failReason           string
		wantDataPreserved    bool
		expectedScrubMarker  bool
		expectedResultURL    string
		expectedFailReason   string
		expectedGetResultURL string
		forgeCredential      bool
		expectReconciliation bool
		mutateTask           func(*Task)
	}{
		{
			name: "failreason only URL", billingSource: TaskBillingSourcePlatformExternal,
			failReason: providerURL, expectedScrubMarker: true,
		},
		{
			name: "private URL with different error text", billingSource: TaskBillingSourcePlatformExternal,
			resultURL: providerURL, failReason: errorText,
			expectedScrubMarker: true, expectedFailReason: errorText, expectedGetResultURL: errorText,
		},
		{
			name: "private URL with different URL evidence", billingSource: TaskBillingSourcePlatformExternal,
			resultURL: providerURL, failReason: differentProviderURL,
			expectedScrubMarker: true, expectedFailReason: differentProviderURL, expectedGetResultURL: differentProviderURL,
		},
		{
			name: "ordinary error text only", billingSource: TaskBillingSourcePlatformExternal,
			failReason: errorText, expectedFailReason: errorText, expectedGetResultURL: errorText,
		},
		{
			name: "ordinary task is not scrubbed", billingSource: "wallet",
			resultURL: providerURL, failReason: providerURL, wantDataPreserved: true,
			expectedResultURL: providerURL, expectedFailReason: providerURL, expectedGetResultURL: providerURL,
			expectReconciliation: true,
		},
		{
			name: "nonzero quota binding mismatch", billingSource: TaskBillingSourcePlatformExternal,
			resultURL: providerURL, failReason: providerURL, wantDataPreserved: true,
			expectedResultURL: providerURL, expectedFailReason: providerURL, expectedGetResultURL: providerURL,
			expectReconciliation: true,
			mutateTask:           func(task *Task) { task.Quota = 1 },
		},
		{
			name: "active native task on terminal job", billingSource: TaskBillingSourcePlatformExternal,
			resultURL: providerURL, failReason: providerURL, wantDataPreserved: true,
			expectedResultURL: providerURL, expectedFailReason: providerURL, expectedGetResultURL: providerURL,
			expectReconciliation: true,
			mutateTask: func(task *Task) {
				task.Status = TaskStatusInProgress
				task.Progress = "50%"
			},
		},
		{
			name: "subscription binding mismatch", billingSource: TaskBillingSourcePlatformExternal,
			resultURL: providerURL, failReason: providerURL, wantDataPreserved: true,
			expectedResultURL: providerURL, expectedFailReason: providerURL, expectedGetResultURL: providerURL,
			expectReconciliation: true,
			mutateTask:           func(task *Task) { task.PrivateData.SubscriptionId = 1 },
		},
		{
			name: "billing context binding mismatch", billingSource: TaskBillingSourcePlatformExternal,
			resultURL: providerURL, failReason: providerURL, wantDataPreserved: true,
			expectedResultURL: providerURL, expectedFailReason: providerURL, expectedGetResultURL: providerURL,
			expectReconciliation: true,
			mutateTask:           func(task *Task) { task.PrivateData.BillingContext = &TaskBillingContext{} },
		},
		{
			name: "pinned key binding mismatch", billingSource: TaskBillingSourcePlatformExternal,
			resultURL: providerURL, failReason: providerURL, wantDataPreserved: true,
			expectedResultURL: providerURL, expectedFailReason: providerURL, expectedGetResultURL: providerURL,
			expectReconciliation: true,
			mutateTask:           func(task *Task) { wrong := 999; task.PrivateData.PinnedKeyIndex = &wrong },
		},
		{
			name: "key fingerprint binding mismatch", billingSource: TaskBillingSourcePlatformExternal,
			resultURL: providerURL, failReason: providerURL, wantDataPreserved: true,
			expectedResultURL: providerURL, expectedFailReason: providerURL, expectedGetResultURL: providerURL,
			expectReconciliation: true,
			mutateTask:           func(task *Task) { task.PrivateData.PinnedKeyFingerprint = strings.Repeat("f", 64) },
		},
		{
			name: "credential tenant binding mismatch", billingSource: TaskBillingSourcePlatformExternal,
			resultURL: providerURL, failReason: providerURL, wantDataPreserved: true,
			expectedResultURL: providerURL, expectedFailReason: providerURL, expectedGetResultURL: providerURL,
			expectReconciliation: true,
			mutateTask:           func(task *Task) { task.PrivateData.ProviderCredentialTenantID = uuid.NewString() },
		},
		{
			name: "credential version binding mismatch", billingSource: TaskBillingSourcePlatformExternal,
			resultURL: providerURL, failReason: providerURL, wantDataPreserved: true,
			expectedResultURL: providerURL, expectedFailReason: providerURL, expectedGetResultURL: providerURL,
			expectReconciliation: true,
			mutateTask:           func(task *Task) { task.PrivateData.ProviderCredentialVersion = "" },
		},
		{
			name: "forged credential version row", billingSource: TaskBillingSourcePlatformExternal,
			resultURL: providerURL, failReason: providerURL, wantDataPreserved: true,
			expectedResultURL: providerURL, expectedFailReason: providerURL, expectedGetResultURL: providerURL,
			forgeCredential: true, expectReconciliation: true,
		},
		{
			name: "upstream task binding mismatch", billingSource: TaskBillingSourcePlatformExternal,
			resultURL: providerURL, failReason: providerURL, wantDataPreserved: true,
			expectedResultURL: providerURL, expectedFailReason: providerURL, expectedGetResultURL: providerURL,
			expectReconciliation: true,
			mutateTask:           func(task *Task) { task.PrivateData.UpstreamTaskID = "different-provider-task" },
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			job, transferToken, objectKey, _ := createPlatformArtifactUploadTestIntent(t)
			route := createPlatformGenerationRouteFixture(t, "native-result-legacy-"+strings.ReplaceAll(test.name, " ", "-"), 10, 2)
			nativeTaskID, err := PlatformGenerationNativeTaskID(job.ID)
			require.NoError(t, err)
			temporaryResultJSON := `{"result_url":"` + providerURL + `"}`
			canonicalOutputsJSON := `[{"asset_id":"canonical","object_key":"` + objectKey + `"}]`
			require.NoError(t, DB.Model(&PlatformGenerationJob{}).Where("id = ?", job.ID).Updates(map[string]any{
				"native_task_id":        nativeTaskID,
				"provider_route_id":     route.ID,
				"provider_channel_id":   route.ChannelID,
				"model":                 route.Model,
				"mode":                  route.Mode,
				"upstream_task_id":      "provider-task-legacy",
				"upstream_result_url":   providerURL,
				"temporary_result_json": temporaryResultJSON,
			}).Error)
			credentialVersion := createPlatformGenerationProviderCredentialFixture(t, DB, job.TenantID, *route)
			nativeTask := Task{
				CreatedAt: time.Now().UTC().Unix(), UpdatedAt: time.Now().UTC().Unix(),
				TaskID: nativeTaskID, ChannelId: route.ChannelID, Status: TaskStatusSuccess, Progress: "100%",
				FailReason: test.failReason,
				Data:       providerData,
				PrivateData: TaskPrivateData{
					BillingSource: test.billingSource, UpstreamTaskID: "provider-task-legacy",
					ResultURL: test.resultURL, PinnedKeyIndex: &route.KeyIndex,
					PinnedKeyFingerprint: route.KeyFingerprint, ProviderCredentialTenantID: job.TenantID,
					ProviderCredentialVersion: credentialVersion,
				},
			}
			if test.mutateTask != nil {
				test.mutateTask(&nativeTask)
			}
			if test.forgeCredential {
				nativeTask.PrivateData.ProviderCredentialVersion = createPlatformGenerationProviderCredentialFixture(
					t,
					DB,
					uuid.NewString(),
					*route,
				)
			}
			require.NoError(t, DB.Create(&nativeTask).Error)

			won, err := CompletePlatformGenerationTransfer(
				job.ID,
				transferToken,
				objectKey,
				canonicalOutputsJSON,
			)
			if test.expectReconciliation {
				require.ErrorIs(t, err, ErrPlatformGenerationProviderMaterialReconciliationRequired)
				assert.False(t, won)
				require.NoError(t, DB.First(&nativeTask, nativeTask.ID).Error)
				assert.Equal(t, test.expectedResultURL, nativeTask.PrivateData.ResultURL)
				assert.Equal(t, test.expectedFailReason, nativeTask.FailReason)
				assert.Equal(t, string(providerData), string(nativeTask.Data))
				var transferringJob PlatformGenerationJob
				require.NoError(t, DB.First(&transferringJob, "id = ?", job.ID).Error)
				assert.Equal(t, PlatformGenerationStatusTransferring, transferringJob.Status)
				assert.Equal(t, providerURL, transferringJob.UpstreamResultURL)
				assert.Equal(t, temporaryResultJSON, transferringJob.TemporaryResultJSON)
				won, err = CompletePlatformGenerationTransferProviderMaterialConflict(job.ID, transferToken)
				require.NoError(t, err)
				assert.True(t, won)
				require.NoError(t, DB.First(&transferringJob, "id = ?", job.ID).Error)
				assert.Equal(t, PlatformGenerationStatusReconciliationRequired, transferringJob.Status)
				assert.Equal(t, PlatformGenerationErrorProviderPollReconciliationRequired, transferringJob.ErrorCode)
				assert.Equal(t, providerURL, transferringJob.UpstreamResultURL)
				assert.Equal(t, temporaryResultJSON, transferringJob.TemporaryResultJSON)
				return
			}
			require.NoError(t, err)
			assert.True(t, won)
			won, err = CompletePlatformGenerationTransfer(
				job.ID,
				transferToken,
				objectKey,
				canonicalOutputsJSON,
			)
			require.NoError(t, err)
			assert.True(t, won, "published replay must keep the scrub restart-safe")
			require.NoError(t, DB.First(&nativeTask, nativeTask.ID).Error)
			assert.Equal(t, test.expectedResultURL, nativeTask.PrivateData.ResultURL)
			assert.Equal(t, test.expectedScrubMarker, nativeTask.PrivateData.ProviderResultURLScrubbed)
			assert.Equal(t, test.expectedFailReason, nativeTask.FailReason)
			assert.Equal(t, test.expectedGetResultURL, nativeTask.GetResultURL())
			if !test.wantDataPreserved {
				assert.NotContains(t, nativeTask.GetResultURL(), "X-Signature=legacy-secret",
					"strict Platform tasks must not expose the provider credential")
				assert.Empty(t, nativeTask.Data)
			} else {
				assert.Equal(t, string(providerData), string(nativeTask.Data))
			}
			var persistedJob PlatformGenerationJob
			require.NoError(t, DB.First(&persistedJob, "id = ?", job.ID).Error)
			assert.Equal(t, PlatformGenerationStatusSucceeded, persistedJob.Status)
			assert.Empty(t, persistedJob.UpstreamResultURL)
			assert.Empty(t, persistedJob.TemporaryResultJSON)
			assert.Equal(t, canonicalOutputsJSON, persistedJob.OutputsJSON)
		})
	}
}

func TestPlatformGenerationTerminalTransferFailureClearsPlatformNativeResultURL(t *testing.T) {
	job, transferToken, _, _ := createPlatformArtifactUploadTestIntent(t)
	route := createPlatformGenerationRouteFixture(t, "native-result-terminal-failure", 10, 2)
	nativeTaskID, err := PlatformGenerationNativeTaskID(job.ID)
	require.NoError(t, err)
	channelID := route.ChannelID
	keyIndex := route.KeyIndex
	providerURL := "https://provider.example/result.png?token=secret"
	temporaryResultJSON := `{"result_url":"` + providerURL + `"}`
	require.NoError(t, DB.Model(&PlatformGenerationJob{}).Where("id = ?", job.ID).Updates(map[string]any{
		"native_task_id":        nativeTaskID,
		"provider_route_id":     route.ID,
		"provider_channel_id":   channelID,
		"model":                 route.Model,
		"mode":                  route.Mode,
		"upstream_task_id":      "provider-task-2",
		"upstream_result_url":   providerURL,
		"temporary_result_json": temporaryResultJSON,
	}).Error)
	credentialVersion := createPlatformGenerationProviderCredentialFixture(t, DB, job.TenantID, *route)
	nativeTask := Task{
		CreatedAt: time.Now().UTC().Unix(), UpdatedAt: time.Now().UTC().Unix(),
		TaskID: nativeTaskID, ChannelId: channelID, Status: TaskStatusSuccess, Progress: "100%",
		Data: []byte(`{"content":{"video_url":"` + providerURL + `"}}`),
		PrivateData: TaskPrivateData{
			BillingSource:              TaskBillingSourcePlatformExternal,
			UpstreamTaskID:             "provider-task-2",
			ResultURL:                  providerURL,
			PinnedKeyIndex:             &keyIndex,
			PinnedKeyFingerprint:       route.KeyFingerprint,
			ProviderCredentialTenantID: job.TenantID,
			ProviderCredentialVersion:  credentialVersion,
		},
	}
	require.NoError(t, DB.Create(&nativeTask).Error)

	won, err := FailPlatformGenerationTransfer(job.ID, transferToken, "artifact validation failed")
	require.NoError(t, err)
	assert.True(t, won)
	require.NoError(t, DB.First(&nativeTask, nativeTask.ID).Error)
	assert.Empty(t, nativeTask.PrivateData.ResultURL)
	assert.True(t, nativeTask.PrivateData.ProviderResultURLScrubbed)
	assert.Empty(t, nativeTask.Data)
	assert.Equal(t, "provider-task-2", nativeTask.PrivateData.UpstreamTaskID)
	var persistedJob PlatformGenerationJob
	require.NoError(t, DB.First(&persistedJob, "id = ?", job.ID).Error)
	assert.Empty(t, persistedJob.UpstreamResultURL)
	assert.Empty(t, persistedJob.TemporaryResultJSON)
	assert.Equal(t, `[]`, persistedJob.OutputsJSON)
}

func TestPlatformGenerationCompletePreservesAmbiguousNativeTaskAndRequiresReconciliation(t *testing.T) {
	job, transferToken, objectKey, _ := createPlatformArtifactUploadTestIntent(t)
	route := createPlatformGenerationRouteFixture(t, "native-result-collision", 10, 2)
	nativeTaskID, err := PlatformGenerationNativeTaskID(job.ID)
	require.NoError(t, err)
	require.NoError(t, DB.Model(&PlatformGenerationJob{}).Where("id = ?", job.ID).Updates(map[string]any{
		"native_task_id":      nativeTaskID,
		"provider_route_id":   route.ID,
		"provider_channel_id": route.ChannelID,
		"model":               route.Model,
		"mode":                route.Mode,
	}).Error)
	providerURL := "https://provider.example/result.png?token=must-remain-on-conflict"
	temporaryResultJSON := `{"result_url":"` + providerURL + `"}`
	require.NoError(t, DB.Model(&PlatformGenerationJob{}).Where("id = ?", job.ID).Updates(map[string]any{
		"upstream_result_url":   providerURL,
		"temporary_result_json": temporaryResultJSON,
	}).Error)
	keyIndex := route.KeyIndex
	platformTask := Task{
		CreatedAt: time.Now().UTC().Unix(), UpdatedAt: time.Now().UTC().Unix(),
		TaskID: nativeTaskID, ChannelId: route.ChannelID, Status: TaskStatusSuccess, Progress: "100%",
		PrivateData: TaskPrivateData{
			BillingSource: TaskBillingSourcePlatformExternal, UpstreamTaskID: "provider-task-collision", ResultURL: providerURL,
			PinnedKeyIndex: &keyIndex, PinnedKeyFingerprint: route.KeyFingerprint,
			ProviderCredentialTenantID: job.TenantID, ProviderCredentialVersion: uuid.NewString(),
		},
	}
	require.NoError(t, DB.Create(&platformTask).Error)
	collidingTask := Task{
		CreatedAt: time.Now().UTC().Unix(), UpdatedAt: time.Now().UTC().Unix(),
		TaskID: nativeTaskID, ChannelId: route.ChannelID + 1, Status: TaskStatusSuccess, Progress: "100%",
		PrivateData: TaskPrivateData{ResultURL: "https://other.example/not-platform.png?token=other"},
	}
	require.NoError(t, DB.Create(&collidingTask).Error)

	canonicalOutputsJSON := `[{"asset_id":"canonical","object_key":"` + objectKey + `"}]`
	won, err := CompletePlatformGenerationTransfer(
		job.ID,
		transferToken,
		objectKey,
		canonicalOutputsJSON,
	)
	require.ErrorIs(t, err, ErrPlatformGenerationProviderMaterialReconciliationRequired)
	assert.False(t, won)
	require.NoError(t, DB.First(&platformTask, platformTask.ID).Error)
	assert.Equal(t, providerURL, platformTask.PrivateData.ResultURL)
	var persistedJob PlatformGenerationJob
	require.NoError(t, DB.First(&persistedJob, "id = ?", job.ID).Error)
	assert.Equal(t, PlatformGenerationStatusTransferring, persistedJob.Status)
	assert.Equal(t, providerURL, persistedJob.UpstreamResultURL)
	assert.Equal(t, temporaryResultJSON, persistedJob.TemporaryResultJSON)
	assert.NotEqual(t, canonicalOutputsJSON, persistedJob.OutputsJSON)

	won, err = CompletePlatformGenerationTransferProviderMaterialConflict(job.ID, transferToken)
	require.NoError(t, err)
	assert.True(t, won)
	require.NoError(t, DB.First(&persistedJob, "id = ?", job.ID).Error)
	assert.Equal(t, PlatformGenerationStatusReconciliationRequired, persistedJob.Status)
	assert.Equal(t, PlatformGenerationErrorProviderPollReconciliationRequired, persistedJob.ErrorCode)
	assert.Equal(t, providerURL, persistedJob.UpstreamResultURL)
	assert.Equal(t, temporaryResultJSON, persistedJob.TemporaryResultJSON)
}

func TestPlatformGenerationCompleteRollsBackJobAndIntentTogether(t *testing.T) {
	job, transferToken, objectKey, intent := createPlatformArtifactUploadTestIntent(t)
	callbackFailure := errors.New("callback enqueue failed")

	won, err := CompletePlatformGenerationTransfer(
		job.ID,
		transferToken,
		objectKey,
		`[{"asset_id":"asset-one","object_key":"`+objectKey+`"}]`,
		func(PlatformGenerationJob) (*PlatformGenerationCallbackDelivery, bool, error) {
			return nil, false, callbackFailure
		},
	)
	assert.ErrorIs(t, err, callbackFailure)
	assert.False(t, won)

	var persistedJob PlatformGenerationJob
	require.NoError(t, DB.First(&persistedJob, "id = ?", job.ID).Error)
	assert.Equal(t, PlatformGenerationStatusTransferring, persistedJob.Status)
	var persistedIntent PlatformArtifactUploadIntent
	require.NoError(t, DB.First(&persistedIntent, "id = ?", intent.ID).Error)
	assert.Equal(t, PlatformArtifactUploadIntentPending, persistedIntent.State)
	assert.Nil(t, persistedIntent.PublishedAt)
}

func TestMigratePlatformArtifactUploadIntentStorageBackfillsOnlyColumnAddition(t *testing.T) {
	dsn := "file:artifact-upload-migration-" + uuid.NewString() + "?mode=memory&cache=shared"
	db, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{})
	require.NoError(t, err)
	sqlDB, err := db.DB()
	require.NoError(t, err)
	sqlDB.SetMaxOpenConns(1)
	previousDB := DB
	DB = db
	t.Cleanup(func() {
		DB = previousDB
		require.NoError(t, sqlDB.Close())
	})

	require.NoError(t, db.AutoMigrate(&legacyPlatformArtifactUploadIntentWithoutDeletePasses{}))
	assert.False(t, db.Migrator().HasColumn(
		&legacyPlatformArtifactUploadIntentWithoutDeletePasses{},
		"delete_passes",
	))
	now := time.Now().UTC()
	cleanedAt := now.Add(-time.Hour)
	legacyRows := []legacyPlatformArtifactUploadIntentWithoutDeletePasses{
		{
			ID:             uuid.NewString(),
			JobID:          uuid.NewString(),
			TransferToken:  uuid.NewString(),
			ObjectKey:      "outputs/legacy/cleaned/object",
			StoreKind:      "huawei_obs",
			StoreBindingID: strings.Repeat("a", 64),
			State:          PlatformArtifactUploadIntentCleaned,
			AvailableAt:    now,
			CleanedAt:      &cleanedAt,
			CreatedAt:      now.Add(-2 * time.Hour),
			UpdatedAt:      cleanedAt,
		},
		{
			ID:             uuid.NewString(),
			JobID:          uuid.NewString(),
			TransferToken:  uuid.NewString(),
			ObjectKey:      "outputs/legacy/pending/object",
			StoreKind:      "huawei_obs",
			StoreBindingID: strings.Repeat("b", 64),
			State:          PlatformArtifactUploadIntentPending,
			AvailableAt:    now,
			CreatedAt:      now.Add(-time.Hour),
			UpdatedAt:      now.Add(-time.Hour),
		},
	}
	require.NoError(t, db.Create(&legacyRows).Error)

	require.NoError(t, MigratePlatformArtifactUploadIntentStorage())
	assert.True(t, db.Migrator().HasColumn(&PlatformArtifactUploadIntent{}, "delete_passes"))
	var cleaned PlatformArtifactUploadIntent
	require.NoError(t, db.First(&cleaned, "id = ?", legacyRows[0].ID).Error)
	assert.Equal(t, PlatformArtifactCleanupMinimumDeletePasses, cleaned.DeletePasses)
	var pending PlatformArtifactUploadIntent
	require.NoError(t, db.First(&pending, "id = ?", legacyRows[1].ID).Error)
	assert.Zero(t, pending.DeletePasses)

	required, err := PlatformArtifactCleanupMaintenanceRequired()
	require.NoError(t, err)
	assert.True(t, required)

	// Once the column exists, a zero value belongs to the current schema and
	// must not be guessed back to two on every restart.
	require.NoError(t, db.Model(&PlatformArtifactUploadIntent{}).
		Where("id = ?", cleaned.ID).
		Update("delete_passes", 0).Error)
	require.NoError(t, MigratePlatformArtifactUploadIntentStorage())
	require.NoError(t, db.First(&cleaned, "id = ?", cleaned.ID).Error)
	assert.Zero(t, cleaned.DeletePasses)
}
