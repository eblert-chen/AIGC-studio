package model

import (
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func platformProviderCostEvidenceFixture(t *testing.T) PlatformProviderCostAllocationEvidence {
	t.Helper()
	rateSet := dto.PlatformProviderCostRateSet{
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
	usage := dto.PlatformProviderCostUsage{OutputSeconds: 5, InputSeconds: 5, InputImageCount: 9}
	calculation, err := dto.CalculatePlatformProviderCost(rateSet, usage)
	require.NoError(t, err)
	rateSetJSON, err := common.Marshal(rateSet)
	require.NoError(t, err)
	usageJSON, err := common.Marshal(usage)
	require.NoError(t, err)
	calculationJSON, err := common.Marshal(calculation)
	require.NoError(t, err)
	digest := func(value []byte) string {
		sum := sha256.Sum256(value)
		return hex.EncodeToString(sum[:])
	}
	return PlatformProviderCostAllocationEvidence{
		CostEventID: uuid.NewString(), RelayJobID: uuid.NewString(), OutcomeID: uuid.NewString(),
		RateSetID: rateSet.ID, RateSetJSON: string(rateSetJSON), RateSetSHA256: digest(rateSetJSON),
		ReceiptSHA256: "sha256:" + strings.Repeat("a", 64), ReceiptProofSHA256: "sha256:" + strings.Repeat("b", 64),
		UsageJSON: string(usageJSON), UsageSHA256: digest(usageJSON),
		CalculationJSON: string(calculationJSON), CalculationSHA256: digest(calculationJSON),
		AmountCents: calculation.AmountCents,
	}
}

func preparePlatformProviderCostEvidenceTest(t *testing.T, historicalGuards bool) {
	t.Helper()
	preparePlatformProviderMonitorCostTest(t, historicalGuards)
	require.NoError(t, DB.Migrator().DropTable(&PlatformProviderCostAllocationEvidence{}))
	require.NoError(t, MigratePlatformProviderCostEvidenceStorageV8WithDB(DB))
	t.Cleanup(func() { require.NoError(t, DB.Migrator().DropTable(&PlatformProviderCostAllocationEvidence{})) })
}

func TestPlatformProviderCostAllocationEvidenceMigrationWiresTableAndAppendOnlyGuard(t *testing.T) {
	preparePlatformProviderCostEvidenceTest(t, true)
	require.True(t, DB.Migrator().HasTable(&PlatformProviderCostAllocationEvidence{}))
	for _, column := range []string{
		"CostEventID", "RelayJobID", "OutcomeID", "RateSetID", "RateSetJSON", "ReceiptSHA256",
		"UsageJSON", "CalculationJSON", "AmountCents",
	} {
		require.True(t, DB.Migrator().HasColumn(&PlatformProviderCostAllocationEvidence{}, column), column)
	}

	evidence := platformProviderCostEvidenceFixture(t)
	created, err := CreatePlatformProviderCostAllocationEvidenceTx(DB, &evidence)
	require.NoError(t, err)
	require.True(t, created)
	require.Error(t, DB.Exec(
		"UPDATE platform_provider_cost_allocation_evidence SET amount_cents = ? WHERE cost_event_id = ?",
		evidence.AmountCents+1, evidence.CostEventID,
	).Error)
	require.Error(t, DB.Exec(
		"DELETE FROM platform_provider_cost_allocation_evidence WHERE cost_event_id = ?", evidence.CostEventID,
	).Error)
}

func TestPlatformProviderCostAllocationEvidenceIsIdempotentAndDetectsEveryIdentityCollision(t *testing.T) {
	preparePlatformProviderCostEvidenceTest(t, false)
	evidence := platformProviderCostEvidenceFixture(t)
	created, err := CreatePlatformProviderCostAllocationEvidenceTx(DB, &evidence)
	require.NoError(t, err)
	require.True(t, created)
	createdAt := evidence.CreatedAt

	retry := evidence
	created, err = CreatePlatformProviderCostAllocationEvidenceTx(DB, &retry)
	require.NoError(t, err)
	require.False(t, created)
	require.Equal(t, createdAt, retry.CreatedAt)

	for _, mutate := range []func(*PlatformProviderCostAllocationEvidence){
		func(value *PlatformProviderCostAllocationEvidence) {
			value.RelayJobID, value.OutcomeID = uuid.NewString(), uuid.NewString()
		},
		func(value *PlatformProviderCostAllocationEvidence) {
			value.CostEventID, value.OutcomeID = uuid.NewString(), uuid.NewString()
		},
		func(value *PlatformProviderCostAllocationEvidence) {
			value.CostEventID, value.RelayJobID = uuid.NewString(), uuid.NewString()
		},
	} {
		collision := evidence
		mutate(&collision)
		_, err = CreatePlatformProviderCostAllocationEvidenceTx(DB, &collision)
		require.ErrorIs(t, err, ErrPlatformChannelCostEventCollision)
	}
}

func TestPlatformProviderCostAllocationEvidenceRejectsTamperedExactCalculation(t *testing.T) {
	preparePlatformProviderCostEvidenceTest(t, false)
	evidence := platformProviderCostEvidenceFixture(t)
	var calculation dto.PlatformProviderCostCalculation
	require.NoError(t, common.Unmarshal([]byte(evidence.CalculationJSON), &calculation))
	calculation.RoundingAdjustmentCNYMicrosNumerator = "1"
	calculationJSON, err := common.Marshal(calculation)
	require.NoError(t, err)
	digest := sha256.Sum256(calculationJSON)
	evidence.CalculationJSON = string(calculationJSON)
	evidence.CalculationSHA256 = hex.EncodeToString(digest[:])

	_, err = CreatePlatformProviderCostAllocationEvidenceTx(DB, &evidence)
	require.ErrorContains(t, err, "does not match rate set and usage")
	var count int64
	require.NoError(t, DB.Model(&PlatformProviderCostAllocationEvidence{}).Count(&count).Error)
	require.Zero(t, count)
}

func TestPlatformProviderCostAllocationEvidenceRejectsInvalidPayloadsBeforeInsert(t *testing.T) {
	preparePlatformProviderCostEvidenceTest(t, false)
	tests := []struct {
		name   string
		mutate func(*PlatformProviderCostAllocationEvidence)
	}{
		{name: "digest mismatch", mutate: func(value *PlatformProviderCostAllocationEvidence) { value.UsageSHA256 = strings.Repeat("0", 64) }},
		{name: "duplicate JSON key", mutate: func(value *PlatformProviderCostAllocationEvidence) {
			value.UsageJSON = `{"input_tokens":0,"input_tokens":1}`
		}},
		{name: "rate set identity mismatch", mutate: func(value *PlatformProviderCostAllocationEvidence) { value.RateSetID = uuid.NewString() }},
		{name: "receipt digest malformed", mutate: func(value *PlatformProviderCostAllocationEvidence) { value.ReceiptSHA256 = strings.Repeat("a", 64) }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			evidence := platformProviderCostEvidenceFixture(t)
			test.mutate(&evidence)
			_, err := CreatePlatformProviderCostAllocationEvidenceTx(DB, &evidence)
			require.Error(t, err)
		})
	}
}
