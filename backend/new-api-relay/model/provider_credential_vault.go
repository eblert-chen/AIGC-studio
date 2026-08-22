package model

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

const (
	providerCredentialEncryptionVersion = 1
	providerCredentialKeyringSchema     = 1
	providerCredentialKeyringMaxBytes   = 64 * 1024
	// Readiness streams active rows but retains one small identity per distinct
	// credential version so task and recovery references can be cross-checked.
	// Fail closed before an unbounded corrupt/backlogged dataset can exhaust the
	// API process that serves health probes.
	providerCredentialReadinessMaxActiveVersions = 100_000

	providerCredentialKeyringFileEnvironment = "RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE"
	providerCredentialKeyringJSONEnvironment = "RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON"

	// ProviderCredentialNativeTenantScope isolates native new-api tasks that do
	// not belong to the customer Platform's tenant boundary. Platform jobs must
	// always use their canonical UUID tenant instead.
	ProviderCredentialNativeTenantScope = "new-api-native"
)

var providerCredentialKeyIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`)

var readProviderCredentialKeyringFile = common.ReadProtectedSecretFile

// ProviderCredentialVersion is an immutable encrypted snapshot of one exact
// provider credential. CredentialVersion is the only value retained by a
// long-running Task or recovery record. Key material is authenticated with AAD
// binding it to tenant, channel, key slot, fingerprint, KEK id, and format
// version, so database-field substitution fails authentication.
type ProviderCredentialVersion struct {
	CredentialVersion string    `json:"-" gorm:"column:credential_version;type:varchar(36);primaryKey"`
	TenantID          string    `json:"-" gorm:"type:varchar(64);not null;uniqueIndex:uniq_provider_credential_identity,priority:1"`
	ChannelID         int       `json:"-" gorm:"not null;uniqueIndex:uniq_provider_credential_identity,priority:2"`
	KeyIndex          int       `json:"-" gorm:"not null;uniqueIndex:uniq_provider_credential_identity,priority:3"`
	KeyFingerprint    string    `json:"-" gorm:"type:varchar(64);not null;uniqueIndex:uniq_provider_credential_identity,priority:4"`
	KeyID             string    `json:"-" gorm:"type:varchar(64);not null;uniqueIndex:uniq_provider_credential_identity,priority:5"`
	Version           int       `json:"-" gorm:"not null"`
	Nonce             []byte    `json:"-" gorm:"not null"`
	Ciphertext        []byte    `json:"-" gorm:"not null"`
	CreatedAt         time.Time `json:"-" gorm:"not null"`
}

type providerCredentialKeyringDocument struct {
	SchemaVersion int               `json:"schema_version"`
	ActiveKeyID   string            `json:"active_key_id"`
	Keys          map[string]string `json:"keys"`
}

type providerCredentialKeyring struct {
	activeKeyID string
	keys        map[string][]byte
}

// ProviderCredentialIsolationRepresentation is a short-lived secret view used
// only by the offline cross-process isolation validator. Callers must clear
// Value after hashing it and must never log either field together with the
// source document. ID is stable, non-secret metadata for commitment receipts.
type ProviderCredentialIsolationRepresentation struct {
	ID    string
	Value []byte
}

type providerCredentialLegacyTaskRow struct {
	ID          int64
	TaskID      string
	ChannelID   int
	PrivateData string
}

type providerCredentialLegacyRecoveryRow struct {
	ID                     string
	TenantID               string
	NativeTaskRecoveryJSON string
}

type providerCredentialRuntimeReference struct {
	CredentialVersion string
	TenantID          string
	ChannelID         int
	KeyIndex          int
	KeyFingerprint    string
}

func providerCredentialSecureEnvironment() bool {
	if RelayDatabaseRoleAttestationRequired() || RelayDatabaseSecretFilesRequired() {
		return true
	}
	for _, name := range []string{"RELAY_COMPAT_ENVIRONMENT", "APP_ENV", "DEPLOYMENT_ENV", "ENVIRONMENT"} {
		switch strings.ToLower(strings.TrimSpace(os.Getenv(name))) {
		case "staging", "production":
			return true
		}
	}
	return false
}

func loadProviderCredentialKeyring(production bool) (*providerCredentialKeyring, error) {
	production = production || providerCredentialSecureEnvironment()
	filePath := os.Getenv(providerCredentialKeyringFileEnvironment)
	inline, inlinePresent := os.LookupEnv(providerCredentialKeyringJSONEnvironment)
	if production && inlinePresent {
		return nil, errors.New("provider credential keyring inline configuration is forbidden in secure environments")
	}
	if filePath != "" && inlinePresent {
		return nil, errors.New("provider credential keyring must use exactly one configuration source")
	}

	var raw []byte
	if filePath != "" {
		if production {
			var err error
			raw, err = readProviderCredentialKeyringFile(providerCredentialKeyringFileEnvironment, providerCredentialKeyringMaxBytes)
			if err != nil {
				return nil, errors.New("provider credential keyring file is unavailable or invalid")
			}
		} else {
			info, err := os.Lstat(filePath)
			if err != nil || info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() ||
				info.Size() < 1 || info.Size() > providerCredentialKeyringMaxBytes {
				return nil, errors.New("provider credential keyring file is unavailable or invalid")
			}
			raw, err = os.ReadFile(filePath)
			if err != nil {
				return nil, errors.New("provider credential keyring file could not be read")
			}
		}
	} else {
		if production {
			return nil, errors.New("secure provider credential storage requires RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE")
		}
		raw = []byte(inline)
	}
	defer clear(raw)
	if len(raw) == 0 {
		return nil, errors.New("provider credential keyring is not configured")
	}

	document, err := parseProviderCredentialKeyringDocument(raw)
	if err != nil {
		return nil, errors.New("provider credential keyring document is invalid")
	}
	if document.SchemaVersion != providerCredentialKeyringSchema ||
		!providerCredentialKeyIDPattern.MatchString(document.ActiveKeyID) ||
		len(document.Keys) == 0 || len(document.Keys) > 16 {
		return nil, errors.New("provider credential keyring metadata is invalid")
	}

	keyring := &providerCredentialKeyring{activeKeyID: document.ActiveKeyID, keys: make(map[string][]byte, len(document.Keys))}
	decodedDigests := make(map[[sha256.Size]byte]struct{}, len(document.Keys))
	for keyID, encoded := range document.Keys {
		if !providerCredentialKeyIDPattern.MatchString(keyID) {
			keyring.clear()
			return nil, errors.New("provider credential keyring contains an invalid key id")
		}
		key, err := base64.StdEncoding.Strict().DecodeString(encoded)
		if err != nil || len(key) != 32 || base64.StdEncoding.EncodeToString(key) != encoded ||
			!providerCredentialKeyDiverse(key) {
			clear(key)
			keyring.clear()
			return nil, errors.New("provider credential KEKs must be base64-encoded 32-byte values")
		}
		digest := sha256.Sum256(key)
		if _, duplicate := decodedDigests[digest]; duplicate {
			clear(key)
			keyring.clear()
			return nil, errors.New("provider credential KEKs must be distinct")
		}
		decodedDigests[digest] = struct{}{}
		if providerCredentialKeyReusesApplicationSecret(key, encoded) {
			clear(key)
			keyring.clear()
			return nil, errors.New("provider credential KEK must be independent from application and session secrets")
		}
		keyring.keys[keyID] = key
	}
	if _, ok := keyring.keys[keyring.activeKeyID]; !ok {
		keyring.clear()
		return nil, errors.New("provider credential keyring active key is missing")
	}
	return keyring, nil
}

func parseProviderCredentialKeyringDocument(raw []byte) (providerCredentialKeyringDocument, error) {
	var envelope struct {
		SchemaVersion int             `json:"schema_version"`
		ActiveKeyID   string          `json:"active_key_id"`
		Keys          json.RawMessage `json:"keys"`
	}
	if common.RejectDuplicateJSONKeys(raw) != nil ||
		common.DecodeJsonDisallowUnknownFields(bytes.NewReader(raw), &envelope) != nil {
		return providerCredentialKeyringDocument{}, errors.New("invalid keyring")
	}
	decoder := json.NewDecoder(bytes.NewReader(envelope.Keys))
	token, err := decoder.Token()
	if err != nil || token != json.Delim('{') {
		return providerCredentialKeyringDocument{}, errors.New("invalid keyring")
	}
	keys := make(map[string]string)
	previousKeyID := ""
	for decoder.More() {
		keyToken, tokenErr := decoder.Token()
		keyID, ok := keyToken.(string)
		if tokenErr != nil || !ok || keyID <= previousKeyID {
			return providerCredentialKeyringDocument{}, errors.New("invalid keyring")
		}
		var encoded string
		if decoder.Decode(&encoded) != nil {
			return providerCredentialKeyringDocument{}, errors.New("invalid keyring")
		}
		keys[keyID] = encoded
		previousKeyID = keyID
	}
	if token, err = decoder.Token(); err != nil || token != json.Delim('}') {
		return providerCredentialKeyringDocument{}, errors.New("invalid keyring")
	}
	var trailing any
	if decoder.Decode(&trailing) != io.EOF {
		return providerCredentialKeyringDocument{}, errors.New("invalid keyring")
	}
	return providerCredentialKeyringDocument{
		SchemaVersion: envelope.SchemaVersion,
		ActiveKeyID:   envelope.ActiveKeyID,
		Keys:          keys,
	}, nil
}

// ProviderCredentialKeyringIsolationRepresentations validates the exact
// canonical keyring document and returns both its encoded and decoded KEK
// representations. This lets the offline validator reject reuse across A/B/C,
// database passwords and KEKs without exposing the keyring to other runtime
// containers. The ordinary runtime loader remains the encryption boundary.
func ProviderCredentialKeyringIsolationRepresentations(raw []byte) ([]ProviderCredentialIsolationRepresentation, error) {
	document, err := parseProviderCredentialKeyringDocument(raw)
	if err != nil || document.SchemaVersion != providerCredentialKeyringSchema ||
		!providerCredentialKeyIDPattern.MatchString(document.ActiveKeyID) ||
		len(document.Keys) == 0 || len(document.Keys) > 16 {
		return nil, errors.New("provider credential keyring isolation input is invalid")
	}
	keyIDs := make([]string, 0, len(document.Keys))
	for keyID := range document.Keys {
		keyIDs = append(keyIDs, keyID)
	}
	sort.Strings(keyIDs)
	representations := make([]ProviderCredentialIsolationRepresentation, 0, len(keyIDs)*2)
	decodedDigests := make(map[[sha256.Size]byte]struct{}, len(keyIDs))
	clearRepresentations := func() {
		for index := range representations {
			clear(representations[index].Value)
		}
	}
	for _, keyID := range keyIDs {
		if !providerCredentialKeyIDPattern.MatchString(keyID) {
			clearRepresentations()
			return nil, errors.New("provider credential keyring isolation input is invalid")
		}
		encoded := document.Keys[keyID]
		decoded, decodeErr := base64.StdEncoding.Strict().DecodeString(encoded)
		if decodeErr != nil || len(decoded) != 32 || base64.StdEncoding.EncodeToString(decoded) != encoded ||
			!providerCredentialKeyDiverse(decoded) {
			clear(decoded)
			clearRepresentations()
			return nil, errors.New("provider credential keyring isolation input is invalid")
		}
		digest := sha256.Sum256(decoded)
		if _, duplicate := decodedDigests[digest]; duplicate {
			clear(decoded)
			clearRepresentations()
			return nil, errors.New("provider credential keyring isolation input is invalid")
		}
		decodedDigests[digest] = struct{}{}
		representations = append(representations,
			ProviderCredentialIsolationRepresentation{ID: "kek." + keyID + ".encoded", Value: []byte(encoded)},
			ProviderCredentialIsolationRepresentation{ID: "kek." + keyID + ".decoded", Value: decoded},
		)
	}
	if _, present := document.Keys[document.ActiveKeyID]; !present {
		clearRepresentations()
		return nil, errors.New("provider credential keyring isolation input is invalid")
	}
	return representations, nil
}

func providerCredentialKeyDiverse(key []byte) bool {
	distinct := make(map[byte]struct{}, len(key))
	for _, value := range key {
		distinct[value] = struct{}{}
	}
	return len(distinct) >= 8
}

func providerCredentialKeyReusesApplicationSecret(key []byte, encoded string) bool {
	if common.ProtectedRelaySecretDigestRegistered(key) ||
		common.ProtectedRelaySecretDigestRegistered([]byte(encoded)) {
		return true
	}
	values := []string{common.CryptoSecret, common.SessionSecret}
	for _, name := range []string{"CRYPTO_SECRET", "SESSION_SECRET", "JWT_SECRET", "TOKEN_SECRET", "PAYLOAD_SECRET"} {
		values = append(values, os.Getenv(name))
	}
	for _, value := range values {
		if value == "" {
			continue
		}
		candidate := []byte(value)
		if len(candidate) == len(key) && subtle.ConstantTimeCompare(candidate, key) == 1 {
			clear(candidate)
			return true
		}
		clear(candidate)
		decoded, err := base64.StdEncoding.Strict().DecodeString(value)
		if err == nil && len(decoded) == len(key) && subtle.ConstantTimeCompare(decoded, key) == 1 {
			clear(decoded)
			return true
		}
		clear(decoded)
	}
	return false
}

func (keyring *providerCredentialKeyring) clear() {
	if keyring == nil {
		return
	}
	for keyID, key := range keyring.keys {
		clear(key)
		delete(keyring.keys, keyID)
	}
}

func providerCredentialAAD(version ProviderCredentialVersion) []byte {
	return []byte(strings.Join([]string{
		"new-api-provider-credential",
		strconv.Itoa(version.Version),
		version.CredentialVersion,
		version.TenantID,
		strconv.Itoa(version.ChannelID),
		strconv.Itoa(version.KeyIndex),
		version.KeyFingerprint,
		version.KeyID,
	}, "\x00"))
}

func providerCredentialAEAD(key []byte) (cipher.AEAD, error) {
	if len(key) != 32 {
		return nil, errors.New("provider credential KEK must be 32 bytes")
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, errors.New("provider credential cipher initialization failed")
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, errors.New("provider credential authenticated encryption initialization failed")
	}
	return aead, nil
}

func normalizeProviderCredentialIdentity(tenantID string, channelID int, keyIndex int, key string, fingerprint string) (string, error) {
	tenantID = strings.TrimSpace(tenantID)
	if tenantID == "" || len(tenantID) > 64 || channelID <= 0 || keyIndex < 0 || key == "" {
		return "", errors.New("provider credential identity is invalid")
	}
	digest := sha256.Sum256([]byte(key))
	computed := fmt.Sprintf("%x", digest[:])
	if fingerprint == "" {
		fingerprint = computed
	}
	if len(fingerprint) != sha256.Size*2 || strings.ToLower(fingerprint) != fingerprint ||
		subtle.ConstantTimeCompare([]byte(fingerprint), []byte(computed)) != 1 {
		return "", errors.New("provider credential fingerprint does not match key material")
	}
	return fingerprint, nil
}

func storeProviderCredentialVersionTx(
	tx *gorm.DB,
	tenantID string,
	channelID int,
	keyIndex int,
	key string,
	fingerprint string,
) (string, error) {
	if tx == nil {
		return "", errors.New("provider credential database transaction is required")
	}
	fingerprint, err := normalizeProviderCredentialIdentity(tenantID, channelID, keyIndex, key, fingerprint)
	if err != nil {
		return "", err
	}
	keyring, err := loadProviderCredentialKeyring(providerCredentialSecureEnvironment())
	if err != nil {
		return "", err
	}
	defer keyring.clear()

	row := ProviderCredentialVersion{
		CredentialVersion: uuid.NewString(),
		TenantID:          strings.TrimSpace(tenantID),
		ChannelID:         channelID,
		KeyIndex:          keyIndex,
		KeyFingerprint:    fingerprint,
		KeyID:             keyring.activeKeyID,
		Version:           providerCredentialEncryptionVersion,
		CreatedAt:         time.Now().UTC(),
	}
	aead, err := providerCredentialAEAD(keyring.keys[row.KeyID])
	if err != nil {
		return "", err
	}
	row.Nonce = make([]byte, aead.NonceSize())
	if _, err := io.ReadFull(rand.Reader, row.Nonce); err != nil {
		return "", errors.New("provider credential nonce generation failed")
	}
	plaintext := []byte(key)
	row.Ciphertext = aead.Seal(nil, row.Nonce, plaintext, providerCredentialAAD(row))
	clear(plaintext)

	result := tx.Clauses(clause.OnConflict{
		Columns:   []clause.Column{{Name: "tenant_id"}, {Name: "channel_id"}, {Name: "key_index"}, {Name: "key_fingerprint"}, {Name: "key_id"}},
		DoNothing: true,
	}).Create(&row)
	if result.Error != nil {
		return "", errors.New("provider credential version could not be stored")
	}
	if result.RowsAffected == 0 {
		// GORM otherwise carries the candidate primary key into First and adds
		// it to the unique identity predicate, which cannot find the winning
		// concurrent row.
		row = ProviderCredentialVersion{}
		if err := tx.Where(
			"tenant_id = ? AND channel_id = ? AND key_index = ? AND key_fingerprint = ? AND key_id = ?",
			strings.TrimSpace(tenantID), channelID, keyIndex, fingerprint, keyring.activeKeyID,
		).First(&row).Error; err != nil {
			return "", errors.New("provider credential version could not be resolved after concurrent creation")
		}
	}
	resolved, err := resolveProviderCredentialVersionRow(row)
	if err != nil {
		return "", err
	}
	defer clear(resolved)
	expected := []byte(key)
	defer clear(expected)
	if subtle.ConstantTimeCompare(resolved, expected) != 1 {
		return "", errors.New("provider credential identity collision detected")
	}
	return row.CredentialVersion, nil
}

// BindTaskProviderCredentialVersion replaces a transient provider key with an
// immutable encrypted credential-version reference before Task persistence.
func BindTaskProviderCredentialVersion(task *Task, tenantID string) error {
	return DB.Transaction(func(tx *gorm.DB) error {
		return bindTaskProviderCredentialVersionTx(tx, task, tenantID)
	})
}

func bindTaskProviderCredentialVersionTx(tx *gorm.DB, task *Task, tenantID string) error {
	if task == nil {
		return errors.New("provider credential task is required")
	}
	if task.PrivateData.ProviderCredentialVersion != "" {
		if task.PrivateData.TransientProviderKey != "" {
			return errors.New("provider credential task contains both a version reference and transient key")
		}
		if task.PrivateData.ProviderCredentialTenantID != strings.TrimSpace(tenantID) {
			return errors.New("provider credential task tenant does not match its version reference")
		}
		return nil
	}
	if task.PrivateData.TransientProviderKey == "" {
		return nil
	}
	if task.PrivateData.PinnedKeyIndex == nil {
		return errors.New("provider credential task lacks its key index")
	}
	version, err := storeProviderCredentialVersionTx(
		tx,
		tenantID,
		task.ChannelId,
		*task.PrivateData.PinnedKeyIndex,
		task.PrivateData.TransientProviderKey,
		task.PrivateData.PinnedKeyFingerprint,
	)
	if err != nil {
		return err
	}
	task.PrivateData.ProviderCredentialTenantID = strings.TrimSpace(tenantID)
	task.PrivateData.ProviderCredentialVersion = version
	task.PrivateData.TransientProviderKey = ""
	return nil
}

func resolveProviderCredentialVersionRowWithKeyring(
	row ProviderCredentialVersion,
	keyring *providerCredentialKeyring,
) ([]byte, error) {
	if row.Version != providerCredentialEncryptionVersion ||
		row.CredentialVersion == "" || row.TenantID == "" || row.ChannelID <= 0 || row.KeyIndex < 0 ||
		len(row.KeyFingerprint) != sha256.Size*2 || !providerCredentialKeyIDPattern.MatchString(row.KeyID) {
		return nil, errors.New("provider credential version metadata is invalid")
	}
	if keyring == nil {
		return nil, errors.New("provider credential keyring is unavailable")
	}
	key, ok := keyring.keys[row.KeyID]
	if !ok {
		return nil, errors.New("provider credential KEK is unavailable")
	}
	aead, err := providerCredentialAEAD(key)
	if err != nil {
		return nil, err
	}
	if len(row.Nonce) != aead.NonceSize() || len(row.Ciphertext) <= aead.Overhead() {
		return nil, errors.New("provider credential ciphertext is invalid")
	}
	plaintext, err := aead.Open(nil, row.Nonce, row.Ciphertext, providerCredentialAAD(row))
	if err != nil {
		return nil, errors.New("provider credential authentication failed")
	}
	digest := sha256.Sum256(plaintext)
	computed := fmt.Sprintf("%x", digest[:])
	if subtle.ConstantTimeCompare([]byte(computed), []byte(row.KeyFingerprint)) != 1 {
		clear(plaintext)
		return nil, errors.New("provider credential plaintext fingerprint is invalid")
	}
	return plaintext, nil
}

func resolveProviderCredentialVersionRow(row ProviderCredentialVersion) ([]byte, error) {
	keyring, err := loadProviderCredentialKeyring(providerCredentialSecureEnvironment())
	if err != nil {
		return nil, err
	}
	defer keyring.clear()
	return resolveProviderCredentialVersionRowWithKeyring(row, keyring)
}

// ResolveTaskProviderCredential decrypts an exact credential version for the
// immediate provider-call boundary. Callers must never log or persist it.
func ResolveTaskProviderCredential(task *Task) (string, error) {
	if task == nil || task.PrivateData.ProviderCredentialVersion == "" ||
		task.PrivateData.ProviderCredentialTenantID == "" || task.PrivateData.PinnedKeyIndex == nil {
		return "", errors.New("task provider credential version is missing")
	}
	if task.PrivateData.LegacyProviderCredentialPresent {
		return "", errors.New("legacy plaintext task credential is rejected")
	}
	var row ProviderCredentialVersion
	if err := DB.Where("credential_version = ?", task.PrivateData.ProviderCredentialVersion).First(&row).Error; err != nil {
		return "", errors.New("task provider credential version is unavailable")
	}
	if row.TenantID != task.PrivateData.ProviderCredentialTenantID || row.ChannelID != task.ChannelId ||
		row.KeyIndex != *task.PrivateData.PinnedKeyIndex || row.KeyFingerprint != task.PrivateData.PinnedKeyFingerprint {
		return "", errors.New("task provider credential version does not match its fenced identity")
	}
	plaintext, err := resolveProviderCredentialVersionRow(row)
	if err != nil {
		return "", err
	}
	defer clear(plaintext)
	return string(plaintext), nil
}

// ValidateProviderCredentialVaultRuntime verifies both long-running task
// snapshots and native channel credential-set storage. Secure workers start
// only when the file-backed keyring is usable, every legacy plaintext column is
// empty, and every channel reference authenticates under an available KEK.
func ValidateProviderCredentialVaultRuntime(production bool) error {
	keyring, err := loadProviderCredentialKeyring(production)
	if err != nil {
		return err
	}
	defer keyring.clear()
	if production {
		if err := validateReferencedProviderCredentialVersions(keyring); err != nil {
			return err
		}
	}
	return validateProviderChannelCredentialVaultRuntime(production)
}

// validateReferencedProviderCredentialVersions streams only task and recovery
// rows that can still cross a provider boundary. Failed native tasks cannot
// serve content, but successful native video tasks can still use /content and
// therefore retain their historical KEK until the task itself is removed by a
// separate retention contract. Platform recovery is needed only while its job
// is non-terminal because a successful Platform job already owns a durable
// artifact. Both status columns are indexed, Rows bounds scan memory, and the
// distinct-version cap fails closed on an abnormal backlog.
func validateReferencedProviderCredentialVersions(keyring *providerCredentialKeyring) error {
	if DB == nil || keyring == nil {
		return errors.New("provider credential readiness context is unavailable")
	}
	references := make(map[string]providerCredentialRuntimeReference)
	addReference := func(reference providerCredentialRuntimeReference) error {
		if reference.CredentialVersion == "" || reference.TenantID == "" || reference.ChannelID <= 0 ||
			reference.KeyIndex < 0 || len(reference.KeyFingerprint) != sha256.Size*2 ||
			strings.ToLower(reference.KeyFingerprint) != reference.KeyFingerprint {
			return errors.New("provider credential readiness found an incomplete active reference")
		}
		if previous, ok := references[reference.CredentialVersion]; ok {
			if previous != reference {
				return errors.New("provider credential readiness found conflicting active references")
			}
			return nil
		}
		if len(references) >= providerCredentialReadinessMaxActiveVersions {
			return errors.New("provider credential readiness active version bound exceeded")
		}
		references[reference.CredentialVersion] = reference
		return nil
	}

	if DB.Migrator().HasTable(&Task{}) {
		rows, err := DB.Session(&gorm.Session{NewDB: true, SkipHooks: true}).Table("tasks").
			Select("channel_id, private_data").
			Where("status IS NULL OR status <> ?", TaskStatusFailure).
			Where("private_data IS NOT NULL").Rows()
		if err != nil {
			return errors.New("provider credential readiness could not inspect active tasks")
		}
		for rows.Next() {
			var channelID int
			var privateData string
			if err := rows.Scan(&channelID, &privateData); err != nil {
				_ = rows.Close()
				return errors.New("provider credential readiness could not read an active task")
			}
			var data TaskPrivateData
			if err := data.Scan(privateData); err != nil || data.LegacyProviderCredentialPresent {
				_ = rows.Close()
				return errors.New("provider credential readiness found invalid active task private data")
			}
			if data.ProviderCredentialVersion == "" && data.ProviderCredentialTenantID == "" &&
				data.PinnedKeyIndex == nil && data.PinnedKeyFingerprint == "" {
				continue
			}
			keyIndex := -1
			if data.PinnedKeyIndex != nil {
				keyIndex = *data.PinnedKeyIndex
			}
			if err := addReference(providerCredentialRuntimeReference{
				CredentialVersion: data.ProviderCredentialVersion,
				TenantID:          data.ProviderCredentialTenantID,
				ChannelID:         channelID,
				KeyIndex:          keyIndex,
				KeyFingerprint:    data.PinnedKeyFingerprint,
			}); err != nil {
				_ = rows.Close()
				return err
			}
		}
		if err := rows.Err(); err != nil {
			_ = rows.Close()
			return errors.New("provider credential readiness active task scan failed")
		}
		if err := rows.Close(); err != nil {
			return errors.New("provider credential readiness active task scan could not close")
		}
	}

	if DB.Migrator().HasTable(&PlatformGenerationJob{}) {
		rows, err := DB.Session(&gorm.Session{NewDB: true, SkipHooks: true}).Table("platform_generation_jobs").
			Select("tenant_id, native_task_recovery_json").
			Where("status NOT IN ?", []string{
				PlatformGenerationStatusSucceeded,
				PlatformGenerationStatusFailed,
				PlatformGenerationStatusCancelled,
			}).
			Where("native_task_recovery_json IS NOT NULL AND native_task_recovery_json <> ''").Rows()
		if err != nil {
			return errors.New("provider credential readiness could not inspect active recovery references")
		}
		for rows.Next() {
			var tenantID string
			var recoveryJSON string
			if err := rows.Scan(&tenantID, &recoveryJSON); err != nil {
				_ = rows.Close()
				return errors.New("provider credential readiness could not read an active recovery reference")
			}
			recovery, err := decodePlatformGenerationNativeTaskRecovery(recoveryJSON)
			if err != nil || recovery.ProviderCredentialTenantID != tenantID {
				_ = rows.Close()
				return errors.New("provider credential readiness found an invalid active recovery reference")
			}
			if err := addReference(providerCredentialRuntimeReference{
				CredentialVersion: recovery.ProviderCredentialVersion,
				TenantID:          recovery.ProviderCredentialTenantID,
				ChannelID:         recovery.ChannelID,
				KeyIndex:          recovery.PinnedKeyIndex,
				KeyFingerprint:    recovery.PinnedKeyFingerprint,
			}); err != nil {
				_ = rows.Close()
				return err
			}
		}
		if err := rows.Err(); err != nil {
			_ = rows.Close()
			return errors.New("provider credential readiness active recovery scan failed")
		}
		if err := rows.Close(); err != nil {
			return errors.New("provider credential readiness active recovery scan could not close")
		}
	}

	for _, reference := range references {
		var row ProviderCredentialVersion
		if err := DB.Session(&gorm.Session{NewDB: true, SkipHooks: true}).
			Where("credential_version = ?", reference.CredentialVersion).Take(&row).Error; err != nil {
			return errors.New("provider credential readiness found an unavailable active version")
		}
		if row.TenantID != reference.TenantID || row.ChannelID != reference.ChannelID ||
			row.KeyIndex != reference.KeyIndex || row.KeyFingerprint != reference.KeyFingerprint {
			return errors.New("provider credential readiness found an active reference with mismatched identity")
		}
		plaintext, err := resolveProviderCredentialVersionRowWithKeyring(row, keyring)
		if err != nil {
			return err
		}
		clear(plaintext)
	}
	return nil
}

// MigrateProviderCredentialVaultStorage safely converts legacy Task and
// platform-generation recovery copies when a keyring is configured. If legacy
// plaintext exists without a usable keyring, startup fails closed and no
// provider polling is allowed to silently fall back to it.
func MigrateProviderCredentialVaultStorage() error {
	return MigrateProviderCredentialVaultStorageWithDB(DB)
}

func MigrateProviderCredentialVaultStorageWithDB(db *gorm.DB) error {
	if db == nil {
		return errors.New("provider credential database is not initialized")
	}
	return db.Transaction(func(tx *gorm.DB) error {
		if db.Dialector.Name() == "postgres" {
			// One transaction-scoped lock makes the legacy conversion atomic
			// across concurrently starting pods. Explicit table locks also fence
			// an older pod that does not know the advisory-lock convention, so it
			// cannot rewrite plaintext between the scan and trigger installation.
			if err := tx.Exec("SELECT pg_advisory_xact_lock(?)", int64(0x5052435641554c54)).Error; err != nil {
				return errors.New("provider credential migration lock could not be acquired")
			}
			if err := tx.Exec("LOCK TABLE tasks, platform_generation_jobs, provider_credential_versions IN SHARE ROW EXCLUSIVE MODE").Error; err != nil {
				return errors.New("provider credential migration tables could not be fenced")
			}
		} else if db.Dialector.Name() == "sqlite" {
			// Force a writer reservation at the beginning of the transaction.
			// SQLite has no TRUNCATE and serializes subsequent writers until the
			// migration and its triggers commit together.
			if err := tx.Exec("UPDATE provider_credential_versions SET credential_version = credential_version WHERE 1 = 0").Error; err != nil {
				return errors.New("provider credential SQLite migration could not reserve the writer")
			}
		}
		if err := rebuildProviderCredentialIdentityIndexTx(tx); err != nil {
			return err
		}
		if err := migrateLegacyTaskProviderCredentialsTx(tx); err != nil {
			return err
		}
		if err := migrateLegacyPlatformGenerationRecoveryCredentialsTx(tx); err != nil {
			return err
		}
		if db.Dialector.Name() == "postgres" {
			return installProviderCredentialPostgresGuardsTx(tx)
		}
		if db.Dialector.Name() == "sqlite" {
			return installProviderCredentialSQLiteGuardsTx(tx)
		}
		return nil
	})
}

func rebuildProviderCredentialIdentityIndexTx(tx *gorm.DB) error {
	migrator := tx.Migrator()
	if migrator.HasIndex(&ProviderCredentialVersion{}, "uniq_provider_credential_identity") {
		if err := migrator.DropIndex(&ProviderCredentialVersion{}, "uniq_provider_credential_identity"); err != nil {
			return errors.New("legacy provider credential identity index could not be removed")
		}
	}
	if err := migrator.CreateIndex(&ProviderCredentialVersion{}, "uniq_provider_credential_identity"); err != nil {
		return errors.New("versioned provider credential identity index could not be created")
	}
	return nil
}

func migrateLegacyTaskProviderCredentialsTx(tx *gorm.DB) error {
	var rows []providerCredentialLegacyTaskRow
	if err := tx.Table("tasks").Select("id, task_id, channel_id, private_data").Where("private_data IS NOT NULL").Find(&rows).Error; err != nil {
		return errors.New("legacy task provider credentials could not be inspected")
	}
	for _, row := range rows {
		fields := map[string]json.RawMessage{}
		if strings.TrimSpace(row.PrivateData) == "" || strings.TrimSpace(row.PrivateData) == "null" {
			continue
		}
		if err := json.Unmarshal([]byte(row.PrivateData), &fields); err != nil {
			return errors.New("legacy task private data is invalid")
		}
		rawKey, ok := fields["key"]
		if !ok {
			continue
		}
		var key string
		if err := json.Unmarshal(rawKey, &key); err != nil {
			return errors.New("legacy task provider credential is invalid")
		}
		if key == "" {
			delete(fields, "key")
			serialized, err := json.Marshal(fields)
			if err != nil {
				return errors.New("legacy task private data without credential could not be encoded")
			}
			result := tx.Table("tasks").Where("id = ?", row.ID).Update("private_data", string(serialized))
			if result.Error != nil || result.RowsAffected != 1 {
				return errors.New("legacy empty task credential field could not be removed")
			}
			continue
		}
		keyIndex := 0
		if raw, ok := fields["pinned_key_index"]; ok {
			if err := json.Unmarshal(raw, &keyIndex); err != nil || keyIndex < 0 {
				return errors.New("legacy task credential key index is invalid")
			}
		}
		fingerprint := ""
		if raw, ok := fields["pinned_key_fingerprint"]; ok {
			_ = json.Unmarshal(raw, &fingerprint)
		}
		if fingerprint == "" {
			digest := sha256.Sum256([]byte(key))
			fingerprint = fmt.Sprintf("%x", digest[:])
			encoded, _ := json.Marshal(fingerprint)
			fields["pinned_key_fingerprint"] = encoded
		}
		encodedIndex, _ := json.Marshal(keyIndex)
		fields["pinned_key_index"] = encodedIndex
		tenantID := ProviderCredentialNativeTenantScope
		var platformTenants []string
		if err := tx.Table("platform_generation_jobs").Distinct("tenant_id").
			Where("native_task_id = ?", row.TaskID).Pluck("tenant_id", &platformTenants).Error; err != nil {
			return errors.New("legacy task provider credential tenant could not be resolved")
		}
		resolvedTenant := ""
		for _, candidate := range platformTenants {
			candidate = strings.TrimSpace(candidate)
			if candidate == "" {
				continue
			}
			if resolvedTenant != "" && resolvedTenant != candidate {
				return errors.New("legacy task provider credential is ambiguous across tenants")
			}
			resolvedTenant = candidate
		}
		if resolvedTenant != "" {
			tenantID = resolvedTenant
		}
		version, err := storeProviderCredentialVersionTx(tx, tenantID, row.ChannelID, keyIndex, key, fingerprint)
		if err != nil {
			return err
		}
		delete(fields, "key")
		encodedTenant, _ := json.Marshal(tenantID)
		encodedVersion, _ := json.Marshal(version)
		fields["provider_credential_tenant_id"] = encodedTenant
		fields["provider_credential_version"] = encodedVersion
		serialized, err := json.Marshal(fields)
		if err != nil {
			return errors.New("migrated task credential reference could not be encoded")
		}
		result := tx.Table("tasks").Where("id = ?", row.ID).Update("private_data", string(serialized))
		if result.Error != nil || result.RowsAffected != 1 {
			return errors.New("legacy task credential could not be replaced")
		}
	}
	return nil
}

func migrateLegacyPlatformGenerationRecoveryCredentialsTx(tx *gorm.DB) error {
	var rows []providerCredentialLegacyRecoveryRow
	if err := tx.Table("platform_generation_jobs").Select("id, tenant_id, native_task_recovery_json").
		Where("native_task_recovery_json IS NOT NULL AND native_task_recovery_json <> ''").Find(&rows).Error; err != nil {
		return errors.New("legacy platform generation recovery credentials could not be inspected")
	}
	for _, row := range rows {
		fields := map[string]json.RawMessage{}
		if err := json.Unmarshal([]byte(row.NativeTaskRecoveryJSON), &fields); err != nil {
			return errors.New("legacy platform generation recovery evidence is invalid")
		}
		rawKey, ok := fields["pinned_key"]
		if !ok {
			continue
		}
		var key string
		if err := json.Unmarshal(rawKey, &key); err != nil {
			return errors.New("legacy platform generation recovery provider credential is invalid")
		}
		if key == "" {
			delete(fields, "pinned_key")
			fields["schema_version"] = json.RawMessage("2")
			serialized, err := json.Marshal(fields)
			if err != nil {
				return errors.New("legacy generation recovery without credential could not be encoded")
			}
			result := tx.Table("platform_generation_jobs").Where("id = ?", row.ID).
				Update("native_task_recovery_json", string(serialized))
			if result.Error != nil || result.RowsAffected != 1 {
				return errors.New("legacy empty generation recovery credential field could not be removed")
			}
			continue
		}
		var channelID, keyIndex int
		var fingerprint string
		if json.Unmarshal(fields["channel_id"], &channelID) != nil ||
			json.Unmarshal(fields["pinned_key_index"], &keyIndex) != nil ||
			json.Unmarshal(fields["pinned_key_fingerprint"], &fingerprint) != nil {
			return errors.New("legacy platform generation recovery credential identity is invalid")
		}
		version, err := storeProviderCredentialVersionTx(tx, row.TenantID, channelID, keyIndex, key, fingerprint)
		if err != nil {
			return err
		}
		delete(fields, "pinned_key")
		fields["schema_version"] = json.RawMessage("2")
		encodedTenant, _ := json.Marshal(row.TenantID)
		encodedVersion, _ := json.Marshal(version)
		fields["provider_credential_tenant_id"] = encodedTenant
		fields["provider_credential_version"] = encodedVersion
		serialized, err := json.Marshal(fields)
		if err != nil {
			return errors.New("migrated platform generation recovery reference could not be encoded")
		}
		result := tx.Table("platform_generation_jobs").Where("id = ?", row.ID).
			Update("native_task_recovery_json", string(serialized))
		if result.Error != nil || result.RowsAffected != 1 {
			return errors.New("legacy platform generation recovery credential could not be replaced")
		}
	}
	return nil
}

func installProviderCredentialPostgresGuardsTx(tx *gorm.DB) error {
	statements := []string{
		`CREATE OR REPLACE FUNCTION reject_provider_credential_version_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'provider credential versions are immutable';
