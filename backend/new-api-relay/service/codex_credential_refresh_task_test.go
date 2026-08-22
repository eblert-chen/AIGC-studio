package service

import (
	"context"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/stretchr/testify/require"
)

func TestCodexCredentialAutoRefreshSwitchIsExact(t *testing.T) {
	t.Setenv("RELAY_CODEX_CREDENTIAL_AUTO_REFRESH_ENABLED", "")
	enabled, err := CodexCredentialAutoRefreshEnabled()
	require.NoError(t, err)
	require.True(t, enabled)

	t.Setenv("RELAY_CODEX_CREDENTIAL_AUTO_REFRESH_ENABLED", "false")
	enabled, err = CodexCredentialAutoRefreshEnabled()
	require.NoError(t, err)
	require.False(t, enabled)

	for _, value := range []string{" false", "FALSE", "0", "true "} {
		t.Run(value, func(t *testing.T) {
			t.Setenv("RELAY_CODEX_CREDENTIAL_AUTO_REFRESH_ENABLED", value)
			_, err := CodexCredentialAutoRefreshEnabled()
			require.Error(t, err)
		})
	}
}

func TestProtectedCodexCredentialRefreshFailsBeforeExternalOrDatabaseWork(t *testing.T) {
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	_, _, err := RefreshCodexChannelCredential(context.Background(), 999999, CodexCredentialRefreshOptions{})
	require.ErrorContains(t, err, "protected Relay rejects Codex credential refresh")
}

func TestProtectedCodexLifecycleRejectsActiveChannelContinuously(t *testing.T) {
	truncate(t)
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
	channel := model.Channel{
		Name:   "protected-codex-lifecycle",
		Type:   constant.ChannelTypeCodex,
		Status: common.ChannelStatusEnabled,
	}
	require.NoError(t, model.DB.Create(&channel).Error)

	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	require.ErrorContains(t, ValidateCodexCredentialAutoRefreshLifecycle(false, true), "rejects active Codex channels")
}
