from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import time
from typing import Callable, Literal, TypeVar
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    ChannelType,
    GenerationTask,
    RelayOperationsSnapshot,
    RelayRouteOperationsSnapshot,
    RelayTaskStage,
    RelayTaskStageEvent,
    new_id,
    utcnow,
)
from .errors import ConflictError, DomainError, NotFoundError


MAX_INT64 = 9_223_372_036_854_775_807


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value.astimezone(timezone.utc)


def _stored_utc(value: datetime) -> datetime:
    # SQLite drops timezone metadata. Every write is normalized to UTC.
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class StrictTelemetryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RelayTaskStagePayload(StrictTelemetryModel):
    schema_version: Literal[1]
    company_id: UUID
    task_id: UUID
    relay_job_id: UUID
    stage: RelayTaskStage
    occurred_at: datetime
    channel_key: str = Field(default="", max_length=120)
    channel_type: Literal["", "reverse", "third_party_api", "official"] = ""
    route_id: int | None = Field(default=None, strict=True, gt=0, le=MAX_INT64)
    provider_task_id: str = Field(default="", max_length=191)
    duration_ms: int | None = Field(
        default=None, strict=True, ge=0, le=MAX_INT64
    )
    error_code: str = Field(default="", max_length=160)

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_channel_binding(self) -> "RelayTaskStagePayload":
        if bool(self.channel_key) != bool(self.channel_type):
            raise ValueError(
                "channel_key and channel_type must both be assigned or both be empty"
            )
        return self


class RelayRouteOperationsPayload(StrictTelemetryModel):
    route_id: int = Field(strict=True, gt=0, le=MAX_INT64)
    channel_key: str = Field(min_length=1, max_length=120)
    channel_type: ChannelType
    provider_name: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=160)
    mode: str = Field(min_length=1, max_length=64)
    enabled: bool
    production_ready: bool
    health_status: Literal[
        "unknown", "healthy", "failed", "invalidated", "cooling", "disabled"
    ]
    failure_code: str = Field(default="", max_length=160)
    last_probe_at: datetime | None = None
    rpm_limit: int = Field(strict=True, ge=0, le=MAX_INT64)
    rpm_used: int = Field(strict=True, ge=0, le=MAX_INT64)
    active_task_count: int = Field(strict=True, ge=0, le=MAX_INT64)
    task_capacity: int = Field(strict=True, ge=0, le=MAX_INT64)
    cooling_account_count: int = Field(strict=True, ge=0, le=MAX_INT64)
    invalid_account_count: int = Field(strict=True, ge=0, le=MAX_INT64)
    busy_account_count: int = Field(strict=True, ge=0, le=MAX_INT64)
    rate_limited_account_count: int = Field(strict=True, ge=0, le=MAX_INT64)
    successful_task_count: int = Field(strict=True, ge=0, le=MAX_INT64)
    failed_task_count: int = Field(strict=True, ge=0, le=MAX_INT64)
    latency_p50_ms: int | None = Field(
        default=None, strict=True, ge=0, le=MAX_INT64
    )
    latency_p95_ms: int | None = Field(
        default=None, strict=True, ge=0, le=MAX_INT64
    )

    @field_validator("last_probe_at")
    @classmethod
    def validate_optional_timestamp(
        cls, value: datetime | None
    ) -> datetime | None:
        return _utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_latency_order(self) -> "RelayRouteOperationsPayload":
        if (
            self.latency_p50_ms is not None
            and self.latency_p95_ms is not None
            and self.latency_p95_ms < self.latency_p50_ms
        ):
            raise ValueError("latency_p95_ms must be greater than or equal to p50")
        return self


