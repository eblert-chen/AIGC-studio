"""Add an explicit terminal outbox state for safe pre-submit cancellation.

Revision ID: 0030_generation_task_cancel
Revises: 0029_artifact_input_promotion
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0030_generation_task_cancel"
down_revision: str | None = "0029_artifact_input_promotion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


old_status = sa.Enum(
    "PENDING",
    "PROCESSING",
    "RETRY",
    "SENT",
    "RECONCILIATION_REQUIRED",
    "PERMANENTLY_FAILED",
    name="relayoutboxstatus",
    native_enum=False,
)
new_status = sa.Enum(
    "PENDING",
    "PROCESSING",
    "RETRY",
    "SENT",
    "RECONCILIATION_REQUIRED",
    "PERMANENTLY_FAILED",
    "CANCELLED",
    name="relayoutboxstatus",
    native_enum=False,
)


def upgrade() -> None:
    with op.batch_alter_table("relay_submission_outbox") as batch:
        batch.alter_column(
            "status",
            existing_type=old_status,
            type_=new_status,
            existing_nullable=False,
        )


def downgrade() -> None:
    op.execute(
        "UPDATE relay_submission_outbox "
        "SET status = 'PERMANENTLY_FAILED', "
        "last_error = COALESCE(last_error, 'cancelled before Relay submission') "
        "WHERE status = 'CANCELLED'"
    )
    with op.batch_alter_table("relay_submission_outbox") as batch:
        batch.alter_column(
            "status",
            existing_type=new_status,
            type_=old_status,
            existing_nullable=False,
        )
