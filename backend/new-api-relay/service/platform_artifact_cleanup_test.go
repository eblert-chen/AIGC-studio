package service

import (
	"context"
	"errors"
	"io"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

type cleanupRecordingArtifactStore struct {
	objects        map[string]bool
	deleteCalls    []string
	deleteVersions []string
	deleteFailures int
	bindingID      string
}

func (*cleanupRecordingArtifactStore) Kind() string { return "cleanup_test" }
func (store *cleanupRecordingArtifactStore) BindingID() string {
	if store.bindingID != "" {
		return store.bindingID
	}
	return strings.Repeat("d", 64)
}
func (*cleanupRecordingArtifactStore) Persistent() bool { return true }

func (store *cleanupRecordingArtifactStore) Put(
	ctx context.Context,
	input PlatformArtifactPutInput,
) (PlatformStoredArtifact, error) {
	if _, err := io.Copy(io.Discard, input.Content); err != nil {
		return PlatformStoredArtifact{}, err
	}
	if err := ctx.Err(); err != nil {
		return PlatformStoredArtifact{}, err
	}
	store.objects[input.ObjectKey] = true
	return PlatformStoredArtifact{
		ObjectKey:   input.ObjectKey,
		ContentType: input.ContentType,
		SizeBytes:   input.SizeBytes,
		SHA256:      input.SHA256,
	}, nil
}

func (store *cleanupRecordingArtifactStore) Delete(ctx context.Context, objectKey string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	store.deleteCalls = append(store.deleteCalls, objectKey)
	if store.deleteFailures > 0 {
		store.deleteFailures--
		return errors.New("temporary object delete failure")
	}
	delete(store.objects, objectKey)
	return nil
}

func (store *cleanupRecordingArtifactStore) DeleteVersion(
	ctx context.Context,
	objectKey string,
	versionID string,
) error {
	store.deleteVersions = append(store.deleteVersions, versionID)
	return store.Delete(ctx, objectKey)
}

func (*cleanupRecordingArtifactStore) IssueSignedDownload(
	context.Context,
	string,
	time.Duration,
) (PlatformIssuedArtifactDownload, error) {
	return PlatformIssuedArtifactDownload{}, nil
}

func (*cleanupRecordingArtifactStore) Healthcheck(context.Context) error { return nil }

func createPlatformArtifactCleanupServiceIntent(
	t *testing.T,
	store *cleanupRecordingArtifactStore,
) (model.PlatformGenerationJob, string, string, model.PlatformArtifactUploadIntent) {
	t.Helper()
	truncate(t)
	return appendPlatformArtifactCleanupServiceIntent(t, store)
}

func appendPlatformArtifactCleanupServiceIntent(
	t *testing.T,
	store *cleanupRecordingArtifactStore,
) (model.PlatformGenerationJob, string, string, model.PlatformArtifactUploadIntent) {
	t.Helper()
	now := time.Now().UTC()
	job := model.PlatformGenerationJob{
		ID:                         uuid.NewString(),
		TenantID:                   uuid.NewString(),
		SourceClientID:             "platform",
		RequestID:                  "cleanup-crash-window-request",
		IdempotencyKey:             uuid.NewString(),
		RequestHash:                strings.Repeat("a", 64),
		RequestJSON:                `{}`,
		Model:                      "cleanup-model",
		Mode:                       "text_to_video",
		ExpectedCapabilityRevision: "sha256:" + strings.Repeat("b", 64),
		CapabilityRevision:         "sha256:" + strings.Repeat("b", 64),
		Status:                     model.PlatformGenerationStatusTransferring,
		Progress:                   95,
		OutputsJSON:                `[]`,
		ErrorDetailsJSON:           `{}`,
		NextTransferAt:             now.Add(-time.Minute),
	}
	require.NoError(t, model.DB.Create(&job).Error)
	_, transferToken, err := model.ClaimPlatformGenerationTransfer(time.Minute)
	require.NoError(t, err)
	objectKey := "outputs/" + job.TenantID + "/" + job.ID + "/" + uuid.NewString()
	intent, err := model.CreatePlatformArtifactUploadIntent(
		job.ID,
		transferToken,
		objectKey,
		store.Kind(),
		store.BindingID(),
	)
	require.NoError(t, err)
	return job, transferToken, objectKey, *intent
}

func expirePlatformArtifactCleanupServiceIntent(
	t *testing.T,
	jobID string,
	intentID string,
) {
	t.Helper()
	past := time.Now().UTC().Add(-time.Second)
	require.NoError(t, model.DB.Model(&model.PlatformGenerationJob{}).Where("id = ?", jobID).
		Update("transfer_lease_expires_at", past).Error)
	require.NoError(t, model.DB.Model(&model.PlatformArtifactUploadIntent{}).Where("id = ?", intentID).
		Update("available_at", past).Error)
}

func TestArtifactCleanupLiveTransferHeadDoesNotBlockLaterOrphan(t *testing.T) {
	store := &cleanupRecordingArtifactStore{objects: map[string]bool{}}
	liveJob, _, liveObjectKey, liveIntent := createPlatformArtifactCleanupServiceIntent(t, store)
	orphanJob, _, orphanObjectKey, orphanIntent := appendPlatformArtifactCleanupServiceIntent(t, store)
	store.objects[liveObjectKey] = true
	store.objects[orphanObjectKey] = true

	// Force the still-live transfer to the head of the due index, modeling an
	// old scheduler timestamp while its keeper has renewed the DB lease.
	require.NoError(t, model.DB.Model(&model.PlatformArtifactUploadIntent{}).Where("id = ?", liveIntent.ID).
		Update("available_at", time.Now().UTC().Add(-2*time.Minute)).Error)
	expirePlatformArtifactCleanupServiceIntent(t, orphanJob.ID, orphanIntent.ID)
	require.NoError(t, model.DB.Model(&model.PlatformArtifactUploadIntent{}).Where("id = ?", orphanIntent.ID).
		Update("available_at", time.Now().UTC().Add(-time.Minute)).Error)

	processed, err := runPlatformArtifactCleanupOnce(context.Background(), store, 3)
	require.NoError(t, err)
	assert.True(t, processed)
	assert.Equal(t, []string{orphanObjectKey}, store.deleteCalls)
	assert.True(t, store.objects[liveObjectKey])
	assert.False(t, store.objects[orphanObjectKey])

	var persistedLiveJob model.PlatformGenerationJob
	require.NoError(t, model.DB.First(&persistedLiveJob, "id = ?", liveJob.ID).Error)
	var deferred model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.First(&deferred, "id = ?", liveIntent.ID).Error)
	assert.Equal(t, model.PlatformArtifactUploadIntentPending, deferred.State)
	assert.WithinDuration(t, persistedLiveJob.TransferLeaseExpiresAt, deferred.AvailableAt, time.Millisecond)
}