class RelayAccountPoolPayload(StrictTelemetryModel):
    total_accounts: int = Field(strict=True, ge=0, le=MAX_INT64)
    active_accounts: int = Field(strict=True, ge=0, le=MAX_INT64)
    cooling_accounts: int = Field(strict=True, ge=0, le=MAX_INT64)
    invalid_accounts: int = Field(strict=True, ge=0, le=MAX_INT64)
    busy_accounts: int = Field(strict=True, ge=0, le=MAX_INT64)
    rate_limited_accounts: int = Field(strict=True, ge=0, le=MAX_INT64)
    active_task_count: int = Field(strict=True, ge=0, le=MAX_INT64)
    task_capacity: int = Field(strict=True, ge=0, le=MAX_INT64)

    @model_validator(mode="after")
    def validate_account_totals(self) -> "RelayAccountPoolPayload":
        if any(
            count > self.total_accounts
            for count in (
                self.active_accounts,
                self.cooling_accounts,
                self.invalid_accounts,
                self.busy_accounts,
                self.rate_limited_accounts,
            )
        ):
            raise ValueError("account status counts cannot exceed total_accounts")
        return self


class RelayTaskOperationsPayload(StrictTelemetryModel):
    queued: int = Field(strict=True, ge=0, le=MAX_INT64)
    submitting: int = Field(strict=True, ge=0, le=MAX_INT64)
    submission_unknown: int = Field(strict=True, ge=0, le=MAX_INT64)
    provider_processing: int = Field(strict=True, ge=0, le=MAX_INT64)
    artifact_transferring: int = Field(strict=True, ge=0, le=MAX_INT64)
    succeeded: int = Field(strict=True, ge=0, le=MAX_INT64)
    failed: int = Field(strict=True, ge=0, le=MAX_INT64)
    cancelled: int = Field(strict=True, ge=0, le=MAX_INT64)
    rate_limited_count: int = Field(strict=True, ge=0, le=MAX_INT64)
    failover_count: int = Field(strict=True, ge=0, le=MAX_INT64)


class RelayDeliveriesPayload(StrictTelemetryModel):
    pending_alert_count: int = Field(strict=True, ge=0, le=MAX_INT64)
    dead_letter_alert_count: int = Field(strict=True, ge=0, le=MAX_INT64)
    oldest_pending_alert_at: datetime | None = None
    pending_cost_count: int = Field(strict=True, ge=0, le=MAX_INT64)
    dead_letter_cost_count: int = Field(strict=True, ge=0, le=MAX_INT64)
    pending_task_stage_count: int = Field(strict=True, ge=0, le=MAX_INT64)
    dead_letter_task_stage_count: int = Field(strict=True, ge=0, le=MAX_INT64)
    pending_snapshot_count: int = Field(strict=True, ge=0, le=MAX_INT64)
    dead_letter_snapshot_count: int = Field(strict=True, ge=0, le=MAX_INT64)

    @field_validator("oldest_pending_alert_at")
    @classmethod
    def validate_optional_timestamp(
        cls, value: datetime | None
    ) -> datetime | None:
        return _utc(value) if value is not None else None


class RelayCostsPayload(StrictTelemetryModel):
    successful_relay_jobs: int = Field(strict=True, ge=0, le=MAX_INT64)
    explicit_cost_relay_jobs: int = Field(strict=True, ge=0, le=MAX_INT64)
    delivered_cost_relay_jobs: int = Field(strict=True, ge=0, le=MAX_INT64)
    incomplete_relay_jobs: int = Field(strict=True, ge=0, le=MAX_INT64)
    native_billing_reconciliation_jobs: int = Field(
        strict=True, ge=0, le=MAX_INT64
    )
    reconciliation_complete: bool


