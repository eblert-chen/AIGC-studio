"""Add the immutable channel cost ledger.

Revision ID: 0015_channel_cost_ledger
Revises: 0014_billing_report_hardening
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0015_channel_cost_ledger"
down_revision: str | None = "0014_billing_report_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


channel_type = sa.Enum(
    "REVERSE",
    "THIRD_PARTY_API",
    "OFFICIAL",
    name="channeltype",
    native_enum=False,
)
channel_cost_source = sa.Enum(
    "PLATFORM_ADMIN",
    "RELAY",
    name="channelcostsource",
    native_enum=False,
)


def _create_immutability_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_channel_cost_entry_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'channel cost entries are immutable';
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_channel_cost_entries_immutable
            BEFORE UPDATE OR DELETE ON channel_cost_entries
            FOR EACH ROW EXECUTE FUNCTION reject_channel_cost_entry_mutation()
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_channel_cost_entries_no_truncate
            BEFORE TRUNCATE ON channel_cost_entries
            FOR EACH STATEMENT EXECUTE FUNCTION reject_channel_cost_entry_mutation()
            """
        )
    elif dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_channel_cost_entries_no_update
            BEFORE UPDATE ON channel_cost_entries
            BEGIN
                SELECT RAISE(ABORT, 'channel cost entries are immutable');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_channel_cost_entries_no_delete
            BEFORE DELETE ON channel_cost_entries
            BEGIN
                SELECT RAISE(ABORT, 'channel cost entries are immutable');
            END
            """
        )


def _drop_immutability_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_channel_cost_entries_no_truncate "
            "ON channel_cost_entries"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_channel_cost_entries_immutable "
            "ON channel_cost_entries"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS reject_channel_cost_entry_mutation()"
        )
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_channel_cost_entries_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_channel_cost_entries_no_delete")


def upgrade() -> None:
    op.create_table(
        "channel_cost_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("channel_key", sa.String(length=120), nullable=False),
        sa.Column("channel_type", channel_type, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "external_reference", sa.String(length=240), nullable=False
        ),
        sa.Column("company_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("relay_job_id", sa.String(length=36), nullable=True),
        sa.Column("note", sa.String(length=240), nullable=False),
        sa.Column("source", channel_cost_source, nullable=False),
        sa.Column("recorded_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount_cents >= -9000000000000000 "
            "AND amount_cents <= 9000000000000000",
            name="ck_channel_cost_amount_range",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["generation_tasks.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_channel_cost_idempotency"
        ),
    )
    for index_name, columns in (
        ("ix_channel_cost_entries_company_id", ["company_id"]),
        ("ix_channel_cost_entries_task_id", ["task_id"]),
        ("ix_channel_cost_entries_relay_job_id", ["relay_job_id"]),
        (
            "ix_channel_cost_entries_recorded_by_user_id",
            ["recorded_by_user_id"],
        ),
        ("ix_channel_cost_occurred", ["occurred_at", "id"]),
        (
            "ix_channel_cost_channel_occurred",
            ["channel_type", "channel_key", "occurred_at"],
        ),
        (
            "ix_channel_cost_company_occurred",
            ["company_id", "occurred_at"],
        ),
    ):
        op.create_index(index_name, "channel_cost_entries", columns)
    _create_immutability_guards()


def downgrade() -> None:
    _drop_immutability_guards()
    op.drop_table("channel_cost_entries")
