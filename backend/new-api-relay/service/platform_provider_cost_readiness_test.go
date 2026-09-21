package service

import (
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func providerCostReadinessRate(
	route PlatformRelayRouteDeclaration,
	mode string,
	resolution string,
) dto.PlatformProviderContractRateInput {
	return dto.PlatformProviderContractRateInput{
		ID:                   uuid.NewString(),
		ProviderName:         route.ProviderName,
		ChannelID:            route.ChannelID,
		UpstreamModel:        route.UpstreamModel,
		Mode:                 mode,
		Resolution:           resolution,
		BillingUnit:          dto.PlatformContractRateUnitOutputItem,
		UnitAmountCents:      17,
		Currency:             "CNY",
		EffectiveFrom:        time.Date(2026, time.August, 1, 0, 0, 0, 0, time.UTC),
		SourceReference:      "provider-contract-cost-readiness",
		SourceDocumentSHA256: strings.Repeat("c", 64),
	}
}

func persistProviderCostReadinessRate(t *testing.T, input dto.PlatformProviderContractRateInput) {
	t.Helper()
	record := model.PlatformProviderContractRate{
		ID: input.ID, ProviderName: input.ProviderName, ChannelID: input.ChannelID,
		UpstreamModel: input.UpstreamModel, Mode: input.Mode, Resolution: input.Resolution,
		BillingUnit: input.BillingUnit, UnitAmountCents: input.UnitAmountCents,
		Currency: input.Currency, EffectiveFrom: input.EffectiveFrom.UTC(),
		SourceReference: input.SourceReference, SourceDocumentSHA256: input.SourceDocumentSHA256,
		CreatedAt: time.Now().UTC(),
	}
	require.NoError(t, model.DB.Create(&record).Error)
}

func providerCostReadinessRateSet(
	route PlatformRelayRouteDeclaration,
	mode string,
	resolution string,
	platformBillingUnit string,
) dto.PlatformProviderCostRateSet {
	return dto.PlatformProviderCostRateSet{
		SchemaVersion:              dto.PlatformProviderCostRateSetSchemaVersion,
		ID:                         uuid.NewString(),
		ProviderName:               route.ProviderName,
		ProviderOrigin:             "https://provider.example.com",
		PricingMarket:              "provider_contract",
		PlatformBillingUnit:        platformBillingUnit,
		ChannelID:                  route.ChannelID,
		UpstreamModel:              route.UpstreamModel,
		Mode:                       mode,
		Resolution:                 resolution,
		Currency:                   "CNY",
		EffectiveFrom:              time.Date(2026, time.August, 1, 0, 0, 0, 0, time.UTC),
		SourceReference:            "provider-contract-multi-component-readiness",
		SourceDocumentSHA256:       strings.Repeat("d", 64),
		FXCNYMicrosPerCurrencyUnit: 1_000_000,
		Components: []dto.PlatformProviderCostRateComponent{
			{Metric: dto.PlatformProviderCostMetricOutputItem, UnitAmountMicrosNumerator: 170_000, UnitAmountMicrosDenominator: 1},
		},
	}
}

func TestProviderCostReadinessRequiresCurrentConfiguredAndPersistedExactRectangles(t *testing.T) {
	route, _ := setupPlatformModelReleaseEvidenceTest(t)
	require.NoError(t, model.DB.AutoMigrate(&model.PlatformProviderContractRate{}))
	now := time.Date(2026, time.September, 1, 0, 0, 0, 0, time.UTC)
	mode := "text_to_image"
	resolution := route.Capabilities.Modes[mode].Limits.Resolutions[0]
	rate := providerCostReadinessRate(route, mode, resolution)

	missingSink, err := platformProviderCostReadinessForRoute(
		platformProviderCostReadinessSnapshot{}, route, now,
	)
	require.NoError(t, err)
	require.Len(t, missingSink.Rectangles, 1)
	assert.False(t, missingSink.Ready)
	assert.Equal(t, platformProviderCostBlockerSinkUnconfigured, missingSink.Rectangles[0].BlockerCode)

	missingRate, err := platformProviderCostReadinessForRoute(
		platformProviderCostReadinessSnapshot{CostConfigured: true}, route, now,
	)
	require.NoError(t, err)
	assert.False(t, missingRate.Ready)
	assert.Equal(t, platformProviderCostBlockerRateMissing, missingRate.Rectangles[0].BlockerCode)

	configuredButNotPersisted, err := platformProviderCostReadinessForRoute(
		platformProviderCostReadinessSnapshot{CostConfigured: true, ContractRates: []dto.PlatformProviderContractRateInput{rate}},
		route,
		now,
	)
	require.NoError(t, err)
	assert.False(t, configuredButNotPersisted.Ready)
	assert.Equal(t, platformProviderCostBlockerRateMissing, configuredButNotPersisted.Rectangles[0].BlockerCode)

	persistProviderCostReadinessRate(t, rate)
	ready, err := platformProviderCostReadinessForRoute(
		platformProviderCostReadinessSnapshot{CostConfigured: true, ContractRates: []dto.PlatformProviderContractRateInput{rate}},
		route,
		now,
	)
	require.NoError(t, err)
	assert.True(t, ready.Ready)
	assert.Equal(t, 1, ready.RectangleCount)
	assert.Equal(t, 1, ready.ReadyCount)
	require.Len(t, ready.Rectangles, 1)
	assert.True(t, ready.Rectangles[0].Ready)
	assert.Equal(t, rate.ID, ready.Rectangles[0].ContractRateID)
	assert.Equal(t, rate.UnitAmountCents, ready.Rectangles[0].UnitAmountCents)
	assert.Equal(t, rate.Currency, ready.Rectangles[0].Currency)
	assert.Equal(t, rate.SourceDocumentSHA256, ready.Rectangles[0].SourceDocumentSHA256)
}

func TestProviderCostReadinessFailsClosedForPartialCoverageAndOmni(t *testing.T) {
	route, _ := setupPlatformModelReleaseEvidenceTest(t)
	require.NoError(t, model.DB.AutoMigrate(&model.PlatformProviderContractRate{}))
	now := time.Date(2026, time.September, 1, 0, 0, 0, 0, time.UTC)
	mode := "text_to_image"
	capability := route.Capabilities.Modes[mode]
	capability.Limits.Resolutions = []string{"1024x1024", "2048x2048"}
	route.Capabilities.Modes[mode] = capability
	rate := providerCostReadinessRate(route, mode, "1024x1024")
	persistProviderCostReadinessRate(t, rate)

	partial, err := platformProviderCostReadinessForRoute(
		platformProviderCostReadinessSnapshot{CostConfigured: true, ContractRates: []dto.PlatformProviderContractRateInput{rate}},
		route,
		now,
	)
	require.NoError(t, err)
	assert.False(t, partial.Ready)
	assert.Equal(t, 2, partial.RectangleCount)
	assert.Equal(t, 1, partial.ReadyCount)
	assert.Equal(t, "1024x1024", partial.Rectangles[0].Resolution)
	assert.True(t, partial.Rectangles[0].Ready)
	assert.Equal(t, platformProviderCostBlockerRateMissing, partial.Rectangles[1].BlockerCode)

	omni := route
	omni.UpstreamModel = dto.PlatformProviderUpstreamModelGeminiOmni11Flash
	omniRateSets := make([]dto.PlatformProviderCostRateSet, 0, 2)
	for _, currentResolution := range omni.Capabilities.Modes[mode].Limits.Resolutions {
		omniRateSets = append(omniRateSets, providerCostReadinessRateSet(
			omni, mode, currentResolution, dto.PlatformProviderPlatformBillingUnitPerItem,
		))
	}
	omniReadiness, err := platformProviderCostReadinessForRoute(
		platformProviderCostReadinessSnapshot{CostConfigured: true, CostRateSets: omniRateSets},
		omni,
		now,
	)
	require.NoError(t, err)
	assert.False(t, omniReadiness.Ready)
	for _, rectangle := range omniReadiness.Rectangles {
		assert.Equal(t, platformProviderCostBlockerUsageUnqualified, rectangle.BlockerCode)
		assert.Empty(t, rectangle.ContractRateID)
		assert.NotEmpty(t, rectangle.RateSetID)
		assert.Equal(t, dto.PlatformProviderPlatformBillingUnitPerItem, rectangle.BillingUnit)
		assert.Zero(t, rectangle.UnitAmountCents)
		assert.Equal(t, "CNY", rectangle.Currency)
		assert.Equal(t, strings.Repeat("d", 64), rectangle.SourceDocumentSHA256)
	}
}

func TestProviderCostReadinessProjectsRateSetAndCustomerBillingUnit(t *testing.T) {
	route, _ := setupPlatformModelReleaseEvidenceTest(t)
	route.ProviderName = "minimax"
	route.UpstreamModel = "MiniMax-H3"
	capability := route.Capabilities.Modes["text_to_image"]
	capability.Limits.Resolutions = []string{"768p"}
	route.Capabilities.Modes = map[string]dto.PlatformModeCapability{"video_to_video": capability}
	rateSet := providerCostReadinessRateSet(
		route, "video_to_video", "768p", dto.PlatformProviderPlatformBillingUnitPerSecond,
	)
	rateSet.Components = []dto.PlatformProviderCostRateComponent{
		{Metric: dto.PlatformProviderCostMetricInputSecond, UnitAmountMicrosNumerator: 500_000, UnitAmountMicrosDenominator: 1},
		{Metric: dto.PlatformProviderCostMetricOutputSecond, UnitAmountMicrosNumerator: 500_000, UnitAmountMicrosDenominator: 1},
	}
	readiness, err := platformProviderCostReadinessForRoute(
		platformProviderCostReadinessSnapshot{CostConfigured: true, CostRateSets: []dto.PlatformProviderCostRateSet{rateSet}},
		route,
		time.Date(2026, time.September, 1, 0, 0, 0, 0, time.UTC),
	)
	require.NoError(t, err)
	require.True(t, readiness.Ready)
	require.Len(t, readiness.Rectangles, 1)
	rectangle := readiness.Rectangles[0]
	require.True(t, rectangle.Ready)
	require.Equal(t, rateSet.ID, rectangle.RateSetID)
	require.Equal(t, dto.PlatformProviderPlatformBillingUnitPerSecond, rectangle.BillingUnit)
	require.Equal(t, rateSet.SourceDocumentSHA256, rectangle.SourceDocumentSHA256)
	require.Empty(t, rectangle.ContractRateID)
	require.Zero(t, rectangle.UnitAmountCents)
}

func TestProviderCostReadinessRejectsPersistedRateIdentityDrift(t *testing.T) {
	route, _ := setupPlatformModelReleaseEvidenceTest(t)
	require.NoError(t, model.DB.AutoMigrate(&model.PlatformProviderContractRate{}))
	mode := "text_to_image"
	resolution := route.Capabilities.Modes[mode].Limits.Resolutions[0]
	rate := providerCostReadinessRate(route, mode, resolution)
	persistProviderCostReadinessRate(t, rate)
	require.NoError(t, model.DB.Exec(
		"UPDATE platform_provider_contract_rates SET unit_amount_cents = ? WHERE id = ?",
		rate.UnitAmountCents+1,
		rate.ID,
	).Error)

	_, err := platformProviderCostReadinessForRoute(
		platformProviderCostReadinessSnapshot{CostConfigured: true, ContractRates: []dto.PlatformProviderContractRateInput{rate}},
		route,
		time.Date(2026, time.September, 1, 0, 0, 0, 0, time.UTC),
	)
	assert.ErrorContains(t, err, "does not match its runtime evidence")
}

func TestModelReleaseProjectionCarriesExactProviderCostCoverage(t *testing.T) {
	route, publicModelID := setupPlatformModelReleaseEvidenceTest(t)
	require.NoError(t, model.DB.AutoMigrate(&model.PlatformProviderContractRate{}))
	setPlatformProviderRuntimeTestEnvironment(t)
	t.Setenv("RELAY_PLATFORM_CHANNEL_COST_URL", "https://platform.internal/internal/channel-costs")
	t.Setenv("RELAY_PLATFORM_INTERNAL_SERVICE_TOKEN", "platform-cost-readiness-token")
	t.Setenv("RELAY_PLATFORM_CHANNEL_COST_SIGNING_SECRET", "platform-cost-readiness-signing-secret")
	mode := "text_to_image"
	resolution := route.Capabilities.Modes[mode].Limits.Resolutions[0]
	rate := providerCostReadinessRate(route, mode, resolution)
	raw, err := common.Marshal([]dto.PlatformProviderContractRateInput{rate})
	require.NoError(t, err)
	t.Setenv("RELAY_PROVIDER_CONTRACT_RATES_JSON", string(raw))
	persistProviderCostReadinessRate(t, rate)

	projection, err := GetPlatformModelReleaseEvidenceProjection()
	require.NoError(t, err)
	evidence := requirePlatformModelReleaseEvidence(t, projection, publicModelID)
	assert.True(t, evidence.ProviderCostReady)
	assert.Equal(t, 1, evidence.ProviderCostRectangleCount)
	assert.Equal(t, 1, evidence.ProviderCostReadyRectangleCount)
	assert.Regexp(t, `^sha256:[0-9a-f]{64}$`, evidence.ProviderCostReadinessSHA256)
	require.Len(t, evidence.Routes, 1)
	require.Len(t, evidence.Routes[0].ProviderCostRectangles, 1)
	rectangle := evidence.Routes[0].ProviderCostRectangles[0]
	assert.True(t, rectangle.Ready)
	assert.Equal(t, mode, rectangle.Mode)
	assert.Equal(t, resolution, rectangle.Resolution)
	assert.Equal(t, rate.ID, rectangle.ContractRateID)
	assert.Equal(t, rate.UnitAmountCents, rectangle.UnitAmountCents)
	assert.Equal(t, rate.Currency, rectangle.Currency)
	assert.Empty(t, rectangle.BlockerCode)
}
