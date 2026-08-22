from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


MONEY_COLUMNS = {
    "company_model_grants": {
        "price_per_second_cents",
        "price_per_item_cents",
    },
    "wallet_accounts": {"available_cents", "reserved_cents"},
    "generation_tasks": {
        "quote_cents",
        "reserved_cents",
        "actual_cost_cents",
    },
    "ledger_entries": {
        "amount_cents",
        "available_delta_cents",
        "reserved_delta_cents",
    },
    "task_timeout_events": {"released_cents"},
}

LEDGER_TRIGGER_NAMES = {
    "trg_ledger_entries_no_update",
    "trg_ledger_entries_no_delete",
}


def _config(project_root: Path, database_path: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url", f"sqlite:///{database_path.as_posix()}"
    )
    return config


def _revision(engine) -> str:
    with engine.connect() as connection:
        return str(connection.scalar(text("SELECT version_num FROM alembic_version")))


def _trigger_names(engine) -> set[str]:
    with engine.connect() as connection:
        return {
            str(name)
            for name in connection.scalars(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'trigger' AND tbl_name = 'ledger_entries'"
                )
            )
        }


def _grant_constraint_names(engine) -> set[str | None]:
    return {
        item["name"]
        for item in inspect(engine).get_check_constraints(
            "company_model_grants"
        )
    }


def _ledger_index_names(engine) -> set[str | None]:
    return {
        item["name"]
        for item in inspect(engine).get_indexes("ledger_entries")
    }


def _assert_money_columns_are_bigint(engine) -> None:
    inspector = inspect(engine)
    for table_name, expected_columns in MONEY_COLUMNS.items():
        columns = {
            column["name"]: column["type"]
            for column in inspector.get_columns(table_name)
        }
        for column_name in expected_columns:
            column_type = columns[column_name]
            assert isinstance(column_type, sa.BigInteger), (
                f"{table_name}.{column_name} remained {column_type}; "
                "expected a BIGINT-equivalent declared type"
            )


def _assert_money_columns_are_integer(engine) -> None:
    inspector = inspect(engine)
    for table_name, expected_columns in MONEY_COLUMNS.items():
        columns = {
            column["name"]: column["type"]
            for column in inspector.get_columns(table_name)
        }
        for column_name in expected_columns:
            column_type = columns[column_name]
            assert isinstance(column_type, sa.Integer)
            assert not isinstance(column_type, sa.BigInteger)


def _insert_foundation_rows(engine) -> None:
    now = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
    model_ids = (
        "model-per-second",
        "model-per-item",
        "model-no-price",
        "model-two-prices",
        "model-after-rebuild",
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO companies "
                "(id, name, status, created_at, updated_at) "
                "VALUES (:id, :name, :status, :created_at, :updated_at)"
            ),
            {
                "id": "billing-migration-company",
                "name": "Billing Migration Company",
                "status": "ACTIVE",
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO model_definitions "
                "(id, slug, display_name, provider_key, billing_mode, "
                "capability_version, active, created_at, updated_at) VALUES "
                "(:id, :slug, :display_name, :provider_key, :billing_mode, 1, 1, "
                ":created_at, :updated_at)"
            ),
            [
                {
                    "id": model_id,
                    "slug": model_id,
                    "display_name": model_id,
                    "provider_key": "migration-test",
                    "billing_mode": (
                        "per_item" if model_id == "model-per-item" else "per_second"
                    ),
                    "created_at": now,
                    "updated_at": now,
                }
                for model_id in model_ids
            ],
        )


def _insert_grant(
    engine,
    *,
    grant_id: str,
    model_id: str,
    second_price: int | None,
    item_price: int | None,
) -> None:
    now = datetime(2026, 8, 4, 12, 1, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO company_model_grants "
                "(id, company_id, model_id, enabled, "
                "price_per_second_cents, price_per_item_cents, "
                "config_override, created_at, updated_at) VALUES "
                "(:id, 'billing-migration-company', :model_id, 1, "
                ":second_price, :item_price, '{}', :created_at, :updated_at)"
            ),
            {
                "id": grant_id,
                "model_id": model_id,
                "second_price": second_price,
                "item_price": item_price,
                "created_at": now,
                "updated_at": now,
            },
        )


