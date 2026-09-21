package doubao

import (
	"crypto/sha256"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

type countingReadCloser struct {
	reader    io.Reader
	bytesRead int
	closed    bool
}

func (r *countingReadCloser) Read(buffer []byte) (int, error) {
	read, err := r.reader.Read(buffer)
	r.bytesRead += read
	return read, err
}

func (r *countingReadCloser) Close() error {
	r.closed = true
	return nil
}

func pinnedArkRelayInfo() *relaycommon.RelayInfo {
	return &relaycommon.RelayInfo{
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: "doubao-seedance-2-0-260128",
		},
		TaskRelayInfo: &relaycommon.TaskRelayInfo{PinnedProviderRoute: true},
	}
}

func pinnedSeedreamRelayInfo() *relaycommon.RelayInfo {
	return &relaycommon.RelayInfo{
		OriginModelName: "image.seedream.5-lite",
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelType:       constant.ChannelTypeVolcEngine,
			ChannelBaseUrl:    "https://ark.cn-beijing.volces.com",
			UpstreamModelName: constant.PlatformGenerationArkSeedream50LiteModel,
		},
		TaskRelayInfo: &relaycommon.TaskRelayInfo{
			PinnedProviderRoute: true,
			PublicTaskID:        "task_pg_0123456789abcdef0123456789abcdef",
		},
	}
}

func seedreamRequestContext(t *testing.T, body string) (*gin.Context, *httptest.ResponseRecorder) {
	t.Helper()
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest(http.MethodPost, "/v1/video/generations", strings.NewReader(body))
	context.Request.Header.Set("Content-Type", "application/json")
	return context, recorder
}

func TestPinnedSeedreamBuildsExactProtectedRequest(t *testing.T) {
	context, _ := seedreamRequestContext(t, `{
		"model":"image.seedream.5-lite",
		"prompt":"a clean product photograph",
		"size":"2048x2048",
		"duration":1,
		"seconds":"1",
		"metadata":{
			"platform_generation_mode":"text_to_image",
			"resolution":"2048x2048",
			"aspectRatio":"1:1",
			"sampleCount":1,
			"face_enabled":false,
			"stream":true,
			"response_format":"b64_json",
			"output_format":"jpeg",
			"watermark":false,
			"sequential_image_generation":"auto"
		}
	}`)
	info := pinnedSeedreamRelayInfo()
	adaptor := &TaskAdaptor{}
	adaptor.Init(info)
	require.Nil(t, adaptor.ValidateRequestAndSetAction(context, info))

	body, err := adaptor.BuildRequestBody(context, info)
	require.NoError(t, err)
	raw, err := io.ReadAll(body)
	require.NoError(t, err)
	var payload map[string]any
	require.NoError(t, common.Unmarshal(raw, &payload))
	require.Len(t, payload, 8)
	require.Equal(t, constant.PlatformGenerationArkSeedream50LiteModel, payload["model"])
	require.Equal(t, "a clean product photograph", payload["prompt"])
	require.Equal(t, constant.PlatformGenerationArkSeedream50LiteSize, payload["size"])
	require.Equal(t, "disabled", payload["sequential_image_generation"])
	require.Equal(t, false, payload["stream"])
	require.Equal(t, "url", payload["response_format"])
	require.Equal(t, "png", payload["output_format"])
	require.Equal(t, true, payload["watermark"])

	requestURL, err := adaptor.BuildRequestURL(info)
	require.NoError(t, err)
	require.Equal(t, "https://ark.cn-beijing.volces.com/api/v3/images/generations", requestURL)
	require.Nil(t, adaptor.ImmediateTerminalTaskResult(), "submission is not terminal until the provider response is validated")
}

func TestPinnedSeedreamRejectsRouteOrContractDrift(t *testing.T) {
	validBody := `{
		"model":"image.seedream.5-lite",
		"prompt":"a clean product photograph",
		"size":"2048x2048",
		"duration":1,
		"seconds":"1",
		"metadata":{"platform_generation_mode":"text_to_image","resolution":"2048x2048","aspectRatio":"1:1","sampleCount":1,"face_enabled":false}
	}`

	t.Run("wrong upstream model", func(t *testing.T) {
		context, _ := seedreamRequestContext(t, validBody)
		info := pinnedSeedreamRelayInfo()
		info.UpstreamModelName = "doubao-seedream-5-0-lite-260128"
		adaptor := &TaskAdaptor{}
		adaptor.Init(info)
		require.Nil(t, adaptor.ValidateRequestAndSetAction(context, info))
		_, err := adaptor.BuildRequestBody(context, info)
		require.ErrorContains(t, err, "requires native channel type")
	})

	t.Run("input asset", func(t *testing.T) {
		context, _ := seedreamRequestContext(t, strings.Replace(validBody, `"duration":1`, `"images":["https://assets.example/input.png"],"duration":1`, 1))
		info := pinnedSeedreamRelayInfo()
		adaptor := &TaskAdaptor{}
		adaptor.Init(info)
		require.Nil(t, adaptor.ValidateRequestAndSetAction(context, info))
		_, err := adaptor.BuildRequestBody(context, info)
		require.ErrorContains(t, err, "does not accept input assets")
	})

	t.Run("multiple output", func(t *testing.T) {
		context, _ := seedreamRequestContext(t, strings.Replace(validBody, `"sampleCount":1`, `"sampleCount":2`, 1))
		info := pinnedSeedreamRelayInfo()
		adaptor := &TaskAdaptor{}
		adaptor.Init(info)
		require.Nil(t, adaptor.ValidateRequestAndSetAction(context, info))
		_, err := adaptor.BuildRequestBody(context, info)
		require.ErrorContains(t, err, "exactly one output")
	})
}

