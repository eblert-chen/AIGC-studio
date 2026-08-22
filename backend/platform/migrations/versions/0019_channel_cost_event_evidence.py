"""Persist signed Relay evidence on immutable channel cost entries.

Revision ID: 0019_channel_cost_evidence
Revises: 0018_relay_contract
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0019_channel_cost_evidence"
down_revision: str | None = "0018_relay_contract"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "channel_cost_entries",
        sa.Column(
            "relay_event_id",
            sa.String(length=36),
            sa.CheckConstraint(
                "relay_event_id IS NULL OR ("
                "length(relay_event_id) = 36 "
                "AND substr(relay_event_id, 9, 1) = '-' "
                "AND substr(relay_event_id, 14, 1) = '-' "
                "AND substr(relay_event_id, 19, 1) = '-' "
                "AND substr(relay_event_id, 24, 1) = '-' "
                "AND lower(relay_event_id) = relay_event_id "
                "AND replace(replace(replace(replace(replace(replace("
                "replace(replace(replace(replace(replace(replace(replace("
                "replace(replace(replace(replace(relay_event_id, '0', ''), "
                "'1', ''), '2', ''), '3', ''), '4', ''), '5', ''), "
                "'6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), "
                "'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', ''), "
                "'-', '') = '')",
                name="ck_channel_cost_relay_event_id_format",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "channel_cost_entries",
        sa.Column(
            "relay_event_timestamp",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "channel_cost_entries",
        sa.Column(
            "relay_payload_sha256",
            sa.String(length=64),
            sa.CheckConstraint(
                "(relay_event_id IS NULL "
                "AND relay_event_timestamp IS NULL "
                "AND relay_payload_sha256 IS NULL) "
                "OR (relay_event_id IS NOT NULL "
                "AND relay_event_timestamp IS NOT NULL "
                "AND relay_payload_sha256 IS NOT NULL)",
                name="ck_channel_cost_relay_evidence_complete",
            ),
            sa.CheckConstraint(
                "relay_payload_sha256 IS NULL OR ("
                "length(relay_payload_sha256) = 64 "
                "AND lower(relay_payload_sha256) = relay_payload_sha256 "
                "AND replace(replace(replace(replace(replace(replace("
                "replace(replace(replace(replace(replace(replace(replace("
                "replace(replace(replace(relay_payload_sha256, '0', ''), "
                "'1', ''), '2', ''), '3', ''), '4', ''), '5', ''), "
                "'6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), "
                "'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '') = '')",
                name="ck_channel_cost_relay_payload_sha256",
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_channel_cost_relay_event_id",
        "channel_cost_entries",
        ["relay_event_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_channel_cost_relay_event_id",
        table_name="channel_cost_entries",
    )
    op.drop_column("channel_cost_entries", "relay_payload_sha256")
    op.drop_column("channel_cost_entries", "relay_event_timestamp")
    op.drop_column("channel_cost_entries", "relay_event_id")
