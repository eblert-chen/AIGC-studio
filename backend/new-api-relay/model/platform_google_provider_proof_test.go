package model

import (
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestGoogleProviderProofContractsAreProtocolSpecific(t *testing.T) {
	veoRevision, veoSupported := PlatformGenerationProviderResultProofRevision(
		generationprofile.GoogleGeminiVeoVideoProtocolV1,
	)
	omniRevision, omniSupported := PlatformGenerationProviderResultProofRevision(
		generationprofile.GoogleGeminiInteractionsVideoProtocolV1,
	)
	_, vertexSupported := PlatformGenerationProviderResultProofRevision(
		generationprofile.GoogleVertexVeoVideoProtocolV1,
	)

	assert.True(t, veoSupported)
	assert.True(t, omniSupported)
	assert.False(t, vertexSupported, "Vertex inline artifacts are not a reviewed Platform result-proof path")
	assert.Equal(t, PlatformGenerationGoogleGeminiVeoProviderResultProofContractRevision, veoRevision)
	assert.Equal(t, PlatformGenerationGoogleGeminiOmniProviderResultProofContractRevision, omniRevision)
	assert.NotEqual(t, veoRevision, omniRevision)
	assert.NotEqual(t, PlatformGenerationProviderResultProofContractRevision, veoRevision)
	assert.NotEqual(t, PlatformGenerationMiniMaxH3ProviderResultProofContractRevision, omniRevision)
}

func TestGoogleProviderProofConflictReceiptsRemainFailClosed(t *testing.T) {
	for _, protocol := range []string{
		generationprofile.GoogleGeminiVeoVideoProtocolV1,
		generationprofile.GoogleGeminiInteractionsVideoProtocolV1,
	} {
		t.Run(protocol, func(t *testing.T) {
			receipt, err := NewPlatformGenerationProviderProofConflictReceiptForProtocol(
				protocol,
				[]byte(`{"provider":"terminal-but-unproven"}`),
			)
			require.NoError(t, err)
			serialized, err := common.Marshal(receipt)
			require.NoError(t, err)
			assert.True(t, PlatformGenerationProviderProofConflictRequired(serialized))
		})
	}
}
