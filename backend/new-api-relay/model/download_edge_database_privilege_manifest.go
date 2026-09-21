package model

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"database/sql"
	"encoding/base64"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/google/uuid"
	"golang.org/x/crypto/pbkdf2"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

const relayDownloadEdgeDatabasePrivilegeManifestV1SHA256 = "sha256:00dc794c68c74a51b351b579841214fbc7cbdb23be3ae00eae4ba09962518ad5"
const relayDownloadEdgeDatabasePrivilegeManifestV2SHA256 = "sha256:00dc794c68c74a51b351b579841214fbc7cbdb23be3ae00eae4ba09962518ad5"
const relayDownloadEdgeDatabasePrivilegeManifestV3SHA256 = "sha256:00dc794c68c74a51b351b579841214fbc7cbdb23be3ae00eae4ba09962518ad5"
const relayDownloadEdgeDatabasePrivilegeManifestV4SHA256 = "sha256:00dc794c68c74a51b351b579841214fbc7cbdb23be3ae00eae4ba09962518ad5"
const relayDownloadEdgeDatabasePrivilegeManifestV5SHA256 = "sha256:00dc794c68c74a51b351b579841214fbc7cbdb23be3ae00eae4ba09962518ad5"
const relayDownloadEdgeDatabasePrivilegeManifestV6SHA256 = "sha256:00dc794c68c74a51b351b579841214fbc7cbdb23be3ae00eae4ba09962518ad5"
const relayDownloadEdgeDatabasePrivilegeManifestV7SHA256 = "sha256:00dc794c68c74a51b351b579841214fbc7cbdb23be3ae00eae4ba09962518ad5"
const relayDownloadEdgeDatabasePrivilegeManifestV8SHA256 = "sha256:5498b7b4d18787c5d0973fd6902c56e04a5af9247b2bb448e47ab48c3eea7841"

type relayDownloadEdgePrivilegeManifest struct {
	Tables        map[string]relayTablePrivilegeSet
	UpdateColumns map[string]map[string]bool
}

var relayDownloadEdgeV1UpdateColumns = map[string][]string{
	"platform_download_edge_tickets": {
		"state", "claim_token", "claimed_at", "claim_expires_at", "gateway_request_id",
		"failure_code", "completed_at", "updated_at",
	},
	"platform_relay_external_deliveries": {
		"state", "attempts", "available_at", "claim_token", "claimed_at", "claim_expires_at",
		"response_status", "last_error", "delivered_at", "dead_lettered_at", "updated_at",
	},
}

var relayDownloadEdgeV2UpdateColumns = map[string][]string{
	"platform_download_edge_tickets": {
		"state", "claim_token", "claimed_at", "claim_expires_at", "gateway_request_id",
		"failure_code", "completed_at", "updated_at",
	},
	"platform_relay_external_deliveries": {
		"state", "attempts", "available_at", "claim_token", "claimed_at", "claim_expires_at",
		"response_status", "last_error", "delivered_at", "dead_lettered_at", "updated_at",
	},
}

var relayDownloadEdgeV3UpdateColumns = map[string][]string{
	"platform_download_edge_tickets": {
		"state", "claim_token", "claimed_at", "claim_expires_at", "gateway_request_id",
		"failure_code", "completed_at", "updated_at",
	},
	"platform_relay_external_deliveries": {
		"state", "attempts", "available_at", "claim_token", "claimed_at", "claim_expires_at",
		"response_status", "last_error", "delivered_at", "dead_lettered_at", "updated_at",
	},
}

var relayDownloadEdgeV4UpdateColumns = map[string][]string{
	"platform_download_edge_tickets": {
		"state", "claim_token", "claimed_at", "claim_expires_at", "gateway_request_id",
		"failure_code", "completed_at", "updated_at",
	},
	"platform_relay_external_deliveries": {
		"state", "attempts", "available_at", "claim_token", "claimed_at", "claim_expires_at",
		"response_status", "last_error", "delivered_at", "dead_lettered_at", "updated_at",
	},
}

var relayDownloadEdgeV5UpdateColumns = map[string][]string{
	"platform_download_edge_tickets": {
		"state", "claim_token", "claimed_at", "claim_expires_at", "gateway_request_id",
		"failure_code", "completed_at", "updated_at",
	},
	"platform_relay_external_deliveries": {
		"state", "attempts", "available_at", "claim_token", "claimed_at", "claim_expires_at",
		"response_status", "last_error", "delivered_at", "dead_lettered_at", "updated_at",
	},
}

var relayDownloadEdgeV6UpdateColumns = map[string][]string{
	"platform_download_edge_tickets": {
		"state", "claim_token", "claimed_at", "claim_expires_at", "gateway_request_id",
		"failure_code", "completed_at", "updated_at",
	},
	"platform_relay_external_deliveries": {
		"state", "attempts", "available_at", "claim_token", "claimed_at", "claim_expires_at",
		"response_status", "last_error", "delivered_at", "dead_lettered_at", "updated_at",
	},
}

// V7 is an explicit independent release projection. Keep an owned map rather
// than aliasing the mutable v6 map: a future v7-only edit must never mutate
// the historical v6 privilege surface in memory.
var relayDownloadEdgeV7UpdateColumns = map[string][]string{
	"platform_download_edge_tickets": {
		"state", "claim_token", "claimed_at", "claim_expires_at", "gateway_request_id",
		"failure_code", "completed_at", "updated_at",
	},
	"platform_relay_external_deliveries": {
		"state", "attempts", "available_at", "claim_token", "claimed_at", "claim_expires_at",
		"response_status", "last_error", "delivered_at", "dead_lettered_at", "updated_at",
	},
}

