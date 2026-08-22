package model

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

const (
	PlatformGenerationStatusQueued                 = "queued"
	PlatformGenerationStatusSubmitting             = "submitting"
	PlatformGenerationStatusReconciliationRequired = "reconciliation_required"
	PlatformGenerationStatusProcessing             = "processing"
	PlatformGenerationStatusTransferring           = "transferring"
	PlatformGenerationStatusSucceeded              = "succeeded"
	PlatformGenerationStatusFailed                 = "failed"
	PlatformGenerationStatusCancelled              = "cancelled"
)

// Platform generation error codes form a frozen, versioned public registry.
// Keep callers on these constants so an internal/provider error can never
// escape through the provider-neutral API or callback contract.
const (
	PlatformGenerationErrorInternal                           = "INTERNAL_ERROR"
	PlatformGenerationErrorRequestValidation                  = "REQUEST_VALIDATION_FAILED"
	PlatformGenerationErrorIdempotencyKeyReused               = "IDEMPOTENCY_KEY_REUSED"
	PlatformGenerationErrorCallbackNotConfigured              = "CALLBACK_NOT_CONFIGURED"
	PlatformGenerationErrorCallbackURLNotAllowed              = "CALLBACK_URL_NOT_ALLOWED"
	PlatformGenerationErrorJobNotFound                        = "JOB_NOT_FOUND"
	PlatformGenerationErrorArtifactNotFound                   = "ARTIFACT_NOT_FOUND"
	PlatformGenerationErrorModelNotFound                      = "MODEL_NOT_FOUND"
	PlatformGenerationErrorOperationsUnauthorized             = "OPERATIONS_UNAUTHORIZED"
	PlatformGenerationErrorReconciliationConflict             = "RECONCILIATION_CONFLICT"
	PlatformGenerationErrorCallbackDeliveryNotFound           = "CALLBACK_DELIVERY_NOT_FOUND"
	PlatformGenerationErrorCallbackRedriveNotFound            = "CALLBACK_REDRIVE_NOT_FOUND"
	PlatformGenerationErrorCallbackRedriveNotAllowed          = "CALLBACK_REDRIVE_NOT_ALLOWED"
	PlatformGenerationErrorCallbackRedriveConflict            = "CALLBACK_REDRIVE_CONFLICT"
	PlatformGenerationErrorControlTenantForbidden             = "CONTROL_TENANT_FORBIDDEN"
	PlatformGenerationErrorChannelNotFound                    = "CHANNEL_NOT_FOUND"
	PlatformGenerationErrorChannelControlOperationNotFound    = "CHANNEL_CONTROL_OPERATION_NOT_FOUND"
	PlatformGenerationErrorChannelControlOperationConflict    = "CHANNEL_CONTROL_OPERATION_CONFLICT"
	PlatformGenerationErrorChannelRevisionConflict            = "CHANNEL_REVISION_CONFLICT"
	PlatformGenerationErrorModelCapabilityUnavailable         = "MODEL_CAPABILITY_UNAVAILABLE"
	PlatformGenerationErrorCapabilityRevisionMismatch         = "CAPABILITY_REVISION_MISMATCH"
	PlatformGenerationErrorRequestNotSupportedByModel         = "REQUEST_NOT_SUPPORTED_BY_MODEL"
	PlatformGenerationErrorModeNotSupportedByModel            = "MODE_NOT_SUPPORTED_BY_MODEL"
	PlatformGenerationErrorNoProviderAvailable                = "NO_PROVIDER_AVAILABLE"
	PlatformGenerationErrorProviderAccountPoolBusy            = "PROVIDER_ACCOUNT_POOL_BUSY"
	PlatformGenerationErrorProviderAccountPoolRateLimited     = "PROVIDER_ACCOUNT_POOL_RATE_LIMITED"
	PlatformGenerationErrorProviderTaskNotAssigned            = "PROVIDER_TASK_NOT_ASSIGNED"
	PlatformGenerationErrorProviderNotFound                   = "PROVIDER_NOT_FOUND"
	PlatformGenerationErrorProviderCircuitOpen                = "PROVIDER_CIRCUIT_OPEN"
	PlatformGenerationErrorProviderPollFailed                 = "PROVIDER_POLL_FAILED"
	PlatformGenerationErrorProviderTaskMismatch               = "PROVIDER_TASK_MISMATCH"
	PlatformGenerationErrorProviderTaskIDInvalid              = "PROVIDER_TASK_ID_INVALID"
	PlatformGenerationErrorUpstreamFailed                     = "UPSTREAM_FAILED"
	PlatformGenerationErrorContentPolicyRejected              = "CONTENT_POLICY_REJECTED"
	PlatformGenerationErrorInputAssetUnavailable              = "INPUT_ASSET_UNAVAILABLE"
	PlatformGenerationErrorGenerationFailed                   = "GENERATION_FAILED"
	PlatformGenerationErrorGenerationTaskNotFoundUpstream     = "GENERATION_TASK_NOT_FOUND_UPSTREAM"
	PlatformGenerationErrorGenerationChannelResponseInvalid   = "GENERATION_CHANNEL_RESPONSE_INVALID"
	PlatformGenerationErrorGenerationChannelUnavailable       = "GENERATION_CHANNEL_UNAVAILABLE"
	PlatformGenerationErrorArtifactTransferRetrying           = "ARTIFACT_TRANSFER_RETRYING"
	PlatformGenerationErrorArtifactTransferFailed             = "ARTIFACT_TRANSFER_FAILED"
	PlatformGenerationErrorSubmissionReconciliationRequired   = "SUBMISSION_RECONCILIATION_REQUIRED"
	PlatformGenerationErrorSubmissionConfirmedNotCreated      = "SUBMISSION_CONFIRMED_NOT_CREATED"
	PlatformGenerationErrorProviderRetriesExhausted           = "PROVIDER_RETRIES_EXHAUSTED"
	PlatformGenerationErrorWorkerAttemptsExhausted            = "WORKER_ATTEMPTS_EXHAUSTED"
	PlatformGenerationErrorProviderPollReconciliationRequired = "PROVIDER_POLL_RECONCILIATION_REQUIRED"
)

func IsPlatformGenerationPublicErrorCode(code string) bool {
	switch code {
	case PlatformGenerationErrorInternal,
		PlatformGenerationErrorRequestValidation,
		PlatformGenerationErrorIdempotencyKeyReused,
		PlatformGenerationErrorCallbackNotConfigured,
		PlatformGenerationErrorCallbackURLNotAllowed,
		PlatformGenerationErrorJobNotFound,
		PlatformGenerationErrorArtifactNotFound,
		PlatformGenerationErrorModelNotFound,
		PlatformGenerationErrorOperationsUnauthorized,
		PlatformGenerationErrorReconciliationConflict,
		PlatformGenerationErrorCallbackDeliveryNotFound,
		PlatformGenerationErrorCallbackRedriveNotFound,
		PlatformGenerationErrorCallbackRedriveNotAllowed,
		PlatformGenerationErrorCallbackRedriveConflict,
		PlatformGenerationErrorControlTenantForbidden,
		PlatformGenerationErrorChannelNotFound,
		PlatformGenerationErrorChannelControlOperationNotFound,
		PlatformGenerationErrorChannelControlOperationConflict,
		PlatformGenerationErrorChannelRevisionConflict,
		PlatformGenerationErrorModelCapabilityUnavailable,
		PlatformGenerationErrorCapabilityRevisionMismatch,
		PlatformGenerationErrorRequestNotSupportedByModel,
		PlatformGenerationErrorModeNotSupportedByModel,
		PlatformGenerationErrorNoProviderAvailable,
		PlatformGenerationErrorProviderAccountPoolBusy,
		PlatformGenerationErrorProviderAccountPoolRateLimited,
		PlatformGenerationErrorProviderTaskNotAssigned,
		PlatformGenerationErrorProviderNotFound,
		PlatformGenerationErrorProviderCircuitOpen,
		PlatformGenerationErrorProviderPollFailed,
		PlatformGenerationErrorProviderTaskMismatch,
		PlatformGenerationErrorProviderTaskIDInvalid,
		PlatformGenerationErrorUpstreamFailed,
		PlatformGenerationErrorContentPolicyRejected,
		PlatformGenerationErrorInputAssetUnavailable,
		PlatformGenerationErrorGenerationFailed,
		PlatformGenerationErrorGenerationTaskNotFoundUpstream,
		PlatformGenerationErrorGenerationChannelResponseInvalid,
		PlatformGenerationErrorGenerationChannelUnavailable,
		PlatformGenerationErrorArtifactTransferRetrying,
		PlatformGenerationErrorArtifactTransferFailed,
		PlatformGenerationErrorSubmissionReconciliationRequired,
		PlatformGenerationErrorSubmissionConfirmedNotCreated,
		PlatformGenerationErrorProviderRetriesExhausted,
		PlatformGenerationErrorWorkerAttemptsExhausted,
		PlatformGenerationErrorProviderPollReconciliationRequired:
		return true
	default:
		return false
	}
}

const (
	PlatformGenerationOutboxPending   = "pending"
	PlatformGenerationOutboxClaimed   = "claimed"
	PlatformGenerationOutboxCompleted = "completed"
	PlatformGenerationOutboxDead      = "dead"
)

