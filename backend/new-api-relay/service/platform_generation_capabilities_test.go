package service

import (
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/dto"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestIntersectPlatformCapabilitiesUsesFailoverSafeValues(t *testing.T) {
	first := platformCapabilityForTest([]int{5, 10}, []string{"16:9", "9:16"}, []string{"720p", "1080p"}, true, 2)
	second := platformCapabilityForTest([]int{5}, []string{"16:9"}, []string{"720p"}, false, 1)
	routes := []PlatformRelayRouteDeclaration{
		{RouteID: "route-a", Capabilities: first},
		{RouteID: "route-b", Capabilities: second},
	}

	capability, err := intersectPlatformCapabilities("video-model", routes)
	require.NoError(t, err)
	mode := capability.Modes["text_to_video"]
	assert.False(t, mode.SupportsFace)
	assert.Equal(t, 1, mode.Limits.MaxImages)
	assert.Equal(t, []int{5}, mode.Limits.DurationSeconds)
	assert.Equal(t, []string{"16:9"}, mode.Limits.AspectRatios)
	assert.Equal(t, []string{"720p"}, mode.Limits.Resolutions)
}

func TestProductionCapabilityConfigRejectsMockRouteIdentity(t *testing.T) {
	_ = newPlatformRouteAcceptanceFixture(t)
	raw := `{"video-model":[{"route_id":"route-a","provider_name":"provider-a","account_id":"account-a","channel_id":1,"key_index":0,"key_fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","channel_class":"official","upstream_model":"provider-model","production_ready":false,"rpm_limit":10,"active_task_limit":2,"capabilities":{"schema_version":1,"modes":{"text_to_video":{"input_media_types":["image"],"supports_face":false,"required_resource_keys":[],"limits":{"max_prompt_length":1000,"max_images":1,"max_videos":0,"max_audio":0,"duration_seconds":[5],"aspect_ratios":["16:9"],"resolutions":["720p"],"output_counts":[1]}}}}}]}`
	raw = strings.Replace(raw, `"provider_name":"provider-a"`, `"provider_name":"mock-video"`, 1)
	raw = strings.Replace(raw, `"production_ready":false`, `"production_ready":true`, 1)

	_, _, err := parsePlatformRelayCapabilities("", raw, "production")
	require.ErrorContains(t, err, "mock identity")
}

func TestIntersectPlatformCapabilitiesRejectsEmptySafeMode(t *testing.T) {
	first := platformCapabilityForTest([]int{5}, []string{"16:9"}, []string{"720p"}, false, 1)
	second := platformCapabilityForTest([]int{10}, []string{"9:16"}, []string{"1080p"}, false, 1)

	_, err := intersectPlatformCapabilities("video-model", []PlatformRelayRouteDeclaration{
		{RouteID: "route-a", Capabilities: first},
		{RouteID: "route-b", Capabilities: second},
	})
	require.Error(t, err)
}

func TestProductionCapabilityConfigRequiresReadyRoutes(t *testing.T) {
	_ = newPlatformRouteAcceptanceFixture(t)
	raw := `{"video-model":[{"route_id":"route-a","provider_name":"provider-a","account_id":"account-a","channel_id":1,"key_index":0,"key_fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","channel_class":"official","upstream_model":"provider-model","production_ready":false,"rpm_limit":10,"active_task_limit":2,"capabilities":{"schema_version":1,"modes":{"text_to_video":{"input_media_types":["image"],"supports_face":false,"required_resource_keys":[],"limits":{"max_prompt_length":1000,"max_images":1,"max_videos":0,"max_audio":0,"duration_seconds":[5],"aspect_ratios":["16:9"],"resolutions":["720p"],"output_counts":[1]}}}}}]}`

	_, _, err := parsePlatformRelayCapabilities("", raw, "production")
	require.Error(t, err)
	assert.Contains(t, err.Error(), "signed acceptance evidence")
}

func TestStagingCapabilityConfigRequiresExplicitStagingAcceptance(t *testing.T) {
	fixture := newPlatformRouteAcceptanceFixture(t)
	unsigned := platformRouteJSONForTest(t, fixture.modelID, fixture.route)
	_, _, err := parsePlatformRelayCapabilities("", unsigned, "staging")
	require.ErrorContains(t, err, "signed acceptance evidence")

	signed := fixture.signedRoute(t, "staging", fixture.route, "11111111-2222-4333-8444-555555555555", fixture.now.Add(-time.Minute), fixture.now.Add(time.Hour))
	_, routes, err := parsePlatformRelayCapabilities("", platformRouteJSONForTest(t, fixture.modelID, signed), "staging")
	require.NoError(t, err)
	require.Len(t, routes[fixture.modelID], 1)
	assert.True(t, routes[fixture.modelID][0].StagingReady)
	assert.False(t, routes[fixture.modelID][0].ProductionReady)

	_, _, err = parsePlatformRelayCapabilities("", platformRouteJSONForTest(t, fixture.modelID, signed), "production")
	require.ErrorContains(t, err, "exact route and capability declaration")
}

