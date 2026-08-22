package model

import (
	"errors"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func preparePlatformGenerationRouteTest(t *testing.T) {
	t.Helper()
	require.NoError(t, DB.AutoMigrate(
		&Channel{},
		&ProviderChannelCredentialSetVersion{},
		&PlatformGenerationJob{},
		&PlatformArtifactUploadIntent{},
		&PlatformGenerationProviderAccountState{},
		&PlatformGenerationProviderRoute{},
		&PlatformGenerationRouteAdmission{},
		&PlatformGenerationReconciliationEvent{},
		&PlatformProviderTerminalOutcome{},
	))
	clear := func() {
		require.NoError(t, DB.Exec("DELETE FROM platform_generation_reconciliation_events").Error)
		require.NoError(t, DB.Exec("DELETE FROM platform_provider_terminal_outcomes").Error)
		require.NoError(t, DB.Session(&gorm.Session{AllowGlobalUpdate: true}).Delete(&PlatformArtifactUploadIntent{}).Error)
		require.NoError(t, DB.Session(&gorm.Session{AllowGlobalUpdate: true}).Delete(&PlatformGenerationRouteAdmission{}).Error)
		require.NoError(t, DB.Session(&gorm.Session{AllowGlobalUpdate: true}).Delete(&PlatformGenerationProviderRoute{}).Error)
		require.NoError(t, DB.Session(&gorm.Session{AllowGlobalUpdate: true}).Delete(&PlatformGenerationProviderAccountState{}).Error)
		require.NoError(t, DB.Session(&gorm.Session{AllowGlobalUpdate: true}).Delete(&PlatformGenerationJob{}).Error)
		require.NoError(t, DB.Session(&gorm.Session{AllowGlobalUpdate: true}).Delete(&ProviderChannelCredentialSetVersion{}).Error)
		require.NoError(t, DB.Session(&gorm.Session{AllowGlobalUpdate: true}).Delete(&Channel{}).Error)
	}
	clear()
	t.Cleanup(clear)
}

func createPlatformGenerationRouteFixture(t *testing.T, modelName string, rpmLimit int, activeLimit int) *PlatformGenerationProviderRoute {
	t.Helper()
	key := "provider-key-" + modelName
	require.NoError(t, DB.Create(&Channel{
		Id:     1001,
		Type:   constant.ChannelTypeKling,
		Name:   "generation-route-test",
		Key:    key,
		Status: common.ChannelStatusEnabled,
	}).Error)
	route := &PlatformGenerationProviderRoute{
		RouteKey:            "route-" + modelName,
		Model:               modelName,
		Mode:                "text_to_video",
		ProviderName:        "provider-test",
		AccountID:           "account-test",
		ChannelID:           1001,
		AcceptedChannelType: constant.ChannelTypeKling,
		KeyIndex:            0,
		KeyFingerprint:      fmt.Sprintf("%x", common.Sha256Raw([]byte(key))),
		ChannelClass:        PlatformGenerationChannelClassOfficialProvider,
		UpstreamModel:       modelName,
		StagingReady:        true,
		ProductionReady:     true,
		Enabled:             true,
		RPMWindowSeconds:    60,
		RPMLimit:            rpmLimit,
		ActiveLimit:         activeLimit,
	}
	require.NoError(t, CreatePlatformGenerationProviderRoute(route))
	return route
}

func createPlatformGenerationRouteForSameAccount(
	t *testing.T,
	base *PlatformGenerationProviderRoute,
	routeKey string,
	modelName string,
	mode string,
) *PlatformGenerationProviderRoute {
	t.Helper()
	route := &PlatformGenerationProviderRoute{
		RouteKey:            routeKey,
		Model:               modelName,
		Mode:                mode,
		ProviderName:        base.ProviderName,
		AccountID:           base.AccountID,
		ChannelID:           base.ChannelID,
		AcceptedChannelType: base.AcceptedChannelType,
		KeyIndex:            base.KeyIndex,
		KeyFingerprint:      base.KeyFingerprint,
		ChannelClass:        base.ChannelClass,
		UpstreamModel:       modelName,
		StagingReady:        base.StagingReady,
		ProductionReady:     base.ProductionReady,
		Enabled:             true,
		RPMWindowSeconds:    base.RPMWindowSeconds,
		RPMLimit:            base.RPMLimit,
		ActiveLimit:         base.ActiveLimit,
	}
	require.NoError(t, CreatePlatformGenerationProviderRoute(route))
	require.Equal(t, base.AccountStateID, route.AccountStateID)
	return route
}

func TestPlatformGenerationUnknownSubmissionRetainsRouteAndSlot(t *testing.T) {
	preparePlatformGenerationRouteTest(t)
	route := createPlatformGenerationRouteFixture(t, "route.unknown", 10, 1)
	jobID := uuid.NewString()

	claim, err := ClaimPlatformGenerationProviderRoute(jobID, route.Model, route.Mode)
	require.NoError(t, err)
	assert.NotEmpty(t, claim.SubmissionToken)
	assert.Equal(t, route.ID, claim.Route.ID)
	assert.Equal(t, 1, claim.Route.ActiveCount)

	staleToken := uuid.NewString()
	won, err := MarkPlatformGenerationRouteSubmissionUnknown(jobID, staleToken)
	require.NoError(t, err)
	assert.False(t, won)

	won, err = MarkPlatformGenerationRouteSubmissionUnknown(jobID, claim.SubmissionToken)
	require.NoError(t, err)
	assert.True(t, won)
	won, err = MarkPlatformGenerationRouteSubmissionUnknown(jobID, claim.SubmissionToken)
	require.NoError(t, err)
	assert.True(t, won, "the same fenced transition is idempotent")

	won, err = ReleasePlatformGenerationProviderRoute(jobID, claim.SubmissionToken)
	assert.False(t, won)
	assert.ErrorIs(t, err, ErrPlatformGenerationRouteAdmissionUnknown)

	_, err = ClaimPlatformGenerationProviderRoute(jobID, route.Model, route.Mode)
	assert.ErrorIs(t, err, ErrPlatformGenerationRouteAdmissionUnknown)

	var persistedRoute PlatformGenerationProviderRoute
	require.NoError(t, DB.First(&persistedRoute, route.ID).Error)
	assert.Equal(t, 1, persistedRoute.ActiveCount, "unknown outcome must retain its long-lived slot")

	won, err = FinishPlatformGenerationProviderRoute(jobID, staleToken)
	require.NoError(t, err)
	assert.False(t, won)
	won, err = FinishPlatformGenerationProviderRoute(jobID, claim.SubmissionToken)
	require.NoError(t, err)
	assert.True(t, won)

	require.NoError(t, DB.First(&persistedRoute, route.ID).Error)
	assert.Zero(t, persistedRoute.ActiveCount)
	admission, assignedRoute, err := GetPlatformGenerationProviderRouteAssignment(jobID)
	require.NoError(t, err)
	assert.Equal(t, route.ID, assignedRoute.ID)
	assert.Equal(t, PlatformGenerationRouteAdmissionFinished, admission.State)
	assert.False(t, admission.SlotHeld)
	assert.Empty(t, admission.SubmissionTokenHash)
}

func TestPlatformGenerationRouteAdmissionEnforcesActiveAndRPMCapacity(t *testing.T) {
	t.Run("active capacity", func(t *testing.T) {
		preparePlatformGenerationRouteTest(t)
		route := createPlatformGenerationRouteFixture(t, "route.active", 10, 1)
		firstJobID := uuid.NewString()
		secondJobID := uuid.NewString()

		first, err := ClaimPlatformGenerationProviderRoute(firstJobID, route.Model, route.Mode)
		require.NoError(t, err)
		_, err = ClaimPlatformGenerationProviderRoute(secondJobID, route.Model, route.Mode)
		assert.ErrorIs(t, err, ErrPlatformGenerationProviderRouteBusy)

		released, err := ReleasePlatformGenerationProviderRoute(firstJobID, first.SubmissionToken)
		require.NoError(t, err)
		assert.True(t, released)
		second, err := ClaimPlatformGenerationProviderRoute(secondJobID, route.Model, route.Mode)
		require.NoError(t, err)
		assert.Equal(t, 1, second.Route.ActiveCount)
	})

	t.Run("fixed RPM window", func(t *testing.T) {
		preparePlatformGenerationRouteTest(t)
		route := createPlatformGenerationRouteFixture(t, "route.rpm", 1, 10)
		firstJobID := uuid.NewString()

		first, err := ClaimPlatformGenerationProviderRoute(firstJobID, route.Model, route.Mode)
		require.NoError(t, err)
		released, err := ReleasePlatformGenerationProviderRoute(firstJobID, first.SubmissionToken)
		require.NoError(t, err)
		assert.True(t, released)

		secondJobID := uuid.NewString()
		_, err = ClaimPlatformGenerationProviderRoute(secondJobID, route.Model, route.Mode)
		assert.ErrorIs(t, err, ErrPlatformGenerationProviderRouteRateLimited)

		expiredWindow := time.Now().UTC().Add(-2 * time.Minute)
		require.NoError(t, DB.Model(&PlatformGenerationProviderAccountState{}).Where("id = ?", route.AccountStateID).Update("rpm_window_started_at", expiredWindow).Error)
		second, err := ClaimPlatformGenerationProviderRoute(secondJobID, route.Model, route.Mode)
		require.NoError(t, err)
		assert.Equal(t, 1, second.Route.RPMWindowCount, "an expired fixed window must reset before admission")
	})
}

func TestPlatformGenerationPhysicalAccountSharesAdmissionAcrossModelsAndModes(t *testing.T) {
	t.Run("active capacity", func(t *testing.T) {
		preparePlatformGenerationRouteTest(t)
		firstRoute := createPlatformGenerationRouteFixture(t, "shared.active.first", 10, 1)
		secondRoute := createPlatformGenerationRouteForSameAccount(
			t,
			firstRoute,
			"shared-active-second",
			"shared.active.second",
			"image_to_video",
		)

		first, err := ClaimPlatformGenerationProviderRoute(uuid.NewString(), firstRoute.Model, firstRoute.Mode)
		require.NoError(t, err)
		_, err = ClaimPlatformGenerationProviderRoute(uuid.NewString(), secondRoute.Model, secondRoute.Mode)
		assert.ErrorIs(t, err, ErrPlatformGenerationProviderRouteBusy,
			"a second route must not multiply one physical account's active capacity")

		released, err := ReleasePlatformGenerationProviderRoute(first.JobID, first.SubmissionToken)
		require.NoError(t, err)
		require.True(t, released)
		second, err := ClaimPlatformGenerationProviderRoute(uuid.NewString(), secondRoute.Model, secondRoute.Mode)
		require.NoError(t, err)
		assert.Equal(t, firstRoute.AccountStateID, second.Route.AccountStateID)
		assert.Equal(t, 1, second.Route.ActiveCount)
	})

	t.Run("fixed RPM window", func(t *testing.T) {
		preparePlatformGenerationRouteTest(t)
		firstRoute := createPlatformGenerationRouteFixture(t, "shared.rpm.first", 1, 10)
		secondRoute := createPlatformGenerationRouteForSameAccount(
			t,
			firstRoute,
			"shared-rpm-second",
			"shared.rpm.second",
			"image_to_video",
		)

		first, err := ClaimPlatformGenerationProviderRoute(uuid.NewString(), firstRoute.Model, firstRoute.Mode)
		require.NoError(t, err)
		released, err := ReleasePlatformGenerationProviderRoute(first.JobID, first.SubmissionToken)
		require.NoError(t, err)
		require.True(t, released)
		_, err = ClaimPlatformGenerationProviderRoute(uuid.NewString(), secondRoute.Model, secondRoute.Mode)
		assert.ErrorIs(t, err, ErrPlatformGenerationProviderRouteRateLimited,
			"a second route must not multiply one physical account's RPM window")

		expiredWindow := time.Now().UTC().Add(-2 * time.Minute)
		require.NoError(t, DB.Model(&PlatformGenerationProviderAccountState{}).
			Where("id = ?", firstRoute.AccountStateID).
			Update("rpm_window_started_at", expiredWindow).Error)
		_, err = ClaimPlatformGenerationProviderRoute(uuid.NewString(), secondRoute.Model, secondRoute.Mode)
		require.NoError(t, err)
	})

	t.Run("cooldown and recovery", func(t *testing.T) {
		preparePlatformGenerationRouteTest(t)
		firstRoute := createPlatformGenerationRouteFixture(t, "shared.cooldown.first", 20, 3)
		secondRoute := createPlatformGenerationRouteForSameAccount(
			t,
			firstRoute,
			"shared-cooldown-second",
			"shared.cooldown.second",
			"image_to_video",
		)
		failureJobID := uuid.NewString()
		failureClaim, err := ClaimPlatformGenerationProviderRoute(failureJobID, firstRoute.Model, firstRoute.Mode)
		require.NoError(t, err)
		successJobID := uuid.NewString()
		_, err = ClaimPlatformGenerationProviderRoute(successJobID, secondRoute.Model, secondRoute.Mode)
		require.NoError(t, err, "work admitted before cooldown remains pinned")

		createProcessingJob := func(jobID string, route *PlatformGenerationProviderRoute, pollToken string) {
			require.NoError(t, DB.Create(&PlatformGenerationJob{
				ID:                         jobID,
				TenantID:                   uuid.NewString(),
				SourceClientID:             "platform",
				RequestID:                  "request-" + jobID,
				IdempotencyKey:             "idempotency-" + jobID,
				RequestHash:                strings.Repeat("a", 64),
				RequestJSON:                `{}`,
				Model:                      route.Model,
				Mode:                       route.Mode,
				ExpectedCapabilityRevision: "sha256:" + strings.Repeat("b", 64),
				CapabilityRevision:         "sha256:" + strings.Repeat("b", 64),
				Status:                     PlatformGenerationStatusProcessing,
				PollLeaseToken:             pollToken,
				PollLeaseExpiresAt:         time.Now().UTC().Add(time.Minute),
				OutputsJSON:                `[]`,
				ErrorDetailsJSON:           `{}`,
			}).Error)
		}
		createProcessingJob(failureJobID, firstRoute, "shared-failure-poll")
		createProcessingJob(successJobID, secondRoute, "shared-success-poll")

		won, err := CompletePlatformGenerationTerminalWithOutcomePolicy(
			failureJobID,
			"shared-failure-poll",
			PlatformGenerationStatusProcessing,
			map[string]any{"status": PlatformGenerationStatusFailed},
			&PlatformProviderTerminalOutcome{
				ID:                uuid.NewString(),
				RouteID:           firstRoute.ID,
				RelayJobID:        failureJobID,
				Outcome:           PlatformProviderOutcomeFailed,
				FailureOwner:      PlatformProviderFailureOwnerProvider,
				FailureCode:       "provider_unavailable",
				OccurredAt:        time.Now().UTC(),
				ExternalReference: "provider-task:" + failureJobID,
			},
			1,
			5*time.Minute,
		)
		require.NoError(t, err)
		require.True(t, won)
		_, err = ClaimPlatformGenerationProviderRoute(uuid.NewString(), secondRoute.Model, secondRoute.Mode)
		assert.ErrorIs(t, err, ErrPlatformGenerationProviderRouteUnavailable,
			"a provider failure cools every route for the same physical account")

		won, err = CompletePlatformGenerationTerminalWithOutcomePolicy(
			successJobID,
			"shared-success-poll",
			PlatformGenerationStatusProcessing,
			map[string]any{"status": PlatformGenerationStatusTransferring},
			&PlatformProviderTerminalOutcome{
				ID:                uuid.NewString(),
				RouteID:           secondRoute.ID,
				RelayJobID:        successJobID,
				Outcome:           PlatformProviderOutcomeSucceeded,
				FailureOwner:      PlatformProviderFailureOwnerNone,
				OccurredAt:        time.Now().UTC(),
				ExternalReference: "provider-task:" + successJobID,
			},
			1,
			5*time.Minute,
		)
		require.NoError(t, err)
		require.True(t, won)
		_, err = ClaimPlatformGenerationProviderRoute(uuid.NewString(), firstRoute.Model, firstRoute.Mode)
		require.NoError(t, err, "an existing task success recovers the shared physical account")
		assert.NotEmpty(t, failureClaim.SubmissionToken)
	})
}

func TestSyncPlatformGenerationProviderRoutesRejectsConflictingPhysicalAccountLimits(t *testing.T) {
	preparePlatformGenerationRouteTest(t)
	key := "sync-shared-account-key"
	fingerprint := fmt.Sprintf("%x", common.Sha256Raw([]byte(key)))
	base := PlatformGenerationProviderRoute{
		RouteKey:         "sync-shared-first",
		Model:            "sync.shared.first",
		Mode:             "text_to_video",
		ProviderName:     "provider-test",
		AccountID:        "account-test",
		ChannelID:        3001,
		KeyIndex:         0,
		KeyFingerprint:   fingerprint,
		ChannelClass:     PlatformGenerationChannelClassOfficialProvider,
		UpstreamModel:    "upstream-first",
		ProductionReady:  true,
		Enabled:          true,
		RPMWindowSeconds: 60,
		RPMLimit:         10,
		ActiveLimit:      2,
	}
	conflicting := base
	conflicting.RouteKey = "sync-shared-second"
	conflicting.Model = "sync.shared.second"
	conflicting.Mode = "image_to_video"
	conflicting.UpstreamModel = "upstream-second"
	conflicting.RPMLimit = 11

	err := SyncPlatformGenerationProviderRoutes([]PlatformGenerationProviderRoute{base, conflicting})
	require.Error(t, err)
	assert.Contains(t, err.Error(), "conflicting RPM or active limits")
	var routeCount int64
	require.NoError(t, DB.Model(&PlatformGenerationProviderRoute{}).Count(&routeCount).Error)
	assert.Zero(t, routeCount, "a conflicting physical-account declaration must fail closed atomically")
	var stateCount int64
	require.NoError(t, DB.Model(&PlatformGenerationProviderAccountState{}).Count(&stateCount).Error)
	assert.Zero(t, stateCount)
}

func TestSyncPlatformGenerationProviderRoutesPersistsIndependentReadinessGates(t *testing.T) {
	preparePlatformGenerationRouteTest(t)
	key := "sync-staging-readiness-key"
	route := PlatformGenerationProviderRoute{
		RouteKey: "sync-staging-readiness", Model: "sync.staging.readiness", Mode: "text_to_video",
		ProviderName: "provider-test", AccountID: "account-test", ChannelID: 3002, KeyIndex: 0,
		KeyFingerprint: fmt.Sprintf("%x", common.Sha256Raw([]byte(key))),
		ChannelClass:   PlatformGenerationChannelClassOfficialProvider, UpstreamModel: "upstream-staging",
		StagingReady: true, ProductionReady: false, Enabled: true,
		RPMWindowSeconds: 60, RPMLimit: 10, ActiveLimit: 2,
	}
	require.NoError(t, SyncPlatformGenerationProviderRoutes([]PlatformGenerationProviderRoute{route}))

	var persisted PlatformGenerationProviderRoute
	require.NoError(t, DB.Where("route_key = ? AND mode = ?", route.RouteKey, route.Mode).First(&persisted).Error)
	assert.True(t, persisted.StagingReady)
	assert.False(t, persisted.ProductionReady)

	route.StagingReady = false
	route.ProductionReady = true
	require.NoError(t, SyncPlatformGenerationProviderRoutes([]PlatformGenerationProviderRoute{route}))
	require.NoError(t, DB.First(&persisted, persisted.ID).Error)
	assert.False(t, persisted.StagingReady)
	assert.True(t, persisted.ProductionReady)
}

func TestMigratePlatformGenerationProviderAccountStateMergesLegacyRouteCounters(t *testing.T) {
	preparePlatformGenerationRouteTest(t)
	key := "legacy-shared-account-key"
	fingerprint := fmt.Sprintf("%x", common.Sha256Raw([]byte(key)))
	windowOne := time.Now().UTC().Add(-30 * time.Second)
	windowTwo := time.Now().UTC().Add(-10 * time.Second)
	coolingOne := time.Now().UTC().Add(time.Minute)
	coolingTwo := time.Now().UTC().Add(2 * time.Minute)
	first := PlatformGenerationProviderRoute{
		RouteKey:            "legacy-shared-first",
		Model:               "legacy.shared.first",
		Mode:                "text_to_video",
		ProviderName:        "provider-test",
		AccountID:           "account-test",
		ChannelID:           4001,
		KeyIndex:            0,
		KeyFingerprint:      fingerprint,
		ChannelClass:        PlatformGenerationChannelClassOfficialProvider,
		UpstreamModel:       "upstream-first",
		ProductionReady:     true,
		Enabled:             true,
		CoolingUntil:        &coolingOne,
		ConsecutiveFailures: 1,
		LastErrorCode:       "first_failure",
		RPMWindowSeconds:    60,
		RPMLimit:            10,
		RPMWindowStartedAt:  &windowOne,
		RPMWindowCount:      2,
		ActiveCount:         1,
		ActiveLimit:         4,
	}
	second := first
	second.RouteKey = "legacy-shared-second"
	second.Model = "legacy.shared.second"
	second.Mode = "image_to_video"
	second.UpstreamModel = "upstream-second"
	second.RPMWindowStartedAt = &windowTwo
	second.RPMWindowCount = 3
	second.ActiveCount = 2
	second.CoolingUntil = &coolingTwo
	second.ConsecutiveFailures = 2
	second.LastErrorCode = "second_failure"
	require.NoError(t, DB.Create(&first).Error)
	require.NoError(t, DB.Create(&second).Error)

	require.NoError(t, MigratePlatformGenerationProviderAccountState())
	var persistedFirst PlatformGenerationProviderRoute
	var persistedSecond PlatformGenerationProviderRoute
	require.NoError(t, DB.First(&persistedFirst, first.ID).Error)
	require.NoError(t, DB.First(&persistedSecond, second.ID).Error)
	require.NotZero(t, persistedFirst.AccountStateID)
	assert.Equal(t, persistedFirst.AccountStateID, persistedSecond.AccountStateID)
	var state PlatformGenerationProviderAccountState
	require.NoError(t, DB.First(&state, persistedFirst.AccountStateID).Error)
	assert.Equal(t, 3, state.ActiveCount)
	assert.Equal(t, 5, state.RPMWindowCount)
	require.NotNil(t, state.RPMWindowStartedAt)
	assert.WithinDuration(t, windowTwo, state.RPMWindowStartedAt.UTC(), time.Second)
	require.NotNil(t, state.CoolingUntil)
	assert.WithinDuration(t, coolingTwo, state.CoolingUntil.UTC(), time.Second)
	assert.Equal(t, 3, state.ConsecutiveFailures)
	assert.Equal(t, 3, persistedFirst.ActiveCount, "legacy route counters become shared-state mirrors")
	assert.Equal(t, 3, persistedSecond.ActiveCount)

	require.NoError(t, MigratePlatformGenerationProviderAccountState())
	require.NoError(t, DB.First(&state, persistedFirst.AccountStateID).Error)
	assert.Equal(t, 3, state.ActiveCount, "the migration must not aggregate a mapped route twice")
	assert.Equal(t, 5, state.RPMWindowCount)
}

func TestPlatformGenerationRouteSubmissionTokenFencesAReusedAdmission(t *testing.T) {
	preparePlatformGenerationRouteTest(t)
	route := createPlatformGenerationRouteFixture(t, "route.fence", 10, 1)
	jobID := uuid.NewString()

	first, err := ClaimPlatformGenerationProviderRoute(jobID, route.Model, route.Mode)
	require.NoError(t, err)
	released, err := ReleasePlatformGenerationProviderRoute(jobID, first.SubmissionToken)
	require.NoError(t, err)
	assert.True(t, released)

	second, err := ClaimPlatformGenerationProviderRoute(jobID, route.Model, route.Mode)
	require.NoError(t, err)
	assert.Equal(t, 2, second.Attempt)
	assert.NotEqual(t, first.SubmissionToken, second.SubmissionToken)

	released, err = ReleasePlatformGenerationProviderRoute(jobID, first.SubmissionToken)
	require.NoError(t, err)
	assert.False(t, released, "an expired worker token must not release the current slot")

	var persistedRoute PlatformGenerationProviderRoute
	require.NoError(t, DB.First(&persistedRoute, route.ID).Error)
	assert.Equal(t, 1, persistedRoute.ActiveCount)

	released, err = ReleasePlatformGenerationProviderRoute(jobID, second.SubmissionToken)
	require.NoError(t, err)
	assert.True(t, released)
	require.NoError(t, DB.First(&persistedRoute, route.ID).Error)
	assert.Zero(t, persistedRoute.ActiveCount)
}

func TestPlatformGenerationRouteSubmissionTokenIsConsumedOnceUnderAnActiveLease(t *testing.T) {
	preparePlatformGenerationRouteTest(t)
	route := createPlatformGenerationRouteFixture(t, "route.post-once", 10, 1)
	jobID := uuid.NewString()
	job := PlatformGenerationJob{
		ID:                         jobID,
		TenantID:                   uuid.NewString(),
		SourceClientID:             "platform",
		RequestID:                  "request-post-once",
		IdempotencyKey:             "idempotency-post-once",
		RequestHash:                strings.Repeat("f", 64),
		RequestJSON:                `{}`,
		Model:                      route.Model,
		Mode:                       route.Mode,
		ExpectedCapabilityRevision: "sha256:" + strings.Repeat("a", 64),
		CapabilityRevision:         "sha256:" + strings.Repeat("a", 64),
		Status:                     PlatformGenerationStatusSubmitting,
		SubmissionLeaseToken:       "submission-owner",
		SubmissionLeaseExpiresAt:   time.Now().UTC().Add(time.Minute),
		OutputsJSON:                `[]`,
		ErrorDetailsJSON:           `{}`,
	}
	require.NoError(t, DB.Create(&job).Error)
	claim, err := ClaimPlatformGenerationProviderRoute(jobID, route.Model, route.Mode)
	require.NoError(t, err)

	started, err := BeginPlatformGenerationRouteSubmission(jobID, route.ID, job.SubmissionLeaseToken, claim.SubmissionToken)
	require.NoError(t, err)
	assert.Equal(t, route.ID, started.ID)
	_, err = BeginPlatformGenerationRouteSubmission(jobID, route.ID, job.SubmissionLeaseToken, claim.SubmissionToken)
	require.Error(t, err, "the same internal request must not trigger a second provider POST")

	admission, _, err := GetPlatformGenerationProviderRouteAssignment(jobID)
	require.NoError(t, err)
	assert.Equal(t, PlatformGenerationRouteAdmissionPosting, admission.State)
	released, err := ReleasePlatformGenerationProviderRoute(jobID, claim.SubmissionToken)
	require.NoError(t, err)
	assert.True(t, released, "a downstream pre-provider rejection may safely release a consumed route token")
}

func TestPlatformGenerationRouteSubmissionRejectsAnExpiredWorkerLease(t *testing.T) {
	preparePlatformGenerationRouteTest(t)
	route := createPlatformGenerationRouteFixture(t, "route.expired-post", 10, 1)
	jobID := uuid.NewString()
	job := PlatformGenerationJob{
		ID:                         jobID,
		TenantID:                   uuid.NewString(),
		SourceClientID:             "platform",
		RequestID:                  "request-expired-post",
		IdempotencyKey:             "idempotency-expired-post",
		RequestHash:                strings.Repeat("e", 64),
		RequestJSON:                `{}`,
		Model:                      route.Model,
		Mode:                       route.Mode,
		ExpectedCapabilityRevision: "sha256:" + strings.Repeat("a", 64),
		CapabilityRevision:         "sha256:" + strings.Repeat("a", 64),
		Status:                     PlatformGenerationStatusSubmitting,
		SubmissionLeaseToken:       "expired-owner",
		SubmissionLeaseExpiresAt:   time.Now().UTC().Add(-time.Second),
		OutputsJSON:                `[]`,
		ErrorDetailsJSON:           `{}`,
	}
	require.NoError(t, DB.Create(&job).Error)
	claim, err := ClaimPlatformGenerationProviderRoute(jobID, route.Model, route.Mode)
	require.NoError(t, err)
	_, err = BeginPlatformGenerationRouteSubmission(jobID, route.ID, job.SubmissionLeaseToken, claim.SubmissionToken)
	require.Error(t, err)

	admission, _, err := GetPlatformGenerationProviderRouteAssignment(jobID)
	require.NoError(t, err)
	assert.Equal(t, PlatformGenerationRouteAdmissionHeld, admission.State)
}

func TestPlatformGenerationRouteAdmissionRejectsLiveChannelChanges(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(t *testing.T, route *PlatformGenerationProviderRoute)
	}{
		{
			name: "disabled channel",
			mutate: func(t *testing.T, route *PlatformGenerationProviderRoute) {
				require.NoError(t, DB.Model(&Channel{}).Where("id = ?", route.ChannelID).
					Update("status", common.ChannelStatusManuallyDisabled).Error)
			},
		},
		{
			name: "disabled multi key",
			mutate: func(t *testing.T, route *PlatformGenerationProviderRoute) {
				var channel Channel
				require.NoError(t, DB.First(&channel, route.ChannelID).Error)
				channel.ChannelInfo = ChannelInfo{
					IsMultiKey:         true,
					MultiKeySize:       1,
					MultiKeyStatusList: map[int]int{route.KeyIndex: common.ChannelStatusManuallyDisabled},
				}
				require.NoError(t, DB.Model(&Channel{}).Where("id = ?", route.ChannelID).
					Update("channel_info", channel.ChannelInfo).Error)
			},
		},
		{
			name: "rotated key",
			mutate: func(t *testing.T, route *PlatformGenerationProviderRoute) {
				require.NoError(t, RotateChannelCredentialSet(route.ChannelID, "rotated-provider-key"))
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			preparePlatformGenerationRouteTest(t)
			route := createPlatformGenerationRouteFixture(t, "route.live-change."+strings.ReplaceAll(test.name, " ", "-"), 10, 1)
			test.mutate(t, route)

			_, err := ClaimPlatformGenerationProviderRoute(uuid.NewString(), route.Model, route.Mode)
			assert.ErrorIs(t, err, ErrPlatformGenerationProviderRouteUnavailable)

			var persistedRoute PlatformGenerationProviderRoute
			require.NoError(t, DB.First(&persistedRoute, route.ID).Error)
			assert.Zero(t, persistedRoute.ActiveCount)
			assert.Zero(t, persistedRoute.RPMWindowCount)
		})
	}
}

