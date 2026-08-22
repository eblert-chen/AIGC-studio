package model

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/google/uuid"
	"gorm.io/gorm"
)

const (
	providerChannelCredentialSetSchemaVersion = 1
	providerChannelCredentialMigrationLock    = int64(0x5043435641554c54)
)

// ProviderChannelCredentialSetVersion is an immutable encrypted snapshot of
// the complete native Channel.Key byte string. Encrypting the whole value (and
// not a normalized list) preserves upstream newline, JSON-array, Vertex JSON,
// and key-index semantics exactly. Only CredentialSetVersion is stored on the
// mutable channels row.
type ProviderChannelCredentialSetVersion struct {
	CredentialSetVersion string    `json:"-" gorm:"column:credential_set_version;type:varchar(36);primaryKey"`
	ChannelID            int       `json:"-" gorm:"not null;index"`
	KeyID                string    `json:"-" gorm:"type:varchar(64);not null;index"`
	SchemaVersion        int       `json:"-" gorm:"not null"`
	KeySetFingerprint    string    `json:"-" gorm:"type:char(64);not null;index"`
	KeyCount             int       `json:"-" gorm:"not null"`
	Nonce                []byte    `json:"-" gorm:"not null"`
	Ciphertext           []byte    `json:"-" gorm:"not null"`
	CreatedAt            time.Time `json:"-" gorm:"not null"`
}

func (ProviderChannelCredentialSetVersion) TableName() string {
	return "provider_channel_credential_set_versions"
}

type legacyProviderChannelCredentialRow struct {
	ID                   int
	LegacyKey            string `gorm:"column:key"`
	CredentialSetVersion string
}

func providerChannelCredentialSetAAD(row ProviderChannelCredentialSetVersion) []byte {
	return []byte(strings.Join([]string{
		"new-api-provider-channel-credential-set",
		strconv.Itoa(row.SchemaVersion),
		row.CredentialSetVersion,
		strconv.Itoa(row.ChannelID),
		row.KeyID,
		row.KeySetFingerprint,
		strconv.Itoa(row.KeyCount),
	}, "\x00"))
}

func providerChannelCredentialKeyCount(raw string) (int, error) {
	if raw == "" {
		return 0, nil
	}
	trimmed := strings.TrimSpace(raw)
	if strings.HasPrefix(trimmed, "[") {
		var values []json.RawMessage
		if err := json.Unmarshal([]byte(trimmed), &values); err != nil {
			return 0, errors.New("provider channel credential key set is invalid")
		}
		if len(values) == 0 {
			return 0, errors.New("provider channel credential key set is empty")
		}
		return len(values), nil
	}
	return len(strings.Split(strings.Trim(raw, "\n"), "\n")), nil
}

func providerChannelCredentialFingerprint(raw string) string {
	digest := sha256.Sum256([]byte(raw))
	return fmt.Sprintf("%x", digest[:])
}

// ProviderChannelCredentialFingerprintPrefix returns non-secret operator
// metadata. It never returns any part of the provider credential itself.
func ProviderChannelCredentialFingerprintPrefix(raw string, length int) string {
	fingerprint := providerChannelCredentialFingerprint(raw)
	if length < 1 || length > len(fingerprint) {
		return fingerprint
	}
	return fingerprint[:length]
}

func storeProviderChannelCredentialSetVersionTx(tx *gorm.DB, channelID int, raw string) (string, error) {
	if tx == nil || channelID <= 0 || raw == "" {
		return "", errors.New("provider channel credential set identity is invalid")
	}
	keyCount, err := providerChannelCredentialKeyCount(raw)
	if err != nil {
		return "", err
	}
	if keyCount <= 0 {
		return "", errors.New("provider channel credential set is empty")
	}
	keyring, err := loadProviderCredentialKeyring(providerCredentialSecureEnvironment())
	if err != nil {
		return "", err
	}
	defer keyring.clear()

	row := ProviderChannelCredentialSetVersion{
		CredentialSetVersion: uuid.NewString(),
		ChannelID:            channelID,
		KeyID:                keyring.activeKeyID,
		SchemaVersion:        providerChannelCredentialSetSchemaVersion,
		KeySetFingerprint:    providerChannelCredentialFingerprint(raw),
		KeyCount:             keyCount,
		CreatedAt:            time.Now().UTC(),
	}
	aead, err := providerCredentialAEAD(keyring.keys[row.KeyID])
	if err != nil {
		return "", err
	}
	row.Nonce = make([]byte, aead.NonceSize())
	if _, err := io.ReadFull(rand.Reader, row.Nonce); err != nil {
		return "", errors.New("provider channel credential nonce generation failed")
	}
	plaintext := []byte(raw)
	row.Ciphertext = aead.Seal(nil, row.Nonce, plaintext, providerChannelCredentialSetAAD(row))
	clear(plaintext)
	if err := tx.Session(&gorm.Session{NewDB: true, SkipHooks: true}).Create(&row).Error; err != nil {
		return "", errors.New("provider channel credential set version could not be stored")
	}
	return row.CredentialSetVersion, nil
}