type PlatformGenerationJob struct {
	ID                         string  `json:"id" gorm:"type:varchar(36);primaryKey"`
	TenantID                   string  `json:"tenant_id" gorm:"type:varchar(36);not null;index:idx_platform_generation_tenant_status,priority:1;uniqueIndex:uniq_platform_generation_idempotency,priority:1"`
	SourceClientID             string  `json:"source_client_id" gorm:"type:varchar(128);not null"`
	RequestID                  string  `json:"request_id" gorm:"type:varchar(80);not null"`
	IdempotencyKey             string  `json:"idempotency_key" gorm:"type:varchar(128);not null;uniqueIndex:uniq_platform_generation_idempotency,priority:2"`
	RequestHash                string  `json:"request_hash" gorm:"type:varchar(64);not null"`
	RequestJSON                string  `json:"request_json" gorm:"type:text;not null"`
	ClientReferenceID          *string `json:"client_reference_id" gorm:"type:varchar(128)"`
	Model                      string  `json:"model" gorm:"type:varchar(128);not null"`
	Mode                       string  `json:"mode" gorm:"type:varchar(32);not null"`
	ExpectedCapabilityRevision string  `json:"expected_capability_revision" gorm:"type:varchar(71);not null"`
	CapabilityRevision         string  `json:"capability_revision" gorm:"type:varchar(71);not null"`
	Status                     string  `json:"status" gorm:"type:varchar(32);not null;index:idx_platform_generation_tenant_status,priority:2;index:idx_platform_generation_status_available,priority:1;index:idx_platform_generation_transfer_available,priority:1"`
	Progress                   int     `json:"progress" gorm:"not null"`
	NativeTaskID               string  `json:"-" gorm:"type:varchar(191);index"`
	// NativeTaskRecoveryJSON is an internal, route-fenced polling identity
	// staged before the provider POST. It deliberately excludes quota and
	// billing state. It stores only an immutable encrypted-vault reference;
	// provider key material is forbidden in this document.
	NativeTaskRecoveryJSON            string    `json:"-" gorm:"type:text"`
	NativeBillingReconciliationNeeded bool      `json:"-" gorm:"not null;index"`
	ProviderRouteID                   int64     `json:"-" gorm:"index"`
	ProviderChannelID                 int       `json:"-" gorm:"index"`
	ProviderKeyIndex                  int       `json:"-"`
	ProviderSubmissionAttempt         int       `json:"-" gorm:"not null"`
	UpstreamTaskID                    string    `json:"upstream_task_id" gorm:"type:varchar(191);index"`
	UpstreamResultURL                 string    `json:"upstream_result_url" gorm:"type:text"`
	TemporaryResultJSON               string    `json:"-" gorm:"type:text"`
	CallbackURL                       string    `json:"callback_url" gorm:"type:text"`
	OutputsJSON                       string    `json:"outputs_json" gorm:"type:text"`
	ErrorCode                         string    `json:"error_code" gorm:"type:varchar(160)"`
	ErrorMessage                      string    `json:"error_message" gorm:"type:text"`
	ErrorRetryable                    bool      `json:"error_retryable"`
	ErrorDetailsJSON                  string    `json:"error_details_json" gorm:"type:text"`
	SubmissionLeaseToken              string    `json:"-" gorm:"type:varchar(36);index"`
	SubmissionLeaseExpiresAt          time.Time `json:"-" gorm:"index"`
	PollLeaseToken                    string    `json:"-" gorm:"type:varchar(36);index"`
	PollLeaseExpiresAt                time.Time `json:"-" gorm:"index"`
	NextPollAt                        time.Time `json:"-" gorm:"index:idx_platform_generation_status_available,priority:2"`
	TransferLeaseToken                string    `json:"-" gorm:"type:varchar(36);index"`
	TransferLeaseExpiresAt            time.Time `json:"-" gorm:"index"`
	NextTransferAt                    time.Time `json:"-" gorm:"index:idx_platform_generation_transfer_available,priority:2"`
	ArtifactTransferAttempts          int       `json:"-" gorm:"not null"`
	CallbackBackfillPending           bool      `json:"-" gorm:"not null;index"`
	CreatedAt                         time.Time `json:"created_at"`
	UpdatedAt                         time.Time `json:"updated_at"`
}

type PlatformGenerationOutbox struct {
	ID             int64     `json:"id" gorm:"primaryKey"`
	JobID          string    `json:"job_id" gorm:"type:varchar(36);not null;index;uniqueIndex:uniq_platform_generation_outbox_topic,priority:1"`
	Topic          string    `json:"topic" gorm:"type:varchar(64);not null;uniqueIndex:uniq_platform_generation_outbox_topic,priority:2;index:idx_platform_generation_outbox_available,priority:1"`
	State          string    `json:"state" gorm:"type:varchar(16);not null;index:idx_platform_generation_outbox_available,priority:2"`
	Attempts       int       `json:"attempts" gorm:"not null"`
	AvailableAt    time.Time `json:"available_at" gorm:"index:idx_platform_generation_outbox_available,priority:3"`
	ClaimToken     string    `json:"-" gorm:"type:varchar(36);index"`
	ClaimExpiresAt time.Time `json:"-" gorm:"index"`
	LastError      string    `json:"last_error" gorm:"type:varchar(512)"`
	CreatedAt      time.Time `json:"created_at"`
	UpdatedAt      time.Time `json:"updated_at"`
}

type PlatformGenerationClaim struct {
	Job      PlatformGenerationJob
	OutboxID int64
	Token    string
}

type PlatformGenerationReconciliationCandidate struct {
	Job                 PlatformGenerationJob
	Admission           PlatformGenerationRouteAdmission
	Route               PlatformGenerationProviderRoute
	ReconciliationToken string
}

type PlatformGenerationCallbackBuilder func(
	job PlatformGenerationJob,
) (*PlatformGenerationCallbackDelivery, bool, error)

const (
	platformGenerationNativeTaskRecoverySchemaVersion = 3
	platformGenerationNativeTaskBillingOwner          = "platform"
	platformGenerationNativeTaskBillingPolicyRevision = "platform-external-v1"
)

// platformGenerationNativeTaskRecovery contains only the evidence required
// to resume provider polling on the exact account used for submission. It is
// not a billing record and must never be used to manufacture customer or
// provider cost data.
type platformGenerationNativeTaskRecovery struct {
	SchemaVersion              int        `json:"schema_version"`
	BillingOwner               string     `json:"billing_owner"`
	BillingPolicyRevision      string     `json:"billing_policy_revision"`
	RouteID                    int64      `json:"route_id"`
	Attempt                    int        `json:"attempt"`
	TaskID                     string     `json:"task_id"`
	ChannelID                  int        `json:"channel_id"`
	Platform                   string     `json:"platform"`
	UserID                     int        `json:"user_id"`
	Group                      string     `json:"group"`
	Action                     string     `json:"action"`
	SubmitTime                 int64      `json:"submit_time"`
	Properties                 Properties `json:"properties"`
	PinnedKeyIndex             int        `json:"pinned_key_index"`
	PinnedKeyFingerprint       string     `json:"pinned_key_fingerprint"`
	ProviderCredentialTenantID string     `json:"provider_credential_tenant_id"`
	ProviderCredentialVersion  string     `json:"provider_credential_version"`
}

// PlatformGenerationNativeTaskCredentialReference is the secret-free,
// immutable credential identity carried by v3 native-task recovery evidence.
// Runtime attestation uses it to batch-load the exact vault row without
// exposing credential bytes or duplicating the recovery JSON contract outside
// the model package.
type PlatformGenerationNativeTaskCredentialReference struct {
	TenantID       string
	Version        string
	ChannelID      int
	KeyIndex       int
	KeyFingerprint string
}

// PlatformGenerationNativeTaskRecoveryCredentialReference parses only valid
// v3 Platform-owned recovery evidence. Older recovery schemas deliberately do
// not receive a compatibility interpretation because they may represent the
// retired native-billing lifecycle.
func PlatformGenerationNativeTaskRecoveryCredentialReference(job PlatformGenerationJob) (PlatformGenerationNativeTaskCredentialReference, error) {
	recovery, err := decodePlatformGenerationNativeTaskRecovery(job.NativeTaskRecoveryJSON)
	if err != nil {
		return PlatformGenerationNativeTaskCredentialReference{}, err
	}
	if recovery.SchemaVersion != platformGenerationNativeTaskRecoverySchemaVersion ||
		recovery.BillingOwner != platformGenerationNativeTaskBillingOwner ||
		recovery.BillingPolicyRevision != platformGenerationNativeTaskBillingPolicyRevision {
		return PlatformGenerationNativeTaskCredentialReference{}, errors.New("generation native task recovery billing policy is unsupported")
	}
	return PlatformGenerationNativeTaskCredentialReference{
		TenantID:       recovery.ProviderCredentialTenantID,
		Version:        recovery.ProviderCredentialVersion,
		ChannelID:      recovery.ChannelID,
		KeyIndex:       recovery.PinnedKeyIndex,
		KeyFingerprint: recovery.PinnedKeyFingerprint,
	}, nil
}

