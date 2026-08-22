from __future__ import annotations

import os
from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError


POSTGRES_URL = os.getenv("PLATFORM_TEST_DATABASE_URL") or os.getenv(
    "DATABASE_URL", ""
)


def _config(project_root: Path, database_url: str) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    # Alembic stores main options in ConfigParser, where a percent sign starts
    # interpolation. PostgreSQL search_path options are URL encoded (``%3D``),
    # so escape them before assigning the DSN or this real-database gate fails
    # before the migration is ever exercised.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _revision(engine) -> str:
    with engine.connect() as connection:
        return str(connection.scalar(text("SELECT version_num FROM alembic_version")))


def _sqlite_triggers(engine) -> set[str]:
    with engine.connect() as connection:
        return {
            str(name)
            for name in connection.scalars(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            )
        }


def _assert_personal_scope_schema(engine) -> None:
    inspector = inspect(engine)
    assert {
        "personal_workspaces",
        "personal_wallet_accounts",
        "personal_retail_model_grants",
        "personal_download_records",
        "personal_ledger_entries",
    } <= set(inspector.get_table_names())
    for table_name in (
        "generation_tasks",
        "task_artifacts",
        "relay_submission_outbox",
        "relay_callback_events",
        "channel_cost_entries",
        "task_timeout_events",
    ):
        assert "personal_workspace_id" in {
            column["name"] for column in inspector.get_columns(table_name)
        }

    expected_personal_fks = {
        "generation_tasks": "fk_generation_task_personal_workspace",
        "task_artifacts": "fk_task_artifact_personal_workspace",
        "relay_submission_outbox": "fk_relay_outbox_personal_workspace",
        "relay_callback_events": "fk_relay_callback_personal_workspace",
        "task_timeout_events": "fk_task_timeout_personal_workspace",
    }
    if engine.dialect.name == "postgresql":
        expected_personal_fks["channel_cost_entries"] = (
            "fk_channel_cost_personal_workspace"
        )
    for table_name, constraint_name in expected_personal_fks.items():
        assert constraint_name in {
            item["name"] for item in inspector.get_foreign_keys(table_name)
        }


