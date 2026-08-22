from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from math import nan
import json
import os
import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from relay_service.models import (
    GenerationInputs,
    GenerationJob,
    GenerationMode,
    OutputOptions,
)
from relay_service.artifacts import InMemoryArtifactStore
from relay_service.auth import ClientCredential, StaticClientAuthenticator
from relay_service.config import RelaySettings
from relay_service.main import create_app
from relay_service.provider_monitoring import (
    InMemoryProviderMonitoringRepository,
    ProviderAlertEventType,
    ProviderAlertDispatcher,
    ProviderAlertKind,
    ProviderHealthSample,
    ProviderMonitor,
    ProviderMonitorPolicy,
    ProviderOutcomeSummary,
)
from relay_service.providers.base import (
    ProviderChannelType,
    ProviderError,
    ProviderSubmission,
)
from relay_service.providers.mock import MockProviderAdapter
from relay_service.providers.pool import InMemoryProviderAccountPool
from relay_service.providers.router import ProviderRouter
from relay_service.queue import InMemoryWorkQueue
from relay_service.repository import InMemoryJobRepository
from relay_service.sql_repository import SqlAlchemyJobRepository


UTC = timezone.utc
START = datetime(2026, 8, 5, 1, 0, tzinfo=UTC)


def test_explicit_channel_scope_never_infers_account_unavailability() -> None:
    error = ProviderError(
        "CHANNEL_UNAVAILABLE",
        "The channel rejected the request before task creation",
        retryable=True,
        failure_scope="channel",
    )

    assert error.account_unavailable is False
    assert error.failure_scope == "channel"


def test_provider_error_code_cannot_smuggle_secret_text_into_monitoring() -> None:
    with pytest.raises(ValueError, match="error code"):
        ProviderError(
            "token=secret https://private.provider.test",
            "adapter message",
            retryable=False,
        )


def test_persistent_runtime_requires_shared_postgresql() -> None:
    settings = RelaySettings(
        runtime_mode="production",
        database_url="sqlite+aiosqlite:///relay.db",
        redis_url="redis://queue",
    )

    with pytest.raises(RuntimeError, match=r"postgresql\+asyncpg"):
        settings.validate()


def _production_monitor_settings(**updates) -> RelaySettings:
    values = {
        "environment": "production",
        "runtime_mode": "production",
        "database_url": "postgresql+asyncpg://db/relay",
        "redis_url": "redis://queue",
        "artifact_store": "huawei_obs",
        "provider_alert_webhook_url": "https://alerts.example.com/relay",
        "provider_alert_signing_secret": "x" * 48,
    }
    values.update(updates)
    return RelaySettings(**values)


def test_production_requires_provider_monitor_and_external_alert_sink() -> None:
    with pytest.raises(RuntimeError, match="MONITOR_ENABLED"):
        _production_monitor_settings(provider_monitor_enabled=False).validate()

    with pytest.raises(RuntimeError, match="alert webhook"):
        _production_monitor_settings(
            provider_alert_webhook_url=None,
            provider_alert_signing_secret=None,
        ).validate()

    _production_monitor_settings().validate()


def test_alert_claim_lease_must_exceed_end_to_end_timeout() -> None:
    with pytest.raises(RuntimeError, match="CLAIM_LEASE_SECONDS"):
        RelaySettings(
            provider_alert_timeout_seconds=30,
            provider_alert_claim_lease_seconds=30,
        ).validate()


@pytest.mark.parametrize(
    "field_name",
    [
        "provider_monitor_interval_seconds",
        "provider_monitor_window_seconds",
        "provider_monitor_min_success_rate",
        "provider_monitor_widespread_failure_ratio",
        "provider_monitor_lease_seconds",
        "provider_alert_timeout_seconds",
    ],
)
def test_monitor_floating_settings_reject_nan(field_name: str) -> None:
    with pytest.raises(RuntimeError, match="must be finite"):
        RelaySettings(**{field_name: nan}).validate()
DATABASE_URL = os.getenv("RELAY_TEST_DATABASE_URL", "")
MOCK_CAPABILITY_REVISION = asyncio.run(
    ProviderRouter([MockProviderAdapter()]).model_catalog()
).data[0].capability_revision


def _job(prompt: str = "high availability") -> GenerationJob:
    return GenerationJob(
        tenant_id=uuid4(),
        model="mock.video.v1",
        expected_capability_revision=MOCK_CAPABILITY_REVISION,
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt=prompt),
        output=OutputOptions(),
    )


class _FailingRoute(MockProviderAdapter):
    def __init__(
        self,
        *,
        name: str,
        account_id: str,
        channel_type: ProviderChannelType,
        error: ProviderError,
        priority: int = 10,
    ) -> None:
        super().__init__(account_id=account_id, priority=priority)
        self.name = name
        self.channel_type = channel_type
        self.error = error
        self.submit_calls = 0

    async def submit(self, job: GenerationJob) -> ProviderSubmission:
        self.submit_calls += 1
        raise self.error


class _CountingRoute(MockProviderAdapter):
    def __init__(
        self,
        *,
        name: str,
        account_id: str,
        channel_type: ProviderChannelType,
        priority: int = 20,
    ) -> None:
        super().__init__(account_id=account_id, priority=priority)
        self.name = name
        self.channel_type = channel_type
        self.submit_calls = 0

    async def submit(self, job: GenerationJob) -> ProviderSubmission:
        self.submit_calls += 1
        return await super().submit(job)


@pytest.mark.parametrize(
    "error",
    [
        ProviderError(
            "CHANNEL_UNAVAILABLE",
            "The channel proved that no task was created",
            retryable=True,
            account_unavailable=False,
        ),
        ProviderError(
            "ACCOUNT_AUTHENTICATION_FAILED",
            "The account proved that no task was created",
            retryable=False,
            account_unavailable=True,
            disable_account=True,
        ),
        ProviderError(
            "PROVIDER_RATE_LIMITED",
            "The account rejected the request before creating a task",
            retryable=True,
            account_unavailable=True,
            retry_after_seconds=15,
        ),
    ],
    ids=["channel-failure", "invalid-account", "rate-limit"],
)
@pytest.mark.asyncio
async def test_proven_non_creation_fails_over_to_another_channel(
    error: ProviderError,
) -> None:
    """Only an authoritative non-creation signal permits another POST."""

    primary = _FailingRoute(
        name="reverse-primary",
        account_id="account-a",
        channel_type=ProviderChannelType.REVERSE,
        error=error,
    )
    backup = _CountingRoute(
        name="official-backup",
        account_id="account-b",
        channel_type=ProviderChannelType.OFFICIAL,
    )
    pool = InMemoryProviderAccountPool()
    router = ProviderRouter(
        [primary, backup],
        account_pool=pool,
        failure_threshold=1,
        cooldown_seconds=60,
    )
    generation = _job(error.code)

    route_id, submission = await router.submit(generation)

    assert route_id == backup.route_id
    assert submission.provider_task_id == f"mock-{generation.id}"
    assert primary.submit_calls == 1
    assert backup.submit_calls == 1
    assert generation.provider == backup.route_id
    snapshots = await pool.snapshots([primary.route_id, backup.route_id])
    assert snapshots[backup.route_id].active_jobs == 1
    assert snapshots[primary.route_id].active_jobs == 0


