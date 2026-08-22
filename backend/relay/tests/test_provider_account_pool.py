from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect

from relay_service.models import (
    GenerationInputs,
    GenerationJob,
    GenerationMode,
    JobStatus,
    OutputOptions,
)
from relay_service.providers.base import ProviderError, ProviderSubmission
from relay_service.providers.mock import MockProviderAdapter
from relay_service.providers.pool import (
    AccountAcquireReason,
    InMemoryProviderAccountPool,
)
from relay_service.providers.router import ProviderRouter
from relay_service.sql_repository import SqlAlchemyJobRepository


MOCK_CAPABILITY_REVISION = asyncio.run(
    ProviderRouter([MockProviderAdapter()]).model_catalog()
).data[0].capability_revision


def _job(prompt: str) -> GenerationJob:
    return GenerationJob(
        tenant_id=uuid4(),
        model="mock.video.v1",
        expected_capability_revision=MOCK_CAPABILITY_REVISION,
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt=prompt),
        output=OutputOptions(),
    )


@pytest.mark.parametrize("value", [0, -1, True])
def test_account_rpm_must_be_a_positive_integer(value) -> None:
    with pytest.raises(ValueError, match="requests_per_minute"):
        MockProviderAdapter(requests_per_minute=value)


def test_permanent_disable_requires_an_account_failure() -> None:
    with pytest.raises(ValueError, match="account_unavailable"):
        ProviderError(
            "INVALID_FLAGS",
            "invalid",
            retryable=False,
            account_unavailable=False,
            disable_account=True,
        )


@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf")])
def test_provider_retry_delay_must_be_finite_and_positive(value) -> None:
    with pytest.raises(ValueError, match="retry_after_seconds"):
        ProviderError(
            "INVALID_RETRY_DELAY",
            "invalid",
            retryable=True,
            account_unavailable=False,
            retry_after_seconds=value,
        )


def test_active_slot_survives_submit_until_upstream_terminal() -> None:
    async def scenario() -> None:
        provider = MockProviderAdapter(max_concurrency=1)
        router = ProviderRouter([provider])
        first = _job("first")
        second = _job("second")

        route_id, _ = await router.submit(first)
        assert first.provider == route_id
        with pytest.raises(ProviderError) as caught:
            await router.submit(second)
        assert caught.value.code == "PROVIDER_ACCOUNT_POOL_BUSY"

        first.status = JobStatus.SUCCEEDED
        await router.complete_job(first)
        second_route, _ = await router.submit(second)
        assert second_route == route_id

    asyncio.run(scenario())


def test_account_rpm_is_shared_across_completed_jobs() -> None:
    async def scenario() -> None:
        provider = MockProviderAdapter(requests_per_minute=1)
        router = ProviderRouter([provider])
        first = _job("first")
        second = _job("second")

        await router.submit(first)
        first.status = JobStatus.SUCCEEDED
        await router.complete_job(first)
        with pytest.raises(ProviderError) as caught:
            await router.submit(second)
        assert caught.value.code == "PROVIDER_ACCOUNT_POOL_RATE_LIMITED"

    asyncio.run(scenario())


def test_permanent_account_failure_disables_only_that_account() -> None:
    class RevokedAccount(MockProviderAdapter):
        async def submit(self, job: GenerationJob) -> ProviderSubmission:
            raise ProviderError(
                "ACCOUNT_REVOKED",
                "Provider account was revoked before task creation",
                retryable=False,
                account_unavailable=True,
                disable_account=True,
            )

    async def scenario() -> None:
        failed = RevokedAccount(account_id="revoked", priority=10)
        fallback = MockProviderAdapter(account_id="healthy", priority=20)
        pool = InMemoryProviderAccountPool()
        router = ProviderRouter([failed, fallback], account_pool=pool)

        route_id, _ = await router.submit(_job("switch"))
        assert route_id == "mock-video@healthy"
        states = await pool.snapshots([failed.route_id, fallback.route_id])
        assert states[failed.route_id].admission_enabled is False
        assert states[fallback.route_id].admission_enabled is True

    asyncio.run(scenario())


def test_unknown_submission_keeps_account_slot_and_route() -> None:
    class AmbiguousAccount(MockProviderAdapter):
        async def submit(self, job: GenerationJob) -> ProviderSubmission:
            raise ProviderError(
                "SUBMISSION_OUTCOME_UNKNOWN",
                "Provider may have created the task",
                retryable=False,
                account_unavailable=False,
                submission_outcome_unknown=True,
            )

    async def scenario() -> None:
        provider = AmbiguousAccount(max_concurrency=1)
        router = ProviderRouter([provider])
        first = _job("ambiguous")
        with pytest.raises(ProviderError) as caught:
            await router.submit(first)
        assert caught.value.submission_outcome_unknown is True
        assert first.provider == provider.route_id

        with pytest.raises(ProviderError) as busy:
            await router.submit(_job("must wait"))
        assert busy.value.code == "PROVIDER_ACCOUNT_POOL_BUSY"

    asyncio.run(scenario())


