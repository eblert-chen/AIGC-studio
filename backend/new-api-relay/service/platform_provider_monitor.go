package service

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"gorm.io/gorm"
)

type PlatformProviderRouteProber interface {
	ProbePlatformProviderRoute(context.Context, model.PlatformGenerationProviderRoute) (dto.PlatformProviderRouteProbeResult, error)
}

type PlatformProviderMonitorPolicy struct {
	OutcomeLookback                 time.Duration
	SuccessRateMinimumSamples       int
	SuccessRateTriggerBasisPoints   int
	SuccessRateRecoveryBasisPoints  int
	WidespreadMinimumRoutes         int
	WidespreadMinimumAffectedRoutes int
	WidespreadTriggerBasisPoints    int
	WidespreadRecoveryBasisPoints   int
	BatchInvalidationMinimumRoutes  int
}

func DefaultPlatformProviderMonitorPolicy() PlatformProviderMonitorPolicy {
	return PlatformProviderMonitorPolicy{
		OutcomeLookback:                 15 * time.Minute,
		SuccessRateMinimumSamples:       20,
		SuccessRateTriggerBasisPoints:   8000,
		SuccessRateRecoveryBasisPoints:  9500,
		WidespreadMinimumRoutes:         3,
		WidespreadMinimumAffectedRoutes: 2,
		WidespreadTriggerBasisPoints:    5000,
		WidespreadRecoveryBasisPoints:   2000,
		BatchInvalidationMinimumRoutes:  2,
	}
}

func (policy PlatformProviderMonitorPolicy) Validate() error {
	if policy.OutcomeLookback < time.Minute || policy.OutcomeLookback > 30*24*time.Hour {
		return fmt.Errorf("provider monitor outcome lookback is invalid")
	}
	if policy.SuccessRateMinimumSamples < 1 ||
		policy.SuccessRateTriggerBasisPoints < 0 || policy.SuccessRateTriggerBasisPoints > 10_000 ||
		policy.SuccessRateRecoveryBasisPoints < 0 || policy.SuccessRateRecoveryBasisPoints > 10_000 ||
		policy.SuccessRateRecoveryBasisPoints <= policy.SuccessRateTriggerBasisPoints {
		return fmt.Errorf("provider monitor success-rate policy is invalid")
	}
	if policy.WidespreadMinimumRoutes < 1 || policy.WidespreadMinimumAffectedRoutes < 1 ||
		policy.WidespreadMinimumAffectedRoutes > policy.WidespreadMinimumRoutes ||
		policy.WidespreadTriggerBasisPoints < 1 || policy.WidespreadTriggerBasisPoints > 10_000 ||
		policy.WidespreadRecoveryBasisPoints < 0 || policy.WidespreadRecoveryBasisPoints >= policy.WidespreadTriggerBasisPoints {
		return fmt.Errorf("provider monitor widespread-failure policy is invalid")
	}
	if policy.BatchInvalidationMinimumRoutes < 2 {
		return fmt.Errorf("provider monitor batch invalidation policy is invalid")
	}
	return nil
}

type PlatformProviderMonitorWorkerOptions struct {
	OwnerID      string
	Lease        time.Duration
	Interval     time.Duration
	AcquireRetry time.Duration
	ProbeTimeout time.Duration
	Policy       PlatformProviderMonitorPolicy
	Prober       PlatformProviderRouteProber
}

func (options PlatformProviderMonitorWorkerOptions) Validate() error {
	if options.OwnerID == "" || strings.TrimSpace(options.OwnerID) != options.OwnerID || len(options.OwnerID) > 128 {
		return fmt.Errorf("provider monitor owner id is invalid")
	}
	if options.Lease < 30*time.Second || options.Interval < time.Second || options.Interval >= options.Lease ||
		options.AcquireRetry < time.Second || options.ProbeTimeout < time.Second || options.ProbeTimeout >= options.Lease {
		return fmt.Errorf("provider monitor worker timing is invalid")
	}
	if options.Prober == nil {
		return fmt.Errorf("provider monitor prober is required")
	}
	return options.Policy.Validate()
}

