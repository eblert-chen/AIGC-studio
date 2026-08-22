package model

import (
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/glebarez/sqlite"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func TestCheckSetupRejectsEmptyProductionDatabase(t *testing.T) {
	previousDB := DB
	previousSetup := constant.Setup
	t.Cleanup(func() {
		DB = previousDB
		constant.Setup = previousSetup
	})
	t.Setenv("APP_ENV", "production")
	t.Setenv("DEPLOYMENT_ENV", "production")

	database, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, database.AutoMigrate(&Setup{}, &User{}))
	DB = database
	constant.Setup = false

	err = CheckSetup()
	require.ErrorContains(t, err, "provisioned out of band")
	require.False(t, constant.Setup)
}

func TestCheckSetupKeepsDevelopmentBootstrapAvailable(t *testing.T) {
	previousDB := DB
	previousSetup := constant.Setup
	t.Cleanup(func() {
		DB = previousDB
		constant.Setup = previousSetup
	})
	t.Setenv("APP_ENV", "development")
	t.Setenv("DEPLOYMENT_ENV", "")

	database, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, database.AutoMigrate(&Setup{}, &User{}))
	DB = database
	constant.Setup = false

	require.NoError(t, CheckSetup())
	require.False(t, constant.Setup)
}

func TestCheckSetupRejectsEmptyProtectedStagingDatabase(t *testing.T) {
	previousDB := DB
	previousSetup := constant.Setup
	t.Cleanup(func() {
		DB = previousDB
		constant.Setup = previousSetup
	})
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")

	database, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, database.AutoMigrate(&Setup{}, &User{}))
	DB = database
	constant.Setup = false

	err = CheckSetup()
	require.ErrorContains(t, err, "provisioned out of band")
	require.False(t, constant.Setup)
}

func TestCheckSetupRejectsProductionRootOnlyWithoutAutoHealing(t *testing.T) {
	previousDB := DB
	previousSetup := constant.Setup
	t.Cleanup(func() {
		DB = previousDB
		constant.Setup = previousSetup
	})
	t.Setenv("APP_ENV", "production")
	t.Setenv("DEPLOYMENT_ENV", "production")
	database, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, database.AutoMigrate(&Setup{}, &User{}))
	hash, err := common.Password2Hash(productionRootTestPassword)
	require.NoError(t, err)
	require.NoError(t, database.Create(&User{
		Username: "root-admin", Password: hash, Role: common.RoleRootUser,
		Status: common.UserStatusEnabled,
	}).Error)
	DB = database
	constant.Setup = false

	err = CheckSetup()
	require.ErrorContains(t, err, "partial root-only")
	require.False(t, constant.Setup)
	var setupCount int64
	require.NoError(t, database.Model(&Setup{}).Count(&setupCount).Error)
	require.Zero(t, setupCount)
}

func TestSetupReadyRequiresOneEnabledRootAndOneValidSetup(t *testing.T) {
	tests := []struct {
		name       string
		rootStates []int
		wantReady  bool
	}{
		{name: "one enabled root", rootStates: []int{common.UserStatusEnabled}, wantReady: true},
		{name: "disabled root", rootStates: []int{common.UserStatusDisabled}, wantReady: false},
		{name: "multiple roots", rootStates: []int{common.UserStatusEnabled, common.UserStatusEnabled}, wantReady: false},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			previousDB := DB
			t.Cleanup(func() { DB = previousDB })
			database, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
			require.NoError(t, err)
			require.NoError(t, database.AutoMigrate(&Setup{}, &User{}))
			require.NoError(t, database.Create(&Setup{Version: "v-test", InitializedAt: 1}).Error)
			hash, err := common.Password2Hash(productionRootTestPassword)
			require.NoError(t, err)
			for index, state := range test.rootStates {
				require.NoError(t, database.Create(&User{
					Username: "root-" + string(rune('a'+index)), Password: hash,
					Role: common.RoleRootUser, Status: state,
					AffCode: "root-aff-" + string(rune('a'+index)),
				}).Error)
			}
			DB = database
			require.Equal(t, test.wantReady, SetupReady())
		})
	}
}
