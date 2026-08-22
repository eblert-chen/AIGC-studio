package model

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/glebarez/sqlite"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

const providerCredentialTestKeyringJSON = `{"schema_version":1,"active_key_id":"test-v1","keys":{"test-v1":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}}`

func useProviderCredentialTestFileReader(t *testing.T) {
	t.Helper()
	original := readProviderCredentialKeyringFile
	readProviderCredentialKeyringFile = func(environment string, maximumBytes int64) ([]byte, error) {
		return os.ReadFile(os.Getenv(environment))
	}
	t.Cleanup(func() { readProviderCredentialKeyringFile = original })
}

func TestProviderCredentialVersionEncryptsAndAuthenticatesTaskIdentity(t *testing.T) {
	require.NoError(t, DB.AutoMigrate(&ProviderCredentialVersion{}))
	tenantID := uuid.NewString()
	providerKey := "provider-secret-that-must-not-be-persisted"
	digest := sha256.Sum256([]byte(providerKey))
	keyIndex := 2
	task := &Task{
		TaskID:    GenerateTaskID(),
		ChannelId: 8101,
		PrivateData: TaskPrivateData{
			PinnedKeyIndex:       &keyIndex,
			PinnedKeyFingerprint: fmt.Sprintf("%x", digest[:]),
			TransientProviderKey: providerKey,
		},
	}

	require.Error(t, DB.Create(task).Error, "transient plaintext must fail before SQL persistence")
	require.NoError(t, BindTaskProviderCredentialVersion(task, tenantID))
	assert.Empty(t, task.PrivateData.TransientProviderKey)
	assert.Equal(t, tenantID, task.PrivateData.ProviderCredentialTenantID)
	require.Regexp(t, `^[0-9a-f-]{36}$`, task.PrivateData.ProviderCredentialVersion)

	var row ProviderCredentialVersion
	require.NoError(t, DB.First(&row, "credential_version = ?", task.PrivateData.ProviderCredentialVersion).Error)
	assert.Equal(t, "test-v1", row.KeyID)
	assert.Equal(t, providerCredentialEncryptionVersion, row.Version)
	assert.Len(t, row.Nonce, 12)
	assert.NotContains(t, string(row.Ciphertext), providerKey)
	serialized, err := task.PrivateData.Value()
	require.NoError(t, err)
	assert.NotContains(t, fmt.Sprint(serialized), providerKey)
	assert.NotContains(t, fmt.Sprint(serialized), `"key"`)

	resolved, err := ResolveTaskProviderCredential(task)
	require.NoError(t, err)
	assert.Equal(t, providerKey, resolved)

	tampered := row
	tampered.TenantID = uuid.NewString()
	_, err = resolveProviderCredentialVersionRow(tampered)
	assert.ErrorContains(t, err, "authentication failed", "AAD must bind tenant identity")
	tampered = row
	tampered.ChannelID++
	_, err = resolveProviderCredentialVersionRow(tampered)
	assert.ErrorContains(t, err, "authentication failed", "AAD must bind channel identity")
}

func TestProviderCredentialVersionConcurrentCreationIsStable(t *testing.T) {
	require.NoError(t, DB.AutoMigrate(&ProviderCredentialVersion{}))
	tenantID := uuid.NewString()
	providerKey := "concurrent-provider-key"
	digest := sha256.Sum256([]byte(providerKey))
	fingerprint := fmt.Sprintf("%x", digest[:])

	const workers = 24
	versions := make(chan string, workers)
	errorsSeen := make(chan error, workers)
	var wg sync.WaitGroup
	for index := 0; index < workers; index++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			version, err := storeProviderCredentialVersionTx(DB, tenantID, 8102, 1, providerKey, fingerprint)
			versions <- version
			errorsSeen <- err
		}()
	}
	wg.Wait()
	close(versions)
	close(errorsSeen)

	for err := range errorsSeen {
		require.NoError(t, err)
	}
	stableVersion := ""
	for version := range versions {
		if stableVersion == "" {
			stableVersion = version
		}
		assert.Equal(t, stableVersion, version)
	}
	var count int64
	require.NoError(t, DB.Model(&ProviderCredentialVersion{}).Where(
		"tenant_id = ? AND channel_id = ? AND key_index = ? AND key_fingerprint = ?",
		tenantID, 8102, 1, fingerprint,
	).Count(&count).Error)
	assert.EqualValues(t, 1, count)
}