type PlatformProviderMonitorReadiness struct {
	Enabled             bool       `json:"enabled"`
	Fresh               bool       `json:"fresh"`
	Degraded            bool       `json:"degraded"`
	LastCompletedAt     *time.Time `json:"last_completed_at"`
	FreshnessSeconds    int64      `json:"freshness_seconds"`
	ActiveIncidents     int64      `json:"active_incidents"`
	UnavailableRoutes   int64      `json:"unavailable_routes"`
	AlertPending        int64      `json:"alert_pending"`
	AlertClaimed        int64      `json:"alert_claimed"`
	AlertDeadLetter     int64      `json:"alert_dead_letter"`
	LastWorkerErrorCode string     `json:"last_worker_error_code"`
}

func RecordPlatformProviderTerminalOutcome(input dto.PlatformProviderTerminalOutcomeInput) (bool, error) {
	if err := input.Validate(); err != nil {
		return false, err
	}
	outcome := model.PlatformProviderTerminalOutcome{
		ID:                 input.EventID,
		RouteID:            input.RouteID,
		RelayJobID:         input.RelayJobID,
		Outcome:            input.Outcome,
		FailureOwner:       input.FailureOwner,
		FailureCode:        input.FailureCode,
		AccountInvalidated: input.AccountInvalidated,
		OccurredAt:         input.OccurredAt.UTC(),
		ExternalReference:  input.ExternalReference,
	}
	return model.CreatePlatformProviderTerminalOutcome(&outcome)
}

// RunPlatformProviderMonitorCycle probes every known route, persists only
// explicit observations, evaluates incidents, and finishes under one lease
// token. Each database mutation rechecks the token and database-clock expiry.
func RunPlatformProviderMonitorCycle(
	ctx context.Context,
	claim model.PlatformProviderMonitorLeaseClaim,
	lease time.Duration,
	probeTimeout time.Duration,
	prober PlatformProviderRouteProber,
	policy PlatformProviderMonitorPolicy,
) error {
	if prober == nil {
		return fmt.Errorf("provider monitor prober is required")
	}
	if err := policy.Validate(); err != nil {
		return err
	}
	_, won, err := model.RenewPlatformProviderMonitorLease(claim.Token, lease)
	if err != nil {
		return err
	}
	if !won {
		return model.ErrPlatformProviderMonitorLeaseLost
	}

	routes, err := model.ListPlatformGenerationProviderRoutesForMonitoring()
	if err != nil {
		completePlatformProviderMonitorCycleWithError(claim.Token, "route_list_failed")
		return err
	}
	observations := make([]model.PlatformProviderRouteObservation, 0, len(routes))
	for _, route := range routes {
		_, renewed, renewErr := model.RenewPlatformProviderMonitorLease(claim.Token, lease)
		if renewErr != nil {
			return renewErr
		}
		if !renewed {
			return model.ErrPlatformProviderMonitorLeaseLost
		}
		observation := model.PlatformProviderRouteObservation{RouteID: route.ID}
		probeContext, cancel := context.WithTimeout(ctx, probeTimeout)
		result, probeErr := prober.ProbePlatformProviderRoute(probeContext, route)
		cancel()
		observation.Probed = true
		if probeErr != nil {
			observation.Status = model.PlatformProviderRouteHealthFailed
			observation.FailureCode = "probe_failed"
		} else {
			if err := result.Validate(); err != nil {
				completePlatformProviderMonitorCycleWithError(claim.Token, "probe_result_invalid")
				return err
			}
			observation.Status = result.Status
			observation.FailureCode = result.FailureCode
			observation.ProviderCaused = result.ProviderCaused
		}
		observations = append(observations, observation)
	}
	if err := model.ApplyPlatformProviderRouteObservations(claim.Token, observations); err != nil {
		completePlatformProviderMonitorCycleWithError(claim.Token, "health_persist_failed")
		return err
	}

	snapshot, err := model.GetPlatformProviderMonitorEvaluationSnapshot(policy.OutcomeLookback)
	if err != nil {
		completePlatformProviderMonitorCycleWithError(claim.Token, "snapshot_failed")
		return err
	}
	decisions, err := EvaluatePlatformProviderMonitorSnapshot(snapshot, policy)
	if err != nil {
		completePlatformProviderMonitorCycleWithError(claim.Token, "evaluation_failed")
		return err
	}
	if err := model.ApplyPlatformProviderIncidentDecisions(claim.Token, decisions); err != nil {
		completePlatformProviderMonitorCycleWithError(claim.Token, "incident_persist_failed")
		return err
	}
	won, err = model.CompletePlatformProviderMonitorCycle(claim.Token, "")
	if err != nil {
		return err
	}
	if !won {
		return model.ErrPlatformProviderMonitorLeaseLost
	}
	// The same database-clock-fenced monitor owner emits the operations
	// snapshot. This prevents duplicate schedulers across Relay replicas while
	// keeping delivery itself independently durable and retryable.
	if _, _, err := EnqueuePlatformOperationsSnapshot(ctx); err != nil {
		return fmt.Errorf("enqueue operations snapshot: %w", err)
	}
	return nil
}

