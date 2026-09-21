from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError


PREVIOUS_HEAD = "0045_system_audit_actor"
CURRENT_HEAD = "0046_company_points_billing_v2"


def _config(project_root: Path, database_url: str) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _revision(engine: Engine) -> str:
    with engine.connect() as connection:
        return str(connection.scalar(text("SELECT version_num FROM alembic_version")))


def _sqlite_triggers(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return {
            str(name)
            for name in connection.scalars(
                text("SELECT name FROM sqlite_master WHERE type='trigger'")
            )
        }


def _seed_0045_wallets_and_tasks(engine: Engine) -> None:
    at = "2026-08-29 10:00:00"
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.execute(
            text(
                "INSERT INTO companies (id, name, status, created_at, updated_at) "
                "VALUES ('points-company', 'Points Company', 'ACTIVE', :at, :at)"
            ),
            {"at": at},
        )
        for user_id, account_type in (
            ("points-company-user", "COMPANY"),
            ("points-personal-user", "PERSONAL"),
        ):
            connection.execute(
                text(
                    "INSERT INTO users ("
                    "id, email, display_name, is_platform_admin, account_type, "
                    "status, auth_version, created_at, updated_at"
                    ") VALUES ("
                    ":id, :email, :id, false, :account_type, 'ACTIVE', 1, :at, :at"
                    ")"
                ),
                {
                    "id": user_id,
                    "email": f"{user_id}@example.test",
                    "account_type": account_type,
                    "at": at,
                },
            )
        connection.execute(
            text(
                "INSERT INTO personal_workspaces "
                "(id, user_id, active, created_at, updated_at) VALUES "
                "('points-personal-workspace', 'points-personal-user', true, :at, :at)"
            ),
            {"at": at},
        )
        connection.execute(
            text(
                "INSERT INTO wallet_accounts "
                "(company_id, available_cents, reserved_cents, created_at, updated_at) "
                "VALUES ('points-company', 12345, 0, :at, :at)"
            ),
            {"at": at},
        )
        connection.execute(
            text(
                "INSERT INTO ledger_entries "
                "(id, company_id, kind, amount_cents, available_delta_cents, "
                "reserved_delta_cents, idempotency_key, task_id, note, created_at) "
                "VALUES ('points-cent-ledger', 'points-company', 'RECHARGE', 12345, "
                "12345, 0, 'points-cent-opening', NULL, 'legacy cash', :at)"
            ),
            {"at": at},
        )
        connection.execute(
            text(
                "INSERT INTO personal_wallet_accounts "
                "(workspace_id, available_points, reserved_points, created_at, updated_at) "
                "VALUES ('points-personal-workspace', 137, 0, :at, :at)"
            ),
            {"at": at},
        )
        connection.execute(
            text(
                "INSERT INTO personal_ledger_entries "
                "(id, workspace_id, kind, amount_points, available_delta_points, "
                "reserved_delta_points, idempotency_key, task_id, note, created_at) "
                "VALUES ('points-personal-ledger', 'points-personal-workspace', "
                "'RECHARGE', 137, 137, 0, 'points-personal-opening', NULL, "
                "'personal points stay points', :at)"
            ),
            {"at": at},
        )
        connection.execute(
            text(
                "INSERT INTO model_definitions "
                "(id, slug, display_name, provider_key, billing_mode, "
                "capability_version, active, "
                "created_at, updated_at) VALUES "
                "('points-model', 'points-model', 'Points Model', 'migration-test', "
                "'per_item', 1, true, :at, :at)"
            ),
            {"at": at},
        )
        connection.execute(
            text(
                "INSERT INTO generation_tasks "
                "(id, company_id, personal_workspace_id, user_id, model_id, "
                "idempotency_key, request_fingerprint, status, request_payload, "
                "quote_cents, quote_points, pricing_snapshot, capability_snapshot, "
                "reserved_cents, reserved_points, actual_cost_cents, "
                "actual_cost_points, created_at, updated_at) VALUES "
                "('points-company-task', 'points-company', NULL, "
                "'points-company-user', 'points-model', 'points-company-task-key', "
                ":company_fingerprint, 'SUCCEEDED', '{}', 20, NULL, '{}', '{}', "
                "0, 0, 20, NULL, :at, :at), "
                "('points-personal-task', NULL, 'points-personal-workspace', "
                "'points-personal-user', 'points-model', 'points-personal-task-key', "
                ":personal_fingerprint, 'SUCCEEDED', '{}', NULL, 17, '{}', '{}', "
                "0, 0, NULL, 17, :at, :at)"
            ),
            {
                "company_fingerprint": "a" * 64,
                "personal_fingerprint": "b" * 64,
                "at": at,
            },
        )


def _assert_0046_preserves_units(engine: Engine) -> None:
    with engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT available_cents, reserved_cents FROM wallet_accounts "
                "WHERE company_id='points-company'"
            )
        ).one() == (12_345, 0)
        assert connection.execute(
            text(
                "SELECT available_points, reserved_points "
                "FROM personal_wallet_accounts "
                "WHERE workspace_id='points-personal-workspace'"
            )
        ).one() == (137, 0)
        assert connection.execute(
            text(
                "SELECT amount_points, available_delta_points, reserved_delta_points "
                "FROM personal_ledger_entries WHERE id='points-personal-ledger'"
            )
        ).one() == (137, 137, 0)
        task_units = dict(
            connection.execute(
                text(
                    "SELECT id, billing_unit || ':' || billing_version "
                    "FROM generation_tasks ORDER BY id"
                )
            ).all()
        )
        assert task_units == {
            "points-company-task": "CNY_CENT:1",
            "points-personal-task": "POINT:2",
        }
        assert connection.scalar(
            text("SELECT count(*) FROM company_point_wallet_accounts")
        ) == 0
        assert connection.scalar(text("SELECT count(*) FROM company_point_lots")) == 0
        assert connection.scalar(
            text("SELECT count(*) FROM company_point_ledger_entries")
        ) == 0


