package dto

import (
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func providerCostRateSetFixture() PlatformProviderCostRateSet {
	return PlatformProviderCostRateSet{
		SchemaVersion:              PlatformProviderCostRateSetSchemaVersion,
		ID:                         "fb88fce3-1246-40ca-87b8-fe68622079d6",
		ProviderName:               "minimax",
		ProviderOrigin:             "https://api.minimax.cn",
		PricingMarket:              "cn",
		PlatformBillingUnit:        PlatformProviderPlatformBillingUnitPerSecond,
		ChannelID:                  930002,
		UpstreamModel:              "MiniMax-H3",
		Mode:                       "video_to_video",
		Resolution:                 "768p",
		Currency:                   "CNY",
		EffectiveFrom:              time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC),
		SourceReference:            "https://platform.minimaxi.com/docs/guides/pricing-paygo",
		SourceDocumentSHA256:       "d6c35125c9752bb126516612689fc8595ce8a4fb32968ad9c42ed2bbb72fd528",
		FXCNYMicrosPerCurrencyUnit: 1_000_000,
		Components: []PlatformProviderCostRateComponent{
			{
				Metric:                      PlatformProviderCostMetricInputImageAboveFree,
				UnitAmountMicrosNumerator:   200_000,
				UnitAmountMicrosDenominator: 1,
				FreeUnits:                   5,
			},
			{
				Metric:                      PlatformProviderCostMetricInputSecond,
				UnitAmountMicrosNumerator:   500_000,
				UnitAmountMicrosDenominator: 1,
			},
			{
				Metric:                      PlatformProviderCostMetricOutputSecond,
				UnitAmountMicrosNumerator:   500_000,
				UnitAmountMicrosDenominator: 1,
			},
		},
	}
}

func TestCalculatePlatformProviderCostCentsMultiComponentCNY(t *testing.T) {
	rateSet := providerCostRateSetFixture()
	amount, err := CalculatePlatformProviderCostCents(rateSet, PlatformProviderCostUsage{
		OutputSeconds:   5,
		InputSeconds:    5,
		InputImageCount: 9,
	})
	require.NoError(t, err)
	// 5*0.50 output + 5*0.50 input + (9-5)*0.20 images = CNY 5.80.
	require.EqualValues(t, 580, amount)
}

func TestCalculatePlatformProviderCostCentsOmniRationalUSD(t *testing.T) {
	rateSet := providerCostRateSetFixture()
	rateSet.ID = "14fde8ec-c23f-40fb-9942-23b3337422e6"
	rateSet.ProviderName = "google-gemini-api"
	rateSet.ProviderOrigin = "https://generativelanguage.googleapis.com"
	rateSet.PricingMarket = "global"
	rateSet.ChannelID = 930003
	rateSet.UpstreamModel = "gemini-omni-1.1-flash"
	rateSet.Mode = "text_to_video"
	rateSet.Resolution = "720p"
	rateSet.Currency = "USD"
	rateSet.FXCNYMicrosPerCurrencyUnit = 7_000_000
	rateSet.FXSourceReference = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
	rateSet.FXSourceDocumentSHA256 = "ad9c42ed2bbb72fd528d6c35125c9752bb126516612689fc8595ce8a4fb32968"
	rateSet.FXObservedAt = time.Date(2026, 8, 31, 14, 0, 0, 0, time.UTC)
	rateSet.Components = []PlatformProviderCostRateComponent{
		{
			Metric:                      PlatformProviderCostMetricInputToken,
			UnitAmountMicrosNumerator:   3,
			UnitAmountMicrosDenominator: 2,
		},
		{
			Metric:                      PlatformProviderCostMetricOutputToken,
			UnitAmountMicrosNumerator:   35,
			UnitAmountMicrosDenominator: 2,
		},
	}
	amount, err := CalculatePlatformProviderCostCents(rateSet, PlatformProviderCostUsage{
		InputTokens:  1_000,
		OutputTokens: 5_792,
		TotalTokens:  6_792,
	})
	require.NoError(t, err)
	// USD 0.10286 * 7 = CNY 0.72002, rounded once to CNY 0.73.
	require.EqualValues(t, 73, amount)
	calculation, err := CalculatePlatformProviderCost(rateSet, PlatformProviderCostUsage{
		InputTokens: 1_000, OutputTokens: 5_792, TotalTokens: 6_792,
	})
	require.NoError(t, err)
	require.Equal(t, "102860", calculation.SourceAmountMicrosNumerator)
	require.Equal(t, "1", calculation.SourceAmountMicrosDenominator)
	require.Equal(t, "720020", calculation.CNYAmountMicrosNumerator)
	require.Equal(t, "1", calculation.CNYAmountMicrosDenominator)
	require.Equal(t, "9980", calculation.RoundingAdjustmentCNYMicrosNumerator)
}

