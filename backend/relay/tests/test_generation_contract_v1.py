from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from relay_service.models import (
    GenerationAccepted,
    JobStatus,
    ReservationAction,
)


REVISION = "sha256:" + ("a" * 64)


def _migration_config(project_root: Path, database_path: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
    )
    return config


def test_public_resource_invariants_reject_wallet_or_pin_drift() -> None:
    job_id = uuid4()
    with pytest.raises(ValueError, match="reservation action"):
        GenerationAccepted(
            id=job_id,
            job_id=job_id,
            status=JobStatus.QUEUED,
            expected_capability_revision=REVISION,
            capability_revision=REVISION,
            reservation_action=ReservationAction.RELEASE,
            created_at="2026-08-05T00:00:00Z",
        )
    with pytest.raises(ValueError, match="capability revisions"):
        GenerationAccepted(
            id=job_id,
            job_id=job_id,
            status=JobStatus.QUEUED,
            expected_capability_revision=REVISION,
            capability_revision="sha256:" + ("b" * 64),
            reservation_action=ReservationAction.HOLD,
            created_at="2026-08-05T00:00:00Z",
        )


def test_0012_backfills_and_fences_revision_on_sqlite(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("RELAY_DATABASE_URL", raising=False)
    database_path = tmp_path / "relay-0012.db"
    project_root = Path(__file__).resolve().parents[1]
    config = _migration_config(project_root, database_path)
    command.upgrade(config, "0011_provider_monitoring")

    sync_url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(sync_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            INSERT INTO generation_jobs (
                id, tenant_id, model, mode, inputs_json, output_json,
                metadata_json, status, progress, outputs_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                str(uuid4()),
                "mock.video.v1",
                "text_to_video",
                '{"prompt":"legacy","assets":[]}',
                '{"duration_seconds":5,"aspect_ratio":"16:9",'
                '"resolution":"720p","count":1,"face_enabled":false}',
                '{"relay_capability_revision":"' + REVISION + '"}',
                "queued",
                0,
                "[]",
                "2026-08-05 00:00:00",
                "2026-08-05 00:00:00",
            ),
        )
    engine.dispose()

    command.upgrade(config, "0012_generation_contract_v1")
    engine = create_engine(sync_url)
    columns = {
        item["name"]
        for item in inspect(engine).get_columns("generation_jobs")
    }
    with engine.begin() as connection:
        stored = connection.exec_driver_sql(
            "SELECT expected_capability_revision FROM generation_jobs"
        ).scalar_one()
        assert stored == REVISION
        with pytest.raises(IntegrityError, match="immutable"):
            connection.exec_driver_sql(
                "UPDATE generation_jobs "
                "SET expected_capability_revision = ?",
                ("sha256:" + ("b" * 64),),
            )
    assert "expected_capability_revision" in columns
    engine.dispose()
