package middleware

import (
	"crypto/sha256"
	"fmt"
	"net/http"
	"net/http/httptest"
	"regexp"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/glebarez/sqlite"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func configurePlatformRelayAuthTest(t *testing.T) {
	t.Helper()
	t.Setenv("RELAY_COMPAT_ENABLED", "true")
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", `{
		"platform": {
			"tenant_id": "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30",
			"api_key": "relay-secret",
			"upstream_token": "upstream-token"
		}
	}`)
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", `{}`)
}

func TestPlatformGenerationServiceAuthBindsTenantAndEchoesSafeRequestID(t *testing.T) {
	configurePlatformRelayAuthTest(t)
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(PlatformGenerationServiceAuth())
	router.GET("/", func(c *gin.Context) {
		principal, ok := GetPlatformRelayPrincipal(c)
		require.True(t, ok)
		assert.Equal(t, "platform", principal.ClientID)
		assert.Equal(t, "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30", principal.TenantID)
		c.Status(http.StatusNoContent)
	})

	request := httptest.NewRequest(http.MethodGet, "/", nil)
	request.Header.Set("X-Client-ID", "platform")
	request.Header.Set("X-API-Key", "relay-secret")
	request.Header.Set("X-Request-ID", "platform-task:123")
	response := httptest.NewRecorder()
	router.ServeHTTP(response, request)

	assert.Equal(t, http.StatusNoContent, response.Code)
	assert.Equal(t, "platform-task:123", response.Header().Get("X-Request-ID"))
}

func TestPlatformGenerationServiceAuthNormalizesUnsafeRequestID(t *testing.T) {
	configurePlatformRelayAuthTest(t)
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(PlatformGenerationServiceAuth())
	router.GET("/", func(c *gin.Context) { c.Status(http.StatusNoContent) })

	request := httptest.NewRequest(http.MethodGet, "/", nil)
	request.Header.Set("X-Client-ID", "platform")
	request.Header.Set("X-API-Key", "relay-secret")
	request.Header.Set("X-Request-ID", "unsafe request id\r\n")
	response := httptest.NewRecorder()
	router.ServeHTTP(response, request)

	requestID := response.Header().Get("X-Request-ID")
	assert.Equal(t, http.StatusNoContent, response.Code)
	assert.NotEqual(t, "unsafe request id\r\n", requestID)
	assert.Regexp(t, regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$`), requestID)
}

func TestPlatformGenerationServiceAuthReturnsContractErrorEnvelope(t *testing.T) {
	configurePlatformRelayAuthTest(t)
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(PlatformGenerationServiceAuth())
	router.GET("/", func(c *gin.Context) { c.Status(http.StatusNoContent) })

	request := httptest.NewRequest(http.MethodGet, "/", nil)
	request.Header.Set("X-Request-ID", "auth-check")
	response := httptest.NewRecorder()
	router.ServeHTTP(response, request)

	var envelope dto.PlatformGenerationErrorEnvelope
	require.NoError(t, common.Unmarshal(response.Body.Bytes(), &envelope))
	assert.Equal(t, http.StatusUnauthorized, response.Code)
	assert.Equal(t, "CLIENT_AUTHENTICATION_REQUIRED", envelope.Error.Code)
	assert.Equal(t, "auth-check", envelope.Error.RequestID)
	assert.Empty(t, envelope.Error.Details)
	assert.Equal(t, "auth-check", response.Header().Get("X-Request-ID"))
}

