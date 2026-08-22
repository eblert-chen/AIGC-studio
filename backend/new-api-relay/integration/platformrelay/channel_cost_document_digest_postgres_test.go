//go:build integration

package platformrelay_test

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/google/uuid"
)

type channelCostDeliveryCapture struct {
	eventID string
	body    string
}

func TestPostgresChannelCostDocumentDigestMigrationPreservesPayloadBytes(t *testing.T) {
	requireNoError(t, model.MigratePlatformProviderMonitorAndCostStorage())
	requireNoError(t, integrationDB.Exec(`
ALTER TABLE platform_channel_cost_events
ALTER COLUMN source_document_sha256 TYPE character(64)
USING source_document_sha256::character(64)`).Error)

	occurredAt := time.Date(2026, time.August, 12, 9, 30, 0, 0, time.UTC)
	providerReported := postgresChannelCostInput(
		dto.PlatformChannelCostEvidenceProviderReported,
		"postgres-provider-reported",
		occurredAt,
	)
	providerInvoice := postgresChannelCostInput(
		dto.PlatformChannelCostEvidenceProviderInvoice,
		"postgres-provider-invoice",
		occurredAt.Add(time.Second),
	)
	providerInvoice.EvidenceReference = "provider-invoice-2026-08-line-1"
	providerInvoice.SourceDocumentSHA256 = strings.Repeat("a", 64)

	created, err := service.EnqueuePlatformChannelCost(providerReported)
	requireNoError(t, err)
	if !created {
		t.Fatal("expected provider_reported cost event to be created")
	}
	created, err = service.EnqueuePlatformChannelCost(providerInvoice)
	requireNoError(t, err)
	if !created {
		t.Fatal("expected provider_invoice cost event to be created")
	}

	assertPostgresStoredDigestLength(t, providerReported.EventID, 64)
	assertPostgresStoredDigestLength(t, providerInvoice.EventID, 64)

	// The production migration converts the legacy bpchar(64) definition, then
	// concurrent startup paths exercise the PostgreSQL append-only installation
	// phase. Every installer must succeed under its transaction-scoped lock.
	requireNoError(t, model.MigratePlatformProviderMonitorAndCostStorage())
	runConcurrentPostgresAppendOnlyGuardInstallations(t, 8)

	var metadata struct {
		UDTName                string `gorm:"column:udt_name"`
		CharacterMaximumLength int64  `gorm:"column:character_maximum_length"`
	}
	requireNoError(t, integrationDB.Raw(`
SELECT udt_name, character_maximum_length
FROM information_schema.columns
WHERE table_schema = current_schema()
  AND table_name = 'platform_channel_cost_events'
  AND column_name = 'source_document_sha256'`).Scan(&metadata).Error)
	if metadata.UDTName != "varchar" || metadata.CharacterMaximumLength != 64 {
		t.Fatalf("unexpected migrated digest column: type=%q length=%d", metadata.UDTName, metadata.CharacterMaximumLength)
	}
	assertPostgresStoredDigestLength(t, providerReported.EventID, 0)
	assertPostgresStoredDigestLength(t, providerInvoice.EventID, 64)
	assertChannelCostReplayAndStoredEvidence(t, providerReported, "")
	assertChannelCostReplayAndStoredEvidence(t, providerInvoice, strings.Repeat("a", 64))
	assertPostgresChannelCostAppendOnlyGuards(t, providerReported.EventID)

	captures := make(chan channelCostDeliveryCapture, 3)
	endpoint := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		body, readErr := io.ReadAll(request.Body)
		if readErr != nil {
			writer.WriteHeader(http.StatusInternalServerError)
			return
		}
		captures <- channelCostDeliveryCapture{
			eventID: request.Header.Get("X-Relay-Event-ID"),
			body:    string(body),
		}
		writer.WriteHeader(http.StatusCreated)
	}))
	t.Cleanup(endpoint.Close)
	config := service.PlatformChannelCostSinkConfig{
		URL:                  endpoint.URL + "/internal/channel-costs",
		InternalServiceToken: "postgres-integration-service-token",
		SigningSecret:        "postgres-integration-signing-secret",
	}

	deliverNextChannelCost(t, config, captures, providerReported.EventID, true)
	deliverNextChannelCost(t, config, captures, providerInvoice.EventID, false)

	operatorAdjustment := postgresChannelCostInput(
		dto.PlatformChannelCostEvidenceOperatorAdjusted,
		"postgres-operator-adjustment",
		occurredAt.Add(2*time.Second),
	)
	created, err = service.EnqueuePlatformChannelCost(operatorAdjustment)
	requireNoError(t, err)
	if !created {
		t.Fatal("expected operator_adjustment cost event to be created")
	}
	assertPostgresStoredDigestLength(t, operatorAdjustment.EventID, 0)
	assertChannelCostReplayAndStoredEvidence(t, operatorAdjustment, "")
	deliverNextChannelCost(t, config, captures, operatorAdjustment.EventID, true)
}

func runConcurrentPostgresAppendOnlyGuardInstallations(t *testing.T, workers int) {
	t.Helper()
	if workers < 2 {
		t.Fatalf("concurrent migration test requires at least two workers, got %d", workers)
	}

	start := make(chan struct{})
	errorsByWorker := make(chan error, workers)
	var waitGroup sync.WaitGroup
	waitGroup.Add(workers)
	for worker := 0; worker < workers; worker++ {
		go func() {
			defer waitGroup.Done()
			<-start
			errorsByWorker <- model.InstallPlatformProviderAppendOnlyGuards()
		}()
	}
	close(start)
	waitGroup.Wait()
	close(errorsByWorker)
	for err := range errorsByWorker {
		requireNoError(t, err)
	}
}

