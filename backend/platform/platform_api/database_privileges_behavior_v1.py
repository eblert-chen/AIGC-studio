"""Frozen PostgreSQL 16 attestation behavior for Platform Alembic 0036.

This module and :mod:`database_privileges_v1` are one historical security
artifact.  Never edit either for a later schema or privilege policy.  Add a
new versioned behavior/policy pair and repoint the runtime facade instead;
revision 0036 must continue importing this module directly.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from threading import Lock
from typing import Iterable
from weakref import WeakKeyDictionary

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from . import database_privileges_v1 as policy_v1
from .database_system_semantic_v1 import (
    POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256,
    platform_postgres16_allowed_extension_surface_is_exact,
    platform_postgres16_audit_configuration,
    platform_postgres16_release_evidence_is_qualified,
    platform_postgres16_system_semantic_sha256,
)


class PlatformDatabaseAttestationError(RuntimeError):
    """A value-free protected-runtime database boundary failure."""


_engine_attestation_lock = Lock()
_engine_attestation_roles: WeakKeyDictionary[Engine, str] = WeakKeyDictionary()


@dataclass(frozen=True)
class DatabasePrincipalEvidence:
    role_name: str
    role_comment: str | None
    can_login: bool
    is_superuser: bool
    inherits: bool
    can_create_role: bool
    can_create_database: bool
    can_replicate: bool
    bypasses_rls: bool
    connection_limit: int
    credential_validity_ok: bool


@dataclass(frozen=True)
class PlatformDatabaseEvidence:
    current_user: str
    session_user: str
    ssl_active: bool
    current_schema: str | None
    explicit_schemas: tuple[str, ...]
    database_owner: str
    public_schema_owner: str
    principals: tuple[DatabasePrincipalEvidence, ...]
    membership_count: int
    role_setting_count: int
    parameter_acl_count: int
    external_owned_object_count: int
    cross_database_acl_count: int
    cross_database_dependency_count: int
    global_role_dependency_count: int
    system_acl_count: int
    system_acl_sha256: str
    system_semantic_sha256: str
    system_extension_surface_exact: bool
    pgaudit_preloaded: bool
    pgaudit_log_class_coverage: bool
    credential_logging_policy_exact: bool
    system_unsafe_object_count: int
    public_unsafe_object_count: int
    legacy_pending_work_count: int
    foreign_owned_object_count: int
    column_acl_count: int
    database_acl: frozenset[tuple[str, str, str, bool]]
    schema_acl: frozenset[tuple[str, str, str, bool]]
    table_names: frozenset[str]
    table_acl: frozenset[tuple[str, str, str, str, bool]]
    sequence_acl: frozenset[tuple[str, str, str, bool]]
    routine_acl: frozenset[tuple[str, str, str, bool]]
    default_acl: frozenset[tuple[str, str, str, str, str, str, bool]]
    catalog_sha256: str
    alembic_heads: tuple[str, ...]


def protected_platform_runtime_requested_v1() -> bool:
    outer_environment = os.environ.get("ENVIRONMENT", "")
    outer_protected = outer_environment in {
        "production",
        "staging",
    }
    if (
        not outer_protected
        and outer_environment.strip().lower() in {"production", "staging"}
    ):
        raise PlatformDatabaseAttestationError(
            "protected Platform environment is invalid"
        )
    explicit = os.environ.get("PLATFORM_PROTECTED_RUNTIME")
    if outer_protected:
        if explicit is not None and explicit != "true":
            raise PlatformDatabaseAttestationError(
                "protected Platform runtime cannot be disabled"
            )
        return True
    if explicit is None or explicit == "false":
        return False
    if explicit == "true":
        return True
    raise PlatformDatabaseAttestationError(
        "protected Platform runtime flag is invalid"
    )


def _protected_database_release_environment_v1() -> str:
    environment = os.environ.get("ENVIRONMENT", "")
    if environment in {"staging", "production"}:
        return environment
    # An explicit protected runtime outside the two deploy labels receives the
    # stricter production database gate.  It can never opt into rehearsal by
    # inventing another environment name.
    return "production"


def _fail(invariant: str) -> None:
    raise PlatformDatabaseAttestationError(
        f"protected Platform database attestation failed: {invariant}"
    )


def _require_frozen_policy(policy) -> None:
    if policy is not policy_v1:
        _fail("policy version")


def expected_platform_table_acl(
    policy=policy_v1,
) -> frozenset[tuple[str, str, str]]:
    _require_frozen_policy(policy)
    return policy_v1.EXPECTED_TABLE_ACL


def _validate_principals(
    principals: Iterable[DatabasePrincipalEvidence],
    *,
    policy=policy_v1,
) -> None:
    _require_frozen_policy(policy)
    by_name = {principal.role_name: principal for principal in principals}
    expected_names = set(policy_v1.DATABASE_ROLE_BY_PROCESS.values())
    if set(by_name) != expected_names:
        _fail("principal inventory")
    process_by_role = {
        database_role: process_role
        for process_role, database_role in policy_v1.DATABASE_ROLE_BY_PROCESS.items()
    }
    for role_name, principal in by_name.items():
        process_role = process_by_role[role_name]
        if (
            principal.role_comment
            != policy_v1.DATABASE_ROLE_COMMENT_BY_PROCESS[process_role]
            or not principal.can_login
            or principal.is_superuser
            or principal.inherits
            or principal.can_create_role
            or principal.can_create_database
            or principal.can_replicate
            or principal.bypasses_rls
            or principal.connection_limit
            != policy_v1.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS[process_role]
            or not principal.credential_validity_ok
        ):
            _fail("principal properties")


def validate_platform_database_evidence(
    evidence: PlatformDatabaseEvidence,
    process_role: str,
    *,
    require_runtime_acl: bool,
    require_head: bool,
    policy=policy_v1,
) -> None:
    _require_frozen_policy(policy)
    if process_role not in policy_v1.DATABASE_ROLE_BY_PROCESS:
        _fail("process role")
    expected_user = policy_v1.DATABASE_ROLE_BY_PROCESS[process_role]
    if evidence.current_user != expected_user or evidence.session_user != expected_user:
        _fail("session identity")
    if not evidence.ssl_active:
        _fail("transport")
    if evidence.current_schema != "public" or evidence.explicit_schemas != (
        "public",
    ):
        _fail("schema search path")
    if evidence.database_owner != policy_v1.MIGRATION_DATABASE_ROLE:
        _fail("database owner")
    if evidence.public_schema_owner not in {
        policy_v1.MIGRATION_DATABASE_ROLE,
        "pg_database_owner",
    }:
        _fail("schema owner")
    _validate_principals(evidence.principals, policy=policy)
    for count, invariant in (
        (evidence.membership_count, "role memberships"),
        (evidence.role_setting_count, "role settings"),
        (evidence.parameter_acl_count, "parameter privileges"),
        (evidence.external_owned_object_count, "global object inventory"),
        (evidence.cross_database_acl_count, "cross-database privileges"),
        (
            evidence.cross_database_dependency_count,
            "cross-database dependencies",
        ),
        (evidence.global_role_dependency_count, "global role dependencies"),
        (evidence.system_acl_count, "system catalog privileges"),
        (evidence.system_unsafe_object_count, "system object inventory"),
        (evidence.public_unsafe_object_count, "public unsafe objects"),
        (evidence.legacy_pending_work_count, "legacy Relay pending work"),
        (evidence.foreign_owned_object_count, "object ownership"),
        (evidence.column_acl_count, "column privileges"),
    ):
        if count:
            _fail(invariant)
    if not platform_postgres16_release_evidence_is_qualified(
        environment=_protected_database_release_environment_v1(),
        system_semantic_sha256=evidence.system_semantic_sha256,
        extension_surface_exact=evidence.system_extension_surface_exact,
        pgaudit_preloaded=evidence.pgaudit_preloaded,
        pgaudit_log_class_coverage=evidence.pgaudit_log_class_coverage,
    ):
        _fail("system catalog semantics")
    if not evidence.credential_logging_policy_exact:
        _fail("credential logging policy")
    if not require_runtime_acl:
        if evidence.alembic_heads == (policy_v1.ALEMBIC_HEAD,):
            validate_platform_database_acl_evidence(
                evidence,
                require_head=True,
                policy=policy,
            )
            return
        if evidence.default_acl:
            _fail("pre-migration default privileges")
        return
    if process_role == "migration":
        _fail("migration role runtime use")
    validate_platform_database_acl_evidence(
        evidence,
        require_head=require_head,
        policy=policy,
    )


def validate_platform_database_acl_evidence(
    evidence: PlatformDatabaseEvidence,
    *,
    require_head: bool,
    policy=policy_v1,
) -> None:
    _require_frozen_policy(policy)
    normalized_database_acl = frozenset(
        (grantee, privilege)
        for grantee, privilege, _, _ in evidence.database_acl
    )
    if (
        normalized_database_acl != policy_v1.EXPECTED_DATABASE_ACL
        or any(
            grantor != policy_v1.MIGRATION_DATABASE_ROLE or is_grantable
            for _, _, grantor, is_grantable in evidence.database_acl
        )
    ):
        _fail("database privileges")
    normalized_schema_acl = frozenset(
        (grantee, privilege)
        for grantee, privilege, _, _ in evidence.schema_acl
    )
    if (
        normalized_schema_acl != policy_v1.EXPECTED_SCHEMA_ACL
        or any(
            grantor
            not in {policy_v1.MIGRATION_DATABASE_ROLE, "pg_database_owner"}
            or is_grantable
            for _, _, grantor, is_grantable in evidence.schema_acl
        )
    ):
        _fail("schema privileges")
    if evidence.table_names != policy_v1.TABLES | {"alembic_version"}:
        _fail("table inventory")
    normalized_table_acl = frozenset(
        (table_name, grantee, privilege)
        for table_name, grantee, privilege, _, _ in evidence.table_acl
    )
    if (
        normalized_table_acl != policy_v1.EXPECTED_TABLE_ACL
        or any(
            grantor != policy_v1.MIGRATION_DATABASE_ROLE or is_grantable
            for _, _, _, grantor, is_grantable in evidence.table_acl
        )
    ):
        _fail("table privileges")
    if evidence.sequence_acl:
        _fail("sequence privileges")
    if evidence.routine_acl:
        _fail("routine privileges")
    if evidence.default_acl != policy_v1.EXPECTED_DEFAULT_ACL:
        _fail("default privileges")
    if evidence.catalog_sha256 != policy_v1.CATALOG_SHA256:
        _fail("catalog fingerprint")
    if require_head and evidence.alembic_heads != (policy_v1.ALEMBIC_HEAD,):
        _fail("migration head")


def _role_predicate(prefix: str) -> tuple[str, dict[str, str]]:
    values = tuple(policy_v1.DATABASE_ROLE_BY_PROCESS.values())
    parameters = {f"{prefix}_{index}": value for index, value in enumerate(values)}
    return ", ".join(f":{name}" for name in parameters), parameters


def _acl_rows(
    connection: Connection,
    *,
    catalog_sql: str,
) -> frozenset[tuple[str, str, str, bool]]:
    return frozenset(
        (str(row[0]), str(row[1]).upper(), str(row[2]), bool(row[3]))
        for row in connection.execute(text(catalog_sql)).all()
    )


_SYSTEM_ACL_PROJECTION_SQL = (
    "WITH objects AS ("
    "SELECT 'namespace'::text kind, n.nspname::text identity, "
    "'pg_namespace'::regclass classoid, n.oid objoid, n.nspowner owner, "
    "n.nspacl acl, 'n'::\"char\" acl_kind FROM pg_namespace n "
    "WHERE n.nspname IN ('pg_catalog','information_schema','pg_toast') "
    "UNION ALL SELECT 'relation', n.nspname||'.'||c.relname||':'||c.relkind::text, "
    "'pg_class'::regclass, c.oid, c.relowner, c.relacl, "
    "CASE WHEN c.relkind='S' THEN 'S'::\"char\" ELSE 'r'::\"char\" END "
    "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
    "WHERE n.nspname IN ('pg_catalog','information_schema') "
    "UNION ALL SELECT 'routine', n.nspname||'.'||p.proname||'('||"
    "pg_get_function_identity_arguments(p.oid)||')', 'pg_proc'::regclass, "
    "p.oid, p.proowner, p.proacl, 'f'::\"char\" FROM pg_proc p "
    "JOIN pg_namespace n ON n.oid=p.pronamespace "
    "WHERE n.nspname IN ('pg_catalog','information_schema') "
    "UNION ALL SELECT 'type', n.nspname||'.'||t.typname, 'pg_type'::regclass, "
    "t.oid, t.typowner, t.typacl, 'T'::\"char\" FROM pg_type t "
    "JOIN pg_namespace n ON n.oid=t.typnamespace "
    "WHERE n.nspname IN ('pg_catalog','information_schema') "
    "UNION ALL SELECT 'language', l.lanname, 'pg_language'::regclass, l.oid, "
    "l.lanowner, l.lanacl, 'l'::\"char\" FROM pg_language l "
    "UNION ALL SELECT 'tablespace', s.spcname, 'pg_tablespace'::regclass, "
    "s.oid, s.spcowner, s.spcacl, 't'::\"char\" FROM pg_tablespace s), "
    "projection AS ("
    "SELECT 'current'::text source, o.kind, o.identity, "
    "COALESCE(grantee.rolname,'PUBLIC') grantee, pg_get_userbyid(a.grantor) grantor, "
    "a.privilege_type, a.is_grantable FROM objects o CROSS JOIN LATERAL "
    "aclexplode(COALESCE(o.acl,acldefault(o.acl_kind,o.owner))) a "
    "LEFT JOIN pg_roles grantee ON grantee.oid=a.grantee "
    "UNION ALL SELECT 'default', o.kind, o.identity, "
    "COALESCE(grantee.rolname,'PUBLIC'), pg_get_userbyid(a.grantor), "
    "a.privilege_type, a.is_grantable FROM objects o CROSS JOIN LATERAL "
    "aclexplode(acldefault(o.acl_kind,o.owner)) a "
    "LEFT JOIN pg_roles grantee ON grantee.oid=a.grantee "
    "UNION ALL SELECT 'init', o.kind, o.identity, "
    "COALESCE(grantee.rolname,'PUBLIC'), pg_get_userbyid(a.grantor), "
    "a.privilege_type, a.is_grantable FROM objects o JOIN pg_init_privs i "
    "ON i.classoid=o.classoid AND i.objoid=o.objoid AND i.objsubid=0 "
    "AND i.privtype='i' CROSS JOIN LATERAL aclexplode(i.initprivs) a "
    "LEFT JOIN pg_roles grantee ON grantee.oid=a.grantee) "
    "SELECT source,kind,identity,grantee,grantor,privilege_type,is_grantable "
    "FROM projection ORDER BY source,kind,identity,grantee,grantor,privilege_type,is_grantable"
)


def platform_system_acl_sha256(connection: Connection) -> str:
    digest = hashlib.sha256()
    for row in connection.execute(text(_SYSTEM_ACL_PROJECTION_SQL)).all():
        digest.update(
            json.dumps(
                list(row),
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


_CATALOG_PROJECTIONS = (
    (
        "relation",
        "SELECT c.relname, c.relkind::text, c.relpersistence::text, "
        "c.relrowsecurity, c.relforcerowsecurity, c.relreplident::text, "
        "COALESCE(array_to_string(c.reloptions, ','), ''), "
        "COALESCE(ts.spcname, ''), "
        "COALESCE(regexp_replace(toast.relname, "
        "'pg_toast_[0-9]+', 'pg_toast_<oid>', 'g'), ''), "
        "COALESCE(toast_ts.spcname, ''), "
        "COALESCE(array_to_string(toast.reloptions, ','), ''), "
        "COALESCE((SELECT string_agg("
        "regexp_replace(ti.relname, 'pg_toast_[0-9]+', 'pg_toast_<oid>', 'g')"
        "||':'||regexp_replace(pg_get_indexdef(ix.indexrelid,0,true), "
        "'pg_toast_[0-9]+', 'pg_toast_<oid>', 'g'), ',' ORDER BY "
        "regexp_replace(ti.relname, 'pg_toast_[0-9]+', 'pg_toast_<oid>', 'g')) "
        "FROM pg_index ix JOIN pg_class ti "
        "ON ti.oid=ix.indexrelid WHERE ix.indrelid=toast.oid), '') "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "LEFT JOIN pg_tablespace ts ON ts.oid = c.reltablespace "
        "LEFT JOIN pg_class toast ON toast.oid = c.reltoastrelid "
        "LEFT JOIN pg_tablespace toast_ts ON toast_ts.oid = toast.reltablespace "
        "WHERE n.nspname = 'public' "
        "AND c.relkind IN ('r','p','v','m','S','i','I') "
        "ORDER BY c.relname, c.relkind",
    ),
    (
        "column",
        "SELECT c.relname, a.attnum, a.attname, "
        "format_type(a.atttypid, a.atttypmod), a.attnotnull, "
        "a.attidentity::text, a.attgenerated::text, a.attstorage::text, "
        "COALESCE(coll.collname, ''), "
        "COALESCE(pg_get_expr(d.adbin, d.adrelid, true), '') "
        "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
        "LEFT JOIN pg_collation coll ON coll.oid = a.attcollation "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m') "
        "AND a.attnum > 0 AND NOT a.attisdropped "
        "ORDER BY c.relname, a.attnum",
    ),
    (
        "constraint",
        "SELECT c.relname, con.conname, con.contype::text, con.condeferrable, "
        "con.condeferred, con.convalidated, "
        "pg_get_constraintdef(con.oid, true) "
        "FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' ORDER BY c.relname, con.conname",
    ),
    (
        "index",
        "SELECT t.relname, i.relname, x.indisunique, x.indisprimary, "
        "x.indisexclusion, x.indimmediate, x.indisvalid, x.indisready, "
        "pg_get_indexdef(x.indexrelid, 0, true) "
        "FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid "
        "JOIN pg_class t ON t.oid = x.indrelid "
        "JOIN pg_namespace n ON n.oid = t.relnamespace "
        "WHERE n.nspname = 'public' ORDER BY t.relname, i.relname",
    ),
    (
        "trigger",
        "SELECT c.relname, t.tgname, t.tgenabled::text, t.tgisinternal, "
        "t.tgtype, t.tgdeferrable, t.tginitdeferred, "
        "COALESCE(con.conname, ''), pg_get_triggerdef(t.oid, true) "
        "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "LEFT JOIN pg_constraint con ON con.oid = t.tgconstraint "
        # PostgreSQL gives FK enforcement triggers OID-derived names. Their
        # semantics are already frozen by the constraint projection; keeping
        # the internal names here makes equal fresh databases hash differently.
        # User-created triggers remain fully projected.
        "WHERE n.nspname = 'public' AND NOT t.tgisinternal "
        "ORDER BY c.relname, t.tgname",
    ),
    (
        "rewrite",
        "SELECT c.relname, r.rulename, r.ev_type::text, r.is_instead, "
        "r.ev_enabled::text, pg_get_ruledef(r.oid, true) "
        "FROM pg_rewrite r JOIN pg_class c ON c.oid = r.ev_class "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' ORDER BY c.relname, r.rulename",
    ),
    (
        "routine",
        "SELECT p.proname, pg_get_function_identity_arguments(p.oid), "
        "p.prokind::text, p.prosecdef, p.proleakproof, p.provolatile::text, "
        "p.proparallel::text, p.proisstrict, p.proretset, "
        "pg_get_function_result(p.oid), l.lanname, pg_get_functiondef(p.oid) "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "JOIN pg_language l ON l.oid = p.prolang "
        "WHERE n.nspname = 'public' "
        "ORDER BY p.proname, pg_get_function_identity_arguments(p.oid)",
    ),
    (
        "view",
        "SELECT c.relname, pg_get_viewdef(c.oid, true) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('v','m') "
        "ORDER BY c.relname",
    ),
    (
        "policy",
        "SELECT c.relname, p.polname, p.polpermissive, p.polcmd::text, "
        "COALESCE((SELECT string_agg(CASE WHEN role_oid = 0 THEN 'PUBLIC' "
        "ELSE pg_get_userbyid(role_oid) END, ',' ORDER BY role_oid) "
        "FROM unnest(p.polroles) role_oid), ''), "
        "COALESCE(pg_get_expr(p.polqual, p.polrelid, true), ''), "
        "COALESCE(pg_get_expr(p.polwithcheck, p.polrelid, true), '') "
        "FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' ORDER BY c.relname, p.polname",
    ),
    (
        "enum",
        "SELECT t.typname, e.enumsortorder::text, e.enumlabel "
        "FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
        "JOIN pg_namespace n ON n.oid = t.typnamespace "
        "WHERE n.nspname = 'public' ORDER BY t.typname, e.enumsortorder",
    ),
    (
        "type",
        "SELECT t.typname, t.typtype::text, t.typcategory::text, "
        "t.typispreferred, t.typnotnull, "
        "COALESCE(format_type(NULLIF(t.typbasetype, 0), t.typtypmod), ''), "
        "COALESCE(c.relname, ''), COALESCE(format_type(NULLIF(t.typelem, 0), NULL), ''), "
        "COALESCE(format_type(NULLIF(r.rngsubtype, 0), NULL), ''), "
        "COALESCE(r.rngcollation::regcollation::text, '') "
        "FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
        "LEFT JOIN pg_class c ON c.oid = t.typrelid "
        "LEFT JOIN pg_range r ON r.rngtypid = t.oid "
        "WHERE n.nspname = 'public' ORDER BY t.typname",
    ),
    (
        "type_constraint",
        "SELECT t.typname, con.conname, con.contype::text, con.convalidated, "
        "pg_get_constraintdef(con.oid, true) "
        "FROM pg_constraint con JOIN pg_type t ON t.oid = con.contypid "
        "JOIN pg_namespace n ON n.oid = t.typnamespace "
        "WHERE n.nspname = 'public' ORDER BY t.typname, con.conname",
    ),
    (
        "operator",
        "SELECT o.oprname, format_type(o.oprleft, NULL), "
        "format_type(o.oprright, NULL), format_type(o.oprresult, NULL), "
        "o.oprcode::regprocedure::text "
        "FROM pg_operator o JOIN pg_namespace n ON n.oid = o.oprnamespace "
        "WHERE n.nspname = 'public' ORDER BY o.oprname, o.oprleft, o.oprright",
    ),
    (
        "cast",
        "SELECT format_type(c.castsource, NULL), format_type(c.casttarget, NULL), "
        "c.castcontext::text, c.castmethod::text, "
        "CASE WHEN c.castfunc = 0 THEN '' ELSE c.castfunc::regprocedure::text END "
        "FROM pg_cast c JOIN pg_type source ON source.oid = c.castsource "
        "JOIN pg_namespace source_ns ON source_ns.oid = source.typnamespace "
        "JOIN pg_type target ON target.oid = c.casttarget "
        "JOIN pg_namespace target_ns ON target_ns.oid = target.typnamespace "
        "WHERE source_ns.nspname = 'public' OR target_ns.nspname = 'public' "
        "ORDER BY format_type(c.castsource, NULL), format_type(c.casttarget, NULL)",
    ),
    (
        "operator_class",
        "SELECT opc.opcname, am.amname, opc.opcdefault, "
        "format_type(opc.opcintype, NULL), format_type(opc.opckeytype, NULL) "
        "FROM pg_opclass opc JOIN pg_namespace n ON n.oid = opc.opcnamespace "
        "JOIN pg_am am ON am.oid = opc.opcmethod "
        "WHERE n.nspname = 'public' ORDER BY opc.opcname, am.amname",
    ),
    (
        "operator_family",
        "SELECT opf.opfname, am.amname FROM pg_opfamily opf "
        "JOIN pg_namespace n ON n.oid = opf.opfnamespace "
        "JOIN pg_am am ON am.oid = opf.opfmethod "
        "WHERE n.nspname = 'public' ORDER BY opf.opfname, am.amname",
    ),
    (
        "partition",
        "SELECT c.relname, COALESCE(pg_get_partkeydef(c.oid), ''), "
        "COALESCE(parent.relname, '') "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "LEFT JOIN pg_inherits inh ON inh.inhrelid = c.oid "
        "LEFT JOIN pg_class parent ON parent.oid = inh.inhparent "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r','p') "
        "ORDER BY c.relname, parent.relname",
    ),
    (
        "sequence",
        "SELECT c.relname, format_type(s.seqtypid, NULL), s.seqstart, "
        "s.seqincrement, s.seqmax, s.seqmin, s.seqcache, s.seqcycle "
        "FROM pg_sequence s JOIN pg_class c ON c.oid = s.seqrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' ORDER BY c.relname",
    ),
    (
        "default_acl",
        "SELECT pg_get_userbyid(d.defaclrole), COALESCE(n.nspname, ''), "
        "d.defaclobjtype::text, COALESCE(grantee.rolname, 'PUBLIC'), "
        "acl.privilege_type, pg_get_userbyid(acl.grantor), acl.is_grantable "
        "FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace "
        "CROSS JOIN LATERAL aclexplode(d.defaclacl) acl "
        "LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee "
        "ORDER BY 1, 2, 3, 4, 5",
    ),
)


def platform_catalog_sha256(connection: Connection) -> str:
    digest = hashlib.sha256()
    for category, statement in _CATALOG_PROJECTIONS:
        for row in connection.execute(text(statement)).all():
            digest.update(
                json.dumps(
                    [category, *tuple(row)],
                    ensure_ascii=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            digest.update(b"\n")
    return digest.hexdigest()


def validate_platform_migration_source_state(
    connection: Connection,
    *,
    policy=policy_v1,
) -> None:
    """Reject a dirty/unknown public schema before Alembic can execute DDL."""

    _require_frozen_policy(policy)
    table_names = frozenset(
        str(value)
        for value in connection.scalars(
            text(
                "SELECT c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relkind IN ('r','p')"
            )
        )
    )
    catalog_sha256 = platform_catalog_sha256(connection)
    if "alembic_version" not in table_names:
        if table_names or catalog_sha256 != policy_v1.EMPTY_CATALOG_SHA256:
            _fail("migration source catalog")
        return
    heads = tuple(
        str(value)
        for value in connection.scalars(
            text("SELECT version_num FROM public.alembic_version ORDER BY version_num")
        )
    )
    if len(heads) != 1:
        _fail("migration source head")
    expected = policy_v1.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD.get(heads[0])
    if expected is None or catalog_sha256 != expected:
        _fail("migration source catalog")


def collect_platform_database_evidence(
    connection: Connection,
    *,
    policy=policy_v1,
) -> PlatformDatabaseEvidence:
    _require_frozen_policy(policy)
    identity = connection.execute(
        text(
            "SELECT current_user, session_user, "
            "COALESCE((SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()), false), "
            "current_schema(), current_schemas(false), "
            "pg_get_userbyid(d.datdba), pg_get_userbyid(n.nspowner) "
            "FROM pg_database d JOIN pg_namespace n ON n.nspname = 'public' "
            "WHERE d.datname = current_database()"
        )
    ).one()
    placeholders, role_parameters = _role_predicate("principal")
    principal_rows = connection.execute(
        text(
            "SELECT r.rolname, shobj_description(r.oid, 'pg_authid'), "
            "r.rolcanlogin, r.rolsuper, r.rolinherit, r.rolcreaterole, "
            "r.rolcreatedb, r.rolreplication, r.rolbypassrls, r.rolconnlimit, "
            "r.rolvaliduntil IS NOT NULL "
            "AND r.rolvaliduntil > statement_timestamp() + interval '24 hours' "
            "AND r.rolvaliduntil <= statement_timestamp() + interval '366 days' "
            f"FROM pg_roles r WHERE r.rolname IN ({placeholders})"
        ),
        role_parameters,
    ).all()
    principals = tuple(
        DatabasePrincipalEvidence(
            role_name=str(row[0]),
            role_comment=str(row[1]) if row[1] is not None else None,
            can_login=bool(row[2]),
            is_superuser=bool(row[3]),
            inherits=bool(row[4]),
            can_create_role=bool(row[5]),
            can_create_database=bool(row[6]),
            can_replicate=bool(row[7]),
            bypasses_rls=bool(row[8]),
            connection_limit=int(row[9]),
            credential_validity_ok=bool(row[10]),
        )
        for row in principal_rows
    )
    membership_count = int(
        connection.scalar(
            text(
                "SELECT count(*) FROM pg_auth_members m "
                "JOIN pg_roles parent ON parent.oid = m.roleid "
                "JOIN pg_roles member ON member.oid = m.member "
                f"WHERE parent.rolname IN ({placeholders}) "
                f"OR member.rolname IN ({placeholders})"
            ),
            role_parameters,
        )
        or 0
    )
    role_setting_count = int(
        connection.scalar(
            text(
                "SELECT count(*) FROM pg_db_role_setting s "
                "LEFT JOIN pg_roles r ON r.oid = s.setrole "
                f"WHERE s.setrole=0 OR r.rolname IN ({placeholders})"
            ),
            role_parameters,
        )
        or 0
    )
    parameter_acl_count = int(
        connection.scalar(
            text(
                "SELECT count(*) FROM pg_parameter_acl p "
                "CROSS JOIN LATERAL aclexplode(p.paracl) acl "
                "LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee "
                f"WHERE acl.grantee = 0 OR grantee.rolname IN ({placeholders})"
            ),
            role_parameters,
        )
        or 0
    )
    # The dedicated Platform cluster has no application-owned/global objects.
    # Built-in pg_default/pg_global tablespaces are the only frozen allowlist.
    external_owned_object_count = int(
        connection.scalar(
            text(
                "SELECT "
                "(SELECT count(*) FROM pg_tablespace WHERE spcname NOT IN ('pg_default','pg_global')) + "
                "(SELECT count(*) FROM pg_foreign_data_wrapper) + "
                "(SELECT count(*) FROM pg_foreign_server) + "
                # pg_user_mapping contains credential-bearing options and is
                # intentionally unreadable to runtime roles.  The public view
                # exposes every mapping effective for the current role (and
                # all mappings to role-pre's superuser), which is the precise
                # runtime privilege surface while role-pre still proves the
                # cluster-global zero inventory.
                "(SELECT count(*) FROM pg_user_mappings) + "
                "(SELECT count(*) FROM pg_largeobject_metadata) + "
                "(SELECT count(*) FROM pg_publication) + "
                "(SELECT count(*) FROM pg_subscription) + "
                "(SELECT count(*) FROM pg_event_trigger event_trigger "
                "WHERE NOT EXISTS (SELECT 1 FROM pg_depend dependency "
                "JOIN pg_extension extension ON extension.oid=dependency.refobjid "
                "WHERE dependency.refclassid='pg_extension'::regclass "
                "AND dependency.classid='pg_event_trigger'::regclass "
                "AND dependency.objid=event_trigger.oid AND dependency.objsubid=0 "
                "AND dependency.deptype='e' AND extension.extname='pgaudit'))"
            )
        )
        or 0
    )
    cross_database_acl_count = int(
        connection.scalar(
            text(
                "SELECT count(*) FROM pg_database d "
                "CROSS JOIN LATERAL aclexplode(COALESCE(d.datacl, acldefault('d', d.datdba))) acl "
                "LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee "
                "WHERE d.datname <> current_database() "
                "AND (acl.grantee = 0 "
                f"OR grantee.rolname IN ({placeholders}) "
                f"OR pg_get_userbyid(d.datdba) IN ({placeholders}))"
            ),
            role_parameters,
        )
        or 0
    )
    cross_database_dependency_count = int(
        connection.scalar(
            text(
                "SELECT count(*) FROM pg_shdepend d "
                "JOIN pg_roles r ON d.refclassid = 'pg_authid'::regclass "
                "AND d.refobjid = r.oid "
                "JOIN pg_database db ON db.oid = d.dbid "
                f"WHERE r.rolname IN ({placeholders}) "
                "AND d.dbid <> (SELECT oid FROM pg_database WHERE datname = current_database())"
            ),
            role_parameters,
        )
        or 0
    )
    global_role_dependency_count = int(
        connection.scalar(
            text(
                "SELECT count(*) FROM pg_shdepend d "
                "JOIN pg_roles r ON d.refclassid = 'pg_authid'::regclass "
                "AND d.refobjid = r.oid "
                f"WHERE r.rolname IN ({placeholders}) AND d.dbid = 0 "
                "AND NOT (d.classid = 'pg_database'::regclass "
                "AND d.objid = (SELECT oid FROM pg_database WHERE datname = current_database()))"
            ),
            role_parameters,
        )
        or 0
    )
    # The frozen fingerprint includes normalized current, acldefault, and
    # pg_init_privs rows, with PUBLIC represented explicitly rather than lost
    # through an inner role join.
    system_semantic_sha256 = platform_postgres16_system_semantic_sha256(
        connection
    )
    system_acl_sha256 = platform_system_acl_sha256(connection)
    system_acl_count = int(
        system_acl_sha256
        != policy_v1.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256.get(
            system_semantic_sha256
        )
    )
    release_environment = _protected_database_release_environment_v1()
    require_pgaudit = (
        release_environment == "production"
        or system_semantic_sha256
        == POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256
    )
    system_extension_surface_exact = (
        platform_postgres16_allowed_extension_surface_is_exact(
            connection,
            require_pgaudit=require_pgaudit,
        )
    )
    pgaudit_preloaded, pgaudit_log_class_coverage = (
        platform_postgres16_audit_configuration(connection)
    )
    credential_logging_policy_exact = bool(
        connection.scalar(
            text(
                "SELECT current_setting('log_parameter_max_length')='0' "
                "AND current_setting('log_parameter_max_length_on_error')='0' "
                "AND COALESCE(NULLIF(current_setting("
                "'auto_explain.log_parameter_max_length',true),''),'-1')='0' "
                "AND lower(COALESCE(NULLIF(current_setting("
                "'pgaudit.log_parameter',true),''),'off'))='off'"
            )
        )
    )
    system_unsafe_object_count = int(
        connection.scalar(
            text(
                "WITH allowed_extension_members AS ("
                "SELECT d.classid,d.objid,d.objsubid FROM pg_depend d "
                "JOIN pg_extension e ON e.oid=d.refobjid "
                "WHERE d.refclassid='pg_extension'::regclass AND d.deptype='e' "
                "AND e.extname IN ('plpgsql','pgaudit')), rogue AS ("
                "SELECT 'pg_class'::regclass classid,c.oid objid FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname IN ('pg_catalog','information_schema') AND c.oid>=16384 "
                "UNION ALL SELECT 'pg_proc'::regclass,p.oid FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname IN ('pg_catalog','information_schema') AND p.oid>=16384 "
                "UNION ALL SELECT 'pg_type'::regclass,t.oid FROM pg_type t "
                "JOIN pg_namespace n ON n.oid=t.typnamespace "
                "WHERE n.nspname IN ('pg_catalog','information_schema') AND t.oid>=16384 "
                "UNION ALL SELECT 'pg_operator'::regclass,o.oid FROM pg_operator o "
                "JOIN pg_namespace n ON n.oid=o.oprnamespace "
                "WHERE n.nspname IN ('pg_catalog','information_schema') AND o.oid>=16384 "
                "UNION ALL SELECT 'pg_opclass'::regclass,o.oid FROM pg_opclass o "
                "JOIN pg_namespace n ON n.oid=o.opcnamespace "
                "WHERE n.nspname IN ('pg_catalog','information_schema') AND o.oid>=16384 "
                "UNION ALL SELECT 'pg_opfamily'::regclass,o.oid FROM pg_opfamily o "
                "JOIN pg_namespace n ON n.oid=o.opfnamespace "
                "WHERE n.nspname IN ('pg_catalog','information_schema') AND o.oid>=16384 "
                "UNION ALL SELECT 'pg_collation'::regclass,c.oid FROM pg_collation c "
                "JOIN pg_namespace n ON n.oid=c.collnamespace "
                "WHERE n.nspname IN ('pg_catalog','information_schema') AND c.oid>=16384 "
                "UNION ALL SELECT 'pg_conversion'::regclass,c.oid FROM pg_conversion c "
                "JOIN pg_namespace n ON n.oid=c.connamespace "
                "WHERE n.nspname IN ('pg_catalog','information_schema') AND c.oid>=16384 "
                "UNION ALL SELECT 'pg_statistic_ext'::regclass,s.oid FROM pg_statistic_ext s "
                "JOIN pg_namespace n ON n.oid=s.stxnamespace "
                "WHERE n.nspname IN ('pg_catalog','information_schema') AND s.oid>=16384) "
                "SELECT "
                "(SELECT count(*) FROM pg_extension e "
                "WHERE e.extname NOT IN ('plpgsql','pgaudit')) + "
                "abs((SELECT count(*) FROM pg_extension WHERE extname='plpgsql')-1) + "
                "(SELECT count(*) FROM pg_namespace n WHERE n.nspname NOT IN "
                "('public','pg_catalog','information_schema','pg_toast') "
                "AND n.nspname NOT LIKE 'pg_temp_%' AND n.nspname NOT LIKE 'pg_toast_temp_%') + "
                "(SELECT count(*) FROM rogue r WHERE NOT EXISTS (SELECT 1 "
                "FROM allowed_extension_members a WHERE a.classid=r.classid "
                "AND a.objid=r.objid AND a.objsubid=0))"
            )
        )
        or 0
    )
    public_unsafe_object_count = int(
        connection.scalar(
            text(
                "SELECT "
                "(SELECT count(*) FROM pg_rewrite r JOIN pg_class c ON c.oid=r.ev_class "
                " JOIN pg_namespace n ON n.oid=c.relnamespace "
                " WHERE n.nspname='public' AND r.rulename <> '_RETURN') + "
                "(SELECT count(*) FROM pg_operator o JOIN pg_namespace n ON n.oid=o.oprnamespace "
                " WHERE n.nspname='public') + "
                "(SELECT count(*) FROM pg_cast c JOIN pg_type s ON s.oid=c.castsource "
                " JOIN pg_namespace sn ON sn.oid=s.typnamespace JOIN pg_type t ON t.oid=c.casttarget "
                " JOIN pg_namespace tn ON tn.oid=t.typnamespace "
                " WHERE sn.nspname='public' OR tn.nspname='public') + "
                "(SELECT count(*) FROM pg_opclass o JOIN pg_namespace n ON n.oid=o.opcnamespace "
                " WHERE n.nspname='public') + "
                "(SELECT count(*) FROM pg_opfamily o JOIN pg_namespace n ON n.oid=o.opfnamespace "
                " WHERE n.nspname='public') + "
                "(SELECT count(*) FROM pg_collation c JOIN pg_namespace n ON n.oid=c.collnamespace "
                " WHERE n.nspname='public') + "
                "(SELECT count(*) FROM pg_conversion c JOIN pg_namespace n ON n.oid=c.connamespace "
                " WHERE n.nspname='public') + "
                "(SELECT count(*) FROM pg_ts_config c JOIN pg_namespace n ON n.oid=c.cfgnamespace "
                " WHERE n.nspname='public') + "
                "(SELECT count(*) FROM pg_ts_dict d JOIN pg_namespace n ON n.oid=d.dictnamespace "
                " WHERE n.nspname='public') + "
                "(SELECT count(*) FROM pg_ts_parser p JOIN pg_namespace n ON n.oid=p.prsnamespace "
                " WHERE n.nspname='public') + "
                "(SELECT count(*) FROM pg_ts_template t JOIN pg_namespace n ON n.oid=t.tmplnamespace "
                " WHERE n.nspname='public') + "
                "(SELECT count(*) FROM pg_transform x JOIN pg_type t ON t.oid=x.trftype "
                " JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public') + "
                "(SELECT count(*) FROM pg_statistic_ext s JOIN pg_namespace n "
                " ON n.oid=s.stxnamespace WHERE n.nspname='public')"
            )
        )
        or 0
    )
    foreign_owned_object_count = int(
        connection.scalar(
            text(
                "SELECT "
                "(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                " WHERE n.nspname = 'public' AND c.relkind IN ('r','p','S','v','m') "
                " AND pg_get_userbyid(c.relowner) <> :migration_role) + "
                "(SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                " WHERE n.nspname = 'public' AND pg_get_userbyid(p.proowner) <> :migration_role) + "
                "(SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
                " WHERE n.nspname = 'public' AND pg_get_userbyid(t.typowner) <> :migration_role) + "
                "(SELECT count(*) FROM pg_namespace n WHERE n.nspname <> 'public' "
                f" AND pg_get_userbyid(n.nspowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                f" WHERE n.nspname <> 'public' AND pg_get_userbyid(c.relowner) IN ({placeholders}) "
                "AND NOT (pg_get_userbyid(c.relowner)=:migration_role AND n.nspname='pg_toast' "
                "AND (EXISTS (SELECT 1 FROM pg_class parent JOIN pg_namespace pn "
                "ON pn.oid=parent.relnamespace WHERE pn.nspname='public' "
                "AND parent.reltoastrelid=c.oid) OR EXISTS (SELECT 1 FROM pg_index ix "
                "JOIN pg_class parent ON parent.reltoastrelid=ix.indrelid "
                "JOIN pg_namespace pn ON pn.oid=parent.relnamespace "
                "WHERE pn.nspname='public' AND ix.indexrelid=c.oid)))) + "
                "(SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                f" WHERE n.nspname <> 'public' AND pg_get_userbyid(p.proowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace "
                f" WHERE n.nspname <> 'public' AND pg_get_userbyid(t.typowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_operator o JOIN pg_namespace n ON n.oid=o.oprnamespace "
                f" WHERE pg_get_userbyid(o.oprowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_opclass o JOIN pg_namespace n ON n.oid=o.opcnamespace "
                f" WHERE pg_get_userbyid(o.opcowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_opfamily o JOIN pg_namespace n ON n.oid=o.opfnamespace "
                f" WHERE pg_get_userbyid(o.opfowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_collation o JOIN pg_namespace n ON n.oid=o.collnamespace "
                f" WHERE pg_get_userbyid(o.collowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_conversion o JOIN pg_namespace n ON n.oid=o.connamespace "
                f" WHERE pg_get_userbyid(o.conowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_ts_config o JOIN pg_namespace n ON n.oid=o.cfgnamespace "
                f" WHERE pg_get_userbyid(o.cfgowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_ts_dict o JOIN pg_namespace n ON n.oid=o.dictnamespace "
                f" WHERE pg_get_userbyid(o.dictowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_statistic_ext o JOIN pg_namespace n ON n.oid=o.stxnamespace "
                f" WHERE pg_get_userbyid(o.stxowner) IN ({placeholders}))"
            ),
            {"migration_role": policy_v1.MIGRATION_DATABASE_ROLE, **role_parameters},
        )
        or 0
    )
    column_acl_count = int(
        connection.scalar(
            text(
                "SELECT count(*) FROM pg_attribute a "
                "JOIN pg_class c ON c.oid = a.attrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND a.attnum > 0 "
                "AND NOT a.attisdropped AND a.attacl IS NOT NULL "
                "AND cardinality(a.attacl) > 0"
            )
        )
        or 0
    )
    database_acl = _acl_rows(
        connection,
        catalog_sql=(
            "SELECT COALESCE(grantee.rolname, 'PUBLIC'), acl.privilege_type, "
            "pg_get_userbyid(acl.grantor), acl.is_grantable "
            "FROM pg_database d CROSS JOIN LATERAL "
            "aclexplode(COALESCE(d.datacl, acldefault('d', d.datdba))) acl "
            "LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee "
            "WHERE d.datname = current_database() AND acl.grantee <> d.datdba"
        ),
    )
    schema_acl = _acl_rows(
        connection,
        catalog_sql=(
            "SELECT COALESCE(grantee.rolname, 'PUBLIC'), acl.privilege_type, "
            "pg_get_userbyid(acl.grantor), acl.is_grantable "
            "FROM pg_namespace n CROSS JOIN LATERAL "
            "aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) acl "
            "LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee "
            "WHERE n.nspname = 'public' AND acl.grantee <> n.nspowner"
        ),
    )
    table_names = frozenset(
        str(value)
        for value in connection.scalars(
            text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r','p')"
            )
        )
    )
    legacy_pending_work_count = 0
    if {"generation_tasks", "relay_submission_outbox"}.issubset(table_names):
        legacy_pending_work_count = int(
            connection.scalar(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM public.generation_tasks "
                    "WHERE relay_backend_id = 'legacy-default-v1' "
                    "AND status::text NOT IN ('SUCCEEDED','FAILED','CANCELLED')) + "
                    "(SELECT count(*) FROM public.relay_submission_outbox "
                    "WHERE relay_backend_id = 'legacy-default-v1' "
                    "AND status::text NOT IN ('SENT','PERMANENTLY_FAILED','CANCELLED'))"
                )
            )
            or 0
        )
    table_acl = frozenset(
        (
            str(row[0]),
            str(row[1]),
            str(row[2]).upper(),
            str(row[3]),
            bool(row[4]),
        )
        for row in connection.execute(
            text(
                "SELECT c.relname, COALESCE(grantee.rolname, 'PUBLIC'), "
                "acl.privilege_type, pg_get_userbyid(acl.grantor), acl.is_grantable "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) acl "
                "LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r','p') "
                "AND acl.grantee <> c.relowner"
            )
        ).all()
    )
    sequence_acl = frozenset(
        (str(row[0]), str(row[1]).upper(), str(row[2]), bool(row[3]))
        for row in connection.execute(
            text(
                "SELECT COALESCE(grantee.rolname, 'PUBLIC'), acl.privilege_type, "
                "pg_get_userbyid(acl.grantor), acl.is_grantable "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl, acldefault('S', c.relowner))) acl "
                "LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee "
                "WHERE n.nspname = 'public' AND c.relkind = 'S' "
                "AND acl.grantee <> c.relowner"
            )
        ).all()
    )
    routine_acl = frozenset(
        (str(row[0]), str(row[1]).upper(), str(row[2]), bool(row[3]))
        for row in connection.execute(
            text(
                "SELECT COALESCE(grantee.rolname, 'PUBLIC'), acl.privilege_type, "
                "pg_get_userbyid(acl.grantor), acl.is_grantable "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) acl "
                "LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee "
                "WHERE n.nspname = 'public' AND acl.grantee <> p.proowner"
            )
        ).all()
    )
    default_acl = frozenset(
        (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[4]).upper(),
            str(row[5]),
            bool(row[6]),
        )
        for row in connection.execute(
            text(
                "SELECT pg_get_userbyid(d.defaclrole), COALESCE(n.nspname, ''), "
                "CASE d.defaclobjtype WHEN 'r' THEN 'TABLE' WHEN 'S' THEN 'SEQUENCE' "
                "WHEN 'f' THEN 'FUNCTION' WHEN 'T' THEN 'TYPE' WHEN 'n' THEN 'SCHEMA' "
                "ELSE d.defaclobjtype::text END, COALESCE(grantee.rolname, 'PUBLIC'), "
                "acl.privilege_type, pg_get_userbyid(acl.grantor), acl.is_grantable "
                "FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace "
                "CROSS JOIN LATERAL aclexplode(d.defaclacl) acl "
                "LEFT JOIN pg_roles grantee ON grantee.oid=acl.grantee"
            )
        ).all()
    )
    alembic_heads: tuple[str, ...] = ()
    if "alembic_version" in table_names:
        alembic_heads = tuple(
            str(value)
            for value in connection.scalars(
                text("SELECT version_num FROM public.alembic_version")
            )
        )
    return PlatformDatabaseEvidence(
        current_user=str(identity[0]),
        session_user=str(identity[1]),
        ssl_active=bool(identity[2]),
        current_schema=str(identity[3]) if identity[3] is not None else None,
        explicit_schemas=tuple(str(value) for value in (identity[4] or ())),
        database_owner=str(identity[5]),
        public_schema_owner=str(identity[6]),
        principals=principals,
        membership_count=membership_count,
        role_setting_count=role_setting_count,
        parameter_acl_count=parameter_acl_count,
        external_owned_object_count=external_owned_object_count,
        cross_database_acl_count=cross_database_acl_count,
        cross_database_dependency_count=cross_database_dependency_count,
        global_role_dependency_count=global_role_dependency_count,
        system_acl_count=system_acl_count,
        system_acl_sha256=system_acl_sha256,
        system_semantic_sha256=system_semantic_sha256,
        system_extension_surface_exact=system_extension_surface_exact,
        pgaudit_preloaded=pgaudit_preloaded,
        pgaudit_log_class_coverage=pgaudit_log_class_coverage,
        credential_logging_policy_exact=credential_logging_policy_exact,
        system_unsafe_object_count=system_unsafe_object_count,
        public_unsafe_object_count=public_unsafe_object_count,
        legacy_pending_work_count=legacy_pending_work_count,
        foreign_owned_object_count=foreign_owned_object_count,
        column_acl_count=column_acl_count,
        database_acl=database_acl,
        schema_acl=schema_acl,
        table_names=table_names,
        table_acl=table_acl,
        sequence_acl=sequence_acl,
        routine_acl=routine_acl,
        default_acl=default_acl,
        catalog_sha256=platform_catalog_sha256(connection),
        alembic_heads=alembic_heads,
    )


def attest_platform_database_connection(
    connection: Connection,
    process_role: str,
    *,
    require_runtime_acl: bool = True,
    require_head: bool = True,
    policy=policy_v1,
) -> None:
    _require_frozen_policy(policy)
    if not protected_platform_runtime_requested_v1():
        return
    if connection.dialect.name != "postgresql":
        _fail("database dialect")
    try:
        evidence = collect_platform_database_evidence(connection, policy=policy)
        from .platform_database_release_proof import (
            PlatformDatabaseReleaseProofError,
            attest_platform_database_release_proof,
        )

        try:
            attest_platform_database_release_proof(connection, evidence)
        except PlatformDatabaseReleaseProofError:
            _fail("database release proof")
        validate_platform_database_evidence(
            evidence,
            process_role,
            require_runtime_acl=require_runtime_acl,
            require_head=require_head,
            policy=policy,
        )
    except PlatformDatabaseAttestationError:
        raise
    except SQLAlchemyError:
        _fail("query")


def install_platform_database_connection_attestation(
    engine: Engine,
    process_role: str,
) -> None:
    """Attest every logical checkout on the connection it will actually use."""

    _require_frozen_policy(policy_v1)
    if not protected_platform_runtime_requested_v1():
        return
    if process_role not in policy_v1.DATABASE_ROLE_BY_PROCESS:
        _fail("process role")
    with _engine_attestation_lock:
        installed_role = _engine_attestation_roles.get(engine)
        if installed_role is not None:
            if installed_role != process_role:
                _fail("engine process role")
            return

        def _attest_checked_out_connection(connection: Connection) -> None:
            # SQLAlchemy's engine_connect event fires after pool checkout but
            # before the Connection is returned to Session/application code.
            # All catalog/proof queries execute on this exact DBAPI connection;
            # no recursive engine.connect() is used.
            migration_connection = process_role == "migration"
            try:
                attest_platform_database_connection(
                    connection,
                    process_role,
                    require_runtime_acl=not migration_connection,
                    require_head=not migration_connection,
                )
            except Exception:
                # A rejected checkout must not remain live in the pool (or
                # consume a tightly bounded principal connection slot).
                try:
                    connection.invalidate()
                finally:
                    raise

        event.listen(engine, "engine_connect", _attest_checked_out_connection)
        _engine_attestation_roles[engine] = process_role


def attest_platform_database(
    engine: Engine,
    process_role: str,
    *,
    policy=policy_v1,
) -> None:
    _require_frozen_policy(policy)
    if not protected_platform_runtime_requested_v1():
        return
    install_platform_database_connection_attestation(engine, process_role)
    try:
        with engine.connect() as connection:
            attest_platform_database_connection(
                connection,
                process_role,
                policy=policy,
            )
    except PlatformDatabaseAttestationError:
        raise
    except SQLAlchemyError:
        _fail("connection")


def assert_platform_database_manifest_matches_metadata(
    table_names: Iterable[str],
) -> None:
    if frozenset(table_names) != policy_v1.TABLES:
        raise AssertionError("Platform database privilege manifest is stale")


def validate_privilege_manifest() -> None:
    expected_roles = set(policy_v1.DATABASE_ROLE_BY_PROCESS) - {"migration"}
    if set(policy_v1.PRIVILEGES_BY_PROCESS) != expected_roles:
        raise AssertionError("Platform database process manifest is incomplete")
    allowed = {"SELECT", "INSERT", "UPDATE", "DELETE"}
    for privileges_by_table in policy_v1.PRIVILEGES_BY_PROCESS.values():
        if not set(privileges_by_table).issubset(policy_v1.TABLES):
            raise AssertionError("Platform database table manifest is invalid")
        if any(
            not privileges or not privileges.issubset(allowed)
            for privileges in privileges_by_table.values()
        ):
            raise AssertionError("Platform database privilege manifest is invalid")


validate_privilege_manifest()
