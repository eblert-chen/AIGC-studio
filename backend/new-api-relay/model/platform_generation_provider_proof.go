package model

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"net/url"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	"gorm.io/gorm"
)

const (
	PlatformGenerationProviderResultReceiptSchemaVersion                  = 1
	PlatformGenerationProviderResultProofContractRevision                 = "volcengine-ark-terminal-result-proof-v2"
	PlatformGenerationMiniMaxH3ProviderResultProofContractRevision        = "minimax-h3-terminal-result-proof-v2"
	PlatformGenerationGoogleGeminiVeoProviderResultProofContractRevision  = "google-gemini-veo-terminal-result-proof-v1"
	PlatformGenerationGoogleGeminiOmniProviderResultProofContractRevision = "google-gemini-omni-terminal-result-proof-v2"
	platformGenerationArkProviderResultProofContractRevisionV1            = "volcengine-ark-terminal-result-proof-v1"
	platformGenerationMiniMaxH3ProviderResultProofContractRevisionV1      = "minimax-h3-terminal-result-proof-v1"
	platformGenerationGoogleOmniProviderResultProofContractRevisionV1     = "google-gemini-omni-terminal-result-proof-v1"
	PlatformGenerationProviderResultReceiptStateVerified                  = "verified"
	PlatformGenerationProviderResultReceiptStateReconciliationRequired    = "reconciliation_required"
	PlatformGenerationProviderReconciliationKindMissingNativeTask         = "missing_native_task"
	PlatformGenerationProviderReconciliationKindProviderResultProof       = "provider_result_proof"
	PlatformGenerationProviderReconciliationKindProviderMaterial          = "provider_material"
	PlatformGenerationProviderReconciliationKindUnknown                   = "unknown"
)

type platformGenerationProviderReconciliationDetails struct {
	ReconciliationKind string `json:"reconciliation_kind"`
}

func PlatformGenerationProviderReconciliationDetailsJSON(kind string) string {
	switch kind {
	case PlatformGenerationProviderReconciliationKindMissingNativeTask:
		return `{"reconciliation_kind":"missing_native_task"}`
	case PlatformGenerationProviderReconciliationKindProviderResultProof:
		return `{"reconciliation_kind":"provider_result_proof"}`
	case PlatformGenerationProviderReconciliationKindProviderMaterial:
		return `{"reconciliation_kind":"provider_material"}`
	default:
		return `{"reconciliation_kind":"unknown"}`
	}
}

func PlatformGenerationProviderReconciliationKind(job PlatformGenerationJob) string {
	raw := []byte(job.ErrorDetailsJSON)
	if len(raw) == 0 || common.RejectDuplicateJSONKeys(raw) != nil {
		return PlatformGenerationProviderReconciliationKindUnknown
	}
	var details platformGenerationProviderReconciliationDetails
	if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(job.ErrorDetailsJSON), &details); err != nil {
		return PlatformGenerationProviderReconciliationKindUnknown
	}
	switch details.ReconciliationKind {
	case PlatformGenerationProviderReconciliationKindMissingNativeTask,
		PlatformGenerationProviderReconciliationKindProviderResultProof,
		PlatformGenerationProviderReconciliationKindProviderMaterial:
		return details.ReconciliationKind
	default:
		return PlatformGenerationProviderReconciliationKindUnknown
	}
}

func PlatformGenerationProviderReconciliationEvidenceRetained(job PlatformGenerationJob) (bool, error) {
	if job.UpstreamResultURL != "" || job.TemporaryResultJSON != "" {
		return true, nil
	}
	if strings.TrimSpace(job.NativeTaskID) == "" {
		return false, nil
	}
	var tasks []Task
	if err := DB.Where("task_id = ?", job.NativeTaskID).Order("id ASC").Limit(2).Find(&tasks).Error; err != nil {
		return false, err
	}
	for _, task := range tasks {
		if !task.IsPlatformExternalBilling() {
			continue
		}
		if task.PrivateData.ResultURL != "" || len(task.Data) != 0 ||
			platformGenerationLegacyResultURL(task.FailReason) {
			return true, nil
		}
	}
	return false, nil
}

// PlatformGenerationProviderResultObservation is the provider-native semantic
// evidence produced by a code-reviewed adapter. Raw bodies and artifact URLs
// are used only to compute digests and are never serialized into Task.Data.
type PlatformGenerationProviderResultObservation struct {
	Protocol             string
	TaskID               string
	Model                string
	ProviderStatus       string
	Resolution           string
	DurationSeconds      int
	AspectRatio          string
	FramesPresent        bool
	OutputCount          int
	MediaType            string
	UsageComplete        bool
	InputTokens          int
	OutputTokens         int
	OutputVideoTokens    int
	OutputTextTokens     int
	ThoughtTokens        int
	CachedTokens         int
	TotalTokens          int
	ProviderTotalSeconds int
	InputSeconds         int
	OutputSeconds        int
	InputImageCount      int
	ArtifactSizeBytes    int64
	ArtifactSHA256       string
	Succeeded            bool
	FailureOwner         string
	FailureCode          string
	ResponseBody         []byte
	ArtifactURL          string
}

// PlatformGenerationProviderResultReceipt is the canonical, secret-free proof
// retained on a protected native task. ProofSHA256 covers every field except
// itself. It is an integrity checksum, while authenticity comes from rechecking
// the receipt against immutable job/route/recovery/release rows inside the
// fenced terminal transaction.
type PlatformGenerationProviderResultReceipt struct {
	SchemaVersion                  int    `json:"schema_version"`
	ProofContractRevision          string `json:"proof_contract_revision"`
	ProofSHA256                    string `json:"proof_sha256"`
	State                          string `json:"state"`
	ResponseBodyBytes              int    `json:"response_body_bytes"`
	ResponseBodySHA256             string `json:"response_body_sha256"`
	ArtifactURLSHA256              string `json:"artifact_url_sha256,omitempty"`
	RequestJSONSHA256              string `json:"request_json_sha256"`
	JobID                          string `json:"job_id"`
	NativeTaskID                   string `json:"native_task_id"`
	UpstreamTaskID                 string `json:"upstream_task_id"`
	RouteID                        int64  `json:"route_id"`
	ChannelID                      int    `json:"channel_id"`
	SubmissionAttempt              int    `json:"submission_attempt"`
	PublicModel                    string `json:"public_model"`
	Mode                           string `json:"mode"`
	UpstreamModel                  string `json:"upstream_model"`
	ProviderStatus                 string `json:"provider_status"`
	TerminalStatus                 string `json:"terminal_status"`
	Protocol                       string `json:"protocol"`
	ProfileID                      string `json:"profile_id"`
	ProfileRevision                string `json:"profile_revision"`
	ModelReleaseID                 string `json:"model_release_id"`
	ModelReleaseRevision           string `json:"model_release_revision"`
	ModelReleaseCapabilityRevision string `json:"model_release_capability_revision"`
	Resolution                     string `json:"resolution,omitempty"`
	DurationSeconds                int    `json:"duration_seconds,omitempty"`
	AspectRatio                    string `json:"aspect_ratio,omitempty"`
	FramesPresent                  bool   `json:"frames_present"`
	OutputCount                    int    `json:"output_count,omitempty"`
	MediaType                      string `json:"media_type,omitempty"`
	InputTokens                    int    `json:"input_tokens,omitempty"`
	OutputTokens                   int    `json:"output_tokens,omitempty"`
	OutputVideoTokens              int    `json:"output_video_tokens,omitempty"`
	OutputTextTokens               int    `json:"output_text_tokens,omitempty"`
	ThoughtTokens                  int    `json:"thought_tokens,omitempty"`
	CachedTokens                   int    `json:"cached_tokens,omitempty"`
	TotalTokens                    int    `json:"total_tokens,omitempty"`
	ProviderTotalSeconds           int    `json:"provider_total_seconds,omitempty"`
	InputSeconds                   int    `json:"input_seconds,omitempty"`
	OutputSeconds                  int    `json:"output_seconds,omitempty"`
	InputImageCount                int    `json:"input_image_count,omitempty"`
	ArtifactSizeBytes              int64  `json:"artifact_size_bytes,omitempty"`
	ArtifactSHA256                 string `json:"artifact_sha256,omitempty"`
	FailureOwner                   string `json:"failure_owner,omitempty"`
	FailureCode                    string `json:"failure_code,omitempty"`
}

