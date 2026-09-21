from __future__ import annotations

import inspect
import re

from fastapi.routing import APIRoute
from sqlalchemy import func, select

from platform_api.dependencies import get_db, get_tenant_context
from platform_api.models import AuditLog, CompanyMembership, User
from platform_api.routers import company_access
from platform_api.services.audit import AuditService
from platform_api.services.errors import DomainError

from .conftest import bootstrap


# Keep public route names and methods stable so moving their implementation
# cannot silently change generated clients or bypass the existing access gate.
_ROUTES = [
    ("company_me", "GET", "/me", 200, None),
    ("list_permission_catalog", "GET", "/permissions", 200, "users.read"),
    ("list_members", "GET", "/members", 200, "users.read"),
    ("member_permission_detail", "GET", "/members/{membership_id}/permissions", 200, "users.read"),
    ("create_member", "POST", "/members", 201, "users.manage"),
    ("set_member_status", "PATCH", "/members/{membership_id}/status", 200, "users.manage"),
    ("list_roles", "GET", "/roles", 200, "users.read"),
    ("create_role", "POST", "/roles", 201, "users.manage"),
    ("update_role", "PUT", "/roles/{role_id}", 200, "users.manage"),
    ("delete_role", "DELETE", "/roles/{role_id}", 204, "users.manage"),
    ("assign_role", "POST", "/roles/{role_id}/assign", 204, "users.manage"),
    ("unassign_role", "DELETE", "/roles/{role_id}/assignments/{membership_id}", 204, "users.manage"),
    ("replace_member_roles", "PUT", "/members/{membership_id}/roles", 200, "users.manage"),
    ("replace_member_access", "PUT", "/members/{membership_id}/access", 200, "users.manage"),
    ("replace_permission_overrides", "PUT", "/members/{membership_id}/permissions", 200, "users.manage"),
    ("set_permission_override", "PUT", "/members/{membership_id}/permission", 200, "users.manage"),
    ("clear_permission_override", "DELETE", "/members/{membership_id}/permission/{permission_code}", 204, "users.manage"),
]
_PREFIX = "/api/v1/companies/{company_id}"


def _url(suffix, tenant, *, membership_id=None):
    return (_PREFIX + suffix).format(
        company_id=tenant["company_id"],
        membership_id=membership_id or tenant["membership_id"],
        role_id="00000000-0000-4000-8000-000000000001",
        permission_code="tasks.read",
    )


def test_company_access_routes_preserve_registration_and_dependency_contract(app):
    expected_keys = {(_PREFIX + suffix, method) for _, method, suffix, _, _ in _ROUTES}
    registered = [
        route for route in app.routes if isinstance(route, APIRoute)
        and any((route.path, method) in expected_keys for method in route.methods)
    ]
    assert len(registered) == len(_ROUTES)
    assert [route.name for route in registered] == [item[0] for item in _ROUTES]
    for route, (name, method, suffix, status, permission) in zip(registered, _ROUTES, strict=True):
        assert route.path == _PREFIX + suffix
        assert route.methods == {method}
        assert (route.status_code or 200) == status
        assert route.endpoint is getattr(company_access, name)
        assert route.tags == []
        expected_id = re.sub(r"\W", "_", name + route.path) + "_" + method.lower()
        assert route.unique_id == expected_id
        db_dependencies = [item for item in route.dependant.dependencies if item.call is get_db]
        assert len(db_dependencies) == 1
        assert db_dependencies[0].scope == "function"
        access_dependencies = [item for item in route.dependant.dependencies if item.call is not get_db]
        assert len(access_dependencies) == 1
        if permission is None:
            assert access_dependencies[0].call is get_tenant_context
        else:
            assert inspect.getclosurevars(access_dependencies[0].call).nonlocals == {
                "permission_code": permission,
            }


def test_every_company_access_route_preserves_the_tenant_fence(client, app, tenant_headers):
    other = bootstrap(client, "company-access-router-other")
    with app.state.session_factory() as session:
        before_audits = session.scalar(select(func.count()).select_from(AuditLog))
        before_members = session.scalar(select(func.count()).select_from(CompanyMembership))
    for name, method, suffix, _, _ in _ROUTES:
        response = client.request(method, _url(suffix, other), headers=tenant_headers, json={})
        assert response.status_code == 403, (name, response.text)
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AuditLog)) == before_audits
        assert session.scalar(select(func.count()).select_from(CompanyMembership)) == before_members


def test_operator_cannot_gain_company_access_management_from_router_extraction(
    client, app, tenant, tenant_headers,
):
    member_response = client.post(
        _url("/members", tenant), headers=tenant_headers,
        json={"email": "router-operator@example.com", "display_name": "Operator"},
    )
    assert member_response.status_code == 201, member_response.text
    member = member_response.json()
    headers = {"X-Company-ID": tenant["company_id"], "X-User-ID": member["user_id"]}
    with app.state.session_factory() as session:
        before_audits = session.scalar(select(func.count()).select_from(AuditLog))
    for name, method, suffix, _, permission in _ROUTES:
        response = client.request(
            method, _url(suffix, tenant, membership_id=member["membership_id"]),
            headers=headers, json={},
        )
        assert response.status_code == (200 if permission is None else 403), (name, response.text)
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AuditLog)) == before_audits


def test_company_member_and_audit_remain_one_transaction_after_router_extraction(
    client, app, tenant, tenant_headers, monkeypatch,
):
    body = {"email": "router-atomic@example.com", "display_name": "Atomic Member"}

    def fail_audit(*args, **kwargs):
        raise DomainError("Audit unavailable", "test_audit_unavailable", 503)

    with monkeypatch.context() as patch:
        patch.setattr(AuditService, "append", fail_audit)
        rejected = client.post(_url("/members", tenant), headers=tenant_headers, json=body)
    assert rejected.status_code == 503
    with app.state.session_factory() as session:
        assert session.scalar(select(User).where(User.email == body["email"])) is None

    headers = {**tenant_headers, "X-Request-ID": "company-access-router-audit"}
    created = client.post(_url("/members", tenant), headers=headers, json=body)
    assert created.status_code == 201, created.text
    replay = client.post(_url("/members", tenant), headers=headers, json=body)
    assert replay.status_code == 201, replay.text
    assert replay.json() == created.json()
    with app.state.session_factory() as session:
        audits = session.scalars(select(AuditLog).where(
            AuditLog.action == "company.member.create",
            AuditLog.target_id == created.json()["membership_id"],
        )).all()
        assert len(audits) == 1
        assert audits[0].actor_user_id == tenant["user_id"]
        assert audits[0].request_id == headers["X-Request-ID"]
        assert audits[0].after_summary["company_id"] == tenant["company_id"]
