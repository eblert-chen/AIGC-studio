package main

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/google/uuid"
	"github.com/huaweicloud/huaweicloud-sdk-go-obs/obs"
	"github.com/jackc/pgx/v5"
)

const (
	configSchemaVersion     = 2
	reportSchemaVersion     = 2
	checkpointSchemaVersion = 2
	maxBodyBytes            = int64(1024 * 1024 * 1024)
)

var (
	revisionPattern       = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
	bucketPattern         = regexp.MustCompile(`^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$`)
	safeIdentifierPattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9._:@/-]{0,159}$`)
	gitRevisionPattern    = regexp.MustCompile(`^[0-9a-f]{40}$`)
)

type acceptanceConfig struct {
	SchemaVersion int            `json:"schema_version"`
	Environment   string         `json:"environment"`
	Mode          string         `json:"mode"`
	Platform      platformConfig `json:"platform"`
	Provider      providerConfig `json:"provider"`
	Storage       storageConfig  `json:"storage"`
	Runtime       runtimeConfig  `json:"runtime"`
}

type platformConfig struct {
	BaseURL                                   string     `json:"base_url"`
	DownloadGatewayBaseURL                    string     `json:"download_gateway_base_url"`
	CompanyID                                 string     `json:"company_id"`
	UserID                                    string     `json:"user_id"`
	BearerTokenEnv                            string     `json:"bearer_token_env"`
	UseNonProductionIdentityHeads             bool       `json:"use_non_production_identity_headers"`
	DatabaseDSNEnv                            string     `json:"database_dsn_env"`
	ExternalDownloadCompletionID              string     `json:"external_download_completion_id"`
	ExternalDownloadCompletionSource          string     `json:"external_download_completion_source"`
	ExternalDownloadProofPath                 string     `json:"external_download_proof_path"`
	ExternalDownloadProofExpectedSHA256       string     `json:"external_download_proof_expected_sha256"`
	ExternalDownloadProofSignaturePath        string     `json:"external_download_proof_signature_path"`
	ExternalDownloadProofPublicKeyEnv         string     `json:"external_download_proof_public_key_env"`
	ExternalDownloadProofPublicKeyFingerprint string     `json:"external_download_proof_public_key_fingerprint"`
	Task                                      taskConfig `json:"task"`
}

type taskConfig struct {
	ExistingTaskID            string         `json:"existing_task_id"`
	ModelID                   string         `json:"model_id"`
	ExpectedCapabilityVersion int            `json:"expected_capability_version"`
	RequestPayload            map[string]any `json:"request_payload"`
}

type providerConfig struct {
	Name                          string `json:"name"`
	AccessKeyEnv                  string `json:"access_key_env"`
	SecretKeyEnv                  string `json:"secret_key_env"`
	BillEvidencePath              string `json:"bill_evidence_path"`
	BillEvidenceExpectedSHA256    string `json:"bill_evidence_expected_sha256"`
	SourceDocumentPath            string `json:"source_document_path"`
	SourceDocumentExpectedSHA256  string `json:"source_document_expected_sha256"`
	ApprovalPayloadPath           string `json:"approval_payload_path"`
	ApprovalPayloadExpectedSHA256 string `json:"approval_payload_expected_sha256"`
	ApprovalSignaturePath         string `json:"approval_signature_path"`
	ApprovalPublicKeyEnv          string `json:"approval_public_key_env"`
	ApprovalPublicKeyFingerprint  string `json:"approval_public_key_fingerprint"`
}

type storageConfig struct {
	EndpointEnv      string `json:"endpoint_env"`
	BucketEnv        string `json:"bucket_env"`
	AccessKeyEnv     string `json:"access_key_env"`
	SecretKeyEnv     string `json:"secret_key_env"`
	SecurityTokenEnv string `json:"security_token_env"`
}

type runtimeConfig struct {
	RelayDatabaseDSNEnv            string `json:"relay_database_dsn_env"`
	RelayBaseURL                   string `json:"relay_base_url"`
	RelayRuntimeIdentityTokenEnv   string `json:"relay_runtime_identity_token_env"`
	ExpectedUpstreamGitRevision    string `json:"expected_upstream_git_revision"`
	ExpectedSourceGitRevision      string `json:"expected_source_git_revision"`
	ExpectedSourceSnapshotSHA256   string `json:"expected_source_snapshot_sha256"`
	ExpectedSourceSnapshotFiles    int    `json:"expected_source_snapshot_file_count"`
	ExpectedImageDigest            string `json:"expected_image_digest"`
	OutputDirectory                string `json:"output_directory"`
	CreateCheckpointPath           string `json:"create_checkpoint_path"`
	CreateCheckpointExpectedSHA256 string `json:"create_checkpoint_expected_sha256"`
	PollIntervalSeconds            int    `json:"poll_interval_seconds"`
	TimeoutSeconds                 int    `json:"timeout_seconds"`
	AllowLoopbackHTTP              bool   `json:"allow_loopback_http"`
}

type runtimeBuildIdentity struct {
	InstanceID           string `json:"instance_id"`
	UpstreamGitRevision  string `json:"upstream_git_revision"`
	SourceGitRevision    string `json:"source_git_revision"`
	SourceSnapshotSHA256 string `json:"source_snapshot_sha256"`
	SourceSnapshotFiles  int    `json:"source_snapshot_file_count"`
	ImageDigest          string `json:"image_digest"`
}

type runtimeBuildIdentityResponse struct {
	SchemaVersion int                  `json:"schema_version"`
	Kind          string               `json:"kind"`
	Candidate     runtimeBuildIdentity `json:"candidate"`
}

type providerBillEvidence struct {
	SchemaVersion               int    `json:"schema_version"`
	ProviderName                string `json:"provider_name"`
	PlatformTaskID              string `json:"platform_task_id"`
	RelayJobID                  string `json:"relay_job_id"`
	ProviderTaskReference       string `json:"provider_task_reference"`
	ProviderBillReference       string `json:"provider_bill_reference"`
	ChannelID                   int    `json:"channel_id"`
	OccurredAtUTC               string `json:"occurred_at_utc"`
	AmountCents                 int64  `json:"amount_cents"`
	ContractCurrency            string `json:"contract_currency"`
	BilledQuantity              string `json:"billed_quantity"`
	BilledUnit                  string `json:"billed_unit"`
	ContractRateSource          string `json:"contract_rate_source"`
	ContractRateEffectiveAtUTC  string `json:"contract_rate_effective_at_utc"`
	EvidenceSource              string `json:"evidence_source"`
	SourceDocumentSHA256        string `json:"source_document_sha256"`
	AccountConsoleVerifiedAtUTC string `json:"account_console_verified_at_utc"`
	OfficialDocumentURL         string `json:"official_document_url"`
	CostDisposition             string `json:"cost_disposition"`
	ZeroCostReason              string `json:"zero_cost_reason,omitempty"`
	ZeroCostEvidenceReference   string `json:"zero_cost_evidence_reference,omitempty"`
}

type providerBillApproval struct {
	SchemaVersion             int    `json:"schema_version"`
	Kind                      string `json:"kind"`
	CheckpointSHA256          string `json:"checkpoint_sha256"`
	BillEvidenceSHA256        string `json:"bill_evidence_sha256"`
	SourceDocumentSHA256      string `json:"source_document_sha256"`
	ProviderName              string `json:"provider_name"`
	PlatformTaskID            string `json:"platform_task_id"`
	RelayJobID                string `json:"relay_job_id"`
	ProviderTaskReference     string `json:"provider_task_reference"`
	ProviderBillReference     string `json:"provider_bill_reference"`
	ChannelID                 int    `json:"channel_id"`
	AmountCents               int64  `json:"amount_cents"`
	ContractCurrency          string `json:"contract_currency"`
	CostDisposition           string `json:"cost_disposition"`
	ZeroCostReason            string `json:"zero_cost_reason,omitempty"`
	ZeroCostEvidenceReference string `json:"zero_cost_evidence_reference,omitempty"`
	Decision                  string `json:"decision"`
	ApproverSubject           string `json:"approver_subject"`
	ApproverRole              string `json:"approver_role"`
	ApprovedAtUTC             string `json:"approved_at_utc"`
	ExpiresAtUTC              string `json:"expires_at_utc"`
	Nonce                     string `json:"nonce"`
}

type providerBillApprovalSignature struct {
	SchemaVersion   int    `json:"schema_version"`
	Algorithm       string `json:"algorithm"`
	KeyID           string `json:"key_id"`
	PayloadSHA256   string `json:"payload_sha256,omitempty"`
	SignatureBase64 string `json:"signature_base64"`
}

type externalDownloadProof struct {
	SchemaVersion          int     `json:"schema_version"`
	Kind                   string  `json:"kind"`
	CompletionID           string  `json:"completion_id"`
	DownloadRecordID       string  `json:"download_record_id"`
	CompanyID              string  `json:"company_id"`
	TaskID                 string  `json:"task_id"`
	AssetID                string  `json:"asset_id"`
	Source                 string  `json:"source"`
	SignedEventID          string  `json:"signed_event_id"`
	SignedPayloadSHA256    string  `json:"signed_payload_sha256"`
	IssuanceRequestID      string  `json:"issuance_request_id"`
	TransferReference      string  `json:"transfer_reference"`
	GatewayRequestID       string  `json:"gateway_request_id"`
	OBSBucket              string  `json:"obs_bucket"`
	OBSObjectKey           string  `json:"obs_object_key"`
	OBSVersionID           *string `json:"obs_version_id"`
	HTTPStatus             int     `json:"http_status"`
	TransferScope          string  `json:"transfer_scope"`
	BytesSent              int64   `json:"bytes_sent"`
	ExpectedSizeBytes      int64   `json:"expected_size_bytes"`
	ArtifactSHA256         string  `json:"artifact_sha256"`
	CompletedAtUTC         string  `json:"completed_at_utc"`
	PlatformDeliveredAtUTC string  `json:"platform_delivered_at_utc"`
	ProducerSubject        string  `json:"producer_subject"`
	ProducedAtUTC          string  `json:"produced_at_utc"`
	Nonce                  string  `json:"nonce"`
}

type checkpointArtifact struct {
	AssetID     string `json:"asset_id"`
	ObjectKey   string `json:"object_key"`
	ContentType string `json:"content_type"`
	SizeBytes   int64  `json:"size_bytes"`
	SHA256      string `json:"sha256"`
}

type createCheckpoint struct {
	SchemaVersion             int                  `json:"schema_version"`
	Kind                      string               `json:"kind"`
	Status                    string               `json:"status"`
	Reason                    string               `json:"reason"`
	CreateRunID               string               `json:"create_run_id"`
	Environment               string               `json:"environment"`
	Mode                      string               `json:"mode"`
	CreatedAtUTC              string               `json:"created_at_utc"`
	TaskCreatedAtUTC          string               `json:"task_created_at_utc"`
	RelayJobCreatedAtUTC      string               `json:"relay_job_created_at_utc"`
	CompanyID                 string               `json:"company_id"`
	UserID                    string               `json:"user_id"`
	TaskID                    string               `json:"task_id"`
	RelayJobID                string               `json:"relay_job_id"`
	ModelID                   string               `json:"model_id"`
	ExpectedCapabilityVersion int                  `json:"expected_capability_version"`
	RequestPayloadSHA256      string               `json:"request_payload_sha256"`
	ProviderName              string               `json:"provider_name"`
	ProviderTaskReference     string               `json:"provider_task_reference"`
	ChannelID                 int                  `json:"channel_id"`
	RouteID                   int64                `json:"route_id"`
	AccountID                 string               `json:"account_id"`
	KeyFingerprint            string               `json:"key_fingerprint"`
	ProviderResponseSHA256    string               `json:"provider_response_sha256"`
	Artifact                  checkpointArtifact   `json:"artifact"`
	StorageEndpointHost       string               `json:"storage_endpoint_host"`
	StorageBucket             string               `json:"storage_bucket"`
	CallbackEventID           string               `json:"callback_event_id"`
	WalletBindingSHA256       string               `json:"wallet_binding_sha256"`
	RuntimeBuildIdentity      runtimeBuildIdentity `json:"runtime_build_identity"`
}

type platformTask struct {
	ID              string         `json:"id"`
	CompanyID       string         `json:"company_id"`
	UserID          string         `json:"user_id"`
	ModelID         string         `json:"model_id"`
	Status          string         `json:"status"`
	QuoteCents      int64          `json:"quote_cents"`
	ReservedCents   int64          `json:"reserved_cents"`
	ActualCostCents *int64         `json:"actual_cost_cents"`
	RelayJobID      string         `json:"relay_job_id"`
	OutputArtifacts []artifactView `json:"output_artifacts"`
	CreatedAt       time.Time      `json:"created_at"`
	UpdatedAt       time.Time      `json:"updated_at"`
}

type artifactView struct {
	AssetID     string `json:"asset_id"`
	MediaType   string `json:"media_type"`
	ContentType string `json:"content_type"`
	SizeBytes   int64  `json:"size_bytes"`
	SHA256      string `json:"sha256"`
}

type artifactDownload struct {
	URL              string `json:"url"`
	ExpiresSeconds   int    `json:"expires_seconds"`
	DownloadRecordID string `json:"download_record_id"`
	DownloadStatus   string `json:"download_status"`
}

type downloadRecordBinding struct {
	ID                           string
	CompanyID                    string
	TaskID                       string
	AssetID                      string
	RequestedByUserID            string
	ExpiresSeconds               int
	ExpiresAt                    time.Time
	IssuanceRequestID            string
	StorageBindingVersion        int
	StorageProvider              string
	StorageEndpointHost          string
	StorageBucket                string
	StorageObjectKey             string
	StorageVersionID             string
	SourceURLSHA256              string
	RelayIssuedAt                time.Time
	RelayExpiresAt               time.Time
	GatewayRegistrationRequestID string
	GatewayTicketID              string
	GatewayTicketURLSHA256       string
	GatewayIssuedAt              time.Time
	GatewayExpiresAt             time.Time
	GatewayTransferReference     string
	CreatedAt                    time.Time
}

type downloadCompletion struct {
	ID                   string            `json:"id"`
	DownloadRecordID     string            `json:"download_record_id"`
	ExternalEventID      string            `json:"external_event_id"`
	Source               string            `json:"source"`
	BytesSent            int64             `json:"bytes_sent"`
	CompletedAt          time.Time         `json:"completed_at"`
	VerificationVersion  int               `json:"verification_version"`
	ArtifactSHA256       string            `json:"artifact_sha256"`
	ExpectedSizeBytes    int64             `json:"expected_size_bytes"`
	HTTPStatus           int               `json:"http_status"`
	TransferScope        string            `json:"transfer_scope"`
	SourceEvidence       map[string]string `json:"source_evidence"`
	SignedEventID        string            `json:"signed_event_id"`
	SignedEventTimestamp time.Time         `json:"signed_event_timestamp"`
	SignedPayloadSHA256  string            `json:"signed_payload_sha256"`
	VerifiedAt           time.Time         `json:"verified_at"`
	CreatedAt            time.Time         `json:"created_at"`
}

type ledgerRow struct {
	ID                  string
	Kind                string
	AmountCents         int64
	AvailableDeltaCents int64
	ReservedDeltaCents  int64
	IdempotencyKey      string
	CreatedAt           time.Time
}

type callbackRow struct {
	ID            string
	RelayStatus   string
	PayloadSHA256 string
	RequestID     string
	OccurredAt    time.Time
	ReceivedAt    time.Time
}

type channelCostRow struct {
	ID                  string
	AmountCents         int64
	IdempotencyKey      string
	ChannelKey          string
	ChannelType         string
	OccurredAt          time.Time
	ExternalReference   string
	RelayEventID        string
	RelayEventTimestamp time.Time
	RelayPayloadSHA256  string
	CreatedAt           time.Time
}

type walletRow struct {
	AvailableCents int64
	ReservedCents  int64
}

type customerFinancialSnapshot struct {
	TaskStatus          string
	TaskQuoteCents      int64
	TaskReservedCents   int64
	TaskActualCostCents int64
	TaskUpdatedAt       time.Time
	WalletAvailable     int64
	WalletReserved      int64
	LedgerCount         int64
	LedgerAmountSum     int64
	LedgerAvailableSum  int64
	LedgerReservedSum   int64
	LedgerRowsSHA256    string
}

type evidenceFile struct {
	Label          string `json:"label"`
	Path           string `json:"path"`
	ExpectedSHA256 string `json:"expectedSha256"`
}

type runReport struct {
	SchemaVersion        int                   `json:"schema_version"`
	Kind                 string                `json:"kind"`
	Status               string                `json:"status"`
	RunID                string                `json:"run_id"`
	CreatedAtUTC         string                `json:"created_at_utc"`
	Phase                string                `json:"phase"`
	Environment          string                `json:"environment"`
	Missing              []string              `json:"missing,omitempty"`
	Checks               map[string]bool       `json:"checks"`
	TaskID               string                `json:"task_id,omitempty"`
	RelayJobID           string                `json:"relay_job_id,omitempty"`
	Reason               string                `json:"reason,omitempty"`
	Retained             []string              `json:"retained_for_audit,omitempty"`
	Evidence             []evidenceFile        `json:"evidence_files,omitempty"`
	RealRecord           map[string]any        `json:"real_channel_acceptance,omitempty"`
	Documentation        map[string]string     `json:"official_documentation"`
	RuntimeBuildIdentity *runtimeBuildIdentity `json:"runtime_build_identity,omitempty"`
}

type runner struct {
	config               acceptanceConfig
	configDir            string
	phase                string
	taskOverride         string
	runID                string
	httpClient           *http.Client
	checks               map[string]bool
	missing              []string
	outputDir            string
	checkpoint           *createCheckpoint
	checkpointSHA256     string
	checkpointEvidence   *evidenceFile
	runtimeBuildIdentity *runtimeBuildIdentity
	relayLifecycleLock   *model.RelayLifecycleLock
}

func main() {
	configPath := flag.String("config", "", "strict staging acceptance JSON configuration")
	phase := flag.String("phase", "preflight", "preflight, create, or finalize")
	taskID := flag.String("task-id", "", "existing Platform task UUID for finalize")
	flag.Parse()

	if *configPath == "" {
		fmt.Fprintln(os.Stderr, "configuration path is required")
		os.Exit(2)
	}
	config, err := readStrictJSON[acceptanceConfig](*configPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "configuration is invalid")
		os.Exit(2)
	}
	r := &runner{
		config:       config,
		configDir:    filepath.Dir(*configPath),
		phase:        strings.ToLower(strings.TrimSpace(*phase)),
		taskOverride: strings.TrimSpace(*taskID),
		runID:        uuid.NewString(),
		httpClient:   newAcceptanceHTTPClient(),
		checks:       map[string]bool{},
	}
	status := r.run(context.Background())
	if status != "PASS" {
		os.Exit(2)
	}
}

func (r *runner) run(ctx context.Context) string {
	report := runReport{
		SchemaVersion: reportSchemaVersion,
		Kind:          "relay_real_channel_acceptance",
		Status:        "BLOCKED",
		RunID:         r.runID,
		CreatedAtUTC:  time.Now().UTC().Format(time.RFC3339Nano),
		Phase:         r.phase,
		Environment:   r.config.Environment,
		Checks:        r.checks,
		Documentation: map[string]string{
			"kling_text_to_video":          "https://kling.ai/document-api/apiReference/model/textToVideo",
			"kling_official_product_guide": "https://app.klingai.com/cn/quickstart/klingai-video-3-model-user-guide",
			"retrieved_at_utc":             time.Now().UTC().Format(time.RFC3339),
			"verification_note":            "Public API reference content was not machine-readable; endpoint, model entitlement, bill reference, currency, and rate must be verified inside the authenticated Kling account.",
		},
	}
	if err := r.validateConfig(); err != nil {
		report.Status = "FAIL"
		report.Reason = "configuration_validation_failed"
		return r.finish(report)
	}
	r.collectMissingEnvironment()
	if len(r.missing) > 0 {
		report.Missing = append([]string(nil), r.missing...)
		report.Reason = "required_staging_credentials_or_connections_missing"
		return r.finish(report)
	}
	r.checks["secret_values_excluded_from_report"] = true
	if r.phase == "preflight" {
		report.Status = "PASS"
		report.Reason = "preflight_only_no_external_state_changed"
		return r.finish(report)
	}
	if r.phase == "finalize" {
		checkpoint, checkpointFile, err := r.loadFinalizeCheckpoint()
		if err != nil {
			report.Reason = "create_checkpoint_invalid_or_missing"
			return r.finish(report)
		}
		r.checkpoint = &checkpoint
		r.checkpointSHA256 = r.config.Runtime.CreateCheckpointExpectedSHA256
		r.checkpointEvidence = &checkpointFile
		r.taskOverride = checkpoint.TaskID
		r.checks["create_checkpoint_hash_and_shape_verified"] = true
	}
	runtimeIdentity, err := r.readRelayRuntimeBuildIdentity(ctx)
	if err != nil {
		report.Reason = "relay_runtime_build_identity_failed"
		return r.finish(report)
	}
	r.runtimeBuildIdentity = &runtimeIdentity
	report.RuntimeBuildIdentity = &runtimeIdentity
	r.checks["relay_runtime_build_identity_matches_candidate"] = true
	if r.phase == "finalize" && r.checkpoint != nil && !sameRuntimeBuild(
		r.checkpoint.RuntimeBuildIdentity, runtimeIdentity,
	) {
		report.Reason = "relay_runtime_build_identity_changed_since_create"
		return r.finish(report)
	}
	if r.phase == "finalize" {
		r.checks["relay_runtime_build_identity_matches_create_checkpoint"] = true
	}

	if err := r.initRelayDB(); err != nil {
		report.Reason = "relay_database_connection_failed"
		return r.finish(report)
	}
	defer func() {
		_ = model.CloseDB()
		_ = model.ReleaseRelayLifecycleLockBounded(r.relayLifecycleLock)
	}()
	platformDB, err := pgx.Connect(ctx, os.Getenv(r.config.Platform.DatabaseDSNEnv))
	if err != nil {
		report.Reason = "platform_database_connection_failed"
		return r.finish(report)
	}
	defer platformDB.Close(ctx)
	r.checks["real_platform_postgresql_connected"] = true

	if err := r.platformReady(ctx); err != nil {
		report.Reason = "platform_readiness_failed"
		return r.finish(report)
	}

	task, err := r.obtainTask(ctx)
	if err != nil {
		report.Reason = "platform_task_creation_or_poll_failed"
		return r.finish(report)
	}
	report.TaskID = task.ID
	report.RelayJobID = task.RelayJobID
	report.Retained = []string{"Platform task and provider task are retained as immutable acceptance evidence", "OBS artifact is retained for post-acceptance audit"}
	if task.Status != "succeeded" || task.RelayJobID == "" || len(task.OutputArtifacts) != 1 || task.CompanyID != r.config.Platform.CompanyID || task.UserID != r.config.Platform.UserID || task.ModelID != r.config.Platform.Task.ModelID {
		report.Reason = "generation_did_not_reach_single_artifact_success"
		return r.finish(report)
	}

	relayObservation, err := r.relayObservation(task)
	if err != nil {
		report.Reason = "relay_provider_route_evidence_failed"
		return r.finish(report)
	}
	providerTaskEvidence, err := providerTaskEvidenceFromObservation(relayObservation)
	if err != nil {
		report.Status = "FAIL"
		report.Reason = "provider_task_evidence_shape_invalid"
		return r.finish(report)
	}
	obsEvidence, err := r.verifyOBS(ctx, platformDB, task, relayObservation)
	if err != nil {
		report.Reason = "huawei_obs_verification_failed"
		return r.finish(report)
	}
	callbackEvidence, err := r.verifyCallback(ctx, platformDB, task)
	if err != nil {
		report.Reason = "signed_callback_evidence_failed"
		return r.finish(report)
	}
	walletEvidence, err := r.verifyWallet(ctx, platformDB, task)
	if err != nil {
		report.Reason = "wallet_settlement_evidence_failed"
		return r.finish(report)
	}
	if r.phase == "finalize" {
		if err := r.verifyCheckpointBindings(task, relayObservation, obsEvidence, callbackEvidence, walletEvidence); err != nil {
			report.Reason = "create_checkpoint_live_binding_failed"
			return r.finish(report)
		}
		r.checks["create_checkpoint_live_bindings_verified"] = true
	}
	financialBeforeCost, err := r.customerFinancialSnapshot(ctx, platformDB, task)
	if err != nil {
		report.Reason = "customer_financial_snapshot_failed"
		return r.finish(report)
	}

	if r.phase == "create" {
		checkpoint, err := r.buildCreateCheckpoint(task, relayObservation, obsEvidence, callbackEvidence, walletEvidence)
		if err != nil {
			report.Status = "FAIL"
			report.Reason = "create_checkpoint_build_failed"
			return r.finish(report)
		}
		files, err := r.writeCreatePhaseEvidence(providerTaskEvidence, checkpoint)
		if err != nil {
			report.Status = "FAIL"
			report.Reason = "create_only_evidence_write_failed"
			return r.finish(report)
		}
		report.Evidence = append(report.Evidence, files...)
		report.Reason = "external_download_receipt_and_independent_provider_bill_approval_required"
		return r.finish(report)
	}
	if r.phase != "finalize" {
		report.Status = "FAIL"
		report.Reason = "unknown_phase"
		return r.finish(report)
	}

	bill, billSummary, err := r.verifyProviderBill(task, relayObservation)
	if err != nil {
		report.Reason = "independent_provider_bill_evidence_invalid_or_missing"
		return r.finish(report)
	}
	costEvidence, err := r.enqueueAndVerifyCost(ctx, platformDB, task, relayObservation, bill, financialBeforeCost)
	if err != nil {
		report.Reason = "cross_service_provider_cost_evidence_failed"
		return r.finish(report)
	}

	for _, item := range []struct {
		label string
		value any
	}{
		{"provider_task", providerTaskEvidence},
		{"provider_bill", billSummary},
		{"obs_head", obsEvidence},
		{"callback_delivery", callbackEvidence},
		{"wallet_settlement", walletEvidence},
		{"provider_cost_ledger", costEvidence},
	} {
		file, err := r.writeEvidence(item.label, item.value)
		if err != nil {
			report.Reason = "create_only_evidence_write_failed"
			return r.finish(report)
		}
		report.Evidence = append(report.Evidence, file)
	}
	if r.checkpointEvidence != nil {
		report.Evidence = append(report.Evidence, *r.checkpointEvidence)
	}

	record := r.realRecord(task, relayObservation, bill, obsEvidence, callbackEvidence, walletEvidence, costEvidence, report.Evidence)
	report.RealRecord = record
	report.Status = "PASS"
	report.Reason = "real_provider_obs_callback_wallet_and_cost_evidence_complete"
	return r.finish(report)
}

func (r *runner) validateConfig() error {
	c := r.config
	if c.SchemaVersion != configSchemaVersion || c.Environment != "relay-real-channel-staging" || !safeIdentifierPattern.MatchString(c.Environment) {
		return errors.New("schema or environment")
	}
	if r.phase != "preflight" && r.phase != "create" && r.phase != "finalize" {
		return errors.New("phase")
	}
	if c.Mode == "" || !safeIdentifierPattern.MatchString(c.Mode) || c.Platform.CompanyID == "" || c.Platform.UserID == "" || c.Platform.Task.ModelID == "" || c.Platform.Task.ExpectedCapabilityVersion < 1 {
		return errors.New("task identity")
	}
	for _, value := range []string{c.Platform.CompanyID, c.Platform.UserID, c.Platform.Task.ModelID} {
		if parsed, err := uuid.Parse(value); err != nil || parsed.String() != value {
			return errors.New("canonical UUID")
		}
	}
	base, err := url.Parse(c.Platform.BaseURL)
	if err != nil || base.User != nil || base.Hostname() == "" || (base.Scheme != "https" && !(c.Runtime.AllowLoopbackHTTP && base.Scheme == "http" && isLoopbackHost(base.Hostname()))) {
		return errors.New("platform URL")
	}
	if err := validateDownloadGatewayBaseURL(c.Platform.DownloadGatewayBaseURL); err != nil {
		return errors.New("download gateway URL")
	}
	if r.phase != "preflight" && (c.Platform.BearerTokenEnv == "" || c.Platform.UseNonProductionIdentityHeads) {
		return errors.New("real acceptance requires bearer authentication")
	}
	if c.Platform.UseNonProductionIdentityHeads && !(r.phase == "preflight" && c.Runtime.AllowLoopbackHTTP && base.Scheme == "http" && isLoopbackHost(base.Hostname())) {
		return errors.New("identity headers are limited to loopback preflight smoke")
	}
	if c.Platform.DatabaseDSNEnv == "" || c.Runtime.RelayDatabaseDSNEnv == "" || c.Runtime.OutputDirectory == "" ||
		c.Runtime.RelayRuntimeIdentityTokenEnv == "" {
		return errors.New("runtime")
	}
	relayBase, relayURLErr := url.Parse(c.Runtime.RelayBaseURL)
	if relayURLErr != nil || relayBase.User != nil || relayBase.RawQuery != "" || relayBase.Fragment != "" || relayBase.Hostname() == "" ||
		(relayBase.Path != "" && relayBase.Path != "/") ||
		(relayBase.Scheme != "https" && !(c.Runtime.AllowLoopbackHTTP && relayBase.Scheme == "http" && isLoopbackHost(relayBase.Hostname()))) {
		return errors.New("Relay runtime identity URL")
	}
	if c.Runtime.ExpectedUpstreamGitRevision != service.PlatformRelayUpstreamGitRevision ||
		!gitRevisionPattern.MatchString(c.Runtime.ExpectedSourceGitRevision) ||
		c.Runtime.ExpectedSourceGitRevision == strings.Repeat("0", 40) ||
		c.Runtime.ExpectedSourceGitRevision == c.Runtime.ExpectedUpstreamGitRevision ||
		!revisionPattern.MatchString(c.Runtime.ExpectedSourceSnapshotSHA256) ||
		c.Runtime.ExpectedSourceSnapshotSHA256 == "sha256:"+strings.Repeat("0", 64) ||
		c.Runtime.ExpectedSourceSnapshotFiles < 1 ||
		!revisionPattern.MatchString(c.Runtime.ExpectedImageDigest) ||
		c.Runtime.ExpectedImageDigest == "sha256:"+strings.Repeat("0", 64) {
		return errors.New("Relay candidate build identity")
	}
	if !strings.EqualFold(c.Provider.Name, "kling") || c.Provider.AccessKeyEnv == "" || c.Provider.SecretKeyEnv == "" {
		return errors.New("provider")
	}
	if c.Storage.EndpointEnv == "" || c.Storage.BucketEnv == "" || c.Storage.AccessKeyEnv == "" || c.Storage.SecretKeyEnv == "" {
		return errors.New("storage")
	}
	if r.phase == "create" && (c.Platform.Task.ExistingTaskID != "" || r.taskOverride != "") {
		return errors.New("create must create a new Platform task")
	}
	if r.phase == "finalize" {
		if c.Runtime.CreateCheckpointPath == "" || !revisionPattern.MatchString(c.Runtime.CreateCheckpointExpectedSHA256) {
			return errors.New("create checkpoint")
		}
		if c.Provider.BillEvidencePath == "" || !revisionPattern.MatchString(c.Provider.BillEvidenceExpectedSHA256) || c.Provider.SourceDocumentPath == "" || !revisionPattern.MatchString(c.Provider.SourceDocumentExpectedSHA256) {
			return errors.New("provider bill")
		}
		if c.Provider.ApprovalPayloadPath == "" || !revisionPattern.MatchString(c.Provider.ApprovalPayloadExpectedSHA256) || c.Provider.ApprovalSignaturePath == "" || c.Provider.ApprovalPublicKeyEnv == "" || !revisionPattern.MatchString(c.Provider.ApprovalPublicKeyFingerprint) {
			return errors.New("provider bill approval")
		}
		if parsed, err := uuid.Parse(c.Platform.ExternalDownloadCompletionID); err != nil || parsed.String() != c.Platform.ExternalDownloadCompletionID {
			return errors.New("external download completion id")
		}
		if c.Platform.ExternalDownloadCompletionSource != "edge_gateway" {
			return errors.New("external download completion source")
		}
		if c.Platform.ExternalDownloadProofPath == "" || !revisionPattern.MatchString(c.Platform.ExternalDownloadProofExpectedSHA256) || c.Platform.ExternalDownloadProofSignaturePath == "" || c.Platform.ExternalDownloadProofPublicKeyEnv == "" || !revisionPattern.MatchString(c.Platform.ExternalDownloadProofPublicKeyFingerprint) {
			return errors.New("external download producer proof")
		}
		if c.Platform.ExternalDownloadProofPublicKeyFingerprint == c.Provider.ApprovalPublicKeyFingerprint {
			return errors.New("external download producer and bill approval keys must be independent")
		}
	}
	if c.Runtime.PollIntervalSeconds < 0 || c.Runtime.TimeoutSeconds < 0 {
		return errors.New("timeouts")
	}
	return nil
}

func (r *runner) collectMissingEnvironment() {
	names := []string{
		r.config.Provider.AccessKeyEnv,
		r.config.Provider.SecretKeyEnv,
		r.config.Storage.EndpointEnv,
		r.config.Storage.BucketEnv,
		r.config.Storage.AccessKeyEnv,
		r.config.Storage.SecretKeyEnv,
		r.config.Platform.DatabaseDSNEnv,
		r.config.Runtime.RelayDatabaseDSNEnv,
		r.config.Runtime.RelayRuntimeIdentityTokenEnv,
	}
	if r.config.Platform.BearerTokenEnv != "" {
		names = append(names, r.config.Platform.BearerTokenEnv)
	}
	if r.phase == "finalize" {
		names = append(names, r.config.Provider.ApprovalPublicKeyEnv, r.config.Platform.ExternalDownloadProofPublicKeyEnv)
	}
	for _, name := range names {
		if strings.TrimSpace(os.Getenv(name)) == "" {
			r.missing = append(r.missing, name)
		} else {
			r.checks["env_present:"+name] = true
		}
	}
	endpointValue := strings.TrimSpace(os.Getenv(r.config.Storage.EndpointEnv))
	if err := validateHuaweiOBSEndpoint(endpointValue); err != nil {
		if os.Getenv(r.config.Storage.EndpointEnv) != "" {
			r.missing = append(r.missing, r.config.Storage.EndpointEnv+":public_huawei_obs_https_required")
		}
	}
	bucket := strings.TrimSpace(os.Getenv(r.config.Storage.BucketEnv))
	if bucket != "" && !bucketPattern.MatchString(bucket) {
		r.missing = append(r.missing, r.config.Storage.BucketEnv+":valid_bucket_required")
	}
	if len(strings.TrimSpace(os.Getenv(r.config.Provider.AccessKeyEnv))) < 8 && os.Getenv(r.config.Provider.AccessKeyEnv) != "" {
		r.missing = append(r.missing, r.config.Provider.AccessKeyEnv+":invalid")
	}
	if len(strings.TrimSpace(os.Getenv(r.config.Provider.SecretKeyEnv))) < 16 && os.Getenv(r.config.Provider.SecretKeyEnv) != "" {
		r.missing = append(r.missing, r.config.Provider.SecretKeyEnv+":invalid")
	}
	if name := r.config.Platform.BearerTokenEnv; name != "" {
		bearer := os.Getenv(name)
		if bearer != strings.TrimSpace(bearer) || strings.ContainsAny(bearer, "\r\n") || len(bearer) < 16 || isKnownPlaceholder(bearer) {
			r.missing = append(r.missing, name+":normalized_non_placeholder_bearer_required")
		}
	}
	identityToken := os.Getenv(r.config.Runtime.RelayRuntimeIdentityTokenEnv)
	if identityToken != strings.TrimSpace(identityToken) || strings.ContainsAny(identityToken, "\r\n") || len(identityToken) < 32 || isKnownPlaceholder(identityToken) {
		r.missing = append(r.missing, r.config.Runtime.RelayRuntimeIdentityTokenEnv+":normalized_non_placeholder_service_token_required")
	}
	if r.phase == "finalize" {
		publicKey, err := approvalPublicKeyFromEnvironment(r.config.Provider.ApprovalPublicKeyEnv)
		if err != nil {
			r.missing = append(r.missing, r.config.Provider.ApprovalPublicKeyEnv+":valid_ed25519_public_key_required")
		} else if "sha256:"+sha256Hex(publicKey) != r.config.Provider.ApprovalPublicKeyFingerprint {
			r.missing = append(r.missing, r.config.Provider.ApprovalPublicKeyEnv+":fingerprint_mismatch")
		}
		producerKey, producerErr := approvalPublicKeyFromEnvironment(r.config.Platform.ExternalDownloadProofPublicKeyEnv)
		if producerErr != nil {
			r.missing = append(r.missing, r.config.Platform.ExternalDownloadProofPublicKeyEnv+":valid_ed25519_public_key_required")
		} else if "sha256:"+sha256Hex(producerKey) != r.config.Platform.ExternalDownloadProofPublicKeyFingerprint {
			r.missing = append(r.missing, r.config.Platform.ExternalDownloadProofPublicKeyEnv+":fingerprint_mismatch")
		}
	}
}

func (r *runner) loadFinalizeCheckpoint() (createCheckpoint, evidenceFile, error) {
	var zero createCheckpoint
	bytesValue, err := r.readStrictHashedFile(r.config.Runtime.CreateCheckpointPath, r.config.Runtime.CreateCheckpointExpectedSHA256)
	if err != nil {
		return zero, evidenceFile{}, err
	}
	checkpoint, err := decodeStrictJSON[createCheckpoint](bytesValue)
	if err != nil {
		return zero, evidenceFile{}, err
	}
	createdAt, timeErr := time.Parse(time.RFC3339Nano, checkpoint.CreatedAtUTC)
	createRunID, runErr := uuid.Parse(checkpoint.CreateRunID)
	if checkpoint.SchemaVersion != checkpointSchemaVersion || checkpoint.Kind != "relay_real_channel_create_checkpoint" || checkpoint.Status != "BLOCKED" || checkpoint.Reason != "external_download_receipt_and_independent_provider_bill_approval_required" || checkpoint.Environment != r.config.Environment || checkpoint.Mode != r.config.Mode || timeErr != nil || createdAt.After(time.Now().UTC().Add(5*time.Minute)) || createdAt.Before(time.Now().UTC().Add(-30*24*time.Hour)) || runErr != nil || createRunID.String() != checkpoint.CreateRunID || !r.runtimeIdentityMatchesExpected(checkpoint.RuntimeBuildIdentity) {
		return zero, evidenceFile{}, errors.New("checkpoint shape")
	}
	for _, value := range []string{checkpoint.CompanyID, checkpoint.UserID, checkpoint.TaskID, checkpoint.RelayJobID, checkpoint.ModelID, checkpoint.Artifact.AssetID} {
		parsed, parseErr := uuid.Parse(value)
		if parseErr != nil || parsed.String() != value {
			return zero, evidenceFile{}, errors.New("checkpoint identity")
		}
	}
	requestedTaskID := strings.TrimSpace(r.taskOverride)
	if requestedTaskID == "" {
		requestedTaskID = strings.TrimSpace(r.config.Platform.Task.ExistingTaskID)
	}
	if requestedTaskID != "" && requestedTaskID != checkpoint.TaskID {
		return zero, evidenceFile{}, errors.New("checkpoint task override mismatch")
	}
	absolute := r.resolveConfigPath(r.config.Runtime.CreateCheckpointPath)
	relative, err := filepath.Rel(r.configDir, absolute)
	if err != nil {
		return zero, evidenceFile{}, err
	}
	return checkpoint, evidenceFile{
		Label:          "create_checkpoint",
		Path:           filepath.ToSlash(relative),
		ExpectedSHA256: r.config.Runtime.CreateCheckpointExpectedSHA256,
	}, nil
}

func (r *runner) buildCreateCheckpoint(task platformTask, observation, obsEvidence, callbackEvidence, walletEvidence map[string]any) (createCheckpoint, error) {
	if r.runtimeBuildIdentity == nil {
		return createCheckpoint{}, errors.New("Relay runtime build identity is missing")
	}
	job := observation["job"].(model.PlatformGenerationJob)
	route := observation["route"].(model.PlatformGenerationProviderRoute)
	output := observation["output"].(dto.PlatformGenerationArtifact)
	providerTask := observation["provider_task"].(map[string]any)
	requestHash, err := canonicalJSONSHA256(r.config.Platform.Task.RequestPayload)
	if err != nil {
		return createCheckpoint{}, err
	}
	walletHash, err := walletBindingSHA256(walletEvidence)
	if err != nil {
		return createCheckpoint{}, err
	}
	endpoint, err := url.Parse(strings.TrimSpace(os.Getenv(r.config.Storage.EndpointEnv)))
	if err != nil {
		return createCheckpoint{}, err
	}
	return createCheckpoint{
		SchemaVersion:             checkpointSchemaVersion,
		Kind:                      "relay_real_channel_create_checkpoint",
		Status:                    "BLOCKED",
		Reason:                    "external_download_receipt_and_independent_provider_bill_approval_required",
		CreateRunID:               r.runID,
		Environment:               r.config.Environment,
		Mode:                      r.config.Mode,
		CreatedAtUTC:              time.Now().UTC().Format(time.RFC3339Nano),
		TaskCreatedAtUTC:          task.CreatedAt.UTC().Format(time.RFC3339Nano),
		RelayJobCreatedAtUTC:      job.CreatedAt.UTC().Format(time.RFC3339Nano),
		CompanyID:                 task.CompanyID,
		UserID:                    task.UserID,
		TaskID:                    task.ID,
		RelayJobID:                task.RelayJobID,
		ModelID:                   task.ModelID,
		ExpectedCapabilityVersion: r.config.Platform.Task.ExpectedCapabilityVersion,
		RequestPayloadSHA256:      requestHash,
		ProviderName:              route.ProviderName,
		ProviderTaskReference:     job.UpstreamTaskID,
		ChannelID:                 route.ChannelID,
		RouteID:                   route.ID,
		AccountID:                 route.AccountID,
		KeyFingerprint:            "sha256:" + route.KeyFingerprint,
		ProviderResponseSHA256:    providerTask["provider_response_sha256"].(string),
		Artifact: checkpointArtifact{
			AssetID: output.AssetID, ObjectKey: output.ObjectKey, ContentType: output.ContentType,
			SizeBytes: output.SizeBytes, SHA256: output.SHA256,
		},
		StorageEndpointHost:  strings.ToLower(endpoint.Hostname()),
		StorageBucket:        obsEvidence["bucket"].(string),
		CallbackEventID:      callbackEvidence["event_id"].(string),
		WalletBindingSHA256:  walletHash,
		RuntimeBuildIdentity: *r.runtimeBuildIdentity,
	}, nil
}

func (r *runner) verifyCheckpointBindings(task platformTask, observation, obsEvidence, callbackEvidence, walletEvidence map[string]any) error {
	if r.checkpoint == nil {
		return errors.New("checkpoint missing")
	}
	checkpoint := *r.checkpoint
	job := observation["job"].(model.PlatformGenerationJob)
	route := observation["route"].(model.PlatformGenerationProviderRoute)
	output := observation["output"].(dto.PlatformGenerationArtifact)
	providerTask := observation["provider_task"].(map[string]any)
	requestHash, err := canonicalJSONSHA256(r.config.Platform.Task.RequestPayload)
	if err != nil {
		return err
	}
	walletHash, err := walletBindingSHA256(walletEvidence)
	if err != nil {
		return err
	}
	endpoint, err := url.Parse(strings.TrimSpace(os.Getenv(r.config.Storage.EndpointEnv)))
	if err != nil {
		return err
	}
	if checkpoint.CompanyID != r.config.Platform.CompanyID || checkpoint.UserID != r.config.Platform.UserID || checkpoint.TaskID != task.ID || checkpoint.RelayJobID != task.RelayJobID || checkpoint.ModelID != r.config.Platform.Task.ModelID || checkpoint.ExpectedCapabilityVersion != r.config.Platform.Task.ExpectedCapabilityVersion || checkpoint.RequestPayloadSHA256 != requestHash || checkpoint.TaskCreatedAtUTC != task.CreatedAt.UTC().Format(time.RFC3339Nano) || checkpoint.RelayJobCreatedAtUTC != job.CreatedAt.UTC().Format(time.RFC3339Nano) || !strings.EqualFold(checkpoint.ProviderName, route.ProviderName) || checkpoint.ProviderTaskReference != job.UpstreamTaskID || checkpoint.ChannelID != route.ChannelID || checkpoint.RouteID != route.ID || checkpoint.AccountID != route.AccountID || checkpoint.KeyFingerprint != "sha256:"+route.KeyFingerprint || checkpoint.ProviderResponseSHA256 != providerTask["provider_response_sha256"].(string) || checkpoint.Artifact.AssetID != output.AssetID || checkpoint.Artifact.ObjectKey != output.ObjectKey || checkpoint.Artifact.ContentType != output.ContentType || checkpoint.Artifact.SizeBytes != output.SizeBytes || checkpoint.Artifact.SHA256 != output.SHA256 || checkpoint.StorageEndpointHost != strings.ToLower(endpoint.Hostname()) || checkpoint.StorageBucket != obsEvidence["bucket"] || checkpoint.CallbackEventID != callbackEvidence["event_id"] || checkpoint.WalletBindingSHA256 != walletHash || r.runtimeBuildIdentity == nil || !sameRuntimeBuild(checkpoint.RuntimeBuildIdentity, *r.runtimeBuildIdentity) {
		return errors.New("checkpoint live binding mismatch")
	}
	return nil
}

func canonicalJSONSHA256(value any) (string, error) {
	serialized, err := json.Marshal(value)
	if err != nil {
		return "", err
	}
	return "sha256:" + sha256Hex(serialized), nil
}

func walletBindingSHA256(evidence map[string]any) (string, error) {
	binding := map[string]any{
		"company_id": evidence["company_id"], "task_id": evidence["task_id"],
		"reservation_reference": evidence["reservation_reference"], "settlement_reference": evidence["settlement_reference"],
		"quote_cents": evidence["quote_cents"], "settled_amount_cents": evidence["settled_amount_cents"],
		"reserve_available_delta_cents": evidence["reserve_available_delta_cents"], "reserve_reserved_delta_cents": evidence["reserve_reserved_delta_cents"],
		"settle_available_delta_cents": evidence["settle_available_delta_cents"], "settle_reserved_delta_cents": evidence["settle_reserved_delta_cents"],
		"task_ledger_count": evidence["task_ledger_count"], "task_ledger_available_delta_sum": evidence["task_ledger_available_delta_sum"],
		"task_ledger_reserved_delta_sum": evidence["task_ledger_reserved_delta_sum"],
	}
	return canonicalJSONSHA256(binding)
}

func (r *runner) initRelayDB() error {
	dsn := os.Getenv(r.config.Runtime.RelayDatabaseDSNEnv)
	if !strings.HasPrefix(dsn, "postgres://") && !strings.HasPrefix(dsn, "postgresql://") {
		return errors.New("real PostgreSQL required")
	}
	if err := os.Setenv("SQL_DSN", dsn); err != nil {
		return err
	}
	common.IsMasterNode = false
	if err := model.InitDB(); err != nil {
		return err
	}
	lifecycleLock, err := model.AcquireRelayRuntimeLifecycleLock(context.Background(), model.DB)
	if err != nil {
		_ = model.CloseDB()
		return err
	}
	if _, err := model.RequireRelaySchemaCurrent(model.DB); err != nil {
		lifecycleLock.Close()
		_ = model.CloseDB()
		return err
	}
	r.relayLifecycleLock = lifecycleLock
	r.checks["real_relay_postgresql_connected_without_migration"] = true
	r.checks["relay_schema_current_and_lifecycle_locked"] = true
	return nil
}

func (r *runner) platformHeaders() http.Header {
	headers := http.Header{"Accept": []string{"application/json"}}
	if name := r.config.Platform.BearerTokenEnv; name != "" {
		headers.Set("Authorization", "Bearer "+os.Getenv(name))
	}
	if r.phase == "preflight" && r.config.Platform.UseNonProductionIdentityHeads {
		headers.Set("X-Company-ID", r.config.Platform.CompanyID)
		headers.Set("X-User-ID", r.config.Platform.UserID)
	}
	return headers
}

func (r *runner) platformReady(ctx context.Context) error {
	var body map[string]any
	if err := r.doJSON(ctx, http.MethodGet, r.config.Platform.BaseURL+"/health/ready", nil, http.Header{}, http.StatusOK, &body); err != nil {
		return err
	}
	r.checks["platform_readiness_ok"] = true
	return nil
}

func (r *runner) readRelayRuntimeBuildIdentity(ctx context.Context) (runtimeBuildIdentity, error) {
	var zero runtimeBuildIdentity
	endpoint := strings.TrimRight(r.config.Runtime.RelayBaseURL, "/") + "/internal/platform-relay/runtime-build-identity"
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return zero, err
	}
	request.Header.Set(constant.HeaderPlatformGenerationInternalAdmission, os.Getenv(r.config.Runtime.RelayRuntimeIdentityTokenEnv))
	response, err := r.httpClient.Do(request)
	if err != nil {
		return zero, err
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, 64*1024+1))
	if err != nil || len(raw) > 64*1024 || response.StatusCode != http.StatusOK || response.Header.Get("Cache-Control") != "no-store" {
		return zero, errors.New("Relay runtime build identity response is invalid")
	}
	decoded, err := decodeStrictJSON[runtimeBuildIdentityResponse](raw)
	if err != nil || decoded.SchemaVersion != 1 || decoded.Kind != "relay_runtime_build_identity" {
		return zero, errors.New("Relay runtime build identity shape is invalid")
	}
	identity := decoded.Candidate
	if !r.runtimeIdentityMatchesExpected(identity) {
		return zero, errors.New("Relay runtime build identity does not match the pinned candidate")
	}
	return identity, nil
}

func (r *runner) runtimeIdentityMatchesExpected(identity runtimeBuildIdentity) bool {
	instance, err := uuid.Parse(identity.InstanceID)
	return err == nil && instance.String() == identity.InstanceID &&
		identity.UpstreamGitRevision == r.config.Runtime.ExpectedUpstreamGitRevision &&
		identity.SourceGitRevision == r.config.Runtime.ExpectedSourceGitRevision &&
		identity.SourceSnapshotSHA256 == r.config.Runtime.ExpectedSourceSnapshotSHA256 &&
		identity.SourceSnapshotFiles == r.config.Runtime.ExpectedSourceSnapshotFiles &&
		identity.ImageDigest == r.config.Runtime.ExpectedImageDigest
}

func sameRuntimeBuild(left, right runtimeBuildIdentity) bool {
	return left.UpstreamGitRevision == right.UpstreamGitRevision &&
		left.SourceGitRevision == right.SourceGitRevision &&
		left.SourceSnapshotSHA256 == right.SourceSnapshotSHA256 &&
		left.SourceSnapshotFiles == right.SourceSnapshotFiles &&
		left.ImageDigest == right.ImageDigest
}

func (r *runner) obtainTask(ctx context.Context) (platformTask, error) {
	taskID := r.taskOverride
	if taskID == "" {
		taskID = r.config.Platform.Task.ExistingTaskID
	}
	if taskID == "" {
		if r.phase != "create" {
			return platformTask{}, errors.New("finalize requires existing task")
		}
		idempotencyKey := "real-channel-" + r.runID
		payload := map[string]any{
			"model_id":                    r.config.Platform.Task.ModelID,
			"expected_capability_version": r.config.Platform.Task.ExpectedCapabilityVersion,
			"idempotency_key":             idempotencyKey,
			"request_payload":             r.config.Platform.Task.RequestPayload,
		}
		var created platformTask
		headers := r.platformHeaders()
		headers.Set("Content-Type", "application/json")
		endpoint := fmt.Sprintf("%s/api/v1/companies/%s/tasks", strings.TrimRight(r.config.Platform.BaseURL, "/"), r.config.Platform.CompanyID)
		if err := r.doJSON(ctx, http.MethodPost, endpoint, payload, headers, http.StatusCreated, &created); err != nil {
			return platformTask{}, err
		}
		taskID = created.ID
		r.checks["generation_submitted_only_to_platform_public_api"] = true
	}
	if parsed, err := uuid.Parse(taskID); err != nil || parsed.String() != taskID {
		return platformTask{}, errors.New("invalid task id")
	}
	deadline := time.Now().Add(r.timeout())
	for time.Now().Before(deadline) {
		var task platformTask
		endpoint := fmt.Sprintf("%s/api/v1/companies/%s/tasks/%s", strings.TrimRight(r.config.Platform.BaseURL, "/"), r.config.Platform.CompanyID, taskID)
		if err := r.doJSON(ctx, http.MethodGet, endpoint, nil, r.platformHeaders(), http.StatusOK, &task); err != nil {
			return platformTask{}, err
		}
		switch task.Status {
		case "succeeded", "failed", "cancelled":
			r.checks["platform_terminal_status_observed"] = true
			return task, nil
		}
		select {
		case <-ctx.Done():
			return platformTask{}, ctx.Err()
		case <-time.After(r.pollInterval()):
		}
	}
	return platformTask{}, errors.New("task timeout")
}

func (r *runner) relayObservation(task platformTask) (map[string]any, error) {
	var job model.PlatformGenerationJob
	if err := model.DB.Where("id = ?", task.RelayJobID).First(&job).Error; err != nil {
		return nil, err
	}
	var route model.PlatformGenerationProviderRoute
	if err := model.DB.Where("id = ?", job.ProviderRouteID).First(&route).Error; err != nil {
		return nil, err
	}
	if job.Status != model.PlatformGenerationStatusSucceeded || job.UpstreamTaskID == "" || route.ChannelClass != model.PlatformGenerationChannelClassOfficialProvider || !route.ProductionReady || !strings.EqualFold(route.ProviderName, r.config.Provider.Name) {
		return nil, errors.New("route not production official provider")
	}
	expectedFingerprint := sha256Hex([]byte(strings.TrimSpace(os.Getenv(r.config.Provider.AccessKeyEnv)) + "|" + strings.TrimSpace(os.Getenv(r.config.Provider.SecretKeyEnv))))
	if route.KeyFingerprint != expectedFingerprint {
		return nil, errors.New("live credential is not the pinned route")
	}
	var nativeTask model.Task
	if err := model.DB.Where("task_id = ? AND channel_id = ?", job.NativeTaskID, route.ChannelID).First(&nativeTask).Error; err != nil {
		return nil, err
	}
	if nativeTask.PrivateData.UpstreamTaskID != job.UpstreamTaskID {
		return nil, errors.New("native upstream task mismatch")
	}
	var outputs []dto.PlatformGenerationArtifact
	if err := json.Unmarshal([]byte(job.OutputsJSON), &outputs); err != nil || len(outputs) != 1 {
		return nil, errors.New("relay outputs")
	}
	r.checks["real_kling_provider_task_observed"] = true
	r.checks["official_route_and_pinned_credential_verified"] = true
	return map[string]any{
		"job":    job,
		"route":  route,
		"output": outputs[0],
		"provider_task": map[string]any{
			"schema_version":           1,
			"provider_name":            route.ProviderName,
			"provider_task_reference":  job.UpstreamTaskID,
			"native_task_id":           job.NativeTaskID,
			"relay_job_id":             job.ID,
			"route_id":                 strconv.FormatInt(route.ID, 10),
			"channel_id":               strconv.Itoa(route.ChannelID),
			"channel_class":            route.ChannelClass,
			"account_id":               route.AccountID,
			"key_fingerprint":          "sha256:" + route.KeyFingerprint,
			"provider_response_sha256": "sha256:" + sha256Hex(nativeTask.Data),
			"provider_response_bytes":  len(nativeTask.Data),
			"observed_at_utc":          time.Now().UTC().Format(time.RFC3339Nano),
		},
	}, nil
}

func (r *runner) verifyOBS(ctx context.Context, db *pgx.Conn, task platformTask, observation map[string]any) (map[string]any, error) {
	output := observation["output"].(dto.PlatformGenerationArtifact)
	public := task.OutputArtifacts[0]
	if public.AssetID != output.AssetID || public.SHA256 != output.SHA256 || public.SizeBytes != output.SizeBytes || public.ContentType != output.ContentType {
		return nil, errors.New("platform and relay artifact mismatch")
	}
	client, err := r.newOBSClient()
	if err != nil {
		return nil, err
	}
	defer client.Close()
	bucket := strings.TrimSpace(os.Getenv(r.config.Storage.BucketEnv))
	metadata, err := client.GetObjectMetadata(&obs.GetObjectMetadataInput{Bucket: bucket, Key: output.ObjectKey})
	if err != nil || metadata == nil || metadata.StatusCode < 200 || metadata.StatusCode >= 300 {
		return nil, errors.New("OBS HEAD failed")
	}
	storedSHA := metadataValue(metadata.Metadata, "sha256")
	storedSize := metadataValue(metadata.Metadata, "size-bytes")
	if metadata.ContentLength != output.SizeBytes || metadata.ContentType != output.ContentType || storedSHA != output.SHA256 || storedSize != strconv.FormatInt(output.SizeBytes, 10) {
		return nil, errors.New("OBS HEAD mismatch")
	}

	headers := r.platformHeaders()
	endpoint := fmt.Sprintf("%s/api/v1/companies/%s/tasks/%s/artifacts/%s/download", strings.TrimRight(r.config.Platform.BaseURL, "/"), r.config.Platform.CompanyID, task.ID, output.AssetID)
	var download artifactDownload
	if err := r.doJSON(ctx, http.MethodGet, endpoint, nil, headers, http.StatusOK, &download); err != nil {
		return nil, err
	}
	if download.DownloadStatus != "issued" || download.URL == "" || download.ExpiresSeconds < 30 || download.ExpiresSeconds > 3600 {
		return nil, errors.New("signed download not issued")
	}
	if parsed, err := uuid.Parse(download.DownloadRecordID); err != nil || parsed.String() != download.DownloadRecordID {
		return nil, errors.New("invalid download record id")
	}
	now := time.Now().UTC()
	record, err := loadDownloadRecordBinding(ctx, db, download.DownloadRecordID)
	if err != nil {
		return nil, errors.New("download record storage binding is unavailable")
	}
	if err := validateDownloadRecordBinding(
		record,
		download,
		task,
		output,
		strings.TrimSpace(os.Getenv(r.config.Storage.EndpointEnv)),
		bucket,
		now,
	); err != nil {
		return nil, errors.New("download record is not bound to the verified OBS object and Gateway ticket")
	}
	gatewayMeta, err := validateDownloadGatewayTicketURL(download.URL, r.config.Platform.DownloadGatewayBaseURL)
	if err != nil {
		return nil, errors.New("Platform download URL is not a bounded Gateway ticket")
	}
	unsignedURL, err := anonymousOBSObjectURL(strings.TrimSpace(os.Getenv(r.config.Storage.EndpointEnv)), bucket, output.ObjectKey)
	if err != nil {
		return nil, errors.New("anonymous OBS probe URL is invalid")
	}
	request, _ := http.NewRequestWithContext(ctx, http.MethodGet, unsignedURL, nil)
	request.Header.Set("Accept-Encoding", "identity")
	anonymousResponse, err := r.httpClient.Do(request)
	if err != nil {
		return nil, errors.New("anonymous OBS probe failed")
	}
	_, _ = io.Copy(io.Discard, io.LimitReader(anonymousResponse.Body, 4096))
	anonymousResponse.Body.Close()
	if !anonymousOBSAccessDenied(anonymousResponse.StatusCode) {
		return nil, errors.New("OBS anonymous probe did not return an explicit access denial")
	}

	request, _ = http.NewRequestWithContext(ctx, http.MethodGet, download.URL, nil)
	request.Header.Set("Accept-Encoding", "identity")
	response, err := r.httpClient.Do(request)
	if err != nil || response == nil || !isCompleteArtifactHTTPResponse(response, output.SizeBytes) || response.Header.Get("Accept-Ranges") != "none" || !strings.Contains(strings.ToLower(response.Header.Get("Cache-Control")), "no-store") {
		if response != nil {
			response.Body.Close()
		}
		return nil, errors.New("Gateway full-body GET failed")
	}
	gatewayRequestID := strings.TrimSpace(response.Header.Get("X-Request-ID"))
	gatewayTransferReference := strings.TrimSpace(response.Header.Get("X-Transfer-Reference"))
	if parsed, parseErr := uuid.Parse(gatewayRequestID); parseErr != nil || parsed.String() != gatewayRequestID || gatewayRequestID != record.IssuanceRequestID || gatewayTransferReference != record.GatewayTransferReference {
		response.Body.Close()
		return nil, errors.New("Gateway response identity is not bound to the issued ticket")
	}
	hasher := sha256.New()
	count, copyErr := io.Copy(hasher, io.LimitReader(response.Body, maxBodyBytes+1))
	response.Body.Close()
	if copyErr != nil || count > maxBodyBytes || count != output.SizeBytes || hex.EncodeToString(hasher.Sum(nil)) != output.SHA256 {
		return nil, errors.New("Gateway full-body GET digest mismatch")
	}
	replayRequest, _ := http.NewRequestWithContext(ctx, http.MethodGet, download.URL, nil)
	replayRequest.Header.Set("Accept-Encoding", "identity")
	replayResponse, replayErr := r.httpClient.Do(replayRequest)
	if replayErr != nil || replayResponse == nil {
		return nil, errors.New("Gateway one-time replay probe failed")
	}
	_, _ = io.Copy(io.Discard, io.LimitReader(replayResponse.Body, 4096))
	replayResponse.Body.Close()
	if replayResponse.StatusCode != http.StatusNotFound {
		return nil, errors.New("Gateway ticket was not one-time")
	}
	r.checks["obs_private_anonymous_get_denied"] = true
	r.checks["obs_head_matches_relay_artifact"] = true
	r.checks["platform_issued_gateway_full_body_digest_verified"] = true
	r.checks["platform_gateway_ticket_and_storage_binding_verified"] = true
	r.checks["platform_gateway_ticket_one_time_replay_denied"] = true
	evidence := map[string]any{
		"schema_version":                       2,
		"bucket":                               bucket,
		"object_key":                           output.ObjectKey,
		"etag":                                 strings.Trim(metadata.ETag, "\""),
		"size_bytes":                           output.SizeBytes,
		"content_type":                         output.ContentType,
		"sha256":                               output.SHA256,
		"metadata_sha256":                      storedSHA,
		"anonymous_get_status":                 anonymousResponse.StatusCode,
		"gateway_get_bytes":                    count,
		"gateway_get_sha256":                   hex.EncodeToString(hasher.Sum(nil)),
		"download_record_id":                   download.DownloadRecordID,
		"storage_endpoint_host":                record.StorageEndpointHost,
		"storage_source_url_sha256":            "sha256:" + record.SourceURLSHA256,
		"relay_source_issued_at_utc":           record.RelayIssuedAt.UTC().Format(time.RFC3339Nano),
		"relay_source_expires_at_utc":          record.RelayExpiresAt.UTC().Format(time.RFC3339Nano),
		"gateway_origin_host":                  gatewayMeta.EndpointHost,
		"gateway_ticket_path_sha256":           "sha256:" + gatewayMeta.PathSHA256,
		"gateway_ticket_url_sha256":            "sha256:" + record.GatewayTicketURLSHA256,
		"gateway_ticket_id":                    record.GatewayTicketID,
		"gateway_issued_at_utc":                record.GatewayIssuedAt.UTC().Format(time.RFC3339Nano),
		"gateway_expires_at_utc":               record.GatewayExpiresAt.UTC().Format(time.RFC3339Nano),
		"gateway_expires_seconds":              download.ExpiresSeconds,
		"gateway_transfer_reference":           record.GatewayTransferReference,
		"gateway_request_id":                   gatewayRequestID,
		"gateway_one_time_replay_status":       replayResponse.StatusCode,
		"gateway_immutable_storage_binding_v1": true,
		"checked_at_utc":                       time.Now().UTC().Format(time.RFC3339Nano),
		"verified":                             true,
	}
	if r.phase == "create" {
		evidence["external_download_completion_gate"] = "BLOCKED_pending_independent_producer_proof"
		return evidence, nil
	}
	externalEvidence, err := r.verifyExternalDownloadCompletion(ctx, db, task, output)
	if err != nil {
		return nil, err
	}
	for key, value := range externalEvidence {
		evidence[key] = value
	}
	r.checks["external_download_completion_record_verified"] = true
	r.checks["external_download_producer_ed25519_proof_verified"] = true
	return evidence, nil
}

type downloadGatewayURLMetadata struct {
	EndpointHost string
	PathSHA256   string
}

func validateDownloadGatewayBaseURL(raw string) error {
	if raw == "" || raw != strings.TrimSpace(raw) || strings.ContainsAny(raw, "\r\n\x00") {
		return errors.New("invalid Download Gateway base URL")
	}
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Scheme != "https" || parsed.User != nil || parsed.Hostname() == "" || parsed.RawQuery != "" || parsed.ForceQuery || parsed.Fragment != "" || (parsed.Path != "" && parsed.Path != "/") || (parsed.Port() != "" && parsed.Port() != "443") {
		return errors.New("invalid Download Gateway base URL")
	}
	host := parsed.Hostname()
	if host != strings.ToLower(strings.TrimSuffix(host, ".")) || net.ParseIP(host) != nil || host == "localhost" || !strings.Contains(host, ".") {
		return errors.New("Download Gateway base URL must use a canonical public hostname")
	}
	return nil
}

func validateDownloadGatewayTicketURL(raw, baseRaw string) (downloadGatewayURLMetadata, error) {
	var metadata downloadGatewayURLMetadata
	if err := validateDownloadGatewayBaseURL(baseRaw); err != nil || raw == "" || raw != strings.TrimSpace(raw) || strings.ContainsAny(raw, "\r\n\x00") {
		return metadata, errors.New("invalid Download Gateway ticket expectation")
	}
	base, _ := url.Parse(baseRaw)
	ticket, err := url.Parse(raw)
	if err != nil || ticket.Scheme != base.Scheme || ticket.User != nil || ticket.Hostname() == "" || !strings.EqualFold(ticket.Hostname(), base.Hostname()) || ticket.Port() != base.Port() || ticket.RawQuery != "" || ticket.ForceQuery || ticket.Fragment != "" || ticket.RawPath != "" || ticket.EscapedPath() != ticket.Path {
		return metadata, errors.New("Download Gateway ticket is outside the configured origin")
	}
	const prefix = "/downloads/"
	if !strings.HasPrefix(ticket.Path, prefix) || strings.Count(ticket.Path, "/") != 2 {
		return metadata, errors.New("Download Gateway ticket path is invalid")
	}
	token := strings.TrimPrefix(ticket.Path, prefix)
	decoded, decodeErr := base64.RawURLEncoding.DecodeString(token)
	if decodeErr != nil || len(decoded) != sha256.Size || base64.RawURLEncoding.EncodeToString(decoded) != token {
		return metadata, errors.New("Download Gateway ticket token is not canonical")
	}
	return downloadGatewayURLMetadata{
		EndpointHost: strings.ToLower(ticket.Hostname()),
		PathSHA256:   sha256Hex([]byte(ticket.Path)),
	}, nil
}

func anonymousOBSObjectURL(endpointRaw, bucket, objectKey string) (string, error) {
	if err := validateHuaweiOBSEndpoint(endpointRaw); err != nil || !bucketPattern.MatchString(bucket) || service.ValidatePlatformArtifactObjectKey(objectKey) != nil {
		return "", errors.New("invalid OBS object binding")
	}
	endpoint, _ := url.Parse(endpointRaw)
	endpoint.Path = "/" + bucket + "/" + objectKey
	endpoint.RawPath = ""
	endpoint.RawQuery = ""
	endpoint.ForceQuery = false
	endpoint.Fragment = ""
	return endpoint.String(), nil
}

func loadDownloadRecordBinding(ctx context.Context, db *pgx.Conn, recordID string) (downloadRecordBinding, error) {
	var record downloadRecordBinding
	err := db.QueryRow(ctx, `
