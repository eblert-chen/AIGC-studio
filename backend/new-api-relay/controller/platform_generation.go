package controller

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"gorm.io/gorm"
)

const platformGenerationRequestBodyLimit = 1 << 20

var platformGenerationModelIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`)
var platformGenerationReconciliationTokenPattern = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
var platformGenerationOperationIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$`)
var platformGenerationRequestIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$`)
var platformGenerationApprovalKeyIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$`)
var platformGenerationApprovalSignaturePattern = regexp.MustCompile(`^hmac-sha256:[0-9a-f]{64}$`)

type platformGenerationValidationIssue struct {
	Location []string `json:"location"`
	Message  string   `json:"message"`
	Type     string   `json:"type"`
}

type platformGenerationValidationError struct {
	Issue platformGenerationValidationIssue
}

func (e platformGenerationValidationError) Error() string {
	return e.Issue.Message
}

type platformRelayDependencyHealth struct {
	Name    string         `json:"name"`
	State   string         `json:"state"`
	Details map[string]any `json:"details"`
}

type platformRelayHealthResponse struct {
	State        string                          `json:"state"`
	Dependencies []platformRelayDependencyHealth `json:"dependencies"`
}

type platformRelayConcurrentReadinessResult struct {
	slot         string
	dependencies []platformRelayDependencyHealth
	unavailable  bool
	degraded     bool
}

// collectPlatformRelayConcurrentReadinessResults observes one bounded result
// per readiness slot while the request budget is live. The result channel is
// sized for every launched child, so returning at the hard deadline cannot
// block a context-aware child while it unwinds and publishes its final result.
// Database/Redis checks receive the same context; in-memory checks do not own
// resources that need a request-side join.
func collectPlatformRelayConcurrentReadinessResults(
	ctx context.Context,
	slotOrder []string,
	results <-chan platformRelayConcurrentReadinessResult,
) (map[string]platformRelayConcurrentReadinessResult, bool, error) {
	collected := make(map[string]platformRelayConcurrentReadinessResult, len(slotOrder))
	allowed := make(map[string]struct{}, len(slotOrder))
	for _, slot := range slotOrder {
		allowed[slot] = struct{}{}
	}
	var collectionErr error
	for received := 0; received < len(slotOrder); received++ {
		if ctx.Err() != nil {
			return collected, true, collectionErr
		}
		var result platformRelayConcurrentReadinessResult
		select {
		case result = <-results:
		case <-ctx.Done():
			return collected, true, collectionErr
		}
		if _, exists := allowed[result.slot]; !exists {
			collectionErr = errors.Join(collectionErr, fmt.Errorf("unknown Relay readiness slot %q", result.slot))
			continue
		}
		if _, duplicate := collected[result.slot]; duplicate {
			collectionErr = errors.Join(collectionErr, fmt.Errorf("duplicate Relay readiness slot %q", result.slot))
			continue
		}
		collected[result.slot] = result
	}
	if len(collected) != len(slotOrder) {
		collectionErr = errors.Join(collectionErr, errors.New("Relay readiness child result set is incomplete"))
	}
	return collected, false, collectionErr
}

func launchPlatformRelayReadinessChild(
	ctx context.Context,
	slot string,
	fallback platformRelayConcurrentReadinessResult,
	results chan<- platformRelayConcurrentReadinessResult,
	check func(context.Context) platformRelayConcurrentReadinessResult,
) {
	go func() {
		fallback.slot = slot
		result := fallback
		defer func() {
			if recover() != nil {
				// Never include the recovered value: a provider SDK panic can carry
				// request or credential material. The canonical slot result is enough.
				common.SysError("Relay readiness child failed unexpectedly: " + slot)
			}
			results <- result
		}()
		if ctx == nil {
			common.SysError("Relay readiness child context is unavailable: " + slot)
			return
		}
		if check != nil {
			if ctx.Err() != nil {
				return
			}
			candidate := check(ctx)
			if ctx.Err() != nil {
				return
			}
			wellFormed := len(candidate.dependencies) == len(fallback.dependencies)
			dependencyUnavailable := false
			dependencyDegraded := false
			if wellFormed {
				for index := range fallback.dependencies {
					dependency := candidate.dependencies[index]
					if dependency.Name != fallback.dependencies[index].Name || dependency.Details == nil ||
						(dependency.State != "healthy" && dependency.State != "degraded" && dependency.State != "unavailable") {
						wellFormed = false
						break
					}
					dependencyUnavailable = dependencyUnavailable || dependency.State == "unavailable"
					dependencyDegraded = dependencyDegraded || dependency.State == "degraded"
				}
			}
			if wellFormed && (candidate.unavailable != dependencyUnavailable || candidate.degraded != dependencyDegraded) {
				wellFormed = false
			}
			if wellFormed {
				result = candidate
				result.slot = slot
			} else {
				common.SysError("Relay readiness child returned malformed evidence: " + slot)
			}
		}
	}()
}

// Keep the process-local state lookup replaceable in controller tests without
// weakening the production service boundary.
var getPlatformGenerationWorkerRuntimeState = service.GetPlatformGenerationWorkerRuntimeState
var getProtectedPlatformRelayAPIReadiness = service.GetProtectedPlatformRelayAPIReadiness
var getPlatformProviderReadinessSummary = service.GetPlatformProviderReadinessSummary
var getPlatformArtifactReadinessStatus = service.GetPlatformArtifactReadinessStatus
var getPlatformRelayModelCatalogReadinessStatus = service.GetPlatformRelayModelCatalogReadinessStatus

func SubmitPlatformGeneration(c *gin.Context) {
	principal, ok := middleware.GetPlatformRelayPrincipal(c)
	if !ok {
		writePlatformGenerationError(c, http.StatusInternalServerError, "INTERNAL_ERROR", "The relay could not complete the request", true, nil)
		return
	}
	workerState := getPlatformGenerationWorkerRuntimeState()
	if workerState != service.PlatformGenerationWorkerStateRunning {
		// Gate before decoding or persistence: startup, failed startup, and
		// draining requests cannot create a job/outbox pair. GET and the separate
		// internal native-submit handler remain available.
		writePlatformGenerationError(
			c,
			http.StatusServiceUnavailable,
			model.PlatformGenerationErrorGenerationChannelUnavailable,
			"Generation workers are not accepting new jobs",
			true,
			map[string]any{"worker_state": workerState},
		)
		return
	}
	if c.ContentType() != "application/json" {
		writePlatformGenerationValidationError(c, platformGenerationValidationError{
			Issue: platformGenerationValidationIssue{
				Location: []string{"body"},
				Message:  "Content-Type must be application/json",
				Type:     "content_type_error",
			},
		})
		return
	}

	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, platformGenerationRequestBodyLimit)
	request, err := decodePlatformGenerationRequest(c.Request.Body)
	if err != nil {
		var validationError platformGenerationValidationError
		if !errors.As(err, &validationError) {
			validationError = platformGenerationValidationError{
				Issue: platformGenerationValidationIssue{
					Location: []string{"body"},
					Message:  "Request body is not valid JSON",
					Type:     "json_invalid",
				},
			}
		}
		writePlatformGenerationValidationError(c, validationError)
		return
	}

	idempotencyKey := c.GetHeader("Idempotency-Key")
	if len(idempotencyKey) < 8 || len(idempotencyKey) > 128 || strings.TrimSpace(idempotencyKey) != idempotencyKey {
		writePlatformGenerationValidationError(c, platformGenerationValidationError{
			Issue: platformGenerationValidationIssue{
				Location: []string{"header", "Idempotency-Key"},
				Message:  "Idempotency-Key must contain 8 to 128 characters",
				Type:     "value_error",
			},
		})
		return
	}

	accepted, err := service.SubmitPlatformGeneration(
		principal,
		request,
		idempotencyKey,
		c.GetString(common.RequestIdKey),
	)
	if err == nil {
		c.JSON(http.StatusAccepted, accepted)
		return
	}

	var conflict service.PlatformGenerationConflictError
	if errors.As(err, &conflict) {
		writePlatformGenerationError(c, http.StatusConflict, "IDEMPOTENCY_KEY_REUSED", "Idempotency-Key was already used with a different payload", false, nil)
		return
	}
	var callbackPolicy service.PlatformGenerationCallbackPolicyError
	if errors.As(err, &callbackPolicy) {
		message := "Callback URL is not allowed"
		if callbackPolicy.Code == "CALLBACK_NOT_CONFIGURED" {
			message = "No callback route is configured for this tenant"
		}
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, callbackPolicy.Code, message, false, nil)
		return
	}
	common.SysError("submit Platform generation: " + err.Error())
	writePlatformGenerationError(c, http.StatusInternalServerError, "INTERNAL_ERROR", "The relay could not complete the request", true, nil)
}

