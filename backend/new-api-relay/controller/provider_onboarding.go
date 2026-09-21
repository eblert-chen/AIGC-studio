package controller

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"net/http"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

const (
	providerOnboardingCredentialBodyLimit = 70 * 1024
	providerOnboardingStatusBodyLimit     = 4 * 1024
)

type providerOnboardingCredentialRequest struct {
	APIKey           string `json:"api_key"`
	Reason           string `json:"reason"`
	ExpectedRevision string `json:"expected_revision,omitempty"`
}

type providerOnboardingStatusRequest struct {
	Reason           string `json:"reason"`
	ExpectedRevision string `json:"expected_revision"`
}

func decodeProviderOnboardingBody(c *gin.Context, limit int64, target any) bool {
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, limit)
	raw, err := io.ReadAll(c.Request.Body)
	if err != nil {
		writeProviderOnboardingRequestError(c)
		return false
	}
	defer clear(raw)
	if len(bytes.TrimSpace(raw)) == 0 || common.RejectDuplicateJSONKeys(raw) != nil ||
		common.DecodeJsonDisallowUnknownFields(bytes.NewReader(raw), target) != nil {
		writeProviderOnboardingRequestError(c)
		return false
	}
	return true
}

func writeProviderOnboardingRequestError(c *gin.Context) {
	c.JSON(http.StatusUnprocessableEntity, gin.H{
		"success": false,
		"code":    "PROVIDER_ONBOARDING_REQUEST_INVALID",
		"message": "Provider onboarding request is invalid",
	})
}

func writeProviderOnboardingError(c *gin.Context, err error) {
	status := http.StatusInternalServerError
	code := "PROVIDER_ONBOARDING_INTERNAL_ERROR"
	message := "Provider onboarding operation failed"
	switch {
	case errors.Is(err, service.ErrProviderOnboardingUnknownProvider):
		status, code, message = http.StatusNotFound, "PROVIDER_ONBOARDING_PROVIDER_NOT_FOUND", "Provider is not managed by this onboarding API"
	case errors.Is(err, service.ErrProviderOnboardingCredentialInvalid),
		errors.Is(err, service.ErrProviderOnboardingReasonInvalid):
		status, code, message = http.StatusUnprocessableEntity, "PROVIDER_ONBOARDING_REQUEST_INVALID", "Provider onboarding request is invalid"
	case errors.Is(err, service.ErrProviderOnboardingEnvironmentNotReady):
		status, code, message = http.StatusServiceUnavailable, "PROVIDER_ONBOARDING_ENVIRONMENT_NOT_READY", "Provider onboarding is not available in this Relay environment"
	case errors.Is(err, model.ErrProviderOnboardingExpectedRevisionRequired):
		status, code, message = http.StatusPreconditionRequired, "PROVIDER_ONBOARDING_REVISION_REQUIRED", "Current control revision is required"
	case errors.Is(err, model.ErrPlatformChannelControlRevisionConflict):
		status, code, message = http.StatusConflict, "PROVIDER_ONBOARDING_REVISION_CONFLICT", "Channel control revision does not match"
	case errors.Is(err, model.ErrPlatformGenerationChannelInUse):
		status, code, message = http.StatusConflict, "PROVIDER_ONBOARDING_ACTIVE_TASKS", "Credential rotation is blocked by active tasks"
	case errors.Is(err, model.ErrProviderOnboardingRouteNotReady):
		status, code, message = http.StatusConflict, "PROVIDER_ONBOARDING_ROUTE_NOT_READY", "Provider route acceptance is not ready"
	case errors.Is(err, model.ErrProviderOnboardingChannelConflict):
		status, code, message = http.StatusConflict, "PROVIDER_ONBOARDING_CHANNEL_CONFLICT", "Reserved provider channel has conflicting state"
	case errors.Is(err, model.ErrProviderOnboardingManagedChannel):
		status, code, message = http.StatusConflict, "PROVIDER_ONBOARDING_MANAGED_CHANNEL", "Managed provider channel must use the provider onboarding API"
	case errors.Is(err, gorm.ErrRecordNotFound):
		status, code, message = http.StatusNotFound, "PROVIDER_ONBOARDING_NOT_CONFIGURED", "Provider credential is not configured"
	}
	c.JSON(status, gin.H{"success": false, "code": code, "message": message})
}

