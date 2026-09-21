package controller

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

var platformChannelControlRevisionPattern = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)

func authorizePlatformChannelControl(c *gin.Context, tenantID string) bool {
	if !service.AuthenticatePlatformGenerationOperationsCredential(
		c.GetHeader("X-Relay-Operations-Token"),
		tenantID,
	) {
		writePlatformGenerationError(c, http.StatusUnauthorized, model.PlatformGenerationErrorOperationsUnauthorized, "Operations credential is not authorized", false, nil)
		return false
	}
	if !service.IsPlatformChannelControlTenant(tenantID) {
		writePlatformGenerationError(c, http.StatusForbidden, model.PlatformGenerationErrorControlTenantForbidden, "Operations tenant is not authorized for global channel control", false, nil)
		return false
	}
	return true
}

func validatePlatformChannelControlQuery(c *gin.Context, allowed ...string) bool {
	allow := make(map[string]struct{}, len(allowed))
	for _, name := range allowed {
		allow[name] = struct{}{}
	}
	for name := range c.Request.URL.Query() {
		if _, ok := allow[name]; !ok {
			writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
			return false
		}
	}
	return true
}

func platformChannelControlChannelID(c *gin.Context) (int, bool) {
	channelID, err := strconv.Atoi(c.Param("channel_id"))
	if err != nil || channelID <= 0 {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return 0, false
	}
	return channelID, true
}

func platformChannelControlRequestID(c *gin.Context) (string, bool) {
	requestID := c.GetHeader("X-Request-ID")
	if !platformGenerationRequestIDPattern.MatchString(requestID) || requestID != c.GetString(common.RequestIdKey) {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "X-Request-ID is required for channel control operations", false, nil)
		return "", false
	}
	return requestID, true
}

func ListPlatformChannelControlChannels(c *gin.Context) {
	if !validatePlatformChannelControlQuery(c, "tenant_id", "page", "page_size", "status") {
		return
	}
	tenantID := c.Query("tenant_id")
	if !authorizePlatformChannelControl(c, tenantID) {
		return
	}
	page, err := strconv.Atoi(c.DefaultQuery("page", "1"))
	if err != nil || page < 1 || page > 1_000_000 {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return
	}
	pageSize, err := strconv.Atoi(c.DefaultQuery("page_size", "50"))
	if err != nil || pageSize < 1 || pageSize > 100 {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return
	}
	result, err := service.ListPlatformChannelControlChannels(c.DefaultQuery("status", "all"), page, pageSize)
	if err == nil {
		c.Header("Cache-Control", "no-store")
		c.JSON(http.StatusOK, result)
		return
	}
	if strings.Contains(err.Error(), "status is invalid") {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return
	}
	common.SysError("list Platform channel control channels: " + err.Error())
	writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", true, nil)
}

func GetPlatformChannelControlChannel(c *gin.Context) {
	if !validatePlatformChannelControlQuery(c, "tenant_id") {
		return
	}
	tenantID := c.Query("tenant_id")
	if !authorizePlatformChannelControl(c, tenantID) {
		return
	}
	channelID, ok := platformChannelControlChannelID(c)
	if !ok {
		return
	}
	result, err := service.GetPlatformChannelControlChannel(channelID)
	if err == nil {
		c.Header("Cache-Control", "no-store")
		c.JSON(http.StatusOK, result)
		return
	}
	if errors.Is(err, gorm.ErrRecordNotFound) {
		writePlatformGenerationError(c, http.StatusNotFound, model.PlatformGenerationErrorChannelNotFound, "Channel does not exist", false, nil)
		return
	}
	common.SysError("get Platform channel control channel: " + err.Error())
	writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", true, nil)
}

func decodePlatformChannelControlBody(c *gin.Context, allowed []string, required []string) ([]byte, map[string]json.RawMessage, bool) {
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 16*1024)
	body, err := io.ReadAll(c.Request.Body)
	if err != nil {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return nil, nil, false
	}
	root, err := decodePlatformJSONObject(json.RawMessage(body), []string{"body"}, allowed, required)
	if err != nil {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return nil, nil, false
	}
	return body, root, true
}