@pytest.mark.asyncio
async def test_unknown_submit_outcome_never_fails_over() -> None:
    ambiguous = _FailingRoute(
        name="reverse-ambiguous",
        account_id="account-a",
        channel_type=ProviderChannelType.REVERSE,
        error=ProviderError(
            "SUBMISSION_OUTCOME_UNKNOWN",
            "The channel may already have accepted the task",
            retryable=False,
            account_unavailable=False,
            submission_outcome_unknown=True,
        ),
    )
    forbidden_backup = _CountingRoute(
        name="third-party-must-not-run",
        account_id="account-b",
        channel_type=ProviderChannelType.THIRD_PARTY_API,
    )
    pool = InMemoryProviderAccountPool()
    router = ProviderRouter(
        [ambiguous, forbidden_backup], account_pool=pool
    )
    generation = _job("ambiguous submission")

    with pytest.raises(ProviderError) as caught:
        await router.submit(generation)

    assert caught.value.submission_outcome_unknown is True
    assert caught.value.route_id == ambiguous.route_id
    assert ambiguous.submit_calls == 1
    assert forbidden_backup.submit_calls == 0
    assert generation.provider == ambiguous.route_id
    snapshots = await pool.snapshots(
        [ambiguous.route_id, forbidden_backup.route_id]
    )
    assert snapshots[ambiguous.route_id].active_jobs == 1
    assert snapshots[forbidden_backup.route_id].active_jobs == 0


@pytest.mark.asyncio
async def test_channel_failure_skips_sibling_accounts_in_same_channel() -> None:
    channel_error = ProviderError(
        "CHANNEL_GLOBALLY_RATE_LIMITED",
        "The channel proved that no task was created",
        retryable=True,
        account_unavailable=False,
        failure_scope="channel",
        retry_after_seconds=30,
    )
    primary = _FailingRoute(
        name="reverse-channel",
        account_id="account-a",
        channel_type=ProviderChannelType.REVERSE,
        error=channel_error,
        priority=10,
    )
    sibling_account = _CountingRoute(
        name="reverse-channel",
        account_id="account-b",
        channel_type=ProviderChannelType.REVERSE,
        priority=11,
    )
    other_channel = _CountingRoute(
        name="official-backup",
        account_id="account-c",
        channel_type=ProviderChannelType.OFFICIAL,
        priority=20,
    )
    generation = _job("channel-scoped failure")
    pool = InMemoryProviderAccountPool()
    router = ProviderRouter(
        [primary, sibling_account, other_channel], account_pool=pool
    )

    route_id, _ = await router.submit(generation)

    assert route_id == other_channel.route_id
    assert primary.submit_calls == 1
    assert sibling_account.submit_calls == 0
    assert other_channel.submit_calls == 1

    # The provider-wide cooldown is shared beyond this one routing loop. A
    # later job goes directly to the independent backup without probing every
    # account in the failed channel again.
    second_route_id, _ = await router.submit(
        _job("channel circuit remains open")
    )
    assert second_route_id == other_channel.route_id
    assert primary.submit_calls == 1
    assert sibling_account.submit_calls == 0
    assert other_channel.submit_calls == 2
    snapshots = await pool.snapshots(
        [primary.route_id, sibling_account.route_id]
    )
    assert snapshots[primary.route_id].cooldown_until is not None
    assert snapshots[sibling_account.route_id].cooldown_until is not None


@pytest.mark.asyncio
async def test_channel_cooldown_only_extends_and_preserves_disabled_account_error(
) -> None:
    first = _CountingRoute(
        name="shared-channel",
        account_id="invalid",
        channel_type=ProviderChannelType.REVERSE,
    )
    second = _CountingRoute(
        name="shared-channel",
        account_id="admitted",
        channel_type=ProviderChannelType.REVERSE,
    )
    pool = InMemoryProviderAccountPool()
    await pool.register_routes([first.manifest, second.manifest])
    await pool.record_failure(
        first.route_id,
        error_code="ACCOUNT_REVOKED",
        failure_threshold=1,
        cooldown=timedelta(seconds=5),
        disable_account=True,
    )

    assert await pool.record_channel_failure(
        "shared-channel",
        error_code="CHANNEL_UNAVAILABLE",
        cooldown=timedelta(minutes=5),
    ) == 1
    long_snapshot = (await pool.snapshots([second.route_id]))[second.route_id]
    assert long_snapshot.cooldown_until is not None
    await pool.record_channel_failure(
        "shared-channel",
        error_code="CHANNEL_RETRY",
        cooldown=timedelta(seconds=1),
    )
    snapshots = await pool.snapshots([first.route_id, second.route_id])

    assert snapshots[first.route_id].last_error_code == "ACCOUNT_REVOKED"
    assert snapshots[first.route_id].admission_disabled_reason == "provider_error"
    assert snapshots[second.route_id].cooldown_until >= long_snapshot.cooldown_until


