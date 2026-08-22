from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from platform_api.models import (
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    RelayOutboxStatus,
    RelaySubmissionOutbox,
    TaskStatus,
    TaskTimeoutEvent,
    WalletAccount,
    utcnow,
)
from platform_api.relay_client import (
    RelayArtifact,
    RelayJobSnapshot,
    RelayTemporaryError,
)
from platform_api.services.relay_status import RelayStatusService
from platform_api.services.task_timeouts import TaskTimeoutService

from .test_relay_boundary import job_snapshot, recharge_and_create


RELAY_JOB_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


def _make_stale(app, task_id: str, *, relay_job_id: str | None = None) -> None:
    with app.state.session_factory.begin() as session:
        task = session.get(GenerationTask, task_id)
        task.created_at = utcnow() - timedelta(hours=8)
        if relay_job_id is not None:
            task.relay_job_id = relay_job_id
            task.status = TaskStatus.PROCESSING
            outbox = session.scalar(
                select(RelaySubmissionOutbox).where(
                    RelaySubmissionOutbox.task_id == task.id
                )
            )
            outbox.status = RelayOutboxStatus.SENT
            outbox.relay_job_id = relay_job_id


def _artifact() -> RelayArtifact:
    return RelayArtifact(
        asset_id="88888888-8888-4888-8888-888888888888",
        object_key=f"outputs/{RELAY_JOB_ID}/timeout-output",
        media_type="video",
        content_type="video/mp4",
        size_bytes=42,
        sha256="e" * 64,
    )


def test_stale_undispatched_queue_releases_reservation_once(
    app, client, tenant, tenant_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="timeout-undispatched"
    )
    _make_stale(app, task["id"])
    service = TaskTimeoutService(
        app.state.session_factory,
        relay_client=None,
        queued_timeout_seconds=60,
        processing_timeout_seconds=60,
    )

    first = service.scan_once()
    repeated = service.scan_once()

    assert first.scanned == 1
    assert first.compensated == 1
    assert first.items[0].outcome == "timeout_released"
    assert first.items[0].released_cents == 400
    assert repeated.scanned == 0
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        wallet = session.get(WalletAccount, tenant["company_id"])
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task["id"]
            )
        )
        event = session.scalar(
            select(TaskTimeoutEvent).where(TaskTimeoutEvent.task_id == task["id"])
        )
        releases = session.scalar(
            select(func.count(LedgerEntry.id)).where(
                LedgerEntry.task_id == task["id"],
                LedgerEntry.kind == LedgerKind.RELEASE,
            )
        )
        assert stored.status == TaskStatus.FAILED
        assert stored.reserved_cents == 0
        assert stored.failure_reason.startswith("platform_timeout:")
        assert wallet.available_cents == 1000
        assert wallet.reserved_cents == 0
        assert outbox.status == RelayOutboxStatus.PERMANENTLY_FAILED
        assert event.outcome == "timeout_released"
        assert event.ledger_entry_id is not None
        assert releases == 1


def test_unknown_inflight_submission_is_never_released(
    app, client, tenant, tenant_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="timeout-inflight"
    )
    _make_stale(app, task["id"])
    with app.state.session_factory.begin() as session:
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task["id"]
            )
        )
        outbox.status = RelayOutboxStatus.PROCESSING

    result = TaskTimeoutService(
        app.state.session_factory,
        relay_client=None,
        queued_timeout_seconds=60,
        processing_timeout_seconds=60,
    ).scan_once()

    assert result.scanned == 1
    assert result.deferred == 1
    assert result.items[0].outcome == "deferred_unsafe_submission"
    with app.state.session_factory.begin() as session:
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task["id"]
            )
        )
        outbox.status = RelayOutboxStatus.RETRY
        outbox.attempt_count = 1
    retry_result = TaskTimeoutService(
        app.state.session_factory,
        relay_client=None,
        queued_timeout_seconds=60,
        processing_timeout_seconds=60,
    ).scan_once()
    assert retry_result.items[0].outcome == "deferred_unsafe_submission"
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        wallet = session.get(WalletAccount, tenant["company_id"])
        assert stored.status == TaskStatus.QUEUED
        assert stored.reserved_cents == 400
        assert stored.timeout_checked_at is not None
        assert wallet.reserved_cents == 400
        assert session.scalar(select(func.count(TaskTimeoutEvent.id))) == 0


