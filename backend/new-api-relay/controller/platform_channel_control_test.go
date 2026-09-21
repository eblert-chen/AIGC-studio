package controller

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
	"github.com/glebarez/sqlite"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

const (
	platformChannelControlTestTenant = "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30"
	platformChannelControlTestToken  = "channel-control-operations-token-at-least-32-bytes"
)

func setupPlatformChannelControlControllerTest(t *testing.T) (*gin.Engine, model.Channel) {
	return setupPlatformChannelControlControllerTestWithV5Seed(t, nil)
}

func setupPlatformChannelControlControllerTestWithV5Seed(
	t *testing.T,
	seed func(database *gorm.DB, channel model.Channel),
) (*gin.Engine, model.Channel) {
	t.Helper()
	originalDB := model.DB
	originalDatabaseType := common.MainDatabaseType()
	dsn := "file:platform-channel-control-controller-" + uuid.NewString() + "?mode=memory&cache=shared&_pragma=busy_timeout(5000)"
	database, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{})
	require.NoError(t, err)
	model.DB = database
	common.SetMainDatabaseType(common.DatabaseTypeSQLite)
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE", "")
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON", `{"schema_version":1,"active_key_id":"controller-control-v1","keys":{"controller-control-v1":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}}`)
	t.Cleanup(func() {
		model.DB = originalDB
		common.SetMainDatabaseType(originalDatabaseType)
	})
	require.NoError(t, database.AutoMigrate(&model.Channel{}, &model.ProviderChannelCredentialSetVersion{}))
	require.NoError(t, model.MigrateProviderChannelCredentialVaultStorage())
	require.NoError(t, model.MigratePlatformChannelControlStorageV5WithDB(database))
	require.NoError(t, database.AutoMigrate(&model.Ability{}, &model.User{}))

	digest := fmt.Sprintf("%x", sha256.Sum256([]byte(platformChannelControlTestToken)))
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("ENVIRONMENT", "development")
	t.Setenv("RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON", fmt.Sprintf(`[{"tenant_id":%q,"token_sha256":%q}]`, platformChannelControlTestTenant, digest))
	t.Setenv("RELAY_PLATFORM_CONTROL_TENANT_ID", platformChannelControlTestTenant)

	secretKey := "SECRET_KEY_CANARY_8fb6f4\nSECRET_KEY_CANARY_73cc01"
	baseURL := "https://SECRET_BASE_URL_CANARY.invalid"
	organization := "SECRET_ORG_CANARY"
	setting := `{"credential":"SECRET_SETTING_CANARY"}`
	paramOverride := `{"secret":"SECRET_PARAM_CANARY"}`
	headerOverride := `{"Authorization":"SECRET_HEADER_CANARY"}`
	remark := "safe operator remark"
	autoBan := 1
	weight := uint(10)
	priority := int64(20)
	testModel := "provider-video-model"
	tag := "video-production"
	channel := model.Channel{
		Type:               constant.ChannelTypeKling,
		Key:                secretKey,
		OpenAIOrganization: &organization,
		TestModel:          &testModel,
		Status:             common.ChannelStatusEnabled,
		Name:               "Kling primary",
		Weight:             &weight,
		CreatedTime:        1_786_700_000,
		BaseURL:            &baseURL,
		Other:              `{"token":"SECRET_OTHER_CANARY"}`,
		Models:             "provider-video-model,provider-image-model",
		ModelMapping:       stringPointer(`{"alias":"SECRET_MAPPING_CANARY"}`),
		Priority:           &priority,
		AutoBan:            &autoBan,
		OtherInfo:          `{"proxy":"SECRET_PROXY_CANARY"}`,
		Tag:                &tag,
		Setting:            &setting,
		ParamOverride:      &paramOverride,
		HeaderOverride:     &headerOverride,
		Remark:             &remark,
		OtherSettings:      `{"azure_key":"SECRET_SETTINGS_CANARY"}`,
		ChannelInfo: model.ChannelInfo{
			IsMultiKey:             true,
			MultiKeySize:           2,
			MultiKeyDisabledReason: map[int]string{1: "SECRET_DISABLED_REASON_CANARY"},
		},
	}
	require.NoError(t, database.Create(&channel).Error)
	require.NoError(t, database.Create(&model.User{Username: "channel-control-root", Role: common.RoleRootUser, Status: common.UserStatusEnabled}).Error)
	if seed != nil {
		seed(database, channel)
	}
	require.NoError(t, model.MigratePlatformChannelControlStorageV6WithDB(database))

	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(middleware.PlatformRelayRequestID())
	router.GET("/channels", ListPlatformChannelControlChannels)
	router.GET("/channels/:channel_id", GetPlatformChannelControlChannel)
	router.POST("/channels/:channel_id/test", TestPlatformChannelControlChannel)
	router.POST("/channels/:channel_id/status", UpdatePlatformChannelControlStatus)
	router.GET("/channels/:channel_id/operations/:operation_id", GetPlatformChannelControlOperation)
	router.POST("/channels/:channel_id/operations/:operation_id/reconcile-no-creation", ReconcilePlatformChannelControlTestNoCreation)
	return router, channel
}

