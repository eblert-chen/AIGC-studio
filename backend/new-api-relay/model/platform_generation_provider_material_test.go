package model

import (
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func TestPlatformGenerationProviderFailureClassificationAllowlist(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name    string
		status  string
		owner   string
		code    string
		allowed bool
	}{
		{name: "content policy is client owned", status: "failed", owner: PlatformProviderFailureOwnerClient, code: "content_policy_rejected", allowed: true},
		{name: "reviewed upstream service failure is provider owned", status: "failed", owner: PlatformProviderFailureOwnerProvider, code: "provider_service_failure", allowed: true},
		{name: "unclassified failure is relay owned", status: "failed", owner: PlatformProviderFailureOwnerRelay, code: "unclassified_provider_terminal", allowed: true},
		{name: "cancelled is client owned", status: "cancelled", owner: PlatformProviderFailureOwnerClient, code: "task_cancelled", allowed: true},
		{name: "expired is relay owned", status: "expired", owner: PlatformProviderFailureOwnerRelay, code: "task_expired", allowed: true},
		{name: "safe arbitrary provider code is rejected", status: "failed", owner: PlatformProviderFailureOwnerProvider, code: "account_revoked"},
		{name: "content policy cannot poison provider health", status: "failed", owner: PlatformProviderFailureOwnerProvider, code: "content_policy_rejected"},
		{name: "cancelled cannot be provider owned", status: "cancelled", owner: PlatformProviderFailureOwnerProvider, code: "provider_service_failure"},
		{name: "expired cannot be client owned", status: "expired", owner: PlatformProviderFailureOwnerClient, code: "task_cancelled"},
		{name: "unknown terminal status is rejected", status: "failure", owner: PlatformProviderFailureOwnerRelay, code: "unclassified_provider_terminal"},
		{name: "unsafe code is rejected", status: "failed", owner: PlatformProviderFailureOwnerRelay, code: "https://provider.example/?sig=secret"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			require.Equal(t, test.allowed, platformGenerationProviderFailureClassificationAllowed(test.status, test.owner, test.code))
		})
	}
}

func createPlatformGenerationProviderCredentialFixture(
	t *testing.T,
	database *gorm.DB,
	tenantID string,
	route PlatformGenerationProviderRoute,
) string {
	t.Helper()
	require.NoError(t, database.AutoMigrate(&ProviderCredentialVersion{}))
	credentialVersion := uuid.NewString()
	require.NoError(t, database.Create(&ProviderCredentialVersion{
		CredentialVersion: credentialVersion,
		TenantID:          tenantID,
		ChannelID:         route.ChannelID,
		KeyIndex:          route.KeyIndex,
		KeyFingerprint:    route.KeyFingerprint,
		KeyID:             "test-" + uuid.NewString(),
		Version:           1,
		Nonce:             []byte("test-nonce"),
		Ciphertext:        []byte("test-ciphertext"),
		CreatedAt:         time.Now().UTC(),
	}).Error)
	return credentialVersion
}