func TestSeedreamResponseCreatesTerminalReceiptWithoutPersistingProviderURLInTaskData(t *testing.T) {
	context, recorder := seedreamRequestContext(t, `{}`)
	info := pinnedSeedreamRelayInfo()
	adaptor := &TaskAdaptor{seedreamImage: true}
	raw := []byte(`{
		"model":"doubao-seedream-5-0-260128",
		"created":1787479200,
		"data":[{"url":"https://provider.example/result.png?signature=synthetic","size":"2048x2048"}],
		"usage":{"generated_images":1,"output_tokens":321,"total_tokens":456}
	}`)
	response := &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(string(raw)))}

	taskID, taskData, taskErr := adaptor.DoResponse(context, response, info)

	require.Nil(t, taskErr)
	expectedDigest := sha256.Sum256(raw)
	require.Equal(t, "seedream:"+fmt.Sprintf("%x", expectedDigest), taskID)
	terminal := adaptor.ImmediateTerminalTaskResult()
	require.NotNil(t, terminal)
	require.Equal(t, taskID, terminal.TaskID)
	require.Equal(t, string(model.TaskStatusSuccess), terminal.Status)
	require.Equal(t, "100%", terminal.Progress)
	require.Equal(t, "https://provider.example/result.png?signature=synthetic", terminal.Url)
	require.Equal(t, 321, terminal.CompletionTokens)
	require.Equal(t, 456, terminal.TotalTokens)
	require.NotNil(t, terminal.ProviderResultProof)
	require.Equal(t, 1, terminal.ProviderResultProof.SchemaVersion)
	require.Equal(t, generationprofile.VolcengineArkImageProtocolV1, terminal.ProviderResultProof.Protocol)
	require.Equal(t, taskID, terminal.ProviderResultProof.TaskID)
	require.Equal(t, constant.PlatformGenerationArkSeedream50Model, terminal.ProviderResultProof.Model)
	require.Equal(t, "succeeded", terminal.ProviderResultProof.ProviderStatus)
	require.Equal(t, constant.PlatformGenerationArkSeedream50CompatibilitySize, terminal.ProviderResultProof.Resolution)
	require.Zero(t, terminal.ProviderResultProof.DurationSeconds)
	require.Equal(t, "1:1", terminal.ProviderResultProof.AspectRatio)

	terminal.ProviderResultProof.Model = "mutated-by-caller"
	defensiveCopy := adaptor.ImmediateTerminalTaskResult()
	require.NotNil(t, defensiveCopy)
	require.NotNil(t, defensiveCopy.ProviderResultProof)
	require.Equal(t, constant.PlatformGenerationArkSeedream50Model, defensiveCopy.ProviderResultProof.Model)
	require.False(t, terminal.ProviderResultProof.FramesPresent)
	require.Equal(t, 1, terminal.ProviderResultProof.OutputCount)
	require.Equal(t, "image", terminal.ProviderResultProof.MediaType)
	require.NotContains(t, string(taskData), "provider.example")
	require.NotContains(t, string(taskData), "signature")
	require.NotContains(t, recorder.Body.String(), "provider.example")
	var receipt seedreamImageTaskReceipt
	require.NoError(t, common.Unmarshal(taskData, &receipt))
	require.Equal(t, taskID, receipt.ID)
	require.Equal(t, constant.PlatformGenerationArkSeedream50LiteModel, receipt.Model)
	require.Equal(t, constant.PlatformGenerationArkSeedream50LiteSize, receipt.Size)
}

