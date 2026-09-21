package controller

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/glebarez/sqlite"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func TestCollectPlatformRelayConcurrentReadinessResultsReturnsAtCancellationAndLateSendsDoNotBlock(t *testing.T) {
	slots := []string{"a", "b", "c", "d", "e", "f", "g", "h"}
	results := make(chan platformRelayConcurrentReadinessResult, len(slots))
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	started := time.Now()
	collected, timedOut, err := collectPlatformRelayConcurrentReadinessResults(ctx, slots, results)
	require.Less(t, time.Since(started), 100*time.Millisecond)
	require.True(t, timedOut)
	require.NoError(t, err)
	require.Empty(t, collected)

	var children sync.WaitGroup
	children.Add(len(slots))
	for index := len(slots) - 1; index >= 0; index-- {
		slot := slots[index]
		go func() {
			defer children.Done()
			results <- platformRelayConcurrentReadinessResult{
				slot:         slot,
				dependencies: []platformRelayDependencyHealth{{Name: slot, State: "unavailable", Details: map[string]any{}}},
			}
		}()
	}
	childrenDone := make(chan struct{})
	go func() {
		children.Wait()
		close(childrenDone)
	}()
	select {
	case <-childrenDone:
	case <-time.After(time.Second):
		t.Fatal("late readiness child sends blocked after collector returned")
	}
}

func TestCollectPlatformRelayConcurrentReadinessResultsIsOrderIndependentAndJoinsSlowHalf(t *testing.T) {
	slots := []string{"a", "b", "c", "d", "e", "f", "g", "h"}
	for _, test := range []struct {
		name     string
		fastHalf []string
		slowHalf []string
	}{
		{name: "first-four-fast", fastHalf: slots[:4], slowHalf: slots[4:]},
		{name: "last-four-fast", fastHalf: slots[4:], slowHalf: slots[:4]},
	} {
		t.Run(test.name, func(t *testing.T) {
			results := make(chan platformRelayConcurrentReadinessResult, len(slots))
			for _, slot := range test.fastHalf {
				results <- platformRelayConcurrentReadinessResult{slot: slot}
			}
			releaseSlow := make(chan struct{})
			for _, slot := range test.slowHalf {
				slot := slot
				go func() {
					<-releaseSlow
					results <- platformRelayConcurrentReadinessResult{slot: slot}
				}()
			}
			type collectionOutcome struct {
				collected map[string]platformRelayConcurrentReadinessResult
				err       error
			}
			done := make(chan collectionOutcome, 1)
			go func() {
				collected, _, err := collectPlatformRelayConcurrentReadinessResults(context.Background(), slots, results)
				done <- collectionOutcome{collected: collected, err: err}
			}()
			select {
			case <-done:
				t.Fatal("collector returned before the slow half joined")
			case <-time.After(20 * time.Millisecond):
			}
			close(releaseSlow)
			outcome := <-done
			require.NoError(t, outcome.err)
			require.Len(t, outcome.collected, len(slots))
		})
	}
}

func TestCollectPlatformRelayConcurrentReadinessResultsRejectsDuplicateAndUnknownSlots(t *testing.T) {
	slots := []string{"a", "b"}
	results := make(chan platformRelayConcurrentReadinessResult, len(slots))
	results <- platformRelayConcurrentReadinessResult{slot: "a"}
	results <- platformRelayConcurrentReadinessResult{slot: "unknown"}
	collected, _, err := collectPlatformRelayConcurrentReadinessResults(context.Background(), slots, results)
	require.Error(t, err)
	require.Len(t, collected, 1)

	results = make(chan platformRelayConcurrentReadinessResult, len(slots))
	results <- platformRelayConcurrentReadinessResult{slot: "a"}
	results <- platformRelayConcurrentReadinessResult{slot: "a"}
	collected, _, err = collectPlatformRelayConcurrentReadinessResults(context.Background(), slots, results)
	require.Error(t, err)
	require.Len(t, collected, 1)
}

