package service

import (
	"crypto/sha256"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

const (
	platformModelCatalogReadinessMissing       = "catalog_proof_missing"
	platformModelCatalogReadinessInvalid       = "catalog_proof_invalid"
	platformModelCatalogReadinessExpired       = "route_evidence_expired"
	platformModelCatalogReadinessConfigChanged = "catalog_configuration_changed"
	platformModelCatalogReadinessClockFailed   = "catalog_clock_invalid"
)

// PlatformRelayModelCatalogReadinessStatus is the scalar, secret-free view
// consumed by /health/ready. It deliberately contains no catalog slices or
// route declarations and can be read without the configuration-cache mutex,
// JSON parsing, signature verification, or filesystem access.
type PlatformRelayModelCatalogReadinessStatus struct {
	Current           bool
	Configured        bool
	ModelCount        int
	CatalogRevision   string
	ConfigGeneration  uint64
	VerifiedAt        time.Time
	EvidenceExpiresAt time.Time
	ErrorCode         string
}

type platformRelayModelCatalogReadinessProof struct {
	configured        bool
	modelCount        int
	catalogRevision   string
	configGeneration  uint64
	verifiedAt        time.Time
	evidenceExpiresAt time.Time
	configFingerprint [sha256.Size]byte
	invalidated       bool
	errorCode         string
}

var (
	platformRelayModelCatalogReadinessProofState   atomic.Pointer[platformRelayModelCatalogReadinessProof]
	platformRelayModelCatalogReadinessClockFailed  atomic.Bool
	platformRelayModelCatalogReadinessLastObserved atomic.Int64
	// Sampling the wall clock and comparing it with lastObserved is one
	// operation. Without this mutex, an older concurrent request can sample T1,
	// pause, and then mistake a second request's legitimate T2 publication for a
	// clock rollback when it resumes.
	platformRelayModelCatalogReadinessClockMu sync.Mutex
)

func platformRelayModelCatalogConfigurationFingerprint() [sha256.Size]byte {
	// Credentials do not define the model catalog. Omitting them also keeps this
	// request-side drift check independent of the runtime-secret registry lock.
	// Route declarations carry their signed release/acceptance evidence inline,
	// so a change to either proof changes this fingerprint.
	values := []string{
		os.Getenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON"),
		os.Getenv("RELAY_COMPAT_MODEL_ROUTES_JSON"),
		strings.ToLower(strings.TrimSpace(os.Getenv("RELAY_COMPAT_ENVIRONMENT"))),
		os.Getenv("APP_ENV"),
		os.Getenv("DEPLOYMENT_ENV"),
		os.Getenv("ENVIRONMENT"),
		os.Getenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_PUBLIC_KEYS_JSON"),
		os.Getenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEY"),
		os.Getenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEYS_JSON"),
		os.Getenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_SIGNING_KEY"),
		os.Getenv("RELAY_COMPAT_SOURCE_REVISION"),
		os.Getenv("RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256"),
		os.Getenv("RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT"),
		os.Getenv("RELAY_COMPAT_IMAGE_DIGEST"),
	}
	return sha256.Sum256([]byte(strings.Join(values, "\x00")))
}

func publishPlatformRelayModelCatalogReadiness(snapshot platformRelayConfigSnapshot) {
	if snapshot.err != nil || snapshot.configGeneration == 0 || snapshot.verifiedAt.IsZero() {
		return
	}
	proof := &platformRelayModelCatalogReadinessProof{
		configured:        len(snapshot.catalog.Data) > 0,
		modelCount:        len(snapshot.catalog.Data),
		catalogRevision:   snapshot.catalog.CatalogRevision,
		configGeneration:  snapshot.configGeneration,
		verifiedAt:        snapshot.verifiedAt,
		evidenceExpiresAt: snapshot.routeEvidenceExpiresAt,
		configFingerprint: snapshot.catalogConfigFingerprint,
	}
	platformRelayModelCatalogReadinessClockMu.Lock()
	defer platformRelayModelCatalogReadinessClockMu.Unlock()
	platformRelayModelCatalogReadinessProofState.Store(proof)
	verifiedUnixNano := snapshot.verifiedAt.UnixNano()
	for {
		observed := platformRelayModelCatalogReadinessLastObserved.Load()
		if verifiedUnixNano <= observed || platformRelayModelCatalogReadinessLastObserved.CompareAndSwap(observed, verifiedUnixNano) {
			break
		}
	}
}

func platformRelayInvalidateModelCatalogReadinessProof(
	proof *platformRelayModelCatalogReadinessProof,
	errorCode string,
) {
	if proof == nil || proof.invalidated {
		return
	}
	invalid := *proof
	invalid.invalidated = true
	invalid.errorCode = errorCode
	platformRelayModelCatalogReadinessProofState.CompareAndSwap(proof, &invalid)
}

// GetPlatformRelayModelCatalogReadinessStatus never calls loadPlatformRelayConfig.
// A successful startup or ordinary/background catalog reload publishes the
// immutable proof. This hot path performs only environment reads, hashing,
// clock checks, and atomic operations.
func GetPlatformRelayModelCatalogReadinessStatus() PlatformRelayModelCatalogReadinessStatus {
	proof := platformRelayModelCatalogReadinessProofState.Load()
	if proof == nil {
		return PlatformRelayModelCatalogReadinessStatus{ErrorCode: platformModelCatalogReadinessMissing}
	}
	status := PlatformRelayModelCatalogReadinessStatus{
		Configured:        proof.configured,
		ModelCount:        proof.modelCount,
		CatalogRevision:   proof.catalogRevision,
		ConfigGeneration:  proof.configGeneration,
		VerifiedAt:        proof.verifiedAt,
		EvidenceExpiresAt: proof.evidenceExpiresAt,
		ErrorCode:         proof.errorCode,
	}
	if proof.invalidated {
		return status
	}
	platformRelayModelCatalogReadinessClockMu.Lock()
	if platformRelayModelCatalogReadinessClockFailed.Load() {
		platformRelayModelCatalogReadinessClockMu.Unlock()
		status.ErrorCode = platformModelCatalogReadinessClockFailed
		return status
	}
	if platformRelayModelCatalogConfigurationFingerprint() != proof.configFingerprint {
		platformRelayModelCatalogReadinessClockMu.Unlock()
		platformRelayInvalidateModelCatalogReadinessProof(proof, platformModelCatalogReadinessConfigChanged)
		status.ErrorCode = platformModelCatalogReadinessConfigChanged
		return status
	}
	now := platformRouteAcceptanceNow()
	if now.IsZero() || now.Before(proof.verifiedAt) {
		platformRelayModelCatalogReadinessClockFailed.Store(true)
		platformRelayModelCatalogReadinessClockMu.Unlock()
		platformRelayInvalidateModelCatalogReadinessProof(proof, platformModelCatalogReadinessClockFailed)
		status.ErrorCode = platformModelCatalogReadinessClockFailed
		return status
	}
	nowUnixNano := now.UnixNano()
	for {
		lastObserved := platformRelayModelCatalogReadinessLastObserved.Load()
		if nowUnixNano < lastObserved {
			platformRelayModelCatalogReadinessClockFailed.Store(true)
			platformRelayModelCatalogReadinessClockMu.Unlock()
			platformRelayInvalidateModelCatalogReadinessProof(proof, platformModelCatalogReadinessClockFailed)
			status.ErrorCode = platformModelCatalogReadinessClockFailed
			return status
		}
		if nowUnixNano <= lastObserved || platformRelayModelCatalogReadinessLastObserved.CompareAndSwap(lastObserved, nowUnixNano) {
			break
		}
	}
	platformRelayModelCatalogReadinessClockMu.Unlock()
	if !proof.evidenceExpiresAt.IsZero() && !now.Before(proof.evidenceExpiresAt) {
		platformRelayInvalidateModelCatalogReadinessProof(proof, platformModelCatalogReadinessExpired)
		status.ErrorCode = platformModelCatalogReadinessExpired
		return status
	}
	if !proof.configured || proof.modelCount < 1 || strings.TrimSpace(proof.catalogRevision) == "" {
		platformRelayInvalidateModelCatalogReadinessProof(proof, platformModelCatalogReadinessInvalid)
		status.ErrorCode = platformModelCatalogReadinessInvalid
		return status
	}
	status.Current = true
	status.ErrorCode = ""
	return status
}

// installPlatformRelayModelCatalogReadinessForTest installs a fully specified
// proof without parsing configuration. Production proof publication remains
// private to successful config loads.
func installPlatformRelayModelCatalogReadinessForTest(
	modelCount int,
	catalogRevision string,
	generation uint64,
	verifiedAt time.Time,
	expiresAt time.Time,
) {
	platformRelayModelCatalogReadinessClockMu.Lock()
	defer platformRelayModelCatalogReadinessClockMu.Unlock()
	platformRelayModelCatalogReadinessProofState.Store(&platformRelayModelCatalogReadinessProof{
		configured:        modelCount > 0,
		modelCount:        modelCount,
		catalogRevision:   catalogRevision,
		configGeneration:  generation,
		verifiedAt:        verifiedAt,
		evidenceExpiresAt: expiresAt,
		configFingerprint: platformRelayModelCatalogConfigurationFingerprint(),
	})
	platformRelayModelCatalogReadinessClockFailed.Store(false)
	platformRelayModelCatalogReadinessLastObserved.Store(verifiedAt.UnixNano())
}

func resetPlatformRelayModelCatalogReadinessForTest() {
	platformRelayModelCatalogReadinessClockMu.Lock()
	defer platformRelayModelCatalogReadinessClockMu.Unlock()
	platformRelayModelCatalogReadinessProofState.Store(nil)
	platformRelayModelCatalogReadinessClockFailed.Store(false)
	platformRelayModelCatalogReadinessLastObserved.Store(0)
}
