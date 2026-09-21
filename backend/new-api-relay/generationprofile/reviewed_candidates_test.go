package generationprofile

import (
	"sort"
	"testing"

	"github.com/QuantumNous/new-api/constant"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestReviewedAcceptanceCandidatesAreExactCompleteAndDeterministic(t *testing.T) {
	candidates, err := ReviewedAcceptanceCandidates()
	require.NoError(t, err)
	require.Len(t, candidates, 11)

	expectedIDs := []string{
		"gemini-omni-1.1-flash",
		"minimax-h3",
		"minimax-h3-max",
		"seedance-1.0-pro",
		"seedance-1.0-pro-fast",
		"seedance-2.0",
		"seedance-2.0-fast",
		"seedance-2.0-mini",
		"seedance-2.5",
		"veo-3.1",
		"veo-3.1-fast",
	}
	actualIDs := make([]string, 0, len(candidates))
	for _, candidate := range candidates {
		actualIDs = append(actualIDs, candidate.PublicModelID)
		require.NotEmpty(t, candidate.ProviderModelID)
		require.NotEmpty(t, candidate.DisplayName)
		require.NotEmpty(t, candidate.AdapterProfileID)
		require.NotZero(t, candidate.Capability.SchemaVersion)
		require.NotEmpty(t, candidate.Capability.Modes)
		profile, ok := Get(candidate.AdapterProfileID)
		require.True(t, ok)
		require.Equal(t, profile.NativeChannelType, candidate.NativeChannelType)
		require.NoError(t, profile.ValidateNarrowing(candidate.Capability))
	}
	assert.Equal(t, expectedIDs, actualIDs)
	assert.True(t, sort.StringsAreSorted(actualIDs))

	byID := make(map[string]ReviewedAcceptanceCandidate, len(candidates))
	for _, candidate := range candidates {
		byID[candidate.PublicModelID] = candidate
	}
	assert.Equal(t, constant.ChannelTypeVolcEngine, byID["seedance-2.0"].NativeChannelType)
	assert.Equal(t, constant.ChannelTypeMiniMax, byID["minimax-h3"].NativeChannelType)
	assert.Equal(t, constant.ChannelTypeGemini, byID["veo-3.1"].NativeChannelType)
	assert.NotContains(t, byID, "seedance-1.5-pro")
	assert.NotContains(t, byID, "seedance-2.0-lite")
}

func TestReviewedAcceptanceCandidateExportCannotMutateCompiledCatalog(t *testing.T) {
	first, err := ReviewedAcceptanceCandidates()
	require.NoError(t, err)
	require.NotEmpty(t, first)
	delete(first[0].Capability.Modes, "text_to_video")

	second, err := ReviewedAcceptanceCandidates()
	require.NoError(t, err)
	_, exists := second[0].Capability.Modes["text_to_video"]
	require.True(t, exists)
}
