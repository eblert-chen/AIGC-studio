from __future__ import annotations

from sqlalchemy import select

from platform_api.models import AuditLog

from .conftest import bootstrap


ACTIVE_PERMISSION_CODES = {
    "assets.read",
    "assets.manage",
    "users.read",
    "users.manage",
    "models.read",
    "resources.read",
    "billing.read",
    "billing.manage",
    "tasks.read",
    "tasks.create",
    "reports.read",
    "reports.export",
    "publish.accounts.read",
    "publish.accounts.manage",
    "publish.jobs.read",
    "publish.jobs.manage",
}


def _add_member(client, tenant, headers, suffix):
    response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/members",
        headers=headers,
        json={
            "email": f"permission-{suffix}@example.com",
            "display_name": f"Permission {suffix}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _member_headers(tenant, member):
    return {
        "X-Company-ID": tenant["company_id"],
        "X-User-ID": member["user_id"],
    }


def test_owner_can_flip_every_catalog_permission_and_restore_role_template(
    client, app, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    member = _add_member(client, tenant, tenant_headers, "all-catalog")
    catalog_response = client.get(
        f"/api/v1/companies/{company_id}/permissions",
        headers=tenant_headers,
    )
    assert catalog_response.status_code == 200, catalog_response.text
    catalog = catalog_response.json()
    assert {item["code"] for item in catalog} == ACTIVE_PERMISSION_CODES
    assert all(item["code"] and item["description"] for item in catalog)

    detail_path = (
        f"/api/v1/companies/{company_id}/members/"
        f"{member['membership_id']}/permissions"
    )
    initial = client.get(detail_path, headers=tenant_headers)
    assert initial.status_code == 200, initial.text
    initial_items = {item["code"]: item for item in initial.json()["items"]}
    assert set(initial_items) == {item["code"] for item in catalog}
    assert all(item["override_effect"] is None for item in initial_items.values())

    overrides = {
        code: "deny" if item["inherited"] else "allow"
        for code, item in initial_items.items()
    }
    replaced = client.put(
        detail_path,
        headers={**tenant_headers, "X-Request-ID": "req-permissions-flip-all"},
        json={"overrides": overrides, "expected_overrides": {}},
    )
    assert replaced.status_code == 200, replaced.text
    assert {
        item["permission_code"]: item["effect"]
        for item in replaced.json()["permission_overrides"]
    } == overrides
    assert set(replaced.json()["inherited_permission_codes"]) == {
        code for code, item in initial_items.items() if item["inherited"]
    }
    assert set(replaced.json()["effective_permission_codes"]) == {
        code for code, effect in overrides.items() if effect == "allow"
    }

    flipped = client.get(detail_path, headers=tenant_headers).json()["items"]
    assert all(item["override_effect"] == overrides[item["code"]] for item in flipped)
    assert all(item["effective"] is not item["inherited"] for item in flipped)
    worker_me = client.get(
        f"/api/v1/companies/{company_id}/me",
        headers=_member_headers(tenant, member),
    )
    assert set(worker_me.json()["permission_codes"]) == {
        item["code"] for item in flipped if item["effective"]
    }

    team_lead = next(
        role
        for role in client.get(
            f"/api/v1/companies/{company_id}/roles", headers=tenant_headers
        ).json()
        if role["system_key"] == "team_lead"
    )
    promoted = client.put(
        f"/api/v1/companies/{company_id}/members/{member['membership_id']}/roles",
        headers=tenant_headers,
        json={
            "role_ids": [team_lead["id"]],
            "expected_role_ids": [member["roles"][0]["id"]],
        },
    )
    assert promoted.status_code == 200, promoted.text
    assert {
        item["permission_code"]: item["effect"]
        for item in promoted.json()["permission_overrides"]
    } == overrides
    assert set(promoted.json()["effective_permission_codes"]) == {
        code for code, effect in overrides.items() if effect == "allow"
    }

    cleared = client.put(
        detail_path,
        headers={**tenant_headers, "X-Request-ID": "req-permissions-clear-all"},
        json={"overrides": {}, "expected_overrides": overrides},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["permission_overrides"] == []
    assert set(cleared.json()["effective_permission_codes"]) == set(
        team_lead["permission_codes"]
    )
    assert client.put(
        detail_path,
        headers={**tenant_headers, "X-Request-ID": "req-permissions-clear-replay"},
        json={"overrides": {}, "expected_overrides": {}},
    ).status_code == 200

    with app.state.session_factory() as session:
        audits = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "company.member.permissions.replace",
                    AuditLog.target_id == member["membership_id"],
                )
            ).all()
        )
    assert len(audits) == 2
    by_request = {audit.request_id: audit for audit in audits}
    assert by_request["req-permissions-flip-all"].before_summary == {
        "overrides": {}
    }
    assert by_request["req-permissions-flip-all"].after_summary == {
        "overrides": overrides
    }
    assert by_request["req-permissions-clear-all"].before_summary == {
        "overrides": overrides
    }
    assert by_request["req-permissions-clear-all"].after_summary == {
        "overrides": {}
    }


