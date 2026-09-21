package model

import (
	"errors"
	"strings"

	"gorm.io/gorm"
)

// The readiness verifiers below deliberately live outside the frozen schema
// migration source graph. They preserve the historical verifier byte-for-byte
// while reducing live readiness catalog round trips.
type relayEffectivePrivilegeSnapshotRow struct {
	Kind       string `gorm:"column:kind"`
	Table      string `gorm:"column:table_name"`
	Column     string `gorm:"column:column_name"`
	Select     bool   `gorm:"column:select_privilege"`
	Insert     bool   `gorm:"column:insert_privilege"`
	Update     bool   `gorm:"column:update_privilege"`
	Delete     bool   `gorm:"column:delete_privilege"`
	Truncate   bool   `gorm:"column:truncate_privilege"`
	References bool   `gorm:"column:references_privilege"`
	Trigger    bool   `gorm:"column:trigger_privilege"`
}

type relayEffectivePrivilegeSnapshot struct {
	Tables  map[string]relayEffectivePrivilegeSnapshotRow
	Columns map[string]map[string]relayEffectivePrivilegeSnapshotRow
}

type relayEffectiveSequencePrivilege struct {
	Name        string `gorm:"column:name"`
	OwnedTable  string `gorm:"column:owned_table"`
	OwnedColumn string `gorm:"column:owned_column"`
	Usage       bool   `gorm:"column:usage_privilege"`
	Select      bool   `gorm:"column:select_privilege"`
	Update      bool   `gorm:"column:update_privilege"`
}

func relayEffectiveApplicationPrivilegeSnapshot(db *gorm.DB, role string) (relayEffectivePrivilegeSnapshot, error) {
	var rows []relayEffectivePrivilegeSnapshotRow
	if err := db.Raw(`
WITH target_role AS (
  SELECT oid FROM pg_roles WHERE rolname = ?
), application_tables AS (
  SELECT relation.oid, relation.relname
    FROM pg_class relation
    JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
   WHERE namespace.nspname = 'public' AND relation.relkind IN ('r', 'p')
)
SELECT 'table' AS kind, relation.relname AS table_name, '' AS column_name,
       has_table_privilege(target_role.oid, relation.oid, 'SELECT') AS select_privilege,
       has_table_privilege(target_role.oid, relation.oid, 'INSERT') AS insert_privilege,
       has_table_privilege(target_role.oid, relation.oid, 'UPDATE') AS update_privilege,
       has_table_privilege(target_role.oid, relation.oid, 'DELETE') AS delete_privilege,
       has_table_privilege(target_role.oid, relation.oid, 'TRUNCATE') AS truncate_privilege,
       has_table_privilege(target_role.oid, relation.oid, 'REFERENCES') AS references_privilege,
       has_table_privilege(target_role.oid, relation.oid, 'TRIGGER') AS trigger_privilege
  FROM application_tables relation CROSS JOIN target_role
UNION ALL
SELECT 'column' AS kind, relation.relname AS table_name, attribute.attname AS column_name,
       has_column_privilege(target_role.oid, relation.oid, attribute.attnum, 'SELECT') AS select_privilege,
       has_column_privilege(target_role.oid, relation.oid, attribute.attnum, 'INSERT') AS insert_privilege,
       has_column_privilege(target_role.oid, relation.oid, attribute.attnum, 'UPDATE') AS update_privilege,
       false AS delete_privilege, false AS truncate_privilege,
       has_column_privilege(target_role.oid, relation.oid, attribute.attnum, 'REFERENCES') AS references_privilege,
       false AS trigger_privilege
  FROM application_tables relation
  JOIN pg_attribute attribute ON attribute.attrelid = relation.oid
   AND attribute.attnum > 0 AND NOT attribute.attisdropped
  CROSS JOIN target_role
 ORDER BY kind, table_name, column_name`, role).Scan(&rows).Error; err != nil {
		return relayEffectivePrivilegeSnapshot{}, err
	}
	return relayEffectiveApplicationPrivilegeSnapshotFromRows(rows)
}

