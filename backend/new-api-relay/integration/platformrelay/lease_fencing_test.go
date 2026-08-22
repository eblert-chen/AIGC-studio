//go:build integration

package platformrelay_test

import (
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"gorm.io/gorm"
)

func TestPostgresSubmissionLeaseTakeoverFencesStaleCompletion(t *testing.T) {
	resetIntegrationState(t)
	job, outbox := createQueuedGeneration(t, "integration.submission.fence", "text_to_video")

	first, err := model.ClaimPlatformGenerationSubmission(30*time.Second, outbox.ID)
	requireNoError(t, err)
	forceSubmissionLeaseExpired(t, job.ID, outbox.ID)
	second, err := model.ClaimPlatformGenerationSubmission(30*time.Second, outbox.ID)
	requireNoError(t, err)
	if first.Token == second.Token {
		t.Fatal("submission lease takeover reused its fencing token")
	}

	won, err := model.CompletePlatformGenerationSubmission(*first, map[string]any{
		"status": model.PlatformGenerationStatusFailed,
	})
	requireNoError(t, err)
	if won {
		t.Fatal("expired submission owner completed after a newer lease was issued")
	}
	won, err = model.CompletePlatformGenerationSubmission(*second, map[string]any{
		"status":   model.PlatformGenerationStatusProcessing,
		"progress": 1,
	})
	requireNoError(t, err)
	if !won {
		t.Fatal("current submission owner could not complete its live lease")
	}
}

func TestPostgresSubmissionClaimRepairsTerminalJobOutboxWithoutNilSuccess(t *testing.T) {
	resetIntegrationState(t)
	job, outbox := createQueuedGeneration(t, "integration.submission.terminal-repair", "text_to_video")
	requireNoError(t, integrationDB.Model(&model.PlatformGenerationJob{}).Where("id = ?", job.ID).
		Update("status", model.PlatformGenerationStatusFailed).Error)

	claim, err := model.ClaimPlatformGenerationSubmission(30*time.Second, outbox.ID)
	if claim != nil {
		t.Fatal("terminal job unexpectedly returned a submission claim")
	}
	if !errors.Is(err, gorm.ErrRecordNotFound) {
		t.Fatalf("terminal outbox repair did not report an empty queue: %v", err)
	}

	var repaired model.PlatformGenerationOutbox
	requireNoError(t, integrationDB.First(&repaired, outbox.ID).Error)
	if repaired.State != model.PlatformGenerationOutboxCompleted || repaired.ClaimToken != "" {
		t.Fatalf("terminal job submission outbox was not safely completed: state=%q token=%q", repaired.State, repaired.ClaimToken)
	}
}

func TestPostgresSubmissionTakeoverRejectsOldRouteBegin(t *testing.T) {
	resetIntegrationState(t)
	route := createProviderRoute(t, "integration.route.owner.fence", "text_to_video", 100, 4)
	job, outbox := createQueuedGeneration(t, route.Model, route.Mode)

	first, err := model.ClaimPlatformGenerationSubmission(30*time.Second, outbox.ID)
	requireNoError(t, err)
	routeClaim, err := model.ClaimPlatformGenerationProviderRoute(job.ID, route.Model, route.Mode)
	requireNoError(t, err)
	forceSubmissionLeaseExpired(t, job.ID, outbox.ID)
	second, err := model.ClaimPlatformGenerationSubmission(30*time.Second, outbox.ID)
	requireNoError(t, err)
	if first.Token == second.Token {
		t.Fatal("submission takeover did not rotate the owner token")
	}

	_, err = model.BeginPlatformGenerationRouteSubmission(job.ID, route.ID, first.Token, routeClaim.SubmissionToken)
	if err == nil {
		t.Fatal("expired submission owner began a provider request by borrowing the replacement worker's lease")
	}
	_, err = model.BeginPlatformGenerationRouteSubmission(job.ID, route.ID, second.Token, routeClaim.SubmissionToken)
	requireNoError(t, err)
}

