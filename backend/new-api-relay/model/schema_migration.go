package model

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/binary"
	"errors"
	"fmt"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	_ "github.com/jackc/pgx/v5/stdlib"
	"gorm.io/gorm"
)

const (
	relaySchemaStateSingletonID              = 1
	relayLifecycleAdvisoryLock         int64 = 0x41564944524f4f4c
	relayLifecycleMutationAdvisoryLock int64 = 0x41564944524f4f4d
	// Offline schema/bootstrap commands must never wait indefinitely for an
	// old runtime process to release the lifecycle anchor. Operators drain the
	// old runtime first; a stale holder fails this deployment attempt cleanly.
	relayLifecycleLockAcquireTimeout = 10 * time.Second
)

var (
	relaySchemaMigrationProcessMu    sync.Mutex
	databaseRoleNamePattern          = regexp.MustCompile(`^[a-z_][a-z0-9_]{0,62}$`)
	relaySchemaSourceRevisionPattern = regexp.MustCompile(`^[0-9a-f]{40}$`)
	relaySchemaSnapshotSHA256Pattern = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
)

type RelaySchemaState struct {
	ID                   int        `json:"-" gorm:"primaryKey;check:id = 1"`
	BaselineVersion      int64      `json:"baseline_version" gorm:"not null;default:0"`
	FreshBootstrap       bool       `json:"fresh_bootstrap_eligible" gorm:"not null;default:false"`
	CurrentVersion       int64      `json:"current_version" gorm:"not null;default:0"`
	TargetVersion        int64      `json:"target_version" gorm:"not null;default:0"`
	State                string     `json:"state" gorm:"type:varchar(16);not null"`
	Dirty                bool       `json:"dirty" gorm:"not null;default:false"`
	AttemptID            string     `json:"attempt_id,omitempty" gorm:"type:varchar(36);not null;default:''"`
	CurrentChecksum      string     `json:"current_checksum,omitempty" gorm:"type:varchar(71);not null;default:''"`
	TargetChecksum       string     `json:"target_checksum,omitempty" gorm:"type:varchar(71);not null;default:''"`
	CurrentCatalogSHA256 string     `json:"current_catalog_sha256,omitempty" gorm:"type:varchar(71);not null;default:''"`
	TargetCatalogSHA256  string     `json:"target_catalog_sha256,omitempty" gorm:"type:varchar(71);not null;default:''"`
	SourceRevision       string     `json:"source_revision,omitempty" gorm:"type:varchar(128);not null;default:''"`
	SnapshotSHA256       string     `json:"source_snapshot_sha256,omitempty" gorm:"type:varchar(71);not null;default:''"`
	StartedAt            *time.Time `json:"started_at,omitempty"`
	FinishedAt           *time.Time `json:"finished_at,omitempty"`
	ErrorCode            string     `json:"error_code,omitempty" gorm:"type:varchar(64);not null;default:''"`
	UpdatedAt            time.Time  `json:"updated_at" gorm:"not null"`
}

func (RelaySchemaState) TableName() string { return "relay_schema_state" }

type RelaySchemaMigration struct {
	Version        int64     `json:"version" gorm:"primaryKey"`
	Name           string    `json:"name" gorm:"type:varchar(128);not null"`
	Phase          string    `json:"phase" gorm:"type:varchar(16);not null"`
	Checksum       string    `json:"checksum" gorm:"type:varchar(71);not null"`
	CatalogSHA256  string    `json:"catalog_sha256" gorm:"type:varchar(71);not null"`
	AppliedAt      time.Time `json:"applied_at" gorm:"not null"`
	SourceRevision string    `json:"source_revision,omitempty" gorm:"type:varchar(128);not null;default:''"`
	SnapshotSHA256 string    `json:"source_snapshot_sha256,omitempty" gorm:"type:varchar(71);not null;default:''"`
}

func (RelaySchemaMigration) TableName() string { return "relay_schema_migrations" }

type RelaySchemaStatus struct {
	Classification        string `json:"classification"`
	BaselineVersion       int64  `json:"baseline_version"`
	FreshBootstrap        bool   `json:"fresh_bootstrap_eligible"`
	CurrentVersion        int64  `json:"current_version"`
	TargetVersion         int64  `json:"target_version"`
	PendingVersion        int64  `json:"pending_version,omitempty"`
	MinVersion            int64  `json:"min_version"`
	MaxVersion            int64  `json:"max_version"`
	State                 string `json:"state"`
	Dirty                 bool   `json:"dirty"`
	AttemptID             string `json:"attempt_id,omitempty"`
	CurrentChecksum       string `json:"current_checksum,omitempty"`
	ExpectedChecksum      string `json:"expected_checksum,omitempty"`
	TargetChecksum        string `json:"target_checksum,omitempty"`
	CatalogSHA256         string `json:"catalog_sha256,omitempty"`
	ExpectedCatalogSHA256 string `json:"expected_catalog_sha256,omitempty"`
	SourceRevision        string `json:"source_revision,omitempty"`
	SnapshotSHA256        string `json:"source_snapshot_sha256,omitempty"`
	ErrorCode             string `json:"error_code,omitempty"`
	Compatible            bool   `json:"compatible"`
	Current               bool   `json:"current"`
}

type RelaySchemaMigrationResult struct {
	SchemaVersion int               `json:"schema_version"`
	Kind          string            `json:"kind"`
	State         string            `json:"state"`
	FromVersion   int64             `json:"from_version"`
	ToVersion     int64             `json:"to_version"`
	AttemptID     string            `json:"attempt_id,omitempty"`
	Status        RelaySchemaStatus `json:"status"`
}

func relaySchemaProvenanceValid(sourceRevision string, snapshotSHA256 string) bool {
	return relaySchemaSourceRevisionPattern.MatchString(sourceRevision) &&
		sourceRevision != strings.Repeat("0", 40) &&
		relaySchemaSnapshotSHA256Pattern.MatchString(snapshotSHA256) &&
		snapshotSHA256 != "sha256:"+strings.Repeat("0", 64)
}

func relaySchemaConfiguredProvenance() (string, string, error) {
	sourceRevision := strings.ToLower(strings.TrimSpace(os.Getenv("RELAY_COMPAT_SOURCE_REVISION")))
	snapshotSHA256 := strings.ToLower(strings.TrimSpace(os.Getenv("RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256")))
	if sourceRevision == "" && snapshotSHA256 == "" && !RelayDatabaseRoleAttestationRequired() {
		return "", "", nil
	}
	if !relaySchemaProvenanceValid(sourceRevision, snapshotSHA256) {
		return "", "", errors.New("Relay schema migration source provenance is invalid")
	}
	return sourceRevision, snapshotSHA256, nil
}

type relaySchemaMigrationDefinition struct {
	Version   int64
	Name      string
	Phase     string
	Checksum  string
	Up        func(*gorm.DB) error
	Bootstrap func(*gorm.DB) error
}

type relaySchemaV1Step struct {
	ID string
	Up func(*gorm.DB) error
}

type relaySchemaV2Step struct {
	ID string
	Up func(*gorm.DB) error
}

func relaySchemaMigrations() []relaySchemaMigrationDefinition {
	return []relaySchemaMigrationDefinition{
		{
			Version:   relaySchemaV1FrozenVersion,
			Name:      relaySchemaV1FrozenName,
			Phase:     relaySchemaV1FrozenPhase,
			Checksum:  RelaySchemaV1Checksum(),
			Up:        migrateRelaySchemaV1,
			Bootstrap: migrateRelaySchemaV1,
		},
		{
			Version:   relaySchemaV2FrozenVersion,
			Name:      relaySchemaV2FrozenName,
			Phase:     relaySchemaV2FrozenPhase,
			Checksum:  RelaySchemaV2Checksum(),
			Up:        migrateRelaySchemaV2NoCatalogDelta,
			Bootstrap: migrateRelaySchemaV2Bootstrap,
		},
	}
}

var relaySchemaDefinitionsForRuntime = relaySchemaMigrations

func relaySchemaV1Steps() []relaySchemaV1Step {
	return []relaySchemaV1Step{
		{ID: "subscription-plan-price-decimal-v1", Up: migrateSubscriptionPlanPriceAmountWithDB},
		{ID: "token-model-limits-text-v1", Up: migrateTokenModelLimitsToTextWithDB},
		{ID: "channel-cost-digest-varchar-v1", Up: migratePlatformChannelCostDocumentDigestStorageWithDB},
		{ID: "gorm-model-baseline-v1", Up: migrateRelaySchemaV1Models},
		{ID: "previous-candidate-catalog-normalization-v1", Up: migrateRelaySchemaV1PreviousCandidateCatalog},
		{ID: "provider-task-credential-vault-v1", Up: MigrateProviderCredentialVaultStorageWithDB},
		{ID: "provider-channel-credential-vault-v1", Up: MigrateProviderChannelCredentialVaultStorageWithDB},
		{ID: "artifact-intent-v1", Up: MigratePlatformArtifactUploadIntentStorageWithDB},
		{ID: "shared-account-state-v1", Up: MigratePlatformGenerationProviderAccountStateWithDB},
		{ID: "reconciliation-append-only-v1", Up: MigratePlatformGenerationReconciliationStorageWithDB},
		{ID: "callback-redrive-append-only-v1", Up: MigratePlatformGenerationCallbackOperationsStorageWithDB},
		{ID: "channel-control-guards-v1", Up: MigratePlatformChannelControlStorageWithDB},
		{ID: "provider-cost-monitor-guards-v1", Up: MigratePlatformProviderMonitorAndCostStorageWithDB},
		{ID: "auth-version-backfill-v1", Up: InitializeUserAuthVersionsWithDB},
		{ID: "external-identity-backfill-v1", Up: InitializeExternalIdentityClaimsWithDB},
		{ID: "subscription-plan-v1", Up: migrateRelaySchemaV1SubscriptionPlan},
		{ID: "retired-option-migration-v1", Up: migrateRetiredFrontendOptionsStrictWithDB},
		{ID: "runtime-dml-privilege-manifest-v1", Up: ApplyRelayDatabasePrivilegeManifestWithDB},
		{ID: "download-edge-rls-v1", Up: MigratePlatformDownloadEdgeIsolationWithDB},
		{ID: "download-edge-dml-privilege-manifest-v1", Up: ApplyRelayDownloadEdgeDatabasePrivilegeManifestWithDB},
	}
}