func resolveProviderChannelCredentialSetVersionRowWithKeyring(
	row ProviderChannelCredentialSetVersion,
	keyring *providerCredentialKeyring,
) ([]byte, error) {
	if row.SchemaVersion != providerChannelCredentialSetSchemaVersion ||
		row.CredentialSetVersion == "" || row.ChannelID <= 0 ||
		!providerCredentialKeyIDPattern.MatchString(row.KeyID) ||
		len(row.KeySetFingerprint) != sha256.Size*2 || row.KeyCount <= 0 {
		return nil, errors.New("provider channel credential set metadata is invalid")
	}
	if keyring == nil {
		return nil, errors.New("provider channel credential keyring is unavailable")
	}
	key, ok := keyring.keys[row.KeyID]
	if !ok {
		return nil, errors.New("provider channel credential KEK is unavailable")
	}
	aead, err := providerCredentialAEAD(key)
	if err != nil {
		return nil, err
	}
	if len(row.Nonce) != aead.NonceSize() || len(row.Ciphertext) <= aead.Overhead() {
		return nil, errors.New("provider channel credential ciphertext is invalid")
	}
	plaintext, err := aead.Open(nil, row.Nonce, row.Ciphertext, providerChannelCredentialSetAAD(row))
	if err != nil {
		return nil, errors.New("provider channel credential authentication failed")
	}
	fingerprint := providerChannelCredentialFingerprint(string(plaintext))
	keyCount, countErr := providerChannelCredentialKeyCount(string(plaintext))
	if countErr != nil || keyCount != row.KeyCount ||
		subtle.ConstantTimeCompare([]byte(fingerprint), []byte(row.KeySetFingerprint)) != 1 {
		clear(plaintext)
		return nil, errors.New("provider channel credential plaintext identity is invalid")
	}
	return plaintext, nil
}

func resolveProviderChannelCredentialSetVersionRow(row ProviderChannelCredentialSetVersion) ([]byte, error) {
	keyring, err := loadProviderCredentialKeyring(providerCredentialSecureEnvironment())
	if err != nil {
		return nil, err
	}
	defer keyring.clear()
	return resolveProviderChannelCredentialSetVersionRowWithKeyring(row, keyring)
}

func loadProviderChannelCredentialSetVersionTx(tx *gorm.DB, version string) (ProviderChannelCredentialSetVersion, error) {
	var row ProviderChannelCredentialSetVersion
	if tx == nil || version == "" {
		return row, errors.New("provider channel credential set version is missing")
	}
	if err := tx.Session(&gorm.Session{NewDB: true, SkipHooks: true}).
		Where("credential_set_version = ?", version).First(&row).Error; err != nil {
		return row, errors.New("provider channel credential set version is unavailable")
	}
	return row, nil
}

// HydrateChannelCredential decrypts a cloned/persisted channel for immediate
// internal use. Callers must never cache, log, or serialize the returned Key.
func HydrateChannelCredential(tx *gorm.DB, channel *Channel) error {
	if channel == nil {
		return errors.New("provider channel credential target is missing")
	}
	if channel.CredentialSetVersion == "" {
		if channel.LegacyKey != "" {
			return errors.New("legacy plaintext provider channel credential is rejected")
		}
		channel.Key = ""
		return nil
	}
	if tx == nil {
		tx = DB
	}
	row, err := loadProviderChannelCredentialSetVersionTx(tx, channel.CredentialSetVersion)
	if err != nil {
		return err
	}
	if row.ChannelID != channel.Id {
		return errors.New("provider channel credential set does not match channel identity")
	}
	plaintext, err := resolveProviderChannelCredentialSetVersionRow(row)
	if err != nil {
		return err
	}
	channel.Key = string(plaintext)
	clear(plaintext)
	channel.LegacyKey = ""
	return nil
}

