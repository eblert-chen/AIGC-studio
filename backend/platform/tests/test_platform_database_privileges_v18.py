from __future__ import annotations

from dataclasses import replace

import pytest

from platform_api import database_privileges_behavior_v18 as behavior_v18
from platform_api import database_privileges_v17 as policy_v17
from platform_api import database_privileges_v18 as policy_v18
from platform_api.database_system_semantic_v1 import (
    POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256,
)


def _runtime_evidence(
    process_role: str,
) -> behavior_v18.PlatformDatabaseEvidence:
    principals = tuple(
        behavior_v18.DatabasePrincipalEvidence(
            role_name=database_role,
            role_comment=policy_v18.DATABASE_ROLE_COMMENT_BY_PROCESS[role],
            can_login=True,
            is_superuser=False,
            inherits=False,
            can_create_role=False,
            can_create_database=False,
            can_replicate=False,
            bypasses_rls=False,
            connection_limit=(
                policy_v18.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS[role]
            ),
            credential_validity_ok=True,
        )
        for role, database_role in policy_v18.DATABASE_ROLE_BY_PROCESS.items()
    )
    current_user = policy_v18.DATABASE_ROLE_BY_PROCESS[process_role]
    return behavior_v18.PlatformDatabaseEvidence(
        current_user=current_user,
        session_user=current_user,
        ssl_active=True,
        current_schema="public",
        explicit_schemas=("public",),
        database_owner=policy_v18.MIGRATION_DATABASE_ROLE,
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
        system_acl_sha256=policy_v18.SYSTEM_ACL_SHA256,
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
                policy_v18.MIGRATION_DATABASE_ROLE,
                False,
            )
            for role, privilege in policy_v18.EXPECTED_DATABASE_ACL
        ),
        schema_acl=frozenset(
            (
                role,
                privilege,
                policy_v18.MIGRATION_DATABASE_ROLE,
                False,
            )
            for role, privilege in policy_v18.EXPECTED_SCHEMA_ACL
        ),
        table_names=policy_v18.TABLES | {"alembic_version"},
        table_acl=frozenset(
            (
                table_name,
                role,
                privilege,
                policy_v18.MIGRATION_DATABASE_ROLE,
                False,
            )
            for table_name, role, privilege in policy_v18.EXPECTED_TABLE_ACL
        ),
        sequence_acl=frozenset(),
        routine_acl=frozenset(),
        default_acl=policy_v18.EXPECTED_DEFAULT_ACL,
        catalog_sha256=policy_v18.CATALOG_SHA256,
        alembic_heads=(policy_v18.ALEMBIC_HEAD,),
    )


def test_v18_extends_only_platform_api_with_append_only_package_tables() -> None:
    assert policy_v18.TABLES == (
        policy_v17.TABLES | policy_v18.DIRECTOR_SHOT_PACKAGE_TABLES
    )
    for table_name in policy_v18.DIRECTOR_SHOT_PACKAGE_TABLES:
        assert policy_v18.PRIVILEGES_BY_PROCESS["platform-api"][table_name] == (
            frozenset({"SELECT", "INSERT"})
        )
    for process_role, privileges in policy_v18.PRIVILEGES_BY_PROCESS.items():
        if process_role != "platform-api":
            assert policy_v18.DIRECTOR_SHOT_PACKAGE_TABLES.isdisjoint(privileges)


@pytest.mark.parametrize(
    "process_role",
    ["platform-api", "dispatcher", "relay-sync", "timeout-worker"],
)
def test_v18_accepts_only_the_exact_director_package_acl(
    process_role: str,
) -> None:
    behavior_v18.validate_platform_database_evidence(
        _runtime_evidence(process_role),
        process_role,
        require_runtime_acl=True,
        require_head=True,
        policy=policy_v18,
    )


def test_v18_rejects_missing_or_widened_director_package_acl() -> None:
    evidence = _runtime_evidence("platform-api")
    role = policy_v18.DATABASE_ROLE_BY_PROCESS["platform-api"]
    required = (
        "director_shot_packages",
        role,
        "INSERT",
        policy_v18.MIGRATION_DATABASE_ROLE,
        False,
    )
    missing = replace(evidence, table_acl=evidence.table_acl - {required})
    widened = replace(
        evidence,
        table_acl=evidence.table_acl
        | {
            (
                "task_director_shot_packages",
                role,
                "UPDATE",
                policy_v18.MIGRATION_DATABASE_ROLE,
                False,
            )
        },
    )
    for invalid in (missing, widened):
        with pytest.raises(
            behavior_v18.PlatformDatabaseAttestationError,
            match="database privileges|table privileges",
        ):
            behavior_v18.validate_platform_database_evidence(
                invalid,
                "platform-api",
                require_runtime_acl=True,
                require_head=True,
                policy=policy_v18,
            )


def test_v18_catalog_is_deliberately_unqualified() -> None:
    assert policy_v18.CATALOG_SHA256 == policy_v18.UNQUALIFIED_CATALOG_SHA256
    assert policy_v18.CATALOG_SHA256 == "0" * 64

