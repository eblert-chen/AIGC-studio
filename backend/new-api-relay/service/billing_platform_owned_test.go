package service

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strconv"
	"sync/atomic"
	"testing"

	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func protectedBillingTestContext(t *testing.T) (*gin.Context, *relaycommon.RelayInfo) {
	t.Helper()
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(nil)
	c.Request = httptest.NewRequest("POST", "/internal/platform-generations/native-submit", nil)
	c.Set(string(constant.ContextKeyPlatformGenerationPinnedRoute), true)
	info := &relaycommon.RelayInfo{
		TaskRelayInfo:           &relaycommon.TaskRelayInfo{PinnedProviderRoute: true},
		BillingSource:           BillingSourceWallet,
		SubscriptionId:          91,
		SubscriptionPreConsumed: 123,
		SubscriptionPostDelta:   456,
	}
	return c, info
}

func TestProtectedBillingRejectsForgedHeadersWithoutNativeAdmissionMarker(t *testing.T) {
	c, info := protectedBillingTestContext(t)
	// Headers and the public Gin flag are caller-controlled test inputs; neither
	// can create the private request-context marker installed by the middleware.
	c.Request.Header.Set("X-Platform-Generation-Internal", "forged")
	c.Request.Header.Set("X-Platform-Generation-Job-Id", "00000000-0000-0000-0000-000000000001")

	apiErr := PrepareProtectedRelayBilling(c, info)
	require.NotNil(t, apiErr)
	require.ErrorIs(t, apiErr, ErrProtectedRelayNativeBillingDisabled)
	require.Nil(t, info.Billing)
}

func TestProtectedPlatformBillingIsZeroSideEffectForPaidAndFreeSettlement(t *testing.T) {
	for _, estimatedQuota := range []int{0, 123456} {
		t.Run(strconv.Itoa(estimatedQuota), func(t *testing.T) {
			c, info := protectedBillingTestContext(t)
			AuthorizePlatformOwnedBilling(c)

			require.Nil(t, PrepareProtectedRelayBilling(c, info))
			require.True(t, IsPlatformOwnedBilling(info))
			require.Equal(t, BillingSourcePlatformExternal, info.BillingSource)
			require.Zero(t, info.FinalPreConsumedQuota)
			require.Zero(t, info.SubscriptionId)
			require.Zero(t, info.SubscriptionPreConsumed)
			require.Zero(t, info.SubscriptionPostDelta)

			// Paid paths may call PreConsume again after tier/group selection; the
			// same Platform-owned sentinel remains zero-side-effect.
			require.Nil(t, PreConsumeBilling(c, estimatedQuota, info))
			require.NoError(t, info.Billing.Reserve(estimatedQuota+999))
			require.NoError(t, SettleBilling(c, info, estimatedQuota+999))
			require.False(t, info.Billing.NeedsRefund())
			require.Zero(t, info.Billing.GetPreConsumedQuota())
			info.Billing.Refund(c)
		})
	}
}

func TestProtectedSettlementNeverFallsBackToLegacyQuotaMutation(t *testing.T) {
	c, info := protectedBillingTestContext(t)
	AuthorizePlatformOwnedBilling(c)
	// A missing sentinel must fail before the legacy PostConsumeQuota path.
	require.ErrorIs(t, SettleBilling(c, info, 100), ErrProtectedRelayNativeBillingDisabled)
}

func TestPlatformOwnedBillingAuthorizationIsIndependentOfDeploymentMode(t *testing.T) {
	for _, protected := range []bool{false, true} {
		for _, evidence := range []string{"exact", "forged_http_and_gin", "missing_gin_pin", "missing_info_pin", "missing_task_info"} {
			t.Run(strconv.FormatBool(protected)+"/"+evidence, func(t *testing.T) {
				c, info := protectedBillingTestContext(t)
				if !protected {
					t.Setenv("APP_ENV", "development")
					t.Setenv("DEPLOYMENT_ENV", "development")
					t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
				}
				c.Request.Header.Set(constant.HeaderPlatformGenerationInternalAdmission, "forged-http-secret")
				c.Request.Header.Set(constant.HeaderPlatformGenerationJobID, "00000000-0000-0000-0000-000000000001")
				if evidence != "forged_http_and_gin" {
					AuthorizePlatformOwnedBilling(c)
				}
				switch evidence {
				case "missing_gin_pin":
					c.Set(string(constant.ContextKeyPlatformGenerationPinnedRoute), false)
				case "missing_info_pin":
					info.PinnedProviderRoute = false
				case "missing_task_info":
					info.TaskRelayInfo = nil
				}
				err := PrepareProtectedRelayBilling(c, info)
				if evidence == "exact" {
					require.Nil(t, err)
					assert.True(t, IsPlatformOwnedBilling(info))
					assert.Equal(t, BillingSourcePlatformExternal, info.BillingSource)
				} else {
					if protected {
						require.NotNil(t, err)
						assert.ErrorIs(t, err, ErrProtectedRelayNativeBillingDisabled)
					} else {
						assert.Nil(t, err)
					}
					assert.False(t, IsPlatformOwnedBilling(info))
					assert.Nil(t, info.Billing)
				}
			})
		}
	}
}

