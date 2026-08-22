package model

import (
	"encoding/hex"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

const PlatformChannelCostMaxAmountCents int64 = 9_000_000_000_000_000

// platformProviderAppendOnlyMigrationAdvisoryLock serializes PostgreSQL
// append-only guard installation across concurrently starting Relay masters.
// A transaction-scoped lock is required because every trigger is replaced in
// one transaction: existing guards remain visible until the atomic commit and
// a second starter cannot race a DROP/CREATE pair.
const platformProviderAppendOnlyMigrationAdvisoryLock int64 = 0x41564944454f0001

var (
	ErrPlatformChannelCostEventCollision = errors.New("channel cost idempotency key is already used by a different event")
	ErrPlatformChannelCostImmutable      = errors.New("channel cost event is append-only")
)

// PlatformChannelCostEvent is an immutable, evidence-backed event. Delivery
// state lives in PlatformRelayExternalDelivery so retries never mutate cost
// facts. AmountCents is signed to support provider credits and corrections.
type PlatformChannelCostEvent struct {
	ID                string    `json:"id" gorm:"type:varchar(36);primaryKey"`
	AmountCents       int64     `json:"amount_cents" gorm:"not null"`
	IdempotencyKey    string    `json:"idempotency_key" gorm:"type:varchar(160);not null;uniqueIndex"`
	ChannelKey        string    `json:"channel_key" gorm:"type:varchar(120);not null;index"`
	ChannelType       string    `json:"channel_type" gorm:"type:varchar(32);not null"`
	OccurredAt        time.Time `json:"occurred_at" gorm:"not null;index"`
	ExternalReference string    `json:"external_reference" gorm:"type:varchar(240);not null"`
	CompanyID         string    `json:"company_id" gorm:"type:varchar(64);index"`
	TaskID            string    `json:"task_id" gorm:"type:varchar(64);index"`
	RelayJobID        string    `json:"relay_job_id" gorm:"type:varchar(36);index"`
	Note              string    `json:"note" gorm:"type:varchar(240);not null"`
	EvidenceSource    string    `json:"evidence_source" gorm:"type:varchar(32);not null"`
	EvidenceReference string    `json:"evidence_reference" gorm:"type:varchar(240);not null"`
	// This evidence is optional for provider_reported/operator_adjustment.
	// PostgreSQL character(64) blank-pads an empty string, which changes the
	// persisted payload when it is read back for signed delivery. varchar(64)
	// preserves the exact empty/non-empty contract.
	SourceDocumentSHA256 string    `json:"source_document_sha256" gorm:"type:varchar(64);not null"`
	PayloadJSON          string    `json:"-" gorm:"type:text;not null"`
	PayloadSHA256        string    `json:"payload_sha256" gorm:"type:char(64);not null"`
	CreatedAt            time.Time `json:"created_at"`
}

func (PlatformChannelCostEvent) TableName() string {
	return "platform_channel_cost_events"
}

func (*PlatformChannelCostEvent) BeforeUpdate(*gorm.DB) error {
	return ErrPlatformChannelCostImmutable
}

func (*PlatformChannelCostEvent) BeforeDelete(*gorm.DB) error {
	return ErrPlatformChannelCostImmutable
}

type PlatformChannelCostReconciliationSummary struct {
	SuccessfulRelayJobs             int64 `json:"successful_relay_jobs"`
	ExplicitCostRelayJobs           int64 `json:"explicit_cost_relay_jobs"`
	DeliveredCostRelayJobs          int64 `json:"delivered_cost_relay_jobs"`
	IncompleteRelayJobs             int64 `json:"incomplete_relay_jobs"`
	NativeBillingReconciliationJobs int64 `json:"native_billing_reconciliation_jobs"`
	PendingDeliveries               int64 `json:"pending_deliveries"`
	ClaimedDeliveries               int64 `json:"claimed_deliveries"`
	DeadLetterDeliveries            int64 `json:"dead_letter_deliveries"`
	ReconciliationComplete          bool  `json:"reconciliation_complete"`
}

func CreatePlatformChannelCostEvent(event *PlatformChannelCostEvent) (bool, error) {
	if err := validatePlatformChannelCostEvent(event); err != nil {
		return false, err
	}
	created := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		event.OccurredAt = event.OccurredAt.UTC()
		event.CreatedAt = now
		result := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(event)
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected == 0 {
			var existing PlatformChannelCostEvent
			if err := tx.Where("idempotency_key = ? OR id = ?", event.IdempotencyKey, event.ID).First(&existing).Error; err != nil {
				return err
			}
			if !platformChannelCostEventsEqual(existing, *event) {
				return ErrPlatformChannelCostEventCollision
			}
			*event = existing
			return nil
		}

		created = true
		_, err = CreatePlatformRelayExternalDeliveryTx(
			tx,
			PlatformRelayDeliveryKindChannelCost,
			event.ID,
			"relay-channel-cost-"+event.ID,
			DefaultPlatformRelayDeliveryMaxAttempts,
		)
		return err
	})
	return created, err
}

