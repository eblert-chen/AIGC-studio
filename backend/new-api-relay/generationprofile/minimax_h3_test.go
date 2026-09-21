package generationprofile

import (
	"fmt"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestMiniMaxH3ProfilesExposeOnlyFixedRatioSingleMP4Contracts(t *testing.T) {
	require.Len(t, BuiltinMiniMaxH3Profiles(), 2)
	for _, test := range []struct {
		profileID, providerID string
		minimum               int
		resolutions           []string
		modes                 []string
	}{
		{MiniMaxH3ReferenceVideoGenerationV1, "MiniMax-H3", 4, []string{"768p", "2k"}, []string{"text_to_video", "image_to_video", "video_to_video"}},
		{MiniMaxH3MaxTextToVideoGenerationV1, "MiniMax-H3-Max", 5, []string{"480p", "768p"}, []string{"text_to_video"}},
	} {
		t.Run(test.providerID, func(t *testing.T) {
			profile, ok, err := Resolve(test.profileID, constant.ChannelTypeMiniMax, test.providerID)
			require.NoError(t, err)
			require.True(t, ok)
			require.NoError(t, profile.ValidateImmutableContract())
			assert.Equal(t, MiniMaxH3VideoProtocolV2, profile.Protocol)
			assert.Equal(t, profileRevision(profile), profile.Revision)
			assert.Nil(t, profile.Image)
			assert.Len(t, profile.Capability.Modes, len(test.modes))
			acceptanceMode, supported := profile.AcceptanceTestMode()
			assert.True(t, supported)
			assert.Equal(t, "text_to_video", acceptanceMode)
			for _, modeName := range test.modes {
				mode, ok := profile.Capability.Modes[modeName]
				require.True(t, ok)
				assert.Equal(t, 7000, mode.Limits.MaxPromptLength)
				assert.Equal(t, integerRange(test.minimum, 15), mode.Limits.DurationSeconds)
				assert.Equal(t, test.resolutions, mode.Limits.Resolutions)
				assert.Equal(t, []int{1}, mode.Limits.OutputCounts)
				assert.ElementsMatch(t, []string{"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}, mode.Limits.AspectRatios)
				assert.LessOrEqual(t, mode.Limits.MaxImages+mode.Limits.MaxVideos+mode.Limits.MaxAudio, 12)
				assert.False(t, mode.SupportsFace)
				assert.Empty(t, mode.RequiredResourceKeys)
				assert.True(t, profile.DurationApplies(modeName))
				artifact, ok := profile.Artifact(modeName)
				require.True(t, ok)
				assert.Equal(t, ArtifactContract{MediaType: "video", ContentType: "video/mp4", Count: 1}, artifact)
			}
		})
	}
}

func TestMiniMaxH3ProfilesValidateMediaAndPromptBoundariesWithoutDroppingInputs(t *testing.T) {
	profile, ok := Get(MiniMaxH3ReferenceVideoGenerationV1)
	require.True(t, ok)
	for _, test := range []struct {
		mode                  string
		images, videos, audio int
	}{
		{"text_to_video", 0, 0, 0},
		{"image_to_video", 9, 0, 3},
		{"video_to_video", 6, 3, 3},
	} {
		t.Run(test.mode, func(t *testing.T) {
			request := miniMaxH3TestRequest(test.mode, test.images, test.videos, test.audio)
			request.Inputs.Prompt = strings.Repeat("镜", 7000)
			assert.NoError(t, profile.ValidateRequest(request), "the prompt limit counts characters, not UTF-8 bytes")
			assert.Len(t, request.Inputs.Assets, test.images+test.videos+test.audio)
			request.Inputs.Prompt += "头"
			assert.ErrorContains(t, profile.ValidateRequest(request), "prompt")
		})
	}
	for _, test := range []struct {
		name, mode            string
		images, videos, audio int
	}{
		{"text cannot hide references", "text_to_video", 1, 0, 0},
		{"text cannot be audio-only reference", "text_to_video", 0, 0, 1},
		{"image requires image", "image_to_video", 0, 0, 1},
		{"image cannot accept video", "image_to_video", 1, 1, 0},
		{"image thirteen files", "image_to_video", 10, 0, 3},
		{"video requires video", "video_to_video", 1, 0, 1},
		{"video thirteen files", "video_to_video", 7, 3, 3},
		{"video fourth clip", "video_to_video", 1, 4, 0},
		{"video fourth audio", "video_to_video", 1, 1, 4},
	} {
		t.Run(test.name, func(t *testing.T) {
			request := miniMaxH3TestRequest(test.mode, test.images, test.videos, test.audio)
			assert.Error(t, profile.ValidateRequest(request))
			assert.Len(t, request.Inputs.Assets, test.images+test.videos+test.audio, "invalid media must not be silently trimmed")
		})
	}
}

func TestMiniMaxH3RequestCannotExpandThroughMetadataOrMaxProfile(t *testing.T) {
	for _, test := range []struct {
		name, profileID string
		mutate          func(*dto.PlatformGenerationRequest)
	}{
		{"adaptive ratio", MiniMaxH3ReferenceVideoGenerationV1, func(r *dto.PlatformGenerationRequest) { r.Output.AspectRatio = "adaptive" }},
		{"unreviewed ratio", MiniMaxH3ReferenceVideoGenerationV1, func(r *dto.PlatformGenerationRequest) { r.Output.AspectRatio = "3:2" }},
		{"count two", MiniMaxH3ReferenceVideoGenerationV1, func(r *dto.PlatformGenerationRequest) { r.Output.Count = 2 }},
		{"duration sixteen", MiniMaxH3ReferenceVideoGenerationV1, func(r *dto.PlatformGenerationRequest) { r.Output.DurationSeconds = 16 }},
		{"duration three", MiniMaxH3ReferenceVideoGenerationV1, func(r *dto.PlatformGenerationRequest) { r.Output.DurationSeconds = 3 }},
		{"H3 480p", MiniMaxH3ReferenceVideoGenerationV1, func(r *dto.PlatformGenerationRequest) { r.Output.Resolution = "480p" }},
		{"face", MiniMaxH3ReferenceVideoGenerationV1, func(r *dto.PlatformGenerationRequest) { r.Output.FaceEnabled = true }},
		{"max four seconds", MiniMaxH3MaxTextToVideoGenerationV1, func(r *dto.PlatformGenerationRequest) { r.Output.DurationSeconds = 4 }},
		{"max 2k", MiniMaxH3MaxTextToVideoGenerationV1, func(r *dto.PlatformGenerationRequest) { r.Output.Resolution = "2k" }},
		{"max reference", MiniMaxH3MaxTextToVideoGenerationV1, func(r *dto.PlatformGenerationRequest) { *r = miniMaxH3TestRequest("image_to_video", 1, 0, 0) }},
		{"unknown mode", MiniMaxH3ReferenceVideoGenerationV1, func(r *dto.PlatformGenerationRequest) { r.Mode = "audio_to_video" }},
	} {
		t.Run(test.name, func(t *testing.T) {
			profile, ok := Get(test.profileID)
			require.True(t, ok)
			request := miniMaxH3TestRequest("text_to_video", 0, 0, 0)
			test.mutate(&request)
			request.Metadata = map[string]any{
				"model": "MiniMax-H3", "mode": "text_to_video", "duration": 5,
				"resolution": "768p", "ratio": "16:9", "output_count": 1,
				"minimax_h3":      map[string]any{"allow_unsupported": true},
				MetadataProfileID: MiniMaxH3ReferenceVideoGenerationV1,
			}
			assert.Error(t, profile.ValidateRequest(request), "metadata is never a provider-parameter override")
		})
	}
}

func TestMiniMaxH3ImmutableSnapshotsRejectUnsupportedSemanticsWithoutPanicking(t *testing.T) {
	for _, test := range []struct {
		name   string
		mutate func(*Profile)
	}{
		{"foreign channel", func(p *Profile) { p.NativeChannelType = constant.ChannelTypeVolcEngine }},
		{"unknown profile", func(p *Profile) { p.ID = "minimax.unreviewed.video.v1" }},
		{"image options", func(p *Profile) { p.Image = &ImageProviderContract{} }},
		{"non-second duration", func(p *Profile) { p.DurationSemantics["text_to_video"] = DurationSemanticsNone }},
		{"audio artifact", func(p *Profile) {
			p.Artifacts["text_to_video"] = ArtifactContract{MediaType: "audio", ContentType: "audio/wav", Count: 1}
		}},
		{"expanded prompt", func(p *Profile) {
			mode := p.Capability.Modes["text_to_video"]
			mode.Limits.MaxPromptLength = 7001
			p.Capability.Modes["text_to_video"] = mode
		}},
		{"expanded mixed count", func(p *Profile) {
			mode := p.Capability.Modes["video_to_video"]
			mode.Limits.MaxImages = 9
			p.Capability.Modes["video_to_video"] = mode
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			profile, ok := Get(MiniMaxH3ReferenceVideoGenerationV1)
			require.True(t, ok)
			metadata := SnapshotMetadata(nil, profile)
			test.mutate(&profile)
			assert.Error(t, profile.ValidateImmutableContract())
			raw, err := common.Marshal(profile)
			require.NoError(t, err)
			metadata[MetadataProfileSnapshot] = string(raw)
			require.NotPanics(t, func() {
				_, _, err = ResolveSnapshot(metadata)
			})
			assert.Error(t, err)
		})
	}

	profile, ok := Get(MiniMaxH3MaxTextToVideoGenerationV1)
	require.True(t, ok)
	metadata := SnapshotMetadata(map[string]any{
		"client_metadata":       map[string]any{"model": "MiniMax-H3"},
		MetadataProfileID:       MiniMaxH3ReferenceVideoGenerationV1,
		MetadataProfileRevision: "sha256:" + strings.Repeat("0", 64),
		MetadataProfileSnapshot: `{"id":"minimax.video-generation.h3-reference-v1"}`,
	}, profile)
	resolved, found, err := ResolveSnapshot(metadata)
	require.NoError(t, err)
	require.True(t, found)
	assert.Equal(t, profile.ID, resolved.ID)
	assert.Equal(t, profile.Revision, resolved.Revision)
	assert.NotContains(t, resolved.Capability.Modes, "image_to_video")
}

func miniMaxH3TestRequest(mode string, images, videos, audio int) dto.PlatformGenerationRequest {
	request := dto.PlatformGenerationRequest{
		Model: "minimax-h3", Mode: mode,
		Inputs: dto.PlatformGenerationInputs{Prompt: "海风吹过灯塔，镜头缓慢推进。", Assets: []dto.PlatformGenerationAssetInput{}},
		Output: dto.PlatformGenerationOutputOptions{DurationSeconds: 5, AspectRatio: "16:9", Resolution: "768p", Count: 1},
	}
	for _, media := range []struct {
		typeName string
		count    int
	}{{"image", images}, {"video", videos}, {"audio", audio}} {
		for index := 0; index < media.count; index++ {
			request.Inputs.Assets = append(request.Inputs.Assets, dto.PlatformGenerationAssetInput{
				MediaType: media.typeName, URL: fmt.Sprintf("https://assets.example.test/%s-%d", media.typeName, index),
			})
		}
	}
	return request
}