func migrateRelaySchemaV1(db *gorm.DB) error {
	for _, step := range relaySchemaV1Steps() {
		if err := step.Up(db); err != nil {
			return fmt.Errorf("Relay schema v1 step %s failed: %w", step.ID, err)
		}
	}
	return nil
}

func migrateRelaySchemaV1Models(db *gorm.DB) error {
	return db.AutoMigrate(relaySchemaV1Models()...)
}

// migrateRelaySchemaV1PreviousCandidateCatalog removes the one catalog object
// that existed in the immutable pre-migration candidate but is not part of the
// frozen v1 model snapshot. Keeping this explicit makes legacy convergence
// deterministic without weakening the exact catalog fingerprint.
func migrateRelaySchemaV1PreviousCandidateCatalog(db *gorm.DB) error {
	if db == nil || db.Dialector.Name() != "postgres" {
		return nil
	}
	for _, statement := range []string{
		`ALTER TABLE public.prefill_groups DROP CONSTRAINT IF EXISTS idx_prefill_groups_name`,
		`DROP INDEX IF EXISTS public.idx_prefill_groups_name`,
	} {
		if err := db.Exec(statement).Error; err != nil {
			return errors.New("previous Relay candidate catalog could not be normalized")
		}
	}
	return nil
}

func migrateRelaySchemaV1SubscriptionPlan(db *gorm.DB) error {
	if db.Dialector.Name() == "sqlite" {
		return ensureSubscriptionPlanTableSQLiteWithDB(db)
	}
	return db.AutoMigrate(&SubscriptionPlan{})
}

// relaySchemaV2BootstrapSteps is a complete fresh-v2 snapshot. It repeats the
// required transforms instead of calling migrateRelaySchemaV1, so a fresh v2
// database never executes or records a historical v1 migration.
func relaySchemaV2BootstrapSteps() []relaySchemaV2Step {
	return []relaySchemaV2Step{
		{ID: "subscription-plan-price-decimal-v2", Up: migrateSubscriptionPlanPriceAmountWithDB},
		{ID: "token-model-limits-text-v2", Up: migrateTokenModelLimitsToTextWithDB},
		{ID: "channel-cost-digest-varchar-v2", Up: migratePlatformChannelCostDocumentDigestStorageWithDB},
		{ID: "gorm-model-bootstrap-v2", Up: migrateRelaySchemaV2Models},
		{ID: "previous-candidate-catalog-normalization-v2", Up: migrateRelaySchemaV2PreviousCandidateCatalog},
		{ID: "provider-task-credential-vault-v2", Up: MigrateProviderCredentialVaultStorageWithDB},
		{ID: "provider-channel-credential-vault-v2", Up: MigrateProviderChannelCredentialVaultStorageWithDB},
		{ID: "artifact-intent-v2", Up: MigratePlatformArtifactUploadIntentStorageWithDB},
		{ID: "shared-account-state-v2", Up: MigratePlatformGenerationProviderAccountStateWithDB},
		{ID: "reconciliation-append-only-v2", Up: MigratePlatformGenerationReconciliationStorageWithDB},
		{ID: "callback-redrive-append-only-v2", Up: MigratePlatformGenerationCallbackOperationsStorageWithDB},
		{ID: "channel-control-guards-v2", Up: MigratePlatformChannelControlStorageWithDB},
		{ID: "provider-cost-monitor-guards-v2", Up: MigratePlatformProviderMonitorAndCostStorageWithDB},
		{ID: "auth-version-backfill-v2", Up: InitializeUserAuthVersionsWithDB},
		{ID: "external-identity-backfill-v2", Up: InitializeExternalIdentityClaimsWithDB},
		{ID: "subscription-plan-v2", Up: migrateRelaySchemaV2SubscriptionPlan},
		{ID: "retired-option-migration-v2", Up: migrateRetiredFrontendOptionsStrictWithDB},
		{ID: "runtime-dml-privilege-manifest-v2", Up: ApplyRelayDatabasePrivilegeManifestWithDB},
		{ID: "download-edge-rls-v2", Up: MigratePlatformDownloadEdgeIsolationWithDB},
		{ID: "download-edge-dml-privilege-manifest-v2", Up: ApplyRelayDownloadEdgeDatabasePrivilegeManifestWithDB},
	}
}

func migrateRelaySchemaV2Bootstrap(db *gorm.DB) error {
	for _, step := range relaySchemaV2BootstrapSteps() {
		if err := step.Up(db); err != nil {
			return fmt.Errorf("Relay schema v2 bootstrap step %s failed: %w", step.ID, err)
		}
	}
	return nil
}

func migrateRelaySchemaV2Models(db *gorm.DB) error {
	return db.AutoMigrate(relaySchemaV2Models()...)
}

func migrateRelaySchemaV2PreviousCandidateCatalog(db *gorm.DB) error {
	if db == nil || db.Dialector.Name() != "postgres" {
		return nil
	}
	for _, statement := range []string{
		`ALTER TABLE public.prefill_groups DROP CONSTRAINT IF EXISTS idx_prefill_groups_name`,
		`DROP INDEX IF EXISTS public.idx_prefill_groups_name`,
	} {
		if err := db.Exec(statement).Error; err != nil {
			return errors.New("previous Relay candidate catalog could not be normalized")
		}
	}
	return nil
}

func migrateRelaySchemaV2SubscriptionPlan(db *gorm.DB) error {
	if db.Dialector.Name() == "sqlite" {
		return ensureSubscriptionPlanTableSQLiteWithDB(db)
	}
	return db.AutoMigrate(&SubscriptionPlan{})
}

// migrateRelaySchemaV2NoCatalogDelta is an attested bridge, not a replay. It
// accepts only an exact, actually-applied v1 baseline and performs no schema
// DDL. The enclosing version transaction then reapplies the independently
// frozen v2 principal manifests and appends the real v2 ledger event.
func migrateRelaySchemaV2NoCatalogDelta(db *gorm.DB) error {
	if db == nil {
		return errors.New("Relay schema v2 bridge database is unavailable")
	}
	var state RelaySchemaState
	if err := db.Where("id = ?", relaySchemaStateSingletonID).First(&state).Error; err != nil {
		return errors.New("Relay schema v2 bridge state is unavailable")
	}
	v1Catalog := relaySchemaExpectedCatalogForRuntime(db.Dialector.Name(), relaySchemaV1FrozenVersion)
	v2Catalog := relaySchemaExpectedCatalogForRuntime(db.Dialector.Name(), relaySchemaV2FrozenVersion)
	if state.BaselineVersion != relaySchemaV1FrozenVersion || state.FreshBootstrap ||
		state.CurrentVersion != relaySchemaV1FrozenVersion || state.TargetVersion != relaySchemaV2FrozenVersion ||
		state.State != RelaySchemaStateApplying || !state.Dirty || state.AttemptID == "" ||
		state.CurrentChecksum != relaySchemaV1FrozenChecksumSHA256 || state.TargetChecksum != relaySchemaV2FrozenChecksumSHA256 ||
		(v1Catalog != "" && state.CurrentCatalogSHA256 != v1Catalog) || state.TargetCatalogSHA256 != v2Catalog {
		return errors.New("Relay schema v2 bridge requires the exact v1 state")
	}
	if v1Catalog != "" && v1Catalog != v2Catalog {
		return errors.New("Relay schema v2 bridge is not a no-catalog-delta release")
	}
	if !relaySchemaProvenanceValid(state.SourceRevision, state.SnapshotSHA256) &&
		(RelayDatabaseRoleAttestationRequired() || state.SourceRevision != "" || state.SnapshotSHA256 != "") {
		return errors.New("Relay schema v2 bridge source provenance is invalid")
	}
	var applied []RelaySchemaMigration
	if err := db.Order("version ASC").Find(&applied).Error; err != nil || len(applied) != 1 {
		return errors.New("Relay schema v2 bridge requires the exact v1 ledger")
	}
	v1 := applied[0]
	if v1.Version != relaySchemaV1FrozenVersion || v1.Name != relaySchemaV1FrozenName ||
		v1.Phase != relaySchemaV1FrozenPhase || v1.Checksum != relaySchemaV1FrozenChecksumSHA256 ||
		v1.CatalogSHA256 == "" || v1.CatalogSHA256 != state.CurrentCatalogSHA256 ||
		(v1Catalog != "" && v1.CatalogSHA256 != v1Catalog) || v1.AppliedAt.IsZero() ||
		(!relaySchemaProvenanceValid(v1.SourceRevision, v1.SnapshotSHA256) &&
			(RelayDatabaseRoleAttestationRequired() || v1.SourceRevision != "" || v1.SnapshotSHA256 != "")) {
		return errors.New("Relay schema v2 bridge requires the exact v1 ledger")
	}
	actualCatalog, err := relaySchemaCatalogFingerprintForRuntime(db, relaySchemaV1FrozenVersion)
	if err != nil || actualCatalog != v1.CatalogSHA256 {
		return errors.New("Relay schema v2 bridge requires the exact v1 catalog")
	}
	if RelayDatabaseRoleAttestationRequired() {
		ownerRole := strings.TrimSpace(getenvRelayDatabaseRole(relaySchemaOwnerRoleEnvironment))
		migrationRole := strings.TrimSpace(getenvRelayDatabaseRole(relayMigrationDatabaseRoleEnvironment))
		runtimeRole := strings.TrimSpace(getenvRelayDatabaseRole(relayRuntimeDatabaseRoleEnvironment))
		if !databaseRoleNamePattern.MatchString(ownerRole) || !databaseRoleNamePattern.MatchString(migrationRole) ||
			!databaseRoleNamePattern.MatchString(runtimeRole) || runtimeRole != "relay_runtime" {
			return errors.New("Relay schema v2 bridge role topology is invalid")
		}
		if err := verifyRelayDatabaseRoleTopology(db, ownerRole, migrationRole, runtimeRole); err != nil {
			return errors.New("Relay schema v2 bridge requires the exact v1 role topology")
		}
		if err := verifyRelayRuntimeDatabasePrivilegeManifest(db, runtimeRole, relaySchemaV1FrozenVersion); err != nil {
			return errors.New("Relay schema v2 bridge requires the exact v1 runtime manifest")
		}
		if err := verifyRelayDownloadEdgePreMigrationRole(db); err != nil {
			return errors.New("Relay schema v2 bridge requires the exact pre-migration edge role")
		}
	}
	return nil
}

