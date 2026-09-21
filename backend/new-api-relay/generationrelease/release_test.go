package generationrelease

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/stretchr/testify/require"
)

func validRelease(t *testing.T, providerModel string) (Release, generationprofile.Profile) {
	t.Helper()
	profile, ok := generationprofile.Get(generationprofile.VolcengineArkImageGenerationV1)
	require.True(t, ok)
	return Release{
		APIVersion:             APIVersion,
		Kind:                   Kind,
		ReleaseID:              "seedream-public-r1",
		PublicModelID:          "seedream-public",
		ProviderModelID:        providerModel,
		AdapterProfileID:       profile.ID,
		AdapterProfileRevision: profile.Revision,
		LegacyPublicAliases:    []string{"seedream-public-old"},
		Capability:             profile.Capability,
		Audit: Audit{
			CreatedAt: "2026-08-28T00:00:00Z",
			CreatedBy: "release-bot",
			Reason:    "staging provider acceptance passed",
			SourceRef: "acceptance/seedream/2026-08-28",
		},
	}, profile
}

func signRelease(t *testing.T, release Release, privateKey ed25519.PrivateKey) Release {
	t.Helper()
	attestation := Attestation{
		Algorithm: Algorithm,
		KeyID:     "release-key-2026",
		SignedAt:  "2026-08-28T00:00:00Z",
		NotAfter:  "2026-09-28T00:00:00Z",
	}
	payload, err := release.SigningPayload(attestation)
	require.NoError(t, err)
	attestation.Signature = base64.StdEncoding.EncodeToString(ed25519.Sign(privateKey, payload))
	release.Attestation = &attestation
	return release
}

func TestReusableProfileAcceptsNewProviderModelWithoutCodeRegistration(t *testing.T) {
	first, profile := validRelease(t, "doubao-seedream-5-0-260128")
	second, _ := validRelease(t, "doubao-seedream-5-1-unknown-future-id")
	second.ReleaseID = "seedream-public-r2"

	require.NoError(t, first.Validate(profile))
	require.NoError(t, second.Validate(profile), "a new provider_model_id using an accepted protocol profile must be data-only")
	require.NotEqual(t, first.ProviderModelID, second.ProviderModelID)
	firstCapability, err := first.CapabilityRevision()
	require.NoError(t, err)
	secondCapability, err := second.CapabilityRevision()
	require.NoError(t, err)
	require.Equal(t, firstCapability, secondCapability, "provider identity changes must not change capability revision")
	firstBinding, err := first.BindingRevision()
	require.NoError(t, err)
	secondBinding, err := second.BindingRevision()
	require.NoError(t, err)
	require.NotEqual(t, firstBinding, secondBinding, "provider identity remains auditable as a separate binding release")
}

func TestReleaseAndRouteCannotExpandProfile(t *testing.T) {
	release, profile := validRelease(t, "doubao-seedream-5-0-260128")
	expanded := release
	expanded.Capability = generationprofile.NormalizeCapability(release.Capability)
	mode := expanded.Capability.Modes["text_to_image"]
	mode.Limits.Resolutions = []string{"2048x2048", "4096x4096"}
	expanded.Capability.Modes["text_to_image"] = mode
	require.ErrorContains(t, expanded.Validate(profile), "expands adapter profile")

	narrowRelease := release
	narrowRelease.Capability = generationprofile.NormalizeCapability(release.Capability)
	mode = narrowRelease.Capability.Modes["text_to_image"]
	mode.Limits.MaxPromptLength = 500
	narrowRelease.Capability.Modes["text_to_image"] = mode
	require.NoError(t, narrowRelease.Validate(profile))
	routeExpansion := generationprofile.NormalizeCapability(narrowRelease.Capability)
	mode = routeExpansion.Modes["text_to_image"]
	mode.Limits.MaxPromptLength = 501
	routeExpansion.Modes["text_to_image"] = mode
	require.ErrorContains(t, narrowRelease.ValidateRouteNarrowing(profile, routeExpansion), "expands generation model release")
}

func TestReleaseAttestationIsDeterministicAndFailsClosed(t *testing.T) {
	release, profile := validRelease(t, "doubao-seedream-5-0-260128")
	seed := sha256.Sum256([]byte("test model release signing seed"))
	privateKey := ed25519.NewKeyFromSeed(seed[:])
	publicKey := privateKey.Public().(ed25519.PublicKey)
	signed := signRelease(t, release, privateKey)
	require.NoError(t, signed.Validate(profile))
	require.NoError(t, signed.VerifyAttestation(map[string]ed25519.PublicKey{"release-key-2026": publicKey}, time.Date(2026, 8, 29, 0, 0, 0, 0, time.UTC)))

	tampered := signed
	tampered.ProviderModelID = "doubao-seedream-tampered"
	require.ErrorContains(t, tampered.VerifyAttestation(map[string]ed25519.PublicKey{"release-key-2026": publicKey}, time.Date(2026, 8, 29, 0, 0, 0, 0, time.UTC)), "verification failed")
	require.ErrorContains(t, signed.VerifyAttestation(nil, time.Date(2026, 8, 29, 0, 0, 0, 0, time.UTC)), "key is unavailable")
	require.ErrorContains(t, signed.VerifyAttestation(map[string]ed25519.PublicKey{"release-key-2026": publicKey}, time.Date(2026, 9, 28, 0, 0, 0, 0, time.UTC)), "validity window")

	tooLong := signed
	tooLong.Attestation = &Attestation{
		Algorithm: Algorithm,
		KeyID:     "release-key-2026",
		SignedAt:  "2026-08-28T00:00:00Z",
		NotAfter:  "2027-08-29T00:00:01Z",
		Signature: signed.Attestation.Signature,
	}
	require.ErrorContains(t, tooLong.Validate(profile), "window")
}

func TestDecodeStrictRejectsUnknownTrailingAndIncompleteDocuments(t *testing.T) {
	release, profile := validRelease(t, "doubao-seedream-5-0-260128")
	raw, err := json.Marshal(release)
	require.NoError(t, err)
	decoded, err := DecodeStrict(raw)
	require.NoError(t, err)
	require.NoError(t, decoded.Validate(profile))

	var document map[string]any
	require.NoError(t, json.Unmarshal(raw, &document))
	document["route_weight"] = 99
	unknown, err := json.Marshal(document)
	require.NoError(t, err)
	_, err = DecodeStrict(unknown)
	require.ErrorContains(t, err, "unknown field")
	_, err = DecodeStrict(append(raw, []byte(` {}`)...))
	require.ErrorContains(t, err, "trailing")

	incomplete := release
	incomplete.Capability = dto.PlatformGenerationCapabilities{SchemaVersion: 1, Modes: map[string]dto.PlatformModeCapability{}}
	require.ErrorContains(t, incomplete.Validate(profile), "at least one mode")
}
