package service

import (
	"context"
	"crypto/sha256"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func setPlatformProviderRuntimeTestEnvironment(t *testing.T) {
	t.Helper()
	t.Setenv("RELAY_COMPAT_ENABLED", "true")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("RELAY_PROVIDER_MONITOR_ENABLED", "true")
	t.Setenv("RELAY_PROVIDER_MONITOR_INTERVAL_SECONDS", "")
	t.Setenv("RELAY_PROVIDER_MONITOR_LEASE_SECONDS", "")
	t.Setenv("RELAY_PROVIDER_MONITOR_ACQUIRE_RETRY_SECONDS", "")
	t.Setenv("RELAY_PROVIDER_HEALTHCHECK_TIMEOUT_SECONDS", "")
	t.Setenv("RELAY_PROVIDER_CHANNEL_TEST_MAX_AGE_SECONDS", "")
	t.Setenv("CHANNEL_TEST_ENABLED", "true")
	t.Setenv("CHANNEL_TEST_FREQUENCY", "2")
	t.Setenv("NODE_TYPE", "master")
	t.Setenv("RELAY_PROVIDER_MONITOR_WINDOW_SECONDS", "")
	t.Setenv("RELAY_PROVIDER_MONITOR_MIN_OUTCOMES", "")
	t.Setenv("RELAY_PROVIDER_MONITOR_MIN_SUCCESS_RATE", "")
	t.Setenv("RELAY_PROVIDER_MONITOR_SUCCESS_RECOVERY_RATE", "")
	t.Setenv("RELAY_PROVIDER_MONITOR_WIDESPREAD_FAILURE_RATIO", "")
	t.Setenv("RELAY_PROVIDER_MONITOR_WIDESPREAD_RECOVERY_RATIO", "")
	t.Setenv("RELAY_PROVIDER_MONITOR_WIDESPREAD_MIN_ROUTES", "")
	t.Setenv("RELAY_PROVIDER_MONITOR_BATCH_DISABLED_THRESHOLD", "")
	t.Setenv("RELAY_PROVIDER_ALERT_WEBHOOK_URL", "")
	t.Setenv("RELAY_PROVIDER_ALERT_SIGNING_SECRET", "")
	t.Setenv("RELAY_PROVIDER_ALERT_CLAIM_LEASE_SECONDS", "")
	t.Setenv("RELAY_PROVIDER_ALERT_POLL_SECONDS", "")
	t.Setenv("RELAY_PLATFORM_CHANNEL_COST_URL", "")
	t.Setenv("RELAY_PLATFORM_INTERNAL_SERVICE_TOKEN", "")
	t.Setenv("RELAY_PLATFORM_CHANNEL_COST_SIGNING_SECRET", "")
	t.Setenv("RELAY_PROVIDER_CONTRACT_RATES_JSON", "")
	t.Setenv("RELAY_CHANNEL_COST_CLAIM_LEASE_SECONDS", "")
	t.Setenv("RELAY_CHANNEL_COST_POLL_SECONDS", "")
	t.Setenv("RELAY_PLATFORM_TASK_STAGE_URL", "")
	t.Setenv("RELAY_PLATFORM_OPERATIONS_SNAPSHOT_URL", "")
	t.Setenv("RELAY_TELEMETRY_SIGNING_SECRET", "")
	t.Setenv("RELAY_TELEMETRY_CLAIM_LEASE_SECONDS", "")
	t.Setenv("RELAY_TELEMETRY_POLL_SECONDS", "")
}

func TestPlatformProviderRuntimeProductionRequiresFreshNativeScheduledChannelTests(t *testing.T) {
	setPlatformProviderRuntimeTestEnvironment(t)
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "production")

	t.Setenv("NODE_TYPE", "slave")
	err := ValidatePlatformProviderRuntimeConfiguration()
	assert.ErrorContains(t, err, "NODE_TYPE=master")

	t.Setenv("NODE_TYPE", "master")
	t.Setenv("CHANNEL_TEST_ENABLED", "false")
	err = ValidatePlatformProviderRuntimeConfiguration()
	assert.ErrorContains(t, err, "CHANNEL_TEST_ENABLED=true")

	t.Setenv("CHANNEL_TEST_ENABLED", "true")
	t.Setenv("CHANNEL_TEST_FREQUENCY", " 2")
	err = ValidatePlatformProviderRuntimeConfiguration()
	assert.ErrorContains(t, err, "normalized positive integer")

	t.Setenv("CHANNEL_TEST_FREQUENCY", "9223372036854775807")
	err = ValidatePlatformProviderRuntimeConfiguration()
	assert.ErrorContains(t, err, "plus scheduler delay")

	t.Setenv("CHANNEL_TEST_FREQUENCY", "5")
	t.Setenv("RELAY_PROVIDER_CHANNEL_TEST_MAX_AGE_SECONDS", "300")
	err = ValidatePlatformProviderRuntimeConfiguration()
	assert.ErrorContains(t, err, "plus scheduler delay")

	t.Setenv("CHANNEL_TEST_FREQUENCY", "2")
	err = ValidatePlatformProviderRuntimeConfiguration()
	assert.ErrorContains(t, err, "signed provider alert sink")
}