// ValidatePlatformGenerationNativeTaskBindingEvidence proves the full durable
// identity used by the generic native poller. All inputs must come from one
// database snapshot. A nil task is permitted only for an explicit
// reconciliation_required job whose provider outcome is unknown; it is never
// returned to the generic poller.
func ValidatePlatformGenerationNativeTaskBindingEvidence(
	job PlatformGenerationJob,
	task *Task,
	route PlatformGenerationProviderRoute,
	admission PlatformGenerationRouteAdmission,
	credential ProviderCredentialVersion,
	now time.Time,
	allowProcessingTaskAbsenceTransition bool,
) error {
	if job.Status == PlatformGenerationStatusSucceeded ||
		job.Status == PlatformGenerationStatusFailed ||
		job.Status == PlatformGenerationStatusCancelled {
		return errors.New("generation native task is bound to a terminal Platform job")
	}
	if job.ProviderRouteID <= 0 || job.ProviderRouteID != route.ID ||
		job.ProviderChannelID != route.ChannelID ||
		job.ProviderKeyIndex != route.KeyIndex ||
		job.ProviderSubmissionAttempt <= 0 ||
		job.NativeBillingReconciliationNeeded ||
		admission.JobID != job.ID || admission.RouteID != route.ID ||
		admission.Attempt != job.ProviderSubmissionAttempt {
		return errors.New("generation native task route attempt is inconsistent")
	}
	recovery, err := decodePlatformGenerationNativeTaskRecovery(job.NativeTaskRecoveryJSON)
	if err != nil {
		return err
	}
	if err := validatePlatformGenerationNativeTaskRecovery(
		job.ID,
		job.TenantID,
		recovery,
		route,
		admission.Attempt,
	); err != nil {
		return err
	}
	if job.NativeTaskID != recovery.TaskID {
		return errors.New("generation Platform job native task identity is inconsistent")
	}
	if credential.CredentialVersion == "" || credential.CredentialVersion != recovery.ProviderCredentialVersion ||
		credential.TenantID != job.TenantID || credential.TenantID != recovery.ProviderCredentialTenantID ||
		credential.ChannelID != route.ChannelID || credential.ChannelID != recovery.ChannelID ||
		credential.KeyIndex != route.KeyIndex || credential.KeyIndex != recovery.PinnedKeyIndex ||
		credential.KeyFingerprint != route.KeyFingerprint || credential.KeyFingerprint != recovery.PinnedKeyFingerprint {
		return errors.New("generation native task credential version is inconsistent")
	}
	if task == nil {
		reconciliationPending := job.Status == PlatformGenerationStatusReconciliationRequired &&
			admission.State == PlatformGenerationRouteAdmissionUnknown && admission.SlotHeld
		leaseToken, leaseTokenErr := uuid.Parse(job.SubmissionLeaseToken)
		stagedSubmission := job.Status == PlatformGenerationStatusSubmitting &&
			admission.State == PlatformGenerationRouteAdmissionPosting && admission.SlotHeld &&
			leaseTokenErr == nil && leaseToken.String() == job.SubmissionLeaseToken &&
			job.SubmissionLeaseExpiresAt.After(now)
		processingTransition := allowProcessingTaskAbsenceTransition &&
			job.Status == PlatformGenerationStatusProcessing && admission.SlotHeld &&
			(admission.State == PlatformGenerationRouteAdmissionPosting ||
				admission.State == PlatformGenerationRouteAdmissionUnknown)
		if !reconciliationPending && !stagedSubmission && !processingTransition {
			return errors.New("generation native task is missing outside explicit reconciliation")
		}
		return nil
	}
	if err := validatePlatformGenerationNativeTaskIdentity(*task, recovery); err != nil {
		return err
	}
	if task.Progress == "" {
		return errors.New("generation native task progress is missing")
	}
	switch task.Status {
	case TaskStatusNotStart, TaskStatusSubmitted, TaskStatusQueued, TaskStatusInProgress, TaskStatusUnknown:
		if task.Progress == "100%" {
			return errors.New("generation native task non-terminal status has terminal progress")
		}
	case TaskStatusSuccess, TaskStatusFailure:
		if task.Progress != "100%" {
			return errors.New("generation native task terminal status lacks terminal progress")
		}
	default:
		return errors.New("generation native task status is invalid")
	}
	if task.PrivateData.ProviderCredentialTenantID != job.TenantID ||
		task.PrivateData.ProviderCredentialVersion != credential.CredentialVersion {
		return errors.New("generation native task credential tenant is inconsistent")
	}
	if job.UpstreamTaskID != "" && task.PrivateData.UpstreamTaskID != job.UpstreamTaskID {
		return errors.New("generation native task sticky upstream identity is inconsistent")
	}
	switch job.Status {
	case PlatformGenerationStatusSubmitting:
		if !admission.SlotHeld || admission.State != PlatformGenerationRouteAdmissionPosting {
			return errors.New("generation native task staged admission is inconsistent")
		}
		leaseToken, err := uuid.Parse(job.SubmissionLeaseToken)
		if err != nil || leaseToken.String() != job.SubmissionLeaseToken ||
			!job.SubmissionLeaseExpiresAt.After(now) {
			return errors.New("generation native task submission lease is invalid")
		}
	case PlatformGenerationStatusProcessing:
		if !admission.SlotHeld || (admission.State != PlatformGenerationRouteAdmissionPosting &&
			admission.State != PlatformGenerationRouteAdmissionUnknown) {
			return errors.New("generation native task processing admission is inconsistent")
		}
		if job.UpstreamTaskID == "" || task.PrivateData.UpstreamTaskID != job.UpstreamTaskID {
			return errors.New("generation native task upstream identity is inconsistent")
		}
	case PlatformGenerationStatusReconciliationRequired:
		if !admission.SlotHeld || admission.State != PlatformGenerationRouteAdmissionUnknown {
			return errors.New("generation native task reconciliation admission is inconsistent")
		}
	case PlatformGenerationStatusTransferring:
		if admission.SlotHeld || admission.State != PlatformGenerationRouteAdmissionFinished ||
			admission.ClosedAt == nil || task.Status != TaskStatusSuccess || task.Progress != "100%" {
			return errors.New("generation native task transfer admission is inconsistent")
		}
		if job.UpstreamTaskID == "" || task.PrivateData.UpstreamTaskID != job.UpstreamTaskID {
			return errors.New("generation native task transfer upstream identity is inconsistent")
		}
	default:
		return errors.New("generation native task Platform lifecycle is inconsistent")
	}
	return nil
}

// StagePlatformGenerationNativeTaskRecovery persists the exact native polling
// identity after the one-shot route token has been consumed but before any
// provider bytes are sent. It also materializes the route/attempt assignment
// on the provider-neutral job early, so lease recovery never has to guess which
// channel may have accepted the request.
func StagePlatformGenerationNativeTaskRecovery(jobID string, expectedWorkerLeaseToken string, task *Task) error {
	if task == nil {
		return errors.New("generation native task recovery template is required")
	}
	if parsed, err := uuid.Parse(jobID); err != nil || parsed.String() != jobID {
		return errors.New("generation job id must be a canonical UUID")
	}
	if parsed, err := uuid.Parse(expectedWorkerLeaseToken); err != nil || parsed.String() != expectedWorkerLeaseToken {
		return errors.New("generation submission worker lease token must be a canonical UUID")
	}
	if task.PrivateData.PinnedKeyIndex == nil ||
		(task.PrivateData.ProviderCredentialVersion == "" && task.PrivateData.TransientProviderKey == "") {
		return errors.New("generation native task recovery lacks its pinned credential")
	}
	if task.Quota != 0 || task.PrivateData.BillingSource != TaskBillingSourcePlatformExternal ||
		task.PrivateData.SubscriptionId != 0 || task.PrivateData.TokenId != 0 ||
		task.PrivateData.BillingContext != nil {
		return errors.New("generation native task recovery requires Platform-owned zero native billing")
	}

	return DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		var job PlatformGenerationJob
		if err := lockForUpdate(tx.Where(
			"id = ? AND status = ? AND submission_lease_token = ? AND submission_lease_expires_at > ?",
			jobID,
			PlatformGenerationStatusSubmitting,
			expectedWorkerLeaseToken,
			now,
		)).First(&job).Error; err != nil {
			return errors.New("generation submission worker lease is stale")
		}
		var admission PlatformGenerationRouteAdmission
		if err := lockForUpdate(tx.Where("job_id = ?", jobID)).First(&admission).Error; err != nil {
			return err
		}
		if admission.State != PlatformGenerationRouteAdmissionPosting || !admission.SlotHeld || admission.Attempt <= 0 {
			return errors.New("generation native task recovery is not bound to a posting route")
		}
		var route PlatformGenerationProviderRoute
		if err := lockForUpdate(tx.Where("id = ?", admission.RouteID)).First(&route).Error; err != nil {
			return err
		}
		if task.ChannelId != route.ChannelID || *task.PrivateData.PinnedKeyIndex != route.KeyIndex ||
			task.PrivateData.PinnedKeyFingerprint != route.KeyFingerprint {
			return errors.New("generation native task recovery does not match the fenced route credential")
		}
		if err := bindTaskProviderCredentialVersionTx(tx, task, job.TenantID); err != nil {
			return err
		}
		recovery := platformGenerationNativeTaskRecovery{
			SchemaVersion:              platformGenerationNativeTaskRecoverySchemaVersion,
			BillingOwner:               platformGenerationNativeTaskBillingOwner,
			BillingPolicyRevision:      platformGenerationNativeTaskBillingPolicyRevision,
			RouteID:                    route.ID,
			Attempt:                    admission.Attempt,
			TaskID:                     task.TaskID,
			ChannelID:                  task.ChannelId,
			Platform:                   string(task.Platform),
			UserID:                     task.UserId,
			Group:                      task.Group,
			Action:                     task.Action,
			SubmitTime:                 task.SubmitTime,
			Properties:                 task.Properties,
			PinnedKeyIndex:             *task.PrivateData.PinnedKeyIndex,
			PinnedKeyFingerprint:       task.PrivateData.PinnedKeyFingerprint,
			ProviderCredentialTenantID: task.PrivateData.ProviderCredentialTenantID,
			ProviderCredentialVersion:  task.PrivateData.ProviderCredentialVersion,
		}
		if err := validatePlatformGenerationNativeTaskRecovery(jobID, job.TenantID, recovery, route, admission.Attempt); err != nil {
			return err
		}
		serialized, err := json.Marshal(recovery)
		if err != nil {
			return err
		}
		if job.NativeTaskRecoveryJSON != "" && job.NativeTaskRecoveryJSON != string(serialized) {
			return errors.New("generation native task recovery identity is immutable")
		}
		updates := map[string]any{
			"native_task_id":              recovery.TaskID,
			"native_task_recovery_json":   string(serialized),
			"provider_route_id":           route.ID,
			"provider_channel_id":         route.ChannelID,
			"provider_key_index":          route.KeyIndex,
			"provider_submission_attempt": admission.Attempt,
			"updated_at":                  now,
		}
		result := tx.Model(&PlatformGenerationJob{}).Where(
			"id = ? AND status = ? AND submission_lease_token = ? AND submission_lease_expires_at > ?",
			job.ID,
			PlatformGenerationStatusSubmitting,
			expectedWorkerLeaseToken,
			now,
		).Updates(updates)
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return errors.New("generation native task recovery lost its submission lease fence")
		}
		return nil
	})
}

