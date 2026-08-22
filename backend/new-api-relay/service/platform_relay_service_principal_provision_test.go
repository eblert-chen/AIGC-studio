package service

import (
	"fmt"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

var (
	protectedPlatformRelayProvisionTokenA = "sk-f46886b77bef238058b225eb30fd80db0002185b9ad3a410"
	protectedPlatformRelayProvisionTokenB = "sk-8715f49bbf886bacde04e97019d5e5011076c78aef796182"
)

func seedProtectedPlatformRelayProvisionRoot(t *testing.T) {
	t.Helper()
	truncate(t)
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	require.NoError(t, model.DB.Create(&model.User{
		Username: "root-admin", Password: "root-password-hash", DisplayName: "Root User",
		Role: common.RoleRootUser, Status: common.UserStatusEnabled, Group: "default",
		AuthVersion: 1, AffCode: "root-aff-code",
	}).Error)
	require.NoError(t, model.DB.Create(&model.Setup{Version: "v-test", InitializedAt: 1}).Error)
}

func protectedPlatformRelayProvisionInputs() []PlatformRelayServicePrincipalProvisionInput {
	return []PlatformRelayServicePrincipalProvisionInput{
		{ClientID: "client-a", TenantID: "00000000-0000-4000-8000-000000000001", UpstreamToken: protectedPlatformRelayProvisionTokenA},
		{ClientID: "client-b", TenantID: "00000000-0000-4000-8000-000000000002", UpstreamToken: protectedPlatformRelayProvisionTokenB},
	}
}

func TestParsePlatformRelayServicePrincipalsFileIsCanonicalAndStrict(t *testing.T) {
	valid := fmt.Sprintf(`{"kind":%q,"schema_version":1,"principals":[{"client_id":"client-a","tenant_id":"00000000-0000-4000-8000-000000000001","upstream_token":%q}]}`,
		PlatformRelayServicePrincipalsFileKind, protectedPlatformRelayProvisionTokenA)
	inputs, err := ParsePlatformRelayServicePrincipalsFile([]byte(valid))
	require.NoError(t, err)
	require.Len(t, inputs, 1)

	invalid := []string{
		`{}`,
		valid + `{}`,
		strings.Replace(valid, `"schema_version":1`, `"schema_version":1,"schema_version":1`, 1),
		strings.Replace(valid, `"principals":`, `"unknown":true,"principals":`, 1),
		strings.Replace(valid, protectedPlatformRelayProvisionTokenA, strings.TrimPrefix(protectedPlatformRelayProvisionTokenA, "sk-"), 1),
		strings.Replace(valid, `00000000-0000-4000-8000-000000000001`, "51BDF7C4-93A6-4B7C-A4A1-03F616A10F30", 1),
		strings.Replace(valid, `00000000-0000-4000-8000-000000000001`, "00000000-0000-0000-0000-000000000000", 1),
		strings.Replace(valid, protectedPlatformRelayProvisionTokenA, "sk-"+strings.Repeat("A", 48), 1),
		strings.Replace(valid, protectedPlatformRelayProvisionTokenA, "sk-"+strings.Repeat("A1b2C3d4", 6), 1),
		strings.Replace(valid, protectedPlatformRelayProvisionTokenA, "sk-replacewith"+strings.Repeat("A", 37), 1),
	}
	for _, document := range invalid {
		_, err := ParsePlatformRelayServicePrincipalsFile([]byte(document))
		require.Error(t, err)
		require.NotContains(t, err.Error(), strings.TrimPrefix(protectedPlatformRelayProvisionTokenA, "sk-"))
	}
	unsorted := fmt.Sprintf(`{"kind":%q,"schema_version":1,"principals":[{"client_id":"client-b","tenant_id":"00000000-0000-4000-8000-000000000002","upstream_token":%q},{"client_id":"client-a","tenant_id":"00000000-0000-4000-8000-000000000001","upstream_token":%q}]}`,
		PlatformRelayServicePrincipalsFileKind, protectedPlatformRelayProvisionTokenB, protectedPlatformRelayProvisionTokenA)
	_, err = ParsePlatformRelayServicePrincipalsFile([]byte(unsorted))
	require.Error(t, err)
}

func TestProvisionProtectedPlatformRelayServicePrincipalsCreatesAndExactlyReplays(t *testing.T) {
	seedProtectedPlatformRelayProvisionRoot(t)
	inputs := protectedPlatformRelayProvisionInputs()
	result, err := ProvisionProtectedPlatformRelayServicePrincipals(inputs)
	require.NoError(t, err)
	require.True(t, result.Created)
	require.Equal(t, 2, result.Count)

	expected, err := buildProtectedPlatformRelayExpectedPrincipals(inputs)
	require.NoError(t, err)
	require.NoError(t, model.DB.Transaction(func(tx *gorm.DB) error {
		return validateProtectedPlatformRelayServicePrincipalsTx(tx, expected)
	}))
	var firstUsers []model.User
	require.NoError(t, model.DB.Unscoped().Where("remark = ?", model.PlatformRelayServicePrincipalRemark).Order("id ASC").Find(&firstUsers).Error)
	var firstTokens []model.Token
	require.NoError(t, model.DB.Unscoped().Where("name LIKE ?", model.PlatformRelayServicePrincipalTokenNamePrefix+"%").Order("id ASC").Find(&firstTokens).Error)
	var root model.User
	require.NoError(t, model.DB.Where("role = ?", common.RoleRootUser).First(&root).Error)
	accessToken := "root-access-token-after-first-login"
	require.NoError(t, model.DB.Model(&root).Updates(map[string]any{
		"access_token":  &accessToken,
		"last_login_at": int64(42),
	}).Error)
	require.NoError(t, model.DB.Create(&model.UserSession{
		SID: "root-session-after-bootstrap", UserID: root.Id, Version: 1,
		UserAuthVersion: root.AuthVersion, Status: model.UserSessionStatusActive,
		RefreshHash: "root-refresh-hash", LoginMethod: "password", LastActiveAt: 42, ExpiresAt: 84,
	}).Error)

	replay, err := ProvisionProtectedPlatformRelayServicePrincipals(inputs)
	require.NoError(t, err)
	require.False(t, replay.Created)
	require.Equal(t, 2, replay.Count)
	var replayUsers []model.User
	require.NoError(t, model.DB.Unscoped().Where("remark = ?", model.PlatformRelayServicePrincipalRemark).Order("id ASC").Find(&replayUsers).Error)
	var replayTokens []model.Token
	require.NoError(t, model.DB.Unscoped().Where("name LIKE ?", model.PlatformRelayServicePrincipalTokenNamePrefix+"%").Order("id ASC").Find(&replayTokens).Error)
	require.Equal(t, firstUsers, replayUsers)
	require.Equal(t, firstTokens, replayTokens)
}

func TestProvisionProtectedPlatformRelayServicePrincipalsRejectsPartialAndBatchConflictWithoutWrites(t *testing.T) {
	seedProtectedPlatformRelayProvisionRoot(t)
	inputs := protectedPlatformRelayProvisionInputs()
	partialUser, err := BuildProtectedPlatformRelayServicePrincipalUser(inputs[0].ClientID, inputs[0].TenantID)
	require.NoError(t, err)
	require.NoError(t, model.DB.Create(&partialUser).Error)
	_, err = ProvisionProtectedPlatformRelayServicePrincipals(inputs)
	require.ErrorIs(t, err, ErrProtectedPlatformRelayPrincipalProvisionConflict)
	var reservedUserCount int64
	require.NoError(t, model.DB.Unscoped().Model(&model.User{}).Where("remark = ?", model.PlatformRelayServicePrincipalRemark).Count(&reservedUserCount).Error)
	require.EqualValues(t, 1, reservedUserCount)
	var reservedTokenCount int64
	require.NoError(t, model.DB.Unscoped().Model(&model.Token{}).Where("name LIKE ?", model.PlatformRelayServicePrincipalTokenNamePrefix+"%").Count(&reservedTokenCount).Error)
	require.Zero(t, reservedTokenCount)

	require.NoError(t, model.DB.Unscoped().Delete(&partialUser).Error)
	key, ok := protectedPlatformRelayTokenKey(inputs[1].UpstreamToken)
	require.True(t, ok)
	root := model.User{}
	require.NoError(t, model.DB.Where("role = ?", common.RoleRootUser).First(&root).Error)
	require.NoError(t, model.DB.Create(&model.Token{UserId: root.Id, Name: "ordinary", Key: key, Status: common.TokenStatusEnabled}).Error)
	_, err = ProvisionProtectedPlatformRelayServicePrincipals(inputs)
	require.ErrorIs(t, err, ErrProtectedPlatformRelayPrincipalProvisionConflict)
	require.NotContains(t, err.Error(), key)
	require.NoError(t, model.DB.Unscoped().Model(&model.User{}).Where("remark = ?", model.PlatformRelayServicePrincipalRemark).Count(&reservedUserCount).Error)
	require.Zero(t, reservedUserCount)
	require.NoError(t, model.DB.Unscoped().Model(&model.Token{}).Where("name LIKE ?", model.PlatformRelayServicePrincipalTokenNamePrefix+"%").Count(&reservedTokenCount).Error)
	require.Zero(t, reservedTokenCount)
}

func TestProvisionProtectedPlatformRelayServicePrincipalsRequiresExactRootSetup(t *testing.T) {
	truncate(t)
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	inputs := protectedPlatformRelayProvisionInputs()
	_, err := ProvisionProtectedPlatformRelayServicePrincipals(inputs)
	require.ErrorIs(t, err, ErrProtectedPlatformRelayPrincipalProvisionConflict)
	var count int64
	require.NoError(t, model.DB.Unscoped().Model(&model.User{}).Count(&count).Error)
	require.Zero(t, count)
}
