package service

import (
	"context"
	"errors"
	"fmt"
	"net/http"

	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/logger"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/types"
	"github.com/gin-gonic/gin"
)

const (
	BillingSourceWallet           = "wallet"
	BillingSourceSubscription     = "subscription"
	BillingSourcePlatformExternal = model.TaskBillingSourcePlatformExternal
)

var ErrProtectedRelayNativeBillingDisabled = errors.New("native new-api billing is disabled for the protected Platform Relay")

// platformOwnedBillingAdmissionKey is deliberately private and lives in the
// request context rather than in an HTTP header or a Gin string key. Only the
// server-side native-admission middleware can install it after the durable
// route/submission fence has been validated.
type platformOwnedBillingAdmissionKey struct{}

type platformOwnedBillingSettler struct{}

func (*platformOwnedBillingSettler) Settle(int) error         { return nil }
func (*platformOwnedBillingSettler) Refund(*gin.Context)      {}
func (*platformOwnedBillingSettler) NeedsRefund() bool        { return false }
func (*platformOwnedBillingSettler) GetPreConsumedQuota() int { return 0 }
func (*platformOwnedBillingSettler) Reserve(int) error        { return nil }

// AuthorizePlatformOwnedBilling is called only after
// PlatformGenerationNativeAdmission has validated the internal secret, job,
// immutable route, worker lease, submission token, channel credential and the
// durable BeginPlatformGenerationRouteSubmission fence. HTTP callers cannot
// synthesize this in-process request-context value with headers.
func AuthorizePlatformOwnedBilling(c *gin.Context) {
	if c == nil || c.Request == nil {
		return
	}
	requestContext := context.WithValue(c.Request.Context(), platformOwnedBillingAdmissionKey{}, true)
	c.Request = c.Request.WithContext(requestContext)
}

func platformOwnedBillingAuthorized(c *gin.Context, relayInfo *relaycommon.RelayInfo) bool {
	if c == nil || c.Request == nil || relayInfo == nil || relayInfo.TaskRelayInfo == nil {
		return false
	}
	if admitted, _ := c.Request.Context().Value(platformOwnedBillingAdmissionKey{}).(bool); !admitted {
		return false
	}
	return relayInfo.TaskRelayInfo.PinnedProviderRoute &&
		c.GetBool(string(constant.ContextKeyPlatformGenerationPinnedRoute))
}

// PrepareProtectedRelayBilling installs the explicit Platform-owned no-op
// settler before price/free-model branching. Consequently a fenced free model
// cannot fall through to legacy settlement, while every ordinary protected
// task is rejected before an adaptor or provider request is reached.
func PrepareProtectedRelayBilling(c *gin.Context, relayInfo *relaycommon.RelayInfo) *types.NewAPIError {
	if !model.RelayDatabaseRoleAttestationRequired() {
		return nil
	}
	if !platformOwnedBillingAuthorized(c, relayInfo) {
		return protectedNativeBillingError()
	}
	if relayInfo.Billing != nil && !IsPlatformOwnedBilling(relayInfo) {
		return protectedNativeBillingError()
	}
	relayInfo.Billing = &platformOwnedBillingSettler{}
	relayInfo.FinalPreConsumedQuota = 0
	relayInfo.BillingSource = BillingSourcePlatformExternal
	relayInfo.SubscriptionId = 0
	relayInfo.SubscriptionPreConsumed = 0
	relayInfo.SubscriptionPostDelta = 0
	return nil
}

// IsPlatformOwnedBilling reports the explicit zero-side-effect billing mode
// installed for one fenced Platform-owned native submission. It is used when
// persisting the native task so no legacy quota/refund metadata is attached.
func IsPlatformOwnedBilling(relayInfo *relaycommon.RelayInfo) bool {
	if relayInfo == nil || relayInfo.Billing == nil {
		return false
	}
	_, ok := relayInfo.Billing.(*platformOwnedBillingSettler)
	return ok
}

func protectedNativeBillingError() *types.NewAPIError {
	return types.NewErrorWithStatusCode(
		ErrProtectedRelayNativeBillingDisabled,
		types.ErrorCodePreConsumeTokenQuotaFailed,
		http.StatusForbidden,
		types.ErrOptionWithSkipRetry(),
		types.ErrOptionWithNoRecordErrorLog(),
	)
}

