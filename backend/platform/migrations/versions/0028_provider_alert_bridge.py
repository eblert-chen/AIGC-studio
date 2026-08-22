"""Add immutable signed provider-alert receipts.

Revision ID: 0028_provider_alert_bridge
Revises: 0027_channel_cost_evidence
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0028_provider_alert_bridge"
down_revision: str | None = "0027_channel_cost_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_immutable_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION reject_relay_provider_alert_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'relay provider alert events are immutable';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            "CREATE TRIGGER trg_relay_provider_alert_events_immutable "
            "BEFORE UPDATE OR DELETE ON relay_provider_alert_events "
            "FOR EACH ROW EXECUTE FUNCTION reject_relay_provider_alert_mutation()"
        )
        op.execute(
            "CREATE TRIGGER trg_relay_provider_alert_events_no_truncate "
            "BEFORE TRUNCATE ON relay_provider_alert_events FOR EACH STATEMENT "
            "EXECUTE FUNCTION reject_relay_provider_alert_mutation()"
        )
    elif dialect == "sqlite":
        op.execute(
            "CREATE TRIGGER trg_relay_provider_alert_events_no_update "
            "BEFORE UPDATE ON relay_provider_alert_events BEGIN "
            "SELECT RAISE(ABORT, 'relay provider alert events are immutable'); END"
        )
        op.execute(
            "CREATE TRIGGER trg_relay_provider_alert_events_no_delete "
            "BEFORE DELETE ON relay_provider_alert_events BEGIN "
            "SELECT RAISE(ABORT, 'relay provider alert events are immutable'); END"
        )


def _drop_immutable_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_relay_provider_alert_events_no_truncate "
            "ON relay_provider_alert_events"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_relay_provider_alert_events_immutable "
            "ON relay_provider_alert_events"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS reject_relay_provider_alert_mutation()"
        )
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_relay_provider_alert_events_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_relay_provider_alert_events_no_delete")


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        digest_check = "payload_sha256 ~ '^[0-9a-f]{64}$'"
    else:
        digest_check = (
            "length(payload_sha256) = 64 AND "
            "lower(payload_sha256) = payload_sha256 AND "
            "payload_sha256 NOT GLOB '*[^0-9a-f]*'"
        )
    op.create_table(
        "relay_provider_alert_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=192), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("incident_kind", sa.String(length=40), nullable=False),
        sa.Column("incident_state", sa.String(length=16), nullable=False),
        sa.Column("provider_name", sa.String(length=64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("affected_routes", sa.Integer(), nullable=False),
        sa.Column("total_routes", sa.Integer(), nullable=False),
        sa.Column("success_rate_basis_points", sa.Integer(), nullable=False),
        sa.Column("delivery_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=80), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "schema_version = 1", name="ck_relay_provider_alert_schema_v1"
        ),
        sa.CheckConstraint(
            "incident_kind IN ('success_rate_drop', "
            "'widespread_route_failure', 'batch_account_invalidation')",
            name="ck_relay_provider_alert_kind",
        ),
        sa.CheckConstraint(
            "incident_state IN ('triggered', 'recovered')",
            name="ck_relay_provider_alert_state",
        ),
        sa.CheckConstraint(
            "event_type = 'provider_monitor.' || incident_kind || '.' || "
            "incident_state",
            name="ck_relay_provider_alert_event_type",
        ),
        sa.CheckConstraint(
            "generation > 0 AND sample_size >= 0 AND success_count >= 0 AND "
            "success_count <= sample_size AND affected_routes >= 0 AND "
            "total_routes >= 0 AND affected_routes <= total_routes AND "
            "success_rate_basis_points >= 0 AND "
            "success_rate_basis_points <= 10000",
            name="ck_relay_provider_alert_metrics",
        ),
        sa.CheckConstraint(
            digest_check,
            name="ck_relay_provider_alert_payload_sha256",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_relay_provider_alert_provider_occurred",
        "relay_provider_alert_events",
        ["provider_name", "occurred_at"],
    )
    op.create_index(
        "ix_relay_provider_alert_kind_state_occurred",
        "relay_provider_alert_events",
        ["incident_kind", "incident_state", "occurred_at"],
    )
    _create_immutable_triggers()


def downgrade() -> None:
    _drop_immutable_triggers()
    op.drop_index(
        "ix_relay_provider_alert_kind_state_occurred",
        table_name="relay_provider_alert_events",
    )
    op.drop_index(
        "ix_relay_provider_alert_provider_occurred",
        table_name="relay_provider_alert_events",
    )
    op.drop_table("relay_provider_alert_events")
