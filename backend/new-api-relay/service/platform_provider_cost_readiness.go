package service

import (
	"errors"
	"fmt"
	"sort"
	"time"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"gorm.io/gorm"
)

const (
	platformProviderCostBlockerRuntimeUnavailable = "provider_cost_runtime_unavailable"
	platformProviderCostBlockerSinkUnconfigured   = "provider_cost_sink_unconfigured"
	platformProviderCostBlockerRateMissing        = "provider_contract_rate_missing"
	platformProviderCostBlockerRateInvalid        = "provider_contract_rate_invalid"
	platformProviderCostBlockerUsageUnqualified   = "provider_usage_cost_materialization_unqualified"
)

// PlatformProviderCostRectangleEvidence is a secret-free proof that one exact
// provider route/mode/resolution rectangle has a current immutable contract
// rate that the existing reconciliation worker can actually consume. A cost
// sink, customer selling price, or an unconnected multi-component rate-set is
// deliberately insufficient.
type PlatformProviderCostRectangleEvidence struct {
	Mode                 string     `json:"mode"`
	Resolution           string     `json:"resolution"`
	Ready                bool       `json:"ready"`
	BlockerCode          string     `json:"blocker_code,omitempty"`
	ContractRateID       string     `json:"contract_rate_id,omitempty"`
	RateSetID            string     `json:"rate_set_id,omitempty"`
	BillingUnit          string     `json:"billing_unit,omitempty"`
	UnitAmountCents      int64      `json:"unit_amount_cents,omitempty"`
	Currency             string     `json:"currency,omitempty"`
	EffectiveFrom        *time.Time `json:"effective_from,omitempty"`
	SourceDocumentSHA256 string     `json:"source_document_sha256,omitempty"`
}

type platformProviderCostRouteReadiness struct {
	Ready          bool
	RectangleCount int
	ReadyCount     int
	Rectangles     []PlatformProviderCostRectangleEvidence
}

type platformProviderCostReadinessSnapshot struct {
	CostConfigured bool
	ContractRates  []dto.PlatformProviderContractRateInput
	CostRateSets   []dto.PlatformProviderCostRateSet
	LoadError      error
}

func currentPlatformProviderCostReadinessSnapshot() platformProviderCostReadinessSnapshot {
	configuration, err := getPlatformProviderRuntimeConfiguration()
	if err != nil {
		return platformProviderCostReadinessSnapshot{LoadError: err}
	}
	return platformProviderCostReadinessSnapshot{
		CostConfigured: configuration.CostConfigured,
		ContractRates:  append([]dto.PlatformProviderContractRateInput(nil), configuration.ContractRates...),
		CostRateSets:   append([]dto.PlatformProviderCostRateSet(nil), configuration.CostRateSets...),
	}
}

func platformProviderCostReadinessForRoute(
	snapshot platformProviderCostReadinessSnapshot,
	route PlatformRelayRouteDeclaration,
	now time.Time,
) (platformProviderCostRouteReadiness, error) {
	result := platformProviderCostRouteReadiness{}
	modes := make([]string, 0, len(route.Capabilities.Modes))
	for mode := range route.Capabilities.Modes {
		modes = append(modes, mode)
	}
	sort.Strings(modes)
	for _, mode := range modes {
		resolutions := append([]string(nil), route.Capabilities.Modes[mode].Limits.Resolutions...)
		sort.Strings(resolutions)
		for _, resolution := range resolutions {
			result.Rectangles = append(result.Rectangles, PlatformProviderCostRectangleEvidence{
				Mode:       mode,
				Resolution: resolution,
			})
		}
	}
	result.RectangleCount = len(result.Rectangles)
	if result.RectangleCount == 0 {
		return result, fmt.Errorf("provider route %q has no costable mode/resolution rectangle", route.RouteID)
	}

	for index := range result.Rectangles {
		rectangle := &result.Rectangles[index]
		switch {
		case snapshot.LoadError != nil:
			rectangle.BlockerCode = platformProviderCostBlockerRuntimeUnavailable
		case !snapshot.CostConfigured:
			rectangle.BlockerCode = platformProviderCostBlockerSinkUnconfigured
		default:
			rateSet := latestConfiguredPlatformProviderCostRateSet(
				snapshot.CostRateSets, route, rectangle.Mode, rectangle.Resolution, now,
			)
			if rateSet != nil {
				if err := rateSet.Validate(); err != nil {
					rectangle.BlockerCode = platformProviderCostBlockerRateInvalid
					continue
				}
				effectiveFrom := rateSet.EffectiveFrom.UTC()
				rectangle.RateSetID = rateSet.ID
				rectangle.BillingUnit = rateSet.PlatformBillingUnit
				rectangle.Currency = rateSet.Currency
				rectangle.EffectiveFrom = &effectiveFrom
				rectangle.SourceDocumentSHA256 = rateSet.SourceDocumentSHA256
				if dto.PlatformContractRateRequiresTokenUsageEvidence(route.UpstreamModel) {
					// Actual token cost is connected, but commercial readiness still
					// requires a server-enforced customer-unit cost ceiling. Current
					// capability contracts do not declare one, so fail closed.
					rectangle.BlockerCode = platformProviderCostBlockerUsageUnqualified
					continue
				}
				rectangle.Ready = true
				result.ReadyCount++
				continue
			}
			if dto.PlatformContractRateRequiresTokenUsageEvidence(route.UpstreamModel) {
				rectangle.BlockerCode = platformProviderCostBlockerRateMissing
				continue
			}
			rate := latestConfiguredPlatformProviderContractRate(
				snapshot.ContractRates,
				route,
				rectangle.Mode,
				rectangle.Resolution,
				now,
			)
			if rate == nil {
				rectangle.BlockerCode = platformProviderCostBlockerRateMissing
				continue
			}
			if err := verifyPersistedPlatformProviderContractRate(*rate); err != nil {
				if errors.Is(err, gorm.ErrRecordNotFound) {
					rectangle.BlockerCode = platformProviderCostBlockerRateMissing
					continue
				}
				return result, err
			}
			effectiveFrom := rate.EffectiveFrom.UTC()
			rectangle.Ready = true
			rectangle.ContractRateID = rate.ID
			rectangle.BillingUnit = rate.BillingUnit
			rectangle.UnitAmountCents = rate.UnitAmountCents
			rectangle.Currency = rate.Currency
			rectangle.EffectiveFrom = &effectiveFrom
			rectangle.SourceDocumentSHA256 = rate.SourceDocumentSHA256
			result.ReadyCount++
		}
	}
	result.Ready = result.ReadyCount == result.RectangleCount
	return result, nil
}

