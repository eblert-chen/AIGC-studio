"""Frozen PostgreSQL 16 attestation behavior for Platform Alembic 0046."""

from __future__ import annotations

from dataclasses import replace
from threading import Lock
from typing import Iterable
from weakref import WeakKeyDictionary

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from . import database_privileges_behavior_v10 as behavior_v10
from . import database_privileges_v10 as policy_v10
from . import database_privileges_v11 as policy_v11


DatabasePrincipalEvidence = behavior_v10.DatabasePrincipalEvidence
PlatformDatabaseEvidence = behavior_v10.PlatformDatabaseEvidence
PlatformDatabaseAttestationError = behavior_v10.PlatformDatabaseAttestationError
platform_catalog_sha256 = behavior_v10.platform_catalog_sha256
platform_system_acl_sha256 = behavior_v10.platform_system_acl_sha256

_engine_attestation_lock = Lock()
_engine_attestation_roles: WeakKeyDictionary[Engine, str] = WeakKeyDictionary()


def protected_platform_runtime_requested_v11() -> bool:
    return behavior_v10.protected_platform_runtime_requested_v10()


def _fail(invariant: str) -> None:
    raise PlatformDatabaseAttestationError(
        f"protected Platform database attestation failed: {invariant}"
    )


def _require_frozen_policy(policy) -> None:
    if policy is not policy_v11:
        _fail("policy version")


def _v10_compatible_evidence(
    evidence: PlatformDatabaseEvidence,
) -> PlatformDatabaseEvidence:
    """Remove only the 0046 catalog surface before frozen v10 delegation.

    v11 validates the complete current catalog and ACL first.  The predecessor
    then receives an intentionally non-current catalog so it checks unchanged
    identity, TLS, ownership, principals, system semantics, and risk evidence
    without reinterpreting v11 tables as if they existed at 0045.
    """

    return replace(
        evidence,
        table_names=(evidence.table_names - policy_v11.POINT_TABLES),
        table_acl=frozenset(
            row
            for row in evidence.table_acl
            if row[0] not in policy_v11.POINT_TABLES
        ),
        catalog_sha256=policy_v10.EMPTY_CATALOG_SHA256,
        alembic_heads=(),
    )


def expected_platform_table_acl(
    policy=policy_v11,
) -> frozenset[tuple[str, str, str]]:
    _require_frozen_policy(policy)
    return policy_v11.EXPECTED_TABLE_ACL


def validate_platform_database_evidence(
    evidence: PlatformDatabaseEvidence,
    process_role: str,
    *,
    require_runtime_acl: bool,
    require_head: bool,
    policy=policy_v11,
) -> None:
    _require_frozen_policy(policy)
    if process_role not in policy_v11.DATABASE_ROLE_BY_PROCESS:
        _fail("process role")
    expected_user = policy_v11.DATABASE_ROLE_BY_PROCESS[process_role]
    if (
        evidence.current_user != expected_user
        or evidence.session_user != expected_user
    ):
        _fail("session identity")

    current_catalog = evidence.catalog_sha256 == policy_v11.CATALOG_SHA256
    if current_catalog:
        validate_platform_database_acl_evidence(
            evidence,
            require_head=require_head,
            policy=policy,
        )
    elif require_runtime_acl or require_head:
        _fail("catalog fingerprint")

    behavior_v10.validate_platform_database_evidence(
        _v10_compatible_evidence(evidence),
        process_role,
        require_runtime_acl=False,
        require_head=False,
        policy=policy_v10,
    )


def validate_platform_database_acl_evidence(
    evidence: PlatformDatabaseEvidence,
    *,
    require_head: bool,
    policy=policy_v11,
) -> None:
    _require_frozen_policy(policy)
    if evidence.catalog_sha256 != policy_v11.CATALOG_SHA256:
        _fail("catalog fingerprint")
    if require_head and evidence.alembic_heads != (policy_v11.ALEMBIC_HEAD,):
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
        normalized_database_acl != policy_v11.EXPECTED_DATABASE_ACL
        or normalized_schema_acl != policy_v11.EXPECTED_SCHEMA_ACL
        or evidence.table_names != policy_v11.TABLES | {"alembic_version"}
        or normalized_table_acl != policy_v11.EXPECTED_TABLE_ACL
        or evidence.sequence_acl
        or evidence.routine_acl
        or evidence.default_acl != policy_v11.EXPECTED_DEFAULT_ACL
    ):
        _fail("database privileges")
    if any(
        grantor != policy_v11.MIGRATION_DATABASE_ROLE or is_grantable
        for _, _, grantor, is_grantable in evidence.database_acl
    ):
        _fail("database privileges")
    if any(
        grantor
        not in {policy_v11.MIGRATION_DATABASE_ROLE, "pg_database_owner"}
        or is_grantable
        for _, _, grantor, is_grantable in evidence.schema_acl
    ):
        _fail("database privileges")
    if any(
        grantor != policy_v11.MIGRATION_DATABASE_ROLE or is_grantable
        for _, _, _, grantor, is_grantable in evidence.table_acl
    ):
        _fail("table privileges")


def validate_platform_migration_source_state(
    connection: Connection,
    *,
    policy=policy_v11,
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
        if table_names or catalog_sha256 != policy_v11.EMPTY_CATALOG_SHA256:
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
    expected = policy_v11.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD.get(heads[0])
    if expected is None or catalog_sha256 != expected:
        _fail("migration source catalog")


def collect_platform_database_evidence(
    connection: Connection,
    *,
    policy=policy_v11,
) -> PlatformDatabaseEvidence:
    _require_frozen_policy(policy)
    # v11 adds no principal, so v10's collector already captures the complete
    # principal/system/risk surface and dynamically observes the 0046 catalog.
    evidence = behavior_v10.collect_platform_database_evidence(
        connection,
        policy=policy_v10,
    )
    return replace(evidence, catalog_sha256=platform_catalog_sha256(connection))


def attest_platform_database_connection(
    connection: Connection,
    process_role: str,
    *,
    require_runtime_acl: bool = True,
    require_head: bool = True,
    policy=policy_v11,
) -> None:
    _require_frozen_policy(policy)
    if not protected_platform_runtime_requested_v11():
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
    _require_frozen_policy(policy_v11)
    if not protected_platform_runtime_requested_v11():
        return
    if process_role not in policy_v11.DATABASE_ROLE_BY_PROCESS:
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
    policy=policy_v11,
) -> None:
    _require_frozen_policy(policy)
    if not protected_platform_runtime_requested_v11():
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
    if frozenset(table_names) != policy_v11.TABLES:
        raise AssertionError("Platform database privilege manifest is stale")


def validate_privilege_manifest() -> None:
    expected_roles = set(policy_v11.DATABASE_ROLE_BY_PROCESS) - {"migration"}
    if set(policy_v11.PRIVILEGES_BY_PROCESS) != expected_roles:
        raise AssertionError("Platform database process manifest is incomplete")
    allowed = {"SELECT", "INSERT", "UPDATE", "DELETE"}
    for privileges_by_table in policy_v11.PRIVILEGES_BY_PROCESS.values():
        if not set(privileges_by_table).issubset(policy_v11.TABLES):
            raise AssertionError("Platform database table manifest is invalid")
        if any(
            not privileges or not privileges.issubset(allowed)
            for privileges in privileges_by_table.values()
        ):
            raise AssertionError("Platform database privilege manifest is invalid")


validate_privilege_manifest()
