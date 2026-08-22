package model

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

var (
	ErrPlatformGenerationCallbackRedriveEventImmutable = errors.New("generation callback redrive event is append-only")
	ErrPlatformGenerationCallbackRedriveConflict       = errors.New("generation callback redrive operation conflicts with an existing operation")
	ErrPlatformGenerationCallbackNotDeadLetter         = errors.New("generation callback delivery is not dead-lettered")
)

// PlatformGenerationCallbackRedriveRequest is the operator-authored portion
// of a callback redrive. OperationID is tenant-wide and stable. RequestID is
// evidence for the first HTTP request that committed the operation.
type PlatformGenerationCallbackRedriveRequest struct {
	OperationID string
	RequestID   string
	Actor       string
	Reason      string
}

// PlatformGenerationCallbackRedriveEvent is the immutable audit receipt for
// rearming one dead-lettered callback delivery. The callback delivery remains
// the original event: its ID, payload, digest, destination, job binding, and
// original request ID are never replaced by a redrive.
type PlatformGenerationCallbackRedriveEvent struct {
	ID                        string     `json:"id" gorm:"type:varchar(36);primaryKey"`
	TenantID                  string     `json:"tenant_id" gorm:"type:varchar(36);not null;uniqueIndex:ux_platform_generation_callback_redrive_operation,priority:1;index:idx_platform_generation_callback_redrive_delivery,priority:1"`
	DeliveryID                string     `json:"delivery_id" gorm:"type:varchar(36);not null;index:idx_platform_generation_callback_redrive_delivery,priority:2"`
	OperationID               string     `json:"operation_id" gorm:"type:varchar(128);not null;uniqueIndex:ux_platform_generation_callback_redrive_operation,priority:2"`
	RequestID                 string     `json:"request_id" gorm:"type:varchar(80);not null"`
	Actor                     string     `json:"actor" gorm:"type:varchar(128);not null"`
	Reason                    string     `json:"reason" gorm:"type:varchar(240);not null"`
	PreviousState             string     `json:"previous_state" gorm:"type:varchar(16);not null"`
	PreviousAttempts          int        `json:"previous_attempts" gorm:"not null"`
	PreviousMaxAttempts       int        `json:"previous_max_attempts" gorm:"not null"`
	PreviousResponseStatus    int        `json:"previous_response_status" gorm:"not null"`
	PreviousLastError         string     `json:"previous_last_error" gorm:"type:varchar(64);not null"`
	PreviousDeadLetteredAt    *time.Time `json:"previous_dead_lettered_at" gorm:"not null"`
	CallbackURLSHA256         string     `json:"callback_url_sha256" gorm:"type:char(64);not null"`
	PayloadSHA256             string     `json:"payload_sha256" gorm:"type:char(64);not null"`
	OriginalCallbackRequestID string     `json:"original_callback_request_id" gorm:"type:varchar(80);not null"`
	ResultState               string     `json:"result_state" gorm:"type:varchar(16);not null"`
	PayloadJSON               string     `json:"-" gorm:"type:text;not null"`
	ReceiptSHA256             string     `json:"receipt_sha256" gorm:"type:char(64);not null"`
	RedrivenAt                time.Time  `json:"redriven_at" gorm:"not null;index"`
	CreatedAt                 time.Time  `json:"created_at" gorm:"not null"`
}

func (PlatformGenerationCallbackRedriveEvent) TableName() string {
	return "platform_generation_callback_redrive_events"
}

func (*PlatformGenerationCallbackRedriveEvent) BeforeUpdate(*gorm.DB) error {
	return ErrPlatformGenerationCallbackRedriveEventImmutable
}

func (*PlatformGenerationCallbackRedriveEvent) BeforeDelete(*gorm.DB) error {
	return ErrPlatformGenerationCallbackRedriveEventImmutable
}

type PlatformGenerationCallbackRedriveReceipt struct {
	Event        PlatformGenerationCallbackRedriveEvent
	CurrentState string
}

