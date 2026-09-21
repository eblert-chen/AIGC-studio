package service

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"math"
	"os"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"gorm.io/gorm"
)

const (
	platformChannelCostReconciliationDiscoveryBatch = 500
	platformChannelCostMissingRateDelay             = 5 * time.Minute
	platformChannelCostInvalidEvidenceDelay         = 30 * time.Minute
	platformChannelCostTokenUsageEvidenceRequired   = "provider_token_usage_evidence_required"
	platformProviderCostRateSetNotePrefix           = "provider cost rate set "
)

var errPlatformChannelCostClaimNeedsDeferral = errors.New("channel cost claim requires durable deferral")

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
	deferredCode := ""
	deferredDelay := time.Duration(0)
	var deferredCause error
	deferClaim := func(code string, delay time.Duration, cause error) error {
		deferredCode = code
		deferredDelay = delay
		deferredCause = cause
		return errPlatformChannelCostClaimNeedsDeferral
	}

	err := model.DB.Transaction(func(tx *gorm.DB) error {
		facts, existing, err := model.LockPlatformProviderCostMaterializationClaimTx(tx, claim)
		if err != nil {
			return err
		}
		environment := strings.ToLower(strings.TrimSpace(os.Getenv("RELAY_COMPAT_ENVIRONMENT")))
		if ready, code := platformGenerationProviderRouteReadiness(facts.Route, environment); !ready {
			return deferClaim(code, platformChannelCostInvalidEvidenceDelay, nil)
		}

		request := dto.NewPlatformGenerationRequest()
		if err := common.Unmarshal([]byte(facts.Job.RequestJSON), &request); err != nil {
			return deferClaim("request_snapshot_invalid", platformChannelCostInvalidEvidenceDelay, err)
		}
		if err := request.Validate(); err != nil {
			return deferClaim("request_snapshot_invalid", platformChannelCostInvalidEvidenceDelay, err)
		}
		if request.Model != facts.Job.Model || request.Mode != facts.Job.Mode || request.Output.Count < 1 {
			return deferClaim("billing_quantity_unproven", platformChannelCostInvalidEvidenceDelay, nil)
		}
		rateSetID := ""
		if existing != nil && strings.HasPrefix(existing.Note, platformProviderCostRateSetNotePrefix) {
			var parseErr error
			rateSetID, _, parseErr = platformProviderCostRateSetIdentityFromCostEvent(*existing)
			if parseErr != nil {
				return model.ErrPlatformChannelCostEventCollision
			}
		}
		rateSet, err := findPlatformProviderCostRateSetTx(tx, facts, request.Output.Resolution, rateSetID)
		if err != nil {
			return deferClaim("provider_cost_rate_set_lookup_failed", platformChannelCostMissingRateDelay, err)
		}
		if rateSet != nil {
			receiptEvidence, err := model.LoadPlatformGenerationProviderCostReceiptEvidenceTx(tx, facts.Job, facts.Route)
			if err != nil {
				return deferClaim(platformChannelCostTokenUsageEvidenceRequired, platformChannelCostInvalidEvidenceDelay, err)
			}
			usage := platformProviderCostUsageFromReceipt(receiptEvidence.Receipt)
			if err := validatePlatformProviderCostCoverage(*rateSet, receiptEvidence.Receipt, usage); err != nil {
				return deferClaim("provider_cost_formula_incomplete", platformChannelCostInvalidEvidenceDelay, err)
			}
			calculation, err := dto.CalculatePlatformProviderCost(*rateSet, usage)
			if err != nil {
				return deferClaim("provider_cost_usage_invalid", platformChannelCostInvalidEvidenceDelay, err)
			}
			companyID, personalWorkspaceID, taskID, linkageErr := platformProviderCostLinkage(facts.Job, request)
			if linkageErr != nil {
				return deferClaim("platform_linkage_invalid", platformChannelCostInvalidEvidenceDelay, linkageErr)
			}
			eventID, idempotencyKey := platformContractCostIdentity(facts.Outcome.ID)
			if existing != nil && (existing.ID != eventID || existing.IdempotencyKey != idempotencyKey) {
				return model.ErrPlatformChannelCostEventCollision
			}
			note := platformProviderCostRateSetNote(*rateSet, receiptEvidence.Receipt.ProofSHA256)
			input := dto.PlatformChannelCostInput{
				EventID: eventID, AmountCents: calculation.AmountCents, IdempotencyKey: idempotencyKey,
				ChannelKey: facts.Route.RouteKey, ChannelType: facts.Route.ChannelClass,
				OccurredAt: facts.Outcome.OccurredAt.UTC(), ExternalReference: facts.Outcome.ExternalReference,
				CompanyID: companyID, PersonalWorkspaceID: personalWorkspaceID, TaskID: taskID,
				RelayJobID: facts.Job.ID, Note: note,
				EvidenceSource:    dto.PlatformChannelCostEvidenceContractRate,
				EvidenceReference: rateSet.SourceReference, SourceDocumentSHA256: rateSet.SourceDocumentSHA256,
			}
			created, err := enqueuePlatformChannelCostTx(tx, input)
			if err != nil {
				return err
			}
			allocation, err := newPlatformProviderCostAllocationEvidence(
				eventID, facts, *rateSet, receiptEvidence, usage, calculation,
			)
			if err != nil {
				return err
			}
			if _, err := model.CreatePlatformProviderCostAllocationEvidenceTx(tx, &allocation); err != nil {
				return err
			}
			won, err := model.CompletePlatformChannelCostReconciliationTx(tx, claim)
			if err != nil {
				return err
			}
			if !won {
				return model.ErrPlatformCostReconciliationClaimLost
			}
			result.Materialized, result.Completed = created, true
			return nil
		}
		if rateSetID != "" {
			return deferClaim("provider_cost_rate_set_missing", platformChannelCostMissingRateDelay, nil)
		}
		// Token-priced providers must never fall back to the legacy single-unit
		// request estimate when no complete, actual-usage rate set is available.
		if dto.PlatformContractRateRequiresTokenUsageEvidence(facts.Route.UpstreamModel) {
			return deferClaim(platformChannelCostTokenUsageEvidenceRequired, platformChannelCostInvalidEvidenceDelay, nil)
		}

		var rate *model.PlatformProviderContractRate
		if existing != nil {
			rateID, parseErr := platformContractRateIDFromCostEvent(*existing)
			if parseErr != nil {
				return model.ErrPlatformChannelCostEventCollision
			}
			rate, err = model.GetPlatformProviderContractRateTx(tx, rateID)
		} else {
			rate, err = model.FindPlatformProviderContractRateTx(tx, facts, request.Output.Resolution)
		}
		if errors.Is(err, model.ErrPlatformProviderContractRateUsageEvidenceRequired) {
			return deferClaim(platformChannelCostTokenUsageEvidenceRequired, platformChannelCostInvalidEvidenceDelay, nil)
		}
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return deferClaim("contract_rate_missing", platformChannelCostMissingRateDelay, nil)
		}
		if err != nil {
			return deferClaim("contract_rate_lookup_failed", platformChannelCostMissingRateDelay, err)
		}
		if err := validatePlatformContractRateForFacts(*rate, facts, request.Output.Resolution); err != nil {
			return model.ErrPlatformChannelCostEventCollision
		}
		amount, err := calculatePlatformProviderContractCost(*rate, request.Output)
		if err != nil {
			return deferClaim("billing_quantity_unsupported", platformChannelCostInvalidEvidenceDelay, err)
		}
		companyID, personalWorkspaceID, taskID, linkageErr := platformProviderCostLinkage(facts.Job, request)
		if linkageErr != nil {
			return deferClaim("platform_linkage_invalid", platformChannelCostInvalidEvidenceDelay, linkageErr)
		}

		eventID, idempotencyKey := platformContractCostIdentity(facts.Outcome.ID)
		if existing != nil {
			legacyID, legacyKey := platformLegacyContractCostIdentity(facts.Outcome.ID, rate.ID)
			switch {
			case existing.ID == eventID && existing.IdempotencyKey == idempotencyKey:
			case existing.ID == legacyID && existing.IdempotencyKey == legacyKey:
				eventID, idempotencyKey = legacyID, legacyKey
			default:
				return model.ErrPlatformChannelCostEventCollision
			}
		}
		input := dto.PlatformChannelCostInput{
			EventID:              eventID,
			AmountCents:          amount,
			IdempotencyKey:       idempotencyKey,
			ChannelKey:           facts.Route.RouteKey,
			ChannelType:          facts.Route.ChannelClass,
			OccurredAt:           facts.Outcome.OccurredAt.UTC(),
			ExternalReference:    facts.Outcome.ExternalReference,
			CompanyID:            companyID,
			PersonalWorkspaceID:  personalWorkspaceID,
			TaskID:               taskID,
			RelayJobID:           facts.Job.ID,
			Note:                 "provider contract rate " + rate.ID,
			EvidenceSource:       dto.PlatformChannelCostEvidenceContractRate,
			EvidenceReference:    rate.SourceReference,
			SourceDocumentSHA256: rate.SourceDocumentSHA256,
		}
		created, err := enqueuePlatformChannelCostTx(tx, input)
		if err != nil {
			return err
		}
		won, err := model.CompletePlatformChannelCostReconciliationTx(tx, claim)
		if err != nil {
			return err
		}
		if !won {
			return model.ErrPlatformCostReconciliationClaimLost
		}
		result.Materialized = created
		result.Completed = true
		return nil
	})
	if errors.Is(err, errPlatformChannelCostClaimNeedsDeferral) {
		return deferPlatformChannelCostClaim(claim, deferredCode, deferredDelay, deferredCause)
	}
	return result, err
}