func GetPlatformGeneration(c *gin.Context) {
	principal, ok := middleware.GetPlatformRelayPrincipal(c)
	if !ok {
		writePlatformGenerationError(c, http.StatusInternalServerError, "INTERNAL_ERROR", "The relay could not complete the request", true, nil)
		return
	}
	jobID, err := uuid.Parse(c.Param("job_id"))
	if err != nil {
		writePlatformGenerationValidationError(c, platformGenerationValidationError{
			Issue: platformGenerationValidationIssue{
				Location: []string{"path", "job_id"},
				Message:  "Input should be a valid UUID",
				Type:     "uuid_parsing",
			},
		})
		return
	}
	snapshot, err := service.GetPlatformGeneration(principal, jobID.String())
	if err == nil {
		c.JSON(http.StatusOK, snapshot)
		return
	}
	if service.IsPlatformGenerationNotFound(err) {
		writePlatformGenerationError(c, http.StatusNotFound, "JOB_NOT_FOUND", "Generation job does not exist", false, nil)
		return
	}
	common.SysError("get Platform generation: " + err.Error())
	writePlatformGenerationError(c, http.StatusInternalServerError, "INTERNAL_ERROR", "The relay could not complete the request", true, nil)
}

func GetPlatformGenerationArtifactDownload(c *gin.Context) {
	principal, ok := middleware.GetPlatformRelayPrincipal(c)
	if !ok {
		writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", true, nil)
		return
	}
	download, err := service.GetPlatformGenerationArtifactSignedDownload(
		c.Request.Context(),
		principal,
		c.Param("job_id"),
		c.Param("asset_id"),
	)
	if err == nil {
		c.JSON(http.StatusOK, download)
		return
	}
	if service.IsPlatformGenerationNotFound(err) || errors.Is(err, service.ErrPlatformArtifactNotFound) {
		writePlatformGenerationError(c, http.StatusNotFound, model.PlatformGenerationErrorArtifactNotFound, "Artifact does not exist", false, nil)
		return
	}
	common.SysError("issue Platform generation artifact download: " + err.Error())
	writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", true, nil)
}

func DownloadPlatformGenerationFilesystemArtifact(c *gin.Context) {
	expires, err := strconv.ParseInt(c.Query("expires"), 10, 64)
	if err != nil {
		writePlatformGenerationError(c, http.StatusNotFound, model.PlatformGenerationErrorArtifactNotFound, "Artifact does not exist", false, nil)
		return
	}
	opened, err := service.OpenPlatformGenerationFilesystemArtifact(
		c.Request.Context(),
		c.Query("key"),
		expires,
		c.Query("signature"),
	)
	if err != nil {
		writePlatformGenerationError(c, http.StatusNotFound, model.PlatformGenerationErrorArtifactNotFound, "Artifact does not exist", false, nil)
		return
	}
	defer opened.Content.Close()
	c.Header("Content-Type", opened.ContentType)
	c.Header("Content-Length", strconv.FormatInt(opened.SizeBytes, 10))
	c.Header("Content-Disposition", `attachment; filename="artifact"`)
	c.Header("X-Content-Type-Options", "nosniff")
	c.Status(http.StatusOK)
	_, _ = io.Copy(c.Writer, opened.Content)
}

