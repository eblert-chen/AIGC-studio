package model

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"sort"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
)

type relayTablePrivilegeSet struct {
	Select bool
	Insert bool
	Update bool
	Delete bool
}

var relayRuntimeRestrictedTablePrivileges = map[string]relayTablePrivilegeSet{
	"relay_schema_state":      {Select: true},
	"relay_schema_migrations": {Select: true},
	"setups":                  {Select: true, Insert: true},

	"provider_credential_versions":                  {Select: true, Insert: true},
	"provider_channel_credential_set_versions":      {Select: true, Insert: true},
	"platform_generation_reconciliation_events":     {Select: true, Insert: true},
	"platform_generation_callback_redrive_events":   {Select: true, Insert: true},
	"platform_provider_terminal_outcomes":           {Select: true, Insert: true},
	"platform_provider_alert_events":                {Select: true, Insert: true},
	"platform_provider_retirement_acknowledgements": {Select: true, Insert: true},
	"platform_provider_contract_rates":              {Select: true, Insert: true},
	"platform_channel_cost_events":                  {Select: true, Insert: true},
	"platform_download_completion_events":           {Select: true, Insert: true},
	"platform_download_completion_proofs":           {Select: true, Insert: true},
	"platform_task_stage_events":                    {Select: true, Insert: true},
	"platform_operations_snapshot_events":           {Select: true, Insert: true},
	"platform_channel_control_operations":           {Select: true, Insert: true, Update: true},
}

const relayRuntimeDatabasePrivilegeManifestV1Artifact = `abilities|SIUD
auth_flows|SIUD
authz_roles|SIUD
casbin_rule|SIUD
channels|SIUD
checkins|SIUD
custom_oauth_providers|SIUD
external_identity_claims|SIUD
logs|SIUD
midjourneys|SIUD
models|SIUD
options|SIUD
passkey_credentials|SIUD
perf_metrics|SIUD
platform_artifact_upload_intents|SIUD
platform_channel_control_operations|SIU-
platform_channel_cost_events|SI--
platform_channel_cost_reconciliations|SIUD
platform_download_completion_events|SI--
platform_download_completion_proofs|SI--
platform_download_edge_tickets|SIUD
platform_generation_callback_deliveries|SIUD
platform_generation_callback_redrive_events|SI--
platform_generation_jobs|SIUD
platform_generation_outboxes|SIUD
platform_generation_provider_account_states|SIUD
platform_generation_provider_routes|SIUD
platform_generation_reconciliation_events|SI--
platform_generation_route_admissions|SIUD
platform_operations_snapshot_events|SI--
platform_provider_alert_events|SI--
platform_provider_contract_rates|SI--
platform_provider_incidents|SIUD
platform_provider_monitor_leases|SIUD
platform_provider_retirement_acknowledgements|SI--
platform_provider_route_health|SIUD
platform_provider_terminal_outcomes|SI--
platform_relay_external_deliveries|SIUD
platform_task_stage_events|SI--
prefill_groups|SIUD
provider_channel_credential_set_versions|SI--
provider_credential_versions|SI--
quota_data|SIUD
redemptions|SIUD
relay_schema_migrations|S---
relay_schema_state|S---
setups|SI--
subscription_orders|SIUD
subscription_plans|SIUD
subscription_pre_consume_records|SIUD
system_instances|SIUD
system_task_locks|SIUD
system_tasks|SIUD
tasks|SIUD
tokens|SIUD
top_ups|SIUD
two_fa_backup_codes|SIUD
two_fas|SIUD
user_oauth_bindings|SIUD
user_sessions|SIUD
user_subscriptions|SIUD
users|SIUD
vendors|SIUD
`

const relayRuntimeDatabasePrivilegeManifestV1SHA256 = "sha256:b10950f53737ba40edf7c7548c5e9afecd75ec017da21bd8e8975861a8310709"

// v2 is an explicit no-ACL-delta release snapshot. Keeping a versioned
// registry entry prevents the current runtime model from silently widening
// either the v1 history or the v2 release surface.
const relayRuntimeDatabasePrivilegeManifestV2Artifact = relayRuntimeDatabasePrivilegeManifestV1Artifact
const relayRuntimeDatabasePrivilegeManifestV2SHA256 = "sha256:b10950f53737ba40edf7c7548c5e9afecd75ec017da21bd8e8975861a8310709"

