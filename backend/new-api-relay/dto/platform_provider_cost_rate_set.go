package dto

import (
	"encoding/hex"
	"fmt"
	"math/big"
	"net/url"
	"strings"
	"time"

	"github.com/google/uuid"
)

const (
	PlatformProviderCostRateSetSchemaVersion = 1

	PlatformProviderCostMetricInputToken          = "input_token"
	PlatformProviderCostMetricUncachedInputToken  = "uncached_input_token"
	PlatformProviderCostMetricCachedInputToken    = "cached_input_token"
	PlatformProviderCostMetricOutputToken         = "output_token"
	PlatformProviderCostMetricOutputTextToken     = "output_text_token"
	PlatformProviderCostMetricOutputVideoToken    = "output_video_token"
	PlatformProviderCostMetricThoughtToken        = "thought_token"
	PlatformProviderCostMetricTotalToken          = "total_token"
	PlatformProviderCostMetricOutputSecond        = "output_second"
	PlatformProviderCostMetricInputSecond         = "input_second"
	PlatformProviderCostMetricOutputItem          = "output_item"
	PlatformProviderCostMetricInputImageAboveFree = "input_image_above_free"

	PlatformProviderPlatformBillingUnitPerSecond = "per_second"
	PlatformProviderPlatformBillingUnitPerItem   = "per_item"

	platformProviderCurrencyMicrosPerUnit = int64(1_000_000)
	platformProviderCNYMicrosPerCent      = int64(10_000)
)

// PlatformProviderCostRateSet is a complete immutable provider-side pricing
// fact for one accepted provider route rectangle. Components use rational
// currency-micro amounts so token prices such as USD 1.50 / 1M tokens remain
// exact without floating point or premature per-component rounding.
//
// USD rate sets must bind the exact observed FX response used to convert the
// provider cost to CNY. CNY sets use the exact identity conversion and do not
// pretend that an external FX observation was needed.
type PlatformProviderCostRateSet struct {
	SchemaVersion              int                                 `json:"schema_version"`
	ID                         string                              `json:"id"`
	ProviderName               string                              `json:"provider_name"`
	ProviderOrigin             string                              `json:"provider_origin"`
	PricingMarket              string                              `json:"pricing_market"`
	PlatformBillingUnit        string                              `json:"platform_billing_unit"`
	ChannelID                  int                                 `json:"channel_id"`
	UpstreamModel              string                              `json:"upstream_model"`
	Mode                       string                              `json:"mode"`
	Resolution                 string                              `json:"resolution"`
	Currency                   string                              `json:"currency"`
	EffectiveFrom              time.Time                           `json:"effective_from"`
	SourceReference            string                              `json:"source_reference"`
	SourceDocumentSHA256       string                              `json:"source_document_sha256"`
	FXCNYMicrosPerCurrencyUnit int64                               `json:"fx_cny_micros_per_currency_unit"`
	FXSourceReference          string                              `json:"fx_source_reference,omitempty"`
	FXSourceDocumentSHA256     string                              `json:"fx_source_document_sha256,omitempty"`
	FXObservedAt               time.Time                           `json:"fx_observed_at,omitempty"`
	Components                 []PlatformProviderCostRateComponent `json:"components"`
}

type PlatformProviderCostRateComponent struct {
	Metric                      string `json:"metric"`
	UnitAmountMicrosNumerator   int64  `json:"unit_amount_micros_numerator"`
	UnitAmountMicrosDenominator int64  `json:"unit_amount_micros_denominator"`
	FreeUnits                   int64  `json:"free_units,omitempty"`
}

