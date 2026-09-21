package service

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/model"
	"gorm.io/gorm"
)

const (
	platformArtifactStoreHealthInterval   = 30 * time.Second
	platformArtifactStoreHealthTimeout    = platformArtifactCleanupTimeout
	platformArtifactStoreHealthFreshAfter = platformArtifactStoreHealthInterval + platformArtifactStoreHealthTimeout + 15*time.Second
)

var (
	platformArtifactReadinessClock               = time.Now
	platformArtifactReadinessStoreFactory        = NewPlatformArtifactStoreFromEnvironment
	platformArtifactReadinessDatabase            = func() *gorm.DB { return model.DB }
	platformArtifactReadinessMaintenanceRequired = func(ctx context.Context, db *gorm.DB) (bool, error) {
		return model.PlatformArtifactCleanupMaintenanceRequiredWithDB(ctx, db)
	}
	platformArtifactReadinessAfterStateSnapshotForTest func()
)

// PlatformArtifactReadinessStatus is a query- and network-free process
// snapshot. The immutable startup proof describes the required storage
// destination; the cleanup worker fields prove that the process-owned live
// store is still running, healthy, fresh, and bound to that same destination.
type PlatformArtifactReadinessStatus struct {
	Required   bool
	Configured bool
	Current    bool
	Kind       string
	BindingID  string
	Persistent bool
	VerifiedAt time.Time

	CleanupWorkerRunning          bool
	CleanupWorkerStale            bool
	CleanupWorkerCurrentErrorCode string
	CleanupWorkerLastHeartbeatAt  time.Time
	StoreHealthHealthy            bool
	StoreHealthFresh              bool
	LastStoreHealthAt             time.Time
	StoreHealthCurrentErrorCode   string
}

type platformArtifactReadinessProof struct {
	required          bool
	configured        bool
	kind              string
	bindingID         string
	persistent        bool
	verifiedAt        time.Time
	configFingerprint string
	database          *gorm.DB
	databasePool      *sql.DB
}

func (proof platformArtifactReadinessProof) sameConfiguration(other platformArtifactReadinessProof) bool {
	return proof.required == other.required && proof.configured == other.configured &&
		proof.kind == other.kind && proof.bindingID == other.bindingID &&
		proof.persistent == other.persistent && proof.configFingerprint == other.configFingerprint &&
		proof.database == other.database && proof.databasePool == other.databasePool
}

func (proof platformArtifactReadinessProof) sameProcessBinding(other platformArtifactReadinessProof) bool {
	return proof.configFingerprint == other.configFingerprint &&
		proof.database == other.database && proof.databasePool == other.databasePool
}

var platformArtifactReadinessState struct {
	sync.Mutex
	proof                *platformArtifactReadinessProof
	generation           uint64
	lastObserved         time.Time
	clockFailed          bool
	healthGeneration     uint64
	healthEpoch          uint64
	revalidationRequired bool
}

type platformArtifactReadinessGeneration struct {
	generation  uint64
	proof       platformArtifactReadinessProof
	hasProof    bool
	candidate   platformArtifactReadinessProof
	healthEpoch uint64
}

// publishPlatformArtifactReadinessProof installs one immutable process proof.
// Repeating the same startup validation is idempotent; a changed tuple fails
// closed instead of silently rebinding cleanup obligations to another store.
func publishPlatformArtifactReadinessProof(
	required bool,
	store PlatformArtifactStore,
	databases ...*gorm.DB,
) error {
	database, err := selectPlatformArtifactReadinessDatabase(databases...)
	if err != nil {
		return err
	}
	proof, err := newPlatformArtifactReadinessProof(required, store, database)
	if err != nil {
		return err
	}

	platformArtifactReadinessState.Lock()
	defer platformArtifactReadinessState.Unlock()
	if platformArtifactReadinessState.proof != nil {
		if !platformArtifactReadinessState.proof.sameConfiguration(proof) {
			return errors.New("artifact readiness startup configuration changed")
		}
		return nil
	}
	proofCopy := proof
	platformArtifactReadinessState.proof = &proofCopy
	platformArtifactReadinessState.generation++
	platformArtifactReadinessState.lastObserved = proof.verifiedAt
	platformArtifactReadinessState.clockFailed = false
	return nil
}

