package service

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func enableProtectedNativeBillingTest(t *testing.T) {
	t.Helper()
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
}

type protectedNativeBindingFixture struct {
	Task       *model.Task
	Job        *model.PlatformGenerationJob
	Route      *model.PlatformGenerationProviderRoute
	Admission  *model.PlatformGenerationRouteAdmission
	Credential *model.ProviderCredentialVersion
}

func seedProtectedExternalNativeBinding(
	t *testing.T,
	jobStatus string,
	taskStatus model.TaskStatus,
	createTask bool,
) protectedNativeBindingFixture {
	t.Helper()
	now := time.Now().UTC()
	jobID := uuid.NewString()
	tenantID := uuid.NewString()
	taskID, err := model.PlatformGenerationNativeTaskID(jobID)
	require.NoError(t, err)
	keyIndex := 1
	fingerprint := strings.Repeat("a", 64)
	route := &model.PlatformGenerationProviderRoute{
		RouteKey:            "protected-attestation-" + jobID,
		Model:               "video-model",
		Mode:                "text_to_video",
		ProviderName:        "test-provider",
		AccountID:           "account-" + jobID,
		ChannelID:           8701,
		AcceptedChannelType: 1,
		KeyIndex:            keyIndex,
		KeyFingerprint:      fingerprint,
		ChannelClass:        model.PlatformGenerationChannelClassOfficialProvider,
		UpstreamModel:       "provider-video-model",
		ProductionReady:     true,
		Enabled:             true,
		RPMWindowSeconds:    60,
		RPMLimit:            1,
		ActiveLimit:         1,
	}
	require.NoError(t, model.DB.Create(route).Error)
	credential := &model.ProviderCredentialVersion{
		CredentialVersion: uuid.NewString(),
		TenantID:          tenantID,
		ChannelID:         route.ChannelID,
		KeyIndex:          keyIndex,
		KeyFingerprint:    fingerprint,
		KeyID:             "test-v1",
		Version:           1,
		Nonce:             []byte("012345678901"),
		Ciphertext:        []byte("test-ciphertext"),
		CreatedAt:         now,
	}
	require.NoError(t, model.DB.Create(credential).Error)
	task := &model.Task{
		TaskID:     taskID,
		Platform:   constant.TaskPlatform("kling"),
		ChannelId:  route.ChannelID,
		Status:     taskStatus,
		Progress:   "50%",
		Action:     "text_to_video",
		SubmitTime: now.Unix(),
		CreatedAt:  now.Unix(),
		UpdatedAt:  now.Unix(),
		PrivateData: model.TaskPrivateData{
			PinnedKeyIndex:             &keyIndex,
			PinnedKeyFingerprint:       fingerprint,
			ProviderCredentialTenantID: tenantID,
			ProviderCredentialVersion:  credential.CredentialVersion,
			UpstreamTaskID:             "upstream-" + jobID,
			BillingSource:              model.TaskBillingSourcePlatformExternal,
		},
	}
	if taskStatus == model.TaskStatusSuccess || taskStatus == model.TaskStatusFailure {
		task.Progress = "100%"
	}
	if createTask {
		require.NoError(t, model.DB.Create(task).Error)
	}
	attempt := 1
	recoveryJSON, err := json.Marshal(map[string]any{
		"schema_version":                3,
		"billing_owner":                 "platform",
		"billing_policy_revision":       "platform-external-v1",
		"route_id":                      route.ID,
		"attempt":                       attempt,
		"task_id":                       taskID,
		"channel_id":                    route.ChannelID,
		"platform":                      string(task.Platform),
		"user_id":                       task.UserId,
		"group":                         task.Group,
		"action":                        task.Action,
		"submit_time":                   task.SubmitTime,
		"properties":                    task.Properties,
		"pinned_key_index":              keyIndex,
		"pinned_key_fingerprint":        fingerprint,
		"provider_credential_tenant_id": tenantID,
		"provider_credential_version":   credential.CredentialVersion,
	})
	require.NoError(t, err)
	job := &model.PlatformGenerationJob{
		ID:                         jobID,
		TenantID:                   tenantID,
		SourceClientID:             "platform",
		RequestID:                  "billing-attestation",
		IdempotencyKey:             uuid.NewString(),
		RequestHash:                strings.Repeat("b", 64),
		RequestJSON:                "{}",
		Model:                      route.Model,
		Mode:                       route.Mode,
		ExpectedCapabilityRevision: "sha256:" + strings.Repeat("c", 64),
		CapabilityRevision:         "sha256:" + strings.Repeat("c", 64),
		Status:                     jobStatus,
		NativeTaskID:               taskID,
		NativeTaskRecoveryJSON:     string(recoveryJSON),
		ProviderRouteID:            route.ID,
		ProviderChannelID:          route.ChannelID,
		ProviderKeyIndex:           keyIndex,
		ProviderSubmissionAttempt:  attempt,
	}
	if jobStatus == model.PlatformGenerationStatusProcessing || jobStatus == model.PlatformGenerationStatusTransferring {
		job.UpstreamTaskID = task.PrivateData.UpstreamTaskID
	}
	if jobStatus == model.PlatformGenerationStatusSubmitting {
		job.SubmissionLeaseToken = uuid.NewString()
		job.SubmissionLeaseExpiresAt = now.Add(time.Minute)
	}
	require.NoError(t, model.DB.Create(job).Error)
	admissionState := model.PlatformGenerationRouteAdmissionPosting
	slotHeld := true
	var closedAt *time.Time
	if jobStatus == model.PlatformGenerationStatusReconciliationRequired {
		admissionState = model.PlatformGenerationRouteAdmissionUnknown
	} else if jobStatus == model.PlatformGenerationStatusTransferring {
		admissionState = model.PlatformGenerationRouteAdmissionFinished
		slotHeld = false
		closed := now
		closedAt = &closed
	}
	admission := &model.PlatformGenerationRouteAdmission{
		JobID:               job.ID,
		RouteID:             route.ID,
		SubmissionTokenHash: strings.Repeat("d", 64),
		State:               admissionState,
		SlotHeld:            slotHeld,
		Attempt:             attempt,
		ClosedAt:            closedAt,
	}
	require.NoError(t, model.DB.Create(admission).Error)
	return protectedNativeBindingFixture{Task: task, Job: job, Route: route, Admission: admission, Credential: credential}
}

