"""Frozen PostgreSQL 16 attestation behavior for Platform Alembic 0039.

The normalized catalog and system projections are reused byte-for-byte from
the frozen 0038 behavior.  This module owns policy-sensitive validation and
connection hooks for the 0039 head so historical policies stay immutable.
"""

from __future__ import annotations

from dataclasses import replace
from threading import Lock
from typing import Iterable
from weakref import WeakKeyDictionary

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from . import database_privileges_behavior_v3 as behavior_v3
from . import database_privileges_v3 as policy_v3
from . import database_privileges_v4 as policy_v4


DatabasePrincipalEvidence = behavior_v3.DatabasePrincipalEvidence
PlatformDatabaseEvidence = behavior_v3.PlatformDatabaseEvidence
PlatformDatabaseAttestationError = behavior_v3.PlatformDatabaseAttestationError

_engine_attestation_lock = Lock()
_engine_attestation_roles: WeakKeyDictionary[Engine, str] = WeakKeyDictionary()

_CATALOG_PROJECTIONS = behavior_v3._CATALOG_PROJECTIONS
platform_catalog_sha256 = behavior_v3.platform_catalog_sha256
platform_system_acl_sha256 = behavior_v3.platform_system_acl_sha256


def protected_platform_runtime_requested_v4() -> bool:
    return behavior_v3.protected_platform_runtime_requested_v3()


def _fail(invariant: str) -> None:
    raise PlatformDatabaseAttestationError(
        f"protected Platform database attestation failed: {invariant}"
    )


def _require_frozen_policy(policy) -> None:
    if policy is not policy_v4:
        _fail("policy version")


def _normalized_current_evidence(
    evidence: PlatformDatabaseEvidence,
    *,
    require_head: bool,
) -> PlatformDatabaseEvidence:
    if evidence.catalog_sha256 != policy_v4.CATALOG_SHA256:
        _fail("catalog fingerprint")
    if require_head and evidence.alembic_heads != (policy_v4.ALEMBIC_HEAD,):
        _fail("migration head")
    return replace(
        evidence,
        catalog_sha256=policy_v3.CATALOG_SHA256,
        alembic_heads=(policy_v3.ALEMBIC_HEAD,),
    )


def expected_platform_table_acl(
    policy=policy_v4,
) -> frozenset[tuple[str, str, str]]:
    _require_frozen_policy(policy)
    return policy_v4.EXPECTED_TABLE_ACL


def validate_platform_database_evidence(
    evidence: PlatformDatabaseEvidence,
    process_role: str,
    *,
    require_runtime_acl: bool,
    require_head: bool,
    policy=policy_v4,
) -> None:
    _require_frozen_policy(policy)
    if evidence.catalog_sha256 == policy_v4.CATALOG_SHA256:
        normalized = _normalized_current_evidence(
            evidence,
            require_head=require_head,
        )
    else:
        if require_runtime_acl or evidence.alembic_heads == (
            policy_v4.ALEMBIC_HEAD,
        ):
            _fail("catalog fingerprint")
        normalized = evidence
    behavior_v3.validate_platform_database_evidence(
        normalized,
        process_role,
        require_runtime_acl=require_runtime_acl,
        require_head=(require_head and normalized is evidence),
        policy=policy_v3,
    )


def validate_platform_database_acl_evidence(
    evidence: PlatformDatabaseEvidence,
    *,
    require_head: bool,
    policy=policy_v4,
) -> None:
    _require_frozen_policy(policy)
    normalized = _normalized_current_evidence(
        evidence,
        require_head=require_head,
    )
    behavior_v3.validate_platform_database_acl_evidence(
        normalized,
        require_head=True,
        policy=policy_v3,
    )


def validate_platform_migration_source_state(
    connection: Connection,
    *,
    policy=policy_v4,
) -> None:
    """Reject a dirty or unknown public schema before Alembic executes DDL."""

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
        if table_names or catalog_sha256 != policy_v4.EMPTY_CATALOG_SHA256:
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
    expected = policy_v4.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD.get(heads[0])
    if expected is None or catalog_sha256 != expected:
        _fail("migration source catalog")


