package controller

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/glebarez/sqlite"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

const platformGenerationTestApprovalSecret = "platform-approval-secret-with-at-least-32-bytes"

func platformGenerationTestApprovalSignature(
	tenantID string,
	jobID string,
	request dto.PlatformGenerationReconciliationRequest,
) string {
	var payload bytes.Buffer
	payload.WriteString("platform-generation-reconciliation-approval-v1\x00")
	for _, value := range []string{
		tenantID,
		jobID,
		request.OperationID,
		request.Outcome,
		request.UpstreamTaskID,
		strconv.FormatInt(request.ExpectedRouteID, 10),
		strconv.Itoa(request.ExpectedSubmissionAttempt),
		request.ExpectedReconciliationToken,
		request.VerificationReference,
		request.ApprovedBy,
		request.ApprovalReason,
		request.ApprovalKeyID,
	} {
		payload.WriteString(strconv.Itoa(len([]byte(value))))
		payload.WriteByte(':')
		payload.WriteString(value)
	}
	mac := hmac.New(sha256.New, []byte(platformGenerationTestApprovalSecret))
	_, _ = mac.Write(payload.Bytes())
	return fmt.Sprintf("hmac-sha256:%x", mac.Sum(nil))
}

func TestPlatformGenerationOperationsCredentialIsTenantScoped(t *testing.T) {
	tenantID := "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30"
	token := "tenant-scoped-operations-token-32-bytes"
	digest := sha256.Sum256([]byte(token))
	t.Setenv("RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON", fmt.Sprintf(
		`[{"tenant_id":%q,"token_sha256":"%x"}]`,
		tenantID,
		digest,
	))
	t.Setenv("RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON", fmt.Sprintf(
		`[{"tenant_id":%q,"key_id":"platform-approval-v1","secret":%q}]`,
		tenantID,
		platformGenerationTestApprovalSecret,
	))
	assert.True(t, service.AuthenticatePlatformGenerationOperationsCredential(token, tenantID))
	assert.False(t, service.AuthenticatePlatformGenerationOperationsCredential(token, "58775bb2-b6d2-4ad3-ab03-2f9d10854ba1"))
	assert.False(t, service.AuthenticatePlatformGenerationOperationsCredential("wrong-tenant-scoped-operations-token", tenantID))
}

