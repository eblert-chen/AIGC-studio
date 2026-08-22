package service

import (
	"context"
	"errors"
	"fmt"
	"math"
	"os"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"gorm.io/gorm"
)

const (
	platformChannelCostReconciliationDiscoveryBatch = 500
	platformChannelCostMissingRateDelay             = 5 * time.Minute
	platformChannelCostInvalidEvidenceDelay         = 30 * time.Minute
)

type PlatformChannelCostReconciliationResult struct {
	Materialized bool
	Completed    bool
	DeferredCode string
}

// ReconcilePlatformChannelCostClaim materializes exactly one evidence-backed
// cost. Missing/unsupported evidence is durable waiting state; it never blocks
// or mutates the already accepted generation job.
func ReconcilePlatformChannelCostClaim(
	claim model.PlatformChannelCostReconciliationClaim,
) (PlatformChannelCostReconciliationResult, error) {
	result := PlatformChannelCostReconciliationResult{}
	facts, err := model.GetPlatformProviderCostMaterializationFacts(claim.RelayJobID)
	if err != nil {
		return deferPlatformChannelCostClaim(claim, "cost_facts_unavailable", platformChannelCostInvalidEvidenceDelay, err)
	}
	if facts.Outcome.ID != claim.OutcomeID || facts.Outcome.Outcome != model.PlatformProviderOutcomeSucceeded {
		return deferPlatformChannelCostClaim(claim, "provider_outcome_mismatch", platformChannelCostInvalidEvidenceDelay, nil)
	}
	if facts.AlreadyCosted {
		won, err := model.CompletePlatformChannelCostReconciliation(claim)
		if err != nil {
			return result, err
		}
		if !won {
			return result, model.ErrPlatformCostReconciliationClaimLost
		}
		result.Completed = true
		return result, nil
	}
	environment := strings.ToLower(strings.TrimSpace(os.Getenv("RELAY_COMPAT_ENVIRONMENT")))
	if ready, code := platformGenerationProviderRouteReadiness(facts.Route, environment); !ready {
		return deferPlatformChannelCostClaim(claim, code, platformChannelCostInvalidEvidenceDelay, nil)
	}

	request := dto.NewPlatformGenerationRequest()
	if err := common.Unmarshal([]byte(facts.Job.RequestJSON), &request); err != nil || request.Validate() != nil {
		return deferPlatformChannelCostClaim(claim, "request_snapshot_invalid", platformChannelCostInvalidEvidenceDelay, err)
	}
	if request.Model != facts.Job.Model || request.Mode != facts.Job.Mode || request.Output.Count < 1 {
		return deferPlatformChannelCostClaim(claim, "billing_quantity_unproven", platformChannelCostInvalidEvidenceDelay, nil)
	}
	rate, err := model.FindPlatformProviderContractRate(facts, request.Output.Resolution)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return deferPlatformChannelCostClaim(claim, "contract_rate_missing", platformChannelCostMissingRateDelay, nil)
	}
	if err != nil {
		return deferPlatformChannelCostClaim(claim, "contract_rate_lookup_failed", platformChannelCostMissingRateDelay, err)
	}
	amount, err := calculatePlatformProviderContractCost(*rate, request.Output)
	if err != nil {
		return deferPlatformChannelCostClaim(claim, "billing_quantity_unsupported", platformChannelCostInvalidEvidenceDelay, err)
	}

	companyID, taskID, linkageErr := platformProviderCostLinkage(facts.Job, request)
	if linkageErr != nil {
		return deferPlatformChannelCostClaim(claim, "platform_linkage_invalid", platformChannelCostInvalidEvidenceDelay, linkageErr)
	}
	eventID := uuid.NewSHA1(uuid.NameSpaceURL, []byte("relay-contract-cost:"+facts.Outcome.ID+":"+rate.ID)).String()
	input := dto.PlatformChannelCostInput{
		EventID:              eventID,
		AmountCents:          amount,
		IdempotencyKey:       "relay-contract-cost-" + facts.Outcome.ID + "-" + rate.ID,
		ChannelKey:           facts.Route.RouteKey,
		ChannelType:          facts.Route.ChannelClass,
		OccurredAt:           facts.Outcome.OccurredAt.UTC(),
		ExternalReference:    facts.Outcome.ExternalReference,
		CompanyID:            companyID,
		TaskID:               taskID,
		RelayJobID:           facts.Job.ID,
		Note:                 "provider contract rate " + rate.ID,
		EvidenceSource:       dto.PlatformChannelCostEvidenceContractRate,
		EvidenceReference:    rate.SourceReference,
		SourceDocumentSHA256: rate.SourceDocumentSHA256,
	}
	created, err := EnqueuePlatformChannelCost(input)
	if err != nil {
		return deferPlatformChannelCostClaim(claim, "cost_event_enqueue_failed", platformChannelCostMissingRateDelay, err)
	}
	won, err := model.CompletePlatformChannelCostReconciliation(claim)
	if err != nil {
		return result, err
	}
	if !won {
		return result, model.ErrPlatformCostReconciliationClaimLost
	}
	result.Materialized = created
	result.Completed = true
	return result, nil
}

