package model

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"strconv"
	"strings"

	"gorm.io/gorm"
)

// relaySchemaV1PostgresCatalogSHA256 is frozen from an empty PostgreSQL 16
// application database after the complete v1 transaction. It is deliberately
// version-specific: a schema change must be represented by a new migration,
// never by changing what v1 means.
const relaySchemaV1PostgresCatalogSHA256 = "sha256:0ebe3f289439193f207f087452c289504fdd231759ac2b3d0159f8cc61d6cb6d"

// v2 intentionally has no PostgreSQL catalog delta. The separate registry
// entry is still frozen so an upgrade cannot inherit a mutable "latest"
// fingerprint or imply that the v1 migration was replayed.
const relaySchemaV2PostgresCatalogSHA256 = "sha256:0ebe3f289439193f207f087452c289504fdd231759ac2b3d0159f8cc61d6cb6d"

// v3 changes migration execution ordering without changing the resulting
// PostgreSQL catalog.
const relaySchemaV3PostgresCatalogSHA256 = "sha256:0ebe3f289439193f207f087452c289504fdd231759ac2b3d0159f8cc61d6cb6d"

type relaySchemaCatalogObject struct {
	Kind       string `gorm:"column:kind"`
	Identity   string `gorm:"column:identity"`
	Definition string `gorm:"column:definition"`
}

func expectedRelaySchemaCatalogFingerprint(dialect string, version int64) string {
	if dialect == "postgres" {
		switch version {
		case 1:
			return relaySchemaV1PostgresCatalogSHA256
		case 2:
			return relaySchemaV2PostgresCatalogSHA256
		case 3:
			return relaySchemaV3PostgresCatalogSHA256
		}
	}
	return ""
}

func relaySchemaCatalogAlgorithmAvailable(dialect string, version int64) bool {
	switch dialect {
	case "postgres":
		return version == 1 || version == 2 || version == 3
	case "sqlite", "mysql":
		return version >= 1
	default:
		return false
	}
}

var (
	relaySchemaExpectedCatalogForRuntime    = expectedRelaySchemaCatalogFingerprint
	relaySchemaCatalogAlgorithmForRuntime   = relaySchemaCatalogAlgorithmAvailable
	relaySchemaCatalogFingerprintForRuntime = getRelaySchemaCatalogFingerprintForVersion
)

// GetRelaySchemaCatalogFingerprint hashes normalized catalog definitions, not
// table data. PostgreSQL includes every application object in public: tables,
// columns, defaults, constraints, indexes, views, RLS policies, non-internal
// triggers and guard function bodies. Consequently dropping or replacing a
// safety trigger cannot leave a ledger-only "current" result.
func GetRelaySchemaCatalogFingerprint(db *gorm.DB) (string, error) {
	return relaySchemaCatalogFingerprintForRuntime(db, relaySchemaContractForRuntime().TargetVersion)
}

// getRelaySchemaCatalogFingerprintForVersion freezes normalization together
// with each schema version. A future catalog algorithm may add object classes
// for v2 without reinterpreting the v1 ledger or breaking a bridge image while
// it is still running against v1.
func getRelaySchemaCatalogFingerprintForVersion(db *gorm.DB, version int64) (string, error) {
	if db == nil {
		return "", errors.New("Relay schema catalog database is unavailable")
	}
	var objects []relaySchemaCatalogObject
	switch db.Dialector.Name() {
	case "postgres":
		if !relaySchemaCatalogAlgorithmAvailable("postgres", version) {
			return "", errors.New("Relay PostgreSQL schema catalog algorithm version is unavailable")
		}
		if err := db.Raw(relayPostgresSchemaCatalogV1SQL).Scan(&objects).Error; err != nil {
			return "", errors.New("Relay PostgreSQL schema catalog could not be inspected")
		}
	case "sqlite":
		if err := db.Raw(`
SELECT type AS kind, name AS identity, COALESCE(sql, '') AS definition
FROM sqlite_master
WHERE name NOT LIKE 'sqlite_%'
ORDER BY type, name`).Scan(&objects).Error; err != nil {
			return "", errors.New("Relay SQLite schema catalog could not be inspected")
		}
	default:
		// MySQL remains a supported upstream development database, but the
		// production Relay contract is PostgreSQL-only. Its stable marker keeps
		// local migration state usable without pretending to provide a
		// production-grade catalog attestation.
		objects = []relaySchemaCatalogObject{{Kind: "dialect", Identity: db.Dialector.Name(), Definition: fmt.Sprintf("non-production-v%d", version)}}
	}

	var canonical strings.Builder
	writeField := func(value string) {
		canonical.WriteString(strconv.Itoa(len(value)))
		canonical.WriteByte(':')
		canonical.WriteString(value)
	}
	for _, object := range objects {
		writeField(object.Kind)
		writeField(object.Identity)
		writeField(object.Definition)
	}
	digest := sha256.Sum256([]byte(canonical.String()))
	return fmt.Sprintf("sha256:%x", digest[:]), nil
}