func GetPlatformGenerationCallbackDeliveryForOperations(
	deliveryID string,
	tenantID string,
) (*PlatformGenerationCallbackDelivery, error) {
	if !isCanonicalPlatformGenerationReconciliationUUID(deliveryID) ||
		!isCanonicalPlatformGenerationReconciliationUUID(tenantID) {
		return nil, gorm.ErrRecordNotFound
	}
	var delivery PlatformGenerationCallbackDelivery
	if err := DB.Where("id = ? AND tenant_id = ?", deliveryID, tenantID).First(&delivery).Error; err != nil {
		return nil, err
	}
	return &delivery, nil
}

func ListPlatformGenerationCallbackDeliveriesForOperations(
	tenantID string,
	state string,
	page int,
	pageSize int,
) ([]PlatformGenerationCallbackDelivery, int64, error) {
	if !isCanonicalPlatformGenerationReconciliationUUID(tenantID) ||
		!isPlatformGenerationCallbackState(state) ||
		page < 1 || pageSize < 1 || pageSize > 100 {
		return nil, 0, fmt.Errorf("generation callback delivery query is invalid")
	}
	query := DB.Model(&PlatformGenerationCallbackDelivery{}).
		Where("tenant_id = ? AND state = ?", tenantID, state)
	var total int64
	if err := query.Count(&total).Error; err != nil {
		return nil, 0, err
	}
	deliveries := make([]PlatformGenerationCallbackDelivery, 0)
	if err := query.Order("updated_at DESC, id DESC").
		Offset((page - 1) * pageSize).Limit(pageSize).
		Find(&deliveries).Error; err != nil {
		return nil, 0, err
	}
	return deliveries, total, nil
}

func isPlatformGenerationCallbackState(state string) bool {
	switch state {
	case PlatformGenerationCallbackPending,
		PlatformGenerationCallbackClaimed,
		PlatformGenerationCallbackDelivered,
		PlatformGenerationCallbackDeadLetter:
		return true
	default:
		return false
	}
}

type platformGenerationCallbackRedriveCanonicalPayload struct {
	TenantID                  string     `json:"tenant_id"`
	DeliveryID                string     `json:"delivery_id"`
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
}

func validatePlatformGenerationCallbackRedriveRequest(request PlatformGenerationCallbackRedriveRequest) error {
	if !platformGenerationOperationIDPattern.MatchString(request.OperationID) ||
		!platformGenerationRequestIDPattern.MatchString(request.RequestID) ||
		strings.TrimSpace(request.Actor) != request.Actor || len(request.Actor) < 1 || len(request.Actor) > 128 ||
		strings.TrimSpace(request.Reason) != request.Reason || len(request.Reason) < 3 || len(request.Reason) > 240 {
		return fmt.Errorf("generation callback redrive request is invalid")
	}
	return nil
}

