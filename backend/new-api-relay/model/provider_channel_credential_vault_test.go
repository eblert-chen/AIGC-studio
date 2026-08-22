package model

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/glebarez/sqlite"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

const (
	providerChannelVaultKeyringA = `{"schema_version":1,"active_key_id":"channel-a","keys":{"channel-a":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}}`
	providerChannelVaultKeyringB = `{"schema_version":1,"active_key_id":"channel-b","keys":{"channel-a":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=","channel-b":"YWJjZGVmMDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODk="}}`
	providerChannelVaultOnlyB    = `{"schema_version":1,"active_key_id":"channel-b","keys":{"channel-b":"YWJjZGVmMDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODk="}}`
)

func setupProviderChannelVaultSQLite(t *testing.T, installGuards bool) *gorm.DB {
	t.Helper()
	originalDB := DB
	database, err := gorm.Open(sqlite.Open("file:provider-channel-vault-"+uuid.NewString()+"?mode=memory&cache=shared"), &gorm.Config{
		Logger: logger.Default.LogMode(logger.Silent),
	})
	require.NoError(t, err)
	DB = database
	common.SetMainDatabaseType(common.DatabaseTypeSQLite)
	t.Setenv(providerCredentialKeyringFileEnvironment, "")
	t.Setenv(providerCredentialKeyringJSONEnvironment, providerChannelVaultKeyringA)
	require.NoError(t, DB.AutoMigrate(&Channel{}, &ProviderChannelCredentialSetVersion{}, &ProviderCredentialVersion{}, &Ability{}))
	if installGuards {
		require.NoError(t, MigrateProviderChannelCredentialVaultStorage())
	}
	t.Cleanup(func() {
		DB = originalDB
		common.SetMainDatabaseType(common.DatabaseTypeSQLite)
	})
	return database
}

func loadProviderChannelVaultRow(t *testing.T, version string) ProviderChannelCredentialSetVersion {
	t.Helper()
	var row ProviderChannelCredentialSetVersion
	require.NoError(t, DB.Session(&gorm.Session{SkipHooks: true}).Where("credential_set_version = ?", version).First(&row).Error)
	return row
}

func TestProviderChannelCredentialVaultNativeCRUDPreservesExactMultiKeyBytes(t *testing.T) {
	setupProviderChannelVaultSQLite(t, true)
	raw := "first-key\n{\"type\":\"service_account\",\"private_key\":\"second-key\"}\nthird-key"
	channel := &Channel{
		Id:     9101,
		Name:   "encrypted-multi-key",
		Key:    raw,
		Status: common.ChannelStatusEnabled,
		ChannelInfo: ChannelInfo{
			IsMultiKey:   true,
			MultiKeySize: 3,
		},
	}
	require.NoError(t, DB.Create(channel).Error)
	require.NotEmpty(t, channel.CredentialSetVersion)

	var stored struct {
		LegacyKey            string `gorm:"column:key"`
		CredentialSetVersion string
	}
	require.NoError(t, DB.Session(&gorm.Session{SkipHooks: true}).Table("channels").
		Select("key, credential_set_version").Where("id = ?", channel.Id).Take(&stored).Error)
	assert.Empty(t, stored.LegacyKey)
	assert.Equal(t, channel.CredentialSetVersion, stored.CredentialSetVersion)

	row := loadProviderChannelVaultRow(t, stored.CredentialSetVersion)
	assert.Equal(t, 3, row.KeyCount)
	assert.NotContains(t, string(row.Ciphertext), "first-key")
	assert.NotContains(t, string(row.Ciphertext), "second-key")

	secretFree, err := GetChannelById(channel.Id, false)
	require.NoError(t, err)
	assert.Empty(t, secretFree.Key)
	assert.Empty(t, secretFree.CredentialSetVersion)

	hydrated, err := GetChannelById(channel.Id, true)
	require.NoError(t, err)
	assert.Equal(t, raw, hydrated.Key)
	assert.Equal(t, []string{"first-key", `{"type":"service_account","private_key":"second-key"}`, "third-key"}, hydrated.GetKeys())
	encoded, err := json.Marshal(hydrated)
	require.NoError(t, err)
	assert.NotContains(t, string(encoded), "first-key")
	assert.NotContains(t, string(encoded), hydrated.CredentialSetVersion)
	assert.NotContains(t, string(encoded), "ciphertext")

	initialVersion := hydrated.CredentialSetVersion
	hydrated.Key = ""
	hydrated.Name = "metadata-only-update"
	require.NoError(t, hydrated.Update())
	assert.Equal(t, initialVersion, hydrated.CredentialSetVersion)

	require.NoError(t, RotateChannelCredentialSet(channel.Id, raw))
	unchanged, err := GetChannelById(channel.Id, true)
	require.NoError(t, err)
	assert.Equal(t, initialVersion, unchanged.CredentialSetVersion)

	rotatedRaw := raw + "\nfourth-key"
	require.NoError(t, RotateChannelCredentialSet(channel.Id, rotatedRaw))
	rotated, err := GetChannelById(channel.Id, true)
	require.NoError(t, err)
	assert.NotEqual(t, initialVersion, rotated.CredentialSetVersion)
	assert.Equal(t, rotatedRaw, rotated.Key)

	oldRow := loadProviderChannelVaultRow(t, initialVersion)
	oldPlaintext, err := resolveProviderChannelCredentialSetVersionRow(oldRow)
	require.NoError(t, err)
	assert.Equal(t, raw, string(oldPlaintext))
	clear(oldPlaintext)
}

