package geminiomni

import (
	"bytes"
	"encoding/base64"
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

func omniTestInfo(baseURL string) *relaycommon.RelayInfo {
	return &relaycommon.RelayInfo{
		OriginModelName: ModelGeminiOmni11Flash,
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelType:       constant.ChannelTypeGemini,
			ChannelBaseUrl:    baseURL,
			ApiKey:            "omni-secret-key",
			UpstreamModelName: ModelGeminiOmni11Flash,
		},
		TaskRelayInfo: &relaycommon.TaskRelayInfo{PublicTaskID: "task_omni_public"},
	}
}

func omniTestContext(req relaycommon.TaskSubmitReq) (*gin.Context, *httptest.ResponseRecorder) {
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Set("task_request", req)
	return context, recorder
}

func TestOmniBuildsSynchronousURIDeliveryWithoutCredentialLeak(t *testing.T) {
	context, _ := omniTestContext(relaycommon.TaskSubmitReq{
		Prompt:   "Animate the paper boat crossing a quiet lake",
		Images:   []string{"data:image/png;base64,aW1hZ2U="},
		Duration: 7,
		Size:     "1080x1920",
		Mode:     "image_to_video",
	})
	info := omniTestInfo("https://generativelanguage.googleapis.com")
	adaptor := &TaskAdaptor{}
	adaptor.Init(info)

	body, err := adaptor.BuildRequestBody(context, info)
	require.NoError(t, err)
	raw, err := io.ReadAll(body)
	require.NoError(t, err)
	require.NotContains(t, string(raw), "omni-secret-key")

	var payload interactionRequest
	require.NoError(t, common.Unmarshal(raw, &payload))
	require.Equal(t, ModelGeminiOmni11Flash, payload.Model)
	require.False(t, payload.Background)
	require.False(t, payload.Stream)
	require.False(t, payload.Store)
	require.Equal(t, "video", payload.ResponseFormat.Type)
	require.Equal(t, "uri", payload.ResponseFormat.Delivery)
	require.Equal(t, "7s", payload.ResponseFormat.Duration)
	require.Equal(t, "1080p", payload.ResponseFormat.Resolution)
	require.Equal(t, "9:16", payload.ResponseFormat.AspectRatio)
	require.Equal(t, "image_to_video", payload.GenerationConfig.VideoConfig.Task)
	require.Len(t, payload.Input, 2)
	require.Equal(t, "image", payload.Input[0].Type)
	require.Equal(t, "text", payload.Input[1].Type)

	requestURL, err := adaptor.BuildRequestURL(info)
	require.NoError(t, err)
	require.Equal(t, "https://generativelanguage.googleapis.com/v1beta/interactions", requestURL)
	httpRequest, err := http.NewRequest(http.MethodPost, requestURL, bytes.NewReader(raw))
	require.NoError(t, err)
	require.NoError(t, adaptor.BuildRequestHeader(context, httpRequest, info))
	require.Equal(t, "omni-secret-key", httpRequest.Header.Get("x-goog-api-key"))
	require.Empty(t, httpRequest.URL.Query().Get("key"))
}

func TestOmniBlocksUnpublishedLineageAndReferenceModes(t *testing.T) {
	tests := []relaycommon.TaskSubmitReq{
		{Prompt: "edit", Mode: "video_to_video", Videos: []string{"https://assets.example/input.mp4"}},
		{Prompt: "reference", Mode: "reference_to_video", Images: []string{"aW1hZ2U="}},
		{Prompt: "extend", Mode: "text_to_video", Metadata: map[string]any{"previous_interaction_id": "v1_prior"}},
		{Prompt: "hidden video", Mode: "text_to_video", Videos: []string{"https://assets.example/input.mp4"}},
	}
	for _, req := range tests {
		spec, err := resolveRequestSpec(req)
		if err == nil {
			err = validateTaskRequest(req, spec)
		}
		require.Error(t, err)
	}
}

func completedOmniResponse(status string, content string) string {
	return `{
        "created":"2026-09-01T00:00:00Z",
        "id":"v1_test_interaction_123",
        "model":"gemini-omni-1.1-flash",
        "object":"interaction",
        "status":"` + status + `",
        "steps":[
          {"type":"thought","content":[{"type":"thought","text":"internal"}],"signature":"ignored-extension"},
          {"type":"model_output","content":[` + content + `]}
        ],
        "updated":"2026-09-01T00:00:10Z",
        "usage":{
          "input_tokens_by_modality":[{"modality":"text","tokens":12}],
          "output_tokens_by_modality":[{"modality":"video","tokens":100}],
          "total_cached_tokens":2,
          "total_input_tokens":12,
          "total_output_tokens":100,
          "total_thought_tokens":3,
          "total_tokens":115,
          "total_tool_use_tokens":0
        }
      }`
}