func TestArtifactCleanupDeletesPutThatSurvivedTransferProcessCrash(t *testing.T) {
	store := &cleanupRecordingArtifactStore{objects: map[string]bool{}}
	job, _, objectKey, intent := createPlatformArtifactCleanupServiceIntent(t, store)
	_, err := store.Put(context.Background(), PlatformArtifactPutInput{
		ObjectKey:   objectKey,
		Content:     strings.NewReader("uploaded artifact"),
		ContentType: "video/mp4",
		SizeBytes:   int64(len("uploaded artifact")),
		SHA256:      strings.Repeat("c", 64),
	})
	require.NoError(t, err)
	assert.True(t, store.objects[objectKey])

	// Simulate process death: no Complete or in-process cleanup follows Put.
	expirePlatformArtifactCleanupServiceIntent(t, job.ID, intent.ID)
	processed, err := runPlatformArtifactCleanupOnce(context.Background(), store, 3)
	require.NoError(t, err)
	assert.True(t, processed)
	assert.Equal(t, []string{objectKey}, store.deleteCalls)
	assert.False(t, store.objects[objectKey])
	var quarantined model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.First(&quarantined, "id = ?", intent.ID).Error)
	assert.Equal(t, model.PlatformArtifactUploadIntentQuarantined, quarantined.State)
	assert.Equal(t, 1, quarantined.DeletePasses)

	// Model the original Put committing remotely after the first Delete and
	// after its process has disappeared, so no in-process acknowledgement can
	// re-arm cleanup. The durable quarantine must survive and delete it again.
	store.objects[objectKey] = true
	require.NoError(t, model.DB.Model(&quarantined).
		Update("available_at", time.Now().UTC().Add(-time.Second)).Error)
	processed, err = runPlatformArtifactCleanupOnce(context.Background(), store, 3)
	require.NoError(t, err)
	assert.True(t, processed)
	assert.Equal(t, []string{objectKey, objectKey}, store.deleteCalls)
	assert.False(t, store.objects[objectKey])
	var cleaned model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.First(&cleaned, "id = ?", intent.ID).Error)
	assert.Equal(t, model.PlatformArtifactUploadIntentCleaned, cleaned.State)
	assert.Equal(t, 2, cleaned.DeletePasses)
	assert.True(t, cleaned.AvailableAt.After(time.Now().UTC()))

	// No finite HTTP timeout can prove that an unacknowledged remote Put will
	// never commit later. Even after the mandatory second delete, the cleaned
	// tombstone remains scheduled forever. Simulate a much later commit with no
	// RecordPlatformArtifactUploadPut call, advance the DB-owned due time, and
	// prove the periodic reaper deletes it again.
	store.objects[objectKey] = true
	require.NoError(t, model.DB.Model(&cleaned).
		Update("available_at", time.Now().UTC().Add(-time.Second)).Error)
	processed, err = runPlatformArtifactCleanupOnce(context.Background(), store, 3)
	require.NoError(t, err)
	assert.True(t, processed)
	assert.Equal(t, []string{objectKey, objectKey, objectKey}, store.deleteCalls)
	assert.False(t, store.objects[objectKey])
	var reapedAgain model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.First(&reapedAgain, "id = ?", intent.ID).Error)
	assert.Equal(t, model.PlatformArtifactUploadIntentCleaned, reapedAgain.State)
	assert.Equal(t, 3, reapedAgain.DeletePasses)
	assert.True(t, reapedAgain.AvailableAt.After(time.Now().UTC()))

	// A long OBS outage after cleanup must not terminally dead-letter the
	// permanent tombstone, even when the ordinary retry budget is one.
	store.objects[objectKey] = true
	store.deleteFailures = 1
	require.NoError(t, model.DB.Model(&reapedAgain).
		Update("available_at", time.Now().UTC().Add(-time.Second)).Error)
	processed, err = runPlatformArtifactCleanupOnce(context.Background(), store, 1)
	require.NoError(t, err)
	assert.True(t, processed)
	var retryForever model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.First(&retryForever, "id = ?", intent.ID).Error)
	assert.Equal(t, model.PlatformArtifactUploadIntentCleaned, retryForever.State)
	assert.Equal(t, "artifact_delete_failed", retryForever.LastErrorCode)
	assert.True(t, store.objects[objectKey])
	counts, err := model.GetPlatformArtifactUploadIntentCounts()
	require.NoError(t, err)
	assert.EqualValues(t, 1, counts.CleanedRetrying)
	require.NoError(t, model.DB.Model(&retryForever).
		Update("available_at", time.Now().UTC().Add(-time.Second)).Error)
	processed, err = runPlatformArtifactCleanupOnce(context.Background(), store, 1)
	require.NoError(t, err)
	assert.True(t, processed)
	assert.False(t, store.objects[objectKey])
	var recovered model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.First(&recovered, "id = ?", intent.ID).Error)
	assert.Equal(t, model.PlatformArtifactUploadIntentCleaned, recovered.State)
	assert.Empty(t, recovered.LastErrorCode)
	counts, err = model.GetPlatformArtifactUploadIntentCounts()
	require.NoError(t, err)
	assert.Zero(t, counts.CleanedRetrying)
}

