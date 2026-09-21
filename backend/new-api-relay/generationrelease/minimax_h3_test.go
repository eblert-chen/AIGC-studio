package generationrelease

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestMiniMaxH3ReleaseBindsReviewedCapabilityWithoutClaimingAcceptance(t *testing.T) {
	models, err := generationprofile.MiniMaxH3ModelCatalog()
	require.NoError(t, err)
	require.Len(t, models, 2)
	for _, model := range models {
		t.Run(model.ProviderModelID, func(t *testing.T) {
			release, profile := miniMaxH3TestRelease(t, model.ProviderModelID)
			assert.NoError(t, release.Validate(profile))
			assert.NoError(t, release.ValidateRouteNarrowing(profile, release.Capability))
			assert.ErrorContains(t, release.VerifyAttestation(nil, time.Date(2026, 8, 31, 0, 0, 0, 0, time.UTC)), "requires a signed attestation")
			raw, err := common.Marshal(release)
			require.NoError(t, err)
			decoded, err := DecodeStrict(raw)
			require.NoError(t, err)
			assert.NoError(t, decoded.Validate(profile))

			narrowed := model.CompatibleCapability()
			mode := narrowed.Modes["text_to_video"]
			mode.Limits.DurationSeconds = []int{5}
			mode.Limits.Resolutions = []string{"768p"}
			mode.RequiredResourceKeys = []string{"feature.video_generation"}
			narrowed.Modes["text_to_video"] = mode
			assert.NoError(t, release.ValidateRouteNarrowing(profile, narrowed))
			mode.Limits.Resolutions = []string{"1080p"}
			narrowed.Modes["text_to_video"] = mode
			assert.ErrorContains(t, release.ValidateRouteNarrowing(profile, narrowed), "expands generation model release")
		})
	}
}

func TestMiniMaxH3SignedReleaseCannotSubstituteUnknownModelOrMaxProfile(t *testing.T) {
	for _, test := range []struct{ name, providerID string }{
		{"unknown official version", "MiniMax-H3-Future"},
		{"public alias is not provider ID", "minimax-h3"},
		{"legacy Hailuo is not H3", "MiniMax-Hailuo-2.3"},
		{"Max cannot inherit H3 reference ceiling", "MiniMax-H3-Max"},
	} {
		t.Run(test.name, func(t *testing.T) {
			release, profile := miniMaxH3TestRelease(t, "MiniMax-H3")
			release.ProviderModelID = test.providerID
			seed := sha256.Sum256([]byte("offline MiniMax H3 release boundary fixture"))
			privateKey := ed25519.NewKeyFromSeed(seed[:])
			attestation := Attestation{
				Algorithm: Algorithm, KeyID: "h3-offline-test", SignedAt: "2026-08-31T00:00:00Z", NotAfter: "2026-09-30T00:00:00Z",
			}
			payload, err := release.SigningPayload(attestation)
			require.NoError(t, err)
			attestation.Signature = base64.StdEncoding.EncodeToString(ed25519.Sign(privateKey, payload))
			release.Attestation = &attestation
			require.NoError(t, release.VerifyAttestation(
				map[string]ed25519.PublicKey{"h3-offline-test": privateKey.Public().(ed25519.PublicKey)},
				time.Date(2026, 8, 31, 1, 0, 0, 0, time.UTC),
			))
			assert.ErrorContains(t, release.Validate(profile), "violates provider model contract", "a valid signature is not permission to invent provider capability")
		})
	}
}

func TestMiniMaxH3IdentityCannotBypassBindingThroughAnUnrelatedProfile(t *testing.T) {
	release, _ := miniMaxH3TestRelease(t, "MiniMax-H3")
	ark, ok := generationprofile.Get(generationprofile.VolcengineArkImageGenerationV1)
	require.True(t, ok)
	release.AdapterProfileID = ark.ID
	release.AdapterProfileRevision = ark.Revision
	release.Capability = ark.Capability
	assert.ErrorContains(t, release.Validate(ark), "requires a reviewed V2 video profile")

	maxRelease, maxProfile := miniMaxH3TestRelease(t, "MiniMax-H3-Max")
	h3Release, _ := miniMaxH3TestRelease(t, "MiniMax-H3")
	maxRelease.Capability = h3Release.Capability
	assert.ErrorContains(t, maxRelease.Validate(maxProfile), "expands adapter profile")
}

func miniMaxH3TestRelease(t *testing.T, providerModelID string) (Release, generationprofile.Profile) {
	t.Helper()
	model, found, err := generationprofile.ResolveMiniMaxH3ProviderModel(providerModelID)
	require.NoError(t, err)
	require.True(t, found)
	profile, ok := generationprofile.Get(model.AdapterProfileID)
	require.True(t, ok)
	return Release{
		APIVersion: APIVersion, Kind: Kind, ReleaseID: model.PublicModelID + "-offline-r1",
		PublicModelID: model.PublicModelID, ProviderModelID: model.ProviderModelID,
		AdapterProfileID: profile.ID, AdapterProfileRevision: profile.Revision,
		Capability: model.CompatibleCapability(),
		Audit: Audit{
			CreatedAt: "2026-08-31T00:00:00Z", CreatedBy: "offline-test",
			Reason: "synthetic signature and capability test; not paid provider acceptance", SourceRef: model.OfficialSources[0],
		},
	}, profile
}