func TestPlatformGenerationRouteAdmissionFencesAcceptedChannelTypeDrift(t *testing.T) {
	preparePlatformGenerationRouteTest(t)
	route := createPlatformGenerationRouteFixture(t, "route.accepted-channel-type", 10, 2)
	jobID := uuid.NewString()
	job := PlatformGenerationJob{
		ID:                         jobID,
		TenantID:                   uuid.NewString(),
		SourceClientID:             "platform",
		RequestID:                  "request-accepted-channel-type",
		IdempotencyKey:             "idempotency-accepted-channel-type",
		RequestHash:                strings.Repeat("c", 64),
		RequestJSON:                `{}`,
		Model:                      route.Model,
		Mode:                       route.Mode,
		ExpectedCapabilityRevision: "sha256:" + strings.Repeat("a", 64),
		CapabilityRevision:         "sha256:" + strings.Repeat("a", 64),
		Status:                     PlatformGenerationStatusSubmitting,
		SubmissionLeaseToken:       "accepted-channel-type-owner",
		SubmissionLeaseExpiresAt:   time.Now().UTC().Add(time.Minute),
		OutputsJSON:                `[]`,
		ErrorDetailsJSON:           `{}`,
	}
	require.NoError(t, DB.Create(&job).Error)
	claim, err := ClaimPlatformGenerationProviderRoute(jobID, route.Model, route.Mode)
	require.NoError(t, err)
	_, err = BeginPlatformGenerationRouteSubmission(
		jobID,
		route.ID,
		job.SubmissionLeaseToken,
		claim.SubmissionToken,
	)
	require.NoError(t, err)

	require.NoError(t, DB.Model(&Channel{}).Where("id = ?", route.ChannelID).
		UpdateColumn("type", constant.ChannelTypeGemini).Error)
	_, err = ClaimPlatformGenerationProviderRoute(uuid.NewString(), route.Model, route.Mode)
	assert.ErrorIs(t, err, ErrPlatformGenerationProviderRouteUnavailable,
		"an adapter type changed after acceptance must reject new admissions")

	admission, assignedRoute, err := GetPlatformGenerationProviderRouteAssignment(jobID)
	require.NoError(t, err)
	assert.Equal(t, PlatformGenerationRouteAdmissionPosting, admission.State)
	assert.Equal(t, constant.ChannelTypeKling, assignedRoute.AcceptedChannelType)
	finished, err := FinishPlatformGenerationProviderRoute(jobID, claim.SubmissionToken)
	require.NoError(t, err)
	assert.True(t, finished,
		"an already-bound provider task must remain terminal-closeable after adapter drift")

	require.NoError(t, DB.Model(&Channel{}).Where("id = ?", route.ChannelID).
		UpdateColumn("type", constant.ChannelTypeKling).Error)
	require.NoError(t, RotateChannelCredentialSet(
		route.ChannelID,
		"rotated-after-channel-type-restore",
	))
	_, err = ClaimPlatformGenerationProviderRoute(uuid.NewString(), route.Model, route.Mode)
	assert.ErrorIs(t, err, ErrPlatformGenerationProviderRouteUnavailable,
		"restoring the adapter type must not bypass the accepted key fingerprint")
}