func relayRuntimeDatabasePrivilegeManifestLiveV1(db *gorm.DB) (map[string]relayTablePrivilegeSet, error) {
	manifest := make(map[string]relayTablePrivilegeSet)
	for _, value := range relaySchemaV1ArtifactModels() {
		statement := &gorm.Statement{DB: db}
		if err := statement.Parse(value); err != nil || statement.Schema == nil || statement.Schema.Table == "" {
			return nil, errors.New("Relay database privilege manifest contains an invalid model")
		}
		manifest[statement.Schema.Table] = relayTablePrivilegeSet{Select: true, Insert: true, Update: true, Delete: true}
	}
	for table, privileges := range relayRuntimeRestrictedTablePrivileges {
		if _, exists := manifest[table]; !exists {
			return nil, fmt.Errorf("Relay database privilege manifest references unknown table %s", table)
		}
		manifest[table] = privileges
	}
	return manifest, nil
}

func relayRuntimeDatabasePrivilegeManifestForVersion(version int64) (map[string]relayTablePrivilegeSet, error) {
	var artifact string
	switch version {
	case 1:
		artifact = relayRuntimeDatabasePrivilegeManifestV1Artifact
	case 2:
		artifact = relayRuntimeDatabasePrivilegeManifestV2Artifact
	default:
		return nil, errors.New("Relay runtime database privilege manifest version is unavailable")
	}
	manifest := make(map[string]relayTablePrivilegeSet)
	for _, line := range strings.Split(strings.TrimSpace(artifact), "\n") {
		fields := strings.Split(line, "|")
		if len(fields) != 2 || !databaseRoleNamePattern.MatchString(fields[0]) || len(fields[1]) != 4 {
			return nil, errors.New("Relay runtime database privilege manifest artifact is invalid")
		}
		if _, duplicate := manifest[fields[0]]; duplicate {
			return nil, errors.New("Relay runtime database privilege manifest artifact contains a duplicate")
		}
		flags := fields[1]
		for index, expected := range "SIUD" {
			if rune(flags[index]) != expected && flags[index] != '-' {
				return nil, errors.New("Relay runtime database privilege manifest artifact contains an invalid privilege")
			}
		}
		manifest[fields[0]] = relayTablePrivilegeSet{
			Select: flags[0] == 'S', Insert: flags[1] == 'I', Update: flags[2] == 'U', Delete: flags[3] == 'D',
		}
	}
	if len(manifest) == 0 {
		return nil, errors.New("Relay runtime database privilege manifest artifact is empty")
	}
	return manifest, nil
}

var relayRuntimeDatabasePrivilegeManifestForRuntime = relayRuntimeDatabasePrivilegeManifestForVersion

func relayRuntimeDatabasePrivilegeManifestCanonical(manifest map[string]relayTablePrivilegeSet) string {
	tables := make([]string, 0, len(manifest))
	for table := range manifest {
		tables = append(tables, table)
	}
	sort.Strings(tables)
	var canonical strings.Builder
	for _, table := range tables {
		canonical.WriteString(table)
		canonical.WriteByte('|')
		canonical.WriteString(relayTablePrivilegeFlags(manifest[table]))
		canonical.WriteByte('\n')
	}
	return canonical.String()
}

func relayTablePrivilegeFlags(privileges relayTablePrivilegeSet) string {
	flags := []byte{'-', '-', '-', '-'}
	if privileges.Select {
		flags[0] = 'S'
	}
	if privileges.Insert {
		flags[1] = 'I'
	}
	if privileges.Update {
		flags[2] = 'U'
	}
	if privileges.Delete {
		flags[3] = 'D'
	}
	return string(flags)
}

func relayRuntimeDatabasePrivilegeManifestSHA256(manifest map[string]relayTablePrivilegeSet) string {
	digest := sha256.Sum256([]byte(relayRuntimeDatabasePrivilegeManifestCanonical(manifest)))
	return fmt.Sprintf("sha256:%x", digest[:])
}