def test_post_submit_pool_counter_failure_never_hides_provider_task() -> None:
    class BrokenCounterPool(InMemoryProviderAccountPool):
        async def record_success(
            self, route_id: str, *, submission: bool = False
        ) -> None:
            raise RuntimeError("scheduler database unavailable")

    async def scenario() -> None:
        provider = MockProviderAdapter()
        router = ProviderRouter(
            [provider], account_pool=BrokenCounterPool()
        )
        job = _job("accepted")
        route_id, submission = await router.submit(job)
        assert route_id == provider.route_id
        assert submission.provider_task_id == f"mock-{job.id}"

    asyncio.run(scenario())


def test_known_non_creation_pool_failure_is_fenced_for_reconciliation() -> None:
    class KnownAccountFailure(MockProviderAdapter):
        async def submit(self, job: GenerationJob) -> ProviderSubmission:
            raise ProviderError(
                "ACCOUNT_COOLING",
                "No task was created",
                retryable=True,
                account_unavailable=True,
            )

    class BrokenReleasePool(InMemoryProviderAccountPool):
        async def record_failure(self, *args, **kwargs) -> bool:
            raise RuntimeError("scheduler database unavailable")

    async def scenario() -> None:
        provider = KnownAccountFailure()
        router = ProviderRouter(
            [provider], account_pool=BrokenReleasePool()
        )
        job = _job("known failure, broken scheduler")
        with pytest.raises(ProviderError) as caught:
            await router.submit(job)
        assert caught.value.code == "PROVIDER_ACCOUNT_ASSIGNMENT_LOST"
        assert caught.value.submission_outcome_unknown is True
        assert caught.value.route_id == provider.route_id
        assert job.provider == provider.route_id

    asyncio.run(scenario())


def test_poll_receives_only_sanitized_job_metadata() -> None:
    class InspectingAccount(MockProviderAdapter):
        seen: GenerationJob | None = None

        async def poll(self, job: GenerationJob):
            self.seen = job
            return None

    async def scenario() -> None:
        provider = InspectingAccount()
        router = ProviderRouter([provider])
        job = _job("poll")
        job.provider = provider.route_id
        job.provider_task_id = "provider-task"
        job.source_client_id = "private-client"
        job.client_reference_id = "private-reference"
        job.callback_url = "https://callback.example.test/private"
        job.metadata = {
            "secret": "must-not-leak",
            "relay_request_id": "safe-trace",
        }

        await router.poll(job)
        assert provider.seen is not None
        assert provider.seen.source_client_id is None
        assert provider.seen.client_reference_id is None
        assert provider.seen.callback_url is None
        assert provider.seen.metadata == {"relay_request_id": "safe-trace"}

    asyncio.run(scenario())


def test_disabled_account_rejects_new_jobs_but_keeps_sticky_polling() -> None:
    class PollingAccount(MockProviderAdapter):
        polls = 0

        async def poll(self, job: GenerationJob):
            self.polls += 1
            return None

    async def scenario() -> None:
        provider = PollingAccount()
        pool = InMemoryProviderAccountPool()
        router = ProviderRouter([provider], account_pool=pool)
        await router.validate_configuration()
        assert await router.set_account_admission(
            provider.route_id, enabled=False
        )

        existing = _job("existing")
        existing.provider = provider.route_id
        existing.provider_task_id = "already-created"
        await router.poll(existing)
        assert provider.polls == 1

        with pytest.raises(ProviderError) as caught:
            await router.submit(_job("new"))
        assert caught.value.code == "PROVIDER_ACCOUNT_POOL_DISABLED"
        state = (await pool.snapshots([provider.route_id]))[
            provider.route_id
        ]
        assert state.admission_enabled is False

    asyncio.run(scenario())


