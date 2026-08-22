"""Persist safe relay artifact metadata on platform tasks.

Revision ID: 0004_task_artifacts
Revises: 0003_platform_admin
Create Date: 2026-07-29
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004_task_artifacts"
down_revision: str | None = "0003_platform_admin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("generation_tasks") as batch_op:
        batch_op.add_column(
            sa.Column(
                "output_artifacts",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("generation_tasks") as batch_op:
        batch_op.drop_column("output_artifacts")
