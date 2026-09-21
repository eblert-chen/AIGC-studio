package generationprofile

import (
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestSeedanceCatalogPublishesSevenExactCompatibleModels(t *testing.T) {
	models, err := SeedanceModelCatalog()
	require.NoError(t, err)
	current, newRoutes := 0, 0
	for _, model := range models {
		if len(model.Capability.Modes) == 0 {
			assert.False(t, model.NewRoutesAllowed)
			continue
		}
		current++
		if model.NewRoutesAllowed {
			newRoutes++
		}
		profile, ok := Get(model.AdapterProfileID)
		require.True(t, ok, model.ProviderModelID)
		assert.NoError(t, ValidateSeedanceModelBinding(profile, model.ProviderModelID, model.CompatibleCapability()))
		for _, mode := range model.Capability.Modes {
			assert.LessOrEqual(t, mode.Limits.MaxImages+mode.Limits.MaxVideos+mode.Limits.MaxAudio, 15)
			assert.Equal(t, []int{1}, mode.Limits.OutputCounts)
			assert.ElementsMatch(t, seedanceNumericAspectRatios, mode.Limits.AspectRatios)
			assert.False(t, mode.SupportsFace)
		}
	}
	assert.Equal(t, 7, current)
	assert.Equal(t, 6, newRoutes)

	for _, test := range []struct {
		id, public, profile      string
		minimum, maximum, images int
		resolutions              []string
		video                    bool
	}{
		{"doubao-seedance-2-5-260628", "seedance-2.5", VolcengineArkVideoGeneration30SReferenceV1, 4, 30, 9, []string{"480p", "720p", "1080p"}, true},
		{"doubao-seedance-2-0-260128", "seedance-2.0", VolcengineArkVideoGeneration4KV1, 4, 15, 9, []string{"480p", "720p", "1080p", "4k"}, true},
		{"doubao-seedance-2-0-fast-260128", "seedance-2.0-fast", VolcengineArkVideoGenerationV1, 4, 15, 9, []string{"480p", "720p"}, true},
		{"doubao-seedance-2-0-mini-260615", "seedance-2.0-mini", VolcengineArkVideoGenerationV1, 4, 15, 9, []string{"480p", "720p"}, true},
		{"doubao-seedance-1-5-pro-251215", "seedance-1.5-pro", VolcengineArkVideoGenerationV1, 4, 12, 2, []string{"480p", "720p", "1080p"}, false},
		{"doubao-seedance-1-0-pro-250528", "seedance-1.0-pro", VolcengineArkLegacyVideoGenerationV1, 2, 12, 2, []string{"480p", "720p", "1080p"}, false},
		{"doubao-seedance-1-0-pro-fast-251015", "seedance-1.0-pro-fast", VolcengineArkLegacyVideoGenerationV1, 2, 12, 1, []string{"480p", "720p", "1080p"}, false},
	} {
		t.Run(test.public, func(t *testing.T) {
			model, ok, err := ResolveSeedanceProviderModel(test.id)
			require.NoError(t, err)
			require.True(t, ok)
			assert.Equal(t, test.public, model.PublicModelID)
			assert.Equal(t, test.profile, model.AdapterProfileID)
			assert.Equal(t, integerRange(test.minimum, test.maximum), model.Capability.Modes["text_to_video"].Limits.DurationSeconds)
			assert.Equal(t, test.resolutions, model.Capability.Modes["text_to_video"].Limits.Resolutions)
			assert.Equal(t, test.images, model.Capability.Modes["image_to_video"].Limits.MaxImages)
			_, hasVideo := model.Capability.Modes["video_to_video"]
			assert.Equal(t, test.video, hasVideo)
		})
	}
}

func TestSeedanceLifecyclePreservesExistingOnlyAndHistoricalEvidence(t *testing.T) {
	model, ok, err := ResolveSeedanceProviderModel("doubao-seedance-1-5-pro-251215")
	require.NoError(t, err)
	require.True(t, ok)
	assert.False(t, model.NewRoutesAllowed)
	assert.Equal(t, "deprecated", model.Lifecycle)
	assert.Equal(t, "existing_routes_only", model.EvidenceStatus)
	assert.Equal(t, "2026-07-10T02:00:00Z", model.EOMAt)
	assert.Equal(t, "2026-09-21T06:00:00Z", model.EOSAt)
	assert.NoError(t, model.ValidateSubmissionAt(time.Date(2026, 9, 21, 5, 59, 59, 0, time.UTC)))
	assert.ErrorContains(t, model.ValidateSubmissionAt(time.Date(2026, 9, 21, 6, 0, 0, 0, time.UTC)), "end of service")

	for _, id := range []string{"doubao-seedance-1-0-lite-t2v-250428", "doubao-seedance-1-0-lite-i2v-250428", "doubao-seedance-1-0-pro-fast-250610"} {
		model, ok, err := ResolveSeedanceProviderModel(id)
		require.NoError(t, err)
		require.True(t, ok)
		assert.False(t, model.NewRoutesAllowed)
		assert.Empty(t, model.Capability.Modes)
		assert.Empty(t, model.AdapterProfileID)
		assert.Error(t, model.ValidateSubmissionAt(time.Date(2026, 8, 31, 0, 0, 0, 0, time.UTC)))
		if model.Lifecycle == "retired" {
			assert.Equal(t, "2026-05-11T06:00:00Z", model.EOSAt)
			assert.Contains(t, model.OfficialSources, "https://docs.volcengine.com/docs/82379/1350667")
		} else {
			assert.Equal(t, "official_model_id_unverified", model.EvidenceStatus)
			assert.NotEqual(t, "seedance-1.0-pro-fast", model.PublicModelID, "old version must not look like a routable alias")
		}
	}
}

func TestSeedanceBindingRejectsModelSpecificExpansionBeforePublication(t *testing.T) {
	for _, test := range []struct {
		id     string
		mutate func(*dto.PlatformGenerationCapabilities)
	}{
		{"doubao-seedance-2-0-mini-260615", func(cap *dto.PlatformGenerationCapabilities) {
			mode := cap.Modes["text_to_video"]
			mode.Limits.Resolutions = []string{"1080p"}
			cap.Modes["text_to_video"] = mode
		}},
		{"doubao-seedance-2-0-fast-260128", func(cap *dto.PlatformGenerationCapabilities) {
			mode := cap.Modes["text_to_video"]
			mode.Limits.Resolutions = []string{"1080p"}
			cap.Modes["text_to_video"] = mode
		}},
		{"doubao-seedance-1-0-pro-fast-251015", func(cap *dto.PlatformGenerationCapabilities) {
			mode := cap.Modes["image_to_video"]
			mode.Limits.MaxImages = 2
			cap.Modes["image_to_video"] = mode
		}},
		{"doubao-seedance-1-5-pro-251215", func(cap *dto.PlatformGenerationCapabilities) {
			mode := cap.Modes["text_to_video"]
			mode.Limits.DurationSeconds = []int{13}
			cap.Modes["text_to_video"] = mode
		}},
	} {
		t.Run(test.id, func(t *testing.T) {
			model, ok, err := ResolveSeedanceProviderModel(test.id)
			require.NoError(t, err)
			require.True(t, ok)
			profile, ok := Get(model.AdapterProfileID)
			require.True(t, ok)
			candidate := model.CompatibleCapability()
			test.mutate(&candidate)
			require.NoError(t, profile.ValidateNarrowing(candidate), "regression is narrower than the implementation ceiling but wider than this model")
			assert.ErrorContains(t, ValidateSeedanceModelBinding(profile, test.id, candidate), "exceeds reviewed Seedance provider model")
		})
	}
	model, ok, err := ResolveSeedanceProviderModel("doubao-seedance-2-0-260128")
	require.NoError(t, err)
	require.True(t, ok)
	oldProfile, ok := Get(VolcengineArkVideoGenerationV1)
	require.True(t, ok)
	assert.NoError(t, ValidateSeedanceModelBinding(oldProfile, model.ProviderModelID, oldProfile.Capability))
	assert.Error(t, ValidateSeedanceModelBinding(oldProfile, model.ProviderModelID, model.Capability), "4K requires its new immutable profile")
	assert.ErrorContains(t, ValidateSeedanceModelBinding(oldProfile, "doubao-seedance-future-unreviewed", oldProfile.Capability), "no reviewed adapter manifest")
}

func TestSeedanceCatalogRejectsAmbiguousOrUnreviewedDeclarations(t *testing.T) {
	for _, test := range []struct {
		name   string
		mutate func(*seedanceCatalogDocument)
	}{
		{"duplicate provider", func(doc *seedanceCatalogDocument) { doc.Models[1].ProviderModelID = doc.Models[0].ProviderModelID }},
		{"duplicate public name", func(doc *seedanceCatalogDocument) { doc.Models[1].PublicModelID = doc.Models[0].PublicModelID }},
		{"duplicate compatible profile", func(doc *seedanceCatalogDocument) {
			doc.Models[1].CompatibleAdapterProfileIDs = []string{VolcengineArkVideoGenerationV1, VolcengineArkVideoGenerationV1}
		}},
		{"wrong product source", func(doc *seedanceCatalogDocument) {
			doc.Models[0].OfficialSources = []string{"https://docs.volcengine.com/docs/6492/2389898"}
		}},
		{"auto editing semantics", func(doc *seedanceCatalogDocument) { doc.Models[0].OmniReferenceTaskType = "auto" }},
		{"deprecated new routes", func(doc *seedanceCatalogDocument) { doc.Models[4].NewRoutesAllowed = true }},
		{"historical executable ceiling", func(doc *seedanceCatalogDocument) { doc.Models[7].Capability = doc.Models[0].Capability }},
	} {
		t.Run(test.name, func(t *testing.T) {
			var document seedanceCatalogDocument
			require.NoError(t, common.Unmarshal(seedanceCatalogJSON, &document))
			test.mutate(&document)
			raw, err := common.Marshal(document)
			require.NoError(t, err)
			_, err = decodeSeedanceCatalog(raw)
			assert.Error(t, err)
		})
	}
	for _, raw := range []string{
		strings.Replace(string(seedanceCatalogJSON), `"reviewed_at":`, `"unknown": true, "reviewed_at":`, 1),
		strings.Replace(string(seedanceCatalogJSON), `"reviewed_at":`, `"schema_version": 1, "reviewed_at":`, 1),
		string(seedanceCatalogJSON) + `{}`,
	} {
		_, err := decodeSeedanceCatalog([]byte(raw))
		assert.Error(t, err)
	}
}

func TestSeedanceCatalogExportCannotMutateRuntimeCeiling(t *testing.T) {
	model, ok, err := ResolveSeedanceProviderModel("doubao-seedance-2-5-260628")
	require.NoError(t, err)
	require.True(t, ok)
	capability := model.CompatibleCapability()
	capability.Modes["video_to_video"].Limits.Resolutions[0] = "4k"
	model.OfficialSources[0] = "https://untrusted.example"
	delete(model.Capability.Modes, "text_to_video")
	fresh, ok, err := ResolveSeedanceProviderModel(model.ProviderModelID)
	require.NoError(t, err)
	require.True(t, ok)
	assert.NotContains(t, fresh.Capability.Modes["video_to_video"].Limits.Resolutions, "4k")
	assert.Contains(t, fresh.Capability.Modes, "text_to_video")
	assert.NotEqual(t, model.OfficialSources[0], fresh.OfficialSources[0])
}
