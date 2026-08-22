package controller

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/constant"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestPlatformRelayInstanceHeaderIsReadinessOnlyAndProcessStable(t *testing.T) {
	gin.SetMode(gin.TestMode)
	buildRecorder := httptest.NewRecorder()
	buildContext, _ := gin.CreateTestContext(buildRecorder)
	setPlatformRelayBuildHeaders(buildContext)
	require.Empty(t, buildRecorder.Header().Get("X-Relay-Instance-ID"))

	readyRecorder := httptest.NewRecorder()
	readyContext, _ := gin.CreateTestContext(readyRecorder)
	setPlatformRelayReadyInstanceHeader(readyContext)
	first := readyRecorder.Header().Get("X-Relay-Instance-ID")
	parsed, err := uuid.Parse(first)
	require.NoError(t, err)
	require.Equal(t, uuid.Version(4), parsed.Version())

	secondRecorder := httptest.NewRecorder()
	secondContext, _ := gin.CreateTestContext(secondRecorder)
	setPlatformRelayReadyInstanceHeader(secondContext)
	require.Equal(t, first, secondRecorder.Header().Get("X-Relay-Instance-ID"))
}

func TestPlatformRelayRuntimeBuildIdentityRequiresInternalAdmission(t *testing.T) {
	t.Setenv("RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN", "runtime-identity-token")
	t.Setenv("RELAY_COMPAT_SOURCE_REVISION", "1111111111111111111111111111111111111111")
	t.Setenv("RELAY_COMPAT_IMAGE_DIGEST", "sha256:2222222222222222222222222222222222222222222222222222222222222222")
	t.Setenv("RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256", "sha256:3333333333333333333333333333333333333333333333333333333333333333")
	t.Setenv("RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT", "321")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", `{"platform":{"tenant_id":"00000000-0000-4000-8000-000000000001","api_key":"dev-api-key","upstream_token":"dev-token"}}`)
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", `{}`)
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", "")
	gin.SetMode(gin.TestMode)

	unauthorized := httptest.NewRecorder()
	unauthorizedContext, _ := gin.CreateTestContext(unauthorized)
	unauthorizedContext.Request = httptest.NewRequest(http.MethodGet, "/internal/platform-relay/runtime-build-identity", nil)
	PlatformRelayRuntimeBuildIdentity(unauthorizedContext)
	require.Equal(t, http.StatusUnauthorized, unauthorized.Code)

	authorized := httptest.NewRecorder()
	authorizedContext, _ := gin.CreateTestContext(authorized)
	authorizedContext.Request = httptest.NewRequest(http.MethodGet, "/internal/platform-relay/runtime-build-identity", nil)
	authorizedContext.Request.Header.Set(constant.HeaderPlatformGenerationInternalAdmission, "runtime-identity-token")
	PlatformRelayRuntimeBuildIdentity(authorizedContext)
	require.Equal(t, http.StatusOK, authorized.Code)
	require.Equal(t, "no-store", authorized.Header().Get("Cache-Control"))
	require.Contains(t, authorized.Body.String(), `"source_snapshot_file_count":321`)
	require.Contains(t, authorized.Body.String(), `"route_acceptance"`)
	require.NotContains(t, authorized.Body.String(), `"signature"`)
}
