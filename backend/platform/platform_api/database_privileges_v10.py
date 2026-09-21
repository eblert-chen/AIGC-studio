"""Immutable Platform PostgreSQL principal/ACL policy for Alembic 0045.

Revision 0045 makes audit identity explicit for user and system actors and
introduces one dedicated runtime principal for periodic Relay catalog
reconciliation.  The worker may materialize model drafts and append audit
evidence; it cannot approve, publish, price, distribute, or read user rows.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from . import database_privileges_v9 as policy_v9


ALEMBIC_HEAD = "0045_system_audit_actor"
MIGRATION_DATABASE_ROLE = policy_v9.MIGRATION_DATABASE_ROLE

_PROCESS = "relay-catalog-sync"
_DATABASE_ROLE = "platform_relay_catalog_sync"

DATABASE_ROLE_BY_PROCESS = MappingProxyType(
    {
        **dict(policy_v9.DATABASE_ROLE_BY_PROCESS),
        _PROCESS: _DATABASE_ROLE,
    }
)
DATABASE_ROLE_COMMENT_BY_PROCESS = MappingProxyType(
    {
        **dict(policy_v9.DATABASE_ROLE_COMMENT_BY_PROCESS),
        _PROCESS: "ai-video/platform-db-principal/v1/relay-catalog-sync",
    }
)
DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS = MappingProxyType(
    {
        **dict(policy_v9.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS),
        _PROCESS: 4,
    }
)

TABLES = policy_v9.TABLES
_privileges: dict[str, Mapping[str, frozenset[str]]] = {
    process: MappingProxyType(dict(table_privileges))
    for process, table_privileges in policy_v9.PRIVILEGES_BY_PROCESS.items()
}
_privileges[_PROCESS] = MappingProxyType(
    {
        "model_definitions": frozenset({"SELECT", "INSERT", "UPDATE"}),
        "model_capabilities": frozenset({"SELECT", "INSERT"}),
        "audit_logs": frozenset({"INSERT"}),
    }
)
PRIVILEGES_BY_PROCESS: Mapping[
    str, Mapping[str, frozenset[str]]
] = MappingProxyType(_privileges)


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
EXPECTED_DATABASE_ACL = frozenset(
    (role_name, "CONNECT")
    for process_role, role_name in DATABASE_ROLE_BY_PROCESS.items()
    if process_role != "migration"
)
EXPECTED_SCHEMA_ACL = frozenset(
    (role_name, "USAGE")
    for process_role, role_name in DATABASE_ROLE_BY_PROCESS.items()
    if process_role != "migration"
)
EXPECTED_DEFAULT_ACL = policy_v9.EXPECTED_DEFAULT_ACL

# Qualified on PostgreSQL 16 from two independent ``TEMPLATE template0``
# databases after a fresh ``alembic upgrade head`` and from one
# 0045 -> 0044 -> 0045 round trip.  The normalized projection includes the
# frozen ``platform_migration`` default-privilege state; table/role ACLs and
# PostgreSQL system semantics remain independently attested.
UNQUALIFIED_CATALOG_SHA256 = "0" * 64
CATALOG_SHA256 = "7ce8849ecc4be298fe9889bdeaeb7ea17932a51c9ff9eeb024cc55bbcad44142"
EMPTY_CATALOG_SHA256 = policy_v9.EMPTY_CATALOG_SHA256
MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD = MappingProxyType(
    {
        policy_v9.ALEMBIC_HEAD: policy_v9.CATALOG_SHA256,
        ALEMBIC_HEAD: CATALOG_SHA256,
    }
)
POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256 = (
    policy_v9.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256
)
SYSTEM_ACL_SHA256 = policy_v9.SYSTEM_ACL_SHA256
QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256 = (
    policy_v9.QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256
)
