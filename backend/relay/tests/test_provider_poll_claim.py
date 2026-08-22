from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect

from relay_service.config import RelaySettings
from relay_service.models import (
    GenerationInputs,
    GenerationJob,
    GenerationMode,
    JobStatus,
    OutputOptions,
    WorkItem,
)
from relay_service.providers.mock import MockProviderAdapter
from relay_service.providers.router import ProviderRouter
from relay_service.queue import InMemoryWorkQueue
from relay_service.repository import InMemoryJobRepository
from relay_service.service import GenerationService
from relay_service.sql_repository import SqlAlchemyJobRepository


class BlockingPollingProvider(MockProviderAdapter):
    name = "poll-claim-provider"

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def poll(self, job):
        del job
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return None


def _processing_job(provider_route: str = "poll-claim-provider:mock:default"):
    return GenerationJob(
        tenant_id=uuid4(),
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="fenced provider polling"),
        output=OutputOptions(),
        status=JobStatus.PROCESSING,
        progress=25,
        provider=provider_route,
        provider_task_id=f"upstream-{uuid4()}",
    )


def test_claim_lease_settings_are_loaded_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("RELAY_ENVIRONMENT", "development")
    monkeypatch.setenv("RELAY_RUNTIME_MODE", "memory")
    monkeypatch.setenv("RELAY_ARTIFACT_STORE", "memory")
    monkeypatch.setenv("RELAY_CALLBACK_ROUTES_JSON", "")
    monkeypatch.setenv("RELAY_PROVIDER_POLL_CLAIM_LEASE_SECONDS", "45.5")
    monkeypatch.setenv("RELAY_ARTIFACT_TRANSFER_CLAIM_LEASE_SECONDS", "240.5")

    settings = RelaySettings.from_environment()

    assert settings.provider_poll_claim_lease_seconds == 45.5
    assert settings.artifact_transfer_claim_lease_seconds == 240.5


@pytest.mark.parametrize(
    ("field_name", "message"),
    [
        (
            "provider_poll_claim_lease_seconds",
            "RELAY_PROVIDER_POLL_CLAIM_LEASE_SECONDS",
        ),
        (
            "artifact_transfer_claim_lease_seconds",
            "RELAY_ARTIFACT_TRANSFER_CLAIM_LEASE_SECONDS",
        ),
    ],
)
def test_claim_lease_settings_must_be_positive(field_name: str, message: str) -> None:
    settings = RelaySettings(**{field_name: 0})

    with pytest.raises(RuntimeError, match=message):
        settings.validate()


@pytest.mark.asyncio
async def test_memory_expired_poll_claim_rejects_every_stale_write() -> None:
    repository = InMemoryJobRepository()
    job = _processing_job()
    await repository.create_idempotent(job, "memory-poll-claim", "hash")

    stale = (
        await repository.claim_processing_jobs(limit=1, lease=timedelta(milliseconds=1))
    )[0]
    await asyncio.sleep(0.01)
    current = (
        await repository.claim_processing_jobs(limit=1, lease=timedelta(seconds=30))
    )[0]
    assert current.token != stale.token

    stale.job.progress = 90
    assert (
        await repository.apply_webhook_event(
            stale.job,
            stale.job.provider or "",
            "stale-progress-event",
            poll_token=stale.token,
        )
        is None
    )
    assert (
        await repository.begin_artifact_transfer(
            stale.job,
            stale.job.provider or "",
            "stale-transfer-event",
            WorkItem(job_id=stale.job.id),
            poll_token=stale.token,
        )
        is None
    )
    stale.job.status = JobStatus.RECONCILIATION_REQUIRED
    assert not await repository.finish_provider_poll(stale.job, token=stale.token)
    assert not await repository.record_provider_poll_failure(
        stale.job.id,
        token=stale.token,
        error_code="STALE_POLLER",
        retry_delay=timedelta(seconds=30),
    )

    assert await repository.record_provider_poll_success(
        current.job.id, token=current.token
    )
    persisted = await repository.get(job.id)
    assert persisted is not None
    assert persisted.status == JobStatus.PROCESSING
    assert persisted.progress == 25
    assert persisted.provider_poll_failures == 0