// PlatformGenerationProviderProofConflictReceipt is a fixed-shape signal from
// the generic poller to the leased Platform worker. It contains no provider
// strings; the body digest is enough for an operator to correlate evidence.
type PlatformGenerationProviderProofConflictReceipt struct {
	SchemaVersion         int    `json:"schema_version"`
	ProofContractRevision string `json:"proof_contract_revision"`
	State                 string `json:"state"`
	ErrorCode             string `json:"error_code"`
	ResponseBodyBytes     int    `json:"response_body_bytes"`
	ResponseBodySHA256    string `json:"response_body_sha256"`
}

// PlatformGenerationProviderResultTransition binds the service-layer semantic
// check to the exact Task.Data and ResultURL bytes reloaded by the model-layer
// terminal transaction.
type PlatformGenerationProviderResultTransition struct {
	NativeTaskID       string
	ReceiptSHA256      string
	ReceiptProofSHA256 string
	ArtifactURLSHA256  string
	Succeeded          bool
}

// PlatformGenerationProviderCostReceiptEvidence is the canonical, validated
// terminal usage material consumed by provider-cost reconciliation. It
// contains no credential or provider result URL.
type PlatformGenerationProviderCostReceiptEvidence struct {
	Receipt       PlatformGenerationProviderResultReceipt
	ReceiptSHA256 string
	Request       dto.PlatformGenerationRequest
}

// PlatformGenerationProviderResultProofRevision selects a reviewed proof
// contract, not a provider-supplied revision. Existing Ark receipts retain their
// original revision; another adapter cannot reuse it to attest different bytes.
func PlatformGenerationProviderResultProofRevision(protocol string) (string, bool) {
	switch protocol {
	case generationprofile.VolcengineArkVideoProtocolV1:
		return PlatformGenerationProviderResultProofContractRevision, true
	case generationprofile.MiniMaxH3VideoProtocolV2:
		return PlatformGenerationMiniMaxH3ProviderResultProofContractRevision, true
	case generationprofile.GoogleGeminiVeoVideoProtocolV1:
		return PlatformGenerationGoogleGeminiVeoProviderResultProofContractRevision, true
	case generationprofile.GoogleGeminiInteractionsVideoProtocolV1:
		return PlatformGenerationGoogleGeminiOmniProviderResultProofContractRevision, true
	default:
		return "", false
	}
}

func NewPlatformGenerationProviderProofConflictReceipt(body []byte) PlatformGenerationProviderProofConflictReceipt {
	receipt, _ := NewPlatformGenerationProviderProofConflictReceiptForProtocol(generationprofile.VolcengineArkVideoProtocolV1, body)
	return receipt
}

func NewPlatformGenerationProviderProofConflictReceiptForProtocol(protocol string, body []byte) (PlatformGenerationProviderProofConflictReceipt, error) {
	revision, ok := PlatformGenerationProviderResultProofRevision(protocol)
	if !ok {
		return PlatformGenerationProviderProofConflictReceipt{}, errors.New("generation provider proof protocol is unsupported")
	}
	return PlatformGenerationProviderProofConflictReceipt{
		SchemaVersion:         PlatformGenerationProviderResultReceiptSchemaVersion,
		ProofContractRevision: revision,
		State:                 PlatformGenerationProviderResultReceiptStateReconciliationRequired,
		ErrorCode:             PlatformGenerationErrorProviderPollReconciliationRequired,
		ResponseBodyBytes:     len(body),
		ResponseBodySHA256:    platformGenerationSHA256Bytes(body),
	}, nil
}

func PlatformGenerationProviderProofConflictRequired(data []byte) bool {
	if len(data) == 0 || common.RejectDuplicateJSONKeys(data) != nil {
		return false
	}
	var receipt PlatformGenerationProviderProofConflictReceipt
	if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(string(data)), &receipt); err != nil {
		return false
	}
	return receipt.SchemaVersion == PlatformGenerationProviderResultReceiptSchemaVersion &&
		platformGenerationProviderProofContractRevisionSupported(receipt.ProofContractRevision) &&
		receipt.State == PlatformGenerationProviderResultReceiptStateReconciliationRequired &&
		receipt.ErrorCode == PlatformGenerationErrorProviderPollReconciliationRequired &&
		receipt.ResponseBodyBytes >= 0 && platformGenerationSHA256Revision(receipt.ResponseBodySHA256)
}

func platformGenerationProviderProofContractRevisionSupported(revision string) bool {
	switch revision {
	case PlatformGenerationProviderResultProofContractRevision,
		PlatformGenerationMiniMaxH3ProviderResultProofContractRevision,
		PlatformGenerationGoogleGeminiVeoProviderResultProofContractRevision,
		PlatformGenerationGoogleGeminiOmniProviderResultProofContractRevision:
		return true
	default:
		return false
	}
}

// PlatformGenerationProviderResultReconciliationRequired treats UNKNOWN as a
// fail-closed lifecycle state for Platform-owned native tasks. Those tasks are
// created as SUBMITTED; the protected poller is the only writer that moves one
// to UNKNOWN, and it does so only after provider result proof fails. A damaged
// or partially written receipt therefore cannot make the task eligible for a
// later automatic poll or timeout transition.
func PlatformGenerationProviderResultReconciliationRequired(task *Task) bool {
	return task != nil && task.IsPlatformExternalBilling() &&
		(task.Status == TaskStatusUnknown || PlatformGenerationProviderProofConflictRequired(task.Data))
}

