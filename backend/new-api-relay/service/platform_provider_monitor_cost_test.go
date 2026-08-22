package service

import (
	"context"
	"crypto/sha256"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/glebarez/sqlite"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func preparePlatformProviderMonitorCostServiceTest(t *testing.T) {
	t.Helper()
	previousDB := model.DB
	dsn := "file:provider-monitor-cost-" + uuid.NewString() + "?mode=memory&cache=shared"
	db, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{})
	require.NoError(t, err)
	sqlDB, err := db.DB()
	require.NoError(t, err)
	sqlDB.SetMaxOpenConns(1)
	models := append([]any{
		&model.Channel{},
		&model.ProviderChannelCredentialSetVersion{},
		&model.PlatformGenerationProviderAccountState{},
		&model.PlatformGenerationProviderRoute{},
		&model.PlatformGenerationJob{},
		&model.PlatformArtifactUploadIntent{},
	}, model.PlatformProviderMonitorAndCostModels()...)
	require.NoError(t, db.AutoMigrate(models...))
	model.DB = db
	t.Cleanup(func() {
		model.DB = previousDB
		require.NoError(t, sqlDB.Close())
	})
}

func createPlatformProviderServiceRoute(t *testing.T, routeKey string, channelID int) model.PlatformGenerationProviderRoute {
	t.Helper()
	route := model.PlatformGenerationProviderRoute{
		RouteKey:         routeKey,
		Model:            "video-model",
		Mode:             "text_to_video",
		ProviderName:     "provider-a",
		AccountID:        "account-" + routeKey,
		ChannelID:        channelID,
		KeyIndex:         0,
		KeyFingerprint:   strings.Repeat("a", 64),
		ChannelClass:     model.PlatformGenerationChannelClassOfficialProvider,
		UpstreamModel:    "provider-video-model",
		StagingReady:     true,
		ProductionReady:  true,
		Enabled:          true,
		RPMWindowSeconds: 60,
		RPMLimit:         10,
		ActiveLimit:      2,
	}
	require.NoError(t, model.CreatePlatformGenerationProviderRoute(&route))
	return route
}

func TestEvaluateProviderMonitorPreservesActiveIncidentWithoutEvidenceAndRequiresRetirementAck(t *testing.T) {
	policy := DefaultPlatformProviderMonitorPolicy()
	policy.SuccessRateMinimumSamples = 4
	snapshot := model.PlatformProviderMonitorEvaluationSnapshot{
		Incidents: []model.PlatformProviderIncident{
			{ProviderName: "provider-a", Kind: model.PlatformProviderIncidentSuccessRateDrop, Active: true},
			{ProviderName: "provider-a", Kind: model.PlatformProviderIncidentWidespreadFailure, Active: true},
			{ProviderName: "provider-a", Kind: model.PlatformProviderIncidentBatchInvalidation, Active: true},
		},
	}
	decisions, err := EvaluatePlatformProviderMonitorSnapshot(snapshot, policy)
	require.NoError(t, err)
	require.Len(t, decisions, 3)
	for _, decision := range decisions {
		assert.True(t, decision.DesiredActive, decision.Kind)
	}

	snapshot.Retirements = []model.PlatformProviderRetirementAcknowledgement{{ProviderName: "provider-a"}}
	decisions, err = EvaluatePlatformProviderMonitorSnapshot(snapshot, policy)
	require.NoError(t, err)
	require.Len(t, decisions, 3)
	for _, decision := range decisions {
		assert.False(t, decision.DesiredActive, decision.Kind)
		assert.Equal(t, "planned_retirement_acknowledged", decision.ReasonCode)
	}
}

