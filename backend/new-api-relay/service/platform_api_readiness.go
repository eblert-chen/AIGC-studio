package service

import (
	"context"
	"errors"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/model"
	"gorm.io/gorm"
)

const (
	platformRelayAPICatalogRefreshAfter   = 5 * time.Minute
	platformRelayAPICatalogMaximumAge     = 10 * time.Minute
	platformRelayAPICatalogRefreshTimeout = 2 * time.Minute
	platformRelayAPICatalogRefreshRetry   = 5 * time.Second
)

var (
	attestPlatformRelayAPIDatabaseRole          = model.AttestRelayRuntimeDatabaseRoleWithContext
	verifyPlatformRelayAPIDatabaseRoleProof     = model.VerifyRelayRuntimeDatabaseRoleProof
	validatePlatformRelayAPIDatabaseRoleProof   = model.ValidateRelayRuntimeDatabaseRoleProofBinding
	samePlatformRelayAPIDatabaseRoleRelease     = func(left, right model.RelayRuntimeDatabaseRoleProof) bool { return left.SameRelease(right) }
	platformRelayAPIDatabaseRoleProofVerifiedAt = func(proof model.RelayRuntimeDatabaseRoleProof) time.Time { return proof.VerifiedAt() }
	attestPlatformRelayAPIDatabaseRelease       = attestPlatformRelayDatabaseReleaseProofBoundToSchema
	samePlatformRelayAPIDatabaseRelease         = samePlatformRelayDatabaseReleaseProofBinding
	platformRelayAPIReadinessClock              = time.Now
	platformRelayAPIReadinessInstallStopTimeout = 5 * time.Second
)

// PlatformRelayAPIReadiness owns the protected API's process-local bounded
// catalog proof. It is installed and stopped explicitly by main; tests and a
// second in-process server cannot silently inherit another instance's proof.
type PlatformRelayAPIReadiness struct {
	database *gorm.DB
	clock    func() time.Time

	mu              sync.Mutex
	proof           model.RelayRuntimeDatabaseRoleProof
	releaseBinding  platformRelayDatabaseReleaseProofBinding
	verifiedAt      time.Time
	proofValid      bool
	refreshInFlight bool
	nextRefresh     time.Time
	refreshAfter    time.Duration
	maximumAge      time.Duration
	refreshTimeout  time.Duration
	refreshRetry    time.Duration
	generation      uint64
	lastObserved    time.Time
	clockFailed     bool
	stopped         bool
	lifecycle       context.Context
	cancel          context.CancelFunc
	workers         sync.WaitGroup
	stopWaitOnce    sync.Once
	stopDone        chan struct{}
}

func NewProtectedPlatformRelayAPIReadiness(
	parent context.Context,
	database *gorm.DB,
	proof model.RelayRuntimeDatabaseRoleProof,
) (*PlatformRelayAPIReadiness, error) {
	if parent == nil || parent.Err() != nil {
		return nil, errors.New("protected Relay API readiness lifecycle is unavailable")
	}
	if err := validatePlatformRelayAPIDatabaseRoleProof(database, proof); err != nil {
		return nil, err
	}
	releaseBinding, err := attestPlatformRelayAPIDatabaseRelease(
		parent,
		database,
		PlatformRelaySecretIsolationConsumerAPI,
		proof.SchemaStatus(),
	)
	if err != nil || !releaseBinding.valid() {
		return nil, errors.Join(errors.New("Relay API database release proof is unavailable"), err)
	}
	if releaseBinding.schemaStatus != proof.SchemaStatus() {
		return nil, errors.New("Relay API database proofs do not describe the same release")
	}
	clock := platformRelayAPIReadinessClock
	if clock == nil {
		return nil, errors.New("Relay API readiness clock is unavailable")
	}
	now := clock()
	verifiedAt := platformRelayAPIDatabaseRoleProofVerifiedAt(proof)
	age := now.Sub(verifiedAt)
	if now.IsZero() || verifiedAt.IsZero() || age < 0 || age >= platformRelayAPICatalogMaximumAge {
		return nil, errors.New("Relay API database role proof is outside the freshness window")
	}
	lifecycle, cancel := context.WithCancel(parent)
	readiness := &PlatformRelayAPIReadiness{
		database:       database,
		clock:          clock,
		proof:          proof,
		releaseBinding: releaseBinding,
		verifiedAt:     verifiedAt,
		proofValid:     true,
		refreshAfter:   platformRelayAPICatalogRefreshAfter,
		maximumAge:     platformRelayAPICatalogMaximumAge,
		refreshTimeout: platformRelayAPICatalogRefreshTimeout,
		refreshRetry:   platformRelayAPICatalogRefreshRetry,
		generation:     1,
		lastObserved:   now,
		lifecycle:      lifecycle,
		cancel:         cancel,
		stopDone:       make(chan struct{}),
	}
	readiness.workers.Add(1)
	go func() {
		defer readiness.workers.Done()
		<-lifecycle.Done()
		readiness.mu.Lock()
		readiness.proofValid = false
		readiness.generation++
		readiness.mu.Unlock()
	}()
	return readiness, nil
}

