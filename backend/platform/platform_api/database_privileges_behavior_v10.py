"""Frozen PostgreSQL 16 attestation behavior for Platform Alembic 0045."""

from __future__ import annotations

from dataclasses import replace
from threading import Lock
from typing import Iterable
from weakref import WeakKeyDictionary

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from . import database_privileges_behavior_v9 as behavior_v9
from . import database_privileges_v9 as policy_v9
from . import database_privileges_v10 as policy_v10


DatabasePrincipalEvidence = behavior_v9.DatabasePrincipalEvidence
PlatformDatabaseEvidence = behavior_v9.PlatformDatabaseEvidence
PlatformDatabaseAttestationError = behavior_v9.PlatformDatabaseAttestationError
platform_catalog_sha256 = behavior_v9.platform_catalog_sha256
platform_system_acl_sha256 = behavior_v9.platform_system_acl_sha256

_engine_attestation_lock = Lock()
_engine_attestation_roles: WeakKeyDictionary[Engine, str] = WeakKeyDictionary()
_PROCESS = "relay-catalog-sync"
_ROLE = policy_v10.DATABASE_ROLE_BY_PROCESS[_PROCESS]


def protected_platform_runtime_requested_v10() -> bool:
    return behavior_v9.protected_platform_runtime_requested_v9()


def _fail(invariant: str) -> None:
    raise PlatformDatabaseAttestationError(
        f"protected Platform database attestation failed: {invariant}"
    )


def _require_frozen_policy(policy) -> None:
    if policy is not policy_v10:
        _fail("policy version")


def _validate_principals(
    principals: Iterable[DatabasePrincipalEvidence],
) -> None:
    by_name = {principal.role_name: principal for principal in principals}
    expected_names = set(policy_v10.DATABASE_ROLE_BY_PROCESS.values())
    if set(by_name) != expected_names:
        _fail("principal inventory")
    process_by_role = {
        database_role: process_role
        for process_role, database_role in (
            policy_v10.DATABASE_ROLE_BY_PROCESS.items()
        )
    }
    for role_name, principal in by_name.items():
        process_role = process_by_role[role_name]
        if (
            principal.role_comment
            != policy_v10.DATABASE_ROLE_COMMENT_BY_PROCESS[process_role]
            or not principal.can_login
            or principal.is_superuser
            or principal.inherits
            or principal.can_create_role
            or principal.can_create_database
            or principal.can_replicate
            or principal.bypasses_rls
            or principal.connection_limit
            != policy_v10.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS[
                process_role
            ]
            or not principal.credential_validity_ok
        ):
            _fail("principal properties")


def _without_new_role(
    evidence: PlatformDatabaseEvidence,
    *,
    normalize_current: bool,
) -> PlatformDatabaseEvidence:
    current_user = evidence.current_user
    session_user = evidence.session_user
    if normalize_current and current_user == _ROLE and session_user == _ROLE:
        current_user = policy_v9.DATABASE_ROLE_BY_PROCESS["platform-api"]
        session_user = current_user
    # v10 has already validated the complete current catalog and ACL before
    # reaching this adapter.  Delegate only the unchanged connection/system
    # invariants to the frozen chain.  An empty pre-migration snapshot makes
    # every older behavior skip its historical catalog/ACL shape while still
    # enforcing identity, transport, ownership, principal and risk evidence.
    # This also avoids pretending that the new role existed in a frozen head.
    return replace(
        evidence,
        current_user=current_user,
        session_user=session_user,
        principals=tuple(
            principal
            for principal in evidence.principals
            if principal.role_name != _ROLE
        ),
        database_acl=frozenset(
            row for row in evidence.database_acl if row[0] != _ROLE
        ),
        schema_acl=frozenset(
            row for row in evidence.schema_acl if row[0] != _ROLE
        ),
        table_acl=frozenset(
            row for row in evidence.table_acl if row[1] != _ROLE
        ),
        sequence_acl=frozenset(
            row for row in evidence.sequence_acl if row[0] != _ROLE
        ),
        routine_acl=frozenset(
            row for row in evidence.routine_acl if row[0] != _ROLE
        ),
        default_acl=frozenset(),
        catalog_sha256=policy_v10.EMPTY_CATALOG_SHA256,
        alembic_heads=(),
    )


def expected_platform_table_acl(
    policy=policy_v10,
) -> frozenset[tuple[str, str, str]]:
    _require_frozen_policy(policy)
    return policy_v10.EXPECTED_TABLE_ACL


def validate_platform_database_evidence(
    evidence: PlatformDatabaseEvidence,
    process_role: str,
    *,
    require_runtime_acl: bool,
    require_head: bool,
    policy=policy_v10,
) -> None:
    _require_frozen_policy(policy)
    if process_role not in policy_v10.DATABASE_ROLE_BY_PROCESS:
        _fail("process role")
    expected_user = policy_v10.DATABASE_ROLE_BY_PROCESS[process_role]
    if (
        evidence.current_user != expected_user
        or evidence.session_user != expected_user
    ):
        _fail("session identity")
    _validate_principals(evidence.principals)

    current_catalog = evidence.catalog_sha256 == policy_v10.CATALOG_SHA256
    if current_catalog:
        validate_platform_database_acl_evidence(
            evidence,
            require_head=require_head,
            policy=policy,
        )
    elif require_runtime_acl or require_head:
        _fail("catalog fingerprint")

    normalized_process = process_role
    normalize_current = process_role == _PROCESS
    if normalize_current:
        normalized_process = "platform-api"
    normalized = _without_new_role(
        evidence,
        normalize_current=normalize_current,
    )
    behavior_v9.validate_platform_database_evidence(
        normalized,
        normalized_process,
        require_runtime_acl=False,
        require_head=False,
        policy=policy_v9,
    )


