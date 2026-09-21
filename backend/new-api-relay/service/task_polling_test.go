package service

import (
	"bytes"
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	taskdto "github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/bytedance/gopkg/util/gopool"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

type taskPollingFetchAdaptor struct {
	mu           sync.Mutex
	taskIDs      []string
	fetched      chan string
	blockTaskID  string
	blockStarted chan struct{}
	releaseBlock chan struct{}
	blockOnce    sync.Once
}

type sunoFailurePollingAdaptor struct {
	failReason string
}

type protectedTaskPollingAdaptor struct {
	body            []byte
	statusCode      int
	missingResponse bool
	missingBody     bool
	result          *relaycommon.TaskInfo
	parseError      error
}

func (a *protectedTaskPollingAdaptor) Init(_ *relaycommon.RelayInfo) {}

func (a *protectedTaskPollingAdaptor) FetchTask(_ string, _ string, body map[string]any, _ string) (*http.Response, error) {
	return a.FetchTaskWithContext(context.Background(), "", "", body, "")
}

func (a *protectedTaskPollingAdaptor) FetchTaskWithContext(_ context.Context, _ string, _ string, _ map[string]any, _ string) (*http.Response, error) {
	if a.missingResponse {
		return nil, nil
	}
	statusCode := a.statusCode
	if statusCode == 0 {
		statusCode = http.StatusOK
	}
	if a.missingBody {
		return &http.Response{StatusCode: statusCode}, nil
	}
	return &http.Response{
		StatusCode: statusCode,
		Body:       io.NopCloser(bytes.NewReader(a.body)),
	}, nil
}

func (a *protectedTaskPollingAdaptor) ParseTaskResult([]byte) (*relaycommon.TaskInfo, error) {
	if a.parseError != nil {
		return nil, a.parseError
	}
	return a.result, nil
}

func (a *protectedTaskPollingAdaptor) AdjustBillingOnComplete(_ *model.Task, _ *relaycommon.TaskInfo) int {
	return 0
}

func (a *sunoFailurePollingAdaptor) Init(_ *relaycommon.RelayInfo) {}

func (a *sunoFailurePollingAdaptor) FetchTask(_ string, _ string, body map[string]any, _ string) (*http.Response, error) {
	return a.FetchTaskWithContext(context.Background(), "", "", body, "")
}

func (a *sunoFailurePollingAdaptor) FetchTaskWithContext(_ context.Context, _ string, _ string, body map[string]any, _ string) (*http.Response, error) {
	taskIDs, _ := body["ids"].([]string)
	items := make([]taskdto.SunoDataResponse, 0, len(taskIDs))
	for _, taskID := range taskIDs {
		items = append(items, taskdto.SunoDataResponse{
			TaskID:     taskID,
			Status:     string(model.TaskStatusFailure),
			FailReason: a.failReason,
			FinishTime: time.Now().Unix(),
		})
	}

	responseBody, err := common.Marshal(taskdto.TaskResponse[[]taskdto.SunoDataResponse]{
		Code: taskdto.TaskSuccessCode,
		Data: items,
	})
	if err != nil {
		return nil, err
	}
	return &http.Response{
		StatusCode: http.StatusOK,
		Body:       io.NopCloser(bytes.NewReader(responseBody)),
	}, nil
}

func (a *sunoFailurePollingAdaptor) ParseTaskResult([]byte) (*relaycommon.TaskInfo, error) {
	return nil, nil
}

func (a *sunoFailurePollingAdaptor) AdjustBillingOnComplete(_ *model.Task, _ *relaycommon.TaskInfo) int {
	return 0
}

func (a *taskPollingFetchAdaptor) Init(_ *relaycommon.RelayInfo) {}

func (a *taskPollingFetchAdaptor) FetchTask(_ string, _ string, body map[string]any, _ string) (*http.Response, error) {
	return a.FetchTaskWithContext(context.Background(), "", "", body, "")
}

func (a *taskPollingFetchAdaptor) FetchTaskWithContext(ctx context.Context, _ string, _ string, body map[string]any, _ string) (*http.Response, error) {
	taskID, _ := body["task_id"].(string)
	if taskID == a.blockTaskID && a.releaseBlock != nil {
		a.blockOnce.Do(func() {
			if a.blockStarted != nil {
				close(a.blockStarted)
			}
		})
		select {
		case <-a.releaseBlock:
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}

	a.mu.Lock()
	a.taskIDs = append(a.taskIDs, taskID)
	a.mu.Unlock()
	if a.fetched != nil {
		select {
		case a.fetched <- taskID:
		default:
		}
	}

	response := taskdto.TaskResponse[model.Task]{
		Code: taskdto.TaskSuccessCode,
		Data: model.Task{
			TaskID:   taskID,
			Status:   model.TaskStatusInProgress,
			Progress: "30%",
		},
	}
	responseBody, err := common.Marshal(response)
	if err != nil {
		return nil, err
	}
	return &http.Response{
		StatusCode: http.StatusOK,
		Body:       io.NopCloser(bytes.NewReader(responseBody)),
	}, nil
}

func (a *taskPollingFetchAdaptor) ParseTaskResult([]byte) (*relaycommon.TaskInfo, error) {
	return &relaycommon.TaskInfo{Status: model.TaskStatusInProgress}, nil
}

func (a *taskPollingFetchAdaptor) AdjustBillingOnComplete(_ *model.Task, _ *relaycommon.TaskInfo) int {
	return 0
}

func (a *taskPollingFetchAdaptor) fetchCount() int {
	a.mu.Lock()
	defer a.mu.Unlock()
	return len(a.taskIDs)
}

func (a *taskPollingFetchAdaptor) fetchedTaskIDs() []string {
	a.mu.Lock()
	defer a.mu.Unlock()
	return append([]string(nil), a.taskIDs...)
}

func seedTaskPollingChannel(t *testing.T, id int, disableSleep bool) {
	t.Helper()
	ch := &model.Channel{
		Id:     id,
		Type:   constant.ChannelTypeKling,
		Name:   "polling_channel",
		Key:    "sk-test",
		Status: common.ChannelStatusEnabled,
	}
	if disableSleep {
		ch.SetOtherSettings(dto.ChannelOtherSettings{DisableTaskPollingSleep: true})
	}
	require.NoError(t, model.DB.Create(ch).Error)
}

func seedPollingTask(t *testing.T, channelID int, publicID string, upstreamID string) *model.Task {
	t.Helper()
	task := &model.Task{
		TaskID:    publicID,
		Platform:  constant.TaskPlatform("kling"),
		UserId:    1,
		ChannelId: channelID,
		Action:    constant.TaskActionGenerate,
		Status:    model.TaskStatusInProgress,
		Progress:  "30%",
		CreatedAt: time.Now().Unix(),
		UpdatedAt: time.Now().Unix(),
		PrivateData: model.TaskPrivateData{
			UpstreamTaskID: upstreamID,
		},
	}
	require.NoError(t, model.DB.Create(task).Error)
	return task
}

func seedProtectedSeedancePollingBinding(t *testing.T) protectedNativeBindingFixture {
	t.Helper()
	t.Setenv("APP_ENV", "test")
	t.Setenv("DEPLOYMENT_ENV", "test")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE", "")
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON", `{"schema_version":1,"active_key_id":"poll-proof-v1","keys":{"poll-proof-v1":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}}`)
	fixture := seedProtectedExternalNativeBinding(
		t,
		model.PlatformGenerationStatusProcessing,
		model.TaskStatusInProgress,
		true,
	)
	profile, ok := generationprofile.Get(generationprofile.VolcengineArkVideoGenerationV1)
	require.True(t, ok)
	providerKey := "provider-key"
	providerKeyDigest := sha256.Sum256([]byte(providerKey))
	providerKeyFingerprint := fmt.Sprintf("%x", providerKeyDigest)
	require.NoError(t, model.DB.Delete(fixture.Credential).Error)
	fixture.Route.KeyFingerprint = providerKeyFingerprint
	fixture.Task.PrivateData.PinnedKeyFingerprint = providerKeyFingerprint
	fixture.Task.PrivateData.ProviderCredentialTenantID = ""
	fixture.Task.PrivateData.ProviderCredentialVersion = ""
	fixture.Task.PrivateData.TransientProviderKey = providerKey
	require.NoError(t, model.BindTaskProviderCredentialVersion(fixture.Task, fixture.Job.TenantID))
	require.NoError(t, model.DB.Model(fixture.Task).Update("private_data", fixture.Task.PrivateData).Error)
	fixture.Credential = &model.ProviderCredentialVersion{}
	require.NoError(t, model.DB.Where(
		"credential_version = ?",
		fixture.Task.PrivateData.ProviderCredentialVersion,
	).First(fixture.Credential).Error)
	request := taskdto.NewPlatformGenerationRequest()
	request.Model = fixture.Route.Model
	request.Mode = fixture.Route.Mode
	request.ExpectedCapabilityRevision = "sha256:" + strings.Repeat("c", 64)
	request.Inputs.Prompt = "A safe proof fixture"
	request.Output.DurationSeconds = 5
	request.Output.AspectRatio = "16:9"
	request.Output.Resolution = "720p"
	request.Output.Count = 1
	request.Metadata = generationprofile.SnapshotMetadata(request.Metadata, profile)
	require.NoError(t, profile.ValidateRequest(request))
	rawRequest, err := common.Marshal(request)
	require.NoError(t, err)
	requestDigest := sha256.Sum256(rawRequest)

	var recovery map[string]any
	require.NoError(t, common.Unmarshal([]byte(fixture.Job.NativeTaskRecoveryJSON), &recovery))
	recovery["schema_version"] = 4
	recovery["request_json_sha256"] = fmt.Sprintf("sha256:%x", requestDigest)
	recovery["pinned_key_fingerprint"] = providerKeyFingerprint
	recovery["provider_credential_version"] = fixture.Task.PrivateData.ProviderCredentialVersion
	rawRecovery, err := common.Marshal(recovery)
	require.NoError(t, err)
	profileSnapshot, _ := request.Metadata[generationprofile.MetadataProfileSnapshot].(string)
	require.NoError(t, model.DB.Model(fixture.Route).Updates(map[string]any{
		"key_fingerprint":                   providerKeyFingerprint,
		"accepted_channel_type":             profile.NativeChannelType,
		"capability_profile_id":             profile.ID,
		"capability_profile_revision":       profile.Revision,
		"capability_profile_snapshot":       profileSnapshot,
		"model_release_id":                  "test-seedance-release-v1",
		"model_release_revision":            "sha256:" + strings.Repeat("d", 64),
		"model_release_capability_revision": request.ExpectedCapabilityRevision,
	}).Error)
	require.NoError(t, model.DB.Model(fixture.Job).Updates(map[string]any{
		"request_json":                 string(rawRequest),
		"native_task_recovery_json":    string(rawRecovery),
		"expected_capability_revision": request.ExpectedCapabilityRevision,
		"capability_revision":          request.ExpectedCapabilityRevision,
	}).Error)
	require.NoError(t, model.DB.First(fixture.Route, fixture.Route.ID).Error)
	require.NoError(t, model.DB.First(fixture.Job, "id = ?", fixture.Job.ID).Error)
	require.NoError(t, model.DB.First(fixture.Task, fixture.Task.ID).Error)
	return fixture
}

func seedanceTerminalProof(taskID string, modelID string, status string) *relaycommon.ProviderTaskResultProof {
	proof := &relaycommon.ProviderTaskResultProof{
		SchemaVersion:  1,
		Protocol:       generationprofile.VolcengineArkVideoProtocolV1,
		TaskID:         taskID,
		Model:          modelID,
		ProviderStatus: status,
	}
	if status == "succeeded" {
		proof.Resolution = "720p"
		proof.DurationSeconds = 5
		proof.AspectRatio = "16:9"
		proof.OutputCount = 1
		proof.MediaType = "video"
	} else {
		proof.FailureOwner = model.PlatformProviderFailureOwnerRelay
		proof.FailureCode = "unclassified_provider_terminal"
	}
	return proof
}

func TestRedactVideoResponseBodyProtectsPlatformWithoutChangingOrdinaryMetadata(t *testing.T) {
	providerURL := "https://provider.example/result.mp4?X-Signature=secret"
	documentationURL := "https://docs.example/provider-contract"
	body := []byte(`{"id":"provider-task","status":"succeeded","content":{"video_url":"` + providerURL + `"},"metadata":{"documentation_url":"` + documentationURL + `"}}`)

	protected := redactVideoResponseBody(body, true)
	require.NotContains(t, string(protected), providerURL)
	require.NotContains(t, string(protected), "X-Signature")
	require.NotContains(t, string(protected), documentationURL)
	require.Contains(t, string(protected), "[redacted-provider-url]")

	ordinary := redactVideoResponseBody(body, false)
	require.Contains(t, string(ordinary), providerURL)
	require.Contains(t, string(ordinary), documentationURL)
	require.Nil(t, redactVideoResponseBody([]byte(`{"broken"`), true),
		"an unparsable provider body must fail closed instead of being persisted raw")
}

func TestPlatformExternalPollPersistsOnlySecretFreeReceipt(t *testing.T) {
	truncate(t)
	fixture := seedProtectedSeedancePollingBinding(t)
	task := fixture.Task
	upstreamTaskID := task.PrivateData.UpstreamTaskID
	providerURL := "https://provider.example/result.mp4?X-Signature=poll-secret"
	body := []byte(`{"id":"` + upstreamTaskID + `","model":"` + fixture.Route.UpstreamModel + `","status":"succeeded","resolution":"720p","duration":"5","ratio":"16:9","content":{"video_url":"` + providerURL + `"},"usage":{"total_tokens":42}}`)
	adaptor := &protectedTaskPollingAdaptor{
		body: body,
		result: &relaycommon.TaskInfo{
			TaskID: upstreamTaskID, Status: string(model.TaskStatusSuccess), Progress: "100%", Url: providerURL,
			ProviderResultProof: seedanceTerminalProof(upstreamTaskID, fixture.Route.UpstreamModel, "succeeded"),
		},
	}
	channel := &model.Channel{Id: task.ChannelId, Key: "provider-key"}

	require.NoError(t, updateVideoSingleTask(
		context.Background(),
		adaptor,
		channel,
		upstreamTaskID,
		map[string]*model.Task{upstreamTaskID: task},
	))
	var persisted model.Task
	require.NoError(t, model.DB.First(&persisted, task.ID).Error)
	require.Equal(t, model.TaskStatus(model.TaskStatusSuccess), persisted.Status)
	require.Equal(t, providerURL, persisted.PrivateData.ResultURL,
		"the internal transfer worker still needs the short-lived provider URL")
	receipt, err := model.DecodePlatformGenerationProviderResultReceipt(persisted.Data)
	require.NoError(t, err)
	require.Equal(t, model.PlatformGenerationProviderResultProofContractRevision, receipt.ProofContractRevision)
	require.Contains(t, receipt.ResponseBodySHA256, "sha256:")
	require.Contains(t, receipt.ArtifactURLSHA256, "sha256:")
	require.NotContains(t, string(persisted.Data), providerURL)
	require.NotContains(t, string(persisted.Data), "X-Signature")
	require.Empty(t, persisted.GetPublicResultURL())
	serialized, err := common.Marshal(persisted)
	require.NoError(t, err)
	require.NotContains(t, string(serialized), providerURL)
	require.NotContains(t, string(serialized), "X-Signature")
}

func TestPlatformGoogleStickyPollRejectsTransportDriftBeforeProviderRequest(t *testing.T) {
	for _, test := range []struct {
		name   string
		update map[string]any
	}{
		{name: "base URL", update: map[string]any{"base_url": "https://credential-sink.example"}},
		{name: "proxy", update: map[string]any{"setting": `{"proxy":"http://proxy.example:3128"}`}},
	} {
		t.Run(test.name, func(t *testing.T) {
			truncate(t)
			t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
			t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
			baseURL := "https://generativelanguage.googleapis.com"
			setting := `{}`
			channel := &model.Channel{
				Id: 724, Type: constant.ChannelTypeGemini, Name: "sticky-google-poll",
				Key: "google-secret", Status: common.ChannelStatusEnabled,
				BaseURL: &baseURL, Setting: &setting,
			}
			require.NoError(t, model.DB.Create(channel).Error)
			require.NoError(t, model.DB.First(channel, channel.Id).Error)
			revision, digest, err := resolvePlatformRouteTransportBinding(channel)
			require.NoError(t, err)
			task := &model.Task{
				TaskID: "task_google_sticky_transport", ChannelId: channel.Id,
				Status: model.TaskStatusInProgress, Progress: "30%",
				PrivateData: model.TaskPrivateData{
					BillingSource:             model.TaskBillingSourcePlatformExternal,
					UpstreamTaskID:            "google-operation-sticky",
					ProviderTransportRevision: revision,
					ProviderTransportSHA256:   digest,
				},
			}
			require.NoError(t, model.DB.Session(&gorm.Session{SkipHooks: true}).Model(&model.Channel{}).
				Where("id = ?", channel.Id).Updates(test.update).Error)
			adaptor := &taskPollingFetchAdaptor{}
			err = updateVideoSingleTask(context.Background(), adaptor, channel,
				task.PrivateData.UpstreamTaskID,
				map[string]*model.Task{task.PrivateData.UpstreamTaskID: task},
			)
			require.ErrorContains(t, err, "transport binding changed")
			require.Zero(t, adaptor.fetchCount(), "transport drift must fail before any provider poll")
		})
	}
}

func TestPlatformExternalPollParseAndFailureEvidenceAreSecretFree(t *testing.T) {
	providerURL := "https://provider.example/result.mp4?X-Signature=parse-secret"
	body := []byte(`{"status":"failed","error":"inspect ` + providerURL + `"}`)
	t.Run("parse error", func(t *testing.T) {
		truncate(t)
		fixture := seedProtectedSeedancePollingBinding(t)
		task := fixture.Task
		adaptor := &protectedTaskPollingAdaptor{
			body: body, parseError: errors.New("provider payload contained " + providerURL),
		}
		err := updateVideoSingleTask(
			context.Background(), adaptor, &model.Channel{Id: task.ChannelId, Key: "provider-key"},
			task.PrivateData.UpstreamTaskID, map[string]*model.Task{task.PrivateData.UpstreamTaskID: task},
		)
		require.NoError(t, err)
		var persisted model.Task
		require.NoError(t, model.DB.First(&persisted, task.ID).Error)
		require.Equal(t, model.TaskStatus(model.TaskStatusUnknown), persisted.Status)
		require.True(t, model.PlatformGenerationProviderProofConflictRequired(persisted.Data))
		require.NotContains(t, string(persisted.Data), providerURL)
	})

	t.Run("failure reason", func(t *testing.T) {
		truncate(t)
		fixture := seedProtectedSeedancePollingBinding(t)
		task := fixture.Task
		upstreamTaskID := task.PrivateData.UpstreamTaskID
		adaptor := &protectedTaskPollingAdaptor{
			body: body,
			result: &relaycommon.TaskInfo{
				TaskID: upstreamTaskID, Status: string(model.TaskStatusFailure), Progress: "100%",
				Reason:              "provider rejected artifact at " + providerURL,
				ProviderResultProof: seedanceTerminalProof(upstreamTaskID, fixture.Route.UpstreamModel, "failed"),
			},
		}
		require.NoError(t, updateVideoSingleTask(
			context.Background(), adaptor, &model.Channel{Id: task.ChannelId, Key: "provider-key"},
			upstreamTaskID, map[string]*model.Task{upstreamTaskID: task},
		))
		var persisted model.Task
		require.NoError(t, model.DB.First(&persisted, task.ID).Error)
		require.Equal(t, "provider task failed", persisted.FailReason)
		require.NotContains(t, string(persisted.Data), providerURL)
		require.NotContains(t, string(persisted.Data), "X-Signature")
	})
}

func TestPlatformExternalPollRejectsUntrustedHTTPAndInlineTerminalResults(t *testing.T) {
	providerURL := "https://provider.example/result.mp4?X-Signature=false-success"
	falseSuccessBody := []byte(`{"id":"provider-task","status":"succeeded","content":{"video_url":"` + providerURL + `"}}`)
	for _, test := range []struct {
		name           string
		adaptor        *protectedTaskPollingAdaptor
		wantPersisted  model.TaskStatus
		wantFailReason string
	}{
		{
			name: "500 body shaped like success",
			adaptor: &protectedTaskPollingAdaptor{
				body: falseSuccessBody, statusCode: http.StatusInternalServerError,
				result: &relaycommon.TaskInfo{TaskID: "provider-task", Status: string(model.TaskStatusSuccess), Progress: "100%", Url: providerURL},
			},
			wantPersisted: model.TaskStatusInProgress,
		},
		{
			name:          "nil response",
			adaptor:       &protectedTaskPollingAdaptor{missingResponse: true},
			wantPersisted: model.TaskStatusInProgress,
		},
		{
			name:          "nil response body",
			adaptor:       &protectedTaskPollingAdaptor{missingBody: true},
			wantPersisted: model.TaskStatusInProgress,
		},
		{
			name: "inline data artifact",
			adaptor: &protectedTaskPollingAdaptor{
				body: []byte(`{"id":"provider-task","status":"succeeded","content":{"video_url":"data:video/mp4;base64,c2VjcmV0"}}`),
				result: &relaycommon.TaskInfo{
					TaskID: "provider-task", Status: string(model.TaskStatusSuccess), Progress: "100%", Url: "data:video/mp4;base64,c2VjcmV0",
				},
			},
			wantPersisted: model.TaskStatusUnknown,
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			truncate(t)
			task := &model.Task{
				CreatedAt: time.Now().UTC().Unix(), UpdatedAt: time.Now().UTC().Unix(),
				TaskID: "public-protected-boundary", ChannelId: 704, Status: model.TaskStatusInProgress,
				Progress: "30%", PrivateData: model.TaskPrivateData{
					BillingSource: model.TaskBillingSourcePlatformExternal, UpstreamTaskID: "provider-task",
				},
			}
			require.NoError(t, model.DB.Create(task).Error)
			err := updateVideoSingleTask(
				context.Background(), test.adaptor, &model.Channel{Id: task.ChannelId, Key: "provider-key"},
				"provider-task", map[string]*model.Task{"provider-task": task},
			)
			if test.wantPersisted == model.TaskStatusInProgress {
				require.Error(t, err)
				require.NotContains(t, err.Error(), providerURL)
				require.NotContains(t, err.Error(), "X-Signature")
			} else {
				require.NoError(t, err)
			}
			var persisted model.Task
			require.NoError(t, model.DB.First(&persisted, task.ID).Error)
			require.Equal(t, test.wantPersisted, persisted.Status)
			require.Equal(t, test.wantFailReason, persisted.FailReason)
			require.NotContains(t, string(persisted.Data), providerURL)
			require.NotContains(t, string(persisted.Data), "X-Signature")
			require.NotContains(t, persisted.PrivateData.ResultURL, "data:")
		})
	}
}

func TestPlatformExternalPollRejectsCrossTaskAndInconsistentTerminalEnvelope(t *testing.T) {
	providerURL := "https://cdn.example/B.mp4?sig=secret"
	for _, test := range []struct {
		name string
		body []byte
	}{
		{
			name: "cross-task success",
			body: []byte(`{"code":"success","data":{"task_id":"provider-B","status":"SUCCESS","progress":"100%","fail_reason":"` + providerURL + `"}}`),
		},
		{
			name: "terminal success with nonterminal progress",
			body: []byte(`{"code":"success","data":{"task_id":"provider-A","status":"SUCCESS","progress":"50%","fail_reason":"` + providerURL + `"}}`),
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			truncate(t)
			task := &model.Task{
				CreatedAt: time.Now().UTC().Unix(), UpdatedAt: time.Now().UTC().Unix(),
				TaskID: "public-platform-envelope", ChannelId: 705,
				Status: model.TaskStatusInProgress, Progress: "30%",
				PrivateData: model.TaskPrivateData{
					BillingSource:  model.TaskBillingSourcePlatformExternal,
					UpstreamTaskID: "provider-A",
				},
			}
			require.NoError(t, model.DB.Create(task).Error)
			adaptor := &protectedTaskPollingAdaptor{body: test.body}
			err := updateVideoSingleTask(
				context.Background(),
				adaptor,
				&model.Channel{Id: task.ChannelId, Key: "provider-key"},
				"provider-A",
				map[string]*model.Task{"provider-A": task},
			)
			require.NoError(t, err)
			var persisted model.Task
			require.NoError(t, model.DB.First(&persisted, task.ID).Error)
			require.Equal(t, model.TaskStatus(model.TaskStatusUnknown), persisted.Status)
			require.Equal(t, "0%", persisted.Progress)
			require.Empty(t, persisted.PrivateData.ResultURL)
			require.True(t, model.PlatformGenerationProviderProofConflictRequired(persisted.Data))
			require.NotContains(t, string(persisted.Data), providerURL)
		})
	}
}