// PlatformProviderCostUsage contains provider-observed terminal quantities,
// never requested estimates. Protocol-specific receipt validation happens
// before this value reaches the calculator.
type PlatformProviderCostUsage struct {
	InputTokens       int64 `json:"input_tokens"`
	CachedInputTokens int64 `json:"cached_input_tokens"`
	OutputTokens      int64 `json:"output_tokens"`
	OutputTextTokens  int64 `json:"output_text_tokens"`
	OutputVideoTokens int64 `json:"output_video_tokens"`
	ThoughtTokens     int64 `json:"thought_tokens"`
	TotalTokens       int64 `json:"total_tokens"`
	OutputSeconds     int64 `json:"output_seconds"`
	InputSeconds      int64 `json:"input_seconds"`
	OutputItems       int64 `json:"output_items"`
	InputImageCount   int64 `json:"input_image_count"`
}

// PlatformProviderCostCalculation preserves both exact rational amounts and
// the final per-task CNY-cent posting. RoundingAdjustmentCNYMicros is the
// non-negative difference between the posted cents and exact converted cost;
// invoice settlement can aggregate and reverse that difference instead of
// pretending every sub-cent task was independently billed by the provider.
type PlatformProviderCostCalculation struct {
	AmountCents                            int64  `json:"amount_cents"`
	SourceCurrency                         string `json:"source_currency"`
	SourceAmountMicrosNumerator            string `json:"source_amount_micros_numerator"`
	SourceAmountMicrosDenominator          string `json:"source_amount_micros_denominator"`
	CNYAmountMicrosNumerator               string `json:"cny_amount_micros_numerator"`
	CNYAmountMicrosDenominator             string `json:"cny_amount_micros_denominator"`
	RoundingAdjustmentCNYMicrosNumerator   string `json:"rounding_adjustment_cny_micros_numerator"`
	RoundingAdjustmentCNYMicrosDenominator string `json:"rounding_adjustment_cny_micros_denominator"`
}

