package service

import (
	"testing"

	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/stretchr/testify/require"
)

func TestPlatformGenerationProfileSetAllowsHeterogeneousFailoverRoutes(t *testing.T) {
	profile, ok := generationprofile.Get(generationprofile.Seedream50TextToImageV1)
	require.True(t, ok)
	profiled := PlatformRelayRouteDeclaration{
		ResolvedCapabilityProfileID:       profile.ID,
		ResolvedCapabilityProfileRevision: profile.Revision,
	}
	legacyProfileless := PlatformRelayRouteDeclaration{}

	for name, routes := range map[string][]PlatformRelayRouteDeclaration{
		"profile then profileless": {profiled, legacyProfileless},
		"profileless then profile": {legacyProfileless, profiled},
	} {
		t.Run(name, func(t *testing.T) {
			_, found, err := platformGenerationProfileForRoutes(routes)
			require.NoError(t, err)
			require.False(t, found, "heterogeneous routes select their executable profile only after durable assignment")
		})
	}

	resolved, found, err := platformGenerationProfileForRoutes([]PlatformRelayRouteDeclaration{profiled, profiled})
	require.NoError(t, err)
	require.True(t, found)
	require.Equal(t, profile.ID, resolved.ID)

	_, found, err = platformGenerationProfileForRoutes([]PlatformRelayRouteDeclaration{legacyProfileless, legacyProfileless})
	require.NoError(t, err)
	require.False(t, found)
}
