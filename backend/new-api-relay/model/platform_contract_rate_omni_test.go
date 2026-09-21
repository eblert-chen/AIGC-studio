package model

import (
	"testing"

	"github.com/QuantumNous/new-api/dto"
	"github.com/stretchr/testify/require"
)

func TestFindPlatformProviderContractRateRejectsGeminiOmniBeforeDatabaseLookup(t *testing.T) {
	facts := PlatformProviderCostMaterializationFacts{
		Route: PlatformGenerationProviderRoute{
			UpstreamModel: dto.PlatformProviderUpstreamModelGeminiOmni11Flash,
		},
	}

	_, err := FindPlatformProviderContractRateTx(nil, facts, "720p")
	require.ErrorIs(t, err, ErrPlatformProviderContractRateUsageEvidenceRequired)
}
