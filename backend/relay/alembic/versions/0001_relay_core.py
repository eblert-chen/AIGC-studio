"""Create generation jobs, idempotency, webhook events, and outbox.

Revision ID: 0001_relay_core
Revises:
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_relay_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("client_reference_id", sa.String(128)),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("inputs_json", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(128)),
        sa.Column("provider_task_id", sa.String(256)),
        sa.Column("outputs_json", sa.JSON(), nullable=False),
        sa.Column("error_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_generation_jobs_tenant_id", "generation_jobs", ["tenant_id"]
    )
    op.create_index("ix_generation_jobs_status", "generation_jobs", ["status"])
    op.create_index(
        "ix_generation_jobs_provider", "generation_jobs", ["provider"]
    )
    op.create_index(
        "ix_generation_jobs_provider_task_id",
        "generation_jobs",
        ["provider_task_id"],
    )
    op.create_index(
        "ix_generation_jobs_provider_task",
        "generation_jobs",
        ["provider", "provider_task_id"],
    )

    op.create_table(
        "generation_idempotency",
        sa.Column("tenant_id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("generation_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "provider_webhook_events",
        sa.Column("provider", sa.String(128), primary_key=True),
        sa.Column("event_id", sa.String(256), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "relay_outbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("topic", sa.String(128), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_relay_outbox_topic", "relay_outbox", ["topic"])
    op.create_index("ix_relay_outbox_status", "relay_outbox", ["status"])
    op.create_index(
        "ix_relay_outbox_available_at", "relay_outbox", ["available_at"]
    )


def downgrade() -> None:
    op.drop_table("relay_outbox")
    op.drop_table("provider_webhook_events")
    op.drop_table("generation_idempotency")
    op.drop_table("generation_jobs")