func decodePlatformGenerationNativeTaskRecovery(raw string) (platformGenerationNativeTaskRecovery, error) {
	var recovery platformGenerationNativeTaskRecovery
	if strings.TrimSpace(raw) == "" {
		return recovery, errors.New("generation native task recovery evidence is missing")
	}
	if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(raw), &recovery); err != nil {
		return recovery, fmt.Errorf("generation native task recovery evidence is invalid: %w", err)
	}
	return recovery, nil
}

func validatePlatformGenerationNativeTaskRecovery(
	jobID string,
	tenantID string,
	recovery platformGenerationNativeTaskRecovery,
	route PlatformGenerationProviderRoute,
	attempt int,
) error {
	expectedTaskID, err := PlatformGenerationNativeTaskID(jobID)
	if err != nil {
		return err
	}
	if recovery.SchemaVersion != platformGenerationNativeTaskRecoverySchemaVersion ||
		recovery.BillingOwner != platformGenerationNativeTaskBillingOwner ||
		recovery.BillingPolicyRevision != platformGenerationNativeTaskBillingPolicyRevision ||
		recovery.RouteID != route.ID || recovery.Attempt != attempt ||
		recovery.TaskID != expectedTaskID || recovery.ChannelID != route.ChannelID ||
		recovery.PinnedKeyIndex != route.KeyIndex || recovery.PinnedKeyFingerprint != route.KeyFingerprint ||
		recovery.ProviderCredentialTenantID != tenantID || recovery.ProviderCredentialVersion == "" {
		return errors.New("generation native task recovery does not match the fenced route attempt")
	}
	if recovery.Platform == "" || len(recovery.Platform) > 30 ||
		len(recovery.Group) > 50 || len(recovery.Action) > 40 || recovery.SubmitTime <= 0 || recovery.UserID < 0 {
		return errors.New("generation native task recovery metadata is invalid")
	}
	return nil
}

func platformGenerationRecoveredNativeTask(
	recovery platformGenerationNativeTaskRecovery,
	upstreamTaskID string,
	now time.Time,
) Task {
	keyIndex := recovery.PinnedKeyIndex
	return Task{
		CreatedAt:  now.Unix(),
		UpdatedAt:  now.Unix(),
		TaskID:     recovery.TaskID,
		Platform:   constant.TaskPlatform(recovery.Platform),
		UserId:     recovery.UserID,
		Group:      recovery.Group,
		ChannelId:  recovery.ChannelID,
		Quota:      0,
		Action:     recovery.Action,
		Status:     TaskStatusSubmitted,
		SubmitTime: recovery.SubmitTime,
		Progress:   "0%",
		Properties: recovery.Properties,
		PrivateData: TaskPrivateData{
			PinnedKeyIndex:             &keyIndex,
			PinnedKeyFingerprint:       recovery.PinnedKeyFingerprint,
			ProviderCredentialTenantID: recovery.ProviderCredentialTenantID,
			ProviderCredentialVersion:  recovery.ProviderCredentialVersion,
			UpstreamTaskID:             upstreamTaskID,
			BillingSource:              TaskBillingSourcePlatformExternal,
		},
	}
}

func validatePlatformGenerationNativeTaskIdentity(task Task, recovery platformGenerationNativeTaskRecovery) error {
	if task.TaskID != recovery.TaskID || task.ChannelId != recovery.ChannelID ||
		string(task.Platform) != recovery.Platform || task.UserId != recovery.UserID ||
		task.Group != recovery.Group || task.Action != recovery.Action ||
		task.SubmitTime != recovery.SubmitTime || task.Properties != recovery.Properties ||
		task.PrivateData.PinnedKeyIndex == nil || *task.PrivateData.PinnedKeyIndex != recovery.PinnedKeyIndex ||
		task.PrivateData.PinnedKeyFingerprint != recovery.PinnedKeyFingerprint ||
		task.PrivateData.ProviderCredentialTenantID != recovery.ProviderCredentialTenantID ||
		task.PrivateData.ProviderCredentialVersion != recovery.ProviderCredentialVersion ||
		task.PrivateData.BillingSource != TaskBillingSourcePlatformExternal || task.Quota != 0 ||
		task.PrivateData.SubscriptionId != 0 || task.PrivateData.TokenId != 0 ||
		task.PrivateData.BillingContext != nil {
		return errors.New("native generation task does not match its sticky polling identity")
	}
	return nil
}

func validatePlatformGenerationNativeTaskAgainstRoute(
	task Task,
	jobID string,
	route PlatformGenerationProviderRoute,
) error {
	expectedTaskID, err := PlatformGenerationNativeTaskID(jobID)
	if err != nil {
		return err
	}
	if task.TaskID != expectedTaskID || task.ChannelId != route.ChannelID ||
		task.PrivateData.PinnedKeyIndex == nil || *task.PrivateData.PinnedKeyIndex != route.KeyIndex ||
		task.PrivateData.PinnedKeyFingerprint != route.KeyFingerprint ||
		task.PrivateData.ProviderCredentialTenantID == "" || task.PrivateData.ProviderCredentialVersion == "" ||
		task.PrivateData.BillingSource != TaskBillingSourcePlatformExternal || task.Quota != 0 ||
		task.PrivateData.SubscriptionId != 0 || task.PrivateData.TokenId != 0 ||
		task.PrivateData.BillingContext != nil {
		return errors.New("native generation task does not match the fenced provider route")
	}
	return nil
}

func CreatePlatformGenerationJob(job *PlatformGenerationJob) (*PlatformGenerationJob, bool, bool, error) {
	var result PlatformGenerationJob
	replayed := false
	conflict := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		createResult := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(job)
		if createResult.Error != nil {
			return createResult.Error
		}
		if createResult.RowsAffected == 1 {
			now, err := GetDBTimeTx(tx)
			if err != nil {
				return err
			}
			outbox := PlatformGenerationOutbox{
				JobID:       job.ID,
				Topic:       "generation.submit",
				State:       PlatformGenerationOutboxPending,
				AvailableAt: now,
			}
			if err := tx.Create(&outbox).Error; err != nil {
				return err
			}
			if err := RecordPlatformTaskStageTransitionTx(tx, job.ID, job.Status, now); err != nil {
				return err
			}
			result = *job
			return nil
		}

		lookupErr := tx.Where("tenant_id = ? AND idempotency_key = ?", job.TenantID, job.IdempotencyKey).First(&result).Error
		if lookupErr != nil {
			return lookupErr
		}
		if result.RequestHash != job.RequestHash {
			conflict = true
			return nil
		}
		replayed = true
		return nil
	})
	return &result, replayed, conflict, err
}

func GetPlatformGenerationJob(jobID string, tenantID string) (*PlatformGenerationJob, error) {
	var job PlatformGenerationJob
	err := DB.Where("id = ? AND tenant_id = ?", jobID, tenantID).First(&job).Error
	return &job, err
}

func ClaimPlatformGenerationSubmission(lease time.Duration, outboxIDs ...int64) (*PlatformGenerationClaim, error) {
	var claim *PlatformGenerationClaim
	noClaim := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		var outbox PlatformGenerationOutbox
		query := tx.Where(
			"topic = ? AND available_at <= ? AND (state = ? OR (state = ? AND claim_expires_at <= ?))",
			"generation.submit",
			now,
			PlatformGenerationOutboxPending,
			PlatformGenerationOutboxClaimed,
			now,
		).Order("id ASC")
		if len(outboxIDs) > 0 {
			if len(outboxIDs) != 1 || outboxIDs[0] <= 0 {
				return errors.New("generation submission outbox id must be positive")
			}
			query = query.Where("id = ?", outboxIDs[0])
		}
		query = lockForUpdate(query)
		if err := query.First(&outbox).Error; err != nil {
			return err
		}

		var job PlatformGenerationJob
		jobQuery := lockForUpdate(tx.Where("id = ?", outbox.JobID))
		if err := jobQuery.First(&job).Error; err != nil {
			return err
		}
		if job.Status != PlatformGenerationStatusQueued && job.Status != PlatformGenerationStatusSubmitting {
			if err := tx.Model(&outbox).Updates(map[string]any{
				"state":            PlatformGenerationOutboxCompleted,
				"claim_token":      "",
				"claim_expires_at": time.Time{},
			}).Error; err != nil {
				return err
			}
			// Commit the stale-outbox repair, then report an empty queue to the
			// caller. Returning (nil, nil) here would make the worker dereference
			// a nil claim and crash the Relay process.
			noClaim = true
			return nil
		}

		token := uuid.NewString()
		expiresAt := now.Add(lease)
		if err := tx.Model(&outbox).Updates(map[string]any{
			"state":            PlatformGenerationOutboxClaimed,
			"claim_token":      token,
			"claim_expires_at": expiresAt,
			"attempts":         gorm.Expr("attempts + 1"),
		}).Error; err != nil {
			return err
		}
		if err := tx.Model(&job).Updates(map[string]any{
			"status":                      PlatformGenerationStatusSubmitting,
			"submission_lease_token":      token,
			"submission_lease_expires_at": expiresAt,
		}).Error; err != nil {
			return err
		}
		if err := RecordPlatformTaskStageTransitionTx(tx, job.ID, PlatformGenerationStatusSubmitting, now); err != nil {
			return err
		}
		job.Status = PlatformGenerationStatusSubmitting
		job.SubmissionLeaseToken = token
		job.SubmissionLeaseExpiresAt = expiresAt
		claim = &PlatformGenerationClaim{Job: job, OutboxID: outbox.ID, Token: token}
		return nil
	})
	if err == nil && noClaim {
		return nil, gorm.ErrRecordNotFound
	}
	return claim, err
}

