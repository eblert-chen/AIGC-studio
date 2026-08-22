package service

import (
	"net/http/httptest"
	"strconv"
	"testing"

	"github.com/QuantumNous/new-api/constant"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
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