func TestPlatformExternalPollRejectsOversizedProviderResponse(t *testing.T) {
	truncate(t)
	task := &model.Task{
		CreatedAt: time.Now().UTC().Unix(), UpdatedAt: time.Now().UTC().Unix(),
		TaskID: "public-platform-oversized", ChannelId: 706,
		Status: model.TaskStatusInProgress, Progress: "30%",
		PrivateData: model.TaskPrivateData{
			BillingSource:  model.TaskBillingSourcePlatformExternal,
			UpstreamTaskID: "provider-oversized",
		},
	}
	require.NoError(t, model.DB.Create(task).Error)
	adaptor := &protectedTaskPollingAdaptor{
		body: bytes.Repeat([]byte{'x'}, relaycommon.ProviderTaskResponseBodyLimit+1),
	}
	err := updateVideoSingleTask(
		context.Background(),
		adaptor,
		&model.Channel{Id: task.ChannelId, Key: "provider-key"},
		"provider-oversized",
		map[string]*model.Task{"provider-oversized": task},
	)
	require.Error(t, err)
	require.Contains(t, err.Error(), "exceeded the limit")
	var persisted model.Task
	require.NoError(t, model.DB.First(&persisted, task.ID).Error)
	require.Equal(t, model.TaskStatus(model.TaskStatusInProgress), persisted.Status)
	require.Empty(t, persisted.PrivateData.ResultURL)
	require.Empty(t, persisted.Data)
}

