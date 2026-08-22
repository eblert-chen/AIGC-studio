from __future__ import annotations

import asyncio
from datetime import timedelta
import os
import re
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from relay_service.models import (
    GenerationInputs,
    GenerationJob,
    GenerationMode,
    OutputOptions,
)
from relay_service.providers.mock import MockProviderAdapter
from relay_service.providers.pool import AccountAcquireReason
from relay_service.sql_repository import SqlAlchemyJobRepository


DATABASE_URL = os.getenv("RELAY_TEST_DATABASE_URL", "")


def _job(prompt: str) -> GenerationJob:
    return GenerationJob(
        tenant_id=uuid4(),
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt=prompt),
        output=OutputOptions(),
    )


@pytest.mark.asyncio
async def test_postgres_route_lock_prevents_cross_worker_overcommit() -> None:
    if not DATABASE_URL.startswith("postgresql+asyncpg://"):
        pytest.skip("requires RELAY_TEST_DATABASE_URL with asyncpg")

    schema = f"relay_pool_{uuid4().hex}"
    assert re.fullmatch(r"relay_pool_[0-9a-f]{32}", schema)
    administration_engine = create_async_engine(
        DATABASE_URL, pool_pre_ping=True
    )
    async with administration_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_async_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    first = SqlAlchemyJobRepository(engine)
    second = SqlAlchemyJobRepository(engine)
    try:
        await first.create_schema()
        provider = MockProviderAdapter(max_concurrency=1)
        manifest = provider.manifest
        await first.register_routes([manifest])
        jobs = [_job("first"), _job("second")]
        claims = []
        for index, job in enumerate(jobs):
            repository = first if index == 0 else second
            await repository.create_idempotent(
                job, f"postgres-pool-{index}", f"hash-{index}"
            )
            claim = await repository.claim_submission(
                job.id, lease=timedelta(seconds=30)
            )
            assert claim is not None
            claims.append(claim)

        barrier = asyncio.Event()

        async def acquire(repository, job, claim):
            await barrier.wait()
            return await repository.acquire(
                job.id, manifest, owner_token=claim.token
            )

        attempts = [
            asyncio.create_task(acquire(first, jobs[0], claims[0])),
            asyncio.create_task(acquire(second, jobs[1], claims[1])),
        ]
        barrier.set()
        results = await asyncio.gather(*attempts)
        assert sum(item.acquired for item in results) == 1
        assert {item.reason for item in results} == {
            AccountAcquireReason.ACQUIRED,
            AccountAcquireReason.BUSY,
        }
        snapshot = (await first.snapshots([manifest.route_id]))[
            manifest.route_id
        ]
        assert snapshot.active_jobs == 1
    finally:
        await engine.dispose()
        async with administration_engine.begin() as connection:
            await connection.execute(
                text(f'DROP SCHEMA "{schema}" CASCADE')
            )
        await administration_engine.dispose()