func GetPlatformChannelCostEvent(eventID string) (*PlatformChannelCostEvent, error) {
	var event PlatformChannelCostEvent
	err := DB.Where("id = ?", eventID).First(&event).Error
	return &event, err
}

// GetPlatformChannelCostReconciliationSummary treats only a delivered explicit
// cost event as reconciled. A missing event, a queued delivery, and a dead
// letter all remain visibly incomplete; zero amount is complete once delivered.
func GetPlatformChannelCostReconciliationSummary() (PlatformChannelCostReconciliationSummary, error) {
	var summary PlatformChannelCostReconciliationSummary
	successfulJobs := DB.Model(&PlatformProviderTerminalOutcome{}).
		Where("outcome = ? AND relay_job_id <> ?", PlatformProviderOutcomeSucceeded, "")
	if err := successfulJobs.Distinct("relay_job_id").Count(&summary.SuccessfulRelayJobs).Error; err != nil {
		return summary, err
	}
	if err := DB.Model(&PlatformChannelCostEvent{}).
		Where("relay_job_id <> ?", "").
		Distinct("relay_job_id").
		Count(&summary.ExplicitCostRelayJobs).Error; err != nil {
		return summary, err
	}

	deliveredJobs := DB.Table("platform_channel_cost_events AS costs").
		Select("costs.relay_job_id").
		Joins("JOIN platform_relay_external_deliveries AS deliveries ON deliveries.event_kind = ? AND deliveries.event_id = costs.id", PlatformRelayDeliveryKindChannelCost).
		Where("deliveries.state = ? AND costs.relay_job_id <> ?", PlatformRelayDeliveryDelivered, "")
	if err := DB.Table("(?) AS delivered_cost_jobs", deliveredJobs).
		Distinct("relay_job_id").Count(&summary.DeliveredCostRelayJobs).Error; err != nil {
		return summary, err
	}
	if err := successfulJobs.
		Where("relay_job_id NOT IN (?)", deliveredJobs).
		Distinct("relay_job_id").
		Count(&summary.IncompleteRelayJobs).Error; err != nil {
		return summary, err
	}
	if err := DB.Model(&PlatformGenerationJob{}).
		Where("native_billing_reconciliation_needed = ?", true).
		Count(&summary.NativeBillingReconciliationJobs).Error; err != nil {
		return summary, err
	}

	deliveryCounts, err := GetPlatformRelayDeliveryCounts(PlatformRelayDeliveryKindChannelCost)
	if err != nil {
		return summary, err
	}
	summary.PendingDeliveries = deliveryCounts.Pending
	summary.ClaimedDeliveries = deliveryCounts.Claimed
	summary.DeadLetterDeliveries = deliveryCounts.DeadLetter
	summary.ReconciliationComplete = summary.IncompleteRelayJobs == 0 &&
		summary.NativeBillingReconciliationJobs == 0 &&
		summary.PendingDeliveries == 0 && summary.ClaimedDeliveries == 0 && summary.DeadLetterDeliveries == 0
	return summary, nil
}

