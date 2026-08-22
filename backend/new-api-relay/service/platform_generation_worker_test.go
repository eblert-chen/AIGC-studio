package service

import (
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

type fencingPlatformArtifactStore struct {
	jobID            string
	putObjectKey     string
	deletedObjectKey string
}

type panickingPlatformArtifactStore struct{}

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
	preparePlatformProviderMonitorCostServiceTest(t)
	payload := platformArtifactValidMP4Fixture(t)
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.Header().Set("Content-Type", "video/mp4")
		_, _ = response.Write(payload)
	}))
	t.Cleanup(server.Close)
	downloader, sourceURL, _ := artifactTestDownloader(
		t,
		server,
		PlatformArtifactDownloadConfig{MaxBytes: int64(len(payload)) + 1, Timeout: 5 * time.Second},
		[]net.IPAddr{{IP: net.ParseIP("8.8.8.8")}},
	)
	request := dto.NewPlatformGenerationRequest()
	request.Model = "transfer-fence-model"
	request.Mode = "text_to_video"
	request.ExpectedCapabilityRevision = "sha256:" + strings.Repeat("a", 64)
	request.Inputs.Prompt = "transfer fence cleanup fixture"
	requestJSON, err := common.Marshal(request)
	require.NoError(t, err)
	nativeTaskID := "native-transfer-fence"
	temporaryJSON, err := common.Marshal(platformNativeTemporaryResult{
		NativeTaskID: nativeTaskID,
		ResultURL:    sourceURL,
	})
	require.NoError(t, err)
	job := model.PlatformGenerationJob{
		ID:                         uuid.NewString(),
		TenantID:                   uuid.NewString(),
		SourceClientID:             "platform",
		RequestID:                  "transfer-fence-cleanup-request",
		IdempotencyKey:             "transfer-fence-cleanup-idempotency",
		RequestHash:                strings.Repeat("b", 64),
		RequestJSON:                string(requestJSON),
		Model:                      request.Model,
		Mode:                       request.Mode,
		ExpectedCapabilityRevision: request.ExpectedCapabilityRevision,
		CapabilityRevision:         request.ExpectedCapabilityRevision,
		Status:                     model.PlatformGenerationStatusTransferring,
		Progress:                   95,
		NativeTaskID:               nativeTaskID,
		TemporaryResultJSON:        string(temporaryJSON),
		OutputsJSON:                `[]`,
		ErrorDetailsJSON:           `{}`,
		NextTransferAt:             time.Now().UTC().Add(-time.Minute),
	}
	require.NoError(t, model.DB.Create(&job).Error)
	claimed, token, err := model.ClaimPlatformGenerationTransfer(time.Minute)
	require.NoError(t, err)
	store := &fencingPlatformArtifactStore{jobID: job.ID}

	err = transferClaimedPlatformGeneration(context.Background(), *claimed, token, downloader, store)
	require.ErrorIs(t, err, errPlatformGenerationTransferLeaseFenced)
	assert.NotEmpty(t, store.putObjectKey)
	assert.Empty(t, store.deletedObjectKey, "cleanup must survive the transfer worker process")
	processed, cleanupErr := runPlatformArtifactCleanupOnce(context.Background(), store, 3)
	require.NoError(t, cleanupErr)
	assert.True(t, processed)
	assert.Equal(t, store.putObjectKey, store.deletedObjectKey)
	var persisted model.PlatformGenerationJob
	require.NoError(t, model.DB.First(&persisted, "id = ?", job.ID).Error)
	assert.Equal(t, model.PlatformGenerationStatusTransferring, persisted.Status)
	assert.Equal(t, `[]`, persisted.OutputsJSON)
}

