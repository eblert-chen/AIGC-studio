package model

import (
	"crypto/sha256"
	"encoding/base64"
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

const (
	PlatformDownloadEdgeTicketPending   = "pending"
	PlatformDownloadEdgeTicketClaimed   = "claimed"
	PlatformDownloadEdgeTicketCompleted = "completed"
	PlatformDownloadEdgeTicketFailed    = "failed"
	PlatformDownloadEdgeTicketExpired   = "expired"
)

var (
	ErrPlatformDownloadEdgeTicketCollision   = errors.New("download edge registration id is already bound to another payload")
	ErrPlatformDownloadEdgeTicketUnavailable = errors.New("download edge ticket is unavailable")
	ErrPlatformDownloadEdgeSourceLifetime    = errors.New("download edge source URL lifetime is insufficient")
	ErrPlatformDownloadEdgeClaimLost         = errors.New("download edge ticket claim was lost")
	ErrPlatformDownloadCompletionImmutable   = errors.New("download completion evidence is append-only")
)

// PlatformDownloadEdgeTicket is the only mutable part of a download. The OBS
// signed URL is encrypted by the service before it reaches this model. The
// public bearer token is never stored; TokenSHA256 is sufficient for lookup.
type PlatformDownloadEdgeTicket struct {
	ID                        string     `json:"id" gorm:"type:varchar(36);primaryKey"`
	TokenSHA256               string     `json:"-" gorm:"type:char(64);not null;uniqueIndex"`
	RegistrationRequestID     string     `json:"registration_request_id" gorm:"type:varchar(36);not null;uniqueIndex"`
	RegistrationPayloadSHA256 string     `json:"registration_payload_sha256" gorm:"type:char(64);not null"`
	DownloadRecordID          string     `json:"download_record_id" gorm:"type:varchar(36);not null;index"`
	CompanyID                 string     `json:"company_id" gorm:"type:varchar(36);not null;index"`
	TaskID                    string     `json:"task_id" gorm:"type:varchar(36);not null;index"`
	AssetID                   string     `json:"asset_id" gorm:"type:varchar(160);not null;index"`
	ExpectedSizeBytes         int64      `json:"expected_size_bytes" gorm:"not null;check:chk_download_edge_ticket_size,expected_size_bytes > 0"`
	ArtifactSHA256            string     `json:"artifact_sha256" gorm:"type:char(64);not null"`
	OBSBucket                 string     `json:"obs_bucket" gorm:"type:varchar(63);not null"`
	OBSObjectKey              string     `json:"obs_object_key" gorm:"type:text;not null"`
	OBSVersionID              string     `json:"obs_version_id,omitempty" gorm:"type:varchar(256);not null"`
	IssuanceRequestID         string     `json:"issuance_request_id" gorm:"type:varchar(160);not null"`
	TransferReference         string     `json:"transfer_reference" gorm:"type:varchar(36);not null;uniqueIndex"`
	SourceURLSHA256           string     `json:"-" gorm:"type:char(64);not null"`
	SourceExpiresAt           time.Time  `json:"-" gorm:"not null;index"`
	SourceURLCiphertext       []byte     `json:"-" gorm:"not null"`
	SourceURLNonce            []byte     `json:"-" gorm:"not null"`
	State                     string     `json:"state" gorm:"type:varchar(16);not null;index"`
	ClaimToken                string     `json:"-" gorm:"type:varchar(36);not null"`
	ClaimedAt                 *time.Time `json:"-"`
	ClaimExpiresAt            *time.Time `json:"-" gorm:"index"`
	GatewayRequestID          string     `json:"gateway_request_id,omitempty" gorm:"type:varchar(160);not null"`
	FailureCode               string     `json:"failure_code,omitempty" gorm:"type:varchar(64);not null"`
	IssuedAt                  time.Time  `json:"issued_at" gorm:"not null"`
	ExpiresAt                 time.Time  `json:"expires_at" gorm:"not null;index"`
	CompletedAt               *time.Time `json:"completed_at,omitempty"`
	CreatedAt                 time.Time  `json:"created_at"`
	UpdatedAt                 time.Time  `json:"updated_at"`
}

func (PlatformDownloadEdgeTicket) TableName() string { return "platform_download_edge_tickets" }

// PlatformDownloadCompletionEvent is immutable transfer evidence. It contains
// no source URL, query string, service token, or signing key.
type PlatformDownloadCompletionEvent struct {
	ID                string    `json:"id" gorm:"type:varchar(36);primaryKey"`
	TicketID          string    `json:"ticket_id" gorm:"type:varchar(36);not null;uniqueIndex"`
	DownloadRecordID  string    `json:"download_record_id" gorm:"type:varchar(36);not null;index"`
	CompanyID         string    `json:"company_id" gorm:"type:varchar(36);not null;index"`
	TaskID            string    `json:"task_id" gorm:"type:varchar(36);not null;index"`
	AssetID           string    `json:"asset_id" gorm:"type:varchar(160);not null;index"`
	IssuanceRequestID string    `json:"issuance_request_id" gorm:"type:varchar(160);not null"`
	TransferReference string    `json:"transfer_reference" gorm:"type:varchar(36);not null"`
	GatewayRequestID  string    `json:"gateway_request_id" gorm:"type:varchar(160);not null"`
	OBSBucket         string    `json:"obs_bucket" gorm:"type:varchar(63);not null"`
	OBSObjectKey      string    `json:"obs_object_key" gorm:"type:text;not null"`
	OBSVersionID      string    `json:"obs_version_id,omitempty" gorm:"type:varchar(256);not null"`
	BytesSent         int64     `json:"bytes_sent" gorm:"not null;check:chk_download_completion_event_size,bytes_sent > 0 AND bytes_sent = expected_size_bytes"`
	ExpectedSizeBytes int64     `json:"expected_size_bytes" gorm:"not null"`
	ArtifactSHA256    string    `json:"artifact_sha256" gorm:"type:char(64);not null"`
	HTTPStatus        int       `json:"http_status" gorm:"not null;check:chk_download_completion_event_status,http_status = 200"`
	TransferScope     string    `json:"transfer_scope" gorm:"type:varchar(16);not null;check:chk_download_completion_event_scope,transfer_scope = 'full_body'"`
	CompletedAt       time.Time `json:"completed_at" gorm:"not null"`
	PayloadJSON       string    `json:"-" gorm:"type:text;not null"`
	PayloadSHA256     string    `json:"payload_sha256" gorm:"type:char(64);not null"`
	CreatedAt         time.Time `json:"created_at"`
}

func (PlatformDownloadCompletionEvent) TableName() string {
	return "platform_download_completion_events"
}

func (*PlatformDownloadCompletionEvent) BeforeUpdate(*gorm.DB) error {
	return ErrPlatformDownloadCompletionImmutable
}
func (*PlatformDownloadCompletionEvent) BeforeDelete(*gorm.DB) error {
	return ErrPlatformDownloadCompletionImmutable
}

// PlatformDownloadCompletionProof stores the exact canonical bytes signed by
// the external producer. Both this row and the transfer event are append-only;
// retry/dead-letter state remains in PlatformRelayExternalDelivery.
type PlatformDownloadCompletionProof struct {
	EventID         string    `json:"event_id" gorm:"type:varchar(36);primaryKey"`
	CompletionID    string    `json:"completion_id" gorm:"type:varchar(36);not null;uniqueIndex"`
	KeyID           string    `json:"key_id" gorm:"type:varchar(120);not null"`
	PayloadJSON     string    `json:"-" gorm:"type:text;not null"`
	PayloadSHA256   string    `json:"payload_sha256" gorm:"type:char(64);not null"`
	SignatureBase64 string    `json:"signature_base64" gorm:"type:varchar(128);not null"`
	ProducedAt      time.Time `json:"produced_at" gorm:"not null"`
	CreatedAt       time.Time `json:"created_at"`
}

func (PlatformDownloadCompletionProof) TableName() string {
	return "platform_download_completion_proofs"
}

func (*PlatformDownloadCompletionProof) BeforeUpdate(*gorm.DB) error {
	return ErrPlatformDownloadCompletionImmutable
}
func (*PlatformDownloadCompletionProof) BeforeDelete(*gorm.DB) error {
	return ErrPlatformDownloadCompletionImmutable
}

type PlatformDownloadEdgeTicketClaim struct {
	Ticket PlatformDownloadEdgeTicket
	Token  string
}

type platformDownloadCompletionPayload struct {
	DownloadRecordID         string    `json:"download_record_id"`
	CompanyID                string    `json:"company_id"`
	TaskID                   string    `json:"task_id"`
	AssetID                  string    `json:"asset_id"`
	ExternalEventID          string    `json:"external_event_id"`
	BytesSent                int64     `json:"bytes_sent"`
	CompletedAt              time.Time `json:"completed_at"`
	ArtifactSHA256           string    `json:"artifact_sha256"`
	ExpectedSizeBytes        int64     `json:"expected_size_bytes"`
	HTTPStatus               int       `json:"http_status"`
	TransferScope            string    `json:"transfer_scope"`
	GatewayRequestID         string    `json:"gateway_request_id"`
	GatewayTransferReference string    `json:"gateway_transfer_reference"`
}

func GetPlatformDownloadEdgeTicketForReplay(registrationRequestID, payloadSHA256 string) (*PlatformDownloadEdgeTicket, bool, error) {
	if !canonicalUUID(registrationRequestID) || !validLowerHexDigest(payloadSHA256) {
		return nil, false, ErrPlatformDownloadEdgeTicketCollision
	}
	var ticket PlatformDownloadEdgeTicket
	expired := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		if err := tx.Where("registration_request_id = ?", registrationRequestID).First(&ticket).Error; err != nil {
			return err
		}
		if ticket.RegistrationPayloadSHA256 != payloadSHA256 {
			return ErrPlatformDownloadEdgeTicketCollision
		}
		expired = !ticket.ExpiresAt.After(now)
		return nil
	})
	if err != nil {
		return nil, false, err
	}
	return &ticket, expired, nil
}

