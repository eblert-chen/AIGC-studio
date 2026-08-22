from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from relay_service.models import (
    CallbackDeliveryStatus,
    GenerationInputs,
    GenerationJob,
    GenerationMode,
    JobStatus,
    OutputOptions,
)
from relay_service.repository import CallbackRepository, InMemoryJobRepository
from relay_service.sql_repository import SqlAlchemyJobRepository


async def _create_callback_delivery(repository: CallbackRepository):
    tenant_id = uuid4()
    job = GenerationJob(
        tenant_id=tenant_id,
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="callback claim fence"),
        output=OutputOptions(),
        callback_url="https://platform.example.test/internal/relay-callbacks",
    )
    stored, replayed, conflict = await repository.create_idempotent(
        job,
        f"callback-fence-{uuid4()}",
        f"request-hash-{uuid4()}",
    )
    assert replayed is False
    assert conflict is False
    stored.status = JobStatus.PROCESSING
    stored.progress = 1
    await repository.save(stored)
    return tenant_id


async def _assert_stale_failure_cannot_regress_delivered(
    repository: CallbackRepository,
) -> None:
    tenant_id = await _create_callback_delivery(repository)
    first_claims = await repository.claim_callback_deliveries(
        batch_size=1,
        lease=timedelta(seconds=60),
    )
    assert len(first_claims) == 1
    first = first_claims[0]

    # Worker A exceeds its visibility lease. Worker B then owns the same stable
    # event under a fresh token, so A's eventual result must be ignored.
    await asyncio.sleep(0.002)
    second_claims = await repository.claim_callback_deliveries(
        batch_size=1,
        lease=timedelta(0),
    )
    assert len(second_claims) == 1
    second = second_claims[0]
    assert second.delivery.id == first.delivery.id
    assert second.token != first.token
    assert second.delivery.attempts == 2

    delivered = asyncio.Event()

    async def current_worker_completes() -> bool:
        completed = await repository.mark_callback_delivered(
            second.delivery.id,
            token=second.token,
            response_status=204,
        )
        delivered.set()
        return completed

    async def stale_worker_fails_late() -> bool:
        await delivered.wait()
        return await repository.release_callback_delivery(
            first.delivery.id,
            token=first.token,
            error="late timeout from stale worker",
            retry_delay=timedelta(0),
            dead_letter=False,
        )

    current_result, stale_result = await asyncio.gather(
        current_worker_completes(),
        stale_worker_fails_late(),
    )
    assert current_result is True
    assert stale_result is False

    # A stale success is fenced as well; delivered is a monotonic terminal state.
    assert (
        await repository.mark_callback_delivered(
            first.delivery.id,
            token=first.token,
            response_status=200,
        )
        is False
    )
    records = await repository.list_callback_deliveries(tenant_id)
    assert len(records) == 1
    record = records[0]
    assert record.delivery_status == CallbackDeliveryStatus.DELIVERED
    assert record.attempts == 2
    assert record.response_status == 204
    assert record.last_error is None
    assert record.delivered_at is not None


def test_in_memory_callback_claim_fences_late_failure() -> None:
    asyncio.run(
        _assert_stale_failure_cannot_regress_delivered(
            InMemoryJobRepository()
        )
    )


def test_sql_callback_claim_fences_late_failure(tmp_path) -> None:
    async def scenario() -> None:
        repository = SqlAlchemyJobRepository.from_url(
            f"sqlite+aiosqlite:///{tmp_path / 'callback-fence.db'}"
        )
        await repository.create_schema()
        await _assert_stale_failure_cannot_regress_delivered(repository)
        await repository.dispose()

    asyncio.run(scenario())


def _migration_config(project_root: Path, database_path: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
    )
    return config


def test_0008_callback_claim_fencing_upgrades_and_downgrades_sqlite(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("RELAY_DATABASE_URL", raising=False)
    database_path = tmp_path / "relay-0008.db"
    project_root = Path(__file__).resolve().parents[1]
    config = _migration_config(project_root, database_path)

    command.upgrade(config, "0007_artifact_transfer_claim")
    command.upgrade(config, "0008_callback_claim_fencing")

    sync_url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(sync_url)
    columns = {
        item["name"]
        for item in inspect(engine).get_columns("callback_deliveries")
    }
    with engine.connect() as connection:
        revision = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
    assert revision == "0008_callback_claim_fencing"
    assert "claim_token" in columns
    engine.dispose()

    command.downgrade(config, "0007_artifact_transfer_claim")
    engine = create_engine(sync_url)
    columns = {
        item["name"]
        for item in inspect(engine).get_columns("callback_deliveries")
    }
    with engine.connect() as connection:
        revision = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
    assert revision == "0007_artifact_transfer_claim"
    assert "claim_token" not in columns
    engine.dispose()
