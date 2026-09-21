package model

import (
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/stretchr/testify/require"
)

func validGoogleOmniProviderResultEvidenceForTest() PlatformGenerationProviderResultObservation {
	return PlatformGenerationProviderResultObservation{
		Protocol:          generationprofile.GoogleGeminiInteractionsVideoProtocolV1,
		Succeeded:         true,
		UsageComplete:     true,
		InputTokens:       120,
		OutputTokens:      28_960,
		CachedTokens:      20,
		TotalTokens:       29_080,
		ArtifactSizeBytes: 4096,
		ArtifactSHA256:    strings.Repeat("a", 64),
	}
}

func TestGoogleOmniProviderResultEvidenceRequiresTokenAndFilesFacts(t *testing.T) {
	require.NoError(t, validatePlatformGenerationProviderResultEvidence(
		validGoogleOmniProviderResultEvidenceForTest(),
	))

	tests := []struct {
		name   string
		mutate func(*PlatformGenerationProviderResultObservation)
	}{
		{"input tokens", func(value *PlatformGenerationProviderResultObservation) { value.InputTokens = 0 }},
		{"output tokens", func(value *PlatformGenerationProviderResultObservation) { value.OutputTokens = 0 }},
		{"negative cached tokens", func(value *PlatformGenerationProviderResultObservation) { value.CachedTokens = -1 }},
		{"cached exceeds input", func(value *PlatformGenerationProviderResultObservation) { value.CachedTokens = value.InputTokens + 1 }},
		{"total omits output", func(value *PlatformGenerationProviderResultObservation) {
			value.TotalTokens = value.InputTokens + value.OutputTokens - 1
		}},
		{"artifact size", func(value *PlatformGenerationProviderResultObservation) { value.ArtifactSizeBytes = 0 }},
		{"artifact digest length", func(value *PlatformGenerationProviderResultObservation) {
			value.ArtifactSHA256 = strings.Repeat("a", 62)
		}},
		{"artifact digest uppercase", func(value *PlatformGenerationProviderResultObservation) {
			value.ArtifactSHA256 = "AB" + strings.Repeat("a", 62)
		}},
		{"artifact digest alphabet", func(value *PlatformGenerationProviderResultObservation) {
			value.ArtifactSHA256 = strings.Repeat("z", 64)
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			observation := validGoogleOmniProviderResultEvidenceForTest()
			test.mutate(&observation)
			require.Error(t, validatePlatformGenerationProviderResultEvidence(observation))
		})
	}
}

func TestGoogleOmniProviderResultEvidenceAllowsExplicitlyIncompleteUsageWithoutFabrication(t *testing.T) {
	observation := validGoogleOmniProviderResultEvidenceForTest()
	observation.InputTokens = 0
	observation.OutputTokens = 0
	observation.CachedTokens = 0
	observation.TotalTokens = 0
	observation.UsageComplete = false
	require.NoError(t, validatePlatformGenerationProviderResultEvidence(observation))

	observation.UsageComplete = true
	require.Error(t, validatePlatformGenerationProviderResultEvidence(observation))

	observation.UsageComplete = false
	observation.OutputTokens = 1
	require.Error(t, validatePlatformGenerationProviderResultEvidence(observation))
}

func TestProviderResultEvidenceKeepsExistingProtocolsZeroCompatible(t *testing.T) {
	legacy := PlatformGenerationProviderResultObservation{
		Protocol:  generationprofile.GoogleGeminiVeoVideoProtocolV1,
		Succeeded: true,
	}
	require.NoError(t, validatePlatformGenerationProviderResultEvidence(legacy))

	legacy.InputTokens = 1
	require.Error(t, validatePlatformGenerationProviderResultEvidence(legacy))

	omniFailure := validGoogleOmniProviderResultEvidenceForTest()
	omniFailure.Succeeded = false
	require.Error(t, validatePlatformGenerationProviderResultEvidence(omniFailure))
}

func TestProviderResultReceiptHashCoversOmniEvidenceAndLegacyJSONReplays(t *testing.T) {
	legacy := PlatformGenerationProviderResultReceipt{
		SchemaVersion:         PlatformGenerationProviderResultReceiptSchemaVersion,
		ProofContractRevision: PlatformGenerationGoogleGeminiVeoProviderResultProofContractRevision,
		State:                 PlatformGenerationProviderResultReceiptStateVerified,
	}
	legacyDigest, err := platformGenerationProviderResultReceiptProofSHA256(legacy)
	require.NoError(t, err)
	legacy.ProofSHA256 = legacyDigest
	legacyJSON, err := common.Marshal(legacy)
	require.NoError(t, err)
	require.NotContains(t, string(legacyJSON), "input_tokens")
	require.NotContains(t, string(legacyJSON), "artifact_size_bytes")
	decoded, err := DecodePlatformGenerationProviderResultReceipt(legacyJSON)
	require.NoError(t, err)
	require.Zero(t, decoded.InputTokens)
	require.Zero(t, decoded.ArtifactSizeBytes)

	omni := PlatformGenerationProviderResultReceipt{
		InputTokens:       120,
		OutputTokens:      28_960,
		CachedTokens:      20,
		TotalTokens:       29_080,
		ArtifactSizeBytes: 4096,
		ArtifactSHA256:    strings.Repeat("a", 64),
	}
	baseline, err := platformGenerationProviderResultReceiptProofSHA256(omni)
	require.NoError(t, err)
	mutations := []func(*PlatformGenerationProviderResultReceipt){
		func(value *PlatformGenerationProviderResultReceipt) { value.InputTokens++ },
		func(value *PlatformGenerationProviderResultReceipt) { value.OutputTokens++ },
		func(value *PlatformGenerationProviderResultReceipt) { value.CachedTokens++ },
		func(value *PlatformGenerationProviderResultReceipt) { value.TotalTokens++ },
		func(value *PlatformGenerationProviderResultReceipt) { value.ArtifactSizeBytes++ },
		func(value *PlatformGenerationProviderResultReceipt) { value.ArtifactSHA256 = strings.Repeat("b", 64) },
	}
	for _, mutate := range mutations {
		changed := omni
		mutate(&changed)
		digest, err := platformGenerationProviderResultReceiptProofSHA256(changed)
		require.NoError(t, err)
		require.NotEqual(t, baseline, digest)
	}
}
