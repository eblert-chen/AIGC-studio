"""Add durable, signed customer callback deliveries.

Revision ID: 0004_callback_delivery
Revises: 0003_submission_claim
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_callback_delivery"
down_revision = "0003_submission_claim"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_jobs",
        sa.Column("callback_url", sa.Text(), nullable=True),
    )
    op.create_table(
        "callback_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("generation_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("callback_url", sa.Text(), nullable=False),
        sa.Column("request_id", sa.String(80), nullable=False),
        sa.Column("event_json", sa.JSON(), nullable=False),
        sa.Column("job_status", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("response_status", sa.Integer()),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_callback_deliveries_tenant_id",
        "callback_deliveries",
        ["tenant_id"],
    )
    op.create_index(
        "ix_callback_deliveries_job_id",
        "callback_deliveries",
        ["job_id"],
    )
    op.create_index(
        "ix_callback_deliveries_status",
        "callback_deliveries",
        ["status"],
    )
    op.create_index(
        "ix_callback_deliveries_available_at",
        "callback_deliveries",
        ["available_at"],
    )


def downgrade() -> None:
    op.drop_table("callback_deliveries")
    op.drop_column("generation_jobs", "callback_url")
