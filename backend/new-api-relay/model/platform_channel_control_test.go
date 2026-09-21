package model

import (
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/glebarez/sqlite"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func setupPlatformChannelControlModelTest(t *testing.T) {
	t.Helper()
	originalDB := DB
	originalDatabaseType := common.MainDatabaseType()
	dsn := "file:platform-channel-control-" + uuid.NewString() + "?mode=memory&cache=shared&_pragma=busy_timeout(5000)"
	database, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{})
	require.NoError(t, err)
	DB = database
	common.SetMainDatabaseType(common.DatabaseTypeSQLite)
	t.Cleanup(func() {
		DB = originalDB
		common.SetMainDatabaseType(originalDatabaseType)
	})
	require.NoError(t, database.AutoMigrate(&Channel{}, &ProviderChannelCredentialSetVersion{}))
	require.NoError(t, MigrateProviderChannelCredentialVaultStorage())
	require.NoError(t, MigratePlatformChannelControlStorage())
	require.NoError(t, MigratePlatformChannelControlStorageV6WithDB(database))
	require.NoError(t, database.AutoMigrate(&Ability{}))
}

func createPlatformChannelControlTestChannel(t *testing.T, status int) Channel {
	t.Helper()
	channel := Channel{
		Name:        "control-test-channel",
		Key:         "provider-secret",
		Status:      status,
		Models:      "provider-model",
		CreatedTime: 1_786_700_000,
	}
	require.NoError(t, DB.Create(&channel).Error)
	require.NoError(t, DB.Create(&Ability{Group: "default", Model: "provider-model", ChannelId: channel.Id, Enabled: status == common.ChannelStatusEnabled}).Error)
	return channel
}

func platformChannelControlTestIntent(channelID int, operationID string) PlatformChannelControlIntent {
	return PlatformChannelControlIntent{
		OperationID:                 operationID,
		TenantID:                    "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30",
		ChannelID:                   channelID,
		Kind:                        PlatformChannelControlOperationKindTest,
		RequestID:                   "channel-control-request-0001",
		Actor:                       "platform-owner-1",
		Reason:                      "Verify Relay channel health",
		Model:                       "provider-model",
		PublicModelID:               "public-provider-model",
		RouteID:                     "route-provider-model-1",
		UpstreamModel:               "provider-model",
		CapabilityProfileID:         "test-profile-v1",
		CapabilityProfileRevision:   "sha256:" + strings.Repeat("a", 64),
		CapabilityRevision:          "sha256:" + strings.Repeat("b", 64),
		RoutingReleaseSHA256:        "sha256:" + strings.Repeat("c", 64),
		RouteBindingSHA256:          "sha256:" + strings.Repeat("d", 64),
		CredentialFingerprintSHA256: strings.Repeat("e", 64),
		TransportRevision:           "sha256:" + strings.Repeat("f", 64),
		TransportSHA256:             "sha256:" + strings.Repeat("1", 64),
	}
}

func TestPlatformChannelTestReconciliationTextRejectsSecretsAndControls(t *testing.T) {
	assert.True(t, isSafePlatformChannelTestReconciliationText("已在供应商控制台确认没有创建任务"))
	assert.True(t, isSafePlatformChannelTestReconciliationText("Provider console checked; no task was created"))

	unsafe := []string{
		"provider response\nraw body",
		"operator\u007fmarker",
		"hidden\u202Etext",
		"https://provider.invalid/task/1?token=secret",
		"custom+scheme://provider/task/1",
		"Bearer provider-secret",
		"Authorization : provider-secret",
		"token: provider-secret",
		"key = provider-secret",
		"password=provider-secret",
		"secret : provider-secret",
		"sig=provider-secret",
		"checked console?X-Amz-Signature=provider-secret",
		"checked console&signature=provider-secret",
	}
	for _, value := range unsafe {
		t.Run(fmt.Sprintf("unsafe-%x", len(value)), func(t *testing.T) {
			assert.False(t, isSafePlatformChannelTestReconciliationText(value), value)
		})
	}
}

