package toc

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"github.com/QuantumNous/new-api/common"
	"time"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"gorm.io/gorm"
)

// RecordProviderCost records provider CNY evidence, never customer POINT spend.
// The receipt and local-delivery acknowledgement share the caller's transaction.
func RecordProviderCost(tx *gorm.DB, jobID string, event model.PlatformChannelCostEvent) error {
	if tx == nil || jobID == "" || event.RelayJobID != jobID {
		return fmt.Errorf("TOC cost job mismatch")
	}
	computed := sha256.Sum256([]byte(event.PayloadJSON))
	if hex.EncodeToString(computed[:]) != event.PayloadSHA256 {
		return fmt.Errorf("TOC cost payload digest mismatch")
	}
	var payload dto.PlatformChannelCostPayload
	if err := strictJSON([]byte(event.PayloadJSON), &payload); err != nil {
		return err
	}
	var job model.PlatformGenerationJob
	if err := tx.First(&job, "id = ?", jobID).Error; err != nil {
		return err
	}
	authority, err := model.LoadRelayGenerationAuthorityWithDB(tx, job)
	if err != nil || authority == nil {
		return fmt.Errorf("TOC cost authority missing")
	}
	var task Task
	if err = tx.First(&task, "id = ? AND user_id = ? AND admission_id = ?", jobID, authority.OwnerUserID, authority.AdmissionID).Error; err != nil {
		return err
	}
	if event.TaskID != jobID || payload.TaskID != jobID || payload.RelayJobID != jobID || event.CompanyID != "" || payload.CompanyID != "" || payload.PersonalWorkspaceID != workspace(task.UserID) || event.PersonalWorkspaceID != workspace(task.UserID) || payload.AmountCents != event.AmountCents || payload.IdempotencyKey != event.IdempotencyKey || payload.ChannelKey != event.ChannelKey || payload.EvidenceSource != event.EvidenceSource || payload.EvidenceReference != event.EvidenceReference || payload.SourceDocumentSHA256 != event.SourceDocumentSHA256 {
		return fmt.Errorf("TOC cost attribution mismatch")
	}
	if payload.ChannelType != event.ChannelType || !payload.OccurredAt.Equal(event.OccurredAt) || payload.ExternalReference != event.ExternalReference || payload.Note != event.Note {
		return fmt.Errorf("TOC cost envelope differs from signed payload")
	}
	input := dto.PlatformChannelCostInput{SchemaVersion: payload.SchemaVersion, EventID: event.ID, AmountCents: payload.AmountCents, IdempotencyKey: payload.IdempotencyKey, ChannelKey: payload.ChannelKey, ChannelType: payload.ChannelType, RouteID: payload.RouteID, OccurredAt: payload.OccurredAt, ExternalReference: payload.ExternalReference, CompanyID: payload.CompanyID, PersonalWorkspaceID: payload.PersonalWorkspaceID, TaskID: payload.TaskID, RelayJobID: payload.RelayJobID, Note: payload.Note, EvidenceSource: payload.EvidenceSource, EvidenceReference: payload.EvidenceReference, SourceDocumentSHA256: payload.SourceDocumentSHA256, ExecutionContractSHA256: payload.ExecutionContractSHA256, ProviderCostRevisionSHA256: payload.ProviderCostRevisionSHA256, PlatformProviderRouteIdentity: payload.PlatformProviderRouteIdentity}
	if err = input.Validate(); err != nil {
		return err
	}
	var request dto.PlatformGenerationRequest
	if err = common.Unmarshal([]byte(job.RequestJSON), &request); err != nil || request.ExecutionContract == nil {
		return fmt.Errorf("TOC cost execution contract missing")
	}
	contractSHA, err := request.ExecutionContract.Digest()
	if err != nil || contractSHA != payload.ExecutionContractSHA256 || request.ExecutionContract.RoutingReleaseSHA256 != payload.RoutingReleaseSHA256 || payload.ProviderRouteID != job.ProviderRouteID || payload.ProviderChannelID != job.ProviderChannelID || payload.ProviderKeyIndex != job.ProviderKeyIndex {
		return fmt.Errorf("TOC cost route pin mismatch")
	}
	var route model.PlatformGenerationProviderRoute
	if err = tx.First(&route, "id = ?", job.ProviderRouteID).Error; err != nil {
		return err
	}
	if route.KeyFingerprint != payload.ProviderKeyFingerprint || route.ProviderName != payload.ProviderName || route.AccountID != payload.ProviderAccountID || route.ChannelID != payload.ProviderChannelID || route.RouteKey != payload.RouteKey {
		return fmt.Errorf("TOC cost physical account mismatch")
	}
	matched := false
	for _, pin := range request.ExecutionContract.Routes {
		if pin.RouteID == payload.RouteKey && pin.ChannelID == payload.ProviderChannelID && pin.ProviderAccountID == payload.ProviderAccountID && pin.CostSHA256 == payload.ProviderCostRevisionSHA256 {
			reference := model.PlatformGenerationNativeTaskCredentialReference{TenantID: job.TenantID, Version: payload.ProviderCredentialVersion, ChannelID: payload.ProviderChannelID, KeyIndex: payload.ProviderKeyIndex, KeyFingerprint: payload.ProviderKeyFingerprint}
			if err := model.ValidateProviderCredentialSetMemberWithDB(tx, pin.ProviderCredentialSetVersion, reference); err != nil {
				return fmt.Errorf("TOC cost credential membership mismatch: %w", err)
			}
			matched = true
		}
	}
	if !matched {
		return fmt.Errorf("TOC cost price pin mismatch")
	}
	var receipt CostReceipt
	err = tx.First(&receipt, "event_id = ?", event.ID).Error
	if err == nil {
		if receipt.JobID != jobID || receipt.UserID != task.UserID || receipt.PayloadSHA256 != event.PayloadSHA256 {
			return fmt.Errorf("TOC cost event identity conflict")
		}
		return nil
	}
	if !errors.Is(err, gorm.ErrRecordNotFound) {
		return err
	}
	return tx.Create(&CostReceipt{EventID: event.ID, JobID: jobID, UserID: task.UserID, PayloadSHA256: event.PayloadSHA256, PayloadJSON: event.PayloadJSON, CreatedAt: time.Now().UTC()}).Error
}