def test_personal_permissions_are_owner_only_tenant_scoped_and_locked_when_disabled(
    client, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    manager = _add_member(client, tenant, tenant_headers, "manager")
    worker = _add_member(client, tenant, tenant_headers, "worker")
    manager_role = client.post(
        f"/api/v1/companies/{company_id}/roles",
        headers=tenant_headers,
        json={
            "name": "Delegated member manager",
            "permission_codes": ["users.manage", "users.read", "tasks.read"],
        },
    ).json()
    assert client.post(
        f"/api/v1/companies/{company_id}/roles/{manager_role['id']}/assign",
        headers=tenant_headers,
        json={"membership_id": manager["membership_id"]},
    ).status_code == 204
    manager_headers = _member_headers(tenant, manager)
    singular_path = (
        f"/api/v1/companies/{company_id}/members/"
        f"{worker['membership_id']}/permission"
    )
    detail_path = f"{singular_path}s"
    access_path = (
        f"/api/v1/companies/{company_id}/members/"
        f"{worker['membership_id']}/access"
    )
    worker_role_ids = [role["id"] for role in worker["roles"]]

    assert client.put(
        singular_path,
        headers=tenant_headers,
        json={"permission_code": "tasks.read", "effect": "deny"},
    ).status_code == 200

    assert client.put(
        singular_path,
        headers=manager_headers,
        json={"permission_code": "tasks.read", "effect": "allow"},
    ).status_code == 403
    assert client.put(
        detail_path,
        headers=manager_headers,
        json={"overrides": {}, "expected_overrides": {"tasks.read": "deny"}},
    ).status_code == 403
    assert client.put(
        access_path,
        headers=manager_headers,
        json={
            "role_ids": worker_role_ids,
            "permission_overrides": {},
            "expected_role_ids": worker_role_ids,
            "expected_permission_overrides": {"tasks.read": "deny"},
        },
    ).status_code == 403
    assert client.delete(
        f"{singular_path}/tasks.read", headers=manager_headers
    ).status_code == 403

    owner_singular = (
        f"/api/v1/companies/{company_id}/members/"
        f"{tenant['membership_id']}/permission"
    )
    assert client.put(
        owner_singular,
        headers=tenant_headers,
        json={"permission_code": "tasks.read", "effect": "deny"},
    ).status_code == 403
    assert client.delete(
        f"{owner_singular}/tasks.read", headers=tenant_headers
    ).status_code == 403
    assert client.put(
        f"{owner_singular}s",
        headers=tenant_headers,
        json={"overrides": {}, "expected_overrides": {}},
    ).status_code == 403
    assert client.put(
        (
            f"/api/v1/companies/{company_id}/members/"
            f"{tenant['membership_id']}/access"
        ),
        headers=tenant_headers,
        json={
            "role_ids": [],
            "permission_overrides": {},
            "expected_role_ids": [],
            "expected_permission_overrides": {},
        },
    ).status_code == 403

    disabled = client.patch(
        f"/api/v1/companies/{company_id}/members/{worker['membership_id']}/status",
        headers=tenant_headers,
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200
    assert client.put(
        singular_path,
        headers=tenant_headers,
        json={"permission_code": "tasks.read", "effect": "allow"},
    ).status_code == 409
    assert client.delete(
        f"{singular_path}/tasks.read", headers=tenant_headers
    ).status_code == 409
    assert client.put(
        detail_path,
        headers=tenant_headers,
        json={"overrides": {}, "expected_overrides": {"tasks.read": "deny"}},
    ).status_code == 409
    assert client.put(
        access_path,
        headers=tenant_headers,
        json={
            "role_ids": worker_role_ids,
            "permission_overrides": {},
            "expected_role_ids": worker_role_ids,
            "expected_permission_overrides": {"tasks.read": "deny"},
        },
    ).status_code == 409
    disabled_detail = client.get(detail_path, headers=tenant_headers)
    assert disabled_detail.status_code == 200
    tasks_read = next(
        item for item in disabled_detail.json()["items"] if item["code"] == "tasks.read"
    )
    assert tasks_read["override_effect"] == "deny"

    other = bootstrap(client, "permission-foreign")
    foreign_detail = (
        f"/api/v1/companies/{company_id}/members/"
        f"{other['membership_id']}/permissions"
    )
    assert client.get(foreign_detail, headers=tenant_headers).status_code == 404
    assert client.put(
        foreign_detail,
        headers=tenant_headers,
        json={
            "overrides": {"tasks.read": "allow"},
            "expected_overrides": {},
        },
    ).status_code == 404
    assert client.put(
        foreign_detail.removesuffix("/permissions") + "/access",
        headers=tenant_headers,
        json={
            "role_ids": worker_role_ids,
            "permission_overrides": {},
            "expected_role_ids": worker_role_ids,
            "expected_permission_overrides": {},
        },
    ).status_code == 404
    assert client.get(
        (
            f"/api/v1/companies/{other['company_id']}/members/"
            f"{other['membership_id']}/permissions"
        ),
        headers=tenant_headers,
    ).status_code == 403


def test_member_access_update_is_atomic_when_an_override_is_invalid(
    client, app, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    member = _add_member(client, tenant, tenant_headers, "atomic-access")
    roles = client.get(
        f"/api/v1/companies/{company_id}/roles", headers=tenant_headers
    ).json()
    operator = next(role for role in roles if role["system_key"] == "operator")
    team_lead = next(role for role in roles if role["system_key"] == "team_lead")
    access_path = (
        f"/api/v1/companies/{company_id}/members/"
        f"{member['membership_id']}/access"
    )

    rejected = client.put(
        access_path,
        headers={**tenant_headers, "X-Request-ID": "req-access-rejected"},
        json={
            "role_ids": [team_lead["id"]],
            "permission_overrides": {"permission.does-not-exist": "allow"},
            "expected_role_ids": [operator["id"]],
            "expected_permission_overrides": {},
        },
    )
    assert rejected.status_code == 404, rejected.text
    unchanged = next(
        item
        for item in client.get(
            f"/api/v1/companies/{company_id}/members", headers=tenant_headers
        ).json()
        if item["membership_id"] == member["membership_id"]
    )
    assert [role["id"] for role in unchanged["roles"]] == [operator["id"]]
    assert unchanged["permission_overrides"] == []

    updated = client.put(
        access_path,
        headers={**tenant_headers, "X-Request-ID": "req-access-updated"},
        json={
            "role_ids": [team_lead["id"]],
            "permission_overrides": {"billing.read": "allow"},
            "expected_role_ids": [operator["id"]],
            "expected_permission_overrides": {},
        },
    )
    assert updated.status_code == 200, updated.text
    assert [role["id"] for role in updated.json()["roles"]] == [team_lead["id"]]
    assert updated.json()["permission_overrides"] == [
        {"permission_code": "billing.read", "effect": "allow"}
    ]
    assert "billing.read" in updated.json()["effective_permission_codes"]
    assert client.put(
        access_path,
        headers={**tenant_headers, "X-Request-ID": "req-access-replay"},
        json={
            "role_ids": [team_lead["id"]],
            "permission_overrides": {"billing.read": "allow"},
            "expected_role_ids": [team_lead["id"]],
            "expected_permission_overrides": {"billing.read": "allow"},
        },
    ).status_code == 200

    with app.state.session_factory() as session:
        audits = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "company.member.access.replace",
                    AuditLog.target_id == member["membership_id"],
                )
            ).all()
        )
    assert len(audits) == 1
    assert audits[0].request_id == "req-access-updated"
    assert audits[0].before_summary == {
        "role_ids": [operator["id"]],
        "permission_overrides": {},
    }
    assert audits[0].after_summary == {
        "role_ids": [team_lead["id"]],
        "permission_overrides": {"billing.read": "allow"},
    }


