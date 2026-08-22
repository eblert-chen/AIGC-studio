package model

import (
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
)

const (
	relayRuntimeDatabaseRoleEnvironment     = "RELAY_RUNTIME_DATABASE_ROLE"
	relayMigrationDatabaseRoleEnvironment   = "RELAY_MIGRATION_DATABASE_ROLE"
	relaySchemaOwnerRoleEnvironment         = "RELAY_SCHEMA_OWNER_DATABASE_ROLE"
	relayDatabaseRoleAttestationEnvironment = "RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED"
)

// RelayDatabaseRoleAttestationRequired extends the production invariant to a
// secure staging deployment. Invalid non-empty values fail closed by enabling
// attestation; they can never silently downgrade a protected deployment.
func RelayDatabaseRoleAttestationRequired() bool {
	if common.IsProductionEnvironment() {
		return true
	}
	raw, present := os.LookupEnv(relayDatabaseRoleAttestationEnvironment)
	if !present || strings.TrimSpace(raw) == "" {
		return false
	}
	required, err := strconv.ParseBool(strings.TrimSpace(raw))
	return err != nil || required
}

type RelayRuntimeDatabaseRoleStatus struct {
	Required bool   `json:"required"`
	State    string `json:"state"`
	Role     string `json:"role,omitempty"`
}

type relayDatabaseRoleAttestation struct {
	SessionUser                 string `gorm:"column:session_user"`
	CurrentUser                 string `gorm:"column:current_user"`
	Superuser                   bool   `gorm:"column:superuser"`
	BypassRLS                   bool   `gorm:"column:bypass_rls"`
	CreateDatabase              bool   `gorm:"column:create_database"`
	CreateRole                  bool   `gorm:"column:create_role"`
	Replication                 bool   `gorm:"column:replication"`
	CanLogin                    bool   `gorm:"column:can_login"`
	SafeSearchPath              bool   `gorm:"column:safe_search_path"`
	CanCreateDatabase           bool   `gorm:"column:can_create_database"`
	CanCreatePublicSchema       bool   `gorm:"column:can_create_public_schema"`
	CanCreateTemporary          bool   `gorm:"column:can_create_temporary"`
	OwnsApplicationObject       bool   `gorm:"column:owns_application_object"`
	CanAssumeDangerousRole      bool   `gorm:"column:can_assume_dangerous_role"`
	CanTruncateApplicationTable bool   `gorm:"column:can_truncate_application_table"`
	CanReadSchemaState          bool   `gorm:"column:can_read_schema_state"`
	CanInsertSchemaState        bool   `gorm:"column:can_insert_schema_state"`
	CanUpdateSchemaState        bool   `gorm:"column:can_update_schema_state"`
	CanDeleteSchemaState        bool   `gorm:"column:can_delete_schema_state"`
	CanTruncateSchemaState      bool   `gorm:"column:can_truncate_schema_state"`
	CanReadSchemaLedger         bool   `gorm:"column:can_read_schema_ledger"`
	CanInsertSchemaLedger       bool   `gorm:"column:can_insert_schema_ledger"`
	CanUpdateSchemaLedger       bool   `gorm:"column:can_update_schema_ledger"`
	CanDeleteSchemaLedger       bool   `gorm:"column:can_delete_schema_ledger"`
	CanTruncateSchemaLedger     bool   `gorm:"column:can_truncate_schema_ledger"`
	CanReadUsers                bool   `gorm:"column:can_read_users"`
	CanWriteUsers               bool   `gorm:"column:can_write_users"`
	CanReadChannels             bool   `gorm:"column:can_read_channels"`
	CanWriteChannels            bool   `gorm:"column:can_write_channels"`
	CanReadGenerationJobs       bool   `gorm:"column:can_read_generation_jobs"`
	CanWriteGenerationJobs      bool   `gorm:"column:can_write_generation_jobs"`
}

const relayDatabaseRoleAttestationSQL = `
SELECT
  session_user AS session_user,
  current_user AS current_user,
  role.rolsuper AS superuser,
  role.rolbypassrls AS bypass_rls,
  role.rolcreatedb AS create_database,
  role.rolcreaterole AS create_role,
	role.rolreplication AS replication,
	role.rolcanlogin AS can_login,
	current_setting('search_path') = 'public' AND
	current_schema() = 'public' AND
	current_schemas(true) = ARRAY['pg_catalog', 'public']::name[] AS safe_search_path,
	has_database_privilege(current_user, current_database(), 'CREATE') AS can_create_database,
	EXISTS (
	  SELECT 1 FROM pg_namespace namespace
	   WHERE namespace.nspname <> 'information_schema'
	     AND namespace.nspname NOT LIKE 'pg_%'
	     AND has_schema_privilege(current_user, namespace.oid, 'CREATE')
	) AS can_create_public_schema,
  has_database_privilege(current_user, current_database(), 'TEMP') AS can_create_temporary,
  EXISTS (
    SELECT 1
      FROM pg_class object
      JOIN pg_namespace namespace ON namespace.oid = object.relnamespace
	 WHERE namespace.nspname <> 'information_schema' AND namespace.nspname NOT LIKE 'pg_%'
       AND object.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
       AND pg_has_role(current_user, object.relowner, 'MEMBER')
  ) OR EXISTS (
    SELECT 1
      FROM pg_proc function
      JOIN pg_namespace namespace ON namespace.oid = function.pronamespace
	 WHERE namespace.nspname <> 'information_schema' AND namespace.nspname NOT LIKE 'pg_%'
       AND pg_has_role(current_user, function.proowner, 'MEMBER')
  ) OR EXISTS (
	SELECT 1 FROM pg_namespace namespace
	 WHERE namespace.nspname <> 'information_schema' AND namespace.nspname NOT LIKE 'pg_%'
       AND pg_has_role(current_user, namespace.nspowner, 'MEMBER')
  ) AS owns_application_object,
  EXISTS (
    SELECT 1
      FROM pg_roles assumed
     WHERE assumed.oid <> role.oid
       AND pg_has_role(current_user, assumed.oid, 'MEMBER')
  ) AS can_assume_dangerous_role,
  EXISTS (
    SELECT 1 FROM pg_class object
    JOIN pg_namespace namespace ON namespace.oid = object.relnamespace
	WHERE namespace.nspname <> 'information_schema' AND namespace.nspname NOT LIKE 'pg_%'
      AND object.relkind IN ('r', 'p')
      AND has_table_privilege(current_user, object.oid, 'TRUNCATE')
  ) AS can_truncate_application_table,
  has_table_privilege(current_user, 'public.relay_schema_state', 'SELECT') AS can_read_schema_state,
	has_table_privilege(current_user, 'public.relay_schema_state', 'INSERT') AS can_insert_schema_state,
	has_table_privilege(current_user, 'public.relay_schema_state', 'UPDATE') AS can_update_schema_state,
	has_table_privilege(current_user, 'public.relay_schema_state', 'DELETE') AS can_delete_schema_state,
	has_table_privilege(current_user, 'public.relay_schema_state', 'TRUNCATE') AS can_truncate_schema_state,
  has_table_privilege(current_user, 'public.relay_schema_migrations', 'SELECT') AS can_read_schema_ledger,
	has_table_privilege(current_user, 'public.relay_schema_migrations', 'INSERT') AS can_insert_schema_ledger,
	has_table_privilege(current_user, 'public.relay_schema_migrations', 'UPDATE') AS can_update_schema_ledger,
	has_table_privilege(current_user, 'public.relay_schema_migrations', 'DELETE') AS can_delete_schema_ledger,
	has_table_privilege(current_user, 'public.relay_schema_migrations', 'TRUNCATE') AS can_truncate_schema_ledger,
  has_table_privilege(current_user, 'public.users', 'SELECT') AS can_read_users,
	(has_table_privilege(current_user, 'public.users', 'INSERT') AND
	 has_table_privilege(current_user, 'public.users', 'UPDATE')) AS can_write_users,
  has_table_privilege(current_user, 'public.channels', 'SELECT') AS can_read_channels,
	(has_table_privilege(current_user, 'public.channels', 'INSERT') AND
	 has_table_privilege(current_user, 'public.channels', 'UPDATE') AND
	 has_table_privilege(current_user, 'public.channels', 'DELETE')) AS can_write_channels,
  has_table_privilege(current_user, 'public.platform_generation_jobs', 'SELECT') AS can_read_generation_jobs,
	(has_table_privilege(current_user, 'public.platform_generation_jobs', 'INSERT') AND
	 has_table_privilege(current_user, 'public.platform_generation_jobs', 'UPDATE')) AS can_write_generation_jobs
FROM pg_roles AS role
WHERE role.rolname = current_user`

// The protected-role surface has two deliberately different gates. The
// provision preflight permits only drift that the role provisioner is about to
// repair (role attributes, parameter ACLs, and the current database ACL). The
// exact gate is shared by migration, runtime, download-edge readiness, and the
// provision postcondition. Neither gate mutates the catalog.
func verifyRelayProtectedDatabaseSurfacePreflight(
	db *gorm.DB,
	ownerRole, migrationRole, runtimeRole string,
	exact bool,
) error {
	edgeRole := relayDownloadEdgeDatabaseRoleName
	roles := []string{ownerRole, migrationRole, runtimeRole, edgeRole}
	seen := make(map[string]struct{}, len(roles))
	for _, role := range roles {
		if !databaseRoleNamePattern.MatchString(role) {
			return errors.New("Relay protected database surface role name is invalid")
		}
		if _, duplicate := seen[role]; duplicate {
			return errors.New("Relay protected database surface role names overlap")
		}
		seen[role] = struct{}{}
	}
	if runtimeRole != "relay_runtime" || edgeRole != "relay_download_edge" {
		return errors.New("Relay protected database surface role topology is invalid")
	}
	if db == nil || db.Dialector.Name() != "postgres" {
		return errors.New("Relay protected database surface requires PostgreSQL")
	}
	if err := verifyRelayProtectedRoleBindingsPreflight(db, ownerRole, migrationRole, runtimeRole, exact); err != nil {
		return err
	}
	if err := verifyRelayOnlyPublicApplicationSchema(db); err != nil {
		return err
	}
	// The migration path reconstructs missing edge column grants after this
	// no-DDL preflight. Treat only the versioned set as an upper bound here;
	// runtime/edge manifest verification later requires the complete set.
	if err := verifyRelayApplicationColumnACLTopology(db, RelaySchemaTargetVersion, false); err != nil {
		return err
	}
	if err := verifyRelayPublicTypeACLTopology(db); err != nil {
		return err
	}
	if err := verifyRelayUnusedDatabaseObjectSurface(db); err != nil {
		return err
	}
	if err := verifyRelayAllowedExtensionMembers(db); err != nil {
		return err
	}
	if err := verifyRelaySystemNamespaceObjectSet(db); err != nil {
		return err
	}
	if err := verifyRelayPostgres16SystemSemanticBaseline(db); err != nil {
		return err
	}
	if err := verifyRelayProtectedRoleOwnershipSurface(db, ownerRole, migrationRole, runtimeRole); err != nil {
		return err
	}
	if err := verifyRelaySystemObjectOwnershipBaseline(db); err != nil {
		return err
	}
	if err := verifyRelayProtectedRoleSystemACLs(db, ownerRole, migrationRole, runtimeRole); err != nil {
		return err
	}
	if err := verifyRelayProtectedRoleDefaultACLs(db, ownerRole, migrationRole, runtimeRole); err != nil {
		return err
	}
	if err := verifyRelayProtectedRoleTablespaces(db, ownerRole, migrationRole, runtimeRole); err != nil {
		return err
	}
	if err := verifyRelayProtectedRoleSharedDependencies(db, ownerRole, migrationRole, runtimeRole, exact); err != nil {
		return err
	}
	if exact {
		if err := verifyRelayPublicDatabaseAndSchemaACLs(db, ownerRole, migrationRole, runtimeRole); err != nil {
			return err
		}
	}
	return nil
}

func verifyRelayProtectedDatabaseExactSurfaceFromEnvironment(db *gorm.DB) error {
	return verifyRelayProtectedDatabaseSurfacePreflight(
		db,
		strings.TrimSpace(os.Getenv(relaySchemaOwnerRoleEnvironment)),
		strings.TrimSpace(os.Getenv(relayMigrationDatabaseRoleEnvironment)),
		strings.TrimSpace(os.Getenv(relayRuntimeDatabaseRoleEnvironment)),
		true,
	)
}

