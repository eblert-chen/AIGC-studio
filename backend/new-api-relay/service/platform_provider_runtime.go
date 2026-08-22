package service

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"gorm.io/gorm"
)

const (
	platformProviderRuntimeDefaultInterval       = 30 * time.Second
	platformProviderRuntimeDefaultLease          = 120 * time.Second
	platformProviderRuntimeDefaultProbeTimeout   = 5 * time.Second
	platformProviderRuntimeDefaultAcquireRetry   = 5 * time.Second
	platformProviderRuntimeDefaultNativeTestAge  = 5 * time.Minute
	platformProviderRuntimeDefaultOutcomeWindow  = 5 * time.Minute
	platformProviderRuntimeDefaultBatchThreshold = 3
	platformProviderRuntimeDefaultDeliveryLease  = 60 * time.Second
	platformProviderRuntimeDefaultDeliveryPoll   = 500 * time.Millisecond
	platformProviderRuntimeMaximumDuration       = 24 * time.Hour
	platformProviderRuntimeMinimumDeliveryLease  = 2 * time.Second
	platformProviderRuntimeMinimumDeliveryPoll   = 50 * time.Millisecond
	platformProviderRuntimeDefaultRecoveryRate   = 0.95
	platformProviderRuntimeDefaultRecoveryRoutes = 0.20
)

type platformProviderRuntimeConfiguration struct {
	Enabled             bool
	Production          bool
	Monitor             PlatformProviderMonitorWorkerOptions
	MaximumFreshness    time.Duration
	Alert               PlatformProviderAlertSinkConfig
	AlertConfigured     bool
	AlertLease          time.Duration
	AlertPoll           time.Duration
	Cost                PlatformChannelCostSinkConfig
	CostConfigured      bool
	ContractRates       []dto.PlatformProviderContractRateInput
	CostLease           time.Duration
	CostPoll            time.Duration
	Telemetry           PlatformTelemetrySinkConfig
	TelemetryConfigured bool
	TelemetryLease      time.Duration
	TelemetryPoll       time.Duration
}

// PlatformProviderReadinessSummary is diagnostic state, not an API admission
// decision. In particular, provider incidents and delivery backlog set
// Degraded but do not turn a provider-only outage into an HTTP 503.
type PlatformProviderReadinessSummary struct {
	Enabled                         bool       `json:"enabled"`
	Degraded                        bool       `json:"degraded"`
	MonitorFresh                    bool       `json:"monitor_fresh"`
	MonitorLastCompletedAt          *time.Time `json:"monitor_last_completed_at"`
	MonitorFreshnessSeconds         int64      `json:"monitor_freshness_seconds"`
	MonitorLastWorkerErrorCode      string     `json:"monitor_last_worker_error_code"`
	ActiveAlerts                    int64      `json:"active_alerts"`
	UnavailableRoutes               int64      `json:"unavailable_routes"`
	AlertBacklog                    int64      `json:"alert_backlog"`
	AlertDeadLetter                 int64      `json:"alert_dead_letter"`
	CostIncomplete                  int64      `json:"cost_incomplete"`
	CostBacklog                     int64      `json:"cost_backlog"`
	CostDeadLetter                  int64      `json:"cost_dead_letter"`
	CostSuccessfulRelayJobs         int64      `json:"cost_successful_relay_jobs"`
	CostExplicitRelayJobs           int64      `json:"cost_explicit_relay_jobs"`
	NativeBillingReconciliationJobs int64      `json:"native_billing_reconciliation_jobs"`
	CostReconciliationComplete      bool       `json:"cost_reconciliation_complete"`
	TaskStageBacklog                int64      `json:"task_stage_backlog"`
	TaskStageDeadLetter             int64      `json:"task_stage_dead_letter"`
	OperationsSnapshotBacklog       int64      `json:"operations_snapshot_backlog"`
	OperationsSnapshotDeadLetter    int64      `json:"operations_snapshot_dead_letter"`
}

type platformProviderRuntimeCoordinator struct {
	mu      sync.Mutex
	cancel  context.CancelFunc
	done    chan struct{}
	running bool
}

var platformProviderRuntimeWorkers platformProviderRuntimeCoordinator

var platformProviderRuntimeConfigurationCache struct {
	sync.RWMutex
	fingerprint [32]byte
	loaded      bool
	value       platformProviderRuntimeConfiguration
}

// ValidatePlatformProviderRuntimeConfiguration is the startup fail-closed
// gate. Production compatibility Relay processes must run native scheduled
// channel tests and monitoring, and must have signed alert, cost, and telemetry
// sinks. This prevents monitoring from accepting stale native TestTime rows.
func ValidatePlatformProviderRuntimeConfiguration() error {
	if !PlatformRelayCompatEnabled() {
		return nil
	}
	configuration, err := getPlatformProviderRuntimeConfiguration()
	if err != nil {
		return err
	}
	if len(configuration.ContractRates) > 0 {
		return model.SyncPlatformProviderContractRates(configuration.ContractRates)
	}
	return nil
}

// StartPlatformProviderRuntimeWorkers starts identical workers in every Relay
// process. Database-clock leases and random claim tokens provide cross-process
// ownership and stale-worker fencing.
func StartPlatformProviderRuntimeWorkers() error {
	if !PlatformRelayCompatEnabled() {
		return nil
	}
	configuration, err := getPlatformProviderRuntimeConfiguration()
	if err != nil {
		return fmt.Errorf("platform provider runtime configuration rejected: %w", err)
	}
	if !configuration.Enabled {
		return nil
	}
	if len(configuration.ContractRates) > 0 {
		if err := model.SyncPlatformProviderContractRates(configuration.ContractRates); err != nil {
			return fmt.Errorf("platform provider contract rates rejected: %w", err)
		}
	}
	return platformProviderRuntimeWorkers.start(configuration)
}