func TestPlatformChannelControlV6DatabaseRequiresTransportEvidence(t *testing.T) {
	setupPlatformChannelControlModelTest(t)
	channel := createPlatformChannelControlTestChannel(t, common.ChannelStatusEnabled)
	tests := []struct {
		name   string
		mutate func(*PlatformChannelControlOperation)
	}{
		{name: "both missing", mutate: func(operation *PlatformChannelControlOperation) {
			operation.IntentTransportRevision = ""
			operation.IntentTransportSHA256 = ""
		}},
		{name: "revision missing", mutate: func(operation *PlatformChannelControlOperation) {
			operation.IntentTransportRevision = ""
		}},
		{name: "digest missing", mutate: func(operation *PlatformChannelControlOperation) {
			operation.IntentTransportSHA256 = ""
		}},
		{name: "revision malformed", mutate: func(operation *PlatformChannelControlOperation) {
			operation.IntentTransportRevision = "sha256:not-a-digest"
		}},
		{name: "digest malformed", mutate: func(operation *PlatformChannelControlOperation) {
			operation.IntentTransportSHA256 = "SHA256:" + strings.Repeat("a", 64)
		}},
	}
	for index, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			intent := platformChannelControlTestIntent(channel.Id, fmt.Sprintf("channel-test-db-transport-guard-%04d", index+1))
			payload, digest, err := platformChannelControlIntentPayload(intent)
			require.NoError(t, err)
			operation := newPlatformChannelControlOperation(intent, payload, digest, time.Now().UTC())
			test.mutate(&operation)

			err = DB.Session(&gorm.Session{SkipHooks: true}).Create(&operation).Error
			require.Error(t, err, "a direct database writer must not bypass the v6 transport binding")
		})
	}
}

func TestPlatformChannelTestIntentRequiresExactGenerationRouteBinding(t *testing.T) {
	setupPlatformChannelControlModelTest(t)
	channel := createPlatformChannelControlTestChannel(t, common.ChannelStatusEnabled)
	valid := platformChannelControlTestIntent(channel.Id, "channel-test-route-binding-0001")

	tests := []struct {
		name   string
		mutate func(*PlatformChannelControlIntent)
	}{
		{name: "missing public model", mutate: func(intent *PlatformChannelControlIntent) { intent.PublicModelID = "" }},
		{name: "missing route", mutate: func(intent *PlatformChannelControlIntent) { intent.RouteID = "" }},
		{name: "missing upstream model", mutate: func(intent *PlatformChannelControlIntent) { intent.UpstreamModel = "" }},
		{name: "model is not the bound upstream", mutate: func(intent *PlatformChannelControlIntent) { intent.Model = "other-model" }},
		{name: "missing transport evidence", mutate: func(intent *PlatformChannelControlIntent) {
			intent.TransportRevision = ""
		}},
	}
	for index, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			intent := valid
			intent.OperationID = fmt.Sprintf("channel-test-route-binding-%04d", index+2)
			test.mutate(&intent)
			_, _, _, err := BeginPlatformChannelTestOperation(intent)
			require.ErrorContains(t, err, "route binding")
		})
	}

	var count int64
	require.NoError(t, DB.Model(&PlatformChannelControlOperation{}).Count(&count).Error)
	assert.Zero(t, count, "invalid or partial route bindings must fail before durable execution intent is created")
}

func TestPlatformChannelTestIntentIsAtMostOnceAndTerminalReceiptIsImmutable(t *testing.T) {
	setupPlatformChannelControlModelTest(t)
	channel := createPlatformChannelControlTestChannel(t, common.ChannelStatusEnabled)
	intent := platformChannelControlTestIntent(channel.Id, "channel-test-operation-0001")

	created, execute, replay, err := BeginPlatformChannelTestOperation(intent)
	require.NoError(t, err)
	assert.True(t, execute)
	assert.False(t, replay)
	assert.Equal(t, PlatformChannelControlOperationPending, created.State)

	second, execute, replay, err := BeginPlatformChannelTestOperation(intent)
	require.NoError(t, err)
	assert.False(t, execute, "a persisted test intent must never automatically resend")
	assert.True(t, replay)
	assert.Equal(t, created.ID, second.ID)

	conflictIntent := intent
	conflictIntent.Reason = "Different semantic intent"
	_, _, _, err = BeginPlatformChannelTestOperation(conflictIntent)
	assert.ErrorIs(t, err, ErrPlatformChannelControlOperationConflict)

	completed, err := CompletePlatformChannelRouteTestFailure(intent.TenantID, intent.OperationID, 42, PlatformChannelControlErrorTestFailed, false)
	require.NoError(t, err)
	assert.Equal(t, PlatformChannelControlOperationFailed, completed.State)
	require.NotNil(t, completed.ResultSuccess)
	assert.False(t, *completed.ResultSuccess)

	// A late duplicate completion cannot replace the first terminal result.
	_, err = CompletePlatformChannelRouteTestFailure(intent.TenantID, intent.OperationID, 7, PlatformChannelControlErrorTestFailed, false)
	assert.ErrorIs(t, err, ErrPlatformChannelControlOperationConflict)

	err = DB.Model(&PlatformChannelControlOperation{}).Where("id = ?", completed.ID).Update("actor", "tampered").Error
	assert.ErrorIs(t, err, ErrPlatformChannelControlOperationImmutable)
	err = DB.Delete(&PlatformChannelControlOperation{}, "id = ?", completed.ID).Error
	assert.ErrorIs(t, err, ErrPlatformChannelControlOperationImmutable)
}

