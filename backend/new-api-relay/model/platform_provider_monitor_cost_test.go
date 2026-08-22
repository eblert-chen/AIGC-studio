package model

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func preparePlatformProviderMonitorCostTest(t *testing.T, appendOnlyGuards bool) {
	t.Helper()
	models := append([]any{&PlatformGenerationProviderAccountState{}, &PlatformGenerationProviderRoute{}, &PlatformGenerationJob{}}, PlatformProviderMonitorAndCostModels()...)
	for index := len(models) - 1; index >= 0; index-- {
		require.NoError(t, DB.Migrator().DropTable(models[index]))
	}
	require.NoError(t, DB.AutoMigrate(models...))
	if appendOnlyGuards {
		require.NoError(t, InstallPlatformProviderAppendOnlyGuards())
	}
	t.Cleanup(func() {
		for index := len(models) - 1; index >= 0; index-- {
			require.NoError(t, DB.Migrator().DropTable(models[index]))
		}
	})
}

func createPlatformProviderMonitorRoute(t *testing.T, routeKey string, providerName string) PlatformGenerationProviderRoute {
	t.Helper()
	route := PlatformGenerationProviderRoute{
		RouteKey:         routeKey,
		Model:            "video-model",
		Mode:             "text_to_video",
		ProviderName:     providerName,
		AccountID:        "account-" + routeKey,
		ChannelID:        9001,
		KeyIndex:         0,
		KeyFingerprint:   strings.Repeat("a", 64),
		ChannelClass:     PlatformGenerationChannelClassOfficialProvider,
		UpstreamModel:    "provider-video-model",
		ProductionReady:  true,
		Enabled:          true,
		RPMWindowSeconds: 60,
		RPMLimit:         10,
		ActiveLimit:      2,
	}
	require.NoError(t, CreatePlatformGenerationProviderRoute(&route))
	return route
}

func TestPlatformChannelCostDocumentDigestMigrationIsSQLiteSafeAndIdempotent(t *testing.T) {
	preparePlatformProviderMonitorCostTest(t, false)
	require.Equal(t, "sqlite", DB.Dialector.Name())

	require.NoError(t, migratePlatformChannelCostDocumentDigestStorage())
	require.NoError(t, migratePlatformChannelCostDocumentDigestStorage())

	payload := dto.PlatformChannelCostPayload{
		AmountCents:       7,
		IdempotencyKey:    "sqlite-empty-document-evidence",
		ChannelKey:        "sqlite-route",
		ChannelType:       PlatformGenerationChannelClassOfficialProvider,
		OccurredAt:        time.Date(2026, time.August, 12, 8, 0, 0, 0, time.UTC),
		ExternalReference: "sqlite-provider-report",
		Note:              "provider reported cost",
		EvidenceSource:    dto.PlatformChannelCostEvidenceProviderReported,
	}
	payloadJSON, err := common.Marshal(payload)
	require.NoError(t, err)
	digest := sha256.Sum256(payloadJSON)
	event := PlatformChannelCostEvent{
		ID:                uuid.NewString(),
		AmountCents:       payload.AmountCents,
		IdempotencyKey:    payload.IdempotencyKey,
		ChannelKey:        payload.ChannelKey,
		ChannelType:       payload.ChannelType,
		OccurredAt:        payload.OccurredAt,
		ExternalReference: payload.ExternalReference,
		Note:              payload.Note,
		EvidenceSource:    payload.EvidenceSource,
		PayloadJSON:       string(payloadJSON),
		PayloadSHA256:     fmt.Sprintf("%x", digest),
	}
	created, err := CreatePlatformChannelCostEvent(&event)
	require.NoError(t, err)
	require.True(t, created)

	stored, err := GetPlatformChannelCostEvent(event.ID)
	require.NoError(t, err)
	assert.Empty(t, stored.EvidenceReference)
	assert.Empty(t, stored.SourceDocumentSHA256)
	assert.Equal(t, string(payloadJSON), stored.PayloadJSON)
}

