package generationprofile

import (
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestMiniMaxH3CatalogBindsTwoExactModelsToTruthfulPublicSubsets(t *testing.T) {
	models, err := MiniMaxH3ModelCatalog()
	require.NoError(t, err)
	require.Len(t, models, 2)
	for _, test := range []struct {
		providerID, publicID, displayName, profileID string
		modes                                        []string
	}{
		{"MiniMax-H3", "minimax-h3", "MiniMax H3", MiniMaxH3ReferenceVideoGenerationV1, []string{"text_to_video", "image_to_video", "video_to_video"}},
		{"MiniMax-H3-Max", "minimax-h3-max", "MiniMax H3 Max", MiniMaxH3MaxTextToVideoGenerationV1, []string{"text_to_video"}},
	} {
		t.Run(test.providerID, func(t *testing.T) {
			model, found, err := ResolveMiniMaxH3ProviderModel(test.providerID)
			require.NoError(t, err)
			require.True(t, found)
			assert.Equal(t, test.publicID, model.PublicModelID)
			assert.Equal(t, test.displayName, model.DisplayName)
			assert.Equal(t, test.profileID, model.AdapterProfileID)
			assert.Equal(t, "acceptance_candidate", model.Lifecycle)
			assert.Equal(t, "route_acceptance_required", model.EvidenceStatus)
			assert.True(t, model.NewRoutesAllowed)
			assert.NotEmpty(t, model.OfficialSources)
			assert.NotEmpty(t, model.Notes)
			assert.Len(t, model.Capability.Modes, len(test.modes))
			profile, ok := Get(model.AdapterProfileID)
			require.True(t, ok)
			assert.NoError(t, ValidateMiniMaxH3ModelBinding(profile, model.ProviderModelID, model.CompatibleCapability()))
			for _, modeName := range test.modes {
				assert.Contains(t, model.Capability.Modes, modeName)
			}
			assert.Equal(t, 0, model.Capability.Modes["text_to_video"].Limits.MaxImages)
			assert.Equal(t, 0, model.Capability.Modes["text_to_video"].Limits.MaxVideos)
			assert.Equal(t, 0, model.Capability.Modes["text_to_video"].Limits.MaxAudio)
		})
	}
	assert.Equal(t, "reference_image", models[0].ImageRole, "fixed-ratio I2V is reference generation, not a first-frame promise")
	assert.Empty(t, models[1].ImageRole)
}

func TestMiniMaxH3BindingRejectsUnknownModelsAndCrossProfilePrivilegeExpansion(t *testing.T) {
	h3, ok := Get(MiniMaxH3ReferenceVideoGenerationV1)
	require.True(t, ok)
	max, ok := Get(MiniMaxH3MaxTextToVideoGenerationV1)
	require.True(t, ok)
	for _, providerID := range []string{"minimax-h3", "MiniMax-H3 ", "MiniMax-H3-Future", "MiniMax-Hailuo-2.3", "minimax3"} {
		t.Run(providerID, func(t *testing.T) {
			_, found, err := ResolveMiniMaxH3ProviderModel(providerID)
			require.NoError(t, err)
			assert.False(t, found)
			assert.ErrorContains(t, ValidateMiniMaxH3ModelBinding(h3, providerID, h3.Capability), "no reviewed adapter manifest")
		})
	}
	assert.ErrorContains(t, ValidateMiniMaxH3ModelBinding(h3, "MiniMax-H3-Max", h3.Capability), "does not match provider model")
	assert.ErrorContains(t, ValidateMiniMaxH3ModelBinding(max, "MiniMax-H3", max.Capability), "does not match provider model")
	_, _, err := Resolve(h3.ID, constant.ChannelTypeMiniMax, "MiniMax-H3-Max")
	assert.ErrorContains(t, err, "does not match provider model")
	_, _, err = Resolve(h3.ID, constant.ChannelTypeMiniMax, "MiniMax-H3-Future")
	assert.ErrorContains(t, err, "no reviewed adapter manifest")
	_, _, err = Resolve(h3.ID, constant.ChannelTypeVolcEngine, "MiniMax-H3")
	assert.ErrorContains(t, err, "native channel type")
	_, found, err := Resolve("", constant.ChannelTypeMiniMax, "MiniMax-H3")
	require.NoError(t, err)
	assert.False(t, found, "H3 never takes an inferred legacy Hailuo profile")

	ark, ok := Get(VolcengineArkImageGenerationV1)
	require.True(t, ok)
	assert.ErrorContains(t, ValidateMiniMaxH3ModelBinding(ark, "MiniMax-H3", ark.Capability), "requires a reviewed V2 video profile")
	assert.NoError(t, ValidateMiniMaxH3ModelBinding(ark, "doubao-seedream-5-0-260128", ark.Capability), "unrelated existing protocols retain their contract")
}

func TestMiniMaxH3CatalogRejectsUnreviewedOrAmbiguousDeclarations(t *testing.T) {
	for _, test := range []struct {
		name   string
		mutate func(*miniMaxH3CatalogDocument)
	}{
		{"unknown schema", func(d *miniMaxH3CatalogDocument) { d.SchemaVersion = 2 }},
		{"invalid review date", func(d *miniMaxH3CatalogDocument) { d.ReviewedAt = "2026-02-30" }},
		{"empty models", func(d *miniMaxH3CatalogDocument) { d.Models = nil }},
		{"duplicate provider", func(d *miniMaxH3CatalogDocument) { d.Models[1].ProviderModelID = d.Models[0].ProviderModelID }},
		{"duplicate public identity", func(d *miniMaxH3CatalogDocument) { d.Models[1].PublicModelID = d.Models[0].PublicModelID }},
		{"unknown official identity", func(d *miniMaxH3CatalogDocument) { d.Models[0].ProviderModelID = "MiniMax-H3-Future" }},
		{"wrong max profile", func(d *miniMaxH3CatalogDocument) { d.Models[1].AdapterProfileID = d.Models[0].AdapterProfileID }},
		{"false first-frame claim", func(d *miniMaxH3CatalogDocument) { d.Models[0].ImageRole = "first_frame" }},
		{"fabricated production evidence", func(d *miniMaxH3CatalogDocument) { d.Models[0].EvidenceStatus = "production_ready" }},
		{"third-party source", func(d *miniMaxH3CatalogDocument) { d.Models[0].OfficialSources = []string{"https://minimax3.com/api"} }},
		{"lookalike official source", func(d *miniMaxH3CatalogDocument) {
			d.Models[0].OfficialSources = []string{"https://platform.minimax.io.example.test/docs/api-reference/video-generation-v2-create"}
		}},
		{"missing limitations", func(d *miniMaxH3CatalogDocument) { d.Models[0].Notes = nil }},
		{"missing acceptance mode", func(d *miniMaxH3CatalogDocument) { delete(d.Models[0].Capability.Modes, "text_to_video") }},
		{"max reference expansion", func(d *miniMaxH3CatalogDocument) { d.Models[1].Capability = d.Models[0].Capability }},
		{"mixed fifteen files", func(d *miniMaxH3CatalogDocument) {
			mode := d.Models[0].Capability.Modes["video_to_video"]
			mode.Limits.MaxImages = 9
			d.Models[0].Capability.Modes["video_to_video"] = mode
		}},
		{"duplicate duration", func(d *miniMaxH3CatalogDocument) {
			mode := d.Models[0].Capability.Modes["text_to_video"]
			mode.Limits.DurationSeconds = []int{5, 5}
			d.Models[0].Capability.Modes["text_to_video"] = mode
		}},
		{"unknown public mode", func(d *miniMaxH3CatalogDocument) {
			d.Models[0].Capability.Modes["audio_to_video"] = d.Models[0].Capability.Modes["text_to_video"]
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			var document miniMaxH3CatalogDocument
			require.NoError(t, common.Unmarshal(miniMaxH3CatalogJSON, &document))
			test.mutate(&document)
			raw, err := common.Marshal(document)
			require.NoError(t, err)
			_, err = decodeMiniMaxH3Catalog(raw)
			assert.Error(t, err)
		})
	}
	for _, test := range []struct{ name, raw string }{
		{"unknown root field", strings.Replace(string(miniMaxH3CatalogJSON), `"reviewed_at":`, `"metadata": {}, "reviewed_at":`, 1)},
		{"duplicate root key", strings.Replace(string(miniMaxH3CatalogJSON), `"reviewed_at":`, `"schema_version": 1, "reviewed_at":`, 1)},
		{"unknown provider override", strings.Replace(string(miniMaxH3CatalogJSON), `"max_prompt_length":`, `"provider_parameters": {"ratio":"adaptive"}, "max_prompt_length":`, 1)},
		{"duplicate nested key", strings.Replace(string(miniMaxH3CatalogJSON), `"max_prompt_length":`, `"max_images": 15, "max_prompt_length":`, 1)},
		{"trailing document", string(miniMaxH3CatalogJSON) + `{}`},
	} {
		t.Run(test.name, func(t *testing.T) {
			_, err := decodeMiniMaxH3Catalog([]byte(test.raw))
			assert.Error(t, err)
		})
	}
}

func TestMiniMaxH3CatalogAndProfileExportsAreIndependentCopies(t *testing.T) {
	model, found, err := ResolveMiniMaxH3ProviderModel("MiniMax-H3")
	require.NoError(t, err)
	require.True(t, found)
	capability := model.CompatibleCapability()
	capability.Modes["video_to_video"].Limits.Resolutions[0] = "8k"
	model.OfficialSources[0] = "https://untrusted.example.test"
	model.Notes[0] = "fabricated approval"
	delete(model.Capability.Modes, "text_to_video")
	fresh, found, err := ResolveMiniMaxH3ProviderModel("MiniMax-H3")
	require.NoError(t, err)
	require.True(t, found)
	assert.NotContains(t, fresh.Capability.Modes["video_to_video"].Limits.Resolutions, "8k")
	assert.Contains(t, fresh.Capability.Modes, "text_to_video")
	assert.NotEqual(t, model.OfficialSources[0], fresh.OfficialSources[0])
	assert.NotEqual(t, model.Notes[0], fresh.Notes[0])

	profile, ok := Get(MiniMaxH3ReferenceVideoGenerationV1)
	require.True(t, ok)
	profile.Capability = dto.PlatformGenerationCapabilities{}
	profile.Artifacts["text_to_video"] = ArtifactContract{Count: 2}
	freshProfile, ok := Get(MiniMaxH3ReferenceVideoGenerationV1)
	require.True(t, ok)
	assert.Len(t, freshProfile.Capability.Modes, 3)
	assert.Equal(t, 1, freshProfile.Artifacts["text_to_video"].Count)
}
