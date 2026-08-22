from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from platform_api.models import AuditLog, CompanyModelGrant, MembershipRole, Role
from platform_api.services.companies import CompanyService

from .conftest import bootstrap


def _admin_headers(client, suffix: str = "lifecycle") -> tuple[str, dict[str, str]]:
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


def _add_member(client, tenant: dict[str, str], headers: dict[str, str], suffix: str):
    response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/members",
        headers=headers,
        json={
            "email": f"member-{suffix}@example.com",
            "display_name": f"Member {suffix}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_company_me_and_member_role_lifecycle_are_complete_and_idempotent(
    client, app, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    roles_response = client.get(
        f"/api/v1/companies/{company_id}/roles", headers=tenant_headers
    )
    assert roles_response.status_code == 200
    roles = roles_response.json()
    by_key = {role["system_key"]: role for role in roles}
    assert set(by_key) == {"owner", "team_lead", "operator"}
    assert by_key["owner"]["permission_codes"]
    assert set(by_key["operator"]["permission_codes"]) == {
        "assets.manage",
        "assets.read",
        "models.read",
        "resources.read",
        "tasks.create",
        "tasks.read",
        "publish.accounts.read",
        "publish.jobs.read",
        "publish.jobs.manage",
    }

    me = client.get(f"/api/v1/companies/{company_id}/me", headers=tenant_headers)
    assert me.status_code == 200
    assert me.json()["membership_id"] == tenant["membership_id"]
    assert {role["system_key"] for role in me.json()["roles"]} == {"owner"}
    assert "users.manage" in me.json()["permission_codes"]

    member = _add_member(client, tenant, tenant_headers, "role-flow")
    assert [role["system_key"] for role in member["roles"]] == ["operator"]
    replay = client.post(
        f"/api/v1/companies/{company_id}/members",
        headers=tenant_headers,
        json={
            "email": "member-role-flow@example.com",
            "display_name": "Member role-flow",
        },
    )
    assert replay.status_code == 201
    assert replay.json()["membership_id"] == member["membership_id"]

    listed_member = next(
        item
        for item in client.get(
            f"/api/v1/companies/{company_id}/members", headers=tenant_headers
        ).json()
        if item["membership_id"] == member["membership_id"]
    )
    assert [(role["id"], role["name"]) for role in listed_member["roles"]] == [
        (by_key["operator"]["id"], "运营")
    ]

    worker_headers = {
        "X-Company-ID": company_id,
        "X-User-ID": member["user_id"],
    }
    worker_me = client.get(f"/api/v1/companies/{company_id}/me", headers=worker_headers)
    assert worker_me.status_code == 200
    assert worker_me.json()["permission_codes"] == sorted(
        by_key["operator"]["permission_codes"]
    )

    promoted = client.post(
        f"/api/v1/companies/{company_id}/roles/{by_key['team_lead']['id']}/assign",
        headers=tenant_headers,
        json={"membership_id": member["membership_id"]},
    )
    assert promoted.status_code == 204
    promoted_member = next(
        item
        for item in client.get(
            f"/api/v1/companies/{company_id}/members", headers=tenant_headers
        ).json()
        if item["membership_id"] == member["membership_id"]
    )
    assert [role["system_key"] for role in promoted_member["roles"]] == ["team_lead"]

    replacement = client.put(
        f"/api/v1/companies/{company_id}/members/{member['membership_id']}/roles",
        headers=tenant_headers,
        json={
            "role_ids": [by_key["operator"]["id"]],
            "expected_role_ids": [by_key["team_lead"]["id"]],
        },
    )
    assert replacement.status_code == 200, replacement.text
    assert replacement.json()["membership_id"] == member["membership_id"]
    assert replacement.json()["roles"][0]["system_key"] == "operator"
    replay_replacement = client.put(
        f"/api/v1/companies/{company_id}/members/{member['membership_id']}/roles",
        headers=tenant_headers,
        json={
            "role_ids": [by_key["operator"]["id"]],
            "expected_role_ids": [by_key["operator"]["id"]],
        },
    )
    assert replay_replacement.status_code == 200
    assert replay_replacement.json() == replacement.json()
    assert (
        client.put(
            f"/api/v1/companies/{company_id}/members/{member['membership_id']}/roles",
            headers=tenant_headers,
            json={
                "role_ids": [
                    by_key["operator"]["id"],
                    by_key["team_lead"]["id"],
                ],
                "expected_role_ids": [by_key["operator"]["id"]],
            },
        ).status_code
        == 409
    )
    assert (
        client.put(
            f"/api/v1/companies/{company_id}/members/{member['membership_id']}/roles",
            headers=tenant_headers,
            json={
                "role_ids": [],
                "expected_role_ids": [by_key["operator"]["id"]],
            },
        ).status_code
        == 409
    )

    disabled = client.patch(
        f"/api/v1/companies/{company_id}/members/{member['membership_id']}/status",
        headers=tenant_headers,
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert disabled.json()["roles"][0]["system_key"] == "operator"
    assert (
        client.patch(
            f"/api/v1/companies/{company_id}/members/{member['membership_id']}/status",
            headers=tenant_headers,
            json={"status": "disabled"},
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/companies/{company_id}/me", headers=worker_headers
        ).status_code
        == 403
    )

    enabled = client.patch(
        f"/api/v1/companies/{company_id}/members/{member['membership_id']}/status",
        headers=tenant_headers,
        json={"status": "active"},
    )
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "active"
    assert (
        client.get(
            f"/api/v1/companies/{company_id}/me", headers=worker_headers
        ).status_code
        == 200
    )

    assert (
        client.patch(
            f"/api/v1/companies/{company_id}/members/{tenant['membership_id']}/status",
            headers=tenant_headers,
            json={"status": "disabled"},
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/v1/companies/{company_id}/roles/{by_key['owner']['id']}",
            headers=tenant_headers,
        ).status_code
        == 403
    )

    with app.state.session_factory() as session:
        status_audits = session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == "company.member.status.update",
                AuditLog.target_id == member["membership_id"],
            )
        )
        assign_audits = session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == "company.member.role.assign",
                AuditLog.target_id == member["membership_id"],
            )
        )
    assert status_audits == 2
    assert assign_audits == 1


