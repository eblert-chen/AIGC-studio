package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestProtectedRelayNativeCompatibilityGateRunsBeforeProviderRoute(t *testing.T) {
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	gin.SetMode(gin.TestMode)
	called := false
	router := gin.New()
	router.POST("/v1/chat/completions", RejectProtectedRelayNativeCompatibility(), func(c *gin.Context) {
		called = true
		c.Status(http.StatusNoContent)
	})
	request := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
	request.Header.Set("Authorization", "Bearer forged")
	request.Header.Set("X-Platform-Generation-Internal", "forged")
	response := httptest.NewRecorder()
	router.ServeHTTP(response, request)
	require.Equal(t, http.StatusForbidden, response.Code)
	require.False(t, called)
}

func TestDevelopmentRelayNativeCompatibilityRemainsAvailable(t *testing.T) {
	t.Setenv("APP_ENV", "development")
	t.Setenv("DEPLOYMENT_ENV", "development")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
	gin.SetMode(gin.TestMode)
	called := false
	router := gin.New()
	router.POST("/v1/chat/completions", RejectProtectedRelayNativeCompatibility(), func(c *gin.Context) {
		called = true
		c.Status(http.StatusNoContent)
	})
	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil))
	require.Equal(t, http.StatusNoContent, response.Code)
	require.True(t, called)
}
