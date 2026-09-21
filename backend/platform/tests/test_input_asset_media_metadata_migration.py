from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DatabaseError


def _config(database_path: Path) -> Config:
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite:///{database_path.as_posix()}",
    )
    return config


def _insert_asset(connection, *, asset_id: str, normalized: bool) -> None:
    metadata = (
        {
            "metadata_version": 1,
            "width": 320,
            "height": 180,
            "container": "mp4",
            "codec": "h264",
            "fps": 24.0,
            "duration": 2_200,
            "has_audio": False,
            "profile": "director_previs_mp4_v1",
            "source_sha": "b" * 64,
        }
        if normalized
        else {
            "metadata_version": None,
            "width": None,
            "height": None,
            "container": None,
            "codec": None,
            "fps": None,
            "duration": None,
            "has_audio": None,
            "profile": None,
            "source_sha": None,
        }
    )
    now = datetime.now(timezone.utc)
    connection.execute(
        text(
            "INSERT INTO input_assets ("
            "id, company_id, personal_workspace_id, uploaded_by_user_id, "
            "source_task_artifact_id, idempotency_key, original_filename, "
            "media_type, content_type, size_bytes, sha256, "
            "media_metadata_version, width_px, height_px, media_container, "
            "video_codec, video_fps, duration_ms, video_has_audio, "
            "normalization_profile, source_sha256, storage_backend, object_key, "
            "status, created_at, updated_at"
            ") VALUES ("
            ":id, :company, NULL, :user, NULL, :idempotency, :filename, "
            ":media_type, :content_type, 100, :sha, :metadata_version, :width, "
            ":height, :container, :codec, :fps, :duration, :has_audio, :profile, "
            ":source_sha, 'filesystem', :object_key, 'ACTIVE', :now, :now)"
        ),
        {
            "id": asset_id,
            "company": "10000000-0000-4000-8000-000000000001",
            "user": "20000000-0000-4000-8000-000000000001",
            "idempotency": f"migration-{asset_id}",
            "filename": "previs.mp4" if normalized else "legacy.webm",
            "media_type": "video",
            "content_type": "video/mp4" if normalized else "video/webm",
            "sha": "a" * 64,
            "object_key": f"inputs/migration/{asset_id}",
            "now": now,
            **metadata,
        },
    )


def test_0054_adds_trusted_metadata_constraints_and_safe_downgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_path = tmp_path / "input-asset-media-metadata.db"
    config = _config(database_path)
    command.upgrade(config, "0054_input_asset_media_metadata")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("input_assets")}
    assert {
        "media_metadata_version",
        "width_px",
        "height_px",
        "media_container",
        "video_codec",
        "video_fps",
        "duration_ms",
        "video_has_audio",
        "normalization_profile",
        "source_sha256",
    } <= columns
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("input_assets")
    } >= {
        "ck_input_asset_media_metadata_version",
        "ck_input_asset_dimensions_positive",
        "ck_input_asset_trusted_metadata_shape",
        "ck_input_asset_normalization_profile",
        "ck_input_asset_source_sha256",
    }
    legacy_id = "30000000-0000-4000-8000-000000000001"
    normalized_id = "30000000-0000-4000-8000-000000000002"
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0054_input_asset_media_metadata"
        )
        _insert_asset(connection, asset_id=legacy_id, normalized=False)
        _insert_asset(connection, asset_id=normalized_id, normalized=True)
    with pytest.raises(DatabaseError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE input_assets SET video_fps = 15 "
                    "WHERE id = :asset_id"
                ),
                {"asset_id": normalized_id},
            )
    with pytest.raises(DatabaseError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE input_assets SET source_sha256 = sha256 "
                    "WHERE id = :asset_id"
                ),
                {"asset_id": normalized_id},
            )
    engine.dispose()

    with pytest.raises(RuntimeError, match="normalized director previs"):
        command.downgrade(config, "0053_director_shot_packages")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM input_assets WHERE id = :asset_id"),
            {"asset_id": normalized_id},
        )
    engine.dispose()
    command.downgrade(config, "0053_director_shot_packages")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert "media_metadata_version" not in {
        column["name"] for column in inspect(engine).get_columns("input_assets")
    }
    assert engine.connect().scalar(
        text("SELECT count(*) FROM input_assets WHERE id = :asset_id"),
        {"asset_id": legacy_id},
    ) == 1
    engine.dispose()
    command.upgrade(config, "head")
    command.check(config)
