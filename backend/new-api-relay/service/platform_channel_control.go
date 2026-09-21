package service

import (
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
)

func platformChannelControlStatusValue(status string) (*int, error) {
	switch status {
	case "", "all":
		return nil, nil
	case "enabled":
		value := common.ChannelStatusEnabled
		return &value, nil
	case "manually_disabled":
		value := common.ChannelStatusManuallyDisabled
		return &value, nil
	case "auto_disabled":
		value := common.ChannelStatusAutoDisabled
		return &value, nil
	default:
		return nil, fmt.Errorf("channel control status is invalid")
	}
}

// IsChannelTestSupported is shared by the native channel-test handler and the
// Platform facade so neither surface can advertise a synthetic connectivity
// test for asynchronous video/image providers that the native tester cannot
// exercise faithfully.
func IsChannelTestSupported(channelType int) bool {
	switch channelType {
	case constant.ChannelTypeMidjourney,
		constant.ChannelTypeMidjourneyPlus,
		constant.ChannelTypeSunoAPI,
		constant.ChannelTypeSora,
		constant.ChannelTypeKling,
		constant.ChannelTypeJimeng,
		constant.ChannelTypeDoubaoVideo,
		constant.ChannelTypeVidu:
		return false
	default:
		return true
	}
}

func platformChannelControlChannel(channel model.Channel) (dto.PlatformChannelControlChannel, error) {
	status := model.PlatformChannelControlStatusName(channel.Status)
	if status == "unknown" {
		return dto.PlatformChannelControlChannel{}, fmt.Errorf("channel has an invalid status")
	}
	models := channel.GetModels()
	configuredModels := make([]string, 0, len(models))
	for _, modelName := range models {
		if normalized := strings.TrimSpace(modelName); normalized != "" {
			configuredModels = append(configuredModels, normalized)
		}
	}
	sort.Strings(configuredModels)
	configuredModels = compactSortedStrings(configuredModels)

	keyCount := 0
	if channel.CredentialSetVersion != "" {
		keyCount = 1
		if channel.ChannelInfo.IsMultiKey && channel.ChannelInfo.MultiKeySize > 0 {
			keyCount = channel.ChannelInfo.MultiKeySize
		}
	}
	var testModel *string
	if channel.TestModel != nil {
		normalized := strings.TrimSpace(*channel.TestModel)
		if normalized != "" {
			testModel = &normalized
		}
	}
	weight := uint(0)
	if channel.Weight != nil {
		weight = *channel.Weight
	}
	priority := int64(0)
	if channel.Priority != nil && *channel.Priority > 0 {
		priority = *channel.Priority
	}
	autoBan := channel.AutoBan != nil && *channel.AutoBan != 0
	tag := ""
	if channel.Tag != nil {
		tag = *channel.Tag
	}
	createdAt := time.Unix(channel.CreatedTime, 0).UTC()
	var lastTestedAt *time.Time
	var responseTimeMS *int
	if channel.TestTime > 0 {
		value := time.Unix(channel.TestTime, 0).UTC()
		lastTestedAt = &value
		response := channel.ResponseTime
		if response < 0 {
			response = 0
		}
		responseTimeMS = &response
	}
	return dto.PlatformChannelControlChannel{
		ID:                        channel.Id,
		Name:                      channel.Name,
		Type:                      channel.Type,
		TypeLabel:                 constant.GetChannelTypeName(channel.Type),
		Status:                    status,
		TestSupported:             IsChannelTestSupported(channel.Type),
		ConfiguredModels:          configuredModels,
		TestModel:                 testModel,
		Weight:                    weight,
		Priority:                  priority,
		AutoBan:                   autoBan,
		Tag:                       tag,
		ProviderOnboardingManaged: model.IsProviderOnboardingManagedChannel(&channel),
		CreatedAt:                 createdAt,
		LastTestedAt:              lastTestedAt,
		ResponseTimeMS:            responseTimeMS,
		Credential: dto.PlatformChannelControlCredentialSummary{
			Configured: channel.CredentialSetVersion != "" && keyCount > 0,
			KeyCount:   keyCount,
		},
		Revision: model.PlatformChannelControlRevision(channel),
	}, nil
}

