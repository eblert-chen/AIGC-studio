from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


PREVIOUS_HEAD = "0044_account_product_partition"
CURRENT_HEAD = "0045_system_audit_actor"


def _config(project_root: Path, database_url: str) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _insert_legacy_audit(database_url: str) -> None:
    engine = create_engine(database_url)
    at = "2026-08-29 00:00:00"
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id,email,display_name,is_platform_admin,account_type,status,"
                    "auth_version,created_at,updated_at) VALUES "
                    "('audit-user','audit@example.test','Audit User',false,"
                    "'PERSONAL','ACTIVE',1,:at,:at)"
                ),
                {"at": at},
            )
            connection.execute(
                text(
                    "INSERT INTO audit_logs "
                    "(id,actor_user_id,action,target_type,target_id,"
                    "before_summary,after_summary,outcome,request_id,created_at) "
                    "VALUES ('legacy-audit','audit-user','legacy.action','model',"
                    "'legacy-model','{}','{}','SUCCEEDED','legacy-request',:at)"
                ),
                {"at": at},
            )
    finally:
        engine.dispose()


def test_upgrade_backfills_user_actor_and_enforces_exclusive_identity(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).parents[1]
    database_path = tmp_path / "system-audit-actor.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = _config(project_root, database_url)

    command.upgrade(config, PREVIOUS_HEAD)
    _insert_legacy_audit(database_url)
    command.upgrade(config, "head")
    command.check(config)

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        columns = {
            column["name"]: column
            for column in inspector.get_columns("audit_logs")
        }
        assert columns["actor_user_id"]["nullable"] is True
        assert columns["actor_kind"]["nullable"] is False
        assert columns["actor_key"]["nullable"] is True
        assert "ix_audit_system_actor_created" in {
            index["name"] for index in inspector.get_indexes("audit_logs")
        }
        assert "ck_audit_log_actor_identity" in {
            constraint["name"]
            for constraint in inspector.get_check_constraints("audit_logs")
        }
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT actor_user_id,actor_kind,actor_key FROM audit_logs "
                    "WHERE id='legacy-audit'"
                )
            ).one() == ("audit-user", "user", None)

        at = "2026-08-29 00:01:00"
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO audit_logs "
                    "(id,actor_user_id,actor_kind,actor_key,action,target_type,"
                    "target_id,before_summary,after_summary,outcome,request_id,"
                    "created_at) VALUES "
                    "('system-audit',NULL,'system','relay-catalog-sync',"
                    "'model.relay_catalog.reconcile','relay_model_catalog',"
                    "'sha256:test','{}','{}','SUCCEEDED','system-request',:at)"
                ),
                {"at": at},
            )
        invalid = (
            ("invalid-user", None, "user", None),
            ("invalid-system", "audit-user", "system", "relay-catalog-sync"),
            ("invalid-kind", None, "service", "relay-catalog-sync"),
        )
        for row_id, user_id, kind, key in invalid:
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO audit_logs "
                            "(id,actor_user_id,actor_kind,actor_key,action,"
                            "target_type,target_id,before_summary,after_summary,"
                            "outcome,request_id,created_at) VALUES "
                            "(:id,:user_id,:kind,:key,'invalid','model','x',"
                            "'{}','{}','FAILED',:id,:at)"
                        ),
                        {
                            "id": row_id,
                            "user_id": user_id,
                            "kind": kind,
                            "key": key,
                            "at": at,
                        },
                    )
    finally:
        engine.dispose()


def test_downgrade_refuses_to_erase_system_actor_evidence(tmp_path: Path) -> None:
    project_root = Path(__file__).parents[1]
    database_path = tmp_path / "system-audit-downgrade.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = _config(project_root, database_url)
    command.upgrade(config, CURRENT_HEAD)
    engine = create_engine(database_url)
    at = "2026-08-29 00:02:00"
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO audit_logs "
                    "(id,actor_user_id,actor_kind,actor_key,action,target_type,"
                    "target_id,before_summary,after_summary,outcome,request_id,"
                    "created_at) VALUES "
                    "('system-audit',NULL,'system','relay-catalog-sync',"
                    "'model.relay_catalog.reconcile','relay_model_catalog','x',"
                    "'{}','{}','SUCCEEDED','system-request',:at)"
                ),
                {"at": at},
            )
        with pytest.raises(
            RuntimeError,
            match="erase system audit identity evidence",
        ):
            command.downgrade(config, PREVIOUS_HEAD)
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM audit_logs WHERE actor_kind='system'"))
    finally:
        engine.dispose()

    command.downgrade(config, PREVIOUS_HEAD)
    engine = create_engine(database_url)
    try:
        columns = {
            column["name"]: column
            for column in inspect(engine).get_columns("audit_logs")
        }
        assert "actor_kind" not in columns
        assert "actor_key" not in columns
        assert columns["actor_user_id"]["nullable"] is False
    finally:
        engine.dispose()
