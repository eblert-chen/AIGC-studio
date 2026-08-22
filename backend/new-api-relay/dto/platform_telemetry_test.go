package dto

import (
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestPlatformTaskStagePayloadGoldenJSONContract(t *testing.T) {
	routeID := int64(42)
	duration := int64(12_345)
	payload := PlatformTaskStagePayload{
		SchemaVersion:  1,
		CompanyID:      "11111111-1111-4111-8111-111111111111",
		TaskID:         "22222222-2222-4222-8222-222222222222",
		RelayJobID:     "33333333-3333-4333-8333-333333333333",
		Stage:          PlatformTaskStageProviderProcessing,
		OccurredAt:     time.Date(2026, 8, 10, 12, 30, 0, 0, time.UTC),
		ChannelKey:     "official-video-account-1",
		ChannelType:    "official",
		RouteID:        &routeID,
		ProviderTaskID: "provider-task-9",
		DurationMS:     &duration,
		ErrorCode:      "",
	}
	require.NoError(t, payload.Validate())
	encoded, err := common.Marshal(payload)
	require.NoError(t, err)
	assert.JSONEq(t, `{
		"schema_version":1,
		"company_id":"11111111-1111-4111-8111-111111111111",
		"task_id":"22222222-2222-4222-8222-222222222222",
		"relay_job_id":"33333333-3333-4333-8333-333333333333",
		"stage":"provider_processing",
		"occurred_at":"2026-08-10T12:30:00Z",
		"channel_key":"official-video-account-1",
		"channel_type":"official",
		"route_id":42,
		"provider_task_id":"provider-task-9",
		"duration_ms":12345,
		"error_code":""
	}`, string(encoded))
}

func TestPlatformOperationsSnapshotGoldenJSONContract(t *testing.T) {
	observed := time.Date(2026, 8, 10, 12, 30, 0, 0, time.UTC)
	snapshot := PlatformOperationsSnapshot{
		SchemaVersion:   1,
		ObservedAt:      observed,
		ExpiresAt:       observed.Add(2 * time.Minute),
		WindowStartedAt: observed.Add(-15 * time.Minute),
		MonitorFresh:    false,
		Routes:          []PlatformOperationsRouteSnapshot{},
	}
	require.NoError(t, snapshot.Validate())
	encoded, err := common.Marshal(snapshot)
	require.NoError(t, err)
	assert.JSONEq(t, `{
		"schema_version":1,
		"observed_at":"2026-08-10T12:30:00Z",
		"expires_at":"2026-08-10T12:32:00Z",
		"window_started_at":"2026-08-10T12:15:00Z",
		"monitor_fresh":false,
		"monitor_last_completed_at":null,
		"routes":[],
		"account_pool":{"total_accounts":0,"active_accounts":0,"cooling_accounts":0,"invalid_accounts":0,"busy_accounts":0,"rate_limited_accounts":0,"active_task_count":0,"task_capacity":0},
		"tasks":{"queued":0,"submitting":0,"submission_unknown":0,"provider_processing":0,"artifact_transferring":0,"succeeded":0,"failed":0,"cancelled":0,"rate_limited_count":0,"failover_count":0},
		"deliveries":{"pending_alert_count":0,"dead_letter_alert_count":0,"oldest_pending_alert_at":null,"pending_cost_count":0,"dead_letter_cost_count":0,"pending_task_stage_count":0,"dead_letter_task_stage_count":0,"pending_snapshot_count":0,"dead_letter_snapshot_count":0},
		"costs":{"successful_relay_jobs":0,"explicit_cost_relay_jobs":0,"delivered_cost_relay_jobs":0,"incomplete_relay_jobs":0,"native_billing_reconciliation_jobs":0,"reconciliation_complete":false}
	}`, string(encoded))
}

func TestPlatformTelemetryRejectsUnknownStagesAndIncompleteRouteIdentity(t *testing.T) {
	base := PlatformTaskStagePayload{
		SchemaVersion: 1,
		CompanyID:     "11111111-1111-4111-8111-111111111111",
		TaskID:        "22222222-2222-4222-8222-222222222222",
		RelayJobID:    "33333333-3333-4333-8333-333333333333",
		Stage:         PlatformTaskStageQueued,
		OccurredAt:    time.Now().UTC(),
	}
	require.NoError(t, base.Validate())

	unknown := base
	unknown.Stage = "made_up"
	assert.ErrorContains(t, unknown.Validate(), "stage is invalid")

	routeID := int64(1)
	incomplete := base
	incomplete.RouteID = &routeID
	assert.ErrorContains(t, incomplete.Validate(), "route identity is incomplete")
}
