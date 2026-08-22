package ali

import (
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/stretchr/testify/require"
)

func testRelayInfo() *relaycommon.RelayInfo {
	return &relaycommon.RelayInfo{
		ChannelMeta: &relaycommon.ChannelMeta{},
	}
}

func TestConvertToAliRequestWan27I2VBuildsMediaFromImage(t *testing.T) {
	adaptor := &TaskAdaptor{}
	req := relaycommon.TaskSubmitReq{
		Model:    "wan2.7-i2v",
		Prompt:   "animate the first frame",
		Image:    "https://example.com/first.png",
		Size:     "720p",
		Duration: 10,
	}

	aliReq, err := adaptor.convertToAliRequest(testRelayInfo(), req)

	require.NoError(t, err)
	require.Equal(t, "wan2.7-i2v", aliReq.Model)
	require.Equal(t, "720P", aliReq.Parameters.Resolution)
	require.Equal(t, 10, aliReq.Parameters.Duration)
	require.Equal(t, []AliVideoMedia{
		{Type: "first_frame", URL: "https://example.com/first.png"},
	}, aliReq.Input.Media)
	require.Empty(t, aliReq.Input.ImgURL)

	body, err := common.Marshal(aliReq)
	require.NoError(t, err)
	require.Contains(t, string(body), `"media"`)
	require.NotContains(t, string(body), `"img_url"`)
}

func TestConvertToAliRequestWan27I2VBuildsFirstAndLastFrameFromImages(t *testing.T) {
	adaptor := &TaskAdaptor{}
	req := relaycommon.TaskSubmitReq{
		Model:  "wan2.7-i2v",
		Prompt: "interpolate between frames",
		Images: []string{
			"https://example.com/first.png",
			"https://example.com/last.png",
		},
	}

	aliReq, err := adaptor.convertToAliRequest(testRelayInfo(), req)

	require.NoError(t, err)
	require.Equal(t, []AliVideoMedia{
		{Type: "first_frame", URL: "https://example.com/first.png"},
		{Type: "last_frame", URL: "https://example.com/last.png"},
	}, aliReq.Input.Media)
}

func TestConvertToAliRequestWan27I2VPrefersImageBeforeImagesAndInputReference(t *testing.T) {
	adaptor := &TaskAdaptor{}
	req := relaycommon.TaskSubmitReq{
		Model:          "wan2.7-i2v",
		Prompt:         "use the direct image",
		Image:          " https://example.com/direct.png ",
		Images:         []string{"https://example.com/images-first.png", " https://example.com/images-last.png "},
		InputReference: "https://example.com/input-reference.png",
	}

	aliReq, err := adaptor.convertToAliRequest(testRelayInfo(), req)

	require.NoError(t, err)
	require.Equal(t, []AliVideoMedia{
		{Type: "first_frame", URL: "https://example.com/direct.png"},
		{Type: "last_frame", URL: "https://example.com/images-last.png"},
	}, aliReq.Input.Media)
}

func TestConvertToAliRequestWan27I2VFallsBackToFirstNonEmptyImage(t *testing.T) {
	adaptor := &TaskAdaptor{}
	req := relaycommon.TaskSubmitReq{
		Model:  "wan2.7-i2v",
		Prompt: "skip blank images",
		Image:  " ",
		Images: []string{
			" ",
			" https://example.com/first.png ",
			" https://example.com/last.png ",
		},
		InputReference: "https://example.com/input-reference.png",
	}

	aliReq, err := adaptor.convertToAliRequest(testRelayInfo(), req)

	require.NoError(t, err)
	require.Equal(t, []AliVideoMedia{
		{Type: "first_frame", URL: "https://example.com/first.png"},
		{Type: "last_frame", URL: "https://example.com/last.png"},
	}, aliReq.Input.Media)
}

