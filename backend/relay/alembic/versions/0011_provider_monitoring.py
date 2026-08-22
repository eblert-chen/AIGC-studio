"""Add provider health, terminal outcomes and durable alerting.

Revision ID: 0011_provider_monitoring
Revises: 0010_provider_account_pool
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_provider_monitoring"
down_revision = "0010_provider_account_pool"
branch_labels = None
depends_on = None


def _create_outcome_immutability_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_provider_outcome_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'provider outcome events are immutable';
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_provider_outcome_events_immutable
            BEFORE UPDATE OR DELETE ON provider_outcome_events
            FOR EACH ROW EXECUTE FUNCTION reject_provider_outcome_mutation()
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_provider_outcome_events_no_truncate
            BEFORE TRUNCATE ON provider_outcome_events
            FOR EACH STATEMENT EXECUTE FUNCTION reject_provider_outcome_mutation()
            """
        )
    elif dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_provider_outcome_events_no_update
            BEFORE UPDATE ON provider_outcome_events
            BEGIN
                SELECT RAISE(ABORT, 'provider outcome events are immutable');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_provider_outcome_events_no_delete
            BEFORE DELETE ON provider_outcome_events
            BEGIN
                SELECT RAISE(ABORT, 'provider outcome events are immutable');
            END
            """
        )


def _drop_outcome_immutability_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_provider_outcome_events_no_truncate "
            "ON provider_outcome_events"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_provider_outcome_events_immutable "
            "ON provider_outcome_events"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_provider_outcome_mutation()")
    elif dialect == "sqlite":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_provider_outcome_events_no_delete"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_provider_outcome_events_no_update"
        )


def upgrade() -> None:
    op.add_column(
        "provider_account_states",
        sa.Column("admission_disabled_reason", sa.String(32), nullable=True),
    )
    account_states = sa.table(
        "provider_account_states",
        sa.column("admission_enabled", sa.Boolean()),
        sa.column("admission_disabled_reason", sa.String(32)),
    )
    # The pre-0011 schema cannot prove whether an existing disabled route was
    # manually drained or invalidated by the provider. Mark it unknown so the
    # new batch-invalid alarm never invents historical evidence.
    op.execute(
        account_states.update()
        .where(account_states.c.admission_enabled.is_(False))
        .values(admission_disabled_reason="legacy_unknown")
    )

    op.create_table(
        "provider_health_samples",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("route_id", sa.String(128), nullable=False),
        sa.Column("provider_name", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("channel_type", sa.String(32), nullable=False),
        sa.Column("healthy", sa.Boolean(), nullable=False),
        sa.Column("admission_enabled", sa.Boolean(), nullable=False),
        sa.Column("admission_disabled_reason", sa.String(32)),
        sa.Column("error_code", sa.String(128)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column(
            "checked_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_provider_health_latency_nonnegative",
        ),
    )
    op.create_index(
        "ix_provider_health_samples_route_id",
        "provider_health_samples",
        ["route_id"],
    )
    op.create_index(
        "ix_provider_health_samples_provider_name",
        "provider_health_samples",
        ["provider_name"],
    )
    op.create_index(
        "ix_provider_health_samples_checked_at",
        "provider_health_samples",
        ["checked_at"],
    )
    op.create_index(
        "ix_provider_health_provider_checked",
        "provider_health_samples",
        ["provider_name", "checked_at"],
    )

    op.create_table(
        "provider_outcome_events",
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("generation_jobs.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("route_id", sa.String(128), nullable=False),
        sa.Column("provider_name", sa.String(64), nullable=False),
        sa.Column("channel_type", sa.String(32), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False
        ),
    )
    op.create_index(
        "ix_provider_outcome_events_route_id",
        "provider_outcome_events",
        ["route_id"],
    )
    op.create_index(
        "ix_provider_outcome_events_provider_name",
        "provider_outcome_events",
        ["provider_name"],
    )
    op.create_index(
        "ix_provider_outcome_events_occurred_at",
        "provider_outcome_events",
        ["occurred_at"],
    )
    op.create_index(
        "ix_provider_outcome_provider_occurred",
        "provider_outcome_events",
        ["provider_name", "occurred_at"],
    )
    _create_outcome_immutability_guards()

    op.create_table(
        "provider_alert_states",
        sa.Column("fingerprint", sa.String(256), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("provider_name", sa.String(64), nullable=False),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "breach_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "recovery_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column(
            "last_observed_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.CheckConstraint(
            "breach_count >= 0",
            name="ck_provider_alert_breach_nonnegative",
        ),
        sa.CheckConstraint(
            "recovery_count >= 0",
            name="ck_provider_alert_recovery_nonnegative",
        ),
    )
    op.create_index(
        "ix_provider_alert_states_provider_name",
        "provider_alert_states",
        ["provider_name"],
    )

    op.create_table(
        "provider_alert_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("fingerprint", sa.String(256), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("provider_name", sa.String(64), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "delivery_status",
            sa.String(32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("claim_token", sa.String(36)),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("response_status", sa.Integer()),
        sa.Column("last_error", sa.String(256)),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_provider_alert_attempts_nonnegative",
        ),
    )
    for name, columns in (
        ("ix_provider_alert_events_fingerprint", ["fingerprint"]),
        ("ix_provider_alert_events_provider_name", ["provider_name"]),
        ("ix_provider_alert_events_occurred_at", ["occurred_at"]),
        ("ix_provider_alert_events_delivery_status", ["delivery_status"]),
        ("ix_provider_alert_events_available_at", ["available_at"]),
        ("ix_provider_alert_events_claim_expires_at", ["claim_expires_at"]),
    ):
        op.create_index(name, "provider_alert_events", columns)

    op.create_table(
        "provider_monitor_lease",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("worker_id", sa.String(128)),
        sa.Column("claim_token", sa.String(36)),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
        sa.Column("next_cycle_at", sa.DateTime(timezone=True)),
        sa.Column("last_successful_cycle_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
    )
    op.create_index(
        "ix_provider_monitor_lease_claim_expires_at",
        "provider_monitor_lease",
        ["claim_expires_at"],
    )
    op.create_index(
        "ix_provider_monitor_lease_next_cycle_at",
        "provider_monitor_lease",
        ["next_cycle_at"],
    )
    op.create_index(
        "ix_provider_monitor_lease_last_successful_cycle_at",
        "provider_monitor_lease",
        ["last_successful_cycle_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_monitor_lease_last_successful_cycle_at",
        table_name="provider_monitor_lease",
    )
    op.drop_index(
        "ix_provider_monitor_lease_next_cycle_at",
        table_name="provider_monitor_lease",
    )
    op.drop_index(
        "ix_provider_monitor_lease_claim_expires_at",
        table_name="provider_monitor_lease",
    )
    op.drop_table("provider_monitor_lease")
    for name in (
        "ix_provider_alert_events_claim_expires_at",
        "ix_provider_alert_events_available_at",
        "ix_provider_alert_events_delivery_status",
        "ix_provider_alert_events_occurred_at",
        "ix_provider_alert_events_provider_name",
        "ix_provider_alert_events_fingerprint",
    ):
        op.drop_index(name, table_name="provider_alert_events")
    op.drop_table("provider_alert_events")
    op.drop_index(
        "ix_provider_alert_states_provider_name",
        table_name="provider_alert_states",
    )
    op.drop_table("provider_alert_states")
    _drop_outcome_immutability_guards()
    op.drop_index(
        "ix_provider_outcome_provider_occurred",
        table_name="provider_outcome_events",
    )
    op.drop_index(
        "ix_provider_outcome_events_occurred_at",
        table_name="provider_outcome_events",
    )
    op.drop_index(
        "ix_provider_outcome_events_provider_name",
        table_name="provider_outcome_events",
    )
    op.drop_index(
        "ix_provider_outcome_events_route_id",
        table_name="provider_outcome_events",
    )
    op.drop_table("provider_outcome_events")
    op.drop_index(
        "ix_provider_health_provider_checked",
        table_name="provider_health_samples",
    )
    op.drop_index(
        "ix_provider_health_samples_checked_at",
        table_name="provider_health_samples",
    )
    op.drop_index(
        "ix_provider_health_samples_provider_name",
        table_name="provider_health_samples",
    )
    op.drop_index(
        "ix_provider_health_samples_route_id",
        table_name="provider_health_samples",
    )
    op.drop_table("provider_health_samples")
    op.drop_column("provider_account_states", "admission_disabled_reason")
