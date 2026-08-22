package model

import (
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func TestProtectedRelayRejectsActiveCodexChannelMutations(t *testing.T) {
	t.Setenv(relayDatabaseRoleAttestationEnvironment, "true")
	channels := []Channel{
		{Name: "codex-active-create", Type: constant.ChannelTypeCodex, Status: common.ChannelStatusEnabled},
		{Name: "codex-disabled", Type: constant.ChannelTypeCodex, Status: common.ChannelStatusManuallyDisabled, Tag: common.GetPointer("codex-protected")},
		{Name: "ordinary-active", Type: constant.ChannelTypeOpenAI, Status: common.ChannelStatusEnabled},
	}
	t.Cleanup(func() {
		DB.Session(&gorm.Session{SkipHooks: true}).Where("name IN ?", []string{
			"codex-active-create", "codex-disabled", "ordinary-active",
		}).Delete(&Channel{})
	})

	require.ErrorContains(t, DB.Create(&channels[0]).Error, "rejects active Codex channels")
	require.NoError(t, DB.Create(&channels[1]).Error)
	require.NoError(t, DB.Create(&channels[2]).Error)

	require.False(t, UpdateChannelStatus(channels[1].Id, "", common.ChannelStatusEnabled, "test"))
	require.ErrorContains(t, EnableChannelByTag("codex-protected"), "rejects enabling Codex channels")

	channels[1].Status = common.ChannelStatusEnabled
	require.ErrorContains(t, channels[1].SaveWithoutKey(), "rejects active Codex channels")

	channels[2].Type = constant.ChannelTypeCodex
	require.ErrorContains(t, channels[2].SaveWithoutKey(), "rejects active Codex channels")
}