func TestPlatformChannelTestClaimedProviderRejectionClosesWithoutInventingTaskID(t *testing.T) {
	setupPlatformChannelControlModelTest(t)
	channel := createPlatformChannelControlTestChannel(t, common.ChannelStatusEnabled)
	intent := platformChannelControlTestIntent(channel.Id, "channel-test-provider-rejected-0001")

	_, execute, _, err := BeginPlatformChannelTestOperation(intent)
	require.NoError(t, err)
	require.True(t, execute)
	_, claimed, err := ClaimPlatformChannelTestSubmission(intent.TenantID, intent.OperationID)
	require.NoError(t, err)
	require.True(t, claimed)

	completed, err := CompletePlatformChannelRouteTestProvenNoCreation(
		intent.TenantID,
		intent.OperationID,
		19,
		PlatformChannelControlErrorTestValidation,
	)
	require.NoError(t, err)
	assert.Equal(t, PlatformChannelControlOperationFailed, completed.State)
	assert.Equal(t, PlatformChannelTestSubmissionProviderRejected, completed.ProviderSubmissionState)
	assert.Empty(t, completed.ProviderTaskID, "a rejected provider request must never invent a task identity")
	require.NotNil(t, completed.ResultSuccess)
	assert.False(t, *completed.ResultSuccess)
	assert.Equal(t, PlatformChannelControlErrorTestValidation, completed.ResultErrorCode)

	unknown := platformChannelControlTestIntent(channel.Id, "channel-test-transport-unknown-0001")
	_, execute, _, err = BeginPlatformChannelTestOperation(unknown)
	require.NoError(t, err)
	require.True(t, execute)
	_, claimed, err = ClaimPlatformChannelTestSubmission(unknown.TenantID, unknown.OperationID)
	require.NoError(t, err)
	require.True(t, claimed)
	for _, errorCode := range []string{
		PlatformChannelControlErrorTestFailed,
		PlatformChannelControlErrorTestUnavailable,
		PlatformChannelControlErrorTestArtifact,
		PlatformChannelControlErrorTestRouteDrift,
	} {
		_, err = CompletePlatformChannelRouteTestProvenNoCreation(
			unknown.TenantID,
			unknown.OperationID,
			19,
			errorCode,
		)
		require.ErrorContains(t, err, "proven no-creation")
	}
}

func TestPlatformChannelTestSubmittedAcceptanceBlockerCannotReleaseRoute(t *testing.T) {
	setupPlatformChannelControlModelTest(t)
	channel := createPlatformChannelControlTestChannel(t, common.ChannelStatusEnabled)
	intent := platformChannelControlTestIntent(channel.Id, "channel-test-artifact-blocked-0001")

	_, execute, _, err := BeginPlatformChannelTestOperation(intent)
	require.NoError(t, err)
	require.True(t, execute)
	_, claimed, err := ClaimPlatformChannelTestSubmission(intent.TenantID, intent.OperationID)
	require.NoError(t, err)
	require.True(t, claimed)
	_, err = RecordPlatformChannelTestSubmitted(intent.TenantID, intent.OperationID, "provider-task-artifact-blocked-1")
	require.NoError(t, err)
	_, err = RecordPlatformChannelTestBlocker(
		intent.TenantID,
		intent.OperationID,
		PlatformChannelControlErrorTestArtifact,
	)
	require.NoError(t, err)

	_, err = CompletePlatformChannelRouteTestFailure(
		intent.TenantID,
		intent.OperationID,
		31,
		PlatformChannelControlErrorTestArtifact,
		true,
	)
	require.ErrorContains(t, err, "not provider-terminal")

	sticky, err := GetPlatformChannelControlOperation(intent.TenantID, channel.Id, intent.OperationID)
	require.NoError(t, err)
	assert.Equal(t, PlatformChannelControlOperationPending, sticky.State)
	assert.Equal(t, PlatformChannelTestSubmissionSubmitted, sticky.ProviderSubmissionState)
	assert.Equal(t, PlatformChannelControlErrorTestArtifact, sticky.ProviderBlockerCode)
	assert.Equal(t, "provider-task-artifact-blocked-1", sticky.ProviderTaskID)
	assert.Nil(t, sticky.CompletedAt)

	replay, execute, idempotentReplay, err := BeginPlatformChannelTestOperation(intent)
	require.NoError(t, err)
	assert.False(t, execute, "the model layer never authorizes a second POST on replay")
	assert.True(t, idempotentReplay)
	assert.Equal(t, sticky.ID, replay.ID)

	other := intent
	other.OperationID = "channel-test-artifact-blocked-0002"
	_, _, _, err = BeginPlatformChannelTestOperation(other)
	assert.ErrorIs(t, err, ErrPlatformChannelTestRouteBlocked)

	_, err = RecordPlatformChannelTestBlocker(intent.TenantID, intent.OperationID, "SECRET_PROVIDER_BODY")
	require.ErrorContains(t, err, "blocker is invalid")
}

