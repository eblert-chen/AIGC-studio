from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError, IntegrityError

from platform_api import database_privileges as facade
from platform_api import database_privileges_behavior_v24 as behavior
from platform_api import database_privileges_v23 as previous_policy
from platform_api import database_privileges_v24 as policy


PREVIOUS = "0058_personal_limits"
HEAD = "0059_director_shot_schema_v2"


def _config(database_path: Path) -> Config:
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite+pysqlite:///{database_path.as_posix()}",
    )
    return config


def _revision(engine) -> str:
    with engine.connect() as connection:
        return str(
            connection.scalar(text("SELECT version_num FROM alembic_version"))
        )


def _insert_package(connection, *, suffix: str, schema_version: int) -> None:
    digest = suffix * 64
    connection.execute(
        text(
            "INSERT INTO director_shot_packages ("
            "id, company_id, personal_workspace_id, created_by_user_id, "
            "composition_asset_id, schema_version, manifest, manifest_sha256, "
            "scene_revision_sha256, sealed_revision_sha256, idempotency_key, "
            "request_fingerprint, created_at, updated_at"
            ") VALUES ("
            ":id, :company, NULL, :user, :asset, :schema_version, '{}', "
            ":digest, :digest, :digest, :idempotency, :digest, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {
            "id": "dsp_" + suffix * 32,
            "company": "cmp_" + suffix * 32,
            "user": "usr_" + suffix * 32,
            "asset": "ast_" + suffix * 32,
            "schema_version": schema_version,
            "digest": digest,
            "idempotency": f"schema-v{schema_version}-{suffix}",
        },
    )


def _trigger_names(engine) -> set[str]:
    with engine.connect() as connection:
        return set(
            connection.scalars(
                text(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND tbl_name='director_shot_packages' ORDER BY name"
                )
            )
        )


def test_sqlite_upgrade_accepts_v2_rejects_other_versions_and_preserves_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_path = tmp_path / "director-shot-schema-v2.db"
    config = _config(database_path)
    command.upgrade(config, PREVIOUS)
    engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    try:
        with pytest.raises(IntegrityError, match="schema_version"):
            with engine.begin() as connection:
                _insert_package(connection, suffix="2", schema_version=2)
    finally:
        engine.dispose()

    command.upgrade(config, HEAD)
    command.check(config)
    engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    try:
        assert _revision(engine) == HEAD
        assert _trigger_names(engine) == {
            "trg_director_shot_package_no_delete",
            "trg_director_shot_package_no_update",
        }
        with engine.begin() as connection:
            _insert_package(connection, suffix="2", schema_version=2)
        with pytest.raises(IntegrityError, match="schema_version"):
            with engine.begin() as connection:
                _insert_package(connection, suffix="3", schema_version=3)
        with pytest.raises(DatabaseError, match="immutable"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE director_shot_packages SET schema_version=2 "
                        "WHERE id=:id"
                    ),
                    {"id": "dsp_" + "2" * 32},
                )
    finally:
        engine.dispose()

    with pytest.raises(
        RuntimeError,
        match="downgrade is blocked while director shot schema v2 packages exist",
    ):
        command.downgrade(config, PREVIOUS)
    engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    try:
        assert _revision(engine) == HEAD
    finally:
        engine.dispose()


def test_sqlite_downgrade_with_only_v1_rows_restores_v1_constraint_and_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_path = tmp_path / "director-shot-schema-v1-downgrade.db"
    config = _config(database_path)
    command.upgrade(config, HEAD)
    engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    try:
        with engine.begin() as connection:
            _insert_package(connection, suffix="1", schema_version=1)
    finally:
        engine.dispose()

    command.downgrade(config, PREVIOUS)
    engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    try:
        assert _revision(engine) == PREVIOUS
        assert _trigger_names(engine) == {
            "trg_director_shot_package_no_delete",
            "trg_director_shot_package_no_update",
        }
        with engine.connect() as connection:
            assert connection.scalar(
                text(
                    "SELECT schema_version FROM director_shot_packages "
                    "WHERE id=:id"
                ),
                {"id": "dsp_" + "1" * 32},
            ) == 1
        with pytest.raises(IntegrityError, match="schema_version"):
            with engine.begin() as connection:
                _insert_package(connection, suffix="2", schema_version=2)
        with pytest.raises(DatabaseError, match="immutable"):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM director_shot_packages WHERE id=:id"),
                    {"id": "dsp_" + "1" * 32},
                )
    finally:
        engine.dispose()


def test_postgres_downgrade_locks_before_v2_check_and_ddl(monkeypatch) -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0059_director_shot_package_schema_v2.py"
    )
    spec = importlib.util.spec_from_file_location("director_shot_v2_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    events: list[str] = []

    class RecordingConnection:
        dialect = SimpleNamespace(name="postgresql")

        def execute(self, statement):
            assert str(statement) == (
                "LOCK TABLE director_shot_packages IN ACCESS EXCLUSIVE MODE"
            )
            events.append("lock")

        def scalar(self, statement):
            assert "WHERE schema_version = 2" in str(statement)
            events.append("check")
            return None

    monkeypatch.setattr(migration.op, "get_bind", RecordingConnection)
    monkeypatch.setattr(
        migration.op,
        "drop_constraint",
        lambda *_args, **_kwargs: events.append("drop"),
    )
    monkeypatch.setattr(
        migration.op,
        "create_check_constraint",
        lambda *_args, **_kwargs: events.append("create"),
    )

    migration.downgrade()

    assert events == ["lock", "check", "drop", "create"]


def test_v24_registry_remains_unqualified_without_acl_expansion() -> None:
    assert ScriptDirectory.from_config(
        Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    ).get_current_head() == HEAD
    assert facade.PLATFORM_ALEMBIC_HEAD == HEAD
    assert facade.PLATFORM_DATABASE_PRIVILEGE_POLICY_REGISTRY[HEAD] == (
        policy,
        behavior,
    )
    assert policy.TABLES == previous_policy.TABLES
    assert policy.PRIVILEGES_BY_PROCESS == previous_policy.PRIVILEGES_BY_PROCESS
    assert policy.CATALOG_SHA256 == policy.UNQUALIFIED_CATALOG_SHA256 == "0" * 64
    with pytest.raises(
        behavior.PlatformDatabaseAttestationError,
        match="v24 catalog is UNQUALIFIED",
    ):
        behavior.validate_platform_database_evidence(
            None,
            "api",
            require_runtime_acl=True,
            require_head=True,
        )