def test_owner_configures_builtin_levels_and_member_cannot_edit_self(
    client, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    roles = client.get(
        f"/api/v1/companies/{company_id}/roles", headers=tenant_headers
    ).json()
    by_key = {role["system_key"]: role for role in roles if role["system_key"]}
    updated = client.put(
        f"/api/v1/companies/{company_id}/roles/{by_key['team_lead']['id']}",
        headers=tenant_headers,
        json={
            "name": "组长",
            "description": "由老板配置的组长权限",
            "permission_codes": ["tasks.read", "reports.read"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert set(updated.json()["permission_codes"]) == {
        "tasks.read",
        "reports.read",
    }
    assert (
        client.put(
            f"/api/v1/companies/{company_id}/roles/{by_key['team_lead']['id']}",
            headers=tenant_headers,
            json={
                "name": "主管",
                "description": "rename",
                "permission_codes": ["tasks.read"],
            },
        ).status_code
        == 403
    )

    member = _add_member(client, tenant, tenant_headers, "self-role-guard")
    manager_role = client.post(
        f"/api/v1/companies/{company_id}/roles",
        headers=tenant_headers,
        json={
            "name": "成员管理员",
            "permission_codes": ["users.manage", "tasks.read"],
        },
    ).json()
    assert (
        client.post(
            f"/api/v1/companies/{company_id}/roles/{manager_role['id']}/assign",
            headers=tenant_headers,
            json={"membership_id": member["membership_id"]},
        ).status_code
        == 204
    )
    member_headers = {
        "X-Company-ID": company_id,
        "X-User-ID": member["user_id"],
    }
    assert (
        client.put(
            f"/api/v1/companies/{company_id}/roles/{by_key['team_lead']['id']}",
            headers=member_headers,
            json={
                "name": "组长",
                "description": "attempted edit",
                "permission_codes": ["tasks.read"],
            },
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/api/v1/companies/{company_id}/members/{member['membership_id']}/roles",
            headers=member_headers,
            json={
                "role_ids": [by_key["operator"]["id"]],
                "expected_role_ids": [
                    by_key["operator"]["id"],
                    manager_role["id"],
                ],
            },
        ).status_code
        == 403
    )


def test_custom_role_update_unassign_delete_and_permission_reset(
    client, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    member = _add_member(client, tenant, tenant_headers, "custom-role")
    created = client.post(
        f"/api/v1/companies/{company_id}/roles",
        headers=tenant_headers,
        json={
            "name": "Reviewer",
            "description": "Read tasks",
            "permission_codes": ["tasks.read"],
        },
    )
    assert created.status_code == 201
    role = created.json()
    replay = client.post(
        f"/api/v1/companies/{company_id}/roles",
        headers=tenant_headers,
        json={
            "name": "Reviewer",
            "description": "Read tasks",
            "permission_codes": ["tasks.read"],
        },
    )
    assert replay.status_code == 201
    assert replay.json()["id"] == role["id"]

    updated = client.put(
        f"/api/v1/companies/{company_id}/roles/{role['id']}",
        headers=tenant_headers,
        json={
            "name": "Task operator",
            "description": "Read and create tasks",
            "permission_codes": ["tasks.read", "tasks.create"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert set(updated.json()["permission_codes"]) == {
        "tasks.read",
        "tasks.create",
    }
    assert (
        client.put(
            f"/api/v1/companies/{company_id}/roles/{role['id']}",
            headers=tenant_headers,
            json={
                "name": "Task operator",
                "description": "Read and create tasks",
                "permission_codes": ["tasks.read", "tasks.create"],
            },
        ).status_code
        == 200
    )

    assert (
        client.post(
            f"/api/v1/companies/{company_id}/roles/{role['id']}/assign",
            headers=tenant_headers,
            json={"membership_id": member["membership_id"]},
        ).status_code
        == 204
    )
    assert (
        client.delete(
            f"/api/v1/companies/{company_id}/roles/{role['id']}/assignments/{member['membership_id']}",
            headers=tenant_headers,
        ).status_code
        == 204
    )
    assert (
        client.delete(
            f"/api/v1/companies/{company_id}/roles/{role['id']}/assignments/{member['membership_id']}",
            headers=tenant_headers,
        ).status_code
        == 204
    )

    override = client.put(
        f"/api/v1/companies/{company_id}/members/{member['membership_id']}/permission",
        headers=tenant_headers,
        json={"permission_code": "tasks.read", "effect": "allow"},
    )
    assert override.status_code == 200
    assert (
        client.put(
            f"/api/v1/companies/{company_id}/members/{member['membership_id']}/permission",
            headers=tenant_headers,
            json={"permission_code": "tasks.read", "effect": "allow"},
        ).json()["id"]
        == override.json()["id"]
    )
    clear_path = (
        f"/api/v1/companies/{company_id}/members/{member['membership_id']}"
        "/permission/tasks.read"
    )
    assert client.delete(clear_path, headers=tenant_headers).status_code == 204
    assert client.delete(clear_path, headers=tenant_headers).status_code == 204

    assert (
        client.delete(
            f"/api/v1/companies/{company_id}/roles/{role['id']}",
            headers=tenant_headers,
        ).status_code
        == 204
    )
    role_ids = {
        item["id"]
        for item in client.get(
            f"/api/v1/companies/{company_id}/roles", headers=tenant_headers
        ).json()
    }
    assert role["id"] not in role_ids


def test_role_replacement_rejects_foreign_roles(client, tenant, tenant_headers):
    other = bootstrap(client, "lifecycle-foreign")
    member = _add_member(client, tenant, tenant_headers, "foreign-role")
    other_headers = {
        "X-Company-ID": other["company_id"],
        "X-User-ID": other["user_id"],
    }
    foreign_role = next(
        role
        for role in client.get(
            f"/api/v1/companies/{other['company_id']}/roles",
            headers=other_headers,
        ).json()
        if role["system_key"] == "operator"
    )
    response = client.put(
        f"/api/v1/companies/{tenant['company_id']}/members/{member['membership_id']}/roles",
        headers=tenant_headers,
        json={
            "role_ids": [foreign_role["id"]],
            "expected_role_ids": [member["roles"][0]["id"]],
        },
    )
    assert response.status_code == 404
    own_operator = next(
        role
        for role in client.get(
            f"/api/v1/companies/{tenant['company_id']}/roles",
            headers=tenant_headers,
        ).json()
        if role["system_key"] == "operator"
    )
    assert (
        client.put(
            (
                f"/api/v1/companies/{tenant['company_id']}/members/"
                f"{other['membership_id']}/roles"
            ),
            headers=tenant_headers,
            json={"role_ids": [own_operator["id"]], "expected_role_ids": []},
        ).status_code
        == 404
    )
    assert (
        client.patch(
            (
                f"/api/v1/companies/{tenant['company_id']}/members/"
                f"{other['membership_id']}/status"
            ),
            headers=tenant_headers,
            json={"status": "disabled"},
        ).status_code
        == 404
    )


def test_platform_admin_model_catalog_full_lifecycle(client, app, tenant):
    admin_id, admin_headers = _admin_headers(client, "model-catalog")
    me = client.get("/api/v1/platform-admin/me", headers=admin_headers)
    assert me.status_code == 200
    assert {
        key: me.json()[key]
        for key in (
            "user_id",
            "email",
            "display_name",
            "is_platform_admin",
        )
    } == {
        "user_id": admin_id,
        "email": "admin-model-catalog@example.com",
        "display_name": "Admin model-catalog",
        "is_platform_admin": True,
    }
    assert me.json()["is_platform_owner"] is True
    assert "platform.models.manage" in me.json()["permission_codes"]

    payload = {
        "slug": "catalog.video-v1",
        "display_name": "Catalog Video",
        "provider_key": "relay-primary",
        "billing_mode": "per_item",
        "capabilities": [
            {"key": "duration", "config": {"values": [5, 10]}},
            {"key": "aspect_ratio", "config": {"values": ["16:9"]}},
        ],
    }
    created = client.post(
        "/api/v1/platform-admin/models", headers=admin_headers, json=payload
    )
    assert created.status_code == 201, created.text
    model = created.json()
    assert model["status"] == "draft"
    assert model["active"] is False
    assert model["published_at"] is None
    assert model["capability_version"] == 1

    replay = client.post(
        "/api/v1/platform-admin/models", headers=admin_headers, json=payload
    )
    assert replay.status_code == 201
    assert replay.json()["id"] == model["id"]

    draft_grant = client.put(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/model-grants",
        headers=admin_headers,
        json={
            "model_id": model["id"],
            "enabled": True,
            "price_per_item_cents": 100,
        },
    )
    assert draft_grant.status_code == 409

    update_payload = {
        "display_name": "Catalog Video Pro",
        "provider_key": "relay-primary",
        "expected_capability_version": 1,
        "capabilities": [
            {"key": "duration", "config": {"values": [5, 10, 15]}},
            {"key": "aspect_ratio", "config": {"values": ["16:9", "9:16"]}},
        ],
    }
    updated = client.put(
        f"/api/v1/platform-admin/models/{model['id']}",
        headers=admin_headers,
        json=update_payload,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["capability_version"] == 2
    replay_update = client.put(
        f"/api/v1/platform-admin/models/{model['id']}",
        headers=admin_headers,
        json=update_payload,
    )
    assert replay_update.status_code == 200
    assert replay_update.json()["capability_version"] == 2
    stale = client.put(
        f"/api/v1/platform-admin/models/{model['id']}",
        headers=admin_headers,
        json={**update_payload, "display_name": "Stale replacement"},
    )
    assert stale.status_code == 409

    published = client.post(
        f"/api/v1/platform-admin/models/{model['id']}/publish",
        headers=admin_headers,
    )
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    published_at = published.json()["published_at"]
    assert published_at
    assert (
        client.post(
            f"/api/v1/platform-admin/models/{model['id']}/publish",
            headers=admin_headers,
        ).json()["published_at"]
        == published_at
    )

    grant = client.put(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/model-grants",
        headers=admin_headers,
        json={
            "model_id": model["id"],
            "enabled": True,
            "price_per_item_cents": 100,
        },
    )
    assert grant.status_code == 200, grant.text
    assert (
        client.put(
            f"/api/v1/platform-admin/models/{model['id']}",
            headers=admin_headers,
            json={**update_payload, "expected_capability_version": 2},
        ).status_code
        == 409
    )

    disabled = client.post(
        f"/api/v1/platform-admin/models/{model['id']}/disable",
        headers=admin_headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert (
        client.post(
            f"/api/v1/platform-admin/models/{model['id']}/disable",
            headers=admin_headers,
        ).status_code
        == 200
    )

    revised_payload = {
        **update_payload,
        "expected_capability_version": 2,
        "capabilities": [
            {"key": "duration", "config": {"values": [5, 10, 15, 20]}},
        ],
    }
    revised = client.put(
        f"/api/v1/platform-admin/models/{model['id']}",
        headers=admin_headers,
        json=revised_payload,
    )
    assert revised.status_code == 200
    assert revised.json()["capability_version"] == 3
    republished = client.post(
        f"/api/v1/platform-admin/models/{model['id']}/publish",
        headers=admin_headers,
    )
    assert republished.status_code == 200
    assert republished.json()["published_at"] == published_at
    assert (
        client.delete(
            f"/api/v1/platform-admin/models/{model['id']}", headers=admin_headers
        ).status_code
        == 409
    )

    listed = client.get("/api/v1/platform-admin/models", headers=admin_headers)
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == model["id"]
    detail = client.get(
        f"/api/v1/platform-admin/models/{model['id']}", headers=admin_headers
    )
    assert detail.status_code == 200
    assert detail.json()["capability_version"] == 3

    disposable = client.post(
        "/api/v1/platform-admin/models",
        headers=admin_headers,
        json={
            "slug": "catalog.disposable",
            "display_name": "Disposable",
            "provider_key": "relay-primary",
            "capabilities": [],
        },
    ).json()
    assert (
        client.delete(
            f"/api/v1/platform-admin/models/{disposable['id']}",
            headers=admin_headers,
        ).status_code
        == 204
    )
    assert (
        client.get(
            f"/api/v1/platform-admin/models/{disposable['id']}",
            headers=admin_headers,
        ).status_code
        == 404
    )

    with app.state.session_factory() as session:
        counts = {
            action: session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.action == action,
                    AuditLog.target_id == model["id"],
                )
            )
            for action in (
                "model.create",
                "model.update",
                "model.publish",
                "model.disable",
            )
        }
        assert (
            session.scalar(
                select(func.count(CompanyModelGrant.id)).where(
                    CompanyModelGrant.model_id == model["id"]
                )
            )
            == 1
        )
    assert counts == {
        "model.create": 1,
        "model.update": 2,
        "model.publish": 2,
        "model.disable": 1,
    }


def test_platform_admin_model_catalog_rejects_tenant_and_missing_admin(
    client, tenant_headers
):
    assert (
        client.get("/api/v1/platform-admin/models", headers=tenant_headers).status_code
        == 401
    )
    assert client.get("/api/v1/platform-admin/me").status_code == 401


def test_access_lifecycle_migration_backfills_existing_catalog_and_company(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_path = tmp_path / "access-lifecycle.db"
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    command.upgrade(config, "0007_task_timeout_compensation")

    company_id = str(uuid.uuid4())
    owner_role_id = str(uuid.uuid4())
    model_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO companies "
                "(id, name, status, created_at, updated_at) VALUES "
                "(:id, :name, :status, :created_at, :updated_at)"
            ),
            {
                "id": company_id,
                "name": "Existing company",
                "status": "ACTIVE",
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO roles "
                "(id, company_id, name, description, is_system, created_at, updated_at) "
                "VALUES (:id, :company_id, :name, :description, :is_system, "
                ":created_at, :updated_at)"
            ),
            {
                "id": owner_role_id,
                "company_id": company_id,
                "name": "老板",
                "description": "Existing owner",
                "is_system": True,
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO model_definitions "
                "(id, slug, display_name, provider_key, capability_version, active, "
                "created_at, updated_at) VALUES "
                "(:id, :slug, :display_name, :provider_key, 1, :active, "
                ":created_at, :updated_at)"
            ),
            {
                "id": model_id,
                "slug": "existing.model",
                "display_name": "Existing model",
                "provider_key": "existing-provider",
                "active": True,
                "created_at": now,
                "updated_at": now,
            },
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.connect() as connection:
        system_keys = set(
            connection.execute(
                text("SELECT system_key FROM roles WHERE company_id = :company_id"),
                {"company_id": company_id},
            ).scalars()
        )
        published_at = connection.execute(
            text("SELECT published_at FROM model_definitions WHERE id = :model_id"),
            {"model_id": model_id},
        ).scalar()
    engine.dispose()
    assert system_keys == {"owner", "team_lead", "operator"}
    assert published_at is not None


def test_company_member_level_migration_normalizes_legacy_assignments(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_path = tmp_path / "company-member-levels.db"
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    command.upgrade(config, "0011_relay_submit_reconcile")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    company_id = "legacy-company"
    ids = {
        "owner": "legacy-owner-membership",
        "roleless": "legacy-roleless-membership",
        "dual": "legacy-dual-membership",
    }
    role_ids = {
        "owner": "legacy-owner-role",
        "team_lead": "legacy-team-lead-role",
        "operator": "legacy-operator-role",
    }
    now = datetime.now(timezone.utc)
    # Seed only columns present at 0011. Calling current services here would
    # couple this historical migration fixture to fields added after 0011.
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO companies (id, name, status, created_at, updated_at) "
                "VALUES (:id, :name, 'ACTIVE', :created_at, :updated_at)"
            ),
            {
                "id": company_id,
                "name": "Legacy organization",
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, email, display_name, is_platform_admin, created_at, updated_at) "
                "VALUES (:id, :email, :display_name, 0, :created_at, :updated_at)"
            ),
            [
                {
                    "id": "legacy-owner-user",
                    "email": "legacy-owner@example.com",
                    "display_name": "Legacy Owner",
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": "legacy-roleless-user",
                    "email": "legacy-roleless@example.com",
                    "display_name": "Legacy Roleless",
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": "legacy-dual-user",
                    "email": "legacy-dual@example.com",
                    "display_name": "Legacy Dual",
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
        connection.execute(
            text(
                "INSERT INTO roles "
                "(id, company_id, name, description, is_system, system_key, "
                "created_at, updated_at) VALUES "
                "(:id, :company_id, :name, :description, 1, :system_key, "
                ":created_at, :updated_at)"
            ),
            [
                {
                    "id": role_ids[system_key],
                    "company_id": company_id,
                    "name": name,
                    "description": f"Legacy {system_key}",
                    "system_key": system_key,
                    "created_at": now,
                    "updated_at": now,
                }
                for system_key, name in (
                    ("owner", "Legacy Owner"),
                    ("team_lead", "Legacy Team Lead"),
                    ("operator", "Legacy Operator"),
                )
            ],
        )
        connection.execute(
            text(
                "INSERT INTO company_memberships "
                "(id, company_id, user_id, status, created_at, updated_at) "
                "VALUES (:id, :company_id, :user_id, 'ACTIVE', "
                ":created_at, :updated_at)"
            ),
            [
                {
                    "id": ids[label],
                    "company_id": company_id,
                    "user_id": user_id,
                    "created_at": now,
                    "updated_at": now,
                }
                for label, user_id in (
                    ("owner", "legacy-owner-user"),
                    ("roleless", "legacy-roleless-user"),
                    ("dual", "legacy-dual-user"),
                )
            ],
        )
        connection.execute(
            text(
                "INSERT INTO membership_roles (membership_id, role_id) "
                "VALUES (:membership_id, :role_id)"
            ),
            [
                {"membership_id": ids["owner"], "role_id": role_ids["owner"]},
                {"membership_id": ids["owner"], "role_id": role_ids["operator"]},
                {"membership_id": ids["dual"], "role_id": role_ids["operator"]},
                {"membership_id": ids["dual"], "role_id": role_ids["team_lead"]},
            ],
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.connect() as connection:
        normalized = {
            label: set(
                connection.execute(
                    text(
                        "SELECT roles.system_key FROM roles "
                        "JOIN membership_roles "
                        "ON membership_roles.role_id = roles.id "
                        "WHERE membership_roles.membership_id = :membership_id "
                        "AND roles.system_key IS NOT NULL"
                    ),
                    {"membership_id": membership_id},
                ).scalars()
            )
            for label, membership_id in ids.items()
        }
    engine.dispose()

    assert normalized == {
        "owner": {"owner"},
        "roleless": {"operator"},
        "dual": {"team_lead"},
    }