func NewPlatformGenerationProviderResultReceipt(
	task Task,
	binding PlatformGenerationProviderResultBinding,
	observation PlatformGenerationProviderResultObservation,
) (PlatformGenerationProviderResultReceipt, error) {
	if err := validatePlatformGenerationProviderResultObservation(task, binding, observation); err != nil {
		return PlatformGenerationProviderResultReceipt{}, err
	}
	revision, ok := PlatformGenerationProviderResultProofRevision(observation.Protocol)
	if !ok {
		return PlatformGenerationProviderResultReceipt{}, errors.New("generation provider proof protocol is unsupported")
	}
	terminalStatus := string(TaskStatusFailure)
	artifactURLSHA256 := ""
	if observation.Succeeded {
		terminalStatus = string(TaskStatusSuccess)
		artifactURLSHA256 = platformGenerationSHA256Bytes([]byte(observation.ArtifactURL))
	}
	receipt := PlatformGenerationProviderResultReceipt{
		SchemaVersion:                  PlatformGenerationProviderResultReceiptSchemaVersion,
		ProofContractRevision:          revision,
		State:                          PlatformGenerationProviderResultReceiptStateVerified,
		ResponseBodyBytes:              len(observation.ResponseBody),
		ResponseBodySHA256:             platformGenerationSHA256Bytes(observation.ResponseBody),
		ArtifactURLSHA256:              artifactURLSHA256,
		RequestJSONSHA256:              platformGenerationRequestJSONSHA256(binding.Job.RequestJSON),
		JobID:                          binding.Job.ID,
		NativeTaskID:                   task.TaskID,
		UpstreamTaskID:                 task.PrivateData.UpstreamTaskID,
		RouteID:                        binding.Route.ID,
		ChannelID:                      binding.Route.ChannelID,
		SubmissionAttempt:              binding.Admission.Attempt,
		PublicModel:                    binding.Job.Model,
		Mode:                           binding.Job.Mode,
		UpstreamModel:                  binding.Route.UpstreamModel,
		ProviderStatus:                 observation.ProviderStatus,
		TerminalStatus:                 terminalStatus,
		Protocol:                       observation.Protocol,
		ProfileID:                      binding.Route.CapabilityProfileID,
		ProfileRevision:                binding.Route.CapabilityProfileRevision,
		ModelReleaseID:                 binding.Route.ModelReleaseID,
		ModelReleaseRevision:           binding.Route.ModelReleaseRevision,
		ModelReleaseCapabilityRevision: binding.Route.ModelReleaseCapabilityRevision,
		Resolution:                     observation.Resolution,
		DurationSeconds:                observation.DurationSeconds,
		AspectRatio:                    observation.AspectRatio,
		FramesPresent:                  observation.FramesPresent,
		OutputCount:                    observation.OutputCount,
		MediaType:                      observation.MediaType,
		InputTokens:                    observation.InputTokens,
		OutputTokens:                   observation.OutputTokens,
		OutputVideoTokens:              observation.OutputVideoTokens,
		OutputTextTokens:               observation.OutputTextTokens,
		ThoughtTokens:                  observation.ThoughtTokens,
		CachedTokens:                   observation.CachedTokens,
		TotalTokens:                    observation.TotalTokens,
		ProviderTotalSeconds:           observation.ProviderTotalSeconds,
		InputSeconds:                   observation.InputSeconds,
		OutputSeconds:                  observation.OutputSeconds,
		InputImageCount:                observation.InputImageCount,
		ArtifactSizeBytes:              observation.ArtifactSizeBytes,
		ArtifactSHA256:                 observation.ArtifactSHA256,
		FailureOwner:                   observation.FailureOwner,
		FailureCode:                    observation.FailureCode,
	}
	digest, err := platformGenerationProviderResultReceiptProofSHA256(receipt)
	if err != nil {
		return PlatformGenerationProviderResultReceipt{}, err
	}
	receipt.ProofSHA256 = digest
	return receipt, nil
}

func DecodePlatformGenerationProviderResultReceipt(data []byte) (PlatformGenerationProviderResultReceipt, error) {
	var receipt PlatformGenerationProviderResultReceipt
	if len(data) == 0 || common.RejectDuplicateJSONKeys(data) != nil {
		return receipt, errors.New("generation provider result receipt is invalid")
	}
	if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(string(data)), &receipt); err != nil {
		return receipt, errors.New("generation provider result receipt is invalid")
	}
	expected, err := platformGenerationProviderResultReceiptProofSHA256(receipt)
	if err != nil || receipt.ProofSHA256 != expected {
		return receipt, errors.New("generation provider result receipt checksum is invalid")
	}
	return receipt, nil
}

func PlatformGenerationProviderResultReceiptSHA256(data []byte) string {
	return platformGenerationSHA256Bytes(data)
}

func ValidatePlatformGenerationProviderResultReceipt(
	task Task,
	binding PlatformGenerationProviderResultBinding,
	receipt PlatformGenerationProviderResultReceipt,
) error {
	return validatePlatformGenerationProviderResultReceipt(task, binding, receipt)
}

