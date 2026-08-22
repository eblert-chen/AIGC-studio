from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from platform_api.database import Base
from platform_api.config import Settings
from platform_api.models import (
    Company,
    CompanyResourceGrant,
    PublicationAttempt,
    PublicationAttemptStatus,
    PublicationJob,
    PublicationJobStatus,
    PublisherConnection,
    PublisherConnectionStatus,
    ResourceDefinition,
    ResourceKind,
    TaskArtifact,
    User,
)
from platform_api.publishing_adapters import (
    MockPublisherAdapter,
    PublicationReceipt,
    PublisherAdapterRegistry,
    PublisherReauthenticationRequired,
    SubmissionOutcomeUnknownError,
    TemporaryPreSubmissionError,
    build_publisher_registry,
)
from platform_api.publishing_worker import (
    PublicationArtifact,
    PublishingWorker,
    _adapter_specs,
    exponential_backoff_seconds,
)


UTC = timezone.utc


class FixedClock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FixtureMediaResolver:
    def resolve(self, artifact: PublicationArtifact) -> tuple[str, ...]:
        return (f"https://assets.example.test/{artifact.asset_id}",)


class FixtureAdapter:
    provider = "fixture"
    production_ready = True
    is_mock = False

    def __init__(self, outcome: object | None = None, *, on_submit=None):
        self.outcome = outcome
        self.on_submit = on_submit
        self.calls = 0
        self.requests = []

    def submit(self, request):
        self.calls += 1
        self.requests.append(request)
        if self.on_submit is not None:
            self.on_submit(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        if self.outcome is not None:
            return self.outcome
        return PublicationReceipt(
            external_post_id="post-123",
            external_post_url="https://social.example.test/posts/post-123",
            provider_request_id="request-123",
            response_metadata={"visibility": "public"},
        )


class FinalGateMutationWorker(PublishingWorker):
    """Commits a deterministic administration change after preparation."""

    def __init__(self, *args, before_final_gate, **kwargs):
        super().__init__(*args, **kwargs)
        self.before_final_gate = before_final_gate

    def _authorize_and_mark_submit_started(self, claimed):
        self.before_final_gate()
        return super()._authorize_and_mark_submit_started(claimed)


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()


def _seed_job(
    session_factory,
    *,
    now: datetime,
    status: PublicationJobStatus = PublicationJobStatus.QUEUED,
) -> tuple[str, str]:
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    connection_id = str(uuid.uuid4())
    artifact_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    with session_factory.begin() as session:
        session.add(Company(id=company_id, name="Publishing test company"))
        session.add(
            User(
                id=user_id,
                email=f"{user_id}@example.test",
                display_name="Publishing Tester",
            )
        )
        resource = ResourceDefinition(
            key="feature.auto_publish",
            kind=ResourceKind.FEATURE,
            display_name="Automatic publishing",
            active=True,
        )
        session.add(resource)
        session.flush()
        session.add(
            CompanyResourceGrant(
                company_id=company_id,
                resource_id=resource.id,
                enabled=True,
                config_override={},
            )
        )
        session.add(
            TaskArtifact(
                id=artifact_id,
                company_id=company_id,
                task_id=str(uuid.uuid4()),
                asset_id="stored-video.mp4",
                position=0,
                media_type="video",
                content_type="video/mp4",
                size_bytes=1024,
                sha256="a" * 64,
                created_at=now,
            )
        )
        session.add(
            PublisherConnection(
                id=connection_id,
                company_id=company_id,
                created_by_user_id=user_id,
                provider="fixture",
                display_name="Fixture account",
                external_account_id="external-account-1",
                status=PublisherConnectionStatus.ACTIVE,
                config={"credential_reference": "secret://fixture/one"},
            )
        )
        session.add(
            PublicationJob(
                id=job_id,
                company_id=company_id,
                created_by_user_id=user_id,
                task_artifact_id=artifact_id,
                connection_id=connection_id,
                idempotency_key="publish-once",
                request_fingerprint="b" * 64,
                status=status,
                title="Product launch",
                caption="A generated product video",
                scheduled_at=now if status == PublicationJobStatus.SCHEDULED else None,
                next_attempt_at=now if status == PublicationJobStatus.QUEUED else None,
            )
        )
    return job_id, connection_id


def _worker(
    session_factory,
    adapter,
    clock,
    *,
    worker_type=PublishingWorker,
    **overrides,
) -> PublishingWorker:
    registry = PublisherAdapterRegistry(environment="test", adapters=[adapter])
    return worker_type(
        session_factory,
        registry,
        FixtureMediaResolver(),
        lease_owner="test-worker",
        clock=clock,
        **overrides,
    )


def test_production_registry_rejects_mock_and_unready_adapters():
    with pytest.raises(ValueError, match="not production-ready"):
        build_publisher_registry(
            environment="production",
            include_mock=True,
        )

    unready = FixtureAdapter()
    unready.production_ready = False
    with pytest.raises(ValueError, match="not production-ready"):
        PublisherAdapterRegistry(
            environment="production",
            adapters=[unready],
        )

    development = build_publisher_registry(
        environment="development",
        include_mock=True,
    )
    assert isinstance(development.require("mock"), MockPublisherAdapter)


def test_exponential_backoff_is_bounded():
    assert exponential_backoff_seconds(1) == 5
    assert exponential_backoff_seconds(2) == 10
    assert exponential_backoff_seconds(20, cap_seconds=300) == 300
    with pytest.raises(ValueError):
        exponential_backoff_seconds(0)


def test_worker_adapter_and_resolver_configuration_comes_from_settings():
    settings = Settings(
        publishing_worker_enabled=True,
        publishing_adapters=" package.one:create ,package.two:create ",
        publishing_media_resolver="package.media:create",
    )
    assert _adapter_specs(settings.publishing_adapters) == [
        "package.one:create",
        "package.two:create",
    ]
    assert settings.publishing_media_resolver == "package.media:create"


def test_successful_publication_is_fenced_and_appends_attempt(session_factory):
    now = datetime(2026, 8, 7, 1, 0, tzinfo=UTC)
    job_id, _ = _seed_job(session_factory, now=now)
    adapter = FixtureAdapter()

    result = _worker(session_factory, adapter, FixedClock(now)).run_once()

    assert result.processed is True
    assert result.status == PublicationJobStatus.PUBLISHED.value
    assert adapter.calls == 1
    assert adapter.requests[0].idempotency_key == job_id
    assert adapter.requests[0].media_urls == (
        "https://assets.example.test/stored-video.mp4",
    )
    with session_factory() as session:
        job = session.get(PublicationJob, job_id)
        assert job is not None
        assert job.status == PublicationJobStatus.PUBLISHED
        assert job.external_post_id == "post-123"
        assert job.external_post_url.endswith("/post-123")
        assert job.lease_token is None
        attempts = session.scalars(
            select(PublicationAttempt).where(PublicationAttempt.job_id == job_id)
        ).all()
        assert len(attempts) == 1
        assert attempts[0].status == PublicationAttemptStatus.PUBLISHED
        assert attempts[0].provider_request_id == "request-123"
        assert attempts[0].response_payload == {
            "provider_request_id": "request-123",
            "visibility": "public",
        }


def test_final_gate_marker_is_committed_before_adapter_call(session_factory):
    now = datetime(2026, 8, 7, 1, 30, tzinfo=UTC)
    job_id, _ = _seed_job(session_factory, now=now)
    observed_submit_started_at = []

    def observe_marker(_request):
        with session_factory() as session:
            job = session.get(PublicationJob, job_id)
            assert job is not None
            observed_submit_started_at.append(job.submit_started_at)

    adapter = FixtureAdapter(on_submit=observe_marker)

    result = _worker(session_factory, adapter, FixedClock(now)).run_once()

    assert result.status == PublicationJobStatus.PUBLISHED.value
    assert observed_submit_started_at == [now.replace(tzinfo=None)]


def test_proven_pre_submission_failure_retries_with_backoff(session_factory):
    now = datetime(2026, 8, 7, 2, 0, tzinfo=UTC)
    job_id, _ = _seed_job(session_factory, now=now)
    adapter = FixtureAdapter(
        TemporaryPreSubmissionError("provider_busy", "Provider is busy")
    )

    result = _worker(
        session_factory,
        adapter,
        FixedClock(now),
        backoff_base_seconds=7,
    ).run_once()

    assert result.status == PublicationJobStatus.QUEUED.value
    with session_factory() as session:
        job = session.get(PublicationJob, job_id)
        assert job is not None
        assert job.status == PublicationJobStatus.QUEUED
        # SQLite drops timezone metadata, so compare the wall-clock value.
        assert job.next_attempt_at == (now + timedelta(seconds=7)).replace(
            tzinfo=None
        )
        attempt = session.scalar(
            select(PublicationAttempt).where(PublicationAttempt.job_id == job_id)
        )
        assert attempt is not None
        assert attempt.status == PublicationAttemptStatus.FAILED
        assert attempt.error_code == "provider_busy"


def test_unknown_submission_is_never_automatically_retried(session_factory):
    now = datetime(2026, 8, 7, 3, 0, tzinfo=UTC)
    clock = FixedClock(now)
    job_id, _ = _seed_job(session_factory, now=now)
    adapter = FixtureAdapter(
        SubmissionOutcomeUnknownError(
            "provider_timeout_after_send",
            "Provider response was not received",
        )
    )
    worker = _worker(session_factory, adapter, clock)

    first = worker.run_once()
    clock.value += timedelta(days=30)
    second = worker.run_once()

    assert first.status == PublicationJobStatus.SUBMISSION_UNKNOWN.value
    assert second.processed is False
    assert adapter.calls == 1
    with session_factory() as session:
        job = session.get(PublicationJob, job_id)
        assert job is not None
        assert job.status == PublicationJobStatus.SUBMISSION_UNKNOWN
        assert job.next_attempt_at is None
        assert job.attempt_count == 1
        assert session.scalar(
            select(func.count(PublicationAttempt.id)).where(
                PublicationAttempt.job_id == job_id
            )
        ) == 1


def test_expired_post_started_lease_is_quarantined_without_resubmit(
    session_factory,
):
    now = datetime(2026, 8, 7, 4, 0, tzinfo=UTC)
    job_id, _ = _seed_job(session_factory, now=now)
    old_token = uuid.uuid4().hex
    with session_factory.begin() as session:
        job = session.get(PublicationJob, job_id)
        assert job is not None
        job.status = PublicationJobStatus.SUBMITTING
        job.attempt_count = 1
        job.lease_owner = "dead-worker"
        job.lease_token = old_token
        job.lease_expires_at = now - timedelta(seconds=1)
        job.submit_started_at = now - timedelta(seconds=10)
        session.add(
            PublicationAttempt(
                company_id=job.company_id,
                job_id=job.id,
                attempt_number=1,
                status=PublicationAttemptStatus.SUBMITTING,
                lease_token=old_token,
                started_at=now - timedelta(seconds=10),
            )
        )
    adapter = FixtureAdapter()

    result = _worker(session_factory, adapter, FixedClock(now)).run_once()

    assert result.status == PublicationJobStatus.SUBMISSION_UNKNOWN.value
    assert adapter.calls == 0
    with session_factory() as session:
        job = session.get(PublicationJob, job_id)
        attempt = session.scalar(
            select(PublicationAttempt).where(PublicationAttempt.job_id == job_id)
        )
        assert job is not None and attempt is not None
        assert job.lease_token is None
        assert attempt.status == PublicationAttemptStatus.SUBMISSION_UNKNOWN


def test_reauthentication_failure_marks_job_and_connection(session_factory):
    now = datetime(2026, 8, 7, 5, 0, tzinfo=UTC)
    job_id, connection_id = _seed_job(session_factory, now=now)
    adapter = FixtureAdapter(
        PublisherReauthenticationRequired(
            "provider_token_expired",
            "Publisher authorization expired",
        )
    )

    result = _worker(session_factory, adapter, FixedClock(now)).run_once()

    assert result.status == PublicationJobStatus.REQUIRES_REAUTH.value
    with session_factory() as session:
        job = session.get(PublicationJob, job_id)
        connection = session.get(PublisherConnection, connection_id)
        assert job is not None and connection is not None
        assert job.status == PublicationJobStatus.REQUIRES_REAUTH
        assert connection.status == PublisherConnectionStatus.REQUIRES_REAUTH


def test_revoked_entitlement_fails_claim_without_calling_adapter(session_factory):
    now = datetime(2026, 8, 7, 5, 30, tzinfo=UTC)
    job_id, _ = _seed_job(session_factory, now=now)
    with session_factory.begin() as session:
        grant = session.scalar(select(CompanyResourceGrant))
        assert grant is not None
        grant.enabled = False
    adapter = FixtureAdapter()

    result = _worker(session_factory, adapter, FixedClock(now)).run_once()

    assert result.status == PublicationJobStatus.FAILED.value
    assert adapter.calls == 0
    with session_factory() as session:
        job = session.get(PublicationJob, job_id)
        attempt = session.scalar(
            select(PublicationAttempt).where(PublicationAttempt.job_id == job_id)
        )
        assert job is not None and attempt is not None
        assert job.error_code == "auto_publish_entitlement_revoked"
        assert attempt.status == PublicationAttemptStatus.FAILED
        assert attempt.error_code == "auto_publish_entitlement_revoked"


@pytest.mark.parametrize("policy_state", ["not_yet_effective", "expired"])
def test_final_gate_enforces_entitlement_schedule(
    session_factory,
    policy_state,
):
    now = datetime(2026, 8, 7, 5, 35, tzinfo=UTC)
    job_id, _ = _seed_job(session_factory, now=now)
    with session_factory.begin() as session:
        grant = session.scalar(select(CompanyResourceGrant))
        assert grant is not None
        if policy_state == "not_yet_effective":
            grant.effective_at = now + timedelta(seconds=1)
        else:
            grant.expires_at = now
    adapter = FixtureAdapter()

    result = _worker(session_factory, adapter, FixedClock(now)).run_once()

    assert result.status == PublicationJobStatus.FAILED.value
    assert adapter.calls == 0
    with session_factory() as session:
        job = session.get(PublicationJob, job_id)
        assert job is not None
        assert job.submit_started_at is None
        assert job.error_code == "auto_publish_entitlement_revoked"


@pytest.mark.parametrize(
    ("administration_change", "expected_error"),
    [
        ("revoke_entitlement", "auto_publish_entitlement_revoked"),
        ("disable_connection", "publisher_connection_inactive"),
    ],
)
def test_final_gate_blocks_change_committed_after_prepare_before_post(
    session_factory,
    administration_change,
    expected_error,
):
    now = datetime(2026, 8, 7, 5, 40, tzinfo=UTC)
    job_id, connection_id = _seed_job(session_factory, now=now)

    def mutate_after_prepare():
        with session_factory.begin() as session:
            if administration_change == "revoke_entitlement":
                grant = session.scalar(select(CompanyResourceGrant))
                assert grant is not None
                grant.enabled = False
            else:
                connection = session.get(PublisherConnection, connection_id)
                assert connection is not None
                connection.status = PublisherConnectionStatus.DISABLED
                connection.disabled_at = now

    adapter = FixtureAdapter()
    worker = _worker(
        session_factory,
        adapter,
        FixedClock(now),
        worker_type=FinalGateMutationWorker,
        before_final_gate=mutate_after_prepare,
    )

    result = worker.run_once()

    assert result.status == PublicationJobStatus.FAILED.value
    assert adapter.calls == 0
    with session_factory() as session:
        job = session.get(PublicationJob, job_id)
        attempt = session.scalar(
            select(PublicationAttempt).where(PublicationAttempt.job_id == job_id)
        )
        assert job is not None and attempt is not None
        assert job.submit_started_at is None
        assert job.error_code == expected_error
        assert attempt.error_code == expected_error
        assert attempt.status == PublicationAttemptStatus.FAILED


def test_expired_last_pre_submit_claim_is_failed_instead_of_stuck(
    session_factory,
):
    now = datetime(2026, 8, 7, 5, 45, tzinfo=UTC)
    job_id, _ = _seed_job(session_factory, now=now)
    old_token = uuid.uuid4().hex
    with session_factory.begin() as session:
        job = session.get(PublicationJob, job_id)
        assert job is not None
        job.status = PublicationJobStatus.SUBMITTING
        job.attempt_count = 1
        job.lease_owner = "crashed-worker"
        job.lease_token = old_token
        job.lease_expires_at = now - timedelta(seconds=1)
        job.submit_started_at = None
        session.add(
            PublicationAttempt(
                company_id=job.company_id,
                job_id=job.id,
                attempt_number=1,
                status=PublicationAttemptStatus.SUBMITTING,
                lease_token=old_token,
                started_at=now - timedelta(seconds=30),
            )
        )
    adapter = FixtureAdapter()

    result = _worker(
        session_factory,
        adapter,
        FixedClock(now),
        max_attempts=1,
    ).run_once()

    assert result.status == PublicationJobStatus.FAILED.value
    assert adapter.calls == 0
    with session_factory() as session:
        job = session.get(PublicationJob, job_id)
        attempt = session.scalar(
            select(PublicationAttempt).where(PublicationAttempt.job_id == job_id)
        )
        assert job is not None and attempt is not None
        assert job.error_code == "publication_attempt_limit_exhausted"
        assert job.lease_token is None
        assert attempt.status == PublicationAttemptStatus.FAILED
        assert attempt.finished_at is not None
