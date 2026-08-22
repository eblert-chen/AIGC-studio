package service

import (
	"bytes"
	"crypto/sha256"
	"database/sql"
	"encoding/json"
	"errors"
	"io"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
	"gorm.io/gorm/logger"
)

const (
	PlatformRelayServicePrincipalsFileKind          = "relay_service_principals"
	PlatformRelayServicePrincipalsFileSchemaVersion = 1
	protectedPlatformRelayPrincipalProvisionLock    = int64(0x415649445350524e)
)

var ErrProtectedPlatformRelayPrincipalProvisionConflict = errors.New("protected Platform Relay service principal provisioning conflicts with existing state")

type PlatformRelayServicePrincipalProvisionInput struct {
	ClientID      string `json:"client_id"`
	TenantID      string `json:"tenant_id"`
	UpstreamToken string `json:"upstream_token"`
}

type PlatformRelayServicePrincipalsFile struct {
	Kind          string                                        `json:"kind"`
	SchemaVersion int                                           `json:"schema_version"`
	Principals    []PlatformRelayServicePrincipalProvisionInput `json:"principals"`
}

type PlatformRelayServicePrincipalProvisionResult struct {
	Created bool
	Count   int
}

// ParsePlatformRelayServicePrincipalsFile validates the isolated identity
// bundle used by both the principal one-shot and the API. It rejects duplicate
// JSON keys before decoding so no parser-dependent credential can be selected.
func ParsePlatformRelayServicePrincipalsFile(raw []byte) ([]PlatformRelayServicePrincipalProvisionInput, error) {
	if len(raw) == 0 || !json.Valid(raw) || rejectPlatformRelayDuplicateJSONKeys(raw) != nil {
		return nil, errors.New("Relay service principal file is invalid")
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	var document PlatformRelayServicePrincipalsFile
	if err := decoder.Decode(&document); err != nil {
		return nil, errors.New("Relay service principal file is invalid")
	}
	if err := requirePlatformRelayJSONEOF(decoder); err != nil ||
		document.Kind != PlatformRelayServicePrincipalsFileKind ||
		document.SchemaVersion != PlatformRelayServicePrincipalsFileSchemaVersion {
		return nil, errors.New("Relay service principal file is invalid")
	}
	for _, principal := range document.Principals {
		key := strings.TrimPrefix(principal.UpstreamToken, "sk-")
		if platformRelaySecretIsPlaceholder(principal.UpstreamToken) || !protectedPlatformRelaySecretDiverse(key) {
			return nil, errors.New("Relay service principal file is invalid")
		}
	}
	if _, err := buildProtectedPlatformRelayExpectedPrincipals(document.Principals); err != nil {
		return nil, errors.New("Relay service principal file is invalid")
	}
	return document.Principals, nil
}

func rejectPlatformRelayDuplicateJSONKeys(raw []byte) error {
	return common.RejectDuplicateJSONKeys(raw)
}

func requirePlatformRelayJSONEOF(decoder *json.Decoder) error {
	if _, err := decoder.Token(); !errors.Is(err, io.EOF) {
		if err == nil {
			return errors.New("JSON document contains trailing data")
		}
		return err
	}
	return nil
}

func buildProtectedPlatformRelayExpectedPrincipals(
	inputs []PlatformRelayServicePrincipalProvisionInput,
) (map[string]protectedPlatformRelayExpectedPrincipal, error) {
	if len(inputs) == 0 || len(inputs) > protectedPlatformRelayPrincipalMaxClients {
		return nil, ErrProtectedPlatformRelayPrincipalProvisionConflict
	}
	expected := make(map[string]protectedPlatformRelayExpectedPrincipal, len(inputs))
	seenKeys := make(map[[sha256.Size]byte]struct{}, len(inputs))
	seenUsers := make(map[string]struct{}, len(inputs))
	seenAffCodes := make(map[string]struct{}, len(inputs))
	previousClientID := ""
	for index, input := range inputs {
		if index > 0 && input.ClientID <= previousClientID {
			return nil, ErrProtectedPlatformRelayPrincipalProvisionConflict
		}
		previousClientID = input.ClientID
		if !strings.HasPrefix(input.UpstreamToken, "sk-") {
			return nil, ErrProtectedPlatformRelayPrincipalProvisionConflict
		}
		key, ok := protectedPlatformRelayTokenKey(input.UpstreamToken)
		user, userErr := BuildProtectedPlatformRelayServicePrincipalUser(input.ClientID, input.TenantID)
		if !ok || userErr != nil || len(input.UpstreamToken) != 51 {
			return nil, ErrProtectedPlatformRelayPrincipalProvisionConflict
		}
		purpose := protectedPlatformRelayTokenPurpose(input.ClientID, input.TenantID)
		if _, duplicate := expected[purpose]; duplicate {
			return nil, ErrProtectedPlatformRelayPrincipalProvisionConflict
		}
		keyDigest := sha256.Sum256([]byte(key))
		if _, duplicate := seenKeys[keyDigest]; duplicate {
			return nil, ErrProtectedPlatformRelayPrincipalProvisionConflict
		}
		seenKeys[keyDigest] = struct{}{}
		if _, duplicate := seenUsers[user.Username]; duplicate {
			return nil, ErrProtectedPlatformRelayPrincipalProvisionConflict
		}
		seenUsers[user.Username] = struct{}{}
		if _, duplicate := seenAffCodes[user.AffCode]; duplicate {
			return nil, ErrProtectedPlatformRelayPrincipalProvisionConflict
		}
		seenAffCodes[user.AffCode] = struct{}{}
		expected[purpose] = protectedPlatformRelayExpectedPrincipal{
			clientID: input.ClientID,
			tenantID: input.TenantID,
			key:      key,
		}
	}
	return expected, nil
}

// ProvisionProtectedPlatformRelayServicePrincipals creates the complete exact
// principal set, or proves that an identical set already exists. It never
// repairs, rotates, deletes, or partially upserts service identities.
func ProvisionProtectedPlatformRelayServicePrincipals(
	inputs []PlatformRelayServicePrincipalProvisionInput,
) (PlatformRelayServicePrincipalProvisionResult, error) {
	result := PlatformRelayServicePrincipalProvisionResult{Count: len(inputs)}
	if !model.RelayDatabaseRoleAttestationRequired() || model.DB == nil {
		return result, ErrProtectedPlatformRelayPrincipalProvisionConflict
	}
	expected, err := buildProtectedPlatformRelayExpectedPrincipals(inputs)
	if err != nil {
		return result, ErrProtectedPlatformRelayPrincipalProvisionConflict
	}
	dialect := model.DB.Dialector.Name()
	if dialect != "postgres" && dialect != "sqlite" {
		return result, ErrProtectedPlatformRelayPrincipalProvisionConflict
	}
	err = model.DB.Transaction(func(tx *gorm.DB) error {
		if tx.Dialector.Name() == "postgres" {
			if err := model.AcquireRelayLifecycleMutationTransactionLock(tx); err != nil {
				return err
			}
			if err := tx.Exec(
				"SELECT pg_catalog.pg_advisory_xact_lock(?)",
				protectedPlatformRelayPrincipalProvisionLock,
			).Error; err != nil {
				return errors.New("Relay service principal provisioning lock could not be acquired")
			}
		}
		if err := verifyProtectedPlatformRelayRootSetupTx(tx); err != nil {
			return err
		}
		if err := installProtectedPlatformRelaySecretSQLLoggingPolicyTx(tx); err != nil {
			return err
		}

		// Lock the entire reserved namespace before deciding between exact replay
		// and an all-new batch. Ordinary application writers are independently
		// forbidden from claiming this namespace.
		var reservedTokens []model.Token
		if err := tx.Unscoped().Clauses(clause.Locking{Strength: "UPDATE"}).
			Select("id, user_id, name").
			Where("name LIKE ?", model.PlatformRelayServicePrincipalTokenNamePrefix+"%").
			Limit(protectedPlatformRelayPrincipalMaxClients + 1).Find(&reservedTokens).Error; err != nil {
			return ErrProtectedPlatformRelayPrincipalProvisionConflict
		}
		var reservedUsers []model.User
		if err := tx.Unscoped().Clauses(clause.Locking{Strength: "UPDATE"}).
			Select("id, username, remark").Where(
			"remark = ? OR lower(substr(username, 1, ?)) = ?",
			model.PlatformRelayServicePrincipalRemark,
			len(model.PlatformRelayServicePrincipalUsernamePrefix),
			model.PlatformRelayServicePrincipalUsernamePrefix,
		).Limit(protectedPlatformRelayPrincipalMaxClients + 1).Find(&reservedUsers).Error; err != nil {
			return ErrProtectedPlatformRelayPrincipalProvisionConflict
		}
		if len(reservedTokens) != 0 || len(reservedUsers) != 0 {
			return validateProtectedPlatformRelayServicePrincipalsTx(tx, expected)
		}

		usernames := make([]string, 0, len(expected))
		affCodes := make([]string, 0, len(expected))
		for purpose, principal := range expected {
			user, err := BuildProtectedPlatformRelayServicePrincipalUser(principal.clientID, principal.tenantID)
			if err != nil {
				return ErrProtectedPlatformRelayPrincipalProvisionConflict
			}
			usernames = append(usernames, user.Username)
			affCodes = append(affCodes, user.AffCode)
			if purpose != protectedPlatformRelayTokenPurpose(principal.clientID, principal.tenantID) {
				return ErrProtectedPlatformRelayPrincipalProvisionConflict
			}
		}
		var userCollisionCount int64
		if err := tx.Unscoped().Model(&model.User{}).
			Where("username IN ? OR aff_code IN ?", usernames, affCodes).
			Count(&userCollisionCount).Error; err != nil || userCollisionCount != 0 {
			return ErrProtectedPlatformRelayPrincipalProvisionConflict
		}
		secretTx := tx.Session(&gorm.Session{Logger: logger.Default.LogMode(logger.Silent)})
		for _, principal := range expected {
			var collision model.Token
			err := secretTx.Unscoped().Select("id").Where("key = ?", principal.key).Take(&collision).Error
			if err == nil || !errors.Is(err, gorm.ErrRecordNotFound) {
				return ErrProtectedPlatformRelayPrincipalProvisionConflict
			}
		}

		now, err := model.GetDBTimeTx(tx)
		if err != nil {
			return ErrProtectedPlatformRelayPrincipalProvisionConflict
		}
		for _, input := range inputs {
			principal := expected[protectedPlatformRelayTokenPurpose(input.ClientID, input.TenantID)]
			user, err := BuildProtectedPlatformRelayServicePrincipalUser(input.ClientID, input.TenantID)
			if err != nil {
				return ErrProtectedPlatformRelayPrincipalProvisionConflict
			}
			// This direct insert is intentionally confined to the provisioner. The
			// ordinary User.Insert path rejects the reserved rsvc_ namespace.
			if err := secretTx.Create(&user).Error; err != nil {
				return ErrProtectedPlatformRelayPrincipalProvisionConflict
			}
			token := model.Token{
				UserId:         user.Id,
				Key:            principal.key,
				Name:           protectedPlatformRelayTokenPurpose(input.ClientID, input.TenantID),
				Status:         common.TokenStatusEnabled,
				CreatedTime:    now.Unix(),
				ExpiredTime:    -1,
				UnlimitedQuota: true,
			}
			insert := secretTx.Clauses(clause.OnConflict{
				Columns:   []clause.Column{{Name: "key"}},
				DoNothing: true,
			}).Create(&token)
			if insert.Error != nil || insert.RowsAffected != 1 {
				return ErrProtectedPlatformRelayPrincipalProvisionConflict
			}
		}
		if err := validateProtectedPlatformRelayServicePrincipalsTx(tx, expected); err != nil {
			return err
		}
		result.Created = true
		return nil
	})
	if err != nil {
		return PlatformRelayServicePrincipalProvisionResult{Count: len(inputs)}, ErrProtectedPlatformRelayPrincipalProvisionConflict
	}
	return result, nil
}

func installProtectedPlatformRelaySecretSQLLoggingPolicyTx(tx *gorm.DB) error {
	if tx.Dialector.Name() != "postgres" {
		return nil
	}
	var parameterLength string
	var errorParameterLength string
	var autoExplainParameterLength sql.NullString
	var pgAuditParameter sql.NullString
	if err := tx.Raw(
		`SELECT current_setting('log_parameter_max_length'),
                current_setting('log_parameter_max_length_on_error'),
                current_setting('auto_explain.log_parameter_max_length', true),
                current_setting('pgaudit.log_parameter', true)`,
	).Row().Scan(&parameterLength, &errorParameterLength, &autoExplainParameterLength, &pgAuditParameter); err != nil ||
		parameterLength != "0" || errorParameterLength != "0" {
		return errors.New("Relay service principal SQL logging policy is not exact")
	}
	if !autoExplainParameterLength.Valid || strings.TrimSpace(autoExplainParameterLength.String) != "0" {
		return errors.New("Relay service principal auto-explain parameter logging must be disabled")
	}
	if !pgAuditParameter.Valid || strings.ToLower(strings.TrimSpace(pgAuditParameter.String)) != "off" {
		return errors.New("Relay service principal audit parameter logging must be disabled")
	}
	return nil
}

func verifyProtectedPlatformRelayRootSetupTx(tx *gorm.DB) error {
	var setups []model.Setup
	// The offline provisioner already owns exclusive lifecycle gate A, and its
	// transaction owns gate B plus the principal advisory lock. PostgreSQL also
	// requires UPDATE for SELECT ... FOR SHARE, so use this transaction-snapshot
	// read and preserve the runtime manifest's deliberately read-only Setup ACL.
	if err := tx.Order("id ASC").Limit(2).Find(&setups).Error; err != nil ||
		len(setups) != 1 || strings.TrimSpace(setups[0].Version) == "" || setups[0].InitializedAt <= 0 {
		return ErrProtectedPlatformRelayPrincipalProvisionConflict
	}
	var roots []model.User
	if err := tx.Unscoped().Clauses(clause.Locking{Strength: "UPDATE"}).
		Where("role = ?", common.RoleRootUser).Order("id ASC").Limit(2).Find(&roots).Error; err != nil ||
		len(roots) != 1 || roots[0].DeletedAt.Valid || roots[0].Status != common.UserStatusEnabled ||
		model.PlatformRelayServicePrincipalUsernameIsReserved(roots[0].Username) {
		return ErrProtectedPlatformRelayPrincipalProvisionConflict
	}
	return nil
}