func ListPlatformGenerationSubmissionUnknown(c *gin.Context) {
	tenantID := c.Query("tenant_id")
	if !service.AuthenticatePlatformGenerationOperationsCredential(
		c.GetHeader("X-Relay-Operations-Token"),
		tenantID,
	) {
		writePlatformGenerationError(c, http.StatusUnauthorized, model.PlatformGenerationErrorOperationsUnauthorized, "Operations credential is not authorized", false, nil)
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
	result, err := service.ListPlatformGenerationUnknownSubmissions(tenantID, page, pageSize)
	if err == nil {
		c.JSON(http.StatusOK, result)
		return
	}
	common.SysError("list Platform generation unknown submissions: " + err.Error())
	writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", true, nil)
}

func ListPlatformGenerationProviderResultReconciliations(c *gin.Context) {
	tenantID := c.Query("tenant_id")
	if !service.AuthenticatePlatformGenerationOperationsCredential(
		c.GetHeader("X-Relay-Operations-Token"),
		tenantID,
	) {
		writePlatformGenerationError(c, http.StatusUnauthorized, model.PlatformGenerationErrorOperationsUnauthorized, "Operations credential is not authorized", false, nil)
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
	result, err := service.ListPlatformGenerationProviderResultReconciliations(tenantID, page, pageSize)
	if err == nil {
		c.JSON(http.StatusOK, result)
		return
	}
	common.SysError("list Platform generation provider-result reconciliations: " + err.Error())
	writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", true, nil)
}

func GetPlatformGenerationProviderResultReconciliation(c *gin.Context) {
	tenantID := c.Query("tenant_id")
	if !service.AuthenticatePlatformGenerationOperationsCredential(
		c.GetHeader("X-Relay-Operations-Token"),
		tenantID,
	) {
		writePlatformGenerationError(c, http.StatusUnauthorized, model.PlatformGenerationErrorOperationsUnauthorized, "Operations credential is not authorized", false, nil)
		return
	}
	result, err := service.GetPlatformGenerationProviderResultReconciliation(tenantID, c.Param("job_id"))
	if err == nil {
		c.JSON(http.StatusOK, result)
		return
	}
	if service.IsPlatformGenerationNotFound(err) {
		writePlatformGenerationError(c, http.StatusNotFound, model.PlatformGenerationErrorJobNotFound, "Generation job does not exist", false, nil)
		return
	}
	common.SysError("get Platform generation provider-result reconciliation: " + err.Error())
	writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", true, nil)
}

func GetPlatformGenerationSubmissionUnknown(c *gin.Context) {
	tenantID := c.Query("tenant_id")
	if !service.AuthenticatePlatformGenerationOperationsCredential(
		c.GetHeader("X-Relay-Operations-Token"),
		tenantID,
	) {
		writePlatformGenerationError(c, http.StatusUnauthorized, model.PlatformGenerationErrorOperationsUnauthorized, "Operations credential is not authorized", false, nil)
		return
	}
	result, err := service.GetPlatformGenerationUnknownSubmission(tenantID, c.Param("job_id"))
	if err == nil {
		c.JSON(http.StatusOK, result)
		return
	}
	if service.IsPlatformGenerationNotFound(err) {
		writePlatformGenerationError(c, http.StatusNotFound, model.PlatformGenerationErrorJobNotFound, "Generation job does not exist", false, nil)
		return
	}
	if errors.Is(err, model.ErrPlatformGenerationReconciliationConflict) {
		writePlatformGenerationError(c, http.StatusConflict, model.PlatformGenerationErrorReconciliationConflict, "Unknown submission state is internally inconsistent", false, nil)
		return
	}
	common.SysError("get Platform generation unknown submission: " + err.Error())
	writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", true, nil)
}

func ResolvePlatformGenerationSubmissionUnknown(c *gin.Context) {
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 16*1024)
	body, err := io.ReadAll(c.Request.Body)
	if err != nil {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return
	}
	if err := common.RejectDuplicateJSONKeys(body); err != nil {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return
	}
	root, err := decodePlatformJSONObject(
		json.RawMessage(body),
		[]string{"body"},
		[]string{"operation_id", "tenant_id", "outcome", "upstream_task_id", "expected_route_id", "expected_submission_attempt", "expected_reconciliation_token", "verification_reference", "approved_by", "approval_reason", "approval_key_id", "approval_signature", "synchronous_result"},
		[]string{"operation_id", "tenant_id", "outcome", "upstream_task_id", "expected_route_id", "expected_submission_attempt", "expected_reconciliation_token", "verification_reference", "approved_by", "approval_reason", "approval_key_id", "approval_signature"},
	)
	if err != nil {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return
	}
	for _, field := range []string{"operation_id", "tenant_id", "outcome", "upstream_task_id", "expected_reconciliation_token", "verification_reference", "approved_by", "approval_reason", "approval_key_id", "approval_signature"} {
		if requirePlatformJSONType(root[field], "string", []string{"body", field}) != nil {
			writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
			return
		}
	}
	for _, field := range []string{"expected_route_id", "expected_submission_attempt"} {
		if requirePlatformJSONType(root[field], "number", []string{"body", field}) != nil {
			writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
			return
		}
	}
	if synchronousRaw, present := root["synchronous_result"]; present {
		synchronous, nestedErr := decodePlatformJSONObject(
			synchronousRaw,
			[]string{"body", "synchronous_result"},
			[]string{"provider_model_id", "provider_response_sha256", "artifact_url", "provider_created_at", "generated_images", "output_tokens", "total_tokens"},
			[]string{"provider_model_id", "provider_response_sha256", "artifact_url", "provider_created_at", "generated_images", "output_tokens", "total_tokens"},
		)
		if nestedErr != nil {
			writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
			return
		}
		for _, field := range []string{"provider_model_id", "provider_response_sha256", "artifact_url", "provider_created_at"} {
			if requirePlatformJSONType(synchronous[field], "string", []string{"body", "synchronous_result", field}) != nil {
				writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
				return
			}
		}
		for _, field := range []string{"generated_images", "output_tokens", "total_tokens"} {
			if requirePlatformJSONType(synchronous[field], "number", []string{"body", "synchronous_result", field}) != nil {
				writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
				return
			}
		}
	}
	var request dto.PlatformGenerationReconciliationRequest
	if common.Unmarshal(body, &request) != nil ||
		!platformGenerationOperationIDPattern.MatchString(request.OperationID) ||
		(request.Outcome != "created" && request.Outcome != "not_created") ||
		(request.Outcome == "created" && strings.TrimSpace(request.UpstreamTaskID) == "") ||
		(request.Outcome == "not_created" && request.UpstreamTaskID != "") ||
		(request.Outcome == "not_created" && request.SynchronousResult != nil) ||
		strings.TrimSpace(request.UpstreamTaskID) != request.UpstreamTaskID ||
		len(request.UpstreamTaskID) > 191 ||
		request.ExpectedRouteID <= 0 || request.ExpectedSubmissionAttempt <= 0 ||
		!platformGenerationReconciliationTokenPattern.MatchString(request.ExpectedReconciliationToken) ||
		strings.TrimSpace(request.VerificationReference) != request.VerificationReference ||
		len(request.VerificationReference) < 1 || len(request.VerificationReference) > 191 ||
		strings.TrimSpace(request.ApprovedBy) != request.ApprovedBy ||
		len(request.ApprovedBy) < 1 || len(request.ApprovedBy) > 128 ||
		strings.TrimSpace(request.ApprovalReason) != request.ApprovalReason ||
		len(request.ApprovalReason) < 3 || len(request.ApprovalReason) > 240 ||
		!platformGenerationApprovalKeyIDPattern.MatchString(request.ApprovalKeyID) ||
		!platformGenerationApprovalSignaturePattern.MatchString(request.ApprovalSignature) {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return
	}
	if !service.AuthenticatePlatformGenerationOperationsCredential(
		c.GetHeader("X-Relay-Operations-Token"),
		request.TenantID,
	) {
		writePlatformGenerationError(c, http.StatusUnauthorized, model.PlatformGenerationErrorOperationsUnauthorized, "Operations credential is not authorized", false, nil)
		return
	}
	requestID := c.GetHeader("X-Request-ID")
	if !platformGenerationRequestIDPattern.MatchString(requestID) || requestID != c.GetString(common.RequestIdKey) {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "X-Request-ID is required for reconciliation", false, nil)
		return
	}
	approvalAuthorized, approvalErr := service.VerifyPlatformGenerationReconciliationApproval(
		request.TenantID,
		c.Param("job_id"),
		request,
	)
	if approvalErr != nil {
		common.SysError("verify Platform generation reconciliation approval: " + approvalErr.Error())
		writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", true, nil)
		return
	}
	if !approvalAuthorized {
		writePlatformGenerationError(c, http.StatusForbidden, model.PlatformGenerationErrorOperationsUnauthorized, "Approval signature is not authorized", false, nil)
		return
	}
	snapshot, receipt, idempotentReplay, err := service.ResolvePlatformGenerationUnknownSubmission(
		request.TenantID,
		c.Param("job_id"),
		request,
		requestID,
	)
	if err == nil {
		c.Header("X-Reconciliation-Event-ID", receipt.EventID)
		c.Header("X-Idempotent-Replay", strconv.FormatBool(idempotentReplay))
		c.JSON(http.StatusOK, snapshot)
		return
	}
	if service.IsPlatformGenerationNotFound(err) {
		writePlatformGenerationError(c, http.StatusNotFound, model.PlatformGenerationErrorJobNotFound, "Generation job does not exist", false, nil)
		return
	}
	if errors.Is(err, model.ErrPlatformGenerationReconciliationConflict) {
		writePlatformGenerationError(c, http.StatusConflict, model.PlatformGenerationErrorReconciliationConflict, "Reconciliation proof does not match the unknown submission", false, nil)
		return
	}
	common.SysError("resolve Platform generation submission: " + err.Error())
	writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", true, nil)
}

func GetPlatformGenerationSubmissionUnknownResult(c *gin.Context) {
	tenantID := c.Query("tenant_id")
	if !service.AuthenticatePlatformGenerationOperationsCredential(
		c.GetHeader("X-Relay-Operations-Token"),
		tenantID,
	) {
		writePlatformGenerationError(c, http.StatusUnauthorized, model.PlatformGenerationErrorOperationsUnauthorized, "Operations credential is not authorized", false, nil)
		return
	}
	operationID := c.Query("operation_id")
	if !platformGenerationOperationIDPattern.MatchString(operationID) {
		writePlatformGenerationError(c, http.StatusUnprocessableEntity, model.PlatformGenerationErrorRequestValidation, "Request validation failed", false, nil)
		return
	}
	result, err := service.GetPlatformGenerationReconciliationResult(tenantID, c.Param("job_id"), operationID)
	if err == nil {
		c.JSON(http.StatusOK, result)
		return
	}
	if service.IsPlatformGenerationNotFound(err) {
		writePlatformGenerationError(c, http.StatusNotFound, model.PlatformGenerationErrorJobNotFound, "Reconciliation result does not exist", false, nil)
		return
	}
	common.SysError("get Platform generation reconciliation result: " + err.Error())
	writePlatformGenerationError(c, http.StatusInternalServerError, model.PlatformGenerationErrorInternal, "The relay could not complete the request", true, nil)
}

