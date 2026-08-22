from __future__ import annotations

import pytest
from sqlalchemy import select

from platform_api.models import (
    AuditLog,
    CompanyMembership,
    GenerationTask,
    MembershipRole,
    Permission,
    Role,
    RolePermission,
    User,
    WalletAccount,
)

from .conftest import bootstrap
from .test_wallet_and_tasks import seed_model


def bootstrap_admin(client, suffix: str = "main"):
    response = client.post(
        "/api/v1/bootstrap/platform-admin",
        json={
            "email": f"admin-{suffix}@example.com",
            "display_name": f"Admin {suffix}",
        },
    )
    assert response.status_code == 201, response.text
    user_id = response.json()["user_id"]
    return user_id, {"X-Platform-Admin-User-ID": user_id}


def test_development_platform_admin_headers_require_explicit_opt_in(app, client):
    _, admin_headers = bootstrap_admin(client, "header-auth-disabled")
    app.state.settings.development_header_auth_enabled = False
    try:
        legacy_response = client.get(
            "/api/v1/platform-admin/companies",
            headers=admin_headers,
        )
        granular_response = client.get(
            "/api/v1/platform-admin/access/permissions",
            headers=admin_headers,
        )
    finally:
        app.state.settings.development_header_auth_enabled = True

    assert legacy_response.status_code == 401
    assert granular_response.status_code == 401


def test_company_owner_cannot_use_platform_admin_and_company_list_is_paginated(
    app, client
):
    owner = bootstrap(client, "owner-not-admin")
    denied = client.get(
        "/api/v1/platform-admin/companies",
        headers={"X-Platform-Admin-User-ID": owner["user_id"]},
    )
    assert denied.status_code == 403

    _, admin_headers = bootstrap_admin(client, "pagination")
    last_company_id = None
    for index in range(3):
        created = client.post(
            "/api/v1/platform-admin/companies",
            headers=admin_headers,
            json={
                "name": f"Managed {index}",
                "owner_email": f"managed-owner-{index}@example.com",
                "owner_display_name": f"Owner {index}",
            },
        )
        assert created.status_code == 201, created.text
        last_company_id = created.json()["id"]

    suspended = client.patch(
        f"/api/v1/platform-admin/companies/{last_company_id}/status",
        headers=admin_headers,
        json={"status": "suspended"},
    )
    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"
    reenabled = client.patch(
        f"/api/v1/platform-admin/companies/{last_company_id}/status",
        headers=admin_headers,
        json={"status": "active"},
    )
    assert reenabled.status_code == 200
    assert reenabled.json()["status"] == "active"

    page = client.get(
        "/api/v1/platform-admin/companies?page=1&page_size=2",
        headers=admin_headers,
    )
    assert page.status_code == 200
    assert page.json()["total"] == 4
    assert len(page.json()["items"]) == 2
    second_page = client.get(
        "/api/v1/platform-admin/companies?page=2&page_size=2",
        headers=admin_headers,
    )
    assert len(second_page.json()["items"]) == 2


def test_admin_company_creation_builds_owner_membership_wallet_and_audit(
    app, client
):
    admin_id, admin_headers = bootstrap_admin(client, "company-create")
    response = client.post(
        "/api/v1/platform-admin/companies",
        headers={**admin_headers, "X-Request-ID": "req-company-create"},
        json={
            "name": "Created By Admin",
            "owner_email": "created-owner@example.com",
            "owner_display_name": "Created Owner",
        },
    )
    assert response.status_code == 201
    company_id = response.json()["id"]
    with app.state.session_factory() as session:
        owner = session.scalar(
            select(User).where(User.email == "created-owner@example.com")
        )
        membership = session.scalar(
            select(CompanyMembership).where(
                CompanyMembership.company_id == company_id,
                CompanyMembership.user_id == owner.id,
            )
        )
        wallet = session.get(WalletAccount, company_id)
        roles = list(
            session.scalars(select(Role).where(Role.company_id == company_id)).all()
        )
        owner_role = next(role for role in roles if role.system_key == "owner")
        owner_assignment = session.get(
            MembershipRole,
            {"membership_id": membership.id, "role_id": owner_role.id},
        )
        owner_permission_count = len(
            session.scalars(
                select(RolePermission.permission_code).where(
                    RolePermission.role_id == owner_role.id
                )
            ).all()
        )
        permission_count = len(session.scalars(select(Permission.code)).all())
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "company.create",
                AuditLog.target_id == company_id,
            )
        )
        assert membership is not None
        assert wallet.available_cents == 0
        assert {role.system_key for role in roles} == {
            "owner",
            "team_lead",
            "operator",
        }
        assert owner_assignment is not None
        assert owner_permission_count == permission_count
        assert audit.actor_user_id == admin_id
        assert audit.request_id == "req-company-create"
        assert audit.after_summary["owner_user_id"] == owner.id