func TestProductionReadinessDoesNotRequireStagingDeclaration(t *testing.T) {
	fixture := newPlatformRouteAcceptanceFixture(t)
	signed := fixture.signedRoute(t, "production", fixture.route, "11111111-2222-4333-8444-555555555555", fixture.now.Add(-time.Minute), fixture.now.Add(time.Hour))
	_, routes, err := parsePlatformRelayCapabilities("", platformRouteJSONForTest(t, fixture.modelID, signed), "production")
	require.NoError(t, err)
	assert.False(t, routes[fixture.modelID][0].StagingReady)
	assert.True(t, routes[fixture.modelID][0].ProductionReady)
}

func TestDevelopmentRouteRulesRemainUnchanged(t *testing.T) {
	raw := `{"video-model":[{"route_id":"mock-route","provider_name":"mock-provider","account_id":"mock-account","channel_id":1,"key_index":0,"key_fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","channel_class":"official","upstream_model":"mock-model","rpm_limit":10,"active_task_limit":2,"capabilities":{"schema_version":1,"modes":{"text_to_video":{"input_media_types":["image"],"supports_face":false,"required_resource_keys":[],"limits":{"max_prompt_length":1000,"max_images":1,"max_videos":0,"max_audio":0,"duration_seconds":[5],"aspect_ratios":["16:9"],"resolutions":["720p"],"output_counts":[1]}}}}}]}`

	_, _, err := parsePlatformRelayCapabilities("", raw, "development")
	require.NoError(t, err)
}

func TestCapabilityConfigRejectsUnknownEnvironmentInsteadOfFallingBack(t *testing.T) {
	_, _, err := parsePlatformRelayCapabilities(`{}`, "", "stagin")
	require.ErrorContains(t, err, "environment")

	_, _, err = parsePlatformRelayCapabilities(`{}`, "", "staging")
	require.ErrorContains(t, err, "staging requires RELAY_COMPAT_MODEL_ROUTES_JSON")
}

func TestPlatformCapabilityConfigRejectsUnknownFields(t *testing.T) {
	raw := `{"video-model":{"schema_version":1,"unknown_capability_field":true,"modes":{"text_to_video":{"input_media_types":[],"supports_face":false,"required_resource_keys":[],"limits":{"max_prompt_length":1000,"max_images":0,"max_videos":0,"max_audio":0,"duration_seconds":[5],"aspect_ratios":["16:9"],"resolutions":["720p"],"output_counts":[1]}}}}}`

	_, _, err := parsePlatformRelayCapabilities(raw, "", "development")
	require.Error(t, err)
	assert.Contains(t, err.Error(), "unknown_capability_field")
}

func TestPlatformNativeTaskBridgeRejectsCapabilitiesItCannotPreserve(t *testing.T) {
	base := platformCapabilityForTest([]int{5}, []string{"16:9"}, []string{"720p"}, false, 1)
	require.NoError(t, validatePlatformNativeTaskBridgeCapability("video-model", base))

	face := platformCapabilityForTest([]int{5}, []string{"16:9"}, []string{"720p"}, true, 1)
	require.ErrorContains(t, validatePlatformNativeTaskBridgeCapability("video-model", face), "face controls")

	multiple := platformCapabilityForTest([]int{5}, []string{"16:9"}, []string{"720p"}, false, 1)
	mode := multiple.Modes["text_to_video"]
	mode.Limits.OutputCounts = []int{1, 2}
	multiple.Modes["text_to_video"] = mode
	require.ErrorContains(t, validatePlatformNativeTaskBridgeCapability("video-model", multiple), "exactly one output")

	image := platformCapabilityForTest([]int{5}, []string{"16:9"}, []string{"720p"}, false, 1)
	image.Modes["text_to_image"] = image.Modes["text_to_video"]
	delete(image.Modes, "text_to_video")
	require.ErrorContains(t, validatePlatformNativeTaskBridgeCapability("image-model", image), "not supported")
}

func platformCapabilityForTest(durations []int, aspectRatios []string, resolutions []string, supportsFace bool, maxImages int) dto.PlatformGenerationCapabilities {
	return dto.PlatformGenerationCapabilities{
		SchemaVersion: 1,
		Modes: map[string]dto.PlatformModeCapability{
			"text_to_video": {
				InputMediaTypes:      []string{"image"},
				SupportsFace:         supportsFace,
				RequiredResourceKeys: []string{},
				Limits: dto.PlatformCapabilityLimits{
					MaxPromptLength: 1000,
					MaxImages:       maxImages,
					DurationSeconds: durations,
					AspectRatios:    aspectRatios,
					Resolutions:     resolutions,
					OutputCounts:    []int{1},
				},
			},
		},
	}
}
