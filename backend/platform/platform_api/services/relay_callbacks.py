from __future__ import annotations

from datetime import datetime
import hashlib
import hmac
import json
import re
import time
from typing import Any, Callable, Literal, Mapping
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    GenerationTask,
    RelayCallbackEvent,
    RelayOutboxStatus,
    RelaySubmissionOutbox,
    TaskStatus,
)
from ..relay_client import (
    RelayArtifact,
    RelayErrorDetail,
    RelayReservationAction,
    expected_reservation_action,
)
from ..relay_backends import RELAY_BACKEND_ID_PATTERN
from .errors import ConflictError, DomainError, NotFoundError
from .relay_status import RelayStatusService


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class RelayCallbackJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"]
    id: UUID
    client_reference_id: str | None = Field(default=None, max_length=128)
    status: Literal[
        "processing",
        "reconciliation_required",
        "succeeded",
        "failed",
        "cancelled",
    ]
    expected_capability_revision: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$"
    )
    capability_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reservation_action: RelayReservationAction
    progress: int = Field(strict=True, ge=0, le=100)
    outputs: list[RelayArtifact] = Field(default_factory=list, max_length=16)
    error: RelayErrorDetail | None = None

    @model_validator(mode="after")
    def validate_contract(self) -> "RelayCallbackJob":
        if self.expected_capability_revision != self.capability_revision:
            raise ValueError("Relay callback capability revisions must match")
        if self.reservation_action != expected_reservation_action(self.status):
            raise ValueError("Relay callback reservation_action does not match status")
        if self.status == "succeeded":
            if self.progress != 100 or self.error is not None or not self.outputs:
                raise ValueError(
                    "A succeeded Relay callback requires progress=100, outputs, and error=null"
                )
        else:
            if self.outputs:
                raise ValueError("Only a succeeded Relay callback may contain outputs")
            if self.status == "failed" and self.error is None:
                raise ValueError("A failed Relay callback must contain an error")
        return self


class RelayCallbackPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"]
    schema_version: Literal[1]
    event_id: UUID
    type: Literal["generation.status_changed"]
    occurred_at: datetime
    job: RelayCallbackJob


class RelayCallbackVerificationError(DomainError):
    def __init__(self, message: str = "中转站回调签名无效") -> None:
        super().__init__(message, "relay_callback_unauthorized", 401)


class RelayCallbackPayloadError(DomainError):
    def __init__(self, message: str = "中转站回调内容无效") -> None:
        super().__init__(message, "relay_callback_invalid", 422)


class RelayCallbackVerifier:
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
                "Relay callback signing secret must contain at least 32 bytes"
            )
        if max_age_seconds < 30:
            raise ValueError("Relay callback replay window must be at least 30 seconds")
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
    ) -> tuple[RelayCallbackPayload, str]:
        if not event_id or not timestamp or not signature:
            raise RelayCallbackVerificationError()
        try:
            header_event_id = UUID(event_id)
            timestamp_value = int(timestamp)
        except (TypeError, ValueError):
            raise RelayCallbackVerificationError() from None
        if (
            str(timestamp_value) != timestamp
            or abs(self._clock() - timestamp_value) > self._max_age_seconds
        ):
            raise RelayCallbackVerificationError("中转站回调已过期或时间戳无效")
        if not signature.startswith("v1=") or len(signature) != 67:
            raise RelayCallbackVerificationError()
        supplied_digest = signature[3:]
        if any(character not in "0123456789abcdef" for character in supplied_digest):
            raise RelayCallbackVerificationError()
        signing_input = (
            timestamp.encode("ascii")
            + b"."
            + str(header_event_id).encode("ascii")
            + b"."
            + raw_body
        )
        expected_digest = hmac.new(
            self._secret, signing_input, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(supplied_digest, expected_digest):
            raise RelayCallbackVerificationError()
        try:
            decoded = json.loads(
                raw_body, object_pairs_hook=_reject_duplicate_json_keys
            )
            payload = RelayCallbackPayload.model_validate(decoded)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
            ValueError,
        ):
            raise RelayCallbackPayloadError() from None
        if payload.event_id != header_event_id:
            raise RelayCallbackPayloadError("回调请求头事件编号与消息体不一致")
        if (
            payload.occurred_at.tzinfo is None
            or payload.occurred_at.utcoffset() is None
        ):
            raise RelayCallbackPayloadError("回调发生时间必须包含 UTC 偏移")
        return payload, hashlib.sha256(raw_body).hexdigest()