func (readiness *PlatformRelayAPIReadiness) observeClockLocked(now time.Time) bool {
	if readiness.clockFailed {
		return false
	}
	if now.IsZero() ||
		(!readiness.lastObserved.IsZero() && now.Before(readiness.lastObserved)) ||
		(!readiness.verifiedAt.IsZero() && now.Before(readiness.verifiedAt)) {
		readiness.clockFailed = true
		readiness.proofValid = false
		readiness.generation++
		if readiness.cancel != nil {
			readiness.cancel()
		}
		return false
	}
	readiness.lastObserved = now
	return true
}

func (readiness *PlatformRelayAPIReadiness) currentProof() (model.RelayRuntimeDatabaseRoleProof, bool) {
	readiness.mu.Lock()
	now := readiness.clock()
	proof := readiness.proof
	clockHealthy := readiness.observeClockLocked(now)
	valid := clockHealthy && readiness.proofValid
	age := now.Sub(readiness.verifiedAt)
	if !clockHealthy || readiness.stopped || readiness.lifecycle == nil || readiness.lifecycle.Err() != nil ||
		readiness.verifiedAt.IsZero() || age < 0 || age >= readiness.maximumAge {
		if readiness.proofValid {
			readiness.generation++
		}
		valid = false
		readiness.proofValid = false
	}
	startRefresh := false
	releaseBinding := readiness.releaseBinding
	if clockHealthy && !readiness.clockFailed && !readiness.stopped && readiness.lifecycle != nil &&
		readiness.lifecycle.Err() == nil && !readiness.refreshInFlight && !now.Before(readiness.nextRefresh) &&
		(!valid || age >= readiness.refreshAfter) {
		readiness.refreshInFlight = true
		readiness.workers.Add(1)
		startRefresh = true
	}
	generation := readiness.generation
	readiness.mu.Unlock()
	if startRefresh {
		readiness.launchRefresh(proof, releaseBinding, generation)
	}
	return proof, valid
}

func (readiness *PlatformRelayAPIReadiness) launchRefresh(
	expected model.RelayRuntimeDatabaseRoleProof,
	expectedRelease platformRelayDatabaseReleaseProofBinding,
	generation uint64,
) {
	go func() {
		defer readiness.workers.Done()
		readiness.refresh(expected, expectedRelease, generation)
	}()
}