@pytest.mark.asyncio
async def test_sql_expired_poll_claim_rejects_every_stale_write(tmp_path) -> None:
    repository = SqlAlchemyJobRepository.from_url(
        f"sqlite+aiosqlite:///{tmp_path / 'provider-poll-claim.db'}"
    )
    await repository.create_schema()
    try:
        job = _processing_job()
        await repository.create_idempotent(job, "sql-poll-claim", "hash")
        stale = (
            await repository.claim_processing_jobs(
                limit=1, lease=timedelta(milliseconds=1)
            )
        )[0]
        await asyncio.sleep(0.01)
        current = (
            await repository.claim_processing_jobs(limit=1, lease=timedelta(seconds=30))
        )[0]
        assert current.token != stale.token

        stale.job.progress = 90
        assert (
            await repository.apply_webhook_event(
                stale.job,
                stale.job.provider or "",
                "stale-sql-progress-event",
                poll_token=stale.token,
            )
            is None
        )
        assert (
            await repository.begin_artifact_transfer(
                stale.job,
                stale.job.provider or "",
                "stale-sql-transfer-event",
                WorkItem(job_id=stale.job.id),
                poll_token=stale.token,
            )
            is None
        )
        stale.job.status = JobStatus.RECONCILIATION_REQUIRED
        assert not await repository.finish_provider_poll(stale.job, token=stale.token)
        assert not await repository.record_provider_poll_failure(
            stale.job.id,
            token=stale.token,
            error_code="STALE_POLLER",
            retry_delay=timedelta(seconds=30),
        )

        assert await repository.record_provider_poll_success(
            current.job.id, token=current.token
        )
        persisted = await repository.get(job.id)
        assert persisted is not None
        assert persisted.status == JobStatus.PROCESSING
        assert persisted.progress == 25
        assert persisted.provider_poll_failures == 0
    finally:
        await repository.dispose()


@pytest.mark.asyncio
async def test_two_memory_pollers_call_provider_only_once() -> None:
    repository = InMemoryJobRepository()
    provider = BlockingPollingProvider()
    job = _processing_job(provider.route_id)
    await repository.create_idempotent(job, "duplicate-memory-poll", "hash")
    first_service = GenerationService(
        repository,
        InMemoryWorkQueue(),
        ProviderRouter([provider]),
        transfer_queue=InMemoryWorkQueue(),
    )
    second_service = GenerationService(
        repository,
        InMemoryWorkQueue(),
        ProviderRouter([provider]),
        transfer_queue=InMemoryWorkQueue(),
    )

    active = asyncio.create_task(first_service.poll_provider_jobs())
    await asyncio.wait_for(provider.entered.wait(), timeout=1)
    duplicate = await asyncio.wait_for(second_service.poll_provider_jobs(), timeout=1)
    assert duplicate.scanned == 0
    assert provider.calls == 1

    provider.release.set()
    completed = await asyncio.wait_for(active, timeout=1)
    assert completed.scanned == 1
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_poll_heartbeat_keeps_claim_during_slow_provider_call() -> None:
    repository = InMemoryJobRepository()
    provider = BlockingPollingProvider()
    job = _processing_job(provider.route_id)
    await repository.create_idempotent(job, "slow-memory-poll", "hash")
    first_service = GenerationService(
        repository,
        InMemoryWorkQueue(),
        ProviderRouter([provider]),
        transfer_queue=InMemoryWorkQueue(),
        provider_poll_claim_lease_seconds=0.06,
    )
    second_service = GenerationService(
        repository,
        InMemoryWorkQueue(),
        ProviderRouter([provider]),
        transfer_queue=InMemoryWorkQueue(),
        provider_poll_claim_lease_seconds=0.06,
    )

    active = asyncio.create_task(first_service.poll_provider_jobs())
    await asyncio.wait_for(provider.entered.wait(), timeout=1)
    await asyncio.sleep(0.2)
    duplicate = await asyncio.wait_for(second_service.poll_provider_jobs(), timeout=1)
    assert duplicate.scanned == 0
    assert provider.calls == 1

    provider.release.set()
    completed = await asyncio.wait_for(active, timeout=1)
    assert completed.scanned == 1
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_polled_event_clears_previous_poll_backoff() -> None:
    repository = InMemoryJobRepository()
    job = _processing_job()
    job.provider_poll_failures = 3
    job.provider_last_poll_error = "PREVIOUS_TRANSIENT_ERROR"
    await repository.create_idempotent(job, "poll-event-clears-backoff", "hash")
    claim = (
        await repository.claim_processing_jobs(limit=1, lease=timedelta(seconds=30))
    )[0]
    claim.job.progress = 55
    claim.job.provider_poll_failures = 0
    claim.job.provider_next_poll_at = None
    claim.job.provider_last_poll_error = None

    result = await repository.apply_webhook_event(
        claim.job,
        claim.job.provider or "",
        "fresh-processing-event",
        poll_token=claim.token,
    )

    assert result == (True, True)
    persisted = await repository.get(job.id)
    assert persisted is not None
    assert persisted.progress == 55
    assert persisted.provider_poll_failures == 0
    assert persisted.provider_next_poll_at is None
    assert persisted.provider_last_poll_error is None


