from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..models import (
    Company,
    CompanyResourceGrant,
    CompanyStatus,
    GenerationTask,
    PublicationAttempt,
    PublicationJob,
    PublicationJobStatus,
    PublisherConnection,
    PublisherConnectionStatus,
    PublisherOAuthSession,
    ResourceDefinition,
    ResourceKind,
    TaskArtifact,
    new_id,
    utcnow,
)
from .errors import ConflictError, NotFoundError, PermissionDeniedError
from ..publishing_adapters import PublisherOAuthGrant


AUTO_PUBLISH_RESOURCE_KEY = "feature.auto_publish"
MOCK_PROVIDER = "mock"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        # SQLite does not preserve the timezone marker for DateTime columns.
        # Platform writes these values in UTC, so a naive value read back from
        # SQLite is still UTC rather than local server time.
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class PublishingService:
    @staticmethod
    def require_entitlement(
        session: Session, *, company_id: str
    ) -> CompanyResourceGrant:
        now = utcnow()
        entitled = session.scalar(
            select(CompanyResourceGrant)
            .join(
                ResourceDefinition,
                ResourceDefinition.id == CompanyResourceGrant.resource_id,
            )
            .where(
                CompanyResourceGrant.company_id == company_id,
                CompanyResourceGrant.enabled.is_(True),
                or_(
                    CompanyResourceGrant.effective_at.is_(None),
                    CompanyResourceGrant.effective_at <= now,
                ),
                or_(
                    CompanyResourceGrant.expires_at.is_(None),
                    CompanyResourceGrant.expires_at > now,
                ),
                ResourceDefinition.key == AUTO_PUBLISH_RESOURCE_KEY,
                ResourceDefinition.kind == ResourceKind.FEATURE,
                ResourceDefinition.active.is_(True),
            ).with_for_update()
        )
        if entitled is None:
            raise PermissionDeniedError(
                "Company is not entitled to feature.auto_publish"
            )
        return entitled

    @staticmethod
    def _required_text(value: str, *, field_name: str, max_length: int) -> str:
        normalized = value.strip()
        if not normalized:
            raise ConflictError(f"{field_name} must not be blank")
        if len(normalized) > max_length:
            raise ConflictError(f"{field_name} is too long")
        return normalized

    @staticmethod
    def _require_connection_usable(
        connection: PublisherConnection, *, environment: str
    ) -> None:
        if connection.status != PublisherConnectionStatus.ACTIVE:
            raise ConflictError("Publisher connection is not active")
        if environment in {"production", "staging"} and connection.provider == MOCK_PROVIDER:
            raise PermissionDeniedError(
                "Mock publisher connections are forbidden in production"
            )

    @classmethod
    def list_connections(
        cls, session: Session, *, company_id: str
    ) -> list[PublisherConnection]:
        return list(
            session.scalars(
                select(PublisherConnection)
                .where(PublisherConnection.company_id == company_id)
                .order_by(
                    PublisherConnection.created_at.desc(),
                    PublisherConnection.id.desc(),
                )
            ).all()
        )

    @staticmethod
    def create_oauth_session(
        session: Session,
        *,
        company_id: str,
        user_id: str,
        provider: str,
        ttl_seconds: int,
    ) -> tuple[PublisherOAuthSession, str]:
        normalized_provider = provider.strip().lower()
        if not normalized_provider or len(normalized_provider) > 40:
            raise ConflictError("Publisher OAuth provider is invalid")
        state = secrets.token_urlsafe(32)
        now = utcnow()
        oauth_session = PublisherOAuthSession(
            state_sha256=hashlib.sha256(state.encode("ascii")).hexdigest(),
            company_id=company_id,
            created_by_user_id=user_id,
            provider=normalized_provider,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        session.add(oauth_session)
        session.flush()
        return oauth_session, state

    @staticmethod
    def claim_oauth_session(
        session: Session,
        *,
        state: str,
    ) -> PublisherOAuthSession:
        normalized_state = state.strip()
        if not normalized_state or len(normalized_state) > 256:
            raise NotFoundError("Publisher OAuth session does not exist")
        state_sha256 = hashlib.sha256(normalized_state.encode("utf-8")).hexdigest()
        oauth_session = session.scalar(
            select(PublisherOAuthSession)
            .where(PublisherOAuthSession.state_sha256 == state_sha256)
            .with_for_update()
        )
        if oauth_session is None:
            raise NotFoundError("Publisher OAuth session does not exist")
        now = utcnow()
        if oauth_session.consumed_at is not None:
            raise ConflictError("Publisher OAuth session was already used")
        if _as_utc(oauth_session.expires_at) <= _as_utc(now):
            oauth_session.consumed_at = now
            raise ConflictError("Publisher OAuth session expired")
        oauth_session.consumed_at = now
        session.flush()
        return oauth_session

    @classmethod
    def complete_oauth_connection(
        cls,
        session: Session,
        *,
        oauth_session: PublisherOAuthSession,
        grant: PublisherOAuthGrant,
    ) -> tuple[PublisherConnection, bool]:
        external_account_id = cls._required_text(
            grant.external_account_id,
            field_name="external_account_id",
            max_length=160,
        )
        display_name = cls._required_text(
            grant.display_name,
            field_name="display_name",
            max_length=120,
        )
        credential_reference = grant.credential_reference.strip()
        if (
            not credential_reference
            or len(credential_reference) > 512
            or any(character.isspace() for character in credential_reference)
            or any(ord(character) < 32 for character in credential_reference)
        ):
            raise ConflictError("Publisher credential reference is invalid")

        config: dict[str, object] = {
            "credential_reference": credential_reference,
        }
        if grant.credential_expires_at is not None:
            config["credential_expires_at"] = _as_utc(
                grant.credential_expires_at
            ).isoformat()

        connection = session.scalar(
            select(PublisherConnection)
            .where(
                PublisherConnection.company_id == oauth_session.company_id,
                PublisherConnection.provider == oauth_session.provider,
                PublisherConnection.external_account_id == external_account_id,
            )
            .with_for_update()
        )
        created = connection is None
        if connection is None:
            connection = PublisherConnection(
                company_id=oauth_session.company_id,
                created_by_user_id=oauth_session.created_by_user_id,
                provider=oauth_session.provider,
                display_name=display_name,
                external_account_id=external_account_id,
                status=PublisherConnectionStatus.ACTIVE,
                config=config,
            )
            session.add(connection)
        else:
            connection.display_name = display_name
            connection.status = PublisherConnectionStatus.ACTIVE
            connection.config = config
            connection.disabled_at = None
        session.flush()
        return connection, created

    @classmethod
    def get_connection_for_company(
        cls,
        session: Session,
        *,
        company_id: str,
        connection_id: str,
    ) -> PublisherConnection:
        connection = session.scalar(
            select(PublisherConnection).where(
                PublisherConnection.id == connection_id,
                PublisherConnection.company_id == company_id,
            )
        )
        if connection is None:
            raise NotFoundError("Publisher connection does not exist")
        return connection

    @classmethod
    def create_dev_connection(
        cls,
        session: Session,
        *,
        company_id: str,
        user_id: str,
        provider: str,
        display_name: str,
        environment: str,
        mock_enabled: bool,
    ) -> PublisherConnection:
        cls.require_entitlement(session, company_id=company_id)
        if environment not in {"development", "test"} or not mock_enabled:
            raise NotFoundError("Development publisher connections are disabled")
        if provider != MOCK_PROVIDER:
            raise ConflictError("Only provider=mock is supported by this endpoint")
        connection = PublisherConnection(
            company_id=company_id,
            created_by_user_id=user_id,
            provider=MOCK_PROVIDER,
            display_name=cls._required_text(
                display_name, field_name="display_name", max_length=120
            ),
            external_account_id=f"mock-{new_id()}",
            status=PublisherConnectionStatus.ACTIVE,
            config={"development_only": True},
        )
        session.add(connection)
        session.flush()
        return connection

    @classmethod
    def disable_connection(
        cls,
        session: Session,
        *,
        company_id: str,
        connection_id: str,
    ) -> tuple[PublisherConnection, bool]:
        connection = session.scalar(
            select(PublisherConnection)
            .where(
                PublisherConnection.id == connection_id,
                PublisherConnection.company_id == company_id,
            )
            .with_for_update()
        )
        if connection is None:
            raise NotFoundError("Publisher connection does not exist")
        if connection.status == PublisherConnectionStatus.DISABLED:
            return connection, False
        connection.status = PublisherConnectionStatus.DISABLED
        connection.disabled_at = utcnow()
        session.flush()
        return connection, True

    @staticmethod
    def request_fingerprint(
        *,
        artifact_id: str,
        connection_id: str,
        title: str,
        caption: str,
        scheduled_at: datetime | None,
        timezone_name: str,
    ) -> str:
        canonical_request = json.dumps(
            {
                "artifact_id": artifact_id,
                "connection_id": connection_id,
                "title": title,
                "caption": caption,
                "scheduled_at": (
                    _as_utc(scheduled_at).isoformat()
                    if scheduled_at is not None
                    else None
                ),
                "timezone": timezone_name,
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_replay(
        job: PublicationJob,
        *,
        user_id: str,
        request_fingerprint: str,
    ) -> PublicationJob:
        if (
            job.created_by_user_id != user_id
            or job.request_fingerprint != request_fingerprint
        ):
            raise ConflictError(
                "Idempotency key is already used by a different publication job"
            )
        return job

    @classmethod
    def create_job(
        cls,
        session: Session,
        *,
        company_id: str,
        user_id: str,
        artifact_id: str,
        connection_id: str,
        idempotency_key: str,
        title: str,
        caption: str,
        scheduled_at: datetime | None,
        timezone_name: str,
        environment: str,
        allow_company_artifacts: bool = False,
    ) -> tuple[PublicationJob, bool]:
        entitlement = cls.require_entitlement(session, company_id=company_id)
        company = session.get(Company, company_id)
        if company is None or company.status != CompanyStatus.ACTIVE:
            raise NotFoundError("Company does not exist or is unavailable")
        normalized_title = title.strip()
        normalized_caption = caption.strip()
        normalized_timezone = timezone_name.strip()
        if len(normalized_title) > 160:
            raise ConflictError("title is too long")
        if len(normalized_caption) > 5000:
            raise ConflictError("caption is too long")
        if not normalized_timezone or len(normalized_timezone) > 64:
            raise ConflictError("timezone is invalid")
        normalized_scheduled_at = (
            _as_utc(scheduled_at) if scheduled_at is not None else None
        )
        request_fingerprint = cls.request_fingerprint(
            artifact_id=artifact_id,
            connection_id=connection_id,
            title=normalized_title,
            caption=normalized_caption,
            scheduled_at=normalized_scheduled_at,
            timezone_name=normalized_timezone,
        )
        artifact_row = session.execute(
            select(TaskArtifact, GenerationTask.user_id)
            .join(GenerationTask, GenerationTask.id == TaskArtifact.task_id)
            .where(
                TaskArtifact.id == artifact_id,
                TaskArtifact.company_id == company_id,
                GenerationTask.company_id == company_id,
            )
        ).one_or_none()
        if artifact_row is None:
            raise NotFoundError("Task artifact does not exist")
        artifact, artifact_owner_user_id = artifact_row
        if (
            artifact_owner_user_id != user_id
            and not allow_company_artifacts
        ):
            # Do not reveal another employee's artifact identifier to an
            # ordinary publisher, even when they guessed a valid UUID.
            raise NotFoundError("Task artifact does not exist")
        existing = session.scalar(
            select(PublicationJob).where(
                PublicationJob.company_id == company_id,
                PublicationJob.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return (
                cls._validate_replay(
                    existing,
                    user_id=user_id,
                    request_fingerprint=request_fingerprint,
                ),
                False,
            )

        quota_filters = [PublicationJob.company_id == company_id]
        if entitlement.effective_at is not None:
            quota_filters.append(
                PublicationJob.created_at >= entitlement.effective_at
            )
        if entitlement.expires_at is not None:
            quota_filters.append(PublicationJob.created_at < entitlement.expires_at)
        if entitlement.call_quota is not None:
            used_calls = int(
                session.scalar(
                    select(func.count(PublicationJob.id)).where(*quota_filters)
                )
                or 0
            )
            if used_calls >= entitlement.call_quota:
                raise PermissionDeniedError("Auto-publish call quota is exhausted")
        if entitlement.concurrency_limit is not None:
            active_jobs = int(
                session.scalar(
                    select(func.count(PublicationJob.id)).where(
                        PublicationJob.company_id == company_id,
                        PublicationJob.status.in_(
                            (
                                PublicationJobStatus.PENDING_APPROVAL,
                                PublicationJobStatus.SCHEDULED,
                                PublicationJobStatus.QUEUED,
                                PublicationJobStatus.SUBMITTING,
                            )
                        ),
                    )
                )
                or 0
            )
            if active_jobs >= entitlement.concurrency_limit:
                raise PermissionDeniedError(
                    "Auto-publish concurrency limit is reached"
                )

        connection = session.scalar(
            select(PublisherConnection)
            .where(
                PublisherConnection.id == connection_id,
                PublisherConnection.company_id == company_id,
            )
            .with_for_update()
        )
        if connection is None:
            raise NotFoundError("Publisher connection does not exist")
        cls._require_connection_usable(connection, environment=environment)

        timestamp = utcnow()
        values = {
            "id": new_id(),
            "company_id": company_id,
            "created_by_user_id": user_id,
            "task_artifact_id": artifact.id,
            "connection_id": connection.id,
            "idempotency_key": idempotency_key,
            "request_fingerprint": request_fingerprint,
            "status": PublicationJobStatus.PENDING_APPROVAL,
            "title": normalized_title,
            "caption": normalized_caption,
            "timezone": normalized_timezone,
            "scheduled_at": normalized_scheduled_at,
            "approved_by_user_id": None,
            "approved_at": None,
            "cancelled_by_user_id": None,
            "cancelled_at": None,
            "published_at": None,
            "external_post_id": None,
            "external_post_url": None,
            "error_code": None,
            "error_message": None,
            "attempt_count": 0,
            "next_attempt_at": None,
            "submit_started_at": None,
            "lease_owner": None,
            "lease_token": None,
            "lease_expires_at": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        dialect_name = session.get_bind().dialect.name
        if dialect_name == "postgresql":
            insert_statement = postgresql_insert(PublicationJob)
        elif dialect_name == "sqlite":
            insert_statement = sqlite_insert(PublicationJob)
        else:
            raise RuntimeError(
                f"publication idempotency is not implemented for {dialect_name}"
            )
        inserted_id = session.scalar(
            insert_statement.values(**values)
            .on_conflict_do_nothing(
                index_elements=["company_id", "idempotency_key"]
            )
            .returning(PublicationJob.id)
        )
        if inserted_id is not None:
            job = session.get(PublicationJob, inserted_id)
            if job is None:
                raise RuntimeError("inserted publication job could not be loaded")
            return job, True
        winner = session.scalar(
            select(PublicationJob).where(
                PublicationJob.company_id == company_id,
                PublicationJob.idempotency_key == idempotency_key,
            )
        )
        if winner is None:
            raise RuntimeError("publication idempotency winner could not be loaded")
        return (
            cls._validate_replay(
                winner,
                user_id=user_id,
                request_fingerprint=request_fingerprint,
            ),
            False,
        )

    @classmethod
    def list_jobs_page(
        cls,
        session: Session,
        *,
        company_id: str,
        status: PublicationJobStatus | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[int, list[PublicationJob]]:
        statement = select(PublicationJob).where(
            PublicationJob.company_id == company_id
        )
        if status is not None:
            statement = statement.where(PublicationJob.status == status)
        total = int(
            session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        )
        items = list(
            session.scalars(
                statement.order_by(
                    PublicationJob.created_at.desc(), PublicationJob.id.desc()
                ).offset((page - 1) * page_size).limit(page_size)
            ).all()
        )
        return total, items

    @classmethod
    def get_job(
        cls, session: Session, *, company_id: str, job_id: str
    ) -> PublicationJob:
        job = session.scalar(
            select(PublicationJob).where(
                PublicationJob.id == job_id,
                PublicationJob.company_id == company_id,
            )
        )
        if job is None:
            raise NotFoundError("Publication job does not exist")
        return job

    @classmethod
    def job_detail(
        cls, session: Session, *, company_id: str, job_id: str
    ) -> dict:
        job = cls.get_job(session, company_id=company_id, job_id=job_id)
        attempts = list(
            session.scalars(
                select(PublicationAttempt)
                .where(
                    PublicationAttempt.company_id == company_id,
                    PublicationAttempt.job_id == job.id,
                )
                .order_by(PublicationAttempt.attempt_number)
            ).all()
        )
        return {
            **{
                column.key: getattr(job, column.key)
                for column in PublicationJob.__table__.columns
            },
            "attempts": attempts,
        }

    @classmethod
    def _locked_job(
        cls, session: Session, *, company_id: str, job_id: str
    ) -> PublicationJob:
        job = session.scalar(
            select(PublicationJob)
            .where(
                PublicationJob.id == job_id,
                PublicationJob.company_id == company_id,
            )
            .with_for_update()
        )
        if job is None:
            raise NotFoundError("Publication job does not exist")
        return job

    @staticmethod
    def _clear_lease(job: PublicationJob) -> None:
        job.lease_owner = None
        job.lease_token = None
        job.lease_expires_at = None

    @staticmethod
    def _queued_status(job: PublicationJob, *, now: datetime) -> PublicationJobStatus:
        if (
            job.scheduled_at is not None
            and _as_utc(job.scheduled_at) > now
        ):
            return PublicationJobStatus.SCHEDULED
        return PublicationJobStatus.QUEUED

    @classmethod
    def approve_job(
        cls,
        session: Session,
        *,
        company_id: str,
        job_id: str,
        actor_user_id: str,
        environment: str,
    ) -> PublicationJob:
        cls.require_entitlement(session, company_id=company_id)
        job = cls._locked_job(session, company_id=company_id, job_id=job_id)
        if job.status != PublicationJobStatus.PENDING_APPROVAL:
            raise ConflictError("Only a pending publication job can be approved")
        connection = session.scalar(
            select(PublisherConnection)
            .where(
                PublisherConnection.id == job.connection_id,
                PublisherConnection.company_id == company_id,
            )
            .with_for_update()
        )
        if connection is None:
            raise NotFoundError("Publisher connection does not exist")
        cls._require_connection_usable(connection, environment=environment)
        now = utcnow()
        job.status = cls._queued_status(job, now=now)
        job.approved_by_user_id = actor_user_id
        job.approved_at = now
        job.next_attempt_at = (
            _as_utc(job.scheduled_at)
            if job.status == PublicationJobStatus.SCHEDULED
            else now
        )
        job.error_code = None
        job.error_message = None
        cls._clear_lease(job)
        session.flush()
        return job

    @classmethod
    def reconcile_unknown_job(
        cls,
        session: Session,
        *,
        company_id: str,
        job_id: str,
        outcome: str,
        external_post_id: str | None,
        external_post_url: str | None,
        error_code: str | None,
        error_message: str | None,
    ) -> tuple[PublicationJob, bool]:
        job = cls._locked_job(session, company_id=company_id, job_id=job_id)
        normalized_post_id = (
            external_post_id.strip() if external_post_id is not None else None
        )
        normalized_post_url = (
            external_post_url.strip() if external_post_url is not None else None
        )
        normalized_error_code = error_code.strip() if error_code is not None else None
        normalized_error_message = (
            error_message.strip() if error_message is not None else None
        )
        if outcome == "published":
            if not normalized_post_id:
                raise ConflictError(
                    "Published reconciliation requires external_post_id"
                )
            if normalized_error_code is not None or normalized_error_message is not None:
                raise ConflictError(
                    "Published reconciliation cannot include failure fields"
                )
            if job.status == PublicationJobStatus.PUBLISHED:
                if (
                    job.external_post_id == normalized_post_id
                    and job.external_post_url == normalized_post_url
                ):
                    return job, False
                raise ConflictError(
                    "Publication job was already reconciled with another outcome"
                )
        elif outcome == "failed":
            if normalized_post_id is not None or normalized_post_url is not None:
                raise ConflictError(
                    "Failed reconciliation cannot include external publication fields"
                )
            final_error_code = (
                normalized_error_code
                or "manual_reconciliation_confirmed_not_published"
            )
            final_error_message = (
                normalized_error_message
                or "An authorized operator confirmed that no external post was created"
            )
            if (
                job.status == PublicationJobStatus.FAILED
                and job.error_code == final_error_code
                and job.error_message == final_error_message
            ):
                return job, False
        else:
            raise ConflictError("Unsupported publication reconciliation outcome")

        if job.status != PublicationJobStatus.SUBMISSION_UNKNOWN:
            raise ConflictError(
                "Only an unknown publication submission can be reconciled"
            )
        if any(
            value is not None
            for value in (
                job.lease_owner,
                job.lease_token,
                job.lease_expires_at,
            )
        ):
            raise ConflictError(
                "Publication submission still has an active worker lease"
            )

        now = utcnow()
        if outcome == "published":
            duplicate = session.scalar(
                select(PublicationJob.id).where(
                    PublicationJob.connection_id == job.connection_id,
                    PublicationJob.external_post_id == normalized_post_id,
                    PublicationJob.id != job.id,
                )
            )
            if duplicate is not None:
                raise ConflictError(
                    "External publication is already linked to another job"
                )
            job.status = PublicationJobStatus.PUBLISHED
            job.external_post_id = normalized_post_id
            job.external_post_url = normalized_post_url
            job.published_at = now
            job.error_code = None
            job.error_message = None
        else:
            job.status = PublicationJobStatus.FAILED
            job.external_post_id = None
            job.external_post_url = None
            job.published_at = None
            job.error_code = final_error_code
            job.error_message = final_error_message
        job.next_attempt_at = None
        cls._clear_lease(job)
        session.flush()
        return job, True

    @classmethod
    def cancel_job(
        cls,
        session: Session,
        *,
        company_id: str,
        job_id: str,
        actor_user_id: str,
    ) -> PublicationJob:
        job = cls._locked_job(session, company_id=company_id, job_id=job_id)
        cancellable = {
            PublicationJobStatus.PENDING_APPROVAL,
            PublicationJobStatus.SCHEDULED,
            PublicationJobStatus.QUEUED,
            PublicationJobStatus.REQUIRES_REAUTH,
        }
        if job.status not in cancellable:
            raise ConflictError(
                "Publication job cannot be cancelled from its current state"
            )
        job.status = PublicationJobStatus.CANCELLED
        job.cancelled_by_user_id = actor_user_id
        job.cancelled_at = utcnow()
        job.next_attempt_at = None
        cls._clear_lease(job)
        session.flush()
        return job

    @classmethod
    def retry_job(
        cls,
        session: Session,
        *,
        company_id: str,
        job_id: str,
        environment: str,
        max_attempts: int,
    ) -> PublicationJob:
        cls.require_entitlement(session, company_id=company_id)
        job = cls._locked_job(session, company_id=company_id, job_id=job_id)
        if job.status == PublicationJobStatus.SUBMISSION_UNKNOWN:
            raise ConflictError(
                "An unknown submission outcome must be reconciled, never retried"
            )
        if job.status not in {
            PublicationJobStatus.FAILED,
            PublicationJobStatus.REQUIRES_REAUTH,
        }:
            raise ConflictError(
                "Publication job cannot be retried from its current state"
            )
        if job.attempt_count >= max_attempts:
            raise ConflictError("Publication job exhausted its attempt limit")
        connection = session.scalar(
            select(PublisherConnection)
            .where(
                PublisherConnection.id == job.connection_id,
                PublisherConnection.company_id == company_id,
            )
            .with_for_update()
        )
        if connection is None:
            raise NotFoundError("Publisher connection does not exist")
        cls._require_connection_usable(connection, environment=environment)
        now = utcnow()
        job.status = cls._queued_status(job, now=now)
        job.next_attempt_at = (
            _as_utc(job.scheduled_at)
            if job.status == PublicationJobStatus.SCHEDULED
            else now
        )
        job.error_code = None
        job.error_message = None
        cls._clear_lease(job)
        session.flush()
        return job
