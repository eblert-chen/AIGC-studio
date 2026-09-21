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

func TestMiniMaxH3RouteCatalogRequiresExactReviewedModelRelease(t *testing.T) {
	models, err := generationprofile.MiniMaxH3ModelCatalog()
	require.NoError(t, err)
	for _, providerModel := range models {
		t.Run(providerModel.PublicModelID, func(t *testing.T) {
			profile, found := generationprofile.Get(providerModel.AdapterProfileID)
			require.True(t, found)
			route := PlatformRelayRouteDeclaration{
				RouteID: "h3-contract-fixture", ProviderName: "minimax", AccountID: "fixture-only",
				ChannelID: 1, NativeChannelType: constant.ChannelTypeMiniMax,
				KeyIndex: 0, KeyFingerprint: strings.Repeat("a", 64), ChannelClass: PlatformChannelClassOfficial,
				UpstreamModel: providerModel.ProviderModelID, RPMLimit: 1, ActiveTaskLimit: 1,
				Capabilities: providerModel.CompatibleCapability(),
			}
			for _, profileID := range []string{"", profile.ID} {
				route.CapabilityProfile = profileID
				assert.ErrorContains(t, resolvePlatformGenerationRouteProfile(providerModel.PublicModelID, &route), "model_release")
			}
			route.ModelRelease = &generationrelease.Release{
				APIVersion: generationrelease.APIVersion, Kind: generationrelease.Kind, ReleaseID: "minimax-h3-fixture-r1",
				PublicModelID: providerModel.PublicModelID, ProviderModelID: providerModel.ProviderModelID,
				AdapterProfileID: profile.ID, AdapterProfileRevision: profile.Revision,
				Capability: providerModel.CompatibleCapability(),
				Audit: generationrelease.Audit{
					CreatedAt: "2026-08-31T00:00:00Z", CreatedBy: "fixture-only",
					Reason: "offline contract test; not provider acceptance", SourceRef: providerModel.OfficialSources[0],
				},
			}
			raw, err := common.Marshal(map[string][]PlatformRelayRouteDeclaration{providerModel.PublicModelID: {route}})
			require.NoError(t, err)
			capabilities, routes, err := parsePlatformRelayCapabilities("", string(raw), "development")
			require.NoError(t, err)
			assert.Equal(t, normalizePlatformCapability(providerModel.Capability), capabilities[providerModel.PublicModelID])
			require.Len(t, routes[providerModel.PublicModelID], 1)
			assert.False(t, routes[providerModel.PublicModelID][0].ProductionReady)
			assert.False(t, routes[providerModel.PublicModelID][0].StagingReady)
			assert.Equal(t, profile.ID, routes[providerModel.PublicModelID][0].ResolvedCapabilityProfileID)

			mode := route.Capabilities.Modes["text_to_video"]
			mode.Limits.Resolutions = append(mode.Limits.Resolutions, "4k")
			route.Capabilities.Modes["text_to_video"] = mode
			assert.Error(t, resolvePlatformGenerationRouteProfile(providerModel.PublicModelID, &route), "a route cannot widen the reviewed release")
		})
	}
}

func TestMiniMaxH3ReleaseCannotUseLegacyHailuoOrAnotherH3Model(t *testing.T) {
	profile, ok := generationprofile.Get(generationprofile.MiniMaxH3ReferenceVideoGenerationV1)
	require.True(t, ok)
	for _, modelID := range []string{"MiniMax-H3-Max", "MiniMax-Hailuo-2.3", "MiniMax-H3-Future"} {
		t.Run(modelID, func(t *testing.T) {
			route := PlatformRelayRouteDeclaration{
				NativeChannelType: constant.ChannelTypeMiniMax, UpstreamModel: modelID,
				CapabilityProfile: profile.ID, Capabilities: profile.Capability,
			}
			assert.Error(t, resolvePlatformGenerationRouteProfile("minimax-h3", &route))
		})
	}
	legacy := PlatformRelayRouteDeclaration{
		NativeChannelType: constant.ChannelTypeMiniMax,
		UpstreamModel:     "MiniMax-Hailuo-2.3", Capabilities: platformCapabilityForTest([]int{6}, []string{"16:9"}, []string{"768p"}, false, 0),
	}
	assert.NoError(t, resolvePlatformGenerationRouteProfile("legacy-hailuo", &legacy))
	assert.Empty(t, legacy.ResolvedCapabilityProfileID)
}