func TestProviderChannelCredentialVaultChannelUpdateRotatesAtomicallyAndKeepsPinnedTaskResolvable(t *testing.T) {
	setupProviderChannelVaultSQLite(t, true)
	const oldRaw = "provider-key-pinned-before-native-update"
	const rotatedRaw = "provider-key-installed-by-native-update"
	channel := &Channel{Id: 9107, Name: "native-update", Key: oldRaw, Status: common.ChannelStatusEnabled}
	require.NoError(t, DB.Create(channel).Error)
	oldChannelVersion := channel.CredentialSetVersion

	keyIndex := 0
	task := &Task{
		TaskID:    GenerateTaskID(),
		ChannelId: channel.Id,
		PrivateData: TaskPrivateData{
			PinnedKeyIndex:       &keyIndex,
			PinnedKeyFingerprint: providerChannelCredentialFingerprint(oldRaw),
			TransientProviderKey: oldRaw,
		},
	}
	require.NoError(t, BindTaskProviderCredentialVersion(task, uuid.NewString()))
	pinnedTaskVersion := task.PrivateData.ProviderCredentialVersion
	require.NotEmpty(t, pinnedTaskVersion)

	editable, err := GetChannelById(channel.Id, true)
	require.NoError(t, err)
	editable.Key = rotatedRaw
	require.NoError(t, editable.Update())

	rotated, err := GetChannelById(channel.Id, true)
	require.NoError(t, err)
	assert.Equal(t, rotatedRaw, rotated.Key)
	assert.NotEqual(t, oldChannelVersion, rotated.CredentialSetVersion)

	oldChannelRow := loadProviderChannelVaultRow(t, oldChannelVersion)
	oldChannelPlaintext, err := resolveProviderChannelCredentialSetVersionRow(oldChannelRow)
	require.NoError(t, err)
	assert.Equal(t, oldRaw, string(oldChannelPlaintext))
	clear(oldChannelPlaintext)

	resolvedPinnedTask, err := ResolveTaskProviderCredential(task)
	require.NoError(t, err)
	assert.Equal(t, oldRaw, resolvedPinnedTask)
	assert.Equal(t, pinnedTaskVersion, task.PrivateData.ProviderCredentialVersion)
}

func TestProviderChannelCredentialVaultChannelUpdateEmptyKeyPreservesVersion(t *testing.T) {
	setupProviderChannelVaultSQLite(t, true)
	channel := &Channel{Id: 9108, Name: "empty-key-preserves", Key: "provider-key-to-preserve", Status: common.ChannelStatusEnabled}
	require.NoError(t, DB.Create(channel).Error)
	originalVersion := channel.CredentialSetVersion

	editable, err := GetChannelById(channel.Id, true)
	require.NoError(t, err)
	editable.Key = ""
	editable.Name = "metadata-only"
	require.NoError(t, editable.Update())

	updated, err := GetChannelById(channel.Id, true)
	require.NoError(t, err)
	assert.Equal(t, "provider-key-to-preserve", updated.Key)
	assert.Equal(t, originalVersion, updated.CredentialSetVersion)
}

