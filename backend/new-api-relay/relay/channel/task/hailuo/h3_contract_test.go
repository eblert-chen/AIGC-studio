package hailuo

import (
	"context"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func h3Fixture(t *testing.T, providerModel, mode string) (relaycommon.TaskSubmitReq, *relaycommon.RelayInfo) {
	t.Helper()
	manifest, found, err := generationprofile.ResolveMiniMaxH3ProviderModel(providerModel)
	require.NoError(t, err)
	require.True(t, found)
	profile, found := generationprofile.Get(manifest.AdapterProfileID)
	require.True(t, found)
	req := relaycommon.TaskSubmitReq{
		Model: manifest.PublicModelID, Prompt: "镜头缓慢靠近窗边的白色陶杯，保持自然光影。", Duration: 5, Seconds: "5",
		Metadata: generationprofile.SnapshotMetadata(map[string]any{
			"platform_generation_mode": mode, "durationSeconds": 5, "resolution": "768p",
			"aspectRatio": "16:9", "sampleCount": 1, "face_enabled": false,
		}, profile),
	}
	if mode == "image_to_video" {
		req.Images = []string{"https://inputs.example/first.png?signature=kept-exactly"}
	}
	if mode == "video_to_video" {
		req.Videos = []string{"https://inputs.example/reference.mp4"}
	}
	info := &relaycommon.RelayInfo{
		OriginModelName: manifest.PublicModelID,
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelType: constant.ChannelTypeMiniMax, ChannelId: 35,
			ChannelBaseUrl: "https://api.minimax.cn", ApiKey: "fixture-only-pinned-credential",
			UpstreamModelName: providerModel,
		},
		TaskRelayInfo: &relaycommon.TaskRelayInfo{PinnedProviderRoute: true, PublicTaskID: "task_pg_h3_fixture"},
	}
	return req, info
}

func h3Context(t *testing.T, req relaycommon.TaskSubmitReq) (*gin.Context, *httptest.ResponseRecorder) {
	t.Helper()
	gin.SetMode(gin.TestMode)
	raw, err := common.Marshal(req)
	require.NoError(t, err)
	recorder := httptest.NewRecorder()
	ctx, _ := gin.CreateTestContext(recorder)
	ctx.Request = httptest.NewRequest(http.MethodPost, "/internal/platform-generations/native-submit", strings.NewReader(string(raw)))
	ctx.Request.Header.Set("Content-Type", "application/json")
	ctx.Set("task_request", req)
	return ctx, recorder
}

func h3MediaFixtures(kind string, count int) []string {
	urls := make([]string, count)
	for index := range urls {
		urls[index] = fmt.Sprintf("https://inputs.example/%s-%d", kind, index)
	}
	return urls
}

func TestPinnedH3RequestPreservesReviewedModesAndReferenceRoles(t *testing.T) {
	for _, test := range []struct {
		name, model, mode, resolution string
		images, videos, audios        int
	}{
		{"H3 text", "MiniMax-H3", "text_to_video", "2k", 0, 0, 0},
		{"H3 image references", "MiniMax-H3", "image_to_video", "768p", 9, 0, 3},
		{"H3 mixed references", "MiniMax-H3", "video_to_video", "2k", 6, 3, 3},
		{"H3 Max text", "MiniMax-H3-Max", "text_to_video", "480p", 0, 0, 0},
	} {
		t.Run(test.name, func(t *testing.T) {
			req, info := h3Fixture(t, test.model, test.mode)
			req.Images, req.Videos, req.Audios = h3MediaFixtures("image", test.images), h3MediaFixtures("video", test.videos), h3MediaFixtures("audio", test.audios)
			req.Metadata["resolution"] = test.resolution
			req.Metadata["aspectRatio"] = "21:9"
			// Untrusted provider-shaped metadata must never replace admitted
			// controls, invent a callback or accidentally select adaptive frames.
			req.Metadata["model"] = "unreviewed-model"
			req.Metadata["ratio"] = "adaptive"
			req.Metadata["duration"] = 999999999
			req.Metadata["callback_url"] = "https://attacker.example/callback"
			req.Metadata["first_frame_image"] = "https://attacker.example/frame.png"
			req.Metadata["content"] = []any{map[string]any{"role": "first_frame"}}
			req.Metadata["aigc_watermark"] = true
			ctx, _ := h3Context(t, req)
			adapter := &TaskAdaptor{}
			adapter.Init(info)
			body, err := adapter.BuildRequestBody(ctx, info)
			require.NoError(t, err)
			raw, err := io.ReadAll(body)
			require.NoError(t, err)
			var payload h3VideoRequest
			require.NoError(t, common.Unmarshal(raw, &payload))
			assert.Equal(t, test.model, payload.Model)
			assert.Equal(t, strings.ToUpper(test.resolution), payload.Resolution)
			assert.Equal(t, 5, payload.Duration)
			assert.Equal(t, "21:9", payload.Ratio)
			require.Len(t, payload.Content, 1+test.images+test.videos+test.audios)
			assert.Equal(t, h3ContentItem{Type: "text", Text: req.Prompt}, payload.Content[0])
			for _, item := range payload.Content[1:] {
				assert.Equal(t, "reference_"+strings.TrimSuffix(item.Type, "_url"), item.Role)
			}
			var fields map[string]any
			require.NoError(t, common.Unmarshal(raw, &fields))
			assert.Len(t, fields, 5)
			assert.NotContains(t, string(raw), "attacker")
			assert.NotContains(t, string(raw), "first_frame")
			endpoint, err := adapter.BuildRequestURL(info)
			require.NoError(t, err)
			assert.Equal(t, "https://api.minimax.cn/v2/video_generation", endpoint)
		})
	}
}