func TestArtifactCleanupLegacyCleanedEvidenceNeverDeadLettersDuringLongOutage(t *testing.T) {
	store := &cleanupRecordingArtifactStore{objects: map[string]bool{}}
	job, _, objectKey, intent := createPlatformArtifactCleanupServiceIntent(t, store)
	expirePlatformArtifactCleanupServiceIntent(t, job.ID, intent.ID)
	cleanedAt := time.Now().UTC().Add(-time.Hour)
	require.NoError(t, model.DB.Model(&model.PlatformArtifactUploadIntent{}).
		Where("id = ?", intent.ID).
		Updates(map[string]any{
			"state":         model.PlatformArtifactUploadIntentCleaned,
			"delete_passes": 0,
			"cleaned_at":    cleanedAt,
			"available_at":  time.Now().UTC().Add(-time.Second),
		}).Error)
	store.objects[objectKey] = true
	store.deleteFailures = platformArtifactCleanupMaxAttempts + 2

	for attempt := 0; attempt < platformArtifactCleanupMaxAttempts+2; attempt++ {
		processed, err := runPlatformArtifactCleanupOnce(
			context.Background(),
			store,
			platformArtifactCleanupMaxAttempts,
		)
		require.NoError(t, err)
		require.True(t, processed)
		var retrying model.PlatformArtifactUploadIntent
		require.NoError(t, model.DB.First(&retrying, "id = ?", intent.ID).Error)
		assert.Equal(t, model.PlatformArtifactUploadIntentCleaned, retrying.State)
		assert.Equal(t, "artifact_delete_failed", retrying.LastErrorCode)
		require.NoError(t, model.DB.Model(&retrying).
			Update("available_at", time.Now().UTC().Add(-time.Second)).Error)
	}

	processed, err := runPlatformArtifactCleanupOnce(
		context.Background(),
		store,
		platformArtifactCleanupMaxAttempts,
	)
	require.NoError(t, err)
	assert.True(t, processed)
	assert.False(t, store.objects[objectKey])
	var recovered model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.First(&recovered, "id = ?", intent.ID).Error)
	assert.NotEqual(t, model.PlatformArtifactUploadIntentDeadLetter, recovered.State)
	assert.Empty(t, recovered.LastErrorCode)
}