func TestUpdateVideoTasksDefaultSleepWaitsBetweenTasks(t *testing.T) {
	truncate(t)

	const channelID = 101
	seedTaskPollingChannel(t, channelID, false)
	first := seedPollingTask(t, channelID, "task_public_1", "upstream_1")
	second := seedPollingTask(t, channelID, "task_public_2", "upstream_2")

	adaptor := &taskPollingFetchAdaptor{}
	previousFactory := GetTaskAdaptorFunc
	GetTaskAdaptorFunc = func(constant.TaskPlatform) TaskPollingAdaptor { return adaptor }
	t.Cleanup(func() { GetTaskAdaptorFunc = previousFactory })

	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()

	err := UpdateVideoTasks(ctx, constant.TaskPlatform("kling"), map[int][]string{
		channelID: {
			first.GetUpstreamTaskID(),
			second.GetUpstreamTaskID(),
		},
	}, map[string]*model.Task{
		first.GetUpstreamTaskID():  first,
		second.GetUpstreamTaskID(): second,
	})

	require.ErrorIs(t, err, context.DeadlineExceeded)
	assert.Equal(t, 1, adaptor.fetchCount())
}

func TestUpdateVideoTasksCanSkipPollingSleepPerChannel(t *testing.T) {
	truncate(t)

	const channelID = 102
	seedTaskPollingChannel(t, channelID, true)
	first := seedPollingTask(t, channelID, "task_public_3", "upstream_3")
	second := seedPollingTask(t, channelID, "task_public_4", "upstream_4")

	adaptor := &taskPollingFetchAdaptor{}
	previousFactory := GetTaskAdaptorFunc
	GetTaskAdaptorFunc = func(constant.TaskPlatform) TaskPollingAdaptor { return adaptor }
	t.Cleanup(func() { GetTaskAdaptorFunc = previousFactory })

	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()

	err := UpdateVideoTasks(ctx, constant.TaskPlatform("kling"), map[int][]string{
		channelID: {
			first.GetUpstreamTaskID(),
			second.GetUpstreamTaskID(),
		},
	}, map[string]*model.Task{
		first.GetUpstreamTaskID():  first,
		second.GetUpstreamTaskID(): second,
	})

	require.NoError(t, err)
	assert.Equal(t, 2, adaptor.fetchCount())
}

