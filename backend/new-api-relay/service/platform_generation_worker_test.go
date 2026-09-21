package service

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

type platformGenerationInputRoundTripFunc func(*http.Request) (*http.Response, error)

func (function platformGenerationInputRoundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

type fencingPlatformArtifactStore struct {
	jobID            string
	putObjectKey     string
	deletedObjectKey string
}

type panickingPlatformArtifactStore struct{}

func configureLegacyProfilelessTransferTest(t *testing.T, tenantID string) {
	t.Helper()
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", fmt.Sprintf(
		`{"platform":{"tenant_id":%q,"api_key":"relay-worker-test-secret","upstream_token":"native-worker-test-token"}}`,
		tenantID,
	))
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", `{}`)
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", "")
}

func seedClaimedProtectedSeedanceTransfer(
	t *testing.T,
	sourceURL string,
) (model.PlatformGenerationJob, string, protectedNativeBindingFixture) {
	t.Helper()
	require.NoError(t, model.DB.AutoMigrate(model.PlatformProviderMonitorAndCostModels()...))
	fixture := seedProtectedSeedancePollingBinding(t)
	state := &model.PlatformGenerationProviderAccountState{
		ChannelID:        fixture.Route.ChannelID,
		KeyIndex:         fixture.Route.KeyIndex,
		KeyFingerprint:   fixture.Route.KeyFingerprint,
		RPMWindowSeconds: fixture.Route.RPMWindowSeconds,
		RPMLimit:         fixture.Route.RPMLimit,
		ActiveCount:      1,
		ActiveLimit:      fixture.Route.ActiveLimit,
	}
	require.NoError(t, model.DB.Create(state).Error)
	require.NoError(t, model.DB.Model(fixture.Route).Updates(map[string]any{
		"account_state_id": state.ID,
		"active_count":     1,
	}).Error)
	require.NoError(t, model.DB.Model(fixture.Job).Updates(map[string]any{
		"outputs_json":       "[]",
		"error_details_json": "{}",
	}).Error)

	upstreamTaskID := fixture.Task.PrivateData.UpstreamTaskID
	body := []byte(`{"id":"` + upstreamTaskID + `","model":"` + fixture.Route.UpstreamModel + `","status":"succeeded","resolution":"720p","duration":"5","ratio":"16:9","content":{"video_url":"` + sourceURL + `"}}`)
	adaptor := &protectedTaskPollingAdaptor{
		body: body,
		result: &relaycommon.TaskInfo{
			TaskID:              upstreamTaskID,
			Status:              string(model.TaskStatusSuccess),
			Progress:            "100%",
			Url:                 sourceURL,
			ProviderResultProof: seedanceTerminalProof(upstreamTaskID, fixture.Route.UpstreamModel, "succeeded"),
		},
	}
	require.NoError(t, updateVideoSingleTask(
		context.Background(),
		adaptor,
		&model.Channel{Id: fixture.Task.ChannelId, Key: "provider-key"},
		upstreamTaskID,
		map[string]*model.Task{upstreamTaskID: fixture.Task},
	))

	processed, err := RunPlatformGenerationPollOnce(context.Background())
	require.NoError(t, err)
	require.True(t, processed)
	claimed, token, err := model.ClaimPlatformGenerationTransfer(time.Minute)
	require.NoError(t, err)
	require.Equal(t, fixture.Job.ID, claimed.ID)
	return *claimed, token, fixture
}

func (*panickingPlatformArtifactStore) Kind() string      { return "panic_test" }
func (*panickingPlatformArtifactStore) BindingID() string { return strings.Repeat("f", 64) }
func (*panickingPlatformArtifactStore) Persistent() bool  { return true }

func (*panickingPlatformArtifactStore) Put(
	context.Context,
	PlatformArtifactPutInput,
) (PlatformStoredArtifact, error) {
	panic("artifact store panic")
}

func (*panickingPlatformArtifactStore) Delete(context.Context, string) error { return nil }

func (*panickingPlatformArtifactStore) IssueSignedDownload(
	context.Context,
	string,
	time.Duration,
) (PlatformIssuedArtifactDownload, error) {
	return PlatformIssuedArtifactDownload{}, nil
}

func (*panickingPlatformArtifactStore) Healthcheck(context.Context) error { return nil }

func (store *fencingPlatformArtifactStore) Kind() string      { return "fencing_test" }
func (store *fencingPlatformArtifactStore) BindingID() string { return strings.Repeat("e", 64) }
func (store *fencingPlatformArtifactStore) Persistent() bool  { return true }

func (store *fencingPlatformArtifactStore) Put(
	ctx context.Context,
	input PlatformArtifactPutInput,
) (PlatformStoredArtifact, error) {
	if _, err := io.Copy(io.Discard, input.Content); err != nil {
		return PlatformStoredArtifact{}, err
	}
	if err := ctx.Err(); err != nil {
		return PlatformStoredArtifact{}, err
	}
	store.putObjectKey = input.ObjectKey
	if err := model.DB.Model(&model.PlatformGenerationJob{}).Where("id = ?", store.jobID).Updates(map[string]any{
		"transfer_lease_token":      uuid.NewString(),
		"transfer_lease_expires_at": time.Now().UTC().Add(time.Minute),
	}).Error; err != nil {
		return PlatformStoredArtifact{}, err
	}
	return PlatformStoredArtifact{
		ObjectKey:   input.ObjectKey,
		ContentType: input.ContentType,
		SizeBytes:   input.SizeBytes,
		SHA256:      input.SHA256,
	}, nil
}

func (store *fencingPlatformArtifactStore) Delete(ctx context.Context, objectKey string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	store.deletedObjectKey = objectKey
	return nil
}

func (*fencingPlatformArtifactStore) IssueSignedDownload(
	context.Context,
	string,
	time.Duration,
) (PlatformIssuedArtifactDownload, error) {
	return PlatformIssuedArtifactDownload{}, nil
}

func (*fencingPlatformArtifactStore) Healthcheck(context.Context) error { return nil }

