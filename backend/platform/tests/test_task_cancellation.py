from __future__ import annotations

from sqlalchemy import func, select

from platform_api.models import (
    AuditLog,
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    RelayOutboxStatus,
    RelaySubmissionOutbox,
    TaskStatus,
    WalletAccount,
)

from .test_wallet_and_tasks import seed_model


def _create_reserved_task(app, client, tenant, tenant_headers, suffix: str) -> str:
    company_id = tenant["company_id"]
    model_id = seed_model(app, company_id)
    recharge = client.post(
        f"/api/v1/companies/{company_id}/wallet/recharge",
        headers=tenant_headers,
        json={
            "amount_cents": 1000,
            "idempotency_key": f"cancel-recharge-{suffix}",
        },
    )
    assert recharge.status_code == 200, recharge.text
    created = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": f"cancel-task-{suffix}",
            "request_payload": {
                "prompt": "safe cancellation test",
                "duration_seconds": 5,
            },
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["reserved_cents"] == 400
    return created.json()["id"]


def test_creator_can_cancel_unsubmitted_task_once_and_release_all_balance(
    app, client, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    task_id = _create_reserved_task(
        app, client, tenant, tenant_headers, "happy-path-01"
    )

    first = client.post(
        f"/api/v1/companies/{company_id}/tasks/{task_id}/cancel",
        headers=tenant_headers,
    )
    replay = client.post(
        f"/api/v1/companies/{company_id}/tasks/{task_id}/cancel",
        headers=tenant_headers,
    )

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert first.json()["status"] == "cancelled"
    assert replay.json()["status"] == "cancelled"
    assert replay.json()["reserved_cents"] == 0

    with app.state.session_factory() as session:
        task = session.get(GenerationTask, task_id)
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task_id
            )
        )
        wallet = session.get(WalletAccount, company_id)
        release_count = session.scalar(
            select(func.count(LedgerEntry.id)).where(
                LedgerEntry.task_id == task_id,
                LedgerEntry.kind == LedgerKind.RELEASE,
            )
        )
        audit_count = session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.target_id == task_id,
                AuditLog.action == "generation.task.cancel",
            )
        )
        assert task is not None and task.status == TaskStatus.CANCELLED
        assert task.reserved_cents == 0
        assert outbox is not None and outbox.status == RelayOutboxStatus.CANCELLED
        assert wallet is not None
        assert (wallet.available_cents, wallet.reserved_cents) == (1000, 0)
        assert release_count == 1
        assert audit_count == 1


def test_cancel_fails_closed_after_dispatch_claim_or_submit_attempt(
    app, client, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    task_id = _create_reserved_task(
        app, client, tenant, tenant_headers, "claimed-path-01"
    )
    with app.state.session_factory.begin() as session:
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task_id
            )
        )
        assert outbox is not None
        outbox.status = RelayOutboxStatus.PROCESSING
        outbox.attempt_count = 1

    response = client.post(
        f"/api/v1/companies/{company_id}/tasks/{task_id}/cancel",
        headers=tenant_headers,
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "conflict"

    with app.state.session_factory() as session:
        task = session.get(GenerationTask, task_id)
        wallet = session.get(WalletAccount, company_id)
        assert task is not None and task.status == TaskStatus.QUEUED
        assert task.reserved_cents == 400
        assert wallet is not None
        assert (wallet.available_cents, wallet.reserved_cents) == (600, 400)


def test_cancel_does_not_reveal_or_mutate_another_creators_task(
    app, client, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    task_id = _create_reserved_task(
        app, client, tenant, tenant_headers, "owner-scope-01"
    )
    # A different user identity is intentionally not a member. Authentication
    # fails before task lookup, preserving the same non-disclosure outcome.
    foreign_headers = {
        "X-Company-ID": company_id,
        "X-User-ID": "11111111-1111-4111-8111-111111111111",
    }
    response = client.post(
        f"/api/v1/companies/{company_id}/tasks/{task_id}/cancel",
        headers=foreign_headers,
    )
    assert response.status_code in {401, 403, 404}

    with app.state.session_factory() as session:
        task = session.get(GenerationTask, task_id)
        assert task is not None and task.status == TaskStatus.QUEUED
        assert task.reserved_cents == 400