func TestUpdateVideoTasksDefaultSleepDoesNotBlockOtherChannels(t *testing.T) {
	truncate(t)

	const firstChannelID = 201
	const secondChannelID = 202
	seedTaskPollingChannel(t, firstChannelID, false)
	seedTaskPollingChannel(t, secondChannelID, false)
	firstChannelFirst := seedPollingTask(t, firstChannelID, "task_public_5", "upstream_a_1")
	firstChannelSecond := seedPollingTask(t, firstChannelID, "task_public_6", "upstream_a_2")
	secondChannelFirst := seedPollingTask(t, secondChannelID, "task_public_7", "upstream_b_1")
	secondChannelSecond := seedPollingTask(t, secondChannelID, "task_public_8", "upstream_b_2")

	adaptor := &taskPollingFetchAdaptor{}
	previousFactory := GetTaskAdaptorFunc
	GetTaskAdaptorFunc = func(constant.TaskPlatform) TaskPollingAdaptor { return adaptor }
	t.Cleanup(func() { GetTaskAdaptorFunc = previousFactory })

	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	err := UpdateVideoTasks(ctx, constant.TaskPlatform("kling"), map[int][]string{
		firstChannelID: {
			firstChannelFirst.GetUpstreamTaskID(),
			firstChannelSecond.GetUpstreamTaskID(),
		},
		secondChannelID: {
			secondChannelFirst.GetUpstreamTaskID(),
			secondChannelSecond.GetUpstreamTaskID(),
		},
	}, map[string]*model.Task{
		firstChannelFirst.GetUpstreamTaskID():   firstChannelFirst,
		firstChannelSecond.GetUpstreamTaskID():  firstChannelSecond,
		secondChannelFirst.GetUpstreamTaskID():  secondChannelFirst,
		secondChannelSecond.GetUpstreamTaskID(): secondChannelSecond,
	})

	require.ErrorIs(t, err, context.DeadlineExceeded)
	assert.ElementsMatch(t, []string{"upstream_a_1", "upstream_b_1"}, adaptor.fetchedTaskIDs())
}

