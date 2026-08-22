"""Add persistent artifact transfer plans.

Revision ID: 0002_artifact_transfer
Revises: 0001_relay_core
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_artifact_transfer"
down_revision = "0001_relay_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_jobs",
        sa.Column(
            "transfer_sources_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("generation_jobs", "transfer_sources_json")

