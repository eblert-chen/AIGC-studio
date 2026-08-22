package model

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"strconv"
	"strings"

	"gorm.io/gorm"
)

// These digests freeze the security-relevant, non-extension pg_catalog
// semantics of the two PostgreSQL 16 initdb distributions qualified by the
// Relay release gate. The collation and information-schema catalogs are
// distribution and initdb data,
// so a different PostgreSQL image must be deliberately requalified instead of
// being accepted merely because its major version is 16. information_schema
// is included so a missing or replaced standard view cannot evade the separate
// ownership and ACL proofs. The exact plpgsql/pgaudit member sets are also
// verified separately; their complete non-ACL semantics use stable identities
// below instead of their database-assigned OIDs.
const (
	relayPostgres16AlpineSystemSemanticSHA256 = "sha256:d67b2a78cc769e306723fee1dc7a7282ee4d481b6e2b8353ee1ef1bf81d574eb"
	relayPostgres16DebianSystemSemanticSHA256 = "sha256:f97e2f23386ec637defd1cf62f84def8cd76198bfd9e784a1646d1942215b12a"
)

func verifyRelayPostgres16SystemSemanticBaseline(db *gorm.DB) error {
	actual, err := relayPostgres16SystemSemanticFingerprint(db)
	if err != nil {
		return err
	}
	if relayPostgres16AlpineSystemSemanticSHA256 == "sha256:pending" ||
		relayPostgres16DebianSystemSemanticSHA256 == "sha256:pending" {
		return fmt.Errorf("freeze Relay PostgreSQL 16 system semantic baseline as %s", actual)
	}
	if actual != relayPostgres16AlpineSystemSemanticSHA256 &&
		actual != relayPostgres16DebianSystemSemanticSHA256 {
		return errors.New("Relay PostgreSQL 16 system semantic baseline is not exact")
	}
	return nil
}