// RunPlatformProviderMonitorWorker is safe to run in every worker process.
// PostgreSQL/MySQL row locking plus the random token ensure only the current
// database-clock lease owner can publish a cycle.
func RunPlatformProviderMonitorWorker(ctx context.Context, options PlatformProviderMonitorWorkerOptions) error {
	if err := options.Validate(); err != nil {
		return err
	}
	for {
		if err := ctx.Err(); err != nil {
			return nil
		}
		claim, err := model.ClaimPlatformProviderMonitorLease(options.OwnerID, options.Lease)
		if errors.Is(err, model.ErrPlatformProviderMonitorLeaseHeld) {
			if !waitPlatformProviderMonitor(ctx, options.AcquireRetry) {
				return nil
			}
			continue
		}
		if err != nil {
			if !waitPlatformProviderMonitor(ctx, options.AcquireRetry) {
				return nil
			}
			continue
		}

		for {
			err = RunPlatformProviderMonitorCycle(
				ctx,
				*claim,
				options.Lease,
				options.ProbeTimeout,
				options.Prober,
				options.Policy,
			)
			if errors.Is(err, model.ErrPlatformProviderMonitorLeaseLost) {
				break
			}
			if !waitPlatformProviderMonitor(ctx, options.Interval) {
				_, _ = model.ReleasePlatformProviderMonitorLease(claim.Token)
				return nil
			}
		}
	}
}

