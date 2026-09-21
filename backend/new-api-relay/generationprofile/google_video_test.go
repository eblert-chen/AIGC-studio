package generationprofile

import (
	"testing"

	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestBuiltinGoogleVideoProfilesAreClosedDeterministicCeilings(t *testing.T) {
	profiles := BuiltinGoogleVideoProfiles()
	require.Len(t, profiles, 3)
	seen := make(map[string]struct{}, len(profiles))
	for _, profile := range profiles {
		_, duplicate := seen[profile.ID]
		assert.False(t, duplicate)
		seen[profile.ID] = struct{}{}
		assert.NoError(t, profile.ValidateImmutableContract())
		assert.Equal(t, profileRevision(profile), profile.Revision)
		mode, ok := profile.AcceptanceTestMode()
		assert.True(t, ok)
		assert.Equal(t, "text_to_video", mode)
		for modeName, capability := range profile.Capability.Modes {
			assert.Equal(t, []int{1}, capability.Limits.OutputCounts, modeName)
			artifact, ok := profile.Artifact(modeName)
			require.True(t, ok)
			assert.Equal(t, ArtifactContract{MediaType: "video", ContentType: "video/mp4", Count: 1}, artifact)
		}
	}
}

func TestGoogleOmniProfilePublishesOnlyStatelessBasicGeneration(t *testing.T) {
	profile, ok := Get(GoogleGeminiOmni11FlashBasicVideoV1)
	require.True(t, ok)
	assert.Equal(t, constant.ChannelTypeGemini, profile.NativeChannelType)
	assert.Equal(t, GoogleGeminiInteractionsVideoProtocolV1, profile.Protocol)
	assert.ElementsMatch(t, []string{"text_to_video", "image_to_video"}, mapKeys(profile.Capability.Modes))
	assert.NotContains(t, profile.Capability.Modes, "video_to_video", "edit/extension need explicit lineage semantics")
	text := profile.Capability.Modes["text_to_video"]
	assert.Equal(t, integerRange(3, 10), text.Limits.DurationSeconds)
	assert.ElementsMatch(t, []string{"360p", "720p", "1080p", "4k"}, text.Limits.Resolutions)
	image := profile.Capability.Modes["image_to_video"]
	assert.Equal(t, 2, image.Limits.MaxImages)
	assert.Equal(t, []string{"image"}, image.InputMediaTypes)

	request := dto.PlatformGenerationRequest{
		Model: "gemini-omni-1.1-flash", Mode: "image_to_video",
		Inputs: dto.PlatformGenerationInputs{
			Prompt: "use the first and last images as the shot boundary",
			Assets: []dto.PlatformGenerationAssetInput{
				{URL: "https://assets.example/first.png", MediaType: "image"},
				{URL: "https://assets.example/last.png", MediaType: "image"},
			},
		},
		Output: dto.PlatformGenerationOutputOptions{
			DurationSeconds: 5, AspectRatio: "16:9", Resolution: "720p", Count: 1,
		},
	}
	assert.NoError(t, profile.ValidateRequest(request))
	request.Inputs.Assets = append(request.Inputs.Assets, dto.PlatformGenerationAssetInput{
		URL: "https://assets.example/third.png", MediaType: "image",
	})
	assert.ErrorContains(t, profile.ValidateRequest(request), "media counts exceed")
}

func TestGoogleVeoProfilesUseValidEightSecondResolutionRectangle(t *testing.T) {
	for _, test := range []struct {
		id       string
		protocol string
		channel  int
	}{
		{GoogleGeminiVeo31VideoV1, GoogleGeminiVeoVideoProtocolV1, constant.ChannelTypeGemini},
		{GoogleVertexVeo31VideoV1, GoogleVertexVeoVideoProtocolV1, constant.ChannelTypeVertexAi},
	} {
		t.Run(test.id, func(t *testing.T) {
			profile, ok := Get(test.id)
			require.True(t, ok)
			assert.Equal(t, test.protocol, profile.Protocol)
			assert.Equal(t, test.channel, profile.NativeChannelType)
			assert.ElementsMatch(t, []string{"text_to_video", "image_to_video"}, mapKeys(profile.Capability.Modes))
			for _, mode := range profile.Capability.Modes {
				assert.Equal(t, []int{8}, mode.Limits.DurationSeconds)
				assert.Equal(t, []string{"720p", "1080p", "4k"}, mode.Limits.Resolutions)
				assert.Equal(t, googleVideoAspectRatios, mode.Limits.AspectRatios)
			}
		})
	}
}

func TestGoogleProfilesRejectCrossProtocolAndVideoInputExpansion(t *testing.T) {
	gemini, ok := Get(GoogleGeminiVeo31VideoV1)
	require.True(t, ok)
	vertex, ok := Get(GoogleVertexVeo31VideoV1)
	require.True(t, ok)

	mutated := cloneProfile(gemini)
	mutated.NativeChannelType = constant.ChannelTypeVertexAi
	assert.ErrorContains(t, mutated.ValidateImmutableContract(), "native channel")

	capability := cloneProfile(gemini).Capability
	capability.Modes["video_to_video"] = dto.PlatformModeCapability{
		InputMediaTypes: []string{"video"},
		Limits: dto.PlatformCapabilityLimits{
			MaxPromptLength: 512, MaxVideos: 1, DurationSeconds: []int{8},
			AspectRatios: googleVideoAspectRatios, Resolutions: []string{"720p"}, OutputCounts: []int{1},
		},
	}
	assert.ErrorContains(t, gemini.ValidateNarrowing(capability), "not implemented")

	_, _, err := Resolve(gemini.ID, constant.ChannelTypeGemini, "veo-3.1-generate-001")
	assert.ErrorContains(t, err, "no reviewed manifest")
	_, _, err = Resolve(vertex.ID, constant.ChannelTypeVertexAi, "veo-3.1-generate-preview")
	assert.ErrorContains(t, err, "no reviewed manifest")
}

func mapKeys[T any](values map[string]T) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	return keys
}
