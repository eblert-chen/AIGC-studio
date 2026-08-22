package service

import (
	"crypto/sha256"
	"fmt"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"gorm.io/gorm"
)

func platformGenerationCallbackRedriveEvidence(
	event model.PlatformGenerationCallbackRedriveEvent,
) dto.PlatformGenerationCallbackRedriveEvidence {
	return dto.PlatformGenerationCallbackRedriveEvidence{
		EventID:                   event.ID,
		OperationID:               event.OperationID,
		RequestID:                 event.RequestID,
		Actor:                     event.Actor,
		Reason:                    event.Reason,
		PreviousState:             event.PreviousState,
		PreviousAttempts:          event.PreviousAttempts,
		PreviousMaxAttempts:       event.PreviousMaxAttempts,
		PreviousResponseStatus:    event.PreviousResponseStatus,
		PreviousLastError:         event.PreviousLastError,
		PreviousDeadLetteredAt:    event.PreviousDeadLetteredAt,
		CallbackURLSHA256:         event.CallbackURLSHA256,
		PayloadSHA256:             event.PayloadSHA256,
		OriginalCallbackRequestID: event.OriginalCallbackRequestID,
		ResultState:               event.ResultState,
		ReceiptSHA256:             event.ReceiptSHA256,
		RedrivenAt:                event.RedrivenAt,
	}
}

func platformGenerationCallbackDeliveryItem(
	delivery model.PlatformGenerationCallbackDelivery,
	redrives []model.PlatformGenerationCallbackRedriveEvent,
) dto.PlatformGenerationCallbackDeliveryItem {
	callbackURLDigest := sha256.Sum256([]byte(delivery.CallbackURL))
	evidence := make([]dto.PlatformGenerationCallbackRedriveEvidence, 0, len(redrives))
	for _, event := range redrives {
		evidence = append(evidence, platformGenerationCallbackRedriveEvidence(event))
	}
	return dto.PlatformGenerationCallbackDeliveryItem{
		APIVersion:        dto.PlatformRelayAPIVersion,
		SchemaVersion:     dto.PlatformRelaySchemaVersion,
		Object:            "generation.callback_delivery",
		EventID:           delivery.ID,
		TenantID:          delivery.TenantID,
		JobID:             delivery.JobID,
		SourceClientID:    delivery.SourceClientID,
		OriginalRequestID: delivery.RequestID,
		PayloadSHA256:     delivery.PayloadSHA256,
		CallbackURLSHA256: fmt.Sprintf("%x", callbackURLDigest),
		State:             delivery.State,
		Attempts:          delivery.Attempts,
		MaxAttempts:       delivery.MaxAttempts,
		AvailableAt:       delivery.AvailableAt,
		ResponseStatus:    delivery.ResponseStatus,
		LastError:         delivery.LastError,
		DeliveredAt:       delivery.DeliveredAt,
		DeadLetteredAt:    delivery.DeadLetteredAt,
		CreatedAt:         delivery.CreatedAt,
		UpdatedAt:         delivery.UpdatedAt,
		Redrives:          evidence,
	}
}

func ListPlatformGenerationCallbackDeliveries(
	tenantID string,
	state string,
	page int,
	pageSize int,
) (dto.PlatformGenerationCallbackDeliveryPage, error) {
	if parsed, err := uuid.Parse(tenantID); err != nil || parsed.String() != tenantID {
		return dto.PlatformGenerationCallbackDeliveryPage{}, gorm.ErrRecordNotFound
	}
	deliveries, total, err := model.ListPlatformGenerationCallbackDeliveriesForOperations(
		tenantID,
		state,
		page,
		pageSize,
	)
	if err != nil {
		return dto.PlatformGenerationCallbackDeliveryPage{}, err
	}
	items := make([]dto.PlatformGenerationCallbackDeliveryItem, 0, len(deliveries))
	for _, delivery := range deliveries {
		items = append(items, platformGenerationCallbackDeliveryItem(delivery, nil))
	}
	return dto.PlatformGenerationCallbackDeliveryPage{
		APIVersion:    dto.PlatformRelayAPIVersion,
		SchemaVersion: dto.PlatformRelaySchemaVersion,
		Object:        "list",
		Data:          items,
		Page:          page,
		PageSize:      pageSize,
		Total:         total,
	}, nil
}

func GetPlatformGenerationCallbackDelivery(
	tenantID string,
	deliveryID string,
) (dto.PlatformGenerationCallbackDeliveryItem, error) {
	delivery, err := model.GetPlatformGenerationCallbackDeliveryForOperations(deliveryID, tenantID)
	if err != nil {
		return dto.PlatformGenerationCallbackDeliveryItem{}, err
	}
	redrives, err := model.ListPlatformGenerationCallbackRedriveEvents(deliveryID, tenantID)
	if err != nil {
		return dto.PlatformGenerationCallbackDeliveryItem{}, err
	}
	return platformGenerationCallbackDeliveryItem(*delivery, redrives), nil
}

func RedrivePlatformGenerationCallbackDelivery(
	tenantID string,
	deliveryID string,
	request dto.PlatformGenerationCallbackRedriveRequest,
	requestID string,
) (dto.PlatformGenerationCallbackRedriveResult, bool, error) {
	receipt, idempotentReplay, err := model.RedrivePlatformGenerationCallbackDelivery(
		deliveryID,
		tenantID,
		model.PlatformGenerationCallbackRedriveRequest{
			OperationID: request.OperationID,
			RequestID:   requestID,
			Actor:       request.Actor,
			Reason:      request.Reason,
		},
	)
	if err != nil {
		return dto.PlatformGenerationCallbackRedriveResult{}, false, err
	}
	return platformGenerationCallbackRedriveResult(*receipt), idempotentReplay, nil
}

func GetPlatformGenerationCallbackRedriveResult(
	tenantID string,
	deliveryID string,
	operationID string,
) (dto.PlatformGenerationCallbackRedriveResult, error) {
	receipt, err := model.GetPlatformGenerationCallbackRedriveReceipt(deliveryID, tenantID, operationID)
	if err != nil {
		return dto.PlatformGenerationCallbackRedriveResult{}, err
	}
	return platformGenerationCallbackRedriveResult(*receipt), nil
}

func platformGenerationCallbackRedriveResult(
	receipt model.PlatformGenerationCallbackRedriveReceipt,
) dto.PlatformGenerationCallbackRedriveResult {
	return dto.PlatformGenerationCallbackRedriveResult{
		APIVersion:      dto.PlatformRelayAPIVersion,
		SchemaVersion:   dto.PlatformRelaySchemaVersion,
		Object:          "generation.callback_redrive_result",
		DeliveryEventID: receipt.Event.DeliveryID,
		TenantID:        receipt.Event.TenantID,
		CurrentState:    receipt.CurrentState,
		Evidence:        platformGenerationCallbackRedriveEvidence(receipt.Event),
	}
}
