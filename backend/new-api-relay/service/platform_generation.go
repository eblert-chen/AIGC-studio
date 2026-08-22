package service

import (
	"crypto/sha256"
	"fmt"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
)

type PlatformGenerationConflictError struct{}

func (PlatformGenerationConflictError) Error() string {
	return "idempotency key is already associated with a different request"
}

type PlatformGenerationCallbackPolicyError struct {
	Code string
}

func (e PlatformGenerationCallbackPolicyError) Error() string {
	return e.Code
}

func SubmitPlatformGeneration(
	principal PlatformRelayPrincipal,
	request dto.PlatformGenerationRequest,
	idempotencyKey string,
	requestID string,
) (dto.PlatformGenerationAccepted, error) {
	if len(idempotencyKey) < 8 || len(idempotencyKey) > 128 || strings.TrimSpace(idempotencyKey) != idempotencyKey {
		return dto.PlatformGenerationAccepted{}, fmt.Errorf("Idempotency-Key must contain 8 to 128 non-whitespace-delimited characters")
	}
	if request.Callback != nil {
		if principal.CallbackURL == "" {
			return dto.PlatformGenerationAccepted{}, PlatformGenerationCallbackPolicyError{Code: "CALLBACK_NOT_CONFIGURED"}
		}
		normalizedURL, normalizeErr := normalizePlatformCallbackURL(
			request.Callback.URL,
			PlatformRelayProductionSecurityEnabled(),
		)
		if normalizeErr != nil || normalizedURL != principal.CallbackURL {
			return dto.PlatformGenerationAccepted{}, PlatformGenerationCallbackPolicyError{Code: "CALLBACK_URL_NOT_ALLOWED"}
		}
	}

	requestForHash, err := common.Marshal(request)
	if err != nil {
		return dto.PlatformGenerationAccepted{}, err
	}
	digest := sha256.Sum256(requestForHash)
	metadata := make(map[string]any, len(request.Metadata)+1)
	for key, value := range request.Metadata {
		if key == "relay_request_id" || key == "relay_capability_revision" {
			continue
		}
		metadata[key] = value
	}
	if requestID != "" {
		metadata["relay_request_id"] = requestID
	}
	request.Metadata = metadata
	serialized, err := common.Marshal(request)
	if err != nil {
		return dto.PlatformGenerationAccepted{}, err
	}
	callbackURL := ""
	if request.Callback != nil {
		callbackURL = principal.CallbackURL
	}
	now := time.Now().UTC()
	job := &model.PlatformGenerationJob{
		ID:                         uuid.NewString(),
		TenantID:                   principal.TenantID,
		SourceClientID:             principal.ClientID,
		RequestID:                  requestID,
		IdempotencyKey:             idempotencyKey,
		RequestHash:                fmt.Sprintf("%x", digest),
		RequestJSON:                string(serialized),
		ClientReferenceID:          request.ClientReferenceID,
		Model:                      request.Model,
		Mode:                       request.Mode,
		ExpectedCapabilityRevision: request.ExpectedCapabilityRevision,
		CapabilityRevision:         request.ExpectedCapabilityRevision,
		Status:                     model.PlatformGenerationStatusQueued,
		Progress:                   0,
		CallbackURL:                callbackURL,
		OutputsJSON:                "[]",
		ErrorDetailsJSON:           "{}",
		NextPollAt:                 now,
		CreatedAt:                  now,
		UpdatedAt:                  now,
	}
	persisted, replayed, conflict, err := model.CreatePlatformGenerationJob(job)
	if err != nil {
		return dto.PlatformGenerationAccepted{}, err
	}
	if conflict {
		return dto.PlatformGenerationAccepted{}, PlatformGenerationConflictError{}
	}
	return platformGenerationAccepted(*persisted, replayed), nil
}

func GetPlatformGeneration(principal PlatformRelayPrincipal, jobID string) (dto.PlatformGenerationSnapshot, error) {
	if _, err := uuid.Parse(jobID); err != nil {
		return dto.PlatformGenerationSnapshot{}, gormNotFoundCompatibilityError{}
	}
	job, err := model.GetPlatformGenerationJob(jobID, principal.TenantID)
	if err != nil {
		return dto.PlatformGenerationSnapshot{}, err
	}
	return platformGenerationSnapshot(*job)
}