func (readiness *PlatformRelayAPIReadiness) refresh(
	expected model.RelayRuntimeDatabaseRoleProof,
	expectedRelease platformRelayDatabaseReleaseProofBinding,
	generation uint64,
) {
	timeout := readiness.refreshTimeout
	if timeout <= 0 {
		timeout = platformRelayAPICatalogRefreshTimeout
	}
	ctx, cancel := context.WithTimeout(readiness.lifecycle, timeout)
	refreshed, err := attestPlatformRelayAPIDatabaseRole(ctx, readiness.database)
	if err == nil && !samePlatformRelayAPIDatabaseRoleRelease(expected, refreshed) {
		err = errors.New("Relay API catalog refresh changed the bound release")
	}
	if err == nil {
		err = validatePlatformRelayAPIDatabaseRoleProof(readiness.database, refreshed)
	}
	refreshedRelease := platformRelayDatabaseReleaseProofBinding{}
	if err == nil {
		refreshedRelease, err = attestPlatformRelayAPIDatabaseRelease(
			ctx,
			readiness.database,
			PlatformRelaySecretIsolationConsumerAPI,
			refreshed.SchemaStatus(),
		)
		if err == nil && !samePlatformRelayAPIDatabaseRelease(expectedRelease, refreshedRelease) {
			err = errors.New("Relay API database release binding changed")
		}
	}
	cancel()
	refreshedVerifiedAt := platformRelayAPIDatabaseRoleProofVerifiedAt(refreshed)
	readiness.mu.Lock()
	readiness.refreshInFlight = false
	now := readiness.clock()
	clockHealthy := readiness.observeClockLocked(now)
	if generation != readiness.generation {
		readiness.proofValid = false
		if clockHealthy && !readiness.stopped && readiness.lifecycle.Err() == nil {
			readiness.nextRefresh = now
		}
		readiness.mu.Unlock()
		return
	}
	age := now.Sub(refreshedVerifiedAt)
	if !clockHealthy || err != nil || refreshedVerifiedAt.IsZero() || age < 0 || age >= readiness.maximumAge ||
		readiness.stopped || readiness.lifecycle.Err() != nil {
		readiness.proofValid = false
		readiness.generation++
		if clockHealthy && !readiness.stopped && readiness.lifecycle.Err() == nil {
			readiness.nextRefresh = now.Add(readiness.refreshRetry)
		}
		readiness.mu.Unlock()
		return
	}
	readiness.proof = refreshed
	readiness.releaseBinding = refreshedRelease
	readiness.verifiedAt = refreshedVerifiedAt
	readiness.proofValid = true
	readiness.generation++
	readiness.nextRefresh = time.Time{}
	readiness.mu.Unlock()
}

func (readiness *PlatformRelayAPIReadiness) invalidate(expected model.RelayRuntimeDatabaseRoleProof) {
	readiness.mu.Lock()
	now := readiness.clock()
	clockHealthy := readiness.observeClockLocked(now)
	if samePlatformRelayAPIDatabaseRoleRelease(readiness.proof, expected) {
		readiness.proofValid = false
		readiness.generation++
	}
	startRefresh := false
	releaseBinding := readiness.releaseBinding
	if clockHealthy && !readiness.clockFailed && !readiness.stopped && readiness.lifecycle != nil &&
		readiness.lifecycle.Err() == nil && !readiness.refreshInFlight && !now.Before(readiness.nextRefresh) {
		readiness.refreshInFlight = true
		readiness.workers.Add(1)
		startRefresh = true
	}
	generation := readiness.generation
	readiness.mu.Unlock()
	if startRefresh {
		readiness.launchRefresh(expected, releaseBinding, generation)
	}
}

func (readiness *PlatformRelayAPIReadiness) proofStillValid(expected model.RelayRuntimeDatabaseRoleProof) bool {
	readiness.mu.Lock()
	defer readiness.mu.Unlock()
	now := readiness.clock()
	if !readiness.observeClockLocked(now) || readiness.stopped || readiness.lifecycle == nil ||
		readiness.lifecycle.Err() != nil || !readiness.proofValid ||
		!samePlatformRelayAPIDatabaseRoleRelease(readiness.proof, expected) || readiness.verifiedAt.IsZero() {
		return false
	}
	age := now.Sub(readiness.verifiedAt)
	if age < 0 || age >= readiness.maximumAge {
		readiness.proofValid = false
		readiness.generation++
		return false
	}
	return true
}

// Verify performs the request-scoped light proof. A caller cancellation fails
// only that request; a genuine live DB/schema/role/ACL error invalidates the
// process proof and starts at most one bounded full refresh.
func (readiness *PlatformRelayAPIReadiness) Verify(ctx context.Context) (model.RelaySchemaStatus, model.RelayRuntimeDatabaseRoleStatus, error) {
	roleStatus := model.RelayRuntimeDatabaseRoleStatus{Required: true, State: "unavailable"}
	if readiness == nil || ctx == nil || ctx.Err() != nil {
		return model.RelaySchemaStatus{}, roleStatus, errors.New("Relay API readiness request is unavailable")
	}
	proof, available := readiness.currentProof()
	if !available {
		return model.RelaySchemaStatus{}, roleStatus, errors.New("Relay API full database proof is unavailable")
	}
	requestDB := readiness.database.WithContext(ctx)
	sqlDB, err := requestDB.DB()
	if err != nil || sqlDB.PingContext(ctx) != nil {
		if ctx.Err() == nil {
			readiness.invalidate(proof)
		}
		return model.RelaySchemaStatus{}, roleStatus, errors.New("Relay API database is unavailable")
	}
	roleStatus, err = verifyPlatformRelayAPIDatabaseRoleProof(requestDB, proof)
	if err != nil {
		if ctx.Err() == nil {
			readiness.invalidate(proof)
		}
		return model.RelaySchemaStatus{}, roleStatus, err
	}
	if ctx.Err() != nil {
		return model.RelaySchemaStatus{}, roleStatus, ctx.Err()
	}
	if !readiness.proofStillValid(proof) {
		return model.RelaySchemaStatus{}, roleStatus, errors.New("Relay API full database proof changed during the request")
	}
	return proof.SchemaStatus(), roleStatus, nil
}