def validate_platform_database_acl_evidence(
    evidence: PlatformDatabaseEvidence,
    *,
    require_head: bool,
    policy=policy_v10,
) -> None:
    _require_frozen_policy(policy)
    if evidence.catalog_sha256 != policy_v10.CATALOG_SHA256:
        _fail("catalog fingerprint")
    if require_head and evidence.alembic_heads != (policy_v10.ALEMBIC_HEAD,):
        _fail("migration head")
    normalized_database_acl = frozenset(
        (grantee, privilege)
        for grantee, privilege, _, _ in evidence.database_acl
    )
    normalized_schema_acl = frozenset(
        (grantee, privilege)
        for grantee, privilege, _, _ in evidence.schema_acl
    )
    normalized_table_acl = frozenset(
        (table_name, grantee, privilege)
        for table_name, grantee, privilege, _, _ in evidence.table_acl
    )
    if (
        normalized_database_acl != policy_v10.EXPECTED_DATABASE_ACL
        or normalized_schema_acl != policy_v10.EXPECTED_SCHEMA_ACL
        or evidence.table_names != policy_v10.TABLES | {"alembic_version"}
        or normalized_table_acl != policy_v10.EXPECTED_TABLE_ACL
        or evidence.sequence_acl
        or evidence.routine_acl
        or evidence.default_acl != policy_v10.EXPECTED_DEFAULT_ACL
    ):
        _fail("database privileges")
    if any(
        grantor != policy_v10.MIGRATION_DATABASE_ROLE or is_grantable
        for _, _, grantor, is_grantable in evidence.database_acl
    ):
        _fail("database privileges")
    if any(
        grantor
        not in {policy_v10.MIGRATION_DATABASE_ROLE, "pg_database_owner"}
        or is_grantable
        for _, _, grantor, is_grantable in evidence.schema_acl
    ):
        _fail("schema privileges")
    if any(
        grantor != policy_v10.MIGRATION_DATABASE_ROLE or is_grantable
        for _, _, _, grantor, is_grantable in evidence.table_acl
    ):
        _fail("table privileges")


def validate_platform_migration_source_state(
    connection: Connection,
    *,
    policy=policy_v10,
) -> None:
    _require_frozen_policy(policy)
    table_names = frozenset(
        str(value)
        for value in connection.scalars(
            text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
                "ON n.oid=c.relnamespace WHERE n.nspname='public' "
                "AND c.relkind IN ('r','p')"
            )
        )
    )
    catalog_sha256 = platform_catalog_sha256(connection)
    if "alembic_version" not in table_names:
        if table_names or catalog_sha256 != policy_v10.EMPTY_CATALOG_SHA256:
            _fail("migration source catalog")
        return
    heads = tuple(
        str(value)
        for value in connection.scalars(
            text(
                "SELECT version_num FROM public.alembic_version "
                "ORDER BY version_num"
            )
        )
    )
    if len(heads) != 1:
        _fail("migration source head")
    expected = policy_v10.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD.get(heads[0])
    if expected is None or catalog_sha256 != expected:
        _fail("migration source catalog")