func CreatePlatformDownloadEdgeTicket(ticket *PlatformDownloadEdgeTicket, ttl, sourceSafetyMargin time.Duration) (bool, error) {
	if err := validatePlatformDownloadEdgeTicket(ticket); err != nil {
		return false, err
	}
	if ttl < 30*time.Second || ttl > time.Hour {
		return false, fmt.Errorf("download edge ticket TTL is invalid")
	}
	if sourceSafetyMargin < time.Second || sourceSafetyMargin > 15*time.Minute {
		return false, fmt.Errorf("download edge source expiry safety margin is invalid")
	}
	created := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		var existing PlatformDownloadEdgeTicket
		existingErr := tx.Where("registration_request_id = ?", ticket.RegistrationRequestID).First(&existing).Error
		if existingErr == nil {
			if !platformDownloadEdgeTicketRegistrationEqual(existing, *ticket) {
				return ErrPlatformDownloadEdgeTicketCollision
			}
			*ticket = existing
			return nil
		}
		if !errors.Is(existingErr, gorm.ErrRecordNotFound) {
			return existingErr
		}
		maximumLifetime := ticket.SourceExpiresAt.Sub(now) - sourceSafetyMargin
		effectiveLifetime := ttl
		if maximumLifetime < effectiveLifetime {
			effectiveLifetime = maximumLifetime
		}
		effectiveSeconds := int64(effectiveLifetime / time.Second)
		if effectiveSeconds < 30 {
			return ErrPlatformDownloadEdgeSourceLifetime
		}
		ticket.State = PlatformDownloadEdgeTicketPending
		ticket.IssuedAt = now
		ticket.ExpiresAt = now.Add(time.Duration(effectiveSeconds) * time.Second)
		ticket.CreatedAt = now
		ticket.UpdatedAt = now
		result := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(ticket)
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected == 1 {
			created = true
			return nil
		}
		if err := tx.Where("registration_request_id = ? OR token_sha256 = ?", ticket.RegistrationRequestID, ticket.TokenSHA256).First(&existing).Error; err != nil {
			return err
		}
		if !platformDownloadEdgeTicketRegistrationEqual(existing, *ticket) {
			return ErrPlatformDownloadEdgeTicketCollision
		}
		*ticket = existing
		return nil
	})
	return created, err
}