func TestArtifactCleanupLatePutResetsAccumulatedCleanedAttemptsBeforeFailure(t *testing.T) {
	store := &cleanupRecordingArtifactStore{objects: map[string]bool{}}
	job, transferToken, objectKey, intent := createPlatformArtifactCleanupServiceIntent(t, store)
	expirePlatformArtifactCleanupServiceIntent(t, job.ID, intent.ID)
	cleanedAt := time.Now().UTC().Add(-time.Hour)
	require.NoError(t, model.DB.Model(&model.PlatformArtifactUploadIntent{}).
		Where("id = ?", intent.ID).
		Updates(map[string]any{
			"state":         model.PlatformArtifactUploadIntentCleaned,
			"attempts":      platformArtifactCleanupMaxAttempts - 1,
			"delete_passes": model.PlatformArtifactCleanupMinimumDeletePasses,
			"cleaned_at":    cleanedAt,
			"available_at":  time.Now().UTC().Add(-time.Second),
		}).Error)
	store.objects[objectKey] = true

	won, err := model.RecordPlatformArtifactUploadPut(job.ID, transferToken, objectKey, "")
	require.NoError(t, err)
	require.True(t, won)
	var rearmed model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.First(&rearmed, "id = ?", intent.ID).Error)
	assert.Equal(t, model.PlatformArtifactUploadIntentPending, rearmed.State)
	assert.Zero(t, rearmed.Attempts)
	assert.Zero(t, rearmed.DeletePasses)
	assert.Nil(t, rearmed.CleanedAt)

	store.deleteFailures = 1
	processed, err := runPlatformArtifactCleanupOnce(
		context.Background(),
		store,
		platformArtifactCleanupMaxAttempts,
	)
	require.NoError(t, err)
	assert.True(t, processed)
	var retrying model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.First(&retrying, "id = ?", intent.ID).Error)
	assert.Equal(t, model.PlatformArtifactUploadIntentPending, retrying.State)
	assert.Equal(t, 1, retrying.Attempts)
	assert.NotEqual(t, model.PlatformArtifactUploadIntentDeadLetter, retrying.State)
}