func verifyRelayProtectedRoleBindingsPreflight(
	db *gorm.DB,
	ownerRole, migrationRole, runtimeRole string,
	requireAll bool,
) error {
	var value struct {
		Existing int64 `gorm:"column:existing"`
		Invalid  int64 `gorm:"column:invalid"`
	}
	if err := db.Raw(`
WITH target AS (
  SELECT oid, datname FROM pg_catalog.pg_database WHERE datname = pg_catalog.current_database()
), expected(role_name, role_kind) AS (
  VALUES (?, 'owner'), (?, 'migrator'), (?, 'runtime'), ('relay_download_edge', 'download-edge')
), inspected AS (
  SELECT role.oid,
         pg_catalog.shobj_description(role.oid, 'pg_authid') IS DISTINCT FROM
           ('ai-video-relay-role/v1;database=' || target.datname ||
            ';database_oid=' || target.oid::text || ';kind=' || expected.role_kind) AS invalid
    FROM expected CROSS JOIN target
    LEFT JOIN pg_catalog.pg_roles role ON role.rolname = expected.role_name
)
SELECT count(oid) AS existing,
       count(*) FILTER (WHERE oid IS NOT NULL AND invalid) AS invalid
  FROM inspected`, ownerRole, migrationRole, runtimeRole).Scan(&value).Error; err != nil {
		return errors.New("Relay protected role bindings could not be preflighted")
	}
	if value.Invalid != 0 || (requireAll && value.Existing != 4) {
		return errors.New("Relay protected role database bindings are not exact")
	}
	return nil
}

func verifyRelayOnlyPublicApplicationSchema(db *gorm.DB) error {
	var exact bool
	if err := db.Raw(`SELECT count(*) = 1 AND bool_and(nspname = 'public')
  FROM pg_catalog.pg_namespace
 WHERE nspname <> 'information_schema' AND nspname NOT LIKE 'pg_%'`).Scan(&exact).Error; err != nil {
		return errors.New("Relay application schema surface could not be inspected")
	}
	if !exact {
		return errors.New("Relay application schema surface is not limited to public")
	}
	return nil
}

func verifyRelayUnusedDatabaseObjectSurface(db *gorm.DB) error {
	var exact bool
	if err := db.Raw(`
SELECT NOT EXISTS (
         SELECT 1
           FROM pg_catalog.pg_event_trigger event_trigger
          WHERE NOT (
            event_trigger.evtenabled = 'O'
            AND COALESCE(cardinality(event_trigger.evttags), 0) = 0
            AND ((event_trigger.evtname = 'pgaudit_ddl_command_end' AND event_trigger.evtevent = 'ddl_command_end')
              OR (event_trigger.evtname = 'pgaudit_sql_drop' AND event_trigger.evtevent = 'sql_drop'))
            AND (SELECT count(*)
                   FROM pg_catalog.pg_depend dependency
                   JOIN pg_catalog.pg_extension extension
                     ON extension.oid = dependency.refobjid
                    AND dependency.refclassid = 'pg_catalog.pg_extension'::regclass
                  WHERE dependency.classid = 'pg_catalog.pg_event_trigger'::regclass
                    AND dependency.objid = event_trigger.oid
                    AND dependency.objsubid = 0
                    AND dependency.deptype = 'e'
                    AND extension.extname = 'pgaudit'
                    AND extension.extowner = event_trigger.evtowner) = 1
            AND EXISTS (
              SELECT 1
                FROM pg_catalog.pg_depend function_dependency
                JOIN pg_catalog.pg_depend event_dependency
                  ON event_dependency.refclassid = function_dependency.refclassid
                 AND event_dependency.refobjid = function_dependency.refobjid
                 AND event_dependency.deptype = 'e'
                JOIN pg_catalog.pg_extension extension ON extension.oid = function_dependency.refobjid
               WHERE function_dependency.classid = 'pg_catalog.pg_proc'::regclass
                 AND function_dependency.objid = event_trigger.evtfoid
                 AND function_dependency.objsubid = 0
                 AND function_dependency.refclassid = 'pg_catalog.pg_extension'::regclass
                 AND function_dependency.deptype = 'e'
                 AND event_dependency.classid = 'pg_catalog.pg_event_trigger'::regclass
                 AND event_dependency.objid = event_trigger.oid
                 AND event_dependency.objsubid = 0
                 AND extension.extname = 'pgaudit'
            )
          )
       )
       AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_publication)
       AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_subscription)
	   AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_foreign_data_wrapper)
	   AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_foreign_server)
	   -- pg_user_mapping is intentionally not queried: PostgreSQL 16 denies
	   -- that catalog to ordinary roles. Zero foreign servers is a structural
	   -- proof that no mapping can exist because every mapping depends on one.
	   AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_largeobject_metadata)
	   AND NOT EXISTS (
	     SELECT 1 FROM pg_catalog.pg_conversion object
	     JOIN pg_catalog.pg_namespace namespace ON namespace.oid = object.connamespace
	     WHERE namespace.nspname = 'public'
	   )
	   AND NOT EXISTS (
	     SELECT 1 FROM pg_catalog.pg_operator object
	     JOIN pg_catalog.pg_namespace namespace ON namespace.oid = object.oprnamespace
	     WHERE namespace.nspname = 'public'
	   )
	   AND NOT EXISTS (
	     SELECT 1 FROM pg_catalog.pg_opclass object
	     JOIN pg_catalog.pg_namespace namespace ON namespace.oid = object.opcnamespace
	     WHERE namespace.nspname = 'public'
	   )
	   AND NOT EXISTS (
	     SELECT 1 FROM pg_catalog.pg_opfamily object
	     JOIN pg_catalog.pg_namespace namespace ON namespace.oid = object.opfnamespace
	     WHERE namespace.nspname = 'public'
	   )
	   AND NOT EXISTS (
	     SELECT 1 FROM pg_catalog.pg_ts_config object
	     JOIN pg_catalog.pg_namespace namespace ON namespace.oid = object.cfgnamespace
	     WHERE namespace.nspname = 'public'
	   )
	   AND NOT EXISTS (
	     SELECT 1 FROM pg_catalog.pg_ts_dict object
	     JOIN pg_catalog.pg_namespace namespace ON namespace.oid = object.dictnamespace
	     WHERE namespace.nspname = 'public'
	   )
	   AND NOT EXISTS (
	     SELECT 1 FROM pg_catalog.pg_ts_parser object
	     JOIN pg_catalog.pg_namespace namespace ON namespace.oid = object.prsnamespace
	     WHERE namespace.nspname = 'public'
	   )
	   AND NOT EXISTS (
	     SELECT 1 FROM pg_catalog.pg_ts_template object
	     JOIN pg_catalog.pg_namespace namespace ON namespace.oid = object.tmplnamespace
	     WHERE namespace.nspname = 'public'
	   )
	   AND NOT EXISTS (
	     SELECT 1 FROM pg_catalog.pg_statistic_ext object
	     JOIN pg_catalog.pg_namespace namespace ON namespace.oid = object.stxnamespace
	     WHERE namespace.nspname = 'public'
	   )
	   AND NOT EXISTS (
	     SELECT 1 FROM pg_catalog.pg_rewrite rewrite
	     JOIN pg_catalog.pg_class relation ON relation.oid = rewrite.ev_class
	     JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
	     WHERE namespace.nspname = 'public'
	       AND NOT (rewrite.rulename = '_RETURN' AND relation.relkind IN ('v', 'm'))
	   )
	   AND NOT EXISTS (
	     SELECT 1
	       FROM pg_catalog.pg_extension extension
	       JOIN pg_catalog.pg_namespace namespace ON namespace.oid = extension.extnamespace
	       JOIN pg_catalog.pg_database database ON database.datname = pg_catalog.current_database()
	       JOIN pg_catalog.pg_namespace catalog_namespace ON catalog_namespace.nspname = 'pg_catalog'
	       LEFT JOIN pg_catalog.pg_available_extensions available ON available.name = extension.extname
	      WHERE NOT (
	           (extension.extname = 'plpgsql' AND extension.extversion = '1.0'
	               AND namespace.nspname = 'pg_catalog' AND extension.extowner = catalog_namespace.nspowner)
	           OR (extension.extname = 'pgaudit' AND extension.extversion = available.default_version
	               AND namespace.nspname = 'pg_catalog' AND extension.extowner = database.datdba)
	         )
	   )
	   AND NOT EXISTS (
	     SELECT 1 FROM pg_catalog.pg_class relation
	     JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
	     WHERE namespace.nspname = 'public' AND relation.relkind = 'c'
	   )
	   AND NOT EXISTS (
	     SELECT 1 FROM pg_catalog.pg_cast cast_object
	     WHERE cast_object.oid >= 16384
	   )
	   AND NOT EXISTS (
	     SELECT 1 FROM pg_catalog.pg_trigger trigger_object
	     JOIN pg_catalog.pg_class relation ON relation.oid = trigger_object.tgrelid
	     JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
	     LEFT JOIN pg_catalog.pg_constraint constraint_object ON constraint_object.oid = trigger_object.tgconstraint
	     WHERE namespace.nspname = 'public' AND trigger_object.tgisinternal
	       AND (trigger_object.tgenabled <> 'O' OR trigger_object.tgconstraint = 0
	            OR constraint_object.oid IS NULL
	            OR relation.oid NOT IN (constraint_object.conrelid, constraint_object.confrelid))
	   )
	   AND (SELECT count(*) FROM pg_catalog.pg_extension WHERE extname = 'plpgsql' AND extversion = '1.0') = 1 AS exact`).Scan(&exact).Error; err != nil {
		return fmt.Errorf("Relay unused database object surface could not be inspected: %w", err)
	}
	if !exact {
		return errors.New("Relay unused database object surface is not empty")
	}
	return nil
}