func createPlatformProviderRuntimeRoute(t *testing.T, channelStatus int) (model.PlatformGenerationProviderRoute, model.Channel) {
	t.Helper()
	key := "runtime-provider-key-" + uuid.NewString()
	channel := model.Channel{
		Name:     "runtime-provider-channel-" + uuid.NewString(),
		Key:      key,
		Status:   channelStatus,
		Models:   "provider-video-model",
		TestTime: time.Now().UTC().Unix(),
	}
	require.NoError(t, model.DB.Create(&channel).Error)
	t.Cleanup(func() {
		require.NoError(t, model.DB.Unscoped().Delete(&model.Channel{}, channel.Id).Error)
	})
	digest := sha256.Sum256([]byte(key))
	route := model.PlatformGenerationProviderRoute{
		RouteKey:         "runtime-route-" + uuid.NewString(),
		Model:            "video-model",
		Mode:             "text_to_video",
		ProviderName:     "runtime-provider",
		AccountID:        "runtime-account",
		ChannelID:        channel.Id,
		KeyIndex:         0,
		KeyFingerprint:   fmt.Sprintf("%x", digest),
		ChannelClass:     model.PlatformGenerationChannelClassOfficialProvider,
		UpstreamModel:    "provider-video-model",
		ProductionReady:  true,
		Enabled:          true,
		RPMWindowSeconds: 60,
		RPMLimit:         10,
		ActiveLimit:      2,
	}
	require.NoError(t, model.CreatePlatformGenerationProviderRoute(&route))
	return route, channel
}