func TestSeedreamResponseFailsClosedOnAmbiguousArtifact(t *testing.T) {
	tests := []struct {
		name string
		body string
	}{
		{
			name: "invalid JSON",
			body: `{not-json}`,
		},
		{
			name: "model mismatch",
			body: `{"model":"doubao-seedream-5-0-lite-260128","data":[{"url":"https://provider.example/result.png","size":"2048x2048"}],"usage":{"generated_images":1}}`,
		},
		{
			name: "model surrounding whitespace",
			body: `{"model":" doubao-seedream-5-0-260128 ","data":[{"url":"https://provider.example/result.png","size":"2048x2048"}],"usage":{"generated_images":1}}`,
		},
		{
			name: "zero artifacts",
			body: `{"model":"doubao-seedream-5-0-260128","data":[],"usage":{"generated_images":0}}`,
		},
		{
			name: "multiple URLs",
			body: `{"model":"doubao-seedream-5-0-260128","data":[{"url":"https://provider.example/one.png","size":"2048x2048"},{"url":"https://provider.example/two.png","size":"2048x2048"}],"usage":{"generated_images":2}}`,
		},
		{
			name: "usage count mismatch",
			body: `{"model":"doubao-seedream-5-0-260128","data":[{"url":"https://provider.example/result.png","size":"2048x2048"}],"usage":{"generated_images":2}}`,
		},
		{
			name: "non HTTPS URL",
			body: `{"model":"doubao-seedream-5-0-260128","data":[{"url":"http://provider.example/result.png","size":"2048x2048"}],"usage":{"generated_images":1}}`,
		},
		{
			name: "URL surrounding whitespace",
			body: `{"model":"doubao-seedream-5-0-260128","data":[{"url":" https://provider.example/result.png ","size":"2048x2048"}],"usage":{"generated_images":1}}`,
		},
		{
			name: "URL user info",
			body: `{"model":"doubao-seedream-5-0-260128","data":[{"url":"https://user:password@provider.example/result.png","size":"2048x2048"}],"usage":{"generated_images":1}}`,
		},
		{
			name: "URL fragment",
			body: `{"model":"doubao-seedream-5-0-260128","data":[{"url":"https://provider.example/result.png#fragment","size":"2048x2048"}],"usage":{"generated_images":1}}`,
		},
		{
			name: "URL length limit",
			body: `{"model":"doubao-seedream-5-0-260128","data":[{"url":"https://provider.example/` + strings.Repeat("a", seedreamArtifactURLMaxLength) + `","size":"2048x2048"}],"usage":{"generated_images":1}}`,
		},
		{
			name: "missing URL",
			body: `{"model":"doubao-seedream-5-0-260128","data":[{"b64_json":"synthetic","size":"2048x2048"}],"usage":{"generated_images":1}}`,
		},
		{
			name: "control character URL",
			body: `{"model":"doubao-seedream-5-0-260128","data":[{"url":"https://provider.example/result.png\nleak","size":"2048x2048"}],"usage":{"generated_images":1}}`,
		},
		{
			name: "size mismatch",
			body: `{"model":"doubao-seedream-5-0-260128","data":[{"url":"https://provider.example/result.png","size":"1024x1024"}],"usage":{"generated_images":1}}`,
		},
		{
			name: "size surrounding whitespace",
			body: `{"model":"doubao-seedream-5-0-260128","data":[{"url":"https://provider.example/result.png","size":" 2048x2048 "}],"usage":{"generated_images":1}}`,
		},
		{
			name: "negative created",
			body: `{"model":"doubao-seedream-5-0-260128","created":-1,"data":[{"url":"https://provider.example/result.png","size":"2048x2048"}],"usage":{"generated_images":1}}`,
		},
		{
			name: "negative generated images",
			body: `{"model":"doubao-seedream-5-0-260128","data":[{"url":"https://provider.example/result.png","size":"2048x2048"}],"usage":{"generated_images":-1}}`,
		},
		{
			name: "negative output tokens",
			body: `{"model":"doubao-seedream-5-0-260128","data":[{"url":"https://provider.example/result.png","size":"2048x2048"}],"usage":{"generated_images":1,"output_tokens":-1,"total_tokens":0}}`,
		},
		{
			name: "negative total tokens",
			body: `{"model":"doubao-seedream-5-0-260128","data":[{"url":"https://provider.example/result.png","size":"2048x2048"}],"usage":{"generated_images":1,"output_tokens":0,"total_tokens":-1}}`,
		},
		{
			name: "total tokens below output tokens",
			body: `{"model":"doubao-seedream-5-0-260128","data":[{"url":"https://provider.example/result.png","size":"2048x2048"}],"usage":{"generated_images":1,"output_tokens":2,"total_tokens":1}}`,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			context, _ := seedreamRequestContext(t, `{}`)
			adaptor := &TaskAdaptor{seedreamImage: true}
			response := &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(test.body))}
			_, _, taskErr := adaptor.DoResponse(context, response, pinnedSeedreamRelayInfo())
			require.NotNil(t, taskErr)
			require.Nil(t, adaptor.ImmediateTerminalTaskResult())
		})
	}
}

func TestConvertPinnedPlatformRequestUsesRouteModelAndOnlyAdmittedControls(t *testing.T) {
	req := relaycommon.TaskSubmitReq{
		Model:    "video.standard.t2v",
		Prompt:   "a product rotating on a clean table",
		Duration: 5,
		Metadata: map[string]interface{}{
			"platform_generation_mode": "text_to_video",
			"resolution":               "720P",
			"aspectRatio":              "16:9",
			"sampleCount":              1,
			"face_enabled":             false,
			"model":                    "metadata-controlled-model",
			"content":                  []interface{}{map[string]interface{}{"type": "text", "text": "overridden"}},
			"callback_url":             "https://attacker.example/callback",
			"duration":                 999,
			"ratio":                    "99:1",
			"watermark":                true,
		},
	}

	payload, err := convertPinnedPlatformRequest(&req, pinnedArkRelayInfo())

	require.NoError(t, err)
	require.Equal(t, "doubao-seedance-2-0-260128", payload.Model)
	require.Equal(t, []ContentItem{{Type: "text", Text: req.Prompt}}, payload.Content)
	require.Equal(t, "720p", payload.Resolution)
	require.Equal(t, "16:9", payload.Ratio)
	require.NotNil(t, payload.Duration)
	require.Equal(t, dto.IntValue(5), *payload.Duration)
	require.Empty(t, payload.CallbackURL)
	require.Nil(t, payload.Watermark)
}

func TestConvertPinnedPlatformRequestBuildsOneImageInput(t *testing.T) {
	req := relaycommon.TaskSubmitReq{
		Prompt:   "animate the first frame",
		Images:   []string{" https://assets.example/first.png "},
		Duration: 10,
		Metadata: map[string]interface{}{
			"platform_generation_mode": "image_to_video",
			"resolution":               "1080p",
			"aspectRatio":              "9:16",
			"sampleCount":              1,
			"face_enabled":             false,
		},
	}

	payload, err := convertPinnedPlatformRequest(&req, pinnedArkRelayInfo())

	require.NoError(t, err)
	require.Len(t, payload.Content, 2)
	require.Equal(t, ContentItem{Type: "text", Text: req.Prompt}, payload.Content[0])
	require.Equal(t, "image_url", payload.Content[1].Type)
	require.NotNil(t, payload.Content[1].ImageURL)
	require.Equal(t, "https://assets.example/first.png", payload.Content[1].ImageURL.URL)
}