// The extension row alone is not an integrity boundary: an extension owner can
// attach an arbitrary existing object with ALTER EXTENSION ... ADD. Freeze the
// complete PostgreSQL 16 member sets and the security-relevant function shape
// for the two allowed extensions so extension dependency type 'e' cannot turn
// a new pg_catalog object into a trusted baseline.
func verifyRelayAllowedExtensionMembers(db *gorm.DB) error {
	var exact bool
	if err := db.Raw(`
WITH installed AS (
  SELECT extension.oid, extension.extname
    FROM pg_catalog.pg_extension extension
   WHERE extension.extname IN ('plpgsql', 'pgaudit')
), members(extension_oid, classid, objid, objsubid) AS (
  SELECT installed.oid, dependency.classid, dependency.objid, dependency.objsubid
    FROM installed
    JOIN pg_catalog.pg_depend dependency
      ON dependency.refclassid = 'pg_catalog.pg_extension'::regclass
     AND dependency.refobjid = installed.oid
     AND dependency.deptype = 'e'
), expected(extension_oid, classid, objid, objsubid) AS (
  SELECT installed.oid, 'pg_catalog.pg_language'::regclass, language_object.oid, 0
    FROM installed
    JOIN pg_catalog.pg_language language_object ON language_object.lanname = 'plpgsql'
   WHERE installed.extname = 'plpgsql'
     AND language_object.lanispl AND language_object.lanpltrusted
     AND language_object.lanplcallfoid = (
       SELECT function_object.oid FROM pg_catalog.pg_proc function_object
       JOIN pg_catalog.pg_namespace namespace ON namespace.oid = function_object.pronamespace
       WHERE namespace.nspname = 'pg_catalog'
         AND function_object.proname = 'plpgsql_call_handler'
         AND pg_catalog.pg_get_function_identity_arguments(function_object.oid) = ''
     )
     AND language_object.laninline = (
       SELECT function_object.oid FROM pg_catalog.pg_proc function_object
       JOIN pg_catalog.pg_namespace namespace ON namespace.oid = function_object.pronamespace
       WHERE namespace.nspname = 'pg_catalog'
         AND function_object.proname = 'plpgsql_inline_handler'
         AND pg_catalog.pg_get_function_identity_arguments(function_object.oid) = 'internal'
     )
     AND language_object.lanvalidator = (
       SELECT function_object.oid FROM pg_catalog.pg_proc function_object
       JOIN pg_catalog.pg_namespace namespace ON namespace.oid = function_object.pronamespace
       WHERE namespace.nspname = 'pg_catalog'
         AND function_object.proname = 'plpgsql_validator'
         AND pg_catalog.pg_get_function_identity_arguments(function_object.oid) = 'oid'
     )
  UNION ALL
  SELECT installed.oid, 'pg_catalog.pg_proc'::regclass, function_object.oid, 0
    FROM installed
    JOIN pg_catalog.pg_proc function_object ON true
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = function_object.pronamespace
    JOIN pg_catalog.pg_language language_object ON language_object.oid = function_object.prolang
   WHERE installed.extname = 'plpgsql'
     AND namespace.nspname = 'pg_catalog' AND language_object.lanname = 'c'
     AND NOT function_object.prosecdef AND function_object.provolatile = 'v'
     AND function_object.proparallel = 'u' AND NOT function_object.proleakproof
     AND function_object.probin = '$libdir/plpgsql' AND function_object.proconfig IS NULL
     AND (
       (function_object.proname = 'plpgsql_call_handler'
         AND pg_catalog.pg_get_function_identity_arguments(function_object.oid) = ''
         AND function_object.prorettype = 'pg_catalog.language_handler'::regtype
         AND NOT function_object.proisstrict
         AND function_object.prosrc = 'plpgsql_call_handler')
       OR (function_object.proname = 'plpgsql_inline_handler'
         AND pg_catalog.pg_get_function_identity_arguments(function_object.oid) = 'internal'
         AND function_object.prorettype = 'pg_catalog.void'::regtype
         AND function_object.proisstrict
         AND function_object.prosrc = 'plpgsql_inline_handler')
       OR (function_object.proname = 'plpgsql_validator'
         AND pg_catalog.pg_get_function_identity_arguments(function_object.oid) = 'oid'
         AND function_object.prorettype = 'pg_catalog.void'::regtype
         AND function_object.proisstrict
         AND function_object.prosrc = 'plpgsql_validator')
     )
  UNION ALL
  SELECT installed.oid, 'pg_catalog.pg_proc'::regclass, function_object.oid, 0
    FROM installed
    JOIN pg_catalog.pg_proc function_object ON true
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = function_object.pronamespace
    JOIN pg_catalog.pg_language language_object ON language_object.oid = function_object.prolang
   WHERE installed.extname = 'pgaudit'
     AND namespace.nspname = 'pg_catalog' AND language_object.lanname = 'c'
     AND function_object.proname IN ('pgaudit_ddl_command_end', 'pgaudit_sql_drop')
     AND pg_catalog.pg_get_function_identity_arguments(function_object.oid) = ''
     AND function_object.prorettype = 'pg_catalog.event_trigger'::regtype
     AND function_object.prosecdef AND function_object.provolatile = 'v'
     AND function_object.proparallel = 'u' AND NOT function_object.proisstrict
     AND NOT function_object.proleakproof AND function_object.probin = '$libdir/pgaudit'
     AND function_object.prosrc = function_object.proname
     AND function_object.proconfig = ARRAY['search_path="pg_catalog, pg_temp"']::text[]
  UNION ALL
  SELECT installed.oid, 'pg_catalog.pg_event_trigger'::regclass, event_trigger.oid, 0
    FROM installed
    JOIN pg_catalog.pg_event_trigger event_trigger ON true
   WHERE installed.extname = 'pgaudit'
     AND event_trigger.evtenabled = 'O'
     AND COALESCE(cardinality(event_trigger.evttags), 0) = 0
     AND ((event_trigger.evtname = 'pgaudit_ddl_command_end'
             AND event_trigger.evtevent = 'ddl_command_end')
       OR (event_trigger.evtname = 'pgaudit_sql_drop'
             AND event_trigger.evtevent = 'sql_drop'))
     AND event_trigger.evtfoid = (
       SELECT function_object.oid FROM pg_catalog.pg_proc function_object
       JOIN pg_catalog.pg_namespace namespace ON namespace.oid = function_object.pronamespace
       WHERE namespace.nspname = 'pg_catalog'
         AND function_object.proname = event_trigger.evtname
         AND pg_catalog.pg_get_function_identity_arguments(function_object.oid) = ''
     )
), extra AS (
  SELECT * FROM members EXCEPT SELECT * FROM expected
), missing AS (
  SELECT * FROM expected EXCEPT SELECT * FROM members
)
SELECT (SELECT count(*) FROM installed WHERE extname = 'plpgsql') = 1
   AND (SELECT count(*) FROM installed WHERE extname = 'pgaudit') <= 1
   AND (SELECT count(*) FROM expected
         WHERE extension_oid = (SELECT oid FROM installed WHERE extname = 'plpgsql')) = 4
   AND (NOT EXISTS (SELECT 1 FROM installed WHERE extname = 'pgaudit')
        OR (SELECT count(*) FROM expected
             WHERE extension_oid = (SELECT oid FROM installed WHERE extname = 'pgaudit')) = 4)
   AND NOT EXISTS (SELECT 1 FROM extra)
   AND NOT EXISTS (SELECT 1 FROM missing) AS exact`).Scan(&exact).Error; err != nil {
		return fmt.Errorf("Relay allowed extension member surface could not be inspected: %w", err)
	}
	if !exact {
		return errors.New("Relay allowed extension member surface is not exact")
	}
	return nil
}

// A pg_init_privs/acldefault comparison proves the ACL of an existing system
// object, but it cannot distinguish a newly-created pg_catalog object whose
// default ACL is itself unsafe (functions, for example, default to PUBLIC
// EXECUTE). PostgreSQL reserves OIDs below FirstNormalObjectId (16384) for the
// bootstrap catalog. Reject every later object attached to pg_catalog or
// information_schema unless it is one of the exact plpgsql/pgaudit members
// attested immediately above. Relation-owned objects are included explicitly
// because rules, triggers, constraints, defaults, policies, and enum labels do
// not have a direct namespace dependency of their own.
func verifyRelaySystemNamespaceObjectSet(db *gorm.DB) error {
	var unexpected int64
	if err := db.Raw(`
WITH allowed_extension_members AS (
  SELECT dependency.classid, dependency.objid, dependency.objsubid
    FROM pg_catalog.pg_depend dependency
    JOIN pg_catalog.pg_extension extension
      ON extension.oid = dependency.refobjid
     AND dependency.refclassid = 'pg_catalog.pg_extension'::regclass
   WHERE dependency.deptype = 'e'
     AND extension.extname IN ('plpgsql', 'pgaudit')
), system_namespaces AS (
  SELECT namespace.oid
    FROM pg_catalog.pg_namespace namespace
   WHERE namespace.nspname IN ('pg_catalog', 'information_schema')
), unexpected_objects(classid, objid, objsubid) AS (
  SELECT dependency.classid, dependency.objid, dependency.objsubid
    FROM pg_catalog.pg_depend dependency
   WHERE dependency.refclassid = 'pg_catalog.pg_namespace'::regclass
     AND dependency.refobjid IN (SELECT oid FROM system_namespaces)
     AND dependency.objid >= 16384
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = dependency.classid
          AND allowed.objid = dependency.objid
          AND allowed.objsubid = dependency.objsubid
     )
  UNION ALL
  SELECT 'pg_catalog.pg_namespace'::regclass, namespace.oid, 0
    FROM pg_catalog.pg_namespace namespace
   WHERE namespace.oid >= 16384
     AND namespace.nspname LIKE 'pg\_%' ESCAPE '\'
     AND namespace.nspname NOT LIKE 'pg\_temp\_%' ESCAPE '\'
     AND namespace.nspname NOT LIKE 'pg\_toast\_temp\_%' ESCAPE '\'
  UNION ALL
  SELECT 'pg_catalog.pg_class'::regclass, relation.oid, 0
    FROM pg_catalog.pg_class relation
   WHERE relation.oid >= 16384
     AND relation.relnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_class'::regclass
          AND allowed.objid = relation.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_proc'::regclass, function_object.oid, 0
    FROM pg_catalog.pg_proc function_object
   WHERE function_object.oid >= 16384
     AND function_object.pronamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_proc'::regclass
          AND allowed.objid = function_object.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_type'::regclass, type_object.oid, 0
    FROM pg_catalog.pg_type type_object
   WHERE type_object.oid >= 16384
     AND type_object.typnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_type'::regclass
          AND allowed.objid = type_object.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_collation'::regclass, collation_object.oid, 0
    FROM pg_catalog.pg_collation collation_object
   WHERE collation_object.oid >= 16384
     AND collation_object.collnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_collation'::regclass
          AND allowed.objid = collation_object.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_conversion'::regclass, conversion_object.oid, 0
    FROM pg_catalog.pg_conversion conversion_object
   WHERE conversion_object.oid >= 16384
     AND conversion_object.connamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_conversion'::regclass
          AND allowed.objid = conversion_object.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_operator'::regclass, operator_object.oid, 0
    FROM pg_catalog.pg_operator operator_object
   WHERE operator_object.oid >= 16384
     AND operator_object.oprnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_operator'::regclass
          AND allowed.objid = operator_object.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_opclass'::regclass, operator_class.oid, 0
    FROM pg_catalog.pg_opclass operator_class
   WHERE operator_class.oid >= 16384
     AND operator_class.opcnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_opclass'::regclass
          AND allowed.objid = operator_class.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_opfamily'::regclass, operator_family.oid, 0
    FROM pg_catalog.pg_opfamily operator_family
   WHERE operator_family.oid >= 16384
     AND operator_family.opfnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_opfamily'::regclass
          AND allowed.objid = operator_family.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_ts_config'::regclass, text_search_config.oid, 0
    FROM pg_catalog.pg_ts_config text_search_config
   WHERE text_search_config.oid >= 16384
     AND text_search_config.cfgnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_ts_config'::regclass
          AND allowed.objid = text_search_config.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_ts_dict'::regclass, text_search_dictionary.oid, 0
    FROM pg_catalog.pg_ts_dict text_search_dictionary
   WHERE text_search_dictionary.oid >= 16384
     AND text_search_dictionary.dictnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_ts_dict'::regclass
          AND allowed.objid = text_search_dictionary.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_ts_parser'::regclass, text_search_parser.oid, 0
    FROM pg_catalog.pg_ts_parser text_search_parser
   WHERE text_search_parser.oid >= 16384
     AND text_search_parser.prsnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_ts_parser'::regclass
          AND allowed.objid = text_search_parser.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_ts_template'::regclass, text_search_template.oid, 0
    FROM pg_catalog.pg_ts_template text_search_template
   WHERE text_search_template.oid >= 16384
     AND text_search_template.tmplnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_ts_template'::regclass
          AND allowed.objid = text_search_template.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_statistic_ext'::regclass, statistics_object.oid, 0
    FROM pg_catalog.pg_statistic_ext statistics_object
   WHERE statistics_object.oid >= 16384
     AND statistics_object.stxnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_statistic_ext'::regclass
          AND allowed.objid = statistics_object.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_rewrite'::regclass, rewrite.oid, 0
    FROM pg_catalog.pg_rewrite rewrite
    JOIN pg_catalog.pg_class relation ON relation.oid = rewrite.ev_class
   WHERE rewrite.oid >= 16384
     AND relation.relnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_rewrite'::regclass
          AND allowed.objid = rewrite.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_trigger'::regclass, trigger_object.oid, 0
    FROM pg_catalog.pg_trigger trigger_object
    JOIN pg_catalog.pg_class relation ON relation.oid = trigger_object.tgrelid
   WHERE trigger_object.oid >= 16384
     AND relation.relnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_trigger'::regclass
          AND allowed.objid = trigger_object.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_constraint'::regclass, constraint_object.oid, 0
    FROM pg_catalog.pg_constraint constraint_object
    LEFT JOIN pg_catalog.pg_class relation ON relation.oid = constraint_object.conrelid
    LEFT JOIN pg_catalog.pg_type type_object ON type_object.oid = constraint_object.contypid
   WHERE constraint_object.oid >= 16384
     AND (relation.relnamespace IN (SELECT oid FROM system_namespaces)
       OR type_object.typnamespace IN (SELECT oid FROM system_namespaces))
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_constraint'::regclass
          AND allowed.objid = constraint_object.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_attrdef'::regclass, default_object.oid, 0
    FROM pg_catalog.pg_attrdef default_object
    JOIN pg_catalog.pg_class relation ON relation.oid = default_object.adrelid
   WHERE default_object.oid >= 16384
     AND relation.relnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_attrdef'::regclass
          AND allowed.objid = default_object.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_policy'::regclass, policy.oid, 0
    FROM pg_catalog.pg_policy policy
    JOIN pg_catalog.pg_class relation ON relation.oid = policy.polrelid
   WHERE policy.oid >= 16384
     AND relation.relnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_policy'::regclass
          AND allowed.objid = policy.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_enum'::regclass, enum_label.oid, 0
    FROM pg_catalog.pg_enum enum_label
    JOIN pg_catalog.pg_type type_object ON type_object.oid = enum_label.enumtypid
   WHERE enum_label.oid >= 16384
     AND type_object.typnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_enum'::regclass
          AND allowed.objid = enum_label.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_language'::regclass, language_object.oid, 0
    FROM pg_catalog.pg_language language_object
   WHERE language_object.oid >= 16384
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_language'::regclass
          AND allowed.objid = language_object.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_am'::regclass, access_method.oid, 0
    FROM pg_catalog.pg_am access_method
   WHERE access_method.oid >= 16384
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_am'::regclass
          AND allowed.objid = access_method.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'pg_catalog.pg_transform'::regclass, transform_object.oid, 0
    FROM pg_catalog.pg_transform transform_object
   WHERE transform_object.oid >= 16384
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_transform'::regclass
          AND allowed.objid = transform_object.oid AND allowed.objsubid = 0
     )
)
SELECT count(*) FROM unexpected_objects`).Scan(&unexpected).Error; err != nil {
		return fmt.Errorf("Relay system namespace object set could not be inspected: %w", err)
	}
	if unexpected != 0 {
		return errors.New("Relay system namespace contains an object outside the PostgreSQL 16 baseline")
	}
	return nil
}