func TestPlatformGenerationRouteSubmissionRechecksLiveChannelBeforeProviderPost(t *testing.T) {
	preparePlatformGenerationRouteTest(t)
	route := createPlatformGenerationRouteFixture(t, "route.pre-post-channel-check", 10, 1)
	jobID := uuid.NewString()
	job := PlatformGenerationJob{
		ID:                         jobID,
		TenantID:                   uuid.NewString(),
		SourceClientID:             "platform",
		RequestID:                  "request-pre-post-channel-check",
		IdempotencyKey:             "idempotency-pre-post-channel-check",
		RequestHash:                strings.Repeat("d", 64),
		RequestJSON:                `{}`,
		Model:                      route.Model,
		Mode:                       route.Mode,
		ExpectedCapabilityRevision: "sha256:" + strings.Repeat("a", 64),
		CapabilityRevision:         "sha256:" + strings.Repeat("a", 64),
		Status:                     PlatformGenerationStatusSubmitting,
		SubmissionLeaseToken:       "submission-owner",
		SubmissionLeaseExpiresAt:   time.Now().UTC().Add(time.Minute),
		OutputsJSON:                `[]`,
		ErrorDetailsJSON:           `{}`,
	}
	require.NoError(t, DB.Create(&job).Error)
	claim, err := ClaimPlatformGenerationProviderRoute(jobID, route.Model, route.Mode)
	require.NoError(t, err)

	const rotatedCredential = "rotated-after-route-claim"
	assert.ErrorIs(t, RotateChannelCredentialSet(route.ChannelID, rotatedCredential), ErrPlatformGenerationChannelInUse)
	// Fault injection: production rotation must return ChannelInUse while this
	// admission is held. Create a valid encrypted immutable version, then bypass
	// that guard only by drifting the channel reference; no plaintext is stored.
	require.NoError(t, DB.Transaction(func(tx *gorm.DB) error {
		version, err := storeProviderChannelCredentialSetVersionTx(tx, route.ChannelID, rotatedCredential)
		if err != nil {
			return err
		}
		result := tx.Session(&gorm.Session{NewDB: true, SkipHooks: true}).Table("channels").
			Where("id = ?", route.ChannelID).Update("credential_set_version", version)
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return errors.New("fault-injected credential reference was not installed")
		}
		return nil
	}))
	_, err = BeginPlatformGenerationRouteSubmission(jobID, route.ID, job.SubmissionLeaseToken, claim.SubmissionToken)
	assert.ErrorIs(t, err, ErrPlatformGenerationProviderRouteUnavailable)

	admission, _, err := GetPlatformGenerationProviderRouteAssignment(jobID)
	require.NoError(t, err)
	assert.Equal(t, PlatformGenerationRouteAdmissionHeld, admission.State,
		"the provider-post token must remain unconsumed when live channel state changed")
	assert.True(t, admission.SlotHeld)

	released, err := ReleasePlatformGenerationProviderRoute(jobID, claim.SubmissionToken)
	require.NoError(t, err)
	assert.True(t, released)
}

