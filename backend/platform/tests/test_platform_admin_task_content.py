from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from platform_api.models import GenerationTask, PersonalWorkspace, TaskStatus

from .conftest import bootstrap
from .test_platform_admin import bootstrap_admin
from .test_wallet_and_tasks import seed_model


def _seed_task_content(app, tenant: dict[str, str]):
    model_id = seed_model(app, tenant["company_id"])
    with app.state.session_factory.begin() as session:
        workspace = PersonalWorkspace(user_id=tenant["user_id"])
        session.add(workspace)
        session.flush()
        company_task = GenerationTask(
            company_id=tenant["company_id"],
            user_id=tenant["user_id"],
            model_id=model_id,
            idempotency_key="admin-prompt-company",
            request_fingerprint="a" * 64,
            status=TaskStatus.QUEUED,
            request_payload={
                "prompt": (
                    "联系 creator@example.com 或 13800138000，参考 "
                    "https://private.example.test/shot，API_KEY=ABCDEFGH12345678。"
                    + "镜头缓慢向前移动。"
                    * 12
                ),
                "duration_seconds": 5,
                "internal_resource_key": "must-not-leak",
            },
            quote_cents=400,
            pricing_snapshot={},
            capability_snapshot={},
            reserved_cents=400,
        )
        no_prompt_task = GenerationTask(
            company_id=tenant["company_id"],
            user_id=tenant["user_id"],
            model_id=model_id,
            idempotency_key="admin-prompt-unrecorded",
            request_fingerprint="c" * 64,
            status=TaskStatus.CANCELLED,
            request_payload={"duration_seconds": 5},
            quote_cents=1,
            pricing_snapshot={},
            capability_snapshot={},
            reserved_cents=0,
        )
        personal_task = GenerationTask(
            personal_workspace_id=workspace.id,
            user_id=tenant["user_id"],
            model_id=model_id,
            idempotency_key="admin-prompt-personal",
            request_fingerprint="b" * 64,
            status=TaskStatus.FAILED,
            request_payload={"prompt": "  个人空间\n海边日落 🎬  "},
            quote_points=8,
            pricing_snapshot={},
            capability_snapshot={},
            reserved_points=0,
        )
        session.add_all((company_task, no_prompt_task, personal_task))
        session.flush()
        return {
            "model_id": model_id,
            "workspace_id": workspace.id,
            "company_task_id": company_task.id,
            "no_prompt_task_id": no_prompt_task.id,
            "personal_task_id": personal_task.id,
            "company_prompt": company_task.request_payload["prompt"],
            "personal_prompt": personal_task.request_payload["prompt"],
        }


