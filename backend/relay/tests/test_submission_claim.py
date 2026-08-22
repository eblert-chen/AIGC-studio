from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from relay_service.models import (
    GenerationInputs,
    GenerationJob,
    GenerationMode,
    GenerationRequest,
    JobStatus,
    WorkItem,
)
from relay_service.providers.base import ProviderSubmission
from relay_service.providers.mock import MockProviderAdapter
from relay_service.providers.router import ProviderRouter
from relay_service.queue import InMemoryWorkQueue
from relay_service.repository import InMemoryJobRepository
from relay_service.service import GenerationService
from relay_service.sql_repository import JobRow, SqlAlchemyJobRepository


MOCK_CAPABILITY_REVISION = asyncio.run(
    ProviderRouter([MockProviderAdapter()]).model_catalog()
).data[0].capability_revision


def request() -> GenerationRequest:
    return GenerationRequest(
        model="mock.video.v1",
        expected_capability_revision=MOCK_CAPABILITY_REVISION,
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="submission claim test"),
    )


class BlockingCountingProvider(MockProviderAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def submit(self, job: GenerationJob) -> ProviderSubmission:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return await super().submit(job)


class HealthDiscoveryFailsOnce(MockProviderAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.health_calls = 0
        self.submit_calls = 0

    async def healthcheck(self) -> bool:
        self.health_calls += 1
        if self.health_calls == 1:
            raise RuntimeError("provider health discovery failed")
        return True

    async def submit(self, job: GenerationJob) -> ProviderSubmission:
        self.submit_calls += 1
        return await super().submit(job)


class CapabilityDiscoveryAlwaysFails(MockProviderAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.capability_calls = 0
        self.submit_calls = 0

    async def capabilities(self):
        self.capability_calls += 1
        raise RuntimeError("provider capability discovery failed")

    async def submit(self, job: GenerationJob) -> ProviderSubmission:
        self.submit_calls += 1
        return await super().submit(job)


class AmbiguousPostProvider(MockProviderAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.submit_calls = 0

    async def submit(self, job: GenerationJob) -> ProviderSubmission:
        self.submit_calls += 1
        raise RuntimeError("connection failed after provider POST")


def test_concurrent_deliveries_submit_to_provider_once() -> None:
    async def scenario() -> None:
        repository = InMemoryJobRepository()
        queue = InMemoryWorkQueue()
        provider = BlockingCountingProvider()
        service = GenerationService(
            repository,
            queue,
            ProviderRouter([provider]),
            submission_claim_lease_seconds=0.06,
        )
        accepted = await service.submit(
            request(), "claim-concurrency", uuid4()
        )
        await queue.enqueue(WorkItem(job_id=accepted.job_id))

        owner = asyncio.create_task(service.process_next())
        await asyncio.wait_for(provider.entered.wait(), timeout=1)
        await asyncio.sleep(0.16)
        duplicate = await service.process_next()
        assert duplicate is not None
        assert duplicate.status == JobStatus.SUBMITTING
        assert provider.calls == 1

        provider.release.set()
        completed = await owner
        assert completed is not None
        assert completed.status == JobStatus.PROCESSING
        assert provider.calls == 1

        persisted = await repository.get(accepted.job_id)
        assert persisted is not None
        assert persisted.status == JobStatus.PROCESSING

    asyncio.run(scenario())


def test_lost_heartbeat_requires_reconciliation_without_requeue() -> None:
    class LosingRenewalRepository(InMemoryJobRepository):
        async def renew_submission_claim(self, job_id, *, token, lease):
            return False

    async def scenario() -> None:
        repository = LosingRenewalRepository()
        queue = InMemoryWorkQueue()
        provider = BlockingCountingProvider()
        service = GenerationService(
            repository,
            queue,
            ProviderRouter([provider]),
            submission_claim_lease_seconds=0.03,
        )
        accepted = await service.submit(
            request(), "claim-heartbeat-lost", uuid4()
        )

        owner = asyncio.create_task(service.process_next())
        await asyncio.wait_for(provider.entered.wait(), timeout=1)
        result = await asyncio.wait_for(owner, timeout=1)

        persisted = await repository.get(accepted.job_id)
        assert result is not None
        assert result.status == JobStatus.RECONCILIATION_REQUIRED
        assert persisted is not None
        assert persisted.status == JobStatus.RECONCILIATION_REQUIRED
        assert accepted.job_id not in repository._submission_claims
        assert await queue.depth() == 0
        assert provider.calls == 1

    asyncio.run(scenario())


def test_pre_submission_health_failure_requeues_without_reconciliation() -> None:
    async def scenario() -> None:
        repository = InMemoryJobRepository()
        queue = InMemoryWorkQueue()
        provider = HealthDiscoveryFailsOnce()
        service = GenerationService(
            repository,
            queue,
            ProviderRouter([provider]),
            max_worker_attempts=2,
        )
        accepted = await service.submit(
            request(), "health-discovery-retry", uuid4()
        )

        first_attempt = await service.process_next()
        assert first_attempt is not None
        assert first_attempt.status == JobStatus.QUEUED

        retryable = await repository.get(accepted.job_id)
        assert retryable is not None
        assert retryable.status == JobStatus.QUEUED
        assert accepted.job_id not in repository._submission_claims
        assert await queue.depth() == 1
        assert provider.submit_calls == 0

        completed = await service.process_next()
        assert completed is not None
        assert completed.status == JobStatus.PROCESSING
        assert provider.submit_calls == 1
        assert await queue.depth() == 0

    asyncio.run(scenario())


def test_pre_submission_capability_failure_is_bounded_without_reconciliation() -> None:
    async def scenario() -> None:
        repository = InMemoryJobRepository()
        queue = InMemoryWorkQueue()
        provider = CapabilityDiscoveryAlwaysFails()
        service = GenerationService(
            repository,
            queue,
            ProviderRouter([provider]),
            max_worker_attempts=2,
        )
        accepted = await service.submit(
            request(), "capability-discovery-bounded", uuid4()
        )

        with pytest.raises(
            RuntimeError, match="could not declare capabilities"
        ):
            await service.process_next()

        retryable = await repository.get(accepted.job_id)
        assert retryable is not None
        assert retryable.status == JobStatus.QUEUED
        assert await queue.depth() == 1

        failed = await service.process_next()
        assert failed is not None
        assert failed.status == JobStatus.FAILED
        assert failed.error is not None
        assert failed.error.code == "WORKER_ATTEMPTS_EXHAUSTED"
        assert provider.submit_calls == 0
        assert provider.capability_calls == 2
        assert await queue.depth() == 0

    asyncio.run(scenario())


def test_ambiguous_provider_post_is_never_requeued() -> None:
    async def scenario() -> None:
        repository = InMemoryJobRepository()
        queue = InMemoryWorkQueue()
        provider = AmbiguousPostProvider()
        service = GenerationService(
            repository,
            queue,
            ProviderRouter([provider]),
            max_worker_attempts=3,
        )
        accepted = await service.submit(
            request(), "ambiguous-provider-post", uuid4()
        )

        quarantined = await service.process_next()

        assert quarantined is not None
        assert quarantined.status == JobStatus.RECONCILIATION_REQUIRED
        assert quarantined.error is not None
        assert quarantined.error.code == "SUBMISSION_RECONCILIATION_REQUIRED"
        assert provider.submit_calls == 1
        assert await queue.depth() == 0
        persisted = await repository.get(accepted.job_id)
        assert persisted is not None
        assert persisted.status == JobStatus.RECONCILIATION_REQUIRED

    asyncio.run(scenario())


def test_sql_pre_submission_failure_atomically_requeues(tmp_path) -> None:
    async def scenario() -> None:
        url = f"sqlite+aiosqlite:///{tmp_path / 'safe-submit-retry.db'}"
        repository = SqlAlchemyJobRepository.from_url(url)
        await repository.create_schema()
        queue = InMemoryWorkQueue()
        provider = HealthDiscoveryFailsOnce()
        service = GenerationService(
            repository,
            queue,
            ProviderRouter([provider]),
            max_worker_attempts=2,
        )
        accepted = await service.submit(
            request(), "sql-health-discovery-retry", uuid4()
        )
        await queue.enqueue(WorkItem(job_id=accepted.job_id))

        first_attempt = await service.process_next()
        assert first_attempt is not None
        assert first_attempt.status == JobStatus.QUEUED

        retryable = await repository.get(accepted.job_id)
        assert retryable is not None
        assert retryable.status == JobStatus.QUEUED
        async with repository.sessions() as session:
            row = await session.scalar(
                select(JobRow).where(JobRow.id == str(accepted.job_id))
            )
            assert row is not None
            assert row.status == JobStatus.QUEUED.value
            assert row.submission_claim_token is None
            assert row.submission_claim_expires_at is None
        assert await queue.depth() == 1

        completed = await service.process_next()
        assert completed is not None
        assert completed.status == JobStatus.PROCESSING
        assert provider.submit_calls == 1
        assert await queue.depth() == 0
        await repository.dispose()

    asyncio.run(scenario())


def test_sql_expired_claim_rejects_stale_owner(tmp_path) -> None:
    async def scenario() -> None:
        url = f"sqlite+aiosqlite:///{tmp_path / 'submission-claim.db'}"
        repository = SqlAlchemyJobRepository.from_url(url)
        await repository.create_schema()
        service = GenerationService(
            repository,
            InMemoryWorkQueue(),
            ProviderRouter([MockProviderAdapter()]),
        )
        accepted = await service.submit(
            request(), "claim-lease", uuid4()
        )

        first = await repository.claim_submission(
            accepted.job_id, lease=timedelta(milliseconds=200)
        )
        assert first is not None
        assert not await repository.renew_submission_claim(
            accepted.job_id, token=uuid4(), lease=timedelta(milliseconds=200)
        )
        assert (
            await repository.claim_submission(
                accepted.job_id, lease=timedelta(seconds=1)
            )
            is None
        )

        await asyncio.sleep(0.12)
        assert await repository.renew_submission_claim(
            accepted.job_id,
            token=first.token,
            lease=timedelta(milliseconds=200),
        )
        await asyncio.sleep(0.12)
        assert (
            await repository.claim_submission(
                accepted.job_id, lease=timedelta(seconds=1)
            )
            is None
        )
        await asyncio.sleep(0.2)
        replacement = await repository.claim_submission(
            accepted.job_id, lease=timedelta(seconds=1)
        )
        assert replacement is None

        first.job.status = JobStatus.PROCESSING
        first.job.provider = "stale-provider"
        first.job.provider_task_id = "stale-task"
        assert (
            await repository.finish_submission(
                first.job, token=first.token
            )
            is False
        )

        persisted = await repository.get(accepted.job_id)
        assert persisted is not None
        assert persisted.status == JobStatus.RECONCILIATION_REQUIRED
        assert persisted.provider is None
        assert persisted.error is not None
        assert persisted.error.code == "SUBMISSION_RECONCILIATION_REQUIRED"
        async with repository.sessions() as session:
            row = await session.scalar(
                select(JobRow).where(JobRow.id == str(accepted.job_id))
            )
            assert row is not None
            assert row.submission_claim_token is None
            assert row.submission_claim_expires_at is None
        await repository.dispose()

    asyncio.run(scenario())


def test_provider_return_then_finish_failure_keeps_claim_and_delivery() -> None:
    class FinishFailsRepository(InMemoryJobRepository):
        async def finish_submission(self, job, *, token):
            raise RuntimeError("database write failed")

    async def scenario() -> None:
        repository = FinishFailsRepository()
        queue = InMemoryWorkQueue()
        provider = MockProviderAdapter()
        service = GenerationService(
            repository,
            queue,
            ProviderRouter([provider]),
            submission_claim_lease_seconds=0.03,
        )
        accepted = await service.submit(
            request(), "claim-finish-failure", uuid4()
        )

        with pytest.raises(RuntimeError, match="database write failed"):
            await service.process_next()

        persisted = await repository.get(accepted.job_id)
        assert persisted is not None
        assert persisted.status == JobStatus.SUBMITTING
        assert accepted.job_id in repository._submission_claims
        assert await queue.depth() == 1

        await asyncio.sleep(0.05)
        # A durable queue will redeliver after its visibility lease. Trigger the
        # repository fence directly because the in-memory test queue does not
        # implement visibility-timeout reclaim.
        assert (
            await repository.claim_submission(
                accepted.job_id, lease=timedelta(seconds=1)
            )
            is None
        )
        quarantined = await repository.get(accepted.job_id)
        assert quarantined is not None
        assert quarantined.status == JobStatus.RECONCILIATION_REQUIRED
        assert accepted.job_id not in repository._submission_claims
        assert await queue.depth() == 1

    asyncio.run(scenario())