// Stop cancels and joins every full-proof refresh before main may close the DB.
func (readiness *PlatformRelayAPIReadiness) Stop(ctx context.Context) error {
	if readiness == nil || ctx == nil {
		return errors.New("Relay API readiness shutdown is invalid")
	}
	readiness.mu.Lock()
	if !readiness.stopped {
		readiness.stopped = true
		readiness.proofValid = false
		readiness.generation++
		if readiness.cancel != nil {
			readiness.cancel()
		}
	}
	readiness.mu.Unlock()
	readiness.stopWaitOnce.Do(func() {
		go func() {
			readiness.workers.Wait()
			close(readiness.stopDone)
		}()
	})
	select {
	case <-readiness.stopDone:
		return nil
	case <-ctx.Done():
		return errors.Join(errors.New("Relay API readiness refresh did not stop before the deadline"), ctx.Err())
	}
}

var platformRelayAPIReadinessRegistry struct {
	sync.RWMutex
	current *PlatformRelayAPIReadiness
}

func InstallProtectedPlatformRelayAPIReadiness(readiness *PlatformRelayAPIReadiness) error {
	if readiness == nil {
		return errors.New("protected Relay API readiness is unavailable")
	}
	platformRelayAPIReadinessRegistry.Lock()
	existing := platformRelayAPIReadinessRegistry.current
	if existing != nil {
		platformRelayAPIReadinessRegistry.Unlock()
		// A constructor starts a lifecycle sentinel immediately. If registration
		// loses a race, cancel and join the uninstalled newcomer so it cannot leak
		// across tests or a second in-process server. Re-registering the exact
		// installed pointer is merely rejected; stopping it would poison the owner.
		if existing != readiness {
			ctx, cancel := context.WithTimeout(context.Background(), platformRelayAPIReadinessInstallStopTimeout)
			stopErr := readiness.Stop(ctx)
			cancel()
			return errors.Join(errors.New("protected Relay API readiness is already installed"), stopErr)
		}
		return errors.New("protected Relay API readiness is already installed")
	}
	platformRelayAPIReadinessRegistry.current = readiness
	platformRelayAPIReadinessRegistry.Unlock()
	return nil
}

func GetProtectedPlatformRelayAPIReadiness(ctx context.Context) (model.RelaySchemaStatus, model.RelayRuntimeDatabaseRoleStatus, error) {
	platformRelayAPIReadinessRegistry.RLock()
	readiness := platformRelayAPIReadinessRegistry.current
	platformRelayAPIReadinessRegistry.RUnlock()
	if readiness == nil {
		return model.RelaySchemaStatus{}, model.RelayRuntimeDatabaseRoleStatus{Required: true, State: "unavailable"},
			errors.New("protected Relay API readiness is not installed")
	}
	return readiness.Verify(ctx)
}

// StopInstalledProtectedPlatformRelayAPIReadiness keeps the stopping process
// binding reachable until every refresh has joined. Concurrent handlers fail
// closed because Stop invalidates the proof before it waits.
func StopInstalledProtectedPlatformRelayAPIReadiness(ctx context.Context) error {
	platformRelayAPIReadinessRegistry.RLock()
	readiness := platformRelayAPIReadinessRegistry.current
	platformRelayAPIReadinessRegistry.RUnlock()
	if readiness == nil {
		return nil
	}
	if err := readiness.Stop(ctx); err != nil {
		// Preserve the stopping manager in the registry so the database owner can
		// retry the join. Losing the pointer after a timeout would make a later
		// CloseDB race an in-flight full attestation.
		return err
	}
	platformRelayAPIReadinessRegistry.Lock()
	if platformRelayAPIReadinessRegistry.current == readiness {
		platformRelayAPIReadinessRegistry.current = nil
	}
	platformRelayAPIReadinessRegistry.Unlock()
	return nil
}