def test_single_permission_override_audit_records_before_after_and_idempotency(
    client, app, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    member = _add_member(client, tenant, tenant_headers, "audit")
    singular_path = (
        f"/api/v1/companies/{company_id}/members/"
        f"{member['membership_id']}/permission"
    )
    assert client.put(
        singular_path,
        headers={**tenant_headers, "X-Request-ID": "req-permission-allow"},
        json={"permission_code": "billing.read", "effect": "allow"},
    ).status_code == 200
    assert "billing.read" in client.get(
        f"/api/v1/companies/{company_id}/me",
        headers=_member_headers(tenant, member),
    ).json()["permission_codes"]
    assert client.put(
        singular_path,
        headers={**tenant_headers, "X-Request-ID": "req-permission-allow-replay"},
        json={"permission_code": "billing.read", "effect": "allow"},
    ).status_code == 200
    assert client.put(
        singular_path,
        headers={**tenant_headers, "X-Request-ID": "req-permission-deny"},
        json={"permission_code": "billing.read", "effect": "deny"},
    ).status_code == 200
    clear_path = f"{singular_path}/billing.read"
    assert client.delete(
        clear_path,
        headers={**tenant_headers, "X-Request-ID": "req-permission-clear"},
    ).status_code == 204
    assert client.delete(
        clear_path,
        headers={**tenant_headers, "X-Request-ID": "req-permission-clear-replay"},
    ).status_code == 204

    with app.state.session_factory() as session:
        audits = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.target_id == member["membership_id"],
                    AuditLog.action.in_(
                        [
                            "company.member.permission.set",
                            "company.member.permission.clear",
                        ]
                    ),
                )
            ).all()
        )
    assert len(audits) == 3
    by_request = {audit.request_id: audit for audit in audits}
    assert by_request["req-permission-allow"].before_summary == {
        "permission_code": "billing.read",
        "effect": None,
    }
    assert by_request["req-permission-allow"].after_summary == {
        "permission_code": "billing.read",
        "effect": "allow",
    }
    assert by_request["req-permission-deny"].before_summary["effect"] == "allow"
    assert by_request["req-permission-deny"].after_summary["effect"] == "deny"
    assert by_request["req-permission-clear"].before_summary["effect"] == "deny"
    assert by_request["req-permission-clear"].after_summary["effect"] is None


