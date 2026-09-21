package router

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
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

type providerOnboardingRouterIdentity struct {
	accessToken string
	identity    service.AuthIdentity
}

func setupProviderOnboardingRouterTest(t *testing.T) *gin.Engine {
	t.Helper()
	previousDB, previousLogDB := model.DB, model.LOG_DB
	previousMainType, previousLogType := common.MainDatabaseType(), common.LogDatabaseType()
	previousRedis := common.RedisEnabled
	previousSessionSecret := common.SessionSecret

	db, err := gorm.Open(sqlite.Open("file:provider-onboarding-router-"+uuid.NewString()+"?mode=memory&cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, db.AutoMigrate(
		&model.User{},
		&model.UserSession{},
		&model.AuthFlow{},
		&model.Log{},
		&model.Channel{},
		&model.ProviderChannelCredentialSetVersion{},
		&model.Ability{},
		&model.PlatformGenerationProviderRoute{},
	))
	model.DB, model.LOG_DB = db, db
	common.SetDatabaseTypes(common.DatabaseTypeSQLite, common.DatabaseTypeSQLite)
	common.RedisEnabled = false
	common.SessionSecret = "provider-onboarding-router-session-secret"

	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("APP_ENV", "development")
	t.Setenv("DEPLOYMENT_ENV", "development")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
	t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "false")
	t.Setenv("RELAY_DATABASE_SECRET_FILES_REQUIRED", "false")
	t.Setenv("RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED", "false")
	t.Setenv("RELAY_SECRET_ISOLATION_GENERATION", "")
	t.Setenv("RELAY_ROOT_SECRET_ISOLATION_PROOF_FILE", "")
	t.Setenv("RELAY_SECRET_ISOLATION_RECEIPT_FILE", "")
	t.Setenv("RELAY_SECRET_ISOLATION_COMMIT_FILE", "")
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE", "")
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON", `{"schema_version":1,"active_key_id":"router-v1","keys":{"router-v1":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}}`)
	require.NoError(t, model.MigrateProviderChannelCredentialVaultStorage())

	t.Cleanup(func() {
		model.DB, model.LOG_DB = previousDB, previousLogDB
		common.SetDatabaseTypes(previousMainType, previousLogType)
		common.RedisEnabled = previousRedis
		common.SessionSecret = previousSessionSecret
	})

	engine := gin.New()
	api := engine.Group("/api")
	registerProviderOnboardingRoutes(api)
	registerChannelRoutes(api)
	return engine
}

func issueProviderOnboardingBoundProof(t *testing.T, identity service.AuthIdentity, scope, provider, path, body, revision string) string {
	t.Helper()
	digest := sha256.Sum256([]byte(body))
	proof, _, err := service.IssueBoundSecurityProof(identity, "2fa", scope, service.SecurityProofBinding{
		Action: scope, Provider: provider, HTTPMethod: http.MethodPost, HTTPPath: path,
		BodySHA256: "sha256:" + hex.EncodeToString(digest[:]), ExpectedRevision: revision,
	})
	require.NoError(t, err)
	return proof
}

func createProviderOnboardingRouterIdentity(t *testing.T, role int, username string) providerOnboardingRouterIdentity {
	t.Helper()
	user := model.User{
		Username: username, Password: "password-placeholder", Role: role,
		Status: common.UserStatusEnabled, Group: "default", AuthVersion: 1,
		AffCode: "provider-onboarding-" + username,
	}
	require.NoError(t, model.DB.Create(&user).Error)
	now := time.Now().Unix()
	session := model.UserSession{
		SID: "provider-onboarding-" + uuid.NewString(), UserID: user.Id,
		Version: 1, UserAuthVersion: user.AuthVersion,
		Status: model.UserSessionStatusActive, RefreshHash: "refresh-hash-" + uuid.NewString(),
		LoginMethod: "password", LastActiveAt: now, ExpiresAt: now + 3600,
	}
	require.NoError(t, model.CreateUserSession(&session))
	identity := service.AuthIdentity{
		UserID: user.Id, SessionID: session.SID,
		UserAuthVersion: session.UserAuthVersion, SessionVersion: session.Version,
	}
	accessToken, _, err := service.IssueAccessToken(identity)
	require.NoError(t, err)
	return providerOnboardingRouterIdentity{accessToken: accessToken, identity: identity}
}