func TestEvaluateProviderMonitorUsesHysteresisAndExplicitRouteRecovery(t *testing.T) {
	policy := DefaultPlatformProviderMonitorPolicy()
	policy.SuccessRateMinimumSamples = 4
	policy.WidespreadMinimumRoutes = 3
	policy.WidespreadMinimumAffectedRoutes = 2
	policy.BatchInvalidationMinimumRoutes = 2
	snapshot := model.PlatformProviderMonitorEvaluationSnapshot{
		Health: []model.PlatformProviderRouteHealth{
			{RouteID: 1, ProviderName: "provider-a", Status: model.PlatformProviderRouteHealthInvalidated, FailureProviderCaused: true},
			{RouteID: 2, ProviderName: "provider-a", Status: model.PlatformProviderRouteHealthInvalidated, FailureProviderCaused: true},
			{RouteID: 3, ProviderName: "provider-a", Status: model.PlatformProviderRouteHealthHealthy},
		},
		Outcomes: []model.PlatformProviderTerminalOutcome{
			{ProviderName: "provider-a", Outcome: model.PlatformProviderOutcomeSucceeded},
			{ProviderName: "provider-a", Outcome: model.PlatformProviderOutcomeSucceeded},
			{ProviderName: "provider-a", Outcome: model.PlatformProviderOutcomeFailed, FailureOwner: model.PlatformProviderFailureOwnerProvider},
			{ProviderName: "provider-a", Outcome: model.PlatformProviderOutcomeFailed, FailureOwner: model.PlatformProviderFailureOwnerProvider},
			{ProviderName: "provider-a", Outcome: model.PlatformProviderOutcomeFailed, FailureOwner: model.PlatformProviderFailureOwnerClient},
		},
	}
	decisions, err := EvaluatePlatformProviderMonitorSnapshot(snapshot, policy)
	require.NoError(t, err)
	byKind := make(map[string]model.PlatformProviderIncidentDecision, len(decisions))
	for _, decision := range decisions {
		byKind[decision.Kind] = decision
	}
	assert.True(t, byKind[model.PlatformProviderIncidentSuccessRateDrop].DesiredActive)
	assert.Equal(t, 4, byKind[model.PlatformProviderIncidentSuccessRateDrop].SampleSize, "client-caused failures are excluded")
	assert.True(t, byKind[model.PlatformProviderIncidentWidespreadFailure].DesiredActive)
	assert.True(t, byKind[model.PlatformProviderIncidentBatchInvalidation].DesiredActive)

	snapshot.Incidents = []model.PlatformProviderIncident{
		{ProviderName: "provider-a", Kind: model.PlatformProviderIncidentWidespreadFailure, Active: true},
		{ProviderName: "provider-a", Kind: model.PlatformProviderIncidentBatchInvalidation, Active: true},
	}
	for index := range snapshot.Health {
		snapshot.Health[index].Status = model.PlatformProviderRouteHealthHealthy
		snapshot.Health[index].FailureProviderCaused = false
	}
	decisions, err = EvaluatePlatformProviderMonitorSnapshot(snapshot, policy)
	require.NoError(t, err)
	byKind = make(map[string]model.PlatformProviderIncidentDecision, len(decisions))
	for _, decision := range decisions {
		byKind[decision.Kind] = decision
	}
	assert.False(t, byKind[model.PlatformProviderIncidentWidespreadFailure].DesiredActive)
	assert.Equal(t, "route_health_recovered", byKind[model.PlatformProviderIncidentWidespreadFailure].ReasonCode)
	assert.False(t, byKind[model.PlatformProviderIncidentBatchInvalidation].DesiredActive)
	assert.Equal(t, "invalidated_accounts_explicitly_recovered", byKind[model.PlatformProviderIncidentBatchInvalidation].ReasonCode)
}

type fixedPlatformProviderProber struct {
	result dto.PlatformProviderRouteProbeResult
	err    error
}

func (prober fixedPlatformProviderProber) ProbePlatformProviderRoute(
	context.Context,
	model.PlatformGenerationProviderRoute,
) (dto.PlatformProviderRouteProbeResult, error) {
	return prober.result, prober.err
}

func TestProviderMonitorCyclePersistsFreshnessAndDeduplicatedAlert(t *testing.T) {
	preparePlatformProviderMonitorCostServiceTest(t)
	createPlatformProviderServiceRoute(t, "route-one", 9101)
	createPlatformProviderServiceRoute(t, "route-two", 9102)
	createPlatformProviderServiceRoute(t, "route-three", 9103)
	claim, err := model.ClaimPlatformProviderMonitorLease("worker-a", 30*time.Second)
	require.NoError(t, err)
	policy := DefaultPlatformProviderMonitorPolicy()
	policy.WidespreadMinimumRoutes = 3
	policy.WidespreadMinimumAffectedRoutes = 2
	policy.BatchInvalidationMinimumRoutes = 2
	prober := fixedPlatformProviderProber{result: dto.PlatformProviderRouteProbeResult{
		Status:         dto.PlatformProviderProbeInvalidated,
		FailureCode:    "account_revoked",
		ProviderCaused: true,
	}}
	require.NoError(t, RunPlatformProviderMonitorCycle(
		context.Background(),
		*claim,
		30*time.Second,
		2*time.Second,
		prober,
		policy,
	))
	require.NoError(t, RunPlatformProviderMonitorCycle(
		context.Background(),
		*claim,
		30*time.Second,
		2*time.Second,
		prober,
		policy,
	))

	readiness, err := GetPlatformProviderMonitorReadiness(true, time.Minute)
	require.NoError(t, err)
	assert.True(t, readiness.Fresh)
	assert.True(t, readiness.Degraded, "provider outage is reported as degraded without making the API unavailable")
	assert.Equal(t, int64(3), readiness.UnavailableRoutes)
	assert.Equal(t, int64(2), readiness.ActiveIncidents)
	assert.Equal(t, int64(2), readiness.AlertPending)

	var alertCount int64
	require.NoError(t, model.DB.Model(&model.PlatformProviderAlertEvent{}).Count(&alertCount).Error)
	assert.Equal(t, int64(2), alertCount, "repeated monitor cycles must not duplicate active transitions")
}