func platformContractCostIdentity(outcomeID string) (string, string) {
	return uuid.NewSHA1(uuid.NameSpaceURL, []byte("relay-contract-cost:"+outcomeID)).String(),
		"relay-contract-cost-" + outcomeID
}

func platformProviderCostRateSetNote(rateSet dto.PlatformProviderCostRateSet, receiptProofSHA256 string) string {
	return platformProviderCostRateSetNotePrefix + rateSet.ID + " receipt " + receiptProofSHA256
}

func platformProviderCostRateSetIdentityFromCostEvent(event model.PlatformChannelCostEvent) (string, string, error) {
	if event.EvidenceSource != dto.PlatformChannelCostEvidenceContractRate ||
		!strings.HasPrefix(event.Note, platformProviderCostRateSetNotePrefix) {
		return "", "", model.ErrPlatformChannelCostEventCollision
	}
	parts := strings.Split(strings.TrimPrefix(event.Note, platformProviderCostRateSetNotePrefix), " receipt ")
	if len(parts) != 2 {
		return "", "", model.ErrPlatformChannelCostEventCollision
	}
	if parsed, err := uuid.Parse(parts[0]); err != nil || parsed.String() != parts[0] ||
		!strings.HasPrefix(parts[1], "sha256:") || len(parts[1]) != 71 {
		return "", "", model.ErrPlatformChannelCostEventCollision
	}
	if _, err := hex.DecodeString(strings.TrimPrefix(parts[1], "sha256:")); err != nil {
		return "", "", model.ErrPlatformChannelCostEventCollision
	}
	return parts[0], parts[1], nil
}

