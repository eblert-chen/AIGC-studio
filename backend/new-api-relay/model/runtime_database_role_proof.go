package model

import (
	"context"
	"database/sql"
	"errors"
	"os"
	"strings"
	"time"

	"github.com/google/uuid"
	"gorm.io/gorm"
)

// RelayRuntimeDatabaseRoleProof binds the protected API's expensive catalog
// attestation to one live runtime pool and one immutable Relay release. The
// fields remain process-local so configuration or a serialized value cannot
// manufacture a successful readiness proof.
type RelayRuntimeDatabaseRoleProof struct {
	pool         *sql.DB
	schemaStatus RelaySchemaStatus
	role         string
	edgeRole     string
	verifiedAt   time.Time
}

func (proof RelayRuntimeDatabaseRoleProof) SchemaStatus() RelaySchemaStatus {
	return proof.schemaStatus
}

// VerifiedAt is the completion instant owned by the full attestation. A later
// install or refresh must never rewrite this time and revive an old proof.
func (proof RelayRuntimeDatabaseRoleProof) VerifiedAt() time.Time {
	return proof.verifiedAt
}

// SameRelease includes the complete schema/candidate tuple, including the
// migration attempt identity, and the exact process pool and runtime role.
func (proof RelayRuntimeDatabaseRoleProof) SameRelease(other RelayRuntimeDatabaseRoleProof) bool {
	return proof.pool != nil && proof.pool == other.pool && proof.role == "relay_runtime" &&
		proof.role == other.role && proof.edgeRole == relayDownloadEdgeDatabaseRoleName &&
		proof.edgeRole == other.edgeRole && proof.schemaStatus == other.schemaStatus
}

func relayRuntimeDatabaseRoleConfiguration() (ownerRole, migrationRole, runtimeRole string, err error) {
	runtimeRole = strings.TrimSpace(os.Getenv(relayRuntimeDatabaseRoleEnvironment))
	ownerRole = strings.TrimSpace(os.Getenv(relaySchemaOwnerRoleEnvironment))
	migrationRole = strings.TrimSpace(os.Getenv(relayMigrationDatabaseRoleEnvironment))
	if !databaseRoleNamePattern.MatchString(runtimeRole) || !databaseRoleNamePattern.MatchString(ownerRole) ||
		!databaseRoleNamePattern.MatchString(migrationRole) || runtimeRole != "relay_runtime" ||
		ownerRole == runtimeRole || migrationRole == runtimeRole || ownerRole == migrationRole {
		return "", "", "", errors.New("Relay database role topology configuration is invalid")
	}
	return ownerRole, migrationRole, runtimeRole, nil
}

// verifyRelayRuntimeDatabaseRoleSession checks only the current runtime login
// and its direct/effective DML boundary. Full catalog and shared topology checks
// are deliberately selected by the caller so a live probe cannot recurse into
// the expensive startup surface.
func verifyRelayRuntimeDatabaseRoleSession(db *gorm.DB) (RelayRuntimeDatabaseRoleStatus, error) {
	status := RelayRuntimeDatabaseRoleStatus{Required: true, State: "healthy"}
	_, _, expected, err := relayRuntimeDatabaseRoleConfiguration()
	if err != nil {
		status.State = "unavailable"
		return status, err
	}
	var value relayDatabaseRoleAttestation
	result := db.Raw(relayDatabaseRoleAttestationSQL).Scan(&value)
	if result.Error != nil || result.RowsAffected != 1 {
		status.State = "unavailable"
		return status, errors.New("Relay runtime database role could not be attested")
	}
	status.Role = value.CurrentUser
	if value.SessionUser != expected || value.CurrentUser != expected || !value.CanLogin || !value.SafeSearchPath ||
		value.Superuser || value.BypassRLS || value.CreateDatabase || value.CreateRole || value.Replication || value.Inherits ||
		value.CanCreateDatabase || value.CanCreatePublicSchema || value.CanCreateTemporary ||
		value.OwnsApplicationObject || value.CanAssumeDangerousRole || value.CanTruncateApplicationTable {
		status.State = "unavailable"
		return status, errors.New("Relay runtime database role has forbidden DDL or ownership privileges")
	}
	ownsForbiddenObject, err := relayRoleOwnsForbiddenDatabaseObject(db, expected)
	if err != nil {
		status.State = "unavailable"
		return status, err
	}
	if ownsForbiddenObject {
		status.State = "unavailable"
		return status, errors.New("Relay runtime database role owns a forbidden database object")
	}
	if !value.CanReadSchemaState || !value.CanReadSchemaLedger || !value.CanReadUsers || !value.CanWriteUsers ||
		!value.CanReadChannels || !value.CanWriteChannels || !value.CanReadGenerationJobs || !value.CanWriteGenerationJobs {
		status.State = "unavailable"
		return status, errors.New("Relay runtime database role is missing required DML privileges")
	}
	if value.CanInsertSchemaState || value.CanUpdateSchemaState || value.CanDeleteSchemaState || value.CanTruncateSchemaState ||
		value.CanInsertSchemaLedger || value.CanUpdateSchemaLedger || value.CanDeleteSchemaLedger || value.CanTruncateSchemaLedger {
		status.State = "unavailable"
		return status, errors.New("Relay runtime database role can mutate schema metadata")
	}
	return status, nil
}