func TestPlatformGenerationOperationsDiscoversOnlyFencedUnknownSubmissions(t *testing.T) {
	originalDB := model.DB
	originalDatabaseType := common.MainDatabaseType()
	database, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	require.NoError(t, err)
	model.DB = database
	common.SetMainDatabaseType(common.DatabaseTypeSQLite)
	t.Cleanup(func() {
		model.DB = originalDB
		common.SetMainDatabaseType(originalDatabaseType)
	})
	require.NoError(t, database.AutoMigrate(
		&model.PlatformGenerationJob{},
		&model.PlatformGenerationProviderAccountState{},
		&model.PlatformGenerationProviderRoute{},
		&model.PlatformGenerationRouteAdmission{},
	))
	require.NoError(t, model.MigratePlatformGenerationReconciliationStorage())

	tenantID := "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30"
	operationsToken := "tenant-scoped-operations-token-32-bytes"
	digest := sha256.Sum256([]byte(operationsToken))
	otherTenantID := "58775bb2-b6d2-4ad3-ab03-2f9d10854ba1"
	otherOperationsToken := "other-tenant-operations-token-32-bytes"
	otherDigest := sha256.Sum256([]byte(otherOperationsToken))
	t.Setenv("RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON", fmt.Sprintf(
		`[{"tenant_id":%q,"token_sha256":"%x"},{"tenant_id":%q,"token_sha256":"%x"}]`,
		tenantID,
		digest,
		otherTenantID,
		otherDigest,
	))
	t.Setenv("RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON", fmt.Sprintf(
		`[{"tenant_id":%q,"key_id":"platform-approval-v1","secret":%q},{"tenant_id":%q,"key_id":"platform-approval-v1","secret":%q}]`,
		tenantID,
		platformGenerationTestApprovalSecret,
		otherTenantID,
		"other-platform-approval-secret-with-at-least-32-bytes",
	))
	accountState := model.PlatformGenerationProviderAccountState{
		ChannelID: 7, KeyIndex: 0, KeyFingerprint: strings.Repeat("1", 64),
		RPMWindowSeconds: 60, RPMLimit: 10, ActiveCount: 1, ActiveLimit: 1,
	}
	require.NoError(t, database.Create(&accountState).Error)
	route := model.PlatformGenerationProviderRoute{
		RouteKey: "operations-route", Model: "operations-model", Mode: "text_to_video",
		ProviderName: "provider", AccountID: "account-one", ChannelID: 7, KeyIndex: 0,
		KeyFingerprint: strings.Repeat("1", 64), AccountStateID: accountState.ID, ChannelClass: "official",
		UpstreamModel: "video-v1", ProductionReady: true, Enabled: true,
		RPMWindowSeconds: 60, RPMLimit: 10, ActiveCount: 1, ActiveLimit: 1,
	}
	require.NoError(t, database.Create(&route).Error)
	job := model.PlatformGenerationJob{
		ID: uuid.NewString(), TenantID: tenantID, SourceClientID: "platform",
		RequestID: "operations-discovery", IdempotencyKey: "operations-discovery-key",
		RequestHash: strings.Repeat("2", 64), RequestJSON: `{}`,
		Model: route.Model, Mode: route.Mode,
		ExpectedCapabilityRevision: "sha256:" + strings.Repeat("3", 64),
		CapabilityRevision:         "sha256:" + strings.Repeat("3", 64),
		Status:                     model.PlatformGenerationStatusReconciliationRequired,
		ProviderRouteID:            route.ID,
		ProviderChannelID:          route.ChannelID,
		ProviderKeyIndex:           route.KeyIndex,
		ProviderSubmissionAttempt:  2,
		ErrorCode:                  model.PlatformGenerationErrorSubmissionReconciliationRequired,
		ErrorMessage:               "Provider response was lost",
	}
	require.NoError(t, database.Create(&job).Error)
	unknownAt := time.Now().UTC()
	admission := model.PlatformGenerationRouteAdmission{
		JobID: job.ID, RouteID: route.ID, SubmissionTokenHash: strings.Repeat("4", 64),
		State: model.PlatformGenerationRouteAdmissionUnknown, SlotHeld: true,
		Attempt: 2, UnknownAt: &unknownAt,
	}
	require.NoError(t, database.Create(&admission).Error)
	pollReconciliationJob := model.PlatformGenerationJob{
		ID: uuid.NewString(), TenantID: tenantID, SourceClientID: "platform",
		RequestID: "provider-poll-reconciliation", IdempotencyKey: "provider-poll-reconciliation-key",
		RequestHash: strings.Repeat("5", 64), RequestJSON: `{}`,
		Model: route.Model, Mode: route.Mode,
		ExpectedCapabilityRevision: "sha256:" + strings.Repeat("6", 64),
		CapabilityRevision:         "sha256:" + strings.Repeat("6", 64),
		Status:                     model.PlatformGenerationStatusReconciliationRequired,
		ProviderRouteID:            route.ID,
		ProviderChannelID:          route.ChannelID,
		ProviderKeyIndex:           route.KeyIndex,
		ProviderSubmissionAttempt:  3,
		ErrorCode:                  model.PlatformGenerationErrorProviderPollReconciliationRequired,
		ErrorMessage:               "Native provider task requires poll reconciliation",
	}
	require.NoError(t, database.Create(&pollReconciliationJob).Error)
	pollAdmission := model.PlatformGenerationRouteAdmission{
		JobID: pollReconciliationJob.ID, RouteID: route.ID, SubmissionTokenHash: strings.Repeat("7", 64),
		State: model.PlatformGenerationRouteAdmissionPosting, SlotHeld: true, Attempt: 3,
	}
	require.NoError(t, database.Create(&pollAdmission).Error)

	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(middleware.PlatformRelayRequestID())
	router.GET("/internal/platform-generation-operations/submission-unknown", ListPlatformGenerationSubmissionUnknown)
	router.GET("/internal/platform-generation-operations/:job_id/reconciliation", GetPlatformGenerationSubmissionUnknown)
	router.GET("/internal/platform-generation-operations/:job_id/reconciliation-result", GetPlatformGenerationSubmissionUnknownResult)
	router.POST("/internal/platform-generation-operations/:job_id/reconciliation", ResolvePlatformGenerationSubmissionUnknown)

	listRequest := httptest.NewRequest(
		http.MethodGet,
		"/internal/platform-generation-operations/submission-unknown?tenant_id="+tenantID,
		nil,
	)
	listRequest.Header.Set("X-Relay-Operations-Token", operationsToken)
	listResponse := httptest.NewRecorder()
	router.ServeHTTP(listResponse, listRequest)
	require.Equal(t, http.StatusOK, listResponse.Code)
	var page dto.PlatformGenerationReconciliationPage
	require.NoError(t, common.Unmarshal(listResponse.Body.Bytes(), &page))
	require.Len(t, page.Data, 1)
	assert.EqualValues(t, 1, page.Total)
	assert.Equal(t, job.ID, page.Data[0].JobID)
	assert.Equal(t, route.ID, page.Data[0].ProviderRouteID)
	assert.Equal(t, 2, page.Data[0].ProviderSubmissionAttempt)
	assert.Regexp(t, `^sha256:[0-9a-f]{64}$`, page.Data[0].ReconciliationToken)

	pollDetailRequest := httptest.NewRequest(
		http.MethodGet,
		"/internal/platform-generation-operations/"+pollReconciliationJob.ID+"/reconciliation?tenant_id="+tenantID,
		nil,
	)
	pollDetailRequest.Header.Set("X-Relay-Operations-Token", operationsToken)
	pollDetailResponse := httptest.NewRecorder()
	router.ServeHTTP(pollDetailResponse, pollDetailRequest)
	assert.Equal(t, http.StatusNotFound, pollDetailResponse.Code)

	wrongTenantRequest := httptest.NewRequest(
		http.MethodGet,
		"/internal/platform-generation-operations/submission-unknown?tenant_id=58775bb2-b6d2-4ad3-ab03-2f9d10854ba1",
		nil,
	)
	wrongTenantRequest.Header.Set("X-Relay-Operations-Token", operationsToken)
	wrongTenantResponse := httptest.NewRecorder()
	router.ServeHTTP(wrongTenantResponse, wrongTenantRequest)
	assert.Equal(t, http.StatusUnauthorized, wrongTenantResponse.Code)

	legacyResolveRequest := httptest.NewRequest(
		http.MethodPost,
		"/internal/platform-generation-operations/"+job.ID+"/reconciliation",
		strings.NewReader(fmt.Sprintf(
			`{"tenant_id":%q,"outcome":"not_created","upstream_task_id":"","expected_route_id":%d,"expected_submission_attempt":2}`,
			tenantID,
			route.ID,
		)),
	)
	legacyResolveRequest.Header.Set("X-Relay-Operations-Token", operationsToken)
	legacyResolveResponse := httptest.NewRecorder()
	router.ServeHTTP(legacyResolveResponse, legacyResolveRequest)
	assert.Equal(t, http.StatusUnprocessableEntity, legacyResolveResponse.Code)

	reconciliationRequest := dto.PlatformGenerationReconciliationRequest{
		OperationID:                 "reconcile-operation-0001",
		TenantID:                    tenantID,
		Outcome:                     "not_created",
		ExpectedRouteID:             route.ID,
		ExpectedSubmissionAttempt:   2,
		ExpectedReconciliationToken: page.Data[0].ReconciliationToken,
		VerificationReference:       "provider-console-case-42",
		ApprovedBy:                  "platform-admin-1",
		ApprovalReason:              "Provider console proves absence",
		ApprovalKeyID:               "platform-approval-v1",
	}
	reconciliationRequest.ApprovalSignature = platformGenerationTestApprovalSignature(
		tenantID,
		job.ID,
		reconciliationRequest,
	)
	resolveBytes, err := common.Marshal(reconciliationRequest)
	require.NoError(t, err)
	resolveBody := string(resolveBytes)
	missingRequestIDRequest := httptest.NewRequest(
		http.MethodPost,
		"/internal/platform-generation-operations/"+job.ID+"/reconciliation",
		strings.NewReader(resolveBody),
	)
	missingRequestIDRequest.Header.Set("X-Relay-Operations-Token", operationsToken)
	missingRequestIDResponse := httptest.NewRecorder()
	router.ServeHTTP(missingRequestIDResponse, missingRequestIDRequest)
	assert.Equal(t, http.StatusUnprocessableEntity, missingRequestIDResponse.Code)

	forgedApproval := reconciliationRequest
	forgedApproval.ApprovalSignature = "hmac-sha256:" + strings.Repeat("0", 64)
	forgedBytes, err := common.Marshal(forgedApproval)
	require.NoError(t, err)
	forgedRequest := httptest.NewRequest(
		http.MethodPost,
		"/internal/platform-generation-operations/"+job.ID+"/reconciliation",
		bytes.NewReader(forgedBytes),
	)
	forgedRequest.Header.Set("X-Relay-Operations-Token", operationsToken)
	forgedRequest.Header.Set("X-Request-ID", "operations-forged-approval")
	forgedResponse := httptest.NewRecorder()
	router.ServeHTTP(forgedResponse, forgedRequest)
	assert.Equal(t, http.StatusForbidden, forgedResponse.Code)

	resolveRequest := httptest.NewRequest(
		http.MethodPost,
		"/internal/platform-generation-operations/"+job.ID+"/reconciliation",
		strings.NewReader(resolveBody),
	)
	resolveRequest.Header.Set("X-Relay-Operations-Token", operationsToken)
	resolveRequest.Header.Set("X-Request-ID", "operations-resolve-request-1")
	resolveResponse := httptest.NewRecorder()
	router.ServeHTTP(resolveResponse, resolveRequest)
	require.Equal(t, http.StatusOK, resolveResponse.Code, resolveResponse.Body.String())
	var resolved dto.PlatformGenerationSnapshot
	require.NoError(t, common.Unmarshal(resolveResponse.Body.Bytes(), &resolved))
	assert.Equal(t, model.PlatformGenerationStatusFailed, resolved.Status)
	require.NotNil(t, resolved.Error)
	assert.Equal(t, model.PlatformGenerationErrorSubmissionConfirmedNotCreated, resolved.Error.Code)
	assert.Equal(t, "false", resolveResponse.Header().Get("X-Idempotent-Replay"))
	eventID := resolveResponse.Header().Get("X-Reconciliation-Event-ID")
	assert.NotEmpty(t, eventID)

	var event model.PlatformGenerationReconciliationEvent
	require.NoError(t, database.First(&event, "id = ?", eventID).Error)
	assert.Equal(t, tenantID, event.TenantID)
	assert.Equal(t, job.ID, event.JobID)
	assert.Equal(t, "reconcile-operation-0001", event.OperationID)
	assert.Equal(t, "operations-resolve-request-1", event.RequestID)
	assert.Equal(t, "provider-console-case-42", event.VerificationReference)
	assert.Equal(t, "platform-admin-1", event.ApprovedBy)
	assert.Equal(t, "Provider console proves absence", event.ApprovalReason)
	assert.Equal(t, "platform-approval-v1", event.ApprovalKeyID)
	assert.Equal(t, reconciliationRequest.ApprovalSignature, event.ApprovalSignature)
	assert.Regexp(t, `^[0-9a-f]{64}$`, event.PayloadSHA256)

	replayRequest := httptest.NewRequest(
		http.MethodPost,
		"/internal/platform-generation-operations/"+job.ID+"/reconciliation",
		strings.NewReader(resolveBody),
	)
	replayRequest.Header.Set("X-Relay-Operations-Token", operationsToken)
	replayRequest.Header.Set("X-Request-ID", "operations-resolve-request-2")
	replayResponse := httptest.NewRecorder()
	router.ServeHTTP(replayResponse, replayRequest)
	require.Equal(t, http.StatusOK, replayResponse.Code, replayResponse.Body.String())
	assert.Equal(t, "true", replayResponse.Header().Get("X-Idempotent-Replay"))
	assert.Equal(t, eventID, replayResponse.Header().Get("X-Reconciliation-Event-ID"))
	var eventCount int64
	require.NoError(t, database.Model(&model.PlatformGenerationReconciliationEvent{}).Count(&eventCount).Error)
	assert.EqualValues(t, 1, eventCount)

	conflictingApproval := reconciliationRequest
	conflictingApproval.ApprovalReason = "Provider console evidence changed"
	conflictingApproval.ApprovalSignature = platformGenerationTestApprovalSignature(
		tenantID,
		job.ID,
		conflictingApproval,
	)
	conflictingBytes, err := common.Marshal(conflictingApproval)
	require.NoError(t, err)
	conflictingRequest := httptest.NewRequest(
		http.MethodPost,
		"/internal/platform-generation-operations/"+job.ID+"/reconciliation",
		bytes.NewReader(conflictingBytes),
	)
	conflictingRequest.Header.Set("X-Relay-Operations-Token", operationsToken)
	conflictingRequest.Header.Set("X-Request-ID", "operations-resolve-request-3")
	conflictingResponse := httptest.NewRecorder()
	router.ServeHTTP(conflictingResponse, conflictingRequest)
	assert.Equal(t, http.StatusConflict, conflictingResponse.Code)

	resultRequest := httptest.NewRequest(
		http.MethodGet,
		"/internal/platform-generation-operations/"+job.ID+"/reconciliation-result?tenant_id="+tenantID+"&operation_id=reconcile-operation-0001",
		nil,
	)
	resultRequest.Header.Set("X-Relay-Operations-Token", operationsToken)
	resultResponse := httptest.NewRecorder()
	router.ServeHTTP(resultResponse, resultRequest)
	require.Equal(t, http.StatusOK, resultResponse.Code, resultResponse.Body.String())
	var result dto.PlatformGenerationReconciliationResult
	require.NoError(t, common.Unmarshal(resultResponse.Body.Bytes(), &result))
	assert.Equal(t, "generation.reconciliation_result", result.Object)
	assert.Equal(t, eventID, result.EventID)
	assert.Equal(t, "reconcile-operation-0001", result.OperationID)
	assert.Equal(t, "operations-resolve-request-1", result.RequestID)
	assert.Equal(t, model.PlatformGenerationStatusFailed, result.ResolvedStatus)
	assert.Equal(t, model.PlatformGenerationStatusFailed, result.CurrentStatus)
	assert.Equal(t, "provider-console-case-42", result.VerificationReference)
	assert.Equal(t, "platform-admin-1", result.ApprovedBy)
	assert.Equal(t, "Provider console proves absence", result.ApprovalReason)
	assert.Equal(t, "platform-approval-v1", result.ApprovalKeyID)
	assert.Equal(t, reconciliationRequest.ApprovalSignature, result.ApprovalSignature)

	wrongResultRequest := httptest.NewRequest(
		http.MethodGet,
		"/internal/platform-generation-operations/"+job.ID+"/reconciliation-result?tenant_id=58775bb2-b6d2-4ad3-ab03-2f9d10854ba1&operation_id=reconcile-operation-0001",
		nil,
	)
	wrongResultRequest.Header.Set("X-Relay-Operations-Token", operationsToken)
	wrongResultResponse := httptest.NewRecorder()
	router.ServeHTTP(wrongResultResponse, wrongResultRequest)
	assert.Equal(t, http.StatusUnauthorized, wrongResultResponse.Code)
	authorizedOtherTenantRequest := httptest.NewRequest(
		http.MethodGet,
		"/internal/platform-generation-operations/"+job.ID+"/reconciliation-result?tenant_id="+otherTenantID+"&operation_id=reconcile-operation-0001",
		nil,
	)
	authorizedOtherTenantRequest.Header.Set("X-Relay-Operations-Token", otherOperationsToken)
	authorizedOtherTenantResponse := httptest.NewRecorder()
	router.ServeHTTP(authorizedOtherTenantResponse, authorizedOtherTenantRequest)
	assert.Equal(t, http.StatusNotFound, authorizedOtherTenantResponse.Code)
	assert.NotContains(t, authorizedOtherTenantResponse.Body.String(), eventID)
	assert.Error(t, database.Exec(
		"UPDATE platform_generation_reconciliation_events SET approval_reason = ? WHERE id = ?",
		"tampered",
		eventID,
	).Error)
	assert.Error(t, database.Exec(
		"DELETE FROM platform_generation_reconciliation_events WHERE id = ?",
		eventID,
	).Error)

	var persistedAdmission model.PlatformGenerationRouteAdmission
	require.NoError(t, database.First(&persistedAdmission, "job_id = ?", job.ID).Error)
	assert.Equal(t, model.PlatformGenerationRouteAdmissionReleased, persistedAdmission.State)
	assert.False(t, persistedAdmission.SlotHeld)
	var persistedState model.PlatformGenerationProviderAccountState
	require.NoError(t, database.First(&persistedState, accountState.ID).Error)
	assert.Zero(t, persistedState.ActiveCount)
}

