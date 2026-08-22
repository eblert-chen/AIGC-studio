"""Add immutable provider document evidence to channel costs.

Revision ID: 0027_channel_cost_evidence
Revises: 0026_relay_telemetry
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0027_channel_cost_evidence"
down_revision: str | None = "0026_relay_telemetry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # These nullable columns preserve historical operator/provider-reported
    # rows. The Relay ingest service requires both document fields for invoice
    # and contract-rate evidence before inserting any new immutable row.
    op.add_column(
        "channel_cost_entries",
        sa.Column("evidence_source", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "channel_cost_entries",
        sa.Column("evidence_reference", sa.String(length=240), nullable=True),
    )
    op.add_column(
        "channel_cost_entries",
        sa.Column("source_document_sha256", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("channel_cost_entries", "source_document_sha256")
    op.drop_column("channel_cost_entries", "evidence_reference")
    op.drop_column("channel_cost_entries", "evidence_source")