// AttestRelayRuntimeDatabaseRoleWithContext is the protected API startup and
// bounded-refresh gate. It performs exactly one full catalog fingerprint on a
// pinned physical connection, then completes all role/ACL checks against that
// same release without recursively re-running the exact surface.
func AttestRelayRuntimeDatabaseRoleWithContext(ctx context.Context, db *gorm.DB) (RelayRuntimeDatabaseRoleProof, error) {
	var proof RelayRuntimeDatabaseRoleProof
	if ctx == nil || ctx.Err() != nil {
		return proof, errors.New("Relay runtime database role proof context is unavailable")
	}
	if db == nil || db.Dialector.Name() != "postgres" {
		return proof, errors.New("production Relay runtime requires PostgreSQL")
	}
	pool, err := db.DB()
	if err != nil || pool == nil {
		return proof, errors.New("Relay runtime database pool is unavailable")
	}
	proof.pool = pool
	proof.role = "relay_runtime"
	proof.edgeRole = relayDownloadEdgeDatabaseRoleName
	err = db.WithContext(ctx).Connection(func(connection *gorm.DB) error {
		pinned := relayPinnedConnectionSession(connection)
		if err := VerifyRelayDatabaseTLS(pinned); err != nil {
			return err
		}
		ownerRole, migrationRole, runtimeRole, err := relayRuntimeDatabaseRoleConfiguration()
		if err != nil {
			return err
		}
		if err := verifyRelayProtectedDatabaseSurfacePreflight(pinned, ownerRole, migrationRole, runtimeRole, true); err != nil {
			return err
		}
		schemaStatus, err := RequireRelaySchemaCurrent(pinned)
		if err != nil {
			return err
		}
		if _, err := verifyRelayRuntimeDatabaseRoleSession(pinned); err != nil {
			return err
		}
		if err := verifyRelayDatabaseRoleTopologyAfterExactSurface(pinned, ownerRole, migrationRole, runtimeRole); err != nil {
			return err
		}
		if err := verifyRelayRuntimeDatabasePrivilegeManifestOptimized(pinned, runtimeRole, schemaStatus.CurrentVersion); err != nil {
			return err
		}
		if err := verifyRelayDownloadEdgeCurrentDatabaseRoleOptimizedAfterTopology(pinned, schemaStatus.CurrentVersion, true); err != nil {
			return errors.New("Relay download edge database role is not finalized")
		}
		proof.schemaStatus = schemaStatus
		proof.verifiedAt = time.Now()
		return nil
	})
	if err != nil {
		return RelayRuntimeDatabaseRoleProof{}, err
	}
	if err := ValidateRelayRuntimeDatabaseRoleProofBinding(db, proof); err != nil {
		return RelayRuntimeDatabaseRoleProof{}, err
	}
	return proof, nil
}

func AttestRelayRuntimeDatabaseRole(db *gorm.DB) (RelayRuntimeDatabaseRoleProof, error) {
	return AttestRelayRuntimeDatabaseRoleWithContext(context.Background(), db)
}

