package doubao

import (
	"testing"

	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/stretchr/testify/require"
)

func pinnedArkRelayInfo() *relaycommon.RelayInfo {
	return &relaycommon.RelayInfo{
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: "doubao-seedance-2-0-260128",
		},
		TaskRelayInfo: &relaycommon.TaskRelayInfo{PinnedProviderRoute: true},
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
			name: "image mode includes two images",
			request: relaycommon.TaskSubmitReq{
				Prompt:   "a lake",
				Images:   []string{"https://assets.example/first.png", "https://assets.example/last.png"},
				Duration: 5,
				Metadata: map[string]interface{}{
					"platform_generation_mode": "image_to_video",
					"resolution":               "720p",
					"aspectRatio":              "16:9",
				},
			},
			wantError: "exactly one image",
		},
		{
			name: "video input is not advertised",
			request: relaycommon.TaskSubmitReq{
				Prompt:         "continue the camera move",
				InputReference: "https://assets.example/input.mp4",
				Duration:       5,
				Metadata: map[string]interface{}{
					"platform_generation_mode": "video_to_video",
					"resolution":               "720p",
					"aspectRatio":              "16:9",
				},
			},
			wantError: "does not advertise video input",
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

func TestParseTaskResultAcceptsCurrentArkPollShape(t *testing.T) {
	adaptor := &TaskAdaptor{}
	result, err := adaptor.ParseTaskResult([]byte(`{
		"id":"cgt-20260812-1234",
		"model":"doubao-seedance-2-0-260128",
		"status":"succeeded",
		"content":{"video_url":"https://assets.example/result.mp4"},
		"duration":"5",
		"created_at":"1786478400",
		"updated_at":1786478460
	}`))

	require.NoError(t, err)
	require.Equal(t, model.TaskStatusSuccess, result.Status)
	require.Equal(t, "100%", result.Progress)
	require.Equal(t, "https://assets.example/result.mp4", result.Url)
}

func TestParseTaskResultTerminatesCancelledAndExpiredTasks(t *testing.T) {
	adaptor := &TaskAdaptor{}
	for _, status := range []string{"cancelled", "expired"} {
		t.Run(status, func(t *testing.T) {
			result, err := adaptor.ParseTaskResult([]byte(`{"status":"` + status + `","error":{}}`))
			require.NoError(t, err)
			require.Equal(t, model.TaskStatusFailure, result.Status)
			require.Equal(t, "Ark task ended with status "+status, result.Reason)
		})
	}
}

func TestParseTaskResultRejectsAmbiguousTerminalPayloads(t *testing.T) {
	adaptor := &TaskAdaptor{}

	_, err := adaptor.ParseTaskResult([]byte(`{"status":"succeeded","content":{}}`))
	require.ErrorContains(t, err, "does not contain video_url")

	_, err = adaptor.ParseTaskResult([]byte(`{"status":"paused"}`))
	require.ErrorContains(t, err, "unknown Ark task status")
}

func TestFetchTaskRejectsTaskIDPathInjection(t *testing.T) {
	adaptor := &TaskAdaptor{}

	_, err := adaptor.FetchTask("https://ark.cn-beijing.volces.com", "unused", map[string]any{
		"task_id": "../other-task?leak=true",
	}, "")

	require.ErrorContains(t, err, "invalid task_id")
}
