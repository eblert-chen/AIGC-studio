//go:build integration

package platformrelay_test

import (
	"crypto/sha256"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
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

// prepareTerminalPlatformTransferBinding upgrades the deliberately minimal
// queued-generation fixture into the same immutable route/account/credential
// binding that a real Platform-owned native task carries before artifact
// publication. Transfer fencing tests must exercise that production contract;
// otherwise terminal cleanup correctly refuses to erase ambiguous provider
// material and the test never reaches the lease behavior it intends to prove.
func prepareTerminalPlatformTransferBinding(t *testing.T, job model.PlatformGenerationJob) {
	t.Helper()
	route := createProviderRoute(t, job.Model, job.Mode, 100, 4)
	profile, ok := generationprofile.Get(generationprofile.VolcengineArkVideoGenerationV1)
	if !ok {
		t.Fatal("the code-reviewed Seedance profile is unavailable")
	}
	request := dto.NewPlatformGenerationRequest()
	request.Model = job.Model
	request.Mode = job.Mode
	request.ExpectedCapabilityRevision = job.ExpectedCapabilityRevision
	request.Inputs.Prompt = "A durable transfer fencing fixture"
	request.Output.Resolution = "720p"
	request.Output.DurationSeconds = 5
	request.Output.AspectRatio = "16:9"
	request.Output.Count = 1
	request.Metadata = generationprofile.SnapshotMetadata(request.Metadata, profile)
	requireNoError(t, profile.ValidateRequest(request))
	requestJSON, err := common.Marshal(request)
	requireNoError(t, err)
	requestDigest := sha256.Sum256(requestJSON)
	profileSnapshot, ok := request.Metadata[generationprofile.MetadataProfileSnapshot].(string)
	if !ok || profileSnapshot == "" {
		t.Fatal("the Seedance profile snapshot was not serialized into the request")
	}
	route.AcceptedChannelType = profile.NativeChannelType
	route.CapabilityProfileID = profile.ID
	route.CapabilityProfileRevision = profile.Revision
	route.CapabilityProfileSnapshot = profileSnapshot
	route.ModelReleaseID = "integration-seedance-release-v1"
	route.ModelReleaseRevision = "sha256:" + strings.Repeat("d", 64)
	route.ModelReleaseCapabilityRevision = job.CapabilityRevision
	requireNoError(t, integrationDB.Model(route).Updates(map[string]any{
		"accepted_channel_type":             route.AcceptedChannelType,
		"capability_profile_id":             route.CapabilityProfileID,
		"capability_profile_revision":       route.CapabilityProfileRevision,
		"capability_profile_snapshot":       route.CapabilityProfileSnapshot,
		"model_release_id":                  route.ModelReleaseID,
		"model_release_revision":            route.ModelReleaseRevision,
		"model_release_capability_revision": route.ModelReleaseCapabilityRevision,
	}).Error)
	var channel model.Channel
	requireNoError(t, integrationDB.First(&channel, route.ChannelID).Error)
	providerKey, err := channel.GetKeyAt(route.KeyIndex)
	requireNoError(t, err)
	nativeTaskID, err := model.PlatformGenerationNativeTaskID(job.ID)
	requireNoError(t, err)
	keyIndex := route.KeyIndex
	upstreamTaskID := "provider-task-" + uuid.NewString()
	providerURL := "https://provider.example/temporary.mp4?X-Signature=" + uuid.NewString()
	now := time.Now().UTC()
	nativeTask := &model.Task{
		TaskID:     nativeTaskID,
		Platform:   constant.TaskPlatform("integration-platform-transfer"),
		ChannelId:  route.ChannelID,
		Action:     constant.TaskActionTextGenerate,
		Status:     model.TaskStatusSuccess,
		Progress:   "100%",
		FinishTime: now.Unix(),
		SubmitTime: now.Unix(),
		CreatedAt:  now.Unix(),
		UpdatedAt:  now.Unix(),
		PrivateData: model.TaskPrivateData{
			BillingSource:        model.TaskBillingSourcePlatformExternal,
			UpstreamTaskID:       upstreamTaskID,
			ResultURL:            providerURL,
			PinnedKeyIndex:       &keyIndex,
			PinnedKeyFingerprint: route.KeyFingerprint,
			TransientProviderKey: providerKey,
		},
	}
	requireNoError(t, model.BindTaskProviderCredentialVersion(nativeTask, job.TenantID))
	attempt := 1
	recoveryJSON, err := common.Marshal(map[string]any{
		"schema_version":                4,
		"billing_owner":                 "platform",
		"billing_policy_revision":       "platform-external-v1",
		"route_id":                      route.ID,
		"attempt":                       attempt,
		"task_id":                       nativeTaskID,
		"channel_id":                    route.ChannelID,
		"platform":                      string(nativeTask.Platform),
		"user_id":                       nativeTask.UserId,
		"group":                         nativeTask.Group,
		"action":                        nativeTask.Action,
		"submit_time":                   nativeTask.SubmitTime,
		"properties":                    nativeTask.Properties,
		"pinned_key_index":              keyIndex,
		"pinned_key_fingerprint":        route.KeyFingerprint,
		"provider_credential_tenant_id": job.TenantID,
		"provider_credential_version":   nativeTask.PrivateData.ProviderCredentialVersion,
		"request_json_sha256":           fmt.Sprintf("sha256:%x", requestDigest),
	})
	requireNoError(t, err)
	closedAt := now
	admission := &model.PlatformGenerationRouteAdmission{
		JobID:               job.ID,
		RouteID:             route.ID,
		SubmissionTokenHash: strings.Repeat("e", 64),
		State:               model.PlatformGenerationRouteAdmissionFinished,
		SlotHeld:            false,
		Attempt:             attempt,
		ClosedAt:            &closedAt,
	}
	requireNoError(t, integrationDB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Create(nativeTask).Error; err != nil {
			return err
		}
		if err := tx.Create(admission).Error; err != nil {
			return err
		}
		return tx.Model(&model.PlatformGenerationJob{}).Where("id = ?", job.ID).Updates(map[string]any{
			"status":                      model.PlatformGenerationStatusTransferring,
			"next_transfer_at":            gorm.Expr("CURRENT_TIMESTAMP - INTERVAL '1 second'"),
			"request_json":                string(requestJSON),
			"native_task_id":              nativeTaskID,
			"native_task_recovery_json":   string(recoveryJSON),
			"provider_route_id":           route.ID,
			"provider_channel_id":         route.ChannelID,
			"provider_key_index":          route.KeyIndex,
			"provider_submission_attempt": attempt,
			"upstream_task_id":            upstreamTaskID,
			"upstream_result_url":         providerURL,
			"temporary_result_json":       `{"result_url":"` + providerURL + `"}`,
		}).Error
	}))
	binding, err := model.ResolvePlatformGenerationProviderResultBinding(*nativeTask)
	requireNoError(t, err)
	responseBody := []byte(`{"id":"` + upstreamTaskID + `","model":"` + route.UpstreamModel + `","status":"succeeded","resolution":"720p","duration":"5","ratio":"16:9","content":{"video_url":"` + providerURL + `"}}`)
	receipt, err := model.NewPlatformGenerationProviderResultReceipt(
		*nativeTask,
		binding,
		model.PlatformGenerationProviderResultObservation{
			Protocol:        profile.Protocol,
			TaskID:          upstreamTaskID,
			Model:           route.UpstreamModel,
			ProviderStatus:  "succeeded",
			Resolution:      request.Output.Resolution,
			DurationSeconds: request.Output.DurationSeconds,
			AspectRatio:     request.Output.AspectRatio,
			OutputCount:     request.Output.Count,
			MediaType:       "video",
			Succeeded:       true,
			ResponseBody:    responseBody,
			ArtifactURL:     providerURL,
		},
	)
	requireNoError(t, err)
	receiptJSON, err := common.Marshal(receipt)
	requireNoError(t, err)
	requireNoError(t, integrationDB.Model(nativeTask).Update("data", string(receiptJSON)).Error)
	nativeTask.Data = receiptJSON
	requireNoError(t, model.ValidatePlatformGenerationProviderResultReceipt(*nativeTask, binding, receipt))
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