func TestPlatformProviderMonitorLeaseFencesStaleHealthWriterAndMissingProbeDoesNotRecover(t *testing.T) {
	preparePlatformProviderMonitorCostTest(t, false)
	route := createPlatformProviderMonitorRoute(t, "route-fence", "provider-a")

	first, err := ClaimPlatformProviderMonitorLease("worker-a", 30*time.Second)
	require.NoError(t, err)
	_, err = ClaimPlatformProviderMonitorLease("worker-b", 30*time.Second)
	assert.ErrorIs(t, err, ErrPlatformProviderMonitorLeaseHeld)

	require.NoError(t, DB.Model(&PlatformProviderMonitorLease{}).
		Where("name = ?", platformProviderMonitorLeaseName).
		Update("expires_at", time.Unix(1, 0).UTC()).Error)
	second, err := ClaimPlatformProviderMonitorLease("worker-b", 30*time.Second)
	require.NoError(t, err)
	assert.NotEqual(t, first.Token, second.Token)

	err = ApplyPlatformProviderRouteObservations(first.Token, []PlatformProviderRouteObservation{{
		RouteID: route.ID,
		Probed:  true,
		Status:  PlatformProviderRouteHealthHealthy,
	}})
	assert.ErrorIs(t, err, ErrPlatformProviderMonitorLeaseLost)

	require.NoError(t, ApplyPlatformProviderRouteObservations(second.Token, []PlatformProviderRouteObservation{{
		RouteID:        route.ID,
		Probed:         true,
		Status:         PlatformProviderRouteHealthFailed,
		FailureCode:    "provider_unavailable",
		ProviderCaused: true,
	}}))
	require.NoError(t, ApplyPlatformProviderRouteObservations(second.Token, []PlatformProviderRouteObservation{{
		RouteID: route.ID,
		Probed:  false,
	}}))

	var health PlatformProviderRouteHealth
	require.NoError(t, DB.First(&health, "route_id = ?", route.ID).Error)
	assert.Equal(t, PlatformProviderRouteHealthFailed, health.Status)
	assert.Equal(t, "provider_unavailable", health.FailureCode)
	assert.Equal(t, 1, health.ConsecutiveFailures)
}

func TestPlatformProviderTerminalOutcomesAreIdempotentImmutableAndInvalidateHealth(t *testing.T) {
	preparePlatformProviderMonitorCostTest(t, true)
	route := createPlatformProviderMonitorRoute(t, "route-invalid", "provider-a")
	outcome := PlatformProviderTerminalOutcome{
		ID:                 uuid.NewString(),
		RouteID:            route.ID,
		RelayJobID:         uuid.NewString(),
		Outcome:            PlatformProviderOutcomeFailed,
		FailureOwner:       PlatformProviderFailureOwnerProvider,
		FailureCode:        "account_revoked",
		AccountInvalidated: true,
		OccurredAt:         time.Date(2026, time.August, 6, 10, 0, 0, 0, time.UTC),
		ExternalReference:  "provider-event-42",
	}
	created, err := CreatePlatformProviderTerminalOutcome(&outcome)
	require.NoError(t, err)
	assert.True(t, created)
	created, err = CreatePlatformProviderTerminalOutcome(&outcome)
	require.NoError(t, err)
	assert.False(t, created)

	collision := outcome
	collision.ExternalReference = "provider-event-different"
	_, err = CreatePlatformProviderTerminalOutcome(&collision)
	assert.ErrorIs(t, err, ErrPlatformProviderTerminalOutcomeCollision)

	var health PlatformProviderRouteHealth
	require.NoError(t, DB.First(&health, "route_id = ?", route.ID).Error)
	assert.Equal(t, PlatformProviderRouteHealthInvalidated, health.Status)
	assert.True(t, health.FailureProviderCaused)

	err = DB.Exec(
		"UPDATE platform_provider_terminal_outcomes SET failure_code = ? WHERE id = ?",
		"tampered",
		outcome.ID,
	).Error
	assert.Error(t, err)
	err = DB.Exec("DELETE FROM platform_provider_terminal_outcomes WHERE id = ?", outcome.ID).Error
	assert.Error(t, err)
}