func calculatePlatformProviderContractCost(
	rate model.PlatformProviderContractRate,
	output dto.PlatformGenerationOutputOptions,
) (int64, error) {
	if rate.UnitAmountCents <= 0 || output.Count < 1 {
		return 0, fmt.Errorf("provider contract billing quantity is invalid")
	}
	quantity := int64(output.Count)
	if rate.BillingUnit == dto.PlatformContractRateUnitOutputSecond {
		if output.DurationSeconds < 1 {
			return 0, fmt.Errorf("provider contract billing duration is invalid")
		}
		if int64(output.DurationSeconds) > math.MaxInt64/quantity {
			return 0, fmt.Errorf("provider contract billing quantity overflows")
		}
		quantity *= int64(output.DurationSeconds)
	} else if rate.BillingUnit != dto.PlatformContractRateUnitOutputItem {
		return 0, fmt.Errorf("provider contract billing unit is unsupported")
	}
	if quantity <= 0 || rate.UnitAmountCents > dto.PlatformChannelCostMaxAmountCents/quantity {
		return 0, fmt.Errorf("provider contract cost is outside the supported range")
	}
	amount := rate.UnitAmountCents * quantity
	if amount <= 0 || amount > dto.PlatformChannelCostMaxAmountCents {
		return 0, fmt.Errorf("provider contract cost is outside the supported range")
	}
	return amount, nil
}

func platformProviderCostLinkage(
	job model.PlatformGenerationJob,
	request dto.PlatformGenerationRequest,
) (string, string, error) {
	companyValue, hasCompany := request.Metadata["platform_company_id"]
	taskValue, hasTask := request.Metadata["platform_task_id"]
	if !hasCompany && !hasTask {
		return "", "", nil
	}
	companyID, companyOK := companyValue.(string)
	taskID, taskOK := taskValue.(string)
	if !hasCompany || !hasTask || !companyOK || !taskOK || companyID == "" || taskID == "" ||
		strings.TrimSpace(companyID) != companyID || strings.TrimSpace(taskID) != taskID ||
		len(companyID) > 64 || len(taskID) > 64 || request.ClientReferenceID == nil ||
		*request.ClientReferenceID != taskID || job.ClientReferenceID == nil || *job.ClientReferenceID != taskID {
		return "", "", fmt.Errorf("Platform task metadata cannot be proven")
	}
	return companyID, taskID, nil
}

func deferPlatformChannelCostClaim(
	claim model.PlatformChannelCostReconciliationClaim,
	code string,
	delay time.Duration,
	cause error,
) (PlatformChannelCostReconciliationResult, error) {
	result := PlatformChannelCostReconciliationResult{DeferredCode: code}
	won, err := model.DeferPlatformChannelCostReconciliation(claim, code, delay)
	if err != nil {
		return result, err
	}
	if !won {
		return result, model.ErrPlatformCostReconciliationClaimLost
	}
	// A missing rate or unsupported contract is an expected reconciliation gap,
	// not a worker crash. Return the operational result while readiness stays
	// false through the missing immutable cost event.
	if cause != nil {
		return result, fmt.Errorf("%s: %w", code, cause)
	}
	return result, nil
}

// RunPlatformChannelCostReconciliationOnce exposes one complete
// discover/claim/reconcile cycle for deterministic worker tests and bounded
// operational invocations. The long-running worker below applies the same
// model repeatedly.
func RunPlatformChannelCostReconciliationOnce(ctx context.Context, lease time.Duration) (bool, error) {
	if ctx == nil {
		return false, fmt.Errorf("channel cost reconciliation context is required")
	}
	if err := ctx.Err(); err != nil {
		return false, err
	}
	if _, err := model.DiscoverPlatformChannelCostReconciliations(platformChannelCostReconciliationDiscoveryBatch); err != nil {
		return false, err
	}
	claim, err := model.ClaimPlatformChannelCostReconciliation(lease)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	_, err = ReconcilePlatformChannelCostClaim(*claim)
	return true, err
}

func runPlatformChannelCostReconciliationWorker(
	ctx context.Context,
	lease time.Duration,
	poll time.Duration,
) {
	for {
		if err := ctx.Err(); err != nil {
			return
		}
		if _, err := model.DiscoverPlatformChannelCostReconciliations(platformChannelCostReconciliationDiscoveryBatch); err != nil {
			common.SysError("platform channel cost reconciliation discovery failed: " + err.Error())
			if !waitPlatformProviderMonitor(ctx, poll) {
				return
			}
			continue
		}
		claim, err := model.ClaimPlatformChannelCostReconciliation(lease)
		if errors.Is(err, gorm.ErrRecordNotFound) {
			if !waitPlatformProviderMonitor(ctx, poll) {
				return
			}
			continue
		}
		if err != nil {
			common.SysError("platform channel cost reconciliation claim failed: " + err.Error())
			if !waitPlatformProviderMonitor(ctx, poll) {
				return
			}
			continue
		}
		result, err := ReconcilePlatformChannelCostClaim(*claim)
		if err != nil {
			common.SysError("platform channel cost reconciliation failed: " + err.Error())
		} else if result.DeferredCode != "" {
			common.SysLog("platform channel cost reconciliation deferred: " + result.DeferredCode)
		}
	}
}
