"""Frozen PostgreSQL 16 attestation behavior for Platform Alembic 0041."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from threading import Lock
from typing import Iterable
from weakref import WeakKeyDictionary

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from . import database_privileges_behavior_v5 as behavior_v5
from . import database_privileges_v5 as policy_v5
from . import database_privileges_v6 as policy_v6


DatabasePrincipalEvidence = behavior_v5.DatabasePrincipalEvidence
PlatformDatabaseEvidence = behavior_v5.PlatformDatabaseEvidence
PlatformDatabaseAttestationError = behavior_v5.PlatformDatabaseAttestationError

_engine_attestation_lock = Lock()
_engine_attestation_roles: WeakKeyDictionary[Engine, str] = WeakKeyDictionary()


def _catalog_projections_v6() -> tuple[tuple[str, str], ...]:
    """Normalize column identity by name instead of physical ``attnum``.

    PostgreSQL preserves dropped attribute slots and an automatically-created
    default TOAST relation after its triggering columns are dropped.  The
    v1-v5 projection therefore changed after an otherwise semantic 0041
    downgrade/re-upgrade.  v6 keeps explicit relation/column storage semantics
    while normalizing those two internal allocation-history details, making
    the release fingerprint stable across the supported rollback path.
    """

    projections: list[tuple[str, str]] = []
    for category, statement in behavior_v5._CATALOG_PROJECTIONS:
        inherited = statement
        if category == "relation":
            statement = statement.replace(
                "COALESCE(regexp_replace(toast.relname, "
                "'pg_toast_[0-9]+', 'pg_toast_<oid>', 'g'), ''), ",
                "",
            )
            default_toast_index = statement.index(
                ", COALESCE((SELECT string_agg("
            )
            relation_from = statement.index(" FROM pg_class c", default_toast_index)
            statement = statement[:default_toast_index] + statement[relation_from:]
        elif category == "column":
            statement = statement.replace(
                "SELECT c.relname, a.attnum, a.attname, ",
                "SELECT c.relname, a.attname, ",
            ).replace(
                "ORDER BY c.relname, a.attnum",
                "ORDER BY c.relname, a.attname",
            )
        if category in {"relation", "column"}:
            if statement == inherited:
                raise RuntimeError(f"frozen v6 {category} projection is stale")
        projections.append((category, statement))
    return tuple(projections)


_CATALOG_PROJECTIONS = _catalog_projections_v6()


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


platform_system_acl_sha256 = behavior_v5.platform_system_acl_sha256


def protected_platform_runtime_requested_v6() -> bool:
    return behavior_v5.protected_platform_runtime_requested_v5()


def _fail(invariant: str) -> None:
    raise PlatformDatabaseAttestationError(
        f"protected Platform database attestation failed: {invariant}"
    )


def _require_frozen_policy(policy) -> None:
    if policy is not policy_v6:
        _fail("policy version")


def _normalized_current_evidence(
    evidence: PlatformDatabaseEvidence,
    *,
    require_head: bool,
) -> PlatformDatabaseEvidence:
    if evidence.catalog_sha256 != policy_v6.CATALOG_SHA256:
        _fail("catalog fingerprint")
    if require_head and evidence.alembic_heads != (policy_v6.ALEMBIC_HEAD,):
        _fail("migration head")
    removed_tables = policy_v6.TABLES - policy_v5.TABLES
    return replace(
        evidence,
        catalog_sha256=policy_v5.CATALOG_SHA256,
        alembic_heads=(policy_v5.ALEMBIC_HEAD,),
        table_names=policy_v5.TABLES | {"alembic_version"},
        table_acl=frozenset(
            row for row in evidence.table_acl if row[0] not in removed_tables
        ),
    )


def expected_platform_table_acl(policy=policy_v6) -> frozenset[tuple[str, str, str]]:
    _require_frozen_policy(policy)
    return policy_v6.EXPECTED_TABLE_ACL


def validate_platform_database_evidence(
    evidence: PlatformDatabaseEvidence,
    process_role: str,
    *,
    require_runtime_acl: bool,
    require_head: bool,
    policy=policy_v6,
) -> None:
    _require_frozen_policy(policy)
    if evidence.catalog_sha256 == policy_v6.CATALOG_SHA256:
        normalized = _normalized_current_evidence(evidence, require_head=require_head)
        if require_runtime_acl or evidence.alembic_heads == (policy_v6.ALEMBIC_HEAD,):
            validate_platform_database_acl_evidence(
                evidence,
                require_head=require_head,
                policy=policy,
            )
    elif (
        evidence.alembic_heads == (policy_v5.ALEMBIC_HEAD,)
        and evidence.catalog_sha256
        == policy_v6.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD[
            policy_v5.ALEMBIC_HEAD
        ]
    ):
        # v6 deliberately normalizes physical PostgreSQL attnum slots, so its
        # qualified 0040 source digest is different from v5's frozen digest.
        # Translate only this exact head+digest pair before delegating the
        # unchanged principal and ACL checks to frozen v5 behavior.
        normalized = replace(
            evidence,
            catalog_sha256=policy_v5.CATALOG_SHA256,
        )
    else:
        if require_runtime_acl or evidence.alembic_heads == (policy_v6.ALEMBIC_HEAD,):
            _fail("catalog fingerprint")
        normalized = evidence
    behavior_v5.validate_platform_database_evidence(
        normalized,
        process_role,
        require_runtime_acl=(
            False if normalized is not evidence else require_runtime_acl
        ),
        require_head=(require_head and normalized is evidence),
        policy=policy_v5,
    )


def validate_platform_database_acl_evidence(
    evidence: PlatformDatabaseEvidence,
    *,
    require_head: bool,
    policy=policy_v6,
) -> None:
    _require_frozen_policy(policy)
    _normalized_current_evidence(evidence, require_head=require_head)
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
        normalized_database_acl != policy_v6.EXPECTED_DATABASE_ACL
        or normalized_schema_acl != policy_v6.EXPECTED_SCHEMA_ACL
        or evidence.table_names != policy_v6.TABLES | {"alembic_version"}
        or normalized_table_acl != policy_v6.EXPECTED_TABLE_ACL
        or evidence.sequence_acl
        or evidence.routine_acl
        or evidence.default_acl != policy_v6.EXPECTED_DEFAULT_ACL
    ):
        _fail("database privileges")
    if any(
        grantor != policy_v6.MIGRATION_DATABASE_ROLE or is_grantable
        for _, _, grantor, is_grantable in evidence.database_acl
    ):
        _fail("database privileges")
    if any(
        grantor not in {policy_v6.MIGRATION_DATABASE_ROLE, "pg_database_owner"}
        or is_grantable
        for _, _, grantor, is_grantable in evidence.schema_acl
    ):
        _fail("schema privileges")
    if any(
        grantor != policy_v6.MIGRATION_DATABASE_ROLE or is_grantable
        for _, _, _, grantor, is_grantable in evidence.table_acl
    ):
        _fail("table privileges")
    if require_head and evidence.alembic_heads != (policy_v6.ALEMBIC_HEAD,):
        _fail("migration head")


def validate_platform_migration_source_state(
    connection: Connection,
    *,
    policy=policy_v6,
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
        if table_names or catalog_sha256 != policy_v6.EMPTY_CATALOG_SHA256:
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
    expected = policy_v6.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD.get(heads[0])
    if expected is None or catalog_sha256 != expected:
        _fail("migration source catalog")


def collect_platform_database_evidence(
    connection: Connection,
    *,
    policy=policy_v6,
) -> PlatformDatabaseEvidence:
    _require_frozen_policy(policy)
    evidence = behavior_v5.collect_platform_database_evidence(
        connection,
        policy=policy_v5,
    )
    return replace(
        evidence,
        catalog_sha256=platform_catalog_sha256(connection),
    )


def attest_platform_database_connection(
    connection: Connection,
    process_role: str,
    *,
    require_runtime_acl: bool = True,
    require_head: bool = True,
    policy=policy_v6,
) -> None:
    _require_frozen_policy(policy)
    if not protected_platform_runtime_requested_v6():
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
    _require_frozen_policy(policy_v6)
    if not protected_platform_runtime_requested_v6():
        return
    if process_role not in policy_v6.DATABASE_ROLE_BY_PROCESS:
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
    policy=policy_v6,
) -> None:
    _require_frozen_policy(policy)
    if not protected_platform_runtime_requested_v6():
        return
    install_platform_database_connection_attestation(engine, process_role)
    try:
        with engine.connect() as connection:
            attest_platform_database_connection(connection, process_role, policy=policy)
    except PlatformDatabaseAttestationError:
        raise
    except SQLAlchemyError:
        _fail("connection")


def assert_platform_database_manifest_matches_metadata(table_names: Iterable[str]) -> None:
    if frozenset(table_names) != policy_v6.TABLES:
        raise AssertionError("Platform database privilege manifest is stale")


def validate_privilege_manifest() -> None:
    expected_roles = set(policy_v6.DATABASE_ROLE_BY_PROCESS) - {"migration"}
    if set(policy_v6.PRIVILEGES_BY_PROCESS) != expected_roles:
        raise AssertionError("Platform database process manifest is incomplete")
    allowed = {"SELECT", "INSERT", "UPDATE", "DELETE"}
    for privileges_by_table in policy_v6.PRIVILEGES_BY_PROCESS.values():
        if not set(privileges_by_table).issubset(policy_v6.TABLES):
            raise AssertionError("Platform database table manifest is invalid")
        if any(
            not privileges or not privileges.issubset(allowed)
            for privileges in privileges_by_table.values()
        ):
            raise AssertionError("Platform database privilege manifest is invalid")


validate_privilege_manifest()
