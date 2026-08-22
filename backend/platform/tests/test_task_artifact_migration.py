from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from platform_api.models import (
    DownloadCompletion,
    DownloadCompletionSource,
    DownloadRecord,
    TaskStatus,
    utcnow,
)


def _config(project_root: Path, database_path: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


def _task(
    *,
    task_id: str,
    company_id: str,
    user_id: str,
    model_id: str,
    outputs: list[dict],
    output_count: int | None = None,
    status: TaskStatus = TaskStatus.SUCCEEDED,
) -> dict:
    request_payload = {
        "mode": "text_to_video",
        "prompt": task_id,
    }
    if output_count is not None:
        request_payload["output_count"] = output_count
    timestamp = utcnow()
    return {
        "id": task_id,
        "company_id": company_id,
        "user_id": user_id,
        "model_id": model_id,
        "idempotency_key": f"migration-{task_id}",
        "request_fingerprint": (task_id * 64)[:64],
        "status": status.name,
        "request_payload": request_payload,
        "quote_cents": 25,
        "pricing_snapshot": {
            "mode": "per_item",
            "unit_price_cents": 25,
            "quantity": 1,
            "quote_cents": 25,
        },
        "capability_snapshot": {"capability_version": 1},
        "reserved_cents": 0,
        "actual_cost_cents": 25 if status == TaskStatus.SUCCEEDED else None,
        "provider_task_id": None,
        "relay_job_id": None,
        "output_artifacts": outputs,
        "failure_reason": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def test_0016_backfills_only_complete_artifacts_and_guards_audit_rows(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "task-artifact-0016.db"
    config = _config(project_root, database_path)
    command.upgrade(config, "0015_channel_cost_ledger")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = utcnow()
    valid_image = {
        "asset_id": "migration-image",
        "media_type": "image",
        "content_type": "image/png",
        "size_bytes": 640,
        "sha256": "a" * 64,
    }
    valid_video_one = {
        "asset_id": "migration-video-one",
        "media_type": "video",
        "content_type": "video/mp4",
        "size_bytes": 1_200,
        "sha256": "b" * 64,
    }
    valid_video_two = {
        "asset_id": "migration-video-two",
        "media_type": "video",
        "content_type": "video/mp4",
        "size_bytes": 2_400,
        "sha256": "c" * 64,
    }
    with factory.begin() as session:
        company_id = "migration-company"
        user_id = "migration-user"
        legacy_companies = Table(
            "companies",
            MetaData(),
            autoload_with=session.connection(),
        )
        legacy_users = Table(
            "users",
            MetaData(),
            autoload_with=session.connection(),
        )
        session.execute(
            legacy_companies.insert(),
            {
                "id": company_id,
                "name": "Migration Company",
                "status": "ACTIVE",
                "created_at": now,
                "updated_at": now,
            },
        )
        session.execute(
            legacy_users.insert(),
            {
                "id": user_id,
                "email": "migration@example.com",
                "display_name": "Migration User",
                "is_platform_admin": False,
                "created_at": now,
                "updated_at": now,
            },
        )
        model_id = "migration-model"
        # This fixture intentionally targets the pre-0016 schema. Insert only
        # columns that existed at that revision instead of using the current
        # ORM mapping, which may include fields added by later migrations.
        session.execute(
            text(
                "INSERT INTO model_definitions "
                "(id, slug, display_name, provider_key, billing_mode, "
                "capability_version, active, published_at, created_at, updated_at) "
                "VALUES (:id, :slug, :display_name, :provider_key, :billing_mode, "
                ":capability_version, :active, :published_at, :created_at, :updated_at)"
            ),
            {
                "id": model_id,
                "slug": model_id,
                "display_name": "Migration Model",
                "provider_key": "migration-provider",
                "billing_mode": "per_item",
                "capability_version": 1,
                "active": True,
                "published_at": now,
                "created_at": now,
                "updated_at": now,
            },
        )
        legacy_generation_tasks = Table(
            "generation_tasks",
            MetaData(),
            autoload_with=session.connection(),
        )
        session.execute(
            legacy_generation_tasks.insert(),
            [
                _task(
                    task_id="valid-default-count",
                    company_id=company_id,
                    user_id=user_id,
                    model_id=model_id,
                    outputs=[valid_image],
                ),
                _task(
                    task_id="valid-two",
                    company_id=company_id,
                    user_id=user_id,
                    model_id=model_id,
                    outputs=[valid_video_one, valid_video_two],
                    output_count=2,
                ),
                _task(
                    task_id="skip-empty",
                    company_id=company_id,
                    user_id=user_id,
                    model_id=model_id,
                    outputs=[],
                ),
                _task(
                    task_id="skip-count-mismatch",
                    company_id=company_id,
                    user_id=user_id,
                    model_id=model_id,
                    outputs=[valid_video_one],
                    output_count=2,
                ),
                _task(
                    task_id="skip-invalid-sha",
                    company_id=company_id,
                    user_id=user_id,
                    model_id=model_id,
                    outputs=[{**valid_video_one, "sha256": "not-a-digest"}],
                ),
                _task(
                    task_id="skip-duplicate-assets",
                    company_id=company_id,
                    user_id=user_id,
                    model_id=model_id,
                    outputs=[valid_video_one, valid_video_one],
                    output_count=2,
                ),
                _task(
                    task_id="skip-non-success",
                    company_id=company_id,
                    user_id=user_id,
                    model_id=model_id,
                    outputs=[valid_video_one],
                    status=TaskStatus.FAILED,
                ),
            ],
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    assert {"download_completions", "task_artifacts"} <= set(
        inspect(engine).get_table_names()
    )
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0040_showcase_management"
        )
        artifacts = list(
            connection.execute(
                text(
                    "SELECT task_id, asset_id, position, media_type, size_bytes "
                    "FROM task_artifacts ORDER BY task_id, position"
                )
            ).mappings()
        )
        assert artifacts == [
            {
                "task_id": "valid-default-count",
                "asset_id": "migration-image",
                "position": 0,
                "media_type": "image",
                "size_bytes": 640,
            },
            {
                "task_id": "valid-two",
                "asset_id": "migration-video-one",
                "position": 0,
                "media_type": "video",
                "size_bytes": 1_200,
            },
            {
                "task_id": "valid-two",
                "asset_id": "migration-video-two",
                "position": 1,
                "media_type": "video",
                "size_bytes": 2_400,
            },
        ]

        trigger_rows = list(
            connection.execute(
                text(
                    "SELECT tbl_name, name FROM sqlite_master "
                    "WHERE type = 'trigger' AND tbl_name IN "
                    "('task_artifacts', 'download_records', "
                    "'download_completions')"
                )
            )
        )
        assert len(trigger_rows) == 9
        assert (
            "task_artifacts",
            "trg_task_artifacts_size_positive_insert",
        ) in trigger_rows
        assert (
            "download_records",
            "trg_download_records_storage_binding_insert",
        ) in trigger_rows
        assert (
            "download_completions",
            "trg_download_completions_verified_insert",
        ) in trigger_rows

    with factory.begin() as session:
        record = DownloadRecord(
            id="migration-download-record",
            company_id="migration-company",
            task_id="valid-default-count",
            asset_id="migration-image",
            requested_by_user_id="migration-user",
            expires_seconds=300,
            expires_at=now + timedelta(seconds=300),
            request_id="migration-download-request",
            created_at=now,
        )
        session.add(record)
        session.flush()
        session.add(
            DownloadCompletion(
                id="migration-download-completion",
                download_record_id=record.id,
                external_event_id="migration-download-event",
                source=DownloadCompletionSource.OBS_ACCESS_LOG,
                bytes_sent=640,
                completed_at=now + timedelta(seconds=1),
                verification_version=1,
                artifact_sha256="a" * 64,
                expected_size_bytes=640,
                http_status=200,
                transfer_scope="full_body",
                source_evidence={
                    "obs_bucket": "migration-artifacts",
                    "obs_object_key": "migration-image",
                    "obs_request_id": "migration-obs-request",
                },
                signed_event_id="11111111-1111-4111-8111-111111111111",
                signed_event_timestamp=now + timedelta(seconds=1),
                signed_payload_sha256="d" * 64,
                verified_at=now + timedelta(seconds=2),
                created_at=now + timedelta(seconds=2),
            )
        )

    mutations = (
        "UPDATE task_artifacts SET size_bytes = 1 "
        "WHERE task_id = 'valid-default-count'",
        "DELETE FROM task_artifacts WHERE task_id = 'valid-default-count'",
        "UPDATE download_records SET expires_seconds = 1 "
        "WHERE id = 'migration-download-record'",
        "DELETE FROM download_records WHERE id = 'migration-download-record'",
        "UPDATE download_completions SET bytes_sent = 1 "
        "WHERE id = 'migration-download-completion'",
        "DELETE FROM download_completions "
        "WHERE id = 'migration-download-completion'",
    )
    for statement in mutations:
        with pytest.raises(
            IntegrityError,
            match="artifact and download audit records are immutable",
        ):
            with engine.begin() as connection:
                connection.execute(text(statement))

    with factory() as session:
        assert session.scalar(select(DownloadRecord.id)) == (
            "migration-download-record"
        )
        assert session.scalar(select(DownloadCompletion.id)) == (
            "migration-download-completion"
        )
    engine.dispose()