func TestPinnedH3RejectsContractEscapesBeforeProviderSubmission(t *testing.T) {
	for _, test := range []struct {
		name   string
		model  string
		mode   string
		mutate func(*relaycommon.TaskSubmitReq, *relaycommon.RelayInfo)
	}{
		{"unpinned", "MiniMax-H3", "text_to_video", func(_ *relaycommon.TaskSubmitReq, i *relaycommon.RelayInfo) { i.PinnedProviderRoute = false }},
		{"unknown H3 model", "MiniMax-H3", "text_to_video", func(_ *relaycommon.TaskSubmitReq, i *relaycommon.RelayInfo) { i.UpstreamModelName = "MiniMax-H3-Fast" }},
		{"wrong channel", "MiniMax-H3", "text_to_video", func(_ *relaycommon.TaskSubmitReq, i *relaycommon.RelayInfo) {
			i.ChannelType = constant.ChannelTypeVolcEngine
		}},
		{"missing snapshot", "MiniMax-H3", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) {
			delete(r.Metadata, generationprofile.MetadataProfileID)
			delete(r.Metadata, generationprofile.MetadataProfileRevision)
			delete(r.Metadata, generationprofile.MetadataProfileSnapshot)
		}},
		{"tampered snapshot", "MiniMax-H3", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) {
			r.Metadata[generationprofile.MetadataProfileSnapshot] = `{}`
		}},
		{"cross model profile", "MiniMax-H3", "text_to_video", func(_ *relaycommon.TaskSubmitReq, i *relaycommon.RelayInfo) { i.UpstreamModelName = "MiniMax-H3-Max" }},
		{"unknown mode", "MiniMax-H3", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) {
			r.Metadata["platform_generation_mode"] = "regeneration"
		}},
		{"empty prompt", "MiniMax-H3", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) { r.Prompt = " \n" }},
		{"long Chinese prompt", "MiniMax-H3", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) { r.Prompt = strings.Repeat("画", 7001) }},
		{"fractional duration", "MiniMax-H3", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) { r.Metadata["durationSeconds"] = 5.5 }},
		{"huge duration", "MiniMax-H3", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) { r.Metadata["durationSeconds"] = 1e100 }},
		{"NaN duration", "MiniMax-H3", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) {
			r.Metadata["durationSeconds"] = math.NaN()
		}},
		{"string duration", "MiniMax-H3", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) { r.Metadata["durationSeconds"] = "5" }},
		{"duration alias drift", "MiniMax-H3", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) { r.Duration = 6 }},
		{"seconds alias drift", "MiniMax-H3", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) { r.Seconds = "05" }},
		{"duration outside range", "MiniMax-H3", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) {
			r.Duration, r.Seconds, r.Metadata["durationSeconds"] = 16, "16", 16
		}},
		{"Max rejects four seconds", "MiniMax-H3-Max", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) {
			r.Duration, r.Seconds, r.Metadata["durationSeconds"] = 4, "4", 4
		}},
		{"Max rejects 2K", "MiniMax-H3-Max", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) { r.Metadata["resolution"] = "2k" }},
		{"H3 rejects 480p", "MiniMax-H3", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) { r.Metadata["resolution"] = "480p" }},
		{"adaptive ratio", "MiniMax-H3", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) { r.Metadata["aspectRatio"] = "adaptive" }},
		{"output count", "MiniMax-H3", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) { r.Metadata["sampleCount"] = 2 }},
		{"face enabled", "MiniMax-H3", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) { r.Metadata["face_enabled"] = true }},
		{"missing face contract", "MiniMax-H3", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) { delete(r.Metadata, "face_enabled") }},
		{"text mode media", "MiniMax-H3", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) { r.Images = h3MediaFixtures("image", 1) }},
		{"Max reference media", "MiniMax-H3-Max", "text_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) { r.Images = h3MediaFixtures("image", 1) }},
		{"image required", "MiniMax-H3", "image_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) { r.Images = nil }},
		{"video required", "MiniMax-H3", "video_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) { r.Videos = nil }},
		{"ten images", "MiniMax-H3", "image_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) { r.Images = h3MediaFixtures("image", 10) }},
		{"four audio", "MiniMax-H3", "image_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) { r.Audios = h3MediaFixtures("audio", 4) }},
		{"thirteen mixed assets", "MiniMax-H3", "video_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) {
			r.Images, r.Videos, r.Audios = h3MediaFixtures("image", 7), h3MediaFixtures("video", 3), h3MediaFixtures("audio", 3)
		}},
		{"four videos", "MiniMax-H3", "video_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) { r.Videos = h3MediaFixtures("video", 4) }},
		{"image mode cannot hide video", "MiniMax-H3", "image_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) {
			r.InputReference = "https://inputs.example/hidden.mp4"
		}},
		{"HTTP reference", "MiniMax-H3", "image_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) {
			r.Images[0] = "http://inputs.example/image.png"
		}},
		{"reference with credentials", "MiniMax-H3", "image_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) {
			r.Images[0] = "https://secret:token@inputs.example/image.png"
		}},
		{"reference with fragment", "MiniMax-H3", "image_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) {
			r.Images[0] = "https://inputs.example/image.png#fragment"
		}},
		{"image alias mismatch", "MiniMax-H3", "image_to_video", func(r *relaycommon.TaskSubmitReq, _ *relaycommon.RelayInfo) {
			r.Image = "https://inputs.example/hidden.png"
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			req, info := h3Fixture(t, test.model, test.mode)
			test.mutate(&req, info)
			payload, err := convertPinnedH3Request(&req, info)
			require.Error(t, err)
			assert.Nil(t, payload)
		})
	}
}

