from __future__ import annotations

from sqlalchemy import func, select

from platform_api.models import (
    CompanyModelGrant,
    GenerationTask,
    LedgerEntry,
    ModelCapability,
    ModelDefinition,
    TaskStatus,
)


def seed_model(
    app,
    company_id: str,
    *,
    price_per_second_cents: int | None = 80,
    price_per_item_cents: int | None = None,
    capability_key: str = "text-to-video",
    capability_config: dict | None = None,
) -> str:
    with app.state.session_factory.begin() as session:
        model = ModelDefinition(
            slug="video-pro",
            display_name="Video Pro",
            provider_key="test-provider",
            billing_mode=(
                "per_second"
                if price_per_second_cents is not None
                else "per_item"
            ),
            relay_capability_revision="sha256:" + ("1" * 64),
        )
        session.add(model)
        session.flush()
        session.add(
            ModelCapability(
                model_id=model.id,
                capability_key=capability_key,
                config=capability_config
                or {"durations": [5, 10], "ratios": ["16:9", "9:16"]},
            )
        )
        session.add(
            CompanyModelGrant(
                company_id=company_id,
                model_id=model.id,
                enabled=True,
                price_per_second_cents=price_per_second_cents,
                price_per_item_cents=price_per_item_cents,
            )
        )
        return model.id


def test_recharge_reserve_and_success_settlement_are_integer_and_idempotent(
    app, client, tenant, tenant_headers, internal_headers
):
    company_id = tenant["company_id"]
    model_id = seed_model(app, company_id)
    recharge_url = f"/api/v1/companies/{company_id}/wallet/recharge"
    recharge_body = {
        "amount_cents": 1000,
        "idempotency_key": "recharge-0001",
        "note": "人工充值测试",
    }

    first_recharge = client.post(
        recharge_url, headers=tenant_headers, json=recharge_body
    )
    repeated_recharge = client.post(
        recharge_url, headers=tenant_headers, json=recharge_body
    )
    assert first_recharge.status_code == 200
    assert repeated_recharge.status_code == 200
    assert repeated_recharge.json()["wallet"] == {
        "company_id": company_id,
        "available_cents": 1000,
        "reserved_cents": 0,
    }

    created = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "reserve-task-0001",
            "request_payload": {
                "prompt": "一只猫在窗边",
                "duration_seconds": 5,
            },
        },
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    assert created.json()["status"] == "queued"
    assert created.json()["reserved_cents"] == 400

    relay_job_id = "11111111-1111-1111-1111-111111111111"
    with app.state.session_factory.begin() as session:
        task = session.get(GenerationTask, task_id)
        task.relay_job_id = relay_job_id
    status_body = {
        "company_id": company_id,
        "task_id": task_id,
        "relay_job_id": relay_job_id,
        "status": "succeeded",
        "outputs": [
            {
                "asset_id": "99999999-9999-4999-8999-999999999999",
                "object_key": f"outputs/{relay_job_id}/wallet-success-output",
                "media_type": "video",
                "content_type": "video/mp4",
                "size_bytes": 1234,
                "sha256": "a" * 64,
            }
        ],
    }
    settled = client.post(
        "/internal/relay/status", headers=internal_headers, json=status_body
    )
    repeated_settlement = client.post(
        "/internal/relay/status", headers=internal_headers, json=status_body
    )
    assert settled.status_code == 200, settled.text
    assert repeated_settlement.status_code == 200
    wallet = client.get(
        f"/api/v1/companies/{company_id}/wallet", headers=tenant_headers
    ).json()
    assert wallet == {
        "company_id": company_id,
        "available_cents": 600,
        "reserved_cents": 0,
    }

    with app.state.session_factory() as session:
        task = session.get(GenerationTask, task_id)
        ledger_count = session.scalar(
            select(func.count(LedgerEntry.id)).where(
                LedgerEntry.company_id == company_id
            )
        )
        assert task.status == TaskStatus.SUCCEEDED
        assert task.actual_cost_cents == 400
        assert ledger_count == 3


def test_failed_task_releases_all_reserved_balance_and_is_idempotent(
    app, client, tenant, tenant_headers, internal_headers
):
    company_id = tenant["company_id"]
    model_id = seed_model(
        app,
        company_id,
        price_per_second_cents=None,
        price_per_item_cents=300,
        capability_config={"max_outputs": 1},
    )
    client.post(
        f"/api/v1/companies/{company_id}/wallet/recharge",
        headers=tenant_headers,
        json={
            "amount_cents": 500,
            "idempotency_key": "recharge-failure-01",
        },
    )
    created = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "reserve-failure-01",
            "request_payload": {"prompt": "test", "output_count": 1},
        },
    )
    task_id = created.json()["id"]
    relay_job_id = "22222222-2222-2222-2222-222222222222"
    with app.state.session_factory.begin() as session:
        task = session.get(GenerationTask, task_id)
        task.relay_job_id = relay_job_id
    body = {
        "company_id": company_id,
        "task_id": task_id,
        "relay_job_id": relay_job_id,
        "status": "failed",
        "failure_reason": "供应商超时",
    }
    failed = client.post(
        "/internal/relay/status",
        headers=internal_headers,
        json=body,
    )
    repeated = client.post(
        "/internal/relay/status",
        headers=internal_headers,
        json=body,
    )
    assert failed.status_code == 200, failed.text
    assert repeated.status_code == 200, repeated.text
    wallet = client.get(
        f"/api/v1/companies/{company_id}/wallet", headers=tenant_headers
    ).json()
    assert wallet["available_cents"] == 500
    assert wallet["reserved_cents"] == 0
    with app.state.session_factory() as session:
        task = session.get(GenerationTask, task_id)
        assert task.status == TaskStatus.FAILED
        assert task.failure_reason == "供应商超时"


def test_company_cannot_create_task_with_another_company_model_grant(
    app, client, tenant, tenant_headers
):
    other = client.post(
        "/api/v1/bootstrap",
        json={
            "company_name": "Other",
            "owner_email": "other-model@example.com",
            "owner_display_name": "Other Owner",
        },
    ).json()
    model_id = seed_model(app, other["company_id"])
    response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "cross-tenant-model",
            "request_payload": {"duration_seconds": 5},
        },
    )
    assert response.status_code == 404