// PlatformProviderMonitorAndCostModels is the explicit migration wiring point.
// The root migration must include every returned model before workers start.
func PlatformProviderMonitorAndCostModels() []any {
	return []any{
		&PlatformProviderMonitorLease{},
		&PlatformProviderRouteHealth{},
		&PlatformProviderTerminalOutcome{},
		&PlatformProviderIncident{},
		&PlatformProviderAlertEvent{},
		&PlatformProviderRetirementAcknowledgement{},
		&PlatformProviderContractRate{},
		&PlatformChannelCostReconciliation{},
		&PlatformChannelCostEvent{},
		&PlatformRelayExternalDelivery{},
		&PlatformDownloadEdgeTicket{},
		&PlatformDownloadCompletionEvent{},
		&PlatformDownloadCompletionProof{},
		&PlatformTaskStageEvent{},
		&PlatformOperationsSnapshotEvent{},
	}
}

// MigratePlatformProviderMonitorAndCostStorage is intentionally not called
// from init. The application migration path must invoke it explicitly so a
// partially migrated candidate cannot report ready.
func MigratePlatformProviderMonitorAndCostStorage() error {
	return MigratePlatformProviderMonitorAndCostStorageWithDB(DB)
}

func MigratePlatformProviderMonitorAndCostStorageWithDB(db *gorm.DB) error {
	if db == nil {
		return fmt.Errorf("database is not initialized")
	}
	if err := migratePlatformChannelCostDocumentDigestStorageWithDB(db); err != nil {
		return err
	}
	if err := db.AutoMigrate(PlatformProviderMonitorAndCostModels()...); err != nil {
		return err
	}
	return InstallPlatformProviderAppendOnlyGuardsWithDB(db)
}

// migratePlatformChannelCostDocumentDigestStorage runs before AutoMigrate so
// an existing PostgreSQL character(64) column is converted deliberately rather
// than through a dialect-dependent generic migration. The USING expression
// removes only legacy right-padding; a real 64-character lowercase SHA-256 is
// preserved byte-for-byte. ALTER COLUMN is atomic and safe to repeat across
// concurrently starting Relay processes.
func migratePlatformChannelCostDocumentDigestStorage() error {
	return migratePlatformChannelCostDocumentDigestStorageWithDB(DB)
}

func migratePlatformChannelCostDocumentDigestStorageWithDB(db *gorm.DB) error {
	if db == nil {
		return fmt.Errorf("database is not initialized")
	}
	if db.Dialector.Name() != "postgres" {
		// SQLite does not blank-pad character declarations. MySQL's ordinary
		// AutoMigrate path owns its column definition change.
		return nil
	}
	migrator := db.Migrator()
	if !migrator.HasTable(&PlatformChannelCostEvent{}) ||
		!migrator.HasColumn(&PlatformChannelCostEvent{}, "SourceDocumentSHA256") {
		return nil
	}

	var column struct {
		UDTName                string `gorm:"column:udt_name"`
		CharacterMaximumLength *int64 `gorm:"column:character_maximum_length"`
	}
	result := db.Raw(`
SELECT udt_name, character_maximum_length
FROM information_schema.columns
WHERE table_schema = current_schema()
  AND table_name = 'platform_channel_cost_events'
  AND column_name = 'source_document_sha256'`).Scan(&column)
	if result.Error != nil {
		return result.Error
	}
	if result.RowsAffected != 1 {
		return fmt.Errorf("platform channel cost document digest column could not be inspected")
	}
	if column.CharacterMaximumLength == nil || *column.CharacterMaximumLength != 64 {
		return fmt.Errorf("platform channel cost document digest column length is invalid")
	}
	switch column.UDTName {
	case "varchar":
		return nil
	case "bpchar":
		return db.Exec(`
ALTER TABLE platform_channel_cost_events
ALTER COLUMN source_document_sha256 TYPE varchar(64)
USING rtrim(source_document_sha256::text, ' ')::varchar(64)`).Error
	default:
		return fmt.Errorf("platform channel cost document digest column type %q is unsupported", column.UDTName)
	}
}

