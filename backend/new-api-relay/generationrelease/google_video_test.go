package generationrelease

import (
	"testing"
	"time"

	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestGoogleVideoReleaseBindsExactModelAndAPISurface(t *testing.T) {
	models, err := generationprofile.GoogleVideoModelCatalog()
	require.NoError(t, err)
	for _, model := range models {
		t.Run(model.AccessSurface+"/"+model.ProviderModelID, func(t *testing.T) {
			profile, ok := generationprofile.Get(model.AdapterProfileID)
			require.True(t, ok)
			release := googleVideoTestRelease(model, profile)
			assert.NoError(t, release.Validate(profile))
			assert.NoError(t, release.ValidateRouteNarrowing(profile, release.Capability))
			assert.ErrorContains(t, release.VerifyAttestation(nil, time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)), "requires a signed attestation")
		})
	}
}

func TestGoogleVideoReleaseRejectsUnqualifiedVertexInventory(t *testing.T) {
	profile, ok := generationprofile.Get(generationprofile.GoogleVertexVeo31VideoV1)
	require.True(t, ok)
	release := Release{
		APIVersion: APIVersion, Kind: Kind,
		ReleaseID:              "veo-3.1-vertex-unqualified-r1",
		PublicModelID:          "veo-3.1",
		ProviderModelID:        "veo-3.1-generate-001",
		AdapterProfileID:       profile.ID,
		AdapterProfileRevision: profile.Revision,
		Capability:             profile.Capability,
		Audit: Audit{
			CreatedAt: "2026-09-01T00:00:00Z", CreatedBy: "offline-test",
			Reason: "negative contract test for an unqualified Vertex identity", SourceRef: "https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/veo/3-1-generate",
		},
	}
	assert.ErrorContains(t, release.Validate(profile), "no reviewed manifest")
}

func TestGoogleVideoReleaseRejectsCrossSurfaceAndSemanticExpansion(t *testing.T) {
	geminiModel, found, err := generationprofile.ResolveGoogleVideoProviderModel(constant.ChannelTypeGemini, "veo-3.1-generate-preview")
	require.NoError(t, err)
	require.True(t, found)
	geminiProfile, ok := generationprofile.Get(geminiModel.AdapterProfileID)
	require.True(t, ok)
	release := googleVideoTestRelease(geminiModel, geminiProfile)

	release.ProviderModelID = "veo-3.1-generate-001"
	assert.ErrorContains(t, release.Validate(geminiProfile), "no reviewed manifest")

	release = googleVideoTestRelease(geminiModel, geminiProfile)
	release.Capability = geminiModel.CompatibleCapability()
	mode := release.Capability.Modes["image_to_video"]
	mode.Limits.MaxImages = 2
	release.Capability.Modes["image_to_video"] = mode
	assert.ErrorContains(t, release.Validate(geminiProfile), "expands adapter profile")
}

func googleVideoTestRelease(model generationprofile.GoogleVideoProviderModel, profile generationprofile.Profile) Release {
	return Release{
		APIVersion: APIVersion, Kind: Kind,
		ReleaseID:              model.PublicModelID + "-" + model.AccessSurface + "-offline-r1",
		PublicModelID:          model.PublicModelID,
		ProviderModelID:        model.ProviderModelID,
		AdapterProfileID:       profile.ID,
		AdapterProfileRevision: profile.Revision,
		Capability:             model.CompatibleCapability(),
		Audit: Audit{
			CreatedAt: "2026-09-01T00:00:00Z", CreatedBy: "offline-test",
			Reason: "synthetic Google model binding test; not paid route acceptance", SourceRef: model.OfficialSources[0],
		},
	}
}