func TestPostgresSubmissionTakeoverRejectsOldNativeRecoveryStage(t *testing.T) {
	resetIntegrationState(t)
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON", `{"schema_version":1,"active_key_id":"pg-test-v1","keys":{"pg-test-v1":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}}`)
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE", "")
	route := createProviderRoute(t, "integration.native.stage.fence", "text_to_video", 100, 4)
	job, outbox := createQueuedGeneration(t, route.Model, route.Mode)

	first, err := model.ClaimPlatformGenerationSubmission(30*time.Second, outbox.ID)
	requireNoError(t, err)
	routeClaim, err := model.ClaimPlatformGenerationProviderRoute(job.ID, route.Model, route.Mode)
	requireNoError(t, err)
	_, err = model.BeginPlatformGenerationRouteSubmission(job.ID, route.ID, first.Token, routeClaim.SubmissionToken)
	requireNoError(t, err)
	forceSubmissionLeaseExpired(t, job.ID, outbox.ID)
	second, err := model.ClaimPlatformGenerationSubmission(30*time.Second, outbox.ID)
	requireNoError(t, err)
	if first.Token == second.Token {
		t.Fatal("submission takeover did not rotate the owner token")
	}

	var channel model.Channel
	requireNoError(t, integrationDB.First(&channel, route.ChannelID).Error)
	pinnedKey, err := channel.GetKeyAt(route.KeyIndex)
	requireNoError(t, err)
	nativeTaskID, err := model.PlatformGenerationNativeTaskID(job.ID)
	requireNoError(t, err)
	keyIndex := route.KeyIndex
	recovery := &model.Task{
		TaskID:     nativeTaskID,
		ChannelId:  route.ChannelID,
		Platform:   constant.TaskPlatform("integration-native-stage"),
		UserId:     42,
		Group:      "service",
		Action:     constant.TaskActionTextGenerate,
		SubmitTime: time.Now().UTC().Unix(),
		Properties: model.Properties{UpstreamModelName: route.UpstreamModel, OriginModelName: route.Model},
		PrivateData: model.TaskPrivateData{
			PinnedKeyIndex:       &keyIndex,
			PinnedKeyFingerprint: route.KeyFingerprint,
			TransientProviderKey: pinnedKey,
		},
	}

	if err := model.StagePlatformGenerationNativeTaskRecovery(job.ID, first.Token, recovery); err == nil {
		t.Fatal("expired submission owner staged provider recovery identity after takeover")
	}
	var afterStale model.PlatformGenerationJob
	requireNoError(t, integrationDB.First(&afterStale, "id = ?", job.ID).Error)
	if afterStale.NativeTaskID != "" || afterStale.NativeTaskRecoveryJSON != "" {
		t.Fatal("stale native recovery stage mutated the provider-neutral job")
	}

	requireNoError(t, model.StagePlatformGenerationNativeTaskRecovery(job.ID, second.Token, recovery))
	var afterCurrent model.PlatformGenerationJob
	requireNoError(t, integrationDB.First(&afterCurrent, "id = ?", job.ID).Error)
	if afterCurrent.NativeTaskID != nativeTaskID || afterCurrent.NativeTaskRecoveryJSON == "" {
		t.Fatal("current submission owner could not stage the pinned provider recovery identity")
	}
}