func validatePlatformChannelControlIdentity(root map[string]json.RawMessage, operationID, actor, reason string) bool {
	for _, field := range []string{"operation_id", "tenant_id", "actor", "reason"} {
		if requirePlatformJSONType(root[field], "string", []string{"body", field}) != nil {
			return false
		}
	}
	return platformGenerationOperationIDPattern.MatchString(operationID) &&
		strings.TrimSpace(actor) == actor && len(actor) >= 1 && len(actor) <= 128 &&
		strings.TrimSpace(reason) == reason && len(reason) >= 3 && len(reason) <= 240
}

func validatePlatformChannelControlRouteBinding(root map[string]json.RawMessage, publicModelID, routeID, mode string) bool {
	for _, field := range []string{"public_model_id", "route_id"} {
		if requirePlatformJSONType(root[field], "string", []string{"body", field}) != nil {
			return false
		}
	}
	if rawMode, exists := root["mode"]; exists {
		if requirePlatformJSONType(rawMode, "string", []string{"body", "mode"}) != nil {
			return false
		}
		if mode != "text_to_image" && mode != "text_to_video" && mode != "image_to_video" && mode != "video_to_video" {
			return false
		}
	}
	return strings.TrimSpace(publicModelID) == publicModelID && len(publicModelID) >= 1 && len(publicModelID) <= 128 &&
		strings.TrimSpace(routeID) == routeID && len(routeID) >= 1 && len(routeID) <= 128
}

func platformChannelControlReplayStatus(receipt dto.PlatformChannelControlOperation) int {
	if receipt.State == model.PlatformChannelControlOperationPending {
		return http.StatusAccepted
	}
	return http.StatusOK
}

func TestPlatformChannelControlChannel(c *gin.Context) {
	if !validatePlatformChannelControlQuery(c) {
		return
	}
	channelID, ok := platformChannelControlChannelID(c)
	if !ok {
		return
	}
	body, root, ok := decodePlatformChannelControlBody(c,
		[]string{"operation_id", "tenant_id", "actor", "reason", "public_model_id", "route_id", "mode"},
		[]string{"operation_id", "tenant_id", "actor", "reason", "public_model_id", "route_id"},
	)
	if !ok {
		return
	}
	var request dto.PlatformChannelControlTestRequest
	if common.Unmarshal(body, &request) != nil ||
		!validatePlatformChannelControlIdentity(root, request.OperationID, request.Actor, request.Reason) ||
		!validatePlatformChannelControlRouteBinding(root, request.PublicModelID, request.RouteID, request.Mode) {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return
	}
	if !authorizePlatformChannelControl(c, request.TenantID) {
		return
	}
	requestID, ok := platformChannelControlRequestID(c)
	if !ok {
		return
	}
	receipt, execute, binding, err := service.BeginPlatformChannelControlTest(channelID, request, requestID)
	if err != nil {
		writePlatformChannelControlOperationError(c, err)
		return
	}
	if !execute {
		c.Header("Cache-Control", "no-store")
		c.Header("X-Idempotent-Replay", "true")
		c.JSON(platformChannelControlReplayStatus(receipt), receipt)
		return
	}
	replayed := receipt.IdempotentReplay
	if binding == nil {
		common.SysError("begin Platform channel test returned no exact generation route binding")
		writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", false, map[string]any{"operation_id": request.OperationID})
		return
	}

	completed, responseStatus, err := executeDurablePlatformChannelControlRouteTest(
		c.Request.Context(), channelID, request.TenantID, request.OperationID, binding,
	)
	if err != nil {
		common.SysError("complete Platform channel test operation: " + err.Error())
		writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", true, map[string]any{"operation_id": request.OperationID})
		return
	}
	// executeDurable... may read the durable operation back through a
	// secret-free service method that intentionally has no request context.
	// Preserve the Begin decision so the response body and header agree on
	// whether this request resumed an existing operation.
	completed.IdempotentReplay = replayed
	c.Header("Cache-Control", "no-store")
	c.Header("X-Idempotent-Replay", strconv.FormatBool(replayed))
	c.JSON(responseStatus, completed)
}

