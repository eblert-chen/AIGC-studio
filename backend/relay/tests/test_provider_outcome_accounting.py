from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from relay_service.models import (
    ErrorDetail,
    GenerationInputs,
    GenerationJob,
    GenerationMode,
    JobStatus,
    OutputOptions,
    SubmissionReconciliationRequest,
    TransferSource,
    WorkItem,
)
from relay_service.providers.mock import MockProviderAdapter
from relay_service.providers.router import ProviderRouter
from relay_service.queue import InMemoryWorkQueue
from relay_service.service import GenerationService
from relay_service.sql_repository import (
    JobRow,
    OutboxRow,
    ProviderOutcomeRow,
    SqlAlchemyJobRepository,
    WebhookEventRow,
)


def _processing_job(provider: MockProviderAdapter, reference: str) -> GenerationJob:
    return GenerationJob(
        tenant_id=uuid4(),
        client_reference_id=reference,
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="Account for the upstream result once"),
        output=OutputOptions(),
        status=JobStatus.PROCESSING,
        progress=30,
        provider=provider.route_id,
        provider_task_id=f"upstream-{uuid4()}",
    )


def _transferring(job: GenerationJob) -> GenerationJob:
    replacement = job.model_copy(deep=True)
    asset_id = uuid4()
    replacement.status = JobStatus.TRANSFERRING
    replacement.progress = 95
    replacement.transfer_sources = [
        TransferSource(
            asset_id=asset_id,
            source_url="https://provider.example.test/temporary.mp4",
            media_type="video",
            object_key=f"outputs/{job.tenant_id}/{job.id}/{asset_id}",
        )
    ]
    return replacement


async def _persist_job(
    repository: SqlAlchemyJobRepository,
    provider: MockProviderAdapter,
    reference: str,
) -> GenerationJob:
    await repository.register_routes([provider.manifest])
    job = _processing_job(provider, reference)
    stored, replayed, conflict = await repository.create_idempotent(
        job, f"idempotency-{reference}", f"hash-{reference}"
    )
    assert replayed is False
    assert conflict is False
    return stored


async def _outcomes(
    repository: SqlAlchemyJobRepository,
) -> list[ProviderOutcomeRow]:
    async with repository.sessions() as session:
        return list(
            (
                await session.scalars(
                    select(ProviderOutcomeRow).order_by(
                        ProviderOutcomeRow.occurred_at
                    )
                )
            ).all()
        )


@pytest.mark.asyncio
async def test_sql_success_is_recorded_once_with_transfer_transition(
    tmp_path,
) -> None:
    repository = SqlAlchemyJobRepository.from_url(
        f"sqlite+aiosqlite:///{tmp_path / 'provider-success.db'}"
    )
    await repository.create_schema()
    try:
        provider = MockProviderAdapter()
        job = await _persist_job(repository, provider, "success")
        transferring = _transferring(job)

        assert await repository.begin_artifact_transfer(
            transferring,
            provider.name,
            "evt-success",
            WorkItem(job_id=job.id),
        ) == (True, True)

        # An at-least-once provider webhook cannot create a second outcome or
        # a second durable artifact-transfer message.
        assert await repository.begin_artifact_transfer(
            transferring,
            provider.name,
            "evt-success",
            WorkItem(job_id=job.id),
        ) == (False, False)

        async with repository.sessions() as session:
            persisted_status = await session.scalar(
                select(JobRow.status).where(JobRow.id == str(job.id))
            )
            outcome_count = await session.scalar(
                select(func.count()).select_from(ProviderOutcomeRow)
            )
            webhook_count = await session.scalar(
                select(func.count()).select_from(WebhookEventRow)
            )
            transfer_message_count = await session.scalar(
                select(func.count())
                .select_from(OutboxRow)
                .where(OutboxRow.topic == "artifact.transfer")
            )

        assert persisted_status == JobStatus.TRANSFERRING.value
        assert outcome_count == 1
        assert webhook_count == 1
        assert transfer_message_count == 1
        [outcome] = await _outcomes(repository)
        assert outcome.job_id == str(job.id)
        assert outcome.route_id == provider.route_id
        assert outcome.provider_name == provider.name
        assert outcome.channel_type == provider.channel_type.value
        assert outcome.succeeded is True
    finally:
        await repository.dispose()


