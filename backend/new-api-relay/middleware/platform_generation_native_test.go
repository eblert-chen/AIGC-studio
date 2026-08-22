package middleware

import (
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"

	"github.com/QuantumNous/new-api/constant"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
)

func TestPlatformGenerationNativeAdmissionRejectsLegacyRequestWithoutWorkerFence(t *testing.T) {
	t.Setenv("RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN", "internal-admission-test-token")
	gin.SetMode(gin.TestMode)
	reachedProvider := false
	router := gin.New()
	router.POST("/", PlatformGenerationNativeAdmission(), func(c *gin.Context) {
		reachedProvider = true
		c.Status(http.StatusNoContent)
	})

	request := httptest.NewRequest(http.MethodPost, "/", nil)
	request.Header.Set(constant.HeaderPlatformGenerationInternalAdmission, "internal-admission-test-token")
	request.Header.Set(constant.HeaderPlatformGenerationJobID, uuid.NewString())
	request.Header.Set(constant.HeaderPlatformGenerationRouteID, strconv.FormatInt(42, 10))
	request.Header.Set(constant.HeaderPlatformGenerationSubmissionToken, uuid.NewString())
	response := httptest.NewRecorder()
	router.ServeHTTP(response, request)

	assert.Equal(t, http.StatusBadRequest, response.Code)
	assert.False(t, reachedProvider)
	assert.Equal(t, "false", response.Header().Get(constant.HeaderPlatformGenerationProviderStarted))
}