func TestLegacyHailuoKeepsItsV1RequestContract(t *testing.T) {
	req := relaycommon.TaskSubmitReq{
		Model: "MiniMax-Hailuo-2.3", Prompt: "A cup on a table", Duration: 6, Size: "1920x1080",
		Metadata: map[string]any{"first_frame_image": "https://inputs.example/legacy.png"},
	}
	info := &relaycommon.RelayInfo{
		ChannelMeta: &relaycommon.ChannelMeta{ChannelType: constant.ChannelTypeMiniMax, ChannelBaseUrl: "https://api.minimax.chat", UpstreamModelName: req.Model},
	}
	ctx, _ := h3Context(t, req)
	adapter := &TaskAdaptor{}
	adapter.Init(info)
	body, err := adapter.BuildRequestBody(ctx, info)
	require.NoError(t, err)
	raw, err := io.ReadAll(body)
	require.NoError(t, err)
	var payload VideoRequest
	require.NoError(t, common.Unmarshal(raw, &payload))
	assert.Equal(t, req.Model, payload.Model)
	assert.Equal(t, "1080P", payload.Resolution)
	assert.Equal(t, "https://inputs.example/legacy.png", payload.FirstFrameImage)
	endpoint, err := adapter.BuildRequestURL(info)
	require.NoError(t, err)
	assert.Equal(t, "https://api.minimax.chat/v1/video_generation", endpoint)
}

func TestH3ProviderOriginFailsBeforeBuildingAProviderRequest(t *testing.T) {
	for _, origin := range []string{
		"", "http://api.minimax.cn", "https://user:credential@api.minimax.cn", "https://api.minimax.cn/v1",
		"https://api.minimax.cn?query=value", "https://api.minimax.cn?", "https://api.minimax.cn#", " https://api.minimax.cn",
		"https://api.minimax.cn:443", "https://api.minimaxi.com", "https://unapproved.example",
	} {
		req, info := h3Fixture(t, "MiniMax-H3", "text_to_video")
		info.ChannelBaseUrl = origin
		payload, err := convertPinnedH3Request(&req, info)
		require.Error(t, err)
		assert.Nil(t, payload)
	}
	for _, origin := range []string{"https://api.minimax.cn", "https://api.minimax.io/"} {
		req, info := h3Fixture(t, "MiniMax-H3", "text_to_video")
		info.ChannelBaseUrl = origin
		payload, err := convertPinnedH3Request(&req, info)
		require.NoError(t, err)
		assert.Equal(t, "MiniMax-H3", payload.Model)
	}
}

