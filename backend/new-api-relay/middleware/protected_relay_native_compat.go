package middleware

import (
	"net/http"

	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
)

// RejectProtectedRelayNativeCompatibility closes every ordinary new-api
// provider compatibility surface in the protected Platform Relay. Those
// routes do not yet have the Platform generation job's durable submission,
// unknown-outcome and customer-ledger state machines. The fenced internal
// native-submit bridge is registered separately and never traverses this
// middleware.
func RejectProtectedRelayNativeCompatibility() gin.HandlerFunc {
	return func(c *gin.Context) {
		if !model.RelayDatabaseRoleAttestationRequired() {
			c.Next()
			return
		}
		c.AbortWithStatusJSON(http.StatusForbidden, gin.H{
			"error": "native new-api provider compatibility routes are disabled on the protected Platform Relay",
		})
	}
}
