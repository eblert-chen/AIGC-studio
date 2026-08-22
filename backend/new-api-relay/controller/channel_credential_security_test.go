package controller

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
	"github.com/glebarez/sqlite"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func setupChannelCredentialControllerTest(t *testing.T) *gorm.DB {
	t.Helper()
	previousDB := model.DB
	previousLogDB := model.LOG_DB
	previousMemoryCacheEnabled := common.MemoryCacheEnabled
	previousRedisEnabled := common.RedisEnabled
	previousMainDatabaseType := common.MainDatabaseType()
	previousLogDatabaseType := common.LogDatabaseType()

	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE", "")
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON", `{"schema_version":1,"active_key_id":"controller-channel-v1","keys":{"controller-channel-v1":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}}`)
	common.SetDatabaseTypes(common.DatabaseTypeSQLite, common.DatabaseTypeSQLite)
	common.MemoryCacheEnabled = false
	common.RedisEnabled = false
	gin.SetMode(gin.TestMode)

	dsn := fmt.Sprintf("file:%s?mode=memory&cache=shared", strings.ReplaceAll(t.Name(), "/", "_"))
	db, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, db.AutoMigrate(
		&model.User{},
		&model.Log{},
		&model.Channel{},
		&model.ProviderChannelCredentialSetVersion{},
		&model.Ability{},
	))
	model.DB = db
	model.LOG_DB = db
	require.NoError(t, model.MigrateProviderChannelCredentialVaultStorage())

	t.Cleanup(func() {
		model.DB = previousDB
		model.LOG_DB = previousLogDB
		common.MemoryCacheEnabled = previousMemoryCacheEnabled
		common.RedisEnabled = previousRedisEnabled
		common.SetDatabaseTypes(previousMainDatabaseType, previousLogDatabaseType)
		sqlDB, err := db.DB()
		if err == nil {
			require.NoError(t, sqlDB.Close())
		}
	})
	return db
}

func TestChannelCredentialControllerEncryptsAtRestAndLimitsPlaintextBoundary(t *testing.T) {
	db := setupChannelCredentialControllerTest(t)
	const rawKey = "provider-secret-never-list-this"

	addBody := []byte(`{
		"mode":"single",
		"channel":{
			"type":1,
			"name":"encrypted-controller-channel",
			"key":"provider-secret-never-list-this",
			"status":1,
			"models":"gpt-test",
			"group":"default"
		}
	}`)
	addRecorder := httptest.NewRecorder()
	addContext, _ := gin.CreateTestContext(addRecorder)
	addContext.Request = httptest.NewRequest(http.MethodPost, "/api/channel", bytes.NewReader(addBody))
	addContext.Request.Header.Set("Content-Type", "application/json")
	AddChannel(addContext)
	require.Equal(t, http.StatusOK, addRecorder.Code)
	var addResponse struct {
		Success bool `json:"success"`
	}
	require.NoError(t, json.Unmarshal(addRecorder.Body.Bytes(), &addResponse))
	require.True(t, addResponse.Success, addRecorder.Body.String())

	var stored struct {
		ID                   int
		LegacyKey            string `gorm:"column:key"`
		CredentialSetVersion string
	}
	require.NoError(t, db.Table("channels").
		Select("id, key, credential_set_version").
		Where("name = ?", "encrypted-controller-channel").
		Take(&stored).Error)
	assert.Empty(t, stored.LegacyKey)
	require.NotEmpty(t, stored.CredentialSetVersion)

	var encrypted model.ProviderChannelCredentialSetVersion
	require.NoError(t, db.Session(&gorm.Session{SkipHooks: true}).
		First(&encrypted, "credential_set_version = ?", stored.CredentialSetVersion).Error)
	assert.NotContains(t, string(encrypted.Ciphertext), rawKey)

	detailRecorder := httptest.NewRecorder()
	detailContext, _ := gin.CreateTestContext(detailRecorder)
	detailContext.Request = httptest.NewRequest(http.MethodGet, fmt.Sprintf("/api/channel/%d", stored.ID), nil)
	detailContext.Params = gin.Params{{Key: "id", Value: fmt.Sprintf("%d", stored.ID)}}
	GetChannel(detailContext)
	require.Equal(t, http.StatusOK, detailRecorder.Code)
	assert.NotContains(t, detailRecorder.Body.String(), rawKey)
	assert.NotContains(t, detailRecorder.Body.String(), "credential_set_version")
	assert.NotContains(t, detailRecorder.Body.String(), "ciphertext")

	listRecorder := httptest.NewRecorder()
	listContext, _ := gin.CreateTestContext(listRecorder)
	listContext.Request = httptest.NewRequest(http.MethodGet, "/api/channel?page=1&page_size=20", nil)
	GetAllChannels(listContext)
	require.Equal(t, http.StatusOK, listRecorder.Code)
	assert.NotContains(t, listRecorder.Body.String(), rawKey)
	assert.NotContains(t, listRecorder.Body.String(), "credential_set_version")
	assert.NotContains(t, listRecorder.Body.String(), "ciphertext")

	keyRecorder := httptest.NewRecorder()
	keyContext, _ := gin.CreateTestContext(keyRecorder)
	keyContext.Request = httptest.NewRequest(http.MethodGet, fmt.Sprintf("/api/channel/%d/key", stored.ID), nil)
	keyContext.Params = gin.Params{{Key: "id", Value: fmt.Sprintf("%d", stored.ID)}}
	keyContext.Set("id", 0)
	GetChannelKey(keyContext)
	require.Equal(t, http.StatusOK, keyRecorder.Code)
	assert.Equal(t, "no-store, max-age=0", keyRecorder.Header().Get("Cache-Control"))
	assert.Equal(t, "no-cache", keyRecorder.Header().Get("Pragma"))
	assert.Contains(t, keyRecorder.Body.String(), rawKey)
	assert.NotContains(t, keyRecorder.Body.String(), stored.CredentialSetVersion)
	assert.NotContains(t, keyRecorder.Body.String(), "ciphertext")
}