func relayEffectiveApplicationPrivilegeSnapshotFromRows(rows []relayEffectivePrivilegeSnapshotRow) (relayEffectivePrivilegeSnapshot, error) {
	snapshot := relayEffectivePrivilegeSnapshot{
		Tables:  make(map[string]relayEffectivePrivilegeSnapshotRow),
		Columns: make(map[string]map[string]relayEffectivePrivilegeSnapshotRow),
	}
	for _, row := range rows {
		switch row.Kind {
		case "table":
			if row.Table == "" || row.Column != "" {
				return relayEffectivePrivilegeSnapshot{}, errors.New("Relay effective table privilege snapshot is malformed")
			}
			if _, exists := snapshot.Tables[row.Table]; exists {
				return relayEffectivePrivilegeSnapshot{}, errors.New("Relay effective table privilege snapshot contains a duplicate")
			}
			snapshot.Tables[row.Table] = row
		case "column":
			if row.Table == "" || row.Column == "" {
				return relayEffectivePrivilegeSnapshot{}, errors.New("Relay effective column privilege snapshot is malformed")
			}
			columns := snapshot.Columns[row.Table]
			if columns == nil {
				columns = make(map[string]relayEffectivePrivilegeSnapshotRow)
				snapshot.Columns[row.Table] = columns
			}
			if _, exists := columns[row.Column]; exists {
				return relayEffectivePrivilegeSnapshot{}, errors.New("Relay effective column privilege snapshot contains a duplicate")
			}
			columns[row.Column] = row
		default:
			return relayEffectivePrivilegeSnapshot{}, errors.New("Relay effective privilege snapshot contains an unknown object kind")
		}
	}
	return snapshot, nil
}

func relayEffectiveSequencePrivileges(db *gorm.DB, role string) ([]relayEffectiveSequencePrivilege, error) {
	var sequences []relayEffectiveSequencePrivilege
	if err := db.Raw(`
WITH target_role AS (
  SELECT oid FROM pg_roles WHERE rolname = ?
)
SELECT sequence.relname AS name,
       COALESCE(table_rel.relname, '') AS owned_table,
       COALESCE(table_column.attname, '') AS owned_column,
       has_sequence_privilege(target_role.oid, sequence.oid, 'USAGE') AS usage_privilege,
       has_sequence_privilege(target_role.oid, sequence.oid, 'SELECT') AS select_privilege,
       has_sequence_privilege(target_role.oid, sequence.oid, 'UPDATE') AS update_privilege
FROM pg_class sequence
JOIN pg_namespace namespace ON namespace.oid = sequence.relnamespace
LEFT JOIN pg_depend dependency ON dependency.classid = 'pg_class'::regclass
 AND dependency.objid = sequence.oid AND dependency.refclassid = 'pg_class'::regclass
 AND dependency.refobjsubid > 0 AND dependency.deptype IN ('a', 'i')
LEFT JOIN pg_class table_rel ON table_rel.oid = dependency.refobjid
LEFT JOIN pg_attribute table_column ON table_column.attrelid = dependency.refobjid
 AND table_column.attnum = dependency.refobjsubid AND NOT table_column.attisdropped
CROSS JOIN target_role
WHERE namespace.nspname = 'public' AND sequence.relkind = 'S'
ORDER BY sequence.relname`, role).Scan(&sequences).Error; err != nil {
		return nil, err
	}
	seen := make(map[string]struct{}, len(sequences))
	for _, sequence := range sequences {
		if sequence.Name == "" {
			return nil, errors.New("Relay effective sequence privilege snapshot is malformed")
		}
		if _, exists := seen[sequence.Name]; exists {
			return nil, errors.New("Relay effective sequence privilege snapshot contains a duplicate")
		}
		seen[sequence.Name] = struct{}{}
	}
	return sequences, nil
}