def _insert_ledger(engine, ledger_id: str, note: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ledger_entries "
                "(id, company_id, kind, amount_cents, available_delta_cents, "
                "reserved_delta_cents, idempotency_key, task_id, note, "
                "created_at) VALUES "
                "(:id, 'billing-migration-company', 'RECHARGE', 500, 500, 0, "
                ":idempotency_key, NULL, :note, :created_at)"
            ),
            {
                "id": ledger_id,
                "idempotency_key": f"idempotency-{ledger_id}",
                "note": note,
                "created_at": datetime(
                    2026, 8, 4, 12, 2, tzinfo=timezone.utc
                ),
            },
        )


def _assert_raw_ledger_mutation_is_rejected(engine, ledger_id: str) -> None:
    with pytest.raises(IntegrityError, match="ledger entries are immutable"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE ledger_entries SET note = 'tampered' WHERE id = :id"),
                {"id": ledger_id},
            )
    with pytest.raises(IntegrityError, match="ledger entries are immutable"):
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM ledger_entries WHERE id = :id"),
                {"id": ledger_id},
            )
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT count(*) FROM ledger_entries WHERE id = :id"),
            {"id": ledger_id},
        ) == 1


def test_0013_billing_invariants_upgrade_downgrade_and_reupgrade(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "billing-0013.db"
    config = _config(project_root, database_path)

    command.upgrade(config, "0013_billing_invariants")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == "0013_billing_invariants"
    assert "ck_grant_exactly_one_price" in _grant_constraint_names(engine)
    assert _trigger_names(engine) == LEDGER_TRIGGER_NAMES
    assert "ix_ledger_kind_created" not in _ledger_index_names(engine)
    _assert_money_columns_are_bigint(engine)
    _insert_foundation_rows(engine)

    _insert_grant(
        engine,
        grant_id="valid-per-second",
        model_id="model-per-second",
        second_price=25,
        item_price=None,
    )
    _insert_grant(
        engine,
        grant_id="valid-per-item",
        model_id="model-per-item",
        second_price=None,
        item_price=125,
    )
    with pytest.raises(IntegrityError, match="ck_grant_exactly_one_price"):
        _insert_grant(
            engine,
            grant_id="invalid-no-price",
            model_id="model-no-price",
            second_price=None,
            item_price=None,
        )
    with pytest.raises(IntegrityError, match="ck_grant_exactly_one_price"):
        _insert_grant(
            engine,
            grant_id="invalid-two-prices",
            model_id="model-two-prices",
            second_price=25,
            item_price=125,
        )

    _insert_ledger(engine, "ledger-before-downgrade", "immutable at 0013")
    _assert_raw_ledger_mutation_is_rejected(engine, "ledger-before-downgrade")
    engine.dispose()

    command.downgrade(config, "0012_company_member_levels")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == "0012_company_member_levels"
    assert "ck_grant_exactly_one_price" not in _grant_constraint_names(engine)
    assert _trigger_names(engine) == set()
    _assert_money_columns_are_integer(engine)

    _insert_grant(
        engine,
        grant_id="temporarily-invalid-at-0012",
        model_id="model-no-price",
        second_price=None,
        item_price=None,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM company_model_grants "
                "WHERE id = 'temporarily-invalid-at-0012'"
            )
        )
        connection.execute(
            text(
                "UPDATE ledger_entries SET note = 'mutable at 0012' "
                "WHERE id = 'ledger-before-downgrade'"
            )
        )
        connection.execute(
            text(
                "DELETE FROM ledger_entries WHERE id = 'ledger-before-downgrade'"
            )
        )
    _insert_ledger(engine, "ledger-after-downgrade", "guard must be rebuilt")
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _revision(engine) == "0040_showcase_management"
    model_columns = {
        column["name"]
        for column in inspect(engine).get_columns("model_definitions")
    }
    assert {
        "relay_capability_revision",
        "relay_capability_synced_at",
    }.issubset(model_columns)
    task_columns = {
        column["name"]
        for column in inspect(engine).get_columns("generation_tasks")
    }
    assert "relay_error_snapshot" in task_columns
    assert "ck_grant_exactly_one_price" in _grant_constraint_names(engine)
    assert _trigger_names(engine) == LEDGER_TRIGGER_NAMES
    assert "ix_ledger_kind_created" in _ledger_index_names(engine)
    _assert_money_columns_are_bigint(engine)

    with pytest.raises(IntegrityError, match="ck_grant_exactly_one_price"):
        _insert_grant(
            engine,
            grant_id="invalid-after-rebuild",
            model_id="model-after-rebuild",
            second_price=None,
            item_price=None,
        )
    _assert_raw_ledger_mutation_is_rejected(engine, "ledger-after-downgrade")
    engine.dispose()
