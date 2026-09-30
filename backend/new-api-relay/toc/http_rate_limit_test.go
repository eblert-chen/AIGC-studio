package toc

import (
	"bytes"
	"image"
	"image/png"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/bytedance/gopkg/util/gopool"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestTOCAPIRateLimitCoversIndependentGroupsAndPreservesSignedMedia(t *testing.T) {
	s, _ := fixture(t)
	previousDB, previousLog, previousSecret := model.DB, model.LOG_DB, common.SessionSecret
	previousRedis := common.RedisEnabled
	previousEnabled, previousNum, previousDuration := common.GlobalApiRateLimitEnable, common.GlobalApiRateLimitNum, common.GlobalApiRateLimitDuration
	previousMain, previousLogType := common.MainDatabaseType(), common.LogDatabaseType()
	model.DB, model.LOG_DB = s.DB, s.DB
	common.SessionSecret = strings.Repeat("rate-limit-session-secret-", 2)
	common.RedisEnabled = false
	common.GlobalApiRateLimitEnable, common.GlobalApiRateLimitNum, common.GlobalApiRateLimitDuration = true, 4, 60
	common.SetDatabaseTypes(common.DatabaseTypeSQLite, common.DatabaseTypeSQLite)
	t.Cleanup(func() {
		require.Eventually(t, func() bool { return gopool.WorkerCount() == 0 }, 3*time.Second, time.Millisecond)
		model.DB, model.LOG_DB, common.SessionSecret = previousDB, previousLog, previousSecret
		common.RedisEnabled = previousRedis
		common.GlobalApiRateLimitEnable, common.GlobalApiRateLimitNum, common.GlobalApiRateLimitDuration = previousEnabled, previousNum, previousDuration
		common.SetDatabaseTypes(previousMain, previousLogType)
	})
	t.Setenv("RELAY_RUNTIME_PROFILE", "toc")
	t.Setenv("TOC_CLIENT_ORIGIN", "http://127.0.0.1:14340")
	tokens := map[int]string{}
	for _, userID := range []int{1, 2} {
		session := model.UserSession{SID: uuid.NewString(), UserID: userID, Version: 1, UserAuthVersion: 1, Status: model.UserSessionStatusActive, RefreshHash: uuid.NewString(), LoginMethod: "password", LastActiveAt: time.Now().Unix(), ExpiresAt: time.Now().Add(time.Hour).Unix()}
		require.NoError(t, model.CreateUserSession(&session))
		token, _, err := service.IssueAccessToken(service.AuthIdentity{UserID: userID, SessionID: session.SID, UserAuthVersion: 1, SessionVersion: 1})
		require.NoError(t, err)
		tokens[userID] = token
	}
	var pngBytes bytes.Buffer
	require.NoError(t, png.Encode(&pngBytes, image.NewRGBA(image.Rect(0, 0, 2, 2))))
	asset, err := s.Upload(2, uuid.NewString(), "limit-reference.png", "image", "", pngBytes.Bytes())
	require.NoError(t, err)
	access, err := s.AssetAccess(2, asset.ID)
	require.NoError(t, err)
	signed, err := url.Parse(access["url"].(string))
	require.NoError(t, err)
	gin.SetMode(gin.TestMode)
	engine := gin.New()
	require.NoError(t, engine.SetTrustedProxies(nil))
	RegisterRoutes(engine, s)
	server := httptest.NewServer(engine)
	t.Cleanup(server.Close)
	client := server.Client()
	call := func(method, path, token string) (int, http.Header, []byte) {
		request, requestErr := http.NewRequest(method, server.URL+path, nil)
		require.NoError(t, requestErr)
		if token != "" {
			request.Header.Set("Authorization", "Bearer "+token)
		}
		request.Header.Set("Origin", "http://127.0.0.1:14340")
		response, requestErr := client.Do(request)
		require.NoError(t, requestErr)
		body, readErr := io.ReadAll(response.Body)
		require.NoError(t, response.Body.Close())
		require.NoError(t, readErr)
		return response.StatusCode, response.Header, body
	}
	// Authentication failures, personal APIs, Root APIs and session surfaces
	// consume the same existing per-IP API budget, exactly once per request.
	for _, check := range []struct {
		path   string
		token  string
		status int
	}{
		{"/api/v1/personal/me", "", http.StatusUnauthorized},
		{"/api/v1/personal/me", tokens[2], http.StatusOK},
		{"/api/v1/toc-admin/config", tokens[1], http.StatusOK},
		{"/api/v1/session/surfaces", tokens[2], http.StatusOK},
	} {
		status, _, _ := call(http.MethodGet, check.path, check.token)
		require.Equal(t, check.status, status, check.path)
	}
	for _, check := range []struct{ method, path, token string }{
		{http.MethodPost, "/api/v1/personal/assets", tokens[2]},
		{http.MethodGet, "/api/v1/toc-admin/config", tokens[1]},
		{http.MethodGet, "/api/v1/personal/me", ""},
	} {
		status, headers, _ := call(check.method, check.path, check.token)
		require.Equal(t, http.StatusTooManyRequests, status)
		require.Equal(t, "60", headers.Get("Retry-After"))
	}
	var assets int64
	require.NoError(t, s.DB.Model(&Asset{}).Count(&assets).Error)
	require.EqualValues(t, 1, assets, "rate-limited upload cannot create another asset")
	status, headers, raw := call(http.MethodGet, signed.RequestURI(), "")
	require.Equal(t, http.StatusOK, status, "provider signed-media retrieval keeps its separate lease semantics")
	require.Equal(t, "image/png", headers.Get("Content-Type"))
	require.Equal(t, pngBytes.Bytes(), raw)
	query := signed.Query()
	query.Set("signature", strings.Repeat("0", 64))
	signed.RawQuery = query.Encode()
	status, _, _ = call(http.MethodGet, signed.RequestURI(), "")
	require.Equal(t, http.StatusForbidden, status, "API budget must not replace signature validation")

	// The existing explicit off switch remains effective at route registration.
	common.GlobalApiRateLimitEnable = false
	disabledEngine := gin.New()
	RegisterRoutes(disabledEngine, s)
	disabledServer := httptest.NewServer(disabledEngine)
	t.Cleanup(disabledServer.Close)
	for i := 0; i < 2; i++ {
		request, requestErr := http.NewRequest(http.MethodGet, disabledServer.URL+"/api/v1/personal/me", nil)
		require.NoError(t, requestErr)
		request.Header.Set("Authorization", "Bearer "+tokens[2])
		response, requestErr := disabledServer.Client().Do(request)
		require.NoError(t, requestErr)
		require.Equal(t, http.StatusOK, response.StatusCode)
		require.NoError(t, response.Body.Close())
	}
}

func TestTOCRateLimitedRoutesRemainAbsentFromEnterprise(t *testing.T) {
	t.Setenv("RELAY_RUNTIME_PROFILE", "enterprise")
	engine := gin.New()
	RegisterRoutes(engine, &Service{})
	for _, path := range []string{"/api/v1/personal/me", "/api/v1/toc-admin/config", "/api/v1/session/surfaces"} {
		response := httptest.NewRecorder()
		engine.ServeHTTP(response, httptest.NewRequest(http.MethodGet, path, nil))
		require.Equal(t, http.StatusNotFound, response.Code, path)
	}
}
