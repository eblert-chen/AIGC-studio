from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
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


def _cost_trigger_names(engine) -> set[str]:
    with engine.connect() as connection:
        return {
            str(name)
            for name in connection.scalars(
                text(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND tbl_name = 'channel_cost_entries'"
                )
            )
        }


def _insert_cost(engine, *, entry_id: str, amount_cents: int) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO channel_cost_entries "
                "(id, amount_cents, idempotency_key, channel_key, channel_type, "
                "occurred_at, external_reference, company_id, task_id, "
                "relay_job_id, note, source, recorded_by_user_id, created_at) "
                "VALUES (:id, :amount_cents, :idempotency_key, 'official.demo', "
                "'OFFICIAL', :occurred_at, :external_reference, NULL, NULL, NULL, "
                "'', 'RELAY', NULL, :created_at)"
            ),
            {
                "id": entry_id,
                "amount_cents": amount_cents,
                "idempotency_key": f"idempotency-{entry_id}",
                "external_reference": f"proof-{entry_id}",
                "occurred_at": datetime(2026, 8, 5, 8, 0, tzinfo=timezone.utc),
                "created_at": datetime(2026, 8, 5, 8, 1, tzinfo=timezone.utc),
            },
        )


def _insert_cost_evidence(
    engine,
    *,
    entry_id: str,
    relay_event_id: str | None,
    relay_event_timestamp: datetime | None,
    relay_payload_sha256: str | None,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO channel_cost_entries "
                "(id, amount_cents, idempotency_key, channel_key, channel_type, "
                "occurred_at, external_reference, company_id, task_id, "
                "relay_job_id, relay_event_id, relay_event_timestamp, "
                "relay_payload_sha256, note, source, recorded_by_user_id, "
                "created_at) VALUES "
                "(:id, 25, :idempotency_key, 'official.demo', 'OFFICIAL', "
                ":occurred_at, :external_reference, NULL, NULL, NULL, "
                ":relay_event_id, :relay_event_timestamp, "
                ":relay_payload_sha256, '', 'RELAY', NULL, :created_at)"
            ),
            {
                "id": entry_id,
                "idempotency_key": f"idempotency-{entry_id}",
                "external_reference": f"proof-{entry_id}",
                "occurred_at": datetime(2026, 8, 5, 8, 0, tzinfo=timezone.utc),
                "created_at": datetime(2026, 8, 5, 8, 1, tzinfo=timezone.utc),
                "relay_event_id": relay_event_id,
                "relay_event_timestamp": relay_event_timestamp,
                "relay_payload_sha256": relay_payload_sha256,
            },
        )


