"""Immutable Platform PostgreSQL principal/ACL policy for Alembic 0044.

Revision 0044 persists the mutually exclusive product account type on users
and adds database-enforced personal/company boundary guards.  It does not add
tables or widen any runtime principal privilege, so the frozen table/ACL
manifest remains identical to policy v8 while the qualified catalog
fingerprint advances for the new column, constraints, trigger functions, and
triggers.
"""

from __future__ import annotations

from types import MappingProxyType

from . import database_privileges_v8 as policy_v8


ALEMBIC_HEAD = "0044_account_product_partition"
MIGRATION_DATABASE_ROLE = policy_v8.MIGRATION_DATABASE_ROLE
DATABASE_ROLE_BY_PROCESS = policy_v8.DATABASE_ROLE_BY_PROCESS
DATABASE_ROLE_COMMENT_BY_PROCESS = policy_v8.DATABASE_ROLE_COMMENT_BY_PROCESS
DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS = (
    policy_v8.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS
)
TABLES = policy_v8.TABLES
PRIVILEGES_BY_PROCESS = policy_v8.PRIVILEGES_BY_PROCESS
EXPECTED_TABLE_ACL = policy_v8.EXPECTED_TABLE_ACL
EXPECTED_DATABASE_ACL = policy_v8.EXPECTED_DATABASE_ACL
EXPECTED_SCHEMA_ACL = policy_v8.EXPECTED_SCHEMA_ACL
EXPECTED_DEFAULT_ACL = policy_v8.EXPECTED_DEFAULT_ACL

# Qualified on PostgreSQL 16.14 after the complete 0044 chain.  The hash is
# independent of migrated row contents and freezes only the normalized public
# schema catalog projected by database_privileges_behavior_v1.
CATALOG_SHA256 = "64640e8ccf7069fc6ca0773af64def56babfdb80101ea9cd22e6b8e7fc00c167"
EMPTY_CATALOG_SHA256 = policy_v8.EMPTY_CATALOG_SHA256
MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD = MappingProxyType(
    {
        policy_v8.ALEMBIC_HEAD: policy_v8.CATALOG_SHA256,
        ALEMBIC_HEAD: CATALOG_SHA256,
    }
)
POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256 = (
    policy_v8.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256
)
SYSTEM_ACL_SHA256 = policy_v8.SYSTEM_ACL_SHA256
QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256 = (
    policy_v8.QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256
)
