from __future__ import annotations

from dataclasses import replace
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from platform_api import database_privileges as privileges
from platform_api import database_privileges_behavior_v1 as behavior_v1
from platform_api import database_privileges_v1 as policy_v1
from platform_api.database_system_semantic_v1 import (
    POSTGRES16_ALPINE_REHEARSAL_SYSTEM_SEMANTIC_SHA256,
    POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256,
    pgaudit_log_classes_cover_protected_writes,
)
from platform_api.database import Base
from platform_api import models  # noqa: F401 - register metadata
from platform_api import platform_admin_access_models  # noqa: F401


def _principals() -> tuple[behavior_v1.DatabasePrincipalEvidence, ...]:
    return tuple(
        behavior_v1.DatabasePrincipalEvidence(
            role_name=database_role,
            role_comment=policy_v1.DATABASE_ROLE_COMMENT_BY_PROCESS[process_role],
            can_login=True,
            is_superuser=False,
            inherits=False,
            can_create_role=False,
            can_create_database=False,
            can_replicate=False,
            bypasses_rls=False,
            connection_limit=policy_v1.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS[
                process_role
            ],
            credential_validity_ok=True,
        )
        for process_role, database_role in policy_v1.DATABASE_ROLE_BY_PROCESS.items()
    )


def _runtime_evidence(process_role: str) -> behavior_v1.PlatformDatabaseEvidence:
    database_role = policy_v1.DATABASE_ROLE_BY_PROCESS[process_role]
    return behavior_v1.PlatformDatabaseEvidence(
        current_user=database_role,
        session_user=database_role,
        ssl_active=True,
        current_schema="public",
        explicit_schemas=("public",),
        database_owner=policy_v1.MIGRATION_DATABASE_ROLE,
        public_schema_owner="pg_database_owner",
        principals=_principals(),
        membership_count=0,
        role_setting_count=0,
        parameter_acl_count=0,
        external_owned_object_count=0,
        cross_database_acl_count=0,
        cross_database_dependency_count=0,
        global_role_dependency_count=0,
        system_acl_count=0,
        system_acl_sha256=policy_v1.SYSTEM_ACL_SHA256,
        system_semantic_sha256=(
            POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256
        ),
        system_extension_surface_exact=True,
        pgaudit_preloaded=True,
        pgaudit_log_class_coverage=True,
        credential_logging_policy_exact=True,
        system_unsafe_object_count=0,
        public_unsafe_object_count=0,
        legacy_pending_work_count=0,
        foreign_owned_object_count=0,
        column_acl_count=0,
        database_acl=frozenset(
            (*item, policy_v1.MIGRATION_DATABASE_ROLE, False)
            for item in policy_v1.EXPECTED_DATABASE_ACL
        ),
        schema_acl=frozenset(
            (*item, policy_v1.MIGRATION_DATABASE_ROLE, False)
            for item in policy_v1.EXPECTED_SCHEMA_ACL
        ),
        table_names=policy_v1.TABLES | {"alembic_version"},
        table_acl=frozenset(
            (*item, policy_v1.MIGRATION_DATABASE_ROLE, False)
            for item in policy_v1.EXPECTED_TABLE_ACL
        ),
        sequence_acl=frozenset(),
        routine_acl=frozenset(),
        default_acl=policy_v1.EXPECTED_DEFAULT_ACL,
        catalog_sha256=policy_v1.CATALOG_SHA256,
        alembic_heads=(policy_v1.ALEMBIC_HEAD,),
    )


@pytest.mark.parametrize(
    "process_role", tuple(policy_v1.PRIVILEGES_BY_PROCESS)
)
def test_each_runtime_role_has_one_exact_valid_database_evidence(process_role):
    behavior_v1.validate_platform_database_evidence(
        _runtime_evidence(process_role),
        process_role,
        require_runtime_acl=True,
        require_head=True,
        policy=policy_v1,
    )


