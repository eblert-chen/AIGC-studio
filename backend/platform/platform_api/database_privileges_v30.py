"""Immutable Platform PostgreSQL ACL policy for Alembic 0066.

Revision 0065 (point_expiry) adds expiry-settlement fields to company /
personal point-lot rows without introducing new tables or changing ACL.
Revision 0066 (relay_outbox_recovery) adds two nullable integer / timestamp
columns and an index to ``relay_submission_outbox`` — again, no new tables
and no permission changes. Every existing table and ACL entry is inherited
unchanged from v29. The catalog intentionally remains UNQUALIFIED.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from . import database_privileges_v29 as policy_v29


ALEMBIC_HEAD = "0066_relay_outbox_recovery"
DATABASE_ROLE_BY_PROCESS = policy_v29.DATABASE_ROLE_BY_PROCESS
MIGRATION_DATABASE_ROLE = policy_v29.MIGRATION_DATABASE_ROLE
DATABASE_ROLE_COMMENT_BY_PROCESS = policy_v29.DATABASE_ROLE_COMMENT_BY_PROCESS
DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS = (
    policy_v29.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS
)

TABLES = policy_v29.TABLES
PRIVILEGES_BY_PROCESS: Mapping[
    str, Mapping[str, frozenset[str]]
] = policy_v29.PRIVILEGES_BY_PROCESS


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
EXPECTED_DATABASE_ACL = policy_v29.EXPECTED_DATABASE_ACL
EXPECTED_SCHEMA_ACL = policy_v29.EXPECTED_SCHEMA_ACL
EXPECTED_DEFAULT_ACL = policy_v29.EXPECTED_DEFAULT_ACL
UNQUALIFIED_CATALOG_SHA256 = policy_v29.UNQUALIFIED_CATALOG_SHA256
CATALOG_SHA256 = UNQUALIFIED_CATALOG_SHA256
EMPTY_CATALOG_SHA256 = policy_v29.EMPTY_CATALOG_SHA256
MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD = MappingProxyType(
    {
        **dict(policy_v29.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD),
        ALEMBIC_HEAD: CATALOG_SHA256,
    }
)
POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256 = (
    policy_v29.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256
)
SYSTEM_ACL_SHA256 = policy_v29.SYSTEM_ACL_SHA256
QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256 = (
    policy_v29.QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256
)