func compactSortedStrings(values []string) []string {
	if len(values) < 2 {
		return values
	}
	result := values[:1]
	for _, value := range values[1:] {
		if value != result[len(result)-1] {
			result = append(result, value)
		}
	}
	return result
}

func ListPlatformChannelControlChannels(status string, page int, pageSize int) (dto.PlatformChannelControlChannelPage, error) {
	statusValue, err := platformChannelControlStatusValue(status)
	if err != nil {
		return dto.PlatformChannelControlChannelPage{}, err
	}
	channels, total, err := model.ListPlatformChannelControlChannels(page, pageSize, statusValue)
	if err != nil {
		return dto.PlatformChannelControlChannelPage{}, err
	}
	items := make([]dto.PlatformChannelControlChannel, 0, len(channels))
	for _, channel := range channels {
		item, err := platformChannelControlChannel(channel)
		if err != nil {
			return dto.PlatformChannelControlChannelPage{}, err
		}
		items = append(items, item)
	}
	return dto.PlatformChannelControlChannelPage{
		APIVersion:    dto.PlatformRelayAPIVersion,
		SchemaVersion: dto.PlatformRelaySchemaVersion,
		Object:        "list",
		Data:          items,
		Page:          page,
		PageSize:      pageSize,
		Total:         total,
	}, nil
}

func GetPlatformChannelControlChannel(channelID int) (dto.PlatformChannelControlChannel, error) {
	channel, err := model.GetPlatformChannelControlChannel(channelID)
	if err != nil {
		return dto.PlatformChannelControlChannel{}, err
	}
	return platformChannelControlChannel(*channel)
}

func BeginPlatformChannelControlTest(
	channelID int,
	request dto.PlatformChannelControlTestRequest,
	requestID string,
) (dto.PlatformChannelControlOperation, bool, *PlatformGenerationRouteTestBinding, error) {
	if strings.TrimSpace(request.PublicModelID) != request.PublicModelID || request.PublicModelID == "" ||
		strings.TrimSpace(request.RouteID) != request.RouteID || request.RouteID == "" ||
		strings.TrimSpace(request.Mode) != request.Mode {
		return dto.PlatformChannelControlOperation{}, false, nil, fmt.Errorf("Platform channel test requires an exact public_model_id and route_id binding")
	}
	binding, err := ResolvePlatformGenerationRouteTestBindingForMode(channelID, request.PublicModelID, request.RouteID, request.Mode)
	if err != nil {
		return dto.PlatformChannelControlOperation{}, false, nil, err
	}
	intent := model.PlatformChannelControlIntent{
		OperationID: request.OperationID,
		TenantID:    request.TenantID,
		ChannelID:   channelID,
		Kind:        model.PlatformChannelControlOperationKindTest,
		RequestID:   requestID,
		Actor:       request.Actor,
		Reason:      request.Reason,
	}
	intent.Model = binding.UpstreamModel
	intent.PublicModelID = binding.PublicModelID
	intent.RouteID = binding.RouteID
	intent.Mode = binding.Mode
	intent.UpstreamModel = binding.UpstreamModel
	intent.CapabilityProfileID = binding.CapabilityProfileID
	intent.CapabilityProfileRevision = binding.CapabilityProfileRevision
	intent.ModelReleaseID = binding.ModelReleaseID
	intent.ModelReleaseRevision = binding.ModelReleaseRevision
	intent.CapabilityRevision = binding.CapabilityRevision
	intent.RoutingReleaseSHA256 = binding.RoutingReleaseSHA256
	intent.RouteBindingSHA256 = binding.RouteBindingSHA256
	intent.CredentialFingerprintSHA256 = binding.CredentialFingerprintSHA256
	intent.TransportRevision = binding.TransportRevision
	intent.TransportSHA256 = binding.TransportSHA256
	operation, execute, replay, err := model.BeginPlatformChannelTestOperation(intent)
	if err != nil {
		return dto.PlatformChannelControlOperation{}, false, nil, err
	}
	if binding != nil && operation.ProviderSubmissionState == model.PlatformChannelTestSubmissionSubmitted {
		binding.ProviderTaskID = operation.ProviderTaskID
	}
	resume := operation.State == model.PlatformChannelControlOperationPending &&
		(operation.ProviderSubmissionState == model.PlatformChannelTestSubmissionNotStarted ||
			operation.ProviderSubmissionState == model.PlatformChannelTestSubmissionSubmitted)
	return platformChannelControlOperation(*operation, replay), execute || resume, binding, nil
}

