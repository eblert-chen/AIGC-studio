package controller

import (
	"encoding/json"
	"errors"
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

func TestPlatformChannelControlChannel(c *gin.Context) {
	if !validatePlatformChannelControlQuery(c) {
		return
	}
	channelID, ok := platformChannelControlChannelID(c)
	if !ok {
		return
	}
	body, root, ok := decodePlatformChannelControlBody(c,
		[]string{"operation_id", "tenant_id", "actor", "reason"},
		[]string{"operation_id", "tenant_id", "actor", "reason"},
	)
	if !ok {
		return
	}
	var request dto.PlatformChannelControlTestRequest
	if common.Unmarshal(body, &request) != nil || !validatePlatformChannelControlIdentity(root, request.OperationID, request.Actor, request.Reason) {
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
	receipt, execute, err := service.BeginPlatformChannelControlTest(channelID, request, requestID)
	if err != nil {
		writePlatformChannelControlOperationError(c, err)
		return
	}
	if !execute {
		c.Header("Cache-Control", "no-store")
		c.Header("X-Idempotent-Replay", "true")
		c.JSON(http.StatusOK, receipt)
		return
	}

	started := time.Now()
	success := false
	errorCode := model.PlatformChannelControlErrorTestUnavailable
	channel, loadErr := model.GetChannelById(channelID, true)
	testUserID, userErr := resolveChannelTestUserID(nil)
	if loadErr == nil && userErr == nil {
		result := testChannel(c.Request.Context(), channel, testUserID, "", "", false)
		if result.localErr == nil {
			elapsed := time.Since(started).Milliseconds()
			channel.UpdateResponseTime(elapsed)
			if result.newAPIError == nil {
				success = true
				errorCode = ""
			} else {
				errorCode = model.PlatformChannelControlErrorTestFailed
			}
		}
	}
	responseTimeMS := time.Since(started).Milliseconds()
	completed, err := service.CompletePlatformChannelControlTest(request.TenantID, request.OperationID, success, responseTimeMS, errorCode)
	if err != nil {
		common.SysError("complete Platform channel test operation: " + err.Error())
		writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", true, map[string]any{"operation_id": request.OperationID})
		return
	}
	c.Header("Cache-Control", "no-store")
	c.Header("X-Idempotent-Replay", "false")
	c.JSON(http.StatusOK, completed)
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

func writePlatformChannelControlOperationError(c *gin.Context, err error) {
	switch {
	case errors.Is(err, gorm.ErrRecordNotFound):
		writePlatformGenerationError(c, http.StatusNotFound, model.PlatformGenerationErrorChannelNotFound, "Channel does not exist", false, nil)
	case errors.Is(err, model.ErrPlatformChannelControlOperationConflict):
		writePlatformGenerationError(c, http.StatusConflict, model.PlatformGenerationErrorChannelControlOperationConflict, "operation_id was already used with different channel control intent", false, nil)
	default:
		common.SysError("Platform channel control operation: " + err.Error())
		writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", true, nil)
	}
}
