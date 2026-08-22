package service

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"gorm.io/gorm"
)

const (
	platformTaskStageUserAgent          = "ai-video-relay-task-stage/1.0"
	platformOperationsSnapshotUserAgent = "ai-video-relay-operations-snapshot/1.0"
	platformOperationsDefaultWindow     = 15 * time.Minute
	platformOperationsDefaultTTL        = 2 * time.Minute
)

type PlatformTelemetrySinkConfig struct {
	TaskStageURL          string
	OperationsSnapshotURL string
	InternalServiceToken  string
	SigningSecret         string
	Production            bool
}

func (config PlatformTelemetrySinkConfig) Validate() error {
	if err := validatePlatformTelemetryTarget(config.TaskStageURL, "/internal/relay/task-stages", config.Production); err != nil {
		return err
	}
	if err := validatePlatformTelemetryTarget(config.OperationsSnapshotURL, "/internal/relay/operations-snapshots", config.Production); err != nil {
		return err
	}
	if config.InternalServiceToken == "" || config.SigningSecret == "" {
		return fmt.Errorf("telemetry delivery credentials are required")
	}
	if config.Production && (len([]byte(config.InternalServiceToken)) < 32 || len([]byte(config.SigningSecret)) < 32) {
		return fmt.Errorf("telemetry delivery credentials are too short")
	}
	if config.Production && (platformRelaySecretIsPlaceholder(config.InternalServiceToken) || platformRelaySecretIsPlaceholder(config.SigningSecret)) {
		return fmt.Errorf("telemetry delivery credentials contain a placeholder")
	}
	if config.Production && config.InternalServiceToken == config.SigningSecret {
		return fmt.Errorf("telemetry authentication and signing secrets must be independent")
	}
	return nil
}

func DeliverPlatformTaskStageClaim(
	ctx context.Context,
	claim model.PlatformRelayDeliveryClaim,
	config PlatformTelemetrySinkConfig,
) (string, bool, error) {
	result := PlatformRelayExternalDispatchResult{Retryable: true, Failure: model.PlatformRelayDeliveryFailureConfiguration}
	if claim.Delivery.EventKind != model.PlatformRelayDeliveryKindTaskStage {
		result.Retryable = false
	} else if config.Validate() == nil {
		event, err := model.GetPlatformTaskStageEvent(claim.Delivery.EventID)
		if err == nil {
			payload := []byte(event.PayloadJSON)
			var envelope dto.PlatformTaskStagePayload
			if platformRelayExternalPayloadDigestMatches(payload, event.PayloadSHA256) &&
				common.Unmarshal(payload, &envelope) == nil && envelope.Validate() == nil &&
				envelope.RelayJobID == event.RelayJobID && envelope.CompanyID == event.CompanyID &&
				envelope.TaskID == event.TaskID && envelope.Stage == event.Stage &&
				envelope.OccurredAt.UTC().Equal(event.OccurredAt.UTC()) {
				client := newPlatformRelayExternalHTTPClient(config.Production, false)
				result = sendPlatformRelayExternalEvent(
					ctx, client, time.Now().UTC(), config.TaskStageURL, config.SigningSecret,
					config.InternalServiceToken, platformTaskStageUserAgent, claim.Delivery, payload,
				)
			} else {
				result = PlatformRelayExternalDispatchResult{Failure: model.PlatformRelayDeliveryFailurePayload}
			}
		} else if errors.Is(err, gorm.ErrRecordNotFound) {
			result.Retryable = false
			result.Failure = model.PlatformRelayDeliveryFailurePayload
		}
	}
	return applyPlatformRelayExternalDispatchResult(claim, result)
}

