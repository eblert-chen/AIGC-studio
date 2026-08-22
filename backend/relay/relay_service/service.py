from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import hashlib
import json
from datetime import timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from .callback import CallbackPolicy
from .errors import RelayError, public_async_error, public_generation_error
from .models import (
    GenerationAccepted,
    GenerationJob,
    GenerationRequest,
    JobStatus,
    PublicAsyncErrorCode,
    reservation_action_for,
    SubmissionReconciliationRequest,
    ProviderWebhookEvent,
    ProviderWebhookStatus,
    TERMINAL_STATUSES,
    TransferSource,
    WebhookReceipt,
    WorkItem,
    utc_now,
)
from .providers.base import ProviderError, ProviderSubmission
from .providers.router import ProviderRouter
from .queue import WorkQueue
from .repository import JobRepository, ProviderPollClaim, SubmissionClaim


class _SubmissionClaimLost(RuntimeError):
    """The worker can no longer prove ownership of the provider side effect."""


class _ProviderPollClaimLost(RuntimeError):
    """A stale poller must not publish its provider response."""


_ACCOUNT_POOL_ADMISSION_ERRORS = frozenset(
    {
        "PROVIDER_ACCOUNT_POOL_BUSY",
        "PROVIDER_ACCOUNT_POOL_RATE_LIMITED",
    }
)


@dataclass(frozen=True)
class ProviderPollSummary:
    scanned: int = 0
    events: int = 0
    duplicates: int = 0
    failures: int = 0


