"""Persist Operations audit outcomes and administrator activity evidence.

Revision ID: 0035_operations_evidence
Revises: 0034_personal_workspaces

Existing audit rows were only appended after completed mutation paths, so the
only honest historical backfill is ``SUCCEEDED``. Future callers can persist
``FAILED`` and ``UNKNOWN`` when they hold corresponding durable evidence.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0035_operations_evidence"
down_revision: str | None = "0034_personal_workspaces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


audit_outcome = sa.Enum(
    "SUCCEEDED",
    "FAILED",
    "UNKNOWN",
    name="auditoutcome",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "platform_admin_activity",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )
    # Keep a row for every currently authorized administrator while leaving
    # historical activity unknown. The first successful authorized request
    # atomically replaces NULL with a real timestamp.
    op.execute(
        sa.text(
            "INSERT INTO platform_admin_activity (user_id, last_active_at) "
            "SELECT id, NULL FROM users WHERE is_platform_admin = true"
        )
    )
    # A NOT NULL server default backfills without issuing UPDATE statements,
    # which preserves the already-installed immutable audit-log triggers.
    op.add_column(
        "audit_logs",
        sa.Column(
            "outcome",
            audit_outcome,
            nullable=False,
            server_default="SUCCEEDED",
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_column("outcome")
    op.drop_table("platform_admin_activity")
