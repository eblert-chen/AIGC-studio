from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from platform_api.models import Company, CompanyModelGrant, CompanyPointPriceVersion, ModelDefinition, PersonalRetailModelGrant, User
from platform_api.relay_client import RelayModelReleaseEvidence

from .test_model_commercial_release import _admin_headers, _commercial_body, _prepare_video_draft


def _released(app, client):
    assert not getattr(app.state, "legacy_commercial_gate_isolated", False)
    headers = _admin_headers(client, "price-policy")
    model = _prepare_video_draft(app, client, headers)
    with app.state.session_factory.begin() as session:
        company = Company(name="Pricing policy company", billing_version=2)
        target = Company(name="Pricing policy target", billing_version=2)
        session.add_all([company, target])
        session.flush()
        company_id, target_id = company.id, target.id
    approved = client.put(
        f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan",
        headers=headers, json=_commercial_body(model=model),
    )
    assert approved.status_code == 200, approved.text
    result = client.post("/api/v1/platform-admin/model-commercial-releases/reconcile", headers=headers)
    assert result.status_code == 200 and result.json()["released_count"] == 1, result.text
    assert result.json()["items"][0]["minimum_price_points"] == 12
    return headers, model, company_id, target_id


def test_legacy_unit_dependency_isolation_does_not_weaken_a_different_commercial_app(app, client):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from platform_api.main import create_app
    from .legacy_commercial_isolation import isolate_legacy_commercial_gate

    isolated_engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    try:
        legacy_app = create_app(settings=app.state.settings, engine=isolated_engine)
        isolate_legacy_commercial_gate(legacy_app)
        assert legacy_app.state.legacy_commercial_gate_isolated is True
        headers, model, company_id, _ = _released(app, client)
        denied = _write(app, client, headers, model, company_id, "personal", 1)
        assert denied.status_code == 409 and "最低价 12" in denied.json()["detail"]
    finally:
        isolated_engine.dispose()


def _personal_payload(client, headers, model, price, enabled=True):
    rows = client.get("/api/v1/platform-admin/personal-model-grants", headers=headers).json()
    current = next(row for row in rows if row["model_id"] == model["id"])
    return {
        "expected_capability_version": current["capability_version"],
        "expected_quote_revision": current["quote_revision"],
        "enabled": enabled,
        "price_per_second_points": price,
        "price_per_item_points": None,
        "config_override": {},
        "reason": "Explicit customer price decision",
    }


def _company_payload(app, model, company_id, price, enabled=True):
    with app.state.session_factory() as session:
        grant = session.scalar(select(CompanyModelGrant).where(
            CompanyModelGrant.company_id == company_id, CompanyModelGrant.model_id == model["id"],
        ))
        stamp = grant.updated_at
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
    return {
        "model_id": model["id"], "expected_updated_at": stamp.isoformat(),
        "enabled": enabled, "price_per_second_points": price,
        "price_per_item_points": None, "config_override": {},
    }


def _write(app, client, headers, model, company_id, scope, price, enabled=True):
    if scope == "personal":
        path = f"/api/v1/platform-admin/personal-model-grants/{model['id']}"
        body = _personal_payload(client, headers, model, price, enabled)
    else:
        path = f"/api/v1/platform-admin/companies/{company_id}/model-grants"
        body = _company_payload(app, model, company_id, price, enabled)
    return client.put(path, headers=headers, json=body)


@pytest.mark.parametrize("scope", ["personal", "company"])
@pytest.mark.parametrize("price,expected", [(1, 409), (12, 200), (20, 200)])
def test_all_direct_price_writes_enforce_the_commercial_floor(app, client, scope, price, expected):
    headers, model, company_id, _ = _released(app, client)
    response = _write(app, client, headers, model, company_id, scope, price)
    assert response.status_code == expected, response.text
    if expected == 409:
        assert "最低价 12" in response.json()["detail"]
    with app.state.session_factory() as session:
        personal = session.scalar(select(PersonalRetailModelGrant).where(PersonalRetailModelGrant.model_id == model["id"]))
        company = session.scalar(select(CompanyModelGrant).where(CompanyModelGrant.company_id == company_id, CompanyModelGrant.model_id == model["id"]))
        assert personal.price_per_second_points == (price if scope == "personal" and expected == 200 else 12)
        assert company.price_per_second_points == (price if scope == "company" and expected == 200 else 12)


@pytest.mark.parametrize("scope", ["personal", "company"])
def test_disable_cannot_smuggle_a_lower_price_but_unchanged_stop_is_available(app, client, scope):
    headers, model, company_id, _ = _released(app, client)
    assert _write(app, client, headers, model, company_id, scope, 1, enabled=False).status_code == 409
    app.state.relay_client = None
    response = _write(app, client, headers, model, company_id, scope, 12, enabled=False)
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is False


