"""Immutable Platform PostgreSQL ACL policy for Alembic 0056.

Revision 0056 repairs task JSON immutability and permits explicit commercial
plan successors while freezing released execution state and receipts. It does
not add a table or widen any runtime role, so the v20 table and ACL manifests
remain exact. The catalog stays deliberately unqualified until captured on
PostgreSQL 16.
"""
from __future__ import annotations

from types import MappingProxyType

from . import database_privileges_v20 as policy_v20


ALEMBIC_HEAD = "0056_billing_integrity_guards"
MIGRATION_DATABASE_ROLE = policy_v20.MIGRATION_DATABASE_ROLE
DATABASE_ROLE_BY_PROCESS = policy_v20.DATABASE_ROLE_BY_PROCESS
DATABASE_ROLE_COMMENT_BY_PROCESS = policy_v20.DATABASE_ROLE_COMMENT_BY_PROCESS
DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS = (
    policy_v20.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS
)
TABLES = policy_v20.TABLES
PRIVILEGES_BY_PROCESS = policy_v20.PRIVILEGES_BY_PROCESS
EXPECTED_TABLE_ACL = policy_v20.EXPECTED_TABLE_ACL
EXPECTED_DATABASE_ACL = policy_v20.EXPECTED_DATABASE_ACL
EXPECTED_SCHEMA_ACL = policy_v20.EXPECTED_SCHEMA_ACL
EXPECTED_DEFAULT_ACL = policy_v20.EXPECTED_DEFAULT_ACL
UNQUALIFIED_CATALOG_SHA256 = "0" * 64
CATALOG_SHA256 = UNQUALIFIED_CATALOG_SHA256
EMPTY_CATALOG_SHA256 = policy_v20.EMPTY_CATALOG_SHA256
MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD = MappingProxyType(
    {
        policy_v20.ALEMBIC_HEAD: policy_v20.CATALOG_SHA256,
        ALEMBIC_HEAD: CATALOG_SHA256,
    }
)
POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256 = (
    policy_v20.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256
)
SYSTEM_ACL_SHA256 = policy_v20.SYSTEM_ACL_SHA256
QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256 = (
    policy_v20.QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256
)