// PreConsumeBilling 根据用户计费偏好创建 BillingSession 并执行预扣费。
// 会话存储在 relayInfo.Billing 上，供后续 Settle / Refund 使用。
func PreConsumeBilling(c *gin.Context, preConsumedQuota int, relayInfo *relaycommon.RelayInfo) *types.NewAPIError {
	if relayInfo != nil && relayInfo.QuotaClamp != nil {
		return types.NewErrorWithStatusCode(
			relayInfo.QuotaClamp,
			types.ErrorCodeModelPriceError,
			http.StatusBadRequest,
			types.ErrOptionWithSkipRetry(),
		)
	}
	if preConsumedQuota < 0 {
		return types.NewErrorWithStatusCode(
			fmt.Errorf("pre-consume quota cannot be negative: %d", preConsumedQuota),
			types.ErrorCodeModelPriceError,
			http.StatusBadRequest,
			types.ErrOptionWithSkipRetry(),
		)
	}
	if model.RelayDatabaseRoleAttestationRequired() {
		return PrepareProtectedRelayBilling(c, relayInfo)
	}
	session, apiErr := NewBillingSession(c, relayInfo, preConsumedQuota)
	if apiErr != nil {
		return apiErr
	}
	relayInfo.Billing = session
	return nil
}

// ---------------------------------------------------------------------------
// SettleBilling — 后结算辅助函数
// ---------------------------------------------------------------------------

// SettleBilling 执行计费结算。如果 RelayInfo 上有 BillingSession 则通过 session 结算，
// 否则回退到旧的 PostConsumeQuota 路径（兼容按次计费等场景）。
func SettleBilling(ctx *gin.Context, relayInfo *relaycommon.RelayInfo, actualQuota int) error {
	if relayInfo == nil {
		return errors.New("billing relay information is required")
	}
	if model.RelayDatabaseRoleAttestationRequired() {
		if !platformOwnedBillingAuthorized(ctx, relayInfo) || !IsPlatformOwnedBilling(relayInfo) {
			return ErrProtectedRelayNativeBillingDisabled
		}
		// Platform is the sole customer ledger for this fenced internal call.
		// Do not emit native quota notifications, consumption deltas, reserves or
		// refunds even when the provider reports a non-zero usage estimate.
		return nil
	}
	if relayInfo.Billing != nil {
		preConsumed := relayInfo.Billing.GetPreConsumedQuota()
		delta := actualQuota - preConsumed

		if delta > 0 {
			logger.LogInfo(ctx, fmt.Sprintf("预扣费后补扣费：%s（实际消耗：%s，预扣费：%s）",
				logger.FormatQuota(delta),
				logger.FormatQuota(actualQuota),
				logger.FormatQuota(preConsumed),
			))
		} else if delta < 0 {
			logger.LogInfo(ctx, fmt.Sprintf("预扣费后返还扣费：%s（实际消耗：%s，预扣费：%s）",
				logger.FormatQuota(-delta),
				logger.FormatQuota(actualQuota),
				logger.FormatQuota(preConsumed),
			))
		} else {
			logger.LogInfo(ctx, fmt.Sprintf("预扣费与实际消耗一致，无需调整：%s（按次计费）",
				logger.FormatQuota(actualQuota),
			))
		}

		if err := relayInfo.Billing.Settle(actualQuota); err != nil {
			return err
		}

		// 发送额度通知（订阅计费使用订阅剩余额度）
		if actualQuota != 0 {
			if relayInfo.BillingSource == BillingSourceSubscription {
				checkAndSendSubscriptionQuotaNotify(relayInfo)
			} else {
				checkAndSendQuotaNotify(relayInfo, actualQuota-preConsumed, preConsumed)
			}
		}
		return nil
	}

	// 回退：无 BillingSession 时使用旧路径
	quotaDelta := actualQuota - relayInfo.FinalPreConsumedQuota
	if quotaDelta != 0 {
		return PostConsumeQuota(relayInfo, quotaDelta, relayInfo.FinalPreConsumedQuota, true)
	}
	return nil
}