func TestPlatformGenerationRouteConcurrentClaimOccupiesOneSlot(t *testing.T) {
	preparePlatformGenerationRouteTest(t)
	route := createPlatformGenerationRouteFixture(t, "route.concurrent", 10, 2)
	jobID := uuid.NewString()

	type claimResult struct {
		claim *PlatformGenerationRouteClaim
		err   error
	}
	results := make(chan claimResult, 2)
	start := make(chan struct{})
	var workers sync.WaitGroup
	workers.Add(2)
	for range 2 {
		go func() {
			defer workers.Done()
			<-start
			claim, err := ClaimPlatformGenerationProviderRoute(jobID, route.Model, route.Mode)
			results <- claimResult{claim: claim, err: err}
		}()
	}
	close(start)
	workers.Wait()
	close(results)

	successes := 0
	heldRejections := 0
	for result := range results {
		switch {
		case result.err == nil:
			successes++
			assert.NotNil(t, result.claim)
		case errors.Is(result.err, ErrPlatformGenerationRouteAdmissionHeld):
			heldRejections++
		default:
			require.NoError(t, result.err)
		}
	}
	assert.Equal(t, 1, successes)
	assert.Equal(t, 1, heldRejections)

	var persistedRoute PlatformGenerationProviderRoute
	require.NoError(t, DB.First(&persistedRoute, route.ID).Error)
	assert.Equal(t, 1, persistedRoute.ActiveCount)
}