func TestConvertPinnedPlatformRequestRejectsContractEscape(t *testing.T) {
	tests := []struct {
		name      string
		request   relaycommon.TaskSubmitReq
		wantError string
	}{
		{
			name: "prompt embeds provider ratio switch",
			request: relaycommon.TaskSubmitReq{
				Prompt:   "a lake --ratio 21:9",
				Duration: 5,
				Metadata: map[string]interface{}{
					"platform_generation_mode": "text_to_video",
					"resolution":               "720p",
					"aspectRatio":              "16:9",
				},
			},
			wantError: "must not override",
		},
		{
			name: "text mode includes an image",
			request: relaycommon.TaskSubmitReq{
				Prompt:   "a lake",
				Images:   []string{"https://assets.example/first.png"},
				Duration: 5,
				Metadata: map[string]interface{}{
					"platform_generation_mode": "text_to_video",
					"resolution":               "720p",
					"aspectRatio":              "16:9",
				},
			},
			wantError: "does not accept image",
		},
		{
			name: "image mode exceeds reviewed image limit",
			request: relaycommon.TaskSubmitReq{
				Prompt: "a lake",
				Images: []string{
					"https://assets.example/1.png", "https://assets.example/2.png", "https://assets.example/3.png",
					"https://assets.example/4.png", "https://assets.example/5.png", "https://assets.example/6.png",
					"https://assets.example/7.png", "https://assets.example/8.png", "https://assets.example/9.png",
					"https://assets.example/10.png",
				},
				Duration: 5,
				Metadata: map[string]interface{}{
					"platform_generation_mode": "image_to_video",
					"resolution":               "720p",
					"aspectRatio":              "16:9",
					"sampleCount":              1,
					"face_enabled":             false,
				},
			},
			wantError: "at most 9 image",
		},
		{
			name: "unknown mode",
			request: relaycommon.TaskSubmitReq{
				Prompt:   "a lake",
				Duration: 5,
				Metadata: map[string]interface{}{
					"platform_generation_mode": "text_to_image",
					"resolution":               "720p",
					"aspectRatio":              "16:9",
					"sampleCount":              1,
					"face_enabled":             false,
				},
			},
			wantError: "does not support generation mode",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := convertPinnedPlatformRequest(&test.request, pinnedArkRelayInfo())
			require.ErrorContains(t, err, test.wantError)
		})
	}
}

func TestConvertPinnedPlatformRequestBuildsSeedance20MultimodalVideoInput(t *testing.T) {
	req := relaycommon.TaskSubmitReq{
		Prompt:   "continue the camera move and preserve the product identity",
		Images:   []string{"https://assets.example/product.png", "https://assets.example/actor.png"},
		Videos:   []string{"https://assets.example/motion.mp4", "https://assets.example/camera.mp4"},
		Audios:   []string{"https://assets.example/voice.wav", "https://assets.example/music.wav"},
		Duration: 15,
		Metadata: map[string]interface{}{
			"platform_generation_mode": "video_to_video",
			"resolution":               "720p",
			"aspectRatio":              "16:9",
			"sampleCount":              1,
			"face_enabled":             false,
		},
	}

	payload, err := convertPinnedPlatformRequest(&req, pinnedArkRelayInfo())
	require.NoError(t, err)
	require.Len(t, payload.Content, 7)
	require.Equal(t, "text", payload.Content[0].Type)
	for _, index := range []int{1, 2} {
		require.Equal(t, "image_url", payload.Content[index].Type)
		require.Equal(t, "reference_image", payload.Content[index].Role)
	}
	for _, index := range []int{3, 4} {
		require.Equal(t, "video_url", payload.Content[index].Type)
		require.Equal(t, "reference_video", payload.Content[index].Role)
	}
	for _, index := range []int{5, 6} {
		require.Equal(t, "audio_url", payload.Content[index].Type)
		require.Equal(t, "reference_audio", payload.Content[index].Role)
	}
}

func TestParseTaskResultAcceptsCurrentArkPollShape(t *testing.T) {
	adaptor := &TaskAdaptor{}
	result, err := adaptor.ParseTaskResult([]byte(`{
		"id":"cgt-20260812-1234",
		"model":"doubao-seedance-2-0-260128",
		"status":"succeeded",
		"content":{"video_url":"https://assets.example/result.mp4"},
		"duration":"5",
		"resolution":"720p",
		"ratio":"16:9",
		"usage":{"completion_tokens":42000,"total_tokens":43000},
		"created_at":"1786478400",
		"updated_at":1786478460
	}`))

	require.NoError(t, err)
	require.Equal(t, model.TaskStatusSuccess, result.Status)
	require.Equal(t, "100%", result.Progress)
	require.Equal(t, "https://assets.example/result.mp4", result.Url)
}

func TestArkVideoSubmissionRemainsAsynchronous(t *testing.T) {
	context, _ := seedreamRequestContext(t, `{}`)
	adaptor := &TaskAdaptor{}
	response := &http.Response{
		StatusCode: http.StatusOK,
		Body:       io.NopCloser(strings.NewReader(`{"id":"cgt-20260823-video-task"}`)),
	}

	taskID, _, taskErr := adaptor.DoResponse(context, response, pinnedArkRelayInfo())
	require.Nil(t, taskErr)
	require.Equal(t, "cgt-20260823-video-task", taskID)
	require.Nil(t, adaptor.ImmediateTerminalTaskResult())
}

