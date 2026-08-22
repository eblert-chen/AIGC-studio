package controller

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

func authorizePlatformGenerationCallbackOperations(c *gin.Context, tenantID string) bool {
	if service.AuthenticatePlatformGenerationOperationsCredential(
		c.GetHeader("X-Relay-Operations-Token"),
		tenantID,
	) {
		return true
	}
	writePlatformGenerationError(c, http.StatusUnauthorized, model.PlatformGenerationErrorOperationsUnauthorized, "Operations credential is not authorized", false, nil)
	return false
}

func ListPlatformGenerationCallbackDeliveries(c *gin.Context) {
	tenantID := c.Query("tenant_id")
	if !authorizePlatformGenerationCallbackOperations(c, tenantID) {
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
	state := c.DefaultQuery("state", model.PlatformGenerationCallbackDeadLetter)
	if state != model.PlatformGenerationCallbackPending &&
		state != model.PlatformGenerationCallbackClaimed &&
		state != model.PlatformGenerationCallbackDelivered &&
		state != model.PlatformGenerationCallbackDeadLetter {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return
	}
	result, err := service.ListPlatformGenerationCallbackDeliveries(tenantID, state, page, pageSize)
	if err == nil {
		c.JSON(http.StatusOK, result)
		return
	}
	common.SysError("list Platform generation callback deliveries: " + err.Error())
	writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", true, nil)
}

func GetPlatformGenerationCallbackDelivery(c *gin.Context) {
	tenantID := c.Query("tenant_id")
	if !authorizePlatformGenerationCallbackOperations(c, tenantID) {
		return
	}
	result, err := service.GetPlatformGenerationCallbackDelivery(tenantID, c.Param("event_id"))
	if err == nil {
		c.JSON(http.StatusOK, result)
		return
	}
	if errors.Is(err, gorm.ErrRecordNotFound) {
		writePlatformGenerationError(c, http.StatusNotFound, model.PlatformGenerationErrorCallbackDeliveryNotFound, "Callback delivery does not exist", false, nil)
		return
	}
	common.SysError("get Platform generation callback delivery: " + err.Error())
	writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", true, nil)
}

func RedrivePlatformGenerationCallbackDelivery(c *gin.Context) {
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 16*1024)
	body, err := io.ReadAll(c.Request.Body)
	if err != nil {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return
	}
	root, err := decodePlatformJSONObject(
		json.RawMessage(body),
		[]string{"body"},
		[]string{"operation_id", "tenant_id", "actor", "reason"},
		[]string{"operation_id", "tenant_id", "actor", "reason"},
	)
	if err != nil {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return
	}
	for _, field := range []string{"operation_id", "tenant_id", "actor", "reason"} {
		if requirePlatformJSONType(root[field], "string", []string{"body", field}) != nil {
			writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
			return
		}
	}
	var request dto.PlatformGenerationCallbackRedriveRequest
	if common.Unmarshal(body, &request) != nil ||
		!platformGenerationOperationIDPattern.MatchString(request.OperationID) ||
		strings.TrimSpace(request.Actor) != request.Actor || len(request.Actor) < 1 || len(request.Actor) > 128 ||
		strings.TrimSpace(request.Reason) != request.Reason || len(request.Reason) < 3 || len(request.Reason) > 240 {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return
	}
	if !authorizePlatformGenerationCallbackOperations(c, request.TenantID) {
		return
	}
	requestID := c.GetHeader("X-Request-ID")
	if !platformGenerationRequestIDPattern.MatchString(requestID) || requestID != c.GetString(common.RequestIdKey) {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "X-Request-ID is required for callback redrive", false, nil)
		return
	}
	result, idempotentReplay, err := service.RedrivePlatformGenerationCallbackDelivery(
		request.TenantID,
		c.Param("event_id"),
		request,
		requestID,
	)
	if err == nil {
		c.Header("X-Callback-Redrive-Event-ID", result.Evidence.EventID)
		c.Header("X-Idempotent-Replay", strconv.FormatBool(idempotentReplay))
		c.JSON(http.StatusOK, result)
		return
	}
	if errors.Is(err, gorm.ErrRecordNotFound) {
		writePlatformGenerationError(c, http.StatusNotFound, model.PlatformGenerationErrorCallbackDeliveryNotFound, "Callback delivery does not exist", false, nil)
		return
	}
	if errors.Is(err, model.ErrPlatformGenerationCallbackNotDeadLetter) {
		writePlatformGenerationError(c, http.StatusConflict, model.PlatformGenerationErrorCallbackRedriveNotAllowed, "Only a dead-lettered callback delivery can be redriven", false, nil)
		return
	}
	if errors.Is(err, model.ErrPlatformGenerationCallbackRedriveConflict) {
		writePlatformGenerationError(c, http.StatusConflict, model.PlatformGenerationErrorCallbackRedriveConflict, "Callback redrive operation_id was already used with different evidence", false, nil)
		return
	}
	common.SysError("redrive Platform generation callback delivery: " + err.Error())
	writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", true, nil)
}

func GetPlatformGenerationCallbackRedriveResult(c *gin.Context) {
	tenantID := c.Query("tenant_id")
	if !authorizePlatformGenerationCallbackOperations(c, tenantID) {
		return
	}
	operationID := c.Query("operation_id")
	if !platformGenerationOperationIDPattern.MatchString(operationID) {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return
	}
	result, err := service.GetPlatformGenerationCallbackRedriveResult(tenantID, c.Param("event_id"), operationID)
	if err == nil {
		c.JSON(http.StatusOK, result)
		return
	}
	if errors.Is(err, gorm.ErrRecordNotFound) {
		writePlatformGenerationError(c, http.StatusNotFound, model.PlatformGenerationErrorCallbackRedriveNotFound, "Callback redrive operation does not exist", false, nil)
		return
	}
	common.SysError("get Platform generation callback redrive result: " + err.Error())
	writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", true, nil)
}
