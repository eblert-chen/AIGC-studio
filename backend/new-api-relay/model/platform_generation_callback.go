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
	PlatformGenerationCallbackPending    = "pending"
	PlatformGenerationCallbackClaimed    = "claimed"
	PlatformGenerationCallbackDelivered  = "delivered"
	PlatformGenerationCallbackDeadLetter = "dead_letter"
)

const (
	PlatformGenerationCallbackFailureConfiguration = "configuration_rejected"
	PlatformGenerationCallbackFailureTarget        = "target_rejected"
	PlatformGenerationCallbackFailurePayload       = "payload_rejected"
	PlatformGenerationCallbackFailureTransport     = "transport_failed"
	PlatformGenerationCallbackFailureEndpoint      = "endpoint_rejected"
	PlatformGenerationCallbackFailureGeneric       = "delivery_failed"
)

const DefaultPlatformGenerationCallbackMaxAttempts = 8

var ErrPlatformGenerationCallbackEventCollision = errors.New("generation callback event id collision")

// PlatformGenerationCallbackDelivery is a durable, at-least-once delivery.
// ID is the stable event UUID used by the receiver for idempotency. Secrets are
// deliberately not persisted; workers resolve them from the authenticated
// client binding when an item is claimed.
type PlatformGenerationCallbackDelivery struct {
	ID             string     `json:"id" gorm:"type:varchar(36);primaryKey"`
	TenantID       string     `json:"tenant_id" gorm:"type:varchar(36);not null;index:idx_platform_generation_callback_tenant,priority:1"`
	SourceClientID string     `json:"source_client_id" gorm:"type:varchar(128);not null"`
	JobID          string     `json:"job_id" gorm:"type:varchar(36);not null;index:idx_platform_generation_callback_job"`
	CallbackURL    string     `json:"-" gorm:"type:text;not null"`
	RequestID      string     `json:"request_id" gorm:"type:varchar(80);not null"`
	PayloadJSON    string     `json:"-" gorm:"type:text;not null"`
	PayloadSHA256  string     `json:"payload_sha256" gorm:"type:varchar(64);not null"`
	State          string     `json:"state" gorm:"type:varchar(16);not null;index:idx_platform_generation_callback_available,priority:1;index:idx_platform_generation_callback_tenant,priority:2"`
	Attempts       int        `json:"attempts" gorm:"not null"`
	MaxAttempts    int        `json:"max_attempts" gorm:"not null"`
	AvailableAt    time.Time  `json:"available_at" gorm:"not null;index:idx_platform_generation_callback_available,priority:2"`
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

type PlatformGenerationCallbackClaim struct {
	Delivery PlatformGenerationCallbackDelivery
	Token    string
}

type PlatformGenerationCallbackCounts struct {
	Pending    int64 `json:"pending"`
	Claimed    int64 `json:"claimed"`
	Delivered  int64 `json:"delivered"`
	DeadLetter int64 `json:"dead_letter"`
}

func GetPlatformGenerationCallbackCounts() (PlatformGenerationCallbackCounts, error) {
	var counts PlatformGenerationCallbackCounts
	for state, target := range map[string]*int64{
		PlatformGenerationCallbackPending:    &counts.Pending,
		PlatformGenerationCallbackClaimed:    &counts.Claimed,
		PlatformGenerationCallbackDelivered:  &counts.Delivered,
		PlatformGenerationCallbackDeadLetter: &counts.DeadLetter,
	} {
		if err := DB.Model(&PlatformGenerationCallbackDelivery{}).Where("state = ?", state).Count(target).Error; err != nil {
			return counts, err
		}
	}
	return counts, nil
}

func CreatePlatformGenerationCallbackDelivery(delivery *PlatformGenerationCallbackDelivery) (bool, error) {
	created := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		var err error
		created, err = CreatePlatformGenerationCallbackDeliveryTx(tx, delivery)
		return err
	})
	return created, err
}