func executeDurablePlatformChannelControlRouteTest(
	ctx context.Context,
	channelID int,
	tenantID string,
	operationID string,
	binding *service.PlatformGenerationRouteTestBinding,
) (dto.PlatformChannelControlOperation, int, error) {
	started := time.Now()
	currentReceipt := func() (dto.PlatformChannelControlOperation, int, error) {
		receipt, err := service.GetPlatformChannelControlOperation(tenantID, channelID, operationID)
		if err != nil {
			return dto.PlatformChannelControlOperation{}, 0, err
		}
		return receipt, platformChannelControlReplayStatus(receipt), nil
	}
	channel, err := model.GetChannelById(channelID, false)
	if err != nil || service.ValidatePlatformGenerationRouteTestTransport(channel, binding) != nil {
		if binding.ProviderTaskID != "" {
			return currentReceipt()
		}
		completed, completeErr := service.CompletePlatformChannelControlRouteTestFailure(
			tenantID, operationID, time.Since(started).Milliseconds(),
			model.PlatformChannelControlErrorTestRouteDrift, false,
		)
		return completed, http.StatusOK, completeErr
	}

	providerTaskID := binding.ProviderTaskID
	if providerTaskID == "" {
		claimed, claimErr := service.ClaimPlatformChannelControlTestSubmission(tenantID, operationID)
		if errors.Is(claimErr, model.ErrPlatformChannelTestSubmissionUnknown) || !claimed {
			return currentReceipt()
		}
		if claimErr != nil {
			return dto.PlatformChannelControlOperation{}, 0, claimErr
		}
	}

	// Re-read the secret-free row and verify the official endpoint after the
	// durable claim. Only then may the encrypted credential be hydrated.
	channel, err = model.GetChannelById(channelID, false)
	if err != nil || service.ValidatePlatformGenerationRouteTestTransport(channel, binding) != nil {
		if providerTaskID != "" {
			return currentReceipt()
		}
		completed, completeErr := service.CompletePlatformChannelControlRouteTestProvenNoCreation(
			tenantID, operationID, time.Since(started).Milliseconds(), model.PlatformChannelControlErrorTestRouteDrift,
		)
		return completed, http.StatusOK, completeErr
	}
	channel, err = model.GetChannelById(channelID, true)
	if err != nil || service.ValidatePlatformGenerationRouteTestTransport(channel, binding) != nil {
		if providerTaskID != "" {
			return currentReceipt()
		}
		completed, completeErr := service.CompletePlatformChannelControlRouteTestProvenNoCreation(
			tenantID, operationID, time.Since(started).Milliseconds(), model.PlatformChannelControlErrorTestRouteDrift,
		)
		return completed, http.StatusOK, completeErr
	}
	key, keyErr := channel.GetKeyAt(binding.KeyIndex)
	if keyErr != nil || fmt.Sprintf("%x", common.Sha256Raw([]byte(key))) != binding.CredentialFingerprintSHA256 {
		if providerTaskID != "" {
			return currentReceipt()
		}
		completed, completeErr := service.CompletePlatformChannelControlRouteTestProvenNoCreation(
			tenantID, operationID, time.Since(started).Milliseconds(), model.PlatformChannelControlErrorTestRouteDrift,
		)
		return completed, http.StatusOK, completeErr
	}
	pinned := *channel
	pinned.Key = key
	pinned.Keys = []string{key}
	pinned.ChannelInfo.IsMultiKey = false
	pinned.ChannelInfo.MultiKeySize = 1
	pinned.ChannelInfo.MultiKeyStatusList = nil

	if _, profileErr := resolvePlatformGenerationRouteTestPlan(binding); profileErr != nil {
		if providerTaskID != "" {
			return currentReceipt()
		}
		completed, completeErr := service.CompletePlatformChannelControlRouteTestProvenNoCreation(
			tenantID, operationID, time.Since(started).Milliseconds(), model.PlatformChannelControlErrorTestValidation,
		)
		return completed, http.StatusOK, completeErr
	}
	evidence := model.PlatformChannelTestArtifactEvidence{}
	providerTaskKnown := providerTaskID != ""
	routeErr := runPlatformGenerationRouteChannelTestDurable(
		ctx, &pinned, binding,
		platformGenerationRouteTestPollInterval, platformGenerationRouteTestTimeout,
		providerTaskID,
		func(taskID string) error {
			if err := service.RecordPlatformChannelControlTestSubmitted(tenantID, operationID, taskID); err != nil {
				return err
			}
			providerTaskKnown = true
			return nil
		},
		&evidence,
	)
	responseTimeMS := time.Since(started).Milliseconds()
	if routeErr == nil {
		pinned.UpdateResponseTime(responseTimeMS)
		completed, completeErr := service.CompletePlatformChannelControlRouteTestSuccess(
			tenantID, operationID, responseTimeMS, evidence,
		)
		return completed, http.StatusOK, completeErr
	}
	var classified *platformGenerationRouteTestError
	if errors.As(routeErr, &classified) {
		switch {
		case classified.submissionUnknown:
			return currentReceipt()
		case classified.pollPending:
			if providerTaskKnown {
				if err := service.RecordPlatformChannelControlTestBlocker(tenantID, operationID, classified.receiptCode); err != nil {
					return dto.PlatformChannelControlOperation{}, 0, err
				}
			}
			return currentReceipt()
		case classified.submissionRejected:
			completed, completeErr := service.CompletePlatformChannelControlRouteTestProvenNoCreation(
				tenantID, operationID, responseTimeMS, classified.receiptCode,
			)
			return completed, http.StatusOK, completeErr
		case classified.providerTerminal && providerTaskKnown:
			completed, completeErr := service.CompletePlatformChannelControlRouteTestFailure(
				tenantID, operationID, responseTimeMS, classified.receiptCode, true,
			)
			return completed, http.StatusOK, completeErr
		}
	}
	if providerTaskKnown {
		return currentReceipt()
	}
	completed, completeErr := service.CompletePlatformChannelControlRouteTestProvenNoCreation(
		tenantID, operationID, responseTimeMS, platformGenerationRouteTestReceiptCode(routeErr),
	)
	return completed, http.StatusOK, completeErr
}

