package model

import (
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestPlatformGenerationActiveTaskProtectsChannelPollingIdentity(t *testing.T) {
	preparePlatformGenerationRouteTest(t)
	route := createPlatformGenerationRouteFixture(t, "channel.guard", 10, 1)
	claim, err := ClaimPlatformGenerationProviderRoute(uuid.NewString(), route.Model, route.Mode)
	require.NoError(t, err)

	channel, err := GetChannelById(route.ChannelID, true)
	require.NoError(t, err)
	channel.Key = "rotated-while-active"
	err = channel.Update()
	assert.ErrorIs(t, err, ErrPlatformGenerationChannelInUse)

	err = (&Channel{Id: route.ChannelID}).Delete()
	assert.ErrorIs(t, err, ErrPlatformGenerationChannelInUse)
	_, err = BatchDeleteChannels([]int{route.ChannelID})
	assert.ErrorIs(t, err, ErrPlatformGenerationChannelInUse)

	require.NoError(t, DB.Model(&Channel{}).Where("id = ?", route.ChannelID).
		Update("status", common.ChannelStatusManuallyDisabled).Error)
	_, err = DeleteDisabledChannel()
	assert.ErrorIs(t, err, ErrPlatformGenerationChannelInUse,
		"disabling must reject only new work; it must not make an active polling channel deletable")

	current, err := GetChannelById(route.ChannelID, true)
	require.NoError(t, err)
	assert.Equal(t, "provider-key-channel.guard", current.Key)

	released, err := ReleasePlatformGenerationProviderRoute(claim.JobID, claim.SubmissionToken)
	require.NoError(t, err)
	require.True(t, released)
	current.Key = "rotated-after-drain"
	require.NoError(t, current.Update())
	require.NoError(t, (&Channel{Id: route.ChannelID}).Delete())
	_, err = GetChannelById(route.ChannelID, true)
	assert.Error(t, err)
}

func TestPlatformGenerationActiveTaskAllowsNonTransportChannelMetadataUpdate(t *testing.T) {
	preparePlatformGenerationRouteTest(t)
	route := createPlatformGenerationRouteFixture(t, "channel.metadata", 10, 1)
	claim, err := ClaimPlatformGenerationProviderRoute(uuid.NewString(), route.Model, route.Mode)
	require.NoError(t, err)

	channel, err := GetChannelById(route.ChannelID, true)
	require.NoError(t, err)
	channel.Name = "renamed-with-active-task"
	require.NoError(t, channel.Update())
	updated, err := GetChannelById(route.ChannelID, true)
	require.NoError(t, err)
	assert.Equal(t, "renamed-with-active-task", updated.Name)

	released, err := ReleasePlatformGenerationProviderRoute(claim.JobID, claim.SubmissionToken)
	require.NoError(t, err)
	assert.True(t, released)
}
