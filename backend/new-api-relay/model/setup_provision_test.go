package model

import (
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/glebarez/sqlite"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

const productionRootTestPassword = "correct-horse-battery-staple-root-01"

func prepareProductionRootSQLiteTest(t *testing.T) *gorm.DB {
	t.Helper()
	previousDB := DB
	previousMainType := common.MainDatabaseType()
	database, err := gorm.Open(
		sqlite.Open("file:root-provision-"+uuid.NewString()+"?mode=memory&cache=shared"),
		&gorm.Config{},
	)
	require.NoError(t, err)
	require.NoError(t, database.AutoMigrate(
		&User{},
		&Setup{},
		&Token{},
		&UserSession{},
		&AuthFlow{},
		&ExternalIdentityClaim{},
		&PasskeyCredential{},
		&UserOAuthBinding{},
		&TwoFA{},
		&TwoFABackupCode{},
	))
	DB = database
	common.SetMainDatabaseType(common.DatabaseTypeSQLite)
	t.Cleanup(func() {
		sqlDatabase, sqlErr := database.DB()
		if sqlErr == nil {
			require.NoError(t, sqlDatabase.Close())
		}
		DB = previousDB
		common.SetMainDatabaseType(previousMainType)
	})
	return database
}

func TestProvisionProductionRootRejectsEveryFreshOrphanAuthenticationFootprint(t *testing.T) {
	futureRootID := 1
	tests := []struct {
		name string
		row  func() any
	}{
		{name: "token", row: func() any {
			return &Token{UserId: futureRootID, Key: "orphan-root-token-00000000000000000001", Name: "orphan"}
		}},
		{name: "session", row: func() any {
			return &UserSession{
				SID: uuid.NewString(), UserID: futureRootID, UserAuthVersion: 1,
				Status: UserSessionStatusRevoked, RefreshHash: strings.Repeat("a", 64),
				LoginMethod: "password", ExpiresAt: time.Now().Add(time.Hour).Unix(),
			}
		}},
		{name: "auth flow", row: func() any {
			return &AuthFlow{
				TokenHash: strings.Repeat("b", 64), Purpose: "login", UserId: futureRootID,
				ExpiresAt: time.Now().Add(time.Hour),
			}
		}},
		{name: "external identity", row: func() any {
			return &ExternalIdentityClaim{Provider: "oidc", Subject: "orphan-root", UserId: futureRootID}
		}},
		{name: "passkey", row: func() any {
			return &PasskeyCredential{UserID: futureRootID, CredentialID: "orphan-root-credential", PublicKey: "orphan-root-public-key"}
		}},
		{name: "OAuth binding", row: func() any {
			return &UserOAuthBinding{UserId: futureRootID, ProviderId: 71, ProviderUserId: "orphan-root-oauth"}
		}},
		{name: "two factor", row: func() any {
			return &TwoFA{UserId: futureRootID, Secret: "orphan-root-two-factor-secret"}
		}},
		{name: "two factor backup", row: func() any {
			return &TwoFABackupCode{UserId: futureRootID, CodeHash: "orphan-root-backup-code-hash"}
		}},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			database := prepareProductionRootSQLiteTest(t)
			row := test.row()
			require.NoError(t, database.Create(row).Error)

			result, err := ProvisionProductionRoot("root-admin", productionRootTestPassword, "v-test")
			require.ErrorIs(t, err, ErrProductionRootProvisionConflict)
			require.Equal(t, ProductionRootProvisionResult{Username: "root-admin"}, result)
			var userCount int64
			var setupCount int64
			require.NoError(t, database.Unscoped().Model(&User{}).Count(&userCount).Error)
			require.NoError(t, database.Model(&Setup{}).Count(&setupCount).Error)
			require.Zero(t, userCount)
			require.Zero(t, setupCount)
		})
	}
}

func TestProvisionProductionRootRechecksAssignedIDBeforeSetup(t *testing.T) {
	database := prepareProductionRootSQLiteTest(t)
	require.NoError(t, database.Exec(`
CREATE TRIGGER inject_production_root_auth_footprint
AFTER INSERT ON users
BEGIN
  INSERT INTO tokens (user_id, key, name)
  VALUES (NEW.id, 'orphan-insert-race-token-000000000001', 'orphan');
END`).Error)

	result, err := ProvisionProductionRoot("root-admin", productionRootTestPassword, "v-test")
	require.ErrorIs(t, err, ErrProductionRootProvisionConflict)
	require.Equal(t, ProductionRootProvisionResult{Username: "root-admin"}, result)
	var userCount int64
	var setupCount int64
	var tokenCount int64
	require.NoError(t, database.Unscoped().Model(&User{}).Count(&userCount).Error)
	require.NoError(t, database.Model(&Setup{}).Count(&setupCount).Error)
	require.NoError(t, database.Unscoped().Model(&Token{}).Count(&tokenCount).Error)
	require.Zero(t, userCount)
	require.Zero(t, setupCount)
	require.Zero(t, tokenCount)
}

