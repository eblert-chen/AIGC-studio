package service

import (
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	"github.com/stretchr/testify/require"
)

func TestBindGoogleOmniArtifactExpectationUsesVerifiedReceiptMetadata(t *testing.T) {
	request := PlatformArtifactTransferRequest{}
	receipt := model.PlatformGenerationProviderResultReceipt{
		Protocol:          generationprofile.GoogleGeminiInteractionsVideoProtocolV1,
		ArtifactSizeBytes: 4096,
		ArtifactSHA256:    strings.Repeat("a", 64),
	}

	require.NoError(t, bindPlatformGenerationProviderArtifactExpectation(
		&request,
		generationprofile.GoogleGeminiInteractionsVideoProtocolV1,
		&receipt,
	))
	require.NotNil(t, request.ExpectedSizeBytes)
	require.Equal(t, int64(4096), *request.ExpectedSizeBytes)
	require.Equal(t, receipt.ArtifactSHA256, request.ExpectedSHA256)
}

func TestBindGoogleOmniArtifactExpectationFailsClosedOnMissingEvidence(t *testing.T) {
	for _, receipt := range []*model.PlatformGenerationProviderResultReceipt{
		nil,
		{Protocol: generationprofile.GoogleGeminiInteractionsVideoProtocolV1},
		{
			Protocol:          generationprofile.GoogleGeminiInteractionsVideoProtocolV1,
			ArtifactSizeBytes: 4096,
			ArtifactSHA256:    strings.Repeat("A", 64),
		},
	} {
		request := PlatformArtifactTransferRequest{}
		err := bindPlatformGenerationProviderArtifactExpectation(
			&request,
			generationprofile.GoogleGeminiInteractionsVideoProtocolV1,
			receipt,
		)
		require.ErrorIs(t, err, model.ErrPlatformGenerationProviderMaterialReconciliationRequired)
		require.Nil(t, request.ExpectedSizeBytes)
		require.Empty(t, request.ExpectedSHA256)
	}
}

func TestBindGoogleOmniArtifactExpectationDoesNotChangeVeoCompatibility(t *testing.T) {
	request := PlatformArtifactTransferRequest{}
	require.NoError(t, bindPlatformGenerationProviderArtifactExpectation(
		&request,
		generationprofile.GoogleGeminiVeoVideoProtocolV1,
		nil,
	))
	require.Nil(t, request.ExpectedSizeBytes)
	require.Empty(t, request.ExpectedSHA256)
}