func LoadPlatformGenerationProviderCostReceiptEvidenceTx(
	tx *gorm.DB,
	job PlatformGenerationJob,
	route PlatformGenerationProviderRoute,
) (PlatformGenerationProviderCostReceiptEvidence, error) {
	var evidence PlatformGenerationProviderCostReceiptEvidence
	if tx == nil || job.NativeTaskID == "" || job.NativeTaskID != strings.TrimSpace(job.NativeTaskID) {
		return evidence, ErrPlatformGenerationProviderMaterialReconciliationRequired
	}
	var tasks []Task
	if err := tx.Where("task_id = ?", job.NativeTaskID).Order("id ASC").Limit(2).Find(&tasks).Error; err != nil {
		return evidence, err
	}
	if len(tasks) != 1 || !platformGenerationNativeTaskIsTerminalPair(tasks[0]) ||
		!platformGenerationNativeTaskMatchesRouteBinding(job, tasks[0], route) {
		return evidence, ErrPlatformGenerationProviderMaterialReconciliationRequired
	}
	task := tasks[0]
	var credential ProviderCredentialVersion
	if err := tx.Where("credential_version = ?", task.PrivateData.ProviderCredentialVersion).First(&credential).Error; err != nil {
		return evidence, err
	}
	if !platformGenerationProviderCredentialMatchesRouteBinding(job, task, route, credential) {
		return evidence, ErrPlatformGenerationProviderMaterialReconciliationRequired
	}
	var admission PlatformGenerationRouteAdmission
	if err := tx.Where("job_id = ?", job.ID).First(&admission).Error; err != nil {
		return evidence, err
	}
	binding := PlatformGenerationProviderResultBinding{
		Job: job, Route: route, Admission: admission, Credential: credential,
	}
	receipt, err := DecodePlatformGenerationProviderResultReceipt(task.Data)
	if err != nil || receipt.TerminalStatus != string(TaskStatusSuccess) {
		return evidence, ErrPlatformGenerationProviderMaterialReconciliationRequired
	}
	if task.PrivateData.ResultURL != "" {
		if err := validatePlatformGenerationProviderResultReceipt(task, binding, receipt); err != nil {
			return evidence, ErrPlatformGenerationProviderMaterialReconciliationRequired
		}
	} else {
		if !task.PrivateData.ProviderResultURLScrubbed ||
			validatePlatformGenerationProviderResultReceiptBinding(task, binding, receipt) != nil {
			return evidence, ErrPlatformGenerationProviderMaterialReconciliationRequired
		}
		observation := platformGenerationProviderResultObservationFromReceipt(
			receipt, true, "https://scrubbed.invalid/provider-result",
		)
		if err := validatePlatformGenerationProviderResultObservation(task, binding, observation); err != nil {
			return evidence, ErrPlatformGenerationProviderMaterialReconciliationRequired
		}
	}
	request, _, _, err := ResolvePlatformGenerationProviderResultContract(binding)
	if err != nil {
		return evidence, ErrPlatformGenerationProviderMaterialReconciliationRequired
	}
	evidence.Receipt = receipt
	evidence.ReceiptSHA256 = PlatformGenerationProviderResultReceiptSHA256(task.Data)
	evidence.Request = request
	return evidence, nil
}

// retainPlatformGenerationProviderResultReceiptTx distinguishes a canonical,
// secret-free, contract-validated result receipt from legacy provider response
// material. The signed result URL is still scrubbed, while immutable usage and
// artifact digests remain available for later cost and invoice reconciliation.
func retainPlatformGenerationProviderResultReceiptTx(
	tx *gorm.DB,
	job PlatformGenerationJob,
	task Task,
	route PlatformGenerationProviderRoute,
	credential ProviderCredentialVersion,
) (bool, error) {
	if tx == nil || job.Mode == "text_to_image" || len(task.Data) == 0 {
		return false, nil
	}
	receipt, err := DecodePlatformGenerationProviderResultReceipt(task.Data)
	if err != nil {
		var raw map[string]any
		if common.RejectDuplicateJSONKeys(task.Data) == nil && common.Unmarshal(task.Data, &raw) == nil {
			_, isProofReceipt := raw["proof_contract_revision"]
			if isProofReceipt {
				return false, ErrPlatformGenerationProviderMaterialReconciliationRequired
			}
		}
		return false, nil
	}
	if _, supported := PlatformGenerationProviderResultProofRevision(receipt.Protocol); !supported {
		return false, nil
	}
	var admission PlatformGenerationRouteAdmission
	if err := tx.Where("job_id = ?", job.ID).First(&admission).Error; err != nil {
		return false, err
	}
	binding := PlatformGenerationProviderResultBinding{
		Job:        job,
		Route:      route,
		Admission:  admission,
		Credential: credential,
	}
	succeeded := receipt.TerminalStatus == string(TaskStatusSuccess)
	if !succeeded || task.PrivateData.ResultURL != "" {
		if err := validatePlatformGenerationProviderResultReceipt(task, binding, receipt); err != nil {
			return false, ErrPlatformGenerationProviderMaterialReconciliationRequired
		}
	} else {
		if !task.PrivateData.ProviderResultURLScrubbed ||
			validatePlatformGenerationProviderResultReceiptBinding(task, binding, receipt) != nil {
			return false, ErrPlatformGenerationProviderMaterialReconciliationRequired
		}
		observation := platformGenerationProviderResultObservationFromReceipt(
			receipt,
			true,
			"https://scrubbed.invalid/provider-result",
		)
		if err := validatePlatformGenerationProviderResultObservation(task, binding, observation); err != nil {
			return false, ErrPlatformGenerationProviderMaterialReconciliationRequired
		}
	}
	if succeeded && job.Status == PlatformGenerationStatusSucceeded &&
		receipt.Protocol == generationprofile.GoogleGeminiInteractionsVideoProtocolV1 {
		var outputs []dto.PlatformGenerationArtifact
		rawOutputs := []byte(job.OutputsJSON)
		if common.RejectDuplicateJSONKeys(rawOutputs) != nil || common.Unmarshal(rawOutputs, &outputs) != nil ||
			len(outputs) != 1 || outputs[0].SizeBytes != receipt.ArtifactSizeBytes ||
			outputs[0].SHA256 != receipt.ArtifactSHA256 {
			return false, ErrPlatformGenerationProviderMaterialReconciliationRequired
		}
	}
	return true, nil
}

func validatePlatformGenerationProviderResultTransitionTx(
	tx *gorm.DB,
	now time.Time,
	job PlatformGenerationJob,
	admission PlatformGenerationRouteAdmission,
	route PlatformGenerationProviderRoute,
	transition PlatformGenerationProviderResultTransition,
) error {
	if transition.NativeTaskID == "" || transition.NativeTaskID != job.NativeTaskID ||
		!platformGenerationSHA256Revision(transition.ReceiptSHA256) ||
		!platformGenerationSHA256Revision(transition.ReceiptProofSHA256) {
		return ErrPlatformGenerationProviderMaterialReconciliationRequired
	}
	var tasks []Task
	if err := lockForUpdate(tx.Where("task_id = ?", transition.NativeTaskID).
		Order("id ASC").Limit(2)).Find(&tasks).Error; err != nil {
		return err
	}
	if len(tasks) != 1 || !platformGenerationNativeTaskIsTerminalPair(tasks[0]) {
		return ErrPlatformGenerationProviderMaterialReconciliationRequired
	}
	task := tasks[0]
	var credential ProviderCredentialVersion
	if err := tx.Where("credential_version = ?", task.PrivateData.ProviderCredentialVersion).
		First(&credential).Error; err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return ErrPlatformGenerationProviderMaterialReconciliationRequired
		}
		return err
	}
	if err := ValidatePlatformGenerationRequestSnapshotBinding(job); err != nil {
		return ErrPlatformGenerationProviderMaterialReconciliationRequired
	}
	if err := ValidatePlatformGenerationNativeTaskBindingEvidence(
		job,
		&task,
		route,
		admission,
		credential,
		now,
		false,
	); err != nil {
		return ErrPlatformGenerationProviderMaterialReconciliationRequired
	}
	binding := PlatformGenerationProviderResultBinding{
		Job:        job,
		Route:      route,
		Admission:  admission,
		Credential: credential,
	}
	if PlatformGenerationProviderResultReceiptSHA256(task.Data) != transition.ReceiptSHA256 {
		return ErrPlatformGenerationProviderMaterialReconciliationRequired
	}
	receipt, err := DecodePlatformGenerationProviderResultReceipt(task.Data)
	if err != nil || receipt.ProofSHA256 != transition.ReceiptProofSHA256 ||
		validatePlatformGenerationProviderResultReceipt(task, binding, receipt) != nil {
		return ErrPlatformGenerationProviderMaterialReconciliationRequired
	}
	if transition.Succeeded {
		if task.Status != TaskStatusSuccess || transition.ArtifactURLSHA256 == "" ||
			receipt.ArtifactURLSHA256 != transition.ArtifactURLSHA256 ||
			platformGenerationSHA256Bytes([]byte(task.PrivateData.ResultURL)) != transition.ArtifactURLSHA256 {
			return ErrPlatformGenerationProviderMaterialReconciliationRequired
		}
	} else if task.Status != TaskStatusFailure || transition.ArtifactURLSHA256 != "" ||
		receipt.ArtifactURLSHA256 != "" || task.PrivateData.ResultURL != "" {
		return ErrPlatformGenerationProviderMaterialReconciliationRequired
	}
	return nil
}

