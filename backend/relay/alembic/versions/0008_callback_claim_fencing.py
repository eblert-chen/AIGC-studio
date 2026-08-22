"""Fence concurrent callback delivery attempts.

Revision ID: 0008_callback_claim_fencing
Revises: 0007_artifact_transfer_claim
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_callback_claim_fencing"
down_revision = "0007_artifact_transfer_claim"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "callback_deliveries",
        sa.Column("claim_token", sa.String(36), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("callback_deliveries", "claim_token")
