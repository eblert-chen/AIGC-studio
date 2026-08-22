"""Add reliable relay submission outbox.

Revision ID: 0002_relay_submission_outbox
Revises: 0001_initial_platform
Create Date: 2026-07-29
"""

from datetime import datetime
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_relay_submission_outbox"
down_revision: str | None = "0001_initial_platform"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


relay_outbox_status = sa.Enum(
    "PENDING",
    "PROCESSING",
    "RETRY",
    "SENT",
    "PERMANENTLY_FAILED",
    name="relayoutboxstatus",
    native_enum=False,
)


def upgrade() -> None:
    with op.batch_alter_table("generation_tasks") as batch_op:
        batch_op.add_column(
            sa.Column("relay_job_id", sa.String(length=36), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_generation_tasks_relay_job_id", ["relay_job_id"]
        )

    op.create_table(
        "relay_submission_outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("status", relay_outbox_status, nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("relay_payload", sa.JSON(), nullable=False),
        sa.Column("relay_job_id", sa.String(length=36), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_relay_attempt_nonnegative"
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["generation_tasks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", name="uq_relay_outbox_task"),
    )
    op.create_index(
        "ix_relay_submission_outbox_company_id",
        "relay_submission_outbox",
        ["company_id"],
    )
    op.create_index(
        "ix_relay_outbox_dispatch",
        "relay_submission_outbox",
        ["status", "next_attempt_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_relay_outbox_dispatch", table_name="relay_submission_outbox"
    )
    op.drop_index(
        "ix_relay_submission_outbox_company_id",
        table_name="relay_submission_outbox",
    )
    op.drop_table("relay_submission_outbox")
    with op.batch_alter_table("generation_tasks") as batch_op:
        batch_op.drop_constraint(
            "uq_generation_tasks_relay_job_id", type_="unique"
        )
        batch_op.drop_column("relay_job_id")
