package model

import (
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func preparePlatformGenerationCallbackTest(t *testing.T) {
	t.Helper()
	require.NoError(t, DB.AutoMigrate(&PlatformGenerationCallbackDelivery{}))
	require.NoError(t, DB.Session(&gorm.Session{AllowGlobalUpdate: true}).Delete(&PlatformGenerationCallbackDelivery{}).Error)
	t.Cleanup(func() {
		require.NoError(t, DB.Session(&gorm.Session{AllowGlobalUpdate: true}).Delete(&PlatformGenerationCallbackDelivery{}).Error)
	})
}

func createPlatformGenerationCallbackFixture(t *testing.T, maxAttempts int) *PlatformGenerationCallbackDelivery {
	t.Helper()
	delivery := &PlatformGenerationCallbackDelivery{
		ID:             uuid.NewString(),
		TenantID:       uuid.NewString(),
		SourceClientID: "platform-service",
		JobID:          uuid.NewString(),
		CallbackURL:    "https://callbacks.example.com/internal/relay",
		RequestID:      "relay-request-42",
		PayloadJSON:    `{"api_version":"v1"}`,
		PayloadSHA256:  strings.Repeat("a", 64),
		MaxAttempts:    maxAttempts,
	}
	created, err := CreatePlatformGenerationCallbackDelivery(delivery)
	require.NoError(t, err)
	require.True(t, created)
	return delivery
}

func TestPlatformGenerationCallbackClaimTokenAndExpiryFenceCompletion(t *testing.T) {
	preparePlatformGenerationCallbackTest(t)
	delivery := createPlatformGenerationCallbackFixture(t, 3)

	first, err := ClaimPlatformGenerationCallbackDelivery(30 * time.Second)
	require.NoError(t, err)
	assert.Equal(t, delivery.ID, first.Delivery.ID)
	assert.Equal(t, 1, first.Delivery.Attempts)
	assert.NotEmpty(t, first.Token)

	won, err := CompletePlatformGenerationCallbackDelivery(delivery.ID, uuid.NewString(), httpStatusNoContent)
	require.NoError(t, err)
	assert.False(t, won)

	// Expiry alone invalidates the old token, even before another worker
	// obtains a replacement claim.
	require.NoError(t, DB.Model(&PlatformGenerationCallbackDelivery{}).Where("id = ?", delivery.ID).
		Update("claim_expires_at", time.Unix(1, 0).UTC()).Error)
	won, err = CompletePlatformGenerationCallbackDelivery(delivery.ID, first.Token, httpStatusNoContent)
	require.NoError(t, err)
	assert.False(t, won)

	second, err := ClaimPlatformGenerationCallbackDelivery(30 * time.Second)
	require.NoError(t, err)
	assert.Equal(t, 2, second.Delivery.Attempts)
	assert.NotEqual(t, first.Token, second.Token)

	won, err = CompletePlatformGenerationCallbackDelivery(delivery.ID, first.Token, httpStatusNoContent)
	require.NoError(t, err)
	assert.False(t, won, "an expired worker must not overwrite the current owner")
	won, err = CompletePlatformGenerationCallbackDelivery(delivery.ID, second.Token, httpStatusNoContent)
	require.NoError(t, err)
	assert.True(t, won)

	var persisted PlatformGenerationCallbackDelivery
	require.NoError(t, DB.First(&persisted, "id = ?", delivery.ID).Error)
	assert.Equal(t, PlatformGenerationCallbackDelivered, persisted.State)
	assert.Equal(t, httpStatusNoContent, persisted.ResponseStatus)
	assert.NotNil(t, persisted.DeliveredAt)
	assert.Empty(t, persisted.ClaimToken)
}

func TestPlatformGenerationCallbackRetryBudgetEndsInDeadLetter(t *testing.T) {
	preparePlatformGenerationCallbackTest(t)
	delivery := createPlatformGenerationCallbackFixture(t, 2)

	first, err := ClaimPlatformGenerationCallbackDelivery(30 * time.Second)
	require.NoError(t, err)
	state, won, err := ReleasePlatformGenerationCallbackDelivery(
		delivery.ID,
		first.Token,
		0,
		PlatformGenerationCallbackFailureTransport,
		0,
	)
	require.NoError(t, err)
	assert.True(t, won)
	assert.Equal(t, PlatformGenerationCallbackPending, state)

	second, err := ClaimPlatformGenerationCallbackDelivery(30 * time.Second)
	require.NoError(t, err)
	assert.Equal(t, 2, second.Delivery.Attempts)
	state, won, err = ReleasePlatformGenerationCallbackDelivery(
		delivery.ID,
		second.Token,
		0,
		"https://secret.example/callback?credential=must-not-persist",
		httpStatusServiceUnavailable,
	)
	require.NoError(t, err)
	assert.True(t, won)
	assert.Equal(t, PlatformGenerationCallbackDeadLetter, state)

	var persisted PlatformGenerationCallbackDelivery
	require.NoError(t, DB.First(&persisted, "id = ?", delivery.ID).Error)
	assert.Equal(t, PlatformGenerationCallbackDeadLetter, persisted.State)
	assert.Equal(t, 2, persisted.Attempts)
	assert.Equal(t, PlatformGenerationCallbackFailureGeneric, persisted.LastError)
	assert.Equal(t, httpStatusServiceUnavailable, persisted.ResponseStatus)
	assert.NotNil(t, persisted.DeadLetteredAt)
	assert.NotContains(t, persisted.LastError, "secret")

	_, err = ClaimPlatformGenerationCallbackDelivery(30 * time.Second)
	assert.True(t, errors.Is(err, gorm.ErrRecordNotFound))
}

func TestPlatformGenerationCallbackExplicitDeadLetterIsTokenFenced(t *testing.T) {
	preparePlatformGenerationCallbackTest(t)
	delivery := createPlatformGenerationCallbackFixture(t, 8)
	claim, err := ClaimPlatformGenerationCallbackDelivery(30 * time.Second)
	require.NoError(t, err)

	won, err := DeadLetterPlatformGenerationCallbackDelivery(
		delivery.ID,
		uuid.NewString(),
		PlatformGenerationCallbackFailureTarget,
		0,
	)
	require.NoError(t, err)
	assert.False(t, won)
	won, err = DeadLetterPlatformGenerationCallbackDelivery(
		delivery.ID,
		claim.Token,
		PlatformGenerationCallbackFailureTarget,
		0,
	)
	require.NoError(t, err)
	assert.True(t, won)

	var persisted PlatformGenerationCallbackDelivery
	require.NoError(t, DB.First(&persisted, "id = ?", delivery.ID).Error)
	assert.Equal(t, PlatformGenerationCallbackDeadLetter, persisted.State)
	assert.Equal(t, PlatformGenerationCallbackFailureTarget, persisted.LastError)
}

func TestPlatformGenerationCallbackExpiredFinalAttemptIsRecoveredToDeadLetter(t *testing.T) {
	preparePlatformGenerationCallbackTest(t)
	delivery := createPlatformGenerationCallbackFixture(t, 1)
	_, err := ClaimPlatformGenerationCallbackDelivery(30 * time.Second)
	require.NoError(t, err)
	require.NoError(t, DB.Model(&PlatformGenerationCallbackDelivery{}).Where("id = ?", delivery.ID).
		Update("claim_expires_at", time.Unix(1, 0).UTC()).Error)

	_, err = ClaimPlatformGenerationCallbackDelivery(30 * time.Second)
	assert.True(t, errors.Is(err, gorm.ErrRecordNotFound))
	var persisted PlatformGenerationCallbackDelivery
	require.NoError(t, DB.First(&persisted, "id = ?", delivery.ID).Error)
	assert.Equal(t, PlatformGenerationCallbackDeadLetter, persisted.State)
	assert.Equal(t, PlatformGenerationCallbackFailureGeneric, persisted.LastError)
	assert.NotNil(t, persisted.DeadLetteredAt)
}

const (
	httpStatusNoContent          = 204
	httpStatusServiceUnavailable = 503
)
