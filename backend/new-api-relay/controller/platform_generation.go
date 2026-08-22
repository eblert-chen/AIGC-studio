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

// Keep the process-local state lookup replaceable in controller tests without
// weakening the production service boundary.
var getPlatformGenerationWorkerRuntimeState = service.GetPlatformGenerationWorkerRuntimeState

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
	root, err := decodePlatformJSONObject(
		json.RawMessage(body),
		[]string{"body"},
		[]string{"operation_id", "tenant_id", "outcome", "upstream_task_id", "expected_route_id", "expected_submission_attempt", "expected_reconciliation_token", "verification_reference", "approved_by", "approval_reason", "approval_key_id", "approval_signature"},
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
	var request dto.PlatformGenerationReconciliationRequest
	if common.Unmarshal(body, &request) != nil ||
		!platformGenerationOperationIDPattern.MatchString(request.OperationID) ||
		(request.Outcome != "created" && request.Outcome != "not_created") ||
		(request.Outcome == "created" && strings.TrimSpace(request.UpstreamTaskID) == "") ||
		(request.Outcome == "not_created" && request.UpstreamTaskID != "") ||
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

func PlatformRelayReady(c *gin.Context) {
	setPlatformRelayReadyInstanceHeader(c)
	setPlatformRelayBuildHeaders(c)
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

	databaseState := "healthy"
	if err := model.PingDB(); err != nil {
		databaseState = "unavailable"
		markUnavailable()
	}
	dependencies = append(dependencies, platformRelayDependencyHealth{
		Name:    "database",
		State:   databaseState,
		Details: make(map[string]any),
	})

	schemaState := "unavailable"
	schemaDetails := make(map[string]any)
	schemaStatus, schemaErr := model.GetRelaySchemaStatus(model.DB)
	if schemaErr == nil {
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

	databaseRole, databaseRoleErr := model.GetRelayRuntimeDatabaseRoleStatus(model.DB)
	databaseRoleState := databaseRole.State
	if databaseRoleErr != nil {
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

	codexLifecycleState := "healthy"
	codexRefreshEnabled, codexRefreshErr := service.CodexCredentialAutoRefreshEnabled()
	if codexRefreshErr != nil || service.ValidateCodexCredentialAutoRefreshLifecycle(
		codexRefreshEnabled, model.RelayDatabaseRoleAttestationRequired(),
	) != nil {
		codexLifecycleState = "unavailable"
		markUnavailable()
	}
	dependencies = append(dependencies, platformRelayDependencyHealth{
		Name:  "codex_credential_lifecycle",
		State: codexLifecycleState,
		Details: map[string]any{
			"auto_refresh_enabled": codexRefreshEnabled,
			"protected":            model.RelayDatabaseRoleAttestationRequired(),
		},
	})

	nativeBillingState := "healthy"
	if err := service.ValidateProtectedPlatformNativeBillingState(); err != nil {
		nativeBillingState = "unavailable"
		markUnavailable()
	}
	dependencies = append(dependencies, platformRelayDependencyHealth{
		Name:  "native_billing_lifecycle",
		State: nativeBillingState,
		Details: map[string]any{
			"protected": model.RelayDatabaseRoleAttestationRequired(),
		},
	})

	servicePrincipalState := "healthy"
	if err := service.ValidateProtectedPlatformRelayServicePrincipals(); err != nil {
		servicePrincipalState = "unavailable"
		markUnavailable()
	}
	dependencies = append(dependencies, platformRelayDependencyHealth{
		Name:  "platform_service_principals",
		State: servicePrincipalState,
		Details: map[string]any{
			"protected": model.RelayDatabaseRoleAttestationRequired(),
		},
	})

	setupRequired := common.IsProductionEnvironment() || model.RelayDatabaseRoleAttestationRequired()
	setupState := "healthy"
	setupReady := !setupRequired || model.SetupReady()
	if !setupReady {
		setupState = "unavailable"
		markUnavailable()
	}
	dependencies = append(dependencies, platformRelayDependencyHealth{
		Name:  "production_setup",
		State: setupState,
		Details: map[string]any{
			"required": setupRequired,
			"ready":    setupReady,
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
	compatState := "healthy"
	if compatEnabled {
		catalog, err := service.GetPlatformRelayModelCatalog()
		if err != nil {
			compatState = "unavailable"
			compatDetails["configured"] = false
			markUnavailable()
		} else {
			compatDetails["configured"] = true
			compatDetails["model_count"] = len(catalog.Data)
			if len(catalog.Data) == 0 {
				compatState = "unavailable"
				markUnavailable()
			}
		}
		workerConfigurationErr := service.ValidatePlatformGenerationWorkerConfiguration()
		workerConfigurationValid := workerConfigurationErr == nil && workersEnabled
		compatDetails["worker_configuration_valid"] = workerConfigurationValid
		if !workerConfigurationValid || !workerRuntimeRunning {
			compatState = "unavailable"
			markUnavailable()
		}
	} else {
		compatState = "degraded"
		markDegraded()
	}
	dependencies = append(dependencies, platformRelayDependencyHealth{
		Name:    "platform_relay_compat",
		State:   compatState,
		Details: compatDetails,
	})

	redisState := "degraded"
	redisDetails := map[string]any{"enabled": common.RedisEnabled}
	redisRequired := compatEnabled && workersEnabled && service.PlatformRelayProductionSecurityEnabled()
	redisDetails["required"] = redisRequired
	if common.RedisEnabled {
		redisState = "healthy"
		if common.RDB == nil {
			redisState = "unavailable"
			markUnavailable()
		} else {
			ctx, cancel := context.WithTimeout(c.Request.Context(), 2*time.Second)
			_, err := common.RDB.Ping(ctx).Result()
			cancel()
			if err != nil {
				redisState = "unavailable"
				markUnavailable()
			}
		}
	} else if redisRequired {
		redisState = "unavailable"
		markUnavailable()
	} else {
		markDegraded()
	}
	dependencies = append(dependencies, platformRelayDependencyHealth{
		Name:    "redis",
		State:   redisState,
		Details: redisDetails,
	})

	artifactIntentCounts, artifactIntentCountsErr := model.GetPlatformArtifactUploadIntentCounts()
	artifactMaintenanceRequired := artifactIntentCountsErr == nil &&
		platformArtifactCleanupMaintenanceRequired(artifactIntentCounts)
	artifactStoreRequired := (compatEnabled && workersEnabled) || artifactMaintenanceRequired

	artifactState := "healthy"
	artifactDetails := map[string]any{"required": artifactStoreRequired}
	if artifactStoreRequired {
		store, err := service.NewPlatformArtifactStoreFromEnvironment()
		if err != nil {
			artifactState = "unavailable"
			artifactDetails["configured"] = false
			markUnavailable()
		} else {
			artifactDetails["configured"] = true
			artifactDetails["kind"] = store.Kind()
			artifactDetails["binding_id"] = store.BindingID()
			persistent := store.Persistent()
			artifactDetails["persistent"] = persistent
			ctx, cancel := context.WithTimeout(c.Request.Context(), 2*time.Second)
			healthErr := store.Healthcheck(ctx)
			cancel()
			if closer, ok := store.(interface{ Close() }); ok {
				closer.Close()
			}
			if healthErr != nil || !persistent {
				artifactState = "unavailable"
				markUnavailable()
			} else {
				artifactState = "healthy"
			}
		}
	} else {
		artifactDetails["configured"] = false
	}
	dependencies = append(dependencies, platformRelayDependencyHealth{
		Name:    "artifact_store",
		State:   artifactState,
		Details: artifactDetails,
	})

	artifactCleanupState := "degraded"
	artifactCleanupDetails := map[string]any{
		"enabled":              true,
		"maintenance_required": artifactMaintenanceRequired,
		"generation_enabled":   compatEnabled && workersEnabled,
	}
	workerStatus := service.GetPlatformArtifactCleanupWorkerStatus()
	artifactCleanupDetails["worker_started"] = workerStatus.Started
	artifactCleanupDetails["worker_running"] = workerStatus.Running
	artifactCleanupDetails["worker_stale"] = workerStatus.Stale
	artifactCleanupDetails["worker_current_error"] = workerStatus.CurrentErrorCode
	artifactCleanupDetails["worker_consecutive_errors"] = workerStatus.ConsecutiveErrors
	if !workerStatus.StartedAt.IsZero() {
		artifactCleanupDetails["worker_started_at"] = workerStatus.StartedAt
	}
	if !workerStatus.LastHeartbeatAt.IsZero() {
		artifactCleanupDetails["worker_last_heartbeat_at"] = workerStatus.LastHeartbeatAt
	}
	if !workerStatus.LastSuccessAt.IsZero() {
		artifactCleanupDetails["worker_last_success_at"] = workerStatus.LastSuccessAt
	}
	if !workerStatus.LastErrorAt.IsZero() {
		artifactCleanupDetails["worker_last_error_at"] = workerStatus.LastErrorAt
	}
	if artifactIntentCountsErr != nil {
		artifactCleanupState = "unavailable"
		markUnavailable()
	} else {
		artifactCleanupDetails["pending"] = artifactIntentCounts.Pending
		artifactCleanupDetails["claimed"] = artifactIntentCounts.Claimed
		artifactCleanupDetails["quarantined"] = artifactIntentCounts.Quarantined
		artifactCleanupDetails["cleaned"] = artifactIntentCounts.Cleaned
		artifactCleanupDetails["published"] = artifactIntentCounts.Published
		artifactCleanupDetails["due"] = artifactIntentCounts.Due
		artifactCleanupDetails["dead_letter"] = artifactIntentCounts.DeadLetter
		artifactCleanupDetails["binding_mismatch_dead_letter"] = artifactIntentCounts.BindingMismatchDeadLetter
		artifactCleanupDetails["retrying"] = artifactIntentCounts.Retrying
		artifactCleanupDetails["retrying_binding_mismatch"] = artifactIntentCounts.RetryingBindingMismatch
		artifactCleanupDetails["cleaned_retrying"] = artifactIntentCounts.CleanedRetrying
		artifactCleanupDetails["cleaned_binding_mismatch"] = artifactIntentCounts.CleanedBindingMismatch
		artifactCleanupState = platformArtifactCleanupReadinessState(
			artifactIntentCounts,
			workerStatus,
			service.PlatformRelayProductionSecurityEnabled(),
		)
		switch artifactCleanupState {
		case "unavailable":
			markUnavailable()
		case "degraded":
			markDegraded()
		}
	}
	dependencies = append(dependencies, platformRelayDependencyHealth{
		Name:    "artifact_cleanup",
		State:   artifactCleanupState,
		Details: artifactCleanupDetails,
	})

	callbackState := "degraded"
	callbackDetails := map[string]any{"enabled": compatEnabled && workersEnabled}
	if compatEnabled && workersEnabled {
		counts, err := model.GetPlatformGenerationCallbackCounts()
		if err != nil {
			callbackState = "unavailable"
			markUnavailable()
		} else {
			callbackDetails["pending"] = counts.Pending
			callbackDetails["claimed"] = counts.Claimed
			callbackDetails["delivered"] = counts.Delivered
			callbackDetails["dead_letter"] = counts.DeadLetter
			if counts.Pending+counts.Claimed+counts.DeadLetter > 0 {
				callbackState = "degraded"
				markDegraded()
			} else {
				callbackState = "healthy"
			}
		}
	} else {
		markDegraded()
	}
	dependencies = append(dependencies, platformRelayDependencyHealth{
		Name:    "generation_callbacks",
		State:   callbackState,
		Details: callbackDetails,
	})

	providerState := "degraded"
	providerDetails := map[string]any{"enabled": false}
	if compatEnabled {
		ctx, cancel := context.WithTimeout(c.Request.Context(), 2*time.Second)
		summary, err := service.GetPlatformProviderReadinessSummary(ctx)
		cancel()
		if err != nil {
			providerState = "unavailable"
			markUnavailable()
		} else {
			providerDetails = map[string]any{
				"enabled":                            summary.Enabled,
				"monitor_fresh":                      summary.MonitorFresh,
				"monitor_last_completed_at":          summary.MonitorLastCompletedAt,
				"monitor_freshness_seconds":          summary.MonitorFreshnessSeconds,
				"monitor_last_error_code":            summary.MonitorLastWorkerErrorCode,
				"active_alerts":                      summary.ActiveAlerts,
				"unavailable_routes":                 summary.UnavailableRoutes,
				"alert_backlog":                      summary.AlertBacklog,
				"alert_dead_letter":                  summary.AlertDeadLetter,
				"cost_incomplete":                    summary.CostIncomplete,
				"cost_backlog":                       summary.CostBacklog,
				"cost_dead_letter":                   summary.CostDeadLetter,
				"cost_successful_relay_jobs":         summary.CostSuccessfulRelayJobs,
				"cost_explicit_relay_jobs":           summary.CostExplicitRelayJobs,
				"native_billing_reconciliation_jobs": summary.NativeBillingReconciliationJobs,
				"cost_reconciliation_complete":       summary.CostReconciliationComplete,
				"task_stage_backlog":                 summary.TaskStageBacklog,
				"task_stage_dead_letter":             summary.TaskStageDeadLetter,
				"operations_snapshot_backlog":        summary.OperationsSnapshotBacklog,
				"operations_snapshot_dead_letter":    summary.OperationsSnapshotDeadLetter,
			}
			costEvidenceUnavailable := service.PlatformRelayProductionSecurityEnabled() &&
				(summary.CostIncomplete > 0 || summary.NativeBillingReconciliationJobs > 0 ||
					summary.CostDeadLetter > 0 || !summary.CostReconciliationComplete)
			if costEvidenceUnavailable {
				// Production must never be admitted as ready while successful
				// provider work lacks an evidence-backed cost fact. Provider
				// outages remain degraded, but incomplete financial truth is a
				// cutover blocker.
				providerState = "unavailable"
				markUnavailable()
			} else if summary.Degraded {
				providerState = "degraded"
				markDegraded()
			} else {
				providerState = "healthy"
			}
		}
	} else {
		markDegraded()
	}
	dependencies = append(dependencies, platformRelayDependencyHealth{
		Name:    "provider_runtime",
		State:   providerState,
		Details: providerDetails,
	})

	c.JSON(status, platformRelayHealthResponse{State: state, Dependencies: dependencies})
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
