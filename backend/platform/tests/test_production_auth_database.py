from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


PLATFORM_ROOT = Path(__file__).parents[1]
AUTH_TABLES = {
    "account_security_events",
    "auth_sessions",
    "company_invitations",
    "external_identities",
    "oidc_login_transactions",
}


def _config() -> Config:
    return Config(str(PLATFORM_ROOT / "alembic.ini"))


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _use_unprotected_sqlite(
    monkeypatch: pytest.MonkeyPatch,
    path: Path,
) -> str:
    database_url = _database_url(path)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("PLATFORM_PROTECTED_RUNTIME", raising=False)
    return database_url


def test_sqlite_0037_upgrade_downgrade_and_auth_history_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _use_unprotected_sqlite(
        monkeypatch,
        tmp_path / "production-auth.sqlite",
    )
    config = _config()
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert AUTH_TABLES.issubset(inspector.get_table_names())
        assert {
            "status",
            "email_verified_at",
            "auth_version",
            "last_login_at",
            "deactivated_at",
        }.issubset({column["name"] for column in inspector.get_columns("users")})
        user_checks = {
            item["name"] for item in inspector.get_check_constraints("users")
        }
        assert {
            "ck_users_auth_version",
            "ck_users_status_deactivated",
        }.issubset(user_checks)
        invitation_checks = {
            item["name"]
            for item in inspector.get_check_constraints("company_invitations")
        }
        assert {
            "ck_company_invitation_primary_role",
            "ck_company_invitation_request_fingerprint_sha256",
            "ck_company_invitation_status_evidence",
            "ck_company_invitation_token_digest_sha256",
        }.issubset(invitation_checks)
        with engine.connect() as connection:
            index_sql = connection.scalar(
                text(
                    "SELECT sql FROM sqlite_master WHERE type='index' "
                    "AND name='uq_users_email_casefold'"
                )
            )
            trigger_names = set(
                connection.scalars(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='trigger' "
                        "AND name LIKE 'trg_%'"
                    )
                )
            )
        assert index_sql is not None and "lower(email)" in index_sql.lower()
        auth_session_columns = {
            column["name"]: column
            for column in inspector.get_columns("auth_sessions")
        }
        assert auth_session_columns["external_identity_id"]["nullable"] is False
        assert auth_session_columns["auth_time"]["nullable"] is True
        assert {
            "trg_account_security_events_no_update",
            "trg_account_security_events_no_delete",
            "trg_company_invitations_no_delete",
            "trg_channel_cost_personal_workspace_fk",
        }.issubset(trigger_names)

        now = "2026-08-20 00:00:00+00:00"
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id,email,display_name,is_platform_admin,status,auth_version,"
                    "created_at,updated_at) "
                    "VALUES ('user-1','owner@example.com','Owner',0,'ACTIVE',1,:now,:now)"
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO companies (id,name,status,created_at,updated_at) "
                    "VALUES ('company-1','Company','ACTIVE',:now,:now)"
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO account_security_events "
                    "(id,user_id,event_type,outcome,request_id,user_agent,details,created_at) "
                    "VALUES ('event-1','user-1','login','SUCCEEDED','request-1',"
                    "'ua','{}',:now)"
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO external_identities "
                    "(id,user_id,issuer,subject,email_at_link,created_at,updated_at) "
                    "VALUES ('identity-1','user-1','https://idp.example/','subject-1',"
                    "'owner@example.com',:now,:now)"
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO auth_sessions "
                    "(id,token_digest,csrf_digest,user_id,external_identity_id,"
                    "auth_version,amr,auth_time,created_at,last_seen_at,expires_at,"
                    "user_agent) VALUES ('session-without-auth-time',:token,:csrf,"
                    "'user-1','identity-1',1,'[]',NULL,:now,:now,:now,'ua')"
                ),
                {"token": "d" * 64, "csrf": "e" * 64, "now": now},
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO auth_sessions "
                        "(id,token_digest,csrf_digest,user_id,external_identity_id,"
                        "auth_version,amr,auth_time,created_at,last_seen_at,expires_at,"
                        "user_agent) VALUES ('session-without-identity',:token,:csrf,"
                        "'user-1',NULL,1,'[]',:now,:now,:now,:now,'ua')"
                    ),
                    {"token": "b" * 64, "csrf": "c" * 64, "now": now},
                )

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO company_invitations "
                    "(id,token_digest,company_id,email,display_name,primary_role,status,"
                    "expires_at,created_by_user_id,idempotency_key,request_fingerprint,"
                    "created_at,updated_at) VALUES "
                    "('invite-1',:digest,'company-1','member@example.com','Member',"
                    "'operator','PENDING',:now,'user-1','key-1',:digest,:now,:now)"
                ),
                {"digest": "a" * 64, "now": now},
            )

        for statement in (
            "UPDATE account_security_events SET event_type='changed' "
            "WHERE id='event-1'",
            "DELETE FROM account_security_events WHERE id='event-1'",
            "DELETE FROM company_invitations WHERE id='invite-1'",
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(text(statement))

        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE company_invitations SET status='REVOKED', revoked_at=:now "
                    "WHERE id='invite-1'"
                ),
                {"now": now},
            )

        command.downgrade(config, "0036_platform_db_roles")
        inspector = inspect(engine)
        assert AUTH_TABLES.isdisjoint(inspector.get_table_names())
        assert "status" not in {
            column["name"] for column in inspector.get_columns("users")
        }
    finally:
        engine.dispose()


