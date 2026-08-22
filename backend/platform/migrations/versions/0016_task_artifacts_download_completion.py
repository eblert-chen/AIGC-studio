"""Index durable task artifacts and distinguish download completion.

Revision ID: 0016_task_artifact_audit
Revises: 0015_channel_cost_ledger
"""

from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Any, Sequence
import uuid

from alembic import op
import sqlalchemy as sa


revision: str = "0016_task_artifact_audit"
down_revision: str | None = "0015_channel_cost_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


download_completion_source = sa.Enum(
    "PLATFORM_PROXY",
    "OBS_ACCESS_LOG",
    "EDGE_GATEWAY",
    name="downloadcompletionsource",
    native_enum=False,
)


def _json_value(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return fallback
    return value


def _valid_artifact(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    asset_id = value.get("asset_id")
    media_type = value.get("media_type")
    content_type = value.get("content_type")
    size_bytes = value.get("size_bytes")
    sha256 = value.get("sha256")
    return bool(
        isinstance(asset_id, str)
        and 1 <= len(asset_id) <= 160
        and media_type in {"image", "video"}
        and isinstance(content_type, str)
        and 1 <= len(content_type) <= 255
        and not isinstance(size_bytes, bool)
        and isinstance(size_bytes, int)
        and size_bytes >= 0
        and isinstance(sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", sha256)
    )


def _backfill_task_artifacts() -> None:
    connection = op.get_bind()
    query = sa.text(
        "SELECT id, company_id, request_payload, output_artifacts, updated_at "
        "FROM generation_tasks WHERE lower(status) = 'succeeded'"
    ).columns(
        id=sa.String(),
        company_id=sa.String(),
        request_payload=sa.JSON(),
        output_artifacts=sa.JSON(),
        updated_at=sa.DateTime(timezone=True),
    )
    rows = connection.execute(query).mappings()
    artifact_table = sa.table(
        "task_artifacts",
        sa.column("id", sa.String()),
        sa.column("company_id", sa.String()),
        sa.column("task_id", sa.String()),
        sa.column("asset_id", sa.String()),
        sa.column("position", sa.Integer()),
        sa.column("media_type", sa.String()),
        sa.column("content_type", sa.String()),
        sa.column("size_bytes", sa.BigInteger()),
        sa.column("sha256", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    for row in rows:
        request_payload = _json_value(row["request_payload"], {})
        outputs = _json_value(row["output_artifacts"], [])
        if not isinstance(request_payload, dict) or not isinstance(outputs, list):
            continue
        expected_count = request_payload.get("output_count", 1)
        if (
            isinstance(expected_count, bool)
            or not isinstance(expected_count, int)
            or expected_count <= 0
            or len(outputs) != expected_count
            or not outputs
            or not all(_valid_artifact(item) for item in outputs)
        ):
            continue
        asset_ids = [item["asset_id"] for item in outputs]
        if len(set(asset_ids)) != len(asset_ids):
            continue
        created_at = row["updated_at"]
        if not isinstance(created_at, datetime):
            continue
        connection.execute(
            artifact_table.insert(),
            [
                {
                    "id": str(uuid.uuid4()),
                    "company_id": row["company_id"],
                    "task_id": row["id"],
                    "asset_id": item["asset_id"],
                    "position": position,
                    "media_type": item["media_type"],
                    "content_type": item["content_type"],
                    "size_bytes": item["size_bytes"],
                    "sha256": item["sha256"],
                    "created_at": created_at,
                }
                for position, item in enumerate(outputs)
            ],
        )


def _create_immutability_guards() -> None:
    dialect = op.get_bind().dialect.name
    table_names = (
        "task_artifacts",
        "download_records",
        "download_completions",
    )
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_artifact_audit_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'artifact and download audit records are immutable';
            END;
            $$
            """
        )
        for table_name in table_names:
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_immutable "
                f"BEFORE UPDATE OR DELETE ON {table_name} "
                "FOR EACH ROW EXECUTE FUNCTION reject_artifact_audit_mutation()"
            )
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_no_truncate "
                f"BEFORE TRUNCATE ON {table_name} FOR EACH STATEMENT "
                "EXECUTE FUNCTION reject_artifact_audit_mutation()"
            )
    elif dialect == "sqlite":
        for table_name in table_names:
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_no_update "
                f"BEFORE UPDATE ON {table_name} BEGIN "
                "SELECT RAISE(ABORT, 'artifact and download audit records "
                "are immutable'); END"
            )
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_no_delete "
                f"BEFORE DELETE ON {table_name} BEGIN "
                "SELECT RAISE(ABORT, 'artifact and download audit records "
                "are immutable'); END"
            )


def _drop_immutability_guards() -> None:
    dialect = op.get_bind().dialect.name
    table_names = (
        "task_artifacts",
        "download_records",
        "download_completions",
    )
    if dialect == "postgresql":
        for table_name in table_names:
            op.execute(
                f"DROP TRIGGER IF EXISTS trg_{table_name}_no_truncate "
                f"ON {table_name}"
            )
            op.execute(
                f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable "
                f"ON {table_name}"
            )
        op.execute("DROP FUNCTION IF EXISTS reject_artifact_audit_mutation()")
    elif dialect == "sqlite":
        for table_name in table_names:
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_no_update")
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_no_delete")


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE permissions SET description = :description "
            "WHERE code = 'tasks.read'"
        ),
        {"description": "查看本人的任务、作品和下载记录"},
    )
    connection.execute(
        sa.text(
            "UPDATE permissions SET description = :description "
            "WHERE code = 'reports.read'"
        ),
        {"description": "查看全公司的任务、作品、消费和下载报表"},
    )
    op.create_table(
        "task_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=160), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "media_type IN ('image', 'video')",
            name="ck_task_artifact_media_type",
        ),
        sa.CheckConstraint(
            "size_bytes >= 0", name="ck_task_artifact_size_nonnegative"
        ),
        sa.CheckConstraint(
            "position >= 0", name="ck_task_artifact_position_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["generation_tasks.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id", "asset_id", name="uq_task_artifact_asset"
        ),
        sa.UniqueConstraint(
            "task_id", "position", name="uq_task_artifact_position"
        ),
    )
    for index_name, columns in (
        ("ix_task_artifacts_company_id", ["company_id"]),
        ("ix_task_artifacts_task_id", ["task_id"]),
        ("ix_task_artifact_company_created", ["company_id", "created_at"]),
        ("ix_task_artifact_task_created", ["task_id", "created_at"]),
    ):
        op.create_index(index_name, "task_artifacts", columns)

    op.create_table(
        "download_completions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("download_record_id", sa.String(length=36), nullable=False),
        sa.Column("external_event_id", sa.String(length=160), nullable=False),
        sa.Column("source", download_completion_source, nullable=False),
        sa.Column("bytes_sent", sa.BigInteger(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "bytes_sent >= 0", name="ck_download_completion_bytes_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["download_record_id"], ["download_records.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "download_record_id", name="uq_download_completion_record"
        ),
        sa.UniqueConstraint(
            "external_event_id", name="uq_download_completion_external_event"
        ),
    )
    op.create_index(
        "ix_download_completions_download_record_id",
        "download_completions",
        ["download_record_id"],
    )
    op.create_index(
        "ix_download_completion_completed",
        "download_completions",
        ["completed_at", "id"],
    )

    _backfill_task_artifacts()
    _create_immutability_guards()


def downgrade() -> None:
    _drop_immutability_guards()
    op.drop_table("download_completions")
    op.drop_table("task_artifacts")
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE permissions SET description = '查看公司任务' "
            "WHERE code = 'tasks.read'"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE permissions SET description = '查看公司任务、消费和下载报表' "
            "WHERE code = 'reports.read'"
        )
    )
