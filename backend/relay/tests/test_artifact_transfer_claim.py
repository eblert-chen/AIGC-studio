from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import io
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect

from relay_service.artifacts import InMemoryArtifactStore
from relay_service.downloader import DownloadedArtifact
from relay_service.models import (
    GenerationInputs,
    GenerationJob,
    GenerationMode,
    JobStatus,
    OutputOptions,
    TransferSource,
    WorkItem,
)
from relay_service.queue import InMemoryWorkQueue
from relay_service.repository import InMemoryJobRepository
from relay_service.sql_repository import SqlAlchemyJobRepository
from relay_service.transfer import ArtifactTransferService


def _transferring_job() -> GenerationJob:
    tenant_id = uuid4()
    job_id = uuid4()
    asset_id = uuid4()
    return GenerationJob(
        id=job_id,
        tenant_id=tenant_id,
        model="test.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="claim fencing"),
        output=OutputOptions(),
        status=JobStatus.TRANSFERRING,
        progress=95,
        transfer_sources=[
            TransferSource(
                asset_id=asset_id,
                source_url="https://cdn.example.test/output.mp4",
                media_type="video",
                object_key=f"outputs/{tenant_id}/{job_id}/{asset_id}",
            )
        ],
    )


@pytest.mark.asyncio
async def test_memory_expired_transfer_claim_rejects_stale_owner() -> None:
    repository = InMemoryJobRepository()
    job = _transferring_job()
    await repository.create_idempotent(job, "memory-transfer-claim", "hash")

    stale = await repository.claim_artifact_transfer(
        job.id, lease=timedelta(milliseconds=1)
    )
    assert stale is not None
    await asyncio.sleep(0.01)
    current = await repository.claim_artifact_transfer(
        job.id, lease=timedelta(seconds=30)
    )
    assert current is not None
    assert current.token != stale.token

    stale.job.updated_at = stale.job.updated_at + timedelta(seconds=1)
    assert not await repository.save_artifact_transfer_progress(
        stale.job, token=stale.token
    )
    stale.job.status = JobStatus.FAILED
    assert not await repository.finish_artifact_transfer(stale.job, token=stale.token)
    current.job.status = JobStatus.SUCCEEDED
    current.job.progress = 100
    assert await repository.finish_artifact_transfer(current.job, token=current.token)
    persisted = await repository.get(job.id)
    assert persisted is not None
    assert persisted.status == JobStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_sql_expired_transfer_claim_rejects_stale_owner(tmp_path) -> None:
    repository = SqlAlchemyJobRepository.from_url(
        f"sqlite+aiosqlite:///{tmp_path / 'transfer-claim.db'}"
    )
    await repository.create_schema()
    try:
        job = _transferring_job()
        await repository.create_idempotent(job, "sql-transfer-claim", "hash")
        stale = await repository.claim_artifact_transfer(
            job.id, lease=timedelta(milliseconds=1)
        )
        assert stale is not None
        await asyncio.sleep(0.01)
        current = await repository.claim_artifact_transfer(
            job.id, lease=timedelta(seconds=30)
        )
        assert current is not None
        assert current.token != stale.token

        stale.job.updated_at = stale.job.updated_at + timedelta(seconds=1)
        assert not await repository.save_artifact_transfer_progress(
            stale.job, token=stale.token
        )
        stale.job.status = JobStatus.FAILED
        assert not await repository.finish_artifact_transfer(
            stale.job, token=stale.token
        )
        current.job.status = JobStatus.SUCCEEDED
        current.job.progress = 100
        assert await repository.finish_artifact_transfer(
            current.job, token=current.token
        )
        persisted = await repository.get(job.id)
        assert persisted is not None
        assert persisted.status == JobStatus.SUCCEEDED
    finally:
        await repository.dispose()