func verifyRelayProtectedRoleOwnershipSurface(db *gorm.DB, ownerRole, migrationRole, runtimeRole string) error {
	var unexpected int64
	if err := db.Raw(`
WITH protected(role_name, role_kind) AS (
  VALUES (?, 'owner'), (?, 'migrator'), (?, 'runtime'), ('relay_download_edge', 'download-edge')
), roles AS (
  SELECT role.oid, protected.role_kind
    FROM protected JOIN pg_catalog.pg_roles role ON role.rolname = protected.role_name
), public_relations AS (
  SELECT relation.oid, relation.reltoastrelid
    FROM pg_catalog.pg_class relation
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
   WHERE namespace.nspname = 'public'
), allowed_toast_relations AS (
  SELECT reltoastrelid AS oid FROM public_relations WHERE reltoastrelid <> 0
), allowed_toast_classes AS (
  SELECT oid FROM allowed_toast_relations
  UNION
  SELECT index_relation.indexrelid
    FROM pg_catalog.pg_index index_relation
    JOIN allowed_toast_relations toast ON toast.oid = index_relation.indrelid
), owned(role_kind, allowed) AS (
  SELECT roles.role_kind, roles.role_kind = 'owner' AND namespace.nspname = 'public'
    FROM roles JOIN pg_catalog.pg_namespace namespace ON namespace.nspowner = roles.oid
  UNION ALL
  SELECT roles.role_kind, roles.role_kind = 'owner' AND
         (namespace.nspname = 'public' OR relation.oid IN (SELECT oid FROM allowed_toast_classes))
    FROM roles JOIN pg_catalog.pg_class relation ON relation.relowner = roles.oid
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
  UNION ALL
  SELECT roles.role_kind, roles.role_kind = 'owner' AND
         (namespace.nspname = 'public' OR type_object.typrelid IN (SELECT oid FROM allowed_toast_classes))
    FROM roles JOIN pg_catalog.pg_type type_object ON type_object.typowner = roles.oid
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = type_object.typnamespace
  UNION ALL
  SELECT roles.role_kind, roles.role_kind = 'owner' AND namespace.nspname = 'public'
    FROM roles JOIN pg_catalog.pg_proc function_object ON function_object.proowner = roles.oid
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = function_object.pronamespace
  UNION ALL
  SELECT roles.role_kind, roles.role_kind = 'owner' AND namespace.nspname = 'public'
    FROM roles JOIN pg_catalog.pg_collation collation_object ON collation_object.collowner = roles.oid
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = collation_object.collnamespace
  UNION ALL
  SELECT roles.role_kind, roles.role_kind = 'owner' AND namespace.nspname = 'public'
    FROM roles JOIN pg_catalog.pg_conversion conversion_object ON conversion_object.conowner = roles.oid
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = conversion_object.connamespace
  UNION ALL SELECT roles.role_kind, false FROM roles JOIN pg_catalog.pg_extension object ON object.extowner = roles.oid
  UNION ALL SELECT roles.role_kind, false FROM roles JOIN pg_catalog.pg_language object ON object.lanowner = roles.oid
  UNION ALL SELECT roles.role_kind, false FROM roles JOIN pg_catalog.pg_event_trigger object ON object.evtowner = roles.oid
  UNION ALL SELECT roles.role_kind, false FROM roles JOIN pg_catalog.pg_publication object ON object.pubowner = roles.oid
  UNION ALL SELECT roles.role_kind, false FROM roles JOIN pg_catalog.pg_subscription object ON object.subowner = roles.oid
  UNION ALL SELECT roles.role_kind, false FROM roles JOIN pg_catalog.pg_foreign_data_wrapper object ON object.fdwowner = roles.oid
  UNION ALL SELECT roles.role_kind, false FROM roles JOIN pg_catalog.pg_foreign_server object ON object.srvowner = roles.oid
  UNION ALL SELECT roles.role_kind, false FROM roles JOIN pg_catalog.pg_largeobject_metadata object ON object.lomowner = roles.oid
  UNION ALL SELECT roles.role_kind, false FROM roles JOIN pg_catalog.pg_operator object ON object.oprowner = roles.oid
  UNION ALL SELECT roles.role_kind, false FROM roles JOIN pg_catalog.pg_opclass object ON object.opcowner = roles.oid
  UNION ALL SELECT roles.role_kind, false FROM roles JOIN pg_catalog.pg_opfamily object ON object.opfowner = roles.oid
  UNION ALL SELECT roles.role_kind, false FROM roles JOIN pg_catalog.pg_ts_config object ON object.cfgowner = roles.oid
  UNION ALL SELECT roles.role_kind, false FROM roles JOIN pg_catalog.pg_ts_dict object ON object.dictowner = roles.oid
  UNION ALL SELECT roles.role_kind, false FROM roles JOIN pg_catalog.pg_statistic_ext object ON object.stxowner = roles.oid
  UNION ALL SELECT roles.role_kind, false FROM roles JOIN pg_catalog.pg_tablespace object ON object.spcowner = roles.oid
  UNION ALL SELECT roles.role_kind, false FROM roles JOIN pg_catalog.pg_database object ON object.datdba = roles.oid
)
SELECT count(*) FROM owned WHERE NOT allowed`, ownerRole, migrationRole, runtimeRole).Scan(&unexpected).Error; err != nil {
		return errors.New("Relay protected role ownership surface could not be inspected")
	}
	if unexpected != 0 {
		return errors.New("Relay protected role owns an object outside the versioned public surface")
	}
	return nil
}

func verifyRelaySystemObjectOwnershipBaseline(db *gorm.DB) error {
	var unexpected int64
	if err := db.Raw(`
WITH public_relations AS (
  SELECT relation.reltoastrelid
    FROM pg_catalog.pg_class relation
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
   WHERE namespace.nspname = 'public' AND relation.reltoastrelid <> 0
), allowed_toast_classes AS (
  SELECT reltoastrelid AS oid FROM public_relations
  UNION
  SELECT index_relation.indexrelid
    FROM pg_catalog.pg_index index_relation
    JOIN public_relations toast ON toast.reltoastrelid = index_relation.indrelid
), catalog_owner AS (
  SELECT nspowner AS oid FROM pg_catalog.pg_namespace WHERE nspname = 'pg_catalog'
), system_objects(classoid, objoid, objsubid, owner_oid, namespace_owner, derived) AS (
  SELECT 'pg_catalog.pg_namespace'::regclass, namespace.oid, 0, namespace.nspowner, catalog_owner.oid, false
    FROM pg_catalog.pg_namespace namespace CROSS JOIN catalog_owner
   WHERE namespace.nspname = 'information_schema'
      OR (namespace.nspname LIKE 'pg_%'
          AND namespace.nspname NOT LIKE 'pg_temp_%'
          AND namespace.nspname NOT LIKE 'pg_toast_temp_%')
  UNION ALL
  SELECT 'pg_catalog.pg_class'::regclass, relation.oid, 0, relation.relowner, namespace.nspowner,
         relation.oid IN (SELECT oid FROM allowed_toast_classes)
    FROM pg_catalog.pg_class relation
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
   WHERE namespace.nspname = 'information_schema'
      OR (namespace.nspname LIKE 'pg_%'
          AND namespace.nspname NOT LIKE 'pg_temp_%'
          AND namespace.nspname NOT LIKE 'pg_toast_temp_%')
  UNION ALL
  SELECT 'pg_catalog.pg_type'::regclass, type_object.oid, 0, type_object.typowner, namespace.nspowner,
         type_object.typrelid IN (SELECT oid FROM allowed_toast_classes)
    FROM pg_catalog.pg_type type_object
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = type_object.typnamespace
   WHERE namespace.nspname = 'information_schema'
      OR (namespace.nspname LIKE 'pg_%'
          AND namespace.nspname NOT LIKE 'pg_temp_%'
          AND namespace.nspname NOT LIKE 'pg_toast_temp_%')
  UNION ALL
  SELECT 'pg_catalog.pg_proc'::regclass, function_object.oid, 0, function_object.proowner, namespace.nspowner, false
    FROM pg_catalog.pg_proc function_object
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = function_object.pronamespace
   WHERE namespace.nspname = 'information_schema'
      OR (namespace.nspname LIKE 'pg_%'
          AND namespace.nspname NOT LIKE 'pg_temp_%'
          AND namespace.nspname NOT LIKE 'pg_toast_temp_%')
  UNION ALL
  SELECT 'pg_catalog.pg_collation'::regclass, object.oid, 0, object.collowner, namespace.nspowner, false
    FROM pg_catalog.pg_collation object JOIN pg_catalog.pg_namespace namespace ON namespace.oid = object.collnamespace
   WHERE namespace.nspname = 'information_schema' OR namespace.nspname LIKE 'pg_%'
  UNION ALL
  SELECT 'pg_catalog.pg_conversion'::regclass, object.oid, 0, object.conowner, namespace.nspowner, false
    FROM pg_catalog.pg_conversion object JOIN pg_catalog.pg_namespace namespace ON namespace.oid = object.connamespace
   WHERE namespace.nspname = 'information_schema' OR namespace.nspname LIKE 'pg_%'
  UNION ALL
  SELECT 'pg_catalog.pg_operator'::regclass, object.oid, 0, object.oprowner, namespace.nspowner, false
    FROM pg_catalog.pg_operator object JOIN pg_catalog.pg_namespace namespace ON namespace.oid = object.oprnamespace
   WHERE namespace.nspname = 'information_schema' OR namespace.nspname LIKE 'pg_%'
  UNION ALL
  SELECT 'pg_catalog.pg_opclass'::regclass, object.oid, 0, object.opcowner, namespace.nspowner, false
    FROM pg_catalog.pg_opclass object JOIN pg_catalog.pg_namespace namespace ON namespace.oid = object.opcnamespace
   WHERE namespace.nspname = 'information_schema' OR namespace.nspname LIKE 'pg_%'
  UNION ALL
  SELECT 'pg_catalog.pg_opfamily'::regclass, object.oid, 0, object.opfowner, namespace.nspowner, false
    FROM pg_catalog.pg_opfamily object JOIN pg_catalog.pg_namespace namespace ON namespace.oid = object.opfnamespace
   WHERE namespace.nspname = 'information_schema' OR namespace.nspname LIKE 'pg_%'
  UNION ALL
  SELECT 'pg_catalog.pg_ts_config'::regclass, object.oid, 0, object.cfgowner, namespace.nspowner, false
    FROM pg_catalog.pg_ts_config object JOIN pg_catalog.pg_namespace namespace ON namespace.oid = object.cfgnamespace
   WHERE namespace.nspname = 'information_schema' OR namespace.nspname LIKE 'pg_%'
  UNION ALL
  SELECT 'pg_catalog.pg_ts_dict'::regclass, object.oid, 0, object.dictowner, namespace.nspowner, false
    FROM pg_catalog.pg_ts_dict object JOIN pg_catalog.pg_namespace namespace ON namespace.oid = object.dictnamespace
   WHERE namespace.nspname = 'information_schema' OR namespace.nspname LIKE 'pg_%'
  UNION ALL
  SELECT 'pg_catalog.pg_statistic_ext'::regclass, object.oid, 0, object.stxowner, namespace.nspowner, false
    FROM pg_catalog.pg_statistic_ext object JOIN pg_catalog.pg_namespace namespace ON namespace.oid = object.stxnamespace
   WHERE namespace.nspname = 'information_schema' OR namespace.nspname LIKE 'pg_%'
  UNION ALL
  SELECT 'pg_catalog.pg_language'::regclass, object.oid, 0, object.lanowner, catalog_owner.oid, false
    FROM pg_catalog.pg_language object CROSS JOIN catalog_owner
  UNION ALL
  SELECT 'pg_catalog.pg_event_trigger'::regclass, object.oid, 0, object.evtowner, catalog_owner.oid, false
    FROM pg_catalog.pg_event_trigger object CROSS JOIN catalog_owner
), inspected AS (
  SELECT object.*,
         (SELECT count(*)
            FROM pg_catalog.pg_depend dependency
            JOIN pg_catalog.pg_extension extension
              ON extension.oid = dependency.refobjid
             AND dependency.refclassid = 'pg_catalog.pg_extension'::regclass
           WHERE dependency.classid = object.classoid
             AND dependency.objid = object.objoid
             AND dependency.objsubid = object.objsubid
             AND dependency.deptype = 'e'
             AND extension.extowner = object.owner_oid) AS matching_extensions
    FROM system_objects object
)
SELECT count(*)
  FROM inspected
 WHERE NOT derived AND owner_oid <> namespace_owner AND matching_extensions <> 1`).Scan(&unexpected).Error; err != nil {
		return fmt.Errorf("Relay system object ownership baseline could not be inspected: %w", err)
	}
	if unexpected != 0 {
		return errors.New("Relay system object is owned by an untrusted non-extension role")
	}
	return nil
}

