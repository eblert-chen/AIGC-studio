package main

import (
	"bytes"
	"io"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func reviewedMiniMaxH3Options() miniMaxH3PlanOptions {
	return miniMaxH3PlanOptions{
		createdAt: "2026-08-31T00:00:00Z",
		createdBy: "test-release-reviewer",
		reason:    "Offline regression only; not paid provider acceptance or deployment",
	}
}

func TestMiniMaxH3PlanPreparesBothUnsignedModelsWithoutClaimingReadiness(t *testing.T) {
	t.Setenv("MINIMAX_API_KEY", "synthetic-secret-must-not-appear")
	t.Setenv("MINIMAX_BASE_URL", "https://must-not-be-contacted.example.test")
	options := reviewedMiniMaxH3Options()
	plan, err := buildMiniMaxH3Plan(options)
	require.NoError(t, err)
	require.Len(t, plan.Models, 2)
	assert.Equal(t, "review_required_not_deployed", plan.Status)
	assert.Equal(t, options.createdAt, plan.ReviewedAsOf)
	assert.NotEmpty(t, plan.Notices)
	ids := make([]string, 0, len(plan.Models))
	for _, item := range plan.Models {
		release := item.UnsignedModelRelease
		ids = append(ids, release.PublicModelID)
		assert.True(t, item.NewRoutesAllowed)
		assert.Equal(t, "acceptance_candidate", item.Lifecycle)
		assert.Equal(t, "route_acceptance_required", item.EvidenceStatus)
		assert.Nil(t, release.Attestation)
		assert.NotEmpty(t, item.OfficialSources)
		assert.NotEmpty(t, item.Notes)
		assert.Equal(t, options.createdAt, release.Audit.CreatedAt)
		assert.Equal(t, options.createdBy, release.Audit.CreatedBy)
		assert.Equal(t, options.reason, release.Audit.Reason)
		assert.Equal(t, "generationprofile/minimax_h3_models.v1.json", release.Audit.SourceRef)
		profile, ok := generationprofile.Get(release.AdapterProfileID)
		require.True(t, ok)
		require.NoError(t, release.Validate(profile))
		assert.ErrorContains(t, release.VerifyAttestation(nil, time.Date(2026, 8, 31, 0, 0, 0, 0, time.UTC)), "requires a signed attestation")
	}
	assert.Equal(t, []string{"minimax-h3", "minimax-h3-max"}, ids)
	encoded, err := common.Marshal(plan)
	require.NoError(t, err)
	for _, forbidden := range []string{
		`"attestation"`, `"api_key"`, `"production_ready"`, `"staging_ready"`, `"price_points"`,
		`"channel_id"`, `"route_id"`, `"grant"`, `"published"`, "synthetic-secret-must-not-appear", "must-not-be-contacted.example.test",
	} {
		assert.NotContains(t, string(encoded), forbidden)
	}
}

func TestMiniMaxH3PlanPreservesTheModelSpecificReferenceAndOutputSubset(t *testing.T) {
	for _, scenario := range []struct {
		model, providerID  string
		minimum, modeCount int
		resolutions        []string
	}{
		{"minimax-h3", "MiniMax-H3", 4, 3, []string{"768p", "2k"}},
		{"minimax-h3-max", "MiniMax-H3-Max", 5, 1, []string{"480p", "768p"}},
	} {
		t.Run(scenario.model, func(t *testing.T) {
			options := reviewedMiniMaxH3Options()
			options.publicModelID = scenario.model
			plan, err := buildMiniMaxH3Plan(options)
			require.NoError(t, err)
			require.Len(t, plan.Models, 1)
			release := plan.Models[0].UnsignedModelRelease
			assert.Equal(t, scenario.providerID, release.ProviderModelID)
			assert.Len(t, release.Capability.Modes, scenario.modeCount)
			for _, mode := range release.Capability.Modes {
				assert.Equal(t, 7000, mode.Limits.MaxPromptLength)
				assert.Len(t, mode.Limits.DurationSeconds, 16-scenario.minimum)
				assert.Contains(t, mode.Limits.DurationSeconds, scenario.minimum)
				assert.Contains(t, mode.Limits.DurationSeconds, 15)
				assert.NotContains(t, mode.Limits.DurationSeconds, scenario.minimum-1)
				assert.NotContains(t, mode.Limits.DurationSeconds, 16)
				assert.Equal(t, scenario.resolutions, mode.Limits.Resolutions)
				assert.Equal(t, []int{1}, mode.Limits.OutputCounts)
				assert.NotContains(t, mode.Limits.AspectRatios, "adaptive")
				assert.LessOrEqual(t, mode.Limits.MaxImages+mode.Limits.MaxVideos+mode.Limits.MaxAudio, 12)
			}
			if scenario.model == "minimax-h3" {
				assert.Equal(t, 9, release.Capability.Modes["image_to_video"].Limits.MaxImages)
				assert.Equal(t, 0, release.Capability.Modes["image_to_video"].Limits.MaxVideos)
				assert.Equal(t, 3, release.Capability.Modes["image_to_video"].Limits.MaxAudio)
				assert.Equal(t, 6, release.Capability.Modes["video_to_video"].Limits.MaxImages)
				assert.Equal(t, 3, release.Capability.Modes["video_to_video"].Limits.MaxVideos)
				assert.Equal(t, 3, release.Capability.Modes["video_to_video"].Limits.MaxAudio)
			} else {
				assert.Empty(t, release.Capability.Modes["text_to_video"].InputMediaTypes)
			}
		})
	}
}

func TestMiniMaxH3PlanRequiresAnExplicitActualReviewAndExactPublicIdentity(t *testing.T) {
	for _, model := range []string{
		"MiniMax-H3", "MiniMax-H3-Max", "minimax-h3-future", "minimax-hailuo-2.3", "minimax3", "minimax-h3 ", " minimax-h3", "minimax-h3,minimax-h3-max",
	} {
		t.Run(model, func(t *testing.T) {
			options := reviewedMiniMaxH3Options()
			options.publicModelID = model
			_, err := buildMiniMaxH3Plan(options)
			assert.Error(t, err)
		})
	}
	for _, test := range []struct {
		name   string
		mutate func(*miniMaxH3PlanOptions)
	}{
		{"missing timestamp", func(o *miniMaxH3PlanOptions) { o.createdAt = "" }},
		{"date without time", func(o *miniMaxH3PlanOptions) { o.createdAt = "2026-08-31" }},
		{"noncanonical timezone", func(o *miniMaxH3PlanOptions) { o.createdAt = "2026-08-31T08:00:00+08:00" }},
		{"missing reviewer", func(o *miniMaxH3PlanOptions) { o.createdBy = " " }},
		{"missing reason", func(o *miniMaxH3PlanOptions) { o.reason = "" }},
		{"reviewer whitespace", func(o *miniMaxH3PlanOptions) { o.createdBy = " test-reviewer" }},
		{"reason controls", func(o *miniMaxH3PlanOptions) { o.reason = "review\naccepted" }},
		{"unbounded reason", func(o *miniMaxH3PlanOptions) { o.reason = strings.Repeat("a", 513) }},
	} {
		t.Run(test.name, func(t *testing.T) {
			options := reviewedMiniMaxH3Options()
			test.mutate(&options)
			_, err := buildMiniMaxH3Plan(options)
			assert.Error(t, err)
		})
	}
}

func TestMiniMaxH3PlanCLIEmitsOnlyADeterministicCompleteUnsignedDocument(t *testing.T) {
	args := []string{
		"--created-at", "2026-08-31T00:00:00Z", "--created-by", "test-reviewer", "--reason", "Review candidate without deploying", "--model", "minimax-h3-max",
	}
	var first, second, diagnostics bytes.Buffer
	require.NoError(t, runMiniMaxH3Plan(args, &first, &diagnostics))
	require.NoError(t, runMiniMaxH3Plan(args, &second, &diagnostics))
	assert.Equal(t, first.String(), second.String())
	assert.Empty(t, diagnostics.String())
	assert.True(t, strings.HasSuffix(first.String(), "\n"))
	var plan miniMaxH3Plan
	require.NoError(t, common.Unmarshal(first.Bytes(), &plan))
	require.Len(t, plan.Models, 1)
	assert.Equal(t, "minimax-h3-max", plan.Models[0].UnsignedModelRelease.PublicModelID)
	assert.Nil(t, plan.Models[0].UnsignedModelRelease.Attestation)

	for _, invalidArgs := range [][]string{
		{}, append(append([]string(nil), args...), "unexpected"),
		append(append([]string(nil), args...), "--apply"),
		append(append([]string(nil), args...), "--api-key", "synthetic-secret"),
		append(append([]string(nil), args...), "--price", "1"),
		append(append([]string(nil), args...), "--publish"),
		append(append([]string(nil), args...), "--grant"),
		append(append([]string(nil), args...), "--model", "MiniMax-H3"),
	} {
		var output bytes.Buffer
		assert.Error(t, runMiniMaxH3Plan(invalidArgs, &output, io.Discard))
		assert.Empty(t, output.String(), "invalid input must not emit a partial release plan")
	}
}