func (input PlatformProviderCostRateSet) Validate() error {
	if input.SchemaVersion != PlatformProviderCostRateSetSchemaVersion {
		return fmt.Errorf("provider cost rate set schema_version is invalid")
	}
	if parsed, err := uuid.Parse(input.ID); err != nil || parsed.String() != input.ID {
		return fmt.Errorf("provider cost rate set id must be a canonical UUID")
	}
	for name, value := range map[string]struct {
		value   string
		maximum int
	}{
		"provider_name":  {input.ProviderName, 64},
		"upstream_model": {input.UpstreamModel, 128},
		"mode":           {input.Mode, 32},
		"resolution":     {input.Resolution, 32},
	} {
		if value.value == "" || strings.TrimSpace(value.value) != value.value || len(value.value) > value.maximum {
			return fmt.Errorf("provider cost rate set %s is invalid", name)
		}
	}
	if input.ChannelID <= 0 {
		return fmt.Errorf("provider cost rate set channel_id must be positive")
	}
	if !platformProviderOriginValid(input.ProviderOrigin) {
		return fmt.Errorf("provider cost rate set provider_origin is invalid")
	}
	switch input.PricingMarket {
	case "cn", "global", "provider_contract":
	default:
		return fmt.Errorf("provider cost rate set pricing_market is invalid")
	}
	switch input.PlatformBillingUnit {
	case PlatformProviderPlatformBillingUnitPerSecond, PlatformProviderPlatformBillingUnitPerItem:
	default:
		return fmt.Errorf("provider cost rate set platform_billing_unit is invalid")
	}
	switch input.Mode {
	case "text_to_image", "text_to_video", "image_to_video", "video_to_video":
	default:
		return fmt.Errorf("provider cost rate set mode is invalid")
	}
	if input.Currency != "CNY" && input.Currency != "USD" {
		return fmt.Errorf("provider cost rate set currency is invalid")
	}
	if input.EffectiveFrom.IsZero() || input.EffectiveFrom.Location() != time.UTC {
		return fmt.Errorf("provider cost rate set effective_from must use canonical UTC")
	}
	if !platformProviderEvidenceReferenceValid(input.SourceReference) ||
		!platformProviderSHA256Valid(input.SourceDocumentSHA256) {
		return fmt.Errorf("provider cost rate set source evidence is invalid")
	}
	if input.FXCNYMicrosPerCurrencyUnit <= 0 {
		return fmt.Errorf("provider cost rate set FX value must be positive")
	}
	if input.Currency == "CNY" {
		if input.FXCNYMicrosPerCurrencyUnit != platformProviderCurrencyMicrosPerUnit ||
			input.FXSourceReference != "" || input.FXSourceDocumentSHA256 != "" || !input.FXObservedAt.IsZero() {
			return fmt.Errorf("CNY provider cost rate set must use only the identity conversion")
		}
	} else if !platformProviderEvidenceReferenceValid(input.FXSourceReference) ||
		!platformProviderSHA256Valid(input.FXSourceDocumentSHA256) || input.FXObservedAt.IsZero() ||
		input.FXObservedAt.Location() != time.UTC || !platformProviderFXObservationFresh(input.FXObservedAt, input.EffectiveFrom) {
		return fmt.Errorf("USD provider cost rate set requires immutable FX evidence")
	}
	if len(input.Components) == 0 || len(input.Components) > 8 {
		return fmt.Errorf("provider cost rate set components are invalid")
	}
	seen := make(map[string]struct{}, len(input.Components))
	previousMetric := ""
	for _, component := range input.Components {
		if previousMetric != "" && component.Metric <= previousMetric {
			return fmt.Errorf("provider cost rate set components must be in canonical metric order")
		}
		previousMetric = component.Metric
		if _, duplicate := seen[component.Metric]; duplicate {
			return fmt.Errorf("provider cost rate set component metric is duplicated")
		}
		seen[component.Metric] = struct{}{}
		switch component.Metric {
		case PlatformProviderCostMetricInputToken,
			PlatformProviderCostMetricUncachedInputToken,
			PlatformProviderCostMetricCachedInputToken,
			PlatformProviderCostMetricOutputToken,
			PlatformProviderCostMetricOutputTextToken,
			PlatformProviderCostMetricOutputVideoToken,
			PlatformProviderCostMetricThoughtToken,
			PlatformProviderCostMetricTotalToken,
			PlatformProviderCostMetricOutputSecond,
			PlatformProviderCostMetricInputSecond,
			PlatformProviderCostMetricOutputItem:
			if component.FreeUnits != 0 {
				return fmt.Errorf("provider cost rate set free_units is not allowed for this metric")
			}
		case PlatformProviderCostMetricInputImageAboveFree:
			if component.FreeUnits < 0 || component.FreeUnits > 1_000_000 {
				return fmt.Errorf("provider cost rate set image free_units is invalid")
			}
		default:
			return fmt.Errorf("provider cost rate set component metric is invalid")
		}
		if component.UnitAmountMicrosNumerator <= 0 ||
			component.UnitAmountMicrosDenominator <= 0 ||
			component.UnitAmountMicrosNumerator > 1_000_000_000_000_000 ||
			component.UnitAmountMicrosDenominator > 1_000_000_000_000_000 {
			return fmt.Errorf("provider cost rate set component amount is invalid")
		}
		greatestCommonDivisor := new(big.Int).GCD(nil, nil,
			big.NewInt(component.UnitAmountMicrosNumerator),
			big.NewInt(component.UnitAmountMicrosDenominator),
		)
		if greatestCommonDivisor.Cmp(big.NewInt(1)) != 0 {
			return fmt.Errorf("provider cost rate set component amount must be reduced")
		}
	}
	_, hasTotalToken := seen[PlatformProviderCostMetricTotalToken]
	if hasTotalToken {
		for _, metric := range []string{
			PlatformProviderCostMetricInputToken,
			PlatformProviderCostMetricUncachedInputToken,
			PlatformProviderCostMetricCachedInputToken,
			PlatformProviderCostMetricOutputToken,
			PlatformProviderCostMetricOutputTextToken,
			PlatformProviderCostMetricOutputVideoToken,
			PlatformProviderCostMetricThoughtToken,
		} {
			if _, ambiguous := seen[metric]; ambiguous {
				return fmt.Errorf("provider cost rate set token components are ambiguous")
			}
		}
	}
	if _, genericInput := seen[PlatformProviderCostMetricInputToken]; genericInput {
		if _, split := seen[PlatformProviderCostMetricUncachedInputToken]; split {
			return fmt.Errorf("provider cost rate set input token components are ambiguous")
		}
		if _, split := seen[PlatformProviderCostMetricCachedInputToken]; split {
			return fmt.Errorf("provider cost rate set input token components are ambiguous")
		}
	}
	if _, genericOutput := seen[PlatformProviderCostMetricOutputToken]; genericOutput {
		for _, metric := range []string{PlatformProviderCostMetricOutputTextToken, PlatformProviderCostMetricOutputVideoToken} {
			if _, split := seen[metric]; split {
				return fmt.Errorf("provider cost rate set output token components are ambiguous")
			}
		}
	}
	return nil
}

