package model

import (
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/dto"
	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

const (
	PlatformCostReconciliationPending   = "pending"
	PlatformCostReconciliationWaiting   = "waiting"
	PlatformCostReconciliationClaimed   = "claimed"
	PlatformCostReconciliationCompleted = "completed"
)

var (
	ErrPlatformProviderContractRateCollision = errors.New("provider contract rate identity is already used by different evidence")
	ErrPlatformProviderContractRateImmutable = errors.New("provider contract rates are append-only")
	ErrPlatformCostReconciliationClaimLost   = errors.New("channel cost reconciliation claim is no longer current")
)

// PlatformProviderContractRate is an immutable provider-side pricing fact.
// Effective periods are versioned by inserting a new row; the latest
// EffectiveFrom at the provider terminal occurrence wins. Historical rows are
// never edited or derived from customer pricing/new-api quota.
type PlatformProviderContractRate struct {
	ID                   string    `json:"id" gorm:"type:varchar(36);primaryKey"`
	ProviderName         string    `json:"provider_name" gorm:"type:varchar(64);not null;index:idx_platform_contract_rate_lookup,priority:1;uniqueIndex:uniq_platform_contract_rate_scope,priority:1"`
	ChannelID            int       `json:"channel_id" gorm:"not null;index:idx_platform_contract_rate_lookup,priority:2;uniqueIndex:uniq_platform_contract_rate_scope,priority:2"`
	UpstreamModel        string    `json:"upstream_model" gorm:"type:varchar(128);not null;index:idx_platform_contract_rate_lookup,priority:3;uniqueIndex:uniq_platform_contract_rate_scope,priority:3"`
	Mode                 string    `json:"mode" gorm:"type:varchar(32);not null;index:idx_platform_contract_rate_lookup,priority:4;uniqueIndex:uniq_platform_contract_rate_scope,priority:4"`
	Resolution           string    `json:"resolution" gorm:"type:varchar(32);not null;index:idx_platform_contract_rate_lookup,priority:5;uniqueIndex:uniq_platform_contract_rate_scope,priority:5"`
	BillingUnit          string    `json:"billing_unit" gorm:"type:varchar(24);not null"`
	UnitAmountCents      int64     `json:"unit_amount_cents" gorm:"not null"`
	Currency             string    `json:"currency" gorm:"type:char(3);not null"`
	EffectiveFrom        time.Time `json:"effective_from" gorm:"not null;index:idx_platform_contract_rate_lookup,priority:6,sort:desc;uniqueIndex:uniq_platform_contract_rate_scope,priority:6"`
	SourceReference      string    `json:"source_reference" gorm:"type:varchar(240);not null"`
	SourceDocumentSHA256 string    `json:"source_document_sha256" gorm:"type:char(64);not null"`
	CreatedAt            time.Time `json:"created_at"`
}

func (PlatformProviderContractRate) TableName() string {
	return "platform_provider_contract_rates"
}

func (*PlatformProviderContractRate) BeforeUpdate(*gorm.DB) error {
	return ErrPlatformProviderContractRateImmutable
}

func (*PlatformProviderContractRate) BeforeDelete(*gorm.DB) error {
	return ErrPlatformProviderContractRateImmutable
}

// PlatformChannelCostReconciliation is mutable operational queue state, not a
// financial fact. It keeps missing-rate jobs from starving later covered jobs
// and makes multi-worker retries token-fenced. The cost event it eventually
// creates remains immutable in PlatformChannelCostEvent.
type PlatformChannelCostReconciliation struct {
	RelayJobID     string     `json:"relay_job_id" gorm:"type:varchar(36);primaryKey"`
	OutcomeID      string     `json:"outcome_id" gorm:"type:varchar(36);not null;uniqueIndex"`
	State          string     `json:"state" gorm:"type:varchar(16);not null;index:idx_platform_cost_reconcile_available,priority:1"`
	Attempts       int        `json:"attempts" gorm:"not null"`
	NextAttemptAt  time.Time  `json:"next_attempt_at" gorm:"not null;index:idx_platform_cost_reconcile_available,priority:2"`
	ClaimToken     string     `json:"-" gorm:"type:varchar(36);not null;index"`
	ClaimExpiresAt *time.Time `json:"-" gorm:"index"`
	LastErrorCode  string     `json:"last_error_code" gorm:"type:varchar(64);not null"`
	CreatedAt      time.Time  `json:"created_at"`
	UpdatedAt      time.Time  `json:"updated_at"`
}

func (PlatformChannelCostReconciliation) TableName() string {
	return "platform_channel_cost_reconciliations"
}

type PlatformChannelCostReconciliationClaim struct {
	RelayJobID string
	OutcomeID  string
	Token      string
}

type PlatformProviderCostMaterializationFacts struct {
	Outcome       PlatformProviderTerminalOutcome
	Job           PlatformGenerationJob
	Route         PlatformGenerationProviderRoute
	AlreadyCosted bool
}

func SyncPlatformProviderContractRates(inputs []dto.PlatformProviderContractRateInput) error {
	if DB == nil {
		return fmt.Errorf("database is not initialized")
	}
	for _, input := range inputs {
		if err := input.Validate(); err != nil {
			return err
		}
		var routeCount int64
		if err := DB.Model(&PlatformGenerationProviderRoute{}).Where(
			"channel_id = ? AND provider_name = ? AND upstream_model = ? AND mode = ?",
			input.ChannelID, input.ProviderName, input.UpstreamModel, input.Mode,
		).Count(&routeCount).Error; err != nil {
			return err
		}
		if routeCount == 0 {
			return fmt.Errorf("contract rate %s does not match a configured provider route", input.ID)
		}
		rate := PlatformProviderContractRate{
			ID:                   input.ID,
			ProviderName:         input.ProviderName,
			ChannelID:            input.ChannelID,
			UpstreamModel:        input.UpstreamModel,
			Mode:                 input.Mode,
			Resolution:           input.Resolution,
			BillingUnit:          input.BillingUnit,
			UnitAmountCents:      input.UnitAmountCents,
			Currency:             input.Currency,
			EffectiveFrom:        input.EffectiveFrom.UTC(),
			SourceReference:      input.SourceReference,
			SourceDocumentSHA256: input.SourceDocumentSHA256,
		}
		created := false
		err := DB.Transaction(func(tx *gorm.DB) error {
			now, err := GetDBTimeTx(tx)
			if err != nil {
				return err
			}
			rate.CreatedAt = now
			result := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&rate)
			if result.Error != nil {
				return result.Error
			}
			created = result.RowsAffected == 1
			if created {
				return nil
			}
			var existing PlatformProviderContractRate
			query := tx.Where("id = ?", rate.ID).First(&existing)
			if errors.Is(query.Error, gorm.ErrRecordNotFound) {
				query = tx.Where(
					"provider_name = ? AND channel_id = ? AND upstream_model = ? AND mode = ? AND resolution = ? AND effective_from = ?",
					rate.ProviderName, rate.ChannelID, rate.UpstreamModel, rate.Mode, rate.Resolution, rate.EffectiveFrom,
				).First(&existing)
			}
			if query.Error != nil {
				return query.Error
			}
			if !platformProviderContractRatesEqual(existing, rate) {
				return ErrPlatformProviderContractRateCollision
			}
			return nil
		})
		if err != nil {
			return err
		}
	}
	return nil
}

