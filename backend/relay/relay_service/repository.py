from __future__ import annotations

from abc import ABC, abstractmethod
from asyncio import Lock
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from .errors import public_async_error
from .models import (
    CallbackDelivery,
    CallbackDeliveryStatus,
    CallbackDeliveryView,
    GenerationJob,
    JobStatus,
    OutboxMessage,
    PublicAsyncErrorCode,
    WorkItem,
    callback_delivery_for_job,
    utc_now,
)


@dataclass(frozen=True)
class IdempotencyRecord:
    request_hash: str
    job_id: UUID


@dataclass(frozen=True)
class SubmissionClaim:
    job: GenerationJob
    token: UUID


@dataclass(frozen=True)
class ArtifactTransferClaim:
    job: GenerationJob
    token: UUID


@dataclass(frozen=True)
class ProviderPollClaim:
    job: GenerationJob
    token: UUID


@dataclass(frozen=True)
class CallbackClaim:
    """One fenced ownership lease for a callback delivery attempt."""

    delivery: CallbackDelivery
    token: UUID


def _merge_provider_update(
    current: GenerationJob, requested: GenerationJob
) -> GenerationJob:
    """Merge a provider event without allowing a stale reader to regress state."""

    merged = current.model_copy(deep=True)
    merged.status = requested.status
    merged.progress = max(current.progress, requested.progress)
    merged.outputs = [item.model_copy(deep=True) for item in requested.outputs]
    merged.transfer_sources = [
        item.model_copy(deep=True) for item in requested.transfer_sources
    ]
    merged.error = (
        requested.error.model_copy(deep=True)
        if requested.error is not None
        else None
    )
    current_updated = current.updated_at
    requested_updated = requested.updated_at
    if current_updated.tzinfo is None:
        current_updated = current_updated.replace(tzinfo=timezone.utc)
    if requested_updated.tzinfo is None:
        requested_updated = requested_updated.replace(tzinfo=timezone.utc)
    merged.updated_at = max(current_updated, requested_updated)
    return merged