def test_production_requires_the_debian_pgaudit_release_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    evidence = _runtime_evidence("platform-api")
    for drifted in (
        replace(
            evidence,
            system_semantic_sha256=(
                POSTGRES16_ALPINE_REHEARSAL_SYSTEM_SEMANTIC_SHA256
            ),
            pgaudit_preloaded=False,
            pgaudit_log_class_coverage=False,
        ),
        replace(evidence, system_extension_surface_exact=False),
        replace(evidence, pgaudit_preloaded=False),
        replace(evidence, pgaudit_log_class_coverage=False),
    ):
        with pytest.raises(behavior_v1.PlatformDatabaseAttestationError):
            behavior_v1.validate_platform_database_evidence(
                drifted,
                "platform-api",
                require_runtime_acl=True,
                require_head=True,
                policy=policy_v1,
            )


def test_staging_accepts_only_an_exact_rehearsal_or_full_production_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "staging")
    production = _runtime_evidence("dispatcher")
    rehearsal = replace(
        production,
        system_semantic_sha256=(
            POSTGRES16_ALPINE_REHEARSAL_SYSTEM_SEMANTIC_SHA256
        ),
        pgaudit_preloaded=False,
        pgaudit_log_class_coverage=False,
    )
    behavior_v1.validate_platform_database_evidence(
        rehearsal,
        "dispatcher",
        require_runtime_acl=True,
        require_head=True,
        policy=policy_v1,
    )
    for mismatched in (
        replace(rehearsal, pgaudit_preloaded=True),
        replace(rehearsal, pgaudit_log_class_coverage=True),
        replace(production, pgaudit_preloaded=False),
        replace(production, pgaudit_log_class_coverage=False),
    ):
        with pytest.raises(behavior_v1.PlatformDatabaseAttestationError):
            behavior_v1.validate_platform_database_evidence(
                mismatched,
                "dispatcher",
                require_runtime_acl=True,
                require_head=True,
                policy=policy_v1,
            )


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("ddl,role,write", True),
        ("all", True),
        ("all,-read", True),
        ("all,-write", False),
        ("write,role,ddl,-ddl", False),
        ("none", False),
        ("ddl,role", False),
        ("ddl,role,write,unknown", False),
        (None, False),
    ),
)
def test_pgaudit_log_class_contract(value, expected) -> None:
    assert pgaudit_log_classes_cover_protected_writes(value) is expected


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("ssl_active", False),
        ("current_schema", "platform_api"),
        ("explicit_schemas", ("public", "shadow")),
        ("membership_count", 1),
        ("role_setting_count", 1),
        ("parameter_acl_count", 1),
        ("external_owned_object_count", 1),
        ("cross_database_acl_count", 1),
        ("cross_database_dependency_count", 1),
        ("global_role_dependency_count", 1),
        ("system_acl_count", 1),
        ("system_semantic_sha256", "sha256:" + "0" * 64),
        ("system_extension_surface_exact", False),
        ("pgaudit_preloaded", False),
        ("pgaudit_log_class_coverage", False),
        ("credential_logging_policy_exact", False),
        ("system_unsafe_object_count", 1),
        ("public_unsafe_object_count", 1),
        ("legacy_pending_work_count", 1),
        ("foreign_owned_object_count", 1),
        ("column_acl_count", 1),
        ("catalog_sha256", "0" * 64),
        ("alembic_heads", ("0035_operations_evidence",)),
    ),
)
def test_runtime_attestation_rejects_identity_ddl_and_catalog_drift(field, value):
    evidence = replace(_runtime_evidence("platform-api"), **{field: value})
    with pytest.raises(behavior_v1.PlatformDatabaseAttestationError):
        behavior_v1.validate_platform_database_evidence(
            evidence,
            "platform-api",
            require_runtime_acl=True,
            require_head=True,
            policy=policy_v1,
        )