func newPlatformGenerationCallbackRedriveEvent(
	delivery PlatformGenerationCallbackDelivery,
	request PlatformGenerationCallbackRedriveRequest,
	now time.Time,
) (*PlatformGenerationCallbackRedriveEvent, error) {
	if !isCanonicalPlatformGenerationReconciliationUUID(delivery.ID) ||
		!isCanonicalPlatformGenerationReconciliationUUID(delivery.TenantID) ||
		delivery.State != PlatformGenerationCallbackDeadLetter || delivery.DeadLetteredAt == nil ||
		len(delivery.PayloadSHA256) != 64 {
		return nil, fmt.Errorf("generation callback dead-letter evidence is invalid")
	}
	if err := validatePlatformGenerationCallbackRedriveRequest(request); err != nil {
		return nil, err
	}
	callbackURLDigest := sha256.Sum256([]byte(delivery.CallbackURL))
	payload := platformGenerationCallbackRedriveCanonicalPayload{
		TenantID:                  delivery.TenantID,
		DeliveryID:                delivery.ID,
		OperationID:               request.OperationID,
		RequestID:                 request.RequestID,
		Actor:                     request.Actor,
		Reason:                    request.Reason,
		PreviousState:             delivery.State,
		PreviousAttempts:          delivery.Attempts,
		PreviousMaxAttempts:       delivery.MaxAttempts,
		PreviousResponseStatus:    delivery.ResponseStatus,
		PreviousLastError:         delivery.LastError,
		PreviousDeadLetteredAt:    delivery.DeadLetteredAt,
		CallbackURLSHA256:         hex.EncodeToString(callbackURLDigest[:]),
		PayloadSHA256:             delivery.PayloadSHA256,
		OriginalCallbackRequestID: delivery.RequestID,
		ResultState:               PlatformGenerationCallbackPending,
	}
	payloadBytes, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	receiptDigest := sha256.Sum256(payloadBytes)
	eventID := uuid.NewSHA1(
		uuid.NameSpaceURL,
		[]byte("platform-generation-callback-redrive-v1\x00"+delivery.TenantID+"\x00"+request.OperationID),
	).String()
	return &PlatformGenerationCallbackRedriveEvent{
		ID:                        eventID,
		TenantID:                  delivery.TenantID,
		DeliveryID:                delivery.ID,
		OperationID:               request.OperationID,
		RequestID:                 request.RequestID,
		Actor:                     request.Actor,
		Reason:                    request.Reason,
		PreviousState:             delivery.State,
		PreviousAttempts:          delivery.Attempts,
		PreviousMaxAttempts:       delivery.MaxAttempts,
		PreviousResponseStatus:    delivery.ResponseStatus,
		PreviousLastError:         delivery.LastError,
		PreviousDeadLetteredAt:    delivery.DeadLetteredAt,
		CallbackURLSHA256:         payload.CallbackURLSHA256,
		PayloadSHA256:             delivery.PayloadSHA256,
		OriginalCallbackRequestID: delivery.RequestID,
		ResultState:               PlatformGenerationCallbackPending,
		PayloadJSON:               string(payloadBytes),
		ReceiptSHA256:             hex.EncodeToString(receiptDigest[:]),
		RedrivenAt:                now,
		CreatedAt:                 now,
	}, nil
}

func platformGenerationCallbackRedriveRequestMatchesEvent(
	event PlatformGenerationCallbackRedriveEvent,
	deliveryID string,
	request PlatformGenerationCallbackRedriveRequest,
) bool {
	// RequestID records the first committed transport attempt. A retry with a
	// new request ID is a semantic replay and returns the immutable first receipt.
	return event.DeliveryID == deliveryID && event.OperationID == request.OperationID &&
		event.Actor == request.Actor && event.Reason == request.Reason
}

func findPlatformGenerationCallbackRedriveEventTx(
	tx *gorm.DB,
	tenantID string,
	operationID string,
) (*PlatformGenerationCallbackRedriveEvent, error) {
	var event PlatformGenerationCallbackRedriveEvent
	if err := tx.Where("tenant_id = ? AND operation_id = ?", tenantID, operationID).First(&event).Error; err != nil {
		return nil, err
	}
	return &event, nil
}

func callbackRedriveReplayTx(
	tx *gorm.DB,
	tenantID string,
	deliveryID string,
	request PlatformGenerationCallbackRedriveRequest,
) (*PlatformGenerationCallbackRedriveReceipt, bool, error) {
	event, err := findPlatformGenerationCallbackRedriveEventTx(tx, tenantID, request.OperationID)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	if !platformGenerationCallbackRedriveRequestMatchesEvent(*event, deliveryID, request) {
		return nil, false, ErrPlatformGenerationCallbackRedriveConflict
	}
	var delivery PlatformGenerationCallbackDelivery
	if err := tx.Select("state").Where("id = ? AND tenant_id = ?", deliveryID, tenantID).First(&delivery).Error; err != nil {
		return nil, false, err
	}
	return &PlatformGenerationCallbackRedriveReceipt{Event: *event, CurrentState: delivery.State}, true, nil
}