func TestManagedPlatformChannelTestCompletionRevalidatesCurrentCredentialAndRouteTuple(t *testing.T) {
	setupPlatformChannelControlModelTest(t)
	require.NoError(t, DB.AutoMigrate(&PlatformGenerationProviderRoute{}))
	channel := createPlatformChannelControlTestChannel(t, common.ChannelStatusEnabled)
	managedTag := ProviderOnboardingManagedChannelTag
	require.NoError(t, DB.Model(&Channel{}).Where("id = ?", channel.Id).Update("tag", managedTag).Error)
	intent := platformChannelControlTestIntent(channel.Id, "managed-channel-test-route-drift-0001")
	intent.Mode = "text_to_video"
	intent.CredentialFingerprintSHA256 = providerChannelCredentialFingerprint("provider-secret")
	require.NoError(t, DB.Create(&PlatformGenerationProviderRoute{
		RouteKey: intent.RouteID, Model: intent.PublicModelID, Mode: intent.Mode,
		ProviderName: "managed-provider", AccountID: "primary", ChannelID: channel.Id,
		KeyIndex: 0, KeyFingerprint: intent.CredentialFingerprintSHA256,
		ChannelClass: "official", UpstreamModel: intent.UpstreamModel,
		CapabilityProfileID:       intent.CapabilityProfileID,
		CapabilityProfileRevision: intent.CapabilityProfileRevision,
		Enabled:                   true,
	}).Error)

	_, execute, _, err := BeginPlatformChannelTestOperation(intent)
	require.NoError(t, err)
	require.True(t, execute)
	_, claimed, err := ClaimPlatformChannelTestSubmission(intent.TenantID, intent.OperationID)
	require.NoError(t, err)
	require.True(t, claimed)
	_, err = RecordPlatformChannelTestSubmitted(intent.TenantID, intent.OperationID, "managed-provider-task-drift-1")
	require.NoError(t, err)
	require.NoError(t, DB.Model(&PlatformGenerationProviderRoute{}).
		Where("route_key = ? AND mode = ?", intent.RouteID, intent.Mode).
		Update("capability_profile_revision", "sha256:"+strings.Repeat("9", 64)).Error)

	_, err = CompletePlatformChannelRouteTestSuccess(intent.TenantID, intent.OperationID, 23, PlatformChannelTestArtifactEvidence{
		SHA256: strings.Repeat("a", 64), SizeBytes: 128, ContentType: "video/mp4",
	})
	assert.ErrorIs(t, err, ErrPlatformChannelControlOperationConflict)
	stored, err := GetPlatformChannelControlOperation(intent.TenantID, channel.Id, intent.OperationID)
	require.NoError(t, err)
	assert.Equal(t, PlatformChannelControlOperationPending, stored.State)
	assert.Equal(t, PlatformChannelTestSubmissionSubmitted, stored.ProviderSubmissionState)
	assert.Nil(t, stored.CompletedAt)
}

func TestPlatformChannelTestReceiptAcceptsOnlyClosedSecretFreeFailureTaxonomy(t *testing.T) {
	accepted := []string{
		PlatformChannelControlErrorTestFailed,
		PlatformChannelControlErrorTestUnavailable,
		PlatformChannelControlErrorTestValidation,
		PlatformChannelControlErrorTestAuth,
		PlatformChannelControlErrorTestQuota,
		PlatformChannelControlErrorTestTerminal,
	}
	for _, errorCode := range accepted {
		t.Run(errorCode, func(t *testing.T) {
			setupPlatformChannelControlModelTest(t)
			channel := createPlatformChannelControlTestChannel(t, common.ChannelStatusEnabled)
			intent := platformChannelControlTestIntent(channel.Id, "channel-test-taxonomy-"+fmt.Sprintf("%x", len(errorCode))+"-0001")
			_, execute, _, err := BeginPlatformChannelTestOperation(intent)
			require.NoError(t, err)
			require.True(t, execute)

			completed, err := CompletePlatformChannelRouteTestFailure(intent.TenantID, intent.OperationID, 11, errorCode, false)
			require.NoError(t, err)
			require.Equal(t, errorCode, completed.ResultErrorCode)
		})
	}

	setupPlatformChannelControlModelTest(t)
	channel := createPlatformChannelControlTestChannel(t, common.ChannelStatusEnabled)
	intent := platformChannelControlTestIntent(channel.Id, "channel-test-taxonomy-reject-0001")
	_, execute, _, err := BeginPlatformChannelTestOperation(intent)
	require.NoError(t, err)
	require.True(t, execute)
	_, err = CompletePlatformChannelRouteTestFailure(intent.TenantID, intent.OperationID, 11, "SECRET_RAW_PROVIDER_ERROR", false)
	require.ErrorContains(t, err, "channel test route failure is invalid")
}

