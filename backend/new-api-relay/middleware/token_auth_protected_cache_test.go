package middleware

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/alicebob/miniredis/v2"
	"github.com/gin-gonic/gin"
	"github.com/go-redis/redis/v8"
	"github.com/stretchr/testify/require"
)

func TestProtectedTokenAuthReadOnlyRejectsPreheatedOldServiceToken(t *testing.T) {
	previousDB := model.DB
	previousType := common.MainDatabaseType()
	previousRedisEnabled := common.RedisEnabled
	previousRedis := common.RDB
	previousCryptoSecret := common.CryptoSecret
	previousSQLitePath := common.SQLitePath
	common.SQLitePath = filepath.Join(t.TempDir(), "protected-token-auth.db") + "?_busy_timeout=30000"
	t.Setenv("SQL_DSN", "")
	require.NoError(t, os.Unsetenv("SQL_DSN"))
	t.Setenv("SQL_DSN_FILE", "")
	require.NoError(t, os.Unsetenv("SQL_DSN_FILE"))
	require.NoError(t, model.InitDB())
	require.NoError(t, model.DB.AutoMigrate(&model.User{}, &model.Token{}))
	server := miniredis.RunT(t)
	common.RedisEnabled = true
	common.RDB = redis.NewClient(&redis.Options{Addr: server.Addr()})
	common.CryptoSecret = "protected-token-auth-read-only-cache-test"
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Cleanup(func() {
		_ = common.RDB.Close()
		if sqlDB, sqlErr := model.DB.DB(); sqlErr == nil {
			_ = sqlDB.Close()
		}
		model.DB = previousDB
		common.SetMainDatabaseType(previousType)
		common.RedisEnabled = previousRedisEnabled
		common.RDB = previousRedis
		common.CryptoSecret = previousCryptoSecret
		common.SQLitePath = previousSQLitePath
	})

	user := model.User{
		Username: "protected-read-only-cache-user", Password: "password-placeholder",
		DisplayName: "Protected Read Only", Role: common.RoleCommonUser,
		Status: common.UserStatusEnabled, Group: "default", AuthVersion: 1,
		AffCode: "protected-read-only-cache-aff",
	}
	require.NoError(t, model.DB.Create(&user).Error)
	const (
		oldKey = "A1b2C3d4E5f6G7h8J9k0L1m2N3p4Q5r6S7t8U9v0W1x2Y3z4"
		newKey = "C7d8E9f0G1h2J3k4L5m6N7p8Q9r0S1t2U3v4W5x6Y7z8A9b0"
	)
	token := model.Token{
		UserId: user.Id, Key: oldKey, Name: "protected-read-only-cache-probe",
		Status: common.TokenStatusEnabled, ExpiredTime: -1, UnlimitedQuota: true,
	}
	require.NoError(t, model.DB.Create(&token).Error)
	cached := token
	cached.Clean()
	require.NoError(t, common.RedisHSetObj(
		"token:"+common.GenerateHMAC(oldKey), &cached, time.Hour,
	))
	require.NoError(t, model.DB.Model(&model.Token{}).Where("id = ?", token.Id).UpdateColumn("key", newKey).Error)
	require.True(t, server.Exists("token:"+common.GenerateHMAC(oldKey)), "the stale old-key cache must remain present for the regression")

	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.GET("/read-only", TokenAuthReadOnly(), func(c *gin.Context) {
		c.Status(http.StatusNoContent)
	})

	oldRequest := httptest.NewRequest(http.MethodGet, "/read-only", nil)
	oldRequest.Header.Set("Authorization", "Bearer sk-"+oldKey)
	oldResponse := httptest.NewRecorder()
	router.ServeHTTP(oldResponse, oldRequest)
	require.Equal(t, http.StatusUnauthorized, oldResponse.Code)

	newRequest := httptest.NewRequest(http.MethodGet, "/read-only", nil)
	newRequest.Header.Set("Authorization", "Bearer sk-"+newKey)
	newResponse := httptest.NewRecorder()
	router.ServeHTTP(newResponse, newRequest)
	require.Equal(t, http.StatusNoContent, newResponse.Code)
}