// RelayLifecycleLock is a PostgreSQL session advisory lock shared by schema
// migration and production root provisioning. It deliberately uses a
// dedicated connection so a pool cannot release the lock between operations.
type RelayLifecycleLock struct {
	connection  *sql.Conn
	database    *sql.DB
	shared      bool
	anchorPID   int
	anchorStart time.Time
	anchorEpoch int64
}

var relayRuntimeAnchorIdentityMu sync.RWMutex
var relayRuntimeAnchorPID int
var relayRuntimeAnchorStart time.Time
var relayRuntimeAnchorEpoch int64

func setRelayRuntimeAnchorIdentity(pid int, startedAt time.Time, epoch int64) {
	relayRuntimeAnchorIdentityMu.Lock()
	relayRuntimeAnchorPID = pid
	relayRuntimeAnchorStart = startedAt
	relayRuntimeAnchorEpoch = epoch
	relayRuntimeAnchorIdentityMu.Unlock()
}

func getRelayRuntimeAnchorIdentity() (int, time.Time, int64, bool) {
	relayRuntimeAnchorIdentityMu.RLock()
	defer relayRuntimeAnchorIdentityMu.RUnlock()
	return relayRuntimeAnchorPID, relayRuntimeAnchorStart, relayRuntimeAnchorEpoch,
		relayRuntimeAnchorPID > 0 && !relayRuntimeAnchorStart.IsZero() && relayRuntimeAnchorEpoch != 0
}

func clearRelayRuntimeAnchorIdentity(pid int, startedAt time.Time, epoch int64) {
	relayRuntimeAnchorIdentityMu.Lock()
	defer relayRuntimeAnchorIdentityMu.Unlock()
	if relayRuntimeAnchorPID == pid && relayRuntimeAnchorStart.Equal(startedAt) && relayRuntimeAnchorEpoch == epoch {
		relayRuntimeAnchorPID = 0
		relayRuntimeAnchorStart = time.Time{}
		relayRuntimeAnchorEpoch = 0
	}
}

func newRelayRuntimeAnchorEpoch() (int64, error) {
	var raw [8]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return 0, err
	}
	epoch := int64(binary.BigEndian.Uint64(raw[:]) & uint64(^uint64(0)>>1))
	if epoch == 0 || epoch == relayLifecycleAdvisoryLock || epoch == relayLifecycleMutationAdvisoryLock {
		return newRelayRuntimeAnchorEpoch()
	}
	return epoch, nil
}

func RelayLifecycleLockRequiresMonitoring(lock *RelayLifecycleLock) bool {
	return lock != nil && lock.connection != nil
}

// MonitorRelayLifecycleLock continuously proves that the dedicated anchor is
// still the same PostgreSQL session and still owns process lock A. The returned
// stop function cancels and joins the monitor; callers must stop it before
// releasing/closing the anchor connection.
func MonitorRelayLifecycleLock(parent context.Context, lock *RelayLifecycleLock, interval time.Duration) (<-chan error, func(), error) {
	if parent == nil || lock == nil || lock.connection == nil || interval <= 0 {
		return nil, nil, errors.New("Relay lifecycle lock monitor configuration is invalid")
	}
	ctx, cancel := context.WithCancel(parent)
	initialProbeContext, cancelInitialProbe := context.WithTimeout(ctx, 5*time.Second)
	initialProbeErr := verifyRelayLifecycleLockAnchor(initialProbeContext, lock)
	cancelInitialProbe()
	if initialProbeErr != nil {
		cancel()
		return nil, nil, initialProbeErr
	}
	if lock.shared {
		relayRuntimeDatabaseLifecycleHealthy.Store(true)
	}
	failures := make(chan error, 1)
	done := make(chan struct{})
	var stopOnce sync.Once
	stop := func() {
		stopOnce.Do(func() {
			cancel()
			<-done
		})
	}
	go func() {
		defer close(done)
		defer close(failures)
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
			}
			probeTimeout := 3 * interval
			if probeTimeout < time.Second {
				probeTimeout = time.Second
			}
			if probeTimeout > 5*time.Second {
				probeTimeout = 5 * time.Second
			}
			probeContext, cancelProbe := context.WithTimeout(ctx, probeTimeout)
			err := verifyRelayLifecycleLockAnchor(probeContext, lock)
			cancelProbe()
			if err != nil {
				if lock.shared {
					relayRuntimeDatabaseLifecycleHealthy.Store(false)
				}
				select {
				case failures <- err:
				default:
				}
				return
			}
		}
	}()
	return failures, stop, nil
}

func verifyRelayLifecycleLockAnchor(ctx context.Context, lock *RelayLifecycleLock) error {
	if lock == nil || lock.connection == nil {
		return errors.New("Relay lifecycle lock anchor is unavailable")
	}
	mode := "ExclusiveLock"
	if lock.shared {
		mode = "ShareLock"
	}
	var held bool
	err := lock.connection.QueryRowContext(ctx, `
SELECT EXISTS (
  SELECT 1
    FROM pg_catalog.pg_locks
   WHERE pid = pg_catalog.pg_backend_pid()
     AND locktype = 'advisory'
     AND classid = (($1::bigint >> 32) & 4294967295)::oid
     AND objid = ($1::bigint & 4294967295)::oid
     AND objsubid = 1
     AND mode = $2
     AND granted
)`, relayLifecycleAdvisoryLock, mode).Scan(&held)
	if err != nil || !held {
		return errors.New("Relay lifecycle lock anchor was lost")
	}
	if lock.shared {
		var epochHeld bool
		err = lock.connection.QueryRowContext(ctx, `
SELECT EXISTS (
  SELECT 1
    FROM pg_catalog.pg_stat_activity activity
    JOIN pg_catalog.pg_locks epoch_lock ON epoch_lock.pid = activity.pid
   WHERE activity.pid = pg_catalog.pg_backend_pid()
     AND activity.backend_start = $1
     AND epoch_lock.locktype = 'advisory'
     AND epoch_lock.classid = (($2::bigint >> 32) & 4294967295)::oid
     AND epoch_lock.objid = ($2::bigint & 4294967295)::oid
     AND epoch_lock.objsubid = 1
     AND epoch_lock.mode = 'ExclusiveLock'
     AND epoch_lock.granted
)`, lock.anchorStart, lock.anchorEpoch).Scan(&epochHeld)
		if err != nil || !epochHeld {
			return errors.New("Relay lifecycle lock anchor epoch was lost")
		}
	}
	return nil
}

func AcquireRelayLifecycleLock(ctx context.Context, db *gorm.DB) (*RelayLifecycleLock, error) {
	return acquireRelayDedicatedLifecycleLock(ctx, db, false)
}

// AcquireRelayRuntimeLifecycleLock holds the shared side of the schema
// lifecycle lock for the complete lifetime of a runtime process. Migrations
// and root provisioning take the exclusive side, removing the startup-status
// TOCTOU where DDL could otherwise begin after readiness was admitted.
func AcquireRelayRuntimeLifecycleLock(ctx context.Context, db *gorm.DB) (*RelayLifecycleLock, error) {
	return acquireRelayDedicatedLifecycleLock(ctx, db, true)
}

