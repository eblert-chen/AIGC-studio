package controller

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strconv"
	"sync/atomic"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestManagedProviderChannelNativeOperationsMakeNoProviderRequestOrMutation(t *testing.T) {
	db := setupModelListControllerTestDB(t)
	var providerRequests atomic.Int32
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		providerRequests.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":[{"id":"must-not-be-observed"}]}`))
	}))
	t.Cleanup(provider.Close)

	tag := model.ProviderOnboardingManagedChannelTag
	settings := dto.ChannelOtherSettings{
		UpstreamModelUpdateCheckEnabled:       true,
		UpstreamModelUpdateAutoSyncEnabled:    true,
		UpstreamModelUpdateLastDetectedModels: []string{"must-not-be-added"},
	}
	channel := &model.Channel{
		Id: model.ProviderOnboardingGoogleChannelID, Type: constant.ChannelTypeOpenAI,
		Name: "managed provider native guard", Key: "managed-provider-secret",
		BaseURL: &provider.URL, Models: "kept-model", Group: "default",
		Status: common.ChannelStatusEnabled, Tag: &tag,
	}
	channel.SetOtherSettings(settings)
	require.NoError(t, db.Create(channel).Error)
	require.NoError(t, db.Create(&model.Ability{
		Group: "default", Model: "kept-model", ChannelId: channel.Id, Enabled: false,
	}).Error)

	result := testChannel(context.Background(), channel, 1, "kept-model", "", false)
	assert.ErrorIs(t, result.localErr, model.ErrProviderOnboardingManagedChannel)
	assert.Equal(t, channelTestSummary{}, performChannelTests(
		context.Background(), []*model.Channel{channel}, 1, true, nil,
	))

	_, err := updateChannelBalance(channel)
	assert.ErrorIs(t, err, model.ErrProviderOnboardingManagedChannel)
	require.NoError(t, updateAllChannelsBalance())

	_, err = fetchChannelUpstreamModelIDs(channel)
	assert.ErrorIs(t, err, model.ErrProviderOnboardingManagedChannel)
	workingSettings := channel.GetOtherSettings()
	changed, added, err := checkAndPersistChannelUpstreamModelUpdatesWithContext(
		context.Background(), channel, &workingSettings, true, true,
	)
	assert.ErrorIs(t, err, model.ErrProviderOnboardingManagedChannel)
	assert.False(t, changed)
	assert.Zero(t, added)
	_, _, _, _, changed, err = applyChannelUpstreamModelUpdates(
		channel, []string{"must-not-be-added"}, nil, nil,
	)
	assert.ErrorIs(t, err, model.ErrProviderOnboardingManagedChannel)
	assert.False(t, changed)
	assert.Equal(t, upstreamModelUpdateSummary{}, runChannelUpstreamModelUpdateTaskOnce(
		context.Background(), true, true, nil,
	))

	recorder := httptest.NewRecorder()
	ctx, _ := gin.CreateTestContext(recorder)
	ctx.Params = gin.Params{{Key: "id", Value: strconv.Itoa(channel.Id)}}
	ctx.Request = httptest.NewRequest(http.MethodGet, "/api/channel/test/"+strconv.Itoa(channel.Id), nil)
	TestChannel(ctx)
	assert.Contains(t, recorder.Body.String(), "PROVIDER_ONBOARDING_MANAGED_CHANNEL")

	assert.Zero(t, providerRequests.Load(), "no legacy path may contact the provider")
	var persisted model.Channel
	require.NoError(t, db.Omit("key", "credential_set_version").First(&persisted, channel.Id).Error)
	assert.Equal(t, common.ChannelStatusEnabled, persisted.Status)
	assert.Equal(t, "kept-model", persisted.Models)
	assert.Equal(t, channel.OtherSettings, persisted.OtherSettings)
	var ability model.Ability
	require.NoError(t, db.First(&ability, "channel_id = ?", channel.Id).Error)
	assert.False(t, ability.Enabled)
}

func TestManagedProviderTagIsExcludedFromAutomaticNativeOperations(t *testing.T) {
	db := setupModelListControllerTestDB(t)
	tag := model.ProviderOnboardingManagedChannelTag
	channel := &model.Channel{
		Type: constant.ChannelTypeOpenAI, Name: "tagged managed provider",
		Key: "tagged-managed-secret", Models: "kept-model", Group: "default",
		Status: common.ChannelStatusEnabled, Tag: &tag,
	}
	require.NoError(t, db.Create(channel).Error)

	selected := selectChannelsForAutomaticTest([]*model.Channel{channel}, "")
	assert.Empty(t, selected)
	var discovered []*model.Channel
	require.NoError(t, model.ExcludeProviderOnboardingManagedChannels(
		db.Model(&model.Channel{}),
	).Find(&discovered).Error)
	assert.Empty(t, discovered)
}