func ListPlatformRelayModels(c *gin.Context) {
	catalog, err := service.GetPlatformRelayModelCatalog()
	if err != nil {
		common.SysError("read Platform Relay model catalog: " + err.Error())
		writePlatformGenerationError(c, http.StatusInternalServerError, "INTERNAL_ERROR", "The relay could not complete the request", true, nil)
		return
	}
	etag := fmt.Sprintf("\"%s\"", catalog.CatalogRevision)
	setPlatformRelayBuildHeaders(c)
	setPlatformRelayCacheHeaders(c, etag)
	if c.GetHeader("If-None-Match") == etag {
		c.Status(http.StatusNotModified)
		return
	}
	c.JSON(http.StatusOK, catalog)
}

func GetPlatformRelayModel(c *gin.Context) {
	resource, ok, err := service.GetPlatformRelayModel(c.Param("model"))
	if err != nil {
		common.SysError("read Platform Relay model capability: " + err.Error())
		writePlatformGenerationError(c, http.StatusInternalServerError, "INTERNAL_ERROR", "The relay could not complete the request", true, nil)
		return
	}
	if !ok {
		writePlatformGenerationError(c, http.StatusNotFound, "MODEL_NOT_FOUND", "Generation model does not exist", false, nil)
		return
	}
	etag := fmt.Sprintf("\"%s\"", resource.CapabilityRevision)
	setPlatformRelayBuildHeaders(c)
	setPlatformRelayCacheHeaders(c, etag)
	if c.GetHeader("If-None-Match") == etag {
		c.Status(http.StatusNotModified)
		return
	}
	c.JSON(http.StatusOK, resource)
}

func PlatformRelayLive(c *gin.Context) {
	c.JSON(http.StatusOK, platformRelayHealthResponse{
		State:        "healthy",
		Dependencies: make([]platformRelayDependencyHealth, 0),
	})
}

func PlatformRelayRuntimeBuildIdentity(c *gin.Context) {
	if !service.AuthenticatePlatformRelayRuntimeIdentity(
		c.GetHeader(constant.HeaderPlatformGenerationInternalAdmission),
	) {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "invalid runtime identity admission"})
		return
	}
	acceptanceAudit, err := service.GetPlatformRouteAcceptanceAudit()
	if err != nil {
		common.SysError("read Platform Relay route acceptance audit: " + err.Error())
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "relay route acceptance configuration is unavailable"})
		return
	}
	c.Header("Cache-Control", "no-store")
	c.JSON(http.StatusOK, gin.H{
		"schema_version":   1,
		"kind":             "relay_runtime_build_identity",
		"candidate":        service.GetPlatformRelayBuildProvenance(),
		"route_acceptance": acceptanceAudit,
	})
}

// PlatformRelayModelReleaseEvidence exposes only server-computed, secret-free
// release readiness. It shares the internal runtime admission boundary and is
// never authenticated by a browser or a new-api operator session.
func PlatformRelayModelReleaseEvidence(c *gin.Context) {
	if !service.AuthenticatePlatformRelayRuntimeIdentity(
		c.GetHeader(constant.HeaderPlatformGenerationInternalAdmission),
	) {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "invalid runtime identity admission"})
		return
	}
	projection, err := service.GetPlatformModelReleaseEvidenceProjection()
	if err != nil {
		common.SysError("read Platform Relay model release evidence: " + err.Error())
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "relay model release evidence is unavailable"})
		return
	}
	c.Header("Cache-Control", "no-store")
	c.JSON(http.StatusOK, projection)
}

