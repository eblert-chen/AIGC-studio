from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    and_,
    case,
    delete,
    event,
    func,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .models import (
    CallbackDelivery,
    CallbackDeliveryStatus,
    CallbackDeliveryView,
    CallbackEvent,
    GeneratedAsset,
    GenerationInputs,
    GenerationJob,
    GenerationMode,
    JobStatus,
    OutboxMessage,
    OutputOptions,
    PublicErrorDetail,
    TransferSource,
    WorkItem,
    callback_delivery_for_job,
)
from .repository import (
    ArtifactTransferClaim,
    CallbackClaim,
    CallbackRepository,
    JobRepository,
    OutboxRepository,
    ProviderPollClaim,
    SubmissionClaim,
    _merge_provider_update,
)
from .providers.base import ProviderRouteManifest
from .provider_monitoring import (
    ProviderAlertClaim,
    ProviderAlertCondition,
    ProviderAlertDeliveryStatus,
    ProviderAlertEvent,
    ProviderAlertEventType,
    ProviderAlertKind,
    ProviderHealthSample,
    ProviderMonitoringRepository,
    ProviderMonitoringStatus,
    ProviderMonitorCycleClaim,
    ProviderOutcomeSummary,
)
from .providers.pool import (
    AccountAcquireReason,
    AccountAcquireResult,
    ProviderAccountPool,
    ProviderAccountSnapshot,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


_ACTIVE_PROVIDER_STATUSES = (
    JobStatus.SUBMITTING.value,
    JobStatus.RECONCILIATION_REQUIRED.value,
    JobStatus.PROCESSING.value,
)


class Base(DeclarativeBase):
    pass


class JobRow(Base):
    __tablename__ = "generation_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source_client_id: Mapped[str | None] = mapped_column(String(128), index=True)
    client_reference_id: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    expected_capability_revision: Mapped[str] = mapped_column(
        String(71), nullable=False
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    inputs_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    output_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    callback_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider: Mapped[str | None] = mapped_column(String(128), index=True)
    provider_task_id: Mapped[str | None] = mapped_column(String(256), index=True)
    provider_poll_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    provider_next_poll_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    provider_last_poll_error: Mapped[str | None] = mapped_column(String(128))
    provider_poll_claim_token: Mapped[str | None] = mapped_column(String(36))
    provider_poll_claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    outputs_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    transfer_sources_json: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    error_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    submission_claim_token: Mapped[str | None] = mapped_column(String(36))
    submission_claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    transfer_claim_token: Mapped[str | None] = mapped_column(String(36))
    transfer_claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    __table_args__ = (
        CheckConstraint(
            "length(expected_capability_revision) = 71 AND "
            "expected_capability_revision LIKE 'sha256:%'",
            name="ck_generation_jobs_capability_revision_shape",
        ),
        Index(
            "ix_generation_jobs_provider_task",
            "provider",
            "provider_task_id",
            unique=True,
        ),
    )


class IdempotencyRow(Base):
    __tablename__ = "generation_idempotency"

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("generation_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class WebhookEventRow(Base):
    __tablename__ = "provider_webhook_events"

    provider: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class OutboxRow(Base):
    __tablename__ = "relay_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    topic: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, index=True
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class CallbackDeliveryRow(Base):
    __tablename__ = "callback_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("generation_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    callback_url: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    event_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    job_status: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, index=True
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[str | None] = mapped_column(String(36))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_status: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class ProviderAccountStateRow(Base):
    """Secret-free, cross-process admission state for one account route."""

    __tablename__ = "provider_account_states"

    route_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    max_concurrency: Mapped[int | None] = mapped_column(Integer)
    requests_per_minute: Mapped[int | None] = mapped_column(Integer)
    admission_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    admission_disabled_reason: Mapped[str | None] = mapped_column(String(32))
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    cooldown_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    rate_window_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    rate_window_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_submissions: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    last_acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        CheckConstraint(
            "priority >= 0", name="ck_provider_account_priority_nonnegative"
        ),
        CheckConstraint(
            "max_concurrency IS NULL OR max_concurrency > 0",
            name="ck_provider_account_max_concurrency_positive",
        ),
        CheckConstraint(
            "requests_per_minute IS NULL OR requests_per_minute > 0",
            name="ck_provider_account_rpm_positive",
        ),
        CheckConstraint(
            "consecutive_failures >= 0",
            name="ck_provider_account_failures_nonnegative",
        ),
        CheckConstraint(
            "rate_window_count >= 0",
            name="ck_provider_account_rate_count_nonnegative",
        ),
        CheckConstraint(
            "successful_submissions >= 0",
            name="ck_provider_account_success_nonnegative",
        ),
        Index(
            "uq_provider_account_identity",
            "provider_name",
            "account_id",
            unique=True,
        ),
    )


class ProviderHealthSampleRow(Base):
    __tablename__ = "provider_health_samples"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    route_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    healthy: Mapped[bool] = mapped_column(Boolean, nullable=False)
    admission_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    admission_disabled_reason: Mapped[str | None] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(128))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    __table_args__ = (
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_provider_health_latency_nonnegative",
        ),
        Index(
            "ix_provider_health_provider_checked",
            "provider_name",
            "checked_at",
        ),
    )


class ProviderOutcomeRow(Base):
    """One immutable, provider-attributable terminal result per generation."""

    __tablename__ = "provider_outcome_events"

    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    route_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    __table_args__ = (
        Index(
            "ix_provider_outcome_provider_occurred",
            "provider_name",
            "occurred_at",
        ),
    )


class ProviderAlertStateRow(Base):
    __tablename__ = "provider_alert_states"

    fingerprint: Mapped[str] = mapped_column(String(256), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    breach_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recovery_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    details_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "breach_count >= 0", name="ck_provider_alert_breach_nonnegative"
        ),
        CheckConstraint(
            "recovery_count >= 0", name="ck_provider_alert_recovery_nonnegative"
        ),
    )


class ProviderAlertEventRow(Base):
    __tablename__ = "provider_alert_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    details_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    delivery_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    claim_token: Mapped[str | None] = mapped_column(String(36))
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_status: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(256))

    __table_args__ = (
        CheckConstraint("attempts >= 0", name="ck_provider_alert_attempts_nonnegative"),
    )