func TestChannelCostDeliveryUsesExactPlatformContractTokenAndSignature(t *testing.T) {
	preparePlatformProviderMonitorCostServiceTest(t)
	relayJobID := uuid.NewString()
	input := dto.PlatformChannelCostInput{
		EventID:              uuid.NewString(),
		AmountCents:          125,
		IdempotencyKey:       "provider-cost-event-42",
		ChannelKey:           "route-provider-a",
		ChannelType:          model.PlatformGenerationChannelClassOfficialProvider,
		OccurredAt:           time.Date(2026, time.August, 6, 12, 0, 0, 0, time.UTC),
		ExternalReference:    "provider-invoice-line-42",
		CompanyID:            uuid.NewString(),
		TaskID:               uuid.NewString(),
		RelayJobID:           relayJobID,
		Note:                 "actual provider charge",
		EvidenceSource:       dto.PlatformChannelCostEvidenceProviderInvoice,
		EvidenceReference:    "provider-invoice-2026-08-line-42",
		SourceDocumentSHA256: strings.Repeat("f", 64),
	}
	created, err := EnqueuePlatformChannelCost(input)
	require.NoError(t, err)
	assert.True(t, created)
	created, err = EnqueuePlatformChannelCost(input)
	require.NoError(t, err)
	assert.False(t, created)

	collision := input
	collision.EventID = uuid.NewString()
	collision.AmountCents = 126
	_, err = EnqueuePlatformChannelCost(collision)
	assert.ErrorIs(t, err, model.ErrPlatformChannelCostEventCollision)
	inferred := input
	inferred.EventID = uuid.NewString()
	inferred.IdempotencyKey = "provider-cost-inferred-42"
	inferred.EvidenceSource = "new_api_quota"
	_, err = EnqueuePlatformChannelCost(inferred)
	assert.ErrorContains(t, err, "provider-side evidence")

	received := make(chan *http.Request, 1)
	receivedBody := make(chan []byte, 1)
	endpoint := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		body, _ := io.ReadAll(request.Body)
		received <- request.Clone(request.Context())
		receivedBody <- body
		writer.WriteHeader(http.StatusCreated)
	}))
	t.Cleanup(endpoint.Close)
	claim, err := model.ClaimPlatformRelayExternalDelivery(model.PlatformRelayDeliveryKindChannelCost, 30*time.Second)
	require.NoError(t, err)
	config := PlatformChannelCostSinkConfig{
		URL:                  endpoint.URL + "/internal/channel-costs",
		InternalServiceToken: "platform-internal-token",
		SigningSecret:        "cost-signing-secret",
	}
	state, won, err := DeliverPlatformChannelCostClaim(context.Background(), *claim, config)
	require.NoError(t, err)
	assert.True(t, won)
	assert.Equal(t, model.PlatformRelayDeliveryDelivered, state)

	request := <-received
	body := <-receivedBody
	assert.Equal(t, "platform-internal-token", request.Header.Get("X-Internal-Service-Token"))
	assert.Equal(t, input.EventID, request.Header.Get("X-Relay-Event-ID"))
	assert.Equal(t, claim.Delivery.RequestID, request.Header.Get("X-Request-ID"))
	timestamp, err := strconv.ParseInt(request.Header.Get("X-Relay-Timestamp"), 10, 64)
	require.NoError(t, err)
	expectedSignature, err := SignPlatformRelayExternalEvent(config.SigningSecret, timestamp, input.EventID, body)
	require.NoError(t, err)
	assert.Equal(t, expectedSignature, request.Header.Get("X-Relay-Signature"))
	assert.JSONEq(t, fmt.Sprintf(`{
		"amount_cents":125,
		"idempotency_key":"provider-cost-event-42",
		"channel_key":"route-provider-a",
		"channel_type":"official",
		"occurred_at":"2026-08-06T12:00:00Z",
		"external_reference":"provider-invoice-line-42",
		"company_id":"%s",
		"task_id":"%s",
		"relay_job_id":"%s",
		"note":"actual provider charge",
		"evidence_source":"provider_invoice",
		"evidence_reference":"provider-invoice-2026-08-line-42",
		"source_document_sha256":"%s"
	}`, input.CompanyID, input.TaskID, relayJobID, strings.Repeat("f", 64)), string(body))
}

