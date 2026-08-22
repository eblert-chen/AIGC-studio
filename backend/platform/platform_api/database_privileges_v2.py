"""Immutable Platform PostgreSQL principal/ACL policy for Alembic 0037.

This module is a historical migration artifact.  Do not edit it when adding a
table, process, or privilege: add a new versioned policy and a new Alembic
revision, then point the runtime registry at that policy.  Keeping the role and
ACL literals here prevents a future live model from reinterpreting revision
0037 during a fresh database build.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping


ALEMBIC_HEAD = "0037_production_auth_lifecycle"
MIGRATION_DATABASE_ROLE = "platform_migration"
DATABASE_ROLE_BY_PROCESS = MappingProxyType(
    {
        "migration": "platform_migration",
        "platform-api": "platform_api",
        "dispatcher": "platform_dispatcher",
        "relay-sync": "platform_relay_sync",
        "timeout-worker": "platform_timeout_worker",
        "publishing-worker": "platform_publishing_worker",
        "download-gateway-registration-worker": (
            "platform_download_gateway_worker"
        ),
    }
)
DATABASE_ROLE_COMMENT_BY_PROCESS = MappingProxyType(
    {
        "migration": "ai-video/platform-db-principal/v1/migration",
        "platform-api": "ai-video/platform-db-principal/v1/platform-api",
        "dispatcher": "ai-video/platform-db-principal/v1/dispatcher",
        "relay-sync": "ai-video/platform-db-principal/v1/relay-sync",
        "timeout-worker": "ai-video/platform-db-principal/v1/timeout-worker",
        "publishing-worker": (
            "ai-video/platform-db-principal/v1/publishing-worker"
        ),
        "download-gateway-registration-worker": (
            "ai-video/platform-db-principal/v1/"
            "download-gateway-registration-worker"
        ),
    }
)
DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS = MappingProxyType(
    {
        "migration": 1,
        "platform-api": 32,
        "dispatcher": 8,
        "relay-sync": 8,
        "timeout-worker": 4,
        "publishing-worker": 4,
        "download-gateway-registration-worker": 4,
    }
)

TABLES = frozenset(
    {
        "account_security_events",
        "auth_sessions",
        "audit_logs",
        "channel_cost_entries",
        "companies",
        "company_memberships",
        "company_invitations",
        "company_model_grants",
        "company_resource_grants",
        "download_completions",
        "download_gateway_registration_attempts",
        "download_records",
        "external_identities",
        "generation_tasks",
        "input_assets",
        "ledger_entries",
        "member_permission_overrides",
        "membership_roles",
        "model_capabilities",
        "model_definitions",
        "oidc_login_transactions",
        "permissions",
        "personal_download_records",
        "personal_ledger_entries",
        "personal_retail_model_grants",
        "personal_wallet_accounts",
        "personal_workspaces",
        "platform_admin_access_profiles",
        "platform_admin_activity",
        "platform_admin_permissions",
        "platform_admin_role_assignments",
        "platform_admin_role_permissions",
        "platform_admin_roles",
        "platform_admin_user_permission_overrides",
        "publication_attempts",
        "publication_jobs",
        "publisher_connections",
        "publisher_oauth_sessions",
        "relay_callback_events",
        "relay_channel_operations",
        "relay_operations_snapshots",
        "relay_provider_alert_events",
        "relay_route_operations_snapshots",
        "relay_submission_outbox",
        "relay_task_stage_events",
        "resource_definitions",
        "role_permissions",
        "roles",
        "task_artifacts",
        "task_input_assets",
        "task_timeout_events",
        "users",
        "wallet_accounts",
    }
)

_ALL_DML = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})
_READ = frozenset({"SELECT"})
_READ_WRITE = frozenset({"SELECT", "UPDATE"})
_APPEND = frozenset({"SELECT", "INSERT"})
_INVITATION_HISTORY = frozenset({"SELECT", "INSERT", "UPDATE"})
_AUTH_LIFECYCLE_PRIVILEGES = MappingProxyType(
    {
        "account_security_events": _APPEND,
        "auth_sessions": _INVITATION_HISTORY,
        "company_invitations": _INVITATION_HISTORY,
        "external_identities": _INVITATION_HISTORY,
        "oidc_login_transactions": _ALL_DML,
        "users": _INVITATION_HISTORY,
    }
)
_APPEND_ONLY_API_TABLES = frozenset(
    {
        "account_security_events",
        "audit_logs",
        "channel_cost_entries",
        "download_completions",
        "download_records",
        "ledger_entries",
        "personal_download_records",
        "personal_ledger_entries",
        "relay_callback_events",
        "relay_provider_alert_events",
        "relay_task_stage_events",
        "task_artifacts",
        "task_timeout_events",
    }
)


def _api_privileges() -> dict[str, frozenset[str]]:
    return {
        table_name: (
            _AUTH_LIFECYCLE_PRIVILEGES[table_name]
            if table_name in _AUTH_LIFECYCLE_PRIVILEGES
            else _APPEND
            if table_name in _APPEND_ONLY_API_TABLES
            else _ALL_DML
        )
        for table_name in TABLES
    }


PRIVILEGES_BY_PROCESS: Mapping[str, Mapping[str, frozenset[str]]] = (
    MappingProxyType(
        {
            "platform-api": MappingProxyType(_api_privileges()),
            "dispatcher": MappingProxyType(
                {
                    "generation_tasks": _READ_WRITE,
                    "input_assets": _READ,
                    "ledger_entries": _APPEND,
                    "model_definitions": _READ,
                    "personal_ledger_entries": _APPEND,
                    "personal_wallet_accounts": _READ_WRITE,
                    "relay_submission_outbox": _READ_WRITE,
                    "wallet_accounts": _READ_WRITE,
                }
            ),
            "relay-sync": MappingProxyType(
                {
                    "generation_tasks": _READ_WRITE,
                    "ledger_entries": _APPEND,
                    "personal_ledger_entries": _APPEND,
                    "personal_wallet_accounts": _READ_WRITE,
                    "task_artifacts": _APPEND,
                    "wallet_accounts": _READ_WRITE,
                }
            ),
            "timeout-worker": MappingProxyType(
                {
                    "generation_tasks": _READ_WRITE,
                    "ledger_entries": _APPEND,
                    "personal_ledger_entries": _APPEND,
                    "personal_wallet_accounts": _READ_WRITE,
                    "relay_submission_outbox": _READ_WRITE,
                    "task_artifacts": _APPEND,
                    "task_timeout_events": _APPEND,
                    "wallet_accounts": _READ_WRITE,
                }
            ),
            "publishing-worker": MappingProxyType(
                {
                    "company_resource_grants": _READ,
                    "publication_attempts": frozenset(
                        {"SELECT", "INSERT", "UPDATE"}
                    ),
                    "publication_jobs": _READ_WRITE,
                    "publisher_connections": _READ_WRITE,
                    "resource_definitions": _READ,
                    "task_artifacts": _READ,
                }
            ),
            "download-gateway-registration-worker": MappingProxyType(
                {
                    "download_gateway_registration_attempts": _READ_WRITE,
                    "download_records": _APPEND,
                }
            ),
        }
    )
)


def _expected_table_acl() -> frozenset[tuple[str, str, str]]:
    expected: set[tuple[str, str, str]] = set()
    for process_role, privileges_by_table in PRIVILEGES_BY_PROCESS.items():
        database_role = DATABASE_ROLE_BY_PROCESS[process_role]
        for table_name, privileges in privileges_by_table.items():
            expected.update(
                (table_name, database_role, privilege)
                for privilege in privileges
            )
        expected.add(("alembic_version", database_role, "SELECT"))
    return frozenset(expected)


EXPECTED_TABLE_ACL = _expected_table_acl()
EXPECTED_DATABASE_ACL = frozenset(
    (role_name, "CONNECT")
    for process_role, role_name in DATABASE_ROLE_BY_PROCESS.items()
    if process_role != "migration"
)
EXPECTED_SCHEMA_ACL = frozenset(
    (role_name, "USAGE")
    for process_role, role_name in DATABASE_ROLE_BY_PROCESS.items()
    if process_role != "migration"
)
EXPECTED_DEFAULT_ACL = frozenset(
    {
        (
            MIGRATION_DATABASE_ROLE,
            "",
            "FUNCTION",
            MIGRATION_DATABASE_ROLE,
            "EXECUTE",
            MIGRATION_DATABASE_ROLE,
            False,
        )
    }
)

# Filled from a PostgreSQL 16 fresh ``alembic upgrade head`` using the
# normalized catalog projection in database_privileges_behavior_v2.py. The projection
# includes object shape, ownership, effective ACLs, and default ACLs; the
# Alembic head is attested as a separate exact value.
CATALOG_SHA256 = "7427bb1db832d08d75b86d426b63c867464358b3a7d74b07bd7e659421db5f0f"

# Alembic may enter the frozen v2 policy only from an exact empty database,
# the immediately preceding protected head, or an already-complete replay.
# These hashes were qualified from fresh PostgreSQL 16 Alpine databases using
# the projection frozen in database_privileges_behavior_v2.py.  A migration
# from any older/unknown/partially-written shape is deliberately unsupported.
EMPTY_CATALOG_SHA256 = (
    "e3b0c44298fc1c149afbf4c8996fb924"
    "27ae41e4649b934ca495991b7852b855"
)
MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD = MappingProxyType(
    {
        "0036_platform_db_roles": (
            "816e9b60476fff7b6e1fc9ee6e7c5c46"
            "0bf971ead1dbccb8ac8fce86e5fcffeb"
        ),
        ALEMBIC_HEAD: CATALOG_SHA256,
    }
)

# PostgreSQL 16 qualified initdb baselines. These hashes commit normalized
# effective ACLs together with acldefault() and pg_init_privs for pg_catalog,
# information_schema, languages, and tablespaces. They are paired with one
# exact system-semantic fingerprint: accepting an ACL from one distribution
# beside another distribution's catalog would not be a qualified release.
POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256 = MappingProxyType(
    {
        # postgres:16-alpine 16.14; staging rehearsal only.
        "sha256:d67b2a78cc769e306723fee1dc7a7282ee4d481b6e2b8353ee1ef1bf81d574eb": (
            "b39796701e84718166fd32a6cb71506207172c0b3af88b32486a139df106adad"
        ),
        # Debian 13 PostgreSQL 16.14 + pgAudit 16.1 production candidate.
        "sha256:f97e2f23386ec637defd1cf62f84def8cd76198bfd9e784a1646d1942215b12a": (
            "56d2422deb29ada83a65082758430bab8b57dc453013d33356272e7d85296cb6"
        ),
    }
)
SYSTEM_ACL_SHA256 = POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256[
    "sha256:f97e2f23386ec637defd1cf62f84def8cd76198bfd9e784a1646d1942215b12a"
]
QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256 = frozenset(
    POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256.values()
)