func UpdatePlatformChannelControlStatus(c *gin.Context) {
	if !validatePlatformChannelControlQuery(c) {
		return
	}
	channelID, ok := platformChannelControlChannelID(c)
	if !ok {
		return
	}
	body, root, ok := decodePlatformChannelControlBody(c,
		[]string{"operation_id", "tenant_id", "actor", "reason", "expected_revision", "target_status"},
		[]string{"operation_id", "tenant_id", "actor", "reason", "expected_revision", "target_status"},
	)
	if !ok {
		return
	}
	for _, field := range []string{"expected_revision", "target_status"} {
		if requirePlatformJSONType(root[field], "string", []string{"body", field}) != nil {
			writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
			return
		}
	}
	var request dto.PlatformChannelControlStatusRequest
	if common.Unmarshal(body, &request) != nil ||
		!validatePlatformChannelControlIdentity(root, request.OperationID, request.Actor, request.Reason) ||
		(request.TargetStatus != "enabled" && request.TargetStatus != "manually_disabled") ||
		!platformChannelControlRevisionPattern.MatchString(request.ExpectedRevision) {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return
	}
	if !authorizePlatformChannelControl(c, request.TenantID) {
		return
	}
	requestID, ok := platformChannelControlRequestID(c)
	if !ok {
		return
	}
	receipt, err := service.ApplyPlatformChannelControlStatus(channelID, request, requestID)
	if err == nil {
		c.Header("Cache-Control", "no-store")
		c.Header("X-Idempotent-Replay", strconv.FormatBool(receipt.IdempotentReplay))
		c.JSON(http.StatusOK, receipt)
		return
	}
	if errors.Is(err, model.ErrPlatformChannelControlRevisionConflict) {
		writePlatformGenerationError(c, http.StatusConflict, model.PlatformGenerationErrorChannelRevisionConflict, "Channel revision does not match", false, map[string]any{
			"operation_id":     request.OperationID,
			"current_revision": receipt.ResultRevision,
		})
		return
	}
	writePlatformChannelControlOperationError(c, err)
}