@pytest.mark.asyncio
async def test_sql_channel_cooldown_only_extends_and_skips_disabled_accounts(
    tmp_path,
) -> None:
    repository = SqlAlchemyJobRepository.from_url(
        f"sqlite+aiosqlite:///{tmp_path / 'channel-cooldown.db'}"
    )
    first = _CountingRoute(
        name="shared-channel",
        account_id="invalid",
        channel_type=ProviderChannelType.REVERSE,
    )
    second = _CountingRoute(
        name="shared-channel",
        account_id="admitted",
        channel_type=ProviderChannelType.REVERSE,
    )
    try:
        await repository.create_schema()
        await repository.register_routes([first.manifest, second.manifest])
        await repository.record_failure(
            first.route_id,
            error_code="ACCOUNT_REVOKED",
            failure_threshold=1,
            cooldown=timedelta(seconds=5),
            disable_account=True,
        )
        assert await repository.record_channel_failure(
            "shared-channel",
            error_code="CHANNEL_UNAVAILABLE",
            cooldown=timedelta(minutes=5),
        ) == 1
        long_snapshot = (
            await repository.snapshots([second.route_id])
        )[second.route_id]
        assert long_snapshot.cooldown_until is not None
        await repository.record_channel_failure(
            "shared-channel",
            error_code="CHANNEL_RETRY",
            cooldown=timedelta(seconds=1),
        )
        snapshots = await repository.snapshots(
            [first.route_id, second.route_id]
        )
        assert snapshots[first.route_id].last_error_code == "ACCOUNT_REVOKED"
        assert (
            snapshots[first.route_id].admission_disabled_reason
            == "provider_error"
        )
        assert (
            snapshots[second.route_id].cooldown_until
            >= long_snapshot.cooldown_until
        )
    finally:
        await repository.dispose()


@pytest.mark.asyncio
async def test_route_failure_cannot_shorten_channel_cooldown() -> None:
    route = _CountingRoute(
        name="shared-channel",
        account_id="account-a",
        channel_type=ProviderChannelType.REVERSE,
    )
    pool = InMemoryProviderAccountPool()
    await pool.register_routes([route.manifest])
    await pool.record_channel_failure(
        route.name,
        error_code="CHANNEL_RATE_LIMITED",
        cooldown=timedelta(minutes=5),
    )
    before = (await pool.snapshots([route.route_id]))[route.route_id]
    assert before.cooldown_until is not None

    await pool.record_failure(
        route.route_id,
        error_code="ACCOUNT_TRANSIENT_FAILURE",
        failure_threshold=1,
        cooldown=timedelta(seconds=1),
        disable_account=False,
    )
    after = (await pool.snapshots([route.route_id]))[route.route_id]
    assert after.cooldown_until >= before.cooldown_until


@pytest.mark.asyncio
async def test_sql_route_failure_cannot_shorten_channel_cooldown(tmp_path) -> None:
    repository = SqlAlchemyJobRepository.from_url(
        f"sqlite+aiosqlite:///{tmp_path / 'route-cooldown.db'}"
    )
    route = _CountingRoute(
        name="shared-channel",
        account_id="account-a",
        channel_type=ProviderChannelType.REVERSE,
    )
    try:
        await repository.create_schema()
        await repository.register_routes([route.manifest])
        await repository.record_channel_failure(
            route.name,
            error_code="CHANNEL_RATE_LIMITED",
            cooldown=timedelta(minutes=5),
        )
        before = (
            await repository.snapshots([route.route_id])
        )[route.route_id]
        assert before.cooldown_until is not None

        await repository.record_failure(
            route.route_id,
            error_code="ACCOUNT_TRANSIENT_FAILURE",
            failure_threshold=1,
            cooldown=timedelta(seconds=1),
            disable_account=False,
        )
        after = (
            await repository.snapshots([route.route_id])
        )[route.route_id]
        assert after.cooldown_until >= before.cooldown_until
    finally:
        await repository.dispose()


@pytest.mark.asyncio
async def test_account_failure_can_use_sibling_account_in_same_channel() -> None:
    primary = _FailingRoute(
        name="reverse-channel",
        account_id="account-a",
        channel_type=ProviderChannelType.REVERSE,
        error=ProviderError(
            "ACCOUNT_RATE_LIMITED",
            "The account proved that no task was created",
            retryable=True,
            account_unavailable=True,
            failure_scope="account",
            retry_after_seconds=30,
        ),
        priority=10,
    )
    sibling_account = _CountingRoute(
        name="reverse-channel",
        account_id="account-b",
        channel_type=ProviderChannelType.REVERSE,
        priority=11,
    )
    other_channel = _CountingRoute(
        name="official-backup",
        account_id="account-c",
        channel_type=ProviderChannelType.OFFICIAL,
        priority=20,
    )

    route_id, _ = await ProviderRouter(
        [primary, sibling_account, other_channel]
    ).submit(_job("account-scoped failure"))

    assert route_id == sibling_account.route_id
    assert primary.submit_calls == 1
    assert sibling_account.submit_calls == 1
    assert other_channel.submit_calls == 0


@pytest.mark.asyncio
async def test_request_failure_never_tries_another_route() -> None:
    primary = _FailingRoute(
        name="reverse-channel",
        account_id="account-a",
        channel_type=ProviderChannelType.REVERSE,
        error=ProviderError(
            "INVALID_USER_REQUEST",
            "The user request is invalid",
            retryable=False,
            account_unavailable=False,
            failure_scope="request",
        ),
        priority=10,
    )
    sibling_account = _CountingRoute(
        name="reverse-channel",
        account_id="account-b",
        channel_type=ProviderChannelType.REVERSE,
        priority=11,
    )
    other_channel = _CountingRoute(
        name="official-backup",
        account_id="account-c",
        channel_type=ProviderChannelType.OFFICIAL,
        priority=20,
    )

    with pytest.raises(ProviderError) as caught:
        await ProviderRouter(
            [primary, sibling_account, other_channel]
        ).submit(_job("request-scoped failure"))

    assert caught.value.code == "INVALID_USER_REQUEST"
    assert primary.submit_calls == 1
    assert sibling_account.submit_calls == 0
    assert other_channel.submit_calls == 0


@pytest.mark.asyncio
async def test_unknown_outcome_cannot_enable_channel_failover() -> None:
    try:
        unknown = ProviderError(
            "SUBMISSION_OUTCOME_UNKNOWN",
            "The provider may already have accepted the task",
            retryable=False,
            account_unavailable=False,
            failure_scope="channel",
            submission_outcome_unknown=True,
        )
    except ValueError:
        # Rejecting the contradictory construction is the strongest contract.
        return

    ambiguous = _FailingRoute(
        name="reverse-channel",
        account_id="account-a",
        channel_type=ProviderChannelType.REVERSE,
        error=unknown,
        priority=10,
    )
    forbidden_sibling = _CountingRoute(
        name="reverse-channel",
        account_id="account-b",
        channel_type=ProviderChannelType.REVERSE,
        priority=11,
    )
    forbidden_backup = _CountingRoute(
        name="official-backup",
        account_id="account-c",
        channel_type=ProviderChannelType.OFFICIAL,
        priority=20,
    )
    generation = _job("unknown channel-scoped outcome")

    with pytest.raises(ProviderError) as caught:
        await ProviderRouter(
            [ambiguous, forbidden_sibling, forbidden_backup]
        ).submit(generation)

    assert caught.value.submission_outcome_unknown is True
    assert generation.provider == ambiguous.route_id
    assert forbidden_sibling.submit_calls == 0
    assert forbidden_backup.submit_calls == 0


