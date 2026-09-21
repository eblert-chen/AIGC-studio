package generationrelease

import (
	"testing"
	"time"

	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestSeedanceReleaseBindsReviewedProviderCapabilityBeforeRoutePublication(t *testing.T) {
	models, err := generationprofile.SeedanceModelCatalog()
	require.NoError(t, err)
	for _, model := range models {
		if len(model.Capability.Modes) == 0 {
			continue
		}
		t.Run(model.PublicModelID, func(t *testing.T) {
			profile, ok := generationprofile.Get(model.AdapterProfileID)
			require.True(t, ok)
			release := Release{
				APIVersion: APIVersion, Kind: Kind, ReleaseID: model.PublicModelID + "-r1",
				PublicModelID: model.PublicModelID, ProviderModelID: model.ProviderModelID,
				AdapterProfileID: profile.ID, AdapterProfileRevision: profile.Revision,
				Capability: model.CompatibleCapability(),
				Audit:      Audit{CreatedAt: "2026-08-31T00:00:00Z", CreatedBy: "offline-test", Reason: "fixture only; not provider acceptance", SourceRef: model.OfficialSources[0]},
			}
			assert.NoError(t, release.Validate(profile))
			assert.NoError(t, release.ValidateRouteNarrowing(profile, release.Capability))
			assert.ErrorContains(t, release.VerifyAttestation(nil, time.Date(2026, 8, 31, 0, 0, 0, 0, time.UTC)), "requires a signed attestation")
			if model.PublicModelID == "seedance-2.0-mini" || model.PublicModelID == "seedance-2.0-fast" || model.PublicModelID == "seedance-1.5-pro" || model.PublicModelID == "seedance-1.0-pro-fast" {
				// These models do not inherit the reusable adapter's full ceiling.
				release.Capability = profile.Capability
				assert.ErrorContains(t, release.Validate(profile), "violates provider model contract")
			}
		})
	}
}
