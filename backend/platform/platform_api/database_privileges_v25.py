"""Immutable Platform PostgreSQL ACL policy for Alembic 0060.

Revision 0060 adds the commercial release batch journal plus a nullable
``batch_id`` binding on the immutable commercial release plan table.

The new journal is mutable (it moves ``approved`` -> ``released``/``abandoned``)
and is written only by the Platform API request path, so only the Platform API
runtime role receives it.  Every existing table and ACL entry is inherited
unchanged from v24.  No PostgreSQL reference test is runtime qualification; the
current catalog intentionally remains UNQUALIFIED.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from . import database_privileges_v24 as policy_v24


ALEMBIC_HEAD = "0060_commercial_release_batches"
MIGRATION_DATABASE_ROLE = policy_v24.MIGRATION_DATABASE_ROLE
DATABASE_ROLE_BY_PROCESS = policy_v24.DATABASE_ROLE_BY_PROCESS
DATABASE_ROLE_COMMENT_BY_PROCESS = policy_v24.DATABASE_ROLE_COMMENT_BY_PROCESS
DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS = (
    policy_v24.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS
)

# The journal records an owner decision and its atomic activation outcome, so
# the request path must be able to append and then advance the row.
COMMERCIAL_RELEASE_BATCH_TABLES = frozenset(
    {
        "model_commercial_release_batches",
    }
)

TABLES = policy_v24.TABLES | COMMERCIAL_RELEASE_BATCH_TABLES

_privileges: dict[str, dict[str, frozenset[str]]] = {
    process: dict(table_privileges)
    for process, table_privileges in policy_v24.PRIVILEGES_BY_PROCESS.items()
}
_privileges["platform-api"].update(
    {
        table_name: frozenset({"SELECT", "INSERT", "UPDATE"})
        for table_name in COMMERCIAL_RELEASE_BATCH_TABLES
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
EXPECTED_DATABASE_ACL = policy_v24.EXPECTED_DATABASE_ACL
EXPECTED_SCHEMA_ACL = policy_v24.EXPECTED_SCHEMA_ACL
EXPECTED_DEFAULT_ACL = policy_v24.EXPECTED_DEFAULT_ACL
UNQUALIFIED_CATALOG_SHA256 = "0" * 64
CATALOG_SHA256 = UNQUALIFIED_CATALOG_SHA256
EMPTY_CATALOG_SHA256 = policy_v24.EMPTY_CATALOG_SHA256
MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD = MappingProxyType(
    {
        policy_v24.ALEMBIC_HEAD: policy_v24.CATALOG_SHA256,
        ALEMBIC_HEAD: CATALOG_SHA256,
    }
)
POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256 = (
    policy_v24.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256
)
SYSTEM_ACL_SHA256 = policy_v24.SYSTEM_ACL_SHA256
QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256 = (
    policy_v24.QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256
)