class JobRepository(ABC):
    persistent: bool = False
    kind: str = "abstract"
    has_outbox: bool = False

    @abstractmethod
    async def create_idempotent(
        self, job: GenerationJob, idempotency_key: str, request_hash: str
    ) -> tuple[GenerationJob, bool, bool]:
        """Return (job, replayed, payload_conflict)."""

    @abstractmethod
    async def get(self, job_id: UUID) -> GenerationJob | None: ...

    @abstractmethod
    async def get_for_tenant(
        self, job_id: UUID, tenant_id: UUID
    ) -> GenerationJob | None: ...

    @abstractmethod
    async def save(self, job: GenerationJob) -> None: ...

    @abstractmethod
    async def save_if_status(
        self, job: GenerationJob, *, expected_status: JobStatus
    ) -> bool:
        """Atomically save ``job`` only when its persisted status still matches."""

    @abstractmethod
    async def list_submission_reconciliations(
        self, tenant_id: UUID, *, limit: int = 100
    ) -> list[GenerationJob]: ...

    @abstractmethod
    async def claim_submission(
        self, job_id: UUID, *, lease: timedelta
    ) -> SubmissionClaim | None: ...

    @abstractmethod
    async def renew_submission_claim(
        self, job_id: UUID, *, token: UUID, lease: timedelta
    ) -> bool: ...

    @abstractmethod
    async def finish_submission(
        self, job: GenerationJob, *, token: UUID
    ) -> bool: ...

    @abstractmethod
    async def release_submission_claim(
        self, job_id: UUID, *, token: UUID
    ) -> bool:
        """Release a claim after proving no upstream submission was attempted.

        The transition back to ``queued`` and claim release must be atomic. A
        caller must never use this method for an ambiguous provider POST; those
        jobs stay fenced in ``submitting`` until reconciliation.
        """

    @abstractmethod
    async def claim_artifact_transfer(
        self, job_id: UUID, *, lease: timedelta
    ) -> ArtifactTransferClaim | None: ...

    @abstractmethod
    async def renew_artifact_transfer_claim(
        self, job_id: UUID, *, token: UUID, lease: timedelta
    ) -> bool: ...

    @abstractmethod
    async def save_artifact_transfer_progress(
        self, job: GenerationJob, *, token: UUID
    ) -> bool:
        """Persist partial transfer progress while retaining the claim."""

    @abstractmethod
    async def finish_artifact_transfer(
        self, job: GenerationJob, *, token: UUID
    ) -> bool:
        """Persist a retry or terminal state and release the transfer claim."""

    @abstractmethod
    async def save_with_outbox(
        self, job: GenerationJob, topic: str, item: WorkItem
    ) -> None: ...

    @abstractmethod
    async def begin_artifact_transfer(
        self,
        job: GenerationJob,
        provider: str,
        event_id: str,
        item: WorkItem,
        *,
        poll_token: UUID | None = None,
    ) -> tuple[bool, bool] | None:
        """Return (event_is_new, transfer_started) atomically."""

    @abstractmethod
    async def find_by_provider_task(
        self, provider: str, provider_task_id: str
    ) -> GenerationJob | None: ...

    @abstractmethod
    async def list_processing_jobs(
        self, *, limit: int = 100, cursor: UUID | None = None
    ) -> list[GenerationJob]: ...

    @abstractmethod
    async def claim_processing_jobs(
        self,
        *,
        limit: int = 100,
        cursor: UUID | None = None,
        lease: timedelta,
    ) -> list[ProviderPollClaim]:
        """Claim due provider tasks so only one poller calls upstream."""

    @abstractmethod
    async def renew_provider_poll_claim(
        self, job_id: UUID, *, token: UUID, lease: timedelta
    ) -> bool: ...

    @abstractmethod
    async def finish_provider_poll(
        self, job: GenerationJob, *, token: UUID
    ) -> bool:
        """Persist a non-event poll result and release the owned claim."""

    @abstractmethod
    async def record_provider_poll_failure(
        self,
        job_id: UUID,
        *,
        token: UUID,
        error_code: str,
        retry_delay: timedelta,
    ) -> bool: ...

    @abstractmethod
    async def record_provider_poll_success(
        self, job_id: UUID, *, token: UUID
    ) -> bool: ...

    @abstractmethod
    async def apply_webhook_event(
        self,
        job: GenerationJob,
        provider: str,
        event_id: str,
        *,
        poll_token: UUID | None = None,
    ) -> tuple[bool, bool] | None:
        """Record an event and update its job in one transaction.

        Return ``(event_is_new, job_updated)``. Existing terminal or
        transferring jobs still consume a new event, but are never regressed.
        """

    @abstractmethod
    async def healthcheck(self) -> bool: ...


class OutboxRepository(ABC):
    @abstractmethod
    async def claim_outbox(
        self, *, batch_size: int = 100, lease: timedelta = timedelta(seconds=60)
    ) -> list[OutboxMessage]: ...

    @abstractmethod
    async def mark_outbox_published(self, message_id: UUID) -> None: ...

    @abstractmethod
    async def release_outbox(self, message_id: UUID, error: str) -> None: ...


class CallbackRepository(ABC):
    @abstractmethod
    async def claim_callback_deliveries(
        self,
        *,
        batch_size: int = 50,
        lease: timedelta = timedelta(seconds=60),
        exclude_ids: set[UUID] | None = None,
    ) -> list[CallbackClaim]: ...

    @abstractmethod
    async def mark_callback_delivered(
        self, delivery_id: UUID, *, token: UUID, response_status: int
    ) -> bool:
        """Complete only the currently owned attempt."""

    @abstractmethod
    async def release_callback_delivery(
        self,
        delivery_id: UUID,
        *,
        token: UUID,
        error: str,
        retry_delay: timedelta,
        dead_letter: bool,
        response_status: int | None = None,
    ) -> bool:
        """Fail only the currently owned attempt."""

    @abstractmethod
    async def list_callback_deliveries(
        self,
        tenant_id: UUID,
        *,
        status: CallbackDeliveryStatus | None = None,
        limit: int = 100,
    ) -> list[CallbackDeliveryView]: ...