func TestDecodePlatformGenerationRequestAppliesContractDefaults(t *testing.T) {
	revision := "sha256:" + strings.Repeat("0", 64)
	body := fmt.Sprintf(`{
		"model":"video-model",
		"expected_capability_revision":%q,
		"mode":"text_to_video",
		"inputs":{"prompt":"hello"}
	}`, revision)

	request, err := decodePlatformGenerationRequest(strings.NewReader(body))
	require.NoError(t, err)
	assert.Equal(t, 5, request.Output.DurationSeconds)
	assert.Equal(t, "16:9", request.Output.AspectRatio)
	assert.Equal(t, "720p", request.Output.Resolution)
	assert.Equal(t, 1, request.Output.Count)
	assert.False(t, request.Output.FaceEnabled)
	assert.Empty(t, request.Inputs.Assets)
	assert.NotNil(t, request.Inputs.Assets)
	assert.Empty(t, request.Metadata)
	assert.NotNil(t, request.Metadata)
}

func TestDecodePlatformGenerationRequestPreservesNullableClientReferenceID(t *testing.T) {
	revision := "sha256:" + strings.Repeat("0", 64)
	tests := []struct {
		name      string
		field     string
		expected  string
		expectNil bool
	}{
		{name: "missing", expectNil: true},
		{name: "explicit null", field: `"client_reference_id":null,`, expectNil: true},
		{name: "empty string", field: `"client_reference_id":"",`, expected: ""},
		{name: "non-empty string", field: `"client_reference_id":"platform-task-1",`, expected: "platform-task-1"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			body := fmt.Sprintf(`{%s"model":"video-model","expected_capability_revision":%q,"mode":"text_to_video","inputs":{"prompt":"hello"}}`, test.field, revision)
			request, err := decodePlatformGenerationRequest(strings.NewReader(body))
			require.NoError(t, err)
			if test.expectNil {
				assert.Nil(t, request.ClientReferenceID)
				return
			}
			require.NotNil(t, request.ClientReferenceID)
			assert.Equal(t, test.expected, *request.ClientReferenceID)
		})
	}

	invalidBody := fmt.Sprintf(`{"client_reference_id":42,"model":"video-model","expected_capability_revision":%q,"mode":"text_to_video","inputs":{"prompt":"hello"}}`, revision)
	_, err := decodePlatformGenerationRequest(strings.NewReader(invalidBody))
	require.Error(t, err)
	var validationError platformGenerationValidationError
	require.ErrorAs(t, err, &validationError)
	assert.Equal(t, []string{"body", "client_reference_id"}, validationError.Issue.Location)
}

