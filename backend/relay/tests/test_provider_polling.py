from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from relay_service.models import (
    GenerationInputs,
    GenerationJob,
    GenerationMode,
    GenerationRequest,
    OutputOptions,
    ProviderAsset,
    ProviderWebhookEvent,
    ProviderWebhookStatus,
    JobStatus,
    SubmissionReconciliationRequest,
)
from relay_service.providers.mock import MockProviderAdapter
from relay_service.providers.base import ProviderError
from relay_service.providers.router import ProviderRouter
from relay_service.queue import InMemoryWorkQueue
from relay_service.repository import InMemoryJobRepository
from relay_service.errors import RelayError
from relay_service.service import GenerationService
from relay_service.sql_repository import SqlAlchemyJobRepository


class PollingProvider(MockProviderAdapter):
    name = "polling-provider"

    def __init__(self) -> None:
        super().__init__()
        self.event: ProviderWebhookEvent | None = None

    async def poll(self, job):
        return self.event


POLLING_CAPABILITY_REVISION = asyncio.run(
    ProviderRouter([PollingProvider()]).model_catalog()
).data[0].capability_revision


@pytest.mark.asyncio
async def test_polling_updates_progress_deduplicates_and_starts_transfer() -> None:
    repository = InMemoryJobRepository()
    generation_queue = InMemoryWorkQueue()
    transfer_queue = InMemoryWorkQueue()
    provider = PollingProvider()
    service = GenerationService(
        repository,
        generation_queue,
        ProviderRouter([provider]),
        transfer_queue=transfer_queue,
    )
    accepted = await service.submit(
        GenerationRequest(
            model="mock.video.v1",
            expected_capability_revision=POLLING_CAPABILITY_REVISION,
            mode=GenerationMode.TEXT_TO_VIDEO,
            inputs=GenerationInputs(prompt="ocean at night"),
            output=OutputOptions(),
        ),
        "polling-test-key",
        uuid4(),
    )
    submitted = await service.process_next()
    assert submitted is not None
    assert submitted.provider_task_id is not None

    provider.event = ProviderWebhookEvent(
        event_id="poll-processing-50",
        provider_task_id=submitted.provider_task_id,
        status=ProviderWebhookStatus.PROCESSING,
        progress=50,
    )
    first = await service.poll_provider_jobs()
    second = await service.poll_provider_jobs()
    assert first.scanned == 1
    assert first.events == 1
    assert first.duplicates == 0
    assert second.duplicates == 1
    processing = await repository.get(accepted.job_id)
    assert processing is not None
    assert processing.progress == 50

    provider.event = ProviderWebhookEvent(
        event_id="poll-succeeded",
        provider_task_id=submitted.provider_task_id,
        status=ProviderWebhookStatus.SUCCEEDED,
        progress=100,
        outputs=[
            ProviderAsset(
                url="https://provider.example/output.mp4",
                media_type="video",
                content_type="video/mp4",
            )
        ],
    )
    completed = await service.poll_provider_jobs()
    assert completed.events == 1
    transferring = await repository.get(accepted.job_id)
    assert transferring is not None
    assert transferring.status == "transferring"
    assert len(transferring.transfer_sources) == 1
    assert await transfer_queue.depth() == 1


@pytest.mark.asyncio
async def test_polling_quarantines_a_mismatched_provider_task() -> None:
    repository = InMemoryJobRepository()
    queue = InMemoryWorkQueue()
    provider = PollingProvider()
    service = GenerationService(
        repository,
        queue,
        ProviderRouter([provider]),
        transfer_queue=InMemoryWorkQueue(),
    )
    accepted = await service.submit(
        GenerationRequest(
            model="mock.video.v1",
            expected_capability_revision=POLLING_CAPABILITY_REVISION,
            mode=GenerationMode.TEXT_TO_VIDEO,
            inputs=GenerationInputs(prompt="forest"),
        ),
        "polling-failure-key",
        uuid4(),
    )
    submitted = await service.process_next()
    assert submitted is not None
    provider.event = ProviderWebhookEvent(
        event_id="wrong-task",
        provider_task_id="a-different-task",
        status=ProviderWebhookStatus.PROCESSING,
    )

    summary = await service.poll_provider_jobs()

    assert summary.scanned == 1
    assert summary.events == 0
    assert summary.failures == 1
    quarantined = await repository.get(accepted.job_id)
    assert quarantined is not None
    assert quarantined.status == JobStatus.RECONCILIATION_REQUIRED
    assert quarantined.provider == submitted.provider
    assert quarantined.provider_task_id == submitted.provider_task_id
    assert quarantined.error is not None
    assert quarantined.error.code == "PROVIDER_POLL_RECONCILIATION_REQUIRED"
    assert quarantined.error.details == {}
    assert quarantined.provider_last_poll_error == "PROVIDER_TASK_MISMATCH"