func TestProviderChannelCredentialVaultChannelUpdateFailureRollsBackVersionAndReference(t *testing.T) {
	setupProviderChannelVaultSQLite(t, true)
	channel := &Channel{Id: 9109, Name: "rollback-update", Key: "provider-key-before-failure", Status: common.ChannelStatusEnabled}
	require.NoError(t, DB.Create(channel).Error)
	originalVersion := channel.CredentialSetVersion
	var versionsBefore int64
	require.NoError(t, DB.Model(&ProviderChannelCredentialSetVersion{}).Count(&versionsBefore).Error)

	const callbackName = "test:fail-channel-update-after-credential-version"
	require.NoError(t, DB.Callback().Update().After("gorm:before_update").Before("gorm:update").Register(callbackName, func(tx *gorm.DB) {
		if tx.Statement != nil && tx.Statement.Schema != nil && tx.Statement.Schema.Name == "Channel" {
			tx.AddError(errors.New("injected channel update failure"))
		}
	}))
	t.Cleanup(func() { _ = DB.Callback().Update().Remove(callbackName) })

	editable, err := GetChannelById(channel.Id, true)
	require.NoError(t, err)
	editable.Key = "provider-key-that-must-roll-back"
	err = editable.Update()
	require.ErrorContains(t, err, "injected channel update failure")
	require.NoError(t, DB.Callback().Update().Remove(callbackName))

	updated, err := GetChannelById(channel.Id, true)
	require.NoError(t, err)
	assert.Equal(t, "provider-key-before-failure", updated.Key)
	assert.Equal(t, originalVersion, updated.CredentialSetVersion)
	var versionsAfter int64
	require.NoError(t, DB.Model(&ProviderChannelCredentialSetVersion{}).Count(&versionsAfter).Error)
	assert.Equal(t, versionsBefore, versionsAfter)
}

func TestProviderChannelCredentialVaultKEKRotationCreatesNewVersionAndFailsClosedWithoutOldKEK(t *testing.T) {
	setupProviderChannelVaultSQLite(t, true)
	channel := &Channel{Id: 9102, Name: "rotation", Key: "same-provider-key", Status: common.ChannelStatusEnabled}
	require.NoError(t, DB.Create(channel).Error)
	oldVersion := channel.CredentialSetVersion
	assert.Equal(t, "channel-a", loadProviderChannelVaultRow(t, oldVersion).KeyID)

	t.Setenv(providerCredentialKeyringJSONEnvironment, providerChannelVaultKeyringB)
	require.NoError(t, RotateChannelCredentialSet(channel.Id, "same-provider-key"))
	current, err := GetChannelById(channel.Id, true)
	require.NoError(t, err)
	assert.NotEqual(t, oldVersion, current.CredentialSetVersion)
	assert.Equal(t, "channel-b", loadProviderChannelVaultRow(t, current.CredentialSetVersion).KeyID)

	oldPlaintext, err := resolveProviderChannelCredentialSetVersionRow(loadProviderChannelVaultRow(t, oldVersion))
	require.NoError(t, err)
	assert.Equal(t, "same-provider-key", string(oldPlaintext))
	clear(oldPlaintext)

	t.Setenv(providerCredentialKeyringJSONEnvironment, providerChannelVaultOnlyB)
	_, err = resolveProviderChannelCredentialSetVersionRow(loadProviderChannelVaultRow(t, oldVersion))
	assert.ErrorContains(t, err, "KEK is unavailable")
	current, err = GetChannelById(channel.Id, true)
	require.NoError(t, err)
	assert.Equal(t, "same-provider-key", current.Key)
}

func TestProviderChannelCredentialVaultSQLiteMigrationAndGuards(t *testing.T) {
	setupProviderChannelVaultSQLite(t, false)
	raw := `[{"token":"first"},{"token":"second"}]`
	require.NoError(t, DB.Session(&gorm.Session{SkipHooks: true}).Table("channels").Create(map[string]any{
		"id": 9103, "name": "legacy", "key": raw,
	}).Error)
	require.NoError(t, MigrateProviderChannelCredentialVaultStorage())

	channel, err := GetChannelById(9103, true)
	require.NoError(t, err)
	assert.Equal(t, raw, channel.Key)
	assert.NotEmpty(t, channel.CredentialSetVersion)
	var legacyKey string
	require.NoError(t, DB.Session(&gorm.Session{SkipHooks: true}).Table("channels").Where("id = ?", 9103).Pluck("key", &legacyKey).Error)
	assert.Empty(t, legacyKey)

	assert.Error(t, DB.Session(&gorm.Session{SkipHooks: true}).Table("channels").Where("id = ?", 9103).Update("key", "plaintext-rewrite").Error)
	assert.Error(t, DB.Session(&gorm.Session{SkipHooks: true}).Table("channels").Create(map[string]any{
		"id": 9104, "name": "old-writer", "key": "plaintext-insert",
	}).Error)
	row := loadProviderChannelVaultRow(t, channel.CredentialSetVersion)
	assert.Error(t, DB.Model(&ProviderChannelCredentialSetVersion{}).
		Where("credential_set_version = ?", row.CredentialSetVersion).Update("key_id", "tampered").Error)
	assert.Error(t, DB.Where("credential_set_version = ?", row.CredentialSetVersion).
		Delete(&ProviderChannelCredentialSetVersion{}).Error)
}

