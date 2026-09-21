"""Immutable Platform PostgreSQL ACL policy for Alembic 0050.

Revision 0050 adds immutable commercial model release plans and their mutable,
fail-closed execution state.  The Relay catalog worker may consume an already
approved plan; it still cannot create or alter pricing evidence.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from . import database_privileges_v14 as policy_v14


ALEMBIC_HEAD = "0050_model_commercial_release"
MIGRATION_DATABASE_ROLE = policy_v14.MIGRATION_DATABASE_ROLE
DATABASE_ROLE_BY_PROCESS = policy_v14.DATABASE_ROLE_BY_PROCESS
DATABASE_ROLE_COMMENT_BY_PROCESS = policy_v14.DATABASE_ROLE_COMMENT_BY_PROCESS
DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS = (
    policy_v14.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS
)

MODEL_COMMERCIAL_RELEASE_TABLES = frozenset(
    {
        "model_commercial_release_plans",
        "model_commercial_release_executions",
    }
)
TABLES = policy_v14.TABLES | MODEL_COMMERCIAL_RELEASE_TABLES

_privileges: dict[str, dict[str, frozenset[str]]] = {
    process: dict(table_privileges)
    for process, table_privileges in policy_v14.PRIVILEGES_BY_PROCESS.items()
}
_privileges["platform-api"].update(
    {
        "model_commercial_release_plans": frozenset({"SELECT", "INSERT"}),
        "model_commercial_release_executions": frozenset(
            {"SELECT", "INSERT", "UPDATE"}
        ),
    }
)
_privileges["relay-catalog-sync"].update(
    {
        "companies": frozenset({"SELECT"}),
        "company_model_grants": frozenset({"SELECT", "INSERT", "UPDATE"}),
        "company_point_price_versions": frozenset({"SELECT", "INSERT"}),
        "personal_retail_model_grants": frozenset(
            {"SELECT", "INSERT", "UPDATE"}
        ),
        "model_commercial_release_plans": frozenset({"SELECT"}),
        "model_commercial_release_executions": frozenset({"SELECT", "UPDATE"}),
    }
)
PRIVILEGES_BY_PROCESS: Mapping[
    str, Mapping[str, frozenset[str]]
] = MappingProxyType(
    {
        process: MappingProxyType(table_privileges)
        for process, table_privileges in _privileges.items()
    }
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
EXPECTED_DATABASE_ACL = policy_v14.EXPECTED_DATABASE_ACL
EXPECTED_SCHEMA_ACL = policy_v14.EXPECTED_SCHEMA_ACL
EXPECTED_DEFAULT_ACL = policy_v14.EXPECTED_DEFAULT_ACL

# Filled only from an independently captured PostgreSQL 16 catalog. Protected
# runtime remains fail-closed until this exact revision is qualified.
UNQUALIFIED_CATALOG_SHA256 = "0" * 64
CATALOG_SHA256 = UNQUALIFIED_CATALOG_SHA256
EMPTY_CATALOG_SHA256 = policy_v14.EMPTY_CATALOG_SHA256
MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD = MappingProxyType(
    {
        policy_v14.ALEMBIC_HEAD: policy_v14.CATALOG_SHA256,
        ALEMBIC_HEAD: CATALOG_SHA256,
    }
)
POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256 = (
    policy_v14.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256
)
SYSTEM_ACL_SHA256 = policy_v14.SYSTEM_ACL_SHA256
QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256 = (
    policy_v14.QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256
)