// RenewPlatformGenerationSubmission extends both halves of a live submission
// claim with one database-clock expiry. The outbox row is locked before the
// job row, matching ClaimPlatformGenerationSubmission's lock order. A stale
// token or an already expired lease can never be revived.
func RenewPlatformGenerationSubmission(claim PlatformGenerationClaim, lease time.Duration) (bool, error) {
	if parsed, err := uuid.Parse(claim.Job.ID); err != nil || parsed.String() != claim.Job.ID {
		return false, errors.New("generation submission job id is invalid")
	}
	if claim.OutboxID <= 0 {
		return false, errors.New("generation submission outbox id is invalid")
	}
	if parsed, err := uuid.Parse(claim.Token); err != nil || parsed.String() != claim.Token {
		return false, errors.New("generation submission lease token is invalid")
	}
	if lease < time.Second || lease > 24*time.Hour {
		return false, errors.New("generation submission renewal lease is invalid")
	}

	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		var outbox PlatformGenerationOutbox
		if err := lockForUpdate(tx.Where(
			"id = ? AND job_id = ? AND topic = ?",
			claim.OutboxID,
			claim.Job.ID,
			"generation.submit",
		)).First(&outbox).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				return nil
			}
			return err
		}
		if outbox.State != PlatformGenerationOutboxClaimed || outbox.ClaimToken != claim.Token ||
			!outbox.ClaimExpiresAt.After(now) {
			return nil
		}

		var job PlatformGenerationJob
		if err := lockForUpdate(tx.Where("id = ?", claim.Job.ID)).First(&job).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				return nil
			}
			return err
		}
		if job.Status != PlatformGenerationStatusSubmitting || job.SubmissionLeaseToken != claim.Token ||
			!job.SubmissionLeaseExpiresAt.After(now) {
			return nil
		}

		expiresAt := now.Add(lease)
		outboxUpdate := tx.Model(&PlatformGenerationOutbox{}).Where(
			"id = ? AND state = ? AND claim_token = ? AND claim_expires_at > ?",
			claim.OutboxID,
			PlatformGenerationOutboxClaimed,
			claim.Token,
			now,
		).UpdateColumn("claim_expires_at", expiresAt)
		if outboxUpdate.Error != nil {
			return outboxUpdate.Error
		}
		if outboxUpdate.RowsAffected != 1 {
			return errors.New("generation submission outbox renewal lost its lease fence")
		}
		jobUpdate := tx.Model(&PlatformGenerationJob{}).Where(
			"id = ? AND status = ? AND submission_lease_token = ? AND submission_lease_expires_at > ?",
			claim.Job.ID,
			PlatformGenerationStatusSubmitting,
			claim.Token,
			now,
		).UpdateColumn("submission_lease_expires_at", expiresAt)
		if jobUpdate.Error != nil {
			return jobUpdate.Error
		}
		if jobUpdate.RowsAffected != 1 {
			return errors.New("generation submission job renewal lost its lease fence")
		}
		won = true
		return nil
	})
	return won, err
}

func CompletePlatformGenerationSubmission(
	claim PlatformGenerationClaim,
	updates map[string]any,
	callbackBuilders ...PlatformGenerationCallbackBuilder,
) (bool, error) {
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		updates["submission_lease_token"] = ""
		updates["submission_lease_expires_at"] = nil
		updates["callback_backfill_pending"] = true
		updates["updated_at"] = now
		result := tx.Model(&PlatformGenerationJob{}).Where(
			"id = ? AND status = ? AND submission_lease_token = ? AND submission_lease_expires_at > ?",
			claim.Job.ID,
			PlatformGenerationStatusSubmitting,
			claim.Token,
			now,
		).Updates(updates)
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return nil
		}
		if status, ok := telemetryStatusFromUpdates(updates); ok {
			if err := RecordPlatformTaskStageTransitionTx(tx, claim.Job.ID, status, now); err != nil {
				return err
			}
		}
		if err := buildPlatformGenerationCallbackTx(tx, claim.Job.ID, callbackBuilders); err != nil {
			return err
		}
		outboxResult := tx.Model(&PlatformGenerationOutbox{}).Where(
			"id = ? AND state = ? AND claim_token = ? AND claim_expires_at > ?",
			claim.OutboxID,
			PlatformGenerationOutboxClaimed,
			claim.Token,
			now,
		).Updates(map[string]any{
			"state":            PlatformGenerationOutboxCompleted,
			"claim_token":      "",
			"claim_expires_at": nil,
		})
		if outboxResult.Error != nil {
			return outboxResult.Error
		}
		if outboxResult.RowsAffected != 1 {
			return errors.New("generation submission outbox lost its live lease fence")
		}
		won = true
		return nil
	})
	return won, err
}

// ReleasePlatformGenerationSubmission returns a locally blocked submission to
// the durable outbox. The submission lease token fences both rows, so an
// expired worker cannot requeue work after a newer worker has taken ownership.
func ReleasePlatformGenerationSubmission(claim PlatformGenerationClaim, delay time.Duration, lastError string) (bool, error) {
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		jobUpdate := tx.Model(&PlatformGenerationJob{}).Where(
			"id = ? AND status = ? AND submission_lease_token = ? AND submission_lease_expires_at > ?",
			claim.Job.ID,
			PlatformGenerationStatusSubmitting,
			claim.Token,
			now,
		).Updates(map[string]any{
			"status":                      PlatformGenerationStatusQueued,
			"submission_lease_token":      "",
			"submission_lease_expires_at": nil,
			"updated_at":                  now,
		})
		if jobUpdate.Error != nil {
			return jobUpdate.Error
		}
		if jobUpdate.RowsAffected != 1 {
			return nil
		}
		if err := RecordPlatformTaskStageTransitionTx(tx, claim.Job.ID, PlatformGenerationStatusQueued, now); err != nil {
			return err
		}
		outboxUpdate := tx.Model(&PlatformGenerationOutbox{}).Where(
			"id = ? AND state = ? AND claim_token = ? AND claim_expires_at > ?",
			claim.OutboxID,
			PlatformGenerationOutboxClaimed,
			claim.Token,
			now,
		).Updates(map[string]any{
			"state":            PlatformGenerationOutboxPending,
			"available_at":     now.Add(delay),
			"claim_token":      "",
			"claim_expires_at": nil,
			"last_error":       lastError,
			"updated_at":       now,
		})
		if outboxUpdate.Error != nil {
			return outboxUpdate.Error
		}
		won = outboxUpdate.RowsAffected == 1
		return nil
	})
	return won, err
}

func ClaimPlatformGenerationPoll(lease time.Duration) (*PlatformGenerationJob, string, error) {
	var claimed PlatformGenerationJob
	token := ""
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		query := tx.Where(
			"status = ? AND next_poll_at <= ? AND (poll_lease_token = ? OR poll_lease_expires_at <= ?)",
			PlatformGenerationStatusProcessing,
			now,
			"",
			now,
		).Order("next_poll_at ASC, created_at ASC")
		query = lockForUpdate(query)
		if err := query.First(&claimed).Error; err != nil {
			return err
		}
		token = uuid.NewString()
		return tx.Model(&claimed).Updates(map[string]any{
			"poll_lease_token":      token,
			"poll_lease_expires_at": now.Add(lease),
		}).Error
	})
	return &claimed, token, err
}

func ClaimPlatformGenerationReconciliation(lease time.Duration) (*PlatformGenerationJob, string, error) {
	return claimPlatformGenerationByStatus(PlatformGenerationStatusReconciliationRequired, lease)
}

func claimPlatformGenerationByStatus(status string, lease time.Duration) (*PlatformGenerationJob, string, error) {
	var claimed PlatformGenerationJob
	token := ""
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		query := tx.Where(
			"status = ? AND next_poll_at <= ? AND (poll_lease_token = ? OR poll_lease_expires_at <= ?)",
			status,
			now,
			"",
			now,
		).Order("next_poll_at ASC, created_at ASC")
		query = lockForUpdate(query)
		if err := query.First(&claimed).Error; err != nil {
			return err
		}
		token = uuid.NewString()
		return tx.Model(&claimed).Updates(map[string]any{
			"poll_lease_token":      token,
			"poll_lease_expires_at": now.Add(lease),
			"updated_at":            now,
		}).Error
	})
	return &claimed, token, err
}

