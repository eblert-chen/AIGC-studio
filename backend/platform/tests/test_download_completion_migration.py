from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def _config(project_root: Path, database_path: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


def _insert_completion(
    engine,
    *,
    completion_id: str,
    external_event_id: str,
    signed_event_id: str | None = None,
) -> None:
    verified = signed_event_id is not None
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO download_completions "
                "(id, download_record_id, external_event_id, source, "
                "bytes_sent, completed_at, verification_version, "
                "artifact_sha256, expected_size_bytes, http_status, "
                "transfer_scope, source_evidence, signed_event_id, "
                "signed_event_timestamp, signed_payload_sha256, verified_at, "
                "created_at) VALUES "
                "(:id, 'missing-parent-for-migration-test', :external_event_id, "
                "'EDGE_GATEWAY', 4096, :completed_at, :verification_version, "
                ":artifact_sha256, :expected_size_bytes, :http_status, "
                ":transfer_scope, :source_evidence, :signed_event_id, "
                ":signed_event_timestamp, :signed_payload_sha256, :verified_at, "
                ":created_at)"
            ),
            {
                "id": completion_id,
                "external_event_id": external_event_id,
                "completed_at": now,
                "verification_version": 1 if verified else None,
                "artifact_sha256": "a" * 64 if verified else None,
                "expected_size_bytes": 4096 if verified else None,
                "http_status": 200 if verified else None,
                "transfer_scope": "full_body" if verified else None,
                "source_evidence": (
                    '{"gateway_request_id":"request-1",'
                    '"gateway_transfer_reference":"transfer-1"}'
                    if verified
                    else None
                ),
                "signed_event_id": signed_event_id,
                "signed_event_timestamp": now if verified else None,
                "signed_payload_sha256": "b" * 64 if verified else None,
                "verified_at": now if verified else None,
                "created_at": now,
            },
        )


def test_0021_preserves_history_but_requires_verified_new_rows(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "download-completion-0021.db"
    config = _config(project_root, database_path)
    command.upgrade(config, "0020_publishing")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")

    now = datetime(2026, 8, 7, 11, 59, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO download_completions "
                "(id, download_record_id, external_event_id, source, "
                "bytes_sent, completed_at, created_at) VALUES "
                "('historical-unsigned', 'historical-missing-parent', "
                "'historical-event', 'OBS_ACCESS_LOG', 4096, :now, :now)"
            ),
            {"now": now},
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0040_showcase_management"
        )
        historical = connection.execute(
            text(
                "SELECT verification_version, signed_event_id, verified_at "
                "FROM download_completions WHERE id = 'historical-unsigned'"
            )
        ).one()
        assert historical == (None, None, None)
        columns = {
            column["name"]
            for column in inspect(engine).get_columns("download_completions")
        }
        assert {
            "verification_version",
            "artifact_sha256",
            "expected_size_bytes",
            "http_status",
            "transfer_scope",
            "source_evidence",
            "signed_event_id",
            "signed_event_timestamp",
            "signed_payload_sha256",
            "verified_at",
        } <= columns

    with pytest.raises(
        IntegrityError,
        match="new download completions require signed source evidence",
    ):
        _insert_completion(
            engine,
            completion_id="new-unsigned",
            external_event_id="new-unsigned-event",
        )
    _insert_completion(
        engine,
        completion_id="new-verified",
        external_event_id="new-verified-event",
        signed_event_id="11111111-1111-4111-8111-111111111111",
    )
    with pytest.raises(IntegrityError):
        _insert_completion(
            engine,
            completion_id="duplicate-signed-event",
            external_event_id="another-external-event",
            signed_event_id="11111111-1111-4111-8111-111111111111",
        )
    engine.dispose()

    command.downgrade(config, "0020_publishing")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0020_publishing"
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM download_completions "
                    "WHERE id = 'historical-unsigned'"
                )
            )
            == 1
        )
    engine.dispose()
