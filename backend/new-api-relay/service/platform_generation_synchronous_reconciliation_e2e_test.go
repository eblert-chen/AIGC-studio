package service

import (
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	"github.com/glebarez/sqlite"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

type synchronousImageArtifactStore struct {
	payload []byte
	stored  PlatformStoredArtifact
}

func (*synchronousImageArtifactStore) Kind() string      { return "synchronous_image_test" }
func (*synchronousImageArtifactStore) BindingID() string { return strings.Repeat("9", 64) }
func (*synchronousImageArtifactStore) Persistent() bool  { return true }

func (store *synchronousImageArtifactStore) Put(
	ctx context.Context,
	input PlatformArtifactPutInput,
) (PlatformStoredArtifact, error) {
	payload, err := io.ReadAll(input.Content)
	if err != nil {
		return PlatformStoredArtifact{}, err
	}
	if err := ctx.Err(); err != nil {
		return PlatformStoredArtifact{}, err
	}
	store.payload = payload
	store.stored = PlatformStoredArtifact{
		ObjectKey: input.ObjectKey, ContentType: input.ContentType,
		SizeBytes: input.SizeBytes, SHA256: input.SHA256, VersionID: "image-version-1",
	}
	return store.stored, nil
}

func (*synchronousImageArtifactStore) Delete(context.Context, string) error { return nil }

func (*synchronousImageArtifactStore) IssueSignedDownload(
	context.Context,
	string,
	time.Duration,
) (PlatformIssuedArtifactDownload, error) {
	return PlatformIssuedArtifactDownload{}, nil
}

func (*synchronousImageArtifactStore) Healthcheck(context.Context) error { return nil }

func prepareSynchronousImageReconciliationE2E(t *testing.T) {
	t.Helper()
	previousDB := model.DB
	previousLogDB := model.LOG_DB
	dsn := "file:synchronous-image-e2e-" + uuid.NewString() + "?mode=memory&cache=shared"
	database, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{})
	require.NoError(t, err)
	sqlDB, err := database.DB()
	require.NoError(t, err)
	sqlDB.SetMaxOpenConns(1)
	models := []any{
		&model.Channel{},
		&model.ProviderChannelCredentialSetVersion{},
		&model.ProviderCredentialVersion{},
		&model.Task{},
		&model.PlatformGenerationJob{},
		&model.PlatformGenerationOutbox{},
		&model.PlatformArtifactUploadIntent{},
		&model.PlatformGenerationProviderAccountState{},
		&model.PlatformGenerationProviderRoute{},
		&model.PlatformGenerationRouteAdmission{},
		&model.PlatformGenerationCallbackDelivery{},
		&model.PlatformGenerationReconciliationEvent{},
	}
	models = append(models, model.PlatformProviderMonitorAndCostModels()...)
	require.NoError(t, database.AutoMigrate(models...))
	require.NoError(t, model.MigratePlatformChannelCostPersonalScopeV7WithDB(database))
	model.DB = database
	model.LOG_DB = database
	t.Cleanup(func() {
		model.DB = previousDB
		model.LOG_DB = previousLogDB
		require.NoError(t, sqlDB.Close())
	})
}

