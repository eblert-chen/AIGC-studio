package service

import (
	"bytes"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

type platformRouteAcceptanceFixture struct {
	modelID    string
	keyID      string
	privateKey ed25519.PrivateKey
	provenance PlatformRelayBuildProvenance
	now        time.Time
	route      PlatformRelayRouteDeclaration
}

func newPlatformRouteAcceptanceFixture(t *testing.T) platformRouteAcceptanceFixture {
	t.Helper()
	seed := bytes.Repeat([]byte{0x42}, ed25519.SeedSize)
	privateKey := ed25519.NewKeyFromSeed(seed)
	publicKey := privateKey.Public().(ed25519.PublicKey)
	keyID := "release-test-2026"
	encodedKeys := map[string]string{keyID: base64.StdEncoding.EncodeToString(publicKey)}
	keysJSON, err := json.Marshal(encodedKeys)
	require.NoError(t, err)
	canonicalKeys, err := platformRelayCanonicalJSON(encodedKeys)
	require.NoError(t, err)
	trustDigest := fmt.Sprintf("sha256:%x", sha256.Sum256(canonicalKeys))

	previousTrustDigest := platformRelayCompiledRouteAcceptanceKeysSHA256
	previousNow := platformRouteAcceptanceNow
	t.Cleanup(func() {
		platformRelayCompiledRouteAcceptanceKeysSHA256 = previousTrustDigest
		platformRouteAcceptanceNow = previousNow
	})
	platformRelayCompiledRouteAcceptanceKeysSHA256 = trustDigest
	now := time.Date(2026, 8, 15, 12, 0, 0, 0, time.UTC)
	platformRouteAcceptanceNow = func() time.Time { return now }
	t.Setenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_PUBLIC_KEYS_JSON", string(keysJSON))
	t.Setenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEY", "")
	t.Setenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEYS_JSON", "")
	t.Setenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_SIGNING_KEY", "")
	t.Setenv("RELAY_COMPAT_SOURCE_REVISION", strings.Repeat("b", 40))
	t.Setenv("RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256", "sha256:"+strings.Repeat("d", 64))
	t.Setenv("RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT", "123")
	t.Setenv("RELAY_COMPAT_IMAGE_DIGEST", "sha256:"+strings.Repeat("c", 64))

	return platformRouteAcceptanceFixture{
		modelID:    "video-model",
		keyID:      keyID,
		privateKey: privateKey,
		provenance: GetPlatformRelayBuildProvenance(),
		now:        now,
		route: PlatformRelayRouteDeclaration{
			RouteID:           "route-a",
			ProviderName:      "provider-a",
			AccountID:         "account-a",
			ChannelID:         17,
			NativeChannelType: constant.ChannelTypeKling,
			KeyIndex:          0,
			KeyFingerprint:    strings.Repeat("a", 64),
			ChannelClass:      PlatformChannelClassOfficial,
			UpstreamModel:     "provider-video-v1",
			RPMLimit:          10,
			ActiveTaskLimit:   2,
			Capabilities:      platformCapabilityForTest([]int{5}, []string{"16:9"}, []string{"720p"}, false, 1),
		},
	}
}

func (fixture platformRouteAcceptanceFixture) signedRoute(
	t *testing.T,
	environment string,
	route PlatformRelayRouteDeclaration,
	acceptanceID string,
	notBefore time.Time,
	notAfter time.Time,
) PlatformRelayRouteDeclaration {
	t.Helper()
	manifest, err := BuildPlatformRouteAcceptanceManifest(
		fixture.modelID,
		&route,
		acceptanceID,
		fixture.keyID,
		environment,
		fixture.provenance,
		notBefore,
		notAfter,
	)
	require.NoError(t, err)
	payload, err := PlatformRouteAcceptanceSignaturePayload(manifest)
	require.NoError(t, err)
	route.Acceptance = &PlatformRouteAcceptanceEvidence{
		Manifest:  manifest,
		Signature: base64.StdEncoding.EncodeToString(ed25519.Sign(fixture.privateKey, payload)),
	}
	return route
}

func platformRouteJSONForTest(t *testing.T, modelID string, routes ...PlatformRelayRouteDeclaration) string {
	t.Helper()
	encoded, err := json.Marshal(map[string][]PlatformRelayRouteDeclaration{modelID: routes})
	require.NoError(t, err)
	return string(encoded)
}

func TestSecureRouteReadinessIsDerivedOnlyFromVerifiedEvidence(t *testing.T) {
	fixture := newPlatformRouteAcceptanceFixture(t)
	signed := fixture.signedRoute(
		t,
		"production",
		fixture.route,
		uuid.MustParse("11111111-2222-4333-8444-555555555555").String(),
		fixture.now.Add(-time.Minute),
		fixture.now.Add(time.Hour),
	)

	_, routes, err := parsePlatformRelayCapabilities("", platformRouteJSONForTest(t, fixture.modelID, signed), "production")
	require.NoError(t, err)
	verified := routes[fixture.modelID][0]
	assert.True(t, verified.ProductionReady)
	assert.False(t, verified.StagingReady)
	assert.Regexp(t, platformRelaySnapshotDigestPattern, verified.AcceptanceDigest)
	assert.Equal(t, fixture.now.Add(time.Hour), verified.AcceptanceNotAfter)

	audit, expiry, err := buildPlatformRouteAcceptanceAudit("production", routes)
	require.NoError(t, err)
	assert.Equal(t, verified.AcceptanceNotAfter, expiry)
	require.Len(t, audit.Routes, 1)
	assert.Equal(t, verified.AcceptanceDigest, audit.Routes[0].EvidenceSHA256)
	serializedAudit, err := json.Marshal(audit)
	require.NoError(t, err)
	assert.NotContains(t, string(serializedAudit), signed.Acceptance.Signature)
}

func TestSecureRouteRejectsLegacySelfCertifiedReadiness(t *testing.T) {
	fixture := newPlatformRouteAcceptanceFixture(t)
	legacy := fixture.route
	legacy.ProductionReady = true

	_, _, err := parsePlatformRelayCapabilities("", platformRouteJSONForTest(t, fixture.modelID, legacy), "production")
	require.ErrorContains(t, err, "signed acceptance evidence")
}

func TestSecureRouteAcceptanceRejectsRouteAndCapabilityTampering(t *testing.T) {
	fixture := newPlatformRouteAcceptanceFixture(t)
	signed := fixture.signedRoute(t, "production", fixture.route, uuid.NewString(), fixture.now.Add(-time.Minute), fixture.now.Add(time.Hour))
	raw := platformRouteJSONForTest(t, fixture.modelID, signed)

	tamperedLimit := strings.Replace(raw, `"rpm_limit":10`, `"rpm_limit":11`, 1)
	_, _, err := parsePlatformRelayCapabilities("", tamperedLimit, "production")
	require.ErrorContains(t, err, "exact route and capability declaration")

	tamperedCapability := strings.Replace(raw, `"duration_seconds":[5]`, `"duration_seconds":[10]`, 1)
	_, _, err = parsePlatformRelayCapabilities("", tamperedCapability, "production")
	require.ErrorContains(t, err, "exact route and capability declaration")
}

func TestSecureRouteAcceptanceCannotReplayAcrossEnvironmentOrRoute(t *testing.T) {
	fixture := newPlatformRouteAcceptanceFixture(t)
	signed := fixture.signedRoute(t, "staging", fixture.route, uuid.NewString(), fixture.now.Add(-time.Minute), fixture.now.Add(time.Hour))
	raw := platformRouteJSONForTest(t, fixture.modelID, signed)

	_, _, err := parsePlatformRelayCapabilities("", raw, "production")
	require.ErrorContains(t, err, "exact route and capability declaration")

	replayedRoute := strings.Replace(raw, `"route_id":"route-a"`, `"route_id":"route-b"`, 1)
	_, _, err = parsePlatformRelayCapabilities("", replayedRoute, "staging")
	require.ErrorContains(t, err, "exact route and capability declaration")
}

func TestSecureRouteAcceptanceRejectsWrongImageAndExpiredReplay(t *testing.T) {
	fixture := newPlatformRouteAcceptanceFixture(t)
	signed := fixture.signedRoute(t, "production", fixture.route, uuid.NewString(), fixture.now.Add(-time.Minute), fixture.now.Add(time.Hour))
	raw := platformRouteJSONForTest(t, fixture.modelID, signed)

	t.Setenv("RELAY_COMPAT_IMAGE_DIGEST", "sha256:"+strings.Repeat("e", 64))
	_, _, err := parsePlatformRelayCapabilities("", raw, "production")
	require.ErrorContains(t, err, "running source and image")

	t.Setenv("RELAY_COMPAT_IMAGE_DIGEST", fixture.provenance.ImageDigest)
	expired := fixture.signedRoute(t, "production", fixture.route, uuid.NewString(), fixture.now.Add(-2*time.Hour), fixture.now.Add(-time.Hour))
	_, _, err = parsePlatformRelayCapabilities("", platformRouteJSONForTest(t, fixture.modelID, expired), "production")
	require.ErrorContains(t, err, "expired and cannot be replayed")
}

func TestSecureRouteAcceptanceRejectsSignatureAndAcceptanceIDReplay(t *testing.T) {
	fixture := newPlatformRouteAcceptanceFixture(t)
	signed := fixture.signedRoute(t, "production", fixture.route, uuid.NewString(), fixture.now.Add(-time.Minute), fixture.now.Add(time.Hour))
	signed.Acceptance.Signature = base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0x7f}, ed25519.SignatureSize))
	_, _, err := parsePlatformRelayCapabilities("", platformRouteJSONForTest(t, fixture.modelID, signed), "production")
	require.ErrorContains(t, err, "signature verification failed")

	sharedID := uuid.NewString()
	first := fixture.signedRoute(t, "production", fixture.route, sharedID, fixture.now.Add(-time.Minute), fixture.now.Add(time.Hour))
	secondRoute := fixture.route
	secondRoute.RouteID = "route-b"
	second := fixture.signedRoute(t, "production", secondRoute, sharedID, fixture.now.Add(-time.Minute), fixture.now.Add(time.Hour))
	_, _, err = parsePlatformRelayCapabilities("", platformRouteJSONForTest(t, fixture.modelID, first, second), "production")
	require.ErrorContains(t, err, "reuses acceptance_id")
}

