package service

import (
	"context"
	"crypto/sha256"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// All rows and credentials here live in the isolated test database. No route,
// release acceptance, price or grant is written to the running environment.
func seedProtectedMiniMaxH3PollingBinding(t *testing.T, modelID string) protectedNativeBindingFixture {
	t.Helper()
	fixture := seedProtectedSeedancePollingBinding(t)
	providerModel, found, err := generationprofile.ResolveMiniMaxH3ProviderModel(modelID)
	require.NoError(t, err)
	require.True(t, found)
	profile, found := generationprofile.Get(providerModel.AdapterProfileID)
	require.True(t, found)
	request := dto.NewPlatformGenerationRequest()
	request.Model, request.Mode = providerModel.PublicModelID, "text_to_video"
	request.ExpectedCapabilityRevision = fixture.Job.ExpectedCapabilityRevision
	request.Inputs.Prompt = "A single continuous shot of a ceramic cup in daylight"
	request.Output.Count, request.Output.DurationSeconds = 1, 5
	request.Output.AspectRatio, request.Output.Resolution = "16:9", "768p"
	request.Metadata = generationprofile.SnapshotMetadata(request.Metadata, profile)
	require.NoError(t, profile.ValidateRequest(request))
	rawRequest, err := common.Marshal(request)
	require.NoError(t, err)
	requestDigest := sha256.Sum256(rawRequest)
	fixture.Task.Platform = constant.TaskPlatform("hailuo")
	fixture.Task.Properties.UpstreamModelName = modelID
	require.NoError(t, model.DB.Model(fixture.Task).Updates(map[string]any{
		"platform": fixture.Task.Platform, "properties": fixture.Task.Properties,
	}).Error)
	var recovery map[string]any
	require.NoError(t, common.Unmarshal([]byte(fixture.Job.NativeTaskRecoveryJSON), &recovery))
	recovery["request_json_sha256"] = fmt.Sprintf("sha256:%x", requestDigest)
	recovery["platform"], recovery["properties"] = fixture.Task.Platform, fixture.Task.Properties
	rawRecovery, err := common.Marshal(recovery)
	require.NoError(t, err)
	require.NoError(t, model.DB.Model(fixture.Route).Updates(map[string]any{
		"model": providerModel.PublicModelID, "upstream_model": modelID, "provider_name": "minimax",
		"accepted_channel_type": profile.NativeChannelType, "capability_profile_id": profile.ID,
		"capability_profile_revision": profile.Revision,
		"capability_profile_snapshot": request.Metadata[generationprofile.MetadataProfileSnapshot],
		"model_release_id":            "test-minimax-h3-release-v1",
	}).Error)
	require.NoError(t, model.DB.Model(fixture.Job).Updates(map[string]any{
		"model": providerModel.PublicModelID, "request_json": string(rawRequest),
		"native_task_recovery_json": string(rawRecovery), "outputs_json": "[]", "error_details_json": "{}",
	}).Error)
	require.NoError(t, model.DB.First(fixture.Route, fixture.Route.ID).Error)
	require.NoError(t, model.DB.First(fixture.Job, "id = ?", fixture.Job.ID).Error)
	require.NoError(t, model.DB.First(fixture.Task, fixture.Task.ID).Error)
	return fixture
}

type miniMaxH3RecordingPollAdaptor struct {
	protectedTaskPollingAdaptor
	providerModel         string
	providerModelProvided bool
	providerKey           string
}

func (a *miniMaxH3RecordingPollAdaptor) FetchTaskWithContext(ctx context.Context, baseURL string, key string, body map[string]any, proxy string) (*http.Response, error) {
	a.providerModel, a.providerModelProvided = body["provider_model"].(string)
	a.providerKey = key
	return a.protectedTaskPollingAdaptor.FetchTaskWithContext(ctx, baseURL, key, body, proxy)
}

func miniMaxH3PollingResult(fixture protectedNativeBindingFixture, status string, sourceURL string) *relaycommon.TaskInfo {
	proof := &relaycommon.ProviderTaskResultProof{
		SchemaVersion: 1, Protocol: generationprofile.MiniMaxH3VideoProtocolV2,
		TaskID: fixture.Task.GetUpstreamTaskID(), Model: fixture.Route.UpstreamModel, ProviderStatus: status,
	}
	result := &relaycommon.TaskInfo{TaskID: proof.TaskID, ProviderResultProof: proof}
	switch status {
	case "succeeded":
		proof.Resolution, proof.DurationSeconds, proof.AspectRatio = "768p", 5, "16:9"
		proof.OutputCount, proof.MediaType = 1, "video"
		proof.ProviderTotalSeconds, proof.OutputSeconds = 5, 5
		result.Status, result.Progress, result.Url = string(model.TaskStatusSuccess), "100%", sourceURL
	case "queued":
		result.Status, result.Progress = string(model.TaskStatusQueued), "10%"
	case "running":
		result.Status, result.Progress = string(model.TaskStatusInProgress), "50%"
	case "failed":
		proof.FailureOwner, proof.FailureCode = model.PlatformProviderFailureOwnerClient, "content_policy_rejected"
		result.Status, result.Progress = string(model.TaskStatusFailure), "100%"
	}
	return result
}

func pollMiniMaxH3Fixture(t *testing.T, fixture protectedNativeBindingFixture, result *relaycommon.TaskInfo) *miniMaxH3RecordingPollAdaptor {
	t.Helper()
	adaptor := &miniMaxH3RecordingPollAdaptor{protectedTaskPollingAdaptor: protectedTaskPollingAdaptor{
		body: []byte(`{"task":{"id":"fixture","private":"never persist raw provider fields"}}`), result: result,
	}}
	baseURL := "https://api.minimax.cn"
	require.NoError(t, updateVideoSingleTask(context.Background(), adaptor,
		&model.Channel{Id: fixture.Task.ChannelId, Type: constant.ChannelTypeMiniMax, Key: "rotated-channel-key", BaseURL: &baseURL},
		fixture.Task.GetUpstreamTaskID(), map[string]*model.Task{fixture.Task.GetUpstreamTaskID(): fixture.Task},
	))
	return adaptor
}

func TestMiniMaxH3PollUsesPinnedModelAndCredentialWithSecretFreeReceipts(t *testing.T) {
	for _, modelID := range []string{"MiniMax-H3", "MiniMax-H3-Max"} {
		t.Run(modelID, func(t *testing.T) {
			truncate(t)
			fixture := seedProtectedMiniMaxH3PollingBinding(t, modelID)
			providerURL := "https://media.example/h3.mp4?signature=private-provider-token"
			adaptor := pollMiniMaxH3Fixture(t, fixture, miniMaxH3PollingResult(fixture, "succeeded", providerURL))
			assert.Equal(t, modelID, adaptor.providerModel)
			assert.Equal(t, "provider-key", adaptor.providerKey, "rotation must not change an accepted task's credential version")
			var persisted model.Task
			require.NoError(t, model.DB.First(&persisted, fixture.Task.ID).Error)
			assert.Equal(t, model.TaskStatus(model.TaskStatusSuccess), persisted.Status)
			assert.Zero(t, persisted.Quota, "Platform remains the only customer billing owner")
			assert.Empty(t, persisted.GetPublicResultURL(), "a temporary provider locator is never a public output")
			receipt, err := model.DecodePlatformGenerationProviderResultReceipt(persisted.Data)
			require.NoError(t, err)
			assert.Equal(t, model.PlatformGenerationMiniMaxH3ProviderResultProofContractRevision, receipt.ProofContractRevision)
			assert.Equal(t, generationprofile.MiniMaxH3VideoProtocolV2, receipt.Protocol)
			assert.NotContains(t, string(persisted.Data), "private-provider-token")
			assert.NotContains(t, string(persisted.Data), "never persist raw")
			binding, err := model.ResolvePlatformGenerationProviderResultBinding(persisted)
			require.NoError(t, err)
			require.NoError(t, model.ValidatePlatformGenerationProviderResultReceipt(persisted, binding, receipt))
			receipt.ProofContractRevision = model.PlatformGenerationProviderResultProofContractRevision
			assert.Error(t, model.ValidatePlatformGenerationProviderResultReceipt(persisted, binding, receipt), "Ark proof revisions cannot attest H3 results")
		})
	}
}

func TestMiniMaxH3PollingKeepsLegacyTasksWithoutModelSnapshot(t *testing.T) {
	truncate(t)
	task := seedPollingTask(t, 35, "legacy-hailuo-task", "legacy-provider-task")
	require.Empty(t, task.Properties.UpstreamModelName)
	adaptor := &miniMaxH3RecordingPollAdaptor{protectedTaskPollingAdaptor: protectedTaskPollingAdaptor{
		body:   []byte(`{"status":"Processing"}`),
		result: &relaycommon.TaskInfo{TaskID: task.GetUpstreamTaskID(), Status: model.TaskStatusInProgress, Progress: "50%"},
	}}
	require.NoError(t, updateVideoSingleTask(context.Background(), adaptor,
		&model.Channel{Id: 35, Type: constant.ChannelTypeMiniMax, Key: "legacy-fixture-key"},
		task.GetUpstreamTaskID(), map[string]*model.Task{task.GetUpstreamTaskID(): task},
	))
	assert.False(t, adaptor.providerModelProvided, "a missing historical model must not become an explicit invalid empty identity")
	assert.Equal(t, "legacy-fixture-key", adaptor.providerKey)
	var persisted model.Task
	require.NoError(t, model.DB.First(&persisted, task.ID).Error)
	assert.Equal(t, model.TaskStatus(model.TaskStatusInProgress), persisted.Status)
}

func TestMiniMaxH3PollHandlesProgressAndFailureWithoutNativeBilling(t *testing.T) {
	for _, status := range []string{"queued", "running", "failed"} {
		t.Run(status, func(t *testing.T) {
			truncate(t)
			fixture := seedProtectedMiniMaxH3PollingBinding(t, "MiniMax-H3")
			result := miniMaxH3PollingResult(fixture, status, "")
			pollMiniMaxH3Fixture(t, fixture, result)
			var persisted model.Task
			require.NoError(t, model.DB.First(&persisted, fixture.Task.ID).Error)
			assert.Equal(t, result.Status, string(persisted.Status))
			assert.Empty(t, persisted.PrivateData.ResultURL)
			assert.Zero(t, persisted.Quota)
		})
	}
}

func TestMiniMaxH3PollConflictingEvidenceRequiresReconciliation(t *testing.T) {
	mutations := map[string]func(*relaycommon.ProviderTaskResultProof){
		"legacy protocol": func(p *relaycommon.ProviderTaskResultProof) {
			p.Protocol = generationprofile.VolcengineArkVideoProtocolV1
		},
		"other model":      func(p *relaycommon.ProviderTaskResultProof) { p.Model = "MiniMax-H3-Max" },
		"other task":       func(p *relaycommon.ProviderTaskResultProof) { p.TaskID = "different-task" },
		"other resolution": func(p *relaycommon.ProviderTaskResultProof) { p.Resolution = "2k" },
		"other duration":   func(p *relaycommon.ProviderTaskResultProof) { p.DurationSeconds = 6 },
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			truncate(t)
			fixture := seedProtectedMiniMaxH3PollingBinding(t, "MiniMax-H3")
			result := miniMaxH3PollingResult(fixture, "succeeded", "https://media.example/h3.mp4?signature=private")
			mutate(result.ProviderResultProof)
			pollMiniMaxH3Fixture(t, fixture, result)
			var persisted model.Task
			require.NoError(t, model.DB.First(&persisted, fixture.Task.ID).Error)
			assert.Equal(t, model.TaskStatus(model.TaskStatusUnknown), persisted.Status)
			assert.True(t, model.PlatformGenerationProviderProofConflictRequired(persisted.Data))
			assert.Contains(t, string(persisted.Data), model.PlatformGenerationMiniMaxH3ProviderResultProofContractRevision)
			assert.Empty(t, persisted.PrivateData.ResultURL)
			var admission model.PlatformGenerationRouteAdmission
			require.NoError(t, model.DB.First(&admission, fixture.Admission.ID).Error)
			assert.True(t, admission.SlotHeld, "uncertain provider evidence cannot free a slot or settle a job")
		})
	}
}

