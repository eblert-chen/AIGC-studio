//go:build integration

package platformrelay_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"gorm.io/gorm"
)

const (
	providerChannelKeyringA  = `{"schema_version":1,"active_key_id":"pg-channel-a","keys":{"pg-channel-a":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}}`
	providerChannelKeyringB  = `{"schema_version":1,"active_key_id":"pg-channel-b","keys":{"pg-channel-b":"YWJjZGVmMDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODk="}}`
	providerChannelKeyringAB = `{"schema_version":1,"active_key_id":"pg-channel-b","keys":{"pg-channel-a":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=","pg-channel-b":"YWJjZGVmMDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODk="}}`
)

func setProviderChannelCredentialDevelopmentKeyring(t *testing.T, document string) {
	t.Helper()
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("APP_ENV", "")
	t.Setenv("DEPLOYMENT_ENV", "")
	t.Setenv("ENVIRONMENT", "")
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE", "")
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON", document)
}

func TestProviderChannelCredentialVaultPostgresCRUDRotationReadinessAndGuards(t *testing.T) {
	resetIntegrationState(t)
	setProviderChannelCredentialDevelopmentKeyring(t, providerChannelKeyringA)

	rawA := "postgres-provider-key-one\npostgres-provider-key-two"
	channel := model.Channel{
		Type:   constant.ChannelTypeOpenAI,
		Key:    rawA,
		Status: common.ChannelStatusEnabled,
		Name:   "postgres-encrypted-channel-" + uuid.NewString(),
		Models: "gpt-test",
		Group:  "default",
	}
	requireNoError(t, integrationDB.Create(&channel).Error)
	if channel.CredentialSetVersion == "" {
		t.Fatal("channel create did not install an encrypted credential reference")
	}
	versionA := channel.CredentialSetVersion

	var physical struct {
		LegacyKey            string `gorm:"column:key"`
		CredentialSetVersion string
	}
	requireNoError(t, integrationDB.Session(&gorm.Session{SkipHooks: true}).Table("channels").
		Select("key, credential_set_version").Where("id = ?", channel.Id).Take(&physical).Error)
	if physical.LegacyKey != "" || physical.CredentialSetVersion != versionA {
		t.Fatal("channel create retained plaintext or lost its encrypted reference")
	}

	var encryptedA model.ProviderChannelCredentialSetVersion
	requireNoError(t, integrationDB.Session(&gorm.Session{SkipHooks: true}).
		First(&encryptedA, "credential_set_version = ?", versionA).Error)
	if encryptedA.ChannelID != channel.Id || encryptedA.KeyID != "pg-channel-a" ||
		encryptedA.KeyCount != 2 || strings.Contains(string(encryptedA.Ciphertext), "postgres-provider-key") {
		t.Fatal("encrypted channel credential metadata is invalid or contains plaintext")
	}

	ordinary, err := model.GetChannelById(channel.Id, false)
	requireNoError(t, err)
	if ordinary.Key != "" || ordinary.CredentialSetVersion != "" {
		t.Fatal("ordinary channel lookup hydrated or exposed credential state")
	}
	hydrated, err := model.GetChannelById(channel.Id, true)
	requireNoError(t, err)
	if hydrated.Key != rawA {
		t.Fatal("explicit internal channel lookup did not preserve exact multi-key bytes")
	}

	requireNoError(t, model.RotateChannelCredentialSet(channel.Id, rawA))
	var currentVersion string
	requireNoError(t, integrationDB.Table("channels").Select("credential_set_version").
		Where("id = ?", channel.Id).Scan(&currentVersion).Error)
	if currentVersion != versionA {
		t.Fatal("same credential under the same active KEK created an unnecessary version")
	}

	rawB := "postgres-provider-key-three\npostgres-provider-key-four"
	requireNoError(t, model.RotateChannelCredentialSet(channel.Id, rawB))
	requireNoError(t, integrationDB.Table("channels").Select("credential_set_version").
		Where("id = ?", channel.Id).Scan(&currentVersion).Error)
	if currentVersion == versionA {
		t.Fatal("credential rotation did not create a new immutable version")
	}
	versionChangedUnderA := currentVersion

	setProviderChannelCredentialDevelopmentKeyring(t, providerChannelKeyringAB)
	requireNoError(t, model.RotateChannelCredentialSet(channel.Id, rawB))
	requireNoError(t, integrationDB.Table("channels").Select("credential_set_version").
		Where("id = ?", channel.Id).Scan(&currentVersion).Error)
	if currentVersion == versionChangedUnderA {
		t.Fatal("active KEK rotation reused the old channel credential version")
	}
	versionB := currentVersion
	var encryptedB model.ProviderChannelCredentialSetVersion
	requireNoError(t, integrationDB.Session(&gorm.Session{SkipHooks: true}).
		First(&encryptedB, "credential_set_version = ?", versionB).Error)
	if encryptedB.KeyID != "pg-channel-b" {
		t.Fatalf("rotated credential used unexpected KEK id %q", encryptedB.KeyID)
	}

	old := model.Channel{Id: channel.Id, CredentialSetVersion: versionA}
	requireNoError(t, model.HydrateChannelCredential(integrationDB, &old))
	if old.Key != rawA {
		t.Fatal("old credential version stopped resolving while its KEK was retained")
	}
	setProviderChannelCredentialDevelopmentKeyring(t, providerChannelKeyringB)
	old = model.Channel{Id: channel.Id, CredentialSetVersion: versionA}
	if err := model.HydrateChannelCredential(integrationDB, &old); err == nil {
		t.Fatal("old credential version resolved without its historical KEK")
	}
	current := model.Channel{Id: channel.Id, CredentialSetVersion: versionB}
	requireNoError(t, model.HydrateChannelCredential(integrationDB, &current))
	if current.Key != rawB {
		t.Fatal("current credential did not resolve under the new KEK")
	}

	if err := integrationDB.Exec(
		`INSERT INTO channels (type, key, status, name, models, "group") VALUES (?, ?, ?, ?, ?, ?)`,
		constant.ChannelTypeOpenAI, "forbidden-plaintext-insert", common.ChannelStatusEnabled,
		"forbidden-channel-"+uuid.NewString(), "gpt-test", "default",
	).Error; err == nil {
		t.Fatal("PostgreSQL guard accepted a plaintext channel credential insert")
	}
	if err := integrationDB.Exec("UPDATE channels SET key = ? WHERE id = ?", "forbidden-plaintext-update", channel.Id).Error; err == nil {
		t.Fatal("PostgreSQL guard accepted a plaintext channel credential update")
	}
	if err := integrationDB.Exec("UPDATE channels SET credential_set_version = ? WHERE id = ?", uuid.NewString(), channel.Id).Error; err == nil {
		t.Fatal("PostgreSQL guard accepted a credential reference not bound to the channel")
	}
	if err := integrationDB.Model(&model.ProviderChannelCredentialSetVersion{}).
		Where("credential_set_version = ?", versionB).Update("key_id", "tampered").Error; err == nil {
		t.Fatal("PostgreSQL guard accepted channel credential version update")
	}
	if err := integrationDB.Where("credential_set_version = ?", versionB).
		Delete(&model.ProviderChannelCredentialSetVersion{}).Error; err == nil {
		t.Fatal("PostgreSQL guard accepted channel credential version delete")
	}
	if err := integrationDB.Exec("TRUNCATE TABLE provider_channel_credential_set_versions").Error; err == nil {
		t.Fatal("PostgreSQL guard accepted channel credential version truncate")
	}

	keyringPath := filepath.Join(t.TempDir(), "provider-channel-keyring.json")
	requireNoError(t, os.WriteFile(keyringPath, []byte(providerChannelKeyringB), 0o600))
	inlineBefore, inlinePresent := os.LookupEnv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON")
	requireNoError(t, os.Unsetenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON"))
	t.Cleanup(func() {
		if inlinePresent {
			_ = os.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON", inlineBefore)
		} else {
			_ = os.Unsetenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON")
		}
	})
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE", keyringPath)
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "production")
	requireNoError(t, model.ValidateProviderCredentialVaultRuntime(true))
}