func completedOmniResponseWithoutUsage(content string) string {
	return `{
        "created":"2026-09-01T00:00:00Z",
        "id":"v1_test_interaction_without_usage",
        "model":"gemini-omni-1.1-flash",
        "object":"interaction",
        "status":"completed",
        "steps":[{"type":"model_output","content":[` + content + `]}],
        "updated":"2026-09-01T00:00:10Z"
      }`
}

func preparedOmniAdaptor(t *testing.T) (*TaskAdaptor, *gin.Context, *relaycommon.RelayInfo) {
	t.Helper()
	context, _ := omniTestContext(relaycommon.TaskSubmitReq{
		Prompt: "A paper boat crossing a lake", Mode: "text_to_video", Duration: 5,
		Metadata: map[string]any{"resolution": "720p", "aspectRatio": "16:9"},
	})
	info := omniTestInfo("https://generativelanguage.googleapis.com")
	adaptor := &TaskAdaptor{}
	adaptor.Init(info)
	_, err := adaptor.BuildRequestBody(context, info)
	require.NoError(t, err)
	return adaptor, context, info
}

func TestOmniSubmitPersistsFileIdentityAndSecretFreeUsageReceipt(t *testing.T) {
	adaptor, context, info := preparedOmniAdaptor(t)
	response := &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(completedOmniResponse(
		"completed",
		`{"type":"video","mime_type":"video/mp4","uri":"https://generativelanguage.googleapis.com/v1beta/files/omni-output-123:download?alt=media"}`,
	)))}

	taskID, taskData, taskErr := adaptor.DoResponse(context, response, info)
	require.Nil(t, taskErr)
	identity, err := parsePersistedTaskID(taskID)
	require.NoError(t, err)
	require.Equal(t, "omni-output-123", identity.FileID)
	require.Equal(t, 5, identity.DurationSeconds)
	require.Equal(t, 12, identity.TotalInputTokens)
	require.Equal(t, 100, identity.TotalOutputTokens)
	require.Equal(t, 2, identity.TotalCachedTokens)
	require.Equal(t, 115, identity.TotalTokens)
	require.NotContains(t, string(taskData), "omni-secret-key")
	require.NotContains(t, string(taskData), "alt=media")
	require.NotContains(t, string(taskData), "generativelanguage.googleapis.com")
	var receipt submissionReceipt
	require.NoError(t, common.Unmarshal(taskData, &receipt))
	require.Equal(t, "v1_test_interaction_123", receipt.InteractionID)
	require.Equal(t, "omni-output-123", receipt.FileID)
}

func TestOmniSubmitWithoutUsageKeepsGenerationRecoverableAndCostEvidenceIncomplete(t *testing.T) {
	adaptor, context, info := preparedOmniAdaptor(t)
	response := &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(completedOmniResponseWithoutUsage(
		`{"type":"video","mime_type":"video/mp4","uri":"https://generativelanguage.googleapis.com/v1beta/files/omni-output-123:download?alt=media"}`,
	)))}

	taskID, taskData, taskErr := adaptor.DoResponse(context, response, info)
	require.Nil(t, taskErr)
	identity, err := parsePersistedTaskID(taskID)
	require.NoError(t, err)
	require.Equal(t, 3, identity.SchemaVersion)
	require.False(t, identity.UsageComplete)
	require.Zero(t, identity.TotalInputTokens)
	require.Zero(t, identity.TotalOutputTokens)

	var receipt submissionReceipt
	require.NoError(t, common.Unmarshal(taskData, &receipt))
	require.Equal(t, 3, receipt.SchemaVersion)
	require.False(t, receipt.UsageComplete)
	require.Zero(t, receipt.TotalTokens)

	digest := bytes.Repeat([]byte{0x5a}, 32)
	activeRaw := `{
      "name":"files/omni-output-123",
      "mimeType":"video/mp4",
      "sizeBytes":"123456",
      "sha256Hash":"` + base64.StdEncoding.EncodeToString(digest) + `",
      "state":"ACTIVE",
      "source":"GENERATED",
      "videoMetadata":{"videoDuration":"5.000s"}
    }`
	active, err := buildFileStatusEnvelope([]byte(activeRaw), "https://generativelanguage.googleapis.com", taskID, ModelGeminiOmni11Flash)
	require.NoError(t, err)
	result, err := (&TaskAdaptor{}).ParseTaskResult(active)
	require.NoError(t, err)
	require.Equal(t, model.TaskStatusSuccess, result.Status)
	require.False(t, result.ProviderResultProof.UsageComplete)
	require.Zero(t, result.ProviderResultProof.TotalTokens)
	require.Equal(t, int64(123456), result.ProviderResultProof.ArtifactSizeBytes)
}

