"""Add one-time publisher OAuth sessions.

Revision ID: 0032_publisher_oauth
Revises: 0031_relay_channel_journal
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0032_publisher_oauth"
down_revision: str | None = "0031_relay_channel_journal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "publisher_oauth_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("state_sha256", sa.String(length=64), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "length(state_sha256) = 64 AND state_sha256 = lower(state_sha256)",
            name="ck_publisher_oauth_session_state_sha256",
        ),
        sa.CheckConstraint(
            "length(provider) > 0 AND provider = lower(provider)",
            name="ck_publisher_oauth_session_provider_normalized",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "state_sha256", name="uq_publisher_oauth_session_state_sha256"
        ),
    )
    op.create_index(
        "ix_publisher_oauth_sessions_company_id",
        "publisher_oauth_sessions",
        ["company_id"],
    )
    op.create_index(
        "ix_publisher_oauth_sessions_created_by_user_id",
        "publisher_oauth_sessions",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_publisher_oauth_session_company_created",
        "publisher_oauth_sessions",
        ["company_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_publisher_oauth_session_company_created",
        table_name="publisher_oauth_sessions",
    )
    op.drop_index(
        "ix_publisher_oauth_sessions_created_by_user_id",
        table_name="publisher_oauth_sessions",
    )
    op.drop_index(
        "ix_publisher_oauth_sessions_company_id",
        table_name="publisher_oauth_sessions",
    )
    op.drop_table("publisher_oauth_sessions")
