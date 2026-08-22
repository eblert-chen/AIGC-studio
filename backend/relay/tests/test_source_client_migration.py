from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def _migration_config(project_root: Path, database_path: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
    )
    return config


def test_0006_source_client_identity_upgrades_and_downgrades_sqlite(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("RELAY_DATABASE_URL", raising=False)
    database_path = tmp_path / "relay-0006.db"
    sync_url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(sync_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE generation_jobs (id VARCHAR(36) PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE alembic_version "
            "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            "INSERT INTO alembic_version (version_num) "
            "VALUES ('0005_provider_polling')"
        )
    engine.dispose()

    project_root = Path(__file__).resolve().parents[1]
    config = _migration_config(project_root, database_path)
    command.upgrade(config, "0006_source_client_identity")

    engine = create_engine(sync_url)
    inspector = inspect(engine)
    columns = {item["name"]: item for item in inspector.get_columns("generation_jobs")}
    indexes = {item["name"] for item in inspector.get_indexes("generation_jobs")}
    with engine.connect() as connection:
        revision = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
    assert revision == "0006_source_client_identity"
    assert columns["source_client_id"]["nullable"] is True
    assert columns["source_client_id"]["type"].length == 128
    assert "ix_generation_jobs_source_client_id" in indexes
    engine.dispose()

    command.downgrade(config, "0005_provider_polling")
    engine = create_engine(sync_url)
    inspector = inspect(engine)
    assert "source_client_id" not in {
        item["name"] for item in inspector.get_columns("generation_jobs")
    }
    assert "ix_generation_jobs_source_client_id" not in {
        item["name"] for item in inspector.get_indexes("generation_jobs")
    }
    engine.dispose()


def test_empty_sqlite_database_upgrades_through_head(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("RELAY_DATABASE_URL", raising=False)
    database_path = tmp_path / "relay-full-chain.db"
    project_root = Path(__file__).resolve().parents[1]
    config = _migration_config(project_root, database_path)

    command.upgrade(config, "head")
    command.check(config)

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    columns = {item["name"] for item in inspector.get_columns("generation_jobs")}
    callback_columns = {
        item["name"] for item in inspector.get_columns("callback_deliveries")
    }
    with engine.connect() as connection:
        revision = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
        trigger_names = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
    assert revision == "0012_generation_contract_v1"
    assert {
        "expected_capability_revision",
        "source_client_id",
        "provider_poll_failures",
        "provider_next_poll_at",
        "provider_last_poll_error",
        "provider_poll_claim_token",
        "provider_poll_claim_expires_at",
        "transfer_claim_token",
        "transfer_claim_expires_at",
    } <= columns
    assert "claim_token" in callback_columns
    table_names = set(inspector.get_table_names())
    assert {
        "provider_account_states",
        "provider_health_samples",
        "provider_outcome_events",
        "provider_alert_states",
        "provider_alert_events",
        "provider_monitor_lease",
    } <= table_names
    account_columns = {
        item["name"]
        for item in inspector.get_columns("provider_account_states")
    }
    assert "admission_disabled_reason" in account_columns
    monitor_columns = {
        item["name"]
        for item in inspector.get_columns("provider_monitor_lease")
    }
    assert "last_successful_cycle_at" in monitor_columns
    assert {
        "trg_generation_jobs_capability_revision_immutable",
        "trg_provider_outcome_events_no_update",
        "trg_provider_outcome_events_no_delete",
    } <= trigger_names
    engine.dispose()