@pytest.mark.asyncio
async def test_polling_cursor_prevents_a_full_duplicate_batch_from_starving_jobs() -> None:
    repository = InMemoryJobRepository()
    queue = InMemoryWorkQueue()
    provider = PollingProvider()
    polled: list[str] = []

    async def record(job):
        assert job.provider_task_id is not None
        polled.append(job.provider_task_id)
        return None

    provider.poll = record  # type: ignore[method-assign]
    service = GenerationService(
        repository,
        queue,
        ProviderRouter([provider]),
        transfer_queue=InMemoryWorkQueue(),
    )
    task_ids = set()
    for index in range(3):
        await service.submit(
            GenerationRequest(
                model="mock.video.v1",
                expected_capability_revision=POLLING_CAPABILITY_REVISION,
                mode=GenerationMode.TEXT_TO_VIDEO,
                inputs=GenerationInputs(prompt=f"scene {index}"),
            ),
            f"round-robin-key-{index}",
            uuid4(),
        )
        submitted = await service.process_next()
        assert submitted is not None and submitted.provider_task_id is not None
        task_ids.add(submitted.provider_task_id)

    await service.poll_provider_jobs(limit=2)
    await service.poll_provider_jobs(limit=2)

    assert set(polled) == task_ids


@pytest.mark.asyncio
async def test_repository_merge_prevents_stale_progress_regression() -> None:
    repository = InMemoryJobRepository()
    task = GenerationJob(
        tenant_id=uuid4(),
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="city"),
        output=OutputOptions(),
        status=JobStatus.PROCESSING,
        provider="polling-provider",
        provider_task_id="provider-task",
        progress=1,
    )
    await repository.create_idempotent(task, "merge-key", "hash")
    high = task.model_copy(deep=True, update={"progress": 80})
    stale = task.model_copy(deep=True, update={"progress": 50})

    await repository.apply_webhook_event(
        high, "polling-provider", "progress-80"
    )
    await repository.apply_webhook_event(
        stale, "polling-provider", "progress-50"
    )

    persisted = await repository.get(task.id)
    assert persisted is not None
    assert persisted.progress == 80


@pytest.mark.asyncio
async def test_sql_processing_cursor_and_stale_merge(tmp_path) -> None:
    repository = SqlAlchemyJobRepository.from_url(
        f"sqlite+aiosqlite:///{tmp_path / 'provider-polling.db'}"
    )
    await repository.create_schema()
    jobs = []
    for index in range(3):
        task = GenerationJob(
            tenant_id=uuid4(),
            model="mock.video.v1",
            mode=GenerationMode.TEXT_TO_VIDEO,
            inputs=GenerationInputs(prompt=f"sql scene {index}"),
            output=OutputOptions(),
            status=JobStatus.PROCESSING,
            provider="polling-provider",
            provider_task_id=f"sql-provider-task-{index}",
            progress=1,
        )
        await repository.create_idempotent(
            task, f"sql-merge-key-{index}", f"hash-{index}"
        )
        jobs.append(task)

    first = await repository.list_processing_jobs(limit=2)
    second = await repository.list_processing_jobs(
        limit=2, cursor=first[-1].id
    )
    assert {job.id for job in (*first, *second)} == {
        job.id for job in jobs
    }

    target = jobs[0]
    high = target.model_copy(deep=True, update={"progress": 90})
    stale = target.model_copy(deep=True, update={"progress": 40})
    await repository.apply_webhook_event(
        high, "polling-provider", "sql-progress-90"
    )
    await repository.apply_webhook_event(
        stale, "polling-provider", "sql-progress-40"
    )
    persisted = await repository.get(target.id)
    assert persisted is not None
    assert persisted.progress == 90
    await repository.dispose()