func TestProtectedNativeBillingAttestationRequiresExactPlatformJobBinding(t *testing.T) {
	truncate(t)
	enableProtectedNativeBillingTest(t)
	require.NoError(t, ValidateProtectedPlatformNativeBillingState())

	ordinary := &model.Task{
		TaskID:    "ordinary-unfinished-task",
		Status:    model.TaskStatusInProgress,
		Progress:  "50%",
		CreatedAt: time.Now().Unix(),
		UpdatedAt: time.Now().Unix(),
	}
	require.NoError(t, model.DB.Create(ordinary).Error)
	require.ErrorIs(t, ValidateProtectedPlatformNativeBillingState(), ErrProtectedPlatformNativeBillingState)
	require.NoError(t, model.DB.Delete(ordinary).Error)

	fixture := seedProtectedExternalNativeBinding(
		t,
		model.PlatformGenerationStatusProcessing,
		model.TaskStatusInProgress,
		true,
	)
	require.NoError(t, ValidateProtectedPlatformNativeBillingState())

	require.NoError(t, model.DB.Model(fixture.Task).Update("quota", 1).Error)
	require.ErrorIs(t, ValidateProtectedPlatformNativeBillingState(), ErrProtectedPlatformNativeBillingState)
	require.NoError(t, model.DB.Model(fixture.Task).Update("quota", 0).Error)
	require.NoError(t, model.DB.Model(fixture.Job).Update("native_billing_reconciliation_needed", true).Error)
	require.ErrorIs(t, ValidateProtectedPlatformNativeBillingState(), ErrProtectedPlatformNativeBillingState)
}

