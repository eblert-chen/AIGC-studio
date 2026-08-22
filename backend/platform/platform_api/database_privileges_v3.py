"""Immutable Platform PostgreSQL principal/ACL policy for Alembic 0038.

Revision 0038 changes only catalog CHECK constraints.  Its role, table, and
ACL manifests intentionally reuse the already-frozen 0037 policy; the new
head, source fingerprint, and target fingerprint remain independently pinned
here so revision 0037 is never reinterpreted.
"""

from __future__ import annotations

from types import MappingProxyType

from . import database_privileges_v2 as policy_v2


ALEMBIC_HEAD = "0038_download_evidence_checks"
MIGRATION_DATABASE_ROLE = policy_v2.MIGRATION_DATABASE_ROLE
DATABASE_ROLE_BY_PROCESS = policy_v2.DATABASE_ROLE_BY_PROCESS
DATABASE_ROLE_COMMENT_BY_PROCESS = policy_v2.DATABASE_ROLE_COMMENT_BY_PROCESS
DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS = (
    policy_v2.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS
)
TABLES = policy_v2.TABLES
PRIVILEGES_BY_PROCESS = policy_v2.PRIVILEGES_BY_PROCESS
EXPECTED_TABLE_ACL = policy_v2.EXPECTED_TABLE_ACL
EXPECTED_DATABASE_ACL = policy_v2.EXPECTED_DATABASE_ACL
EXPECTED_SCHEMA_ACL = policy_v2.EXPECTED_SCHEMA_ACL
EXPECTED_DEFAULT_ACL = policy_v2.EXPECTED_DEFAULT_ACL

# Qualified from independent fresh PostgreSQL 16 databases after the immutable
# 0038 migration has been applied with the frozen v2 catalog projection.
CATALOG_SHA256 = "6fd6420e20423ac99e72262f7186a386e02ae6a98d613755f12ddc89f32ed71b"
EMPTY_CATALOG_SHA256 = policy_v2.EMPTY_CATALOG_SHA256
MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD = MappingProxyType(
    {
        policy_v2.ALEMBIC_HEAD: policy_v2.CATALOG_SHA256,
        ALEMBIC_HEAD: CATALOG_SHA256,
    }
)

POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256 = (
    policy_v2.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256
)
SYSTEM_ACL_SHA256 = policy_v2.SYSTEM_ACL_SHA256
QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256 = (
    policy_v2.QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256
)