func (usage PlatformProviderCostUsage) Validate() error {
	if usage.InputTokens < 0 || usage.CachedInputTokens < 0 || usage.OutputTokens < 0 ||
		usage.OutputTextTokens < 0 || usage.OutputVideoTokens < 0 || usage.ThoughtTokens < 0 || usage.TotalTokens < 0 ||
		usage.OutputSeconds < 0 || usage.InputSeconds < 0 || usage.OutputItems < 0 ||
		usage.InputImageCount < 0 {
		return fmt.Errorf("provider cost usage cannot be negative")
	}
	if usage.CachedInputTokens > usage.InputTokens ||
		(usage.OutputTextTokens+usage.OutputVideoTokens > 0 && usage.OutputTextTokens+usage.OutputVideoTokens != usage.OutputTokens) ||
		(usage.TotalTokens > 0 && usage.TotalTokens < usage.InputTokens+usage.OutputTokens+usage.ThoughtTokens) {
		return fmt.Errorf("provider cost token usage is inconsistent")
	}
	return nil
}

func platformProviderOriginValid(value string) bool {
	parsed, err := url.Parse(value)
	return err == nil && value == strings.TrimSpace(value) && parsed.Scheme == "https" && parsed.Host != "" &&
		parsed.User == nil && parsed.Port() == "" && parsed.RawQuery == "" && parsed.Fragment == "" &&
		(parsed.EscapedPath() == "" || parsed.EscapedPath() == "/") && value == "https://"+parsed.Hostname()
}

func platformProviderFXObservationFresh(observedAt time.Time, effectiveFrom time.Time) bool {
	age := effectiveFrom.Sub(observedAt)
	return age >= 0 && age <= 7*24*time.Hour
}

func platformProviderEvidenceReferenceValid(value string) bool {
	return value != "" && strings.TrimSpace(value) == value && len(value) <= 500 && !strings.ContainsAny(value, "\x00\r\n\t")
}

func platformProviderSHA256Valid(value string) bool {
	decoded, err := hex.DecodeString(value)
	return err == nil && len(decoded) == 32 && strings.ToLower(value) == value
}

func (usage PlatformProviderCostUsage) quantity(metric string, freeUnits int64) (int64, error) {
	switch metric {
	case PlatformProviderCostMetricInputToken:
		return usage.InputTokens, nil
	case PlatformProviderCostMetricUncachedInputToken:
		return usage.InputTokens - usage.CachedInputTokens, nil
	case PlatformProviderCostMetricCachedInputToken:
		return usage.CachedInputTokens, nil
	case PlatformProviderCostMetricOutputToken:
		return usage.OutputTokens, nil
	case PlatformProviderCostMetricOutputTextToken:
		return usage.OutputTextTokens, nil
	case PlatformProviderCostMetricOutputVideoToken:
		return usage.OutputVideoTokens, nil
	case PlatformProviderCostMetricThoughtToken:
		return usage.ThoughtTokens, nil
	case PlatformProviderCostMetricTotalToken:
		return usage.TotalTokens, nil
	case PlatformProviderCostMetricOutputSecond:
		return usage.OutputSeconds, nil
	case PlatformProviderCostMetricInputSecond:
		return usage.InputSeconds, nil
	case PlatformProviderCostMetricOutputItem:
		return usage.OutputItems, nil
	case PlatformProviderCostMetricInputImageAboveFree:
		if usage.InputImageCount <= freeUnits {
			return 0, nil
		}
		return usage.InputImageCount - freeUnits, nil
	default:
		return 0, fmt.Errorf("provider cost metric is unsupported")
	}
}