func TestSecureRouteAcceptanceRejectsPrivateSigningMaterialInRuntime(t *testing.T) {
	fixture := newPlatformRouteAcceptanceFixture(t)
	t.Setenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEY", "must-not-be-mounted")

	_, _, err := parsePlatformRelayCapabilities("", platformRouteJSONForTest(t, fixture.modelID, fixture.route), "production")
	require.ErrorContains(t, err, "must never be present")
}

func TestSecureRouteAcceptanceCacheExpiresWithoutEnvironmentChange(t *testing.T) {
	fixture := newPlatformRouteAcceptanceFixture(t)
	signed := fixture.signedRoute(t, "production", fixture.route, uuid.NewString(), fixture.now.Add(-time.Minute), fixture.now.Add(time.Hour))
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "production")
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", fmt.Sprintf(
		`{"platform":{"tenant_id":"00000000-0000-4000-8000-000000000001","api_key":%q,"upstream_token":%q}}`,
		platformRelayRuntimeSecretsTestValue("route-cache-api"),
		platformRelayRuntimeSecretsTestToken("route-cache-upstream"),
	))
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", "")
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", platformRouteJSONForTest(t, fixture.modelID, signed))

	_, err := GetPlatformRelayModelCatalog()
	require.NoError(t, err)
	platformRouteAcceptanceNow = func() time.Time { return fixture.now.Add(2 * time.Hour) }
	_, err = GetPlatformRelayModelCatalog()
	require.ErrorContains(t, err, "expired and cannot be replayed")
}

