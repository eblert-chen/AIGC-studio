package model

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
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
	platformGenerationProviderResponseDigestPattern   = regexp.MustCompile(`^[0-9a-f]{64}$`)
)

// PlatformGenerationSynchronousResultEvidence is private worker evidence for
// reconstructing a terminal native task after a synchronous provider response
// was lost on the return path. ResultURL is intentionally excluded from the
// public reconciliation receipt; its SHA-256 digest remains in the immutable
// event while the URL is consumed by the verified artifact-transfer worker.
type PlatformGenerationSynchronousResultEvidence struct {
	ProviderModelID        string
	ProviderResponseSHA256 string
	ResultURL              string
	Size                   string
	ProviderCreatedAt      time.Time
	GeneratedImages        int
	OutputTokens           int
	TotalTokens            int
}

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
	SynchronousResult           *PlatformGenerationSynchronousResultEvidence
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
	TenantID                    string                                              `json:"tenant_id"`
	JobID                       string                                              `json:"job_id"`
	OperationID                 string                                              `json:"operation_id"`
	Outcome                     string                                              `json:"outcome"`
	UpstreamTaskID              string                                              `json:"upstream_task_id"`
	ExpectedRouteID             int64                                               `json:"expected_route_id"`
	ExpectedSubmissionAttempt   int                                                 `json:"expected_submission_attempt"`
	ExpectedReconciliationToken string                                              `json:"expected_reconciliation_token"`
	VerificationReference       string                                              `json:"verification_reference"`
	ApprovedBy                  string                                              `json:"approved_by"`
	ApprovalReason              string                                              `json:"approval_reason"`
	ApprovalKeyID               string                                              `json:"approval_key_id"`
	ApprovalSignature           string                                              `json:"approval_signature"`
	SynchronousResult           *platformGenerationReconciliationSynchronousPayload `json:"synchronous_result,omitempty"`
	ResolvedStatus              string                                              `json:"resolved_status"`
}

type platformGenerationReconciliationSynchronousPayload struct {
	ProviderModelID        string `json:"provider_model_id"`
	ProviderResponseSHA256 string `json:"provider_response_sha256"`
	ArtifactURLSHA256      string `json:"artifact_url_sha256"`
	ProviderCreatedAt      string `json:"provider_created_at"`
	GeneratedImages        int    `json:"generated_images"`
	OutputTokens           int    `json:"output_tokens"`
	TotalTokens            int    `json:"total_tokens"`
}

// PlatformGenerationReconciliationSynchronousReceipt is the secret-free,
// immutable subset of a synchronous provider result that may be returned to
// operations clients. The temporary provider URL is represented only by its
// digest and is never reconstructed from the audit event.
type PlatformGenerationReconciliationSynchronousReceipt struct {
	ProviderModelID        string
	ProviderResponseSHA256 string
	ArtifactURLSHA256      string
	ProviderCreatedAt      time.Time
	GeneratedImages        int
	OutputTokens           int
	TotalTokens            int
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
	var synchronousPayload *platformGenerationReconciliationSynchronousPayload
	if resolution.SynchronousResult != nil {
		if !resolution.Created {
			return nil, fmt.Errorf("synchronous result evidence requires created outcome")
		}
		evidence := resolution.SynchronousResult
		if strings.TrimSpace(evidence.ProviderModelID) != evidence.ProviderModelID || evidence.ProviderModelID == "" || len(evidence.ProviderModelID) > 128 ||
			!platformGenerationProviderResponseDigestPattern.MatchString(evidence.ProviderResponseSHA256) ||
			resolution.UpstreamTaskID != "seedream:"+evidence.ProviderResponseSHA256 ||
			strings.TrimSpace(evidence.ResultURL) != evidence.ResultURL || evidence.ResultURL == "" || len(evidence.ResultURL) > 8192 ||
			strings.TrimSpace(evidence.Size) != evidence.Size || evidence.Size == "" || len(evidence.Size) > 32 ||
			evidence.ProviderCreatedAt.IsZero() || evidence.GeneratedImages < 1 || evidence.GeneratedImages > 16 ||
			evidence.OutputTokens < 0 || evidence.TotalTokens < evidence.OutputTokens {
			return nil, fmt.Errorf("synchronous result evidence is invalid")
		}
		artifactDigest := sha256.Sum256([]byte(evidence.ResultURL))
		synchronousPayload = &platformGenerationReconciliationSynchronousPayload{
			ProviderModelID:        evidence.ProviderModelID,
			ProviderResponseSHA256: evidence.ProviderResponseSHA256,
			ArtifactURLSHA256:      hex.EncodeToString(artifactDigest[:]),
			ProviderCreatedAt:      evidence.ProviderCreatedAt.UTC().Format(time.RFC3339),
			GeneratedImages:        evidence.GeneratedImages,
			OutputTokens:           evidence.OutputTokens,
			TotalTokens:            evidence.TotalTokens,
		}
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
		SynchronousResult:           synchronousPayload,
		ResolvedStatus:              resolvedStatus,
	}
	payloadBytes, err := common.Marshal(payload)
	if err != nil {
		return nil, err
	}
	payloadDigest := sha256.Sum256(payloadBytes)
	eventID := uuid.NewSHA1(
		uuid.NameSpaceURL,
		[]byte("platform-generation-reconciliation-v1\x00"+tenantID+"\x00"+resolution.OperationID),
	).String()
	event := &PlatformGenerationReconciliationEvent{
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
	}
	return event, nil
}