func TestPlatformProviderIncidentTransitionsAreDeduplicatedAndRetirementIsExplicit(t *testing.T) {
	preparePlatformProviderMonitorCostTest(t, false)
	claim, err := ClaimPlatformProviderMonitorLease("worker-a", 30*time.Second)
	require.NoError(t, err)
	trigger := PlatformProviderIncidentDecision{
		ProviderName:           "provider-a",
		Kind:                   PlatformProviderIncidentSuccessRateDrop,
		DesiredActive:          true,
		ReasonCode:             "success_rate_below_threshold",
		SampleSize:             20,
		SuccessCount:           10,
		SuccessRateBasisPoints: 5000,
	}
	require.NoError(t, ApplyPlatformProviderIncidentDecisions(claim.Token, []PlatformProviderIncidentDecision{trigger}))
	require.NoError(t, ApplyPlatformProviderIncidentDecisions(claim.Token, []PlatformProviderIncidentDecision{trigger}))

	var eventCount int64
	require.NoError(t, DB.Model(&PlatformProviderAlertEvent{}).Count(&eventCount).Error)
	assert.Equal(t, int64(1), eventCount)
	var deliveryCount int64
	require.NoError(t, DB.Model(&PlatformRelayExternalDelivery{}).Count(&deliveryCount).Error)
	assert.Equal(t, int64(1), deliveryCount)

	recovery := trigger
	recovery.DesiredActive = false
	recovery.ReasonCode = "success_rate_recovered"
	recovery.SuccessCount = 20
	recovery.SuccessRateBasisPoints = 10_000
	require.NoError(t, ApplyPlatformProviderIncidentDecisions(claim.Token, []PlatformProviderIncidentDecision{recovery}))
	require.NoError(t, DB.Model(&PlatformProviderAlertEvent{}).Count(&eventCount).Error)
	assert.Equal(t, int64(2), eventCount)

	ackID := uuid.NewString()
	created, err := AcknowledgePlatformProviderRetirement("provider-a", ackID, "change-123", "planned_provider_retirement")
	require.NoError(t, err)
	assert.True(t, created)
	created, err = AcknowledgePlatformProviderRetirement("provider-a", ackID, "change-123", "planned_provider_retirement")
	require.NoError(t, err)
	assert.False(t, created)
	_, err = AcknowledgePlatformProviderRetirement("provider-a", uuid.NewString(), "change-other", "planned_provider_retirement")
	assert.ErrorIs(t, err, ErrPlatformProviderRetirementCollision)
}

func TestPlatformRelayExternalDeliveryLeaseAndRetryBudgetAreTokenFenced(t *testing.T) {
	preparePlatformProviderMonitorCostTest(t, false)
	require.NoError(t, DB.Transaction(func(tx *gorm.DB) error {
		_, err := CreatePlatformRelayExternalDeliveryTx(
			tx,
			PlatformRelayDeliveryKindProviderAlert,
			uuid.NewString(),
			"request-alert",
			2,
		)
		return err
	}))
	first, err := ClaimPlatformRelayExternalDelivery(PlatformRelayDeliveryKindProviderAlert, 30*time.Second)
	require.NoError(t, err)
	require.NoError(t, DB.Model(&PlatformRelayExternalDelivery{}).
		Where("id = ?", first.Delivery.ID).
		Update("claim_expires_at", time.Unix(1, 0).UTC()).Error)
	second, err := ClaimPlatformRelayExternalDelivery(PlatformRelayDeliveryKindProviderAlert, 30*time.Second)
	require.NoError(t, err)
	assert.NotEqual(t, first.Token, second.Token)

	won, err := CompletePlatformRelayExternalDelivery(
		PlatformRelayDeliveryKindProviderAlert,
		first.Delivery.EventID,
		first.Token,
		204,
	)
	require.NoError(t, err)
	assert.False(t, won)
	state, won, err := ReleasePlatformRelayExternalDelivery(
		PlatformRelayDeliveryKindProviderAlert,
		second.Delivery.EventID,
		second.Token,
		0,
		PlatformRelayDeliveryFailureTransport,
		0,
	)
	require.NoError(t, err)
	assert.True(t, won)
	assert.Equal(t, PlatformRelayDeliveryDeadLetter, state)
}