func DeliverPlatformOperationsSnapshotClaim(
	ctx context.Context,
	claim model.PlatformRelayDeliveryClaim,
	config PlatformTelemetrySinkConfig,
) (string, bool, error) {
	result := PlatformRelayExternalDispatchResult{Retryable: true, Failure: model.PlatformRelayDeliveryFailureConfiguration}
	if claim.Delivery.EventKind != model.PlatformRelayDeliveryKindOperationsSnapshot {
		result.Retryable = false
	} else if config.Validate() == nil {
		event, err := model.GetPlatformOperationsSnapshotEvent(claim.Delivery.EventID)
		if err == nil {
			payload := []byte(event.PayloadJSON)
			var envelope dto.PlatformOperationsSnapshot
			if platformRelayExternalPayloadDigestMatches(payload, event.PayloadSHA256) &&
				common.Unmarshal(payload, &envelope) == nil && envelope.Validate() == nil &&
				envelope.ObservedAt.UTC().Equal(event.ObservedAt.UTC()) &&
				envelope.ExpiresAt.UTC().Equal(event.ExpiresAt.UTC()) {
				client := newPlatformRelayExternalHTTPClient(config.Production, false)
				result = sendPlatformRelayExternalEvent(
					ctx, client, time.Now().UTC(), config.OperationsSnapshotURL, config.SigningSecret,
					config.InternalServiceToken, platformOperationsSnapshotUserAgent, claim.Delivery, payload,
				)
			} else {
				result = PlatformRelayExternalDispatchResult{Failure: model.PlatformRelayDeliveryFailurePayload}
			}
		} else if errors.Is(err, gorm.ErrRecordNotFound) {
			result.Retryable = false
			result.Failure = model.PlatformRelayDeliveryFailurePayload
		}
	}
	return applyPlatformRelayExternalDispatchResult(claim, result)
}

func EnqueuePlatformOperationsSnapshot(ctx context.Context) (bool, *model.PlatformOperationsSnapshotEvent, error) {
	snapshot, err := BuildPlatformOperationsSnapshot(ctx, platformOperationsDefaultWindow, platformOperationsDefaultTTL)
	if err != nil {
		return false, nil, err
	}
	return model.CreatePlatformOperationsSnapshotEvent(snapshot)
}

