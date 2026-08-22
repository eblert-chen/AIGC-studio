"""Add immutable Relay task-stage and operations telemetry.

Revision ID: 0026_relay_telemetry
Revises: 0025_entitlement_policy
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0026_relay_telemetry"
down_revision: str | None = "0025_entitlement_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _immutable_triggers(table_names: tuple[str, ...]) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION reject_relay_telemetry_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'relay telemetry is immutable';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        for table_name in table_names:
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_immutable "
                f"BEFORE UPDATE OR DELETE ON {table_name} FOR EACH ROW "
                "EXECUTE FUNCTION reject_relay_telemetry_mutation()"
            )
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_no_truncate "
                f"BEFORE TRUNCATE ON {table_name} FOR EACH STATEMENT "
                "EXECUTE FUNCTION reject_relay_telemetry_mutation()"
            )
    elif dialect == "sqlite":
        for table_name in table_names:
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_no_update "
                f"BEFORE UPDATE ON {table_name} BEGIN "
                "SELECT RAISE(ABORT, 'relay telemetry is immutable'); END"
            )
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_no_delete "
                f"BEFORE DELETE ON {table_name} BEGIN "
                "SELECT RAISE(ABORT, 'relay telemetry is immutable'); END"
            )


def _drop_immutable_triggers(table_names: tuple[str, ...]) -> None:
    dialect = op.get_bind().dialect.name
    for table_name in table_names:
        if dialect == "postgresql":
            op.execute(
                f"DROP TRIGGER IF EXISTS trg_{table_name}_no_truncate "
                f"ON {table_name}"
            )
            op.execute(
                f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable "
                f"ON {table_name}"
            )
        elif dialect == "sqlite":
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_no_update")
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_no_delete")
    if dialect == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS reject_relay_telemetry_mutation()")


def upgrade() -> None:
    op.create_table(
        "relay_task_stage_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("relay_job_id", sa.String(length=36), nullable=False),
        sa.Column(
            "stage",
            sa.Enum(
                "queued",
                "submitting",
                "submission_unknown",
                "provider_processing",
                "artifact_transferring",
                "artifact_stored",
                "failed",
                "cancelled",
                name="relaytaskstage",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel_key", sa.String(length=120), nullable=False),
        sa.Column(
            "channel_type",
            sa.Enum(
                "reverse",
                "third_party_api",
                "official",
                name="channeltype",
                native_enum=False,
            ),
            nullable=True,
        ),
        sa.Column("route_id", sa.BigInteger(), nullable=True),
        sa.Column("provider_task_id", sa.String(length=191), nullable=False),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.String(length=160), nullable=False),
        sa.Column("delivery_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=80), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("schema_version = 1", name="ck_relay_task_stage_schema_v1"),
        sa.CheckConstraint(
            "duration_ms IS NULL OR (duration_ms >= 0 AND "
            "duration_ms <= 9223372036854775807)",
            name="ck_relay_task_stage_duration_range",
        ),
        sa.CheckConstraint(
            "route_id IS NULL OR route_id > 0",
            name="ck_relay_task_stage_route_positive",
        ),
        sa.CheckConstraint(
            "(channel_key = '' AND channel_type IS NULL) OR "
            "(channel_key <> '' AND channel_type IS NOT NULL)",
            name="ck_relay_task_stage_channel_binding",
        ),
        sa.CheckConstraint(
            "length(payload_sha256) = 64 AND lower(payload_sha256) = payload_sha256",
            name="ck_relay_task_stage_payload_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["generation_tasks.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_relay_task_stage_task_occurred",
        "relay_task_stage_events",
        ["task_id", "occurred_at"],
    )
    op.create_index(
        "ix_relay_task_stage_company_occurred",
        "relay_task_stage_events",
        ["company_id", "occurred_at"],
    )
    op.create_index(
        "ix_relay_task_stage_stage_occurred",
        "relay_task_stage_events",
        ["stage", "occurred_at"],
    )
    op.create_index(
        "ix_relay_task_stage_events_company_id",
        "relay_task_stage_events",
        ["company_id"],
    )
    op.create_index(
        "ix_relay_task_stage_events_task_id",
        "relay_task_stage_events",
        ["task_id"],
    )
    op.create_index(
        "ix_relay_task_stage_events_relay_job_id",
        "relay_task_stage_events",
        ["relay_job_id"],
    )

    op.create_table(
        "relay_operations_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("monitor_fresh", sa.Boolean(), nullable=False),
        sa.Column(
            "monitor_last_completed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("account_total", sa.BigInteger(), nullable=False),
        sa.Column("account_active", sa.BigInteger(), nullable=False),
        sa.Column("account_cooling", sa.BigInteger(), nullable=False),
        sa.Column("account_invalid", sa.BigInteger(), nullable=False),
        sa.Column("account_busy", sa.BigInteger(), nullable=False),
        sa.Column("account_rate_limited", sa.BigInteger(), nullable=False),
        sa.Column("account_active_tasks", sa.BigInteger(), nullable=False),
        sa.Column("account_task_capacity", sa.BigInteger(), nullable=False),
        sa.Column("task_queued", sa.BigInteger(), nullable=False),
        sa.Column("task_submitting", sa.BigInteger(), nullable=False),
        sa.Column("task_submission_unknown", sa.BigInteger(), nullable=False),
        sa.Column("task_provider_processing", sa.BigInteger(), nullable=False),
        sa.Column("task_artifact_transferring", sa.BigInteger(), nullable=False),
        sa.Column("task_succeeded", sa.BigInteger(), nullable=False),
        sa.Column("task_failed", sa.BigInteger(), nullable=False),
        sa.Column("task_cancelled", sa.BigInteger(), nullable=False),
        sa.Column("task_rate_limited_count", sa.BigInteger(), nullable=False),
        sa.Column("task_failover_count", sa.BigInteger(), nullable=False),
        sa.Column("delivery_pending_alert_count", sa.BigInteger(), nullable=False),
        sa.Column("delivery_dead_alert_count", sa.BigInteger(), nullable=False),
        sa.Column(
            "delivery_oldest_pending_alert_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("delivery_pending_cost_count", sa.BigInteger(), nullable=False),
        sa.Column("delivery_dead_cost_count", sa.BigInteger(), nullable=False),
        sa.Column(
            "delivery_pending_task_stage_count", sa.BigInteger(), nullable=False
        ),
        sa.Column("delivery_dead_task_stage_count", sa.BigInteger(), nullable=False),
        sa.Column("delivery_pending_snapshot_count", sa.BigInteger(), nullable=False),
        sa.Column("delivery_dead_snapshot_count", sa.BigInteger(), nullable=False),
        sa.Column("cost_successful_jobs", sa.BigInteger(), nullable=False),
        sa.Column("cost_explicit_jobs", sa.BigInteger(), nullable=False),
        sa.Column("cost_delivered_jobs", sa.BigInteger(), nullable=False),
        sa.Column("cost_incomplete_jobs", sa.BigInteger(), nullable=False),
        sa.Column(
            "cost_native_reconciliation_jobs", sa.BigInteger(), nullable=False
        ),
        sa.Column("cost_reconciliation_complete", sa.Boolean(), nullable=False),
        sa.Column("delivery_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=80), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("schema_version = 1", name="ck_relay_operations_schema_v1"),
        sa.CheckConstraint(
            "window_started_at < observed_at AND expires_at > observed_at",
            name="ck_relay_operations_time_order",
        ),
        sa.CheckConstraint(
            "monitor_last_completed_at IS NULL OR "
            "monitor_last_completed_at <= observed_at",
            name="ck_relay_operations_monitor_time",
        ),
        sa.CheckConstraint(
            "account_total >= 0 AND account_active >= 0 AND account_cooling >= 0 "
            "AND account_invalid >= 0 AND account_busy >= 0 AND "
            "account_rate_limited >= 0 AND account_active_tasks >= 0 AND "
            "account_task_capacity >= 0",
            name="ck_relay_operations_account_counts",
        ),
        sa.CheckConstraint(
            "task_queued >= 0 AND task_submitting >= 0 AND "
            "task_submission_unknown >= 0 AND task_provider_processing >= 0 AND "
            "task_artifact_transferring >= 0 AND task_succeeded >= 0 AND "
            "task_failed >= 0 AND task_cancelled >= 0 AND "
            "task_rate_limited_count >= 0 AND task_failover_count >= 0",
            name="ck_relay_operations_task_counts",
        ),
        sa.CheckConstraint(
            "delivery_pending_alert_count >= 0 AND delivery_dead_alert_count >= 0 "
            "AND delivery_pending_cost_count >= 0 AND delivery_dead_cost_count >= 0 "
            "AND delivery_pending_task_stage_count >= 0 AND "
            "delivery_dead_task_stage_count >= 0 AND "
            "delivery_pending_snapshot_count >= 0 AND "
            "delivery_dead_snapshot_count >= 0",
            name="ck_relay_operations_delivery_counts",
        ),
        sa.CheckConstraint(
            "cost_successful_jobs >= 0 AND cost_explicit_jobs >= 0 AND "
            "cost_delivered_jobs >= 0 AND cost_incomplete_jobs >= 0 AND "
            "cost_native_reconciliation_jobs >= 0",
            name="ck_relay_operations_cost_counts",
        ),
        sa.CheckConstraint(
            "length(payload_sha256) = 64 AND lower(payload_sha256) = payload_sha256",
            name="ck_relay_operations_payload_sha256",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_relay_operations_snapshot_observed",
        "relay_operations_snapshots",
        ["observed_at", "id"],
    )
    op.create_index(
        "ix_relay_operations_snapshot_expiry",
        "relay_operations_snapshots",
        ["expires_at", "observed_at"],
    )

    op.create_table(
        "relay_route_operations_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("route_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_key", sa.String(length=120), nullable=False),
        sa.Column(
            "channel_type",
            sa.Enum(
                "reverse",
                "third_party_api",
                "official",
                name="channeltype",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("provider_name", sa.String(length=120), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("mode", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("production_ready", sa.Boolean(), nullable=False),
        sa.Column("health_status", sa.String(length=24), nullable=False),
        sa.Column("failure_code", sa.String(length=160), nullable=False),
        sa.Column("last_probe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rpm_limit", sa.BigInteger(), nullable=False),
        sa.Column("rpm_used", sa.BigInteger(), nullable=False),
        sa.Column("active_task_count", sa.BigInteger(), nullable=False),
        sa.Column("task_capacity", sa.BigInteger(), nullable=False),
        sa.Column("cooling_account_count", sa.BigInteger(), nullable=False),
        sa.Column("invalid_account_count", sa.BigInteger(), nullable=False),
        sa.Column("busy_account_count", sa.BigInteger(), nullable=False),
        sa.Column("rate_limited_account_count", sa.BigInteger(), nullable=False),
        sa.Column("successful_task_count", sa.BigInteger(), nullable=False),
        sa.Column("failed_task_count", sa.BigInteger(), nullable=False),
        sa.Column("latency_p50_ms", sa.BigInteger(), nullable=True),
        sa.Column("latency_p95_ms", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("route_id > 0", name="ck_relay_route_id_positive"),
        sa.CheckConstraint(
            "health_status IN ('unknown', 'healthy', 'failed', 'invalidated', "
            "'cooling', 'disabled')",
            name="ck_relay_route_health_status",
        ),
        sa.CheckConstraint(
            "rpm_limit >= 0 AND rpm_used >= 0 AND active_task_count >= 0 AND "
            "task_capacity >= 0 AND cooling_account_count >= 0 AND "
            "invalid_account_count >= 0 AND busy_account_count >= 0 AND "
            "rate_limited_account_count >= 0 AND successful_task_count >= 0 "
            "AND failed_task_count >= 0",
            name="ck_relay_route_metric_counts",
        ),
        sa.CheckConstraint(
            "latency_p50_ms IS NULL OR latency_p50_ms >= 0",
            name="ck_relay_route_latency_p50",
        ),
        sa.CheckConstraint(
            "latency_p95_ms IS NULL OR latency_p95_ms >= 0",
            name="ck_relay_route_latency_p95",
        ),
        sa.CheckConstraint(
            "latency_p50_ms IS NULL OR latency_p95_ms IS NULL OR "
            "latency_p95_ms >= latency_p50_ms",
            name="ck_relay_route_latency_order",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["relay_operations_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id", "route_id", name="uq_relay_route_snapshot"
        ),
    )
    op.create_index(
        "ix_relay_route_operations_snapshots_snapshot_id",
        "relay_route_operations_snapshots",
        ["snapshot_id"],
    )
    op.create_index(
        "ix_relay_route_operations_channel_snapshot",
        "relay_route_operations_snapshots",
        ["channel_key", "snapshot_id"],
    )

    _immutable_triggers(
        (
            "relay_task_stage_events",
            "relay_operations_snapshots",
            "relay_route_operations_snapshots",
        )
    )


def downgrade() -> None:
    table_names = (
        "relay_task_stage_events",
        "relay_operations_snapshots",
        "relay_route_operations_snapshots",
    )
    _drop_immutable_triggers(table_names)
    op.drop_index(
        "ix_relay_route_operations_channel_snapshot",
        table_name="relay_route_operations_snapshots",
    )
    op.drop_index(
        "ix_relay_route_operations_snapshots_snapshot_id",
        table_name="relay_route_operations_snapshots",
    )
    op.drop_table("relay_route_operations_snapshots")
    op.drop_index(
        "ix_relay_operations_snapshot_expiry",
        table_name="relay_operations_snapshots",
    )
    op.drop_index(
        "ix_relay_operations_snapshot_observed",
        table_name="relay_operations_snapshots",
    )
    op.drop_table("relay_operations_snapshots")
    op.drop_index(
        "ix_relay_task_stage_events_relay_job_id",
        table_name="relay_task_stage_events",
    )
    op.drop_index(
        "ix_relay_task_stage_events_task_id",
        table_name="relay_task_stage_events",
    )
    op.drop_index(
        "ix_relay_task_stage_events_company_id",
        table_name="relay_task_stage_events",
    )
    op.drop_index(
        "ix_relay_task_stage_stage_occurred",
        table_name="relay_task_stage_events",
    )
    op.drop_index(
        "ix_relay_task_stage_company_occurred",
        table_name="relay_task_stage_events",
    )
    op.drop_index(
        "ix_relay_task_stage_task_occurred",
        table_name="relay_task_stage_events",
    )
    op.drop_table("relay_task_stage_events")