func TestArtifactCleanupPersistsAndDeletesExactAcknowledgedObjectVersion(t *testing.T) {
	store := &cleanupRecordingArtifactStore{objects: map[string]bool{}}
	job, transferToken, objectKey, intent := createPlatformArtifactCleanupServiceIntent(t, store)
	store.objects[objectKey] = true
	won, err := model.RecordPlatformArtifactUploadPut(job.ID, transferToken, objectKey, "obs-version-exact")
	require.NoError(t, err)
	require.True(t, won)
	expirePlatformArtifactCleanupServiceIntent(t, job.ID, intent.ID)

	processed, err := runPlatformArtifactCleanupOnce(context.Background(), store, 3)
	require.NoError(t, err)
	assert.True(t, processed)
	assert.Equal(t, []string{"obs-version-exact"}, store.deleteVersions)
	var persisted model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.First(&persisted, "id = ?", intent.ID).Error)
	assert.Equal(t, "obs-version-exact", persisted.StoreVersionID)
	assert.Equal(t, model.PlatformArtifactUploadIntentQuarantined, persisted.State)
}

func TestArtifactCleanupNeverDeletesPublishedObjectAfterUnknownCompleteCommit(t *testing.T) {
	store := &cleanupRecordingArtifactStore{objects: map[string]bool{}}
	job, transferToken, objectKey, intent := createPlatformArtifactCleanupServiceIntent(t, store)
	store.objects[objectKey] = true
	outputsJSON := `[{"asset_id":"asset-one","object_key":"` + objectKey + `"}]`

	// The caller deliberately discards the successful result, modeling a lost
	// commit acknowledgement after PostgreSQL committed job + intent together.
	_, _ = model.CompletePlatformGenerationTransfer(
		job.ID,
		transferToken,
		objectKey,
		outputsJSON,
	)
	require.NoError(t, model.DB.Model(&model.PlatformArtifactUploadIntent{}).Where("id = ?", intent.ID).
		Update("available_at", time.Now().UTC().Add(-time.Second)).Error)

	processed, err := runPlatformArtifactCleanupOnce(context.Background(), store, 3)
	assert.False(t, processed)
	assert.ErrorIs(t, err, gorm.ErrRecordNotFound)
	assert.Empty(t, store.deleteCalls)
	assert.True(t, store.objects[objectKey])
	var persisted model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.First(&persisted, "id = ?", intent.ID).Error)
	assert.Equal(t, model.PlatformArtifactUploadIntentPublished, persisted.State)
}

func TestArtifactCleanupRetriesTemporaryDeleteAndThenCompletes(t *testing.T) {
	store := &cleanupRecordingArtifactStore{
		objects:        map[string]bool{},
		deleteFailures: 1,
	}
	job, _, objectKey, intent := createPlatformArtifactCleanupServiceIntent(t, store)
	store.objects[objectKey] = true
	expirePlatformArtifactCleanupServiceIntent(t, job.ID, intent.ID)

	processed, err := runPlatformArtifactCleanupOnce(context.Background(), store, 3)
	require.NoError(t, err)
	assert.True(t, processed)
	var retry model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.First(&retry, "id = ?", intent.ID).Error)
	assert.Equal(t, model.PlatformArtifactUploadIntentPending, retry.State)
	assert.Equal(t, 1, retry.Attempts)
	assert.Equal(t, "artifact_delete_failed", retry.LastErrorCode)
	counts, err := model.GetPlatformArtifactUploadIntentCounts()
	require.NoError(t, err)
	assert.EqualValues(t, 1, counts.Retrying)
	assert.Zero(t, counts.Due, "retry backoff must remain visible even before it is due")
	require.NoError(t, model.DB.Model(&retry).Update("available_at", time.Now().UTC().Add(-time.Second)).Error)

	processed, err = runPlatformArtifactCleanupOnce(context.Background(), store, 3)
	require.NoError(t, err)
	assert.True(t, processed)
	assert.Len(t, store.deleteCalls, 2)
	assert.False(t, store.objects[objectKey])
	var quarantined model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.First(&quarantined, "id = ?", intent.ID).Error)
	assert.Equal(t, model.PlatformArtifactUploadIntentQuarantined, quarantined.State)
	counts, err = model.GetPlatformArtifactUploadIntentCounts()
	require.NoError(t, err)
	assert.Zero(t, counts.Retrying)
	require.NoError(t, model.DB.Model(&quarantined).
		Update("available_at", time.Now().UTC().Add(-time.Second)).Error)
	processed, err = runPlatformArtifactCleanupOnce(context.Background(), store, 3)
	require.NoError(t, err)
	assert.True(t, processed)
	assert.Len(t, store.deleteCalls, 3)
	var cleaned model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.First(&cleaned, "id = ?", intent.ID).Error)
	assert.Equal(t, model.PlatformArtifactUploadIntentCleaned, cleaned.State)
}