func findPlatformProviderCostRateSetTx(
	tx *gorm.DB,
	facts model.PlatformProviderCostMaterializationFacts,
	resolution string,
	exactID string,
) (*dto.PlatformProviderCostRateSet, error) {
	configuration, err := getPlatformProviderRuntimeConfiguration()
	if err != nil || len(configuration.CostRateSets) == 0 {
		return nil, err
	}
	var channel model.Channel
	if tx == nil {
		return nil, fmt.Errorf("provider cost rate-set transaction is required")
	}
	if err := tx.Select("id", "type", "base_url").Where("id = ?", facts.Route.ChannelID).First(&channel).Error; err != nil {
		return nil, err
	}
	origin, err := platformProviderRateSetOrigin(channel.GetBaseURL())
	if err != nil {
		return nil, err
	}
	var selected *dto.PlatformProviderCostRateSet
	for index := range configuration.CostRateSets {
		candidate := configuration.CostRateSets[index]
		if exactID != "" && candidate.ID != exactID {
			continue
		}
		if candidate.ProviderName != facts.Route.ProviderName || candidate.ProviderOrigin != origin ||
			candidate.ChannelID != facts.Route.ChannelID || candidate.UpstreamModel != facts.Route.UpstreamModel ||
			candidate.Mode != facts.Job.Mode || candidate.Resolution != resolution ||
			candidate.EffectiveFrom.After(facts.Outcome.OccurredAt.UTC()) {
			continue
		}
		if selected == nil || candidate.EffectiveFrom.After(selected.EffectiveFrom) {
			copy := candidate
			selected = &copy
		}
	}
	return selected, nil
}