def test_runtime_attestation_rejects_migration_dsn_and_extra_worker_dml():
    evidence = _runtime_evidence("dispatcher")
    with pytest.raises(behavior_v1.PlatformDatabaseAttestationError):
        behavior_v1.validate_platform_database_evidence(
            replace(
                evidence,
                current_user=policy_v1.MIGRATION_DATABASE_ROLE,
                session_user=policy_v1.MIGRATION_DATABASE_ROLE,
            ),
            "dispatcher",
            require_runtime_acl=True,
            require_head=True,
            policy=policy_v1,
        )

    extra_acl = evidence.table_acl | {
        (
            "relay_submission_outbox",
            policy_v1.DATABASE_ROLE_BY_PROCESS["dispatcher"],
            "DELETE",
            policy_v1.MIGRATION_DATABASE_ROLE,
            False,
        )
    }
    with pytest.raises(behavior_v1.PlatformDatabaseAttestationError):
        behavior_v1.validate_platform_database_evidence(
            replace(evidence, table_acl=extra_acl),
            "dispatcher",
            require_runtime_acl=True,
            require_head=True,
            policy=policy_v1,
        )


def test_runtime_attestation_rejects_grant_option_and_unknown_table():
    evidence = _runtime_evidence("relay-sync")
    one_acl = next(iter(evidence.table_acl))
    grantable_acl = (evidence.table_acl - {one_acl}) | {
        (*one_acl[:4], True)
    }
    with pytest.raises(behavior_v1.PlatformDatabaseAttestationError):
        behavior_v1.validate_platform_database_evidence(
            replace(evidence, table_acl=grantable_acl),
            "relay-sync",
            require_runtime_acl=True,
            require_head=True,
            policy=policy_v1,
        )
    with pytest.raises(behavior_v1.PlatformDatabaseAttestationError):
        behavior_v1.validate_platform_database_evidence(
            replace(evidence, table_names=evidence.table_names | {"rogue_table"}),
            "relay-sync",
            require_runtime_acl=True,
            require_head=True,
            policy=policy_v1,
        )


def test_migration_preflight_allows_no_runtime_acl_but_never_runtime_use():
    evidence = replace(
        _runtime_evidence("platform-api"),
        current_user=policy_v1.MIGRATION_DATABASE_ROLE,
        session_user=policy_v1.MIGRATION_DATABASE_ROLE,
        database_acl=frozenset(),
        schema_acl=frozenset(),
        table_names=frozenset(),
        table_acl=frozenset(),
        default_acl=frozenset(),
        catalog_sha256="uncommitted",
        alembic_heads=(),
    )
    behavior_v1.validate_platform_database_evidence(
        evidence,
        "migration",
        require_runtime_acl=False,
        require_head=False,
        policy=policy_v1,
    )
    with pytest.raises(behavior_v1.PlatformDatabaseAttestationError):
        behavior_v1.validate_platform_database_evidence(
            evidence,
            "migration",
            require_runtime_acl=True,
            require_head=False,
            policy=policy_v1,
        )


def test_v1_table_manifest_remains_frozen_before_auth_lifecycle_tables():
    auth_tables = {
        "account_security_events",
        "auth_sessions",
        "company_invitations",
        "external_identities",
        "oidc_login_transactions",
    }
    assert policy_v1.TABLES.isdisjoint(auth_tables)
    behavior_v1.assert_platform_database_manifest_matches_metadata(policy_v1.TABLES)
    with pytest.raises(AssertionError, match="privilege manifest is stale"):
        behavior_v1.assert_platform_database_manifest_matches_metadata(
            Base.metadata.tables
        )


