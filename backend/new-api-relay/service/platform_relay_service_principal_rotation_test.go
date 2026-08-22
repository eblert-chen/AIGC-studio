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
	protectedPlatformRelayRotationAttemptID     = strings.Repeat("a", 64)
	protectedPlatformRelayRotationNewAttemptID  = strings.Repeat("b", 64)
	protectedPlatformRelayRotationCurrentTokenA = "sk-A1b2C3d4E5f6G7h8J9k0L1m2N3p4Q5r6S7t8U9v0W1x2Y3z4"
	protectedPlatformRelayRotationCurrentTokenB = "sk-Z9y8X7w6V5u4T3s2R1q0P9n8M7k6J5h4G3f2E1d0C9b8A7z6"
	protectedPlatformRelayRotationTokenC        = "sk-C7d8E9f0G1h2J3k4L5m6N7p8Q9r0S1t2U3v4W5x6Y7z8A9b0"
	protectedPlatformRelayRotationTokenD        = "sk-M3n4P5q6R7s8T9u0V1w2X3y4Z5a6B7c8D9e0F1g2H3i4J5k6"
	protectedPlatformRelayRotationTokenE        = "sk-R7s8T9u0V1w2X3y4Z5a6B7c8D9e0F1g2H3i4J5k6L7m8N9p0"
)

func protectedPlatformRelayRotationInputs() (
	[]PlatformRelayServicePrincipalProvisionInput,
	[]PlatformRelayServicePrincipalProvisionInput,
) {
	current := []PlatformRelayServicePrincipalProvisionInput{
		{ClientID: "client-a", TenantID: "00000000-0000-4000-8000-000000000001", UpstreamToken: protectedPlatformRelayRotationCurrentTokenA},
		{ClientID: "client-b", TenantID: "00000000-0000-4000-8000-000000000002", UpstreamToken: protectedPlatformRelayRotationCurrentTokenB},
	}
	desired := []PlatformRelayServicePrincipalProvisionInput{
		{ClientID: current[0].ClientID, TenantID: current[0].TenantID, UpstreamToken: protectedPlatformRelayRotationTokenC},
		{ClientID: current[1].ClientID, TenantID: current[1].TenantID, UpstreamToken: protectedPlatformRelayRotationTokenD},
	}
	return current, desired
}

func seedProtectedPlatformRelayRotation(t *testing.T) (
	[]PlatformRelayServicePrincipalProvisionInput,
	[]PlatformRelayServicePrincipalProvisionInput,
) {
	t.Helper()
	seedProtectedPlatformRelayProvisionRoot(t)
	current, desired := protectedPlatformRelayRotationInputs()
	_, err := ProvisionProtectedPlatformRelayServicePrincipals(current)
	require.NoError(t, err)
	return current, desired
}

func requireProtectedPlatformRelayPrincipalSet(
	t *testing.T,
	inputs []PlatformRelayServicePrincipalProvisionInput,
) {
	t.Helper()
	expected, err := buildProtectedPlatformRelayExpectedPrincipals(inputs)
	require.NoError(t, err)
	require.NoError(t, model.DB.Transaction(func(tx *gorm.DB) error {
		return validateProtectedPlatformRelayServicePrincipalsTx(tx, expected)
	}))
}

func TestProtectedPlatformRelayServicePrincipalRotationIsAtomicAndExactlyReplayable(t *testing.T) {
	current, desired := seedProtectedPlatformRelayRotation(t)

	result, err := RotateProtectedPlatformRelayServicePrincipals(
		protectedPlatformRelayRotationAttemptID, current, desired,
	)
	require.NoError(t, err)
	require.Equal(t, PlatformRelayServicePrincipalRotationStateRotated, result.State)
	require.Equal(t, 2, result.Count)
	require.Equal(t, 2, result.RotatedCount)
	require.Equal(t, protectedPlatformRelayRotationAttemptID, result.AttemptID)
	for _, input := range append(append([]PlatformRelayServicePrincipalProvisionInput(nil), current...), desired...) {
		require.NotContains(t, result.AttemptID, input.UpstreamToken)
		require.NotContains(t, result.AttemptID, strings.TrimPrefix(input.UpstreamToken, "sk-"))
	}
	requireProtectedPlatformRelayPrincipalSet(t, desired)

	replay, err := RotateProtectedPlatformRelayServicePrincipals(
		protectedPlatformRelayRotationAttemptID, current, desired,
	)
	require.NoError(t, err)
	require.Equal(t, PlatformRelayServicePrincipalRotationStateReplayed, replay.State)
	require.Equal(t, result.AttemptID, replay.AttemptID)
	require.Equal(t, result.Count, replay.Count)
	require.Equal(t, result.RotatedCount, replay.RotatedCount)
	requireProtectedPlatformRelayPrincipalSet(t, desired)
}