func TestProtectedNativeBillingAttestationRejectsDuplicateAndCredentialDrift(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(t *testing.T, fixture protectedNativeBindingFixture)
	}{
		{
			name: "duplicate task id",
			mutate: func(t *testing.T, fixture protectedNativeBindingFixture) {
				duplicate := *fixture.Task
				duplicate.ID = 0
				require.NoError(t, model.DB.Create(&duplicate).Error)
			},
		},
		{
			name: "empty credential version",
			mutate: func(t *testing.T, fixture protectedNativeBindingFixture) {
				privateData := fixture.Task.PrivateData
				privateData.ProviderCredentialVersion = ""
				require.NoError(t, model.DB.Model(fixture.Task).Update("private_data", privateData).Error)
			},
		},
		{
			name: "credential tenant mismatch",
			mutate: func(t *testing.T, fixture protectedNativeBindingFixture) {
				privateData := fixture.Task.PrivateData
				privateData.ProviderCredentialTenantID = uuid.NewString()
				require.NoError(t, model.DB.Model(fixture.Task).Update("private_data", privateData).Error)
			},
		},
		{
			name: "pinned index mismatch",
			mutate: func(t *testing.T, fixture protectedNativeBindingFixture) {
				privateData := fixture.Task.PrivateData
				index := *privateData.PinnedKeyIndex + 1
				privateData.PinnedKeyIndex = &index
				require.NoError(t, model.DB.Model(fixture.Task).Update("private_data", privateData).Error)
			},
		},
		{
			name: "pinned fingerprint mismatch",
			mutate: func(t *testing.T, fixture protectedNativeBindingFixture) {
				privateData := fixture.Task.PrivateData
				privateData.PinnedKeyFingerprint = strings.Repeat("e", 64)
				require.NoError(t, model.DB.Model(fixture.Task).Update("private_data", privateData).Error)
			},
		},
		{
			name: "task platform mismatch",
			mutate: func(t *testing.T, fixture protectedNativeBindingFixture) {
				require.NoError(t, model.DB.Model(fixture.Task).Update("platform", "suno").Error)
			},
		},
		{
			name: "task user mismatch",
			mutate: func(t *testing.T, fixture protectedNativeBindingFixture) {
				require.NoError(t, model.DB.Model(fixture.Task).Update("user_id", fixture.Task.UserId+1).Error)
			},
		},
		{
			name: "task group mismatch",
			mutate: func(t *testing.T, fixture protectedNativeBindingFixture) {
				require.NoError(t, model.DB.Model(fixture.Task).Update("group", "tampered").Error)
			},
		},
		{
			name: "task action mismatch",
			mutate: func(t *testing.T, fixture protectedNativeBindingFixture) {
				require.NoError(t, model.DB.Model(fixture.Task).Update("action", "tampered").Error)
			},
		},
		{
			name: "task submit time mismatch",
			mutate: func(t *testing.T, fixture protectedNativeBindingFixture) {
				require.NoError(t, model.DB.Model(fixture.Task).Update("submit_time", fixture.Task.SubmitTime+1).Error)
			},
		},
		{
			name: "task properties mismatch",
			mutate: func(t *testing.T, fixture protectedNativeBindingFixture) {
				require.NoError(t, model.DB.Model(fixture.Task).Update("properties", model.Properties{OriginModelName: "tampered"}).Error)
			},
		},
		{
			name: "sticky upstream mismatch",
			mutate: func(t *testing.T, fixture protectedNativeBindingFixture) {
				privateData := fixture.Task.PrivateData
				privateData.UpstreamTaskID = "different-upstream"
				require.NoError(t, model.DB.Model(fixture.Task).Update("private_data", privateData).Error)
			},
		},
		{
			name: "route mismatch",
			mutate: func(t *testing.T, fixture protectedNativeBindingFixture) {
				require.NoError(t, model.DB.Model(fixture.Job).Update("provider_route_id", fixture.Route.ID+999).Error)
			},
		},
		{
			name: "credential row missing",
			mutate: func(t *testing.T, fixture protectedNativeBindingFixture) {
				require.NoError(t, model.DB.Delete(fixture.Credential).Error)
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			truncate(t)
			enableProtectedNativeBillingTest(t)
			fixture := seedProtectedExternalNativeBinding(
				t,
				model.PlatformGenerationStatusProcessing,
				model.TaskStatusInProgress,
				true,
			)
			test.mutate(t, fixture)
			require.ErrorIs(t, ValidateProtectedPlatformNativeBillingState(), ErrProtectedPlatformNativeBillingState)
		})
	}
}

func TestProtectedNativeBillingAttestationValidatesOrphanRecoveryAndStagedSubmission(t *testing.T) {
	t.Run("explicit reconciliation may await task recovery", func(t *testing.T) {
		truncate(t)
		enableProtectedNativeBillingTest(t)
		seedProtectedExternalNativeBinding(
			t,
			model.PlatformGenerationStatusReconciliationRequired,
			model.TaskStatusSubmitted,
			false,
		)
		require.NoError(t, ValidateProtectedPlatformNativeBillingState())
	})
	t.Run("fenced staged submission may await provider response", func(t *testing.T) {
		truncate(t)
		enableProtectedNativeBillingTest(t)
		seedProtectedExternalNativeBinding(
			t,
			model.PlatformGenerationStatusSubmitting,
			model.TaskStatusSubmitted,
			false,
		)
		require.NoError(t, ValidateProtectedPlatformNativeBillingState())
	})
	t.Run("old recovery schema is never reinterpreted", func(t *testing.T) {
		truncate(t)
		enableProtectedNativeBillingTest(t)
		fixture := seedProtectedExternalNativeBinding(
			t,
			model.PlatformGenerationStatusReconciliationRequired,
			model.TaskStatusSubmitted,
			false,
		)
		var recovery map[string]any
		require.NoError(t, json.Unmarshal([]byte(fixture.Job.NativeTaskRecoveryJSON), &recovery))
		recovery["schema_version"] = 2
		raw, err := json.Marshal(recovery)
		require.NoError(t, err)
		require.NoError(t, model.DB.Model(fixture.Job).Update("native_task_recovery_json", string(raw)).Error)
		require.ErrorIs(t, ValidateProtectedPlatformNativeBillingState(), ErrProtectedPlatformNativeBillingState)
	})
	t.Run("artifact transfer retains exact finished route evidence", func(t *testing.T) {
		truncate(t)
		enableProtectedNativeBillingTest(t)
		seedProtectedExternalNativeBinding(
			t,
			model.PlatformGenerationStatusTransferring,
			model.TaskStatusSuccess,
			true,
		)
		require.NoError(t, ValidateProtectedPlatformNativeBillingState())
	})
}