func PlatformRelayReady(c *gin.Context) {
	setPlatformRelayReadyInstanceHeader(c)
	setPlatformRelayBuildHeaders(c)
	readinessContext, cancelReadiness := context.WithTimeout(c.Request.Context(), 2500*time.Millisecond)
	defer cancelReadiness()
	state := "healthy"
	status := http.StatusOK
	dependencies := make([]platformRelayDependencyHealth, 0, 10)
	markUnavailable := func() {
		state = "unavailable"
		status = http.StatusServiceUnavailable
	}
	markDegraded := func() {
		if state == "healthy" {
			state = "degraded"
		}
	}
	var readinessDB = model.DB
	if readinessDB != nil {
		readinessDB = readinessDB.WithContext(readinessContext)
	}
	readinessDBForChild := func(ctx context.Context) *gorm.DB {
		if readinessDB == nil || ctx == nil {
			return nil
		}
		return readinessDB.WithContext(ctx)
	}

	databaseState := "healthy"
	schemaState := "unavailable"
	schemaDetails := make(map[string]any)
	databaseRole := model.RelayRuntimeDatabaseRoleStatus{
		Required: model.RelayDatabaseRoleAttestationRequired(), State: "healthy",
	}
	var schemaStatus model.RelaySchemaStatus
	var databaseEvidenceErr error
	if databaseRole.Required {
		schemaStatus, databaseRole, databaseEvidenceErr = getProtectedPlatformRelayAPIReadiness(readinessContext)
	} else if model.DB == nil {
		databaseEvidenceErr = errors.New("Relay database is unavailable")
	} else {
		requestDB := readinessDB
		sqlDB, err := requestDB.DB()
		if err != nil {
			databaseEvidenceErr = err
		} else if err := sqlDB.PingContext(readinessContext); err != nil {
			databaseEvidenceErr = err
		} else if schemaStatus, err = model.GetRelaySchemaStatus(requestDB); err != nil {
			databaseEvidenceErr = err
		} else if databaseRole, err = model.GetRelayRuntimeDatabaseRoleStatus(requestDB); err != nil {
			databaseEvidenceErr = err
		}
	}
	if databaseEvidenceErr != nil {
		databaseState = "unavailable"
		databaseRole.State = "unavailable"
		markUnavailable()
	}
	dependencies = append(dependencies, platformRelayDependencyHealth{
		Name:    "database",
		State:   databaseState,
		Details: make(map[string]any),
	})

	if databaseEvidenceErr == nil {
		schemaDetails = map[string]any{
			"classification":          schemaStatus.Classification,
			"current_version":         schemaStatus.CurrentVersion,
			"target_version":          schemaStatus.TargetVersion,
			"min_version":             schemaStatus.MinVersion,
			"max_version":             schemaStatus.MaxVersion,
			"state":                   schemaStatus.State,
			"dirty":                   schemaStatus.Dirty,
			"catalog_sha256":          schemaStatus.CatalogSHA256,
			"expected_catalog_sha256": schemaStatus.ExpectedCatalogSHA256,
		}
		switch schemaStatus.Classification {
		case model.RelaySchemaStatusCurrent:
			schemaState = "healthy"
		case model.RelaySchemaStatusCompatible:
			schemaState = "degraded"
			markDegraded()
		default:
			markUnavailable()
		}
	} else {
		schemaDetails["classification"] = model.RelaySchemaStatusUnavailable
		markUnavailable()
	}
	dependencies = append(dependencies, platformRelayDependencyHealth{
		Name:    "database_schema",
		State:   schemaState,
		Details: schemaDetails,
	})

	databaseRoleState := databaseRole.State
	if databaseEvidenceErr != nil {
		databaseRoleState = "unavailable"
		markUnavailable()
	}
	dependencies = append(dependencies, platformRelayDependencyHealth{
		Name:  "database_role",
		State: databaseRoleState,
		Details: map[string]any{
			"required": databaseRole.Required,
			"role":     databaseRole.Role,
		},
	})

	compatEnabled := service.PlatformRelayCompatEnabled()
	workersEnabled := service.PlatformGenerationWorkersEnabled()
	workerRuntimeState := getPlatformGenerationWorkerRuntimeState()
	workerRuntimeRunning := workerRuntimeState == service.PlatformGenerationWorkerStateRunning
	compatDetails := map[string]any{
		"enabled":                compatEnabled,
		"workers_enabled":        workersEnabled,
		"worker_runtime_state":   workerRuntimeState,
		"worker_runtime_running": workerRuntimeRunning,
	}

	protectedRuntime := model.RelayDatabaseRoleAttestationRequired()
	concurrentSlotOrder := []string{
		"codex", "native_billing", "principals", "setup",
		"model_catalog", "redis", "artifacts", "callbacks", "provider",
	}
	concurrentFallbacks := map[string]platformRelayConcurrentReadinessResult{
		"codex": {unavailable: true, dependencies: []platformRelayDependencyHealth{{
			Name: "codex_credential_lifecycle", State: "unavailable", Details: map[string]any{"protected": protectedRuntime},
		}}},
		"native_billing": {unavailable: true, dependencies: []platformRelayDependencyHealth{{
			Name: "native_billing_lifecycle", State: "unavailable", Details: map[string]any{"protected": protectedRuntime},
		}}},
		"principals": {unavailable: true, dependencies: []platformRelayDependencyHealth{{
			Name: "platform_service_principals", State: "unavailable", Details: map[string]any{"protected": protectedRuntime},
		}}},
		"setup": {unavailable: true, dependencies: []platformRelayDependencyHealth{{
			Name: "production_setup", State: "unavailable", Details: map[string]any{"required": true, "ready": false},
		}}},
		"model_catalog": {unavailable: true, dependencies: []platformRelayDependencyHealth{{
			Name: "platform_relay_compat", State: "unavailable", Details: compatDetails,
		}}},
		"redis": {unavailable: true, dependencies: []platformRelayDependencyHealth{{
			Name: "redis", State: "unavailable", Details: map[string]any{"enabled": common.RedisEnabled},
		}}},
		"artifacts": {unavailable: true, dependencies: []platformRelayDependencyHealth{
			{Name: "artifact_store", State: "unavailable", Details: map[string]any{"required": true, "configured": false}},
			{Name: "artifact_cleanup", State: "unavailable", Details: map[string]any{"enabled": true}},
		}},
		"callbacks": {unavailable: true, dependencies: []platformRelayDependencyHealth{{
			Name: "generation_callbacks", State: "unavailable", Details: map[string]any{"enabled": compatEnabled && workersEnabled},
		}}},
		"provider": {degraded: true, dependencies: []platformRelayDependencyHealth{{
			Name: "provider_runtime", State: "degraded", Details: map[string]any{"enabled": compatEnabled, "inspection_available": false},
		}}},
	}
	concurrentResults := make(chan platformRelayConcurrentReadinessResult, len(concurrentSlotOrder))
	launchPlatformRelayReadinessChild(readinessContext, "codex", concurrentFallbacks["codex"], concurrentResults, func(childContext context.Context) platformRelayConcurrentReadinessResult {
		state := "healthy"
		enabled, err := service.CodexCredentialAutoRefreshEnabled()
		if err != nil || service.ValidateCodexCredentialAutoRefreshLifecycleWithDB(readinessDBForChild(childContext), enabled, protectedRuntime) != nil {
			state = "unavailable"
		}
		return platformRelayConcurrentReadinessResult{
			unavailable: state == "unavailable",
			dependencies: []platformRelayDependencyHealth{{
				Name: "codex_credential_lifecycle", State: state,
				Details: map[string]any{"auto_refresh_enabled": enabled, "protected": protectedRuntime},
			}},
		}
	})
	launchPlatformRelayReadinessChild(readinessContext, "native_billing", concurrentFallbacks["native_billing"], concurrentResults, func(childContext context.Context) platformRelayConcurrentReadinessResult {
		state := "healthy"
		if service.ValidateProtectedPlatformNativeBillingStateWithDB(readinessDBForChild(childContext)) != nil {
			state = "unavailable"
		}
		return platformRelayConcurrentReadinessResult{
			unavailable: state == "unavailable",
			dependencies: []platformRelayDependencyHealth{{
				Name: "native_billing_lifecycle", State: state, Details: map[string]any{"protected": protectedRuntime},
			}},
		}
	})
	launchPlatformRelayReadinessChild(readinessContext, "principals", concurrentFallbacks["principals"], concurrentResults, func(childContext context.Context) platformRelayConcurrentReadinessResult {
		state := "healthy"
		if service.ValidateProtectedPlatformRelayServicePrincipalsWithDB(readinessDBForChild(childContext)) != nil {
			state = "unavailable"
		}
		return platformRelayConcurrentReadinessResult{
			unavailable: state == "unavailable",
			dependencies: []platformRelayDependencyHealth{{
				Name: "platform_service_principals", State: state, Details: map[string]any{"protected": protectedRuntime},
			}},
		}
	})
	launchPlatformRelayReadinessChild(readinessContext, "setup", concurrentFallbacks["setup"], concurrentResults, func(childContext context.Context) platformRelayConcurrentReadinessResult {
		required := common.IsProductionEnvironment() || protectedRuntime
		ready := !required || model.SetupReadyWithDB(readinessDBForChild(childContext))
		state := "healthy"
		if !ready {
			state = "unavailable"
		}
		return platformRelayConcurrentReadinessResult{
			unavailable: state == "unavailable",
			dependencies: []platformRelayDependencyHealth{{
				Name: "production_setup", State: state, Details: map[string]any{"required": required, "ready": ready},
			}},
		}
	})
	launchPlatformRelayReadinessChild(readinessContext, "model_catalog", concurrentFallbacks["model_catalog"], concurrentResults, func(context.Context) platformRelayConcurrentReadinessResult {
		result := platformRelayConcurrentReadinessResult{slot: "model_catalog"}
		compatState := "healthy"
		details := map[string]any{
			"enabled":                compatEnabled,
			"workers_enabled":        workersEnabled,
			"worker_runtime_state":   workerRuntimeState,
			"worker_runtime_running": workerRuntimeRunning,
		}
		if compatEnabled {
			catalog := getPlatformRelayModelCatalogReadinessStatus()
			details["configured"] = catalog.Configured
			details["model_count"] = catalog.ModelCount
			details["catalog_revision"] = catalog.CatalogRevision
			details["config_generation"] = catalog.ConfigGeneration
			details["proof_current"] = catalog.Current
			details["proof_error"] = catalog.ErrorCode
			if !catalog.VerifiedAt.IsZero() {
				details["verified_at"] = catalog.VerifiedAt
			}
			if !catalog.EvidenceExpiresAt.IsZero() {
				details["evidence_expires_at"] = catalog.EvidenceExpiresAt
			}
			// StartPlatformGenerationWorkers performs the full Redis/OBS/provider
			// configuration gate before publishing Running. Readiness consumes
			// that evidence plus the lock-free catalog proof; it never reloads.
			workerConfigurationValid := workersEnabled && workerRuntimeRunning
			details["worker_configuration_valid"] = workerConfigurationValid
			if !catalog.Current || !catalog.Configured || catalog.ModelCount < 1 || !workerConfigurationValid {
				compatState, result.unavailable = "unavailable", true
			}
		} else {
			compatState, result.degraded = "degraded", true
		}
		result.dependencies = []platformRelayDependencyHealth{{
			Name: "platform_relay_compat", State: compatState, Details: details,
		}}
		return result
	})
	launchPlatformRelayReadinessChild(readinessContext, "redis", concurrentFallbacks["redis"], concurrentResults, func(childContext context.Context) platformRelayConcurrentReadinessResult {
		redisState := "degraded"
		redisDetails := map[string]any{"enabled": common.RedisEnabled}
		redisRequired := compatEnabled && workersEnabled && service.PlatformRelayProductionSecurityEnabled()
		redisDetails["required"] = redisRequired
		result := platformRelayConcurrentReadinessResult{slot: "redis"}
		if common.RedisEnabled {
			redisState = "healthy"
			if common.RDB == nil {
				redisState, result.unavailable = "unavailable", true
			} else if _, err := common.RDB.Ping(childContext).Result(); err != nil {
				redisState, result.unavailable = "unavailable", true
			}
		} else if redisRequired {
			redisState, result.unavailable = "unavailable", true
		} else {
			result.degraded = true
		}
		result.dependencies = []platformRelayDependencyHealth{{Name: "redis", State: redisState, Details: redisDetails}}
		return result
	})
	launchPlatformRelayReadinessChild(readinessContext, "artifacts", concurrentFallbacks["artifacts"], concurrentResults, func(childContext context.Context) platformRelayConcurrentReadinessResult {
		result := platformRelayConcurrentReadinessResult{slot: "artifacts"}
		artifactReadiness, artifactReadinessErr := getPlatformArtifactReadinessStatus()
		generationStoreRequired := compatEnabled && workersEnabled
		artifactState := "healthy"
		artifactDetails := map[string]any{
			"required": artifactReadiness.Required, "configured": artifactReadiness.Configured,
			"kind": artifactReadiness.Kind, "binding_id": artifactReadiness.BindingID,
			"persistent":           artifactReadiness.Persistent,
			"current":              artifactReadiness.Current,
			"verified_at":          artifactReadiness.VerifiedAt,
			"store_health_healthy": artifactReadiness.StoreHealthHealthy,
			"store_health_fresh":   artifactReadiness.StoreHealthFresh,
			"store_health_error":   artifactReadiness.StoreHealthCurrentErrorCode,
			"store_health_at":      artifactReadiness.LastStoreHealthAt,
		}
		// This getter is process-memory only. Startup and the supervised cleanup
		// worker own store construction, live health, freshness, and binding CAS.
		if artifactReadinessErr != nil || !artifactReadiness.Current ||
			(generationStoreRequired && !artifactReadiness.Required) {
			artifactState, result.unavailable = "unavailable", true
		}
		cleanupState := "healthy"
		cleanupDetails := map[string]any{
			"enabled": true, "required": artifactReadiness.Required,
			"generation_enabled": compatEnabled && workersEnabled,
		}
		cleanupDetails["worker_running"] = artifactReadiness.CleanupWorkerRunning
		cleanupDetails["worker_stale"] = artifactReadiness.CleanupWorkerStale
		cleanupDetails["worker_current_error"] = artifactReadiness.CleanupWorkerCurrentErrorCode
		if !artifactReadiness.CleanupWorkerLastHeartbeatAt.IsZero() {
			cleanupDetails["worker_last_heartbeat_at"] = artifactReadiness.CleanupWorkerLastHeartbeatAt
		}
		if artifactReadinessErr != nil || !artifactReadiness.CleanupWorkerRunning ||
			artifactReadiness.CleanupWorkerStale || artifactReadiness.CleanupWorkerCurrentErrorCode != "" {
			cleanupState, result.unavailable = "unavailable", true
		}
		result.dependencies = []platformRelayDependencyHealth{
			{Name: "artifact_store", State: artifactState, Details: artifactDetails},
			{Name: "artifact_cleanup", State: cleanupState, Details: cleanupDetails},
		}
		return result
	})
	launchPlatformRelayReadinessChild(readinessContext, "callbacks", concurrentFallbacks["callbacks"], concurrentResults, func(childContext context.Context) platformRelayConcurrentReadinessResult {
		result := platformRelayConcurrentReadinessResult{slot: "callbacks"}
		callbackState := "degraded"
		callbackDetails := map[string]any{"enabled": compatEnabled && workersEnabled}
		if compatEnabled && workersEnabled {
			var counts model.PlatformGenerationCallbackCounts
			var err error
			childDB := readinessDBForChild(childContext)
			if childDB == nil {
				err = errors.New("generation callback database is unavailable")
			} else {
				counts, err = model.GetPlatformGenerationCallbackCountsWithDB(childDB)
			}
			if err != nil {
				callbackState, result.unavailable = "unavailable", true
			} else {
				callbackDetails["pending"], callbackDetails["claimed"] = counts.Pending, counts.Claimed
				callbackDetails["delivered"], callbackDetails["dead_letter"] = counts.Delivered, counts.DeadLetter
				if counts.Pending+counts.Claimed+counts.DeadLetter > 0 {
					result.degraded = true
				} else {
					callbackState = "healthy"
				}
			}
		} else {
			result.degraded = true
		}
		result.dependencies = []platformRelayDependencyHealth{{Name: "generation_callbacks", State: callbackState, Details: callbackDetails}}
		return result
	})
	launchPlatformRelayReadinessChild(readinessContext, "provider", concurrentFallbacks["provider"], concurrentResults, func(childContext context.Context) platformRelayConcurrentReadinessResult {
		result := platformRelayConcurrentReadinessResult{slot: "provider"}
		providerState := "degraded"
		providerDetails := map[string]any{"enabled": false}
		if compatEnabled {
			summary, err := getPlatformProviderReadinessSummary(childContext)
			if err != nil {
				// Provider-only observability loss must not remove the Relay API
				// from service. The independent database/schema/role proof above
				// remains fail-closed for an actual core database failure.
				providerState, result.degraded = "degraded", true
				providerDetails["enabled"] = true
				providerDetails["inspection_available"] = false
			} else {
				var productionCutoverReady bool
				providerState, productionCutoverReady = platformRelayProviderRuntimeClassification(summary)
				providerDetails = platformRelayProviderRuntimeDetails(summary, productionCutoverReady)
				result.degraded = providerState == "degraded"
			}
		} else {
			result.degraded = true
		}
		result.dependencies = []platformRelayDependencyHealth{{Name: "provider_runtime", State: providerState, Details: providerDetails}}
		return result
	})

	resultsBySlot, readinessTimedOut, readinessCollectionErr := collectPlatformRelayConcurrentReadinessResults(
		readinessContext, concurrentSlotOrder, concurrentResults,
	)
	if readinessTimedOut || readinessCollectionErr != nil || readinessContext.Err() != nil {
		markUnavailable()
	}
	appendConcurrentResult := func(slot string) {
		result, exists := resultsBySlot[slot]
		if !exists {
			// A malformed/duplicate result must never shorten the stable 13-item
			// wire contract. Render the canonical fail-closed slot evidence.
			result = concurrentFallbacks[slot]
		}
		dependencies = append(dependencies, result.dependencies...)
		if result.unavailable {
			markUnavailable()
		} else if result.degraded {
			markDegraded()
		}
	}
	for _, slot := range concurrentSlotOrder[:4] {
		appendConcurrentResult(slot)
	}
	appendConcurrentResult("model_catalog")
	for _, slot := range concurrentSlotOrder[5:] {
		appendConcurrentResult(slot)
	}

	c.JSON(status, platformRelayHealthResponse{State: state, Dependencies: dependencies})
}