func TestProviderChannelCredentialPostgresMigrationFailureRollsBackAllRowsAndGuards(t *testing.T) {
	resetIntegrationState(t)
	setProviderChannelCredentialDevelopmentKeyring(t, providerChannelKeyringA)
	requireNoError(t, integrationDB.Exec("DROP TRIGGER IF EXISTS trg_channels_provider_credential_storage ON channels").Error)

	firstID := insertLegacyProviderChannel(t, integrationDB, "rollback-legacy-one", "legacy-one-plaintext", "")
	secondID := insertLegacyProviderChannel(t, integrationDB, "rollback-legacy-two", "legacy-two-plaintext", uuid.NewString())
	t.Cleanup(func() {
		_ = integrationDB.Exec("DROP TRIGGER IF EXISTS trg_channels_provider_credential_storage ON channels").Error
		_ = integrationDB.Exec("UPDATE channels SET credential_set_version = '' WHERE id = ?", secondID).Error
		_ = model.MigrateProviderChannelCredentialVaultStorage()
	})

	var before int64
	requireNoError(t, integrationDB.Table("provider_channel_credential_set_versions").Count(&before).Error)
	if err := model.MigrateProviderChannelCredentialVaultStorage(); err == nil {
		t.Fatal("migration accepted a channel with an invalid encrypted credential reference")
	}
	var after int64
	requireNoError(t, integrationDB.Table("provider_channel_credential_set_versions").Count(&after).Error)
	if after != before {
		t.Fatal("failed migration committed a partial channel credential version")
	}

	var first struct {
		LegacyKey            string `gorm:"column:key"`
		CredentialSetVersion string
	}
	requireNoError(t, integrationDB.Table("channels").Select("key, credential_set_version").
		Where("id = ?", firstID).Take(&first).Error)
	if first.LegacyKey != "legacy-one-plaintext" || first.CredentialSetVersion != "" {
		t.Fatal("failed migration cleared or partially converted an earlier legacy row")
	}
	var guardCount int64
	requireNoError(t, integrationDB.Raw(`
		SELECT COUNT(*) FROM pg_trigger
		WHERE tgname = 'trg_channels_provider_credential_storage' AND NOT tgisinternal
	`).Scan(&guardCount).Error)
	if guardCount != 0 {
		t.Fatal("failed migration committed the plaintext guard outside its transaction")
	}
}