// ValidatePlatformGenerationProviderResultForTransfer rechecks the exact
// receipt and provider URL under the active transfer lease before any network
// download. The transfer manifest may carry the raw short-lived URL, but it may
// not substitute a different URL after the terminal proof was accepted.
func ValidatePlatformGenerationProviderResultForTransfer(
	jobID string,
	transferToken string,
	nativeTaskID string,
	receiptSHA256 string,
	receiptProofSHA256 string,
	artifactURLSHA256 string,
) error {
	_, err := ResolvePlatformGenerationProviderResultForTransfer(
		jobID,
		transferToken,
		nativeTaskID,
		receiptSHA256,
		receiptProofSHA256,
		artifactURLSHA256,
	)
	return err
}

// ResolvePlatformGenerationProviderResultForTransfer returns the exact
// receipt only after the active transfer lease, native task, receipt checksum,
// provider URL, immutable route/release binding, and protocol semantics have
// all been revalidated in one transaction.
func ResolvePlatformGenerationProviderResultForTransfer(
	jobID string,
	transferToken string,
	nativeTaskID string,
	receiptSHA256 string,
	receiptProofSHA256 string,
	artifactURLSHA256 string,
) (PlatformGenerationProviderResultReceipt, error) {
	var resolved PlatformGenerationProviderResultReceipt
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		var job PlatformGenerationJob
		if err := lockForUpdate(tx.Where(
			"id = ? AND status = ? AND transfer_lease_token = ? AND transfer_lease_expires_at > ?",
			jobID,
			PlatformGenerationStatusTransferring,
			transferToken,
			now,
		)).First(&job).Error; err != nil {
			return err
		}
		if job.NativeTaskID != nativeTaskID || job.ProviderRouteID <= 0 {
			return ErrPlatformGenerationProviderMaterialReconciliationRequired
		}
		var admission PlatformGenerationRouteAdmission
		if err := tx.Where("job_id = ?", job.ID).First(&admission).Error; err != nil {
			return err
		}
		var route PlatformGenerationProviderRoute
		if err := tx.Where("id = ?", job.ProviderRouteID).First(&route).Error; err != nil {
			return err
		}
		if err := validatePlatformGenerationProviderResultTransitionTx(
			tx,
			now,
			job,
			admission,
			route,
			PlatformGenerationProviderResultTransition{
				NativeTaskID:       nativeTaskID,
				ReceiptSHA256:      receiptSHA256,
				ReceiptProofSHA256: receiptProofSHA256,
				ArtifactURLSHA256:  artifactURLSHA256,
				Succeeded:          true,
			},
		); err != nil {
			return err
		}
		var task Task
		if err := tx.Where("task_id = ?", nativeTaskID).First(&task).Error; err != nil {
			return err
		}
		decoded, err := DecodePlatformGenerationProviderResultReceipt(task.Data)
		if err != nil || decoded.ProofSHA256 != receiptProofSHA256 {
			return ErrPlatformGenerationProviderMaterialReconciliationRequired
		}
		resolved = decoded
		return nil
	})
	return resolved, err
}

// CompletePlatformGenerationProviderProofConflict moves a still-fenced job to
// manual reconciliation without closing its provider admission, recording a
// provider terminal outcome, settling cost, or exposing provider evidence.
func CompletePlatformGenerationProviderProofConflict(
	jobID string,
	leaseToken string,
	fromStatus string,
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
			fromStatus,
			leaseToken,
			now,
		)).First(&job).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				return nil
			}
			return err
		}
		var admission PlatformGenerationRouteAdmission
		if err := lockForUpdate(tx.Where("job_id = ?", job.ID)).First(&admission).Error; err != nil {
			return err
		}
		if !admission.SlotHeld ||
			(admission.State != PlatformGenerationRouteAdmissionHeld &&
				admission.State != PlatformGenerationRouteAdmissionPosting &&
				admission.State != PlatformGenerationRouteAdmissionUnknown) {
			return errors.New("generation provider proof conflict lost its held route admission")
		}
		admissionUpdates := map[string]any{
			"state":      PlatformGenerationRouteAdmissionUnknown,
			"updated_at": now,
		}
		if admission.UnknownAt == nil {
			admissionUpdates["unknown_at"] = now
		}
		admissionResult := tx.Model(&admission).Where("slot_held = ?", true).Updates(admissionUpdates)
		if admissionResult.Error != nil {
			return admissionResult.Error
		}
		if admissionResult.RowsAffected != 1 {
			return errors.New("generation provider proof conflict lost its admission write fence")
		}
		jobResult := tx.Model(&job).Where(
			"status = ? AND poll_lease_token = ? AND poll_lease_expires_at > ?",
			fromStatus,
			leaseToken,
			now,
		).Updates(map[string]any{
			"status":                    PlatformGenerationStatusReconciliationRequired,
			"error_code":                PlatformGenerationErrorProviderPollReconciliationRequired,
			"error_message":             "Provider terminal evidence requires manual reconciliation",
			"error_retryable":           false,
			"error_details_json":        PlatformGenerationProviderReconciliationDetailsJSON(PlatformGenerationProviderReconciliationKindProviderResultProof),
			"next_poll_at":              now.Add(24 * time.Hour),
			"poll_lease_token":          "",
			"poll_lease_expires_at":     nil,
			"callback_backfill_pending": true,
			"updated_at":                now,
		})
		if jobResult.Error != nil {
			return jobResult.Error
		}
		if jobResult.RowsAffected != 1 {
			return errors.New("generation provider proof conflict lost its poll lease fence")
		}
		if fromStatus != PlatformGenerationStatusReconciliationRequired {
			if err := RecordPlatformTaskStageTransitionTx(
				tx,
				job.ID,
				PlatformGenerationStatusReconciliationRequired,
				now,
			); err != nil {
				return err
			}
		}
		if err := buildPlatformGenerationCallbackTx(tx, job.ID, callbackBuilders); err != nil {
			return err
		}
		won = true
		return nil
	})
	return won, err
}