func capturePlatformArtifactReadinessGeneration(
	store PlatformArtifactStore,
	databases ...*gorm.DB,
) (platformArtifactReadinessGeneration, error) {
	database, err := selectPlatformArtifactReadinessDatabase(databases...)
	if err != nil {
		return platformArtifactReadinessGeneration{}, err
	}
	candidate, err := newPlatformArtifactReadinessProof(true, store, database)
	if err != nil {
		return platformArtifactReadinessGeneration{}, err
	}
	platformArtifactReadinessState.Lock()
	defer platformArtifactReadinessState.Unlock()
	token := platformArtifactReadinessGeneration{
		generation:  platformArtifactReadinessState.generation,
		candidate:   candidate,
		healthEpoch: platformArtifactReadinessState.healthEpoch,
	}
	if platformArtifactReadinessState.proof != nil {
		token.proof = *platformArtifactReadinessState.proof
		token.hasProof = true
	}
	return token, nil
}

// publishPlatformArtifactReadinessProofAfterLiveHealth is the only permitted
// optional-to-required transition. The caller must capture the generation
// before its live Healthcheck. A fresh durable-requirement read and generation
// CAS prevent a late probe from reopening readiness after another state
// transition. Required proofs remain immutable.
func publishPlatformArtifactReadinessProofAfterLiveHealth(
	ctx context.Context,
	store PlatformArtifactStore,
	expectedGeneration platformArtifactReadinessGeneration,
) error {
	if ctx == nil {
		return errors.New("artifact readiness confirmation context is required")
	}
	if err := ctx.Err(); err != nil {
		return fmt.Errorf("artifact readiness confirmation was canceled: %w", err)
	}
	if expectedGeneration.candidate.configFingerprint != platformArtifactReadinessConfigurationFingerprint() ||
		(expectedGeneration.hasProof && !expectedGeneration.proof.sameProcessBinding(expectedGeneration.candidate)) ||
		!platformArtifactReadinessDatabaseIsCurrent(expectedGeneration.candidate.database, expectedGeneration.candidate.databasePool) {
		invalidatePlatformArtifactReadinessHealthUnconditionally()
		return errors.New("artifact readiness configuration changed during store healthcheck")
	}
	// A fresh durable edge is mandatory only when installing the first proof
	// or promoting optional maintenance to required. Once a required proof is
	// immutable, disappearance of the last pending row during a healthcheck
	// does not revoke the process's established storage obligation.
	if !expectedGeneration.hasProof || !expectedGeneration.proof.required {
		required, err := platformArtifactReadinessRequiredNow(ctx, expectedGeneration.candidate.database)
		if err != nil {
			return fmt.Errorf("artifact readiness requirement could not be confirmed: %w", err)
		}
		if !required {
			return errors.New("artifact storage is not currently required")
		}
		if err := ctx.Err(); err != nil {
			return fmt.Errorf("artifact readiness confirmation was canceled: %w", err)
		}
	}
	proof, err := newPlatformArtifactReadinessProof(true, store, expectedGeneration.candidate.database)
	if err != nil {
		return err
	}
	if !proof.sameConfiguration(expectedGeneration.candidate) {
		return errors.New("artifact readiness store binding changed during store healthcheck")
	}

	platformArtifactReadinessState.Lock()
	defer platformArtifactReadinessState.Unlock()
	if err := ctx.Err(); err != nil {
		return fmt.Errorf("artifact readiness confirmation was canceled: %w", err)
	}
	finalProof, err := newPlatformArtifactReadinessProof(true, store, expectedGeneration.candidate.database)
	if err != nil {
		return err
	}
	if !finalProof.sameConfiguration(expectedGeneration.candidate) ||
		platformArtifactReadinessConfigurationFingerprint() != expectedGeneration.candidate.configFingerprint ||
		!platformArtifactReadinessDatabaseIsCurrent(expectedGeneration.candidate.database, expectedGeneration.candidate.databasePool) {
		return errors.New("artifact readiness store binding changed before proof commit")
	}
	if finalProof.verifiedAt.Before(expectedGeneration.candidate.verifiedAt) {
		platformArtifactReadinessState.clockFailed = true
		return errors.New("artifact readiness clock moved backwards during store healthcheck")
	}
	proof = finalProof
	if err := ctx.Err(); err != nil {
		return fmt.Errorf("artifact readiness confirmation was canceled: %w", err)
	}
	if platformArtifactReadinessState.generation != expectedGeneration.generation {
		return errors.New("artifact readiness startup proof changed during store healthcheck")
	}
	if platformArtifactReadinessState.healthEpoch != expectedGeneration.healthEpoch {
		return errors.New("artifact readiness health epoch changed during store healthcheck")
	}
	current := platformArtifactReadinessState.proof
	if current == nil {
		if expectedGeneration.hasProof || expectedGeneration.generation != 0 {
			return errors.New("artifact readiness startup proof changed during store healthcheck")
		}
		if proof.verifiedAt.Before(platformArtifactReadinessState.lastObserved) {
			platformArtifactReadinessState.clockFailed = true
			return errors.New("artifact readiness clock moved backwards during store healthcheck")
		}
		proofCopy := proof
		platformArtifactReadinessState.proof = &proofCopy
		platformArtifactReadinessState.generation++
		platformArtifactReadinessState.lastObserved = proof.verifiedAt
		return nil
	}
	if !expectedGeneration.hasProof || !current.sameConfiguration(expectedGeneration.proof) {
		return errors.New("artifact readiness startup proof changed during store healthcheck")
	}
	if current.required {
		if !current.sameConfiguration(proof) {
			return errors.New("artifact readiness store binding changed")
		}
		return nil
	}
	if proof.verifiedAt.Before(platformArtifactReadinessState.lastObserved) {
		platformArtifactReadinessState.clockFailed = true
		return errors.New("artifact readiness clock moved backwards during store healthcheck")
	}
	proofCopy := proof
	platformArtifactReadinessState.proof = &proofCopy
	platformArtifactReadinessState.generation++
	platformArtifactReadinessState.lastObserved = proof.verifiedAt
	return nil
}