@pytest.mark.parametrize("scope", ["personal", "company"])
@pytest.mark.parametrize("drift", ["cost_source", "cost_rate", "route", "expired"])
def test_stale_or_different_live_cost_authority_cannot_reprice(app, client, monkeypatch, scope, drift):
    headers, model, company_id, _ = _released(app, client)
    evidence = app.state.relay_client.evidence.model_dump()
    item = evidence["models"][0]
    route = item["routes"][0]
    if drift == "cost_source":
        for rectangle in route["provider_cost_rectangles"]:
            rectangle["source_document_sha256"] = "9" * 64
    elif drift == "cost_rate":
        for rectangle in route["provider_cost_rectangles"]:
            rectangle["unit_amount_cents"] = 200
    elif drift == "route":
        route["provider_account_id"] = "different-account"
    else:
        now = datetime.now(timezone.utc)
        route["latest_successful_test_at"] = now - timedelta(seconds=10)
        route["fresh_until"] = now + timedelta(seconds=10)
        monkeypatch.setattr("platform_api.services.commercial_pricing.utcnow", lambda: now + timedelta(seconds=11))
    app.state.relay_client.evidence = RelayModelReleaseEvidence.model_validate(evidence)
    response = _write(app, client, headers, model, company_id, scope, 20)
    assert response.status_code == 409, response.text


def _cell(model, company_id, price):
    return {
        "company_id": company_id, "item_kind": "model", "item_id": model["id"],
        "enabled": True, "billing_unit": "POINT", "billing_version": 2,
        "price_per_second_points": price, "config_override": {},
    }


def test_batch_and_template_reject_low_prices_before_any_write(app, client):
    headers, model, source, target = _released(app, client)
    changes = [_cell(model, source, 20), _cell(model, target, 1)]
    response = client.post("/api/v1/platform-admin/entitlements/batch/preview", headers=headers, json={"changes": changes})
    assert response.status_code == 409, response.text
    cell = _cell(model, target, 1)
    cell.pop("company_id")
    response = client.post("/api/v1/platform-admin/entitlements/templates/preview", headers=headers, json={
        "template_name": "Cannot bypass margin", "template_version": 1,
        "target_company_ids": [source, target], "cells": [cell],
    })
    assert response.status_code == 409, response.text
    with app.state.session_factory() as session:
        prices = list(session.scalars(select(CompanyModelGrant.price_per_second_points).where(CompanyModelGrant.model_id == model["id"])))
        assert prices == [12, 12]


def test_personal_batch_executes_valid_price_and_rejects_cost_drift_after_preview(app, client):
    headers, model, company_id, _ = _released(app, client)
    change = _personal_payload(client, headers, model, 20)
    change.pop("reason")
    change["model_id"] = model["id"]
    preview = client.post("/api/v1/platform-admin/personal-model-grants/batch/preview", headers=headers, json={"changes": [change]})
    assert preview.status_code == 200, preview.text
    body = {"changes": [change], "expected_snapshot": preview.json()["snapshot"], "idempotency_key": "pricing-batch", "reason": "Approve valid retail price"}
    valid = client.post("/api/v1/platform-admin/personal-model-grants/batch/execute", headers=headers, json=body)
    assert valid.status_code == 200, valid.text
    change = _personal_payload(client, headers, model, 22)
    change.pop("reason")
    change["model_id"] = model["id"]
    preview = client.post("/api/v1/platform-admin/personal-model-grants/batch/preview", headers=headers, json={"changes": [change]})
    evidence = app.state.relay_client.evidence.model_dump()
    for rectangle in evidence["models"][0]["routes"][0]["provider_cost_rectangles"]:
        rectangle["source_document_sha256"] = "9" * 64
    app.state.relay_client.evidence = RelayModelReleaseEvidence.model_validate(evidence)
    body.update(changes=[change], expected_snapshot=preview.json()["snapshot"], idempotency_key="pricing-batch-stale")
    response = client.post("/api/v1/platform-admin/personal-model-grants/batch/execute", headers=headers, json=body)
    assert response.status_code == 409, response.text


def test_copy_validates_destination_and_current_price_authority(app, client):
    headers, model, source, target = _released(app, client)
    assert _write(app, client, headers, model, source, "company", 20).status_code == 200
    body = {"source_company_id": source, "target_company_ids": [target], "include_resources": False}
    preview = client.post("/api/v1/platform-admin/entitlements/copy/preview", headers=headers, json=body)
    assert preview.status_code == 200, preview.text
    body.update(expected_snapshot=preview.json()["snapshot"], idempotency_key="copy-commercial", reason="Copy valid commercial price")
    executed = client.post("/api/v1/platform-admin/entitlements/copy/execute", headers=headers, json=body)
    assert executed.status_code == 200, executed.text
    with app.state.session_factory.begin() as session:
        # A legacy underpriced row is data to repair, never new copy authority.
        row = session.scalar(select(CompanyModelGrant).where(CompanyModelGrant.company_id == source, CompanyModelGrant.model_id == model["id"]))
        row.price_per_second_points = 1
    body = {"source_company_id": source, "target_company_ids": [target], "include_resources": False}
    preview = client.post("/api/v1/platform-admin/entitlements/copy/preview", headers=headers, json=body)
    assert preview.status_code == 409, preview.text


