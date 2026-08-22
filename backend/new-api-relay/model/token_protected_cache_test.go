package model

import (
	"errors"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func TestProtectedTokenLookupRejectsPreheatedOldKeyAfterDatabaseRotation(t *testing.T) {
	truncateTables(t)
	useUserCacheMiniRedis(t)
	t.Setenv(relayDatabaseRoleAttestationEnvironment, "true")

	user := User{
		Username: "protected-token-cache-user", Password: "password-placeholder",
		DisplayName: "Protected Token Cache", Role: common.RoleCommonUser,
		Status: common.UserStatusEnabled, Group: "default", AuthVersion: 1,
		AffCode: "protected-token-cache-aff",
	}
	require.NoError(t, DB.Create(&user).Error)
	const (
		oldKey = "protected-old-token-cache-key"
		newKey = "protected-new-token-database-key"
	)
	token := Token{
		UserId: user.Id, Key: oldKey, Name: "ordinary-protected-cache-probe",
		Status: common.TokenStatusEnabled, ExpiredTime: -1, UnlimitedQuota: true,
	}
	require.NoError(t, DB.Create(&token).Error)
	require.NoError(t, cacheSetToken(token))

	preheated, err := cacheGetTokenByKey(oldKey)
	require.NoError(t, err)
	require.Equal(t, token.Id, preheated.Id)
	require.NoError(t, DB.Model(&Token{}).Where("id = ?", token.Id).UpdateColumn("key", newKey).Error)

	// The stale Redis row deliberately remains present. Protected lookup must
	// ignore it rather than depending on best-effort cache deletion by an
	// offline job that has no Redis credential.
	stale, err := cacheGetTokenByKey(oldKey)
	require.NoError(t, err)
	require.Equal(t, token.Id, stale.Id)
	_, err = GetTokenByKey(oldKey, false)
	require.ErrorIs(t, err, gorm.ErrRecordNotFound)
	_, err = ValidateUserToken(oldKey)
	require.ErrorIs(t, err, ErrTokenInvalid)

	current, err := GetTokenByKey(newKey, false)
	require.NoError(t, err)
	require.Equal(t, token.Id, current.Id)
	validated, err := ValidateUserToken(newKey)
	require.NoError(t, err)
	require.Equal(t, token.Id, validated.Id)
}

func TestUnprotectedTokenLookupRetainsRedisCompatibility(t *testing.T) {
	truncateTables(t)
	useUserCacheMiniRedis(t)
	t.Setenv(relayDatabaseRoleAttestationEnvironment, "false")

	const cachedOnlyKey = "unprotected-cache-compatibility-key"
	require.NoError(t, cacheSetToken(Token{
		Id: 913, UserId: 812, Key: cachedOnlyKey, Name: "cached-only",
		Status: common.TokenStatusEnabled, ExpiredTime: -1, UnlimitedQuota: true,
	}))
	token, err := GetTokenByKey(cachedOnlyKey, false)
	require.NoError(t, err)
	require.Equal(t, 913, token.Id)

	_, err = GetTokenByKey(cachedOnlyKey, true)
	require.True(t, errors.Is(err, gorm.ErrRecordNotFound))
}

func TestProtectedTokenInsertCollidesWithoutDriverErrorDetail(t *testing.T) {
	truncateTables(t)
	t.Setenv(relayDatabaseRoleAttestationEnvironment, "true")
	user := User{
		Username: "protected-token-insert-user", Password: "password-placeholder",
		DisplayName: "Protected Token Insert", Role: common.RoleCommonUser,
		Status: common.UserStatusEnabled, Group: "default", AuthVersion: 1,
		AffCode: "protected-token-insert-aff",
	}
	require.NoError(t, DB.Create(&user).Error)
	const key = "protected-token-insert-conflict-key-canary"
	first := Token{UserId: user.Id, Key: key, Name: "first", Status: common.TokenStatusEnabled}
	require.NoError(t, first.Insert())
	second := Token{UserId: user.Id, Key: key, Name: "second", Status: common.TokenStatusEnabled}
	err := second.Insert()
	require.ErrorIs(t, err, ErrTokenKeyConflict)
	require.NotContains(t, err.Error(), key)
	var count int64
	require.NoError(t, DB.Model(&Token{}).Where("key = ?", key).Count(&count).Error)
	require.Equal(t, int64(1), count)
}