func TestPlatformProviderRuntimeProductionConfigurationFailsClosed(t *testing.T) {
	setPlatformProviderRuntimeTestEnvironment(t)
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "production")
	t.Setenv("RELAY_PROVIDER_MONITOR_ENABLED", "false")
	err := ValidatePlatformProviderRuntimeConfiguration()
	assert.ErrorContains(t, err, "RELAY_PROVIDER_MONITOR_ENABLED")

	t.Setenv("RELAY_PROVIDER_MONITOR_ENABLED", "true")
	err = ValidatePlatformProviderRuntimeConfiguration()
	assert.ErrorContains(t, err, "signed provider alert sink")

	t.Setenv("RELAY_PROVIDER_ALERT_WEBHOOK_URL", "https://8.8.8.8/relay/provider")
	t.Setenv("RELAY_PROVIDER_ALERT_SIGNING_SECRET", "alert-runtime-secret-0123456789abcdef")
	err = ValidatePlatformProviderRuntimeConfiguration()
	assert.ErrorContains(t, err, "channel-cost URL")

	t.Setenv("RELAY_PLATFORM_CHANNEL_COST_URL", "https://platform.internal/internal/channel-costs")
	t.Setenv("RELAY_PLATFORM_INTERNAL_SERVICE_TOKEN", "platform-runtime-token-0123456789abcdef")
	err = ValidatePlatformProviderRuntimeConfiguration()
	assert.ErrorContains(t, err, "SIGNING_SECRET")

	t.Setenv("RELAY_PLATFORM_CHANNEL_COST_SIGNING_SECRET", "platform-cost-signing-0123456789abcdef")
	err = ValidatePlatformProviderRuntimeConfiguration()
	assert.ErrorContains(t, err, "task-stage and operations-snapshot")

	t.Setenv("RELAY_PLATFORM_TASK_STAGE_URL", "https://platform.internal/internal/relay/task-stages")
	t.Setenv("RELAY_PLATFORM_OPERATIONS_SNAPSHOT_URL", "https://platform.internal/internal/relay/operations-snapshots")
	t.Setenv("RELAY_TELEMETRY_SIGNING_SECRET", "platform-telemetry-signing-0123456789abcdef")
	require.NoError(t, ValidatePlatformProviderRuntimeConfiguration())

	t.Setenv("RELAY_PLATFORM_CHANNEL_COST_SIGNING_SECRET", "alert-runtime-secret-0123456789abcdef")
	err = ValidatePlatformProviderRuntimeConfiguration()
	assert.ErrorContains(t, err, "provider alert signing secrets must be independent")

	t.Setenv("RELAY_PLATFORM_CHANNEL_COST_SIGNING_SECRET", "platform-runtime-token-0123456789abcdef")
	err = ValidatePlatformProviderRuntimeConfiguration()
	assert.ErrorContains(t, err, "must be independent")

	t.Setenv("RELAY_PLATFORM_INTERNAL_SERVICE_TOKEN", "replace-with-a-long-random-platform-token")
	t.Setenv("RELAY_PLATFORM_CHANNEL_COST_SIGNING_SECRET", "platform-cost-signing-0123456789abcdef")
	err = ValidatePlatformProviderRuntimeConfiguration()
	assert.ErrorContains(t, err, "placeholder")
}

func TestPlatformProviderRuntimeRejectsInvalidPolicyAndLease(t *testing.T) {
	setPlatformProviderRuntimeTestEnvironment(t)
	t.Setenv("RELAY_PROVIDER_MONITOR_MIN_SUCCESS_RATE", "0.98")
	t.Setenv("RELAY_PROVIDER_MONITOR_SUCCESS_RECOVERY_RATE", "0.95")
	err := ValidatePlatformProviderRuntimeConfiguration()
	assert.ErrorContains(t, err, "success-rate policy")

	t.Setenv("RELAY_PROVIDER_MONITOR_MIN_SUCCESS_RATE", "0.80")
	t.Setenv("RELAY_PROVIDER_MONITOR_INTERVAL_SECONDS", "120")
	t.Setenv("RELAY_PROVIDER_MONITOR_LEASE_SECONDS", "120")
	err = ValidatePlatformProviderRuntimeConfiguration()
	assert.ErrorContains(t, err, "shorter than its lease")
}

func TestPlatformProviderRuntimeStrictlyLoadsAndSyncsContractRates(t *testing.T) {
	preparePlatformProviderMonitorCostServiceTest(t)
	setPlatformProviderRuntimeTestEnvironment(t)
	route := createPlatformProviderServiceRoute(t, "runtime-config-rate", 9401)
	t.Setenv("RELAY_PLATFORM_CHANNEL_COST_URL", "https://platform.internal/internal/channel-costs")
	t.Setenv("RELAY_PLATFORM_INTERNAL_SERVICE_TOKEN", "platform-runtime-token")
	t.Setenv("RELAY_PLATFORM_CHANNEL_COST_SIGNING_SECRET", "platform-cost-signing-secret")
	t.Setenv("RELAY_PROVIDER_CONTRACT_RATES_JSON", `[{"unknown":true}]`)
	err := ValidatePlatformProviderRuntimeConfiguration()
	assert.ErrorContains(t, err, "unknown field")

	rate := dto.PlatformProviderContractRateInput{
		ID:                   uuid.NewString(),
		ProviderName:         route.ProviderName,
		ChannelID:            route.ChannelID,
		UpstreamModel:        route.UpstreamModel,
		Mode:                 route.Mode,
		Resolution:           "720p",
		BillingUnit:          dto.PlatformContractRateUnitOutputSecond,
		UnitAmountCents:      11,
		Currency:             "CNY",
		EffectiveFrom:        time.Date(2026, time.August, 1, 0, 0, 0, 0, time.UTC),
		SourceReference:      "provider-contract-runtime-config",
		SourceDocumentSHA256: strings.Repeat("c", 64),
	}
	raw, err := common.Marshal([]dto.PlatformProviderContractRateInput{rate})
	require.NoError(t, err)
	t.Setenv("RELAY_PROVIDER_CONTRACT_RATES_JSON", string(raw))
	require.NoError(t, ValidatePlatformProviderRuntimeConfiguration())

	var persisted model.PlatformProviderContractRate
	require.NoError(t, model.DB.First(&persisted, "id = ?", rate.ID).Error)
	assert.Equal(t, rate.SourceReference, persisted.SourceReference)
	assert.Equal(t, rate.SourceDocumentSHA256, persisted.SourceDocumentSHA256)
}