func TestOmniSubmitRejectsPartialUsageInsteadOfTreatingItAsAbsent(t *testing.T) {
	adaptor, context, info := preparedOmniAdaptor(t)
	raw := strings.Replace(
		completedOmniResponseWithoutUsage(`{"type":"video","mime_type":"video/mp4","uri":"https://generativelanguage.googleapis.com/v1beta/files/omni-output-123:download?alt=media"}`),
		`"updated":"2026-09-01T00:00:10Z"`,
		`"updated":"2026-09-01T00:00:10Z","usage":{"total_input_tokens":12}`,
		1,
	)
	response := &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(raw))}

	taskID, taskData, taskErr := adaptor.DoResponse(context, response, info)
	require.NotNil(t, taskErr)
	require.Empty(t, taskID)
	require.Nil(t, taskData)
}

func TestOmniSubmitFailsClosedOnNonterminalInlineOrAmbiguousOutput(t *testing.T) {
	tests := []struct {
		name    string
		status  string
		content string
	}{
		{"in progress", "in_progress", `{"type":"video","mime_type":"video/mp4","uri":"https://generativelanguage.googleapis.com/v1beta/files/omni-output-123:download?alt=media"}`},
		{"inline", "completed", `{"type":"video","mime_type":"video/mp4","data":"AAAA"}`},
		{"non https", "completed", `{"type":"video","mime_type":"video/mp4","uri":"http://generativelanguage.googleapis.com/v1beta/files/omni-output-123:download?alt=media"}`},
		{"multiple", "completed", `{"type":"video","mime_type":"video/mp4","uri":"https://generativelanguage.googleapis.com/v1beta/files/omni-output-123:download?alt=media"},{"type":"video","mime_type":"video/mp4","uri":"https://generativelanguage.googleapis.com/v1beta/files/omni-output-456:download?alt=media"}`},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			adaptor, context, info := preparedOmniAdaptor(t)
			response := &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(completedOmniResponse(test.status, test.content)))}
			taskID, taskData, taskErr := adaptor.DoResponse(context, response, info)
			require.NotNil(t, taskErr)
			require.Empty(t, taskID)
			require.Nil(t, taskData)
		})
	}
}

func TestOmniFilesEnvelopeBindsProcessingAndActiveEvidence(t *testing.T) {
	spec := requestSpec{DurationSeconds: 5, Resolution: "720p", AspectRatio: "16:9", Task: "text_to_video"}
	usage := interactionUsage{
		TotalCachedTokens: 2, TotalInputTokens: 12, TotalOutputTokens: 100, TotalThoughtTokens: 3, TotalTokens: 115,
		OutputTokensByModality: []interactionModalityUsage{{Modality: "video", Tokens: 100}},
	}
	taskID, err := buildPersistedTaskID("omni-output-123", spec, usage)
	require.NoError(t, err)

	processing, err := buildFileStatusEnvelope(
		[]byte(`{"name":"files/omni-output-123","state":"PROCESSING","source":"GENERATED"}`),
		"https://generativelanguage.googleapis.com", taskID, ModelGeminiOmni11Flash,
	)
	require.NoError(t, err)
	processingResult, err := (&TaskAdaptor{}).ParseTaskResult(processing)
	require.NoError(t, err)
	require.Equal(t, model.TaskStatusInProgress, processingResult.Status)
	require.Equal(t, taskID, processingResult.TaskID)
	require.Equal(t, "processing", processingResult.ProviderResultProof.ProviderStatus)

	digest := bytes.Repeat([]byte{0x5a}, 32)
	activeRaw := `{
      "name":"files/omni-output-123",
      "mimeType":"video/mp4",
      "sizeBytes":"123456",
      "sha256Hash":"` + base64.StdEncoding.EncodeToString(digest) + `",
      "state":"ACTIVE",
      "source":"GENERATED",
      "videoMetadata":{"videoDuration":"5.000s"}
    }`
	active, err := buildFileStatusEnvelope([]byte(activeRaw), "https://generativelanguage.googleapis.com", taskID, ModelGeminiOmni11Flash)
	require.NoError(t, err)
	activeResult, err := (&TaskAdaptor{}).ParseTaskResult(active)
	require.NoError(t, err)
	require.Equal(t, model.TaskStatusSuccess, activeResult.Status)
	require.Equal(t, "https://generativelanguage.googleapis.com/v1beta/files/omni-output-123:download?alt=media", activeResult.Url)
	require.Equal(t, 100, activeResult.CompletionTokens)
	require.Equal(t, 115, activeResult.TotalTokens)
	require.Equal(t, ProviderProtocolV1, activeResult.ProviderResultProof.Protocol)
	require.Equal(t, 5, activeResult.ProviderResultProof.DurationSeconds)
	require.Equal(t, "720p", activeResult.ProviderResultProof.Resolution)
	require.Equal(t, "16:9", activeResult.ProviderResultProof.AspectRatio)
	require.Equal(t, 12, activeResult.ProviderResultProof.InputTokens)
	require.Equal(t, 100, activeResult.ProviderResultProof.OutputTokens)
	require.Equal(t, 2, activeResult.ProviderResultProof.CachedTokens)
	require.Equal(t, 115, activeResult.ProviderResultProof.TotalTokens)
	require.Equal(t, int64(123456), activeResult.ProviderResultProof.ArtifactSizeBytes)
	require.Equal(t, strings.Repeat("5a", 32), activeResult.ProviderResultProof.ArtifactSHA256)
}