func CompletePlatformGenerationReconciliation(
	jobID string,
	token string,
	updates map[string]any,
	callbackBuilders ...PlatformGenerationCallbackBuilder,
) (bool, error) {
	return completePlatformGenerationLease(jobID, token, PlatformGenerationStatusReconciliationRequired, updates, callbackBuilders)
}

func CompletePlatformGenerationPoll(
	jobID string,
	token string,
	updates map[string]any,
	callbackBuilders ...PlatformGenerationCallbackBuilder,
) (bool, error) {
	return completePlatformGenerationLease(jobID, token, PlatformGenerationStatusProcessing, updates, callbackBuilders)
}

// CompletePlatformGenerationMissingNativeTask moves a processing job and its
// still-held route admission into the explicit unknown/reconciliation state in
// one transaction. Updating only the job would leave admission=posting and
// make the exact runtime binding impossible to resume safely.
func CompletePlatformGenerationMissingNativeTask(
	jobID string,
	token string,
	callbackBuilders ...PlatformGenerationCallbackBuilder,
) (bool, error) {
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		var job PlatformGenerationJob
		if err := lockForUpdate(tx.Where(
			"id = ? AND status = ? AND poll_lease_token = ? AND poll_lease_expires_at > ?",
			jobID,
			PlatformGenerationStatusProcessing,
			token,
			now,
		)).First(&job).Error; err != nil {
			return err
		}
		var admission PlatformGenerationRouteAdmission
		if err := lockForUpdate(tx.Where("job_id = ?", jobID)).First(&admission).Error; err != nil {
			return err
		}
		if !admission.SlotHeld ||
			(admission.State != PlatformGenerationRouteAdmissionPosting &&
				admission.State != PlatformGenerationRouteAdmissionUnknown) {
			return errors.New("generation missing-task transition lost its route admission fence")
		}
		admissionResult := tx.Model(&admission).Where("slot_held = ?", true).Updates(map[string]any{
			"state":      PlatformGenerationRouteAdmissionUnknown,
			"unknown_at": now,
			"updated_at": now,
		})
		if admissionResult.Error != nil {
			return admissionResult.Error
		}
		if admissionResult.RowsAffected != 1 {
			return errors.New("generation missing-task transition lost its route admission write fence")
		}
		jobResult := tx.Model(&job).Where(
			"status = ? AND poll_lease_token = ? AND poll_lease_expires_at > ?",
			PlatformGenerationStatusProcessing,
			token,
			now,
		).Updates(map[string]any{
			"status":                    PlatformGenerationStatusReconciliationRequired,
			"error_code":                PlatformGenerationErrorProviderPollReconciliationRequired,
			"error_message":             "The native task row is temporarily unavailable",
			"error_retryable":           true,
			"poll_lease_token":          "",
			"poll_lease_expires_at":     nil,
			"callback_backfill_pending": true,
			"updated_at":                now,
		})
		if jobResult.Error != nil {
			return jobResult.Error
		}
		if jobResult.RowsAffected != 1 {
			return errors.New("generation missing-task transition lost its poll lease fence")
		}
		if err := RecordPlatformTaskStageTransitionTx(tx, jobID, PlatformGenerationStatusReconciliationRequired, now); err != nil {
			return err
		}
		if err := buildPlatformGenerationCallbackTx(tx, jobID, callbackBuilders); err != nil {
			return err
		}
		won = true
		return nil
	})
	return won, err
}

func completePlatformGenerationLease(
	jobID string,
	token string,
	status string,
	updates map[string]any,
	callbackBuilders []PlatformGenerationCallbackBuilder,
) (bool, error) {
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		updates["poll_lease_token"] = ""
		updates["poll_lease_expires_at"] = nil
		updates["callback_backfill_pending"] = true
		updates["updated_at"] = now
		result := tx.Model(&PlatformGenerationJob{}).Where(
			"id = ? AND status = ? AND poll_lease_token = ? AND poll_lease_expires_at > ?",
			jobID,
			status,
			token,
			now,
		).Updates(updates)
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return nil
		}
		if nextStatus, ok := telemetryStatusFromUpdates(updates); ok {
			if err := RecordPlatformTaskStageTransitionTx(tx, jobID, nextStatus, now); err != nil {
				return err
			}
		}
		if err := buildPlatformGenerationCallbackTx(tx, jobID, callbackBuilders); err != nil {
			return err
		}
		won = true
		return nil
	})
	return won, err
}

func ReleasePlatformGenerationPoll(jobID string, token string, delay time.Duration) error {
	return releasePlatformGenerationLease(jobID, token, PlatformGenerationStatusProcessing, delay)
}

func ReleasePlatformGenerationReconciliation(jobID string, token string, delay time.Duration) error {
	return releasePlatformGenerationLease(jobID, token, PlatformGenerationStatusReconciliationRequired, delay)
}

func releasePlatformGenerationLease(jobID string, token string, status string, delay time.Duration) error {
	return DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		return tx.Model(&PlatformGenerationJob{}).Where(
			"id = ? AND status = ? AND poll_lease_token = ? AND poll_lease_expires_at > ?",
			jobID,
			status,
			token,
			now,
		).Updates(map[string]any{
			"poll_lease_token":      "",
			"poll_lease_expires_at": nil,
			"next_poll_at":          now.Add(delay),
			"updated_at":            now,
		}).Error
	})
}

// ClaimPlatformGenerationTransfer owns exactly one provider-result transfer.
// It deliberately has a lease separate from provider polling: storage writes
// can outlive a poll lease and must be fenced independently before publishing
// durable outputs.
func ClaimPlatformGenerationTransfer(lease time.Duration) (*PlatformGenerationJob, string, error) {
	if lease < time.Second {
		return nil, "", fmt.Errorf("generation transfer lease must be at least one second")
	}
	var claimed PlatformGenerationJob
	token := ""
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		query := tx.Where(
			"status = ? AND next_transfer_at <= ? AND (transfer_lease_token = ? OR transfer_lease_expires_at <= ?)",
			PlatformGenerationStatusTransferring,
			now,
			"",
			now,
		).Order("next_transfer_at ASC, created_at ASC")
		query = lockForUpdate(query)
		if err := query.First(&claimed).Error; err != nil {
			return err
		}
		token = uuid.NewString()
		expiresAt := now.Add(lease)
		result := tx.Model(&claimed).Where("status = ?", PlatformGenerationStatusTransferring).Updates(map[string]any{
			"transfer_lease_token":       token,
			"transfer_lease_expires_at":  expiresAt,
			"artifact_transfer_attempts": gorm.Expr("artifact_transfer_attempts + 1"),
			"updated_at":                 now,
		})
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return gorm.ErrRecordNotFound
		}
		claimed.TransferLeaseToken = token
		claimed.TransferLeaseExpiresAt = expiresAt
		claimed.ArtifactTransferAttempts++
		return nil
	})
	return &claimed, token, err
}

// RenewPlatformGenerationTransfer extends only the current live owner. It
// uses the database clock and the same random token fencing as completion, so
// a paused worker cannot revive itself after expiry or after another worker
// has taken over.
func RenewPlatformGenerationTransfer(jobID string, token string, lease time.Duration) (bool, error) {
	if parsed, err := uuid.Parse(jobID); err != nil || parsed.String() != jobID {
		return false, fmt.Errorf("generation transfer job id is invalid")
	}
	if parsed, err := uuid.Parse(token); err != nil || parsed.String() != token {
		return false, fmt.Errorf("generation transfer token is invalid")
	}
	if lease < time.Second || lease > 24*time.Hour {
		return false, fmt.Errorf("generation transfer renewal lease is invalid")
	}
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		result := tx.Model(&PlatformGenerationJob{}).Where(
			"id = ? AND status = ? AND transfer_lease_token = ? AND transfer_lease_expires_at > ?",
			jobID,
			PlatformGenerationStatusTransferring,
			token,
			now,
		).Updates(map[string]any{
			"transfer_lease_expires_at": now.Add(lease),
			"updated_at":                now,
		})
		if result.Error != nil {
			return result.Error
		}
		won = result.RowsAffected == 1
		return nil
	})
	return won, err
}

func ReleasePlatformGenerationTransfer(
	jobID string,
	token string,
	delay time.Duration,
	code string,
	message string,
) (bool, error) {
	if delay < 0 || !IsPlatformGenerationPublicErrorCode(code) {
		return false, fmt.Errorf("generation transfer release is invalid")
	}
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		result := tx.Model(&PlatformGenerationJob{}).Where(
			"id = ? AND status = ? AND transfer_lease_token = ? AND transfer_lease_expires_at > ?",
			jobID,
			PlatformGenerationStatusTransferring,
			token,
			now,
		).Updates(map[string]any{
			"transfer_lease_token":      "",
			"transfer_lease_expires_at": nil,
			"next_transfer_at":          now.Add(delay),
			"error_code":                code,
			"error_message":             message,
			"error_retryable":           true,
			"updated_at":                now,
		})
		won = result.RowsAffected == 1
		return result.Error
	})
	return won, err
}