// ApplyRelayDatabasePrivilegeManifestWithDB is executed by the schema owner at
// the end of v1. The same in-process manifest is then used by runtime
// attestation, eliminating drift between deployment SQL and server needs.
func ApplyRelayDatabasePrivilegeManifestWithDB(db *gorm.DB) error {
	return applyRelayDatabasePrivilegeManifestForVersion(db, RelaySchemaTargetVersion)
}

func applyRelayDatabasePrivilegeManifestForVersion(db *gorm.DB, version int64) error {
	if !RelayDatabaseRoleAttestationRequired() {
		return nil
	}
	if db == nil || db.Dialector.Name() != "postgres" {
		return errors.New("production Relay database privileges require PostgreSQL")
	}
	runtimeRole := strings.TrimSpace(getenvRelayDatabaseRole(relayRuntimeDatabaseRoleEnvironment))
	if !databaseRoleNamePattern.MatchString(runtimeRole) {
		return errors.New("Relay runtime database role is invalid")
	}
	manifest, err := relayRuntimeDatabasePrivilegeManifestForRuntime(version)
	if err != nil {
		return err
	}
	ownerRole := strings.TrimSpace(getenvRelayDatabaseRole(relaySchemaOwnerRoleEnvironment))
	if !databaseRoleNamePattern.MatchString(ownerRole) {
		return errors.New("Relay schema owner database role is invalid")
	}
	// Clear every non-owner application-object ACL before rebuilding the two
	// versioned DML surfaces. This removes direct and column-level grants left
	// by a legacy deployment or an out-of-band role without using DROP OWNED,
	// which could affect unrelated schemas.
	if err := resetRelayApplicationObjectACLs(db, ownerRole); err != nil {
		return err
	}
	tables := make([]string, 0, len(manifest))
	for table := range manifest {
		tables = append(tables, table)
	}
	sort.Strings(tables)
	quotedRole := quoteRelayDatabaseIdentifier(runtimeRole)
	for _, table := range tables {
		quotedTable := quoteRelayDatabaseIdentifier(table)
		if err := db.Exec("REVOKE ALL PRIVILEGES ON TABLE public." + quotedTable + " FROM " + quotedRole).Error; err != nil {
			return errors.New("Relay runtime table privileges could not be reset")
		}
		privileges := relayTablePrivilegeNames(manifest[table])
		if len(privileges) > 0 {
			statement := "GRANT " + strings.Join(privileges, ", ") + " ON TABLE public." + quotedTable + " TO " + quotedRole
			if err := db.Exec(statement).Error; err != nil {
				return errors.New("Relay runtime table privileges could not be granted")
			}
		}
	}
	if err := db.Exec("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC").Error; err != nil {
		return errors.New("Relay public table privileges could not be revoked")
	}
	if err := db.Exec("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC, " + quotedRole).Error; err != nil {
		return errors.New("Relay runtime sequence privileges could not be reset")
	}
	var sequences []relayCatalogSequence
	if err := db.Raw(relayCatalogSequencesSQL).Scan(&sequences).Error; err != nil {
		return errors.New("Relay runtime sequence catalog could not be inspected")
	}
	for _, sequence := range sequences {
		privileges, exists := manifest[sequence.OwnedTable]
		if !exists || !privileges.Insert {
			continue
		}
		if err := db.Exec("GRANT USAGE ON SEQUENCE public." + quoteRelayDatabaseIdentifier(sequence.Name) + " TO " + quotedRole).Error; err != nil {
			return errors.New("Relay runtime sequence privileges could not be granted")
		}
	}
	if err := db.Exec("REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, " + quotedRole).Error; err != nil {
		return errors.New("Relay runtime function privileges could not be revoked")
	}
	return verifyRelayRuntimeDatabasePrivilegeManifest(db, runtimeRole, version)
}

type relayApplicationACLGrantee struct {
	OID  int64  `gorm:"column:oid"`
	Name string `gorm:"column:name"`
}

