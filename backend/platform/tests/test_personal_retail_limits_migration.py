from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from platform_api import database_privileges as facade


PREVIOUS = "0057_execution_integrity"
HEAD = "0058_personal_limits"


def _config(url: str) -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _revision(engine) -> str:
    with engine.connect() as connection:
        return str(
            connection.scalar(text("SELECT version_num FROM alembic_version"))
        )


def _migration_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0058_personal_retail_usage_limits.py"
    )
    spec = importlib.util.spec_from_file_location(
        "personal_retail_usage_limits_migration_test", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_personal_retail_limits_upgrade_preserves_grant_and_enforces_positive(
    tmp_path, monkeypatch
):
    url = "sqlite+pysqlite:///" + (tmp_path / "personal-limits.db").as_posix()
    monkeypatch.setenv("DATABASE_URL", url)
    config = _config(url)
    command.upgrade(config, PREVIOUS)
    engine = create_engine(url)
    model_id = str(uuid4())
    grant_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO model_definitions "
                "(id, slug, display_name, provider_key, billing_mode, "
                "capability_version, active, created_at, updated_at) VALUES "
                "(:id, :slug, 'Migration Model', 'migration-provider', "
                "'per_second', 1, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": model_id, "slug": f"migration-{model_id}"},
        )
        connection.execute(
            text(
                "INSERT INTO personal_retail_model_grants "
                "(id, model_id, enabled, price_per_second_points, "
                "price_per_item_points, config_override, created_at, updated_at) "
                "VALUES (:id, :model_id, true, 1, NULL, '{}', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": grant_id, "model_id": model_id},
        )
    assert {
        column["name"]
        for column in inspect(engine).get_columns("personal_retail_model_grants")
    }.isdisjoint({"call_quota", "concurrency_limit"})
    engine.dispose()

    command.upgrade(config, HEAD)
    engine = create_engine(url)
    assert _revision(engine) == HEAD
    assert {
        "call_quota",
        "concurrency_limit",
    } <= {
        column["name"]
        for column in inspect(engine).get_columns("personal_retail_model_grants")
    }
    with engine.connect() as connection:
        preserved = connection.execute(
            text(
                "SELECT call_quota, concurrency_limit "
                "FROM personal_retail_model_grants WHERE id=:id"
            ),
            {"id": grant_id},
        ).one()
    assert tuple(preserved) == (None, None)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE personal_retail_model_grants "
                "SET call_quota=2, concurrency_limit=1 WHERE id=:id"
            ),
            {"id": grant_id},
        )
    for field in ("call_quota", "concurrency_limit"):
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        f"UPDATE personal_retail_model_grants SET {field}=0 "
                        "WHERE id=:id"
                    ),
                    {"id": grant_id},
                )
        except IntegrityError:
            pass
        else:  # pragma: no cover - makes a missing database constraint explicit.
            raise AssertionError(f"{field} accepted a non-positive value")
    engine.dispose()

    # This test owns the 0058 transition, while the repository may already
    # have a later Alembic head.  Current-head/autogenerate consistency is
    # covered by the latest migration test; do not make this frozen revision
    # masquerade as the repository head.
    assert ScriptDirectory.from_config(config).get_current_head() == (
        facade.PLATFORM_ALEMBIC_HEAD
    )

    with pytest.raises(
        RuntimeError,
        match="downgrade is blocked while personal usage limits exist",
    ):
        command.downgrade(config, PREVIOUS)
    engine = create_engine(url)
    assert _revision(engine) == HEAD
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE personal_retail_model_grants "
                "SET call_quota=NULL, concurrency_limit=NULL WHERE id=:id"
            ),
            {"id": grant_id},
        )
    engine.dispose()

    command.downgrade(config, PREVIOUS)
    engine = create_engine(url)
    assert _revision(engine) == PREVIOUS
    assert {
        column["name"]
        for column in inspect(engine).get_columns("personal_retail_model_grants")
    }.isdisjoint({"call_quota", "concurrency_limit"})
    engine.dispose()


@pytest.mark.parametrize(
    ("dialect", "expected_prefix"),
    (
        ("postgresql", ["lock", "check", "batch"]),
        ("sqlite", ["check", "batch"]),
    ),
)
def test_personal_retail_limits_downgrade_locks_before_check_only_on_postgres(
    monkeypatch, dialect, expected_prefix
):
    migration = _migration_module()
    events: list[str] = []

    class RecordingConnection:
        def __init__(self, dialect_name: str):
            self.dialect = SimpleNamespace(name=dialect_name)

        def execute(self, statement):
            assert str(statement) == (
                "LOCK TABLE personal_retail_model_grants "
                "IN ACCESS EXCLUSIVE MODE"
            )
            events.append("lock")

        def scalar(self, statement):
            assert "SELECT count(*) FROM personal_retail_model_grants" in str(
                statement
            )
            events.append("check")
            return 0

    class RecordingBatch:
        def __enter__(self):
            events.append("batch")
            return self

        def __exit__(self, *_):
            return False

        def drop_constraint(self, *_args, **_kwargs):
            pass

        def drop_column(self, *_args, **_kwargs):
            pass

    connection = RecordingConnection(dialect)
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
    monkeypatch.setattr(
        migration.op,
        "batch_alter_table",
        lambda table: RecordingBatch(),
    )

    migration.downgrade()

    assert events[: len(expected_prefix)] == expected_prefix