func verifyRelayProtectedRoleSystemACLs(db *gorm.DB, ownerRole, migrationRole, runtimeRole string) error {
	arguments := []any{ownerRole, migrationRole, runtimeRole}
	var direct int64
	if err := db.Raw(`
WITH protected AS (
  SELECT role.oid FROM pg_catalog.pg_roles role WHERE role.rolname IN (?, ?, ?, 'relay_download_edge')
), direct_grants AS (
  SELECT acl.grantee
    FROM pg_catalog.pg_namespace namespace
    CROSS JOIN LATERAL pg_catalog.aclexplode(namespace.nspacl) acl
   WHERE namespace.nspname = 'information_schema' OR namespace.nspname LIKE 'pg_%'
  UNION ALL
  SELECT acl.grantee
    FROM pg_catalog.pg_class relation
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
    CROSS JOIN LATERAL pg_catalog.aclexplode(relation.relacl) acl
   WHERE namespace.nspname = 'information_schema' OR namespace.nspname LIKE 'pg_%'
  UNION ALL
  SELECT acl.grantee
    FROM pg_catalog.pg_attribute attribute
    JOIN pg_catalog.pg_class relation ON relation.oid = attribute.attrelid
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
    CROSS JOIN LATERAL pg_catalog.aclexplode(attribute.attacl) acl
   WHERE attribute.attnum > 0 AND NOT attribute.attisdropped
     AND (namespace.nspname = 'information_schema' OR namespace.nspname LIKE 'pg_%')
  UNION ALL
  SELECT acl.grantee
    FROM pg_catalog.pg_proc function_object
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = function_object.pronamespace
    CROSS JOIN LATERAL pg_catalog.aclexplode(function_object.proacl) acl
   WHERE namespace.nspname = 'information_schema' OR namespace.nspname LIKE 'pg_%'
  UNION ALL
  SELECT acl.grantee
    FROM pg_catalog.pg_type type_object
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = type_object.typnamespace
    CROSS JOIN LATERAL pg_catalog.aclexplode(type_object.typacl) acl
   WHERE namespace.nspname = 'information_schema' OR namespace.nspname LIKE 'pg_%'
  UNION ALL
  SELECT acl.grantee
    FROM pg_catalog.pg_language language_object
    CROSS JOIN LATERAL pg_catalog.aclexplode(language_object.lanacl) acl
)
	SELECT count(*) FROM direct_grants JOIN protected ON protected.oid = direct_grants.grantee`, arguments...).Scan(&direct).Error; err != nil {
		return fmt.Errorf("Relay protected role system ACLs could not be inspected: %w", err)
	}
	if direct != 0 {
		return errors.New("Relay protected role has a direct system object ACL")
	}

	// pg_init_privs is the immutable initdb/extension baseline. Revoking a
	// baseline tuple is permitted, but every current grantee/grantor/privilege/
	// grant-option tuple on pg_* objects must come from that baseline. This also
	// catches a grant to an arbitrary intermediary role, not just a direct grant
	// to one of the four protected roles.
	var systemEscalations int64
	if err := db.Raw(`
WITH objects(classoid, objoid, objsubid, current_acl, default_acl) AS (
  SELECT 'pg_catalog.pg_namespace'::regclass, namespace.oid, 0,
         namespace.nspacl, pg_catalog.acldefault('n'::"char", namespace.nspowner)
    FROM pg_catalog.pg_namespace namespace WHERE namespace.nspname LIKE 'pg_%'
  UNION ALL
  SELECT 'pg_catalog.pg_class'::regclass, relation.oid, 0,
         relation.relacl,
         pg_catalog.acldefault(CASE WHEN relation.relkind = 'S' THEN 's'::"char" ELSE 'r'::"char" END, relation.relowner)
    FROM pg_catalog.pg_class relation
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
   WHERE namespace.nspname LIKE 'pg_%'
  UNION ALL
  SELECT 'pg_catalog.pg_class'::regclass, relation.oid, attribute.attnum,
         attribute.attacl, NULL::aclitem[]
    FROM pg_catalog.pg_attribute attribute
    JOIN pg_catalog.pg_class relation ON relation.oid = attribute.attrelid
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
   WHERE attribute.attnum > 0 AND NOT attribute.attisdropped AND namespace.nspname LIKE 'pg_%'
  UNION ALL
  SELECT 'pg_catalog.pg_proc'::regclass, function_object.oid, 0,
         function_object.proacl, pg_catalog.acldefault('f'::"char", function_object.proowner)
    FROM pg_catalog.pg_proc function_object
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = function_object.pronamespace
   WHERE namespace.nspname LIKE 'pg_%'
  UNION ALL
  SELECT 'pg_catalog.pg_type'::regclass, type_object.oid, 0,
         type_object.typacl, pg_catalog.acldefault('T'::"char", type_object.typowner)
    FROM pg_catalog.pg_type type_object
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = type_object.typnamespace
   WHERE namespace.nspname LIKE 'pg_%'
  UNION ALL
  SELECT 'pg_catalog.pg_language'::regclass, language_object.oid, 0,
         language_object.lanacl, pg_catalog.acldefault('l'::"char", language_object.lanowner)
    FROM pg_catalog.pg_language language_object
), current_acl AS (
  SELECT objects.classoid, objects.objoid, objects.objsubid,
         acl.grantee, acl.grantor, acl.privilege_type, acl.is_grantable
    FROM objects
    CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(objects.current_acl, objects.default_acl)) acl
), baseline_acl AS (
  SELECT objects.classoid, objects.objoid, objects.objsubid,
         acl.grantee, acl.grantor, acl.privilege_type, acl.is_grantable
    FROM objects
    LEFT JOIN pg_catalog.pg_init_privs initial
      ON initial.classoid = objects.classoid AND initial.objoid = objects.objoid
     AND initial.objsubid = objects.objsubid AND initial.privtype IN ('i', 'e')
    CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(initial.initprivs, objects.default_acl)) acl
)
SELECT count(*)
  FROM current_acl current_privilege
 WHERE NOT EXISTS (
   SELECT 1 FROM baseline_acl baseline
    WHERE baseline.classoid = current_privilege.classoid
      AND baseline.objoid = current_privilege.objoid
      AND baseline.objsubid = current_privilege.objsubid
      AND baseline.grantee = current_privilege.grantee
      AND baseline.grantor = current_privilege.grantor
      AND baseline.privilege_type = current_privilege.privilege_type
      AND baseline.is_grantable = current_privilege.is_grantable
 )`).Scan(&systemEscalations).Error; err != nil {
		return fmt.Errorf("Relay system ACL baseline could not be inspected: %w", err)
	}
	if systemEscalations != 0 {
		return errors.New("Relay system ACL exceeds the PostgreSQL initial privilege baseline")
	}

	// PostgreSQL 16 does not populate pg_init_privs for information_schema.
	// Its standard surface is still closed explicitly: only the object owner
	// and PUBLIC may appear, and PUBLIC gets only the read/use/execute privilege
	// appropriate to the object kind, never a grant option.
	var informationSchemaEscalations int64
	if err := db.Raw(`
WITH information_schema_acl(owner_oid, object_kind, acl) AS (
  SELECT namespace.nspowner, 'schema',
         COALESCE(namespace.nspacl, pg_catalog.acldefault('n'::"char", namespace.nspowner))
    FROM pg_catalog.pg_namespace namespace WHERE namespace.nspname = 'information_schema'
  UNION ALL
  SELECT relation.relowner, 'relation',
         COALESCE(relation.relacl,
           pg_catalog.acldefault(CASE WHEN relation.relkind = 'S' THEN 's'::"char" ELSE 'r'::"char" END, relation.relowner))
    FROM pg_catalog.pg_class relation
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
   WHERE namespace.nspname = 'information_schema'
  UNION ALL
  SELECT relation.relowner, 'column', attribute.attacl
    FROM pg_catalog.pg_attribute attribute
    JOIN pg_catalog.pg_class relation ON relation.oid = attribute.attrelid
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
   WHERE namespace.nspname = 'information_schema'
     AND attribute.attnum > 0 AND NOT attribute.attisdropped
  UNION ALL
  SELECT function_object.proowner, 'function',
         COALESCE(function_object.proacl, pg_catalog.acldefault('f'::"char", function_object.proowner))
    FROM pg_catalog.pg_proc function_object
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = function_object.pronamespace
   WHERE namespace.nspname = 'information_schema'
  UNION ALL
  SELECT type_object.typowner, 'type',
         COALESCE(type_object.typacl, pg_catalog.acldefault('T'::"char", type_object.typowner))
    FROM pg_catalog.pg_type type_object
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = type_object.typnamespace
   WHERE namespace.nspname = 'information_schema'
), privileges AS (
  SELECT object.owner_oid, object.object_kind, acl.*
    FROM information_schema_acl object
    CROSS JOIN LATERAL pg_catalog.aclexplode(object.acl) acl
)
SELECT count(*)
  FROM privileges
 WHERE grantee NOT IN (0, owner_oid)
    OR (grantee = 0 AND (
      is_grantable OR
      (object_kind = 'schema' AND privilege_type <> 'USAGE') OR
      (object_kind IN ('relation', 'column') AND privilege_type <> 'SELECT') OR
      (object_kind = 'function' AND privilege_type <> 'EXECUTE') OR
      (object_kind = 'type' AND privilege_type <> 'USAGE')
    ))`).Scan(&informationSchemaEscalations).Error; err != nil {
		return fmt.Errorf("Relay information schema ACL baseline could not be inspected: %w", err)
	}
	if informationSchemaEscalations != 0 {
		return errors.New("Relay information schema ACL exceeds the PostgreSQL 16 baseline")
	}
	return nil
}

func verifyRelayProtectedRoleDefaultACLs(db *gorm.DB, ownerRole, migrationRole, runtimeRole string) error {
	var count int64
	if err := db.Raw(`
WITH protected AS (
  SELECT role.oid FROM pg_catalog.pg_roles role WHERE role.rolname IN (?, ?, ?, 'relay_download_edge')
)
SELECT count(*)
  FROM pg_catalog.pg_default_acl default_acl
 WHERE default_acl.defaclrole IN (SELECT oid FROM protected)
    OR EXISTS (
      SELECT 1 FROM pg_catalog.aclexplode(default_acl.defaclacl) acl
       WHERE acl.grantee IN (SELECT oid FROM protected)
    )`, ownerRole, migrationRole, runtimeRole).Scan(&count).Error; err != nil {
		return errors.New("Relay protected role default ACLs could not be inspected")
	}
	if count != 0 {
		return errors.New("Relay protected role default ACL surface is not empty")
	}
	return nil
}

func verifyRelayProtectedRoleTablespaces(db *gorm.DB, ownerRole, migrationRole, runtimeRole string) error {
	var value struct {
		ProtectedCount int64 `gorm:"column:protected_count"`
		DatabaseExact  bool  `gorm:"column:database_exact"`
		RelationsExact bool  `gorm:"column:relations_exact"`
	}
	if err := db.Raw(`
WITH protected AS (
  SELECT role.oid, role.rolname
    FROM pg_catalog.pg_roles role WHERE role.rolname IN (?, ?, ?, 'relay_download_edge')
), public_toast_relations AS (
  SELECT relation.reltoastrelid AS oid
    FROM pg_catalog.pg_class relation
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
   WHERE namespace.nspname = 'public' AND relation.reltoastrelid <> 0
), application_storage_relations AS (
  SELECT relation.oid
    FROM pg_catalog.pg_class relation
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
   WHERE namespace.nspname = 'public'
  UNION
  SELECT oid FROM public_toast_relations
  UNION
  SELECT index_object.indexrelid
    FROM pg_catalog.pg_index index_object
   WHERE index_object.indrelid IN (SELECT oid FROM public_toast_relations)
)
SELECT (SELECT count(*)
          FROM protected CROSS JOIN pg_catalog.pg_tablespace tablespace
         WHERE tablespace.spcowner = protected.oid
            OR pg_catalog.has_tablespace_privilege(protected.rolname, tablespace.oid, 'CREATE')) AS protected_count,
       (SELECT count(*) = 1
          FROM pg_catalog.pg_database database
          JOIN pg_catalog.pg_tablespace tablespace ON tablespace.oid = database.dattablespace
         WHERE database.datname = pg_catalog.current_database()
           AND tablespace.spcname = 'pg_default') AS database_exact,
       NOT EXISTS (
         SELECT 1
           FROM pg_catalog.pg_class relation
          WHERE relation.oid IN (SELECT oid FROM application_storage_relations)
            AND relation.reltablespace <> 0
       ) AS relations_exact`,
		ownerRole, migrationRole, runtimeRole).Scan(&value).Error; err != nil {
		return errors.New("Relay protected role tablespace surface could not be inspected")
	}
	if value.ProtectedCount != 0 || !value.DatabaseExact || !value.RelationsExact {
		return errors.New("Relay protected role or database tablespace surface is not exact")
	}
	return nil
}