// CreatePlatformGenerationCallbackDeliveryTx lets a generation state change
// and its callback event commit atomically in the same database transaction.
func CreatePlatformGenerationCallbackDeliveryTx(tx *gorm.DB, delivery *PlatformGenerationCallbackDelivery) (bool, error) {
	if tx == nil || delivery == nil {
		return false, fmt.Errorf("generation callback delivery is required")
	}
	if _, err := uuid.Parse(delivery.ID); err != nil {
		return false, fmt.Errorf("generation callback event id is invalid")
	}
	if _, err := uuid.Parse(delivery.TenantID); err != nil {
		return false, fmt.Errorf("generation callback tenant id is invalid")
	}
	if _, err := uuid.Parse(delivery.JobID); err != nil {
		return false, fmt.Errorf("generation callback job id is invalid")
	}
	if strings.TrimSpace(delivery.SourceClientID) == "" || strings.TrimSpace(delivery.CallbackURL) == "" ||
		strings.TrimSpace(delivery.RequestID) == "" || strings.TrimSpace(delivery.PayloadJSON) == "" ||
		len(delivery.PayloadSHA256) != 64 {
		return false, fmt.Errorf("generation callback delivery is incomplete")
	}

	now, err := GetDBTimeTx(tx)
	if err != nil {
		return false, err
	}
	if delivery.MaxAttempts < 1 {
		delivery.MaxAttempts = DefaultPlatformGenerationCallbackMaxAttempts
	}
	if delivery.MaxAttempts > 100 {
		return false, fmt.Errorf("generation callback max attempts exceeds 100")
	}
	if delivery.AvailableAt.IsZero() {
		delivery.AvailableAt = now
	}
	delivery.State = PlatformGenerationCallbackPending
	delivery.Attempts = 0
	delivery.ClaimToken = ""
	delivery.ClaimedAt = nil
	delivery.ClaimExpiresAt = nil
	delivery.ResponseStatus = 0
	delivery.LastError = ""
	delivery.DeliveredAt = nil
	delivery.DeadLetteredAt = nil
	delivery.CreatedAt = now
	delivery.UpdatedAt = now

	result := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(delivery)
	if result.Error != nil {
		return false, result.Error
	}
	if result.RowsAffected == 1 {
		return true, nil
	}

	var existing PlatformGenerationCallbackDelivery
	if err := tx.Where("id = ?", delivery.ID).First(&existing).Error; err != nil {
		return false, err
	}
	if existing.TenantID != delivery.TenantID || existing.SourceClientID != delivery.SourceClientID ||
		existing.JobID != delivery.JobID || existing.CallbackURL != delivery.CallbackURL ||
		existing.RequestID != delivery.RequestID || existing.PayloadSHA256 != delivery.PayloadSHA256 ||
		existing.PayloadJSON != delivery.PayloadJSON {
		return false, ErrPlatformGenerationCallbackEventCollision
	}
	*delivery = existing
	return false, nil
}

// ClaimPlatformGenerationCallbackDelivery claims exactly one delivery. The
// database clock is authoritative and every claim gets a random fencing token.
func ClaimPlatformGenerationCallbackDelivery(lease time.Duration) (*PlatformGenerationCallbackClaim, error) {
	if lease < time.Second {
		return nil, fmt.Errorf("generation callback lease must be at least one second")
	}
	var claim *PlatformGenerationCallbackClaim
	noClaim := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}

		// A worker may crash after consuming the final attempt. Once its lease
		// expires, finalize that row instead of repeatedly reclaiming it.
		if err := tx.Model(&PlatformGenerationCallbackDelivery{}).Where(
			"attempts >= max_attempts AND (state = ? OR (state = ? AND claim_expires_at <= ?))",
			PlatformGenerationCallbackPending,
			PlatformGenerationCallbackClaimed,
			now,
		).Updates(map[string]any{
			"state":            PlatformGenerationCallbackDeadLetter,
			"claim_token":      "",
			"claimed_at":       nil,
			"claim_expires_at": nil,
			"last_error":       PlatformGenerationCallbackFailureGeneric,
			"dead_lettered_at": now,
		}).Error; err != nil {
			return err
		}

		var delivery PlatformGenerationCallbackDelivery
		query := tx.Where(
			"available_at <= ? AND attempts < max_attempts AND (state = ? OR (state = ? AND claim_expires_at <= ?))",
			now,
			PlatformGenerationCallbackPending,
			PlatformGenerationCallbackClaimed,
			now,
		).Order("available_at ASC, created_at ASC, id ASC")
		query = lockForUpdate(query)
		if err := query.First(&delivery).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				// Commit final-attempt cleanup above before reporting an empty
				// queue to the caller.
				noClaim = true
				return nil
			}
			return err
		}

		token := uuid.NewString()
		expiresAt := now.Add(lease)
		if err := tx.Model(&delivery).Updates(map[string]any{
			"state":            PlatformGenerationCallbackClaimed,
			"attempts":         gorm.Expr("attempts + 1"),
			"claim_token":      token,
			"claimed_at":       now,
			"claim_expires_at": expiresAt,
		}).Error; err != nil {
			return err
		}
		delivery.State = PlatformGenerationCallbackClaimed
		delivery.Attempts++
		delivery.ClaimToken = token
		delivery.ClaimedAt = &now
		delivery.ClaimExpiresAt = &expiresAt
		claim = &PlatformGenerationCallbackClaim{Delivery: delivery, Token: token}
		return nil
	})
	if err == nil && noClaim {
		return nil, gorm.ErrRecordNotFound
	}
	return claim, err
}