func stringPointer(value string) *string { return &value }

func performPlatformChannelControlRequest(router http.Handler, method, target, body, requestID string) *httptest.ResponseRecorder {
	request := httptest.NewRequest(method, target, strings.NewReader(body))
	request.Header.Set("X-Relay-Operations-Token", platformChannelControlTestToken)
	if requestID != "" {
		request.Header.Set("X-Request-ID", requestID)
	}
	if body != "" {
		request.Header.Set("Content-Type", "application/json")
	}
	recorder := httptest.NewRecorder()
	router.ServeHTTP(recorder, request)
	return recorder
}

func assertPlatformChannelControlResponseHasNoSecrets(t *testing.T, body string) {
	t.Helper()
	for _, canary := range []string{
		"SECRET_KEY_CANARY", "SECRET_BASE_URL_CANARY", "SECRET_ORG_CANARY", "SECRET_SETTING_CANARY",
		"SECRET_PARAM_CANARY", "SECRET_HEADER_CANARY", "SECRET_OTHER_CANARY", "SECRET_MAPPING_CANARY",
		"SECRET_PROXY_CANARY", "SECRET_SETTINGS_CANARY", "SECRET_DISABLED_REASON_CANARY",
	} {
		assert.NotContains(t, body, canary)
	}
	for _, forbiddenField := range []string{`"key":`, `"base_url":`, `"settings":`, `"header_override":`, `"param_override":`, `"proxy":`} {
		assert.NotContains(t, body, forbiddenField)
	}
}

func seedPlatformChannelControlUnknownSubmission(t *testing.T, channelID int, operationID string) {
	t.Helper()
	intent := model.PlatformChannelControlIntent{
		OperationID:                 operationID,
		TenantID:                    platformChannelControlTestTenant,
		ChannelID:                   channelID,
		Kind:                        model.PlatformChannelControlOperationKindTest,
		RequestID:                   "channel-control-reconciliation-seed",
		Actor:                       "platform-owner-1",
		Reason:                      "Seed an ambiguous provider submission",
		Model:                       "provider-video-model",
		PublicModelID:               "video.seedance.reconciliation",
		RouteID:                     "route-reconciliation-1",
		UpstreamModel:               "provider-video-model",
		CapabilityProfileID:         "volcengine-ark-video-generation-v1",
		CapabilityProfileRevision:   "sha256:" + strings.Repeat("a", 64),
		CapabilityRevision:          "sha256:" + strings.Repeat("b", 64),
		RoutingReleaseSHA256:        "sha256:" + strings.Repeat("c", 64),
		RouteBindingSHA256:          "sha256:" + strings.Repeat("d", 64),
		CredentialFingerprintSHA256: strings.Repeat("e", 64),
		TransportRevision:           "sha256:" + strings.Repeat("f", 64),
		TransportSHA256:             "sha256:" + strings.Repeat("1", 64),
	}
	_, execute, _, err := model.BeginPlatformChannelTestOperation(intent)
	require.NoError(t, err)
	require.True(t, execute)
	_, claimed, err := model.ClaimPlatformChannelTestSubmission(intent.TenantID, intent.OperationID)
	require.NoError(t, err)
	require.True(t, claimed)
}