class RelayCallbackVerifierRegistry:
    """Select a callback HMAC verifier by server-owned Relay backend id."""

    def __init__(self, verifiers: Mapping[str, RelayCallbackVerifier]) -> None:
        registered: dict[str, RelayCallbackVerifier] = {}
        for backend_id, verifier in verifiers.items():
            if re.fullmatch(RELAY_BACKEND_ID_PATTERN, backend_id) is None:
                raise ValueError("Relay callback backend id is invalid")
            registered[backend_id] = verifier
        self._registered = registered

    def resolve(self, backend_id: str) -> RelayCallbackVerifier:
        if re.fullmatch(RELAY_BACKEND_ID_PATTERN, backend_id) is None:
            raise RelayCallbackVerificationError()
        verifier = self._registered.get(backend_id)
        if verifier is None:
            raise RelayCallbackVerificationError()
        return verifier


class RelayCallbackService:
    _BINDABLE_OUTBOX_STATUSES = frozenset(
        {
            RelayOutboxStatus.PENDING,
            RelayOutboxStatus.PROCESSING,
            RelayOutboxStatus.RETRY,
            RelayOutboxStatus.RECONCILIATION_REQUIRED,
        }
    )

    @staticmethod
    def page(
        session: Session,
        *,
        page: int,
        page_size: int,
        company_id: str | None = None,
        task_id: str | None = None,
        relay_status: str | None = None,
    ) -> tuple[int, list[RelayCallbackEvent]]:
        filters = []
        if company_id:
            filters.append(RelayCallbackEvent.company_id == company_id)
        if task_id:
            filters.append(RelayCallbackEvent.task_id == task_id)
        if relay_status:
            filters.append(RelayCallbackEvent.relay_status == relay_status)
        total = session.scalar(
            select(func.count()).select_from(RelayCallbackEvent).where(*filters)
        )
        items = list(
            session.scalars(
                select(RelayCallbackEvent)
                .where(*filters)
                .order_by(RelayCallbackEvent.received_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return int(total or 0), items

    @staticmethod
    def _existing(
        session: Session, *, event_id: str, payload_sha256: str
    ) -> RelayCallbackEvent | None:
        existing = session.get(RelayCallbackEvent, event_id)
        if existing is None:
            return None
        if not hmac.compare_digest(existing.payload_sha256, payload_sha256):
            raise ConflictError("相同回调事件编号对应了不同消息内容")
        return existing

    @classmethod
    def _bind_relay_job_from_trusted_callback(
        cls,
        *,
        task: GenerationTask,
        outbox: RelaySubmissionOutbox | None,
        relay_job_id: str,
        source_backend_id: str,
    ) -> None:
        if (
            outbox is None
            or outbox.task_id != task.id
            or outbox.company_id != task.company_id
            or outbox.personal_workspace_id != task.personal_workspace_id
            or source_backend_id != task.relay_backend_id
            or outbox.relay_backend_id != task.relay_backend_id
            or outbox.relay_contract_revision != task.relay_contract_revision
        ):
            raise NotFoundError("No matching task-bound Relay affinity exists")
        if task.relay_job_id is not None:
            if task.relay_job_id != relay_job_id:
                raise NotFoundError("不存在匹配的客户平台与中转站任务")
            return

        expected_idempotency_key = f"platform-task-{task.id}"
        if (
            outbox.idempotency_key != expected_idempotency_key
            or outbox.status not in cls._BINDABLE_OUTBOX_STATUSES
            or outbox.relay_job_id not in {None, relay_job_id}
        ):
            raise NotFoundError("不存在匹配的客户平台与中转站任务")

        submitted_payload = outbox.materialized_relay_payload or outbox.relay_payload
        metadata = (
            submitted_payload.get("metadata")
            if isinstance(submitted_payload, dict)
            else None
        )
        scope_metadata_matches = isinstance(metadata, dict) and (
            metadata.get("platform_billing_scope")
            == ("company" if task.company_id is not None else "personal")
            and metadata.get("platform_billing_scope_id")
            == (task.company_id or task.personal_workspace_id)
            and (
                metadata.get("platform_company_id") == task.company_id
                if task.company_id is not None
                else metadata.get("platform_personal_workspace_id")
                == task.personal_workspace_id
            )
        )
        if (
            not isinstance(metadata, dict)
            or submitted_payload.get("client_reference_id") != task.id
            or not scope_metadata_matches
            or metadata.get("platform_task_id") != task.id
        ):
            raise NotFoundError("不存在匹配的客户平台与中转站任务")

        task.relay_job_id = relay_job_id
        outbox.relay_job_id = relay_job_id
        outbox.status = RelayOutboxStatus.SENT
        outbox.last_error = None

    @classmethod
    def _lock_task_and_outbox(
        cls,
        session: Session,
        *,
        task_id: str,
        target_status: TaskStatus,
    ) -> tuple[GenerationTask, RelaySubmissionOutbox | None]:
        # Resolve the immutable tenant key without taking a row lock. The
        # authoritative rows are then locked in wallet -> task -> outbox order.
        scope = session.execute(
            select(
                GenerationTask.company_id,
                GenerationTask.personal_workspace_id,
            ).where(GenerationTask.id == task_id)
        ).one_or_none()
        if scope is None or ((scope.company_id is None) == (scope.personal_workspace_id is None)):
            raise NotFoundError("不存在匹配的客户平台与中转站任务")
        if RelayStatusService.is_terminal_status(target_status):
            task = RelayStatusService.lock_wallet_and_task_for_scope(
                session,
                company_id=scope.company_id,
                personal_workspace_id=scope.personal_workspace_id,
                task_id=task_id,
            )
        else:
            task = RelayStatusService.lock_task_for_scope(
                session,
                company_id=scope.company_id,
                personal_workspace_id=scope.personal_workspace_id,
                task_id=task_id,
            )
        outbox = session.scalar(
            select(RelaySubmissionOutbox)
            .where(RelaySubmissionOutbox.task_id == task_id)
            .with_for_update()
        )
        return task, outbox

    @classmethod
    def apply(
        cls,
        session: Session,
        *,
        payload: RelayCallbackPayload,
        payload_sha256: str,
        request_id: str,
        source_backend_id: str,
    ) -> tuple[GenerationTask, bool]:
        event_id = str(payload.event_id)
        existing = cls._existing(
            session, event_id=event_id, payload_sha256=payload_sha256
        )
        if existing is not None:
            task = session.get(GenerationTask, existing.task_id)
            if task is None:
                raise NotFoundError("回调关联的任务不存在")
            outbox = session.scalar(
                select(RelaySubmissionOutbox).where(
                    RelaySubmissionOutbox.task_id == task.id
                )
            )
            cls._bind_relay_job_from_trusted_callback(
                task=task,
                outbox=outbox,
                relay_job_id=existing.relay_job_id,
                source_backend_id=source_backend_id,
            )
            return task, True

        task_reference = payload.job.client_reference_id
        if not task_reference:
            raise RelayCallbackPayloadError("回调缺少客户平台任务编号")
        target_status = RelayStatusService.target_status(payload.job.status)
        task, outbox = cls._lock_task_and_outbox(
            session,
            task_id=task_reference,
            target_status=target_status,
        )
        relay_job_id = str(payload.job.id)
        cls._bind_relay_job_from_trusted_callback(
            task=task,
            outbox=outbox,
            relay_job_id=relay_job_id,
            source_backend_id=source_backend_id,
        )
        expected_revision = (task.capability_snapshot or {}).get(
            "relay_capability_revision"
        )
        if expected_revision != payload.job.expected_capability_revision:
            raise NotFoundError(
                "No matching customer-platform and Relay capability pin exists"
            )

        event = RelayCallbackEvent(
            id=event_id,
            company_id=task.company_id,
            personal_workspace_id=task.personal_workspace_id,
            task_id=task.id,
            relay_job_id=relay_job_id,
            relay_status=payload.job.status,
            occurred_at=payload.occurred_at,
            payload_sha256=payload_sha256,
            request_id=request_id,
        )
        try:
            with session.begin_nested():
                session.add(event)
                session.flush()
        except IntegrityError:
            existing = cls._existing(
                session, event_id=event_id, payload_sha256=payload_sha256
            )
            if existing is None:
                raise
            repeated_task = session.get(GenerationTask, existing.task_id)
            if repeated_task is None:
                raise NotFoundError("回调关联的任务不存在")
            return repeated_task, True

        failure_reason = payload.job.error.message if payload.job.error else ""
        updated_task = RelayStatusService.apply_to_locked_task(
            session,
            task=task,
            company_id=task.company_id,
            task_id=task.id,
            relay_job_id=relay_job_id,
            target_status=target_status,
            outputs=payload.job.outputs,
            failure_reason=failure_reason,
            error_snapshot=(
                {
                    **payload.job.error.model_dump(mode="json"),
                    "source": "callback",
                }
                if payload.job.error is not None
                else None
            ),
            reservation_action=payload.job.reservation_action,
            personal_workspace_id=task.personal_workspace_id,
        )
        return updated_task, False