func validatePlatformGenerationProviderResultObservation(
	task Task,
	binding PlatformGenerationProviderResultBinding,
	observation PlatformGenerationProviderResultObservation,
) error {
	if observation.TaskID != task.PrivateData.UpstreamTaskID || observation.TaskID != binding.Job.UpstreamTaskID ||
		observation.Model != binding.Route.UpstreamModel || observation.Protocol == "" ||
		observation.ProviderStatus == "" || observation.ProviderStatus != strings.TrimSpace(observation.ProviderStatus) ||
		len(observation.ResponseBody) == 0 {
		return errors.New("generation provider result identity proof is inconsistent")
	}
	request, profile, artifact, err := ResolvePlatformGenerationProviderResultContract(binding)
	if err != nil {
		return err
	}
	_, supported := PlatformGenerationProviderResultProofRevision(profile.Protocol)
	if !supported || observation.Protocol != profile.Protocol {
		return errors.New("generation provider result protocol proof is inconsistent")
	}
	if err := validatePlatformGenerationProviderResultEvidenceForRequest(request, observation); err != nil {
		return err
	}
	if observation.Succeeded {
		if observation.ProviderStatus != "succeeded" || observation.ArtifactURL == "" ||
			!platformGenerationProviderArtifactURLValid(observation.ArtifactURL) ||
			observation.Resolution != request.Output.Resolution ||
			observation.DurationSeconds != request.Output.DurationSeconds ||
			observation.AspectRatio != request.Output.AspectRatio || observation.FramesPresent ||
			observation.OutputCount != request.Output.Count || observation.MediaType != artifact.MediaType ||
			artifact.MediaType != "video" || artifact.Count != 1 || request.Output.Count != 1 ||
			observation.FailureOwner != "" || observation.FailureCode != "" {
			return errors.New("generation provider success proof is inconsistent")
		}
		return nil
	}
	if observation.ArtifactURL != "" || observation.Resolution != "" || observation.DurationSeconds != 0 ||
		observation.AspectRatio != "" || observation.FramesPresent || observation.OutputCount != 0 ||
		observation.MediaType != "" {
		return errors.New("generation provider failure proof contains output evidence")
	}
	if !platformGenerationProviderFailureClassificationAllowed(
		observation.ProviderStatus,
		observation.FailureOwner,
		observation.FailureCode,
	) {
		return errors.New("generation provider failure proof classification is invalid")
	}
	return nil
}

// platformGenerationProviderFailureClassificationAllowed binds every accepted
// video terminal state to one reviewed, secret-free ownership classification.
// A syntactically safe but semantically arbitrary owner/code pair must never be
// allowed to influence route health or customer-visible failure handling.
func platformGenerationProviderFailureClassificationAllowed(status string, owner string, code string) bool {
	if !platformProviderSafeCodePattern.MatchString(code) {
		return false
	}
	switch status {
	case "failed":
		return (owner == PlatformProviderFailureOwnerClient && code == "content_policy_rejected") ||
			(owner == PlatformProviderFailureOwnerProvider && code == "provider_service_failure") ||
			(owner == PlatformProviderFailureOwnerRelay && code == "unclassified_provider_terminal")
	case "cancelled":
		return owner == PlatformProviderFailureOwnerClient && code == "task_cancelled"
	case "expired":
		return owner == PlatformProviderFailureOwnerRelay && code == "task_expired"
	default:
		return false
	}
}

func validatePlatformGenerationProviderResultReceipt(
	task Task,
	binding PlatformGenerationProviderResultBinding,
	receipt PlatformGenerationProviderResultReceipt,
) error {
	if err := validatePlatformGenerationProviderResultReceiptBinding(task, binding, receipt); err != nil {
		return err
	}
	succeeded := receipt.TerminalStatus == string(TaskStatusSuccess)
	if !succeeded && receipt.TerminalStatus != string(TaskStatusFailure) {
		return errors.New("generation provider result receipt status is invalid")
	}
	artifactURL := ""
	if succeeded {
		artifactURL = task.PrivateData.ResultURL
		if artifactURL == "" || receipt.ArtifactURLSHA256 != platformGenerationSHA256Bytes([]byte(artifactURL)) {
			return errors.New("generation provider result receipt artifact binding is inconsistent")
		}
	} else if receipt.ArtifactURLSHA256 != "" || task.PrivateData.ResultURL != "" {
		return errors.New("generation provider failure receipt has an artifact URL")
	}
	return validatePlatformGenerationProviderResultObservation(
		task,
		binding,
		platformGenerationProviderResultObservationFromReceipt(receipt, succeeded, artifactURL),
	)
}

func validatePlatformGenerationProviderResultReceiptBinding(
	task Task,
	binding PlatformGenerationProviderResultBinding,
	receipt PlatformGenerationProviderResultReceipt,
) error {
	expectedRevision, supported := PlatformGenerationProviderResultProofRevision(receipt.Protocol)
	if receipt.SchemaVersion != PlatformGenerationProviderResultReceiptSchemaVersion ||
		!supported || !platformGenerationProviderResultProofRevisionAccepted(receipt.Protocol, receipt.ProofContractRevision, expectedRevision) ||
		receipt.State != PlatformGenerationProviderResultReceiptStateVerified ||
		receipt.ResponseBodyBytes <= 0 || !platformGenerationSHA256Revision(receipt.ResponseBodySHA256) ||
		receipt.RequestJSONSHA256 != platformGenerationRequestJSONSHA256(binding.Job.RequestJSON) ||
		receipt.JobID != binding.Job.ID || receipt.NativeTaskID != task.TaskID ||
		receipt.UpstreamTaskID != task.PrivateData.UpstreamTaskID || receipt.UpstreamTaskID != binding.Job.UpstreamTaskID ||
		receipt.RouteID != binding.Route.ID || receipt.ChannelID != binding.Route.ChannelID ||
		receipt.SubmissionAttempt != binding.Admission.Attempt ||
		receipt.PublicModel != binding.Job.Model || receipt.Mode != binding.Job.Mode ||
		receipt.UpstreamModel != binding.Route.UpstreamModel ||
		receipt.ProfileID != binding.Route.CapabilityProfileID ||
		receipt.ProfileRevision != binding.Route.CapabilityProfileRevision ||
		receipt.ModelReleaseID != binding.Route.ModelReleaseID ||
		receipt.ModelReleaseRevision != binding.Route.ModelReleaseRevision ||
		receipt.ModelReleaseCapabilityRevision != binding.Route.ModelReleaseCapabilityRevision {
		return errors.New("generation provider result receipt binding is inconsistent")
	}
	return nil
}

