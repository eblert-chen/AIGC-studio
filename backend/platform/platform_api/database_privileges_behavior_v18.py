"""PostgreSQL 16 attestation behavior for Platform Alembic 0053."""
from __future__ import annotations

from dataclasses import replace
from threading import Lock
from typing import Iterable
from weakref import WeakKeyDictionary

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from . import database_privileges_behavior_v17 as behavior_v17
from . import database_privileges_v17 as policy_v17
from . import database_privileges_v18 as policy_v18


DatabasePrincipalEvidence = behavior_v17.DatabasePrincipalEvidence
PlatformDatabaseEvidence = behavior_v17.PlatformDatabaseEvidence
PlatformDatabaseAttestationError = behavior_v17.PlatformDatabaseAttestationError
platform_catalog_sha256 = behavior_v17.platform_catalog_sha256
platform_system_acl_sha256 = behavior_v17.platform_system_acl_sha256

_engine_attestation_lock = Lock()
_engine_attestation_roles: WeakKeyDictionary[Engine, str] = WeakKeyDictionary()


def protected_platform_runtime_requested_v18() -> bool:
    return behavior_v17.protected_platform_runtime_requested_v17()


def _fail(invariant: str) -> None:
    raise PlatformDatabaseAttestationError(
        f"protected Platform database attestation failed: {invariant}"
    )


def _require_frozen_policy(policy) -> None:
    if policy is not policy_v18:
        _fail("policy version")


def _v17_compatible_evidence(
    evidence: PlatformDatabaseEvidence,
) -> PlatformDatabaseEvidence:
    """Remove only the 0053 catalog surface before frozen v17 delegation.

    v18 validates the complete current catalog and ACL first. The predecessor
    receives an intentionally non-current catalog so it can validate unchanged
    identity, ownership, system semantics, and risk evidence without treating
    0053 tables as part of its frozen 0052 contract.
    """

    return replace(
        evidence,
        table_names=(
            evidence.table_names - policy_v18.DIRECTOR_SHOT_PACKAGE_TABLES
        ),
        table_acl=frozenset(
            row
            for row in evidence.table_acl
            if row[0] not in policy_v18.DIRECTOR_SHOT_PACKAGE_TABLES
        ),
        catalog_sha256=policy_v17.EMPTY_CATALOG_SHA256,
        alembic_heads=(),
    )


def expected_platform_table_acl(
    policy=policy_v18,
) -> frozenset[tuple[str, str, str]]:
    _require_frozen_policy(policy)
    return policy_v18.EXPECTED_TABLE_ACL


def validate_platform_database_evidence(
    evidence: PlatformDatabaseEvidence,
    process_role: str,
    *,
    require_runtime_acl: bool,
    require_head: bool,
    policy=policy_v18,
) -> None:
    _require_frozen_policy(policy)
    if process_role not in policy_v18.DATABASE_ROLE_BY_PROCESS:
        _fail("process role")
    expected_user = policy_v18.DATABASE_ROLE_BY_PROCESS[process_role]
    if evidence.current_user != expected_user or evidence.session_user != expected_user:
        _fail("session identity")
    current_catalog = evidence.catalog_sha256 == policy_v18.CATALOG_SHA256
    if current_catalog:
        validate_platform_database_acl_evidence(
            evidence,
            require_head=require_head,
            policy=policy,
        )
    elif require_runtime_acl or require_head:
        _fail("catalog fingerprint")
    behavior_v17.validate_platform_database_evidence(
        _v17_compatible_evidence(evidence),
        process_role,
        require_runtime_acl=False,
        require_head=False,
        policy=policy_v17,
    )


