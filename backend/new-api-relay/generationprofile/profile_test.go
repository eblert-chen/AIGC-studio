package generationprofile

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/stretchr/testify/require"
)

func TestGenericArkImageProfileContainsNoModelIdentity(t *testing.T) {
	profile, ok := Get(VolcengineArkImageGenerationV1)
	require.True(t, ok)
	require.Equal(t, "volcengine.ark.image-generation.v1", profile.ID)
	require.Equal(t, "volcengine-ark-images-generations", profile.Protocol)
	require.Equal(t, constant.ChannelTypeVolcEngine, profile.NativeChannelType)
	require.True(t, strings.HasPrefix(profile.Revision, "sha256:"))
	require.Len(t, profile.Revision, len("sha256:")+64)
	serialized, err := json.Marshal(profile)
	require.NoError(t, err)
	require.NotContains(t, string(serialized), "public_model")
	require.NotContains(t, string(serialized), "provider_model")
	require.NotContains(t, string(serialized), "upstream_model")

	first, found, err := Resolve(profile.ID, constant.ChannelTypeVolcEngine, "doubao-seedream-5-0-260128")
	require.NoError(t, err)
	require.True(t, found)
	second, found, err := Resolve(profile.ID, constant.ChannelTypeVolcEngine, "future-provider-model-id")
	require.NoError(t, err)
	require.True(t, found)
	require.Equal(t, first.ID, second.ID, "explicit reusable profiles must not require a provider-model code registration")
}

func TestAcceptanceTestModeIsDerivedFromImmutableProtocolAndArtifact(t *testing.T) {
	for _, test := range []struct {
		profileID string
		mode      string
		mediaType string
		mimeType  string
	}{
		{profileID: VolcengineArkImageGenerationV1, mode: "text_to_image", mediaType: "image", mimeType: "image/png"},
		{profileID: VolcengineArkVideoGenerationV1, mode: "text_to_video", mediaType: "video", mimeType: "video/mp4"},
	} {
		profile, ok := Get(test.profileID)
		require.True(t, ok)
		mode, ok := profile.AcceptanceTestMode()
		require.True(t, ok)
		require.Equal(t, test.mode, mode)
		artifact, ok := profile.Artifact(mode)
		require.True(t, ok)
		require.Equal(t, test.mediaType, artifact.MediaType)
		require.Equal(t, test.mimeType, artifact.ContentType)
	}
}

func TestLegacySeedreamProfileInferenceRemainsExact(t *testing.T) {
	profile, ok := Get(Seedream50TextToImageV1)
	require.True(t, ok)

	resolved, found, err := Resolve("", constant.ChannelTypeVolcEngine, "doubao-seedream-5-0-260128")
	require.NoError(t, err)
	require.True(t, found)
	require.Equal(t, profile.ID, resolved.ID)

	_, found, err = Resolve("", constant.ChannelTypeVolcEngine, "doubao-seedream-5-0-lite-260128")
	require.NoError(t, err)
	require.False(t, found, "a deprecated public alias must never become an upstream-model alias")

	_, _, err = Resolve(profile.ID, constant.ChannelTypeVolcEngine, "doubao-seedream-5-0-lite-260128")
	require.ErrorContains(t, err, "legacy generation capability profile")
}

func TestProfilesAreImmutableCopiesAndSnapshotsFailClosed(t *testing.T) {
	first, ok := Get(Seedream50TextToImageV1)
	require.True(t, ok)
	mode := first.Capability.Modes["text_to_image"]
	mode.Limits.Resolutions[0] = "tampered"
	first.Capability.Modes["text_to_image"] = mode
	first.Artifacts["text_to_image"] = ArtifactContract{Count: 99}

	second, ok := Get(Seedream50TextToImageV1)
	require.True(t, ok)
	require.Equal(t, []string{"2048x2048"}, second.Capability.Modes["text_to_image"].Limits.Resolutions)
	require.Equal(t, 1, second.Artifacts["text_to_image"].Count)

	metadata := SnapshotMetadata(map[string]any{
		"customer_trace":        "trace-1",
		MetadataProfileID:       "attacker-profile",
		MetadataProfileRevision: "sha256:" + strings.Repeat("0", 64),
		MetadataProfileSnapshot: `{"id":"attacker-profile"}`,
	}, second)
	require.Equal(t, second.ID, metadata[MetadataProfileID])
	require.Equal(t, second.Revision, metadata[MetadataProfileRevision])
	require.NotEmpty(t, metadata[MetadataProfileSnapshot])

	resolved, found, err := ResolveSnapshot(metadata)
	require.NoError(t, err)
	require.True(t, found)
	require.Equal(t, second.ID, resolved.ID)

	metadata[MetadataProfileRevision] = "sha256:" + strings.Repeat("0", 64)
	_, _, err = ResolveSnapshot(metadata)
	require.ErrorContains(t, err, "unavailable")

	public := PublicMetadata(metadata)
	require.Equal(t, map[string]any{"customer_trace": "trace-1"}, public)
}