// platformRelayProviderRuntimeDetails keeps the readiness wire contract in one
// place. The migration acceptance gate consumes these exact snake_case names.
func platformRelayProviderRuntimeDetails(
	summary service.PlatformProviderReadinessSummary,
	productionCutoverReady bool,
) map[string]any {
	return map[string]any{
		"enabled":                                summary.Enabled,
		"monitor_fresh":                          summary.MonitorFresh,
		"monitor_last_completed_at":              summary.MonitorLastCompletedAt,
		"monitor_freshness_seconds":              summary.MonitorFreshnessSeconds,
		"monitor_last_error_code":                summary.MonitorLastWorkerErrorCode,
		"active_alerts":                          summary.ActiveAlerts,
		"unavailable_routes":                     summary.UnavailableRoutes,
		"alert_backlog":                          summary.AlertBacklog,
		"alert_dead_letter":                      summary.AlertDeadLetter,
		"cost_incomplete":                        summary.CostIncomplete,
		"cost_backlog":                           summary.CostBacklog,
		"cost_dead_letter":                       summary.CostDeadLetter,
		"cost_successful_relay_jobs":             summary.CostSuccessfulRelayJobs,
		"cost_explicit_relay_jobs":               summary.CostExplicitRelayJobs,
		"native_billing_reconciliation_jobs":     summary.NativeBillingReconciliationJobs,
		"cost_reconciliation_complete":           summary.CostReconciliationComplete,
		"task_stage_backlog":                     summary.TaskStageBacklog,
		"task_stage_dead_letter":                 summary.TaskStageDeadLetter,
		"operations_snapshot_backlog":            summary.OperationsSnapshotBacklog,
		"operations_snapshot_dead_letter":        summary.OperationsSnapshotDeadLetter,
		"provider_result_reconciliation_backlog": summary.ProviderResultReconciliationBacklog,
		"production_cutover_ready":               productionCutoverReady,
	}
}