func verifyRelayRuntimeDatabasePrivilegeManifestOptimized(db *gorm.DB, role string, version int64) error {
	manifest, err := relayRuntimeDatabasePrivilegeManifestForRuntime(version)
	if err != nil {
		return err
	}
	snapshot, err := relayEffectiveApplicationPrivilegeSnapshot(db, role)
	if err != nil {
		return errors.New("Relay runtime table catalog could not be inspected")
	}
	if len(snapshot.Tables) != len(manifest) || len(snapshot.Columns) != len(manifest) {
		return errors.New("Relay runtime table privilege manifest does not cover the catalog")
	}
	for table, actual := range snapshot.Tables {
		expected, exists := manifest[table]
		if !exists {
			return errors.New("Relay runtime table privilege manifest does not cover the catalog")
		}
		if actual.Truncate || actual.References || actual.Trigger {
			return errors.New("Relay runtime role has a forbidden table privilege")
		}
		if actual.Select != expected.Select || actual.Insert != expected.Insert ||
			actual.Update != expected.Update || actual.Delete != expected.Delete {
			return errors.New("Relay runtime table privileges do not match the manifest")
		}
		columns, exists := snapshot.Columns[table]
		if !exists || len(columns) == 0 {
			return errors.New("Relay runtime column privileges do not match the manifest")
		}
		for _, column := range columns {
			if column.Select != expected.Select || column.Insert != expected.Insert ||
				column.Update != expected.Update || column.References {
				return errors.New("Relay runtime column privileges do not match the manifest")
			}
		}
	}
	var directColumnACLs int64
	if err := db.Raw(`
SELECT count(*)
  FROM pg_attribute attribute
  JOIN pg_class relation ON relation.oid = attribute.attrelid
  JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
  CROSS JOIN LATERAL aclexplode(attribute.attacl) acl
  JOIN pg_roles role ON role.oid = acl.grantee
 WHERE namespace.nspname = 'public'
   AND attribute.attnum > 0 AND NOT attribute.attisdropped
   AND role.rolname = ?`, role).Scan(&directColumnACLs).Error; err != nil {
		return errors.New("Relay runtime direct column ACLs could not be inspected")
	}
	if directColumnACLs != 0 {
		return errors.New("Relay runtime direct column ACL surface is not empty")
	}
	sequences, err := relayEffectiveSequencePrivileges(db, role)
	if err != nil {
		return errors.New("Relay runtime sequence catalog could not be inspected")
	}
	for _, sequence := range sequences {
		expectedUsage := false
		if tablePrivileges, exists := manifest[sequence.OwnedTable]; exists {
			expectedUsage = tablePrivileges.Insert
		}
		if sequence.Usage != expectedUsage || sequence.Select || sequence.Update {
			return errors.New("Relay runtime sequence privileges do not match the manifest")
		}
	}
	var executableFunctionCount int64
	if err := db.Raw(`
SELECT count(*)
FROM pg_proc function
JOIN pg_namespace namespace ON namespace.oid = function.pronamespace
WHERE namespace.nspname <> 'information_schema'
  AND namespace.nspname NOT LIKE 'pg_%'
  AND has_function_privilege(?, function.oid, 'EXECUTE')`, role).Scan(&executableFunctionCount).Error; err != nil {
		return errors.New("Relay runtime function privileges could not be inspected")
	}
	if executableFunctionCount != 0 {
		return errors.New("Relay runtime function privileges do not match the manifest")
	}
	if err := verifyRelayApplicationColumnACLTopology(db, version, false); err != nil {
		return err
	}
	return verifyRelayApplicationObjectACLTopology(db)
}

