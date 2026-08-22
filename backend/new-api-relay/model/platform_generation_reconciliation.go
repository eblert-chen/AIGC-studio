package model

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

var (
	ErrPlatformGenerationReconciliationEventImmutable = errors.New("generation reconciliation event is append-only")
	platformGenerationOperationIDPattern              = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$`)
	platformGenerationRequestIDPattern                = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$`)
	platformGenerationProofTokenPattern               = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
	platformGenerationApprovalKeyIDPattern            = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$`)
	platformGenerationApprovalSignaturePattern        = regexp.MustCompile(`^hmac-sha256:[0-9a-f]{64}$`)
)

// PlatformGenerationReconciliationResolution contains the complete durable
// proof submitted by an operator. OperationID is the idempotency boundary;
// RequestID identifies the first HTTP attempt that committed the decision.
type PlatformGenerationReconciliationResolution struct {
	Created                     bool
	UpstreamTaskID              string
	ExpectedRouteID             int64
	ExpectedSubmissionAttempt   int
	ExpectedReconciliationToken string
	OperationID                 string
	RequestID                   string
	VerificationReference       string
	ApprovedBy                  string
	ApprovalReason              string
	ApprovalKeyID               string
	ApprovalSignature           string
}

// PlatformGenerationReconciliationEvent is the immutable Relay-side receipt
// for a manual unknown-submission decision. Mutable job/callback state remains
// in its existing tables and is committed in the same transaction.
type PlatformGenerationReconciliationEvent struct {
	ID                          string    `json:"id" gorm:"type:varchar(36);primaryKey"`
	TenantID                    string    `json:"tenant_id" gorm:"type:varchar(36);not null;uniqueIndex:ux_platform_generation_reconcile_operation,priority:1;index:idx_platform_generation_reconcile_job,priority:1"`
	JobID                       string    `json:"job_id" gorm:"type:varchar(36);not null;index:idx_platform_generation_reconcile_job,priority:2"`
	OperationID                 string    `json:"operation_id" gorm:"type:varchar(128);not null;uniqueIndex:ux_platform_generation_reconcile_operation,priority:2"`
	RequestID                   string    `json:"request_id" gorm:"type:varchar(80);not null"`
	Outcome                     string    `json:"outcome" gorm:"type:varchar(16);not null"`
	UpstreamTaskID              string    `json:"upstream_task_id" gorm:"type:varchar(191);not null"`
	ExpectedRouteID             int64     `json:"expected_route_id" gorm:"not null"`
	ExpectedSubmissionAttempt   int       `json:"expected_submission_attempt" gorm:"not null"`
	ExpectedReconciliationToken string    `json:"expected_reconciliation_token" gorm:"type:char(71);not null"`
	VerificationReference       string    `json:"verification_reference" gorm:"type:varchar(191);not null"`
	ApprovedBy                  string    `json:"approved_by" gorm:"type:varchar(128);not null"`
	ApprovalReason              string    `json:"approval_reason" gorm:"type:varchar(240);not null"`
	ApprovalKeyID               string    `json:"approval_key_id" gorm:"type:varchar(120);not null"`
	ApprovalSignature           string    `json:"approval_signature" gorm:"type:char(76);not null"`
	ResolvedStatus              string    `json:"resolved_status" gorm:"type:varchar(32);not null"`
	PayloadJSON                 string    `json:"-" gorm:"type:text;not null"`
	PayloadSHA256               string    `json:"payload_sha256" gorm:"type:char(64);not null"`
	ResolvedAt                  time.Time `json:"resolved_at" gorm:"not null;index"`
	CreatedAt                   time.Time `json:"created_at" gorm:"not null"`
}

func (PlatformGenerationReconciliationEvent) TableName() string {
	return "platform_generation_reconciliation_events"
}

func (*PlatformGenerationReconciliationEvent) BeforeUpdate(*gorm.DB) error {
	return ErrPlatformGenerationReconciliationEventImmutable
}

func (*PlatformGenerationReconciliationEvent) BeforeDelete(*gorm.DB) error {
	return ErrPlatformGenerationReconciliationEventImmutable
}

type PlatformGenerationReconciliationReceipt struct {
	Event         PlatformGenerationReconciliationEvent
	CurrentStatus string
}

type platformGenerationReconciliationCanonicalPayload struct {
	TenantID                    string `json:"tenant_id"`
	JobID                       string `json:"job_id"`
	OperationID                 string `json:"operation_id"`
	Outcome                     string `json:"outcome"`
	UpstreamTaskID              string `json:"upstream_task_id"`
	ExpectedRouteID             int64  `json:"expected_route_id"`
	ExpectedSubmissionAttempt   int    `json:"expected_submission_attempt"`
	ExpectedReconciliationToken string `json:"expected_reconciliation_token"`
	VerificationReference       string `json:"verification_reference"`
	ApprovedBy                  string `json:"approved_by"`
	ApprovalReason              string `json:"approval_reason"`
	ApprovalKeyID               string `json:"approval_key_id"`
	ApprovalSignature           string `json:"approval_signature"`
	ResolvedStatus              string `json:"resolved_status"`
}

func isCanonicalPlatformGenerationReconciliationUUID(value string) bool {
	parsed, err := uuid.Parse(value)
	return err == nil && parsed.String() == value
}

func newPlatformGenerationReconciliationEvent(
	jobID string,
	tenantID string,
	resolution PlatformGenerationReconciliationResolution,
	now time.Time,
) (*PlatformGenerationReconciliationEvent, error) {
	if !isCanonicalPlatformGenerationReconciliationUUID(jobID) {
		return nil, fmt.Errorf("generation reconciliation job id is invalid")
	}
	if !isCanonicalPlatformGenerationReconciliationUUID(tenantID) {
		return nil, fmt.Errorf("generation reconciliation tenant id is invalid")
	}
	if !platformGenerationOperationIDPattern.MatchString(resolution.OperationID) ||
		!platformGenerationRequestIDPattern.MatchString(resolution.RequestID) ||
		resolution.ExpectedRouteID <= 0 || resolution.ExpectedSubmissionAttempt <= 0 ||
		!platformGenerationProofTokenPattern.MatchString(resolution.ExpectedReconciliationToken) ||
		strings.TrimSpace(resolution.VerificationReference) != resolution.VerificationReference ||
		len(resolution.VerificationReference) < 1 || len(resolution.VerificationReference) > 191 ||
		strings.TrimSpace(resolution.ApprovedBy) != resolution.ApprovedBy ||
		len(resolution.ApprovedBy) < 1 || len(resolution.ApprovedBy) > 128 ||
		strings.TrimSpace(resolution.ApprovalReason) != resolution.ApprovalReason ||
		len(resolution.ApprovalReason) < 3 || len(resolution.ApprovalReason) > 240 ||
		!platformGenerationApprovalKeyIDPattern.MatchString(resolution.ApprovalKeyID) ||
		!platformGenerationApprovalSignaturePattern.MatchString(resolution.ApprovalSignature) {
		return nil, fmt.Errorf("generation reconciliation proof is invalid")
	}

	outcome := "not_created"
	resolvedStatus := PlatformGenerationStatusFailed
	if resolution.Created {
		outcome = "created"
		resolvedStatus = PlatformGenerationStatusProcessing
		if strings.TrimSpace(resolution.UpstreamTaskID) == "" ||
			strings.TrimSpace(resolution.UpstreamTaskID) != resolution.UpstreamTaskID ||
			len(resolution.UpstreamTaskID) > 191 {
			return nil, fmt.Errorf("upstream task id is invalid")
		}
	} else if resolution.UpstreamTaskID != "" {
		return nil, fmt.Errorf("upstream task id must be empty for not_created")
	}

	payload := platformGenerationReconciliationCanonicalPayload{
		TenantID:                    tenantID,
		JobID:                       jobID,
		OperationID:                 resolution.OperationID,
		Outcome:                     outcome,
		UpstreamTaskID:              resolution.UpstreamTaskID,
		ExpectedRouteID:             resolution.ExpectedRouteID,
		ExpectedSubmissionAttempt:   resolution.ExpectedSubmissionAttempt,
		ExpectedReconciliationToken: resolution.ExpectedReconciliationToken,
		VerificationReference:       resolution.VerificationReference,
		ApprovedBy:                  resolution.ApprovedBy,
		ApprovalReason:              resolution.ApprovalReason,
		ApprovalKeyID:               resolution.ApprovalKeyID,
		ApprovalSignature:           resolution.ApprovalSignature,
		ResolvedStatus:              resolvedStatus,
	}
	payloadBytes, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	payloadDigest := sha256.Sum256(payloadBytes)
	eventID := uuid.NewSHA1(
		uuid.NameSpaceURL,
		[]byte("platform-generation-reconciliation-v1\x00"+tenantID+"\x00"+resolution.OperationID),
	).String()
	return &PlatformGenerationReconciliationEvent{
		ID:                          eventID,
		TenantID:                    tenantID,
		JobID:                       jobID,
		OperationID:                 resolution.OperationID,
		RequestID:                   resolution.RequestID,
		Outcome:                     outcome,
		UpstreamTaskID:              resolution.UpstreamTaskID,
		ExpectedRouteID:             resolution.ExpectedRouteID,
		ExpectedSubmissionAttempt:   resolution.ExpectedSubmissionAttempt,
		ExpectedReconciliationToken: resolution.ExpectedReconciliationToken,
		VerificationReference:       resolution.VerificationReference,
		ApprovedBy:                  resolution.ApprovedBy,
		ApprovalReason:              resolution.ApprovalReason,
		ApprovalKeyID:               resolution.ApprovalKeyID,
		ApprovalSignature:           resolution.ApprovalSignature,
		ResolvedStatus:              resolvedStatus,
		PayloadJSON:                 string(payloadBytes),
		PayloadSHA256:               hex.EncodeToString(payloadDigest[:]),
		ResolvedAt:                  now,
		CreatedAt:                   now,
	}, nil
}

func platformGenerationReconciliationEventsEqual(
	left PlatformGenerationReconciliationEvent,
	right PlatformGenerationReconciliationEvent,
) bool {
	// RequestID, timestamps, and approval-key material identify the first
	// committed attempt and therefore do not participate in semantic replay
	// equality. A key rotation must not turn an otherwise identical retry into
	// an operation_id conflict; the original immutable receipt is returned.
	return left.ID == right.ID && left.TenantID == right.TenantID && left.JobID == right.JobID &&
		left.OperationID == right.OperationID && left.Outcome == right.Outcome &&
		left.UpstreamTaskID == right.UpstreamTaskID && left.ExpectedRouteID == right.ExpectedRouteID &&
		left.ExpectedSubmissionAttempt == right.ExpectedSubmissionAttempt &&
		left.ExpectedReconciliationToken == right.ExpectedReconciliationToken &&
		left.VerificationReference == right.VerificationReference && left.ApprovedBy == right.ApprovedBy &&
		left.ApprovalReason == right.ApprovalReason && left.ResolvedStatus == right.ResolvedStatus
}

func findPlatformGenerationReconciliationEventTx(
	tx *gorm.DB,
	tenantID string,
	operationID string,
) (*PlatformGenerationReconciliationEvent, error) {
	var event PlatformGenerationReconciliationEvent
	if err := tx.Where("tenant_id = ? AND operation_id = ?", tenantID, operationID).First(&event).Error; err != nil {
		return nil, err
	}
	return &event, nil
}

func insertPlatformGenerationReconciliationEventTx(
	tx *gorm.DB,
	event PlatformGenerationReconciliationEvent,
) (*PlatformGenerationReconciliationEvent, error) {
	result := tx.Clauses(clause.OnConflict{
		Columns:   []clause.Column{{Name: "tenant_id"}, {Name: "operation_id"}},
		DoNothing: true,
	}).Create(&event)
	if result.Error != nil {
		return nil, result.Error
	}
	if result.RowsAffected == 1 {
		return &event, nil
	}
	existing, err := findPlatformGenerationReconciliationEventTx(tx, event.TenantID, event.OperationID)
	if err != nil {
		return nil, err
	}
	if !platformGenerationReconciliationEventsEqual(*existing, event) {
		return nil, ErrPlatformGenerationReconciliationConflict
	}
	return existing, nil
}

func GetPlatformGenerationReconciliationReceipt(
	jobID string,
	tenantID string,
	operationID string,
) (*PlatformGenerationReconciliationReceipt, error) {
	if !isCanonicalPlatformGenerationReconciliationUUID(jobID) ||
		!isCanonicalPlatformGenerationReconciliationUUID(tenantID) ||
		!platformGenerationOperationIDPattern.MatchString(operationID) {
		return nil, gorm.ErrRecordNotFound
	}
	var receipt PlatformGenerationReconciliationReceipt
	err := DB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Where(
			"tenant_id = ? AND job_id = ? AND operation_id = ?",
			tenantID,
			jobID,
			operationID,
		).First(&receipt.Event).Error; err != nil {
			return err
		}
		var job PlatformGenerationJob
		if err := tx.Select("status").Where("id = ? AND tenant_id = ?", jobID, tenantID).First(&job).Error; err != nil {
			return err
		}
		receipt.CurrentStatus = job.Status
		return nil
	})
	if err != nil {
		return nil, err
	}
	return &receipt, nil
}

// MigratePlatformGenerationReconciliationStorage is a separate wiring point so
// the receipt table and its database-level immutability guards cannot be
// omitted when the rest of the Relay schema is migrated.
func MigratePlatformGenerationReconciliationStorage() error {
	return MigratePlatformGenerationReconciliationStorageWithDB(DB)
}

func MigratePlatformGenerationReconciliationStorageWithDB(db *gorm.DB) error {
	if db == nil {
		return fmt.Errorf("database is not initialized")
	}
	if err := db.AutoMigrate(&PlatformGenerationReconciliationEvent{}); err != nil {
		return err
	}
	return installPlatformGenerationReconciliationAppendOnlyGuardsWithDB(db)
}

func installPlatformGenerationReconciliationAppendOnlyGuards() error {
	return installPlatformGenerationReconciliationAppendOnlyGuardsWithDB(DB)
}

func installPlatformGenerationReconciliationAppendOnlyGuardsWithDB(db *gorm.DB) error {
	if db == nil {
		return fmt.Errorf("database is not initialized")
	}
	const table = "platform_generation_reconciliation_events"
	switch db.Dialector.Name() {
	case "postgres":
		if err := db.Exec(`