def _insert_migrated_point_evidence(engine: Engine) -> None:
    at = "2026-08-29 11:00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO company_point_wallet_accounts "
                "(company_id, available_points, reserved_points, "
                "migration_idempotency_key, migrated_from_available_cents, "
                "migration_remainder_cents, migration_rounding_grant_points, "
                "created_at, updated_at) VALUES "
                "('points-company', 1235, 0, 'direct-migration', 12345, 5, 1, :at, :at)"
            ),
            {"at": at},
        )
        connection.execute(
            text(
                "INSERT INTO company_point_lots "
                "(id, company_id, source_kind, original_points, available_points, "
                "reserved_points, settled_points, cash_basis_cents, subsidy_cents, "
                "idempotency_key, expires_at, created_at, updated_at) VALUES "
                "('points-opening-lot', 'points-company', 'LEGACY', 1235, 1235, "
                "0, 0, 12345, 5, 'direct-opening-lot', NULL, :at, :at)"
            ),
            {"at": at},
        )
        connection.execute(
            text(
                "INSERT INTO company_point_ledger_entries "
                "(id, company_id, kind, amount_points, available_delta_points, "
                "reserved_delta_points, idempotency_key, task_id, note, created_at) "
                "VALUES ('points-opening-ledger', 'points-company', 'MIGRATION', "
                "1235, 1235, 0, 'direct-migration', NULL, 'opening evidence', :at)"
            ),
            {"at": at},
        )
        connection.execute(
            text(
                "UPDATE companies SET billing_version=2 "
                "WHERE id='points-company'"
            )
        )