func TestProviderChannelCredentialVaultMigrationFailureRollsBackLegacyRow(t *testing.T) {
	setupProviderChannelVaultSQLite(t, false)
	require.NoError(t, DB.Session(&gorm.Session{SkipHooks: true}).Table("channels").Create(map[string]any{
		"id": 9105, "name": "rollback", "key": "must-remain-on-failure",
	}).Error)
	t.Setenv(providerCredentialKeyringJSONEnvironment, "")
	err := MigrateProviderChannelCredentialVaultStorage()
	require.Error(t, err)

	var stored legacyProviderChannelCredentialRow
	require.NoError(t, DB.Session(&gorm.Session{SkipHooks: true}).Table("channels").
		Select("id, key, credential_set_version").Where("id = ?", 9105).Take(&stored).Error)
	assert.Equal(t, "must-remain-on-failure", stored.LegacyKey)
	assert.Empty(t, stored.CredentialSetVersion)
	var count int64
	require.NoError(t, DB.Model(&ProviderChannelCredentialSetVersion{}).Count(&count).Error)
	assert.Zero(t, count)
}

func TestProviderChannelCredentialVaultMemoryCacheNeverRetainsOrSharesPlaintext(t *testing.T) {
	setupProviderChannelVaultSQLite(t, true)
	originalMemoryCache := common.MemoryCacheEnabled
	common.MemoryCacheEnabled = true
	t.Cleanup(func() {
		common.MemoryCacheEnabled = originalMemoryCache
		channelSyncLock.Lock()
		channelsIDM = nil
		group2model2channels = nil
		channel2advancedCustomConfig = nil
		channelSyncLock.Unlock()
	})
	channel := &Channel{
		Id: 9106, Name: "cached", Key: "cache-must-not-retain-this-key",
		Status: common.ChannelStatusEnabled, Group: "default", Models: "cache-model",
	}
	require.NoError(t, DB.Create(channel).Error)
	require.NoError(t, channel.AddAbilities(nil))
	InitChannelCache()

	channelSyncLock.RLock()
	cached := channelsIDM[channel.Id]
	require.NotNil(t, cached)
	assert.Empty(t, cached.Key)
	assert.Empty(t, cached.LegacyKey)
	assert.NotEmpty(t, cached.CredentialSetVersion)
	encoded, err := json.Marshal(cached)
	channelSyncLock.RUnlock()
	require.NoError(t, err)
	assert.NotContains(t, string(encoded), "cache-must-not-retain-this-key")
	assert.NotContains(t, string(encoded), cached.CredentialSetVersion)

	first, err := CacheGetChannel(channel.Id)
	require.NoError(t, err)
	assert.Equal(t, "cache-must-not-retain-this-key", first.Key)
	first.Name = "caller-mutated"
	first.ChannelInfo.MultiKeyStatusList = map[int]int{0: 2}
	second, err := CacheGetChannel(channel.Id)
	require.NoError(t, err)
	assert.Equal(t, "cached", second.Name)
	assert.Nil(t, second.ChannelInfo.MultiKeyStatusList)
	assert.NotSame(t, first, second)

	selected, err := GetRandomSatisfiedChannel("default", "cache-model", 0, "")
	require.NoError(t, err)
	require.NotNil(t, selected)
	assert.Equal(t, "cache-must-not-retain-this-key", selected.Key)
	channelSyncLock.RLock()
	assert.Empty(t, channelsIDM[channel.Id].Key)
	channelSyncLock.RUnlock()
}

func TestProviderChannelCredentialFingerprintPrefixNeverContainsRawPrefix(t *testing.T) {
	raw := "sk-live-obvious-prefix"
	prefix := ProviderChannelCredentialFingerprintPrefix(raw, 12)
	assert.Len(t, prefix, 12)
	assert.False(t, strings.Contains(prefix, "sk-live"))
}