func claimMiniMaxH3FixtureTransfer(t *testing.T, sourceURL string) (*model.PlatformGenerationJob, string) {
	t.Helper()
	require.NoError(t, model.DB.AutoMigrate(model.PlatformProviderMonitorAndCostModels()...))
	fixture := seedProtectedMiniMaxH3PollingBinding(t, "MiniMax-H3")
	state := &model.PlatformGenerationProviderAccountState{
		ChannelID: fixture.Route.ChannelID, KeyIndex: fixture.Route.KeyIndex, KeyFingerprint: fixture.Route.KeyFingerprint,
		RPMWindowSeconds: 60, RPMLimit: 1, ActiveCount: 1, ActiveLimit: 1,
	}
	require.NoError(t, model.DB.Create(state).Error)
	require.NoError(t, model.DB.Model(fixture.Route).Updates(map[string]any{"account_state_id": state.ID, "active_count": 1}).Error)
	pollMiniMaxH3Fixture(t, fixture, miniMaxH3PollingResult(fixture, "succeeded", sourceURL))
	processed, err := RunPlatformGenerationPollOnce(context.Background())
	require.NoError(t, err)
	require.True(t, processed)
	claimed, token, err := model.ClaimPlatformGenerationTransfer(time.Minute)
	require.NoError(t, err)
	require.NotNil(t, claimed)
	return claimed, token
}

