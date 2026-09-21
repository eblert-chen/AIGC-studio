package model

import "time"

// platformChannelControlOperationArtifactV5 is the immutable model-artifact
// projection of the deployed v5 receipt table. It is never used for runtime
// reads or writes. Keeping it separate from the live ORM prevents v6 lifecycle
// columns from silently reinterpreting the frozen v5 model identity.
type platformChannelControlOperationArtifactV5 struct {
	ID                     string     `json:"id" gorm:"type:varchar(36);primaryKey"`
	TenantID               string     `json:"tenant_id" gorm:"type:varchar(36);not null;uniqueIndex:ux_platform_channel_control_operation,priority:1"`
	OperationID            string     `json:"operation_id" gorm:"type:varchar(128);not null;uniqueIndex:ux_platform_channel_control_operation,priority:2"`
	ChannelID              int        `json:"channel_id" gorm:"not null;index:idx_platform_channel_control_channel"`
	Kind                   string     `json:"kind" gorm:"type:varchar(16);not null"`
	State                  string     `json:"state" gorm:"type:varchar(16);not null;index"`
	RequestID              string     `json:"request_id" gorm:"type:varchar(80);not null"`
	Actor                  string     `json:"actor" gorm:"type:varchar(128);not null"`
	Reason                 string     `json:"reason" gorm:"type:varchar(240);not null"`
	IntentSHA256           string     `json:"intent_sha256" gorm:"type:char(64);not null"`
	IntentJSON             string     `json:"-" gorm:"type:text;not null"`
	IntentExpectedRevision string     `json:"intent_expected_revision,omitempty" gorm:"type:varchar(72);not null;default:''"`
	IntentTargetStatus     string     `json:"intent_target_status,omitempty" gorm:"type:varchar(24);not null;default:''"`
	PreviousRevision       string     `json:"previous_revision,omitempty" gorm:"type:varchar(72);not null;default:''"`
	ResultRevision         string     `json:"result_revision,omitempty" gorm:"type:varchar(72);not null;default:''"`
	ResultSuccess          *bool      `json:"result_success,omitempty"`
	ResultResponseMS       *int64     `json:"result_response_ms,omitempty"`
	ResultErrorCode        string     `json:"result_error_code,omitempty" gorm:"type:varchar(64);not null;default:''"`
	ResultPreviousStatus   string     `json:"result_previous_status,omitempty" gorm:"type:varchar(24);not null;default:''"`
	ResultCurrentStatus    string     `json:"result_current_status,omitempty" gorm:"type:varchar(24);not null;default:''"`
	ResultChanged          *bool      `json:"result_changed,omitempty"`
	CreatedAt              time.Time  `json:"created_at" gorm:"not null;index"`
	CompletedAt            *time.Time `json:"completed_at,omitempty"`
}

func (platformChannelControlOperationArtifactV5) TableName() string {
	return "platform_channel_control_operations"
}
