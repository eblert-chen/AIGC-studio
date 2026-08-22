from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from relay_service.models import (
    GenerationInputs,
    GenerationMode,
    GenerationRequest,
    JobStatus,
    OutputOptions,
)
from relay_service.outbox import OutboxDispatcher
from relay_service.providers.mock import MockProviderAdapter
from relay_service.providers.router import ProviderRouter
from relay_service.queue import InMemoryWorkQueue
from relay_service.repository import InMemoryJobRepository
from relay_service.service import GenerationService
from relay_service.sql_repository import (
    JobRow,
    SqlAlchemyJobRepository,
    WebhookEventRow,
)


WEBHOOK_HEADERS = {
    "x-mock-webhook-secret": "development-only-secret",
}
MOCK_CAPABILITY_REVISION = asyncio.run(
    ProviderRouter([MockProviderAdapter()]).model_catalog()
).data[0].capability_revision


def request(reference: str) -> GenerationRequest:
    return GenerationRequest(
        client_reference_id=reference,
        model="mock.video.v1",
        expected_capability_revision=MOCK_CAPABILITY_REVISION,
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="Keep webhook state atomic"),
        output=OutputOptions(),
    )


async def make_service(
    repository: InMemoryJobRepository | SqlAlchemyJobRepository,
) -> tuple[GenerationService, OutboxDispatcher | None]:
    queue = InMemoryWorkQueue()
    service = GenerationService(
        repository,
        queue,
        ProviderRouter([MockProviderAdapter()]),
    )
    dispatcher = (
        OutboxDispatcher(repository, queue) if repository.has_outbox else None
    )
    return service, dispatcher


async def create_processing_job(
    service: GenerationService,
    dispatcher: OutboxDispatcher | None,
    *,
    key: str,
):
    accepted = await service.submit(request(key), key, uuid4())
    if dispatcher is not None:
        assert await dispatcher.dispatch_once() == 1
    processing = await service.process_next()
    assert processing is not None
    assert processing.status == JobStatus.PROCESSING
    return accepted, processing


def terminal_event(
    *,
    event_id: str,
    provider_task_id: str,
    status: str,
) -> dict:
    event = {
        "event_id": event_id,
        "provider_task_id": provider_task_id,
        "status": status,
        "progress": 100,
    }
    if status == "failed":
        event["error"] = {
            "code": "UPSTREAM_FAILED",
            "message": "Provider rejected the generation",
            "retryable": False,
        }
    return event


async def assert_terminal_webhook_contract(
    repository: InMemoryJobRepository | SqlAlchemyJobRepository,
) -> None:
    service, dispatcher = await make_service(repository)

    for index, status in enumerate(("failed", "cancelled")):
        accepted, processing = await create_processing_job(
            service,
            dispatcher,
            key=f"terminal-{index}",
        )
        event = terminal_event(
            event_id=f"evt-terminal-{index}",
            provider_task_id=processing.provider_task_id,
            status=status,
        )

        first = await service.receive_webhook(
            "mock-video", json.dumps(event).encode(), WEBHOOK_HEADERS
        )
        duplicate = await service.receive_webhook(
            "mock-video", json.dumps(event).encode(), WEBHOOK_HEADERS
        )

        assert first.duplicate is False
        assert first.status == JobStatus(status)
        assert duplicate.duplicate is True
        assert duplicate.status == JobStatus(status)
        persisted = await repository.get(accepted.job_id)
        assert persisted is not None
        assert persisted.status == JobStatus(status)
        if status == "failed":
            assert persisted.error is not None
            assert persisted.error.code == "UPSTREAM_FAILED"
        else:
            assert persisted.error is None

        # A different, late callback is consumed, but cannot regress or replace
        # a terminal result.
        late = terminal_event(
            event_id=f"evt-terminal-{index}-late",
            provider_task_id=processing.provider_task_id,
            status="cancelled" if status == "failed" else "failed",
        )
        late_receipt = await service.receive_webhook(
            "mock-video", json.dumps(late).encode(), WEBHOOK_HEADERS
        )
        assert late_receipt.duplicate is False
        assert late_receipt.status == JobStatus(status)


def test_memory_terminal_webhooks_are_atomic_and_idempotent() -> None:
    asyncio.run(assert_terminal_webhook_contract(InMemoryJobRepository()))


