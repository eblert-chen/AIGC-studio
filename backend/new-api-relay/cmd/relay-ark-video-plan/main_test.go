package main

import (
	"bytes"
	"io"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func reviewedArkOptions() arkVideoPlanOptions {
	return arkVideoPlanOptions{
		createdAt: "2026-08-31T00:00:00Z",
		createdBy: "test-release-reviewer",
		reason:    "Offline declaration regression; no provider acceptance or deployment",
	}
}

func TestArkVideoPlanPreparesCurrentUnsignedModelsWithoutReadiness(t *testing.T) {
	plan, err := buildArkVideoPlan(reviewedArkOptions())
	require.NoError(t, err)
	require.Len(t, plan.Models, 6)
	assert.Equal(t, "review_required_not_deployed", plan.Status)
	ids := make([]string, 0, len(plan.Models))
	for _, item := range plan.Models {
		release := item.UnsignedModelRelease
		ids = append(ids, release.PublicModelID)
		assert.True(t, item.NewRoutesAllowed)
		assert.Equal(t, "acceptance_candidate", item.Lifecycle)
		assert.Nil(t, release.Attestation)
		require.NotEmpty(t, item.OfficialSources)
		profile, ok := generationprofile.Get(release.AdapterProfileID)
		require.True(t, ok)
		require.NoError(t, release.Validate(profile))
	}
	assert.Equal(t, []string{
		"seedance-1.0-pro", "seedance-1.0-pro-fast", "seedance-2.0", "seedance-2.0-fast", "seedance-2.0-mini", "seedance-2.5",
	}, ids)
	encoded, err := common.Marshal(plan)
	require.NoError(t, err)
	for _, forbidden := range []string{"\"attestation\"", "\"api_key\"", "\"production_ready\"", "\"staging_ready\"", "\"price_points\""} {
		assert.NotContains(t, string(encoded), forbidden)
	}
}

func TestArkVideoPlanRequiresExplicitExistingRouteChoice(t *testing.T) {
	options := reviewedArkOptions()
	options.publicModelID = "seedance-1.5-pro"
	_, err := buildArkVideoPlan(options)
	require.ErrorContains(t, err, "--include-existing-only")
	options.includeExistingOnly = true
	plan, err := buildArkVideoPlan(options)
	require.NoError(t, err)
	require.Len(t, plan.Models, 1)
	assert.False(t, plan.Models[0].NewRoutesAllowed)
	assert.Equal(t, "deprecated", plan.Models[0].Lifecycle)
	assert.Equal(t, "doubao-seedance-1-5-pro-251215", plan.Models[0].UnsignedModelRelease.ProviderModelID)
	options.publicModelID = ""
	plan, err = buildArkVideoPlan(options)
	require.NoError(t, err)
	assert.Len(t, plan.Models, 7)
}

func TestArkVideoPlanPreservesModelSpecificOutputLimits(t *testing.T) {
	for _, scenario := range []struct {
		model       string
		maxDuration int
		resolutions []string
	}{
		{"seedance-2.5", 30, []string{"480p", "720p", "1080p"}},
		{"seedance-2.0", 15, []string{"480p", "720p", "1080p", "4k"}},
		{"seedance-2.0-fast", 15, []string{"480p", "720p"}},
		{"seedance-2.0-mini", 15, []string{"480p", "720p"}},
	} {
		t.Run(scenario.model, func(t *testing.T) {
			options := reviewedArkOptions()
			options.publicModelID = scenario.model
			plan, err := buildArkVideoPlan(options)
			require.NoError(t, err)
			require.Len(t, plan.Models, 1)
			limits := plan.Models[0].UnsignedModelRelease.Capability.Modes["text_to_video"].Limits
			assert.Contains(t, limits.DurationSeconds, scenario.maxDuration)
			assert.NotContains(t, limits.DurationSeconds, scenario.maxDuration+1)
			assert.ElementsMatch(t, scenario.resolutions, limits.Resolutions)
		})
	}
}

func TestArkVideoPlanStopsPreparingDeprecatedModelAtEOS(t *testing.T) {
	options := reviewedArkOptions()
	options.publicModelID = "seedance-1.5-pro"
	options.includeExistingOnly = true
	options.createdAt = "2026-09-21T05:59:59Z"
	before, err := buildArkVideoPlan(options)
	require.NoError(t, err)
	require.Len(t, before.Models, 1)
	assert.Equal(t, options.createdAt, before.ReviewedAsOf)
	assert.Equal(t, "2026-09-21T06:00:00Z", before.Models[0].EOSAt)
	options.createdAt = "2026-09-21T06:00:00Z"
	_, err = buildArkVideoPlan(options)
	require.ErrorContains(t, err, "end of service")
	options.publicModelID = ""
	after, err := buildArkVideoPlan(options)
	require.NoError(t, err)
	assert.Len(t, after.Models, 6)
	for _, model := range after.Models {
		assert.NotEqual(t, "deprecated", model.Lifecycle)
	}
}

func TestArkVideoPlanRejectsUnreviewedIDsAndInvalidAudit(t *testing.T) {
	for _, model := range []string{"seedance-1.0-lite-t2v", "seedance-unknown", "doubao-seedance-2-5-260628", " seedance-2.5"} {
		options := reviewedArkOptions()
		options.includeExistingOnly = true
		options.publicModelID = model
		_, err := buildArkVideoPlan(options)
		require.Error(t, err, model)
	}
	for _, edit := range []func(*arkVideoPlanOptions){
		func(options *arkVideoPlanOptions) { options.createdAt = "2026-08-31" },
		func(options *arkVideoPlanOptions) { options.createdAt = "2026-08-31T08:00:00+08:00" },
		func(options *arkVideoPlanOptions) { options.createdBy = "" },
		func(options *arkVideoPlanOptions) { options.reason = " " },
	} {
		options := reviewedArkOptions()
		edit(&options)
		_, err := buildArkVideoPlan(options)
		require.Error(t, err)
	}
}

func TestArkVideoPlanCLIIsDeterministicAndDoesNotEmitPartialPlans(t *testing.T) {
	args := []string{"--created-at", "2026-08-31T00:00:00Z", "--created-by", "test-reviewer", "--reason", "Review candidate without deploying", "--model", "seedance-2.5"}
	var first, second bytes.Buffer
	require.NoError(t, runArkVideoPlan(args, &first, io.Discard))
	require.NoError(t, runArkVideoPlan(args, &second, io.Discard))
	assert.Equal(t, first.String(), second.String())
	var plan arkVideoPlan
	require.NoError(t, common.Unmarshal(first.Bytes(), &plan))
	require.Len(t, plan.Models, 1)
	var invalid bytes.Buffer
	require.Error(t, runArkVideoPlan(append(args, "unexpected"), &invalid, io.Discard))
	assert.Empty(t, invalid.String())
}