@pytest.mark.asyncio
async def test_health_probe_preserves_each_route_in_the_same_provider() -> None:
    healthy = MockProviderAdapter(account_id="healthy", healthy=True)
    unhealthy = MockProviderAdapter(account_id="unhealthy", healthy=False)
    router = ProviderRouter([healthy, unhealthy])

    samples = await router.probe_health(at=START)

    assert len(samples) == 2
    by_route = {sample.route_id: sample for sample in samples}
    assert by_route[healthy.route_id].healthy is True
    assert by_route[unhealthy.route_id].healthy is False
    assert {sample.provider_name for sample in samples} == {"mock-video"}
    assert {sample.checked_at for sample in samples} == {START}


@pytest.mark.asyncio
async def test_health_probe_never_persists_adapter_exception_details() -> None:
    secret = "credential=do-not-store https://private.provider.test/ping"

    class LeakyHealthcheck(MockProviderAdapter):
        async def healthcheck(self) -> bool:
            raise RuntimeError(secret)

    provider = LeakyHealthcheck()
    samples = await ProviderRouter([provider]).probe_health(at=START)

    assert len(samples) == 1
    assert samples[0].healthy is False
    assert samples[0].error_code
    assert secret not in repr(samples[0])
    assert "private.provider.test" not in repr(samples[0])


def _test_authenticator() -> StaticClientAuthenticator:
    return StaticClientAuthenticator(
        {
            "health-test-client": ClientCredential(
                tenant_id=uuid4(),
                api_key="health-test-api-key",
            )
        }
    )


def test_all_providers_down_keeps_relay_ready_for_existing_work() -> None:
    app = create_app(
        router=ProviderRouter([MockProviderAdapter(healthy=False)]),
        authenticator=_test_authenticator(),
        settings=RelaySettings(),
        process_in_background=False,
    )

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["state"] == "degraded"
    dependencies = {
        item["name"]: item for item in response.json()["dependencies"]
    }
    assert dependencies["provider:mock-video"]["state"] == "unavailable"
    assert dependencies["repository"]["state"] == "healthy"
    assert dependencies["queue"]["state"] == "healthy"
    assert dependencies["artifact_store"]["state"] == "degraded"


class _UnhealthyRepository(InMemoryJobRepository):
    async def healthcheck(self) -> bool:
        return False


class _UnhealthyQueue(InMemoryWorkQueue):
    async def healthcheck(self) -> bool:
        return False


class _UnhealthyArtifactStore(InMemoryArtifactStore):
    async def healthcheck(self) -> bool:
        return False


@pytest.mark.parametrize(
    ("failed_dependency", "dependency_name"),
    [
        ("repository", "repository"),
        ("queue", "queue"),
        ("artifact_store", "artifact_store"),
    ],
)
def test_local_durable_dependency_failure_still_fails_readiness(
    failed_dependency: str,
    dependency_name: str,
) -> None:
    repository = (
        _UnhealthyRepository()
        if failed_dependency == "repository"
        else InMemoryJobRepository()
    )
    queue = (
        _UnhealthyQueue()
        if failed_dependency == "queue"
        else InMemoryWorkQueue()
    )
    artifact_store = (
        _UnhealthyArtifactStore()
        if failed_dependency == "artifact_store"
        else InMemoryArtifactStore()
    )
    app = create_app(
        repository=repository,
        queue=queue,
        transfer_queue=InMemoryWorkQueue(),
        artifact_store=artifact_store,
        router=ProviderRouter([MockProviderAdapter(healthy=True)]),
        authenticator=_test_authenticator(),
        settings=RelaySettings(),
        process_in_background=False,
    )

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["state"] == "unavailable"
    dependencies = {
        item["name"]: item for item in response.json()["dependencies"]
    }
    assert dependencies[dependency_name]["state"] == "unavailable"


def _sample(
    route_id: str,
    *,
    provider_name: str = "shared-provider",
    healthy: bool = True,
    admission_enabled: bool = True,
    admission_disabled_reason: str | None = None,
    error_code: str | None = None,
    checked_at: datetime = START,
) -> ProviderHealthSample:
    account_id = route_id.rsplit("@", 1)[-1]
    return ProviderHealthSample(
        route_id=route_id,
        provider_name=provider_name,
        account_id=account_id,
        channel_type=ProviderChannelType.REVERSE,
        healthy=healthy,
        admission_enabled=admission_enabled,
        admission_disabled_reason=admission_disabled_reason,
        checked_at=checked_at,
        error_code=error_code,
    )


def _policy(**updates: object) -> ProviderMonitorPolicy:
    values: dict[str, object] = {
        "outcome_window": timedelta(minutes=5),
        "min_outcomes": 4,
        "min_success_rate": 0.75,
        "widespread_failure_ratio": 0.5,
        "batch_disabled_threshold": 2,
        "breach_cycles": 2,
        "recovery_cycles": 2,
        "cycle_lease": timedelta(seconds=30),
    }
    values.update(updates)
    return ProviderMonitorPolicy(**values)


class _ProbeRouter:
    def __init__(self, samples: list[ProviderHealthSample]) -> None:
        self.samples = samples
        self.calls = 0

    async def probe_health(
        self, *, at: datetime | None = None
    ) -> list[ProviderHealthSample]:
        self.calls += 1
        checked_at = at or START
        return [replace(sample, checked_at=checked_at) for sample in self.samples]


async def _events(
    repository: InMemoryProviderMonitoringRepository,
    kind: ProviderAlertKind | None = None,
):
    events = await repository.alert_events()
    if kind is None:
        return events
    return [event for event in events if event.kind == kind]


@pytest.mark.asyncio
async def test_scheduled_cycle_records_one_sample_per_route() -> None:
    router = _ProbeRouter(
        [
            _sample("shared-provider@a"),
            _sample(
                "shared-provider@b",
                healthy=False,
                error_code="HEALTHCHECK_TIMEOUT",
            ),
        ]
    )
    repository = InMemoryProviderMonitoringRepository()
    monitor = ProviderMonitor(
        router,
        repository,
        policy=_policy(),
        worker_id="monitor-a",
    )

    assert await monitor.run_cycle(now=START) is True
    assert await monitor.run_cycle(now=START + timedelta(minutes=1)) is True

    samples = await repository.samples()
    assert router.calls == 2
    assert len(samples) == 4
    assert {sample.route_id for sample in samples} == {
        "shared-provider@a",
        "shared-provider@b",
    }
    assert {sample.checked_at for sample in samples} == {
        START,
        START + timedelta(minutes=1),
    }