// V8 adds no download-edge write surface. It owns an independent copy so a
// future edge change cannot mutate the historical v7 projection in memory.
var relayDownloadEdgeV8UpdateColumns = map[string][]string{
	"platform_download_edge_tickets": {
		"state", "claim_token", "claimed_at", "claim_expires_at", "gateway_request_id",
		"failure_code", "completed_at", "updated_at",
	},
	"platform_relay_external_deliveries": {
		"state", "attempts", "available_at", "claim_token", "claimed_at", "claim_expires_at",
		"response_status", "last_error", "delivered_at", "dead_lettered_at", "updated_at",
	},
}

func relayDownloadEdgeDatabasePrivilegeManifestForVersion(version int64) (relayDownloadEdgePrivilegeManifest, error) {
	var updateColumns map[string][]string
	switch version {
	case 1:
		updateColumns = relayDownloadEdgeV1UpdateColumns
	case 2:
		updateColumns = relayDownloadEdgeV2UpdateColumns
	case 3:
		updateColumns = relayDownloadEdgeV3UpdateColumns
	case 4:
		updateColumns = relayDownloadEdgeV4UpdateColumns
	case 5:
		updateColumns = relayDownloadEdgeV5UpdateColumns
	case 6:
		updateColumns = relayDownloadEdgeV6UpdateColumns
	case 7:
		updateColumns = relayDownloadEdgeV7UpdateColumns
	case 8:
		updateColumns = relayDownloadEdgeV8UpdateColumns
	default:
		return relayDownloadEdgePrivilegeManifest{}, errors.New("Relay download edge privilege manifest version is unavailable")
	}
	runtimeManifest, err := relayRuntimeDatabasePrivilegeManifestForRuntime(version)
	if err != nil {
		return relayDownloadEdgePrivilegeManifest{}, err
	}
	manifest := relayDownloadEdgePrivilegeManifest{
		Tables:        make(map[string]relayTablePrivilegeSet, len(runtimeManifest)),
		UpdateColumns: make(map[string]map[string]bool),
	}
	for table := range runtimeManifest {
		manifest.Tables[table] = relayTablePrivilegeSet{}
	}
	for _, table := range []string{
		"platform_download_edge_tickets",
		"platform_relay_external_deliveries",
		"platform_download_completion_events",
		"platform_download_completion_proofs",
	} {
		if _, exists := manifest.Tables[table]; !exists {
			return relayDownloadEdgePrivilegeManifest{}, errors.New("Relay download edge privilege manifest references an unknown table")
		}
		manifest.Tables[table] = relayTablePrivilegeSet{Select: true, Insert: true}
	}
	for _, table := range []string{"relay_schema_state", "relay_schema_migrations"} {
		if _, exists := manifest.Tables[table]; !exists {
			return relayDownloadEdgePrivilegeManifest{}, errors.New("Relay download edge privilege manifest is missing schema metadata")
		}
		manifest.Tables[table] = relayTablePrivilegeSet{Select: true}
	}
	for table, columns := range updateColumns {
		allowed := make(map[string]bool, len(columns))
		for _, column := range columns {
			if column == "" || allowed[column] {
				return relayDownloadEdgePrivilegeManifest{}, errors.New("Relay download edge privilege manifest contains an invalid update column")
			}
			allowed[column] = true
		}
		manifest.UpdateColumns[table] = allowed
	}
	return manifest, nil
}

var relayDownloadEdgeDatabasePrivilegeManifestForRuntime = relayDownloadEdgeDatabasePrivilegeManifestForVersion

func relayDownloadEdgeDatabasePrivilegeManifestCanonical(manifest relayDownloadEdgePrivilegeManifest) string {
	tables := make([]string, 0, len(manifest.Tables))
	for table := range manifest.Tables {
		tables = append(tables, table)
	}
	sort.Strings(tables)
	var canonical strings.Builder
	for _, table := range tables {
		canonical.WriteString(table)
		canonical.WriteByte('|')
		canonical.WriteString(relayTablePrivilegeFlags(manifest.Tables[table]))
		canonical.WriteByte('|')
		columns := make([]string, 0, len(manifest.UpdateColumns[table]))
		for column := range manifest.UpdateColumns[table] {
			columns = append(columns, column)
		}
		sort.Strings(columns)
		canonical.WriteString(strings.Join(columns, ","))
		canonical.WriteByte('\n')
	}
	return canonical.String()
}

func relayDownloadEdgeDatabasePrivilegeManifestSHA256(manifest relayDownloadEdgePrivilegeManifest) string {
	digest := sha256.Sum256([]byte(relayDownloadEdgeDatabasePrivilegeManifestCanonical(manifest)))
	return fmt.Sprintf("sha256:%x", digest[:])
}

func relaySchemaPrivilegeManifestRegistryForVersion(version int64) (map[string]relayTablePrivilegeSet, error) {
	runtimeManifest, err := relayRuntimeDatabasePrivilegeManifestForRuntime(version)
	if err != nil {
		return nil, err
	}
	edgeManifest, err := relayDownloadEdgeDatabasePrivilegeManifestForRuntime(version)
	if err != nil || len(edgeManifest.Tables) == 0 {
		return nil, errors.New("Relay download edge privilege manifest version is unavailable")
	}
	return runtimeManifest, nil
}

// ApplyRelayDownloadEdgeDatabasePrivilegeManifestWithDB is part of the schema
// transaction. The pre-migration provisioner creates a locked NOLOGIN role;
// this step grants its exact DML surface before the post-migration provisioner
// is allowed to attach a login password.
func ApplyRelayDownloadEdgeDatabasePrivilegeManifestWithDB(db *gorm.DB) error {
	return applyRelayDownloadEdgeDatabasePrivilegeManifestForVersion(db, RelaySchemaTargetVersion)
}