def _new_role_principal(connection: Connection) -> DatabasePrincipalEvidence | None:
    row = connection.execute(
        text(
            "SELECT r.rolname, shobj_description(r.oid, 'pg_authid'), "
            "r.rolcanlogin, r.rolsuper, r.rolinherit, r.rolcreaterole, "
            "r.rolcreatedb, r.rolreplication, r.rolbypassrls, r.rolconnlimit, "
            "r.rolvaliduntil IS NOT NULL "
            "AND r.rolvaliduntil > statement_timestamp() + interval '24 hours' "
            "AND r.rolvaliduntil <= statement_timestamp() + interval '366 days' "
            "FROM pg_roles r WHERE r.rolname = :role"
        ),
        {"role": _ROLE},
    ).one_or_none()
    if row is None:
        return None
    return DatabasePrincipalEvidence(
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


def _new_role_risk_counts(connection: Connection) -> dict[str, int]:
    parameters = {"role": _ROLE}
    statements = {
        "membership_count": (
            "SELECT count(*) FROM pg_auth_members m "
            "JOIN pg_roles parent ON parent.oid=m.roleid "
            "JOIN pg_roles member ON member.oid=m.member "
            "WHERE parent.rolname=:role OR member.rolname=:role"
        ),
        "role_setting_count": (
            "SELECT count(*) FROM pg_db_role_setting s "
            "JOIN pg_roles r ON r.oid=s.setrole WHERE r.rolname=:role"
        ),
        "parameter_acl_count": (
            "SELECT count(*) FROM pg_parameter_acl p "
            "CROSS JOIN LATERAL aclexplode(p.paracl) acl "
            "JOIN pg_roles grantee ON grantee.oid=acl.grantee "
            "WHERE grantee.rolname=:role"
        ),
        "cross_database_acl_count": (
            "SELECT count(*) FROM pg_database d CROSS JOIN LATERAL "
            "aclexplode(COALESCE(d.datacl,acldefault('d',d.datdba))) acl "
            "LEFT JOIN pg_roles grantee ON grantee.oid=acl.grantee "
            "WHERE d.datname<>current_database() AND "
            "(grantee.rolname=:role OR pg_get_userbyid(d.datdba)=:role)"
        ),
        "cross_database_dependency_count": (
            "SELECT count(*) FROM pg_shdepend d JOIN pg_roles r "
            "ON d.refclassid='pg_authid'::regclass AND d.refobjid=r.oid "
            "WHERE r.rolname=:role AND d.dbid<>0 AND d.dbid<>"
            "(SELECT oid FROM pg_database WHERE datname=current_database())"
        ),
        "global_role_dependency_count": (
            "SELECT count(*) FROM pg_shdepend d JOIN pg_roles r "
            "ON d.refclassid='pg_authid'::regclass AND d.refobjid=r.oid "
            "WHERE r.rolname=:role AND d.dbid=0 AND NOT "
            "(d.classid='pg_database'::regclass AND d.objid="
            "(SELECT oid FROM pg_database WHERE datname=current_database()))"
        ),
    }
    return {
        field: int(connection.scalar(text(statement), parameters) or 0)
        for field, statement in statements.items()
    }


def collect_platform_database_evidence(
    connection: Connection,
    *,
    policy=policy_v10,
) -> PlatformDatabaseEvidence:
    _require_frozen_policy(policy)
    evidence = behavior_v9.collect_platform_database_evidence(
        connection,
        policy=policy_v9,
    )
    principal = _new_role_principal(connection)
    risks = _new_role_risk_counts(connection)
    return replace(
        evidence,
        principals=(
            evidence.principals
            if principal is None
            else (*evidence.principals, principal)
        ),
        membership_count=evidence.membership_count + risks["membership_count"],
        role_setting_count=evidence.role_setting_count
        + risks["role_setting_count"],
        parameter_acl_count=evidence.parameter_acl_count
        + risks["parameter_acl_count"],
        cross_database_acl_count=evidence.cross_database_acl_count
        + risks["cross_database_acl_count"],
        cross_database_dependency_count=(
            evidence.cross_database_dependency_count
            + risks["cross_database_dependency_count"]
        ),
        global_role_dependency_count=evidence.global_role_dependency_count
        + risks["global_role_dependency_count"],
        catalog_sha256=platform_catalog_sha256(connection),
    )


def attest_platform_database_connection(
    connection: Connection,
    process_role: str,
    *,
    require_runtime_acl: bool = True,
    require_head: bool = True,
    policy=policy_v10,
) -> None:
    _require_frozen_policy(policy)
    if not protected_platform_runtime_requested_v10():
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
    _require_frozen_policy(policy_v10)
    if not protected_platform_runtime_requested_v10():
        return
    if process_role not in policy_v10.DATABASE_ROLE_BY_PROCESS:
        _fail("process role")
    with _engine_attestation_lock:
        installed_role = _engine_attestation_roles.get(engine)
        if installed_role is not None:
            if installed_role != process_role:
                _fail("engine process role")
            return

        def _attest(connection: Connection) -> None:
            migration = process_role == "migration"
            try:
                attest_platform_database_connection(
                    connection,
                    process_role,
                    require_runtime_acl=not migration,
                    require_head=not migration,
                )
            except Exception:
                try:
                    connection.invalidate()
                finally:
                    raise

        event.listen(engine, "engine_connect", _attest)
        _engine_attestation_roles[engine] = process_role


def attest_platform_database(
    engine: Engine,
    process_role: str,
    *,
    policy=policy_v10,
) -> None:
    _require_frozen_policy(policy)
    if not protected_platform_runtime_requested_v10():
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
    if frozenset(table_names) != policy_v10.TABLES:
        raise AssertionError("Platform database privilege manifest is stale")


def validate_privilege_manifest() -> None:
    expected_roles = set(policy_v10.DATABASE_ROLE_BY_PROCESS) - {"migration"}
    if set(policy_v10.PRIVILEGES_BY_PROCESS) != expected_roles:
        raise AssertionError("Platform database process manifest is incomplete")
    allowed = {"SELECT", "INSERT", "UPDATE", "DELETE"}
    for privileges_by_table in policy_v10.PRIVILEGES_BY_PROCESS.values():
        if not set(privileges_by_table).issubset(policy_v10.TABLES):
            raise AssertionError("Platform database table manifest is invalid")
        if any(
            not privileges or not privileges.issubset(allowed)
            for privileges in privileges_by_table.values()
        ):
            raise AssertionError("Platform database privilege manifest is invalid")


validate_privilege_manifest()