func TestDoResponseBoundsAndClosesEveryProviderReceipt(t *testing.T) {
	for _, seedreamImage := range []bool{false, true} {
		name := "async-video"
		info := pinnedArkRelayInfo()
		if seedreamImage {
			name = "synchronous-seedream"
			info = pinnedSeedreamRelayInfo()
		}
		t.Run(name, func(t *testing.T) {
			context, _ := seedreamRequestContext(t, `{}`)
			body := &countingReadCloser{reader: strings.NewReader("provider-secret-marker" + strings.Repeat("x", doubaoProviderResponseLimit+4096))}
			adaptor := &TaskAdaptor{seedreamImage: seedreamImage}

			_, _, taskErr := adaptor.DoResponse(context, &http.Response{StatusCode: http.StatusOK, Body: body}, info)

			require.NotNil(t, taskErr)
			require.Equal(t, "invalid_response", taskErr.Code)
			require.LessOrEqual(t, body.bytesRead, doubaoProviderResponseLimit+1)
			require.True(t, body.closed)
			require.NotContains(t, taskErr.Message, "provider-secret-marker")
			require.NotContains(t, taskErr.Error.Error(), "provider-secret-marker")
			require.Nil(t, adaptor.ImmediateTerminalTaskResult())
		})
	}
}

func TestDoResponseParseErrorsNeverExposeProviderBody(t *testing.T) {
	const secret = "provider-private-payload-should-never-leak"
	tests := []struct {
		name          string
		seedreamImage bool
		info          *relaycommon.RelayInfo
		body          string
	}{
		{name: "async-video", info: pinnedArkRelayInfo(), body: `{"id":"` + secret},
		{name: "synchronous-seedream", seedreamImage: true, info: pinnedSeedreamRelayInfo(), body: `{"model":"` + secret},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			context, _ := seedreamRequestContext(t, `{}`)
			adaptor := &TaskAdaptor{seedreamImage: test.seedreamImage}
			response := &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(test.body))}

			_, _, taskErr := adaptor.DoResponse(context, response, test.info)

			require.NotNil(t, taskErr)
			require.Equal(t, "unmarshal_response_body_failed", taskErr.Code)
			require.NotContains(t, taskErr.Message, secret)
			require.NotContains(t, taskErr.Error.Error(), secret)
			require.Nil(t, adaptor.ImmediateTerminalTaskResult())
		})
	}
}

func TestDoResponseRejectsDuplicateProviderJSONKeys(t *testing.T) {
	tests := []struct {
		name          string
		seedreamImage bool
		info          *relaycommon.RelayInfo
		body          string
	}{
		{
			name: "async task id",
			info: pinnedArkRelayInfo(),
			body: `{"id":"cgt-first","id":"cgt-second"}`,
		},
		{
			name:          "nested Seedream artifact URL",
			seedreamImage: true,
			info:          pinnedSeedreamRelayInfo(),
			body:          `{"model":"doubao-seedream-5-0-260128","data":[{"url":"https://provider.example/first.png","url":"https://provider.example/second.png","size":"2048x2048"}],"usage":{"generated_images":1}}`,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			context, _ := seedreamRequestContext(t, `{}`)
			adaptor := &TaskAdaptor{seedreamImage: test.seedreamImage}
			response := &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(test.body))}

			taskID, taskData, taskErr := adaptor.DoResponse(context, response, test.info)

			require.Empty(t, taskID)
			require.Nil(t, taskData)
			require.NotNil(t, taskErr)
			require.Equal(t, "unmarshal_response_body_failed", taskErr.Code)
			require.Equal(t, "Doubao provider response is not valid unambiguous JSON", taskErr.Message)
			require.Nil(t, adaptor.ImmediateTerminalTaskResult())
		})
	}
}

func TestParseTaskResultTerminatesCancelledAndExpiredTasks(t *testing.T) {
	adaptor := &TaskAdaptor{}
	for _, status := range []string{"cancelled", "expired"} {
		t.Run(status, func(t *testing.T) {
			result, err := adaptor.ParseTaskResult([]byte(`{"id":"cgt-terminal-` + status + `","model":"doubao-seedance-2-0-260128","status":"` + status + `","error":{}}`))
			require.NoError(t, err)
			require.Equal(t, model.TaskStatusFailure, result.Status)
			require.Equal(t, "ARK_TASK_"+strings.ToUpper(status), result.Reason)
			if status == "cancelled" {
				require.Equal(t, "client", result.ProviderResultProof.FailureOwner)
				require.Equal(t, "task_cancelled", result.ProviderResultProof.FailureCode)
			} else {
				require.Equal(t, "relay", result.ProviderResultProof.FailureOwner)
				require.Equal(t, "task_expired", result.ProviderResultProof.FailureCode)
			}
		})
	}
}

func TestParseTaskResultClassifiesProviderFailureWithoutMultilineLeakage(t *testing.T) {
	adaptor := &TaskAdaptor{}
	result, err := adaptor.ParseTaskResult([]byte(`{
		"id":"cgt-failed-identity",
		"model":"doubao-seedance-2-0-260128",
		"status":"failed",
		"error":{"code":"ContentPolicyViolation","message":"unsafe input\nrequest rejected"}
	}`))

	require.NoError(t, err)
	require.Equal(t, model.TaskStatusFailure, result.Status)
	require.Equal(t, "ARK_TASK_FAILED [ContentPolicyViolation]: unsafe input request rejected", result.Reason)
	require.Equal(t, "client", result.ProviderResultProof.FailureOwner)
	require.Equal(t, "content_policy_rejected", result.ProviderResultProof.FailureCode)
}

