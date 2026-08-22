package dto

import "time"

type PlatformGenerationReconciliationItem struct {
	APIVersion                string     `json:"api_version"`
	SchemaVersion             int        `json:"schema_version"`
	Object                    string     `json:"object"`
	JobID                     string     `json:"job_id"`
	TenantID                  string     `json:"tenant_id"`
	ClientReferenceID         *string    `json:"client_reference_id"`
	Model                     string     `json:"model"`
	Mode                      string     `json:"mode"`
	Status                    string     `json:"status"`
	ProviderRouteID           int64      `json:"provider_route_id"`
	ProviderRouteKey          string     `json:"provider_route_key"`
	ProviderName              string     `json:"provider_name"`
	ProviderAccountID         string     `json:"provider_account_id"`
	ProviderChannelID         int        `json:"provider_channel_id"`
	ProviderKeyIndex          int        `json:"provider_key_index"`
	ProviderChannelClass      string     `json:"provider_channel_class"`
	ProviderUpstreamModel     string     `json:"provider_upstream_model"`
	ProviderSubmissionAttempt int        `json:"provider_submission_attempt"`
	UnknownAt                 *time.Time `json:"unknown_at"`
	ReconciliationToken       string     `json:"reconciliation_token"`
	ErrorCode                 string     `json:"error_code"`
	ErrorMessage              string     `json:"error_message"`
	CreatedAt                 time.Time  `json:"created_at"`
	UpdatedAt                 time.Time  `json:"updated_at"`
}

type PlatformGenerationReconciliationPage struct {
	APIVersion    string                                 `json:"api_version"`
	SchemaVersion int                                    `json:"schema_version"`
	Object        string                                 `json:"object"`
	Data          []PlatformGenerationReconciliationItem `json:"data"`
	Page          int                                    `json:"page"`
	PageSize      int                                    `json:"page_size"`
	Total         int64                                  `json:"total"`
}

type PlatformGenerationReconciliationRequest struct {
	OperationID                 string `json:"operation_id"`
	TenantID                    string `json:"tenant_id"`
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
}

// PlatformGenerationReconciliationResult is the durable receipt for one
// manual unknown-submission decision. Unlike the discovery resource, it
// remains readable after the generation leaves reconciliation_required.
type PlatformGenerationReconciliationResult struct {
	APIVersion                  string    `json:"api_version"`
	SchemaVersion               int       `json:"schema_version"`
	Object                      string    `json:"object"`
	EventID                     string    `json:"event_id"`
	OperationID                 string    `json:"operation_id"`
	RequestID                   string    `json:"request_id"`
	TenantID                    string    `json:"tenant_id"`
	JobID                       string    `json:"job_id"`
	Outcome                     string    `json:"outcome"`
	UpstreamTaskID              string    `json:"upstream_task_id"`
	ExpectedRouteID             int64     `json:"expected_route_id"`
	ExpectedSubmissionAttempt   int       `json:"expected_submission_attempt"`
	ExpectedReconciliationToken string    `json:"expected_reconciliation_token"`
	VerificationReference       string    `json:"verification_reference"`
	ApprovedBy                  string    `json:"approved_by"`
	ApprovalReason              string    `json:"approval_reason"`
	ApprovalKeyID               string    `json:"approval_key_id"`
	ApprovalSignature           string    `json:"approval_signature"`
	ResolvedStatus              string    `json:"resolved_status"`
	CurrentStatus               string    `json:"current_status"`
	PayloadSHA256               string    `json:"payload_sha256"`
	ResolvedAt                  time.Time `json:"resolved_at"`
}

type PlatformGenerationCallbackDeliveryItem struct {
	APIVersion        string                                      `json:"api_version"`
	SchemaVersion     int                                         `json:"schema_version"`
	Object            string                                      `json:"object"`
	EventID           string                                      `json:"event_id"`
	TenantID          string                                      `json:"tenant_id"`
	JobID             string                                      `json:"job_id"`
	SourceClientID    string                                      `json:"source_client_id"`
	OriginalRequestID string                                      `json:"original_request_id"`
	PayloadSHA256     string                                      `json:"payload_sha256"`
	CallbackURLSHA256 string                                      `json:"callback_url_sha256"`
	State             string                                      `json:"state"`
	Attempts          int                                         `json:"attempts"`
	MaxAttempts       int                                         `json:"max_attempts"`
	AvailableAt       time.Time                                   `json:"available_at"`
	ResponseStatus    int                                         `json:"response_status"`
	LastError         string                                      `json:"last_error"`
	DeliveredAt       *time.Time                                  `json:"delivered_at"`
	DeadLetteredAt    *time.Time                                  `json:"dead_lettered_at"`
	CreatedAt         time.Time                                   `json:"created_at"`
	UpdatedAt         time.Time                                   `json:"updated_at"`
	Redrives          []PlatformGenerationCallbackRedriveEvidence `json:"redrives"`
}

type PlatformGenerationCallbackDeliveryPage struct {
	APIVersion    string                                   `json:"api_version"`
	SchemaVersion int                                      `json:"schema_version"`
	Object        string                                   `json:"object"`
	Data          []PlatformGenerationCallbackDeliveryItem `json:"data"`
	Page          int                                      `json:"page"`
	PageSize      int                                      `json:"page_size"`
	Total         int64                                    `json:"total"`
}

type PlatformGenerationCallbackRedriveRequest struct {
	OperationID string `json:"operation_id"`
	TenantID    string `json:"tenant_id"`
	Actor       string `json:"actor"`
	Reason      string `json:"reason"`
}

type PlatformGenerationCallbackRedriveEvidence struct {
	EventID                   string     `json:"event_id"`
	OperationID               string     `json:"operation_id"`
	RequestID                 string     `json:"request_id"`
	Actor                     string     `json:"actor"`
	Reason                    string     `json:"reason"`
	PreviousState             string     `json:"previous_state"`
	PreviousAttempts          int        `json:"previous_attempts"`
	PreviousMaxAttempts       int        `json:"previous_max_attempts"`
	PreviousResponseStatus    int        `json:"previous_response_status"`
	PreviousLastError         string     `json:"previous_last_error"`
	PreviousDeadLetteredAt    *time.Time `json:"previous_dead_lettered_at"`
	CallbackURLSHA256         string     `json:"callback_url_sha256"`
	PayloadSHA256             string     `json:"payload_sha256"`
	OriginalCallbackRequestID string     `json:"original_callback_request_id"`
	ResultState               string     `json:"result_state"`
	ReceiptSHA256             string     `json:"receipt_sha256"`
	RedrivenAt                time.Time  `json:"redriven_at"`
}

type PlatformGenerationCallbackRedriveResult struct {
	APIVersion      string                                    `json:"api_version"`
	SchemaVersion   int                                       `json:"schema_version"`
	Object          string                                    `json:"object"`
	DeliveryEventID string                                    `json:"delivery_event_id"`
	TenantID        string                                    `json:"tenant_id"`
	CurrentState    string                                    `json:"current_state"`
	Evidence        PlatformGenerationCallbackRedriveEvidence `json:"evidence"`
}
