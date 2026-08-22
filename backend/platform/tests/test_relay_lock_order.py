from __future__ import annotations

from types import SimpleNamespace

from platform_api.models import (
    GenerationTask,
    RelayOutboxStatus,
    RelaySubmissionOutbox,
    TaskStatus,
    WalletAccount,
)
from platform_api.services.relay_callbacks import RelayCallbackService
from platform_api.services.relay_outbox import RelayOutboxDispatcher
from platform_api.services.relay_status import RelayStatusService
from platform_api.services.task_cancellation import GenerationCancellationService

COMPANY_ID = "company-lock-order"
TASK_ID = "task-lock-order"
OUTBOX_ID = "outbox-lock-order"


def _task() -> GenerationTask:
    return GenerationTask(
        id=TASK_ID,
        company_id=COMPANY_ID,
        user_id="user-lock-order",
        model_id="model-lock-order",
        idempotency_key="task-lock-order-key",
        request_fingerprint="a" * 64,
        status=TaskStatus.PROCESSING,
        request_payload={},
        quote_cents=100,
        pricing_snapshot={},
        capability_snapshot={},
        reserved_cents=100,
        relay_job_id="relay-lock-order",
    )


def _outbox() -> RelaySubmissionOutbox:
    return RelaySubmissionOutbox(
        id=OUTBOX_ID,
        company_id=COMPANY_ID,
        task_id=TASK_ID,
        status=RelayOutboxStatus.PROCESSING,
        idempotency_key="outbox-lock-order-key",
        relay_payload={},
        attempt_count=1,
    )


class _IdentityResult:
    def one_or_none(self):
        return SimpleNamespace(
            company_id=COMPANY_ID,
            personal_workspace_id=None,
            task_id=TASK_ID,
        )


class _RecordingSession:
    def __init__(self):
        self.locked_entities: list[type] = []
        self.wallet = WalletAccount(
            company_id=COMPANY_ID, available_cents=0, reserved_cents=100
        )
        self.task = _task()
        self.outbox = _outbox()

    def execute(self, statement):
        assert statement._for_update_arg is None
        return _IdentityResult()

    def scalar(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if statement._for_update_arg is None:
            if entity is GenerationTask:
                return COMPANY_ID
            raise AssertionError("unexpected unlocked scalar query")
        self.locked_entities.append(entity)
        return {
            WalletAccount: self.wallet,
            GenerationTask: self.task,
            RelaySubmissionOutbox: self.outbox,
        }[entity]

    def flush(self):
        return None


def test_terminal_status_context_locks_wallet_before_task():
    session = _RecordingSession()

    task = RelayStatusService.lock_wallet_and_task_for_update(
        session, company_id=COMPANY_ID, task_id=TASK_ID
    )

    assert task is session.task
    assert session.locked_entities == [WalletAccount, GenerationTask]


def test_terminal_callback_locks_wallet_then_task_then_outbox():
    session = _RecordingSession()

    task, outbox = RelayCallbackService._lock_task_and_outbox(
        session,
        task_id=TASK_ID,
        target_status=TaskStatus.SUCCEEDED,
    )

    assert task is session.task
    assert outbox is session.outbox
    assert session.locked_entities == [
        WalletAccount,
        GenerationTask,
        RelaySubmissionOutbox,
    ]


def test_terminal_dispatch_locks_wallet_then_task_then_outbox():
    session = _RecordingSession()

    task, outbox = RelayOutboxDispatcher._lock_task_and_outbox(
        session,
        outbox_id=OUTBOX_ID,
        include_wallet=True,
    )

    assert task is session.task
    assert outbox is session.outbox
    assert session.locked_entities == [
        WalletAccount,
        GenerationTask,
        RelaySubmissionOutbox,
    ]


def test_nonbilling_dispatch_locks_task_before_outbox():
    session = _RecordingSession()

    RelayOutboxDispatcher._lock_task_and_outbox(
        session,
        outbox_id=OUTBOX_ID,
        include_wallet=False,
    )

    assert session.locked_entities == [
        GenerationTask,
        RelaySubmissionOutbox,
    ]


def test_generation_cancellation_locks_wallet_then_task_then_outbox(monkeypatch):
    session = _RecordingSession()
    session.task.status = TaskStatus.QUEUED
    session.task.relay_job_id = None
    session.task.user_id = "user-lock-order"
    session.outbox.status = RelayOutboxStatus.PENDING
    session.outbox.relay_job_id = None

    monkeypatch.setattr(
        "platform_api.services.task_cancellation.WalletService.release_failure",
        lambda *args, **kwargs: None,
    )
    GenerationCancellationService.cancel_unsubmitted(
        session,
        company_id=COMPANY_ID,
        task_id=TASK_ID,
        actor_user_id="user-lock-order",
    )

    assert session.locked_entities == [
        WalletAccount,
        GenerationTask,
        RelaySubmissionOutbox,
    ]
