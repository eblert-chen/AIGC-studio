"""Add immutable task timeout compensation events and scan index.

Revision ID: 0007_task_timeout_compensation
Revises: 0006_download_reports
Create Date: 2026-08-01
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0007_task_timeout_compensation"
down_revision: str | None = "0006_download_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("generation_tasks") as batch_op:
        batch_op.add_column(
            sa.Column(
                "timeout_checked_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
    op.create_index(
        "ix_generation_task_timeout_scan",
        "generation_tasks",
        ["status", "timeout_checked_at", "created_at"],
        unique=False,
    )
    op.create_table(
        "task_timeout_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("previous_status", sa.String(length=32), nullable=False),
        sa.Column("final_status", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=48), nullable=False),
        sa.Column("reason", sa.String(length=2000), nullable=False),
        sa.Column(
            "released_cents", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("ledger_entry_id", sa.String(length=36), nullable=True),
        sa.Column("relay_job_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "released_cents >= 0",
            name="ck_task_timeout_released_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["ledger_entry_id"], ["ledger_entries.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["generation_tasks.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ledger_entry_id"),
        sa.UniqueConstraint("task_id", name="uq_task_timeout_event_task"),
    )
    op.create_index(
        "ix_task_timeout_event_company_created",
        "task_timeout_events",
        ["company_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_task_timeout_event_created",
        "task_timeout_events",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_task_timeout_events_company_id"),
        "task_timeout_events",
        ["company_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_task_timeout_events_company_id"),
        table_name="task_timeout_events",
    )
    op.drop_index(
        "ix_task_timeout_event_created", table_name="task_timeout_events"
    )
    op.drop_index(
        "ix_task_timeout_event_company_created",
        table_name="task_timeout_events",
    )
    op.drop_table("task_timeout_events")
    op.drop_index(
        "ix_generation_task_timeout_scan", table_name="generation_tasks"
    )
    with op.batch_alter_table("generation_tasks") as batch_op:
        batch_op.drop_column("timeout_checked_at")