def test_sql_terminal_webhooks_are_atomic_and_idempotent(tmp_path) -> None:
    async def scenario() -> None:
        repository = SqlAlchemyJobRepository.from_url(
            f"sqlite+aiosqlite:///{tmp_path / 'terminal-webhooks.db'}"
        )
        await repository.create_schema()
        await assert_terminal_webhook_contract(repository)
        async with repository.sessions() as session:
            count = await session.scalar(
                select(func.count()).select_from(WebhookEventRow)
            )
            assert count == 4
        await repository.dispose()

    asyncio.run(scenario())


class FailOnceSet(set):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_add = True

    def add(self, item) -> None:
        if self.fail_next_add:
            self.fail_next_add = False
            super().add(item)
            raise RuntimeError("injected event registration failure")
        super().add(item)


def test_memory_webhook_failure_rolls_back_and_same_event_retries() -> None:
    async def scenario() -> None:
        repository = InMemoryJobRepository()
        service, dispatcher = await make_service(repository)
        accepted, processing = await create_processing_job(
            service, dispatcher, key="memory-retry"
        )
        repository._events = FailOnceSet()
        event = terminal_event(
            event_id="evt-memory-retry",
            provider_task_id=processing.provider_task_id,
            status="failed",
        )

        with pytest.raises(
            RuntimeError, match="injected event registration failure"
        ):
            await service.receive_webhook(
                "mock-video", json.dumps(event).encode(), WEBHOOK_HEADERS
            )

        unchanged = await repository.get(accepted.job_id)
        assert unchanged is not None
        assert unchanged.status == JobStatus.PROCESSING
        retry = await service.receive_webhook(
            "mock-video", json.dumps(event).encode(), WEBHOOK_HEADERS
        )
        duplicate = await service.receive_webhook(
            "mock-video", json.dumps(event).encode(), WEBHOOK_HEADERS
        )
        assert retry.duplicate is False
        assert retry.status == JobStatus.FAILED
        assert duplicate.duplicate is True

    asyncio.run(scenario())


class FailOnceSqlRepository(SqlAlchemyJobRepository):
    def __init__(self, engine) -> None:
        super().__init__(engine)
        self.fail_next_terminal_update = True

    def _apply_model(self, row: JobRow, job) -> None:
        SqlAlchemyJobRepository._apply_model(row, job)
        if (
            self.fail_next_terminal_update
            and job.status in {JobStatus.FAILED, JobStatus.CANCELLED}
        ):
            self.fail_next_terminal_update = False
            raise RuntimeError("injected transaction failure")


def test_sql_webhook_transaction_rolls_back_and_same_event_retries(
    tmp_path,
) -> None:
    async def scenario() -> None:
        repository = FailOnceSqlRepository.from_url(
            f"sqlite+aiosqlite:///{tmp_path / 'webhook-rollback.db'}"
        )
        await repository.create_schema()
        service, dispatcher = await make_service(repository)
        accepted, processing = await create_processing_job(
            service, dispatcher, key="sql-retry"
        )
        event = terminal_event(
            event_id="evt-sql-retry",
            provider_task_id=processing.provider_task_id,
            status="failed",
        )

        with pytest.raises(RuntimeError, match="injected transaction failure"):
            await service.receive_webhook(
                "mock-video", json.dumps(event).encode(), WEBHOOK_HEADERS
            )

        unchanged = await repository.get(accepted.job_id)
        assert unchanged is not None
        assert unchanged.status == JobStatus.PROCESSING
        async with repository.sessions() as session:
            event_count = await session.scalar(
                select(func.count()).select_from(WebhookEventRow)
            )
            assert event_count == 0

        retry = await service.receive_webhook(
            "mock-video", json.dumps(event).encode(), WEBHOOK_HEADERS
        )
        duplicate = await service.receive_webhook(
            "mock-video", json.dumps(event).encode(), WEBHOOK_HEADERS
        )
        assert retry.duplicate is False
        assert retry.status == JobStatus.FAILED
        assert duplicate.duplicate is True
        async with repository.sessions() as session:
            event_count = await session.scalar(
                select(func.count()).select_from(WebhookEventRow)
            )
            assert event_count == 1
        await repository.dispose()

    asyncio.run(scenario())