class ProviderMonitorLeaseRow(Base):
    __tablename__ = "provider_monitor_lease"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    worker_id: Mapped[str | None] = mapped_column(String(128))
    claim_token: Mapped[str | None] = mapped_column(String(36))
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    next_cycle_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    last_successful_cycle_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SqlAlchemyJobRepository(
    JobRepository,
    OutboxRepository,
    CallbackRepository,
    ProviderAccountPool,
    ProviderMonitoringRepository,
):
    """PostgreSQL repository; SQLite is supported for contract tests."""

    persistent = True
    kind = "sqlalchemy"
    has_outbox = True

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        use_database_coordination_clock: bool | None = None,
    ) -> None:
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.use_database_coordination_clock = (
            engine.dialect.name == "postgresql"
            if use_database_coordination_clock is None
            else use_database_coordination_clock
        )

    async def _coordination_now(
        self,
        session,
        *,
        fallback: datetime | None = None,
    ) -> datetime:
        if self.use_database_coordination_clock:
            value = await session.scalar(select(func.clock_timestamp()))
            aware = _aware(value)
            if aware is None:  # pragma: no cover - database invariant
                raise RuntimeError("Database coordination clock unavailable")
            return aware
        return fallback or _now()

    @classmethod
    def from_url(cls, url: str, *, echo: bool = False) -> "SqlAlchemyJobRepository":
        engine = create_async_engine(url, echo=echo, pool_pre_ping=True)
        if url.startswith("sqlite"):

            @event.listens_for(engine.sync_engine, "connect")
            def enable_sqlite_foreign_keys(dbapi_connection, _):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        return cls(engine)

    async def create_schema(self) -> None:
        """Test/development helper. Production must run Alembic migrations."""

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def register_routes(self, manifests: list[ProviderRouteManifest]) -> None:
        """Upsert non-secret route metadata without re-enabling disabled accounts."""

        if not manifests:
            return
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            for manifest in manifests:
                values = {
                    "route_id": manifest.route_id,
                    "provider_name": manifest.provider_name,
                    "account_id": manifest.account_id,
                    "channel_type": manifest.channel_type.value,
                    "priority": manifest.priority,
                    "max_concurrency": manifest.max_concurrency,
                    "requests_per_minute": manifest.requests_per_minute,
                    "admission_enabled": True,
                    "admission_disabled_reason": None,
                    "consecutive_failures": 0,
                    "rate_window_count": 0,
                    "successful_submissions": 0,
                    "created_at": now,
                    "updated_at": now,
                }
                if self.engine.dialect.name == "postgresql":
                    statement = postgresql_insert(ProviderAccountStateRow).values(
                        **values
                    )
                elif self.engine.dialect.name == "sqlite":
                    statement = sqlite_insert(ProviderAccountStateRow).values(**values)
                else:  # pragma: no cover - only supported test/prod dialects
                    raise RuntimeError(
                        "Provider account pool requires PostgreSQL or SQLite"
                    )
                statement = statement.on_conflict_do_update(
                    index_elements=[ProviderAccountStateRow.route_id],
                    set_={
                        "provider_name": manifest.provider_name,
                        "account_id": manifest.account_id,
                        "channel_type": manifest.channel_type.value,
                        "priority": manifest.priority,
                        "max_concurrency": manifest.max_concurrency,
                        "requests_per_minute": manifest.requests_per_minute,
                        "updated_at": now,
                    },
                )
                await session.execute(statement)

    async def snapshots(
        self, route_ids: list[str]
    ) -> dict[str, ProviderAccountSnapshot]:
        if not route_ids:
            return {}
        unique_route_ids = sorted(set(route_ids))
        async with self.sessions() as session:
            rows = list(
                (
                    await session.scalars(
                        select(ProviderAccountStateRow).where(
                            ProviderAccountStateRow.route_id.in_(unique_route_ids)
                        )
                    )
                ).all()
            )
            active_rows = (
                await session.execute(
                    select(JobRow.provider, func.count(JobRow.id))
                    .where(
                        JobRow.provider.in_(unique_route_ids),
                        JobRow.status.in_(_ACTIVE_PROVIDER_STATUSES),
                    )
                    .group_by(JobRow.provider)
                )
            ).all()
        active = {
            route_id: int(count)
            for route_id, count in active_rows
            if route_id is not None
        }
        return {
            row.route_id: self._account_snapshot(row, active.get(row.route_id, 0))
            for row in rows
        }

    async def acquire(
        self,
        job_id: UUID,
        manifest: ProviderRouteManifest,
        *,
        owner_token: UUID | None = None,
    ) -> AccountAcquireResult:
        async with self.sessions.begin() as session:
            if self.engine.dialect.name == "sqlite":
                # SQLite has no SELECT FOR UPDATE. An immediate transaction is
                # the test/development equivalent of the PostgreSQL route-row
                # lock and prevents two repository instances from both seeing
                # the same free slot.
                await session.execute(text("BEGIN IMMEDIATE"))
            now = await self._coordination_now(session)
            state_statement = select(ProviderAccountStateRow).where(
                ProviderAccountStateRow.route_id == manifest.route_id
            )
            job_statement = select(JobRow).where(JobRow.id == str(job_id))
            if self.engine.dialect.name == "postgresql":
                state_statement = state_statement.with_for_update()
                job_statement = job_statement.with_for_update()
            state = await session.scalar(state_statement)
            if state is None:
                return AccountAcquireResult(AccountAcquireReason.DISABLED)
            job = await session.scalar(job_statement)
            if (
                job is None
                or job.status != JobStatus.SUBMITTING.value
                or owner_token is None
                or job.submission_claim_token != str(owner_token)
            ):
                return AccountAcquireResult(AccountAcquireReason.JOB_NOT_SUBMITTING)
            if job.provider == manifest.route_id:
                return AccountAcquireResult(AccountAcquireReason.ACQUIRED)
            if job.provider is not None:
                return AccountAcquireResult(AccountAcquireReason.ASSIGNMENT_CONFLICT)
            if not state.admission_enabled:
                return AccountAcquireResult(AccountAcquireReason.DISABLED)
            cooldown_until = _aware(state.cooldown_until)
            if cooldown_until is not None:
                if cooldown_until > now:
                    return AccountAcquireResult(
                        AccountAcquireReason.COOLDOWN,
                        (cooldown_until - now).total_seconds(),
                    )
                state.cooldown_until = None
                state.consecutive_failures = 0

            active_jobs = int(
                await session.scalar(
                    select(func.count(JobRow.id)).where(
                        JobRow.provider == manifest.route_id,
                        JobRow.status.in_(_ACTIVE_PROVIDER_STATUSES),
                    )
                )
                or 0
            )
            if (
                manifest.max_concurrency is not None
                and active_jobs >= manifest.max_concurrency
            ):
                return AccountAcquireResult(AccountAcquireReason.BUSY)

            rate_window_started_at = _aware(state.rate_window_started_at)
            if manifest.requests_per_minute is not None:
                window = timedelta(minutes=1)
                if (
                    rate_window_started_at is None
                    or rate_window_started_at + window <= now
                ):
                    rate_window_started_at = now
                    state.rate_window_started_at = now
                    state.rate_window_count = 0
                if state.rate_window_count >= manifest.requests_per_minute:
                    assert rate_window_started_at is not None
                    return AccountAcquireResult(
                        AccountAcquireReason.RATE_LIMITED,
                        max(
                            0.0,
                            (rate_window_started_at + window - now).total_seconds(),
                        ),
                    )
                state.rate_window_count += 1

            job.provider = manifest.route_id
            job.updated_at = now
            state.last_acquired_at = now
            state.updated_at = now
            return AccountAcquireResult(AccountAcquireReason.ACQUIRED)

    async def release_assignment(
        self,
        job_id: UUID,
        route_id: str,
        *,
        owner_token: UUID | None = None,
    ) -> bool:
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            state_statement = select(ProviderAccountStateRow).where(
                ProviderAccountStateRow.route_id == route_id
            )
            job_statement = select(JobRow).where(JobRow.id == str(job_id))
            if self.engine.dialect.name == "postgresql":
                state_statement = state_statement.with_for_update()
                job_statement = job_statement.with_for_update()
            state = await session.scalar(state_statement)
            job = await session.scalar(job_statement)
            if state is None or job is None:
                return False
            if owner_token is None or job.submission_claim_token != str(owner_token):
                return False
            if job.provider is None:
                return True
            if job.provider != route_id or job.status != JobStatus.SUBMITTING.value:
                return False
            job.provider = None
            job.updated_at = now
            return True

    async def record_failure(
        self,
        route_id: str,
        *,
        error_code: str,
        failure_threshold: int,
        cooldown: timedelta,
        disable_account: bool,
        job_id: UUID | None = None,
        release_assignment: bool = False,
        owner_token: UUID | None = None,
    ) -> bool:
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            state_statement = select(ProviderAccountStateRow).where(
                ProviderAccountStateRow.route_id == route_id
            )
            if self.engine.dialect.name == "postgresql":
                state_statement = state_statement.with_for_update()
            state = await session.scalar(state_statement)
            if state is None:
                return False

            job = None
            if release_assignment and job_id is not None:
                job_statement = select(JobRow).where(JobRow.id == str(job_id))
                if self.engine.dialect.name == "postgresql":
                    job_statement = job_statement.with_for_update()
                job = await session.scalar(job_statement)
                if job is None:
                    return False
                if owner_token is None or job.submission_claim_token != str(
                    owner_token
                ):
                    return False
                if job.provider not in {None, route_id}:
                    return False
                if (
                    job.provider == route_id
                    and job.status != JobStatus.SUBMITTING.value
                ):
                    return False
                if job.provider == route_id:
                    job.provider = None
                    job.updated_at = now

            state.consecutive_failures += 1
            state.last_failure_at = now
            state.last_error_code = error_code[:128]
            state.updated_at = now
            if disable_account:
                state.admission_enabled = False
                state.admission_disabled_reason = "provider_error"
                state.cooldown_until = None
            elif state.consecutive_failures >= failure_threshold:
                proposed_cooldown = now + cooldown
                existing_cooldown = _aware(state.cooldown_until)
                if existing_cooldown is None or existing_cooldown < proposed_cooldown:
                    state.cooldown_until = proposed_cooldown
            return True

    async def record_success(self, route_id: str, *, submission: bool = False) -> None:
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            state_statement = select(ProviderAccountStateRow).where(
                ProviderAccountStateRow.route_id == route_id
            )
            if self.engine.dialect.name == "postgresql":
                state_statement = state_statement.with_for_update()
            state = await session.scalar(state_statement)
            if state is None:
                return
            state.consecutive_failures = 0
            state.cooldown_until = None
            state.last_error_code = None
            if submission:
                state.successful_submissions += 1
            state.last_success_at = now
            state.updated_at = now

    async def record_channel_failure(
        self,
        provider_name: str,
        *,
        error_code: str,
        cooldown: timedelta,
    ) -> int:
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            proposed_cooldown = now + cooldown
            result = await session.execute(
                update(ProviderAccountStateRow)
                .where(
                    ProviderAccountStateRow.provider_name == provider_name,
                    ProviderAccountStateRow.admission_enabled.is_(True),
                )
                .values(
                    consecutive_failures=(
                        ProviderAccountStateRow.consecutive_failures + 1
                    ),
                    cooldown_until=case(
                        (
                            ProviderAccountStateRow.cooldown_until.is_(None),
                            proposed_cooldown,
                        ),
                        (
                            ProviderAccountStateRow.cooldown_until < proposed_cooldown,
                            proposed_cooldown,
                        ),
                        else_=ProviderAccountStateRow.cooldown_until,
                    ),
                    last_failure_at=now,
                    last_error_code=error_code[:128],
                    updated_at=now,
                )
            )
            return int(result.rowcount or 0)

    async def complete_job(self, job_id: UUID, route_id: str) -> None:
        # Active slots are derived from generation_jobs under the account-row
        # lock. Once the terminal/transferring transition commits, this job is
        # no longer counted; no second mutable counter can drift.
        return None

    async def set_admission_enabled(self, route_id: str, *, enabled: bool) -> bool:
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            statement = select(ProviderAccountStateRow).where(
                ProviderAccountStateRow.route_id == route_id
            )
            if self.engine.dialect.name == "postgresql":
                statement = statement.with_for_update()
            state = await session.scalar(statement)
            if state is None:
                return False
            state.admission_enabled = enabled
            state.admission_disabled_reason = None if enabled else "manual"
            state.updated_at = now
            if enabled:
                state.consecutive_failures = 0
                state.cooldown_until = None
                state.last_error_code = None
            return True

    async def claim_provider_monitor_cycle(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease: timedelta,
        minimum_interval: timedelta,
    ) -> ProviderMonitorCycleClaim | None:
        token = uuid4()
        async with self.sessions.begin() as session:
            if self.engine.dialect.name == "sqlite":
                await session.execute(text("BEGIN IMMEDIATE"))
            coordination_now = await self._coordination_now(session, fallback=now)
            initial_values = {
                "name": "provider-monitor",
                "worker_id": None,
                "claim_token": None,
                "claim_expires_at": None,
                "next_cycle_at": None,
                "last_successful_cycle_at": None,
                "updated_at": coordination_now,
            }
            if self.engine.dialect.name == "postgresql":
                initial_insert = postgresql_insert(ProviderMonitorLeaseRow).values(
                    **initial_values
                )
            elif self.engine.dialect.name == "sqlite":
                initial_insert = sqlite_insert(ProviderMonitorLeaseRow).values(
                    **initial_values
                )
            else:  # pragma: no cover - supported runtime/test dialects only
                raise RuntimeError("Provider monitor requires PostgreSQL or SQLite")
            await session.execute(
                initial_insert.on_conflict_do_nothing(
                    index_elements=[ProviderMonitorLeaseRow.name]
                )
            )
            statement = select(ProviderMonitorLeaseRow).where(
                ProviderMonitorLeaseRow.name == "provider-monitor"
            )
            if self.engine.dialect.name == "postgresql":
                statement = statement.with_for_update()
            row = await session.scalar(statement)
            assert row is not None
            claim_expires_at = _aware(row.claim_expires_at)
            next_cycle_at = _aware(row.next_cycle_at)
            if (
                row.claim_token is not None
                and claim_expires_at is not None
                and claim_expires_at > coordination_now
            ):
                return None
            if next_cycle_at is not None and next_cycle_at > coordination_now:
                return None
            row.worker_id = worker_id
            row.claim_token = str(token)
            row.claim_expires_at = coordination_now + lease
            row.next_cycle_at = coordination_now + minimum_interval
            row.updated_at = coordination_now
            return ProviderMonitorCycleClaim(
                token=token,
                observed_at=coordination_now,
            )

    async def finish_provider_monitor_cycle(self, token: UUID) -> bool:
        async with self.sessions.begin() as session:
            coordination_now = await self._coordination_now(session)
            result = await session.execute(
                update(ProviderMonitorLeaseRow)
                .where(
                    ProviderMonitorLeaseRow.name == "provider-monitor",
                    ProviderMonitorLeaseRow.claim_token == str(token),
                )
                .values(
                    worker_id=None,
                    claim_token=None,
                    claim_expires_at=None,
                    updated_at=coordination_now,
                )
            )
            return result.rowcount == 1

    async def commit_provider_monitor_cycle(
        self,
        token: UUID,
        samples: list[ProviderHealthSample],
        conditions: list[ProviderAlertCondition],
        *,
        breach_cycles: int,
        recovery_cycles: int,
        retention_before: datetime,
        now: datetime,
        lease_valid_at: datetime,
    ) -> tuple[bool, list[ProviderAlertEvent]]:
        async with self.sessions.begin() as session:
            if self.engine.dialect.name == "sqlite":
                await session.execute(text("BEGIN IMMEDIATE"))
            coordination_now = await self._coordination_now(
                session, fallback=lease_valid_at
            )
            statement = select(ProviderMonitorLeaseRow).where(
                ProviderMonitorLeaseRow.name == "provider-monitor"
            )
            if self.engine.dialect.name == "postgresql":
                statement = statement.with_for_update()
            lease_row = await session.scalar(statement)
            if (
                lease_row is None
                or lease_row.claim_token != str(token)
                or lease_row.claim_expires_at is None
                or _aware(lease_row.claim_expires_at) <= coordination_now
            ):
                return False, []
            session.add_all(
                [
                    ProviderHealthSampleRow(
                        id=str(uuid4()),
                        route_id=sample.route_id,
                        provider_name=sample.provider_name,
                        account_id=sample.account_id,
                        channel_type=(
                            sample.channel_type.value
                            if hasattr(sample.channel_type, "value")
                            else str(sample.channel_type)
                        ),
                        healthy=sample.healthy,
                        admission_enabled=sample.admission_enabled,
                        admission_disabled_reason=(sample.admission_disabled_reason),
                        error_code=(
                            sample.error_code[:128]
                            if sample.error_code is not None
                            else None
                        ),
                        latency_ms=sample.latency_ms,
                        checked_at=sample.checked_at,
                    )
                    for sample in samples
                ]
            )
            await session.execute(
                delete(ProviderHealthSampleRow).where(
                    ProviderHealthSampleRow.checked_at < retention_before
                )
            )
            transitions = await self._apply_provider_alert_conditions_in_session(
                session,
                conditions,
                breach_cycles=breach_cycles,
                recovery_cycles=recovery_cycles,
                now=now,
            )
            lease_row.worker_id = None
            lease_row.claim_token = None
            lease_row.claim_expires_at = None
            lease_row.last_successful_cycle_at = coordination_now
            lease_row.updated_at = coordination_now
            return True, transitions

    async def record_provider_health_samples(
        self, samples: list[ProviderHealthSample]
    ) -> None:
        if not samples:
            return
        async with self.sessions.begin() as session:
            session.add_all(
                [
                    ProviderHealthSampleRow(
                        id=str(uuid4()),
                        route_id=sample.route_id,
                        provider_name=sample.provider_name,
                        account_id=sample.account_id,
                        channel_type=(
                            sample.channel_type.value
                            if hasattr(sample.channel_type, "value")
                            else str(sample.channel_type)
                        ),
                        healthy=sample.healthy,
                        admission_enabled=sample.admission_enabled,
                        admission_disabled_reason=(sample.admission_disabled_reason),
                        error_code=(
                            sample.error_code[:128]
                            if sample.error_code is not None
                            else None
                        ),
                        latency_ms=sample.latency_ms,
                        checked_at=sample.checked_at,
                    )
                    for sample in samples
                ]
            )

    async def prune_provider_health_samples(self, *, before: datetime) -> int:
        async with self.sessions.begin() as session:
            result = await session.execute(
                delete(ProviderHealthSampleRow).where(
                    ProviderHealthSampleRow.checked_at < before
                )
            )
            return int(result.rowcount or 0)

    async def provider_outcome_summaries(
        self, *, since: datetime
    ) -> list[ProviderOutcomeSummary]:
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    select(
                        ProviderOutcomeRow.route_id,
                        ProviderOutcomeRow.provider_name,
                        func.sum(
                            case((ProviderOutcomeRow.succeeded.is_(True), 1), else_=0)
                        ),
                        func.sum(
                            case((ProviderOutcomeRow.succeeded.is_(False), 1), else_=0)
                        ),
                    )
                    .where(ProviderOutcomeRow.occurred_at >= since)
                    .group_by(
                        ProviderOutcomeRow.route_id,
                        ProviderOutcomeRow.provider_name,
                    )
                )
            ).all()
        return [
            ProviderOutcomeSummary(
                route_id=route_id,
                provider_name=provider_name,
                succeeded=int(succeeded or 0),
                failed=int(failed or 0),
            )
            for route_id, provider_name, succeeded, failed in rows
        ]

    async def apply_provider_alert_conditions(
        self,
        conditions: list[ProviderAlertCondition],
        *,
        breach_cycles: int,
        recovery_cycles: int,
        now: datetime,
    ) -> list[ProviderAlertEvent]:
        async with self.sessions.begin() as session:
            if self.engine.dialect.name == "sqlite":
                await session.execute(text("BEGIN IMMEDIATE"))
            return await self._apply_provider_alert_conditions_in_session(
                session,
                conditions,
                breach_cycles=breach_cycles,
                recovery_cycles=recovery_cycles,
                now=now,
            )

    async def _apply_provider_alert_conditions_in_session(
        self,
        session,
        conditions: list[ProviderAlertCondition],
        *,
        breach_cycles: int,
        recovery_cycles: int,
        now: datetime,
    ) -> list[ProviderAlertEvent]:
        transitions: list[ProviderAlertEvent] = []
        for condition in sorted(conditions, key=lambda item: item.fingerprint):
            statement = select(ProviderAlertStateRow).where(
                ProviderAlertStateRow.fingerprint == condition.fingerprint
            )
            if self.engine.dialect.name == "postgresql":
                statement = statement.with_for_update()
            state = await session.scalar(statement)
            if state is None:
                state = ProviderAlertStateRow(
                    fingerprint=condition.fingerprint,
                    kind=condition.kind.value,
                    provider_name=condition.provider_name,
                    active=False,
                    breach_count=0,
                    recovery_count=0,
                    details_json=dict(condition.details),
                    opened_at=None,
                    resolved_at=None,
                    last_observed_at=now,
                )
                session.add(state)
            else:
                state.kind = condition.kind.value
                state.provider_name = condition.provider_name
                state.details_json = dict(condition.details)
                state.last_observed_at = now
            if condition.breached is None:
                continue
            event_type: ProviderAlertEventType | None = None
            if condition.breached:
                state.recovery_count = 0
                if not state.active:
                    state.breach_count += 1
                    if state.breach_count >= breach_cycles:
                        state.active = True
                        state.breach_count = 0
                        state.opened_at = now
                        state.resolved_at = None
                        event_type = ProviderAlertEventType.TRIGGERED
            else:
                state.breach_count = 0
                if state.active:
                    state.recovery_count += 1
                    if state.recovery_count >= recovery_cycles:
                        state.active = False
                        state.recovery_count = 0
                        state.resolved_at = now
                        event_type = ProviderAlertEventType.RECOVERED
            if event_type is None:
                continue
            event = ProviderAlertEvent(
                id=uuid4(),
                fingerprint=condition.fingerprint,
                kind=condition.kind,
                event_type=event_type,
                provider_name=condition.provider_name,
                occurred_at=now,
                details=dict(condition.details),
            )
            session.add(
                ProviderAlertEventRow(
                    id=str(event.id),
                    fingerprint=event.fingerprint,
                    kind=event.kind.value,
                    event_type=event.event_type.value,
                    provider_name=event.provider_name,
                    details_json=dict(event.details),
                    occurred_at=event.occurred_at,
                    delivery_status=ProviderAlertDeliveryStatus.PENDING.value,
                    attempts=0,
                    available_at=now,
                )
            )
            transitions.append(event)
        return transitions

    async def claim_provider_alert_deliveries(
        self,
        *,
        batch_size: int,
        exclude_ids: set[UUID] | None = None,
        lease: timedelta = timedelta(seconds=60),
        max_attempts: int | None = None,
    ) -> list[ProviderAlertClaim]:
        if batch_size < 1:
            return []
        excluded = {str(item) for item in (exclude_ids or set())}
        if max_attempts is not None and max_attempts < 1:
            return []
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            available = and_(
                ProviderAlertEventRow.available_at <= now,
                or_(
                    ProviderAlertEventRow.delivery_status
                    == ProviderAlertDeliveryStatus.PENDING.value,
                    and_(
                        ProviderAlertEventRow.delivery_status
                        == ProviderAlertDeliveryStatus.DELIVERING.value,
                        ProviderAlertEventRow.claim_expires_at <= now,
                    ),
                ),
            )
            if excluded:
                available = and_(available, ProviderAlertEventRow.id.not_in(excluded))
            if max_attempts is not None:
                await session.execute(
                    update(ProviderAlertEventRow)
                    .where(
                        ProviderAlertEventRow.delivery_status
                        == ProviderAlertDeliveryStatus.PENDING.value,
                        ProviderAlertEventRow.attempts >= max_attempts,
                    )
                    .values(
                        delivery_status=(ProviderAlertDeliveryStatus.DEAD_LETTER.value),
                        claim_token=None,
                        claim_expires_at=None,
                        last_error="Alert delivery attempts exhausted",
                    )
                )
            statement = (
                select(ProviderAlertEventRow)
                .where(available)
                .order_by(
                    ProviderAlertEventRow.available_at,
                    ProviderAlertEventRow.occurred_at,
                    ProviderAlertEventRow.id,
                )
                .limit(batch_size)
            )
            if max_attempts is not None:
                statement = statement.where(
                    or_(
                        ProviderAlertEventRow.delivery_status
                        == ProviderAlertDeliveryStatus.DELIVERING.value,
                        ProviderAlertEventRow.attempts < max_attempts,
                    )
                )
            if self.engine.dialect.name == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            rows = list((await session.scalars(statement)).all())
            claims: list[ProviderAlertClaim] = []
            for row in rows:
                token = uuid4()
                row.delivery_status = ProviderAlertDeliveryStatus.DELIVERING.value
                row.claim_token = str(token)
                row.claim_expires_at = now + lease
                claims.append(
                    ProviderAlertClaim(
                        event=self._provider_alert_to_model(row), token=token
                    )
                )
            return claims

    async def mark_provider_alert_delivered(
        self,
        event_id: UUID,
        *,
        token: UUID,
        response_status: int,
    ) -> bool:
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            result = await session.execute(
                update(ProviderAlertEventRow)
                .where(
                    ProviderAlertEventRow.id == str(event_id),
                    ProviderAlertEventRow.delivery_status
                    == ProviderAlertDeliveryStatus.DELIVERING.value,
                    ProviderAlertEventRow.claim_token == str(token),
                )
                .values(
                    delivery_status=ProviderAlertDeliveryStatus.DELIVERED.value,
                    claim_token=None,
                    claim_expires_at=None,
                    delivered_at=now,
                    response_status=response_status,
                    last_error=None,
                )
            )
            return result.rowcount == 1

    async def release_provider_alert_delivery(
        self,
        event_id: UUID,
        *,
        token: UUID,
        error: str,
        retry_delay: timedelta,
        dead_letter: bool,
        response_status: int | None = None,
    ) -> bool:
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            result = await session.execute(
                update(ProviderAlertEventRow)
                .where(
                    ProviderAlertEventRow.id == str(event_id),
                    ProviderAlertEventRow.delivery_status
                    == ProviderAlertDeliveryStatus.DELIVERING.value,
                    ProviderAlertEventRow.claim_token == str(token),
                )
                .values(
                    delivery_status=(
                        ProviderAlertDeliveryStatus.DEAD_LETTER.value
                        if dead_letter
                        else ProviderAlertDeliveryStatus.PENDING.value
                    ),
                    available_at=now + retry_delay,
                    claim_token=None,
                    claim_expires_at=None,
                    response_status=response_status,
                    last_error=error[:256],
                    attempts=ProviderAlertEventRow.attempts + 1,
                )
            )
            return result.rowcount == 1

    async def list_provider_alert_events(
        self, *, limit: int = 100
    ) -> list[ProviderAlertEvent]:
        if limit < 1:
            return []
        async with self.sessions() as session:
            rows = list(
                (
                    await session.scalars(
                        select(ProviderAlertEventRow)
                        .order_by(
                            ProviderAlertEventRow.occurred_at.desc(),
                            ProviderAlertEventRow.id.desc(),
                        )
                        .limit(limit)
                    )
                ).all()
            )
        return [self._provider_alert_to_model(row) for row in rows]

    async def provider_monitoring_status(self) -> ProviderMonitoringStatus:
        async with self.sessions() as session:
            observed_at = await self._coordination_now(session)
            lease = await session.get(ProviderMonitorLeaseRow, "provider-monitor")
            delivery_rows = (
                await session.execute(
                    select(
                        ProviderAlertEventRow.delivery_status,
                        func.count(ProviderAlertEventRow.id),
                        func.min(ProviderAlertEventRow.occurred_at),
                    ).group_by(ProviderAlertEventRow.delivery_status)
                )
            ).all()
            active_alert_count = int(
                await session.scalar(
                    select(func.count(ProviderAlertStateRow.fingerprint)).where(
                        ProviderAlertStateRow.active.is_(True)
                    )
                )
                or 0
            )
        by_status = {
            status: (int(count or 0), _aware(oldest))
            for status, count, oldest in delivery_rows
        }
        pending_statuses = (
            ProviderAlertDeliveryStatus.PENDING.value,
            ProviderAlertDeliveryStatus.DELIVERING.value,
        )
        pending_counts = [
            by_status.get(status, (0, None))[0] for status in pending_statuses
        ]
        pending_dates = [
            oldest
            for status in pending_statuses
            if (oldest := by_status.get(status, (0, None))[1]) is not None
        ]
        dead_count, oldest_dead = by_status.get(
            ProviderAlertDeliveryStatus.DEAD_LETTER.value, (0, None)
        )
        return ProviderMonitoringStatus(
            observed_at=observed_at,
            last_successful_cycle_at=(
                _aware(lease.last_successful_cycle_at) if lease else None
            ),
            active_alert_count=active_alert_count,
            pending_delivery_count=sum(pending_counts),
            oldest_pending_at=min(pending_dates, default=None),
            dead_letter_count=dead_count,
            oldest_dead_letter_at=oldest_dead,
        )

    async def create_idempotent(
        self, job: GenerationJob, idempotency_key: str, request_hash: str
    ) -> tuple[GenerationJob, bool, bool]:
        tenant_id = str(job.tenant_id)
        async with self.sessions() as session:
            try:
                async with session.begin():
                    coordination_now = await self._coordination_now(session)
                    existing = await session.get(
                        IdempotencyRow, (tenant_id, idempotency_key)
                    )
                    if existing is not None:
                        row = await session.get(JobRow, existing.job_id)
                        assert row is not None
                        return (
                            self._to_model(row),
                            True,
                            existing.request_hash != request_hash,
                        )
                    session.add(self._to_row(job))
                    # SQLAlchemy cannot infer insert ordering here because these
                    # rows intentionally have no ORM relationship. Flush the
                    # parent first so PostgreSQL's immediate FK check succeeds.
                    await session.flush()
                    session.add(
                        IdempotencyRow(
                            tenant_id=tenant_id,
                            idempotency_key=idempotency_key,
                            request_hash=request_hash,
                            job_id=str(job.id),
                        )
                    )
                    session.add(
                        OutboxRow(
                            id=str(uuid4()),
                            topic="generation.submit",
                            payload_json=WorkItem(job_id=job.id).model_dump(
                                mode="json"
                            ),
                            available_at=coordination_now,
                            created_at=coordination_now,
                        )
                    )
                return job.model_copy(deep=True), False, False
            except IntegrityError:
                # A concurrent request won the unique key. Read and return it
                # exactly like the non-racing idempotent path.
                await session.rollback()
                existing = await session.get(
                    IdempotencyRow, (tenant_id, idempotency_key)
                )
                if existing is None:
                    raise
                row = await session.get(JobRow, existing.job_id)
                assert row is not None
                return (
                    self._to_model(row),
                    True,
                    existing.request_hash != request_hash,
                )

    async def get(self, job_id: UUID) -> GenerationJob | None:
        async with self.sessions() as session:
            row = await session.get(JobRow, str(job_id))
            return self._to_model(row) if row else None

    async def get_for_tenant(
        self, job_id: UUID, tenant_id: UUID
    ) -> GenerationJob | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(JobRow).where(
                    JobRow.id == str(job_id),
                    JobRow.tenant_id == str(tenant_id),
                )
            )
            return self._to_model(row) if row else None

    async def save(self, job: GenerationJob) -> None:
        async with self.sessions.begin() as session:
            row = await session.get(JobRow, str(job.id))
            if row is None:
                raise KeyError(f"Job {job.id} does not exist")
            await self._record_callback_transition(
                session, row.status, row.progress, job
            )
            self._apply_model(row, job)

    async def save_if_status(
        self, job: GenerationJob, *, expected_status: JobStatus
    ) -> bool:
        async with self.sessions.begin() as session:
            statement = select(JobRow).where(JobRow.id == str(job.id))
            if self.engine.dialect.name == "postgresql":
                statement = statement.with_for_update()
            row = await session.scalar(statement)
            if row is None or row.status != expected_status.value:
                return False
            await self._record_callback_transition(
                session, row.status, row.progress, job
            )
            self._apply_model(row, job)
            return True

    async def list_submission_reconciliations(
        self, tenant_id: UUID, *, limit: int = 100
    ) -> list[GenerationJob]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(JobRow)
                    .where(
                        JobRow.tenant_id == str(tenant_id),
                        JobRow.status == JobStatus.RECONCILIATION_REQUIRED.value,
                    )
                    .order_by(JobRow.created_at, JobRow.id)
                    .limit(limit)
                )
            ).all()
            return [self._to_model(row) for row in rows]

    async def claim_submission(
        self, job_id: UUID, *, lease: timedelta
    ) -> SubmissionClaim | None:
        token = uuid4()
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            # Never blindly repeat a provider POST after a worker crash. Once a
            # SUBMITTING lease expires, the upstream side effect is unknowable
            # and must be explicitly reconciled before billing can move.
            await session.execute(
                update(JobRow)
                .where(
                    JobRow.id == str(job_id),
                    JobRow.status == JobStatus.SUBMITTING.value,
                    or_(
                        JobRow.submission_claim_token.is_(None),
                        JobRow.submission_claim_expires_at.is_(None),
                        JobRow.submission_claim_expires_at <= now,
                    ),
                )
                .values(
                    status=JobStatus.RECONCILIATION_REQUIRED.value,
                    error_json={
                        "code": "SUBMISSION_RECONCILIATION_REQUIRED",
                        "message": (
                            "Provider submission outcome requires reconciliation"
                        ),
                        "retryable": False,
                        "details": {"provider_error": "SUBMISSION_CLAIM_EXPIRED"},
                    },
                    updated_at=now,
                    submission_claim_token=None,
                    submission_claim_expires_at=None,
                )
            )
            statement = (
                update(JobRow)
                .where(
                    JobRow.id == str(job_id),
                    JobRow.status == JobStatus.QUEUED.value,
                )
                .values(
                    status=JobStatus.SUBMITTING.value,
                    updated_at=now,
                    submission_claim_token=str(token),
                    submission_claim_expires_at=now + lease,
                )
                .returning(JobRow)
            )
            row = (await session.scalars(statement)).one_or_none()
            if row is None:
                return None
            return SubmissionClaim(job=self._to_model(row), token=token)

    async def renew_submission_claim(
        self, job_id: UUID, *, token: UUID, lease: timedelta
    ) -> bool:
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            result = await session.execute(
                update(JobRow)
                .where(
                    JobRow.id == str(job_id),
                    JobRow.status == JobStatus.SUBMITTING.value,
                    JobRow.submission_claim_token == str(token),
                )
                .values(submission_claim_expires_at=now + lease)
            )
            return result.rowcount == 1

    async def finish_submission(self, job: GenerationJob, *, token: UUID) -> bool:
        async with self.sessions.begin() as session:
            statement = select(JobRow).where(
                JobRow.id == str(job.id),
                JobRow.submission_claim_token == str(token),
            )
            if self.engine.dialect.name == "postgresql":
                statement = statement.with_for_update()
            row = await session.scalar(statement)
            if row is None:
                return False
            await self._record_callback_transition(
                session, row.status, row.progress, job
            )
            self._apply_model(row, job)
            row.submission_claim_token = None
            row.submission_claim_expires_at = None
            return True

    async def release_submission_claim(self, job_id: UUID, *, token: UUID) -> bool:
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            result = await session.execute(
                update(JobRow)
                .where(
                    JobRow.id == str(job_id),
                    JobRow.status == JobStatus.SUBMITTING.value,
                    JobRow.submission_claim_token == str(token),
                )
                .values(
                    status=JobStatus.QUEUED.value,
                    provider=None,
                    provider_task_id=None,
                    updated_at=now,
                    submission_claim_token=None,
                    submission_claim_expires_at=None,
                )
            )
            return result.rowcount == 1

    async def claim_artifact_transfer(
        self, job_id: UUID, *, lease: timedelta
    ) -> ArtifactTransferClaim | None:
        token = uuid4()
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            statement = (
                update(JobRow)
                .where(
                    JobRow.id == str(job_id),
                    JobRow.status == JobStatus.TRANSFERRING.value,
                    or_(
                        JobRow.transfer_claim_token.is_(None),
                        JobRow.transfer_claim_expires_at.is_(None),
                        JobRow.transfer_claim_expires_at <= now,
                    ),
                )
                .values(
                    transfer_claim_token=str(token),
                    transfer_claim_expires_at=now + lease,
                )
                .returning(JobRow)
            )
            row = (await session.scalars(statement)).one_or_none()
            if row is None:
                return None
            return ArtifactTransferClaim(job=self._to_model(row), token=token)

    async def renew_artifact_transfer_claim(
        self, job_id: UUID, *, token: UUID, lease: timedelta
    ) -> bool:
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            result = await session.execute(
                update(JobRow)
                .where(
                    JobRow.id == str(job_id),
                    JobRow.status == JobStatus.TRANSFERRING.value,
                    JobRow.transfer_claim_token == str(token),
                )
                .values(transfer_claim_expires_at=now + lease)
            )
            return result.rowcount == 1

    async def save_artifact_transfer_progress(
        self, job: GenerationJob, *, token: UUID
    ) -> bool:
        if job.status != JobStatus.TRANSFERRING:
            return False
        async with self.sessions.begin() as session:
            statement = select(JobRow).where(
                JobRow.id == str(job.id),
                JobRow.status == JobStatus.TRANSFERRING.value,
                JobRow.transfer_claim_token == str(token),
            )
            if self.engine.dialect.name == "postgresql":
                statement = statement.with_for_update()
            row = await session.scalar(statement)
            if row is None:
                return False
            await self._record_callback_transition(
                session, row.status, row.progress, job
            )
            self._apply_model(row, job)
            return True

    async def finish_artifact_transfer(
        self, job: GenerationJob, *, token: UUID
    ) -> bool:
        if job.status not in {
            JobStatus.TRANSFERRING,
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            return False
        async with self.sessions.begin() as session:
            statement = select(JobRow).where(
                JobRow.id == str(job.id),
                JobRow.status == JobStatus.TRANSFERRING.value,
                JobRow.transfer_claim_token == str(token),
            )
            if self.engine.dialect.name == "postgresql":
                statement = statement.with_for_update()
            row = await session.scalar(statement)
            if row is None:
                return False
            await self._record_callback_transition(
                session, row.status, row.progress, job
            )
            self._apply_model(row, job)
            row.transfer_claim_token = None
            row.transfer_claim_expires_at = None
            return True

    async def save_with_outbox(
        self, job: GenerationJob, topic: str, item: WorkItem
    ) -> None:
        async with self.sessions.begin() as session:
            coordination_now = await self._coordination_now(session)
            row = await session.get(JobRow, str(job.id))
            if row is None:
                raise KeyError(f"Job {job.id} does not exist")
            await self._record_callback_transition(
                session, row.status, row.progress, job
            )
            self._apply_model(row, job)
            session.add(
                OutboxRow(
                    id=str(uuid4()),
                    topic=topic,
                    payload_json=item.model_dump(mode="json"),
                    available_at=coordination_now,
                    created_at=coordination_now,
                )
            )

    async def begin_artifact_transfer(
        self,
        job: GenerationJob,
        provider: str,
        event_id: str,
        item: WorkItem,
        *,
        poll_token: UUID | None = None,
    ) -> tuple[bool, bool] | None:
        async with self.sessions() as session:
            try:
                async with session.begin():
                    coordination_now = await self._coordination_now(session)
                    statement = select(JobRow).where(JobRow.id == str(job.id))
                    if self.engine.dialect.name == "postgresql":
                        statement = statement.with_for_update()
                    row = await session.scalar(statement)
                    if poll_token is not None and (
                        row is None
                        or row.status != JobStatus.PROCESSING.value
                        or row.provider_poll_claim_token != str(poll_token)
                    ):
                        return None
                    existing_event = await session.get(
                        WebhookEventRow, (provider, event_id)
                    )
                    if existing_event is not None:
                        if poll_token is not None:
                            row.provider_poll_failures = 0
                            row.provider_next_poll_at = None
                            row.provider_last_poll_error = None
                            row.provider_poll_claim_token = None
                            row.provider_poll_claim_expires_at = None
                        return False, False
                    session.add(WebhookEventRow(provider=provider, event_id=event_id))
                    if row is None or row.status in {
                        JobStatus.TRANSFERRING.value,
                        JobStatus.SUCCEEDED.value,
                        JobStatus.FAILED.value,
                        JobStatus.CANCELLED.value,
                    }:
                        return True, False
                    replacement = _merge_provider_update(self._to_model(row), job)
                    if poll_token is not None:
                        replacement.provider_poll_failures = job.provider_poll_failures
                        replacement.provider_next_poll_at = job.provider_next_poll_at
                        replacement.provider_last_poll_error = (
                            job.provider_last_poll_error
                        )
                    await self._record_callback_transition(
                        session, row.status, row.progress, replacement
                    )
                    await self._record_provider_terminal_outcome(
                        session,
                        row,
                        succeeded=True,
                        occurred_at=replacement.updated_at,
                    )
                    self._apply_model(row, replacement)
                    if (
                        poll_token is not None
                        or replacement.status != JobStatus.PROCESSING
                    ):
                        row.provider_poll_claim_token = None
                        row.provider_poll_claim_expires_at = None
                    session.add(
                        OutboxRow(
                            id=str(uuid4()),
                            topic="artifact.transfer",
                            payload_json=item.model_dump(mode="json"),
                            available_at=coordination_now,
                            created_at=coordination_now,
                        )
                    )
                return True, True
            except IntegrityError:
                await session.rollback()
                existing_event = await session.get(
                    WebhookEventRow, (provider, event_id)
                )
                if existing_event is not None:
                    return False, False
                raise

    async def find_by_provider_task(
        self, provider: str, provider_task_id: str
    ) -> GenerationJob | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(JobRow).where(
                    JobRow.provider == provider,
                    JobRow.provider_task_id == provider_task_id,
                )
            )
            return self._to_model(row) if row else None

    async def list_processing_jobs(
        self, *, limit: int = 100, cursor: UUID | None = None
    ) -> list[GenerationJob]:
        if limit < 1:
            return []
        async with self.sessions() as session:
            now = await self._coordination_now(session)
            conditions = (
                JobRow.status == JobStatus.PROCESSING.value,
                JobRow.provider.is_not(None),
                JobRow.provider_task_id.is_not(None),
                or_(
                    JobRow.provider_next_poll_at.is_(None),
                    JobRow.provider_next_poll_at <= now,
                ),
            )
            statement = select(JobRow).where(*conditions)
            if cursor is not None:
                statement = statement.where(JobRow.id > str(cursor))
            rows = list(
                (
                    await session.scalars(statement.order_by(JobRow.id).limit(limit))
                ).all()
            )
            if cursor is not None and len(rows) < limit:
                rows.extend(
                    list(
                        (
                            await session.scalars(
                                select(JobRow)
                                .where(*conditions, JobRow.id <= str(cursor))
                                .order_by(JobRow.id)
                                .limit(limit - len(rows))
                            )
                        ).all()
                    )
                )
            return [self._to_model(row) for row in rows]

    async def claim_processing_jobs(
        self,
        *,
        limit: int = 100,
        cursor: UUID | None = None,
        lease: timedelta,
    ) -> list[ProviderPollClaim]:
        if limit < 1:
            return []
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            claim_available = or_(
                JobRow.provider_poll_claim_token.is_(None),
                JobRow.provider_poll_claim_expires_at.is_(None),
                JobRow.provider_poll_claim_expires_at <= now,
            )
            conditions = (
                JobRow.status == JobStatus.PROCESSING.value,
                JobRow.provider.is_not(None),
                JobRow.provider_task_id.is_not(None),
                or_(
                    JobRow.provider_next_poll_at.is_(None),
                    JobRow.provider_next_poll_at <= now,
                ),
                claim_available,
            )
            candidate_ids: list[str] = []
            statement = select(JobRow.id).where(*conditions)
            if cursor is not None:
                statement = statement.where(JobRow.id > str(cursor))
            candidate_ids.extend(
                list(
                    (
                        await session.scalars(
                            statement.order_by(JobRow.id).limit(limit)
                        )
                    ).all()
                )
            )
            if cursor is not None and len(candidate_ids) < limit:
                candidate_ids.extend(
                    list(
                        (
                            await session.scalars(
                                select(JobRow.id)
                                .where(*conditions, JobRow.id <= str(cursor))
                                .order_by(JobRow.id)
                                .limit(limit - len(candidate_ids))
                            )
                        ).all()
                    )
                )

            claims: list[ProviderPollClaim] = []
            for job_id in candidate_ids:
                token = uuid4()
                row = (
                    await session.scalars(
                        update(JobRow)
                        .where(
                            JobRow.id == job_id,
                            JobRow.status == JobStatus.PROCESSING.value,
                            JobRow.provider.is_not(None),
                            JobRow.provider_task_id.is_not(None),
                            or_(
                                JobRow.provider_next_poll_at.is_(None),
                                JobRow.provider_next_poll_at <= now,
                            ),
                            or_(
                                JobRow.provider_poll_claim_token.is_(None),
                                JobRow.provider_poll_claim_expires_at.is_(None),
                                JobRow.provider_poll_claim_expires_at <= now,
                            ),
                        )
                        .values(
                            provider_poll_claim_token=str(token),
                            provider_poll_claim_expires_at=now + lease,
                        )
                        .returning(JobRow)
                    )
                ).one_or_none()
                if row is not None:
                    claims.append(
                        ProviderPollClaim(job=self._to_model(row), token=token)
                    )
            return claims

    async def renew_provider_poll_claim(
        self, job_id: UUID, *, token: UUID, lease: timedelta
    ) -> bool:
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            result = await session.execute(
                update(JobRow)
                .where(
                    JobRow.id == str(job_id),
                    JobRow.status == JobStatus.PROCESSING.value,
                    JobRow.provider_poll_claim_token == str(token),
                )
                .values(provider_poll_claim_expires_at=now + lease)
            )
            return result.rowcount == 1

    async def finish_provider_poll(self, job: GenerationJob, *, token: UUID) -> bool:
        async with self.sessions.begin() as session:
            statement = select(JobRow).where(
                JobRow.id == str(job.id),
                JobRow.status == JobStatus.PROCESSING.value,
                JobRow.provider_poll_claim_token == str(token),
            )
            if self.engine.dialect.name == "postgresql":
                statement = statement.with_for_update()
            row = await session.scalar(statement)
            if row is None:
                return False
            await self._record_callback_transition(
                session, row.status, row.progress, job
            )
            self._apply_model(row, job)
            row.provider_poll_claim_token = None
            row.provider_poll_claim_expires_at = None
            return True

    async def record_provider_poll_failure(
        self,
        job_id: UUID,
        *,
        token: UUID,
        error_code: str,
        retry_delay: timedelta,
    ) -> bool:
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            result = await session.execute(
                update(JobRow)
                .where(
                    JobRow.id == str(job_id),
                    JobRow.status == JobStatus.PROCESSING.value,
                    JobRow.provider_poll_claim_token == str(token),
                )
                .values(
                    provider_poll_failures=(JobRow.provider_poll_failures + 1),
                    provider_last_poll_error=error_code[:128],
                    provider_next_poll_at=now + retry_delay,
                    provider_poll_claim_token=None,
                    provider_poll_claim_expires_at=None,
                )
            )
            return result.rowcount == 1

    async def record_provider_poll_success(self, job_id: UUID, *, token: UUID) -> bool:
        async with self.sessions.begin() as session:
            result = await session.execute(
                update(JobRow)
                .where(
                    JobRow.id == str(job_id),
                    JobRow.status == JobStatus.PROCESSING.value,
                    JobRow.provider_poll_claim_token == str(token),
                )
                .values(
                    provider_poll_failures=0,
                    provider_last_poll_error=None,
                    provider_next_poll_at=None,
                    provider_poll_claim_token=None,
                    provider_poll_claim_expires_at=None,
                )
            )
            return result.rowcount == 1

    async def apply_webhook_event(
        self,
        job: GenerationJob,
        provider: str,
        event_id: str,
        *,
        poll_token: UUID | None = None,
    ) -> tuple[bool, bool] | None:
        async with self.sessions() as session:
            try:
                async with session.begin():
                    statement = select(JobRow).where(JobRow.id == str(job.id))
                    if self.engine.dialect.name == "postgresql":
                        statement = statement.with_for_update()
                    row = await session.scalar(statement)
                    if poll_token is not None and (
                        row is None
                        or row.status != JobStatus.PROCESSING.value
                        or row.provider_poll_claim_token != str(poll_token)
                    ):
                        return None
                    existing_event = await session.get(
                        WebhookEventRow, (provider, event_id)
                    )
                    if existing_event is not None:
                        if poll_token is not None:
                            row.provider_poll_failures = 0
                            row.provider_next_poll_at = None
                            row.provider_last_poll_error = None
                            row.provider_poll_claim_token = None
                            row.provider_poll_claim_expires_at = None
                        return False, False
                    session.add(WebhookEventRow(provider=provider, event_id=event_id))
                    if row is None or row.status in {
                        JobStatus.TRANSFERRING.value,
                        JobStatus.SUCCEEDED.value,
                        JobStatus.FAILED.value,
                        JobStatus.CANCELLED.value,
                    }:
                        return True, False
                    replacement = _merge_provider_update(self._to_model(row), job)
                    if poll_token is not None:
                        replacement.provider_poll_failures = job.provider_poll_failures
                        replacement.provider_next_poll_at = job.provider_next_poll_at
                        replacement.provider_last_poll_error = (
                            job.provider_last_poll_error
                        )
                    await self._record_callback_transition(
                        session, row.status, row.progress, replacement
                    )
                    if replacement.status in {
                        JobStatus.FAILED,
                        JobStatus.CANCELLED,
                    }:
                        await self._record_provider_terminal_outcome(
                            session,
                            row,
                            succeeded=False,
                            occurred_at=replacement.updated_at,
                        )
                    self._apply_model(row, replacement)
                    if (
                        poll_token is not None
                        or replacement.status != JobStatus.PROCESSING
                    ):
                        row.provider_poll_claim_token = None
                        row.provider_poll_claim_expires_at = None
                return True, True
            except IntegrityError:
                await session.rollback()
                existing_event = await session.get(
                    WebhookEventRow, (provider, event_id)
                )
                if existing_event is not None:
                    return False, False
                raise

    async def _record_provider_terminal_outcome(
        self,
        session,
        row: JobRow,
        *,
        succeeded: bool,
        occurred_at: datetime,
    ) -> None:
        """Write the upstream result in the provider-event transaction.

        This intentionally happens before artifact storage. A successful
        provider generation followed by an OBS transfer failure remains an
        upstream success rather than poisoning the provider success rate.
        """

        route_id = row.provider
        if route_id is None:
            return
        account_state = await session.get(ProviderAccountStateRow, route_id)
        if account_state is None:
            return
        values = {
            "job_id": row.id,
            "route_id": route_id,
            "provider_name": account_state.provider_name,
            "channel_type": account_state.channel_type,
            "succeeded": succeeded,
            "occurred_at": occurred_at,
        }
        if self.engine.dialect.name == "postgresql":
            statement = postgresql_insert(ProviderOutcomeRow).values(**values)
        elif self.engine.dialect.name == "sqlite":
            statement = sqlite_insert(ProviderOutcomeRow).values(**values)
        else:  # pragma: no cover - supported runtime/test dialects only
            raise RuntimeError(
                "Provider outcome recording requires PostgreSQL or SQLite"
            )
        await session.execute(
            statement.on_conflict_do_nothing(index_elements=[ProviderOutcomeRow.job_id])
        )

    async def _record_callback_transition(
        self,
        session,
        previous_status: str,
        previous_progress: int,
        job: GenerationJob,
    ) -> None:
        if previous_status == job.status.value:
            if job.status != JobStatus.PROCESSING or previous_progress == job.progress:
                return
        delivery = callback_delivery_for_job(job)
        if delivery is None:
            return
        if await session.get(CallbackDeliveryRow, str(delivery.id)) is not None:
            return
        coordination_now = await self._coordination_now(session)
        callback_row = self._callback_to_row(delivery)
        callback_row.available_at = coordination_now
        callback_row.created_at = coordination_now
        session.add(callback_row)

    async def healthcheck(self) -> bool:
        try:
            async with self.sessions() as session:
                await session.execute(select(1))
            return True
        except Exception:
            return False

    async def claim_outbox(
        self, *, batch_size: int = 100, lease: timedelta = timedelta(seconds=60)
    ) -> list[OutboxMessage]:
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            stale_before = now - lease
            statement = (
                select(OutboxRow)
                .where(
                    OutboxRow.available_at <= now,
                    or_(
                        OutboxRow.status == "pending",
                        and_(
                            OutboxRow.status == "publishing",
                            OutboxRow.locked_at < stale_before,
                        ),
                    ),
                )
                .order_by(OutboxRow.created_at)
                .limit(batch_size)
            )
            if self.engine.dialect.name == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            rows = list((await session.scalars(statement)).all())
            for row in rows:
                row.status = "publishing"
                row.locked_at = now
                row.attempts += 1
            return [
                OutboxMessage(
                    id=UUID(row.id),
                    topic=row.topic,
                    item=WorkItem.model_validate(row.payload_json),
                    attempts=row.attempts,
                )
                for row in rows
            ]

    async def mark_outbox_published(self, message_id: UUID) -> None:
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            await session.execute(
                update(OutboxRow)
                .where(OutboxRow.id == str(message_id))
                .values(
                    status="published",
                    published_at=now,
                    locked_at=None,
                    last_error=None,
                )
            )

    async def release_outbox(self, message_id: UUID, error: str) -> None:
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            row = await session.get(OutboxRow, str(message_id))
            if row is None:
                return
            delay = min(2 ** max(row.attempts - 1, 0), 60)
            row.status = "pending"
            row.locked_at = None
            row.available_at = now + timedelta(seconds=delay)
            row.last_error = error[:2_000]

    async def claim_callback_deliveries(
        self,
        *,
        batch_size: int = 50,
        lease: timedelta = timedelta(seconds=60),
        exclude_ids: set[UUID] | None = None,
    ) -> list[CallbackClaim]:
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            stale_before = now - lease
            statement = (
                select(CallbackDeliveryRow)
                .where(
                    CallbackDeliveryRow.available_at <= now,
                    or_(
                        CallbackDeliveryRow.status
                        == CallbackDeliveryStatus.PENDING.value,
                        and_(
                            CallbackDeliveryRow.status
                            == CallbackDeliveryStatus.DELIVERING.value,
                            CallbackDeliveryRow.locked_at < stale_before,
                        ),
                    ),
                )
                .order_by(CallbackDeliveryRow.created_at)
                .limit(batch_size)
            )
            if exclude_ids:
                statement = statement.where(
                    CallbackDeliveryRow.id.not_in(
                        [str(delivery_id) for delivery_id in exclude_ids]
                    )
                )
            if self.engine.dialect.name == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            rows = list((await session.scalars(statement)).all())
            claims: list[CallbackClaim] = []
            for row in rows:
                token = uuid4()
                row.status = CallbackDeliveryStatus.DELIVERING.value
                row.locked_at = now
                row.claim_token = str(token)
                row.attempts += 1
                claims.append(
                    CallbackClaim(
                        delivery=self._callback_to_model(row),
                        token=token,
                    )
                )
            return claims

    async def mark_callback_delivered(
        self, delivery_id: UUID, *, token: UUID, response_status: int
    ) -> bool:
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            result = await session.execute(
                update(CallbackDeliveryRow)
                .where(
                    CallbackDeliveryRow.id == str(delivery_id),
                    CallbackDeliveryRow.status
                    == CallbackDeliveryStatus.DELIVERING.value,
                    CallbackDeliveryRow.claim_token == str(token),
                )
                .values(
                    status=CallbackDeliveryStatus.DELIVERED.value,
                    delivered_at=now,
                    locked_at=None,
                    claim_token=None,
                    response_status=response_status,
                    last_error=None,
                )
            )
            return result.rowcount == 1

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
        async with self.sessions.begin() as session:
            now = await self._coordination_now(session)
            target_status = (
                CallbackDeliveryStatus.DEAD_LETTER.value
                if dead_letter
                else CallbackDeliveryStatus.PENDING.value
            )
            result = await session.execute(
                update(CallbackDeliveryRow)
                .where(
                    CallbackDeliveryRow.id == str(delivery_id),
                    CallbackDeliveryRow.status
                    == CallbackDeliveryStatus.DELIVERING.value,
                    CallbackDeliveryRow.claim_token == str(token),
                )
                .values(
                    status=target_status,
                    locked_at=None,
                    claim_token=None,
                    available_at=now + retry_delay,
                    response_status=response_status,
                    last_error=error[:2_000],
                )
            )
            return result.rowcount == 1

    async def list_callback_deliveries(
        self,
        tenant_id: UUID,
        *,
        status: CallbackDeliveryStatus | None = None,
        limit: int = 100,
    ) -> list[CallbackDeliveryView]:
        async with self.sessions() as session:
            statement = (
                select(CallbackDeliveryRow)
                .where(CallbackDeliveryRow.tenant_id == str(tenant_id))
                .order_by(CallbackDeliveryRow.created_at.desc())
                .limit(limit)
            )
            if status is not None:
                statement = statement.where(CallbackDeliveryRow.status == status.value)
            rows = list((await session.scalars(statement)).all())
            return [
                CallbackDeliveryView(
                    event_id=UUID(row.id),
                    request_id=row.request_id,
                    job_id=UUID(row.job_id),
                    job_status=JobStatus(row.job_status),
                    delivery_status=CallbackDeliveryStatus(row.status),
                    attempts=row.attempts,
                    available_at=row.available_at,
                    delivered_at=row.delivered_at,
                    response_status=row.response_status,
                    last_error=row.last_error,
                    created_at=row.created_at,
                )
                for row in rows
            ]

    @staticmethod
    def _account_snapshot(
        row: ProviderAccountStateRow, active_jobs: int
    ) -> ProviderAccountSnapshot:
        return ProviderAccountSnapshot(
            route_id=row.route_id,
            admission_enabled=row.admission_enabled,
            active_jobs=active_jobs,
            consecutive_failures=row.consecutive_failures,
            cooldown_until=_aware(row.cooldown_until),
            rate_window_started_at=_aware(row.rate_window_started_at),
            rate_window_count=row.rate_window_count,
            successful_submissions=row.successful_submissions,
            last_acquired_at=_aware(row.last_acquired_at),
            last_error_code=row.last_error_code,
            admission_disabled_reason=row.admission_disabled_reason,
        )

    @staticmethod
    def _provider_alert_to_model(
        row: ProviderAlertEventRow,
    ) -> ProviderAlertEvent:
        return ProviderAlertEvent(
            id=UUID(row.id),
            fingerprint=row.fingerprint,
            kind=ProviderAlertKind(row.kind),
            event_type=ProviderAlertEventType(row.event_type),
            provider_name=row.provider_name,
            occurred_at=row.occurred_at,
            details=row.details_json or {},
            delivery_status=ProviderAlertDeliveryStatus(row.delivery_status),
            attempts=row.attempts,
            response_status=row.response_status,
            last_error=row.last_error,
        )

    async def dispose(self) -> None:
        await self.engine.dispose()

    @staticmethod
    def _to_row(job: GenerationJob) -> JobRow:
        row = JobRow(id=str(job.id), tenant_id=str(job.tenant_id))
        SqlAlchemyJobRepository._apply_model(row, job)
        return row

    @staticmethod
    def _apply_model(row: JobRow, job: GenerationJob) -> None:
        row.source_client_id = job.source_client_id
        row.client_reference_id = job.client_reference_id
        row.model = job.model
        row.expected_capability_revision = job.expected_capability_revision
        row.mode = job.mode.value
        row.inputs_json = job.inputs.model_dump(mode="json")
        row.output_json = job.output.model_dump(mode="json")
        row.metadata_json = job.metadata
        row.callback_url = job.callback_url
        row.status = job.status.value
        row.progress = job.progress
        row.provider = job.provider
        row.provider_task_id = job.provider_task_id
        row.provider_poll_failures = job.provider_poll_failures
        row.provider_next_poll_at = job.provider_next_poll_at
        row.provider_last_poll_error = job.provider_last_poll_error
        row.outputs_json = [output.model_dump(mode="json") for output in job.outputs]
        row.transfer_sources_json = [
            source.model_dump(mode="json") for source in job.transfer_sources
        ]
        row.error_json = job.error.model_dump(mode="json") if job.error else None
        row.created_at = job.created_at
        row.updated_at = job.updated_at

    @staticmethod
    def _to_model(row: JobRow) -> GenerationJob:
        return GenerationJob(
            id=UUID(row.id),
            tenant_id=UUID(row.tenant_id),
            source_client_id=row.source_client_id,
            client_reference_id=row.client_reference_id,
            model=row.model,
            expected_capability_revision=row.expected_capability_revision,
            mode=GenerationMode(row.mode),
            inputs=GenerationInputs.model_validate(row.inputs_json),
            output=OutputOptions.model_validate(row.output_json),
            metadata=row.metadata_json or {},
            callback_url=row.callback_url,
            status=JobStatus(row.status),
            progress=row.progress,
            provider=row.provider,
            provider_task_id=row.provider_task_id,
            provider_poll_failures=row.provider_poll_failures or 0,
            provider_next_poll_at=row.provider_next_poll_at,
            provider_last_poll_error=row.provider_last_poll_error,
            outputs=[
                GeneratedAsset.model_validate(item) for item in (row.outputs_json or [])
            ],
            transfer_sources=[
                TransferSource.model_validate(item)
                for item in (row.transfer_sources_json or [])
            ],
            error=(
                PublicErrorDetail.model_validate(row.error_json)
                if row.error_json
                else None
            ),
            created_at=_aware(row.created_at),
            updated_at=_aware(row.updated_at),
        )

    @staticmethod
    def _callback_to_row(delivery: CallbackDelivery) -> CallbackDeliveryRow:
        return CallbackDeliveryRow(
            id=str(delivery.id),
            tenant_id=str(delivery.tenant_id),
            job_id=str(delivery.job_id),
            callback_url=delivery.callback_url,
            request_id=delivery.request_id,
            event_json=delivery.event.model_dump(mode="json"),
            job_status=delivery.event.job.status.value,
            status=delivery.status.value,
            attempts=delivery.attempts,
            available_at=delivery.available_at,
            locked_at=delivery.locked_at,
            delivered_at=delivery.delivered_at,
            response_status=delivery.response_status,
            last_error=delivery.last_error,
            created_at=delivery.created_at,
        )

    @staticmethod
    def _callback_to_model(row: CallbackDeliveryRow) -> CallbackDelivery:
        return CallbackDelivery(
            id=UUID(row.id),
            tenant_id=UUID(row.tenant_id),
            job_id=UUID(row.job_id),
            callback_url=row.callback_url,
            request_id=row.request_id,
            event=CallbackEvent.model_validate(row.event_json),
            status=CallbackDeliveryStatus(row.status),
            attempts=row.attempts,
            available_at=row.available_at,
            locked_at=row.locked_at,
            delivered_at=row.delivered_at,
            response_status=row.response_status,
            last_error=row.last_error,
            created_at=row.created_at,
        )