func CompletePlatformGenerationTransfer(
	jobID string,
	token string,
	objectKey string,
	outputsJSON string,
	callbackBuilders ...PlatformGenerationCallbackBuilder,
) (bool, error) {
	if strings.TrimSpace(outputsJSON) == "" || strings.TrimSpace(objectKey) == "" {
		return false, fmt.Errorf("generation outputs are required")
	}
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		var job PlatformGenerationJob
		if err := lockForUpdate(tx.Where("id = ?", jobID)).First(&job).Error; err != nil {
			return err
		}
		var intent PlatformArtifactUploadIntent
		if err := lockForUpdate(tx.Where(
			"job_id = ? AND transfer_token = ? AND object_key = ?",
			jobID,
			token,
			objectKey,
		)).First(&intent).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				return nil
			}
			return err
		}
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		if intent.State == PlatformArtifactUploadIntentPublished {
			if job.Status == PlatformGenerationStatusSucceeded && job.OutputsJSON == outputsJSON {
				won = true
			}
			return nil
		}
		if intent.State != PlatformArtifactUploadIntentPending || intent.ClaimToken != "" ||
			job.Status != PlatformGenerationStatusTransferring ||
			job.TransferLeaseToken != token ||
			!job.TransferLeaseExpiresAt.After(now) {
			return nil
		}
		result := tx.Model(&job).Where(
			"id = ? AND status = ? AND transfer_lease_token = ? AND transfer_lease_expires_at > ?",
			jobID,
			PlatformGenerationStatusTransferring,
			token,
			now,
		).Updates(map[string]any{
			"status":                    PlatformGenerationStatusSucceeded,
			"progress":                  100,
			"outputs_json":              outputsJSON,
			"temporary_result_json":     "",
			"upstream_result_url":       "",
			"transfer_lease_token":      "",
			"transfer_lease_expires_at": nil,
			"error_code":                "",
			"error_message":             "",
			"error_retryable":           false,
			"error_details_json":        "{}",
			"callback_backfill_pending": true,
			"updated_at":                now,
		})
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return nil
		}
		intentResult := tx.Model(&intent).Where(
			"state = ? AND claim_token = ?",
			PlatformArtifactUploadIntentPending,
			"",
		).Updates(map[string]any{
			"state":            PlatformArtifactUploadIntentPublished,
			"claim_token":      "",
			"claim_expires_at": nil,
			"last_error_code":  "",
			"published_at":     now,
			"updated_at":       now,
		})
		if intentResult.Error != nil {
			return intentResult.Error
		}
		if intentResult.RowsAffected != 1 {
			return ErrPlatformArtifactUploadIntentFenced
		}
		if err := RecordPlatformTaskStageTransitionTx(tx, jobID, PlatformGenerationStatusSucceeded, now); err != nil {
			return err
		}
		if err := buildPlatformGenerationCallbackTx(tx, jobID, callbackBuilders); err != nil {
			return err
		}
		won = true
		return nil
	})
	return won, err
}

func FailPlatformGenerationTransfer(
	jobID string,
	token string,
	message string,
	callbackBuilders ...PlatformGenerationCallbackBuilder,
) (bool, error) {
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		result := tx.Model(&PlatformGenerationJob{}).Where(
			"id = ? AND status = ? AND transfer_lease_token = ? AND transfer_lease_expires_at > ?",
			jobID,
			PlatformGenerationStatusTransferring,
			token,
			now,
		).Updates(map[string]any{
			"status":                    PlatformGenerationStatusFailed,
			"progress":                  100,
			"outputs_json":              "[]",
			"temporary_result_json":     "",
			"upstream_result_url":       "",
			"transfer_lease_token":      "",
			"transfer_lease_expires_at": nil,
			"error_code":                PlatformGenerationErrorArtifactTransferFailed,
			"error_message":             message,
			"error_retryable":           false,
			"error_details_json":        "{}",
			"callback_backfill_pending": true,
			"updated_at":                now,
		})
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return nil
		}
		if err := RecordPlatformTaskStageTransitionTx(tx, jobID, PlatformGenerationStatusFailed, now); err != nil {
			return err
		}
		if err := buildPlatformGenerationCallbackTx(tx, jobID, callbackBuilders); err != nil {
			return err
		}
		won = true
		return nil
	})
	return won, err
}

func ListPlatformGenerationCallbackBackfillJobs(limit int) ([]PlatformGenerationJob, error) {
	if limit < 1 || limit > 1000 {
		return nil, fmt.Errorf("generation callback backfill limit is invalid")
	}
	var jobs []PlatformGenerationJob
	err := DB.Where("callback_backfill_pending = ?", true).
		Order("updated_at ASC, id ASC").Limit(limit).Find(&jobs).Error
	return jobs, err
}

func MarkPlatformGenerationCallbackBackfilled(jobID string, status string, progress int) (bool, error) {
	result := DB.Model(&PlatformGenerationJob{}).Where(
		"id = ? AND status = ? AND progress = ? AND callback_backfill_pending = ?",
		jobID,
		status,
		progress,
		true,
	).Update("callback_backfill_pending", false)
	return result.RowsAffected == 1, result.Error
}

func buildPlatformGenerationCallbackTx(
	tx *gorm.DB,
	jobID string,
	builders []PlatformGenerationCallbackBuilder,
) error {
	if len(builders) == 0 || builders[0] == nil {
		return nil
	}
	var job PlatformGenerationJob
	if err := tx.Where("id = ?", jobID).First(&job).Error; err != nil {
		return err
	}
	delivery, eligible, err := builders[0](job)
	if err != nil {
		return err
	}
	if eligible {
		if _, err := CreatePlatformGenerationCallbackDeliveryTx(tx, delivery); err != nil {
			return err
		}
	}
	return tx.Model(&PlatformGenerationJob{}).Where(
		"id = ? AND status = ? AND progress = ?",
		job.ID,
		job.Status,
		job.Progress,
	).Update("callback_backfill_pending", false).Error
}

var ErrPlatformGenerationReconciliationConflict = errors.New("generation reconciliation outcome conflicts with durable state")

func platformGenerationReconciliationToken(
	job PlatformGenerationJob,
	admission PlatformGenerationRouteAdmission,
) string {
	payload := fmt.Sprintf(
		"platform-generation-reconciliation-v1\x00%s\x00%s\x00%d\x00%d\x00%s",
		job.TenantID,
		job.ID,
		admission.RouteID,
		admission.Attempt,
		admission.SubmissionTokenHash,
	)
	digest := sha256.Sum256([]byte(payload))
	return fmt.Sprintf("sha256:%x", digest)
}

func GetPlatformGenerationSubmissionUnknown(
	jobID string,
	tenantID string,
) (*PlatformGenerationReconciliationCandidate, error) {
	var candidate PlatformGenerationReconciliationCandidate
	err := DB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Where(
			"id = ? AND tenant_id = ? AND status = ? AND error_code = ?",
			jobID,
			tenantID,
			PlatformGenerationStatusReconciliationRequired,
			PlatformGenerationErrorSubmissionReconciliationRequired,
		).First(&candidate.Job).Error; err != nil {
			return err
		}
		if err := tx.Where("job_id = ?", jobID).First(&candidate.Admission).Error; err != nil {
			return err
		}
		if candidate.Admission.State != PlatformGenerationRouteAdmissionUnknown {
			return gorm.ErrRecordNotFound
		}
		if !candidate.Admission.SlotHeld ||
			candidate.Admission.RouteID != candidate.Job.ProviderRouteID ||
			candidate.Admission.Attempt != candidate.Job.ProviderSubmissionAttempt ||
			candidate.Admission.SubmissionTokenHash == "" {
			return ErrPlatformGenerationReconciliationConflict
		}
		if err := tx.Where("id = ?", candidate.Admission.RouteID).First(&candidate.Route).Error; err != nil {
			return err
		}
		if candidate.Route.ChannelID != candidate.Job.ProviderChannelID ||
			candidate.Route.KeyIndex != candidate.Job.ProviderKeyIndex {
			return ErrPlatformGenerationReconciliationConflict
		}
		candidate.ReconciliationToken = platformGenerationReconciliationToken(
			candidate.Job,
			candidate.Admission,
		)
		return nil
	})
	return &candidate, err
}

func platformGenerationSubmissionUnknownScope(
	db *gorm.DB,
	tenantID string,
) *gorm.DB {
	return db.Table("platform_generation_jobs AS jobs").
		Joins("JOIN platform_generation_route_admissions AS admissions ON admissions.job_id = jobs.id").
		Where(
			"jobs.tenant_id = ? AND jobs.status = ? AND jobs.error_code = ? AND admissions.state = ?",
			tenantID,
			PlatformGenerationStatusReconciliationRequired,
			PlatformGenerationErrorSubmissionReconciliationRequired,
			PlatformGenerationRouteAdmissionUnknown,
		)
}

func ListPlatformGenerationSubmissionUnknown(
	tenantID string,
	page int,
	pageSize int,
) ([]PlatformGenerationReconciliationCandidate, int64, error) {
	var total int64
	if err := platformGenerationSubmissionUnknownScope(DB, tenantID).
		Count(&total).Error; err != nil {
		return nil, 0, err
	}
	var jobs []PlatformGenerationJob
	if err := platformGenerationSubmissionUnknownScope(DB, tenantID).
		Select("jobs.*").
		Order("jobs.updated_at ASC, jobs.id ASC").
		Offset((page - 1) * pageSize).
		Limit(pageSize).
		Find(&jobs).Error; err != nil {
		return nil, 0, err
	}
	candidates := make([]PlatformGenerationReconciliationCandidate, 0, len(jobs))
	for _, job := range jobs {
		candidate, err := GetPlatformGenerationSubmissionUnknown(job.ID, tenantID)
		if errors.Is(err, gorm.ErrRecordNotFound) {
			continue
		}
		if err != nil {
			return nil, 0, err
		}
		candidates = append(candidates, *candidate)
	}
	return candidates, total, nil
}