func TestProviderCredentialActiveKEKRotationCreatesNewVersionAndKeepsOldTaskResolvable(t *testing.T) {
	require.NoError(t, DB.AutoMigrate(&ProviderCredentialVersion{}))
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("APP_ENV", "")
	t.Setenv("DEPLOYMENT_ENV", "")
	t.Setenv("ENVIRONMENT", "")
	t.Setenv(providerCredentialKeyringFileEnvironment, "")

	keyA := []byte("0123456789abcdef0123456789abcdef")
	keyB := []byte("fedcba9876543210fedcba9876543210")
	keyringJSON := func(active string, keys map[string][]byte) string {
		encoded := make(map[string]string, len(keys))
		for keyID, key := range keys {
			encoded[keyID] = base64.StdEncoding.EncodeToString(key)
		}
		raw, err := json.Marshal(providerCredentialKeyringDocument{
			SchemaVersion: providerCredentialKeyringSchema,
			ActiveKeyID:   active,
			Keys:          encoded,
		})
		require.NoError(t, err)
		return string(raw)
	}

	t.Setenv(providerCredentialKeyringJSONEnvironment, keyringJSON("kek-a", map[string][]byte{"kek-a": keyA}))
	tenantID := uuid.NewString()
	providerKey := "provider-key-stays-the-same-across-kek-rotation"
	digest := sha256.Sum256([]byte(providerKey))
	keyIndex := 4
	newTask := func() *Task {
		return &Task{
			TaskID:    GenerateTaskID(),
			ChannelId: 8103,
			PrivateData: TaskPrivateData{
				PinnedKeyIndex:       &keyIndex,
				PinnedKeyFingerprint: fmt.Sprintf("%x", digest[:]),
				TransientProviderKey: providerKey,
			},
		}
	}
	oldTask := newTask()
	require.NoError(t, BindTaskProviderCredentialVersion(oldTask, tenantID))

	t.Setenv(providerCredentialKeyringJSONEnvironment, keyringJSON("kek-b", map[string][]byte{
		"kek-a": keyA,
		"kek-b": keyB,
	}))
	rotatedTask := newTask()
	require.NoError(t, BindTaskProviderCredentialVersion(rotatedTask, tenantID))
	require.NotEqual(t, oldTask.PrivateData.ProviderCredentialVersion, rotatedTask.PrivateData.ProviderCredentialVersion)

	var oldRow, rotatedRow ProviderCredentialVersion
	require.NoError(t, DB.First(&oldRow, "credential_version = ?", oldTask.PrivateData.ProviderCredentialVersion).Error)
	require.NoError(t, DB.First(&rotatedRow, "credential_version = ?", rotatedTask.PrivateData.ProviderCredentialVersion).Error)
	assert.Equal(t, "kek-a", oldRow.KeyID)
	assert.Equal(t, "kek-b", rotatedRow.KeyID)

	resolvedOld, err := ResolveTaskProviderCredential(oldTask)
	require.NoError(t, err)
	assert.Equal(t, providerKey, resolvedOld)
	resolvedRotated, err := ResolveTaskProviderCredential(rotatedTask)
	require.NoError(t, err)
	assert.Equal(t, providerKey, resolvedRotated)
}

