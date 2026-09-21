"""Add closed-loop commercial billing and financial reconciliation.

Revision ID: 0048_commercial_billing
Revises: 0047_owner_self_product_context
"""
from __future__ import annotations

import re
from typing import Sequence

from alembic import op
import sqlalchemy as sa

from platform_api import database_privileges_v13 as policy_v13
from platform_api.database_privileges_behavior_v12 import (
    protected_platform_runtime_requested_v12,
)


revision: str = "0048_commercial_billing"
down_revision: str | None = "0047_owner_self_product_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_IMMUTABLE_FACT_TABLES = (
    "payment_webhook_receipts",
    "payment_transactions",
    "point_lot_settlement_value_allocations",
    "company_billing_contract_versions",
    "company_invoice_lines",
    "accounts_receivable_ledger_entries",
    "payment_settlement_entries",
    "finance_reconciliation_snapshots",
    "finance_reconciliation_exceptions",
    "finance_reconciliation_resolutions",
)
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")


def _quote_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise RuntimeError("Platform database ACL identifier is invalid")
    return f'"{value}"'


def _apply_commercial_acl() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    runtime_roles = tuple(
        role
        for process, role in policy_v13.DATABASE_ROLE_BY_PROCESS.items()
        if process != "migration"
    )
    for table_name in sorted(policy_v13.COMMERCIAL_TABLES):
        quoted_table = _quote_identifier(table_name)
        op.execute(
            sa.text(
                "REVOKE ALL PRIVILEGES ON TABLE public."
                f"{quoted_table} FROM PUBLIC"
            )
        )
        for database_role in runtime_roles:
            op.execute(
                sa.text(
                    "REVOKE ALL PRIVILEGES ON TABLE public."
                    f"{quoted_table} FROM {_quote_identifier(database_role)}"
                )
            )
    for process_role, privileges_by_table in policy_v13.PRIVILEGES_BY_PROCESS.items():
        quoted_role = _quote_identifier(
            policy_v13.DATABASE_ROLE_BY_PROCESS[process_role]
        )
        for table_name in sorted(policy_v13.COMMERCIAL_TABLES):
            privileges = privileges_by_table.get(table_name)
            if not privileges:
                continue
            op.execute(
                sa.text(
                    f"GRANT {', '.join(sorted(privileges))} ON TABLE public."
                    f"{_quote_identifier(table_name)} TO {quoted_role}"
                )
            )

_SQLITE_REBUILD_TRIGGERS = (
    "trg_ledger_entries_no_delete",
    "trg_ledger_entries_no_update",
    "trg_legacy_ledger_block_v2_insert",
    "trg_company_point_ledger_no_delete",
    "trg_company_point_ledger_no_update",
    "trg_company_point_ledger_task_scope_insert",
    "trg_task_timeout_point_ledger_scope_insert",
    "trg_company_point_lot_identity_immutable",
    "trg_company_point_lot_settled_monotonic",
    "trg_company_point_lot_no_delete",
    "trg_task_point_allocation_scope_insert",
    "trg_personal_ledger_entries_no_delete",
    "trg_personal_ledger_entries_no_update",
)


def _drop_sqlite_guards() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    for trigger_name in _SQLITE_REBUILD_TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    for table_name in _IMMUTABLE_FACT_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_no_update")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_no_delete")
    for trigger_name in (
        "trg_personal_point_lot_identity_immutable",
        "trg_personal_point_lot_no_delete",
        "trg_personal_task_point_allocation_scope_insert",
        "trg_personal_task_point_allocation_identity",
        "trg_personal_task_point_allocation_no_delete",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")


def _create_sqlite_guards() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    for table_name in _IMMUTABLE_FACT_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_no_update BEFORE UPDATE ON {table_name} "
            "BEGIN SELECT RAISE(ABORT, 'commercial billing fact is immutable'); END"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_no_delete BEFORE DELETE ON {table_name} "
            "BEGIN SELECT RAISE(ABORT, 'commercial billing fact is immutable'); END"
        )
    for action in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER trg_ledger_entries_no_{action.lower()} BEFORE {action} "
            "ON ledger_entries BEGIN SELECT RAISE(ABORT, "
            "'ledger entries are immutable'); END"
        )
    op.execute(
        "CREATE TRIGGER trg_legacy_ledger_block_v2_insert BEFORE INSERT ON "
        "ledger_entries WHEN EXISTS (SELECT 1 FROM companies c "
        "WHERE c.id=NEW.company_id AND c.billing_version=2) "
        "BEGIN SELECT RAISE(ABORT, 'legacy cents ledger is read-only after migration'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_company_point_ledger_no_update BEFORE UPDATE ON "
        "company_point_ledger_entries BEGIN SELECT RAISE(ABORT, "
        "'company point ledger is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_company_point_ledger_no_delete BEFORE DELETE ON "
        "company_point_ledger_entries BEGIN SELECT RAISE(ABORT, "
        "'company point ledger is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_company_point_ledger_task_scope_insert BEFORE INSERT ON "
        "company_point_ledger_entries WHEN "
        "(NEW.task_id IS NULL AND NEW.kind NOT IN ('MIGRATION','CREDIT',"
        "'REFUND_RESERVE','REFUND_SETTLE','REFUND_RELEASE','CHARGEBACK',"
        "'DISPUTE_REVERSAL','DEBT_RECOVERY')) OR "
        "(NEW.task_id IS NOT NULL AND NEW.kind NOT IN ('RESERVE','SETTLE','RELEASE')) OR "
        "(NEW.task_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM generation_tasks t "
        "WHERE t.id=NEW.task_id AND t.company_id=NEW.company_id "
        "AND t.personal_workspace_id IS NULL AND t.billing_unit='POINT' "
        "AND t.billing_version=2)) BEGIN SELECT RAISE(ABORT, "
        "'company point ledger task scope mismatch'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_task_timeout_point_ledger_scope_insert BEFORE INSERT ON "
        "task_timeout_events WHEN NEW.company_point_ledger_entry_id IS NOT NULL AND "
        "NOT EXISTS (SELECT 1 FROM company_point_ledger_entries e JOIN generation_tasks t "
        "ON t.id=NEW.task_id WHERE e.id=NEW.company_point_ledger_entry_id "
        "AND e.company_id=NEW.company_id AND e.task_id=NEW.task_id AND "
        "((NEW.released_points>0 AND e.kind='RELEASE' AND e.amount_points=NEW.released_points) "
        "OR (NEW.released_points=0 AND e.kind='SETTLE')) AND t.company_id=NEW.company_id "
        "AND t.billing_unit='POINT' AND t.billing_version=2) "
        "BEGIN SELECT RAISE(ABORT, 'task timeout point ledger scope mismatch'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_company_point_lot_identity_immutable BEFORE UPDATE OF "
        "company_id,source_kind,original_points,cash_basis_cents,receivable_basis_cents,"
        "subsidy_cents,idempotency_key,expires_at,payment_order_id,contract_version_id,"
        "billing_cycle_id ON company_point_lots BEGIN SELECT RAISE(ABORT, "
        "'company point lot identity is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_company_point_lot_settled_monotonic BEFORE UPDATE ON "
        "company_point_lots WHEN NEW.settled_points < OLD.settled_points OR "
        "NEW.reversed_points < OLD.reversed_points BEGIN SELECT RAISE(ABORT, "
        "'company point lot totals cannot decrease'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_company_point_lot_no_delete BEFORE DELETE ON "
        "company_point_lots BEGIN SELECT RAISE(ABORT, 'company point lots are durable'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_task_point_allocation_scope_insert BEFORE INSERT ON "
        "task_point_lot_allocations WHEN NOT EXISTS (SELECT 1 FROM generation_tasks t "
        "JOIN company_point_lots l ON l.id=NEW.lot_id WHERE t.id=NEW.task_id "
        "AND t.company_id=NEW.company_id AND t.personal_workspace_id IS NULL "
        "AND t.billing_unit='POINT' AND t.billing_version=2 "
        "AND l.company_id=NEW.company_id) BEGIN SELECT RAISE(ABORT, "
        "'point allocation scope mismatch'); END"
    )
    for action in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER trg_personal_ledger_entries_no_{action.lower()} BEFORE {action} "
            "ON personal_ledger_entries BEGIN SELECT RAISE(ABORT, "
            "'personal ledger entries are immutable'); END"
        )
    op.execute(
        "CREATE TRIGGER trg_personal_point_lot_identity_immutable BEFORE UPDATE OF "
        "workspace_id,source_kind,original_points,cash_basis_cents,receivable_basis_cents,"
        "subsidy_cents,refundable,idempotency_key,expires_at,payment_order_id "
        "ON personal_point_lots BEGIN SELECT RAISE(ABORT, "
        "'personal point lot identity is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_personal_point_lot_no_delete BEFORE DELETE ON personal_point_lots "
        "BEGIN SELECT RAISE(ABORT, 'personal point lots are durable'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_personal_task_point_allocation_scope_insert BEFORE INSERT ON "
        "personal_task_point_lot_allocations WHEN NOT EXISTS (SELECT 1 FROM generation_tasks t "
        "JOIN personal_point_lots l ON l.id=NEW.lot_id WHERE t.id=NEW.task_id "
        "AND t.personal_workspace_id=NEW.workspace_id AND t.company_id IS NULL "
        "AND t.billing_unit='POINT' AND t.billing_version=2 "
        "AND l.workspace_id=NEW.workspace_id) BEGIN SELECT RAISE(ABORT, "
        "'personal point allocation scope mismatch'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_personal_task_point_allocation_identity BEFORE UPDATE ON "
        "personal_task_point_lot_allocations WHEN NEW.workspace_id<>OLD.workspace_id "
        "OR NEW.task_id<>OLD.task_id OR NEW.lot_id<>OLD.lot_id "
        "OR NEW.allocated_points<>OLD.allocated_points OR NEW.settled_points<OLD.settled_points "
        "OR NEW.released_points<OLD.released_points BEGIN SELECT RAISE(ABORT, "
        "'personal point allocation identity is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_personal_task_point_allocation_no_delete BEFORE DELETE ON "
        "personal_task_point_lot_allocations BEGIN SELECT RAISE(ABORT, "
        "'personal point allocations are durable'); END"
    )


