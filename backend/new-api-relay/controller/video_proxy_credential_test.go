package controller

import (
	"crypto/sha256"
	"fmt"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/glebarez/sqlite"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

func TestVideoContentProviderCredentialUsesPinnedVersionAfterChannelRotation(t *testing.T) {
	originalDB := model.DB
	originalDatabaseType := common.MainDatabaseType()
	database, err := gorm.Open(
		sqlite.Open("file:video-provider-credential-"+uuid.NewString()+"?mode=memory&cache=shared"),
		&gorm.Config{Logger: logger.Default.LogMode(logger.Silent)},
	)
	require.NoError(t, err)
	model.DB = database
	common.SetMainDatabaseType(common.DatabaseTypeSQLite)
	t.Cleanup(func() {
		model.DB = originalDB
		common.SetMainDatabaseType(originalDatabaseType)
	})
	require.NoError(t, database.AutoMigrate(&model.ProviderCredentialVersion{}))

	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("APP_ENV", "")
	t.Setenv("DEPLOYMENT_ENV", "")
	t.Setenv("ENVIRONMENT", "")
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE", "")
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON", `{"schema_version":1,"active_key_id":"video-test-v1","keys":{"video-test-v1":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}}`)

	originalProviderKey := "provider-key-before-channel-rotation"
	digest := sha256.Sum256([]byte(originalProviderKey))
	keyIndex := 1
	task := &model.Task{
		TaskID:    "task_video_credential_rotation",
		ChannelId: 9101,
		PrivateData: model.TaskPrivateData{
			PinnedKeyIndex:       &keyIndex,
			PinnedKeyFingerprint: fmt.Sprintf("%x", digest[:]),
			TransientProviderKey: originalProviderKey,
		},
	}
	require.NoError(t, model.BindTaskProviderCredentialVersion(task, uuid.NewString()))

	rotatedChannel := &model.Channel{Id: task.ChannelId, Key: "provider-key-after-channel-rotation"}
	resolved, err := resolveVideoContentProviderCredential(rotatedChannel, task)
	require.NoError(t, err)
	assert.Equal(t, originalProviderKey, resolved)
}

func TestVideoContentProviderCredentialFailsClosedForPinnedIdentityWithoutVersion(t *testing.T) {
	keyIndex := 0
	task := &model.Task{
		ChannelId: 9102,
		PrivateData: model.TaskPrivateData{
			PinnedKeyIndex:       &keyIndex,
			PinnedKeyFingerprint: fmt.Sprintf("%x", sha256.Sum256([]byte("old-key"))),
		},
	}

	_, err := resolveVideoContentProviderCredential(&model.Channel{Id: task.ChannelId, Key: "rotated-key"}, task)
	assert.ErrorContains(t, err, "version is missing")
}

func TestVideoContentProviderCredentialKeepsUnpinnedNativeCompatibility(t *testing.T) {
	resolved, err := resolveVideoContentProviderCredential(
		&model.Channel{Id: 9103, Key: "native-channel-key"},
		&model.Task{ChannelId: 9103},
	)
	require.NoError(t, err)
	assert.Equal(t, "native-channel-key", resolved)
}
