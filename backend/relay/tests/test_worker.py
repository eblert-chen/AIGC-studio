from __future__ import annotations

import asyncio
from uuid import uuid4

from relay_service.models import (
    GenerationInputs,
    GenerationMode,
    GenerationRequest,
)
from relay_service.providers.mock import MockProviderAdapter
from relay_service.providers.router import ProviderRouter
from relay_service.queue import InMemoryWorkQueue
from relay_service.repository import InMemoryJobRepository
from relay_service.service import GenerationService
from relay_service.worker import consume


MOCK_CAPABILITY_REVISION = asyncio.run(
    ProviderRouter([MockProviderAdapter()]).model_catalog()
).data[0].capability_revision


def request() -> GenerationRequest:
    return GenerationRequest(
        model="mock.video.v1",
        expected_capability_revision=MOCK_CAPABILITY_REVISION,
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="worker test"),
    )


def test_retryable_delivery_is_bounded_and_acknowledged() -> None:
    async def scenario() -> None:
        repository = InMemoryJobRepository()
        queue = InMemoryWorkQueue()
        provider = MockProviderAdapter(fail_submit=True)
        service = GenerationService(
            repository,
            queue,
            ProviderRouter([provider]),
            max_worker_attempts=2,
        )
        accepted = await service.submit(request(), "retry-key", uuid4())

        first = await service.process_next()
        assert first is not None
        assert first.status == "queued"
        assert first.error is not None
        assert first.error.retryable is True
        assert await queue.depth() == 1

        final = await service.process_next()
        assert final is not None
        assert final.status == "failed"
        assert final.error is not None
        assert final.error.code == "PROVIDER_RETRIES_EXHAUSTED"
        assert await queue.depth() == 0
        persisted = await repository.get(accepted.job_id)
        assert persisted is not None
        assert persisted.status == "failed"

    asyncio.run(scenario())


def test_worker_loop_stops_gracefully() -> None:
    async def scenario() -> None:
        repository = InMemoryJobRepository()
        queue = InMemoryWorkQueue()
        service = GenerationService(
            repository,
            queue,
            ProviderRouter([MockProviderAdapter()]),
        )
        accepted = await service.submit(request(), "worker-loop", uuid4())
        stop = asyncio.Event()

        async def stop_after_processing() -> None:
            for _ in range(100):
                job = await repository.get(accepted.job_id)
                if job is not None and job.status == "processing":
                    stop.set()
                    return
                await asyncio.sleep(0.001)
            raise AssertionError("worker did not process the queued job")

        await asyncio.gather(
            consume(service, stop, idle_seconds=0.001),
            stop_after_processing(),
        )

    asyncio.run(scenario())