func TestProvisionProductionRootCreatesCleanEnabledRootAndSetup(t *testing.T) {
	database := prepareProductionRootSQLiteTest(t)
	result, err := ProvisionProductionRoot("Root.ops_01", productionRootTestPassword, "v-test")
	require.NoError(t, err)
	require.Equal(t, ProductionRootProvisionResult{
		Username:     "Root.ops_01",
		RootCreated:  true,
		SetupCreated: true,
	}, result)

	var root User
	require.NoError(t, database.Where("role = ?", common.RoleRootUser).First(&root).Error)
	require.Equal(t, "Root.ops_01", root.Username)
	require.Equal(t, common.RoleRootUser, root.Role)
	require.Equal(t, common.UserStatusEnabled, root.Status)
	require.Nil(t, root.AccessToken)
	require.Equal(t, int64(1), root.AuthVersion)
	require.Zero(t, root.LastLoginAt)
	require.NotEqual(t, productionRootTestPassword, root.Password)
	require.True(t, common.ValidatePasswordAndHash(productionRootTestPassword, root.Password))

	var setup Setup
	require.NoError(t, database.First(&setup).Error)
	require.Equal(t, "v-test", setup.Version)
	require.Positive(t, setup.InitializedAt)

	for _, authenticationModel := range []any{
		&Token{}, &UserSession{}, &AuthFlow{}, &ExternalIdentityClaim{},
		&PasskeyCredential{}, &UserOAuthBinding{}, &TwoFA{}, &TwoFABackupCode{},
	} {
		var count int64
		require.NoError(t, database.Unscoped().Model(authenticationModel).Where("user_id = ?", root.Id).Count(&count).Error)
		require.Zero(t, count)
	}
}

func TestProvisionProductionRootExactReplayPreservesIdentityHashAndSetup(t *testing.T) {
	database := prepareProductionRootSQLiteTest(t)
	_, err := ProvisionProductionRoot("root-admin", productionRootTestPassword, "v-original")
	require.NoError(t, err)
	var originalRoot User
	var originalSetup Setup
	require.NoError(t, database.First(&originalRoot).Error)
	require.NoError(t, database.First(&originalSetup).Error)

	result, err := ProvisionProductionRoot("root-admin", productionRootTestPassword, "v-original")
	require.NoError(t, err)
	require.Equal(t, ProductionRootProvisionResult{Username: "root-admin"}, result)
	var replayedRoot User
	var replayedSetup Setup
	require.NoError(t, database.First(&replayedRoot).Error)
	require.NoError(t, database.First(&replayedSetup).Error)
	require.Equal(t, originalRoot.Id, replayedRoot.Id)
	require.Equal(t, originalRoot.Password, replayedRoot.Password)
	require.Equal(t, originalRoot.CreatedAt, replayedRoot.CreatedAt)
	require.Equal(t, originalSetup, replayedSetup)

	_, err = ProvisionProductionRoot("root-admin", productionRootTestPassword+"-different", "v-original")
	require.ErrorIs(t, err, ErrProductionRootProvisionConflict)
	var afterConflict User
	var setupAfterConflict Setup
	require.NoError(t, database.First(&afterConflict).Error)
	require.NoError(t, database.First(&setupAfterConflict).Error)
	require.Equal(t, originalRoot.Password, afterConflict.Password)
	require.Equal(t, originalRoot.Id, afterConflict.Id)
	require.Equal(t, originalSetup, setupAfterConflict)

	_, err = ProvisionProductionRoot("root-admin", productionRootTestPassword, "v-different")
	require.ErrorIs(t, err, ErrProductionRootProvisionConflict)
	require.NoError(t, database.First(&setupAfterConflict).Error)
	require.Equal(t, originalSetup, setupAfterConflict)
}