def _legacy_nonterminal_affinity_count(connection: Connection) -> int:
    """Count visible legacy work that cannot cross the native cutover.

    SENT is terminal for the submit outbox, but its generation task remains
    nonterminal until Relay reconciliation reaches a task terminal state.
    RECONCILIATION_REQUIRED is intentionally absent from the terminal set, so
    an unknown provider submission can never be rebound to new-api.
    """

    count = 0
    for table_name, terminal_states in (
        ("generation_tasks", ("SUCCEEDED", "FAILED", "CANCELLED")),
        (
            "relay_submission_outbox",
            ("SENT", "PERMANENTLY_FAILED", "CANCELLED"),
        ),
    ):
        can_select = bool(
            connection.scalar(
                text(
                    "SELECT has_table_privilege(current_user, "
                    f"'public.{table_name}', 'SELECT')"
                )
            )
        )
        if not can_select:
            continue
        state_parameters = {
            f"terminal_{index}": state
            for index, state in enumerate(terminal_states)
        }
        placeholders = ", ".join(
            f":terminal_{index}" for index in range(len(terminal_states))
        )
        count += int(
            connection.scalar(
                text(
                    f"SELECT count(*) FROM public.{table_name} "
                    "WHERE relay_backend_id = :legacy_backend_id "
                    f"AND status::text NOT IN ({placeholders})"
                ),
                {
                    "legacy_backend_id": "legacy-default-v1",
                    **state_parameters,
                },
            )
            or 0
        )
    return count


def collect_platform_database_evidence(
    connection: Connection,
    *,
    policy=policy_v4,
) -> PlatformDatabaseEvidence:
    _require_frozen_policy(policy)
    evidence = behavior_v3.collect_platform_database_evidence(
        connection,
        policy=policy_v3,
    )
    # v1-v3 deliberately required visibility of both affinity tables before
    # counting legacy work.  That made the relay-sync principal (which only
    # needs generation_tasks) skip the cutover gate completely.  At the v4
    # production cutover each principal counts every relevant table it can
    # read.  API/dispatcher/timeout observe both tables; relay-sync observes
    # the task state that remains nonterminal for SENT/unknown submissions.
    return replace(
        evidence,
        legacy_pending_work_count=_legacy_nonterminal_affinity_count(connection),
    )


def attest_platform_database_connection(
    connection: Connection,
    process_role: str,
    *,
    require_runtime_acl: bool = True,
    require_head: bool = True,
    policy=policy_v4,
) -> None:
    _require_frozen_policy(policy)
    if not protected_platform_runtime_requested_v4():
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

    _require_frozen_policy(policy_v4)
    if not protected_platform_runtime_requested_v4():
        return
    if process_role not in policy_v4.DATABASE_ROLE_BY_PROCESS:
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
    policy=policy_v4,
) -> None:
    _require_frozen_policy(policy)
    if not protected_platform_runtime_requested_v4():
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
    if frozenset(table_names) != policy_v4.TABLES:
        raise AssertionError("Platform database privilege manifest is stale")


def validate_privilege_manifest() -> None:
    expected_roles = set(policy_v4.DATABASE_ROLE_BY_PROCESS) - {"migration"}
    if set(policy_v4.PRIVILEGES_BY_PROCESS) != expected_roles:
        raise AssertionError("Platform database process manifest is incomplete")
    allowed = {"SELECT", "INSERT", "UPDATE", "DELETE"}
    for privileges_by_table in policy_v4.PRIVILEGES_BY_PROCESS.values():
        if not set(privileges_by_table).issubset(policy_v4.TABLES):
            raise AssertionError("Platform database table manifest is invalid")
        if any(
            not privileges or not privileges.issubset(allowed)
            for privileges in privileges_by_table.values()
        ):
            raise AssertionError("Platform database privilege manifest is invalid")


validate_privilege_manifest()