func EvaluatePlatformProviderMonitorSnapshot(
	snapshot model.PlatformProviderMonitorEvaluationSnapshot,
	policy PlatformProviderMonitorPolicy,
) ([]model.PlatformProviderIncidentDecision, error) {
	if err := policy.Validate(); err != nil {
		return nil, err
	}
	active := make(map[string]bool, len(snapshot.Incidents))
	providers := make(map[string]struct{})
	for _, incident := range snapshot.Incidents {
		providers[incident.ProviderName] = struct{}{}
		active[incident.ProviderName+"\x00"+incident.Kind] = incident.Active
	}
	retired := make(map[string]struct{}, len(snapshot.Retirements))
	for _, acknowledgement := range snapshot.Retirements {
		providers[acknowledgement.ProviderName] = struct{}{}
		retired[acknowledgement.ProviderName] = struct{}{}
	}
	healthByProvider := make(map[string][]model.PlatformProviderRouteHealth)
	for _, health := range snapshot.Health {
		providers[health.ProviderName] = struct{}{}
		healthByProvider[health.ProviderName] = append(healthByProvider[health.ProviderName], health)
	}
	outcomesByProvider := make(map[string][]model.PlatformProviderTerminalOutcome)
	for _, outcome := range snapshot.Outcomes {
		providers[outcome.ProviderName] = struct{}{}
		outcomesByProvider[outcome.ProviderName] = append(outcomesByProvider[outcome.ProviderName], outcome)
	}

	providerNames := make([]string, 0, len(providers))
	for providerName := range providers {
		providerNames = append(providerNames, providerName)
	}
	sort.Strings(providerNames)
	decisions := make([]model.PlatformProviderIncidentDecision, 0, len(providerNames)*3)
	for _, providerName := range providerNames {
		_, retirementAcknowledged := retired[providerName]
		providerHealth := healthByProvider[providerName]
		providerOutcomes := outcomesByProvider[providerName]

		sampleSize := 0
		successCount := 0
		for _, outcome := range providerOutcomes {
			if outcome.Outcome == model.PlatformProviderOutcomeSucceeded {
				sampleSize++
				successCount++
			} else if outcome.Outcome == model.PlatformProviderOutcomeFailed && outcome.FailureOwner == model.PlatformProviderFailureOwnerProvider {
				sampleSize++
			}
		}
		successRate := 0
		if sampleSize > 0 {
			successRate = successCount * 10_000 / sampleSize
		}
		successKey := providerName + "\x00" + model.PlatformProviderIncidentSuccessRateDrop
		successDesired := active[successKey]
		successReason := "success_rate_hysteresis"
		switch {
		case retirementAcknowledged:
			successDesired = false
			successReason = "planned_retirement_acknowledged"
		case sampleSize < policy.SuccessRateMinimumSamples:
			successReason = "insufficient_recent_samples"
		case successRate < policy.SuccessRateTriggerBasisPoints:
			successDesired = true
			successReason = "success_rate_below_threshold"
		case successRate >= policy.SuccessRateRecoveryBasisPoints:
			successDesired = false
			successReason = "success_rate_recovered"
		}
		decisions = append(decisions, model.PlatformProviderIncidentDecision{
			ProviderName:           providerName,
			Kind:                   model.PlatformProviderIncidentSuccessRateDrop,
			DesiredActive:          successDesired,
			ReasonCode:             successReason,
			SampleSize:             sampleSize,
			SuccessCount:           successCount,
			SuccessRateBasisPoints: successRate,
		})

		failedRoutes := 0
		invalidatedRoutes := 0
		for _, health := range providerHealth {
			switch health.Status {
			case model.PlatformProviderRouteHealthFailed:
				failedRoutes++
			case model.PlatformProviderRouteHealthInvalidated:
				failedRoutes++
				if health.FailureProviderCaused {
					invalidatedRoutes++
				}
			}
		}
		totalRoutes := len(providerHealth)
		failureRate := 0
		if totalRoutes > 0 {
			failureRate = failedRoutes * 10_000 / totalRoutes
		}
		widespreadKey := providerName + "\x00" + model.PlatformProviderIncidentWidespreadFailure
		widespreadDesired := active[widespreadKey]
		widespreadReason := "route_failure_hysteresis"
		switch {
		case retirementAcknowledged:
			widespreadDesired = false
			widespreadReason = "planned_retirement_acknowledged"
		case totalRoutes == 0:
			widespreadReason = "no_explicit_route_recovery"
		case totalRoutes >= policy.WidespreadMinimumRoutes &&
			failedRoutes >= policy.WidespreadMinimumAffectedRoutes &&
			failureRate >= policy.WidespreadTriggerBasisPoints:
			widespreadDesired = true
			widespreadReason = "widespread_route_failure"
		case failureRate <= policy.WidespreadRecoveryBasisPoints:
			widespreadDesired = false
			widespreadReason = "route_health_recovered"
		}
		decisions = append(decisions, model.PlatformProviderIncidentDecision{
			ProviderName:           providerName,
			Kind:                   model.PlatformProviderIncidentWidespreadFailure,
			DesiredActive:          widespreadDesired,
			ReasonCode:             widespreadReason,
			AffectedRoutes:         failedRoutes,
			TotalRoutes:            totalRoutes,
			SuccessRateBasisPoints: 10_000 - failureRate,
		})

		batchKey := providerName + "\x00" + model.PlatformProviderIncidentBatchInvalidation
		batchDesired := active[batchKey]
		batchReason := "account_invalidation_requires_probe"
		switch {
		case retirementAcknowledged:
			batchDesired = false
			batchReason = "planned_retirement_acknowledged"
		case invalidatedRoutes >= policy.BatchInvalidationMinimumRoutes:
			batchDesired = true
			batchReason = "provider_batch_account_invalidation"
		case totalRoutes > 0 && invalidatedRoutes == 0:
			batchDesired = false
			batchReason = "invalidated_accounts_explicitly_recovered"
		}
		decisions = append(decisions, model.PlatformProviderIncidentDecision{
			ProviderName:   providerName,
			Kind:           model.PlatformProviderIncidentBatchInvalidation,
			DesiredActive:  batchDesired,
			ReasonCode:     batchReason,
			AffectedRoutes: invalidatedRoutes,
			TotalRoutes:    totalRoutes,
		})
	}
	return decisions, nil
}