func TestLaunchPlatformRelayReadinessChildRecoversWithCanonicalResult(t *testing.T) {
	results := make(chan platformRelayConcurrentReadinessResult, 1)
	fallback := platformRelayConcurrentReadinessResult{
		degraded: true,
		dependencies: []platformRelayDependencyHealth{{
			Name: "provider_runtime", State: "degraded", Details: map[string]any{"inspection_available": false},
		}},
	}
	launchPlatformRelayReadinessChild(context.Background(), "provider", fallback, results, func(context.Context) platformRelayConcurrentReadinessResult {
		panic("synthetic secret must not escape")
	})
	select {
	case result := <-results:
		require.Equal(t, "provider", result.slot)
		require.False(t, result.unavailable)
		require.True(t, result.degraded)
		require.Equal(t, fallback.dependencies, result.dependencies)
	case <-time.After(time.Second):
		t.Fatal("panic-safe readiness child did not return its canonical result")
	}
}

func TestLaunchPlatformRelayReadinessChildRejectsMalformedEvidence(t *testing.T) {
	fallback := platformRelayConcurrentReadinessResult{
		unavailable: true,
		dependencies: []platformRelayDependencyHealth{{
			Name: "database_policy", State: "unavailable", Details: map[string]any{"ready": false},
		}},
	}
	for _, test := range []struct {
		name      string
		candidate platformRelayConcurrentReadinessResult
	}{
		{name: "zero", candidate: platformRelayConcurrentReadinessResult{}},
		{name: "missing details", candidate: platformRelayConcurrentReadinessResult{dependencies: []platformRelayDependencyHealth{{Name: "database_policy", State: "healthy"}}}},
		{name: "wrong name", candidate: platformRelayConcurrentReadinessResult{dependencies: []platformRelayDependencyHealth{{Name: "wrong", State: "healthy", Details: map[string]any{}}}}},
		{name: "classification mismatch", candidate: platformRelayConcurrentReadinessResult{unavailable: true, dependencies: []platformRelayDependencyHealth{{Name: "database_policy", State: "healthy", Details: map[string]any{}}}}},
	} {
		t.Run(test.name, func(t *testing.T) {
			results := make(chan platformRelayConcurrentReadinessResult, 1)
			launchPlatformRelayReadinessChild(context.Background(), "policy", fallback, results, func(context.Context) platformRelayConcurrentReadinessResult {
				return test.candidate
			})
			result := <-results
			require.True(t, result.unavailable)
			require.Equal(t, fallback.dependencies, result.dependencies)
		})
	}
}