class _FailOnceDuringTransferRepository(SqlAlchemyJobRepository):
    def __init__(self, engine) -> None:
        super().__init__(engine)
        self.fail_next_transfer = True

    def _apply_model(self, row: JobRow, job: GenerationJob) -> None:
        if self.fail_next_transfer and job.status == JobStatus.TRANSFERRING:
            self.fail_next_transfer = False
            raise RuntimeError("injected transfer transition failure")
        super()._apply_model(row, job)


@pytest.mark.asyncio
async def test_sql_success_outcome_rolls_back_with_transfer_transaction(
    tmp_path,
) -> None:
    repository = _FailOnceDuringTransferRepository.from_url(
        f"sqlite+aiosqlite:///{tmp_path / 'provider-success-atomic.db'}"
    )
    await repository.create_schema()
    try:
        provider = MockProviderAdapter()
        job = await _persist_job(repository, provider, "success-atomic")
        transferring = _transferring(job)

        with pytest.raises(
            RuntimeError, match="injected transfer transition failure"
        ):
            await repository.begin_artifact_transfer(
                transferring,
                provider.name,
                "evt-success-atomic",
                WorkItem(job_id=job.id),
            )

        async with repository.sessions() as session:
            assert await session.scalar(
                select(JobRow.status).where(JobRow.id == str(job.id))
            ) == JobStatus.PROCESSING.value
            assert await session.scalar(
                select(func.count()).select_from(ProviderOutcomeRow)
            ) == 0
            assert await session.scalar(
                select(func.count()).select_from(WebhookEventRow)
            ) == 0
            assert await session.scalar(
                select(func.count())
                .select_from(OutboxRow)
                .where(OutboxRow.topic == "artifact.transfer")
            ) == 0

        assert await repository.begin_artifact_transfer(
            transferring,
            provider.name,
            "evt-success-atomic",
            WorkItem(job_id=job.id),
        ) == (True, True)
        assert len(await _outcomes(repository)) == 1
    finally:
        await repository.dispose()


class _LateUniqueConflictRepository(SqlAlchemyJobRepository):
    conflicting_outbox_id = "00000000-0000-0000-0000-000000000001"

    async def _record_callback_transition(
        self,
        session,
        previous_status: str,
        previous_progress: int,
        job: GenerationJob,
    ) -> None:
        await super()._record_callback_transition(
            session, previous_status, previous_progress, job
        )
        # Model a unique-key race in a later write within the transaction. The
        # provider event itself is new, so this must not be misreported as an
        # idempotent provider-webhook replay.
        session.add(
            OutboxRow(
                id=self.conflicting_outbox_id,
                topic="injected.unique.conflict",
                payload_json={"job_id": str(job.id)},
            )
        )


@pytest.mark.asyncio
async def test_sql_late_integrity_error_is_not_reported_as_duplicate_webhook(
    tmp_path,
) -> None:
    repository = _LateUniqueConflictRepository.from_url(
        f"sqlite+aiosqlite:///{tmp_path / 'provider-late-conflict.db'}"
    )
    await repository.create_schema()
    try:
        provider = MockProviderAdapter()
        job = await _persist_job(repository, provider, "late-conflict")
        async with repository.sessions.begin() as session:
            session.add(
                OutboxRow(
                    id=repository.conflicting_outbox_id,
                    topic="preexisting",
                    payload_json={"sentinel": True},
                )
            )

        with pytest.raises(IntegrityError):
            await repository.begin_artifact_transfer(
                _transferring(job),
                provider.name,
                "evt-new-late-conflict",
                WorkItem(job_id=job.id),
            )

        async with repository.sessions() as session:
            assert await session.scalar(
                select(JobRow.status).where(JobRow.id == str(job.id))
            ) == JobStatus.PROCESSING.value
            assert await session.scalar(
                select(func.count())
                .select_from(WebhookEventRow)
                .where(
                    WebhookEventRow.provider == provider.name,
                    WebhookEventRow.event_id == "evt-new-late-conflict",
                )
            ) == 0
            assert await session.scalar(
                select(func.count()).select_from(ProviderOutcomeRow)
            ) == 0
            assert await session.scalar(
                select(func.count())
                .select_from(OutboxRow)
                .where(OutboxRow.topic == "artifact.transfer")
            ) == 0
    finally:
        await repository.dispose()


