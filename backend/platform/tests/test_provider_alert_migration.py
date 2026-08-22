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
    config.set_main_option(
        "sqlalchemy.url", f"sqlite:///{database_path.as_posix()}"
    )
    return config


def _revision(engine) -> str:
    with engine.connect() as connection:
        return str(connection.scalar(text("SELECT version_num FROM alembic_version")))


def _insert_alert(
    engine,
    *,
    event_id: str,
    digest: str = "a" * 64,
    success_count: int = 8,
) -> None:
    now = datetime(2026, 8, 12, 1, 2, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO relay_provider_alert_events "
                "(id, schema_version, event_type, occurred_at, incident_kind, "
                "incident_state, provider_name, generation, reason_code, "
                "sample_size, success_count, affected_routes, total_routes, "
                "success_rate_basis_points, delivery_timestamp, payload_sha256, "
                "request_id, received_at) VALUES "
                "(:id, 1, 'provider_monitor.success_rate_drop.triggered', :now, "
                "'success_rate_drop', 'triggered', 'aliyun', 1, "
                "'success_rate_low', 20, :success_count, 2, 3, 4000, :now, :digest, "
                "'migration-test', :now)"
            ),
            {
                "id": event_id,
                "now": now,
                "digest": digest,
                "success_count": success_count,
            },
        )


def test_0028_provider_alert_receipts_are_immutable_and_round_trip(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "provider-alert-0028.db"
    config = _config(project_root, database_path)

    command.upgrade(config, "0027_channel_cost_evidence")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert "relay_provider_alert_events" not in inspect(engine).get_table_names()
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == "0040_showcase_management"
    assert "relay_provider_alert_events" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        trigger_names = set(
            connection.scalars(
                text(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND tbl_name = 'relay_provider_alert_events'"
                )
            )
        )
    assert trigger_names == {
        "trg_relay_provider_alert_events_no_update",
        "trg_relay_provider_alert_events_no_delete",
    }
    event_id = "11111111-1111-4111-8111-111111111111"
    _insert_alert(engine, event_id=event_id)
    with pytest.raises(
        IntegrityError, match="relay provider alert events are immutable"
    ):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE relay_provider_alert_events SET reason_code = 'changed' "
                    "WHERE id = :id"
                ),
                {"id": event_id},
            )
    with pytest.raises(
        IntegrityError, match="relay provider alert events are immutable"
    ):
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM relay_provider_alert_events WHERE id = :id"),
                {"id": event_id},
            )
    with pytest.raises(IntegrityError, match="ck_relay_provider_alert_metrics"):
        _insert_alert(
            engine,
            event_id="22222222-2222-4222-8222-222222222222",
            digest="b" * 64,
            success_count=21,
        )
    with pytest.raises(
        IntegrityError, match="ck_relay_provider_alert_payload_sha256"
    ):
        _insert_alert(
            engine,
            event_id="33333333-3333-4333-8333-333333333333",
            digest="z" * 64,
        )
    engine.dispose()

    command.downgrade(config, "0027_channel_cost_evidence")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == "0027_channel_cost_evidence"
    assert "relay_provider_alert_events" not in inspect(engine).get_table_names()
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == "0040_showcase_management"
    engine.dispose()
