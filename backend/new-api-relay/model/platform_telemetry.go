package model

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

var (
	ErrPlatformTaskStageEventCollision          = errors.New("task stage event collision")
	ErrPlatformOperationsSnapshotEventCollision = errors.New("operations snapshot event collision")
)

type PlatformTaskStageEvent struct {
	ID            string    `json:"id" gorm:"type:varchar(36);primaryKey"`
	RelayJobID    string    `json:"relay_job_id" gorm:"type:varchar(36);not null;index"`
	CompanyID     string    `json:"company_id" gorm:"type:varchar(36);not null;index"`
	TaskID        string    `json:"task_id" gorm:"type:varchar(36);not null;index"`
	Stage         string    `json:"stage" gorm:"type:varchar(32);not null;index"`
	OccurredAt    time.Time `json:"occurred_at" gorm:"not null;index"`
	PayloadJSON   string    `json:"-" gorm:"type:text;not null"`
	PayloadSHA256 string    `json:"payload_sha256" gorm:"type:char(64);not null"`
	CreatedAt     time.Time `json:"created_at"`
}

func (PlatformTaskStageEvent) TableName() string            { return "platform_task_stage_events" }
func (*PlatformTaskStageEvent) BeforeUpdate(*gorm.DB) error { return ErrPlatformProviderImmutableEvent }
func (*PlatformTaskStageEvent) BeforeDelete(*gorm.DB) error { return ErrPlatformProviderImmutableEvent }

type PlatformOperationsSnapshotEvent struct {
	ID            string    `json:"id" gorm:"type:varchar(36);primaryKey"`
	ObservedAt    time.Time `json:"observed_at" gorm:"not null;index"`
	ExpiresAt     time.Time `json:"expires_at" gorm:"not null"`
	PayloadJSON   string    `json:"-" gorm:"type:text;not null"`
	PayloadSHA256 string    `json:"payload_sha256" gorm:"type:char(64);not null"`
	CreatedAt     time.Time `json:"created_at"`
}

func (PlatformOperationsSnapshotEvent) TableName() string {
	return "platform_operations_snapshot_events"
}
func (*PlatformOperationsSnapshotEvent) BeforeUpdate(*gorm.DB) error {
	return ErrPlatformProviderImmutableEvent
}
func (*PlatformOperationsSnapshotEvent) BeforeDelete(*gorm.DB) error {
	return ErrPlatformProviderImmutableEvent
}

func CreatePlatformTaskStageEventTx(tx *gorm.DB, event *PlatformTaskStageEvent) (bool, error) {
	if tx == nil || event == nil {
		return false, fmt.Errorf("task stage event and database handle are required")
	}
	var payload dto.PlatformTaskStagePayload
	if err := common.Unmarshal([]byte(event.PayloadJSON), &payload); err != nil || payload.Validate() != nil ||
		payload.RelayJobID != event.RelayJobID || payload.CompanyID != event.CompanyID ||
		payload.TaskID != event.TaskID || payload.Stage != event.Stage ||
		!payload.OccurredAt.UTC().Equal(event.OccurredAt.UTC()) {
		return false, fmt.Errorf("task stage event payload is invalid")
	}
	digest := sha256.Sum256([]byte(event.PayloadJSON))
	if event.PayloadSHA256 != fmt.Sprintf("%x", digest) {
		return false, fmt.Errorf("task stage event payload digest is invalid")
	}
	if parsed, err := uuid.Parse(event.ID); err != nil || parsed.String() != event.ID {
		return false, fmt.Errorf("task stage event id is invalid")
	}
	now, err := GetDBTimeTx(tx)
	if err != nil {
		return false, err
	}
	event.OccurredAt = event.OccurredAt.UTC()
	event.CreatedAt = now
	result := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(event)
	if result.Error != nil {
		return false, result.Error
	}
	if result.RowsAffected == 0 {
		var existing PlatformTaskStageEvent
		if err := tx.Where("id = ?", event.ID).First(&existing).Error; err != nil {
			return false, err
		}
		if existing.RelayJobID != event.RelayJobID || existing.CompanyID != event.CompanyID ||
			existing.TaskID != event.TaskID || existing.Stage != event.Stage ||
			!existing.OccurredAt.UTC().Equal(event.OccurredAt.UTC()) ||
			existing.PayloadJSON != event.PayloadJSON || existing.PayloadSHA256 != event.PayloadSHA256 {
			return false, ErrPlatformTaskStageEventCollision
		}
		*event = existing
		return false, nil
	}
	_, err = CreatePlatformRelayExternalDeliveryTx(
		tx, PlatformRelayDeliveryKindTaskStage, event.ID,
		"relay-task-stage-"+event.ID, DefaultPlatformRelayDeliveryMaxAttempts,
	)
	return true, err
}

