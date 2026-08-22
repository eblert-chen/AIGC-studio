"""Immutable Platform PostgreSQL principal/ACL policy for Alembic 0039.

Revision 0039 changes only two server defaults.  Role, table, and ACL
manifests reuse the frozen 0038 policy while the new head and normalized
catalog fingerprint are pinned independently.
"""

from __future__ import annotations

from types import MappingProxyType

from . import database_privileges_v3 as policy_v3


ALEMBIC_HEAD = "0039_new_api_relay_defaults"
MIGRATION_DATABASE_ROLE = policy_v3.MIGRATION_DATABASE_ROLE
DATABASE_ROLE_BY_PROCESS = policy_v3.DATABASE_ROLE_BY_PROCESS
DATABASE_ROLE_COMMENT_BY_PROCESS = policy_v3.DATABASE_ROLE_COMMENT_BY_PROCESS
DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS = (
    policy_v3.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS
)
TABLES = policy_v3.TABLES
PRIVILEGES_BY_PROCESS = policy_v3.PRIVILEGES_BY_PROCESS
EXPECTED_TABLE_ACL = policy_v3.EXPECTED_TABLE_ACL
EXPECTED_DATABASE_ACL = policy_v3.EXPECTED_DATABASE_ACL
EXPECTED_SCHEMA_ACL = policy_v3.EXPECTED_SCHEMA_ACL
EXPECTED_DEFAULT_ACL = policy_v3.EXPECTED_DEFAULT_ACL

# Qualified from two independent PostgreSQL 16 databases after applying the
# immutable 0039 migration to the frozen, ACL-attested 0038 source shape.
CATALOG_SHA256 = "c9a154d6c87c714d6af4826bb43ce7fae56f73322066f399cee526d18280b757"
EMPTY_CATALOG_SHA256 = policy_v3.EMPTY_CATALOG_SHA256
MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD = MappingProxyType(
    {
        policy_v3.ALEMBIC_HEAD: policy_v3.CATALOG_SHA256,
        ALEMBIC_HEAD: CATALOG_SHA256,
    }
)

POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256 = (
    policy_v3.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256
)
SYSTEM_ACL_SHA256 = policy_v3.SYSTEM_ACL_SHA256
QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256 = (
    policy_v3.QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256
)