func platformGenerationReconciliationEventsEqual(
	left PlatformGenerationReconciliationEvent,
	right PlatformGenerationReconciliationEvent,
) bool {
	// RequestID and timestamps identify the first HTTP attempt and therefore do
	// not participate in semantic replay equality. Approval key identity and
	// signature do participate: they are part of the operator evidence, so a
	// later request may replay an operation only when its complete signed body is
	// identical to the immutable receipt.
	if !(left.ID == right.ID && left.TenantID == right.TenantID && left.JobID == right.JobID &&
		left.OperationID == right.OperationID && left.Outcome == right.Outcome &&
		left.UpstreamTaskID == right.UpstreamTaskID && left.ExpectedRouteID == right.ExpectedRouteID &&
		left.ExpectedSubmissionAttempt == right.ExpectedSubmissionAttempt &&
		left.ExpectedReconciliationToken == right.ExpectedReconciliationToken &&
		left.VerificationReference == right.VerificationReference && left.ApprovedBy == right.ApprovedBy &&
		left.ApprovalReason == right.ApprovalReason && left.ApprovalKeyID == right.ApprovalKeyID &&
		left.ApprovalSignature == right.ApprovalSignature && left.ResolvedStatus == right.ResolvedStatus) {
		return false
	}
	leftPayload, leftErr := platformGenerationReconciliationCanonicalPayloadFromEvent(left)
	rightPayload, rightErr := platformGenerationReconciliationCanonicalPayloadFromEvent(right)
	if leftErr != nil || rightErr != nil {
		return false
	}
	return platformGenerationReconciliationSynchronousPayloadsEqual(leftPayload.SynchronousResult, rightPayload.SynchronousResult)
}

func platformGenerationReconciliationSynchronousPayloadsEqual(
	left *platformGenerationReconciliationSynchronousPayload,
	right *platformGenerationReconciliationSynchronousPayload,
) bool {
	if left == nil || right == nil {
		return left == nil && right == nil
	}
	return *left == *right
}