func TestRouteSyncBindsAcceptedNativeAdapterToActualChannelType(t *testing.T) {
	truncate(t)
	key := "native-channel-key"
	route := PlatformRelayRouteDeclaration{
		RouteID: "adapter-bound-route", ProviderName: "provider-a", AccountID: "account-a",
		ChannelID: 8181, NativeChannelType: constant.ChannelTypeKling, KeyIndex: 0,
		KeyFingerprint: fmt.Sprintf("%x", common.Sha256Raw([]byte(key))), ChannelClass: PlatformChannelClassOfficial,
		UpstreamModel: "provider-video-v1", RPMLimit: 10, ActiveTaskLimit: 2,
		Capabilities: platformCapabilityForTest([]int{5}, []string{"16:9"}, []string{"720p"}, false, 1),
	}
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", `{"platform":{"tenant_id":"00000000-0000-4000-8000-000000000001","api_key":"dev-api-key","upstream_token":"dev-token"}}`)
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", "")
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", platformRouteJSONForTest(t, "video-model", route))
	require.NoError(t, model.DB.Create(&model.Channel{
		Id: route.ChannelID, Type: constant.ChannelTypeGemini, Key: key, Status: common.ChannelStatusEnabled,
		Name: "wrong-adapter", CreatedTime: 1, Models: "video-model", Group: "default",
	}).Error)

	err := SyncPlatformGenerationProviderRoutes()
	require.ErrorContains(t, err, "does not match accepted adapter type")

	require.NoError(t, model.DB.Model(&model.Channel{}).Where("id = ?", route.ChannelID).
		UpdateColumn("type", constant.ChannelTypeKling).Error)
	require.NoError(t, SyncPlatformGenerationProviderRoutes())
	var persisted model.PlatformGenerationProviderRoute
	require.NoError(t, model.DB.Where(
		"route_key = ? AND mode = ?",
		route.RouteID,
		"text_to_video",
	).First(&persisted).Error)
	assert.Equal(t, route.NativeChannelType, persisted.AcceptedChannelType)
}