func ClaimPlatformDownloadEdgeTicket(tokenSHA256 string, lease time.Duration) (*PlatformDownloadEdgeTicketClaim, error) {
	if !validLowerHexDigest(tokenSHA256) || lease < time.Second {
		return nil, ErrPlatformDownloadEdgeTicketUnavailable
	}
	var claim *PlatformDownloadEdgeTicketClaim
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		var ticket PlatformDownloadEdgeTicket
		query := lockForUpdate(tx.Where("token_sha256 = ?", tokenSHA256))
		if err := query.First(&ticket).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				return ErrPlatformDownloadEdgeTicketUnavailable
			}
			return err
		}
		if ticket.State != PlatformDownloadEdgeTicketPending || !ticket.ExpiresAt.After(now) {
			if ticket.State == PlatformDownloadEdgeTicketPending && !ticket.ExpiresAt.After(now) {
				if err := tx.Model(&PlatformDownloadEdgeTicket{}).Where("id = ? AND state = ?", ticket.ID, PlatformDownloadEdgeTicketPending).Updates(map[string]any{
					"state": PlatformDownloadEdgeTicketExpired, "updated_at": now,
				}).Error; err != nil {
					return err
				}
			}
			return ErrPlatformDownloadEdgeTicketUnavailable
		}
		token := uuid.NewString()
		expiresAt := now.Add(lease)
		result := tx.Model(&PlatformDownloadEdgeTicket{}).Where("id = ? AND state = ?", ticket.ID, PlatformDownloadEdgeTicketPending).Updates(map[string]any{
			"state": PlatformDownloadEdgeTicketClaimed, "claim_token": token,
			"claimed_at": now, "claim_expires_at": expiresAt,
			"gateway_request_id": ticket.IssuanceRequestID, "updated_at": now,
		})
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return ErrPlatformDownloadEdgeTicketUnavailable
		}
		ticket.State = PlatformDownloadEdgeTicketClaimed
		ticket.ClaimToken = token
		ticket.ClaimedAt = &now
		ticket.ClaimExpiresAt = &expiresAt
		ticket.GatewayRequestID = ticket.IssuanceRequestID
		claim = &PlatformDownloadEdgeTicketClaim{Ticket: ticket, Token: token}
		return nil
	})
	return claim, err
}