// RedrivePlatformGenerationCallbackDelivery atomically records immutable
// dead-letter evidence and rearms the same callback event. Only dead_letter is
// an admissible first transition; an identical operation_id replay remains
// readable and idempotent after the delivery has moved on.
func RedrivePlatformGenerationCallbackDelivery(
	deliveryID string,
	tenantID string,
	request PlatformGenerationCallbackRedriveRequest,
) (*PlatformGenerationCallbackRedriveReceipt, bool, error) {
	if !isCanonicalPlatformGenerationReconciliationUUID(deliveryID) ||
		!isCanonicalPlatformGenerationReconciliationUUID(tenantID) {
		return nil, false, gorm.ErrRecordNotFound
	}
	if err := validatePlatformGenerationCallbackRedriveRequest(request); err != nil {
		return nil, false, err
	}
	var receipt *PlatformGenerationCallbackRedriveReceipt
	idempotentReplay := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		var err error
		receipt, idempotentReplay, err = callbackRedriveReplayTx(tx, tenantID, deliveryID, request)
		if err != nil || receipt != nil {
			return err
		}

		var delivery PlatformGenerationCallbackDelivery
		if err := lockForUpdate(tx.Where("id = ? AND tenant_id = ?", deliveryID, tenantID)).First(&delivery).Error; err != nil {
			return err
		}
		// Recheck under the delivery lock. On PostgreSQL this closes the race in
		// which another transaction commits the same operation while we wait.
		receipt, idempotentReplay, err = callbackRedriveReplayTx(tx, tenantID, deliveryID, request)
		if err != nil || receipt != nil {
			return err
		}
		if delivery.State != PlatformGenerationCallbackDeadLetter {
			return ErrPlatformGenerationCallbackNotDeadLetter
		}

		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		event, err := newPlatformGenerationCallbackRedriveEvent(delivery, request, now)
		if err != nil {
			return err
		}
		result := tx.Clauses(clause.OnConflict{
			Columns:   []clause.Column{{Name: "tenant_id"}, {Name: "operation_id"}},
			DoNothing: true,
		}).Create(event)
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			existing, findErr := findPlatformGenerationCallbackRedriveEventTx(tx, tenantID, request.OperationID)
			if findErr != nil {
				return findErr
			}
			if !platformGenerationCallbackRedriveRequestMatchesEvent(*existing, deliveryID, request) {
				return ErrPlatformGenerationCallbackRedriveConflict
			}
			receipt = &PlatformGenerationCallbackRedriveReceipt{Event: *existing, CurrentState: delivery.State}
			idempotentReplay = true
			return nil
		}

		update := tx.Model(&PlatformGenerationCallbackDelivery{}).Where(
			"id = ? AND tenant_id = ? AND state = ?",
			deliveryID,
			tenantID,
			PlatformGenerationCallbackDeadLetter,
		).Updates(map[string]any{
			"state":            PlatformGenerationCallbackPending,
			"attempts":         0,
			"available_at":     now,
			"claim_token":      "",
			"claimed_at":       nil,
			"claim_expires_at": nil,
			"response_status":  0,
			"last_error":       "",
			"delivered_at":     nil,
			"dead_lettered_at": nil,
		})
		if update.Error != nil {
			return update.Error
		}
		if update.RowsAffected != 1 {
			return ErrPlatformGenerationCallbackNotDeadLetter
		}
		receipt = &PlatformGenerationCallbackRedriveReceipt{
			Event:        *event,
			CurrentState: PlatformGenerationCallbackPending,
		}
		return nil
	})
	return receipt, idempotentReplay, err
}

func GetPlatformGenerationCallbackRedriveReceipt(
	deliveryID string,
	tenantID string,
	operationID string,
) (*PlatformGenerationCallbackRedriveReceipt, error) {
	if !isCanonicalPlatformGenerationReconciliationUUID(deliveryID) ||
		!isCanonicalPlatformGenerationReconciliationUUID(tenantID) ||
		!platformGenerationOperationIDPattern.MatchString(operationID) {
		return nil, gorm.ErrRecordNotFound
	}
	var receipt PlatformGenerationCallbackRedriveReceipt
	err := DB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Where(
			"tenant_id = ? AND delivery_id = ? AND operation_id = ?",
			tenantID,
			deliveryID,
			operationID,
		).First(&receipt.Event).Error; err != nil {
			return err
		}
		var delivery PlatformGenerationCallbackDelivery
		if err := tx.Select("state").Where("id = ? AND tenant_id = ?", deliveryID, tenantID).First(&delivery).Error; err != nil {
			return err
		}
		receipt.CurrentState = delivery.State
		return nil
	})
	if err != nil {
		return nil, err
	}
	return &receipt, nil
}