// resetRelayApplicationObjectACLs runs only as the protected schema owner in
// the migration transaction. The owner keeps its inherent privileges; all
// other principals (including PUBLIC, runtime and edge) are reset and then
// reconstructed from the versioned manifests.
func resetRelayApplicationObjectACLs(db *gorm.DB, ownerRole string) error {
	var grantees []relayApplicationACLGrantee
	if err := db.Raw(`
WITH object_grants AS (
  SELECT acl.grantee
    FROM pg_class object
    JOIN pg_namespace namespace ON namespace.oid = object.relnamespace
    CROSS JOIN LATERAL aclexplode(COALESCE(object.relacl,
      acldefault(CASE WHEN object.relkind = 'S' THEN 's'::"char" ELSE 'r'::"char" END, object.relowner))) acl
   WHERE namespace.nspname = 'public' AND object.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
  UNION
  SELECT acl.grantee
    FROM pg_attribute attribute
    JOIN pg_class object ON object.oid = attribute.attrelid
    JOIN pg_namespace namespace ON namespace.oid = object.relnamespace
    CROSS JOIN LATERAL aclexplode(attribute.attacl) acl
   WHERE namespace.nspname = 'public' AND attribute.attnum > 0 AND NOT attribute.attisdropped
  UNION
  SELECT acl.grantee
    FROM pg_proc function
    JOIN pg_namespace namespace ON namespace.oid = function.pronamespace
    CROSS JOIN LATERAL aclexplode(COALESCE(function.proacl, acldefault('f', function.proowner))) acl
   WHERE namespace.nspname = 'public'
), expected_owner AS (SELECT oid FROM pg_roles WHERE rolname = ?)
SELECT DISTINCT object_grants.grantee::bigint AS oid,
       CASE WHEN object_grants.grantee = 0 THEN 'PUBLIC' ELSE role.rolname END AS name
  FROM object_grants
  CROSS JOIN expected_owner
  LEFT JOIN pg_roles role ON role.oid = object_grants.grantee
 WHERE object_grants.grantee <> expected_owner.oid
 ORDER BY name`, ownerRole).Scan(&grantees).Error; err != nil {
		return errors.New("Relay application object ACL grantees could not be inspected")
	}
	for _, grantee := range grantees {
		if grantee.Name == "" || (grantee.OID != 0 && !databaseRoleNamePattern.MatchString(grantee.Name)) {
			return errors.New("Relay application object ACL contains an unknown grantee")
		}
		quotedGrantee := "PUBLIC"
		if grantee.OID != 0 {
			quotedGrantee = quoteRelayDatabaseIdentifier(grantee.Name)
		}
		var columnGrants []struct {
			Table  string `gorm:"column:table_name"`
			Column string `gorm:"column:column_name"`
		}
		if err := db.Raw(`
SELECT DISTINCT object.relname AS table_name, attribute.attname AS column_name
  FROM pg_attribute attribute
  JOIN pg_class object ON object.oid = attribute.attrelid
  JOIN pg_namespace namespace ON namespace.oid = object.relnamespace
  CROSS JOIN LATERAL aclexplode(attribute.attacl) acl
 WHERE namespace.nspname = 'public' AND attribute.attnum > 0 AND NOT attribute.attisdropped
   AND acl.grantee = ?
 ORDER BY object.relname, attribute.attname`, grantee.OID).Scan(&columnGrants).Error; err != nil {
			return errors.New("Relay application column ACLs could not be inspected")
		}
		for _, grant := range columnGrants {
			for _, privilege := range []string{"SELECT", "INSERT", "UPDATE", "REFERENCES"} {
				statement := "REVOKE " + privilege + " (" + quoteRelayDatabaseIdentifier(grant.Column) + ") ON TABLE public." +
					quoteRelayDatabaseIdentifier(grant.Table) + " FROM " + quotedGrantee
				if err := db.Exec(statement).Error; err != nil {
					return errors.New("Relay application column ACL could not be reset")
				}
			}
		}
		for _, statement := range []string{
			"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM " + quotedGrantee,
			"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM " + quotedGrantee,
			"REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM " + quotedGrantee,
		} {
			if err := db.Exec(statement).Error; err != nil {
				return errors.New("Relay application object ACL could not be reset")
			}
		}
	}
	return nil
}