func acquireRelayDedicatedLifecycleLock(ctx context.Context, db *gorm.DB, shared bool) (*RelayLifecycleLock, error) {
	if db == nil {
		return nil, errors.New("Relay lifecycle database is unavailable")
	}
	if db.Dialector.Name() != "postgres" {
		if shared && RelayRuntimeDatabaseLifecycleFencingEnabled() {
			relayRuntimeDatabaseLifecycleHealthy.Store(true)
		}
		return &RelayLifecycleLock{shared: shared}, nil
	}
	resolvedDSN := ""
	if shared {
		resolvedDSN = getRelayRuntimeLifecycleDSNSnapshot()
		if resolvedDSN == "" {
			if RelayDatabaseRoleAttestationRequired() {
				return nil, errors.New("Relay runtime lifecycle lock requires the immutable startup DSN snapshot")
			}
			var resolveErr error
			resolvedDSN, resolveErr = ResolveDatabaseDSN("SQL_DSN")
			if resolveErr != nil {
				return nil, errors.New("Relay lifecycle lock DSN source is invalid")
			}
		}
	} else {
		var resolveErr error
		resolvedDSN, resolveErr = ResolveDatabaseDSN("SQL_DSN")
		if resolveErr != nil {
			return nil, errors.New("Relay lifecycle lock DSN source is invalid")
		}
	}
	dsn := strings.TrimSpace(resolvedDSN)
	if dsn == "" || strings.ContainsAny(dsn, "\x00\r\n") {
		return nil, errors.New("Relay lifecycle lock DSN is invalid")
	}
	lockDatabase, err := sql.Open("pgx", dsn)
	if err != nil {
		return nil, errors.New("Relay lifecycle lock database could not be opened")
	}
	lockDatabase.SetMaxOpenConns(1)
	lockDatabase.SetMaxIdleConns(1)
	lock, err := acquireRelayLifecycleLockFromSQLDB(ctx, lockDatabase, shared)
	if err != nil {
		_ = lockDatabase.Close()
		return nil, err
	}
	lock.database = lockDatabase
	return lock, nil
}

func acquireRelayLifecycleLockFromSQLDB(ctx context.Context, sqlDB *sql.DB, shared bool) (*RelayLifecycleLock, error) {
	return acquireRelayLifecycleLockFromSQLDBWithTimeout(ctx, sqlDB, shared, relayLifecycleLockAcquireTimeout)
}

func acquireRelayLifecycleLockFromSQLDBWithTimeout(
	ctx context.Context,
	sqlDB *sql.DB,
	shared bool,
	timeout time.Duration,
) (*RelayLifecycleLock, error) {
	lockContext, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	connection, err := sqlDB.Conn(lockContext)
	if err != nil {
		return nil, errors.New("Relay lifecycle lock connection failed")
	}
	statement := "SELECT pg_catalog.pg_advisory_lock($1)"
	if shared {
		statement = "SELECT pg_catalog.pg_advisory_lock_shared($1)"
	}
	if _, err := connection.ExecContext(lockContext, statement, relayLifecycleAdvisoryLock); err != nil {
		_ = connection.Close()
		return nil, errors.New("Relay lifecycle lock could not be acquired")
	}
	lock := &RelayLifecycleLock{connection: connection, shared: shared}
	if shared && RelayRuntimeDatabaseLifecycleFencingEnabled() {
		if err := connection.QueryRowContext(lockContext, `
SELECT activity.pid, activity.backend_start
  FROM pg_catalog.pg_stat_activity activity
 WHERE activity.pid = pg_catalog.pg_backend_pid()`).Scan(&lock.anchorPID, &lock.anchorStart); err != nil {
			_ = connection.Close()
			return nil, errors.New("Relay runtime lifecycle anchor identity could not be established")
		}
		lock.anchorEpoch, err = newRelayRuntimeAnchorEpoch()
		if err != nil {
			_ = connection.Close()
			return nil, errors.New("Relay runtime lifecycle anchor epoch could not be generated")
		}
		var epochLocked bool
		if err := connection.QueryRowContext(lockContext, `SELECT pg_catalog.pg_try_advisory_lock($1)`, lock.anchorEpoch).Scan(&epochLocked); err != nil || !epochLocked {
			_ = connection.Close()
			return nil, errors.New("Relay runtime lifecycle anchor epoch could not be acquired")
		}
		setRelayRuntimeAnchorIdentity(lock.anchorPID, lock.anchorStart, lock.anchorEpoch)
	}
	return lock, nil
}

func (lock *RelayLifecycleLock) Release(ctx context.Context) error {
	if lock == nil {
		return errors.New("Relay lifecycle lock is unavailable")
	}
	if lock.connection == nil {
		return nil
	}
	if lock.shared && lock.anchorPID > 0 {
		clearRelayRuntimeAnchorIdentity(lock.anchorPID, lock.anchorStart, lock.anchorEpoch)
		if lock.anchorEpoch != 0 {
			var epochUnlocked bool
			if epochErr := lock.connection.QueryRowContext(ctx, `SELECT pg_catalog.pg_advisory_unlock($1)`, lock.anchorEpoch).Scan(&epochUnlocked); epochErr != nil || !epochUnlocked {
				lock.Close()
				return errors.New("Relay lifecycle anchor epoch could not be released cleanly")
			}
		}
	}
	statement := "SELECT pg_catalog.pg_advisory_unlock($1)"
	if lock.shared {
		statement = "SELECT pg_catalog.pg_advisory_unlock_shared($1)"
	}
	var unlocked bool
	err := lock.connection.QueryRowContext(ctx, statement, relayLifecycleAdvisoryLock).Scan(&unlocked)
	closeErr := lock.connection.Close()
	databaseCloseErr := error(nil)
	if lock.database != nil {
		databaseCloseErr = lock.database.Close()
		lock.database = nil
	}
	lock.connection = nil
	if err != nil || !unlocked || closeErr != nil || databaseCloseErr != nil {
		return errors.New("Relay lifecycle lock could not be released cleanly")
	}
	return nil
}

func (lock *RelayLifecycleLock) Close() {
	if lock != nil && lock.shared && lock.anchorPID > 0 {
		clearRelayRuntimeAnchorIdentity(lock.anchorPID, lock.anchorStart, lock.anchorEpoch)
	}
	if lock != nil && lock.connection != nil {
		_ = lock.connection.Close()
		lock.connection = nil
	}
	if lock != nil && lock.database != nil {
		_ = lock.database.Close()
		lock.database = nil
	}
}

func ReleaseRelayLifecycleLockBounded(lock *RelayLifecycleLock) error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	err := lock.Release(ctx)
	if err != nil {
		lock.Close()
	}
	return err
}

// acquireRelayLifecycleTransactionLock binds every schema/root/role mutation
// to the physical PostgreSQL connection that performs it. The outer session
// lock is an operator/process gate; this transaction lock is the database-level
// safety boundary if that anchor connection is terminated or fails over.
func acquireRelayLifecycleTransactionLock(tx *gorm.DB) error {
	if tx == nil {
		return errors.New("Relay lifecycle transaction is unavailable")
	}
	if tx.Dialector.Name() != "postgres" {
		return nil
	}
	if err := tx.Exec(`SET LOCAL lock_timeout = '5min'`).Error; err != nil {
		return errors.New("Relay lifecycle transaction timeout could not be installed")
	}
	if err := tx.Exec(`SELECT pg_catalog.pg_advisory_xact_lock(?)`, relayLifecycleMutationAdvisoryLock).Error; err != nil {
		return errors.New("Relay lifecycle transaction lock could not be acquired")
	}
	return nil
}

// AcquireRelayLifecycleMutationTransactionLock is the narrow offline-command
// boundary for binding an actual mutation transaction to lifecycle fence B.
// Callers must already hold the process-level exclusive lifecycle lock A.
func AcquireRelayLifecycleMutationTransactionLock(tx *gorm.DB) error {
	return acquireRelayLifecycleTransactionLock(tx)
}

func ensureRelaySchemaMetadata(db *gorm.DB) error {
	hasState := db.Migrator().HasTable(&RelaySchemaState{})
	hasLedger := db.Migrator().HasTable(&RelaySchemaMigration{})
	if hasState != hasLedger {
		return errors.New("Relay schema metadata is partially initialized")
	}
	if hasState {
		// Existing metadata is evidence, not repairable bootstrap material. Its
		// singleton row, ledger and guards are validated by status/integrity
		// checks and must never be recreated implicitly.
		return nil
	}
	return db.Transaction(func(tx *gorm.DB) error {
		if err := setRelaySchemaMigrationLocalSearchPath(tx); err != nil {
			return err
		}
		if err := acquireRelayLifecycleTransactionLock(tx); err != nil {
			return err
		}
		freshBootstrap, err := relaySchemaApplicationCatalogIsEmpty(tx)
		if err != nil {
			return err
		}
		if err := tx.AutoMigrate(&RelaySchemaState{}, &RelaySchemaMigration{}); err != nil {
			return errors.New("Relay schema metadata could not be created")
		}
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		initial := RelaySchemaState{
			ID: relaySchemaStateSingletonID, State: RelaySchemaStateClean,
			FreshBootstrap: freshBootstrap, UpdatedAt: now,
		}
		if err := tx.Create(&initial).Error; err != nil {
			return errors.New("Relay schema state could not be initialized")
		}
		return installRelaySchemaLedgerGuards(tx)
	})
}

func relaySchemaApplicationCatalogIsEmpty(db *gorm.DB) (bool, error) {
	var objectCount int64
	switch db.Dialector.Name() {
	case "postgres":
		if err := db.Raw(`
SELECT count(*) FROM (
  SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'public'
  UNION ALL
  SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'public'
  UNION ALL
  SELECT t.oid FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
   WHERE n.nspname = 'public' AND t.typelem = 0
     AND NOT EXISTS (SELECT 1 FROM pg_class c WHERE c.reltype = t.oid)
  UNION ALL
  SELECT c.oid FROM pg_collation c JOIN pg_namespace n ON n.oid = c.collnamespace
   WHERE n.nspname = 'public'
  UNION ALL
  SELECT e.oid FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
   WHERE n.nspname = 'public'
) application_object`).Scan(&objectCount).Error; err != nil {
			return false, errors.New("Relay application catalog could not be inspected")
		}
	case "sqlite":
		if err := db.Raw(`SELECT count(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'`).Scan(&objectCount).Error; err != nil {
			return false, errors.New("Relay application catalog could not be inspected")
		}
	default:
		if err := db.Raw(`SELECT count(*) FROM information_schema.tables WHERE table_schema = DATABASE()`).Scan(&objectCount).Error; err != nil {
			return false, errors.New("Relay application catalog could not be inspected")
		}
	}
	return objectCount == 0, nil
}