func TestSuccessfulProviderOutcomeMaterializesContractRateCostIdempotently(t *testing.T) {
	preparePlatformProviderMonitorCostServiceTest(t)
	route := createPlatformProviderServiceRoute(t, "runtime-cost-route", 9301)
	job, outcome, companyID := createPlatformChannelCostRuntimeFixture(t, route, 5, 1)
	rate := dto.PlatformProviderContractRateInput{
		ID:                   uuid.NewString(),
		ProviderName:         route.ProviderName,
		ChannelID:            route.ChannelID,
		UpstreamModel:        route.UpstreamModel,
		Mode:                 route.Mode,
		Resolution:           "720p",
		BillingUnit:          dto.PlatformContractRateUnitOutputSecond,
		UnitAmountCents:      7,
		Currency:             "CNY",
		EffectiveFrom:        outcome.OccurredAt.Add(-time.Hour),
		SourceReference:      "provider-contract-2026-video-v3",
		SourceDocumentSHA256: strings.Repeat("d", 64),
	}
	require.NoError(t, model.SyncPlatformProviderContractRates([]dto.PlatformProviderContractRateInput{rate}))

	processed, err := RunPlatformChannelCostReconciliationOnce(context.Background(), 30*time.Second)
	require.NoError(t, err)
	assert.True(t, processed)
	processed, err = RunPlatformChannelCostReconciliationOnce(context.Background(), 30*time.Second)
	require.NoError(t, err)
	assert.False(t, processed, "completed reconciliation must not emit a second event")

	var events []model.PlatformChannelCostEvent
	require.NoError(t, model.DB.Where("relay_job_id = ?", job.ID).Find(&events).Error)
	require.Len(t, events, 1)
	event := events[0]
	assert.Equal(t, int64(35), event.AmountCents)
	assert.Equal(t, uuid.NewSHA1(uuid.NameSpaceURL, []byte("relay-contract-cost:"+outcome.ID+":"+rate.ID)).String(), event.ID)
	assert.Equal(t, "relay-contract-cost-"+outcome.ID+"-"+rate.ID, event.IdempotencyKey)
	assert.Equal(t, route.RouteKey, event.ChannelKey)
	assert.Equal(t, route.ChannelClass, event.ChannelType)
	assert.Equal(t, outcome.ExternalReference, event.ExternalReference)
	assert.Equal(t, companyID, event.CompanyID)
	assert.NotEqual(t, job.TenantID, event.CompanyID, "the authenticated Relay client tenant is not the Platform company tenant")
	require.NotNil(t, job.ClientReferenceID)
	assert.Equal(t, *job.ClientReferenceID, event.TaskID)
	assert.Equal(t, dto.PlatformChannelCostEvidenceContractRate, event.EvidenceSource)
	assert.Equal(t, rate.SourceReference, event.EvidenceReference)
	assert.Equal(t, rate.SourceDocumentSHA256, event.SourceDocumentSHA256)
	assert.Contains(t, event.Note, rate.ID)

	var delivery model.PlatformRelayExternalDelivery
	require.NoError(t, model.DB.Where(
		"event_kind = ? AND event_id = ?",
		model.PlatformRelayDeliveryKindChannelCost,
		event.ID,
	).First(&delivery).Error)
	assert.Equal(t, model.PlatformRelayDeliveryPending, delivery.State)
	var queue model.PlatformChannelCostReconciliation
	require.NoError(t, model.DB.First(&queue, "relay_job_id = ?", job.ID).Error)
	assert.Equal(t, model.PlatformCostReconciliationCompleted, queue.State)
}

