package model

import (
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

const (
	PlatformRelayDeliveryKindProviderAlert      = "provider_alert"
	PlatformRelayDeliveryKindChannelCost        = "channel_cost"
	PlatformRelayDeliveryKindDownloadCompletion = "download_completion"
	PlatformRelayDeliveryKindTaskStage          = "task_stage"
	PlatformRelayDeliveryKindOperationsSnapshot = "operations_snapshot"
)

const (
	PlatformRelayDeliveryPending    = "pending"
	PlatformRelayDeliveryClaimed    = "claimed"
	PlatformRelayDeliveryDelivered  = "delivered"
	PlatformRelayDeliveryDeadLetter = "dead_letter"
)

const (
	PlatformRelayDeliveryFailureConfiguration = "configuration_rejected"
	PlatformRelayDeliveryFailureTarget        = "target_rejected"
	PlatformRelayDeliveryFailurePayload       = "payload_rejected"
	PlatformRelayDeliveryFailureTransport     = "transport_failed"
	PlatformRelayDeliveryFailureEndpoint      = "endpoint_rejected"
	PlatformRelayDeliveryFailureConflict      = "idempotency_conflict"
	PlatformRelayDeliveryFailureGeneric       = "delivery_failed"
)

const DefaultPlatformRelayDeliveryMaxAttempts = 8

var ErrPlatformRelayDeliveryCollision = errors.New("Relay external delivery collision")

// PlatformRelayExternalDelivery is the mutable delivery half of immutable
// provider-alert and channel-cost events. A claim always owns one row only.
type PlatformRelayExternalDelivery struct {
	ID             int64      `json:"id" gorm:"primaryKey"`
	EventKind      string     `json:"event_kind" gorm:"type:varchar(32);not null;uniqueIndex:uniq_platform_relay_delivery_event,priority:1;index:idx_platform_relay_delivery_available,priority:1"`
	EventID        string     `json:"event_id" gorm:"type:varchar(36);not null;uniqueIndex:uniq_platform_relay_delivery_event,priority:2"`
	RequestID      string     `json:"request_id" gorm:"type:varchar(80);not null"`
	State          string     `json:"state" gorm:"type:varchar(16);not null;index:idx_platform_relay_delivery_available,priority:2"`
	Attempts       int        `json:"attempts" gorm:"not null"`
	MaxAttempts    int        `json:"max_attempts" gorm:"not null"`
	AvailableAt    time.Time  `json:"available_at" gorm:"not null;index:idx_platform_relay_delivery_available,priority:3"`
	ClaimToken     string     `json:"-" gorm:"type:varchar(36);index"`
	ClaimedAt      *time.Time `json:"-"`
	ClaimExpiresAt *time.Time `json:"-" gorm:"index"`
	ResponseStatus int        `json:"response_status" gorm:"not null"`
	LastError      string     `json:"last_error" gorm:"type:varchar(64);not null"`
	DeliveredAt    *time.Time `json:"delivered_at"`
	DeadLetteredAt *time.Time `json:"dead_lettered_at"`
	CreatedAt      time.Time  `json:"created_at"`
	UpdatedAt      time.Time  `json:"updated_at"`
}

func (PlatformRelayExternalDelivery) TableName() string {
	return "platform_relay_external_deliveries"
}

type PlatformRelayDeliveryClaim struct {
	Delivery PlatformRelayExternalDelivery
	Token    string
}

type PlatformRelayDeliveryCounts struct {
	Pending    int64 `json:"pending"`
	Claimed    int64 `json:"claimed"`
	Delivered  int64 `json:"delivered"`
	DeadLetter int64 `json:"dead_letter"`
}

func CreatePlatformRelayExternalDeliveryTx(
	tx *gorm.DB,
	eventKind string,
	eventID string,
	requestID string,
	maxAttempts int,
) (bool, error) {
	if tx == nil {
		return false, fmt.Errorf("Relay external delivery database handle is required")
	}
	if !platformRelayDeliveryKindValid(eventKind) {
		return false, fmt.Errorf("Relay external delivery event kind is invalid")
	}
	if parsed, err := uuid.Parse(eventID); err != nil || parsed.String() != eventID {
		return false, fmt.Errorf("Relay external delivery event id is invalid")
	}
	if requestID == "" || strings.TrimSpace(requestID) != requestID || len(requestID) > 80 {
		return false, fmt.Errorf("Relay external delivery request id is invalid")
	}
	if maxAttempts == 0 {
		maxAttempts = DefaultPlatformRelayDeliveryMaxAttempts
	}
	if maxAttempts < 1 || maxAttempts > 100 {
		return false, fmt.Errorf("Relay external delivery max attempts is invalid")
	}

	now, err := GetDBTimeTx(tx)
	if err != nil {
		return false, err
	}
	delivery := PlatformRelayExternalDelivery{
		EventKind:   eventKind,
		EventID:     eventID,
		RequestID:   requestID,
		State:       PlatformRelayDeliveryPending,
		MaxAttempts: maxAttempts,
		AvailableAt: now,
		CreatedAt:   now,
		UpdatedAt:   now,
	}
	result := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&delivery)
	if result.Error != nil {
		return false, result.Error
	}
	if result.RowsAffected == 1 {
		return true, nil
	}

	var existing PlatformRelayExternalDelivery
	if err := tx.Where("event_kind = ? AND event_id = ?", eventKind, eventID).First(&existing).Error; err != nil {
		return false, err
	}
	if existing.RequestID != requestID || existing.MaxAttempts != maxAttempts {
		return false, ErrPlatformRelayDeliveryCollision
	}
	return false, nil
}

