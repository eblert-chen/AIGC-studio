package generationprofile

import (
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestGoogleVideoCatalogBindsExactSurfaceSpecificModels(t *testing.T) {
	models, err := GoogleVideoModelCatalog()
	require.NoError(t, err)
	require.Len(t, models, 3)
	for _, test := range []struct {
		channel, modelIndex                      int
		providerID, publicID, profileID, surface string
		published, blocked                       []string
	}{
		{constant.ChannelTypeGemini, 0, "gemini-omni-1.1-flash", "gemini-omni-1.1-flash", GoogleGeminiOmni11FlashBasicVideoV1, "gemini_api", []string{"text_to_video", "image_to_video"}, []string{"reference_to_video", "edit", "extend"}},
		{constant.ChannelTypeGemini, 1, "veo-3.1-generate-preview", "veo-3.1", GoogleGeminiVeo31VideoV1, "gemini_api", []string{"text_to_video", "image_to_video"}, []string{"interpolation", "reference_to_video", "extend"}},
		{constant.ChannelTypeGemini, 2, "veo-3.1-fast-generate-preview", "veo-3.1-fast", GoogleGeminiVeo31VideoV1, "gemini_api", []string{"text_to_video", "image_to_video"}, []string{"interpolation", "reference_to_video", "extend"}},
	} {
		t.Run(test.providerID+test.surface, func(t *testing.T) {
			model, found, err := ResolveGoogleVideoProviderModel(test.channel, test.providerID)
			require.NoError(t, err)
			require.True(t, found)
			assert.Equal(t, models[test.modelIndex].ProviderModelID, model.ProviderModelID)
			assert.Equal(t, test.publicID, model.PublicModelID)
			assert.Equal(t, test.profileID, model.AdapterProfileID)
			assert.Equal(t, test.surface, model.AccessSurface)
			assert.Equal(t, "acceptance_candidate", model.Lifecycle)
			assert.Equal(t, "route_acceptance_required", model.EvidenceStatus)
			assert.True(t, model.NewRoutesAllowed)
			assert.ElementsMatch(t, test.published, model.PublishedSemantics)
			assert.ElementsMatch(t, test.blocked, model.BlockedSemantics)
			profile, ok := Get(model.AdapterProfileID)
			require.True(t, ok)
			assert.NoError(t, ValidateGoogleVideoModelBinding(profile, model.ProviderModelID, model.CompatibleCapability()))
		})
	}
	for _, providerID := range []string{"veo-3.1-generate-001", "veo-3.1-fast-generate-001"} {
		_, found, err := ResolveGoogleVideoProviderModel(constant.ChannelTypeVertexAi, providerID)
		require.NoError(t, err)
		assert.False(t, found, "unqualified Vertex identities must stay outside the executable provider manifest")
	}
}

func TestGoogleVideoBindingRejectsAliasesAndCrossSurfaceIDs(t *testing.T) {
	gemini, ok := Get(GoogleGeminiVeo31VideoV1)
	require.True(t, ok)
	vertex, ok := Get(GoogleVertexVeo31VideoV1)
	require.True(t, ok)
	omni, ok := Get(GoogleGeminiOmni11FlashBasicVideoV1)
	require.True(t, ok)

	for _, providerID := range []string{"veo-3.1", "veo-3.1-generate-001", "veo-3.1-generate-preview ", "VEO-3.1-generate-preview"} {
		assert.Error(t, ValidateGoogleVideoModelBinding(gemini, providerID, gemini.Capability), providerID)
	}
	assert.Error(t, ValidateGoogleVideoModelBinding(vertex, "veo-3.1-generate-preview", vertex.Capability))
	assert.Error(t, ValidateGoogleVideoModelBinding(omni, "gemini-omni-flash-preview", omni.Capability))

	seedance, ok := Get(VolcengineArkVideoGenerationV1)
	require.True(t, ok)
	assert.ErrorContains(t, ValidateGoogleVideoModelBinding(seedance, "gemini-omni-1.1-flash", seedance.Capability), "requires a reviewed")
	assert.NoError(t, ValidateGoogleVideoModelBinding(seedance, "doubao-seedance-2-0-fast-260128", seedance.Capability))
}

func TestGoogleVideoCatalogRejectsAmbiguousOrFabricatedDeclarations(t *testing.T) {
	for _, test := range []struct {
		name   string
		mutate func(*googleVideoCatalogDocument)
	}{
		{"unknown schema", func(d *googleVideoCatalogDocument) { d.SchemaVersion = 2 }},
		{"invalid review date", func(d *googleVideoCatalogDocument) { d.ReviewedAt = "2026-02-30" }},
		{"empty models", func(d *googleVideoCatalogDocument) { d.Models = nil }},
		{"duplicate provider on surface", func(d *googleVideoCatalogDocument) {
			d.Models[2].ProviderModelID = d.Models[1].ProviderModelID
			d.Models[2].PublicModelID = d.Models[1].PublicModelID
		}},
		{"duplicate public on surface", func(d *googleVideoCatalogDocument) { d.Models[2].PublicModelID = d.Models[1].PublicModelID }},
		{"unknown exact model", func(d *googleVideoCatalogDocument) { d.Models[0].ProviderModelID = "gemini-omni-2.0-flash" }},
		{"cross surface channel", func(d *googleVideoCatalogDocument) { d.Models[1].NativeChannelType = constant.ChannelTypeVertexAi }},
		{"cross protocol profile", func(d *googleVideoCatalogDocument) { d.Models[0].AdapterProfileID = GoogleGeminiVeo31VideoV1 }},
		{"fabricated production evidence", func(d *googleVideoCatalogDocument) { d.Models[0].EvidenceStatus = "production_ready" }},
		{"third party source", func(d *googleVideoCatalogDocument) {
			d.Models[0].OfficialSources = []string{"https://example.test/google"}
		}},
		{"lookalike official source", func(d *googleVideoCatalogDocument) {
			d.Models[0].OfficialSources = []string{"https://ai.google.dev.example.test/gemini-api/docs/omni"}
		}},
		{"missing limitations", func(d *googleVideoCatalogDocument) { d.Models[0].Notes = nil }},
		{"publishes edit as generic mode", func(d *googleVideoCatalogDocument) {
			d.Models[0].PublishedSemantics = append(d.Models[0].PublishedSemantics, "edit")
		}},
		{"semantic omitted", func(d *googleVideoCatalogDocument) {
			d.Models[0].BlockedSemantics = []string{"reference_to_video", "edit"}
		}},
		{"semantic duplicated", func(d *googleVideoCatalogDocument) {
			d.Models[0].BlockedSemantics = append(d.Models[0].BlockedSemantics, "extend")
		}},
		{"unsupported video input", func(d *googleVideoCatalogDocument) {
			mode := d.Models[0].Capability.Modes["image_to_video"]
			mode.InputMediaTypes = []string{"image", "video"}
			mode.Limits.MaxVideos = 1
			d.Models[0].Capability.Modes["image_to_video"] = mode
		}},
		{"invalid short high resolution rectangle", func(d *googleVideoCatalogDocument) {
			mode := d.Models[1].Capability.Modes["text_to_video"]
			mode.Limits.DurationSeconds = []int{4, 8}
			d.Models[1].Capability.Modes["text_to_video"] = mode
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			var document googleVideoCatalogDocument
			require.NoError(t, common.Unmarshal(googleVideoCatalogJSON, &document))
			test.mutate(&document)
			raw, err := common.Marshal(document)
			require.NoError(t, err)
			_, err = decodeGoogleVideoCatalog(raw)
			assert.Error(t, err)
		})
	}
	for _, test := range []struct{ name, raw string }{
		{"unknown root field", strings.Replace(string(googleVideoCatalogJSON), `"reviewed_at":`, `"metadata": {}, "reviewed_at":`, 1)},
		{"duplicate root key", strings.Replace(string(googleVideoCatalogJSON), `"reviewed_at":`, `"schema_version": 1, "reviewed_at":`, 1)},
		{"unknown provider override", strings.Replace(string(googleVideoCatalogJSON), `"max_prompt_length":`, `"provider_parameters": {"task":"edit"}, "max_prompt_length":`, 1)},
		{"duplicate nested key", strings.Replace(string(googleVideoCatalogJSON), `"max_prompt_length":`, `"max_images": 15, "max_prompt_length":`, 1)},
		{"trailing document", string(googleVideoCatalogJSON) + `{}`},
	} {
		t.Run(test.name, func(t *testing.T) {
			_, err := decodeGoogleVideoCatalog([]byte(test.raw))
			assert.Error(t, err)
		})
	}
}

func TestGoogleVideoCatalogExportsIndependentCopies(t *testing.T) {
	model, found, err := ResolveGoogleVideoProviderModel(constant.ChannelTypeGemini, "gemini-omni-1.1-flash")
	require.NoError(t, err)
	require.True(t, found)
	model.OfficialSources[0] = "https://untrusted.example.test"
	model.PublishedSemantics[0] = "edit"
	delete(model.Capability.Modes, "text_to_video")

	fresh, found, err := ResolveGoogleVideoProviderModel(constant.ChannelTypeGemini, "gemini-omni-1.1-flash")
	require.NoError(t, err)
	require.True(t, found)
	assert.NotEqual(t, model.OfficialSources[0], fresh.OfficialSources[0])
	assert.Equal(t, "text_to_video", fresh.PublishedSemantics[0])
	assert.Contains(t, fresh.Capability.Modes, "text_to_video")
}
