from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


V2_HEAD = "0037_production_auth_lifecycle"
V3_HEAD = "0038_download_evidence_checks"
CURRENT_HEAD = "0040_showcase_management"
_AFFECTED_TABLES = (
    "channel_cost_entries",
    "download_completions",
    "download_records",
    "task_artifacts",
    "personal_download_records",
)
_CHECK_NAME_PATTERN = re.compile(
    r'\bCONSTRAINT\s+["`]?([A-Za-z0-9_]+)["`]?\s+CHECK\b',
    re.IGNORECASE,
)


def _config(project_root: Path, database_path: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite:///{database_path.as_posix()}",
    )
    return config


def _revision(engine) -> str:
    with engine.connect() as connection:
        return str(
            connection.scalar(text("SELECT version_num FROM alembic_version"))
        )


def _personal_table_sql(engine) -> str:
    with engine.connect() as connection:
        return str(
            connection.scalar(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name='personal_download_records'"
                )
            )
        )


def _personal_trigger_names(engine) -> set[str]:
    with engine.connect() as connection:
        return {
            str(name)
            for name in connection.scalars(
                text(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND tbl_name='personal_download_records'"
                )
            )
        }


def _normalize_sql(value: object) -> str:
    return " ".join(str(value).split())


def _table_state(engine, table_name: str) -> dict[str, Any]:
    inspector = inspect(engine)
    with engine.connect() as connection:
        table_sql = connection.scalar(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = :table_name"
            ),
            {"table_name": table_name},
        )
        triggers = {
            str(row.name): _normalize_sql(row.sql)
            for row in connection.execute(
                text(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE type = 'trigger' AND tbl_name = :table_name "
                    "ORDER BY name"
                ),
                {"table_name": table_name},
            )
        }
    return {
        "table_sql": _normalize_sql(table_sql),
        "columns": tuple(
            (
                column["name"],
                str(column["type"]),
                bool(column["nullable"]),
                column.get("default"),
                bool(column.get("primary_key")),
            )
            for column in inspector.get_columns(table_name)
        ),
        "primary_key": tuple(
            inspector.get_pk_constraint(table_name)["constrained_columns"]
        ),
        "foreign_keys": tuple(
            sorted(
                (
                    tuple(item["constrained_columns"]),
                    item["referred_table"],
                    tuple(item["referred_columns"]),
                    item.get("options", {}).get("ondelete"),
                    item.get("name"),
                )
                for item in inspector.get_foreign_keys(table_name)
            )
        ),
        "unique_constraints": tuple(
            sorted(
                (
                    item.get("name"),
                    tuple(item["column_names"]),
                )
                for item in inspector.get_unique_constraints(table_name)
            )
        ),
        "indexes": tuple(
            sorted(
                (
                    item["name"],
                    tuple(item["column_names"]),
                    bool(item["unique"]),
                )
                for item in inspector.get_indexes(table_name)
            )
        ),
        "checks": {
            str(item["name"]): _normalize_sql(item["sqltext"])
            for item in inspector.get_check_constraints(table_name)
        },
        "triggers": triggers,
    }


def _structural_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in state.items()
        if key not in {"table_sql", "checks", "triggers"}
    }


def _raw_check_names(state: dict[str, Any]) -> set[str]:
    # SQLAlchemy 2.0.36 can concatenate adjacent SQLite column checks while
    # reflecting them. The canonical sqlite_master DDL retains every exact
    # constraint name, so use it for round-trip identity instead of accepting
    # the lossy reflected representation.
    return set(_CHECK_NAME_PATTERN.findall(state["table_sql"]))


def _database_state(engine) -> dict[str, dict[str, Any]]:
    return {
        table_name: _table_state(engine, table_name)
        for table_name in _AFFECTED_TABLES
    }


def _assert_integrity(engine) -> None:
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA integrity_check").all() == [
            ("ok",)
        ]
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def _insert_personal_download(engine, *, digest: str) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(
            text(
                "INSERT INTO personal_download_records "
                "(id,workspace_id,task_id,asset_id,requested_by_user_id,"
                "expires_seconds,expires_at,request_id,storage_provider,"
                "storage_endpoint_host,storage_bucket,storage_object_key,"
                "storage_version_id,source_url_sha256,relay_issued_at,"
                "relay_expires_at,created_at) VALUES "
                "('download-evidence-check','missing-workspace','missing-task',"
                "'asset','missing-user',60,CURRENT_TIMESTAMP,'request',"
                "'huawei_obs','obs.example.test','bucket','object',NULL,:digest,"
                "CURRENT_TIMESTAMP,datetime(CURRENT_TIMESTAMP,'+1 minute'),"
                "CURRENT_TIMESTAMP)"
            ),
            {"digest": digest},
        )