def test_bulk_replace_payloads_require_explicit_fields_and_reject_unknown_fields(
    client, app, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    member = _add_member(client, tenant, tenant_headers, "strict-payload")
    role_ids = [role["id"] for role in member["roles"]]
    singular_path = (
        f"/api/v1/companies/{company_id}/members/"
        f"{member['membership_id']}/permission"
    )
    detail_path = f"{singular_path}s"
    access_path = (
        f"/api/v1/companies/{company_id}/members/"
        f"{member['membership_id']}/access"
    )
    assert client.put(
        singular_path,
        headers=tenant_headers,
        json={"permission_code": "tasks.read", "effect": "deny"},
    ).status_code == 200
    assert client.put(
        singular_path,
        headers=tenant_headers,
        json={"permission_code": "tasks.manage", "effect": "allow"},
    ).status_code == 404

    invalid_permission_payloads = [
        {"permission_overrides": {"tasks.read": "deny"}},
        {"overrides": {}},
        {
            "overrides": {},
            "expected_overrides": {"tasks.read": "deny"},
            "unknown": True,
        },
    ]
    for payload in invalid_permission_payloads:
        assert client.put(
            detail_path, headers=tenant_headers, json=payload
        ).status_code == 422

    invalid_access_payloads = [
        {
            "role_ids": role_ids,
            "expected_role_ids": role_ids,
            "expected_permission_overrides": {"tasks.read": "deny"},
        },
        {
            "role_ids": role_ids,
            "permissionOverrides": {},
            "expected_role_ids": role_ids,
            "expected_permission_overrides": {"tasks.read": "deny"},
        },
        {
            "role_ids": role_ids,
            "permission_overrides": {},
            "expected_role_ids": role_ids,
            "expected_permission_overrides": {"tasks.read": "deny"},
            "unknown": True,
        },
    ]
    for payload in invalid_access_payloads:
        assert client.put(
            access_path, headers=tenant_headers, json=payload
        ).status_code == 422

    unchanged = client.get(detail_path, headers=tenant_headers).json()["items"]
    tasks_read = next(item for item in unchanged if item["code"] == "tasks.read")
    assert tasks_read["override_effect"] == "deny"
    assert tasks_read["effective"] is False
    with app.state.session_factory() as session:
        unsafe_audits = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.target_id == member["membership_id"],
                    AuditLog.action.in_(
                        [
                            "company.member.permissions.replace",
                            "company.member.access.replace",
                            "company.member.roles.replace",
                        ]
                    ),
                )
            ).all()
        )
    assert unsafe_audits == []