@pytest.mark.asyncio
async def test_success_rate_drop_is_deduplicated_and_recovers_once() -> None:
    repository = InMemoryProviderMonitoringRepository()
    router = _ProbeRouter([_sample("shared-provider@a")])
    monitor = ProviderMonitor(
        router,
        repository,
        policy=_policy(),
        worker_id="monitor-a",
    )
    await repository.set_outcome_summaries(
        [
            ProviderOutcomeSummary(
                route_id="shared-provider@a",
                provider_name="shared-provider",
                succeeded=1,
                failed=3,
            )
        ]
    )

    assert await monitor.run_cycle(now=START) is True
    assert await _events(repository, ProviderAlertKind.SUCCESS_RATE_DROP) == []
    assert await monitor.run_cycle(now=START + timedelta(minutes=1)) is True
    assert await monitor.run_cycle(now=START + timedelta(minutes=2)) is True

    breached = await _events(
        repository, ProviderAlertKind.SUCCESS_RATE_DROP
    )
    assert [event.event_type for event in breached] == [
        ProviderAlertEventType.TRIGGERED
    ]
    assert len(await repository.active_alerts()) == 1

    # Recovery also needs two consecutive cycles. Repeated healthy cycles must
    # not produce duplicate recovery notifications.
    await repository.set_outcome_summaries(
        [
            ProviderOutcomeSummary(
                route_id="shared-provider@a",
                provider_name="shared-provider",
                succeeded=4,
                failed=0,
            )
        ]
    )
    assert await monitor.run_cycle(now=START + timedelta(minutes=3)) is True
    assert len(await breached_events(repository)) == 1
    assert await monitor.run_cycle(now=START + timedelta(minutes=4)) is True
    assert await monitor.run_cycle(now=START + timedelta(minutes=5)) is True

    recovered = await _events(
        repository, ProviderAlertKind.SUCCESS_RATE_DROP
    )
    assert [event.event_type for event in recovered] == [
        ProviderAlertEventType.TRIGGERED,
        ProviderAlertEventType.RECOVERED,
    ]
    assert await repository.active_alerts() == []


@pytest.mark.asyncio
async def test_explicit_provider_retirement_resolves_orphaned_alerts() -> None:
    repository = InMemoryProviderMonitoringRepository()
    provider_name = "planned-retirement"
    failing_monitor = ProviderMonitor(
        _ProbeRouter(
            [
                _sample(
                    f"{provider_name}@a",
                    provider_name=provider_name,
                )
            ]
        ),
        repository,
        policy=_policy(breach_cycles=1, recovery_cycles=1),
    )
    await repository.set_outcome_summaries(
        [
            ProviderOutcomeSummary(
                route_id=f"{provider_name}@a",
                provider_name=provider_name,
                succeeded=0,
                failed=4,
            )
        ]
    )
    assert await failing_monitor.run_cycle(now=START) is True
    assert len(await repository.active_alerts()) == 1

    # Merely disappearing from probes is not treated as recovery. Operations
    # must explicitly acknowledge the planned lifecycle removal.
    missing_monitor = ProviderMonitor(
        _ProbeRouter([]),
        repository,
        policy=_policy(breach_cycles=1, recovery_cycles=1),
    )
    assert await missing_monitor.run_cycle(
        now=START + timedelta(minutes=1)
    ) is True
    assert len(await repository.active_alerts()) == 1

    # Recent terminal outcomes remain in the SQL reporting window after a
    # route is removed and must not block the explicit retirement recovery.
    retired_monitor = ProviderMonitor(
        _ProbeRouter([]),
        repository,
        policy=_policy(breach_cycles=1, recovery_cycles=1),
        retired_provider_names=frozenset({provider_name}),
    )
    assert await retired_monitor.run_cycle(
        now=START + timedelta(minutes=2)
    ) is True
    assert await repository.active_alerts() == []
    events = await _events(
        repository, ProviderAlertKind.SUCCESS_RATE_DROP
    )
    assert [event.event_type for event in events] == [
        ProviderAlertEventType.TRIGGERED,
        ProviderAlertEventType.RECOVERED,
    ]
    assert events[-1].details == {"reason": "provider_retired"}


async def breached_events(
    repository: InMemoryProviderMonitoringRepository,
):
    return await _events(repository, ProviderAlertKind.SUCCESS_RATE_DROP)


@pytest.mark.asyncio
async def test_low_outcome_volume_never_opens_success_rate_alert() -> None:
    repository = InMemoryProviderMonitoringRepository()
    router = _ProbeRouter([_sample("shared-provider@a")])
    monitor = ProviderMonitor(
        router,
        repository,
        policy=_policy(min_outcomes=4),
        worker_id="monitor-a",
    )
    await repository.set_outcome_summaries(
        [
            ProviderOutcomeSummary(
                route_id="shared-provider@a",
                provider_name="shared-provider",
                succeeded=0,
                failed=1,
            )
        ]
    )

    for minute in range(4):
        assert await monitor.run_cycle(
            now=START + timedelta(minutes=minute)
        ) is True

    assert await _events(repository, ProviderAlertKind.SUCCESS_RATE_DROP) == []


class _RecordingAlertTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, bytes, dict[str, str], bool]] = []

    async def post(
        self,
        url: str,
        body: bytes,
        headers: dict[str, str],
        *,
        production: bool,
    ) -> int:
        self.requests.append((url, body, headers, production))
        return 204