// AfterFind hydrates only queries that selected the opaque current-version
// pointer. Secret-free list/cache queries omit that pointer and therefore do
// not perform crypto or retain plaintext.
func (channel *Channel) AfterFind(tx *gorm.DB) error {
	if channel == nil || channel.CredentialSetVersion == "" {
		if channel != nil && channel.LegacyKey != "" {
			return errors.New("legacy plaintext provider channel credential is rejected")
		}
		return nil
	}
	return HydrateChannelCredential(tx, channel)
}

func channelCredentialUpdateSelected(tx *gorm.DB) bool {
	if tx == nil || tx.Statement == nil || len(tx.Statement.Selects) == 0 {
		return true
	}
	for _, field := range tx.Statement.Selects {
		switch strings.ToLower(strings.TrimSpace(field)) {
		case "*", "key", "legacy_key", "credential_set_version", "key,credential_set_version":
			return true
		}
	}
	return false
}

func currentProviderChannelCredentialSetTx(tx *gorm.DB, channelID int) (string, error) {
	var current struct {
		CredentialSetVersion string
	}
	if err := tx.Session(&gorm.Session{NewDB: true, SkipHooks: true}).Table("channels").
		Select("credential_set_version").Where("id = ?", channelID).Take(&current).Error; err != nil {
		return "", err
	}
	return current.CredentialSetVersion, nil
}

func prepareProviderChannelCredentialUpdateTx(tx *gorm.DB, channel *Channel) error {
	if channel == nil || channel.Id <= 0 || !channelCredentialUpdateSelected(tx) {
		return nil
	}
	currentVersion, err := currentProviderChannelCredentialSetTx(tx, channel.Id)
	if err != nil {
		return err
	}
	channel.CredentialSetVersion = currentVersion
	if channel.Key == "" {
		// Empty-key native updates mean preserve, never clear.
		return nil
	}

	currentRaw := ""
	currentKeyID := ""
	if currentVersion != "" {
		row, err := loadProviderChannelCredentialSetVersionTx(tx, currentVersion)
		if err != nil {
			return err
		}
		if row.ChannelID != channel.Id {
			return errors.New("provider channel credential set does not match channel identity")
		}
		plaintext, err := resolveProviderChannelCredentialSetVersionRow(row)
		if err != nil {
			return err
		}
		currentRaw = string(plaintext)
		clear(plaintext)
		currentKeyID = row.KeyID
	}

	keyring, err := loadProviderCredentialKeyring(providerCredentialSecureEnvironment())
	if err != nil {
		return err
	}
	activeKeyID := keyring.activeKeyID
	keyring.clear()
	if currentRaw == channel.Key && currentKeyID == activeKeyID {
		return nil
	}
	if currentRaw != "" && currentRaw != channel.Key {
		if err := rejectPlatformGenerationChannelMutationTx(tx, channel.Id); err != nil {
			return err
		}
	}
	version, err := storeProviderChannelCredentialSetVersionTx(tx, channel.Id, channel.Key)
	if err != nil {
		return err
	}
	channel.CredentialSetVersion = version
	return nil
}

func (channel *Channel) AfterCreate(tx *gorm.DB) error {
	if err := rejectProtectedCodexChannelUpdateTx(tx, channel); err != nil {
		return err
	}
	if channel == nil || channel.Id <= 0 || channel.Key == "" {
		return nil
	}
	version, err := storeProviderChannelCredentialSetVersionTx(tx, channel.Id, channel.Key)
	if err != nil {
		return err
	}
	result := tx.Session(&gorm.Session{NewDB: true, SkipHooks: true}).Table("channels").Where("id = ?", channel.Id).
		Update("credential_set_version", version)
	if result.Error != nil || result.RowsAffected != 1 {
		return errors.New("provider channel credential reference could not be installed")
	}
	channel.CredentialSetVersion = version
	channel.LegacyKey = ""
	var current struct {
		ControlRevision int64
	}
	if err := tx.Session(&gorm.Session{NewDB: true, SkipHooks: true}).Table("channels").
		Select("control_revision").Where("id = ?", channel.Id).Take(&current).Error; err != nil {
		return errors.New("provider channel credential reference revision could not be reloaded")
	}
	channel.ControlRevision = current.ControlRevision
	return nil
}