type h3FixtureTransport func(*http.Request) (*http.Response, error)

func (transport h3FixtureTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	return transport(req)
}

func installH3FixtureTransport(t *testing.T, transport h3FixtureTransport) {
	t.Helper()
	client, err := service.GetHttpClientWithProxy("")
	require.NoError(t, err)
	original := *client
	client.Transport = transport
	client.CheckRedirect = nil
	t.Cleanup(func() { *client = original })
}

func h3FixtureResponse(req *http.Request, status int, body string) *http.Response {
	return &http.Response{StatusCode: status, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(body)), Request: req}
}

func TestH3V2SubmitPollUsesExactModelAndStickyCredentials(t *testing.T) {
	const taskID = "424010985738629"
	const artifactURL = "https://artifacts.example/result.mp4?signature=private"
	req, info := h3Fixture(t, "MiniMax-H3", "image_to_video")
	posts, polls := 0, 0
	installH3FixtureTransport(t, func(request *http.Request) (*http.Response, error) {
		assert.Equal(t, "Bearer "+info.ApiKey, request.Header.Get("Authorization"))
		assert.Equal(t, "api.minimax.cn", request.URL.Host)
		switch request.Method + " " + request.URL.Path {
		case "POST /v2/video_generation":
			posts++
			assert.Nil(t, request.GetBody, "a provider POST cannot be replayed by net/http")
			raw, err := io.ReadAll(request.Body)
			require.NoError(t, err)
			var payload h3VideoRequest
			require.NoError(t, common.Unmarshal(raw, &payload))
			assert.Equal(t, "MiniMax-H3", payload.Model)
			require.Len(t, payload.Content, 2)
			assert.Equal(t, "reference_image", payload.Content[1].Role)
			assert.Equal(t, req.Images[0], payload.Content[1].ImageURL.URL)
			return h3FixtureResponse(request, http.StatusOK, `{"task_id":"`+taskID+`","debug_url":"https://private.example/secret"}`), nil
		case "GET /v2/query/video_generation/" + taskID:
			polls++
			if polls == 1 {
				return h3FixtureResponse(request, http.StatusOK, `{"task":{"id":"`+taskID+`","model":"MiniMax-H3","status":"queued","task_type":"generation","modality":"video"}}`), nil
			}
			return h3FixtureResponse(request, http.StatusOK, `{"task":{"id":"`+taskID+`","model":"MiniMax-H3","status":"succeeded","task_type":"generation","modality":"video","resolution":"768P","duration":5,"ratio":"16:9","content":{"url":"`+artifactURL+`"},"usage":{"total_seconds":5,"input_seconds":0,"output_seconds":5,"input_image_count":1,"input_audio_seconds":0,"total_tokens":273890,"prompt_tokens":13500,"completion_tokens":260390}}}`), nil
		default:
			t.Errorf("unexpected provider request: %s %s", request.Method, request.URL.Path)
			return h3FixtureResponse(request, http.StatusNotFound, `{}`), nil
		}
	})
	adapter := &TaskAdaptor{}
	adapter.Init(info)
	ctx, recorder := h3Context(t, req)
	require.Nil(t, adapter.ValidateRequestAndSetAction(ctx, info))
	body, err := adapter.BuildRequestBody(ctx, info)
	require.NoError(t, err)
	response, err := adapter.DoRequest(ctx, info, body)
	require.NoError(t, err)
	id, data, taskErr := adapter.DoResponse(ctx, response, info)
	require.Nil(t, taskErr)
	assert.Equal(t, taskID, id)
	assert.NotContains(t, string(data), "private.example")
	assert.NotContains(t, string(data), taskID)
	assert.Contains(t, string(data), "provider_response_sha256")
	assert.NotContains(t, recorder.Body.String(), taskID)
	assert.Contains(t, recorder.Body.String(), info.PublicTaskID)
	for _, expectedStatus := range []string{model.TaskStatusQueued, model.TaskStatusSuccess} {
		response, err = adapter.FetchTaskWithContext(context.Background(), info.ChannelBaseUrl, info.ApiKey, map[string]any{"task_id": id, "provider_model": info.UpstreamModelName}, "")
		require.NoError(t, err)
		raw, err := io.ReadAll(response.Body)
		require.NoError(t, err)
		require.NoError(t, response.Body.Close())
		result, err := adapter.ParseTaskResult(raw)
		require.NoError(t, err)
		assert.Equal(t, expectedStatus, result.Status)
		require.NotNil(t, result.ProviderResultProof)
		assert.Equal(t, generationprofile.MiniMaxH3VideoProtocolV2, result.ProviderResultProof.Protocol)
		assert.Equal(t, taskID, result.ProviderResultProof.TaskID)
		assert.Zero(t, result.TotalTokens, "H3 usage is not a native billing multiplier")
		if expectedStatus == model.TaskStatusSuccess {
			assert.Equal(t, artifactURL, result.Url)
			assert.Equal(t, "768p", result.ProviderResultProof.Resolution)
			assert.Equal(t, 5, result.ProviderResultProof.DurationSeconds)
			assert.Equal(t, "16:9", result.ProviderResultProof.AspectRatio)
			assert.Equal(t, 1, result.ProviderResultProof.OutputCount)
			assert.Equal(t, 5, result.ProviderResultProof.ProviderTotalSeconds)
			assert.Zero(t, result.ProviderResultProof.InputSeconds)
			assert.Equal(t, 5, result.ProviderResultProof.OutputSeconds)
			assert.Equal(t, 1, result.ProviderResultProof.InputImageCount)
		}
	}
	assert.Equal(t, 1, posts)
	assert.Equal(t, 2, polls)
}