def test_sqlite_0038_strengthens_hash_and_preserves_immutable_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "download-evidence-0038.db"
    config = _config(project_root, database_path)
    command.upgrade(config, V2_HEAD)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == V2_HEAD
    assert "ck_personal_download_source_url_sha_shape" in _personal_table_sql(
        engine
    )
    pre_upgrade = _database_state(engine)
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == CURRENT_HEAD
    post_upgrade = _database_state(engine)
    for table_name in _AFFECTED_TABLES:
        assert _structural_state(post_upgrade[table_name]) == _structural_state(
            pre_upgrade[table_name]
        )
        assert post_upgrade[table_name]["triggers"] == pre_upgrade[table_name][
            "triggers"
        ]
    assert set(post_upgrade["channel_cost_entries"]["checks"]) == {
        "ck_channel_cost_amount_range",
        "ck_channel_cost_relay_event_id_format",
        "ck_channel_cost_relay_evidence_complete",
        "ck_channel_cost_relay_payload_sha256",
    }
    assert set(post_upgrade["download_completions"]["checks"]) == {
        "ck_download_completion_artifact_sha256",
        "ck_download_completion_bytes_nonnegative",
        "ck_download_completion_payload_sha256",
        "ck_download_completion_signed_event_id",
        "ck_download_completion_verified_evidence_complete",
        "ck_download_completion_verified_source",
    }
    assert set(post_upgrade["download_records"]["checks"]) == {
        "ck_download_expiry_positive",
        "ck_download_gateway_ticket_url_sha256_hex",
        "ck_download_source_url_sha256_hex",
        "ck_download_storage_binding_complete",
    }
    assert set(post_upgrade["task_artifacts"]["checks"]) == {
        "ck_task_artifact_media_type",
        "ck_task_artifact_position_nonnegative",
        "ck_task_artifact_scope",
        "ck_task_artifact_size_nonnegative",
        "ck_task_artifact_size_positive",
    }
    table_sql = _personal_table_sql(engine)
    assert "ck_personal_download_source_url_sha_hex" in table_sql
    assert "NOT GLOB '*[^0-9a-f]*'" in table_sql
    assert {
        "trg_personal_download_records_no_update",
        "trg_personal_download_records_no_delete",
    } <= _personal_trigger_names(engine)
    with pytest.raises(IntegrityError):
        _insert_personal_download(engine, digest="g" * 64)
    _assert_integrity(engine)
    engine.dispose()
    command.check(config)

    command.downgrade(config, V2_HEAD)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == V2_HEAD
    assert "ck_personal_download_source_url_sha_shape" in _personal_table_sql(
        engine
    )
    assert {
        "trg_personal_download_records_no_update",
        "trg_personal_download_records_no_delete",
    } <= _personal_trigger_names(engine)
    downgraded = _database_state(engine)
    for table_name in _AFFECTED_TABLES:
        assert _structural_state(downgraded[table_name]) == _structural_state(
            pre_upgrade[table_name]
        )
        assert _raw_check_names(downgraded[table_name]) == _raw_check_names(
            pre_upgrade[table_name]
        )
        assert downgraded[table_name]["triggers"] == pre_upgrade[table_name][
            "triggers"
        ]
    _assert_integrity(engine)
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == CURRENT_HEAD
    upgraded_again = _database_state(engine)
    for table_name in _AFFECTED_TABLES:
        assert _structural_state(upgraded_again[table_name]) == _structural_state(
            post_upgrade[table_name]
        )
        assert upgraded_again[table_name]["checks"] == post_upgrade[table_name][
            "checks"
        ]
        assert upgraded_again[table_name]["triggers"] == post_upgrade[
            table_name
        ]["triggers"]
    _assert_integrity(engine)
    engine.dispose()
    command.check(config)


def test_sqlite_0038_rejects_invalid_inventory_before_schema_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "download-evidence-invalid.db"
    config = _config(project_root, database_path)
    command.upgrade(config, V2_HEAD)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    _insert_personal_download(engine, digest="g" * 64)
    before_failure = _database_state(engine)
    engine.dispose()

    with pytest.raises(
        RuntimeError,
        match="download evidence SHA-256 inventory is invalid",
    ):
        command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == V2_HEAD
    table_sql = _personal_table_sql(engine)
    assert "ck_personal_download_source_url_sha_shape" in table_sql
    assert "ck_personal_download_source_url_sha_hex" not in table_sql
    assert _database_state(engine) == before_failure
    engine.dispose()


def test_sqlite_0038_rejects_nonpositive_artifact_before_any_ddl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "download-evidence-invalid-artifact.db"
    config = _config(project_root, database_path)
    command.upgrade(config, "0021_download_completion_proof")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO task_artifacts ("
                "id, company_id, task_id, asset_id, position, media_type, "
                "content_type, size_bytes, sha256, created_at) VALUES ("
                "'historical-empty-artifact', 'missing-company', "
                "'missing-task', 'empty-artifact', 0, 'video', "
                "'video/mp4', 0, :sha256, CURRENT_TIMESTAMP)"
            ),
            {"sha256": "0" * 64},
        )
    engine.dispose()
    command.upgrade(config, V2_HEAD)

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == V2_HEAD
    before_failure = _database_state(engine)
    assert all(state["triggers"] for state in before_failure.values())
    engine.dispose()

    with pytest.raises(
        RuntimeError,
        match="task artifact size inventory is invalid",
    ):
        command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == V2_HEAD
    assert _database_state(engine) == before_failure
    with engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT size_bytes FROM task_artifacts "
                "WHERE id = 'historical-empty-artifact'"
            )
        ) == 0
    engine.dispose()


def test_sqlite_fresh_0038_is_metadata_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "download-evidence-fresh.db"
    config = _config(project_root, database_path)

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == CURRENT_HEAD
    _assert_integrity(engine)
    engine.dispose()
    command.check(config)


def test_sqlite_0038_rejects_incomplete_trigger_inventory_before_ddl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "download-evidence-missing-trigger.db"
    config = _config(project_root, database_path)
    command.upgrade(config, V2_HEAD)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP TRIGGER trg_channel_cost_entries_no_update"
        )
    before_failure = _database_state(engine)
    engine.dispose()

    with pytest.raises(
        RuntimeError,
        match="SQLite trigger inventory is invalid for channel_cost_entries",
    ):
        command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == V2_HEAD
    assert _database_state(engine) == before_failure
    engine.dispose()