@pytest.mark.asyncio
async def test_sql_provider_failure_is_recorded_as_failed(tmp_path) -> None:
    repository = SqlAlchemyJobRepository.from_url(
        f"sqlite+aiosqlite:///{tmp_path / 'provider-failure.db'}"
    )
    await repository.create_schema()
    try:
        provider = MockProviderAdapter()
        job = await _persist_job(repository, provider, "failure")
        failed = job.model_copy(deep=True)
        failed.status = JobStatus.FAILED
        failed.progress = 100
        failed.error = ErrorDetail(
            code="UPSTREAM_FAILED",
            message="Provider failed the generation",
            retryable=False,
        )

        assert await repository.apply_webhook_event(
            failed, provider.name, "evt-failure"
        ) == (True, True)
        assert await repository.apply_webhook_event(
            failed, provider.name, "evt-failure"
        ) == (False, False)

        [outcome] = await _outcomes(repository)
        assert outcome.job_id == str(job.id)
        assert outcome.succeeded is False
        persisted = await repository.get(job.id)
        assert persisted is not None
        assert persisted.status == JobStatus.FAILED
    finally:
        await repository.dispose()


@pytest.mark.asyncio
async def test_sql_storage_failure_does_not_reclassify_upstream_success(
    tmp_path,
) -> None:
    repository = SqlAlchemyJobRepository.from_url(
        f"sqlite+aiosqlite:///{tmp_path / 'provider-storage-failure.db'}"
    )
    await repository.create_schema()
    try:
        provider = MockProviderAdapter()
        job = await _persist_job(repository, provider, "storage-failure")
        transferring = _transferring(job)
        assert await repository.begin_artifact_transfer(
            transferring,
            provider.name,
            "evt-storage-failure",
            WorkItem(job_id=job.id),
        ) == (True, True)

        claim = await repository.claim_artifact_transfer(
            job.id, lease=timedelta(seconds=30)
        )
        assert claim is not None
        claim.job.status = JobStatus.FAILED
        claim.job.error = ErrorDetail(
            code="ARTIFACT_TRANSFER_FAILED",
            message="Platform-controlled storage rejected the artifact",
            retryable=False,
        )
        assert await repository.finish_artifact_transfer(
            claim.job, token=claim.token
        )

        [outcome] = await _outcomes(repository)
        assert outcome.job_id == str(job.id)
        assert outcome.succeeded is True
        persisted = await repository.get(job.id)
        assert persisted is not None
        assert persisted.status == JobStatus.FAILED
        assert persisted.error is not None
        assert persisted.error.code == "ARTIFACT_TRANSFER_FAILED"
    finally:
        await repository.dispose()


@pytest.mark.asyncio
async def test_sql_processing_and_confirmed_not_created_are_not_outcomes(
    tmp_path,
) -> None:
    repository = SqlAlchemyJobRepository.from_url(
        f"sqlite+aiosqlite:///{tmp_path / 'provider-non-outcomes.db'}"
    )
    await repository.create_schema()
    try:
        provider = MockProviderAdapter()
        processing = await _persist_job(repository, provider, "processing")
        progress_update = processing.model_copy(deep=True)
        progress_update.progress = 60
        assert await repository.apply_webhook_event(
            progress_update, provider.name, "evt-processing"
        ) == (True, True)

        reconciliation = GenerationJob(
            tenant_id=uuid4(),
            client_reference_id="not-created",
            model="mock.video.v1",
            mode=GenerationMode.TEXT_TO_VIDEO,
            inputs=GenerationInputs(prompt="Confirm no upstream task exists"),
            output=OutputOptions(),
            status=JobStatus.RECONCILIATION_REQUIRED,
            provider=provider.route_id,
            error=ErrorDetail(
                code="SUBMISSION_RECONCILIATION_REQUIRED",
                message="Provider submission outcome requires reconciliation",
                retryable=False,
            ),
        )
        stored, replayed, conflict = await repository.create_idempotent(
            reconciliation, "idempotency-not-created", "hash-not-created"
        )
        assert replayed is False
        assert conflict is False

        router = ProviderRouter([provider], account_pool=repository)
        await router.validate_configuration()
        service = GenerationService(
            repository, InMemoryWorkQueue(), router
        )
        resolved = await service.resolve_submission_reconciliation(
            stored.id,
            stored.tenant_id,
            SubmissionReconciliationRequest(outcome="not_created"),
        )
        duplicate = await service.resolve_submission_reconciliation(
            stored.id,
            stored.tenant_id,
            SubmissionReconciliationRequest(outcome="not_created"),
        )

        assert resolved.status == JobStatus.FAILED
        assert resolved.error is not None
        assert resolved.error.code == "SUBMISSION_CONFIRMED_NOT_CREATED"
        assert duplicate.status == JobStatus.FAILED
        assert await _outcomes(repository) == []
    finally:
        await repository.dispose()
