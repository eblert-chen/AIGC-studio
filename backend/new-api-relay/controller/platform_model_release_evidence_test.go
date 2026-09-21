package controller

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
	"github.com/glebarez/sqlite"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func TestPlatformRelayModelReleaseEvidenceRequiresServiceAdmissionAndIsSecretFree(t *testing.T) {
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
	require.NoError(t, database.AutoMigrate(&model.PlatformChannelControlOperation{}))

	t.Setenv("RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN", "model-release-evidence-token")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", `{"platform":{"tenant_id":"00000000-0000-4000-8000-000000000001","api_key":"dev-api-key","upstream_token":"dev-token"}}`)
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", `{}`)
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", "")
	gin.SetMode(gin.TestMode)

	unauthorized := httptest.NewRecorder()
	unauthorizedContext, _ := gin.CreateTestContext(unauthorized)
	unauthorizedContext.Request = httptest.NewRequest(http.MethodGet, "/internal/platform-relay/model-release-evidence", nil)
	PlatformRelayModelReleaseEvidence(unauthorizedContext)
	require.Equal(t, http.StatusUnauthorized, unauthorized.Code)

	authorized := httptest.NewRecorder()
	authorizedContext, _ := gin.CreateTestContext(authorized)
	authorizedContext.Request = httptest.NewRequest(http.MethodGet, "/internal/platform-relay/model-release-evidence", nil)
	authorizedContext.Request.Header.Set(constant.HeaderPlatformGenerationInternalAdmission, "model-release-evidence-token")
	PlatformRelayModelReleaseEvidence(authorizedContext)
	require.Equal(t, http.StatusOK, authorized.Code)
	require.Equal(t, "no-store", authorized.Header().Get("Cache-Control"))
	require.Contains(t, authorized.Body.String(), `"object":"relay.model_release_evidence"`)
	var payload struct {
		Models []struct {
			Status            string            `json:"status"`
			Routes            []json.RawMessage `json:"routes"`
			ProviderCostReady bool              `json:"provider_cost_ready"`
		} `json:"models"`
	}
	require.NoError(t, json.Unmarshal(authorized.Body.Bytes(), &payload))
	require.NotEmpty(t, payload.Models, "reviewed candidates remain visible even before a route is installed")
	for _, item := range payload.Models {
		require.Equal(t, "blocked", item.Status)
		require.Empty(t, item.Routes)
		require.False(t, item.ProviderCostReady)
	}
	require.NotContains(t, authorized.Body.String(), "api_key")
	require.NotContains(t, authorized.Body.String(), "upstream_token")
	require.NotContains(t, authorized.Body.String(), "credential_fingerprint")
}
