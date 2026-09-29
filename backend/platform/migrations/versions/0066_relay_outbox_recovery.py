"""Platform Relay reconciliation outbox auto-recovery support.

Adds recovery_attempt_count and next_recovery_at to relay_submission_outbox
so a dedicated worker can claim RECONCILIATION_REQUIRED rows, probe the
Relay job by id, and either bind a resolved terminal state, reset the outbox
back to RETRY (Relay 404), or mark PERMANENTLY_FAILED after exhausting
recovery retries.

Revision ID: 0066_relay_outbox_recovery
Revises: 0065_point_expiry
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0066_relay_outbox_recovery"
down_revision: str | None = "0065_point_expiry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("relay_submission_outbox") as batch:
        batch.add_column(
            sa.Column(
                "recovery_attempt_count",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column(
                "next_recovery_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
    op.create_index(
        "ix_relay_outbox_recovery",
        "relay_submission_outbox",
        ["status", "next_recovery_at", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_relay_outbox_recovery",
        table_name="relay_submission_outbox",
    )
    with op.batch_alter_table("relay_submission_outbox") as batch:
        batch.drop_column("next_recovery_at")
        batch.drop_column("recovery_attempt_count")
