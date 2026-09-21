"""Immutable Platform PostgreSQL ACL policy for Alembic 0047.

Revision 0047 adds only the append-only owner-self product-context switch
journal. The customer API may select and insert that evidence; no worker or
catalog process receives access and no runtime principal is introduced.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from . import database_privileges_v11 as policy_v11


ALEMBIC_HEAD = "0047_owner_self_product_context"
MIGRATION_DATABASE_ROLE = policy_v11.MIGRATION_DATABASE_ROLE
DATABASE_ROLE_BY_PROCESS = policy_v11.DATABASE_ROLE_BY_PROCESS
DATABASE_ROLE_COMMENT_BY_PROCESS = policy_v11.DATABASE_ROLE_COMMENT_BY_PROCESS
DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS = (
    policy_v11.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS
)

OWNER_SELF_TABLES = frozenset({"auth_product_context_switches"})
TABLES = policy_v11.TABLES | OWNER_SELF_TABLES

_privileges: dict[str, dict[str, frozenset[str]]] = {
    process: dict(table_privileges)
    for process, table_privileges in policy_v11.PRIVILEGES_BY_PROCESS.items()
}
_privileges["platform-api"]["auth_product_context_switches"] = frozenset(
    {"SELECT", "INSERT"}
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
EXPECTED_DATABASE_ACL = policy_v11.EXPECTED_DATABASE_ACL
EXPECTED_SCHEMA_ACL = policy_v11.EXPECTED_SCHEMA_ACL
EXPECTED_DEFAULT_ACL = policy_v11.EXPECTED_DEFAULT_ACL

UNQUALIFIED_CATALOG_SHA256 = "0" * 64
CATALOG_SHA256 = UNQUALIFIED_CATALOG_SHA256
EMPTY_CATALOG_SHA256 = policy_v11.EMPTY_CATALOG_SHA256
MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD = MappingProxyType(
    {
        policy_v11.ALEMBIC_HEAD: policy_v11.CATALOG_SHA256,
        ALEMBIC_HEAD: CATALOG_SHA256,
    }
)
POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256 = (
    policy_v11.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256
)
SYSTEM_ACL_SHA256 = policy_v11.SYSTEM_ACL_SHA256
QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256 = (
    policy_v11.QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256
)