SELECT id,company_id,task_id,asset_id,requested_by_user_id,expires_seconds,expires_at,request_id,
       COALESCE(storage_binding_version,0),COALESCE(storage_provider,''),COALESCE(storage_endpoint_host,''),
       COALESCE(storage_bucket,''),COALESCE(storage_object_key,''),COALESCE(storage_version_id,''),
       COALESCE(source_url_sha256,''),COALESCE(relay_issued_at,'epoch'::timestamptz),
       COALESCE(relay_expires_at,'epoch'::timestamptz),COALESCE(gateway_registration_request_id,''),
       COALESCE(gateway_ticket_id,''),COALESCE(gateway_ticket_url_sha256,''),
       COALESCE(gateway_issued_at,'epoch'::timestamptz),COALESCE(gateway_expires_at,'epoch'::timestamptz),
       COALESCE(gateway_transfer_reference,''),created_at
FROM download_records WHERE id=$1`, recordID).Scan(
		&record.ID, &record.CompanyID, &record.TaskID, &record.AssetID, &record.RequestedByUserID,
		&record.ExpiresSeconds, &record.ExpiresAt, &record.IssuanceRequestID,
		&record.StorageBindingVersion, &record.StorageProvider, &record.StorageEndpointHost,
		&record.StorageBucket, &record.StorageObjectKey, &record.StorageVersionID,
		&record.SourceURLSHA256, &record.RelayIssuedAt, &record.RelayExpiresAt,
		&record.GatewayRegistrationRequestID, &record.GatewayTicketID, &record.GatewayTicketURLSHA256,
		&record.GatewayIssuedAt, &record.GatewayExpiresAt, &record.GatewayTransferReference, &record.CreatedAt,
	)
	return record, err
}

func validateDownloadRecordBinding(record downloadRecordBinding, download artifactDownload, task platformTask, output dto.PlatformGenerationArtifact, endpointRaw, bucket string, now time.Time) error {
	endpoint, parseErr := url.Parse(endpointRaw)
	if parseErr != nil || validateHuaweiOBSEndpoint(endpointRaw) != nil {
		return errors.New("invalid OBS endpoint")
	}
	for _, value := range []string{record.ID, record.CompanyID, record.TaskID, record.RequestedByUserID, record.GatewayRegistrationRequestID, record.GatewayTicketID, record.GatewayTransferReference, record.IssuanceRequestID} {
		parsed, err := uuid.Parse(value)
		if err != nil || parsed.String() != value {
			return errors.New("download binding identity is not canonical")
		}
	}
	if record.GatewayRegistrationRequestID == record.IssuanceRequestID || record.GatewayRegistrationRequestID == record.GatewayTicketID || record.GatewayRegistrationRequestID == record.GatewayTransferReference || record.GatewayTicketID == record.GatewayTransferReference || record.GatewayTransferReference == record.IssuanceRequestID {
		return errors.New("Gateway registration, issuance, ticket, and transfer identities are not independent")
	}
	if record.ID != download.DownloadRecordID || record.CompanyID != task.CompanyID || record.TaskID != task.ID || record.AssetID != output.AssetID || record.RequestedByUserID != task.UserID {
		return errors.New("download binding identity mismatch")
	}
	if record.StorageBindingVersion != 1 || record.StorageProvider != "huawei_obs" || record.StorageEndpointHost != strings.ToLower(strings.TrimSuffix(endpoint.Hostname(), ".")) || record.StorageBucket != bucket || record.StorageObjectKey != output.ObjectKey || record.StorageVersionID != "" {
		return errors.New("download storage binding mismatch")
	}
	if !revisionPattern.MatchString("sha256:"+record.SourceURLSHA256) || !revisionPattern.MatchString("sha256:"+record.GatewayTicketURLSHA256) || record.GatewayTicketURLSHA256 != sha256Hex([]byte(download.URL)) {
		return errors.New("download URL digest binding mismatch")
	}
	if download.ExpiresSeconds < 30 || download.ExpiresSeconds > 3600 || record.ExpiresSeconds != download.ExpiresSeconds || !record.ExpiresAt.Equal(record.GatewayExpiresAt) || record.GatewayExpiresAt.Sub(record.GatewayIssuedAt) != time.Duration(download.ExpiresSeconds)*time.Second {
		return errors.New("Gateway ticket lifetime mismatch")
	}
	if record.RelayIssuedAt.IsZero() || !record.RelayExpiresAt.After(record.RelayIssuedAt) || record.RelayExpiresAt.Sub(record.RelayIssuedAt) > time.Hour || record.GatewayIssuedAt.Before(record.RelayIssuedAt) || record.GatewayExpiresAt.After(record.RelayExpiresAt) {
		return errors.New("Gateway ticket is outside the Relay source lifetime")
	}
	if record.GatewayIssuedAt.After(now.Add(30*time.Second)) || record.GatewayIssuedAt.Before(now.Add(-5*time.Minute)) || !record.GatewayExpiresAt.After(now) || record.CreatedAt.Before(record.GatewayIssuedAt.Add(-time.Second)) || record.CreatedAt.After(now.Add(30*time.Second)) {
		return errors.New("Gateway ticket issuance time is invalid")
	}
	return nil
}

func validateExternalDownloadRecordBinding(record downloadRecordBinding, task platformTask, output dto.PlatformGenerationArtifact, endpointRaw, bucket string, checkpointTime, now time.Time) error {
	endpoint, parseErr := url.Parse(endpointRaw)
	if parseErr != nil || validateHuaweiOBSEndpoint(endpointRaw) != nil {
		return errors.New("invalid OBS endpoint")
	}
	identities := []string{
		record.ID,
		record.CompanyID,
		record.TaskID,
		record.RequestedByUserID,
		record.IssuanceRequestID,
		record.GatewayRegistrationRequestID,
		record.GatewayTicketID,
		record.GatewayTransferReference,
	}
	for _, value := range identities {
		parsed, err := uuid.Parse(value)
		if err != nil || parsed.String() != value {
			return errors.New("external download identity is not canonical")
		}
	}
	if record.GatewayRegistrationRequestID == record.IssuanceRequestID || record.GatewayRegistrationRequestID == record.GatewayTicketID || record.GatewayRegistrationRequestID == record.GatewayTransferReference || record.GatewayTicketID == record.GatewayTransferReference || record.GatewayTransferReference == record.IssuanceRequestID {
		return errors.New("Gateway registration, issuance, ticket, and transfer identities are not independent")
	}
	if record.CompanyID != task.CompanyID || record.TaskID != task.ID || record.AssetID != output.AssetID || record.RequestedByUserID != task.UserID {
		return errors.New("external download identity mismatch")
	}
	if record.StorageBindingVersion != 1 || record.StorageProvider != "huawei_obs" || record.StorageEndpointHost != strings.ToLower(strings.TrimSuffix(endpoint.Hostname(), ".")) || record.StorageBucket != bucket || record.StorageObjectKey != output.ObjectKey || record.StorageVersionID != "" || !revisionPattern.MatchString("sha256:"+record.SourceURLSHA256) || !revisionPattern.MatchString("sha256:"+record.GatewayTicketURLSHA256) {
		return errors.New("external download storage binding mismatch")
	}
	if record.ExpiresSeconds < 30 || record.ExpiresSeconds > 3600 || !record.ExpiresAt.Equal(record.GatewayExpiresAt) || record.GatewayExpiresAt.Sub(record.GatewayIssuedAt) != time.Duration(record.ExpiresSeconds)*time.Second || record.RelayIssuedAt.IsZero() || !record.RelayExpiresAt.After(record.RelayIssuedAt) || record.RelayExpiresAt.Sub(record.RelayIssuedAt) > time.Hour || record.GatewayIssuedAt.Before(record.RelayIssuedAt) || record.GatewayExpiresAt.After(record.RelayExpiresAt) {
		return errors.New("external Gateway ticket lifetime mismatch")
	}
	if record.CreatedAt.Before(checkpointTime) || record.GatewayIssuedAt.Before(checkpointTime) || record.GatewayIssuedAt.After(now.Add(30*time.Second)) || record.CreatedAt.After(now.Add(30*time.Second)) {
		return errors.New("external Gateway ticket was not issued after the checkpoint")
	}
	return nil
}

type signedOBSURLMetadata struct {
	EndpointHost string
	PathSHA256   string
	Algorithm    string
	ExpiresAt    time.Time
}

func validateHuaweiOBSEndpoint(raw string) error {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Scheme != "https" || parsed.User != nil || parsed.Hostname() == "" || parsed.RawQuery != "" || parsed.Fragment != "" || (parsed.Path != "" && parsed.Path != "/") || (parsed.Port() != "" && parsed.Port() != "443") {
		return errors.New("invalid Huawei OBS endpoint")
	}
	host := strings.ToLower(strings.TrimSuffix(parsed.Hostname(), "."))
	if ip := net.ParseIP(host); ip != nil || host == "localhost" || !strings.HasSuffix(host, ".myhuaweicloud.com") {
		return errors.New("Huawei OBS endpoint must be a public Huawei hostname")
	}
	return nil
}

func validatePlatformSignedOBSURL(raw, endpointRaw, bucket, objectKey string, expiresSeconds int, now time.Time) (signedOBSURLMetadata, error) {
	var metadata signedOBSURLMetadata
	if err := validateHuaweiOBSEndpoint(endpointRaw); err != nil || !bucketPattern.MatchString(bucket) || service.ValidatePlatformArtifactObjectKey(objectKey) != nil || expiresSeconds < 30 || expiresSeconds > 3600 {
		return metadata, errors.New("invalid signed URL expectation")
	}
	endpoint, _ := url.Parse(endpointRaw)
	signed, err := url.Parse(raw)
	if err != nil || signed.Scheme != "https" || signed.User != nil || signed.Hostname() == "" || signed.Fragment != "" || (signed.Port() != "" && signed.Port() != "443") || signed.RawQuery == "" {
		return metadata, errors.New("invalid signed URL")
	}
	endpointHost := strings.ToLower(strings.TrimSuffix(endpoint.Hostname(), "."))
	signedHost := strings.ToLower(strings.TrimSuffix(signed.Hostname(), "."))
	pathStyle := signedHost == endpointHost
	virtualHostStyle := signedHost == strings.ToLower(bucket+"."+endpointHost)
	if !pathStyle && !virtualHostStyle {
		return metadata, errors.New("signed URL host is not the configured OBS bucket")
	}
	escapedPath := signed.EscapedPath()
	lowerEscapedPath := strings.ToLower(escapedPath)
	if strings.Contains(lowerEscapedPath, "%2f") || strings.Contains(lowerEscapedPath, "%5c") || strings.Contains(lowerEscapedPath, "%25") || strings.Contains(signed.Path, "\\") || path.Clean(signed.Path) != signed.Path {
		return metadata, errors.New("signed URL path is ambiguous")
	}
	expectedPath := "/" + objectKey
	if pathStyle {
		expectedPath = "/" + bucket + "/" + objectKey
	}
	if signed.Path != expectedPath {
		return metadata, errors.New("signed URL path is not the verified object")
	}
	query := signed.Query()
	normalized := make(map[string]string, len(query))
	for key, values := range query {
		if len(values) != 1 || values[0] == "" {
			return metadata, errors.New("signed URL query contains a duplicate or empty value")
		}
		lower := strings.ToLower(key)
		if _, exists := normalized[lower]; exists {
			return metadata, errors.New("signed URL query contains a case-folded duplicate")
		}
		normalized[lower] = values[0]
	}
	var expiresAt time.Time
	algorithm := ""
	if signature, hasSignature := normalized["signature"]; hasSignature && signature != "" && normalized["accesskeyid"] != "" && normalized["expires"] != "" {
		expiresUnix, parseErr := strconv.ParseInt(normalized["expires"], 10, 64)
		if parseErr != nil || strconv.FormatInt(expiresUnix, 10) != normalized["expires"] {
			return metadata, errors.New("OBS v2 expiry is invalid")
		}
		expiresAt = time.Unix(expiresUnix, 0).UTC()
		algorithm = "obs-v2"
	} else {
		for _, prefix := range []string{"x-obs-", "x-amz-"} {
			if normalized[prefix+"signature"] == "" || normalized[prefix+"credential"] == "" || normalized[prefix+"expires"] == "" || normalized[prefix+"date"] == "" {
				continue
			}
			durationSeconds, parseErr := strconv.Atoi(normalized[prefix+"expires"])
			issuedAt, dateErr := time.Parse("20060102T150405Z", normalized[prefix+"date"])
			if parseErr != nil || dateErr != nil || durationSeconds < 1 || durationSeconds > expiresSeconds {
				return metadata, errors.New("OBS v4 expiry is invalid")
			}
			expiresAt = issuedAt.UTC().Add(time.Duration(durationSeconds) * time.Second)
			algorithm = strings.TrimSuffix(prefix, "-") + "-v4"
			break
		}
	}
	if algorithm == "" || !expiresAt.After(now) || expiresAt.After(now.Add(time.Duration(expiresSeconds+60)*time.Second)) {
		return metadata, errors.New("signed URL expiry is absent, expired, or exceeds the declared TTL")
	}
	return signedOBSURLMetadata{
		EndpointHost: endpointHost,
		PathSHA256:   sha256Hex([]byte(signed.Path)),
		Algorithm:    algorithm,
		ExpiresAt:    expiresAt,
	}, nil
}

func (r *runner) verifyExternalDownloadCompletion(ctx context.Context, db *pgx.Conn, task platformTask, output dto.PlatformGenerationArtifact) (map[string]any, error) {
	if r.checkpoint == nil {
		return nil, errors.New("external download proof requires a verified checkpoint")
	}
	var completion downloadCompletion
	var sourceEvidenceRaw []byte
	found := false
	deadline := time.Now().Add(r.timeout())
	for time.Now().Before(deadline) {
		err := db.QueryRow(ctx, `