func installRelaySchemaLedgerGuards(db *gorm.DB) error {
	switch db.Dialector.Name() {
	case "postgres":
		statements := []string{
			`CREATE OR REPLACE FUNCTION reject_relay_schema_migration_mutation() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'Relay schema migrations are append-only'; END; $$ LANGUAGE plpgsql`,
			`DROP TRIGGER IF EXISTS trg_relay_schema_migrations_no_mutation ON relay_schema_migrations`,
			`CREATE TRIGGER trg_relay_schema_migrations_no_mutation BEFORE UPDATE OR DELETE ON relay_schema_migrations FOR EACH ROW EXECUTE FUNCTION reject_relay_schema_migration_mutation()`,
			`DROP TRIGGER IF EXISTS trg_relay_schema_migrations_no_truncate ON relay_schema_migrations`,
			`CREATE TRIGGER trg_relay_schema_migrations_no_truncate BEFORE TRUNCATE ON relay_schema_migrations FOR EACH STATEMENT EXECUTE FUNCTION reject_relay_schema_migration_mutation()`,
		}
		for _, statement := range statements {
			if err := db.Exec(statement).Error; err != nil {
				return errors.New("Relay schema ledger guard installation failed")
			}
		}
	case "sqlite":
		for _, statement := range []string{
			`CREATE TRIGGER IF NOT EXISTS trg_relay_schema_migrations_no_update BEFORE UPDATE ON relay_schema_migrations BEGIN SELECT RAISE(ABORT, 'Relay schema migrations are append-only'); END`,
			`CREATE TRIGGER IF NOT EXISTS trg_relay_schema_migrations_no_delete BEFORE DELETE ON relay_schema_migrations BEGIN SELECT RAISE(ABORT, 'Relay schema migrations are append-only'); END`,
		} {
			if err := db.Exec(statement).Error; err != nil {
				return errors.New("Relay schema ledger guard installation failed")
			}
		}
	}
	return nil
}

func GetRelaySchemaStatus(db *gorm.DB) (RelaySchemaStatus, error) {
	contract := relaySchemaContractForRuntime()
	status := RelaySchemaStatus{
		Classification: RelaySchemaStatusUnavailable,
		TargetVersion:  contract.TargetVersion,
		MinVersion:     contract.MinVersion,
		MaxVersion:     contract.MaxVersion,
	}
	if db == nil {
		return status, errors.New("Relay schema database is unavailable")
	}
	hasState := db.Migrator().HasTable(&RelaySchemaState{})
	hasLedger := db.Migrator().HasTable(&RelaySchemaMigration{})
	if !hasState && !hasLedger {
		status.Classification = RelaySchemaStatusUninitialized
		status.State = RelaySchemaStateClean
		return status, nil
	}
	if !hasState || !hasLedger {
		status.Classification = RelaySchemaStatusCorrupt
		return status, nil
	}
	var state RelaySchemaState
	if err := db.Where("id = ?", relaySchemaStateSingletonID).First(&state).Error; err != nil {
		status.Classification = RelaySchemaStatusCorrupt
		return status, nil
	}
	status.CurrentVersion = state.CurrentVersion
	status.BaselineVersion = state.BaselineVersion
	status.FreshBootstrap = state.FreshBootstrap
	status.PendingVersion = state.TargetVersion
	status.State = state.State
	status.Dirty = state.Dirty
	status.AttemptID = state.AttemptID
	status.CurrentChecksum = state.CurrentChecksum
	status.TargetChecksum = state.TargetChecksum
	status.CatalogSHA256 = state.CurrentCatalogSHA256
	status.SourceRevision = state.SourceRevision
	status.SnapshotSHA256 = state.SnapshotSHA256
	status.ExpectedCatalogSHA256 = relaySchemaExpectedCatalogForRuntime(db.Dialector.Name(), state.CurrentVersion)
	status.ErrorCode = state.ErrorCode
	if expected, ok := contract.Checksums[state.CurrentVersion]; ok {
		status.ExpectedChecksum = expected
	}

	var applied []RelaySchemaMigration
	if err := db.Order("version ASC").Find(&applied).Error; err != nil {
		return status, errors.New("Relay schema ledger could not be read")
	}
	if state.BaselineVersion < 0 || state.CurrentVersion < 0 || state.TargetVersion < 0 ||
		state.BaselineVersion > state.CurrentVersion ||
		(state.CurrentVersion == 0 && state.BaselineVersion != 0) ||
		(state.CurrentVersion > 0 && (state.BaselineVersion == 0 || state.FreshBootstrap)) {
		status.Classification = RelaySchemaStatusCorrupt
		return status, nil
	}
	definitions := relaySchemaDefinitionsForRuntime()
	definitionsByVersion := make(map[int64]relaySchemaMigrationDefinition, len(definitions))
	for _, definition := range definitions {
		definitionsByVersion[definition.Version] = definition
	}
	expectedLedgerCount := int64(0)
	if state.CurrentVersion > 0 {
		expectedLedgerCount = state.CurrentVersion - state.BaselineVersion + 1
	}
	if expectedLedgerCount < 0 || int64(len(applied)) != expectedLedgerCount {
		status.Classification = RelaySchemaStatusLedgerGap
		return status, nil
	}
	for index, row := range applied {
		expectedVersion := state.BaselineVersion + int64(index)
		if row.Version != expectedVersion {
			status.Classification = RelaySchemaStatusLedgerGap
			return status, nil
		}
		definition, known := definitionsByVersion[expectedVersion]
		if known {
			expectedCatalogSHA256 := relaySchemaExpectedCatalogForRuntime(db.Dialector.Name(), row.Version)
			if row.Checksum != definition.Checksum || row.Name != definition.Name || row.Phase != definition.Phase ||
				row.CatalogSHA256 == "" || (expectedCatalogSHA256 != "" && row.CatalogSHA256 != expectedCatalogSHA256) {
				status.Classification = RelaySchemaStatusCorrupt
				return status, nil
			}
		}
		if !relaySchemaProvenanceValid(row.SourceRevision, row.SnapshotSHA256) &&
			(RelayDatabaseRoleAttestationRequired() || row.SourceRevision != "" || row.SnapshotSHA256 != "") {
			status.Classification = RelaySchemaStatusCorrupt
			return status, nil
		}
	}
	if state.CurrentVersion > contract.MaxVersion || (len(applied) > 0 && applied[len(applied)-1].Version > contract.MaxVersion) {
		status.Classification = RelaySchemaStatusAhead
		return status, nil
	}
	if state.CurrentVersion > 0 && state.CurrentChecksum != contract.Checksums[state.CurrentVersion] {
		status.Classification = RelaySchemaStatusCorrupt
		return status, nil
	}
	if state.CurrentVersion > 0 {
		ledgerCatalogSHA256 := applied[len(applied)-1].CatalogSHA256
		actualCatalogSHA256, err := relaySchemaCatalogFingerprintForRuntime(db, state.CurrentVersion)
		if err != nil {
			return status, err
		}
		status.CatalogSHA256 = actualCatalogSHA256
		expectedCatalogSHA256 := relaySchemaExpectedCatalogForRuntime(db.Dialector.Name(), state.CurrentVersion)
		if state.CurrentCatalogSHA256 == "" || state.CurrentCatalogSHA256 != ledgerCatalogSHA256 ||
			actualCatalogSHA256 != ledgerCatalogSHA256 ||
			(expectedCatalogSHA256 != "" && actualCatalogSHA256 != expectedCatalogSHA256) {
			status.Classification = RelaySchemaStatusCorrupt
			return status, nil
		}
	}
	switch state.State {
	case RelaySchemaStateApplying, RelaySchemaStateFailed:
		definition, known := definitionsByVersion[state.TargetVersion]
		expectedTargetCatalogSHA256 := relaySchemaExpectedCatalogForRuntime(db.Dialector.Name(), state.TargetVersion)
		validTransition := state.TargetVersion == state.CurrentVersion+1
		if state.CurrentVersion == 0 && state.BaselineVersion == 0 && state.FreshBootstrap &&
			state.TargetVersion == contract.TargetVersion && known && definition.Bootstrap != nil {
			validTransition = true
		}
		if !state.Dirty || state.AttemptID == "" || !known || !validTransition ||
			state.TargetChecksum != definition.Checksum || state.TargetCatalogSHA256 != expectedTargetCatalogSHA256 {
			status.Classification = RelaySchemaStatusCorrupt
			return status, nil
		}
		if !relaySchemaProvenanceValid(state.SourceRevision, state.SnapshotSHA256) &&
			(RelayDatabaseRoleAttestationRequired() || state.SourceRevision != "" || state.SnapshotSHA256 != "") {
			status.Classification = RelaySchemaStatusCorrupt
			return status, nil
		}
		if state.State == RelaySchemaStateApplying {
			status.Classification = RelaySchemaStatusMigrating
		} else {
			status.Classification = RelaySchemaStatusDirty
		}
		return status, nil
	case RelaySchemaStateClean:
		if state.Dirty || state.TargetVersion != state.CurrentVersion || state.TargetChecksum != state.CurrentChecksum ||
			state.TargetCatalogSHA256 != state.CurrentCatalogSHA256 || state.ErrorCode != "" {
			status.Classification = RelaySchemaStatusCorrupt
			return status, nil
		}
		if state.CurrentVersion > 0 {
			latest := applied[len(applied)-1]
			if state.SourceRevision != latest.SourceRevision || state.SnapshotSHA256 != latest.SnapshotSHA256 ||
				(!relaySchemaProvenanceValid(state.SourceRevision, state.SnapshotSHA256) &&
					(RelayDatabaseRoleAttestationRequired() || state.SourceRevision != "" || state.SnapshotSHA256 != "")) {
				status.Classification = RelaySchemaStatusCorrupt
				return status, nil
			}
		}
	default:
		status.Classification = RelaySchemaStatusCorrupt
		return status, nil
	}
	if state.CurrentVersion < contract.MinVersion {
		status.Classification = RelaySchemaStatusTooOld
		return status, nil
	}
	status.Compatible = true
	if state.CurrentVersion == contract.TargetVersion {
		status.Classification = RelaySchemaStatusCurrent
		status.Current = true
		return status, nil
	}
	status.Classification = RelaySchemaStatusCompatible
	return status, nil
}

