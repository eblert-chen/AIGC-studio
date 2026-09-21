package router

import (
	"github.com/QuantumNous/new-api/controller"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/service/authz"
	"github.com/gin-gonic/gin"
)

func registerProviderOnboardingRoutes(apiRouter *gin.RouterGroup) {
	route := apiRouter.Group("/provider-onboarding")
	route.Use(middleware.RootAuth(), middleware.DisableCache())
	route.GET("", middleware.RequirePermission(authz.ChannelRead), controller.ListProviderOnboarding)

	write := route.Group("")
	write.Use(
		middleware.RequirePermission(authz.ChannelSensitiveWrite),
		middleware.CriticalRateLimit(),
	)
	write.POST("/:provider/credential", middleware.ProviderCredentialWriteVerificationRequired(), controller.PutProviderOnboardingCredential)
	write.POST("/:provider/disable", middleware.ProviderDisableVerificationRequired(), controller.DisableProviderOnboarding)
	write.POST("/:provider/resume", middleware.ProviderResumeVerificationRequired(), controller.ResumeProviderOnboarding)
}