func ValidateRelayRuntimeDatabaseRoleProofBinding(db *gorm.DB, proof RelayRuntimeDatabaseRoleProof) error {
	if db == nil || db.Dialector.Name() != "postgres" || proof.pool == nil || proof.role != "relay_runtime" ||
		proof.edgeRole != relayDownloadEdgeDatabaseRoleName {
		return errors.New("Relay runtime database role proof is unavailable")
	}
	pool, err := db.DB()
	if err != nil || pool == nil || pool != proof.pool {
		return errors.New("Relay runtime database role proof belongs to another database pool")
	}
	_, _, expectedRole, err := relayRuntimeDatabaseRoleConfiguration()
	if err != nil || expectedRole != proof.role {
		return errors.New("Relay runtime database role proof role binding is invalid")
	}
	status := proof.schemaStatus
	contract := relaySchemaContractForRuntime()
	expectedCatalog := relaySchemaExpectedCatalogForRuntime("postgres", status.CurrentVersion)
	attemptID, attemptErr := uuid.Parse(status.AttemptID)
	if !status.Current || status.Classification != RelaySchemaStatusCurrent || status.State != RelaySchemaStateClean || status.Dirty ||
		status.CurrentVersion != contract.TargetVersion || status.TargetVersion != contract.TargetVersion ||
		status.CurrentChecksum == "" || status.CurrentChecksum != contract.Checksums[status.CurrentVersion] ||
		status.ExpectedChecksum != status.CurrentChecksum || status.TargetChecksum != status.CurrentChecksum ||
		status.CatalogSHA256 == "" || status.CatalogSHA256 != expectedCatalog || status.ExpectedCatalogSHA256 != expectedCatalog ||
		status.PendingVersion != status.CurrentVersion || status.ErrorCode != "" ||
		status.MinVersion != contract.MinVersion || status.MaxVersion != contract.MaxVersion || !status.Compatible ||
		attemptErr != nil || attemptID.String() != strings.ToLower(status.AttemptID) ||
		!relaySchemaProvenanceValid(status.SourceRevision, status.SnapshotSHA256) || proof.verifiedAt.IsZero() {
		return errors.New("Relay runtime database role proof is not a current release")
	}
	if RelayRuntimeDatabaseLifecycleFencingEnabled() && !RelayRuntimeDatabaseLifecycleHealthy() {
		return errors.New("Relay runtime database lifecycle is unavailable")
	}
	return nil
}

// VerifyRelayRuntimeDatabaseRoleProof is the protected API's light live gate.
// It verifies the complete release state/ledger and both serving ACL surfaces,
// while the bounded full proof owns system-semantic and catalog fingerprint
// freshness. The shared role topology is evaluated exactly once per probe.
func VerifyRelayRuntimeDatabaseRoleProof(db *gorm.DB, proof RelayRuntimeDatabaseRoleProof) (RelayRuntimeDatabaseRoleStatus, error) {
	status := RelayRuntimeDatabaseRoleStatus{Required: true, State: "unavailable"}
	if err := ValidateRelayRuntimeDatabaseRoleProofBinding(db, proof); err != nil {
		return status, err
	}
	err := db.Connection(func(connection *gorm.DB) error {
		pinned := relayPinnedConnectionSession(connection)
		if err := VerifyRelayDatabaseTLS(pinned); err != nil {
			return err
		}
		if err := verifyRelayDownloadEdgeSchemaReleaseProof(pinned, proof.schemaStatus); err != nil {
			return err
		}
		ownerRole, migrationRole, runtimeRole, err := relayRuntimeDatabaseRoleConfiguration()
		if err != nil {
			return err
		}
		status, err = verifyRelayRuntimeDatabaseRoleSession(pinned)
		if err != nil {
			return err
		}
		if err := verifyRelayDatabaseRoleLiveTopology(pinned, ownerRole, migrationRole, runtimeRole); err != nil {
			return err
		}
		if err := verifyRelayRuntimeDatabasePrivilegeManifestOptimized(pinned, runtimeRole, proof.schemaStatus.CurrentVersion); err != nil {
			return err
		}
		if err := verifyRelayDownloadEdgeCurrentDatabaseRoleOptimizedAfterTopology(pinned, proof.schemaStatus.CurrentVersion, true); err != nil {
			return errors.New("Relay download edge database role is not finalized")
		}
		return nil
	})
	if err != nil {
		status.State = "unavailable"
		return status, err
	}
	return status, nil
}