func platformProviderCostUsageFromReceipt(receipt model.PlatformGenerationProviderResultReceipt) dto.PlatformProviderCostUsage {
	outputSeconds := receipt.OutputSeconds
	if outputSeconds == 0 && (receipt.Protocol == generationprofile.GoogleGeminiVeoVideoProtocolV1 ||
		receipt.Protocol == generationprofile.GoogleVertexVeoVideoProtocolV1) {
		outputSeconds = receipt.DurationSeconds
	}
	return dto.PlatformProviderCostUsage{
		InputTokens: int64(receipt.InputTokens), CachedInputTokens: int64(receipt.CachedTokens),
		OutputTokens: int64(receipt.OutputTokens), OutputTextTokens: int64(receipt.OutputTextTokens),
		OutputVideoTokens: int64(receipt.OutputVideoTokens), ThoughtTokens: int64(receipt.ThoughtTokens),
		TotalTokens: int64(receipt.TotalTokens), OutputSeconds: int64(outputSeconds),
		InputSeconds: int64(receipt.InputSeconds), OutputItems: int64(receipt.OutputCount),
		InputImageCount: int64(receipt.InputImageCount),
	}
}

func validatePlatformProviderCostCoverage(
	rateSet dto.PlatformProviderCostRateSet,
	receipt model.PlatformGenerationProviderResultReceipt,
	usage dto.PlatformProviderCostUsage,
) error {
	if err := usage.Validate(); err != nil {
		return err
	}
	metrics := make(map[string]dto.PlatformProviderCostRateComponent, len(rateSet.Components))
	for _, component := range rateSet.Components {
		metrics[component.Metric] = component
	}
	require := func(metric string) error {
		if _, ok := metrics[metric]; !ok {
			return fmt.Errorf("provider cost rate set is missing %s", metric)
		}
		return nil
	}
	currentRevision, supported := model.PlatformGenerationProviderResultProofRevision(receipt.Protocol)
	if !supported || receipt.ProofContractRevision != currentRevision {
		return fmt.Errorf("provider cost receipt uses a legacy proof revision")
	}
	switch receipt.Protocol {
	case generationprofile.GoogleGeminiInteractionsVideoProtocolV1:
		if usage.OutputVideoTokens <= 0 || usage.OutputTextTokens+usage.OutputVideoTokens != usage.OutputTokens {
			return fmt.Errorf("Gemini Omni output modality usage is incomplete")
		}
		if err := require(dto.PlatformProviderCostMetricOutputVideoToken); err != nil {
			return err
		}
		if usage.OutputTextTokens > 0 {
			if err := require(dto.PlatformProviderCostMetricOutputTextToken); err != nil {
				return err
			}
		}
		if usage.ThoughtTokens > 0 {
			if err := require(dto.PlatformProviderCostMetricThoughtToken); err != nil {
				return err
			}
		}
		if usage.CachedInputTokens > 0 {
			if err := require(dto.PlatformProviderCostMetricUncachedInputToken); err != nil {
				return err
			}
			if err := require(dto.PlatformProviderCostMetricCachedInputToken); err != nil {
				return err
			}
		} else if _, generic := metrics[dto.PlatformProviderCostMetricInputToken]; !generic {
			if err := require(dto.PlatformProviderCostMetricUncachedInputToken); err != nil {
				return err
			}
		}
	case generationprofile.VolcengineArkVideoProtocolV1:
		if usage.TotalTokens <= 0 {
			return fmt.Errorf("Ark total-token usage is missing")
		}
		return require(dto.PlatformProviderCostMetricTotalToken)
	case generationprofile.MiniMaxH3VideoProtocolV2:
		if usage.OutputSeconds <= 0 {
			return fmt.Errorf("MiniMax H3 output-second usage is missing")
		}
		if err := require(dto.PlatformProviderCostMetricOutputSecond); err != nil {
			return err
		}
		if receipt.Mode == "video_to_video" {
			if usage.InputSeconds <= 0 {
				return fmt.Errorf("MiniMax H3 input-second usage is missing")
			}
			if err := require(dto.PlatformProviderCostMetricInputSecond); err != nil {
				return err
			}
		}
		if usage.InputImageCount > 0 {
			component, ok := metrics[dto.PlatformProviderCostMetricInputImageAboveFree]
			if !ok || component.FreeUnits != 5 {
				return fmt.Errorf("MiniMax H3 image pricing is incomplete")
			}
		}
	case generationprofile.GoogleGeminiVeoVideoProtocolV1,
		generationprofile.GoogleVertexVeoVideoProtocolV1:
		if usage.OutputSeconds <= 0 {
			return fmt.Errorf("Google Veo successful output duration is missing")
		}
		return require(dto.PlatformProviderCostMetricOutputSecond)
	default:
		return fmt.Errorf("provider cost receipt protocol is unsupported")
	}
	return nil
}

