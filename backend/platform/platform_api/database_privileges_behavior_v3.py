"""Frozen PostgreSQL 16 attestation behavior for Platform Alembic 0038.

The normalized catalog and system projections are reused byte-for-byte from
the frozen 0037 behavior.  This module owns every policy-sensitive validation
and connection hook for 0038, so the v2 artifact remains immutable while the
new catalog head and fingerprint are independently attested.
"""

from __future__ import annotations

from dataclasses import replace
from threading import Lock
from typing import Iterable
from weakref import WeakKeyDictionary

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from . import database_privileges_behavior_v2 as behavior_v2
from . import database_privileges_v2 as policy_v2
from . import database_privileges_v3 as policy_v3


DatabasePrincipalEvidence = behavior_v2.DatabasePrincipalEvidence
PlatformDatabaseEvidence = behavior_v2.PlatformDatabaseEvidence
PlatformDatabaseAttestationError = behavior_v2.PlatformDatabaseAttestationError

_engine_attestation_lock = Lock()
_engine_attestation_roles: WeakKeyDictionary[Engine, str] = WeakKeyDictionary()

# Revision 0038 deliberately retains the frozen v2 projections.  Export them
# as named evidence surfaces for qualification tests and release tooling.
_CATALOG_PROJECTIONS = behavior_v2._CATALOG_PROJECTIONS
platform_catalog_sha256 = behavior_v2.platform_catalog_sha256
platform_system_acl_sha256 = behavior_v2.platform_system_acl_sha256


def protected_platform_runtime_requested_v3() -> bool:
    return behavior_v2.protected_platform_runtime_requested_v2()


def _fail(invariant: str) -> None:
    raise PlatformDatabaseAttestationError(
        f"protected Platform database attestation failed: {invariant}"
    )


def _require_frozen_policy(policy) -> None:
    if policy is not policy_v3:
        _fail("policy version")


def _normalized_current_evidence(
    evidence: PlatformDatabaseEvidence,
    *,
    require_head: bool,
) -> PlatformDatabaseEvidence:
    if evidence.catalog_sha256 != policy_v3.CATALOG_SHA256:
        _fail("catalog fingerprint")
    if require_head and evidence.alembic_heads != (policy_v3.ALEMBIC_HEAD,):
        _fail("migration head")
    return replace(
        evidence,
        catalog_sha256=policy_v2.CATALOG_SHA256,
        alembic_heads=(policy_v2.ALEMBIC_HEAD,),
    )


def expected_platform_table_acl(
    policy=policy_v3,
) -> frozenset[tuple[str, str, str]]:
    _require_frozen_policy(policy)
    return policy_v3.EXPECTED_TABLE_ACL


def validate_platform_database_evidence(
    evidence: PlatformDatabaseEvidence,
    process_role: str,
    *,
    require_runtime_acl: bool,
    require_head: bool,
    policy=policy_v3,
) -> None:
    _require_frozen_policy(policy)
    if evidence.catalog_sha256 == policy_v3.CATALOG_SHA256:
        normalized = _normalized_current_evidence(
            evidence,
            require_head=require_head,
        )
    else:
        # Migration connections may attest an exact historical source before
        # 0038 runs. Runtime roles and anything claiming the 0038 head can
        # never fall back to the historical validator.
        if require_runtime_acl or evidence.alembic_heads == (
            policy_v3.ALEMBIC_HEAD,
        ):
            _fail("catalog fingerprint")
        normalized = evidence
    behavior_v2.validate_platform_database_evidence(
        normalized,
        process_role,
        require_runtime_acl=require_runtime_acl,
        require_head=(
            require_head and normalized is evidence
        ),
        policy=policy_v2,
    )


def validate_platform_database_acl_evidence(
    evidence: PlatformDatabaseEvidence,
    *,
    require_head: bool,
    policy=policy_v3,
) -> None:
    _require_frozen_policy(policy)
    normalized = _normalized_current_evidence(
        evidence,
        require_head=require_head,
    )
    behavior_v2.validate_platform_database_acl_evidence(
        normalized,
        require_head=True,
        policy=policy_v2,
    )


def validate_platform_migration_source_state(
    connection: Connection,
    *,
    policy=policy_v3,
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
        if table_names or catalog_sha256 != policy_v3.EMPTY_CATALOG_SHA256:
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
    expected = policy_v3.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD.get(heads[0])
    if expected is None or catalog_sha256 != expected:
        _fail("migration source catalog")


def collect_platform_database_evidence(
    connection: Connection,
    *,
    policy=policy_v3,
) -> PlatformDatabaseEvidence:
    _require_frozen_policy(policy)
    return behavior_v2.collect_platform_database_evidence(
        connection,
        policy=policy_v2,
    )


def attest_platform_database_connection(
    connection: Connection,
    process_role: str,
    *,
    require_runtime_acl: bool = True,
    require_head: bool = True,
    policy=policy_v3,
) -> None:
    _require_frozen_policy(policy)
    if not protected_platform_runtime_requested_v3():
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
    """Attest every checkout on the exact connection returned to callers."""

    _require_frozen_policy(policy_v3)
    if not protected_platform_runtime_requested_v3():
        return
    if process_role not in policy_v3.DATABASE_ROLE_BY_PROCESS:
        _fail("process role")
    with _engine_attestation_lock:
        installed_role = _engine_attestation_roles.get(engine)
        if installed_role is not None:
            if installed_role != process_role:
                _fail("engine process role")
            return

        def _attest_checked_out_connection(connection: Connection) -> None:
            migration_connection = process_role == "migration"
            try:
                attest_platform_database_connection(
                    connection,
                    process_role,
                    require_runtime_acl=not migration_connection,
                    require_head=not migration_connection,
                )
            except Exception:
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
    policy=policy_v3,
) -> None:
    _require_frozen_policy(policy)
    if not protected_platform_runtime_requested_v3():
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
    if frozenset(table_names) != policy_v3.TABLES:
        raise AssertionError("Platform database privilege manifest is stale")


def validate_privilege_manifest() -> None:
    expected_roles = set(policy_v3.DATABASE_ROLE_BY_PROCESS) - {"migration"}
    if set(policy_v3.PRIVILEGES_BY_PROCESS) != expected_roles:
        raise AssertionError("Platform database process manifest is incomplete")
    allowed = {"SELECT", "INSERT", "UPDATE", "DELETE"}
    for privileges_by_table in policy_v3.PRIVILEGES_BY_PROCESS.values():
        if not set(privileges_by_table).issubset(policy_v3.TABLES):
            raise AssertionError("Platform database table manifest is invalid")
        if any(
            not privileges or not privileges.issubset(allowed)
            for privileges in privileges_by_table.values()
        ):
            raise AssertionError("Platform database privilege manifest is invalid")


validate_privilege_manifest()
