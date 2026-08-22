from __future__ import annotations

import argparse
import importlib
import inspect
import logging
import os
import signal
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Event
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, runtime_checkable
from urllib.parse import quote

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings, runtime_settings_are_protected
from .database import build_engine, build_session_factory
from .database_privileges import attest_platform_database
from .models import (
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
    utcnow,
)
from .publishing_adapters import (
    DeterministicPublicationError,
    PublicationReceipt,
    PublicationRequest,
    PublisherAdapterRegistry,
    PublisherReauthenticationRequired,
    SubmissionOutcomeUnknownError,
    TemporaryPreSubmissionError,
    build_publisher_registry,
    load_publisher_adapter,
)


logger = logging.getLogger("platform.publishing_worker")


AUTO_PUBLISH_RESOURCE_KEY = "feature.auto_publish"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        # SQLite drops timezone markers from DateTime values. Platform writes
        # these timestamps in UTC, so a naive value read back is still UTC.
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class PublicationArtifact:
    company_id: str
    artifact_id: str
    asset_id: str
    media_type: str
    content_type: str


@runtime_checkable
class PublicationMediaResolver(Protocol):
    """Turns a platform-owned artifact into provider-readable references."""

    def resolve(self, artifact: PublicationArtifact) -> tuple[str, ...]: ...


class MockPublicationMediaResolver:
    """Local-only media references for the explicit mock publisher."""

    def resolve(self, artifact: PublicationArtifact) -> tuple[str, ...]:
        return (
            "https://publisher.invalid/assets/"
            + quote(artifact.asset_id, safe=""),
        )


class UnavailablePublicationMediaResolver:
    def resolve(self, artifact: PublicationArtifact) -> tuple[str, ...]:
        raise DeterministicPublicationError(
            "publication_media_resolver_unconfigured",
            "Publication media resolver is not configured",
        )


def load_publication_media_resolver(
    spec: str,
    *,
    credential_manifest: Mapping[str, str] | None = None,
    require_credential_manifest: bool = False,
) -> PublicationMediaResolver:
    module_name, separator, factory_name = spec.partition(":")
    if not separator or not module_name or not factory_name:
        raise ValueError("media resolver spec must be python.module:factory")
    module = importlib.import_module(module_name)
    factory = getattr(module, factory_name)
    if require_credential_manifest:
        if credential_manifest is None:
            raise ValueError(
                "production media resolver requires an explicit credential manifest"
            )
        signature = inspect.signature(factory)
        manifest_parameter = signature.parameters.get("credential_manifest")
        if (
            manifest_parameter is None
            or manifest_parameter.kind is not inspect.Parameter.KEYWORD_ONLY
            or manifest_parameter.default is not inspect.Parameter.empty
        ):
            raise ValueError(
                "production media resolver factory must require keyword-only "
                "credential_manifest"
            )
        try:
            resolver = factory(credential_manifest=credential_manifest)
        except Exception:
            raise RuntimeError("publication media resolver factory failed") from None
    else:
        resolver = factory()
    if not isinstance(resolver, PublicationMediaResolver):
        raise TypeError("media resolver factory returned an invalid resolver")
    return resolver


def exponential_backoff_seconds(
    attempt_number: int,
    *,
    base_seconds: int = 5,
    cap_seconds: int = 3600,
) -> int:
    if attempt_number < 1:
        raise ValueError("attempt_number must be at least 1")
    if base_seconds < 1:
        raise ValueError("base_seconds must be at least 1")
    if cap_seconds < base_seconds:
        raise ValueError("cap_seconds must not be smaller than base_seconds")
    return min(cap_seconds, base_seconds * (2 ** min(attempt_number - 1, 30)))


@dataclass(frozen=True)
class ClaimedPublication:
    job_id: str
    lease_token: str
    attempt_number: int


@dataclass(frozen=True)
class PreparedPublication:
    claimed: ClaimedPublication
    request: PublicationRequest


@dataclass(frozen=True)
class PublishingWorkerResult:
    processed: bool
    job_id: str | None = None
    status: str | None = None
    attempt_number: int | None = None


