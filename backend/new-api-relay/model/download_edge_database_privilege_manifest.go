package model

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"fmt"
	"sort"
	"strings"

	"golang.org/x/crypto/pbkdf2"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

const relayDownloadEdgeDatabasePrivilegeManifestV1SHA256 = "sha256:00dc794c68c74a51b351b579841214fbc7cbdb23be3ae00eae4ba09962518ad5"
const relayDownloadEdgeDatabasePrivilegeManifestV2SHA256 = "sha256:00dc794c68c74a51b351b579841214fbc7cbdb23be3ae00eae4ba09962518ad5"
const relayDownloadEdgeDatabasePrivilegeManifestV3SHA256 = "sha256:00dc794c68c74a51b351b579841214fbc7cbdb23be3ae00eae4ba09962518ad5"

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

func relayDownloadEdgeDatabasePrivilegeManifestForVersion(version int64) (relayDownloadEdgePrivilegeManifest, error) {
	var updateColumns map[string][]string
	switch version {
	case 1:
		updateColumns = relayDownloadEdgeV1UpdateColumns
	case 2:
		updateColumns = relayDownloadEdgeV2UpdateColumns
	case 3:
		updateColumns = relayDownloadEdgeV3UpdateColumns
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
	if db == nil || db.Dialector.Name() != "postgres" {
		return errors.New("Relay download edge requires PostgreSQL")
	}
	if err := VerifyRelayDatabaseTLS(db); err != nil {
		return err
	}
	// Readiness trusts no role-local query until the shared protected-role
	// catalog surface has passed the same exact gate as API readiness.
	if err := verifyRelayProtectedDatabaseExactSurfaceFromEnvironment(db); err != nil {
		return err
	}
	schemaStatus, err := RequireRelaySchemaCurrent(db)
	if err != nil || schemaStatus.CurrentVersion != version {
		return errors.New("Relay download edge requires the exact current schema catalog")
	}
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
	if err := verifyRelayDatabaseRoleTopology(db, ownerRole, migrationRole, runtimeRole); err != nil {
		return err
	}
	return verifyRelayDownloadEdgeDatabasePrivilegeManifest(db, version)
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