func TestDecodePlatformGenerationRequestRejectsUnknownFieldsAtEveryClosedLevel(t *testing.T) {
	revision := "sha256:" + strings.Repeat("a", 64)
	tests := []struct {
		name string
		body string
	}{
		{
			name: "root",
			body: fmt.Sprintf(`{"model":"m","expected_capability_revision":%q,"mode":"text_to_video","inputs":{"prompt":"p"},"tenant_id":"leak"}`, revision),
		},
		{
			name: "inputs",
			body: fmt.Sprintf(`{"model":"m","expected_capability_revision":%q,"mode":"text_to_video","inputs":{"prompt":"p","unknown":true}}`, revision),
		},
		{
			name: "asset",
			body: fmt.Sprintf(`{"model":"m","expected_capability_revision":%q,"mode":"image_to_video","inputs":{"prompt":"p","assets":[{"url":"https://example.test/input.png","media_type":"image","secret":"x"}]}}`, revision),
		},
		{
			name: "output",
			body: fmt.Sprintf(`{"model":"m","expected_capability_revision":%q,"mode":"text_to_video","inputs":{"prompt":"p"},"output":{"count":1,"unknown":true}}`, revision),
		},
		{
			name: "callback",
			body: fmt.Sprintf(`{"model":"m","expected_capability_revision":%q,"mode":"text_to_video","inputs":{"prompt":"p"},"callback":{"url":"https://example.test/callback","secret":"x"}}`, revision),
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := decodePlatformGenerationRequest(strings.NewReader(test.body))
			require.Error(t, err)
			var validationError platformGenerationValidationError
			require.ErrorAs(t, err, &validationError)
			assert.Equal(t, "extra_forbidden", validationError.Issue.Type)
		})
	}
}