func TestPlatformGenerationTerminalTransitionUsesPollFenceAndClosesUnknownSlot(t *testing.T) {
	preparePlatformGenerationRouteTest(t)
	route := createPlatformGenerationRouteFixture(t, "route.terminal", 10, 1)
	jobID := uuid.NewString()
	routeClaim, err := ClaimPlatformGenerationProviderRoute(jobID, route.Model, route.Mode)
	require.NoError(t, err)
	marked, err := MarkPlatformGenerationRouteSubmissionUnknown(jobID, routeClaim.SubmissionToken)
	require.NoError(t, err)
	require.True(t, marked)

	job := PlatformGenerationJob{
		ID:                         jobID,
		TenantID:                   uuid.NewString(),
		SourceClientID:             "platform",
		RequestID:                  "request-terminal",
		IdempotencyKey:             "idempotency-terminal",
		RequestHash:                strings.Repeat("b", 64),
		RequestJSON:                `{}`,
		Model:                      route.Model,
		Mode:                       route.Mode,
		ExpectedCapabilityRevision: "sha256:" + strings.Repeat("c", 64),
		CapabilityRevision:         "sha256:" + strings.Repeat("c", 64),
		Status:                     PlatformGenerationStatusReconciliationRequired,
		PollLeaseToken:             "current-poll-token",
		PollLeaseExpiresAt:         time.Now().UTC().Add(time.Minute),
		OutputsJSON:                `[]`,
		ErrorDetailsJSON:           `{}`,
	}
	require.NoError(t, DB.Create(&job).Error)

	won, err := CompletePlatformGenerationTerminal(jobID, "expired-poll-token", PlatformGenerationStatusReconciliationRequired, map[string]any{
		"status": PlatformGenerationStatusFailed,
	})
	require.NoError(t, err)
	assert.False(t, won)
	var persistedRoute PlatformGenerationProviderRoute
	require.NoError(t, DB.First(&persistedRoute, route.ID).Error)
	assert.Equal(t, 1, persistedRoute.ActiveCount, "an expired poll worker must not release the current route slot")
	require.NoError(t, DB.Model(&PlatformGenerationJob{}).Where("id = ?", jobID).
		Update("poll_lease_expires_at", time.Now().UTC().Add(-time.Second)).Error)
	won, err = CompletePlatformGenerationTerminal(jobID, "current-poll-token", PlatformGenerationStatusReconciliationRequired, map[string]any{
		"status": PlatformGenerationStatusFailed,
	})
	require.NoError(t, err)
	assert.False(t, won, "a matching token cannot write after its database-clock lease expires")
	require.NoError(t, DB.First(&persistedRoute, route.ID).Error)
	assert.Equal(t, 1, persistedRoute.ActiveCount)
	require.NoError(t, DB.Model(&PlatformGenerationJob{}).Where("id = ?", jobID).
		Update("poll_lease_expires_at", time.Now().UTC().Add(time.Minute)).Error)

	outcomeID := uuid.NewString()
	won, err = CompletePlatformGenerationTerminalWithOutcome(jobID, "current-poll-token", PlatformGenerationStatusReconciliationRequired, map[string]any{
		"status":        PlatformGenerationStatusTransferring,
		"progress":      95,
		"error_code":    "",
		"error_message": "",
	}, &PlatformProviderTerminalOutcome{
		ID:                outcomeID,
		RouteID:           route.ID,
		RelayJobID:        jobID,
		Outcome:           PlatformProviderOutcomeSucceeded,
		FailureOwner:      PlatformProviderFailureOwnerNone,
		OccurredAt:        time.Now().UTC(),
		ExternalReference: "provider-task-terminal",
	})
	require.NoError(t, err)
	assert.True(t, won)
	require.NoError(t, DB.First(&persistedRoute, route.ID).Error)
	assert.Zero(t, persistedRoute.ActiveCount)
	admission, _, err := GetPlatformGenerationProviderRouteAssignment(jobID)
	require.NoError(t, err)
	assert.Equal(t, PlatformGenerationRouteAdmissionFinished, admission.State)
	assert.False(t, admission.SlotHeld)
	var persistedJob PlatformGenerationJob
	require.NoError(t, DB.First(&persistedJob, "id = ?", jobID).Error)
	assert.Equal(t, PlatformGenerationStatusTransferring, persistedJob.Status)
	assert.Empty(t, persistedJob.PollLeaseToken)
	var persistedOutcome PlatformProviderTerminalOutcome
	require.NoError(t, DB.First(&persistedOutcome, "id = ?", outcomeID).Error)
	assert.Equal(t, jobID, persistedOutcome.RelayJobID)
	assert.Equal(t, route.RouteKey, persistedOutcome.RouteKey)
}

