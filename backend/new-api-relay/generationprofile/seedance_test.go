package generationprofile

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestBuiltinSeedanceProfilesAreValidDeterministicImplementationCeilings(t *testing.T) {
	profiles := BuiltinSeedanceProfiles()
	require.Len(t, profiles, 4)
	for _, profile := range profiles {
		require.NoError(t, ValidateCapabilityShape(profile.Capability, profile.DurationSemantics))
		assert.Equal(t, profileRevision(profile), profile.Revision)
		for modeName, mode := range profile.Capability.Modes {
			artifact, ok := profile.Artifact(modeName)
			require.True(t, ok)
			assert.Equal(t, "video", artifact.MediaType)
			assert.Equal(t, 1, artifact.Count)
			assert.Equal(t, []int{1}, mode.Limits.OutputCounts)
		}
	}
}

func TestNewSeedanceCeilingsDoNotRewriteExistingSnapshots(t *testing.T) {
	for profileID, revision := range map[string]string{
		VolcengineArkVideoGenerationV1:       "sha256:b7f9d0a460518ac77fd151957be368dffdad59f111a3b482635f3b6b99500d7e",
		VolcengineArkLegacyVideoGenerationV1: "sha256:20b79921ea19ecaa2a5027652e21deb07e720e153a670c4f1fd10300fa420a74",
	} {
		profile, ok := Get(profileID)
		require.True(t, ok)
		assert.Equal(t, revision, profile.Revision, "an existing executable snapshot must not be widened in place")
	}
	fourK, ok := Get(VolcengineArkVideoGeneration4KV1)
	require.True(t, ok)
	assert.Equal(t, []string{"480p", "720p", "1080p", "4k"}, fourK.Capability.Modes["text_to_video"].Limits.Resolutions)
	reference, ok := Get(VolcengineArkVideoGeneration30SReferenceV1)
	require.True(t, ok)
	assert.Equal(t, integerRange(4, 30), reference.Capability.Modes["video_to_video"].Limits.DurationSeconds)
	assert.NotContains(t, reference.Capability.Modes["video_to_video"].Limits.Resolutions, "4k")
}

func TestSeedanceProfilesSeparateLegacyAndMultimodalLimits(t *testing.T) {
	profiles := BuiltinSeedanceProfiles()
	byID := make(map[string]Profile, len(profiles))
	for _, profile := range profiles {
		byID[profile.ID] = profile
	}

	multimodal := byID[VolcengineArkVideoGenerationV1]
	assert.Equal(t, 9, multimodal.Capability.Modes["video_to_video"].Limits.MaxImages)
	assert.Equal(t, 3, multimodal.Capability.Modes["video_to_video"].Limits.MaxVideos)
	assert.Equal(t, 3, multimodal.Capability.Modes["video_to_video"].Limits.MaxAudio)
	assert.Equal(t, []int{4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}, multimodal.Capability.Modes["text_to_video"].Limits.DurationSeconds)

	legacy := byID[VolcengineArkLegacyVideoGenerationV1]
	assert.NotContains(t, legacy.Capability.Modes, "video_to_video")
	assert.Equal(t, 2, legacy.Capability.Modes["image_to_video"].Limits.MaxImages)
	assert.Equal(t, []int{2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}, legacy.Capability.Modes["text_to_video"].Limits.DurationSeconds)
}
