from __future__ import annotations

from dataclasses import replace

import pytest

from platform_api import database_privileges_behavior_v10 as behavior_v10
from platform_api import database_privileges_v10 as policy_v10
from platform_api.database_system_semantic_v1 import (
    POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256,
)


def _runtime_evidence(
    process_role: str,
) -> behavior_v10.PlatformDatabaseEvidence:
    principals = tuple(
        behavior_v10.DatabasePrincipalEvidence(
            role_name=database_role,
            role_comment=(
                policy_v10.DATABASE_ROLE_COMMENT_BY_PROCESS[role]
            ),
            can_login=True,
            is_superuser=False,
            inherits=False,
            can_create_role=False,
            can_create_database=False,
            can_replicate=False,
            bypasses_rls=False,
            connection_limit=(
                policy_v10.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS[role]
            ),
            credential_validity_ok=True,
        )
        for role, database_role in policy_v10.DATABASE_ROLE_BY_PROCESS.items()
    )
    current_user = policy_v10.DATABASE_ROLE_BY_PROCESS[process_role]
    return behavior_v10.PlatformDatabaseEvidence(
        current_user=current_user,
        session_user=current_user,
        ssl_active=True,
        current_schema="public",
        explicit_schemas=("public",),
        database_owner=policy_v10.MIGRATION_DATABASE_ROLE,
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
        system_acl_sha256=policy_v10.SYSTEM_ACL_SHA256,
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
                policy_v10.MIGRATION_DATABASE_ROLE,
                False,
            )
            for role, privilege in policy_v10.EXPECTED_DATABASE_ACL
        ),
        schema_acl=frozenset(
            (
                role,
                privilege,
                policy_v10.MIGRATION_DATABASE_ROLE,
                False,
            )
            for role, privilege in policy_v10.EXPECTED_SCHEMA_ACL
        ),
        table_names=policy_v10.TABLES | {"alembic_version"},
        table_acl=frozenset(
            (
                table_name,
                role,
                privilege,
                policy_v10.MIGRATION_DATABASE_ROLE,
                False,
            )
            for table_name, role, privilege in policy_v10.EXPECTED_TABLE_ACL
        ),
        sequence_acl=frozenset(),
        routine_acl=frozenset(),
        default_acl=policy_v10.EXPECTED_DEFAULT_ACL,
        catalog_sha256=policy_v10.CATALOG_SHA256,
        alembic_heads=(policy_v10.ALEMBIC_HEAD,),
    )


def test_relay_catalog_sync_role_accepts_only_exact_minimum_acl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    evidence = _runtime_evidence("relay-catalog-sync")
    behavior_v10.validate_platform_database_evidence(
        evidence,
        "relay-catalog-sync",
        require_runtime_acl=True,
        require_head=True,
        policy=policy_v10,
    )

    role = policy_v10.DATABASE_ROLE_BY_PROCESS["relay-catalog-sync"]
    widened = replace(
        evidence,
        table_acl=evidence.table_acl
        | {
            (
                "users",
                role,
                "SELECT",
                policy_v10.MIGRATION_DATABASE_ROLE,
                False,
            )
        },
    )
    with pytest.raises(
        behavior_v10.PlatformDatabaseAttestationError,
        match="table privileges|database privileges",
    ):
        behavior_v10.validate_platform_database_evidence(
            widened,
            "relay-catalog-sync",
            require_runtime_acl=True,
            require_head=True,
            policy=policy_v10,
        )


def test_v10_rejects_missing_or_misconfigured_catalog_sync_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    evidence = _runtime_evidence("platform-api")
    role = policy_v10.DATABASE_ROLE_BY_PROCESS["relay-catalog-sync"]
    with pytest.raises(
        behavior_v10.PlatformDatabaseAttestationError,
        match="principal inventory",
    ):
        behavior_v10.validate_platform_database_evidence(
            replace(
                evidence,
                principals=tuple(
                    principal
                    for principal in evidence.principals
                    if principal.role_name != role
                ),
            ),
            "platform-api",
            require_runtime_acl=True,
            require_head=True,
            policy=policy_v10,
        )
    altered = tuple(
        replace(principal, connection_limit=8)
        if principal.role_name == role
        else principal
        for principal in evidence.principals
    )
    with pytest.raises(
        behavior_v10.PlatformDatabaseAttestationError,
        match="principal properties",
    ):
        behavior_v10.validate_platform_database_evidence(
            replace(evidence, principals=altered),
            "platform-api",
            require_runtime_acl=True,
            require_head=True,
            policy=policy_v10,
        )