func configurePlatformGenerationWorkerTest(t *testing.T, nativeURL string, modelID string, channelID int, key string) dto.PlatformModelResource {
	t.Helper()
	truncate(t)
	fingerprint := fmt.Sprintf("%x", common.Sha256Raw([]byte(key)))
	t.Setenv("RELAY_COMPAT_ENABLED", "true")
	t.Setenv("RELAY_COMPAT_WORKER_ENABLED", "true")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("RELAY_COMPAT_INTERNAL_BASE_URL", nativeURL)
	t.Setenv("RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN", "internal-admission-test-token")
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", `{
		"platform": {
			"tenant_id": "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30",
			"api_key": "relay-secret",
			"upstream_token": "native-user-token"
		}
	}`)
	routes := fmt.Sprintf(`{
		%q: [{
			"route_id": "route-test-1",
			"provider_name": "fault-provider",
			"account_id": "fault-account",
			"channel_id": %d,
			"key_index": 0,
			"key_fingerprint": %q,
			"channel_class": "official",
			"upstream_model": "upstream-video-model",
			"production_ready": true,
			"rpm_limit": 10,
			"active_task_limit": 2,
			"capabilities": {
				"schema_version": 1,
				"modes": {
					"text_to_video": {
						"input_media_types": [],
						"supports_face": false,
						"required_resource_keys": [],
						"limits": {
							"max_prompt_length": 1000,
							"max_images": 0,
							"max_videos": 0,
							"max_audio": 0,
							"duration_seconds": [5],
							"aspect_ratios": ["16:9"],
							"resolutions": ["720p"],
							"output_counts": [1]
						}
					}
				}
			}
		}]
	}`, modelID, channelID, fingerprint)
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", routes)
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", "")

	channel := model.Channel{
		Id:          channelID,
		Type:        constant.ChannelTypeKling,
		Key:         key,
		Status:      common.ChannelStatusEnabled,
		Name:        "fault-channel",
		CreatedTime: 1,
		Models:      modelID,
		Group:       "default",
	}
	require.NoError(t, model.DB.Create(&channel).Error)
	require.NoError(t, SyncPlatformGenerationProviderRoutes())
	resource, ok, err := GetPlatformRelayModel(modelID)
	require.NoError(t, err)
	require.True(t, ok)
	return resource
}

func submitPlatformGenerationWorkerFixture(t *testing.T, modelID string, revision string) string {
	t.Helper()
	request := dto.NewPlatformGenerationRequest()
	clientReferenceID := "platform-task-fault"
	request.ClientReferenceID = &clientReferenceID
	request.Model = modelID
	request.Mode = "text_to_video"
	request.ExpectedCapabilityRevision = revision
	request.Inputs.Prompt = "generate a safe test scene"
	accepted, err := SubmitPlatformGeneration(PlatformRelayPrincipal{
		ClientID:      "platform",
		TenantID:      "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30",
		UpstreamToken: "native-user-token",
	}, request, "fault-key-12345", "fault-request-id")
	require.NoError(t, err)
	return accepted.ID
}

func TestPlatformGenerationSubmissionTransportFailureBecomesUnknownWithoutFailover(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hijacker, ok := w.(http.Hijacker)
		require.True(t, ok)
		connection, _, err := hijacker.Hijack()
		require.NoError(t, err)
		require.NoError(t, connection.Close())
	}))
	defer server.Close()

	modelID := "fault-video-model"
	resource := configurePlatformGenerationWorkerTest(t, server.URL, modelID, 9101, "provider-key-one")
	jobID := submitPlatformGenerationWorkerFixture(t, modelID, resource.CapabilityRevision)

	processed, err := RunPlatformGenerationSubmissionOnce(context.Background())
	require.NoError(t, err)
	assert.True(t, processed)

	job, err := model.GetPlatformGenerationJob(jobID, "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30")
	require.NoError(t, err)
	assert.Equal(t, model.PlatformGenerationStatusReconciliationRequired, job.Status)
	assert.Equal(t, "SUBMISSION_RECONCILIATION_REQUIRED", job.ErrorCode)
	assert.Equal(t, 1, job.ProviderSubmissionAttempt)
	assert.NotEmpty(t, job.NativeTaskID)

	admission, route, err := model.GetPlatformGenerationProviderRouteAssignment(jobID)
	require.NoError(t, err)
	assert.Equal(t, model.PlatformGenerationRouteAdmissionUnknown, admission.State)
	assert.True(t, admission.SlotHeld)
	assert.Equal(t, 1, route.ActiveCount)

	processed, err = RunPlatformGenerationSubmissionOnce(context.Background())
	assert.False(t, processed)
	assert.ErrorIs(t, err, gorm.ErrRecordNotFound, "unknown submissions must not be placed back on the submit queue")
}

func TestPlatformGenerationSubmissionSuccessfulNativeAckWithoutTaskRowBecomesUnknown(t *testing.T) {
	var providerPosts atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		providerPosts.Add(1)
		require.Equal(t, http.MethodPost, request.Method)
		require.Equal(t, "/internal/platform-generations/native-submit", request.URL.Path)
		require.NotEmpty(t, request.Header.Get(constant.HeaderPlatformGenerationJobID))
		// This is the observable native boundary after the provider request has
		// succeeded but the controller's Task.Insert has failed: the native HTTP
		// acknowledgement survives, the provider-started receipt is present, and
		// no native Task row can be found by Platform's mandatory post-query.
		w.Header().Set(constant.HeaderPlatformGenerationProviderStarted, "true")
		w.WriteHeader(http.StatusOK)
		_, err := w.Write([]byte(`{"status":"accepted"}`))
		require.NoError(t, err)
	}))
	defer server.Close()

	modelID := "native-ack-missing-task-model"
	resource := configurePlatformGenerationWorkerTest(t, server.URL, modelID, 9110, "synthetic-provider-key")
	jobID := submitPlatformGenerationWorkerFixture(t, modelID, resource.CapabilityRevision)

	processed, err := RunPlatformGenerationSubmissionOnce(context.Background())
	require.NoError(t, err)
	require.True(t, processed)
	require.EqualValues(t, 1, providerPosts.Load())

	job, err := model.GetPlatformGenerationJob(jobID, "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30")
	require.NoError(t, err)
	require.Equal(t, model.PlatformGenerationStatusReconciliationRequired, job.Status)
	require.Equal(t, model.PlatformGenerationErrorSubmissionReconciliationRequired, job.ErrorCode)
	require.NotEmpty(t, job.NativeTaskID)
	_, err = model.GetPlatformGenerationNativeTask(job.NativeTaskID, job.ProviderChannelID)
	require.ErrorIs(t, err, gorm.ErrRecordNotFound)

	admission, route, err := model.GetPlatformGenerationProviderRouteAssignment(jobID)
	require.NoError(t, err)
	require.Equal(t, model.PlatformGenerationRouteAdmissionUnknown, admission.State)
	require.True(t, admission.SlotHeld)
	require.Equal(t, 1, route.ActiveCount)

	processed, err = RunPlatformGenerationSubmissionOnce(context.Background())
	require.False(t, processed)
	require.ErrorIs(t, err, gorm.ErrRecordNotFound)
	require.EqualValues(t, 1, providerPosts.Load(), "a missing native Task row must never trigger a second provider POST")
}

func TestSeedreamUnknownSubmissionCannotBeRebuiltAsAsyncProviderTask(t *testing.T) {
	var nativeRequests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		nativeRequests.Add(1)
		hijacker, ok := w.(http.Hijacker)
		require.True(t, ok)
		connection, _, err := hijacker.Hijack()
		require.NoError(t, err)
		require.NoError(t, connection.Close())
	}))
	defer server.Close()

	modelID := "unknown-seedream-mode"
	resource := configurePlatformGenerationWorkerTest(t, server.URL, modelID, 9109, "synthetic-provider-key")
	require.NoError(t, model.DB.AutoMigrate(&model.PlatformGenerationReconciliationEvent{}))
	require.NoError(t, model.DB.Exec("DELETE FROM platform_generation_reconciliation_events").Error)
	t.Cleanup(func() {
		require.NoError(t, model.DB.Exec("DELETE FROM platform_generation_reconciliation_events").Error)
	})
	jobID := submitPlatformGenerationWorkerFixture(t, modelID, resource.CapabilityRevision)
	processed, err := RunPlatformGenerationSubmissionOnce(context.Background())
	require.NoError(t, err)
	require.True(t, processed)
	require.EqualValues(t, 1, nativeRequests.Load())

	job, err := model.GetPlatformGenerationJob(jobID, "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30")
	require.NoError(t, err)
	var accepted dto.PlatformGenerationRequest
	require.NoError(t, common.Unmarshal([]byte(job.RequestJSON), &accepted))
	accepted.Mode = "text_to_image"
	accepted.Output.DurationSeconds = 1
	accepted.Output.AspectRatio = "1:1"
	accepted.Output.Resolution = constant.PlatformGenerationArkSeedream50LiteSize
	acceptedJSON, err := common.Marshal(accepted)
	require.NoError(t, err)
	require.NoError(t, model.DB.Model(&model.PlatformGenerationJob{}).Where("id = ?", jobID).Updates(map[string]any{
		"mode":         "text_to_image",
		"request_json": string(acceptedJSON),
	}).Error)

	candidate, err := model.GetPlatformGenerationSubmissionUnknown(jobID, job.TenantID)
	require.NoError(t, err)
	_, _, _, err = ResolvePlatformGenerationUnknownSubmission(job.TenantID, jobID, dto.PlatformGenerationReconciliationRequest{
		Outcome:                     "created",
		UpstreamTaskID:              "seedream:manual-provider-console-id",
		ExpectedRouteID:             candidate.Route.ID,
		ExpectedSubmissionAttempt:   candidate.Admission.Attempt,
		ExpectedReconciliationToken: candidate.ReconciliationToken,
	}, "seedream-created-resolution-must-fail")
	require.ErrorIs(t, err, model.ErrPlatformGenerationReconciliationConflict)

	unchanged, err := model.GetPlatformGenerationJob(jobID, job.TenantID)
	require.NoError(t, err)
	require.Equal(t, model.PlatformGenerationStatusReconciliationRequired, unchanged.Status)
	require.Empty(t, unchanged.UpstreamTaskID)
	admission, route, err := model.GetPlatformGenerationProviderRouteAssignment(jobID)
	require.NoError(t, err)
	require.Equal(t, model.PlatformGenerationRouteAdmissionUnknown, admission.State)
	require.True(t, admission.SlotHeld)
	require.Equal(t, 1, route.ActiveCount)

	// Neither manual resolution nor the ordinary submission worker can turn the
	// synchronous image into a polling task or issue a second provider POST.
	processed, err = RunPlatformGenerationSubmissionOnce(context.Background())
	require.False(t, processed)
	require.ErrorIs(t, err, gorm.ErrRecordNotFound)
	require.EqualValues(t, 1, nativeRequests.Load())

	snapshot, result, replayed, err := ResolvePlatformGenerationUnknownSubmission(job.TenantID, jobID, dto.PlatformGenerationReconciliationRequest{
		OperationID:                 "seedream-not-created-resolution",
		Outcome:                     "not_created",
		ExpectedRouteID:             candidate.Route.ID,
		ExpectedSubmissionAttempt:   candidate.Admission.Attempt,
		ExpectedReconciliationToken: candidate.ReconciliationToken,
		VerificationReference:       "provider-console-no-image-task",
		ApprovedBy:                  "platform-admin-test",
		ApprovalReason:              "Provider console proves that no image task was created",
		ApprovalKeyID:               "platform-approval-v1",
		ApprovalSignature:           "hmac-sha256:" + strings.Repeat("a", 64),
	}, "seedream-not-created-request")
	require.NoError(t, err)
	require.False(t, replayed)
	require.Equal(t, model.PlatformGenerationStatusFailed, snapshot.Status)
	require.Equal(t, "not_created", result.Outcome)
	require.EqualValues(t, 1, nativeRequests.Load())
}

func TestValidatePlatformGenerationCreatedReconciliationMode(t *testing.T) {
	videoRequest := dto.NewPlatformGenerationRequest()
	videoRequest.Mode = "text_to_video"
	videoJSON, err := common.Marshal(videoRequest)
	require.NoError(t, err)
	_, err = validatePlatformGenerationCreatedReconciliation(
		model.PlatformGenerationReconciliationCandidate{Job: model.PlatformGenerationJob{
			Mode:        "text_to_video",
			RequestJSON: string(videoJSON),
		}},
		dto.PlatformGenerationReconciliationRequest{},
	)
	require.NoError(t, err)

	imageRequest := dto.NewPlatformGenerationRequest()
	imageRequest.Mode = "text_to_image"
	imageJSON, err := common.Marshal(imageRequest)
	require.NoError(t, err)
	_, err = validatePlatformGenerationCreatedReconciliation(
		model.PlatformGenerationReconciliationCandidate{Job: model.PlatformGenerationJob{
			Mode:        "text_to_image",
			RequestJSON: string(imageJSON),
		}},
		dto.PlatformGenerationReconciliationRequest{},
	)
	require.ErrorIs(t, err, model.ErrPlatformGenerationReconciliationConflict)

	_, err = validatePlatformGenerationCreatedReconciliation(
		model.PlatformGenerationReconciliationCandidate{Job: model.PlatformGenerationJob{
			Mode:        "text_to_image",
			RequestJSON: string(videoJSON),
		}},
		dto.PlatformGenerationReconciliationRequest{},
	)
	require.ErrorIs(t, err, model.ErrPlatformGenerationReconciliationConflict)
}

func TestSeedreamTerminalTaskRecoversLostAcknowledgementWithoutProviderPollOrResubmit(t *testing.T) {
	truncate(t)
	require.NoError(t, model.DB.AutoMigrate(&model.PlatformProviderTerminalOutcome{}))
	require.NoError(t, model.DB.Exec("DELETE FROM platform_provider_terminal_outcomes").Error)
	t.Cleanup(func() {
		require.NoError(t, model.DB.Exec("DELETE FROM platform_provider_terminal_outcomes").Error)
	})
	enableProtectedNativeBillingTest(t)
	fixture := seedProtectedExternalNativeBinding(
		t,
		model.PlatformGenerationStatusReconciliationRequired,
		model.TaskStatusSuccess,
		true,
	)

	accountState := &model.PlatformGenerationProviderAccountState{
		ChannelID:        fixture.Route.ChannelID,
		KeyIndex:         fixture.Route.KeyIndex,
		KeyFingerprint:   fixture.Route.KeyFingerprint,
		RPMWindowSeconds: fixture.Route.RPMWindowSeconds,
		RPMLimit:         fixture.Route.RPMLimit,
		ActiveCount:      1,
		ActiveLimit:      fixture.Route.ActiveLimit,
	}
	require.NoError(t, model.DB.Create(accountState).Error)
	require.NoError(t, model.DB.Model(fixture.Route).Updates(map[string]any{
		"model":                 "image.seedream.5-lite",
		"mode":                  "text_to_image",
		"accepted_channel_type": constant.ChannelTypeVolcEngine,
		"upstream_model":        constant.PlatformGenerationArkSeedream50LiteModel,
		"account_state_id":      accountState.ID,
		"active_count":          1,
	}).Error)

	accepted := dto.NewPlatformGenerationRequest()
	accepted.Model = "image.seedream.5-lite"
	accepted.Mode = "text_to_image"
	accepted.Output.DurationSeconds = 1
	accepted.Output.AspectRatio = "1:1"
	accepted.Output.Resolution = constant.PlatformGenerationArkSeedream50LiteSize
	accepted.Output.Count = 1
	acceptedJSON, err := common.Marshal(accepted)
	require.NoError(t, err)
	require.NoError(t, model.DB.Model(fixture.Job).Updates(map[string]any{
		"model":              accepted.Model,
		"mode":               accepted.Mode,
		"request_json":       string(acceptedJSON),
		"error_code":         model.PlatformGenerationErrorProviderPollReconciliationRequired,
		"error_details_json": model.PlatformGenerationProviderReconciliationDetailsJSON(model.PlatformGenerationProviderReconciliationKindMissingNativeTask),
	}).Error)
	privateData := fixture.Task.PrivateData
	privateData.ResultURL = "https://provider.example/seedream-result.png"
	require.NoError(t, model.DB.Model(fixture.Task).Updates(map[string]any{
		"private_data": privateData,
		"data":         []byte(`{"id":"seedream:terminal-receipt","status":"succeeded"}`),
		"finish_time":  time.Now().Unix(),
	}).Error)

	adaptorLookups := atomic.Int32{}
	previousFactory := GetTaskAdaptorFunc
	GetTaskAdaptorFunc = func(constant.TaskPlatform) TaskPollingAdaptor {
		adaptorLookups.Add(1)
		return nil
	}
	t.Cleanup(func() { GetTaskAdaptorFunc = previousFactory })

	processed, err := RunPlatformGenerationReconciliationOnce(context.Background())
	require.NoError(t, err)
	require.True(t, processed)
	require.Zero(t, adaptorLookups.Load(), "a persisted synchronous terminal task must never enter FetchTask")

	persisted, err := model.GetPlatformGenerationJob(fixture.Job.ID, fixture.Job.TenantID)
	require.NoError(t, err)
	require.Equal(t, model.PlatformGenerationStatusTransferring, persisted.Status)
	require.Equal(t, 95, persisted.Progress)
	require.Contains(t, persisted.TemporaryResultJSON, "https://provider.example/seedream-result.png")
	require.Empty(t, persisted.UpstreamTaskID, "a lost acknowledgement must not be reconstructed into an async job field")
	var outcome model.PlatformProviderTerminalOutcome
	require.NoError(t, model.DB.Where("relay_job_id = ?", fixture.Job.ID).First(&outcome).Error)
	require.Equal(t, model.PlatformProviderOutcomeSucceeded, outcome.Outcome)
	require.Equal(t, "provider-task:"+fixture.Task.PrivateData.UpstreamTaskID, outcome.ExternalReference)
	admission, route, err := model.GetPlatformGenerationProviderRouteAssignment(fixture.Job.ID)
	require.NoError(t, err)
	require.Equal(t, model.PlatformGenerationRouteAdmissionFinished, admission.State)
	require.False(t, admission.SlotHeld)
	require.Zero(t, route.ActiveCount)
}

func TestPlatformGenerationSubmissionRepairsTerminalJobOutboxWithoutPanicking(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		t.Fatal("a terminal job's stale submission outbox must never reach the provider")
	}))
	defer server.Close()

	modelID := "terminal-outbox-repair-model"
	resource := configurePlatformGenerationWorkerTest(t, server.URL, modelID, 9104, "provider-key-terminal-repair")
	jobID := submitPlatformGenerationWorkerFixture(t, modelID, resource.CapabilityRevision)
	require.NoError(t, model.DB.Model(&model.PlatformGenerationJob{}).Where("id = ?", jobID).Updates(map[string]any{
		"status":        model.PlatformGenerationStatusFailed,
		"error_code":    model.PlatformGenerationErrorGenerationFailed,
		"error_message": "terminal before stale outbox repair",
	}).Error)

	processed, err := RunPlatformGenerationSubmissionOnce(context.Background())
	assert.False(t, processed)
	assert.ErrorIs(t, err, gorm.ErrRecordNotFound)

	var repaired model.PlatformGenerationOutbox
	require.NoError(t, model.DB.Where("job_id = ?", jobID).First(&repaired).Error)
	assert.Equal(t, model.PlatformGenerationOutboxCompleted, repaired.State)
	assert.Empty(t, repaired.ClaimToken)
}

func TestPlatformGenerationSubmissionLeaseKeeperCancelsHTTPAfterFencing(t *testing.T) {
	requestStarted := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(_ http.ResponseWriter, request *http.Request) {
		close(requestStarted)
		// The client transport is expected to close the connection after its
		// context is cancelled. Avoid coupling this regression to when the
		// server-side request context observes that disconnect.
		select {
		case <-request.Context().Done():
		case <-time.After(3 * time.Second):
		}
	}))
	defer server.Close()

	modelID := "submission-lease-keeper-model"
	resource := configurePlatformGenerationWorkerTest(t, server.URL, modelID, 9105, "provider-key-lease-keeper")
	jobID := submitPlatformGenerationWorkerFixture(t, modelID, resource.CapabilityRevision)
	claim, err := model.ClaimPlatformGenerationSubmission(3 * time.Second)
	require.NoError(t, err)

	submissionContext, stopLease, err := startPlatformGenerationSubmissionLeaseKeeper(
		context.Background(),
		*claim,
		3*time.Second,
		500*time.Millisecond,
	)
	require.NoError(t, err)
	type callResult struct {
		started bool
		status  int
		err     error
	}
	callFinished := make(chan callResult, 1)
	go func() {
		started, status, callErr := submitPlatformNativeTask(
			submissionContext,
			*claim,
			model.PlatformGenerationRouteClaim{Route: model.PlatformGenerationProviderRoute{ID: 1}, SubmissionToken: uuid.NewString()},
			PlatformRelayPrincipal{UpstreamToken: "native-user-token"},
			[]byte(`{"prompt":"lease fence"}`),
		)
		callFinished <- callResult{started: started, status: status, err: callErr}
	}()

	select {
	case <-requestStarted:
	case <-time.After(2 * time.Second):
		t.Fatal("submission HTTP request did not start")
	}
	replacementToken := uuid.NewString()
	require.NoError(t, model.DB.Transaction(func(tx *gorm.DB) error {
		expiresAt := time.Now().UTC().Add(3 * time.Second)
		if err := tx.Model(&model.PlatformGenerationOutbox{}).Where("id = ?", claim.OutboxID).Updates(map[string]any{
			"claim_token": replacementToken, "claim_expires_at": expiresAt,
		}).Error; err != nil {
			return err
		}
		return tx.Model(&model.PlatformGenerationJob{}).Where("id = ?", jobID).Updates(map[string]any{
			"submission_lease_token": replacementToken, "submission_lease_expires_at": expiresAt,
		}).Error
	}))

	select {
	case result := <-callFinished:
		assert.True(t, result.started)
		assert.Zero(t, result.status)
		require.Error(t, result.err)
	case <-time.After(3 * time.Second):
		t.Fatal("fenced submission HTTP request was not cancelled")
	}
	assert.ErrorIs(t, context.Cause(submissionContext), errPlatformGenerationSubmissionLeaseFenced)
	assert.ErrorIs(t, stopLease(), errPlatformGenerationSubmissionLeaseFenced)

	won, err := model.CompletePlatformGenerationSubmission(*claim, map[string]any{
		"status": model.PlatformGenerationStatusProcessing,
	})
	require.NoError(t, err)
	assert.False(t, won, "the fenced worker must not commit a provider result")
}

func TestPlatformGenerationSubmissionPanicStopsLeaseKeeperAndAllowsTakeover(t *testing.T) {
	modelID := "submission-panic-lease-model"
	resource := configurePlatformGenerationWorkerTest(
		t,
		"http://127.0.0.1:1",
		modelID,
		9106,
		"provider-key-submission-panic",
	)
	jobID := submitPlatformGenerationWorkerFixture(t, modelID, resource.CapabilityRevision)
	claim, err := model.ClaimPlatformGenerationSubmission(2 * time.Second)
	require.NoError(t, err)

	assert.Panics(t, func() {
		_, _, _, _ = runPlatformGenerationSubmissionWithLeaseKeeper(
			context.Background(),
			*claim,
			2*time.Second,
			20*time.Millisecond,
			func(context.Context) (bool, int, error) {
				panic("provider boundary panic")
			},
		)
	})

	var stoppedJob model.PlatformGenerationJob
	require.NoError(t, model.DB.First(&stoppedJob, "id = ?", jobID).Error)
	var stoppedOutbox model.PlatformGenerationOutbox
	require.NoError(t, model.DB.First(&stoppedOutbox, "id = ?", claim.OutboxID).Error)
	time.Sleep(120 * time.Millisecond)
	var laterJob model.PlatformGenerationJob
	require.NoError(t, model.DB.First(&laterJob, "id = ?", jobID).Error)
	var laterOutbox model.PlatformGenerationOutbox
	require.NoError(t, model.DB.First(&laterOutbox, "id = ?", claim.OutboxID).Error)
	assert.Equal(t, stoppedJob.SubmissionLeaseExpiresAt, laterJob.SubmissionLeaseExpiresAt,
		"a recovered panic must not leave the submission lease renewing")
	assert.Equal(t, stoppedOutbox.ClaimExpiresAt, laterOutbox.ClaimExpiresAt,
		"a recovered panic must not leave the outbox claim renewing")

	past := time.Now().UTC().Add(-time.Second)
	require.NoError(t, model.DB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Model(&model.PlatformGenerationOutbox{}).Where("id = ?", claim.OutboxID).
			Update("claim_expires_at", past).Error; err != nil {
			return err
		}
		return tx.Model(&model.PlatformGenerationJob{}).Where("id = ?", jobID).
			Update("submission_lease_expires_at", past).Error
	}))
	replacement, err := model.ClaimPlatformGenerationSubmission(2*time.Second, claim.OutboxID)
	require.NoError(t, err)
	require.NotNil(t, replacement)
	assert.NotEqual(t, claim.Token, replacement.Token)
}

func TestPlatformGenerationTransferFenceDeletesTokenScopedUnpublishedObject(t *testing.T) {
	truncate(t)
	payload := platformArtifactValidMP4Fixture(t)
	server := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.Header().Set("Content-Type", "video/mp4")
		_, _ = response.Write(payload)
	}))
	t.Cleanup(server.Close)
	downloader, sourceURL, _ := artifactTLSTestDownloader(
		t,
		server,
		PlatformArtifactDownloadConfig{MaxBytes: int64(len(payload)) + 1, Timeout: 5 * time.Second},
		[]net.IPAddr{{IP: net.ParseIP("8.8.8.8")}},
	)
	claimed, token, fixture := seedClaimedProtectedSeedanceTransfer(t, sourceURL)
	store := &fencingPlatformArtifactStore{jobID: claimed.ID}

	err := transferClaimedPlatformGeneration(context.Background(), claimed, token, downloader, store)
	require.ErrorIs(t, err, errPlatformGenerationTransferLeaseFenced)
	assert.NotEmpty(t, store.putObjectKey)
	assert.Empty(t, store.deletedObjectKey, "cleanup must survive the transfer worker process")
	processed, cleanupErr := runPlatformArtifactCleanupOnce(context.Background(), store, 3)
	require.NoError(t, cleanupErr)
	assert.True(t, processed)
	assert.Equal(t, store.putObjectKey, store.deletedObjectKey)
	var persisted model.PlatformGenerationJob
	require.NoError(t, model.DB.First(&persisted, "id = ?", fixture.Job.ID).Error)
	assert.Equal(t, model.PlatformGenerationStatusTransferring, persisted.Status)
	assert.Equal(t, `[]`, persisted.OutputsJSON)
}

func TestPlatformGenerationTransferProofConflictMovesDirectlyToManualReconciliation(t *testing.T) {
	preparePlatformProviderMonitorCostServiceTest(t)
	request := dto.NewPlatformGenerationRequest()
	request.Model = "transfer-proof-conflict-model"
	request.Mode = "text_to_video"
	request.ExpectedCapabilityRevision = "sha256:" + strings.Repeat("a", 64)
	request.Inputs.Prompt = "provider proof conflict fixture"
	request.Output.Count = 1
	requestJSON, err := common.Marshal(request)
	require.NoError(t, err)
	nativeTaskID := "native-transfer-proof-conflict"
	providerURL := "https://provider.example/temporary.mp4?signature=retained-for-reconciliation"
	// This is a syntactically valid legacy manifest, but it deliberately lacks
	// the durable proof contract/digests required by asynchronous Platform video.
	temporaryJSON, err := common.Marshal(platformNativeTemporaryResult{
		NativeTaskID: nativeTaskID,
		ResultURL:    providerURL,
	})
	require.NoError(t, err)
	job := model.PlatformGenerationJob{
		ID:                         uuid.NewString(),
		TenantID:                   uuid.NewString(),
		SourceClientID:             "platform",
		RequestID:                  "transfer-proof-conflict-request",
		IdempotencyKey:             "transfer-proof-conflict-idempotency",
		RequestHash:                strings.Repeat("b", 64),
		RequestJSON:                string(requestJSON),
		Model:                      request.Model,
		Mode:                       request.Mode,
		ExpectedCapabilityRevision: request.ExpectedCapabilityRevision,
		CapabilityRevision:         request.ExpectedCapabilityRevision,
		Status:                     model.PlatformGenerationStatusTransferring,
		Progress:                   95,
		NativeTaskID:               nativeTaskID,
		UpstreamResultURL:          providerURL,
		TemporaryResultJSON:        string(temporaryJSON),
		OutputsJSON:                `[]`,
		ErrorDetailsJSON:           `{}`,
		NextTransferAt:             time.Now().UTC().Add(-time.Minute),
	}
	require.NoError(t, model.DB.Create(&job).Error)
	claimed, token, err := model.ClaimPlatformGenerationTransfer(time.Minute)
	require.NoError(t, err)
	require.Equal(t, job.ID, claimed.ID)
	store := &fencingPlatformArtifactStore{jobID: job.ID}

	err = transferClaimedPlatformGeneration(context.Background(), *claimed, token, nil, store)
	require.ErrorIs(t, err, model.ErrPlatformGenerationProviderMaterialReconciliationRequired)
	assert.Empty(t, store.putObjectKey, "proof conflict must stop before download/storage")
	require.NoError(t, retryOrFailPlatformGenerationTransfer(*claimed, token, err))

	var persisted model.PlatformGenerationJob
	require.NoError(t, model.DB.First(&persisted, "id = ?", job.ID).Error)
	assert.Equal(t, model.PlatformGenerationStatusReconciliationRequired, persisted.Status)
	assert.Equal(t, model.PlatformGenerationErrorProviderPollReconciliationRequired, persisted.ErrorCode)
	assert.Equal(t, model.PlatformGenerationProviderReconciliationKindProviderMaterial,
		model.PlatformGenerationProviderReconciliationKind(persisted))
	assert.Empty(t, persisted.TransferLeaseToken)
	assert.Equal(t, providerURL, persisted.UpstreamResultURL)
	assert.Equal(t, string(temporaryJSON), persisted.TemporaryResultJSON)
	assert.Equal(t, 1, persisted.ArtifactTransferAttempts,
		"proof conflicts transition immediately instead of consuming the ordinary retry budget")
	backlog, err := model.CountPlatformGenerationProviderResultReconciliationBacklog()
	require.NoError(t, err)
	assert.Equal(t, int64(1), backlog)
}

func TestPlatformGenerationTransferPanicStopsLeaseAndSchedulesDurableCleanup(t *testing.T) {
	truncate(t)
	payload := platformArtifactValidMP4Fixture(t)
	server := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.Header().Set("Content-Type", "video/mp4")
		_, _ = response.Write(payload)
	}))
	defer server.Close()
	downloader, sourceURL, _ := artifactTLSTestDownloader(
		t,
		server,
		PlatformArtifactDownloadConfig{MaxBytes: int64(len(payload)) + 1, Timeout: 5 * time.Second},
		[]net.IPAddr{{IP: net.ParseIP("8.8.8.8")}},
	)
	claimed, token, fixture := seedClaimedProtectedSeedanceTransfer(t, sourceURL)

	assert.Panics(t, func() {
		_ = transferClaimedPlatformGenerationWithLeasePolicy(
			context.Background(),
			claimed,
			token,
			downloader,
			&panickingPlatformArtifactStore{},
			2*time.Second,
			20*time.Millisecond,
		)
	})

	var intent model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.Where("job_id = ? AND transfer_token = ?", fixture.Job.ID, token).First(&intent).Error)
	assert.Equal(t, model.PlatformArtifactUploadIntentPending, intent.State)
	assert.False(t, intent.AvailableAt.After(time.Now().UTC()),
		"a panic after intent creation must make durable cleanup immediately eligible")

	var stoppedJob model.PlatformGenerationJob
	require.NoError(t, model.DB.First(&stoppedJob, "id = ?", fixture.Job.ID).Error)
	time.Sleep(120 * time.Millisecond)
	var laterJob model.PlatformGenerationJob
	require.NoError(t, model.DB.First(&laterJob, "id = ?", fixture.Job.ID).Error)
	assert.Equal(t, stoppedJob.TransferLeaseExpiresAt, laterJob.TransferLeaseExpiresAt,
		"a recovered panic must not leave the transfer lease renewing")

	require.NoError(t, model.DB.Model(&model.PlatformGenerationJob{}).Where("id = ?", fixture.Job.ID).
		Update("transfer_lease_expires_at", time.Now().UTC().Add(-time.Second)).Error)
	replacement, replacementToken, err := model.ClaimPlatformGenerationTransfer(time.Minute)
	require.NoError(t, err)
	require.NotNil(t, replacement)
	assert.Equal(t, fixture.Job.ID, replacement.ID)
	assert.NotEqual(t, token, replacementToken)
}

func TestPlatformGenerationPreProviderRejectionReleasesRoute(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set(constant.HeaderPlatformGenerationProviderStarted, "false")
		http.Error(w, "rejected before provider", http.StatusConflict)
	}))
	defer server.Close()

	modelID := "preflight-video-model"
	resource := configurePlatformGenerationWorkerTest(t, server.URL, modelID, 9102, "provider-key-two")
	jobID := submitPlatformGenerationWorkerFixture(t, modelID, resource.CapabilityRevision)

	processed, err := RunPlatformGenerationSubmissionOnce(context.Background())
	require.NoError(t, err)
	assert.True(t, processed)
	job, err := model.GetPlatformGenerationJob(jobID, "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30")
	require.NoError(t, err)
	assert.Equal(t, model.PlatformGenerationStatusFailed, job.Status)
	assert.Equal(t, "GENERATION_CHANNEL_UNAVAILABLE", job.ErrorCode)

	admission, route, err := model.GetPlatformGenerationProviderRouteAssignment(jobID)
	require.NoError(t, err)
	assert.Equal(t, model.PlatformGenerationRouteAdmissionReleased, admission.State)
	assert.False(t, admission.SlotHeld)
	assert.Zero(t, route.ActiveCount)
}

func TestPlatformGenerationExpiredSubmissionLeaseConservativelyMarksHeldRouteUnknown(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Fatal("a replacement worker must not submit again while a route is already held")
	}))
	defer server.Close()

	modelID := "expired-lease-video-model"
	resource := configurePlatformGenerationWorkerTest(t, server.URL, modelID, 9103, "provider-key-three")
	jobID := submitPlatformGenerationWorkerFixture(t, modelID, resource.CapabilityRevision)
	firstClaim, err := model.ClaimPlatformGenerationSubmission(platformGenerationSubmissionLease)
	require.NoError(t, err)
	_, err = model.ClaimPlatformGenerationProviderRoute(jobID, modelID, "text_to_video")
	require.NoError(t, err)
	past := time.Now().UTC().Add(-time.Minute)
	require.NoError(t, model.DB.Model(&model.PlatformGenerationJob{}).Where("id = ?", jobID).Update("submission_lease_expires_at", past).Error)
	require.NoError(t, model.DB.Model(&model.PlatformGenerationOutbox{}).Where("id = ?", firstClaim.OutboxID).Update("claim_expires_at", past).Error)

	processed, err := RunPlatformGenerationSubmissionOnce(context.Background())
	require.NoError(t, err)
	assert.True(t, processed)
	job, err := model.GetPlatformGenerationJob(jobID, "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30")
	require.NoError(t, err)
	assert.Equal(t, model.PlatformGenerationStatusReconciliationRequired, job.Status)
	admission, route, err := model.GetPlatformGenerationProviderRouteAssignment(jobID)
	require.NoError(t, err)
	assert.Equal(t, model.PlatformGenerationRouteAdmissionUnknown, admission.State)
	assert.True(t, admission.SlotHeld)
	assert.Equal(t, 1, route.ActiveCount)
}

func TestBuildPlatformNativeTaskRequestKeepsProviderURLsInternal(t *testing.T) {
	request := dto.NewPlatformGenerationRequest()
	request.Model = "video-model"
	request.Mode = "image_to_video"
	request.Inputs.Prompt = "animate"
	request.Inputs.Assets = []dto.PlatformGenerationAssetInput{{URL: "https://assets.example/input.png", MediaType: "image"}}
	request.Output.DurationSeconds = 5
	request.Output.AspectRatio = "9:16"
	request.Output.Resolution = "1080p"
	request.Output.Count = 1
	request.Output.FaceEnabled = false
	profile, ok := generationprofile.Get(generationprofile.VolcengineArkVideoGenerationV1)
	require.True(t, ok)
	request.Metadata = generationprofile.SnapshotMetadata(request.Metadata, profile)
	body, err := buildPlatformNativeTaskRequest(request)
	require.NoError(t, err)
	var native platformNativeTaskRequest
	require.NoError(t, common.Unmarshal(body, &native))
	assert.Equal(t, "https://assets.example/input.png", native.Image)
	assert.Equal(t, "1080x1920", native.Size)
	assert.Equal(t, 5, native.Duration)
	assert.False(t, strings.Contains(string(body), "callback"))
}

func googleVeoImageRequestForTest(t *testing.T, assetURL string) dto.PlatformGenerationRequest {
	t.Helper()
	request := dto.NewPlatformGenerationRequest()
	request.Model = "video.google.veo-3.1"
	request.Mode = "image_to_video"
	request.Inputs.Prompt = "animate the verified still image"
	request.Inputs.Assets = []dto.PlatformGenerationAssetInput{{URL: assetURL, MediaType: "image"}}
	request.Output.DurationSeconds = 8
	request.Output.AspectRatio = "16:9"
	request.Output.Resolution = "720p"
	request.Output.Count = 1
	profile, ok := generationprofile.Get(generationprofile.GoogleGeminiVeo31VideoV1)
	require.True(t, ok)
	request.Metadata = generationprofile.SnapshotMetadata(request.Metadata, profile)
	return request
}

func TestBuildPlatformNativeTaskRequestInlinesVerifiedGoogleVeoImage(t *testing.T) {
	pngBytes, err := base64.StdEncoding.DecodeString("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
	require.NoError(t, err)
	requestCount := 0
	client := &http.Client{Transport: platformGenerationInputRoundTripFunc(func(request *http.Request) (*http.Response, error) {
		requestCount++
		require.Equal(t, http.MethodGet, request.Method)
		require.Equal(t, "identity", request.Header.Get("Accept-Encoding"))
		return &http.Response{
			StatusCode:    http.StatusOK,
			Header:        http.Header{"Content-Type": []string{"image/png"}},
			Body:          io.NopCloser(strings.NewReader(string(pngBytes))),
			ContentLength: int64(len(pngBytes)),
			Request:       request,
		}, nil
	})}
	assetURL := "https://assets.example/signed/input.png?signature=secret"

	body, err := buildPlatformNativeTaskRequestWithDependencies(
		context.Background(),
		googleVeoImageRequestForTest(t, assetURL),
		client,
		func(string) error { return nil },
	)
	require.NoError(t, err)
	require.Equal(t, 1, requestCount)
	require.NotContains(t, string(body), assetURL)
	require.NotContains(t, string(body), "signature=secret")
	var native platformNativeTaskRequest
	require.NoError(t, common.Unmarshal(body, &native))
	require.Len(t, native.Images, 1)
	require.Equal(t, native.Images[0], native.Image)
	require.Equal(t, "data:image/png;base64,"+base64.StdEncoding.EncodeToString(pngBytes), native.Image)
}

func TestBuildPlatformNativeTaskRequestRejectsUnsafeGoogleVeoImageResponses(t *testing.T) {
	validPNG, err := base64.StdEncoding.DecodeString("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
	require.NoError(t, err)
	tests := []struct {
		name     string
		response *http.Response
	}{
		{
			name: "redirect",
			response: &http.Response{StatusCode: http.StatusTemporaryRedirect, Header: http.Header{
				"Location": []string{"https://other.example/input.png"},
			}, Body: io.NopCloser(strings.NewReader(""))},
		},
		{
			name: "declared type mismatch",
			response: &http.Response{StatusCode: http.StatusOK, Header: http.Header{
				"Content-Type": []string{"image/jpeg"},
			}, Body: io.NopCloser(strings.NewReader(string(validPNG))), ContentLength: int64(len(validPNG))},
		},
		{
			name: "oversized",
			response: &http.Response{StatusCode: http.StatusOK, Header: http.Header{
				"Content-Type": []string{"image/png"},
			}, Body: io.NopCloser(strings.NewReader("")), ContentLength: platformGenerationVeoInputMaxBytes + 1},
		},
		{
			name: "not an image",
			response: &http.Response{StatusCode: http.StatusOK, Header: http.Header{
				"Content-Type": []string{"text/plain"},
			}, Body: io.NopCloser(strings.NewReader("not an image")), ContentLength: int64(len("not an image"))},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			requestCount := 0
			client := &http.Client{Transport: platformGenerationInputRoundTripFunc(func(request *http.Request) (*http.Response, error) {
				requestCount++
				copy := *test.response
				copy.Header = test.response.Header.Clone()
				copy.Request = request
				return &copy, nil
			})}
			_, err := buildPlatformNativeTaskRequestWithDependencies(
				context.Background(),
				googleVeoImageRequestForTest(t, "https://assets.example/input.png"),
				client,
				func(string) error { return nil },
			)
			require.ErrorIs(t, err, errPlatformGenerationInputAssetUnavailable)
			require.Equal(t, 1, requestCount)
		})
	}
}

func TestBuildPlatformNativeTaskRequestCancelsGoogleVeoImageFetch(t *testing.T) {
	client := &http.Client{Transport: platformGenerationInputRoundTripFunc(func(request *http.Request) (*http.Response, error) {
		<-request.Context().Done()
		return nil, request.Context().Err()
	})}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	started := time.Now()
	_, err := buildPlatformNativeTaskRequestWithDependencies(
		ctx,
		googleVeoImageRequestForTest(t, "https://assets.example/input.png"),
		client,
		func(string) error { return nil },
	)
	require.ErrorIs(t, err, errPlatformGenerationInputAssetUnavailable)
	require.Less(t, time.Since(started), time.Second)
}

func TestBuildPlatformNativeTaskRequestAppliesGoogleVeoURLPolicyBeforeFetch(t *testing.T) {
	requestCount := 0
	client := &http.Client{Transport: platformGenerationInputRoundTripFunc(func(request *http.Request) (*http.Response, error) {
		requestCount++
		return nil, errors.New("must not be reached")
	})}
	_, err := buildPlatformNativeTaskRequestWithDependencies(
		context.Background(),
		googleVeoImageRequestForTest(t, "https://assets.example/input.png"),
		client,
		func(string) error { return errors.New("blocked by SSRF policy") },
	)
	require.ErrorIs(t, err, errPlatformGenerationInputAssetUnavailable)
	require.Zero(t, requestCount)
}

func TestBuildPlatformNativeTaskRequestPreservesSeedanceMultimodalAssets(t *testing.T) {
	request := dto.NewPlatformGenerationRequest()
	request.Model = "video.seedance.2"
	request.Mode = "video_to_video"
	request.Inputs.Prompt = "preserve the product and continue the camera move"
	request.Inputs.Assets = []dto.PlatformGenerationAssetInput{
		{URL: "https://assets.example/product.png", MediaType: "image"},
		{URL: "https://assets.example/actor.png", MediaType: "image"},
		{URL: "https://assets.example/motion.mp4", MediaType: "video"},
		{URL: "https://assets.example/camera.mp4", MediaType: "video"},
		{URL: "https://assets.example/voice.wav", MediaType: "audio"},
		{URL: "https://assets.example/music.wav", MediaType: "audio"},
	}
	request.Output.DurationSeconds = 15
	request.Output.AspectRatio = "16:9"
	request.Output.Resolution = "720p"
	request.Output.Count = 1
	request.Output.FaceEnabled = false
	profile, ok := generationprofile.Get(generationprofile.VolcengineArkVideoGenerationV1)
	require.True(t, ok)
	request.Metadata = generationprofile.SnapshotMetadata(request.Metadata, profile)

	body, err := buildPlatformNativeTaskRequest(request)
	require.NoError(t, err)
	var native platformNativeTaskRequest
	require.NoError(t, common.Unmarshal(body, &native))
	require.Equal(t, []string{
		"https://assets.example/product.png",
		"https://assets.example/actor.png",
	}, native.Images)
	require.Equal(t, []string{
		"https://assets.example/motion.mp4",
		"https://assets.example/camera.mp4",
	}, native.Videos)
	require.Equal(t, []string{
		"https://assets.example/voice.wav",
		"https://assets.example/music.wav",
	}, native.Audios)
	require.Equal(t, native.Images[0], native.Image, "the legacy single-image field remains only a compatibility projection")
	require.Equal(t, native.Videos[0], native.InputReference, "the legacy reference field remains sticky to the first admitted video")
	require.Equal(t, "video_to_video", native.Metadata["platform_generation_mode"])
}

func TestBuildPlatformNativeTaskRequestUsesProtectedSeedreamContract(t *testing.T) {
	request := dto.NewPlatformGenerationRequest()
	request.Model = "image.seedream.5-lite"
	request.Mode = "text_to_image"
	request.Inputs.Prompt = "a clean product photograph"
	request.Inputs.Assets = []dto.PlatformGenerationAssetInput{}
	request.Output.DurationSeconds = 1
	request.Output.AspectRatio = "1:1"
	request.Output.Resolution = constant.PlatformGenerationArkSeedream50LiteSize
	request.Output.Count = 1
	request.Output.FaceEnabled = false
	profile, ok := generationprofile.Get(generationprofile.Seedream50TextToImageV1)
	require.True(t, ok)
	request.Metadata = generationprofile.SnapshotMetadata(request.Metadata, profile)
	body, err := buildPlatformNativeTaskRequest(request)
	require.NoError(t, err)
	var native platformNativeTaskRequest
	require.NoError(t, common.Unmarshal(body, &native))
	assert.Equal(t, constant.PlatformGenerationArkSeedream50LiteSize, native.Size)
	assert.Zero(t, native.Duration)
	assert.Empty(t, native.Seconds)
	assert.Empty(t, native.Image)
	assert.Empty(t, native.Images)
	assert.Empty(t, native.InputReference)
	assert.Equal(t, "text_to_image", native.Metadata["platform_generation_mode"])
	assert.Equal(t, "1:1", native.Metadata["aspectRatio"])
	assert.Equal(t, constant.PlatformGenerationArkSeedream50LiteSize, native.Metadata["resolution"])
	assert.Equal(t, float64(1), native.Metadata["sampleCount"])
	assert.Equal(t, false, native.Metadata["face_enabled"])
}

func TestBuildPlatformNativeTaskRequestRejectsSeedreamContractDrift(t *testing.T) {
	base := dto.NewPlatformGenerationRequest()
	base.Model = "image.seedream.5-lite"
	base.Mode = "text_to_image"
	base.Inputs.Prompt = "a clean product photograph"
	base.Output.DurationSeconds = 1
	base.Output.AspectRatio = "1:1"
	base.Output.Resolution = constant.PlatformGenerationArkSeedream50LiteSize
	base.Output.Count = 1
	profile, ok := generationprofile.Get(generationprofile.Seedream50TextToImageV1)
	require.True(t, ok)
	base.Metadata = generationprofile.SnapshotMetadata(base.Metadata, profile)

	for name, mutate := range map[string]func(*dto.PlatformGenerationRequest){
		"ratio": func(request *dto.PlatformGenerationRequest) { request.Output.AspectRatio = "16:9" },
		"size":  func(request *dto.PlatformGenerationRequest) { request.Output.Resolution = "2K" },
	} {
		t.Run(name, func(t *testing.T) {
			request := base
			mutate(&request)
			_, err := buildPlatformNativeTaskRequest(request)
			require.ErrorContains(t, err, "exceeds profile")
		})
	}

	withoutDurationSentinel := base
	withoutDurationSentinel.Output.DurationSeconds = 37
	body, err := buildPlatformNativeTaskRequest(withoutDurationSentinel)
	require.NoError(t, err)
	var native platformNativeTaskRequest
	require.NoError(t, common.Unmarshal(body, &native))
	require.Zero(t, native.Duration)
	require.Empty(t, native.Seconds)
}

func TestPlatformGenerationArtifactTransferRequestPinsSeedreamPNGOnly(t *testing.T) {
	job := model.PlatformGenerationJob{
		ID:       uuid.NewString(),
		TenantID: uuid.NewString(),
	}
	configureLegacyProfilelessTransferTest(t, job.TenantID)
	imageRequest := dto.NewPlatformGenerationRequest()
	imageRequest.Model = constant.PlatformGenerationLegacySeedream50LitePublicAlias
	imageRequest.Mode = "text_to_image"
	imageRequest.Inputs.Prompt = "verified product image"
	imageRequest.Output.AspectRatio = "1:1"
	imageRequest.Output.Resolution = constant.PlatformGenerationArkSeedream50LiteSize
	imageRequest.Output.Count = 1
	profile, ok := generationprofile.Get(generationprofile.Seedream50TextToImageV1)
	require.True(t, ok)
	imageRequest.Metadata = generationprofile.SnapshotMetadata(imageRequest.Metadata, profile)
	requestJSON, err := common.Marshal(imageRequest)
	require.NoError(t, err)
	requestDigest := sha256.Sum256(requestJSON)
	job.RequestJSON = string(requestJSON)
	job.NativeTaskRecoveryJSON = fmt.Sprintf(
		`{"schema_version":4,"request_json_sha256":"sha256:%x"}`,
		requestDigest,
	)
	image, err := platformGenerationArtifactTransferRequest(job, imageRequest, "https://provider.example/image.png", uuid.NewString(), "image")
	require.NoError(t, err)
	require.Equal(t, "image/png", image.ExpectedContentType)
	require.Equal(t, constant.PlatformGenerationArkSeedream50LiteWidth, image.ExpectedImageWidth)
	require.Equal(t, constant.PlatformGenerationArkSeedream50LiteHeight, image.ExpectedImageHeight)

	videoRequest := dto.NewPlatformGenerationRequest()
	videoRequest.Mode = "text_to_video"
	video, err := platformGenerationArtifactTransferRequest(job, videoRequest, "https://provider.example/video.mp4", uuid.NewString(), "video")
	require.NoError(t, err)
	require.Empty(t, video.ExpectedContentType)
	require.Zero(t, video.ExpectedImageWidth)
	require.Zero(t, video.ExpectedImageHeight)
}

func TestPlatformGenerationNativeTaskIDIsStableAndOpaque(t *testing.T) {
	jobID := uuid.NewString()
	first, err := model.PlatformGenerationNativeTaskID(jobID)
	require.NoError(t, err)
	second, err := model.PlatformGenerationNativeTaskID(jobID)
	require.NoError(t, err)
	assert.Equal(t, first, second)
	assert.True(t, strings.HasPrefix(first, "task_pg_"))
	assert.NotContains(t, first, "-")
}

func TestSubmitPlatformNativeTaskSendsDistinctWorkerAndRouteFences(t *testing.T) {
	workerLeaseToken := uuid.NewString()
	routeSubmissionToken := uuid.NewString()
	received := make(chan http.Header, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		received <- r.Header.Clone()
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()
	t.Setenv("RELAY_COMPAT_INTERNAL_BASE_URL", server.URL)
	t.Setenv("RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN", "internal-admission-test-token")

	claim := model.PlatformGenerationClaim{
		Token: workerLeaseToken,
		Job: model.PlatformGenerationJob{
			ID:        uuid.NewString(),
			RequestID: "worker-fence-request",
		},
	}
	routeClaim := model.PlatformGenerationRouteClaim{
		Route:           model.PlatformGenerationProviderRoute{ID: 42},
		SubmissionToken: routeSubmissionToken,
	}
	started, status, err := submitPlatformNativeTask(
		context.Background(),
		claim,
		routeClaim,
		PlatformRelayPrincipal{UpstreamToken: "native-token"},
		[]byte(`{}`),
	)
	require.NoError(t, err)
	assert.False(t, started)
	assert.Equal(t, http.StatusNoContent, status)

	headers := <-received
	assert.Equal(t, workerLeaseToken, headers.Get(constant.HeaderPlatformGenerationWorkerLeaseToken))
	assert.Equal(t, routeSubmissionToken, headers.Get(constant.HeaderPlatformGenerationSubmissionToken))
	assert.NotEqual(t,
		headers.Get(constant.HeaderPlatformGenerationWorkerLeaseToken),
		headers.Get(constant.HeaderPlatformGenerationSubmissionToken),
	)
}

func TestPinnedNativeTaskPersistsPublicIDChannelAndExactKeyIndex(t *testing.T) {
	publicTaskID := "task_pg_0123456789abcdef0123456789abcdef"
	pinnedIndex := 1
	pinnedKey := "second-provider-key"
	info := &relaycommon.RelayInfo{
		UserId:     42,
		UsingGroup: "default",
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelId:            9201,
			ChannelType:          constant.ChannelTypeKling,
			ChannelIsMultiKey:    true,
			ChannelMultiKeyIndex: pinnedIndex,
			ApiKey:               pinnedKey,
			UpstreamModelName:    "upstream-video-model",
		},
		TaskRelayInfo: &relaycommon.TaskRelayInfo{
			PublicTaskID:        publicTaskID,
			PinnedProviderRoute: true,
		},
	}
	task := model.InitTask(constant.TaskPlatform(fmt.Sprintf("%d", constant.ChannelTypeKling)), info)
	assert.Equal(t, publicTaskID, task.TaskID)
	assert.Equal(t, 9201, task.ChannelId)
	require.NotNil(t, task.PrivateData.PinnedKeyIndex)
	assert.Equal(t, pinnedIndex, *task.PrivateData.PinnedKeyIndex)
	assert.Equal(t, pinnedKey, task.PrivateData.TransientProviderKey)
	assert.Equal(t, fmt.Sprintf("%x", common.Sha256Raw([]byte(pinnedKey))), task.PrivateData.PinnedKeyFingerprint)
	require.Error(t, model.DB.Create(task).Error, "a transient provider key must never reach task storage")
	require.NoError(t, model.BindTaskProviderCredentialVersion(task, model.ProviderCredentialNativeTenantScope))
	assert.Empty(t, task.PrivateData.TransientProviderKey)
	assert.NotEmpty(t, task.PrivateData.ProviderCredentialVersion)
	resolvedKey, err := model.ResolveTaskProviderCredential(task)
	require.NoError(t, err)
	assert.Equal(t, pinnedKey, resolvedKey)
}