func TestPlatformChannelControlListAndDetailAreSecretFreeWhiteLists(t *testing.T) {
	router, channel := setupPlatformChannelControlControllerTest(t)
	query := "?tenant_id=" + platformChannelControlTestTenant
	list := performPlatformChannelControlRequest(router, http.MethodGet, "/channels"+query, "", "")
	require.Equal(t, http.StatusOK, list.Code, list.Body.String())
	assert.Equal(t, "no-store", list.Header().Get("Cache-Control"))
	assertPlatformChannelControlResponseHasNoSecrets(t, list.Body.String())
	var page dto.PlatformChannelControlChannelPage
	require.NoError(t, json.Unmarshal(list.Body.Bytes(), &page))
	require.Len(t, page.Data, 1)
	assert.Equal(t, constant.GetChannelTypeName(channel.Type), page.Data[0].TypeLabel)
	assert.False(t, page.Data[0].TestSupported)
	assert.True(t, page.Data[0].Credential.Configured)
	assert.Equal(t, 2, page.Data[0].Credential.KeyCount)
	assert.Regexp(t, `^sha256:[0-9a-f]{64}$`, page.Data[0].Revision)

	detail := performPlatformChannelControlRequest(router, http.MethodGet, fmt.Sprintf("/channels/%d%s", channel.Id, query), "", "")
	require.Equal(t, http.StatusOK, detail.Code, detail.Body.String())
	assertPlatformChannelControlResponseHasNoSecrets(t, detail.Body.String())
}

func TestPlatformChannelControlTestRequiresExactRouteAndRejectsCallerSelectedModel(t *testing.T) {
	router, channel := setupPlatformChannelControlControllerTest(t)
	body := fmt.Sprintf(`{"operation_id":"channel-test-extra-field-0001","tenant_id":%q,"actor":"platform-owner-1","reason":"Verify channel health","model":"caller-selected-model"}`, platformChannelControlTestTenant)
	rejected := performPlatformChannelControlRequest(router, http.MethodPost, fmt.Sprintf("/channels/%d/test", channel.Id), body, "channel-control-extra-field-request")
	assert.Equal(t, http.StatusUnprocessableEntity, rejected.Code, rejected.Body.String())

	cases := []string{
		fmt.Sprintf(`{"operation_id":"channel-test-binding-missing-0001","tenant_id":%q,"actor":"platform-owner-1","reason":"Verify channel health"}`, platformChannelControlTestTenant),
		fmt.Sprintf(`{"operation_id":"channel-test-binding-missing-0002","tenant_id":%q,"actor":"platform-owner-1","reason":"Verify channel health","public_model_id":"video.seedance.2"}`, platformChannelControlTestTenant),
		fmt.Sprintf(`{"operation_id":"channel-test-binding-missing-0003","tenant_id":%q,"actor":"platform-owner-1","reason":"Verify channel health","route_id":"route-1"}`, platformChannelControlTestTenant),
		fmt.Sprintf(`{"operation_id":"channel-test-binding-missing-0004","tenant_id":%q,"actor":"platform-owner-1","reason":"Verify channel health","public_model_id":" ","route_id":"route-1"}`, platformChannelControlTestTenant),
		fmt.Sprintf(`{"operation_id":"channel-test-binding-missing-0005","tenant_id":%q,"actor":"platform-owner-1","reason":"Verify channel health","public_model_id":"video.seedance.2","route_id":7}`, platformChannelControlTestTenant),
		fmt.Sprintf(`{"operation_id":"channel-test-binding-mode-0001","tenant_id":%q,"actor":"platform-owner-1","reason":"Verify channel health","public_model_id":"video.seedance.2","route_id":"route-1","mode":"audio_to_video"}`, platformChannelControlTestTenant),
		fmt.Sprintf(`{"operation_id":"channel-test-binding-mode-0002","tenant_id":%q,"actor":"platform-owner-1","reason":"Verify channel health","public_model_id":"video.seedance.2","route_id":"route-1","mode":7}`, platformChannelControlTestTenant),
	}
	for index, requestBody := range cases {
		result := performPlatformChannelControlRequest(router, http.MethodPost, fmt.Sprintf("/channels/%d/test", channel.Id), requestBody, fmt.Sprintf("channel-control-binding-required-%d", index))
		assert.Equal(t, http.StatusUnprocessableEntity, result.Code, result.Body.String())
		assertPlatformChannelControlResponseHasNoSecrets(t, result.Body.String())
	}

	var operationCount int64
	require.NoError(t, model.DB.Model(&model.PlatformChannelControlOperation{}).Count(&operationCount).Error)
	assert.Zero(t, operationCount, "invalid Platform requests must not create a durable intent or reach native generic testing")
}

