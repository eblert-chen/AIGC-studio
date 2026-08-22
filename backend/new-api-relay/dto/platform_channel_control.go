package dto

import "time"

type PlatformChannelControlCredentialSummary struct {
	Configured bool `json:"configured"`
	KeyCount   int  `json:"key_count"`
}

type PlatformChannelControlChannel struct {
	ID               int                                     `json:"id"`
	Name             string                                  `json:"name"`
	Type             int                                     `json:"type"`
	TypeLabel        string                                  `json:"type_label"`
	Status           string                                  `json:"status"`
	TestSupported    bool                                    `json:"test_supported"`
	ConfiguredModels []string                                `json:"configured_models"`
	TestModel        *string                                 `json:"test_model"`
	Weight           uint                                    `json:"weight"`
	Priority         int64                                   `json:"priority"`
	AutoBan          bool                                    `json:"auto_ban"`
	Tag              string                                  `json:"tag"`
	CreatedAt        time.Time                               `json:"created_at"`
	LastTestedAt     *time.Time                              `json:"last_tested_at"`
	ResponseTimeMS   *int                                    `json:"response_time_ms"`
	Credential       PlatformChannelControlCredentialSummary `json:"credential"`
	Revision         string                                  `json:"revision"`
}

type PlatformChannelControlChannelPage struct {
	APIVersion    string                          `json:"api_version"`
	SchemaVersion int                             `json:"schema_version"`
	Object        string                          `json:"object"`
	Data          []PlatformChannelControlChannel `json:"data"`
	Page          int                             `json:"page"`
	PageSize      int                             `json:"page_size"`
	Total         int64                           `json:"total"`
}

type PlatformChannelControlTestRequest struct {
	OperationID string `json:"operation_id"`
	TenantID    string `json:"tenant_id"`
	Actor       string `json:"actor"`
	Reason      string `json:"reason"`
}

type PlatformChannelControlStatusRequest struct {
	OperationID      string `json:"operation_id"`
	TenantID         string `json:"tenant_id"`
	Actor            string `json:"actor"`
	Reason           string `json:"reason"`
	ExpectedRevision string `json:"expected_revision"`
	TargetStatus     string `json:"target_status"`
}

type PlatformChannelControlOperationResult struct {
	Success        *bool  `json:"success,omitempty"`
	ResponseTimeMS *int64 `json:"response_time_ms,omitempty"`
	ErrorCode      string `json:"error_code,omitempty"`
	PreviousStatus string `json:"previous_status,omitempty"`
	CurrentStatus  string `json:"current_status,omitempty"`
	Changed        *bool  `json:"changed,omitempty"`
}

type PlatformChannelControlOperation struct {
	APIVersion       string                                 `json:"api_version"`
	SchemaVersion    int                                    `json:"schema_version"`
	Object           string                                 `json:"object"`
	OperationID      string                                 `json:"operation_id"`
	TenantID         string                                 `json:"tenant_id"`
	ChannelID        int                                    `json:"channel_id"`
	Kind             string                                 `json:"kind"`
	State            string                                 `json:"state"`
	Actor            string                                 `json:"actor"`
	Reason           string                                 `json:"reason"`
	RequestID        string                                 `json:"request_id"`
	IntentSHA256     string                                 `json:"intent_sha256"`
	ExpectedRevision string                                 `json:"expected_revision,omitempty"`
	TargetStatus     string                                 `json:"target_status,omitempty"`
	PreviousRevision string                                 `json:"previous_revision,omitempty"`
	ResultRevision   string                                 `json:"result_revision,omitempty"`
	Result           *PlatformChannelControlOperationResult `json:"result,omitempty"`
	CreatedAt        time.Time                              `json:"created_at"`
	CompletedAt      *time.Time                             `json:"completed_at,omitempty"`
	IdempotentReplay bool                                   `json:"idempotent_replay"`
}