func TestPlatformChannelStatusUsesRevisionCASAndPersistsFailedReceipt(t *testing.T) {
	setupPlatformChannelControlModelTest(t)
	channel := createPlatformChannelControlTestChannel(t, common.ChannelStatusEnabled)
	initialRevision := PlatformChannelControlRevision(channel)
	intent := PlatformChannelControlIntent{
		OperationID:      "channel-status-operation-0001",
		TenantID:         "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30",
		ChannelID:        channel.Id,
		Kind:             PlatformChannelControlOperationKindStatus,
		RequestID:        "channel-control-request-0002",
		Actor:            "platform-owner-1",
		Reason:           "Disable unhealthy provider channel",
		ExpectedRevision: initialRevision,
		TargetStatus:     common.ChannelStatusManuallyDisabled,
	}

	receipt, replay, err := ApplyPlatformChannelStatusOperation(intent)
	require.NoError(t, err)
	assert.False(t, replay)
	assert.Equal(t, PlatformChannelControlOperationSucceeded, receipt.State)
	assert.Equal(t, initialRevision, receipt.IntentExpectedRevision)
	assert.Equal(t, "manually_disabled", receipt.IntentTargetStatus)
	require.NotNil(t, receipt.ResultChanged)
	assert.True(t, *receipt.ResultChanged)
	assert.NotEqual(t, initialRevision, receipt.ResultRevision)

	var saved Channel
	require.NoError(t, DB.First(&saved, channel.Id).Error)
	assert.Equal(t, common.ChannelStatusManuallyDisabled, saved.Status)
	var ability Ability
	require.NoError(t, DB.Where("channel_id = ?", channel.Id).First(&ability).Error)
	assert.False(t, ability.Enabled)

	replayed, replay, err := ApplyPlatformChannelStatusOperation(intent)
	require.NoError(t, err)
	assert.True(t, replay)
	assert.Equal(t, receipt.ID, replayed.ID)

	stale := intent
	stale.OperationID = "channel-status-operation-0002"
	stale.TargetStatus = common.ChannelStatusEnabled
	failed, replay, err := ApplyPlatformChannelStatusOperation(stale)
	assert.ErrorIs(t, err, ErrPlatformChannelControlRevisionConflict)
	assert.False(t, replay)
	require.NotNil(t, failed)
	assert.Equal(t, PlatformChannelControlOperationFailed, failed.State)
	assert.Equal(t, PlatformChannelControlErrorRevisionConflict, failed.ResultErrorCode)
	assert.Equal(t, failed.ResultPreviousStatus, failed.ResultCurrentStatus)
	require.NotNil(t, failed.ResultChanged)
	assert.False(t, *failed.ResultChanged)

	readback, err := GetPlatformChannelControlOperation(intent.TenantID, channel.Id, stale.OperationID)
	require.NoError(t, err)
	assert.Equal(t, failed.ID, readback.ID)
	assert.Equal(t, "enabled", readback.IntentTargetStatus)

	wrongSemantic := stale
	wrongSemantic.TargetStatus = common.ChannelStatusManuallyDisabled
	_, _, err = ApplyPlatformChannelStatusOperation(wrongSemantic)
	assert.True(t, errors.Is(err, ErrPlatformChannelControlOperationConflict))
}