func CompletePlatformGenerationCallbackDelivery(deliveryID string, token string, responseStatus int) (bool, error) {
	if responseStatus < 200 || responseStatus >= 300 {
		return false, fmt.Errorf("generation callback success status is invalid")
	}
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		result := tx.Model(&PlatformGenerationCallbackDelivery{}).Where(
			"id = ? AND state = ? AND claim_token = ? AND claim_expires_at > ?",
			deliveryID,
			PlatformGenerationCallbackClaimed,
			token,
			now,
		).Updates(map[string]any{
			"state":            PlatformGenerationCallbackDelivered,
			"claim_token":      "",
			"claimed_at":       nil,
			"claim_expires_at": nil,
			"response_status":  responseStatus,
			"last_error":       "",
			"delivered_at":     now,
		})
		won = result.RowsAffected == 1
		return result.Error
	})
	return won, err
}

// ReleasePlatformGenerationCallbackDelivery returns a failed claim to the
// queue, or moves it to dead-letter state once its durable attempt budget is
// exhausted. Only the current, unexpired claim token may make the transition.
func ReleasePlatformGenerationCallbackDelivery(
	deliveryID string,
	token string,
	retryDelay time.Duration,
	failure string,
	responseStatus int,
) (string, bool, error) {
	if retryDelay < 0 {
		return "", false, fmt.Errorf("generation callback retry delay cannot be negative")
	}
	state := ""
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		var delivery PlatformGenerationCallbackDelivery
		query := lockForUpdate(tx.Where(
			"id = ? AND state = ? AND claim_token = ? AND claim_expires_at > ?",
			deliveryID,
			PlatformGenerationCallbackClaimed,
			token,
			now,
		))
		if err := query.First(&delivery).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				return nil
			}
			return err
		}

		state = PlatformGenerationCallbackPending
		updates := map[string]any{
			"state":            state,
			"available_at":     now.Add(retryDelay),
			"claim_token":      "",
			"claimed_at":       nil,
			"claim_expires_at": nil,
			"response_status":  normalizePlatformGenerationCallbackResponseStatus(responseStatus),
			"last_error":       normalizePlatformGenerationCallbackFailure(failure),
		}
		if delivery.Attempts >= delivery.MaxAttempts {
			state = PlatformGenerationCallbackDeadLetter
			updates["state"] = state
			updates["dead_lettered_at"] = now
		}
		result := tx.Model(&PlatformGenerationCallbackDelivery{}).Where(
			"id = ? AND state = ? AND claim_token = ?",
			deliveryID,
			PlatformGenerationCallbackClaimed,
			token,
		).Updates(updates)
		won = result.RowsAffected == 1
		return result.Error
	})
	return state, won, err
}

func DeadLetterPlatformGenerationCallbackDelivery(
	deliveryID string,
	token string,
	failure string,
	responseStatus int,
) (bool, error) {
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		result := tx.Model(&PlatformGenerationCallbackDelivery{}).Where(
			"id = ? AND state = ? AND claim_token = ? AND claim_expires_at > ?",
			deliveryID,
			PlatformGenerationCallbackClaimed,
			token,
			now,
		).Updates(map[string]any{
			"state":            PlatformGenerationCallbackDeadLetter,
			"claim_token":      "",
			"claimed_at":       nil,
			"claim_expires_at": nil,
			"response_status":  normalizePlatformGenerationCallbackResponseStatus(responseStatus),
			"last_error":       normalizePlatformGenerationCallbackFailure(failure),
			"dead_lettered_at": now,
		})
		won = result.RowsAffected == 1
		return result.Error
	})
	return won, err
}

func normalizePlatformGenerationCallbackResponseStatus(status int) int {
	if status < 100 || status > 599 {
		return 0
	}
	return status
}

func normalizePlatformGenerationCallbackFailure(failure string) string {
	switch failure {
	case PlatformGenerationCallbackFailureConfiguration,
		PlatformGenerationCallbackFailureTarget,
		PlatformGenerationCallbackFailurePayload,
		PlatformGenerationCallbackFailureTransport,
		PlatformGenerationCallbackFailureEndpoint:
		return failure
	default:
		return PlatformGenerationCallbackFailureGeneric
	}
}