@pytest.mark.asyncio
async def test_triggered_alert_is_signed_and_delivered_only_once() -> None:
    cycle_time = datetime.now(UTC) - timedelta(minutes=1)
    delivery_time = cycle_time + timedelta(minutes=1)
    repository = InMemoryProviderMonitoringRepository()
    monitor = ProviderMonitor(
        _ProbeRouter([_sample("shared-provider@a")]),
        repository,
        policy=_policy(breach_cycles=1),
        worker_id="monitor-a",
    )
    await repository.set_outcome_summaries(
        [
            ProviderOutcomeSummary(
                route_id="shared-provider@a",
                provider_name="shared-provider",
                succeeded=0,
                failed=4,
            )
        ]
    )
    assert await monitor.run_cycle(now=cycle_time) is True
    transport = _RecordingAlertTransport()
    dispatcher = ProviderAlertDispatcher(
        repository,
        webhook_url="https://alerts.example.test/provider-events",
        signing_secret="alert-signing-secret-at-least-32-bytes",
        production=False,
        transport=transport,
        now=lambda: delivery_time,
    )

    assert await dispatcher.dispatch_once() == 1
    assert await dispatcher.dispatch_once() == 0

    assert len(transport.requests) == 1
    url, body, headers, production = transport.requests[0]
    assert url == "https://alerts.example.test/provider-events"
    assert production is False
    assert headers["X-Relay-Alert-ID"]
    assert headers["X-Relay-Alert-Timestamp"] == str(
        int(delivery_time.timestamp())
    )
    assert headers["X-Relay-Alert-Signature"].startswith("v1=")
    payload = json.loads(body)
    assert payload["type"] == ProviderAlertKind.SUCCESS_RATE_DROP.value
    assert payload["status"] == ProviderAlertEventType.TRIGGERED.value
    assert payload["provider"] == {"name": "shared-provider"}
    assert payload["observed"]["succeeded"] == 0
    assert payload["observed"]["failed"] == 4


@pytest.mark.asyncio
async def test_expired_alert_claim_is_reclaimed_without_spending_attempt() -> None:
    cycle_time = datetime.now(UTC) - timedelta(minutes=1)
    repository = InMemoryProviderMonitoringRepository()
    monitor = ProviderMonitor(
        _ProbeRouter([_sample("shared-provider@a")]),
        repository,
        policy=_policy(breach_cycles=1),
    )
    await repository.set_outcome_summaries(
        [
            ProviderOutcomeSummary(
                route_id="shared-provider@a",
                provider_name="shared-provider",
                succeeded=0,
                failed=4,
            )
        ]
    )
    assert await monitor.run_cycle(now=cycle_time) is True

    first = await repository.claim_provider_alert_deliveries(
        batch_size=1,
        lease=timedelta(seconds=-1),
        max_attempts=1,
    )
    assert len(first) == 1
    second = await repository.claim_provider_alert_deliveries(
        batch_size=1,
        max_attempts=1,
    )
    assert len(second) == 1
    assert second[0].token != first[0].token
    assert second[0].event.attempts == 0

    # Only a completed, known failure spends the delivery budget. An expired
    # claim may have crashed either before or after the external POST, so the
    # same stable event ID must remain eligible for at-least-once redelivery.
    assert await repository.release_provider_alert_delivery(
        second[0].event.id,
        token=second[0].token,
        error="Alert transport failed",
        retry_delay=timedelta(0),
        dead_letter=True,
    ) is True

    status = await repository.provider_monitoring_status()
    assert status.pending_delivery_count == 0
    assert status.dead_letter_count == 1


@pytest.mark.asyncio
async def test_lowered_alert_attempt_limit_dead_letters_pending_event() -> None:
    cycle_time = datetime.now(UTC) - timedelta(minutes=1)
    repository = InMemoryProviderMonitoringRepository()
    monitor = ProviderMonitor(
        _ProbeRouter([_sample("shared-provider@a")]),
        repository,
        policy=_policy(breach_cycles=1),
    )
    await repository.set_outcome_summaries(
        [
            ProviderOutcomeSummary(
                route_id="shared-provider@a",
                provider_name="shared-provider",
                succeeded=0,
                failed=4,
            )
        ]
    )
    assert await monitor.run_cycle(now=cycle_time) is True
    [claim] = await repository.claim_provider_alert_deliveries(
        batch_size=1,
        max_attempts=3,
    )
    assert await repository.release_provider_alert_delivery(
        claim.event.id,
        token=claim.token,
        error="HTTP 503",
        retry_delay=timedelta(0),
        dead_letter=False,
        response_status=503,
    ) is True

    assert await repository.claim_provider_alert_deliveries(
        batch_size=1,
        max_attempts=1,
    ) == []
    status = await repository.provider_monitoring_status()
    assert status.pending_delivery_count == 0
    assert status.dead_letter_count == 1


def test_readiness_surfaces_monitor_freshness_and_alert_backlog(tmp_path) -> None:
    repository = SqlAlchemyJobRepository.from_url(
        f"sqlite+aiosqlite:///{tmp_path / 'monitor-readiness.db'}"
    )
    asyncio.run(repository.create_schema())
    cycle_time = datetime.now(UTC)
    monitor = ProviderMonitor(
        _ProbeRouter([_sample("shared-provider@a")]),
        repository,
        policy=_policy(breach_cycles=1),
    )
    assert asyncio.run(monitor.run_cycle(now=cycle_time)) is True
    settings = RelaySettings(
        runtime_mode="production",
        database_url="postgresql+asyncpg://db/relay",
        redis_url="redis://queue",
        provider_alert_webhook_url="https://alerts.example.com/relay",
        provider_alert_signing_secret="x" * 48,
    )
    app = create_app(
        repository=repository,
        queue=InMemoryWorkQueue(),
        transfer_queue=InMemoryWorkQueue(),
        artifact_store=InMemoryArtifactStore(),
        router=ProviderRouter([MockProviderAdapter()]),
        authenticator=_test_authenticator(),
        settings=settings,
        process_in_background=False,
    )
    try:
        with TestClient(app) as client:
            response = client.get("/health/ready")
        dependencies = {
            item["name"]: item for item in response.json()["dependencies"]
        }
        monitoring = dependencies["provider_monitor"]
        assert monitoring["state"] == "healthy"
        assert monitoring["details"]["last_successful_cycle_at"]
        assert monitoring["details"]["pending_deliveries"] == 0
        assert monitoring["details"]["dead_letter_deliveries"] == 0
    finally:
        asyncio.run(repository.dispose())


@pytest.mark.asyncio
async def test_widespread_failure_counts_routes_not_provider_or_health() -> None:
    """Three unhealthy accounts cannot be hidden by one healthy account."""

    repository = InMemoryProviderMonitoringRepository()
    router = _ProbeRouter(
        [
            _sample("shared-provider@a", healthy=True),
            _sample(
                "shared-provider@b",
                healthy=False,
                error_code="TIMEOUT",
            ),
            _sample(
                "shared-provider@c",
                healthy=False,
                error_code="TIMEOUT",
            ),
            _sample(
                "shared-provider@d",
                healthy=False,
                error_code="TIMEOUT",
            ),
        ]
    )
    monitor = ProviderMonitor(
        router,
        repository,
        policy=_policy(widespread_failure_ratio=0.5),
        worker_id="monitor-a",
    )

    assert await monitor.run_cycle(now=START) is True
    assert await monitor.run_cycle(now=START + timedelta(minutes=1)) is True

    events = await _events(
        repository, ProviderAlertKind.WIDESPREAD_CHANNEL_FAILURE
    )
    assert len(events) == 1
    assert events[0].event_type == ProviderAlertEventType.TRIGGERED