func (coordinator *platformProviderRuntimeCoordinator) start(configuration platformProviderRuntimeConfiguration) error {
	workers := []func(context.Context){
		func(ctx context.Context) {
			if err := RunPlatformProviderMonitorWorker(ctx, configuration.Monitor); err != nil && !errors.Is(err, context.Canceled) {
				common.SysError("platform provider monitor worker stopped: " + err.Error())
			}
		},
	}
	if configuration.AlertConfigured {
		workers = append(workers, func(ctx context.Context) {
			runPlatformProviderDeliveryWorker(
				ctx,
				model.PlatformRelayDeliveryKindProviderAlert,
				configuration.AlertLease,
				configuration.AlertPoll,
				func(ctx context.Context, claim model.PlatformRelayDeliveryClaim) (string, bool, error) {
					return DeliverPlatformProviderAlertClaim(ctx, claim, configuration.Alert)
				},
			)
		})
	}
	if configuration.CostConfigured {
		workers = append(workers,
			func(ctx context.Context) {
				runPlatformChannelCostReconciliationWorker(ctx, configuration.CostLease, configuration.CostPoll)
			},
			func(ctx context.Context) {
				runPlatformProviderDeliveryWorker(
					ctx,
					model.PlatformRelayDeliveryKindChannelCost,
					configuration.CostLease,
					configuration.CostPoll,
					func(ctx context.Context, claim model.PlatformRelayDeliveryClaim) (string, bool, error) {
						return DeliverPlatformChannelCostClaim(ctx, claim, configuration.Cost)
					},
				)
			},
		)
	}
	if configuration.TelemetryConfigured {
		workers = append(workers,
			func(ctx context.Context) {
				runPlatformProviderDeliveryWorker(
					ctx,
					model.PlatformRelayDeliveryKindTaskStage,
					configuration.TelemetryLease,
					configuration.TelemetryPoll,
					func(ctx context.Context, claim model.PlatformRelayDeliveryClaim) (string, bool, error) {
						return DeliverPlatformTaskStageClaim(ctx, claim, configuration.Telemetry)
					},
				)
			},
			func(ctx context.Context) {
				runPlatformProviderDeliveryWorker(
					ctx,
					model.PlatformRelayDeliveryKindOperationsSnapshot,
					configuration.TelemetryLease,
					configuration.TelemetryPoll,
					func(ctx context.Context, claim model.PlatformRelayDeliveryClaim) (string, bool, error) {
						return DeliverPlatformOperationsSnapshotClaim(ctx, claim, configuration.Telemetry)
					},
				)
			},
		)
	}
	return coordinator.startWorkers(workers...)
}