func TestPlatformChannelControlReplayStatusKeepsUnresolvedWorkPending(t *testing.T) {
	assert.Equal(t, http.StatusAccepted, platformChannelControlReplayStatus(dto.PlatformChannelControlOperation{
		State:                   model.PlatformChannelControlOperationPending,
		ProviderSubmissionState: model.PlatformChannelTestSubmissionUnknown,
	}))
	assert.Equal(t, http.StatusAccepted, platformChannelControlReplayStatus(dto.PlatformChannelControlOperation{
		State:                   model.PlatformChannelControlOperationPending,
		ProviderSubmissionState: model.PlatformChannelTestSubmissionSubmitted,
	}))
	assert.Equal(t, http.StatusOK, platformChannelControlReplayStatus(dto.PlatformChannelControlOperation{
		State: model.PlatformChannelControlOperationFailed,
	}))
	assert.Equal(t, http.StatusOK, platformChannelControlReplayStatus(dto.PlatformChannelControlOperation{
		State: model.PlatformChannelControlOperationSucceeded,
	}))
}

func TestPlatformChannelControlReconcileNoCreationExactContractAndSafeText(t *testing.T) {
	router, channel := setupPlatformChannelControlControllerTest(t)
	operationID := "channel-test-reconcile-no-creation-0001"
	seedPlatformChannelControlUnknownSubmission(t, channel.Id, operationID)

	body := fmt.Sprintf(`{"tenant_id":%q,"actor":"platform-owner-1","reason":"已在供应商控制台确认没有创建任务","confirmed_no_provider_creation":true}`, platformChannelControlTestTenant)
	result := performPlatformChannelControlRequest(
		router,
		http.MethodPost,
		fmt.Sprintf("/channels/%d/operations/%s/reconcile-no-creation", channel.Id, operationID),
		body,
		"channel-control-reconcile-no-creation",
	)
	require.Equal(t, http.StatusOK, result.Code, result.Body.String())
	assert.Equal(t, "no-store", result.Header().Get("Cache-Control"))
	assertPlatformChannelControlResponseHasNoSecrets(t, result.Body.String())
	assert.NotContains(t, result.Body.String(), "provider_task_id")
	var receipt dto.PlatformChannelControlOperation
	require.NoError(t, json.Unmarshal(result.Body.Bytes(), &receipt))
	assert.Equal(t, model.PlatformChannelControlOperationFailed, receipt.State)
	assert.Equal(t, model.PlatformChannelTestSubmissionReconciledNoCreation, receipt.ProviderSubmissionState)
	assert.Equal(t, "platform-owner-1", receipt.ReconciliationActor)
	assert.Equal(t, "已在供应商控制台确认没有创建任务", receipt.ReconciliationReason)
	require.NotNil(t, receipt.ReconciledAt)
	require.NotNil(t, receipt.Result)
	assert.Equal(t, model.PlatformChannelControlErrorTestReconciled, receipt.Result.ErrorCode)

	replayed := performPlatformChannelControlRequest(
		router,
		http.MethodPost,
		fmt.Sprintf("/channels/%d/operations/%s/reconcile-no-creation", channel.Id, operationID),
		body,
		"channel-control-reconcile-no-creation-replay",
	)
	assert.Equal(t, http.StatusConflict, replayed.Code, replayed.Body.String())

	unsafe := []map[string]any{
		{"tenant_id": platformChannelControlTestTenant, "actor": "platform-owner-1", "reason": "provider body\nraw", "confirmed_no_provider_creation": true},
		{"tenant_id": platformChannelControlTestTenant, "actor": "platform-owner-1", "reason": "https://provider.invalid/task?token=secret", "confirmed_no_provider_creation": true},
		{"tenant_id": platformChannelControlTestTenant, "actor": "Bearer provider-secret", "reason": "provider console checked", "confirmed_no_provider_creation": true},
		{"tenant_id": platformChannelControlTestTenant, "actor": "platform-owner-1", "reason": "signature = provider-secret", "confirmed_no_provider_creation": true},
	}
	for index, requestBody := range unsafe {
		encoded, err := json.Marshal(requestBody)
		require.NoError(t, err)
		rejected := performPlatformChannelControlRequest(
			router,
			http.MethodPost,
			fmt.Sprintf("/channels/%d/operations/channel-test-reconcile-unsafe-%04d/reconcile-no-creation", channel.Id, index+1),
			string(encoded),
			fmt.Sprintf("channel-control-reconcile-unsafe-%d", index+1),
		)
		assert.Equal(t, http.StatusUnprocessableEntity, rejected.Code, rejected.Body.String())
		assertPlatformChannelControlResponseHasNoSecrets(t, rejected.Body.String())
	}

	extraField := fmt.Sprintf(`{"tenant_id":%q,"actor":"platform-owner-1","reason":"provider console checked","confirmed_no_provider_creation":true,"provider_body":"secret"}`, platformChannelControlTestTenant)
	rejected := performPlatformChannelControlRequest(
		router,
		http.MethodPost,
		fmt.Sprintf("/channels/%d/operations/channel-test-reconcile-extra-0001/reconcile-no-creation", channel.Id),
		extraField,
		"channel-control-reconcile-extra",
	)
	assert.Equal(t, http.StatusUnprocessableEntity, rejected.Code, rejected.Body.String())
}