// RecordPlatformTaskStageTransitionTx records only customer Platform tasks.
// Internal callers such as TikTok use non-UUID client_reference_id values and
// intentionally remain outside the customer Platform telemetry stream.
func RecordPlatformTaskStageTransitionTx(tx *gorm.DB, jobID string, status string, occurredAt time.Time) error {
	stage, ok := platformTaskStageFromGenerationStatus(status)
	if !ok {
		return nil
	}
	var job PlatformGenerationJob
	if err := tx.Where("id = ?", jobID).First(&job).Error; err != nil {
		return err
	}
	if job.ClientReferenceID == nil {
		return nil
	}
	companyID, companyErr := uuid.Parse(job.TenantID)
	taskID, taskErr := uuid.Parse(*job.ClientReferenceID)
	if companyErr != nil || taskErr != nil || companyID.String() != job.TenantID || taskID.String() != *job.ClientReferenceID {
		return nil
	}
	payload := dto.PlatformTaskStagePayload{
		SchemaVersion:  1,
		CompanyID:      job.TenantID,
		TaskID:         *job.ClientReferenceID,
		RelayJobID:     job.ID,
		Stage:          stage,
		OccurredAt:     occurredAt.UTC(),
		ChannelKey:     "",
		ChannelType:    "",
		RouteID:        nil,
		ProviderTaskID: job.UpstreamTaskID,
		ErrorCode:      job.ErrorCode,
	}
	if !job.CreatedAt.IsZero() {
		duration := occurredAt.UTC().Sub(job.CreatedAt.UTC()).Milliseconds()
		if duration < 0 {
			duration = 0
		}
		payload.DurationMS = &duration
	}
	if job.ProviderRouteID > 0 {
		var route PlatformGenerationProviderRoute
		if err := tx.Where("id = ?", job.ProviderRouteID).First(&route).Error; err != nil {
			return err
		}
		routeID := route.ID
		payload.RouteID = &routeID
		payload.ChannelKey = route.RouteKey
		payload.ChannelType = route.ChannelClass
	}
	if err := payload.Validate(); err != nil {
		return err
	}
	body, err := common.Marshal(payload)
	if err != nil {
		return err
	}
	digest := sha256.Sum256(body)
	event := PlatformTaskStageEvent{
		// A persisted random identity represents one won, fenced state
		// transition. Database clocks are only second-granular on SQLite and
		// MySQL, so deriving the identity from occurred_at would collapse two
		// legitimate transitions (for example queued -> submitting -> queued)
		// that happen in the same second.
		ID:            uuid.NewString(),
		RelayJobID:    job.ID,
		CompanyID:     job.TenantID,
		TaskID:        *job.ClientReferenceID,
		Stage:         stage,
		OccurredAt:    occurredAt.UTC(),
		PayloadJSON:   string(body),
		PayloadSHA256: fmt.Sprintf("%x", digest),
	}
	_, err = CreatePlatformTaskStageEventTx(tx, &event)
	return err
}