// platformRelayProviderRuntimeClassification separates service readiness from
// the stricter production cutover gate. Cost evidence remains visible and
// blocks release promotion, but it cannot make the Platform receiver wait on
// the Relay that must deliver that same evidence.
func platformRelayProviderRuntimeClassification(
	summary service.PlatformProviderReadinessSummary,
) (state string, productionCutoverReady bool) {
	// Cutover evidence is environment-independent. In particular, a staging
	// candidate may not promote merely because it is not the final production
	// process: it must prove the same delivered provider-cost lifecycle first.
	// The health endpoint remains available as degraded while that evidence is
	// absent, which avoids a Relay/Platform cold-start dependency cycle.
	operationalReady := summary.Enabled &&
		summary.MonitorFresh && summary.MonitorLastWorkerErrorCode == "" &&
		summary.ActiveAlerts == 0 && summary.UnavailableRoutes == 0 &&
		summary.AlertBacklog == 0 && summary.AlertDeadLetter == 0 &&
		summary.TaskStageBacklog == 0 && summary.TaskStageDeadLetter == 0 &&
		summary.OperationsSnapshotBacklog == 0 && summary.OperationsSnapshotDeadLetter == 0 &&
		summary.ProviderResultReconciliationBacklog == 0
	costReady := summary.CostIncomplete == 0 &&
		summary.CostBacklog == 0 && summary.CostDeadLetter == 0 &&
		summary.NativeBillingReconciliationJobs == 0 &&
		summary.CostSuccessfulRelayJobs > 0 &&
		summary.CostReconciliationComplete
	productionCutoverReady = !summary.Degraded && operationalReady && costReady
	if summary.Degraded || !productionCutoverReady {
		return "degraded", productionCutoverReady
	}
	return "healthy", productionCutoverReady
}

func platformArtifactCleanupReadinessState(
	counts model.PlatformArtifactUploadIntentCounts,
	worker service.PlatformArtifactCleanupWorkerStatus,
	production bool,
) string {
	workerUnavailable := !worker.Started || !worker.Running || worker.Stale || worker.CurrentErrorCode != ""
	if production && workerUnavailable {
		return "unavailable"
	}
	if counts.Claimed > 0 || counts.Quarantined > 0 || counts.Due > 0 || counts.DeadLetter > 0 ||
		counts.Retrying > 0 || counts.CleanedRetrying > 0 {
		return "degraded"
	}
	if workerUnavailable {
		return "degraded"
	}
	return "healthy"
}

func platformArtifactCleanupMaintenanceRequired(counts model.PlatformArtifactUploadIntentCounts) bool {
	return counts.Pending+counts.Claimed+counts.Quarantined+counts.Cleaned+counts.DeadLetter > 0
}

func setPlatformRelayReadyInstanceHeader(c *gin.Context) {
	c.Header("X-Relay-Instance-ID", service.GetPlatformRelayBuildProvenance().InstanceID)
}

func setPlatformRelayBuildHeaders(c *gin.Context) {
	provenance := service.GetPlatformRelayBuildProvenance()
	c.Header("X-Relay-Upstream-Revision", provenance.UpstreamGitRevision)
	if provenance.SourceGitRevision != "" {
		c.Header("X-Relay-Source-Revision", provenance.SourceGitRevision)
	}
	if provenance.ImageDigest != "" {
		c.Header("X-Relay-Image-Digest", provenance.ImageDigest)
	}
	if provenance.SourceSnapshotSHA256 != "" {
		c.Header("X-Relay-Source-Snapshot-SHA256", provenance.SourceSnapshotSHA256)
	}
	if provenance.SourceSnapshotFiles > 0 {
		c.Header("X-Relay-Source-File-Count", strconv.Itoa(provenance.SourceSnapshotFiles))
	}
}

