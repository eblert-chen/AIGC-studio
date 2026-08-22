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
	now := time.Now().UTC()
	job := PlatformGenerationJob{
		ID:                         uuid.NewString(),
		TenantID:                   uuid.NewString(),
		SourceClientID:             "platform",
		RequestID:                  "artifact-upload-intent-request",
		IdempotencyKey:             uuid.NewString(),
		RequestHash:                strings.Repeat("a", 64),
		RequestJSON:                `{}`,
		Model:                      "artifact-upload-model",
		Mode:                       "text_to_video",
		ExpectedCapabilityRevision: "sha256:" + strings.Repeat("b", 64),
		CapabilityRevision:         "sha256:" + strings.Repeat("b", 64),
		Status:                     PlatformGenerationStatusTransferring,
		Progress:                   95,
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