func TestStagingProviderOutcomeUsesStagingReadinessInsteadOfProductionApproval(t *testing.T) {
	preparePlatformProviderMonitorCostServiceTest(t)
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "staging")
	route := createPlatformProviderServiceRoute(t, "runtime-cost-staging-route", 9303)
	require.NoError(t, model.DB.Model(&model.PlatformGenerationProviderRoute{}).
		Where("id = ?", route.ID).
		Updates(map[string]any{"staging_ready": true, "production_ready": false}).Error)
	route.StagingReady = true
	route.ProductionReady = false
	job, outcome, _ := createPlatformChannelCostRuntimeFixture(t, route, 5, 1)
	require.NoError(t, model.SyncPlatformProviderContractRates([]dto.PlatformProviderContractRateInput{{
		ID: uuid.NewString(), ProviderName: route.ProviderName, ChannelID: route.ChannelID,
		UpstreamModel: route.UpstreamModel, Mode: route.Mode, Resolution: "720p",
		BillingUnit: dto.PlatformContractRateUnitOutputItem, UnitAmountCents: 17, Currency: "CNY",
		EffectiveFrom: outcome.OccurredAt.Add(-time.Minute), SourceReference: "staging-provider-contract",
		SourceDocumentSHA256: strings.Repeat("c", 64),
	}}))

	processed, err := RunPlatformChannelCostReconciliationOnce(context.Background(), 30*time.Second)
	require.NoError(t, err)
	assert.True(t, processed)
	var event model.PlatformChannelCostEvent
	require.NoError(t, model.DB.First(&event, "relay_job_id = ?", job.ID).Error)
	assert.Equal(t, int64(17), event.AmountCents)
}

func TestProductionProviderOutcomeRejectsStagingOnlyRoute(t *testing.T) {
	preparePlatformProviderMonitorCostServiceTest(t)
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "production")
	route := createPlatformProviderServiceRoute(t, "runtime-cost-production-gate", 9304)
	require.NoError(t, model.DB.Model(&model.PlatformGenerationProviderRoute{}).
		Where("id = ?", route.ID).
		Updates(map[string]any{"staging_ready": true, "production_ready": false}).Error)
	route.StagingReady = true
	route.ProductionReady = false
	job, _, _ := createPlatformChannelCostRuntimeFixture(t, route, 5, 1)

	processed, err := RunPlatformChannelCostReconciliationOnce(context.Background(), 30*time.Second)
	require.NoError(t, err)
	assert.True(t, processed)
	var queue model.PlatformChannelCostReconciliation
	require.NoError(t, model.DB.First(&queue, "relay_job_id = ?", job.ID).Error)
	assert.Equal(t, model.PlatformCostReconciliationWaiting, queue.State)
	assert.Equal(t, "route_not_production_ready", queue.LastErrorCode)
}

func TestMissingProviderContractRateRemainsIncompleteThenRecovers(t *testing.T) {
	preparePlatformProviderMonitorCostServiceTest(t)
	route := createPlatformProviderServiceRoute(t, "runtime-cost-missing-rate", 9302)
	job, outcome, _ := createPlatformChannelCostRuntimeFixture(t, route, 5, 1)

	processed, err := RunPlatformChannelCostReconciliationOnce(context.Background(), 30*time.Second)
	require.NoError(t, err)
	assert.True(t, processed)
	var count int64
	require.NoError(t, model.DB.Model(&model.PlatformChannelCostEvent{}).Where("relay_job_id = ?", job.ID).Count(&count).Error)
	assert.Zero(t, count, "missing evidence must not be materialized as zero cost")
	var queue model.PlatformChannelCostReconciliation
	require.NoError(t, model.DB.First(&queue, "relay_job_id = ?", job.ID).Error)
	assert.Equal(t, model.PlatformCostReconciliationWaiting, queue.State)
	assert.Equal(t, "contract_rate_missing", queue.LastErrorCode)
	summary, err := model.GetPlatformChannelCostReconciliationSummary()
	require.NoError(t, err)
	assert.Equal(t, int64(1), summary.IncompleteRelayJobs)

	rate := dto.PlatformProviderContractRateInput{
		ID:                   uuid.NewString(),
		ProviderName:         route.ProviderName,
		ChannelID:            route.ChannelID,
		UpstreamModel:        route.UpstreamModel,
		Mode:                 route.Mode,
		Resolution:           "720p",
		BillingUnit:          dto.PlatformContractRateUnitOutputItem,
		UnitAmountCents:      19,
		Currency:             "CNY",
		EffectiveFrom:        outcome.OccurredAt.Add(-time.Minute),
		SourceReference:      "provider-contract-recovered-rate",
		SourceDocumentSHA256: strings.Repeat("e", 64),
	}
	require.NoError(t, model.SyncPlatformProviderContractRates([]dto.PlatformProviderContractRateInput{rate}))
	require.NoError(t, model.DB.Model(&model.PlatformChannelCostReconciliation{}).
		Where("relay_job_id = ?", job.ID).
		Update("next_attempt_at", time.Unix(1, 0).UTC()).Error)

	processed, err = RunPlatformChannelCostReconciliationOnce(context.Background(), 30*time.Second)
	require.NoError(t, err)
	assert.True(t, processed)
	var event model.PlatformChannelCostEvent
	require.NoError(t, model.DB.First(&event, "relay_job_id = ?", job.ID).Error)
	assert.Equal(t, int64(19), event.AmountCents)
}