SELECT c.id,c.download_record_id,c.external_event_id,c.source::text,c.bytes_sent,c.completed_at,
       c.verification_version,c.artifact_sha256,c.expected_size_bytes,c.http_status,c.transfer_scope,
	   c.source_evidence,c.signed_event_id,c.signed_event_timestamp,c.signed_payload_sha256,c.verified_at,c.created_at
FROM download_completions AS c WHERE c.id=$1`, r.config.Platform.ExternalDownloadCompletionID).Scan(
			&completion.ID, &completion.DownloadRecordID, &completion.ExternalEventID, &completion.Source, &completion.BytesSent, &completion.CompletedAt,
			&completion.VerificationVersion, &completion.ArtifactSHA256, &completion.ExpectedSizeBytes, &completion.HTTPStatus, &completion.TransferScope,
			&sourceEvidenceRaw, &completion.SignedEventID, &completion.SignedEventTimestamp, &completion.SignedPayloadSHA256, &completion.VerifiedAt, &completion.CreatedAt,
		)
		if err == nil {
			found = true
			break
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(r.pollInterval()):
		}
	}
	if !found {
		return nil, errors.New("external download completion was not observed before timeout")
	}
	var sourceEvidence map[string]string
	if err := json.Unmarshal(sourceEvidenceRaw, &sourceEvidence); err != nil {
		return nil, errors.New("external download source evidence is invalid")
	}
	checkpointTime, _ := time.Parse(time.RFC3339Nano, r.checkpoint.CreatedAtUTC)
	if completion.ID != r.config.Platform.ExternalDownloadCompletionID || completion.Source != r.config.Platform.ExternalDownloadCompletionSource || completion.VerificationVersion != 1 || completion.ArtifactSHA256 != output.SHA256 || completion.ExpectedSizeBytes != output.SizeBytes || completion.BytesSent != output.SizeBytes || completion.HTTPStatus != http.StatusOK || completion.TransferScope != "full_body" || completion.ExternalEventID == "" || completion.ExternalEventID != completion.SignedEventID || completion.SignedPayloadSHA256 == "" || completion.VerifiedAt.IsZero() || completion.CreatedAt.Before(checkpointTime) || completion.CompletedAt.Before(checkpointTime) || completion.SignedEventTimestamp.Before(checkpointTime) {
		return nil, errors.New("external download completion does not bind the accepted artifact")
	}
	if parsed, err := uuid.Parse(completion.SignedEventID); err != nil || parsed.String() != completion.SignedEventID || !revisionPattern.MatchString("sha256:"+completion.SignedPayloadSHA256) {
		return nil, errors.New("external download signed event evidence is invalid")
	}
	record, err := loadDownloadRecordBinding(ctx, db, completion.DownloadRecordID)
	if err != nil || validateExternalDownloadRecordBinding(
		record,
		task,
		output,
		strings.TrimSpace(os.Getenv(r.config.Storage.EndpointEnv)),
		strings.TrimSpace(os.Getenv(r.config.Storage.BucketEnv)),
		checkpointTime,
		time.Now().UTC(),
	) != nil {
		return nil, errors.New("external download record has no valid immutable Gateway and storage binding")
	}
	proofBytes, err := r.readStrictHashedFile(r.config.Platform.ExternalDownloadProofPath, r.config.Platform.ExternalDownloadProofExpectedSHA256)
	if err != nil {
		return nil, errors.New("external download producer proof is unavailable")
	}
	proof, err := decodeStrictJSON[externalDownloadProof](proofBytes)
	if err != nil {
		return nil, errors.New("external download producer proof is invalid")
	}
	signature, err := r.verifyDetachedEd25519(
		proofBytes,
		r.config.Platform.ExternalDownloadProofSignaturePath,
		r.config.Platform.ExternalDownloadProofPublicKeyEnv,
		r.config.Platform.ExternalDownloadProofPublicKeyFingerprint,
		[]byte("relay-download-completion-proof.v1\n"),
	)
	if err != nil {
		return nil, errors.New("external download producer signature is invalid")
	}
	completedAt, err1 := time.Parse(time.RFC3339Nano, proof.CompletedAtUTC)
	deliveredAt, err2 := time.Parse(time.RFC3339Nano, proof.PlatformDeliveredAtUTC)
	producedAt, err3 := time.Parse(time.RFC3339Nano, proof.ProducedAtUTC)
	nonce, nonceErr := uuid.Parse(proof.Nonce)
	proofVersionID := ""
	if proof.OBSVersionID != nil {
		proofVersionID = *proof.OBSVersionID
	}
	if proof.SchemaVersion != 1 || proof.Kind != "relay.download_completion.edge_gateway.proof" || proof.CompletionID != completion.ID || proof.DownloadRecordID != completion.DownloadRecordID || proof.CompanyID != record.CompanyID || proof.TaskID != record.TaskID || proof.AssetID != record.AssetID || proof.Source != "edge_gateway" || proof.SignedEventID != completion.SignedEventID || proof.SignedPayloadSHA256 != completion.SignedPayloadSHA256 || proof.IssuanceRequestID != record.IssuanceRequestID || proof.GatewayRequestID != record.IssuanceRequestID || proof.TransferReference != record.GatewayTransferReference || len(sourceEvidence) != 2 || sourceEvidence["gateway_request_id"] != record.IssuanceRequestID || sourceEvidence["gateway_request_id"] != proof.GatewayRequestID || sourceEvidence["gateway_transfer_reference"] != record.GatewayTransferReference || sourceEvidence["gateway_transfer_reference"] != proof.TransferReference || proof.OBSBucket != record.StorageBucket || proof.OBSObjectKey != record.StorageObjectKey || proofVersionID != record.StorageVersionID || proof.HTTPStatus != http.StatusOK || proof.TransferScope != "full_body" || proof.BytesSent != output.SizeBytes || proof.ExpectedSizeBytes != output.SizeBytes || proof.ArtifactSHA256 != output.SHA256 || !safeIdentifierPattern.MatchString(proof.ProducerSubject) || err1 != nil || err2 != nil || err3 != nil || nonceErr != nil || nonce.String() != proof.Nonce {
		return nil, errors.New("external download producer proof binding is invalid")
	}
	if !completedAt.UTC().Equal(completion.CompletedAt.UTC()) || deliveredAt.Before(completedAt) || !producedAt.Equal(deliveredAt) || completedAt.After(record.GatewayExpiresAt) {
		return nil, errors.New("external download producer proof chronology mismatch")
	}
	for _, timestamp := range []time.Time{completedAt, deliveredAt, producedAt, completion.SignedEventTimestamp, completion.VerifiedAt, completion.CreatedAt} {
		if timestamp.Before(checkpointTime) || timestamp.After(time.Now().UTC().Add(5*time.Minute)) {
			return nil, errors.New("external download producer proof time is invalid")
		}
	}
	return map[string]any{
		"download_completion_id":                         completion.ID,
		"external_download_record_id":                    completion.DownloadRecordID,
		"download_completion_source":                     "edge_gateway",
		"download_completion_signed_event_id":            completion.SignedEventID,
		"download_completion_payload_sha256":             completion.SignedPayloadSHA256,
		"download_completion_producer_subject":           proof.ProducerSubject,
		"download_completion_producer_key_id":            signature.KeyID,
		"download_completion_producer_key_fingerprint":   r.config.Platform.ExternalDownloadProofPublicKeyFingerprint,
		"download_completion_proof_sha256":               r.config.Platform.ExternalDownloadProofExpectedSHA256,
		"download_completed_at_utc":                      completedAt.UTC().Format(time.RFC3339Nano),
		"download_completion_platform_delivered_at_utc":  deliveredAt.UTC().Format(time.RFC3339Nano),
		"download_completion_producer_identity_verified": true,
	}, nil
}

func (r *runner) verifyCallback(ctx context.Context, db *pgx.Conn, task platformTask) (map[string]any, error) {
	var relay model.PlatformGenerationCallbackDelivery
	if err := model.DB.Where("job_id = ? AND state = ?", task.RelayJobID, model.PlatformGenerationCallbackDelivered).Order("delivered_at DESC").First(&relay).Error; err != nil {
		return nil, err
	}
	var row callbackRow
	err := db.QueryRow(ctx, `SELECT id, relay_status, payload_sha256, request_id, occurred_at, received_at FROM relay_callback_events WHERE task_id=$1 AND relay_job_id=$2 AND id=$3`, task.ID, task.RelayJobID, relay.ID).Scan(&row.ID, &row.RelayStatus, &row.PayloadSHA256, &row.RequestID, &row.OccurredAt, &row.ReceivedAt)
	if err != nil || row.RelayStatus != "succeeded" || row.PayloadSHA256 != relay.PayloadSHA256 || relay.ResponseStatus < 200 || relay.ResponseStatus >= 300 || relay.DeliveredAt == nil {
		return nil, errors.New("callback mismatch")
	}
	r.checks["signed_callback_accepted_by_platform"] = true
	r.checks["callback_event_id_and_payload_digest_match"] = true
	return map[string]any{
		"schema_version":           1,
		"event_id":                 relay.ID,
		"relay_job_id":             task.RelayJobID,
		"task_id":                  task.ID,
		"payload_sha256":           relay.PayloadSHA256,
		"request_id":               row.RequestID,
		"relay_attempts":           relay.Attempts,
		"response_status":          relay.ResponseStatus,
		"occurred_at_utc":          row.OccurredAt.UTC().Format(time.RFC3339Nano),
		"delivered_at_utc":         relay.DeliveredAt.UTC().Format(time.RFC3339Nano),
		"platform_received_at_utc": row.ReceivedAt.UTC().Format(time.RFC3339Nano),
		"signature_verified":       true,
	}, nil
}

func (r *runner) verifyWallet(ctx context.Context, db *pgx.Conn, task platformTask) (map[string]any, error) {
	rows, err := db.Query(ctx, `SELECT id, kind::text, amount_cents, available_delta_cents, reserved_delta_cents, idempotency_key, created_at FROM ledger_entries WHERE company_id=$1 AND task_id=$2 ORDER BY created_at,id`, task.CompanyID, task.ID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var reserve, settle *ledgerRow
	ledgerCount := 0
	var availableDeltaSum, reservedDeltaSum int64
	for rows.Next() {
		var row ledgerRow
		if err := rows.Scan(&row.ID, &row.Kind, &row.AmountCents, &row.AvailableDeltaCents, &row.ReservedDeltaCents, &row.IdempotencyKey, &row.CreatedAt); err != nil {
			return nil, err
		}
		ledgerCount++
		availableDeltaSum += row.AvailableDeltaCents
		reservedDeltaSum += row.ReservedDeltaCents
		switch strings.ToLower(row.Kind) {
		case "reserve":
			copy := row
			reserve = &copy
		case "settle":
			copy := row
			settle = &copy
		}
	}
	var wallet walletRow
	if err := db.QueryRow(ctx, `SELECT available_cents,reserved_cents FROM wallet_accounts WHERE company_id=$1`, task.CompanyID).Scan(&wallet.AvailableCents, &wallet.ReservedCents); err != nil {
		return nil, err
	}
	if reserve == nil || settle == nil || ledgerCount != 2 || task.ActualCostCents == nil || task.ReservedCents != 0 || wallet.ReservedCents != 0 || reserve.AmountCents != task.QuoteCents || reserve.AvailableDeltaCents != -task.QuoteCents || reserve.ReservedDeltaCents != task.QuoteCents || settle.AmountCents != *task.ActualCostCents || settle.AvailableDeltaCents != task.QuoteCents-*task.ActualCostCents || settle.ReservedDeltaCents != -task.QuoteCents || availableDeltaSum != -*task.ActualCostCents || reservedDeltaSum != 0 {
		return nil, errors.New("wallet ledger mismatch")
	}
	r.checks["wallet_reserve_and_settle_verified"] = true
	return map[string]any{
		"schema_version":                  1,
		"company_id":                      task.CompanyID,
		"task_id":                         task.ID,
		"reservation_reference":           reserve.ID,
		"settlement_reference":            settle.ID,
		"quote_cents":                     task.QuoteCents,
		"settled_amount_cents":            *task.ActualCostCents,
		"reserve_available_delta_cents":   reserve.AvailableDeltaCents,
		"reserve_reserved_delta_cents":    reserve.ReservedDeltaCents,
		"settle_available_delta_cents":    settle.AvailableDeltaCents,
		"settle_reserved_delta_cents":     settle.ReservedDeltaCents,
		"wallet_available_cents_after":    wallet.AvailableCents,
		"wallet_reserved_cents_after":     wallet.ReservedCents,
		"task_ledger_count":               ledgerCount,
		"task_ledger_available_delta_sum": availableDeltaSum,
		"task_ledger_reserved_delta_sum":  reservedDeltaSum,
		"action":                          "settle",
		"reconciled":                      true,
	}, nil
}

func (r *runner) customerFinancialSnapshot(ctx context.Context, db *pgx.Conn, task platformTask) (customerFinancialSnapshot, error) {
	var snapshot customerFinancialSnapshot
	if err := db.QueryRow(ctx, `