// rejectNativeManagedProviderChannel closes legacy Channel API paths without
// hydrating the credential. Reserved IDs are rejected even if their rows have
// been corrupted or pre-created without the managed tag.
func rejectNativeManagedProviderChannel(c *gin.Context, channelID int) bool {
	if model.IsProviderOnboardingReservedChannelID(channelID) {
		writeProviderOnboardingError(c, model.ErrProviderOnboardingManagedChannel)
		return true
	}
	channel, err := model.GetChannelById(channelID, false)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return false
	}
	if err != nil {
		writeProviderOnboardingError(c, err)
		return true
	}
	if model.IsProviderOnboardingManagedChannel(channel) {
		writeProviderOnboardingError(c, model.ErrProviderOnboardingManagedChannel)
		return true
	}
	return false
}

func rejectNativeManagedProviderChannels(c *gin.Context, channelIDs []int) bool {
	for _, channelID := range channelIDs {
		if rejectNativeManagedProviderChannel(c, channelID) {
			return true
		}
	}
	return false
}

func rejectNativeManagedProviderTag(c *gin.Context, tag string) bool {
	if tag == model.ProviderOnboardingManagedChannelTag {
		writeProviderOnboardingError(c, model.ErrProviderOnboardingManagedChannel)
		return true
	}
	channels, err := model.GetChannelsByTag(tag, false, false)
	if err != nil {
		writeProviderOnboardingError(c, err)
		return true
	}
	for _, channel := range channels {
		if model.IsProviderOnboardingManagedChannel(channel) {
			writeProviderOnboardingError(c, model.ErrProviderOnboardingManagedChannel)
			return true
		}
	}
	return false
}

func providerOnboardingReasonDigest(reason string) string {
	digest := sha256.Sum256([]byte(reason))
	return "sha256:" + hex.EncodeToString(digest[:])
}

func recordProviderOnboardingAudit(
	c *gin.Context,
	action string,
	provider service.ProviderOnboardingProviderView,
	changed bool,
	reason string,
) {
	recordManageAudit(c, action, map[string]interface{}{
		"provider":           provider.ID,
		"channel_id":         provider.ChannelID,
		"account_id":         provider.AccountID,
		"changed":            changed,
		"lifecycle_state":    provider.LifecycleState,
		"fingerprint_prefix": provider.Credential.FingerprintPrefix,
		"control_revision":   provider.ControlRevision,
		"reason_sha256":      providerOnboardingReasonDigest(reason),
	})
}

func ListProviderOnboarding(c *gin.Context) {
	result, err := service.ListProviderOnboardingProviders()
	if err != nil {
		writeProviderOnboardingError(c, err)
		return
	}
	c.Header("Cache-Control", "no-store, max-age=0")
	c.Header("Pragma", "no-cache")
	c.JSON(http.StatusOK, gin.H{"success": true, "data": result})
}

func PutProviderOnboardingCredential(c *gin.Context) {
	var request providerOnboardingCredentialRequest
	if !decodeProviderOnboardingBody(c, providerOnboardingCredentialBodyLimit, &request) {
		return
	}
	if request.ExpectedRevision != "" && !platformChannelControlRevisionPattern.MatchString(request.ExpectedRevision) {
		writeProviderOnboardingRequestError(c)
		return
	}
	provider, changed, err := service.PutProviderOnboardingCredential(
		c.Param("provider"), request.APIKey, request.Reason, request.ExpectedRevision,
	)
	request.APIKey = ""
	if err != nil {
		writeProviderOnboardingError(c, err)
		return
	}
	if changed {
		model.InitChannelCache()
	}
	recordProviderOnboardingAudit(c, "provider_onboarding.credential_write", provider, changed, request.Reason)
	c.JSON(http.StatusOK, gin.H{"success": true, "data": provider})
}

func DisableProviderOnboarding(c *gin.Context) {
	setProviderOnboardingEnabled(c, false)
}

func ResumeProviderOnboarding(c *gin.Context) {
	setProviderOnboardingEnabled(c, true)
}

func setProviderOnboardingEnabled(c *gin.Context, enabled bool) {
	var request providerOnboardingStatusRequest
	if !decodeProviderOnboardingBody(c, providerOnboardingStatusBodyLimit, &request) {
		return
	}
	if !platformChannelControlRevisionPattern.MatchString(request.ExpectedRevision) {
		writeProviderOnboardingRequestError(c)
		return
	}
	provider, changed, err := service.SetProviderOnboardingEnabled(
		c.Param("provider"), request.Reason, request.ExpectedRevision, enabled,
	)
	if err != nil {
		writeProviderOnboardingError(c, err)
		return
	}
	if changed {
		model.InitChannelCache()
	}
	action := "provider_onboarding.disable"
	if enabled {
		action = "provider_onboarding.resume"
	}
	recordProviderOnboardingAudit(c, action, provider, changed, request.Reason)
	c.JSON(http.StatusOK, gin.H{"success": true, "data": provider})
}
