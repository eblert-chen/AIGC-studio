"""Add quota, concurrency and activation policy to company grants.

Revision ID: 0025_entitlement_policy
Revises: 0024_platform_admin_access
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0025_entitlement_policy"
down_revision: str | None = "0024_platform_admin_access"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_policy_columns(table_name: str, *, prefix: str) -> None:
    with op.batch_alter_table(table_name) as batch:
        batch.add_column(sa.Column("call_quota", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("concurrency_limit", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_check_constraint(
            f"ck_{prefix}_grant_call_quota_positive",
            "call_quota IS NULL OR call_quota > 0",
        )
        batch.create_check_constraint(
            f"ck_{prefix}_grant_concurrency_positive",
            "concurrency_limit IS NULL OR concurrency_limit > 0",
        )
        batch.create_check_constraint(
            f"ck_{prefix}_grant_schedule_order",
            "effective_at IS NULL OR expires_at IS NULL OR expires_at > effective_at",
        )
        batch.create_index(
            f"ix_company_{prefix}_grant_schedule",
            ["company_id", "enabled", "effective_at", "expires_at"],
            unique=False,
        )


def _drop_policy_columns(table_name: str, *, prefix: str) -> None:
    with op.batch_alter_table(table_name) as batch:
        batch.drop_index(f"ix_company_{prefix}_grant_schedule")
        batch.drop_constraint(
            f"ck_{prefix}_grant_schedule_order", type_="check"
        )
        batch.drop_constraint(
            f"ck_{prefix}_grant_concurrency_positive", type_="check"
        )
        batch.drop_constraint(
            f"ck_{prefix}_grant_call_quota_positive", type_="check"
        )
        batch.drop_column("expires_at")
        batch.drop_column("effective_at")
        batch.drop_column("concurrency_limit")
        batch.drop_column("call_quota")


def upgrade() -> None:
    _add_policy_columns("company_model_grants", prefix="model")
    _add_policy_columns("company_resource_grants", prefix="resource")


def downgrade() -> None:
    _drop_policy_columns("company_resource_grants", prefix="resource")
    _drop_policy_columns("company_model_grants", prefix="model")
