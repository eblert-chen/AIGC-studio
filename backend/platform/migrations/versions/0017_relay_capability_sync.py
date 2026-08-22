"""Track the Relay capability revision approved for each platform model.

Revision ID: 0017_relay_cap_sync
Revises: 0016_task_artifact_audit
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0017_relay_cap_sync"
down_revision: str | None = "0016_task_artifact_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_definitions",
        sa.Column("relay_capability_revision", sa.String(length=71), nullable=True),
    )
    op.add_column(
        "model_definitions",
        sa.Column(
            "relay_capability_synced_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("model_definitions", "relay_capability_synced_at")
    op.drop_column("model_definitions", "relay_capability_revision")