@pytest.mark.asyncio
async def test_duplicate_transfer_workers_cannot_regress_success() -> None:
    class BlockingDownloader:
        def __init__(self) -> None:
            self.calls = 0
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def download(self, url: str) -> DownloadedArtifact:
            del url
            self.calls += 1
            self.entered.set()
            await self.release.wait()
            content = b"durable generated video"
            return DownloadedArtifact(
                content=io.BytesIO(content),
                content_type="video/mp4",
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )

    repository = InMemoryJobRepository()
    first_queue = InMemoryWorkQueue()
    duplicate_queue = InMemoryWorkQueue()
    downloader = BlockingDownloader()
    store = InMemoryArtifactStore()
    job = _transferring_job()
    await repository.create_idempotent(job, "duplicate-transfer", "hash")
    await first_queue.enqueue(WorkItem(job_id=job.id))
    await duplicate_queue.enqueue(WorkItem(job_id=job.id))
    first_worker = ArtifactTransferService(
        repository,
        first_queue,
        downloader,
        store,
        claim_lease_seconds=1,
    )
    duplicate_worker = ArtifactTransferService(
        repository,
        duplicate_queue,
        downloader,
        store,
        claim_lease_seconds=1,
    )

    active = asyncio.create_task(first_worker.process_next())
    await asyncio.wait_for(downloader.entered.wait(), timeout=1)
    duplicate_result = await asyncio.wait_for(
        duplicate_worker.process_next(), timeout=1
    )
    assert duplicate_result is not None
    assert duplicate_result.status == JobStatus.TRANSFERRING
    assert downloader.calls == 1

    downloader.release.set()
    completed = await asyncio.wait_for(active, timeout=1)
    assert completed is not None
    assert completed.status == JobStatus.SUCCEEDED
    persisted = await repository.get(job.id)
    assert persisted is not None
    assert persisted.status == JobStatus.SUCCEEDED
    assert len(persisted.outputs) == 1


@pytest.mark.asyncio
async def test_transfer_worker_renews_claim_during_slow_download() -> None:
    class SlowDownloader:
        def __init__(self) -> None:
            self.calls = 0
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def download(self, url: str) -> DownloadedArtifact:
            del url
            self.calls += 1
            self.entered.set()
            await self.release.wait()
            content = b"slow durable generated video"
            return DownloadedArtifact(
                content=io.BytesIO(content),
                content_type="video/mp4",
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )

    repository = InMemoryJobRepository()
    first_queue = InMemoryWorkQueue()
    duplicate_queue = InMemoryWorkQueue()
    downloader = SlowDownloader()
    store = InMemoryArtifactStore()
    job = _transferring_job()
    await repository.create_idempotent(job, "slow-transfer-claim", "hash")
    await first_queue.enqueue(WorkItem(job_id=job.id))
    await duplicate_queue.enqueue(WorkItem(job_id=job.id))
    first_worker = ArtifactTransferService(
        repository,
        first_queue,
        downloader,
        store,
        claim_lease_seconds=0.06,
    )
    duplicate_worker = ArtifactTransferService(
        repository,
        duplicate_queue,
        downloader,
        store,
        claim_lease_seconds=0.06,
    )

    active = asyncio.create_task(first_worker.process_next())
    await asyncio.wait_for(downloader.entered.wait(), timeout=1)
    # Wait through several initial lease periods. The heartbeat must keep the
    # same owner fenced in while the external download remains in progress.
    await asyncio.sleep(0.2)
    duplicate_result = await asyncio.wait_for(
        duplicate_worker.process_next(), timeout=1
    )
    assert duplicate_result is not None
    assert duplicate_result.status == JobStatus.TRANSFERRING
    assert downloader.calls == 1

    downloader.release.set()
    completed = await asyncio.wait_for(active, timeout=1)
    assert completed is not None
    assert completed.status == JobStatus.SUCCEEDED
    persisted = await repository.get(job.id)
    assert persisted is not None
    assert persisted.status == JobStatus.SUCCEEDED


def _migration_config(project_root: Path, database_path: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
    )
    return config


def test_0007_artifact_transfer_claim_upgrades_and_downgrades_sqlite(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("RELAY_DATABASE_URL", raising=False)
    database_path = tmp_path / "relay-0007.db"
    project_root = Path(__file__).resolve().parents[1]
    config = _migration_config(project_root, database_path)
    command.upgrade(config, "0006_source_client_identity")
    command.upgrade(config, "0007_artifact_transfer_claim")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    columns = {item["name"] for item in inspect(engine).get_columns("generation_jobs")}
    assert {"transfer_claim_token", "transfer_claim_expires_at"} <= columns
    engine.dispose()

    command.downgrade(config, "0006_source_client_identity")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    columns = {item["name"] for item in inspect(engine).get_columns("generation_jobs")}
    assert "transfer_claim_token" not in columns
    assert "transfer_claim_expires_at" not in columns
    engine.dispose()
