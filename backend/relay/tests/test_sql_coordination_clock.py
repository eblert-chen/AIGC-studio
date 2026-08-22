from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import re
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import relay_service.sql_repository as sql_repository
from relay_service.models import (
    GenerationInputs,
    GenerationJob,
    GenerationMode,
    JobStatus,
    OutputOptions,
    WorkItem,
)
from relay_service.sql_repository import (
    CallbackDeliveryRow,
    JobRow,
    OutboxRow,
    SqlAlchemyJobRepository,
    _aware,
)

COORDINATION_NOW = datetime(2031, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
DATABASE_URL = os.getenv("RELAY_TEST_DATABASE_URL", "")


def _job(*, callback: bool = False) -> GenerationJob:
    return GenerationJob(
        tenant_id=uuid4(),
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="database coordination clock"),
        output=OutputOptions(),
        callback_url=(
            "https://platform.example.test/internal/relay-callbacks"
            if callback
            else None
        ),
    )


async def _install_fixed_coordination_clock(
    repository: SqlAlchemyJobRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fixed_now(_session, *, fallback=None):
        del fallback
        return COORDINATION_NOW

    monkeypatch.setattr(repository, "_coordination_now", fixed_now)

    def process_clock_must_not_be_used() -> datetime:
        raise AssertionError("durable SQL coordination used the process clock")

    monkeypatch.setattr(sql_repository, "_now", process_clock_must_not_be_used)


@pytest.mark.asyncio
async def test_submission_transfer_and_poll_leases_use_coordination_clock(
    tmp_path, monkeypatch
) -> None:
    repository = SqlAlchemyJobRepository.from_url(
        f"sqlite+aiosqlite:///{tmp_path / 'coordination-leases.db'}"
    )
    await repository.create_schema()
    try:
        submission_job = _job()
        await repository.create_idempotent(
            submission_job, "coordination-submit", "submit-hash"
        )
        transfer_job = _job()
        transfer_job.status = JobStatus.TRANSFERRING
        await repository.create_idempotent(
            transfer_job, "coordination-transfer", "transfer-hash"
        )
        await repository.save(transfer_job)
        poll_job = _job()
        poll_job.status = JobStatus.PROCESSING
        poll_job.provider = "mock-route"
        poll_job.provider_task_id = "mock-provider-task"
        await repository.create_idempotent(poll_job, "coordination-poll", "poll-hash")
        await repository.save(poll_job)

        await _install_fixed_coordination_clock(repository, monkeypatch)
        lease = timedelta(seconds=45)

        submission = await repository.claim_submission(submission_job.id, lease=lease)
        assert submission is not None
        assert await repository.renew_submission_claim(
            submission_job.id, token=submission.token, lease=lease
        )

        transfer = await repository.claim_artifact_transfer(
            transfer_job.id, lease=lease
        )
        assert transfer is not None
        assert await repository.renew_artifact_transfer_claim(
            transfer_job.id, token=transfer.token, lease=lease
        )

        poll_claims = await repository.claim_processing_jobs(limit=10, lease=lease)
        assert len(poll_claims) == 1
        poll = poll_claims[0]
        assert await repository.renew_provider_poll_claim(
            poll_job.id, token=poll.token, lease=lease
        )

        async with repository.sessions() as session:
            submission_row = await session.get(JobRow, str(submission_job.id))
            transfer_row = await session.get(JobRow, str(transfer_job.id))
            poll_row = await session.get(JobRow, str(poll_job.id))
        expected_expiry = COORDINATION_NOW + lease
        assert _aware(submission_row.submission_claim_expires_at) == expected_expiry
        assert _aware(transfer_row.transfer_claim_expires_at) == expected_expiry
        assert _aware(poll_row.provider_poll_claim_expires_at) == expected_expiry

        assert await repository.record_provider_poll_failure(
            poll_job.id,
            token=poll.token,
            error_code="PROVIDER_TEMPORARY",
            retry_delay=timedelta(seconds=12),
        )
        async with repository.sessions() as session:
            poll_row = await session.get(JobRow, str(poll_job.id))
        assert _aware(poll_row.provider_next_poll_at) == (
            COORDINATION_NOW + timedelta(seconds=12)
        )
    finally:
        await repository.dispose()


@pytest.mark.asyncio
async def test_outbox_and_callback_delivery_use_coordination_clock(
    tmp_path, monkeypatch
) -> None:
    repository = SqlAlchemyJobRepository.from_url(
        f"sqlite+aiosqlite:///{tmp_path / 'coordination-delivery.db'}"
    )
    await repository.create_schema()
    try:
        outbox_job = _job()
        await repository.create_idempotent(
            outbox_job, "coordination-outbox", "outbox-hash"
        )
        await repository.save_with_outbox(
            outbox_job,
            "generation.submit",
            WorkItem(job_id=outbox_job.id),
        )

        callback_job = _job(callback=True)
        await repository.create_idempotent(
            callback_job, "coordination-callback", "callback-hash"
        )
        callback_job.status = JobStatus.PROCESSING
        callback_job.progress = 1
        await repository.save(callback_job)

        await _install_fixed_coordination_clock(repository, monkeypatch)

        outbox_claims = await repository.claim_outbox(
            batch_size=10, lease=timedelta(seconds=30)
        )
        assert outbox_claims
        outbox_id = outbox_claims[0].id
        await repository.release_outbox(outbox_id, "temporary")
        async with repository.sessions() as session:
            outbox_row = await session.get(OutboxRow, str(outbox_id))
        assert _aware(outbox_row.available_at) == (
            COORDINATION_NOW + timedelta(seconds=1)
        )

        callback_claims = await repository.claim_callback_deliveries(
            batch_size=10, lease=timedelta(seconds=30)
        )
        assert len(callback_claims) == 1
        callback_claim = callback_claims[0]
        assert await repository.release_callback_delivery(
            callback_claim.delivery.id,
            token=callback_claim.token,
            error="temporary",
            retry_delay=timedelta(seconds=17),
            dead_letter=False,
        )
        async with repository.sessions() as session:
            callback_row = await session.get(
                CallbackDeliveryRow, str(callback_claim.delivery.id)
            )
        assert _aware(callback_row.available_at) == (
            COORDINATION_NOW + timedelta(seconds=17)
        )
    finally:
        await repository.dispose()


@pytest.mark.asyncio
async def test_postgres_claims_ignore_skewed_process_clock(monkeypatch) -> None:
    if not DATABASE_URL.startswith("postgresql+asyncpg://"):
        pytest.skip("requires RELAY_TEST_DATABASE_URL with asyncpg")

    schema = f"relay_coordination_clock_{uuid4().hex}"
    assert re.fullmatch(r"relay_coordination_clock_[0-9a-f]{32}", schema)
    administration_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    async with administration_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    repository = SqlAlchemyJobRepository(engine)
    try:
        await repository.create_schema()
        submission_job = _job()
        await repository.create_idempotent(
            submission_job, "postgres-clock-submit", "submit-hash"
        )
        callback_job = _job(callback=True)
        await repository.create_idempotent(
            callback_job, "postgres-clock-callback", "callback-hash"
        )
        callback_job.status = JobStatus.PROCESSING
        callback_job.progress = 1
        await repository.save(callback_job)

        monkeypatch.setattr(
            sql_repository,
            "_now",
            lambda: datetime(2099, 1, 1, tzinfo=timezone.utc),
        )

        submission = await repository.claim_submission(
            submission_job.id, lease=timedelta(seconds=30)
        )
        assert submission is not None
        outbox_claims = await repository.claim_outbox(
            batch_size=10, lease=timedelta(seconds=30)
        )
        assert outbox_claims
        callback_claims = await repository.claim_callback_deliveries(
            batch_size=10, lease=timedelta(seconds=30)
        )
        assert len(callback_claims) == 1

        async with engine.connect() as connection:
            database_now = await connection.scalar(text("SELECT clock_timestamp()"))
            submission_expiry = await connection.scalar(
                text(
                    "SELECT submission_claim_expires_at "
                    "FROM generation_jobs WHERE id = :job_id"
                ),
                {"job_id": str(submission_job.id)},
            )
            outbox_locked_at = await connection.scalar(
                text(
                    "SELECT MAX(locked_at) FROM relay_outbox "
                    "WHERE status = 'publishing'"
                )
            )
            callback_locked_at = await connection.scalar(
                text(
                    "SELECT locked_at FROM callback_deliveries "
                    "WHERE id = :delivery_id"
                ),
                {"delivery_id": str(callback_claims[0].delivery.id)},
            )

        assert database_now is not None
        assert 20 <= (submission_expiry - database_now).total_seconds() <= 35
        assert abs((outbox_locked_at - database_now).total_seconds()) < 5
        assert abs((callback_locked_at - database_now).total_seconds()) < 5
    finally:
        await engine.dispose()
        async with administration_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await administration_engine.dispose()
