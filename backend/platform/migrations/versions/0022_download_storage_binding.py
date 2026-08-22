"""Persist immutable Relay storage and Download Gateway ticket bindings.

Revision ID: 0022_download_storage_binding
Revises: 0021_download_completion_proof
"""

from __future__ import annotations

import re
from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0022_download_storage_binding"
down_revision: str | None = "0021_download_completion_proof"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _binding_check(*, source_hash_check: str, gateway_hash_check: str) -> str:
    return (
        "(storage_binding_version IS NULL "
        "AND storage_provider IS NULL "
        "AND storage_endpoint_host IS NULL "
        "AND storage_bucket IS NULL "
        "AND storage_object_key IS NULL "
        "AND storage_version_id IS NULL "
        "AND source_url_sha256 IS NULL "
        "AND relay_issued_at IS NULL "
        "AND relay_expires_at IS NULL "
        "AND gateway_registration_request_id IS NULL "
        "AND gateway_ticket_id IS NULL "
        "AND gateway_ticket_url_sha256 IS NULL "
        "AND gateway_issued_at IS NULL "
        "AND gateway_expires_at IS NULL "
        "AND gateway_transfer_reference IS NULL) OR "
        "(storage_binding_version = 1 "
        "AND storage_provider IS NOT NULL "
        "AND storage_provider = 'huawei_obs' "
        "AND storage_endpoint_host IS NOT NULL "
        "AND storage_bucket IS NOT NULL "
        "AND storage_object_key IS NOT NULL "
        "AND source_url_sha256 IS NOT NULL "
        f"AND {source_hash_check} "
        "AND relay_issued_at IS NOT NULL "
        "AND relay_expires_at IS NOT NULL "
        "AND relay_expires_at > relay_issued_at "
        "AND ((gateway_registration_request_id IS NULL "
        "AND gateway_ticket_id IS NULL "
        "AND gateway_ticket_url_sha256 IS NULL "
        "AND gateway_issued_at IS NULL "
        "AND gateway_expires_at IS NULL "
        "AND gateway_transfer_reference IS NULL) OR "
        "(gateway_registration_request_id IS NOT NULL "
        "AND gateway_ticket_id IS NOT NULL "
        "AND gateway_ticket_url_sha256 IS NOT NULL "
        f"AND {gateway_hash_check} "
        "AND gateway_issued_at IS NOT NULL "
        "AND gateway_expires_at IS NOT NULL "
        "AND gateway_expires_at > gateway_issued_at "
        "AND expires_at = gateway_expires_at "
        "AND gateway_transfer_reference IS NOT NULL)))"
    )


_POSTGRES_BINDING_CHECK = _binding_check(
    source_hash_check="source_url_sha256 ~ '^[0-9a-f]{64}$'",
    gateway_hash_check="gateway_ticket_url_sha256 ~ '^[0-9a-f]{64}$'",
)
_SQLITE_BINDING_CHECK = _binding_check(
    source_hash_check=(
        "length(source_url_sha256) = 64 "
        "AND lower(source_url_sha256) = source_url_sha256 "
        "AND source_url_sha256 NOT GLOB '*[^0-9a-f]*'"
    ),
    gateway_hash_check=(
        "length(gateway_ticket_url_sha256) = 64 "
        "AND lower(gateway_ticket_url_sha256) = gateway_ticket_url_sha256 "
        "AND gateway_ticket_url_sha256 NOT GLOB '*[^0-9a-f]*'"
    ),
)

_BINDING_COLUMNS = (
    "storage_binding_version",
    "storage_provider",
    "storage_endpoint_host",
    "storage_bucket",
    "storage_object_key",
    "storage_version_id",
    "source_url_sha256",
    "relay_issued_at",
    "relay_expires_at",
    "gateway_registration_request_id",
    "gateway_ticket_id",
    "gateway_ticket_url_sha256",
    "gateway_issued_at",
    "gateway_expires_at",
    "gateway_transfer_reference",
    "expires_at",
)