func (channel *Channel) BeforeUpdate(tx *gorm.DB) error {
	if err := rejectProtectedCodexChannelUpdateTx(tx, channel); err != nil {
		return err
	}
	return prepareProviderChannelCredentialUpdateTx(tx, channel)
}

func rejectProtectedCodexChannelUpdateTx(tx *gorm.DB, channel *Channel) error {
	if !RelayDatabaseRoleAttestationRequired() || channel == nil {
		return nil
	}
	effectiveType := channel.Type
	effectiveStatus := channel.Status
	if channel.Id > 0 {
		var current struct {
			Type   int
			Status int
		}
		if err := tx.Session(&gorm.Session{NewDB: true, SkipHooks: true}).Table("channels").
			Select("type", "status").Where("id = ?", channel.Id).Take(&current).Error; err != nil {
			return errors.New("protected Relay could not validate Codex channel lifecycle state")
		}
		if effectiveType == 0 {
			effectiveType = current.Type
		}
		if effectiveStatus == 0 {
			effectiveStatus = current.Status
		}
	}
	if effectiveType == constant.ChannelTypeCodex &&
		(effectiveStatus == common.ChannelStatusEnabled || effectiveStatus == common.ChannelStatusAutoDisabled) {
		return errors.New("protected Relay rejects active Codex channels until credential refresh reconciliation is implemented")
	}
	return nil
}

// RotateChannelCredentialSet is the narrow atomic write boundary for native
// code paths (for example OAuth refresh) that previously updated channels.key
// directly. The current row is locked, active generation use is fenced when
// the provider bytes change, and only an immutable version reference changes.
func RotateChannelCredentialSet(channelID int, raw string) error {
	if DB == nil || channelID <= 0 || raw == "" {
		return errors.New("provider channel credential rotation is invalid")
	}
	return DB.Transaction(func(tx *gorm.DB) error {
		var channel Channel
		if err := lockForUpdate(tx.Session(&gorm.Session{NewDB: true, SkipHooks: true}).Where("id = ?", channelID)).First(&channel).Error; err != nil {
			return err
		}
		channel.Key = raw
		if err := prepareProviderChannelCredentialUpdateTx(tx, &channel); err != nil {
			return err
		}
		if channel.CredentialSetVersion == "" {
			return errors.New("provider channel credential version was not created")
		}
		result := tx.Session(&gorm.Session{NewDB: true, SkipHooks: true}).Table("channels").Where("id = ?", channelID).
			Update("credential_set_version", channel.CredentialSetVersion)
		if result.Error != nil || result.RowsAffected != 1 {
			return errors.New("provider channel credential reference could not be rotated")
		}
		return nil
	})
}

// MigrateProviderChannelCredentialVaultStorage atomically converts the legacy
// channels.key column, clears it, and installs both plaintext-write and
// append-only guards before commit. A concurrent old pod is fenced by the table
// lock and resumes only after the guard exists, at which point its write fails.
func MigrateProviderChannelCredentialVaultStorage() error {
	return MigrateProviderChannelCredentialVaultStorageWithDB(DB)
}

func MigrateProviderChannelCredentialVaultStorageWithDB(db *gorm.DB) error {
	if db == nil {
		return errors.New("provider channel credential database is not initialized")
	}
	return db.Transaction(func(tx *gorm.DB) error {
		if db.Dialector.Name() == "postgres" {
			if err := tx.Exec("SELECT pg_advisory_xact_lock(?)", providerChannelCredentialMigrationLock).Error; err != nil {
				return errors.New("provider channel credential migration lock could not be acquired")
			}
			if err := tx.Exec("LOCK TABLE channels, provider_channel_credential_set_versions IN SHARE ROW EXCLUSIVE MODE").Error; err != nil {
				return errors.New("provider channel credential migration tables could not be fenced")
			}
			if err := tx.Exec("ALTER TABLE channels ALTER COLUMN key SET DEFAULT ''").Error; err != nil {
				return errors.New("provider channel credential legacy column default could not be normalized")
			}
		} else if db.Dialector.Name() == "sqlite" {
			if err := tx.Exec("UPDATE provider_channel_credential_set_versions SET credential_set_version = credential_set_version WHERE 1 = 0").Error; err != nil {
				return errors.New("provider channel credential SQLite migration could not reserve the writer")
			}
		}
		if err := migrateLegacyProviderChannelCredentialsTx(tx); err != nil {
			return err
		}
		if db.Dialector.Name() == "postgres" {
			return installProviderChannelCredentialPostgresGuardsTx(tx)
		}
		if db.Dialector.Name() == "sqlite" {
			return installProviderChannelCredentialSQLiteGuardsTx(tx)
		}
		return nil
	})
}

