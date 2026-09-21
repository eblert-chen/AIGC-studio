package model

import (
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func TestRelaySchemaSQLiteV5ToV6AddsOnlyDurableChannelTestLifecycle(t *testing.T) {
	database := newRelaySchemaSQLite(t)
	require.NoError(t, MigratePlatformChannelControlStorageV5WithDB(database))

	fields := []string{
		"IntentPublicModelID",
		"IntentRouteID",
		"IntentTransportRevision",
		"IntentTransportSHA256",
		"ProviderSubmissionState",
		"ProviderBlockerCode",
		"ProviderTaskID",
		"ProviderArtifactSHA256",
		"ProviderArtifactSizeBytes",
		"ProviderArtifactContentType",
		"ReconciliationActor",
		"ReconciliationReason",
		"ReconciledAt",
	}
	for _, field := range fields {
		require.False(t, database.Migrator().HasColumn(&PlatformChannelControlOperation{}, field),
			"frozen v5 must not contain v6 field %s", field)
	}

	require.NoError(t, MigratePlatformChannelControlStorageV6WithDB(database))
	for _, field := range fields {
		require.True(t, database.Migrator().HasColumn(&PlatformChannelControlOperation{}, field),
			"v6 must add lifecycle field %s", field)
	}
	for _, index := range []string{
		"idx_platform_channel_control_route",
		"idx_platform_channel_control_submission",
		"ux_platform_channel_control_unresolved_route",
		"ux_platform_channel_control_provider_task",
	} {
		var count int64
		require.NoError(t, database.Raw(
			`SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND name = ?`, index,
		).Scan(&count).Error)
		require.Equal(t, int64(1), count, "missing v6 index %s", index)
	}

	for _, testCase := range []struct {
		name   string
		mutate func(*PlatformChannelControlOperation)
	}{
		{name: "missing-revision", mutate: func(operation *PlatformChannelControlOperation) { operation.IntentTransportRevision = "" }},
		{name: "missing-digest", mutate: func(operation *PlatformChannelControlOperation) { operation.IntentTransportSHA256 = "" }},
		{name: "malformed-revision", mutate: func(operation *PlatformChannelControlOperation) { operation.IntentTransportRevision = "sha256:xyz" }},
		{name: "malformed-digest", mutate: func(operation *PlatformChannelControlOperation) { operation.IntentTransportSHA256 = "sha256:xyz" }},
	} {
		invalid := relaySchemaV6PendingRouteTest("invalid-transport-"+testCase.name, 41)
		testCase.mutate(&invalid)
		require.Error(t, database.Session(&gorm.Session{SkipHooks: true}).Create(&invalid).Error,
			"route-test transport binding %s must be rejected at the database boundary", testCase.name)
	}

	success := relaySchemaV6PendingRouteTest("success", 41)
	require.NoError(t, database.Session(&gorm.Session{SkipHooks: true}).Create(&success).Error)
	duplicate := relaySchemaV6PendingRouteTest("duplicate", 41)
	duplicate.IntentPublicModelID = success.IntentPublicModelID
	duplicate.IntentRouteID = success.IntentRouteID
	require.Error(t, database.Session(&gorm.Session{SkipHooks: true}).Create(&duplicate).Error,
		"only one unresolved test may hold a route")

	require.NoError(t, database.Exec(`UPDATE platform_channel_control_operations
SET provider_submission_state = 'submission_unknown' WHERE id = ?`, success.ID).Error)
	require.NoError(t, database.Exec(`UPDATE platform_channel_control_operations
SET provider_submission_state = 'submitted', provider_task_id = 'provider-task-v6-success'
WHERE id = ?`, success.ID).Error)
	completedAt := time.Now().UTC()
	require.NoError(t, database.Exec(`UPDATE platform_channel_control_operations
SET state = 'succeeded', provider_submission_state = 'artifact_verified',
    result_success = true, result_response_ms = 37, result_error_code = '',
    provider_artifact_sha256 = ?, provider_artifact_size_bytes = 2048,
    provider_artifact_content_type = 'video/mp4', completed_at = ?
WHERE id = ?`, strings.Repeat("a", 64), completedAt, success.ID).Error)
	require.Error(t, database.Exec(`UPDATE platform_channel_control_operations SET actor = 'tampered' WHERE id = ?`, success.ID).Error)
	require.Error(t, database.Exec(`DELETE FROM platform_channel_control_operations WHERE id = ?`, success.ID).Error)

	providerRejected := relaySchemaV6PendingRouteTest("provider-rejected", 41)
	require.NoError(t, database.Session(&gorm.Session{SkipHooks: true}).Create(&providerRejected).Error)
	require.NoError(t, database.Exec(`UPDATE platform_channel_control_operations
SET provider_submission_state = 'submission_unknown' WHERE id = ?`, providerRejected.ID).Error)
	require.Error(t, database.Exec(`UPDATE platform_channel_control_operations
SET state = 'failed', provider_submission_state = 'provider_terminal',
    result_success = false, result_response_ms = 19,
    result_error_code = 'CHANNEL_TEST_PROVIDER_VALIDATION', completed_at = ?
WHERE id = ?`, time.Now().UTC(), providerRejected.ID).Error,
		"submission_unknown must never bypass reconciliation through provider_terminal")
	for _, errorCode := range []string{
		"CHANNEL_TEST_FAILED",
		"CHANNEL_TEST_UNAVAILABLE",
		"CHANNEL_TEST_ARTIFACT_INVALID",
		"CHANNEL_TEST_ROUTE_DRIFT",
	} {
		require.Error(t, database.Exec(`UPDATE platform_channel_control_operations
SET state = 'failed', provider_submission_state = 'provider_rejected',
    result_success = false, result_response_ms = 19,
    result_error_code = ?, completed_at = ?
WHERE id = ?`, errorCode, time.Now().UTC(), providerRejected.ID).Error,
			"non-provider code %s must not forge a deterministic rejection", errorCode)
	}
	require.NoError(t, database.Exec(`UPDATE platform_channel_control_operations
SET state = 'failed', provider_submission_state = 'provider_rejected',
    result_success = false, result_response_ms = 19,
    result_error_code = 'CHANNEL_TEST_PROVIDER_VALIDATION', completed_at = ?
WHERE id = ?`, time.Now().UTC(), providerRejected.ID).Error,
		"a deterministic provider rejection may close submission_unknown without inventing a task id")

	providerTerminal := relaySchemaV6PendingRouteTest("provider-terminal", 41)
	require.NoError(t, database.Session(&gorm.Session{SkipHooks: true}).Create(&providerTerminal).Error)
	require.NoError(t, database.Exec(`UPDATE platform_channel_control_operations
SET provider_submission_state = 'submission_unknown' WHERE id = ?`, providerTerminal.ID).Error)
	require.NoError(t, database.Exec(`UPDATE platform_channel_control_operations
SET provider_submission_state = 'submitted', provider_task_id = 'provider-task-v6-terminal'
WHERE id = ?`, providerTerminal.ID).Error)
	require.NoError(t, database.Exec(`UPDATE platform_channel_control_operations
SET state = 'failed', provider_submission_state = 'provider_terminal',
    result_success = false, result_response_ms = 19,
    result_error_code = 'CHANNEL_TEST_PROVIDER_TERMINAL', completed_at = ?
WHERE id = ?`, time.Now().UTC(), providerTerminal.ID).Error,
		"provider_terminal is valid only after a durable provider task was bound")

	reconciled := relaySchemaV6PendingRouteTest("reconciled", 41)
	require.NoError(t, database.Session(&gorm.Session{SkipHooks: true}).Create(&reconciled).Error)
	require.NoError(t, database.Exec(`UPDATE platform_channel_control_operations
SET provider_submission_state = 'submission_unknown' WHERE id = ?`, reconciled.ID).Error)
	reconciledAt := time.Now().UTC()
	require.NoError(t, database.Exec(`UPDATE platform_channel_control_operations
SET state = 'failed', provider_submission_state = 'reconciled_no_creation',
    result_success = false, result_response_ms = 0,
    result_error_code = 'CHANNEL_TEST_RECONCILED_NO_CREATION',
    reconciliation_actor = 'platform-owner', reconciliation_reason = 'provider console proves no task',
    reconciled_at = ?, completed_at = ?
WHERE id = ?`, reconciledAt, reconciledAt, reconciled.ID).Error)
}

func relaySchemaV6PendingRouteTest(suffix string, channelID int) PlatformChannelControlOperation {
	return PlatformChannelControlOperation{
		ID: uuid.NewString(), TenantID: uuid.NewString(), OperationID: "schema-v6-" + suffix,
		ChannelID: channelID, Kind: PlatformChannelControlOperationKindTest,
		State: PlatformChannelControlOperationPending, RequestID: uuid.NewString(),
		Actor: "schema-v6-test", Reason: "verify durable channel-test lifecycle",
		IntentSHA256: strings.Repeat("2", 64), IntentJSON: `{}`,
		IntentPublicModelID:     "public-model-" + suffix,
		IntentRouteID:           "route-" + suffix,
		IntentTransportRevision: "sha256:" + strings.Repeat("3", 64),
		IntentTransportSHA256:   "sha256:" + strings.Repeat("4", 64),
		ProviderSubmissionState: PlatformChannelTestSubmissionNotStarted,
		CreatedAt:               time.Now().UTC(),
	}
}