SELECT status::text,quote_cents,reserved_cents,actual_cost_cents,updated_at
FROM generation_tasks
WHERE company_id=$1 AND id=$2`, task.CompanyID, task.ID).Scan(
		&snapshot.TaskStatus,
		&snapshot.TaskQuoteCents,
		&snapshot.TaskReservedCents,
		&snapshot.TaskActualCostCents,
		&snapshot.TaskUpdatedAt,
	); err != nil {
		return snapshot, err
	}
	if err := db.QueryRow(ctx, `SELECT available_cents,reserved_cents FROM wallet_accounts WHERE company_id=$1`, task.CompanyID).Scan(&snapshot.WalletAvailable, &snapshot.WalletReserved); err != nil {
		return snapshot, err
	}
	if err := db.QueryRow(ctx, `
SELECT count(*),COALESCE(sum(amount_cents),0),COALESCE(sum(available_delta_cents),0),COALESCE(sum(reserved_delta_cents),0)
FROM ledger_entries
WHERE company_id=$1 AND task_id=$2`, task.CompanyID, task.ID).Scan(
		&snapshot.LedgerCount,
		&snapshot.LedgerAmountSum,
		&snapshot.LedgerAvailableSum,
		&snapshot.LedgerReservedSum,
	); err != nil {
		return snapshot, err
	}
	rows, err := db.Query(ctx, `