def _backfill_personal_lot_projection() -> None:
    connection = op.get_bind()
    if not op.get_context().as_sql:
        mismatch = connection.execute(
            sa.text(
                "SELECT w.workspace_id FROM personal_wallet_accounts w "
                "WHERE w.reserved_points <> COALESCE((SELECT SUM(t.reserved_points) "
                "FROM generation_tasks t WHERE t.personal_workspace_id=w.workspace_id "
                "AND t.company_id IS NULL AND t.billing_unit='POINT' "
                "AND t.billing_version=2 AND t.reserved_points>0), 0) LIMIT 1"
            )
        ).scalar_one_or_none()
        if mismatch is not None:
            raise RuntimeError(
                "personal wallet reserved points do not reconcile to in-flight tasks"
            )
    op.execute(
        sa.text(
            "INSERT INTO personal_point_lots (id,workspace_id,source_kind,"
            "original_points,available_points,reserved_points,reversal_reserved_points,"
            "settled_points,reversed_points,cash_basis_cents,receivable_basis_cents,"
            "subsidy_cents,refundable,idempotency_key,expires_at,payment_order_id,"
            "created_at,updated_at) SELECT w.workspace_id,w.workspace_id,'LEGACY',"
            "w.available_points+w.reserved_points,w.available_points,w.reserved_points,"
            "0,0,0,0,0,(w.available_points+w.reserved_points)*10,false,"
            "'commercial-v1:legacy-wallet',NULL,NULL,w.created_at,w.updated_at "
            "FROM personal_wallet_accounts w "
            "WHERE w.available_points+w.reserved_points>0"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO personal_task_point_lot_allocations (id,workspace_id,task_id,"
            "lot_id,allocated_points,reserved_points,settled_points,released_points,"
            "created_at) SELECT t.id,t.personal_workspace_id,t.id,t.personal_workspace_id,"
            "t.reserved_points,t.reserved_points,0,0,t.created_at FROM generation_tasks t "
            "WHERE t.personal_workspace_id IS NOT NULL AND t.company_id IS NULL "
            "AND t.billing_unit='POINT' AND t.billing_version=2 AND t.reserved_points>0"
        )
    )


def _create_sqlite_legacy_guards() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    for action in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER trg_ledger_entries_no_{action.lower()} BEFORE {action} "
            "ON ledger_entries BEGIN SELECT RAISE(ABORT, "
            "'ledger entries are immutable'); END"
        )
    op.execute(
        "CREATE TRIGGER trg_legacy_ledger_block_v2_insert BEFORE INSERT ON "
        "ledger_entries WHEN EXISTS (SELECT 1 FROM companies c "
        "WHERE c.id=NEW.company_id AND c.billing_version=2) "
        "BEGIN SELECT RAISE(ABORT, 'legacy cents ledger is read-only after migration'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_company_point_ledger_no_update BEFORE UPDATE ON "
        "company_point_ledger_entries BEGIN SELECT RAISE(ABORT, "
        "'company point ledger is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_company_point_ledger_no_delete BEFORE DELETE ON "
        "company_point_ledger_entries BEGIN SELECT RAISE(ABORT, "
        "'company point ledger is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_company_point_ledger_task_scope_insert BEFORE INSERT ON "
        "company_point_ledger_entries WHEN (NEW.task_id IS NULL AND NEW.kind NOT IN "
        "('MIGRATION','CREDIT')) OR (NEW.task_id IS NOT NULL AND NEW.kind NOT IN "
        "('RESERVE','SETTLE','RELEASE')) OR (NEW.task_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM generation_tasks t WHERE t.id=NEW.task_id "
        "AND t.company_id=NEW.company_id AND t.personal_workspace_id IS NULL "
        "AND t.billing_unit='POINT' AND t.billing_version=2)) "
        "BEGIN SELECT RAISE(ABORT, 'company point ledger task scope mismatch'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_task_timeout_point_ledger_scope_insert BEFORE INSERT ON "
        "task_timeout_events WHEN NEW.company_point_ledger_entry_id IS NOT NULL AND "
        "NOT EXISTS (SELECT 1 FROM company_point_ledger_entries e JOIN generation_tasks t "
        "ON t.id=NEW.task_id WHERE e.id=NEW.company_point_ledger_entry_id "
        "AND e.company_id=NEW.company_id AND e.task_id=NEW.task_id AND "
        "((NEW.released_points>0 AND e.kind='RELEASE' AND e.amount_points=NEW.released_points) "
        "OR (NEW.released_points=0 AND e.kind='SETTLE')) AND t.company_id=NEW.company_id "
        "AND t.billing_unit='POINT' AND t.billing_version=2) "
        "BEGIN SELECT RAISE(ABORT, 'task timeout point ledger scope mismatch'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_company_point_lot_identity_immutable BEFORE UPDATE OF "
        "company_id,source_kind,original_points,cash_basis_cents,subsidy_cents,"
        "idempotency_key,expires_at ON company_point_lots BEGIN SELECT RAISE(ABORT, "
        "'company point lot identity is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_company_point_lot_settled_monotonic BEFORE UPDATE ON "
        "company_point_lots WHEN NEW.settled_points<OLD.settled_points "
        "BEGIN SELECT RAISE(ABORT, 'company point settled total cannot decrease'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_company_point_lot_no_delete BEFORE DELETE ON "
        "company_point_lots BEGIN SELECT RAISE(ABORT, 'company point lots are durable'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_task_point_allocation_scope_insert BEFORE INSERT ON "
        "task_point_lot_allocations WHEN NOT EXISTS (SELECT 1 FROM generation_tasks t "
        "JOIN company_point_lots l ON l.id=NEW.lot_id WHERE t.id=NEW.task_id "
        "AND t.company_id=NEW.company_id AND t.personal_workspace_id IS NULL "
        "AND t.billing_unit='POINT' AND t.billing_version=2 "
        "AND l.company_id=NEW.company_id) BEGIN SELECT RAISE(ABORT, "
        "'point allocation scope mismatch'); END"
    )
    for action in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER trg_personal_ledger_entries_no_{action.lower()} BEFORE {action} "
            "ON personal_ledger_entries BEGIN SELECT RAISE(ABORT, "
            "'personal ledger entries are immutable'); END"
        )


def _create_postgres_guards() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_company_point_ledger_task_scope()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (NEW.task_id IS NULL AND NEW.kind NOT IN (
                'MIGRATION','CREDIT','REFUND_RESERVE','REFUND_SETTLE',
                'REFUND_RELEASE','CHARGEBACK','DISPUTE_REVERSAL','DEBT_RECOVERY'))
             OR (NEW.task_id IS NOT NULL
                 AND NEW.kind NOT IN ('RESERVE','SETTLE','RELEASE')) THEN
            RAISE EXCEPTION 'company point ledger task shape mismatch';
          END IF;
          IF NEW.task_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM generation_tasks t WHERE t.id=NEW.task_id
              AND t.company_id=NEW.company_id AND t.personal_workspace_id IS NULL
              AND t.billing_unit='POINT' AND t.billing_version=2
          ) THEN RAISE EXCEPTION 'company point ledger task scope mismatch';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_company_point_lot_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'company point lots are durable'; END IF;
          IF NEW.company_id IS DISTINCT FROM OLD.company_id
             OR NEW.source_kind IS DISTINCT FROM OLD.source_kind
             OR NEW.original_points IS DISTINCT FROM OLD.original_points
             OR NEW.cash_basis_cents IS DISTINCT FROM OLD.cash_basis_cents
             OR NEW.receivable_basis_cents IS DISTINCT FROM OLD.receivable_basis_cents
             OR NEW.subsidy_cents IS DISTINCT FROM OLD.subsidy_cents
             OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
             OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
             OR NEW.payment_order_id IS DISTINCT FROM OLD.payment_order_id
             OR NEW.contract_version_id IS DISTINCT FROM OLD.contract_version_id
             OR NEW.billing_cycle_id IS DISTINCT FROM OLD.billing_cycle_id
             OR NEW.settled_points < OLD.settled_points
             OR NEW.reversed_points < OLD.reversed_points THEN
            RAISE EXCEPTION 'company point lot immutable evidence changed';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_commercial_fact_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'commercial billing fact is immutable'; END $$
        """
    )
    for table_name in _IMMUTABLE_FACT_TABLES:
        trigger_name = f"trg_{table_name}_immutable"
        op.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION reject_commercial_fact_mutation()"
        )
    op.execute(
        """
        CREATE FUNCTION guard_personal_point_lot_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'personal point lots are durable'; END IF;
          IF NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
             OR NEW.source_kind IS DISTINCT FROM OLD.source_kind
             OR NEW.original_points IS DISTINCT FROM OLD.original_points
             OR NEW.cash_basis_cents IS DISTINCT FROM OLD.cash_basis_cents
             OR NEW.receivable_basis_cents IS DISTINCT FROM OLD.receivable_basis_cents
             OR NEW.subsidy_cents IS DISTINCT FROM OLD.subsidy_cents
             OR NEW.refundable IS DISTINCT FROM OLD.refundable
             OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
             OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
             OR NEW.payment_order_id IS DISTINCT FROM OLD.payment_order_id
             OR NEW.settled_points < OLD.settled_points
             OR NEW.reversed_points < OLD.reversed_points THEN
            RAISE EXCEPTION 'personal point lot immutable evidence changed';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_personal_point_lot_guard BEFORE UPDATE OR DELETE ON "
        "personal_point_lots FOR EACH ROW EXECUTE FUNCTION guard_personal_point_lot_mutation()"
    )
    op.execute(
        """
        CREATE FUNCTION guard_personal_task_point_allocation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'personal point allocations are durable'; END IF;
          IF TG_OP='UPDATE' AND (
             NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
             OR NEW.task_id IS DISTINCT FROM OLD.task_id
             OR NEW.lot_id IS DISTINCT FROM OLD.lot_id
             OR NEW.allocated_points IS DISTINCT FROM OLD.allocated_points
             OR NEW.settled_points < OLD.settled_points
             OR NEW.released_points < OLD.released_points) THEN
            RAISE EXCEPTION 'personal point allocation identity is immutable';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM generation_tasks t JOIN personal_point_lots l ON l.id=NEW.lot_id
            WHERE t.id=NEW.task_id AND t.personal_workspace_id=NEW.workspace_id
              AND t.company_id IS NULL AND t.billing_unit='POINT' AND t.billing_version=2
              AND l.workspace_id=NEW.workspace_id
          ) THEN RAISE EXCEPTION 'personal point allocation scope mismatch'; END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_personal_task_point_allocation_guard BEFORE INSERT OR UPDATE OR "
        "DELETE ON personal_task_point_lot_allocations FOR EACH ROW EXECUTE FUNCTION "
        "guard_personal_task_point_allocation()"
    )
    for function_name in (
        "reject_commercial_fact_mutation",
        "guard_personal_point_lot_mutation",
        "guard_personal_task_point_allocation",
    ):
        op.execute(
            f"REVOKE ALL PRIVILEGES ON FUNCTION {function_name}() FROM PUBLIC"
        )


def _drop_postgres_guards() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in _IMMUTABLE_FACT_TABLES:
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}"
        )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_personal_point_lot_guard ON personal_point_lots"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_personal_task_point_allocation_guard "
        "ON personal_task_point_lot_allocations"
    )
    for function_name in (
        "reject_commercial_fact_mutation",
        "guard_personal_point_lot_mutation",
        "guard_personal_task_point_allocation",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {function_name}()")


def _restore_postgres_legacy_guards() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_company_point_ledger_task_scope()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (NEW.task_id IS NULL AND NEW.kind NOT IN ('MIGRATION','CREDIT'))
             OR (NEW.task_id IS NOT NULL
                 AND NEW.kind NOT IN ('RESERVE','SETTLE','RELEASE')) THEN
            RAISE EXCEPTION 'company point ledger task shape mismatch';
          END IF;
          IF NEW.task_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM generation_tasks t WHERE t.id=NEW.task_id
              AND t.company_id=NEW.company_id AND t.personal_workspace_id IS NULL
              AND t.billing_unit='POINT' AND t.billing_version=2
          ) THEN RAISE EXCEPTION 'company point ledger task scope mismatch'; END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_company_point_lot_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'company point lots are durable'; END IF;
          IF NEW.company_id IS DISTINCT FROM OLD.company_id
             OR NEW.source_kind IS DISTINCT FROM OLD.source_kind
             OR NEW.original_points IS DISTINCT FROM OLD.original_points
             OR NEW.cash_basis_cents IS DISTINCT FROM OLD.cash_basis_cents
             OR NEW.subsidy_cents IS DISTINCT FROM OLD.subsidy_cents
             OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
             OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
             OR NEW.settled_points < OLD.settled_points THEN
            RAISE EXCEPTION 'company point lot immutable evidence changed';
          END IF;
          RETURN NEW;
        END $$
        """
    )
