//go:build integration

package platformrelay_test

import (
	"crypto/sha256"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
)

func TestProviderCredentialVaultPostgresEncryptionConcurrencyAndGuards(t *testing.T) {
	resetIntegrationState(t)
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("APP_ENV", "")
	t.Setenv("DEPLOYMENT_ENV", "")
	t.Setenv("ENVIRONMENT", "")
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON", `{"schema_version":1,"active_key_id":"pg-test-v1","keys":{"pg-test-v1":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}}`)
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE", "")

	tenantID := uuid.NewString()
	providerKey := "postgres-provider-key-must-stay-encrypted"
	digest := sha256.Sum256([]byte(providerKey))
	fingerprint := fmt.Sprintf("%x", digest[:])

	const workers = 12
	versions := make(chan string, workers)
	errorsSeen := make(chan error, workers)
	var wg sync.WaitGroup
	for index := 0; index < workers; index++ {
		wg.Add(1)
		go func(taskIndex int) {
			defer wg.Done()
			keyIndex := 3
			task := &model.Task{
				TaskID:    fmt.Sprintf("task_pg_vault_%02d_%s", taskIndex, strings.ReplaceAll(uuid.NewString(), "-", "")),
				ChannelId: 9911,
				Status:    model.TaskStatusSubmitted,
				PrivateData: model.TaskPrivateData{
					PinnedKeyIndex:       &keyIndex,
					PinnedKeyFingerprint: fingerprint,
					TransientProviderKey: providerKey,
				},
			}
			err := model.BindTaskProviderCredentialVersion(task, tenantID)
			if err != nil {
				err = fmt.Errorf("bind credential version: %w", err)
			} else if createErr := integrationDB.Select("TaskID", "ChannelId", "PrivateData").Create(task).Error; createErr != nil {
				err = fmt.Errorf("persist task credential reference: %w", createErr)
			}
			versions <- task.PrivateData.ProviderCredentialVersion
			errorsSeen <- err
		}(index)
	}
	wg.Wait()
	close(versions)
	close(errorsSeen)
	for err := range errorsSeen {
		requireNoError(t, err)
	}
	stableVersion := ""
	for version := range versions {
		if stableVersion == "" {
			stableVersion = version
		}
		if version != stableVersion {
			t.Fatalf("concurrent credential identity returned unstable versions: first=%s next=%s", stableVersion, version)
		}
	}

	var row model.ProviderCredentialVersion
	requireNoError(t, integrationDB.First(&row, "credential_version = ?", stableVersion).Error)
	if row.KeyID != "pg-test-v1" || row.Version != 1 || len(row.Nonce) != 12 ||
		strings.Contains(string(row.Ciphertext), providerKey) {
		t.Fatalf("unexpected encrypted credential row metadata")
	}
	var plaintextTaskCopies int64
	requireNoError(t, integrationDB.Raw(
		"SELECT COUNT(*) FROM tasks WHERE private_data::text LIKE ?",
		"%"+providerKey+"%",
	).Scan(&plaintextTaskCopies).Error)
	if plaintextTaskCopies != 0 {
		t.Fatalf("provider key leaked into task private_data")
	}

	var persisted model.Task
	requireNoError(t, integrationDB.First(&persisted, "task_id LIKE ?", "task_pg_vault_00_%").Error)
	resolved, err := model.ResolveTaskProviderCredential(&persisted)
	requireNoError(t, err)
	if resolved != providerKey {
		t.Fatalf("resolved provider credential did not match")
	}

	// Changing the active KEK must create a new immutable credential version
	// for the same provider key, while old tasks remain resolvable as long as
	// their old KEK is retained in the keyring.
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON", `{"schema_version":1,"active_key_id":"pg-test-v2","keys":{"pg-test-v1":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=","pg-test-v2":"YWJjZGVmMDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODk="}}`)
	rotatedKeyIndex := 3
	rotatedTask := &model.Task{
		TaskID:    "task_pg_vault_rotated_" + strings.ReplaceAll(uuid.NewString(), "-", ""),
		ChannelId: 9911,
		PrivateData: model.TaskPrivateData{
			PinnedKeyIndex:       &rotatedKeyIndex,
			PinnedKeyFingerprint: fingerprint,
			TransientProviderKey: providerKey,
		},
	}
	requireNoError(t, model.BindTaskProviderCredentialVersion(rotatedTask, tenantID))
	if rotatedTask.PrivateData.ProviderCredentialVersion == stableVersion {
		t.Fatal("active KEK rotation reused the old credential version")
	}
	var rotatedRow model.ProviderCredentialVersion
	requireNoError(t, integrationDB.First(&rotatedRow, "credential_version = ?", rotatedTask.PrivateData.ProviderCredentialVersion).Error)
	if rotatedRow.KeyID != "pg-test-v2" {
		t.Fatalf("rotated credential used unexpected KEK id %q", rotatedRow.KeyID)
	}
	resolved, err = model.ResolveTaskProviderCredential(&persisted)
	requireNoError(t, err)
	if resolved != providerKey {
		t.Fatal("old task stopped resolving after active KEK rotation")
	}
	resolved, err = model.ResolveTaskProviderCredential(rotatedTask)
	requireNoError(t, err)
	if resolved != providerKey {
		t.Fatal("rotated task credential did not resolve")
	}

	legacyTaskID := "task_pg_vault_raw_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	legacyJSON := fmt.Sprintf(`{"pinned_key_index":3,"pinned_key_fingerprint":%q,"key":%q}`, fingerprint, providerKey)
	if err := integrationDB.Exec(
		"INSERT INTO tasks (task_id, channel_id, private_data) VALUES (?, ?, ?::json)",
		legacyTaskID, 9911, legacyJSON,
	).Error; err == nil {
		t.Fatal("PostgreSQL guard accepted plaintext task credential")
	}
	if err := integrationDB.Exec(
		"INSERT INTO tasks (task_id, channel_id, private_data) VALUES (?, ?, ?::json)",
		legacyTaskID+"_empty", 9911, `{"key":""}`,
	).Error; err == nil {
		t.Fatal("PostgreSQL guard accepted an empty legacy task credential field")
	}

	job := model.PlatformGenerationJob{
		ID:                         uuid.NewString(),
		TenantID:                   tenantID,
		SourceClientID:             "platform",
		RequestID:                  "pg-vault-guard",
		IdempotencyKey:             "pg-vault-guard-" + uuid.NewString(),
		RequestHash:                strings.Repeat("a", 64),
		RequestJSON:                `{}`,
		Model:                      "video-model",
		Mode:                       "text_to_video",
		ExpectedCapabilityRevision: "sha256:" + strings.Repeat("b", 64),
		CapabilityRevision:         "sha256:" + strings.Repeat("b", 64),
		Status:                     model.PlatformGenerationStatusSubmitting,
		OutputsJSON:                `[]`,
		ErrorDetailsJSON:           `{}`,
	}
	requireNoError(t, integrationDB.Create(&job).Error)
	legacyRecovery := fmt.Sprintf(`{"schema_version":1,"pinned_key":%q}`, providerKey)
	if err := integrationDB.Model(&model.PlatformGenerationJob{}).Where("id = ?", job.ID).
		Update("native_task_recovery_json", legacyRecovery).Error; err == nil {
		t.Fatal("PostgreSQL guard accepted plaintext recovery credential")
	}
	if err := integrationDB.Model(&model.PlatformGenerationJob{}).Where("id = ?", job.ID).
		Update("native_task_recovery_json", `{"schema_version":2,"pinned_key":""}`).Error; err == nil {
		t.Fatal("PostgreSQL guard accepted an empty legacy recovery credential field")
	}
	if err := integrationDB.Model(&model.ProviderCredentialVersion{}).Where("credential_version = ?", stableVersion).
		Update("key_id", "tampered").Error; err == nil {
		t.Fatal("PostgreSQL guard accepted credential version update")
	}
	if err := integrationDB.Where("credential_version = ?", stableVersion).
		Delete(&model.ProviderCredentialVersion{}).Error; err == nil {
		t.Fatal("PostgreSQL guard accepted credential version delete")
	}
	if err := integrationDB.Exec("TRUNCATE TABLE provider_credential_versions").Error; err == nil {
		t.Fatal("PostgreSQL guard accepted credential version truncate")
	}
}

func TestProviderCredentialPostgresMigrationFencesConcurrentLegacyWriterUntilGuardCommit(t *testing.T) {
	resetIntegrationState(t)
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("APP_ENV", "")
	t.Setenv("DEPLOYMENT_ENV", "")
	t.Setenv("ENVIRONMENT", "")
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON", `{"schema_version":1,"active_key_id":"pg-migration-v1","keys":{"pg-migration-v1":"Y2RlZjAxMjM0NTY3ODlhYmNkZWYwMTIzNDU2Nzg5YWI="}}`)
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE", "")

	// Simulate the exact upgrade state: old pods can still write raw Task JSON
	// because the new guard has not yet been installed.
	requireNoError(t, integrationDB.Exec("DROP TRIGGER IF EXISTS trg_tasks_no_plaintext_provider_credential ON tasks").Error)
	t.Cleanup(func() {
		_ = model.MigrateProviderCredentialVaultStorage()
	})

	providerKey := "legacy-provider-key-written-by-old-pod"
	digest := sha256.Sum256([]byte(providerKey))
	fingerprint := fmt.Sprintf("%x", digest[:])
	firstTaskID := "task_pg_migration_before_lock_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	secondTaskID := "task_pg_migration_waiting_writer_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	legacyJSON := fmt.Sprintf(`{"pinned_key_index":0,"pinned_key_fingerprint":%q,"key":%q}`, fingerprint, providerKey)

	oldPodTx := integrationDB.Begin()
	requireNoError(t, oldPodTx.Error)
	t.Cleanup(func() { _ = oldPodTx.Rollback().Error })
	requireNoError(t, oldPodTx.Exec(
		"INSERT INTO tasks (task_id, channel_id, private_data) VALUES (?, ?, ?::json)",
		firstTaskID, 9921, legacyJSON,
	).Error)

	migrationResult := make(chan error, 1)
	go func() {
		migrationResult <- model.MigrateProviderCredentialVaultStorage()
	}()

	// The migration must queue its SHARE ROW EXCLUSIVE table lock behind the
	// old pod's uncommitted ROW EXCLUSIVE lock before it scans legacy rows.
	deadline := time.Now().Add(5 * time.Second)
	for {
		var waiting int64
		requireNoError(t, integrationDB.Raw(`
			SELECT COUNT(*)
			FROM pg_locks locks
			JOIN pg_class relation ON relation.oid = locks.relation
			WHERE relation.relname = 'tasks'
			  AND relation.relnamespace = current_schema()::regnamespace
			  AND locks.mode = 'ShareRowExclusiveLock'
			  AND NOT locks.granted
		`).Scan(&waiting).Error)
		if waiting > 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("provider credential migration did not wait on the expected table fence")
		}
		time.Sleep(10 * time.Millisecond)
	}

	blockedWriterResult := make(chan error, 1)
	go func() {
		blockedWriterResult <- integrationDB.Exec(
			"INSERT INTO tasks (task_id, channel_id, private_data) VALUES (?, ?, ?::json)",
			secondTaskID, 9921, legacyJSON,
		).Error
	}()

	requireNoError(t, oldPodTx.Commit().Error)
	select {
	case err := <-migrationResult:
		requireNoError(t, err)
	case <-time.After(10 * time.Second):
		t.Fatal("provider credential migration did not finish")
	}
	select {
	case err := <-blockedWriterResult:
		if err == nil {
			t.Fatal("legacy writer crossed the migration commit boundary before the plaintext guard")
		}
	case <-time.After(10 * time.Second):
		t.Fatal("legacy writer remained blocked after migration commit")
	}

	var migratedPrivateData string
	requireNoError(t, integrationDB.Table("tasks").Select("private_data").Where("task_id = ?", firstTaskID).Scan(&migratedPrivateData).Error)
	if strings.Contains(migratedPrivateData, providerKey) || strings.Contains(migratedPrivateData, `"key"`) ||
		!strings.Contains(migratedPrivateData, "provider_credential_version") {
		t.Fatal("row committed before the table fence was not atomically converted")
	}
	var secondTaskCount int64
	requireNoError(t, integrationDB.Table("tasks").Where("task_id = ?", secondTaskID).Count(&secondTaskCount).Error)
	if secondTaskCount != 0 {
		t.Fatal("plaintext task row was persisted after migration guard commit")
	}
}
