"""Add durable provider account admission and scheduling state.

Revision ID: 0010_provider_account_pool
Revises: 0009_provider_poll_claim
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_provider_account_pool"
down_revision = "0009_provider_poll_claim"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_account_states",
        sa.Column("route_id", sa.String(128), primary_key=True),
        sa.Column("provider_name", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("channel_type", sa.String(32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=True),
        sa.Column("requests_per_minute", sa.Integer(), nullable=True),
        sa.Column(
            "admission_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("cooldown_until", sa.DateTime(timezone=True)),
        sa.Column("rate_window_started_at", sa.DateTime(timezone=True)),
        sa.Column(
            "rate_window_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "successful_submissions",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_acquired_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_failure_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(128)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.CheckConstraint(
            "priority >= 0", name="ck_provider_account_priority_nonnegative"
        ),
        sa.CheckConstraint(
            "max_concurrency IS NULL OR max_concurrency > 0",
            name="ck_provider_account_max_concurrency_positive",
        ),
        sa.CheckConstraint(
            "requests_per_minute IS NULL OR requests_per_minute > 0",
            name="ck_provider_account_rpm_positive",
        ),
        sa.CheckConstraint(
            "consecutive_failures >= 0",
            name="ck_provider_account_failures_nonnegative",
        ),
        sa.CheckConstraint(
            "rate_window_count >= 0",
            name="ck_provider_account_rate_count_nonnegative",
        ),
        sa.CheckConstraint(
            "successful_submissions >= 0",
            name="ck_provider_account_success_nonnegative",
        ),
    )
    op.create_index(
        "uq_provider_account_identity",
        "provider_account_states",
        ["provider_name", "account_id"],
        unique=True,
    )
    op.create_index(
        "ix_provider_account_states_cooldown_until",
        "provider_account_states",
        ["cooldown_until"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_account_states_cooldown_until",
        table_name="provider_account_states",
    )
    op.drop_index(
        "uq_provider_account_identity",
        table_name="provider_account_states",
    )
    op.drop_table("provider_account_states")
