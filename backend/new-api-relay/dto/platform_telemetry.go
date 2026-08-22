package dto

import (
	"fmt"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
)

const (
	PlatformTaskStageQueued               = "queued"
	PlatformTaskStageSubmitting           = "submitting"
	PlatformTaskStageSubmissionUnknown    = "submission_unknown"
	PlatformTaskStageProviderProcessing   = "provider_processing"
	PlatformTaskStageArtifactTransferring = "artifact_transferring"
	PlatformTaskStageArtifactStored       = "artifact_stored"
	PlatformTaskStageFailed               = "failed"
	PlatformTaskStageCancelled            = "cancelled"
)

var platformTelemetryErrorCodePattern = regexp.MustCompile(`^[A-Z][A-Z0-9_]{0,159}$`)

// PlatformTaskStagePayload is the immutable, secret-free task lifecycle fact
// delivered to the customer Platform. Route fields remain empty/null until a
// provider account has been durably assigned.
type PlatformTaskStagePayload struct {
	SchemaVersion  int       `json:"schema_version"`
	CompanyID      string    `json:"company_id"`
	TaskID         string    `json:"task_id"`
	RelayJobID     string    `json:"relay_job_id"`
	Stage          string    `json:"stage"`
	OccurredAt     time.Time `json:"occurred_at"`
	ChannelKey     string    `json:"channel_key"`
	ChannelType    string    `json:"channel_type"`
	RouteID        *int64    `json:"route_id"`
	ProviderTaskID string    `json:"provider_task_id"`
	DurationMS     *int64    `json:"duration_ms"`
	ErrorCode      string    `json:"error_code"`
}

func (payload PlatformTaskStagePayload) Validate() error {
	if payload.SchemaVersion != 1 {
		return fmt.Errorf("task stage schema_version is invalid")
	}
	for name, value := range map[string]string{
		"company_id":   payload.CompanyID,
		"task_id":      payload.TaskID,
		"relay_job_id": payload.RelayJobID,
	} {
		parsed, err := uuid.Parse(value)
		if err != nil || parsed.String() != value {
			return fmt.Errorf("task stage %s must be a canonical UUID", name)
		}
	}
	switch payload.Stage {
	case PlatformTaskStageQueued, PlatformTaskStageSubmitting,
		PlatformTaskStageSubmissionUnknown, PlatformTaskStageProviderProcessing,
		PlatformTaskStageArtifactTransferring, PlatformTaskStageArtifactStored,
		PlatformTaskStageFailed, PlatformTaskStageCancelled:
	default:
		return fmt.Errorf("task stage is invalid")
	}
	if payload.OccurredAt.IsZero() {
		return fmt.Errorf("task stage occurred_at is required")
	}
	if strings.TrimSpace(payload.ChannelKey) != payload.ChannelKey || utf8.RuneCountInString(payload.ChannelKey) > 120 {
		return fmt.Errorf("task stage channel_key is invalid")
	}
	switch payload.ChannelType {
	case "", "reverse", "third_party_api", "official":
	default:
		return fmt.Errorf("task stage channel_type is invalid")
	}
	if (payload.RouteID == nil) != (payload.ChannelKey == "" || payload.ChannelType == "") {
		return fmt.Errorf("task stage route identity is incomplete")
	}
	if payload.RouteID != nil && *payload.RouteID <= 0 {
		return fmt.Errorf("task stage route_id must be positive")
	}
	if strings.TrimSpace(payload.ProviderTaskID) != payload.ProviderTaskID || utf8.RuneCountInString(payload.ProviderTaskID) > 191 {
		return fmt.Errorf("task stage provider_task_id is invalid")
	}
	if payload.DurationMS != nil && *payload.DurationMS < 0 {
		return fmt.Errorf("task stage duration_ms must not be negative")
	}
	if payload.ErrorCode != "" && !platformTelemetryErrorCodePattern.MatchString(payload.ErrorCode) {
		return fmt.Errorf("task stage error_code is invalid")
	}
	return nil
}

type PlatformOperationsRouteSnapshot struct {
	RouteID                 int64      `json:"route_id"`
	ChannelKey              string     `json:"channel_key"`
	ChannelType             string     `json:"channel_type"`
	ProviderName            string     `json:"provider_name"`
	Model                   string     `json:"model"`
	Mode                    string     `json:"mode"`
	Enabled                 bool       `json:"enabled"`
	ProductionReady         bool       `json:"production_ready"`
	HealthStatus            string     `json:"health_status"`
	FailureCode             string     `json:"failure_code"`
	LastProbeAt             *time.Time `json:"last_probe_at"`
	RPMLimit                int64      `json:"rpm_limit"`
	RPMUsed                 int64      `json:"rpm_used"`
	ActiveTaskCount         int64      `json:"active_task_count"`
	TaskCapacity            int64      `json:"task_capacity"`
	CoolingAccountCount     int64      `json:"cooling_account_count"`
	InvalidAccountCount     int64      `json:"invalid_account_count"`
	BusyAccountCount        int64      `json:"busy_account_count"`
	RateLimitedAccountCount int64      `json:"rate_limited_account_count"`
	SuccessfulTaskCount     int64      `json:"successful_task_count"`
	FailedTaskCount         int64      `json:"failed_task_count"`
	LatencyP50MS            *int64     `json:"latency_p50_ms"`
	LatencyP95MS            *int64     `json:"latency_p95_ms"`
}

