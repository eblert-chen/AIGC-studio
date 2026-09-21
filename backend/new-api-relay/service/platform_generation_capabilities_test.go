package service

import (
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
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

func TestIntersectPlatformCapabilitiesUsesModeEligibleRouteUnion(t *testing.T) {
	textOnly := platformCapabilityForTest([]int{5, 10}, []string{"16:9"}, []string{"720p"}, false, 0)
	imageOnly := platformCapabilityForTest([]int{5}, []string{"16:9", "9:16"}, []string{"720p", "1080p"}, false, 1)
	imageMode := imageOnly.Modes["text_to_video"]
	delete(imageOnly.Modes, "text_to_video")
	imageOnly.Modes["image_to_video"] = imageMode

	capability, err := intersectPlatformCapabilities("multimode-model", []PlatformRelayRouteDeclaration{
		{RouteID: "text-route", Capabilities: textOnly},
		{RouteID: "image-route", Capabilities: imageOnly},
	})
	require.NoError(t, err)
	require.Contains(t, capability.Modes, "text_to_video")
	require.Contains(t, capability.Modes, "image_to_video")
	assert.Equal(t, []int{5, 10}, capability.Modes["text_to_video"].Limits.DurationSeconds)
	// Public capability snapshots are canonicalized for deterministic hashing;
	// enumerations are therefore lexical rather than provider-display order.
	assert.Equal(t, []string{"1080p", "720p"}, capability.Modes["image_to_video"].Limits.Resolutions)
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

func TestCapabilityConfigRejectsDuplicateJSONKeysBeforeNormalization(t *testing.T) {
	_, _, err := parsePlatformRelayCapabilities(
		`{"duplicate-model":{"schema_version":1,"modes":{}},"duplicate-model":{"schema_version":1,"modes":{}}}`,
		"",
		"development",
	)
	require.ErrorContains(t, err, "duplicate")

	_, _, err = parsePlatformRelayCapabilities(
		"",
		`{"duplicate-model":[],"duplicate-model":[]}`,
		"development",
	)
	require.ErrorContains(t, err, "duplicate")
}

func TestSeedreamRoutePublishesCanonicalModelAndKeepsLegacyAliasExplicit(t *testing.T) {
	profile, ok := generationprofile.Get(generationprofile.Seedream50TextToImageV1)
	require.True(t, ok)
	baseRoute := PlatformRelayRouteDeclaration{
		RouteID:           "seedream-official-account-a",
		ProviderName:      "volcengine-ark",
		AccountID:         "ark-account-a",
		ChannelID:         1,
		NativeChannelType: constant.ChannelTypeVolcEngine,
		KeyIndex:          0,
		KeyFingerprint:    strings.Repeat("a", 64),
		ChannelClass:      PlatformChannelClassOfficial,
		UpstreamModel:     constant.PlatformGenerationArkSeedream50Model,
		RPMLimit:          10,
		ActiveTaskLimit:   2,
		Capabilities:      profile.Capability,
		CapabilityProfile: profile.ID,
	}

	canonicalRaw, err := common.Marshal(map[string][]PlatformRelayRouteDeclaration{
		constant.PlatformGenerationPublicSeedream50Model: {baseRoute},
	})
	require.NoError(t, err)
	capabilities, routes, err := parsePlatformRelayCapabilities("", string(canonicalRaw), "development")
	require.NoError(t, err)
	require.Contains(t, capabilities, "seedream-5")
	require.NotContains(t, capabilities, "seedream-5-lite")
	require.Len(t, routes["seedream-5"], 1)
	resolved := routes["seedream-5"][0]
	require.Equal(t, profile.ID, resolved.ResolvedCapabilityProfileID)
	require.Equal(t, profile.Revision, resolved.ResolvedCapabilityProfileRevision)
	require.Equal(t, "seedream-5", resolved.CanonicalPublicModel)
	require.False(t, resolved.DeprecatedPublicAlias)

	legacyRaw, err := common.Marshal(map[string][]PlatformRelayRouteDeclaration{
		constant.PlatformGenerationLegacySeedream50LitePublicAlias: {baseRoute},
	})
	require.NoError(t, err)
	legacyCapabilities, legacyRoutes, err := parsePlatformRelayCapabilities("", string(legacyRaw), "development")
	require.NoError(t, err)
	require.Contains(t, legacyCapabilities, "seedream-5-lite", "the legacy client model remains callable during migration")
	require.Equal(t, "seedream-5", legacyRoutes["seedream-5-lite"][0].CanonicalPublicModel)
	require.True(t, legacyRoutes["seedream-5-lite"][0].DeprecatedPublicAlias)
}

func TestSeedreamAccountRotationChangesRoutingReleaseNotCapabilityRevision(t *testing.T) {
	profile, ok := generationprofile.Get(generationprofile.Seedream50TextToImageV1)
	require.True(t, ok)
	first := PlatformRelayRouteDeclaration{
		RouteID:           "seedream-account-a",
		ProviderName:      "volcengine-ark",
		AccountID:         "ark-account-a",
		ChannelID:         1,
		NativeChannelType: constant.ChannelTypeVolcEngine,
		KeyIndex:          0,
		KeyFingerprint:    strings.Repeat("a", 64),
		ChannelClass:      PlatformChannelClassOfficial,
		UpstreamModel:     constant.PlatformGenerationArkSeedream50Model,
		RPMLimit:          10,
		ActiveTaskLimit:   2,
		Capabilities:      profile.Capability,
		CapabilityProfile: profile.ID,
	}
	rotated := first
	rotated.RouteID = "seedream-account-b"
	rotated.AccountID = "ark-account-b"
	rotated.ChannelID = 2
	rotated.KeyFingerprint = strings.Repeat("b", 64)

	parse := func(route PlatformRelayRouteDeclaration) (string, string) {
		raw, err := common.Marshal(map[string][]PlatformRelayRouteDeclaration{"seedream-5": {route}})
		require.NoError(t, err)
		capabilities, routes, err := parsePlatformRelayCapabilities("", string(raw), "development")
		require.NoError(t, err)
		canonical, err := platformRelayCanonicalJSON(capabilities["seedream-5"])
		require.NoError(t, err)
		capabilityRevision := fmt.Sprintf("sha256:%x", common.Sha256Raw(canonical))
		audit, _, err := buildPlatformRouteAcceptanceAudit("development", routes)
		require.NoError(t, err)
		return capabilityRevision, audit.RoutingReleaseSHA256
	}

	firstCapability, firstRoutingRelease := parse(first)
	rotatedCapability, rotatedRoutingRelease := parse(rotated)
	require.Equal(t, firstCapability, rotatedCapability, "equivalent accounts must not trigger customer capability reapproval")
	require.NotEqual(t, firstRoutingRelease, rotatedRoutingRelease, "route/key rotation must produce a new internal release identity")
}

func TestSeedreamRouteCannotExpandItsCodeReviewedProfile(t *testing.T) {
	profile, ok := generationprofile.Get(generationprofile.Seedream50TextToImageV1)
	require.True(t, ok)
	expanded := profile.Capability
	mode := expanded.Modes["text_to_image"]
	mode.InputMediaTypes = []string{"image"}
	mode.Limits.MaxImages = 1
	expanded.Modes["text_to_image"] = mode
	route := PlatformRelayRouteDeclaration{
		RouteID:           "seedream-expanded",
		ProviderName:      "volcengine-ark",
		AccountID:         "ark-account-a",
		ChannelID:         1,
		NativeChannelType: constant.ChannelTypeVolcEngine,
		KeyIndex:          0,
		KeyFingerprint:    strings.Repeat("a", 64),
		ChannelClass:      PlatformChannelClassOfficial,
		UpstreamModel:     constant.PlatformGenerationArkSeedream50Model,
		RPMLimit:          10,
		ActiveTaskLimit:   2,
		Capabilities:      expanded,
		CapabilityProfile: profile.ID,
	}
	raw, err := common.Marshal(map[string][]PlatformRelayRouteDeclaration{"seedream-5": {route}})
	require.NoError(t, err)
	_, _, err = parsePlatformRelayCapabilities("", string(raw), "development")
	require.ErrorContains(t, err, "exceeds profile")
}

func TestPlatformCapabilityConfigRejectsUnknownFields(t *testing.T) {
	raw := `{"video-model":{"schema_version":1,"unknown_capability_field":true,"modes":{"text_to_video":{"input_media_types":[],"supports_face":false,"required_resource_keys":[],"limits":{"max_prompt_length":1000,"max_images":0,"max_videos":0,"max_audio":0,"duration_seconds":[5],"aspect_ratios":["16:9"],"resolutions":["720p"],"output_counts":[1]}}}}}`

	_, _, err := parsePlatformRelayCapabilities(raw, "", "development")
	require.Error(t, err)
	assert.Contains(t, err.Error(), "unknown_capability_field")
}

func TestPlatformNativeTaskBridgeRejectsCapabilitiesItCannotPreserve(t *testing.T) {
	base := platformCapabilityForTest([]int{5}, []string{"16:9"}, []string{"720p"}, false, 1)
	require.NoError(t, validatePlatformNativeTaskBridgeCapability("video-model", PlatformRelayRouteDeclaration{Capabilities: base}))

	face := platformCapabilityForTest([]int{5}, []string{"16:9"}, []string{"720p"}, true, 1)
	require.ErrorContains(t, validatePlatformNativeTaskBridgeCapability("video-model", PlatformRelayRouteDeclaration{Capabilities: face}), "face controls")

	multiple := platformCapabilityForTest([]int{5}, []string{"16:9"}, []string{"720p"}, false, 1)
	mode := multiple.Modes["text_to_video"]
	mode.Limits.OutputCounts = []int{1, 2}
	multiple.Modes["text_to_video"] = mode
	require.ErrorContains(t, validatePlatformNativeTaskBridgeCapability("video-model", PlatformRelayRouteDeclaration{Capabilities: multiple}), "exactly one output")

	image := platformSeedreamCapabilityForTest()
	seedreamRoute := PlatformRelayRouteDeclaration{
		NativeChannelType: constant.ChannelTypeVolcEngine,
		UpstreamModel:     constant.PlatformGenerationArkSeedream50Model,
		Capabilities:      image,
	}
	require.NoError(t, validatePlatformNativeTaskBridgeCapability("image-model", seedreamRoute))

	mixedModes := platformSeedreamCapabilityForTest()
	mixedModes.Modes["text_to_video"] = platformCapabilityForTest(
		[]int{5},
		[]string{"16:9"},
		[]string{"720p"},
		false,
		0,
	).Modes["text_to_video"]
	seedreamRoute.Capabilities = mixedModes
	require.ErrorContains(
		t,
		validatePlatformNativeTaskBridgeCapability("image-model", seedreamRoute),
		"not implemented by profile",
	)

	seedreamRoute.Capabilities = platformCapabilityForTest([]int{5}, []string{"16:9"}, []string{"720p"}, false, 0)
	require.ErrorContains(
		t,
		validatePlatformNativeTaskBridgeCapability("image-model", seedreamRoute),
		"not implemented by profile",
	)

	wrongRoute := PlatformRelayRouteDeclaration{
		NativeChannelType: constant.ChannelTypeDoubaoVideo,
		UpstreamModel:     constant.PlatformGenerationArkSeedream50Model,
		Capabilities:      image,
	}
	require.ErrorContains(t, validatePlatformNativeTaskBridgeCapability("image-model", wrongRoute), "requires a registered generation capability profile")
	wrongRoute.NativeChannelType = constant.ChannelTypeVolcEngine
	wrongRoute.UpstreamModel = "doubao-seedream-5-0-lite-260128"
	require.ErrorContains(t, validatePlatformNativeTaskBridgeCapability("image-model", wrongRoute), "requires a registered generation capability profile")

	for name, mutate := range map[string]func(*dto.PlatformModeCapability){
		"input": func(mode *dto.PlatformModeCapability) {
			mode.InputMediaTypes = []string{"image"}
			mode.Limits.MaxImages = 1
		},
		"face":     func(mode *dto.PlatformModeCapability) { mode.SupportsFace = true },
		"duration": func(mode *dto.PlatformModeCapability) { mode.Limits.DurationSeconds = []int{5} },
		"ratio":    func(mode *dto.PlatformModeCapability) { mode.Limits.AspectRatios = []string{"16:9"} },
		"size":     func(mode *dto.PlatformModeCapability) { mode.Limits.Resolutions = []string{"2K"} },
		"count":    func(mode *dto.PlatformModeCapability) { mode.Limits.OutputCounts = []int{1, 2} },
	} {
		t.Run("rejects "+name, func(t *testing.T) {
			capability := platformSeedreamCapabilityForTest()
			mode := capability.Modes["text_to_image"]
			mutate(&mode)
			capability.Modes["text_to_image"] = mode
			err := validatePlatformNativeTaskBridgeCapability("image-model", PlatformRelayRouteDeclaration{
				NativeChannelType: constant.ChannelTypeVolcEngine,
				UpstreamModel:     constant.PlatformGenerationArkSeedream50Model,
				Capabilities:      capability,
			})
			require.Error(t, err)
		})
	}
}

func platformSeedreamCapabilityForTest() dto.PlatformGenerationCapabilities {
	return dto.PlatformGenerationCapabilities{
		SchemaVersion: 1,
		Modes: map[string]dto.PlatformModeCapability{
			"text_to_image": {
				InputMediaTypes:      []string{},
				SupportsFace:         false,
				RequiredResourceKeys: []string{},
				Limits: dto.PlatformCapabilityLimits{
					MaxPromptLength: 1000,
					DurationSeconds: []int{1},
					AspectRatios:    []string{"1:1"},
					Resolutions:     []string{constant.PlatformGenerationArkSeedream50LiteSize},
					OutputCounts:    []int{1},
				},
			},
		},
	}
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
