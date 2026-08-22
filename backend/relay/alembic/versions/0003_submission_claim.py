"""Add durable provider submission claims.

Revision ID: 0003_submission_claim
Revises: 0002_artifact_transfer
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_submission_claim"
down_revision = "0002_artifact_transfer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_jobs",
        sa.Column("submission_claim_token", sa.String(36), nullable=True),
    )
    op.add_column(
        "generation_jobs",
        sa.Column(
            "submission_claim_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("generation_jobs", "submission_claim_expires_at")
    op.drop_column("generation_jobs", "submission_claim_token")
