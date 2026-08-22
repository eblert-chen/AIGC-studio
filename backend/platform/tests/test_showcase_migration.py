from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text


PREVIOUS_HEAD = "0039_new_api_relay_defaults"
CURRENT_HEAD = "0040_showcase_management"
TABLES = {
    "showcase_channels",
    "showcase_draft_items",
    "showcase_media",
    "showcase_publication_events",
    "showcase_release_items",
    "showcase_releases",
}


def _config(project_root: Path, database_path: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


def test_sqlite_showcase_migration_round_trip_and_immutable_triggers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "showcase.db"
    config = _config(project_root, database_path)
    command.upgrade(config, PREVIOUS_HEAD)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert TABLES.isdisjoint(inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert TABLES.issubset(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_HEAD
        assert connection.scalar(
            text("SELECT draft_version FROM showcase_channels WHERE id='home'")
        ) == 0
        triggers = set(
            connection.scalars(
                text(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND name LIKE 'trg_showcase_%_immutable'"
                )
            )
        )
        assert len(triggers) == 8
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        draft_checks = {
            row["name"]: row["sqltext"]
            for row in inspect(connection).get_check_constraints(
                "showcase_draft_items"
            )
        }
        release_checks = {
            row["name"]: row["sqltext"]
            for row in inspect(connection).get_check_constraints(
                "showcase_release_items"
            )
        }
        assert "ck_showcase_draft_category" in draft_checks
        assert "ck_showcase_release_item_category" in release_checks
        assert "商品展示" in draft_checks["ck_showcase_draft_category"]
        assert "商品展示" in release_checks["ck_showcase_release_item_category"]
    engine.dispose()

    command.downgrade(config, PREVIOUS_HEAD)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert TABLES.isdisjoint(inspect(engine).get_table_names())
    engine.dispose()
    command.upgrade(config, "head")
    command.check(config)
