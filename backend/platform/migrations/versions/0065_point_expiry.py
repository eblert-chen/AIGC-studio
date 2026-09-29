"""Enable point-lot expiry settlement.

Adds the EXPIRY kind to both ledger CHECK guards and the matching delta-shape
clause, an expired_points column (with conservation update) on both lot tables,
and dedicated sweep indexes on the lot expiry column. Frozen SQL, not live ORM
imports. Constraint replacement uses batch mode so SQLite recreates tables and
PostgreSQL passes operations through unchanged (0046 pattern).

Revision ID: 0065_point_expiry
Revises: 0064_subjects
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0065_point_expiry"
down_revision: str | None = "0064_subjects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMPANY_KIND_OLD = (
    "kind IN ('MIGRATION', 'CREDIT', 'RESERVE', 'SETTLE', 'RELEASE', "
    "'REFUND_RESERVE', 'REFUND_SETTLE', 'REFUND_RELEASE', "
    "'CHARGEBACK', 'DISPUTE_REVERSAL', 'DEBT_RECOVERY')"
)
_COMPANY_KIND = (
    "kind IN ('MIGRATION', 'CREDIT', 'RESERVE', 'SETTLE', 'RELEASE', 'EXPIRY', "
    "'REFUND_RESERVE', 'REFUND_SETTLE', 'REFUND_RELEASE', "
    "'CHARGEBACK', 'DISPUTE_REVERSAL', 'DEBT_RECOVERY')"
)
_PERSONAL_KIND_OLD = (
    "kind IN ('RECHARGE', 'RESERVE', 'SETTLE', 'RELEASE', "
    "'REFUND_RESERVE', 'REFUND_SETTLE', 'REFUND_RELEASE', "
    "'CHARGEBACK', 'DISPUTE_REVERSAL', 'DEBT_RECOVERY')"
)
_PERSONAL_KIND = (
    "kind IN ('RECHARGE', 'RESERVE', 'SETTLE', 'RELEASE', 'EXPIRY', "
    "'REFUND_RESERVE', 'REFUND_SETTLE', 'REFUND_RELEASE', "
    "'CHARGEBACK', 'DISPUTE_REVERSAL', 'DEBT_RECOVERY')"
)

_COMPANY_DELTA_OLD = (
    "(kind = 'MIGRATION' AND amount_points >= 0 "
    "AND available_delta_points = amount_points "
    "AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
    "(kind = 'CREDIT' AND amount_points > 0 "
    "AND available_delta_points = amount_points "
    "AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
    "(kind = 'RESERVE' AND amount_points > 0 "
    "AND available_delta_points = -amount_points "
    "AND reserved_delta_points = amount_points "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
    "(kind = 'SETTLE' AND amount_points > 0 "
    "AND available_delta_points = 0 "
    "AND reserved_delta_points = -amount_points "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
    "(kind = 'RELEASE' AND amount_points > 0 "
    "AND available_delta_points = amount_points "
    "AND reserved_delta_points = -amount_points "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
    "(kind = 'REFUND_RESERVE' AND amount_points > 0 "
    "AND available_delta_points = -amount_points AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = amount_points AND debt_delta_points = 0) OR "
    "(kind = 'REFUND_SETTLE' AND amount_points > 0 "
    "AND available_delta_points = 0 AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = -amount_points AND debt_delta_points = 0) OR "
    "(kind = 'REFUND_RELEASE' AND amount_points > 0 "
    "AND available_delta_points = amount_points AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = -amount_points AND debt_delta_points = 0) OR "
    "(kind = 'CHARGEBACK' AND amount_points > 0 "
    "AND available_delta_points <= 0 AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points >= 0 "
    "AND -available_delta_points + debt_delta_points = amount_points) OR "
    "(kind = 'DISPUTE_REVERSAL' AND amount_points > 0 "
    "AND available_delta_points >= 0 AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points <= 0 "
    "AND available_delta_points - debt_delta_points = amount_points) OR "
    "(kind = 'DEBT_RECOVERY' AND amount_points > 0 "
    "AND available_delta_points = 0 AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = 0 "
    "AND debt_delta_points = -amount_points)"
)
_COMPANY_DELTA = (
    "(kind = 'MIGRATION' AND amount_points >= 0 "
    "AND available_delta_points = amount_points "
    "AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
    "(kind = 'CREDIT' AND amount_points > 0 "
    "AND available_delta_points = amount_points "
    "AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
    "(kind = 'RESERVE' AND amount_points > 0 "
    "AND available_delta_points = -amount_points "
    "AND reserved_delta_points = amount_points "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
    "(kind = 'SETTLE' AND amount_points > 0 "
    "AND available_delta_points = 0 "
    "AND reserved_delta_points = -amount_points "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
    "(kind = 'RELEASE' AND amount_points > 0 "
    "AND available_delta_points = amount_points "
    "AND reserved_delta_points = -amount_points "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
    "(kind = 'EXPIRY' AND amount_points > 0 "
    "AND available_delta_points = -amount_points "
    "AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
    "(kind = 'REFUND_RESERVE' AND amount_points > 0 "
    "AND available_delta_points = -amount_points AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = amount_points AND debt_delta_points = 0) OR "
    "(kind = 'REFUND_SETTLE' AND amount_points > 0 "
    "AND available_delta_points = 0 AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = -amount_points AND debt_delta_points = 0) OR "
    "(kind = 'REFUND_RELEASE' AND amount_points > 0 "
    "AND available_delta_points = amount_points AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = -amount_points AND debt_delta_points = 0) OR "
    "(kind = 'CHARGEBACK' AND amount_points > 0 "
    "AND available_delta_points <= 0 AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points >= 0 "
    "AND -available_delta_points + debt_delta_points = amount_points) OR "
    "(kind = 'DISPUTE_REVERSAL' AND amount_points > 0 "
    "AND available_delta_points >= 0 AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points <= 0 "
    "AND available_delta_points - debt_delta_points = amount_points) OR "
    "(kind = 'DEBT_RECOVERY' AND amount_points > 0 "
    "AND available_delta_points = 0 AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = 0 "
    "AND debt_delta_points = -amount_points)"
)

_PERSONAL_DELTA_OLD = (
    "(kind = 'RECHARGE' AND amount_points > 0 "
    "AND available_delta_points = amount_points AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
    "(kind = 'RESERVE' AND amount_points > 0 "
    "AND available_delta_points = -amount_points "
    "AND reserved_delta_points = amount_points "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
    "(kind = 'SETTLE' AND amount_points >= 0 "
    "AND available_delta_points >= 0 AND reserved_delta_points <= 0 "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
    "(kind = 'RELEASE' AND amount_points > 0 "
    "AND available_delta_points = amount_points "
    "AND reserved_delta_points = -amount_points "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
    "(kind = 'REFUND_RESERVE' AND amount_points > 0 "
    "AND available_delta_points = -amount_points AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = amount_points AND debt_delta_points = 0) OR "
    "(kind = 'REFUND_SETTLE' AND amount_points > 0 "
    "AND available_delta_points = 0 AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = -amount_points AND debt_delta_points = 0) OR "
    "(kind = 'REFUND_RELEASE' AND amount_points > 0 "
    "AND available_delta_points = amount_points AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = -amount_points AND debt_delta_points = 0) OR "
    "(kind = 'CHARGEBACK' AND amount_points > 0 "
    "AND available_delta_points <= 0 AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points >= 0 "
    "AND -available_delta_points + debt_delta_points = amount_points) OR "
    "(kind = 'DISPUTE_REVERSAL' AND amount_points > 0 "
    "AND available_delta_points >= 0 AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points <= 0 "
    "AND available_delta_points - debt_delta_points = amount_points) OR "
    "(kind = 'DEBT_RECOVERY' AND amount_points > 0 "
    "AND available_delta_points = 0 AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = 0 "
    "AND debt_delta_points = -amount_points)"
)
_PERSONAL_DELTA = (
    "(kind = 'RECHARGE' AND amount_points > 0 "
    "AND available_delta_points = amount_points AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
    "(kind = 'RESERVE' AND amount_points > 0 "
    "AND available_delta_points = -amount_points "
    "AND reserved_delta_points = amount_points "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
    "(kind = 'SETTLE' AND amount_points >= 0 "
    "AND available_delta_points >= 0 AND reserved_delta_points <= 0 "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
    "(kind = 'RELEASE' AND amount_points > 0 "
    "AND available_delta_points = amount_points "
    "AND reserved_delta_points = -amount_points "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
    "(kind = 'EXPIRY' AND amount_points > 0 "
    "AND available_delta_points = -amount_points "
    "AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
    "(kind = 'REFUND_RESERVE' AND amount_points > 0 "
    "AND available_delta_points = -amount_points AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = amount_points AND debt_delta_points = 0) OR "
    "(kind = 'REFUND_SETTLE' AND amount_points > 0 "
    "AND available_delta_points = 0 AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = -amount_points AND debt_delta_points = 0) OR "
    "(kind = 'REFUND_RELEASE' AND amount_points > 0 "
    "AND available_delta_points = amount_points AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = -amount_points AND debt_delta_points = 0) OR "
    "(kind = 'CHARGEBACK' AND amount_points > 0 "
    "AND available_delta_points <= 0 AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points >= 0 "
    "AND -available_delta_points + debt_delta_points = amount_points) OR "
    "(kind = 'DISPUTE_REVERSAL' AND amount_points > 0 "
    "AND available_delta_points >= 0 AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = 0 AND debt_delta_points <= 0 "
    "AND available_delta_points - debt_delta_points = amount_points) OR "
    "(kind = 'DEBT_RECOVERY' AND amount_points > 0 "
    "AND available_delta_points = 0 AND reserved_delta_points = 0 "
    "AND reversal_reserved_delta_points = 0 "
    "AND debt_delta_points = -amount_points)"
)

_COMPANY_LOT_CONSERVATION_OLD = (
    "original_points = available_points + reserved_points + "
    "reversal_reserved_points + settled_points + reversed_points"
)
_COMPANY_LOT_CONSERVATION = (
    "original_points = available_points + reserved_points + "
    "reversal_reserved_points + settled_points + expired_points + reversed_points"
)
_PERSONAL_LOT_CONSERVATION_OLD = _COMPANY_LOT_CONSERVATION_OLD
_PERSONAL_LOT_CONSERVATION = _COMPANY_LOT_CONSERVATION



_SQLITE_REBUILD_TRIGGERS = (
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
    "trg_personal_point_lot_identity_immutable",
    "trg_personal_point_lot_no_delete",
    "trg_personal_task_point_allocation_scope_insert",
)


def _drop_sqlite_guards() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    for trigger_name in _SQLITE_REBUILD_TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")


def _create_sqlite_guards() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
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
        "('MIGRATION','CREDIT','EXPIRY','REFUND_RESERVE','REFUND_SETTLE',"
        "'REFUND_RELEASE','CHARGEBACK','DISPUTE_REVERSAL','DEBT_RECOVERY')) OR "
        "(NEW.task_id IS NOT NULL AND NEW.kind NOT IN "
        "('RESERVE','SETTLE','RELEASE','EXPIRY')) OR (NEW.task_id IS NOT NULL AND "
        "NOT EXISTS (SELECT 1 FROM generation_tasks t WHERE t.id=NEW.task_id "
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


def upgrade() -> None:
    _drop_sqlite_guards()
    with op.batch_alter_table("company_point_ledger_entries") as batch:
        batch.drop_constraint("ck_company_point_ledger_kind", type_="check")
        batch.create_check_constraint("ck_company_point_ledger_kind", _COMPANY_KIND)
        batch.drop_constraint("ck_company_point_ledger_delta_shape", type_="check")
        batch.create_check_constraint(
            "ck_company_point_ledger_delta_shape", _COMPANY_DELTA
        )
    with op.batch_alter_table("personal_ledger_entries") as batch:
        batch.drop_constraint("ck_personal_ledger_kind", type_="check")
        batch.create_check_constraint("ck_personal_ledger_kind", _PERSONAL_KIND)
        batch.drop_constraint("ck_personal_ledger_delta_shape", type_="check")
        batch.create_check_constraint(
            "ck_personal_ledger_delta_shape", _PERSONAL_DELTA
        )
    with op.batch_alter_table("company_point_lots") as batch:
        batch.add_column(
            sa.Column("expired_points", sa.BigInteger(), server_default="0", nullable=False)
        )
        batch.drop_constraint("ck_company_point_lot_conservation", type_="check")
        batch.create_check_constraint(
            "ck_company_point_lot_conservation", _COMPANY_LOT_CONSERVATION
        )
        batch.create_check_constraint(
            "ck_company_point_lot_expired", "expired_points >= 0"
        )
    with op.batch_alter_table("personal_point_lots") as batch:
        batch.add_column(
            sa.Column("expired_points", sa.BigInteger(), server_default="0", nullable=False)
        )
        batch.drop_constraint("ck_personal_point_lot_conservation", type_="check")
        batch.create_check_constraint(
            "ck_personal_point_lot_conservation", _PERSONAL_LOT_CONSERVATION
        )
        batch.create_check_constraint(
            "ck_personal_point_lot_expired", "expired_points >= 0"
        )
    op.create_index("ix_company_point_lot_expiry", "company_point_lots", ["expires_at"])
    op.create_index("ix_personal_point_lot_expiry", "personal_point_lots", ["expires_at"])
    _create_sqlite_guards()


def downgrade() -> None:
    _drop_sqlite_guards()
    op.drop_index("ix_personal_point_lot_expiry", table_name="personal_point_lots")
    op.drop_index("ix_company_point_lot_expiry", table_name="company_point_lots")
    with op.batch_alter_table("personal_point_lots") as batch:
        batch.drop_constraint("ck_personal_point_lot_expired", type_="check")
        batch.drop_constraint("ck_personal_point_lot_conservation", type_="check")
        batch.create_check_constraint(
            "ck_personal_point_lot_conservation", _PERSONAL_LOT_CONSERVATION_OLD
        )
        batch.drop_column("expired_points")
    with op.batch_alter_table("company_point_lots") as batch:
        batch.drop_constraint("ck_company_point_lot_expired", type_="check")
        batch.drop_constraint("ck_company_point_lot_conservation", type_="check")
        batch.create_check_constraint(
            "ck_company_point_lot_conservation", _COMPANY_LOT_CONSERVATION_OLD
        )
        batch.drop_column("expired_points")
    with op.batch_alter_table("personal_ledger_entries") as batch:
        batch.drop_constraint("ck_personal_ledger_delta_shape", type_="check")
        batch.create_check_constraint(
            "ck_personal_ledger_delta_shape", _PERSONAL_DELTA_OLD
        )
        batch.drop_constraint("ck_personal_ledger_kind", type_="check")
        batch.create_check_constraint("ck_personal_ledger_kind", _PERSONAL_KIND_OLD)
    with op.batch_alter_table("company_point_ledger_entries") as batch:
        batch.drop_constraint("ck_company_point_ledger_delta_shape", type_="check")
        batch.create_check_constraint(
            "ck_company_point_ledger_delta_shape", _COMPANY_DELTA_OLD
        )
        batch.drop_constraint("ck_company_point_ledger_kind", type_="check")
        batch.create_check_constraint(
            "ck_company_point_ledger_kind", _COMPANY_KIND_OLD
        )