func applyRelayDownloadEdgeDatabasePrivilegeManifestForVersion(db *gorm.DB, version int64) error {
	if !RelayDatabaseRoleAttestationRequired() {
		return nil
	}
	if db == nil || db.Dialector.Name() != "postgres" {
		return errors.New("protected Relay download edge privileges require PostgreSQL")
	}
	manifest, err := relayDownloadEdgeDatabasePrivilegeManifestForRuntime(version)
	if err != nil {
		return err
	}
	role := quoteRelayDatabaseIdentifier(relayDownloadEdgeDatabaseRoleName)
	tables := make([]string, 0, len(manifest.Tables))
	for table := range manifest.Tables {
		tables = append(tables, table)
	}
	sort.Strings(tables)
	for _, table := range tables {
		quotedTable := quoteRelayDatabaseIdentifier(table)
		if err := db.Exec("REVOKE ALL PRIVILEGES ON TABLE public." + quotedTable + " FROM " + role).Error; err != nil {
			return errors.New("Relay download edge table privileges could not be reset")
		}
		privilegeNames := relayTablePrivilegeNames(manifest.Tables[table])
		if len(privilegeNames) > 0 {
			if err := db.Exec("GRANT " + strings.Join(privilegeNames, ", ") + " ON TABLE public." + quotedTable + " TO " + role).Error; err != nil {
				return errors.New("Relay download edge table privileges could not be granted")
			}
		}
		columns := manifest.UpdateColumns[table]
		if len(columns) > 0 {
			names := make([]string, 0, len(columns))
			for column := range columns {
				names = append(names, quoteRelayDatabaseIdentifier(column))
			}
			sort.Strings(names)
			if err := db.Exec("GRANT UPDATE (" + strings.Join(names, ", ") + ") ON TABLE public." + quotedTable + " TO " + role).Error; err != nil {
				return errors.New("Relay download edge column privileges could not be granted")
			}
		}
	}
	if err := db.Exec("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM " + role).Error; err != nil {
		return errors.New("Relay download edge sequence privileges could not be reset")
	}
	var sequences []relayCatalogSequence
	if err := db.Raw(relayCatalogSequencesSQL).Scan(&sequences).Error; err != nil {
		return errors.New("Relay download edge sequence catalog could not be inspected")
	}
	for _, sequence := range sequences {
		if privileges, exists := manifest.Tables[sequence.OwnedTable]; !exists || !privileges.Insert {
			continue
		}
		if err := db.Exec("GRANT USAGE ON SEQUENCE public." + quoteRelayDatabaseIdentifier(sequence.Name) + " TO " + role).Error; err != nil {
			return errors.New("Relay download edge sequence privileges could not be granted")
		}
	}
	if err := db.Exec("REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM " + role).Error; err != nil {
		return errors.New("Relay download edge function privileges could not be revoked")
	}
	return verifyRelayDownloadEdgeDatabasePrivilegeManifest(db, version)
}

func verifyRelayDownloadEdgeDatabasePrivilegeManifest(db *gorm.DB, version int64) error {
	manifest, err := relayDownloadEdgeDatabasePrivilegeManifestForRuntime(version)
	if err != nil {
		return err
	}
	var catalogTables []relayCatalogTable
	if err := db.Raw(`
SELECT c.relname AS name
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
ORDER BY c.relname`).Scan(&catalogTables).Error; err != nil {
		return errors.New("Relay download edge table catalog could not be inspected")
	}
	if len(catalogTables) != len(manifest.Tables) {
		return errors.New("Relay download edge table privilege manifest does not cover the catalog")
	}
	for _, table := range catalogTables {
		expected, exists := manifest.Tables[table.Name]
		if !exists {
			return errors.New("Relay download edge table privilege manifest does not cover the catalog")
		}
		actual, privilegeErr := relayEffectiveTablePrivileges(db, relayDownloadEdgeDatabaseRoleName, table.Name)
		if privilegeErr != nil || actual != expected {
			return errors.New("Relay download edge table privileges do not match the manifest")
		}
		if err := verifyRelayDownloadEdgeColumnPrivileges(
			db, table.Name, expected.Select, expected.Insert, manifest.UpdateColumns[table.Name],
		); err != nil {
			return err
		}
	}
	var sequences []relayCatalogSequence
	if err := db.Raw(relayCatalogSequencesSQL).Scan(&sequences).Error; err != nil {
		return errors.New("Relay download edge sequence catalog could not be inspected")
	}
	for _, sequence := range sequences {
		expectedUsage := false
		if privileges, exists := manifest.Tables[sequence.OwnedTable]; exists {
			expectedUsage = privileges.Insert
		}
		var actual struct {
			Usage  bool `gorm:"column:usage"`
			Select bool `gorm:"column:select_privilege"`
			Update bool `gorm:"column:update_privilege"`
		}
		qualified := "public." + sequence.Name
		if err := db.Raw(`SELECT
  has_sequence_privilege(?, ?, 'USAGE') AS usage,
  has_sequence_privilege(?, ?, 'SELECT') AS select_privilege,
  has_sequence_privilege(?, ?, 'UPDATE') AS update_privilege`,
			relayDownloadEdgeDatabaseRoleName, qualified,
			relayDownloadEdgeDatabaseRoleName, qualified,
			relayDownloadEdgeDatabaseRoleName, qualified).Scan(&actual).Error; err != nil ||
			actual.Usage != expectedUsage || actual.Select || actual.Update {
			return errors.New("Relay download edge sequence privileges do not match the manifest")
		}
	}
	var executableFunctionCount int64
	if err := db.Raw(`
SELECT count(*) FROM pg_proc function
JOIN pg_namespace namespace ON namespace.oid = function.pronamespace
WHERE namespace.nspname <> 'information_schema' AND namespace.nspname NOT LIKE 'pg_%'
  AND has_function_privilege(?, function.oid, 'EXECUTE')`, relayDownloadEdgeDatabaseRoleName).
		Scan(&executableFunctionCount).Error; err != nil || executableFunctionCount != 0 {
		return errors.New("Relay download edge function privileges do not match the manifest")
	}
	if err := verifyRelayApplicationColumnACLTopology(db, version, true); err != nil {
		return err
	}
	return verifyRelayApplicationObjectACLTopology(db)
}