class InMemoryJobRepository(JobRepository, CallbackRepository):
    persistent = False
    kind = "memory"
    has_outbox = False

    def __init__(self) -> None:
        self._jobs: dict[UUID, GenerationJob] = {}
        self._keys: dict[tuple[UUID, str], IdempotencyRecord] = {}
        self._events: set[tuple[str, str]] = set()
        self._submission_claims: dict[
            UUID, tuple[UUID, datetime]
        ] = {}
        self._artifact_transfer_claims: dict[
            UUID, tuple[UUID, datetime]
        ] = {}
        self._provider_poll_claims: dict[
            UUID, tuple[UUID, datetime]
        ] = {}
        self._callback_deliveries: dict[UUID, CallbackDelivery] = {}
        self._callback_claim_tokens: dict[UUID, UUID] = {}
        self._lock = Lock()

    def _record_callback_transition(
        self,
        previous: GenerationJob | None,
        replacement: GenerationJob,
    ) -> None:
        if previous is not None and previous.status == replacement.status:
            if (
                replacement.status != JobStatus.PROCESSING
                or previous.progress == replacement.progress
            ):
                return
        delivery = callback_delivery_for_job(replacement)
        if delivery is not None:
            self._callback_deliveries.setdefault(
                delivery.id, delivery.model_copy(deep=True)
            )

    async def create_idempotent(
        self, job: GenerationJob, idempotency_key: str, request_hash: str
    ) -> tuple[GenerationJob, bool, bool]:
        async with self._lock:
            key = (job.tenant_id, idempotency_key)
            existing = self._keys.get(key)
            if existing:
                existing_job = self._jobs[existing.job_id].model_copy(deep=True)
                return existing_job, True, existing.request_hash != request_hash
            self._jobs[job.id] = job.model_copy(deep=True)
            self._keys[key] = IdempotencyRecord(request_hash, job.id)
            return job.model_copy(deep=True), False, False

    async def get(self, job_id: UUID) -> GenerationJob | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    async def get_for_tenant(
        self, job_id: UUID, tenant_id: UUID
    ) -> GenerationJob | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.tenant_id != tenant_id:
                return None
            return job.model_copy(deep=True)

    async def save(self, job: GenerationJob) -> None:
        async with self._lock:
            current = self._jobs.get(job.id)
            self._record_callback_transition(current, job)
            self._jobs[job.id] = job.model_copy(deep=True)

    async def save_if_status(
        self, job: GenerationJob, *, expected_status: JobStatus
    ) -> bool:
        async with self._lock:
            current = self._jobs.get(job.id)
            if current is None or current.status != expected_status:
                return False
            self._record_callback_transition(current, job)
            self._jobs[job.id] = job.model_copy(deep=True)
            return True

    async def list_submission_reconciliations(
        self, tenant_id: UUID, *, limit: int = 100
    ) -> list[GenerationJob]:
        async with self._lock:
            jobs = [
                job.model_copy(deep=True)
                for job in self._jobs.values()
                if job.tenant_id == tenant_id
                and job.status == JobStatus.RECONCILIATION_REQUIRED
            ]
        jobs.sort(key=lambda job: (job.created_at, str(job.id)))
        return jobs[:limit]

    async def claim_submission(
        self, job_id: UUID, *, lease: timedelta
    ) -> SubmissionClaim | None:
        now = utc_now()
        async with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                return None
            existing_claim = self._submission_claims.get(job_id)
            if current.status == JobStatus.SUBMITTING and (
                existing_claim is None or existing_claim[1] <= now
            ):
                quarantined = current.model_copy(deep=True)
                quarantined.status = JobStatus.RECONCILIATION_REQUIRED
                quarantined.error = public_async_error(
                    PublicAsyncErrorCode.SUBMISSION_RECONCILIATION_REQUIRED,
                    details={"provider_error": "SUBMISSION_CLAIM_EXPIRED"},
                )
                quarantined.updated_at = now
                self._jobs[job_id] = quarantined
                self._submission_claims.pop(job_id, None)
                return None
            if current.status != JobStatus.QUEUED:
                return None

            token = uuid4()
            claimed = current.model_copy(deep=True)
            claimed.status = JobStatus.SUBMITTING
            claimed.updated_at = now
            self._jobs[job_id] = claimed
            self._submission_claims[job_id] = (token, now + lease)
            return SubmissionClaim(
                job=claimed.model_copy(deep=True),
                token=token,
            )

    async def renew_submission_claim(
        self, job_id: UUID, *, token: UUID, lease: timedelta
    ) -> bool:
        now = utc_now()
        async with self._lock:
            current = self._jobs.get(job_id)
            current_claim = self._submission_claims.get(job_id)
            if (
                current is None
                or current.status != JobStatus.SUBMITTING
                or current_claim is None
                or current_claim[0] != token
            ):
                return False
            self._submission_claims[job_id] = (token, now + lease)
            return True

    async def finish_submission(
        self, job: GenerationJob, *, token: UUID
    ) -> bool:
        async with self._lock:
            current_claim = self._submission_claims.get(job.id)
            if current_claim is None or current_claim[0] != token:
                return False
            if job.id not in self._jobs:
                self._submission_claims.pop(job.id, None)
                return False
            self._record_callback_transition(self._jobs[job.id], job)
            self._jobs[job.id] = job.model_copy(deep=True)
            del self._submission_claims[job.id]
            return True

    async def release_submission_claim(
        self, job_id: UUID, *, token: UUID
    ) -> bool:
        async with self._lock:
            current = self._jobs.get(job_id)
            current_claim = self._submission_claims.get(job_id)
            if (
                current is None
                or current.status != JobStatus.SUBMITTING
                or current_claim is None
                or current_claim[0] != token
            ):
                return False
            released = current.model_copy(deep=True)
            released.status = JobStatus.QUEUED
            released.provider = None
            released.provider_task_id = None
            released.updated_at = utc_now()
            self._jobs[job_id] = released
            del self._submission_claims[job_id]
            return True

    async def claim_artifact_transfer(
        self, job_id: UUID, *, lease: timedelta
    ) -> ArtifactTransferClaim | None:
        now = utc_now()
        async with self._lock:
            current = self._jobs.get(job_id)
            if current is None or current.status != JobStatus.TRANSFERRING:
                return None
            existing_claim = self._artifact_transfer_claims.get(job_id)
            if existing_claim is not None and existing_claim[1] > now:
                return None
            token = uuid4()
            self._artifact_transfer_claims[job_id] = (token, now + lease)
            return ArtifactTransferClaim(
                job=current.model_copy(deep=True),
                token=token,
            )

    async def renew_artifact_transfer_claim(
        self, job_id: UUID, *, token: UUID, lease: timedelta
    ) -> bool:
        now = utc_now()
        async with self._lock:
            current = self._jobs.get(job_id)
            current_claim = self._artifact_transfer_claims.get(job_id)
            if (
                current is None
                or current.status != JobStatus.TRANSFERRING
                or current_claim is None
                or current_claim[0] != token
            ):
                return False
            self._artifact_transfer_claims[job_id] = (token, now + lease)
            return True

    async def save_artifact_transfer_progress(
        self, job: GenerationJob, *, token: UUID
    ) -> bool:
        async with self._lock:
            current = self._jobs.get(job.id)
            current_claim = self._artifact_transfer_claims.get(job.id)
            if (
                current is None
                or current.status != JobStatus.TRANSFERRING
                or job.status != JobStatus.TRANSFERRING
                or current_claim is None
                or current_claim[0] != token
            ):
                return False
            self._record_callback_transition(current, job)
            self._jobs[job.id] = job.model_copy(deep=True)
            return True

    async def finish_artifact_transfer(
        self, job: GenerationJob, *, token: UUID
    ) -> bool:
        allowed_replacements = {
            JobStatus.TRANSFERRING,
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }
        async with self._lock:
            current = self._jobs.get(job.id)
            current_claim = self._artifact_transfer_claims.get(job.id)
            if (
                current is None
                or current.status != JobStatus.TRANSFERRING
                or job.status not in allowed_replacements
                or current_claim is None
                or current_claim[0] != token
            ):
                return False
            self._record_callback_transition(current, job)
            self._jobs[job.id] = job.model_copy(deep=True)
            del self._artifact_transfer_claims[job.id]
            return True

    async def save_with_outbox(
        self, job: GenerationJob, topic: str, item: WorkItem
    ) -> None:
        # Memory mode has no durable outbox; the service enqueues after this save.
        await self.save(job)

    async def begin_artifact_transfer(
        self,
        job: GenerationJob,
        provider: str,
        event_id: str,
        item: WorkItem,
        *,
        poll_token: UUID | None = None,
    ) -> tuple[bool, bool] | None:
        async with self._lock:
            current = self._jobs.get(job.id)
            if poll_token is not None:
                current_claim = self._provider_poll_claims.get(job.id)
                if (
                    current is None
                    or current.status != JobStatus.PROCESSING
                    or current_claim is None
                    or current_claim[0] != poll_token
                ):
                    return None
            event_key = (provider, event_id)
            if event_key in self._events:
                if poll_token is not None:
                    current.provider_poll_failures = 0
                    current.provider_next_poll_at = None
                    current.provider_last_poll_error = None
                    self._provider_poll_claims.pop(job.id, None)
                return False, False
            self._events.add(event_key)
            if current is None:
                return True, False
            if current.status in {
                JobStatus.TRANSFERRING,
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }:
                return True, False
            replacement = _merge_provider_update(current, job)
            if poll_token is not None:
                replacement.provider_poll_failures = job.provider_poll_failures
                replacement.provider_next_poll_at = job.provider_next_poll_at
                replacement.provider_last_poll_error = (
                    job.provider_last_poll_error
                )
            self._record_callback_transition(current, replacement)
            self._jobs[job.id] = replacement
            if poll_token is not None or replacement.status != JobStatus.PROCESSING:
                self._provider_poll_claims.pop(job.id, None)
            return True, True

    async def find_by_provider_task(
        self, provider: str, provider_task_id: str
    ) -> GenerationJob | None:
        async with self._lock:
            for job in self._jobs.values():
                if job.provider == provider and job.provider_task_id == provider_task_id:
                    return job.model_copy(deep=True)
            return None

    async def list_processing_jobs(
        self, *, limit: int = 100, cursor: UUID | None = None
    ) -> list[GenerationJob]:
        if limit < 1:
            return []
        async with self._lock:
            jobs = [
                job.model_copy(deep=True)
                for job in self._jobs.values()
                if job.status == JobStatus.PROCESSING
                and job.provider is not None
                and job.provider_task_id is not None
                and (
                    job.provider_next_poll_at is None
                    or job.provider_next_poll_at <= utc_now()
                )
            ]
        jobs.sort(key=lambda job: str(job.id))
        if cursor is not None:
            cursor_value = str(cursor)
            jobs = (
                [job for job in jobs if str(job.id) > cursor_value]
                + [job for job in jobs if str(job.id) <= cursor_value]
            )
        return jobs[:limit]

    async def claim_processing_jobs(
        self,
        *,
        limit: int = 100,
        cursor: UUID | None = None,
        lease: timedelta,
    ) -> list[ProviderPollClaim]:
        if limit < 1:
            return []
        now = utc_now()
        async with self._lock:
            jobs = [
                job
                for job in self._jobs.values()
                if job.status == JobStatus.PROCESSING
                and job.provider is not None
                and job.provider_task_id is not None
                and (
                    job.provider_next_poll_at is None
                    or job.provider_next_poll_at <= now
                )
                and (
                    job.id not in self._provider_poll_claims
                    or self._provider_poll_claims[job.id][1] <= now
                )
            ]
            jobs.sort(key=lambda job: str(job.id))
            if cursor is not None:
                cursor_value = str(cursor)
                jobs = (
                    [job for job in jobs if str(job.id) > cursor_value]
                    + [job for job in jobs if str(job.id) <= cursor_value]
                )
            claims: list[ProviderPollClaim] = []
            for job in jobs[:limit]:
                token = uuid4()
                self._provider_poll_claims[job.id] = (token, now + lease)
                claims.append(
                    ProviderPollClaim(
                        job=job.model_copy(deep=True),
                        token=token,
                    )
                )
            return claims

    async def renew_provider_poll_claim(
        self, job_id: UUID, *, token: UUID, lease: timedelta
    ) -> bool:
        now = utc_now()
        async with self._lock:
            current = self._jobs.get(job_id)
            current_claim = self._provider_poll_claims.get(job_id)
            if (
                current is None
                or current.status != JobStatus.PROCESSING
                or current_claim is None
                or current_claim[0] != token
            ):
                return False
            self._provider_poll_claims[job_id] = (token, now + lease)
            return True

    async def finish_provider_poll(
        self, job: GenerationJob, *, token: UUID
    ) -> bool:
        async with self._lock:
            current = self._jobs.get(job.id)
            current_claim = self._provider_poll_claims.get(job.id)
            if (
                current is None
                or current.status != JobStatus.PROCESSING
                or current_claim is None
                or current_claim[0] != token
            ):
                return False
            self._record_callback_transition(current, job)
            self._jobs[job.id] = job.model_copy(deep=True)
            del self._provider_poll_claims[job.id]
            return True

    async def record_provider_poll_failure(
        self,
        job_id: UUID,
        *,
        token: UUID,
        error_code: str,
        retry_delay: timedelta,
    ) -> bool:
        async with self._lock:
            current = self._jobs.get(job_id)
            current_claim = self._provider_poll_claims.get(job_id)
            if (
                current is None
                or current.status != JobStatus.PROCESSING
                or current_claim is None
                or current_claim[0] != token
            ):
                return False
            replacement = current.model_copy(deep=True)
            replacement.provider_poll_failures += 1
            replacement.provider_last_poll_error = error_code[:128]
            replacement.provider_next_poll_at = utc_now() + retry_delay
            self._jobs[job_id] = replacement
            del self._provider_poll_claims[job_id]
            return True

    async def record_provider_poll_success(
        self, job_id: UUID, *, token: UUID
    ) -> bool:
        async with self._lock:
            current = self._jobs.get(job_id)
            current_claim = self._provider_poll_claims.get(job_id)
            if (
                current is None
                or current.status != JobStatus.PROCESSING
                or current_claim is None
                or current_claim[0] != token
            ):
                return False
            replacement = current.model_copy(deep=True)
            replacement.provider_poll_failures = 0
            replacement.provider_next_poll_at = None
            replacement.provider_last_poll_error = None
            self._jobs[job_id] = replacement
            del self._provider_poll_claims[job_id]
            return True

    async def apply_webhook_event(
        self,
        job: GenerationJob,
        provider: str,
        event_id: str,
        *,
        poll_token: UUID | None = None,
    ) -> tuple[bool, bool] | None:
        async with self._lock:
            current = self._jobs.get(job.id)
            if poll_token is not None:
                current_claim = self._provider_poll_claims.get(job.id)
                if (
                    current is None
                    or current.status != JobStatus.PROCESSING
                    or current_claim is None
                    or current_claim[0] != poll_token
                ):
                    return None
            key = (provider, event_id)
            if key in self._events:
                if poll_token is not None:
                    current.provider_poll_failures = 0
                    current.provider_next_poll_at = None
                    current.provider_last_poll_error = None
                    self._provider_poll_claims.pop(job.id, None)
                return False, False
            if current is None or current.status in {
                JobStatus.TRANSFERRING,
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }:
                self._events.add(key)
                return True, False

            # Register the provider event before publishing the replacement.
            # If a test double (or runtime error) rejects registration, no job
            # or callback state has changed and the caller can retry safely.
            replacement = _merge_provider_update(current, job)
            if poll_token is not None:
                replacement.provider_poll_failures = job.provider_poll_failures
                replacement.provider_next_poll_at = job.provider_next_poll_at
                replacement.provider_last_poll_error = (
                    job.provider_last_poll_error
                )
            try:
                self._events.add(key)
            except Exception:
                self._events.discard(key)
                raise
            self._record_callback_transition(current, replacement)
            self._jobs[job.id] = replacement
            if poll_token is not None or replacement.status != JobStatus.PROCESSING:
                self._provider_poll_claims.pop(job.id, None)
            return True, True

    async def healthcheck(self) -> bool:
        return True

    async def claim_callback_deliveries(
        self,
        *,
        batch_size: int = 50,
        lease: timedelta = timedelta(seconds=60),
        exclude_ids: set[UUID] | None = None,
    ) -> list[CallbackClaim]:
        now = utc_now()
        stale_before = now - lease
        excluded = exclude_ids or set()
        async with self._lock:
            candidates = sorted(
                (
                    delivery
                    for delivery in self._callback_deliveries.values()
                    if delivery.id not in excluded
                    and delivery.available_at <= now
                    and (
                        delivery.status == CallbackDeliveryStatus.PENDING
                        or (
                            delivery.status
                            == CallbackDeliveryStatus.DELIVERING
                            and delivery.locked_at is not None
                            and delivery.locked_at < stale_before
                        )
                    )
                ),
                key=lambda delivery: delivery.created_at,
            )[:batch_size]
            claimed: list[CallbackClaim] = []
            for delivery in candidates:
                token = uuid4()
                delivery.status = CallbackDeliveryStatus.DELIVERING
                delivery.locked_at = now
                delivery.attempts += 1
                self._callback_claim_tokens[delivery.id] = token
                claimed.append(
                    CallbackClaim(
                        delivery=delivery.model_copy(deep=True),
                        token=token,
                    )
                )
            return claimed

    async def mark_callback_delivered(
        self, delivery_id: UUID, *, token: UUID, response_status: int
    ) -> bool:
        async with self._lock:
            delivery = self._callback_deliveries.get(delivery_id)
            if (
                delivery is None
                or delivery.status != CallbackDeliveryStatus.DELIVERING
                or self._callback_claim_tokens.get(delivery_id) != token
            ):
                return False
            delivery.status = CallbackDeliveryStatus.DELIVERED
            delivery.delivered_at = utc_now()
            delivery.locked_at = None
            delivery.response_status = response_status
            delivery.last_error = None
            self._callback_claim_tokens.pop(delivery_id, None)
            return True

    async def release_callback_delivery(
        self,
        delivery_id: UUID,
        *,
        token: UUID,
        error: str,
        retry_delay: timedelta,
        dead_letter: bool,
        response_status: int | None = None,
    ) -> bool:
        async with self._lock:
            delivery = self._callback_deliveries.get(delivery_id)
            if (
                delivery is None
                or delivery.status != CallbackDeliveryStatus.DELIVERING
                or self._callback_claim_tokens.get(delivery_id) != token
            ):
                return False
            delivery.status = (
                CallbackDeliveryStatus.DEAD_LETTER
                if dead_letter
                else CallbackDeliveryStatus.PENDING
            )
            delivery.locked_at = None
            delivery.available_at = utc_now() + retry_delay
            delivery.response_status = response_status
            delivery.last_error = error[:2_000]
            self._callback_claim_tokens.pop(delivery_id, None)
            return True

    async def list_callback_deliveries(
        self,
        tenant_id: UUID,
        *,
        status: CallbackDeliveryStatus | None = None,
        limit: int = 100,
    ) -> list[CallbackDeliveryView]:
        async with self._lock:
            deliveries = sorted(
                (
                    delivery
                    for delivery in self._callback_deliveries.values()
                    if delivery.tenant_id == tenant_id
                    and (status is None or delivery.status == status)
                ),
                key=lambda delivery: delivery.created_at,
                reverse=True,
            )[:limit]
            return [
                CallbackDeliveryView(
                    event_id=delivery.id,
                    request_id=delivery.request_id,
                    job_id=delivery.job_id,
                    job_status=delivery.event.job.status,
                    delivery_status=delivery.status,
                    attempts=delivery.attempts,
                    available_at=delivery.available_at,
                    delivered_at=delivery.delivered_at,
                    response_status=delivery.response_status,
                    last_error=delivery.last_error,
                    created_at=delivery.created_at,
                )
                for delivery in deliveries
            ]