def test_0037_rejects_casefold_duplicates_before_any_schema_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _use_unprotected_sqlite(
        monkeypatch,
        tmp_path / "duplicate-email.sqlite",
    )
    config = _config()
    command.upgrade(config, "0036_platform_db_roles")
    engine = create_engine(database_url)
    try:
        now = "2026-08-20 00:00:00+00:00"
        with engine.begin() as connection:
            for user_id, email in (
                ("user-1", "Alias@example.com"),
                ("user-2", "alias@example.com"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id,email,display_name,is_platform_admin,created_at,updated_at) "
                        "VALUES (:id,:email,'Alias',0,:now,:now)"
                    ),
                    {"id": user_id, "email": email, "now": now},
                )

        with pytest.raises(
            RuntimeError,
            match="user email casefold inventory is not unique",
        ):
            command.upgrade(config, "head")

        inspector = inspect(engine)
        assert AUTH_TABLES.isdisjoint(inspector.get_table_names())
        assert "status" not in {
            column["name"] for column in inspector.get_columns("users")
        }
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0036_platform_db_roles"
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("column_values", "expected_constraint"),
    (
        ({"status": "ACTIVE", "auth_version": 0}, "ck_users_auth_version"),
        (
            {
                "status": "DEACTIVATED",
                "auth_version": 1,
                "deactivated_at": None,
            },
            "ck_users_status_deactivated",
        ),
        (
            {
                "status": "ACTIVE",
                "auth_version": 1,
                "deactivated_at": "2026-08-20 00:00:00+00:00",
            },
            "ck_users_status_deactivated",
        ),
    ),
)
def test_user_lifecycle_constraints_are_database_enforced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    column_values: dict[str, object],
    expected_constraint: str,
) -> None:
    database_url = _use_unprotected_sqlite(
        monkeypatch,
        tmp_path / f"{expected_constraint}-{column_values['status']}.sqlite",
    )
    command.upgrade(_config(), "head")
    engine = create_engine(database_url)
    try:
        values = {
            "id": "invalid-user",
            "email": "invalid@example.com",
            "display_name": "Invalid",
            "is_platform_admin": False,
            "status": column_values["status"],
            "auth_version": column_values["auth_version"],
            "deactivated_at": column_values.get("deactivated_at"),
            "now": "2026-08-20 00:00:00+00:00",
        }
        with pytest.raises(IntegrityError, match=expected_constraint):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id,email,display_name,is_platform_admin,status,auth_version,"
                        "deactivated_at,created_at,updated_at) VALUES "
                        "(:id,:email,:display_name,:is_platform_admin,:status,"
                        ":auth_version,:deactivated_at,:now,:now)"
                    ),
                    values,
                )
    finally:
        engine.dispose()