func TestPlatformChannelStatusRejectsReservedAndManagedOnboardingChannels(t *testing.T) {
	setupPlatformChannelControlModelTest(t)
	tag := ProviderOnboardingManagedChannelTag
	tests := []struct {
		name      string
		channelID int
		tag       *string
		otherInfo string
	}{
		{name: "reserved identity", channelID: ProviderOnboardingGoogleChannelID},
		{name: "managed tag", channelID: 880001, tag: &tag},
		{
			name: "durable lifecycle marker without tag", channelID: 880002,
			otherInfo: `{"credential_late_provider":{"schema_version":1,"provider":"google","account_id":"primary","channel_id":880002,"public_model_ids":["provider-model"],"state":"route_test_ready"}}`,
		},
		{
			name: "malformed lifecycle marker fails closed", channelID: 880003,
			otherInfo: `{"credential_late_provider":`,
		},
		{
			name: "escaped malformed lifecycle marker fails closed", channelID: 880004,
			otherInfo: `{"credential_late_provide\u0072":`,
		},
	}
	for index, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			channel := Channel{
				Id: test.channelID, Name: "managed-status-guard", Key: "managed-provider-secret",
				Status: common.ChannelStatusManuallyDisabled, Models: "provider-model", Tag: test.tag,
				OtherInfo: test.otherInfo,
			}
			require.NoError(t, DB.Create(&channel).Error)
			require.NoError(t, DB.Create(&Ability{
				Group: "default", Model: "provider-model", ChannelId: channel.Id, Enabled: false,
			}).Error)
			require.NoError(t, DB.First(&channel, channel.Id).Error)

			_, replay, err := ApplyPlatformChannelStatusOperation(PlatformChannelControlIntent{
				OperationID: fmt.Sprintf("managed-channel-status-guard-%04d", index+1),
				TenantID:    "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30", ChannelID: channel.Id,
				Kind: PlatformChannelControlOperationKindStatus, RequestID: "managed-channel-status-request",
				Actor: "platform-owner-1", Reason: "Must use the dedicated provider onboarding lifecycle",
				ExpectedRevision: PlatformChannelControlRevision(channel), TargetStatus: common.ChannelStatusEnabled,
			})
			assert.ErrorIs(t, err, ErrProviderOnboardingManagedChannel)
			assert.False(t, replay)

			var persisted Channel
			require.NoError(t, DB.First(&persisted, channel.Id).Error)
			assert.Equal(t, common.ChannelStatusManuallyDisabled, persisted.Status)
			var ability Ability
			require.NoError(t, DB.First(&ability, "channel_id = ?", channel.Id).Error)
			assert.False(t, ability.Enabled)
			var receipts int64
			require.NoError(t, DB.Model(&PlatformChannelControlOperation{}).
				Where("channel_id = ?", channel.Id).Count(&receipts).Error)
			assert.Zero(t, receipts)
		})
	}
}

func TestProviderOnboardingMarkerFenceDoesNotClaimOrdinaryOtherInfo(t *testing.T) {
	ordinary := []Channel{
		{Id: 880010, OtherInfo: `{"status_reason":"operator disabled"}`},
		{Id: 880011, OtherInfo: `{"legacy_metadata":`},
		{Id: 880012},
	}
	for index := range ordinary {
		assert.False(t, IsProviderOnboardingManagedChannel(&ordinary[index]))
	}
}

func TestPlatformChannelStatusReplayRechecksManagedTagButKeepsDeletedOrdinaryReceipt(t *testing.T) {
	setupPlatformChannelControlModelTest(t)
	channel := createPlatformChannelControlTestChannel(t, common.ChannelStatusManuallyDisabled)
	intent := PlatformChannelControlIntent{
		OperationID: "channel-status-managed-replay-fence-0001",
		TenantID:    "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30", ChannelID: channel.Id,
		Kind: PlatformChannelControlOperationKindStatus, RequestID: "managed-replay-request",
		Actor: "platform-owner-1", Reason: "Verify managed replay fence",
		ExpectedRevision: PlatformChannelControlRevision(channel), TargetStatus: common.ChannelStatusEnabled,
	}
	receipt, replay, err := ApplyPlatformChannelStatusOperation(intent)
	require.NoError(t, err)
	assert.False(t, replay)
	require.NotNil(t, receipt)

	managedTag := ProviderOnboardingManagedChannelTag
	require.NoError(t, DB.Model(&Channel{}).Where("id = ?", channel.Id).Update("tag", managedTag).Error)
	_, replay, err = ApplyPlatformChannelStatusOperation(intent)
	assert.ErrorIs(t, err, ErrProviderOnboardingManagedChannel)
	assert.False(t, replay)
	var operations int64
	require.NoError(t, DB.Model(&PlatformChannelControlOperation{}).Count(&operations).Error)
	assert.Equal(t, int64(1), operations)

	ordinary := createPlatformChannelControlTestChannel(t, common.ChannelStatusManuallyDisabled)
	ordinaryIntent := intent
	ordinaryIntent.OperationID = "channel-status-deleted-replay-0001"
	ordinaryIntent.ChannelID = ordinary.Id
	ordinaryIntent.ExpectedRevision = PlatformChannelControlRevision(ordinary)
	receipt, replay, err = ApplyPlatformChannelStatusOperation(ordinaryIntent)
	require.NoError(t, err)
	assert.False(t, replay)
	require.NoError(t, DB.Exec("DELETE FROM channels WHERE id = ?", ordinary.Id).Error)
	replayed, replay, err := ApplyPlatformChannelStatusOperation(ordinaryIntent)
	require.NoError(t, err)
	assert.True(t, replay)
	assert.Equal(t, receipt.ID, replayed.ID)
}