// ClaimPlatformRelayExternalDelivery claims exactly one item of one event kind
// with a random token and the database clock as the lease authority.
func ClaimPlatformRelayExternalDelivery(eventKind string, lease time.Duration) (*PlatformRelayDeliveryClaim, error) {
	if !platformRelayDeliveryKindValid(eventKind) {
		return nil, fmt.Errorf("Relay external delivery event kind is invalid")
	}
	if lease < time.Second {
		return nil, fmt.Errorf("Relay external delivery lease must be at least one second")
	}

	var claim *PlatformRelayDeliveryClaim
	noClaim := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		if err := tx.Model(&PlatformRelayExternalDelivery{}).Where(
			"event_kind = ? AND attempts >= max_attempts AND (state = ? OR (state = ? AND claim_expires_at <= ?))",
			eventKind,
			PlatformRelayDeliveryPending,
			PlatformRelayDeliveryClaimed,
			now,
		).Updates(map[string]any{
			"state":            PlatformRelayDeliveryDeadLetter,
			"claim_token":      "",
			"claimed_at":       nil,
			"claim_expires_at": nil,
			"last_error":       PlatformRelayDeliveryFailureGeneric,
			"dead_lettered_at": now,
			"updated_at":       now,
		}).Error; err != nil {
			return err
		}

		var delivery PlatformRelayExternalDelivery
		query := tx.Where(
			"event_kind = ? AND available_at <= ? AND attempts < max_attempts AND (state = ? OR (state = ? AND claim_expires_at <= ?))",
			eventKind,
			now,
			PlatformRelayDeliveryPending,
			PlatformRelayDeliveryClaimed,
			now,
		).Order("available_at ASC, created_at ASC, id ASC")
		if err := lockForUpdate(query).First(&delivery).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				noClaim = true
				return nil
			}
			return err
		}

		token := uuid.NewString()
		expiresAt := now.Add(lease)
		result := tx.Model(&PlatformRelayExternalDelivery{}).Where(
			"id = ? AND state = ?",
			delivery.ID,
			delivery.State,
		).Updates(map[string]any{
			"state":            PlatformRelayDeliveryClaimed,
			"attempts":         gorm.Expr("attempts + 1"),
			"claim_token":      token,
			"claimed_at":       now,
			"claim_expires_at": expiresAt,
			"updated_at":       now,
		})
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return fmt.Errorf("Relay external delivery claim lost its state fence")
		}
		delivery.State = PlatformRelayDeliveryClaimed
		delivery.Attempts++
		delivery.ClaimToken = token
		delivery.ClaimedAt = &now
		delivery.ClaimExpiresAt = &expiresAt
		claim = &PlatformRelayDeliveryClaim{Delivery: delivery, Token: token}
		return nil
	})
	if err == nil && noClaim {
		return nil, gorm.ErrRecordNotFound
	}
	return claim, err
}

func CompletePlatformRelayExternalDelivery(eventKind string, eventID string, token string, responseStatus int) (bool, error) {
	if !platformRelayDeliveryKindValid(eventKind) || responseStatus < 200 || responseStatus >= 300 {
		return false, fmt.Errorf("Relay external delivery completion is invalid")
	}
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		result := tx.Model(&PlatformRelayExternalDelivery{}).Where(
			"event_kind = ? AND event_id = ? AND state = ? AND claim_token = ? AND "+unexpiredDatabaseLeasePredicate("claim_expires_at"),
			eventKind,
			eventID,
			PlatformRelayDeliveryClaimed,
			token,
		).Updates(map[string]any{
			"state":            PlatformRelayDeliveryDelivered,
			"claim_token":      "",
			"claimed_at":       nil,
			"claim_expires_at": nil,
			"response_status":  responseStatus,
			"last_error":       "",
			"delivered_at":     now,
			"updated_at":       now,
		})
		won = result.RowsAffected == 1
		return result.Error
	})
	return won, err
}