func newPlatformArtifactReadinessProof(
	required bool,
	store PlatformArtifactStore,
	databases ...*gorm.DB,
) (platformArtifactReadinessProof, error) {
	database, err := selectPlatformArtifactReadinessDatabase(databases...)
	if err != nil {
		return platformArtifactReadinessProof{}, err
	}
	databasePool, err := database.DB()
	if err != nil || databasePool == nil {
		return platformArtifactReadinessProof{}, errors.New("artifact readiness database pool is unavailable")
	}
	clock := platformArtifactReadinessClock
	if clock == nil {
		return platformArtifactReadinessProof{}, errors.New("artifact readiness clock is unavailable")
	}
	proof := platformArtifactReadinessProof{
		required:          required,
		verifiedAt:        clock(),
		configFingerprint: platformArtifactReadinessConfigurationFingerprint(),
		database:          database,
		databasePool:      databasePool,
	}
	if proof.verifiedAt.IsZero() {
		return platformArtifactReadinessProof{}, errors.New("artifact readiness verification time is unavailable")
	}
	if !required {
		if store != nil {
			return platformArtifactReadinessProof{}, errors.New("optional artifact readiness proof cannot bind a store")
		}
		return proof, nil
	}
	if store == nil {
		return platformArtifactReadinessProof{}, errors.New("required artifact store proof is unavailable")
	}
	proof.configured = true
	proof.kind = strings.TrimSpace(store.Kind())
	proof.bindingID = strings.ToLower(strings.TrimSpace(store.BindingID()))
	proof.persistent = store.Persistent()
	if proof.kind == "" || proof.kind != store.Kind() || len(proof.bindingID) != sha256.Size*2 {
		return platformArtifactReadinessProof{}, errors.New("artifact store proof is invalid")
	}
	if _, err := hex.DecodeString(proof.bindingID); err != nil {
		return platformArtifactReadinessProof{}, errors.New("artifact store proof binding is invalid")
	}
	return proof, nil
}

func selectPlatformArtifactReadinessDatabase(databases ...*gorm.DB) (*gorm.DB, error) {
	if len(databases) > 1 {
		return nil, errors.New("exactly one artifact readiness database is required")
	}
	if len(databases) == 1 {
		if databases[0] == nil {
			return nil, errors.New("artifact readiness database is unavailable")
		}
		return databases[0], nil
	}
	database := platformArtifactReadinessDatabase()
	if database == nil {
		return nil, errors.New("artifact readiness database is unavailable")
	}
	return database, nil
}

func platformArtifactReadinessDatabaseIsCurrent(database *gorm.DB, pool *sql.DB) bool {
	current := platformArtifactReadinessDatabase()
	if current == nil || current != database {
		return false
	}
	currentPool, err := current.DB()
	return err == nil && currentPool != nil && currentPool == pool
}

func platformArtifactReadinessRequiredNow(ctx context.Context, database *gorm.DB) (bool, error) {
	if ctx == nil {
		return false, errors.New("artifact readiness confirmation context is required")
	}
	if err := ctx.Err(); err != nil {
		return false, err
	}
	if PlatformGenerationWorkersEnabled() {
		return true, nil
	}
	return platformArtifactReadinessMaintenanceRequired(ctx, database)
}