func TestExplicitZeroChannelCostReconcilesOnlyAfterDelivery(t *testing.T) {
	preparePlatformProviderMonitorCostTest(t, true)
	route := createPlatformProviderMonitorRoute(t, "route-cost", "provider-a")
	relayJobID := uuid.NewString()
	outcome := PlatformProviderTerminalOutcome{
		ID:                uuid.NewString(),
		RouteID:           route.ID,
		RelayJobID:        relayJobID,
		Outcome:           PlatformProviderOutcomeSucceeded,
		FailureOwner:      PlatformProviderFailureOwnerNone,
		OccurredAt:        time.Date(2026, time.August, 6, 11, 0, 0, 0, time.UTC),
		ExternalReference: "provider-success-42",
	}
	created, err := CreatePlatformProviderTerminalOutcome(&outcome)
	require.NoError(t, err)
	assert.True(t, created)

	summary, err := GetPlatformChannelCostReconciliationSummary()
	require.NoError(t, err)
	assert.Equal(t, int64(1), summary.IncompleteRelayJobs)
	assert.False(t, summary.ReconciliationComplete)

	payload := dto.PlatformChannelCostPayload{
		AmountCents:       0,
		IdempotencyKey:    "provider-cost-zero-42",
		ChannelKey:        route.RouteKey,
		ChannelType:       route.ChannelClass,
		OccurredAt:        outcome.OccurredAt,
		ExternalReference: "provider-invoice-zero-42",
		RelayJobID:        relayJobID,
		Note:              "provider explicitly reported zero cost",
	}
	payloadJSON, err := common.Marshal(payload)
	require.NoError(t, err)
	digest := sha256.Sum256(payloadJSON)
	cost := PlatformChannelCostEvent{
		ID:                uuid.NewString(),
		AmountCents:       payload.AmountCents,
		IdempotencyKey:    payload.IdempotencyKey,
		ChannelKey:        payload.ChannelKey,
		ChannelType:       payload.ChannelType,
		OccurredAt:        payload.OccurredAt,
		ExternalReference: payload.ExternalReference,
		RelayJobID:        payload.RelayJobID,
		Note:              payload.Note,
		EvidenceSource:    dto.PlatformChannelCostEvidenceProviderReported,
		PayloadJSON:       string(payloadJSON),
		PayloadSHA256:     fmt.Sprintf("%x", digest),
	}
	created, err = CreatePlatformChannelCostEvent(&cost)
	require.NoError(t, err)
	assert.True(t, created)
	created, err = CreatePlatformChannelCostEvent(&cost)
	require.NoError(t, err)
	assert.False(t, created)

	summary, err = GetPlatformChannelCostReconciliationSummary()
	require.NoError(t, err)
	assert.Equal(t, int64(1), summary.IncompleteRelayJobs)
	assert.Equal(t, int64(1), summary.PendingDeliveries)

	claim, err := ClaimPlatformRelayExternalDelivery(PlatformRelayDeliveryKindChannelCost, 30*time.Second)
	require.NoError(t, err)
	won, err := CompletePlatformRelayExternalDelivery(
		PlatformRelayDeliveryKindChannelCost,
		cost.ID,
		claim.Token,
		201,
	)
	require.NoError(t, err)
	assert.True(t, won)
	summary, err = GetPlatformChannelCostReconciliationSummary()
	require.NoError(t, err)
	assert.Zero(t, summary.IncompleteRelayJobs)
	assert.True(t, summary.ReconciliationComplete)

	recoveryJobID := uuid.NewString()
	recoveryJob := PlatformGenerationJob{
		ID:                                recoveryJobID,
		TenantID:                          uuid.NewString(),
		SourceClientID:                    "platform",
		RequestID:                         "request-native-billing-gap",
		IdempotencyKey:                    "idempotency-native-billing-gap-" + recoveryJobID,
		RequestHash:                       strings.Repeat("b", 64),
		RequestJSON:                       `{}`,
		Model:                             "video-model",
		Mode:                              "text_to_video",
		ExpectedCapabilityRevision:        "sha256:" + strings.Repeat("c", 64),
		CapabilityRevision:                "sha256:" + strings.Repeat("c", 64),
		Status:                            PlatformGenerationStatusProcessing,
		OutputsJSON:                       `[]`,
		ErrorDetailsJSON:                  `{}`,
		NativeBillingReconciliationNeeded: true,
	}
	require.NoError(t, DB.Create(&recoveryJob).Error)
	summary, err = GetPlatformChannelCostReconciliationSummary()
	require.NoError(t, err)
	assert.Equal(t, int64(1), summary.NativeBillingReconciliationJobs)
	assert.False(t, summary.ReconciliationComplete,
		"a recovered polling identity must remain visible until native billing is reconciled")

	err = DB.Exec("UPDATE platform_channel_cost_events SET amount_cents = 1 WHERE id = ?", cost.ID).Error
	assert.Error(t, err)
	_, err = ClaimPlatformRelayExternalDelivery(PlatformRelayDeliveryKindChannelCost, 30*time.Second)
	assert.True(t, errors.Is(err, gorm.ErrRecordNotFound))
}