func TestDecodePlatformGenerationRequestUsesCharacterLengthForPrompt(t *testing.T) {
	request := dto.NewPlatformGenerationRequest()
	request.Model = "video-model"
	request.ExpectedCapabilityRevision = "sha256:" + strings.Repeat("b", 64)
	request.Mode = "text_to_video"
	request.Inputs.Prompt = strings.Repeat("界", 10_000)
	body, err := common.Marshal(request)
	require.NoError(t, err)

	_, err = decodePlatformGenerationRequest(strings.NewReader(string(body)))
	require.NoError(t, err)

	request.Inputs.Prompt += "界"
	body, err = common.Marshal(request)
	require.NoError(t, err)
	_, err = decodePlatformGenerationRequest(strings.NewReader(string(body)))
	require.Error(t, err)
}

func TestDecodePlatformGenerationRequestRequiresHTTPSInProduction(t *testing.T) {
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "production")
	revision := "sha256:" + strings.Repeat("c", 64)
	body := fmt.Sprintf(`{
		"model":"video-model",
		"expected_capability_revision":%q,
		"mode":"image_to_video",
		"inputs":{"prompt":"hello","assets":[{"url":"http://127.0.0.1/input.png","media_type":"image"}]}
	}`, revision)

	_, err := decodePlatformGenerationRequest(strings.NewReader(body))
	require.Error(t, err)
	assert.Contains(t, err.Error(), "HTTPS")
}

