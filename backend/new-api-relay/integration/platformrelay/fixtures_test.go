//go:build integration

package platformrelay_test

import (
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"gorm.io/gorm"
)

func createQueuedGeneration(t *testing.T, modelName string, mode string) (model.PlatformGenerationJob, model.PlatformGenerationOutbox) {
	t.Helper()
	now := time.Now().UTC()
	revision := "sha256:" + strings.Repeat("a", 64)
	job := model.PlatformGenerationJob{
		ID:                         uuid.NewString(),
		TenantID:                   "00000000-0000-4000-8000-000000000001",
		SourceClientID:             "integration-platform",
		RequestID:                  "it-" + uuid.NewString(),
		IdempotencyKey:             uuid.NewString(),
		RequestHash:                strings.Repeat("b", 64),
		RequestJSON:                `{}`,
		Model:                      modelName,
		Mode:                       mode,
		ExpectedCapabilityRevision: revision,
		CapabilityRevision:         revision,
		Status:                     model.PlatformGenerationStatusQueued,
		OutputsJSON:                `[]`,
		ErrorDetailsJSON:           `{}`,
		NextPollAt:                 now.Add(-time.Second),
		NextTransferAt:             now.Add(-time.Second),
		CreatedAt:                  now,
		UpdatedAt:                  now,
	}
	outbox := model.PlatformGenerationOutbox{
		JobID:       job.ID,
		Topic:       "generation.submit",
		State:       model.PlatformGenerationOutboxPending,
		AvailableAt: now.Add(-time.Second),
		CreatedAt:   now,
		UpdatedAt:   now,
	}
	requireNoError(t, integrationDB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Create(&job).Error; err != nil {
			return err
		}
		return tx.Create(&outbox).Error
	}))
	return job, outbox
}

func createProviderRoute(t *testing.T, modelName string, mode string, rpmLimit int, activeLimit int) *model.PlatformGenerationProviderRoute {
	t.Helper()
	channelID := int(time.Now().UnixNano() % 1_000_000_000)
	if channelID < 1 {
		channelID = 1
	}
	key := "provider-key-" + uuid.NewString()
	channel := model.Channel{
		Id:     channelID,
		Type:   constant.ChannelTypeOpenAI,
		Name:   "platform-relay-integration",
		Key:    key,
		Status: common.ChannelStatusEnabled,
	}
	requireNoError(t, integrationDB.Create(&channel).Error)
	route := &model.PlatformGenerationProviderRoute{
		RouteKey:            "route-" + uuid.NewString(),
		Model:               modelName,
		Mode:                mode,
		ProviderName:        "integration-provider",
		AccountID:           "integration-account",
		ChannelID:           channel.Id,
		AcceptedChannelType: constant.ChannelTypeOpenAI,
		KeyIndex:            0,
		KeyFingerprint:      fmt.Sprintf("%x", common.Sha256Raw([]byte(key))),
		ChannelClass:        model.PlatformGenerationChannelClassOfficialProvider,
		UpstreamModel:       modelName,
		StagingReady:        true,
		ProductionReady:     true,
		Enabled:             true,
		RPMWindowSeconds:    60,
		RPMLimit:            rpmLimit,
		ActiveLimit:         activeLimit,
	}
	requireNoError(t, model.CreatePlatformGenerationProviderRoute(route))
	return route
}

func createRouteForSharedAccount(t *testing.T, base *model.PlatformGenerationProviderRoute, modelName string, mode string) *model.PlatformGenerationProviderRoute {
	t.Helper()
	route := &model.PlatformGenerationProviderRoute{
		RouteKey:            "route-" + uuid.NewString(),
		Model:               modelName,
		Mode:                mode,
		ProviderName:        base.ProviderName,
		AccountID:           base.AccountID,
		ChannelID:           base.ChannelID,
		AcceptedChannelType: base.AcceptedChannelType,
		KeyIndex:            base.KeyIndex,
		KeyFingerprint:      base.KeyFingerprint,
		ChannelClass:        base.ChannelClass,
		UpstreamModel:       modelName,
		StagingReady:        base.StagingReady,
		ProductionReady:     true,
		Enabled:             true,
		RPMWindowSeconds:    base.RPMWindowSeconds,
		RPMLimit:            base.RPMLimit,
		ActiveLimit:         base.ActiveLimit,
	}
	requireNoError(t, model.CreatePlatformGenerationProviderRoute(route))
	if route.AccountStateID != base.AccountStateID {
		t.Fatalf("shared provider account mapped to different state rows: %d != %d", route.AccountStateID, base.AccountStateID)
	}
	return route
}

func forceSubmissionLeaseExpired(t *testing.T, jobID string, outboxID int64) {
	t.Helper()
	requireNoError(t, integrationDB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Model(&model.PlatformGenerationJob{}).Where("id = ?", jobID).
			Update("submission_lease_expires_at", gorm.Expr("CURRENT_TIMESTAMP - INTERVAL '1 second'")).Error; err != nil {
			return err
		}
		return tx.Model(&model.PlatformGenerationOutbox{}).Where("id = ?", outboxID).
			Update("claim_expires_at", gorm.Expr("CURRENT_TIMESTAMP - INTERVAL '1 second'")).Error
	}))
}

func dbNow(t *testing.T) time.Time {
	t.Helper()
	var now time.Time
	requireNoError(t, integrationDB.Raw("SELECT CURRENT_TIMESTAMP").Scan(&now).Error)
	return now.UTC()
}