func TestH3TransportNeverReplaysUnknownSubmissionOrFollowsRedirect(t *testing.T) {
	for _, test := range []struct {
		name      string
		status    int
		transport error
	}{
		{"redirect", http.StatusTemporaryRedirect, nil},
		{"server ambiguity", http.StatusInternalServerError, nil},
		{"lost acknowledgement", 0, errors.New("fixture connection reset after request body")},
	} {
		t.Run(test.name, func(t *testing.T) {
			calls := 0
			installH3FixtureTransport(t, func(req *http.Request) (*http.Response, error) {
				calls++
				if test.transport != nil {
					return nil, test.transport
				}
				response := h3FixtureResponse(req, test.status, `{"type":"error","error":{"message":"secret provider detail"}}`)
				response.Header.Set("Location", "https://redirect.example/replay")
				return response, nil
			})
			req, info := h3Fixture(t, "MiniMax-H3", "text_to_video")
			ctx, _ := h3Context(t, req)
			adapter := &TaskAdaptor{}
			adapter.Init(info)
			body, err := adapter.BuildRequestBody(ctx, info)
			require.NoError(t, err)
			response, err := adapter.DoRequest(ctx, info, body)
			if test.transport != nil {
				require.Error(t, err)
			} else {
				require.NoError(t, err)
				_, _, taskErr := adapter.DoResponse(ctx, response, info)
				require.NotNil(t, taskErr)
				assert.False(t, taskErr.LocalError, "post-send failure is not proof that no task exists")
				assert.NotContains(t, taskErr.Message, "secret provider detail")
			}
			assert.Equal(t, 1, calls)
		})
	}
}

func TestH3PollSelectsProtocolByProviderModelAndRejectsUnsafeIdentity(t *testing.T) {
	var paths []string
	installH3FixtureTransport(t, func(req *http.Request) (*http.Response, error) {
		paths = append(paths, req.URL.RequestURI())
		response := h3FixtureResponse(req, http.StatusTemporaryRedirect, `{}`)
		response.Header.Set("Location", "https://redirect.example/leak-credential")
		return response, nil
	})
	adapter := &TaskAdaptor{}
	for _, test := range []struct{ providerModel, expectedPath string }{
		{"MiniMax-H3", "/v2/query/video_generation/task-1"},
		{"MiniMax-H3-Max", "/v2/query/video_generation/task-1"},
	} {
		response, err := adapter.FetchTaskWithContext(context.Background(), "https://api.minimax.io", "exact-task-credential", map[string]any{"task_id": "task-1", "provider_model": test.providerModel}, "")
		require.NoError(t, err)
		require.NoError(t, response.Body.Close())
		assert.Equal(t, http.StatusTemporaryRedirect, response.StatusCode)
		assert.Equal(t, test.expectedPath, paths[len(paths)-1])
	}
	assert.Len(t, paths, 2, "poll redirects must not forward the pinned credential")
	for _, taskID := range []string{"", "../other-task", "task/id", "task?model=other", "task#fragment", "task%2Fother", " task"} {
		_, err := adapter.FetchTaskWithContext(context.Background(), "https://api.minimax.cn", "key", map[string]any{"task_id": taskID, "provider_model": "MiniMax-H3"}, "")
		require.Error(t, err)
	}
	_, err := adapter.FetchTaskWithContext(context.Background(), "https://api.minimax.cn", "key", map[string]any{"task_id": "task-1", "provider_model": "MiniMax-H3-Unknown"}, "")
	require.Error(t, err)
	assert.Len(t, paths, 2)
}