func FailPlatformDownloadEdgeTicket(ticketID, claimToken, failureCode string) (bool, error) {
	if !canonicalUUID(ticketID) || !canonicalUUID(claimToken) || failureCode == "" || len(failureCode) > 64 || strings.TrimSpace(failureCode) != failureCode {
		return false, fmt.Errorf("download edge ticket failure is invalid")
	}
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		result := tx.Model(&PlatformDownloadEdgeTicket{}).Where(
			"id = ? AND state = ? AND claim_token = ? AND "+unexpiredDatabaseLeasePredicate("claim_expires_at"),
			ticketID, PlatformDownloadEdgeTicketClaimed, claimToken,
		).Updates(map[string]any{
			"state": PlatformDownloadEdgeTicketFailed, "claim_token": "",
			"claimed_at": nil, "claim_expires_at": nil,
			"failure_code": failureCode, "updated_at": now,
		})
		won = result.RowsAffected == 1
		return result.Error
	})
	return won, err
}

func FinalizePlatformDownloadEdgeTransfer(ticketID, claimToken, eventID string, maxDeliveryAttempts int) (*PlatformDownloadCompletionEvent, error) {
	if !canonicalUUID(ticketID) || !canonicalUUID(claimToken) || !canonicalUUID(eventID) {
		return nil, fmt.Errorf("download completion identity is invalid")
	}
	var event PlatformDownloadCompletionEvent
	err := DB.Transaction(func(tx *gorm.DB) error {
		var ticket PlatformDownloadEdgeTicket
		query := lockForUpdate(tx.Where(
			"id = ? AND state = ? AND claim_token = ?",
			ticketID, PlatformDownloadEdgeTicketClaimed, claimToken,
		))
		if err := query.First(&ticket).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				return ErrPlatformDownloadEdgeClaimLost
			}
			return err
		}
		completedAt, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		payload := platformDownloadCompletionPayload{
			DownloadRecordID: ticket.DownloadRecordID, CompanyID: ticket.CompanyID,
			TaskID: ticket.TaskID, AssetID: ticket.AssetID, ExternalEventID: eventID,
			BytesSent: ticket.ExpectedSizeBytes, CompletedAt: completedAt.UTC(),
			ArtifactSHA256: ticket.ArtifactSHA256, ExpectedSizeBytes: ticket.ExpectedSizeBytes,
			HTTPStatus: 200, TransferScope: "full_body", GatewayRequestID: ticket.GatewayRequestID,
			GatewayTransferReference: ticket.TransferReference,
		}
		raw, err := json.Marshal(payload)
		if err != nil {
			return err
		}
		digest := sha256.Sum256(raw)
		event = PlatformDownloadCompletionEvent{
			ID: eventID, TicketID: ticket.ID, DownloadRecordID: ticket.DownloadRecordID,
			CompanyID: ticket.CompanyID, TaskID: ticket.TaskID, AssetID: ticket.AssetID,
			IssuanceRequestID: ticket.IssuanceRequestID, TransferReference: ticket.TransferReference,
			GatewayRequestID: ticket.GatewayRequestID, OBSBucket: ticket.OBSBucket,
			OBSObjectKey: ticket.OBSObjectKey, OBSVersionID: ticket.OBSVersionID,
			BytesSent: ticket.ExpectedSizeBytes, ExpectedSizeBytes: ticket.ExpectedSizeBytes,
			ArtifactSHA256: ticket.ArtifactSHA256, HTTPStatus: 200, TransferScope: "full_body",
			CompletedAt: completedAt.UTC(), PayloadJSON: string(raw), PayloadSHA256: hex.EncodeToString(digest[:]),
			CreatedAt: completedAt,
		}
		if err := tx.Create(&event).Error; err != nil {
			return err
		}
		if _, err := CreatePlatformRelayExternalDeliveryTx(
			tx, PlatformRelayDeliveryKindDownloadCompletion, event.ID,
			"edge-download-completion-"+event.ID, maxDeliveryAttempts,
		); err != nil {
			return err
		}
		// Keep the token/lease fence as the final state-changing statement. If
		// event construction or outbox insertion crosses the lease boundary,
		// this update loses and the transaction rolls every derived row back.
		result := tx.Model(&PlatformDownloadEdgeTicket{}).Where(
			"id = ? AND state = ? AND claim_token = ? AND "+unexpiredDatabaseLeasePredicate("claim_expires_at"),
			ticket.ID, PlatformDownloadEdgeTicketClaimed, claimToken,
		).Updates(map[string]any{
			"state": PlatformDownloadEdgeTicketCompleted, "claim_token": "",
			"claimed_at": nil, "claim_expires_at": nil, "completed_at": completedAt, "updated_at": completedAt,
		})
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return ErrPlatformDownloadEdgeClaimLost
		}
		return nil
	})
	return &event, err
}