func newPlatformProviderCostAllocationEvidence(
	eventID string,
	facts model.PlatformProviderCostMaterializationFacts,
	rateSet dto.PlatformProviderCostRateSet,
	receipt model.PlatformGenerationProviderCostReceiptEvidence,
	usage dto.PlatformProviderCostUsage,
	calculation dto.PlatformProviderCostCalculation,
) (model.PlatformProviderCostAllocationEvidence, error) {
	var evidence model.PlatformProviderCostAllocationEvidence
	rateSetJSON, err := common.Marshal(rateSet)
	if err != nil {
		return evidence, err
	}
	usageJSON, err := common.Marshal(usage)
	if err != nil {
		return evidence, err
	}
	calculationJSON, err := common.Marshal(calculation)
	if err != nil {
		return evidence, err
	}
	digest := func(value []byte) string {
		sum := sha256.Sum256(value)
		return hex.EncodeToString(sum[:])
	}
	evidence = model.PlatformProviderCostAllocationEvidence{
		CostEventID: eventID, RelayJobID: facts.Job.ID, OutcomeID: facts.Outcome.ID,
		RateSetID: rateSet.ID, RateSetJSON: string(rateSetJSON), RateSetSHA256: digest(rateSetJSON),
		ReceiptSHA256: receipt.ReceiptSHA256, ReceiptProofSHA256: receipt.Receipt.ProofSHA256,
		UsageJSON: string(usageJSON), UsageSHA256: digest(usageJSON),
		CalculationJSON: string(calculationJSON), CalculationSHA256: digest(calculationJSON),
		AmountCents: calculation.AmountCents,
	}
	return evidence, nil
}