func TestUpdateVideoTasksSlowChannelDoesNotBlockOtherChannels(t *testing.T) {
	truncate(t)

	const slowChannelID = 251
	const fastChannelID = 252
	seedTaskPollingChannel(t, slowChannelID, false)
	seedTaskPollingChannel(t, fastChannelID, true)
	slowTask := seedPollingTask(t, slowChannelID, "task_public_slow", "upstream_slow_1")
	fastFirst := seedPollingTask(t, fastChannelID, "task_public_fast_1", "upstream_fast_parallel_1")
	fastSecond := seedPollingTask(t, fastChannelID, "task_public_fast_2", "upstream_fast_parallel_2")
	slowUpstreamID := slowTask.GetUpstreamTaskID()
	fastFirstUpstreamID := fastFirst.GetUpstreamTaskID()
	fastSecondUpstreamID := fastSecond.GetUpstreamTaskID()

	adaptor := &taskPollingFetchAdaptor{
		fetched:      make(chan string, 4),
		blockTaskID:  slowUpstreamID,
		blockStarted: make(chan struct{}),
		releaseBlock: make(chan struct{}),
	}
	var releaseOnce sync.Once
	releaseBlockedTask := func() {
		releaseOnce.Do(func() {
			close(adaptor.releaseBlock)
		})
	}
	t.Cleanup(releaseBlockedTask)
	previousFactory := GetTaskAdaptorFunc
	GetTaskAdaptorFunc = func(constant.TaskPlatform) TaskPollingAdaptor { return adaptor }
	t.Cleanup(func() { GetTaskAdaptorFunc = previousFactory })

	errCh := make(chan error, 1)
	gopool.Go(func() {
		errCh <- UpdateVideoTasks(context.Background(), constant.TaskPlatform("kling"), map[int][]string{
			slowChannelID: {
				slowUpstreamID,
			},
			fastChannelID: {
				fastFirstUpstreamID,
				fastSecondUpstreamID,
			},
		}, map[string]*model.Task{
			slowUpstreamID:       slowTask,
			fastFirstUpstreamID:  fastFirst,
			fastSecondUpstreamID: fastSecond,
		})
	})

	select {
	case <-adaptor.blockStarted:
	case <-time.After(500 * time.Millisecond):
		t.Fatal("slow channel did not start blocking")
	}

	require.Eventually(t, func() bool {
		fetchedTaskIDs := adaptor.fetchedTaskIDs()
		return len(fetchedTaskIDs) == 2 &&
			fetchedTaskIDs[0] == fastFirstUpstreamID &&
			fetchedTaskIDs[1] == fastSecondUpstreamID
	}, 500*time.Millisecond, 10*time.Millisecond)

	releaseBlockedTask()
	require.NoError(t, <-errCh)
	assert.ElementsMatch(t, []string{
		slowUpstreamID,
		fastFirstUpstreamID,
		fastSecondUpstreamID,
	}, adaptor.fetchedTaskIDs())
}