func TestArtifactCleanupTakeoverFencesExpiredWorkerAndKeepsSecondDelete(t *testing.T) {
	store := &cleanupRecordingArtifactStore{objects: map[string]bool{}}
	job, _, objectKey, intent := createPlatformArtifactCleanupServiceIntent(t, store)
	store.objects[objectKey] = true
	expirePlatformArtifactCleanupServiceIntent(t, job.ID, intent.ID)

	stale, err := model.ClaimPlatformArtifactCleanup(time.Minute)
	require.NoError(t, err)
	require.NoError(t, model.DB.Model(&model.PlatformArtifactUploadIntent{}).Where("id = ?", intent.ID).
		Update("claim_expires_at", time.Now().UTC().Add(-time.Second)).Error)
	processed, err := runPlatformArtifactCleanupOnce(context.Background(), store, 3)
	require.NoError(t, err)
	assert.True(t, processed)

	won, err := model.CompletePlatformArtifactCleanup(
		intent.ID,
		stale.Token,
		platformArtifactInitialReapInterval,
		platformArtifactPeriodicReapInterval,
		model.PlatformArtifactCleanupMinimumDeletePasses,
	)
	require.NoError(t, err)
	assert.False(t, won)
	var quarantined model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.First(&quarantined, "id = ?", intent.ID).Error)
	assert.Equal(t, model.PlatformArtifactUploadIntentQuarantined, quarantined.State)
	assert.Equal(t, 1, quarantined.DeletePasses)
	require.NoError(t, model.DB.Model(&quarantined).
		Update("available_at", time.Now().UTC().Add(-time.Second)).Error)
	processed, err = runPlatformArtifactCleanupOnce(context.Background(), store, 3)
	require.NoError(t, err)
	assert.True(t, processed)
	assert.Len(t, store.deleteCalls, 2)
}

func TestArtifactCleanupDeadLettersAtMaximumAttemptsAndReportsReadinessCounts(t *testing.T) {
	store := &cleanupRecordingArtifactStore{
		objects:        map[string]bool{},
		deleteFailures: 1,
	}
	job, _, objectKey, intent := createPlatformArtifactCleanupServiceIntent(t, store)
	store.objects[objectKey] = true
	expirePlatformArtifactCleanupServiceIntent(t, job.ID, intent.ID)

	processed, err := runPlatformArtifactCleanupOnce(context.Background(), store, 1)
	require.NoError(t, err)
	assert.True(t, processed)
	var dead model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.First(&dead, "id = ?", intent.ID).Error)
	assert.Equal(t, model.PlatformArtifactUploadIntentDeadLetter, dead.State)
	assert.Equal(t, 1, dead.Attempts)
	assert.Equal(t, "artifact_delete_failed", dead.LastErrorCode)
	counts, err := model.GetPlatformArtifactUploadIntentCounts()
	require.NoError(t, err)
	assert.EqualValues(t, 1, counts.DeadLetter)
	assert.Zero(t, counts.Due)

	processed, err = runPlatformArtifactCleanupOnce(context.Background(), store, 1)
	assert.False(t, processed)
	assert.ErrorIs(t, err, gorm.ErrRecordNotFound)
	assert.Len(t, store.deleteCalls, 1)
}