const relayPostgresSchemaCatalogV1SQL = `
WITH catalog_objects AS (
    SELECT 'relation'::text AS kind,
           c.relname::text AS identity,
           concat_ws('|', c.relkind::text, c.relpersistence::text,
                     c.relrowsecurity::text, c.relforcerowsecurity::text,
				 COALESCE(relation_tablespace.spcname, '')) AS definition
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
	  LEFT JOIN pg_tablespace relation_tablespace ON relation_tablespace.oid = c.reltablespace
     WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
    UNION ALL
    SELECT 'column',
           c.relname || '.' || a.attname,
           concat_ws('|', format_type(a.atttypid, a.atttypmod), a.attnotnull::text,
                     a.attidentity::text, a.attgenerated::text,
                     COALESCE(pg_get_expr(d.adbin, d.adrelid, true), ''),
	                 COALESCE(collation_namespace.nspname || '.' || coll.collname, ''),
	                 COALESCE(coll.collprovider::text, ''),
	                 COALESCE(coll.collversion, ''))
      FROM pg_attribute a
      JOIN pg_class c ON c.oid = a.attrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
      LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
	  LEFT JOIN pg_collation coll ON coll.oid = a.attcollation
	  LEFT JOIN pg_namespace collation_namespace ON collation_namespace.oid = coll.collnamespace
     WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
       AND a.attnum > 0 AND NOT a.attisdropped
    UNION ALL
    SELECT 'constraint', c.relname || '.' || con.conname,
           concat_ws('|', con.contype::text, con.condeferrable::text, con.condeferred::text,
                     con.convalidated::text, pg_get_constraintdef(con.oid, true))
      FROM pg_constraint con
      JOIN pg_class c ON c.oid = con.conrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public'
    UNION ALL
    SELECT 'index', table_rel.relname || '.' || index_rel.relname,
	       concat_ws('|', i.indisvalid::text, i.indisready::text, i.indislive::text,
	                 pg_get_indexdef(index_rel.oid, 0, true),
	                 COALESCE(index_tablespace.spcname, ''))
      FROM pg_index i
      JOIN pg_class table_rel ON table_rel.oid = i.indrelid
      JOIN pg_class index_rel ON index_rel.oid = i.indexrelid
      JOIN pg_namespace n ON n.oid = table_rel.relnamespace
	  LEFT JOIN pg_tablespace index_tablespace ON index_tablespace.oid = index_rel.reltablespace
     WHERE n.nspname = 'public'
    UNION ALL
    SELECT 'toast-storage', parent_relation.relname,
           concat_ws('|', toast_relation.relkind::text,
                     COALESCE(toast_tablespace.spcname, ''),
                     COALESCE((
                       SELECT string_agg(
                                concat_ws(':', toast_index.indisunique::text,
                                          toast_index.indisprimary::text,
                                          toast_index.indisvalid::text,
                                          toast_index.indisready::text,
                                          toast_index.indislive::text,
                                          COALESCE(index_tablespace.spcname, '')),
                                ',' ORDER BY toast_index.indisprimary DESC,
                                             toast_index.indisunique DESC,
                                             toast_index.indisvalid DESC,
                                             toast_index.indisready DESC,
                                             toast_index.indislive DESC,
                                             COALESCE(index_tablespace.spcname, ''))
                         FROM pg_index toast_index
                         JOIN pg_class index_relation ON index_relation.oid = toast_index.indexrelid
                         LEFT JOIN pg_tablespace index_tablespace ON index_tablespace.oid = index_relation.reltablespace
                        WHERE toast_index.indrelid = toast_relation.oid
                     ), ''))
      FROM pg_class parent_relation
      JOIN pg_namespace parent_namespace ON parent_namespace.oid = parent_relation.relnamespace
      JOIN pg_class toast_relation ON toast_relation.oid = parent_relation.reltoastrelid
      LEFT JOIN pg_tablespace toast_tablespace ON toast_tablespace.oid = toast_relation.reltablespace
     WHERE parent_namespace.nspname = 'public' AND parent_relation.reltoastrelid <> 0
    UNION ALL
    SELECT 'view', c.relname, pg_get_viewdef(c.oid, true)
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public' AND c.relkind IN ('v', 'm')
    UNION ALL
    SELECT 'policy', c.relname || '.' || p.polname,
           concat_ws('|', p.polcmd::text, p.polpermissive::text,
	                 COALESCE((
	                   SELECT string_agg(CASE WHEN role_oid = 0 THEN 'PUBLIC' ELSE role.rolname END, ','
	                                     ORDER BY CASE WHEN role_oid = 0 THEN 'PUBLIC' ELSE role.rolname END)
	                   FROM unnest(p.polroles) AS role_oid
	                   LEFT JOIN pg_roles role ON role.oid = role_oid
	                 ), ''),
                     COALESCE(pg_get_expr(p.polqual, p.polrelid, true), ''),
                     COALESCE(pg_get_expr(p.polwithcheck, p.polrelid, true), ''))
      FROM pg_policy p
      JOIN pg_class c ON c.oid = p.polrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public'
    UNION ALL
    SELECT 'trigger', c.relname || '.' || t.tgname, pg_get_triggerdef(t.oid, true)
      FROM pg_trigger t
      JOIN pg_class c ON c.oid = t.tgrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
	 WHERE n.nspname = 'public' AND NOT t.tgisinternal
	UNION ALL
	SELECT 'trigger-state', c.relname || '.' || t.tgname,
	       concat_ws('|', t.tgenabled::text, t.tgconstraint::text)
	  FROM pg_trigger t
	  JOIN pg_class c ON c.oid = t.tgrelid
	  JOIN pg_namespace n ON n.oid = c.relnamespace
	 WHERE n.nspname = 'public' AND NOT t.tgisinternal
	UNION ALL
	SELECT 'sequence', c.relname,
	       concat_ws('|', format_type(s.seqtypid, NULL), s.seqstart::text,
	                 s.seqincrement::text, s.seqmax::text, s.seqmin::text,
	                 s.seqcache::text, s.seqcycle::text,
	                 COALESCE((
	                   SELECT owned_namespace.nspname || '.' || owned_relation.relname || '.' || owned_column.attname
	                     FROM pg_depend dependency
	                     JOIN pg_class owned_relation ON owned_relation.oid = dependency.refobjid
	                     JOIN pg_namespace owned_namespace ON owned_namespace.oid = owned_relation.relnamespace
	                     JOIN pg_attribute owned_column ON owned_column.attrelid = dependency.refobjid
	                      AND owned_column.attnum = dependency.refobjsubid AND NOT owned_column.attisdropped
	                    WHERE dependency.classid = 'pg_class'::regclass AND dependency.objid = c.oid
	                      AND dependency.refclassid = 'pg_class'::regclass AND dependency.refobjsubid > 0
	                      AND dependency.deptype IN ('a', 'i')
	                    ORDER BY owned_namespace.nspname, owned_relation.relname, owned_column.attname
	                    LIMIT 1
	                 ), ''))
	  FROM pg_sequence s
	  JOIN pg_class c ON c.oid = s.seqrelid
	  JOIN pg_namespace n ON n.oid = c.relnamespace
	 WHERE n.nspname = 'public'
    UNION ALL
    SELECT 'function', p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
           pg_get_functiondef(p.oid)
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public'
	UNION ALL
	SELECT 'type', t.typname,
	       concat_ws('|', t.typtype::text, t.typcategory::text, t.typnotnull::text,
	                 format_type(t.typbasetype, t.typtypmod), COALESCE(t.typdefault, ''),
	                 COALESCE((SELECT string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder)
	                             FROM pg_enum e WHERE e.enumtypid = t.oid), ''))
	  FROM pg_type t
	  JOIN pg_namespace n ON n.oid = t.typnamespace
	 WHERE n.nspname = 'public'
	   AND t.typelem = 0
	   AND NOT EXISTS (SELECT 1 FROM pg_class c WHERE c.reltype = t.oid)
	UNION ALL
	SELECT 'type-constraint', t.typname || '.' || con.conname,
	       concat_ws('|', con.contype::text, con.convalidated::text, pg_get_constraintdef(con.oid, true))
	  FROM pg_constraint con
	  JOIN pg_type t ON t.oid = con.contypid
	  JOIN pg_namespace n ON n.oid = t.typnamespace
	 WHERE n.nspname = 'public'
	UNION ALL
	SELECT 'collation', c.collname,
	       concat_ws('|', c.collprovider::text, c.collisdeterministic::text,
	                 COALESCE(c.collcollate, ''), COALESCE(c.collctype, ''),
	                 COALESCE(c.colliculocale, ''), COALESCE(c.collversion, ''))
	  FROM pg_collation c
	  JOIN pg_namespace n ON n.oid = c.collnamespace
	 WHERE n.nspname = 'public'
	UNION ALL
	SELECT 'extension', e.extname,
	       concat_ws('|', e.extversion, e.extrelocatable::text, n.nspname)
	  FROM pg_extension e
	  JOIN pg_namespace n ON n.oid = e.extnamespace
	 WHERE n.nspname = 'public'
)
SELECT kind, identity, definition
FROM catalog_objects
ORDER BY kind, identity, definition`