func platformTaskStageFromGenerationStatus(status string) (string, bool) {
	switch status {
	case PlatformGenerationStatusQueued:
		return dto.PlatformTaskStageQueued, true
	case PlatformGenerationStatusSubmitting:
		return dto.PlatformTaskStageSubmitting, true
	case PlatformGenerationStatusReconciliationRequired:
		return dto.PlatformTaskStageSubmissionUnknown, true
	case PlatformGenerationStatusProcessing:
		return dto.PlatformTaskStageProviderProcessing, true
	case PlatformGenerationStatusTransferring:
		return dto.PlatformTaskStageArtifactTransferring, true
	case PlatformGenerationStatusSucceeded:
		return dto.PlatformTaskStageArtifactStored, true
	case PlatformGenerationStatusFailed:
		return dto.PlatformTaskStageFailed, true
	case PlatformGenerationStatusCancelled:
		return dto.PlatformTaskStageCancelled, true
	default:
		return "", false
	}
}

func CreatePlatformOperationsSnapshotEvent(snapshot dto.PlatformOperationsSnapshot) (bool, *PlatformOperationsSnapshotEvent, error) {
	if err := snapshot.Validate(); err != nil {
		return false, nil, err
	}
	payload, err := common.Marshal(snapshot)
	if err != nil {
		return false, nil, err
	}
	digest := sha256.Sum256(payload)
	event := PlatformOperationsSnapshotEvent{
		ID: uuid.NewSHA1(uuid.NameSpaceURL, []byte(
			"relay-operations-snapshot:"+snapshot.ObservedAt.UTC().Format(time.RFC3339Nano)+":"+fmt.Sprintf("%x", digest),
		)).String(),
		ObservedAt:    snapshot.ObservedAt.UTC(),
		ExpiresAt:     snapshot.ExpiresAt.UTC(),
		PayloadJSON:   string(payload),
		PayloadSHA256: fmt.Sprintf("%x", digest),
	}
	created := false
	err = DB.Transaction(func(tx *gorm.DB) error {
		now, clockErr := GetDBTimeTx(tx)
		if clockErr != nil {
			return clockErr
		}
		event.CreatedAt = now
		result := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&event)
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected == 0 {
			var existing PlatformOperationsSnapshotEvent
			if lookupErr := tx.Where("id = ?", event.ID).First(&existing).Error; lookupErr != nil {
				return lookupErr
			}
			if !existing.ObservedAt.UTC().Equal(event.ObservedAt.UTC()) ||
				!existing.ExpiresAt.UTC().Equal(event.ExpiresAt.UTC()) ||
				existing.PayloadJSON != event.PayloadJSON || existing.PayloadSHA256 != event.PayloadSHA256 {
				return ErrPlatformOperationsSnapshotEventCollision
			}
			event = existing
			return nil
		}
		created = true
		_, deliveryErr := CreatePlatformRelayExternalDeliveryTx(
			tx, PlatformRelayDeliveryKindOperationsSnapshot, event.ID,
			"relay-operations-snapshot-"+event.ID, DefaultPlatformRelayDeliveryMaxAttempts,
		)
		return deliveryErr
	})
	return created, &event, err
}

func GetPlatformTaskStageEvent(eventID string) (*PlatformTaskStageEvent, error) {
	var event PlatformTaskStageEvent
	err := DB.Where("id = ?", eventID).First(&event).Error
	return &event, err
}

func GetPlatformOperationsSnapshotEvent(eventID string) (*PlatformOperationsSnapshotEvent, error) {
	var event PlatformOperationsSnapshotEvent
	err := DB.Where("id = ?", eventID).First(&event).Error
	return &event, err
}

func GetOldestPendingPlatformRelayDeliveryAt(eventKind string) (*time.Time, error) {
	if !platformRelayDeliveryKindValid(eventKind) {
		return nil, fmt.Errorf("Relay external delivery event kind is invalid")
	}
	var delivery PlatformRelayExternalDelivery
	err := DB.Where("event_kind = ? AND state IN ?", eventKind, []string{
		PlatformRelayDeliveryPending, PlatformRelayDeliveryClaimed,
	}).Order("created_at ASC").First(&delivery).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	value := delivery.CreatedAt.UTC()
	return &value, nil
}

func telemetryStatusFromUpdates(updates map[string]any) (string, bool) {
	value, ok := updates["status"]
	if !ok {
		return "", false
	}
	status, ok := value.(string)
	return status, ok
}
