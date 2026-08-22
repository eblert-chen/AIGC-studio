"""Persist the authenticated Relay caller identity for audit and isolation.

Revision ID: 0006_source_client_identity
Revises: 0005_provider_polling
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_source_client_identity"
down_revision = "0005_provider_polling"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_jobs",
        sa.Column("source_client_id", sa.String(128), nullable=True),
    )
    op.create_index(
        "ix_generation_jobs_source_client_id",
        "generation_jobs",
        ["source_client_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_generation_jobs_source_client_id",
        table_name="generation_jobs",
    )
    op.drop_column("generation_jobs", "source_client_id")