func RequireRelaySchemaCompatible(db *gorm.DB) (RelaySchemaStatus, error) {
	status, err := GetRelaySchemaStatus(db)
	if err != nil {
		return status, err
	}
	if !status.Compatible {
		return status, fmt.Errorf("Relay database schema is %s", status.Classification)
	}
	return status, nil
}

func RequireRelaySchemaCurrent(db *gorm.DB) (RelaySchemaStatus, error) {
	status, err := GetRelaySchemaStatus(db)
	if err != nil {
		return status, err
	}
	if !status.Current {
		return status, fmt.Errorf("Relay database schema is %s", status.Classification)
	}
	return status, nil
}

type relaySchemaExecutionPlan struct {
	Bootstrap   *relaySchemaMigrationDefinition
	Incremental []relaySchemaMigrationDefinition
}

// buildRelaySchemaExecutionPlan is the version-evolution fence for historical
// baselines. A future binary must provide a latest-version frozen bootstrap
// snapshot for a truly empty database; it never replays an older baseline that
// may still reference live ORM models. Existing versioned databases take only
// immutable incrementals. Unversioned legacy data and a dirty older bootstrap
// must first be handled by the pinned image for that older version.
func buildRelaySchemaExecutionPlan(
	status RelaySchemaStatus,
	definitions []relaySchemaMigrationDefinition,
	targetVersion int64,
) (relaySchemaExecutionPlan, error) {
	plan := relaySchemaExecutionPlan{}
	if targetVersion < 1 || len(definitions) == 0 {
		return plan, errors.New("Relay schema migration registry is empty")
	}
	byVersion := make(map[int64]relaySchemaMigrationDefinition, len(definitions))
	for _, definition := range definitions {
		if definition.Version < 1 || definition.Version > targetVersion || definition.Name == "" || definition.Phase == "" ||
			definition.Checksum == "" || definition.Up == nil {
			return plan, errors.New("Relay schema migration registry is invalid")
		}
		if _, duplicate := byVersion[definition.Version]; duplicate {
			return plan, errors.New("Relay schema migration registry contains a duplicate")
		}
		byVersion[definition.Version] = definition
	}
	for version := int64(1); version <= targetVersion; version++ {
		if _, exists := byVersion[version]; !exists {
			return plan, errors.New("Relay schema migration registry contains a gap")
		}
	}
	if status.CurrentVersion == 0 && status.FreshBootstrap {
		if (status.Classification == RelaySchemaStatusDirty || status.Classification == RelaySchemaStatusMigrating) &&
			status.PendingVersion != targetVersion {
			return plan, errors.New("Relay schema dirty historical bootstrap requires its pinned migrator image")
		}
		latest := byVersion[targetVersion]
		if latest.Bootstrap == nil {
			return plan, errors.New("Relay schema latest bootstrap snapshot is unavailable")
		}
		plan.Bootstrap = &latest
		return plan, nil
	}
	if status.CurrentVersion == 0 && targetVersion > 1 {
		return plan, errors.New("Relay schema unversioned legacy database requires the pinned v1 migrator image")
	}
	for version := status.CurrentVersion + 1; version <= targetVersion; version++ {
		plan.Incremental = append(plan.Incremental, byVersion[version])
	}
	return plan, nil
}

func validateRelaySchemaRegistry(
	contract RelaySchemaContract,
	definitions []relaySchemaMigrationDefinition,
	dialect string,
	catalogForVersion func(string, int64) string,
	catalogAlgorithmAvailable func(string, int64) bool,
	privilegeManifestForVersion func(int64) (map[string]relayTablePrivilegeSet, error),
) error {
	if catalogForVersion == nil || catalogAlgorithmAvailable == nil || privilegeManifestForVersion == nil {
		return errors.New("Relay schema registry validators are unavailable")
	}
	if contract.TargetVersion < 1 || contract.MinVersion < 1 || contract.MinVersion > contract.TargetVersion ||
		contract.MaxVersion != contract.TargetVersion || len(definitions) != int(contract.TargetVersion) ||
		len(contract.Checksums) != int(contract.TargetVersion) {
		return errors.New("Relay schema contract is inconsistent")
	}
	byVersion := make(map[int64]relaySchemaMigrationDefinition, len(definitions))
	for _, definition := range definitions {
		if definition.Version < 1 || definition.Version > contract.TargetVersion || definition.Name == "" ||
			definition.Phase == "" || definition.Checksum == "" || definition.Up == nil {
			return errors.New("Relay schema migration registry is invalid")
		}
		if _, duplicate := byVersion[definition.Version]; duplicate {
			return errors.New("Relay schema migration registry contains a duplicate")
		}
		byVersion[definition.Version] = definition
	}
	for version := int64(1); version <= contract.TargetVersion; version++ {
		definition, exists := byVersion[version]
		expectedChecksum, checksumExists := contract.Checksums[version]
		if !exists || !checksumExists || expectedChecksum == "" || definition.Checksum != expectedChecksum {
			return errors.New("Relay schema contract checksum registry is inconsistent")
		}
		if dialect == "postgres" {
			expectedCatalog := catalogForVersion(dialect, version)
			if expectedCatalog == "" || expectedCatalog == "sha256:pending" || !catalogAlgorithmAvailable(dialect, version) {
				return errors.New("Relay schema PostgreSQL catalog registry is incomplete")
			}
		}
		manifest, manifestErr := privilegeManifestForVersion(version)
		if manifestErr != nil || len(manifest) == 0 {
			return errors.New("Relay schema runtime privilege manifest registry is incomplete")
		}
	}
	latest := byVersion[contract.TargetVersion]
	if latest.Bootstrap == nil {
		return errors.New("Relay schema latest bootstrap snapshot is unavailable")
	}
	v1 := byVersion[relaySchemaV1FrozenVersion]
	if v1.Version != relaySchemaV1FrozenVersion || v1.Name != relaySchemaV1FrozenName ||
		v1.Phase != relaySchemaV1FrozenPhase || v1.Checksum != relaySchemaV1FrozenChecksumSHA256 ||
		RelaySchemaV1Checksum() != relaySchemaV1FrozenChecksumSHA256 {
		return errors.New("Relay schema historical v1 definition changed")
	}
	v2 := byVersion[relaySchemaV2FrozenVersion]
	if v2.Version != relaySchemaV2FrozenVersion || v2.Name != relaySchemaV2FrozenName ||
		v2.Phase != relaySchemaV2FrozenPhase || v2.Checksum != relaySchemaV2FrozenChecksumSHA256 ||
		RelaySchemaV2Checksum() != relaySchemaV2FrozenChecksumSHA256 || v2.Up == nil || v2.Bootstrap == nil {
		return errors.New("Relay schema v2 definition is not frozen")
	}
	return nil
}