func validatePlatformChannelCostEvent(event *PlatformChannelCostEvent) error {
	if event == nil {
		return fmt.Errorf("channel cost event is required")
	}
	if parsed, err := uuid.Parse(event.ID); err != nil || parsed.String() != event.ID {
		return fmt.Errorf("channel cost event id is invalid")
	}
	if event.AmountCents < -PlatformChannelCostMaxAmountCents || event.AmountCents > PlatformChannelCostMaxAmountCents {
		return fmt.Errorf("channel cost amount is outside the supported range")
	}
	if len(event.IdempotencyKey) < 8 || len(event.IdempotencyKey) > 160 || strings.TrimSpace(event.IdempotencyKey) != event.IdempotencyKey ||
		event.ChannelKey == "" || len(event.ChannelKey) > 120 || strings.TrimSpace(event.ChannelKey) != event.ChannelKey {
		return fmt.Errorf("channel cost identity is invalid")
	}
	switch event.ChannelType {
	case PlatformGenerationChannelClassReverseEngineered,
		PlatformGenerationChannelClassThirdPartyAPI,
		PlatformGenerationChannelClassOfficialProvider:
	default:
		return fmt.Errorf("channel cost channel type is invalid")
	}
	if event.OccurredAt.IsZero() || event.ExternalReference == "" || len(event.ExternalReference) > 240 || strings.TrimSpace(event.ExternalReference) != event.ExternalReference {
		return fmt.Errorf("channel cost occurrence is invalid")
	}
	if len(event.CompanyID) > 64 || len(event.TaskID) > 64 || len(event.RelayJobID) > 36 || len(event.Note) > 240 {
		return fmt.Errorf("channel cost linkage is invalid")
	}
	if event.RelayJobID != "" {
		if parsed, err := uuid.Parse(event.RelayJobID); err != nil || parsed.String() != event.RelayJobID {
			return fmt.Errorf("channel cost Relay job id is invalid")
		}
	}
	switch event.EvidenceSource {
	case "provider_reported", "provider_invoice", "contract_rate", "operator_adjustment":
	default:
		return fmt.Errorf("channel cost evidence source is invalid")
	}
	if event.EvidenceSource == "provider_invoice" || event.EvidenceSource == "contract_rate" {
		decodedHash, decodeErr := hex.DecodeString(event.SourceDocumentSHA256)
		if event.EvidenceReference == "" || len(event.EvidenceReference) > 240 || strings.TrimSpace(event.EvidenceReference) != event.EvidenceReference ||
			decodeErr != nil || len(decodedHash) != 32 || strings.ToLower(event.SourceDocumentSHA256) != event.SourceDocumentSHA256 {
			return fmt.Errorf("channel cost document evidence is invalid")
		}
	} else if event.EvidenceReference != "" || event.SourceDocumentSHA256 != "" {
		return fmt.Errorf("channel cost document evidence is not allowed")
	}
	if event.PayloadJSON == "" || len(event.PayloadSHA256) != 64 {
		return fmt.Errorf("channel cost payload is invalid")
	}
	return nil
}

func platformChannelCostEventsEqual(left PlatformChannelCostEvent, right PlatformChannelCostEvent) bool {
	return left.ID == right.ID && left.AmountCents == right.AmountCents && left.IdempotencyKey == right.IdempotencyKey &&
		left.ChannelKey == right.ChannelKey && left.ChannelType == right.ChannelType &&
		left.OccurredAt.UTC().Equal(right.OccurredAt.UTC()) && left.ExternalReference == right.ExternalReference &&
		left.CompanyID == right.CompanyID && left.TaskID == right.TaskID && left.RelayJobID == right.RelayJobID &&
		left.Note == right.Note && left.EvidenceSource == right.EvidenceSource &&
		left.EvidenceReference == right.EvidenceReference && left.SourceDocumentSHA256 == right.SourceDocumentSHA256 &&
		left.PayloadSHA256 == right.PayloadSHA256 && left.PayloadJSON == right.PayloadJSON
}