func migrateLegacyProviderChannelCredentialsTx(tx *gorm.DB) error {
	var rows []legacyProviderChannelCredentialRow
	if err := tx.Session(&gorm.Session{NewDB: true, SkipHooks: true}).Table("channels").
		Select("id, key, credential_set_version").Order("id ASC").Find(&rows).Error; err != nil {
		return errors.New("legacy provider channel credentials could not be inspected")
	}
	for _, row := range rows {
		version := strings.TrimSpace(row.CredentialSetVersion)
		if version != "" {
			stored, err := loadProviderChannelCredentialSetVersionTx(tx, version)
			if err != nil || stored.ChannelID != row.ID {
				return errors.New("provider channel credential reference is invalid")
			}
			plaintext, err := resolveProviderChannelCredentialSetVersionRow(stored)
			if err != nil {
				return err
			}
			if row.LegacyKey != "" && subtle.ConstantTimeCompare(plaintext, []byte(row.LegacyKey)) != 1 {
				clear(plaintext)
				return errors.New("legacy provider channel credential conflicts with encrypted version")
			}
			clear(plaintext)
		} else if row.LegacyKey != "" {
			created, err := storeProviderChannelCredentialSetVersionTx(tx, row.ID, row.LegacyKey)
			if err != nil {
				return err
			}
			version = created
		}
		if row.LegacyKey != "" {
			result := tx.Session(&gorm.Session{NewDB: true, SkipHooks: true}).Table("channels").Where("id = ?", row.ID).
				Updates(map[string]any{"key": "", "credential_set_version": version})
			if result.Error != nil || result.RowsAffected != 1 {
				return errors.New("legacy provider channel credential could not be replaced")
			}
		}
	}
	return nil
}

func installProviderChannelCredentialPostgresGuardsTx(tx *gorm.DB) error {
	statements := []string{
		`CREATE OR REPLACE FUNCTION enforce_provider_channel_credential_storage()
RETURNS trigger AS $$
BEGIN
    IF COALESCE(NEW.key, '') <> '' THEN
        RAISE EXCEPTION 'plaintext provider channel credentials are forbidden';
    END IF;
    IF COALESCE(NEW.credential_set_version, '') <> '' AND NOT EXISTS (
        SELECT 1 FROM provider_channel_credential_set_versions versions
         WHERE versions.credential_set_version = NEW.credential_set_version
           AND versions.channel_id = NEW.id
    ) THEN
        RAISE EXCEPTION 'provider channel credential reference is invalid';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql`,
		`DROP TRIGGER IF EXISTS trg_channels_provider_credential_storage ON channels`,
		`CREATE TRIGGER trg_channels_provider_credential_storage BEFORE INSERT OR UPDATE OF key, credential_set_version ON channels FOR EACH ROW EXECUTE FUNCTION enforce_provider_channel_credential_storage()`,
		`CREATE OR REPLACE FUNCTION reject_provider_channel_credential_set_version_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'provider channel credential set versions are immutable';
END;
$$ LANGUAGE plpgsql`,
		`DROP TRIGGER IF EXISTS trg_provider_channel_credential_set_versions_no_update_delete ON provider_channel_credential_set_versions`,
		`CREATE TRIGGER trg_provider_channel_credential_set_versions_no_update_delete BEFORE UPDATE OR DELETE ON provider_channel_credential_set_versions FOR EACH ROW EXECUTE FUNCTION reject_provider_channel_credential_set_version_mutation()`,
		`DROP TRIGGER IF EXISTS trg_provider_channel_credential_set_versions_no_truncate ON provider_channel_credential_set_versions`,
		`CREATE TRIGGER trg_provider_channel_credential_set_versions_no_truncate BEFORE TRUNCATE ON provider_channel_credential_set_versions FOR EACH STATEMENT EXECUTE FUNCTION reject_provider_channel_credential_set_version_mutation()`,
	}
	for _, statement := range statements {
		if err := tx.Exec(statement).Error; err != nil {
			return errors.New("provider channel credential PostgreSQL guard installation failed")
		}
	}
	return nil
}