func DiscoverPlatformChannelCostReconciliations(limit int) (int, error) {
	if limit < 1 || limit > 10_000 {
		return 0, fmt.Errorf("channel cost reconciliation discovery limit is invalid")
	}
	var outcomes []PlatformProviderTerminalOutcome
	if err := DB.Table("platform_provider_terminal_outcomes AS outcomes").
		Select("outcomes.*").
		Joins("LEFT JOIN platform_channel_cost_reconciliations AS queue ON queue.relay_job_id = outcomes.relay_job_id").
		Where("outcomes.outcome = ? AND outcomes.relay_job_id <> ? AND queue.relay_job_id IS NULL", PlatformProviderOutcomeSucceeded, "").
		Order("outcomes.occurred_at ASC, outcomes.id ASC").
		Limit(limit).
		Find(&outcomes).Error; err != nil {
		return 0, err
	}
	if len(outcomes) == 0 {
		return 0, nil
	}
	created := 0
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		for _, outcome := range outcomes {
			row := PlatformChannelCostReconciliation{
				RelayJobID:    outcome.RelayJobID,
				OutcomeID:     outcome.ID,
				State:         PlatformCostReconciliationPending,
				NextAttemptAt: now,
				CreatedAt:     now,
				UpdatedAt:     now,
			}
			result := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&row)
			if result.Error != nil {
				return result.Error
			}
			created += int(result.RowsAffected)
		}
		return nil
	})
	return created, err
}

func ClaimPlatformChannelCostReconciliation(lease time.Duration) (*PlatformChannelCostReconciliationClaim, error) {
	if lease < time.Second || lease > 24*time.Hour {
		return nil, fmt.Errorf("channel cost reconciliation lease is invalid")
	}
	var claim *PlatformChannelCostReconciliationClaim
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		var row PlatformChannelCostReconciliation
		query := lockForUpdate(tx.Where(
			"((state = ? OR state = ?) AND next_attempt_at <= ?) OR (state = ? AND claim_expires_at <= ?)",
			PlatformCostReconciliationPending,
			PlatformCostReconciliationWaiting,
			now,
			PlatformCostReconciliationClaimed,
			now,
		)).Order("next_attempt_at ASC, created_at ASC, relay_job_id ASC").First(&row)
		if query.Error != nil {
			return query.Error
		}
		token := uuid.NewString()
		expiresAt := now.Add(lease)
		result := tx.Model(&PlatformChannelCostReconciliation{}).
			Where("relay_job_id = ? AND updated_at = ?", row.RelayJobID, row.UpdatedAt).
			Updates(map[string]any{
				"state":            PlatformCostReconciliationClaimed,
				"claim_token":      token,
				"claim_expires_at": expiresAt,
				"updated_at":       now,
			})
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return ErrPlatformCostReconciliationClaimLost
		}
		claim = &PlatformChannelCostReconciliationClaim{RelayJobID: row.RelayJobID, OutcomeID: row.OutcomeID, Token: token}
		return nil
	})
	return claim, err
}