func TestProviderChannelCredentialPostgresMigrationFencesConcurrentLegacyWriter(t *testing.T) {
	resetIntegrationState(t)
	setProviderChannelCredentialDevelopmentKeyring(t, providerChannelKeyringA)
	requireNoError(t, integrationDB.Exec("DROP TRIGGER IF EXISTS trg_channels_provider_credential_storage ON channels").Error)
	t.Cleanup(func() { _ = model.MigrateProviderChannelCredentialVaultStorage() })

	oldPodTx := integrationDB.Begin()
	requireNoError(t, oldPodTx.Error)
	t.Cleanup(func() { _ = oldPodTx.Rollback().Error })
	firstName := "legacy-channel-before-lock-" + uuid.NewString()
	firstID := insertLegacyProviderChannel(t, oldPodTx, firstName, "legacy-before-lock-plaintext", "")

	migrationResult := make(chan error, 1)
	go func() {
		migrationResult <- model.MigrateProviderChannelCredentialVaultStorage()
	}()

	deadline := time.Now().Add(5 * time.Second)
	for {
		var waiting int64
		requireNoError(t, integrationDB.Raw(`
			SELECT COUNT(*)
			FROM pg_locks locks
			JOIN pg_class relation ON relation.oid = locks.relation
			WHERE relation.relname = 'channels'
			  AND relation.relnamespace = current_schema()::regnamespace
			  AND locks.mode = 'ShareRowExclusiveLock'
			  AND NOT locks.granted
		`).Scan(&waiting).Error)
		if waiting > 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("provider channel credential migration did not wait on the expected table fence")
		}
		time.Sleep(10 * time.Millisecond)
	}

	secondName := "legacy-channel-after-lock-" + uuid.NewString()
	blockedWriterResult := make(chan error, 1)
	go func() {
		blockedWriterResult <- integrationDB.Exec(
			`INSERT INTO channels (type, key, status, name, models, "group") VALUES (?, ?, ?, ?, ?, ?)`,
			constant.ChannelTypeOpenAI, "legacy-after-lock-plaintext", common.ChannelStatusEnabled,
			secondName, "gpt-test", "default",
		).Error
	}()

	requireNoError(t, oldPodTx.Commit().Error)
	select {
	case err := <-migrationResult:
		requireNoError(t, err)
	case <-time.After(10 * time.Second):
		t.Fatal("provider channel credential migration did not finish")
	}
	select {
	case err := <-blockedWriterResult:
		if err == nil {
			t.Fatal("legacy channel writer crossed the migration commit boundary before the plaintext guard")
		}
	case <-time.After(10 * time.Second):
		t.Fatal("legacy channel writer remained blocked after migration commit")
	}

	var migrated struct {
		LegacyKey            string `gorm:"column:key"`
		CredentialSetVersion string
	}
	requireNoError(t, integrationDB.Table("channels").Select("key, credential_set_version").
		Where("id = ?", firstID).Take(&migrated).Error)
	if migrated.LegacyKey != "" || migrated.CredentialSetVersion == "" {
		t.Fatal("row committed before the table fence was not atomically converted")
	}
	var secondCount int64
	requireNoError(t, integrationDB.Table("channels").Where("name = ?", secondName).Count(&secondCount).Error)
	if secondCount != 0 {
		t.Fatal("plaintext channel row was persisted after migration guard commit")
	}
}

func insertLegacyProviderChannel(t *testing.T, tx *gorm.DB, name string, raw string, version string) int {
	t.Helper()
	var id int
	requireNoError(t, tx.Raw(
		`INSERT INTO channels (type, key, credential_set_version, status, name, models, "group")
		 VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id`,
		constant.ChannelTypeOpenAI, raw, version, common.ChannelStatusEnabled, name, "gpt-test", "default",
	).Scan(&id).Error)
	if id <= 0 {
		t.Fatal("legacy channel insert did not return an id")
	}
	return id
}
