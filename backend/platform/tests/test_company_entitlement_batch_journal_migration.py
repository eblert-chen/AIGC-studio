from __future__ import annotations

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, inspect, text


PREVIOUS_HEAD = "0041_model_capability_releases"
JOURNAL = "company_entitlement_batch_journals"


def _config(project_root: Path, database_path: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


def _head(engine) -> str:
    with engine.connect() as connection:
        return str(connection.scalar(text("SELECT version_num FROM alembic_version")))


def _seed_user(connection, *, user_id: str, email: str) -> None:
    connection.execute(
        text(
            "INSERT INTO users ("
            "id, email, display_name, is_platform_admin, status, auth_version, "
            "created_at, updated_at"
            ") VALUES ("
            ":id, :email, :display_name, 1, 'ACTIVE', 1, :at, :at"
            ")"
        ),
        {
            "id": user_id,
            "email": email,
            "display_name": user_id,
            "at": "2026-08-01 00:00:00",
        },
    )


def _seed_legacy_audit(
    connection,
    *,
    audit_id: str,
    actor_user_id: str,
    key: str,
    request_hash: str,
    created_at: str,
) -> None:
    connection.execute(
        text(
            "INSERT INTO audit_logs ("
            "id, actor_user_id, action, target_type, target_id, "
            "before_summary, after_summary, outcome, request_id, created_at"
            ") VALUES ("
            ":id, :actor, 'company.entitlements.batch', 'entitlement_batch', "
            ":key, :before_summary, :after_summary, 'SUCCEEDED', :request_id, :at"
            ")"
        ),
        {
            "id": audit_id,
            "actor": actor_user_id,
            "key": key,
            "before_summary": json.dumps({"snapshot": "legacy"}),
            "after_summary": json.dumps({"request_hash": request_hash}),
            "request_id": f"request-{audit_id}",
            "at": created_at,
        },
    )


def test_0042_sqlite_round_trip_and_legacy_keys_are_globally_frozen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "company-entitlement-journal.db"
    config = _config(project_root, database_path)

    command.upgrade(config, PREVIOUS_HEAD)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _head(engine) == PREVIOUS_HEAD
    assert JOURNAL not in inspect(engine).get_table_names()
    with engine.begin() as connection:
        _seed_user(connection, user_id="legacy-actor-a", email="a@example.test")
        _seed_user(connection, user_id="legacy-actor-b", email="b@example.test")
        _seed_legacy_audit(
            connection,
            audit_id="legacy-audit-a",
            actor_user_id="legacy-actor-a",
            key="legacy-shared-key",
            request_hash="a" * 64,
            created_at="2026-08-01 00:00:00",
        )
        _seed_legacy_audit(
            connection,
            audit_id="legacy-audit-b",
            actor_user_id="legacy-actor-b",
            key="legacy-shared-key",
            request_hash="b" * 64,
            created_at="2026-08-02 00:00:00",
        )
        _seed_legacy_audit(
            connection,
            audit_id="legacy-audit-c",
            actor_user_id="legacy-actor-a",
            key="legacy-single-key",
            request_hash="c" * 64,
            created_at="2026-08-03 00:00:00",
        )
    engine.dispose()

    command.upgrade(config, "0042_entitlement_batch_journal")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _head(engine) == "0042_entitlement_batch_journal"
    inspector = inspect(engine)
    assert JOURNAL in inspector.get_table_names()
    assert {
        "id",
        "idempotency_key",
        "actor_user_id",
        "request_sha256",
        "expected_snapshot",
        "state",
        "result_payload",
        "created_at",
        "updated_at",
    } <= {str(column["name"]) for column in inspector.get_columns(JOURNAL)}
    assert {
        constraint["name"] for constraint in inspector.get_unique_constraints(JOURNAL)
    } == {"uq_company_entitlement_batch_idempotency"}
    assert {
        index["name"] for index in inspector.get_indexes(JOURNAL)
    } == {"ix_company_entitlement_batch_actor_created"}
    with engine.connect() as connection:
        rows = {
            row["idempotency_key"]: row
            for row in connection.execute(
                text(
                    "SELECT idempotency_key, actor_user_id, request_sha256, "
                    "expected_snapshot, state, result_payload "
                    f"FROM {JOURNAL} ORDER BY idempotency_key"
                )
            ).mappings()
        }
    assert set(rows) == {"legacy-shared-key", "legacy-single-key"}
    shared = rows["legacy-shared-key"]
    assert shared["state"] == "frozen"
    assert len(shared["request_sha256"]) == 64
    assert len(shared["expected_snapshot"]) == 64
    payload = json.loads(shared["result_payload"])
    assert payload["legacy_frozen"] is True
    assert payload["audit_count"] == 2
    assert payload["actor_count"] == 2
    assert payload["request_hash_count"] == 2
    assert payload["conflicting_legacy_rows"] is True
    assert len(payload["legacy_audit_set_sha256"]) == 64
    engine.dispose()

    command.downgrade(config, PREVIOUS_HEAD)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert _head(engine) == PREVIOUS_HEAD
    assert JOURNAL not in inspect(engine).get_table_names()
    engine.dispose()

    command.upgrade(config, "head")
    command.check(config)


def test_protected_0042_migration_attests_before_ddl_and_acl_after_backfill() -> None:
    source = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "0042_company_entitlement_batch_journal.py"
    ).read_text(encoding="utf-8")
    upgrade = source.split("def upgrade() -> None:", 1)[1].split(
        "def downgrade() -> None:", 1
    )[0]
    source_gate = upgrade.index("validate_platform_migration_source_state(")
    attestation = upgrade.index("attest_platform_database_connection(")
    first_ddl = upgrade.index("op.create_table(")
    legacy_freeze = upgrade.index("_freeze_legacy_audit_keys(connection)")
    apply_acl = upgrade.index("_apply_journal_acl()")
    post_ddl_acl = upgrade.index("validate_platform_database_acl_evidence(")
    assert source_gate < attestation < first_ddl < legacy_freeze < apply_acl < post_ddl_acl
    assert "policy=policy_v7" in upgrade
    assert 'frozenset({"SELECT", "INSERT", "UPDATE"})' in (
        Path(__file__).parents[1]
        / "platform_api"
        / "database_privileges_v7.py"
    ).read_text(encoding="utf-8")
