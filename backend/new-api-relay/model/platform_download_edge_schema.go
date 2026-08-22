package model

import (
	"errors"
	"strings"

	"gorm.io/gorm"
)

const relayDownloadEdgeDatabaseRoleName = "relay_download_edge"

// MigratePlatformDownloadEdgeIsolationWithDB owns the RLS objects as schema,
// not deployment ACL. Provisioning scripts may rotate the edge login and
// grants, but must never mutate these versioned policies after the catalog
// fingerprint has been committed.
func MigratePlatformDownloadEdgeIsolationWithDB(db *gorm.DB) error {
	if db == nil || db.Dialector.Name() != "postgres" {
		return nil
	}
	runtimeRole := strings.TrimSpace(getenvRelayDatabaseRole(relayRuntimeDatabaseRoleEnvironment))
	if runtimeRole != "relay_runtime" {
		return errors.New("Relay runtime database role must be relay_runtime for the v1 RLS artifact")
	}
	if err := verifyRelayDownloadEdgePreMigrationRole(db); err != nil {
		return err
	}
	for _, statement := range []string{
		`ALTER TABLE public.platform_relay_external_deliveries ENABLE ROW LEVEL SECURITY`,
		`DROP POLICY IF EXISTS relay_runtime_outbox_policy ON public.platform_relay_external_deliveries`,
		`CREATE POLICY relay_runtime_outbox_policy ON public.platform_relay_external_deliveries FOR ALL TO relay_runtime USING (true) WITH CHECK (true)`,
		`DROP POLICY IF EXISTS relay_download_edge_outbox_policy ON public.platform_relay_external_deliveries`,
		`CREATE POLICY relay_download_edge_outbox_policy ON public.platform_relay_external_deliveries FOR ALL TO relay_download_edge USING (event_kind = 'download_completion') WITH CHECK (event_kind = 'download_completion')`,
	} {
		if err := db.Exec(statement).Error; err != nil {
			return errors.New("Relay download edge row isolation could not be installed")
		}
	}
	return nil
}