// GetPlatformArtifactReadinessStatus performs no database query, store
// construction, SDK call, or healthcheck. It only compares the startup proof
// with the supervised worker's bounded in-memory state.
func GetPlatformArtifactReadinessStatus() (PlatformArtifactReadinessStatus, error) {
	clock := platformArtifactReadinessClock
	if clock == nil {
		return PlatformArtifactReadinessStatus{}, errors.New("artifact readiness clock is unavailable")
	}
	platformArtifactReadinessState.Lock()
	// Take the sample under the same lock used by health acknowledgement. An
	// acknowledgement cannot otherwise advance lastObserved to T2 between this
	// request's T1 sample and comparison and be misclassified as clock rollback.
	now := clock()
	proofPointer := platformArtifactReadinessState.proof
	if proofPointer == nil {
		platformArtifactReadinessState.Unlock()
		return PlatformArtifactReadinessStatus{}, errors.New("artifact readiness startup proof is unavailable")
	}
	proof := *proofPointer
	if now.IsZero() || now.Before(proof.verifiedAt) ||
		(!platformArtifactReadinessState.lastObserved.IsZero() && now.Before(platformArtifactReadinessState.lastObserved)) {
		platformArtifactReadinessState.clockFailed = true
	}
	if !now.IsZero() && !now.Before(platformArtifactReadinessState.lastObserved) {
		platformArtifactReadinessState.lastObserved = now
	}
	clockFailed := platformArtifactReadinessState.clockFailed
	healthGeneration := platformArtifactReadinessState.healthGeneration
	platformArtifactReadinessState.Unlock()
	if hook := platformArtifactReadinessAfterStateSnapshotForTest; hook != nil {
		hook()
	}

	worker := getPlatformArtifactCleanupWorkerStatusWithClock(clock)
	status := PlatformArtifactReadinessStatus{
		Required:                      proof.required,
		Configured:                    proof.configured,
		Kind:                          proof.kind,
		BindingID:                     proof.bindingID,
		Persistent:                    proof.persistent,
		VerifiedAt:                    proof.verifiedAt,
		CleanupWorkerRunning:          worker.Started && worker.Running,
		CleanupWorkerStale:            worker.Stale,
		CleanupWorkerCurrentErrorCode: worker.CurrentErrorCode,
		CleanupWorkerLastHeartbeatAt:  worker.LastHeartbeatAt,
		StoreHealthHealthy:            worker.StoreHealthHealthy,
		StoreHealthFresh:              worker.StoreHealthFresh,
		LastStoreHealthAt:             worker.LastStoreHealthAt,
		StoreHealthCurrentErrorCode:   worker.StoreHealthCurrentErrorCode,
	}

	var readinessErr error
	configurationChanged := proof.configFingerprint != platformArtifactReadinessConfigurationFingerprint()
	databaseChanged := !platformArtifactReadinessDatabaseIsCurrent(proof.database, proof.databasePool)
	storeBindingChanged := proof.required && (!worker.StoreConfigured || worker.StoreKind != proof.kind ||
		worker.StoreBindingID != proof.bindingID || worker.StorePersistent != proof.persistent)
	storeHealthUnavailable := proof.required && (!worker.StoreHealthHealthy || !worker.StoreHealthFresh ||
		worker.StoreHealthCurrentErrorCode != "")
	if configurationChanged || databaseChanged || storeBindingChanged {
		invalidatePlatformArtifactReadinessHealthUnconditionally()
	} else if storeHealthUnavailable {
		invalidatePlatformArtifactReadinessHealth(healthGeneration)
	}
	platformArtifactReadinessState.Lock()
	revalidationRequired := platformArtifactReadinessState.revalidationRequired
	platformArtifactReadinessState.Unlock()
	switch {
	case clockFailed:
		readinessErr = errors.New("artifact readiness clock is invalid")
	case configurationChanged:
		readinessErr = errors.New("artifact readiness configuration changed after startup")
	case databaseChanged:
		readinessErr = errors.New("artifact readiness database changed after startup")
	case !worker.Started || !worker.Running || worker.Stale || worker.CurrentErrorCode != "":
		readinessErr = errors.New("artifact cleanup worker is unavailable")
	case proof.required && (!proof.configured || !proof.persistent || proof.kind == "" || proof.bindingID == ""):
		readinessErr = errors.New("required artifact startup proof is incomplete")
	case storeBindingChanged:
		readinessErr = errors.New("artifact cleanup worker store binding changed")
	case storeHealthUnavailable:
		readinessErr = errors.New("artifact store health proof is unavailable")
	case revalidationRequired:
		readinessErr = errors.New("artifact store requires a new successful health proof")
	case !proof.required && (proof.configured || proof.kind != "" || proof.bindingID != "" || proof.persistent || worker.StoreConfigured):
		readinessErr = errors.New("optional artifact readiness proof is inconsistent")
	}
	status.Current = readinessErr == nil
	return status, readinessErr
}