func platformLegacyContractCostIdentity(outcomeID string, rateID string) (string, string) {
	return uuid.NewSHA1(uuid.NameSpaceURL, []byte("relay-contract-cost:"+outcomeID+":"+rateID)).String(),
		"relay-contract-cost-" + outcomeID + "-" + rateID
}

func platformContractRateIDFromCostEvent(event model.PlatformChannelCostEvent) (string, error) {
	const notePrefix = "provider contract rate "
	if event.EvidenceSource != dto.PlatformChannelCostEvidenceContractRate ||
		!strings.HasPrefix(event.Note, notePrefix) {
		return "", model.ErrPlatformChannelCostEventCollision
	}
	rateID := strings.TrimPrefix(event.Note, notePrefix)
	if parsed, err := uuid.Parse(rateID); err != nil || parsed.String() != rateID {
		return "", model.ErrPlatformChannelCostEventCollision
	}
	return rateID, nil
}

func validatePlatformContractRateForFacts(
	rate model.PlatformProviderContractRate,
	facts model.PlatformProviderCostMaterializationFacts,
	resolution string,
) error {
	if rate.ProviderName != facts.Route.ProviderName || rate.ChannelID != facts.Route.ChannelID ||
		rate.UpstreamModel != facts.Route.UpstreamModel || rate.Mode != facts.Job.Mode ||
		rate.Resolution != resolution || rate.EffectiveFrom.UTC().After(facts.Outcome.OccurredAt.UTC()) {
		return model.ErrPlatformChannelCostEventCollision
	}
	return nil
}

func calculatePlatformProviderContractCost(
	rate model.PlatformProviderContractRate,
	output dto.PlatformGenerationOutputOptions,
) (int64, error) {
	if dto.PlatformContractRateRequiresTokenUsageEvidence(rate.UpstreamModel) {
		return 0, model.ErrPlatformProviderContractRateUsageEvidenceRequired
	}
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
) (string, string, string, error) {
	companyValue, hasCompany := request.Metadata["platform_company_id"]
	personalValue, hasPersonal := request.Metadata["platform_personal_workspace_id"]
	taskValue, hasTask := request.Metadata["platform_task_id"]
	scopeValue, hasScope := request.Metadata["platform_billing_scope"]
	scopeIDValue, hasScopeID := request.Metadata["platform_billing_scope_id"]
	if !hasCompany && !hasPersonal && !hasTask && !hasScope && !hasScopeID {
		return "", "", "", nil
	}
	companyID, companyOK := metadataString(companyValue, hasCompany)
	personalWorkspaceID, personalOK := metadataString(personalValue, hasPersonal)
	taskID, taskOK := taskValue.(string)
	if !hasTask || !taskOK || taskID == "" || strings.TrimSpace(taskID) != taskID || len(taskID) > 64 ||
		hasCompany != companyOK || hasPersonal != personalOK || companyOK == personalOK || request.ClientReferenceID == nil ||
		*request.ClientReferenceID != taskID || job.ClientReferenceID == nil || *job.ClientReferenceID != taskID {
		return "", "", "", fmt.Errorf("Platform task metadata cannot be proven")
	}

	expectedScope := "company"
	expectedScopeID := companyID
	if personalOK {
		expectedScope = "personal"
		expectedScopeID = personalWorkspaceID
	}
	if !hasScope || !hasScopeID {
		return "", "", "", fmt.Errorf("Platform billing scope metadata is incomplete")
	}
	scope, scopeOK := scopeValue.(string)
	scopeID, scopeIDOK := scopeIDValue.(string)
	if !scopeOK || !scopeIDOK || scope != expectedScope || scopeID != expectedScopeID {
		return "", "", "", fmt.Errorf("Platform billing scope metadata is inconsistent")
	}
	return companyID, personalWorkspaceID, taskID, nil
}

func metadataString(value any, present bool) (string, bool) {
	if !present {
		return "", false
	}
	text, ok := value.(string)
	if !ok || text == "" || strings.TrimSpace(text) != text || len(text) > 64 {
		return "", false
	}
	return text, true
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