func ClaimPlatformChannelControlTestSubmission(tenantID string, operationID string) (bool, error) {
	_, claimed, err := model.ClaimPlatformChannelTestSubmission(tenantID, operationID)
	return claimed, err
}

func RecordPlatformChannelControlTestSubmitted(tenantID string, operationID string, providerTaskID string) error {
	_, err := model.RecordPlatformChannelTestSubmitted(tenantID, operationID, providerTaskID)
	return err
}

func RecordPlatformChannelControlTestBlocker(tenantID string, operationID string, errorCode string) error {
	_, err := model.RecordPlatformChannelTestBlocker(tenantID, operationID, errorCode)
	return err
}

func CompletePlatformChannelControlRouteTestSuccess(
	tenantID string,
	operationID string,
	responseTimeMS int64,
	evidence model.PlatformChannelTestArtifactEvidence,
) (dto.PlatformChannelControlOperation, error) {
	operation, err := model.CompletePlatformChannelRouteTestSuccess(tenantID, operationID, responseTimeMS, evidence)
	if err != nil {
		return dto.PlatformChannelControlOperation{}, err
	}
	return platformChannelControlOperation(*operation, false), nil
}

func CompletePlatformChannelControlRouteTestFailure(
	tenantID string,
	operationID string,
	responseTimeMS int64,
	errorCode string,
	providerRequestStarted bool,
) (dto.PlatformChannelControlOperation, error) {
	operation, err := model.CompletePlatformChannelRouteTestFailure(
		tenantID, operationID, responseTimeMS, errorCode, providerRequestStarted,
	)
	if err != nil {
		return dto.PlatformChannelControlOperation{}, err
	}
	return platformChannelControlOperation(*operation, false), nil
}

func CompletePlatformChannelControlRouteTestProvenNoCreation(
	tenantID string,
	operationID string,
	responseTimeMS int64,
	errorCode string,
) (dto.PlatformChannelControlOperation, error) {
	operation, err := model.CompletePlatformChannelRouteTestProvenNoCreation(
		tenantID, operationID, responseTimeMS, errorCode,
	)
	if err != nil {
		return dto.PlatformChannelControlOperation{}, err
	}
	return platformChannelControlOperation(*operation, false), nil
}

func CompletePlatformChannelControlTest(
	tenantID string,
	operationID string,
	success bool,
	responseTimeMS int64,
	errorCode string,
) (dto.PlatformChannelControlOperation, error) {
	operation, err := model.CompletePlatformChannelTestOperation(tenantID, operationID, success, responseTimeMS, errorCode)
	if err != nil {
		return dto.PlatformChannelControlOperation{}, err
	}
	return platformChannelControlOperation(*operation, false), nil
}

func ApplyPlatformChannelControlStatus(
	channelID int,
	request dto.PlatformChannelControlStatusRequest,
	requestID string,
) (dto.PlatformChannelControlOperation, error) {
	var targetStatus int
	switch request.TargetStatus {
	case "enabled":
		targetStatus = common.ChannelStatusEnabled
	case "manually_disabled":
		targetStatus = common.ChannelStatusManuallyDisabled
	default:
		return dto.PlatformChannelControlOperation{}, fmt.Errorf("channel control target status is invalid")
	}
	operation, replay, err := model.ApplyPlatformChannelStatusOperation(model.PlatformChannelControlIntent{
		OperationID:      request.OperationID,
		TenantID:         request.TenantID,
		ChannelID:        channelID,
		Kind:             model.PlatformChannelControlOperationKindStatus,
		RequestID:        requestID,
		Actor:            request.Actor,
		Reason:           request.Reason,
		ExpectedRevision: request.ExpectedRevision,
		TargetStatus:     targetStatus,
	})
	if operation == nil {
		return dto.PlatformChannelControlOperation{}, err
	}
	result := platformChannelControlOperation(*operation, replay)
	if err == nil && operation.ResultChanged != nil && *operation.ResultChanged {
		model.InitChannelCache()
	}
	return result, err
}

