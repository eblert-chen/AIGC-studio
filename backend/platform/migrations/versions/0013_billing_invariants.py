"""Harden billing prices, money columns, and the immutable ledger.

Revision ID: 0013_billing_invariants
Revises: 0012_company_member_levels
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0013_billing_invariants"
down_revision: str | None = "0012_company_member_levels"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


MONEY_COLUMNS: tuple[tuple[str, tuple[tuple[str, bool], ...]], ...] = (
    (
        "company_model_grants",
        (("price_per_second_cents", True), ("price_per_item_cents", True)),
    ),
    (
        "wallet_accounts",
        (("available_cents", False), ("reserved_cents", False)),
    ),
    (
        "generation_tasks",
        (
            ("quote_cents", False),
            ("reserved_cents", False),
            ("actual_cost_cents", True),
        ),
    ),
    (
        "ledger_entries",
        (
            ("amount_cents", False),
            ("available_delta_cents", False),
            ("reserved_delta_cents", False),
        ),
    ),
    ("task_timeout_events", (("released_cents", False),)),
)


def _alter_money_columns(target_type: sa.types.TypeEngine) -> None:
    existing_type: sa.types.TypeEngine
    if isinstance(target_type, sa.BigInteger):
        existing_type = sa.Integer()
    else:
        existing_type = sa.BigInteger()
    for table_name, columns in MONEY_COLUMNS:
        with op.batch_alter_table(table_name) as batch:
            for column_name, nullable in columns:
                batch.alter_column(
                    column_name,
                    existing_type=existing_type,
                    type_=target_type,
                    existing_nullable=nullable,
                )


def _create_ledger_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_ledger_entry_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'ledger entries are immutable';
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_ledger_entries_immutable
            BEFORE UPDATE OR DELETE ON ledger_entries
            FOR EACH ROW EXECUTE FUNCTION reject_ledger_entry_mutation()
            """
        )
    elif dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_ledger_entries_no_update
            BEFORE UPDATE ON ledger_entries
            BEGIN
                SELECT RAISE(ABORT, 'ledger entries are immutable');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_ledger_entries_no_delete
            BEFORE DELETE ON ledger_entries
            BEGIN
                SELECT RAISE(ABORT, 'ledger entries are immutable');
            END
            """
        )


def _drop_ledger_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_ledger_entries_immutable "
            "ON ledger_entries"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_ledger_entry_mutation()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_ledger_entries_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_ledger_entries_no_delete")


def upgrade() -> None:
    connection = op.get_bind()
    invalid_price_rows = int(
        connection.scalar(
            sa.text(
                "SELECT count(*) FROM company_model_grants "
                "WHERE (price_per_second_cents IS NULL "
                "AND price_per_item_cents IS NULL) "
                "OR (price_per_second_cents IS NOT NULL "
                "AND price_per_item_cents IS NOT NULL)"
            )
        )
        or 0
    )
    if invalid_price_rows:
        raise RuntimeError(
            "company_model_grants contains rows without exactly one price"
        )

    conflicting_models = int(
        connection.scalar(
            sa.text(
                "SELECT count(*) FROM ("
                "SELECT model_id FROM company_model_grants GROUP BY model_id "
                "HAVING SUM(CASE WHEN price_per_second_cents IS NOT NULL "
                "THEN 1 ELSE 0 END) > 0 AND "
                "SUM(CASE WHEN price_per_item_cents IS NOT NULL "
                "THEN 1 ELSE 0 END) > 0"
                ") AS conflicting_billing_modes"
            )
        )
        or 0
    )
    if conflicting_models:
        raise RuntimeError(
            "a model cannot use different billing modes across companies"
        )

    with op.batch_alter_table("model_definitions") as batch:
        batch.add_column(
            sa.Column("billing_mode", sa.String(length=24), nullable=True)
        )
    connection.execute(
        sa.text(
            "UPDATE model_definitions SET billing_mode = 'per_item' "
            "WHERE id IN (SELECT model_id FROM company_model_grants "
            "WHERE price_per_item_cents IS NOT NULL)"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE model_definitions SET billing_mode = 'per_second' "
            "WHERE billing_mode IS NULL"
        )
    )
    with op.batch_alter_table("model_definitions") as batch:
        batch.alter_column(
            "billing_mode",
            existing_type=sa.String(length=24),
            nullable=False,
        )
        batch.create_check_constraint(
            "ck_model_billing_mode",
            "billing_mode IN ('per_second', 'per_item')",
        )

    _alter_money_columns(sa.BigInteger())
    with op.batch_alter_table("company_model_grants") as batch:
        batch.create_check_constraint(
            "ck_grant_exactly_one_price",
            "(price_per_second_cents IS NOT NULL "
            "AND price_per_item_cents IS NULL) OR "
            "(price_per_second_cents IS NULL "
            "AND price_per_item_cents IS NOT NULL)",
        )
    op.create_index(
        "ix_ledger_company_kind_created",
        "ledger_entries",
        ["company_id", "kind", "created_at"],
        unique=False,
    )
    _create_ledger_guards()


def downgrade() -> None:
    connection = op.get_bind()
    for table_name, columns in MONEY_COLUMNS:
        for column_name, _ in columns:
            outside_integer = int(
                connection.scalar(
                    sa.text(
                        f"SELECT count(*) FROM {table_name} "
                        f"WHERE {column_name} < -2147483648 "
                        f"OR {column_name} > 2147483647"
                    )
                )
                or 0
            )
            if outside_integer:
                raise RuntimeError(
                    f"cannot downgrade {table_name}.{column_name}: "
                    "values exceed INTEGER range"
                )

    _drop_ledger_guards()
    op.drop_index(
        "ix_ledger_company_kind_created", table_name="ledger_entries"
    )
    with op.batch_alter_table("company_model_grants") as batch:
        batch.drop_constraint(
            "ck_grant_exactly_one_price", type_="check"
        )
    _alter_money_columns(sa.Integer())
    with op.batch_alter_table("model_definitions") as batch:
        batch.drop_constraint("ck_model_billing_mode", type_="check")
        batch.drop_column("billing_mode")