func TestProvisionProductionRootRejectsEveryPersistedRootShapeDrift(t *testing.T) {
	tests := []struct {
		name   string
		column string
		value  any
	}{
		{name: "username", column: "username", value: "different-root"},
		{name: "password hash", column: "password", value: "not-a-password-hash"},
		{name: "display name", column: "display_name", value: "Different Root"},
		{name: "role", column: "role", value: common.RoleAdminUser},
		{name: "status", column: "status", value: common.UserStatusDisabled},
		{name: "email", column: "email", value: "root@example.invalid"},
		{name: "GitHub identity", column: "github_id", value: "github-root"},
		{name: "Discord identity", column: "discord_id", value: "discord-root"},
		{name: "OIDC identity", column: "oidc_id", value: "oidc-root"},
		{name: "WeChat identity", column: "wechat_id", value: "wechat-root"},
		{name: "Telegram identity", column: "telegram_id", value: "telegram-root"},
		{name: "LinuxDO identity", column: "linux_do_id", value: "linuxdo-root"},
		{name: "access token", column: "access_token", value: "12345678901234567890123456789012"},
		{name: "quota", column: "quota", value: 99999999},
		{name: "used quota", column: "used_quota", value: 1},
		{name: "request count", column: "request_count", value: 1},
		{name: "group", column: "group", value: "other"},
		{name: "affiliate code", column: "aff_code", value: "root-affiliate"},
		{name: "affiliate count", column: "aff_count", value: 1},
		{name: "affiliate quota", column: "aff_quota", value: 1},
		{name: "affiliate history", column: "aff_history", value: 1},
		{name: "inviter", column: "inviter_id", value: 1},
		{name: "setting", column: "setting", value: `{}`},
		{name: "remark", column: "remark", value: "changed"},
		{name: "Stripe customer", column: "stripe_customer", value: "cus_root"},
		{name: "last login", column: "last_login_at", value: int64(1)},
		{name: "auth version", column: "auth_version", value: int64(2)},
		{name: "soft delete", column: "deleted_at", value: time.Now()},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			database := prepareProductionRootSQLiteTest(t)
			_, err := ProvisionProductionRoot("root-admin", productionRootTestPassword, "v-test")
			require.NoError(t, err)
			var root User
			var setup Setup
			require.NoError(t, database.First(&root).Error)
			require.NoError(t, database.First(&setup).Error)
			require.NoError(t, database.Unscoped().Model(&User{}).Where("id = ?", root.Id).Update(test.column, test.value).Error)

			result, err := ProvisionProductionRoot("root-admin", productionRootTestPassword, "v-test")
			require.ErrorIs(t, err, ErrProductionRootProvisionConflict)
			require.Equal(t, ProductionRootProvisionResult{Username: "root-admin"}, result)
			var setupAfter Setup
			require.NoError(t, database.First(&setupAfter).Error)
			require.Equal(t, setup, setupAfter)
		})
	}
}

func TestProvisionProductionRootRejectsPartialAndUsedState(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(t *testing.T, database *gorm.DB, root *User, setup *Setup)
	}{
		{
			name: "root only",
			mutate: func(t *testing.T, database *gorm.DB, _ *User, setup *Setup) {
				require.NoError(t, database.Delete(setup).Error)
			},
		},
		{
			name: "setup only",
			mutate: func(t *testing.T, database *gorm.DB, root *User, _ *Setup) {
				require.NoError(t, database.Unscoped().Delete(root).Error)
			},
		},
		{
			name: "disabled root",
			mutate: func(t *testing.T, database *gorm.DB, root *User, _ *Setup) {
				require.NoError(t, database.Model(root).Update("status", common.UserStatusDisabled).Error)
			},
		},
		{
			name: "system access token",
			mutate: func(t *testing.T, database *gorm.DB, root *User, _ *Setup) {
				require.NoError(t, database.Model(root).Update("access_token", "12345678901234567890123456789012").Error)
			},
		},
		{
			name: "API token footprint",
			mutate: func(t *testing.T, database *gorm.DB, root *User, _ *Setup) {
				require.NoError(t, database.Create(&Token{UserId: root.Id, Key: uuid.NewString()}).Error)
			},
		},
		{
			name: "session footprint",
			mutate: func(t *testing.T, database *gorm.DB, root *User, _ *Setup) {
				require.NoError(t, database.Create(&UserSession{
					SID: uuid.NewString(), UserID: root.Id, UserAuthVersion: 1,
					Status: UserSessionStatusRevoked, RefreshHash: strings.Repeat("a", 64),
					LoginMethod: "password",
				}).Error)
			},
		},
		{
			name: "external identity footprint",
			mutate: func(t *testing.T, database *gorm.DB, root *User, _ *Setup) {
				require.NoError(t, database.Create(&ExternalIdentityClaim{
					Provider: "oidc", Subject: "subject", UserId: root.Id,
				}).Error)
			},
		},
		{
			name: "prior login marker",
			mutate: func(t *testing.T, database *gorm.DB, root *User, _ *Setup) {
				require.NoError(t, database.Model(root).Update("last_login_at", int64(1)).Error)
			},
		},
		{
			name: "email login footprint",
			mutate: func(t *testing.T, database *gorm.DB, root *User, _ *Setup) {
				require.NoError(t, database.Model(root).Update("email", "root@example.invalid").Error)
			},
		},
		{
			name: "invalid setup marker",
			mutate: func(t *testing.T, database *gorm.DB, _ *User, setup *Setup) {
				require.NoError(t, database.Model(setup).Update("initialized_at", int64(0)).Error)
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			database := prepareProductionRootSQLiteTest(t)
			_, err := ProvisionProductionRoot("root-admin", productionRootTestPassword, "v-test")
			require.NoError(t, err)
			var root User
			var setup Setup
			require.NoError(t, database.First(&root).Error)
			require.NoError(t, database.First(&setup).Error)
			test.mutate(t, database, &root, &setup)

			_, err = ProvisionProductionRoot("root-admin", productionRootTestPassword, "v-test")
			require.ErrorIs(t, err, ErrProductionRootProvisionConflict)
		})
	}
}