def test_bulk_replace_rejects_stale_member_access_snapshot(
    client, app, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    member = _add_member(client, tenant, tenant_headers, "stale-snapshot")
    roles = client.get(
        f"/api/v1/companies/{company_id}/roles", headers=tenant_headers
    ).json()
    operator = next(role for role in roles if role["system_key"] == "operator")
    team_lead = next(role for role in roles if role["system_key"] == "team_lead")
    singular_path = (
        f"/api/v1/companies/{company_id}/members/"
        f"{member['membership_id']}/permission"
    )
    detail_path = f"{singular_path}s"
    access_path = (
        f"/api/v1/companies/{company_id}/members/"
        f"{member['membership_id']}/access"
    )
    assert client.put(
        singular_path,
        headers=tenant_headers,
        json={"permission_code": "tasks.read", "effect": "deny"},
    ).status_code == 200

    stale_roles = client.put(
        f"/api/v1/companies/{company_id}/members/{member['membership_id']}/roles",
        headers={**tenant_headers, "X-Request-ID": "req-roles-stale"},
        json={
            "role_ids": [team_lead["id"]],
            "expected_role_ids": [],
        },
    )
    assert stale_roles.status_code == 409, stale_roles.text

    stale_access = client.put(
        access_path,
        headers={**tenant_headers, "X-Request-ID": "req-access-stale"},
        json={
            "role_ids": [team_lead["id"]],
            "permission_overrides": {},
            "expected_role_ids": [operator["id"]],
            "expected_permission_overrides": {},
        },
    )
    assert stale_access.status_code == 409, stale_access.text
    stale_permissions = client.put(
        detail_path,
        headers={**tenant_headers, "X-Request-ID": "req-permissions-stale"},
        json={
            "overrides": {"tasks.read": "allow"},
            "expected_overrides": {},
        },
    )
    assert stale_permissions.status_code == 409, stale_permissions.text

    unchanged = next(
        item
        for item in client.get(
            f"/api/v1/companies/{company_id}/members", headers=tenant_headers
        ).json()
        if item["membership_id"] == member["membership_id"]
    )
    assert [role["id"] for role in unchanged["roles"]] == [operator["id"]]
    assert unchanged["permission_overrides"] == [
        {"permission_code": "tasks.read", "effect": "deny"}
    ]
    with app.state.session_factory() as session:
        stale_audits = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.target_id == member["membership_id"],
                    AuditLog.action.in_(
                        [
                            "company.member.permissions.replace",
                            "company.member.access.replace",
                            "company.member.roles.replace",
                        ]
                    ),
                )
            ).all()
        )
    assert stale_audits == []

    refreshed = client.put(
        access_path,
        headers=tenant_headers,
        json={
            "role_ids": [team_lead["id"]],
            "permission_overrides": {"tasks.read": "allow"},
            "expected_role_ids": [operator["id"]],
            "expected_permission_overrides": {"tasks.read": "deny"},
        },
    )
    assert refreshed.status_code == 200, refreshed.text
    assert [role["id"] for role in refreshed.json()["roles"]] == [team_lead["id"]]
    assert refreshed.json()["permission_overrides"] == [
        {"permission_code": "tasks.read", "effect": "allow"}
    ]