func verifyRelayProtectedRoleSharedDependencies(
	db *gorm.DB,
	ownerRole, migrationRole, runtimeRole string,
	exact bool,
) error {
	var value struct {
		Unexpected int64 `gorm:"column:unexpected"`
		Allowed    int64 `gorm:"column:allowed"`
	}
	if err := db.Raw(`
WITH target AS (
  SELECT oid FROM pg_catalog.pg_database WHERE datname = pg_catalog.current_database()
), protected AS (
  SELECT role.oid, role.rolname
    FROM pg_catalog.pg_roles role WHERE role.rolname IN (?, ?, ?, 'relay_download_edge')
), dependencies AS (
  SELECT dependency.*, protected.rolname
    FROM pg_catalog.pg_shdepend dependency
    JOIN protected ON protected.oid = dependency.refobjid
   WHERE dependency.refclassid = 'pg_catalog.pg_authid'::regclass
), classified AS (
  SELECT dependencies.*,
         dependencies.dbid = 0
           AND dependencies.classid = 'pg_catalog.pg_database'::regclass
           AND dependencies.objid = target.oid
           AND dependencies.objsubid = 0
           AND dependencies.deptype = 'a'
           AND dependencies.rolname IN (?, ?, 'relay_download_edge') AS allowed_database_connect,
         dependencies.dbid = 0
           AND dependencies.classid = 'pg_catalog.pg_parameter_acl'::regclass
           AND dependencies.deptype = 'a' AS repairable_parameter_acl,
         dependencies.dbid <> 0 AND dependencies.dbid <> target.oid AS cross_database
    FROM dependencies CROSS JOIN target
)
SELECT count(*) FILTER (
         WHERE cross_database OR
           (dbid = 0 AND NOT allowed_database_connect AND NOT (NOT ? AND repairable_parameter_acl))
       ) AS unexpected,
       count(*) FILTER (WHERE allowed_database_connect) AS allowed
  FROM classified`, ownerRole, migrationRole, runtimeRole, migrationRole, runtimeRole, exact).Scan(&value).Error; err != nil {
		return errors.New("Relay protected role shared dependencies could not be inspected")
	}
	if value.Unexpected != 0 || (exact && value.Allowed != 3) {
		return errors.New("Relay protected role shared dependency surface is not exact")
	}
	return nil
}

func verifyRelayPublicDatabaseAndSchemaACLs(db *gorm.DB, ownerRole, migrationRole, runtimeRole string) error {
	var exact bool
	if err := db.Raw(`
WITH roles AS (
  SELECT owner.oid AS owner_oid, migrator.oid AS migrator_oid,
         runtime.oid AS runtime_oid, edge.oid AS edge_oid
    FROM pg_catalog.pg_roles owner
    CROSS JOIN pg_catalog.pg_roles migrator
    CROSS JOIN pg_catalog.pg_roles runtime
    CROSS JOIN pg_catalog.pg_roles edge
   WHERE owner.rolname = ? AND migrator.rolname = ?
     AND runtime.rolname = ? AND edge.rolname = 'relay_download_edge'
), target_database AS (
  SELECT database.oid, database.datdba, database.datacl
    FROM pg_catalog.pg_database database WHERE database.datname = pg_catalog.current_database()
), application_schema AS (
  SELECT namespace.oid, namespace.nspowner, namespace.nspacl
    FROM pg_catalog.pg_namespace namespace WHERE namespace.nspname = 'public'
), actual_database AS (
  SELECT acl.grantee, acl.privilege_type, acl.is_grantable
    FROM target_database
    CROSS JOIN LATERAL pg_catalog.aclexplode(
      COALESCE(target_database.datacl, pg_catalog.acldefault('d'::"char", target_database.datdba))
    ) acl
), expected_database AS (
  SELECT target_database.datdba AS grantee, privilege_type, false AS is_grantable
    FROM target_database CROSS JOIN (VALUES ('CONNECT'), ('CREATE'), ('TEMPORARY')) privilege(privilege_type)
  UNION ALL SELECT roles.migrator_oid, 'CONNECT', false FROM roles
  UNION ALL SELECT roles.runtime_oid, 'CONNECT', false FROM roles
  UNION ALL SELECT roles.edge_oid, 'CONNECT', false FROM roles
), actual_schema AS (
  SELECT acl.grantee, acl.privilege_type, acl.is_grantable
    FROM application_schema
    CROSS JOIN LATERAL pg_catalog.aclexplode(
      COALESCE(application_schema.nspacl, pg_catalog.acldefault('n'::"char", application_schema.nspowner))
    ) acl
), expected_schema AS (
  SELECT roles.owner_oid AS grantee, privilege_type, false AS is_grantable
    FROM roles CROSS JOIN (VALUES ('CREATE'), ('USAGE')) privilege(privilege_type)
  UNION ALL SELECT roles.migrator_oid, 'USAGE', false FROM roles
  UNION ALL SELECT roles.runtime_oid, 'USAGE', false FROM roles
  UNION ALL SELECT roles.edge_oid, 'USAGE', false FROM roles
), database_extra AS (
  SELECT * FROM actual_database EXCEPT SELECT * FROM expected_database
), database_missing AS (
  SELECT * FROM expected_database EXCEPT SELECT * FROM actual_database
), schema_extra AS (
  SELECT * FROM actual_schema EXCEPT SELECT * FROM expected_schema
), schema_missing AS (
  SELECT * FROM expected_schema EXCEPT SELECT * FROM actual_schema
)
SELECT (SELECT count(*) FROM roles) = 1
   AND (SELECT count(*) FROM target_database) = 1
   AND (SELECT count(*) FROM application_schema) = 1
   AND (SELECT count(*) FROM database_extra) = 0
   AND (SELECT count(*) FROM database_missing) = 0
   AND (SELECT count(*) FROM schema_extra) = 0
   AND (SELECT count(*) FROM schema_missing) = 0 AS exact`, ownerRole, migrationRole, runtimeRole).Scan(&exact).Error; err != nil {
		return errors.New("Relay public database and schema ACLs could not be inspected")
	}
	if !exact {
		return errors.New("Relay public database or schema ACL surface is not exact")
	}
	return nil
}

