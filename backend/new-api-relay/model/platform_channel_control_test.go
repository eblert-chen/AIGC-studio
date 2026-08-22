package model

import (
	"errors"
	"fmt"
	"testing"

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
		OperationID: operationID,
		TenantID:    "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30",
		ChannelID:   channelID,
		Kind:        PlatformChannelControlOperationKindTest,
		RequestID:   "channel-control-request-0001",
		Actor:       "platform-owner-1",
		Reason:      "Verify Relay channel health",
	}
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

	completed, err := CompletePlatformChannelTestOperation(intent.TenantID, intent.OperationID, false, 42, PlatformChannelControlErrorTestFailed)
	require.NoError(t, err)
	assert.Equal(t, PlatformChannelControlOperationFailed, completed.State)
	require.NotNil(t, completed.ResultSuccess)
	assert.False(t, *completed.ResultSuccess)

	// A late duplicate completion cannot replace the first terminal result.
	terminal, err := CompletePlatformChannelTestOperation(intent.TenantID, intent.OperationID, true, 7, "")
	require.NoError(t, err)
	assert.Equal(t, PlatformChannelControlOperationFailed, terminal.State)
	require.NotNil(t, terminal.ResultSuccess)
	assert.False(t, *terminal.ResultSuccess)

	err = DB.Model(&PlatformChannelControlOperation{}).Where("id = ?", completed.ID).Update("actor", "tampered").Error
	assert.ErrorIs(t, err, ErrPlatformChannelControlOperationImmutable)
	err = DB.Delete(&PlatformChannelControlOperation{}, "id = ?", completed.ID).Error
	assert.ErrorIs(t, err, ErrPlatformChannelControlOperationImmutable)
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
