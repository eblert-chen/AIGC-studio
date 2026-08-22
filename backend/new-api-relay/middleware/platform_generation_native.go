package middleware

import (
	"crypto/sha256"
	"crypto/subtle"
	"fmt"
	"net/http"
	"strconv"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	relayconstant "github.com/QuantumNous/new-api/relay/constant"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

// PlatformGenerationNativeAdmission protects the private bridge into the
// native new-api task relay and binds it to one already-held provider route.
func PlatformGenerationNativeAdmission() gin.HandlerFunc {
	return func(c *gin.Context) {
		// Set before any downstream response is written. Only RelayTaskSubmit may
		// flip this to true, immediately before invoking the provider adaptor.
		c.Header(constant.HeaderPlatformGenerationProviderStarted, "false")
		expected := service.PlatformRelayInternalAdmissionToken()
		provided := c.GetHeader(constant.HeaderPlatformGenerationInternalAdmission)
		providedDigest := sha256.Sum256([]byte(provided))
		expectedDigest := sha256.Sum256([]byte(expected))
		if expected == "" || subtle.ConstantTimeCompare(providedDigest[:], expectedDigest[:]) != 1 {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "invalid internal generation admission"})
			return
		}

		jobID := c.GetHeader(constant.HeaderPlatformGenerationJobID)
		parsedJobID, err := uuid.Parse(jobID)
		if err != nil || parsedJobID.String() != jobID {
			c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{"error": "invalid generation job id"})
			return
		}
		if err := service.ValidateProtectedPlatformRelayRequestPrincipalForJobID(
			jobID,
			c.GetInt("token_id"),
			c.GetString("token_key"),
			c.GetHeader("Authorization"),
		); err != nil {
			c.AbortWithStatusJSON(http.StatusServiceUnavailable, gin.H{"error": "generation service principal is unavailable"})
			return
		}
		routeID, err := strconv.ParseInt(c.GetHeader(constant.HeaderPlatformGenerationRouteID), 10, 64)
		if err != nil || routeID <= 0 {
			c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{"error": "invalid generation route id"})
			return
		}
		workerLeaseToken := c.GetHeader(constant.HeaderPlatformGenerationWorkerLeaseToken)
		routeSubmissionToken := c.GetHeader(constant.HeaderPlatformGenerationSubmissionToken)
		if !isCanonicalPlatformGenerationFenceToken(workerLeaseToken) || !isCanonicalPlatformGenerationFenceToken(routeSubmissionToken) {
			c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{"error": "invalid generation fencing tokens"})
			return
		}
		route, err := model.BeginPlatformGenerationRouteSubmission(
			jobID,
			routeID,
			workerLeaseToken,
			routeSubmissionToken,
		)
		if err != nil {
			c.AbortWithStatusJSON(http.StatusConflict, gin.H{"error": "stale generation route admission"})
			return
		}
		channel, err := model.GetChannelById(route.ChannelID, true)
		if err != nil {
			c.AbortWithStatusJSON(http.StatusConflict, gin.H{"error": "generation route channel no longer exists"})
			return
		}
		key, err := channel.GetKeyAt(route.KeyIndex)
		if err != nil {
			c.AbortWithStatusJSON(http.StatusConflict, gin.H{"error": "generation route key no longer exists"})
			return
		}
		fingerprint := fmt.Sprintf("%x", common.Sha256Raw([]byte(key)))
		if subtle.ConstantTimeCompare([]byte(fingerprint), []byte(route.KeyFingerprint)) != 1 {
			c.AbortWithStatusJSON(http.StatusConflict, gin.H{"error": "generation route key was rotated"})
			return
		}
		publicTaskID, err := model.PlatformGenerationNativeTaskID(jobID)
		if err != nil {
			c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{"error": "invalid generation task identity"})
			return
		}
		if setupErr := SetupContextForPinnedChannel(c, channel, route.Model, key, route.KeyIndex); setupErr != nil {
			c.AbortWithStatusJSON(http.StatusConflict, gin.H{"error": "generation route could not be pinned"})
			return
		}
		common.SetContextKey(c, constant.ContextKeyPlatformGenerationPublicTaskID, publicTaskID)
		common.SetContextKey(c, constant.ContextKeyPlatformGenerationUpstreamModel, route.UpstreamModel)
		common.SetContextKey(c, constant.ContextKeyPlatformGenerationPinnedRoute, true)
		common.SetContextKey(c, constant.ContextKeyPlatformGenerationWorkerLeaseToken, workerLeaseToken)
		service.AuthorizePlatformOwnedBilling(c)
		common.SetContextKey(c, constant.ContextKeyTokenSpecificChannelId, strconv.Itoa(route.ChannelID))
		c.Set("specific_channel_id", strconv.Itoa(route.ChannelID))
		c.Set("relay_mode", relayconstant.RelayModeVideoSubmit)
		c.Next()
	}
}

func isCanonicalPlatformGenerationFenceToken(value string) bool {
	parsed, err := uuid.Parse(value)
	return err == nil && parsed.String() == value
}
