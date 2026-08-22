package model

import (
	"testing"
	"time"

	"github.com/glebarez/sqlite"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func TestPlatformGenerationLifecyclePersistsTaskStagesAtomically(t *testing.T) {
	previousDB := DB
	db, err := gorm.Open(sqlite.Open("file:task-stage-"+uuid.NewString()+"?mode=memory&cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	sqlDB, err := db.DB()
	require.NoError(t, err)
	sqlDB.SetMaxOpenConns(1)
	require.NoError(t, db.AutoMigrate(
		&PlatformGenerationJob{}, &PlatformGenerationOutbox{},
		&PlatformTaskStageEvent{}, &PlatformRelayExternalDelivery{},
	))
	DB = db
	t.Cleanup(func() {
		DB = previousDB
		require.NoError(t, sqlDB.Close())
	})

	companyID := "11111111-1111-4111-8111-111111111111"
	taskID := "22222222-2222-4222-8222-222222222222"
	job := &PlatformGenerationJob{
		ID: uuid.NewString(), TenantID: companyID, SourceClientID: "platform",
		RequestID: "request-1", IdempotencyKey: "task-stage-idempotency",
		RequestHash: "request-hash", RequestJSON: `{}`,
		ClientReferenceID: &taskID, Model: "video-model", Mode: "text_to_video",
		ExpectedCapabilityRevision: "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
		CapabilityRevision:         "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
		Status:                     PlatformGenerationStatusQueued, OutputsJSON: `[]`, ErrorDetailsJSON: `{}`,
		CreatedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC(),
	}
	_, replayed, conflict, err := CreatePlatformGenerationJob(job)
	require.NoError(t, err)
	assert.False(t, replayed)
	assert.False(t, conflict)

	claim, err := ClaimPlatformGenerationSubmission(30 * time.Second)
	require.NoError(t, err)
	won, err := CompletePlatformGenerationSubmission(*claim, map[string]any{
		"status":          PlatformGenerationStatusFailed,
		"progress":        100,
		"error_code":      PlatformGenerationErrorGenerationFailed,
		"error_message":   "provider rejected the task",
		"error_retryable": false,
	})
	require.NoError(t, err)
	assert.True(t, won)

	var events []PlatformTaskStageEvent
	require.NoError(t, DB.Order("created_at ASC, stage ASC").Find(&events).Error)
	require.Len(t, events, 3)
	stages := make(map[string]bool, len(events))
	for _, event := range events {
		stages[event.Stage] = true
		assert.Equal(t, companyID, event.CompanyID)
		assert.Equal(t, taskID, event.TaskID)
		assert.Equal(t, job.ID, event.RelayJobID)
	}
	assert.True(t, stages["queued"])
	assert.True(t, stages["submitting"])
	assert.True(t, stages["failed"])

	var deliveryCount int64
	require.NoError(t, DB.Model(&PlatformRelayExternalDelivery{}).
		Where("event_kind = ? AND state = ?", PlatformRelayDeliveryKindTaskStage, PlatformRelayDeliveryPending).
		Count(&deliveryCount).Error)
	assert.Equal(t, int64(3), deliveryCount)
}