def upgrade() -> None:
    _drop_sqlite_guards()
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('finance_reconciliation_runs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('run_kind', sa.String(length=24), nullable=False),
    sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=True),
    sa.Column('merchant_account', sa.String(length=120), nullable=True),
    sa.Column('status', sa.Enum('RUNNING', 'BALANCED', 'BALANCED_WITH_EXCEPTIONS', 'FAILED', 'STALE', name='reconciliationrunstatus', native_enum=False), nullable=False),
    sa.Column('source_watermarks', sa.JSON(), nullable=False),
    sa.Column('control_totals', sa.JSON(), nullable=False),
    sa.Column('snapshot_sha256', sa.String(length=64), nullable=True),
    sa.Column('idempotency_key', sa.String(length=160), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("snapshot_sha256 IS NULL OR (length(snapshot_sha256) = 64 AND lower(snapshot_sha256) = snapshot_sha256 AND snapshot_sha256 NOT GLOB '*[^0-9a-f]*')", name='ck_finance_reconciliation_snapshot_sha256').ddl_if(dialect="sqlite"),
    sa.CheckConstraint("snapshot_sha256 IS NULL OR (snapshot_sha256 ~ '^[0-9a-f]{64}$')", name='ck_finance_reconciliation_snapshot_sha256').ddl_if(dialect="postgresql"),
    sa.CheckConstraint('period_end > period_start', name='ck_finance_reconciliation_period'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key', name='uq_finance_reconciliation_key')
    )
    op.create_table('payment_settlement_entries',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('merchant_account', sa.String(length=120), nullable=False),
    sa.Column('provider_line_id', sa.String(length=160), nullable=False),
    sa.Column('provider_transaction_id', sa.String(length=160), nullable=True),
    sa.Column('line_type', sa.String(length=40), nullable=False),
    sa.Column('gross_amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('fee_amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('net_amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('source_document_sha256', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("currency = 'CNY'", name='ck_payment_settlement_currency'),
    sa.CheckConstraint("length(source_document_sha256) = 64 AND lower(source_document_sha256) = source_document_sha256 AND source_document_sha256 NOT GLOB '*[^0-9a-f]*'", name='ck_payment_settlement_document_sha256').ddl_if(dialect="sqlite"),
    sa.CheckConstraint("source_document_sha256 ~ '^[0-9a-f]{64}$'", name='ck_payment_settlement_document_sha256').ddl_if(dialect="postgresql"),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'merchant_account', 'provider_line_id', name='uq_payment_settlement_provider_line')
    )
    op.create_table('company_billing_contract_versions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('company_id', sa.String(length=36), nullable=False),
    sa.Column('status', sa.Enum('ACTIVE', 'SUPERSEDED', 'TERMINATED', name='enterprisecontractstatus', native_enum=False), nullable=False),
    sa.Column('contract_reference', sa.String(length=160), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('timezone_name', sa.String(length=80), nullable=False),
    sa.Column('cycle_day', sa.Integer(), nullable=False),
    sa.Column('payment_terms_days', sa.Integer(), nullable=False),
    sa.Column('credit_limit_points', sa.BigInteger(), nullable=False),
    sa.Column('receivable_per_point_cents', sa.Integer(), nullable=False),
    sa.Column('content_sha256', sa.String(length=64), nullable=False),
    sa.Column('supersedes_version_id', sa.String(length=36), nullable=True),
    sa.Column('effective_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by_user_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name='ck_company_billing_contract_sha256').ddl_if(dialect="postgresql"),
    sa.CheckConstraint("currency = 'CNY'", name='ck_company_billing_contract_currency'),
    sa.CheckConstraint("length(content_sha256) = 64 AND lower(content_sha256) = content_sha256 AND content_sha256 NOT GLOB '*[^0-9a-f]*'", name='ck_company_billing_contract_sha256').ddl_if(dialect="sqlite"),
    sa.CheckConstraint('credit_limit_points > 0', name='ck_company_billing_credit_limit'),
    sa.CheckConstraint('cycle_day BETWEEN 1 AND 28', name='ck_company_billing_cycle_day'),
    sa.CheckConstraint('payment_terms_days BETWEEN 0 AND 180', name='ck_company_billing_terms'),
    sa.CheckConstraint('receivable_per_point_cents = 10', name='ck_company_billing_point_anchor'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['supersedes_version_id'], ['company_billing_contract_versions.id'], ondelete='RESTRICT', use_alter=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('content_sha256', name='uq_company_billing_contract_content'),
    sa.UniqueConstraint('supersedes_version_id')
    )
    with op.batch_alter_table('company_billing_contract_versions', schema=None) as batch_op:
        batch_op.create_index('ix_company_billing_contract_company_effective', ['company_id', 'effective_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_company_billing_contract_versions_company_id'), ['company_id'], unique=False)

    op.create_table('finance_reconciliation_exceptions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('dimension', sa.String(length=24), nullable=False),
    sa.Column('code', sa.String(length=120), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('entity_type', sa.String(length=40), nullable=False),
    sa.Column('entity_id', sa.String(length=160), nullable=True),
    sa.Column('expected_amount', sa.BigInteger(), nullable=True),
    sa.Column('actual_amount', sa.BigInteger(), nullable=True),
    sa.Column('evidence_sha256', sa.String(length=64), nullable=False),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("evidence_sha256 ~ '^[0-9a-f]{64}$'", name='ck_finance_exception_evidence_sha256').ddl_if(dialect="postgresql"),
    sa.CheckConstraint("length(evidence_sha256) = 64 AND lower(evidence_sha256) = evidence_sha256 AND evidence_sha256 NOT GLOB '*[^0-9a-f]*'", name='ck_finance_exception_evidence_sha256').ddl_if(dialect="sqlite"),
    sa.ForeignKeyConstraint(['run_id'], ['finance_reconciliation_runs.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('finance_reconciliation_exceptions', schema=None) as batch_op:
        batch_op.create_index('ix_finance_reconciliation_exception_run', ['run_id', 'dimension', 'code'], unique=False)
        batch_op.create_index(batch_op.f('ix_finance_reconciliation_exceptions_run_id'), ['run_id'], unique=False)

    op.create_table('finance_reconciliation_snapshots',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('dimension', sa.String(length=24), nullable=False),
    sa.Column('status', sa.Enum('MATCHED', 'MISSING', 'MISMATCH', 'DUPLICATE', 'UNATTRIBUTED', 'PENDING', 'NOT_APPLICABLE', 'SOURCE_UNAVAILABLE', name='reconciliationdimensionstatus', native_enum=False), nullable=False),
    sa.Column('totals', sa.JSON(), nullable=False),
    sa.Column('evidence_sha256', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("evidence_sha256 ~ '^[0-9a-f]{64}$'", name='ck_finance_snapshot_evidence_sha256').ddl_if(dialect="postgresql"),
    sa.CheckConstraint("length(evidence_sha256) = 64 AND lower(evidence_sha256) = evidence_sha256 AND evidence_sha256 NOT GLOB '*[^0-9a-f]*'", name='ck_finance_snapshot_evidence_sha256').ddl_if(dialect="sqlite"),
    sa.ForeignKeyConstraint(['run_id'], ['finance_reconciliation_runs.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'dimension', name='uq_finance_reconciliation_dimension')
    )
    with op.batch_alter_table('finance_reconciliation_snapshots', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_finance_reconciliation_snapshots_run_id'), ['run_id'], unique=False)

    op.create_table('company_billing_accounts',
    sa.Column('company_id', sa.String(length=36), nullable=False),
    sa.Column('active_contract_version_id', sa.String(length=36), nullable=False),
    sa.Column('unbilled_receivable_cents', sa.BigInteger(), nullable=False),
    sa.Column('billing_hold', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('unbilled_receivable_cents >= 0', name='ck_company_unbilled_receivable'),
    sa.ForeignKeyConstraint(['active_contract_version_id'], ['company_billing_contract_versions.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('company_id'),
    sa.UniqueConstraint('active_contract_version_id')
    )
    op.create_table('company_billing_cycles',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('company_id', sa.String(length=36), nullable=False),
    sa.Column('contract_version_id', sa.String(length=36), nullable=False),
    sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.Enum('OPEN', 'FROZEN', 'ISSUED', 'PAID', 'OVERDUE', 'DISPUTED', 'CLOSED', name='enterprisebillingcyclestatus', native_enum=False), nullable=False),
    sa.Column('frozen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('period_end > period_start', name='ck_company_billing_period_order'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['contract_version_id'], ['company_billing_contract_versions.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('company_id', 'period_start', 'period_end', name='uq_company_billing_period')
    )
    with op.batch_alter_table('company_billing_cycles', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_company_billing_cycles_company_id'), ['company_id'], unique=False)

    op.create_table('finance_reconciliation_resolutions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('exception_id', sa.String(length=36), nullable=False),
    sa.Column('action', sa.String(length=24), nullable=False),
    sa.Column('note', sa.String(length=240), nullable=False),
    sa.Column('evidence_sha256', sa.String(length=64), nullable=False),
    sa.Column('actor_user_id', sa.String(length=36), nullable=False),
    sa.Column('idempotency_key', sa.String(length=120), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("evidence_sha256 ~ '^[0-9a-f]{64}$'", name='ck_finance_resolution_evidence_sha256').ddl_if(dialect="postgresql"),
    sa.CheckConstraint("length(evidence_sha256) = 64 AND lower(evidence_sha256) = evidence_sha256 AND evidence_sha256 NOT GLOB '*[^0-9a-f]*'", name='ck_finance_resolution_evidence_sha256').ddl_if(dialect="sqlite"),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['exception_id'], ['finance_reconciliation_exceptions.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('exception_id', 'idempotency_key', name='uq_finance_resolution_key')
    )
    with op.batch_alter_table('finance_reconciliation_resolutions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_finance_reconciliation_resolutions_exception_id'), ['exception_id'], unique=False)

    op.create_table('auto_recharge_rules',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('company_id', sa.String(length=36), nullable=True),
    sa.Column('personal_workspace_id', sa.String(length=36), nullable=True),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('merchant_account', sa.String(length=120), nullable=False),
    sa.Column('provider_customer_reference', sa.String(length=240), nullable=False),
    sa.Column('created_by_user_id', sa.String(length=36), nullable=False),
    sa.Column('threshold_points', sa.BigInteger(), nullable=False),
    sa.Column('top_up_points', sa.BigInteger(), nullable=False),
    sa.Column('monthly_cap_cents', sa.BigInteger(), nullable=False),
    sa.Column('cooldown_seconds', sa.Integer(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR (company_id IS NULL AND personal_workspace_id IS NOT NULL)', name='ck_auto_recharge_scope'),
    sa.CheckConstraint('cooldown_seconds >= 60', name='ck_auto_recharge_cooldown'),
    sa.CheckConstraint('monthly_cap_cents > 0', name='ck_auto_recharge_monthly_cap'),
    sa.CheckConstraint('threshold_points >= 0', name='ck_auto_recharge_threshold'),
    sa.CheckConstraint('top_up_points > 0', name='ck_auto_recharge_top_up'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['personal_workspace_id'], ['personal_workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('company_id', name='uq_auto_recharge_company'),
    sa.UniqueConstraint('personal_workspace_id', name='uq_auto_recharge_personal')
    )
    with op.batch_alter_table('auto_recharge_rules', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_auto_recharge_rules_company_id'), ['company_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_auto_recharge_rules_personal_workspace_id'), ['personal_workspace_id'], unique=False)

    op.create_table('company_invoices',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('company_id', sa.String(length=36), nullable=False),
    sa.Column('cycle_id', sa.String(length=36), nullable=False),
    sa.Column('invoice_number', sa.String(length=80), nullable=False),
    sa.Column('status', sa.Enum('DRAFT', 'ISSUED', 'PARTIALLY_PAID', 'PAID', 'OVERDUE', 'DISPUTED', 'VOID', name='enterpriseinvoicestatus', native_enum=False), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('subtotal_cents', sa.BigInteger(), nullable=False),
    sa.Column('credit_cents', sa.BigInteger(), nullable=False),
    sa.Column('tax_cents', sa.BigInteger(), nullable=False),
    sa.Column('total_cents', sa.BigInteger(), nullable=False),
    sa.Column('paid_cents', sa.BigInteger(), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('subtotal_cents >= 0 AND credit_cents >= 0 AND tax_cents >= 0 AND total_cents >= 0 AND paid_cents >= 0', name='ck_company_invoice_totals_nonnegative'),
    sa.CheckConstraint('total_cents = subtotal_cents - credit_cents + tax_cents', name='ck_company_invoice_total'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['cycle_id'], ['company_billing_cycles.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('cycle_id', name='uq_company_invoice_cycle'),
    sa.UniqueConstraint('invoice_number', name='uq_company_invoice_number')
    )
    with op.batch_alter_table('company_invoices', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_company_invoices_company_id'), ['company_id'], unique=False)

    op.create_table('payment_orders',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('company_id', sa.String(length=36), nullable=True),
    sa.Column('personal_workspace_id', sa.String(length=36), nullable=True),
    sa.Column('created_by_user_id', sa.String(length=36), nullable=False),
    sa.Column('purpose', sa.Enum('POINT_PURCHASE', 'INVOICE_PAYMENT', name='paymentpurpose', native_enum=False), nullable=False),
    sa.Column('purpose_reference_id', sa.String(length=36), nullable=True),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('merchant_account', sa.String(length=120), nullable=False),
    sa.Column('provider_order_id', sa.String(length=160), nullable=True),
    sa.Column('status', sa.Enum('CREATED', 'PENDING', 'REQUIRES_ACTION', 'PAID', 'PARTIALLY_REFUNDED', 'REFUNDED', 'DISPUTED', 'FAILED', 'CANCELLED', 'EXPIRED', 'RECONCILIATION_REQUIRED', name='paymentorderstatus', native_enum=False), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('points', sa.BigInteger(), nullable=False),
    sa.Column('captured_amount_cents', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('refunded_amount_cents', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('disputed_amount_cents', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('fee_amount_cents', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('idempotency_key', sa.String(length=120), nullable=False),
    sa.Column('request_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('automatic', sa.Boolean(), nullable=False),
    sa.Column('checkout_url', sa.String(length=2048), nullable=True),
    sa.Column('provider_customer_reference', sa.String(length=240), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('captured_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("(purpose = 'POINT_PURCHASE' AND points > 0 AND purpose_reference_id IS NULL) OR (purpose = 'INVOICE_PAYMENT' AND points = 0 AND purpose_reference_id IS NOT NULL)", name='ck_payment_order_purpose'),
    sa.CheckConstraint("currency = 'CNY'", name='ck_payment_order_currency'),
    sa.CheckConstraint("length(request_fingerprint) = 64 AND lower(request_fingerprint) = request_fingerprint AND request_fingerprint NOT GLOB '*[^0-9a-f]*'", name='ck_payment_order_fingerprint_sha256').ddl_if(dialect="sqlite"),
    sa.CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name='ck_payment_order_fingerprint_sha256').ddl_if(dialect="postgresql"),
    sa.CheckConstraint('(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR (company_id IS NULL AND personal_workspace_id IS NOT NULL)', name='ck_payment_order_scope'),
    sa.CheckConstraint('amount_cents > 0', name='ck_payment_order_amount'),
    sa.CheckConstraint('captured_amount_cents >= 0 AND refunded_amount_cents >= 0 AND disputed_amount_cents >= 0 AND fee_amount_cents >= 0', name='ck_payment_order_amount_totals'),
    sa.CheckConstraint('points >= 0', name='ck_payment_order_points'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['personal_workspace_id'], ['personal_workspaces.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('company_id', 'idempotency_key', name='uq_payment_order_company_idempotency'),
    sa.UniqueConstraint('personal_workspace_id', 'idempotency_key', name='uq_payment_order_personal_idempotency'),
    sa.UniqueConstraint('provider', 'merchant_account', 'provider_order_id', name='uq_payment_order_provider_order')
    )
    with op.batch_alter_table('payment_orders', schema=None) as batch_op:
        batch_op.create_index('ix_payment_order_status_created', ['status', 'created_at', 'id'], unique=False)
        batch_op.create_index(batch_op.f('ix_payment_orders_company_id'), ['company_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_payment_orders_personal_workspace_id'), ['personal_workspace_id'], unique=False)

    op.create_table('personal_point_lots',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('source_kind', sa.Enum('PURCHASED', 'CONTRACT', 'PROMOTIONAL', 'COMPENSATION', 'LEGACY', 'MIGRATION_REMAINDER', 'INTERNAL_TEST', name='pointlotsourcekind', native_enum=False), nullable=False),
    sa.Column('original_points', sa.BigInteger(), nullable=False),
    sa.Column('available_points', sa.BigInteger(), nullable=False),
    sa.Column('reserved_points', sa.BigInteger(), nullable=False),
    sa.Column('reversal_reserved_points', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('settled_points', sa.BigInteger(), nullable=False),
    sa.Column('reversed_points', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('cash_basis_cents', sa.BigInteger(), nullable=False),
    sa.Column('receivable_basis_cents', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('subsidy_cents', sa.BigInteger(), nullable=False),
    sa.Column('refundable', sa.Boolean(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=120), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('payment_order_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('available_points >= 0', name='ck_personal_point_lot_available'),
    sa.CheckConstraint('cash_basis_cents + receivable_basis_cents + subsidy_cents = original_points * 10', name='ck_personal_point_lot_value_basis'),
    sa.CheckConstraint('cash_basis_cents >= 0', name='ck_personal_point_lot_cash_basis'),
    sa.CheckConstraint('original_points = available_points + reserved_points + reversal_reserved_points + settled_points + reversed_points', name='ck_personal_point_lot_conservation'),
    sa.CheckConstraint('original_points > 0', name='ck_personal_point_lot_original'),
    sa.CheckConstraint('receivable_basis_cents >= 0', name='ck_personal_point_lot_receivable_basis'),
    sa.CheckConstraint('reserved_points >= 0', name='ck_personal_point_lot_reserved'),
    sa.CheckConstraint('reversal_reserved_points >= 0', name='ck_personal_point_lot_reversal_reserved'),
    sa.CheckConstraint('reversed_points >= 0', name='ck_personal_point_lot_reversed'),
    sa.CheckConstraint('settled_points >= 0', name='ck_personal_point_lot_settled'),
    sa.CheckConstraint('subsidy_cents >= 0', name='ck_personal_point_lot_subsidy'),
    sa.ForeignKeyConstraint(['payment_order_id'], ['payment_orders.id'], ondelete='RESTRICT', use_alter=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['personal_workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('payment_order_id'),
    sa.UniqueConstraint('workspace_id', 'idempotency_key', name='uq_personal_point_lot_idempotency')
    )
    with op.batch_alter_table('personal_point_lots', schema=None) as batch_op:
        batch_op.create_index('ix_personal_point_lot_spend_order', ['workspace_id', 'expires_at', 'created_at', 'id'], unique=False)
        batch_op.create_index(batch_op.f('ix_personal_point_lots_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('auto_recharge_executions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('rule_id', sa.String(length=36), nullable=False),
    sa.Column('payment_order_id', sa.String(length=36), nullable=False),
    sa.Column('trigger_key', sa.String(length=160), nullable=False),
    sa.Column('observed_available_points', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('observed_available_points >= 0', name='ck_auto_recharge_observed'),
    sa.ForeignKeyConstraint(['payment_order_id'], ['payment_orders.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['rule_id'], ['auto_recharge_rules.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('payment_order_id'),
    sa.UniqueConstraint('rule_id', 'trigger_key', name='uq_auto_recharge_trigger')
    )
    with op.batch_alter_table('auto_recharge_executions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_auto_recharge_executions_rule_id'), ['rule_id'], unique=False)

    op.create_table('payment_attempts',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('order_id', sa.String(length=36), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=160), nullable=False),
    sa.Column('provider_attempt_id', sa.String(length=160), nullable=True),
    sa.Column('status', sa.Enum('PENDING', 'REQUIRES_ACTION', 'SUCCEEDED', 'FAILED', 'UNKNOWN', name='paymentattemptstatus', native_enum=False), nullable=False),
    sa.Column('request_sha256', sa.String(length=64), nullable=False),
    sa.Column('response_sha256', sa.String(length=64), nullable=True),
    sa.Column('failure_code', sa.String(length=120), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("length(request_sha256) = 64 AND lower(request_sha256) = request_sha256 AND request_sha256 NOT GLOB '*[^0-9a-f]*'", name='ck_payment_attempt_request_sha256').ddl_if(dialect="sqlite"),
    sa.CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'", name='ck_payment_attempt_request_sha256').ddl_if(dialect="postgresql"),
    sa.CheckConstraint("response_sha256 IS NULL OR (length(response_sha256) = 64 AND lower(response_sha256) = response_sha256 AND response_sha256 NOT GLOB '*[^0-9a-f]*')", name='ck_payment_attempt_response_sha256').ddl_if(dialect="sqlite"),
    sa.CheckConstraint("response_sha256 IS NULL OR (response_sha256 ~ '^[0-9a-f]{64}$')", name='ck_payment_attempt_response_sha256').ddl_if(dialect="postgresql"),
    sa.CheckConstraint('sequence > 0', name='ck_payment_attempt_sequence_positive'),
    sa.ForeignKeyConstraint(['order_id'], ['payment_orders.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key', name='uq_payment_attempt_idempotency'),
    sa.UniqueConstraint('order_id', 'sequence', name='uq_payment_attempt_sequence'),
    sa.UniqueConstraint('provider', 'provider_attempt_id', name='uq_payment_attempt_provider_id')
    )
    with op.batch_alter_table('payment_attempts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_payment_attempts_order_id'), ['order_id'], unique=False)

    op.create_table('payment_disputes',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('order_id', sa.String(length=36), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('provider_dispute_id', sa.String(length=160), nullable=False),
    sa.Column('status', sa.Enum('OPEN', 'WON', 'LOST', name='paymentdisputestatus', native_enum=False), nullable=False),
    sa.Column('amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('points', sa.BigInteger(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('reason_code', sa.String(length=120), nullable=False),
    sa.Column('recovered_available_points', sa.BigInteger(), nullable=False),
    sa.Column('debt_points', sa.BigInteger(), nullable=False),
    sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("currency = 'CNY'", name='ck_payment_dispute_currency'),
    sa.CheckConstraint('amount_cents > 0', name='ck_payment_dispute_amount'),
    sa.CheckConstraint('points >= 0', name='ck_payment_dispute_points'),
    sa.ForeignKeyConstraint(['order_id'], ['payment_orders.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'provider_dispute_id', name='uq_payment_dispute_provider_id')
    )
    with op.batch_alter_table('payment_disputes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_payment_disputes_order_id'), ['order_id'], unique=False)

    op.create_table('payment_refunds',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('order_id', sa.String(length=36), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('provider_refund_id', sa.String(length=160), nullable=True),
    sa.Column('status', sa.Enum('REQUESTED', 'PENDING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'RECONCILIATION_REQUIRED', name='paymentrefundstatus', native_enum=False), nullable=False),
    sa.Column('amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('points', sa.BigInteger(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('reason', sa.String(length=240), nullable=False),
    sa.Column('idempotency_key', sa.String(length=120), nullable=False),
    sa.Column('request_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('requested_by_user_id', sa.String(length=36), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("currency = 'CNY'", name='ck_payment_refund_currency'),
    sa.CheckConstraint("length(request_fingerprint) = 64 AND lower(request_fingerprint) = request_fingerprint AND request_fingerprint NOT GLOB '*[^0-9a-f]*'", name='ck_payment_refund_fingerprint_sha256').ddl_if(dialect="sqlite"),
    sa.CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name='ck_payment_refund_fingerprint_sha256').ddl_if(dialect="postgresql"),
    sa.CheckConstraint('amount_cents > 0', name='ck_payment_refund_amount'),
    sa.CheckConstraint('points >= 0', name='ck_payment_refund_points'),
    sa.ForeignKeyConstraint(['order_id'], ['payment_orders.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['requested_by_user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('order_id', 'idempotency_key', name='uq_payment_refund_order_key'),
    sa.UniqueConstraint('provider', 'provider_refund_id', name='uq_payment_refund_provider_id')
    )
    with op.batch_alter_table('payment_refunds', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_payment_refunds_order_id'), ['order_id'], unique=False)

    op.create_table('personal_task_point_lot_allocations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('lot_id', sa.String(length=36), nullable=False),
    sa.Column('allocated_points', sa.BigInteger(), nullable=False),
    sa.Column('reserved_points', sa.BigInteger(), nullable=False),
    sa.Column('settled_points', sa.BigInteger(), nullable=False),
    sa.Column('released_points', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('allocated_points = reserved_points + settled_points + released_points', name='ck_personal_task_point_allocation_conservation'),
    sa.CheckConstraint('allocated_points > 0', name='ck_personal_task_point_allocation_total'),
    sa.CheckConstraint('released_points >= 0', name='ck_personal_task_point_allocation_released'),
    sa.CheckConstraint('reserved_points >= 0', name='ck_personal_task_point_allocation_reserved'),
    sa.CheckConstraint('settled_points >= 0', name='ck_personal_task_point_allocation_settled'),
    sa.ForeignKeyConstraint(['lot_id'], ['personal_point_lots.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['task_id'], ['generation_tasks.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['workspace_id'], ['personal_workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('task_id', 'lot_id', name='uq_personal_task_point_lot_allocation')
    )
    with op.batch_alter_table('personal_task_point_lot_allocations', schema=None) as batch_op:
        batch_op.create_index('ix_personal_task_point_lot_allocation_task', ['task_id', 'id'], unique=False)
        batch_op.create_index(batch_op.f('ix_personal_task_point_lot_allocations_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('payment_webhook_receipts',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('merchant_account', sa.String(length=120), nullable=False),
    sa.Column('provider_event_id', sa.String(length=160), nullable=False),
    sa.Column('event_type', sa.String(length=80), nullable=False),
    sa.Column('payload_sha256', sa.String(length=64), nullable=False),
    sa.Column('signature_key_id', sa.String(length=120), nullable=False),
    sa.Column('signature_timestamp', sa.DateTime(timezone=True), nullable=False),
    sa.Column('provider_occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('outcome', sa.Enum('PROCESSED', 'RECONCILIATION_REQUIRED', name='paymentwebhookoutcome', native_enum=False), nullable=False),
    sa.Column('order_id', sa.String(length=36), nullable=True),
    sa.Column('refund_id', sa.String(length=36), nullable=True),
    sa.Column('dispute_id', sa.String(length=36), nullable=True),
    sa.Column('error_code', sa.String(length=120), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("length(payload_sha256) = 64 AND lower(payload_sha256) = payload_sha256 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'", name='ck_payment_webhook_payload_sha256').ddl_if(dialect="sqlite"),
    sa.CheckConstraint("payload_sha256 ~ '^[0-9a-f]{64}$'", name='ck_payment_webhook_payload_sha256').ddl_if(dialect="postgresql"),
    sa.ForeignKeyConstraint(['dispute_id'], ['payment_disputes.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['order_id'], ['payment_orders.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['refund_id'], ['payment_refunds.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'merchant_account', 'provider_event_id', name='uq_payment_webhook_provider_event')
    )
    with op.batch_alter_table('payment_webhook_receipts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_payment_webhook_receipts_dispute_id'), ['dispute_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_payment_webhook_receipts_order_id'), ['order_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_payment_webhook_receipts_refund_id'), ['refund_id'], unique=False)
        batch_op.create_index('ix_payment_webhook_received', ['received_at', 'id'], unique=False)

    op.create_table('point_lot_settlement_value_allocations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('company_id', sa.String(length=36), nullable=True),
    sa.Column('personal_workspace_id', sa.String(length=36), nullable=True),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('company_task_allocation_id', sa.String(length=36), nullable=True),
    sa.Column('personal_task_allocation_id', sa.String(length=36), nullable=True),
    sa.Column('company_settle_ledger_id', sa.String(length=36), nullable=True),
    sa.Column('personal_settle_ledger_id', sa.String(length=36), nullable=True),
    sa.Column('settled_points', sa.BigInteger(), nullable=False),
    sa.Column('cash_basis_cents', sa.BigInteger(), nullable=False),
    sa.Column('receivable_basis_cents', sa.BigInteger(), nullable=False),
    sa.Column('subsidy_cents', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('(company_id IS NOT NULL AND personal_workspace_id IS NULL AND company_task_allocation_id IS NOT NULL AND personal_task_allocation_id IS NULL AND company_settle_ledger_id IS NOT NULL AND personal_settle_ledger_id IS NULL) OR (company_id IS NULL AND personal_workspace_id IS NOT NULL AND company_task_allocation_id IS NULL AND personal_task_allocation_id IS NOT NULL AND company_settle_ledger_id IS NULL AND personal_settle_ledger_id IS NOT NULL)', name='ck_point_value_allocation_scope'),
    sa.CheckConstraint('cash_basis_cents + receivable_basis_cents + subsidy_cents = settled_points * 10', name='ck_point_value_conservation'),
    sa.CheckConstraint('cash_basis_cents >= 0 AND receivable_basis_cents >= 0 AND subsidy_cents >= 0', name='ck_point_value_nonnegative'),
    sa.CheckConstraint('settled_points > 0', name='ck_point_value_settled_points'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['company_settle_ledger_id'], ['company_point_ledger_entries.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['company_task_allocation_id'], ['task_point_lot_allocations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['personal_settle_ledger_id'], ['personal_ledger_entries.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['personal_task_allocation_id'], ['personal_task_point_lot_allocations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['personal_workspace_id'], ['personal_workspaces.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['task_id'], ['generation_tasks.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('company_task_allocation_id', name='uq_point_value_company_allocation'),
    sa.UniqueConstraint('personal_task_allocation_id', name='uq_point_value_personal_allocation')
    )
    with op.batch_alter_table('point_lot_settlement_value_allocations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_point_lot_settlement_value_allocations_company_id'), ['company_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_point_lot_settlement_value_allocations_personal_workspace_id'), ['personal_workspace_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_point_lot_settlement_value_allocations_task_id'), ['task_id'], unique=False)

    op.create_table('company_invoice_lines',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('invoice_id', sa.String(length=36), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('value_allocation_id', sa.String(length=36), nullable=False),
    sa.Column('contract_version_id', sa.String(length=36), nullable=False),
    sa.Column('points', sa.BigInteger(), nullable=False),
    sa.Column('amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('amount_cents >= 0', name='ck_company_invoice_line_amount'),
    sa.CheckConstraint('points > 0', name='ck_company_invoice_line_points'),
    sa.ForeignKeyConstraint(['contract_version_id'], ['company_billing_contract_versions.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['invoice_id'], ['company_invoices.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['task_id'], ['generation_tasks.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['value_allocation_id'], ['point_lot_settlement_value_allocations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('value_allocation_id', name='uq_company_invoice_value_allocation')
    )
    with op.batch_alter_table('company_invoice_lines', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_company_invoice_lines_invoice_id'), ['invoice_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_company_invoice_lines_task_id'), ['task_id'], unique=False)

    op.create_table('payment_transactions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('order_id', sa.String(length=36), nullable=False),
    sa.Column('refund_id', sa.String(length=36), nullable=True),
    sa.Column('dispute_id', sa.String(length=36), nullable=True),
    sa.Column('webhook_receipt_id', sa.String(length=36), nullable=True),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('provider_transaction_id', sa.String(length=160), nullable=False),
    sa.Column('kind', sa.Enum('CAPTURE', 'REFUND', 'CHARGEBACK', 'DISPUTE_REVERSAL', 'FEE', 'PAYOUT', name='paymenttransactionkind', native_enum=False), nullable=False),
    sa.Column('amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("currency = 'CNY'", name='ck_payment_transaction_currency'),
    sa.CheckConstraint('amount_cents > 0', name='ck_payment_transaction_amount'),
    sa.ForeignKeyConstraint(['dispute_id'], ['payment_disputes.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['order_id'], ['payment_orders.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['refund_id'], ['payment_refunds.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['webhook_receipt_id'], ['payment_webhook_receipts.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'provider_transaction_id', name='uq_payment_transaction_provider_id'),
    sa.UniqueConstraint('webhook_receipt_id')
    )
    with op.batch_alter_table('payment_transactions', schema=None) as batch_op:
        batch_op.create_index('ix_payment_transaction_order_created', ['order_id', 'created_at', 'id'], unique=False)
        batch_op.create_index(batch_op.f('ix_payment_transactions_dispute_id'), ['dispute_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_payment_transactions_order_id'), ['order_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_payment_transactions_refund_id'), ['refund_id'], unique=False)

    op.create_table('accounts_receivable_ledger_entries',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('company_id', sa.String(length=36), nullable=False),
    sa.Column('invoice_id', sa.String(length=36), nullable=True),
    sa.Column('payment_transaction_id', sa.String(length=36), nullable=True),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('debit_cents', sa.BigInteger(), nullable=False),
    sa.Column('credit_cents', sa.BigInteger(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=160), nullable=False),
    sa.Column('note', sa.String(length=240), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('debit_cents >= 0 AND credit_cents >= 0 AND ((debit_cents > 0 AND credit_cents = 0) OR (debit_cents = 0 AND credit_cents > 0))', name='ck_accounts_receivable_entry_shape'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['invoice_id'], ['company_invoices.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['payment_transaction_id'], ['payment_transactions.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key', name='uq_accounts_receivable_idempotency')
    )
    with op.batch_alter_table('accounts_receivable_ledger_entries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_accounts_receivable_ledger_entries_company_id'), ['company_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_accounts_receivable_ledger_entries_invoice_id'), ['invoice_id'], unique=False)

    with op.batch_alter_table('company_point_ledger_entries', schema=None) as batch_op:
        batch_op.drop_constraint('ck_company_point_ledger_kind', type_='check')
        batch_op.drop_constraint('ck_company_point_ledger_delta_shape', type_='check')
        batch_op.add_column(sa.Column('reversal_reserved_delta_points', sa.BigInteger(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('debt_delta_points', sa.BigInteger(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('payment_order_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('payment_refund_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('payment_dispute_id', sa.String(length=36), nullable=True))
        batch_op.alter_column('kind',
               existing_type=sa.VARCHAR(length=9),
               type_=sa.Enum('MIGRATION', 'CREDIT', 'RESERVE', 'SETTLE', 'RELEASE', 'REFUND_RESERVE', 'REFUND_SETTLE', 'REFUND_RELEASE', 'CHARGEBACK', 'DISPUTE_REVERSAL', 'DEBT_RECOVERY', name='pointledgerkind', native_enum=False),
               existing_nullable=False)
        batch_op.create_index(batch_op.f('ix_company_point_ledger_entries_payment_dispute_id'), ['payment_dispute_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_company_point_ledger_entries_payment_order_id'), ['payment_order_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_company_point_ledger_entries_payment_refund_id'), ['payment_refund_id'], unique=False)
        batch_op.create_foreign_key('fk_company_point_ledger_payment_dispute', 'payment_disputes', ['payment_dispute_id'], ['id'], ondelete='RESTRICT', use_alter=True)
        batch_op.create_foreign_key('fk_company_point_ledger_payment_order', 'payment_orders', ['payment_order_id'], ['id'], ondelete='RESTRICT', use_alter=True)
        batch_op.create_foreign_key('fk_company_point_ledger_payment_refund', 'payment_refunds', ['payment_refund_id'], ['id'], ondelete='RESTRICT', use_alter=True)
        batch_op.create_check_constraint('ck_company_point_ledger_kind', "kind IN ('MIGRATION','CREDIT','RESERVE','SETTLE','RELEASE','REFUND_RESERVE','REFUND_SETTLE','REFUND_RELEASE','CHARGEBACK','DISPUTE_REVERSAL','DEBT_RECOVERY')")
        batch_op.create_check_constraint('ck_company_point_ledger_delta_shape', "(kind='MIGRATION' AND amount_points>=0 AND available_delta_points=amount_points AND reserved_delta_points=0 AND reversal_reserved_delta_points=0 AND debt_delta_points=0) OR (kind='CREDIT' AND amount_points>0 AND available_delta_points=amount_points AND reserved_delta_points=0 AND reversal_reserved_delta_points=0 AND debt_delta_points=0) OR (kind='RESERVE' AND amount_points>0 AND available_delta_points=-amount_points AND reserved_delta_points=amount_points AND reversal_reserved_delta_points=0 AND debt_delta_points=0) OR (kind='SETTLE' AND amount_points>0 AND available_delta_points=0 AND reserved_delta_points=-amount_points AND reversal_reserved_delta_points=0 AND debt_delta_points=0) OR (kind='RELEASE' AND amount_points>0 AND available_delta_points=amount_points AND reserved_delta_points=-amount_points AND reversal_reserved_delta_points=0 AND debt_delta_points=0) OR (kind='REFUND_RESERVE' AND amount_points>0 AND available_delta_points=-amount_points AND reserved_delta_points=0 AND reversal_reserved_delta_points=amount_points AND debt_delta_points=0) OR (kind='REFUND_SETTLE' AND amount_points>0 AND available_delta_points=0 AND reserved_delta_points=0 AND reversal_reserved_delta_points=-amount_points AND debt_delta_points=0) OR (kind='REFUND_RELEASE' AND amount_points>0 AND available_delta_points=amount_points AND reserved_delta_points=0 AND reversal_reserved_delta_points=-amount_points AND debt_delta_points=0) OR (kind='CHARGEBACK' AND amount_points>0 AND available_delta_points<=0 AND reserved_delta_points=0 AND reversal_reserved_delta_points=0 AND debt_delta_points>=0 AND -available_delta_points+debt_delta_points=amount_points) OR (kind='DISPUTE_REVERSAL' AND amount_points>0 AND available_delta_points>=0 AND reserved_delta_points=0 AND reversal_reserved_delta_points=0 AND debt_delta_points<=0 AND available_delta_points-debt_delta_points=amount_points) OR (kind='DEBT_RECOVERY' AND amount_points>0 AND available_delta_points=0 AND reserved_delta_points=0 AND reversal_reserved_delta_points=0 AND debt_delta_points=-amount_points)")

    with op.batch_alter_table('company_point_lots', schema=None) as batch_op:
        batch_op.drop_constraint('ck_company_point_lot_conservation', type_='check')
        batch_op.drop_constraint('ck_company_point_lot_value_basis', type_='check')
        batch_op.add_column(sa.Column('reversal_reserved_points', sa.BigInteger(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('reversed_points', sa.BigInteger(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('receivable_basis_cents', sa.BigInteger(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('payment_order_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('contract_version_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('billing_cycle_id', sa.String(length=36), nullable=True))
        batch_op.create_index(batch_op.f('ix_company_point_lots_billing_cycle_id'), ['billing_cycle_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_company_point_lots_contract_version_id'), ['contract_version_id'], unique=False)
        batch_op.create_unique_constraint('uq_company_point_lot_payment_order', ['payment_order_id'])
        batch_op.create_foreign_key('fk_company_point_lot_payment_order', 'payment_orders', ['payment_order_id'], ['id'], ondelete='RESTRICT', use_alter=True)
        batch_op.create_foreign_key('fk_company_point_lot_contract_version', 'company_billing_contract_versions', ['contract_version_id'], ['id'], ondelete='RESTRICT', use_alter=True)
        batch_op.create_foreign_key('fk_company_point_lot_billing_cycle', 'company_billing_cycles', ['billing_cycle_id'], ['id'], ondelete='RESTRICT', use_alter=True)
        batch_op.create_check_constraint('ck_company_point_lot_receivable_basis', 'receivable_basis_cents >= 0')
        batch_op.create_check_constraint('ck_company_point_lot_reversal_reserved', 'reversal_reserved_points >= 0')
        batch_op.create_check_constraint('ck_company_point_lot_reversed', 'reversed_points >= 0')
        batch_op.create_check_constraint('ck_company_point_lot_value_basis', 'cash_basis_cents + receivable_basis_cents + subsidy_cents = original_points * 10')
        batch_op.create_check_constraint('ck_company_point_lot_conservation', 'original_points = available_points + reserved_points + reversal_reserved_points + settled_points + reversed_points')

    with op.batch_alter_table('company_point_wallet_accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reversal_reserved_points', sa.BigInteger(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('debt_points', sa.BigInteger(), server_default='0', nullable=False))
        batch_op.create_check_constraint('ck_company_point_wallet_debt', 'debt_points >= 0')
        batch_op.create_check_constraint('ck_company_point_wallet_reversal_reserved', 'reversal_reserved_points >= 0')

    with op.batch_alter_table('ledger_entries', schema=None) as batch_op:
        batch_op.alter_column('kind',
               existing_type=sa.VARCHAR(length=8),
               type_=sa.Enum('RECHARGE', 'RESERVE', 'SETTLE', 'RELEASE', 'REFUND_RESERVE', 'REFUND_SETTLE', 'REFUND_RELEASE', 'CHARGEBACK', 'DISPUTE_REVERSAL', 'DEBT_RECOVERY', name='ledgerkind', native_enum=False),
               existing_nullable=False)

    with op.batch_alter_table('personal_ledger_entries', schema=None) as batch_op:
        batch_op.drop_constraint('ck_personal_ledger_kind', type_='check')
        batch_op.add_column(sa.Column('reversal_reserved_delta_points', sa.BigInteger(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('debt_delta_points', sa.BigInteger(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('payment_order_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('payment_refund_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('payment_dispute_id', sa.String(length=36), nullable=True))
        batch_op.alter_column('kind',
               existing_type=sa.VARCHAR(length=8),
               type_=sa.Enum('RECHARGE', 'RESERVE', 'SETTLE', 'RELEASE', 'REFUND_RESERVE', 'REFUND_SETTLE', 'REFUND_RELEASE', 'CHARGEBACK', 'DISPUTE_REVERSAL', 'DEBT_RECOVERY', name='ledgerkind', native_enum=False),
               existing_nullable=False)
        batch_op.create_index(batch_op.f('ix_personal_ledger_entries_payment_dispute_id'), ['payment_dispute_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_personal_ledger_entries_payment_order_id'), ['payment_order_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_personal_ledger_entries_payment_refund_id'), ['payment_refund_id'], unique=False)
        batch_op.create_foreign_key('fk_personal_ledger_payment_dispute', 'payment_disputes', ['payment_dispute_id'], ['id'], ondelete='RESTRICT', use_alter=True)
        batch_op.create_foreign_key('fk_personal_ledger_payment_order', 'payment_orders', ['payment_order_id'], ['id'], ondelete='RESTRICT', use_alter=True)
        batch_op.create_foreign_key('fk_personal_ledger_payment_refund', 'payment_refunds', ['payment_refund_id'], ['id'], ondelete='RESTRICT', use_alter=True)
        batch_op.create_check_constraint('ck_personal_ledger_delta_shape', "(kind = 'RECHARGE' AND amount_points > 0 AND available_delta_points = amount_points AND reserved_delta_points = 0 AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR (kind = 'RESERVE' AND amount_points > 0 AND available_delta_points = -amount_points AND reserved_delta_points = amount_points AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR (kind = 'SETTLE' AND amount_points >= 0 AND available_delta_points >= 0 AND reserved_delta_points <= 0 AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR (kind = 'RELEASE' AND amount_points > 0 AND available_delta_points = amount_points AND reserved_delta_points = -amount_points AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR (kind = 'REFUND_RESERVE' AND amount_points > 0 AND available_delta_points = -amount_points AND reserved_delta_points = 0 AND reversal_reserved_delta_points = amount_points AND debt_delta_points = 0) OR (kind = 'REFUND_SETTLE' AND amount_points > 0 AND available_delta_points = 0 AND reserved_delta_points = 0 AND reversal_reserved_delta_points = -amount_points AND debt_delta_points = 0) OR (kind = 'REFUND_RELEASE' AND amount_points > 0 AND available_delta_points = amount_points AND reserved_delta_points = 0 AND reversal_reserved_delta_points = -amount_points AND debt_delta_points = 0) OR (kind = 'CHARGEBACK' AND amount_points > 0 AND available_delta_points <= 0 AND reserved_delta_points = 0 AND reversal_reserved_delta_points = 0 AND debt_delta_points >= 0 AND -available_delta_points + debt_delta_points = amount_points) OR (kind = 'DISPUTE_REVERSAL' AND amount_points > 0 AND available_delta_points >= 0 AND reserved_delta_points = 0 AND reversal_reserved_delta_points = 0 AND debt_delta_points <= 0 AND available_delta_points - debt_delta_points = amount_points) OR (kind = 'DEBT_RECOVERY' AND amount_points > 0 AND available_delta_points = 0 AND reserved_delta_points = 0 AND reversal_reserved_delta_points = 0 AND debt_delta_points = -amount_points)")
        batch_op.create_check_constraint('ck_personal_ledger_kind', "kind IN ('RECHARGE','RESERVE','SETTLE','RELEASE','REFUND_RESERVE','REFUND_SETTLE','REFUND_RELEASE','CHARGEBACK','DISPUTE_REVERSAL','DEBT_RECOVERY')")

    with op.batch_alter_table('personal_wallet_accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reversal_reserved_points', sa.BigInteger(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('debt_points', sa.BigInteger(), server_default='0', nullable=False))
        batch_op.create_check_constraint('ck_personal_wallet_debt_nonnegative', 'debt_points >= 0')
        batch_op.create_check_constraint('ck_personal_wallet_reversal_reserved_nonnegative', 'reversal_reserved_points >= 0')

    _backfill_personal_lot_projection()
    _create_sqlite_guards()
    _create_postgres_guards()
    if (
        op.get_bind().dialect.name == "postgresql"
        and protected_platform_runtime_requested_v12()
    ):
        _apply_commercial_acl()
    # ### end Alembic commands ###


def downgrade() -> None:
    _drop_sqlite_guards()
    _drop_postgres_guards()
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('personal_wallet_accounts', schema=None) as batch_op:
        batch_op.drop_constraint('ck_personal_wallet_reversal_reserved_nonnegative', type_='check')
        batch_op.drop_constraint('ck_personal_wallet_debt_nonnegative', type_='check')
        batch_op.drop_column('debt_points')
        batch_op.drop_column('reversal_reserved_points')

    with op.batch_alter_table('personal_ledger_entries', schema=None) as batch_op:
        batch_op.drop_constraint('ck_personal_ledger_delta_shape', type_='check')
        batch_op.drop_constraint('ck_personal_ledger_kind', type_='check')
        batch_op.drop_constraint('fk_personal_ledger_payment_dispute', type_='foreignkey')
        batch_op.drop_constraint('fk_personal_ledger_payment_order', type_='foreignkey')
        batch_op.drop_constraint('fk_personal_ledger_payment_refund', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_personal_ledger_entries_payment_refund_id'))
        batch_op.drop_index(batch_op.f('ix_personal_ledger_entries_payment_order_id'))
        batch_op.drop_index(batch_op.f('ix_personal_ledger_entries_payment_dispute_id'))
        batch_op.alter_column('kind',
               existing_type=sa.Enum('RECHARGE', 'RESERVE', 'SETTLE', 'RELEASE', 'REFUND_RESERVE', 'REFUND_SETTLE', 'REFUND_RELEASE', 'CHARGEBACK', 'DISPUTE_REVERSAL', 'DEBT_RECOVERY', name='ledgerkind', native_enum=False),
               type_=sa.VARCHAR(length=8),
               existing_nullable=False)
        batch_op.drop_column('payment_dispute_id')
        batch_op.drop_column('payment_refund_id')
        batch_op.drop_column('payment_order_id')
        batch_op.drop_column('debt_delta_points')
        batch_op.drop_column('reversal_reserved_delta_points')
        batch_op.create_check_constraint('ck_personal_ledger_kind', "kind IN ('RECHARGE','RESERVE','SETTLE','RELEASE')")

    with op.batch_alter_table('ledger_entries', schema=None) as batch_op:
        batch_op.alter_column('kind',
               existing_type=sa.Enum('RECHARGE', 'RESERVE', 'SETTLE', 'RELEASE', 'REFUND_RESERVE', 'REFUND_SETTLE', 'REFUND_RELEASE', 'CHARGEBACK', 'DISPUTE_REVERSAL', 'DEBT_RECOVERY', name='ledgerkind', native_enum=False),
               type_=sa.VARCHAR(length=8),
               existing_nullable=False)

    with op.batch_alter_table('company_point_wallet_accounts', schema=None) as batch_op:
        batch_op.drop_constraint('ck_company_point_wallet_reversal_reserved', type_='check')
        batch_op.drop_constraint('ck_company_point_wallet_debt', type_='check')
        batch_op.drop_column('debt_points')
        batch_op.drop_column('reversal_reserved_points')

    with op.batch_alter_table('company_point_lots', schema=None) as batch_op:
        batch_op.drop_constraint('ck_company_point_lot_conservation', type_='check')
        batch_op.drop_constraint('ck_company_point_lot_value_basis', type_='check')
        batch_op.drop_constraint('ck_company_point_lot_reversed', type_='check')
        batch_op.drop_constraint('ck_company_point_lot_reversal_reserved', type_='check')
        batch_op.drop_constraint('ck_company_point_lot_receivable_basis', type_='check')
        batch_op.drop_constraint('fk_company_point_lot_payment_order', type_='foreignkey')
        batch_op.drop_constraint('fk_company_point_lot_contract_version', type_='foreignkey')
        batch_op.drop_constraint('fk_company_point_lot_billing_cycle', type_='foreignkey')
        batch_op.drop_constraint('uq_company_point_lot_payment_order', type_='unique')
        batch_op.drop_index(batch_op.f('ix_company_point_lots_contract_version_id'))
        batch_op.drop_index(batch_op.f('ix_company_point_lots_billing_cycle_id'))
        batch_op.drop_column('billing_cycle_id')
        batch_op.drop_column('contract_version_id')
        batch_op.drop_column('payment_order_id')
        batch_op.drop_column('receivable_basis_cents')
        batch_op.drop_column('reversed_points')
        batch_op.drop_column('reversal_reserved_points')
        batch_op.create_check_constraint('ck_company_point_lot_value_basis', 'cash_basis_cents + subsidy_cents = original_points * 10')
        batch_op.create_check_constraint('ck_company_point_lot_conservation', 'original_points = available_points + reserved_points + settled_points')

    with op.batch_alter_table('company_point_ledger_entries', schema=None) as batch_op:
        batch_op.drop_constraint('ck_company_point_ledger_kind', type_='check')
        batch_op.drop_constraint('ck_company_point_ledger_delta_shape', type_='check')
        batch_op.drop_constraint('fk_company_point_ledger_payment_dispute', type_='foreignkey')
        batch_op.drop_constraint('fk_company_point_ledger_payment_order', type_='foreignkey')
        batch_op.drop_constraint('fk_company_point_ledger_payment_refund', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_company_point_ledger_entries_payment_refund_id'))
        batch_op.drop_index(batch_op.f('ix_company_point_ledger_entries_payment_order_id'))
        batch_op.drop_index(batch_op.f('ix_company_point_ledger_entries_payment_dispute_id'))
        batch_op.alter_column('kind',
               existing_type=sa.Enum('MIGRATION', 'CREDIT', 'RESERVE', 'SETTLE', 'RELEASE', 'REFUND_RESERVE', 'REFUND_SETTLE', 'REFUND_RELEASE', 'CHARGEBACK', 'DISPUTE_REVERSAL', 'DEBT_RECOVERY', name='pointledgerkind', native_enum=False),
               type_=sa.VARCHAR(length=9),
               existing_nullable=False)
        batch_op.drop_column('payment_dispute_id')
        batch_op.drop_column('payment_refund_id')
        batch_op.drop_column('payment_order_id')
        batch_op.drop_column('debt_delta_points')
        batch_op.drop_column('reversal_reserved_delta_points')
        batch_op.create_check_constraint('ck_company_point_ledger_kind', "kind IN ('MIGRATION','CREDIT','RESERVE','SETTLE','RELEASE')")
        batch_op.create_check_constraint('ck_company_point_ledger_delta_shape', "(kind='MIGRATION' AND amount_points>=0 AND available_delta_points=amount_points AND reserved_delta_points=0) OR (kind='CREDIT' AND amount_points>0 AND available_delta_points=amount_points AND reserved_delta_points=0) OR (kind='RESERVE' AND amount_points>0 AND available_delta_points=-amount_points AND reserved_delta_points=amount_points) OR (kind='SETTLE' AND amount_points>0 AND available_delta_points=0 AND reserved_delta_points=-amount_points) OR (kind='RELEASE' AND amount_points>0 AND available_delta_points=amount_points AND reserved_delta_points=-amount_points)")

    with op.batch_alter_table('accounts_receivable_ledger_entries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_accounts_receivable_ledger_entries_invoice_id'))
        batch_op.drop_index(batch_op.f('ix_accounts_receivable_ledger_entries_company_id'))

    op.drop_table('accounts_receivable_ledger_entries')
    with op.batch_alter_table('payment_transactions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_payment_transactions_refund_id'))
        batch_op.drop_index(batch_op.f('ix_payment_transactions_order_id'))
        batch_op.drop_index(batch_op.f('ix_payment_transactions_dispute_id'))
        batch_op.drop_index('ix_payment_transaction_order_created')

    op.drop_table('payment_transactions')
    with op.batch_alter_table('company_invoice_lines', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_company_invoice_lines_task_id'))
        batch_op.drop_index(batch_op.f('ix_company_invoice_lines_invoice_id'))

    op.drop_table('company_invoice_lines')
    with op.batch_alter_table('point_lot_settlement_value_allocations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_point_lot_settlement_value_allocations_task_id'))
        batch_op.drop_index(batch_op.f('ix_point_lot_settlement_value_allocations_personal_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_point_lot_settlement_value_allocations_company_id'))

    op.drop_table('point_lot_settlement_value_allocations')
    with op.batch_alter_table('payment_webhook_receipts', schema=None) as batch_op:
        batch_op.drop_index('ix_payment_webhook_received')
        batch_op.drop_index(batch_op.f('ix_payment_webhook_receipts_refund_id'))
        batch_op.drop_index(batch_op.f('ix_payment_webhook_receipts_order_id'))
        batch_op.drop_index(batch_op.f('ix_payment_webhook_receipts_dispute_id'))

    op.drop_table('payment_webhook_receipts')
    with op.batch_alter_table('personal_task_point_lot_allocations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_personal_task_point_lot_allocations_workspace_id'))
        batch_op.drop_index('ix_personal_task_point_lot_allocation_task')

    op.drop_table('personal_task_point_lot_allocations')
    with op.batch_alter_table('payment_refunds', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_payment_refunds_order_id'))

    op.drop_table('payment_refunds')
    with op.batch_alter_table('payment_disputes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_payment_disputes_order_id'))

    op.drop_table('payment_disputes')
    with op.batch_alter_table('payment_attempts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_payment_attempts_order_id'))

    op.drop_table('payment_attempts')
    with op.batch_alter_table('auto_recharge_executions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_auto_recharge_executions_rule_id'))

    op.drop_table('auto_recharge_executions')
    with op.batch_alter_table('personal_point_lots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_personal_point_lots_workspace_id'))
        batch_op.drop_index('ix_personal_point_lot_spend_order')

    op.drop_table('personal_point_lots')
    with op.batch_alter_table('payment_orders', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_payment_orders_personal_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_payment_orders_company_id'))
        batch_op.drop_index('ix_payment_order_status_created')

    op.drop_table('payment_orders')
    with op.batch_alter_table('company_invoices', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_company_invoices_company_id'))

    op.drop_table('company_invoices')
    with op.batch_alter_table('auto_recharge_rules', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_auto_recharge_rules_personal_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_auto_recharge_rules_company_id'))

    op.drop_table('auto_recharge_rules')
    with op.batch_alter_table('finance_reconciliation_resolutions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_finance_reconciliation_resolutions_exception_id'))

    op.drop_table('finance_reconciliation_resolutions')
    with op.batch_alter_table('company_billing_cycles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_company_billing_cycles_company_id'))

    op.drop_table('company_billing_cycles')
    op.drop_table('company_billing_accounts')
    with op.batch_alter_table('finance_reconciliation_snapshots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_finance_reconciliation_snapshots_run_id'))

    op.drop_table('finance_reconciliation_snapshots')
    with op.batch_alter_table('finance_reconciliation_exceptions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_finance_reconciliation_exceptions_run_id'))
        batch_op.drop_index('ix_finance_reconciliation_exception_run')

    op.drop_table('finance_reconciliation_exceptions')
    with op.batch_alter_table('company_billing_contract_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_company_billing_contract_versions_company_id'))
        batch_op.drop_index('ix_company_billing_contract_company_effective')

    op.drop_table('company_billing_contract_versions')
    op.drop_table('payment_settlement_entries')
    op.drop_table('finance_reconciliation_runs')
    _create_sqlite_legacy_guards()
    _restore_postgres_legacy_guards()
    # ### end Alembic commands ###
