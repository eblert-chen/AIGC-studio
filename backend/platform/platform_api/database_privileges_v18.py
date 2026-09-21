"""Immutable Platform PostgreSQL ACL policy for Alembic 0053.

Revision 0053 adds immutable Director Shot Package evidence and exact task
bindings. Only the customer Platform API may read or append these records.
The catalog remains deliberately unqualified until captured on PostgreSQL 16.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from . import database_privileges_v17 as policy_v17


ALEMBIC_HEAD = "0053_director_shot_packages"
MIGRATION_DATABASE_ROLE = policy_v17.MIGRATION_DATABASE_ROLE
DATABASE_ROLE_BY_PROCESS = policy_v17.DATABASE_ROLE_BY_PROCESS
DATABASE_ROLE_COMMENT_BY_PROCESS = policy_v17.DATABASE_ROLE_COMMENT_BY_PROCESS
DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS = (
    policy_v17.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS
)
DIRECTOR_SHOT_PACKAGE_TABLES = frozenset(
    {"director_shot_packages", "task_director_shot_packages"}
)
TABLES = policy_v17.TABLES | DIRECTOR_SHOT_PACKAGE_TABLES

_privileges: dict[str, dict[str, frozenset[str]]] = {
    process: dict(table_privileges)
    for process, table_privileges in policy_v17.PRIVILEGES_BY_PROCESS.items()
}
_privileges["platform-api"].update(
    {
        "director_shot_packages": frozenset({"SELECT", "INSERT"}),
        "task_director_shot_packages": frozenset({"SELECT", "INSERT"}),
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
EXPECTED_DATABASE_ACL = policy_v17.EXPECTED_DATABASE_ACL
EXPECTED_SCHEMA_ACL = policy_v17.EXPECTED_SCHEMA_ACL
EXPECTED_DEFAULT_ACL = policy_v17.EXPECTED_DEFAULT_ACL
UNQUALIFIED_CATALOG_SHA256 = "0" * 64
CATALOG_SHA256 = UNQUALIFIED_CATALOG_SHA256
EMPTY_CATALOG_SHA256 = policy_v17.EMPTY_CATALOG_SHA256
MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD = MappingProxyType(
    {
        policy_v17.ALEMBIC_HEAD: policy_v17.CATALOG_SHA256,
        ALEMBIC_HEAD: CATALOG_SHA256,
    }
)
POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256 = (
    policy_v17.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256
)
SYSTEM_ACL_SHA256 = policy_v17.SYSTEM_ACL_SHA256
QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256 = (
    policy_v17.QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256
)