func TestParseTaskResultClassifiesOnlyAllowlistedProviderFailuresAsProviderOwned(t *testing.T) {
	adaptor := &TaskAdaptor{}
	providerFailure, err := adaptor.ParseTaskResult([]byte(`{
		"id":"cgt-provider-internal-error",
		"model":"doubao-seedance-2-0-260128",
		"status":"failed",
		"error":{"code":"InternalError","message":"provider unavailable"}
	}`))
	require.NoError(t, err)
	require.Equal(t, "provider", providerFailure.ProviderResultProof.FailureOwner)
	require.Equal(t, "provider_service_failure", providerFailure.ProviderResultProof.FailureCode)

	unclassified, err := adaptor.ParseTaskResult([]byte(`{
		"id":"cgt-provider-unknown-error",
		"model":"doubao-seedance-2-0-260128",
		"status":"failed",
		"error":{"code":"FutureUndocumentedCode","message":"unknown"}
	}`))
	require.NoError(t, err)
	require.Equal(t, "relay", unclassified.ProviderResultProof.FailureOwner)
	require.Equal(t, "unclassified_provider_terminal", unclassified.ProviderResultProof.FailureCode)
}

func TestParseTaskResultClassifiesValidationAuthAndQuotaWithoutUsingProviderMessage(t *testing.T) {
	tests := []struct {
		name          string
		providerCode  string
		providerOwner string
		failureCode   string
	}{
		{name: "validation", providerCode: "InvalidParameter", providerOwner: "client", failureCode: "provider_validation_failed"},
		{name: "authentication", providerCode: "InvalidAPIKey", providerOwner: "relay", failureCode: "provider_authentication_failed"},
		{name: "quota", providerCode: "QuotaExceeded", providerOwner: "provider", failureCode: "provider_quota_exhausted"},
		{name: "safe_experience_inference_limit", providerCode: "SetLimitExceeded", providerOwner: "provider", failureCode: "provider_quota_exhausted"},
	}
	adaptor := &TaskAdaptor{}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			body := fmt.Sprintf(`{
				"id":"cgt-classified-%s",
				"model":"doubao-seedance-2-0-260128",
				"status":"failed",
				"error":{"code":%q,"message":"the same opaque message for every class"}
			}`, test.name, test.providerCode)
			result, err := adaptor.ParseTaskResult([]byte(body))
			require.NoError(t, err)
			require.Equal(t, test.providerOwner, result.ProviderResultProof.FailureOwner)
			require.Equal(t, test.failureCode, result.ProviderResultProof.FailureCode)
		})
	}
}

func TestParseTaskResultRejectsAmbiguousTerminalPayloads(t *testing.T) {
	adaptor := &TaskAdaptor{}

	_, err := adaptor.ParseTaskResult([]byte(`{"id":"cgt-ambiguous-success","model":"doubao-seedance-2-0-260128","status":"succeeded","duration":"5","resolution":"720p","ratio":"16:9","content":{}}`))
	require.ErrorContains(t, err, "does not contain video_url")

	_, err = adaptor.ParseTaskResult([]byte(`{"id":"cgt-paused","model":"doubao-seedance-2-0-260128","status":"paused"}`))
	require.ErrorContains(t, err, "unknown Ark task status")
}

func TestParseTaskResultRejectsOutputEvidenceOnEveryFailureTerminal(t *testing.T) {
	adaptor := &TaskAdaptor{}
	evidence := []string{
		`"resolution":"720p"`,
		`"duration":"5"`,
		`"ratio":"16:9"`,
		`"frames":null`,
		`"content":{"videos":null}`,
		`"content":{"video_url":"https://assets.example/must-not-exist.mp4"}`,
	}
	for _, status := range []string{"failed", "cancelled", "expired"} {
		for index, field := range evidence {
			t.Run(fmt.Sprintf("%s-%d", status, index), func(t *testing.T) {
				body := fmt.Sprintf(
					`{"id":"cgt-%s-output-%d","model":"doubao-seedance-2-0-260128","status":%q,"error":{"code":"Terminal"},%s}`,
					status,
					index,
					status,
					field,
				)
				_, err := adaptor.ParseTaskResult([]byte(body))
				require.ErrorContains(t, err, "inconsistent terminal shape")
			})
		}
	}
}

func TestFetchTaskRejectsTaskIDPathInjection(t *testing.T) {
	adaptor := &TaskAdaptor{}

	_, err := adaptor.FetchTask("https://ark.cn-beijing.volces.com", "unused", map[string]any{
		"task_id": "../other-task?leak=true",
	}, "")

	require.ErrorContains(t, err, "invalid task_id")
}