func CompletePlatformChannelCostReconciliation(claim PlatformChannelCostReconciliationClaim) (bool, error) {
	return finishPlatformChannelCostReconciliation(claim, PlatformCostReconciliationCompleted, "", 0)
}

func DeferPlatformChannelCostReconciliation(
	claim PlatformChannelCostReconciliationClaim,
	errorCode string,
	delay time.Duration,
) (bool, error) {
	if errorCode == "" || strings.TrimSpace(errorCode) != errorCode || len(errorCode) > 64 || delay < time.Second || delay > 24*time.Hour {
		return false, fmt.Errorf("channel cost reconciliation deferral is invalid")
	}
	return finishPlatformChannelCostReconciliation(claim, PlatformCostReconciliationWaiting, errorCode, delay)
}

func finishPlatformChannelCostReconciliation(
	claim PlatformChannelCostReconciliationClaim,
	state string,
	errorCode string,
	delay time.Duration,
) (bool, error) {
	if parsed, err := uuid.Parse(claim.RelayJobID); err != nil || parsed.String() != claim.RelayJobID ||
		claim.OutcomeID == "" || claim.Token == "" {
		return false, fmt.Errorf("channel cost reconciliation claim is invalid")
	}
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		updates := map[string]any{
			"state":            state,
			"claim_token":      "",
			"claim_expires_at": nil,
			"last_error_code":  errorCode,
			"attempts":         gorm.Expr("attempts + 1"),
			"updated_at":       now,
		}
		if state == PlatformCostReconciliationWaiting {
			updates["next_attempt_at"] = now.Add(delay)
		} else {
			updates["next_attempt_at"] = now
		}
		result := tx.Model(&PlatformChannelCostReconciliation{}).Where(
			"relay_job_id = ? AND outcome_id = ? AND state = ? AND claim_token = ? AND claim_expires_at > ?",
			claim.RelayJobID, claim.OutcomeID, PlatformCostReconciliationClaimed, claim.Token, now,
		).Updates(updates)
		if result.Error != nil {
			return result.Error
		}
		won = result.RowsAffected == 1
		return nil
	})
	return won, err
}

func GetPlatformProviderCostMaterializationFacts(relayJobID string) (PlatformProviderCostMaterializationFacts, error) {
	var facts PlatformProviderCostMaterializationFacts
	if parsed, err := uuid.Parse(relayJobID); err != nil || parsed.String() != relayJobID {
		return facts, fmt.Errorf("Relay job id is invalid")
	}
	if err := DB.Where("relay_job_id = ? AND outcome = ?", relayJobID, PlatformProviderOutcomeSucceeded).
		Order("occurred_at ASC, id ASC").First(&facts.Outcome).Error; err != nil {
		return facts, err
	}
	if err := DB.Where("id = ?", relayJobID).First(&facts.Job).Error; err != nil {
		return facts, err
	}
	if err := DB.Where("id = ?", facts.Outcome.RouteID).First(&facts.Route).Error; err != nil {
		return facts, err
	}
	if facts.Job.ProviderRouteID != facts.Route.ID || facts.Job.ID != facts.Outcome.RelayJobID {
		return facts, fmt.Errorf("provider cost route evidence is inconsistent")
	}
	var count int64
	if err := DB.Model(&PlatformChannelCostEvent{}).Where("relay_job_id = ?", relayJobID).Count(&count).Error; err != nil {
		return facts, err
	}
	facts.AlreadyCosted = count > 0
	return facts, nil
}

func FindPlatformProviderContractRate(
	facts PlatformProviderCostMaterializationFacts,
	resolution string,
) (*PlatformProviderContractRate, error) {
	var rate PlatformProviderContractRate
	err := DB.Where(
		"provider_name = ? AND channel_id = ? AND upstream_model = ? AND mode = ? AND resolution = ? AND effective_from <= ?",
		facts.Route.ProviderName,
		facts.Route.ChannelID,
		facts.Route.UpstreamModel,
		facts.Job.Mode,
		resolution,
		facts.Outcome.OccurredAt.UTC(),
	).Order("effective_from DESC, id DESC").First(&rate).Error
	return &rate, err
}

func platformProviderContractRatesEqual(left PlatformProviderContractRate, right PlatformProviderContractRate) bool {
	return left.ID == right.ID && left.ProviderName == right.ProviderName && left.ChannelID == right.ChannelID &&
		left.UpstreamModel == right.UpstreamModel && left.Mode == right.Mode && left.Resolution == right.Resolution &&
		left.BillingUnit == right.BillingUnit && left.UnitAmountCents == right.UnitAmountCents && left.Currency == right.Currency &&
		left.EffectiveFrom.UTC().Equal(right.EffectiveFrom.UTC()) && left.SourceReference == right.SourceReference &&
		left.SourceDocumentSHA256 == right.SourceDocumentSHA256
}