func latestConfiguredPlatformProviderCostRateSet(
	rateSets []dto.PlatformProviderCostRateSet,
	route PlatformRelayRouteDeclaration,
	mode string,
	resolution string,
	now time.Time,
) *dto.PlatformProviderCostRateSet {
	var selected *dto.PlatformProviderCostRateSet
	for index := range rateSets {
		candidate := &rateSets[index]
		if candidate.ProviderName != route.ProviderName || candidate.ChannelID != route.ChannelID ||
			candidate.UpstreamModel != route.UpstreamModel || candidate.Mode != mode ||
			candidate.Resolution != resolution || candidate.EffectiveFrom.UTC().After(now.UTC()) {
			continue
		}
		if selected == nil || candidate.EffectiveFrom.UTC().After(selected.EffectiveFrom.UTC()) {
			selected = candidate
		}
	}
	if selected == nil {
		return nil
	}
	copy := *selected
	copy.Components = append([]dto.PlatformProviderCostRateComponent(nil), selected.Components...)
	return &copy
}

func latestConfiguredPlatformProviderContractRate(
	rates []dto.PlatformProviderContractRateInput,
	route PlatformRelayRouteDeclaration,
	mode string,
	resolution string,
	now time.Time,
) *dto.PlatformProviderContractRateInput {
	var selected *dto.PlatformProviderContractRateInput
	for index := range rates {
		candidate := &rates[index]
		if candidate.ProviderName != route.ProviderName || candidate.ChannelID != route.ChannelID ||
			candidate.UpstreamModel != route.UpstreamModel || candidate.Mode != mode ||
			candidate.Resolution != resolution || candidate.EffectiveFrom.UTC().After(now.UTC()) {
			continue
		}
		if selected == nil || candidate.EffectiveFrom.UTC().After(selected.EffectiveFrom.UTC()) {
			selected = candidate
		}
	}
	if selected == nil {
		return nil
	}
	copy := *selected
	return &copy
}

func verifyPersistedPlatformProviderContractRate(input dto.PlatformProviderContractRateInput) error {
	if err := input.Validate(); err != nil {
		return fmt.Errorf("configured provider contract rate is invalid: %w", err)
	}
	if model.DB == nil {
		return fmt.Errorf("provider contract rate database is unavailable")
	}
	persisted, err := model.GetPlatformProviderContractRateTx(model.DB, input.ID)
	if err != nil {
		return err
	}
	if persisted.ProviderName != input.ProviderName || persisted.ChannelID != input.ChannelID ||
		persisted.UpstreamModel != input.UpstreamModel || persisted.Mode != input.Mode ||
		persisted.Resolution != input.Resolution || persisted.BillingUnit != input.BillingUnit ||
		persisted.UnitAmountCents != input.UnitAmountCents || persisted.Currency != input.Currency ||
		!persisted.EffectiveFrom.UTC().Equal(input.EffectiveFrom.UTC()) ||
		persisted.SourceReference != input.SourceReference ||
		persisted.SourceDocumentSHA256 != input.SourceDocumentSHA256 {
		return fmt.Errorf("persisted provider contract rate %s does not match its runtime evidence", input.ID)
	}
	return nil
}