func TestPlatformChannelControlRevisionTracksNativeMutationsWithoutHealthNoise(t *testing.T) {
	setupPlatformChannelControlModelTest(t)
	channel := createPlatformChannelControlTestChannel(t, common.ChannelStatusEnabled)

	mutations := []struct {
		column string
		value  any
	}{
		{column: "type", value: 1},
		{column: "credential_set_version", value: "rotated-provider-secret"},
		{column: "open_ai_organization", value: "organization-2"},
		{column: "test_model", value: "provider-model-test"},
		{column: "status", value: common.ChannelStatusManuallyDisabled},
		{column: "name", value: "renamed-control-test-channel"},
		{column: "weight", value: 11},
		{column: "base_url", value: "https://provider.example.invalid"},
		{column: "other", value: "other-configuration"},
		{column: "models", value: "provider-model,provider-model-2"},
		{column: "group", value: "premium"},
		{column: "model_mapping", value: `{"alias":"provider-model"}`},
		{column: "status_code_mapping", value: `{"429":"500"}`},
		{column: "priority", value: 17},
		{column: "auto_ban", value: 0},
		{column: "other_info", value: `{"status_reason":"native mutation"}`},
		{column: "tag", value: "revision-tested"},
		{column: "setting", value: `{"proxy":""}`},
		{column: "param_override", value: `{"temperature":0.1}`},
		{column: "header_override", value: `{"X-Safe":"value"}`},
		{column: "remark", value: "operator-visible remark"},
		{column: "channel_info", value: ChannelInfo{
			IsMultiKey:         true,
			MultiKeySize:       2,
			MultiKeyStatusList: map[int]int{1: common.ChannelStatusAutoDisabled},
		}},
		{column: "settings", value: `{"upstream_model_update_check_enabled":true}`},
	}

	var saved Channel
	require.NoError(t, DB.First(&saved, channel.Id).Error)
	expectedControlRevision := saved.ControlRevision
	for _, mutation := range mutations {
		t.Run(mutation.column, func(t *testing.T) {
			if mutation.column == "credential_set_version" {
				require.NoError(t, RotateChannelCredentialSet(channel.Id, mutation.value.(string)))
			} else {
				require.NoError(t, DB.Model(&Channel{}).Where("id = ?", channel.Id).
					Update(mutation.column, mutation.value).Error)
			}
			require.NoError(t, DB.First(&saved, channel.Id).Error)
			expectedControlRevision++
			assert.Equal(t, expectedControlRevision, saved.ControlRevision)
		})
	}

	// Provider observations and accounting do not change an operator's
	// approval proof.
	require.NoError(t, DB.Model(&Channel{}).Where("id = ?", channel.Id).Updates(map[string]any{
		"test_time":            int64(1_786_700_100),
		"response_time":        99,
		"balance":              12.5,
		"balance_updated_time": int64(1_786_700_101),
		"used_quota":           int64(1234),
	}).Error)
	require.NoError(t, DB.First(&saved, channel.Id).Error)
	assert.Equal(t, expectedControlRevision, saved.ControlRevision)

	// The Platform path relies on the same database trigger and must bump
	// exactly once rather than once in Go and once in the trigger.
	platformExpectedRevision := PlatformChannelControlRevision(saved)
	receipt, replay, err := ApplyPlatformChannelStatusOperation(PlatformChannelControlIntent{
		OperationID:      "channel-status-native-revision-0001",
		TenantID:         "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30",
		ChannelID:        channel.Id,
		Kind:             PlatformChannelControlOperationKindStatus,
		RequestID:        "channel-control-native-revision-0001",
		Actor:            "platform-owner-1",
		Reason:           "Verify the shared revision trigger",
		ExpectedRevision: platformExpectedRevision,
		TargetStatus:     common.ChannelStatusEnabled,
	})
	require.NoError(t, err)
	assert.False(t, replay)
	require.NoError(t, DB.First(&saved, channel.Id).Error)
	expectedControlRevision++
	assert.Equal(t, expectedControlRevision, saved.ControlRevision)
	assert.Equal(t, PlatformChannelControlRevision(saved), receipt.ResultRevision)

	// A native status ABA must invalidate the old Platform detail even though
	// the visible status eventually returns to its original value.
	approvalRevision := PlatformChannelControlRevision(saved)
	require.NoError(t, DB.Model(&Channel{}).Where("id = ?", channel.Id).
		Update("status", common.ChannelStatusManuallyDisabled).Error)
	require.NoError(t, DB.Model(&Channel{}).Where("id = ?", channel.Id).
		Update("status", common.ChannelStatusEnabled).Error)
	expectedControlRevision += 2
	require.NoError(t, DB.First(&saved, channel.Id).Error)
	assert.Equal(t, expectedControlRevision, saved.ControlRevision)
	assert.Equal(t, common.ChannelStatusEnabled, saved.Status)

	failed, _, err := ApplyPlatformChannelStatusOperation(PlatformChannelControlIntent{
		OperationID:      "channel-status-native-revision-0002",
		TenantID:         "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30",
		ChannelID:        channel.Id,
		Kind:             PlatformChannelControlOperationKindStatus,
		RequestID:        "channel-control-native-revision-0002",
		Actor:            "platform-owner-1",
		Reason:           "Reject an approval captured before native ABA",
		ExpectedRevision: approvalRevision,
		TargetStatus:     common.ChannelStatusManuallyDisabled,
	})
	assert.ErrorIs(t, err, ErrPlatformChannelControlRevisionConflict)
	require.NotNil(t, failed)

	// A stale native full-row save may change configuration, but it cannot
	// write its old revision over a newer database-owned value.
	var stale Channel
	require.NoError(t, DB.First(&stale, channel.Id).Error)
	require.NoError(t, DB.Model(&Channel{}).Where("id = ?", channel.Id).
		Update("weight", 29).Error)
	expectedControlRevision++
	stale.Name = fmt.Sprintf("stale-save-%d", stale.ControlRevision)
	require.NoError(t, stale.SaveWithoutKey())
	expectedControlRevision++
	require.NoError(t, DB.First(&saved, channel.Id).Error)
	assert.Equal(t, expectedControlRevision, saved.ControlRevision)
	assert.Greater(t, saved.ControlRevision, stale.ControlRevision)
}

