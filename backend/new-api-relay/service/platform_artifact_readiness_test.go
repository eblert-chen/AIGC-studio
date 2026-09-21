package service

import (
	"context"
	"errors"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/model"
	"github.com/glebarez/sqlite"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

type controlledPlatformArtifactReadinessStore struct {
	kind        string
	bindingID   string
	persistent  bool
	health      func(context.Context) error
	healthCalls atomic.Int32
	closeCalls  atomic.Int32
}

func (store *controlledPlatformArtifactReadinessStore) Kind() string      { return store.kind }
func (store *controlledPlatformArtifactReadinessStore) BindingID() string { return store.bindingID }
func (store *controlledPlatformArtifactReadinessStore) Persistent() bool  { return store.persistent }
func (*controlledPlatformArtifactReadinessStore) Put(context.Context, PlatformArtifactPutInput) (PlatformStoredArtifact, error) {
	return PlatformStoredArtifact{}, nil
}
func (*controlledPlatformArtifactReadinessStore) Delete(context.Context, string) error { return nil }
func (*controlledPlatformArtifactReadinessStore) IssueSignedDownload(context.Context, string, time.Duration) (PlatformIssuedArtifactDownload, error) {
	return PlatformIssuedArtifactDownload{}, nil
}
func (store *controlledPlatformArtifactReadinessStore) Healthcheck(ctx context.Context) error {
	store.healthCalls.Add(1)
	if store.health != nil {
		return store.health(ctx)
	}
	return nil
}
func (store *controlledPlatformArtifactReadinessStore) Close() { store.closeCalls.Add(1) }

func newControlledPlatformArtifactReadinessStore(bindingByte string) *controlledPlatformArtifactReadinessStore {
	return &controlledPlatformArtifactReadinessStore{
		kind:       "cleanup_test",
		bindingID:  strings.Repeat(bindingByte, 64),
		persistent: true,
	}
}

func preparePlatformArtifactReadinessTest(t *testing.T) {
	t.Helper()
	resetPlatformArtifactReadinessForTest()
	resetPlatformArtifactCleanupWorkerStatusForTest()
	t.Cleanup(resetPlatformArtifactReadinessForTest)
	t.Cleanup(resetPlatformArtifactCleanupWorkerStatusForTest)
	t.Setenv("RELAY_COMPAT_ENABLED", "false")
	t.Setenv("RELAY_COMPAT_WORKER_ENABLED", "false")
	t.Setenv("RELAY_ARTIFACT_STORE", "cleanup_test")
}

func TestPlatformArtifactReadinessGetterIsPureFastAndOptionalConfigBound(t *testing.T) {
	preparePlatformArtifactReadinessTest(t)
	now := time.Date(2026, time.August, 29, 9, 0, 0, 0, time.UTC)
	platformArtifactReadinessClock = func() time.Time { return now }
	var factoryCalls atomic.Int32
	var maintenanceCalls atomic.Int32
	platformArtifactReadinessStoreFactory = func() (PlatformArtifactStore, error) {
		factoryCalls.Add(1)
		return nil, errors.New("getter must not construct a store")
	}
	platformArtifactReadinessMaintenanceRequired = func(context.Context, *gorm.DB) (bool, error) {
		maintenanceCalls.Add(1)
		return false, errors.New("getter must not query maintenance state")
	}
	require.NoError(t, publishPlatformArtifactReadinessProof(false, nil))
	markPlatformArtifactCleanupWorkerStarted(now)
	markPlatformArtifactCleanupWorkerSuccess(now)

	started := time.Now()
	for range 1000 {
		status, err := GetPlatformArtifactReadinessStatus()
		require.NoError(t, err)
		require.True(t, status.Current)
		require.False(t, status.Required)
	}
	elapsed := time.Since(started)
	assert.Less(t, elapsed/1000, time.Millisecond)
	assert.Zero(t, factoryCalls.Load())
	assert.Zero(t, maintenanceCalls.Load())

	t.Setenv("RELAY_ARTIFACT_STORE", "changed_cleanup_test")
	status, err := GetPlatformArtifactReadinessStatus()
	require.Error(t, err)
	assert.False(t, status.Current)
}

func TestPlatformArtifactReadinessMaintenanceValidationCanBlockWithoutBlockingGetter(t *testing.T) {
	preparePlatformArtifactReadinessTest(t)
	now := time.Date(2026, time.August, 29, 9, 30, 0, 0, time.UTC)
	platformArtifactReadinessClock = func() time.Time { return now }
	platformArtifactReadinessMaintenanceRequired = func(context.Context, *gorm.DB) (bool, error) { return true, nil }
	store := newControlledPlatformArtifactReadinessStore("a")
	healthStarted := make(chan struct{})
	healthRelease := make(chan struct{})
	var once sync.Once
	store.health = func(context.Context) error {
		once.Do(func() { close(healthStarted) })
		<-healthRelease
		return nil
	}
	var factoryCalls atomic.Int32
	platformArtifactReadinessStoreFactory = func() (PlatformArtifactStore, error) {
		factoryCalls.Add(1)
		return store, nil
	}

	result := make(chan error, 1)
	go func() { result <- ValidatePlatformArtifactCleanupMaintenanceConfiguration() }()
	select {
	case <-healthStarted:
	case <-time.After(time.Second):
		t.Fatal("maintenance healthcheck did not start")
	}
	started := time.Now()
	_, err := GetPlatformArtifactReadinessStatus()
	assert.Error(t, err)
	assert.Less(t, time.Since(started), time.Millisecond)
	assert.EqualValues(t, 1, factoryCalls.Load())
	assert.EqualValues(t, 1, store.healthCalls.Load())
	close(healthRelease)
	require.NoError(t, <-result)
	assert.EqualValues(t, 1, store.closeCalls.Load())

	status, err := GetPlatformArtifactReadinessStatus()
	require.Error(t, err, "the cleanup worker has not yet installed the same live store")
	assert.True(t, status.Required)
	assert.True(t, status.Configured)
	assert.True(t, status.Persistent)
}

func TestPlatformArtifactReadinessHealthFailureExpiryDriftRollbackAndRecovery(t *testing.T) {
	preparePlatformArtifactReadinessTest(t)
	now := time.Date(2026, time.August, 29, 10, 0, 0, 0, time.UTC)
	platformArtifactReadinessClock = func() time.Time { return now }
	store := newControlledPlatformArtifactReadinessStore("b")
	require.NoError(t, publishPlatformArtifactReadinessProof(true, store))
	markPlatformArtifactCleanupWorkerStarted(now)
	markPlatformArtifactCleanupWorkerStoreReady(store)
	markPlatformArtifactCleanupWorkerStoreHealthSuccess(now)
	status, err := GetPlatformArtifactReadinessStatus()
	require.NoError(t, err)
	assert.True(t, status.Current)

	t.Setenv("RELAY_ARTIFACT_STORE", "drifted_cleanup_test")
	status, err = GetPlatformArtifactReadinessStatus()
	require.Error(t, err)
	assert.False(t, status.Current)
	t.Setenv("RELAY_ARTIFACT_STORE", "cleanup_test")
	_, err = GetPlatformArtifactReadinessStatus()
	require.Error(t, err, "reverting config alone must not revive a latched proof")
	now = now.Add(time.Second)
	markPlatformArtifactCleanupWorkerStoreHealthSuccess(now)
	requireCurrentPlatformArtifactReadiness(t)

	other := newControlledPlatformArtifactReadinessStore("c")
	markPlatformArtifactCleanupWorkerStoreReady(other)
	markPlatformArtifactCleanupWorkerStoreHealthSuccess(now)
	_, err = GetPlatformArtifactReadinessStatus()
	require.Error(t, err)
	markPlatformArtifactCleanupWorkerStoreReady(store)
	now = now.Add(time.Second)
	markPlatformArtifactCleanupWorkerStoreHealthSuccess(now)
	requireCurrentPlatformArtifactReadiness(t)

	now = now.Add(platformArtifactStoreHealthFreshAfter + time.Second)
	markPlatformArtifactCleanupWorkerSuccess(now)
	status, err = GetPlatformArtifactReadinessStatus()
	require.Error(t, err)
	assert.False(t, status.StoreHealthFresh)
	markPlatformArtifactCleanupWorkerStoreHealthSuccess(now)
	requireCurrentPlatformArtifactReadiness(t)

	now = now.Add(time.Second)
	markPlatformArtifactCleanupWorkerStoreHealthFailure(now, "store_health_failed")
	_, err = GetPlatformArtifactReadinessStatus()
	require.Error(t, err)
	markPlatformArtifactCleanupWorkerStoreHealthSuccess(now)
	requireCurrentPlatformArtifactReadiness(t)

	now = now.Add(time.Second)
	requireCurrentPlatformArtifactReadiness(t)
	lastObserved := now
	now = now.Add(-time.Second)
	_, err = GetPlatformArtifactReadinessStatus()
	require.Error(t, err)
	now = lastObserved.Add(time.Second)
	_, err = GetPlatformArtifactReadinessStatus()
	require.Error(t, err, "wall clock catch-up alone must not clear the rollback latch")
	markPlatformArtifactCleanupWorkerStoreHealthSuccess(now)
	requireCurrentPlatformArtifactReadiness(t)
}

func TestPlatformArtifactReadinessConcurrentHealthAdvanceDoesNotFakeClockRollback(t *testing.T) {
	preparePlatformArtifactReadinessTest(t)
	base := time.Date(2026, time.August, 29, 10, 20, 0, 0, time.UTC)
	platformArtifactReadinessClock = func() time.Time { return base }
	store := newControlledPlatformArtifactReadinessStore("d")
	require.NoError(t, publishPlatformArtifactReadinessProof(true, store))
	markPlatformArtifactCleanupWorkerStarted(base)
	markPlatformArtifactCleanupWorkerStoreReady(store)
	markPlatformArtifactCleanupWorkerStoreHealthSuccess(base)
	requireCurrentPlatformArtifactReadiness(t)

	firstSampled := make(chan struct{})
	releaseFirst := make(chan struct{})
	var calls atomic.Int32
	platformArtifactReadinessClock = func() time.Time {
		if calls.Add(1) == 1 {
			close(firstSampled)
			<-releaseFirst
			return base.Add(time.Second)
		}
		return base.Add(2 * time.Second)
	}
	readinessResult := make(chan error, 1)
	go func() {
		status, err := GetPlatformArtifactReadinessStatus()
		if err == nil && !status.Current {
			err = errors.New("artifact readiness unexpectedly closed")
		}
		readinessResult <- err
	}()
	select {
	case <-firstSampled:
	case <-time.After(time.Second):
		t.Fatal("artifact readiness did not sample the clock")
	}
	healthDone := make(chan struct{})
	go func() {
		markPlatformArtifactCleanupWorkerStoreHealthSuccess(base.Add(2 * time.Second))
		close(healthDone)
	}()
	require.Eventually(t, func() bool {
		platformArtifactCleanupWorkerState.RLock()
		lastHealth := platformArtifactCleanupWorkerState.LastStoreHealthAt
		platformArtifactCleanupWorkerState.RUnlock()
		return lastHealth.Equal(base.Add(2 * time.Second))
	}, time.Second, time.Millisecond)
	close(releaseFirst)
	select {
	case err := <-readinessResult:
		require.NoError(t, err)
	case <-time.After(time.Second):
		t.Fatal("artifact readiness call did not finish")
	}
	select {
	case <-healthDone:
	case <-time.After(time.Second):
		t.Fatal("artifact health acknowledgement did not finish")
	}
	requireCurrentPlatformArtifactReadiness(t)
}

func TestPlatformArtifactReadinessDriftAfterConcurrentHealthAckStaysClosed(t *testing.T) {
	preparePlatformArtifactReadinessTest(t)
	now := time.Date(2026, time.August, 29, 10, 30, 0, 0, time.UTC)
	platformArtifactReadinessClock = func() time.Time { return now }
	store := newControlledPlatformArtifactReadinessStore("0")
	require.NoError(t, publishPlatformArtifactReadinessProof(true, store))
	markPlatformArtifactCleanupWorkerStarted(now)
	markPlatformArtifactCleanupWorkerStoreReady(store)
	markPlatformArtifactCleanupWorkerStoreHealthSuccess(now)

	snapshotTaken := make(chan struct{})
	continueGetter := make(chan struct{})
	var once sync.Once
	platformArtifactReadinessAfterStateSnapshotForTest = func() {
		once.Do(func() { close(snapshotTaken) })
		<-continueGetter
	}
	result := make(chan error, 1)
	go func() {
		_, err := GetPlatformArtifactReadinessStatus()
		result <- err
	}()
	select {
	case <-snapshotTaken:
	case <-time.After(time.Second):
		t.Fatal("readiness getter did not capture its old health generation")
	}
	// This successful acknowledgement advances the health generation after the
	// getter captured it. Config drift observed afterward must invalidate
	// unconditionally, not lose a conditional CAS against the old generation.
	markPlatformArtifactCleanupWorkerStoreHealthSuccess(now)
	t.Setenv("RELAY_ARTIFACT_STORE", "drifted_cleanup_test")
	close(continueGetter)
	require.Error(t, <-result)
	platformArtifactReadinessAfterStateSnapshotForTest = nil

	t.Setenv("RELAY_ARTIFACT_STORE", "cleanup_test")
	_, err := GetPlatformArtifactReadinessStatus()
	require.Error(t, err, "restoring config must not revive a drift-invalidated proof")
	markPlatformArtifactCleanupWorkerStoreHealthSuccess(now)
	requireCurrentPlatformArtifactReadiness(t)
}

func TestPlatformArtifactReadinessDriftRejectsHealthProbeFromOlderEpoch(t *testing.T) {
	preparePlatformArtifactReadinessTest(t)
	now := time.Date(2026, time.August, 29, 10, 40, 0, 0, time.UTC)
	platformArtifactReadinessClock = func() time.Time { return now }
	store := newControlledPlatformArtifactReadinessStore("1")
	require.NoError(t, publishPlatformArtifactReadinessProof(true, store))
	markPlatformArtifactCleanupWorkerStarted(now)
	markPlatformArtifactCleanupWorkerStoreReady(store)
	markPlatformArtifactCleanupWorkerStoreHealthSuccess(now)
	requireCurrentPlatformArtifactReadiness(t)

	oldProbe, err := capturePlatformArtifactReadinessGeneration(store)
	require.NoError(t, err)
	t.Setenv("RELAY_ARTIFACT_STORE", "drifted_cleanup_test")
	_, err = GetPlatformArtifactReadinessStatus()
	require.Error(t, err)
	t.Setenv("RELAY_ARTIFACT_STORE", "cleanup_test")
	now = now.Add(time.Second)
	require.Error(t, publishPlatformArtifactReadinessProofAfterLiveHealth(context.Background(), store, oldProbe))
	markPlatformArtifactCleanupWorkerStoreHealthSuccess(now, oldProbe.healthEpoch)
	_, err = GetPlatformArtifactReadinessStatus()
	require.Error(t, err, "a health probe captured before drift must not clear the drift latch")

	newProbe, err := capturePlatformArtifactReadinessGeneration(store)
	require.NoError(t, err)
	now = now.Add(time.Second)
	require.NoError(t, publishPlatformArtifactReadinessProofAfterLiveHealth(context.Background(), store, newProbe))
	markPlatformArtifactCleanupWorkerStoreHealthSuccess(now, newProbe.healthEpoch)
	requireCurrentPlatformArtifactReadiness(t)
}

func TestPlatformArtifactReadinessBindsOneDatabasePool(t *testing.T) {
	preparePlatformArtifactReadinessTest(t)
	now := time.Date(2026, time.August, 29, 10, 45, 0, 0, time.UTC)
	platformArtifactReadinessClock = func() time.Time { return now }
	store := newControlledPlatformArtifactReadinessStore("a")
	require.NoError(t, publishPlatformArtifactReadinessProof(true, store))
	markPlatformArtifactCleanupWorkerStarted(now)
	markPlatformArtifactCleanupWorkerStoreReady(store)
	markPlatformArtifactCleanupWorkerStoreHealthSuccess(now)
	requireCurrentPlatformArtifactReadiness(t)

	alternate, err := gorm.Open(sqlite.Open("file::memory:?cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	originalDatabase := model.DB
	platformArtifactReadinessDatabase = func() *gorm.DB { return alternate }
	_, err = GetPlatformArtifactReadinessStatus()
	require.Error(t, err)
	platformArtifactReadinessDatabase = func() *gorm.DB { return originalDatabase }
	_, err = GetPlatformArtifactReadinessStatus()
	require.Error(t, err, "restoring the pool alone must not revive readiness")
	markPlatformArtifactCleanupWorkerStoreHealthSuccess(now)
	requireCurrentPlatformArtifactReadiness(t)

	token, err := capturePlatformArtifactReadinessGeneration(store, originalDatabase)
	require.NoError(t, err)
	platformArtifactReadinessDatabase = func() *gorm.DB { return alternate }
	require.Error(t, publishPlatformArtifactReadinessProofAfterLiveHealth(context.Background(), store, token))

	resetPlatformArtifactReadinessForTest()
	resetPlatformArtifactCleanupWorkerStatusForTest()
	platformArtifactReadinessClock = func() time.Time { return now }
	platformArtifactReadinessDatabase = func() *gorm.DB { return originalDatabase }
	require.NoError(t, publishPlatformArtifactReadinessProof(false, nil, originalDatabase))
	markPlatformArtifactCleanupWorkerStarted(now)
	markPlatformArtifactCleanupWorkerSuccess(now)
	platformArtifactReadinessMaintenanceRequired = func(context.Context, *gorm.DB) (bool, error) { return true, nil }
	platformArtifactReadinessDatabase = func() *gorm.DB { return alternate }
	crossPoolToken, err := capturePlatformArtifactReadinessGeneration(store, alternate)
	require.NoError(t, err)
	require.Error(t, publishPlatformArtifactReadinessProofAfterLiveHealth(context.Background(), store, crossPoolToken))
	platformArtifactReadinessState.Lock()
	require.NotNil(t, platformArtifactReadinessState.proof)
	assert.False(t, platformArtifactReadinessState.proof.required)
	platformArtifactReadinessState.Unlock()
	platformArtifactReadinessDatabase = func() *gorm.DB { return originalDatabase }
	_, err = GetPlatformArtifactReadinessStatus()
	require.Error(t, err, "restoring the old pool cannot revive a cross-pool upgrade attempt")
}

func TestPlatformArtifactCleanupSupervisorBindsInitialAndPostHealthRequirementReadsToOneDatabasePool(t *testing.T) {
	preparePlatformArtifactReadinessTest(t)
	truncate(t)
	platformArtifactReadinessClock = time.Now
	database := model.DB
	databasePool, err := database.DB()
	require.NoError(t, err)
	require.NoError(t, publishPlatformArtifactReadinessProof(false, nil, database))

	type databaseObservation struct {
		database *gorm.DB
		samePool bool
		err      error
	}
	observations := make(chan databaseObservation, 4)
	platformArtifactReadinessMaintenanceRequired = func(_ context.Context, observed *gorm.DB) (bool, error) {
		observedPool, observedErr := observed.DB()
		observations <- databaseObservation{
			database: observed,
			samePool: observedErr == nil && observedPool == databasePool,
			err:      observedErr,
		}
		return true, nil
	}

	store := newControlledPlatformArtifactReadinessStore("b")
	healthStarted := make(chan struct{})
	healthRelease := make(chan struct{})
	var healthOnce sync.Once
	var releaseOnce sync.Once
	releaseHealth := func() { releaseOnce.Do(func() { close(healthRelease) }) }
	store.health = func(context.Context) error {
		healthOnce.Do(func() { close(healthStarted) })
		<-healthRelease
		return nil
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		runPlatformArtifactCleanupSupervisor(
			ctx,
			func() (PlatformArtifactStore, error) { return store, nil },
			platformArtifactCleanupWorkerOptions{
				interval:       5 * time.Millisecond,
				initialRetry:   5 * time.Millisecond,
				maxRetry:       10 * time.Millisecond,
				healthInterval: time.Hour,
				healthTimeout:  time.Second,
				now:            time.Now,
			},
			func() bool { return false },
		)
	}()
	t.Cleanup(func() {
		releaseHealth()
		cancel()
		select {
		case <-done:
		case <-time.After(time.Second):
		}
	})

	select {
	case <-healthStarted:
	case <-time.After(time.Second):
		t.Fatal("artifact store healthcheck did not start")
	}
	initial := <-observations
	require.NoError(t, initial.err)
	assert.Same(t, database, initial.database)
	assert.True(t, initial.samePool)
	select {
	case extra := <-observations:
		t.Fatalf("post-health requirement was read before health completed: %+v", extra)
	default:
	}
	releaseHealth()
	var postHealth databaseObservation
	select {
	case postHealth = <-observations:
	case <-time.After(time.Second):
		t.Fatal("post-health durable requirement was not re-read")
	}
	require.NoError(t, postHealth.err)
	assert.Same(t, database, postHealth.database)
	assert.True(t, postHealth.samePool)
	require.Eventually(t, func() bool {
		status, readinessErr := GetPlatformArtifactReadinessStatus()
		return readinessErr == nil && status.Current && status.Required
	}, time.Second, 5*time.Millisecond)

	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("artifact cleanup supervisor did not stop")
	}
}

func requireCurrentPlatformArtifactReadiness(t *testing.T) PlatformArtifactReadinessStatus {
	t.Helper()
	status, err := GetPlatformArtifactReadinessStatus()
	require.NoError(t, err)
	require.True(t, status.Current)
	return status
}

func TestPlatformArtifactReadinessOptionalToRequiredUsesLiveHealthDBCAS(t *testing.T) {
	preparePlatformArtifactReadinessTest(t)
	truncate(t)
	now := time.Date(2026, time.August, 29, 11, 0, 0, 0, time.UTC)
	platformArtifactReadinessClock = func() time.Time { return now }
	store := newControlledPlatformArtifactReadinessStore("d")
	require.NoError(t, publishPlatformArtifactReadinessProof(false, nil))
	_, _, _, firstIntent := appendPlatformArtifactCleanupServiceIntent(t, &cleanupRecordingArtifactStore{
		objects:   map[string]bool{},
		bindingID: store.bindingID,
	})
	token, err := capturePlatformArtifactReadinessGeneration(store)
	require.NoError(t, err)
	require.NoError(t, store.Healthcheck(context.Background()))
	now = now.Add(time.Second)
	require.NoError(t, publishPlatformArtifactReadinessProofAfterLiveHealth(context.Background(), store, token))

	markPlatformArtifactCleanupWorkerStarted(now)
	markPlatformArtifactCleanupWorkerStoreReady(store)
	markPlatformArtifactCleanupWorkerStoreHealthSuccess(now)
	status := requireCurrentPlatformArtifactReadiness(t)
	assert.True(t, status.Required)
	assert.Equal(t, store.bindingID, status.BindingID)
	require.Error(t, publishPlatformArtifactReadinessProof(false, nil))
	require.Error(t, publishPlatformArtifactReadinessProof(true, newControlledPlatformArtifactReadinessStore("e")))

	// Another pod may finish the only pending intent while this process is
	// probing OBS. The established required proof is sticky and must not be
	// downgraded or failed merely because the transient DB edge disappeared.
	require.NoError(t, model.DB.Model(&model.PlatformArtifactUploadIntent{}).
		Where("id = ?", firstIntent.ID).
		Update("state", model.PlatformArtifactUploadIntentPublished).Error)
	required, err := model.PlatformArtifactCleanupMaintenanceRequiredWithDB(context.Background(), model.DB)
	require.NoError(t, err)
	require.False(t, required)
	var requirementReads atomic.Int32
	platformArtifactReadinessMaintenanceRequired = func(context.Context, *gorm.DB) (bool, error) {
		requirementReads.Add(1)
		return false, nil
	}
	stickyToken, err := capturePlatformArtifactReadinessGeneration(store)
	require.NoError(t, err)
	now = now.Add(time.Second)
	require.NoError(t, publishPlatformArtifactReadinessProofAfterLiveHealth(context.Background(), store, stickyToken))
	assert.Zero(t, requirementReads.Load(), "an established required proof must not depend on a transient pending-row edge")
	markPlatformArtifactCleanupWorkerStoreHealthSuccess(now)
	requireCurrentPlatformArtifactReadiness(t)

	newJob, _, _, newIntent := appendPlatformArtifactCleanupServiceIntent(t, &cleanupRecordingArtifactStore{
		objects:   map[string]bool{},
		bindingID: store.bindingID,
	})
	required, err = model.PlatformArtifactCleanupMaintenanceRequiredWithDB(context.Background(), model.DB)
	require.NoError(t, err)
	assert.True(t, required)
	expirePlatformArtifactCleanupServiceIntent(t, newJob.ID, newIntent.ID)
	processed, err := runPlatformArtifactCleanupOnce(context.Background(), store, platformArtifactCleanupMaxAttempts)
	require.NoError(t, err)
	assert.True(t, processed)
	requireCurrentPlatformArtifactReadiness(t)
}

func TestPlatformArtifactReadinessFirstPublishCancellationCASAndTOCTOUFailClosed(t *testing.T) {
	t.Run("first required publish after live health", func(t *testing.T) {
		preparePlatformArtifactReadinessTest(t)
		t.Setenv("RELAY_COMPAT_ENABLED", "true")
		t.Setenv("RELAY_COMPAT_WORKER_ENABLED", "true")
		store := newControlledPlatformArtifactReadinessStore("1")
		token, err := capturePlatformArtifactReadinessGeneration(store)
		require.NoError(t, err)
		require.NoError(t, store.Healthcheck(context.Background()))
		require.NoError(t, publishPlatformArtifactReadinessProofAfterLiveHealth(context.Background(), store, token))
		platformArtifactReadinessState.Lock()
		require.NotNil(t, platformArtifactReadinessState.proof)
		assert.True(t, platformArtifactReadinessState.proof.required)
		platformArtifactReadinessState.Unlock()
	})

	t.Run("canceled context", func(t *testing.T) {
		preparePlatformArtifactReadinessTest(t)
		t.Setenv("RELAY_COMPAT_ENABLED", "true")
		t.Setenv("RELAY_COMPAT_WORKER_ENABLED", "true")
		store := newControlledPlatformArtifactReadinessStore("2")
		token, err := capturePlatformArtifactReadinessGeneration(store)
		require.NoError(t, err)
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		require.Error(t, publishPlatformArtifactReadinessProofAfterLiveHealth(ctx, store, token))
		platformArtifactReadinessState.Lock()
		assert.Nil(t, platformArtifactReadinessState.proof)
		platformArtifactReadinessState.Unlock()
	})

	t.Run("stale generation", func(t *testing.T) {
		preparePlatformArtifactReadinessTest(t)
		platformArtifactReadinessMaintenanceRequired = func(context.Context, *gorm.DB) (bool, error) { return true, nil }
		store := newControlledPlatformArtifactReadinessStore("3")
		require.NoError(t, publishPlatformArtifactReadinessProof(false, nil))
		token, err := capturePlatformArtifactReadinessGeneration(store)
		require.NoError(t, err)
		platformArtifactReadinessState.Lock()
		platformArtifactReadinessState.generation++
		platformArtifactReadinessState.Unlock()
		require.Error(t, publishPlatformArtifactReadinessProofAfterLiveHealth(context.Background(), store, token))
	})

	t.Run("environment changes during durable recheck", func(t *testing.T) {
		preparePlatformArtifactReadinessTest(t)
		store := newControlledPlatformArtifactReadinessStore("4")
		require.NoError(t, publishPlatformArtifactReadinessProof(false, nil))
		token, err := capturePlatformArtifactReadinessGeneration(store)
		require.NoError(t, err)
		platformArtifactReadinessMaintenanceRequired = func(context.Context, *gorm.DB) (bool, error) {
			require.NoError(t, os.Setenv("RELAY_ARTIFACT_STORE", "drifted_cleanup_test"))
			return true, nil
		}
		t.Cleanup(func() { _ = os.Setenv("RELAY_ARTIFACT_STORE", "cleanup_test") })
		require.Error(t, publishPlatformArtifactReadinessProofAfterLiveHealth(context.Background(), store, token))
	})

	t.Run("store binding changes during durable recheck", func(t *testing.T) {
		preparePlatformArtifactReadinessTest(t)
		store := newControlledPlatformArtifactReadinessStore("5")
		require.NoError(t, publishPlatformArtifactReadinessProof(false, nil))
		token, err := capturePlatformArtifactReadinessGeneration(store)
		require.NoError(t, err)
		platformArtifactReadinessMaintenanceRequired = func(context.Context, *gorm.DB) (bool, error) {
			store.bindingID = strings.Repeat("6", 64)
			return true, nil
		}
		require.Error(t, publishPlatformArtifactReadinessProofAfterLiveHealth(context.Background(), store, token))
	})
}

func TestPlatformArtifactCleanupWorkerHealthFailureWithoutTasksRecoversOnlyAfterNewProbe(t *testing.T) {
	preparePlatformArtifactReadinessTest(t)
	truncate(t)
	t.Setenv("RELAY_COMPAT_ENABLED", "true")
	t.Setenv("RELAY_COMPAT_WORKER_ENABLED", "true")
	store := newControlledPlatformArtifactReadinessStore("7")
	secondHealthStarted := make(chan struct{})
	secondHealthRelease := make(chan struct{})
	store.health = func(context.Context) error {
		switch store.healthCalls.Load() {
		case 1:
			return errors.New("bucket unavailable")
		case 2:
			close(secondHealthStarted)
			<-secondHealthRelease
		}
		return nil
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		runPlatformArtifactCleanupWorker(ctx, func() (PlatformArtifactStore, error) { return store, nil }, platformArtifactCleanupWorkerOptions{
			interval:       5 * time.Millisecond,
			initialRetry:   5 * time.Millisecond,
			maxRetry:       10 * time.Millisecond,
			healthInterval: 20 * time.Millisecond,
			healthTimeout:  100 * time.Millisecond,
			now:            time.Now,
		})
	}()
	select {
	case <-secondHealthStarted:
	case <-time.After(time.Second):
		t.Fatal("second healthcheck did not start")
	}
	status := GetPlatformArtifactCleanupWorkerStatus()
	assert.Equal(t, "store_health_failed", status.CurrentErrorCode)
	_, err := GetPlatformArtifactReadinessStatus()
	require.Error(t, err)
	close(secondHealthRelease)
	require.Eventually(t, func() bool {
		status, err := GetPlatformArtifactReadinessStatus()
		return err == nil && status.Current
	}, time.Second, 5*time.Millisecond)
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("artifact cleanup worker did not stop")
	}
}

func TestPlatformArtifactCleanupWorkerStopDoesNotCloseBlockedStore(t *testing.T) {
	preparePlatformArtifactReadinessTest(t)
	truncate(t)
	t.Setenv("RELAY_COMPAT_ENABLED", "true")
	t.Setenv("RELAY_COMPAT_WORKER_ENABLED", "true")
	store := newControlledPlatformArtifactReadinessStore("8")
	healthStarted := make(chan struct{})
	healthRelease := make(chan struct{})
	var once sync.Once
	store.health = func(context.Context) error {
		once.Do(func() { close(healthStarted) })
		<-healthRelease // deliberately models an SDK call that ignores context
		return nil
	}
	var coordinator platformArtifactCleanupWorkerCoordinator
	require.NoError(t, coordinator.start(
		func() (PlatformArtifactStore, error) { return store, nil },
		platformArtifactCleanupWorkerOptions{
			interval:       5 * time.Millisecond,
			initialRetry:   5 * time.Millisecond,
			maxRetry:       10 * time.Millisecond,
			healthInterval: 20 * time.Millisecond,
			healthTimeout:  20 * time.Millisecond,
			now:            time.Now,
		},
		func() bool { return true },
	))
	select {
	case <-healthStarted:
	case <-time.After(time.Second):
		t.Fatal("blocking healthcheck did not start")
	}
	stopContext, cancelStop := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancelStop()
	require.Error(t, coordinator.stop(stopContext))
	assert.Zero(t, store.closeCalls.Load(), "Stop timeout must not close a store with in-flight SDK I/O")
	close(healthRelease)
	joinContext, cancelJoin := context.WithTimeout(context.Background(), time.Second)
	defer cancelJoin()
	require.NoError(t, coordinator.stop(joinContext))
	assert.EqualValues(t, 1, store.closeCalls.Load())
}

func TestPlatformArtifactCleanupWorkerStopClearsStoreProofUntilNewLiveProbe(t *testing.T) {
	preparePlatformArtifactReadinessTest(t)
	now := time.Date(2026, time.August, 29, 12, 0, 0, 0, time.UTC)
	platformArtifactReadinessClock = func() time.Time { return now }
	store := newControlledPlatformArtifactReadinessStore("f")
	require.NoError(t, publishPlatformArtifactReadinessProof(true, store))
	markPlatformArtifactCleanupWorkerStarted(now)
	markPlatformArtifactCleanupWorkerStoreReady(store)
	markPlatformArtifactCleanupWorkerStoreHealthSuccess(now)
	requireCurrentPlatformArtifactReadiness(t)

	now = now.Add(time.Second)
	markPlatformArtifactCleanupWorkerStopped(func() time.Time { return now })
	stopped := GetPlatformArtifactCleanupWorkerStatus()
	assert.False(t, stopped.Running)
	assert.False(t, stopped.StoreConfigured)
	assert.False(t, stopped.StoreHealthHealthy)
	assert.True(t, stopped.LastStoreHealthAt.IsZero())
	_, err := GetPlatformArtifactReadinessStatus()
	require.Error(t, err)

	markPlatformArtifactCleanupWorkerStarted(now)
	_, err = GetPlatformArtifactReadinessStatus()
	require.Error(t, err, "restart alone must not reuse the closed store's health evidence")
	markPlatformArtifactCleanupWorkerStoreReady(store)
	now = now.Add(time.Second)
	markPlatformArtifactCleanupWorkerStoreHealthSuccess(now)
	requireCurrentPlatformArtifactReadiness(t)
}

func TestPlatformArtifactCleanupCoordinatorRestartCannotReuseClosedStoreHealth(t *testing.T) {
	preparePlatformArtifactReadinessTest(t)
	truncate(t)
	t.Setenv("RELAY_COMPAT_ENABLED", "true")
	t.Setenv("RELAY_COMPAT_WORKER_ENABLED", "true")
	firstStore := newControlledPlatformArtifactReadinessStore("e")
	secondStore := newControlledPlatformArtifactReadinessStore("e")
	secondHealthStarted := make(chan struct{})
	secondHealthRelease := make(chan struct{})
	var secondHealthOnce sync.Once
	secondStore.health = func(context.Context) error {
		secondHealthOnce.Do(func() { close(secondHealthStarted) })
		<-secondHealthRelease
		return nil
	}

	var factoryCalls atomic.Int32
	factory := func() (PlatformArtifactStore, error) {
		if factoryCalls.Add(1) == 1 {
			return firstStore, nil
		}
		return secondStore, nil
	}
	options := platformArtifactCleanupWorkerOptions{
		interval:       5 * time.Millisecond,
		initialRetry:   5 * time.Millisecond,
		maxRetry:       10 * time.Millisecond,
		healthInterval: time.Hour,
		healthTimeout:  time.Second,
		now:            time.Now,
	}
	var coordinator platformArtifactCleanupWorkerCoordinator
	require.NoError(t, coordinator.start(factory, options, func() bool { return true }))
	require.Eventually(t, func() bool {
		status, err := GetPlatformArtifactReadinessStatus()
		return err == nil && status.Current
	}, time.Second, 5*time.Millisecond)

	stopContext, cancelStop := context.WithTimeout(context.Background(), time.Second)
	require.NoError(t, coordinator.stop(stopContext))
	cancelStop()
	assert.EqualValues(t, 1, firstStore.closeCalls.Load())
	stopped := GetPlatformArtifactCleanupWorkerStatus()
	assert.False(t, stopped.Running)
	assert.False(t, stopped.StoreConfigured)
	assert.False(t, stopped.StoreHealthHealthy)
	_, err := GetPlatformArtifactReadinessStatus()
	require.Error(t, err)

	require.NoError(t, coordinator.start(factory, options, func() bool { return true }))
	select {
	case <-secondHealthStarted:
	case <-time.After(time.Second):
		t.Fatal("replacement store healthcheck did not start")
	}
	_, err = GetPlatformArtifactReadinessStatus()
	require.Error(t, err, "a restarted worker must not reuse the closed store's health evidence")
	assert.Zero(t, secondStore.closeCalls.Load())
	close(secondHealthRelease)
	require.Eventually(t, func() bool {
		status, readinessErr := GetPlatformArtifactReadinessStatus()
		return readinessErr == nil && status.Current && status.StoreHealthHealthy
	}, time.Second, 5*time.Millisecond)

	stopContext, cancelStop = context.WithTimeout(context.Background(), time.Second)
	require.NoError(t, coordinator.stop(stopContext))
	cancelStop()
	assert.EqualValues(t, 1, secondStore.closeCalls.Load())
}

func TestPlatformGenerationWorkerArtifactReadinessPublishesValidatedPersistentStore(t *testing.T) {
	preparePlatformArtifactReadinessTest(t)
	store := newControlledPlatformArtifactReadinessStore("9")
	var factoryCalls atomic.Int32
	platformArtifactReadinessStoreFactory = func() (PlatformArtifactStore, error) {
		factoryCalls.Add(1)
		return store, nil
	}
	require.NoError(t, validatePlatformGenerationWorkerArtifactReadiness())
	assert.EqualValues(t, 1, factoryCalls.Load())
	assert.EqualValues(t, 1, store.closeCalls.Load())
	platformArtifactReadinessState.Lock()
	require.NotNil(t, platformArtifactReadinessState.proof)
	assert.True(t, platformArtifactReadinessState.proof.required)
	assert.Equal(t, store.bindingID, platformArtifactReadinessState.proof.bindingID)
	platformArtifactReadinessState.Unlock()

	resetPlatformArtifactReadinessForTest()
	nonPersistent := newControlledPlatformArtifactReadinessStore("a")
	nonPersistent.persistent = false
	platformArtifactReadinessStoreFactory = func() (PlatformArtifactStore, error) { return nonPersistent, nil }
	require.Error(t, validatePlatformGenerationWorkerArtifactReadiness())
	assert.EqualValues(t, 1, nonPersistent.closeCalls.Load())
}

func TestPlatformArtifactCleanupMaintenanceRequiredWithDBHonorsCancellation(t *testing.T) {
	preparePlatformArtifactReadinessTest(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := model.PlatformArtifactCleanupMaintenanceRequiredWithDB(ctx, model.DB)
	require.ErrorIs(t, err, context.Canceled)
}