func TestPlatformGenerationHTTPContractIdempotencyAndTenantIsolation(t *testing.T) {
	originalWorkerState := getPlatformGenerationWorkerRuntimeState
	getPlatformGenerationWorkerRuntimeState = func() service.PlatformGenerationWorkerRuntimeState {
		return service.PlatformGenerationWorkerStateRunning
	}
	t.Cleanup(func() { getPlatformGenerationWorkerRuntimeState = originalWorkerState })

	originalDB := model.DB
	originalDatabaseType := common.MainDatabaseType()
	database, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	require.NoError(t, err)
	model.DB = database
	common.SetMainDatabaseType(common.DatabaseTypeSQLite)
	t.Cleanup(func() {
		model.DB = originalDB
		common.SetMainDatabaseType(originalDatabaseType)
	})
	require.NoError(t, database.AutoMigrate(&model.PlatformGenerationJob{}, &model.PlatformGenerationOutbox{}))

	t.Setenv("RELAY_COMPAT_ENABLED", "true")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", `{}`)
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", "")
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", `{
		"platform": {
			"tenant_id": "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30",
			"api_key": "relay-secret",
			"upstream_token": "upstream-token"
		},
		"other-platform": {
			"tenant_id": "58775bb2-b6d2-4ad3-ab03-2f9d10854ba1",
			"api_key": "other-secret",
			"upstream_token": "other-upstream-token"
		}
	}`)

	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(middleware.PlatformGenerationServiceAuth())
	router.POST("/v1/generations", SubmitPlatformGeneration)
	router.GET("/v1/generations/:job_id", GetPlatformGeneration)

	revision := "sha256:" + strings.Repeat("d", 64)
	body := fmt.Sprintf(`{
		"client_reference_id":"platform-task-1",
		"model":"video-model",
		"expected_capability_revision":%q,
		"mode":"text_to_video",
		"inputs":{"prompt":"hello"},
		"metadata":{"safe":"value","relay_request_id":"forged","relay_capability_revision":"forged"}
	}`, revision)

	createdResponse := performPlatformGenerationRequest(router, http.MethodPost, "/v1/generations", body, "platform", "relay-secret", "generation-key-1", "request-1")
	require.Equal(t, http.StatusAccepted, createdResponse.Code)
	var created dto.PlatformGenerationAccepted
	require.NoError(t, common.Unmarshal(createdResponse.Body.Bytes(), &created))
	assert.False(t, created.IdempotentReplay)
	assert.Equal(t, "queued", created.Status)
	assert.Equal(t, created.ID, created.JobID)
	assert.Equal(t, "request-1", createdResponse.Header().Get("X-Request-ID"))

	replayResponse := performPlatformGenerationRequest(router, http.MethodPost, "/v1/generations", body, "platform", "relay-secret", "generation-key-1", "request-2")
	require.Equal(t, http.StatusAccepted, replayResponse.Code)
	var replay dto.PlatformGenerationAccepted
	require.NoError(t, common.Unmarshal(replayResponse.Body.Bytes(), &replay))
	assert.True(t, replay.IdempotentReplay)
	assert.Equal(t, created.ID, replay.ID)

	changedBody := strings.Replace(body, `"prompt":"hello"`, `"prompt":"changed"`, 1)
	conflictResponse := performPlatformGenerationRequest(router, http.MethodPost, "/v1/generations", changedBody, "platform", "relay-secret", "generation-key-1", "request-3")
	require.Equal(t, http.StatusConflict, conflictResponse.Code)
	var conflict dto.PlatformGenerationErrorEnvelope
	require.NoError(t, common.Unmarshal(conflictResponse.Body.Bytes(), &conflict))
	assert.Equal(t, "IDEMPOTENCY_KEY_REUSED", conflict.Error.Code)

	readResponse := performPlatformGenerationRequest(router, http.MethodGet, "/v1/generations/"+created.ID, "", "platform", "relay-secret", "", "request-4")
	require.Equal(t, http.StatusOK, readResponse.Code)
	var snapshot dto.PlatformGenerationSnapshot
	require.NoError(t, common.Unmarshal(readResponse.Body.Bytes(), &snapshot))
	assert.Equal(t, "value", snapshot.Metadata["safe"])
	assert.Equal(t, "request-1", snapshot.Metadata["relay_request_id"])
	assert.NotContains(t, snapshot.Metadata, "relay_capability_revision")

	crossTenantResponse := performPlatformGenerationRequest(router, http.MethodGet, "/v1/generations/"+created.ID, "", "other-platform", "other-secret", "", "request-5")
	require.Equal(t, http.StatusNotFound, crossTenantResponse.Code)
	var notFound dto.PlatformGenerationErrorEnvelope
	require.NoError(t, common.Unmarshal(crossTenantResponse.Body.Bytes(), &notFound))
	assert.Equal(t, "JOB_NOT_FOUND", notFound.Error.Code)

	withoutReference := fmt.Sprintf(`{
		"model":"video-model",
		"expected_capability_revision":%q,
		"mode":"text_to_video",
		"inputs":{"prompt":"nullable reference"}
	}`, revision)
	withoutReferenceResponse := performPlatformGenerationRequest(router, http.MethodPost, "/v1/generations", withoutReference, "platform", "relay-secret", "generation-key-2", "request-6")
	require.Equal(t, http.StatusAccepted, withoutReferenceResponse.Code)
	var withoutReferenceAccepted dto.PlatformGenerationAccepted
	require.NoError(t, common.Unmarshal(withoutReferenceResponse.Body.Bytes(), &withoutReferenceAccepted))

	explicitNull := strings.Replace(withoutReference, "{", `{"client_reference_id":null,`, 1)
	nullReplayResponse := performPlatformGenerationRequest(router, http.MethodPost, "/v1/generations", explicitNull, "platform", "relay-secret", "generation-key-2", "request-7")
	require.Equal(t, http.StatusAccepted, nullReplayResponse.Code)
	var nullReplay dto.PlatformGenerationAccepted
	require.NoError(t, common.Unmarshal(nullReplayResponse.Body.Bytes(), &nullReplay))
	assert.True(t, nullReplay.IdempotentReplay)
	assert.Equal(t, withoutReferenceAccepted.ID, nullReplay.ID, "missing and explicit null must have one canonical request identity")

	nullSnapshotResponse := performPlatformGenerationRequest(router, http.MethodGet, "/v1/generations/"+withoutReferenceAccepted.ID, "", "platform", "relay-secret", "", "request-8")
	require.Equal(t, http.StatusOK, nullSnapshotResponse.Code)
	var nullSnapshot dto.PlatformGenerationSnapshot
	require.NoError(t, common.Unmarshal(nullSnapshotResponse.Body.Bytes(), &nullSnapshot))
	assert.Nil(t, nullSnapshot.ClientReferenceID)
	assert.Contains(t, nullSnapshotResponse.Body.String(), `"client_reference_id":null`)

	emptyReference := strings.Replace(withoutReference, "{", `{"client_reference_id":"",`, 1)
	emptyConflictResponse := performPlatformGenerationRequest(router, http.MethodPost, "/v1/generations", emptyReference, "platform", "relay-secret", "generation-key-2", "request-9")
	require.Equal(t, http.StatusConflict, emptyConflictResponse.Code, "an explicit empty string remains distinct from null")
}

func TestPlatformGenerationPublicSubmitRejectsEveryNonRunningWorkerStateWithoutWrites(t *testing.T) {
	originalWorkerState := getPlatformGenerationWorkerRuntimeState
	t.Cleanup(func() { getPlatformGenerationWorkerRuntimeState = originalWorkerState })

	originalDB := model.DB
	originalDatabaseType := common.MainDatabaseType()
	database, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	require.NoError(t, err)
	model.DB = database
	common.SetMainDatabaseType(common.DatabaseTypeSQLite)
	t.Cleanup(func() {
		model.DB = originalDB
		common.SetMainDatabaseType(originalDatabaseType)
	})
	require.NoError(t, database.AutoMigrate(&model.PlatformGenerationJob{}, &model.PlatformGenerationOutbox{}))

	t.Setenv("RELAY_COMPAT_ENABLED", "true")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", `{}`)
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", "")
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", `{
		"platform": {
			"tenant_id": "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30",
			"api_key": "relay-secret",
			"upstream_token": "upstream-token"
		}
	}`)

	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(middleware.PlatformGenerationServiceAuth())
	router.POST("/v1/generations", SubmitPlatformGeneration)
	router.GET("/v1/generations/:job_id", GetPlatformGeneration)

	states := []service.PlatformGenerationWorkerRuntimeState{
		service.PlatformGenerationWorkerStateStopped,
		service.PlatformGenerationWorkerStateStarting,
		service.PlatformGenerationWorkerStateDraining,
		service.PlatformGenerationWorkerStateFailed,
	}
	for _, state := range states {
		state := state
		t.Run(string(state), func(t *testing.T) {
			getPlatformGenerationWorkerRuntimeState = func() service.PlatformGenerationWorkerRuntimeState {
				return state
			}
			response := performPlatformGenerationRequest(
				router,
				http.MethodPost,
				"/v1/generations",
				`{}`,
				"platform",
				"relay-secret",
				"worker-state-key-"+string(state),
				"worker-state-request-"+string(state),
			)
			require.Equal(t, http.StatusServiceUnavailable, response.Code)
			var envelope dto.PlatformGenerationErrorEnvelope
			require.NoError(t, common.Unmarshal(response.Body.Bytes(), &envelope))
			assert.Equal(t, model.PlatformGenerationErrorGenerationChannelUnavailable, envelope.Error.Code)
			assert.True(t, envelope.Error.Retryable)
			assert.Equal(t, string(state), envelope.Error.Details["worker_state"])

			var jobCount int64
			var outboxCount int64
			require.NoError(t, database.Model(&model.PlatformGenerationJob{}).Count(&jobCount).Error)
			require.NoError(t, database.Model(&model.PlatformGenerationOutbox{}).Count(&outboxCount).Error)
			assert.Zero(t, jobCount)
			assert.Zero(t, outboxCount)
		})
	}

	getPlatformGenerationWorkerRuntimeState = func() service.PlatformGenerationWorkerRuntimeState {
		return service.PlatformGenerationWorkerStateDraining
	}
	readResponse := performPlatformGenerationRequest(
		router,
		http.MethodGet,
		"/v1/generations/"+uuid.NewString(),
		"",
		"platform",
		"relay-secret",
		"",
		"draining-read-request",
	)
	assert.Equal(t, http.StatusNotFound, readResponse.Code, "GET polling must remain available while workers drain")
}

func TestPlatformRelayReadyReportsStartingWorkerRuntimeAsUnavailable(t *testing.T) {
	originalWorkerState := getPlatformGenerationWorkerRuntimeState
	getPlatformGenerationWorkerRuntimeState = func() service.PlatformGenerationWorkerRuntimeState {
		return service.PlatformGenerationWorkerStateStarting
	}
	t.Cleanup(func() { getPlatformGenerationWorkerRuntimeState = originalWorkerState })

	originalDB := model.DB
	originalDatabaseType := common.MainDatabaseType()
	database, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	require.NoError(t, err)
	model.DB = database
	common.SetMainDatabaseType(common.DatabaseTypeSQLite)
	t.Cleanup(func() {
		model.DB = originalDB
		common.SetMainDatabaseType(originalDatabaseType)
	})
	require.NoError(t, database.AutoMigrate(&model.PlatformArtifactUploadIntent{}))

	t.Setenv("APP_ENV", "development")
	t.Setenv("DEPLOYMENT_ENV", "development")
	t.Setenv("RELAY_COMPAT_ENABLED", "true")
	t.Setenv("RELAY_COMPAT_WORKER_ENABLED", "true")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", `{}`)
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", "")
	t.Setenv("RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN", "internal-token")

	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.GET("/health/ready", PlatformRelayReady)
	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/health/ready", nil))
	require.Equal(t, http.StatusServiceUnavailable, response.Code)

	var health platformRelayHealthResponse
	require.NoError(t, common.Unmarshal(response.Body.Bytes(), &health))
	var compat *platformRelayDependencyHealth
	for index := range health.Dependencies {
		if health.Dependencies[index].Name == "platform_relay_compat" {
			compat = &health.Dependencies[index]
			break
		}
	}
	require.NotNil(t, compat)
	assert.Equal(t, "unavailable", compat.State)
	assert.Equal(t, string(service.PlatformGenerationWorkerStateStarting), compat.Details["worker_runtime_state"])
	assert.Equal(t, false, compat.Details["worker_runtime_running"])
}

func performPlatformGenerationRequest(
	router http.Handler,
	method string,
	path string,
	body string,
	clientID string,
	apiKey string,
	idempotencyKey string,
	requestID string,
) *httptest.ResponseRecorder {
	request := httptest.NewRequest(method, path, strings.NewReader(body))
	request.Header.Set("X-Client-ID", clientID)
	request.Header.Set("X-API-Key", apiKey)
	request.Header.Set("X-Request-ID", requestID)
	if body != "" {
		request.Header.Set("Content-Type", "application/json")
	}
	if idempotencyKey != "" {
		request.Header.Set("Idempotency-Key", idempotencyKey)
	}
	response := httptest.NewRecorder()
	router.ServeHTTP(response, request)
	return response
}
