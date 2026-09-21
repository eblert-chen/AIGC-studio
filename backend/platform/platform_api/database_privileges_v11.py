"""Immutable Platform PostgreSQL ACL policy for Alembic 0046.

Revision 0046 adds the company points wallet, immutable point ledger and price
versions, point lots, and task-to-lot allocations.  No new runtime principal is
introduced: each existing process receives only the point-table privileges
matching its frozen legacy-cents responsibility.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from . import database_privileges_v10 as policy_v10


ALEMBIC_HEAD = "0046_company_points_billing_v2"
MIGRATION_DATABASE_ROLE = policy_v10.MIGRATION_DATABASE_ROLE
DATABASE_ROLE_BY_PROCESS = policy_v10.DATABASE_ROLE_BY_PROCESS
DATABASE_ROLE_COMMENT_BY_PROCESS = policy_v10.DATABASE_ROLE_COMMENT_BY_PROCESS
DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS = (
    policy_v10.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS
)

POINT_TABLES = frozenset(
    {
        "company_point_wallet_accounts",
        "company_point_lots",
        "company_point_ledger_entries",
        "task_point_lot_allocations",
        "company_point_price_versions",
    }
)
TABLES = policy_v10.TABLES | POINT_TABLES

_privileges: dict[str, dict[str, frozenset[str]]] = {
    process: dict(table_privileges)
    for process, table_privileges in policy_v10.PRIVILEGES_BY_PROCESS.items()
}

# The customer API creates/migrates wallets, approves append-only prices,
# reserves lots, and may settle/release tasks in synchronous test paths.
_privileges["platform-api"].update(
    {
        "company_point_wallet_accounts": frozenset(
            {"SELECT", "INSERT", "UPDATE"}
        ),
        "company_point_lots": frozenset({"SELECT", "INSERT", "UPDATE"}),
        "company_point_ledger_entries": frozenset({"SELECT", "INSERT"}),
        "task_point_lot_allocations": frozenset(
            {"SELECT", "INSERT", "UPDATE"}
        ),
        "company_point_price_versions": frozenset({"SELECT", "INSERT"}),
    }
)

# These workers mirror their frozen legacy-cents duties.  They may advance a
# reservation projection and append its ledger evidence, but cannot mint lots,
# create allocations, publish prices, or delete financial history.
for _process in ("dispatcher", "relay-sync", "timeout-worker"):
    _privileges[_process].update(
        {
            "company_point_wallet_accounts": frozenset({"SELECT", "UPDATE"}),
            "company_point_lots": frozenset({"SELECT", "UPDATE"}),
            "company_point_ledger_entries": frozenset({"SELECT", "INSERT"}),
            "task_point_lot_allocations": frozenset({"SELECT", "UPDATE"}),
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
EXPECTED_DATABASE_ACL = policy_v10.EXPECTED_DATABASE_ACL
EXPECTED_SCHEMA_ACL = policy_v10.EXPECTED_SCHEMA_ACL
EXPECTED_DEFAULT_ACL = policy_v10.EXPECTED_DEFAULT_ACL

UNQUALIFIED_CATALOG_SHA256 = "0" * 64
# Qualified on PostgreSQL 16 + TLS + pgAudit from two independent template0
# databases and one exact 0045 -> 0046 transition.  The transition applied the
# production 0046 ACL delta directly; no post-migration ACL normalization was
# used to manufacture this catalog.
CATALOG_SHA256 = "d4b70386e7592c884394b45d5d0ee00ba3ca7d31cc2881d237ba071f358c142c"
EMPTY_CATALOG_SHA256 = policy_v10.EMPTY_CATALOG_SHA256
MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD = MappingProxyType(
    {
        policy_v10.ALEMBIC_HEAD: policy_v10.CATALOG_SHA256,
        ALEMBIC_HEAD: CATALOG_SHA256,
    }
)
POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256 = (
    policy_v10.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256
)
SYSTEM_ACL_SHA256 = policy_v10.SYSTEM_ACL_SHA256
QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256 = (
    policy_v10.QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256
)