func TestManagedProviderLifecycleMarkerWithoutTagBlocksEveryLegacyMutationAndProviderCall(t *testing.T) {
	db := setupModelListControllerTestDB(t)
	var providerRequests atomic.Int32
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		providerRequests.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":[{"id":"must-not-be-observed"}]}`))
	}))
	t.Cleanup(provider.Close)

	ordinaryTag := "ordinary-looking-tag"
	channel := &model.Channel{
		Id: 880021, Type: constant.ChannelTypeOpenAI,
		Name: "marker-owned provider", Key: "managed-provider-secret",
		BaseURL: &provider.URL, Models: "kept-model", Group: "default",
		Status: common.ChannelStatusEnabled, Tag: &ordinaryTag,
		OtherInfo: `{"credential_late_provider":{"schema_version":1,"provider":"google","account_id":"primary","channel_id":880021,"public_model_ids":["kept-model"],"state":"route_test_ready"}}`,
	}
	require.NoError(t, db.Create(channel).Error)
	require.NoError(t, db.Create(&model.Ability{
		Group: "default", Model: "kept-model", ChannelId: channel.Id, Enabled: false,
	}).Error)
	require.True(t, model.IsProviderOnboardingManagedChannel(channel))

	result := testChannel(context.Background(), channel, 1, "kept-model", "", false)
	assert.ErrorIs(t, result.localErr, model.ErrProviderOnboardingManagedChannel)
	_, err := updateChannelBalance(channel)
	assert.ErrorIs(t, err, model.ErrProviderOnboardingManagedChannel)
	_, err = fetchChannelUpstreamModelIDs(channel)
	assert.ErrorIs(t, err, model.ErrProviderOnboardingManagedChannel)

	loaded, err := model.GetChannelById(channel.Id, true)
	require.NoError(t, err)
	loaded.Name = "must-not-save"
	assert.ErrorIs(t, loaded.SaveWithoutKey(), model.ErrProviderOnboardingManagedChannel)
	assert.False(t, model.UpdateChannelStatus(channel.Id, "", common.ChannelStatusManuallyDisabled, "legacy status"))
	newTag := "must-not-retag"
	assert.ErrorIs(t, model.BatchSetChannelTag([]int{channel.Id}, &newTag), model.ErrProviderOnboardingManagedChannel)
	deleted, err := model.BatchDeleteChannels([]int{channel.Id})
	assert.ErrorIs(t, err, model.ErrProviderOnboardingManagedChannel)
	assert.Zero(t, deleted)

	_, _, err = model.FixAbility()
	require.NoError(t, err)
	var ability model.Ability
	require.NoError(t, db.First(&ability, "channel_id = ?", channel.Id).Error)
	assert.False(t, ability.Enabled, "global ability repair must preserve the managed lifecycle fence")
	assert.Zero(t, providerRequests.Load(), "no marker-owned legacy path may contact the provider")

	var persisted model.Channel
	require.NoError(t, db.Omit("key", "credential_set_version").First(&persisted, channel.Id).Error)
	assert.Equal(t, "marker-owned provider", persisted.Name)
	assert.Equal(t, common.ChannelStatusEnabled, persisted.Status)
	assert.Equal(t, ordinaryTag, persisted.GetTag())
}

func TestNativeModelMutationsCannotInjectOrOverwriteManagedProviderTag(t *testing.T) {
	db := setupModelListControllerTestDB(t)
	ordinaryTag := "ordinary-provider"
	managedTag := model.ProviderOnboardingManagedChannelTag
	channel := &model.Channel{
		Type: constant.ChannelTypeOpenAI, Name: "ordinary provider",
		Key: "ordinary-provider-secret", Models: "kept-model", Group: "default",
		Status: common.ChannelStatusManuallyDisabled, Tag: &ordinaryTag,
	}
	require.NoError(t, db.Create(channel).Error)
	require.NoError(t, db.Create(&model.Ability{
		Group: "default", Model: "kept-model", ChannelId: channel.Id, Tag: &ordinaryTag, Enabled: false,
	}).Error)

	update, err := model.GetChannelById(channel.Id, true)
	require.NoError(t, err)
	update.Tag = &managedTag
	assert.ErrorIs(t, update.Update(), model.ErrProviderOnboardingManagedChannel)
	assert.ErrorIs(t, model.BatchSetChannelTag([]int{channel.Id}, &managedTag), model.ErrProviderOnboardingManagedChannel)
	assert.ErrorIs(t, model.EditChannelByTag(ordinaryTag, &managedTag, nil, nil, nil, nil, nil, nil, nil), model.ErrProviderOnboardingManagedChannel)

	require.NoError(t, db.Model(&model.Channel{}).Where("id = ?", channel.Id).Update("tag", managedTag).Error)
	assert.False(t, model.UpdateChannelStatus(channel.Id, "", common.ChannelStatusEnabled, "legacy status must refuse managed tag"))
	deleted, err := model.BatchDeleteChannels([]int{channel.Id})
	assert.ErrorIs(t, err, model.ErrProviderOnboardingManagedChannel)
	assert.Zero(t, deleted)

	var persisted model.Channel
	require.NoError(t, db.Omit("key", "credential_set_version").First(&persisted, channel.Id).Error)
	require.NotNil(t, persisted.Tag)
	assert.Equal(t, managedTag, *persisted.Tag)
	assert.Equal(t, common.ChannelStatusManuallyDisabled, persisted.Status)
	var ability model.Ability
	require.NoError(t, db.First(&ability, "channel_id = ?", channel.Id).Error)
	assert.False(t, ability.Enabled)
}