type gormNotFoundCompatibilityError struct{}

func (gormNotFoundCompatibilityError) Error() string { return "generation job not found" }

func IsPlatformGenerationNotFound(err error) bool {
	if _, ok := err.(gormNotFoundCompatibilityError); ok {
		return true
	}
	return model.IsPlatformGenerationNotFound(err)
}

func platformGenerationAccepted(job model.PlatformGenerationJob, replayed bool) dto.PlatformGenerationAccepted {
	return dto.PlatformGenerationAccepted{
		APIVersion:                 dto.PlatformRelayAPIVersion,
		SchemaVersion:              dto.PlatformRelaySchemaVersion,
		Object:                     "generation",
		ID:                         job.ID,
		JobID:                      job.ID,
		Status:                     job.Status,
		ExpectedCapabilityRevision: job.ExpectedCapabilityRevision,
		CapabilityRevision:         job.CapabilityRevision,
		ReservationAction:          platformGenerationReservationAction(job.Status),
		IdempotentReplay:           replayed,
		CreatedAt:                  job.CreatedAt.UTC(),
	}
}

func platformGenerationSnapshot(job model.PlatformGenerationJob) (dto.PlatformGenerationSnapshot, error) {
	request := dto.NewPlatformGenerationRequest()
	if err := common.Unmarshal([]byte(job.RequestJSON), &request); err != nil {
		return dto.PlatformGenerationSnapshot{}, fmt.Errorf("stored generation request is invalid: %w", err)
	}
	outputs := make([]dto.PlatformGenerationArtifact, 0)
	if strings.TrimSpace(job.OutputsJSON) != "" {
		if err := common.Unmarshal([]byte(job.OutputsJSON), &outputs); err != nil {
			return dto.PlatformGenerationSnapshot{}, fmt.Errorf("stored generation outputs are invalid: %w", err)
		}
	}
	var jobError *dto.PlatformGenerationErrorDetail
	if job.ErrorCode != "" {
		details := make(map[string]any)
		if strings.TrimSpace(job.ErrorDetailsJSON) != "" {
			if err := common.Unmarshal([]byte(job.ErrorDetailsJSON), &details); err != nil {
				return dto.PlatformGenerationSnapshot{}, fmt.Errorf("stored generation error details are invalid: %w", err)
			}
		}
		jobError = &dto.PlatformGenerationErrorDetail{
			Code:      job.ErrorCode,
			Message:   job.ErrorMessage,
			Retryable: job.ErrorRetryable,
			Details:   details,
		}
	}
	if job.Status != model.PlatformGenerationStatusSucceeded {
		outputs = make([]dto.PlatformGenerationArtifact, 0)
	}
	return dto.PlatformGenerationSnapshot{
		APIVersion:                 dto.PlatformRelayAPIVersion,
		SchemaVersion:              dto.PlatformRelaySchemaVersion,
		Object:                     "generation",
		ID:                         job.ID,
		ClientReferenceID:          request.ClientReferenceID,
		Model:                      request.Model,
		ExpectedCapabilityRevision: job.ExpectedCapabilityRevision,
		CapabilityRevision:         job.CapabilityRevision,
		Mode:                       request.Mode,
		Inputs:                     request.Inputs,
		Output:                     request.Output,
		Metadata:                   request.Metadata,
		Status:                     job.Status,
		ReservationAction:          platformGenerationReservationAction(job.Status),
		Progress:                   job.Progress,
		Outputs:                    outputs,
		Error:                      jobError,
		CreatedAt:                  job.CreatedAt.UTC(),
		UpdatedAt:                  job.UpdatedAt.UTC(),
	}, nil
}

func platformGenerationReservationAction(status string) string {
	switch status {
	case model.PlatformGenerationStatusSucceeded:
		return "settle"
	case model.PlatformGenerationStatusFailed, model.PlatformGenerationStatusCancelled:
		return "release"
	default:
		return "hold"
	}
}