func TestUpdateVideoTasksMixedChannelSleepSettings(t *testing.T) {
	truncate(t)

	const sleepyChannelID = 301
	const fastChannelID = 302
	seedTaskPollingChannel(t, sleepyChannelID, false)
	seedTaskPollingChannel(t, fastChannelID, true)
	sleepyFirst := seedPollingTask(t, sleepyChannelID, "task_public_9", "upstream_sleepy_1")
	sleepySecond := seedPollingTask(t, sleepyChannelID, "task_public_10", "upstream_sleepy_2")
	fastFirst := seedPollingTask(t, fastChannelID, "task_public_11", "upstream_fast_1")
	fastSecond := seedPollingTask(t, fastChannelID, "task_public_12", "upstream_fast_2")

	adaptor := &taskPollingFetchAdaptor{}
	previousFactory := GetTaskAdaptorFunc
	GetTaskAdaptorFunc = func(constant.TaskPlatform) TaskPollingAdaptor { return adaptor }
	t.Cleanup(func() { GetTaskAdaptorFunc = previousFactory })

	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()

	err := UpdateVideoTasks(ctx, constant.TaskPlatform("kling"), map[int][]string{
		sleepyChannelID: {
			sleepyFirst.GetUpstreamTaskID(),
			sleepySecond.GetUpstreamTaskID(),
		},
		fastChannelID: {
			fastFirst.GetUpstreamTaskID(),
			fastSecond.GetUpstreamTaskID(),
		},
	}, map[string]*model.Task{
		sleepyFirst.GetUpstreamTaskID():  sleepyFirst,
		sleepySecond.GetUpstreamTaskID(): sleepySecond,
		fastFirst.GetUpstreamTaskID():    fastFirst,
		fastSecond.GetUpstreamTaskID():   fastSecond,
	})

	require.ErrorIs(t, err, context.DeadlineExceeded)
	assert.ElementsMatch(t, []string{"upstream_sleepy_1", "upstream_fast_1", "upstream_fast_2"}, adaptor.fetchedTaskIDs())
}

