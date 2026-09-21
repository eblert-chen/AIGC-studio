from __future__ import annotations

from dataclasses import replace

import pytest

from platform_api import database_privileges_behavior_v11 as behavior_v11
from platform_api import database_privileges_v11 as policy_v11
from platform_api.database_system_semantic_v1 import (
    POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256,
)


def _runtime_evidence(
    process_role: str,
) -> behavior_v11.PlatformDatabaseEvidence:
    principals = tuple(
        behavior_v11.DatabasePrincipalEvidence(
            role_name=database_role,
            role_comment=policy_v11.DATABASE_ROLE_COMMENT_BY_PROCESS[role],
            can_login=True,
            is_superuser=False,
            inherits=False,
            can_create_role=False,
            can_create_database=False,
            can_replicate=False,
            bypasses_rls=False,
            connection_limit=(
                policy_v11.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS[role]
            ),
            credential_validity_ok=True,
        )
        for role, database_role in policy_v11.DATABASE_ROLE_BY_PROCESS.items()
    )
    current_user = policy_v11.DATABASE_ROLE_BY_PROCESS[process_role]
    return behavior_v11.PlatformDatabaseEvidence(
        current_user=current_user,
        session_user=current_user,
        ssl_active=True,
        current_schema="public",
        explicit_schemas=("public",),
        database_owner=policy_v11.MIGRATION_DATABASE_ROLE,
        public_schema_owner="pg_database_owner",
        principals=principals,
        membership_count=0,
        role_setting_count=0,
        parameter_acl_count=0,
        external_owned_object_count=0,
        cross_database_acl_count=0,
        cross_database_dependency_count=0,
        global_role_dependency_count=0,
        system_acl_count=0,
        system_acl_sha256=policy_v11.SYSTEM_ACL_SHA256,
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
            (
                role,
                privilege,
                policy_v11.MIGRATION_DATABASE_ROLE,
                False,
            )
            for role, privilege in policy_v11.EXPECTED_DATABASE_ACL
        ),
        schema_acl=frozenset(
            (
                role,
                privilege,
                policy_v11.MIGRATION_DATABASE_ROLE,
                False,
            )
            for role, privilege in policy_v11.EXPECTED_SCHEMA_ACL
        ),
        table_names=policy_v11.TABLES | {"alembic_version"},
        table_acl=frozenset(
            (
                table_name,
                role,
                privilege,
                policy_v11.MIGRATION_DATABASE_ROLE,
                False,
            )
            for table_name, role, privilege in policy_v11.EXPECTED_TABLE_ACL
        ),
        sequence_acl=frozenset(),
        routine_acl=frozenset(),
        default_acl=policy_v11.EXPECTED_DEFAULT_ACL,
        catalog_sha256=policy_v11.CATALOG_SHA256,
        alembic_heads=(policy_v11.ALEMBIC_HEAD,),
    )


@pytest.mark.parametrize(
    "process_role",
    ["platform-api", "dispatcher", "relay-sync", "timeout-worker"],
)
def test_v11_accepts_only_the_exact_points_acl(
    monkeypatch: pytest.MonkeyPatch,
    process_role: str,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    behavior_v11.validate_platform_database_evidence(
        _runtime_evidence(process_role),
        process_role,
        require_runtime_acl=True,
        require_head=True,
        policy=policy_v11,
    )


def test_v11_rejects_point_ledger_update_or_any_points_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    evidence = _runtime_evidence("platform-api")
    role = policy_v11.DATABASE_ROLE_BY_PROCESS["platform-api"]
    for table_name, privilege in (
        ("company_point_ledger_entries", "UPDATE"),
        ("company_point_lots", "DELETE"),
        ("company_point_price_versions", "UPDATE"),
    ):
        widened = replace(
            evidence,
            table_acl=evidence.table_acl
            | {
                (
                    table_name,
                    role,
                    privilege,
                    policy_v11.MIGRATION_DATABASE_ROLE,
                    False,
                )
            },
        )
        with pytest.raises(
            behavior_v11.PlatformDatabaseAttestationError,
            match="database privileges|table privileges",
        ):
            behavior_v11.validate_platform_database_evidence(
                widened,
                "platform-api",
                require_runtime_acl=True,
                require_head=True,
                policy=policy_v11,
            )


def test_v11_workers_cannot_mint_lots_allocations_or_prices() -> None:
    for process_role in ("dispatcher", "relay-sync", "timeout-worker"):
        privileges = policy_v11.PRIVILEGES_BY_PROCESS[process_role]
        assert "INSERT" not in privileges["company_point_lots"]
        assert "INSERT" not in privileges["task_point_lot_allocations"]
        assert "company_point_price_versions" not in privileges


def test_v11_catalog_is_the_pg16_qualified_points_schema() -> None:
    assert policy_v11.CATALOG_SHA256 == (
        "d4b70386e7592c884394b45d5d0ee00ba3ca7d31cc2881d237ba071f358c142c"
    )
    assert policy_v11.CATALOG_SHA256 != policy_v11.UNQUALIFIED_CATALOG_SHA256
