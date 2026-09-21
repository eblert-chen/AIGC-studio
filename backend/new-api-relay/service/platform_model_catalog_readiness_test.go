package service

import (
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func installCurrentPlatformModelCatalogReadinessForTest(t *testing.T, now *time.Time) {
	t.Helper()
	resetPlatformRelayModelCatalogReadinessForTest()
	previousNow := platformRouteAcceptanceNow
	platformRouteAcceptanceNow = func() time.Time { return *now }
	t.Cleanup(func() {
		platformRouteAcceptanceNow = previousNow
		resetPlatformRelayModelCatalogReadinessForTest()
	})
	installPlatformRelayModelCatalogReadinessForTest(
		1, "sha256:"+strings.Repeat("a", 64), 7, *now, now.Add(time.Hour),
	)
}

func TestPlatformRelayModelCatalogReadinessSnapshotIsCanonicalAndLockFree(t *testing.T) {
	now := time.Date(2026, 8, 29, 3, 0, 0, 0, time.UTC)
	installCurrentPlatformModelCatalogReadinessForTest(t, &now)

	// Simulate a concurrent full config reload holding the mutex while it parses
	// and verifies external evidence. The readiness snapshot must not wait for it.
	platformRelayConfigCache.Lock()
	defer platformRelayConfigCache.Unlock()
	started := time.Now()
	status := GetPlatformRelayModelCatalogReadinessStatus()
	require.Less(t, time.Since(started), 100*time.Millisecond)
	require.True(t, status.Current)
	require.True(t, status.Configured)
	require.Equal(t, 1, status.ModelCount)
	require.Equal(t, uint64(7), status.ConfigGeneration)
	require.NotEmpty(t, status.CatalogRevision)
	require.Equal(t, now, status.VerifiedAt)
	require.Equal(t, now.Add(time.Hour), status.EvidenceExpiresAt)
	require.Empty(t, status.ErrorCode)
}

func TestSuccessfulPlatformRelayCatalogLoadPublishesReadinessGeneration(t *testing.T) {
	previousNow := platformRouteAcceptanceNow
	now := time.Date(2026, 8, 29, 3, 5, 0, 0, time.UTC)
	platformRouteAcceptanceNow = func() time.Time { return now }
	reset := func() {
		platformRelayConfigCache.Lock()
		platformRelayConfigCache.snapshot = platformRelayConfigSnapshot{}
		platformRelayConfigCache.lastObserved = time.Time{}
		platformRelayConfigCache.clockFailed = false
		platformRelayConfigCache.generation = 0
		platformRelayConfigCache.Unlock()
		resetPlatformRelayModelCatalogReadinessForTest()
	}
	reset()
	t.Cleanup(func() {
		platformRouteAcceptanceNow = previousNow
		reset()
	})
	t.Setenv("APP_ENV", "")
	t.Setenv("DEPLOYMENT_ENV", "")
	t.Setenv("ENVIRONMENT", "")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", `{}`)
	t.Setenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_PUBLIC_KEYS_JSON", "")
	t.Setenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEY", "")
	t.Setenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEYS_JSON", "")
	t.Setenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_SIGNING_KEY", "")
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", "")
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", `{"catalog-model":{"schema_version":1,"modes":{"text_to_video":{"input_media_types":[],"supports_face":false,"required_resource_keys":[],"limits":{"max_prompt_length":100,"max_images":0,"max_videos":0,"max_audio":0,"duration_seconds":[5],"aspect_ratios":["16:9"],"resolutions":["720p"],"output_counts":[1]}}}}}`)

	catalog, err := GetPlatformRelayModelCatalog()
	require.NoError(t, err)
	require.Len(t, catalog.Data, 12)
	status := GetPlatformRelayModelCatalogReadinessStatus()
	require.True(t, status.Current)
	require.Equal(t, catalog.CatalogRevision, status.CatalogRevision)
	require.Equal(t, uint64(1), status.ConfigGeneration)
	require.Equal(t, 12, status.ModelCount)
}

func TestPlatformRelayModelCatalogReadinessFailsClosedForMissingExpiredAndConfigDrift(t *testing.T) {
	previousNow := platformRouteAcceptanceNow
	t.Cleanup(func() {
		platformRouteAcceptanceNow = previousNow
		resetPlatformRelayModelCatalogReadinessForTest()
	})

	resetPlatformRelayModelCatalogReadinessForTest()
	missing := GetPlatformRelayModelCatalogReadinessStatus()
	require.False(t, missing.Current)
	require.Equal(t, platformModelCatalogReadinessMissing, missing.ErrorCode)

	now := time.Date(2026, 8, 29, 3, 10, 0, 0, time.UTC)
	platformRouteAcceptanceNow = func() time.Time { return now }
	installPlatformRelayModelCatalogReadinessForTest(1, "sha256:expired", 2, now.Add(-time.Hour), now)
	expired := GetPlatformRelayModelCatalogReadinessStatus()
	require.False(t, expired.Current)
	require.Equal(t, platformModelCatalogReadinessExpired, expired.ErrorCode)

	resetPlatformRelayModelCatalogReadinessForTest()
	originalRoutes := os.Getenv("RELAY_COMPAT_MODEL_ROUTES_JSON")
	t.Cleanup(func() { _ = os.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", originalRoutes) })
	installPlatformRelayModelCatalogReadinessForTest(1, "sha256:drift", 3, now, now.Add(time.Hour))
	require.NoError(t, os.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", originalRoutes+" "))
	drifted := GetPlatformRelayModelCatalogReadinessStatus()
	require.False(t, drifted.Current)
	require.Equal(t, platformModelCatalogReadinessConfigChanged, drifted.ErrorCode)
	// Returning the environment to its old value cannot revive a stale proof.
	require.NoError(t, os.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", originalRoutes))
	require.False(t, GetPlatformRelayModelCatalogReadinessStatus().Current)
}

func TestPlatformRelayModelCatalogReadinessClockRollbackLatchesClosed(t *testing.T) {
	now := time.Date(2026, 8, 29, 3, 20, 0, 0, time.UTC)
	installCurrentPlatformModelCatalogReadinessForTest(t, &now)
	require.True(t, GetPlatformRelayModelCatalogReadinessStatus().Current)
	now = now.Add(-time.Second)
	rolledBack := GetPlatformRelayModelCatalogReadinessStatus()
	require.False(t, rolledBack.Current)
	require.Equal(t, platformModelCatalogReadinessClockFailed, rolledBack.ErrorCode)
	now = now.Add(2 * time.Hour)
	require.False(t, GetPlatformRelayModelCatalogReadinessStatus().Current)
}

func TestPlatformRelayModelCatalogReadinessConcurrentClockSamplesDoNotFakeRollback(t *testing.T) {
	base := time.Date(2026, 8, 29, 3, 30, 0, 0, time.UTC)
	resetPlatformRelayModelCatalogReadinessForTest()
	previousNow := platformRouteAcceptanceNow
	t.Cleanup(func() {
		platformRouteAcceptanceNow = previousNow
		resetPlatformRelayModelCatalogReadinessForTest()
	})
	installPlatformRelayModelCatalogReadinessForTest(
		1, "sha256:"+strings.Repeat("b", 64), 8, base, base.Add(time.Hour),
	)

	firstSampled := make(chan struct{})
	releaseFirst := make(chan struct{})
	var calls atomic.Int32
	platformRouteAcceptanceNow = func() time.Time {
		if calls.Add(1) == 1 {
			close(firstSampled)
			<-releaseFirst
			return base.Add(time.Second)
		}
		return base.Add(2 * time.Second)
	}
	results := make(chan PlatformRelayModelCatalogReadinessStatus, 2)
	go func() { results <- GetPlatformRelayModelCatalogReadinessStatus() }()
	select {
	case <-firstSampled:
	case <-time.After(time.Second):
		t.Fatal("first readiness call did not sample the clock")
	}
	go func() { results <- GetPlatformRelayModelCatalogReadinessStatus() }()
	select {
	case result := <-results:
		t.Fatalf("concurrent clock comparison was not serialized: %+v", result)
	case <-time.After(20 * time.Millisecond):
	}
	close(releaseFirst)
	for range 2 {
		select {
		case result := <-results:
			require.True(t, result.Current)
			require.Empty(t, result.ErrorCode)
		case <-time.After(time.Second):
			t.Fatal("readiness call did not finish")
		}
	}
	require.False(t, platformRelayModelCatalogReadinessClockFailed.Load())
}
