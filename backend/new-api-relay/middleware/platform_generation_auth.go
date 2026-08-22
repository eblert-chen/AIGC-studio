package middleware

import (
	"context"
	"errors"
	"net/http"
	"regexp"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
)

const platformRelayPrincipalContextKey = "platform_relay_principal"

var platformRelayRequestIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$`)

func PlatformRelayRequestID() gin.HandlerFunc {
	return func(c *gin.Context) {
		setPlatformRelayRequestID(c)
		c.Next()
	}
}

// PlatformGenerationServiceAuth authenticates the service-to-service generation
// contract without accepting a tenant identifier from the request body.
func PlatformGenerationServiceAuth() gin.HandlerFunc {
	return func(c *gin.Context) {
		setPlatformRelayRequestID(c)
		if !service.PlatformRelayCompatEnabled() {
			writePlatformRelayAuthError(
				c,
				http.StatusServiceUnavailable,
				"RELAY_COMPATIBILITY_DISABLED",
				"Relay generation compatibility is not enabled",
				true,
			)
			return
		}

		clientID := c.GetHeader("X-Client-ID")
		apiKey := c.GetHeader("X-API-Key")
		if clientID == "" || apiKey == "" {
			writePlatformRelayAuthError(
				c,
				http.StatusUnauthorized,
				"CLIENT_AUTHENTICATION_REQUIRED",
				"X-Client-ID and X-API-Key headers are required",
				false,
			)
			return
		}

		// Validate configuration separately so a deployment error is not
		// misreported as bad client credentials.
		if _, err := service.GetPlatformRelayModelCatalog(); err != nil {
			common.SysError("Relay compatibility configuration is invalid: " + err.Error())
			writePlatformRelayAuthError(
				c,
				http.StatusInternalServerError,
				"INTERNAL_ERROR",
				"The relay could not complete the request",
				true,
			)
			return
		}
		principal, err := service.AuthenticatePlatformRelayClient(clientID, apiKey)
		if err != nil {
			if errors.Is(err, service.ErrProtectedPlatformRelayPrincipal) {
				writePlatformRelayAuthError(
					c,
					http.StatusServiceUnavailable,
					"SERVICE_PRINCIPAL_UNAVAILABLE",
					"The Relay service principal is unavailable",
					true,
				)
				return
			}
			writePlatformRelayAuthError(
				c,
				http.StatusUnauthorized,
				"INVALID_CLIENT_CREDENTIALS",
				"Client credentials are invalid",
				false,
			)
			return
		}

		c.Set(platformRelayPrincipalContextKey, *principal)
		c.Next()
	}
}

// PlatformRelayOrTokenAuth preserves new-api's existing Bearer/x-api-key model
// endpoint behavior unless the caller explicitly selects the Platform contract
// with X-Client-ID.
func PlatformRelayOrTokenAuth() gin.HandlerFunc {
	platformAuth := PlatformGenerationServiceAuth()
	tokenAuth := TokenAuth()
	return func(c *gin.Context) {
		if model.RelayDatabaseRoleAttestationRequired() {
			platformAuth(c)
			return
		}
		if c.GetHeader("X-Client-ID") != "" {
			platformAuth(c)
			return
		}
		tokenAuth(c)
	}
}

func GetPlatformRelayPrincipal(c *gin.Context) (service.PlatformRelayPrincipal, bool) {
	value, ok := c.Get(platformRelayPrincipalContextKey)
	if !ok {
		return service.PlatformRelayPrincipal{}, false
	}
	principal, ok := value.(service.PlatformRelayPrincipal)
	return principal, ok
}

func setPlatformRelayRequestID(c *gin.Context) {
	requestID := c.GetHeader("X-Request-ID")
	if !platformRelayRequestIDPattern.MatchString(requestID) {
		requestID = c.GetString(common.RequestIdKey)
		if !platformRelayRequestIDPattern.MatchString(requestID) {
			requestID = common.NewRequestId()
		}
	}
	c.Set(common.RequestIdKey, requestID)
	requestContext := context.WithValue(c.Request.Context(), common.RequestIdKey, requestID)
	c.Request = c.Request.WithContext(requestContext)
	c.Header("X-Request-ID", requestID)
	c.Header(common.RequestIdKey, requestID)
}

func writePlatformRelayAuthError(
	c *gin.Context,
	status int,
	code string,
	message string,
	retryable bool,
) {
	details := make(map[string]any)
	c.AbortWithStatusJSON(status, dto.PlatformGenerationErrorEnvelope{
		APIVersion:    dto.PlatformRelayAPIVersion,
		SchemaVersion: dto.PlatformRelaySchemaVersion,
		Error: dto.PlatformGenerationErrorEnvelopeDetail{
			Code:      code,
			Message:   message,
			Retryable: retryable,
			RequestID: c.GetString(common.RequestIdKey),
			Details:   details,
		},
	})
}
