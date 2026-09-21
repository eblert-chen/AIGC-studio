"""Persist personal retail call and concurrency limits.

Revision ID: 0058_personal_limits
Revises: 0057_execution_integrity

Existing retail grants remain unlimited. New limits are nullable, positive and
enforced by Platform task admission per personal workspace and model.
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0058_personal_limits"
down_revision: str | None = "0057_execution_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("personal_retail_model_grants") as batch:
        batch.add_column(sa.Column("call_quota", sa.BigInteger(), nullable=True))
        batch.add_column(
            sa.Column("concurrency_limit", sa.Integer(), nullable=True)
        )
        batch.create_check_constraint(
            "ck_personal_retail_call_quota_positive",
            "call_quota IS NULL OR call_quota > 0",
        )
        batch.create_check_constraint(
            "ck_personal_retail_concurrency_positive",
            "concurrency_limit IS NULL OR concurrency_limit > 0",
        )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        # Serialize the safety check with both concurrent grant mutations and
        # the column drops.  Without taking the DDL-strength lock first, a
        # writer could commit a non-null limit after the count and have that
        # policy silently discarded by the following ALTER TABLE.
        connection.execute(
            sa.text(
                "LOCK TABLE personal_retail_model_grants "
                "IN ACCESS EXCLUSIVE MODE"
            )
        )
    configured = int(
        connection.scalar(
            sa.text(
                "SELECT count(*) FROM personal_retail_model_grants "
                "WHERE call_quota IS NOT NULL OR concurrency_limit IS NOT NULL"
            )
        )
        or 0
    )
    if configured:
        raise RuntimeError(
            "0058 downgrade is blocked while personal usage limits exist"
        )
    with op.batch_alter_table("personal_retail_model_grants") as batch:
        batch.drop_constraint(
            "ck_personal_retail_concurrency_positive", type_="check"
        )
        batch.drop_constraint(
            "ck_personal_retail_call_quota_positive", type_="check"
        )
        batch.drop_column("concurrency_limit")
        batch.drop_column("call_quota")
