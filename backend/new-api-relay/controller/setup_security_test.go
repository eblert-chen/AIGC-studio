package controller

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
	"github.com/glebarez/sqlite"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func TestPostSetupIsDisabledInProductionBeforeDatabaseAccess(t *testing.T) {
	t.Setenv("APP_ENV", "production")
	t.Setenv("DEPLOYMENT_ENV", "production")
	gin.SetMode(gin.TestMode)

	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest(
		http.MethodPost,
		"/api/setup",
		strings.NewReader(`{"username":"attacker","password":"password","confirmPassword":"password"}`),
	)
	context.Request.Header.Set("Content-Type", "application/json")

	PostSetup(context)

	require.Equal(t, http.StatusForbidden, recorder.Code)
	require.Contains(t, recorder.Body.String(), "PROTECTED_SETUP_DISABLED")
}

func TestPostSetupRejectsReservedServicePrincipalUsernameInDevelopment(t *testing.T) {
	previousDB := model.DB
	previousSetup := constant.Setup
	t.Cleanup(func() {
		model.DB = previousDB
		constant.Setup = previousSetup
	})
	t.Setenv("APP_ENV", "development")
	t.Setenv("DEPLOYMENT_ENV", "development")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
	constant.Setup = false
	database, err := gorm.Open(sqlite.Open("file:reserved-setup-"+uuid.NewString()+"?mode=memory&cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, database.AutoMigrate(&model.Setup{}, &model.User{}))
	model.DB = database

	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest(
		http.MethodPost,
		"/api/setup",
		strings.NewReader(`{"username":"RSVC_admin","password":"password","confirmPassword":"password"}`),
	)
	context.Request.Header.Set("Content-Type", "application/json")
	PostSetup(context)

	require.Equal(t, http.StatusOK, recorder.Code)
	require.Contains(t, recorder.Body.String(), `"success":false`)
	var userCount int64
	require.NoError(t, database.Model(&model.User{}).Count(&userCount).Error)
	require.Zero(t, userCount)
	var setupCount int64
	require.NoError(t, database.Model(&model.Setup{}).Count(&setupCount).Error)
	require.Zero(t, setupCount)
}

func TestPostSetupIsDisabledInProtectedStagingBeforeDatabaseAccess(t *testing.T) {
	previousDB := model.DB
	model.DB = nil
	t.Cleanup(func() { model.DB = previousDB })
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	gin.SetMode(gin.TestMode)

	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest(http.MethodPost, "/api/setup", strings.NewReader(`{"username":"attacker"}`))
	context.Request.Header.Set("Content-Type", "application/json")
	PostSetup(context)

	require.Equal(t, http.StatusForbidden, recorder.Code)
	require.Contains(t, recorder.Body.String(), "PROTECTED_SETUP_DISABLED")
}

func TestPlatformRelayReadyRejectsIncompleteProductionSetup(t *testing.T) {
	previousDB := model.DB
	previousRedisEnabled := common.RedisEnabled
	t.Cleanup(func() {
		model.DB = previousDB
		common.RedisEnabled = previousRedisEnabled
	})
	t.Setenv("APP_ENV", "production")
	t.Setenv("DEPLOYMENT_ENV", "production")
	t.Setenv("RELAY_COMPAT_ENABLED", "false")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	common.RedisEnabled = false

	database, err := gorm.Open(
		sqlite.Open("file:production-setup-ready-"+uuid.NewString()+"?mode=memory&cache=shared"),
		&gorm.Config{},
	)
	require.NoError(t, err)
	require.NoError(t, database.AutoMigrate(
		&model.Setup{},
		&model.User{},
		&model.PlatformArtifactUploadIntent{},
	))
	model.DB = database

	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest(http.MethodGet, "/health/ready", nil)
	PlatformRelayReady(context)

	require.Equal(t, http.StatusServiceUnavailable, recorder.Code)
	require.Contains(t, recorder.Body.String(), `"name":"production_setup"`)
	require.Contains(t, recorder.Body.String(), `"state":"unavailable"`)
}

func TestPlatformRelayReadyRejectsIncompleteProtectedStagingSetup(t *testing.T) {
	previousDB := model.DB
	previousRedisEnabled := common.RedisEnabled
	t.Cleanup(func() {
		model.DB = previousDB
		common.RedisEnabled = previousRedisEnabled
	})
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_COMPAT_ENABLED", "false")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	common.RedisEnabled = false

	database, err := gorm.Open(sqlite.Open("file:staging-setup-ready-"+uuid.NewString()+"?mode=memory&cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, database.AutoMigrate(&model.Setup{}, &model.User{}, &model.PlatformArtifactUploadIntent{}))
	model.DB = database

	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest(http.MethodGet, "/health/ready", nil)
	PlatformRelayReady(context)

	require.Equal(t, http.StatusServiceUnavailable, recorder.Code)
	require.Contains(t, recorder.Body.String(), `"name":"production_setup"`)
	require.Contains(t, recorder.Body.String(), `"state":"unavailable"`)
}
