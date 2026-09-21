package gemini

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

const (
	veoTestModel         = "veo-3.1-generate-preview"
	veoTestOperationID   = "operation-abc-123"
	veoTestOperationName = "models/veo-3.1-generate-preview/operations/operation-abc-123"
	veoTestArtifactURL   = "https://generativelanguage.googleapis.com/v1beta/files/veo-output-123:download?alt=media"
)

func veoTestInfo(baseURL string) *relaycommon.RelayInfo {
	return &relaycommon.RelayInfo{
		OriginModelName: "veo-3.1",
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelType:       constant.ChannelTypeGemini,
			ChannelBaseUrl:    baseURL,
			ApiKey:            "veo-secret-key",
			UpstreamModelName: veoTestModel,
		},
		TaskRelayInfo: &relaycommon.TaskRelayInfo{PublicTaskID: "task_veo_public"},
	}
}

func veoTestContext(req relaycommon.TaskSubmitReq) (*gin.Context, *httptest.ResponseRecorder) {
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest(http.MethodPost, "/v1/video/generations", nil)
	context.Set("task_request", req)
	return context, recorder
}

func veoTestRequest() relaycommon.TaskSubmitReq {
	return relaycommon.TaskSubmitReq{
		Prompt:   "A slow dolly shot through a quiet observatory",
		Duration: 8,
		Size:     "1920x1080",
		Metadata: map[string]any{"resolution": "1080p", "aspectRatio": "16:9"},
	}
}

func TestVeoBuildsCurrentOneVideoRequestAndVersionedIdentity(t *testing.T) {
	context, _ := veoTestContext(veoTestRequest())
	info := veoTestInfo("https://generativelanguage.googleapis.com")
	adaptor := &TaskAdaptor{}
	adaptor.Init(info)

	body, err := adaptor.BuildRequestBody(context, info)
	require.NoError(t, err)
	raw, err := io.ReadAll(body)
	require.NoError(t, err)
	require.NotContains(t, string(raw), "veo-secret-key")
	var payload VeoRequestPayload
	require.NoError(t, common.Unmarshal(raw, &payload))
	require.Len(t, payload.Instances, 1)
	require.Equal(t, 1, payload.Parameters.NumberOfVideos)
	require.Zero(t, payload.Parameters.SampleCount)
	require.Equal(t, 8, payload.Parameters.DurationSeconds)
	require.Equal(t, "1080p", payload.Parameters.Resolution)
	require.Equal(t, "16:9", payload.Parameters.AspectRatio)

	response := &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(`{"name":"` + veoTestOperationName + `"}`))}
	taskID, taskData, taskErr := adaptor.DoResponse(context, response, info)
	require.Nil(t, taskErr)
	identity, err := parseVeoTaskID(taskID)
	require.NoError(t, err)
	require.Equal(t, veoTestModel, identity.Model)
	require.Equal(t, veoTestOperationName, identity.OperationName)
	require.Equal(t, 8, identity.DurationSeconds)
	require.Equal(t, "1080p", identity.Resolution)
	require.Equal(t, "16:9", identity.AspectRatio)
	require.NotContains(t, string(taskData), "veo-secret-key")
	var receipt veoSubmissionReceipt
	require.NoError(t, common.Unmarshal(taskData, &receipt))
	require.Equal(t, taskID, receipt.TaskID)
	require.Equal(t, veoTestOperationName, receipt.OperationName)
}

func TestVeoTaskIdentityRejectsModelSpecificationAndOperationDrift(t *testing.T) {
	spec := veoTaskSpec{Model: veoTestModel, DurationSeconds: 8, Resolution: "1080p", AspectRatio: "16:9"}
	taskID, err := buildVeoTaskID(veoTestOperationName, spec)
	require.NoError(t, err)
	identity, err := parseVeoTaskID(taskID)
	require.NoError(t, err)
	require.Equal(t, spec, identity.veoTaskSpec)

	_, err = buildVeoTaskID("models/veo-3.1-fast-generate-preview/operations/"+veoTestOperationID, spec)
	require.Error(t, err)
	for _, damaged := range []string{
		strings.Replace(taskID, ":1080p:", ":720p:", 1),
		strings.Replace(taskID, ":16x9:", ":1x1:", 1),
		strings.Replace(taskID, ":v1", ":v2", 1),
		"gveo:31p:../escape:8:1080p:16x9:v1",
	} {
		_, err := parseVeoTaskID(damaged)
		require.Error(t, err)
	}
}

func veoTaskIDForTest(t *testing.T) string {
	t.Helper()
	taskID, err := buildVeoTaskID(veoTestOperationName, veoTaskSpec{
		Model: veoTestModel, DurationSeconds: 8, Resolution: "1080p", AspectRatio: "16:9",
	})
	require.NoError(t, err)
	return taskID
}