func platformGenerationProviderResultObservationFromReceipt(
	receipt PlatformGenerationProviderResultReceipt,
	succeeded bool,
	artifactURL string,
) PlatformGenerationProviderResultObservation {
	return PlatformGenerationProviderResultObservation{
		Protocol:        receipt.Protocol,
		TaskID:          receipt.UpstreamTaskID,
		Model:           receipt.UpstreamModel,
		ProviderStatus:  receipt.ProviderStatus,
		Resolution:      receipt.Resolution,
		DurationSeconds: receipt.DurationSeconds,
		AspectRatio:     receipt.AspectRatio,
		FramesPresent:   receipt.FramesPresent,
		OutputCount:     receipt.OutputCount,
		MediaType:       receipt.MediaType,
		UsageComplete: receipt.Protocol == generationprofile.GoogleGeminiInteractionsVideoProtocolV1 &&
			(receipt.InputTokens != 0 || receipt.OutputTokens != 0 || receipt.OutputVideoTokens != 0 ||
				receipt.OutputTextTokens != 0 || receipt.ThoughtTokens != 0 || receipt.CachedTokens != 0 || receipt.TotalTokens != 0),
		InputTokens:          receipt.InputTokens,
		OutputTokens:         receipt.OutputTokens,
		OutputVideoTokens:    receipt.OutputVideoTokens,
		OutputTextTokens:     receipt.OutputTextTokens,
		ThoughtTokens:        receipt.ThoughtTokens,
		CachedTokens:         receipt.CachedTokens,
		TotalTokens:          receipt.TotalTokens,
		ProviderTotalSeconds: receipt.ProviderTotalSeconds,
		InputSeconds:         receipt.InputSeconds,
		OutputSeconds:        receipt.OutputSeconds,
		InputImageCount:      receipt.InputImageCount,
		ArtifactSizeBytes:    receipt.ArtifactSizeBytes,
		ArtifactSHA256:       receipt.ArtifactSHA256,
		FailureOwner:         receipt.FailureOwner,
		FailureCode:          receipt.FailureCode,
		Succeeded:            succeeded,
		ResponseBody:         []byte{1}, // the persisted body digest proves nonempty receipt evidence
		ArtifactURL:          artifactURL,
	}
}

func platformGenerationProviderResultProofRevisionAccepted(protocol string, actual string, current string) bool {
	if actual == current {
		return true
	}
	switch protocol {
	case generationprofile.VolcengineArkVideoProtocolV1:
		return actual == platformGenerationArkProviderResultProofContractRevisionV1
	case generationprofile.MiniMaxH3VideoProtocolV2:
		return actual == platformGenerationMiniMaxH3ProviderResultProofContractRevisionV1
	case generationprofile.GoogleGeminiInteractionsVideoProtocolV1:
		return actual == platformGenerationGoogleOmniProviderResultProofContractRevisionV1
	default:
		return false
	}
}

func validatePlatformGenerationProviderResultEvidence(
	observation PlatformGenerationProviderResultObservation,
) error {
	return validatePlatformGenerationProviderResultEvidenceForRequest(dto.PlatformGenerationRequest{}, observation)
}

func validatePlatformGenerationProviderResultEvidenceForRequest(
	request dto.PlatformGenerationRequest,
	observation PlatformGenerationProviderResultObservation,
) error {
	hasExtendedEvidence := observation.InputTokens != 0 || observation.OutputTokens != 0 ||
		observation.OutputVideoTokens != 0 || observation.OutputTextTokens != 0 || observation.ThoughtTokens != 0 ||
		observation.CachedTokens != 0 || observation.TotalTokens != 0 ||
		observation.ProviderTotalSeconds != 0 || observation.InputSeconds != 0 ||
		observation.OutputSeconds != 0 || observation.InputImageCount != 0 ||
		observation.ArtifactSizeBytes != 0 || observation.ArtifactSHA256 != ""
	if !observation.Succeeded {
		if hasExtendedEvidence {
			return errors.New("generation provider failure contains usage or artifact evidence")
		}
		return nil
	}
	switch observation.Protocol {
	case generationprofile.GoogleGeminiInteractionsVideoProtocolV1:
		hasTokenUsage := observation.InputTokens != 0 || observation.OutputTokens != 0 ||
			observation.OutputVideoTokens != 0 || observation.OutputTextTokens != 0 ||
			observation.ThoughtTokens != 0 || observation.CachedTokens != 0 || observation.TotalTokens != 0
		usageComplete := observation.UsageComplete || hasTokenUsage
		if observation.ProviderTotalSeconds != 0 || observation.InputSeconds != 0 ||
			observation.OutputSeconds != 0 || observation.InputImageCount != 0 ||
			(usageComplete && (observation.InputTokens <= 0 || observation.OutputTokens <= 0 ||
				observation.CachedTokens < 0 || observation.CachedTokens > observation.InputTokens ||
				observation.TotalTokens < observation.InputTokens ||
				observation.OutputVideoTokens < 0 || observation.OutputTextTokens < 0 || observation.ThoughtTokens < 0 ||
				(observation.OutputVideoTokens+observation.OutputTextTokens > 0 &&
					observation.OutputVideoTokens+observation.OutputTextTokens != observation.OutputTokens) ||
				observation.TotalTokens-observation.InputTokens < observation.OutputTokens+observation.ThoughtTokens)) ||
			(!usageComplete && hasTokenUsage) ||
			observation.ArtifactSizeBytes <= 0 ||
			!platformGenerationProviderArtifactSHA256Valid(observation.ArtifactSHA256) {
			return errors.New("generation Google Gemini Omni result usage or artifact evidence is inconsistent")
		}
	case generationprofile.VolcengineArkVideoProtocolV1:
		if observation.ProviderTotalSeconds != 0 || observation.InputSeconds != 0 ||
			observation.OutputSeconds != 0 || observation.InputImageCount != 0 ||
			observation.OutputVideoTokens != 0 || observation.OutputTextTokens != 0 || observation.ThoughtTokens != 0 ||
			observation.CachedTokens != 0 || observation.ArtifactSizeBytes != 0 ||
			observation.ArtifactSHA256 != "" {
			return errors.New("generation Ark result contains unsupported usage or artifact evidence")
		}
		// Receipts created before token-evidence support remain readable during
		// rolling deployment, but they cannot be costed. Any non-zero usage must
		// be a complete provider token tuple.
		if observation.InputTokens != 0 || observation.OutputTokens != 0 || observation.TotalTokens != 0 {
			if observation.InputTokens < 0 || observation.OutputTokens <= 0 ||
				observation.TotalTokens < observation.InputTokens+observation.OutputTokens {
				return errors.New("generation Ark result token usage is inconsistent")
			}
		}
	case generationprofile.MiniMaxH3VideoProtocolV2:
		if observation.InputTokens != 0 || observation.OutputTokens != 0 ||
			observation.OutputVideoTokens != 0 || observation.OutputTextTokens != 0 || observation.ThoughtTokens != 0 ||
			observation.CachedTokens != 0 || observation.TotalTokens != 0 ||
			observation.ArtifactSizeBytes != 0 || observation.ArtifactSHA256 != "" {
			return errors.New("generation MiniMax H3 result contains unsupported token or artifact evidence")
		}
		// A zero tuple is a legacy receipt. New receipts must bind actual output
		// duration and exact image count to the immutable accepted request.
		if observation.ProviderTotalSeconds != 0 || observation.InputSeconds != 0 ||
			observation.OutputSeconds != 0 || observation.InputImageCount != 0 {
			imageCount := 0
			for _, asset := range request.Inputs.Assets {
				if asset.MediaType == "image" {
					imageCount++
				}
			}
			if observation.OutputSeconds != observation.DurationSeconds || observation.OutputSeconds <= 0 ||
				observation.InputSeconds < 0 || observation.ProviderTotalSeconds != observation.InputSeconds+observation.OutputSeconds ||
				observation.InputImageCount != imageCount {
				return errors.New("generation MiniMax H3 result usage is inconsistent with the accepted request")
			}
		}
	case generationprofile.GoogleGeminiVeoVideoProtocolV1,
		generationprofile.GoogleVertexVeoVideoProtocolV1:
		if hasExtendedEvidence {
			return errors.New("generation Google Veo result contains unsupported usage or artifact evidence")
		}
	default:
		if hasExtendedEvidence {
			return errors.New("generation provider result contains unsupported usage or artifact evidence")
		}
	}
	return nil
}