func TestProfileSnapshotSurvivesRollingRegistryRevisionAndRejectsTampering(t *testing.T) {
	original, ok := Get(VolcengineArkImageGenerationV1)
	require.True(t, ok)
	metadata := SnapshotMetadata(map[string]any{"customer_trace": "trace-rolling"}, original)

	registered := profiles[original.ID]
	t.Cleanup(func() { profiles[original.ID] = registered })
	replacement := cloneProfile(registered)
	mode := replacement.Capability.Modes["text_to_image"]
	mode.Limits.MaxPromptLength--
	replacement.Capability.Modes["text_to_image"] = mode
	replacement.Revision = profileRevision(replacement)
	require.NotEqual(t, original.Revision, replacement.Revision)
	profiles[original.ID] = replacement

	resolved, found, err := ResolveSnapshot(metadata)
	require.NoError(t, err)
	require.True(t, found)
	require.Equal(t, original.Revision, resolved.Revision)
	require.Equal(t, original.Capability, resolved.Capability)

	tampered := make(map[string]any, len(metadata))
	for key, value := range metadata {
		tampered[key] = value
	}
	tampered[MetadataProfileSnapshot] = strings.Replace(
		tampered[MetadataProfileSnapshot].(string),
		`"max_prompt_length":1000`,
		`"max_prompt_length":999`,
		1,
	)
	_, _, err = ResolveSnapshot(tampered)
	require.ErrorContains(t, err, "unavailable")
}

func TestSeedreamProfileAllowsOnlyNarrowerRouteCapabilities(t *testing.T) {
	profile, ok := Get(Seedream50TextToImageV1)
	require.True(t, ok)

	narrower := profile.Capability
	mode := narrower.Modes["text_to_image"]
	mode.Limits.MaxPromptLength = 600
	mode.RequiredResourceKeys = []string{"feature.premium_image"}
	mode.Limits.DurationSeconds = nil
	narrower.Modes["text_to_image"] = mode
	require.NoError(t, profile.ValidateNarrowing(narrower))

	tests := map[string]func(*dto.PlatformModeCapability){
		"input media": func(mode *dto.PlatformModeCapability) {
			mode.InputMediaTypes = []string{"image"}
			mode.Limits.MaxImages = 1
		},
		"face":       func(mode *dto.PlatformModeCapability) { mode.SupportsFace = true },
		"prompt":     func(mode *dto.PlatformModeCapability) { mode.Limits.MaxPromptLength = 1001 },
		"resolution": func(mode *dto.PlatformModeCapability) { mode.Limits.Resolutions = []string{"1024x1024", "2048x2048"} },
		"outputs":    func(mode *dto.PlatformModeCapability) { mode.Limits.OutputCounts = []int{1, 2} },
	}
	for name, mutate := range tests {
		t.Run(name, func(t *testing.T) {
			candidate, ok := Get(Seedream50TextToImageV1)
			require.True(t, ok)
			mode := candidate.Capability.Modes["text_to_image"]
			mutate(&mode)
			candidate.Capability.Modes["text_to_image"] = mode
			require.Error(t, profile.ValidateNarrowing(candidate.Capability))
		})
	}
}

func TestSeedreamProfileValidatesArtifactAndIgnoresImageDurationSentinel(t *testing.T) {
	profile, ok := Get(Seedream50TextToImageV1)
	require.True(t, ok)
	require.False(t, profile.DurationApplies("text_to_image"))

	artifact, ok := profile.Artifact("text_to_image")
	require.True(t, ok)
	require.Equal(t, ArtifactContract{
		MediaType:   "image",
		ContentType: "image/png",
		Width:       2048,
		Height:      2048,
		Count:       1,
	}, artifact)

	request := dto.PlatformGenerationRequest{
		Model:  "seedream-5",
		Mode:   "text_to_image",
		Inputs: dto.PlatformGenerationInputs{Prompt: "clean product photo", Assets: []dto.PlatformGenerationAssetInput{}},
		Output: dto.PlatformGenerationOutputOptions{
			DurationSeconds: 999,
			AspectRatio:     "1:1",
			Resolution:      "2048x2048",
			Count:           1,
		},
	}
	require.NoError(t, profile.ValidateRequest(request), "duration is not a physical image-model capability")

	request.Output.Resolution = "1024x1024"
	require.Error(t, profile.ValidateRequest(request))
	request.Output.Resolution = "2048x2048"
	request.Inputs.Assets = []dto.PlatformGenerationAssetInput{{URL: "https://assets.example/input.png", MediaType: "image"}}
	require.Error(t, profile.ValidateRequest(request))
}