func BuildPlatformOperationsSnapshot(
	ctx context.Context,
	window time.Duration,
	ttl time.Duration,
) (dto.PlatformOperationsSnapshot, error) {
	var snapshot dto.PlatformOperationsSnapshot
	if ctx == nil || window < time.Minute || ttl < time.Second || ttl > 24*time.Hour {
		return snapshot, fmt.Errorf("operations snapshot parameters are invalid")
	}
	if err := ctx.Err(); err != nil {
		return snapshot, err
	}
	// Read the completed monitor state before fixing the snapshot timestamp.
	// This guarantees monitor_last_completed_at cannot race ahead of the
	// observed_at envelope when another leased monitor finishes concurrently.
	monitor, err := GetPlatformProviderMonitorReadiness(true, ttl)
	if err != nil {
		return snapshot, err
	}
	var observedAt time.Time
	if err := model.DB.Transaction(func(tx *gorm.DB) error {
		var clockErr error
		observedAt, clockErr = model.GetDBTimeTx(tx)
		return clockErr
	}); err != nil {
		return snapshot, err
	}
	windowStartedAt := observedAt.Add(-window)
	snapshot = dto.PlatformOperationsSnapshot{
		SchemaVersion:   1,
		ObservedAt:      observedAt.UTC(),
		ExpiresAt:       observedAt.Add(ttl).UTC(),
		WindowStartedAt: windowStartedAt.UTC(),
		Routes:          make([]dto.PlatformOperationsRouteSnapshot, 0),
	}

	snapshot.MonitorFresh = monitor.Fresh
	snapshot.MonitorLastCompletedAt = monitor.LastCompletedAt

	var routes []model.PlatformGenerationProviderRoute
	if err := model.DB.Order("route_key ASC, mode ASC, id ASC").Find(&routes).Error; err != nil {
		return snapshot, err
	}
	var states []model.PlatformGenerationProviderAccountState
	if err := model.DB.Order("id ASC").Find(&states).Error; err != nil {
		return snapshot, err
	}
	stateByID := make(map[int64]model.PlatformGenerationProviderAccountState, len(states))
	for _, state := range states {
		stateByID[state.ID] = state
	}
	var healthRows []model.PlatformProviderRouteHealth
	if err := model.DB.Find(&healthRows).Error; err != nil {
		return snapshot, err
	}
	healthByRoute := make(map[int64]model.PlatformProviderRouteHealth, len(healthRows))
	for _, health := range healthRows {
		healthByRoute[health.RouteID] = health
	}

	var outcomes []model.PlatformProviderTerminalOutcome
	if err := model.DB.Where("occurred_at >= ? AND occurred_at <= ?", windowStartedAt, observedAt).
		Order("occurred_at ASC, id ASC").Find(&outcomes).Error; err != nil {
		return snapshot, err
	}
	type routeWindow struct{ succeeded, failed int64 }
	windowByRoute := make(map[int64]routeWindow)
	for _, outcome := range outcomes {
		counts := windowByRoute[outcome.RouteID]
		if outcome.Outcome == model.PlatformProviderOutcomeSucceeded {
			counts.succeeded++
		} else if outcome.Outcome == model.PlatformProviderOutcomeFailed {
			counts.failed++
		}
		windowByRoute[outcome.RouteID] = counts
	}

	var terminalJobs []model.PlatformGenerationJob
	if err := model.DB.Where("updated_at >= ? AND updated_at <= ? AND provider_route_id > 0 AND status IN ?", windowStartedAt, observedAt,
		[]string{model.PlatformGenerationStatusSucceeded, model.PlatformGenerationStatusFailed, model.PlatformGenerationStatusCancelled}).
		Order("updated_at ASC, id ASC").Find(&terminalJobs).Error; err != nil {
		return snapshot, err
	}
	latencyByRoute := make(map[int64][]int64)
	for _, job := range terminalJobs {
		latency := job.UpdatedAt.UTC().Sub(job.CreatedAt.UTC()).Milliseconds()
		if latency < 0 {
			latency = 0
		}
		latencyByRoute[job.ProviderRouteID] = append(latencyByRoute[job.ProviderRouteID], latency)
	}

	invalidState := make(map[int64]bool)
	eligibleState := make(map[int64]bool)
	environment := strings.ToLower(strings.TrimSpace(os.Getenv("RELAY_COMPAT_ENVIRONMENT")))
	for _, route := range routes {
		ready, _ := platformGenerationProviderRouteReadiness(route, environment)
		if route.Enabled && ready {
			eligibleState[route.AccountStateID] = true
		}
		if health, ok := healthByRoute[route.ID]; ok && health.Status == model.PlatformProviderRouteHealthInvalidated {
			invalidState[route.AccountStateID] = true
		}
	}
	for _, state := range states {
		if !eligibleState[state.ID] {
			continue
		}
		cooling := state.CoolingUntil != nil && state.CoolingUntil.UTC().After(observedAt.UTC())
		busy := state.ActiveLimit > 0 && state.ActiveCount >= state.ActiveLimit
		rateLimited := state.RPMWindowStartedAt != nil &&
			state.RPMWindowStartedAt.Add(time.Duration(state.RPMWindowSeconds)*time.Second).After(observedAt) &&
			state.RPMWindowCount >= state.RPMLimit
		invalid := invalidState[state.ID]
		snapshot.AccountPool.TotalAccounts++
		if !cooling && !invalid {
			snapshot.AccountPool.ActiveAccounts++
		}
		if cooling {
			snapshot.AccountPool.CoolingAccounts++
		}
		if invalid {
			snapshot.AccountPool.InvalidAccounts++
		}
		if busy {
			snapshot.AccountPool.BusyAccounts++
		}
		if rateLimited {
			snapshot.AccountPool.RateLimitedAccounts++
		}
		snapshot.AccountPool.ActiveTaskCount += int64(state.ActiveCount)
		snapshot.AccountPool.TaskCapacity += int64(state.ActiveLimit)
	}

	for _, route := range routes {
		state, stateExists := stateByID[route.AccountStateID]
		health, healthExists := healthByRoute[route.ID]
		row := dto.PlatformOperationsRouteSnapshot{
			RouteID: route.ID, ChannelKey: route.RouteKey, ChannelType: route.ChannelClass,
			ProviderName: route.ProviderName, Model: route.Model, Mode: route.Mode,
			Enabled: route.Enabled, ProductionReady: route.ProductionReady,
			HealthStatus: model.PlatformProviderRouteHealthUnknown,
		}
		if stateExists {
			row.RPMLimit = int64(state.RPMLimit)
			row.ActiveTaskCount = int64(state.ActiveCount)
			row.TaskCapacity = int64(state.ActiveLimit)
			cooling := state.CoolingUntil != nil && state.CoolingUntil.UTC().After(observedAt.UTC())
			busy := state.ActiveLimit > 0 && state.ActiveCount >= state.ActiveLimit
			rateLimited := state.RPMWindowStartedAt != nil &&
				state.RPMWindowStartedAt.Add(time.Duration(state.RPMWindowSeconds)*time.Second).After(observedAt) &&
				state.RPMWindowCount >= state.RPMLimit
			if state.RPMWindowStartedAt != nil &&
				state.RPMWindowStartedAt.Add(time.Duration(state.RPMWindowSeconds)*time.Second).After(observedAt) {
				row.RPMUsed = int64(state.RPMWindowCount)
			}
			if cooling {
				row.CoolingAccountCount = 1
			}
			if busy {
				row.BusyAccountCount = 1
			}
			if rateLimited {
				row.RateLimitedAccountCount = 1
			}
		}
		if healthExists {
			row.HealthStatus = health.Status
			row.FailureCode = health.FailureCode
			row.LastProbeAt = health.LastProbeAt
			if health.Status == model.PlatformProviderRouteHealthInvalidated {
				row.InvalidAccountCount = 1
			}
		}
		if !route.Enabled {
			row.HealthStatus = "disabled"
		} else if stateExists && state.CoolingUntil != nil && state.CoolingUntil.UTC().After(observedAt.UTC()) {
			row.HealthStatus = "cooling"
			if row.FailureCode == "" {
				row.FailureCode = state.LastErrorCode
			}
		}
		counts := windowByRoute[route.ID]
		row.SuccessfulTaskCount = counts.succeeded
		row.FailedTaskCount = counts.failed
		if values := latencyByRoute[route.ID]; len(values) > 0 {
			sort.Slice(values, func(i, j int) bool { return values[i] < values[j] })
			p50 := percentileNearestRank(values, 50)
			p95 := percentileNearestRank(values, 95)
			row.LatencyP50MS = &p50
			row.LatencyP95MS = &p95
		}
		snapshot.Routes = append(snapshot.Routes, row)
	}

	statusCounts := make([]struct {
		Status string
		Total  int64
	}, 0)
	if err := model.DB.Model(&model.PlatformGenerationJob{}).Select("status, COUNT(*) AS total").Group("status").Scan(&statusCounts).Error; err != nil {
		return snapshot, err
	}
	for _, count := range statusCounts {
		switch count.Status {
		case model.PlatformGenerationStatusQueued:
			snapshot.Tasks.Queued = count.Total
		case model.PlatformGenerationStatusSubmitting:
			snapshot.Tasks.Submitting = count.Total
		case model.PlatformGenerationStatusReconciliationRequired:
			snapshot.Tasks.SubmissionUnknown = count.Total
		case model.PlatformGenerationStatusProcessing:
			snapshot.Tasks.ProviderProcessing = count.Total
		case model.PlatformGenerationStatusTransferring:
			snapshot.Tasks.ArtifactTransferring = count.Total
		case model.PlatformGenerationStatusSucceeded:
			snapshot.Tasks.Succeeded = count.Total
		case model.PlatformGenerationStatusFailed:
			snapshot.Tasks.Failed = count.Total
		case model.PlatformGenerationStatusCancelled:
			snapshot.Tasks.Cancelled = count.Total
		}
	}
	if err := model.DB.Model(&model.PlatformGenerationJob{}).
		Where("updated_at >= ? AND error_code = ?", windowStartedAt, model.PlatformGenerationErrorProviderAccountPoolRateLimited).
		Count(&snapshot.Tasks.RateLimitedCount).Error; err != nil {
		return snapshot, err
	}
	if err := model.DB.Model(&model.PlatformGenerationJob{}).
		Where("updated_at >= ? AND provider_submission_attempt > 1", windowStartedAt).
		Count(&snapshot.Tasks.FailoverCount).Error; err != nil {
		return snapshot, err
	}

	alertCounts, err := model.GetPlatformRelayDeliveryCounts(model.PlatformRelayDeliveryKindProviderAlert)
	if err != nil {
		return snapshot, err
	}
	costCounts, err := model.GetPlatformRelayDeliveryCounts(model.PlatformRelayDeliveryKindChannelCost)
	if err != nil {
		return snapshot, err
	}
	stageCounts, err := model.GetPlatformRelayDeliveryCounts(model.PlatformRelayDeliveryKindTaskStage)
	if err != nil {
		return snapshot, err
	}
	snapshotCounts, err := model.GetPlatformRelayDeliveryCounts(model.PlatformRelayDeliveryKindOperationsSnapshot)
	if err != nil {
		return snapshot, err
	}
	snapshot.Deliveries.PendingAlertCount = alertCounts.Pending + alertCounts.Claimed
	snapshot.Deliveries.DeadLetterAlertCount = alertCounts.DeadLetter
	snapshot.Deliveries.PendingCostCount = costCounts.Pending + costCounts.Claimed
	snapshot.Deliveries.DeadLetterCostCount = costCounts.DeadLetter
	snapshot.Deliveries.PendingTaskStageCount = stageCounts.Pending + stageCounts.Claimed
	snapshot.Deliveries.DeadLetterTaskStageCount = stageCounts.DeadLetter
	snapshot.Deliveries.PendingSnapshotCount = snapshotCounts.Pending + snapshotCounts.Claimed
	snapshot.Deliveries.DeadLetterSnapshotCount = snapshotCounts.DeadLetter
	snapshot.Deliveries.OldestPendingAlertAt, err = model.GetOldestPendingPlatformRelayDeliveryAt(model.PlatformRelayDeliveryKindProviderAlert)
	if err != nil {
		return snapshot, err
	}

	cost, err := model.GetPlatformChannelCostReconciliationSummary()
	if err != nil {
		return snapshot, err
	}
	snapshot.Costs = dto.PlatformOperationsCostSnapshot{
		SuccessfulRelayJobs:             cost.SuccessfulRelayJobs,
		ExplicitCostRelayJobs:           cost.ExplicitCostRelayJobs,
		DeliveredCostRelayJobs:          cost.DeliveredCostRelayJobs,
		IncompleteRelayJobs:             cost.IncompleteRelayJobs,
		NativeBillingReconciliationJobs: cost.NativeBillingReconciliationJobs,
		ReconciliationComplete:          cost.SuccessfulRelayJobs > 0 && cost.ReconciliationComplete,
	}
	if err := snapshot.Validate(); err != nil {
		return snapshot, err
	}
	return snapshot, nil
}

func percentileNearestRank(sorted []int64, percentile int) int64 {
	if len(sorted) == 0 {
		return 0
	}
	index := (len(sorted)*percentile + 99) / 100
	if index < 1 {
		index = 1
	}
	if index > len(sorted) {
		index = len(sorted)
	}
	return sorted[index-1]
}

func validatePlatformTelemetryTarget(rawURL string, path string, production bool) error {
	parsed, err := url.ParseRequestURI(rawURL)
	if err != nil || parsed == nil || !parsed.IsAbs() || parsed.Host == "" || parsed.User != nil ||
		parsed.RawQuery != "" || parsed.Fragment != "" || (parsed.Scheme != "http" && parsed.Scheme != "https") {
		return fmt.Errorf("telemetry target rejected")
	}
	if production && parsed.Scheme != "https" {
		return fmt.Errorf("telemetry target rejected")
	}
	if parsed.EscapedPath() != path {
		return fmt.Errorf("telemetry target path is invalid")
	}
	return nil
}