func TestPostgresTransferLeaseTakeoverFencesStalePublisher(t *testing.T) {
	resetIntegrationState(t)
	job, _ := createQueuedGeneration(t, "integration.transfer.fence", "text_to_video")
	requireNoError(t, integrationDB.Model(&model.PlatformGenerationJob{}).Where("id = ?", job.ID).Updates(map[string]any{
		"status":           model.PlatformGenerationStatusTransferring,
		"next_transfer_at": gorm.Expr("CURRENT_TIMESTAMP - INTERVAL '1 second'"),
	}).Error)

	_, firstToken, err := model.ClaimPlatformGenerationTransfer(30 * time.Second)
	requireNoError(t, err)
	firstObjectKey := "outputs/" + job.TenantID + "/" + job.ID + "/" + uuid.NewString()
	_, err = model.CreatePlatformArtifactUploadIntent(job.ID, firstToken, firstObjectKey, "test_store", strings.Repeat("c", 64))
	requireNoError(t, err)
	requireNoError(t, integrationDB.Model(&model.PlatformGenerationJob{}).Where("id = ?", job.ID).
		Update("transfer_lease_expires_at", gorm.Expr("CURRENT_TIMESTAMP - INTERVAL '1 second'")).Error)
	_, secondToken, err := model.ClaimPlatformGenerationTransfer(30 * time.Second)
	requireNoError(t, err)
	secondObjectKey := "outputs/" + job.TenantID + "/" + job.ID + "/" + uuid.NewString()
	_, err = model.CreatePlatformArtifactUploadIntent(job.ID, secondToken, secondObjectKey, "test_store", strings.Repeat("c", 64))
	requireNoError(t, err)
	if firstToken == secondToken {
		t.Fatal("transfer lease takeover reused its fencing token")
	}

	won, err := model.CompletePlatformGenerationTransfer(job.ID, firstToken, firstObjectKey, `[{"asset_id":"stale","object_key":"`+firstObjectKey+`"}]`)
	requireNoError(t, err)
	if won {
		t.Fatal("stale transfer owner published an artifact after takeover")
	}
	won, err = model.CompletePlatformGenerationTransfer(job.ID, secondToken, secondObjectKey, `[{"asset_id":"current","object_key":"`+secondObjectKey+`"}]`)
	requireNoError(t, err)
	if !won {
		t.Fatal("current transfer owner could not publish its artifact")
	}

	var persisted model.PlatformGenerationJob
	requireNoError(t, integrationDB.First(&persisted, "id = ?", job.ID).Error)
	if persisted.Status != model.PlatformGenerationStatusSucceeded || persisted.OutputsJSON != `[{"asset_id":"current","object_key":"`+secondObjectKey+`"}]` {
		t.Fatalf("unexpected terminal transfer state: status=%q outputs=%s", persisted.Status, persisted.OutputsJSON)
	}
}

func TestPostgresArtifactCleanupTakeoverFencesExpiredWorker(t *testing.T) {
	resetIntegrationState(t)
	job, _ := createQueuedGeneration(t, "integration.artifact.cleanup.fence", "text_to_video")
	requireNoError(t, integrationDB.Model(&model.PlatformGenerationJob{}).Where("id = ?", job.ID).Updates(map[string]any{
		"status":           model.PlatformGenerationStatusTransferring,
		"next_transfer_at": gorm.Expr("CURRENT_TIMESTAMP - INTERVAL '1 second'"),
	}).Error)

	_, transferToken, err := model.ClaimPlatformGenerationTransfer(30 * time.Second)
	requireNoError(t, err)
	objectKey := "outputs/" + job.TenantID + "/" + job.ID + "/" + uuid.NewString()
	intent, err := model.CreatePlatformArtifactUploadIntent(job.ID, transferToken, objectKey, "test_store", strings.Repeat("c", 64))
	requireNoError(t, err)
	requireNoError(t, integrationDB.Model(&model.PlatformGenerationJob{}).Where("id = ?", job.ID).
		Update("transfer_lease_expires_at", gorm.Expr("CURRENT_TIMESTAMP - INTERVAL '1 second'")).Error)
	_, err = model.SchedulePlatformArtifactUploadCleanup(job.ID, transferToken)
	requireNoError(t, err)

	type claimResult struct {
		claim *model.PlatformArtifactCleanupClaim
		err   error
	}
	start := make(chan struct{})
	results := make(chan claimResult, 2)
	for range 2 {
		go func() {
			<-start
			claim, claimErr := model.ClaimPlatformArtifactCleanup(30 * time.Second)
			results <- claimResult{claim: claim, err: claimErr}
		}()
	}
	close(start)
	var first *model.PlatformArtifactCleanupClaim
	notFound := 0
	for range 2 {
		result := <-results
		switch {
		case result.err == nil:
			if first != nil {
				t.Fatal("two cleanup workers claimed the same intent")
			}
			first = result.claim
		case errors.Is(result.err, gorm.ErrRecordNotFound):
			notFound++
		default:
			t.Fatalf("concurrent cleanup claim failed: %v", result.err)
		}
	}
	if first == nil || notFound != 1 {
		t.Fatalf("unexpected concurrent cleanup ownership: owner=%v not_found=%d", first != nil, notFound)
	}
	if first.Intent.ID != intent.ID {
		t.Fatalf("claimed wrong cleanup intent: got=%s want=%s", first.Intent.ID, intent.ID)
	}
	requireNoError(t, integrationDB.Model(&model.PlatformArtifactUploadIntent{}).Where("id = ?", intent.ID).
		Update("claim_expires_at", gorm.Expr("CURRENT_TIMESTAMP - INTERVAL '1 second'")).Error)
	second, err := model.ClaimPlatformArtifactCleanup(30 * time.Second)
	requireNoError(t, err)
	if first.Token == second.Token {
		t.Fatal("cleanup lease takeover reused its fencing token")
	}

	won, err := model.CompletePlatformArtifactCleanup(intent.ID, first.Token, time.Minute, 24*time.Hour, 2)
	requireNoError(t, err)
	if won {
		t.Fatal("expired cleanup worker completed after takeover")
	}
	won, err = model.CompletePlatformArtifactCleanup(intent.ID, second.Token, time.Minute, 24*time.Hour, 2)
	requireNoError(t, err)
	if !won {
		t.Fatal("current cleanup worker could not complete its claim")
	}
}