func RunRelaySchemaMigrations(ctx context.Context, resumeAttemptID string) (RelaySchemaMigrationResult, error) {
	result := RelaySchemaMigrationResult{SchemaVersion: 1, Kind: "relay_schema_migration"}
	if DB == nil {
		return result, errors.New("Relay schema database is unavailable")
	}
	relaySchemaMigrationProcessMu.Lock()
	defer relaySchemaMigrationProcessMu.Unlock()

	lock, err := AcquireRelayLifecycleLock(ctx, DB)
	if err != nil {
		return result, err
	}
	defer func() { _ = ReleaseRelayLifecycleLockBounded(lock) }()

	// The migrator has not assumed the schema owner yet and no schema DDL has
	// run. Reject hostile event triggers, rogue schemas, shared-object drift,
	// or protected-role ACL/ownership drift at this read-only boundary.
	if RelayDatabaseRoleAttestationRequired() {
		if err := verifyRelayProtectedDatabaseExactSurfaceFromEnvironment(DB); err != nil {
			return result, err
		}
	}
	if err := assumeRelaySchemaOwnerRole(DB); err != nil {
		return result, err
	}
	if err := verifyRelayMigrationDatabaseRole(DB); err != nil {
		return result, err
	}
	configuredSourceRevision, configuredSnapshotSHA256, err := relaySchemaConfiguredProvenance()
	if err != nil {
		return result, err
	}
	definitions := relaySchemaDefinitionsForRuntime()
	contract := relaySchemaContractForRuntime()
	if err := validateRelaySchemaRegistry(
		contract,
		definitions,
		DB.Dialector.Name(),
		relaySchemaExpectedCatalogForRuntime,
		relaySchemaCatalogAlgorithmForRuntime,
		relaySchemaPrivilegeManifestRegistryForVersion,
	); err != nil {
		return result, err
	}
	status, err := GetRelaySchemaStatus(DB)
	if err != nil {
		return result, err
	}
	result.FromVersion = status.CurrentVersion
	result.ToVersion = status.CurrentVersion
	result.AttemptID = status.AttemptID
	result.Status = status
	if status.Current {
		// A repeated same-image deployment must be a true no-op. The edge is
		// already in its post-provision LOGIN state here; requiring the upgrade
		// stub would force every ordinary redeploy to strip and then rebuild its
		// DML grants. Real upgrades and dirty resumes take the strict NOLOGIN
		// pre-stub branch below.
		if RelayDatabaseRoleAttestationRequired() {
			deployedErr := verifyRelayDownloadEdgeCurrentDatabaseRole(DB, status.CurrentVersion, true)
			if deployedErr != nil {
				// The schema transaction may have committed while the one-shot
				// process lost its final acknowledgement. Exact current DML plus
				// NOLOGIN is safe to acknowledge so the post-provisioner can attach
				// the login; a zero-ACL pre-stub still fails the manifest check.
				if committedErr := verifyRelayDownloadEdgeCurrentDatabaseRole(DB, status.CurrentVersion, false); committedErr != nil {
					// A status-aware pre-provisioner may deliberately return an
					// already-current role to the strict zero-ACL NOLOGIN stub. Rebuild
					// both DML surfaces from the server-owned versioned manifests in
					// one transaction; partial or extra ACLs fail the stub check.
					if stubErr := verifyRelayDownloadEdgePreMigrationRole(DB); stubErr != nil {
						return result, deployedErr
					}
					if err := DB.Transaction(func(tx *gorm.DB) error {
						if err := setRelaySchemaMigrationLocalSearchPath(tx); err != nil {
							return err
						}
						if err := acquireRelayLifecycleTransactionLock(tx); err != nil {
							return err
						}
						if err := applyRelayDatabasePrivilegeManifestForVersion(tx, status.CurrentVersion); err != nil {
							return err
						}
						return applyRelayDownloadEdgeDatabasePrivilegeManifestForVersion(tx, status.CurrentVersion)
					}); err != nil {
						return result, err
					}
					if err := verifyRelayDownloadEdgeCurrentDatabaseRole(DB, status.CurrentVersion, false); err != nil {
						return result, err
					}
				}
			}
		}
		result.State = "current"
		result.Status = status
		if err := ReleaseRelayLifecycleLockBounded(lock); err != nil {
			return result, err
		}
		return result, nil
	}
	if status.Classification == RelaySchemaStatusCorrupt || status.Classification == RelaySchemaStatusAhead || status.Classification == RelaySchemaStatusLedgerGap {
		return result, fmt.Errorf("Relay schema migration refuses %s state", status.Classification)
	}
	if RelayDatabaseRoleAttestationRequired() {
		if err := verifyRelayDownloadEdgePreMigrationRole(DB); err != nil {
			return result, err
		}
	}
	if status.Classification == RelaySchemaStatusUninitialized {
		if err := ensureRelaySchemaMetadata(DB); err != nil {
			return result, err
		}
		status, err = GetRelaySchemaStatus(DB)
		if err != nil {
			return result, err
		}
		result.FromVersion = status.CurrentVersion
		result.ToVersion = status.CurrentVersion
		result.AttemptID = status.AttemptID
		result.Status = status
	}
	if status.Classification == RelaySchemaStatusDirty || status.Classification == RelaySchemaStatusMigrating {
		if resumeAttemptID == "" || resumeAttemptID != status.AttemptID {
			return result, errors.New("Relay schema is dirty; an exact resume attempt id is required")
		}
		if status.SourceRevision != configuredSourceRevision || status.SnapshotSHA256 != configuredSnapshotSHA256 {
			return result, errors.New("Relay schema dirty attempt requires its exact source artifact")
		}
	} else if resumeAttemptID != "" {
		return result, errors.New("Relay schema resume is not applicable to a clean state")
	}

	plan, err := buildRelaySchemaExecutionPlan(status, definitions, contract.TargetVersion)
	if err != nil {
		return result, err
	}
	if plan.Bootstrap != nil {
		definition := *plan.Bootstrap
		attemptID := status.AttemptID
		if attemptID == "" || resumeAttemptID == "" {
			attemptID = uuid.NewString()
		}
		if resumeAttemptID != "" && (status.PendingVersion != definition.Version || status.TargetChecksum != definition.Checksum) {
			return result, errors.New("Relay schema dirty state does not match the bootstrap registry")
		}
		result.AttemptID = attemptID
		if err := markRelaySchemaApplying(DB, definition, attemptID); err != nil {
			return result, err
		}
		migrationErr := runRelaySchemaBootstrapTransaction(DB, definitions, definition, attemptID)
		if migrationErr != nil {
			committed, reconcileErr := reconcileRelaySchemaCommitOutcome(DB, definition, attemptID, 0)
			if reconcileErr != nil {
				return result, reconcileErr
			}
			if !committed {
				if latest, statusErr := GetRelaySchemaStatus(DB); statusErr == nil {
					result.Status = latest
					result.State = latest.Classification
				}
				return result, fmt.Errorf("Relay schema bootstrap failed: %w", migrationErr)
			}
		}
		status, err = GetRelaySchemaStatus(DB)
		if err != nil {
			return result, err
		}
		result.Status = status
		result.ToVersion = definition.Version
		resumeAttemptID = ""
	}
	for _, definition := range plan.Incremental {
		if definition.Version != status.CurrentVersion+1 {
			return result, errors.New("Relay schema migration registry contains a gap")
		}
		attemptID := status.AttemptID
		if attemptID == "" || resumeAttemptID == "" {
			attemptID = uuid.NewString()
		}
		if resumeAttemptID != "" && (status.PendingVersion != definition.Version || status.TargetChecksum != definition.Checksum) {
			return result, errors.New("Relay schema dirty state does not match the migration registry")
		}
		result.AttemptID = attemptID
		if err := markRelaySchemaApplying(DB, definition, attemptID); err != nil {
			return result, err
		}
		migrationErr := runRelaySchemaDefinitionTransaction(DB, definition, attemptID)
		if migrationErr != nil {
			committed, reconcileErr := reconcileRelaySchemaCommitOutcome(DB, definition, attemptID, status.CurrentVersion)
			if reconcileErr != nil {
				return result, reconcileErr
			}
			if !committed {
				if latest, statusErr := GetRelaySchemaStatus(DB); statusErr == nil {
					result.Status = latest
					result.State = latest.Classification
				}
				return result, fmt.Errorf("Relay schema migration failed: %w", migrationErr)
			}
		}
		status, err = GetRelaySchemaStatus(DB)
		if err != nil {
			return result, err
		}
		result.Status = status
		result.ToVersion = definition.Version
		resumeAttemptID = ""
	}
	status, err = RequireRelaySchemaCurrent(DB)
	if err != nil {
		return result, err
	}
	result.State = "migrated"
	if result.FromVersion == result.ToVersion {
		result.State = "current"
	}
	result.Status = status
	if err := ReleaseRelayLifecycleLockBounded(lock); err != nil {
		return result, err
	}
	return result, nil
}

// reconcileRelaySchemaCommitOutcome handles the PostgreSQL commit-ack-loss
// case. A clean, fully attested ledger commit wins; it must never be rewritten
// to failed merely because COMMIT's response was lost. Only the still-applying
// pre-version state may transition to failed.
func reconcileRelaySchemaCommitOutcome(
	db *gorm.DB,
	definition relaySchemaMigrationDefinition,
	attemptID string,
	previousVersion int64,
) (bool, error) {
	status, statusErr := GetRelaySchemaStatus(db)
	if statusErr == nil && relaySchemaDefinitionCommitted(status, definition) {
		return true, nil
	}
	if err := markRelaySchemaFailed(db, definition, attemptID, previousVersion, "RELAY_SCHEMA_MIGRATION_FAILED"); err == nil {
		return false, nil
	}
	// A concurrent/retried observation may see the commit after the fenced
	// failed update affected zero rows. Re-attest before declaring ambiguity.
	status, statusErr = GetRelaySchemaStatus(db)
	if statusErr == nil && relaySchemaDefinitionCommitted(status, definition) {
		return true, nil
	}
	return false, errors.New("Relay schema migration commit outcome is ambiguous")
}

func relaySchemaDefinitionCommitted(status RelaySchemaStatus, definition relaySchemaMigrationDefinition) bool {
	fullyAttestedClassification := status.Classification == RelaySchemaStatusCurrent ||
		status.Classification == RelaySchemaStatusCompatible || status.Classification == RelaySchemaStatusTooOld
	return fullyAttestedClassification && status.State == RelaySchemaStateClean && !status.Dirty &&
		status.CurrentVersion == definition.Version && status.CurrentChecksum == definition.Checksum
}

