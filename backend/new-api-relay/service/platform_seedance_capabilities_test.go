package service

import (
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/generationrelease"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestSeedanceRouteCatalogRequiresReviewedModelRelease(t *testing.T) {
	model, found, err := generationprofile.ResolveSeedanceProviderModel("doubao-seedance-2-0-mini-260615")
	require.NoError(t, err)
	require.True(t, found)
	profile, found := generationprofile.Get(model.AdapterProfileID)
	require.True(t, found)
	route := PlatformRelayRouteDeclaration{
		RouteID: "ark-video-fixture", ProviderName: "volcengine-ark", AccountID: "fixture-only",
		ChannelID: 1, NativeChannelType: constant.ChannelTypeVolcEngine,
		KeyIndex: 0, KeyFingerprint: strings.Repeat("a", 64), ChannelClass: PlatformChannelClassOfficial,
		UpstreamModel: model.ProviderModelID, RPMLimit: 1, ActiveTaskLimit: 1,
		Capabilities: model.CompatibleCapability(),
	}
	for _, profileID := range []string{"", profile.ID} {
		route.CapabilityProfile = profileID
		raw, err := common.Marshal(map[string][]PlatformRelayRouteDeclaration{model.PublicModelID: {route}})
		require.NoError(t, err)
		_, _, err = parsePlatformRelayCapabilities("", string(raw), "development")
		assert.ErrorContains(t, err, "model_release", "omitting either profile/release is not a legacy bypass")
	}
	route.ModelRelease = &generationrelease.Release{
		APIVersion: generationrelease.APIVersion, Kind: generationrelease.Kind, ReleaseID: "seedance-mini-fixture-r1",
		PublicModelID: model.PublicModelID, ProviderModelID: model.ProviderModelID,
		AdapterProfileID: profile.ID, AdapterProfileRevision: profile.Revision,
		Capability: model.CompatibleCapability(),
		Audit:      generationrelease.Audit{CreatedAt: "2026-08-31T00:00:00Z", CreatedBy: "fixture-only", Reason: "offline contract test; not provider acceptance", SourceRef: model.OfficialSources[0]},
	}
	raw, err := common.Marshal(map[string][]PlatformRelayRouteDeclaration{model.PublicModelID: {route}})
	require.NoError(t, err)
	capabilities, routes, err := parsePlatformRelayCapabilities("", string(raw), "development")
	require.NoError(t, err)
	assert.ElementsMatch(t, []string{"480p", "720p"}, capabilities[model.PublicModelID].Modes["text_to_video"].Limits.Resolutions)
	require.Len(t, routes[model.PublicModelID], 1)
	assert.False(t, routes[model.PublicModelID][0].ProductionReady)
	assert.False(t, routes[model.PublicModelID][0].StagingReady)

	// The route cannot claim a resolution outside its reviewed model release,
	// even though the shared adapter profile implements that resolution.
	route.Capabilities = profile.Capability
	raw, err = common.Marshal(map[string][]PlatformRelayRouteDeclaration{model.PublicModelID: {route}})
	require.NoError(t, err)
	_, _, err = parsePlatformRelayCapabilities("", string(raw), "development")
	assert.ErrorContains(t, err, "expands generation model release")

	// Moving the same expansion into the model release is rejected even earlier.
	route.ModelRelease.Capability = profile.Capability
	raw, err = common.Marshal(map[string][]PlatformRelayRouteDeclaration{model.PublicModelID: {route}})
	require.NoError(t, err)
	_, _, err = parsePlatformRelayCapabilities("", string(raw), "development")
	assert.ErrorContains(t, err, "violates provider model contract")
}

func TestNonArkLegacyVideoRouteKeepsItsExistingDeclarationPath(t *testing.T) {
	route := PlatformRelayRouteDeclaration{
		NativeChannelType: constant.ChannelTypeVolcEngine,
		UpstreamModel:     "other-provider-video", Capabilities: platformCapabilityForTest([]int{5}, []string{"16:9"}, []string{"720p"}, false, 0),
	}
	assert.NoError(t, resolvePlatformGenerationRouteProfile("public-video", &route))
	assert.Empty(t, route.ResolvedCapabilityProfileID)
}
