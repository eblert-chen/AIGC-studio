from __future__ import annotations

import pytest
from sqlalchemy import func, select

from platform_api.models import AuditLog, ChannelCostEntry, GenerationTask

from .test_platform_admin import bootstrap_admin
from .test_wallet_and_tasks import seed_model


def test_dynamic_resource_catalog_and_complete_company_entitlements(
    app, client, tenant, tenant_headers
):
    _, admin_headers = bootstrap_admin(client, "entitlements")
    granted_model_id = seed_model(app, tenant["company_id"])
    disabled_grant = client.put(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/model-grants",
        headers=admin_headers,
        json={
            "model_id": granted_model_id,
            "enabled": False,
            "price_per_second_cents": 80,
            "config_override": {"durations": [5]},
        },
    )
    assert disabled_grant.status_code == 200, disabled_grant.text
    ungranted_model = client.post(
        "/api/v1/bootstrap/models",
        json={
            "slug": "ungranted-catalog-model",
            "display_name": "Ungrant Model",
            "provider_key": "test-provider",
            "billing_mode": "per_item",
        },
    )
    assert ungranted_model.status_code == 201, ungranted_model.text

    first_resource = client.post(
        "/api/v1/platform-admin/resources",
        headers=admin_headers,
        json={
            "key": "agent.script.writer",
            "kind": "agent",
            "display_name": "Script Writer",
            "description": "Initial",
        },
    )
    assert first_resource.status_code == 201, first_resource.text
    resource_id = first_resource.json()["id"]
    second_resource = client.post(
        "/api/v1/platform-admin/resources",
        headers=admin_headers,
        json={
            "key": "external-api.caption",
            "kind": "external_api",
            "display_name": "Caption API",
        },
    )
    assert second_resource.status_code == 201, second_resource.text

    enabled = client.put(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/resources/{resource_id}",
        headers=admin_headers,
        json={"enabled": True, "config_override": {"daily_limit": 9}},
    )
    assert enabled.status_code == 200, enabled.text
    updated = client.put(
        f"/api/v1/platform-admin/resources/{resource_id}",
        headers={**admin_headers, "X-Request-ID": "req-resource-update"},
        json={
            "display_name": "  Script Writer Pro  ",
            "description": "  Disabled for maintenance  ",
            "active": False,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["display_name"] == "Script Writer Pro"
    assert updated.json()["description"] == "Disabled for maintenance"

    available = client.get(
        f"/api/v1/companies/{tenant['company_id']}/resources",
        headers=tenant_headers,
    )
    assert available.status_code == 200
    assert available.json() == []
    cannot_enable_inactive = client.put(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/resources/{resource_id}",
        headers=admin_headers,
        json={"enabled": True},
    )
    assert cannot_enable_inactive.status_code == 409

    entitlements = client.get(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/entitlements",
        headers=admin_headers,
    )
    assert entitlements.status_code == 200, entitlements.text
    body = entitlements.json()
    assert body["company_id"] == tenant["company_id"]
    models = {item["model_id"]: item for item in body["models"]}
    assert models[granted_model_id]["grant_id"] == disabled_grant.json()["id"]
    assert models[granted_model_id]["enabled"] is False
    assert models[granted_model_id]["price_per_second_cents"] == 80
    assert models[ungranted_model.json()["id"]] == {
        "model_id": ungranted_model.json()["id"],
        "slug": "ungranted-catalog-model",
        "display_name": "Ungrant Model",
        "status": "published",
        "billing_mode": "per_item",
        "grant_id": None,
        "enabled": False,
        "price_per_second_cents": None,
        "price_per_item_cents": None,
        "config_override": {},
        "call_quota": None,
        "concurrency_limit": None,
        "effective_at": None,
        "expires_at": None,
    }
    resources = {item["resource_id"]: item for item in body["resources"]}
    assert resources[resource_id]["active"] is False
    assert resources[resource_id]["enabled"] is True
    assert resources[resource_id]["config_override"] == {"daily_limit": 9}
    assert resources[second_resource.json()["id"]]["grant_id"] is None
    assert resources[second_resource.json()["id"]]["enabled"] is False

    disabled = client.put(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/resources/{resource_id}",
        headers=admin_headers,
        json={"enabled": False},
    )
    assert disabled.status_code == 200, disabled.text
    blank_name = client.put(
        f"/api/v1/platform-admin/resources/{resource_id}",
        headers=admin_headers,
        json={"display_name": "   ", "description": "x", "active": False},
    )
    assert blank_name.status_code == 422

    with app.state.session_factory() as session:
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "resource.update",
                AuditLog.target_id == resource_id,
            )
        )
        assert audit is not None
        assert audit.request_id == "req-resource-update"


