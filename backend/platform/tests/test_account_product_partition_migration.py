from __future__ import annotations

import os
from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from platform_api.models import User, UserAccountType


PREVIOUS_HEAD = "0043_admin_task_content"
CURRENT_HEAD = "0044_account_product_partition"
POSTGRES_URL = os.getenv("PLATFORM_TEST_DATABASE_URL") or os.getenv(
    "DATABASE_URL", ""
)


def _config(project_root: Path, database_url: str) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    # ConfigParser treats URL-encoded PostgreSQL search_path options as
    # interpolation markers unless percent signs are escaped.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _revision(engine: Engine) -> str:
    with engine.connect() as connection:
        return str(connection.scalar(text("SELECT version_num FROM alembic_version")))


def _seed_legacy_rows(engine: Engine) -> dict[str, str]:
    ids = {
        "company": "partition-company",
        "admin": "partition-admin",
        "company_user": "partition-company-user",
        "invited_user": "partition-invited-user",
        "personal_user": "partition-personal-user",
        "admin_membership": "partition-admin-membership",
        "company_membership": "partition-company-membership",
        "admin_workspace": "partition-admin-workspace",
        "company_workspace": "partition-company-workspace",
        "invited_workspace": "partition-invited-workspace",
        "personal_workspace": "partition-personal-workspace",
    }
    at = "2026-08-28 00:00:00"
    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.execute(
            text(
                "INSERT INTO companies (id, name, status, created_at, updated_at) "
                "VALUES (:id, 'Partition Company', 'ACTIVE', :at, :at)"
            ),
            {"id": ids["company"], "at": at},
        )
        users = (
            (ids["admin"], "admin@partition.test", True, "ACTIVE"),
            (ids["company_user"], "member@partition.test", False, "ACTIVE"),
            # Case-folding is intentional: this represents an identity row
            # preprovisioned before its pending company invitation is accepted.
            (ids["invited_user"], "INVITED@partition.test", False, "PENDING"),
            # An already-active personal consumer may have received a legacy
            # invitation, but the unaccepted invitation must never convert the
            # product account or hide its personal ledger.
            (ids["personal_user"], "personal@partition.test", False, "ACTIVE"),
        )
        for user_id, email, is_admin, status in users:
            connection.execute(
                text(
                    "INSERT INTO users ("
                    "id, email, display_name, is_platform_admin, status, "
                    "auth_version, created_at, updated_at"
                    ") VALUES ("
                    ":id, :email, :display_name, :is_admin, :status, 1, :at, :at"
                    ")"
                ),
                {
                    "id": user_id,
                    "email": email,
                    "display_name": user_id,
                    "is_admin": is_admin,
                    "status": status,
                    "at": at,
                },
            )
        connection.execute(
            text(
                "INSERT INTO company_memberships ("
                "id, company_id, user_id, status, created_at, updated_at"
                ") VALUES "
                "(:admin_membership, :company, :admin, 'ACTIVE', :at, :at), "
                "(:company_membership, :company, :company_user, 'ACTIVE', :at, :at)"
            ),
            {
                "admin_membership": ids["admin_membership"],
                "company_membership": ids["company_membership"],
                "company": ids["company"],
                "admin": ids["admin"],
                "company_user": ids["company_user"],
                "at": at,
            },
        )
        connection.execute(
            text(
                "INSERT INTO company_invitations ("
                "id, token_digest, company_id, email, display_name, primary_role, "
                "status, expires_at, created_by_user_id, accepted_by_user_id, "
                "accepted_at, revoked_at, idempotency_key, request_fingerprint, "
                "created_at, updated_at"
                ") VALUES ("
                "'partition-active-personal-invitation', :token_digest, :company, "
                "'personal@partition.test', 'Personal User', 'operator', 'PENDING', "
                "'2026-09-28 00:00:00', :creator, NULL, NULL, NULL, "
                "'partition-active-personal-key', :fingerprint, :at, :at"
                ")"
            ),
            {
                "token_digest": "c" * 64,
                "company": ids["company"],
                "creator": ids["company_user"],
                "fingerprint": "d" * 64,
                "at": at,
            },
        )
        connection.execute(
            text(
                "INSERT INTO company_invitations ("
                "id, token_digest, company_id, email, display_name, primary_role, "
                "status, expires_at, created_by_user_id, accepted_by_user_id, "
                "accepted_at, revoked_at, idempotency_key, request_fingerprint, "
                "created_at, updated_at"
                ") VALUES ("
                "'partition-invitation', :token_digest, :company, "
                "'invited@partition.test', 'Invited User', 'operator', 'PENDING', "
                "'2026-09-28 00:00:00', :creator, NULL, NULL, NULL, "
                "'partition-invitation-key', :fingerprint, :at, :at"
                ")"
            ),
            {
                "token_digest": "a" * 64,
                "company": ids["company"],
                "creator": ids["company_user"],
                "fingerprint": "b" * 64,
                "at": at,
            },
        )
        for key, user_key in (
            ("admin_workspace", "admin"),
            ("company_workspace", "company_user"),
            ("invited_workspace", "invited_user"),
            ("personal_workspace", "personal_user"),
        ):
            workspace_id = ids[key]
            connection.execute(
                text(
                    "INSERT INTO personal_workspaces ("
                    "id, user_id, active, created_at, updated_at"
                    ") VALUES (:id, :user_id, true, :at, :at)"
                ),
                {"id": workspace_id, "user_id": ids[user_key], "at": at},
            )
            connection.execute(
                text(
                    "INSERT INTO personal_wallet_accounts ("
                    "workspace_id, available_points, reserved_points, "
                    "created_at, updated_at"
                    ") VALUES (:workspace_id, 17, 3, :at, :at)"
                ),
                {"workspace_id": workspace_id, "at": at},
            )
            connection.execute(
                text(
                    "INSERT INTO personal_ledger_entries ("
                    "id, workspace_id, kind, amount_points, "
                    "available_delta_points, reserved_delta_points, "
                    "idempotency_key, task_id, note, created_at"
                    ") VALUES ("
                    ":id, :workspace_id, 'RECHARGE', 20, 20, 0, "
                    ":key, NULL, 'partition migration evidence', :at"
                    ")"
                ),
                {
                    "id": f"ledger-{workspace_id}",
                    "workspace_id": workspace_id,
                    "key": f"credit-{workspace_id}",
                    "at": at,
                },
            )
    return ids