@pytest.mark.asyncio
async def test_batch_account_invalidation_requires_disabled_with_error() -> None:
    repository = InMemoryProviderMonitoringRepository()
    router = _ProbeRouter(
        [
            # A deliberate drain is not an invalid-account signal.
            _sample(
                "shared-provider@draining",
                healthy=False,
                admission_enabled=False,
                admission_disabled_reason="operator_disabled",
                # A historical error must not turn an operator drain into an
                # account-invalid signal.
                error_code="OLD_AUTHENTICATION_ERROR",
            ),
            # A transient health failure that remains admitted is not disabled.
            _sample(
                "shared-provider@transient",
                healthy=False,
                admission_enabled=True,
                error_code="TIMEOUT",
            ),
            _sample(
                "shared-provider@invalid-a",
                healthy=False,
                admission_enabled=False,
                admission_disabled_reason="provider_error",
                error_code="ACCOUNT_REVOKED",
            ),
        ]
    )
    monitor = ProviderMonitor(
        router,
        repository,
        policy=_policy(batch_disabled_threshold=2),
        worker_id="monitor-a",
    )

    for minute in range(3):
        assert await monitor.run_cycle(
            now=START + timedelta(minutes=minute)
        ) is True
    assert await _events(
        repository, ProviderAlertKind.BATCH_ACCOUNT_INVALIDATION
    ) == []

    router.samples.append(
        _sample(
            "shared-provider@invalid-b",
            healthy=False,
            admission_enabled=False,
            admission_disabled_reason="provider_error",
            error_code="AUTHENTICATION_FAILED",
        )
    )
    assert await monitor.run_cycle(now=START + timedelta(minutes=3)) is True
    assert await monitor.run_cycle(now=START + timedelta(minutes=4)) is True

    events = await _events(
        repository, ProviderAlertKind.BATCH_ACCOUNT_INVALIDATION
    )
    assert len(events) == 1
    assert events[0].event_type == ProviderAlertEventType.TRIGGERED


class _BlockingProbeRouter(_ProbeRouter):
    def __init__(self, samples: list[ProviderHealthSample]) -> None:
        super().__init__(samples)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def probe_health(
        self, *, at: datetime | None = None
    ) -> list[ProviderHealthSample]:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        checked_at = at or START
        return [replace(sample, checked_at=checked_at) for sample in self.samples]


@pytest.mark.asyncio
async def test_second_monitor_skips_cycle_when_shared_lease_is_owned() -> None:
    repository = InMemoryProviderMonitoringRepository()
    router = _BlockingProbeRouter([_sample("shared-provider@a")])
    policy = _policy(cycle_lease=timedelta(seconds=30))
    first = ProviderMonitor(
        router,
        repository,
        policy=policy,
        worker_id="monitor-a",
    )
    second = ProviderMonitor(
        router,
        repository,
        policy=policy,
        worker_id="monitor-b",
    )

    first_cycle = asyncio.create_task(first.run_cycle(now=START))
    await router.entered.wait()
    assert await second.run_cycle(now=START) is False
    router.release.set()
    assert await first_cycle is True

    assert router.calls == 1
    samples = await repository.samples()
    assert len(samples) == 1
    assert samples[0].route_id == "shared-provider@a"


@pytest.mark.asyncio
async def test_expired_monitor_owner_cannot_commit_after_takeover() -> None:
    """A stale probe result must not recover or overwrite a newer incident."""

    repository = InMemoryProviderMonitoringRepository()
    stale_router = _BlockingProbeRouter(
        [
            _sample("shared-provider@a", healthy=True),
            _sample("shared-provider@b", healthy=True),
        ]
    )
    current_router = _ProbeRouter(
        [
            _sample(
                "shared-provider@a", healthy=False, error_code="TIMEOUT"
            ),
            _sample(
                "shared-provider@b", healthy=False, error_code="TIMEOUT"
            ),
        ]
    )
    policy = _policy(
        cycle_lease=timedelta(seconds=1),
        cycle_interval=timedelta(seconds=1),
        breach_cycles=1,
        recovery_cycles=1,
    )
    stale_monitor = ProviderMonitor(
        stale_router,
        repository,
        policy=policy,
        worker_id="stale-monitor",
    )
    current_monitor = ProviderMonitor(
        current_router,
        repository,
        policy=policy,
        worker_id="current-monitor",
    )

    stale_cycle = asyncio.create_task(stale_monitor.run_cycle(now=START))
    await stale_router.entered.wait()
    assert await current_monitor.run_cycle(
        now=START + timedelta(seconds=2)
    ) is True
    stale_router.release.set()
    await stale_cycle

    samples = await repository.samples()
    assert len(samples) == 2
    assert {sample.checked_at for sample in samples} == {
        START + timedelta(seconds=2)
    }
    events = await _events(
        repository, ProviderAlertKind.WIDESPREAD_CHANNEL_FAILURE
    )
    assert [event.event_type for event in events] == [
        ProviderAlertEventType.TRIGGERED
    ]


@pytest.mark.asyncio
async def test_sql_monitor_lease_fences_stale_worker_across_repositories(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite+aiosqlite:///{tmp_path / 'provider-monitor-lease.db'}"
    )
    stale_repository = SqlAlchemyJobRepository.from_url(database_url)
    current_repository = SqlAlchemyJobRepository.from_url(database_url)
    await stale_repository.create_schema()
    stale_router = _BlockingProbeRouter(
        [
            _sample("shared-provider@a", healthy=True),
            _sample("shared-provider@b", healthy=True),
        ]
    )
    current_router = _ProbeRouter(
        [
            _sample(
                "shared-provider@a", healthy=False, error_code="TIMEOUT"
            ),
            _sample(
                "shared-provider@b", healthy=False, error_code="TIMEOUT"
            ),
        ]
    )
    policy = _policy(
        cycle_lease=timedelta(seconds=1),
        cycle_interval=timedelta(seconds=1),
        breach_cycles=1,
        recovery_cycles=1,
    )
    stale_monitor = ProviderMonitor(
        stale_router,
        stale_repository,
        policy=policy,
        worker_id="sql-stale-monitor",
    )
    current_monitor = ProviderMonitor(
        current_router,
        current_repository,
        policy=policy,
        worker_id="sql-current-monitor",
    )

    try:
        stale_cycle = asyncio.create_task(
            stale_monitor.run_cycle(now=START)
        )
        await stale_router.entered.wait()
        assert await current_monitor.run_cycle(
            now=START + timedelta(seconds=2)
        ) is True
        stale_router.release.set()
        await stale_cycle

        events = await current_repository.list_provider_alert_events()
        widespread = [
            event
            for event in events
            if event.kind
            == ProviderAlertKind.WIDESPREAD_CHANNEL_FAILURE
        ]
        assert [event.event_type for event in widespread] == [
            ProviderAlertEventType.TRIGGERED
        ]
        async with current_repository.engine.connect() as connection:
            sample_count = await connection.scalar(
                text("SELECT COUNT(*) FROM provider_health_samples")
            )
        assert sample_count == 2
    finally:
        await stale_repository.engine.dispose()
        await current_repository.engine.dispose()