func TestOmniFetchUsesHeaderCredentialAndFilesIdentity(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		require.Equal(t, "/v1beta/files/omni-output-123", request.URL.Path)
		require.Empty(t, request.URL.RawQuery)
		require.Equal(t, "omni-secret-key", request.Header.Get("x-goog-api-key"))
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{"name":"files/omni-output-123","state":"PROCESSING","source":"GENERATED"}`))
	}))
	defer server.Close()

	taskID, err := buildPersistedTaskID("omni-output-123", requestSpec{DurationSeconds: 5, Resolution: "720p", AspectRatio: "16:9"}, interactionUsage{
		TotalInputTokens: 12, TotalOutputTokens: 100, TotalThoughtTokens: 3, TotalCachedTokens: 2, TotalTokens: 115,
		OutputTokensByModality: []interactionModalityUsage{{Modality: "video", Tokens: 100}},
	})
	require.NoError(t, err)
	response, err := (&TaskAdaptor{}).FetchTask(server.URL, "omni-secret-key", map[string]any{
		"task_id": taskID, "provider_model": ModelGeminiOmni11Flash,
	}, "")
	require.NoError(t, err)
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	require.NoError(t, err)
	result, err := (&TaskAdaptor{}).ParseTaskResult(body)
	require.NoError(t, err)
	require.Equal(t, model.TaskStatusInProgress, result.Status)
}

func TestOmniFetchNeverFollowsOrForwardsCredentialOnRedirect(t *testing.T) {
	var targetCalls atomic.Int32
	target := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		targetCalls.Add(1)
		require.Empty(t, request.Header.Get("x-goog-api-key"))
		writer.WriteHeader(http.StatusOK)
	}))
	defer target.Close()
	origin := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		require.Equal(t, "omni-secret-key", request.Header.Get("x-goog-api-key"))
		writer.Header().Set("Location", target.URL+"/credential-sink")
		writer.WriteHeader(http.StatusTemporaryRedirect)
	}))
	defer origin.Close()

	taskID, err := buildPersistedTaskID("omni-output-redirect", requestSpec{DurationSeconds: 5, Resolution: "720p", AspectRatio: "16:9"}, interactionUsage{
		TotalInputTokens: 12, TotalOutputTokens: 100, TotalThoughtTokens: 3, TotalCachedTokens: 2, TotalTokens: 115,
		OutputTokensByModality: []interactionModalityUsage{{Modality: "video", Tokens: 100}},
	})
	require.NoError(t, err)
	response, err := (&TaskAdaptor{}).FetchTask(origin.URL, "omni-secret-key", map[string]any{
		"task_id": taskID, "provider_model": ModelGeminiOmni11Flash,
	}, "")
	require.NoError(t, err)
	defer response.Body.Close()
	require.Equal(t, http.StatusTemporaryRedirect, response.StatusCode)
	require.Zero(t, targetCalls.Load())
}

func TestGeminiDispatcherAdvertisesOmniWithoutReplacingVeo(t *testing.T) {
	models := (&DispatchTaskAdaptor{}).GetModelList()
	require.Contains(t, models, ModelGeminiOmni11Flash)
	require.Contains(t, models, "veo-3.1-generate-preview")
	require.Equal(t, ChannelName, (&DispatchTaskAdaptor{}).GetChannelName())
}

func TestGeminiDispatcherPaidProbeNeverFollowsSubmitRedirects(t *testing.T) {
	tests := []struct {
		name          string
		providerModel string
		originModel   string
		expectedPath  string
	}{
		{
			name:          "Omni Interactions",
			providerModel: ModelGeminiOmni11Flash,
			originModel:   ModelGeminiOmni11Flash,
			expectedPath:  "/v1beta/interactions",
		},
		{
			name:          "Veo predictLongRunning",
			providerModel: "veo-3.1-generate-preview",
			originModel:   "veo-3.1",
			expectedPath:  "/v1beta/models/veo-3.1-generate-preview:predictLongRunning",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			type observation struct {
				method string
				path   string
				apiKey string
			}
			observed := make(chan observation, 2)
			var requestCount atomic.Int32
			var redirectTargetCount atomic.Int32
			var server *httptest.Server
			server = httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
				requestCount.Add(1)
				if request.URL.Path == "/redirect-target" {
					redirectTargetCount.Add(1)
					writer.WriteHeader(http.StatusOK)
					return
				}
				observed <- observation{
					method: request.Method,
					path:   request.URL.Path,
					apiKey: request.Header.Get("x-goog-api-key"),
				}
				writer.Header().Set("Location", server.URL+"/redirect-target")
				writer.WriteHeader(http.StatusTemporaryRedirect)
			}))
			defer server.Close()

			info := &relaycommon.RelayInfo{
				OriginModelName: test.originModel,
				ChannelMeta: &relaycommon.ChannelMeta{
					ChannelType:       constant.ChannelTypeGemini,
					ChannelBaseUrl:    server.URL,
					ApiKey:            "paid-probe-secret",
					UpstreamModelName: test.providerModel,
				},
				TaskRelayInfo: &relaycommon.TaskRelayInfo{PublicTaskID: "task_paid_probe"},
			}
			adaptor := &DispatchTaskAdaptor{}
			adaptor.Init(info)
			context, _ := omniTestContext(relaycommon.TaskSubmitReq{})
			context.Request = httptest.NewRequest(http.MethodPost, "/internal/platform-generation-route-probe", strings.NewReader("{}"))

			response, err := adaptor.DoRequestNoRedirect(context, info, strings.NewReader("{}"))
			require.NoError(t, err)
			defer response.Body.Close()
			require.Equal(t, http.StatusTemporaryRedirect, response.StatusCode)

			request := <-observed
			require.Equal(t, http.MethodPost, request.method)
			require.Equal(t, test.expectedPath, request.path)
			require.Equal(t, "paid-probe-secret", request.apiKey)
			require.Equal(t, int32(1), requestCount.Load())
			require.Zero(t, redirectTargetCount.Load())
		})
	}
}

func TestGeminiDispatcherOrdinaryGenerationNeverFollowsOrForwardsCredentialsOnRedirect(t *testing.T) {
	tests := []struct {
		name          string
		providerModel string
		originModel   string
	}{
		{name: "Omni", providerModel: ModelGeminiOmni11Flash, originModel: ModelGeminiOmni11Flash},
		{name: "Veo", providerModel: "veo-3.1-generate-preview", originModel: "veo-3.1"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			var targetCalls atomic.Int32
			var targetCredential atomic.Value
			target := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
				targetCalls.Add(1)
				targetCredential.Store(request.Header.Get("x-goog-api-key"))
				writer.WriteHeader(http.StatusOK)
			}))
			defer target.Close()
			origin := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
				writer.Header().Set("Location", target.URL+"/credential-sink")
				writer.WriteHeader(http.StatusTemporaryRedirect)
			}))
			defer origin.Close()

			info := &relaycommon.RelayInfo{
				OriginModelName: test.originModel,
				ChannelMeta: &relaycommon.ChannelMeta{
					ChannelType: constant.ChannelTypeGemini, ChannelBaseUrl: origin.URL,
					ApiKey: "ordinary-generation-secret", UpstreamModelName: test.providerModel,
				},
				TaskRelayInfo: &relaycommon.TaskRelayInfo{PublicTaskID: "task_ordinary_google"},
			}
			adaptor := &DispatchTaskAdaptor{}
			adaptor.Init(info)
			context, _ := omniTestContext(relaycommon.TaskSubmitReq{})
			context.Request = httptest.NewRequest(http.MethodPost, "/internal/platform-generations/native-submit", strings.NewReader("{}"))
			response, err := adaptor.DoRequest(context, info, strings.NewReader("{}"))
			require.NoError(t, err)
			defer response.Body.Close()
			require.Equal(t, http.StatusTemporaryRedirect, response.StatusCode)
			require.Zero(t, targetCalls.Load())
			credential, _ := targetCredential.Load().(string)
			require.Empty(t, credential)
		})
	}
}
