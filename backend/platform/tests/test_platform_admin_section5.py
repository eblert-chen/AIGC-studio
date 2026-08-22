from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from platform_api.models import (
    AuditLog,
    CompanyMembership,
    GenerationTask,
    ModelDefinition,
    User,
)
from platform_api.services.billing import WalletService

from .conftest import TEST_RELAY_CAPABILITY_REVISION, bootstrap
from .test_model_capability_v1_contract import _mode, canonical_capability


def _admin(client, suffix: str) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/api/v1/bootstrap/platform-admin",
        json={
            "email": f"section5-admin-{suffix}@example.com",
            "display_name": f"Section 5 Admin {suffix}",
        },
    )
    assert response.status_code == 201, response.text
    user_id = response.json()["user_id"]
    return user_id, {"X-Platform-Admin-User-ID": user_id}


def _tenant_headers(tenant: dict[str, str]) -> dict[str, str]:
    return {
        "X-Company-ID": tenant["company_id"],
        "X-User-ID": tenant["user_id"],
    }


def _admin_company(client, app, admin_headers, suffix: str) -> dict[str, str]:
    owner_email = f"section5-owner-{suffix}@example.com"
    response = client.post(
        "/api/v1/platform-admin/companies",
        headers=admin_headers,
        json={
            "name": f"Section 5 Company {suffix}",
            "owner_email": owner_email,
            "owner_display_name": f"Section 5 Owner {suffix}",
        },
    )
    assert response.status_code == 201, response.text
    company_id = response.json()["id"]
    with app.state.session_factory() as session:
        owner = session.scalar(select(User).where(User.email == owner_email))
        assert owner is not None
        membership = session.scalar(
            select(CompanyMembership).where(
                CompanyMembership.company_id == company_id,
                CompanyMembership.user_id == owner.id,
            )
        )
        assert membership is not None
        return {
            "company_id": company_id,
            "user_id": owner.id,
            "membership_id": membership.id,
        }