func TestSeedreamUnknownCreatedReconciliationTransfersAndCostsWithoutLeakingProviderURL(t *testing.T) {
	prepareSynchronousImageReconciliationE2E(t)
	t.Setenv("RELAY_COMPAT_ENABLED", "true")
	t.Setenv("RELAY_COMPAT_WORKER_ENABLED", "true")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
	t.Setenv("APP_ENV", "")
	t.Setenv("DEPLOYMENT_ENV", "")
	t.Setenv("ENVIRONMENT", "")
	t.Setenv("RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN", "synchronous-image-admission-token")
	const channelID = 9411
	providerKey := "seedream-provider-key-e2e"
	fingerprint := fmt.Sprintf("%x", common.Sha256Raw([]byte(providerKey)))
	modelID := constant.PlatformGenerationLegacySeedream50LitePublicAlias

	var nativeProviderPosts atomic.Int32
	nativeServer := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		nativeProviderPosts.Add(1)
		require.Equal(t, http.MethodPost, request.Method)
		require.Equal(t, "/internal/platform-generations/native-submit", request.URL.Path)
		jobID := request.Header.Get(constant.HeaderPlatformGenerationJobID)
		routeID, err := strconv.ParseInt(request.Header.Get(constant.HeaderPlatformGenerationRouteID), 10, 64)
		require.NoError(t, err)
		_, err = model.BeginPlatformGenerationRouteSubmission(
			jobID,
			routeID,
			request.Header.Get(constant.HeaderPlatformGenerationWorkerLeaseToken),
			request.Header.Get(constant.HeaderPlatformGenerationSubmissionToken),
		)
		require.NoError(t, err)
		nativeTaskID, err := model.PlatformGenerationNativeTaskID(jobID)
		require.NoError(t, err)
		pinnedIndex := 0
		task := &model.Task{
			CreatedAt: time.Now().UTC().Unix(), UpdatedAt: time.Now().UTC().Unix(),
			TaskID: nativeTaskID, Platform: constant.TaskPlatform(strconv.Itoa(constant.ChannelTypeVolcEngine)),
			Group: "default", ChannelId: channelID, Action: "text_to_image",
			Status: model.TaskStatusSubmitted, SubmitTime: time.Now().UTC().Unix(), Progress: "0%",
			Properties: model.Properties{OriginModelName: modelID, UpstreamModelName: constant.PlatformGenerationArkSeedream50Model},
			PrivateData: model.TaskPrivateData{
				PinnedKeyIndex:       &pinnedIndex,
				PinnedKeyFingerprint: fingerprint,
				TransientProviderKey: providerKey,
				BillingSource:        model.TaskBillingSourcePlatformExternal,
			},
		}
		require.NoError(t, model.StagePlatformGenerationNativeTaskRecovery(
			jobID,
			request.Header.Get(constant.HeaderPlatformGenerationWorkerLeaseToken),
			task,
		))
		hijacker, ok := response.(http.Hijacker)
		require.True(t, ok)
		connection, _, err := hijacker.Hijack()
		require.NoError(t, err)
		require.NoError(t, connection.Close())
	}))
	t.Cleanup(nativeServer.Close)
	t.Setenv("RELAY_COMPAT_INTERNAL_BASE_URL", nativeServer.URL)

	imagePayload := platformArtifactPNGDimensionsFixture(
		t,
		constant.PlatformGenerationArkSeedream50CompatibilityWidth,
		constant.PlatformGenerationArkSeedream50CompatibilityHeight,
	)
	var artifactDownloads atomic.Int32
	artifactServer := httptest.NewTLSServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		artifactDownloads.Add(1)
		require.Equal(t, "/artifact", request.URL.Path)
		require.Equal(t, "provider-secret-query", request.URL.Query().Get("X-Signature"))
		response.Header().Set("Content-Type", "image/png")
		_, err := response.Write(imagePayload)
		require.NoError(t, err)
	}))
	t.Cleanup(artifactServer.Close)
	artifactServerURL, err := url.Parse(artifactServer.URL)
	require.NoError(t, err)
	require.NotEmpty(t, artifactServer.Certificate().DNSNames)
	artifactHost := artifactServer.Certificate().DNSNames[0]
	providerArtifactURL := "https://" + net.JoinHostPort(artifactHost, artifactServerURL.Port()) +
		"/artifact?X-Signature=provider-secret-query"

	profile, ok := generationprofile.Get(generationprofile.Seedream50TextToImageV1)
	require.True(t, ok)
	capabilityJSON, err := common.Marshal(profile.Capability)
	require.NoError(t, err)
	tenantID := uuid.NewString()
	callbackURL := "https://platform.example/internal/relay-callback"
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", fmt.Sprintf(
		`{"platform":{"tenant_id":%q,"api_key":"relay-secret","upstream_token":"native-user-token","callback_url":%q,"callback_signing_secret":"callback-secret-with-at-least-32-bytes"}}`,
		tenantID,
		callbackURL,
	))
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", "")
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", fmt.Sprintf(`{
		%q: [{
			"route_id":"seedream-created-e2e-route",
			"provider_name":"volcengine",
			"account_id":"seedream-created-e2e-account",
			"channel_id":%d,
			"native_channel_type":%d,
			"key_index":0,
			"key_fingerprint":%q,
			"channel_class":"official",
			"upstream_model":%q,
			"production_ready":true,
			"rpm_limit":10,
			"active_task_limit":2,
			"capability_profile":%q,
			"capabilities":%s
		}]
	}`,
		modelID,
		channelID,
		constant.ChannelTypeVolcEngine,
		fingerprint,
		constant.PlatformGenerationArkSeedream50Model,
		generationprofile.Seedream50TextToImageV1,
		capabilityJSON,
	))

	require.NoError(t, model.DB.Create(&model.Channel{
		Id: channelID, Type: constant.ChannelTypeVolcEngine, Key: providerKey,
		Status: common.ChannelStatusEnabled, Name: "seedream-created-e2e",
		CreatedTime: 1, Models: modelID, Group: "default",
	}).Error)
	require.NoError(t, SyncPlatformGenerationProviderRoutes())
	resource, found, err := GetPlatformRelayModel(modelID)
	require.NoError(t, err)
	require.True(t, found)

	companyID := uuid.NewString()
	platformTaskID := uuid.NewString()
	request := dto.NewPlatformGenerationRequest()
	request.ClientReferenceID = &platformTaskID
	request.Model = modelID
	request.Mode = "text_to_image"
	request.ExpectedCapabilityRevision = resource.CapabilityRevision
	request.Inputs.Prompt = "a verified studio product photograph"
	request.Output.DurationSeconds = 1
	request.Output.AspectRatio = "1:1"
	request.Output.Resolution = constant.PlatformGenerationArkSeedream50CompatibilitySize
	request.Output.Count = 1
	request.Metadata["platform_company_id"] = companyID
	request.Metadata["platform_billing_scope"] = "company"
	request.Metadata["platform_billing_scope_id"] = companyID
	request.Metadata["platform_task_id"] = platformTaskID
	request.Callback = &dto.PlatformGenerationCallback{URL: callbackURL}
	require.NoError(t, request.Validate())
	accepted, err := SubmitPlatformGeneration(
		PlatformRelayPrincipal{
			ClientID: "platform", TenantID: tenantID, UpstreamToken: "native-user-token",
			CallbackURL: callbackURL, CallbackSecret: "callback-secret-with-at-least-32-bytes",
		},
		request,
		"seedream-created-e2e-idempotency",
		"seedream-created-e2e-request",
	)
	require.NoError(t, err)

	processed, err := RunPlatformGenerationSubmissionOnce(context.Background())
	require.NoError(t, err)
	require.True(t, processed)
	require.EqualValues(t, 1, nativeProviderPosts.Load())
	unknown, err := model.GetPlatformGenerationSubmissionUnknown(accepted.ID, tenantID)
	require.NoError(t, err)
	require.Equal(t, model.PlatformGenerationRouteAdmissionUnknown, unknown.Admission.State)
	require.True(t, unknown.Admission.SlotHeld)

	providerCreatedAt := time.Now().UTC().Truncate(time.Second)
	rate := dto.PlatformProviderContractRateInput{
		ID: uuid.NewString(), ProviderName: unknown.Route.ProviderName,
		ChannelID: unknown.Route.ChannelID, UpstreamModel: unknown.Route.UpstreamModel,
		Mode: "text_to_image", Resolution: request.Output.Resolution,
		BillingUnit: dto.PlatformContractRateUnitOutputItem, UnitAmountCents: 23,
		Currency: "CNY", EffectiveFrom: providerCreatedAt.Add(-time.Hour),
		SourceReference:      "seedream-provider-contract-e2e",
		SourceDocumentSHA256: strings.Repeat("c", 64),
	}
	require.NoError(t, model.SyncPlatformProviderContractRates([]dto.PlatformProviderContractRateInput{rate}))

	providerResponseSHA256 := strings.Repeat("d", 64)
	upstreamTaskID := "seedream:" + providerResponseSHA256
	reconciliationRequest := dto.PlatformGenerationReconciliationRequest{
		OperationID:                 "seedream-created-e2e-operation",
		Outcome:                     "created",
		UpstreamTaskID:              upstreamTaskID,
		ExpectedRouteID:             unknown.Route.ID,
		ExpectedSubmissionAttempt:   unknown.Admission.Attempt,
		ExpectedReconciliationToken: unknown.ReconciliationToken,
		VerificationReference:       "provider-console-response-digest-e2e",
		ApprovedBy:                  "platform-owner-e2e",
		ApprovalReason:              "Provider response proves one completed image and its temporary artifact",
		ApprovalKeyID:               "platform-approval-e2e",
		ApprovalSignature:           "hmac-sha256:" + strings.Repeat("a", 64),
		SynchronousResult: &dto.PlatformGenerationSynchronousResultEvidence{
			ProviderModelID:        constant.PlatformGenerationArkSeedream50Model,
			ProviderResponseSHA256: providerResponseSHA256,
			ArtifactURL:            providerArtifactURL,
			ProviderCreatedAt:      providerCreatedAt.Format(time.RFC3339),
			GeneratedImages:        1,
			OutputTokens:           1,
			TotalTokens:            1,
		},
	}
	var storedRequest dto.PlatformGenerationRequest
	require.NoError(t, common.Unmarshal([]byte(unknown.Job.RequestJSON), &storedRequest))
	require.NoError(t, model.ValidatePlatformGenerationRequestSnapshotBinding(unknown.Job))
	storedProfile, hasStoredProfile, err := platformGenerationProfileForStoredJob(unknown.Job, storedRequest)
	require.NoError(t, err)
	require.True(t, hasStoredProfile)
	require.Equal(t, generationprofile.Seedream50TextToImageV1, storedProfile.ID)
	_, err = validatePlatformGenerationCreatedReconciliation(*unknown, reconciliationRequest)
	require.NoError(t, err)
	snapshot, receipt, replayed, err := ResolvePlatformGenerationUnknownSubmission(
		tenantID,
		accepted.ID,
		reconciliationRequest,
		"seedream-created-e2e-resolution-request",
	)
	require.NoError(t, err)
	require.False(t, replayed)
	require.Equal(t, model.PlatformGenerationStatusProcessing, snapshot.Status)
	require.NotNil(t, receipt.SynchronousResult)
	expectedURLDigest := sha256.Sum256([]byte(providerArtifactURL))
	assert.Equal(t, fmt.Sprintf("%x", expectedURLDigest), receipt.SynchronousResult.ArtifactURLSHA256)
	receiptJSON, err := common.Marshal(receipt)
	require.NoError(t, err)
	assert.NotContains(t, string(receiptJSON), providerArtifactURL)
	assert.NotContains(t, string(receiptJSON), "provider-secret-query")

	var reconciliationEvent model.PlatformGenerationReconciliationEvent
	require.NoError(t, model.DB.Where("job_id = ?", accepted.ID).First(&reconciliationEvent).Error)
	assert.NotContains(t, reconciliationEvent.PayloadJSON, providerArtifactURL)
	assert.NotContains(t, reconciliationEvent.PayloadJSON, "provider-secret-query")
	var nativeTask model.Task
	require.NoError(t, model.DB.Where("task_id = ?", unknown.Job.NativeTaskID).First(&nativeTask).Error)
	require.Equal(t, model.TaskStatus(model.TaskStatusSuccess), nativeTask.Status)
	require.Equal(t, providerArtifactURL, nativeTask.PrivateData.ResultURL)
	assert.NotContains(t, string(nativeTask.Data), providerArtifactURL)

	var adaptorLookups atomic.Int32
	previousAdaptorFactory := GetTaskAdaptorFunc
	GetTaskAdaptorFunc = func(constant.TaskPlatform) TaskPollingAdaptor {
		adaptorLookups.Add(1)
		return nil
	}
	t.Cleanup(func() { GetTaskAdaptorFunc = previousAdaptorFactory })
	processed, err = RunPlatformGenerationPollOnce(context.Background())
	require.NoError(t, err)
	require.True(t, processed)
	require.Zero(t, adaptorLookups.Load(), "synchronous terminal evidence must never call FetchTask")
	require.EqualValues(t, 1, nativeProviderPosts.Load(), "created reconciliation must never issue a second provider POST")

	transferring, err := model.GetPlatformGenerationJob(accepted.ID, tenantID)
	require.NoError(t, err)
	require.Equal(t, model.PlatformGenerationStatusTransferring, transferring.Status)
	require.Contains(t, transferring.TemporaryResultJSON, providerArtifactURL)
	var outcomes []model.PlatformProviderTerminalOutcome
	require.NoError(t, model.DB.Where("relay_job_id = ?", accepted.ID).Find(&outcomes).Error)
	require.Len(t, outcomes, 1)
	assert.Equal(t, upstreamTaskID, strings.TrimPrefix(outcomes[0].ExternalReference, "provider-task:"))
	assert.NotContains(t, outcomes[0].ExternalReference, providerArtifactURL)

	processed, err = RunPlatformChannelCostReconciliationOnce(context.Background(), 30*time.Second)
	require.NoError(t, err)
	require.True(t, processed)
	processed, err = RunPlatformChannelCostReconciliationOnce(context.Background(), 30*time.Second)
	require.NoError(t, err)
	require.False(t, processed)
	var costEvents []model.PlatformChannelCostEvent
	require.NoError(t, model.DB.Where("relay_job_id = ?", accepted.ID).Find(&costEvents).Error)
	require.Len(t, costEvents, 1)
	assert.Equal(t, int64(23), costEvents[0].AmountCents)
	assert.NotContains(t, costEvents[0].PayloadJSON, providerArtifactURL)
	assert.NotContains(t, costEvents[0].PayloadJSON, "provider-secret-query")

	roots := x509.NewCertPool()
	roots.AddCert(artifactServer.Certificate())
	downloader, err := newPlatformArtifactDownloader(
		PlatformArtifactDownloadConfig{MaxBytes: int64(len(imagePayload)) + 1, Timeout: 5 * time.Second},
		artifactStaticResolver{artifactHost: {{IP: net.ParseIP("8.8.8.8")}}},
		func(ctx context.Context, network string, _ string) (net.Conn, error) {
			dialer := &net.Dialer{}
			return dialer.DialContext(ctx, network, artifactServer.Listener.Addr().String())
		},
	)
	require.NoError(t, err)
	downloader.tlsConfig = &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12}
	claimed, transferToken, err := model.ClaimPlatformGenerationTransfer(30 * time.Second)
	require.NoError(t, err)
	require.Equal(t, accepted.ID, claimed.ID)
	store := &synchronousImageArtifactStore{}
	require.NoError(t, transferClaimedPlatformGenerationWithLeasePolicy(
		context.Background(),
		*claimed,
		transferToken,
		downloader,
		store,
		30*time.Second,
		5*time.Second,
	))
	require.EqualValues(t, 1, artifactDownloads.Load())
	require.Equal(t, imagePayload, store.payload)

	completed, err := model.GetPlatformGenerationJob(accepted.ID, tenantID)
	require.NoError(t, err)
	require.Equal(t, model.PlatformGenerationStatusSucceeded, completed.Status)
	require.Empty(t, completed.TemporaryResultJSON)
	require.Empty(t, completed.UpstreamResultURL)
	require.NotContains(t, completed.OutputsJSON, providerArtifactURL)
	require.Contains(t, completed.OutputsJSON, store.stored.ObjectKey)
	require.NoError(t, model.DB.Where("task_id = ?", unknown.Job.NativeTaskID).First(&nativeTask).Error)
	assert.Empty(t, nativeTask.PrivateData.ResultURL)
	assert.Empty(t, nativeTask.GetResultURL())
	assert.Empty(t, nativeTask.FailReason)
	assert.Equal(t, upstreamTaskID, nativeTask.PrivateData.UpstreamTaskID)
	nativeJSON, err := common.Marshal(nativeTask)
	require.NoError(t, err)
	assert.NotContains(t, string(nativeJSON), providerArtifactURL)
	assert.NotContains(t, string(nativeJSON), "provider-secret-query")

	var callbacks []model.PlatformGenerationCallbackDelivery
	require.NoError(t, model.DB.Where("job_id = ?", accepted.ID).Order("created_at ASC").Find(&callbacks).Error)
	require.NotEmpty(t, callbacks)
	foundCanonicalSuccess := false
	for _, callback := range callbacks {
		assert.NotContains(t, callback.PayloadJSON, providerArtifactURL)
		assert.NotContains(t, callback.PayloadJSON, "provider-secret-query")
		if strings.Contains(callback.PayloadJSON, `"status":"succeeded"`) {
			foundCanonicalSuccess = true
			assert.Contains(t, callback.PayloadJSON, store.stored.ObjectKey)
		}
	}
	assert.True(t, foundCanonicalSuccess)
	var stageEvents []model.PlatformTaskStageEvent
	require.NoError(t, model.DB.Where("task_id = ?", platformTaskID).Find(&stageEvents).Error)
	require.NotEmpty(t, stageEvents)
	for _, event := range stageEvents {
		assert.NotContains(t, event.PayloadJSON, providerArtifactURL)
		assert.NotContains(t, event.PayloadJSON, "provider-secret-query")
	}

	callbackCount := len(callbacks)
	stageEventCount := len(stageEvents)
	replayedSnapshot, replayedReceipt, replayed, err := ResolvePlatformGenerationUnknownSubmission(
		tenantID,
		accepted.ID,
		reconciliationRequest,
		"seedream-created-e2e-resolution-replay",
	)
	require.NoError(t, err)
	require.True(t, replayed)
	assert.Equal(t, model.PlatformGenerationStatusSucceeded, replayedSnapshot.Status)
	assert.Equal(t, receipt.EventID, replayedReceipt.EventID)
	require.NotNil(t, replayedReceipt.SynchronousResult)
	assert.Equal(t, receipt.SynchronousResult.ArtifactURLSHA256, replayedReceipt.SynchronousResult.ArtifactURLSHA256)

	conflictingRequest := reconciliationRequest
	conflictingSynchronous := *reconciliationRequest.SynchronousResult
	conflictingSynchronous.ArtifactURL = providerArtifactURL + "&X-Nonce=changed"
	conflictingRequest.SynchronousResult = &conflictingSynchronous
	_, _, _, err = ResolvePlatformGenerationUnknownSubmission(
		tenantID,
		accepted.ID,
		conflictingRequest,
		"seedream-created-e2e-resolution-conflict",
	)
	assert.ErrorIs(t, err, model.ErrPlatformGenerationReconciliationConflict)

	assert.EqualValues(t, 1, nativeProviderPosts.Load(), "replay must not issue another provider POST")
	assert.EqualValues(t, 1, artifactDownloads.Load(), "replay must not download the provider artifact again")
	assert.Zero(t, adaptorLookups.Load(), "replay must not poll the provider")
	require.NoError(t, model.DB.Where("relay_job_id = ?", accepted.ID).Find(&outcomes).Error)
	assert.Len(t, outcomes, 1)
	require.NoError(t, model.DB.Where("relay_job_id = ?", accepted.ID).Find(&costEvents).Error)
	assert.Len(t, costEvents, 1)
	require.NoError(t, model.DB.Where("job_id = ?", accepted.ID).Find(&callbacks).Error)
	assert.Len(t, callbacks, callbackCount)
	require.NoError(t, model.DB.Where("task_id = ?", platformTaskID).Find(&stageEvents).Error)
	assert.Len(t, stageEvents, stageEventCount)
	require.NoError(t, model.DB.Where("task_id = ?", unknown.Job.NativeTaskID).First(&nativeTask).Error)
	assert.Empty(t, nativeTask.PrivateData.ResultURL, "replay must not restore the temporary provider URL")
	var reconciliationEvents []model.PlatformGenerationReconciliationEvent
	require.NoError(t, model.DB.Where("job_id = ?", accepted.ID).Find(&reconciliationEvents).Error)
	require.Len(t, reconciliationEvents, 1)
	assert.NotContains(t, reconciliationEvents[0].PayloadJSON, providerArtifactURL)
	assert.NotContains(t, reconciliationEvents[0].PayloadJSON, "provider-secret-query")
}