type platformBillingNoHTTP struct{ calls atomic.Int64 }

func (transport *platformBillingNoHTTP) RoundTrip(*http.Request) (*http.Response, error) {
	transport.calls.Add(1)
	return nil, errors.New("unexpected outbound billing request")
}

func TestPlatformOwnedBillingHasNoNativeDatabaseOrHTTPWrites(t *testing.T) {
	for _, protected := range []bool{false, true} {
		for _, estimate := range []int{0, 1234} {
			t.Run(strconv.FormatBool(protected)+"/"+strconv.Itoa(estimate), func(t *testing.T) {
				truncate(t)
				seedUser(t, 9971, 7000)
				seedToken(t, 9972, 9971, "platform-owned-no-native-writes", 8000)
				seedSubscription(t, 9973, 9971, 9000, 100)
				c, info := protectedBillingTestContext(t)
				if !protected {
					t.Setenv("APP_ENV", "development")
					t.Setenv("DEPLOYMENT_ENV", "development")
					t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
				}
				info.UserId, info.TokenId, info.TokenKey, info.UserQuota = 9971, 9972, "platform-owned-no-native-writes", 7000
				info.SubscriptionId = 9973
				info.FinalPreConsumedQuota = 123
				info.ForcePreConsume = true
				AuthorizePlatformOwnedBilling(c)
				var databaseWrites atomic.Int64
				observeWrite := func(*gorm.DB) { databaseWrites.Add(1) }
				const callback = "test:platform-owned-billing-write-observer"
				require.NoError(t, model.DB.Callback().Create().Before("gorm:create").Register(callback, observeWrite))
				require.NoError(t, model.DB.Callback().Update().Before("gorm:update").Register(callback, observeWrite))
				require.NoError(t, model.DB.Callback().Delete().Before("gorm:delete").Register(callback, observeWrite))
				t.Cleanup(func() {
					_ = model.DB.Callback().Create().Remove(callback)
					_ = model.DB.Callback().Update().Remove(callback)
					_ = model.DB.Callback().Delete().Remove(callback)
				})
				noHTTP := &platformBillingNoHTTP{}
				previousHTTP := httpClient
				httpClient = &http.Client{Transport: noHTTP}
				t.Cleanup(func() { httpClient = previousHTTP })
				require.Nil(t, PrepareProtectedRelayBilling(c, info))
				require.Nil(t, PrepareProtectedRelayBilling(c, info), "reentrant preparation cannot switch to legacy billing")
				require.Nil(t, PreConsumeBilling(c, estimate, info))
				require.NoError(t, info.Billing.Reserve(estimate+999))
				require.NoError(t, SettleBilling(c, info, estimate+999))
				require.NoError(t, SettleBilling(c, info, 0))
				info.Billing.Refund(c)
				assert.False(t, info.Billing.NeedsRefund())
				assert.Zero(t, info.Billing.GetPreConsumedQuota())
				assert.Zero(t, info.FinalPreConsumedQuota)
				assert.Zero(t, info.SubscriptionId)
				assert.Zero(t, info.SubscriptionPreConsumed)
				assert.Zero(t, info.SubscriptionPostDelta)
				assert.Zero(t, databaseWrites.Load())
				assert.Zero(t, noHTTP.calls.Load())
				var user model.User
				require.NoError(t, model.DB.First(&user, 9971).Error)
				assert.Equal(t, 7000, user.Quota)
				assert.Zero(t, user.UsedQuota)
				var token model.Token
				require.NoError(t, model.DB.First(&token, 9972).Error)
				assert.Equal(t, 8000, token.RemainQuota)
				assert.Zero(t, token.UsedQuota)
				var subscription model.UserSubscription
				require.NoError(t, model.DB.First(&subscription, 9973).Error)
				assert.EqualValues(t, 100, subscription.AmountUsed)
				var logs int64
				require.NoError(t, model.DB.Model(&model.Log{}).Count(&logs).Error)
				assert.Zero(t, logs)
			})
		}
	}
}

