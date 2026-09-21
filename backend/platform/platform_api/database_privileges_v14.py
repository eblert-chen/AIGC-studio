"""Immutable Platform PostgreSQL ACL policy for Alembic 0049.

Revision 0049 adds durable provider delivery, verified webhook replay, exact
external statement bindings, dispute-debt attribution and enterprise dunning.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from . import database_privileges_v13 as policy_v13


ALEMBIC_HEAD = "0049_payment_finance_closure"
MIGRATION_DATABASE_ROLE = policy_v13.MIGRATION_DATABASE_ROLE
DATABASE_ROLE_BY_PROCESS = policy_v13.DATABASE_ROLE_BY_PROCESS
DATABASE_ROLE_COMMENT_BY_PROCESS = policy_v13.DATABASE_ROLE_COMMENT_BY_PROCESS
DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS = (
    policy_v13.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS
)

CLOSURE_TABLES = frozenset(
    {
        "enterprise_dunning_actions",
        "enterprise_dunning_runs",
        "finance_reconciliation_run_sources",
        "payment_dispute_debt_recovery_allocations",
        "payment_dispute_debt_recovery_reversals",
        "payment_mandates",
        "payment_provider_commands",
        "payment_settlement_batches",
        "payment_webhook_inbox_events",
        "provider_cost_statement_batches",
        "provider_cost_statement_lines",
    }
)
APPEND_ONLY_CLOSURE_TABLES = frozenset(
    {
        "enterprise_dunning_actions",
        "finance_reconciliation_run_sources",
        "payment_dispute_debt_recovery_allocations",
        "payment_dispute_debt_recovery_reversals",
        "payment_settlement_batches",
        "provider_cost_statement_batches",
        "provider_cost_statement_lines",
    }
)
MUTABLE_CLOSURE_TABLES = CLOSURE_TABLES - APPEND_ONLY_CLOSURE_TABLES
TABLES = policy_v13.TABLES | CLOSURE_TABLES

_privileges: dict[str, dict[str, frozenset[str]]] = {
    process: dict(table_privileges)
    for process, table_privileges in policy_v13.PRIVILEGES_BY_PROCESS.items()
}
_privileges["platform-api"].update(
    {
        table_name: frozenset({"SELECT", "INSERT"})
        for table_name in APPEND_ONLY_CLOSURE_TABLES
    }
)
_privileges["platform-api"].update(
    {
        table_name: frozenset({"SELECT", "INSERT", "UPDATE"})
        for table_name in MUTABLE_CLOSURE_TABLES
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
EXPECTED_DATABASE_ACL = policy_v13.EXPECTED_DATABASE_ACL
EXPECTED_SCHEMA_ACL = policy_v13.EXPECTED_SCHEMA_ACL
EXPECTED_DEFAULT_ACL = policy_v13.EXPECTED_DEFAULT_ACL

# Filled only from an independently captured PostgreSQL 16 catalog. Protected
# runtime remains fail-closed until the exact digests are qualified.
UNQUALIFIED_CATALOG_SHA256 = "0" * 64
CATALOG_SHA256 = UNQUALIFIED_CATALOG_SHA256
EMPTY_CATALOG_SHA256 = policy_v13.EMPTY_CATALOG_SHA256
MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD = MappingProxyType(
    {
        policy_v13.ALEMBIC_HEAD: policy_v13.CATALOG_SHA256,
        ALEMBIC_HEAD: CATALOG_SHA256,
    }
)
POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256 = (
    policy_v13.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256
)
SYSTEM_ACL_SHA256 = policy_v13.SYSTEM_ACL_SHA256
QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256 = (
    policy_v13.QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256
)
