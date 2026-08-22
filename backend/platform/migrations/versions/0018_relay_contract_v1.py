"""Persist the provider-neutral Relay error snapshot on platform tasks.

Revision ID: 0018_relay_contract
Revises: 0017_relay_cap_sync
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0018_relay_contract"
down_revision: str | None = "0017_relay_cap_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generation_tasks",
        sa.Column("relay_error_snapshot", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generation_tasks", "relay_error_snapshot")
