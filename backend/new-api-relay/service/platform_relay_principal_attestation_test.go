package service

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

const protectedPlatformRelayTestTenant = "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30"

func configureProtectedPlatformRelayPrincipalStagingEnvironment(t *testing.T) {
	t.Helper()
	fixture := newPlatformRouteAcceptanceFixture(t)
	signedRoute := fixture.signedRoute(
		t,
		"staging",
		fixture.route,
		"11111111-2222-4333-8444-555555555555",
		fixture.now.Add(-time.Minute),
		fixture.now.Add(time.Hour),
	)
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("ENVIRONMENT", "staging")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "staging")
	t.Setenv("RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN", platformRelayRuntimeSecretsTestValue("principal-attestation-internal"))
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", "")
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", platformRouteJSONForTest(t, fixture.modelID, signedRoute))
	operationsToken := platformRelayRuntimeSecretsTestValue("principal-attestation-operations")
	operationsDigest := sha256.Sum256([]byte(operationsToken))
	t.Setenv("RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON", fmt.Sprintf(
		`[{"tenant_id":%q,"token_sha256":%q}]`,
		protectedPlatformRelayTestTenant,
		hex.EncodeToString(operationsDigest[:]),
	))
	t.Setenv("RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON", fmt.Sprintf(
		`[{"tenant_id":%q,"key_id":"principal-attestation-v1","secret":%q}]`,
		protectedPlatformRelayTestTenant,
		platformRelayRuntimeSecretsTestValue("principal-attestation-approval"),
	))
}

