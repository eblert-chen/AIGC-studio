"""Immutable Platform PostgreSQL principal/ACL policy for Alembic 0043.

Revision 0043 adds only two rows to the server-owned platform-administrator
permission catalog. It does not add a table, sequence, routine, or grant, so
the qualified PostgreSQL catalog and ACL evidence intentionally remain equal
to frozen policy v7 while the Alembic head advances independently.
"""

from __future__ import annotations

from types import MappingProxyType

from . import database_privileges_v7 as policy_v7


ALEMBIC_HEAD = "0043_admin_task_content"
MIGRATION_DATABASE_ROLE = policy_v7.MIGRATION_DATABASE_ROLE
DATABASE_ROLE_BY_PROCESS = policy_v7.DATABASE_ROLE_BY_PROCESS
DATABASE_ROLE_COMMENT_BY_PROCESS = policy_v7.DATABASE_ROLE_COMMENT_BY_PROCESS
DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS = (
    policy_v7.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS
)
TABLES = policy_v7.TABLES
PRIVILEGES_BY_PROCESS = policy_v7.PRIVILEGES_BY_PROCESS
EXPECTED_TABLE_ACL = policy_v7.EXPECTED_TABLE_ACL
EXPECTED_DATABASE_ACL = policy_v7.EXPECTED_DATABASE_ACL
EXPECTED_SCHEMA_ACL = policy_v7.EXPECTED_SCHEMA_ACL
EXPECTED_DEFAULT_ACL = policy_v7.EXPECTED_DEFAULT_ACL

# 0043 is data-only. The schema/ACL fingerprint therefore remains byte-for-byte
# equal to the qualified 0042 value; the exact head is checked separately.
CATALOG_SHA256 = policy_v7.CATALOG_SHA256
EMPTY_CATALOG_SHA256 = policy_v7.EMPTY_CATALOG_SHA256
MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD = MappingProxyType(
    {
        policy_v7.ALEMBIC_HEAD: policy_v7.CATALOG_SHA256,
        ALEMBIC_HEAD: CATALOG_SHA256,
    }
)
POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256 = (
    policy_v7.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256
)
SYSTEM_ACL_SHA256 = policy_v7.SYSTEM_ACL_SHA256
QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256 = (
    policy_v7.QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256
)