func providerOnboardingRouterRequest(
	engine *gin.Engine,
	method string,
	path string,
	body string,
	accessToken string,
	securityProof string,
) *httptest.ResponseRecorder {
	request := httptest.NewRequest(method, path, bytes.NewBufferString(body))
	request.Header.Set("Content-Type", "application/json")
	if accessToken != "" {
		request.Header.Set("Authorization", "Bearer "+accessToken)
	}
	if securityProof != "" {
		request.Header.Set("X-Security-Proof", securityProof)
	}
	response := httptest.NewRecorder()
	engine.ServeHTTP(response, request)
	return response
}

func TestProviderOnboardingRoutesRequireRootSessionAndExactWriteProof(t *testing.T) {
	gin.SetMode(gin.TestMode)
	engine := setupProviderOnboardingRouterTest(t)
	root := createProviderOnboardingRouterIdentity(t, common.RoleRootUser, "provider-router-root")
	admin := createProviderOnboardingRouterIdentity(t, common.RoleAdminUser, "provider-router-admin")

	response := providerOnboardingRouterRequest(engine, http.MethodGet, "/api/provider-onboarding", "", admin.accessToken, "")
	assert.Equal(t, http.StatusForbidden, response.Code)

	response = providerOnboardingRouterRequest(engine, http.MethodGet, "/api/provider-onboarding", "", root.accessToken, "")
	require.Equal(t, http.StatusOK, response.Code, response.Body.String())
	assert.NotContains(t, response.Body.String(), "api_key")

	credentialCanary := "google-router-credential-canary-7h4G2s9Q"
	requestBody := `{"api_key":"` + credentialCanary + `","reason":"configure reviewed Google primary credential"}`
	response = providerOnboardingRouterRequest(
		engine, http.MethodPost, "/api/provider-onboarding/google-gemini-api/credential",
		requestBody, root.accessToken, "",
	)
	assert.Equal(t, http.StatusForbidden, response.Code)
	assert.Contains(t, response.Body.String(), "SECURITY_PROOF_REQUIRED")

	wrongScopeProof, _, err := service.IssueSecurityProof(root.identity, "2fa", []string{"channel.key.read"})
	require.NoError(t, err)
	response = providerOnboardingRouterRequest(
		engine, http.MethodPost, "/api/provider-onboarding/google-gemini-api/credential",
		requestBody, root.accessToken, wrongScopeProof,
	)
	assert.Equal(t, http.StatusForbidden, response.Code)
	assert.Contains(t, response.Body.String(), "SECURITY_PROOF_SCOPE_MISMATCH")

	credentialPath := "/api/provider-onboarding/google-gemini-api/credential"
	writeProof := issueProviderOnboardingBoundProof(
		t, root.identity, middleware.SecurityProofScopeProviderCredentialWrite,
		"google-gemini-api", credentialPath, requestBody, "",
	)
	rootPAT := "provider-router-root-pat"
	require.NoError(t, model.DB.Create(&model.User{
		Username: "provider-router-pat-root", Password: "password-placeholder",
		Role: common.RoleRootUser, Status: common.UserStatusEnabled, Group: "default",
		AccessToken: &rootPAT, AuthVersion: 1, AffCode: "provider-onboarding-pat-root",
	}).Error)
	response = providerOnboardingRouterRequest(
		engine, http.MethodPost, credentialPath,
		requestBody, rootPAT, writeProof,
	)
	assert.Equal(t, http.StatusForbidden, response.Code)
	assert.Contains(t, response.Body.String(), "SECURITY_PROOF_INVALID")

	response = providerOnboardingRouterRequest(
		engine, http.MethodPost, credentialPath,
		requestBody, root.accessToken, writeProof,
	)
	require.Equal(t, http.StatusOK, response.Code, response.Body.String())
	assert.NotContains(t, response.Body.String(), credentialCanary)
	assert.NotContains(t, response.Body.String(), "api_key")
	assert.NotContains(t, response.Body.String(), "credential_set_version")

	response = providerOnboardingRouterRequest(
		engine, http.MethodPost, credentialPath, requestBody, root.accessToken, writeProof,
	)
	assert.Equal(t, http.StatusForbidden, response.Code)
	assert.Contains(t, response.Body.String(), "SECURITY_PROOF_CONSUMED")

	tamperedBody := `{"api_key":"different-key","reason":"configure reviewed Google primary credential"}`
	tamperedProof := issueProviderOnboardingBoundProof(
		t, root.identity, middleware.SecurityProofScopeProviderCredentialWrite,
		"google-gemini-api", credentialPath, requestBody, "",
	)
	response = providerOnboardingRouterRequest(
		engine, http.MethodPost, credentialPath, tamperedBody, root.accessToken, tamperedProof,
	)
	assert.Equal(t, http.StatusForbidden, response.Code)
	assert.Contains(t, response.Body.String(), "SECURITY_PROOF_REQUEST_MISMATCH")

	disableBody := `{"reason":"disable reviewed Google route-test channel","expected_revision":"sha256:` + strings.Repeat("1", 64) + `"}`
	disablePath := "/api/provider-onboarding/google-gemini-api/disable"
	disableProof := issueProviderOnboardingBoundProof(
		t, root.identity, middleware.SecurityProofScopeProviderDisable,
		"google-gemini-api", disablePath, disableBody, "sha256:"+strings.Repeat("1", 64),
	)
	response = providerOnboardingRouterRequest(
		engine, http.MethodPost, "/api/provider-onboarding/google-gemini-api/resume",
		disableBody, root.accessToken, disableProof,
	)
	assert.Equal(t, http.StatusForbidden, response.Code)
	assert.Contains(t, response.Body.String(), "SECURITY_PROOF_SCOPE_MISMATCH")

	revisionChangedBody := `{"reason":"disable reviewed Google route-test channel","expected_revision":"sha256:` + strings.Repeat("2", 64) + `"}`
	revisionProof := issueProviderOnboardingBoundProof(
		t, root.identity, middleware.SecurityProofScopeProviderDisable,
		"google-gemini-api", disablePath, disableBody, "sha256:"+strings.Repeat("1", 64),
	)
	response = providerOnboardingRouterRequest(
		engine, http.MethodPost, disablePath, revisionChangedBody, root.accessToken, revisionProof,
	)
	assert.Equal(t, http.StatusForbidden, response.Code)
	assert.Contains(t, response.Body.String(), "SECURITY_PROOF_REQUEST_MISMATCH")

	crossProviderProof := issueProviderOnboardingBoundProof(
		t, root.identity, middleware.SecurityProofScopeProviderCredentialWrite,
		"google-gemini-api", credentialPath, requestBody, "",
	)
	response = providerOnboardingRouterRequest(
		engine, http.MethodPost, "/api/provider-onboarding/minimax/credential",
		requestBody, root.accessToken, crossProviderProof,
	)
	assert.Equal(t, http.StatusForbidden, response.Code)
	assert.Contains(t, response.Body.String(), "SECURITY_PROOF_REQUEST_MISMATCH")

	response = providerOnboardingRouterRequest(
		engine, http.MethodPost, "/api/channel/990001/key", "{}",
		root.accessToken, wrongScopeProof,
	)
	assert.Equal(t, http.StatusConflict, response.Code)
	assert.Contains(t, response.Body.String(), "PROVIDER_ONBOARDING_MANAGED_CHANNEL")
	assert.NotContains(t, response.Body.String(), credentialCanary)

	taggedCredentialCanary := "tag-managed-router-credential-canary-2q8P"
	tag := model.ProviderOnboardingManagedChannelTag
	baseURL := "https://managed-tag.invalid"
	require.NoError(t, model.DB.Create(&model.Channel{
		Id: 880001, Type: 1, Key: taggedCredentialCanary,
		Status: common.ChannelStatusManuallyDisabled, Name: "tag-managed-legacy-guard",
		BaseURL: &baseURL, Models: "guard-model", Group: "default", Tag: &tag,
	}).Error)
	response = providerOnboardingRouterRequest(
		engine, http.MethodPost, "/api/channel/880001/key", "{}",
		root.accessToken, wrongScopeProof,
	)
	assert.Equal(t, http.StatusConflict, response.Code)
	assert.Contains(t, response.Body.String(), "PROVIDER_ONBOARDING_MANAGED_CHANNEL")
	assert.NotContains(t, response.Body.String(), taggedCredentialCanary)
}