def _migration_module():
    path = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "0036_platform_database_roles.py"
    )
    spec = importlib.util.spec_from_file_location(
        "platform_database_roles_0036_test", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0036_uses_frozen_v1_acl_when_live_manifest_changes(monkeypatch):
    migration = _migration_module()
    future_manifest = {
        **dict(privileges.PLATFORM_DATABASE_PRIVILEGES_BY_PROCESS),
        "future-worker": {"future_table": frozenset({"SELECT"})},
    }
    monkeypatch.setattr(
        privileges,
        "PLATFORM_DATABASE_PRIVILEGES_BY_PROCESS",
        future_manifest,
    )

    statements: list[str] = []
    bind = SimpleNamespace(
        scalar=lambda _: "platform_schema_canary",
        dialect=SimpleNamespace(
            identifier_preparer=SimpleNamespace(
                quote=lambda value: f'"{value}"'
            )
        ),
    )
    monkeypatch.setattr(
        migration,
        "op",
        SimpleNamespace(
            get_bind=lambda: bind,
            execute=lambda statement: statements.append(str(statement)),
        ),
    )
    migration._apply_runtime_acl()
    rendered = "\n".join(statements)
    assert "future_table" not in rendered
    assert "future_worker" not in rendered
    assert "CREATE ROLE" not in rendered
    assert "ALTER ROLE" not in rendered
    assert "PASSWORD" not in rendered


def test_0036_imports_complete_frozen_behavior_not_live_runtime(monkeypatch):
    migration = _migration_module()
    assert migration.attest_platform_database_connection is behavior_v1.attest_platform_database_connection
    assert migration.collect_platform_database_evidence is behavior_v1.collect_platform_database_evidence
    assert migration.validate_platform_database_acl_evidence is behavior_v1.validate_platform_database_acl_evidence
    assert migration.protected_platform_runtime_requested is behavior_v1.protected_platform_runtime_requested_v1

    def sentinel(*_args, **_kwargs):
        pytest.fail("live behavior was imported")

    monkeypatch.setattr(privileges, "attest_platform_database_connection", sentinel)
    monkeypatch.setattr(privileges, "collect_platform_database_evidence", sentinel)
    monkeypatch.setattr(privileges, "validate_platform_database_acl_evidence", sentinel)
    assert migration.attest_platform_database_connection is not sentinel
    assert migration.collect_platform_database_evidence is not sentinel
    assert migration.validate_platform_database_acl_evidence is not sentinel


def test_engine_connection_listener_attests_every_checkout_on_that_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("PLATFORM_PROTECTED_RUNTIME", "true")
    observed: list[tuple[object, int]] = []

    def attest(connection, process_role, **_):
        observed.append((connection, id(connection.connection.driver_connection)))
        assert process_role == "platform-api"
        assert connection.scalar(text("SELECT 1")) == 1

    monkeypatch.setattr(
        behavior_v1,
        "attest_platform_database_connection",
        attest,
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        behavior_v1.install_platform_database_connection_attestation(
            engine,
            "platform-api",
        )
        with engine.connect() as first:
            assert first.scalar(text("SELECT 2")) == 2
        with engine.connect() as second:
            assert second.scalar(text("SELECT 3")) == 3
        assert len(observed) == 2
        assert observed[0][1] == observed[1][1]
        with pytest.raises(
            behavior_v1.PlatformDatabaseAttestationError,
            match="engine process role",
        ):
            behavior_v1.install_platform_database_connection_attestation(
                engine,
                "dispatcher",
            )
    finally:
        engine.dispose()


def test_0036_preflight_is_first_and_read_only(monkeypatch):
    migration = _migration_module()
    events: list[str] = []
    connection = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    monkeypatch.setattr(migration, "protected_platform_runtime_requested", lambda: True)
    monkeypatch.setattr(
        migration,
        "op",
        SimpleNamespace(get_bind=lambda: connection),
    )
    monkeypatch.setattr(
        migration,
        "attest_platform_database_connection",
        lambda *args, **kwargs: events.append("preflight"),
    )
    monkeypatch.setattr(
        migration,
        "validate_platform_migration_source_state",
        lambda *args, **kwargs: events.append("source-state"),
    )
    monkeypatch.setattr(
        migration,
        "_apply_runtime_acl",
        lambda: events.append("mutate"),
    )
    monkeypatch.setattr(
        migration,
        "collect_platform_database_evidence",
        lambda *args, **kwargs: events.append("collect") or object(),
    )
    monkeypatch.setattr(
        migration,
        "validate_platform_database_acl_evidence",
        lambda *args, **kwargs: events.append("validate"),
    )
    migration.upgrade()
    assert events == [
        "source-state",
        "preflight",
        "mutate",
        "collect",
        "validate",
    ]


@pytest.mark.skipif(
    not os.getenv("PLATFORM_DATABASE_ATTESTATION_TEST_URL"),
    reason="requires a dedicated PostgreSQL 16 schema-attestation database",
)
def test_postgres_catalog_fingerprint_detects_critical_guard_and_rogue_function():
    engine = create_engine(os.environ["PLATFORM_DATABASE_ATTESTATION_TEST_URL"])
    mutations = (
        "DROP TRIGGER trg_ledger_entries_immutable ON ledger_entries",
        "ALTER TABLE ledger_entries DROP CONSTRAINT ck_ledger_amount_nonnegative",
        "CREATE FUNCTION rogue_owner_function() RETURNS integer LANGUAGE SQL AS 'SELECT 1'",
    )
    try:
        with engine.connect() as connection:
            assert behavior_v1.platform_catalog_sha256(connection) == policy_v1.CATALOG_SHA256
            connection.commit()
            for statement in mutations:
                transaction = connection.begin()
                connection.execute(text(statement))
                assert (
                    behavior_v1.platform_catalog_sha256(connection)
                    != policy_v1.CATALOG_SHA256
                )
                transaction.rollback()
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not os.getenv("PLATFORM_DATABASE_ATTESTATION_TEST_URL"),
    reason="requires a dedicated PostgreSQL 16 schema-attestation database",
)
def test_postgres_system_acl_baseline_catches_public_catalog_grants_and_recovers():
    engine = create_engine(os.environ["PLATFORM_DATABASE_ATTESTATION_TEST_URL"])
    try:
        with engine.connect() as connection:
            assert (
                behavior_v1.platform_system_acl_sha256(connection)
                in policy_v1.QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256
            )
            connection.commit()
            mutations = (
                "GRANT EXECUTE ON FUNCTION "
                "pg_catalog.pg_read_file(text,bigint,bigint,boolean) TO PUBLIC",
                "GRANT SELECT ON TABLE pg_catalog.pg_authid TO PUBLIC",
            )
            for statement in mutations:
                transaction = connection.begin()
                connection.execute(text(statement))
                assert (
                    behavior_v1.platform_system_acl_sha256(connection)
                    not in policy_v1.QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256
                )
                transaction.rollback()
                assert (
                    behavior_v1.platform_system_acl_sha256(connection)
                    in policy_v1.QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256
                )
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not os.getenv("PLATFORM_DATABASE_ATTESTATION_TEST_URL"),
    reason="requires a dedicated PostgreSQL 16 schema-attestation database",
)
def test_postgres_parameter_grant_is_collected_and_rejected():
    database_url = os.environ["PLATFORM_DATABASE_ATTESTATION_TEST_URL"]
    database_name = make_url(database_url).database or ""
    if "canary" not in database_name:
        pytest.skip("parameter privilege mutation requires an explicit canary database")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            existing = set(
                connection.scalars(
                    text(
                        "SELECT rolname FROM pg_roles "
                        "WHERE rolname LIKE 'platform_%'"
                    )
                )
            )
            if existing.intersection(policy_v1.DATABASE_ROLE_BY_PROCESS.values()):
                pytest.skip("canary database already has Platform roles")
            for process_role, database_role in (
                policy_v1.DATABASE_ROLE_BY_PROCESS.items()
            ):
                connection.execute(
                    text(
                        f'CREATE ROLE "{database_role}" LOGIN NOINHERIT '
                        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                    )
                )
                connection.execute(
                    text(
                        f'COMMENT ON ROLE "{database_role}" IS '
                        f"'{policy_v1.DATABASE_ROLE_COMMENT_BY_PROCESS[process_role]}'"
                    )
                )
            connection.execute(
                text(
                    "GRANT SET ON PARAMETER session_replication_role "
                    "TO platform_api"
                )
            )
            evidence = behavior_v1.collect_platform_database_evidence(
                connection, policy=policy_v1
            )
            assert evidence.parameter_acl_count == 1
            with pytest.raises(behavior_v1.PlatformDatabaseAttestationError):
                behavior_v1.validate_platform_database_evidence(
                    replace(
                        _runtime_evidence("platform-api"),
                        parameter_acl_count=evidence.parameter_acl_count,
                    ),
                    "platform-api",
                    require_runtime_acl=True,
                    require_head=True,
                    policy=policy_v1,
                )
            transaction.rollback()
    finally:
        engine.dispose()