// verifyRelayApplicationObjectACLTopology proves that no third role can read,
// mutate or execute application objects and that the DML roles have no grant
// option. Effective-permission manifests remain responsible for the exact
// runtime and edge privilege sets.
func verifyRelayApplicationObjectACLTopology(db *gorm.DB) error {
	ownerRole := strings.TrimSpace(getenvRelayDatabaseRole(relaySchemaOwnerRoleEnvironment))
	runtimeRole := strings.TrimSpace(getenvRelayDatabaseRole(relayRuntimeDatabaseRoleEnvironment))
	if !databaseRoleNamePattern.MatchString(ownerRole) || !databaseRoleNamePattern.MatchString(runtimeRole) {
		return errors.New("Relay application object ACL topology configuration is invalid")
	}
	var unexpectedCount int64
	if err := db.Raw(`
WITH expected AS (
  SELECT owner.oid AS owner_oid, runtime.oid AS runtime_oid, edge.oid AS edge_oid
    FROM pg_roles owner CROSS JOIN pg_roles runtime CROSS JOIN pg_roles edge
   WHERE owner.rolname = ? AND runtime.rolname = ? AND edge.rolname = ?
), object_grants AS (
  SELECT acl.grantee, acl.is_grantable
    FROM pg_class object
    JOIN pg_namespace namespace ON namespace.oid = object.relnamespace
    CROSS JOIN LATERAL aclexplode(COALESCE(object.relacl,
      acldefault(CASE WHEN object.relkind = 'S' THEN 's'::"char" ELSE 'r'::"char" END, object.relowner))) acl
   WHERE namespace.nspname = 'public' AND object.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
  UNION ALL
  SELECT acl.grantee, acl.is_grantable
    FROM pg_attribute attribute
    JOIN pg_class object ON object.oid = attribute.attrelid
    JOIN pg_namespace namespace ON namespace.oid = object.relnamespace
    CROSS JOIN LATERAL aclexplode(attribute.attacl) acl
   WHERE namespace.nspname = 'public' AND attribute.attnum > 0 AND NOT attribute.attisdropped
  UNION ALL
  SELECT acl.grantee, acl.is_grantable
    FROM pg_proc function
    JOIN pg_namespace namespace ON namespace.oid = function.pronamespace
    CROSS JOIN LATERAL aclexplode(COALESCE(function.proacl, acldefault('f', function.proowner))) acl
   WHERE namespace.nspname = 'public'
)
SELECT count(*)
  FROM object_grants CROSS JOIN expected
 WHERE object_grants.grantee NOT IN (expected.owner_oid, expected.runtime_oid, expected.edge_oid)
    OR (object_grants.grantee <> expected.owner_oid AND object_grants.is_grantable)`,
		ownerRole, runtimeRole, relayDownloadEdgeDatabaseRoleName).Scan(&unexpectedCount).Error; err != nil {
		return errors.New("Relay application object ACL topology could not be inspected")
	}
	if unexpectedCount != 0 {
		return errors.New("Relay application object ACL topology is not isolated")
	}
	return verifyRelayPublicTypeACLTopology(db)
}

type relayApplicationCatalogColumn struct {
	Table  string `gorm:"column:table_name"`
	Column string `gorm:"column:column_name"`
}

type relayApplicationDirectColumnACL struct {
	Table       string `gorm:"column:table_name"`
	Column      string `gorm:"column:column_name"`
	Grantee     int64  `gorm:"column:grantee"`
	Grantor     int64  `gorm:"column:grantor"`
	Privilege   string `gorm:"column:privilege_type"`
	GrantOption bool   `gorm:"column:is_grantable"`
}