func TestUpdateSunoTasksStalePollsRefundExactlyOnce(t *testing.T) {
	truncate(t)

	const userID, tokenID, channelID = 401, 401, 401
	const initialUserQuota, initialTokenQuota, taskQuota = 10_000, 6_000, 2_500
	const publicTaskID, upstreamTaskID = "suno_public_refund_once", "suno_upstream_refund_once"

	seedUser(t, userID, initialUserQuota)
	seedToken(t, tokenID, userID, "sk-suno-refund-once", initialTokenQuota)
	baseURL := "https://suno.invalid"
	require.NoError(t, model.DB.Create(&model.Channel{
		Id:      channelID,
		Type:    constant.ChannelTypeSunoAPI,
		Name:    "suno_refund_once",
		Key:     "sk-suno-channel",
		Status:  common.ChannelStatusEnabled,
		BaseURL: &baseURL,
	}).Error)

	task := makeTask(userID, channelID, taskQuota, tokenID, BillingSourceWallet, 0)
	task.TaskID = publicTaskID
	task.Platform = constant.TaskPlatformSuno
	task.Status = model.TaskStatusInProgress
	task.Progress = "50%"
	task.SubmitTime = time.Now().Unix()
	task.PrivateData.UpstreamTaskID = upstreamTaskID
	require.NoError(t, model.DB.Create(task).Error)

	var firstPollTask model.Task
	var staleSecondPollTask model.Task
	require.NoError(t, model.DB.First(&firstPollTask, task.ID).Error)
	require.NoError(t, model.DB.First(&staleSecondPollTask, task.ID).Error)

	adaptor := &sunoFailurePollingAdaptor{failReason: "upstream failed"}
	previousFactory := GetTaskAdaptorFunc
	GetTaskAdaptorFunc = func(constant.TaskPlatform) TaskPollingAdaptor { return adaptor }
	t.Cleanup(func() { GetTaskAdaptorFunc = previousFactory })

	require.NoError(t, updateSunoTasks(context.Background(), channelID, []string{upstreamTaskID}, map[string]*model.Task{
		upstreamTaskID: &firstPollTask,
	}))
	require.NoError(t, updateSunoTasks(context.Background(), channelID, []string{upstreamTaskID}, map[string]*model.Task{
		upstreamTaskID: &staleSecondPollTask,
	}))

	var reloaded model.Task
	require.NoError(t, model.DB.First(&reloaded, task.ID).Error)
	assert.EqualValues(t, model.TaskStatusFailure, reloaded.Status)
	assert.Zero(t, reloaded.Quota)
	assert.Equal(t, initialUserQuota+taskQuota, getUserQuota(t, userID))
	assert.Equal(t, initialTokenQuota+taskQuota, getTokenRemainQuota(t, tokenID))
	assert.Equal(t, int64(1), countLogs(t))
}

func TestRunTaskPollingOnceDoesNotRefundHistoricalFailedTask(t *testing.T) {
	truncate(t)

	const userID, initialQuota, taskQuota = 402, 10_000, 1_200
	seedUser(t, userID, initialQuota)

	task := makeTask(userID, 0, taskQuota, 0, BillingSourceWallet, 0)
	task.TaskID = "historical_failed_already_refunded"
	task.Status = model.TaskStatusFailure
	task.Progress = "100%"
	task.SubmitTime = time.Now().Add(-90 * 24 * time.Hour).Unix()
	task.UpdatedAt = time.Now().Add(-time.Minute).Unix()
	require.NoError(t, model.DB.Create(task).Error)

	previousFactory := GetTaskAdaptorFunc
	GetTaskAdaptorFunc = func(constant.TaskPlatform) TaskPollingAdaptor {
		return &taskPollingFetchAdaptor{}
	}
	t.Cleanup(func() { GetTaskAdaptorFunc = previousFactory })

	summary := RunTaskPollingOnce(context.Background(), nil)

	assert.Zero(t, summary.UnfinishedTasks)
	assert.Equal(t, initialQuota, getUserQuota(t, userID))
	assert.Equal(t, taskQuota, getTaskQuota(t, task.ID))
	assert.Equal(t, int64(0), countLogs(t))
}

func TestSweepTimedOutTasksHonorsRefundRolloutBoundary(t *testing.T) {
	truncate(t)

	const (
		userID          = 403
		initialQuota    = 10_000
		legacyTaskQuota = 1_800
		modernTaskQuota = 1_200
	)
	seedUser(t, userID, initialQuota)

	legacyTask := makeTask(userID, 0, legacyTaskQuota, 0, BillingSourceWallet, 0)
	legacyTask.TaskID = "legacy_timeout_without_refund"
	legacyTask.Progress = "50%"
	legacyTask.SubmitTime = 1771718399 // 2026-02-21 23:59:59 UTC
	require.NoError(t, model.DB.Create(legacyTask).Error)

	modernTask := makeTask(userID, 0, modernTaskQuota, 0, BillingSourceWallet, 0)
	modernTask.TaskID = "modern_timeout_with_refund"
	modernTask.Progress = "50%"
	modernTask.SubmitTime = 1771718400 // 2026-02-22 00:00:00 UTC
	require.NoError(t, model.DB.Create(modernTask).Error)

	previousTimeout := constant.TaskTimeoutMinutes
	constant.TaskTimeoutMinutes = 1
	t.Cleanup(func() { constant.TaskTimeoutMinutes = previousTimeout })

	sweepTimedOutTasks(context.Background())

	var reloadedLegacy model.Task
	var reloadedModern model.Task
	require.NoError(t, model.DB.First(&reloadedLegacy, legacyTask.ID).Error)
	require.NoError(t, model.DB.First(&reloadedModern, modernTask.ID).Error)
	assert.EqualValues(t, model.TaskStatusFailure, reloadedLegacy.Status)
	assert.EqualValues(t, model.TaskStatusFailure, reloadedModern.Status)
	assert.Zero(t, reloadedLegacy.Quota)
	assert.Zero(t, reloadedModern.Quota)
	assert.Contains(t, reloadedLegacy.FailReason, "旧系统遗留任务")
	assert.Contains(t, reloadedModern.FailReason, "任务超时")
	assert.Equal(t, initialQuota+modernTaskQuota, getUserQuota(t, userID))
	assert.Equal(t, int64(1), countLogs(t))
}