class PublishingWorker:
    """Claims and submits publication jobs with durable token fencing.

    A lease is committed before any adapter call.  Once ``submit_started_at`` is
    recorded, an expired claim is quarantined as ``submission_unknown`` and is
    never submitted again automatically.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        registry: PublisherAdapterRegistry,
        media_resolver: PublicationMediaResolver,
        *,
        lease_owner: str,
        lease_seconds: int = 60,
        max_attempts: int = 5,
        backoff_base_seconds: int = 5,
        backoff_cap_seconds: int = 3600,
        clock: Callable[[], datetime] = utcnow,
    ):
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be at least 1")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if not lease_owner.strip():
            raise ValueError("lease_owner is required")
        self.session_factory = session_factory
        self.registry = registry
        self.media_resolver = media_resolver
        self.lease_owner = lease_owner[:120]
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_cap_seconds = backoff_cap_seconds
        self.clock = clock

    @staticmethod
    def _due_expression(now: datetime):
        return or_(
            and_(
                PublicationJob.status == PublicationJobStatus.SCHEDULED,
                PublicationJob.scheduled_at.is_not(None),
                PublicationJob.scheduled_at <= now,
            ),
            and_(
                PublicationJob.status == PublicationJobStatus.QUEUED,
                or_(
                    PublicationJob.next_attempt_at.is_(None),
                    PublicationJob.next_attempt_at <= now,
                ),
            ),
        )

    @staticmethod
    def _clear_lease(job: PublicationJob) -> None:
        job.lease_owner = None
        job.lease_token = None
        job.lease_expires_at = None

    @staticmethod
    def _result(job: PublicationJob) -> PublishingWorkerResult:
        status = job.status.value if hasattr(job.status, "value") else str(job.status)
        return PublishingWorkerResult(
            processed=True,
            job_id=job.id,
            status=status,
            attempt_number=job.attempt_count,
        )

    @staticmethod
    def _attempt_for_token(
        session: Session,
        *,
        job_id: str,
        lease_token: str,
    ) -> PublicationAttempt | None:
        return session.scalar(
            select(PublicationAttempt)
            .where(
                PublicationAttempt.job_id == job_id,
                PublicationAttempt.lease_token == lease_token,
            )
            .with_for_update()
        )

    def _quarantine_expired_unknown(self) -> PublishingWorkerResult | None:
        now = self.clock()
        with self.session_factory.begin() as session:
            job = session.scalar(
                select(PublicationJob)
                .where(
                    PublicationJob.status == PublicationJobStatus.SUBMITTING,
                    PublicationJob.lease_expires_at.is_not(None),
                    PublicationJob.lease_expires_at <= now,
                    PublicationJob.submit_started_at.is_not(None),
                )
                .order_by(PublicationJob.lease_expires_at, PublicationJob.id)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            old_token = job.lease_token
            # The worker that held this token may still return later.  Clearing
            # the token here fences that stale response from changing the job.
            job.status = PublicationJobStatus.SUBMISSION_UNKNOWN
            job.error_code = "publisher_lease_expired_after_submit"
            job.error_message = (
                "Publisher submission outcome requires reconciliation"
            )
            job.next_attempt_at = None
            self._clear_lease(job)
            if old_token:
                attempt = self._attempt_for_token(
                    session, job_id=job.id, lease_token=old_token
                )
                if attempt is not None:
                    attempt.status = PublicationAttemptStatus.SUBMISSION_UNKNOWN
                    attempt.error_code = job.error_code
                    attempt.error_message = job.error_message
                    attempt.finished_at = now
            return self._result(job)

    def _fail_exhausted_due_job(self) -> PublishingWorkerResult | None:
        now = self.clock()
        stale_before_submit = and_(
            PublicationJob.status == PublicationJobStatus.SUBMITTING,
            PublicationJob.lease_expires_at.is_not(None),
            PublicationJob.lease_expires_at <= now,
            PublicationJob.submit_started_at.is_(None),
        )
        with self.session_factory.begin() as session:
            job = session.scalar(
                select(PublicationJob)
                .where(
                    or_(self._due_expression(now), stale_before_submit),
                    PublicationJob.attempt_count >= self.max_attempts,
                )
                .order_by(PublicationJob.next_attempt_at, PublicationJob.created_at)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            old_token = job.lease_token
            job.status = PublicationJobStatus.FAILED
            job.error_code = "publication_attempt_limit_exhausted"
            job.error_message = (
                f"Publication attempt limit ({self.max_attempts}) exhausted"
            )
            job.next_attempt_at = None
            self._clear_lease(job)
            if old_token:
                attempt = self._attempt_for_token(
                    session, job_id=job.id, lease_token=old_token
                )
                if attempt is not None:
                    attempt.status = PublicationAttemptStatus.FAILED
                    attempt.error_code = job.error_code
                    attempt.error_message = job.error_message
                    attempt.finished_at = now
            return self._result(job)

    def _claim(self) -> ClaimedPublication | None:
        now = self.clock()
        stale_safe = and_(
            PublicationJob.status == PublicationJobStatus.SUBMITTING,
            PublicationJob.lease_expires_at.is_not(None),
            PublicationJob.lease_expires_at <= now,
            PublicationJob.submit_started_at.is_(None),
        )
        with self.session_factory.begin() as session:
            job = session.scalar(
                select(PublicationJob)
                .where(
                    or_(self._due_expression(now), stale_safe),
                    PublicationJob.attempt_count < self.max_attempts,
                )
                .order_by(PublicationJob.next_attempt_at, PublicationJob.created_at)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None

            previous_status = job.status
            previous_token = job.lease_token
            previous_attempt_number = job.attempt_count
            lease_token = uuid.uuid4().hex
            attempt_number = previous_attempt_number + 1
            token_predicate = (
                PublicationJob.lease_token.is_(None)
                if previous_token is None
                else PublicationJob.lease_token == previous_token
            )
            # The conditional update is important on SQLite, where FOR UPDATE is
            # ignored.  PostgreSQL additionally benefits from SKIP LOCKED.
            claimed = session.execute(
                update(PublicationJob)
                .where(
                    PublicationJob.id == job.id,
                    PublicationJob.status == previous_status,
                    token_predicate,
                    PublicationJob.attempt_count == previous_attempt_number,
                    or_(self._due_expression(now), stale_safe),
                )
                .values(
                    status=PublicationJobStatus.SUBMITTING,
                    lease_owner=self.lease_owner,
                    lease_token=lease_token,
                    lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                    submit_started_at=None,
                    attempt_count=attempt_number,
                    next_attempt_at=None,
                    error_code=None,
                    error_message=None,
                )
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                return None

            if previous_status == PublicationJobStatus.SUBMITTING and previous_token:
                expired_attempt = self._attempt_for_token(
                    session, job_id=job.id, lease_token=previous_token
                )
                if expired_attempt is not None:
                    expired_attempt.status = PublicationAttemptStatus.FAILED
                    expired_attempt.error_code = "worker_lease_expired_before_submit"
                    expired_attempt.error_message = (
                        "Worker lease expired before provider submission began"
                    )
                    expired_attempt.finished_at = now

            session.add(
                PublicationAttempt(
                    company_id=job.company_id,
                    job_id=job.id,
                    attempt_number=attempt_number,
                    status=PublicationAttemptStatus.SUBMITTING,
                    lease_token=lease_token,
                    started_at=now,
                )
            )
            return ClaimedPublication(
                job_id=job.id,
                lease_token=lease_token,
                attempt_number=attempt_number,
            )

    def _owns_claim(self, job: PublicationJob, claimed: ClaimedPublication) -> bool:
        return (
            job.status == PublicationJobStatus.SUBMITTING
            and job.lease_token == claimed.lease_token
            and job.attempt_count == claimed.attempt_number
        )

    def _prepare(
        self, claimed: ClaimedPublication
    ) -> PreparedPublication | PublishingWorkerResult:
        failure: tuple[str, str, PublicationJobStatus, bool] | None = None
        with self.session_factory() as session:
            job = session.get(PublicationJob, claimed.job_id)
            if job is None or not self._owns_claim(job, claimed):
                return PublishingWorkerResult(processed=False)
            connection = session.get(PublisherConnection, job.connection_id)
            artifact = session.get(TaskArtifact, job.task_artifact_id)
            if connection is None or artifact is None:
                failure = (
                    "publication_reference_missing",
                    "Publication connection or artifact does not exist",
                    PublicationJobStatus.FAILED,
                    False,
                )
            elif (
                connection.company_id != job.company_id
                or artifact.company_id != job.company_id
            ):
                failure = (
                    "publication_tenant_mismatch",
                    "Publication references do not belong to the job company",
                    PublicationJobStatus.FAILED,
                    False,
                )
            elif connection.status == PublisherConnectionStatus.REQUIRES_REAUTH:
                failure = (
                    "publisher_reauthentication_required",
                    "Publisher connection requires reauthentication",
                    PublicationJobStatus.REQUIRES_REAUTH,
                    False,
                )
            elif connection.status != PublisherConnectionStatus.ACTIVE:
                failure = (
                    "publisher_connection_inactive",
                    "Publisher connection is not active",
                    PublicationJobStatus.FAILED,
                    False,
                )
            if failure is None:
                assert connection is not None
                assert artifact is not None
                provider = connection.provider
                external_account_id = connection.external_account_id
                connection_config = dict(connection.config or {})
                job_id = job.id
                # Provider idempotency must be globally unique even when two
                # companies reuse the same Platform request key on a shared
                # destination account.
                idempotency_key = job.id
                title = job.title
                caption = job.caption
                artifact_snapshot = PublicationArtifact(
                    company_id=artifact.company_id,
                    artifact_id=artifact.id,
                    asset_id=artifact.asset_id,
                    media_type=artifact.media_type,
                    content_type=artifact.content_type,
                )

        if failure is not None:
            code, message, final_status, mark_connection_reauth = failure
            return self._finish_before_submit(
                claimed,
                code=code,
                message=message,
                final_status=final_status,
                mark_connection_reauth=mark_connection_reauth,
            )
        try:
            self.registry.require(provider)
        except (KeyError, RuntimeError):
            return self._finish_before_submit(
                claimed,
                code="publisher_adapter_unavailable",
                message="Publisher adapter is not configured for this provider",
            )
        try:
            media_urls = tuple(self.media_resolver.resolve(artifact_snapshot))
        except TemporaryPreSubmissionError as exc:
            return self._retry_before_submit(claimed, exc.code, exc.safe_message)
        except PublisherReauthenticationRequired as exc:
            return self._finish_before_submit(
                claimed,
                code=exc.code,
                message=exc.safe_message,
                final_status=PublicationJobStatus.REQUIRES_REAUTH,
                mark_connection_reauth=True,
            )
        except DeterministicPublicationError as exc:
            return self._finish_before_submit(
                claimed, code=exc.code, message=exc.safe_message
            )
        except Exception as exc:
            return self._retry_before_submit(
                claimed,
                "publication_media_resolution_failed",
                f"Publication media resolution raised {type(exc).__name__}",
            )
        if not media_urls or any(not item.strip() for item in media_urls):
            return self._finish_before_submit(
                claimed,
                code="publication_media_missing",
                message="Publication has no provider-readable media",
            )
        request = PublicationRequest(
            job_id=job_id,
            provider=provider,
            idempotency_key=idempotency_key,
            external_account_id=external_account_id,
            title=title,
            caption=caption,
            media_urls=media_urls,
            connection_config=MappingProxyType(connection_config),
        )
        return PreparedPublication(claimed=claimed, request=request)

    def _lock_claim(
        self,
        session: Session,
        claimed: ClaimedPublication,
    ) -> tuple[PublicationJob | None, PublicationAttempt | None]:
        job = session.scalar(
            select(PublicationJob)
            .where(PublicationJob.id == claimed.job_id)
            .with_for_update()
        )
        if job is None or not self._owns_claim(job, claimed):
            return None, None
        attempt = self._attempt_for_token(
            session,
            job_id=claimed.job_id,
            lease_token=claimed.lease_token,
        )
        if attempt is None or attempt.attempt_number != claimed.attempt_number:
            return None, None
        return job, attempt

    def _finish_before_submit(
        self,
        claimed: ClaimedPublication,
        *,
        code: str,
        message: str,
        final_status: PublicationJobStatus = PublicationJobStatus.FAILED,
        mark_connection_reauth: bool = False,
    ) -> PublishingWorkerResult:
        now = self.clock()
        with self.session_factory.begin() as session:
            job, attempt = self._lock_claim(session, claimed)
            if job is None or attempt is None:
                return PublishingWorkerResult(processed=False)
            job.status = final_status
            job.error_code = code[:120]
            job.error_message = message[:2000]
            job.next_attempt_at = None
            self._clear_lease(job)
            attempt.status = (
                PublicationAttemptStatus.REQUIRES_REAUTH
                if final_status == PublicationJobStatus.REQUIRES_REAUTH
                else PublicationAttemptStatus.FAILED
            )
            attempt.error_code = job.error_code
            attempt.error_message = job.error_message
            attempt.finished_at = now
            if mark_connection_reauth:
                connection = session.get(PublisherConnection, job.connection_id)
                if connection is not None:
                    connection.status = PublisherConnectionStatus.REQUIRES_REAUTH
            return self._result(job)

    def _retry_before_submit(
        self,
        claimed: ClaimedPublication,
        code: str,
        message: str,
    ) -> PublishingWorkerResult:
        now = self.clock()
        with self.session_factory.begin() as session:
            job, attempt = self._lock_claim(session, claimed)
            if job is None or attempt is None:
                return PublishingWorkerResult(processed=False)
            exhausted = job.attempt_count >= self.max_attempts
            job.status = (
                PublicationJobStatus.FAILED
                if exhausted
                else PublicationJobStatus.QUEUED
            )
            job.error_code = code[:120]
            job.error_message = message[:2000]
            job.next_attempt_at = (
                None
                if exhausted
                else now
                + timedelta(
                    seconds=exponential_backoff_seconds(
                        claimed.attempt_number,
                        base_seconds=self.backoff_base_seconds,
                        cap_seconds=self.backoff_cap_seconds,
                    )
                )
            )
            self._clear_lease(job)
            attempt.status = PublicationAttemptStatus.FAILED
            attempt.error_code = job.error_code
            attempt.error_message = job.error_message
            attempt.finished_at = now
            return self._result(job)

    def _authorize_and_mark_submit_started(
        self, claimed: ClaimedPublication
    ) -> PublishingWorkerResult | None:
        """Apply the last side-effect gate and durably start submission.

        Resource/grant and connection administration uses row locks on the
        same records. This transaction is therefore the linearization point:
        a revocation or disable committed first prevents the provider call;
        once this transaction commits, the job is submission-in-flight and a
        lost worker must be reconciled instead of automatically retried.
        """

        now = self.clock()
        now_utc = _as_utc(now)
        with self.session_factory.begin() as session:
            job, attempt = self._lock_claim(session, claimed)
            if job is None or attempt is None:
                return PublishingWorkerResult(processed=False)

            # Match ResourceService's resource -> grant lock order so a grant
            # update and the final worker gate serialize without deadlocking.
            resource = session.scalar(
                select(ResourceDefinition)
                .where(
                    ResourceDefinition.key == AUTO_PUBLISH_RESOURCE_KEY,
                    ResourceDefinition.kind == ResourceKind.FEATURE,
                )
                .with_for_update()
            )
            grant = (
                session.scalar(
                    select(CompanyResourceGrant)
                    .where(
                        CompanyResourceGrant.company_id == job.company_id,
                        CompanyResourceGrant.resource_id == resource.id,
                    )
                    .with_for_update()
                )
                if resource is not None
                else None
            )
            connection = session.scalar(
                select(PublisherConnection)
                .where(PublisherConnection.id == job.connection_id)
                .with_for_update()
            )

            entitlement_active = (
                resource is not None
                and resource.active
                and grant is not None
                and grant.enabled
                and (
                    grant.effective_at is None
                    or _as_utc(grant.effective_at) <= now_utc
                )
                and (
                    grant.expires_at is None
                    or _as_utc(grant.expires_at) > now_utc
                )
            )
            failure: tuple[str, str, PublicationJobStatus] | None = None
            if not entitlement_active:
                failure = (
                    "auto_publish_entitlement_revoked",
                    "Company is no longer entitled to feature.auto_publish",
                    PublicationJobStatus.FAILED,
                )
            elif connection is None:
                failure = (
                    "publication_reference_missing",
                    "Publication connection does not exist",
                    PublicationJobStatus.FAILED,
                )
            elif connection.company_id != job.company_id:
                failure = (
                    "publication_tenant_mismatch",
                    "Publication connection does not belong to the job company",
                    PublicationJobStatus.FAILED,
                )
            elif connection.status == PublisherConnectionStatus.REQUIRES_REAUTH:
                failure = (
                    "publisher_reauthentication_required",
                    "Publisher connection requires reauthentication",
                    PublicationJobStatus.REQUIRES_REAUTH,
                )
            elif connection.status != PublisherConnectionStatus.ACTIVE:
                failure = (
                    "publisher_connection_inactive",
                    "Publisher connection is not active",
                    PublicationJobStatus.FAILED,
                )

            if failure is not None:
                code, message, final_status = failure
                job.status = final_status
                job.error_code = code[:120]
                job.error_message = message[:2000]
                job.next_attempt_at = None
                self._clear_lease(job)
                attempt.status = (
                    PublicationAttemptStatus.REQUIRES_REAUTH
                    if final_status == PublicationJobStatus.REQUIRES_REAUTH
                    else PublicationAttemptStatus.FAILED
                )
                attempt.error_code = job.error_code
                attempt.error_message = job.error_message
                attempt.finished_at = now
                return self._result(job)

            job.submit_started_at = now
            return None

    def _mark_published(
        self,
        claimed: ClaimedPublication,
        receipt: PublicationReceipt,
    ) -> PublishingWorkerResult:
        now = self.clock()
        with self.session_factory.begin() as session:
            job, attempt = self._lock_claim(session, claimed)
            if job is None or attempt is None:
                return PublishingWorkerResult(processed=False)
            job.status = PublicationJobStatus.PUBLISHED
            job.external_post_id = receipt.external_post_id
            job.external_post_url = receipt.external_post_url
            job.published_at = now
            job.error_code = None
            job.error_message = None
            job.next_attempt_at = None
            self._clear_lease(job)
            attempt.status = PublicationAttemptStatus.PUBLISHED
            attempt.external_post_id = receipt.external_post_id
            attempt.external_post_url = receipt.external_post_url
            attempt.provider_request_id = receipt.provider_request_id
            attempt.response_payload = {
                "provider_request_id": receipt.provider_request_id,
                **dict(receipt.response_metadata),
            }
            attempt.finished_at = now
            return self._result(job)

    def _mark_submission_unknown(
        self,
        claimed: ClaimedPublication,
        code: str,
        message: str,
    ) -> PublishingWorkerResult:
        now = self.clock()
        with self.session_factory.begin() as session:
            job, attempt = self._lock_claim(session, claimed)
            if job is None or attempt is None:
                return PublishingWorkerResult(processed=False)
            job.status = PublicationJobStatus.SUBMISSION_UNKNOWN
            job.error_code = code[:120]
            job.error_message = message[:2000]
            job.next_attempt_at = None
            self._clear_lease(job)
            attempt.status = PublicationAttemptStatus.SUBMISSION_UNKNOWN
            attempt.error_code = job.error_code
            attempt.error_message = job.error_message
            attempt.finished_at = now
            return self._result(job)

    def _mark_requires_reauth(
        self,
        claimed: ClaimedPublication,
        code: str,
        message: str,
    ) -> PublishingWorkerResult:
        return self._finish_before_submit(
            claimed,
            code=code,
            message=message,
            final_status=PublicationJobStatus.REQUIRES_REAUTH,
            mark_connection_reauth=True,
        )

    def run_once(self) -> PublishingWorkerResult:
        quarantined = self._quarantine_expired_unknown()
        if quarantined is not None:
            return quarantined
        exhausted = self._fail_exhausted_due_job()
        if exhausted is not None:
            return exhausted
        claimed = self._claim()
        if claimed is None:
            return PublishingWorkerResult(processed=False)
        prepared = self._prepare(claimed)
        if isinstance(prepared, PublishingWorkerResult):
            return prepared
        try:
            adapter = self.registry.require(prepared.request.provider)
        except (KeyError, RuntimeError):
            return self._finish_before_submit(
                claimed,
                code="publisher_adapter_unavailable",
                message="Publisher adapter is not configured for this provider",
            )
        gate_failure = self._authorize_and_mark_submit_started(claimed)
        if gate_failure is not None:
            return gate_failure
        try:
            receipt = adapter.submit(prepared.request)
        except TemporaryPreSubmissionError as exc:
            return self._retry_before_submit(claimed, exc.code, exc.safe_message)
        except DeterministicPublicationError as exc:
            return self._finish_before_submit(
                claimed, code=exc.code, message=exc.safe_message
            )
        except PublisherReauthenticationRequired as exc:
            return self._mark_requires_reauth(
                claimed, exc.code, exc.safe_message
            )
        except SubmissionOutcomeUnknownError as exc:
            return self._mark_submission_unknown(
                claimed, exc.code, exc.safe_message
            )
        except Exception as exc:
            # The adapter call began, so an unclassified exception cannot prove
            # non-creation.  Quarantine instead of risking a duplicate post.
            return self._mark_submission_unknown(
                claimed,
                "publisher_unclassified_submission_error",
                f"Publisher adapter raised {type(exc).__name__}; reconcile manually",
            )
        if not receipt.external_post_id.strip():
            return self._mark_submission_unknown(
                claimed,
                "publisher_receipt_invalid",
                "Publisher returned no external post identifier",
            )
        return self._mark_published(claimed, receipt)


def run_loop(
    worker: PublishingWorker,
    *,
    stop_event: Event,
    poll_interval_seconds: float = 1.0,
    batch_size: int = 50,
    once: bool = False,
    preflight: Callable[[], None] | None = None,
) -> int:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    processed_total = 0
    while not stop_event.is_set():
        processed_in_batch = 0
        for _ in range(batch_size):
            if stop_event.is_set():
                break
            if preflight is not None:
                preflight()
            result = worker.run_once()
            if not result.processed:
                break
            processed_in_batch += 1
            processed_total += 1
        if once:
            return processed_total
        if processed_in_batch < batch_size:
            stop_event.wait(max(poll_interval_seconds, 0.1))
    return processed_total


def _adapter_specs(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def main() -> None:
    settings = get_settings("publishing-worker")
    parser = argparse.ArgumentParser(
        description="Safely dispatch due social publication jobs"
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-interval-seconds", type=float, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    args = parser.parse_args()

    engine = build_engine(settings.database_url)
    attest_platform_database(engine, "publishing-worker")
    logging.basicConfig(level=logging.INFO)
    if not settings.publishing_worker_enabled:
        logger.warning(
            "Publishing worker is disabled; set PUBLISHING_WORKER_ENABLED=true "
            "only after adapters and media resolution are configured"
        )
        engine.dispose()
        return
    adapter_specs = _adapter_specs(settings.publishing_adapters)
    adapters = [
        load_publisher_adapter(
            spec,
            credential_manifest=(
                settings.publishing_plugin_secret_manifest("adapters", spec)
                if runtime_settings_are_protected(settings)
                else None
            ),
            require_credential_manifest=runtime_settings_are_protected(settings),
        )
        for spec in adapter_specs
    ]
    mock_enabled = bool(getattr(settings, "publishing_mock_enabled", False))
    registry = build_publisher_registry(
        environment=settings.environment,
        adapters=adapters,
        include_mock=mock_enabled,
    )
    if runtime_settings_are_protected(settings) and not registry.providers:
        raise RuntimeError(
            "Production publishing worker requires at least one production-ready adapter"
        )
    resolver_spec = settings.publishing_media_resolver.strip()
    if resolver_spec:
        media_resolver = load_publication_media_resolver(
            resolver_spec,
            credential_manifest=(
                settings.publishing_plugin_secret_manifest(
                    "media_resolvers", resolver_spec
                )
                if runtime_settings_are_protected(settings)
                else None
            ),
            require_credential_manifest=runtime_settings_are_protected(settings),
        )
    elif mock_enabled and settings.environment in {"development", "test"}:
        media_resolver = MockPublicationMediaResolver()
    else:
        if runtime_settings_are_protected(settings):
            raise RuntimeError(
                "Production publishing worker requires PUBLISHING_MEDIA_RESOLVER"
            )
        media_resolver = UnavailablePublicationMediaResolver()

    worker = PublishingWorker(
        build_session_factory(engine),
        registry,
        media_resolver,
        lease_owner=(
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
        ),
        lease_seconds=int(getattr(settings, "publishing_lease_seconds", 60)),
        max_attempts=int(getattr(settings, "publishing_max_attempts", 5)),
    )
    stop_event = Event()

    def stop(*_: Any) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        run_loop(
            worker,
            stop_event=stop_event,
            poll_interval_seconds=(
                args.poll_interval_seconds
                if args.poll_interval_seconds is not None
                else float(
                    getattr(
                        settings,
                        "publishing_worker_poll_interval_seconds",
                        1.0,
                    )
                )
            ),
            batch_size=(
                args.batch_size
                if args.batch_size is not None
                else int(getattr(settings, "publishing_worker_batch_size", 50))
            ),
            once=args.once,
            preflight=lambda: attest_platform_database(
                engine, "publishing-worker"
            ),
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