// verifyRelayApplicationColumnACLTopology compares every direct public-column
// ACL tuple to the versioned edge manifest. Runtime and owner permissions are
// table-level in v1, while the download edge has only the listed UPDATE column
// grants. Effective privilege checks alone cannot detect a redundant SELECT
// grant when a role already has table-level SELECT.
func verifyRelayApplicationColumnACLTopology(db *gorm.DB, version int64, requireComplete bool) error {
	manifest, err := relayDownloadEdgeDatabasePrivilegeManifestForRuntime(version)
	if err != nil {
		return err
	}
	ownerRole := strings.TrimSpace(getenvRelayDatabaseRole(relaySchemaOwnerRoleEnvironment))
	if !databaseRoleNamePattern.MatchString(ownerRole) {
		return errors.New("Relay application column ACL topology configuration is invalid")
	}
	var roles struct {
		OwnerOID int64 `gorm:"column:owner_oid"`
		EdgeOID  int64 `gorm:"column:edge_oid"`
	}
	if err := db.Raw(`
SELECT COALESCE((SELECT oid FROM pg_roles WHERE rolname = ?), 0)::bigint AS owner_oid,
       COALESCE((SELECT oid FROM pg_roles WHERE rolname = ?), 0)::bigint AS edge_oid`,
		ownerRole, relayDownloadEdgeDatabaseRoleName).Scan(&roles).Error; err != nil {
		return errors.New("Relay application column ACL roles could not be inspected")
	}
	if requireComplete && (roles.OwnerOID == 0 || roles.EdgeOID == 0) {
		return errors.New("Relay application column ACL roles are incomplete")
	}

	var catalogColumns []relayApplicationCatalogColumn
	if err := db.Raw(`
SELECT relation.relname AS table_name, attribute.attname AS column_name
  FROM pg_attribute attribute
  JOIN pg_class relation ON relation.oid = attribute.attrelid
  JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
 WHERE namespace.nspname = 'public'
   AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
   AND attribute.attnum > 0 AND NOT attribute.attisdropped
 ORDER BY relation.relname, attribute.attnum`).Scan(&catalogColumns).Error; err != nil {
		return errors.New("Relay application column catalog could not be inspected")
	}
	expected := make(map[string]struct{})
	for _, column := range catalogColumns {
		if manifest.UpdateColumns[column.Table][column.Column] {
			expected[column.Table+"\x00"+column.Column] = struct{}{}
		}
	}

	var actual []relayApplicationDirectColumnACL
	if err := db.Raw(`
SELECT relation.relname AS table_name, attribute.attname AS column_name,
       acl.grantee::bigint AS grantee, acl.grantor::bigint AS grantor,
       acl.privilege_type, acl.is_grantable
  FROM pg_attribute attribute
  JOIN pg_class relation ON relation.oid = attribute.attrelid
  JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
  CROSS JOIN LATERAL aclexplode(attribute.attacl) acl
 WHERE namespace.nspname = 'public'
   AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
   AND attribute.attnum > 0 AND NOT attribute.attisdropped
 ORDER BY relation.relname, attribute.attnum, acl.grantee, acl.privilege_type`).Scan(&actual).Error; err != nil {
		return errors.New("Relay application direct column ACLs could not be inspected")
	}
	seen := make(map[string]struct{}, len(actual))
	for _, acl := range actual {
		key := acl.Table + "\x00" + acl.Column
		if _, allowed := expected[key]; !allowed ||
			acl.Grantee != roles.EdgeOID || acl.Grantor != roles.OwnerOID ||
			acl.Privilege != "UPDATE" || acl.GrantOption {
			return errors.New("Relay application direct column ACL surface is not exact")
		}
		if _, duplicate := seen[key]; duplicate {
			return errors.New("Relay application direct column ACL surface is not exact")
		}
		seen[key] = struct{}{}
	}
	if requireComplete && len(seen) != len(expected) {
		return errors.New("Relay application direct column ACL surface is incomplete")
	}
	return nil
}

