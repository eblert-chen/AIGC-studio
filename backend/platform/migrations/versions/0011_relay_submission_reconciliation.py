"""Persist one Relay request and quarantine ambiguous submissions.

Revision ID: 0011_relay_submit_reconcile
Revises: 0010_relay_callback_events
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0011_relay_submit_reconcile"
down_revision: str | None = "0010_relay_callback_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


old_status = sa.Enum(
    "PENDING",
    "PROCESSING",
    "RETRY",
    "SENT",
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
    name="relayoutboxstatus",
    native_enum=False,
)


def upgrade() -> None:
    with op.batch_alter_table("relay_submission_outbox") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=old_status,
            type_=new_status,
            existing_nullable=False,
        )
        batch_op.add_column(
            sa.Column("materialized_relay_payload", sa.JSON(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "relay_submit_attempted_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "submission_outcome_uncertain_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )


def downgrade() -> None:
    op.execute(
        "UPDATE relay_submission_outbox SET status = 'RETRY' "
        "WHERE status = 'RECONCILIATION_REQUIRED'"
    )
    with op.batch_alter_table("relay_submission_outbox") as batch_op:
        batch_op.drop_column("submission_outcome_uncertain_at")
        batch_op.drop_column("relay_submit_attempted_at")
        batch_op.drop_column("materialized_relay_payload")
        batch_op.alter_column(
            "status",
            existing_type=new_status,
            type_=old_status,
            existing_nullable=False,
        )