func TestPlatformOwnedSentinelCannotFallBackWhenAuthorizationDisappears(t *testing.T) {
	for _, protected := range []bool{false, true} {
		for _, lost := range []string{"marker", "gin_pin", "info_pin"} {
			t.Run(strconv.FormatBool(protected)+"/"+lost, func(t *testing.T) {
				c, info := protectedBillingTestContext(t)
				if !protected {
					t.Setenv("APP_ENV", "development")
					t.Setenv("DEPLOYMENT_ENV", "development")
					t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
				}
				AuthorizePlatformOwnedBilling(c)
				require.Nil(t, PrepareProtectedRelayBilling(c, info))
				sentinel := info.Billing
				switch lost {
				case "marker":
					c.Request = c.Request.WithContext(context.Background())
				case "gin_pin":
					c.Set(string(constant.ContextKeyPlatformGenerationPinnedRoute), false)
				case "info_pin":
					info.PinnedProviderRoute = false
				}
				err := PrepareProtectedRelayBilling(c, info)
				require.NotNil(t, err)
				assert.ErrorIs(t, err, ErrProtectedRelayNativeBillingDisabled)
				err = PreConsumeBilling(c, 100, info)
				require.NotNil(t, err)
				assert.ErrorIs(t, err, ErrProtectedRelayNativeBillingDisabled)
				assert.ErrorIs(t, SettleBilling(c, info, 100), ErrProtectedRelayNativeBillingDisabled)
				assert.Same(t, sentinel, info.Billing)
			})
		}
	}
}

func TestPlatformOwnedBillingPreservesQuantityValidationAndRejectsExistingWallet(t *testing.T) {
	c, info := protectedBillingTestContext(t)
	t.Setenv("APP_ENV", "development")
	t.Setenv("DEPLOYMENT_ENV", "development")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
	AuthorizePlatformOwnedBilling(c)
	require.Nil(t, PrepareProtectedRelayBilling(c, info))
	err := PreConsumeBilling(c, -1, info)
	require.NotNil(t, err)
	assert.Contains(t, err.Error(), "negative")
	assert.True(t, IsPlatformOwnedBilling(info))
	info.Billing = &BillingSession{}
	err = PrepareProtectedRelayBilling(c, info)
	require.NotNil(t, err)
	assert.ErrorIs(t, err, ErrProtectedRelayNativeBillingDisabled)
	assert.ErrorIs(t, SettleBilling(c, info, 0), ErrProtectedRelayNativeBillingDisabled)
}

func TestOrdinaryDevelopmentNativeBillingKeepsRealWalletAndTokenCharges(t *testing.T) {
	truncate(t)
	seedUser(t, 9971, 7000)
	seedToken(t, 9972, 9971, "ordinary-development-native", 8000)
	c, info := protectedBillingTestContext(t)
	t.Setenv("APP_ENV", "development")
	t.Setenv("DEPLOYMENT_ENV", "development")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
	c.Request.Header.Set(constant.HeaderPlatformGenerationInternalAdmission, "forged-http-admission")
	info.UserId, info.UserQuota, info.TokenId, info.TokenKey = 9971, 7000, 9972, "ordinary-development-native"
	info.UserSetting.BillingPreference = "wallet_only"
	info.ForcePreConsume = true
	require.Nil(t, PrepareProtectedRelayBilling(c, info))
	assert.False(t, IsPlatformOwnedBilling(info))
	require.Nil(t, PreConsumeBilling(c, 321, info))
	assert.False(t, IsPlatformOwnedBilling(info))
	require.NoError(t, info.Billing.Settle(321))
	var user model.User
	require.NoError(t, model.DB.First(&user, 9971).Error)
	assert.Equal(t, 7000-321, user.Quota)
	var token model.Token
	require.NoError(t, model.DB.First(&token, 9972).Error)
	assert.Equal(t, 8000-321, token.RemainQuota)
	assert.Equal(t, 321, token.UsedQuota)
}
