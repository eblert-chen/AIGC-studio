package service

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestCalculatePlatformProviderContractCostRejectsGeminiOmniLegacyUnits(t *testing.T) {
	output := dto.PlatformGenerationOutputOptions{Count: 1, DurationSeconds: 5}
	for _, billingUnit := range []string{
		dto.PlatformContractRateUnitOutputItem,
		dto.PlatformContractRateUnitOutputSecond,
	} {
		t.Run(billingUnit, func(t *testing.T) {
			rate := model.PlatformProviderContractRate{
				UpstreamModel:   dto.PlatformProviderUpstreamModelGeminiOmni11Flash,
				BillingUnit:     billingUnit,
				UnitAmountCents: 10,
			}

			amount, err := calculatePlatformProviderContractCost(rate, output)
			require.Zero(t, amount)
			require.ErrorIs(t, err, model.ErrPlatformProviderContractRateUsageEvidenceRequired)
		})
	}
}

func TestCalculatePlatformProviderContractCostKeepsVeoOutputSecond(t *testing.T) {
	rate := model.PlatformProviderContractRate{
		UpstreamModel:   "veo-3.1-generate-preview",
		BillingUnit:     dto.PlatformContractRateUnitOutputSecond,
		UnitAmountCents: 10,
	}
	output := dto.PlatformGenerationOutputOptions{Count: 2, DurationSeconds: 5}

	amount, err := calculatePlatformProviderContractCost(rate, output)
	require.NoError(t, err)
	require.Equal(t, int64(100), amount)
}

func TestGeminiOmniCostReconciliationDefersLegacyRateWithoutZeroCostEvent(t *testing.T) {
	preparePlatformProviderMonitorCostServiceTest(t)
	route := createPlatformProviderServiceRoute(t, "runtime-cost-gemini-omni", 9341)
	require.NoError(t, model.DB.Model(&model.PlatformGenerationProviderRoute{}).
		Where("id = ?", route.ID).
		Update("upstream_model", dto.PlatformProviderUpstreamModelGeminiOmni11Flash).Error)
	route.UpstreamModel = dto.PlatformProviderUpstreamModelGeminiOmni11Flash
	job, outcome, _ := createPlatformChannelCostRuntimeFixture(t, route, 5, 1)

	// Simulate a legacy/imported row that bypassed the supported DTO sync path.
	// Reconciliation must still reject it rather than treating five requested
	// seconds as the exact Omni bill.
	require.NoError(t, model.DB.Create(&model.PlatformProviderContractRate{
		ID:                   uuid.NewString(),
		ProviderName:         route.ProviderName,
		ChannelID:            route.ChannelID,
		UpstreamModel:        route.UpstreamModel,
		Mode:                 route.Mode,
		Resolution:           "720p",
		BillingUnit:          dto.PlatformContractRateUnitOutputSecond,
		UnitAmountCents:      10,
		Currency:             "CNY",
		EffectiveFrom:        outcome.OccurredAt.Add(-time.Hour),
		SourceReference:      "legacy-omni-rate-must-not-materialize",
		SourceDocumentSHA256: strings.Repeat("f", 64),
	}).Error)

	processed, err := RunPlatformChannelCostReconciliationOnce(context.Background(), 30*time.Second)
	require.NoError(t, err)
	require.True(t, processed)

	var eventCount int64
	require.NoError(t, model.DB.Model(&model.PlatformChannelCostEvent{}).
		Where("relay_job_id = ?", job.ID).
		Count(&eventCount).Error)
	require.Zero(t, eventCount)

	var queue model.PlatformChannelCostReconciliation
	require.NoError(t, model.DB.First(&queue, "relay_job_id = ?", job.ID).Error)
	require.Equal(t, model.PlatformCostReconciliationWaiting, queue.State)
	require.Equal(t, platformChannelCostTokenUsageEvidenceRequired, queue.LastErrorCode)
}
