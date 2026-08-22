package model

import (
	"errors"
	"fmt"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
)

const (
	productionRootProvisionAdvisoryLock int64 = 0x41564944524f4f54
	productionRootMinimumPasswordBytes        = 32
	productionRootMaximumPasswordBytes        = 72
)

var ErrProductionRootProvisionConflict = errors.New("production root provisioning conflicts with existing state")

type ProductionRootProvisionResult struct {
	Username     string
	RootCreated  bool
	SetupCreated bool
}

type productionRootExactShape struct {
	Username        string
	DisplayName     string
	Role            int
	Status          int
	Email           string
	GitHubID        string
	DiscordID       string
	OIDCID          string
	WeChatID        string
	TelegramID      string
	LinuxDOID       string
	HasAccessToken  bool
	Quota           int
	UsedQuota       int
	RequestCount    int
	Group           string
	AffCode         string
	AffCount        int
	AffQuota        int
	AffHistoryQuota int
	InviterID       int
	Deleted         bool
	Setting         string
	Remark          string
	StripeCustomer  string
	LastLoginAt     int64
	AuthVersion     int64
}

func newProductionRoot(username string, passwordHash string) User {
	return User{
		Username:    username,
		Password:    passwordHash,
		DisplayName: "Root User",
		Role:        common.RoleRootUser,
		Status:      common.UserStatusEnabled,
		AccessToken: nil,
		Quota:       100000000,
		Group:       "default",
		AuthVersion: 1,
	}
}

func productionRootShape(user User) productionRootExactShape {
	return productionRootExactShape{
		Username: user.Username, DisplayName: user.DisplayName,
		Role: user.Role, Status: user.Status, Email: user.Email,
		GitHubID: user.GitHubId, DiscordID: user.DiscordId, OIDCID: user.OidcId,
		WeChatID: user.WeChatId, TelegramID: user.TelegramId, LinuxDOID: user.LinuxDOId,
		HasAccessToken: user.AccessToken != nil,
		Quota:          user.Quota, UsedQuota: user.UsedQuota, RequestCount: user.RequestCount,
		Group: user.Group, AffCode: user.AffCode, AffCount: user.AffCount,
		AffQuota: user.AffQuota, AffHistoryQuota: user.AffHistoryQuota, InviterID: user.InviterId,
		Deleted: user.DeletedAt.Valid, Setting: user.Setting, Remark: user.Remark,
		StripeCustomer: user.StripeCustomer, LastLoginAt: user.LastLoginAt, AuthVersion: user.AuthVersion,
	}
}

func productionRootAuthenticationModels() []any {
	return []any{
		&Token{},
		&UserSession{},
		&AuthFlow{},
		&ExternalIdentityClaim{},
		&PasskeyCredential{},
		&UserOAuthBinding{},
		&TwoFA{},
		&TwoFABackupCode{},
	}
}

func requireProductionRootAuthenticationFootprintZero(tx *gorm.DB, userID *int) error {
	for _, authenticationModel := range productionRootAuthenticationModels() {
		query := tx.Unscoped().Model(authenticationModel)
		if userID != nil {
			query = query.Where("user_id = ?", *userID)
		}
		var footprintCount int64
		if err := query.Count(&footprintCount).Error; err != nil {
			return err
		}
		if footprintCount != 0 {
			return ErrProductionRootProvisionConflict
		}
	}
	return nil
}

func ValidateProductionRootCredentials(username string, password string) error {
	if !validProductionRootUsername(username) {
		return errors.New("production root username is invalid")
	}
	if !utf8.ValidString(password) || strings.TrimSpace(password) != password ||
		containsProductionRootControl(password) || protectedRelaySecretLooksWeak([]byte(password)) {
		return errors.New("production root password is invalid")
	}
	passwordBytes := len([]byte(password))
	if passwordBytes < productionRootMinimumPasswordBytes || passwordBytes > productionRootMaximumPasswordBytes {
		return fmt.Errorf(
			"production root password must contain between %d and %d UTF-8 bytes",
			productionRootMinimumPasswordBytes,
			productionRootMaximumPasswordBytes,
		)
	}
	return nil
}

func protectedRelaySecretLooksWeak(value []byte) bool {
	if len(value) == 0 {
		return true
	}
	distinct := make(map[byte]struct{}, len(value))
	for _, character := range value {
		distinct[character] = struct{}{}
	}
	if len(distinct) < 8 {
		return true
	}
	for period := 1; period <= 16 && period*2 <= len(value); period++ {
		periodic := true
		for index := period; index < len(value); index++ {
			if value[index] != value[index%period] {
				periodic = false
				break
			}
		}
		if periodic {
			return true
		}
	}
	lower := strings.ToLower(string(value))
	for _, marker := range []string{
		"changeme", "change-me", "change_me", "replace-me", "replace_me", "replacewith",
		"placeholder", "development-only", "developmentonly", "example-only", "exampleonly",
		"not-a-secret", "notasecret",
	} {
		if strings.Contains(lower, marker) {
			return true
		}
	}
	return false
}