func TestPlatformChannelControlSoraDTOAndTestFailClosed(t *testing.T) {
	router, channel := setupPlatformChannelControlControllerTest(t)
	require.NoError(t, model.DB.Model(&model.Channel{}).Where("id = ?", channel.Id).
		Update("type", constant.ChannelTypeSora).Error)

	query := "?tenant_id=" + platformChannelControlTestTenant
	detail := performPlatformChannelControlRequest(router, http.MethodGet, fmt.Sprintf("/channels/%d%s", channel.Id, query), "", "")
	require.Equal(t, http.StatusOK, detail.Code, detail.Body.String())
	var channelDTO dto.PlatformChannelControlChannel
	require.NoError(t, json.Unmarshal(detail.Body.Bytes(), &channelDTO))
	assert.Equal(t, constant.ChannelTypeSora, channelDTO.Type)
	assert.Equal(t, constant.GetChannelTypeName(constant.ChannelTypeSora), channelDTO.TypeLabel)
	assert.False(t, channelDTO.TestSupported)

	body := fmt.Sprintf(`{"operation_id":"channel-test-sora-0001","tenant_id":%q,"actor":"platform-owner-1","reason":"Verify Sora test fails closed"}`, platformChannelControlTestTenant)
	result := performPlatformChannelControlRequest(router, http.MethodPost, fmt.Sprintf("/channels/%d/test", channel.Id), body, "channel-control-sora-test")
	require.Equal(t, http.StatusUnprocessableEntity, result.Code, result.Body.String())
	assertPlatformChannelControlResponseHasNoSecrets(t, result.Body.String())
}

func TestPlatformChannelControlStatusCASAndLostResponseReceipt(t *testing.T) {
	router, channel := setupPlatformChannelControlControllerTest(t)
	query := "?tenant_id=" + platformChannelControlTestTenant
	detail := performPlatformChannelControlRequest(router, http.MethodGet, fmt.Sprintf("/channels/%d%s", channel.Id, query), "", "")
	require.Equal(t, http.StatusOK, detail.Code, detail.Body.String())
	var channelDTO dto.PlatformChannelControlChannel
	require.NoError(t, json.Unmarshal(detail.Body.Bytes(), &channelDTO))

	statusBody := fmt.Sprintf(`{"operation_id":"channel-status-operation-0001","tenant_id":%q,"actor":"platform-owner-1","reason":"Disable unhealthy provider channel","expected_revision":%q,"target_status":"manually_disabled"}`, platformChannelControlTestTenant, channelDTO.Revision)
	changed := performPlatformChannelControlRequest(router, http.MethodPost, fmt.Sprintf("/channels/%d/status", channel.Id), statusBody, "channel-control-status-request")
	require.Equal(t, http.StatusOK, changed.Code, changed.Body.String())
	var changedReceipt dto.PlatformChannelControlOperation
	require.NoError(t, json.Unmarshal(changed.Body.Bytes(), &changedReceipt))
	assert.Equal(t, channelDTO.Revision, changedReceipt.ExpectedRevision)
	assert.Equal(t, "manually_disabled", changedReceipt.TargetStatus)
	assert.Equal(t, "succeeded", changedReceipt.State)
	require.NotNil(t, changedReceipt.Result)
	assert.Equal(t, "enabled", changedReceipt.Result.PreviousStatus)
	assert.Equal(t, "manually_disabled", changedReceipt.Result.CurrentStatus)
	require.NotNil(t, changedReceipt.Result.Changed)
	assert.True(t, *changedReceipt.Result.Changed)

	staleBody := fmt.Sprintf(`{"operation_id":"channel-status-operation-0002","tenant_id":%q,"actor":"platform-owner-1","reason":"Enable after stale review","expected_revision":%q,"target_status":"enabled"}`, platformChannelControlTestTenant, channelDTO.Revision)
	conflict := performPlatformChannelControlRequest(router, http.MethodPost, fmt.Sprintf("/channels/%d/status", channel.Id), staleBody, "channel-control-status-stale")
	require.Equal(t, http.StatusConflict, conflict.Code, conflict.Body.String())

	readback := performPlatformChannelControlRequest(router, http.MethodGet, fmt.Sprintf("/channels/%d/operations/channel-status-operation-0002%s", channel.Id, query), "", "")
	require.Equal(t, http.StatusOK, readback.Code, readback.Body.String())
	var failedReceipt dto.PlatformChannelControlOperation
	require.NoError(t, json.Unmarshal(readback.Body.Bytes(), &failedReceipt))
	assert.Equal(t, "failed", failedReceipt.State)
	assert.Equal(t, channelDTO.Revision, failedReceipt.ExpectedRevision)
	assert.Equal(t, "enabled", failedReceipt.TargetStatus)
	require.NotNil(t, failedReceipt.Result)
	assert.Equal(t, model.PlatformChannelControlErrorRevisionConflict, failedReceipt.Result.ErrorCode)
	assert.Equal(t, failedReceipt.Result.PreviousStatus, failedReceipt.Result.CurrentStatus)
	require.NotNil(t, failedReceipt.Result.Changed)
	assert.False(t, *failedReceipt.Result.Changed)
	assertPlatformChannelControlResponseHasNoSecrets(t, readback.Body.String())
}