func TestProviderCredentialLegacyMigrationIsAtomicAndInstallsSQLiteGuards(t *testing.T) {
	originalDB := DB
	database, err := gorm.Open(sqlite.Open("file:provider-vault-"+uuid.NewString()+"?mode=memory&cache=shared"), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	DB = database
	t.Cleanup(func() { DB = originalDB })
	require.NoError(t, DB.AutoMigrate(&Task{}, &PlatformGenerationJob{}, &ProviderCredentialVersion{}))
	require.NoError(t, DB.Migrator().DropIndex(&ProviderCredentialVersion{}, "uniq_provider_credential_identity"))
	require.NoError(t, DB.Exec(
		"CREATE UNIQUE INDEX uniq_provider_credential_identity ON provider_credential_versions (tenant_id, channel_id, key_index, key_fingerprint)",
	).Error, "simulate the pre-key_id identity index during an upgrade")

	tenantID := uuid.NewString()
	taskID := GenerateTaskID()
	providerKey := "legacy-provider-key"
	digest := sha256.Sum256([]byte(providerKey))
	fingerprint := fmt.Sprintf("%x", digest[:])
	legacyTaskJSON := fmt.Sprintf(`{"pinned_key_index":0,"pinned_key_fingerprint":%q,"key":%q}`, fingerprint, providerKey)
	require.NoError(t, DB.Exec(
		"INSERT INTO tasks (task_id, channel_id, private_data) VALUES (?, ?, ?)",
		taskID, 8201, legacyTaskJSON,
	).Error)
	emptyLegacyTaskID := GenerateTaskID()
	require.NoError(t, DB.Exec(
		"INSERT INTO tasks (task_id, channel_id, private_data) VALUES (?, ?, ?)",
		emptyLegacyTaskID, 8201, `{"key":""}`,
	).Error)
	job := PlatformGenerationJob{
		ID:                         uuid.NewString(),
		TenantID:                   tenantID,
		SourceClientID:             "platform",
		RequestID:                  "provider-vault-migration",
		IdempotencyKey:             "provider-vault-migration",
		RequestHash:                strings.Repeat("a", 64),
		RequestJSON:                `{}`,
		Model:                      "video-model",
		Mode:                       "text_to_video",
		ExpectedCapabilityRevision: "sha256:" + strings.Repeat("b", 64),
		CapabilityRevision:         "sha256:" + strings.Repeat("b", 64),
		Status:                     PlatformGenerationStatusSubmitting,
		NativeTaskID:               taskID,
		NativeTaskRecoveryJSON:     fmt.Sprintf(`{"schema_version":1,"pinned_key":%q}`, providerKey),
		OutputsJSON:                `[]`,
		ErrorDetailsJSON:           `{}`,
	}
	require.NoError(t, DB.Create(&job).Error)
	emptyRecoveryJob := job
	emptyRecoveryJob.ID = uuid.NewString()
	emptyRecoveryJob.RequestID = "provider-vault-empty-migration"
	emptyRecoveryJob.IdempotencyKey = "provider-vault-empty-migration"
	emptyRecoveryJob.NativeTaskID = ""
	emptyRecoveryJob.NativeTaskRecoveryJSON = `{"schema_version":1,"pinned_key":""}`
	require.NoError(t, DB.Create(&emptyRecoveryJob).Error)

	require.Error(t, MigrateProviderCredentialVaultStorage(), "invalid recovery must roll the whole migration back")
	var privateData string
	require.NoError(t, DB.Table("tasks").Select("private_data").Where("task_id = ?", taskID).Scan(&privateData).Error)
	assert.Contains(t, privateData, providerKey)
	var count int64
	require.NoError(t, DB.Model(&ProviderCredentialVersion{}).Count(&count).Error)
	assert.Zero(t, count, "credential insert and task rewrite must roll back together")

	validRecovery := fmt.Sprintf(`{"schema_version":1,"route_id":1,"attempt":1,"task_id":%q,"channel_id":8201,"platform":"test","user_id":1,"group":"default","action":"generate","submit_time":1,"properties":{},"pinned_key_index":0,"pinned_key_fingerprint":%q,"pinned_key":%q}`, taskID, fingerprint, providerKey)
	require.NoError(t, DB.Model(&PlatformGenerationJob{}).Where("id = ?", job.ID).
		Update("native_task_recovery_json", validRecovery).Error)
	require.NoError(t, MigrateProviderCredentialVaultStorage())
	var identityIndexColumns []struct{ Name string }
	require.NoError(t, DB.Raw("PRAGMA index_info('uniq_provider_credential_identity')").Scan(&identityIndexColumns).Error)
	var identityColumnNames []string
	for _, column := range identityIndexColumns {
		identityColumnNames = append(identityColumnNames, column.Name)
	}
	assert.Equal(t, []string{"tenant_id", "channel_id", "key_index", "key_fingerprint", "key_id"}, identityColumnNames)

	require.NoError(t, DB.Table("tasks").Select("private_data").Where("task_id = ?", taskID).Scan(&privateData).Error)
	assert.NotContains(t, privateData, providerKey)
	assert.NotContains(t, privateData, `"key"`)
	var recoveryJSON string
	require.NoError(t, DB.Table("platform_generation_jobs").Select("native_task_recovery_json").Where("id = ?", job.ID).Scan(&recoveryJSON).Error)
	assert.NotContains(t, recoveryJSON, providerKey)
	assert.NotContains(t, recoveryJSON, "pinned_key\"")
	assert.Contains(t, recoveryJSON, "provider_credential_version")
	var emptyTaskPrivateData string
	require.NoError(t, DB.Table("tasks").Select("private_data").Where("task_id = ?", emptyLegacyTaskID).Scan(&emptyTaskPrivateData).Error)
	assert.NotContains(t, emptyTaskPrivateData, `"key"`)
	var emptyRecoveryJSON string
	require.NoError(t, DB.Table("platform_generation_jobs").Select("native_task_recovery_json").
		Where("id = ?", emptyRecoveryJob.ID).Scan(&emptyRecoveryJSON).Error)
	assert.NotContains(t, emptyRecoveryJSON, "pinned_key")
	assert.Contains(t, emptyRecoveryJSON, `"schema_version":2`)

	assert.Error(t, DB.Table("tasks").Where("task_id = ?", taskID).Update("private_data", legacyTaskJSON).Error)
	assert.Error(t, DB.Table("tasks").Where("task_id = ?", taskID).Update("private_data", `{"key":""}`).Error)
	assert.Error(t, DB.Table("platform_generation_jobs").Where("id = ?", job.ID).
		Update("native_task_recovery_json", validRecovery).Error)
	assert.Error(t, DB.Table("platform_generation_jobs").Where("id = ?", job.ID).
		Update("native_task_recovery_json", `{"schema_version":2,"pinned_key":""}`).Error)
	assert.Error(t, DB.Model(&ProviderCredentialVersion{}).Where("tenant_id = ?", tenantID).Update("key_id", "tampered").Error)
	assert.Error(t, DB.Where("tenant_id = ?", tenantID).Delete(&ProviderCredentialVersion{}).Error)
}

func TestProviderCredentialProductionKeyringUsesDedicatedPrivateFileAndBlocksNativePlaintext(t *testing.T) {
	useProviderCredentialTestFileReader(t)
	originalDB := DB
	database, err := gorm.Open(sqlite.Open("file:provider-vault-prod-"+uuid.NewString()+"?mode=memory&cache=shared"), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	DB = database
	t.Cleanup(func() { DB = originalDB })
	require.NoError(t, DB.AutoMigrate(&Channel{}, &ProviderCredentialVersion{}, &ProviderChannelCredentialSetVersion{}))

	originalInline, hadInline := os.LookupEnv(providerCredentialKeyringJSONEnvironment)
	originalFile, hadFile := os.LookupEnv(providerCredentialKeyringFileEnvironment)
	require.NoError(t, os.Unsetenv(providerCredentialKeyringJSONEnvironment))
	t.Cleanup(func() {
		if hadInline {
			_ = os.Setenv(providerCredentialKeyringJSONEnvironment, originalInline)
		} else {
			_ = os.Unsetenv(providerCredentialKeyringJSONEnvironment)
		}
		if hadFile {
			_ = os.Setenv(providerCredentialKeyringFileEnvironment, originalFile)
		} else {
			_ = os.Unsetenv(providerCredentialKeyringFileEnvironment)
		}
	})

	keyringPath := filepath.Join(t.TempDir(), "provider-keyring.json")
	require.NoError(t, os.WriteFile(keyringPath, []byte(providerCredentialTestKeyringJSON), 0o600))
	require.NoError(t, os.Chmod(keyringPath, 0o600))
	require.NoError(t, os.Setenv(providerCredentialKeyringFileEnvironment, keyringPath))
	require.NoError(t, ValidateProviderCredentialVaultRuntime(true))

	require.NoError(t, DB.Session(&gorm.Session{SkipHooks: true}).Table("channels").Create(map[string]any{
		"id": 8301, "key": "native-plaintext-key", "name": "legacy-channel",
	}).Error)
	err = ValidateProviderCredentialVaultRuntime(true)
	assert.ErrorContains(t, err, "plaintext native channel keys")

	require.NoError(t, os.Setenv(providerCredentialKeyringJSONEnvironment, providerCredentialTestKeyringJSON))
	err = ValidateProviderCredentialVaultRuntime(true)
	assert.ErrorContains(t, err, "inline configuration is forbidden")

	if runtime.GOOS != "windows" {
		require.NoError(t, os.Unsetenv(providerCredentialKeyringJSONEnvironment))
		require.NoError(t, os.Chmod(keyringPath, 0o644))
		readProviderCredentialKeyringFile = common.ReadProtectedSecretFile
		_, err = loadProviderCredentialKeyring(true)
		assert.ErrorContains(t, err, "unavailable or invalid")
	}
}

func TestProviderCredentialProductionReadinessRequiresHistoricalKEKForActiveReferences(t *testing.T) {
	useProviderCredentialTestFileReader(t)
	originalDB := DB
	database, err := gorm.Open(sqlite.Open("file:provider-vault-readiness-"+uuid.NewString()+"?mode=memory&cache=shared"), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	DB = database
	t.Cleanup(func() { DB = originalDB })
	require.NoError(t, DB.AutoMigrate(
		&Channel{},
		&ProviderCredentialVersion{},
		&ProviderChannelCredentialSetVersion{},
		&Task{},
		&PlatformGenerationJob{},
	))

	originalInline, hadInline := os.LookupEnv(providerCredentialKeyringJSONEnvironment)
	originalFile, hadFile := os.LookupEnv(providerCredentialKeyringFileEnvironment)
	require.NoError(t, os.Unsetenv(providerCredentialKeyringJSONEnvironment))
	t.Cleanup(func() {
		if hadInline {
			_ = os.Setenv(providerCredentialKeyringJSONEnvironment, originalInline)
		} else {
			_ = os.Unsetenv(providerCredentialKeyringJSONEnvironment)
		}
		if hadFile {
			_ = os.Setenv(providerCredentialKeyringFileEnvironment, originalFile)
		} else {
			_ = os.Unsetenv(providerCredentialKeyringFileEnvironment)
		}
	})
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "production")
	t.Setenv("APP_ENV", "")
	t.Setenv("DEPLOYMENT_ENV", "")
	t.Setenv("ENVIRONMENT", "")

	oldKEK := base64.StdEncoding.EncodeToString([]byte("0123456789abcdef0123456789abcdef"))
	newKEK := base64.StdEncoding.EncodeToString([]byte("fedcba9876543210fedcba9876543210"))
	keyringPath := filepath.Join(t.TempDir(), "provider-keyring.json")
	writeKeyring := func(active string, keys string) {
		t.Helper()
		require.NoError(t, os.WriteFile(keyringPath, []byte(fmt.Sprintf(
			`{"schema_version":1,"active_key_id":%q,"keys":%s}`,
			active,
			keys,
		)), 0o600))
		require.NoError(t, os.Chmod(keyringPath, 0o600))
	}
	writeKeyring("old-v1", fmt.Sprintf(`{"old-v1":%q}`, oldKEK))
	require.NoError(t, os.Setenv(providerCredentialKeyringFileEnvironment, keyringPath))

	channel := Channel{Id: 8401, Name: "readiness-channel", Key: "historical-provider-key"}
	require.NoError(t, DB.Create(&channel).Error)
	require.NotEmpty(t, channel.CredentialSetVersion)

	tenantID := uuid.NewString()
	keyIndex := 0
	fingerprint := fmt.Sprintf("%x", sha256.Sum256([]byte(channel.Key)))
	task := Task{
		TaskID:    GenerateTaskID(),
		ChannelId: channel.Id,
		Status:    TaskStatusInProgress,
		PrivateData: TaskPrivateData{
			PinnedKeyIndex:       &keyIndex,
			PinnedKeyFingerprint: fingerprint,
			TransientProviderKey: channel.Key,
		},
	}
	require.NoError(t, BindTaskProviderCredentialVersion(&task, tenantID))
	require.NoError(t, DB.Create(&task).Error)

	recovery := platformGenerationNativeTaskRecovery{
		SchemaVersion:              platformGenerationNativeTaskRecoverySchemaVersion,
		ChannelID:                  channel.Id,
		PinnedKeyIndex:             keyIndex,
		PinnedKeyFingerprint:       fingerprint,
		ProviderCredentialTenantID: tenantID,
		ProviderCredentialVersion:  task.PrivateData.ProviderCredentialVersion,
	}
	recoveryJSON, err := json.Marshal(recovery)
	require.NoError(t, err)
	job := PlatformGenerationJob{
		ID:                         uuid.NewString(),
		TenantID:                   tenantID,
		SourceClientID:             "readiness-test",
		RequestID:                  "readiness-request",
		IdempotencyKey:             "readiness-idempotency",
		RequestHash:                strings.Repeat("a", 64),
		RequestJSON:                `{}`,
		Model:                      "readiness-model",
		Mode:                       "text_to_video",
		ExpectedCapabilityRevision: "sha256:" + strings.Repeat("b", 64),
		CapabilityRevision:         "sha256:" + strings.Repeat("b", 64),
		Status:                     PlatformGenerationStatusProcessing,
		NativeTaskRecoveryJSON:     string(recoveryJSON),
	}
	require.NoError(t, DB.Create(&job).Error)

	writeKeyring("new-v2", fmt.Sprintf(`{"new-v2":%q,"old-v1":%q}`, newKEK, oldKEK))
	require.NoError(t, RotateChannelCredentialSet(channel.Id, "current-provider-key"))
	require.NoError(t, ValidateProviderCredentialVaultRuntime(true), "retaining the historical KEK must keep active task and recovery references ready")

	writeKeyring("new-v2", fmt.Sprintf(`{"new-v2":%q}`, newKEK))
	err = ValidateProviderCredentialVaultRuntime(true)
	assert.ErrorContains(t, err, "KEK is unavailable")

	require.NoError(t, DB.Model(&Task{}).Where("id = ?", task.ID).Update("status", TaskStatusSuccess).Error)
	err = ValidateProviderCredentialVaultRuntime(true)
	assert.ErrorContains(t, err, "KEK is unavailable", "successful native video content must retain its historical KEK")

	require.NoError(t, DB.Model(&PlatformGenerationJob{}).Where("id = ?", job.ID).
		Update("status", PlatformGenerationStatusSucceeded).Error)
	err = ValidateProviderCredentialVaultRuntime(true)
	assert.ErrorContains(t, err, "KEK is unavailable", "terminal Platform recovery cannot hide a still-readable native content reference")

	require.NoError(t, DB.Delete(&task).Error)
	require.NoError(t, ValidateProviderCredentialVaultRuntime(true), "explicit task retention removal closes the final provider-facing reference")
}

func TestProviderCredentialKeyringRejectsApplicationSecretReuse(t *testing.T) {
	key := []byte("0123456789abcdef0123456789abcdef")
	t.Setenv("CRYPTO_SECRET", string(key))
	t.Setenv(providerCredentialKeyringJSONEnvironment, fmt.Sprintf(
		`{"schema_version":1,"active_key_id":"bad-v1","keys":{"bad-v1":%q}}`,
		base64.StdEncoding.EncodeToString(key),
	))
	_, err := loadProviderCredentialKeyring(false)
	assert.ErrorContains(t, err, "independent")
}

func TestProviderCredentialKeyringRejectsNonCanonicalWeakAndDuplicateKeys(t *testing.T) {
	keyA := make([]byte, 32)
	keyB := make([]byte, 32)
	for index := range keyA {
		keyA[index] = byte(index)
		keyB[index] = byte(index + 32)
	}
	encodedA := base64.StdEncoding.EncodeToString(keyA)
	encodedB := base64.StdEncoding.EncodeToString(keyB)
	lowDiversity := base64.StdEncoding.EncodeToString(make([]byte, 32))
	tests := map[string]string{
		"duplicate metadata key": `{"schema_version":1,"schema_version":1,"active_key_id":"a","keys":{"a":"` + encodedA + `"}}`,
		"unsorted key ids":       `{"schema_version":1,"active_key_id":"a","keys":{"z":"` + encodedB + `","a":"` + encodedA + `"}}`,
		"base64 with newline":    `{"schema_version":1,"active_key_id":"a","keys":{"a":"` + encodedA[:12] + `\n` + encodedA[12:] + `"}}`,
		"low diversity":          `{"schema_version":1,"active_key_id":"a","keys":{"a":"` + lowDiversity + `"}}`,
		"same key across ids":    `{"schema_version":1,"active_key_id":"a","keys":{"a":"` + encodedA + `","b":"` + encodedA + `"}}`,
	}
	for name, document := range tests {
		t.Run(name, func(t *testing.T) {
			t.Setenv("APP_ENV", "development")
			t.Setenv("DEPLOYMENT_ENV", "development")
			t.Setenv("ENVIRONMENT", "development")
			t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
			t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
			t.Setenv("RELAY_DATABASE_SECRET_FILES_REQUIRED", "false")
			t.Setenv(providerCredentialKeyringFileEnvironment, "")
			t.Setenv(providerCredentialKeyringJSONEnvironment, document)
			_, err := loadProviderCredentialKeyring(false)
			require.Error(t, err)
			require.NotContains(t, err.Error(), encodedA)
		})
	}
}

func TestProviderCredentialKeyringRejectsProtectedRuntimeSecretDigestReuse(t *testing.T) {
	if os.Getenv("RELAY_KEK_DIGEST_REUSE_HELPER") == "1" {
		key := []byte("0123456789abcdef0123456789abcdef")
		encoded := base64.StdEncoding.EncodeToString(key)
		registeredValue := key
		if os.Getenv("RELAY_KEK_DIGEST_REUSE_REPRESENTATION") == "encoded" {
			registeredValue = []byte(encoded)
		}
		digest := sha256.Sum256(registeredValue)
		require.NoError(t, common.ConfigureProtectedRelayRuntimeSecrets(
			"rediss://:unused@redis.invalid:6380/0", "unused-session", "unused-crypto",
			relayModelTestRedisTLSCA(t), digest,
		))
		t.Setenv("APP_ENV", "development")
		t.Setenv("DEPLOYMENT_ENV", "development")
		t.Setenv("ENVIRONMENT", "development")
		t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
		t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
		t.Setenv("RELAY_DATABASE_SECRET_FILES_REQUIRED", "false")
		t.Setenv(providerCredentialKeyringFileEnvironment, "")
		t.Setenv(providerCredentialKeyringJSONEnvironment, fmt.Sprintf(
			`{"schema_version":1,"active_key_id":"runtime-reuse","keys":{"runtime-reuse":%q}}`,
			encoded,
		))
		_, err := loadProviderCredentialKeyring(false)
		require.ErrorContains(t, err, "independent")
		return
	}
	for _, representation := range []string{"decoded", "encoded"} {
		t.Run(representation, func(t *testing.T) {
			command := exec.Command(os.Args[0], "-test.run=^TestProviderCredentialKeyringRejectsProtectedRuntimeSecretDigestReuse$")
			command.Env = append(
				os.Environ(),
				"RELAY_KEK_DIGEST_REUSE_HELPER=1",
				"RELAY_KEK_DIGEST_REUSE_REPRESENTATION="+representation,
			)
			output, err := command.CombinedOutput()
			require.NoError(t, err, string(output))
		})
	}
}

func TestTaskPrivateDataRejectsLegacyAndTransientPlaintext(t *testing.T) {
	var scanned TaskPrivateData
	require.NoError(t, scanned.Scan([]byte(`{"key":"legacy-provider-key"}`)))
	assert.True(t, scanned.LegacyProviderCredentialPresent)
	_, err := scanned.Value()
	assert.ErrorContains(t, err, "plaintext provider credentials")

	_, err = (TaskPrivateData{TransientProviderKey: "ephemeral"}).Value()
	assert.ErrorContains(t, err, "plaintext provider credentials")

	safe := TaskPrivateData{
		ProviderCredentialTenantID: ProviderCredentialNativeTenantScope,
		ProviderCredentialVersion:  uuid.NewString(),
	}
	value, err := safe.Value()
	require.NoError(t, err)
	encoded, ok := value.(string)
	require.True(t, ok)
	var fields map[string]any
	require.NoError(t, json.Unmarshal([]byte(encoded), &fields))
	_, hasRawKey := fields["key"]
	assert.False(t, hasRawKey)
}

func TestPlatformRecoverySchemaRejectsLegacyPinnedKey(t *testing.T) {
	_, err := decodePlatformGenerationNativeTaskRecovery(`{"schema_version":1,"pinned_key":"must-not-be-used"}`)
	require.Error(t, err)
	assert.NotContains(t, err.Error(), "must-not-be-used")
}

func TestProviderCredentialIdentityDoesNotAcceptEmptyTenant(t *testing.T) {
	key := "provider-key"
	digest := sha256.Sum256([]byte(key))
	_, err := normalizeProviderCredentialIdentity("", 1, 0, key, fmt.Sprintf("%x", digest[:]))
	assert.ErrorContains(t, err, "identity is invalid")
}