func GetPlatformDownloadCompletionEvent(eventID string) (*PlatformDownloadCompletionEvent, error) {
	var event PlatformDownloadCompletionEvent
	err := DB.Where("id = ?", eventID).First(&event).Error
	return &event, err
}

func GetPlatformDownloadCompletionProof(eventID string) (*PlatformDownloadCompletionProof, error) {
	var proof PlatformDownloadCompletionProof
	err := DB.Where("event_id = ?", eventID).First(&proof).Error
	return &proof, err
}

type PlatformDownloadCompletionProofBuilder func(deliveredAt time.Time) (*PlatformDownloadCompletionProof, error)

// CompletePlatformDownloadCompletionDelivery commits the immutable detached
// proof and the mutable outbox delivery state atomically under the claim fence.
func CompletePlatformDownloadCompletionDelivery(
	eventID, claimToken string,
	responseStatus int,
	builder PlatformDownloadCompletionProofBuilder,
) (bool, error) {
	if !canonicalUUID(eventID) || !canonicalUUID(claimToken) || responseStatus != 201 || builder == nil {
		return false, fmt.Errorf("download completion delivery result is invalid")
	}
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		var delivery PlatformRelayExternalDelivery
		query := lockForUpdate(tx.Where(
			"event_kind = ? AND event_id = ? AND state = ? AND claim_token = ?",
			PlatformRelayDeliveryKindDownloadCompletion, eventID,
			PlatformRelayDeliveryClaimed, claimToken,
		))
		if err := query.First(&delivery).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				return nil
			}
			return err
		}
		deliveredAt, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		proof, err := builder(deliveredAt.UTC())
		if err != nil {
			return err
		}
		if err := validatePlatformDownloadCompletionProof(proof, eventID, deliveredAt); err != nil {
			return err
		}
		proof.CreatedAt = deliveredAt
		if err := tx.Create(proof).Error; err != nil {
			return err
		}
		// The proof is provisional until the final database-clock fence wins.
		// A lost/expired claim rolls both the proof and this state update back.
		result := tx.Model(&PlatformRelayExternalDelivery{}).Where(
			"id = ? AND state = ? AND claim_token = ? AND "+unexpiredDatabaseLeasePredicate("claim_expires_at"),
			delivery.ID, PlatformRelayDeliveryClaimed, claimToken,
		).Updates(map[string]any{
			"state": PlatformRelayDeliveryDelivered, "claim_token": "",
			"claimed_at": nil, "claim_expires_at": nil, "response_status": responseStatus,
			"last_error": "", "delivered_at": deliveredAt, "updated_at": deliveredAt,
		})
		if result.Error != nil {
			return result.Error
		}
		won = result.RowsAffected == 1
		if !won {
			return ErrPlatformDownloadEdgeClaimLost
		}
		return nil
	})
	return won, err
}