func platformGenerationProviderArtifactSHA256Valid(value string) bool {
	decoded, err := hex.DecodeString(value)
	return err == nil && len(decoded) == sha256.Size && value == strings.ToLower(value)
}

// ResolvePlatformGenerationProviderResultContract rechecks the immutable
// request, route, model release and adapter snapshot before interpreting any
// provider identity or terminal evidence.
func ResolvePlatformGenerationProviderResultContract(
	binding PlatformGenerationProviderResultBinding,
) (dto.PlatformGenerationRequest, generationprofile.Profile, generationprofile.ArtifactContract, error) {
	var request dto.PlatformGenerationRequest
	if err := ValidatePlatformGenerationRequestSnapshotBinding(binding.Job); err != nil {
		return request, generationprofile.Profile{}, generationprofile.ArtifactContract{}, errors.New("generation provider result request snapshot is not fenced")
	}
	rawRequest := []byte(binding.Job.RequestJSON)
	if common.RejectDuplicateJSONKeys(rawRequest) != nil || common.Unmarshal(rawRequest, &request) != nil {
		return request, generationprofile.Profile{}, generationprofile.ArtifactContract{}, errors.New("generation provider result request snapshot is invalid")
	}
	profile, found, err := generationprofile.ResolveSnapshot(request.Metadata)
	if err != nil || !found {
		return request, generationprofile.Profile{}, generationprofile.ArtifactContract{}, errors.New("generation provider result profile snapshot is unavailable")
	}
	profileSnapshot, _ := request.Metadata[generationprofile.MetadataProfileSnapshot].(string)
	// Discovery and model-release capabilities have distinct hashing contracts.
	// Bind discovery to the SHA-fenced accepted request, and the release triple
	// to its exact immutable route. Existing v4 requests pin that route/profile
	// in recovery evidence; they do not contain a separate release-metadata copy.
	// Never rewrite their request or substitute the current model/channel here.
	if request.Model != binding.Job.Model || request.Mode != binding.Job.Mode ||
		request.ExpectedCapabilityRevision != binding.Job.ExpectedCapabilityRevision ||
		binding.Job.CapabilityRevision != binding.Job.ExpectedCapabilityRevision ||
		!platformGenerationSHA256Revision(binding.Job.CapabilityRevision) ||
		binding.Job.ProviderRouteID <= 0 || binding.Job.ProviderRouteID != binding.Route.ID ||
		binding.Job.ProviderChannelID != binding.Route.ChannelID ||
		binding.Job.ProviderKeyIndex != binding.Route.KeyIndex ||
		binding.Route.Model != request.Model || binding.Route.Mode != request.Mode ||
		profile.ID != binding.Route.CapabilityProfileID || profile.Revision != binding.Route.CapabilityProfileRevision ||
		profileSnapshot != binding.Route.CapabilityProfileSnapshot || profile.NativeChannelType != binding.Route.AcceptedChannelType ||
		binding.Route.ModelReleaseID == "" || len(binding.Route.ModelReleaseID) > 128 ||
		strings.TrimSpace(binding.Route.ModelReleaseID) != binding.Route.ModelReleaseID ||
		!platformGenerationSHA256Revision(binding.Route.ModelReleaseRevision) ||
		!platformGenerationSHA256Revision(binding.Route.ModelReleaseCapabilityRevision) {
		return request, generationprofile.Profile{}, generationprofile.ArtifactContract{}, errors.New("generation provider result immutable contract is inconsistent")
	}
	if err := profile.ValidateRequest(request); err != nil {
		return request, generationprofile.Profile{}, generationprofile.ArtifactContract{}, errors.New("generation provider result request is outside the profile")
	}
	artifact, ok := profile.Artifact(request.Mode)
	if !ok {
		return request, generationprofile.Profile{}, generationprofile.ArtifactContract{}, errors.New("generation provider result artifact contract is unavailable")
	}
	return request, profile, artifact, nil
}

func platformGenerationProviderResultReceiptProofSHA256(receipt PlatformGenerationProviderResultReceipt) (string, error) {
	receipt.ProofSHA256 = ""
	serialized, err := common.Marshal(receipt)
	if err != nil {
		return "", fmt.Errorf("generation provider result receipt could not be canonicalized: %w", err)
	}
	return platformGenerationSHA256Bytes(serialized), nil
}

func platformGenerationSHA256Bytes(value []byte) string {
	digest := sha256.Sum256(value)
	return fmt.Sprintf("sha256:%x", digest)
}

func platformGenerationProviderArtifactURLValid(raw string) bool {
	if raw == "" || raw != strings.TrimSpace(raw) || len(raw) > 8192 || strings.ContainsAny(raw, "\x00\r\n") {
		return false
	}
	parsed, err := url.Parse(raw)
	return err == nil && parsed.Scheme == "https" && parsed.Host != "" && parsed.User == nil && parsed.Fragment == ""
}
