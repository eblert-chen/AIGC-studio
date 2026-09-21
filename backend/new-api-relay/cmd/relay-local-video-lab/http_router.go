//go:build relay_local_video_lab

package main

import (
	"bytes"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/controller"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/localvideoconfig"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/router"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

type labCallbackRecord struct {
	EventID       string    `gorm:"primaryKey;size:64" json:"event_id"`
	PayloadSHA256 string    `gorm:"size:64;not null" json:"payload_sha256"`
	JobID         string    `gorm:"size:64;not null" json:"job_id"`
	Status        string    `gorm:"size:40;not null" json:"status"`
	ReceivedAt    time.Time `gorm:"not null" json:"received_at"`
}

func buildLabRouter(options labOptions, state *labState, config localvideoconfig.Config, summary labSummary, mock *labMockProvider, paid *labPaidAuthorization) (*gin.Engine, error) {
	gin.SetMode(gin.ReleaseMode)
	engine := gin.New()
	if err := engine.SetTrustedProxies(nil); err != nil {
		return nil, err
	}
	engine.Use(gin.Recovery(), middleware.RequestId(), middleware.BodyStorageCleanup())
	engine.Use(func(c *gin.Context) {
		c.Header("X-Local-Video-Lab", options.Mode)
		c.Header("X-Local-Video-State", config.StateID)
		if options.Mode == "live" && c.Request.Method == http.MethodPost &&
			(c.Request.URL.Path == "/v1/generations" || c.Request.URL.Path == "/internal/platform-generations/native-submit" || (strings.HasPrefix(c.Request.URL.Path, "/internal/platform-generation-operations/channels/") && strings.HasSuffix(c.Request.URL.Path, "/test"))) {
			if !labPaidInboundAllowed(c.Request, paid) {
				c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"error": "Live creates require this model's state-bound, unexpired paid batch approval"})
				return
			}
		}
		c.Next()
	})
	router.SetPlatformGenerationRouter(engine)
	models := engine.Group("/v1/models", middleware.PlatformGenerationServiceAuth())
	models.GET("", controller.ListPlatformRelayModels)
	models.GET("/:model", controller.GetPlatformRelayModel)
	engine.GET("/lab/health", func(c *gin.Context) {
		status := http.StatusOK
		if !service.PlatformGenerationWorkersAcceptingNewWork() {
			status = http.StatusServiceUnavailable
		}
		c.JSON(status, gin.H{
			"kind": "local_video_lab", "mode": options.Mode, "environment": "development", "state_id": config.StateID,
			"workers": service.GetPlatformGenerationWorkerRuntimeState(), "production_ready": false,
			"provider_is_mock": mock != nil, "paid_create_locked": options.Mode == "live" && paid == nil,
		})
	})
	engine.GET("/lab/summary", func(c *gin.Context) { c.JSON(http.StatusOK, summary) })
	engine.GET("/lab/manifest", func(c *gin.Context) {
		if !labExactSecret(c.GetHeader("X-Relay-Operations-Token"), config.Principal.OperationsToken) {
			c.AbortWithStatus(http.StatusUnauthorized)
			return
		}
		c.JSON(http.StatusOK, config)
	})
	engine.GET("/lab/evidence", func(c *gin.Context) {
		c.Header("Cache-Control", "no-store")
		if !labExactSecret(c.GetHeader("X-Relay-Operations-Token"), config.Principal.OperationsToken) {
			c.AbortWithStatus(http.StatusUnauthorized)
			return
		}
		evidence := gin.H{"mode": options.Mode, "state_id": state.Manifest.StateID, "production_acceptance": false}
		nativeBilling, err := readLabNativeBillingEvidence(c.Request.Context(), state, config)
		if err != nil {
			c.AbortWithStatusJSON(http.StatusServiceUnavailable, gin.H{"error": "local native billing evidence unavailable"})
			return
		}
		evidence["native_billing"] = nativeBilling
		if mock != nil {
			evidence["provider_posts_this_process"] = mock.posts.Load()
			evidence["provider_polls_this_process"] = mock.polls.Load()
			evidence["artifact_fetches_this_process"] = mock.downloads.Load()
			evidence["input_fetches_this_process"] = mock.inputFetches.Load()
			evidence["input_bytes_this_process"] = mock.inputBytes.Load()
		}
		for name, value := range map[string]any{
			"jobs": &model.PlatformGenerationJob{}, "native_tasks": &model.Task{},
			"callback_deliveries": &model.PlatformGenerationCallbackDelivery{}, "local_callback_receipts": &labCallbackRecord{},
			"provider_terminal_outcomes": &model.PlatformProviderTerminalOutcome{}, "provider_cost_events": &model.PlatformChannelCostEvent{},
		} {
			var count int64
			if err := model.DB.Model(value).Count(&count).Error; err != nil {
				c.AbortWithStatusJSON(http.StatusServiceUnavailable, gin.H{"error": "local evidence database unavailable"})
				return
			}
			evidence[name] = count
		}
		c.JSON(http.StatusOK, evidence)
	})
	engine.POST("/lab/callback", func(c *gin.Context) { receiveLabCallback(c, config.Principal.CallbackSigningSecret) })
	return engine, nil
}