def test_0015_channel_cost_upgrade_guards_and_signed_amounts(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "channel-cost-0015.db"
    config = _config(project_root, database_path)

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == "0040_showcase_management"
    assert "channel_cost_entries" in inspect(engine).get_table_names()
    amount_column = next(
        column
        for column in inspect(engine).get_columns("channel_cost_entries")
        if column["name"] == "amount_cents"
    )
    assert isinstance(amount_column["type"], sa.BigInteger)
    assert _cost_trigger_names(engine) == {
        "trg_channel_cost_entries_no_update",
        "trg_channel_cost_entries_no_delete",
        "trg_channel_cost_personal_workspace_fk",
    }

    _insert_cost(engine, entry_id="positive-cost", amount_cents=125)
    _insert_cost(engine, entry_id="zero-cost", amount_cents=0)
    _insert_cost(engine, entry_id="refund-cost", amount_cents=-25)
    with pytest.raises(IntegrityError, match="ck_channel_cost_amount_range"):
        _insert_cost(
            engine,
            entry_id="outside-cost-range",
            amount_cents=9_000_000_000_000_001,
        )
    for statement in (
        "UPDATE channel_cost_entries SET amount_cents = 1 "
        "WHERE id = 'positive-cost'",
        "DELETE FROM channel_cost_entries WHERE id = 'positive-cost'",
    ):
        with pytest.raises(
            IntegrityError, match="channel cost entries are immutable"
        ):
            with engine.begin() as connection:
                connection.execute(text(statement))
    engine.dispose()

    command.downgrade(config, "0014_billing_report_hardening")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == "0014_billing_report_hardening"
    assert "channel_cost_entries" not in inspect(engine).get_table_names()
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == "0040_showcase_management"
    assert _cost_trigger_names(engine) == {
        "trg_channel_cost_entries_no_update",
        "trg_channel_cost_entries_no_delete",
        "trg_channel_cost_personal_workspace_fk",
    }
    engine.dispose()


def test_0019_preserves_historical_costs_and_rolls_back_without_data_loss(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "channel-cost-0019-history.db"
    config = _config(project_root, database_path)

    command.upgrade(config, "0018_relay_contract")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    _insert_cost(engine, entry_id="historical-cost", amount_cents=75)
    engine.dispose()

    command.upgrade(config, "0019_channel_cost_evidence")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    columns = {
        column["name"]
        for column in inspect(engine).get_columns("channel_cost_entries")
    }
    assert {
        "relay_event_id",
        "relay_event_timestamp",
        "relay_payload_sha256",
    } <= columns
    assert any(
        index["name"] == "uq_channel_cost_relay_event_id"
        and index["unique"]
        for index in inspect(engine).get_indexes("channel_cost_entries")
    )
    with engine.connect() as connection:
        historical = connection.execute(
            text(
                "SELECT amount_cents, relay_event_id, relay_event_timestamp, "
                "relay_payload_sha256 FROM channel_cost_entries "
                "WHERE id = 'historical-cost'"
            )
        ).one()
    assert historical == (75, None, None, None)

    valid_timestamp = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    _insert_cost_evidence(
        engine,
        entry_id="valid-evidence",
        relay_event_id="11111111-1111-4111-8111-111111111111",
        relay_event_timestamp=valid_timestamp,
        relay_payload_sha256="a" * 64,
    )
    with pytest.raises(
        IntegrityError,
        match="ck_channel_cost_relay_evidence_complete",
    ):
        _insert_cost_evidence(
            engine,
            entry_id="partial-evidence",
            relay_event_id="22222222-2222-4222-8222-222222222222",
            relay_event_timestamp=None,
            relay_payload_sha256=None,
        )
    with pytest.raises(
        IntegrityError,
        match="ck_channel_cost_relay_event_id_format",
    ):
        _insert_cost_evidence(
            engine,
            entry_id="invalid-event-id",
            relay_event_id="gggggggg-gggg-4ggg-8ggg-gggggggggggg",
            relay_event_timestamp=valid_timestamp,
            relay_payload_sha256="b" * 64,
        )
    with pytest.raises(
        IntegrityError,
        match="ck_channel_cost_relay_payload_sha256",
    ):
        _insert_cost_evidence(
            engine,
            entry_id="invalid-payload-digest",
            relay_event_id="33333333-3333-4333-8333-333333333333",
            relay_event_timestamp=valid_timestamp,
            relay_payload_sha256="C" * 64,
        )
    engine.dispose()

    command.downgrade(config, "0018_relay_contract")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    columns = {
        column["name"]
        for column in inspect(engine).get_columns("channel_cost_entries")
    }
    assert "relay_event_id" not in columns
    with engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT amount_cents FROM channel_cost_entries "
                "WHERE id = 'historical-cost'"
            )
        ) == 75
    assert _cost_trigger_names(engine) == {
        "trg_channel_cost_entries_no_update",
        "trg_channel_cost_entries_no_delete",
    }
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == "0040_showcase_management"
    with engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT count(*) FROM channel_cost_entries "
                "WHERE id = 'historical-cost' AND relay_event_id IS NULL"
            )
        ) == 1
    engine.dispose()


def test_0027_adds_document_evidence_without_rewriting_historical_costs(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "channel-cost-0027-document-evidence.db"
    config = _config(project_root, database_path)

    command.upgrade(config, "0026_relay_telemetry")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    _insert_cost(engine, entry_id="historical-document-cost", amount_cents=88)
    engine.dispose()

    command.upgrade(config, "0027_channel_cost_evidence")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    columns = {
        column["name"]
        for column in inspect(engine).get_columns("channel_cost_entries")
    }
    assert {
        "evidence_source",
        "evidence_reference",
        "source_document_sha256",
    } <= columns
    with engine.connect() as connection:
        historical = connection.execute(
            text(
                "SELECT amount_cents, evidence_source, evidence_reference, "
                "source_document_sha256 FROM channel_cost_entries "
                "WHERE id = 'historical-document-cost'"
            )
        ).one()
    assert historical == (88, None, None, None)
    engine.dispose()

    command.downgrade(config, "0026_relay_telemetry")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    columns = {
        column["name"]
        for column in inspect(engine).get_columns("channel_cost_entries")
    }
    assert "evidence_source" not in columns
    with engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT amount_cents FROM channel_cost_entries "
                "WHERE id = 'historical-document-cost'"
            )
        ) == 88
    engine.dispose()