def _catalog_model(client, admin_headers, suffix: str) -> dict:
    created = client.post(
        "/api/v1/platform-admin/models",
        headers=admin_headers,
        json={
            "slug": f"section5-model-{suffix}",
            "display_name": f"Section 5 Model {suffix}",
            "provider_key": "section5-provider",
            "billing_mode": "per_item",
            "capabilities": [
                {
                    "key": "generation",
                    "config": canonical_capability(
                        modes={
                            "text_to_video": _mode(output_counts=[1, 2])
                        }
                    ),
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    model = created.json()
    published = client.post(
        f"/api/v1/platform-admin/models/{model['id']}/publish",
        headers=admin_headers,
    )
    assert published.status_code == 200, published.text
    with client.app.state.session_factory.begin() as session:
        stored_model = session.get(ModelDefinition, model["id"])
        assert stored_model is not None
        stored_model.relay_capability_revision = TEST_RELAY_CAPABILITY_REVISION
    return published.json()


def _grant_model(
    client,
    admin_headers,
    *,
    company_id: str,
    model_id: str,
    enabled: bool,
    price_cents: int,
):
    response = client.put(
        f"/api/v1/platform-admin/companies/{company_id}/model-grants",
        headers=admin_headers,
        json={
            "model_id": model_id,
            "enabled": enabled,
            "price_per_item_cents": price_cents,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _recharge(client, admin_headers, *, company_id: str, suffix: str):
    response = client.post(
        f"/api/v1/platform-admin/companies/{company_id}/recharge",
        headers=admin_headers,
        json={
            "amount_cents": 5_000,
            "idempotency_key": f"section5-recharge-{suffix}",
            "note": f"section 5 {suffix}",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create_task(
    client,
    tenant: dict[str, str],
    *,
    model_id: str,
    suffix: str,
    output_count: int = 1,
):
    response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=_tenant_headers(tenant),
        json={
            "model_id": model_id,
            "idempotency_key": f"section5-task-{suffix}",
            "request_payload": {
                "prompt": f"section 5 {suffix}",
                "mode": "text_to_video",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "output_count": output_count,
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _resource(client, admin_headers, *, key: str, kind: str) -> dict:
    response = client.post(
        "/api/v1/platform-admin/resources",
        headers=admin_headers,
        json={
            "key": key,
            "kind": kind,
            "display_name": key,
            "description": f"Section 5 {kind}",
            "active": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _cost_payload(
    *,
    suffix: str,
    amount_cents: int,
    channel_key: str,
    channel_type: str,
    company_id: str | None = None,
    task_id: str | None = None,
    relay_job_id: str | None = None,
    occurred_at: str = "2026-08-05T01:02:03Z",
    evidence_source: str | None = None,
) -> dict:
    payload = {
        "amount_cents": amount_cents,
        "idempotency_key": f"section5-cost-{suffix}",
        "channel_key": channel_key,
        "channel_type": channel_type,
        "occurred_at": occurred_at,
        "external_reference": f"section5-external-{suffix}",
    }
    if company_id is not None:
        payload["company_id"] = company_id
    if task_id is not None:
        payload["task_id"] = task_id
    if relay_job_id is not None:
        payload["relay_job_id"] = relay_job_id
    if evidence_source is not None:
        payload["evidence_source"] = evidence_source
    return payload


def test_section5_admin_routes_are_outside_the_company_owner_boundary(
    client, tenant
):
    owner_as_admin = {"X-Platform-Admin-User-ID": tenant["user_id"]}
    company_id = tenant["company_id"]
    requests = [
        ("GET", "/api/v1/platform-admin/companies", None),
        (
            "POST",
            "/api/v1/platform-admin/companies",
            {
                "name": "Unauthorized Company",
                "owner_email": "unauthorized-owner@example.com",
                "owner_display_name": "Unauthorized Owner",
            },
        ),
        (
            "PATCH",
            f"/api/v1/platform-admin/companies/{company_id}/status",
            {"status": "suspended"},
        ),
        (
            "POST",
            f"/api/v1/platform-admin/companies/{company_id}/recharge",
            {
                "amount_cents": 100,
                "idempotency_key": "unauthorized-recharge",
            },
        ),
        (
            "GET",
            f"/api/v1/platform-admin/companies/{company_id}/entitlements",
            None,
        ),
        ("GET", "/api/v1/platform-admin/resources", None),
        (
            "POST",
            "/api/v1/platform-admin/resources",
            {
                "key": "unauthorized.feature",
                "kind": "feature",
                "display_name": "Unauthorized feature",
            },
        ),
        (
            "PUT",
            "/api/v1/platform-admin/resources/unauthorized-resource",
            {
                "display_name": "Unauthorized feature update",
                "description": "must be rejected before lookup",
                "active": False,
            },
        ),
        ("GET", "/api/v1/platform-admin/channel-costs", None),
        (
            "POST",
            "/api/v1/platform-admin/channel-costs",
            _cost_payload(
                suffix="unauthorized",
                amount_cents=1,
                channel_key="official.unauthorized",
                channel_type="official",
            ),
        ),
        ("GET", "/api/v1/platform-admin/dashboard", None),
    ]

    for method, path, body in requests:
        missing = client.request(method, path, json=body)
        assert missing.status_code == 401, (method, path, missing.text)
        company_owner = client.request(
            method,
            path,
            headers=owner_as_admin,
            json=body,
        )
        assert company_owner.status_code == 403, (
            method,
            path,
            company_owner.text,
        )


def test_admin_company_suspend_is_immediate_and_isolated(
    client, app
):
    _, admin_headers = _admin(client, "company-status")
    first = _admin_company(client, app, admin_headers, "status-a")
    second = _admin_company(client, app, admin_headers, "status-b")

    suspended = client.patch(
        f"/api/v1/platform-admin/companies/{first['company_id']}/status",
        headers=admin_headers,
        json={"status": "suspended"},
    )
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["status"] == "suspended"

    first_me = client.get(
        f"/api/v1/companies/{first['company_id']}/me",
        headers=_tenant_headers(first),
    )
    assert first_me.status_code == 404
    second_me = client.get(
        f"/api/v1/companies/{second['company_id']}/me",
        headers=_tenant_headers(second),
    )
    assert second_me.status_code == 200, second_me.text

    companies = client.get(
        "/api/v1/platform-admin/companies?page=1&page_size=100",
        headers=admin_headers,
    )
    assert companies.status_code == 200, companies.text
    statuses = {item["id"]: item["status"] for item in companies.json()["items"]}
    assert statuses[first["company_id"]] == "suspended"
    assert statuses[second["company_id"]] == "active"

    reenabled = client.patch(
        f"/api/v1/platform-admin/companies/{first['company_id']}/status",
        headers=admin_headers,
        json={"status": "active"},
    )
    assert reenabled.status_code == 200, reenabled.text
    assert client.get(
        f"/api/v1/companies/{first['company_id']}/me",
        headers=_tenant_headers(first),
    ).status_code == 200


def test_company_model_grants_keep_prices_and_tasks_tenant_scoped(
    client, app
):
    first = bootstrap(client, "section5-model-a")
    second = bootstrap(client, "section5-model-b")
    _, admin_headers = _admin(client, "model-grants")
    model = _catalog_model(client, admin_headers, "different-prices")

    first_grant = _grant_model(
        client,
        admin_headers,
        company_id=first["company_id"],
        model_id=model["id"],
        enabled=True,
        price_cents=100,
    )
    second_grant = _grant_model(
        client,
        admin_headers,
        company_id=second["company_id"],
        model_id=model["id"],
        enabled=True,
        price_cents=250,
    )
    assert first_grant["company_id"] != second_grant["company_id"]

    first_models = client.get(
        f"/api/v1/companies/{first['company_id']}/models",
        headers=_tenant_headers(first),
    )
    second_models = client.get(
        f"/api/v1/companies/{second['company_id']}/models",
        headers=_tenant_headers(second),
    )
    assert first_models.status_code == 200, first_models.text
    assert second_models.status_code == 200, second_models.text
    assert first_models.json()[0]["pricing_mode"] == "per_item"
    assert first_models.json()[0]["unit_price_cents"] == 100
    assert second_models.json()[0]["pricing_mode"] == "per_item"
    assert second_models.json()[0]["unit_price_cents"] == 250

    _recharge(
        client,
        admin_headers,
        company_id=first["company_id"],
        suffix="model-a",
    )
    _recharge(
        client,
        admin_headers,
        company_id=second["company_id"],
        suffix="model-b",
    )
    first_task = _create_task(
        client,
        first,
        model_id=model["id"],
        suffix="price-a",
        output_count=2,
    )
    second_task = _create_task(
        client,
        second,
        model_id=model["id"],
        suffix="price-b",
        output_count=2,
    )
    assert first_task["quote_cents"] == 200
    assert second_task["quote_cents"] == 500

    first_entitlements = client.get(
        f"/api/v1/platform-admin/companies/{first['company_id']}/entitlements",
        headers=admin_headers,
    )
    second_entitlements = client.get(
        f"/api/v1/platform-admin/companies/{second['company_id']}/entitlements",
        headers=admin_headers,
    )
    assert first_entitlements.status_code == 200, first_entitlements.text
    assert second_entitlements.status_code == 200, second_entitlements.text
    first_model = next(
        item
        for item in first_entitlements.json()["models"]
        if item["model_id"] == model["id"]
    )
    second_model = next(
        item
        for item in second_entitlements.json()["models"]
        if item["model_id"] == model["id"]
    )
    assert first_model["enabled"] is True
    assert first_model["price_per_item_cents"] == 100
    assert second_model["enabled"] is True
    assert second_model["price_per_item_cents"] == 250

    _grant_model(
        client,
        admin_headers,
        company_id=second["company_id"],
        model_id=model["id"],
        enabled=False,
        price_cents=250,
    )
    assert client.get(
        f"/api/v1/companies/{second['company_id']}/models",
        headers=_tenant_headers(second),
    ).json() == []
    assert len(
        client.get(
            f"/api/v1/companies/{first['company_id']}/models",
            headers=_tenant_headers(first),
        ).json()
    ) == 1

    disabled_entitlements = client.get(
        f"/api/v1/platform-admin/companies/{second['company_id']}/entitlements",
        headers=admin_headers,
    ).json()
    disabled_model = next(
        item
        for item in disabled_entitlements["models"]
        if item["model_id"] == model["id"]
    )
    assert disabled_model["grant_id"] == second_grant["id"]
    assert disabled_model["enabled"] is False


def test_dynamic_resource_catalog_and_company_grants_have_full_admin_readback(
    client, app
):
    first = bootstrap(client, "section5-resource-a")
    second = bootstrap(client, "section5-resource-b")
    _, admin_headers = _admin(client, "resources")
    resources = {
        kind: _resource(
            client,
            admin_headers,
            key=f"section5.{kind}",
            kind=kind,
        )
        for kind in ("feature", "agent", "external_api")
    }

    before = client.get(
        f"/api/v1/platform-admin/companies/{first['company_id']}/entitlements",
        headers=admin_headers,
    )
    assert before.status_code == 200, before.text
    before_by_key = {
        item["key"]: item for item in before.json()["resources"]
    }
    assert set(before_by_key) == {
        "section5.feature",
        "section5.agent",
        "section5.external_api",
    }
    assert all(item["grant_id"] is None for item in before_by_key.values())
    assert all(item["enabled"] is False for item in before_by_key.values())

    for kind in ("feature", "agent"):
        granted = client.put(
            (
                f"/api/v1/platform-admin/companies/{first['company_id']}"
                f"/resources/{resources[kind]['id']}"
            ),
            headers=admin_headers,
            json={
                "enabled": True,
                "config_override": {"company": "a", "kind": kind},
            },
        )
        assert granted.status_code == 200, granted.text
    external_for_second = client.put(
        (
            f"/api/v1/platform-admin/companies/{second['company_id']}"
            f"/resources/{resources['external_api']['id']}"
        ),
        headers=admin_headers,
        json={
            "enabled": True,
            "config_override": {"company": "b", "quota": 9},
        },
    )
    assert external_for_second.status_code == 200, external_for_second.text

    first_available = client.get(
        f"/api/v1/companies/{first['company_id']}/resources",
        headers=_tenant_headers(first),
    )
    second_available = client.get(
        f"/api/v1/companies/{second['company_id']}/resources",
        headers=_tenant_headers(second),
    )
    assert {item["key"] for item in first_available.json()} == {
        "section5.feature",
        "section5.agent",
    }
    assert {item["key"] for item in second_available.json()} == {
        "section5.external_api"
    }

    first_readback = client.get(
        f"/api/v1/platform-admin/companies/{first['company_id']}/entitlements",
        headers=admin_headers,
    )
    second_readback = client.get(
        f"/api/v1/platform-admin/companies/{second['company_id']}/entitlements",
        headers=admin_headers,
    )
    assert first_readback.status_code == 200, first_readback.text
    assert second_readback.status_code == 200, second_readback.text
    first_by_key = {
        item["key"]: item for item in first_readback.json()["resources"]
    }
    second_by_key = {
        item["key"]: item for item in second_readback.json()["resources"]
    }
    assert first_by_key["section5.feature"]["enabled"] is True
    assert first_by_key["section5.feature"]["config_override"] == {
        "company": "a",
        "kind": "feature",
    }
    assert first_by_key["section5.external_api"]["enabled"] is False
    assert second_by_key["section5.external_api"]["enabled"] is True
    assert second_by_key["section5.external_api"]["config_override"] == {
        "company": "b",
        "quota": 9,
    }
    assert second_by_key["section5.feature"]["enabled"] is False

    updated = client.put(
        f"/api/v1/platform-admin/resources/{resources['feature']['id']}",
        headers=admin_headers,
        json={
            "display_name": "Section 5 feature paused",
            "description": "Temporarily unavailable",
            "active": False,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["key"] == "section5.feature"
    assert updated.json()["kind"] == "feature"
    assert updated.json()["active"] is False
    assert updated.json()["display_name"] == "Section 5 feature paused"

    cannot_open_inactive = client.put(
        (
            f"/api/v1/platform-admin/companies/{second['company_id']}"
            f"/resources/{resources['feature']['id']}"
        ),
        headers=admin_headers,
        json={"enabled": True},
    )
    assert cannot_open_inactive.status_code == 409

    assert {item["key"] for item in client.get(
        f"/api/v1/companies/{first['company_id']}/resources",
        headers=_tenant_headers(first),
    ).json()} == {"section5.agent"}
    inactive_readback = client.get(
        f"/api/v1/platform-admin/companies/{first['company_id']}/entitlements",
        headers=admin_headers,
    ).json()
    inactive_feature = next(
        item
        for item in inactive_readback["resources"]
        if item["resource_id"] == resources["feature"]["id"]
    )
    assert inactive_feature["active"] is False
    assert inactive_feature["enabled"] is True
    with app.state.session_factory() as session:
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "resource.update",
                AuditLog.target_id == resources["feature"]["id"],
            )
        )
        assert audit is not None
        assert audit.before_summary["active"] is True
        assert audit.after_summary["active"] is False
        assert audit.after_summary["display_name"] == (
            "Section 5 feature paused"
        )


def test_channel_cost_ledger_is_idempotent_filterable_and_drives_real_margin(
    client, app, tenant
):
    admin_id, admin_headers = _admin(client, "channel-cost")
    model = _catalog_model(client, admin_headers, "cost-ledger")
    _grant_model(
        client,
        admin_headers,
        company_id=tenant["company_id"],
        model_id=model["id"],
        enabled=True,
        price_cents=400,
    )
    _recharge(
        client,
        admin_headers,
        company_id=tenant["company_id"],
        suffix="cost-ledger",
    )
    succeeded = _create_task(
        client,
        tenant,
        model_id=model["id"],
        suffix="cost-success",
    )
    failed = _create_task(
        client,
        tenant,
        model_id=model["id"],
        suffix="cost-failed",
    )
    succeeded_relay_job_id = "55555555-5555-4555-8555-555555555551"
    failed_relay_job_id = "55555555-5555-4555-8555-555555555552"
    with app.state.session_factory.begin() as session:
        session.get(GenerationTask, succeeded["id"]).relay_job_id = (
            succeeded_relay_job_id
        )
        session.get(GenerationTask, failed["id"]).relay_job_id = failed_relay_job_id
        WalletService.settle_success(
            session,
            company_id=tenant["company_id"],
            task_id=succeeded["id"],
            actual_cost_cents=350,
            idempotency_key="section5-settle-success",
        )
        WalletService.release_failure(
            session,
            company_id=tenant["company_id"],
            task_id=failed["id"],
            idempotency_key="section5-release-failed",
            failure_reason="provider failed",
        )

    unreconciled = client.get(
        "/api/v1/platform-admin/dashboard?page=1&page_size=100",
        headers=admin_headers,
    )
    assert unreconciled.status_code == 200, unreconciled.text
    assert unreconciled.json()["platform_recharge_cents"] == 5_000
    assert unreconciled.json()["platform_income_cents"] == 350
    assert unreconciled.json()["channel_cost_cents"] == 0
    assert unreconciled.json()["known_gross_profit_cents"] == 350
    assert unreconciled.json()["gross_profit_cents"] is None
    assert unreconciled.json()["channel_cost_status"] == "incomplete"
    assert unreconciled.json()["unreconciled_succeeded_count"] == 1
    automatic_costs = client.get(
        "/api/v1/platform-admin/channel-costs",
        headers=admin_headers,
    )
    assert automatic_costs.status_code == 200, automatic_costs.text
    assert automatic_costs.json()["total"] == 0
    assert automatic_costs.json()["total_amount_cents"] == 0

    success_cost = _cost_payload(
        suffix="success-official",
        amount_cents=80,
        channel_key="kling.official",
        channel_type="official",
        company_id=tenant["company_id"],
        task_id=succeeded["id"],
        relay_job_id=succeeded_relay_job_id,
    )
    first = client.post(
        "/api/v1/platform-admin/channel-costs",
        headers=admin_headers,
        json=success_cost,
    )
    assert first.status_code == 201, first.text
    assert first.json()["source"] == "platform_admin"
    assert first.json()["recorded_by_user_id"] == admin_id

    replay_from_relay = client.post(
        "/internal/channel-costs",
        headers={"X-Internal-Service-Token": "test-internal-token"},
        json=success_cost,
    )
    assert replay_from_relay.status_code == 409, replay_from_relay.text

    conflict = client.post(
        "/api/v1/platform-admin/channel-costs",
        headers=admin_headers,
        json={**success_cost, "amount_cents": 81},
    )
    assert conflict.status_code == 409

    charged_failure_without_task_link = _cost_payload(
        suffix="failed-real-charge",
        amount_cents=20,
        channel_key="reverse.pool-a",
        channel_type="reverse",
        company_id=tenant["company_id"],
        occurred_at="2026-08-05T02:03:04Z",
    )
    unlinked = client.post(
        "/api/v1/platform-admin/channel-costs",
        headers=admin_headers,
        json=charged_failure_without_task_link,
    )
    assert unlinked.status_code == 201, unlinked.text

    refund_adjustment = _cost_payload(
        suffix="provider-refund",
        amount_cents=-5,
        channel_key="reverse.pool-a",
        channel_type="reverse",
        company_id=tenant["company_id"],
        occurred_at="2026-08-05T02:30:00Z",
    )
    refund = client.post(
        "/api/v1/platform-admin/channel-costs",
        headers=admin_headers,
        json=refund_adjustment,
    )
    assert refund.status_code == 201, refund.text
    assert refund.json()["amount_cents"] == -5

    failed_task_link = client.post(
        "/api/v1/platform-admin/channel-costs",
        headers=admin_headers,
        json=_cost_payload(
            suffix="invalid-failed-link",
            amount_cents=20,
            channel_key="reverse.pool-a",
            channel_type="reverse",
            company_id=tenant["company_id"],
            task_id=failed["id"],
            relay_job_id=failed_relay_job_id,
        ),
    )
    assert failed_task_link.status_code == 409

    wrong_relay = client.post(
        "/api/v1/platform-admin/channel-costs",
        headers=admin_headers,
        json=_cost_payload(
            suffix="invalid-relay-link",
            amount_cents=1,
            channel_key="kling.official",
            channel_type="official",
            company_id=tenant["company_id"],
            task_id=succeeded["id"],
            relay_job_id="55555555-5555-4555-8555-555555555599",
        ),
    )
    assert wrong_relay.status_code == 409

    other_company = bootstrap(client, "section5-cost-other")
    wrong_company = client.post(
        "/api/v1/platform-admin/channel-costs",
        headers=admin_headers,
        json=_cost_payload(
            suffix="invalid-company-link",
            amount_cents=1,
            channel_key="kling.official",
            channel_type="official",
            company_id=other_company["company_id"],
            task_id=succeeded["id"],
            relay_job_id=succeeded_relay_job_id,
        ),
    )
    assert wrong_company.status_code == 409

    reconciled = client.get(
        "/api/v1/platform-admin/dashboard?page=1&page_size=100",
        headers=admin_headers,
    )
    assert reconciled.status_code == 200, reconciled.text
    dashboard = reconciled.json()
    assert dashboard["platform_recharge_cents"] == 5_000
    assert dashboard["platform_income_cents"] == 350
    assert dashboard["channel_cost_cents"] == 95
    assert dashboard["known_gross_profit_cents"] == 255
    assert dashboard["gross_profit_cents"] == 255
    assert dashboard["channel_cost_status"] == "complete"
    assert dashboard["unreconciled_succeeded_count"] == 0
    costs_by_channel = {
        (item["channel_key"], item["channel_type"]): item["amount_cents"]
        for item in dashboard["channel_costs"]
    }
    assert costs_by_channel == {
        ("kling.official", "official"): 80,
        ("reverse.pool-a", "reverse"): 15,
    }
    company = next(
        item
        for item in dashboard["companies"]
        if item["company_id"] == tenant["company_id"]
    )
    assert company["recharge_cents"] == 5_000
    assert company["consumption_cents"] == 350
    assert company["task_count"] == 2
    assert company["succeeded_count"] == 1
    assert company["failed_count"] == 1

    all_costs = client.get(
        "/api/v1/platform-admin/channel-costs?page=1&page_size=1",
        headers=admin_headers,
    )
    assert all_costs.status_code == 200, all_costs.text
    assert all_costs.json()["total"] == 3
    assert all_costs.json()["total_amount_cents"] == 95
    assert len(all_costs.json()["items"]) == 1

    official = client.get(
        "/api/v1/platform-admin/channel-costs",
        headers=admin_headers,
        params={
            "company_id": tenant["company_id"],
            "channel_key": "kling.official",
            "channel_type": "official",
            "start_time": "2026-08-05T00:00:00Z",
            "end_time": "2026-08-05T01:30:00Z",
        },
    )
    assert official.status_code == 200, official.text
    assert official.json()["total"] == 1
    assert official.json()["total_amount_cents"] == 80
    assert official.json()["items"][0]["task_id"] == succeeded["id"]
    assert official.json()["items"][0]["relay_job_id"] == succeeded_relay_job_id

    reverse = client.get(
        "/api/v1/platform-admin/channel-costs",
        headers=admin_headers,
        params={"channel_type": "reverse"},
    )
    assert reverse.status_code == 200, reverse.text
    assert reverse.json()["total"] == 2
    assert reverse.json()["total_amount_cents"] == 15
    assert {
        item["external_reference"] for item in reverse.json()["items"]
    } == {
        "section5-external-failed-real-charge",
        "section5-external-provider-refund",
    }

    future = client.get(
        "/api/v1/platform-admin/channel-costs",
        headers=admin_headers,
        params={"start_time": "2100-01-01T00:00:00Z"},
    )
    assert future.status_code == 200, future.text
    assert future.json()["total"] == 0
    assert future.json()["total_amount_cents"] == 0

def test_zero_channel_cost_explicitly_reconciles_a_successful_task(
    client, app, tenant
):
    _, admin_headers = _admin(client, "zero-cost")
    model = _catalog_model(client, admin_headers, "zero-cost")
    _grant_model(
        client,
        admin_headers,
        company_id=tenant["company_id"],
        model_id=model["id"],
        enabled=True,
        price_cents=100,
    )
    _recharge(
        client,
        admin_headers,
        company_id=tenant["company_id"],
        suffix="zero-cost",
    )
    task = _create_task(
        client,
        tenant,
        model_id=model["id"],
        suffix="zero-cost",
    )
    relay_job_id = "66666666-6666-4666-8666-666666666666"
    with app.state.session_factory.begin() as session:
        session.get(GenerationTask, task["id"]).relay_job_id = relay_job_id
        WalletService.settle_success(
            session,
            company_id=tenant["company_id"],
            task_id=task["id"],
            actual_cost_cents=100,
            idempotency_key="section5-zero-settle",
        )

    posted = client.post(
        "/api/v1/platform-admin/channel-costs",
        headers=admin_headers,
        json=_cost_payload(
            suffix="zero-explicit",
            amount_cents=0,
            channel_key="wan.official",
            channel_type="official",
            company_id=tenant["company_id"],
            task_id=task["id"],
            relay_job_id=relay_job_id,
        ),
    )
    assert posted.status_code == 201, posted.text
    assert posted.json()["amount_cents"] == 0

    dashboard = client.get(
        "/api/v1/platform-admin/dashboard",
        headers=admin_headers,
    ).json()
    assert dashboard["channel_cost_cents"] == 0
    assert dashboard["known_gross_profit_cents"] == 100
    assert dashboard["gross_profit_cents"] == 100
    assert dashboard["channel_cost_status"] == "complete"
    assert dashboard["unreconciled_succeeded_count"] == 0


def test_channel_cost_first_writer_source_survives_cross_entrypoint_replay(
    client,
):
    admin_id, admin_headers = _admin(client, "cost-source")
    relay_first_payload = _cost_payload(
        suffix="relay-first",
        amount_cents=0,
        channel_key="wan.official",
        channel_type="official",
        evidence_source="provider_reported",
    )
    assert client.post(
        "/internal/channel-costs",
        json=relay_first_payload,
    ).status_code == 401
    assert client.post(
        "/internal/channel-costs",
        headers={"X-Internal-Service-Token": "wrong-service-token"},
        json=relay_first_payload,
    ).status_code == 401
    assert client.post(
        "/internal/channel-costs",
        headers={"X-Platform-Admin-User-ID": admin_id},
        json=relay_first_payload,
    ).status_code == 401
    relay_first = client.post(
        "/internal/channel-costs",
        headers={"X-Internal-Service-Token": "test-internal-token"},
        json=relay_first_payload,
    )
    assert relay_first.status_code == 201, relay_first.text
    assert relay_first.json()["source"] == "relay"
    assert relay_first.json()["recorded_by_user_id"] is None

    admin_replay = client.post(
        "/api/v1/platform-admin/channel-costs",
        headers=admin_headers,
        json=relay_first_payload,
    )
    assert admin_replay.status_code == 409, admin_replay.text

    admin_first_payload = _cost_payload(
        suffix="admin-first",
        amount_cents=9,
        channel_key="marketplace.api",
        channel_type="third_party_api",
    )
    admin_first = client.post(
        "/api/v1/platform-admin/channel-costs",
        headers=admin_headers,
        json=admin_first_payload,
    )
    assert admin_first.status_code == 201, admin_first.text
    assert admin_first.json()["source"] == "platform_admin"
    assert admin_first.json()["recorded_by_user_id"] == admin_id

    relay_replay = client.post(
        "/internal/channel-costs",
        headers={"X-Internal-Service-Token": "test-internal-token"},
        json=admin_first_payload,
    )
    assert relay_replay.status_code == 409, relay_replay.text


def test_channel_cost_entries_are_immutable_at_the_orm_boundary(
    client, app
):
    _, admin_headers = _admin(client, "cost-immutable")
    response = client.post(
        "/api/v1/platform-admin/channel-costs",
        headers=admin_headers,
        json=_cost_payload(
            suffix="immutable",
            amount_cents=12,
            channel_key="third-party.marketplace",
            channel_type="third_party_api",
        ),
    )
    assert response.status_code == 201, response.text
    entry_id = response.json()["id"]

    from platform_api import models

    entry_type = getattr(models, "ChannelCostEntry", None)
    assert entry_type is not None, "ChannelCostEntry ORM model is required"
    with pytest.raises(RuntimeError, match="channel cost entries are immutable"):
        with app.state.session_factory.begin() as session:
            entry = session.get(entry_type, entry_id)
            assert entry is not None
            entry.amount_cents = 13

    with pytest.raises(RuntimeError, match="channel cost entries are immutable"):
        with app.state.session_factory.begin() as session:
            entry = session.get(entry_type, entry_id)
            assert entry is not None
            session.delete(entry)

    with app.state.session_factory() as session:
        preserved = session.get(entry_type, entry_id)
        assert preserved is not None
        assert preserved.amount_cents == 12
        assert preserved.channel_key == "third-party.marketplace"
        channel_type = getattr(preserved.channel_type, "value", preserved.channel_type)
        assert channel_type == "third_party_api"
        occurred_at = preserved.occurred_at
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        assert occurred_at.astimezone(timezone.utc) == datetime(
            2026, 8, 5, 1, 2, 3, tzinfo=timezone.utc
        )
