"""Add immutable Relay callback event receipts.

Revision ID: 0010_relay_callback_events
Revises: 0009_input_assets
Create Date: 2026-08-03
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0010_relay_callback_events"
down_revision: str | None = "0009_input_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "relay_callback_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("relay_job_id", sa.String(length=36), nullable=False),
        sa.Column("relay_status", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=80), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["generation_tasks.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_relay_callback_event_task_received",
        "relay_callback_events",
        ["task_id", "received_at"],
    )
    op.create_index(
        "ix_relay_callback_event_company_received",
        "relay_callback_events",
        ["company_id", "received_at"],
    )
    op.create_index(
        "ix_relay_callback_event_status_received",
        "relay_callback_events",
        ["relay_status", "received_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_relay_callback_event_status_received",
        table_name="relay_callback_events",
    )
    op.drop_index(
        "ix_relay_callback_event_company_received",
        table_name="relay_callback_events",
    )
    op.drop_index(
        "ix_relay_callback_event_task_received",
        table_name="relay_callback_events",
    )
    op.drop_table("relay_callback_events")