func verifyRelayDownloadEdgePreMigrationRole(db *gorm.DB) error {
	var role struct {
		Exists            bool `gorm:"column:exists"`
		Superuser         bool `gorm:"column:superuser"`
		BypassRLS         bool `gorm:"column:bypass_rls"`
		CreateDatabase    bool `gorm:"column:create_database"`
		CreateRole        bool `gorm:"column:create_role"`
		Replication       bool `gorm:"column:replication"`
		Inherits          bool `gorm:"column:inherits"`
		CanLogin          bool `gorm:"column:can_login"`
		CanConnect        bool `gorm:"column:can_connect"`
		CanCreateDatabase bool `gorm:"column:can_create_database_acl"`
		CanCreateTemp     bool `gorm:"column:can_create_temp"`
		CanUsePublic      bool `gorm:"column:can_use_public"`
		CanCreateSchema   bool `gorm:"column:can_create_schema"`
		CanAccessTable    bool `gorm:"column:can_access_table"`
		CanAccessColumn   bool `gorm:"column:can_access_column"`
		CanAccessSequence bool `gorm:"column:can_access_sequence"`
		CanExecute        bool `gorm:"column:can_execute"`
		HasMembership     bool `gorm:"column:has_membership"`
		HasMember         bool `gorm:"column:has_member"`
	}
	result := db.Raw(`
SELECT true AS exists, role.rolsuper AS superuser, role.rolbypassrls AS bypass_rls,
       role.rolcreatedb AS create_database, role.rolcreaterole AS create_role,
       role.rolreplication AS replication, role.rolinherit AS inherits,
       role.rolcanlogin AS can_login,
	   has_database_privilege(role.rolname, current_database(), 'CONNECT') AS can_connect,
	   has_database_privilege(role.rolname, current_database(), 'CREATE') AS can_create_database_acl,
	   has_database_privilege(role.rolname, current_database(), 'TEMP') AS can_create_temp,
	   has_schema_privilege(role.rolname, 'public', 'USAGE') AS can_use_public,
	   EXISTS (
	     SELECT 1 FROM pg_namespace namespace
	      WHERE namespace.nspname <> 'information_schema' AND namespace.nspname NOT LIKE 'pg_%'
	        AND has_schema_privilege(role.rolname, namespace.oid, 'CREATE')
	   ) AS can_create_schema,
	   EXISTS (
	     SELECT 1 FROM pg_class object JOIN pg_namespace namespace ON namespace.oid = object.relnamespace
	      WHERE namespace.nspname <> 'information_schema' AND namespace.nspname NOT LIKE 'pg_%'
	        AND object.relkind IN ('r', 'p', 'v', 'm', 'f')
	        AND (has_table_privilege(role.rolname, object.oid, 'SELECT')
	          OR has_table_privilege(role.rolname, object.oid, 'INSERT')
	          OR has_table_privilege(role.rolname, object.oid, 'UPDATE')
	          OR has_table_privilege(role.rolname, object.oid, 'DELETE')
	          OR has_table_privilege(role.rolname, object.oid, 'TRUNCATE')
	          OR has_table_privilege(role.rolname, object.oid, 'REFERENCES')
	          OR has_table_privilege(role.rolname, object.oid, 'TRIGGER'))
	   ) AS can_access_table,
	   EXISTS (
	     SELECT 1 FROM pg_class object JOIN pg_namespace namespace ON namespace.oid = object.relnamespace
	      WHERE namespace.nspname <> 'information_schema' AND namespace.nspname NOT LIKE 'pg_%'
	        AND object.relkind IN ('r', 'p', 'v', 'm', 'f')
	        AND (has_any_column_privilege(role.rolname, object.oid, 'SELECT')
	          OR has_any_column_privilege(role.rolname, object.oid, 'INSERT')
	          OR has_any_column_privilege(role.rolname, object.oid, 'UPDATE')
	          OR has_any_column_privilege(role.rolname, object.oid, 'REFERENCES'))
	   ) AS can_access_column,
	   EXISTS (
	     SELECT 1 FROM pg_class object JOIN pg_namespace namespace ON namespace.oid = object.relnamespace
	      WHERE namespace.nspname <> 'information_schema' AND namespace.nspname NOT LIKE 'pg_%'
	        AND object.relkind = 'S'
	        AND (has_sequence_privilege(role.rolname, object.oid, 'USAGE')
	          OR has_sequence_privilege(role.rolname, object.oid, 'SELECT')
	          OR has_sequence_privilege(role.rolname, object.oid, 'UPDATE'))
	   ) AS can_access_sequence,
	   EXISTS (
	     SELECT 1 FROM pg_proc function JOIN pg_namespace namespace ON namespace.oid = function.pronamespace
	      WHERE namespace.nspname <> 'information_schema' AND namespace.nspname NOT LIKE 'pg_%'
	        AND has_function_privilege(role.rolname, function.oid, 'EXECUTE')
	   ) AS can_execute,
       EXISTS (SELECT 1 FROM pg_auth_members membership WHERE membership.member = role.oid) AS has_membership,
	   EXISTS (SELECT 1 FROM pg_auth_members membership WHERE membership.roleid = role.oid) AS has_member
FROM pg_roles role WHERE role.rolname = ?`, relayDownloadEdgeDatabaseRoleName).Scan(&role)
	if result.Error != nil || result.RowsAffected != 1 || !role.Exists || role.Superuser || role.BypassRLS ||
		role.CreateDatabase || role.CreateRole || role.Replication || role.Inherits || role.CanLogin || !role.CanConnect ||
		role.CanCreateDatabase || role.CanCreateTemp || !role.CanUsePublic || role.CanCreateSchema || role.CanAccessTable ||
		role.CanAccessColumn || role.CanAccessSequence || role.CanExecute || role.HasMembership || role.HasMember {
		return errors.New("Relay download edge pre-migration role is not isolated")
	}
	owns, err := relayRoleOwnsForbiddenDatabaseObject(db, relayDownloadEdgeDatabaseRoleName)
	if err != nil || owns {
		return errors.New("Relay download edge pre-migration role owns a forbidden object")
	}
	return nil
}