func miniMaxH3ArtifactFixture(t *testing.T) (*PlatformArtifactDownloader, string) {
	t.Helper()
	payload := platformArtifactValidMP4Fixture(t)
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "video/mp4")
		_, _ = w.Write(payload)
	}))
	t.Cleanup(server.Close)
	downloader, sourceURL, _ := artifactTLSTestDownloader(t, server,
		PlatformArtifactDownloadConfig{MaxBytes: int64(len(payload)) + 1, Timeout: 5 * time.Second},
		[]net.IPAddr{{IP: net.ParseIP("8.8.8.8")}},
	)
	return downloader, sourceURL
}

func TestMiniMaxH3WorkerRetainsProofThroughPrivateArtifactTransfer(t *testing.T) {
	truncate(t)
	downloader, sourceURL := miniMaxH3ArtifactFixture(t)
	claimed, token := claimMiniMaxH3FixtureTransfer(t, sourceURL)
	assert.Contains(t, claimed.TemporaryResultJSON, model.PlatformGenerationMiniMaxH3ProviderResultProofContractRevision)

	// Reject a manifest that swaps the proof family before any network/storage.
	wrong := *claimed
	wrong.TemporaryResultJSON = strings.ReplaceAll(wrong.TemporaryResultJSON,
		model.PlatformGenerationMiniMaxH3ProviderResultProofContractRevision, model.PlatformGenerationProviderResultProofContractRevision)
	assert.ErrorIs(t, transferClaimedPlatformGeneration(context.Background(), wrong, token, nil, nil), model.ErrPlatformGenerationProviderMaterialReconciliationRequired)

	store := &fencingPlatformArtifactStore{jobID: claimed.ID}
	err := transferClaimedPlatformGeneration(context.Background(), *claimed, token, downloader, store)
	require.ErrorIs(t, err, errPlatformGenerationTransferLeaseFenced, "a verified H3 result reaches the real download/storage path and still obeys the commit fence")
	require.NotEmpty(t, store.putObjectKey)
	processed, err := runPlatformArtifactCleanupOnce(context.Background(), store, 3)
	require.NoError(t, err)
	assert.True(t, processed)
	assert.Equal(t, store.putObjectKey, store.deletedObjectKey)
	var persisted model.PlatformGenerationJob
	require.NoError(t, model.DB.First(&persisted, "id = ?", claimed.ID).Error)
	assert.Equal(t, model.PlatformGenerationStatusTransferring, persisted.Status)
	assert.Equal(t, "[]", persisted.OutputsJSON, "fenced storage cannot publish a customer artifact")
}