func TestProvisionProductionRootRejectsPreexistingUnrelatedState(t *testing.T) {
	database := prepareProductionRootSQLiteTest(t)
	hash, err := common.Password2Hash(productionRootTestPassword)
	require.NoError(t, err)
	require.NoError(t, database.Create(&User{
		Username: "ordinary-user", Password: hash, Role: common.RoleCommonUser,
		Status: common.UserStatusEnabled, AffCode: "ordinary-user-aff",
	}).Error)

	_, err = ProvisionProductionRoot("root-admin", productionRootTestPassword, "v-test")
	require.ErrorIs(t, err, ErrProductionRootProvisionConflict)
	var setupCount int64
	require.NoError(t, database.Model(&Setup{}).Count(&setupCount).Error)
	require.Zero(t, setupCount)
}

func TestValidateProductionRootCredentialsUsesASCIISafeUsernameAndStrictPasswordBytes(t *testing.T) {
	require.NoError(t, ValidateProductionRootCredentials("Root.ops_01-admin", productionRootTestPassword))

	invalidUsernames := []string{
		"", " root", "root ", "_root", "-root", ".root", "root/admin", "root admin",
		"rsvc_admin", "RSVC_admin", "RsVc_admin",
		"管理员", "root\u202eadmin", "root@example", strings.Repeat("a", UserNameMaxLength+1),
	}
	for _, username := range invalidUsernames {
		require.Error(t, ValidateProductionRootCredentials(username, productionRootTestPassword), username)
	}

	invalidPasswords := []string{
		strings.Repeat("p", 31),
		strings.Repeat("p", 73),
		" " + strings.Repeat("p", 32),
		strings.Repeat("p", 32) + " ",
		strings.Repeat("p", 16) + "\n" + strings.Repeat("p", 16),
		strings.Repeat("p", 16) + "\x00" + strings.Repeat("p", 16),
		strings.Repeat("p", 16) + "\ufeff" + strings.Repeat("p", 16),
		string(append([]byte{0xff}, []byte(strings.Repeat("p", 32))...)),
		strings.Repeat("Ab3_def-", 4),
		"replace-me-with-a-production-root-secret-2026",
	}
	for _, password := range invalidPasswords {
		err := ValidateProductionRootCredentials("root-admin", password)
		require.Error(t, err)
		require.NotContains(t, err.Error(), password)
	}
}

func TestProvisionProductionRootRejectsInvalidVersionWithoutWrites(t *testing.T) {
	database := prepareProductionRootSQLiteTest(t)
	_, err := ProvisionProductionRoot("root-admin", productionRootTestPassword, "\n")
	require.Error(t, err)
	require.False(t, errors.Is(err, ErrProductionRootProvisionConflict))
	var userCount int64
	var setupCount int64
	require.NoError(t, database.Model(&User{}).Count(&userCount).Error)
	require.NoError(t, database.Model(&Setup{}).Count(&setupCount).Error)
	require.Zero(t, userCount)
	require.Zero(t, setupCount)
}

func TestProvisionProductionRootRollsBackRootWhenSetupInsertFails(t *testing.T) {
	database := prepareProductionRootSQLiteTest(t)
	callbackName := "test:fail-production-root-setup-insert"
	require.NoError(t, database.Callback().Create().Before("gorm:create").Register(callbackName, func(tx *gorm.DB) {
		if tx.Statement != nil && tx.Statement.Schema != nil && tx.Statement.Schema.Table == "setups" {
			tx.AddError(errors.New("injected setup insert failure"))
		}
	}))
	t.Cleanup(func() {
		require.NoError(t, database.Callback().Create().Remove(callbackName))
	})

	result, err := ProvisionProductionRoot("root-admin", productionRootTestPassword, "v-test")
	require.ErrorContains(t, err, "injected setup insert failure")
	require.False(t, result.RootCreated)
	require.False(t, result.SetupCreated)
	var userCount int64
	var setupCount int64
	require.NoError(t, database.Unscoped().Model(&User{}).Count(&userCount).Error)
	require.NoError(t, database.Model(&Setup{}).Count(&setupCount).Error)
	require.Zero(t, userCount)
	require.Zero(t, setupCount)
}