@pytest.mark.parametrize("kind", ["feature", "agent", "external_api"])
def test_new_resources_default_denied_then_follow_company_switch(
    client, tenant, tenant_headers, kind
):
    _, admin_headers = bootstrap_admin(client, f"resource-{kind}")
    created = client.post(
        "/api/v1/platform-admin/resources",
        headers=admin_headers,
        json={
            "key": f"{kind}.demo",
            "kind": kind,
            "display_name": f"{kind} demo",
        },
    )
    assert created.status_code == 201
    resource_id = created.json()["id"]

    default_denied = client.get(
        f"/api/v1/companies/{tenant['company_id']}/resources",
        headers=tenant_headers,
    )
    assert default_denied.status_code == 200
    assert default_denied.json() == []

    enabled = client.put(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/resources/{resource_id}",
        headers=admin_headers,
        json={"enabled": True, "config_override": {"limit": 3}},
    )
    assert enabled.status_code == 200
    available = client.get(
        f"/api/v1/companies/{tenant['company_id']}/resources",
        headers=tenant_headers,
    ).json()
    assert available[0]["kind"] == kind
    assert available[0]["config_override"] == {"limit": 3}

    client.put(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/resources/{resource_id}",
        headers=admin_headers,
        json={"enabled": False},
    )
    assert (
        client.get(
            f"/api/v1/companies/{tenant['company_id']}/resources",
            headers=tenant_headers,
        ).json()
        == []
    )


def test_admin_recharge_model_pricing_and_audit_are_recorded(
    app, client, tenant
):
    admin_id, admin_headers = bootstrap_admin(client, "billing-model")
    model = client.post(
        "/api/v1/bootstrap/models",
        json={
            "slug": "admin-priced-model",
            "display_name": "Admin Priced",
            "provider_key": "development",
            "capabilities": [
                {"key": "text-to-video", "config": {"durations": [5]}}
            ],
        },
    ).json()
    grant = client.put(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/model-grants",
        headers={**admin_headers, "X-Request-ID": "req-model-grant"},
        json={
            "model_id": model["id"],
            "enabled": True,
            "price_per_second_cents": 91,
        },
    )
    assert grant.status_code == 200
    recharge = client.post(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/recharge",
        headers={**admin_headers, "X-Request-ID": "req-admin-recharge"},
        json={
            "amount_cents": 5000,
            "idempotency_key": "admin-recharge-0001",
            "note": "platform admin",
        },
    )
    assert recharge.status_code == 200

    logs = client.get(
        "/api/v1/platform-admin/audit-logs?page=1&page_size=100",
        headers=admin_headers,
    ).json()["items"]
    actions = {entry["action"]: entry for entry in logs}
    assert actions["company.model_grant.upsert"]["request_id"] == "req-model-grant"
    assert actions["company.wallet.recharge"]["request_id"] == "req-admin-recharge"
    assert actions["company.wallet.recharge"]["actor_user_id"] == admin_id

    with app.state.session_factory() as session:
        recharge_log = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "company.wallet.recharge"
            )
        )
        recharge_log.action = "tampered"
        with pytest.raises(RuntimeError, match="immutable"):
            session.flush()
        session.rollback()