def test_sqlite_0046_upgrade_preserves_units_and_database_guards_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "points-v2-guards.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = _config(project_root, database_url)

    command.upgrade(config, PREVIOUS_HEAD)
    engine = create_engine(database_url)
    _seed_0045_wallets_and_tasks(engine)
    engine.dispose()

    command.upgrade(config, CURRENT_HEAD)
    engine = create_engine(database_url)
    assert _revision(engine) == CURRENT_HEAD
    assert {
        "company_point_wallet_accounts",
        "company_point_lots",
        "company_point_ledger_entries",
        "task_point_lot_allocations",
    } <= set(inspect(engine).get_table_names())
    assert {
        "trg_company_point_ledger_no_update",
        "trg_company_point_ledger_no_delete",
        "trg_company_point_lot_no_delete",
        "trg_legacy_ledger_block_v2_insert",
        "trg_legacy_wallet_block_v2_update",
        "trg_task_billing_contract_immutable",
        "trg_company_billing_no_downgrade",
    } <= _sqlite_triggers(engine)
    _assert_0046_preserves_units(engine)
    _insert_migrated_point_evidence(engine)

    for statement in (
        "UPDATE company_point_ledger_entries SET note='tampered' "
        "WHERE id='points-opening-ledger'",
        "DELETE FROM company_point_ledger_entries "
        "WHERE id='points-opening-ledger'",
        "DELETE FROM company_point_lots WHERE id='points-opening-lot'",
        "UPDATE wallet_accounts SET available_cents=0 "
        "WHERE company_id='points-company'",
        "INSERT INTO ledger_entries "
        "(id, company_id, kind, amount_cents, available_delta_cents, "
        "reserved_delta_cents, idempotency_key, task_id, note, created_at) "
        "VALUES ('late-cent-write', 'points-company', 'RECHARGE', 1, 1, 0, "
        "'late-cent-write', NULL, '', CURRENT_TIMESTAMP)",
        "UPDATE generation_tasks SET quote_cents=21 "
        "WHERE id='points-company-task'",
        "INSERT INTO task_point_lot_allocations "
        "(id, company_id, task_id, lot_id, allocated_points, reserved_points, "
        "settled_points, released_points, created_at) VALUES "
        "('cross-scope-allocation', 'points-company', 'points-personal-task', "
        "'points-opening-lot', 1, 1, 0, 0, CURRENT_TIMESTAMP)",
        "UPDATE companies SET billing_version=1 WHERE id='points-company'",
    ):
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(text(statement))

    # Downgrading after any company switched to v2 is deliberately refused;
    # otherwise the point ledger and lot evidence would be destroyed.
    engine.dispose()
    with pytest.raises(RuntimeError, match="erase active company point billing evidence"):
        command.downgrade(config, PREVIOUS_HEAD)
    engine = create_engine(database_url)
    assert _revision(engine) == CURRENT_HEAD
    with engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT count(*) FROM company_point_ledger_entries "
                "WHERE id='points-opening-ledger'"
            )
        ) == 1
        assert connection.scalar(
            text("SELECT count(*) FROM company_point_lots WHERE id='points-opening-lot'")
        ) == 1
    engine.dispose()


def test_sqlite_0046_clean_downgrade_keeps_legacy_and_personal_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "points-v2-clean-downgrade.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = _config(project_root, database_url)

    command.upgrade(config, PREVIOUS_HEAD)
    engine = create_engine(database_url)
    _seed_0045_wallets_and_tasks(engine)
    engine.dispose()
    command.upgrade(config, CURRENT_HEAD)
    engine = create_engine(database_url)
    _assert_0046_preserves_units(engine)
    engine.dispose()

    command.downgrade(config, PREVIOUS_HEAD)
    engine = create_engine(database_url)
    assert _revision(engine) == PREVIOUS_HEAD
    inspector = inspect(engine)
    assert "billing_version" not in {
        column["name"] for column in inspector.get_columns("companies")
    }
    assert "billing_unit" not in {
        column["name"] for column in inspector.get_columns("generation_tasks")
    }
    assert "company_point_wallet_accounts" not in inspector.get_table_names()
    with engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT available_cents, reserved_cents FROM wallet_accounts "
                "WHERE company_id='points-company'"
            )
        ).one() == (12_345, 0)
        assert connection.execute(
            text(
                "SELECT available_points, reserved_points "
                "FROM personal_wallet_accounts "
                "WHERE workspace_id='points-personal-workspace'"
            )
        ).one() == (137, 0)
        assert connection.execute(
            text(
                "SELECT quote_cents, quote_points FROM generation_tasks "
                "WHERE id='points-company-task'"
            )
        ).one() == (20, None)
        assert connection.execute(
            text(
                "SELECT quote_cents, quote_points FROM generation_tasks "
                "WHERE id='points-personal-task'"
            )
        ).one() == (None, 17)
    engine.dispose()