func decodePlatformGenerationRequest(reader io.Reader) (dto.PlatformGenerationRequest, error) {
	body, err := io.ReadAll(reader)
	if err != nil {
		return dto.PlatformGenerationRequest{}, err
	}
	root, err := decodePlatformJSONObject(
		json.RawMessage(body),
		[]string{"body"},
		[]string{"client_reference_id", "model", "expected_capability_revision", "mode", "inputs", "output", "metadata", "callback"},
		[]string{"model", "expected_capability_revision", "mode", "inputs"},
	)
	if err != nil {
		return dto.PlatformGenerationRequest{}, err
	}
	if raw, ok := root["client_reference_id"]; ok {
		jsonType := common.GetJsonType(raw)
		if jsonType != "string" && jsonType != "null" {
			return dto.PlatformGenerationRequest{}, platformJSONIssue(
				[]string{"body", "client_reference_id"},
				"value must be a string or null",
				"type_error",
			)
		}
	}
	for _, field := range []string{"model", "expected_capability_revision", "mode"} {
		if raw, ok := root[field]; ok {
			if err := requirePlatformJSONType(raw, "string", []string{"body", field}); err != nil {
				return dto.PlatformGenerationRequest{}, err
			}
		}
	}

	inputs, err := decodePlatformJSONObject(
		root["inputs"],
		[]string{"body", "inputs"},
		[]string{"prompt", "assets"},
		[]string{"prompt"},
	)
	if err != nil {
		return dto.PlatformGenerationRequest{}, err
	}
	if err := requirePlatformJSONType(inputs["prompt"], "string", []string{"body", "inputs", "prompt"}); err != nil {
		return dto.PlatformGenerationRequest{}, err
	}
	if assetsRaw, ok := inputs["assets"]; ok {
		if err := requirePlatformJSONType(assetsRaw, "array", []string{"body", "inputs", "assets"}); err != nil {
			return dto.PlatformGenerationRequest{}, err
		}
		var assets []json.RawMessage
		if err := common.Unmarshal(assetsRaw, &assets); err != nil {
			return dto.PlatformGenerationRequest{}, platformJSONIssue([]string{"body", "inputs", "assets"}, "assets must be an array", "type_error")
		}
		for index, assetRaw := range assets {
			location := []string{"body", "inputs", "assets", fmt.Sprintf("%d", index)}
			asset, err := decodePlatformJSONObject(assetRaw, location, []string{"url", "media_type"}, []string{"url", "media_type"})
			if err != nil {
				return dto.PlatformGenerationRequest{}, err
			}
			for _, field := range []string{"url", "media_type"} {
				if err := requirePlatformJSONType(asset[field], "string", append(append([]string{}, location...), field)); err != nil {
					return dto.PlatformGenerationRequest{}, err
				}
			}
		}
	}

	if outputRaw, ok := root["output"]; ok {
		output, err := decodePlatformJSONObject(
			outputRaw,
			[]string{"body", "output"},
			[]string{"duration_seconds", "aspect_ratio", "resolution", "count", "face_enabled"},
			nil,
		)
		if err != nil {
			return dto.PlatformGenerationRequest{}, err
		}
		for _, field := range []string{"duration_seconds", "count"} {
			if raw, ok := output[field]; ok {
				if err := requirePlatformJSONType(raw, "number", []string{"body", "output", field}); err != nil {
					return dto.PlatformGenerationRequest{}, err
				}
			}
		}
		for _, field := range []string{"aspect_ratio", "resolution"} {
			if raw, ok := output[field]; ok {
				if err := requirePlatformJSONType(raw, "string", []string{"body", "output", field}); err != nil {
					return dto.PlatformGenerationRequest{}, err
				}
			}
		}
		if raw, ok := output["face_enabled"]; ok {
			if err := requirePlatformJSONType(raw, "boolean", []string{"body", "output", "face_enabled"}); err != nil {
				return dto.PlatformGenerationRequest{}, err
			}
		}
	}

	if metadataRaw, ok := root["metadata"]; ok {
		if err := requirePlatformJSONType(metadataRaw, "object", []string{"body", "metadata"}); err != nil {
			return dto.PlatformGenerationRequest{}, err
		}
	}
	if callbackRaw, ok := root["callback"]; ok {
		callback, err := decodePlatformJSONObject(callbackRaw, []string{"body", "callback"}, []string{"url"}, []string{"url"})
		if err != nil {
			return dto.PlatformGenerationRequest{}, err
		}
		if err := requirePlatformJSONType(callback["url"], "string", []string{"body", "callback", "url"}); err != nil {
			return dto.PlatformGenerationRequest{}, err
		}
	}

	request := dto.NewPlatformGenerationRequest()
	if err := common.Unmarshal(body, &request); err != nil {
		return dto.PlatformGenerationRequest{}, platformJSONIssue([]string{"body"}, "Request body is not valid JSON", "json_invalid")
	}
	if !platformGenerationModelIDPattern.MatchString(request.Model) {
		return dto.PlatformGenerationRequest{}, platformJSONIssue([]string{"body", "model"}, "model is invalid", "value_error")
	}
	if err := request.Validate(); err != nil {
		return dto.PlatformGenerationRequest{}, platformJSONIssue([]string{"body"}, err.Error(), "value_error")
	}
	if service.PlatformRelayProductionSecurityEnabled() {
		for _, asset := range request.Inputs.Assets {
			if !strings.HasPrefix(strings.ToLower(asset.URL), "https://") {
				return dto.PlatformGenerationRequest{}, platformJSONIssue([]string{"body", "inputs", "assets"}, "asset URLs must use HTTPS in production", "value_error")
			}
		}
		if request.Callback != nil && !strings.HasPrefix(strings.ToLower(request.Callback.URL), "https://") {
			return dto.PlatformGenerationRequest{}, platformJSONIssue([]string{"body", "callback", "url"}, "callback URL must use HTTPS in production", "value_error")
		}
	}
	return request, nil
}

func decodePlatformJSONObject(
	raw json.RawMessage,
	location []string,
	allowed []string,
	required []string,
) (map[string]json.RawMessage, error) {
	if common.GetJsonType(raw) != "object" {
		return nil, platformJSONIssue(location, "value must be an object", "type_error")
	}
	object := make(map[string]json.RawMessage)
	if err := common.Unmarshal(raw, &object); err != nil {
		return nil, platformJSONIssue(location, "value must be an object", "type_error")
	}
	allowedSet := make(map[string]struct{}, len(allowed))
	for _, field := range allowed {
		allowedSet[field] = struct{}{}
	}
	unknown := make([]string, 0)
	for field := range object {
		if _, ok := allowedSet[field]; !ok {
			unknown = append(unknown, field)
		}
	}
	sort.Strings(unknown)
	if len(unknown) > 0 {
		return nil, platformJSONIssue(append(append([]string{}, location...), unknown[0]), "Extra inputs are not permitted", "extra_forbidden")
	}
	missing := make([]string, 0)
	for _, field := range required {
		if _, ok := object[field]; !ok {
			missing = append(missing, field)
		}
	}
	sort.Strings(missing)
	if len(missing) > 0 {
		return nil, platformJSONIssue(append(append([]string{}, location...), missing[0]), "Field required", "missing")
	}
	return object, nil
}

func requirePlatformJSONType(raw json.RawMessage, expected string, location []string) error {
	if common.GetJsonType(raw) != expected {
		return platformJSONIssue(location, fmt.Sprintf("value must be a %s", expected), "type_error")
	}
	return nil
}

func platformJSONIssue(location []string, message string, issueType string) platformGenerationValidationError {
	return platformGenerationValidationError{
		Issue: platformGenerationValidationIssue{
			Location: location,
			Message:  message,
			Type:     issueType,
		},
	}
}

func writePlatformGenerationValidationError(c *gin.Context, err platformGenerationValidationError) {
	writePlatformGenerationError(
		c,
		http.StatusUnprocessableEntity,
		"REQUEST_VALIDATION_FAILED",
		"Request validation failed",
		false,
		map[string]any{"issues": []platformGenerationValidationIssue{err.Issue}},
	)
}

func writePlatformGenerationError(
	c *gin.Context,
	status int,
	code string,
	message string,
	retryable bool,
	details map[string]any,
) {
	if !model.IsPlatformGenerationPublicErrorCode(code) {
		common.SysError("rejected unregistered Platform generation public error code")
		code = model.PlatformGenerationErrorInternal
		message = "The relay could not complete the request"
		retryable = true
	}
	if details == nil {
		details = make(map[string]any)
	}
	c.JSON(status, dto.PlatformGenerationErrorEnvelope{
		APIVersion:    dto.PlatformRelayAPIVersion,
		SchemaVersion: dto.PlatformRelaySchemaVersion,
		Error: dto.PlatformGenerationErrorEnvelopeDetail{
			Code:      code,
			Message:   message,
			Retryable: retryable,
			RequestID: c.GetString(common.RequestIdKey),
			Details:   details,
		},
	})
}

func setPlatformRelayCacheHeaders(c *gin.Context, etag string) {
	c.Header("ETag", etag)
	c.Header("Cache-Control", "private, max-age=60, must-revalidate")
	c.Header("Vary", "X-Client-ID, X-API-Key")
}
