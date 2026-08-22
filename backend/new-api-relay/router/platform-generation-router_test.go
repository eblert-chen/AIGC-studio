package router

import (
	"net/http"
	"net/http/httptest"
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

func TestPlatformModelCatalogDispatchUsesServiceAuthAndETag(t *testing.T) {
	previousDB := model.DB
	db, err := gorm.Open(sqlite.Open("file:platform_model_catalog_router?mode=memory&cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, db.AutoMigrate(
		&model.User{}, &model.Token{}, &model.UserSession{}, &model.AuthFlow{},
		&model.ExternalIdentityClaim{}, &model.PasskeyCredential{}, &model.UserOAuthBinding{},
		&model.TwoFA{}, &model.TwoFABackupCode{},
	))
	model.DB = db
	t.Cleanup(func() { model.DB = previousDB })
	servicePrincipalUser, err := service.BuildProtectedPlatformRelayServicePrincipalUser(
		"platform", "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30",
	)
	require.NoError(t, err)
	serviceUser := &servicePrincipalUser
	require.NoError(t, db.Create(serviceUser).Error)
	upstreamKey := strings.Repeat("T", 48)
	require.NoError(t, db.Create(&model.Token{
		UserId:         serviceUser.Id,
		Key:            upstreamKey,
		Name:           "platform-relay:platform:51bdf7c4-93a6-4b7c-a4a1-03f616a10f30",
		Status:         common.TokenStatusEnabled,
		ExpiredTime:    -1,
		UnlimitedQuota: true,
	}).Error)
	t.Setenv("APP_ENV", "development")
	t.Setenv("DEPLOYMENT_ENV", "development")
	t.Setenv("ENVIRONMENT", "development")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
	t.Setenv("RELAY_COMPAT_ENABLED", "true")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", "")
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", `{
		"platform": {
			"tenant_id": "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30",
			"api_key": "relay-secret-0123456789-abcdef-xyz",
			"upstream_token": "sk-`+upstreamKey+`"
		}
	}`)
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", `{
		"video-model": {
			"schema_version": 1,
			"modes": {
				"text_to_video": {
					"input_media_types": [],
					"supports_face": false,
					"required_resource_keys": [],
					"limits": {
						"max_prompt_length": 10000,
						"max_images": 0,
						"max_videos": 0,
						"max_audio": 0,
						"duration_seconds": [5],
						"aspect_ratios": ["16:9"],
						"resolutions": ["720p"],
						"output_counts": [1]
					}
				}
			}
		}
	}`)

	gin.SetMode(gin.TestMode)
	engine := gin.New()
	SetRelayRouter(engine)
	SetPlatformGenerationRouter(engine)

	request := httptest.NewRequest(http.MethodGet, "/v1/models", nil)
	request.Header.Set("X-Client-ID", "platform")
	request.Header.Set("X-API-Key", "relay-secret-0123456789-abcdef-xyz")
	request.Header.Set("X-Request-ID", "catalog-request")
	response := httptest.NewRecorder()
	engine.ServeHTTP(response, request)

	require.Equal(t, http.StatusOK, response.Code)
	var catalog dto.PlatformModelCatalog
	require.NoError(t, common.Unmarshal(response.Body.Bytes(), &catalog))
	require.Len(t, catalog.Data, 1)
	assert.Equal(t, "video-model", catalog.Data[0].ID)
	etag := response.Header().Get("ETag")
	assert.Equal(t, `"`+catalog.CatalogRevision+`"`, etag)
	assert.Equal(t, "catalog-request", response.Header().Get("X-Request-ID"))
	assert.Equal(t, "X-Client-ID, X-API-Key", response.Header().Get("Vary"))

	revalidation := httptest.NewRequest(http.MethodGet, "/v1/models", nil)
	revalidation.Header.Set("X-Client-ID", "platform")
	revalidation.Header.Set("X-API-Key", "relay-secret-0123456789-abcdef-xyz")
	revalidation.Header.Set("X-Request-ID", "catalog-revalidation")
	revalidation.Header.Set("If-None-Match", etag)
	notModified := httptest.NewRecorder()
	engine.ServeHTTP(notModified, revalidation)

	assert.Equal(t, http.StatusNotModified, notModified.Code)
	assert.Empty(t, notModified.Body.String())
	assert.Equal(t, etag, notModified.Header().Get("ETag"))
	assert.Equal(t, "catalog-revalidation", notModified.Header().Get("X-Request-ID"))

	// A protected deployment routes every model-catalog authentication attempt
	// through the Platform service-principal boundary; ordinary new-api bearer,
	// native x-api-key, and query-key credentials must never become a second
	// production data plane.
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	for name, mutate := range map[string]func(*http.Request){
		"bearer token": func(request *http.Request) {
			request.Header.Set("Authorization", "Bearer ordinary-token")
		},
		"native x-api-key": func(request *http.Request) {
			request.Header.Set("X-API-Key", "ordinary-token")
		},
		"query key": func(request *http.Request) {},
		"forged platform identity": func(request *http.Request) {
			request.Header.Set("X-Client-ID", "platform")
			request.Header.Set("X-API-Key", "wrong-secret")
		},
	} {
		t.Run(name, func(t *testing.T) {
			path := "/v1/models"
			if name == "query key" {
				path += "?key=ordinary-token"
			}
			request := httptest.NewRequest(http.MethodGet, path, nil)
			mutate(request)
			response := httptest.NewRecorder()
			engine.ServeHTTP(response, request)
			assert.Equal(t, http.StatusUnauthorized, response.Code)
		})
	}

	liveRequest := httptest.NewRequest(http.MethodGet, "/health/live", nil)
	liveRequest.Header.Set("X-Request-ID", "health-check")
	liveResponse := httptest.NewRecorder()
	engine.ServeHTTP(liveResponse, liveRequest)
	assert.Equal(t, http.StatusOK, liveResponse.Code)
	assert.Equal(t, "health-check", liveResponse.Header().Get("X-Request-ID"))
}