def _assert_backfill_and_preservation(engine: Engine, ids: dict[str, str]) -> None:
    columns = {column["name"]: column for column in inspect(engine).get_columns("users")}
    assert columns["account_type"]["nullable"] is False
    with engine.connect() as connection:
        account_types = dict(
            connection.execute(
                text("SELECT id, account_type FROM users")
            ).all()
        )
        assert account_types[ids["admin"]] == "PLATFORM_ADMIN"
        assert account_types[ids["company_user"]] == "COMPANY"
        assert account_types[ids["invited_user"]] == "COMPANY"
        assert account_types[ids["personal_user"]] == "PERSONAL"

        active_by_user = dict(
            connection.execute(
                text("SELECT user_id, active FROM personal_workspaces")
            ).all()
        )
        assert bool(active_by_user[ids["admin"]]) is False
        assert bool(active_by_user[ids["company_user"]]) is False
        assert bool(active_by_user[ids["invited_user"]]) is False
        assert bool(active_by_user[ids["personal_user"]]) is True

        memberships = dict(
            connection.execute(
                text("SELECT user_id, status FROM company_memberships")
            ).all()
        )
        assert memberships[ids["admin"]] == "DISABLED"
        assert memberships[ids["company_user"]] == "ACTIVE"

        assert connection.scalar(text("SELECT count(*) FROM personal_wallet_accounts")) == 4
        assert connection.scalar(text("SELECT count(*) FROM personal_ledger_entries")) == 4
        assert connection.scalar(
            text("SELECT sum(available_points) FROM personal_wallet_accounts")
        ) == 68
        assert connection.scalar(
            text("SELECT sum(amount_points) FROM personal_ledger_entries")
        ) == 80