func TestPlatformChannelControlOperationReadbackCanonicalizesDatabaseTimestampsToUTC(t *testing.T) {
	nonUTC := time.FixedZone("UTC+8", 8*60*60)
	createdAt := time.Date(2026, 8, 28, 15, 30, 0, 0, nonUTC)
	completedAt := time.Date(2026, 8, 28, 15, 31, 2, 0, nonUTC)
	operationID := "channel-test-non-utc-readback-0001"
	router, channel := setupPlatformChannelControlControllerTestWithV5Seed(t, func(database *gorm.DB, channel model.Channel) {
		require.NoError(t, database.Table("platform_channel_control_operations").Create(map[string]any{
			"id":                 uuid.NewString(),
			"tenant_id":          platformChannelControlTestTenant,
			"operation_id":       operationID,
			"channel_id":         channel.Id,
			"kind":               model.PlatformChannelControlOperationKindTest,
			"state":              model.PlatformChannelControlOperationSucceeded,
			"request_id":         "channel-control-non-utc-readback",
			"actor":              "platform-owner-1",
			"reason":             "Verify canonical UTC receipt timestamps",
			"intent_sha256":      strings.Repeat("a", sha256.Size*2),
			"intent_json":        `{}`,
			"result_success":     true,
			"result_response_ms": int64(62_000),
			"created_at":         createdAt,
			"completed_at":       completedAt,
		}).Error)
	})

	query := "?tenant_id=" + platformChannelControlTestTenant
	readback := performPlatformChannelControlRequest(
		router,
		http.MethodGet,
		fmt.Sprintf("/channels/%d/operations/%s%s", channel.Id, operationID, query),
		"",
		"",
	)
	require.Equal(t, http.StatusOK, readback.Code, readback.Body.String())

	var payload map[string]any
	require.NoError(t, json.Unmarshal(readback.Body.Bytes(), &payload))
	assert.Equal(t, "2026-08-28T07:30:00Z", payload["created_at"])
	assert.Equal(t, "2026-08-28T07:31:02Z", payload["completed_at"])
}

func TestPlatformChannelControlRejectsValidOperationsTenantThatIsNotGlobalController(t *testing.T) {
	router, _ := setupPlatformChannelControlControllerTest(t)
	otherTenant := "58775bb2-b6d2-4ad3-ab03-2f9d10854ba1"
	otherToken := "other-operations-token-with-at-least-32-bytes"
	firstDigest := fmt.Sprintf("%x", sha256.Sum256([]byte(platformChannelControlTestToken)))
	otherDigest := fmt.Sprintf("%x", sha256.Sum256([]byte(otherToken)))
	t.Setenv("RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON", fmt.Sprintf(`[{"tenant_id":%q,"token_sha256":%q},{"tenant_id":%q,"token_sha256":%q}]`, platformChannelControlTestTenant, firstDigest, otherTenant, otherDigest))
	request := httptest.NewRequest(http.MethodGet, "/channels?tenant_id="+otherTenant, nil)
	request.Header.Set("X-Relay-Operations-Token", otherToken)
	recorder := httptest.NewRecorder()
	router.ServeHTTP(recorder, request)
	assert.Equal(t, http.StatusForbidden, recorder.Code, recorder.Body.String())
	assert.Contains(t, recorder.Body.String(), model.PlatformGenerationErrorControlTenantForbidden)
}
