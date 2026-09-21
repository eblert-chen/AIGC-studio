package service

import (
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func platformProviderH3RateSetFixture() dto.PlatformProviderCostRateSet {
	return dto.PlatformProviderCostRateSet{
		SchemaVersion:              dto.PlatformProviderCostRateSetSchemaVersion,
		ID:                         "fb88fce3-1246-40ca-87b8-fe68622079d6",
		ProviderName:               "minimax",
		ProviderOrigin:             "https://api.minimax.cn",
		PricingMarket:              "cn",
		PlatformBillingUnit:        dto.PlatformProviderPlatformBillingUnitPerSecond,
		ChannelID:                  930002,
		UpstreamModel:              "MiniMax-H3",
		Mode:                       "video_to_video",
		Resolution:                 "768p",
		Currency:                   "CNY",
		EffectiveFrom:              time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC),
		SourceReference:            "https://platform.minimaxi.com/docs/guides/pricing-paygo",
		SourceDocumentSHA256:       "d6c35125c9752bb126516612689fc8595ce8a4fb32968ad9c42ed2bbb72fd528",
		FXCNYMicrosPerCurrencyUnit: 1_000_000,
		Components: []dto.PlatformProviderCostRateComponent{
			{Metric: dto.PlatformProviderCostMetricInputImageAboveFree, UnitAmountMicrosNumerator: 200_000, UnitAmountMicrosDenominator: 1, FreeUnits: 5},
			{Metric: dto.PlatformProviderCostMetricInputSecond, UnitAmountMicrosNumerator: 500_000, UnitAmountMicrosDenominator: 1},
			{Metric: dto.PlatformProviderCostMetricOutputSecond, UnitAmountMicrosNumerator: 500_000, UnitAmountMicrosDenominator: 1},
		},
	}
}

func TestProviderCostAllocationPersistsCompleteH3MultiComponentEvidence(t *testing.T) {
	preparePlatformProviderMonitorCostServiceTest(t)
	require.NoError(t, model.MigratePlatformProviderCostEvidenceStorageV8WithDB(model.DB))
	receipt := model.PlatformGenerationProviderResultReceipt{
		ProofContractRevision: model.PlatformGenerationMiniMaxH3ProviderResultProofContractRevision,
		ProofSHA256:           "sha256:" + strings.Repeat("b", 64),
		Protocol:              generationprofile.MiniMaxH3VideoProtocolV2,
		Mode:                  "video_to_video",
		OutputSeconds:         5,
		InputSeconds:          5,
		InputImageCount:       9,
	}
	usage := platformProviderCostUsageFromReceipt(receipt)
	rateSet := platformProviderH3RateSetFixture()
	require.NoError(t, validatePlatformProviderCostCoverage(rateSet, receipt, usage))
	calculation, err := dto.CalculatePlatformProviderCost(rateSet, usage)
	require.NoError(t, err)
	require.EqualValues(t, 580, calculation.AmountCents)

	facts := model.PlatformProviderCostMaterializationFacts{
		Job:     model.PlatformGenerationJob{ID: uuid.NewString()},
		Outcome: model.PlatformProviderTerminalOutcome{ID: uuid.NewString()},
	}
	evidence, err := newPlatformProviderCostAllocationEvidence(
		uuid.NewString(), facts, rateSet,
		model.PlatformGenerationProviderCostReceiptEvidence{
			Receipt: receipt, ReceiptSHA256: "sha256:" + strings.Repeat("a", 64),
		},
		usage, calculation,
	)
	require.NoError(t, err)
	created, err := model.CreatePlatformProviderCostAllocationEvidenceTx(model.DB, &evidence)
	require.NoError(t, err)
	require.True(t, created)
	require.Equal(t, rateSet.ID, evidence.RateSetID)
	require.Equal(t, receipt.ProofSHA256, evidence.ReceiptProofSHA256)
	require.EqualValues(t, 580, evidence.AmountCents)

	created, err = model.CreatePlatformProviderCostAllocationEvidenceTx(model.DB, &evidence)
	require.NoError(t, err)
	require.False(t, created)
}

func TestProviderCostCoverageFailsClosedOnIncompleteH3OrLegacyProof(t *testing.T) {
	rateSet := platformProviderH3RateSetFixture()
	receipt := model.PlatformGenerationProviderResultReceipt{
		ProofContractRevision: model.PlatformGenerationMiniMaxH3ProviderResultProofContractRevision,
		Protocol:              generationprofile.MiniMaxH3VideoProtocolV2,
		Mode:                  "video_to_video",
		OutputSeconds:         5,
		InputSeconds:          5,
		InputImageCount:       9,
	}
	usage := platformProviderCostUsageFromReceipt(receipt)

	missingImageRate := rateSet
	missingImageRate.Components = missingImageRate.Components[1:]
	require.ErrorContains(t,
		validatePlatformProviderCostCoverage(missingImageRate, receipt, usage),
		"image pricing is incomplete",
	)
	missingInputUsage := usage
	missingInputUsage.InputSeconds = 0
	require.ErrorContains(t,
		validatePlatformProviderCostCoverage(rateSet, receipt, missingInputUsage),
		"input-second usage is missing",
	)
	legacy := receipt
	legacy.ProofContractRevision = "minimax-h3-terminal-result-proof-v1"
	require.ErrorContains(t,
		validatePlatformProviderCostCoverage(rateSet, legacy, usage),
		"legacy proof revision",
	)
}