func TestPlatformProviderRuntimeChannelProberVerifiesFixedKeyAndState(t *testing.T) {
	preparePlatformProviderMonitorCostServiceTest(t)
	route, channel := createPlatformProviderRuntimeRoute(t, common.ChannelStatusEnabled)
	prober := platformProviderRuntimeChannelProber{MaximumTestAge: 5 * time.Minute}

	result, err := prober.ProbePlatformProviderRoute(context.Background(), route)
	require.NoError(t, err)
	assert.Equal(t, dto.PlatformProviderProbeHealthy, result.Status)

	drifted := route
	drifted.KeyFingerprint = strings.Repeat("a", 64)
	result, err = prober.ProbePlatformProviderRoute(context.Background(), drifted)
	require.NoError(t, err)
	assert.Equal(t, dto.PlatformProviderProbeFailed, result.Status)
	assert.Equal(t, "route_key_drift", result.FailureCode)
	assert.False(t, result.ProviderCaused)

	require.NoError(t, model.DB.Model(&model.Channel{}).Where("id = ?", channel.Id).
		Update("status", common.ChannelStatusAutoDisabled).Error)
	result, err = prober.ProbePlatformProviderRoute(context.Background(), route)
	require.NoError(t, err)
	assert.Equal(t, "channel_auto_disabled", result.FailureCode)
	assert.True(t, result.ProviderCaused)

	require.NoError(t, model.DB.Model(&model.Channel{}).Where("id = ?", channel.Id).Updates(map[string]any{
		"status":    common.ChannelStatusEnabled,
		"test_time": time.Now().UTC().Add(-time.Hour).Unix(),
	}).Error)
	result, err = prober.ProbePlatformProviderRoute(context.Background(), route)
	require.NoError(t, err)
	assert.Equal(t, "channel_probe_stale", result.FailureCode)
	assert.False(t, result.ProviderCaused)
}

