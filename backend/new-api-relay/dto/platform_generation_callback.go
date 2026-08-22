package dto

import (
	"fmt"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
)

const PlatformGenerationCallbackEventType = "generation.status_changed"

var platformGenerationArtifactDigestPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)

var platformGenerationCallbackErrorCodes = map[string]struct{}{
	"MODEL_CAPABILITY_UNAVAILABLE":          {},
	"CAPABILITY_REVISION_MISMATCH":          {},
	"REQUEST_NOT_SUPPORTED_BY_MODEL":        {},
	"MODE_NOT_SUPPORTED_BY_MODEL":           {},
	"NO_PROVIDER_AVAILABLE":                 {},
	"PROVIDER_ACCOUNT_POOL_BUSY":            {},
	"PROVIDER_ACCOUNT_POOL_RATE_LIMITED":    {},
	"PROVIDER_TASK_NOT_ASSIGNED":            {},
	"PROVIDER_NOT_FOUND":                    {},
	"PROVIDER_CIRCUIT_OPEN":                 {},
	"PROVIDER_POLL_FAILED":                  {},
	"PROVIDER_TASK_MISMATCH":                {},
	"PROVIDER_TASK_ID_INVALID":              {},
	"UPSTREAM_FAILED":                       {},
	"CONTENT_POLICY_REJECTED":               {},
	"INPUT_ASSET_UNAVAILABLE":               {},
	"GENERATION_FAILED":                     {},
	"GENERATION_TASK_NOT_FOUND_UPSTREAM":    {},
	"GENERATION_CHANNEL_RESPONSE_INVALID":   {},
	"GENERATION_CHANNEL_UNAVAILABLE":        {},
	"ARTIFACT_TRANSFER_RETRYING":            {},
	"ARTIFACT_TRANSFER_FAILED":              {},
	"SUBMISSION_RECONCILIATION_REQUIRED":    {},
	"SUBMISSION_CONFIRMED_NOT_CREATED":      {},
	"PROVIDER_RETRIES_EXHAUSTED":            {},
	"WORKER_ATTEMPTS_EXHAUSTED":             {},
	"PROVIDER_POLL_RECONCILIATION_REQUIRED": {},
}

// PlatformGenerationCallbackEvent is the exact body covered by the Relay
// callback signature. Keep fields concrete and without omitempty so the
// required keys remain present in every serialized event.
type PlatformGenerationCallbackEvent struct {
	APIVersion    string                             `json:"api_version"`
	SchemaVersion int                                `json:"schema_version"`
	EventID       string                             `json:"event_id"`
	Type          string                             `json:"type"`
	OccurredAt    time.Time                          `json:"occurred_at"`
	Job           PlatformGenerationCallbackEventJob `json:"job"`
}

type PlatformGenerationCallbackEventJob struct {
	APIVersion                 string                         `json:"api_version"`
	ID                         string                         `json:"id"`
	ClientReferenceID          *string                        `json:"client_reference_id"`
	Status                     string                         `json:"status"`
	Progress                   int                            `json:"progress"`
	Outputs                    []PlatformGenerationArtifact   `json:"outputs"`
	Error                      *PlatformGenerationErrorDetail `json:"error"`
	ExpectedCapabilityRevision string                         `json:"expected_capability_revision"`
	CapabilityRevision         string                         `json:"capability_revision"`
	ReservationAction          string                         `json:"reservation_action"`
}

func (event PlatformGenerationCallbackEvent) Validate() error {
	if event.APIVersion != PlatformRelayAPIVersion || event.SchemaVersion != PlatformRelaySchemaVersion {
		return fmt.Errorf("callback event version is invalid")
	}
	if _, err := uuid.Parse(event.EventID); err != nil {
		return fmt.Errorf("callback event_id is invalid")
	}
	if event.Type != PlatformGenerationCallbackEventType {
		return fmt.Errorf("callback event type is invalid")
	}
	if event.OccurredAt.IsZero() {
		return fmt.Errorf("callback occurred_at is required")
	}
	return event.Job.Validate()
}

func (job PlatformGenerationCallbackEventJob) Validate() error {
	if job.APIVersion != PlatformRelayAPIVersion {
		return fmt.Errorf("callback job api_version is invalid")
	}
	if _, err := uuid.Parse(job.ID); err != nil {
		return fmt.Errorf("callback job id is invalid")
	}
	if job.ClientReferenceID != nil && utf8.RuneCountInString(*job.ClientReferenceID) > 128 {
		return fmt.Errorf("callback client_reference_id exceeds 128 characters")
	}
	if !platformRelayRevisionPattern.MatchString(job.ExpectedCapabilityRevision) ||
		!platformRelayRevisionPattern.MatchString(job.CapabilityRevision) {
		return fmt.Errorf("callback capability revision is invalid")
	}
	if job.CapabilityRevision != job.ExpectedCapabilityRevision {
		return fmt.Errorf("callback capability revisions do not match")
	}
	if job.Progress < 0 || job.Progress > 100 {
		return fmt.Errorf("callback progress must be between 0 and 100")
	}
	if job.Outputs == nil {
		return fmt.Errorf("callback outputs must be an array")
	}
	if job.Error != nil {
		if _, ok := platformGenerationCallbackErrorCodes[job.Error.Code]; !ok {
			return fmt.Errorf("callback error code is invalid")
		}
		if job.Error.Details == nil {
			return fmt.Errorf("callback error details must be an object")
		}
	}

	expectedReservationAction := "hold"
	switch job.Status {
	case "succeeded":
		expectedReservationAction = "settle"
		if job.Progress != 100 || len(job.Outputs) < 1 || len(job.Outputs) > 16 || job.Error != nil {
			return fmt.Errorf("succeeded callback job invariant is invalid")
		}
	case "failed":
		expectedReservationAction = "release"
		if len(job.Outputs) != 0 || job.Error == nil {
			return fmt.Errorf("failed callback job invariant is invalid")
		}
	case "cancelled":
		expectedReservationAction = "release"
		if len(job.Outputs) != 0 {
			return fmt.Errorf("cancelled callback job cannot expose outputs")
		}
	case "processing", "reconciliation_required":
		if len(job.Outputs) != 0 {
			return fmt.Errorf("non-terminal callback job cannot expose outputs")
		}
	default:
		return fmt.Errorf("callback job status is invalid")
	}
	if job.ReservationAction != expectedReservationAction {
		return fmt.Errorf("callback reservation action does not match status")
	}
	for _, artifact := range job.Outputs {
		if err := validatePlatformGenerationCallbackArtifact(artifact); err != nil {
			return err
		}
	}
	return nil
}

func validatePlatformGenerationCallbackArtifact(artifact PlatformGenerationArtifact) error {
	if _, err := uuid.Parse(artifact.AssetID); err != nil {
		return fmt.Errorf("callback artifact asset_id is invalid")
	}
	if strings.TrimSpace(artifact.ObjectKey) == "" || utf8.RuneCountInString(artifact.ObjectKey) > 1024 {
		return fmt.Errorf("callback artifact object_key is invalid")
	}
	if artifact.MediaType != "image" && artifact.MediaType != "video" {
		return fmt.Errorf("callback artifact media_type is invalid")
	}
	if strings.TrimSpace(artifact.ContentType) == "" {
		return fmt.Errorf("callback artifact content_type is required")
	}
	if artifact.SizeBytes < 0 {
		return fmt.Errorf("callback artifact size_bytes cannot be negative")
	}
	if !platformGenerationArtifactDigestPattern.MatchString(artifact.SHA256) {
		return fmt.Errorf("callback artifact sha256 is invalid")
	}
	return nil
}