func GetRelayRuntimeDatabaseRoleStatus(db *gorm.DB) (RelayRuntimeDatabaseRoleStatus, error) {
	status := RelayRuntimeDatabaseRoleStatus{Required: RelayDatabaseRoleAttestationRequired(), State: "healthy"}
	if !status.Required {
		return status, nil
	}
	if err := VerifyRelayDatabaseTLS(db); err != nil {
		return status, err
	}
	if db == nil || db.Dialector.Name() != "postgres" {
		status.State = "unavailable"
		return status, errors.New("production Relay runtime requires PostgreSQL")
	}
	expected := strings.TrimSpace(os.Getenv(relayRuntimeDatabaseRoleEnvironment))
	ownerRole := strings.TrimSpace(os.Getenv(relaySchemaOwnerRoleEnvironment))
	migrationRole := strings.TrimSpace(os.Getenv(relayMigrationDatabaseRoleEnvironment))
	if !databaseRoleNamePattern.MatchString(expected) || !databaseRoleNamePattern.MatchString(ownerRole) ||
		!databaseRoleNamePattern.MatchString(migrationRole) || ownerRole == expected ||
		migrationRole == expected || ownerRole == migrationRole {
		status.State = "unavailable"
		return status, errors.New("Relay database role topology configuration is invalid")
	}
	// Readiness first proves the cluster/database catalog surface. No
	// role-specific DML or schema query is trusted before this read-only gate.
	if err := verifyRelayProtectedDatabaseSurfacePreflight(db, ownerRole, migrationRole, expected, true); err != nil {
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
	if value.SessionUser != expected || value.CurrentUser != expected || !value.CanLogin || !value.SafeSearchPath || value.Superuser || value.BypassRLS ||
		value.CreateDatabase || value.CreateRole || value.Replication || value.CanCreateDatabase ||
		value.CanCreatePublicSchema || value.CanCreateTemporary ||
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
	if err := verifyRelayDatabaseRoleTopology(db, ownerRole, migrationRole, expected); err != nil {
		status.State = "unavailable"
		return status, err
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
	schemaStatus, err := RequireRelaySchemaCurrent(db)
	if err != nil {
		status.State = "unavailable"
		return status, err
	}
	if err := verifyRelayRuntimeDatabasePrivilegeManifest(db, expected, schemaStatus.CurrentVersion); err != nil {
		status.State = "unavailable"
		return status, err
	}
	// API readiness is the last deployment gate after the post-provisioner.
	// The migrator deliberately accepts the exact A/B/C edge states for
	// idempotent recovery, but a serving runtime must observe only deployed A:
	// LOGIN plus the exact versioned edge privilege manifest.
	if err := verifyRelayDownloadEdgeCurrentDatabaseRole(db, schemaStatus.CurrentVersion, true); err != nil {
		status.State = "unavailable"
		return status, errors.New("Relay download edge database role is not finalized")
	}
	return status, nil
}

func VerifyRelayRuntimeDatabaseRole(db *gorm.DB) error {
	_, err := GetRelayRuntimeDatabaseRoleStatus(db)
	return err
}

func verifyRelayMigrationDatabaseRole(db *gorm.DB) error {
	if !RelayDatabaseRoleAttestationRequired() {
		return nil
	}
	if err := VerifyRelayDatabaseTLS(db); err != nil {
		return err
	}
	migrationRole := strings.TrimSpace(os.Getenv(relayMigrationDatabaseRoleEnvironment))
	ownerRole := strings.TrimSpace(os.Getenv(relaySchemaOwnerRoleEnvironment))
	runtimeRole := strings.TrimSpace(os.Getenv(relayRuntimeDatabaseRoleEnvironment))
	if !databaseRoleNamePattern.MatchString(migrationRole) || !databaseRoleNamePattern.MatchString(ownerRole) ||
		!databaseRoleNamePattern.MatchString(runtimeRole) || migrationRole == ownerRole || migrationRole == runtimeRole || ownerRole == runtimeRole {
		return errors.New("Relay database role separation is invalid")
	}
	if db == nil || db.Dialector.Name() != "postgres" {
		return errors.New("production Relay migration requires PostgreSQL")
	}
	if err := verifyRelayProtectedDatabaseSurfacePreflight(db, ownerRole, migrationRole, runtimeRole, true); err != nil {
		return err
	}
	var value struct {
		SessionUser                   string `gorm:"column:session_user"`
		CurrentUser                   string `gorm:"column:current_user"`
		SessionSuperuser              bool   `gorm:"column:session_superuser"`
		SessionBypassRLS              bool   `gorm:"column:session_bypass_rls"`
		SessionCreateDatabase         bool   `gorm:"column:session_create_database"`
		SessionCreateRole             bool   `gorm:"column:session_create_role"`
		SessionReplication            bool   `gorm:"column:session_replication"`
		SessionInherits               bool   `gorm:"column:session_inherits"`
		SessionCanLogin               bool   `gorm:"column:session_can_login"`
		SafeSearchPath                bool   `gorm:"column:safe_search_path"`
		SessionCanCreateDatabase      bool   `gorm:"column:session_can_create_database"`
		SessionCanCreateTemporary     bool   `gorm:"column:session_can_create_temporary"`
		SessionCanCreatePublicSchema  bool   `gorm:"column:session_can_create_public_schema"`
		SessionOwnsApplicationObject  bool   `gorm:"column:session_owns_application_object"`
		SessionCanTruncateApplication bool   `gorm:"column:session_can_truncate_application"`
		OwnerSuperuser                bool   `gorm:"column:owner_superuser"`
		OwnerBypassRLS                bool   `gorm:"column:owner_bypass_rls"`
		OwnerCreateDatabase           bool   `gorm:"column:owner_create_database"`
		OwnerCreateRole               bool   `gorm:"column:owner_create_role"`
		OwnerReplication              bool   `gorm:"column:owner_replication"`
		OwnerCanLogin                 bool   `gorm:"column:owner_can_login"`
		OwnerCanCreateDatabase        bool   `gorm:"column:owner_can_create_database"`
		OwnerCanCreateTemporary       bool   `gorm:"column:owner_can_create_temporary"`
		OwnerCanCreatePublicSchema    bool   `gorm:"column:owner_can_create_public_schema"`
		OwnerCanCreateOtherSchema     bool   `gorm:"column:owner_can_create_other_schema"`
		OwnerOwnsOtherApplication     bool   `gorm:"column:owner_owns_other_application"`
		SessionCanAssumeOwner         bool   `gorm:"column:session_can_assume_owner"`
		SessionCanAssumeDangerousRole bool   `gorm:"column:session_can_assume_dangerous_role"`
		OwnerCanAssumeDangerousRole   bool   `gorm:"column:owner_can_assume_dangerous_role"`
	}
	result := db.Raw(`
SELECT session_user AS session_user,
       current_user AS current_user,
	   session_role.rolsuper AS session_superuser,
	   session_role.rolbypassrls AS session_bypass_rls,
	   session_role.rolcreatedb AS session_create_database,
	   session_role.rolcreaterole AS session_create_role,
	   session_role.rolreplication AS session_replication,
	   session_role.rolinherit AS session_inherits,
	   session_role.rolcanlogin AS session_can_login,
	   current_setting('search_path') = 'public' AND
	   current_schema() = 'public' AND
	   current_schemas(true) = ARRAY['pg_catalog', 'public']::name[] AS safe_search_path,
	   has_database_privilege(session_role.rolname, current_database(), 'CREATE') AS session_can_create_database,
	   has_database_privilege(session_role.rolname, current_database(), 'TEMP') AS session_can_create_temporary,
	   EXISTS (
	     SELECT 1 FROM pg_namespace n
	      WHERE n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'
	        AND has_schema_privilege(session_role.rolname, n.oid, 'CREATE')
	   ) AS session_can_create_public_schema,
	   EXISTS (
	     SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
	      WHERE n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%' AND c.relowner = session_role.oid
	   ) OR EXISTS (
	     SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
	      WHERE n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%' AND p.proowner = session_role.oid
	   ) OR EXISTS (
	     SELECT 1 FROM pg_namespace n
	      WHERE n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%' AND n.nspowner = session_role.oid
	   ) AS session_owns_application_object,
	   EXISTS (
	     SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
	      WHERE n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%' AND c.relkind IN ('r', 'p')
	        AND has_table_privilege(session_role.rolname, c.oid, 'TRUNCATE')
	   ) AS session_can_truncate_application,
	   owner_role.rolsuper AS owner_superuser,
	   owner_role.rolbypassrls AS owner_bypass_rls,
	   owner_role.rolcreatedb AS owner_create_database,
	   owner_role.rolcreaterole AS owner_create_role,
	   owner_role.rolreplication AS owner_replication,
	   owner_role.rolcanlogin AS owner_can_login,
	   has_database_privilege(owner_role.rolname, current_database(), 'CREATE') AS owner_can_create_database,
	   has_database_privilege(owner_role.rolname, current_database(), 'TEMP') AS owner_can_create_temporary,
	   has_schema_privilege(owner_role.rolname, 'public', 'CREATE') AS owner_can_create_public_schema,
	   EXISTS (
	     SELECT 1 FROM pg_namespace n
	      WHERE n.nspname <> 'public' AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'
	        AND has_schema_privilege(owner_role.rolname, n.oid, 'CREATE')
	   ) AS owner_can_create_other_schema,
	   EXISTS (
	     SELECT 1 FROM pg_namespace n
	      WHERE n.nspname <> 'public' AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'
	        AND n.nspowner = owner_role.oid
	   ) OR EXISTS (
	     SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
	      WHERE n.nspname <> 'public' AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'
	        AND c.relowner = owner_role.oid
	   ) OR EXISTS (
	     SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
	      WHERE n.nspname <> 'public' AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'
	        AND p.proowner = owner_role.oid
	   ) OR EXISTS (
	     SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
	      WHERE n.nspname <> 'public' AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'
	        AND t.typowner = owner_role.oid
	   ) AS owner_owns_other_application,
	   pg_has_role(session_role.rolname, owner_role.oid, 'MEMBER') AS session_can_assume_owner,
	   EXISTS (
	     SELECT 1 FROM pg_roles assumed
	      WHERE assumed.oid NOT IN (session_role.oid, owner_role.oid)
	        AND pg_has_role(session_role.rolname, assumed.oid, 'MEMBER')
	   ) AS session_can_assume_dangerous_role,
	   EXISTS (
	     SELECT 1 FROM pg_roles assumed
	      WHERE assumed.oid <> owner_role.oid
	        AND pg_has_role(owner_role.rolname, assumed.oid, 'MEMBER')
	   ) AS owner_can_assume_dangerous_role
FROM pg_roles session_role
CROSS JOIN pg_roles owner_role
WHERE session_role.rolname = session_user AND owner_role.rolname = current_user`).Scan(&value)
	if result.Error != nil || result.RowsAffected != 1 {
		return errors.New("Relay migration database role could not be attested")
	}
	if value.SessionUser != migrationRole || value.CurrentUser != ownerRole || value.SessionSuperuser ||
		value.SessionBypassRLS || value.SessionCreateDatabase || value.SessionCreateRole || value.SessionReplication ||
		value.SessionInherits || !value.SessionCanLogin || !value.SafeSearchPath || value.SessionCanCreateDatabase || value.SessionCanCreateTemporary ||
		value.SessionCanCreatePublicSchema || value.SessionOwnsApplicationObject || value.SessionCanTruncateApplication ||
		value.OwnerSuperuser || value.OwnerBypassRLS ||
		value.OwnerCreateDatabase || value.OwnerCreateRole || value.OwnerReplication || value.OwnerCanLogin ||
		value.OwnerCanCreateDatabase || value.OwnerCanCreateTemporary || !value.OwnerCanCreatePublicSchema || value.OwnerCanCreateOtherSchema ||
		value.OwnerOwnsOtherApplication ||
		!value.SessionCanAssumeOwner || value.SessionCanAssumeDangerousRole || value.OwnerCanAssumeDangerousRole {
		return errors.New("Relay migration database role is not the isolated schema owner session")
	}
	ownsForbiddenObject, err := relayRoleOwnsForbiddenDatabaseObject(db, migrationRole)
	if err != nil {
		return err
	}
	if ownsForbiddenObject {
		return errors.New("Relay migration login owns a forbidden database object")
	}
	if err := verifyRelayDatabaseRoleTopology(db, ownerRole, migrationRole, runtimeRole); err != nil {
		return err
	}
	return nil
}

func verifyRelayDatabaseRoleTopology(db *gorm.DB, ownerRole string, migrationRole string, runtimeRole string) error {
	var topology struct {
		PublicOwnerExact       bool  `gorm:"column:public_owner_exact"`
		OwnerCanCreate         bool  `gorm:"column:owner_can_create"`
		MigratorCannotCreate   bool  `gorm:"column:migrator_cannot_create"`
		RuntimeCannotCreate    bool  `gorm:"column:runtime_cannot_create"`
		UnexpectedCreateACLs   int64 `gorm:"column:unexpected_create_acls"`
		EffectiveOwnerMembers  int64 `gorm:"column:effective_owner_members"`
		ExpectedDirectMember   bool  `gorm:"column:expected_direct_member"`
		ExpectedAdminOption    bool  `gorm:"column:expected_admin_option"`
		ExpectedInheritOption  bool  `gorm:"column:expected_inherit_option"`
		ExpectedSetOption      bool  `gorm:"column:expected_set_option"`
		OwnerSafeAttributes    bool  `gorm:"column:owner_safe_attributes"`
		MigratorSafeAttributes bool  `gorm:"column:migrator_safe_attributes"`
		OwnerMembershipCount   int64 `gorm:"column:owner_membership_count"`
		MigratorMemberships    int64 `gorm:"column:migrator_membership_count"`
		MigratorMemberCount    int64 `gorm:"column:migrator_member_count"`
		RuntimeMemberCount     int64 `gorm:"column:runtime_member_count"`
	}
	result := db.Raw(`
WITH RECURSIVE roles AS (
  SELECT owner.oid AS owner_oid, migrator.oid AS migrator_oid, runtime.oid AS runtime_oid
  FROM pg_roles owner CROSS JOIN pg_roles migrator CROSS JOIN pg_roles runtime
  WHERE owner.rolname = ? AND migrator.rolname = ? AND runtime.rolname = ?
), application_schema AS (
  SELECT namespace.oid, namespace.nspowner, namespace.nspacl
  FROM pg_namespace namespace WHERE namespace.nspname = 'public'
), owner_members AS (
  SELECT membership.member
    FROM pg_auth_members membership, roles
   WHERE membership.roleid = roles.owner_oid
  UNION
  SELECT membership.member
    FROM pg_auth_members membership
    JOIN owner_members parent ON membership.roleid = parent.member
)
SELECT application_schema.nspowner = roles.owner_oid AS public_owner_exact,
       has_schema_privilege(roles.owner_oid, application_schema.oid, 'CREATE') AS owner_can_create,
       NOT has_schema_privilege(roles.migrator_oid, application_schema.oid, 'CREATE') AS migrator_cannot_create,
       NOT has_schema_privilege(roles.runtime_oid, application_schema.oid, 'CREATE') AS runtime_cannot_create,
       (SELECT count(*) FROM aclexplode(COALESCE(application_schema.nspacl,
                                      acldefault('n', application_schema.nspowner))) acl
         WHERE acl.privilege_type = 'CREATE' AND acl.grantee <> roles.owner_oid) AS unexpected_create_acls,
       (SELECT count(DISTINCT member) FROM owner_members) AS effective_owner_members,
       EXISTS (SELECT 1 FROM pg_auth_members membership
                WHERE membership.roleid = roles.owner_oid AND membership.member = roles.migrator_oid) AS expected_direct_member,
       COALESCE((SELECT membership.admin_option FROM pg_auth_members membership
                  WHERE membership.roleid = roles.owner_oid AND membership.member = roles.migrator_oid), false) AS expected_admin_option,
	   COALESCE((SELECT membership.inherit_option FROM pg_auth_members membership
	              WHERE membership.roleid = roles.owner_oid AND membership.member = roles.migrator_oid), true) AS expected_inherit_option,
       COALESCE((SELECT membership.set_option FROM pg_auth_members membership
                  WHERE membership.roleid = roles.owner_oid AND membership.member = roles.migrator_oid), false) AS expected_set_option,
	   NOT owner.rolsuper AND NOT owner.rolbypassrls AND NOT owner.rolcreatedb
	     AND NOT owner.rolcreaterole AND NOT owner.rolreplication AND NOT owner.rolcanlogin
	     AND NOT owner.rolinherit
	     AND NOT has_database_privilege(owner.rolname, current_database(), 'CREATE')
	     AND NOT has_database_privilege(owner.rolname, current_database(), 'TEMP')
	     AND NOT EXISTS (
	       SELECT 1 FROM pg_namespace namespace
	        WHERE namespace.nspname <> 'public' AND namespace.nspname <> 'information_schema'
	          AND namespace.nspname NOT LIKE 'pg_%'
	          AND has_schema_privilege(owner.rolname, namespace.oid, 'CREATE')
	     ) AS owner_safe_attributes,
	   NOT migrator.rolsuper AND NOT migrator.rolbypassrls AND NOT migrator.rolcreatedb
	     AND NOT migrator.rolcreaterole AND NOT migrator.rolreplication AND migrator.rolcanlogin
	     AND NOT migrator.rolinherit
	     AND NOT has_database_privilege(migrator.rolname, current_database(), 'CREATE')
	     AND NOT has_database_privilege(migrator.rolname, current_database(), 'TEMP')
	     AND NOT EXISTS (
	       SELECT 1 FROM pg_namespace namespace
	        WHERE namespace.nspname <> 'information_schema' AND namespace.nspname NOT LIKE 'pg_%'
	          AND has_schema_privilege(migrator.rolname, namespace.oid, 'CREATE')
	     ) AS migrator_safe_attributes,
	   (SELECT count(*) FROM pg_auth_members membership WHERE membership.member = roles.owner_oid) AS owner_membership_count,
	   (SELECT count(*) FROM pg_auth_members membership WHERE membership.member = roles.migrator_oid) AS migrator_membership_count,
	   (SELECT count(*) FROM pg_auth_members membership WHERE membership.roleid = roles.migrator_oid) AS migrator_member_count,
	   (SELECT count(*) FROM pg_auth_members membership WHERE membership.roleid = roles.runtime_oid) AS runtime_member_count
FROM roles CROSS JOIN application_schema
CROSS JOIN pg_roles owner CROSS JOIN pg_roles migrator
WHERE owner.oid = roles.owner_oid AND migrator.oid = roles.migrator_oid`, ownerRole, migrationRole, runtimeRole).Scan(&topology)
	if result.Error != nil || result.RowsAffected != 1 {
		return errors.New("Relay database role topology could not be inspected")
	}
	if !topology.PublicOwnerExact || !topology.OwnerCanCreate || !topology.MigratorCannotCreate ||
		!topology.RuntimeCannotCreate || topology.UnexpectedCreateACLs != 0 ||
		topology.EffectiveOwnerMembers != 1 || !topology.ExpectedDirectMember ||
		topology.ExpectedAdminOption || topology.ExpectedInheritOption || !topology.ExpectedSetOption ||
		!topology.OwnerSafeAttributes || !topology.MigratorSafeAttributes || topology.OwnerMembershipCount != 0 ||
		topology.MigratorMemberships != 1 || topology.MigratorMemberCount != 0 || topology.RuntimeMemberCount != 0 {
		return errors.New("Relay database role topology is not isolated")
	}
	if err := verifyRelayApplicationObjectOwnership(db, ownerRole); err != nil {
		return err
	}
	if err := verifyRelaySchemaOwnerScope(db, ownerRole); err != nil {
		return err
	}
	if err := verifyRelayProtectedRoleSettings(db, ownerRole, migrationRole, runtimeRole); err != nil {
		return err
	}
	if err := verifyRelayProtectedRoleClusterBinding(db, ownerRole, migrationRole, runtimeRole); err != nil {
		return err
	}
	if err := verifyRelayProtectedRoleParameterPrivileges(db, ownerRole, migrationRole, runtimeRole, relayDownloadEdgeDatabaseRoleName); err != nil {
		return err
	}
	return verifyRelayProtectedDatabaseSurfacePreflight(db, ownerRole, migrationRole, runtimeRole, true)
}

// PostgreSQL roles are cluster-global. Secure Relay therefore requires a
// dedicated cluster/instance whose other databases grant none of CONNECT,
// CREATE or TEMP to any protected role. The immutable role comments bind the
// names to one database OID so an unrelated pre-existing role is never silently
// taken over. pg_shdepend detects ownership and ACL references in databases
// that cannot be inspected through the current connection.
func verifyRelayProtectedRoleClusterBinding(db *gorm.DB, ownerRole, migrationRole, runtimeRole string) error {
	var value struct {
		BindingsExact     bool `gorm:"column:bindings_exact"`
		NoDatabaseOwner   bool `gorm:"column:no_database_owner"`
		NoExternalAccess  bool `gorm:"column:no_external_access"`
		NoExternalObjects bool `gorm:"column:no_external_objects"`
	}
	if err := db.Raw(`
WITH target AS (
  SELECT oid, datname FROM pg_database WHERE datname = current_database()
), expected(role_name, role_kind) AS (
  VALUES (?, 'owner'), (?, 'migrator'), (?, 'runtime'), ('relay_download_edge', 'download-edge')
), protected AS (
  SELECT role.oid, role.rolname, expected.role_kind,
         pg_catalog.shobj_description(role.oid, 'pg_authid') AS binding
    FROM expected LEFT JOIN pg_roles role ON role.rolname = expected.role_name
)
SELECT (SELECT count(protected.oid) = 4 AND bool_and(
         protected.binding = 'ai-video-relay-role/v1;database=' || target.datname ||
           ';database_oid=' || target.oid::text || ';kind=' || protected.role_kind
       ) FROM protected CROSS JOIN target) AS bindings_exact,
       NOT EXISTS (
         SELECT 1 FROM protected JOIN pg_database database ON database.datdba = protected.oid
       ) AS no_database_owner,
       NOT EXISTS (
         SELECT 1 FROM protected CROSS JOIN pg_database database CROSS JOIN target
          WHERE database.oid <> target.oid
            AND (has_database_privilege(protected.rolname, database.oid, 'CONNECT')
              OR has_database_privilege(protected.rolname, database.oid, 'CREATE')
              OR has_database_privilege(protected.rolname, database.oid, 'TEMP'))
       ) AS no_external_access,
       NOT EXISTS (
         SELECT 1 FROM pg_shdepend dependency
         JOIN protected ON protected.oid = dependency.refobjid
         CROSS JOIN target
          WHERE dependency.refclassid = 'pg_authid'::regclass
            AND dependency.dbid <> 0 AND dependency.dbid <> target.oid
	       ) AS no_external_objects`, ownerRole, migrationRole, runtimeRole).Scan(&value).Error; err != nil {
		return fmt.Errorf("Relay protected role cluster binding could not be inspected: %w", err)
	}
	if !value.BindingsExact {
		return errors.New("Relay protected role database bindings are not exact")
	}
	if !value.NoDatabaseOwner {
		return errors.New("Relay protected role owns a database")
	}
	if !value.NoExternalAccess {
		return errors.New("Relay protected role can access another database")
	}
	if !value.NoExternalObjects {
		return errors.New("Relay protected role has a cross-database dependency")
	}
	return nil
}

func verifyRelayProtectedRoleSettings(db *gorm.DB, ownerRole, migrationRole, runtimeRole string) error {
	var exact bool
	if err := db.Raw(`
WITH expected(role_name, can_login, connection_limit, settings) AS (
  VALUES
    (?, false, -1, ARRAY['auto_explain.log_parameter_max_length=0', 'log_parameter_max_length=0', 'log_parameter_max_length_on_error=0', 'pgaudit.log_parameter=off', 'search_path=public']::text[]),
    (?, true, 8, ARRAY['auto_explain.log_parameter_max_length=0', 'log_parameter_max_length=0', 'log_parameter_max_length_on_error=0', 'pgaudit.log_parameter=off', 'search_path=public']::text[]),
    (?, true, 256, ARRAY['auto_explain.log_parameter_max_length=0', 'log_parameter_max_length=0', 'log_parameter_max_length_on_error=0', 'pgaudit.log_parameter=off', 'row_security=on', 'search_path=public']::text[]),
    ('relay_download_edge', NULL::boolean, 64, ARRAY['auto_explain.log_parameter_max_length=0', 'log_parameter_max_length=0', 'log_parameter_max_length_on_error=0', 'pgaudit.log_parameter=off', 'row_security=on', 'search_path=public']::text[])
), inspected AS (
  SELECT expected.role_name,
         role.rolname IS NOT NULL
           AND (expected.can_login IS NULL OR role.rolcanlogin = expected.can_login)
           AND role.rolconnlimit = expected.connection_limit
           AND (role.rolvaliduntil IS NULL OR role.rolvaliduntil = 'infinity'::timestamptz)
           AND COALESCE((SELECT array_agg(value ORDER BY value) FROM unnest(role.rolconfig) value), ARRAY[]::text[]) = expected.settings
           AND NOT EXISTS (
             SELECT 1 FROM pg_db_role_setting setting
              WHERE setting.setrole = role.oid AND setting.setdatabase <> 0
           ) AS role_exact
    FROM expected LEFT JOIN pg_roles role ON role.rolname = expected.role_name
)
SELECT count(*) = 4 AND bool_and(role_exact) AS exact FROM inspected`, ownerRole, migrationRole, runtimeRole).Scan(&exact).Error; err != nil {
		return errors.New("Relay protected database role settings could not be inspected")
	}
	if !exact {
		return errors.New("Relay protected database role settings are not exact")
	}
	if err := db.Raw(`SELECT current_setting('session_replication_role') = 'origin'
  AND NOT EXISTS (
    SELECT 1 FROM pg_db_role_setting setting
     JOIN pg_database database ON database.oid = setting.setdatabase
    WHERE database.datname = current_database() AND setting.setrole = 0
  )`).Scan(&exact).Error; err != nil {
		return errors.New("Relay database security defaults could not be inspected")
	}
	if !exact {
		return errors.New("Relay database security defaults are not exact")
	}
	return nil
}

func verifyRelayProtectedRoleParameterPrivileges(db *gorm.DB, roles ...string) error {
	if len(roles) == 0 {
		return errors.New("Relay protected role parameter manifest is empty")
	}
	placeholders := make([]string, 0, len(roles))
	arguments := make([]any, 0, len(roles))
	for _, role := range roles {
		if !databaseRoleNamePattern.MatchString(role) {
			return errors.New("Relay protected role parameter manifest is invalid")
		}
		placeholders = append(placeholders, "(?)")
		arguments = append(arguments, role)
	}
	var unexpected int64
	query := `WITH protected(role_name) AS (VALUES ` + strings.Join(placeholders, ",") + `)
SELECT count(*)
  FROM protected CROSS JOIN pg_catalog.pg_parameter_acl parameter
 WHERE pg_catalog.has_parameter_privilege(protected.role_name, parameter.parname, 'SET')
    OR pg_catalog.has_parameter_privilege(protected.role_name, parameter.parname, 'ALTER SYSTEM')`
	if err := db.Raw(query, arguments...).Scan(&unexpected).Error; err != nil {
		return errors.New("Relay protected role parameter privileges could not be inspected")
	}
	if unexpected != 0 {
		return errors.New("Relay protected role has database parameter privileges")
	}
	return nil
}

func verifyRelaySchemaOwnerScope(db *gorm.DB, ownerRole string) error {
	var outsideOwnerCount int64
	if err := db.Raw(`
WITH expected AS (SELECT oid FROM pg_roles WHERE rolname = ?), outside_objects AS (
  SELECT n.nspowner AS owner_oid FROM pg_namespace n
   WHERE n.nspname <> 'public' AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'
  UNION ALL
  SELECT c.relowner FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname <> 'public' AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'
  UNION ALL
  SELECT p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname <> 'public' AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'
  UNION ALL
  SELECT t.typowner FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
   WHERE n.nspname <> 'public' AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'
)
SELECT count(*) FROM outside_objects, expected WHERE outside_objects.owner_oid = expected.oid`, ownerRole).
		Scan(&outsideOwnerCount).Error; err != nil {
		return errors.New("Relay schema owner scope could not be inspected")
	}
	if outsideOwnerCount != 0 {
		return errors.New("Relay schema owner controls objects outside the application schema")
	}
	return nil
}

// verifyRelayApplicationObjectOwnership proves that the protected NOLOGIN
// owner is the only principal able to ALTER or DROP an application object.
// ACL checks alone are insufficient: an unexpected table, function, type,
// collation or extension owner retains DDL authority without CREATE privilege.
func verifyRelayApplicationObjectOwnership(db *gorm.DB, ownerRole string) error {
	var unexpectedOwnerCount int64
	if err := db.Raw(`
WITH expected AS (SELECT oid FROM pg_roles WHERE rolname = ?), application_objects AS (
  SELECT c.relowner AS owner_oid
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'public'
  UNION ALL
  SELECT p.proowner
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'public'
  UNION ALL
  SELECT t.typowner
    FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
   WHERE n.nspname = 'public'
  UNION ALL
  SELECT c.collowner
    FROM pg_collation c JOIN pg_namespace n ON n.oid = c.collnamespace
   WHERE n.nspname = 'public'
  UNION ALL
  SELECT c.conowner
    FROM pg_conversion c JOIN pg_namespace n ON n.oid = c.connamespace
   WHERE n.nspname = 'public'
  UNION ALL
  SELECT e.extowner
    FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
   WHERE n.nspname = 'public'
)
SELECT count(*)
  FROM application_objects, expected
 WHERE application_objects.owner_oid <> expected.oid`, ownerRole).Scan(&unexpectedOwnerCount).Error; err != nil {
		return errors.New("Relay application object ownership could not be inspected")
	}
	if unexpectedOwnerCount != 0 {
		return errors.New("Relay application object ownership is not isolated")
	}
	return nil
}

// relayRoleOwnsForbiddenDatabaseObject covers application DDL ownership that
// is not represented by ordinary relations/functions. PostgreSQL permits the
// owner of an enum, domain, collation or extension (among other object classes)
// to ALTER or DROP it even when table/schema ACLs look DML-only.
func relayRoleOwnsForbiddenDatabaseObject(db *gorm.DB, role string) (bool, error) {
	var owns bool
	err := db.Raw(`
WITH target AS (SELECT oid FROM pg_roles WHERE rolname = ?)
SELECT EXISTS (
  SELECT 1 FROM pg_namespace n, target WHERE n.nspowner = target.oid
    AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'
  UNION ALL
  SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, target
   WHERE c.relowner = target.oid AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'
  UNION ALL
  SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace, target
   WHERE p.proowner = target.oid AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'
  UNION ALL
  SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace, target
   WHERE t.typowner = target.oid AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'
     AND NOT EXISTS (SELECT 1 FROM pg_class c WHERE c.reltype = t.oid)
  UNION ALL
  SELECT 1 FROM pg_collation c JOIN pg_namespace n ON n.oid = c.collnamespace, target
   WHERE c.collowner = target.oid AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'
  UNION ALL
  SELECT 1 FROM pg_conversion c JOIN pg_namespace n ON n.oid = c.connamespace, target
   WHERE c.conowner = target.oid AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%'
  UNION ALL SELECT 1 FROM pg_extension e, target WHERE e.extowner = target.oid
  UNION ALL SELECT 1 FROM pg_foreign_data_wrapper f, target WHERE f.fdwowner = target.oid
  UNION ALL SELECT 1 FROM pg_foreign_server s, target WHERE s.srvowner = target.oid
  UNION ALL SELECT 1 FROM pg_largeobject_metadata l, target WHERE l.lomowner = target.oid
  UNION ALL SELECT 1 FROM pg_event_trigger e, target WHERE e.evtowner = target.oid
) AS owns_forbidden_object`, role).Scan(&owns).Error
	if err != nil {
		return false, errors.New("Relay database role object ownership could not be inspected")
	}
	return owns, nil
}

func VerifyRelayMigrationDatabaseRole(db *gorm.DB) error {
	return verifyRelayMigrationDatabaseRole(db)
}