func TestPlatformChannelControlRevisionCoversNativeChannelMutationAPIs(t *testing.T) {
	setupPlatformChannelControlModelTest(t)
	originalMemoryCacheEnabled := common.MemoryCacheEnabled
	common.MemoryCacheEnabled = false
	t.Cleanup(func() { common.MemoryCacheEnabled = originalMemoryCacheEnabled })

	tag := "native-control-tag"
	channel := createPlatformChannelControlTestChannel(t, common.ChannelStatusEnabled)
	require.NoError(t, DB.Model(&Channel{}).Where("id = ?", channel.Id).Update("tag", tag).Error)

	var saved Channel
	require.NoError(t, DB.First(&saved, channel.Id).Error)
	expected := saved.ControlRevision
	assertBump := func(label string, mutate func() error) {
		t.Helper()
		require.NoError(t, mutate(), label)
		require.NoError(t, DB.First(&saved, channel.Id).Error)
		expected++
		assert.Equal(t, expected, saved.ControlRevision, label)
	}

	assertBump("Channel.Update", func() error {
		editable, err := GetChannelById(channel.Id, true)
		if err != nil {
			return err
		}
		editable.Models = "provider-model,provider-model-native"
		return editable.Update()
	})
	assertBump("UpdateChannelStatus", func() error {
		if !UpdateChannelStatus(channel.Id, "", common.ChannelStatusManuallyDisabled, "native status test") {
			return errors.New("UpdateChannelStatus reported no mutation")
		}
		return nil
	})
	assertBump("EnableChannelByTag", func() error { return EnableChannelByTag(tag) })
	assertBump("EditChannelByTag", func() error {
		weight := uint(31)
		return EditChannelByTag(tag, nil, nil, nil, nil, nil, &weight, nil, nil)
	})
	assertBump("BatchSetChannelTag", func() error {
		newTag := "native-control-tag-updated"
		return BatchSetChannelTag([]int{channel.Id}, &newTag)
	})
	assertBump("SaveChannelInfo", func() error {
		latest, err := GetChannelById(channel.Id, true)
		if err != nil {
			return err
		}
		latest.ChannelInfo = ChannelInfo{
			IsMultiKey:             true,
			MultiKeySize:           2,
			MultiKeyStatusList:     map[int]int{1: common.ChannelStatusAutoDisabled},
			MultiKeyDisabledReason: map[int]string{1: "native key disabled"},
		}
		return latest.SaveChannelInfo()
	})

	// The native observation/accounting helpers remain revision-neutral.
	saved.UpdateResponseTime(71)
	saved.UpdateBalance(9.25)
	updateChannelUsedQuota(channel.Id, 19)
	require.NoError(t, DB.First(&saved, channel.Id).Error)
	assert.Equal(t, expected, saved.ControlRevision)
}