def test_dashboard_reports_income_real_channel_cost_and_reconciliation(
    app, client, tenant, tenant_headers, internal_headers
):
    _, admin_headers = bootstrap_admin(client, "dashboard")
    model_id = seed_model(app, tenant["company_id"])
    client.post(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/recharge",
        headers=admin_headers,
        json={
            "amount_cents": 1000,
            "idempotency_key": "dashboard-recharge",
        },
    )
    first = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "dashboard-task-success",
            "request_payload": {"prompt": "success", "duration_seconds": 5},
        },
    ).json()
    second = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "dashboard-task-failed",
            "request_payload": {"prompt": "failed", "duration_seconds": 5},
        },
    ).json()
    with app.state.session_factory.begin() as session:
        session.get(GenerationTask, first["id"]).relay_job_id = (
            "99999999-9999-9999-9999-999999999991"
        )
        session.get(GenerationTask, second["id"]).relay_job_id = (
            "99999999-9999-9999-9999-999999999992"
        )
    client.post(
        "/internal/relay/status",
        headers=internal_headers,
        json={
            "company_id": tenant["company_id"],
            "task_id": first["id"],
            "relay_job_id": "99999999-9999-9999-9999-999999999991",
            "status": "succeeded",
            "outputs": [
                {
                    "asset_id": "66666666-6666-4666-8666-666666666666",
                    "object_key": (
                        "outputs/99999999-9999-9999-9999-999999999991/"
                        "dashboard-output"
                    ),
                    "media_type": "video",
                    "content_type": "video/mp4",
                    "size_bytes": 1234,
                    "sha256": "b" * 64,
                }
            ],
        },
    )
    client.post(
        "/internal/relay/status",
        headers=internal_headers,
        json={
            "company_id": tenant["company_id"],
            "task_id": second["id"],
            "relay_job_id": "99999999-9999-9999-9999-999999999992",
            "status": "failed",
        },
    )
    cost = client.post(
        "/internal/channel-costs",
        headers=internal_headers,
        json={
            "amount_cents": 125,
            "idempotency_key": "dashboard-channel-cost-0001",
            "channel_key": "official.demo",
            "channel_type": "official",
            "occurred_at": "2026-08-05T10:00:00+08:00",
            "external_reference": "provider-invoice-line-1",
            "evidence_source": "provider_reported",
            "task_id": first["id"],
        },
    )
    assert cost.status_code == 201, cost.text
    assert cost.json()["company_id"] == tenant["company_id"]
    assert cost.json()["relay_job_id"] == (
        "99999999-9999-9999-9999-999999999991"
    )
    assert cost.json()["source"] == "relay"

    dashboard = client.get(
        "/api/v1/platform-admin/dashboard?page=1&page_size=100",
        headers=admin_headers,
    )
    assert dashboard.status_code == 200, dashboard.text
    data = dashboard.json()
    assert data["platform_income_cents"] == 400
    assert data["platform_recharge_cents"] == 1000
    assert data["channel_cost_cents"] == 125
    assert data["known_gross_profit_cents"] == 275
    assert data["gross_profit_cents"] == 275
    assert data["channel_cost_status"] == "complete"
    assert data["unreconciled_succeeded_count"] == 0
    assert data["channel_costs"] == [
        {
            "channel_key": "official.demo",
            "channel_type": "official",
            "amount_cents": 125,
        }
    ]
    assert data["active_company_count"] == 1
    assert data["total_task_count"] == 2
    assert data["succeeded_task_count"] == 1
    assert data["failed_task_count"] == 1
    company = next(
        row
        for row in data["companies"]
        if row["company_id"] == tenant["company_id"]
    )
    assert company["recharge_cents"] == 1000
    assert company["consumption_cents"] == 400
    assert company["available_cents"] == 600
    assert company["reserved_cents"] == 0
    assert company["task_count"] == 2
    assert company["succeeded_count"] == 1
    assert company["failed_count"] == 1