class RelayOperationsSnapshotPayload(StrictTelemetryModel):
    schema_version: Literal[1]
    observed_at: datetime
    expires_at: datetime
    window_started_at: datetime
    monitor_fresh: bool
    monitor_last_completed_at: datetime | None = None
    routes: list[RelayRouteOperationsPayload] = Field(max_length=5000)
    account_pool: RelayAccountPoolPayload
    tasks: RelayTaskOperationsPayload
    deliveries: RelayDeliveriesPayload
    costs: RelayCostsPayload

    @field_validator(
        "observed_at",
        "expires_at",
        "window_started_at",
        "monitor_last_completed_at",
    )
    @classmethod
    def validate_timestamp(
        cls, value: datetime | None
    ) -> datetime | None:
        return _utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_snapshot(self) -> "RelayOperationsSnapshotPayload":
        if self.window_started_at >= self.observed_at:
            raise ValueError("window_started_at must be before observed_at")
        if self.expires_at <= self.observed_at:
            raise ValueError("expires_at must be after observed_at")
        if (
            self.monitor_last_completed_at is not None
            and self.monitor_last_completed_at > self.observed_at
        ):
            raise ValueError("monitor_last_completed_at must not be in the future")
        if (
            self.deliveries.oldest_pending_alert_at is not None
            and self.deliveries.oldest_pending_alert_at > self.observed_at
        ):
            raise ValueError("oldest_pending_alert_at must not be after observed_at")
        route_ids = [route.route_id for route in self.routes]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError("routes must contain unique route_id values")
        return self


class RelayTelemetryVerificationError(DomainError):
    def __init__(self, message: str = "Relay telemetry signature is invalid") -> None:
        super().__init__(message, "relay_telemetry_unauthorized", 401)


class RelayTelemetryPayloadError(DomainError):
    def __init__(self, message: str = "Relay telemetry payload is invalid") -> None:
        super().__init__(message, "relay_telemetry_invalid", 422)


PayloadT = TypeVar("PayloadT", bound=StrictTelemetryModel)