func (coordinator *platformProviderRuntimeCoordinator) startWorkers(workers ...func(context.Context)) error {
	if len(workers) == 0 {
		return errors.New("platform provider runtime requires at least one worker")
	}
	for _, worker := range workers {
		if worker == nil {
			return errors.New("platform provider runtime worker is invalid")
		}
	}
	coordinator.mu.Lock()
	defer coordinator.mu.Unlock()
	if coordinator.running {
		return nil
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	coordinator.cancel = cancel
	coordinator.done = done
	coordinator.running = true
	var waitGroup sync.WaitGroup
	launch := func(run func()) {
		waitGroup.Add(1)
		go func() {
			defer waitGroup.Done()
			run()
		}()
	}
	for _, worker := range workers {
		worker := worker
		launch(func() { worker(ctx) })
	}
	go func() {
		waitGroup.Wait()
		coordinator.mu.Lock()
		if coordinator.done == done {
			coordinator.running = false
			coordinator.cancel = nil
			close(done)
		}
		coordinator.mu.Unlock()
	}()
	return nil
}

// StopPlatformProviderRuntimeWorkers cancels and joins every provider monitor,
// alert, cost and telemetry loop. A lifecycle-loss caller supplies one shared
// hard deadline so an in-flight upstream request cannot delay process exit.
func StopPlatformProviderRuntimeWorkers(ctx context.Context) error {
	return platformProviderRuntimeWorkers.stop(ctx)
}

func (coordinator *platformProviderRuntimeCoordinator) stop(ctx context.Context) error {
	if ctx == nil {
		return errors.New("platform provider runtime stop context is required")
	}
	coordinator.mu.Lock()
	if !coordinator.running {
		coordinator.mu.Unlock()
		return nil
	}
	cancel := coordinator.cancel
	done := coordinator.done
	coordinator.mu.Unlock()
	cancel()
	select {
	case <-done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// GetPlatformProviderReadinessSummary returns provider monitoring and cost
// reconciliation diagnostics. Provider failures are represented as Degraded;
// only inability to inspect the database/configuration is returned as error.
func GetPlatformProviderReadinessSummary(ctx context.Context) (PlatformProviderReadinessSummary, error) {
	var summary PlatformProviderReadinessSummary
	if ctx == nil {
		return summary, fmt.Errorf("provider readiness context is required")
	}
	if err := ctx.Err(); err != nil {
		return summary, err
	}
	if !PlatformRelayCompatEnabled() {
		return summary, nil
	}
	configuration, err := getPlatformProviderRuntimeConfiguration()
	if err != nil {
		return summary, err
	}
	summary.Enabled = configuration.Enabled
	if !configuration.Enabled {
		summary.Degraded = true
		return summary, nil
	}

	monitor, err := GetPlatformProviderMonitorReadiness(true, configuration.MaximumFreshness)
	if err != nil {
		return summary, err
	}
	if err := ctx.Err(); err != nil {
		return summary, err
	}
	cost, err := model.GetPlatformChannelCostReconciliationSummary()
	if err != nil {
		return summary, err
	}
	summary.MonitorFresh = monitor.Fresh
	summary.MonitorLastCompletedAt = monitor.LastCompletedAt
	summary.MonitorFreshnessSeconds = monitor.FreshnessSeconds
	summary.MonitorLastWorkerErrorCode = monitor.LastWorkerErrorCode
	summary.ActiveAlerts = monitor.ActiveIncidents
	summary.UnavailableRoutes = monitor.UnavailableRoutes
	summary.AlertBacklog = monitor.AlertPending + monitor.AlertClaimed
	summary.AlertDeadLetter = monitor.AlertDeadLetter
	summary.CostIncomplete = cost.IncompleteRelayJobs
	summary.CostBacklog = cost.PendingDeliveries + cost.ClaimedDeliveries
	summary.CostDeadLetter = cost.DeadLetterDeliveries
	summary.CostSuccessfulRelayJobs = cost.SuccessfulRelayJobs
	summary.CostExplicitRelayJobs = cost.ExplicitCostRelayJobs
	summary.NativeBillingReconciliationJobs = cost.NativeBillingReconciliationJobs
	// An empty ledger proves no integration. Keep the migration gate
	// incomplete until at least one successful provider outcome has matching,
	// delivered, evidence-backed cost (including an explicit zero amount).
	summary.CostReconciliationComplete = cost.SuccessfulRelayJobs > 0 && cost.ReconciliationComplete
	stageCounts, err := model.GetPlatformRelayDeliveryCounts(model.PlatformRelayDeliveryKindTaskStage)
	if err != nil {
		return summary, err
	}
	snapshotCounts, err := model.GetPlatformRelayDeliveryCounts(model.PlatformRelayDeliveryKindOperationsSnapshot)
	if err != nil {
		return summary, err
	}
	summary.TaskStageBacklog = stageCounts.Pending + stageCounts.Claimed
	summary.TaskStageDeadLetter = stageCounts.DeadLetter
	summary.OperationsSnapshotBacklog = snapshotCounts.Pending + snapshotCounts.Claimed
	summary.OperationsSnapshotDeadLetter = snapshotCounts.DeadLetter
	summary.Degraded = monitor.Degraded || summary.CostIncomplete > 0 ||
		summary.NativeBillingReconciliationJobs > 0 || summary.CostBacklog > 0 ||
		summary.CostDeadLetter > 0 || !summary.CostReconciliationComplete ||
		summary.TaskStageBacklog > 0 || summary.TaskStageDeadLetter > 0 ||
		summary.OperationsSnapshotBacklog > 0 || summary.OperationsSnapshotDeadLetter > 0
	return summary, nil
}

type platformProviderRuntimeChannelProber struct {
	MaximumTestAge time.Duration
}

// ProbePlatformProviderRoute consumes the channel state maintained by new-api's
// native channel test/auto-disable machinery. It also verifies that the fixed
// route still refers to the same real key without ever persisting or logging
// credential material.
func (prober platformProviderRuntimeChannelProber) ProbePlatformProviderRoute(
	ctx context.Context,
	route model.PlatformGenerationProviderRoute,
) (dto.PlatformProviderRouteProbeResult, error) {
	if err := ctx.Err(); err != nil {
		return dto.PlatformProviderRouteProbeResult{}, err
	}
	channel, err := model.GetChannelById(route.ChannelID, true)
	if err != nil {
		return dto.PlatformProviderRouteProbeResult{}, err
	}
	keys := channel.GetKeys()
	if route.KeyIndex < 0 || route.KeyIndex >= len(keys) {
		return platformProviderRuntimeFailedProbe("route_key_missing", false), nil
	}
	digest := sha256.Sum256([]byte(keys[route.KeyIndex]))
	actualFingerprint := fmt.Sprintf("%x", digest)
	if subtle.ConstantTimeCompare([]byte(actualFingerprint), []byte(route.KeyFingerprint)) != 1 {
		return platformProviderRuntimeFailedProbe("route_key_drift", false), nil
	}
	if !route.Enabled {
		return platformProviderRuntimeFailedProbe("route_disabled", false), nil
	}
	status := channel.Status
	if channel.ChannelInfo.IsMultiKey && channel.ChannelInfo.MultiKeyStatusList != nil {
		if keyStatus, exists := channel.ChannelInfo.MultiKeyStatusList[route.KeyIndex]; exists {
			status = keyStatus
		}
	}
	switch status {
	case common.ChannelStatusEnabled:
		var databaseTime time.Time
		if err := model.DB.Transaction(func(tx *gorm.DB) error {
			var clockErr error
			databaseTime, clockErr = model.GetDBTimeTx(tx)
			return clockErr
		}); err != nil {
			return dto.PlatformProviderRouteProbeResult{}, err
		}
		lastNativeTest := time.Unix(channel.TestTime, 0).UTC()
		age := databaseTime.Sub(lastNativeTest)
		if channel.TestTime <= 0 || age < -time.Minute || age > prober.MaximumTestAge {
			return platformProviderRuntimeFailedProbe("channel_probe_stale", false), nil
		}
		return dto.PlatformProviderRouteProbeResult{Status: dto.PlatformProviderProbeHealthy}, nil
	case common.ChannelStatusAutoDisabled:
		return platformProviderRuntimeFailedProbe("channel_auto_disabled", true), nil
	case common.ChannelStatusManuallyDisabled:
		return platformProviderRuntimeFailedProbe("channel_manually_disabled", false), nil
	default:
		return platformProviderRuntimeFailedProbe("channel_status_unknown", false), nil
	}
}

func platformProviderRuntimeFailedProbe(code string, providerCaused bool) dto.PlatformProviderRouteProbeResult {
	return dto.PlatformProviderRouteProbeResult{
		Status:         dto.PlatformProviderProbeFailed,
		FailureCode:    code,
		ProviderCaused: providerCaused,
	}
}

func runPlatformProviderDeliveryWorker(
	ctx context.Context,
	eventKind string,
	lease time.Duration,
	poll time.Duration,
	deliver func(context.Context, model.PlatformRelayDeliveryClaim) (string, bool, error),
) {
	for {
		if err := ctx.Err(); err != nil {
			return
		}
		claim, err := model.ClaimPlatformRelayExternalDelivery(eventKind, lease)
		if errors.Is(err, gorm.ErrRecordNotFound) {
			if !waitPlatformProviderMonitor(ctx, poll) {
				return
			}
			continue
		}
		if err != nil {
			common.SysError("platform provider delivery claim failed for " + eventKind + ": " + err.Error())
			if !waitPlatformProviderMonitor(ctx, poll) {
				return
			}
			continue
		}
		if _, _, err := deliver(ctx, *claim); err != nil {
			common.SysError("platform provider delivery failed for " + eventKind + ": " + err.Error())
		}
	}
}

func loadPlatformProviderRuntimeConfiguration() (platformProviderRuntimeConfiguration, error) {
	production := PlatformRelayProductionSecurityEnabled()
	enabled, err := platformProviderRuntimeBoolean("RELAY_PROVIDER_MONITOR_ENABLED", false)
	if err != nil {
		return platformProviderRuntimeConfiguration{}, err
	}
	configuration := platformProviderRuntimeConfiguration{
		Enabled:    enabled,
		Production: production,
	}
	if production && !enabled {
		return configuration, fmt.Errorf("production requires RELAY_PROVIDER_MONITOR_ENABLED=true")
	}
	if !enabled {
		return configuration, nil
	}

	interval, err := platformProviderRuntimeSeconds("RELAY_PROVIDER_MONITOR_INTERVAL_SECONDS", platformProviderRuntimeDefaultInterval, time.Second, platformProviderRuntimeMaximumDuration)
	if err != nil {
		return configuration, err
	}
	lease, err := platformProviderRuntimeSeconds("RELAY_PROVIDER_MONITOR_LEASE_SECONDS", platformProviderRuntimeDefaultLease, 30*time.Second, platformProviderRuntimeMaximumDuration)
	if err != nil {
		return configuration, err
	}
	probeTimeout, err := platformProviderRuntimeSeconds("RELAY_PROVIDER_HEALTHCHECK_TIMEOUT_SECONDS", platformProviderRuntimeDefaultProbeTimeout, time.Second, time.Minute)
	if err != nil {
		return configuration, err
	}
	acquireRetry, err := platformProviderRuntimeSeconds("RELAY_PROVIDER_MONITOR_ACQUIRE_RETRY_SECONDS", platformProviderRuntimeDefaultAcquireRetry, time.Second, time.Minute)
	if err != nil {
		return configuration, err
	}
	if interval >= lease || probeTimeout >= lease {
		return configuration, fmt.Errorf("provider monitor interval and probe timeout must be shorter than its lease")
	}
	nativeTestAge, err := platformProviderRuntimeSeconds("RELAY_PROVIDER_CHANNEL_TEST_MAX_AGE_SECONDS", platformProviderRuntimeDefaultNativeTestAge, interval, platformProviderRuntimeMaximumDuration)
	if err != nil {
		return configuration, err
	}
	if production {
		nodeTypeRaw := os.Getenv("NODE_TYPE")
		if nodeTypeRaw != "master" {
			return configuration, fmt.Errorf("production provider monitoring requires NODE_TYPE=master so native scheduled channel tests run on this deployment")
		}
		channelTestEnabledRaw := os.Getenv("CHANNEL_TEST_ENABLED")
		channelTestEnabled, parseErr := strconv.ParseBool(channelTestEnabledRaw)
		if parseErr != nil || !channelTestEnabled || channelTestEnabledRaw != "true" {
			return configuration, fmt.Errorf("production provider monitoring requires CHANNEL_TEST_ENABLED=true")
		}
		channelTestFrequencyRaw := os.Getenv("CHANNEL_TEST_FREQUENCY")
		channelTestFrequencyMinutes, parseErr := strconv.Atoi(channelTestFrequencyRaw)
		if parseErr != nil || channelTestFrequencyMinutes <= 0 || strings.TrimSpace(channelTestFrequencyRaw) != channelTestFrequencyRaw {
			return configuration, fmt.Errorf("production provider monitoring requires CHANNEL_TEST_FREQUENCY to be a normalized positive integer minute interval")
		}
		maximumChannelTestMinutes := int64(platformProviderRuntimeMaximumDuration / time.Minute)
		if int64(channelTestFrequencyMinutes) > maximumChannelTestMinutes {
			return configuration, fmt.Errorf("CHANNEL_TEST_FREQUENCY plus scheduler delay must be shorter than RELAY_PROVIDER_CHANNEL_TEST_MAX_AGE_SECONDS")
		}
		channelTestInterval := time.Duration(channelTestFrequencyMinutes) * time.Minute
		if channelTestInterval+systemTaskSchedulerInterval >= nativeTestAge {
			return configuration, fmt.Errorf("CHANNEL_TEST_FREQUENCY plus scheduler delay must be shorter than RELAY_PROVIDER_CHANNEL_TEST_MAX_AGE_SECONDS")
		}
	}

	policy, err := loadPlatformProviderRuntimePolicy()
	if err != nil {
		return configuration, err
	}
	ownerID := platformProviderRuntimeOwnerID()
	configuration.Monitor = PlatformProviderMonitorWorkerOptions{
		OwnerID:      ownerID,
		Lease:        lease,
		Interval:     interval,
		AcquireRetry: acquireRetry,
		ProbeTimeout: probeTimeout,
		Policy:       policy,
		Prober:       platformProviderRuntimeChannelProber{MaximumTestAge: nativeTestAge},
	}
	if err := configuration.Monitor.Validate(); err != nil {
		return configuration, err
	}
	configuration.MaximumFreshness = lease
	if candidate := interval * 3; candidate > configuration.MaximumFreshness {
		configuration.MaximumFreshness = candidate
	}

	alertURLRaw := os.Getenv("RELAY_PROVIDER_ALERT_WEBHOOK_URL")
	alertURL := strings.TrimSpace(alertURLRaw)
	alertSecret := platformRelayRuntimeSecretOrEnvironment("RELAY_PROVIDER_ALERT_SIGNING_SECRET")
	if alertURLRaw != alertURL {
		return configuration, fmt.Errorf("RELAY_PROVIDER_ALERT_WEBHOOK_URL must be normalized")
	}
	configuration.AlertConfigured = alertURL != "" || alertSecret != ""
	if (alertURL == "") != (alertSecret == "") {
		return configuration, fmt.Errorf("RELAY_PROVIDER_ALERT_WEBHOOK_URL and RELAY_PROVIDER_ALERT_SIGNING_SECRET must be configured together")
	}
	if configuration.AlertConfigured {
		configuration.Alert = PlatformProviderAlertSinkConfig{URL: alertURL, SigningSecret: alertSecret, Production: production}
		if err := configuration.Alert.Validate(); err != nil {
			return configuration, err
		}
		if production && platformRelaySecretIsPlaceholder(alertSecret) {
			return configuration, fmt.Errorf("production provider alert signing secret is a placeholder")
		}
	}
	if production && !configuration.AlertConfigured {
		return configuration, fmt.Errorf("production requires an external signed provider alert sink")
	}

	costURLRaw := os.Getenv("RELAY_PLATFORM_CHANNEL_COST_URL")
	costURL := strings.TrimSpace(costURLRaw)
	costToken := platformRelayRuntimeSecretOrEnvironment("RELAY_PLATFORM_INTERNAL_SERVICE_TOKEN")
	costSigningSecret := platformRelayRuntimeSecretOrEnvironment("RELAY_PLATFORM_CHANNEL_COST_SIGNING_SECRET")
	if costURLRaw != costURL {
		return configuration, fmt.Errorf("RELAY_PLATFORM_CHANNEL_COST_URL must be normalized")
	}
	if strings.TrimSpace(costToken) != costToken || strings.TrimSpace(costSigningSecret) != costSigningSecret {
		return configuration, fmt.Errorf("Platform channel-cost credentials must be normalized")
	}
	configuration.CostConfigured = costURL != "" || costToken != "" || costSigningSecret != ""
	if configuration.CostConfigured && (costURL == "" || costToken == "" || costSigningSecret == "") {
		return configuration, fmt.Errorf("RELAY_PLATFORM_CHANNEL_COST_URL, RELAY_PLATFORM_INTERNAL_SERVICE_TOKEN, and RELAY_PLATFORM_CHANNEL_COST_SIGNING_SECRET must be configured together")
	}
	if configuration.CostConfigured {
		configuration.Cost = PlatformChannelCostSinkConfig{
			URL:                  costURL,
			InternalServiceToken: costToken,
			SigningSecret:        costSigningSecret,
			Production:           production,
		}
		if err := configuration.Cost.Validate(); err != nil {
			return configuration, err
		}
		if production && configuration.AlertConfigured && subtle.ConstantTimeCompare([]byte(costSigningSecret), []byte(alertSecret)) == 1 {
			return configuration, fmt.Errorf("channel cost and provider alert signing secrets must be independent")
		}
	}
	if production && !configuration.CostConfigured {
		return configuration, fmt.Errorf("production requires the Platform channel-cost URL, internal service token, and independent signing secret")
	}
	contractRatesRaw := strings.TrimSpace(os.Getenv("RELAY_PROVIDER_CONTRACT_RATES_JSON"))
	if contractRatesRaw != "" {
		if !configuration.CostConfigured {
			return configuration, fmt.Errorf("RELAY_PROVIDER_CONTRACT_RATES_JSON requires the Platform channel-cost sink")
		}
		if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(contractRatesRaw), &configuration.ContractRates); err != nil {
			return configuration, fmt.Errorf("RELAY_PROVIDER_CONTRACT_RATES_JSON is invalid: %w", err)
		}
		if len(configuration.ContractRates) == 0 {
			return configuration, fmt.Errorf("RELAY_PROVIDER_CONTRACT_RATES_JSON must contain at least one contract rate")
		}
		identities := make(map[string]struct{}, len(configuration.ContractRates)*2)
		for _, rate := range configuration.ContractRates {
			if err := rate.Validate(); err != nil {
				return configuration, fmt.Errorf("RELAY_PROVIDER_CONTRACT_RATES_JSON is invalid: %w", err)
			}
			scope := fmt.Sprintf(
				"%s\x00%d\x00%s\x00%s\x00%s\x00%s",
				rate.ProviderName,
				rate.ChannelID,
				rate.UpstreamModel,
				rate.Mode,
				rate.Resolution,
				rate.EffectiveFrom.UTC().Format(time.RFC3339Nano),
			)
			for _, identity := range []string{"id:" + rate.ID, "scope:" + scope} {
				if _, duplicate := identities[identity]; duplicate {
					return configuration, fmt.Errorf("RELAY_PROVIDER_CONTRACT_RATES_JSON contains a duplicate immutable identity")
				}
				identities[identity] = struct{}{}
			}
		}
	}

	taskStageURLRaw := os.Getenv("RELAY_PLATFORM_TASK_STAGE_URL")
	taskStageURL := strings.TrimSpace(taskStageURLRaw)
	operationsSnapshotURLRaw := os.Getenv("RELAY_PLATFORM_OPERATIONS_SNAPSHOT_URL")
	operationsSnapshotURL := strings.TrimSpace(operationsSnapshotURLRaw)
	telemetrySigningSecret := platformRelayRuntimeSecretOrEnvironment("RELAY_TELEMETRY_SIGNING_SECRET")
	if taskStageURLRaw != taskStageURL || operationsSnapshotURLRaw != operationsSnapshotURL ||
		strings.TrimSpace(telemetrySigningSecret) != telemetrySigningSecret {
		return configuration, fmt.Errorf("Platform telemetry configuration must be normalized")
	}
	configuration.TelemetryConfigured = taskStageURL != "" || operationsSnapshotURL != "" || telemetrySigningSecret != ""
	if configuration.TelemetryConfigured && (taskStageURL == "" || operationsSnapshotURL == "" ||
		costToken == "" || telemetrySigningSecret == "") {
		return configuration, fmt.Errorf("RELAY_PLATFORM_TASK_STAGE_URL, RELAY_PLATFORM_OPERATIONS_SNAPSHOT_URL, RELAY_PLATFORM_INTERNAL_SERVICE_TOKEN, and RELAY_TELEMETRY_SIGNING_SECRET must be configured together")
	}
	if configuration.TelemetryConfigured {
		configuration.Telemetry = PlatformTelemetrySinkConfig{
			TaskStageURL:          taskStageURL,
			OperationsSnapshotURL: operationsSnapshotURL,
			InternalServiceToken:  costToken,
			SigningSecret:         telemetrySigningSecret,
			Production:            production,
		}
		if err := configuration.Telemetry.Validate(); err != nil {
			return configuration, err
		}
		if production && configuration.AlertConfigured &&
			subtle.ConstantTimeCompare([]byte(telemetrySigningSecret), []byte(alertSecret)) == 1 {
			return configuration, fmt.Errorf("telemetry and provider alert signing secrets must be independent")
		}
		if production && configuration.CostConfigured &&
			subtle.ConstantTimeCompare([]byte(telemetrySigningSecret), []byte(costSigningSecret)) == 1 {
			return configuration, fmt.Errorf("telemetry and channel cost signing secrets must be independent")
		}
	}
	if production && !configuration.TelemetryConfigured {
		return configuration, fmt.Errorf("production requires signed Platform task-stage and operations-snapshot telemetry sinks")
	}

	configuration.AlertLease, err = platformProviderRuntimeSeconds("RELAY_PROVIDER_ALERT_CLAIM_LEASE_SECONDS", platformProviderRuntimeDefaultDeliveryLease, platformProviderRuntimeMinimumDeliveryLease, platformProviderRuntimeMaximumDuration)
	if err != nil {
		return configuration, err
	}
	configuration.AlertPoll, err = platformProviderRuntimeSeconds("RELAY_PROVIDER_ALERT_POLL_SECONDS", platformProviderRuntimeDefaultDeliveryPoll, platformProviderRuntimeMinimumDeliveryPoll, time.Minute)
	if err != nil {
		return configuration, err
	}
	configuration.CostLease, err = platformProviderRuntimeSeconds("RELAY_CHANNEL_COST_CLAIM_LEASE_SECONDS", platformProviderRuntimeDefaultDeliveryLease, platformProviderRuntimeMinimumDeliveryLease, platformProviderRuntimeMaximumDuration)
	if err != nil {
		return configuration, err
	}
	configuration.CostPoll, err = platformProviderRuntimeSeconds("RELAY_CHANNEL_COST_POLL_SECONDS", platformProviderRuntimeDefaultDeliveryPoll, platformProviderRuntimeMinimumDeliveryPoll, time.Minute)
	if err != nil {
		return configuration, err
	}
	configuration.TelemetryLease, err = platformProviderRuntimeSeconds("RELAY_TELEMETRY_CLAIM_LEASE_SECONDS", platformProviderRuntimeDefaultDeliveryLease, platformProviderRuntimeMinimumDeliveryLease, platformProviderRuntimeMaximumDuration)
	if err != nil {
		return configuration, err
	}
	configuration.TelemetryPoll, err = platformProviderRuntimeSeconds("RELAY_TELEMETRY_POLL_SECONDS", platformProviderRuntimeDefaultDeliveryPoll, platformProviderRuntimeMinimumDeliveryPoll, time.Minute)
	if err != nil {
		return configuration, err
	}
	if configuration.AlertLease <= platformRelayExternalDeliveryTimeout || configuration.CostLease <= platformRelayExternalDeliveryTimeout ||
		configuration.TelemetryLease <= platformRelayExternalDeliveryTimeout {
		return configuration, fmt.Errorf("external delivery claim leases must exceed the HTTP delivery timeout")
	}
	return configuration, nil
}

func getPlatformProviderRuntimeConfiguration() (platformProviderRuntimeConfiguration, error) {
	fingerprint := platformProviderRuntimeEnvironmentFingerprint()
	platformProviderRuntimeConfigurationCache.RLock()
	if platformProviderRuntimeConfigurationCache.loaded && platformProviderRuntimeConfigurationCache.fingerprint == fingerprint {
		configuration := platformProviderRuntimeConfigurationCache.value
		platformProviderRuntimeConfigurationCache.RUnlock()
		return configuration, nil
	}
	platformProviderRuntimeConfigurationCache.RUnlock()

	configuration, err := loadPlatformProviderRuntimeConfiguration()
	if err != nil {
		return configuration, err
	}
	platformProviderRuntimeConfigurationCache.Lock()
	platformProviderRuntimeConfigurationCache.fingerprint = fingerprint
	platformProviderRuntimeConfigurationCache.value = configuration
	platformProviderRuntimeConfigurationCache.loaded = true
	platformProviderRuntimeConfigurationCache.Unlock()
	return configuration, nil
}

func platformProviderRuntimeEnvironmentFingerprint() [32]byte {
	names := []string{
		"RELAY_COMPAT_ENVIRONMENT",
		"RELAY_PROVIDER_MONITOR_ENABLED",
		"RELAY_PROVIDER_MONITOR_INTERVAL_SECONDS",
		"RELAY_PROVIDER_MONITOR_LEASE_SECONDS",
		"RELAY_PROVIDER_MONITOR_ACQUIRE_RETRY_SECONDS",
		"RELAY_PROVIDER_HEALTHCHECK_TIMEOUT_SECONDS",
		"RELAY_PROVIDER_CHANNEL_TEST_MAX_AGE_SECONDS",
		"CHANNEL_TEST_ENABLED",
		"CHANNEL_TEST_FREQUENCY",
		"RELAY_PROVIDER_MONITOR_WINDOW_SECONDS",
		"RELAY_PROVIDER_MONITOR_MIN_OUTCOMES",
		"RELAY_PROVIDER_MONITOR_MIN_SUCCESS_RATE",
		"RELAY_PROVIDER_MONITOR_SUCCESS_RECOVERY_RATE",
		"RELAY_PROVIDER_MONITOR_WIDESPREAD_FAILURE_RATIO",
		"RELAY_PROVIDER_MONITOR_WIDESPREAD_RECOVERY_RATIO",
		"RELAY_PROVIDER_MONITOR_WIDESPREAD_MIN_ROUTES",
		"RELAY_PROVIDER_MONITOR_BATCH_DISABLED_THRESHOLD",
		"RELAY_PROVIDER_ALERT_WEBHOOK_URL",
		"RELAY_PROVIDER_ALERT_SIGNING_SECRET",
		"RELAY_PROVIDER_ALERT_CLAIM_LEASE_SECONDS",
		"RELAY_PROVIDER_ALERT_POLL_SECONDS",
		"RELAY_PLATFORM_CHANNEL_COST_URL",
		"RELAY_PLATFORM_INTERNAL_SERVICE_TOKEN",
		"RELAY_PLATFORM_CHANNEL_COST_SIGNING_SECRET",
		"RELAY_PROVIDER_CONTRACT_RATES_JSON",
		"RELAY_CHANNEL_COST_CLAIM_LEASE_SECONDS",
		"RELAY_CHANNEL_COST_POLL_SECONDS",
		"RELAY_PLATFORM_TASK_STAGE_URL",
		"RELAY_PLATFORM_OPERATIONS_SNAPSHOT_URL",
		"RELAY_TELEMETRY_SIGNING_SECRET",
		"RELAY_TELEMETRY_CLAIM_LEASE_SECONDS",
		"RELAY_TELEMETRY_POLL_SECONDS",
		"NODE_TYPE",
		"NODE_NAME",
	}
	hash := sha256.New()
	for _, name := range names {
		value := os.Getenv(name)
		switch name {
		case "RELAY_PROVIDER_ALERT_SIGNING_SECRET", "RELAY_PLATFORM_INTERNAL_SERVICE_TOKEN",
			"RELAY_PLATFORM_CHANNEL_COST_SIGNING_SECRET", "RELAY_TELEMETRY_SIGNING_SECRET":
			value = platformRelayRuntimeSecretOrEnvironment(name)
		}
		_, _ = hash.Write([]byte(strconv.Itoa(len(name))))
		_, _ = hash.Write([]byte(":" + name + ":" + strconv.Itoa(len(value)) + ":"))
		_, _ = hash.Write([]byte(value))
	}
	var fingerprint [32]byte
	copy(fingerprint[:], hash.Sum(nil))
	return fingerprint
}

func loadPlatformProviderRuntimePolicy() (PlatformProviderMonitorPolicy, error) {
	policy := DefaultPlatformProviderMonitorPolicy()
	var err error
	policy.OutcomeLookback, err = platformProviderRuntimeSeconds("RELAY_PROVIDER_MONITOR_WINDOW_SECONDS", platformProviderRuntimeDefaultOutcomeWindow, time.Minute, 30*24*time.Hour)
	if err != nil {
		return policy, err
	}
	policy.SuccessRateMinimumSamples, err = platformProviderRuntimeInteger("RELAY_PROVIDER_MONITOR_MIN_OUTCOMES", policy.SuccessRateMinimumSamples, 1, 1_000_000)
	if err != nil {
		return policy, err
	}
	triggerRate, err := platformProviderRuntimeRate("RELAY_PROVIDER_MONITOR_MIN_SUCCESS_RATE", float64(policy.SuccessRateTriggerBasisPoints)/10_000)
	if err != nil {
		return policy, err
	}
	recoveryRate, err := platformProviderRuntimeRate("RELAY_PROVIDER_MONITOR_SUCCESS_RECOVERY_RATE", platformProviderRuntimeDefaultRecoveryRate)
	if err != nil {
		return policy, err
	}
	policy.SuccessRateTriggerBasisPoints = int(triggerRate * 10_000)
	policy.SuccessRateRecoveryBasisPoints = int(recoveryRate * 10_000)

	widespreadRate, err := platformProviderRuntimeRate("RELAY_PROVIDER_MONITOR_WIDESPREAD_FAILURE_RATIO", float64(policy.WidespreadTriggerBasisPoints)/10_000)
	if err != nil {
		return policy, err
	}
	widespreadRecovery, err := platformProviderRuntimeRate("RELAY_PROVIDER_MONITOR_WIDESPREAD_RECOVERY_RATIO", platformProviderRuntimeDefaultRecoveryRoutes)
	if err != nil {
		return policy, err
	}
	policy.WidespreadTriggerBasisPoints = int(widespreadRate * 10_000)
	policy.WidespreadRecoveryBasisPoints = int(widespreadRecovery * 10_000)
	policy.WidespreadMinimumAffectedRoutes, err = platformProviderRuntimeInteger("RELAY_PROVIDER_MONITOR_WIDESPREAD_MIN_ROUTES", policy.WidespreadMinimumAffectedRoutes, 1, 1_000_000)
	if err != nil {
		return policy, err
	}
	policy.WidespreadMinimumRoutes = policy.WidespreadMinimumAffectedRoutes
	policy.BatchInvalidationMinimumRoutes, err = platformProviderRuntimeInteger("RELAY_PROVIDER_MONITOR_BATCH_DISABLED_THRESHOLD", platformProviderRuntimeDefaultBatchThreshold, 2, 1_000_000)
	if err != nil {
		return policy, err
	}
	return policy, policy.Validate()
}

func platformProviderRuntimeOwnerID() string {
	ownerID := strings.TrimSpace(os.Getenv("NODE_NAME"))
	if ownerID == "" {
		hostname, _ := os.Hostname()
		ownerID = strings.TrimSpace(hostname)
	}
	if ownerID == "" {
		ownerID = "new-api"
	}
	ownerID = fmt.Sprintf("%s-provider-monitor-%d", ownerID, os.Getpid())
	if len(ownerID) > 128 {
		ownerID = ownerID[:128]
	}
	return ownerID
}

func platformProviderRuntimeBoolean(name string, fallback bool) (bool, error) {
	raw := strings.TrimSpace(os.Getenv(name))
	if raw == "" {
		return fallback, nil
	}
	value, err := strconv.ParseBool(raw)
	if err != nil {
		return false, fmt.Errorf("%s must be true or false", name)
	}
	return value, nil
}

func platformProviderRuntimeSeconds(name string, fallback time.Duration, minimum time.Duration, maximum time.Duration) (time.Duration, error) {
	raw := strings.TrimSpace(os.Getenv(name))
	if raw == "" {
		return fallback, nil
	}
	seconds, err := strconv.ParseFloat(raw, 64)
	if err != nil || seconds <= 0 {
		return 0, fmt.Errorf("%s must be a positive number of seconds", name)
	}
	duration := time.Duration(seconds * float64(time.Second))
	if duration < minimum || duration > maximum {
		return 0, fmt.Errorf("%s is outside the supported range", name)
	}
	return duration, nil
}

func platformProviderRuntimeInteger(name string, fallback int, minimum int, maximum int) (int, error) {
	raw := strings.TrimSpace(os.Getenv(name))
	if raw == "" {
		return fallback, nil
	}
	value, err := strconv.Atoi(raw)
	if err != nil || value < minimum || value > maximum {
		return 0, fmt.Errorf("%s is outside the supported range", name)
	}
	return value, nil
}

func platformProviderRuntimeRate(name string, fallback float64) (float64, error) {
	raw := strings.TrimSpace(os.Getenv(name))
	if raw == "" {
		return fallback, nil
	}
	value, err := strconv.ParseFloat(raw, 64)
	if err != nil || value < 0 || value > 1 {
		return 0, fmt.Errorf("%s must be between zero and one", name)
	}
	return value, nil
}