def test_owner_lists_full_collected_prompts_and_filters_workspaces(app, client):
    tenant = bootstrap(client, "task-content-owner")
    seeded = _seed_task_content(app, tenant)
    _, owner_headers = bootstrap_admin(client, "task-content-owner")

    response = client.get(
        "/api/v1/platform-admin/task-content",
        headers=owner_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 25
    assert {item["workspace_type"] for item in body["items"]} == {
        "company",
        "personal",
    }
    company_item = next(
        item for item in body["items"] if item["task_id"] == seeded["company_task_id"]
    )
    assert company_item["prompt"] == seeded["company_prompt"]
    assert company_item["prompt_length"] == len(seeded["company_prompt"])

    personal_item = next(
        item for item in body["items"] if item["task_id"] == seeded["personal_task_id"]
    )
    assert personal_item["prompt"] == seeded["personal_prompt"]
    assert personal_item["status"] == "failed"

    no_prompt_item = next(
        item for item in body["items"] if item["task_id"] == seeded["no_prompt_task_id"]
    )
    assert no_prompt_item["prompt"] is None
    assert no_prompt_item["prompt_length"] == 0
    assert no_prompt_item["status"] == "cancelled"

    serialized = json.dumps(body, ensure_ascii=False)
    assert "request_payload" not in serialized
    assert "prompt_preview" not in serialized
    assert "internal_resource_key" not in serialized
    assert "must-not-leak" not in serialized
    assert response.headers["cache-control"].startswith("private, no-store")
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"

    company_only = client.get(
        "/api/v1/platform-admin/task-content",
        headers=owner_headers,
        params={
            "workspace_type": "company",
            "company_id": tenant["company_id"],
            "user_id": tenant["user_id"],
            "model_id": seeded["model_id"],
            "status": "queued",
        },
    )
    assert company_only.status_code == 200
    assert [item["task_id"] for item in company_only.json()["items"]] == [
        seeded["company_task_id"]
    ]
    personal_only = client.get(
        "/api/v1/platform-admin/task-content",
        headers=owner_headers,
        params={
            "workspace_type": "personal",
            "personal_workspace_id": seeded["workspace_id"],
            "status": "failed",
        },
    )
    assert personal_only.status_code == 200
    assert [item["task_id"] for item in personal_only.json()["items"]] == [
        seeded["personal_task_id"]
    ]

    now = datetime.now(timezone.utc)
    bounded = client.get(
        "/api/v1/platform-admin/task-content",
        headers=owner_headers,
        params={
            "created_from": (now - timedelta(days=1)).isoformat(),
            "created_before": (now + timedelta(days=1)).isoformat(),
        },
    )
    assert bounded.status_code == 200
    assert bounded.json()["total"] == 3


def test_list_rejects_ambiguous_workspace_and_time_filters(app, client):
    _, owner_headers = bootstrap_admin(client, "task-content-filter-owner")
    contradictory_filters = (
        {"workspace_type": "company", "personal_workspace_id": "workspace-1"},
        {"workspace_type": "personal", "company_id": "company-1"},
        {"company_id": "company-1", "personal_workspace_id": "workspace-1"},
        {"created_from": "2026-08-28T12:00:00"},
        {"created_before": "2026-08-28T12:00:00"},
        {"page_size": 26},
        {
            "created_from": "2026-08-29T00:00:00+08:00",
            "created_before": "2026-08-28T00:00:00+08:00",
        },
    )
    for params in contradictory_filters:
        response = client.get(
            "/api/v1/platform-admin/task-content",
            headers=owner_headers,
            params=params,
        )
        assert response.status_code == 422, (params, response.text)


def test_delegated_admin_fails_closed_then_reads_collection_with_permission(
    app, client
):
    tenant = bootstrap(client, "task-content-delegated")
    seeded = _seed_task_content(app, tenant)
    _, owner_headers = bootstrap_admin(client, "task-content-policy-owner")
    delegated_id, delegated_headers = bootstrap_admin(
        client, "task-content-policy-delegated"
    )

    denied = client.get(
        "/api/v1/platform-admin/task-content", headers=delegated_headers
    )
    assert denied.status_code == 403
    assert "platform.task_content.read" in denied.json()["detail"]

    role = client.post(
        "/api/v1/platform-admin/access/roles",
        headers=owner_headers,
        json={
            "key": "task-content-reader",
            "display_name": "Task prompt collection reader",
            "description": "Can read the automatically collected task prompts",
            "permission_codes": ["platform.task_content.read"],
            "change_reason": "Delegate read-only prompt collection access",
        },
    )
    assert role.status_code == 201, role.text
    assigned = client.put(
        f"/api/v1/platform-admin/access/users/{delegated_id}",
        headers=owner_headers,
        json={
            "role_ids": [role.json()["id"]],
            "permission_overrides": {},
            "expected_lock_version": 0,
            "change_reason": "Grant least-privilege collection access",
        },
    )
    assert assigned.status_code == 200, assigned.text

    allowed = client.get(
        "/api/v1/platform-admin/task-content",
        headers=delegated_headers,
        params={"task_id": seeded["company_task_id"]},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["items"][0]["prompt"] == seeded["company_prompt"]


def test_exact_task_and_workspace_filters_do_not_leak_cross_scope(app, client):
    tenant = bootstrap(client, "task-content-scope")
    seeded = _seed_task_content(app, tenant)
    _, owner_headers = bootstrap_admin(client, "task-content-scope-owner")

    wrong_scope = client.get(
        "/api/v1/platform-admin/task-content",
        headers=owner_headers,
        params={
            "task_id": seeded["company_task_id"],
            "workspace_type": "personal",
            "personal_workspace_id": seeded["workspace_id"],
        },
    )
    missing = client.get(
        "/api/v1/platform-admin/task-content",
        headers=owner_headers,
        params={
            "task_id": "00000000-0000-0000-0000-000000000000",
            "workspace_type": "personal",
            "personal_workspace_id": seeded["workspace_id"],
        },
    )
    assert wrong_scope.status_code == missing.status_code == 200
    assert wrong_scope.json()["total"] == missing.json()["total"] == 0
    assert wrong_scope.json()["items"] == missing.json()["items"] == []

    personal = client.get(
        "/api/v1/platform-admin/task-content",
        headers=owner_headers,
        params={
            "task_id": seeded["personal_task_id"],
            "workspace_type": "personal",
            "personal_workspace_id": seeded["workspace_id"],
        },
    )
    assert personal.status_code == 200, personal.text
    assert personal.json()["items"][0]["prompt"] == seeded["personal_prompt"]


def test_legacy_reveal_post_is_not_exposed(app, client):
    _, owner_headers = bootstrap_admin(client, "task-content-no-reveal-owner")
    response = client.post(
        "/api/v1/platform-admin/task-content/"
        "00000000-0000-0000-0000-000000000000/reveal",
        headers=owner_headers,
        json={"review_reason": "support_case", "company_id": "company-1"},
    )
    assert response.status_code in {404, 405}


def test_0043_migration_adds_collection_permissions_and_removes_them(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "platform-admin-task-content.db"
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    command.upgrade(config, "0024_platform_admin_access")
    command.stamp(config, "0042_entitlement_batch_journal")
    command.upgrade(config, "0043_admin_task_content")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.connect() as connection:
        assert inspect(engine).has_table("platform_admin_permissions")
        rows = connection.execute(
            text(
                "SELECT code, action, description FROM platform_admin_permissions "
                "WHERE domain = 'task_content' ORDER BY code"
            )
        ).all()
        assert rows == [
            (
                "platform.task_content.read",
                "read",
                "查看用户生成提示词自动汇总库",
            )
        ]
    command.downgrade(config, "0042_entitlement_batch_journal")
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM platform_admin_permissions "
                    "WHERE domain = 'task_content'"
                )
            )
            == 0
        )
    engine.dispose()