def test_timeout_batch_rotation_does_not_starve_later_safe_tasks(
    app, client, tenant, tenant_headers
):
    first = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="timeout-batch-unsafe"
    )
    second_response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": first["model_id"],
            "idempotency_key": "timeout-batch-safe-task",
            "request_payload": {
                "prompt": "safe later task",
                "duration_seconds": 5,
            },
        },
    )
    assert second_response.status_code == 201, second_response.text
    second = second_response.json()
    _make_stale(app, first["id"])
    _make_stale(app, second["id"])
    with app.state.session_factory.begin() as session:
        first_task = session.get(GenerationTask, first["id"])
        second_task = session.get(GenerationTask, second["id"])
        first_task.created_at = utcnow() - timedelta(hours=9)
        second_task.created_at = utcnow() - timedelta(hours=8)
        first_outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == first["id"]
            )
        )
        first_outbox.status = RelayOutboxStatus.PROCESSING

    service = TaskTimeoutService(
        app.state.session_factory,
        relay_client=None,
        queued_timeout_seconds=60,
        processing_timeout_seconds=60,
        batch_size=1,
    )
    first_scan = service.scan_once()
    second_scan = service.scan_once()

    assert first_scan.items[0].task_id == first["id"]
    assert first_scan.items[0].outcome == "deferred_unsafe_submission"
    assert second_scan.items[0].task_id == second["id"]
    assert second_scan.items[0].outcome == "timeout_released"
    with app.state.session_factory() as session:
        assert session.get(GenerationTask, first["id"]).status == TaskStatus.QUEUED
        assert session.get(GenerationTask, second["id"]).status == TaskStatus.FAILED


def test_nonterminal_or_unreachable_relay_is_deferred_without_charge_change(
    app, client, tenant, tenant_headers
):
    first = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="timeout-active"
    )
    client.post(
        f"/api/v1/companies/{tenant['company_id']}/wallet/recharge",
        headers=tenant_headers,
        json={
            "amount_cents": 1000,
            "idempotency_key": "timeout-unreachable-recharge",
        },
    )
    second_response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": first["model_id"],
            "idempotency_key": "timeout-unreachable-task",
            "request_payload": {
                "prompt": "unreachable relay timeout test",
                "duration_seconds": 5,
            },
        },
    )
    assert second_response.status_code == 201, second_response.text
    second = second_response.json()
    first_job = RELAY_JOB_ID
    second_job = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    _make_stale(app, first["id"], relay_job_id=first_job)
    _make_stale(app, second["id"], relay_job_id=second_job)

    class Relay:
        def get(self, relay_job_id):
            if relay_job_id == first_job:
                return job_snapshot(
                    task_id=first["id"],
                    job_id=relay_job_id,
                    status="processing",
                )
            raise RelayTemporaryError("network details must not leak")

    result = TaskTimeoutService(
        app.state.session_factory,
        Relay(),
        queued_timeout_seconds=60,
        processing_timeout_seconds=60,
    ).scan_once()

    assert result.scanned == 2
    assert result.deferred == 2
    assert {item.outcome for item in result.items} == {
        "deferred_relay_active",
        "deferred_relay_query",
    }
    assert "network details" not in str(result.items)
    with app.state.session_factory() as session:
        wallet = session.get(WalletAccount, tenant["company_id"])
        assert wallet.reserved_cents == 800
        assert session.scalar(select(func.count(TaskTimeoutEvent.id))) == 0


def test_timeout_scan_reconciles_authoritative_relay_failure(
    app, client, tenant, tenant_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="timeout-relay-failed"
    )
    _make_stale(app, task["id"], relay_job_id=RELAY_JOB_ID)

    class FailedRelay:
        def get(self, relay_job_id):
            return job_snapshot(
                task_id=task["id"],
                job_id=relay_job_id,
                status="failed",
                error={
                    "code": "GENERATION_FAILED",
                    "message": "provider timed out",
                    "retryable": False,
                    "details": {},
                },
            )

    result = TaskTimeoutService(
        app.state.session_factory,
        FailedRelay(),
        queued_timeout_seconds=60,
        processing_timeout_seconds=60,
    ).scan_once()

    assert result.compensated == 1
    assert result.reconciled == 1
    assert result.items[0].outcome == "relay_failed"
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        wallet = session.get(WalletAccount, tenant["company_id"])
        event = session.scalar(
            select(TaskTimeoutEvent).where(TaskTimeoutEvent.task_id == task["id"])
        )
        assert stored.status == TaskStatus.FAILED
        assert stored.failure_reason == "provider timed out"
        assert wallet.available_cents == 1000
        assert wallet.reserved_cents == 0
        assert event.outcome == "relay_failed"
        assert event.released_cents == 400


