"""Add durable provider polling backoff and task uniqueness.

Revision ID: 0005_provider_polling
Revises: 0004_callback_delivery
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_provider_polling"
down_revision = "0004_callback_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_jobs",
        sa.Column(
            "provider_poll_failures",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "generation_jobs",
        sa.Column(
            "provider_next_poll_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "generation_jobs",
        sa.Column(
            "provider_last_poll_error",
            sa.String(128),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_generation_jobs_provider_next_poll_at",
        "generation_jobs",
        ["provider_next_poll_at"],
    )
    op.drop_index(
        "ix_generation_jobs_provider_task",
        table_name="generation_jobs",
    )
    op.create_index(
        "ix_generation_jobs_provider_task",
        "generation_jobs",
        ["provider", "provider_task_id"],
        unique=True,
    )
    # SQLite cannot alter a column default in place. Keeping the defensive
    # default in local/dev SQLite is harmless; production PostgreSQL removes it
    # so all writes stay explicit in the repository model.
    if op.get_bind().dialect.name != "sqlite":
        op.alter_column(
            "generation_jobs",
            "provider_poll_failures",
            existing_type=sa.Integer(),
            server_default=None,
        )


def downgrade() -> None:
    op.drop_index(
        "ix_generation_jobs_provider_task",
        table_name="generation_jobs",
    )
    op.create_index(
        "ix_generation_jobs_provider_task",
        "generation_jobs",
        ["provider", "provider_task_id"],
        unique=False,
    )
    op.drop_index(
        "ix_generation_jobs_provider_next_poll_at",
        table_name="generation_jobs",
    )
    op.drop_column("generation_jobs", "provider_last_poll_error")
    op.drop_column("generation_jobs", "provider_next_poll_at")
    op.drop_column("generation_jobs", "provider_poll_failures")
