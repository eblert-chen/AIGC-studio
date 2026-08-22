"""Add durable Download Gateway registration attempts.

Revision ID: 0023_download_gateway_attempts
Revises: 0022_download_storage_binding
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0023_download_gateway_attempts"
down_revision: str | None = "0022_download_storage_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _sha256_check(
    column_name: str,
    *,
    dialect: str,
    nullable: bool = False,
) -> str:
    if dialect == "postgresql":
        hash_check = f"{column_name} ~ '^[0-9a-f]{{64}}$'"
    else:
        hash_check = (
            f"length({column_name}) = 64 "
            f"AND lower({column_name}) = {column_name} "
            f"AND {column_name} NOT GLOB '*[^0-9a-f]*'"
        )
    if nullable:
        return f"{column_name} IS NULL OR ({hash_check})"
    return hash_check


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    op.create_table(
        "download_gateway_registration_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=160), nullable=False),
        sa.Column("requested_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("platform_request_id", sa.String(length=80), nullable=False),
        sa.Column("registration_request_id", sa.String(length=36), nullable=False),
        sa.Column("download_record_id", sa.String(length=36), nullable=False),
        sa.Column("transfer_reference", sa.String(length=36), nullable=False),
        sa.Column("expected_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_provider", sa.String(length=32), nullable=False),
        sa.Column("storage_endpoint_host", sa.String(length=253), nullable=False),
        sa.Column("storage_bucket", sa.String(length=63), nullable=False),
        sa.Column("storage_object_key", sa.String(length=1024), nullable=False),
        sa.Column("source_url_sha256", sa.String(length=64), nullable=False),
        sa.Column("relay_issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("relay_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("request_nonce", sa.LargeBinary(length=12), nullable=True),
        sa.Column("response_sha256", sa.String(length=64), nullable=True),
        sa.Column("response_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("response_nonce", sa.LargeBinary(length=12), nullable=True),
        sa.Column("gateway_ticket_id", sa.String(length=36), nullable=True),
        sa.Column(
            "gateway_ticket_url_sha256", sa.String(length=64), nullable=True
        ),
        sa.Column("gateway_issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gateway_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gateway_expires_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "PROCESSING",
                "RETRY",
                "UNKNOWN",
                "RECONCILED_EXPIRED",
                "REGISTERED",
                "ATTACHED",
                "DEAD",
                name="downloadgatewayregistrationstatus",
                native_enum=False,
                validate_strings=True,
            ),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column("ticket_replay_count", sa.BigInteger(), nullable=False),
        sa.Column("ticket_replayed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "response_destroy_after", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "reconciliation_ack_sha256", sa.String(length=64), nullable=True
        ),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_download_gateway_attempt_count_nonnegative",
        ),
        sa.CheckConstraint(
            "expected_size_bytes > 0",
            name="ck_download_gateway_attempt_size_positive",
        ),
        sa.CheckConstraint(
            "ticket_replay_count >= 0 "
            "AND ticket_replay_count <= 9223372036854775807",
            name="ck_download_gateway_attempt_replay_count",
        ),
        sa.CheckConstraint(
            "((lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL))",
            name="ck_download_gateway_attempt_lease_complete",
        ),
        sa.CheckConstraint(
            _sha256_check("artifact_sha256", dialect=dialect),
            name="ck_download_gateway_attempt_artifact_sha_hex",
        ),
        sa.CheckConstraint(
            _sha256_check("source_url_sha256", dialect=dialect),
            name="ck_download_gateway_attempt_source_url_sha_hex",
        ),
        sa.CheckConstraint(
            _sha256_check("body_sha256", dialect=dialect),
            name="ck_download_gateway_attempt_body_sha_shape",
        ),
        sa.CheckConstraint(
            _sha256_check("response_sha256", dialect=dialect, nullable=True),
            name="ck_download_gateway_attempt_response_sha_hex",
        ),
        sa.CheckConstraint(
            _sha256_check(
                "gateway_ticket_url_sha256", dialect=dialect, nullable=True
            ),
            name="ck_download_gateway_attempt_ticket_url_sha_hex",
        ),
        sa.CheckConstraint(
            _sha256_check(
                "reconciliation_ack_sha256", dialect=dialect, nullable=True
            ),
            name="ck_download_gateway_attempt_reconciliation_ack_sha_hex",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["generation_tasks.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "requested_by_user_id",
            "platform_request_id",
            name="uq_download_gateway_attempt_request",
        ),
        sa.UniqueConstraint(
            "registration_request_id",
            name="uq_download_gateway_attempt_registration",
        ),
        sa.UniqueConstraint(
            "download_record_id",
            name="uq_download_gateway_attempt_record",
        ),
        sa.UniqueConstraint(
            "transfer_reference",
            name="uq_download_gateway_attempt_transfer",
        ),
    )
    for index_name, columns in (
        (
            "ix_download_gateway_attempt_dispatch",
            ("status", "next_attempt_at", "lease_expires_at", "created_at"),
        ),
        (
            "ix_download_gateway_attempt_company_created",
            ("company_id", "created_at"),
        ),
        (
            "ix_download_gateway_registration_attempts_company_id",
            ("company_id",),
        ),
        (
            "ix_download_gateway_registration_attempts_task_id",
            ("task_id",),
        ),
        (
            "ix_download_gateway_registration_attempts_requested_by_user_id",
            ("requested_by_user_id",),
        ),
    ):
        op.create_index(
            index_name,
            "download_gateway_registration_attempts",
            list(columns),
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("download_gateway_registration_attempts")
