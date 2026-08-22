package dto

import (
	"encoding/hex"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
)

const (
	PlatformContractRateUnitOutputItem   = "output_item"
	PlatformContractRateUnitOutputSecond = "output_second"
)

// PlatformProviderContractRateInput is an immutable, versioned pricing fact
// copied from a provider contract. It deliberately accepts neither customer
// price nor new-api quota. A changed contract must use a new ID and a later
// EffectiveFrom so historical jobs keep their original evidence.
type PlatformProviderContractRateInput struct {
	ID                   string    `json:"id"`
	ProviderName         string    `json:"provider_name"`
	ChannelID            int       `json:"channel_id"`
	UpstreamModel        string    `json:"upstream_model"`
	Mode                 string    `json:"mode"`
	Resolution           string    `json:"resolution"`
	BillingUnit          string    `json:"billing_unit"`
	UnitAmountCents      int64     `json:"unit_amount_cents"`
	Currency             string    `json:"currency"`
	EffectiveFrom        time.Time `json:"effective_from"`
	SourceReference      string    `json:"source_reference"`
	SourceDocumentSHA256 string    `json:"source_document_sha256"`
}

func (input PlatformProviderContractRateInput) Validate() error {
	if parsed, err := uuid.Parse(input.ID); err != nil || parsed.String() != input.ID {
		return fmt.Errorf("contract rate id must be a canonical UUID")
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
			return fmt.Errorf("contract rate %s is invalid", name)
		}
	}
	if input.ChannelID <= 0 {
		return fmt.Errorf("contract rate channel_id must be positive")
	}
	switch input.Mode {
	case "text_to_image", "text_to_video", "image_to_video", "video_to_video":
	default:
		return fmt.Errorf("contract rate mode is invalid")
	}
	switch input.BillingUnit {
	case PlatformContractRateUnitOutputItem, PlatformContractRateUnitOutputSecond:
	default:
		return fmt.Errorf("contract rate billing_unit is invalid")
	}
	if input.UnitAmountCents <= 0 || input.UnitAmountCents > PlatformChannelCostMaxAmountCents {
		return fmt.Errorf("contract rate unit_amount_cents must be positive and in range")
	}
	if input.Currency != "CNY" {
		return fmt.Errorf("contract rate currency must be CNY")
	}
	if input.EffectiveFrom.IsZero() {
		return fmt.Errorf("contract rate effective_from is required")
	}
	if input.SourceReference == "" || strings.TrimSpace(input.SourceReference) != input.SourceReference || len(input.SourceReference) > 240 {
		return fmt.Errorf("contract rate source_reference is invalid")
	}
	decoded, err := hex.DecodeString(input.SourceDocumentSHA256)
	if err != nil || len(decoded) != 32 || strings.ToLower(input.SourceDocumentSHA256) != input.SourceDocumentSHA256 {
		return fmt.Errorf("contract rate source_document_sha256 must be a lowercase SHA-256 digest")
	}
	return nil
}