// CalculatePlatformProviderCostCents sums the complete rational source-cost,
// applies one immutable FX observation, and rounds up only once at the final
// CNY-cent boundary. A successful provider task with a configured rate set may
// never become a zero-cost fact through truncation.
func CalculatePlatformProviderCostCents(
	rateSet PlatformProviderCostRateSet,
	usage PlatformProviderCostUsage,
) (int64, error) {
	calculation, err := CalculatePlatformProviderCost(rateSet, usage)
	return calculation.AmountCents, err
}

func CalculatePlatformProviderCost(
	rateSet PlatformProviderCostRateSet,
	usage PlatformProviderCostUsage,
) (PlatformProviderCostCalculation, error) {
	var calculation PlatformProviderCostCalculation
	if err := rateSet.Validate(); err != nil {
		return calculation, err
	}
	if err := usage.Validate(); err != nil {
		return calculation, err
	}
	totalSourceMicros := new(big.Rat)
	usedQuantity := false
	for _, component := range rateSet.Components {
		quantity, err := usage.quantity(component.Metric, component.FreeUnits)
		if err != nil {
			return calculation, err
		}
		if quantity == 0 {
			continue
		}
		usedQuantity = true
		term := new(big.Rat).SetFrac(
			new(big.Int).Mul(big.NewInt(quantity), big.NewInt(component.UnitAmountMicrosNumerator)),
			big.NewInt(component.UnitAmountMicrosDenominator),
		)
		totalSourceMicros.Add(totalSourceMicros, term)
	}
	if !usedQuantity || totalSourceMicros.Sign() <= 0 {
		return calculation, fmt.Errorf("provider cost rate set has no proven billable quantity")
	}

	cnyMicros := new(big.Rat).Mul(totalSourceMicros, new(big.Rat).SetFrac(
		big.NewInt(rateSet.FXCNYMicrosPerCurrencyUnit),
		big.NewInt(platformProviderCurrencyMicrosPerUnit),
	))
	cnyCents := new(big.Rat).Quo(cnyMicros, big.NewRat(platformProviderCNYMicrosPerCent, 1))
	quotient, remainder := new(big.Int).QuoRem(cnyCents.Num(), cnyCents.Denom(), new(big.Int))
	if remainder.Sign() > 0 {
		quotient.Add(quotient, big.NewInt(1))
	}
	if !quotient.IsInt64() || quotient.Sign() <= 0 || quotient.Int64() > PlatformChannelCostMaxAmountCents {
		return calculation, fmt.Errorf("provider cost amount is outside the supported CNY-cent range")
	}
	postedCNYMicros := new(big.Rat).Mul(new(big.Rat).SetInt(quotient), big.NewRat(platformProviderCNYMicrosPerCent, 1))
	roundingAdjustment := new(big.Rat).Sub(postedCNYMicros, cnyMicros)
	calculation = PlatformProviderCostCalculation{
		AmountCents:                            quotient.Int64(),
		SourceCurrency:                         rateSet.Currency,
		SourceAmountMicrosNumerator:            totalSourceMicros.Num().String(),
		SourceAmountMicrosDenominator:          totalSourceMicros.Denom().String(),
		CNYAmountMicrosNumerator:               cnyMicros.Num().String(),
		CNYAmountMicrosDenominator:             cnyMicros.Denom().String(),
		RoundingAdjustmentCNYMicrosNumerator:   roundingAdjustment.Num().String(),
		RoundingAdjustmentCNYMicrosDenominator: roundingAdjustment.Denom().String(),
	}
	return calculation, nil
}
