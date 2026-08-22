package router

import (
	"github.com/QuantumNous/new-api/controller"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
)

func SetPlatformGenerationRouter(router *gin.Engine) {
	healthRouter := router.Group("/health")
	healthRouter.Use(middleware.PlatformRelayRequestID())
	{
		healthRouter.GET("/live", controller.PlatformRelayLive)
		healthRouter.GET("/ready", controller.PlatformRelayReady)
	}

	generationRouter := router.Group("/v1/generations")
	generationRouter.Use(middleware.RouteTag("platform-relay"))
	generationRouter.Use(middleware.PlatformGenerationServiceAuth())
	{
		generationRouter.POST("", controller.SubmitPlatformGeneration)
		generationRouter.GET("/:job_id", controller.GetPlatformGeneration)
		generationRouter.GET("/:job_id/artifacts/:asset_id/download", controller.GetPlatformGenerationArtifactDownload)
	}

	artifactRouter := router.Group(service.PlatformArtifactDownloadPath)
	artifactRouter.Use(middleware.RouteTag("platform-relay-artifact"))
	artifactRouter.Use(middleware.PlatformRelayRequestID())
	{
		artifactRouter.GET("", controller.DownloadPlatformGenerationFilesystemArtifact)
	}

	operationsRouter := router.Group("/internal/platform-generation-operations")
	operationsRouter.Use(middleware.RouteTag("platform-relay-operations"))
	operationsRouter.Use(middleware.PlatformRelayRequestID())
	{
		operationsRouter.GET("/channels", controller.ListPlatformChannelControlChannels)
		operationsRouter.GET("/channels/:channel_id", controller.GetPlatformChannelControlChannel)
		operationsRouter.POST("/channels/:channel_id/test", controller.TestPlatformChannelControlChannel)
		operationsRouter.POST("/channels/:channel_id/status", controller.UpdatePlatformChannelControlStatus)
		operationsRouter.GET("/channels/:channel_id/operations/:operation_id", controller.GetPlatformChannelControlOperation)
		operationsRouter.GET("/submission-unknown", controller.ListPlatformGenerationSubmissionUnknown)
		operationsRouter.GET("/:job_id/reconciliation", controller.GetPlatformGenerationSubmissionUnknown)
		operationsRouter.GET("/:job_id/reconciliation-result", controller.GetPlatformGenerationSubmissionUnknownResult)
		operationsRouter.POST("/:job_id/reconciliation", controller.ResolvePlatformGenerationSubmissionUnknown)
		operationsRouter.GET("/callback-deliveries", controller.ListPlatformGenerationCallbackDeliveries)
		operationsRouter.GET("/callback-deliveries/:event_id", controller.GetPlatformGenerationCallbackDelivery)
		operationsRouter.GET("/callback-deliveries/:event_id/redrive-result", controller.GetPlatformGenerationCallbackRedriveResult)
		operationsRouter.POST("/callback-deliveries/:event_id/redrive", controller.RedrivePlatformGenerationCallbackDelivery)
	}

	runtimeIdentityRouter := router.Group("/internal/platform-relay")
	runtimeIdentityRouter.Use(middleware.RouteTag("platform-relay-runtime-identity"))
	runtimeIdentityRouter.Use(middleware.PlatformRelayRequestID())
	{
		runtimeIdentityRouter.GET("/runtime-build-identity", controller.PlatformRelayRuntimeBuildIdentity)
	}

	nativeRouter := router.Group("/internal/platform-generations")
	nativeRouter.Use(middleware.RouteTag("platform-relay-internal"))
	// Authenticate the exact service token before native admission mutates the
	// durable route state. Admission then rebinds the TokenAuth context to the
	// job's configured Platform principal and rejects suffix/channel variants.
	nativeRouter.Use(middleware.TokenAuth(), middleware.PlatformGenerationNativeAdmission())
	{
		nativeRouter.POST("/native-submit", controller.RelayTask)
	}
}
