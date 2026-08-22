"""Fence concurrent provider polling attempts.

Revision ID: 0009_provider_poll_claim
Revises: 0008_callback_claim_fencing
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_provider_poll_claim"
down_revision = "0008_callback_claim_fencing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_jobs",
        sa.Column("provider_poll_claim_token", sa.String(36), nullable=True),
    )
    op.add_column(
        "generation_jobs",
        sa.Column(
            "provider_poll_claim_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_generation_jobs_provider_poll_claim_expires_at",
        "generation_jobs",
        ["provider_poll_claim_expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_generation_jobs_provider_poll_claim_expires_at",
        table_name="generation_jobs",
    )
    op.drop_column("generation_jobs", "provider_poll_claim_expires_at")
    op.drop_column("generation_jobs", "provider_poll_claim_token")