func TestH3AndLegacyTasksCanShareAChannelWithoutProtocolDrift(t *testing.T) {
	var paths []string
	installH3FixtureTransport(t, func(req *http.Request) (*http.Response, error) {
		paths = append(paths, req.URL.RequestURI())
		if strings.HasPrefix(req.URL.Path, H3QueryTaskEndpoint) {
			return h3FixtureResponse(req, http.StatusOK, `{"task":{"id":"h3-task","model":"MiniMax-H3","status":"running","task_type":"generation","modality":"video"}}`), nil
		}
		return h3FixtureResponse(req, http.StatusOK, `{"task_id":"legacy-task","status":"Preparing","base_resp":{"status_code":0}}`), nil
	})
	_, info := h3Fixture(t, "MiniMax-H3", "text_to_video")
	adapter := &TaskAdaptor{}
	adapter.Init(info)
	for _, task := range []struct{ id, providerModel string }{
		{"h3-task", "MiniMax-H3"}, {"legacy-task", "MiniMax-Hailuo-2.3"},
	} {
		response, err := adapter.FetchTaskWithContext(context.Background(), info.ChannelBaseUrl, info.ApiKey, map[string]any{"task_id": task.id, "provider_model": task.providerModel}, "")
		require.NoError(t, err)
		raw, err := io.ReadAll(response.Body)
		require.NoError(t, err)
		require.NoError(t, response.Body.Close())
		result, err := adapter.ParseTaskResult(raw)
		require.NoError(t, err)
		assert.Equal(t, model.TaskStatusInProgress, result.Status)
		if task.providerModel == "MiniMax-H3" {
			require.NotNil(t, result.ProviderResultProof)
			assert.Equal(t, "running", result.ProviderResultProof.ProviderStatus)
		} else {
			assert.Nil(t, result.ProviderResultProof)
		}
	}
	assert.Equal(t, []string{"/v2/query/video_generation/h3-task", "/v1/query/video_generation?task_id=legacy-task"}, paths)
}

type h3TrackedBody struct {
	reader io.Reader
	read   int
	closed bool
}

func (body *h3TrackedBody) Read(buffer []byte) (int, error) {
	count, err := body.reader.Read(buffer)
	body.read += count
	return count, err
}

func (body *h3TrackedBody) Close() error { body.closed = true; return nil }

func TestH3AcknowledgementRejectsAmbiguityAndBoundsSecretResponses(t *testing.T) {
	for _, raw := range []string{
		`{}`, `null`, `{"task_id":""}`, `{"task_id":123}`, `{"task_id":"../task"}`,
		`{"task_id":"a","task_id":"b"}`, `{"task_id":"a","error":{"message":"secret-provider-token"}}`,
		`{"task_id":"a","base_resp":{"status_code":0}}`, `{"task_id":"a","task":{}}`,
		`{"task_id":`, "secret-provider-token" + strings.Repeat("x", h3ProviderResponseLimit+1),
	} {
		req, info := h3Fixture(t, "MiniMax-H3", "text_to_video")
		ctx, recorder := h3Context(t, req)
		adapter := &TaskAdaptor{}
		adapter.Init(info)
		body := &h3TrackedBody{reader: strings.NewReader(raw)}
		id, data, taskErr := adapter.DoResponse(ctx, &http.Response{StatusCode: http.StatusOK, Body: body}, info)
		require.NotNil(t, taskErr)
		assert.Empty(t, id)
		assert.Empty(t, data)
		assert.Empty(t, recorder.Body.String())
		assert.NotContains(t, taskErr.Message, "secret-provider-token")
		assert.True(t, body.closed)
		assert.LessOrEqual(t, body.read, h3ProviderResponseLimit+1)
		assert.False(t, taskErr.LocalError)
	}
}