// Public type ACLs are not part of the v1 DML manifest, so their only allowed
// state is PostgreSQL's owner/PUBLIC acldefault. Both added grants and baseline
// revocations are drift and must fail readiness and migration preflight.
func verifyRelayPublicTypeACLTopology(db *gorm.DB) error {
	var mismatchCount int64
	if err := db.Raw(`
WITH application_types AS (
  SELECT type_object.oid, type_object.typowner, type_object.typacl
    FROM pg_type type_object
    JOIN pg_namespace namespace ON namespace.oid = type_object.typnamespace
   WHERE namespace.nspname = 'public'
), actual AS (
  SELECT type_object.oid, acl.grantee, acl.grantor,
         acl.privilege_type, acl.is_grantable
    FROM application_types type_object
    CROSS JOIN LATERAL aclexplode(
      COALESCE(type_object.typacl, acldefault('T'::"char", type_object.typowner))
    ) acl
), expected AS (
  SELECT type_object.oid, acl.grantee, acl.grantor,
         acl.privilege_type, acl.is_grantable
    FROM application_types type_object
    CROSS JOIN LATERAL aclexplode(acldefault('T'::"char", type_object.typowner)) acl
), extra AS (
  SELECT * FROM actual EXCEPT SELECT * FROM expected
), missing AS (
  SELECT * FROM expected EXCEPT SELECT * FROM actual
)
SELECT (SELECT count(*) FROM extra) + (SELECT count(*) FROM missing)`,
	).Scan(&mismatchCount).Error; err != nil {
		return errors.New("Relay public type ACL baseline could not be inspected")
	}
	if mismatchCount != 0 {
		return errors.New("Relay public type ACL surface is not exact")
	}
	return nil
}

func relayTablePrivilegeNames(privileges relayTablePrivilegeSet) []string {
	result := make([]string, 0, 4)
	if privileges.Select {
		result = append(result, "SELECT")
	}
	if privileges.Insert {
		result = append(result, "INSERT")
	}
	if privileges.Update {
		result = append(result, "UPDATE")
	}
	if privileges.Delete {
		result = append(result, "DELETE")
	}
	return result
}

func quoteRelayDatabaseIdentifier(value string) string {
	return `"` + strings.ReplaceAll(value, `"`, `""`) + `"`
}

// getenvRelayDatabaseRole is a seam kept tiny so privilege-manifest tests can
// exercise role validation without copying environment parsing logic.
var getenvRelayDatabaseRole = func(name string) string {
	return strings.TrimSpace(common.GetEnvOrDefaultString(name, ""))
}

type relayCatalogTable struct {
	Name string `gorm:"column:name"`
}

type relayCatalogSequence struct {
	Name        string `gorm:"column:name"`
	OwnedTable  string `gorm:"column:owned_table"`
	OwnedColumn string `gorm:"column:owned_column"`
}

const relayCatalogSequencesSQL = `
SELECT sequence.relname AS name,
       COALESCE(table_rel.relname, '') AS owned_table,
       COALESCE(table_column.attname, '') AS owned_column
FROM pg_class sequence
JOIN pg_namespace namespace ON namespace.oid = sequence.relnamespace
LEFT JOIN pg_depend dependency ON dependency.classid = 'pg_class'::regclass
 AND dependency.objid = sequence.oid AND dependency.refclassid = 'pg_class'::regclass
 AND dependency.refobjsubid > 0 AND dependency.deptype IN ('a', 'i')
LEFT JOIN pg_class table_rel ON table_rel.oid = dependency.refobjid
LEFT JOIN pg_attribute table_column ON table_column.attrelid = dependency.refobjid
 AND table_column.attnum = dependency.refobjsubid AND NOT table_column.attisdropped
WHERE namespace.nspname = 'public' AND sequence.relkind = 'S'
ORDER BY sequence.relname`