// acknowledgePlatformArtifactReadinessStoreHealth permits recovery from a
// clock rollback only after a new successful live store probe at or beyond the
// last observed instant. Merely waiting for wall time to catch up cannot reopen
// readiness.
func acknowledgePlatformArtifactReadinessStoreHealth(now time.Time, expectedEpochs ...uint64) {
	platformArtifactReadinessState.Lock()
	defer platformArtifactReadinessState.Unlock()
	if len(expectedEpochs) > 1 ||
		(len(expectedEpochs) == 1 && platformArtifactReadinessState.healthEpoch != expectedEpochs[0]) {
		return
	}
	clock := platformArtifactReadinessClock
	if clock == nil {
		return
	}
	confirmedAt := clock()
	if platformArtifactReadinessState.proof == nil || now.IsZero() || confirmedAt.IsZero() ||
		now.Before(platformArtifactReadinessState.proof.verifiedAt) ||
		confirmedAt.Before(now) || confirmedAt.Before(platformArtifactReadinessState.proof.verifiedAt) ||
		confirmedAt.Before(platformArtifactReadinessState.lastObserved) {
		return
	}
	platformArtifactReadinessState.lastObserved = confirmedAt
	platformArtifactReadinessState.clockFailed = false
	platformArtifactReadinessState.revalidationRequired = false
	platformArtifactReadinessState.healthGeneration++
}

func invalidatePlatformArtifactReadinessHealth(expectedGeneration uint64) {
	platformArtifactReadinessState.Lock()
	defer platformArtifactReadinessState.Unlock()
	if platformArtifactReadinessState.healthGeneration == expectedGeneration &&
		!platformArtifactReadinessState.revalidationRequired {
		platformArtifactReadinessState.revalidationRequired = true
		platformArtifactReadinessState.healthEpoch++
	}
}

func invalidatePlatformArtifactReadinessHealthUnconditionally() {
	platformArtifactReadinessState.Lock()
	platformArtifactReadinessState.revalidationRequired = true
	platformArtifactReadinessState.healthEpoch++
	platformArtifactReadinessState.Unlock()
}

// platformArtifactReadinessConfigurationFingerprint contains only non-secret
// binding material. Reading it is local and side-effect free.
func platformArtifactReadinessConfigurationFingerprint() string {
	kind := strings.ToLower(strings.TrimSpace(os.Getenv("RELAY_ARTIFACT_STORE")))
	parts := []string{kind}
	switch kind {
	case PlatformArtifactFilesystemKind:
		parts = append(parts, strings.TrimSpace(os.Getenv("RELAY_ARTIFACT_FILESYSTEM_ROOT")))
	case PlatformArtifactHuaweiOBSKind:
		parts = append(parts,
			strings.ToLower(strings.TrimSpace(os.Getenv("HUAWEI_OBS_ENDPOINT"))),
			strings.TrimSpace(os.Getenv("HUAWEI_OBS_BUCKET")),
		)
	}
	digest := sha256.Sum256([]byte(strings.Join(parts, "\x00")))
	return hex.EncodeToString(digest[:])
}

func resetPlatformArtifactReadinessForTest() {
	platformArtifactReadinessState.Lock()
	platformArtifactReadinessState.proof = nil
	platformArtifactReadinessState.generation = 0
	platformArtifactReadinessState.lastObserved = time.Time{}
	platformArtifactReadinessState.clockFailed = false
	platformArtifactReadinessState.healthGeneration = 0
	platformArtifactReadinessState.healthEpoch = 0
	platformArtifactReadinessState.revalidationRequired = false
	platformArtifactReadinessState.Unlock()
	platformArtifactReadinessClock = time.Now
	platformArtifactReadinessStoreFactory = NewPlatformArtifactStoreFromEnvironment
	platformArtifactReadinessDatabase = func() *gorm.DB { return model.DB }
	platformArtifactReadinessMaintenanceRequired = func(ctx context.Context, db *gorm.DB) (bool, error) {
		return model.PlatformArtifactCleanupMaintenanceRequiredWithDB(ctx, db)
	}
	platformArtifactReadinessAfterStateSnapshotForTest = nil
}