func GetPlatformProviderMonitorReadiness(enabled bool, maximumFreshness time.Duration) (PlatformProviderMonitorReadiness, error) {
	summary := PlatformProviderMonitorReadiness{Enabled: enabled}
	if !enabled {
		return summary, nil
	}
	if maximumFreshness < time.Second {
		return summary, fmt.Errorf("provider monitor freshness threshold is invalid")
	}
	lease, err := model.GetPlatformProviderMonitorLease()
	if errors.Is(err, gorm.ErrRecordNotFound) {
		summary.Degraded = true
		return summary, nil
	}
	if err != nil {
		return summary, err
	}
	summary.LastCompletedAt = lease.LastCompletedAt
	summary.LastWorkerErrorCode = lease.LastErrorCode
	if lease.LastCompletedAt != nil {
		var now time.Time
		err := model.DB.Transaction(func(tx *gorm.DB) error {
			var clockErr error
			now, clockErr = model.GetDBTimeTx(tx)
			return clockErr
		})
		if err != nil {
			return summary, err
		}
		age := now.Sub(lease.LastCompletedAt.UTC())
		if age < 0 {
			age = 0
		}
		summary.FreshnessSeconds = int64(age / time.Second)
		summary.Fresh = age <= maximumFreshness
	}
	if err := model.DB.Model(&model.PlatformProviderIncident{}).Where("active = ?", true).Count(&summary.ActiveIncidents).Error; err != nil {
		return summary, err
	}
	if err := model.DB.Model(&model.PlatformProviderRouteHealth{}).
		Where("status IN ?", []string{model.PlatformProviderRouteHealthFailed, model.PlatformProviderRouteHealthInvalidated}).
		Count(&summary.UnavailableRoutes).Error; err != nil {
		return summary, err
	}
	counts, err := model.GetPlatformRelayDeliveryCounts(model.PlatformRelayDeliveryKindProviderAlert)
	if err != nil {
		return summary, err
	}
	summary.AlertPending = counts.Pending
	summary.AlertClaimed = counts.Claimed
	summary.AlertDeadLetter = counts.DeadLetter
	// Degraded is diagnostic only. A provider outage does not make the Relay
	// API liveness/readiness endpoint unavailable.
	summary.Degraded = !summary.Fresh || summary.LastWorkerErrorCode != "" ||
		summary.ActiveIncidents > 0 || summary.UnavailableRoutes > 0 ||
		summary.AlertPending > 0 || summary.AlertClaimed > 0 || summary.AlertDeadLetter > 0
	return summary, nil
}

func completePlatformProviderMonitorCycleWithError(token string, errorCode string) {
	_, _ = model.CompletePlatformProviderMonitorCycle(token, errorCode)
}

func waitPlatformProviderMonitor(ctx context.Context, duration time.Duration) bool {
	timer := time.NewTimer(duration)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}