func verifyRelayRuntimeDatabasePrivilegeManifest(db *gorm.DB, role string, version int64) error {
	manifest, err := relayRuntimeDatabasePrivilegeManifestForRuntime(version)
	if err != nil {
		return err
	}
	var catalogTables []relayCatalogTable
	if err := db.Raw(`
SELECT c.relname AS name
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
ORDER BY c.relname`).Scan(&catalogTables).Error; err != nil {
		return errors.New("Relay runtime table catalog could not be inspected")
	}
	if len(catalogTables) != len(manifest) {
		return errors.New("Relay runtime table privilege manifest does not cover the catalog")
	}
	for _, table := range catalogTables {
		expected, exists := manifest[table.Name]
		if !exists {
			return errors.New("Relay runtime table privilege manifest does not cover the catalog")
		}
		actual, err := relayEffectiveTablePrivileges(db, role, table.Name)
		if err != nil || actual != expected {
			return errors.New("Relay runtime table privileges do not match the manifest")
		}
		if err := verifyRelayColumnPrivileges(db, role, table.Name, expected); err != nil {
			return err
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

	var sequences []relayCatalogSequence
	if err := db.Raw(relayCatalogSequencesSQL).Scan(&sequences).Error; err != nil {
		return errors.New("Relay runtime sequence catalog could not be inspected")
	}
	for _, sequence := range sequences {
		expectedUsage := false
		if tablePrivileges, exists := manifest[sequence.OwnedTable]; exists {
			expectedUsage = tablePrivileges.Insert
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
			role, qualified, role, qualified, role, qualified).Scan(&actual).Error; err != nil {
			return errors.New("Relay runtime sequence privileges could not be inspected")
		}
		if actual.Usage != expectedUsage || actual.Select || actual.Update {
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
	// The runtime grant step precedes the edge grant step inside the same v1
	// transaction. Reject every unexpected direct column tuple here, but let the
	// later edge step prove that its complete versioned set has been installed.
	if err := verifyRelayApplicationColumnACLTopology(db, version, false); err != nil {
		return err
	}
	return verifyRelayApplicationObjectACLTopology(db)
}

func relayEffectiveTablePrivileges(db *gorm.DB, role string, table string) (relayTablePrivilegeSet, error) {
	qualified := "public." + table
	var actual struct {
		Select     bool `gorm:"column:select_privilege"`
		Insert     bool `gorm:"column:insert_privilege"`
		Update     bool `gorm:"column:update_privilege"`
		Delete     bool `gorm:"column:delete_privilege"`
		Truncate   bool `gorm:"column:truncate_privilege"`
		References bool `gorm:"column:references_privilege"`
		Trigger    bool `gorm:"column:trigger_privilege"`
	}
	if err := db.Raw(`SELECT
  has_table_privilege(?, ?, 'SELECT') AS select_privilege,
  has_table_privilege(?, ?, 'INSERT') AS insert_privilege,
  has_table_privilege(?, ?, 'UPDATE') AS update_privilege,
  has_table_privilege(?, ?, 'DELETE') AS delete_privilege,
  has_table_privilege(?, ?, 'TRUNCATE') AS truncate_privilege,
  has_table_privilege(?, ?, 'REFERENCES') AS references_privilege,
  has_table_privilege(?, ?, 'TRIGGER') AS trigger_privilege`,
		role, qualified, role, qualified, role, qualified, role, qualified,
		role, qualified, role, qualified, role, qualified).Scan(&actual).Error; err != nil {
		return relayTablePrivilegeSet{}, err
	}
	if actual.Truncate || actual.References || actual.Trigger {
		return relayTablePrivilegeSet{}, errors.New("Relay runtime role has a forbidden table privilege")
	}
	return relayTablePrivilegeSet{Select: actual.Select, Insert: actual.Insert, Update: actual.Update, Delete: actual.Delete}, nil
}

func verifyRelayColumnPrivileges(db *gorm.DB, role string, table string, expected relayTablePrivilegeSet) error {
	var mismatchCount int64
	if err := db.Raw(`
SELECT count(*)
FROM pg_attribute attribute
JOIN pg_class relation ON relation.oid = attribute.attrelid
JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
WHERE namespace.nspname = 'public' AND relation.relname = ?
  AND attribute.attnum > 0 AND NOT attribute.attisdropped
  AND (
    has_column_privilege(?, relation.oid, attribute.attnum, 'SELECT') <> ? OR
    has_column_privilege(?, relation.oid, attribute.attnum, 'INSERT') <> ? OR
    has_column_privilege(?, relation.oid, attribute.attnum, 'UPDATE') <> ? OR
    has_column_privilege(?, relation.oid, attribute.attnum, 'REFERENCES')
  )`, table, role, expected.Select, role, expected.Insert, role, expected.Update, role).Scan(&mismatchCount).Error; err != nil {
		return errors.New("Relay runtime column privileges could not be inspected")
	}
	if mismatchCount != 0 {
		return errors.New("Relay runtime column privileges do not match the manifest")
	}
	return nil
}