def test_success_wins_race_and_timeout_scan_never_releases(
    app, client, tenant, tenant_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="timeout-success-race"
    )
    _make_stale(app, task["id"], relay_job_id=RELAY_JOB_ID)
    snapshot = job_snapshot(
        task_id=task["id"],
        job_id=RELAY_JOB_ID,
        status="succeeded",
        outputs=[_artifact()],
    )

    class RacingRelay:
        def get(self, relay_job_id):
            # Simulate the ordinary poller committing success after candidate
            # selection but before the timeout worker acquires the task lock.
            with app.state.session_factory.begin() as session:
                RelayStatusService.apply(
                    session,
                    company_id=tenant["company_id"],
                    task_id=task["id"],
                    relay_job_id=relay_job_id,
                    status="succeeded",
                    outputs=snapshot.outputs,
                )
            return snapshot

    result = TaskTimeoutService(
        app.state.session_factory,
        RacingRelay(),
        queued_timeout_seconds=60,
        processing_timeout_seconds=60,
    ).scan_once()

    assert result.items[0].outcome == "already_terminal"
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        wallet = session.get(WalletAccount, tenant["company_id"])
        assert stored.status == TaskStatus.SUCCEEDED
        assert wallet.available_cents == 600
        assert wallet.reserved_cents == 0
        assert (
            session.scalar(
                select(func.count(LedgerEntry.id)).where(
                    LedgerEntry.task_id == task["id"],
                    LedgerEntry.kind == LedgerKind.RELEASE,
                )
            )
            == 0
        )
        assert session.scalar(select(func.count(TaskTimeoutEvent.id))) == 0


def test_timeout_scan_reconciles_success_with_normal_settlement(
    app, client, tenant, tenant_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="timeout-relay-success"
    )
    _make_stale(app, task["id"], relay_job_id=RELAY_JOB_ID)

    class SucceededRelay:
        def get(self, relay_job_id):
            return job_snapshot(
                task_id=task["id"],
                job_id=relay_job_id,
                status="succeeded",
                outputs=[_artifact()],
            )

    result = TaskTimeoutService(
        app.state.session_factory,
        SucceededRelay(),
        queued_timeout_seconds=60,
        processing_timeout_seconds=60,
    ).scan_once()

    assert result.reconciled == 1
    assert result.compensated == 0
    assert result.items[0].outcome == "relay_succeeded"
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        wallet = session.get(WalletAccount, tenant["company_id"])
        event = session.scalar(
            select(TaskTimeoutEvent).where(TaskTimeoutEvent.task_id == task["id"])
        )
        assert stored.status == TaskStatus.SUCCEEDED
        assert wallet.available_cents == 600
        assert wallet.reserved_cents == 0
        assert event.outcome == "relay_succeeded"
        assert event.released_cents == 0
        assert event.ledger_entry_id is not None


def test_internal_timeout_scan_and_event_feed_require_service_auth(
    app, client, tenant, tenant_headers, internal_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="timeout-internal-api"
    )
    _make_stale(app, task["id"])

    assert client.post("/internal/tasks/timeout-scan").status_code == 401
    scanned = client.post(
        "/internal/tasks/timeout-scan", headers=internal_headers
    )
    assert scanned.status_code == 200, scanned.text
    assert scanned.json()["compensated"] == 1
    assert scanned.json()["items"][0]["task_id"] == task["id"]

    assert client.get("/internal/tasks/timeout-events").status_code == 401
    events = client.get(
        "/internal/tasks/timeout-events", headers=internal_headers
    )
    assert events.status_code == 200, events.text
    assert events.json()["total"] == 1
    assert events.json()["items"][0]["task_id"] == task["id"]
    assert events.json()["items"][0]["outcome"] == "timeout_released"


def test_timeout_event_is_append_only(app, client, tenant, tenant_headers):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="timeout-immutable-event"
    )
    _make_stale(app, task["id"])
    TaskTimeoutService(
        app.state.session_factory,
        relay_client=None,
        queued_timeout_seconds=60,
        processing_timeout_seconds=60,
    ).scan_once()

    with pytest.raises(RuntimeError, match="immutable"):
        with app.state.session_factory.begin() as session:
            event = session.scalar(
                select(TaskTimeoutEvent).where(
                    TaskTimeoutEvent.task_id == task["id"]
                )
            )
            event.reason = "attempted rewrite"