func ResolvePlatformGenerationSubmissionUnknown(
	jobID string,
	tenantID string,
	resolution PlatformGenerationReconciliationResolution,
	callbackBuilders ...PlatformGenerationCallbackBuilder,
) (*PlatformGenerationJob, *PlatformGenerationReconciliationEvent, bool, error) {
	var resolved PlatformGenerationJob
	var receipt *PlatformGenerationReconciliationEvent
	idempotentReplay := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		var job PlatformGenerationJob
		if err := lockForUpdate(tx.Where("id = ? AND tenant_id = ?", jobID, tenantID)).First(&job).Error; err != nil {
			return err
		}
		expectedEvent, err := newPlatformGenerationReconciliationEvent(jobID, tenantID, resolution, now)
		if err != nil {
			return err
		}
		existingEvent, err := findPlatformGenerationReconciliationEventTx(tx, tenantID, resolution.OperationID)
		if err == nil {
			if !platformGenerationReconciliationEventsEqual(*existingEvent, *expectedEvent) {
				return ErrPlatformGenerationReconciliationConflict
			}
			resolved = job
			receipt = existingEvent
			idempotentReplay = true
			return nil
		}
		if !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		if job.Status != PlatformGenerationStatusReconciliationRequired {
			return ErrPlatformGenerationReconciliationConflict
		}
		var admission PlatformGenerationRouteAdmission
		if err := lockForUpdate(tx.Where("job_id = ?", jobID)).First(&admission).Error; err != nil {
			return err
		}
		if admission.State != PlatformGenerationRouteAdmissionUnknown || !admission.SlotHeld ||
			admission.RouteID != job.ProviderRouteID ||
			job.ProviderRouteID != resolution.ExpectedRouteID ||
			job.ProviderSubmissionAttempt != resolution.ExpectedSubmissionAttempt ||
			admission.Attempt != resolution.ExpectedSubmissionAttempt ||
			subtle.ConstantTimeCompare(
				[]byte(platformGenerationReconciliationToken(job, admission)),
				[]byte(resolution.ExpectedReconciliationToken),
			) != 1 {
			return ErrPlatformGenerationReconciliationConflict
		}
		var route PlatformGenerationProviderRoute
		if err := lockForUpdate(tx.Where("id = ?", admission.RouteID)).First(&route).Error; err != nil {
			return err
		}
		state, err := loadPlatformGenerationAccountStateForRouteTx(tx, route, make(map[int64]*PlatformGenerationProviderAccountState))
		if err != nil {
			return err
		}
		if route.ChannelID != job.ProviderChannelID || state.ActiveCount <= 0 {
			return ErrPlatformGenerationReconciliationConflict
		}

		if resolution.Created {
			if strings.TrimSpace(resolution.UpstreamTaskID) == "" ||
				strings.TrimSpace(resolution.UpstreamTaskID) != resolution.UpstreamTaskID ||
				len(resolution.UpstreamTaskID) > 191 {
				return fmt.Errorf("upstream task id is invalid")
			}
			expectedNativeTaskID, err := PlatformGenerationNativeTaskID(job.ID)
			if err != nil || job.NativeTaskID != expectedNativeTaskID {
				return ErrPlatformGenerationReconciliationConflict
			}
			var nativeTasks []Task
			// Search globally by the deterministic native ID. A row on any other
			// channel is a collision, never evidence that permits route switching.
			if err := lockForUpdate(tx.Where("task_id = ?", job.NativeTaskID).
				Order("id ASC").Limit(2)).Find(&nativeTasks).Error; err != nil {
				return err
			}
			if len(nativeTasks) > 1 {
				return ErrPlatformGenerationReconciliationConflict
			}
			recovery, err := decodePlatformGenerationNativeTaskRecovery(job.NativeTaskRecoveryJSON)
			if err != nil {
				return ErrPlatformGenerationReconciliationConflict
			}
			if err := validatePlatformGenerationNativeTaskRecovery(job.ID, job.TenantID, recovery, route, admission.Attempt); err != nil {
				return ErrPlatformGenerationReconciliationConflict
			}
			var credential ProviderCredentialVersion
			if err := tx.Where("credential_version = ?", recovery.ProviderCredentialVersion).First(&credential).Error; err != nil {
				return ErrPlatformGenerationReconciliationConflict
			}
			var existingTask *Task
			if len(nativeTasks) == 1 {
				existingTask = &nativeTasks[0]
			}
			if err := ValidatePlatformGenerationNativeTaskBindingEvidence(
				job,
				existingTask,
				route,
				admission,
				credential,
				now,
				false,
			); err != nil {
				return ErrPlatformGenerationReconciliationConflict
			}
			var nativeTask Task
			nativeBillingReconciliationNeeded := false
			if len(nativeTasks) == 0 {
				nativeTask = platformGenerationRecoveredNativeTask(recovery, resolution.UpstreamTaskID, now)
				if err := tx.Create(&nativeTask).Error; err != nil {
					return err
				}
				// Platform owns customer billing, so reconstructing the provider
				// polling identity cannot create a native billing gap.
			} else {
				nativeTask = nativeTasks[0]
				if nativeTask.PrivateData.UpstreamTaskID != "" &&
					nativeTask.PrivateData.UpstreamTaskID != resolution.UpstreamTaskID {
					return ErrPlatformGenerationReconciliationConflict
				}
			}
			nativeTask.PrivateData.UpstreamTaskID = resolution.UpstreamTaskID
			nativeUpdates := map[string]any{
				"private_data": nativeTask.PrivateData,
				"quota":        0,
				"updated_at":   now.Unix(),
			}
			// A reconciliation may race with a successful native poll. Preserve
			// terminal evidence instead of regressing it back to SUBMITTED.
			if nativeTask.Status != TaskStatusSuccess && nativeTask.Status != TaskStatusFailure {
				nativeUpdates["status"] = TaskStatusSubmitted
				nativeUpdates["progress"] = "0%"
				nativeUpdates["fail_reason"] = ""
			}
			if err := tx.Model(&Task{}).Where("id = ?", nativeTask.ID).Updates(nativeUpdates).Error; err != nil {
				return err
			}
			if err := tx.Model(&admission).Updates(map[string]any{
				"state":      PlatformGenerationRouteAdmissionPosting,
				"updated_at": now,
			}).Error; err != nil {
				return err
			}
			if err := tx.Model(&job).Updates(map[string]any{
				"status":                               PlatformGenerationStatusProcessing,
				"progress":                             0,
				"upstream_task_id":                     resolution.UpstreamTaskID,
				"poll_lease_token":                     "",
				"poll_lease_expires_at":                nil,
				"next_poll_at":                         now,
				"error_code":                           "",
				"error_message":                        "",
				"error_retryable":                      false,
				"error_details_json":                   "{}",
				"native_billing_reconciliation_needed": nativeBillingReconciliationNeeded,
				"callback_backfill_pending":            true,
				"updated_at":                           now,
			}).Error; err != nil {
				return err
			}
		} else {
			if strings.TrimSpace(resolution.UpstreamTaskID) != "" {
				return fmt.Errorf("upstream task id must be empty for not_created")
			}
			stateResult := tx.Model(&PlatformGenerationProviderAccountState{}).
				Where("id = ? AND active_count > 0", state.ID).
				Updates(map[string]any{
					"active_count": gorm.Expr("active_count - 1"),
					"updated_at":   now,
				})
			if stateResult.Error != nil {
				return stateResult.Error
			}
			if stateResult.RowsAffected != 1 {
				return ErrPlatformGenerationReconciliationConflict
			}
			state.ActiveCount--
			state.UpdatedAt = now
			if err := syncPlatformGenerationAccountRouteMirrorsTx(tx, *state, now); err != nil {
				return err
			}
			if err := tx.Model(&admission).Updates(map[string]any{
				"state":                 PlatformGenerationRouteAdmissionReleased,
				"slot_held":             false,
				"submission_token_hash": "",
				"closed_at":             now,
				"updated_at":            now,
			}).Error; err != nil {
				return err
			}
			if err := tx.Model(&job).Updates(map[string]any{
				"status":                    PlatformGenerationStatusFailed,
				"progress":                  100,
				"outputs_json":              "[]",
				"temporary_result_json":     "",
				"upstream_result_url":       "",
				"poll_lease_token":          "",
				"poll_lease_expires_at":     nil,
				"error_code":                PlatformGenerationErrorSubmissionConfirmedNotCreated,
				"error_message":             "The provider confirmed that no task was created",
				"error_retryable":           false,
				"error_details_json":        "{}",
				"callback_backfill_pending": true,
				"updated_at":                now,
			}).Error; err != nil {
				return err
			}
		}
		if err := buildPlatformGenerationCallbackTx(tx, jobID, callbackBuilders); err != nil {
			return err
		}
		resolvedStatus := PlatformGenerationStatusFailed
		if resolution.Created {
			resolvedStatus = PlatformGenerationStatusProcessing
		}
		if err := RecordPlatformTaskStageTransitionTx(tx, jobID, resolvedStatus, now); err != nil {
			return err
		}
		insertedEvent, err := insertPlatformGenerationReconciliationEventTx(tx, *expectedEvent)
		if err != nil {
			return err
		}
		receipt = insertedEvent
		return tx.Where("id = ?", jobID).First(&resolved).Error
	})
	return &resolved, receipt, idempotentReplay, err
}

func IsPlatformGenerationNotFound(err error) bool {
	return errors.Is(err, gorm.ErrRecordNotFound)
}

func PlatformGenerationNativeTaskID(jobID string) (string, error) {
	parsed, err := uuid.Parse(jobID)
	if err != nil || parsed.String() != jobID {
		return "", errors.New("generation job id must be a canonical UUID")
	}
	return "task_pg_" + strings.ReplaceAll(jobID, "-", ""), nil
}
