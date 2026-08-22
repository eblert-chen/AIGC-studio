package service

import (
	"context"
	"crypto/sha256"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func TestTaskStageDeliveryUsesServiceCredentialSignatureAndDurableState(t *testing.T) {
	preparePlatformProviderMonitorCostServiceTest(t)
	routeID := int64(41)
	duration := int64(900)
	payload := dto.PlatformTaskStagePayload{
		SchemaVersion: 1,
		CompanyID:     "11111111-1111-4111-8111-111111111111",
		TaskID:        "22222222-2222-4222-8222-222222222222",
		RelayJobID:    "33333333-3333-4333-8333-333333333333",
		Stage:         dto.PlatformTaskStageProviderProcessing,
		OccurredAt:    time.Date(2026, 8, 10, 12, 0, 0, 0, time.UTC),
		ChannelKey:    "official-route-1", ChannelType: "official", RouteID: &routeID,
		ProviderTaskID: "upstream-task-1", DurationMS: &duration,
	}
	body, err := common.Marshal(payload)
	require.NoError(t, err)
	digest := sha256.Sum256(body)
	eventID := uuid.NewString()
	event := model.PlatformTaskStageEvent{
		ID: eventID, RelayJobID: payload.RelayJobID, CompanyID: payload.CompanyID,
		TaskID: payload.TaskID, Stage: payload.Stage, OccurredAt: payload.OccurredAt,
		PayloadJSON: string(body), PayloadSHA256: fmt.Sprintf("%x", digest),
	}
	require.NoError(t, model.DB.Transaction(func(tx *gorm.DB) error {
		_, createErr := model.CreatePlatformTaskStageEventTx(tx, &event)
		return createErr
	}))

	serviceToken := "platform-internal-service-token"
	signingSecret := "independent-telemetry-signing-secret"
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		require.Equal(t, "/internal/relay/task-stages", request.URL.Path)
		require.Equal(t, serviceToken, request.Header.Get("X-Internal-Service-Token"))
		require.Equal(t, eventID, request.Header.Get("X-Relay-Event-ID"))
		timestamp, parseErr := strconv.ParseInt(request.Header.Get("X-Relay-Timestamp"), 10, 64)
		require.NoError(t, parseErr)
		received, readErr := io.ReadAll(request.Body)
		require.NoError(t, readErr)
		expected, signErr := SignPlatformRelayExternalEvent(signingSecret, timestamp, eventID, received)
		require.NoError(t, signErr)
		require.Equal(t, expected, request.Header.Get("X-Relay-Signature"))
		assert.JSONEq(t, string(body), string(received))
		response.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()

	claim, err := model.ClaimPlatformRelayExternalDelivery(model.PlatformRelayDeliveryKindTaskStage, 30*time.Second)
	require.NoError(t, err)
	state, won, err := DeliverPlatformTaskStageClaim(context.Background(), *claim, PlatformTelemetrySinkConfig{
		TaskStageURL:          server.URL + "/internal/relay/task-stages",
		OperationsSnapshotURL: server.URL + "/internal/relay/operations-snapshots",
		InternalServiceToken:  serviceToken,
		SigningSecret:         signingSecret,
	})
	require.NoError(t, err)
	assert.True(t, won)
	assert.Equal(t, model.PlatformRelayDeliveryDelivered, state)
}

func TestPlatformTelemetryCrossLanguageSignatureVector(t *testing.T) {
	body := []byte(`{"schema_version":1,"company_id":"11111111-1111-4111-8111-111111111111","task_id":"22222222-2222-4222-8222-222222222222","relay_job_id":"33333333-3333-4333-8333-333333333333","stage":"provider_processing","occurred_at":"2026-08-10T00:00:00Z","channel_key":"official-primary","channel_type":"official","route_id":17,"provider_task_id":"provider-42","duration_ms":1250,"error_code":""}`)
	signature, err := SignPlatformRelayExternalEvent(
		"cross-language-telemetry-secret-32-bytes!!",
		1786320000,
		"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
		body,
	)
	require.NoError(t, err)
	assert.Equal(t, "v1=9184d12ad56c9b0aaf9e862cdc7d677826e22f1186b4bf29386d12ccdedaca76", signature)
}

func TestOperationsSnapshotUsesLiveAccountStateAndExpiresOldRPMWindow(t *testing.T) {
	preparePlatformProviderMonitorCostServiceTest(t)
	route := createPlatformProviderServiceRoute(t, "operations-route", 9876)
	now := time.Now().UTC()
	require.NoError(t, model.DB.Model(&model.PlatformGenerationProviderAccountState{}).
		Where("id = ?", route.AccountStateID).Updates(map[string]any{
		"rpm_window_started_at": now.Add(-2 * time.Minute),
		"rpm_window_count":      10,
		"active_count":          1,
	}).Error)

	snapshot, err := BuildPlatformOperationsSnapshot(context.Background(), 15*time.Minute, 2*time.Minute)
	require.NoError(t, err)
	require.Len(t, snapshot.Routes, 1)
	assert.Equal(t, int64(0), snapshot.Routes[0].RPMUsed, "expired RPM windows must not appear active")
	assert.Equal(t, int64(1), snapshot.Routes[0].ActiveTaskCount)
	assert.Equal(t, int64(1), snapshot.AccountPool.TotalAccounts)
	assert.Equal(t, int64(1), snapshot.AccountPool.ActiveAccounts)

	require.NoError(t, model.DB.Model(&model.PlatformGenerationProviderRoute{}).
		Where("id = ?", route.ID).Update("enabled", false).Error)
	snapshot, err = BuildPlatformOperationsSnapshot(context.Background(), 15*time.Minute, 2*time.Minute)
	require.NoError(t, err)
	assert.Equal(t, int64(0), snapshot.AccountPool.TotalAccounts, "disabled routes are not usable production accounts")
	assert.Equal(t, "disabled", snapshot.Routes[0].HealthStatus)
}

func TestStagingOperationsSnapshotCountsStagingReadyAccount(t *testing.T) {
	preparePlatformProviderMonitorCostServiceTest(t)
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "staging")
	route := createPlatformProviderServiceRoute(t, "operations-staging-route", 9877)
	require.NoError(t, model.DB.Model(&model.PlatformGenerationProviderRoute{}).
		Where("id = ?", route.ID).
		Updates(map[string]any{"staging_ready": true, "production_ready": false}).Error)

	snapshot, err := BuildPlatformOperationsSnapshot(context.Background(), 15*time.Minute, 2*time.Minute)
	require.NoError(t, err)
	assert.Equal(t, int64(1), snapshot.AccountPool.TotalAccounts)
}