func TestChannelCredentialControllerUpdateRotatesAndEmptyKeyPreserves(t *testing.T) {
	db := setupChannelCredentialControllerTest(t)
	channel := &model.Channel{
		Type:   1,
		Name:   "controller-update-channel",
		Key:    "controller-provider-key-before-update",
		Status: common.ChannelStatusEnabled,
		Models: "gpt-test",
		Group:  "default",
	}
	require.NoError(t, channel.Insert())
	originalVersion := channel.CredentialSetVersion

	update := func(key string) *httptest.ResponseRecorder {
		body, err := json.Marshal(map[string]any{"id": channel.Id, "key": key})
		require.NoError(t, err)
		recorder := httptest.NewRecorder()
		ctx, _ := gin.CreateTestContext(recorder)
		ctx.Request = httptest.NewRequest(http.MethodPut, "/api/channel", bytes.NewReader(body))
		ctx.Request.Header.Set("Content-Type", "application/json")
		ctx.Set("id", 1)
		ctx.Set("role", common.RoleRootUser)
		UpdateChannel(ctx)
		return recorder
	}

	rotatedRecorder := update("controller-provider-key-after-update")
	require.Equal(t, http.StatusOK, rotatedRecorder.Code)
	var rotatedResponse struct {
		Success bool `json:"success"`
	}
	require.NoError(t, json.Unmarshal(rotatedRecorder.Body.Bytes(), &rotatedResponse))
	require.True(t, rotatedResponse.Success, rotatedRecorder.Body.String())
	assert.NotContains(t, rotatedRecorder.Body.String(), "controller-provider-key-after-update")
	assert.NotContains(t, rotatedRecorder.Body.String(), "credential_set_version")

	rotated, err := model.GetChannelById(channel.Id, true)
	require.NoError(t, err)
	assert.Equal(t, "controller-provider-key-after-update", rotated.Key)
	assert.NotEqual(t, originalVersion, rotated.CredentialSetVersion)
	rotatedVersion := rotated.CredentialSetVersion

	emptyRecorder := update("")
	require.Equal(t, http.StatusOK, emptyRecorder.Code)
	var emptyResponse struct {
		Success bool `json:"success"`
	}
	require.NoError(t, json.Unmarshal(emptyRecorder.Body.Bytes(), &emptyResponse))
	require.True(t, emptyResponse.Success, emptyRecorder.Body.String())

	preserved, err := model.GetChannelById(channel.Id, true)
	require.NoError(t, err)
	assert.Equal(t, "controller-provider-key-after-update", preserved.Key)
	assert.Equal(t, rotatedVersion, preserved.CredentialSetVersion)

	var legacy string
	require.NoError(t, db.Table("channels").Where("id = ?", channel.Id).Pluck("key", &legacy).Error)
	assert.Empty(t, legacy)
}