func GetPlatformChannelControlOperation(tenantID string, channelID int, operationID string) (dto.PlatformChannelControlOperation, error) {
	operation, err := model.GetPlatformChannelControlOperation(tenantID, channelID, operationID)
	if err != nil {
		return dto.PlatformChannelControlOperation{}, err
	}
	return platformChannelControlOperation(*operation, false), nil
}

func ReconcilePlatformChannelControlTestNoCreation(
	channelID int,
	operationID string,
	request dto.PlatformChannelControlReconcileNoCreationRequest,
) (dto.PlatformChannelControlOperation, error) {
	operation, err := model.ReconcilePlatformChannelTestNoCreation(
		request.TenantID,
		channelID,
		operationID,
		request.Actor,
		request.Reason,
		request.ConfirmedNoProviderCreation,
	)
	if err != nil {
		return dto.PlatformChannelControlOperation{}, err
	}
	return platformChannelControlOperation(*operation, false), nil
}

func platformChannelControlOperation(operation model.PlatformChannelControlOperation, replay bool) dto.PlatformChannelControlOperation {
	var result *dto.PlatformChannelControlOperationResult
	if operation.ResultSuccess != nil || operation.ResultChanged != nil || operation.ResultErrorCode != "" ||
		operation.ResultPreviousStatus != "" || operation.ResultCurrentStatus != "" {
		result = &dto.PlatformChannelControlOperationResult{
			Success:        operation.ResultSuccess,
			ResponseTimeMS: operation.ResultResponseMS,
			ErrorCode:      operation.ResultErrorCode,
			PreviousStatus: operation.ResultPreviousStatus,
			CurrentStatus:  operation.ResultCurrentStatus,
			Changed:        operation.ResultChanged,
		}
	}
	createdAt := operation.CreatedAt.UTC()
	var completedAt *time.Time
	if operation.CompletedAt != nil {
		value := operation.CompletedAt.UTC()
		completedAt = &value
	}
	var reconciledAt *time.Time
	if operation.ReconciledAt != nil {
		value := operation.ReconciledAt.UTC()
		reconciledAt = &value
	}
	return dto.PlatformChannelControlOperation{
		APIVersion:              dto.PlatformRelayAPIVersion,
		SchemaVersion:           dto.PlatformRelaySchemaVersion,
		Object:                  "relay.channel_control_operation",
		OperationID:             operation.OperationID,
		TenantID:                operation.TenantID,
		ChannelID:               operation.ChannelID,
		Kind:                    operation.Kind,
		State:                   operation.State,
		Actor:                   operation.Actor,
		Reason:                  operation.Reason,
		RequestID:               operation.RequestID,
		IntentSHA256:            operation.IntentSHA256,
		ExpectedRevision:        operation.IntentExpectedRevision,
		TargetStatus:            operation.IntentTargetStatus,
		PreviousRevision:        operation.PreviousRevision,
		ResultRevision:          operation.ResultRevision,
		Result:                  result,
		ProviderSubmissionState: operation.ProviderSubmissionState,
		ProviderBlockerCode:     operation.ProviderBlockerCode,
		ReconciliationActor:     operation.ReconciliationActor,
		ReconciliationReason:    operation.ReconciliationReason,
		ReconciledAt:            reconciledAt,
		CreatedAt:               createdAt,
		CompletedAt:             completedAt,
		IdempotentReplay:        replay,
	}
}