func platformGenerationReconciliationCanonicalPayloadFromEvent(
	event PlatformGenerationReconciliationEvent,
) (platformGenerationReconciliationCanonicalPayload, error) {
	var payload platformGenerationReconciliationCanonicalPayload
	if strings.TrimSpace(event.PayloadJSON) == "" {
		return payload, errors.New("generation reconciliation payload is missing")
	}
	digest := sha256.Sum256([]byte(event.PayloadJSON))
	if event.PayloadSHA256 != hex.EncodeToString(digest[:]) {
		return payload, errors.New("generation reconciliation payload digest is invalid")
	}
	if err := common.RejectDuplicateJSONKeys([]byte(event.PayloadJSON)); err != nil {
		return payload, fmt.Errorf("generation reconciliation payload is invalid: %w", err)
	}
	if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(event.PayloadJSON), &payload); err != nil {
		return payload, fmt.Errorf("generation reconciliation payload is invalid: %w", err)
	}
	if payload.TenantID != event.TenantID || payload.JobID != event.JobID || payload.OperationID != event.OperationID ||
		payload.Outcome != event.Outcome || payload.UpstreamTaskID != event.UpstreamTaskID ||
		payload.ExpectedRouteID != event.ExpectedRouteID || payload.ExpectedSubmissionAttempt != event.ExpectedSubmissionAttempt ||
		payload.ExpectedReconciliationToken != event.ExpectedReconciliationToken ||
		payload.VerificationReference != event.VerificationReference || payload.ApprovedBy != event.ApprovedBy ||
		payload.ApprovalReason != event.ApprovalReason || payload.ApprovalKeyID != event.ApprovalKeyID ||
		payload.ApprovalSignature != event.ApprovalSignature || payload.ResolvedStatus != event.ResolvedStatus {
		return payload, errors.New("generation reconciliation payload does not match its event")
	}
	return payload, nil
}

// PlatformGenerationReconciliationSynchronousResult validates the immutable
// event payload and returns the public-safe synchronous-result receipt.
func PlatformGenerationReconciliationSynchronousResult(
	event PlatformGenerationReconciliationEvent,
) (*PlatformGenerationReconciliationSynchronousReceipt, error) {
	payload, err := platformGenerationReconciliationCanonicalPayloadFromEvent(event)
	if err != nil {
		return nil, err
	}
	if payload.SynchronousResult == nil {
		return nil, nil
	}
	synchronous := payload.SynchronousResult
	providerCreatedAt, err := time.Parse(time.RFC3339, synchronous.ProviderCreatedAt)
	if err != nil || providerCreatedAt.UTC().Format(time.RFC3339) != synchronous.ProviderCreatedAt ||
		!platformGenerationProviderResponseDigestPattern.MatchString(synchronous.ProviderResponseSHA256) ||
		!platformGenerationProviderResponseDigestPattern.MatchString(synchronous.ArtifactURLSHA256) ||
		synchronous.ProviderModelID == "" || synchronous.GeneratedImages < 1 ||
		synchronous.OutputTokens < 0 || synchronous.TotalTokens < synchronous.OutputTokens {
		return nil, errors.New("generation reconciliation synchronous result is invalid")
	}
	return &PlatformGenerationReconciliationSynchronousReceipt{
		ProviderModelID:        synchronous.ProviderModelID,
		ProviderResponseSHA256: synchronous.ProviderResponseSHA256,
		ArtifactURLSHA256:      synchronous.ArtifactURLSHA256,
		ProviderCreatedAt:      providerCreatedAt.UTC(),
		GeneratedImages:        synchronous.GeneratedImages,
		OutputTokens:           synchronous.OutputTokens,
		TotalTokens:            synchronous.TotalTokens,
	}, nil
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

// GetPlatformGenerationReconciliationReceiptByOperation resolves the durable
// idempotency identity before consulting mutable unknown-submission state. The
// event and its current job status are read in one transaction; callers must
// still compare Event.JobID with the requested resource before replaying it.
func GetPlatformGenerationReconciliationReceiptByOperation(
	tenantID string,
	operationID string,
) (*PlatformGenerationReconciliationReceipt, error) {
	if !isCanonicalPlatformGenerationReconciliationUUID(tenantID) ||
		!platformGenerationOperationIDPattern.MatchString(operationID) {
		return nil, gorm.ErrRecordNotFound
	}
	var receipt PlatformGenerationReconciliationReceipt
	err := DB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Where(
			"tenant_id = ? AND operation_id = ?",
			tenantID,
			operationID,
		).First(&receipt.Event).Error; err != nil {
			return err
		}
		var job PlatformGenerationJob
		if err := tx.Select("status").Where(
			"id = ? AND tenant_id = ?",
			receipt.Event.JobID,
			tenantID,
		).First(&job).Error; err != nil {
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