def _sqlite_new_row_binding_check() -> str:
    check = _SQLITE_BINDING_CHECK
    for column_name in _BINDING_COLUMNS:
        check = re.sub(
            rf"(?<![\w.]){re.escape(column_name)}\b",
            f"NEW.{column_name}",
            check,
        )
    return check


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    columns = (
        sa.Column("storage_binding_version", sa.Integer(), nullable=True),
        sa.Column("storage_provider", sa.String(length=32), nullable=True),
        sa.Column("storage_endpoint_host", sa.String(length=253), nullable=True),
        sa.Column("storage_bucket", sa.String(length=63), nullable=True),
        sa.Column("storage_object_key", sa.String(length=1024), nullable=True),
        sa.Column("storage_version_id", sa.String(length=256), nullable=True),
        sa.Column("source_url_sha256", sa.String(length=64), nullable=True),
        sa.Column("relay_issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("relay_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "gateway_registration_request_id",
            sa.String(length=36),
            nullable=True,
        ),
        sa.Column("gateway_ticket_id", sa.String(length=36), nullable=True),
        sa.Column(
            "gateway_ticket_url_sha256",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("gateway_issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gateway_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "gateway_transfer_reference",
            sa.String(length=36),
            nullable=True,
        ),
    )
    for column in columns:
        op.add_column("download_records", column)
    for index_name, column_name in (
        (
            "uq_download_gateway_registration_request",
            "gateway_registration_request_id",
        ),
        ("uq_download_gateway_ticket", "gateway_ticket_id"),
        (
            "uq_download_gateway_transfer_reference",
            "gateway_transfer_reference",
        ),
    ):
        op.create_index(
            index_name,
            "download_records",
            [column_name],
            unique=True,
        )

    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE task_artifacts ADD CONSTRAINT "
            "ck_task_artifact_size_positive CHECK (size_bytes > 0) NOT VALID"
        )
        op.execute(
            "ALTER TABLE download_records ADD CONSTRAINT "
            "ck_download_storage_binding_complete CHECK ("
            + _POSTGRES_BINDING_CHECK
            + ") NOT VALID"
        )
    elif dialect == "sqlite":
        op.execute(
            "CREATE TRIGGER trg_task_artifacts_size_positive_insert "
            "BEFORE INSERT ON task_artifacts WHEN NEW.size_bytes <= 0 "
            "BEGIN SELECT RAISE(ABORT, "
            "'task artifact size must be positive'); END"
        )
        op.execute(
            "CREATE TRIGGER trg_download_records_storage_binding_insert "
            "BEFORE INSERT ON download_records WHEN NOT ("
            + _sqlite_new_row_binding_check()
            + ") BEGIN SELECT RAISE(ABORT, "
            "'download storage binding is incomplete'); END"
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE download_records DROP CONSTRAINT IF EXISTS "
            "ck_download_storage_binding_complete"
        )
        op.execute(
            "ALTER TABLE task_artifacts DROP CONSTRAINT IF EXISTS "
            "ck_task_artifact_size_positive"
        )
    elif dialect == "sqlite":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_download_records_storage_binding_insert"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_task_artifacts_size_positive_insert"
        )
    for index_name in (
        "uq_download_gateway_transfer_reference",
        "uq_download_gateway_ticket",
        "uq_download_gateway_registration_request",
    ):
        op.drop_index(index_name, table_name="download_records")
    for column_name in (
        "gateway_transfer_reference",
        "gateway_expires_at",
        "gateway_issued_at",
        "gateway_ticket_url_sha256",
        "gateway_ticket_id",
        "gateway_registration_request_id",
        "relay_expires_at",
        "relay_issued_at",
        "source_url_sha256",
        "storage_version_id",
        "storage_object_key",
        "storage_bucket",
        "storage_endpoint_host",
        "storage_provider",
        "storage_binding_version",
    ):
        op.drop_column("download_records", column_name)