@pytest.mark.asyncio
async def test_transient_poll_failure_is_persisted_with_backoff() -> None:
    repository = InMemoryJobRepository()
    queue = InMemoryWorkQueue()
    provider = PollingProvider()

    async def unavailable(job):
        raise ProviderError(
            "PROVIDER_QUERY_UNAVAILABLE",
            "query unavailable",
            retryable=True,
        )

    provider.poll = unavailable  # type: ignore[method-assign]
    service = GenerationService(
        repository,
        queue,
        ProviderRouter([provider]),
        transfer_queue=InMemoryWorkQueue(),
        provider_poll_error_base_seconds=60,
        provider_poll_error_max_seconds=120,
    )
    accepted = await service.submit(
        GenerationRequest(
            model="mock.video.v1",
            expected_capability_revision=POLLING_CAPABILITY_REVISION,
            mode=GenerationMode.TEXT_TO_VIDEO,
            inputs=GenerationInputs(prompt="rain"),
        ),
        "backoff-test-key",
        uuid4(),
    )
    await service.process_next()

    failed_poll = await service.poll_provider_jobs()
    immediate_retry = await service.poll_provider_jobs()

    assert failed_poll.failures == 1
    assert immediate_retry.scanned == 0
    persisted = await repository.get(accepted.job_id)
    assert persisted is not None
    assert persisted.provider_poll_failures == 1
    assert persisted.provider_last_poll_error == "PROVIDER_QUERY_UNAVAILABLE"
    assert persisted.provider_next_poll_at is not None


@pytest.mark.asyncio
async def test_task_terminal_poll_error_becomes_stable_failed_event() -> None:
    repository = InMemoryJobRepository()
    queue = InMemoryWorkQueue()
    provider = PollingProvider()

    async def invalid_task(job):
        raise ProviderError(
            "PROVIDER_TASK_ID_INVALID",
            "invalid task identifier",
            retryable=False,
            fail_job=True,
        )

    provider.poll = invalid_task  # type: ignore[method-assign]
    service = GenerationService(
        repository,
        queue,
        ProviderRouter([provider]),
        transfer_queue=InMemoryWorkQueue(),
    )
    accepted = await service.submit(
        GenerationRequest(
            model="mock.video.v1",
            expected_capability_revision=POLLING_CAPABILITY_REVISION,
            mode=GenerationMode.TEXT_TO_VIDEO,
            inputs=GenerationInputs(prompt="snow"),
        ),
        "terminal-poll-key",
        uuid4(),
    )
    await service.process_next()

    summary = await service.poll_provider_jobs()

    assert summary.events == 1
    failed = await repository.get(accepted.job_id)
    assert failed is not None and failed.error is not None
    assert failed.status == JobStatus.FAILED
    assert failed.error.code == "PROVIDER_TASK_ID_INVALID"


@pytest.mark.asyncio
async def test_nonretryable_uncertain_poll_error_requires_reconciliation() -> None:
    repository = InMemoryJobRepository()
    provider = PollingProvider()

    async def uncertain(job):
        raise ProviderError(
            "PROVIDER_TASK_QUERY_UNSUPPORTED",
            "provider cannot safely query this task",
            retryable=False,
            fail_job=False,
        )

    provider.poll = uncertain  # type: ignore[method-assign]
    job = GenerationJob(
        tenant_id=uuid4(),
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="preserve upstream task identity"),
        output=OutputOptions(),
        callback_url="https://platform.example.test/relay-callback",
        status=JobStatus.PROCESSING,
        progress=37,
        provider=provider.route_id,
        provider_task_id="paid-upstream-task-123",
        provider_poll_failures=2,
    )
    await repository.create_idempotent(job, "uncertain-poll-key", "hash")
    service = GenerationService(
        repository,
        InMemoryWorkQueue(),
        ProviderRouter([provider]),
        transfer_queue=InMemoryWorkQueue(),
    )

    summary = await service.poll_provider_jobs()
    repeated = await service.poll_provider_jobs()

    assert summary.scanned == 1
    assert summary.events == 0
    assert summary.failures == 1
    assert repeated.scanned == 0
    persisted = await repository.get(job.id)
    assert persisted is not None
    assert persisted.status == JobStatus.RECONCILIATION_REQUIRED
    assert persisted.progress == 37
    assert persisted.provider == provider.route_id
    assert persisted.provider_task_id == "paid-upstream-task-123"
    assert persisted.provider_poll_failures == 2
    assert persisted.provider_next_poll_at is None
    assert persisted.provider_last_poll_error == (
        "PROVIDER_TASK_QUERY_UNSUPPORTED"
    )
    assert persisted.error is not None
    assert persisted.error.code == "PROVIDER_POLL_RECONCILIATION_REQUIRED"
    assert persisted.error.retryable is False
    assert persisted.error.details == {}
    deliveries = await repository.list_callback_deliveries(job.tenant_id)
    assert len(deliveries) == 1
    assert deliveries[0].job_status == JobStatus.RECONCILIATION_REQUIRED