func labPaidInboundAllowed(request *http.Request, paid *labPaidAuthorization) bool {
	if paid == nil || !paid.approval.ExpiresAt.After(time.Now().UTC()) {
		return false
	}
	if request.URL.Path == "/internal/platform-generations/native-submit" {
		if model.DB == nil {
			return false
		}
		var job model.PlatformGenerationJob
		return model.DB.Where("id = ?", request.Header.Get(constant.HeaderPlatformGenerationJobID)).First(&job).Error == nil && paid.publicModels[job.Model]
	}
	if request.Body == nil {
		return false
	}
	payload, err := io.ReadAll(io.LimitReader(request.Body, 128*1024+1))
	if err != nil || len(payload) > 128*1024 {
		return false
	}
	_ = request.Body.Close()
	request.Body = io.NopCloser(bytes.NewReader(payload))
	var operation struct {
		Model         string `json:"model"`
		PublicModelID string `json:"public_model_id"`
	}
	if common.Unmarshal(payload, &operation) != nil {
		return false
	}
	if request.URL.Path == "/v1/generations" {
		return paid.publicModels[operation.Model]
	}
	return paid.publicModels[operation.PublicModelID]
}

func receiveLabCallback(c *gin.Context, secret string) {
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 128*1024)
	payload, err := io.ReadAll(c.Request.Body)
	timestamp, timeErr := strconv.ParseInt(c.GetHeader("X-Relay-Timestamp"), 10, 64)
	if err != nil || timeErr != nil || timestamp < time.Now().Add(-5*time.Minute).Unix() || timestamp > time.Now().Add(time.Minute).Unix() {
		c.AbortWithStatus(http.StatusUnauthorized)
		return
	}
	eventID := c.GetHeader("X-Relay-Event-ID")
	signature, err := service.SignPlatformGenerationCallback(secret, timestamp, eventID, payload)
	if err != nil || !labExactSecret(c.GetHeader("X-Relay-Signature"), signature) {
		c.AbortWithStatus(http.StatusUnauthorized)
		return
	}
	var event dto.PlatformGenerationCallbackEvent
	if common.Unmarshal(payload, &event) != nil || event.Validate() != nil || event.EventID != eventID {
		c.AbortWithStatus(http.StatusUnprocessableEntity)
		return
	}
	record := labCallbackRecord{
		EventID: eventID, PayloadSHA256: fmt.Sprintf("%x", sha256.Sum256(payload)),
		JobID: event.Job.ID, Status: event.Job.Status, ReceivedAt: time.Now().UTC(),
	}
	var existing labCallbackRecord
	err = model.DB.Where("event_id = ?", eventID).First(&existing).Error
	if err == nil {
		if existing.PayloadSHA256 != record.PayloadSHA256 {
			c.AbortWithStatus(http.StatusConflict)
			return
		}
		c.Header("X-Relay-Callback-Duplicate", "true")
		c.Status(http.StatusNoContent)
		return
	}
	if !errors.Is(err, gorm.ErrRecordNotFound) || model.DB.Create(&record).Error != nil {
		c.AbortWithStatus(http.StatusServiceUnavailable)
		return
	}
	c.Header("X-Relay-Callback-Duplicate", "false")
	c.Status(http.StatusNoContent)
}