func TestProviderContractPerItemCostDoesNotDependOnVideoDuration(t *testing.T) {
	amount, err := calculatePlatformProviderContractCost(
		model.PlatformProviderContractRate{
			BillingUnit:     dto.PlatformContractRateUnitOutputItem,
			UnitAmountCents: 23,
		},
		dto.PlatformGenerationOutputOptions{Count: 3},
	)

	require.NoError(t, err)
	assert.Equal(t, int64(69), amount)
}

func TestProviderContractPerSecondCostRequiresPositiveDuration(t *testing.T) {
	_, err := calculatePlatformProviderContractCost(
		model.PlatformProviderContractRate{
			BillingUnit:     dto.PlatformContractRateUnitOutputSecond,
			UnitAmountCents: 7,
		},
		dto.PlatformGenerationOutputOptions{Count: 1},
	)

	require.ErrorContains(t, err, "duration")
}

func createPlatformChannelCostRuntimeFixture(
	t *testing.T,
	route model.PlatformGenerationProviderRoute,
	durationSeconds int,
	outputCount int,
) (model.PlatformGenerationJob, model.PlatformProviderTerminalOutcome, string) {
	t.Helper()
	serviceTenantID := uuid.NewString()
	companyID := uuid.NewString()
	taskID := uuid.NewString()
	request := dto.NewPlatformGenerationRequest()
	request.ClientReferenceID = &taskID
	request.Model = route.Model
	request.Mode = route.Mode
	request.ExpectedCapabilityRevision = "sha256:" + strings.Repeat("a", 64)
	request.Inputs.Prompt = "provider cost runtime fixture"
	request.Output.DurationSeconds = durationSeconds
	request.Output.Resolution = "720p"
	request.Output.Count = outputCount
	request.Metadata["platform_company_id"] = companyID
	request.Metadata["platform_task_id"] = taskID
	requestJSON, err := common.Marshal(request)
	require.NoError(t, err)
	job := model.PlatformGenerationJob{
		ID:                         uuid.NewString(),
		TenantID:                   serviceTenantID,
		SourceClientID:             "platform",
		RequestID:                  "runtime-cost-request-" + uuid.NewString(),
		IdempotencyKey:             "runtime-cost-idempotency-" + uuid.NewString(),
		RequestHash:                strings.Repeat("b", 64),
		RequestJSON:                string(requestJSON),
		ClientReferenceID:          &taskID,
		Model:                      route.Model,
		Mode:                       route.Mode,
		ExpectedCapabilityRevision: request.ExpectedCapabilityRevision,
		CapabilityRevision:         request.ExpectedCapabilityRevision,
		Status:                     model.PlatformGenerationStatusSucceeded,
		Progress:                   100,
		ProviderRouteID:            route.ID,
		ProviderChannelID:          route.ChannelID,
		OutputsJSON:                `[]`,
		ErrorDetailsJSON:           `{}`,
	}
	require.NoError(t, model.DB.Create(&job).Error)
	outcome := model.PlatformProviderTerminalOutcome{
		ID:                uuid.NewString(),
		RouteID:           route.ID,
		RelayJobID:        job.ID,
		Outcome:           model.PlatformProviderOutcomeSucceeded,
		FailureOwner:      model.PlatformProviderFailureOwnerNone,
		OccurredAt:        time.Date(2026, time.August, 11, 9, 30, 0, 0, time.UTC),
		ExternalReference: "provider-task:runtime-cost-" + job.ID,
	}
	created, err := model.CreatePlatformProviderTerminalOutcome(&outcome)
	require.NoError(t, err)
	require.True(t, created)
	return job, outcome, companyID
}