func TestPlatformGenerationProviderCooldownOnlyBlocksNewAdmissionsAndNeverShortens(t *testing.T) {
	preparePlatformGenerationRouteTest(t)
	route := createPlatformGenerationRouteFixture(t, "route.cooldown", 20, 4)

	type existingTask struct {
		jobID string
		token string
	}
	claimExisting := func(label string) existingTask {
		jobID := uuid.NewString()
		_, err := ClaimPlatformGenerationProviderRoute(jobID, route.Model, route.Mode)
		require.NoError(t, err)
		pollToken := "poll-" + label
		require.NoError(t, DB.Create(&PlatformGenerationJob{
			ID:                         jobID,
			TenantID:                   uuid.NewString(),
			SourceClientID:             "platform",
			RequestID:                  "request-" + label,
			IdempotencyKey:             "idempotency-" + label + "-" + jobID,
			RequestHash:                strings.Repeat("a", 64),
			RequestJSON:                `{}`,
			Model:                      route.Model,
			Mode:                       route.Mode,
			ExpectedCapabilityRevision: "sha256:" + strings.Repeat("b", 64),
			CapabilityRevision:         "sha256:" + strings.Repeat("b", 64),
			Status:                     PlatformGenerationStatusProcessing,
			PollLeaseToken:             pollToken,
			PollLeaseExpiresAt:         time.Now().UTC().Add(time.Minute),
			OutputsJSON:                `[]`,
			ErrorDetailsJSON:           `{}`,
		}).Error)
		return existingTask{jobID: jobID, token: pollToken}
	}

	firstFailure := claimExisting("cooldown-first")
	secondFailure := claimExisting("cooldown-second")
	existingSuccess := claimExisting("cooldown-success")
	completeFailure := func(task existingTask, cooldown time.Duration) {
		won, err := CompletePlatformGenerationTerminalWithOutcomePolicy(
			task.jobID,
			task.token,
			PlatformGenerationStatusProcessing,
			map[string]any{"status": PlatformGenerationStatusFailed},
			&PlatformProviderTerminalOutcome{
				ID:                uuid.NewString(),
				RouteID:           route.ID,
				RelayJobID:        task.jobID,
				Outcome:           PlatformProviderOutcomeFailed,
				FailureOwner:      PlatformProviderFailureOwnerProvider,
				FailureCode:       "provider_unavailable",
				OccurredAt:        time.Now().UTC(),
				ExternalReference: "provider-task:" + task.jobID,
			},
			1,
			cooldown,
		)
		require.NoError(t, err)
		require.True(t, won)
	}

	completeFailure(firstFailure, 5*time.Minute)
	var afterLongCooldown PlatformGenerationProviderRoute
	require.NoError(t, DB.First(&afterLongCooldown, route.ID).Error)
	require.NotNil(t, afterLongCooldown.CoolingUntil)
	longCooldownUntil := afterLongCooldown.CoolingUntil.UTC()

	completeFailure(secondFailure, time.Second)
	var afterShortCooldown PlatformGenerationProviderRoute
	require.NoError(t, DB.First(&afterShortCooldown, route.ID).Error)
	require.NotNil(t, afterShortCooldown.CoolingUntil)
	assert.False(t, afterShortCooldown.CoolingUntil.UTC().Before(longCooldownUntil),
		"a shorter later failure must not shorten a shared database cooldown")
	assert.Equal(t, 2, afterShortCooldown.ConsecutiveFailures)

	_, err := ClaimPlatformGenerationProviderRoute(uuid.NewString(), route.Model, route.Mode)
	assert.ErrorIs(t, err, ErrPlatformGenerationProviderRouteUnavailable,
		"cooldown rejects only a new admission")

	won, err := CompletePlatformGenerationTerminalWithOutcomePolicy(
		existingSuccess.jobID,
		existingSuccess.token,
		PlatformGenerationStatusProcessing,
		map[string]any{"status": PlatformGenerationStatusTransferring},
		&PlatformProviderTerminalOutcome{
			ID:                uuid.NewString(),
			RouteID:           route.ID,
			RelayJobID:        existingSuccess.jobID,
			Outcome:           PlatformProviderOutcomeSucceeded,
			FailureOwner:      PlatformProviderFailureOwnerNone,
			OccurredAt:        time.Now().UTC(),
			ExternalReference: "provider-task:" + existingSuccess.jobID,
		},
		1,
		5*time.Minute,
	)
	require.NoError(t, err)
	require.True(t, won)

	var recovered PlatformGenerationProviderRoute
	require.NoError(t, DB.First(&recovered, route.ID).Error)
	assert.Nil(t, recovered.CoolingUntil)
	assert.Zero(t, recovered.ConsecutiveFailures)
	assert.Empty(t, recovered.LastErrorCode)
	_, err = ClaimPlatformGenerationProviderRoute(uuid.NewString(), route.Model, route.Mode)
	require.NoError(t, err, "an explicit provider success reopens new admission")
}