func markRelaySchemaApplying(db *gorm.DB, definition relaySchemaMigrationDefinition, attemptID string) error {
	sourceRevision, snapshotSHA256, err := relaySchemaConfiguredProvenance()
	if err != nil {
		return err
	}
	return db.Transaction(func(tx *gorm.DB) error {
		if err := acquireRelayLifecycleTransactionLock(tx); err != nil {
			return err
		}
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		result := tx.Model(&RelaySchemaState{}).Where("id = ?", relaySchemaStateSingletonID).Updates(map[string]any{
			"target_version":        definition.Version,
			"state":                 RelaySchemaStateApplying,
			"dirty":                 true,
			"attempt_id":            attemptID,
			"target_checksum":       definition.Checksum,
			"target_catalog_sha256": relaySchemaExpectedCatalogForRuntime(tx.Dialector.Name(), definition.Version),
			"source_revision":       sourceRevision,
			"snapshot_sha256":       snapshotSHA256,
			"started_at":            now,
			"finished_at":           nil,
			"error_code":            "",
			"updated_at":            now,
		})
		if result.Error != nil || result.RowsAffected != 1 {
			return errors.New("Relay schema applying state could not be recorded")
		}
		return nil
	})
}

func runRelaySchemaDefinitionTransaction(db *gorm.DB, definition relaySchemaMigrationDefinition, attemptID string) error {
	sourceRevision, snapshotSHA256, err := relaySchemaConfiguredProvenance()
	if err != nil {
		return err
	}
	return db.Transaction(func(tx *gorm.DB) error {
		if err := setRelaySchemaMigrationLocalSearchPath(tx); err != nil {
			return err
		}
		if err := acquireRelayLifecycleTransactionLock(tx); err != nil {
			return err
		}
		if err := definition.Up(tx); err != nil {
			return err
		}
		if err := applyRelayDatabasePrivilegeManifestForVersion(tx, definition.Version); err != nil {
			return err
		}
		if err := applyRelayDownloadEdgeDatabasePrivilegeManifestForVersion(tx, definition.Version); err != nil {
			return err
		}
		catalogSHA256, err := relaySchemaCatalogFingerprintForRuntime(tx, definition.Version)
		if err != nil {
			return err
		}
		expectedCatalogSHA256 := relaySchemaExpectedCatalogForRuntime(tx.Dialector.Name(), definition.Version)
		if expectedCatalogSHA256 != "" && catalogSHA256 != expectedCatalogSHA256 {
			return fmt.Errorf("Relay schema catalog does not match the frozen migration artifact: expected %s, got %s", expectedCatalogSHA256, catalogSHA256)
		}
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		migration := RelaySchemaMigration{
			Version:        definition.Version,
			Name:           definition.Name,
			Phase:          definition.Phase,
			Checksum:       definition.Checksum,
			CatalogSHA256:  catalogSHA256,
			AppliedAt:      now,
			SourceRevision: sourceRevision,
			SnapshotSHA256: snapshotSHA256,
		}
		if err := tx.Create(&migration).Error; err != nil {
			return err
		}
		result := tx.Model(&RelaySchemaState{}).
			Where("id = ? AND attempt_id = ? AND state = ?", relaySchemaStateSingletonID, attemptID, RelaySchemaStateApplying).
			Updates(map[string]any{
				"baseline_version":       gorm.Expr("CASE WHEN baseline_version = 0 THEN ? ELSE baseline_version END", definition.Version),
				"fresh_bootstrap":        false,
				"current_version":        definition.Version,
				"target_version":         definition.Version,
				"state":                  RelaySchemaStateClean,
				"dirty":                  false,
				"current_checksum":       definition.Checksum,
				"target_checksum":        definition.Checksum,
				"current_catalog_sha256": catalogSHA256,
				"target_catalog_sha256":  catalogSHA256,
				"finished_at":            now,
				"error_code":             "",
				"updated_at":             now,
			})
		if result.Error != nil || result.RowsAffected != 1 {
			return errors.New("Relay schema completion state was fenced")
		}
		return nil
	})
}

func runRelaySchemaBootstrapTransaction(
	db *gorm.DB,
	_ []relaySchemaMigrationDefinition,
	target relaySchemaMigrationDefinition,
	attemptID string,
) error {
	sourceRevision, snapshotSHA256, err := relaySchemaConfiguredProvenance()
	if err != nil {
		return err
	}
	return db.Transaction(func(tx *gorm.DB) error {
		if err := setRelaySchemaMigrationLocalSearchPath(tx); err != nil {
			return err
		}
		if err := acquireRelayLifecycleTransactionLock(tx); err != nil {
			return err
		}
		if target.Bootstrap == nil {
			return errors.New("Relay schema bootstrap snapshot is unavailable")
		}
		if err := target.Bootstrap(tx); err != nil {
			return err
		}
		if err := applyRelayDatabasePrivilegeManifestForVersion(tx, target.Version); err != nil {
			return err
		}
		if err := applyRelayDownloadEdgeDatabasePrivilegeManifestForVersion(tx, target.Version); err != nil {
			return err
		}
		catalogSHA256, err := relaySchemaCatalogFingerprintForRuntime(tx, target.Version)
		if err != nil {
			return err
		}
		expectedCatalogSHA256 := relaySchemaExpectedCatalogForRuntime(tx.Dialector.Name(), target.Version)
		if expectedCatalogSHA256 != "" && catalogSHA256 != expectedCatalogSHA256 {
			return fmt.Errorf("Relay schema catalog does not match the frozen bootstrap artifact: expected %s, got %s", expectedCatalogSHA256, catalogSHA256)
		}
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		migration := RelaySchemaMigration{
			Version: target.Version, Name: target.Name, Phase: target.Phase,
			Checksum: target.Checksum, CatalogSHA256: catalogSHA256, AppliedAt: now,
			SourceRevision: sourceRevision,
			SnapshotSHA256: snapshotSHA256,
		}
		if err := tx.Create(&migration).Error; err != nil {
			return err
		}
		result := tx.Model(&RelaySchemaState{}).
			Where(
				"id = ? AND attempt_id = ? AND state = ? AND current_version = 0 AND baseline_version = 0 AND fresh_bootstrap = ?",
				relaySchemaStateSingletonID, attemptID, RelaySchemaStateApplying, true,
			).
			Updates(map[string]any{
				"baseline_version":       target.Version,
				"fresh_bootstrap":        false,
				"current_version":        target.Version,
				"target_version":         target.Version,
				"state":                  RelaySchemaStateClean,
				"dirty":                  false,
				"current_checksum":       target.Checksum,
				"target_checksum":        target.Checksum,
				"current_catalog_sha256": catalogSHA256,
				"target_catalog_sha256":  catalogSHA256,
				"finished_at":            now,
				"error_code":             "",
				"updated_at":             now,
			})
		if result.Error != nil || result.RowsAffected != 1 {
			return errors.New("Relay schema bootstrap completion state was fenced")
		}
		return nil
	})
}

// Production connections set search_path to public only. PostgreSQL implicitly
// resolves pg_catalog first when it is not named explicitly, while
// current_schema() remains public for GORM's schema introspection and
// unqualified CREATE targets the locked-down, owner-only application schema.
// Pin the same contract locally so a caller cannot alter migration placement.
func setRelaySchemaMigrationLocalSearchPath(tx *gorm.DB) error {
	if tx.Dialector.Name() != "postgres" {
		return nil
	}
	if err := tx.Exec(`SET LOCAL search_path = public`).Error; err != nil {
		return errors.New("Relay schema migration search path could not be fixed")
	}
	return nil
}

func markRelaySchemaFailed(
	db *gorm.DB,
	definition relaySchemaMigrationDefinition,
	attemptID string,
	previousVersion int64,
	errorCode string,
) error {
	return db.Transaction(func(tx *gorm.DB) error {
		if err := acquireRelayLifecycleTransactionLock(tx); err != nil {
			return err
		}
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		result := tx.Model(&RelaySchemaState{}).
			Where(
				"id = ? AND attempt_id = ? AND state = ? AND current_version = ? AND target_version = ?",
				relaySchemaStateSingletonID,
				attemptID,
				RelaySchemaStateApplying,
				previousVersion,
				definition.Version,
			).
			Updates(map[string]any{
				"target_version":        definition.Version,
				"state":                 RelaySchemaStateFailed,
				"dirty":                 true,
				"target_checksum":       definition.Checksum,
				"target_catalog_sha256": relaySchemaExpectedCatalogForRuntime(tx.Dialector.Name(), definition.Version),
				"finished_at":           now,
				"error_code":            errorCode,
				"updated_at":            now,
			})
		if result.Error != nil || result.RowsAffected != 1 {
			return errors.New("Relay schema failure state could not be recorded")
		}
		return nil
	})
}

func assumeRelaySchemaOwnerRole(db *gorm.DB) error {
	if db.Dialector.Name() != "postgres" || !RelayDatabaseRoleAttestationRequired() {
		return nil
	}
	owner := strings.TrimSpace(os.Getenv("RELAY_SCHEMA_OWNER_DATABASE_ROLE"))
	if !databaseRoleNamePattern.MatchString(owner) {
		return errors.New("Relay schema owner database role is invalid")
	}
	// The migration CLI is the only caller and holds the process migration
	// mutex. SET ROLE applies to the pooled session used for each statement only
	// when configured in the DSN, so production requires current_user already to
	// be the owner and uses this statement as an explicit fail-closed assertion.
	var currentUser string
	if err := db.Raw("SELECT current_user").Scan(&currentUser).Error; err != nil {
		return errors.New("Relay migration database role could not be inspected")
	}
	if currentUser == owner {
		return nil
	}
	return errors.New("Relay migration SQL_DSN must assume the configured schema owner role on every connection")
}