func TestProtectedTaskPollingRefusesLegacyUnfinishedTasksBeforeAdaptorLookup(t *testing.T) {
	truncate(t)
	enableProtectedNativeBillingTest(t)
	legacy := &model.Task{
		TaskID:    "legacy-native-paid-task",
		Status:    model.TaskStatusUnknown,
		Progress:  "50%",
		Quota:     500,
		CreatedAt: time.Now().Unix(),
		UpdatedAt: time.Now().Unix(),
	}
	require.NoError(t, model.DB.Create(legacy).Error)
	called := false
	previousFactory := GetTaskAdaptorFunc
	GetTaskAdaptorFunc = func(constant.TaskPlatform) TaskPollingAdaptor {
		called = true
		return nil
	}
	t.Cleanup(func() { GetTaskAdaptorFunc = previousFactory })

	summary := RunTaskPollingOnce(context.Background(), nil)
	require.False(t, called)
	require.Zero(t, summary.UnfinishedTasks)
	var persisted model.Task
	require.NoError(t, model.DB.First(&persisted, legacy.ID).Error)
	require.Equal(t, 500, persisted.Quota)
	require.Equal(t, model.TaskStatus(model.TaskStatusUnknown), persisted.Status)
}

func TestProtectedMissingNativeTaskTransitionIsAtomic(t *testing.T) {
	t.Run("processing route and job move to reconciliation together", func(t *testing.T) {
		truncate(t)
		enableProtectedNativeBillingTest(t)
		fixture := seedProtectedExternalNativeBinding(
			t,
			model.PlatformGenerationStatusProcessing,
			model.TaskStatusSubmitted,
			false,
		)
		pollToken := uuid.NewString()
		require.NoError(t, model.DB.Model(fixture.Job).Updates(map[string]any{
			"poll_lease_token":      pollToken,
			"poll_lease_expires_at": time.Now().UTC().Add(time.Minute),
		}).Error)
		require.NoError(t, model.DB.First(fixture.Job, "id = ?", fixture.Job.ID).Error)
		task, err := loadProtectedPlatformNativeTaskForJob(*fixture.Job, true)
		require.NoError(t, err)
		require.Nil(t, task)
		won, err := model.CompletePlatformGenerationMissingNativeTask(fixture.Job.ID, pollToken)
		require.NoError(t, err)
		require.True(t, won)
		var persistedJob model.PlatformGenerationJob
		require.NoError(t, model.DB.First(&persistedJob, "id = ?", fixture.Job.ID).Error)
		require.Equal(t, model.PlatformGenerationStatusReconciliationRequired, persistedJob.Status)
		var persistedAdmission model.PlatformGenerationRouteAdmission
		require.NoError(t, model.DB.First(&persistedAdmission, "job_id = ?", fixture.Job.ID).Error)
		require.Equal(t, model.PlatformGenerationRouteAdmissionUnknown, persistedAdmission.State)
		require.True(t, persistedAdmission.SlotHeld)
		require.NoError(t, ValidateProtectedPlatformNativeBillingState())
	})
	t.Run("stale poll lease changes neither row", func(t *testing.T) {
		truncate(t)
		enableProtectedNativeBillingTest(t)
		fixture := seedProtectedExternalNativeBinding(
			t,
			model.PlatformGenerationStatusProcessing,
			model.TaskStatusSubmitted,
			false,
		)
		pollToken := uuid.NewString()
		require.NoError(t, model.DB.Model(fixture.Job).Updates(map[string]any{
			"poll_lease_token":      pollToken,
			"poll_lease_expires_at": time.Now().UTC().Add(time.Minute),
		}).Error)
		_, err := model.CompletePlatformGenerationMissingNativeTask(fixture.Job.ID, uuid.NewString())
		require.Error(t, err)
		var persistedJob model.PlatformGenerationJob
		require.NoError(t, model.DB.First(&persistedJob, "id = ?", fixture.Job.ID).Error)
		require.Equal(t, model.PlatformGenerationStatusProcessing, persistedJob.Status)
		var persistedAdmission model.PlatformGenerationRouteAdmission
		require.NoError(t, model.DB.First(&persistedAdmission, "job_id = ?", fixture.Job.ID).Error)
		require.Equal(t, model.PlatformGenerationRouteAdmissionPosting, persistedAdmission.State)
		require.True(t, persistedAdmission.SlotHeld)
	})
}