func ListPlatformGenerationCallbackRedriveEvents(
	deliveryID string,
	tenantID string,
) ([]PlatformGenerationCallbackRedriveEvent, error) {
	events := make([]PlatformGenerationCallbackRedriveEvent, 0)
	if err := DB.Where("tenant_id = ? AND delivery_id = ?", tenantID, deliveryID).
		Order("redriven_at DESC, id DESC").Find(&events).Error; err != nil {
		return nil, err
	}
	return events, nil
}

// MigratePlatformGenerationCallbackOperationsStorage installs the immutable
// audit table separately so startup cannot accidentally omit database guards.
func MigratePlatformGenerationCallbackOperationsStorage() error {
	return MigratePlatformGenerationCallbackOperationsStorageWithDB(DB)
}

func MigratePlatformGenerationCallbackOperationsStorageWithDB(db *gorm.DB) error {
	if db == nil {
		return fmt.Errorf("database is not initialized")
	}
	if err := db.AutoMigrate(&PlatformGenerationCallbackRedriveEvent{}); err != nil {
		return err
	}
	return installPlatformGenerationCallbackRedriveAppendOnlyGuardsWithDB(db)
}

func installPlatformGenerationCallbackRedriveAppendOnlyGuards() error {
	return installPlatformGenerationCallbackRedriveAppendOnlyGuardsWithDB(DB)
}

func installPlatformGenerationCallbackRedriveAppendOnlyGuardsWithDB(db *gorm.DB) error {
	if db == nil {
		return fmt.Errorf("database is not initialized")
	}
	const table = "platform_generation_callback_redrive_events"
	switch db.Dialector.Name() {
	case "postgres":
		if err := db.Exec(`
CREATE OR REPLACE FUNCTION reject_platform_generation_callback_redrive_event_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'platform generation callback redrive events are append-only';
END;
$$ LANGUAGE plpgsql`).Error; err != nil {
			return err
		}
		for _, trigger := range []string{
			"trg_platform_generation_callback_redrive_events_no_mutation",
			"trg_platform_generation_callback_redrive_events_no_truncate",
		} {
			if err := db.Exec(fmt.Sprintf("DROP TRIGGER IF EXISTS %s ON %s", trigger, table)).Error; err != nil {
				return err
			}
		}
		if err := db.Exec("CREATE TRIGGER trg_platform_generation_callback_redrive_events_no_mutation BEFORE UPDATE OR DELETE ON " + table + " FOR EACH ROW EXECUTE FUNCTION reject_platform_generation_callback_redrive_event_mutation()").Error; err != nil {
			return err
		}
		return db.Exec("CREATE TRIGGER trg_platform_generation_callback_redrive_events_no_truncate BEFORE TRUNCATE ON " + table + " FOR EACH STATEMENT EXECUTE FUNCTION reject_platform_generation_callback_redrive_event_mutation()").Error
	case "mysql":
		for _, operation := range []string{"UPDATE", "DELETE"} {
			trigger := "trg_platform_generation_callback_redrive_no_" + strings.ToLower(operation)
			if err := db.Exec("DROP TRIGGER IF EXISTS " + trigger).Error; err != nil {
				return err
			}
			if err := db.Exec(fmt.Sprintf(
				"CREATE TRIGGER %s BEFORE %s ON %s FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'platform generation callback redrive events are append-only'",
				trigger,
				operation,
				table,
			)).Error; err != nil {
				return err
			}
		}
	default:
		for _, operation := range []string{"UPDATE", "DELETE"} {
			trigger := "trg_platform_generation_callback_redrive_no_" + strings.ToLower(operation)
			if err := db.Exec(fmt.Sprintf(
				"CREATE TRIGGER IF NOT EXISTS %s BEFORE %s ON %s BEGIN SELECT RAISE(ABORT, 'platform generation callback redrive events are append-only'); END",
				trigger,
				operation,
				table,
			)).Error; err != nil {
				return err
			}
		}
	}
	return nil
}