func verifyRelayDownloadEdgeColumnPrivileges(
	db *gorm.DB,
	table string,
	selectExpected bool,
	insertExpected bool,
	updateColumns map[string]bool,
) error {
	var columns []struct {
		Name       string `gorm:"column:name"`
		Select     bool   `gorm:"column:select_privilege"`
		Insert     bool   `gorm:"column:insert_privilege"`
		Update     bool   `gorm:"column:update_privilege"`
		References bool   `gorm:"column:references_privilege"`
	}
	if err := db.Raw(`
SELECT attribute.attname AS name,
	   has_column_privilege(?, relation.oid, attribute.attnum, 'SELECT') AS select_privilege,
       has_column_privilege(?, relation.oid, attribute.attnum, 'INSERT') AS insert_privilege,
       has_column_privilege(?, relation.oid, attribute.attnum, 'UPDATE') AS update_privilege,
       has_column_privilege(?, relation.oid, attribute.attnum, 'REFERENCES') AS references_privilege
FROM pg_attribute attribute
JOIN pg_class relation ON relation.oid = attribute.attrelid
JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
WHERE namespace.nspname = 'public' AND relation.relname = ?
  AND attribute.attnum > 0 AND NOT attribute.attisdropped
ORDER BY attribute.attname`, relayDownloadEdgeDatabaseRoleName, relayDownloadEdgeDatabaseRoleName,
		relayDownloadEdgeDatabaseRoleName, relayDownloadEdgeDatabaseRoleName, table).Scan(&columns).Error; err != nil {
		return errors.New("Relay download edge column privileges could not be inspected")
	}
	for _, column := range columns {
		if column.Select != selectExpected || column.Insert != insertExpected ||
			column.Update != updateColumns[column.Name] || column.References {
			return errors.New("Relay download edge column privileges do not match the manifest")
		}
	}
	return nil
}

// verifyRelayDownloadEdgeCurrentDatabaseRole is used only by the migrator's
// already-current no-op path. It runs through the schema-owner connection, so
// it verifies the named edge role rather than asserting current_user. LOGIN is
// expected on an ordinary redeploy; NOLOGIN with the exact current manifest is
// the commit-complete state recovered before the post-provisioner runs.
func verifyRelayDownloadEdgeCurrentDatabaseRole(db *gorm.DB, version int64, expectedCanLogin bool) error {
	if db == nil || db.Dialector.Name() != "postgres" {
		return errors.New("Relay download edge requires PostgreSQL")
	}
	var role struct {
		Superuser          bool `gorm:"column:superuser"`
		BypassRLS          bool `gorm:"column:bypass_rls"`
		CreateDatabase     bool `gorm:"column:create_database"`
		CreateRole         bool `gorm:"column:create_role"`
		Replication        bool `gorm:"column:replication"`
		Inherits           bool `gorm:"column:inherits"`
		CanLogin           bool `gorm:"column:can_login"`
		CanConnect         bool `gorm:"column:can_connect"`
		CanCreateDatabase  bool `gorm:"column:can_create_database"`
		CanCreateTemporary bool `gorm:"column:can_create_temporary"`
		CanUsePublic       bool `gorm:"column:can_use_public"`
		CanCreateSchema    bool `gorm:"column:can_create_schema"`
		HasMembership      bool `gorm:"column:has_membership"`
		HasMember          bool `gorm:"column:has_member"`
		CanTruncate        bool `gorm:"column:can_truncate"`
	}
	result := db.Raw(`
SELECT role.rolsuper AS superuser, role.rolbypassrls AS bypass_rls,
       role.rolcreatedb AS create_database, role.rolcreaterole AS create_role,
       role.rolreplication AS replication, role.rolinherit AS inherits,
       role.rolcanlogin AS can_login,
       has_database_privilege(role.rolname, current_database(), 'CONNECT') AS can_connect,
       has_database_privilege(role.rolname, current_database(), 'CREATE') AS can_create_database,
       has_database_privilege(role.rolname, current_database(), 'TEMP') AS can_create_temporary,
       has_schema_privilege(role.rolname, 'public', 'USAGE') AS can_use_public,
       EXISTS (SELECT 1 FROM pg_namespace namespace
                WHERE namespace.nspname <> 'information_schema' AND namespace.nspname NOT LIKE 'pg_%'
                  AND has_schema_privilege(role.rolname, namespace.oid, 'CREATE')) AS can_create_schema,
       EXISTS (SELECT 1 FROM pg_auth_members membership WHERE membership.member = role.oid) AS has_membership,
       EXISTS (SELECT 1 FROM pg_auth_members membership WHERE membership.roleid = role.oid) AS has_member,
       EXISTS (SELECT 1 FROM pg_class relation JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname <> 'information_schema' AND namespace.nspname NOT LIKE 'pg_%'
                  AND relation.relkind IN ('r', 'p') AND has_table_privilege(role.rolname, relation.oid, 'TRUNCATE')) AS can_truncate
FROM pg_roles role WHERE role.rolname = ?`, relayDownloadEdgeDatabaseRoleName).Scan(&role)
	if result.Error != nil || result.RowsAffected != 1 || role.Superuser || role.BypassRLS || role.CreateDatabase ||
		role.CreateRole || role.Replication || role.Inherits || role.CanLogin != expectedCanLogin || !role.CanConnect || role.CanCreateDatabase ||
		role.CanCreateTemporary || !role.CanUsePublic || role.CanCreateSchema || role.HasMembership || role.HasMember || role.CanTruncate {
		return errors.New("Relay deployed download edge database role is not isolated")
	}
	owns, err := relayRoleOwnsForbiddenDatabaseObject(db, relayDownloadEdgeDatabaseRoleName)
	if err != nil || owns {
		return errors.New("Relay deployed download edge database role owns a forbidden object")
	}
	ownerRole := strings.TrimSpace(getenvRelayDatabaseRole(relaySchemaOwnerRoleEnvironment))
	migrationRole := strings.TrimSpace(getenvRelayDatabaseRole(relayMigrationDatabaseRoleEnvironment))
	runtimeRole := strings.TrimSpace(getenvRelayDatabaseRole(relayRuntimeDatabaseRoleEnvironment))
	if !databaseRoleNamePattern.MatchString(ownerRole) || !databaseRoleNamePattern.MatchString(migrationRole) ||
		!databaseRoleNamePattern.MatchString(runtimeRole) || runtimeRole != "relay_runtime" {
		return errors.New("Relay download edge database role topology configuration is invalid")
	}
	if err := verifyRelayDatabaseRoleTopology(db, ownerRole, migrationRole, runtimeRole); err != nil {
		return err
	}
	return verifyRelayDownloadEdgeDatabasePrivilegeManifest(db, version)
}

