package model

import (
	"errors"
	"fmt"
	"strings"

	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

const relayDatabaseRoleTopologySQL = `
DO $roles$
DECLARE
  owner_name text := current_setting('relay.schema_owner_role');
  migrator_name text := current_setting('relay.migration_role');
  runtime_name text := current_setting('relay.runtime_role');
  edge_name text := current_setting('relay.edge_role');
  protected_roles text[] := ARRAY[owner_name, migrator_name, runtime_name, edge_name];
  membership record;
  protected_role text;
  role_database text;
  role_binding record;
  database_oid oid := (SELECT oid FROM pg_database WHERE datname = current_database());
BEGIN
  IF owner_name !~ '^[A-Za-z_][A-Za-z0-9_]{0,62}$'
     OR migrator_name !~ '^[A-Za-z_][A-Za-z0-9_]{0,62}$'
     OR runtime_name !~ '^[A-Za-z_][A-Za-z0-9_]{0,62}$'
     OR edge_name !~ '^[A-Za-z_][A-Za-z0-9_]{0,62}$'
     OR cardinality(ARRAY(SELECT DISTINCT unnest(protected_roles))) <> 4
     OR runtime_name <> 'relay_runtime'
     OR edge_name <> 'relay_download_edge' THEN
    RAISE EXCEPTION 'Relay database role names are invalid or overlap';
  END IF;
  FOR role_binding IN
    SELECT * FROM (VALUES
      (owner_name, 'owner'),
      (migrator_name, 'migrator'),
      (runtime_name, 'runtime'),
      (edge_name, 'download-edge')
    ) AS binding(role_name, role_kind)
  LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_binding.role_name) THEN
      EXECUTE format('CREATE ROLE %I', role_binding.role_name);
      EXECUTE format(
        'COMMENT ON ROLE %I IS %L',
        role_binding.role_name,
        'ai-video-relay-role/v1;database=' || current_database() ||
          ';database_oid=' || database_oid::text || ';kind=' || role_binding.role_kind
      );
    ELSIF (
      SELECT pg_catalog.shobj_description(role.oid, 'pg_authid')
        FROM pg_roles role WHERE role.rolname = role_binding.role_name
    ) IS DISTINCT FROM (
      'ai-video-relay-role/v1;database=' || current_database() ||
        ';database_oid=' || database_oid::text || ';kind=' || role_binding.role_kind
    ) THEN
      RAISE EXCEPTION 'Relay protected role is not bound to this database';
    END IF;
  END LOOP;

  -- Remove every inherited global and database-specific GUC before restoring
  -- the small, versioned allowlist below. Per-database settings override global
  -- settings and otherwise survive an ordinary role password rotation.
  FOREACH protected_role IN ARRAY protected_roles
  LOOP
    EXECUTE format('ALTER ROLE %I RESET ALL', protected_role);
    EXECUTE format('ALTER ROLE %I VALID UNTIL %L', protected_role, 'infinity');
    FOR role_database IN
      SELECT DISTINCT database.datname
        FROM pg_db_role_setting setting
        JOIN pg_roles role ON role.oid = setting.setrole
        JOIN pg_database database ON database.oid = setting.setdatabase
       WHERE role.rolname = protected_role
    LOOP
      EXECUTE format('ALTER ROLE %I IN DATABASE %I RESET ALL', protected_role, role_database);
    END LOOP;
  END LOOP;

  EXECUTE format(
    'ALTER ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD NULL CONNECTION LIMIT -1',
    owner_name
  );
  EXECUTE format(
    'ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 8',
    migrator_name
  );
  EXECUTE format(
    'ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 256',
    runtime_name
  );
  EXECUTE format(
    'ALTER ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD NULL CONNECTION LIMIT 64',
    edge_name
  );

  FOR membership IN
    SELECT parent.rolname AS parent_name, member.rolname AS member_name
      FROM pg_auth_members grant_row
      JOIN pg_roles parent ON parent.oid = grant_row.roleid
      JOIN pg_roles member ON member.oid = grant_row.member
     WHERE parent.rolname = ANY(protected_roles)
        OR member.rolname = ANY(protected_roles)
  LOOP
    EXECUTE format('REVOKE %I FROM %I', membership.parent_name, membership.member_name);
  END LOOP;
  EXECUTE format(
    'GRANT %I TO %I WITH ADMIN FALSE, INHERIT FALSE, SET TRUE',
    owner_name, migrator_name
  );
END
$roles$;`

const relayDatabaseRoleApplicationACLsSQL = `
DO $database_acl$
DECLARE
  owner_name text := current_setting('relay.schema_owner_role');
  migrator_name text := current_setting('relay.migration_role');
  runtime_name text := current_setting('relay.runtime_role');
  edge_name text := current_setting('relay.edge_role');
  protected_roles text[] := ARRAY[owner_name, migrator_name, runtime_name, edge_name];
  protected_role text;
  database_name text := current_database();
  owned_relation record;
  function_object record;
  standalone_type record;
  column_acl record;
  database_grantee record;
  schema_grantee record;
BEGIN
  EXECUTE format('ALTER DATABASE %I RESET ALL', database_name);
  FOR database_grantee IN
    SELECT acl.grantee, role.rolname
      FROM pg_database database
      CROSS JOIN LATERAL aclexplode(COALESCE(database.datacl, acldefault('d', database.datdba))) acl
      LEFT JOIN pg_roles role ON role.oid = acl.grantee
     WHERE database.datname = database_name AND acl.grantee <> database.datdba
     GROUP BY acl.grantee, role.rolname
  LOOP
    IF database_grantee.grantee = 0 THEN
      EXECUTE format('REVOKE ALL PRIVILEGES ON DATABASE %I FROM PUBLIC', database_name);
    ELSIF database_grantee.rolname IS NULL THEN
      RAISE EXCEPTION 'Relay database ACL contains an unknown grantee';
    ELSE
      EXECUTE format(
        'REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I', database_name, database_grantee.rolname
      );
    END IF;
  END LOOP;
  EXECUTE format(
    'GRANT CONNECT ON DATABASE %I TO %I, %I, %I',
    database_name, migrator_name, runtime_name, edge_name
  );

  EXECUTE format('ALTER SCHEMA public OWNER TO %I', owner_name);
  FOR schema_grantee IN
    SELECT acl.grantee, role.rolname
      FROM pg_namespace namespace
      CROSS JOIN LATERAL aclexplode(COALESCE(namespace.nspacl, acldefault('n', namespace.nspowner))) acl
      LEFT JOIN pg_roles role ON role.oid = acl.grantee
     WHERE namespace.nspname = 'public' AND acl.grantee <> namespace.nspowner
     GROUP BY acl.grantee, role.rolname
  LOOP
    IF schema_grantee.grantee = 0 THEN
      REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC;
    ELSIF schema_grantee.rolname IS NULL THEN
      RAISE EXCEPTION 'Relay public schema ACL contains an unknown grantee';
    ELSE
      EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA public FROM %I', schema_grantee.rolname);
    END IF;
  END LOOP;
  EXECUTE format('GRANT USAGE, CREATE ON SCHEMA public TO %I', owner_name);
  EXECUTE format('GRANT USAGE ON SCHEMA public TO %I, %I, %I', migrator_name, runtime_name, edge_name);

  FOR owned_relation IN
    SELECT namespace.nspname, object.relname, object.relkind
      FROM pg_class object
      JOIN pg_namespace namespace ON namespace.oid = object.relnamespace
     WHERE namespace.nspname = 'public'
       AND object.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
     ORDER BY CASE WHEN object.relkind = 'S' THEN 2 ELSE 1 END, object.relname
  LOOP
    EXECUTE CASE owned_relation.relkind
      WHEN 'S' THEN format('ALTER SEQUENCE %I.%I OWNER TO %I', owned_relation.nspname, owned_relation.relname, owner_name)
      WHEN 'v' THEN format('ALTER VIEW %I.%I OWNER TO %I', owned_relation.nspname, owned_relation.relname, owner_name)
      WHEN 'm' THEN format('ALTER MATERIALIZED VIEW %I.%I OWNER TO %I', owned_relation.nspname, owned_relation.relname, owner_name)
      WHEN 'f' THEN format('ALTER FOREIGN TABLE %I.%I OWNER TO %I', owned_relation.nspname, owned_relation.relname, owner_name)
      ELSE format('ALTER TABLE %I.%I OWNER TO %I', owned_relation.nspname, owned_relation.relname, owner_name)
    END;
  END LOOP;

  FOR function_object IN
    SELECT function.oid::regprocedure AS identity, function.prokind
      FROM pg_proc function
      JOIN pg_namespace namespace ON namespace.oid = function.pronamespace
     WHERE namespace.nspname = 'public'
  LOOP
    IF function_object.prokind = 'p' THEN
      EXECUTE format('ALTER PROCEDURE %s OWNER TO %I', function_object.identity, owner_name);
    ELSIF function_object.prokind <> 'a' THEN
      EXECUTE format('ALTER FUNCTION %s OWNER TO %I', function_object.identity, owner_name);
    END IF;
  END LOOP;

  FOR standalone_type IN
    SELECT namespace.nspname, type_object.typname
      FROM pg_type type_object
      JOIN pg_namespace namespace ON namespace.oid = type_object.typnamespace
     WHERE namespace.nspname = 'public'
       AND type_object.typrelid = 0
       AND type_object.typelem = 0
       AND type_object.typtype IN ('c', 'd', 'e', 'm', 'r')
  LOOP
    EXECUTE format('ALTER TYPE %I.%I OWNER TO %I', standalone_type.nspname, standalone_type.typname, owner_name);
  END LOOP;

  EXECUTE format('REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM %I', edge_name);
  EXECUTE format('REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM %I', edge_name);
  EXECUTE format('REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM %I', edge_name);
  REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
  FOR column_acl IN
    SELECT acl_relation.relname,
           string_agg(format('%I', attribute.attname), ', ' ORDER BY attribute.attnum) AS columns
      FROM pg_class acl_relation
      JOIN pg_namespace namespace ON namespace.oid = acl_relation.relnamespace
      JOIN pg_attribute attribute ON attribute.attrelid = acl_relation.oid
     WHERE namespace.nspname = 'public'
       AND acl_relation.relkind IN ('r', 'p', 'v', 'm', 'f')
       AND attribute.attnum > 0
       AND NOT attribute.attisdropped
     GROUP BY acl_relation.relname
  LOOP
    EXECUTE format(
      'REVOKE SELECT (%s), INSERT (%s), UPDATE (%s), REFERENCES (%s) ON TABLE public.%I FROM %I',
      column_acl.columns, column_acl.columns, column_acl.columns, column_acl.columns,
      column_acl.relname, edge_name
    );
  END LOOP;

  EXECUTE format('ALTER ROLE %I SET search_path = public', owner_name);
  EXECUTE format('ALTER ROLE %I SET search_path = public', migrator_name);
  EXECUTE format('ALTER ROLE %I SET search_path = public', runtime_name);
  EXECUTE format('ALTER ROLE %I SET search_path = public', edge_name);
	FOREACH protected_role IN ARRAY protected_roles LOOP
		EXECUTE format('ALTER ROLE %I SET auto_explain.log_parameter_max_length = 0', protected_role);
		EXECUTE format('ALTER ROLE %I SET log_parameter_max_length = 0', protected_role);
		EXECUTE format('ALTER ROLE %I SET log_parameter_max_length_on_error = 0', protected_role);
		EXECUTE format('ALTER ROLE %I SET pgaudit.log_parameter = off', protected_role);
	END LOOP;
  EXECUTE format('ALTER ROLE %I SET row_security = on', runtime_name);
  EXECUTE format('ALTER ROLE %I SET row_security = on', edge_name);
END
$database_acl$;`

const relayDatabaseSCRAMVerifierPattern = `^SCRAM-SHA-256\$4096:[A-Za-z0-9+/]+={0,2}\$[A-Za-z0-9+/]+={0,2}:[A-Za-z0-9+/]+={0,2}$`

// ProvisionRelayDatabaseRoles is the privileged, same-image predecessor to
// relay-migrate. It accepts only raw high-entropy base64url passwords, derives
// PostgreSQL SCRAM verifiers in memory, and performs topology, password, owner,
// ACL, and edge pre-stub changes under the schema lifecycle lock in one
// transaction. It never creates application schema objects.
func ProvisionRelayDatabaseRoles(db *gorm.DB, migrationPassword, runtimePassword, edgePassword []byte) error {
	return provisionRelayDatabaseRoles(db, migrationPassword, runtimePassword, edgePassword, "30s")
}

func provisionRelayDatabaseRoles(db *gorm.DB, migrationPassword, runtimePassword, edgePassword []byte, lockTimeout string) error {
	defer clear(migrationPassword)
	defer clear(runtimePassword)
	defer clear(edgePassword)
	if !RelayDatabaseRoleAttestationRequired() {
		return errors.New("Relay database role provisioning requires role attestation")
	}
	if db == nil || db.Dialector.Name() != "postgres" {
		return errors.New("Relay database role provisioning requires PostgreSQL")
	}
	if err := VerifyRelayDatabaseTLS(db); err != nil {
		return err
	}
	if strings.TrimSpace(lockTimeout) == "" || strings.ContainsAny(lockTimeout, "\x00\r\n'\"") {
		return errors.New("Relay database role provisioning lock timeout is invalid")
	}
	if err := ValidateDistinctRelayDatabasePasswords(migrationPassword, runtimePassword, edgePassword); err != nil {
		return err
	}
	migrationVerifier, err := GenerateRelaySCRAMSHA256Verifier(migrationPassword)
	if err != nil {
		return errors.New("Relay migration database password is invalid")
	}
	runtimeVerifier, err := GenerateRelaySCRAMSHA256Verifier(runtimePassword)
	if err != nil {
		return errors.New("Relay runtime database password is invalid")
	}
	edgeVerifier, err := GenerateRelaySCRAMSHA256Verifier(edgePassword)
	if err != nil {
		return errors.New("Relay download edge database password is invalid")
	}
	ownerRole := strings.TrimSpace(getenvRelayDatabaseRole(relaySchemaOwnerRoleEnvironment))
	migrationRole := strings.TrimSpace(getenvRelayDatabaseRole(relayMigrationDatabaseRoleEnvironment))
	runtimeRole := strings.TrimSpace(getenvRelayDatabaseRole(relayRuntimeDatabaseRoleEnvironment))
	edgeRole := relayDownloadEdgeDatabaseRoleName
	if !databaseRoleNamePattern.MatchString(ownerRole) || !databaseRoleNamePattern.MatchString(migrationRole) ||
		!databaseRoleNamePattern.MatchString(runtimeRole) || runtimeRole != "relay_runtime" ||
		ownerRole == migrationRole || ownerRole == runtimeRole || ownerRole == edgeRole ||
		migrationRole == runtimeRole || migrationRole == edgeRole || runtimeRole == edgeRole {
		return errors.New("Relay database role topology configuration is invalid")
	}
	quotedMigrationRole := relayQuoteDatabaseIdentifier(migrationRole)
	quotedRuntimeRole := relayQuoteDatabaseIdentifier(runtimeRole)
	quotedEdgeRole := relayQuoteDatabaseIdentifier(edgeRole)

	return db.Transaction(func(tx *gorm.DB) error {
		quiet := tx.Session(&gorm.Session{Logger: logger.Default.LogMode(logger.Silent)})
		if err := quiet.Exec(`SET LOCAL search_path = public`).Error; err != nil {
			return errors.New("Relay database role provisioning search path could not be fixed")
		}
		if err := quiet.Exec(`SET LOCAL session_replication_role = origin`).Error; err != nil {
			return errors.New("Relay database role provisioning replication mode could not be fixed")
		}
		var safeSearchPath bool
		if err := quiet.Raw(`SELECT current_setting('search_path') = 'public'
  AND current_schema() = 'public'
  AND current_schemas(true) = ARRAY['pg_catalog', 'public']::name[]`).Scan(&safeSearchPath).Error; err != nil || !safeSearchPath {
			return errors.New("Relay database role provisioning search path is unsafe")
		}
		if err := quiet.Exec(`SELECT set_config('lock_timeout', ?, true)`, lockTimeout).Error; err != nil {
			return errors.New("Relay database role provisioning lock timeout could not be installed")
		}
		if err := quiet.Exec(`SET LOCAL statement_timeout = '10min'`).Error; err != nil {
			return errors.New("Relay database role provisioning safety guard could not be installed")
		}
		if err := installRelayCredentialLoggingGuards(quiet); err != nil {
			return err
		}
		if err := quiet.Exec(`SELECT pg_catalog.pg_advisory_xact_lock(?)`, relayLifecycleAdvisoryLock).Error; err != nil {
			return errors.New("Relay database role provisioning lifecycle lock could not be acquired")
		}
		if err := acquireRelayLifecycleTransactionLock(quiet); err != nil {
			return err
		}
		// This is the last statement before role/schema DDL. It deliberately
		// permits only drift normalized below; event triggers, shared-object
		// ownership, system ACLs, default ACLs, and rogue schemas fail without
		// any catalog mutation.
		if err := verifyRelayProtectedDatabaseSurfacePreflight(
			quiet, ownerRole, migrationRole, runtimeRole, false,
		); err != nil {
			return err
		}
		for setting, value := range map[string]string{
			"relay.schema_owner_role": ownerRole,
			"relay.migration_role":    migrationRole,
			"relay.runtime_role":      runtimeRole,
			"relay.edge_role":         edgeRole,
		} {
			if err := quiet.Exec(`SELECT set_config(?, ?, true)`, setting, value).Error; err != nil {
				return errors.New("Relay database role provisioning context could not be installed")
			}
		}
		if err := quiet.Exec(relayDatabaseRoleTopologySQL).Error; err != nil {
			return errors.New("Relay database role topology could not be provisioned")
		}
		if err := revokeRelayProtectedRoleParameterPrivileges(
			quiet, ownerRole, migrationRole, runtimeRole, edgeRole,
		); err != nil {
			return err
		}
		for _, statement := range []string{
			fmt.Sprintf("ALTER ROLE %s PASSWORD '%s'", quotedMigrationRole, migrationVerifier),
			fmt.Sprintf("ALTER ROLE %s PASSWORD '%s'", quotedRuntimeRole, runtimeVerifier),
			fmt.Sprintf("ALTER ROLE %s PASSWORD '%s'", quotedEdgeRole, edgeVerifier),
		} {
			if err := quiet.Exec(statement).Error; err != nil {
				return errors.New("Relay database role credential could not be provisioned")
			}
		}
		if err := verifyRelayProtectedLoginRoleSCRAMCredentials(
			quiet, migrationRole, runtimeRole, edgeRole,
		); err != nil {
			return err
		}
		if err := quiet.Exec(relayDatabaseRoleApplicationACLsSQL).Error; err != nil {
			return errors.New("Relay database role application ACLs could not be provisioned")
		}
		if err := verifyRelayDatabaseRoleTopology(quiet, ownerRole, migrationRole, runtimeRole); err != nil {
			return fmt.Errorf("Relay database role topology postcondition failed: %w", err)
		}
		if err := verifyRelayDownloadEdgePreMigrationRole(quiet); err != nil {
			return errors.New("Relay download edge pre-migration role postcondition failed")
		}
		return nil
	})
}

// verifyRelayProtectedLoginRoleSCRAMCredentials is intentionally restricted to
// the role-admin pre/post boundary. It returns one boolean and never selects a
// verifier value, so neither client evidence nor an ordinary SQL result can
// expose the credential artifact. Runtime roles are not granted pg_authid.
func verifyRelayProtectedLoginRoleSCRAMCredentials(db *gorm.DB, migrationRole, runtimeRole, edgeRole string) error {
	roles := []string{migrationRole, runtimeRole, edgeRole}
	for _, role := range roles {
		if !databaseRoleNamePattern.MatchString(role) {
			return errors.New("Relay protected login credential topology is invalid")
		}
	}
	if migrationRole == runtimeRole || migrationRole == edgeRole || runtimeRole == edgeRole {
		return errors.New("Relay protected login credential topology is invalid")
	}
	var exact bool
	result := db.Raw(`SELECT count(*) = 3
  AND bool_and(rolpassword IS NOT NULL AND rolpassword ~ ?)
FROM pg_catalog.pg_authid
WHERE rolname IN (?, ?, ?)`, relayDatabaseSCRAMVerifierPattern, migrationRole, runtimeRole, edgeRole).Scan(&exact)
	if result.Error != nil || !exact {
		return errors.New("Relay protected login role credentials are not exact SCRAM-SHA-256 verifiers")
	}
	return nil
}

func installRelayCredentialLoggingGuards(db *gorm.DB) error {
	for _, guard := range []string{
		`SET LOCAL log_statement = 'none'`,
		`SET LOCAL log_min_error_statement = 'PANIC'`,
		`SET LOCAL log_min_duration_statement = -1`,
		`SET LOCAL log_min_duration_sample = -1`,
		`SET LOCAL log_statement_sample_rate = 0`,
		`SET LOCAL log_transaction_sample_rate = 0`,
		`SET LOCAL log_parameter_max_length = 0`,
		`SET LOCAL log_parameter_max_length_on_error = 0`,
		`SET LOCAL auto_explain.log_parameter_max_length = 0`,
		`SET LOCAL pgaudit.log_parameter = off`,
	} {
		if err := db.Exec(guard).Error; err != nil {
			return errors.New("Relay database credential logging guard could not be installed")
		}
	}
	var pgauditInstalled bool
	if err := db.Raw(`SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pgaudit')`).Scan(&pgauditInstalled).Error; err != nil {
		return errors.New("Relay database audit policy could not be inspected")
	}
	if pgauditInstalled {
		if err := db.Exec(`SET LOCAL pgaudit.log = 'none'`).Error; err != nil {
			return errors.New("Relay database audit logging could not be disabled for credential rotation")
		}
	}
	var exact bool
	if err := db.Raw(`SELECT current_setting('log_statement') = 'none'
  AND lower(current_setting('log_min_error_statement')) = 'panic'
  AND current_setting('log_min_duration_statement')::integer = -1
  AND current_setting('log_min_duration_sample')::integer = -1
  AND current_setting('log_statement_sample_rate')::double precision = 0
  AND current_setting('log_transaction_sample_rate')::double precision = 0
  AND current_setting('log_parameter_max_length')::integer = 0
  AND current_setting('log_parameter_max_length_on_error')::integer = 0
  AND current_setting('auto_explain.log_parameter_max_length')::integer = 0
  AND lower(current_setting('pgaudit.log_parameter')) = 'off'`).Scan(&exact).Error; err != nil || !exact {
		return errors.New("Relay database credential logging guards are not exact")
	}
	return nil
}

func revokeRelayProtectedRoleParameterPrivileges(db *gorm.DB, roles ...string) error {
	var parameters []string
	if err := db.Raw(`SELECT parname FROM pg_catalog.pg_parameter_acl ORDER BY parname`).Scan(&parameters).Error; err != nil {
		return errors.New("Relay database parameter ACL catalog could not be inspected")
	}
	for _, role := range roles {
		quotedRole := relayQuoteDatabaseIdentifier(role)
		for _, parameter := range parameters {
			if err := db.Exec(
				"REVOKE SET, ALTER SYSTEM ON PARAMETER " + relayQuoteDatabaseIdentifier(parameter) + " FROM " + quotedRole,
			).Error; err != nil {
				return errors.New("Relay protected role parameter privileges could not be revoked")
			}
		}
	}
	return nil
}

func relayQuoteDatabaseIdentifier(value string) string {
	return `"` + strings.ReplaceAll(value, `"`, `""`) + `"`
}