func TestMiniMaxH3WorkerPublishesOnlyVerifiedStoredArtifact(t *testing.T) {
	truncate(t)
	downloader, sourceURL := miniMaxH3ArtifactFixture(t)
	claimed, token := claimMiniMaxH3FixtureTransfer(t, sourceURL)
	store, err := NewPlatformFilesystemArtifactStore(t.TempDir(), "https://studio.example", []byte(strings.Repeat("s", 32)))
	require.NoError(t, err)
	require.NoError(t, transferClaimedPlatformGeneration(context.Background(), *claimed, token, downloader, store))
	var persisted model.PlatformGenerationJob
	require.NoError(t, model.DB.First(&persisted, "id = ?", claimed.ID).Error)
	assert.Equal(t, model.PlatformGenerationStatusSucceeded, persisted.Status)
	assert.Empty(t, persisted.TemporaryResultJSON)
	assert.Empty(t, persisted.UpstreamResultURL)
	var artifacts []dto.PlatformGenerationArtifact
	require.NoError(t, common.Unmarshal([]byte(persisted.OutputsJSON), &artifacts))
	require.Len(t, artifacts, 1)
	assert.Equal(t, "video/mp4", artifacts[0].ContentType)
	assert.NotEmpty(t, artifacts[0].ObjectKey)
	assert.NotEmpty(t, artifacts[0].SHA256)
	assert.Positive(t, artifacts[0].SizeBytes)
	assert.NotContains(t, persisted.OutputsJSON, sourceURL)
	var native model.Task
	require.NoError(t, model.DB.Where("task_id = ?", persisted.NativeTaskID).First(&native).Error)
	assert.Zero(t, native.Quota)
	assert.Empty(t, native.PrivateData.ResultURL, "provider material is scrubbed only after storage commits")
}