func validProductionRootUsername(username string) bool {
	if username == "" || len(username) > UserNameMaxLength {
		return false
	}
	if PlatformRelayServicePrincipalUsernameIsReserved(username) {
		return false
	}
	for index := 0; index < len(username); index++ {
		character := username[index]
		alphaNumeric := character >= 'a' && character <= 'z' ||
			character >= 'A' && character <= 'Z' ||
			character >= '0' && character <= '9'
		if index == 0 {
			if !alphaNumeric {
				return false
			}
			continue
		}
		if !alphaNumeric && character != '.' && character != '_' && character != '-' {
			return false
		}
	}
	return true
}

func containsProductionRootControl(value string) bool {
	for _, character := range value {
		if unicode.IsControl(character) || unicode.Is(unicode.Cf, character) {
			return true
		}
	}
	return false
}

func validProductionRootSetupVersion(version string) bool {
	return utf8.ValidString(version) && version != "" && strings.TrimSpace(version) == version &&
		utf8.RuneCountInString(version) <= 50 && !containsProductionRootControl(version)
}

// ProvisionProductionRoot creates the first application root and setup marker,
// or proves that an exact prior invocation already committed them. PostgreSQL
// callers are serialized with a transaction-scoped advisory lock before any
// state is inspected. Existing partial or conflicting state is never repaired.
func ProvisionProductionRoot(username string, password string, version string) (ProductionRootProvisionResult, error) {
	result := ProductionRootProvisionResult{Username: username}
	if DB == nil {
		return result, errors.New("database is not initialized")
	}
	if err := ValidateProductionRootCredentials(username, password); err != nil {
		return result, err
	}
	if !validProductionRootSetupVersion(version) {
		return result, errors.New("production root setup version is invalid")
	}
	dialect := DB.Dialector.Name()
	if dialect != "postgres" && dialect != "sqlite" {
		return result, errors.New("production root provisioning requires PostgreSQL")
	}

	err := DB.Transaction(func(tx *gorm.DB) error {
		if tx.Dialector.Name() == "postgres" {
			if err := acquireRelayLifecycleTransactionLock(tx); err != nil {
				return err
			}
			if err := tx.Exec(
				"SELECT pg_catalog.pg_advisory_xact_lock(?)",
				productionRootProvisionAdvisoryLock,
			).Error; err != nil {
				return fmt.Errorf("acquire production root provisioning lock: %w", err)
			}
		}

		var roots []User
		if err := tx.Unscoped().Where("role = ?", common.RoleRootUser).Order("id ASC").Find(&roots).Error; err != nil {
			return err
		}
		var matchingUsers []User
		if err := tx.Unscoped().Where("username = ?", username).Order("id ASC").Find(&matchingUsers).Error; err != nil {
			return err
		}
		var setups []Setup
		if err := tx.Order("id ASC").Find(&setups).Error; err != nil {
			return err
		}
		if len(roots) > 1 || len(matchingUsers) > 1 || len(setups) > 1 {
			return ErrProductionRootProvisionConflict
		}

		if len(roots) == 1 {
			root := roots[0]
			expectedRoot := newProductionRoot(username, root.Password)
			if len(matchingUsers) != 1 || matchingUsers[0].Id != root.Id ||
				root.Id <= 0 || root.CreatedAt <= 0 ||
				productionRootShape(root) != productionRootShape(expectedRoot) ||
				!common.ValidatePasswordAndHash(password, root.Password) ||
				len(setups) != 1 || setups[0].ID == 0 || setups[0].Version != version || setups[0].InitializedAt <= 0 {
				return ErrProductionRootProvisionConflict
			}
			if err := requireProductionRootAuthenticationFootprintZero(tx, &root.Id); err != nil {
				return err
			}
			return nil
		}

		var userCount int64
		if err := tx.Unscoped().Model(&User{}).Count(&userCount).Error; err != nil {
			return err
		}
		if userCount != 0 || len(matchingUsers) != 0 || len(setups) != 0 {
			return ErrProductionRootProvisionConflict
		}
		if err := requireProductionRootAuthenticationFootprintZero(tx, nil); err != nil {
			return err
		}
		hashedPassword, err := common.Password2Hash(password)
		if err != nil {
			return errors.New("production root password could not be hashed")
		}
		root := newProductionRoot(username, hashedPassword)
		if err := tx.Create(&root).Error; err != nil {
			return err
		}
		if root.Id <= 0 || root.CreatedAt <= 0 {
			return ErrProductionRootProvisionConflict
		}
		if err := requireProductionRootAuthenticationFootprintZero(tx, &root.Id); err != nil {
			return err
		}
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		if err := tx.Create(&Setup{Version: version, InitializedAt: now.Unix()}).Error; err != nil {
			return err
		}
		result.RootCreated = true
		result.SetupCreated = true
		return nil
	})
	return result, err
}