func TestPlatformProviderReadinessReportsOutageAndCostGapWithoutError(t *testing.T) {
	setPlatformProviderRuntimeTestEnvironment(t)
	preparePlatformProviderMonitorCostServiceTest(t)
	route, _ := createPlatformProviderRuntimeRoute(t, common.ChannelStatusEnabled)
	claim, err := model.ClaimPlatformProviderMonitorLease("runtime-worker", 30*time.Second)
	require.NoError(t, err)
	require.NoError(t, model.ApplyPlatformProviderRouteObservations(claim.Token, []model.PlatformProviderRouteObservation{{
		RouteID:        route.ID,
		Probed:         true,
		Status:         model.PlatformProviderRouteHealthFailed,
		FailureCode:    "provider_unavailable",
		ProviderCaused: true,
	}}))
	require.NoError(t, model.ApplyPlatformProviderIncidentDecisions(claim.Token, []model.PlatformProviderIncidentDecision{{
		ProviderName:           route.ProviderName,
		Kind:                   model.PlatformProviderIncidentSuccessRateDrop,
		DesiredActive:          true,
		ReasonCode:             "success_rate_below_threshold",
		SampleSize:             20,
		SuccessCount:           10,
		SuccessRateBasisPoints: 5000,
	}}))
	won, err := model.CompletePlatformProviderMonitorCycle(claim.Token, "")
	require.NoError(t, err)
	require.True(t, won)

	jobID := uuid.NewString()
	created, err := RecordPlatformProviderTerminalOutcome(dto.PlatformProviderTerminalOutcomeInput{
		EventID:           uuid.NewString(),
		RouteID:           route.ID,
		RelayJobID:        jobID,
		Outcome:           dto.PlatformProviderOutcomeSucceeded,
		FailureOwner:      dto.PlatformProviderFailureOwnerNone,
		OccurredAt:        time.Now().UTC(),
		ExternalReference: "runtime-provider-success",
	})
	require.NoError(t, err)
	require.True(t, created)

	summary, err := GetPlatformProviderReadinessSummary(context.Background())
	require.NoError(t, err, "provider-only outage must remain a degraded HTTP-200 diagnostic")
	assert.True(t, summary.Enabled)
	assert.True(t, summary.MonitorFresh)
	assert.True(t, summary.Degraded)
	assert.Equal(t, int64(1), summary.ActiveAlerts)
	assert.Equal(t, int64(1), summary.UnavailableRoutes)
	assert.Equal(t, int64(1), summary.AlertBacklog)
	assert.Equal(t, int64(1), summary.CostIncomplete)
	assert.Equal(t, int64(1), summary.CostSuccessfulRelayJobs)
	assert.Zero(t, summary.NativeBillingReconciliationJobs)
	assert.False(t, summary.CostReconciliationComplete)
}

func TestPlatformProviderReadinessDoesNotTreatEmptyCostLedgerAsComplete(t *testing.T) {
	setPlatformProviderRuntimeTestEnvironment(t)
	preparePlatformProviderMonitorCostServiceTest(t)
	claim, err := model.ClaimPlatformProviderMonitorLease("runtime-worker", 30*time.Second)
	require.NoError(t, err)
	won, err := model.CompletePlatformProviderMonitorCycle(claim.Token, "")
	require.NoError(t, err)
	require.True(t, won)

	summary, err := GetPlatformProviderReadinessSummary(context.Background())
	require.NoError(t, err)
	assert.True(t, summary.MonitorFresh)
	assert.Zero(t, summary.CostSuccessfulRelayJobs)
	assert.False(t, summary.CostReconciliationComplete)
	assert.True(t, summary.Degraded)
}

func TestPlatformProviderRuntimeCoordinatorCancelsAndJoinsEveryWorker(t *testing.T) {
	var coordinator platformProviderRuntimeCoordinator
	started := make(chan struct{}, 2)
	exited := make(chan struct{}, 2)
	worker := func(ctx context.Context) {
		started <- struct{}{}
		<-ctx.Done()
		exited <- struct{}{}
	}
	require.NoError(t, coordinator.startWorkers(worker, worker))
	for range 2 {
		select {
		case <-started:
		case <-time.After(time.Second):
			t.Fatal("provider runtime worker did not start")
		}
	}
	stopContext, cancelStop := context.WithTimeout(context.Background(), time.Second)
	require.NoError(t, coordinator.stop(stopContext))
	cancelStop()
	for range 2 {
		select {
		case <-exited:
		case <-time.After(time.Second):
			t.Fatal("provider runtime worker was not joined")
		}
	}
	require.NoError(t, coordinator.stop(context.Background()), "stop must be idempotent after a complete join")
}

func TestPlatformProviderRuntimeCoordinatorHonorsHardStopDeadline(t *testing.T) {
	var coordinator platformProviderRuntimeCoordinator
	started := make(chan struct{})
	release := make(chan struct{})
	require.NoError(t, coordinator.startWorkers(func(context.Context) {
		close(started)
		<-release
	}))
	<-started
	stopContext, cancelStop := context.WithTimeout(context.Background(), 20*time.Millisecond)
	err := coordinator.stop(stopContext)
	cancelStop()
	require.ErrorIs(t, err, context.DeadlineExceeded)
	close(release)
	require.Eventually(t, func() bool {
		coordinator.mu.Lock()
		defer coordinator.mu.Unlock()
		return !coordinator.running
	}, time.Second, 5*time.Millisecond)
}