@pytest.mark.asyncio
async def test_two_sql_repositories_atomically_claim_one_job(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'poll-race.db'}"
    first_repository = SqlAlchemyJobRepository.from_url(database_url)
    second_repository = SqlAlchemyJobRepository.from_url(database_url)
    await first_repository.create_schema()
    try:
        job = _processing_job()
        await first_repository.create_idempotent(job, "sql-concurrent-poll", "hash")
        first, second = await asyncio.gather(
            first_repository.claim_processing_jobs(
                limit=1, lease=timedelta(seconds=30)
            ),
            second_repository.claim_processing_jobs(
                limit=1, lease=timedelta(seconds=30)
            ),
        )
        assert len(first) + len(second) == 1
        claim = (first or second)[0]
        owner = first_repository if first else second_repository
        assert await owner.record_provider_poll_success(claim.job.id, token=claim.token)
    finally:
        await first_repository.dispose()
        await second_repository.dispose()


@pytest.mark.asyncio
async def test_two_sql_services_call_provider_only_once(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'poll-service-race.db'}"
    first_repository = SqlAlchemyJobRepository.from_url(database_url)
    second_repository = SqlAlchemyJobRepository.from_url(database_url)
    await first_repository.create_schema()
    provider = BlockingPollingProvider()
    try:
        job = _processing_job(provider.route_id)
        await first_repository.create_idempotent(job, "sql-service-poll-race", "hash")
        first_service = GenerationService(
            first_repository,
            InMemoryWorkQueue(),
            ProviderRouter([provider]),
            transfer_queue=InMemoryWorkQueue(),
        )
        second_service = GenerationService(
            second_repository,
            InMemoryWorkQueue(),
            ProviderRouter([provider]),
            transfer_queue=InMemoryWorkQueue(),
        )

        active = asyncio.create_task(first_service.poll_provider_jobs())
        await asyncio.wait_for(provider.entered.wait(), timeout=1)
        duplicate = await asyncio.wait_for(
            second_service.poll_provider_jobs(), timeout=1
        )
        assert duplicate.scanned == 0
        assert provider.calls == 1

        provider.release.set()
        completed = await asyncio.wait_for(active, timeout=1)
        assert completed.scanned == 1
        assert provider.calls == 1
    finally:
        provider.release.set()
        await first_repository.dispose()
        await second_repository.dispose()


def _migration_config(project_root: Path, database_path: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
    )
    return config


def test_0009_provider_poll_claim_upgrades_and_downgrades_sqlite(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("RELAY_DATABASE_URL", raising=False)
    database_path = tmp_path / "relay-0009.db"
    project_root = Path(__file__).resolve().parents[1]
    config = _migration_config(project_root, database_path)
    command.upgrade(config, "0008_callback_claim_fencing")
    command.upgrade(config, "0009_provider_poll_claim")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    columns = {item["name"] for item in inspector.get_columns("generation_jobs")}
    indexes = {item["name"] for item in inspector.get_indexes("generation_jobs")}
    assert {
        "provider_poll_claim_token",
        "provider_poll_claim_expires_at",
    } <= columns
    assert "ix_generation_jobs_provider_poll_claim_expires_at" in indexes
    engine.dispose()

    command.downgrade(config, "0008_callback_claim_fencing")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    columns = {item["name"] for item in inspect(engine).get_columns("generation_jobs")}
    assert "provider_poll_claim_token" not in columns
    assert "provider_poll_claim_expires_at" not in columns
    engine.dispose()