END;
$$ LANGUAGE plpgsql`,
		`DROP TRIGGER IF EXISTS trg_provider_credential_versions_no_update_delete ON provider_credential_versions`,
		`CREATE TRIGGER trg_provider_credential_versions_no_update_delete BEFORE UPDATE OR DELETE ON provider_credential_versions FOR EACH ROW EXECUTE FUNCTION reject_provider_credential_version_mutation()`,
		`DROP TRIGGER IF EXISTS trg_provider_credential_versions_no_truncate ON provider_credential_versions`,
		`CREATE TRIGGER trg_provider_credential_versions_no_truncate BEFORE TRUNCATE ON provider_credential_versions FOR EACH STATEMENT EXECUTE FUNCTION reject_provider_credential_version_mutation()`,
		`CREATE OR REPLACE FUNCTION reject_plaintext_provider_credential_snapshot()
RETURNS trigger AS $$
DECLARE
    payload jsonb;
BEGIN
    IF TG_TABLE_NAME = 'tasks' THEN
        IF NEW.private_data IS NOT NULL THEN
            payload := NEW.private_data::jsonb;
            IF payload ? 'key' THEN
                RAISE EXCEPTION 'plaintext provider credentials are forbidden in task private_data';
            END IF;
        END IF;
    ELSIF TG_TABLE_NAME = 'platform_generation_jobs' THEN
        IF COALESCE(BTRIM(NEW.native_task_recovery_json), '') <> '' THEN
            payload := NEW.native_task_recovery_json::jsonb;
            IF payload ? 'pinned_key' THEN
                RAISE EXCEPTION 'plaintext provider credentials are forbidden in generation recovery evidence';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql`,
		`DROP TRIGGER IF EXISTS trg_tasks_no_plaintext_provider_credential ON tasks`,
		`CREATE TRIGGER trg_tasks_no_plaintext_provider_credential BEFORE INSERT OR UPDATE OF private_data ON tasks FOR EACH ROW EXECUTE FUNCTION reject_plaintext_provider_credential_snapshot()`,
		`DROP TRIGGER IF EXISTS trg_platform_generation_jobs_no_plaintext_provider_credential ON platform_generation_jobs`,
		`CREATE TRIGGER trg_platform_generation_jobs_no_plaintext_provider_credential BEFORE INSERT OR UPDATE OF native_task_recovery_json ON platform_generation_jobs FOR EACH ROW EXECUTE FUNCTION reject_plaintext_provider_credential_snapshot()`,
	}
	for _, statement := range statements {
		if err := tx.Exec(statement).Error; err != nil {
			return errors.New("provider credential PostgreSQL guard installation failed")
		}
	}
	return nil
}

func installProviderCredentialSQLiteGuardsTx(tx *gorm.DB) error {
	statements := []string{
		`DROP TRIGGER IF EXISTS trg_provider_credential_versions_no_update`,
		`CREATE TRIGGER trg_provider_credential_versions_no_update BEFORE UPDATE ON provider_credential_versions BEGIN SELECT RAISE(ABORT, 'provider credential versions are immutable'); END`,
		`DROP TRIGGER IF EXISTS trg_provider_credential_versions_no_delete`,
		`CREATE TRIGGER trg_provider_credential_versions_no_delete BEFORE DELETE ON provider_credential_versions BEGIN SELECT RAISE(ABORT, 'provider credential versions are immutable'); END`,
		`DROP TRIGGER IF EXISTS trg_tasks_no_plaintext_provider_credential_insert`,
		`CREATE TRIGGER trg_tasks_no_plaintext_provider_credential_insert BEFORE INSERT ON tasks WHEN NEW.private_data IS NOT NULL AND CASE WHEN json_valid(NEW.private_data) THEN json_type(NEW.private_data, '$.key') IS NOT NULL ELSE 1 END BEGIN SELECT RAISE(ABORT, 'plaintext provider credentials are forbidden in task private_data'); END`,
		`DROP TRIGGER IF EXISTS trg_tasks_no_plaintext_provider_credential_update`,
		`CREATE TRIGGER trg_tasks_no_plaintext_provider_credential_update BEFORE UPDATE OF private_data ON tasks WHEN NEW.private_data IS NOT NULL AND CASE WHEN json_valid(NEW.private_data) THEN json_type(NEW.private_data, '$.key') IS NOT NULL ELSE 1 END BEGIN SELECT RAISE(ABORT, 'plaintext provider credentials are forbidden in task private_data'); END`,
		`DROP TRIGGER IF EXISTS trg_platform_generation_jobs_no_plaintext_provider_credential_insert`,
		`CREATE TRIGGER trg_platform_generation_jobs_no_plaintext_provider_credential_insert BEFORE INSERT ON platform_generation_jobs WHEN COALESCE(trim(NEW.native_task_recovery_json), '') <> '' AND CASE WHEN json_valid(NEW.native_task_recovery_json) THEN json_type(NEW.native_task_recovery_json, '$.pinned_key') IS NOT NULL ELSE 1 END BEGIN SELECT RAISE(ABORT, 'plaintext provider credentials are forbidden in generation recovery evidence'); END`,
		`DROP TRIGGER IF EXISTS trg_platform_generation_jobs_no_plaintext_provider_credential_update`,
		`CREATE TRIGGER trg_platform_generation_jobs_no_plaintext_provider_credential_update BEFORE UPDATE OF native_task_recovery_json ON platform_generation_jobs WHEN COALESCE(trim(NEW.native_task_recovery_json), '') <> '' AND CASE WHEN json_valid(NEW.native_task_recovery_json) THEN json_type(NEW.native_task_recovery_json, '$.pinned_key') IS NOT NULL ELSE 1 END BEGIN SELECT RAISE(ABORT, 'plaintext provider credentials are forbidden in generation recovery evidence'); END`,
	}
	for _, statement := range statements {
		if err := tx.Exec(statement).Error; err != nil {
			return errors.New("provider credential SQLite guard installation failed")
		}
	}
	return nil
}