def test_channel_cost_ledger_is_signed_idempotent_filtered_and_immutable(
    app, client, tenant, tenant_headers, internal_headers
):
    admin_id, admin_headers = bootstrap_admin(client, "channel-cost")
    model_id = seed_model(app, tenant["company_id"])
    recharge = client.post(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/recharge",
        headers=admin_headers,
        json={"amount_cents": 2000, "idempotency_key": "cost-recharge-0001"},
    )
    assert recharge.status_code == 200, recharge.text
    task = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "channel-cost-task-0001",
            "request_payload": {"prompt": "test", "duration_seconds": 5},
        },
    ).json()
    relay_job_id = "88888888-8888-8888-8888-888888888888"
    with app.state.session_factory.begin() as session:
        session.get(GenerationTask, task["id"]).relay_job_id = relay_job_id

    queued_cost = client.post(
        "/internal/channel-costs",
        headers=internal_headers,
        json={
            "amount_cents": 10,
            "idempotency_key": "queued-cost-rejected",
            "channel_key": "official.vendor",
            "channel_type": "official",
            "occurred_at": "2026-08-05T09:00:00Z",
            "external_reference": "queued-is-not-billable",
            "task_id": task["id"],
        },
    )
    assert queued_cost.status_code == 409

    succeeded = client.post(
        "/internal/relay/status",
        headers=internal_headers,
        json={
            "company_id": tenant["company_id"],
            "task_id": task["id"],
            "relay_job_id": relay_job_id,
            "status": "succeeded",
            "outputs": [
                {
                    "asset_id": "77777777-7777-4777-8777-777777777777",
                    "object_key": f"outputs/{relay_job_id}/cost-output",
                    "media_type": "video",
                    "content_type": "video/mp4",
                    "size_bytes": 10,
                    "sha256": "c" * 64,
                }
            ],
        },
    )
    assert succeeded.status_code == 200, succeeded.text

    task_cost_body = {
        "amount_cents": 125,
        "idempotency_key": "relay-task-cost-0001",
        "channel_key": "official.vendor",
        "channel_type": "official",
        "occurred_at": "2026-08-05T10:00:00+08:00",
        "external_reference": "provider-charge-1",
        "evidence_source": "provider_reported",
        "task_id": task["id"],
        "note": "settled upstream invoice",
    }
    first = client.post(
        "/internal/channel-costs", headers=internal_headers, json=task_cost_body
    )
    assert first.status_code == 201, first.text
    assert first.json()["source"] == "relay"
    assert first.json()["recorded_by_user_id"] is None
    assert first.json()["company_id"] == tenant["company_id"]
    assert first.json()["relay_job_id"] == relay_job_id

    cross_entry_replay = client.post(
        "/api/v1/platform-admin/channel-costs",
        headers=admin_headers,
        json=task_cost_body,
    )
    assert cross_entry_replay.status_code == 409, cross_entry_replay.text

    changed_payload = client.post(
        "/api/v1/platform-admin/channel-costs",
        headers=admin_headers,
        json={**task_cost_body, "amount_cents": 126},
    )
    assert changed_payload.status_code == 409
    mismatched_relay = client.post(
        "/internal/channel-costs",
        headers=internal_headers,
        json={
            **task_cost_body,
            "idempotency_key": "relay-mismatch-cost-0001",
            "relay_job_id": "77777777-7777-7777-7777-777777777777",
        },
    )
    assert mismatched_relay.status_code == 409

    adjustment = client.post(
        "/api/v1/platform-admin/channel-costs",
        headers={**admin_headers, "X-Request-ID": "req-cost-adjustment"},
        json={
            "amount_cents": -25,
            "idempotency_key": "admin-cost-adjustment-0001",
            "channel_key": "official.vendor",
            "channel_type": "official",
            "occurred_at": "2026-08-05T11:00:00Z",
            "external_reference": "provider-refund-1",
            "company_id": tenant["company_id"],
            "note": "provider refund",
        },
    )
    assert adjustment.status_code == 201, adjustment.text
    assert adjustment.json()["source"] == "platform_admin"
    assert adjustment.json()["recorded_by_user_id"] == admin_id
    zero_cost = client.post(
        "/api/v1/platform-admin/channel-costs",
        headers=admin_headers,
        json={
            "amount_cents": 0,
            "idempotency_key": "admin-zero-cost-entry-0001",
            "channel_key": "reverse.free-pool",
            "channel_type": "reverse",
            "occurred_at": "2026-08-05T12:00:00Z",
            "external_reference": "zero-cost-proof-1",
        },
    )
    assert zero_cost.status_code == 201, zero_cost.text

    page = client.get(
        "/api/v1/platform-admin/channel-costs"
        f"?company_id={tenant['company_id']}&channel_type=official",
        headers=admin_headers,
    )
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 2
    assert page.json()["total_amount_cents"] == 100
    assert {item["id"] for item in page.json()["items"]} == {
        first.json()["id"],
        adjustment.json()["id"],
    }

    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(ChannelCostEntry.id))) == 3
        entry = session.get(ChannelCostEntry, first.json()["id"])
        entry.amount_cents = 999
        with pytest.raises(RuntimeError, match="immutable"):
            session.flush()
        session.rollback()
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "channel_cost.create",
                AuditLog.target_id == adjustment.json()["id"],
            )
        )
        assert audit is not None
        assert audit.request_id == "req-cost-adjustment"


def test_channel_cost_endpoints_require_the_correct_admin_boundary(
    client, tenant, tenant_headers
):
    _, admin_headers = bootstrap_admin(client, "cost-boundary")
    body = {
        "amount_cents": 1,
        "idempotency_key": "boundary-cost-0001",
        "channel_key": "third-party.demo",
        "channel_type": "third_party_api",
        "occurred_at": "2026-08-05T12:00:00Z",
        "external_reference": "boundary-proof",
    }
    assert client.post("/internal/channel-costs", json=body).status_code == 401
    assert (
        client.post(
            "/api/v1/platform-admin/channel-costs",
            headers={"X-Platform-Admin-User-ID": tenant["user_id"]},
            json=body,
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/v1/platform-admin/channel-costs", headers=admin_headers
        ).status_code
        == 200
    )
    missing_company = client.get(
        "/api/v1/platform-admin/companies/not-found/entitlements",
        headers=admin_headers,
    )
    assert missing_company.status_code == 404