type PlatformOperationsAccountPoolSnapshot struct {
	TotalAccounts       int64 `json:"total_accounts"`
	ActiveAccounts      int64 `json:"active_accounts"`
	CoolingAccounts     int64 `json:"cooling_accounts"`
	InvalidAccounts     int64 `json:"invalid_accounts"`
	BusyAccounts        int64 `json:"busy_accounts"`
	RateLimitedAccounts int64 `json:"rate_limited_accounts"`
	ActiveTaskCount     int64 `json:"active_task_count"`
	TaskCapacity        int64 `json:"task_capacity"`
}

type PlatformOperationsTaskSnapshot struct {
	Queued               int64 `json:"queued"`
	Submitting           int64 `json:"submitting"`
	SubmissionUnknown    int64 `json:"submission_unknown"`
	ProviderProcessing   int64 `json:"provider_processing"`
	ArtifactTransferring int64 `json:"artifact_transferring"`
	Succeeded            int64 `json:"succeeded"`
	Failed               int64 `json:"failed"`
	Cancelled            int64 `json:"cancelled"`
	RateLimitedCount     int64 `json:"rate_limited_count"`
	FailoverCount        int64 `json:"failover_count"`
}

type PlatformOperationsDeliverySnapshot struct {
	PendingAlertCount        int64      `json:"pending_alert_count"`
	DeadLetterAlertCount     int64      `json:"dead_letter_alert_count"`
	OldestPendingAlertAt     *time.Time `json:"oldest_pending_alert_at"`
	PendingCostCount         int64      `json:"pending_cost_count"`
	DeadLetterCostCount      int64      `json:"dead_letter_cost_count"`
	PendingTaskStageCount    int64      `json:"pending_task_stage_count"`
	DeadLetterTaskStageCount int64      `json:"dead_letter_task_stage_count"`
	PendingSnapshotCount     int64      `json:"pending_snapshot_count"`
	DeadLetterSnapshotCount  int64      `json:"dead_letter_snapshot_count"`
}

type PlatformOperationsCostSnapshot struct {
	SuccessfulRelayJobs             int64 `json:"successful_relay_jobs"`
	ExplicitCostRelayJobs           int64 `json:"explicit_cost_relay_jobs"`
	DeliveredCostRelayJobs          int64 `json:"delivered_cost_relay_jobs"`
	IncompleteRelayJobs             int64 `json:"incomplete_relay_jobs"`
	NativeBillingReconciliationJobs int64 `json:"native_billing_reconciliation_jobs"`
	ReconciliationComplete          bool  `json:"reconciliation_complete"`
}

// PlatformOperationsSnapshot is generated exclusively from persisted Relay
// routing, account-pool, monitor, job, and delivery state. It has no fallback
// or synthetic/demo path.
type PlatformOperationsSnapshot struct {
	SchemaVersion          int                                   `json:"schema_version"`
	ObservedAt             time.Time                             `json:"observed_at"`
	ExpiresAt              time.Time                             `json:"expires_at"`
	WindowStartedAt        time.Time                             `json:"window_started_at"`
	MonitorFresh           bool                                  `json:"monitor_fresh"`
	MonitorLastCompletedAt *time.Time                            `json:"monitor_last_completed_at"`
	Routes                 []PlatformOperationsRouteSnapshot     `json:"routes"`
	AccountPool            PlatformOperationsAccountPoolSnapshot `json:"account_pool"`
	Tasks                  PlatformOperationsTaskSnapshot        `json:"tasks"`
	Deliveries             PlatformOperationsDeliverySnapshot    `json:"deliveries"`
	Costs                  PlatformOperationsCostSnapshot        `json:"costs"`
}

