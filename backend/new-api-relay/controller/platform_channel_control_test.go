package controller

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

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
	require.NoError(t, model.MigratePlatformChannelControlStorage())
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

	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(middleware.PlatformRelayRequestID())
	router.GET("/channels", ListPlatformChannelControlChannels)
	router.GET("/channels/:channel_id", GetPlatformChannelControlChannel)
	router.POST("/channels/:channel_id/test", TestPlatformChannelControlChannel)
	router.POST("/channels/:channel_id/status", UpdatePlatformChannelControlStatus)
	router.GET("/channels/:channel_id/operations/:operation_id", GetPlatformChannelControlOperation)
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

func TestPlatformChannelControlTestRejectsCallerSelectedModelAndNeverLeaksProviderError(t *testing.T) {
	router, channel := setupPlatformChannelControlControllerTest(t)
	body := fmt.Sprintf(`{"operation_id":"channel-test-extra-field-0001","tenant_id":%q,"actor":"platform-owner-1","reason":"Verify channel health","model":"caller-selected-model"}`, platformChannelControlTestTenant)
	rejected := performPlatformChannelControlRequest(router, http.MethodPost, fmt.Sprintf("/channels/%d/test", channel.Id), body, "channel-control-extra-field-request")
	assert.Equal(t, http.StatusUnprocessableEntity, rejected.Code, rejected.Body.String())

	validBody := fmt.Sprintf(`{"operation_id":"channel-test-operation-0001","tenant_id":%q,"actor":"platform-owner-1","reason":"Verify channel health"}`, platformChannelControlTestTenant)
	result := performPlatformChannelControlRequest(router, http.MethodPost, fmt.Sprintf("/channels/%d/test", channel.Id), validBody, "channel-control-test-request")
	require.Equal(t, http.StatusOK, result.Code, result.Body.String())
	assertPlatformChannelControlResponseHasNoSecrets(t, result.Body.String())
	var receipt dto.PlatformChannelControlOperation
	require.NoError(t, json.Unmarshal(result.Body.Bytes(), &receipt))
	assert.Equal(t, "test", receipt.Kind)
	assert.Equal(t, "failed", receipt.State)
	require.NotNil(t, receipt.Result)
	assert.Equal(t, model.PlatformChannelControlErrorTestUnavailable, receipt.Result.ErrorCode)
	assert.Empty(t, receipt.ExpectedRevision)
	assert.Empty(t, receipt.TargetStatus)

	replay := performPlatformChannelControlRequest(router, http.MethodPost, fmt.Sprintf("/channels/%d/test", channel.Id), validBody, "channel-control-test-replay")
	require.Equal(t, http.StatusOK, replay.Code, replay.Body.String())
	assert.Equal(t, "true", replay.Header().Get("X-Idempotent-Replay"))
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
	require.Equal(t, http.StatusOK, result.Code, result.Body.String())
	var receipt dto.PlatformChannelControlOperation
	require.NoError(t, json.Unmarshal(result.Body.Bytes(), &receipt))
	assert.Equal(t, model.PlatformChannelControlOperationFailed, receipt.State)
	require.NotNil(t, receipt.Result)
	assert.Equal(t, model.PlatformChannelControlErrorTestUnavailable, receipt.Result.ErrorCode)
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
