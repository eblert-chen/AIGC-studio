package service

import (
	"testing"

	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestIsChannelTestSupportedRejectsVideoOnlySora(t *testing.T) {
	assert.False(t, IsChannelTestSupported(constant.ChannelTypeSora), "Sora only implements the OpenAI video endpoint and must never be probed through chat/completions")
	assert.True(t, IsChannelTestSupported(constant.ChannelTypeOpenAI), "ordinary OpenAI-compatible chat channels retain native connectivity tests")
}

func TestPlatformChannelControlProjectionCarriesSecretFreeManagedMarkerFence(t *testing.T) {
	managed, err := platformChannelControlChannel(model.Channel{
		Id: 880031, Name: "marker-owned", Type: constant.ChannelTypeOpenAI,
		Status: 1, Models: "provider-model", CreatedTime: 1,
		OtherInfo: `{"credential_late_provider":{"schema_version":1,"provider":"google","account_id":"primary","channel_id":880031,"public_model_ids":["provider-model"],"state":"route_test_ready"}}`,
	})
	require.NoError(t, err)
	assert.True(t, managed.ProviderOnboardingManaged)

	ordinary, err := platformChannelControlChannel(model.Channel{
		Id: 880032, Name: "ordinary", Type: constant.ChannelTypeOpenAI,
		Status: 1, Models: "provider-model", CreatedTime: 1,
		OtherInfo: `{"status_reason":"operator disabled"}`,
	})
	require.NoError(t, err)
	assert.False(t, ordinary.ProviderOnboardingManaged)
}

func TestBeginPlatformChannelControlTestRequiresExactReleasedRoute(t *testing.T) {
	route, publicModelID := setupPlatformModelReleaseEvidenceTest(t)

	tests := []struct {
		name          string
		publicModelID string
		routeID       string
	}{
		{name: "both missing"},
		{name: "public model only", publicModelID: publicModelID},
		{name: "route only", routeID: route.RouteID},
		{name: "blank public model", publicModelID: " ", routeID: route.RouteID},
		{name: "blank route", publicModelID: publicModelID, routeID: " "},
	}
	for index, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			request := platformBoundTestRequest("route-binding-required-000"+string(rune('1'+index)), test.publicModelID, test.routeID)
			_, execute, binding, err := BeginPlatformChannelControlTest(route.ChannelID, request, "route-binding-required-request")
			require.ErrorContains(t, err, "requires an exact")
			assert.False(t, execute)
			assert.Nil(t, binding)
		})
	}

	var count int64
	require.NoError(t, model.DB.Model(&model.PlatformChannelControlOperation{}).Count(&count).Error)
	assert.Zero(t, count, "a missing route binding must fail before an operation can authorize provider access")

	valid := platformBoundTestRequest("route-binding-required-valid-0001", publicModelID, route.RouteID)
	_, execute, binding, err := BeginPlatformChannelControlTest(route.ChannelID, valid, "route-binding-required-valid-request")
	require.NoError(t, err)
	assert.True(t, execute)
	require.NotNil(t, binding)
	assert.Equal(t, publicModelID, binding.PublicModelID)
	assert.Equal(t, route.RouteID, binding.RouteID)
}

func TestBeginPlatformChannelControlTestReplayResumesOnlyDurablySubmittedTask(t *testing.T) {
	t.Run("submitted resumes sticky provider task", func(t *testing.T) {
		route, publicModelID := setupPlatformModelReleaseEvidenceTest(t)
		request := platformBoundTestRequest("route-replay-submitted-0001", publicModelID, route.RouteID)

		_, execute, _, err := BeginPlatformChannelControlTest(route.ChannelID, request, "route-replay-submitted-request-1")
		require.NoError(t, err)
		require.True(t, execute)
		claimed, err := ClaimPlatformChannelControlTestSubmission(request.TenantID, request.OperationID)
		require.NoError(t, err)
		require.True(t, claimed)
		require.NoError(t, RecordPlatformChannelControlTestSubmitted(request.TenantID, request.OperationID, "provider-task-sticky-1"))
		require.NoError(t, RecordPlatformChannelControlTestBlocker(
			request.TenantID,
			request.OperationID,
			model.PlatformChannelControlErrorTestArtifact,
		))

		receipt, execute, binding, err := BeginPlatformChannelControlTest(route.ChannelID, request, "route-replay-submitted-request-2")
		require.NoError(t, err)
		assert.True(t, execute, "a submitted replay may resume polling, but must not submit again")
		assert.True(t, receipt.IdempotentReplay)
		assert.Equal(t, model.PlatformChannelTestSubmissionSubmitted, receipt.ProviderSubmissionState)
		assert.Equal(t, model.PlatformChannelControlErrorTestArtifact, receipt.ProviderBlockerCode)
		require.NotNil(t, binding)
		assert.Equal(t, "provider-task-sticky-1", binding.ProviderTaskID)
	})

	t.Run("unknown is receipt only", func(t *testing.T) {
		route, publicModelID := setupPlatformModelReleaseEvidenceTest(t)
		request := platformBoundTestRequest("route-replay-unknown-0001", publicModelID, route.RouteID)

		_, execute, _, err := BeginPlatformChannelControlTest(route.ChannelID, request, "route-replay-unknown-request-1")
		require.NoError(t, err)
		require.True(t, execute)
		claimed, err := ClaimPlatformChannelControlTestSubmission(request.TenantID, request.OperationID)
		require.NoError(t, err)
		require.True(t, claimed)

		receipt, execute, binding, err := BeginPlatformChannelControlTest(route.ChannelID, request, "route-replay-unknown-request-2")
		require.NoError(t, err)
		assert.False(t, execute, "submission_unknown can never authorize a second provider POST")
		assert.True(t, receipt.IdempotentReplay)
		assert.Equal(t, model.PlatformChannelControlOperationPending, receipt.State)
		assert.Equal(t, model.PlatformChannelTestSubmissionUnknown, receipt.ProviderSubmissionState)
		require.NotNil(t, binding)
		assert.Empty(t, binding.ProviderTaskID)
	})
}