func (snapshot PlatformOperationsSnapshot) Validate() error {
	if snapshot.SchemaVersion != 1 || snapshot.ObservedAt.IsZero() || snapshot.ExpiresAt.IsZero() ||
		snapshot.WindowStartedAt.IsZero() || !snapshot.ExpiresAt.After(snapshot.ObservedAt) ||
		!snapshot.WindowStartedAt.Before(snapshot.ObservedAt) {
		return fmt.Errorf("operations snapshot time envelope is invalid")
	}
	if snapshot.Routes == nil {
		return fmt.Errorf("operations snapshot routes must be an array")
	}
	if len(snapshot.Routes) > 5000 {
		return fmt.Errorf("operations snapshot has too many routes")
	}
	if snapshot.MonitorLastCompletedAt != nil && snapshot.MonitorLastCompletedAt.After(snapshot.ObservedAt) {
		return fmt.Errorf("operations snapshot monitor timestamp is in the future")
	}
	if snapshot.Deliveries.OldestPendingAlertAt != nil && snapshot.Deliveries.OldestPendingAlertAt.After(snapshot.ObservedAt) {
		return fmt.Errorf("operations snapshot alert timestamp is in the future")
	}
	routeIDs := make(map[int64]struct{}, len(snapshot.Routes))
	for _, route := range snapshot.Routes {
		if route.RouteID <= 0 || route.ChannelKey == "" || strings.TrimSpace(route.ChannelKey) != route.ChannelKey ||
			utf8.RuneCountInString(route.ChannelKey) > 120 || strings.TrimSpace(route.ProviderName) == "" ||
			utf8.RuneCountInString(route.ProviderName) > 120 || strings.TrimSpace(route.Model) == "" ||
			utf8.RuneCountInString(route.Model) > 160 || strings.TrimSpace(route.Mode) == "" ||
			utf8.RuneCountInString(route.Mode) > 64 || utf8.RuneCountInString(route.FailureCode) > 160 {
			return fmt.Errorf("operations route identity is invalid")
		}
		if _, exists := routeIDs[route.RouteID]; exists {
			return fmt.Errorf("operations snapshot route ids must be unique")
		}
		routeIDs[route.RouteID] = struct{}{}
		switch route.ChannelType {
		case "reverse", "third_party_api", "official":
		default:
			return fmt.Errorf("operations route channel_type is invalid")
		}
		switch route.HealthStatus {
		case "unknown", "healthy", "failed", "invalidated", "cooling", "disabled":
		default:
			return fmt.Errorf("operations route health_status is invalid")
		}
		values := []int64{route.RPMLimit, route.RPMUsed, route.ActiveTaskCount, route.TaskCapacity,
			route.CoolingAccountCount, route.InvalidAccountCount, route.BusyAccountCount,
			route.RateLimitedAccountCount, route.SuccessfulTaskCount, route.FailedTaskCount}
		for _, value := range values {
			if value < 0 {
				return fmt.Errorf("operations route counters must not be negative")
			}
		}
		if route.LatencyP50MS != nil && *route.LatencyP50MS < 0 ||
			route.LatencyP95MS != nil && *route.LatencyP95MS < 0 ||
			route.LatencyP50MS != nil && route.LatencyP95MS != nil && *route.LatencyP95MS < *route.LatencyP50MS {
			return fmt.Errorf("operations route latency is invalid")
		}
	}
	aggregates := []int64{
		snapshot.AccountPool.TotalAccounts, snapshot.AccountPool.ActiveAccounts,
		snapshot.AccountPool.CoolingAccounts, snapshot.AccountPool.InvalidAccounts,
		snapshot.AccountPool.BusyAccounts, snapshot.AccountPool.RateLimitedAccounts,
		snapshot.AccountPool.ActiveTaskCount, snapshot.AccountPool.TaskCapacity,
		snapshot.Tasks.Queued, snapshot.Tasks.Submitting, snapshot.Tasks.SubmissionUnknown,
		snapshot.Tasks.ProviderProcessing, snapshot.Tasks.ArtifactTransferring,
		snapshot.Tasks.Succeeded, snapshot.Tasks.Failed, snapshot.Tasks.Cancelled,
		snapshot.Tasks.RateLimitedCount, snapshot.Tasks.FailoverCount,
		snapshot.Deliveries.PendingAlertCount, snapshot.Deliveries.DeadLetterAlertCount,
		snapshot.Deliveries.PendingCostCount, snapshot.Deliveries.DeadLetterCostCount,
		snapshot.Deliveries.PendingTaskStageCount, snapshot.Deliveries.DeadLetterTaskStageCount,
		snapshot.Deliveries.PendingSnapshotCount, snapshot.Deliveries.DeadLetterSnapshotCount,
		snapshot.Costs.SuccessfulRelayJobs, snapshot.Costs.ExplicitCostRelayJobs,
		snapshot.Costs.DeliveredCostRelayJobs, snapshot.Costs.IncompleteRelayJobs,
		snapshot.Costs.NativeBillingReconciliationJobs,
	}
	for _, value := range aggregates {
		if value < 0 {
			return fmt.Errorf("operations snapshot counters must not be negative")
		}
	}
	for _, value := range []int64{
		snapshot.AccountPool.ActiveAccounts, snapshot.AccountPool.CoolingAccounts,
		snapshot.AccountPool.InvalidAccounts, snapshot.AccountPool.BusyAccounts,
		snapshot.AccountPool.RateLimitedAccounts,
	} {
		if value > snapshot.AccountPool.TotalAccounts {
			return fmt.Errorf("operations account status count exceeds total accounts")
		}
	}
	return nil
}