def _assert_database_guards(engine: Engine, ids: dict[str, str]) -> None:
    with pytest.raises(DBAPIError, match="user account_type is immutable"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE users SET account_type = 'COMPANY' WHERE id = :id"),
                {"id": ids["personal_user"]},
            )

    with pytest.raises(
        DBAPIError, match="personal workspace requires a PERSONAL account"
    ):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE personal_workspaces SET active = true "
                    "WHERE id = :id"
                ),
                {"id": ids["company_workspace"]},
            )

    with pytest.raises(DBAPIError, match="company membership requires a COMPANY account"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO company_memberships ("
                    "id, company_id, user_id, status, created_at, updated_at"
                    ") VALUES ("
                    "'partition-invalid-membership', :company, :user_id, "
                    "'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"company": ids["company"], "user_id": ids["personal_user"]},
            )

    with pytest.raises(DBAPIError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users ("
                    "id, email, display_name, is_platform_admin, account_type, "
                    "status, auth_version, created_at, updated_at"
                    ") VALUES ("
                    "'partition-inconsistent', 'inconsistent@partition.test', "
                    "'Inconsistent', true, 'PERSONAL', 'ACTIVE', 1, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

    # Valid same-boundary operations prove the guards are not blanket locks.
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users ("
                "id, email, display_name, is_platform_admin, account_type, "
                "status, auth_version, created_at, updated_at"
                ") VALUES ("
                "'partition-extra-company', 'extra-company@partition.test', "
                "'Extra Company', false, 'COMPANY', 'ACTIVE', 1, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO company_memberships ("
                "id, company_id, user_id, status, created_at, updated_at"
                ") VALUES ("
                "'partition-valid-membership', :company, "
                "'partition-extra-company', 'ACTIVE', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"company": ids["company"]},
        )
        connection.execute(
            text(
                "INSERT INTO users ("
                "id, email, display_name, is_platform_admin, account_type, "
                "status, auth_version, created_at, updated_at"
                ") VALUES ("
                "'partition-extra-personal', 'extra-personal@partition.test', "
                "'Extra Personal', false, 'PERSONAL', 'ACTIVE', 1, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO personal_workspaces ("
                "id, user_id, active, created_at, updated_at"
                ") VALUES ("
                "'partition-valid-workspace', 'partition-extra-personal', true, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )


def _assert_orm_context_default(engine: Engine) -> None:
    with Session(engine) as session:
        ordinary = User(
            id="partition-orm-personal",
            email="orm-personal@partition.test",
            display_name="ORM Personal",
        )
        administrator = User(
            id="partition-orm-admin",
            email="orm-admin@partition.test",
            display_name="ORM Admin",
            is_platform_admin=True,
        )
        session.add_all((ordinary, administrator))
        session.flush()
        assert ordinary.account_type is UserAccountType.PERSONAL
        assert administrator.account_type is UserAccountType.PLATFORM_ADMIN
        session.rollback()


def _exercise_upgrade(engine: Engine, config: Config) -> dict[str, str]:
    assert _revision(engine) == PREVIOUS_HEAD
    ids = _seed_legacy_rows(engine)
    engine.dispose()
    command.upgrade(config, CURRENT_HEAD)
    _assert_backfill_and_preservation(engine, ids)
    _assert_database_guards(engine, ids)
    _assert_orm_context_default(engine)
    return ids


def test_sqlite_account_product_partition_round_trip(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "account-product-partition.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = _config(project_root, database_url)

    command.upgrade(config, PREVIOUS_HEAD)
    engine = create_engine(database_url)
    ids = _exercise_upgrade(engine, config)
    assert _revision(engine) == CURRENT_HEAD

    command.downgrade(config, PREVIOUS_HEAD)
    assert _revision(engine) == PREVIOUS_HEAD
    assert "account_type" not in {
        column["name"] for column in inspect(engine).get_columns("users")
    }
    with engine.connect() as connection:
        restored = dict(
            connection.execute(
                text("SELECT user_id, active FROM personal_workspaces")
            ).all()
        )
        # Without durable provenance, rollback cannot distinguish a workspace
        # disabled by 0044 from one that was already intentionally inactive.
        # It therefore preserves the fail-closed state instead of widening
        # personal or tenant access.
        assert bool(restored[ids["admin"]]) is False
        assert bool(restored[ids["company_user"]]) is False
        assert bool(restored[ids["invited_user"]]) is False
        assert connection.scalar(
            text(
                "SELECT status FROM company_memberships WHERE id = :id"
            ),
            {"id": ids["admin_membership"]},
        ) == "DISABLED"
        assert connection.scalar(text("SELECT count(*) FROM personal_wallet_accounts")) == 4
        assert connection.scalar(text("SELECT count(*) FROM personal_ledger_entries")) == 4
    engine.dispose()

    # A second upgrade proves that both directions leave the schema operable.
    command.upgrade(config, CURRENT_HEAD)


def test_postgres_account_product_partition_guards_and_round_trip() -> None:
    if not POSTGRES_URL.startswith("postgresql"):
        pytest.skip("requires a PostgreSQL test database")

    schema_name = f"account_partition_{uuid.uuid4().hex}"
    administration_engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    with administration_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema_name}"')
    schema_url = (
        make_url(POSTGRES_URL)
        .update_query_dict({"options": f"-csearch_path={schema_name}"})
        .render_as_string(hide_password=False)
    )
    project_root = Path(__file__).resolve().parents[1]
    config = _config(project_root, schema_url)
    engine = create_engine(schema_url, pool_pre_ping=True)

    try:
        command.upgrade(config, PREVIOUS_HEAD)
        ids = _exercise_upgrade(engine, config)
        assert _revision(engine) == CURRENT_HEAD
        command.downgrade(config, PREVIOUS_HEAD)
        assert _revision(engine) == PREVIOUS_HEAD
        assert "account_type" not in {
            column["name"] for column in inspect(engine).get_columns("users")
        }
        with engine.connect() as connection:
            assert connection.scalar(
                text(
                    "SELECT status FROM company_memberships WHERE id = :id"
                ),
                {"id": ids["admin_membership"]},
            ) == "DISABLED"
    finally:
        engine.dispose()
        with administration_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema_name}" CASCADE')
        administration_engine.dispose()
