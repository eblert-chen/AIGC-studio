from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DatabaseError


def _config(database_path: Path) -> Config:
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite:///{database_path.as_posix()}",
    )
    return config


def test_0053_upgrades_empty_sqlite_and_installs_immutable_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_path = tmp_path / "director-shot-package.db"
    config = _config(database_path)
    command.upgrade(config, "0053_director_shot_packages")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    assert {"director_shot_packages", "task_director_shot_packages"} <= set(
        inspector.get_table_names()
    )
    package_columns = {
        column["name"]
        for column in inspector.get_columns("director_shot_packages")
    }
    assert package_columns >= {
        "company_id",
        "personal_workspace_id",
        "composition_asset_id",
        "manifest",
        "manifest_sha256",
        "scene_revision_sha256",
        "sealed_revision_sha256",
        "idempotency_key",
        "request_fingerprint",
    }

    now = datetime.now(timezone.utc)
    digest = "a" * 64
    package_id = "dsp_" + "1" * 32
    with engine.begin() as connection:
        assert connection.scalar(
            text("SELECT version_num FROM alembic_version")
        ) == "0053_director_shot_packages"
        trigger_names = set(
            connection.scalars(
                text(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND name LIKE 'trg_%director_shot_package%'"
                )
            )
        )
        assert trigger_names == {
            "trg_director_shot_package_no_update",
            "trg_director_shot_package_no_delete",
            "trg_task_director_shot_package_no_update",
            "trg_task_director_shot_package_no_delete",
        }
        connection.execute(
            text(
                "INSERT INTO director_shot_packages ("
                "id, company_id, personal_workspace_id, created_by_user_id, "
                "composition_asset_id, schema_version, manifest, manifest_sha256, "
                "scene_revision_sha256, sealed_revision_sha256, idempotency_key, "
                "request_fingerprint, created_at, updated_at"
                ") VALUES ("
                ":id, :company, NULL, :user, :asset, 1, :manifest, :digest, "
                ":digest, :digest, :idempotency, :digest, :created_at, :created_at"
                ")"
            ),
            {
                "id": package_id,
                "company": "cmp_" + "1" * 32,
                "user": "usr_" + "1" * 32,
                "asset": "ast_" + "1" * 32,
                "manifest": "{}",
                "digest": digest,
                "idempotency": "migration-test",
                "created_at": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO task_director_shot_packages ("
                "task_id, package_id, manifest_sha256, created_at"
                ") VALUES (:task, :package, :digest, :created_at)"
            ),
            {
                "task": "tsk_" + "1" * 32,
                "package": package_id,
                "digest": digest,
                "created_at": now,
            },
        )

    for statement, identifier in (
        (
            "UPDATE director_shot_packages SET schema_version = 1 WHERE id = :id",
            package_id,
        ),
        ("DELETE FROM director_shot_packages WHERE id = :id", package_id),
        (
            "UPDATE task_director_shot_packages SET package_id = package_id "
            "WHERE task_id = :id",
            "tsk_" + "1" * 32,
        ),
        (
            "DELETE FROM task_director_shot_packages WHERE task_id = :id",
            "tsk_" + "1" * 32,
        ),
    ):
        with pytest.raises(DatabaseError, match="immutable"):
            with engine.begin() as connection:
                connection.execute(text(statement), {"id": identifier})

    engine.dispose()

    with pytest.raises(
        RuntimeError,
        match="downgrade blocked by sealed director shot packages",
    ):
        command.downgrade(config, "0052_personal_input_assets")
    command.upgrade(config, "head")
    command.check(config)