func verifyRelayDownloadEdgeDatabasePrivilegeManifestOptimized(db *gorm.DB, version int64) error {
	manifest, err := relayDownloadEdgeDatabasePrivilegeManifestForRuntime(version)
	if err != nil {
		return err
	}
	snapshot, err := relayEffectiveApplicationPrivilegeSnapshot(db, relayDownloadEdgeDatabaseRoleName)
	if err != nil {
		return errors.New("Relay download edge table catalog could not be inspected")
	}
	if err := verifyRelayDownloadEdgeEffectivePrivilegeSnapshot(snapshot, manifest); err != nil {
		return err
	}
	sequences, err := relayEffectiveSequencePrivileges(db, relayDownloadEdgeDatabaseRoleName)
	if err != nil {
		return errors.New("Relay download edge sequence catalog could not be inspected")
	}
	for _, sequence := range sequences {
		expectedUsage := false
		if privileges, exists := manifest.Tables[sequence.OwnedTable]; exists {
			expectedUsage = privileges.Insert
		}
		if sequence.Usage != expectedUsage || sequence.Select || sequence.Update {
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

func verifyRelayDownloadEdgeEffectivePrivilegeSnapshot(
	snapshot relayEffectivePrivilegeSnapshot,
	manifest relayDownloadEdgePrivilegeManifest,
) error {
	if len(snapshot.Tables) != len(manifest.Tables) || len(snapshot.Columns) != len(manifest.Tables) {
		return errors.New("Relay download edge table privilege manifest does not cover the catalog")
	}
	for table, actual := range snapshot.Tables {
		expected, exists := manifest.Tables[table]
		if !exists {
			return errors.New("Relay download edge table privilege manifest does not cover the catalog")
		}
		if actual.Truncate || actual.References || actual.Trigger {
			return errors.New("Relay download edge role has a forbidden table privilege")
		}
		if actual.Select != expected.Select || actual.Insert != expected.Insert ||
			actual.Update != expected.Update || actual.Delete != expected.Delete {
			return errors.New("Relay download edge table privileges do not match the manifest")
		}
		columns, exists := snapshot.Columns[table]
		if !exists || len(columns) == 0 {
			return errors.New("Relay download edge column privileges do not match the manifest")
		}
		seenUpdates := make(map[string]bool, len(manifest.UpdateColumns[table]))
		for name, column := range columns {
			updateExpected := manifest.UpdateColumns[table][name]
			if column.Select != expected.Select || column.Insert != expected.Insert ||
				column.Update != updateExpected || column.References {
				return errors.New("Relay download edge column privileges do not match the manifest")
			}
			if updateExpected {
				seenUpdates[name] = true
			}
		}
		for name, expected := range manifest.UpdateColumns[table] {
			if expected && !seenUpdates[name] {
				return errors.New("Relay download edge column privilege manifest does not cover the catalog")
			}
		}
	}
	return nil
}

// This is the serving-only counterpart of the frozen migrator verifier. It
// keeps the same role/topology checks and selects only the batched ACL proof.
func verifyRelayDownloadEdgeCurrentDatabaseRoleOptimized(db *gorm.DB, version int64, expectedCanLogin bool) error {
	return verifyRelayDownloadEdgeCurrentDatabaseRoleOptimizedWithTopology(db, version, expectedCanLogin, true)
}

// The protected API has already proven the shared live/full role topology in
// the same pinned connection before it checks the edge role by name. This
// entry keeps the complete edge identity, ownership and ACL proof without
// paying for an identical topology pass a second time.
func verifyRelayDownloadEdgeCurrentDatabaseRoleOptimizedAfterTopology(db *gorm.DB, version int64, expectedCanLogin bool) error {
	return verifyRelayDownloadEdgeCurrentDatabaseRoleOptimizedWithTopology(db, version, expectedCanLogin, false)
}

func verifyRelayDownloadEdgeCurrentDatabaseRoleOptimizedWithTopology(
	db *gorm.DB,
	version int64,
	expectedCanLogin bool,
	checkTopology bool,
) error {
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
	if checkTopology {
		if err := verifyRelayDatabaseRoleLiveTopology(db, ownerRole, migrationRole, runtimeRole); err != nil {
			return err
		}
	}
	return verifyRelayDownloadEdgeDatabasePrivilegeManifestOptimized(db, version)
}
