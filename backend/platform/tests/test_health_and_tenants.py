from __future__ import annotations

from .conftest import bootstrap


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "customer-platform",
    }
    assert client.get("/health/live").json()["status"] == "ok"
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["database"] == "ok"


def test_bootstrap_rejects_an_invalid_token(client):
    response = client.post(
        "/api/v1/bootstrap",
        headers={"X-Bootstrap-Token": "wrong-bootstrap-secret-2026-08-14-aa"},
        json={
            "company_name": "Must Not Exist",
            "owner_email": "invalid-bootstrap@example.com",
            "owner_display_name": "Invalid Bootstrap",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Bootstrap authentication failed"


def test_development_tenant_headers_require_explicit_opt_in(app, client):
    tenant = bootstrap(client, "header-auth-disabled")
    app.state.settings.development_header_auth_enabled = False
    try:
        response = client.get(
            f"/api/v1/companies/{tenant['company_id']}/me",
            headers={
                "X-Company-ID": tenant["company_id"],
                "X-User-ID": tenant["user_id"],
            },
        )
    finally:
        app.state.settings.development_header_auth_enabled = True

    assert response.status_code == 401
    assert response.json()["detail"] == (
        "Development header authentication is disabled"
    )


def test_company_header_and_membership_are_both_enforced(client):
    first = bootstrap(client, "first")
    second = bootstrap(client, "second")
    path = f"/api/v1/companies/{first['company_id']}/members"

    mismatched_company = client.get(
        path,
        headers={
            "X-Company-ID": second["company_id"],
            "X-User-ID": first["user_id"],
        },
    )
    assert mismatched_company.status_code == 403

    foreign_user = client.get(
        path,
        headers={
            "X-Company-ID": first["company_id"],
            "X-User-ID": second["user_id"],
        },
    )
    assert foreign_user.status_code == 403

    valid = client.get(
        path,
        headers={
            "X-Company-ID": first["company_id"],
            "X-User-ID": first["user_id"],
        },
    )
    assert valid.status_code == 200
    assert [member["user_id"] for member in valid.json()] == [first["user_id"]]


def test_role_permission_and_personal_deny_override(client, tenant, tenant_headers):
    company_id = tenant["company_id"]
    member_response = client.post(
        f"/api/v1/companies/{company_id}/members",
        headers=tenant_headers,
        json={"email": "worker@example.com", "display_name": "Worker"},
    )
    assert member_response.status_code == 201
    member = member_response.json()
    worker_headers = {
        "X-Company-ID": company_id,
        "X-User-ID": member["user_id"],
    }

    denied_before_role = client.get(
        f"/api/v1/companies/{company_id}/wallet", headers=worker_headers
    )
    assert denied_before_role.status_code == 403

    role_response = client.post(
        f"/api/v1/companies/{company_id}/roles",
        headers=tenant_headers,
        json={"name": "账务观察员", "permission_codes": ["billing.read"]},
    )
    assert role_response.status_code == 201
    role_id = role_response.json()["id"]
    assigned = client.post(
        f"/api/v1/companies/{company_id}/roles/{role_id}/assign",
        headers=tenant_headers,
        json={"membership_id": member["membership_id"]},
    )
    assert assigned.status_code == 204
    assert (
        client.get(f"/api/v1/companies/{company_id}/wallet", headers=worker_headers).status_code
        == 200
    )

    override = client.put(
        f"/api/v1/companies/{company_id}/members/{member['membership_id']}/permission",
        headers=tenant_headers,
        json={"permission_code": "billing.read", "effect": "deny"},
    )
    assert override.status_code == 200
    assert (
        client.get(f"/api/v1/companies/{company_id}/wallet", headers=worker_headers).status_code
        == 403
    )