func TestArtifactCleanupBindingChangeDeadLettersWithoutDeleting(t *testing.T) {
	store := &cleanupRecordingArtifactStore{objects: map[string]bool{}}
	job, _, objectKey, intent := createPlatformArtifactCleanupServiceIntent(t, store)
	store.objects[objectKey] = true
	expirePlatformArtifactCleanupServiceIntent(t, job.ID, intent.ID)

	// Simulate an OBS endpoint/bucket or filesystem root change after Put.
	// The cleanup worker must never send this object key to the new binding.
	store.bindingID = strings.Repeat("e", 64)
	processed, err := runPlatformArtifactCleanupOnce(context.Background(), store, 3)
	require.NoError(t, err)
	assert.True(t, processed)
	assert.Empty(t, store.deleteCalls)
	assert.True(t, store.objects[objectKey])

	var persisted model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.First(&persisted, "id = ?", intent.ID).Error)
	assert.Equal(t, model.PlatformArtifactUploadIntentPending, persisted.State)
	assert.Equal(t, "artifact_store_binding_changed", persisted.LastErrorCode)
	counts, err := model.GetPlatformArtifactUploadIntentCounts()
	require.NoError(t, err)
	assert.EqualValues(t, 1, counts.Retrying)
	assert.EqualValues(t, 1, counts.RetryingBindingMismatch)
	assert.Zero(t, counts.Due)

	for attempt := 1; attempt < 3; attempt++ {
		require.NoError(t, model.DB.Model(&model.PlatformArtifactUploadIntent{}).
			Where("id = ?", intent.ID).
			Update("available_at", time.Now().UTC().Add(-time.Second)).Error)
		processed, err = runPlatformArtifactCleanupOnce(context.Background(), store, 3)
		require.NoError(t, err)
		assert.True(t, processed)
	}
	require.NoError(t, model.DB.First(&persisted, "id = ?", intent.ID).Error)
	assert.Equal(t, model.PlatformArtifactUploadIntentDeadLetter, persisted.State)
	assert.Equal(t, "artifact_store_binding_changed", persisted.LastErrorCode)
	counts, err = model.GetPlatformArtifactUploadIntentCounts()
	require.NoError(t, err)
	assert.EqualValues(t, 1, counts.DeadLetter)
	assert.EqualValues(t, 1, counts.BindingMismatchDeadLetter)
	assert.Zero(t, counts.Retrying)
	assert.Zero(t, counts.RetryingBindingMismatch)
}

func TestArtifactCleanupCleanedBindingMismatchRemainsRetryableAndVisible(t *testing.T) {
	store := &cleanupRecordingArtifactStore{objects: map[string]bool{}}
	job, _, objectKey, intent := createPlatformArtifactCleanupServiceIntent(t, store)
	store.objects[objectKey] = true
	expirePlatformArtifactCleanupServiceIntent(t, job.ID, intent.ID)

	processed, err := runPlatformArtifactCleanupOnce(context.Background(), store, 1)
	require.NoError(t, err)
	require.True(t, processed)
	var quarantined model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.First(&quarantined, "id = ?", intent.ID).Error)
	require.NoError(t, model.DB.Model(&quarantined).
		Update("available_at", time.Now().UTC().Add(-time.Second)).Error)
	processed, err = runPlatformArtifactCleanupOnce(context.Background(), store, 1)
	require.NoError(t, err)
	require.True(t, processed)

	store.bindingID = strings.Repeat("e", 64)
	var cleaned model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.First(&cleaned, "id = ?", intent.ID).Error)
	require.NoError(t, model.DB.Model(&cleaned).
		Update("available_at", time.Now().UTC().Add(-time.Second)).Error)
	processed, err = runPlatformArtifactCleanupOnce(context.Background(), store, 1)
	require.NoError(t, err)
	assert.True(t, processed)
	assert.Len(t, store.deleteCalls, 2, "the replacement binding must never receive the old object key")

	var retrying model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.First(&retrying, "id = ?", intent.ID).Error)
	assert.Equal(t, model.PlatformArtifactUploadIntentCleaned, retrying.State)
	assert.Equal(t, "artifact_store_binding_changed", retrying.LastErrorCode)
	counts, err := model.GetPlatformArtifactUploadIntentCounts()
	require.NoError(t, err)
	assert.Zero(t, counts.DeadLetter)
	assert.EqualValues(t, 1, counts.CleanedRetrying)
	assert.EqualValues(t, 1, counts.CleanedBindingMismatch)
}