SELECT id,kind::text,amount_cents,available_delta_cents,reserved_delta_cents,idempotency_key,created_at
FROM ledger_entries
WHERE company_id=$1 AND task_id=$2
ORDER BY created_at,id`, task.CompanyID, task.ID)
	if err != nil {
		return snapshot, err
	}
	defer rows.Close()
	ledgerIdentity := make([]map[string]any, 0, snapshot.LedgerCount)
	for rows.Next() {
		var row ledgerRow
		if err := rows.Scan(&row.ID, &row.Kind, &row.AmountCents, &row.AvailableDeltaCents, &row.ReservedDeltaCents, &row.IdempotencyKey, &row.CreatedAt); err != nil {
			return snapshot, err
		}
		ledgerIdentity = append(ledgerIdentity, map[string]any{
			"id":                    row.ID,
			"kind":                  row.Kind,
			"amount_cents":          row.AmountCents,
			"available_delta_cents": row.AvailableDeltaCents,
			"reserved_delta_cents":  row.ReservedDeltaCents,
			"idempotency_key":       row.IdempotencyKey,
			"created_at_utc":        row.CreatedAt.UTC().Format(time.RFC3339Nano),
		})
	}
	serialized, err := json.Marshal(ledgerIdentity)
	if err != nil {
		return snapshot, err
	}
	snapshot.LedgerRowsSHA256 = sha256Hex(serialized)
	return snapshot, rows.Err()
}

func (r *runner) verifyProviderBill(task platformTask, observation map[string]any) (providerBillEvidence, map[string]any, error) {
	if r.checkpoint == nil {
		return providerBillEvidence{}, nil, errors.New("bill approval requires create checkpoint")
	}
	bytesValue, err := r.readStrictHashedFile(r.config.Provider.BillEvidencePath, r.config.Provider.BillEvidenceExpectedSHA256)
	if err != nil {
		return providerBillEvidence{}, nil, err
	}
	actual := r.config.Provider.BillEvidenceExpectedSHA256
	sourceDocumentHash, err := sha256File(r.resolveConfigPath(r.config.Provider.SourceDocumentPath))
	if err != nil {
		return providerBillEvidence{}, nil, errors.New("source document unavailable")
	}
	if sourceDocumentHash != r.config.Provider.SourceDocumentExpectedSHA256 {
		return providerBillEvidence{}, nil, errors.New("source document hash")
	}
	bill, err := decodeStrictJSON[providerBillEvidence](bytesValue)
	if err != nil {
		return providerBillEvidence{}, nil, err
	}
	job := observation["job"].(model.PlatformGenerationJob)
	route := observation["route"].(model.PlatformGenerationProviderRoute)
	occurred, err1 := time.Parse(time.RFC3339, bill.OccurredAtUTC)
	rateAt, err2 := time.Parse(time.RFC3339, bill.ContractRateEffectiveAtUTC)
	verifiedAt, err3 := time.Parse(time.RFC3339, bill.AccountConsoleVerifiedAtUTC)
	documentURL, err4 := url.Parse(bill.OfficialDocumentURL)
	validEvidence := map[string]bool{"provider_reported": true, "provider_invoice": true, "contract_rate": true}
	if bill.SchemaVersion != 2 || !strings.EqualFold(bill.ProviderName, r.config.Provider.Name) || bill.PlatformTaskID != task.ID || bill.RelayJobID != task.RelayJobID || bill.ProviderTaskReference != job.UpstreamTaskID || bill.ChannelID != route.ChannelID || !safeEvidenceText(bill.ProviderBillReference) || bill.AmountCents < 0 || bill.AmountCents > dto.PlatformChannelCostMaxAmountCents || bill.ContractCurrency != "CNY" || !safeEvidenceText(bill.BilledQuantity) || !safeEvidenceText(bill.BilledUnit) || !safeEvidenceText(bill.ContractRateSource) || !validEvidence[bill.EvidenceSource] || bill.SourceDocumentSHA256 != sourceDocumentHash || err1 != nil || err2 != nil || err3 != nil || err4 != nil || documentURL.Scheme != "https" || documentURL.User != nil || documentURL.RawQuery != "" || documentURL.Fragment != "" || (documentURL.Hostname() != "kling.ai" && documentURL.Hostname() != "app.klingai.com") {
		return providerBillEvidence{}, nil, errors.New("bill content")
	}
	validZeroReasons := map[string]bool{"free_quota": true, "promotional_credit": true, "provider_waiver": true, "included_contract": true}
	if bill.AmountCents > 0 {
		if bill.CostDisposition != "billed" || bill.ZeroCostReason != "" || bill.ZeroCostEvidenceReference != "" {
			return providerBillEvidence{}, nil, errors.New("positive cost disposition")
		}
	} else if bill.CostDisposition != "verified_zero" || !validZeroReasons[bill.ZeroCostReason] || !safeEvidenceText(bill.ZeroCostEvidenceReference) || (bill.EvidenceSource != "provider_invoice" && bill.EvidenceSource != "contract_rate") {
		return providerBillEvidence{}, nil, errors.New("zero cost requires explicit trusted proof")
	}
	checkpointTime, _ := time.Parse(time.RFC3339Nano, r.checkpoint.CreatedAtUTC)
	if occurred.Before(job.CreatedAt.Add(-5*time.Minute)) || occurred.After(time.Now().UTC().Add(5*time.Minute)) || rateAt.After(occurred.Add(5*time.Minute)) || verifiedAt.Before(checkpointTime.Add(-5*time.Second)) || verifiedAt.After(time.Now().UTC().Add(5*time.Minute)) {
		return providerBillEvidence{}, nil, errors.New("bill time binding")
	}
	approvalBytes, err := r.readStrictHashedFile(r.config.Provider.ApprovalPayloadPath, r.config.Provider.ApprovalPayloadExpectedSHA256)
	if err != nil {
		return providerBillEvidence{}, nil, errors.New("bill approval payload")
	}
	approval, err := decodeStrictJSON[providerBillApproval](approvalBytes)
	if err != nil {
		return providerBillEvidence{}, nil, errors.New("bill approval payload")
	}
	approvalSignature, err := r.verifyDetachedEd25519(
		approvalBytes,
		r.config.Provider.ApprovalSignaturePath,
		r.config.Provider.ApprovalPublicKeyEnv,
		r.config.Provider.ApprovalPublicKeyFingerprint,
		[]byte("relay-provider-bill-approval.v1\n"),
	)
	if err != nil {
		return providerBillEvidence{}, nil, errors.New("bill approval signature")
	}
	approvedAt, approvalTimeErr := time.Parse(time.RFC3339, approval.ApprovedAtUTC)
	expiresAt, expiryErr := time.Parse(time.RFC3339, approval.ExpiresAtUTC)
	nonce, nonceErr := uuid.Parse(approval.Nonce)
	if approval.SchemaVersion != 1 || approval.Kind != "relay_real_channel_provider_bill_approval" || approval.CheckpointSHA256 != r.checkpointSHA256 || approval.BillEvidenceSHA256 != actual || approval.SourceDocumentSHA256 != sourceDocumentHash || !strings.EqualFold(approval.ProviderName, bill.ProviderName) || approval.PlatformTaskID != task.ID || approval.RelayJobID != task.RelayJobID || approval.ProviderTaskReference != bill.ProviderTaskReference || approval.ProviderBillReference != bill.ProviderBillReference || approval.ChannelID != bill.ChannelID || approval.AmountCents != bill.AmountCents || approval.ContractCurrency != bill.ContractCurrency || approval.CostDisposition != bill.CostDisposition || approval.ZeroCostReason != bill.ZeroCostReason || approval.ZeroCostEvidenceReference != bill.ZeroCostEvidenceReference || approval.Decision != "approved" || !safeIdentifierPattern.MatchString(approval.ApproverSubject) || !safeIdentifierPattern.MatchString(approval.ApproverRole) || approvalTimeErr != nil || expiryErr != nil || nonceErr != nil || nonce.String() != approval.Nonce || approvedAt.Before(checkpointTime.Add(-5*time.Second)) || approvedAt.After(time.Now().UTC().Add(5*time.Minute)) || !expiresAt.After(time.Now().UTC()) || expiresAt.After(approvedAt.Add(7*24*time.Hour)) {
		return providerBillEvidence{}, nil, errors.New("bill approval binding")
	}
	r.checks["independent_provider_bill_file_hash_verified"] = true
	r.checks["original_provider_bill_or_contract_file_hash_verified"] = true
	r.checks["provider_bill_task_channel_and_time_bound"] = true
	r.checks["provider_contract_currency_and_rate_source_explicit"] = true
	r.checks["provider_bill_independent_ed25519_approval_verified"] = true
	r.checks["provider_cost_disposition_explicit"] = true
	return bill, map[string]any{
		"schema_version":                      1,
		"provider_name":                       bill.ProviderName,
		"platform_task_id":                    bill.PlatformTaskID,
		"relay_job_id":                        bill.RelayJobID,
		"provider_task_reference":             bill.ProviderTaskReference,
		"provider_bill_reference":             bill.ProviderBillReference,
		"channel_id":                          strconv.Itoa(bill.ChannelID),
		"occurred_at_utc":                     occurred.UTC().Format(time.RFC3339Nano),
		"amount_cents":                        bill.AmountCents,
		"contract_currency":                   bill.ContractCurrency,
		"billed_quantity":                     bill.BilledQuantity,
		"billed_unit":                         bill.BilledUnit,
		"contract_rate_source":                bill.ContractRateSource,
		"contract_rate_effective_at_utc":      rateAt.UTC().Format(time.RFC3339Nano),
		"evidence_source":                     bill.EvidenceSource,
		"cost_disposition":                    bill.CostDisposition,
		"zero_cost_reason":                    bill.ZeroCostReason,
		"zero_cost_evidence_reference":        bill.ZeroCostEvidenceReference,
		"source_document_sha256":              sourceDocumentHash,
		"sanitized_bill_evidence_file_sha256": actual,
		"account_console_verified_at_utc":     verifiedAt.UTC().Format(time.RFC3339Nano),
		"official_document_url":               bill.OfficialDocumentURL,
		"approval_payload_sha256":             r.config.Provider.ApprovalPayloadExpectedSHA256,
		"approval_key_id":                     approvalSignature.KeyID,
		"approval_key_fingerprint":            r.config.Provider.ApprovalPublicKeyFingerprint,
		"approval_subject":                    approval.ApproverSubject,
		"approval_role":                       approval.ApproverRole,
		"approved_at_utc":                     approvedAt.UTC().Format(time.RFC3339Nano),
		"approval_expires_at_utc":             expiresAt.UTC().Format(time.RFC3339Nano),
	}, nil
}

func safeEvidenceText(value string) bool {
	return value != "" && value == strings.TrimSpace(value) && len(value) <= 256 && !strings.ContainsAny(value, "\r\n\x00")
}

func (r *runner) enqueueAndVerifyCost(ctx context.Context, db *pgx.Conn, task platformTask, observation map[string]any, bill providerBillEvidence, financialBefore customerFinancialSnapshot) (map[string]any, error) {
	route := observation["route"].(model.PlatformGenerationProviderRoute)
	eventID := uuid.NewSHA1(uuid.NameSpaceURL, []byte("ai-video:real-channel-cost:"+task.RelayJobID)).String()
	input := dto.PlatformChannelCostInput{
		EventID:              eventID,
		AmountCents:          bill.AmountCents,
		IdempotencyKey:       "real-channel-cost-" + task.RelayJobID,
		ChannelKey:           route.RouteKey,
		ChannelType:          route.ChannelClass,
		OccurredAt:           mustTime(bill.OccurredAtUTC),
		ExternalReference:    bill.ProviderBillReference,
		CompanyID:            task.CompanyID,
		TaskID:               task.ID,
		RelayJobID:           task.RelayJobID,
		Note:                 "real-provider staging acceptance; explicit " + bill.EvidenceSource,
		EvidenceSource:       bill.EvidenceSource,
		EvidenceReference:    bill.ProviderBillReference,
		SourceDocumentSHA256: observation["source_document_sha256"].(string),
	}
	created, err := service.EnqueuePlatformChannelCost(input)
	if err != nil {
		return nil, errors.New("cost enqueue")
	}
	replayed, err := service.EnqueuePlatformChannelCost(input)
	if err != nil || replayed {
		return nil, errors.New("cost replay")
	}
	conflict := input
	if conflict.AmountCents == dto.PlatformChannelCostMaxAmountCents {
		conflict.AmountCents--
	} else {
		conflict.AmountCents++
	}
	if _, err := service.EnqueuePlatformChannelCost(conflict); !errors.Is(err, model.ErrPlatformChannelCostEventCollision) {
		return nil, errors.New("cost conflict")
	}

	deadline := time.Now().Add(r.timeout())
	var delivery model.PlatformRelayExternalDelivery
	var platformCost channelCostRow
	for time.Now().Before(deadline) {
		relayErr := model.DB.Where("event_kind = ? AND event_id = ?", model.PlatformRelayDeliveryKindChannelCost, eventID).First(&delivery).Error
		platformErr := db.QueryRow(ctx, `SELECT id,amount_cents,idempotency_key,channel_key,channel_type::text,occurred_at,external_reference,relay_event_id,relay_event_timestamp,relay_payload_sha256,created_at FROM channel_cost_entries WHERE relay_event_id=$1`, eventID).Scan(&platformCost.ID, &platformCost.AmountCents, &platformCost.IdempotencyKey, &platformCost.ChannelKey, &platformCost.ChannelType, &platformCost.OccurredAt, &platformCost.ExternalReference, &platformCost.RelayEventID, &platformCost.RelayEventTimestamp, &platformCost.RelayPayloadSHA256, &platformCost.CreatedAt)
		if relayErr == nil && platformErr == nil && delivery.State == model.PlatformRelayDeliveryDelivered {
			break
		}
		time.Sleep(r.pollInterval())
	}
	var event model.PlatformChannelCostEvent
	if err := model.DB.Where("id = ?", eventID).First(&event).Error; err != nil || event.ChannelKey != input.ChannelKey || event.ChannelType != input.ChannelType || !event.OccurredAt.UTC().Equal(input.OccurredAt.UTC()) || delivery.State != model.PlatformRelayDeliveryDelivered || delivery.DeliveredAt == nil || delivery.Attempts < 1 || platformCost.ID == "" || platformCost.AmountCents != input.AmountCents || platformCost.IdempotencyKey != input.IdempotencyKey || platformCost.ChannelKey != input.ChannelKey || platformCost.ChannelType != input.ChannelType || !platformCost.OccurredAt.UTC().Equal(input.OccurredAt.UTC()) || platformCost.ExternalReference != input.ExternalReference || platformCost.RelayEventID != eventID || platformCost.RelayEventTimestamp.IsZero() || platformCost.RelayEventTimestamp.Before(event.CreatedAt.Add(-5*time.Minute)) || platformCost.RelayEventTimestamp.After(delivery.DeliveredAt.Add(5*time.Minute)) || platformCost.RelayPayloadSHA256 != event.PayloadSHA256 {
		return nil, errors.New("cost delivery mismatch")
	}
	var relayCount, platformCount int64
	if err := model.DB.Model(&model.PlatformChannelCostEvent{}).Where("id = ? OR idempotency_key = ?", eventID, input.IdempotencyKey).Count(&relayCount).Error; err != nil || relayCount != 1 {
		return nil, errors.New("relay cost count")
	}
	if err := db.QueryRow(ctx, `SELECT count(*) FROM channel_cost_entries WHERE relay_event_id=$1 OR idempotency_key=$2`, eventID, input.IdempotencyKey).Scan(&platformCount); err != nil || platformCount != 1 {
		return nil, errors.New("platform cost count")
	}
	if err := model.DB.Exec("UPDATE platform_channel_cost_events SET note=note WHERE id = ?", eventID).Error; err == nil {
		return nil, errors.New("relay append-only guard absent")
	}
	tx, err := db.Begin(ctx)
	if err != nil {
		return nil, err
	}
	_, mutationErr := tx.Exec(ctx, `UPDATE channel_cost_entries SET note=note WHERE id=$1`, platformCost.ID)
	_ = tx.Rollback(ctx)
	if mutationErr == nil {
		return nil, errors.New("platform append-only guard absent")
	}
	financialAfter, err := r.customerFinancialSnapshot(ctx, db, task)
	if err != nil || !financialSnapshotsEqual(financialAfter, financialBefore) {
		return nil, errors.New("provider cost changed customer task wallet or ledger")
	}
	r.checks["provider_cost_created_from_explicit_bill_evidence"] = true
	r.checks["provider_cost_exact_replay_idempotent"] = true
	r.checks["provider_cost_conflict_rejected"] = true
	r.checks["provider_cost_delivered_with_signed_contract"] = true
	r.checks["relay_and_platform_cost_ledgers_append_only"] = true
	r.checks["provider_cost_did_not_change_customer_task_wallet_or_ledger"] = true
	return map[string]any{
		"schema_version":                         1,
		"relay_event_id":                         eventID,
		"relay_event_created_this_run":           created,
		"relay_payload_sha256":                   event.PayloadSHA256,
		"relay_delivery_id":                      delivery.ID,
		"relay_delivery_attempts":                delivery.Attempts,
		"relay_delivery_response_status":         delivery.ResponseStatus,
		"relay_delivered_at_utc":                 delivery.DeliveredAt.UTC().Format(time.RFC3339Nano),
		"platform_ledger_id":                     platformCost.ID,
		"idempotency_key":                        input.IdempotencyKey,
		"external_reference":                     input.ExternalReference,
		"occurred_at_utc":                        input.OccurredAt.UTC().Format(time.RFC3339Nano),
		"amount_cents":                           input.AmountCents,
		"channel_id":                             strconv.Itoa(route.ChannelID),
		"channel_key":                            route.RouteKey,
		"channel_class":                          route.ChannelClass,
		"evidence_source":                        input.EvidenceSource,
		"append_only_verified":                   true,
		"single_event_verified":                  true,
		"idempotent_replay_verified":             true,
		"idempotency_conflict_rejected_verified": true,
		"customer_financial_snapshot_sha256":     "sha256:" + sha256Hex(mustJSON(financialAfter)),
	}, nil
}

func (r *runner) realRecord(task platformTask, observation map[string]any, bill providerBillEvidence, obsEvidence, callbackEvidence, walletEvidence, costEvidence map[string]any, files []evidenceFile) map[string]any {
	route := observation["route"].(model.PlatformGenerationProviderRoute)
	record := map[string]any{
		"mode":          r.config.Mode,
		"realProvider":  true,
		"executedAtUtc": time.Now().UTC().Format(time.RFC3339Nano),
		"route": map[string]any{
			"routeId":        strconv.FormatInt(route.ID, 10),
			"providerName":   route.ProviderName,
			"channelId":      strconv.Itoa(route.ChannelID),
			"channelClass":   route.ChannelClass,
			"accountId":      route.AccountID,
			"keyFingerprint": "sha256:" + route.KeyFingerprint,
		},
		"provider": map[string]any{
			"taskReference": bill.ProviderTaskReference,
			"billReference": bill.ProviderBillReference,
		},
		"obs": map[string]any{
			"bucket":    obsEvidence["bucket"],
			"objectKey": obsEvidence["object_key"],
			"sha256":    obsEvidence["sha256"],
			"head": map[string]any{
				"verified":     true,
				"etag":         obsEvidence["etag"],
				"sizeBytes":    obsEvidence["size_bytes"],
				"contentType":  obsEvidence["content_type"],
				"checkedAtUtc": obsEvidence["checked_at_utc"],
			},
		},
		"callback": map[string]any{
			"eventId":           callbackEvidence["event_id"],
			"signatureVerified": true,
			"deliveredAtUtc":    callbackEvidence["delivered_at_utc"],
		},
		"platformWallet": map[string]any{
			"taskId":               task.ID,
			"reservationReference": walletEvidence["reservation_reference"],
			"settlementReference":  walletEvidence["settlement_reference"],
			"action":               "settle",
			"amountMinor":          walletEvidence["settled_amount_cents"],
			"reconciled":           true,
		},
		"providerCost": map[string]any{
			"ledgerId":                            costEvidence["platform_ledger_id"],
			"idempotencyKey":                      costEvidence["idempotency_key"],
			"externalReference":                   costEvidence["external_reference"],
			"occurredAtUtc":                       costEvidence["occurred_at_utc"],
			"amountMinor":                         costEvidence["amount_cents"],
			"channelId":                           strconv.Itoa(route.ChannelID),
			"channelClass":                        route.ChannelClass,
			"appendOnlyVerified":                  true,
			"singleEventVerified":                 true,
			"idempotentReplayVerified":            true,
			"idempotencyConflictRejectedVerified": true,
		},
		"evidenceFiles": files,
	}
	if r.runtimeBuildIdentity != nil {
		record["candidateBuild"] = *r.runtimeBuildIdentity
	}
	return record
}

func (r *runner) doJSON(ctx context.Context, method, endpoint string, payload any, headers http.Header, expected int, target any) error {
	var body io.Reader
	if payload != nil {
		serialized, err := json.Marshal(payload)
		if err != nil {
			return err
		}
		body = bytes.NewReader(serialized)
	}
	request, err := http.NewRequestWithContext(ctx, method, endpoint, body)
	if err != nil {
		return err
	}
	request.Header = headers.Clone()
	response, err := r.httpClient.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	limited, err := io.ReadAll(io.LimitReader(response.Body, 4*1024*1024))
	if err != nil || response.StatusCode != expected {
		return errors.New("unexpected HTTP response")
	}
	if target != nil && len(limited) > 0 {
		if err := json.Unmarshal(limited, target); err != nil {
			return err
		}
	}
	return nil
}

func isCompleteArtifactHTTPResponse(response *http.Response, expectedSize int64) bool {
	if response == nil || response.StatusCode != http.StatusOK {
		return false
	}
	if strings.TrimSpace(response.Header.Get("Content-Range")) != "" {
		return false
	}
	return response.ContentLength < 0 || response.ContentLength == expectedSize
}

func (r *runner) doRawJSON(ctx context.Context, method, endpoint string, rawBody []byte, headers http.Header, expected int, target any) error {
	request, err := http.NewRequestWithContext(ctx, method, endpoint, bytes.NewReader(rawBody))
	if err != nil {
		return err
	}
	request.Header = headers.Clone()
	response, err := r.httpClient.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	limited, err := io.ReadAll(io.LimitReader(response.Body, 4*1024*1024))
	if err != nil || response.StatusCode != expected {
		return errors.New("unexpected HTTP response")
	}
	if target != nil && len(limited) > 0 {
		if err := json.Unmarshal(limited, target); err != nil {
			return err
		}
	}
	return nil
}

func newAcceptanceHTTPClient() *http.Client {
	return &http.Client{
		Timeout: 90 * time.Second,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
}

func (r *runner) newOBSClient() (*obs.ObsClient, error) {
	accessKey := os.Getenv(r.config.Storage.AccessKeyEnv)
	secretKey := os.Getenv(r.config.Storage.SecretKeyEnv)
	endpoint := os.Getenv(r.config.Storage.EndpointEnv)
	if name := r.config.Storage.SecurityTokenEnv; name != "" && os.Getenv(name) != "" {
		return obs.New(accessKey, secretKey, endpoint,
			obs.WithSecurityToken(os.Getenv(name)),
			obs.WithSslVerify(true),
			obs.WithConnectTimeout(10),
			obs.WithSocketTimeout(60),
			obs.WithHeaderTimeout(30),
			obs.WithMaxRetryCount(2),
			obs.WithProxyFromEnv(false),
		)
	}
	return obs.New(accessKey, secretKey, endpoint,
		obs.WithSslVerify(true),
		obs.WithConnectTimeout(10),
		obs.WithSocketTimeout(60),
		obs.WithHeaderTimeout(30),
		obs.WithMaxRetryCount(2),
		obs.WithProxyFromEnv(false),
	)
}

func (r *runner) writeEvidence(label string, value any) (evidenceFile, error) {
	if err := r.ensureOutputDirectory(); err != nil {
		return evidenceFile{}, err
	}
	name := label + ".json"
	path := filepath.Join(r.outputDir, name)
	serialized, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return evidenceFile{}, err
	}
	serialized = append(serialized, '\n')
	if err := r.scanSecretFree(serialized); err != nil {
		return evidenceFile{}, err
	}
	if err := writeCreateOnly(path, serialized); err != nil {
		return evidenceFile{}, err
	}
	relative, err := filepath.Rel(r.configDir, path)
	if err != nil {
		return evidenceFile{}, err
	}
	return evidenceFile{Label: label, Path: filepath.ToSlash(relative), ExpectedSHA256: "sha256:" + sha256Hex(serialized)}, nil
}

func (r *runner) writeCreatePhaseEvidence(providerTask map[string]any, checkpoint createCheckpoint) ([]evidenceFile, error) {
	providerFile, err := r.writeEvidence("provider_task", providerTask)
	if err != nil {
		return nil, err
	}
	checkpointFile, err := r.writeEvidence("create_checkpoint", checkpoint)
	if err != nil {
		return nil, err
	}
	return []evidenceFile{providerFile, checkpointFile}, nil
}

func providerTaskEvidenceFromObservation(observation map[string]any) (map[string]any, error) {
	evidence, ok := observation["provider_task"].(map[string]any)
	if !ok || len(evidence) == 0 {
		return nil, errors.New("provider task evidence shape is invalid")
	}
	providerTaskReference, taskOK := evidence["provider_task_reference"].(string)
	channelID, channelOK := evidence["channel_id"].(string)
	if !taskOK || !channelOK || strings.TrimSpace(providerTaskReference) == "" || strings.TrimSpace(channelID) == "" {
		return nil, errors.New("provider task evidence identity is incomplete")
	}
	return evidence, nil
}

func (r *runner) finish(report runReport) string {
	report.Checks = r.checks
	if err := r.ensureOutputDirectory(); err != nil {
		fmt.Fprintln(os.Stderr, "acceptance report directory could not be created")
		return "BLOCKED"
	}
	serialized, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		fmt.Fprintln(os.Stderr, "acceptance report could not be serialized")
		return "BLOCKED"
	}
	serialized = append(serialized, '\n')
	if err := r.scanSecretFree(serialized); err != nil {
		report = runReport{
			SchemaVersion: reportSchemaVersion,
			Kind:          "relay_real_channel_acceptance",
			Status:        "BLOCKED",
			RunID:         r.runID,
			CreatedAtUTC:  time.Now().UTC().Format(time.RFC3339Nano),
			Phase:         r.phase,
			Environment:   "redacted",
			Reason:        "output_secret_scan_failed",
			Checks:        map[string]bool{"secret_values_excluded_from_report": false},
			Documentation: map[string]string{},
		}
		serialized, _ = json.MarshalIndent(report, "", "  ")
		serialized = append(serialized, '\n')
	}
	path := filepath.Join(r.outputDir, "report.json")
	if err := writeCreateOnly(path, serialized); err != nil {
		fmt.Fprintln(os.Stderr, "acceptance report is create-only and already exists")
		return "BLOCKED"
	}
	fmt.Printf("real-channel acceptance: %s (%s)\n", report.Status, path)
	return report.Status
}

func (r *runner) scanSecretFree(serialized []byte) error {
	textValue := string(serialized)
	lower := strings.ToLower(textValue)
	for _, marker := range []string{
		"-----begin private key-----", "-----begin rsa private key-----", "bearer ",
		"postgres://", "postgresql://", "accesskeyid=", "x-amz-signature", "x-obs-signature", "signature=",
	} {
		if strings.Contains(lower, marker) {
			return errors.New("credential-like material in evidence")
		}
	}
	for _, secret := range r.secretCorpus() {
		if secret != "" && strings.Contains(textValue, secret) {
			return errors.New("configured secret in evidence")
		}
	}
	return nil
}

func (r *runner) secretCorpus() []string {
	names := []string{
		r.config.Platform.BearerTokenEnv,
		r.config.Platform.DatabaseDSNEnv,
		r.config.Runtime.RelayDatabaseDSNEnv,
		r.config.Runtime.RelayRuntimeIdentityTokenEnv,
		r.config.Provider.AccessKeyEnv,
		r.config.Provider.SecretKeyEnv,
		r.config.Storage.AccessKeyEnv,
		r.config.Storage.SecretKeyEnv,
		r.config.Storage.SecurityTokenEnv,
	}
	seen := map[string]struct{}{}
	values := make([]string, 0, len(names)*5)
	for _, name := range names {
		if name == "" {
			continue
		}
		value := os.Getenv(name)
		if len(value) < 4 {
			continue
		}
		variants := []string{value, url.QueryEscape(value), base64.StdEncoding.EncodeToString([]byte(value)), base64.RawStdEncoding.EncodeToString([]byte(value))}
		if encoded, err := json.Marshal(value); err == nil && len(encoded) >= 2 {
			variants = append(variants, string(encoded[1:len(encoded)-1]))
		}
		for _, variant := range variants {
			if variant == "" {
				continue
			}
			if _, exists := seen[variant]; exists {
				continue
			}
			seen[variant] = struct{}{}
			values = append(values, variant)
		}
	}
	return values
}

func (r *runner) ensureOutputDirectory() error {
	if r.outputDir != "" {
		return nil
	}
	root := r.resolveConfigPath(r.config.Runtime.OutputDirectory)
	stamp := time.Now().UTC().Format("20060102T150405Z")
	r.outputDir = filepath.Join(root, "real-channel-"+stamp+"-"+r.runID)
	return os.MkdirAll(r.outputDir, 0o700)
}

func (r *runner) resolveConfigPath(value string) string {
	if filepath.IsAbs(value) {
		return value
	}
	return filepath.Join(r.configDir, value)
}

func (r *runner) pollInterval() time.Duration {
	seconds := r.config.Runtime.PollIntervalSeconds
	if seconds == 0 {
		seconds = 5
	}
	return time.Duration(seconds) * time.Second
}

func (r *runner) timeout() time.Duration {
	seconds := r.config.Runtime.TimeoutSeconds
	if seconds == 0 {
		seconds = 1800
	}
	return time.Duration(seconds) * time.Second
}

func readStrictJSON[T any](path string) (T, error) {
	var zero T
	value, err := os.ReadFile(path)
	if err != nil {
		return zero, err
	}
	return decodeStrictJSON[T](value)
}

func decodeStrictJSON[T any](value []byte) (T, error) {
	var target T
	decoder := json.NewDecoder(bytes.NewReader(value))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&target); err != nil {
		return target, err
	}
	if decoder.Decode(&struct{}{}) != io.EOF {
		return target, errors.New("trailing JSON")
	}
	return target, nil
}

func (r *runner) readStrictHashedFile(configPath, expectedSHA256 string) ([]byte, error) {
	pathValue := r.resolveConfigPath(configPath)
	info, err := os.Lstat(pathValue)
	if err != nil || !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 || info.Size() < 1 || info.Size() > 16*1024*1024 {
		return nil, errors.New("evidence input must be a bounded regular file")
	}
	value, err := os.ReadFile(pathValue)
	if err != nil || "sha256:"+sha256Hex(value) != expectedSHA256 {
		return nil, errors.New("evidence input hash mismatch")
	}
	return value, nil
}

func approvalPublicKeyFromEnvironment(name string) (ed25519.PublicKey, error) {
	encoded := os.Getenv(name)
	if encoded == "" || encoded != strings.TrimSpace(encoded) {
		return nil, errors.New("public key is missing or not normalized")
	}
	decoded, err := base64.StdEncoding.Strict().DecodeString(encoded)
	if err != nil || len(decoded) != ed25519.PublicKeySize {
		return nil, errors.New("public key is invalid")
	}
	return ed25519.PublicKey(decoded), nil
}

func (r *runner) verifyDetachedEd25519(payload []byte, signatureConfigPath, publicKeyEnv, expectedKeyFingerprint string, domain []byte) (providerBillApprovalSignature, error) {
	var zero providerBillApprovalSignature
	signaturePath := r.resolveConfigPath(signatureConfigPath)
	info, err := os.Lstat(signaturePath)
	if err != nil || !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 || info.Size() < 1 || info.Size() > 16*1024 {
		return zero, errors.New("signature envelope is not a bounded regular file")
	}
	signatureBytes, err := os.ReadFile(signaturePath)
	if err != nil {
		return zero, err
	}
	envelope, err := decodeStrictJSON[providerBillApprovalSignature](signatureBytes)
	if err != nil || envelope.SchemaVersion != 1 || envelope.Algorithm != "Ed25519" || !safeIdentifierPattern.MatchString(envelope.KeyID) || envelope.PayloadSHA256 != "sha256:"+sha256Hex(payload) {
		return zero, errors.New("signature envelope is invalid")
	}
	publicKey, err := approvalPublicKeyFromEnvironment(publicKeyEnv)
	if err != nil || "sha256:"+sha256Hex(publicKey) != expectedKeyFingerprint {
		return zero, errors.New("trusted public key fingerprint mismatch")
	}
	signature, err := base64.StdEncoding.Strict().DecodeString(envelope.SignatureBase64)
	if err != nil || len(signature) != ed25519.SignatureSize {
		return zero, errors.New("signature is invalid")
	}
	signingInput := make([]byte, 0, len(domain)+len(payload))
	signingInput = append(signingInput, domain...)
	signingInput = append(signingInput, payload...)
	if !ed25519.Verify(publicKey, signingInput, signature) {
		return zero, errors.New("signature verification failed")
	}
	return envelope, nil
}

func isKnownPlaceholder(value string) bool {
	lower := strings.ToLower(strings.TrimSpace(value))
	return strings.Contains(lower, "replace-with") || strings.Contains(lower, "change-me") || strings.Contains(lower, "example") || strings.Contains(lower, "placeholder")
}

func writeCreateOnly(path string, value []byte) error {
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	defer file.Close()
	if _, err := file.Write(value); err != nil {
		return err
	}
	return file.Sync()
}

func isLoopbackHost(host string) bool {
	return host == "localhost" || host == "127.0.0.1" || host == "::1"
}

func anonymousOBSAccessDenied(status int) bool {
	return status == http.StatusUnauthorized || status == http.StatusForbidden || status == http.StatusNotFound
}

func sha256Hex(value []byte) string {
	digest := sha256.Sum256(value)
	return hex.EncodeToString(digest[:])
}

func sha256File(path string) (string, error) {
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 || info.Size() < 1 || info.Size() > 16*1024*1024 {
		return "", errors.New("hashed evidence source must be a bounded regular file")
	}
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	hasher := sha256.New()
	if _, err := io.Copy(hasher, file); err != nil {
		return "", err
	}
	return "sha256:" + hex.EncodeToString(hasher.Sum(nil)), nil
}

func mustJSON(value any) []byte {
	serialized, _ := json.Marshal(value)
	return serialized
}

func financialSnapshotsEqual(left, right customerFinancialSnapshot) bool {
	return left.TaskStatus == right.TaskStatus &&
		left.TaskQuoteCents == right.TaskQuoteCents &&
		left.TaskReservedCents == right.TaskReservedCents &&
		left.TaskActualCostCents == right.TaskActualCostCents &&
		left.TaskUpdatedAt.Equal(right.TaskUpdatedAt) &&
		left.WalletAvailable == right.WalletAvailable &&
		left.WalletReserved == right.WalletReserved &&
		left.LedgerCount == right.LedgerCount &&
		left.LedgerAmountSum == right.LedgerAmountSum &&
		left.LedgerAvailableSum == right.LedgerAvailableSum &&
		left.LedgerReservedSum == right.LedgerReservedSum &&
		left.LedgerRowsSHA256 == right.LedgerRowsSHA256
}

func metadataValue(metadata map[string]string, name string) string {
	for key, value := range metadata {
		normalized := strings.TrimPrefix(strings.TrimPrefix(strings.ToLower(strings.TrimSpace(key)), "x-obs-meta-"), "x-amz-meta-")
		if normalized == name {
			return value
		}
	}
	return ""
}

func mustTime(value string) time.Time {
	parsed, _ := time.Parse(time.RFC3339, value)
	return parsed.UTC()
}
