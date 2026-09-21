"""Immutable repricing recovery with provider doubles, never a paid provider run."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from platform_api.database_privileges import PLATFORM_ALEMBIC_HEAD
from platform_api.main import create_app
from platform_api.models import CompanyModelGrant, CompanyPointPriceVersion, ModelCommercialReleaseExecution, ModelDefinition, PersonalRetailModelGrant
from platform_api.relay_client import RelayModelReleaseEvidence

from .conftest import TEST_BOOTSTRAP_TOKEN
from .test_commercial_plan_revisions import _approve, _rotate_and_accept
from .test_commercial_pricing_policy import _company_payload, _released, _write
from .test_model_commercial_release import _commercial_body


def _recovery(app, client, *, stop_model=False):
    headers, model, company_id, target_id = _released(app, client)
    assert _write(app, client, headers, model, company_id, "personal", 40, enabled=False).status_code == 200
    company_body = _company_payload(app, model, company_id, 40, enabled=False)
    company_body.update(call_quota=18, concurrency_limit=2,
                        effective_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                        expires_at=(datetime.now(timezone.utc) + timedelta(days=4)).isoformat())
    priced = client.put(f"/api/v1/platform-admin/companies/{company_id}/model-grants", headers=headers, json=company_body)
    assert priced.status_code == 200, priced.text
    with app.state.session_factory() as session:
        original = session.scalar(select(ModelCommercialReleaseExecution))
        old_plan_id = original.plan_id
        old_receipt, old_receipt_sha = deepcopy(original.publication_receipt), original.publication_receipt_sha256
        old_released_at = original.released_at
        published_at = session.get(ModelDefinition, model["id"]).published_at
        target_grant = session.scalar(select(CompanyModelGrant).where(
            CompanyModelGrant.company_id == target_id, CompanyModelGrant.model_id == model["id"]))
        old_version_id = target_grant.point_price_active_version_id
        old_version = session.get(CompanyPointPriceVersion, old_version_id)
        old_version_sha = old_version.content_sha256
        assert old_version.unit_price_points == 12
    if stop_model:
        stopped = client.post(f"/api/v1/platform-admin/models/{model['id']}/disable", headers=headers)
        assert stopped.status_code == 200, stopped.text

    _rotate_and_accept(app)
    evidence = app.state.relay_client.evidence.model_dump()
    for rectangle in evidence["models"][0]["routes"][0]["provider_cost_rectangles"]:
        rectangle["source_document_sha256"] = "9" * 64
        rectangle["unit_amount_cents"] = 160
    app.state.relay_client.evidence = RelayModelReleaseEvidence.model_validate(evidence)
    body = _commercial_body(
        model=model, expected_routing_release_sha256="sha256:" + "4" * 64
    )
    body.update(idempotency_key=body["idempotency_key"] + ":new-cost",
                provider_cost_evidence_sha256="9" * 64,
                supersedes_plan_id=old_plan_id)
    body["provider_cost_formula"]["components"][0]["rate_micros"] = 1_600_000
    approved = _approve(client, headers, model, body)
    assert approved.status_code == 200, approved.text
    assert approved.json()["minimum_price_points"] == 23
    assert approved.json()["revision"] == 2
    assert approved.json()["state"] == "approved"
    # Even a proposed legal price is not current authority before release.
    assert _write(app, client, headers, model, company_id, "personal", 40).status_code == 409
    assert _write(app, client, headers, model, target_id, "company", 40).status_code == 409
    blocked_publish = client.post(f"/api/v1/platform-admin/models/{model['id']}/publish", headers=headers)
    # The HTTP endpoint preserves an already-active publish's no-op replay;
    # actual reactivation cannot publish while the successor is pending.
    assert blocked_publish.status_code == (409 if stop_model else 200), blocked_publish.text

    # Changing evidence again after approval cannot partially update grants.
    good_evidence = app.state.relay_client.evidence
    changed = good_evidence.model_dump()
    changed["models"][0]["routes"][0]["provider_cost_rectangles"][0]["source_document_sha256"] = "8" * 64
    app.state.relay_client.evidence = RelayModelReleaseEvidence.model_validate(changed)
    blocked = client.post("/api/v1/platform-admin/model-commercial-releases/reconcile", headers=headers)
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["released_count"] == 0
    assert next(item for item in blocked.json()["items"] if item["id"] == approved.json()["id"])["state"] == "blocked"
    with app.state.session_factory() as session:
        assert session.get(CompanyModelGrant, target_grant.id).price_per_second_points == 12
    app.state.relay_client.evidence = good_evidence

    released = client.post("/api/v1/platform-admin/model-commercial-releases/reconcile", headers=headers)
    assert released.status_code == 200, released.text
    assert released.json()["released_count"] == 1, released.text
    result = next(item for item in released.json()["items"] if item["id"] == approved.json()["id"])
    assert result["state"] == "released"
    receipt = result["publication_receipt"]
    assert receipt["schema_version"] == 4
    assert set(receipt) == {
        "schema_version",
        "plan_id",
        "plan_content_sha256",
        "model_id",
        "model_slug",
        "capability_revision",
        "route_identity_sha256",
        "relay_route_release",
        "personal_grant_id",
        "personal_price_points",
        "personal_enabled",
        "personal_quote_revision",
        "personal_config_override",
        "personal_call_quota",
        "personal_concurrency_limit",
        "enterprise_price_points",
        "company_grants",
        "distribution_policy",
        "company_ids",
        "company_grant_count",
        "released_at",
    }
    assert receipt["relay_route_release"]["model_release_revision"] == (
        "release-revision-2"
    )
    assert receipt["relay_route_release"]["published_route_revision"] == (
        "sha256:" + "6" * 64
    )
    assert receipt["personal_price_points"] == 40
    assert receipt["personal_enabled"] is False
    assert receipt["personal_call_quota"] is None
    assert receipt["personal_concurrency_limit"] is None
    assert {item["company_id"]: item["unit_price_points"] for item in receipt["company_grants"]} == {company_id: 40, target_id: 23}
    with app.state.session_factory() as session:
        original = session.scalar(select(ModelCommercialReleaseExecution).where(ModelCommercialReleaseExecution.plan_id == old_plan_id))
        # The receipt remains immutable history, while continuous Relay
        # evidence reconciliation marks its old route authority invalid.
        assert original.state == "blocked"
        assert original.last_blocker_code == "published_route_revision_drift"
        assert original.publication_receipt == old_receipt
        assert original.publication_receipt_sha256 == old_receipt_sha
        assert original.released_at is None
        assert old_released_at is not None
        current_model = session.get(ModelDefinition, model["id"])
        assert current_model.published_at == published_at
        # Catalog reconciliation invalidates the old route authority before
        # the successor plan is released.  Publishing a successor price must
        # not silently reactivate the model or any previously enabled grant.
        assert current_model.active is False
        grant = session.scalar(select(CompanyModelGrant).where(
            CompanyModelGrant.company_id == company_id, CompanyModelGrant.model_id == model["id"]))
        assert grant.enabled is False
        assert grant.price_per_second_points == 40
        assert grant.call_quota == 18 and grant.concurrency_limit == 2
        assert grant.effective_at is not None and grant.expires_at is not None
        target = session.get(CompanyModelGrant, target_grant.id)
        assert target.enabled is False and target.price_per_second_points == 23
        assert target.point_price_active_version_id != old_version_id
        new_version = session.get(CompanyPointPriceVersion, target.point_price_active_version_id)
        assert new_version.supersedes_version_id == old_version_id
        old_version = session.get(CompanyPointPriceVersion, old_version_id)
        assert old_version.content_sha256 == old_version_sha
        assert old_version.unit_price_points == 12
        personal = session.scalar(select(PersonalRetailModelGrant).where(PersonalRetailModelGrant.model_id == model["id"]))
        assert personal.enabled is False and personal.price_per_second_points == 40
    # Recovery is complete only after explicit reactivation; ordinary
    # authorized price writes can then resume under the new released floor.
    assert client.post(
        f"/api/v1/platform-admin/models/{model['id']}/publish", headers=headers
    ).status_code == 200
    assert _write(app, client, headers, model, target_id, "company", 12).status_code == 409
    assert _write(app, client, headers, model, target_id, "company", 23).status_code == 200
    assert _write(app, client, headers, model, company_id, "personal", 23).status_code == 200
    replay = _approve(client, headers, model, body)
    assert replay.status_code == 200 and replay.json()["publication_receipt"] == receipt
    body["idempotency_key"] += ":stale"
    assert _approve(client, headers, model, body).status_code == 409
    return original.id, old_receipt_sha


@pytest.mark.parametrize("stop_model", [False, True])
def test_published_price_successor_recovers_without_rewriting_history(app, client, stop_model):
    _recovery(app, client, stop_model=stop_model)


def test_latest_migrated_postgres_recovers_and_guards_released_history(app, monkeypatch):
    url = os.getenv("PLATFORM_REVIEW_POSTGRES_URL")
    if not url:
        pytest.skip("isolated PostgreSQL review database not configured")
    base = create_engine(url, hide_parameters=True)
    schema = "price_recovery_" + uuid4().hex
    with base.begin() as connection:
        assert str(connection.scalar(text("select current_database()"))).startswith("review_")
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    scoped_url = make_url(url).update_query_dict({"options": "-csearch_path=" + schema})
    engine = create_engine(scoped_url, hide_parameters=True)
    try:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        root = Path(__file__).resolve().parents[1]
        cfg = Config(str(root / "alembic.ini"))
        cfg.set_main_option("script_location", str(root / "migrations"))
        cfg.set_main_option("sqlalchemy.url", scoped_url.render_as_string(hide_password=False).replace("%", "%%"))
        command.upgrade(cfg, "head")
        with engine.connect() as connection:
            assert connection.scalar(text("select current_schema()")) == schema
            assert connection.scalar(text("select version_num from alembic_version")) == PLATFORM_ALEMBIC_HEAD
        settings = app.state.settings.model_copy(update={"database_url": str(scoped_url), "auto_create_tables": False})
        pg_app = create_app(settings=settings, engine=engine)
        with TestClient(pg_app, headers={"X-Bootstrap-Token": TEST_BOOTSTRAP_TOKEN}) as client:
            execution_id, receipt_sha = _recovery(pg_app, client, stop_model=True)
        for assignment in ("state='approved',released_at=NULL", "released_at=released_at + interval '1 second'", "publication_receipt='{}'::json"):
            with pytest.raises(DBAPIError), engine.begin() as connection:
                connection.execute(text(f"UPDATE model_commercial_release_executions SET {assignment} WHERE id=:id"), {"id": execution_id})
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT publication_receipt_sha256 FROM model_commercial_release_executions WHERE id=:id"), {"id": execution_id}) == receipt_sha
    finally:
        engine.dispose()
        # Only the exact random schema created above; never the public schema,
        # never an operator's database or any shared production object.
        with base.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
            assert connection.scalar(text("SELECT count(*) FROM pg_namespace WHERE nspname=:name"), {"name": schema}) == 0
        base.dispose()