def _insert_user_and_personal_ledger(engine) -> str:
    user_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, email, display_name, is_platform_admin, status, auth_version, "
                "created_at, updated_at) "
                "VALUES (:id, :email, 'Personal Migration', false, 'ACTIVE', 1, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": user_id, "email": f"{user_id}@example.test"},
        )
        connection.execute(
            text(
                "INSERT INTO personal_workspaces "
                "(id, user_id, active, created_at, updated_at) "
                "VALUES (:id, :user_id, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": workspace_id, "user_id": user_id},
        )
        connection.execute(
            text(
                "INSERT INTO personal_wallet_accounts "
                "(workspace_id, available_points, reserved_points, "
                "created_at, updated_at) VALUES "
                "(:workspace_id, 10, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"workspace_id": workspace_id},
        )
        connection.execute(
            text(
                "INSERT INTO personal_ledger_entries "
                "(id, workspace_id, kind, amount_points, "
                "available_delta_points, reserved_delta_points, "
                "idempotency_key, task_id, note, created_at) VALUES "
                "(:id, :workspace_id, 'RECHARGE', 10, 10, 0, "
                "'personal-migration-credit', NULL, 'migration test', "
                "CURRENT_TIMESTAMP)"
            ),
            {"id": str(uuid.uuid4()), "workspace_id": workspace_id},
        )
    return workspace_id


def _assert_personal_ledger_is_immutable(engine, workspace_id: str) -> None:
    for statement in (
        "UPDATE personal_ledger_entries SET note = 'changed' "
        "WHERE workspace_id = :workspace_id",
        "DELETE FROM personal_ledger_entries WHERE workspace_id = :workspace_id",
    ):
        with pytest.raises(DBAPIError, match="personal ledger entries are immutable"):
            with engine.begin() as connection:
                connection.execute(text(statement), {"workspace_id": workspace_id})


def test_sqlite_personal_workspace_migration_round_trip_preserves_guards(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "personal-workspaces.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = _config(project_root, database_url)

    command.upgrade(config, "0033_relay_backend_affinity")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, email, display_name, is_platform_admin, created_at, updated_at) "
                "VALUES ('historical-personal-user', 'historical@example.test', "
                "'Historical User', false, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    assert _revision(engine) == "0040_showcase_management"
    _assert_personal_scope_schema(engine)
    with engine.connect() as connection:
        backfilled = connection.execute(
            text(
                "SELECT w.id, a.available_points, a.reserved_points "
                "FROM personal_workspaces w "
                "JOIN personal_wallet_accounts a ON a.workspace_id = w.id "
                "WHERE w.user_id = 'historical-personal-user'"
            )
        ).one()
        assert tuple(backfilled[1:]) == (0, 0)
    workspace_id = _insert_user_and_personal_ledger(engine)
    _assert_personal_ledger_is_immutable(engine, workspace_id)
    assert {
        "trg_personal_ledger_entries_no_update",
        "trg_personal_ledger_entries_no_delete",
        "trg_personal_download_records_no_update",
        "trg_personal_download_records_no_delete",
        "trg_task_artifacts_no_update",
        "trg_task_artifacts_no_delete",
        "trg_task_artifacts_size_positive_insert",
        "trg_channel_cost_personal_workspace_fk",
    } <= _sqlite_triggers(engine)
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.execute(
                text(
                    "INSERT INTO personal_wallet_accounts "
                    "(workspace_id, available_points, reserved_points, "
                    "created_at, updated_at) VALUES "
                    "('missing-workspace', 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
    engine.dispose()

    command.downgrade(config, "0033_relay_backend_affinity")
    engine = create_engine(database_url)
    assert _revision(engine) == "0033_relay_backend_affinity"
    assert "personal_workspaces" not in inspect(engine).get_table_names()
    assert {
        "trg_task_artifacts_no_update",
        "trg_task_artifacts_no_delete",
        "trg_task_artifacts_size_positive_insert",
    } <= _sqlite_triggers(engine)
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    assert _revision(engine) == "0040_showcase_management"
    _assert_personal_scope_schema(engine)
    engine.dispose()


def test_postgres_personal_workspace_migration_guards_and_round_trip(
    monkeypatch,
) -> None:
    if not POSTGRES_URL.startswith("postgresql"):
        pytest.skip("requires a PostgreSQL test database")

    schema_name = f"personal_migration_{uuid.uuid4().hex}"
    administration_engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    with administration_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema_name}"')
    schema_url = (
        make_url(POSTGRES_URL)
        .update_query_dict({"options": f"-csearch_path={schema_name}"})
        .render_as_string(hide_password=False)
    )
    engine = create_engine(schema_url, pool_pre_ping=True)
    project_root = Path(__file__).resolve().parents[1]
    config = _config(project_root, schema_url)
    monkeypatch.setenv("DATABASE_URL", schema_url)

    try:
        command.upgrade(config, "head")
        assert _revision(engine) == "0040_showcase_management"
        _assert_personal_scope_schema(engine)
        workspace_id = _insert_user_and_personal_ledger(engine)
        _assert_personal_ledger_is_immutable(engine, workspace_id)
        with engine.connect() as connection:
            trigger_names = {
                str(name)
                for name in connection.scalars(
                    text(
                        "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal "
                        "AND tgrelid IN "
                        "('personal_ledger_entries'::regclass, "
                        "'personal_download_records'::regclass, "
                        "'task_artifacts'::regclass)"
                    )
                )
            }
        assert {
            "trg_personal_ledger_entries_immutable",
            "trg_personal_ledger_entries_no_truncate",
            "trg_personal_download_records_immutable",
            "trg_personal_download_records_no_truncate",
            "trg_task_artifacts_immutable",
            "trg_task_artifacts_no_truncate",
        } <= trigger_names
        with pytest.raises(DBAPIError, match="personal ledger entries are immutable"):
            with engine.begin() as connection:
                # CASCADE gets past PostgreSQL's inbound-FK precondition so the
                # table's own statement trigger is what proves truncation is
                # rejected (and also covers a caller trying to erase dependents).
                connection.execute(
                    text("TRUNCATE TABLE personal_ledger_entries CASCADE")
                )

        command.downgrade(config, "0033_relay_backend_affinity")
        assert _revision(engine) == "0033_relay_backend_affinity"
        assert "personal_workspaces" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        assert _revision(engine) == "0040_showcase_management"
        _assert_personal_scope_schema(engine)
    finally:
        engine.dispose()
        with administration_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema_name}" CASCADE')
        administration_engine.dispose()