func TestPostgresArtifactIntentCreateAndCompleteUseDeadlockSafeLockOrder(t *testing.T) {
	for iteration := 0; iteration < 8; iteration++ {
		resetIntegrationState(t)
		job, _ := createQueuedGeneration(t, "integration.artifact.lock.order."+uuid.NewString(), "text_to_video")
		requireNoError(t, integrationDB.Model(&model.PlatformGenerationJob{}).Where("id = ?", job.ID).Updates(map[string]any{
			"status":           model.PlatformGenerationStatusTransferring,
			"next_transfer_at": gorm.Expr("CURRENT_TIMESTAMP - INTERVAL '1 second'"),
		}).Error)
		_, transferToken, err := model.ClaimPlatformGenerationTransfer(30 * time.Second)
		requireNoError(t, err)
		objectKey := "outputs/" + job.TenantID + "/" + job.ID + "/" + uuid.NewString()
		bindingID := strings.Repeat("c", 64)
		_, err = model.CreatePlatformArtifactUploadIntent(job.ID, transferToken, objectKey, "test_store", bindingID)
		requireNoError(t, err)
		outputsJSON := `[{"asset_id":"asset","object_key":"` + objectKey + `"}]`

		type operationResult struct {
			operation string
			won       bool
			err       error
		}
		start := make(chan struct{})
		results := make(chan operationResult, 2)
		go func() {
			<-start
			_, createErr := model.CreatePlatformArtifactUploadIntent(job.ID, transferToken, objectKey, "test_store", bindingID)
			results <- operationResult{operation: "create", err: createErr}
		}()
		go func() {
			<-start
			won, completeErr := model.CompletePlatformGenerationTransfer(job.ID, transferToken, objectKey, outputsJSON)
			results <- operationResult{operation: "complete", won: won, err: completeErr}
		}()
		close(start)

		timer := time.NewTimer(5 * time.Second)
		completeWon := false
		for range 2 {
			select {
			case result := <-results:
				switch result.operation {
				case "create":
					if result.err != nil && !errors.Is(result.err, model.ErrPlatformArtifactUploadIntentFenced) {
						t.Fatalf("idempotent create failed unexpectedly: %v", result.err)
					}
				case "complete":
					requireNoError(t, result.err)
					completeWon = result.won
				default:
					t.Fatalf("unexpected operation result: %s", result.operation)
				}
			case <-timer.C:
				t.Fatal("artifact intent create/complete deadlocked")
			}
		}
		if !timer.Stop() {
			select {
			case <-timer.C:
			default:
			}
		}
		if !completeWon {
			t.Fatal("artifact completion lost its live fenced lease")
		}
	}
}