func installProviderChannelCredentialSQLiteGuardsTx(tx *gorm.DB) error {
	statements := []string{
		`DROP TRIGGER IF EXISTS trg_channels_provider_credential_storage_insert`,
		`CREATE TRIGGER trg_channels_provider_credential_storage_insert BEFORE INSERT ON channels
WHEN COALESCE(NEW.key, '') <> '' OR (COALESCE(NEW.credential_set_version, '') <> '' AND NOT EXISTS (
    SELECT 1 FROM provider_channel_credential_set_versions versions
     WHERE versions.credential_set_version = NEW.credential_set_version AND versions.channel_id = NEW.id
)) BEGIN SELECT RAISE(ABORT, 'invalid provider channel credential storage'); END`,
		`DROP TRIGGER IF EXISTS trg_channels_provider_credential_storage_update`,
		`CREATE TRIGGER trg_channels_provider_credential_storage_update BEFORE UPDATE OF key, credential_set_version ON channels
WHEN COALESCE(NEW.key, '') <> '' OR (COALESCE(NEW.credential_set_version, '') <> '' AND NOT EXISTS (
    SELECT 1 FROM provider_channel_credential_set_versions versions
     WHERE versions.credential_set_version = NEW.credential_set_version AND versions.channel_id = NEW.id
)) BEGIN SELECT RAISE(ABORT, 'invalid provider channel credential storage'); END`,
		`DROP TRIGGER IF EXISTS trg_provider_channel_credential_set_versions_no_update`,
		`CREATE TRIGGER trg_provider_channel_credential_set_versions_no_update BEFORE UPDATE ON provider_channel_credential_set_versions BEGIN SELECT RAISE(ABORT, 'provider channel credential set versions are immutable'); END`,
		`DROP TRIGGER IF EXISTS trg_provider_channel_credential_set_versions_no_delete`,
		`CREATE TRIGGER trg_provider_channel_credential_set_versions_no_delete BEFORE DELETE ON provider_channel_credential_set_versions BEGIN SELECT RAISE(ABORT, 'provider channel credential set versions are immutable'); END`,
	}
	for _, statement := range statements {
		if err := tx.Exec(statement).Error; err != nil {
			return errors.New("provider channel credential SQLite guard installation failed")
		}
	}
	return nil
}

func validateProviderChannelCredentialVaultRuntime(production bool) error {
	if !production {
		return nil
	}
	var plaintextCount int64
	if err := DB.Session(&gorm.Session{SkipHooks: true}).Table("channels").
		Where("COALESCE(key, '') <> ''").Count(&plaintextCount).Error; err != nil {
		return errors.New("provider channel credential readiness could not inspect legacy storage")
	}
	if plaintextCount != 0 {
		return errors.New("provider channel credential readiness found plaintext native channel keys")
	}
	var channels []struct {
		ID                   int
		CredentialSetVersion string
	}
	if err := DB.Session(&gorm.Session{SkipHooks: true}).Table("channels").
		Select("id, credential_set_version").Order("id ASC").Find(&channels).Error; err != nil {
		return errors.New("provider channel credential readiness could not inspect channel references")
	}
	keyring, err := loadProviderCredentialKeyring(true)
	if err != nil {
		return err
	}
	defer keyring.clear()
	for _, channel := range channels {
		if channel.ID <= 0 || strings.TrimSpace(channel.CredentialSetVersion) == "" {
			return errors.New("provider channel credential readiness found a channel without an encrypted credential set")
		}
		row, err := loadProviderChannelCredentialSetVersionTx(DB, channel.CredentialSetVersion)
		if err != nil || row.ChannelID != channel.ID {
			return errors.New("provider channel credential readiness found an invalid channel reference")
		}
		plaintext, err := resolveProviderChannelCredentialSetVersionRowWithKeyring(row, keyring)
		if err != nil {
			return err
		}
		clear(plaintext)
	}
	return nil
}