def validate_platform_database_acl_evidence(
    evidence: PlatformDatabaseEvidence,
    *,
    require_head: bool,
    policy=policy_v18,
) -> None:
    _require_frozen_policy(policy)
    if evidence.catalog_sha256 != policy_v18.CATALOG_SHA256:
        _fail("catalog fingerprint")
    if require_head and evidence.alembic_heads != (policy_v18.ALEMBIC_HEAD,):
        _fail("migration head")
    normalized_database_acl = frozenset(
        (grantee, privilege) for grantee, privilege, _, _ in evidence.database_acl
    )
    normalized_schema_acl = frozenset(
        (grantee, privilege) for grantee, privilege, _, _ in evidence.schema_acl
    )
    normalized_table_acl = frozenset(
        (table_name, grantee, privilege)
        for table_name, grantee, privilege, _, _ in evidence.table_acl
    )
    if (
        normalized_database_acl != policy_v18.EXPECTED_DATABASE_ACL
        or normalized_schema_acl != policy_v18.EXPECTED_SCHEMA_ACL
        or evidence.table_names != policy_v18.TABLES | {"alembic_version"}
        or normalized_table_acl != policy_v18.EXPECTED_TABLE_ACL
        or evidence.sequence_acl
        or evidence.routine_acl
        or evidence.default_acl != policy_v18.EXPECTED_DEFAULT_ACL
    ):
        _fail("database privileges")
    if any(
        grantor != policy_v18.MIGRATION_DATABASE_ROLE or is_grantable
        for _, _, grantor, is_grantable in evidence.database_acl
    ):
        _fail("database privileges")
    if any(
        grantor not in {policy_v18.MIGRATION_DATABASE_ROLE, "pg_database_owner"}
        or is_grantable
        for _, _, grantor, is_grantable in evidence.schema_acl
    ):
        _fail("database privileges")
    if any(
        grantor != policy_v18.MIGRATION_DATABASE_ROLE or is_grantable
        for _, _, _, grantor, is_grantable in evidence.table_acl
    ):
        _fail("table privileges")
def validate_platform_migration_source_state(
    connection: Connection,
    *,
    policy=policy_v18,
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
    if "alembic_version" not in table_names:
        return behavior_v17.validate_platform_migration_source_state(
            connection,
            policy=policy_v17,
        )
    heads = tuple(
        str(value)
        for value in connection.scalars(
            text("SELECT version_num FROM public.alembic_version ORDER BY version_num")
        )
    )
    if heads != (policy_v18.ALEMBIC_HEAD,):
        return behavior_v17.validate_platform_migration_source_state(
            connection,
            policy=policy_v17,
        )
    if platform_catalog_sha256(connection) != policy_v18.CATALOG_SHA256:
        _fail("migration source catalog")


def collect_platform_database_evidence(
    connection: Connection,
    *,
    policy=policy_v18,
) -> PlatformDatabaseEvidence:
    _require_frozen_policy(policy)
    evidence = behavior_v17.collect_platform_database_evidence(
        connection,
        policy=policy_v17,
    )
    return replace(evidence, catalog_sha256=platform_catalog_sha256(connection))


def attest_platform_database_connection(
    connection: Connection,
    process_role: str,
    *,
    require_runtime_acl: bool = True,
    require_head: bool = True,
    policy=policy_v18,
) -> None:
    _require_frozen_policy(policy)
    if not protected_platform_runtime_requested_v18():
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
    _require_frozen_policy(policy_v18)
    if not protected_platform_runtime_requested_v18():
        return
    if process_role not in policy_v18.DATABASE_ROLE_BY_PROCESS:
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
    policy=policy_v18,
) -> None:
    _require_frozen_policy(policy)
    if not protected_platform_runtime_requested_v18():
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
    if frozenset(table_names) != policy_v18.TABLES:
        raise AssertionError("Platform database privilege manifest is stale")


def validate_privilege_manifest() -> None:
    behavior_v17.validate_privilege_manifest()
    if not policy_v17.TABLES < policy_v18.TABLES:
        _fail("table manifest extension")
    for process, inherited in policy_v17.PRIVILEGES_BY_PROCESS.items():
        current = policy_v18.PRIVILEGES_BY_PROCESS[process]
        if any(current.get(table) != privileges for table, privileges in inherited.items()):
            _fail("inherited privileges")
    expected_new = {
        "director_shot_packages": frozenset({"SELECT", "INSERT"}),
        "task_director_shot_packages": frozenset({"SELECT", "INSERT"}),
    }
    for process, privileges in policy_v18.PRIVILEGES_BY_PROCESS.items():
        actual = {
            table: privileges.get(table, frozenset())
            for table in policy_v18.DIRECTOR_SHOT_PACKAGE_TABLES
        }
        if process == "platform-api":
            if actual != expected_new:
                _fail("director shot package privileges")
        elif any(actual.values()):
            _fail("director shot package privilege expansion")


validate_privilege_manifest()