func TestPlatformProviderProofConflictsCannotStarveOrdinaryPollingOrTimeoutSelection(t *testing.T) {
	truncate(t)
	cutoff := time.Now().Add(-time.Hour).Unix()
	conflicts := make([]model.Task, 0, 105)
	for index := 0; index < 105; index++ {
		task := model.Task{
			TaskID:     fmt.Sprintf("proof-conflict-%03d", index),
			Status:     model.TaskStatusUnknown,
			Progress:   "0%",
			SubmitTime: cutoff - int64(200-index),
			CreatedAt:  cutoff - int64(200-index),
			UpdatedAt:  cutoff - int64(200-index),
			PrivateData: model.TaskPrivateData{
				BillingSource: model.TaskBillingSourcePlatformExternal,
			},
		}
		task.SetData(model.NewPlatformGenerationProviderProofConflictReceipt([]byte(task.TaskID)))
		conflicts = append(conflicts, task)
	}
	require.NoError(t, model.DB.Create(&conflicts).Error)
	ordinary := model.Task{
		TaskID:     "ordinary-eligible-after-conflicts",
		Status:     model.TaskStatusInProgress,
		Progress:   "50%",
		SubmitTime: cutoff - 1,
		CreatedAt:  cutoff - 1,
		UpdatedAt:  cutoff - 1,
	}
	require.NoError(t, model.DB.Create(&ordinary).Error)

	unfinished := model.GetAllUnFinishSyncTasks(1)
	require.Len(t, unfinished, 1)
	require.Equal(t, ordinary.ID, unfinished[0].ID)
	timedOut := model.GetTimedOutUnfinishedTasks(cutoff+1, 1)
	require.Len(t, timedOut, 1)
	require.Equal(t, ordinary.ID, timedOut[0].ID)
	require.True(t, model.HasUnfinishedSyncTasks())

	require.NoError(t, model.DB.Delete(&ordinary).Error)
	require.False(t, model.HasUnfinishedSyncTasks(), "proof-conflict rows must not keep the poll scheduler hot")
}

func TestRunTaskPollingOncePreservesAttestedProviderProofConflictBeforeTimeoutSweep(t *testing.T) {
	truncate(t)
	enableProtectedNativeBillingTest(t)
	fixture := seedProtectedExternalNativeBinding(
		t,
		model.PlatformGenerationStatusProcessing,
		model.TaskStatusUnknown,
		true,
	)
	conflict := model.NewPlatformGenerationProviderProofConflictReceipt([]byte(`{"id":"wrong-task","status":"succeeded"}`))
	fixture.Task.SetData(conflict)
	fixture.Task.SubmitTime = time.Now().UTC().Add(-2 * time.Hour).Unix()
	var recovery map[string]any
	require.NoError(t, common.Unmarshal([]byte(fixture.Job.NativeTaskRecoveryJSON), &recovery))
	recovery["submit_time"] = fixture.Task.SubmitTime
	recoveryJSON, err := common.Marshal(recovery)
	require.NoError(t, err)
	require.NoError(t, model.DB.Model(&model.Task{}).Where("id = ?", fixture.Task.ID).Updates(map[string]any{
		"data":        fixture.Task.Data,
		"submit_time": fixture.Task.SubmitTime,
	}).Error)
	require.NoError(t, model.DB.Model(&model.PlatformGenerationJob{}).Where("id = ?", fixture.Job.ID).
		Update("native_task_recovery_json", string(recoveryJSON)).Error)
	previousTimeout := constant.TaskTimeoutMinutes
	constant.TaskTimeoutMinutes = 1
	t.Cleanup(func() { constant.TaskTimeoutMinutes = previousTimeout })
	adaptor := &taskPollingFetchAdaptor{}
	previousFactory := GetTaskAdaptorFunc
	GetTaskAdaptorFunc = func(constant.TaskPlatform) TaskPollingAdaptor { return adaptor }
	t.Cleanup(func() { GetTaskAdaptorFunc = previousFactory })

	summary := RunTaskPollingOnce(context.Background(), nil)
	assert.Zero(t, summary.UnfinishedTasks)
	assert.Zero(t, adaptor.fetchCount(), "proof conflict must be excluded before any provider poll")
	var persisted model.Task
	require.NoError(t, model.DB.First(&persisted, fixture.Task.ID).Error)
	assert.EqualValues(t, model.TaskStatusUnknown, persisted.Status)
	assert.Equal(t, "50%", persisted.Progress)
	assert.Equal(t, string(fixture.Task.Data), string(persisted.Data))
	assert.Empty(t, persisted.FailReason)
	require.NoError(t, ValidateProtectedPlatformNativeBillingState(),
		"manual reconciliation evidence remains an exact protected native binding")
}

func TestRunTaskPollingOnceCannotRecoverCorruptUnknownPlatformReceiptOutsideAttestedMode(t *testing.T) {
	truncate(t)
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
	corruptReceipt := []byte(`{"schema_version":1,"state":`)
	task := model.Task{
		TaskID:     "corrupt-platform-proof-conflict",
		Platform:   constant.TaskPlatform("kling"),
		ChannelId:  7788,
		Status:     model.TaskStatusUnknown,
		Progress:   "0%",
		SubmitTime: time.Now().UTC().Add(-2 * time.Hour).Unix(),
		CreatedAt:  time.Now().UTC().Add(-2 * time.Hour).Unix(),
		UpdatedAt:  time.Now().UTC().Add(-2 * time.Hour).Unix(),
		Data:       corruptReceipt,
		PrivateData: model.TaskPrivateData{
			BillingSource:  model.TaskBillingSourcePlatformExternal,
			UpstreamTaskID: "provider-task-that-must-not-be-polled",
		},
	}
	require.NoError(t, model.DB.Create(&task).Error)
	previousTimeout := constant.TaskTimeoutMinutes
	constant.TaskTimeoutMinutes = 1
	t.Cleanup(func() { constant.TaskTimeoutMinutes = previousTimeout })
	adaptor := &taskPollingFetchAdaptor{}
	previousFactory := GetTaskAdaptorFunc
	GetTaskAdaptorFunc = func(constant.TaskPlatform) TaskPollingAdaptor { return adaptor }
	t.Cleanup(func() { GetTaskAdaptorFunc = previousFactory })

	summary := RunTaskPollingOnce(context.Background(), nil)
	assert.Zero(t, summary.UnfinishedTasks)
	assert.Zero(t, adaptor.fetchCount(), "a later valid poll must not overwrite malformed manual evidence")
	var persisted model.Task
	require.NoError(t, model.DB.First(&persisted, task.ID).Error)
	assert.EqualValues(t, model.TaskStatusUnknown, persisted.Status)
	assert.Equal(t, "0%", persisted.Progress)
	assert.Equal(t, string(corruptReceipt), string(persisted.Data))
	assert.Empty(t, persisted.FailReason)
}