// TestSeedanceFixtureSubmitPollTerminalArtifactContract is an in-process,
// zero-provider-cost acceptance fixture for the complete Ark adapter lifecycle.
// It deliberately exercises the same pinned upstream model, credential and
// provider task ID from submission through every poll so a future "retry" on a
// different account/channel cannot accidentally pass this contract.
func TestSeedanceFixtureSubmitPollTerminalArtifactContract(t *testing.T) {
	const (
		apiKey         = "fixture-only-seedance-key"
		providerTaskID = "cgt-seedance-fixture-20260828"
		providerModel  = "doubao-seedance-2-0-260128"
		artifactURL    = "https://assets.example/verified-provider-result.mp4"
	)

	postCount := 0
	pollCount := 0
	authorizations := make([]string, 0, 3)
	var submitted requestPayload
	provider := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		authorizations = append(authorizations, request.Header.Get("Authorization"))
		writer.Header().Set("Content-Type", "application/json")
		switch {
		case request.Method == http.MethodPost && request.URL.Path == "/api/v3/contents/generations/tasks":
			postCount++
			body, readErr := io.ReadAll(request.Body)
			if readErr != nil || common.Unmarshal(body, &submitted) != nil {
				http.Error(writer, `{"error":"invalid fixture request"}`, http.StatusBadRequest)
				return
			}
			_, _ = writer.Write([]byte(`{"id":"` + providerTaskID + `"}`))
		case request.Method == http.MethodGet && request.URL.Path == "/api/v3/contents/generations/tasks/"+providerTaskID:
			pollCount++
			if pollCount == 1 {
				_, _ = writer.Write([]byte(`{"id":"` + providerTaskID + `","model":"` + providerModel + `","status":"queued"}`))
				return
			}
			_, _ = writer.Write([]byte(`{
				"id":"` + providerTaskID + `",
				"model":"` + providerModel + `",
				"status":"succeeded",
				"content":{"video_url":"` + artifactURL + `"},
				"duration":"5",
				"resolution":"720p",
				"ratio":"16:9",
				"usage":{"completion_tokens":17,"total_tokens":23}
			}`))
		default:
			http.NotFound(writer, request)
		}
	}))
	t.Cleanup(provider.Close)

	context, _ := seedreamRequestContext(t, `{
		"model":"video.seedance.2",
		"prompt":"one continuous product orbit on a clean studio table",
		"duration":5,
		"metadata":{
			"platform_generation_mode":"text_to_video",
			"resolution":"720p",
			"aspectRatio":"16:9",
			"sampleCount":1,
			"face_enabled":false
		}
	}`)
	info := &relaycommon.RelayInfo{
		OriginModelName: "video.seedance.2",
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelType:       constant.ChannelTypeVolcEngine,
			ChannelId:         71,
			ChannelBaseUrl:    provider.URL,
			ApiKey:            apiKey,
			UpstreamModelName: providerModel,
		},
		TaskRelayInfo: &relaycommon.TaskRelayInfo{
			PinnedProviderRoute: true,
			PublicTaskID:        "task_pg_seedance_fixture_20260828",
		},
	}
	adaptor := &TaskAdaptor{}
	adaptor.Init(info)
	require.Nil(t, adaptor.ValidateRequestAndSetAction(context, info))

	requestBody, err := adaptor.BuildRequestBody(context, info)
	require.NoError(t, err)
	requestURL, err := adaptor.BuildRequestURL(info)
	require.NoError(t, err)
	outbound, err := http.NewRequest(http.MethodPost, requestURL, requestBody)
	require.NoError(t, err)
	require.NoError(t, adaptor.BuildRequestHeader(context, outbound, info))
	submitResponse, err := provider.Client().Do(outbound)
	require.NoError(t, err)
	returnedProviderTaskID, _, taskErr := adaptor.DoResponse(context, submitResponse, info)
	require.Nil(t, taskErr)
	require.Equal(t, providerTaskID, returnedProviderTaskID)
	require.Equal(t, providerModel, submitted.Model)
	require.Equal(t, []ContentItem{{Type: "text", Text: "one continuous product orbit on a clean studio table"}}, submitted.Content)
	require.Equal(t, 1, postCount, "one accepted provider submission must never be replayed")

	firstPoll, err := adaptor.FetchTask(provider.URL, apiKey, map[string]any{"task_id": returnedProviderTaskID}, "")
	require.NoError(t, err)
	firstBody, err := io.ReadAll(firstPoll.Body)
	require.NoError(t, err)
	require.NoError(t, firstPoll.Body.Close())
	queued, err := adaptor.ParseTaskResult(firstBody)
	require.NoError(t, err)
	require.Equal(t, model.TaskStatusQueued, queued.Status)
	require.Empty(t, queued.Url)

	secondPoll, err := adaptor.FetchTask(provider.URL, apiKey, map[string]any{"task_id": returnedProviderTaskID}, "")
	require.NoError(t, err)
	secondBody, err := io.ReadAll(secondPoll.Body)
	require.NoError(t, err)
	require.NoError(t, secondPoll.Body.Close())
	terminal, err := adaptor.ParseTaskResult(secondBody)
	require.NoError(t, err)
	require.Equal(t, model.TaskStatusSuccess, terminal.Status)
	require.Equal(t, "100%", terminal.Progress)
	require.Equal(t, artifactURL, terminal.Url)
	require.Equal(t, 17, terminal.CompletionTokens)
	require.Equal(t, 23, terminal.TotalTokens)
	require.Equal(t, 2, pollCount)
	require.Equal(t, []string{"Bearer " + apiKey, "Bearer " + apiKey, "Bearer " + apiKey}, authorizations,
		"submission and polling must remain sticky to the exact accepted route credential")
}

