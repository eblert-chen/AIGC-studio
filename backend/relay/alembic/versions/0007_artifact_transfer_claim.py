"""Add a durable artifact-transfer claim fence.

Revision ID: 0007_artifact_transfer_claim
Revises: 0006_source_client_identity
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_artifact_transfer_claim"
down_revision = "0006_source_client_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_jobs",
        sa.Column("transfer_claim_token", sa.String(36), nullable=True),
    )
    op.add_column(
        "generation_jobs",
        sa.Column(
            "transfer_claim_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("generation_jobs", "transfer_claim_expires_at")
    op.drop_column("generation_jobs", "transfer_claim_token")