CREATE OR REPLACE FUNCTION reject_platform_generation_reconciliation_event_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'platform generation reconciliation events are append-only';
END;
$$ LANGUAGE plpgsql`).Error; err != nil {
			return err
		}
		for _, trigger := range []string{
			"trg_platform_generation_reconciliation_events_no_mutation",
			"trg_platform_generation_reconciliation_events_no_truncate",
		} {
			if err := db.Exec(fmt.Sprintf("DROP TRIGGER IF EXISTS %s ON %s", trigger, table)).Error; err != nil {
				return err
			}
		}
		if err := db.Exec("CREATE TRIGGER trg_platform_generation_reconciliation_events_no_mutation BEFORE UPDATE OR DELETE ON " + table + " FOR EACH ROW EXECUTE FUNCTION reject_platform_generation_reconciliation_event_mutation()").Error; err != nil {
			return err
		}
		return db.Exec("CREATE TRIGGER trg_platform_generation_reconciliation_events_no_truncate BEFORE TRUNCATE ON " + table + " FOR EACH STATEMENT EXECUTE FUNCTION reject_platform_generation_reconciliation_event_mutation()").Error
	case "mysql":
		for _, operation := range []string{"UPDATE", "DELETE"} {
			trigger := "trg_platform_generation_reconciliation_no_" + strings.ToLower(operation)
			if err := db.Exec("DROP TRIGGER IF EXISTS " + trigger).Error; err != nil {
				return err
			}
			if err := db.Exec(fmt.Sprintf(
				"CREATE TRIGGER %s BEFORE %s ON %s FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'platform generation reconciliation events are append-only'",
				trigger,
				operation,
				table,
			)).Error; err != nil {
				return err
			}
		}
	default:
		for _, operation := range []string{"UPDATE", "DELETE"} {
			trigger := "trg_platform_generation_reconciliation_no_" + strings.ToLower(operation)
			if err := db.Exec(fmt.Sprintf(
				"CREATE TRIGGER IF NOT EXISTS %s BEFORE %s ON %s BEGIN SELECT RAISE(ABORT, 'platform generation reconciliation events are append-only'); END",
				trigger,
				operation,
				table,
			)).Error; err != nil {
				return err
			}
		}
	}
	return nil
}