func TestPlatformProviderCostRateSetRejectsUnprovenOrAmbiguousEvidence(t *testing.T) {
	valid := providerCostRateSetFixture()
	tests := []struct {
		name   string
		mutate func(*PlatformProviderCostRateSet)
	}{
		{
			name: "duplicate metric",
			mutate: func(value *PlatformProviderCostRateSet) {
				value.Components = append(value.Components, value.Components[0])
			},
		},
		{
			name: "CNY fake FX evidence",
			mutate: func(value *PlatformProviderCostRateSet) {
				value.FXSourceReference = "https://example.invalid/fx"
			},
		},
		{
			name: "USD missing FX evidence",
			mutate: func(value *PlatformProviderCostRateSet) {
				value.Currency = "USD"
			},
		},
		{
			name: "free tokens",
			mutate: func(value *PlatformProviderCostRateSet) {
				value.Components[2].FreeUnits = 1
			},
		},
		{
			name: "missing customer billing unit",
			mutate: func(value *PlatformProviderCostRateSet) {
				value.PlatformBillingUnit = ""
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			candidate := valid
			candidate.Components = append([]PlatformProviderCostRateComponent(nil), valid.Components...)
			test.mutate(&candidate)
			require.Error(t, candidate.Validate())
		})
	}
}

func TestPlatformProviderCostRateSetAcceptsRecentBusinessDayFXAndRejectsFutureOrExpiredEvidence(t *testing.T) {
	valid := providerCostRateSetFixture()
	valid.ProviderName = "google-gemini-api"
	valid.ProviderOrigin = "https://generativelanguage.googleapis.com"
	valid.PricingMarket = "global"
	valid.Currency = "USD"
	valid.FXCNYMicrosPerCurrencyUnit = 7_000_000
	valid.FXSourceReference = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
	valid.FXSourceDocumentSHA256 = "ad9c42ed2bbb72fd528d6c35125c9752bb126516612689fc8595ce8a4fb32968"
	// A Friday ECB observation remains valid for a Monday activation.
	valid.EffectiveFrom = time.Date(2026, 9, 7, 0, 0, 0, 0, time.UTC)
	valid.FXObservedAt = time.Date(2026, 9, 4, 14, 0, 0, 0, time.UTC)
	require.NoError(t, valid.Validate())

	future := valid
	future.FXObservedAt = valid.EffectiveFrom.Add(time.Second)
	require.Error(t, future.Validate())
	expired := valid
	expired.FXObservedAt = valid.EffectiveFrom.Add(-7*24*time.Hour - time.Second)
	require.Error(t, expired.Validate())
}

func TestCalculatePlatformProviderCostCentsRejectsMissingActualQuantity(t *testing.T) {
	_, err := CalculatePlatformProviderCostCents(providerCostRateSetFixture(), PlatformProviderCostUsage{})
	require.ErrorContains(t, err, "proven billable quantity")
}

func TestCalculatePlatformProviderCostUsesCachedAndUncachedInputExactlyOnce(t *testing.T) {
	rateSet := providerCostRateSetFixture()
	rateSet.ID = "9017cf8f-8eaa-4afd-a47a-e2164b3753f8"
	rateSet.ProviderName = "google-gemini-api"
	rateSet.ProviderOrigin = "https://generativelanguage.googleapis.com"
	rateSet.PricingMarket = "global"
	rateSet.ChannelID = 930003
	rateSet.UpstreamModel = "gemini-omni-1.1-flash"
	rateSet.Mode = "text_to_video"
	rateSet.Resolution = "720p"
	rateSet.Currency = "USD"
	rateSet.FXCNYMicrosPerCurrencyUnit = 7_000_000
	rateSet.FXSourceReference = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
	rateSet.FXSourceDocumentSHA256 = "ad9c42ed2bbb72fd528d6c35125c9752bb126516612689fc8595ce8a4fb32968"
	rateSet.FXObservedAt = time.Date(2026, 8, 31, 14, 0, 0, 0, time.UTC)
	rateSet.Components = []PlatformProviderCostRateComponent{
		{Metric: PlatformProviderCostMetricCachedInputToken, UnitAmountMicrosNumerator: 3, UnitAmountMicrosDenominator: 20},
		{Metric: PlatformProviderCostMetricOutputTextToken, UnitAmountMicrosNumerator: 9, UnitAmountMicrosDenominator: 1},
		{Metric: PlatformProviderCostMetricOutputVideoToken, UnitAmountMicrosNumerator: 35, UnitAmountMicrosDenominator: 2},
		{Metric: PlatformProviderCostMetricThoughtToken, UnitAmountMicrosNumerator: 9, UnitAmountMicrosDenominator: 1},
		{Metric: PlatformProviderCostMetricUncachedInputToken, UnitAmountMicrosNumerator: 3, UnitAmountMicrosDenominator: 2},
	}
	usage := PlatformProviderCostUsage{
		InputTokens: 1_000, CachedInputTokens: 200,
		OutputTokens: 5_800, OutputTextTokens: 8, OutputVideoTokens: 5_792,
		ThoughtTokens: 4, TotalTokens: 6_804,
	}
	calculation, err := CalculatePlatformProviderCost(rateSet, usage)
	require.NoError(t, err)
	// 800*1.5 + 200*0.15 + 8*9 + 5792*17.5 + 4*9 = 102698 USD micros.
	require.Equal(t, "102698", calculation.SourceAmountMicrosNumerator)
	require.Equal(t, "1", calculation.SourceAmountMicrosDenominator)
	require.EqualValues(t, 72, calculation.AmountCents)
}

func TestPlatformProviderCostUsageRejectsOverlappingOrImpossibleTokenEvidence(t *testing.T) {
	for _, usage := range []PlatformProviderCostUsage{
		{InputTokens: 10, CachedInputTokens: 11},
		{OutputTokens: 10, OutputTextTokens: 4, OutputVideoTokens: 7},
		{InputTokens: 5, OutputTokens: 5, ThoughtTokens: 1, TotalTokens: 10},
	} {
		require.Error(t, usage.Validate())
	}
}
