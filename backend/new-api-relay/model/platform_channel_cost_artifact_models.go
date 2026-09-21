package model

import "time"

// platformChannelCostEventArtifactV6 is the immutable model-artifact
// projection shared by the frozen v1-v6 releases. It is never used for
// runtime reads or writes. Keeping it separate from the live ORM prevents a
// later channel-cost field from reinterpreting historical model identities.
type platformChannelCostEventArtifactV6 struct {
	ID                   string    `json:"id" gorm:"type:varchar(36);primaryKey"`
	AmountCents          int64     `json:"amount_cents" gorm:"not null"`
	IdempotencyKey       string    `json:"idempotency_key" gorm:"type:varchar(160);not null;uniqueIndex"`
	ChannelKey           string    `json:"channel_key" gorm:"type:varchar(120);not null;index"`
	ChannelType          string    `json:"channel_type" gorm:"type:varchar(32);not null"`
	OccurredAt           time.Time `json:"occurred_at" gorm:"not null;index"`
	ExternalReference    string    `json:"external_reference" gorm:"type:varchar(240);not null"`
	CompanyID            string    `json:"company_id" gorm:"type:varchar(64);index"`
	TaskID               string    `json:"task_id" gorm:"type:varchar(64);index"`
	RelayJobID           string    `json:"relay_job_id" gorm:"type:varchar(36);index"`
	Note                 string    `json:"note" gorm:"type:varchar(240);not null"`
	EvidenceSource       string    `json:"evidence_source" gorm:"type:varchar(32);not null"`
	EvidenceReference    string    `json:"evidence_reference" gorm:"type:varchar(240);not null"`
	SourceDocumentSHA256 string    `json:"source_document_sha256" gorm:"type:varchar(64);not null"`
	PayloadJSON          string    `json:"-" gorm:"type:text;not null"`
	PayloadSHA256        string    `json:"payload_sha256" gorm:"type:char(64);not null"`
	CreatedAt            time.Time `json:"created_at"`
}

func (platformChannelCostEventArtifactV6) TableName() string {
	return "platform_channel_cost_events"
}

// platformChannelCostEventArtifactV7 freezes the v7 model identity without
// making future live ORM growth part of the already released v7 artifact.
// The column itself remains owned by the explicit v7 migration.
type platformChannelCostEventArtifactV7 struct {
	ID                   string    `json:"id" gorm:"type:varchar(36);primaryKey"`
	AmountCents          int64     `json:"amount_cents" gorm:"not null"`
	IdempotencyKey       string    `json:"idempotency_key" gorm:"type:varchar(160);not null;uniqueIndex"`
	ChannelKey           string    `json:"channel_key" gorm:"type:varchar(120);not null;index"`
	ChannelType          string    `json:"channel_type" gorm:"type:varchar(32);not null"`
	OccurredAt           time.Time `json:"occurred_at" gorm:"not null;index"`
	ExternalReference    string    `json:"external_reference" gorm:"type:varchar(240);not null"`
	CompanyID            string    `json:"company_id" gorm:"type:varchar(64);index"`
	PersonalWorkspaceID  string    `json:"personal_workspace_id" gorm:"type:varchar(64);-:migration"`
	TaskID               string    `json:"task_id" gorm:"type:varchar(64);index"`
	RelayJobID           string    `json:"relay_job_id" gorm:"type:varchar(36);index"`
	Note                 string    `json:"note" gorm:"type:varchar(240);not null"`
	EvidenceSource       string    `json:"evidence_source" gorm:"type:varchar(32);not null"`
	EvidenceReference    string    `json:"evidence_reference" gorm:"type:varchar(240);not null"`
	SourceDocumentSHA256 string    `json:"source_document_sha256" gorm:"type:varchar(64);not null"`
	PayloadJSON          string    `json:"-" gorm:"type:text;not null"`
	PayloadSHA256        string    `json:"payload_sha256" gorm:"type:char(64);not null"`
	CreatedAt            time.Time `json:"created_at"`
}

func (platformChannelCostEventArtifactV7) TableName() string {
	return "platform_channel_cost_events"
}