@pytest.mark.asyncio
async def test_sql_pool_serializes_active_slots_across_repositories(
    tmp_path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'account-pool.db'}"
    first_repository = SqlAlchemyJobRepository.from_url(database_url)
    second_repository = SqlAlchemyJobRepository.from_url(database_url)
    await first_repository.create_schema()
    provider = MockProviderAdapter(max_concurrency=1)
    manifest = provider.manifest
    await first_repository.register_routes([manifest])

    jobs = [_job("one"), _job("two")]
    claims = []
    for index, job in enumerate(jobs):
        repository = first_repository if index == 0 else second_repository
        await repository.create_idempotent(job, f"pool-{index}", f"hash-{index}")
        claim = await repository.claim_submission(
            job.id, lease=timedelta(seconds=30)
        )
        assert claim is not None
        claims.append(claim)

    try:
        results = await asyncio.gather(
            first_repository.acquire(
                jobs[0].id, manifest, owner_token=claims[0].token
            ),
            second_repository.acquire(
                jobs[1].id, manifest, owner_token=claims[1].token
            ),
        )
        assert sum(result.acquired for result in results) == 1
        assert {result.reason for result in results} == {
            AccountAcquireReason.ACQUIRED,
            AccountAcquireReason.BUSY,
        }

        winner = 0 if results[0].acquired else 1
        loser = 1 - winner
        winner_repository = (
            first_repository if winner == 0 else second_repository
        )
        loser_repository = (
            first_repository if loser == 0 else second_repository
        )
        claimed_job = claims[winner].job
        claimed_job.provider = manifest.route_id
        claimed_job.provider_task_id = f"upstream-{winner}"
        claimed_job.status = JobStatus.PROCESSING
        assert await winner_repository.finish_submission(
            claimed_job, token=claims[winner].token
        )

        still_busy = await loser_repository.acquire(
            jobs[loser].id,
            manifest,
            owner_token=claims[loser].token,
        )
        assert still_busy.reason == AccountAcquireReason.BUSY

        terminal = await winner_repository.get(jobs[winner].id)
        assert terminal is not None
        terminal.status = JobStatus.FAILED
        await winner_repository.save(terminal)
        acquired_after_terminal = await loser_repository.acquire(
            jobs[loser].id,
            manifest,
            owner_token=claims[loser].token,
        )
        assert acquired_after_terminal.acquired
        snapshots = await first_repository.snapshots([manifest.route_id])
        assert snapshots[manifest.route_id].active_jobs == 1
    finally:
        await first_repository.dispose()
        await second_repository.dispose()


@pytest.mark.asyncio
async def test_sql_pool_enforces_owner_token_and_shared_rpm(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'account-rpm.db'}"
    repository = SqlAlchemyJobRepository.from_url(database_url)
    await repository.create_schema()
    provider = MockProviderAdapter(requests_per_minute=1)
    manifest = provider.manifest
    await repository.register_routes([manifest])
    first = _job("first")
    second = _job("second")
    await repository.create_idempotent(first, "rpm-1", "hash-1")
    await repository.create_idempotent(second, "rpm-2", "hash-2")
    first_claim = await repository.claim_submission(
        first.id, lease=timedelta(seconds=30)
    )
    second_claim = await repository.claim_submission(
        second.id, lease=timedelta(seconds=30)
    )
    assert first_claim is not None and second_claim is not None
    try:
        wrong_owner = await repository.acquire(
            first.id, manifest, owner_token=uuid4()
        )
        assert wrong_owner.reason == AccountAcquireReason.JOB_NOT_SUBMITTING
        assert (
            await repository.acquire(
                first.id, manifest, owner_token=first_claim.token
            )
        ).acquired
        assert await repository.release_assignment(
            first.id,
            manifest.route_id,
            owner_token=first_claim.token,
        )
        limited = await repository.acquire(
            second.id, manifest, owner_token=second_claim.token
        )
        assert limited.reason == AccountAcquireReason.RATE_LIMITED
    finally:
        await repository.dispose()


def _migration_config(project_root: Path, database_path: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
    )
    return config


def test_0010_provider_account_pool_migration_round_trip(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("RELAY_DATABASE_URL", raising=False)
    database_path = tmp_path / "relay-0010.db"
    project_root = Path(__file__).resolve().parents[1]
    config = _migration_config(project_root, database_path)
    command.upgrade(config, "0009_provider_poll_claim")
    command.upgrade(config, "0010_provider_account_pool")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    columns = {
        item["name"]
        for item in inspector.get_columns("provider_account_states")
    }
    assert {
        "route_id",
        "account_id",
        "admission_enabled",
        "max_concurrency",
        "requests_per_minute",
        "cooldown_until",
        "rate_window_count",
        "successful_submissions",
    } <= columns
    with engine.connect() as connection:
        revision = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
    assert revision == "0010_provider_account_pool"
    engine.dispose()

    command.downgrade(config, "0009_provider_poll_claim")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert "provider_account_states" not in inspect(engine).get_table_names()
    engine.dispose()