func GetPlatformChannelControlOperation(c *gin.Context) {
	if !validatePlatformChannelControlQuery(c, "tenant_id") {
		return
	}
	tenantID := c.Query("tenant_id")
	if !authorizePlatformChannelControl(c, tenantID) {
		return
	}
	channelID, ok := platformChannelControlChannelID(c)
	if !ok {
		return
	}
	operationID := c.Param("operation_id")
	if !platformGenerationOperationIDPattern.MatchString(operationID) {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return
	}
	result, err := service.GetPlatformChannelControlOperation(tenantID, channelID, operationID)
	if err == nil {
		c.Header("Cache-Control", "no-store")
		c.JSON(http.StatusOK, result)
		return
	}
	if errors.Is(err, gorm.ErrRecordNotFound) {
		writePlatformGenerationError(c, http.StatusNotFound, model.PlatformGenerationErrorChannelControlOperationNotFound, "Channel control operation does not exist", false, nil)
		return
	}
	common.SysError("get Platform channel control operation: " + err.Error())
	writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", true, nil)
}

func ReconcilePlatformChannelControlTestNoCreation(c *gin.Context) {
	if !validatePlatformChannelControlQuery(c) {
		return
	}
	channelID, ok := platformChannelControlChannelID(c)
	if !ok {
		return
	}
	operationID := c.Param("operation_id")
	if !platformGenerationOperationIDPattern.MatchString(operationID) {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return
	}
	body, root, ok := decodePlatformChannelControlBody(c,
		[]string{"tenant_id", "actor", "reason", "confirmed_no_provider_creation"},
		[]string{"tenant_id", "actor", "reason", "confirmed_no_provider_creation"},
	)
	if !ok {
		return
	}
	for _, field := range []string{"tenant_id", "actor", "reason"} {
		if requirePlatformJSONType(root[field], "string", []string{"body", field}) != nil {
			writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
			return
		}
	}
	if requirePlatformJSONType(root["confirmed_no_provider_creation"], "boolean", []string{"body", "confirmed_no_provider_creation"}) != nil {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return
	}
	var request dto.PlatformChannelControlReconcileNoCreationRequest
	if common.Unmarshal(body, &request) != nil || !request.ConfirmedNoProviderCreation ||
		strings.TrimSpace(request.Actor) != request.Actor || len(request.Actor) < 1 || len(request.Actor) > 128 ||
		strings.TrimSpace(request.Reason) != request.Reason || len(request.Reason) < 3 || len(request.Reason) > 240 {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return
	}
	if !authorizePlatformChannelControl(c, request.TenantID) {
		return
	}
	if _, ok := platformChannelControlRequestID(c); !ok {
		return
	}
	receipt, err := service.ReconcilePlatformChannelControlTestNoCreation(channelID, operationID, request)
	if err != nil {
		writePlatformChannelControlOperationError(c, err)
		return
	}
	c.Header("Cache-Control", "no-store")
	c.JSON(http.StatusOK, receipt)
}

func writePlatformChannelControlOperationError(c *gin.Context, err error) {
	switch {
	case errors.Is(err, gorm.ErrRecordNotFound):
		writePlatformGenerationError(c, http.StatusNotFound, model.PlatformGenerationErrorChannelNotFound, "Channel does not exist", false, nil)
	case errors.Is(err, model.ErrPlatformChannelControlOperationConflict):
		writePlatformGenerationError(c, http.StatusConflict, model.PlatformGenerationErrorChannelControlOperationConflict, "operation_id was already used with different channel control intent", false, nil)
	case errors.Is(err, model.ErrProviderOnboardingManagedChannel):
		writePlatformGenerationError(c, http.StatusConflict, model.PlatformGenerationErrorChannelControlOperationConflict, "managed provider channel requires the dedicated onboarding lifecycle", false, nil)
	case errors.Is(err, model.ErrPlatformChannelTestRouteBlocked):
		writePlatformGenerationError(c, http.StatusConflict, model.PlatformGenerationErrorChannelControlOperationConflict, "route has an unresolved provider submission", false, nil)
	case errors.Is(err, model.ErrPlatformChannelTestSubmissionUnknown):
		writePlatformGenerationError(c, http.StatusConflict, model.PlatformGenerationErrorChannelControlOperationConflict, "provider submission outcome requires reconciliation", false, nil)
	case errors.Is(err, model.ErrPlatformChannelTestReconciliationInvalid):
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
	default:
		common.SysError("Platform channel control operation: " + err.Error())
		writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", true, nil)
	}
}