@pytest.mark.parametrize("scope", ["personal", "company"])
def test_missing_plan_for_approved_relay_model_is_not_legacy_exemption(app, client, scope):
    headers = _admin_headers(client, "missing-price-plan")
    model = _prepare_video_draft(app, client, headers)
    with app.state.session_factory.begin() as session:
        stored = session.get(ModelDefinition, model["id"])
        stored.relay_capability_revision = stored.relay_capability_candidate_revision
        stored.relay_capability_approved_catalog_revision = stored.relay_capability_candidate_catalog_revision
        stored.relay_capability_approved_ceiling = deepcopy(stored.relay_capability_candidate)
        stored.active = True
        stored.published_at = datetime.now(timezone.utc)
        company = Company(name="Unpriced model customer", billing_version=2)
        session.add(company)
        session.flush()
        company_id = company.id
    if scope == "personal":
        body = {"expected_capability_version": model["capability_version"], "expected_quote_revision": None,
                "enabled": True, "price_per_second_points": 20, "config_override": {}, "reason": "Cannot price without plan"}
        path = f"/api/v1/platform-admin/personal-model-grants/{model['id']}"
    else:
        body = {"model_id": model["id"], "expected_updated_at": None, "enabled": True,
                "price_per_second_points": 20, "config_override": {}}
        path = f"/api/v1/platform-admin/companies/{company_id}/model-grants"
    response = client.put(path, headers=headers, json=body)
    assert response.status_code == 409, response.text
    assert "缺少商业价格计划" in response.json()["detail"]


def test_reprice_preserves_the_original_immutable_company_price_version(app, client):
    headers, model, company_id, _ = _released(app, client)
    with app.state.session_factory() as session:
        grant = session.scalar(select(CompanyModelGrant).where(CompanyModelGrant.company_id == company_id, CompanyModelGrant.model_id == model["id"]))
        original_id = grant.point_price_active_version_id
        original_digest = session.get(CompanyPointPriceVersion, original_id).content_sha256
    changed = _write(app, client, headers, model, company_id, "company", 20)
    assert changed.status_code == 200, changed.text
    with app.state.session_factory() as session:
        old = session.get(CompanyPointPriceVersion, original_id)
        grant = session.scalar(select(CompanyModelGrant).where(CompanyModelGrant.company_id == company_id, CompanyModelGrant.model_id == model["id"]))
        new = session.get(CompanyPointPriceVersion, grant.point_price_active_version_id)
        assert old.unit_price_points == 12 and old.content_sha256 == original_digest
        assert new.id != old.id and new.unit_price_points == 20 and new.supersedes_version_id == old.id


def test_model_republication_cannot_reactivate_an_existing_underpriced_grant(app, client):
    headers, model, company_id, _ = _released(app, client)
    with app.state.session_factory.begin() as session:
        stored = session.get(ModelDefinition, model["id"])
        stored.active = False
        grant = session.scalar(select(CompanyModelGrant).where(CompanyModelGrant.company_id == company_id, CompanyModelGrant.model_id == model["id"]))
        grant.price_per_second_points = 1
    response = client.post(f"/api/v1/platform-admin/models/{model['id']}/publish", headers=headers)
    assert response.status_code == 409, response.text
    with app.state.session_factory() as session:
        assert session.get(ModelDefinition, model["id"]).active is False


@pytest.mark.parametrize("scope", ["personal", "company"])
def test_price_authority_does_not_grant_an_ordinary_user_admin_permissions(app, client, scope):
    headers, model, company_id, _ = _released(app, client)
    if scope == "personal":
        body = _personal_payload(client, headers, model, 20)
        path = f"/api/v1/platform-admin/personal-model-grants/{model['id']}"
    else:
        body = _company_payload(app, model, company_id, 20)
        path = f"/api/v1/platform-admin/companies/{company_id}/model-grants"
    with app.state.session_factory.begin() as session:
        user = User(email="pricing-ordinary@example.test", display_name="Ordinary user")
        session.add(user)
        session.flush()
        user_id = user.id
    denied = client.put(path, headers={"X-Platform-Admin-User-ID": user_id}, json=body)
    assert denied.status_code == 403, denied.text