func TestConvertToAliRequestWan27I2VKeepsExplicitMetadataMedia(t *testing.T) {
	adaptor := &TaskAdaptor{}
	req := relaycommon.TaskSubmitReq{
		Model:          "wan2.7-i2v",
		Prompt:         "continue the clip",
		Image:          "https://example.com/direct.png",
		Images:         []string{"https://example.com/images-first.png", "https://example.com/images-last.png"},
		InputReference: "https://example.com/input-reference.png",
		Metadata: map[string]interface{}{
			"input": map[string]interface{}{
				"media": []interface{}{
					map[string]interface{}{
						"type": "first_clip",
						"url":  "https://example.com/input.mp4",
					},
				},
			},
		},
	}

	aliReq, err := adaptor.convertToAliRequest(testRelayInfo(), req)

	require.NoError(t, err)
	require.Equal(t, []AliVideoMedia{
		{Type: "first_clip", URL: "https://example.com/input.mp4"},
	}, aliReq.Input.Media)
	require.Empty(t, aliReq.Input.ImgURL)

	body, err := common.Marshal(aliReq)
	require.NoError(t, err)
	require.Contains(t, string(body), `"media"`)
	require.NotContains(t, string(body), `"img_url"`)
}

func TestConvertToAliRequestWan27I2VRequiresMedia(t *testing.T) {
	adaptor := &TaskAdaptor{}
	req := relaycommon.TaskSubmitReq{
		Model:  "wan2.7-i2v",
		Prompt: "animate without a frame",
	}

	_, err := adaptor.convertToAliRequest(testRelayInfo(), req)

	require.Error(t, err)
	require.True(t, strings.Contains(err.Error(), "requires image"))
}

func TestConvertToAliRequestWan25I2VKeepsLegacyImgURL(t *testing.T) {
	adaptor := &TaskAdaptor{}
	req := relaycommon.TaskSubmitReq{
		Model:  "wan2.5-i2v-preview",
		Prompt: "animate the first frame",
		Image:  "https://example.com/first.png",
	}

	aliReq, err := adaptor.convertToAliRequest(testRelayInfo(), req)

	require.NoError(t, err)
	require.Equal(t, "https://example.com/first.png", aliReq.Input.ImgURL)
	require.Empty(t, aliReq.Input.Media)

	body, err := common.Marshal(aliReq)
	require.NoError(t, err)
	require.Contains(t, string(body), `"img_url"`)
	require.NotContains(t, string(body), `"media"`)
}

func TestConvertToAliRequestPinnedRouteUsesVersionedUpstreamModelAndIgnoresMetadataOverrides(t *testing.T) {
	adaptor := &TaskAdaptor{}
	info := &relaycommon.RelayInfo{
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: "wan2.7-t2v-2026-06-12",
		},
		TaskRelayInfo: &relaycommon.TaskRelayInfo{PinnedProviderRoute: true},
	}
	req := relaycommon.TaskSubmitReq{
		Model:    "customer-video-model",
		Prompt:   "a product rotating on a clean table",
		Size:     "1280x720",
		Duration: 5,
		Metadata: map[string]interface{}{
			"model": "attacker-controlled-model",
			"input": map[string]interface{}{"prompt": "overridden"},
			"parameters": map[string]interface{}{
				"duration": 999,
				"ratio":    "99:1",
			},
		},
	}

	aliReq, err := adaptor.convertToAliRequest(info, req)

	require.NoError(t, err)
	require.Equal(t, "wan2.7-t2v-2026-06-12", aliReq.Model)
	require.Equal(t, "a product rotating on a clean table", aliReq.Input.Prompt)
	require.Equal(t, "720P", aliReq.Parameters.Resolution)
	require.Equal(t, "16:9", aliReq.Parameters.Ratio)
	require.Equal(t, 5, aliReq.Parameters.Duration)
	require.Empty(t, aliReq.Parameters.Size)
}

