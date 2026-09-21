from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


HEAD = "0051_provider_account_evidence"


def _provider_account_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / f"{HEAD}.py"
    )
    spec = importlib.util.spec_from_file_location(HEAD, migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0051_postgres_sha256_check_keeps_regex_quantifier_literal() -> None:
    migration = _provider_account_migration_module()
    assert migration._sha256_hex_check(
        column="publication_receipt_sha256", dialect="postgresql"
    ) == "publication_receipt_sha256 ~ '^[0-9a-f]{64}$'"
    assert "NOT GLOB" in migration._sha256_hex_check(
        column="publication_receipt_sha256", dialect="sqlite"
    )


def _config(database_url: str) -> Config:
    platform_root = Path(__file__).resolve().parents[1]
    config = Config(str(platform_root / "alembic.ini"))
    config.set_main_option("script_location", str(platform_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_model_commercial_release_migration_is_immutable_and_reversible(
    tmp_path,
) -> None:
    database_path = tmp_path / "commercial-release.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = _config(database_url)
    # Includes the later route-identity/revision guards asserted below, without
    # attempting to downgrade through the irreversible 0056 billing boundary.
    command.upgrade(config, "0055_commercial_plan_revisions")
    engine = create_engine(database_url)
    try:
        table_names = set(inspect(engine).get_table_names())
        assert "model_commercial_release_plans" in table_names
        assert "model_commercial_release_executions" in table_names
        plan_indexes = {
            index["name"]: tuple(index["column_names"])
            for index in inspect(engine).get_indexes(
                "model_commercial_release_plans"
            )
        }
        assert plan_indexes == {
            "ix_model_commercial_plan_model_created": (
                "model_id",
                "created_at",
                "id",
            ),
            "ix_model_commercial_release_plans_model_id": ("model_id",),
        }
        with engine.begin() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0055_commercial_plan_revisions"
            triggers = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='trigger' "
                        "AND tbl_name='model_commercial_release_plans'"
                    )
                )
            }
            assert triggers == {
                "trg_model_commercial_release_plan_no_update",
                "trg_model_commercial_release_plan_no_delete",
                "trg_model_commercial_plan_route_identity_insert",
                "trg_commercial_plan_revision_insert",
            }
    finally:
        engine.dispose()

    # Empty state can round-trip; durable plans deliberately block downgrade.
    command.downgrade(config, "0049_payment_finance_closure")
    engine = create_engine(database_url)
    try:
        table_names = set(inspect(engine).get_table_names())
        assert "model_commercial_release_plans" not in table_names
        assert "model_commercial_release_executions" not in table_names
    finally:
        engine.dispose()
    command.upgrade(config, "head")
    command.check(config)


def test_0051_provider_identity_is_validated_immutable_and_blocks_downgrade(
    tmp_path,
) -> None:
    database_path = tmp_path / "provider-account-evidence.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = _config(database_url)
    command.upgrade(config, HEAD)
    engine = create_engine(database_url)
    valid_values = {
        "id": "11111111-1111-4111-8111-111111111111",
        "schema_version": 2,
        "company_id": "22222222-2222-4222-8222-222222222222",
        "task_id": "33333333-3333-4333-8333-333333333333",
        "relay_job_id": "44444444-4444-4444-8444-444444444444",
        "stage": "artifact_stored",
        "occurred_at": "2026-09-02 00:00:00",
        "channel_key": "official.google-account-a",
        "channel_type": "official",
        "route_id": 71,
        "provider_identity_status": "bound",
        "provider_name": "google_gemini",
        "provider_account_id": "google-account-a",
        "provider_channel_id": 990001,
        "provider_route_id": 71,
        "provider_key_index": 0,
        "provider_key_fingerprint": "a" * 64,
        "provider_credential_version": "55555555-5555-4555-8555-555555555555",
        "routing_release_sha256": "sha256:" + ("b" * 64),
        "provider_task_id": "provider-task-1",
        "duration_ms": 1,
        "error_code": "",
        "delivery_timestamp": "2026-09-02 00:00:01",
        "payload_sha256": "c" * 64,
        "request_id": "request-1",
        "received_at": "2026-09-02 00:00:02",
    }
    insert = text(
        "INSERT INTO relay_task_stage_events ("
        + ",".join(valid_values)
        + ") VALUES ("
        + ",".join(f":{name}" for name in valid_values)
        + ")"
    )
    try:
        with engine.begin() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == HEAD
            trigger_names = {
                str(row[0])
                for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type='trigger'")
                )
            }
            assert {
                "trg_relay_task_stage_events_provider_identity_insert",
                "trg_relay_task_stage_events_no_update",
                "trg_channel_cost_entries_provider_identity_insert",
                "trg_channel_cost_entries_no_update",
                "trg_generation_task_provider_route_immutable",
                "trg_model_commercial_execution_receipt_immutable",
            }.issubset(trigger_names)
            connection.execute(insert, valid_values)
            with pytest.raises(
                IntegrityError, match="provider account evidence is invalid"
            ):
                connection.execute(
                    insert,
                    {
                        **valid_values,
                        "id": "66666666-6666-4666-8666-666666666666",
                        "provider_key_fingerprint": "not-a-sha256",
                    },
                )
            with pytest.raises(IntegrityError, match="relay telemetry is immutable"):
                connection.execute(
                    text(
                        "UPDATE relay_task_stage_events SET provider_account_id="
                        "'google-account-b' WHERE id=:event_id"
                    ),
                    {"event_id": valid_values["id"]},
                )
    finally:
        engine.dispose()

    with pytest.raises(
        RuntimeError, match="downgrade blocked by immutable provider account evidence"
    ):
        command.downgrade(config, "0050_model_commercial_release")
    command.upgrade(config, "head")
    command.check(config)