// The image-to-video fixture complements the text-only lifecycle above. It
// proves that the reference survives conversion as an Ark image_url item and
// that the accepted task is polled with the same credential until its terminal
// artifact; no provider account or paid API is involved.
func TestSeedanceImageToVideoFixtureSubmitPollTerminalArtifactContract(t *testing.T) {
	const (
		apiKey         = "fixture-only-seedance-i2v-key"
		providerTaskID = "cgt-seedance-i2v-fixture-20260828"
		providerModel  = "doubao-seedance-2-0-260128"
		artifactURL    = "https://assets.example/verified-i2v-result.mp4"
	)
	var postCount atomic.Int32
	var pollCount atomic.Int32
	var submitted requestPayload
	provider := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Header.Get("Authorization") != "Bearer "+apiKey {
			http.Error(writer, `{"error":"wrong fixture credential"}`, http.StatusUnauthorized)
			return
		}
		writer.Header().Set("Content-Type", "application/json")
		switch {
		case request.Method == http.MethodPost && request.URL.Path == "/api/v3/contents/generations/tasks":
			postCount.Add(1)
			body, err := io.ReadAll(request.Body)
			if err != nil || common.Unmarshal(body, &submitted) != nil {
				http.Error(writer, `{"error":"invalid fixture request"}`, http.StatusBadRequest)
				return
			}
			_, _ = writer.Write([]byte(`{"id":"` + providerTaskID + `"}`))
		case request.Method == http.MethodGet && request.URL.Path == "/api/v3/contents/generations/tasks/"+providerTaskID:
			pollCount.Add(1)
			_, _ = writer.Write([]byte(`{"id":"` + providerTaskID + `","model":"` + providerModel + `","status":"succeeded","duration":"5","resolution":"720p","ratio":"16:9","content":{"video_url":"` + artifactURL + `"},"usage":{"completion_tokens":17,"total_tokens":23}}`))
		default:
			http.NotFound(writer, request)
		}
	}))
	t.Cleanup(provider.Close)

	context, _ := seedreamRequestContext(t, `{
		"model":"video.seedance.2",
		"prompt":"preserve the product while the camera moves slowly",
		"images":["https://assets.example/product-reference.png"],
		"duration":5,
		"metadata":{
			"platform_generation_mode":"image_to_video",
			"resolution":"720p",
			"aspectRatio":"16:9",
			"sampleCount":1,
			"face_enabled":false
		}
	}`)
	info := &relaycommon.RelayInfo{
		OriginModelName: "video.seedance.2",
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelType:       constant.ChannelTypeVolcEngine,
			ChannelId:         72,
			ChannelBaseUrl:    provider.URL,
			ApiKey:            apiKey,
			UpstreamModelName: providerModel,
		},
		TaskRelayInfo: &relaycommon.TaskRelayInfo{PinnedProviderRoute: true, PublicTaskID: "task_pg_seedance_i2v_fixture_20260828"},
	}
	adaptor := &TaskAdaptor{}
	adaptor.Init(info)
	require.Nil(t, adaptor.ValidateRequestAndSetAction(context, info))
	requestBody, err := adaptor.BuildRequestBody(context, info)
	require.NoError(t, err)
	requestURL, err := adaptor.BuildRequestURL(info)
	require.NoError(t, err)
	outbound, err := http.NewRequest(http.MethodPost, requestURL, requestBody)
	require.NoError(t, err)
	require.NoError(t, adaptor.BuildRequestHeader(context, outbound, info))
	submitResponse, err := provider.Client().Do(outbound)
	require.NoError(t, err)
	returnedTaskID, _, taskErr := adaptor.DoResponse(context, submitResponse, info)
	require.Nil(t, taskErr)
	require.Equal(t, providerTaskID, returnedTaskID)
	require.EqualValues(t, 1, postCount.Load())
	require.Len(t, submitted.Content, 2)
	require.Equal(t, "text", submitted.Content[0].Type)
	require.Equal(t, "image_url", submitted.Content[1].Type)
	require.Equal(t, "reference_image", submitted.Content[1].Role)
	require.NotNil(t, submitted.Content[1].ImageURL)
	require.Equal(t, "https://assets.example/product-reference.png", submitted.Content[1].ImageURL.URL)

	pollResponse, err := adaptor.FetchTask(provider.URL, apiKey, map[string]any{"task_id": returnedTaskID}, "")
	require.NoError(t, err)
	pollBody, err := io.ReadAll(pollResponse.Body)
	require.NoError(t, err)
	require.NoError(t, pollResponse.Body.Close())
	terminal, err := adaptor.ParseTaskResult(pollBody)
	require.NoError(t, err)
	require.Equal(t, model.TaskStatusSuccess, terminal.Status)
	require.Equal(t, artifactURL, terminal.Url)
	require.Equal(t, 17, terminal.CompletionTokens)
	require.Equal(t, 23, terminal.TotalTokens)
	require.EqualValues(t, 1, pollCount.Load())
	require.EqualValues(t, 1, postCount.Load(), "polling an accepted i2v task must never resubmit it")
}

func TestSeedanceAdaptorDoesNotInventModelSpecificBillingWithoutEvidence(t *testing.T) {
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(nil)
	adaptor := &TaskAdaptor{}

	// Customer pricing is configured by new-api/Platform, while provider cost
	// is reconciled from the evidence-backed provider contract-rate pipeline.
	// The protocol adapter must not guess either value from a model name,
	// resolution or presence of a video reference.
	ratios := adaptor.EstimateBilling(c, &relaycommon.RelayInfo{
		OriginModelName: "doubao-seedance-unreviewed-future-model",
	})
	require.Empty(t, ratios)
}
