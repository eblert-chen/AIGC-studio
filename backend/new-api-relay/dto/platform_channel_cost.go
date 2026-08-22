package dto

import (
	"encoding/hex"
	"fmt"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
)

const PlatformChannelCostMaxAmountCents int64 = 9_000_000_000_000_000

const (
	PlatformChannelCostEvidenceProviderReported = "provider_reported"
	PlatformChannelCostEvidenceProviderInvoice  = "provider_invoice"
	PlatformChannelCostEvidenceContractRate     = "contract_rate"
	PlatformChannelCostEvidenceOperatorAdjusted = "operator_adjustment"
)

// PlatformChannelCostInput requires an explicit provider-side evidence source.
// Customer prices and new-api quota are intentionally not accepted as evidence.
type PlatformChannelCostInput struct {
	EventID              string
	AmountCents          int64
	IdempotencyKey       string
	ChannelKey           string
	ChannelType          string
	OccurredAt           time.Time
	ExternalReference    string
	CompanyID            string
	TaskID               string
	RelayJobID           string
	Note                 string
	EvidenceSource       string
	EvidenceReference    string
	SourceDocumentSHA256 string
}

func (input PlatformChannelCostInput) Validate() error {
	if parsed, err := uuid.Parse(input.EventID); err != nil || parsed.String() != input.EventID {
		return fmt.Errorf("channel cost event_id must be a canonical UUID")
	}
	if input.AmountCents < -PlatformChannelCostMaxAmountCents || input.AmountCents > PlatformChannelCostMaxAmountCents {
		return fmt.Errorf("channel cost amount_cents is outside the supported range")
	}
	if input.IdempotencyKey == "" || strings.TrimSpace(input.IdempotencyKey) != input.IdempotencyKey ||
		utf8.RuneCountInString(input.IdempotencyKey) < 8 || utf8.RuneCountInString(input.IdempotencyKey) > 160 {
		return fmt.Errorf("channel cost idempotency_key is invalid")
	}
	if input.ChannelKey == "" || strings.TrimSpace(input.ChannelKey) != input.ChannelKey || utf8.RuneCountInString(input.ChannelKey) > 120 {
		return fmt.Errorf("channel cost channel_key is invalid")
	}
	switch input.ChannelType {
	case "reverse", "third_party_api", "official":
	default:
		return fmt.Errorf("channel cost channel_type is invalid")
	}
	if input.OccurredAt.IsZero() {
		return fmt.Errorf("channel cost occurred_at is required")
	}
	if input.ExternalReference == "" || strings.TrimSpace(input.ExternalReference) != input.ExternalReference || utf8.RuneCountInString(input.ExternalReference) > 240 {
		return fmt.Errorf("channel cost external_reference is invalid")
	}
	for name, value := range map[string]string{
		"company_id": input.CompanyID,
		"task_id":    input.TaskID,
	} {
		if value != "" && (strings.TrimSpace(value) != value || utf8.RuneCountInString(value) > 64) {
			return fmt.Errorf("channel cost %s is invalid", name)
		}
	}
	if input.RelayJobID != "" {
		if parsed, err := uuid.Parse(input.RelayJobID); err != nil || parsed.String() != input.RelayJobID {
			return fmt.Errorf("channel cost relay_job_id must be a canonical UUID")
		}
	}
	if strings.TrimSpace(input.Note) != input.Note || utf8.RuneCountInString(input.Note) > 240 {
		return fmt.Errorf("channel cost note is invalid")
	}
	switch input.EvidenceSource {
	case PlatformChannelCostEvidenceProviderReported,
		PlatformChannelCostEvidenceProviderInvoice,
		PlatformChannelCostEvidenceContractRate,
		PlatformChannelCostEvidenceOperatorAdjusted:
	default:
		return fmt.Errorf("channel cost requires explicit provider-side evidence")
	}
	if input.EvidenceSource == PlatformChannelCostEvidenceProviderInvoice || input.EvidenceSource == PlatformChannelCostEvidenceContractRate {
		if input.EvidenceReference == "" || strings.TrimSpace(input.EvidenceReference) != input.EvidenceReference || utf8.RuneCountInString(input.EvidenceReference) > 240 {
			return fmt.Errorf("channel cost evidence_reference is required for invoice and contract evidence")
		}
		decoded, err := hex.DecodeString(input.SourceDocumentSHA256)
		if err != nil || len(decoded) != 32 || strings.ToLower(input.SourceDocumentSHA256) != input.SourceDocumentSHA256 {
			return fmt.Errorf("channel cost source_document_sha256 is required for invoice and contract evidence")
		}
	} else if input.EvidenceReference != "" || input.SourceDocumentSHA256 != "" {
		return fmt.Errorf("channel cost document evidence is only accepted for invoice and contract evidence")
	}
	return nil
}

// PlatformChannelCostPayload maps exactly to Platform POST
// /internal/channel-costs. EvidenceSource and EventID remain Relay-internal.
type PlatformChannelCostPayload struct {
	AmountCents          int64     `json:"amount_cents"`
	IdempotencyKey       string    `json:"idempotency_key"`
	ChannelKey           string    `json:"channel_key"`
	ChannelType          string    `json:"channel_type"`
	OccurredAt           time.Time `json:"occurred_at"`
	ExternalReference    string    `json:"external_reference"`
	CompanyID            string    `json:"company_id,omitempty"`
	TaskID               string    `json:"task_id,omitempty"`
	RelayJobID           string    `json:"relay_job_id,omitempty"`
	Note                 string    `json:"note"`
	EvidenceSource       string    `json:"evidence_source,omitempty"`
	EvidenceReference    string    `json:"evidence_reference,omitempty"`
	SourceDocumentSHA256 string    `json:"source_document_sha256,omitempty"`
}

func (input PlatformChannelCostInput) Payload() PlatformChannelCostPayload {
	return PlatformChannelCostPayload{
		AmountCents:          input.AmountCents,
		IdempotencyKey:       input.IdempotencyKey,
		ChannelKey:           input.ChannelKey,
		ChannelType:          input.ChannelType,
		OccurredAt:           input.OccurredAt.UTC(),
		ExternalReference:    input.ExternalReference,
		CompanyID:            input.CompanyID,
		TaskID:               input.TaskID,
		RelayJobID:           input.RelayJobID,
		Note:                 input.Note,
		EvidenceSource:       input.EvidenceSource,
		EvidenceReference:    input.EvidenceReference,
		SourceDocumentSHA256: input.SourceDocumentSHA256,
	}
}
