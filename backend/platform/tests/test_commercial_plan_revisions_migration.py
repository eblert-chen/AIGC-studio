from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError

from platform_api.models import ModelCommercialReleaseExecution, ModelCommercialReleasePlan
from platform_api import database_privileges as facade
from platform_api import database_privileges_v19 as previous_policy
from platform_api import database_privileges_v20 as policy
from platform_api import database_privileges_behavior_v20 as behavior
from .test_model_commercial_release import _admin_headers, _commercial_body, _prepare_video_draft
from .test_platform_database_privileges_v19 import _runtime_evidence


HEAD = "0055_commercial_plan_revisions"


def _config(path: Path) -> Config:
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", "sqlite:///" + path.as_posix())
    return cfg


def test_0055_preserves_old_approval_and_rejects_forks_and_reactivation(tmp_path, monkeypatch, app, client):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    headers = _admin_headers(client, "migration-history")
    model = _prepare_video_draft(app, client, headers, evidence_status="blocked")
    approved = client.put(f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan", headers=headers, json=_commercial_body(model=model))
    assert approved.status_code == 200, approved.text
    with app.state.session_factory() as session:
        plan_values = dict(session.execute(select(ModelCommercialReleasePlan.__table__)).mappings().one())
        execution_values = dict(session.execute(select(ModelCommercialReleaseExecution.__table__)).mappings().one())

    cfg = _config(tmp_path / "history.db")
    command.upgrade(cfg, "0050_model_commercial_release")
    engine = create_engine(cfg.get_main_option("sqlalchemy.url"))
    try:
        old_plan = Table("model_commercial_release_plans", MetaData(), autoload_with=engine)
        old_execution = Table("model_commercial_release_executions", MetaData(), autoload_with=engine)
        historical = {key: value for key, value in plan_values.items() if key in old_plan.c}
        with engine.begin() as connection:
            connection.execute(old_plan.insert(), historical)
            connection.execute(old_execution.insert(), execution_values)
        command.upgrade(cfg, "0051_provider_account_evidence")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0051_provider_account_evidence"
            assert connection.scalar(text("SELECT content_sha256 FROM model_commercial_release_plans WHERE id=:id"), {"id": historical["id"]}) == historical["content_sha256"]
        command.upgrade(cfg, HEAD)
        current_plan = Table("model_commercial_release_plans", MetaData(), autoload_with=engine)
        with engine.connect() as connection:
            stored = dict(connection.execute(select(current_plan)).mappings().one())
        assert stored["revision"] == 1
        assert stored["supersedes_plan_id"] is None
        assert stored["request_fingerprint"] is None
        assert {key: stored[key] for key in historical} == historical

        successor = dict(stored, id=str(uuid4()), revision=2, supersedes_plan_id=stored["id"],
                         idempotency_key="new-approval-2", content_sha256="f" * 64,
                         request_fingerprint="e" * 64,
                         approved_route_identity=plan_values["approved_route_identity"],
                         approved_route_identity_sha256=plan_values["approved_route_identity_sha256"])
        with engine.begin() as connection:
            connection.execute(current_plan.insert(), successor)
            connection.execute(text("UPDATE model_commercial_release_executions SET state='superseded' WHERE id=:id"), {"id": execution_values["id"]})
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(current_plan.insert(), dict(successor, id=str(uuid4()), idempotency_key="fork-approval", content_sha256="d" * 64))
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("UPDATE model_commercial_release_executions SET state='approved' WHERE id=:id"), {"id": execution_values["id"]})
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("UPDATE model_commercial_release_plans SET approval_reason='rewrite' WHERE id=:id"), {"id": stored["id"]})
        with pytest.raises(RuntimeError, match="durable commercial approval"):
            command.downgrade(cfg, "0054_input_asset_media_metadata")
    finally:
        engine.dispose()


def test_empty_0055_roundtrip_and_metadata_alignment(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _config(tmp_path / "empty.db")
    command.upgrade(cfg, HEAD)
    engine = create_engine(cfg.get_main_option("sqlalchemy.url"))
    try:
        unique = {tuple(item["column_names"]) for item in inspect(engine).get_unique_constraints("model_commercial_release_plans")}
        assert ("model_id", "candidate_revision", "revision") in unique
        assert ("supersedes_plan_id",) in unique
        assert ("model_id", "candidate_revision") not in unique
    finally:
        engine.dispose()
    command.downgrade(cfg, "0054_input_asset_media_metadata")
    command.upgrade(cfg, HEAD)
    command.upgrade(cfg, "head")
    command.check(cfg)


def test_v20_does_not_inherit_runtime_qualification():
    assert facade.PLATFORM_DATABASE_PRIVILEGE_POLICY_REGISTRY[HEAD] == (policy, behavior)
    assert policy.ALEMBIC_HEAD == HEAD
    assert policy.CATALOG_SHA256 == policy.UNQUALIFIED_CATALOG_SHA256 == "0" * 64
    assert policy.TABLES == previous_policy.TABLES
    assert policy.EXPECTED_TABLE_ACL == previous_policy.EXPECTED_TABLE_ACL
    process_role = "platform-api"
    evidence = replace(_runtime_evidence(process_role), alembic_heads=(HEAD,))
    with pytest.raises(behavior.PlatformDatabaseAttestationError, match="UNQUALIFIED"):
        behavior.validate_platform_database_evidence(evidence, process_role, require_runtime_acl=True, require_head=True)
    with pytest.raises(behavior.PlatformDatabaseAttestationError, match="UNQUALIFIED"):
        behavior.validate_platform_database_acl_evidence(evidence, require_head=True)
