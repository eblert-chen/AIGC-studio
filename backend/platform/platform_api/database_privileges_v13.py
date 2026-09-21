"""Immutable Platform PostgreSQL ACL policy for Alembic 0048.

Revision 0048 adds the commercial cash/points, payment lifecycle, enterprise
receivables and four-way reconciliation schema. Payment and enterprise cash
state stays with the Platform API; existing billing workers receive only the
personal lot/allocation projection needed to reserve, settle, or release a
task. Durable financial facts are append-only at both ACL and trigger layers.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from . import database_privileges_v12 as policy_v12


ALEMBIC_HEAD = "0048_commercial_billing"
MIGRATION_DATABASE_ROLE = policy_v12.MIGRATION_DATABASE_ROLE
DATABASE_ROLE_BY_PROCESS = policy_v12.DATABASE_ROLE_BY_PROCESS
DATABASE_ROLE_COMMENT_BY_PROCESS = policy_v12.DATABASE_ROLE_COMMENT_BY_PROCESS
DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS = (
    policy_v12.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS
)

COMMERCIAL_TABLES = frozenset(
    {
        "accounts_receivable_ledger_entries",
        "auto_recharge_executions",
        "auto_recharge_rules",
        "company_billing_accounts",
        "company_billing_contract_versions",
        "company_billing_cycles",
        "company_invoice_lines",
        "company_invoices",
        "finance_reconciliation_exceptions",
        "finance_reconciliation_resolutions",
        "finance_reconciliation_runs",
        "finance_reconciliation_snapshots",
        "payment_attempts",
        "payment_disputes",
        "payment_orders",
        "payment_refunds",
        "payment_settlement_entries",
        "payment_transactions",
        "payment_webhook_receipts",
        "personal_point_lots",
        "personal_task_point_lot_allocations",
        "point_lot_settlement_value_allocations",
    }
)

APPEND_ONLY_COMMERCIAL_TABLES = frozenset(
    {
        "accounts_receivable_ledger_entries",
        "company_billing_contract_versions",
        "company_invoice_lines",
        "finance_reconciliation_exceptions",
        "finance_reconciliation_resolutions",
        "finance_reconciliation_snapshots",
        "payment_settlement_entries",
        "payment_transactions",
        "payment_webhook_receipts",
        "point_lot_settlement_value_allocations",
    }
)
MUTABLE_COMMERCIAL_TABLES = COMMERCIAL_TABLES - APPEND_ONLY_COMMERCIAL_TABLES
TABLES = policy_v12.TABLES | COMMERCIAL_TABLES

_privileges: dict[str, dict[str, frozenset[str]]] = {
    process: dict(table_privileges)
    for process, table_privileges in policy_v12.PRIVILEGES_BY_PROCESS.items()
}
_privileges["platform-api"].update(
    {
        table_name: frozenset({"SELECT", "INSERT"})
        for table_name in APPEND_ONLY_COMMERCIAL_TABLES
    }
)
_privileges["platform-api"].update(
    {
        table_name: frozenset({"SELECT", "INSERT", "UPDATE"})
        for table_name in MUTABLE_COMMERCIAL_TABLES
    }
)
for _billing_process in ("dispatcher", "relay-sync", "timeout-worker"):
    _privileges[_billing_process]["personal_point_lots"] = _privileges[
        _billing_process
    ]["company_point_lots"]
    _privileges[_billing_process][
        "personal_task_point_lot_allocations"
    ] = _privileges[_billing_process]["task_point_lot_allocations"]
for _settlement_process in ("relay-sync", "timeout-worker"):
    _privileges[_settlement_process][
        "point_lot_settlement_value_allocations"
    ] = frozenset({"SELECT", "INSERT"})
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
EXPECTED_DATABASE_ACL = policy_v12.EXPECTED_DATABASE_ACL
EXPECTED_SCHEMA_ACL = policy_v12.EXPECTED_SCHEMA_ACL
EXPECTED_DEFAULT_ACL = policy_v12.EXPECTED_DEFAULT_ACL

UNQUALIFIED_CATALOG_SHA256 = "0" * 64
CATALOG_SHA256 = UNQUALIFIED_CATALOG_SHA256
EMPTY_CATALOG_SHA256 = policy_v12.EMPTY_CATALOG_SHA256
MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD = MappingProxyType(
    {
        policy_v12.ALEMBIC_HEAD: policy_v12.CATALOG_SHA256,
        ALEMBIC_HEAD: CATALOG_SHA256,
    }
)
POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256 = (
    policy_v12.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256
)
SYSTEM_ACL_SHA256 = policy_v12.SYSTEM_ACL_SHA256
QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256 = (
    policy_v12.QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256
)