func assertPostgresChannelCostAppendOnlyGuards(t *testing.T, eventID string) {
	t.Helper()
	var triggerCount int64
	requireNoError(t, integrationDB.Raw(`
SELECT COUNT(*)
FROM pg_trigger
WHERE tgrelid = 'platform_channel_cost_events'::regclass
  AND NOT tgisinternal
  AND tgname IN (
    'trg_platform_channel_cost_events_no_mutation',
    'trg_platform_channel_cost_events_no_truncate'
  )`).Scan(&triggerCount).Error)
	if triggerCount != 2 {
		t.Fatalf("channel cost append-only trigger count=%d, want 2", triggerCount)
	}

	statements := []struct {
		name string
		sql  string
		args []any
	}{
		{
			name: "update",
			sql:  "UPDATE platform_channel_cost_events SET amount_cents = amount_cents + 1 WHERE id = ?",
			args: []any{eventID},
		},
		{
			name: "delete",
			sql:  "DELETE FROM platform_channel_cost_events WHERE id = ?",
			args: []any{eventID},
		},
		{
			name: "truncate",
			sql:  "TRUNCATE TABLE platform_channel_cost_events",
		},
	}
	for _, statement := range statements {
		err := integrationDB.Exec(statement.sql, statement.args...).Error
		if err == nil {
			t.Fatalf("channel cost append-only guard allowed %s", statement.name)
		}
		if !strings.Contains(err.Error(), "platform Relay event tables are append-only") {
			t.Fatalf("channel cost %s failed for the wrong reason: %v", statement.name, err)
		}
	}
}

func postgresChannelCostInput(evidenceSource string, keyPrefix string, occurredAt time.Time) dto.PlatformChannelCostInput {
	return dto.PlatformChannelCostInput{
		EventID:           uuid.NewString(),
		AmountCents:       17,
		IdempotencyKey:    keyPrefix + "-" + uuid.NewString(),
		ChannelKey:        "route-" + keyPrefix,
		ChannelType:       model.PlatformGenerationChannelClassOfficialProvider,
		OccurredAt:        occurredAt,
		ExternalReference: "external-" + keyPrefix,
		CompanyID:         uuid.NewString(),
		TaskID:            uuid.NewString(),
		RelayJobID:        uuid.NewString(),
		Note:              "real PostgreSQL document digest regression",
		EvidenceSource:    evidenceSource,
	}
}

func assertPostgresStoredDigestLength(t *testing.T, eventID string, expected int) {
	t.Helper()
	var length int
	requireNoError(t, integrationDB.Raw(`
SELECT octet_length(source_document_sha256)
FROM platform_channel_cost_events
WHERE id = ?`, eventID).Scan(&length).Error)
	if length != expected {
		t.Fatalf("event %s digest storage length=%d, want %d", eventID, length, expected)
	}
}

func assertChannelCostReplayAndStoredEvidence(t *testing.T, input dto.PlatformChannelCostInput, expectedDigest string) {
	t.Helper()
	stored, err := model.GetPlatformChannelCostEvent(input.EventID)
	requireNoError(t, err)
	if stored.SourceDocumentSHA256 != expectedDigest {
		t.Fatalf("event %s stored digest=%q, want %q", input.EventID, stored.SourceDocumentSHA256, expectedDigest)
	}
	created, err := service.EnqueuePlatformChannelCost(input)
	requireNoError(t, err)
	if created {
		t.Fatalf("event %s exact replay created a duplicate", input.EventID)
	}
}

func deliverNextChannelCost(
	t *testing.T,
	config service.PlatformChannelCostSinkConfig,
	captures <-chan channelCostDeliveryCapture,
	expectedEventID string,
	expectDocumentFieldsOmitted bool,
) {
	t.Helper()
	claim, err := model.ClaimPlatformRelayExternalDelivery(model.PlatformRelayDeliveryKindChannelCost, 30*time.Second)
	requireNoError(t, err)
	if claim.Delivery.EventID != expectedEventID {
		t.Fatalf("claimed event %s, want %s", claim.Delivery.EventID, expectedEventID)
	}
	stored, err := model.GetPlatformChannelCostEvent(expectedEventID)
	requireNoError(t, err)
	state, won, err := service.DeliverPlatformChannelCostClaim(context.Background(), *claim, config)
	requireNoError(t, err)
	if !won || state != model.PlatformRelayDeliveryDelivered {
		t.Fatalf("delivery result state=%q won=%t", state, won)
	}
	capture := <-captures
	if capture.eventID != expectedEventID {
		t.Fatalf("delivered event %s, want %s", capture.eventID, expectedEventID)
	}
	if capture.body != stored.PayloadJSON {
		t.Fatalf("event %s delivery bytes changed across persistence", expectedEventID)
	}
	if expectDocumentFieldsOmitted && (strings.Contains(capture.body, "evidence_reference") || strings.Contains(capture.body, "source_document_sha256")) {
		t.Fatalf("event %s unexpectedly delivered empty document fields: %s", expectedEventID, capture.body)
	}
	var delivery model.PlatformRelayExternalDelivery
	requireNoError(t, integrationDB.Where(
		"event_kind = ? AND event_id = ?",
		model.PlatformRelayDeliveryKindChannelCost,
		expectedEventID,
	).First(&delivery).Error)
	if delivery.ResponseStatus != http.StatusCreated {
		t.Fatalf("event %s response status=%d, want %d", expectedEventID, delivery.ResponseStatus, http.StatusCreated)
	}
}
