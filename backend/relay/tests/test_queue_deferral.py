from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from relay_service.models import (
    GenerationInputs,
    GenerationMode,
    GenerationRequest,
    JobStatus,
    WorkItem,
)
from relay_service.providers.base import ProviderError
from relay_service.queue import InMemoryWorkQueue
from relay_service.repository import InMemoryJobRepository
from relay_service.service import GenerationService


def _request() -> GenerationRequest:
    return GenerationRequest(
        model="mock.video.v1",
        expected_capability_revision="sha256:" + ("0" * 64),
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="wait for an available provider account"),
    )


def test_in_memory_defer_waits_without_incrementing_attempt() -> None:
    async def scenario() -> None:
        queue = InMemoryWorkQueue()
        original = WorkItem(job_id=uuid4(), attempt=7)
        await queue.enqueue(original)
        delivery = await queue.dequeue()
        assert delivery is not None

        await queue.defer(
            delivery,
            delay_seconds=0.05,
            increment_attempt=False,
        )

        assert await queue.depth() == 1
        assert await queue.dequeue() is None

        await asyncio.sleep(0.07)
        redelivery = await queue.dequeue()
        assert redelivery is not None
        assert redelivery.item.job_id == original.job_id
        assert redelivery.item.attempt == original.attempt

    asyncio.run(scenario())


class _AlwaysAdmissionBlockedRouter:
    def __init__(self, code: str, *, retry_after_seconds: float) -> None:
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        self.calls = 0

    async def submit(self, job, *, owner_token=None):
        self.calls += 1
        raise ProviderError(
            self.code,
            "No provider account can admit this task yet",
            retryable=True,
            account_unavailable=False,
            retry_after_seconds=self.retry_after_seconds,
        )


class _RecordingQueue(InMemoryWorkQueue):
    def __init__(self) -> None:
        super().__init__()
        self.deferrals: list[tuple[int, float, bool]] = []

    async def defer(
        self,
        delivery,
        *,
        delay_seconds: float,
        increment_attempt: bool = False,
    ) -> None:
        self.deferrals.append(
            (delivery.item.attempt, delay_seconds, increment_attempt)
        )
        await super().defer(
            delivery,
            delay_seconds=delay_seconds,
            increment_attempt=increment_attempt,
        )


@pytest.mark.parametrize(
    ("error_code", "public_error_code"),
    [
        ("PROVIDER_ACCOUNT_POOL_BUSY", "PROVIDER_ACCOUNT_POOL_BUSY"),
        (
            "PROVIDER_ACCOUNT_POOL_RATE_LIMITED",
            "PROVIDER_ACCOUNT_POOL_RATE_LIMITED",
        ),
    ],
)
def test_account_pool_admission_wait_does_not_consume_worker_attempts(
    error_code: str,
    public_error_code: str,
) -> None:
    async def scenario() -> None:
        repository = InMemoryJobRepository()
        queue = _RecordingQueue()
        retry_after_seconds = 0.03
        router = _AlwaysAdmissionBlockedRouter(
            error_code,
            retry_after_seconds=retry_after_seconds,
        )
        # WorkItem.attempt starts at one. Setting the maximum to one proves an
        # account-pool wait is admission backpressure, not an exhausted
        # provider attempt that should fail the generation.
        service = GenerationService(
            repository,
            queue,
            router,
            max_worker_attempts=1,
        )
        accepted = await service.submit(
            _request(),
            f"admission-wait-{error_code}",
            uuid4(),
        )

        first = await service.process_next()
        assert first is not None
        assert first.status == JobStatus.QUEUED
        assert first.error is not None
        assert first.error.code == public_error_code
        assert queue.deferrals == [(1, retry_after_seconds, False)]
        assert await queue.depth() == 1

        # The delayed delivery must not hot-loop while the account remains
        # occupied or rate-limited.
        assert await service.process_next() is None
        assert router.calls == 1

        await asyncio.sleep(0.05)
        second = await service.process_next()
        assert second is not None
        assert second.status == JobStatus.QUEUED
        assert second.error is not None
        assert second.error.code == public_error_code
        assert queue.deferrals == [
            (1, retry_after_seconds, False),
            (1, retry_after_seconds, False),
        ]
        assert router.calls == 2

        persisted = await repository.get(accepted.job_id)
        assert persisted is not None
        assert persisted.status == JobStatus.QUEUED

    asyncio.run(scenario())