class GenerationService:
    def __init__(
        self,
        repository: JobRepository,
        queue: WorkQueue,
        router: ProviderRouter,
        *,
        max_worker_attempts: int = 3,
        submission_claim_lease_seconds: float = 120.0,
        transfer_queue: WorkQueue | None = None,
        callback_policy: CallbackPolicy | None = None,
        provider_poll_concurrency: int = 8,
        provider_poll_claim_lease_seconds: float = 120.0,
        provider_poll_error_base_seconds: float = 15,
        provider_poll_error_max_seconds: float = 300,
        provider_admission_retry_seconds: float = 5,
    ) -> None:
        self.repository = repository
        self.queue = queue
        self.router = router
        if submission_claim_lease_seconds <= 0:
            raise ValueError("submission_claim_lease_seconds must be positive")
        self.max_worker_attempts = max_worker_attempts
        self.submission_claim_lease = timedelta(seconds=submission_claim_lease_seconds)
        self.transfer_queue = transfer_queue
        self.callback_policy = callback_policy or CallbackPolicy({}, production=False)
        self._provider_poll_cursor: UUID | None = None
        if provider_poll_concurrency < 1:
            raise ValueError("provider_poll_concurrency must be positive")
        if provider_poll_claim_lease_seconds <= 0:
            raise ValueError("provider poll claim lease must be positive")
        if provider_poll_error_base_seconds <= 0:
            raise ValueError("provider_poll_error_base_seconds must be positive")
        if provider_poll_error_max_seconds < provider_poll_error_base_seconds:
            raise ValueError(
                "provider poll maximum delay must not be below its base delay"
            )
        if provider_admission_retry_seconds <= 0:
            raise ValueError("provider_admission_retry_seconds must be positive")
        self.provider_poll_concurrency = provider_poll_concurrency
        self.provider_poll_claim_lease = timedelta(
            seconds=provider_poll_claim_lease_seconds
        )
        self.provider_poll_error_base_seconds = provider_poll_error_base_seconds
        self.provider_poll_error_max_seconds = provider_poll_error_max_seconds
        self.provider_admission_retry_seconds = provider_admission_retry_seconds
        self.provider_poll_claim_heartbeat_seconds = min(
            max(provider_poll_claim_lease_seconds / 3, 0.01),
            provider_poll_claim_lease_seconds / 2,
        )
        lease_seconds = self.submission_claim_lease.total_seconds()
        self.submission_claim_heartbeat_seconds = min(
            max(lease_seconds / 3, 0.01),
            lease_seconds / 2,
        )

    async def submit(
        self,
        request: GenerationRequest,
        idempotency_key: str,
        tenant_id: UUID,
        *,
        source_client_id: str | None = None,
        request_id: str | None = None,
    ) -> GenerationAccepted:
        callback_url = None
        if request.callback is not None:
            callback_url = self.callback_policy.authorize(
                tenant_id, str(request.callback.url)
            )
        request_bytes = json.dumps(
            request.model_dump(mode="json", exclude_none=True),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        request_hash = hashlib.sha256(request_bytes).hexdigest()
        metadata = request.metadata.copy()
        # This namespace is Relay-owned; callers cannot forge an outbound
        # trace header through arbitrary request metadata.
        metadata.pop("relay_request_id", None)
        metadata.pop("relay_capability_revision", None)
        if request_id is not None:
            metadata["relay_request_id"] = request_id
        job = GenerationJob(
            tenant_id=tenant_id,
            source_client_id=source_client_id,
            client_reference_id=request.client_reference_id,
            model=request.model,
            expected_capability_revision=request.expected_capability_revision,
            mode=request.mode,
            inputs=request.inputs,
            output=request.output,
            metadata=metadata,
            callback_url=callback_url,
        )
        stored, replayed, conflict = await self.repository.create_idempotent(
            job, idempotency_key, request_hash
        )
        if conflict:
            raise RelayError(
                "IDEMPOTENCY_KEY_REUSED",
                "Idempotency-Key was already used with a different payload",
                status_code=409,
            )
        if not replayed and not self.repository.has_outbox:
            await self.queue.enqueue(WorkItem(job_id=stored.id))
        return GenerationAccepted(
            id=stored.id,
            job_id=stored.id,
            status=stored.status,
            expected_capability_revision=(stored.expected_capability_revision),
            capability_revision=stored.expected_capability_revision,
            reservation_action=reservation_action_for(stored.status),
            idempotent_replay=replayed,
            created_at=stored.created_at,
        )

    async def get(self, job_id: UUID, tenant_id: UUID) -> GenerationJob:
        job = await self.repository.get_for_tenant(job_id, tenant_id)
        if job is None:
            raise RelayError(
                "JOB_NOT_FOUND",
                "Generation job does not exist",
                status_code=404,
            )
        return job

    async def list_submission_reconciliations(
        self, tenant_id: UUID, *, limit: int = 100
    ) -> list[GenerationJob]:
        return await self.repository.list_submission_reconciliations(
            tenant_id, limit=limit
        )

    async def resolve_submission_reconciliation(
        self,
        job_id: UUID,
        tenant_id: UUID,
        resolution: SubmissionReconciliationRequest,
    ) -> GenerationJob:
        """Resolve an upstream submission whose creation outcome was unknown.

        A positive confirmation resumes durable provider polling. A negative
        confirmation is accepted only for an unknown submission that has no
        persisted provider task, so callers such as the customer Platform can
        safely release a reservation without refunding a known upstream task.
        """

        job = await self.get(job_id, tenant_id)
        if (
            resolution.outcome == "created"
            and job.provider is not None
            and resolution.provider_route is not None
            and resolution.provider_route != job.provider
        ):
            raise RelayError(
                "PROVIDER_ROUTE_MISMATCH",
                "The confirmed provider route does not match the persisted route",
                status_code=409,
            )
        if (
            resolution.outcome == "created"
            and job.provider_task_id is not None
            and resolution.provider_task_id != job.provider_task_id
        ):
            raise RelayError(
                "PROVIDER_TASK_ID_MISMATCH",
                (
                    "The confirmed provider task identifier does not match "
                    "the persisted task"
                ),
                status_code=409,
            )
        if job.status != JobStatus.RECONCILIATION_REQUIRED:
            if (
                resolution.outcome == "created"
                and job.status == JobStatus.PROCESSING
                and job.provider_task_id == resolution.provider_task_id
                and (
                    resolution.provider_route is None
                    or job.provider == resolution.provider_route
                )
            ):
                return job
            if (
                resolution.outcome == "not_created"
                and job.status == JobStatus.FAILED
                and job.error is not None
                and job.error.code == "SUBMISSION_CONFIRMED_NOT_CREATED"
            ):
                return job
            raise RelayError(
                "RECONCILIATION_NOT_REQUIRED",
                "Generation job is not awaiting submission reconciliation",
                status_code=409,
            )

        if resolution.outcome == "not_created" and (
            job.provider_task_id is not None
            or job.error is None
            or job.error.code != "SUBMISSION_RECONCILIATION_REQUIRED"
        ):
            raise RelayError(
                "RECONCILIATION_OUTCOME_NOT_ALLOWED",
                (
                    "The not_created outcome is only valid for an unknown "
                    "submission without a provider task"
                ),
                status_code=409,
            )

        if resolution.outcome == "created":
            # A persisted route identifies the exact provider account that may
            # already own the upstream side effect. Operator input may fill a
            # missing route, but must never move a paid job to another account.
            route = job.provider or resolution.provider_route
            if route is None:
                raise RelayError(
                    "PROVIDER_ROUTE_REQUIRED",
                    "The provider route is required to resume this generation",
                    status_code=422,
                )
            provider = self.router.provider(route)
            if provider is None:
                raise RelayError(
                    "PROVIDER_ROUTE_NOT_FOUND",
                    "The confirmed provider route is not registered",
                    status_code=422,
                )
            assert resolution.provider_task_id is not None
            # Reuse the provider contract validation for length/character rules.
            try:
                submission = ProviderSubmission(resolution.provider_task_id)
            except (TypeError, ValueError) as exc:
                raise RelayError(
                    "PROVIDER_TASK_ID_INVALID",
                    "The confirmed provider task identifier is invalid",
                    status_code=422,
                ) from exc
            existing = await self.repository.find_by_provider_task(
                provider.route_id, submission.provider_task_id
            )
            if existing is not None and existing.id != job.id:
                raise RelayError(
                    "PROVIDER_TASK_ALREADY_ASSIGNED",
                    "The provider task is already assigned to another job",
                    status_code=409,
                )
            job.provider = provider.route_id
            job.provider_task_id = submission.provider_task_id
            job.status = JobStatus.PROCESSING
            job.progress = max(job.progress, 1)
            job.provider_poll_failures = 0
            job.provider_next_poll_at = None
            job.provider_last_poll_error = None
            job.error = None
        else:
            job.status = JobStatus.FAILED
            job.error = public_async_error(
                PublicAsyncErrorCode.SUBMISSION_CONFIRMED_NOT_CREATED,
            )
        job.updated_at = utc_now()
        saved = await self.repository.save_if_status(
            job, expected_status=JobStatus.RECONCILIATION_REQUIRED
        )
        if saved:
            if resolution.outcome == "not_created":
                await self.router.complete_job(job)
            return job

        latest = await self.get(job_id, tenant_id)
        if (
            resolution.outcome == "created"
            and latest.status == JobStatus.PROCESSING
            and latest.provider_task_id == resolution.provider_task_id
            and (
                resolution.provider_route is None
                or latest.provider == resolution.provider_route
            )
        ):
            return latest
        if (
            resolution.outcome == "not_created"
            and latest.status == JobStatus.FAILED
            and latest.error is not None
            and latest.error.code == "SUBMISSION_CONFIRMED_NOT_CREATED"
        ):
            await self.router.complete_job(latest)
            return latest
        raise RelayError(
            "RECONCILIATION_CONFLICT",
            "Generation reconciliation was resolved concurrently",
            status_code=409,
        )

    @staticmethod
    def _require_submission_reconciliation(
        job: GenerationJob,
        *,
        provider_route: str | None = None,
        provider_error: str | None = None,
    ) -> None:
        job.status = JobStatus.RECONCILIATION_REQUIRED
        if provider_route is not None:
            job.provider = provider_route
        job.provider_task_id = None
        if provider_error is not None:
            job.provider_last_poll_error = provider_error
        job.error = public_async_error(
            PublicAsyncErrorCode.SUBMISSION_RECONCILIATION_REQUIRED,
        )
        job.updated_at = utc_now()

    async def _renew_submission_claim(
        self,
        job_id: UUID,
        token: UUID,
        stop: asyncio.Event,
        lost: asyncio.Event,
    ) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self.submission_claim_heartbeat_seconds,
                )
                return
            except TimeoutError:
                pass

            try:
                renewed = await self.repository.renew_submission_claim(
                    job_id,
                    token=token,
                    lease=self.submission_claim_lease,
                )
            except Exception:
                lost.set()
                return
            if not renewed:
                lost.set()
                return

    async def _submit_with_claim_heartbeat(
        self, claim: SubmissionClaim
    ) -> tuple[str, ProviderSubmission]:
        stop = asyncio.Event()
        lost = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._renew_submission_claim(
                claim.job.id,
                claim.token,
                stop,
                lost,
            )
        )
        provider_call = asyncio.create_task(
            self.router.submit(claim.job, owner_token=claim.token)
        )
        claim_lost = asyncio.create_task(lost.wait())
        provider_result: tuple[str, ProviderSubmission] | None = None
        provider_error: BaseException | None = None

        try:
            done, _ = await asyncio.wait(
                {provider_call, claim_lost},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if provider_call in done:
                try:
                    provider_result = provider_call.result()
                except BaseException as exc:
                    provider_error = exc
            else:
                provider_call.cancel()
                with suppress(asyncio.CancelledError):
                    await provider_call
        finally:
            stop.set()
            claim_lost.cancel()
            with suppress(asyncio.CancelledError):
                await claim_lost
            await heartbeat
            if not provider_call.done():
                provider_call.cancel()
                with suppress(asyncio.CancelledError):
                    await provider_call

        if lost.is_set():
            # Cancellation cannot prove that an HTTP request did not commit.
            # The caller moves the job to RECONCILIATION_REQUIRED when it still
            # owns the fence; an expired fence is quarantined on redelivery.
            raise _SubmissionClaimLost(
                "Submission claim was lost during provider submit"
            )
        if provider_error is not None:
            raise provider_error
        assert provider_result is not None
        return provider_result

    async def process_next(self) -> GenerationJob | None:
        delivery = await self.queue.dequeue()

        if delivery is None:
            return None

        # Claiming is the durable fence around the external side effect. A
        # duplicate/reclaimed delivery must not call the provider while a
        # different worker owns an unexpired claim.
        claim = await self.repository.claim_submission(
            delivery.item.job_id,
            lease=self.submission_claim_lease,
        )
        if claim is None:
            latest = await self.repository.get(delivery.item.job_id)
            if latest is None or latest.status not in {
                JobStatus.QUEUED,
                JobStatus.SUBMITTING,
            }:
                await self.queue.ack(delivery)
            # Keep QUEUED/SUBMITTING deliveries pending. With Redis this may be
            # the same message reclaimed from the worker that owns the claim;
            # acknowledging it would remove the only crash-recovery trigger.
            return latest

        job = claim.job
        provider_submission_returned = False
        try:
            try:
                provider_name, submission = await self._submit_with_claim_heartbeat(
                    claim
                )
            except ProviderError as exc:
                if exc.submission_outcome_unknown:
                    self._require_submission_reconciliation(
                        job,
                        provider_route=exc.route_id,
                        provider_error=exc.code,
                    )
                    finished = await self.repository.finish_submission(
                        job, token=claim.token
                    )
                    if not finished:
                        raise RuntimeError("Submission claim was lost")
                    await self.queue.ack(delivery)
                    return job
                admission_wait = exc.code in _ACCOUNT_POOL_ADMISSION_ERRORS
                if exc.retryable and (
                    admission_wait
                    or delivery.item.attempt < self.max_worker_attempts
                ):
                    job.status = JobStatus.QUEUED
                    job.error = public_generation_error(
                        exc.code,
                        exc.message,
                        retryable=True,
                        details={
                            "attempt": delivery.item.attempt,
                            "max_attempts": self.max_worker_attempts,
                        },
                    )
                    job.updated_at = utc_now()
                    finished = await self.repository.finish_submission(
                        job, token=claim.token
                    )
                    if not finished:
                        raise RuntimeError("Submission claim was lost")
                    await self.queue.defer(
                        delivery,
                        delay_seconds=(
                            exc.retry_after_seconds
                            or (
                                self.provider_admission_retry_seconds
                                if admission_wait
                                else 0
                            )
                        ),
                        # Waiting for an account slot or RPM window is local
                        # admission, not another provider creation attempt.
                        increment_attempt=not admission_wait,
                    )
                    return job
                job.status = JobStatus.FAILED
                job.provider_last_poll_error = exc.code
                if exc.retryable:
                    job.error = public_async_error(
                        PublicAsyncErrorCode.PROVIDER_RETRIES_EXHAUSTED,
                        details={"attempts": delivery.item.attempt},
                    )
                else:
                    job.error = public_generation_error(
                        exc.code,
                        exc.message,
                        retryable=False,
                    )
                job.updated_at = utc_now()
                finished = await self.repository.finish_submission(
                    job, token=claim.token
                )
                if not finished:
                    raise RuntimeError("Submission claim was lost")
                await self.queue.ack(delivery)
                return job

            provider_submission_returned = True
            job.provider = provider_name
            job.provider_task_id = submission.provider_task_id
            job.status = JobStatus.PROCESSING
            job.progress = max(job.progress, 1)
            job.updated_at = utc_now()
            finished = await self.repository.finish_submission(job, token=claim.token)
            if not finished:
                raise RuntimeError("Submission claim was lost")
            await self.queue.ack(delivery)
            return job
        except _SubmissionClaimLost:
            # A cancelled in-flight call may already have committed upstream.
            # If this token still owns the durable fence, convert the job to a
            # non-billable reconciliation state. Otherwise the next delivery
            # will quarantine the expired SUBMITTING claim in the repository.
            self._require_submission_reconciliation(job)
            finished = await self.repository.finish_submission(job, token=claim.token)
            if not finished:
                raise
            await self.queue.ack(delivery)
            return job
        except Exception:
            if provider_submission_returned:
                # The external request may have committed. Preserve the claim
                # and delivery. On queue redelivery, an expired SUBMITTING claim
                # is quarantined instead of issuing another paid provider POST.
                raise

            if delivery.item.attempt >= self.max_worker_attempts:
                job.status = JobStatus.FAILED
                job.error = public_async_error(
                    PublicAsyncErrorCode.WORKER_ATTEMPTS_EXHAUSTED,
                    details={"attempts": delivery.item.attempt},
                )
                job.updated_at = utc_now()
                finished = await self.repository.finish_submission(
                    job, token=claim.token
                )
                if not finished:
                    # A newer claim owner must not be overwritten by stale work.
                    raise RuntimeError("Submission claim was lost")
                await self.queue.ack(delivery)
                return job

            # Router adapters convert any exception after an attempted provider
            # POST into ProviderError(submission_outcome_unknown=True), handled
            # above by reconciliation. Reaching this branch therefore proves
            # the failure occurred during local discovery/routing before POST.
            # Resetting SUBMITTING -> QUEUED must be atomic with claim release,
            # otherwise redelivery would quarantine a safe retry by mistake.
            released = await self.repository.release_submission_claim(
                job.id, token=claim.token
            )
            if not released:
                # Another owner took over, or this owner already committed a
                # result before queue acknowledgement failed. Do not overwrite
                # that state or emit an extra retry delivery.
                raise
            await self.queue.nack(delivery)
            raise

    async def receive_webhook(
        self, provider_name: str, body: bytes, headers: dict[str, str]
    ) -> WebhookReceipt:
        provider = self.router.provider(provider_name)
        if provider is None:
            raise RelayError(
                "PROVIDER_NOT_FOUND",
                "Webhook provider is not registered",
                status_code=404,
            )
        try:
            event = await provider.parse_webhook(body, headers)
        except ProviderError as exc:
            raise RelayError(
                exc.code,
                exc.message,
                status_code=401 if exc.code == "WEBHOOK_SIGNATURE_INVALID" else 422,
                retryable=exc.retryable,
            ) from exc

        return await self.apply_provider_event(provider_name, event)

    async def apply_provider_event(
        self,
        provider_name: str,
        event: ProviderWebhookEvent,
        *,
        poll_token: UUID | None = None,
    ) -> WebhookReceipt:

        job = await self.repository.find_by_provider_task(
            provider_name, event.provider_task_id
        )
        if job is None:
            raise RelayError(
                "PROVIDER_TASK_NOT_FOUND",
                "No local job matches this provider task",
                status_code=404,
            )
        status_map = {
            ProviderWebhookStatus.PROCESSING: JobStatus.PROCESSING,
            ProviderWebhookStatus.SUCCEEDED: JobStatus.SUCCEEDED,
            ProviderWebhookStatus.FAILED: JobStatus.FAILED,
            ProviderWebhookStatus.CANCELLED: JobStatus.CANCELLED,
        }
        job.status = status_map[event.status]
        if event.progress is not None:
            job.progress = max(job.progress, event.progress)
        if poll_token is not None:
            job.provider_poll_failures = 0
            job.provider_next_poll_at = None
            job.provider_last_poll_error = None
        if job.status == JobStatus.SUCCEEDED:
            if self.transfer_queue is None:
                raise RelayError(
                    "ARTIFACT_TRANSFER_NOT_CONFIGURED",
                    "Artifact transfer queue is not configured",
                    status_code=503,
                    retryable=True,
                )
            job.status = JobStatus.TRANSFERRING
            job.progress = max(job.progress, 95)
            job.outputs = []
            transfer_sources = []
            for index, output in enumerate(event.outputs):
                asset_id = uuid5(
                    NAMESPACE_URL,
                    f"relay:{job.id}:{index}:{output.media_type}",
                )
                transfer_sources.append(
                    TransferSource(
                        asset_id=asset_id,
                        source_url=output.url,
                        media_type=output.media_type,
                        declared_content_type=output.content_type,
                        object_key=(f"outputs/{job.tenant_id}/{job.id}/{asset_id}"),
                    )
                )
            job.transfer_sources = transfer_sources
            job.error = None
            job.updated_at = utc_now()
            item = WorkItem(job_id=job.id)
            result = await self.repository.begin_artifact_transfer(
                job,
                provider_name,
                event.event_id,
                item,
                poll_token=poll_token,
            )
            if result is None:
                raise _ProviderPollClaimLost(
                    "Provider poll claim was lost before artifact transfer"
                )
            event_is_new, transfer_started = result
            current = await self.repository.get(job.id)
            if not event_is_new or not transfer_started:
                assert current is not None
                if current.status in TERMINAL_STATUSES or current.status == JobStatus.TRANSFERRING:
                    await self.router.complete_job(current)
                return WebhookReceipt(
                    duplicate=not event_is_new,
                    job_id=current.id,
                    status=current.status,
                )
            await self.router.complete_job(job)
            if not self.repository.has_outbox:
                await self.transfer_queue.enqueue(item)
            return WebhookReceipt(job_id=job.id, status=job.status)

        if job.status == JobStatus.FAILED:
            assert event.error is not None
            job.provider_last_poll_error = event.error.code
            job.error = public_generation_error(
                event.error.code,
                event.error.message,
                retryable=event.error.retryable,
            )
        job.updated_at = utc_now()
        result = await self.repository.apply_webhook_event(
            job,
            provider_name,
            event.event_id,
            poll_token=poll_token,
        )
        if result is None:
            raise _ProviderPollClaimLost(
                "Provider poll claim was lost before event persistence"
            )
        event_is_new, job_updated = result
        current = await self.repository.get(job.id)
        assert current is not None
        if current.status in TERMINAL_STATUSES:
            await self.router.complete_job(current)
        if not event_is_new or not job_updated:
            return WebhookReceipt(
                duplicate=not event_is_new,
                job_id=current.id,
                status=current.status,
            )
        return WebhookReceipt(job_id=current.id, status=current.status)

    async def poll_provider_jobs(self, *, limit: int = 100) -> ProviderPollSummary:
        if limit < 1:
            raise ValueError("limit must be positive")
        claims = await self.repository.claim_processing_jobs(
            limit=limit,
            cursor=self._provider_poll_cursor,
            lease=self.provider_poll_claim_lease,
        )
        if claims:
            self._provider_poll_cursor = claims[-1].job.id
        semaphore = asyncio.Semaphore(self.provider_poll_concurrency)

        async def poll_one(
            claim: ProviderPollClaim,
        ) -> tuple[int, int, int]:
            stop = asyncio.Event()
            lost = asyncio.Event()
            heartbeat = asyncio.create_task(
                self._renew_provider_poll_claim(claim, stop, lost)
            )
            try:
                async with semaphore:
                    if lost.is_set():
                        return 0, 0, 0
                    return await self._poll_provider_job(
                        claim.job,
                        token=claim.token,
                        lost=lost,
                    )
            finally:
                stop.set()
                await heartbeat

        results = await asyncio.gather(
            *(poll_one(claim) for claim in claims),
            return_exceptions=True,
        )
        events = duplicates = failures = 0
        for result in results:
            if isinstance(result, BaseException):
                failures += 1
                continue
            result_events, result_duplicates, result_failures = result
            events += result_events
            duplicates += result_duplicates
            failures += result_failures
        return ProviderPollSummary(
            scanned=len(claims),
            events=events,
            duplicates=duplicates,
            failures=failures,
        )

    async def _renew_provider_poll_claim(
        self,
        claim: ProviderPollClaim,
        stop: asyncio.Event,
        lost: asyncio.Event,
    ) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self.provider_poll_claim_heartbeat_seconds,
                )
                return
            except TimeoutError:
                pass
            try:
                renewed = await self.repository.renew_provider_poll_claim(
                    claim.job.id,
                    token=claim.token,
                    lease=self.provider_poll_claim_lease,
                )
            except Exception:
                lost.set()
                return
            if not renewed:
                lost.set()
                return

    async def _poll_provider_job(
        self,
        job: GenerationJob,
        *,
        token: UUID,
        lost: asyncio.Event,
    ) -> tuple[int, int, int]:
        try:
            event = await self.router.poll(job)
        except ProviderError as exc:
            if lost.is_set():
                return 0, 0, 0
            if not exc.retryable and not exc.fail_job:
                # The provider rejected further polling without proving that
                # the upstream task failed. Stop automatic retries and retain
                # the exact route/task identity for operator reconciliation.
                job.status = JobStatus.RECONCILIATION_REQUIRED
                job.provider_next_poll_at = None
                job.provider_last_poll_error = exc.code
                job.error = public_async_error(
                    PublicAsyncErrorCode.PROVIDER_POLL_RECONCILIATION_REQUIRED,
                )
                job.updated_at = utc_now()
                finished = await self.repository.finish_provider_poll(job, token=token)
                return (0, 0, 1) if finished else (0, 0, 0)
            if exc.fail_job and job.provider and job.provider_task_id:
                event = ProviderWebhookEvent(
                    event_id=(
                        "poll-error-"
                        + str(
                            uuid5(
                                NAMESPACE_URL,
                                f"relay:{job.provider}:"
                                f"{job.provider_task_id}:{exc.code}",
                            )
                        )
                    ),
                    provider_task_id=job.provider_task_id,
                    status=ProviderWebhookStatus.FAILED,
                    error=public_generation_error(
                        exc.code,
                        "Provider task can no longer be reconciled",
                        retryable=False,
                    ),
                )
            else:
                attempt = min(job.provider_poll_failures + 1, 20)
                delay_seconds = min(
                    self.provider_poll_error_max_seconds,
                    self.provider_poll_error_base_seconds * (2 ** (attempt - 1)),
                )
                recorded = await self.repository.record_provider_poll_failure(
                    job.id,
                    token=token,
                    error_code=exc.code,
                    retry_delay=timedelta(seconds=delay_seconds),
                )
                return (0, 0, 1) if recorded else (0, 0, 0)
        except Exception:
            if lost.is_set():
                return 0, 0, 0
            recorded = await self.repository.record_provider_poll_failure(
                job.id,
                token=token,
                error_code="PROVIDER_POLL_INTERNAL_ERROR",
                retry_delay=timedelta(seconds=self.provider_poll_error_base_seconds),
            )
            return (0, 0, 1) if recorded else (0, 0, 0)

        if lost.is_set():
            return 0, 0, 0
        if event is None:
            await self.repository.record_provider_poll_success(job.id, token=token)
            return 0, 0, 0
        try:
            receipt = await self.apply_provider_event(
                job.provider or "", event, poll_token=token
            )
        except _ProviderPollClaimLost:
            return 0, 0, 0
        except (ProviderError, RelayError):
            return 0, 0, 1
        except Exception:
            return 0, 0, 1
        return 1, int(receipt.duplicate), 0