func TestProtectedPlatformRelayServicePrincipalRotationMayRotateOneTokenWithoutReissuingOtherIdentities(t *testing.T) {
	current, desired := seedProtectedPlatformRelayRotation(t)
	desired[1].UpstreamToken = current[1].UpstreamToken

	result, err := RotateProtectedPlatformRelayServicePrincipals(
		protectedPlatformRelayRotationAttemptID, current, desired,
	)
	require.NoError(t, err)
	require.Equal(t, PlatformRelayServicePrincipalRotationStateRotated, result.State)
	require.Equal(t, 1, result.RotatedCount)
	requireProtectedPlatformRelayPrincipalSet(t, desired)

	replay, err := RotateProtectedPlatformRelayServicePrincipals(
		protectedPlatformRelayRotationAttemptID, current, desired,
	)
	require.NoError(t, err)
	require.Equal(t, PlatformRelayServicePrincipalRotationStateReplayed, replay.State)
	require.Equal(t, result.AttemptID, replay.AttemptID)
}

func TestProtectedPlatformRelayServicePrincipalRotationRejectsIdentityChangesAndTokenReassignment(t *testing.T) {
	current, desired := seedProtectedPlatformRelayRotation(t)

	t.Run("tenant replacement", func(t *testing.T) {
		invalid := append([]PlatformRelayServicePrincipalProvisionInput(nil), desired...)
		invalid[1].TenantID = "00000000-0000-4000-8000-000000000003"
		_, err := RotateProtectedPlatformRelayServicePrincipals(protectedPlatformRelayRotationAttemptID, current, invalid)
		require.ErrorIs(t, err, ErrProtectedPlatformRelayPrincipalRotationConflict)
		requireProtectedPlatformRelayPrincipalSet(t, current)
	})

	t.Run("principal removal", func(t *testing.T) {
		_, err := RotateProtectedPlatformRelayServicePrincipals(protectedPlatformRelayRotationAttemptID, current, desired[:1])
		require.ErrorIs(t, err, ErrProtectedPlatformRelayPrincipalRotationConflict)
		requireProtectedPlatformRelayPrincipalSet(t, current)
	})

	t.Run("principal addition", func(t *testing.T) {
		invalid := append(append([]PlatformRelayServicePrincipalProvisionInput(nil), desired...), PlatformRelayServicePrincipalProvisionInput{
			ClientID: "client-c", TenantID: "00000000-0000-4000-8000-000000000003",
			UpstreamToken: protectedPlatformRelayRotationTokenE,
		})
		_, err := RotateProtectedPlatformRelayServicePrincipals(protectedPlatformRelayRotationAttemptID, current, invalid)
		require.ErrorIs(t, err, ErrProtectedPlatformRelayPrincipalRotationConflict)
		requireProtectedPlatformRelayPrincipalSet(t, current)
	})

	t.Run("current token reassignment", func(t *testing.T) {
		invalid := append([]PlatformRelayServicePrincipalProvisionInput(nil), desired...)
		invalid[0].UpstreamToken = current[1].UpstreamToken
		_, err := RotateProtectedPlatformRelayServicePrincipals(protectedPlatformRelayRotationAttemptID, current, invalid)
		require.ErrorIs(t, err, ErrProtectedPlatformRelayPrincipalRotationConflict)
		requireProtectedPlatformRelayPrincipalSet(t, current)
	})

	t.Run("no token change", func(t *testing.T) {
		_, err := RotateProtectedPlatformRelayServicePrincipals(protectedPlatformRelayRotationAttemptID, current, current)
		require.ErrorIs(t, err, ErrProtectedPlatformRelayPrincipalRotationConflict)
		requireProtectedPlatformRelayPrincipalSet(t, current)
	})
}