@pytest.mark.asyncio
async def test_postgres_first_monitor_claim_uses_database_clock_once() -> None:
    if not DATABASE_URL.startswith("postgresql+asyncpg://"):
        pytest.skip("requires RELAY_TEST_DATABASE_URL with asyncpg")

    schema = f"relay_monitor_clock_{uuid4().hex}"
    administration_engine = create_async_engine(
        DATABASE_URL, pool_pre_ping=True
    )
    async with administration_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    connect_args = {"server_settings": {"search_path": schema}}
    first_engine = create_async_engine(
        DATABASE_URL, pool_pre_ping=True, connect_args=connect_args
    )
    second_engine = create_async_engine(
        DATABASE_URL, pool_pre_ping=True, connect_args=connect_args
    )
    first_repository = SqlAlchemyJobRepository(first_engine)
    second_repository = SqlAlchemyJobRepository(second_engine)
    try:
        await first_repository.create_schema()
        first_claim, second_claim = await asyncio.gather(
            first_repository.claim_provider_monitor_cycle(
                worker_id="clock-fast",
                now=START + timedelta(days=3650),
                lease=timedelta(seconds=30),
                minimum_interval=timedelta(seconds=30),
            ),
            second_repository.claim_provider_monitor_cycle(
                worker_id="clock-slow",
                now=START - timedelta(days=3650),
                lease=timedelta(seconds=30),
                minimum_interval=timedelta(seconds=30),
            ),
        )
        claims = [
            claim for claim in (first_claim, second_claim) if claim is not None
        ]
        assert len(claims) == 1
        async with first_engine.connect() as connection:
            database_now = await connection.scalar(
                text("SELECT clock_timestamp()")
            )
            lease_row = (
                await connection.execute(
                    text(
                        "SELECT COUNT(*), MAX(claim_expires_at) "
                        "FROM provider_monitor_lease"
                    )
                )
            ).one()
        assert database_now is not None
        assert abs((claims[0].observed_at - database_now).total_seconds()) < 5
        assert lease_row[0] == 1
        assert 20 <= (
            lease_row[1] - claims[0].observed_at
        ).total_seconds() <= 35
    finally:
        await first_engine.dispose()
        await second_engine.dispose()
        async with administration_engine.begin() as connection:
            await connection.execute(
                text(f'DROP SCHEMA "{schema}" CASCADE')
            )
        await administration_engine.dispose()


@pytest.mark.asyncio
async def test_postgres_monitor_cycle_is_single_owner_and_token_fenced() -> None:
    if not DATABASE_URL.startswith("postgresql+asyncpg://"):
        pytest.skip("requires RELAY_TEST_DATABASE_URL with asyncpg")

    schema = f"relay_monitor_{uuid4().hex}"
    assert re.fullmatch(r"relay_monitor_[0-9a-f]{32}", schema)
    administration_engine = create_async_engine(
        DATABASE_URL, pool_pre_ping=True
    )
    async with administration_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    connect_args = {"server_settings": {"search_path": schema}}
    stale_engine = create_async_engine(
        DATABASE_URL, pool_pre_ping=True, connect_args=connect_args
    )
    current_engine = create_async_engine(
        DATABASE_URL, pool_pre_ping=True, connect_args=connect_args
    )
    stale_repository = SqlAlchemyJobRepository(
        stale_engine, use_database_coordination_clock=False
    )
    current_repository = SqlAlchemyJobRepository(
        current_engine, use_database_coordination_clock=False
    )
    stale_router = _BlockingProbeRouter(
        [
            _sample("shared-provider@a", healthy=True),
            _sample("shared-provider@b", healthy=True),
        ]
    )
    current_router = _ProbeRouter(
        [
            _sample(
                "shared-provider@a", healthy=False, error_code="TIMEOUT"
            ),
            _sample(
                "shared-provider@b", healthy=False, error_code="TIMEOUT"
            ),
        ]
    )
    policy = _policy(
        cycle_lease=timedelta(seconds=1),
        cycle_interval=timedelta(seconds=1),
        breach_cycles=1,
        recovery_cycles=1,
    )
    stale_monitor = ProviderMonitor(
        stale_router,
        stale_repository,
        policy=policy,
        worker_id="postgres-stale-monitor",
    )
    current_monitor = ProviderMonitor(
        current_router,
        current_repository,
        policy=policy,
        worker_id="postgres-current-monitor",
    )

    try:
        await stale_repository.create_schema()
        stale_cycle = asyncio.create_task(
            stale_monitor.run_cycle(now=START)
        )
        await stale_router.entered.wait()
        assert await current_monitor.run_cycle(
            now=START + timedelta(seconds=2)
        ) is True
        stale_router.release.set()
        await stale_cycle

        events = await current_repository.list_provider_alert_events()
        widespread = [
            event
            for event in events
            if event.kind
            == ProviderAlertKind.WIDESPREAD_CHANNEL_FAILURE
        ]
        assert [event.event_type for event in widespread] == [
            ProviderAlertEventType.TRIGGERED
        ]
        async with current_engine.connect() as connection:
            sample_count = await connection.scalar(
                text("SELECT COUNT(*) FROM provider_health_samples")
            )
        assert sample_count == 2
    finally:
        stale_router.release.set()
        await stale_engine.dispose()
        await current_engine.dispose()
        async with administration_engine.begin() as connection:
            await connection.execute(
                text(f'DROP SCHEMA "{schema}" CASCADE')
            )
        await administration_engine.dispose()