func ReleasePlatformRelayExternalDelivery(
	eventKind string,
	eventID string,
	token string,
	retryDelay time.Duration,
	failure string,
	responseStatus int,
) (string, bool, error) {
	if !platformRelayDeliveryKindValid(eventKind) || retryDelay < 0 {
		return "", false, fmt.Errorf("Relay external delivery retry is invalid")
	}
	state := ""
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		var delivery PlatformRelayExternalDelivery
		query := lockForUpdate(tx.Where(
			"event_kind = ? AND event_id = ? AND state = ? AND claim_token = ? AND claim_expires_at > ?",
			eventKind,
			eventID,
			PlatformRelayDeliveryClaimed,
			token,
			now,
		))
		if err := query.First(&delivery).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				return nil
			}
			return err
		}

		state = PlatformRelayDeliveryPending
		updates := map[string]any{
			"state":            state,
			"available_at":     now.Add(retryDelay),
			"claim_token":      "",
			"claimed_at":       nil,
			"claim_expires_at": nil,
			"response_status":  normalizePlatformRelayDeliveryResponseStatus(responseStatus),
			"last_error":       normalizePlatformRelayDeliveryFailure(failure),
			"updated_at":       now,
		}
		if delivery.Attempts >= delivery.MaxAttempts {
			state = PlatformRelayDeliveryDeadLetter
			updates["state"] = state
			updates["dead_lettered_at"] = now
		}
		result := tx.Model(&PlatformRelayExternalDelivery{}).Where(
			"id = ? AND state = ? AND claim_token = ? AND "+unexpiredDatabaseLeasePredicate("claim_expires_at"),
			delivery.ID,
			PlatformRelayDeliveryClaimed,
			token,
		).Updates(updates)
		won = result.RowsAffected == 1
		return result.Error
	})
	return state, won, err
}

func DeadLetterPlatformRelayExternalDelivery(
	eventKind string,
	eventID string,
	token string,
	failure string,
	responseStatus int,
) (bool, error) {
	if !platformRelayDeliveryKindValid(eventKind) {
		return false, fmt.Errorf("Relay external delivery event kind is invalid")
	}
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		result := tx.Model(&PlatformRelayExternalDelivery{}).Where(
			"event_kind = ? AND event_id = ? AND state = ? AND claim_token = ? AND "+unexpiredDatabaseLeasePredicate("claim_expires_at"),
			eventKind,
			eventID,
			PlatformRelayDeliveryClaimed,
			token,
		).Updates(map[string]any{
			"state":            PlatformRelayDeliveryDeadLetter,
			"claim_token":      "",
			"claimed_at":       nil,
			"claim_expires_at": nil,
			"response_status":  normalizePlatformRelayDeliveryResponseStatus(responseStatus),
			"last_error":       normalizePlatformRelayDeliveryFailure(failure),
			"dead_lettered_at": now,
			"updated_at":       now,
		})
		won = result.RowsAffected == 1
		return result.Error
	})
	return won, err
}

func GetPlatformRelayDeliveryCounts(eventKind string) (PlatformRelayDeliveryCounts, error) {
	var counts PlatformRelayDeliveryCounts
	if !platformRelayDeliveryKindValid(eventKind) {
		return counts, fmt.Errorf("Relay external delivery event kind is invalid")
	}
	rows, err := DB.Model(&PlatformRelayExternalDelivery{}).
		Select("state, COUNT(*) AS total").
		Where("event_kind = ?", eventKind).
		Group("state").Rows()
	if err != nil {
		return counts, err
	}
	defer rows.Close()
	for rows.Next() {
		var state string
		var total int64
		if err := rows.Scan(&state, &total); err != nil {
			return counts, err
		}
		switch state {
		case PlatformRelayDeliveryPending:
			counts.Pending = total
		case PlatformRelayDeliveryClaimed:
			counts.Claimed = total
		case PlatformRelayDeliveryDelivered:
			counts.Delivered = total
		case PlatformRelayDeliveryDeadLetter:
			counts.DeadLetter = total
		}
	}
	return counts, rows.Err()
}

func platformRelayDeliveryKindValid(eventKind string) bool {
	return eventKind == PlatformRelayDeliveryKindProviderAlert ||
		eventKind == PlatformRelayDeliveryKindChannelCost ||
		eventKind == PlatformRelayDeliveryKindDownloadCompletion ||
		eventKind == PlatformRelayDeliveryKindTaskStage ||
		eventKind == PlatformRelayDeliveryKindOperationsSnapshot
}

func normalizePlatformRelayDeliveryResponseStatus(status int) int {
	if status < 100 || status > 599 {
		return 0
	}
	return status
}

func normalizePlatformRelayDeliveryFailure(failure string) string {
	switch failure {
	case PlatformRelayDeliveryFailureConfiguration,
		PlatformRelayDeliveryFailureTarget,
		PlatformRelayDeliveryFailurePayload,
		PlatformRelayDeliveryFailureTransport,
		PlatformRelayDeliveryFailureEndpoint,
		PlatformRelayDeliveryFailureConflict:
		return failure
	default:
		return PlatformRelayDeliveryFailureGeneric
	}
}
