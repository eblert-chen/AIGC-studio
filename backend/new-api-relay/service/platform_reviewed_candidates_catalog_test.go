package service

import (
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/generationrelease"
	"github.com/QuantumNous/new-api/model"
	"github.com/glebarez/sqlite"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func resetPlatformRelayCatalogCacheForCandidateTest(t *testing.T) {
	t.Helper()
	platformRelayConfigCache.Lock()
	previousSnapshot := platformRelayConfigCache.snapshot
	previousLastObserved := platformRelayConfigCache.lastObserved
	previousClockFailed := platformRelayConfigCache.clockFailed
	previousGeneration := platformRelayConfigCache.generation
	platformRelayConfigCache.snapshot = platformRelayConfigSnapshot{}
	platformRelayConfigCache.lastObserved = time.Time{}
	platformRelayConfigCache.clockFailed = false
	platformRelayConfigCache.generation = 0
	platformRelayConfigCache.Unlock()
	resetPlatformRelayModelCatalogReadinessForTest()
	t.Cleanup(func() {
		platformRelayConfigCache.Lock()
		platformRelayConfigCache.snapshot = previousSnapshot
		platformRelayConfigCache.lastObserved = previousLastObserved
		platformRelayConfigCache.clockFailed = previousClockFailed
		platformRelayConfigCache.generation = previousGeneration
		platformRelayConfigCache.Unlock()
		resetPlatformRelayModelCatalogReadinessForTest()
	})
}

func installReviewedCandidateCatalogEnvironment(t *testing.T) {
	t.Helper()
	resetPlatformRelayCatalogCacheForCandidateTest(t)
	for _, variable := range []string{"APP_ENV", "DEPLOYMENT_ENV", "ENVIRONMENT"} {
		t.Setenv(variable, "")
	}
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", `{}`)
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", "")
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", "")
	t.Setenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_PUBLIC_KEYS_JSON", "")
	t.Setenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEY", "")
	t.Setenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEYS_JSON", "")
	t.Setenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_SIGNING_KEY", "")
}

func TestPlatformRelayCatalogIncludesReviewedCandidatesWithoutRouteEvidence(t *testing.T) {
	installReviewedCandidateCatalogEnvironment(t)

	catalog, err := GetPlatformRelayModelCatalog()
	require.NoError(t, err)
	require.Len(t, catalog.Data, 11)
	ids := make([]string, 0, len(catalog.Data))
	for _, resource := range catalog.Data {
		ids = append(ids, resource.ID)
		routes, err := GetPlatformRelayRoutes(resource.ID, "text_to_video")
		require.NoError(t, err)
		assert.Empty(t, routes, resource.ID+" must remain a route-less candidate")
	}
	assert.True(t, sort.StringsAreSorted(ids))
	assert.Contains(t, ids, "minimax-h3")
	assert.Contains(t, ids, "gemini-omni-1.1-flash")
	assert.Contains(t, ids, "veo-3.1")
	assert.Contains(t, ids, "seedance-2.5")
}

func TestConfiguredRouteWinsCandidateIDDeduplication(t *testing.T) {
	installReviewedCandidateCatalogEnvironment(t)
	providerModel, found, err := generationprofile.ResolveSeedanceProviderModel("doubao-seedance-2-0-260128")
	require.NoError(t, err)
	require.True(t, found)
	profile, found := generationprofile.Get(providerModel.AdapterProfileID)
	require.True(t, found)

	narrowed := providerModel.CompatibleCapability()
	for modeName := range narrowed.Modes {
		if modeName != "text_to_video" {
			delete(narrowed.Modes, modeName)
		}
	}
	mode := narrowed.Modes["text_to_video"]
	mode.Limits.Resolutions = mode.Limits.Resolutions[:1]
	narrowed.Modes["text_to_video"] = mode
	route := PlatformRelayRouteDeclaration{
		RouteID: "reviewed-candidate-priority", ProviderName: "volcengine-ark", AccountID: "fixture-only",
		ChannelID: 42, NativeChannelType: constant.ChannelTypeVolcEngine,
		KeyIndex: 0, KeyFingerprint: strings.Repeat("a", 64), ChannelClass: PlatformChannelClassOfficial,
		UpstreamModel: providerModel.ProviderModelID, RPMLimit: 1, ActiveTaskLimit: 1,
		CapabilityProfile: profile.ID, Capabilities: narrowed,
		ModelRelease: &generationrelease.Release{
			APIVersion: generationrelease.APIVersion, Kind: generationrelease.Kind,
			ReleaseID: "reviewed-candidate-priority-r1", PublicModelID: providerModel.PublicModelID,
			ProviderModelID: providerModel.ProviderModelID, AdapterProfileID: profile.ID,
			AdapterProfileRevision: profile.Revision, Capability: providerModel.CompatibleCapability(),
			Audit: generationrelease.Audit{
				CreatedAt: "2026-09-01T00:00:00Z", CreatedBy: "catalog-union-test",
				Reason: "offline catalog precedence test; not provider acceptance", SourceRef: providerModel.OfficialSources[0],
			},
		},
	}
	raw, err := common.Marshal(map[string][]PlatformRelayRouteDeclaration{providerModel.PublicModelID: {route}})
	require.NoError(t, err)
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", string(raw))

	catalog, err := GetPlatformRelayModelCatalog()
	require.NoError(t, err)
	require.Len(t, catalog.Data, 11, "an exact route id replaces, rather than duplicates, its candidate")
	var configuredCount int
	for _, resource := range catalog.Data {
		if resource.ID == providerModel.PublicModelID {
			configuredCount++
			assert.Equal(t, normalizePlatformCapability(narrowed), resource.Capabilities)
		}
	}
	assert.Equal(t, 1, configuredCount)
	routes, err := GetPlatformRelayRoutes(providerModel.PublicModelID, "text_to_video")
	require.NoError(t, err)
	require.Len(t, routes, 1)
	assert.Equal(t, route.RouteID, routes[0].RouteID)
	veoRoutes, err := GetPlatformRelayRoutes("veo-3.1", "text_to_video")
	require.NoError(t, err)
	assert.Empty(t, veoRoutes)
}

func TestNativeMetadataMaterializerIncludesConfiguredAndCandidateCatalogModels(t *testing.T) {
	installReviewedCandidateCatalogEnvironment(t)
	profile, ok := generationprofile.Get(generationprofile.Seedream50TextToImageV1)
	require.True(t, ok)
	route := PlatformRelayRouteDeclaration{
		RouteID: "configured-seedream-5", ProviderName: "volcengine-ark", AccountID: "fixture-only",
		ChannelID: 7, NativeChannelType: constant.ChannelTypeVolcEngine,
		KeyIndex: 0, KeyFingerprint: strings.Repeat("b", 64), ChannelClass: PlatformChannelClassOfficial,
		UpstreamModel: constant.PlatformGenerationArkSeedream50Model, RPMLimit: 1, ActiveTaskLimit: 1,
		Capabilities: profile.Capability, CapabilityProfile: profile.ID,
	}
	raw, err := common.Marshal(map[string][]PlatformRelayRouteDeclaration{
		constant.PlatformGenerationPublicSeedream50Model: {route},
	})
	require.NoError(t, err)
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", string(raw))

	previousDB := model.DB
	db, err := gorm.Open(sqlite.Open("file:platform_relay_model_metadata_materializer?mode=memory&cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, db.AutoMigrate(&model.Model{}, &model.Channel{}, &model.Ability{}, &model.Vendor{}))
	model.DB = db
	t.Cleanup(func() { model.DB = previousDB })

	inserted, err := MaterializePlatformRelayModelMetadata()
	require.NoError(t, err)
	assert.Equal(t, 12, inserted, "11 reviewed candidates plus the configured seedream-5 route model")
	for _, modelID := range []string{"seedream-5", "seedance-2.5", "minimax-h3", "gemini-omni-1.1-flash", "veo-3.1"} {
		var metadata model.Model
		require.NoError(t, db.Where("model_name = ?", modelID).First(&metadata).Error)
		assert.Zero(t, metadata.Status)
		assert.Zero(t, metadata.SyncOfficial)
		assert.Zero(t, metadata.VendorID)
	}
	for name, target := range map[string]any{
		"channels": &model.Channel{}, "abilities": &model.Ability{}, "vendors": &model.Vendor{},
	} {
		var count int64
		require.NoError(t, db.Model(target).Count(&count).Error)
		assert.Zero(t, count, name+" must remain real-control-plane only")
	}
	repeated, err := MaterializePlatformRelayModelMetadata()
	require.NoError(t, err)
	assert.Zero(t, repeated)
}