func TestH3TaskIdentityMatchesDurable191ByteBoundary(t *testing.T) {
	for _, test := range []struct {
		length   int
		accepted bool
	}{{191, true}, {192, false}, {256, false}} {
		t.Run(fmt.Sprintf("ASCII-%d", test.length), func(t *testing.T) {
			providerTaskID := strings.Repeat("x", test.length)
			task := h3TaskFixture()
			task["id"] = providerTaskID
			queryBody, err := common.Marshal(map[string]any{"task": task})
			require.NoError(t, err)
			posts, polls := 0, 0
			installH3FixtureTransport(t, func(request *http.Request) (*http.Response, error) {
				if request.Method == http.MethodPost && request.URL.Path == H3VideoEndpoint {
					posts++
					return h3FixtureResponse(request, http.StatusOK, `{"task_id":"`+providerTaskID+`"}`), nil
				}
				polls++
				assert.Equal(t, http.MethodGet, request.Method)
				assert.Equal(t, H3QueryTaskEndpoint+providerTaskID, request.URL.Path)
				return h3FixtureResponse(request, http.StatusOK, string(queryBody)), nil
			})
			req, info := h3Fixture(t, "MiniMax-H3", "text_to_video")
			ctx, recorder := h3Context(t, req)
			adapter := &TaskAdaptor{}
			adapter.Init(info)
			body, err := adapter.BuildRequestBody(ctx, info)
			require.NoError(t, err)
			response, err := adapter.DoRequest(ctx, info, body)
			require.NoError(t, err)
			id, receipt, taskErr := adapter.DoResponse(ctx, response, info)
			if test.accepted {
				require.Nil(t, taskErr)
				assert.Equal(t, providerTaskID, id)
				assert.NotEmpty(t, receipt)
			} else {
				require.NotNil(t, taskErr)
				assert.False(t, taskErr.LocalError, "an invalid paid acknowledgement must remain an unknown submission")
				assert.Equal(t, http.StatusBadGateway, taskErr.StatusCode)
				assert.Empty(t, id)
				assert.Empty(t, receipt)
				assert.Empty(t, recorder.Body.String())
			}
			assert.Equal(t, 1, posts, "rejecting the acknowledgement must never replay the provider POST")

			pollResponse, pollErr := adapter.FetchTaskWithContext(context.Background(), info.ChannelBaseUrl, info.ApiKey, map[string]any{
				"task_id": providerTaskID, "provider_model": info.UpstreamModelName,
			}, "")
			result, parseErr := adapter.ParseTaskResult(queryBody)
			if test.accepted {
				require.NoError(t, pollErr)
				require.NoError(t, pollResponse.Body.Close())
				require.NoError(t, parseErr)
				assert.Equal(t, providerTaskID, result.TaskID)
				assert.Equal(t, providerTaskID, result.ProviderResultProof.TaskID)
				assert.Equal(t, 1, polls)
			} else {
				require.Error(t, pollErr)
				assert.Nil(t, pollResponse)
				require.Error(t, parseErr)
				assert.Nil(t, result)
				assert.Zero(t, polls, "unpersistable identities must not reach the provider poll endpoint")
			}
			assert.Equal(t, 1, posts)
		})
	}
}

func h3TaskFixture() map[string]any {
	return map[string]any{
		"id": "424010985738629", "model": "MiniMax-H3", "status": "succeeded",
		"task_type": "generation", "modality": "video", "resolution": "2K", "duration": 5, "ratio": "16:9",
		"content": map[string]any{"url": "https://artifacts.example/output.mp4"},
		"usage": map[string]any{
			"total_seconds": 5, "input_seconds": 0, "output_seconds": 5,
			"input_image_count": 0, "input_audio_seconds": 0,
			"total_tokens": 273890, "prompt_tokens": 13500, "completion_tokens": 260390,
		},
	}
}

func TestH3TerminalProofPreservesSpecsWithoutExposingProviderFailures(t *testing.T) {
	for _, test := range []struct{ status, code, owner, failureCode string }{
		{"failed", "1026", "client", "content_policy_rejected"},
		{"failed", "1000", "provider", "provider_service_failure"},
		{"failed", "vendor_future_code", "relay", "unclassified_provider_terminal"},
		{"cancelled", "", "client", "task_cancelled"},
	} {
		t.Run(test.status+test.code, func(t *testing.T) {
			task := h3TaskFixture()
			task["status"] = test.status
			delete(task, "content")
			delete(task, "usage")
			if test.code != "" {
				task["error"] = map[string]any{"code": test.code, "message": "sensitive provider response\nhttps://secret.example?token=private"}
			}
			raw, err := common.Marshal(map[string]any{"task": task})
			require.NoError(t, err)
			result, err := (&TaskAdaptor{}).ParseTaskResult(raw)
			require.NoError(t, err, "documented failure shapes echo requested output specs")
			assert.Equal(t, model.TaskStatusFailure, result.Status)
			assert.Equal(t, test.owner, result.ProviderResultProof.FailureOwner)
			assert.Equal(t, test.failureCode, result.ProviderResultProof.FailureCode)
			assert.Empty(t, result.Url)
			assert.Empty(t, result.ProviderResultProof.Resolution)
			assert.Zero(t, result.ProviderResultProof.DurationSeconds)
			assert.NotContains(t, result.Reason, "secret.example")
			assert.NotContains(t, result.Reason, "sensitive provider response")
		})
	}
	for _, spec := range []struct{ model, resolution, canonical string }{
		{"MiniMax-H3", "2K", "2k"}, {"MiniMax-H3", "768P", "768p"}, {"MiniMax-H3-Max", "480P", "480p"},
	} {
		task := h3TaskFixture()
		task["model"], task["resolution"] = spec.model, spec.resolution
		raw, err := common.Marshal(map[string]any{"task": task})
		require.NoError(t, err)
		result, err := (&TaskAdaptor{}).ParseTaskResult(raw)
		require.NoError(t, err)
		assert.Equal(t, spec.canonical, result.ProviderResultProof.Resolution)
		assert.Equal(t, spec.model, result.ProviderResultProof.Model)
		assert.Equal(t, "video", result.ProviderResultProof.MediaType)
	}
}