func currentVeoSuccess() []byte {
	return []byte(`{
      "name":"` + veoTestOperationName + `",
      "done":true,
      "response":{"generateVideoResponse":{"generatedSamples":[{"video":{"uri":"` + veoTestArtifactURL + `","mimeType":"video/mp4"}}]}}
    }`)
}

func legacyVeoSuccess() []byte {
	return []byte(`{
      "name":"` + veoTestOperationName + `",
      "done":true,
      "response":{"generateVideoResponse":{"generatedVideos":[{"video":{"uri":"` + veoTestArtifactURL + `","mimeType":"video/mp4"}}]}}
    }`)
}

func TestVeoCurrentAndLegacySuccessProduceExactTerminalProof(t *testing.T) {
	taskID := veoTaskIDForTest(t)
	for _, raw := range [][]byte{currentVeoSuccess(), legacyVeoSuccess()} {
		envelope, err := buildVeoOperationEnvelope(raw, taskID, veoTestModel)
		require.NoError(t, err)
		result, err := (&TaskAdaptor{}).ParseTaskResult(envelope)
		require.NoError(t, err)
		require.Equal(t, model.TaskStatusSuccess, result.Status)
		require.Equal(t, taskID, result.TaskID)
		require.Equal(t, veoTestArtifactURL, result.Url)
		require.Equal(t, veoTestArtifactURL, result.RemoteUrl)
		require.NotNil(t, result.ProviderResultProof)
		require.Equal(t, ProviderProtocolV1, result.ProviderResultProof.Protocol)
		require.Equal(t, veoTestModel, result.ProviderResultProof.Model)
		require.Equal(t, "succeeded", result.ProviderResultProof.ProviderStatus)
		require.Equal(t, 8, result.ProviderResultProof.DurationSeconds)
		require.Equal(t, "1080p", result.ProviderResultProof.Resolution)
		require.Equal(t, "16:9", result.ProviderResultProof.AspectRatio)
		require.Equal(t, 1, result.ProviderResultProof.OutputCount)
		require.Equal(t, "video", result.ProviderResultProof.MediaType)
	}
}

func TestVeoProcessingAndFailureProduceBoundIdentityProof(t *testing.T) {
	taskID := veoTaskIDForTest(t)
	processingRaw := []byte(`{"name":"` + veoTestOperationName + `","done":false}`)
	processingEnvelope, err := buildVeoOperationEnvelope(processingRaw, taskID, veoTestModel)
	require.NoError(t, err)
	processing, err := (&TaskAdaptor{}).ParseTaskResult(processingEnvelope)
	require.NoError(t, err)
	require.Equal(t, model.TaskStatusInProgress, processing.Status)
	require.Equal(t, taskID, processing.TaskID)
	require.Equal(t, "processing", processing.ProviderResultProof.ProviderStatus)

	failures := []struct {
		raw   []byte
		owner string
		code  string
	}{
		{[]byte(`{"name":"` + veoTestOperationName + `","done":true,"error":{"code":13,"message":"internal"}}`), "provider", "provider_service_failure"},
		{[]byte(`{"name":"` + veoTestOperationName + `","done":true,"response":{"raiMediaFilteredCount":1}}`), "client", "content_policy_rejected"},
	}
	for _, test := range failures {
		envelope, err := buildVeoOperationEnvelope(test.raw, taskID, veoTestModel)
		require.NoError(t, err)
		result, err := (&TaskAdaptor{}).ParseTaskResult(envelope)
		require.NoError(t, err)
		require.Equal(t, model.TaskStatusFailure, result.Status)
		require.Equal(t, taskID, result.TaskID)
		require.Equal(t, "failed", result.ProviderResultProof.ProviderStatus)
		require.Equal(t, test.owner, result.ProviderResultProof.FailureOwner)
		require.Equal(t, test.code, result.ProviderResultProof.FailureCode)
	}
}