func TestPlatformProviderCostLinkageAcceptsSignedPlatformCompanyMetadata(t *testing.T) {
	serviceTenantID := uuid.NewString()
	companyID := uuid.NewString()
	taskID := uuid.NewString()
	request := dto.NewPlatformGenerationRequest()
	request.ClientReferenceID = &taskID
	request.Metadata["platform_company_id"] = companyID
	request.Metadata["platform_task_id"] = taskID
	job := model.PlatformGenerationJob{
		TenantID:          serviceTenantID,
		ClientReferenceID: &taskID,
	}

	linkedCompanyID, linkedTaskID, err := platformProviderCostLinkage(job, request)
	require.NoError(t, err)
	assert.Equal(t, companyID, linkedCompanyID)
	assert.NotEqual(t, job.TenantID, linkedCompanyID)
	assert.Equal(t, taskID, linkedTaskID)

	delete(request.Metadata, "platform_company_id")
	_, _, err = platformProviderCostLinkage(job, request)
	require.Error(t, err, "partial Platform linkage must fail closed")

	delete(request.Metadata, "platform_task_id")
	linkedCompanyID, linkedTaskID, err = platformProviderCostLinkage(job, request)
	require.NoError(t, err, "non-Platform callers may carry an unrelated client reference without fabricated Platform linkage")
	assert.Empty(t, linkedCompanyID)
	assert.Empty(t, linkedTaskID)
}

func TestProviderAlertDeliveryIsSignedSecretFreeAndDoesNotFollowRedirect(t *testing.T) {
	preparePlatformProviderMonitorCostServiceTest(t)
	claim, err := model.ClaimPlatformProviderMonitorLease("worker-a", 30*time.Second)
	require.NoError(t, err)
	require.NoError(t, model.ApplyPlatformProviderIncidentDecisions(claim.Token, []model.PlatformProviderIncidentDecision{{
		ProviderName:           "provider-a",
		Kind:                   model.PlatformProviderIncidentSuccessRateDrop,
		DesiredActive:          true,
		ReasonCode:             "success_rate_below_threshold",
		SampleSize:             20,
		SuccessCount:           10,
		SuccessRateBasisPoints: 5000,
	}}))

	var redirectedRequests int
	redirectTarget := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		redirectedRequests++
	}))
	t.Cleanup(redirectTarget.Close)
	redirectSource := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("Location", redirectTarget.URL)
		writer.WriteHeader(http.StatusTemporaryRedirect)
	}))
	t.Cleanup(redirectSource.Close)
	deliveryClaim, err := model.ClaimPlatformRelayExternalDelivery(model.PlatformRelayDeliveryKindProviderAlert, 30*time.Second)
	require.NoError(t, err)
	state, won, err := DeliverPlatformProviderAlertClaim(context.Background(), *deliveryClaim, PlatformProviderAlertSinkConfig{
		URL:           redirectSource.URL,
		SigningSecret: "alert-signing-secret",
	})
	require.NoError(t, err)
	assert.True(t, won)
	assert.Equal(t, model.PlatformRelayDeliveryDeadLetter, state)
	assert.Zero(t, redirectedRequests)

	event, err := model.GetPlatformProviderAlertEvent(deliveryClaim.Delivery.EventID)
	require.NoError(t, err)
	assert.NotContains(t, event.PayloadJSON, "account-")
	assert.NotContains(t, event.PayloadJSON, strings.Repeat("a", 64))
	assert.Error(t, validatePlatformProviderAlertTarget("https://127.0.0.1/alerts", true))
}

func TestPlatformRelayExternalSignatureCoversRawBody(t *testing.T) {
	body := []byte(`{"schema_version":1,"event_id":"123"}`)
	eventID := "123e4567-e89b-12d3-a456-426614174000"
	signature, err := SignPlatformRelayExternalEvent("secret", 1786003200, eventID, body)
	require.NoError(t, err)
	digest := sha256.Sum256([]byte("1786003200." + eventID + "." + string(body)))
	assert.NotEqual(t, fmt.Sprintf("v1=%x", digest), signature, "signature must be HMAC rather than a bare digest")
}