func TestPlatformGenerationTransferPanicStopsLeaseAndSchedulesDurableCleanup(t *testing.T) {
	truncate(t)
	payload := platformArtifactValidMP4Fixture(t)
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.Header().Set("Content-Type", "video/mp4")
		_, _ = response.Write(payload)
	}))
	defer server.Close()
	downloader, sourceURL, _ := artifactTestDownloader(
		t,
		server,
		PlatformArtifactDownloadConfig{MaxBytes: int64(len(payload)) + 1, Timeout: 5 * time.Second},
		[]net.IPAddr{{IP: net.ParseIP("8.8.8.8")}},
	)
	request := dto.NewPlatformGenerationRequest()
	request.Model = "transfer-panic-lease-model"
	request.Mode = "text_to_video"
	request.ExpectedCapabilityRevision = "sha256:" + strings.Repeat("a", 64)
	request.Inputs.Prompt = "transfer panic cleanup fixture"
	requestJSON, err := common.Marshal(request)
	require.NoError(t, err)
	nativeTaskID := "native-transfer-panic"
	temporaryJSON, err := common.Marshal(platformNativeTemporaryResult{
		NativeTaskID: nativeTaskID,
		ResultURL:    sourceURL,
	})
	require.NoError(t, err)
	job := model.PlatformGenerationJob{
		ID:                         uuid.NewString(),
		TenantID:                   uuid.NewString(),
		SourceClientID:             "platform",
		RequestID:                  "transfer-panic-lease-request",
		IdempotencyKey:             "transfer-panic-lease-idempotency",
		RequestHash:                strings.Repeat("c", 64),
		RequestJSON:                string(requestJSON),
		Model:                      request.Model,
		Mode:                       request.Mode,
		ExpectedCapabilityRevision: request.ExpectedCapabilityRevision,
		CapabilityRevision:         request.ExpectedCapabilityRevision,
		Status:                     model.PlatformGenerationStatusTransferring,
		Progress:                   95,
		NativeTaskID:               nativeTaskID,
		TemporaryResultJSON:        string(temporaryJSON),
		OutputsJSON:                `[]`,
		ErrorDetailsJSON:           `{}`,
		NextTransferAt:             time.Now().UTC().Add(-time.Minute),
	}
	require.NoError(t, model.DB.Create(&job).Error)
	claimed, token, err := model.ClaimPlatformGenerationTransfer(time.Minute)
	require.NoError(t, err)

	assert.Panics(t, func() {
		_ = transferClaimedPlatformGenerationWithLeasePolicy(
			context.Background(),
			*claimed,
			token,
			downloader,
			&panickingPlatformArtifactStore{},
			2*time.Second,
			20*time.Millisecond,
		)
	})

	var intent model.PlatformArtifactUploadIntent
	require.NoError(t, model.DB.Where("job_id = ? AND transfer_token = ?", job.ID, token).First(&intent).Error)
	assert.Equal(t, model.PlatformArtifactUploadIntentPending, intent.State)
	assert.False(t, intent.AvailableAt.After(time.Now().UTC()),
		"a panic after intent creation must make durable cleanup immediately eligible")

	var stoppedJob model.PlatformGenerationJob
	require.NoError(t, model.DB.First(&stoppedJob, "id = ?", job.ID).Error)
	time.Sleep(120 * time.Millisecond)
	var laterJob model.PlatformGenerationJob
	require.NoError(t, model.DB.First(&laterJob, "id = ?", job.ID).Error)
	assert.Equal(t, stoppedJob.TransferLeaseExpiresAt, laterJob.TransferLeaseExpiresAt,
		"a recovered panic must not leave the transfer lease renewing")

	require.NoError(t, model.DB.Model(&model.PlatformGenerationJob{}).Where("id = ?", job.ID).
		Update("transfer_lease_expires_at", time.Now().UTC().Add(-time.Second)).Error)
	replacement, replacementToken, err := model.ClaimPlatformGenerationTransfer(time.Minute)
	require.NoError(t, err)
	require.NotNil(t, replacement)
	assert.Equal(t, job.ID, replacement.ID)
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
	body, err := buildPlatformNativeTaskRequest(request)
	require.NoError(t, err)
	var native platformNativeTaskRequest
	require.NoError(t, common.Unmarshal(body, &native))
	assert.Equal(t, "https://assets.example/input.png", native.Image)
	assert.Equal(t, "1080x1920", native.Size)
	assert.Equal(t, 5, native.Duration)
	assert.False(t, strings.Contains(string(body), "callback"))
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