func TestVeoOperationEnvelopeFailsClosedOnConflictingOrCredentialedMaterial(t *testing.T) {
	taskID := veoTaskIDForTest(t)
	tests := []struct {
		name          string
		raw           string
		providerModel string
	}{
		{"operation drift", `{"name":"models/veo-3.1-generate-preview/operations/other","done":false}`, veoTestModel},
		{"model drift", string(currentVeoSuccess()), "veo-3.1-fast-generate-preview"},
		{"inline", `{"name":"` + veoTestOperationName + `","done":true,"response":{"generateVideoResponse":{"generatedSamples":[{"video":{"videoBytes":"AAAA"}}]}}}`, veoTestModel},
		{"credential query", `{"name":"` + veoTestOperationName + `","done":true,"response":{"generateVideoResponse":{"generatedSamples":[{"video":{"uri":"https://generativelanguage.googleapis.com/v1beta/files/veo-output-123:download?alt=media&key=secret"}}]}}}`, veoTestModel},
		{"two outputs", `{"name":"` + veoTestOperationName + `","done":true,"response":{"generateVideoResponse":{"generatedSamples":[{"video":{"uri":"` + veoTestArtifactURL + `"}},{"video":{"uri":"` + veoTestArtifactURL + `"}}]}}}`, veoTestModel},
		{"mixed aliases", `{"name":"` + veoTestOperationName + `","done":true,"response":{"generateVideoResponse":{"generatedSamples":[{"video":{"uri":"` + veoTestArtifactURL + `"}}],"generatedVideos":[{"video":{"uri":"` + veoTestArtifactURL + `"}}]}}}`, veoTestModel},
		{"out of order material", `{"name":"` + veoTestOperationName + `","done":false,"response":{"generateVideoResponse":{"generatedSamples":[{"video":{"uri":"` + veoTestArtifactURL + `"}}]}}}`, veoTestModel},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := buildVeoOperationEnvelope([]byte(test.raw), taskID, test.providerModel)
			require.Error(t, err)
		})
	}
}

func TestVeoParsingIsStatelessAcrossOutOfOrderObservations(t *testing.T) {
	taskID := veoTaskIDForTest(t)
	successEnvelope, err := buildVeoOperationEnvelope(currentVeoSuccess(), taskID, veoTestModel)
	require.NoError(t, err)
	processingEnvelope, err := buildVeoOperationEnvelope([]byte(`{"name":"`+veoTestOperationName+`","done":false}`), taskID, veoTestModel)
	require.NoError(t, err)

	adaptor := &TaskAdaptor{}
	success, err := adaptor.ParseTaskResult(successEnvelope)
	require.NoError(t, err)
	require.Equal(t, model.TaskStatusSuccess, success.Status)
	processing, err := adaptor.ParseTaskResult(processingEnvelope)
	require.NoError(t, err)
	require.Equal(t, model.TaskStatusInProgress, processing.Status)
	require.Equal(t, taskID, processing.ProviderResultProof.TaskID)

	var damaged veoOperationEnvelope
	require.NoError(t, common.Unmarshal(successEnvelope, &damaged))
	damaged.ProviderModel = "veo-3.1-fast-generate-preview"
	damagedRaw, err := common.Marshal(damaged)
	require.NoError(t, err)
	_, err = adaptor.ParseTaskResult(damagedRaw)
	require.Error(t, err)
}

func TestVeoFetchUsesImmutableOperationAndHeaderCredential(t *testing.T) {
	taskID := veoTaskIDForTest(t)
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		require.Equal(t, "/v1beta/"+veoTestOperationName, request.URL.Path)
		require.Empty(t, request.URL.RawQuery)
		require.Equal(t, "veo-secret-key", request.Header.Get("x-goog-api-key"))
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{"name":"` + veoTestOperationName + `","done":false}`))
	}))
	defer server.Close()

	response, err := (&TaskAdaptor{}).FetchTask(server.URL, "veo-secret-key", map[string]any{
		"task_id": taskID, "provider_model": veoTestModel,
	}, "")
	require.NoError(t, err)
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	require.NoError(t, err)
	result, err := (&TaskAdaptor{}).ParseTaskResult(body)
	require.NoError(t, err)
	require.Equal(t, model.TaskStatusInProgress, result.Status)
}

func TestVeoFetchNeverFollowsOrForwardsCredentialOnRedirect(t *testing.T) {
	taskID := veoTaskIDForTest(t)
	var targetCalls atomic.Int32
	target := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		targetCalls.Add(1)
		require.Empty(t, request.Header.Get("x-goog-api-key"))
		writer.WriteHeader(http.StatusOK)
	}))
	defer target.Close()
	origin := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		require.Equal(t, "veo-secret-key", request.Header.Get("x-goog-api-key"))
		writer.Header().Set("Location", target.URL+"/credential-sink")
		writer.WriteHeader(http.StatusTemporaryRedirect)
	}))
	defer origin.Close()

	response, err := (&TaskAdaptor{}).FetchTask(origin.URL, "veo-secret-key", map[string]any{
		"task_id": taskID, "provider_model": veoTestModel,
	}, "")
	require.NoError(t, err)
	defer response.Body.Close()
	require.Equal(t, http.StatusTemporaryRedirect, response.StatusCode)
	require.Zero(t, targetCalls.Load())
}
