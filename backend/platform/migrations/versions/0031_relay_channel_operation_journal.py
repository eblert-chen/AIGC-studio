"""Add the Platform-owned Relay channel operation journal.

Revision ID: 0031_relay_channel_journal
Revises: 0030_generation_task_cancel
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0031_relay_channel_journal"
down_revision: str | None = "0030_generation_task_cancel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "relay_channel_operations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("operation_id", sa.String(length=128), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.String(length=240), nullable=False),
        sa.Column("expected_revision", sa.String(length=72), nullable=True),
        sa.Column("target_status", sa.String(length=24), nullable=True),
        sa.Column("intent_sha256", sa.String(length=64), nullable=False),
        sa.Column("intent_payload", sa.JSON(), nullable=False),
        sa.Column("before_summary", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("approval_audit_id", sa.String(length=36), nullable=False),
        sa.Column("result_audit_id", sa.String(length=36), nullable=True),
        sa.Column("relay_intent_sha256", sa.String(length=64), nullable=True),
        sa.Column("relay_receipt", sa.JSON(), nullable=True),
        sa.Column("approval_request_id", sa.String(length=80), nullable=False),
        sa.Column("result_request_id", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('test', 'status')",
            name="ck_relay_channel_operation_kind",
        ),
        sa.CheckConstraint(
            "state IN ('approved', 'completed')",
            name="ck_relay_channel_operation_state",
        ),
        sa.CheckConstraint(
            "(kind = 'test' AND expected_revision IS NULL AND target_status IS NULL) "
            "OR (kind = 'status' AND expected_revision IS NOT NULL "
            "AND target_status IN ('enabled', 'manually_disabled'))",
            name="ck_relay_channel_operation_intent_shape",
        ),
        sa.CheckConstraint(
            "length(intent_sha256) = 64 AND lower(intent_sha256) = intent_sha256",
            name="ck_relay_channel_operation_intent_sha256",
        ),
        sa.CheckConstraint(
            "relay_intent_sha256 IS NULL OR "
            "(length(relay_intent_sha256) = 64 "
            "AND lower(relay_intent_sha256) = relay_intent_sha256)",
            name="ck_relay_channel_operation_relay_sha256",
        ),
        sa.CheckConstraint(
            "(state = 'approved' AND result_audit_id IS NULL "
            "AND completed_at IS NULL) OR "
            "(state = 'completed' AND result_audit_id IS NOT NULL "
            "AND relay_receipt IS NOT NULL AND relay_intent_sha256 IS NOT NULL "
            "AND completed_at IS NOT NULL)",
            name="ck_relay_channel_operation_completion_shape",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["approval_audit_id"], ["audit_logs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["result_audit_id"], ["audit_logs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "operation_id",
            name="uq_relay_channel_operation_tenant_operation",
        ),
        sa.UniqueConstraint(
            "approval_audit_id", name="uq_relay_channel_operation_approval_audit"
        ),
        sa.UniqueConstraint(
            "result_audit_id", name="uq_relay_channel_operation_result_audit"
        ),
    )
    op.create_index(
        "ix_relay_channel_operation_channel_created",
        "relay_channel_operations",
        ["channel_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_relay_channel_operation_channel_created",
        table_name="relay_channel_operations",
    )
    op.drop_table("relay_channel_operations")