func TestH3QueryRejectsUnverifiableOrContradictoryResults(t *testing.T) {
	for _, test := range []struct {
		name   string
		mutate func(map[string]any)
	}{
		{"missing id", func(task map[string]any) { delete(task, "id") }},
		{"path injection", func(task map[string]any) { task["id"] = "../another-task" }},
		{"missing model", func(task map[string]any) { delete(task, "model") }},
		{"unreviewed model", func(task map[string]any) { task["model"] = "MiniMax-H3-unreviewed" }},
		{"missing generation type", func(task map[string]any) { delete(task, "task_type") }},
		{"regeneration is not generation", func(task map[string]any) { task["task_type"] = "regeneration" }},
		{"context is not video", func(task map[string]any) { task["task_type"], task["modality"] = "h3_context_ir", "text" }},
		{"missing modality", func(task map[string]any) { delete(task, "modality") }},
		{"unknown state", func(task map[string]any) { task["status"] = "Success" }},
		{"missing resolution", func(task map[string]any) { delete(task, "resolution") }},
		{"invalid resolution", func(task map[string]any) { task["resolution"] = "1080P" }},
		{"Max 2K drift", func(task map[string]any) { task["model"] = "MiniMax-H3-Max" }},
		{"missing duration", func(task map[string]any) { delete(task, "duration") }},
		{"fractional duration", func(task map[string]any) { task["duration"] = 5.5 }},
		{"string duration", func(task map[string]any) { task["duration"] = "5" }},
		{"out of range duration", func(task map[string]any) { task["duration"] = 16 }},
		{"adaptive drift", func(task map[string]any) { task["ratio"] = "adaptive" }},
		{"missing ratio", func(task map[string]any) { delete(task, "ratio") }},
		{"missing content", func(task map[string]any) { delete(task, "content") }},
		{"insecure URL", func(task map[string]any) {
			task["content"] = map[string]any{"url": "http://artifacts.example/output.mp4"}
		}},
		{"credential URL", func(task map[string]any) {
			task["content"] = map[string]any{"url": "https://user:secret@artifacts.example/output.mp4"}
		}},
		{"multiple artifacts", func(task map[string]any) {
			task["content"] = map[string]any{"url": "https://artifacts.example/output.mp4", "urls": []string{"https://artifacts.example/second.mp4"}}
		}},
		{"text result", func(task map[string]any) { task["content"] = map[string]any{"prompt": "not a video"} }},
		{"unexpected frames", func(task map[string]any) { task["frames"] = []string{} }},
		{"alternate outputs", func(task map[string]any) { task["outputs"] = []string{"https://artifacts.example/second.mp4"} }},
		{"success with error", func(task map[string]any) { task["error"] = map[string]any{"code": "1026", "message": "rejected"} }},
		{"failure with artifact", func(task map[string]any) { task["status"], task["error"] = "failed", map[string]any{"code": "1026"} }},
		{"failure without code", func(task map[string]any) { task["status"] = "failed"; delete(task, "content") }},
		{"pending with artifact", func(task map[string]any) { task["status"] = "running" }},
	} {
		t.Run(test.name, func(t *testing.T) {
			task := h3TaskFixture()
			test.mutate(task)
			raw, err := common.Marshal(map[string]any{"task": task})
			require.NoError(t, err)
			result, err := (&TaskAdaptor{h3Video: true}).ParseTaskResult(raw)
			require.Error(t, err)
			assert.Nil(t, result)
		})
	}
	for _, raw := range []string{
		`{"task":{"id":"a","id":"b"}}`, `{"task":null}`, `{"task":{}`, `{"task_id":"v1-id","base_resp":{"status_code":0}}`,
		`{"type":"error","error":{"message":"secret-provider-message"}}`, strings.Repeat("x", h3ProviderResponseLimit+1),
	} {
		result, err := (&TaskAdaptor{h3Video: true}).ParseTaskResult([]byte(raw))
		require.Error(t, err)
		assert.Nil(t, result)
		assert.NotContains(t, err.Error(), "secret-provider-message")
	}
}