func relayPostgres16SystemSemanticFingerprint(db *gorm.DB) (string, error) {
	var objects []relaySchemaCatalogObject
	if err := db.Raw(relayPostgres16SystemSemanticSQL).Scan(&objects).Error; err != nil {
		return "", fmt.Errorf("Relay PostgreSQL 16 system semantic baseline could not be inspected: %w", err)
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

const relayPostgres16SystemSemanticSQL = `
WITH allowed_extension_members AS (
  SELECT dependency.classid, dependency.objid, dependency.objsubid,
         extension.extname AS extension_name, extension.extowner
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
), catalog_objects AS (
  SELECT 'system-function'::text AS kind,
         function_object.oid::text AS identity,
         concat_ws('|', function_object.pronamespace::text, function_object.proname,
                   function_object.proowner::text, function_object.prolang::text,
                   function_object.procost::text, function_object.prorows::text,
                   function_object.provariadic::text, function_object.prosupport::text,
                   function_object.prokind::text, function_object.prosecdef::text,
                   function_object.proleakproof::text, function_object.proisstrict::text,
                   function_object.proretset::text, function_object.provolatile::text,
                   function_object.proparallel::text, function_object.pronargs::text,
                   function_object.pronargdefaults::text, function_object.prorettype::text,
                   function_object.proargtypes::text,
                   COALESCE(function_object.proallargtypes::text, ''),
                   COALESCE(function_object.proargmodes::text, ''),
                   COALESCE(function_object.proargnames::text, ''),
                   COALESCE(function_object.proargdefaults::text, ''),
                   COALESCE(function_object.protrftypes::text, ''),
                   function_object.prosrc, COALESCE(function_object.probin, ''),
                   COALESCE(function_object.prosqlbody::text, ''),
                   COALESCE((SELECT pg_catalog.string_agg(setting, ',' ORDER BY setting)
                               FROM pg_catalog.unnest(function_object.proconfig) setting), '')) AS definition
    FROM pg_catalog.pg_proc function_object
   WHERE function_object.pronamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_proc'::regclass
          AND allowed.objid = function_object.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'system-allowed-extension-function',
         allowed.extension_name || ':' || function_object.proname || ':' || function_object.proargtypes::text,
         concat_ws('|', function_object.pronamespace::text, function_object.proname,
                   (function_object.proowner = allowed.extowner)::text,
                   function_object.prolang::text,
                   function_object.procost::text, function_object.prorows::text,
                   function_object.provariadic::text, function_object.prosupport::text,
                   function_object.prokind::text, function_object.prosecdef::text,
                   function_object.proleakproof::text, function_object.proisstrict::text,
                   function_object.proretset::text, function_object.provolatile::text,
                   function_object.proparallel::text, function_object.pronargs::text,
                   function_object.pronargdefaults::text, function_object.prorettype::text,
                   function_object.proargtypes::text,
                   COALESCE(function_object.proallargtypes::text, ''),
                   COALESCE(function_object.proargmodes::text, ''),
                   COALESCE(function_object.proargnames::text, ''),
                   COALESCE(function_object.proargdefaults::text, ''),
                   COALESCE(function_object.protrftypes::text, ''),
                   function_object.prosrc, COALESCE(function_object.probin, ''),
                   COALESCE(function_object.prosqlbody::text, ''),
                   COALESCE((SELECT pg_catalog.string_agg(setting, ',' ORDER BY setting)
                               FROM pg_catalog.unnest(function_object.proconfig) setting), ''))
    FROM allowed_extension_members allowed
    JOIN pg_catalog.pg_proc function_object
      ON allowed.classid = 'pg_catalog.pg_proc'::regclass
     AND allowed.objid = function_object.oid AND allowed.objsubid = 0
  UNION ALL
  SELECT 'system-allowed-extension-language',
         allowed.extension_name || ':' || language_object.lanname,
         concat_ws('|',
                   (language_object.lanowner = allowed.extowner)::text,
                   language_object.lanispl::text,
                   language_object.lanpltrusted::text,
                   COALESCE((SELECT referenced.proname || ':' || referenced.proargtypes::text
                               FROM pg_catalog.pg_proc referenced
                              WHERE referenced.oid = language_object.lanplcallfoid), ''),
                   COALESCE((SELECT referenced.proname || ':' || referenced.proargtypes::text
                               FROM pg_catalog.pg_proc referenced
                              WHERE referenced.oid = language_object.laninline), ''),
                   COALESCE((SELECT referenced.proname || ':' || referenced.proargtypes::text
                               FROM pg_catalog.pg_proc referenced
                              WHERE referenced.oid = language_object.lanvalidator), ''))
    FROM allowed_extension_members allowed
    JOIN pg_catalog.pg_language language_object
      ON allowed.classid = 'pg_catalog.pg_language'::regclass
     AND allowed.objid = language_object.oid AND allowed.objsubid = 0
  UNION ALL
  SELECT 'system-allowed-extension-event-trigger',
         allowed.extension_name || ':' || event_trigger.evtname,
         concat_ws('|', event_trigger.evtevent,
                   (event_trigger.evtowner = allowed.extowner)::text,
                   COALESCE((SELECT referenced.proname || ':' || referenced.proargtypes::text
                               FROM pg_catalog.pg_proc referenced
                              WHERE referenced.oid = event_trigger.evtfoid), ''),
                   event_trigger.evtenabled::text,
                   COALESCE((SELECT pg_catalog.string_agg(tag, ',' ORDER BY tag)
                               FROM pg_catalog.unnest(event_trigger.evttags) tag), ''))
    FROM allowed_extension_members allowed
    JOIN pg_catalog.pg_event_trigger event_trigger
      ON allowed.classid = 'pg_catalog.pg_event_trigger'::regclass
     AND allowed.objid = event_trigger.oid AND allowed.objsubid = 0
  UNION ALL
  SELECT 'system-type', type_object.oid::text,
         concat_ws('|', type_object.typname, type_object.typnamespace::text,
                   type_object.typowner::text, type_object.typlen::text,
                   type_object.typbyval::text, type_object.typtype::text,
                   type_object.typcategory::text, type_object.typispreferred::text,
                   type_object.typisdefined::text, type_object.typdelim::text,
                   type_object.typrelid::text, type_object.typsubscript::text,
                   type_object.typelem::text, type_object.typarray::text,
                   type_object.typinput::text, type_object.typoutput::text,
                   type_object.typreceive::text, type_object.typsend::text,
                   type_object.typmodin::text, type_object.typmodout::text,
                   type_object.typanalyze::text, type_object.typalign::text,
                   type_object.typstorage::text, type_object.typnotnull::text,
                   type_object.typbasetype::text, type_object.typtypmod::text,
                   type_object.typndims::text, type_object.typcollation::text,
                   COALESCE(type_object.typdefaultbin::text, ''),
                   COALESCE(type_object.typdefault, ''))
    FROM pg_catalog.pg_type type_object
   WHERE type_object.typnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_type'::regclass
          AND allowed.objid = type_object.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'system-enum-value', enum_object.oid::text,
         concat_ws('|', enum_object.enumtypid::text,
                   enum_object.enumsortorder::text, enum_object.enumlabel)
    FROM pg_catalog.pg_enum enum_object
    JOIN pg_catalog.pg_type type_object ON type_object.oid = enum_object.enumtypid
   WHERE type_object.typnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_type'::regclass
          AND allowed.objid = type_object.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'system-range', range_object.rngtypid::text,
         concat_ws('|', range_object.rngsubtype::text,
                   range_object.rngmultitypid::text, range_object.rngcollation::text,
                   range_object.rngsubopc::text, range_object.rngcanonical::text,
                   range_object.rngsubdiff::text)
    FROM pg_catalog.pg_range range_object
    JOIN pg_catalog.pg_type type_object ON type_object.oid = range_object.rngtypid
   WHERE type_object.typnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_type'::regclass
          AND allowed.objid = type_object.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'system-collation', collation_object.oid::text,
         concat_ws('|', collation_object.collname,
                   collation_object.collnamespace::text,
                   collation_object.collowner::text,
                   collation_object.collprovider::text,
                   collation_object.collisdeterministic::text,
                   collation_object.collencoding::text,
                   collation_object.collcollate,
                   collation_object.collctype,
                   COALESCE(collation_object.colliculocale, ''),
                   COALESCE(collation_object.collicurules, ''),
                   COALESCE(collation_object.collversion, ''))
    FROM pg_catalog.pg_collation collation_object
   WHERE collation_object.collnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_collation'::regclass
          AND allowed.objid = collation_object.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'system-conversion', conversion_object.oid::text,
         concat_ws('|', conversion_object.conname,
                   conversion_object.connamespace::text,
                   conversion_object.conowner::text,
                   conversion_object.conforencoding::text,
                   conversion_object.contoencoding::text,
                   conversion_object.conproc::text,
                   conversion_object.condefault::text)
    FROM pg_catalog.pg_conversion conversion_object
   WHERE conversion_object.connamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_conversion'::regclass
          AND allowed.objid = conversion_object.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'system-aggregate', aggregate_object.aggfnoid::text,
         concat_ws('|', aggregate_object.aggkind::text,
                   aggregate_object.aggnumdirectargs::text,
                   aggregate_object.aggtransfn::text,
                   aggregate_object.aggfinalfn::text,
                   aggregate_object.aggcombinefn::text,
                   aggregate_object.aggserialfn::text,
                   aggregate_object.aggdeserialfn::text,
                   aggregate_object.aggmtransfn::text,
                   aggregate_object.aggminvtransfn::text,
                   aggregate_object.aggmfinalfn::text,
                   aggregate_object.aggfinalextra::text,
                   aggregate_object.aggmfinalextra::text,
                   aggregate_object.aggfinalmodify::text,
                   aggregate_object.aggmfinalmodify::text,
                   aggregate_object.aggsortop::text,
                   aggregate_object.aggtranstype::text,
                   aggregate_object.aggtransspace::text,
                   aggregate_object.aggmtranstype::text,
                   aggregate_object.aggmtransspace::text,
                   COALESCE(aggregate_object.agginitval, ''),
                   COALESCE(aggregate_object.aggminitval, ''))
    FROM pg_catalog.pg_aggregate aggregate_object
    JOIN pg_catalog.pg_proc function_object ON function_object.oid = aggregate_object.aggfnoid
   WHERE function_object.pronamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_proc'::regclass
          AND allowed.objid = function_object.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'system-relation', relation.oid::text,
         concat_ws('|', relation.relname, relation.relnamespace::text,
                   relation.reltype::text, relation.reloftype::text,
                   relation.relowner::text, relation.relam::text,
                   relation.reltablespace::text, relation.reltoastrelid::text,
                   relation.relhasindex::text, relation.relisshared::text,
                   relation.relpersistence::text, relation.relkind::text,
                   relation.relnatts::text, relation.relchecks::text,
                   relation.relhasrules::text, relation.relhastriggers::text,
                   relation.relhassubclass::text, relation.relrowsecurity::text,
                   relation.relforcerowsecurity::text, relation.relispopulated::text,
                   relation.relreplident::text, relation.relispartition::text,
                   relation.relrewrite::text,
                   COALESCE((SELECT pg_catalog.string_agg(option_value, ',' ORDER BY option_value)
                               FROM pg_catalog.unnest(relation.reloptions) option_value), ''),
                   COALESCE(relation.relpartbound::text, ''))
    FROM pg_catalog.pg_class relation
   WHERE relation.relnamespace IN (SELECT oid FROM system_namespaces)
     AND NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_class'::regclass
          AND allowed.objid = relation.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'system-column', attribute.attrelid::text || '.' || attribute.attnum::text,
         concat_ws('|', attribute.attname, attribute.atttypid::text,
                   attribute.attstattarget::text, attribute.attlen::text,
                   attribute.attnum::text, attribute.attndims::text,
                   attribute.atttypmod::text, attribute.attbyval::text,
                   attribute.attalign::text, attribute.attstorage::text,
                   attribute.attcompression::text, attribute.attnotnull::text,
                   attribute.atthasdef::text, attribute.atthasmissing::text,
                   attribute.attidentity::text, attribute.attgenerated::text,
                   attribute.attisdropped::text, attribute.attislocal::text,
                   attribute.attinhcount::text, attribute.attcollation::text,
                   COALESCE((SELECT pg_catalog.string_agg(option_value, ',' ORDER BY option_value)
                               FROM pg_catalog.unnest(attribute.attoptions) option_value), ''),
                   COALESCE((SELECT pg_catalog.string_agg(option_value, ',' ORDER BY option_value)
                               FROM pg_catalog.unnest(attribute.attfdwoptions) option_value), ''),
                   COALESCE(attribute.attmissingval::text, ''))
    FROM pg_catalog.pg_attribute attribute
    JOIN pg_catalog.pg_class relation ON relation.oid = attribute.attrelid
   WHERE relation.relnamespace IN (SELECT oid FROM system_namespaces)
     AND attribute.attnum <> 0
  UNION ALL
  SELECT 'system-column-default', default_object.oid::text,
         concat_ws('|', default_object.adrelid::text, default_object.adnum::text,
                   default_object.adbin::text)
    FROM pg_catalog.pg_attrdef default_object
    JOIN pg_catalog.pg_class relation ON relation.oid = default_object.adrelid
   WHERE relation.relnamespace IN (SELECT oid FROM system_namespaces)
  UNION ALL
  SELECT 'system-constraint', constraint_object.oid::text,
         concat_ws('|', constraint_object.conname, constraint_object.connamespace::text,
                   constraint_object.contype::text, constraint_object.condeferrable::text,
                   constraint_object.condeferred::text, constraint_object.convalidated::text,
                   constraint_object.conrelid::text, constraint_object.contypid::text,
                   constraint_object.conindid::text, constraint_object.conparentid::text,
                   constraint_object.confrelid::text, constraint_object.confupdtype::text,
                   constraint_object.confdeltype::text, constraint_object.confmatchtype::text,
                   constraint_object.conislocal::text, constraint_object.coninhcount::text,
                   constraint_object.connoinherit::text,
                   COALESCE(constraint_object.conkey::text, ''),
                   COALESCE(constraint_object.confkey::text, ''),
                   COALESCE(constraint_object.conpfeqop::text, ''),
                   COALESCE(constraint_object.conppeqop::text, ''),
                   COALESCE(constraint_object.conffeqop::text, ''),
                   COALESCE(constraint_object.confdelsetcols::text, ''),
                   COALESCE(constraint_object.conexclop::text, ''),
                   COALESCE(constraint_object.conbin::text, ''))
    FROM pg_catalog.pg_constraint constraint_object
    LEFT JOIN pg_catalog.pg_class relation ON relation.oid = constraint_object.conrelid
    LEFT JOIN pg_catalog.pg_type type_object ON type_object.oid = constraint_object.contypid
   WHERE relation.relnamespace IN (SELECT oid FROM system_namespaces)
      OR type_object.typnamespace IN (SELECT oid FROM system_namespaces)
  UNION ALL
  SELECT 'system-index', index_object.indexrelid::text,
         concat_ws('|', index_object.indrelid::text, index_object.indnatts::text,
                   index_object.indnkeyatts::text, index_object.indisunique::text,
                   index_object.indnullsnotdistinct::text, index_object.indisprimary::text,
                   index_object.indisexclusion::text, index_object.indimmediate::text,
                   index_object.indisclustered::text, index_object.indisvalid::text,
                   index_object.indcheckxmin::text, index_object.indisready::text,
                   index_object.indislive::text, index_object.indisreplident::text,
                   index_object.indkey::text, index_object.indcollation::text,
                   index_object.indclass::text, index_object.indoption::text,
                   COALESCE(index_object.indexprs::text, ''),
                   COALESCE(index_object.indpred::text, ''))
    FROM pg_catalog.pg_index index_object
    JOIN pg_catalog.pg_class relation ON relation.oid = index_object.indrelid
   WHERE relation.relnamespace IN (SELECT oid FROM system_namespaces)
  UNION ALL
  SELECT 'system-rule', rewrite.oid::text,
         concat_ws('|', rewrite.rulename, rewrite.ev_class::text,
                   rewrite.ev_type::text, rewrite.ev_enabled::text,
                   rewrite.is_instead::text, rewrite.ev_qual::text,
                   rewrite.ev_action::text)
    FROM pg_catalog.pg_rewrite rewrite
    JOIN pg_catalog.pg_class relation ON relation.oid = rewrite.ev_class
   WHERE relation.relnamespace IN (SELECT oid FROM system_namespaces)
  UNION ALL
  SELECT 'system-trigger', trigger_object.oid::text,
         concat_ws('|', trigger_object.tgrelid::text, trigger_object.tgparentid::text,
                   trigger_object.tgname, trigger_object.tgfoid::text,
                   trigger_object.tgtype::text, trigger_object.tgenabled::text,
                   trigger_object.tgisinternal::text, trigger_object.tgconstrrelid::text,
                   trigger_object.tgconstrindid::text, trigger_object.tgconstraint::text,
                   trigger_object.tgdeferrable::text, trigger_object.tginitdeferred::text,
                   trigger_object.tgnargs::text, trigger_object.tgattr::text,
                   trigger_object.tgargs::text, COALESCE(trigger_object.tgqual::text, ''),
                   COALESCE(trigger_object.tgoldtable, ''),
                   COALESCE(trigger_object.tgnewtable, ''))
    FROM pg_catalog.pg_trigger trigger_object
    JOIN pg_catalog.pg_class relation ON relation.oid = trigger_object.tgrelid
   WHERE relation.relnamespace IN (SELECT oid FROM system_namespaces)
  UNION ALL
  SELECT 'system-operator', operator_object.oid::text,
         concat_ws('|', operator_object.oprname, operator_object.oprnamespace::text,
                   operator_object.oprowner::text, operator_object.oprkind::text,
                   operator_object.oprcanmerge::text, operator_object.oprcanhash::text,
                   operator_object.oprleft::text, operator_object.oprright::text,
                   operator_object.oprresult::text, operator_object.oprcom::text,
                   operator_object.oprnegate::text, operator_object.oprcode::text,
                   operator_object.oprrest::text, operator_object.oprjoin::text)
    FROM pg_catalog.pg_operator operator_object
   WHERE operator_object.oprnamespace IN (SELECT oid FROM system_namespaces)
  UNION ALL
  SELECT 'system-operator-family', operator_family.oid::text,
         concat_ws('|', operator_family.opfmethod::text, operator_family.opfname,
                   operator_family.opfnamespace::text, operator_family.opfowner::text)
    FROM pg_catalog.pg_opfamily operator_family
   WHERE operator_family.opfnamespace IN (SELECT oid FROM system_namespaces)
  UNION ALL
  SELECT 'system-operator-class', operator_class.oid::text,
         concat_ws('|', operator_class.opcmethod::text, operator_class.opcname,
                   operator_class.opcnamespace::text, operator_class.opcowner::text,
                   operator_class.opcfamily::text, operator_class.opcintype::text,
                   operator_class.opcdefault::text, operator_class.opckeytype::text)
    FROM pg_catalog.pg_opclass operator_class
   WHERE operator_class.opcnamespace IN (SELECT oid FROM system_namespaces)
  UNION ALL
  SELECT 'system-operator-family-operator', operator_member.oid::text,
         concat_ws('|', operator_member.amopfamily::text,
                   operator_member.amoplefttype::text, operator_member.amoprighttype::text,
                   operator_member.amopstrategy::text, operator_member.amoppurpose::text,
                   operator_member.amopopr::text, operator_member.amopmethod::text,
                   operator_member.amopsortfamily::text)
    FROM pg_catalog.pg_amop operator_member
    JOIN pg_catalog.pg_opfamily operator_family ON operator_family.oid = operator_member.amopfamily
   WHERE operator_family.opfnamespace IN (SELECT oid FROM system_namespaces)
  UNION ALL
  SELECT 'system-operator-family-function', operator_function.oid::text,
         concat_ws('|', operator_function.amprocfamily::text,
                   operator_function.amproclefttype::text, operator_function.amprocrighttype::text,
                   operator_function.amprocnum::text, operator_function.amproc::text)
    FROM pg_catalog.pg_amproc operator_function
    JOIN pg_catalog.pg_opfamily operator_family ON operator_family.oid = operator_function.amprocfamily
   WHERE operator_family.opfnamespace IN (SELECT oid FROM system_namespaces)
  UNION ALL
  SELECT 'system-text-search-config', text_config.oid::text,
         concat_ws('|', text_config.cfgname, text_config.cfgnamespace::text,
                   text_config.cfgowner::text, text_config.cfgparser::text)
    FROM pg_catalog.pg_ts_config text_config
   WHERE text_config.cfgnamespace IN (SELECT oid FROM system_namespaces)
  UNION ALL
  SELECT 'system-text-search-config-map',
         config_map.mapcfg::text || '.' || config_map.maptokentype::text || '.' || config_map.mapseqno::text,
         config_map.mapdict::text
    FROM pg_catalog.pg_ts_config_map config_map
    JOIN pg_catalog.pg_ts_config text_config ON text_config.oid = config_map.mapcfg
   WHERE text_config.cfgnamespace IN (SELECT oid FROM system_namespaces)
  UNION ALL
  SELECT 'system-text-search-dictionary', dictionary_object.oid::text,
         concat_ws('|', dictionary_object.dictname, dictionary_object.dictnamespace::text,
                   dictionary_object.dictowner::text, dictionary_object.dicttemplate::text,
                   COALESCE(dictionary_object.dictinitoption, ''))
    FROM pg_catalog.pg_ts_dict dictionary_object
   WHERE dictionary_object.dictnamespace IN (SELECT oid FROM system_namespaces)
  UNION ALL
  SELECT 'system-text-search-parser', parser_object.oid::text,
         concat_ws('|', parser_object.prsname, parser_object.prsnamespace::text,
                   parser_object.prsstart::text, parser_object.prstoken::text,
                   parser_object.prsend::text, parser_object.prsheadline::text,
                   parser_object.prslextype::text)
    FROM pg_catalog.pg_ts_parser parser_object
   WHERE parser_object.prsnamespace IN (SELECT oid FROM system_namespaces)
  UNION ALL
  SELECT 'system-text-search-template', template_object.oid::text,
         concat_ws('|', template_object.tmplname, template_object.tmplnamespace::text,
                   template_object.tmplinit::text, template_object.tmpllexize::text)
    FROM pg_catalog.pg_ts_template template_object
   WHERE template_object.tmplnamespace IN (SELECT oid FROM system_namespaces)
  UNION ALL
  SELECT 'system-cast', cast_object.oid::text,
         concat_ws('|', cast_object.castsource::text, cast_object.casttarget::text,
                   cast_object.castfunc::text, cast_object.castcontext::text,
                   cast_object.castmethod::text)
    FROM pg_catalog.pg_cast cast_object
   WHERE cast_object.oid < 16384
  UNION ALL
  SELECT 'system-language', language_object.oid::text,
         concat_ws('|', language_object.lanname, language_object.lanowner::text,
                   language_object.lanispl::text, language_object.lanpltrusted::text,
                   language_object.lanplcallfoid::text, language_object.laninline::text,
                   language_object.lanvalidator::text)
    FROM pg_catalog.pg_language language_object
   WHERE NOT EXISTS (
       SELECT 1 FROM allowed_extension_members allowed
        WHERE allowed.classid = 'pg_catalog.pg_language'::regclass
          AND allowed.objid = language_object.oid AND allowed.objsubid = 0
     )
  UNION ALL
  SELECT 'system-access-method', access_method.oid::text,
         concat_ws('|', access_method.amname, access_method.amhandler::text,
                   access_method.amtype::text)
    FROM pg_catalog.pg_am access_method
   WHERE access_method.oid < 16384
  UNION ALL
  SELECT 'system-transform', transform_object.oid::text,
         concat_ws('|', transform_object.trftype::text, transform_object.trflang::text,
                   transform_object.trffromsql::text, transform_object.trftosql::text)
    FROM pg_catalog.pg_transform transform_object
   WHERE transform_object.oid < 16384
)
SELECT kind, identity, definition
  FROM catalog_objects
 ORDER BY kind, identity, definition`
