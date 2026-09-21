package dto

import (
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func validPlatformProviderContractRateInputForTest() PlatformProviderContractRateInput {
	return PlatformProviderContractRateInput{
		ID:                   uuid.NewString(),
		ProviderName:         "google",
		ChannelID:            24,
		UpstreamModel:        "veo-3.1-generate-preview",
		Mode:                 "text_to_video",
		Resolution:           "720p",
		BillingUnit:          PlatformContractRateUnitOutputSecond,
		UnitAmountCents:      10,
		Currency:             "CNY",
		EffectiveFrom:        time.Date(2026, time.September, 1, 0, 0, 0, 0, time.UTC),
		SourceReference:      "google-contract-2026-09",
		SourceDocumentSHA256: strings.Repeat("a", 64),
	}
}

func TestPlatformProviderContractRateInputRejectsGeminiOmniLegacyUnits(t *testing.T) {
	for _, upstreamModel := range []string{
		PlatformProviderUpstreamModelGeminiOmni11Flash,
		PlatformProviderUpstreamModelGeminiOmniPreview,
	} {
		for _, billingUnit := range []string{
			PlatformContractRateUnitOutputItem,
			PlatformContractRateUnitOutputSecond,
		} {
			t.Run(upstreamModel+"/"+billingUnit, func(t *testing.T) {
				input := validPlatformProviderContractRateInputForTest()
				input.UpstreamModel = upstreamModel
				input.BillingUnit = billingUnit

				err := input.Validate()
				require.ErrorContains(t, err, "requires immutable input and video output token usage evidence")
			})
		}
	}
}

func TestPlatformProviderContractRateInputKeepsVeoOutputSecond(t *testing.T) {
	input := validPlatformProviderContractRateInputForTest()

	require.NoError(t, input.Validate())
	require.False(t, PlatformContractRateRequiresTokenUsageEvidence(input.UpstreamModel))
}
