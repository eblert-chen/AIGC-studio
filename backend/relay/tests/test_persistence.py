from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from sqlalchemy import select

from relay_service.models import (
    GenerationInputs,
    GenerationMode,
    GenerationRequest,
    OutputOptions,
)
from relay_service.outbox import OutboxDispatcher
from relay_service.providers.mock import MockProviderAdapter
from relay_service.providers.router import ProviderRouter
from relay_service.queue import InMemoryWorkQueue
from relay_service.service import GenerationService
from relay_service.sql_repository import OutboxRow, SqlAlchemyJobRepository


MOCK_CAPABILITY_REVISION = asyncio.run(
    ProviderRouter([MockProviderAdapter()]).model_catalog()
).data[0].capability_revision


def request() -> GenerationRequest:
    return GenerationRequest(
        client_reference_id="persistent-001",
        model="mock.video.v1",
        expected_capability_revision=MOCK_CAPABILITY_REVISION,
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="Persist this task"),
        output=OutputOptions(),
    )


def test_sql_repository_outbox_survives_repository_restart(tmp_path) -> None:
    async def scenario() -> None:
        url = f"sqlite+aiosqlite:///{tmp_path / 'relay.db'}"
        repository = SqlAlchemyJobRepository.from_url(url)
        await repository.create_schema()
        queue = InMemoryWorkQueue()
        service = GenerationService(
            repository,
            queue,
            ProviderRouter(
                [MockProviderAdapter(account_id="persistent-account")]
            ),
        )
        dispatcher = OutboxDispatcher(repository, queue)
        tenant_id = uuid4()

        accepted = await service.submit(
            request(),
            "persistent-key",
            tenant_id,
            source_client_id="customer-platform",
        )
        replay = await service.submit(
            request(),
            "persistent-key",
            tenant_id,
            source_client_id="customer-platform",
        )

        assert replay.idempotent_replay is True
        assert replay.job_id == accepted.job_id
        assert await queue.depth() == 0
        assert await dispatcher.dispatch_once() == 1
        assert await dispatcher.dispatch_once() == 0
        assert await queue.depth() == 1

        processed = await service.process_next()
        assert processed is not None
        assert processed.status == "processing"
        await repository.dispose()

        reopened = SqlAlchemyJobRepository.from_url(url)
        persisted = await reopened.get_for_tenant(accepted.job_id, tenant_id)
        assert persisted is not None
        assert persisted.status == "processing"
        assert persisted.provider == "mock-video@persistent-account"
        assert persisted.source_client_id == "customer-platform"
        assert await reopened.healthcheck() is True
        await reopened.dispose()

    asyncio.run(scenario())


def test_failed_publish_releases_outbox_without_sensitive_error(tmp_path) -> None:
    class FailOnceQueue(InMemoryWorkQueue):
        async def enqueue(self, item):
            raise RuntimeError("redis://user:secret@example.invalid")

    async def scenario() -> None:
        url = f"sqlite+aiosqlite:///{tmp_path / 'failure.db'}"
        repository = SqlAlchemyJobRepository.from_url(url)
        await repository.create_schema()
        service = GenerationService(
            repository,
            FailOnceQueue(),
            ProviderRouter([MockProviderAdapter()]),
        )
        dispatcher = OutboxDispatcher(repository, service.queue)
        await service.submit(request(), "failed-publish", uuid4())

        assert await dispatcher.dispatch_once() == 0
        async with repository.sessions() as session:
            row = await session.scalar(select(OutboxRow))
            assert row is not None
            assert row.status == "pending"
            assert row.attempts == 1
            assert row.last_error == "RuntimeError: publish failed"
            assert "secret" not in row.last_error
        await repository.dispose()

    asyncio.run(scenario())


def test_success_callback_atomically_creates_transfer_outbox(tmp_path) -> None:
    async def scenario() -> None:
        url = f"sqlite+aiosqlite:///{tmp_path / 'transfer-outbox.db'}"
        repository = SqlAlchemyJobRepository.from_url(url)
        await repository.create_schema()
        generation_queue = InMemoryWorkQueue()
        transfer_queue = InMemoryWorkQueue()
        service = GenerationService(
            repository,
            generation_queue,
            ProviderRouter([MockProviderAdapter()]),
            transfer_queue=transfer_queue,
        )
        dispatcher = OutboxDispatcher(
            repository,
            {
                "generation.submit": generation_queue,
                "artifact.transfer": transfer_queue,
            },
        )
        accepted = await service.submit(request(), "callback-outbox", uuid4())
        assert await dispatcher.dispatch_once() == 1
        processing = await service.process_next()
        assert processing is not None
        event = {
            "event_id": "evt-persistent-success",
            "provider_task_id": processing.provider_task_id,
            "status": "succeeded",
            "outputs": [
                {
                    "url": "https://provider.example.test/result.mp4",
                    "media_type": "video",
                    "content_type": "video/mp4",
                }
            ],
        }
        receipt = await service.receive_webhook(
            "mock-video",
            json.dumps(event).encode(),
            {"x-mock-webhook-secret": "development-only-secret"},
        )
        duplicate = await service.receive_webhook(
            "mock-video",
            json.dumps(event).encode(),
            {"x-mock-webhook-secret": "development-only-secret"},
        )

        assert receipt.status == "transferring"
        assert duplicate.duplicate is True
        assert await transfer_queue.depth() == 0
        assert await dispatcher.dispatch_once() == 1
        assert await dispatcher.dispatch_once() == 0
        assert await transfer_queue.depth() == 1
        persisted = await repository.get(accepted.job_id)
        assert persisted is not None
        assert persisted.status == "transferring"
        assert len(persisted.transfer_sources) == 1
        await repository.dispose()

    asyncio.run(scenario())
