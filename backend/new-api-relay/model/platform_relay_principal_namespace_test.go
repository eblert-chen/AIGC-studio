package model

import (
	"errors"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/stretchr/testify/require"
)

func TestProtectedPlatformRelayPrincipalNamespaceCannotBeClaimedByApplicationWrites(t *testing.T) {
	database := prepareProductionRootSQLiteTest(t)
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")

	reservedToken := &Token{Name: PlatformRelayServicePrincipalTokenNamePrefix + "client:tenant"}
	require.ErrorIs(t, reservedToken.Insert(), ErrPlatformRelayServicePrincipalNamespaceReserved)
	require.ErrorIs(t, reservedToken.Update(), ErrPlatformRelayServicePrincipalNamespaceReserved)
	require.ErrorIs(t, reservedToken.Delete(), ErrPlatformRelayServicePrincipalNamespaceReserved)

	reservedUsername := &User{Username: PlatformRelayServicePrincipalUsernamePrefix + "candidate", Password: "ordinary-password"}
	require.ErrorIs(t, reservedUsername.prepareForInsert(database), ErrPlatformRelayServicePrincipalNamespaceReserved)
	uppercaseReservedUsername := &User{Username: "RSVC_candidate", Password: "ordinary-password"}
	require.ErrorIs(t, uppercaseReservedUsername.prepareForInsert(database), ErrPlatformRelayServicePrincipalNamespaceReserved)
	reservedRemark := &User{Username: "ordinary-user", Password: "ordinary-password", Remark: PlatformRelayServicePrincipalRemark}
	require.ErrorIs(t, reservedRemark.prepareForInsert(database), ErrPlatformRelayServicePrincipalNamespaceReserved)

	ordinary := &User{Username: "rsvcXordinary", Password: "ordinary-password", Role: common.RoleCommonUser}
	require.NoError(t, ordinary.prepareForInsert(database))
	require.False(t, errors.Is(ordinary.UpdateWithTx(database, false), ErrPlatformRelayServicePrincipalNamespaceReserved))
}

func TestProtectedPlatformRelayPrincipalNamespaceRejectsCurrentRowMutationsAtomically(t *testing.T) {
	database := prepareProductionRootSQLiteTest(t)
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")

	principal := User{
		Username:    "RsVc_principal",
		Password:    "!invalid-service-password!",
		Email:       "principal@example.invalid",
		GitHubId:    "service-github",
		Role:        common.RoleCommonUser,
		Status:      common.UserStatusEnabled,
		Group:       "default",
		AuthVersion: 1,
		Quota:       0,
		UsedQuota:   0,
		AffCode:     "service-aff",
	}
	require.NoError(t, database.Create(&principal).Error)

	edited := principal
	edited.Username = "ordinary-renamed-user"
	edited.Password = "replacement-password"
	require.ErrorIs(t, edited.EditWithTx(database, true), ErrPlatformRelayServicePrincipalNamespaceReserved)
	require.ErrorIs(t, principal.UpdateGitHubId("replacement-github"), ErrPlatformRelayServicePrincipalNamespaceReserved)
	require.ErrorIs(t, UpdateUserSetting(principal.Id, principal.GetSetting()), ErrPlatformRelayServicePrincipalNamespaceReserved)
	require.ErrorIs(t, ResetUserPasswordByEmail(principal.Email, "replacement-password"), ErrPlatformRelayServicePrincipalNamespaceReserved)
	require.ErrorIs(t, IncreaseUserQuota(principal.Id, 10, true), ErrPlatformRelayServicePrincipalNamespaceReserved)
	require.ErrorIs(t, DecreaseUserQuota(principal.Id, 10, true), ErrPlatformRelayServicePrincipalNamespaceReserved)
	require.ErrorIs(t, OverrideUserQuota(principal.Id, 10), ErrPlatformRelayServicePrincipalNamespaceReserved)
	require.ErrorIs(t, principal.ClearBinding("github"), ErrPlatformRelayServicePrincipalNamespaceReserved)
	require.ErrorIs(t, principal.Delete(), ErrPlatformRelayServicePrincipalNamespaceReserved)
	require.ErrorIs(t, principal.HardDelete(), ErrPlatformRelayServicePrincipalNamespaceReserved)

	UpdateUserLastLoginAt(principal.Id)
	UpdateUserUsedQuotaAndRequestCount(principal.Id, 10)
	updateUserQuotaUsedQuotaAndRequestCount(principal.Id, 10, 10, 1)
	updateUserUsedQuota(principal.Id, 10)
	updateUserRequestCount(principal.Id, 1)

	var persisted User
	require.NoError(t, database.First(&persisted, principal.Id).Error)
	require.Equal(t, principal.Username, persisted.Username)
	require.Equal(t, principal.Password, persisted.Password)
	require.Equal(t, principal.GitHubId, persisted.GitHubId)
	require.Zero(t, persisted.Quota)
	require.Zero(t, persisted.UsedQuota)
	require.Zero(t, persisted.RequestCount)
	require.Zero(t, persisted.LastLoginAt)

	reservedToken := Token{
		UserId:      principal.Id,
		Name:        PlatformRelayServicePrincipalTokenNamePrefix + "client:tenant",
		Key:         "service-token-key",
		Status:      common.TokenStatusEnabled,
		RemainQuota: 0,
		UsedQuota:   0,
	}
	ordinaryToken := Token{
		UserId: principal.Id,
		Name:   "ordinary-token",
		Key:    "ordinary-token-key",
		Status: common.TokenStatusEnabled,
	}
	require.NoError(t, database.Create(&reservedToken).Error)
	require.NoError(t, database.Create(&ordinaryToken).Error)
	require.ErrorIs(t, IncreaseTokenQuota(reservedToken.Id, reservedToken.Key, 10), ErrPlatformRelayServicePrincipalNamespaceReserved)
	require.ErrorIs(t, DecreaseTokenQuota(reservedToken.Id, reservedToken.Key, 10), ErrPlatformRelayServicePrincipalNamespaceReserved)
	deleted, err := BatchDeleteTokens([]int{reservedToken.Id, ordinaryToken.Id}, principal.Id)
	require.ErrorIs(t, err, ErrPlatformRelayServicePrincipalNamespaceReserved)
	require.Zero(t, deleted)

	var tokenCount int64
	require.NoError(t, database.Model(&Token{}).Where("id IN ?", []int{reservedToken.Id, ordinaryToken.Id}).Count(&tokenCount).Error)
	require.EqualValues(t, 2, tokenCount)
	var persistedToken Token
	require.NoError(t, database.First(&persistedToken, reservedToken.Id).Error)
	require.Zero(t, persistedToken.RemainQuota)
	require.Zero(t, persistedToken.UsedQuota)
}