func configureProtectedPlatformRelayAuthTest(t *testing.T) (*model.Token, string) {
	t.Helper()
	previousDB := model.DB
	previousType := common.MainDatabaseType()
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, db.AutoMigrate(
		&model.User{}, &model.Token{}, &model.UserSession{}, &model.AuthFlow{},
		&model.ExternalIdentityClaim{}, &model.PasskeyCredential{}, &model.UserOAuthBinding{},
		&model.TwoFA{}, &model.TwoFABackupCode{},
	))
	model.DB = db
	common.SetMainDatabaseType(common.DatabaseTypeSQLite)
	t.Cleanup(func() {
		model.DB = previousDB
		common.SetMainDatabaseType(previousType)
	})

	tenantID := "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30"
	apiKey := strings.Repeat("A", 48)
	tokenKey := strings.Repeat("T", 48)
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_COMPAT_ENABLED", "true")
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("ENVIRONMENT", "staging")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "staging")
	t.Setenv("RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN", strings.Repeat("I", 48))
	operationsToken := "ops-6Jq3Lr8Vm2Xc9Np5Wt7Hy4Ks1Bd0FgEaQzUi"
	operationsDigest := fmt.Sprintf("%x", sha256.Sum256([]byte(operationsToken)))
	t.Setenv("RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON", fmt.Sprintf(
		`[{"tenant_id":%q,"token_sha256":%q}]`, tenantID, operationsDigest,
	))
	t.Setenv("RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON", fmt.Sprintf(
		`[{"tenant_id":%q,"key_id":"test-key-v1","secret":"approval-4Mg8Qw2Zx7Vk5Hs9Nc3Jr6Ty1Bd0FpEa"}]`, tenantID,
	))
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", "")
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", `{}`)
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", fmt.Sprintf(
		`{"platform":{"tenant_id":%q,"api_key":%q,"upstream_token":%q}}`,
		tenantID, apiKey, "sk-"+tokenKey,
	))
	serviceUser, err := service.BuildProtectedPlatformRelayServicePrincipalUser("platform", tenantID)
	require.NoError(t, err)
	user := &serviceUser
	require.NoError(t, model.DB.Create(user).Error)
	token := &model.Token{
		UserId: user.Id, Key: tokenKey, Name: "platform-relay:platform:" + tenantID,
		Status: common.TokenStatusEnabled, ExpiredTime: -1, UnlimitedQuota: true,
	}
	require.NoError(t, model.DB.Create(token).Error)
	require.NoError(t, service.ValidateProtectedPlatformRelayServicePrincipals())
	return token, apiKey
}

func TestPlatformGenerationServiceAuthDistinguishesCredentialMismatchFromPrincipalDrift(t *testing.T) {
	t.Run("credential mismatch is unauthorized", func(t *testing.T) {
		_, _ = configureProtectedPlatformRelayAuthTest(t)
		router := gin.New()
		router.Use(PlatformGenerationServiceAuth())
		router.GET("/", func(c *gin.Context) { c.Status(http.StatusNoContent) })
		request := httptest.NewRequest(http.MethodGet, "/", nil)
		request.Header.Set("X-Client-ID", "platform")
		request.Header.Set("X-API-Key", strings.Repeat("X", 48))
		response := httptest.NewRecorder()

		router.ServeHTTP(response, request)

		var envelope dto.PlatformGenerationErrorEnvelope
		require.NoError(t, common.Unmarshal(response.Body.Bytes(), &envelope))
		require.Equal(t, http.StatusUnauthorized, response.Code)
		require.Equal(t, "INVALID_CLIENT_CREDENTIALS", envelope.Error.Code)
		require.False(t, envelope.Error.Retryable)
	})

	t.Run("database principal drift is retryable unavailable", func(t *testing.T) {
		token, apiKey := configureProtectedPlatformRelayAuthTest(t)
		require.NoError(t, model.DB.Model(token).Update("unlimited_quota", false).Error)
		router := gin.New()
		router.Use(PlatformGenerationServiceAuth())
		router.GET("/", func(c *gin.Context) { c.Status(http.StatusNoContent) })
		request := httptest.NewRequest(http.MethodGet, "/", nil)
		request.Header.Set("X-Client-ID", "platform")
		request.Header.Set("X-API-Key", apiKey)
		response := httptest.NewRecorder()

		router.ServeHTTP(response, request)

		var envelope dto.PlatformGenerationErrorEnvelope
		require.NoError(t, common.Unmarshal(response.Body.Bytes(), &envelope))
		require.Equal(t, http.StatusServiceUnavailable, response.Code)
		require.Equal(t, "SERVICE_PRINCIPAL_UNAVAILABLE", envelope.Error.Code)
		require.True(t, envelope.Error.Retryable)
		require.NotContains(t, response.Body.String(), token.Key)
	})
}