// InstallPlatformProviderAppendOnlyGuards installs the database-enforced
// mutation guards after the event tables have been migrated. PostgreSQL
// callers may invoke it concurrently from independent startup paths.
func InstallPlatformProviderAppendOnlyGuards() error {
	return InstallPlatformProviderAppendOnlyGuardsWithDB(DB)
}

func InstallPlatformProviderAppendOnlyGuardsWithDB(db *gorm.DB) error {
	if db == nil {
		return fmt.Errorf("database is not initialized")
	}
	tables := []string{
		"platform_provider_terminal_outcomes",
		"platform_provider_alert_events",
		"platform_provider_retirement_acknowledgements",
		"platform_provider_contract_rates",
		"platform_channel_cost_events",
		"platform_download_completion_events",
		"platform_download_completion_proofs",
		"platform_task_stage_events",
		"platform_operations_snapshot_events",
	}
	switch db.Dialector.Name() {
	case "postgres":
		return db.Transaction(func(tx *gorm.DB) error {
			if err := tx.Exec(
				"SELECT pg_advisory_xact_lock(?)",
				platformProviderAppendOnlyMigrationAdvisoryLock,
			).Error; err != nil {
				return err
			}
			if err := tx.Exec(`
CREATE OR REPLACE FUNCTION reject_platform_relay_append_only_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'platform Relay event tables are append-only';
END;
$$ LANGUAGE plpgsql`).Error; err != nil {
				return err
			}
			for _, table := range tables {
				rowTrigger := "trg_" + table + "_no_mutation"
				truncateTrigger := "trg_" + table + "_no_truncate"
				if err := tx.Exec(fmt.Sprintf("DROP TRIGGER IF EXISTS %s ON %s", rowTrigger, table)).Error; err != nil {
					return err
				}
				if err := tx.Exec(fmt.Sprintf(
					"CREATE TRIGGER %s BEFORE UPDATE OR DELETE ON %s FOR EACH ROW EXECUTE FUNCTION reject_platform_relay_append_only_mutation()",
					rowTrigger,
					table,
				)).Error; err != nil {
					return err
				}
				if err := tx.Exec(fmt.Sprintf("DROP TRIGGER IF EXISTS %s ON %s", truncateTrigger, table)).Error; err != nil {
					return err
				}
				if err := tx.Exec(fmt.Sprintf(
					"CREATE TRIGGER %s BEFORE TRUNCATE ON %s FOR EACH STATEMENT EXECUTE FUNCTION reject_platform_relay_append_only_mutation()",
					truncateTrigger,
					table,
				)).Error; err != nil {
					return err
				}
			}
			return nil
		})
	case "mysql":
		for _, table := range tables {
			for _, operation := range []string{"UPDATE", "DELETE"} {
				trigger := "trg_" + table + "_no_" + strings.ToLower(operation)
				if err := db.Exec("DROP TRIGGER IF EXISTS " + trigger).Error; err != nil {
					return err
				}
				if err := db.Exec(fmt.Sprintf(
					"CREATE TRIGGER %s BEFORE %s ON %s FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'platform Relay event tables are append-only'",
					trigger,
					operation,
					table,
				)).Error; err != nil {
					return err
				}
			}
		}
	default:
		for _, table := range tables {
			for _, operation := range []string{"UPDATE", "DELETE"} {
				trigger := "trg_" + table + "_no_" + strings.ToLower(operation)
				statement := fmt.Sprintf(
					"CREATE TRIGGER IF NOT EXISTS %s BEFORE %s ON %s BEGIN SELECT RAISE(ABORT, 'platform Relay event tables are append-only'); END",
					trigger,
					operation,
					table,
				)
				if err := db.Exec(statement).Error; err != nil {
					return err
				}
			}
		}
	}
	return nil
}