// VerifyRelayDownloadEdgeDatabaseRole is shared by startup and readiness.
// It verifies role attributes/topology before checking the versioned ACL
// manifest; a post-start GRANT therefore fails the next readiness probe.
func VerifyRelayDownloadEdgeDatabaseRole(db *gorm.DB, version int64) error {
	proof, err := AttestRelayDownloadEdgeDatabaseRole(db)
	if err != nil {
		return err
	}
	if proof.schemaStatus.CurrentVersion != version {
		return errors.New("Relay download edge requires the exact current schema catalog")
	}
	return nil
}

// RelayDownloadEdgeDatabaseRoleProof binds the expensive startup catalog
// attestation to one live database pool and one immutable Relay release. The
// unexported fields make the proof process-local and non-serializable; it cannot
// be replayed against another database or manufactured by configuration.
//
// Readiness never treats this proof as a cached success boolean. Every probe
// still revalidates TLS, lifecycle health, the complete schema state/ledger
// release tuple, the login role/topology and the exact versioned ACL manifest.
// Only the full catalog fingerprint is reused from startup, while the shared
// lifecycle lock prevents an official schema transition beneath the process.
type RelayDownloadEdgeDatabaseRoleProof struct {
	pool         *sql.DB
	schemaStatus RelaySchemaStatus
	role         string
	verifiedAt   time.Time
}

// SchemaStatus returns the immutable schema release proven at startup.
func (proof RelayDownloadEdgeDatabaseRoleProof) SchemaStatus() RelaySchemaStatus {
	return proof.schemaStatus
}

// VerifiedAt reports the attestation-owned completion instant. The monotonic
// component is intentionally preserved for same-process freshness checks.
func (proof RelayDownloadEdgeDatabaseRoleProof) VerifiedAt() time.Time {
	return proof.verifiedAt
}

// SameRelease reports whether two proofs belong to the same process pool,
// edge role and immutable Relay candidate/schema tuple. A refresh may replace
// a proof only when this comparison succeeds.
func (proof RelayDownloadEdgeDatabaseRoleProof) SameRelease(other RelayDownloadEdgeDatabaseRoleProof) bool {
	return proof.pool != nil && proof.pool == other.pool && proof.role == relayDownloadEdgeDatabaseRoleName && proof.role == other.role &&
		proof.schemaStatus == other.schemaStatus
}

// AttestRelayDownloadEdgeDatabaseRole is the protected edge startup gate. It
// computes the full schema/catalog proof exactly once on a pinned connection,
// then proves the serving role and versioned ACL against that same release.
func AttestRelayDownloadEdgeDatabaseRole(db *gorm.DB) (RelayDownloadEdgeDatabaseRoleProof, error) {
	return AttestRelayDownloadEdgeDatabaseRoleWithContext(context.Background(), db)
}

// AttestRelayDownloadEdgeDatabaseRoleWithContext is the refreshable form of
// the startup gate. The supplied deadline bounds every catalog query.
func AttestRelayDownloadEdgeDatabaseRoleWithContext(ctx context.Context, db *gorm.DB) (RelayDownloadEdgeDatabaseRoleProof, error) {
	var proof RelayDownloadEdgeDatabaseRoleProof
	if ctx == nil {
		return proof, errors.New("Relay download edge database role proof context is unavailable")
	}
	if db == nil || db.Dialector.Name() != "postgres" {
		return proof, errors.New("Relay download edge requires PostgreSQL")
	}
	pool, err := db.DB()
	if err != nil || pool == nil {
		return proof, errors.New("Relay download edge database pool is unavailable")
	}
	proof.pool = pool
	proof.role = relayDownloadEdgeDatabaseRoleName
	err = db.WithContext(ctx).Connection(func(connection *gorm.DB) error {
		pinned := relayPinnedConnectionSession(connection)
		if err := VerifyRelayDatabaseTLS(pinned); err != nil {
			return err
		}
		if err := verifyRelayProtectedDatabaseExactSurfaceFromEnvironment(pinned); err != nil {
			return err
		}
		schemaStatus, err := RequireRelaySchemaCurrent(pinned)
		if err != nil {
			return errors.New("Relay download edge requires the exact current schema catalog")
		}
		// The exact protected surface above is intentionally computed once. The
		// follow-up retains live topology plus explicit cluster/parameter checks,
		// without recursively repeating the system/catalog surface.
		if err := verifyRelayDownloadEdgeDatabaseRoleForVersionAfterExactSurface(pinned, schemaStatus.CurrentVersion); err != nil {
			return err
		}
		proof.schemaStatus = schemaStatus
		proof.verifiedAt = time.Now()
		return nil
	})
	if err != nil {
		return RelayDownloadEdgeDatabaseRoleProof{}, err
	}
	if err := validateRelayDownloadEdgeDatabaseRoleProofBinding(db, proof); err != nil {
		return RelayDownloadEdgeDatabaseRoleProof{}, err
	}
	return proof, nil
}

// ValidateRelayDownloadEdgeDatabaseRoleProofBinding is a query-free startup
// check used before opening the protected HTTP listener.
func ValidateRelayDownloadEdgeDatabaseRoleProofBinding(db *gorm.DB, proof RelayDownloadEdgeDatabaseRoleProof) error {
	return validateRelayDownloadEdgeDatabaseRoleProofBinding(db, proof)
}