class RelayTelemetryVerifier:
    def __init__(
        self,
        signing_secret: str,
        *,
        max_age_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        secret = signing_secret.encode("utf-8")
        if len(secret) < 32:
            raise ValueError(
                "Relay telemetry signing secret must contain at least 32 bytes"
            )
        if max_age_seconds < 30:
            raise ValueError("Relay telemetry replay window must be at least 30 seconds")
        self._secret = secret
        self._max_age_seconds = max_age_seconds
        self._clock = clock

    def verify(
        self,
        raw_body: bytes,
        *,
        event_id: str | None,
        timestamp: str | None,
        signature: str | None,
        payload_type: type[PayloadT],
    ) -> tuple[PayloadT, str, datetime, str]:
        if not event_id or not timestamp or not signature:
            raise RelayTelemetryVerificationError(
                "Relay telemetry signature headers are required"
            )
        try:
            canonical_event_id = str(UUID(event_id))
            timestamp_value = int(timestamp)
            delivery_timestamp = datetime.fromtimestamp(
                timestamp_value, tz=timezone.utc
            )
        except (OverflowError, OSError, TypeError, ValueError):
            raise RelayTelemetryVerificationError() from None
        if canonical_event_id != event_id or str(timestamp_value) != timestamp:
            raise RelayTelemetryVerificationError()
        if abs(self._clock() - timestamp_value) > self._max_age_seconds:
            raise RelayTelemetryVerificationError(
                "Relay telemetry signature has expired"
            )
        if not signature.startswith("v1=") or len(signature) != 67:
            raise RelayTelemetryVerificationError()
        supplied_digest = signature[3:]
        if any(character not in "0123456789abcdef" for character in supplied_digest):
            raise RelayTelemetryVerificationError()
        signing_input = (
            timestamp.encode("ascii")
            + b"."
            + event_id.encode("ascii")
            + b"."
            + raw_body
        )
        expected_digest = hmac.new(
            self._secret, signing_input, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(supplied_digest, expected_digest):
            raise RelayTelemetryVerificationError()
        try:
            decoded = json.loads(
                raw_body,
                object_pairs_hook=_reject_duplicate_json_keys,
            )
            payload = payload_type.model_validate(decoded)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
            ValueError,
        ):
            raise RelayTelemetryPayloadError() from None
        return (
            payload,
            hashlib.sha256(raw_body).hexdigest(),
            delivery_timestamp,
            canonical_event_id,
        )


class RelayTelemetryService:
    @staticmethod
    def _existing_event(
        session: Session,
        *,
        model: type[RelayTaskStageEvent] | type[RelayOperationsSnapshot],
        event_id: str,
        payload_sha256: str,
    ) -> RelayTaskStageEvent | RelayOperationsSnapshot | None:
        existing = session.get(model, event_id)
        if existing is None:
            return None
        if not hmac.compare_digest(existing.payload_sha256, payload_sha256):
            raise ConflictError(
                "Relay telemetry event_id is already bound to different payload bytes"
            )
        return existing

    @classmethod
    def record_task_stage(
        cls,
        session: Session,
        *,
        payload: RelayTaskStagePayload,
        event_id: str,
        payload_sha256: str,
        delivery_timestamp: datetime,
        request_id: str,
    ) -> tuple[RelayTaskStageEvent, bool]:
        existing = cls._existing_event(
            session,
            model=RelayTaskStageEvent,
            event_id=event_id,
            payload_sha256=payload_sha256,
        )
        if existing is not None:
            assert isinstance(existing, RelayTaskStageEvent)
            return existing, True

        task = session.scalar(
            select(GenerationTask)
            .where(GenerationTask.id == str(payload.task_id))
            .with_for_update()
        )
        if task is None:
            raise NotFoundError("generation task does not exist")
        if task.company_id != str(payload.company_id):
            raise NotFoundError("no matching company and generation task exists")
        if task.relay_job_id != str(payload.relay_job_id):
            raise NotFoundError("no matching generation task and Relay job exists")
        if payload.occurred_at < _stored_utc(task.created_at) - timedelta(minutes=5):
            raise ConflictError("task stage cannot predate task admission")
        if payload.occurred_at > delivery_timestamp + timedelta(minutes=5):
            raise ConflictError("task stage occurred_at is implausibly in the future")

        entry = RelayTaskStageEvent(
            id=event_id,
            schema_version=payload.schema_version,
            company_id=task.company_id,
            task_id=task.id,
            relay_job_id=str(payload.relay_job_id),
            stage=payload.stage,
            occurred_at=payload.occurred_at,
            channel_key=payload.channel_key,
            channel_type=(
                ChannelType(payload.channel_type) if payload.channel_type else None
            ),
            route_id=payload.route_id,
            provider_task_id=payload.provider_task_id,
            duration_ms=payload.duration_ms,
            error_code=payload.error_code,
            delivery_timestamp=delivery_timestamp,
            payload_sha256=payload_sha256,
            request_id=request_id,
            received_at=utcnow(),
        )
        try:
            with session.begin_nested():
                session.add(entry)
                session.flush()
        except IntegrityError:
            repeated = cls._existing_event(
                session,
                model=RelayTaskStageEvent,
                event_id=event_id,
                payload_sha256=payload_sha256,
            )
            if repeated is None:
                raise
            assert isinstance(repeated, RelayTaskStageEvent)
            return repeated, True
        return entry, False

    @classmethod
    def record_operations_snapshot(
        cls,
        session: Session,
        *,
        payload: RelayOperationsSnapshotPayload,
        event_id: str,
        payload_sha256: str,
        delivery_timestamp: datetime,
        request_id: str,
    ) -> tuple[RelayOperationsSnapshot, bool]:
        existing = cls._existing_event(
            session,
            model=RelayOperationsSnapshot,
            event_id=event_id,
            payload_sha256=payload_sha256,
        )
        if existing is not None:
            assert isinstance(existing, RelayOperationsSnapshot)
            return existing, True

        if abs((payload.observed_at - delivery_timestamp).total_seconds()) > 300:
            raise ConflictError(
                "operations snapshot observed_at must be within five minutes of delivery"
            )
        if payload.expires_at > payload.observed_at + timedelta(minutes=15):
            raise ConflictError(
                "operations snapshot expires_at cannot exceed fifteen minutes"
            )

        account = payload.account_pool
        tasks = payload.tasks
        deliveries = payload.deliveries
        costs = payload.costs
        snapshot = RelayOperationsSnapshot(
            id=event_id,
            schema_version=payload.schema_version,
            observed_at=payload.observed_at,
            expires_at=payload.expires_at,
            window_started_at=payload.window_started_at,
            monitor_fresh=payload.monitor_fresh,
            monitor_last_completed_at=payload.monitor_last_completed_at,
            account_total=account.total_accounts,
            account_active=account.active_accounts,
            account_cooling=account.cooling_accounts,
            account_invalid=account.invalid_accounts,
            account_busy=account.busy_accounts,
            account_rate_limited=account.rate_limited_accounts,
            account_active_tasks=account.active_task_count,
            account_task_capacity=account.task_capacity,
            task_queued=tasks.queued,
            task_submitting=tasks.submitting,
            task_submission_unknown=tasks.submission_unknown,
            task_provider_processing=tasks.provider_processing,
            task_artifact_transferring=tasks.artifact_transferring,
            task_succeeded=tasks.succeeded,
            task_failed=tasks.failed,
            task_cancelled=tasks.cancelled,
            task_rate_limited_count=tasks.rate_limited_count,
            task_failover_count=tasks.failover_count,
            delivery_pending_alert_count=deliveries.pending_alert_count,
            delivery_dead_alert_count=deliveries.dead_letter_alert_count,
            delivery_oldest_pending_alert_at=deliveries.oldest_pending_alert_at,
            delivery_pending_cost_count=deliveries.pending_cost_count,
            delivery_dead_cost_count=deliveries.dead_letter_cost_count,
            delivery_pending_task_stage_count=(
                deliveries.pending_task_stage_count
            ),
            delivery_dead_task_stage_count=(
                deliveries.dead_letter_task_stage_count
            ),
            delivery_pending_snapshot_count=deliveries.pending_snapshot_count,
            delivery_dead_snapshot_count=deliveries.dead_letter_snapshot_count,
            cost_successful_jobs=costs.successful_relay_jobs,
            cost_explicit_jobs=costs.explicit_cost_relay_jobs,
            cost_delivered_jobs=costs.delivered_cost_relay_jobs,
            cost_incomplete_jobs=costs.incomplete_relay_jobs,
            cost_native_reconciliation_jobs=(
                costs.native_billing_reconciliation_jobs
            ),
            cost_reconciliation_complete=costs.reconciliation_complete,
            delivery_timestamp=delivery_timestamp,
            payload_sha256=payload_sha256,
            request_id=request_id,
            received_at=utcnow(),
        )
        route_rows = [
            RelayRouteOperationsSnapshot(
                id=new_id(),
                snapshot_id=event_id,
                route_id=route.route_id,
                channel_key=route.channel_key,
                channel_type=route.channel_type,
                provider_name=route.provider_name,
                model=route.model,
                mode=route.mode,
                enabled=route.enabled,
                production_ready=route.production_ready,
                health_status=route.health_status,
                failure_code=route.failure_code,
                last_probe_at=route.last_probe_at,
                rpm_limit=route.rpm_limit,
                rpm_used=route.rpm_used,
                active_task_count=route.active_task_count,
                task_capacity=route.task_capacity,
                cooling_account_count=route.cooling_account_count,
                invalid_account_count=route.invalid_account_count,
                busy_account_count=route.busy_account_count,
                rate_limited_account_count=route.rate_limited_account_count,
                successful_task_count=route.successful_task_count,
                failed_task_count=route.failed_task_count,
                latency_p50_ms=route.latency_p50_ms,
                latency_p95_ms=route.latency_p95_ms,
            )
            for route in payload.routes
        ]
        try:
            with session.begin_nested():
                session.add(snapshot)
                session.add_all(route_rows)
                session.flush()
        except IntegrityError:
            repeated = cls._existing_event(
                session,
                model=RelayOperationsSnapshot,
                event_id=event_id,
                payload_sha256=payload_sha256,
            )
            if repeated is None:
                raise
            assert isinstance(repeated, RelayOperationsSnapshot)
            return repeated, True
        return snapshot, False