func TestMiniMaxH3WorkerMissingProofRetainsEvidenceForReconciliation(t *testing.T) {
	for _, missing := range []string{"receipt", "profile"} {
		t.Run(missing, func(t *testing.T) {
			truncate(t)
			sourceURL := "https://artifacts.example/h3.mp4?signature=retained-private-evidence"
			claimed, token := claimMiniMaxH3FixtureTransfer(t, sourceURL)
			if missing == "receipt" {
				require.NoError(t, model.DB.Model(&model.Task{}).Where("task_id = ?", claimed.NativeTaskID).Update("data", []byte("{}")).Error)
			} else {
				var request dto.PlatformGenerationRequest
				require.NoError(t, common.Unmarshal([]byte(claimed.RequestJSON), &request))
				delete(request.Metadata, generationprofile.MetadataProfileSnapshot)
				raw, err := common.Marshal(request)
				require.NoError(t, err)
				claimed.RequestJSON = string(raw)
				require.NoError(t, model.DB.Model(claimed).Update("request_json", claimed.RequestJSON).Error)
			}
			err := transferClaimedPlatformGeneration(context.Background(), *claimed, token, nil, nil)
			require.ErrorIs(t, err, model.ErrPlatformGenerationProviderMaterialReconciliationRequired)
			require.NoError(t, retryOrFailPlatformGenerationTransfer(*claimed, token, err))
			var persisted model.PlatformGenerationJob
			require.NoError(t, model.DB.First(&persisted, "id = ?", claimed.ID).Error)
			assert.Equal(t, model.PlatformGenerationStatusReconciliationRequired, persisted.Status)
			assert.Equal(t, model.PlatformGenerationProviderReconciliationKindProviderMaterial, model.PlatformGenerationProviderReconciliationKind(persisted))
			assert.Equal(t, "[]", persisted.OutputsJSON)
			assert.Contains(t, persisted.TemporaryResultJSON, sourceURL, "do not destroy the only evidence when proof is missing")
			assert.Empty(t, persisted.TransferLeaseToken)
			var native model.Task
			require.NoError(t, model.DB.Where("task_id = ?", persisted.NativeTaskID).First(&native).Error)
			assert.Equal(t, sourceURL, native.PrivateData.ResultURL)
			assert.Zero(t, native.Quota)
		})
	}
}