func validateRelayDownloadEdgeDatabaseRoleProofBinding(db *gorm.DB, proof RelayDownloadEdgeDatabaseRoleProof) error {
	if db == nil || db.Dialector.Name() != "postgres" || proof.pool == nil || proof.role != relayDownloadEdgeDatabaseRoleName {
		return errors.New("Relay download edge database role proof is unavailable")
	}
	pool, err := db.DB()
	if err != nil || pool == nil || pool != proof.pool {
		return errors.New("Relay download edge database role proof belongs to another database pool")
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
		status.MinVersion != contract.MinVersion || status.MaxVersion != contract.MaxVersion ||
		!status.Compatible || attemptErr != nil || attemptID.String() != strings.ToLower(status.AttemptID) ||
		!relaySchemaProvenanceValid(status.SourceRevision, status.SnapshotSHA256) || proof.verifiedAt.IsZero() {
		return errors.New("Relay download edge database role proof is not a current release")
	}
	if RelayRuntimeDatabaseLifecycleFencingEnabled() && !RelayRuntimeDatabaseLifecycleHealthy() {
		return errors.New("Relay download edge database lifecycle is unavailable")
	}
	return nil
}

// VerifyRelayDownloadEdgeDatabaseRoleProof is the bounded live readiness gate.
// It cannot outlive its database pool or release tuple, and any query, TLS,
// lifecycle, role or ACL failure closes readiness immediately.
func VerifyRelayDownloadEdgeDatabaseRoleProof(db *gorm.DB, proof RelayDownloadEdgeDatabaseRoleProof) error {
	if err := validateRelayDownloadEdgeDatabaseRoleProofBinding(db, proof); err != nil {
		return err
	}
	return db.Connection(func(connection *gorm.DB) error {
		pinned := relayPinnedConnectionSession(connection)
		if err := VerifyRelayDatabaseTLS(pinned); err != nil {
			return err
		}
		// The cluster-wide protected surface and full catalog fingerprint are
		// deliberately confined to startup and bounded background refresh. The
		// probe path below still checks the live release tuple, role topology and
		// complete versioned ACL surface without turning a short health request
		// into an unbounded catalog scan.
		if err := verifyRelayDownloadEdgeSchemaReleaseProof(pinned, proof.schemaStatus); err != nil {
			return err
		}
		return verifyRelayDownloadEdgeDatabaseRoleForVersionLive(pinned, proof.schemaStatus.CurrentVersion)
	})
}

func verifyRelayDownloadEdgeDatabaseRoleForVersion(db *gorm.DB, version int64) error {
	return verifyRelayDownloadEdgeDatabaseRoleForVersionWithTopology(db, version, verifyRelayDatabaseRoleTopology)
}

func verifyRelayDownloadEdgeDatabaseRoleForVersionLive(db *gorm.DB, version int64) error {
	return verifyRelayDownloadEdgeDatabaseRoleForVersionWithTopology(db, version, verifyRelayDatabaseRoleLiveTopology)
}

func verifyRelayDownloadEdgeDatabaseRoleForVersionAfterExactSurface(db *gorm.DB, version int64) error {
	return verifyRelayDownloadEdgeDatabaseRoleForVersionWithTopology(db, version, verifyRelayDatabaseRoleTopologyAfterExactSurface)
}

func verifyRelayDownloadEdgeDatabaseRoleForVersionWithTopology(
	db *gorm.DB,
	version int64,
	verifyTopology func(*gorm.DB, string, string, string) error,
) error {
	var role struct {
		SessionUser        string `gorm:"column:session_user"`
		CurrentUser        string `gorm:"column:current_user"`
		Superuser          bool   `gorm:"column:superuser"`
		BypassRLS          bool   `gorm:"column:bypass_rls"`
		CreateDatabase     bool   `gorm:"column:create_database"`
		CreateRole         bool   `gorm:"column:create_role"`
		Replication        bool   `gorm:"column:replication"`
		Inherits           bool   `gorm:"column:inherits"`
		CanLogin           bool   `gorm:"column:can_login"`
		SafeSearchPath     bool   `gorm:"column:safe_search_path"`
		CanConnect         bool   `gorm:"column:can_connect"`
		CanCreateDatabase  bool   `gorm:"column:can_create_database"`
		CanCreateTemporary bool   `gorm:"column:can_create_temporary"`
		CanUsePublic       bool   `gorm:"column:can_use_public"`
		CanCreateSchema    bool   `gorm:"column:can_create_schema"`
		HasMembership      bool   `gorm:"column:has_membership"`
		HasMember          bool   `gorm:"column:has_member"`
		CanTruncate        bool   `gorm:"column:can_truncate"`
	}
	result := db.Raw(`
SELECT session_user, current_user,
       role.rolsuper AS superuser, role.rolbypassrls AS bypass_rls,
       role.rolcreatedb AS create_database, role.rolcreaterole AS create_role,
       role.rolreplication AS replication, role.rolinherit AS inherits,
       role.rolcanlogin AS can_login,
       current_setting('search_path') = 'public' AND current_schema() = 'public'
         AND current_schemas(true) = ARRAY['pg_catalog', 'public']::name[] AS safe_search_path,
       has_database_privilege(current_user, current_database(), 'CONNECT') AS can_connect,
       has_database_privilege(current_user, current_database(), 'CREATE') AS can_create_database,
       has_database_privilege(current_user, current_database(), 'TEMP') AS can_create_temporary,
       has_schema_privilege(current_user, 'public', 'USAGE') AS can_use_public,
       EXISTS (SELECT 1 FROM pg_namespace namespace
                WHERE namespace.nspname <> 'information_schema' AND namespace.nspname NOT LIKE 'pg_%'
                  AND has_schema_privilege(current_user, namespace.oid, 'CREATE')) AS can_create_schema,
       EXISTS (SELECT 1 FROM pg_auth_members membership WHERE membership.member = role.oid) AS has_membership,
	   EXISTS (SELECT 1 FROM pg_auth_members membership WHERE membership.roleid = role.oid) AS has_member,
       EXISTS (SELECT 1 FROM pg_class relation JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname <> 'information_schema' AND namespace.nspname NOT LIKE 'pg_%'
                  AND relation.relkind IN ('r', 'p') AND has_table_privilege(current_user, relation.oid, 'TRUNCATE')) AS can_truncate
FROM pg_roles role WHERE role.rolname = current_user`).Scan(&role)
	if result.Error != nil || result.RowsAffected != 1 ||
		role.SessionUser != relayDownloadEdgeDatabaseRoleName || role.CurrentUser != relayDownloadEdgeDatabaseRoleName ||
		role.Superuser || role.BypassRLS || role.CreateDatabase || role.CreateRole || role.Replication || role.Inherits || !role.CanLogin ||
		!role.SafeSearchPath || !role.CanConnect || role.CanCreateDatabase || role.CanCreateTemporary || !role.CanUsePublic ||
		role.CanCreateSchema || role.HasMembership || role.HasMember || role.CanTruncate {
		return errors.New("Relay download edge database role is not isolated")
	}
	owns, err := relayRoleOwnsForbiddenDatabaseObject(db, relayDownloadEdgeDatabaseRoleName)
	if err != nil || owns {
		return errors.New("Relay download edge database role owns a forbidden object")
	}
	ownerRole := strings.TrimSpace(getenvRelayDatabaseRole(relaySchemaOwnerRoleEnvironment))
	migrationRole := strings.TrimSpace(getenvRelayDatabaseRole(relayMigrationDatabaseRoleEnvironment))
	runtimeRole := strings.TrimSpace(getenvRelayDatabaseRole(relayRuntimeDatabaseRoleEnvironment))
	if !databaseRoleNamePattern.MatchString(ownerRole) || !databaseRoleNamePattern.MatchString(migrationRole) ||
		!databaseRoleNamePattern.MatchString(runtimeRole) || runtimeRole != "relay_runtime" {
		return errors.New("Relay download edge database role topology configuration is invalid")
	}
	if err := verifyTopology(db, ownerRole, migrationRole, runtimeRole); err != nil {
		return err
	}
	return verifyRelayDownloadEdgeDatabasePrivilegeManifestOptimized(db, version)
}

func verifyRelayDownloadEdgeSchemaReleaseProof(db *gorm.DB, expected RelaySchemaStatus) error {
	var state RelaySchemaState
	if err := db.Where("id = ?", relaySchemaStateSingletonID).First(&state).Error; err != nil {
		return errors.New("Relay download edge schema release state is unavailable")
	}
	if state.BaselineVersion != expected.BaselineVersion || state.FreshBootstrap != expected.FreshBootstrap ||
		state.CurrentVersion != expected.CurrentVersion || state.TargetVersion != expected.CurrentVersion ||
		state.State != RelaySchemaStateClean || state.Dirty || state.AttemptID != expected.AttemptID ||
		state.CurrentChecksum != expected.CurrentChecksum || state.TargetChecksum != expected.CurrentChecksum ||
		state.CurrentCatalogSHA256 != expected.CatalogSHA256 || state.TargetCatalogSHA256 != expected.CatalogSHA256 ||
		state.SourceRevision != expected.SourceRevision || state.SnapshotSHA256 != expected.SnapshotSHA256 || state.ErrorCode != "" {
		return errors.New("Relay download edge schema release proof changed after startup")
	}
	var ledger []RelaySchemaMigration
	if err := db.Order("version ASC").Find(&ledger).Error; err != nil {
		return errors.New("Relay download edge schema release ledger is unavailable")
	}
	expectedRows := expected.CurrentVersion - expected.BaselineVersion + 1
	if expectedRows <= 0 || int64(len(ledger)) != expectedRows {
		return errors.New("Relay download edge schema release ledger changed after startup")
	}
	definitions := relaySchemaDefinitionsForRuntime()
	definitionsByVersion := make(map[int64]relaySchemaMigrationDefinition, len(definitions))
	for _, definition := range definitions {
		definitionsByVersion[definition.Version] = definition
	}
	for index, row := range ledger {
		version := expected.BaselineVersion + int64(index)
		definition, known := definitionsByVersion[version]
		if !known || row.Version != version || row.Name != definition.Name || row.Phase != definition.Phase ||
			row.Checksum != definition.Checksum || row.CatalogSHA256 != relaySchemaExpectedCatalogForRuntime("postgres", version) ||
			!relaySchemaProvenanceValid(row.SourceRevision, row.SnapshotSHA256) {
			return errors.New("Relay download edge schema release ledger changed after startup")
		}
	}
	latest := ledger[len(ledger)-1]
	if latest.Version != expected.CurrentVersion || latest.Checksum != expected.CurrentChecksum ||
		latest.CatalogSHA256 != expected.CatalogSHA256 || latest.SourceRevision != expected.SourceRevision ||
		latest.SnapshotSHA256 != expected.SnapshotSHA256 {
		return errors.New("Relay download edge schema release tuple changed after startup")
	}
	return nil
}

// FinalizeRelayDownloadEdgeDatabaseRole is the post-migration half of the
// edge role state machine. It runs through the isolated role-admin DSN while
// the caller holds the exclusive Relay lifecycle lock. Exact versioned DML is
// proven both before and after LOGIN attachment in one transaction, so a
// partial/extra ACL can never be made externally usable.
func FinalizeRelayDownloadEdgeDatabaseRole(db *gorm.DB) error {
	if !RelayDatabaseRoleAttestationRequired() {
		return errors.New("Relay download edge login finalization requires database role attestation")
	}
	if db == nil || db.Dialector.Name() != "postgres" {
		return errors.New("Relay download edge login finalization requires PostgreSQL")
	}
	if err := VerifyRelayDatabaseTLS(db); err != nil {
		return err
	}
	return db.Transaction(func(tx *gorm.DB) error {
		quiet := tx.Session(&gorm.Session{Logger: logger.Default.LogMode(logger.Silent)})
		if err := acquireRelayLifecycleTransactionLock(quiet); err != nil {
			return err
		}
		if err := quiet.Exec(`SET LOCAL search_path = public`).Error; err != nil {
			return errors.New("Relay download edge role finalization search path could not be fixed")
		}
		status, err := RequireRelaySchemaCurrent(quiet)
		if err != nil {
			return errors.New("Relay download edge login finalization requires the current schema")
		}
		migrationRole := strings.TrimSpace(getenvRelayDatabaseRole(relayMigrationDatabaseRoleEnvironment))
		runtimeRole := strings.TrimSpace(getenvRelayDatabaseRole(relayRuntimeDatabaseRoleEnvironment))
		if err := verifyRelayProtectedLoginRoleSCRAMCredentials(
			quiet, migrationRole, runtimeRole, relayDownloadEdgeDatabaseRoleName,
		); err != nil {
			return errors.New("Relay download edge login finalization requires exact protected-role credentials")
		}
		if err := verifyRelayDownloadEdgeCurrentDatabaseRole(tx, status.CurrentVersion, false); err != nil {
			// Commit acknowledgement can be lost after a prior B -> A attach.
			// Exact A is the only additional retry state; zero/partial/extra ACLs
			// fail both checks and can never be made LOGIN.
			if retryErr := verifyRelayDownloadEdgeCurrentDatabaseRole(tx, status.CurrentVersion, true); retryErr != nil {
				return errors.New("Relay download edge pre-login manifest is not exact")
			}
		}
		if err := quiet.Exec(`ALTER ROLE relay_download_edge LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 64`).Error; err != nil {
			return errors.New("Relay download edge login could not be attached")
		}
		if err := quiet.Exec(`ALTER ROLE relay_download_edge SET search_path = public`).Error; err != nil {
			return errors.New("Relay download edge search path could not be fixed")
		}
		if err := quiet.Exec(`ALTER ROLE relay_download_edge SET row_security = on`).Error; err != nil {
			return errors.New("Relay download edge row security could not be fixed")
		}
		if err := verifyRelayDownloadEdgeCurrentDatabaseRole(quiet, status.CurrentVersion, true); err != nil {
			return errors.New("Relay download edge post-login manifest is not exact")
		}
		if err := verifyRelayProtectedLoginRoleSCRAMCredentials(
			quiet, migrationRole, runtimeRole, relayDownloadEdgeDatabaseRoleName,
		); err != nil {
			return errors.New("Relay download edge post-login credentials are not exact")
		}
		return nil
	})
}

// GenerateRelaySCRAMSHA256Verifier performs PostgreSQL's client-side password
// derivation. Only the verifier is ever sent in CREATE/ALTER ROLE SQL, so
// statement and error logs cannot capture the raw password.
func GenerateRelaySCRAMSHA256Verifier(password []byte) (string, error) {
	if err := ValidateRelayDatabasePassword(password); err != nil {
		return "", err
	}
	salt := make([]byte, 16)
	if _, err := rand.Read(salt); err != nil {
		return "", errors.New("Relay database password verifier could not be generated")
	}
	return generateRelaySCRAMSHA256VerifierWithSalt(password, salt)
}

// ValidateRelayDatabasePassword fixes the role-password interoperability
// contract to high-entropy base64url. Restricting the alphabet avoids the
// PostgreSQL/libpq SASLprep branch while preserving secret-manager output.
func ValidateRelayDatabasePassword(password []byte) error {
	if len(password) < 32 || len(password) > 128 || protectedRelaySecretLooksWeak(password) {
		return errors.New("Relay database password must contain 32 to 128 base64url characters")
	}
	for _, character := range password {
		if (character >= 'A' && character <= 'Z') || (character >= 'a' && character <= 'z') ||
			(character >= '0' && character <= '9') || character == '_' || character == '-' {
			continue
		}
		return errors.New("Relay database password must contain only base64url characters")
	}
	return nil
}

func ValidateDistinctRelayDatabasePasswords(passwords ...[]byte) error {
	if len(passwords) < 2 {
		return errors.New("Relay database password set is incomplete")
	}
	for _, password := range passwords {
		if err := ValidateRelayDatabasePassword(password); err != nil {
			return err
		}
	}
	for left := 0; left < len(passwords); left++ {
		for right := left + 1; right < len(passwords); right++ {
			if subtle.ConstantTimeCompare(passwords[left], passwords[right]) == 1 {
				return errors.New("Relay database role passwords must be distinct")
			}
		}
	}
	return nil
}

func generateRelaySCRAMSHA256VerifierWithSalt(password, salt []byte) (string, error) {
	const iterations = 4096
	if err := ValidateRelayDatabasePassword(password); err != nil {
		return "", err
	}
	if len(salt) != 16 {
		return "", errors.New("Relay database password verifier salt is invalid")
	}
	saltedPassword := pbkdf2.Key(password, salt, iterations, sha256.Size, sha256.New)
	defer clear(saltedPassword)
	clientMAC := hmac.New(sha256.New, saltedPassword)
	_, _ = clientMAC.Write([]byte("Client Key"))
	clientKey := clientMAC.Sum(nil)
	defer clear(clientKey)
	storedKey := sha256.Sum256(clientKey)
	serverMAC := hmac.New(sha256.New, saltedPassword)
	_, _ = serverMAC.Write([]byte("Server Key"))
	serverKey := serverMAC.Sum(nil)
	defer clear(serverKey)
	return fmt.Sprintf(
		"SCRAM-SHA-256$%d:%s$%s:%s",
		iterations,
		base64.StdEncoding.EncodeToString(salt),
		base64.StdEncoding.EncodeToString(storedKey[:]),
		base64.StdEncoding.EncodeToString(serverKey),
	), nil
}