func configureProtectedPlatformRelayPrincipalTest(t *testing.T, tokenPresentation string) (string, *model.User, *model.Token) {
	t.Helper()
	truncate(t)
	configureProtectedPlatformRelayPrincipalStagingEnvironment(t)
	if tokenPresentation == "" {
		tokenPresentation = platformRelayRuntimeSecretsTestToken("principal-attestation-default")
	}
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", fmt.Sprintf(`{
		"platform": {
			"tenant_id": %q,
			"api_key": %q,
			"upstream_token": %q
		}
	}`, protectedPlatformRelayTestTenant, platformRelayRuntimeSecretsTestValue("principal-attestation-api"), tokenPresentation))
	key, ok := protectedPlatformRelayTokenKey(tokenPresentation)
	require.True(t, ok)
	serviceUser, err := BuildProtectedPlatformRelayServicePrincipalUser("platform", protectedPlatformRelayTestTenant)
	require.NoError(t, err)
	user := &serviceUser
	require.NoError(t, model.DB.Create(user).Error)
	token := &model.Token{
		UserId:         user.Id,
		Key:            key,
		Name:           protectedPlatformRelayTokenPurpose("platform", protectedPlatformRelayTestTenant),
		Status:         common.TokenStatusEnabled,
		ExpiredTime:    -1,
		UnlimitedQuota: true,
	}
	require.NoError(t, model.DB.Create(token).Error)
	return key, user, token
}

func TestProtectedPlatformRelayServicePrincipalAttestationIsExact(t *testing.T) {
	key, _, _ := configureProtectedPlatformRelayPrincipalTest(t, "")
	require.NoError(t, ValidateProtectedPlatformRelayServicePrincipals())
	principal, err := GetPlatformRelayPrincipalForJob("platform", protectedPlatformRelayTestTenant)
	require.NoError(t, err)
	require.Equal(t, "sk-"+key, principal.UpstreamToken)
}

func TestProtectedPlatformRelayServicePrincipalRejectsDrift(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(t *testing.T, user *model.User, token *model.Token)
	}{
		{"token disabled", func(t *testing.T, _ *model.User, token *model.Token) {
			require.NoError(t, model.DB.Model(token).Update("status", common.TokenStatusDisabled).Error)
		}},
		{"token finite", func(t *testing.T, _ *model.User, token *model.Token) {
			require.NoError(t, model.DB.Model(token).Update("unlimited_quota", false).Error)
		}},
		{"token expires", func(t *testing.T, _ *model.User, token *model.Token) {
			require.NoError(t, model.DB.Model(token).Update("expired_time", int64(123)).Error)
		}},
		{"token model limited", func(t *testing.T, _ *model.User, token *model.Token) {
			require.NoError(t, model.DB.Model(token).Updates(map[string]any{"model_limits_enabled": true, "model_limits": "video-model"}).Error)
		}},
		{"token group override", func(t *testing.T, _ *model.User, token *model.Token) {
			require.NoError(t, model.DB.Model(token).Update("group", "auto").Error)
		}},
		{"token cross group retry", func(t *testing.T, _ *model.User, token *model.Token) {
			require.NoError(t, model.DB.Model(token).Update("cross_group_retry", true).Error)
		}},
		{"token auto groups", func(t *testing.T, _ *model.User, token *model.Token) {
			require.NoError(t, model.DB.Model(token).Update("auto_groups", `["default"]`).Error)
		}},
		{"token IP policy", func(t *testing.T, _ *model.User, token *model.Token) {
			allow := "127.0.0.1/32"
			require.NoError(t, model.DB.Model(token).Update("allow_ips", &allow).Error)
		}},
		{"service user disabled", func(t *testing.T, user *model.User, _ *model.Token) {
			require.NoError(t, model.DB.Model(user).Update("status", common.UserStatusDisabled).Error)
		}},
		{"service user elevated", func(t *testing.T, user *model.User, _ *model.Token) {
			require.NoError(t, model.DB.Model(user).Update("role", common.RoleAdminUser).Error)
		}},
		{"service user has an interactive password", func(t *testing.T, user *model.User, _ *model.Token) {
			require.NoError(t, model.DB.Model(user).Update("password", "human-password-hash").Error)
		}},
		{"service user has an access token", func(t *testing.T, user *model.User, _ *model.Token) {
			accessToken := "interactive-access-token"
			require.NoError(t, model.DB.Model(user).Update("access_token", &accessToken).Error)
		}},
		{"service user has an email", func(t *testing.T, user *model.User, _ *model.Token) {
			require.NoError(t, model.DB.Model(user).Update("email", "human@example.invalid").Error)
		}},
		{"service user has logged in", func(t *testing.T, user *model.User, _ *model.Token) {
			require.NoError(t, model.DB.Model(user).Update("last_login_at", int64(123)).Error)
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, user, token := configureProtectedPlatformRelayPrincipalTest(t, "")
			test.mutate(t, user, token)
			require.ErrorIs(t, ValidateProtectedPlatformRelayServicePrincipals(), ErrProtectedPlatformRelayPrincipal)
		})
	}
}

func TestProtectedPlatformRelayServicePrincipalRejectsAmbiguousConfiguration(t *testing.T) {
	t.Run("tenant UUID is not canonical", func(t *testing.T) {
		configureProtectedPlatformRelayPrincipalTest(t, "")
		t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", fmt.Sprintf(`{
			"platform":{"tenant_id":%q,"api_key":%q,"upstream_token":%q}
		}`,
			"urn:uuid:"+protectedPlatformRelayTestTenant, strings.Repeat("A", 48), "sk-"+strings.Repeat("T", 48)))
		require.ErrorIs(t, ValidateProtectedPlatformRelayServicePrincipals(), ErrProtectedPlatformRelayPrincipal)
	})
	t.Run("client id cannot alias purpose separator", func(t *testing.T) {
		configureProtectedPlatformRelayPrincipalTest(t, "")
		t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", fmt.Sprintf(`{
			"platform:tenant":{"tenant_id":%q,"api_key":%q,"upstream_token":%q}
		}`,
			protectedPlatformRelayTestTenant, strings.Repeat("A", 48), "sk-"+strings.Repeat("T", 48)))
		require.ErrorIs(t, ValidateProtectedPlatformRelayServicePrincipals(), ErrProtectedPlatformRelayPrincipal)
	})
	t.Run("suffix token", func(t *testing.T) {
		truncate(t)
		configureProtectedPlatformRelayPrincipalStagingEnvironment(t)
		t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", fmt.Sprintf(`{"platform":{"tenant_id":%q,"api_key":%q,"upstream_token":%q}}`,
			protectedPlatformRelayTestTenant,
			strings.Repeat("A", 48),
			"sk-"+strings.Repeat("T", 48)+"-1"))
		require.ErrorIs(t, ValidateProtectedPlatformRelayServicePrincipals(), ErrProtectedPlatformRelayPrincipal)
	})
	t.Run("token reused across clients", func(t *testing.T) {
		key, _, _ := configureProtectedPlatformRelayPrincipalTest(t, "")
		t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", fmt.Sprintf(`{
			"platform":{"tenant_id":%q,"api_key":%q,"upstream_token":%q},
			"tiktok":{"tenant_id":%q,"api_key":%q,"upstream_token":%q}
		}`,
			protectedPlatformRelayTestTenant, strings.Repeat("A", 48), "sk-"+key,
			uuid.NewString(), strings.Repeat("B", 48), key))
		require.ErrorIs(t, ValidateProtectedPlatformRelayServicePrincipals(), ErrProtectedPlatformRelayPrincipal)
	})
	t.Run("API key reused across clients", func(t *testing.T) {
		configureProtectedPlatformRelayPrincipalTest(t, "")
		secondTenant := uuid.NewString()
		t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", fmt.Sprintf(`{
			"platform":{"tenant_id":%q,"api_key":%q,"upstream_token":%q},
			"tiktok":{"tenant_id":%q,"api_key":%q,"upstream_token":%q}
		}`,
			protectedPlatformRelayTestTenant, strings.Repeat("A", 48), "sk-"+strings.Repeat("T", 48),
			secondTenant, strings.Repeat("A", 48), "sk-"+strings.Repeat("U", 48)))
		require.ErrorIs(t, ValidateProtectedPlatformRelayServicePrincipals(), ErrProtectedPlatformRelayPrincipal)
	})
	t.Run("API key aliases another client upstream token", func(t *testing.T) {
		configureProtectedPlatformRelayPrincipalTest(t, "")
		secondTenant := uuid.NewString()
		t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", fmt.Sprintf(`{
			"platform":{"tenant_id":%q,"api_key":%q,"upstream_token":%q},
			"tiktok":{"tenant_id":%q,"api_key":%q,"upstream_token":%q}
		}`,
			protectedPlatformRelayTestTenant, "sk-"+strings.Repeat("U", 48), "sk-"+strings.Repeat("T", 48),
			secondTenant, strings.Repeat("B", 48), strings.Repeat("U", 48)))
		require.ErrorIs(t, ValidateProtectedPlatformRelayServicePrincipals(), ErrProtectedPlatformRelayPrincipal)
	})
	t.Run("API key aliases internal admission", func(t *testing.T) {
		configureProtectedPlatformRelayPrincipalTest(t, "")
		t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", fmt.Sprintf(`{
			"platform":{"tenant_id":%q,"api_key":%q,"upstream_token":%q}
		}`,
			protectedPlatformRelayTestTenant, strings.Repeat("I", 48), "sk-"+strings.Repeat("T", 48)))
		require.ErrorIs(t, ValidateProtectedPlatformRelayServicePrincipals(), ErrProtectedPlatformRelayPrincipal)
	})
	t.Run("callback secret reused across clients", func(t *testing.T) {
		configureProtectedPlatformRelayPrincipalTest(t, "")
		secondTenant := uuid.NewString()
		callbackSecret := strings.Repeat("C", 48)
		t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", fmt.Sprintf(`{
			"platform":{"tenant_id":%q,"api_key":%q,"upstream_token":%q,"callback_url":"http://platform.invalid/callback","callback_signing_secret":%q},
			"tiktok":{"tenant_id":%q,"api_key":%q,"upstream_token":%q,"callback_url":"http://tiktok.invalid/callback","callback_signing_secret":%q}
		}`,
			protectedPlatformRelayTestTenant, strings.Repeat("A", 48), "sk-"+strings.Repeat("T", 48), callbackSecret,
			secondTenant, strings.Repeat("B", 48), "sk-"+strings.Repeat("U", 48), callbackSecret))
		require.ErrorIs(t, ValidateProtectedPlatformRelayServicePrincipals(), ErrProtectedPlatformRelayPrincipal)
	})
	t.Run("approval secret aliases client API key", func(t *testing.T) {
		configureProtectedPlatformRelayPrincipalTest(t, "")
		operationsToken := strings.Repeat("O", 48)
		operationsDigest := sha256.Sum256([]byte(operationsToken))
		t.Setenv("RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON", fmt.Sprintf(`[{"tenant_id":%q,"token_sha256":%q}]`,
			protectedPlatformRelayTestTenant, hex.EncodeToString(operationsDigest[:])))
		t.Setenv("RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON", fmt.Sprintf(`[{"tenant_id":%q,"key_id":"approval-v1","secret":%q}]`,
			protectedPlatformRelayTestTenant, platformRelayRuntimeSecretsTestValue("principal-attestation-api")))
		require.ErrorIs(t, ValidateProtectedPlatformRelayServicePrincipals(), ErrProtectedPlatformRelayPrincipal)
	})
	t.Run("service user reused", func(t *testing.T) {
		_, user, _ := configureProtectedPlatformRelayPrincipalTest(t, "")
		secondTenant := uuid.NewString()
		secondKey := strings.Repeat("U", 48)
		t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", fmt.Sprintf(`{
			"platform":{"tenant_id":%q,"api_key":%q,"upstream_token":%q},
			"tiktok":{"tenant_id":%q,"api_key":%q,"upstream_token":%q}
		}`,
			protectedPlatformRelayTestTenant, strings.Repeat("A", 48), "sk-"+strings.Repeat("T", 48),
			secondTenant, strings.Repeat("B", 48), "sk-"+secondKey))
		require.NoError(t, model.DB.Create(&model.Token{
			UserId: user.Id, Key: secondKey,
			Name:   protectedPlatformRelayTokenPurpose("tiktok", secondTenant),
			Status: common.TokenStatusEnabled, ExpiredTime: -1, UnlimitedQuota: true,
		}).Error)
		require.ErrorIs(t, ValidateProtectedPlatformRelayServicePrincipals(), ErrProtectedPlatformRelayPrincipal)
	})
	t.Run("purpose has multiple token rows", func(t *testing.T) {
		_, user, token := configureProtectedPlatformRelayPrincipalTest(t, "")
		duplicate := &model.Token{
			UserId: user.Id, Key: strings.Repeat("V", 48), Name: token.Name,
			Status: common.TokenStatusEnabled, ExpiredTime: -1, UnlimitedQuota: true,
		}
		require.NoError(t, model.DB.Create(duplicate).Error)
		require.ErrorIs(t, ValidateProtectedPlatformRelayServicePrincipals(), ErrProtectedPlatformRelayPrincipal)
	})
	t.Run("service user owns an unrelated token", func(t *testing.T) {
		_, user, _ := configureProtectedPlatformRelayPrincipalTest(t, "")
		extra := &model.Token{
			UserId: user.Id, Key: strings.Repeat("V", 48), Name: "unrelated-service-token",
			Status: common.TokenStatusEnabled, ExpiredTime: -1, UnlimitedQuota: true,
		}
		require.NoError(t, model.DB.Create(extra).Error)
		require.ErrorIs(t, ValidateProtectedPlatformRelayServicePrincipals(), ErrProtectedPlatformRelayPrincipal)
	})
	t.Run("stale reserved user remains", func(t *testing.T) {
		configureProtectedPlatformRelayPrincipalTest(t, "")
		staleUser, err := BuildProtectedPlatformRelayServicePrincipalUser("stale", uuid.NewString())
		require.NoError(t, err)
		require.NoError(t, model.DB.Create(&staleUser).Error)
		require.ErrorIs(t, ValidateProtectedPlatformRelayServicePrincipals(), ErrProtectedPlatformRelayPrincipal)
	})
	t.Run("stale reserved token remains", func(t *testing.T) {
		_, user, _ := configureProtectedPlatformRelayPrincipalTest(t, "")
		stale := &model.Token{
			UserId: user.Id, Key: strings.Repeat("V", 48), Name: protectedPlatformRelayTokenPurpose("stale", uuid.NewString()),
			Status: common.TokenStatusEnabled, ExpiredTime: -1, UnlimitedQuota: true,
		}
		require.NoError(t, model.DB.Create(stale).Error)
		require.ErrorIs(t, ValidateProtectedPlatformRelayServicePrincipals(), ErrProtectedPlatformRelayPrincipal)
	})
	t.Run("similar ordinary username is outside reserved namespace", func(t *testing.T) {
		configureProtectedPlatformRelayPrincipalTest(t, "")
		ordinary := &model.User{
			Username: "rsvcXordinary", Password: "ordinary-password", Role: common.RoleCommonUser,
			Status: common.UserStatusEnabled, Group: "default", AffCode: "ordinary-aff-code",
		}
		require.NoError(t, model.DB.Create(ordinary).Error)
		require.NoError(t, ValidateProtectedPlatformRelayServicePrincipals())
	})
}

func TestProtectedPlatformRelayServicePrincipalRejectsInteractiveAuthenticationFootprint(t *testing.T) {
	tests := []struct {
		name   string
		create func(t *testing.T, userID int)
	}{
		{"session", func(t *testing.T, userID int) {
			require.NoError(t, model.DB.Create(&model.UserSession{
				SID: uuid.NewString(), UserID: userID, Version: 1, UserAuthVersion: 1,
				Status: model.UserSessionStatusActive, RefreshHash: strings.Repeat("a", 64),
				LoginMethod: "password", LastActiveAt: 1, ExpiresAt: 2,
			}).Error)
		}},
		{"auth flow", func(t *testing.T, userID int) {
			require.NoError(t, model.DB.Create(&model.AuthFlow{
				TokenHash: strings.Repeat("b", 64), Purpose: model.AuthFlowPurposeOAuth, UserId: userID,
				ExpiresAt: time.Now().Add(time.Hour),
			}).Error)
		}},
		{"external identity", func(t *testing.T, userID int) {
			require.NoError(t, model.DB.Create(&model.ExternalIdentityClaim{
				Provider: "test", Subject: uuid.NewString(), UserId: userID,
			}).Error)
		}},
		{"passkey", func(t *testing.T, userID int) {
			require.NoError(t, model.DB.Create(&model.PasskeyCredential{
				UserID: userID, CredentialID: "credential-" + uuid.NewString(), PublicKey: "public-key",
			}).Error)
		}},
		{"OAuth binding", func(t *testing.T, userID int) {
			require.NoError(t, model.DB.Create(&model.UserOAuthBinding{
				UserId: userID, ProviderId: 123, ProviderUserId: uuid.NewString(),
			}).Error)
		}},
		{"2FA", func(t *testing.T, userID int) {
			require.NoError(t, model.DB.Create(&model.TwoFA{UserId: userID, Secret: "secret", IsEnabled: true}).Error)
		}},
		{"2FA backup", func(t *testing.T, userID int) {
			require.NoError(t, model.DB.Create(&model.TwoFABackupCode{UserId: userID, CodeHash: "hash"}).Error)
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, user, _ := configureProtectedPlatformRelayPrincipalTest(t, "")
			test.create(t, user.Id)
			require.ErrorIs(t, ValidateProtectedPlatformRelayServicePrincipals(), ErrProtectedPlatformRelayPrincipal)
		})
	}
}

func TestProtectedPlatformRelayPrincipalDriftStopsBeforeProviderCall(t *testing.T) {
	key, _, token := configureProtectedPlatformRelayPrincipalTest(t, "")
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls.Add(1)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("RELAY_COMPAT_INTERNAL_BASE_URL", server.URL)
	job := model.PlatformGenerationJob{
		ID: uuid.NewString(), TenantID: protectedPlatformRelayTestTenant,
		SourceClientID: "platform", Status: model.PlatformGenerationStatusSubmitting,
	}
	require.NoError(t, model.DB.Create(&job).Error)
	require.NoError(t, model.DB.Model(token).Update("unlimited_quota", false).Error)
	started, _, err := submitPlatformNativeTask(
		context.Background(),
		model.PlatformGenerationClaim{Job: job},
		model.PlatformGenerationRouteClaim{},
		PlatformRelayPrincipal{ClientID: "platform", TenantID: protectedPlatformRelayTestTenant, UpstreamToken: "sk-" + key},
		[]byte(`{}`),
	)
	require.ErrorIs(t, err, ErrProtectedPlatformRelayPrincipal)
	require.False(t, started)
	require.Zero(t, calls.Load())
	require.NotContains(t, err.Error(), key)
}

func TestProtectedPlatformRelayNativeSubmissionRejectsExternalTargetBeforeRequest(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls.Add(1)
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_COMPAT_INTERNAL_BASE_URL", server.URL)
	_, err := platformNativeSubmissionBaseURL()
	require.EqualError(t, err, "protected Relay native submission target cannot be overridden")
	require.Zero(t, calls.Load())
}