@pytest.mark.asyncio
async def test_poll_reconciliation_preserves_known_provider_task() -> None:
    repository = InMemoryJobRepository()
    provider = PollingProvider()

    async def uncertain(job):
        raise ProviderError(
            "PROVIDER_TASK_QUERY_UNSUPPORTED",
            "provider cannot safely query this task",
            retryable=False,
            fail_job=False,
        )

    provider.poll = uncertain  # type: ignore[method-assign]
    tenant_id = uuid4()
    job = GenerationJob(
        tenant_id=tenant_id,
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="do not refund a known provider task"),
        output=OutputOptions(),
        status=JobStatus.PROCESSING,
        provider=provider.route_id,
        provider_task_id="paid-upstream-task-456",
    )
    await repository.create_idempotent(job, "poll-reconciliation-guard", "hash")
    service = GenerationService(
        repository,
        InMemoryWorkQueue(),
        ProviderRouter([provider]),
        transfer_queue=InMemoryWorkQueue(),
    )

    await service.poll_provider_jobs()

    with pytest.raises(RelayError) as captured:
        await service.resolve_submission_reconciliation(
            job.id,
            tenant_id,
            SubmissionReconciliationRequest(outcome="not_created"),
        )

    assert captured.value.code == "RECONCILIATION_OUTCOME_NOT_ALLOWED"
    assert captured.value.status_code == 409
    persisted = await repository.get(job.id)
    assert persisted is not None
    assert persisted.status == JobStatus.RECONCILIATION_REQUIRED
    assert persisted.provider == provider.route_id
    assert persisted.provider_task_id == "paid-upstream-task-456"
    assert persisted.error is not None
    assert persisted.error.code == "PROVIDER_POLL_RECONCILIATION_REQUIRED"

    with pytest.raises(RelayError) as mismatch:
        await service.resolve_submission_reconciliation(
            job.id,
            tenant_id,
            SubmissionReconciliationRequest(
                outcome="created",
                provider_task_id="different-provider-task",
            ),
        )

    assert mismatch.value.code == "PROVIDER_TASK_ID_MISMATCH"
    assert mismatch.value.status_code == 409
    unchanged = await repository.get(job.id)
    assert unchanged is not None
    assert unchanged.status == JobStatus.RECONCILIATION_REQUIRED
    assert unchanged.provider_task_id == "paid-upstream-task-456"

    resumed = await service.resolve_submission_reconciliation(
        job.id,
        tenant_id,
        SubmissionReconciliationRequest(
            outcome="created",
            provider_task_id="paid-upstream-task-456",
        ),
    )
    assert resumed.status == JobStatus.PROCESSING
    assert resumed.provider == provider.route_id
    assert resumed.provider_task_id == "paid-upstream-task-456"
    assert resumed.error is None


@pytest.mark.asyncio
async def test_polling_uses_bounded_concurrency() -> None:
    repository = InMemoryJobRepository()
    queue = InMemoryWorkQueue()
    provider = PollingProvider()
    active = 0
    maximum_active = 0

    async def observe(job):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return None

    provider.poll = observe  # type: ignore[method-assign]
    service = GenerationService(
        repository,
        queue,
        ProviderRouter([provider]),
        transfer_queue=InMemoryWorkQueue(),
        provider_poll_concurrency=2,
    )
    for index in range(4):
        await service.submit(
            GenerationRequest(
                model="mock.video.v1",
                expected_capability_revision=POLLING_CAPABILITY_REVISION,
                mode=GenerationMode.TEXT_TO_VIDEO,
                inputs=GenerationInputs(prompt=f"cloud {index}"),
            ),
            f"concurrency-key-{index}",
            uuid4(),
        )
        await service.process_next()

    await service.poll_provider_jobs(limit=4)

    assert maximum_active == 2