func TestProtectedPlatformRelayServicePrincipalRotationRejectsStaleCurrentAndKeyCollisionWithoutWrites(t *testing.T) {
	current, desired := seedProtectedPlatformRelayRotation(t)

	stale := append([]PlatformRelayServicePrincipalProvisionInput(nil), current...)
	stale[0].UpstreamToken = protectedPlatformRelayRotationTokenE
	_, err := RotateProtectedPlatformRelayServicePrincipals(protectedPlatformRelayRotationAttemptID, stale, desired)
	require.ErrorIs(t, err, ErrProtectedPlatformRelayPrincipalRotationConflict)
	requireProtectedPlatformRelayPrincipalSet(t, current)

	ordinary := model.User{
		Username: "ordinary-key-owner", Password: "ordinary-password", DisplayName: "Ordinary",
		Role: common.RoleCommonUser, Status: common.UserStatusEnabled, Group: "default",
		AuthVersion: 1, AffCode: "ordinary-key-owner-aff",
	}
	require.NoError(t, model.DB.Create(&ordinary).Error)
	collisionKey := strings.TrimPrefix(protectedPlatformRelayRotationTokenE, "sk-")
	require.NoError(t, model.DB.Create(&model.Token{
		UserId: ordinary.Id, Key: collisionKey, Name: "ordinary-token",
		Status: common.TokenStatusEnabled, ExpiredTime: -1, UnlimitedQuota: true,
	}).Error)
	collidingDesired := append([]PlatformRelayServicePrincipalProvisionInput(nil), desired...)
	collidingDesired[0].UpstreamToken = protectedPlatformRelayRotationTokenE
	_, err = RotateProtectedPlatformRelayServicePrincipals(protectedPlatformRelayRotationAttemptID, current, collidingDesired)
	require.ErrorIs(t, err, ErrProtectedPlatformRelayPrincipalRotationConflict)
	require.NotContains(t, err.Error(), collisionKey)
	require.NotContains(t, err.Error(), protectedPlatformRelayRotationTokenE)
	requireProtectedPlatformRelayPrincipalSet(t, current)
}

func TestProtectedPlatformRelayServicePrincipalRotationRollsBackEveryTokenOnLateFailure(t *testing.T) {
	current, desired := seedProtectedPlatformRelayRotation(t)
	failingPurpose := protectedPlatformRelayTokenPurpose(current[1].ClientID, current[1].TenantID)
	trigger := fmt.Sprintf(`
CREATE TRIGGER fail_platform_relay_rotation
BEFORE UPDATE OF key ON tokens
WHEN OLD.name = %q
BEGIN
  SELECT RAISE(ABORT, 'database-detail-must-not-escape');
END`, failingPurpose)
	require.NoError(t, model.DB.Exec(trigger).Error)

	_, err := RotateProtectedPlatformRelayServicePrincipals(protectedPlatformRelayRotationAttemptID, current, desired)
	require.ErrorIs(t, err, ErrProtectedPlatformRelayPrincipalRotationConflict)
	require.NotContains(t, err.Error(), "database-detail-must-not-escape")
	// client-a is updated before client-b. A transaction that were not atomic
	// would therefore leave the first token at its desired value.
	requireProtectedPlatformRelayPrincipalSet(t, current)

	require.NoError(t, model.DB.Exec("DROP TRIGGER fail_platform_relay_rotation").Error)
	result, err := RotateProtectedPlatformRelayServicePrincipals(
		protectedPlatformRelayRotationAttemptID, current, desired,
	)
	require.NoError(t, err)
	require.Equal(t, PlatformRelayServicePrincipalRotationStateRotated, result.State)
	requireProtectedPlatformRelayPrincipalSet(t, desired)
}

func TestProtectedPlatformRelayServicePrincipalRotationAttemptIdentityIsIndependentReceiptRandomness(t *testing.T) {
	current, desired := seedProtectedPlatformRelayRotation(t)
	for _, invalid := range []string{"", strings.Repeat("0", 63), strings.Repeat("A", 64), "sha256:" + strings.Repeat("a", 64)} {
		_, err := RotateProtectedPlatformRelayServicePrincipals(invalid, current, desired)
		require.ErrorIs(t, err, ErrProtectedPlatformRelayPrincipalRotationConflict)
		requireProtectedPlatformRelayPrincipalSet(t, current)
	}

	rotated, err := RotateProtectedPlatformRelayServicePrincipals(
		protectedPlatformRelayRotationAttemptID, current, desired,
	)
	require.NoError(t, err)
	require.Equal(t, protectedPlatformRelayRotationAttemptID, rotated.AttemptID)
	replay, err := RotateProtectedPlatformRelayServicePrincipals(
		protectedPlatformRelayRotationNewAttemptID, current, desired,
	)
	require.NoError(t, err)
	require.Equal(t, PlatformRelayServicePrincipalRotationStateReplayed, replay.State)
	require.Equal(t, protectedPlatformRelayRotationNewAttemptID, replay.AttemptID)
}