func validatePlatformDownloadEdgeTicket(ticket *PlatformDownloadEdgeTicket) error {
	if ticket == nil || !canonicalUUID(ticket.ID) || !canonicalUUID(ticket.RegistrationRequestID) ||
		!canonicalUUID(ticket.DownloadRecordID) || !canonicalUUID(ticket.CompanyID) || !canonicalUUID(ticket.TaskID) ||
		!canonicalUUID(ticket.TransferReference) || !validLowerHexDigest(ticket.TokenSHA256) ||
		!validLowerHexDigest(ticket.RegistrationPayloadSHA256) || !validLowerHexDigest(ticket.ArtifactSHA256) ||
		!validLowerHexDigest(ticket.SourceURLSHA256) {
		return fmt.Errorf("download edge ticket identity is invalid")
	}
	if ticket.AssetID == "" || len(ticket.AssetID) > 160 || strings.TrimSpace(ticket.AssetID) != ticket.AssetID ||
		ticket.ExpectedSizeBytes <= 0 || ticket.OBSBucket == "" || len(ticket.OBSBucket) > 63 ||
		ticket.OBSObjectKey == "" || len(ticket.OBSObjectKey) > 1024 || len(ticket.OBSVersionID) > 256 ||
		ticket.IssuanceRequestID == "" || len(ticket.IssuanceRequestID) > 160 || strings.TrimSpace(ticket.IssuanceRequestID) != ticket.IssuanceRequestID ||
		ticket.SourceExpiresAt.IsZero() || len(ticket.SourceURLCiphertext) < 16 || len(ticket.SourceURLNonce) != 12 {
		return fmt.Errorf("download edge ticket fields are invalid")
	}
	return nil
}

func platformDownloadEdgeTicketRegistrationEqual(left, right PlatformDownloadEdgeTicket) bool {
	return left.RegistrationRequestID == right.RegistrationRequestID &&
		left.RegistrationPayloadSHA256 == right.RegistrationPayloadSHA256 &&
		left.DownloadRecordID == right.DownloadRecordID && left.CompanyID == right.CompanyID &&
		left.TaskID == right.TaskID && left.AssetID == right.AssetID &&
		left.ExpectedSizeBytes == right.ExpectedSizeBytes && left.ArtifactSHA256 == right.ArtifactSHA256 &&
		left.OBSBucket == right.OBSBucket && left.OBSObjectKey == right.OBSObjectKey &&
		left.OBSVersionID == right.OBSVersionID && left.IssuanceRequestID == right.IssuanceRequestID &&
		left.TransferReference == right.TransferReference && left.SourceURLSHA256 == right.SourceURLSHA256 &&
		left.SourceExpiresAt.Equal(right.SourceExpiresAt)
}

func validatePlatformDownloadCompletionProof(proof *PlatformDownloadCompletionProof, eventID string, now time.Time) error {
	if proof == nil || proof.EventID != eventID || !canonicalUUID(proof.EventID) || !canonicalUUID(proof.CompletionID) ||
		proof.KeyID == "" || len(proof.KeyID) > 120 || strings.TrimSpace(proof.KeyID) != proof.KeyID ||
		!validLowerHexDigest(proof.PayloadSHA256) || proof.PayloadJSON == "" || proof.ProducedAt.IsZero() || !proof.ProducedAt.Equal(now.UTC()) {
		return fmt.Errorf("download completion proof is invalid")
	}
	digest := sha256.Sum256([]byte(proof.PayloadJSON))
	if hex.EncodeToString(digest[:]) != proof.PayloadSHA256 {
		return fmt.Errorf("download completion proof payload digest is invalid")
	}
	signature, err := base64.StdEncoding.DecodeString(proof.SignatureBase64)
	if err != nil || len(signature) != 64 {
		return fmt.Errorf("download completion proof signature is invalid")
	}
	return nil
}

func canonicalUUID(value string) bool {
	parsed, err := uuid.Parse(value)
	return err == nil && parsed.String() == value
}

func validLowerHexDigest(value string) bool {
	if len(value) != 64 || strings.ToLower(value) != value {
		return false
	}
	decoded, err := hex.DecodeString(value)
	return err == nil && len(decoded) == sha256.Size
}