func TestConvertToAliRequestWan27T2VUsesResolutionAndRatioProtocol(t *testing.T) {
	adaptor := &TaskAdaptor{}
	req := relaycommon.TaskSubmitReq{
		Model:    "wan2.7-t2v-2026-06-12",
		Prompt:   "cinematic sunrise",
		Size:     "1648x1248",
		Duration: 15,
	}

	aliReq, err := adaptor.convertToAliRequest(testRelayInfo(), req)

	require.NoError(t, err)
	require.Equal(t, "1080P", aliReq.Parameters.Resolution)
	require.Equal(t, "4:3", aliReq.Parameters.Ratio)
	require.Empty(t, aliReq.Parameters.Size)
	body, err := common.Marshal(aliReq)
	require.NoError(t, err)
	require.Contains(t, string(body), `"resolution":"1080P"`)
	require.Contains(t, string(body), `"ratio":"4:3"`)
	require.NotContains(t, string(body), `"size"`)
}

func TestConvertToAliRequestWan27I2VDoesNotSendSizeOrRatio(t *testing.T) {
	adaptor := &TaskAdaptor{}
	req := relaycommon.TaskSubmitReq{
		Model:    "wan2.7-i2v-2026-04-25",
		Prompt:   "subtle camera motion",
		Image:    "https://example.com/first.png",
		Images:   []string{"https://example.com/first.png", "https://example.com/last.png"},
		Size:     "1920x1080",
		Duration: 10,
	}

	aliReq, err := adaptor.convertToAliRequest(testRelayInfo(), req)

	require.NoError(t, err)
	require.Equal(t, "1080P", aliReq.Parameters.Resolution)
	require.Empty(t, aliReq.Parameters.Ratio)
	require.Empty(t, aliReq.Parameters.Size)
	require.Equal(t, []AliVideoMedia{
		{Type: "first_frame", URL: "https://example.com/first.png"},
		{Type: "last_frame", URL: "https://example.com/last.png"},
	}, aliReq.Input.Media)
}

func TestConvertToAliRequestWan27ContinuationPreservesVideoType(t *testing.T) {
	adaptor := &TaskAdaptor{}
	info := &relaycommon.RelayInfo{
		ChannelMeta:   &relaycommon.ChannelMeta{UpstreamModelName: "wan2.7-i2v-2026-04-25"},
		TaskRelayInfo: &relaycommon.TaskRelayInfo{PinnedProviderRoute: true},
	}
	req := relaycommon.TaskSubmitReq{
		Model:          "wan2.7-i2v-2026-04-25",
		Prompt:         "continue the camera movement",
		Images:         []string{"https://example.com/last.png"},
		InputReference: "https://example.com/input.mp4",
		Size:           "720p",
		Duration:       12,
		Metadata:       map[string]interface{}{"platform_generation_mode": "video_to_video"},
	}

	aliReq, err := adaptor.convertToAliRequest(info, req)

	require.NoError(t, err)
	require.Equal(t, []AliVideoMedia{
		{Type: "first_clip", URL: "https://example.com/input.mp4"},
		{Type: "last_frame", URL: "https://example.com/last.png"},
	}, aliReq.Input.Media)
}

func TestConvertToAliRequestWan27RejectsProtocolDrift(t *testing.T) {
	adaptor := &TaskAdaptor{}

	_, err := adaptor.convertToAliRequest(testRelayInfo(), relaycommon.TaskSubmitReq{
		Model:    "wan2.7-t2v-2026-06-12",
		Prompt:   "too long",
		Size:     "1280x720",
		Duration: 16,
	})
	require.ErrorContains(t, err, "between 2 and 15")

	_, err = adaptor.convertToAliRequest(testRelayInfo(), relaycommon.TaskSubmitReq{
		Model:    "wan2.7-i2v-2026-04-25",
		Prompt:   "invalid media",
		Size:     "720p",
		Duration: 5,
		Metadata: map[string]interface{}{
			"input": map[string]interface{}{
				"media": []interface{}{
					map[string]interface{}{"type": "first_clip", "url": "https://example.com/input.mp4"},
					map[string]interface{}{"type": "driving_audio", "url": "https://example.com/audio.mp3"},
				},
			},
		},
	})
	require.ErrorContains(t, err, "unsupported material combination")
}
