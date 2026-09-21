package model

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

// PlatformProviderCostAllocationEvidence preserves the exact provider rate
// set, terminal usage, FX conversion and sub-cent rounding for one immutable
// channel-cost event. It is separate from mutable delivery state and contains
// no provider credential or result URL.
type PlatformProviderCostAllocationEvidence struct {
	CostEventID        string    `json:"cost_event_id" gorm:"type:varchar(36);primaryKey"`
	RelayJobID         string    `json:"relay_job_id" gorm:"type:varchar(36);not null;uniqueIndex"`
	OutcomeID          string    `json:"outcome_id" gorm:"type:varchar(36);not null;uniqueIndex"`
	RateSetID          string    `json:"rate_set_id" gorm:"type:varchar(36);not null;index"`
	RateSetJSON        string    `json:"-" gorm:"type:text;not null"`
	RateSetSHA256      string    `json:"rate_set_sha256" gorm:"type:char(64);not null"`
	ReceiptSHA256      string    `json:"receipt_sha256" gorm:"type:varchar(71);not null"`
	ReceiptProofSHA256 string    `json:"receipt_proof_sha256" gorm:"type:varchar(71);not null"`
	UsageJSON          string    `json:"-" gorm:"type:text;not null"`
	UsageSHA256        string    `json:"usage_sha256" gorm:"type:char(64);not null"`
	CalculationJSON    string    `json:"-" gorm:"type:text;not null"`
	CalculationSHA256  string    `json:"calculation_sha256" gorm:"type:char(64);not null"`
	AmountCents        int64     `json:"amount_cents" gorm:"not null"`
	CreatedAt          time.Time `json:"created_at"`
}

func (PlatformProviderCostAllocationEvidence) TableName() string {
	return "platform_provider_cost_allocation_evidence"
}

func (*PlatformProviderCostAllocationEvidence) BeforeUpdate(*gorm.DB) error {
	return ErrPlatformChannelCostImmutable
}

func (*PlatformProviderCostAllocationEvidence) BeforeDelete(*gorm.DB) error {
	return ErrPlatformChannelCostImmutable
}

func CreatePlatformProviderCostAllocationEvidenceTx(
	tx *gorm.DB,
	evidence *PlatformProviderCostAllocationEvidence,
) (bool, error) {
	if tx == nil || evidence == nil {
		return false, fmt.Errorf("provider cost allocation evidence is required")
	}
	if err := validatePlatformProviderCostAllocationEvidence(evidence); err != nil {
		return false, err
	}
	now, err := GetDBTimeTx(tx)
	if err != nil {
		return false, err
	}
	evidence.CreatedAt = now
	result := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(evidence)
	if result.Error != nil {
		return false, result.Error
	}
	if result.RowsAffected == 1 {
		return true, nil
	}
	var existing PlatformProviderCostAllocationEvidence
	if err := tx.Where("cost_event_id = ? OR relay_job_id = ? OR outcome_id = ?",
		evidence.CostEventID, evidence.RelayJobID, evidence.OutcomeID).First(&existing).Error; err != nil {
		return false, err
	}
	if !platformProviderCostAllocationEvidenceEqual(existing, *evidence) {
		return false, ErrPlatformChannelCostEventCollision
	}
	*evidence = existing
	return false, nil
}

func validatePlatformProviderCostAllocationEvidence(evidence *PlatformProviderCostAllocationEvidence) error {
	for name, value := range map[string]string{
		"cost_event_id": evidence.CostEventID,
		"relay_job_id":  evidence.RelayJobID,
		"outcome_id":    evidence.OutcomeID,
		"rate_set_id":   evidence.RateSetID,
	} {
		parsed, err := uuid.Parse(value)
		if err != nil || parsed.String() != value {
			return fmt.Errorf("provider cost allocation %s is invalid", name)
		}
	}
	if evidence.AmountCents <= 0 || evidence.AmountCents > dto.PlatformChannelCostMaxAmountCents ||
		!platformGenerationSHA256Revision(evidence.ReceiptSHA256) ||
		!platformGenerationSHA256Revision(evidence.ReceiptProofSHA256) {
		return fmt.Errorf("provider cost allocation amount or receipt evidence is invalid")
	}
	if common.RejectDuplicateJSONKeys([]byte(evidence.RateSetJSON)) != nil ||
		common.RejectDuplicateJSONKeys([]byte(evidence.UsageJSON)) != nil ||
		common.RejectDuplicateJSONKeys([]byte(evidence.CalculationJSON)) != nil {
		return fmt.Errorf("provider cost allocation JSON evidence is invalid")
	}
	var rateSet dto.PlatformProviderCostRateSet
	if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(evidence.RateSetJSON), &rateSet); err != nil ||
		rateSet.Validate() != nil || rateSet.ID != evidence.RateSetID {
		return fmt.Errorf("provider cost allocation rate set is invalid")
	}
	var usage dto.PlatformProviderCostUsage
	if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(evidence.UsageJSON), &usage); err != nil || usage.Validate() != nil {
		return fmt.Errorf("provider cost allocation usage is invalid")
	}
	var calculation dto.PlatformProviderCostCalculation
	if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(evidence.CalculationJSON), &calculation); err != nil ||
		calculation.AmountCents != evidence.AmountCents {
		return fmt.Errorf("provider cost allocation calculation is invalid")
	}
	expectedCalculation, err := dto.CalculatePlatformProviderCost(rateSet, usage)
	if err != nil || calculation != expectedCalculation {
		return fmt.Errorf("provider cost allocation calculation does not match rate set and usage")
	}
	for raw, expected := range map[string]string{
		evidence.RateSetJSON:     evidence.RateSetSHA256,
		evidence.UsageJSON:       evidence.UsageSHA256,
		evidence.CalculationJSON: evidence.CalculationSHA256,
	} {
		digest := sha256.Sum256([]byte(raw))
		if expected != hex.EncodeToString(digest[:]) || !platformProviderCostEvidenceSHA256Valid(expected) {
			return fmt.Errorf("provider cost allocation digest is invalid")
		}
	}
	return nil
}

func platformProviderCostEvidenceSHA256Valid(value string) bool {
	decoded, err := hex.DecodeString(value)
	return err == nil && len(decoded) == sha256.Size && value == strings.ToLower(value)
}

func platformProviderCostAllocationEvidenceEqual(left, right PlatformProviderCostAllocationEvidence) bool {
	return left.CostEventID == right.CostEventID && left.RelayJobID == right.RelayJobID &&
		left.OutcomeID == right.OutcomeID && left.RateSetID == right.RateSetID &&
		left.RateSetJSON == right.RateSetJSON && left.RateSetSHA256 == right.RateSetSHA256 &&
		left.ReceiptSHA256 == right.ReceiptSHA256 && left.ReceiptProofSHA256 == right.ReceiptProofSHA256 &&
		left.UsageJSON == right.UsageJSON && left.UsageSHA256 == right.UsageSHA256 &&
		left.CalculationJSON == right.CalculationJSON && left.CalculationSHA256 == right.CalculationSHA256 &&
		left.AmountCents == right.AmountCents
}
