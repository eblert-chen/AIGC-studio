from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text

from platform_api.models import GenerationTask, RelaySubmissionOutbox


PREVIOUS_HEAD = "0038_download_evidence_checks"
CURRENT_HEAD = "0040_showcase_management"
LEGACY_BACKEND_ID = "legacy-default-v1"
NEW_API_BACKEND_ID = "new-api-v1"


def _config(project_root: Path, database_path: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite:///{database_path.as_posix()}",
    )
    return config


def _revision(engine) -> str:
    with engine.connect() as connection:
        return str(connection.scalar(text("SELECT version_num FROM alembic_version")))


def _backend_default(engine, table_name: str) -> str:
    column = next(
        item
        for item in inspect(engine).get_columns(table_name)
        if item["name"] == "relay_backend_id"
    )
    return str(column["default"]).strip("'\"")


def _insert_historical_legacy_rows(engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(
            text(
                "INSERT INTO generation_tasks "
                "(id,company_id,user_id,model_id,status,request_payload,"
                "quote_cents,pricing_snapshot,capability_snapshot,reserved_cents,"
                "created_at,updated_at,"
                "idempotency_key,request_fingerprint,relay_backend_id,"
                "relay_contract_revision) VALUES "
                "('legacy-task','legacy-company','legacy-user','legacy-model',"
                "'SUCCEEDED','{}',1,'{}','{}',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,"
                "'legacy-task-key',"
                ":fingerprint,:backend_id,'generations.v1')"
            ),
            {
                "fingerprint": "1" * 64,
                "backend_id": LEGACY_BACKEND_ID,
            },
        )
        connection.execute(
            text(
                "INSERT INTO relay_submission_outbox "
                "(id,company_id,task_id,status,idempotency_key,relay_payload,attempt_count,"
                "next_attempt_at,created_at,updated_at,relay_backend_id,"
                "relay_contract_revision) VALUES "
                "('legacy-outbox','legacy-company','legacy-task','SENT',"
                "'legacy-outbox-key','{}',0,"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,"
                ":backend_id,'generations.v1')"
            ),
            {"backend_id": LEGACY_BACKEND_ID},
        )


def _insert_defaulted_rows(engine, *, suffix: str) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(
            text(
                "INSERT INTO generation_tasks "
                "(id,company_id,user_id,model_id,status,request_payload,quote_cents,"
                "pricing_snapshot,capability_snapshot,reserved_cents,created_at,updated_at,"
                "idempotency_key,request_fingerprint,relay_contract_revision) "
                "VALUES (:task_id,'new-company','new-user','new-model','SUCCEEDED',"
                "'{}',1,'{}','{}',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,"
                ":task_key,:fingerprint,"
                "'generations.v1')"
            ),
            {
                "task_id": f"task-{suffix}",
                "task_key": f"task-key-{suffix}",
                "fingerprint": (suffix[0] if suffix else "2") * 64,
            },
        )
        connection.execute(
            text(
                "INSERT INTO relay_submission_outbox "
                "(id,company_id,task_id,status,idempotency_key,relay_payload,attempt_count,"
                "next_attempt_at,created_at,updated_at,relay_contract_revision) "
                "VALUES (:outbox_id,'new-company',:task_id,'SENT',:outbox_key,'{}',0,"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,"
                "'generations.v1')"
            ),
            {
                "outbox_id": f"outbox-{suffix}",
                "task_id": f"task-{suffix}",
                "outbox_key": f"outbox-key-{suffix}",
            },
        )


def _affinities(engine) -> tuple[tuple[str, str], ...]:
    with engine.connect() as connection:
        task_rows = connection.execute(
            text(
                "SELECT id, relay_backend_id FROM generation_tasks ORDER BY id"
            )
        ).all()
        outbox_rows = connection.execute(
            text(
                "SELECT id, relay_backend_id FROM relay_submission_outbox ORDER BY id"
            )
        ).all()
    return tuple((str(row[0]), str(row[1])) for row in (*task_rows, *outbox_rows))


def test_sqlite_0039_preserves_history_and_switches_only_future_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "new-api-relay-defaults.db"
    config = _config(project_root, database_path)

    command.upgrade(config, PREVIOUS_HEAD)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == PREVIOUS_HEAD
    assert _backend_default(engine, "generation_tasks") == LEGACY_BACKEND_ID
    assert _backend_default(engine, "relay_submission_outbox") == LEGACY_BACKEND_ID
    _insert_historical_legacy_rows(engine)
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == CURRENT_HEAD
    assert _backend_default(engine, "generation_tasks") == NEW_API_BACKEND_ID
    assert _backend_default(engine, "relay_submission_outbox") == NEW_API_BACKEND_ID
    assert _affinities(engine) == (
        ("legacy-task", LEGACY_BACKEND_ID),
        ("legacy-outbox", LEGACY_BACKEND_ID),
    )
    _insert_defaulted_rows(engine, suffix="a")
    assert ("task-a", NEW_API_BACKEND_ID) in _affinities(engine)
    assert ("outbox-a", NEW_API_BACKEND_ID) in _affinities(engine)
    engine.dispose()

    command.downgrade(config, PREVIOUS_HEAD)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == PREVIOUS_HEAD
    assert _backend_default(engine, "generation_tasks") == LEGACY_BACKEND_ID
    assert _backend_default(engine, "relay_submission_outbox") == LEGACY_BACKEND_ID
    assert ("task-a", NEW_API_BACKEND_ID) in _affinities(engine)
    assert ("outbox-a", NEW_API_BACKEND_ID) in _affinities(engine)
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == CURRENT_HEAD
    assert ("legacy-task", LEGACY_BACKEND_ID) in _affinities(engine)
    assert ("legacy-outbox", LEGACY_BACKEND_ID) in _affinities(engine)
    engine.dispose()
    command.check(config)


def test_0039_migration_contains_no_affinity_rewrite() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0039_new_api_relay_defaults.py"
    ).read_text(encoding="utf-8")
    assert "UPDATE generation_tasks" not in source
    assert "UPDATE relay_submission_outbox" not in source


def test_orm_and_metadata_defaults_match_the_native_database_default() -> None:
    for model in (GenerationTask, RelaySubmissionOutbox):
        column = model.__table__.c.relay_backend_id
        assert column.default is not None
        assert column.default.arg == NEW_API_BACKEND_ID
        assert column.server_default is not None
        assert str(column.server_default.arg) == NEW_API_BACKEND_ID