func TestPlatformGenerationStalePollAndReconciliationTokensCannotWriteBack(t *testing.T) {
	preparePlatformGenerationRouteTest(t)
	job := PlatformGenerationJob{
		ID:                         uuid.NewString(),
		TenantID:                   uuid.NewString(),
		SourceClientID:             "platform",
		RequestID:                  "request-stale",
		IdempotencyKey:             "idempotency-stale",
		RequestHash:                strings.Repeat("d", 64),
		RequestJSON:                `{}`,
		Model:                      "route.stale",
		Mode:                       "text_to_video",
		ExpectedCapabilityRevision: "sha256:" + strings.Repeat("e", 64),
		CapabilityRevision:         "sha256:" + strings.Repeat("e", 64),
		Status:                     PlatformGenerationStatusProcessing,
		Progress:                   12,
		PollLeaseToken:             "current-processing-token",
		OutputsJSON:                `[]`,
		ErrorDetailsJSON:           `{}`,
	}
	require.NoError(t, DB.Create(&job).Error)

	won, err := CompletePlatformGenerationPoll(job.ID, "expired-processing-token", map[string]any{
		"status":   PlatformGenerationStatusFailed,
		"progress": 100,
	})
	require.NoError(t, err)
	assert.False(t, won)
	var persisted PlatformGenerationJob
	require.NoError(t, DB.First(&persisted, "id = ?", job.ID).Error)
	assert.Equal(t, PlatformGenerationStatusProcessing, persisted.Status)
	assert.Equal(t, 12, persisted.Progress)
	assert.Equal(t, "current-processing-token", persisted.PollLeaseToken)

	require.NoError(t, DB.Model(&PlatformGenerationJob{}).Where("id = ?", job.ID).Updates(map[string]any{
		"status":           PlatformGenerationStatusReconciliationRequired,
		"poll_lease_token": "current-reconciliation-token",
	}).Error)
	won, err = CompletePlatformGenerationReconciliation(job.ID, "expired-reconciliation-token", map[string]any{
		"status":   PlatformGenerationStatusProcessing,
		"progress": 77,
	})
	require.NoError(t, err)
	assert.False(t, won)
	require.NoError(t, DB.First(&persisted, "id = ?", job.ID).Error)
	assert.Equal(t, PlatformGenerationStatusReconciliationRequired, persisted.Status)
	assert.Equal(t, 12, persisted.Progress)
	assert.Equal(t, "current-reconciliation-token", persisted.PollLeaseToken)
}
