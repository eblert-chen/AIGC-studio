from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from platform_api.models import AuditOutcome, PlatformAdminActivity
from platform_api.services.audit import AuditService

from .test_platform_admin import bootstrap_admin


def test_audit_api_returns_the_durable_execution_outcome(client, app):
    admin_id, headers = bootstrap_admin(client, "outcome-evidence")
    with app.state.session_factory.begin() as session:
        AuditService.append(
            session,
            actor_user_id=admin_id,
            action="operations.fixture.failed",
            target_type="operations_fixture",
            target_id="failed-one",
            before_summary={},
            after_summary={"reason": "durable provider rejection"},
            request_id="outcome-failed",
            outcome=AuditOutcome.FAILED,
        )
        AuditService.append(
            session,
            actor_user_id=admin_id,
            action="operations.fixture.unknown",
            target_type="operations_fixture",
            target_id="unknown-one",
            before_summary={},
            after_summary={"reason": "transport outcome requires reconciliation"},
            request_id="outcome-unknown",
            outcome=AuditOutcome.UNKNOWN,
        )

    response = client.get(
        "/api/v1/platform-admin/audit-logs?page=1&page_size=100",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    outcomes = {item["request_id"]: item["outcome"] for item in response.json()["items"]}
    assert outcomes["outcome-failed"] == "failed"
    assert outcomes["outcome-unknown"] == "unknown"
    # Backwards-compatible callers also store a real default instead of
    # relying on a frontend adapter to label every row successful.
    default_key = next(
        key
        for key in outcomes
        if key not in {"outcome-failed", "outcome-unknown"}
    )
    assert outcomes[default_key] == "succeeded"


def test_platform_admin_directory_exposes_authorized_activity_evidence(client, app):
    admin_id, headers = bootstrap_admin(client, "directory-evidence")

    response = client.get(
        "/api/v1/platform-admin/access/users",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    row = next(item for item in response.json() if item["user_id"] == admin_id)
    assert row["status"] == "active"
    assert row["last_active_at"] is not None

    with app.state.session_factory() as session:
        saved = session.get(PlatformAdminActivity, admin_id)
        assert saved is not None
        assert saved.last_active_at is not None


def test_rejected_platform_admin_permission_does_not_forge_activity(client, app):
    # The first development administrator is the explicit local owner. A later
    # administrator starts fail-closed with no inherited access.
    bootstrap_admin(client, "activity-owner")
    delegated_id, delegated_headers = bootstrap_admin(client, "activity-denied")

    response = client.get(
        "/api/v1/platform-admin/access/users",
        headers=delegated_headers,
    )
    assert response.status_code == 403
    with app.state.session_factory() as session:
        delegated = session.get(PlatformAdminActivity, delegated_id)
        assert delegated is None or delegated.last_active_at is None


def test_exception_aggregate_separates_source_health_from_empty_data(client):
    _, headers = bootstrap_admin(client, "source-evidence")

    response = client.get(
        "/api/v1/platform-admin/analytics/exceptions",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["source_status"] == "available"
    assert set(payload["sources"]) == {
        "publishing",
        "relay",
        "artifact_and_download",
    }
    for source in payload["sources"].values():
        assert source["source_status"] == "available"
        assert source["data_status"] == "empty"
        assert source["exception_count"] == 0
        assert source["returned_count"] == 0

    readiness = client.get(
        "/api/v1/platform-admin/analytics/data-readiness",
        headers=headers,
    )
    assert readiness.status_code == 200, readiness.text
    readiness_sources = readiness.json()["sources"]
    assert readiness_sources["publishing"]["source_status"] == "available"
    assert readiness_sources["publishing"]["data_status"] == "empty"
    artifact_source = readiness_sources["artifact_and_download_evidence"]
    assert artifact_source["source_status"] == "available"
    assert artifact_source["data_status"] == "empty"


def _migration_config(project_root: Path, database_path: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url", f"sqlite:///{database_path.as_posix()}"
    )
    return config


def test_0035_migrates_historical_audit_outcome_without_mutating_audit_rows(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "operations-evidence.db"
    config = _migration_config(project_root, database_path)
    # Exercise this revision independently from the much larger 0034 schema;
    # the only prerequisites here are users and immutable audit_logs, both
    # present at 0033. The repository's full migration tests cover the complete
    # linear chain.
    command.upgrade(config, "0033_relay_backend_affinity")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, email, display_name, is_platform_admin, created_at, updated_at) "
                "VALUES (:id, :email, :name, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": "migration-admin", "email": "migration@example.com", "name": "Migration Admin"},
        )
        connection.execute(
            text(
                "INSERT INTO audit_logs "
                "(id, actor_user_id, action, target_type, target_id, "
                "before_summary, after_summary, request_id, created_at) "
                "VALUES ('migration-audit', 'migration-admin', 'historical.action', "
                "'fixture', 'one', '{}', '{}', 'historical-request', CURRENT_TIMESTAMP)"
            )
        )
    engine.dispose()

    command.stamp(config, "0034_personal_workspaces")
    command.upgrade(config, "0035_operations_evidence")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert inspect(engine).has_table("platform_admin_activity")
    activity_columns = {
        column["name"]
        for column in inspect(engine).get_columns("platform_admin_activity")
    }
    assert activity_columns == {"user_id", "last_active_at"}
    audit_columns = {
        column["name"] for column in inspect(engine).get_columns("audit_logs")
    }
    assert "outcome" in audit_columns
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT outcome FROM audit_logs WHERE id = 'migration-audit'")
        ) == "SUCCEEDED"
    engine.dispose()

    command.downgrade(config, "0034_personal_workspaces")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert "outcome" not in {
        column["name"] for column in inspect(engine).get_columns("audit_logs")
    }
    assert not inspect(engine).has_table("platform_admin_activity")
    engine.dispose()
