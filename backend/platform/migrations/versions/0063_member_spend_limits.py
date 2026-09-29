"""Add per-member ceilings on company point reservations.

Revision ID: 0063_member_spend_limits
Revises: 0062_share_links

The company wallet is shared, so an owner needs a way to say how much of it one
member may commit. This table is the policy only -- it stores a ceiling, never
a counter. Usage is always summed from the append-only point ledger, which
cannot be updated or deleted, so the number the admission check sees cannot be
made to disagree with what was actually reserved.

A NULL ceiling means explicitly unlimited and the row is retained, so lifting a
cap leaves an auditable decision behind instead of deleting the evidence. A
member with no row is unlimited too, which keeps every existing company
unaffected by the upgrade.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0063_member_spend_limits"
down_revision: str | None = "0062_share_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SPEND_LIMIT_PERIOD = sa.Enum(
    "DAY",
    "WEEK",
    "MONTH",
    "LIFETIME",
    name="spendlimitperiod",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "company_member_spend_limits",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("period_kind", _SPEND_LIMIT_PERIOD, nullable=False),
        sa.Column("limit_points", sa.BigInteger(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "limit_points IS NULL OR limit_points >= 0",
            name="ck_member_spend_limit_nonnegative",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "company_id", "user_id", name="uq_company_member_spend_limit"
        ),
    )
    op.create_index(
        "ix_company_member_spend_limits_company_id",
        "company_member_spend_limits",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        "ix_company_member_spend_limits_user_id",
        "company_member_spend_limits",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_company_member_spend_limits_user_id",
        table_name="company_member_spend_limits",
    )
    op.drop_index(
        "ix_company_member_spend_limits_company_id",
        table_name="company_member_spend_limits",
    )
    op.drop_table("company_member_spend_limits")