func TestPlatformRelayReadyDeadlineKeepsWireShapeAndCancelsChildrenWithinHardBound(t *testing.T) {
	previousDB := model.DB
	previousRedisEnabled := common.RedisEnabled
	previousReadiness := getProtectedPlatformRelayAPIReadiness
	previousProvider := getPlatformProviderReadinessSummary
	previousArtifacts := getPlatformArtifactReadinessStatus
	t.Cleanup(func() {
		model.DB = previousDB
		common.RedisEnabled = previousRedisEnabled
		getProtectedPlatformRelayAPIReadiness = previousReadiness
		getPlatformProviderReadinessSummary = previousProvider
		getPlatformArtifactReadinessStatus = previousArtifacts
	})
	database, err := gorm.Open(sqlite.Open("file:bounded-platform-ready?mode=memory&cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	model.DB = database
	common.RedisEnabled = false
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_COMPAT_ENABLED", "true")
	t.Setenv("RELAY_COMPAT_WORKER_ENABLED", "false")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	readinessCalls := 0
	getProtectedPlatformRelayAPIReadiness = func(context.Context) (model.RelaySchemaStatus, model.RelayRuntimeDatabaseRoleStatus, error) {
		readinessCalls++
		return model.RelaySchemaStatus{Classification: model.RelaySchemaStatusCurrent},
			model.RelayRuntimeDatabaseRoleStatus{Required: true, State: "healthy", Role: "relay_runtime"}, nil
	}
	getPlatformArtifactReadinessStatus = func() (service.PlatformArtifactReadinessStatus, error) {
		return service.PlatformArtifactReadinessStatus{
			Current: true, CleanupWorkerRunning: true,
		}, nil
	}
	providerEntered := make(chan struct{})
	providerExited := make(chan struct{})
	getPlatformProviderReadinessSummary = func(ctx context.Context) (service.PlatformProviderReadinessSummary, error) {
		close(providerEntered)
		<-ctx.Done()
		close(providerExited)
		return service.PlatformProviderReadinessSummary{}, ctx.Err()
	}

	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/health/ready", nil)
	context, _ := gin.CreateTestContext(recorder)
	context.Request = request
	started := time.Now()
	PlatformRelayReady(context)
	elapsed := time.Since(started)
	<-providerEntered
	select {
	case <-providerExited:
	case <-time.After(500 * time.Millisecond):
		t.Fatal("context-aware readiness child did not exit after the hard request deadline")
	}
	require.GreaterOrEqual(t, elapsed, 2400*time.Millisecond)
	require.Less(t, elapsed, 3*time.Second)
	require.Equal(t, 1, readinessCalls)

	var health platformRelayHealthResponse
	require.NoError(t, common.Unmarshal(recorder.Body.Bytes(), &health))
	require.Equal(t, http.StatusServiceUnavailable, recorder.Code)
	require.Equal(t, "unavailable", health.State)
	names := make([]string, 0, len(health.Dependencies))
	states := make(map[string]string, len(health.Dependencies))
	for _, dependency := range health.Dependencies {
		names = append(names, dependency.Name)
		states[dependency.Name] = dependency.State
	}
	require.Equal(t, []string{
		"database", "database_schema", "database_role", "codex_credential_lifecycle",
		"native_billing_lifecycle", "platform_service_principals", "production_setup",
		"platform_relay_compat", "redis", "artifact_store", "artifact_cleanup",
		"generation_callbacks", "provider_runtime",
	}, names)
	require.Equal(t, "degraded", states["provider_runtime"])
}

func TestPlatformRelayReadyDoesNotWaitForBlockedCatalogReloadPath(t *testing.T) {
	previousDB := model.DB
	previousReadiness := getProtectedPlatformRelayAPIReadiness
	previousCatalog := getPlatformRelayModelCatalogReadinessStatus
	previousArtifacts := getPlatformArtifactReadinessStatus
	previousProvider := getPlatformProviderReadinessSummary
	t.Cleanup(func() {
		model.DB = previousDB
		getProtectedPlatformRelayAPIReadiness = previousReadiness
		getPlatformRelayModelCatalogReadinessStatus = previousCatalog
		getPlatformArtifactReadinessStatus = previousArtifacts
		getPlatformProviderReadinessSummary = previousProvider
	})
	database, err := gorm.Open(sqlite.Open("file:blocked-catalog-platform-ready?mode=memory&cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	model.DB = database
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_COMPAT_ENABLED", "true")
	t.Setenv("RELAY_COMPAT_WORKER_ENABLED", "false")
	getProtectedPlatformRelayAPIReadiness = func(context.Context) (model.RelaySchemaStatus, model.RelayRuntimeDatabaseRoleStatus, error) {
		return model.RelaySchemaStatus{Classification: model.RelaySchemaStatusCurrent},
			model.RelayRuntimeDatabaseRoleStatus{Required: true, State: "healthy", Role: "relay_runtime"}, nil
	}
	getPlatformArtifactReadinessStatus = func() (service.PlatformArtifactReadinessStatus, error) {
		return service.PlatformArtifactReadinessStatus{Current: true, CleanupWorkerRunning: true}, nil
	}
	getPlatformProviderReadinessSummary = func(context.Context) (service.PlatformProviderReadinessSummary, error) {
		return service.PlatformProviderReadinessSummary{}, nil
	}
	catalogEntered := make(chan struct{})
	releaseCatalog := make(chan struct{})
	catalogExited := make(chan struct{})
	getPlatformRelayModelCatalogReadinessStatus = func() service.PlatformRelayModelCatalogReadinessStatus {
		close(catalogEntered)
		<-releaseCatalog
		close(catalogExited)
		return service.PlatformRelayModelCatalogReadinessStatus{}
	}

	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/health/ready", nil)
	ginContext, _ := gin.CreateTestContext(recorder)
	ginContext.Request = request
	started := time.Now()
	PlatformRelayReady(ginContext)
	elapsed := time.Since(started)
	<-catalogEntered
	require.Less(t, elapsed, 3*time.Second)
	require.Equal(t, http.StatusServiceUnavailable, recorder.Code)
	close(releaseCatalog)
	select {
	case <-catalogExited:
	case <-time.After(time.Second):
		t.Fatal("blocked catalog child did not exit after release")
	}

	var health platformRelayHealthResponse
	require.NoError(t, common.Unmarshal(recorder.Body.Bytes(), &health))
	require.Len(t, health.Dependencies, 13)
	require.Equal(t, "platform_relay_compat", health.Dependencies[7].Name)
	require.Equal(t, "unavailable", health.Dependencies[7].State)
}

func TestPlatformRelayReadyChildPanicsKeepCanonicalWireShape(t *testing.T) {
	previousDB := model.DB
	previousRedisEnabled := common.RedisEnabled
	previousReadiness := getProtectedPlatformRelayAPIReadiness
	previousProvider := getPlatformProviderReadinessSummary
	previousArtifacts := getPlatformArtifactReadinessStatus
	t.Cleanup(func() {
		model.DB = previousDB
		common.RedisEnabled = previousRedisEnabled
		getProtectedPlatformRelayAPIReadiness = previousReadiness
		getPlatformProviderReadinessSummary = previousProvider
		getPlatformArtifactReadinessStatus = previousArtifacts
	})

	database, err := gorm.Open(sqlite.Open("file:panic-platform-ready?mode=memory&cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	model.DB = database
	common.RedisEnabled = false
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_COMPAT_ENABLED", "true")
	t.Setenv("RELAY_COMPAT_WORKER_ENABLED", "false")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	getProtectedPlatformRelayAPIReadiness = func(context.Context) (model.RelaySchemaStatus, model.RelayRuntimeDatabaseRoleStatus, error) {
		return model.RelaySchemaStatus{Classification: model.RelaySchemaStatusCurrent},
			model.RelayRuntimeDatabaseRoleStatus{Required: true, State: "healthy", Role: "relay_runtime"}, nil
	}

	canonicalNames := []string{
		"database", "database_schema", "database_role", "codex_credential_lifecycle",
		"native_billing_lifecycle", "platform_service_principals", "production_setup",
		"platform_relay_compat", "redis", "artifact_store", "artifact_cleanup",
		"generation_callbacks", "provider_runtime",
	}
	for _, test := range []struct {
		name             string
		panicArtifacts   bool
		expectedProvider string
	}{
		{name: "core artifact panic", panicArtifacts: true, expectedProvider: "degraded"},
		{name: "provider observation panic", expectedProvider: "degraded"},
	} {
		t.Run(test.name, func(t *testing.T) {
			getPlatformArtifactReadinessStatus = func() (service.PlatformArtifactReadinessStatus, error) {
				if test.panicArtifacts {
					panic("synthetic artifact secret")
				}
				return service.PlatformArtifactReadinessStatus{Current: true, CleanupWorkerRunning: true}, nil
			}
			getPlatformProviderReadinessSummary = func(context.Context) (service.PlatformProviderReadinessSummary, error) {
				panic("synthetic provider secret")
			}

			recorder := httptest.NewRecorder()
			request := httptest.NewRequest(http.MethodGet, "/health/ready", nil)
			ginContext, _ := gin.CreateTestContext(recorder)
			ginContext.Request = request
			PlatformRelayReady(ginContext)

			var health platformRelayHealthResponse
			require.NoError(t, common.Unmarshal(recorder.Body.Bytes(), &health))
			require.Len(t, health.Dependencies, len(canonicalNames))
			names := make([]string, 0, len(health.Dependencies))
			states := make(map[string]string, len(health.Dependencies))
			for _, dependency := range health.Dependencies {
				names = append(names, dependency.Name)
				states[dependency.Name] = dependency.State
			}
			require.Equal(t, canonicalNames, names)
			require.Equal(t, test.expectedProvider, states["provider_runtime"])
			if test.panicArtifacts {
				require.Equal(t, "unavailable", states["artifact_store"])
				require.Equal(t, "unavailable", states["artifact_cleanup"])
				require.Equal(t, http.StatusServiceUnavailable, recorder.Code)
			}
		})
	}
}
