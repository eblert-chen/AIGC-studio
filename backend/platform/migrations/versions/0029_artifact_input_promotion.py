"""Track idempotent task-artifact promotion into private input assets.

Revision ID: 0029_artifact_input_promotion
Revises: 0028_provider_alert_bridge
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0029_artifact_input_promotion"
down_revision: str | None = "0028_provider_alert_bridge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("input_assets") as batch:
        batch.add_column(
            sa.Column(
                "source_task_artifact_id",
                sa.String(length=36),
                nullable=True,
            )
        )
        batch.create_foreign_key(
            "fk_input_assets_source_task_artifact",
            "task_artifacts",
            ["source_task_artifact_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index(
            "ix_input_assets_source_task_artifact_id",
            ["source_task_artifact_id"],
            unique=False,
        )
        batch.create_check_constraint(
            "ck_input_asset_promotion_has_idempotency",
            "source_task_artifact_id IS NULL OR idempotency_key IS NOT NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("input_assets") as batch:
        batch.drop_constraint(
            "ck_input_asset_promotion_has_idempotency",
            type_="check",
        )
        batch.drop_index("ix_input_assets_source_task_artifact_id")
        batch.drop_constraint(
            "fk_input_assets_source_task_artifact",
            type_="foreignkey",
        )
        batch.drop_column("source_task_artifact_id")
