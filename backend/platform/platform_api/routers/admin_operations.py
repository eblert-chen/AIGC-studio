from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..dependencies import PlatformAdminContext, get_db, require_platform_admin
from ..models import AuditLog
from ..request_ids import stable_request_id
from ..schemas import ReconcilePublicationJobRequest
from ..relay_client import (
    RelayClientError,
    RelayCallbackDelivery,
    RelayCallbackRedriveResult,
    RelayChannel,
    RelayChannelOperation,
    RelayChannelStatus,
    RelayOperationsClient,
    RelayPermanentError,
    RelayTemporaryError,
    RelayUnknownSubmission,
    RelayUnknownSubmissionResult,
)
from ..services.audit import AuditService
from ..services.admin_analytics import AdminAnalyticsService
from ..services.admin_entitlements import AdminEntitlementService
from ..services.publishing import PublishingService
from ..services.relay_channel_operations import (
    RelayChannelOperationConflict,
    RelayChannelOperationJournalService,
)

router = APIRouter(prefix="/api/v1/platform-admin", tags=["platform-admin-operations"])


def _window(
    *, start_time: datetime | None, end_time: datetime | None, default_days: int
) -> tuple[datetime, datetime]:
    end = end_time or datetime.now(timezone.utc)
    start = start_time or (end - timedelta(days=default_days))
    return start, end


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return str(value) if value else "platform-admin-entitlement-batch"


class EntitlementCellMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str = Field(min_length=1, max_length=36)
    item_kind: Literal["model", "resource"]
    item_id: str = Field(min_length=1, max_length=36)
    enabled: bool
    price_per_second_cents: int | None = Field(default=None, gt=0)
    price_per_item_cents: int | None = Field(default=None, gt=0)
    config_override: dict[str, Any] | None = None
    call_quota: int | None = Field(default=None, gt=0)
    concurrency_limit: int | None = Field(default=None, gt=0)
    effective_at: datetime | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "EntitlementCellMutation":
        if self.item_kind == "resource" and (
            self.price_per_second_cents is not None
            or self.price_per_item_cents is not None
        ):
            raise ValueError("resource grants do not have prices")
        if (
            self.price_per_second_cents is not None
            and self.price_per_item_cents is not None
        ):
            raise ValueError("only one model price may be configured")
        if self.effective_at and (
            self.effective_at.tzinfo is None or self.effective_at.utcoffset() is None
        ):
            raise ValueError("effective_at must include a UTC offset")
        if self.expires_at and (
            self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None
        ):
            raise ValueError("expires_at must include a UTC offset")
        if (
            self.effective_at
            and self.expires_at
            and self.effective_at >= self.expires_at
        ):
            raise ValueError("effective_at must be before expires_at")
        return self


class TemplateCellMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_kind: Literal["model", "resource"]
    item_id: str = Field(min_length=1, max_length=36)
    enabled: bool
    price_per_second_cents: int | None = Field(default=None, gt=0)
    price_per_item_cents: int | None = Field(default=None, gt=0)
    config_override: dict[str, Any] | None = None
    call_quota: int | None = Field(default=None, gt=0)
    concurrency_limit: int | None = Field(default=None, gt=0)
    effective_at: datetime | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "TemplateCellMutation":
        if self.item_kind == "resource" and (
            self.price_per_second_cents is not None
            or self.price_per_item_cents is not None
        ):
            raise ValueError("resource grants do not have prices")
        if (
            self.price_per_second_cents is not None
            and self.price_per_item_cents is not None
        ):
            raise ValueError("only one model price may be configured")
        if self.effective_at and (
            self.effective_at.tzinfo is None or self.effective_at.utcoffset() is None
        ):
            raise ValueError("effective_at must include a UTC offset")
        if self.expires_at and (
            self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None
        ):
            raise ValueError("expires_at must include a UTC offset")
        if (
            self.effective_at
            and self.expires_at
            and self.effective_at >= self.expires_at
        ):
            raise ValueError("effective_at must be before expires_at")
        return self


class AdminReconcilePublicationJobRequest(ReconcilePublicationJobRequest):
    reason: str = Field(min_length=3, max_length=240)


class AdminResolveRelayUnknownSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$",
    )
    outcome: Literal["created", "not_created"]
    upstream_task_id: str = Field(default="", max_length=191)
    expected_route_id: int = Field(gt=0)
    expected_submission_attempt: int = Field(gt=0)
    expected_reconciliation_token: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    verification_reference: str = Field(min_length=1, max_length=191)
    reason: str = Field(min_length=3, max_length=240)

    @model_validator(mode="after")
    def validate_outcome(self) -> "AdminResolveRelayUnknownSubmissionRequest":
        if self.outcome == "created" and not self.upstream_task_id.strip():
            raise ValueError("created outcome requires upstream_task_id")
        if self.outcome == "not_created" and self.upstream_task_id:
            raise ValueError("not_created outcome forbids upstream_task_id")
        if self.upstream_task_id != self.upstream_task_id.strip():
            raise ValueError("upstream_task_id must not contain edge whitespace")
        if self.verification_reference != self.verification_reference.strip():
            raise ValueError("verification_reference must not contain edge whitespace")
        if self.reason != self.reason.strip():
            raise ValueError("reason must not contain edge whitespace")
        return self


class AdminRedriveRelayCallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$",
    )
    actor: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=3, max_length=240)
    approved: Literal[True]

    @field_validator("actor", "reason")
    @classmethod
    def no_edge_whitespace(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("callback redrive evidence must not contain edge whitespace")
        return value


class AdminTestRelayChannelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$"
    )
    reason: str = Field(min_length=3, max_length=240)
    approved: Literal[True]

    @field_validator("reason")
    @classmethod
    def no_edge_whitespace(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("channel test evidence must not contain edge whitespace")
        return value


class AdminSetRelayChannelStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$"
    )
    reason: str = Field(min_length=3, max_length=240)
    approved: Literal[True]
    expected_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    target_status: Literal["enabled", "manually_disabled"]

    @field_validator("reason")
    @classmethod
    def no_edge_whitespace(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("channel status evidence must not contain edge whitespace")
        return value


def _relay_operations_client(request: Request) -> RelayOperationsClient:
    client = getattr(request.app.state, "relay_operations_client", None)
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="Relay operations client is not configured",
        )
    return client


def _relay_channel_tenant_id(request: Request) -> str:
    value = getattr(request.app.state.settings, "relay_tenant_id", None)
    try:
        canonical = str(UUID(value or ""))
    except ValueError as error:
        raise HTTPException(
            status_code=503,
            detail="Relay channel-control tenant is not configured",
        ) from error
    if canonical != value:
        raise HTTPException(
            status_code=503,
            detail="Relay channel-control tenant is not configured",
        )
    return canonical


def _journal_receipt(row) -> RelayChannelOperation | None:
    if row.state != "completed" or row.relay_receipt is None:
        return None
    try:
        return RelayChannelOperation.model_validate(row.relay_receipt)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="Platform Relay channel operation journal is invalid",
        ) from error


def _raise_relay_channel_journal_conflict(
    error: RelayChannelOperationConflict,
) -> None:
    raise HTTPException(
        status_code=409,
        detail="Platform operation_id is already bound to different Relay channel evidence",
    ) from error


def _raise_relay_channel_not_started(error: RelayClientError) -> None:
    if isinstance(error, RelayPermanentError) and error.response_status == 404:
        raise HTTPException(
            status_code=404,
            detail="Relay channel does not exist",
        ) from error
    status_code = 503 if isinstance(error, RelayTemporaryError) else 502
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": "RELAY_CHANNEL_OPERATION_NOT_STARTED",
            "message": "Relay channel preflight failed; no operation was approved or submitted",
        },
    ) from error


def _raise_relay_channel_outcome_unknown(
    error: Exception | None = None,
    *,
    message: str = (
        "Relay channel operation was approved but its durable receipt is not readable"
    ),
) -> None:
    exception = HTTPException(
        status_code=503,
        detail={
            "code": "RELAY_CHANNEL_OPERATION_OUTCOME_UNKNOWN",
            "message": message,
        },
    )
    if error is None:
        raise exception
    raise exception from error


def _raise_relay_operations_error(error: RelayClientError) -> None:
    if isinstance(error, RelayTemporaryError):
        detail = (
            "Relay reconciliation result is unknown; refresh the detail before "
            "taking another action"
            if error.submission_outcome_unknown
            else "Relay operations service is temporarily unavailable"
        )
        raise HTTPException(status_code=503, detail=detail) from error
    if isinstance(error, RelayPermanentError):
        if error.response_status == 404:
            raise HTTPException(
                status_code=404, detail="Relay unknown submission does not exist"
            ) from error
        if error.response_status == 409:
            raise HTTPException(
                status_code=409,
                detail="Relay reconciliation fencing proof is stale or conflicts",
            ) from error
    raise HTTPException(
        status_code=502, detail="Relay operations request failed"
    ) from error


def _raise_relay_channel_operations_error(error: RelayClientError) -> None:
    if isinstance(error, RelayTemporaryError):
        detail = (
            "Relay channel operation outcome is unknown; query its operation receipt "
            "before taking another action"
            if error.submission_outcome_unknown
            else "Relay channel operations service is temporarily unavailable"
        )
        raise HTTPException(status_code=503, detail=detail) from error
    if isinstance(error, RelayPermanentError):
        if error.response_status == 404:
            raise HTTPException(
                status_code=404,
                detail="Relay channel or channel operation does not exist",
            ) from error
        if error.response_status == 409:
            raise HTTPException(
                status_code=409,
                detail="Relay channel operation conflicts with current state or evidence",
            ) from error
    raise HTTPException(
        status_code=502, detail="Relay channel operations request failed"
    ) from error


def _relay_channel_summary(item: RelayChannel) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "type": item.type,
        "type_label": item.type_label,
        "test_supported": item.test_supported,
        "status": item.status,
        "configured_models": list(item.configured_models),
        "test_model": item.test_model,
        "weight": item.weight,
        "priority": item.priority,
        "auto_ban": item.auto_ban,
        "tag": item.tag,
        "created_at": item.created_at.isoformat(),
        "last_tested_at": (
            item.last_tested_at.isoformat() if item.last_tested_at else None
        ),
        "response_time_ms": item.response_time_ms,
        "credential": {
            "configured": item.credential.configured,
            "key_count": item.credential.key_count,
        },
        "revision": item.revision,
    }


def _relay_channel_operation_summary(
    item: RelayChannelOperation,
) -> dict[str, Any]:
    return {
        "operation_id": item.operation_id,
        "tenant_id": str(item.tenant_id),
        "channel_id": item.channel_id,
        "kind": item.kind,
        "state": item.state,
        "actor": item.actor,
        "reason": item.reason,
        "relay_request_id": item.request_id,
        "intent_sha256": item.intent_sha256,
        "previous_revision": item.previous_revision,
        "result_revision": item.result_revision,
        "expected_revision": item.expected_revision,
        "target_status": item.target_status,
        "result": item.result.model_dump(mode="json") if item.result else None,
        "created_at": item.created_at.isoformat(),
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        "idempotent_replay": item.idempotent_replay,
    }


def _relay_channel_test_proof(
    *,
    channel_id: int,
    body: AdminTestRelayChannelRequest,
    actor: str,
    request_id: str,
) -> dict[str, Any]:
    return {
        "operation_id": body.operation_id,
        "channel_id": channel_id,
        "kind": "test",
        "actor": actor,
        "reason": body.reason,
        "approved": True,
        "request_id": request_id,
    }


def _relay_channel_status_proof(
    *,
    channel_id: int,
    body: AdminSetRelayChannelStatusRequest,
    actor: str,
    request_id: str,
) -> dict[str, Any]:
    return {
        "operation_id": body.operation_id,
        "channel_id": channel_id,
        "kind": "status",
        "actor": actor,
        "reason": body.reason,
        "expected_revision": body.expected_revision,
        "target_status": body.target_status,
        "approved": True,
        "request_id": request_id,
    }


def _relay_channel_receipt_matches_proof(
    receipt: RelayChannelOperation,
    *,
    proof: dict[str, Any],
) -> bool:
    if (
        receipt.channel_id != proof["channel_id"]
        or receipt.operation_id != proof["operation_id"]
        or receipt.kind != proof["kind"]
        or receipt.actor != proof["actor"]
        or receipt.reason != proof["reason"]
    ):
        return False
    if receipt.kind == "status" and (
        receipt.expected_revision != proof["expected_revision"]
        or receipt.target_status != proof["target_status"]
    ):
        return False
    if (
        receipt.kind == "status"
        and receipt.state != "failed"
        and receipt.previous_revision is not None
    ):
        return receipt.previous_revision == proof["expected_revision"]
    return True


def _relay_unknown_summary(item: RelayUnknownSubmission) -> dict[str, Any]:
    return {
        "job_id": str(item.job_id),
        "tenant_id": str(item.tenant_id),
        "status": item.status,
        "model": item.model,
        "mode": item.mode,
        "provider_route_id": item.provider_route_id,
        "provider_channel_id": item.provider_channel_id,
        "provider_submission_attempt": item.provider_submission_attempt,
        "reconciliation_token": item.reconciliation_token,
        "unknown_at": item.unknown_at.isoformat(),
    }


def _relay_result_summary(item: RelayUnknownSubmissionResult) -> dict[str, Any]:
    return {
        "job_id": str(item.job_id),
        "tenant_id": str(item.tenant_id),
        "operation_id": item.operation_id,
        "event_id": str(item.event_id),
        "status": item.current_status,
        "resolved_status": item.resolved_status,
        "expected_route_id": item.expected_route_id,
        "expected_submission_attempt": item.expected_submission_attempt,
        "expected_reconciliation_token": item.expected_reconciliation_token,
        "approval_key_id": item.approval_key_id,
        "approval_signature": item.approval_signature,
        "resolved_at": item.resolved_at.isoformat(),
    }


def _callback_delivery_summary(item: RelayCallbackDelivery) -> dict[str, Any]:
    return {
        "event_id": str(item.event_id),
        "tenant_id": str(item.tenant_id),
        "job_id": str(item.job_id),
        "state": item.state,
        "attempts": item.attempts,
        "max_attempts": item.max_attempts,
        "response_status": item.response_status,
        "last_error": item.last_error,
        "payload_sha256": item.payload_sha256,
        "callback_url_sha256": item.callback_url_sha256,
        "dead_lettered_at": (
            item.dead_lettered_at.isoformat() if item.dead_lettered_at else None
        ),
    }


def _callback_redrive_summary(item: RelayCallbackRedriveResult) -> dict[str, Any]:
    return {
        "event_id": str(item.delivery_event_id),
        "tenant_id": str(item.tenant_id),
        "operation_id": item.evidence.operation_id,
        "relay_request_id": item.evidence.request_id,
        "actor": item.evidence.actor,
        "reason": item.evidence.reason,
        "previous_state": item.evidence.previous_state,
        "current_state": item.current_state,
        "receipt_sha256": item.evidence.receipt_sha256,
        "payload_sha256": item.evidence.payload_sha256,
        "callback_url_sha256": item.evidence.callback_url_sha256,
        "redriven_at": item.evidence.redriven_at.isoformat(),
    }


def _find_callback_redrive_audit(
    session: Session,
    *,
    action: str,
    event_id: str,
    operation_id: str,
) -> AuditLog | None:
    entries = session.scalars(
        select(AuditLog)
        .where(
            AuditLog.action == action,
            AuditLog.target_type == "relay_callback_delivery",
            AuditLog.target_id == event_id,
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(100)
    ).all()
    return next(
        (
            entry
            for entry in entries
            if entry.after_summary.get("operation_id") == operation_id
        ),
        None,
    )


def _latest_callback_redrive_operation_id(
    session: Session, *, event_id: str
) -> str | None:
    entries = session.scalars(
        select(AuditLog)
        .where(
            AuditLog.action == "relay.callback_dead_letter.approve_redrive",
            AuditLog.target_type == "relay_callback_delivery",
            AuditLog.target_id == event_id,
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(100)
    ).all()
    for entry in entries:
        operation_id = entry.after_summary.get("operation_id")
        if isinstance(operation_id, str):
            return operation_id
    return None


def _relay_reconciliation_proof(
    *,
    body: AdminResolveRelayUnknownSubmissionRequest,
    operation_id: str,
    approved_by: str,
    request_id: str,
) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "decision": body.outcome,
        "upstream_task_id": body.upstream_task_id or None,
        "verification_reference": body.verification_reference,
        "approved_by": approved_by,
        "reason": body.reason,
        "expected_route_id": body.expected_route_id,
        "expected_submission_attempt": body.expected_submission_attempt,
        "expected_reconciliation_token": body.expected_reconciliation_token,
        "request_id": request_id,
    }


def _relay_result_matches_proof(
    result: RelayUnknownSubmissionResult,
    *,
    job_id: str,
    proof: dict[str, Any],
) -> bool:
    return (
        str(result.job_id) == job_id
        and result.operation_id == proof["operation_id"]
        and result.outcome == proof["decision"]
        and (result.upstream_task_id or None) == proof["upstream_task_id"]
        and result.expected_route_id == proof["expected_route_id"]
        and result.expected_submission_attempt == proof["expected_submission_attempt"]
        and result.expected_reconciliation_token
        == proof["expected_reconciliation_token"]
        and result.verification_reference == proof["verification_reference"]
        and result.approved_by == proof["approved_by"]
        and result.approval_reason == proof["reason"]
    )


def _find_relay_reconciliation_audit(
    session: Session,
    *,
    action: str,
    job_id: str,
    operation_id: str,
) -> AuditLog | None:
    entries = session.scalars(
        select(AuditLog)
        .where(
            AuditLog.action == action,
            AuditLog.target_type == "relay_generation_job",
            AuditLog.target_id == job_id,
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(100)
    ).all()
    return next(
        (
            entry
            for entry in entries
            if entry.after_summary.get("operation_id") == operation_id
        ),
        None,
    )


def _latest_relay_reconciliation_operation_id(
    session: Session,
    *,
    job_id: str,
) -> str | None:
    entries = session.scalars(
        select(AuditLog)
        .where(
            AuditLog.action == "relay.submission_unknown.approve",
            AuditLog.target_type == "relay_generation_job",
            AuditLog.target_id == job_id,
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(100)
    ).all()
    for entry in entries:
        operation_id = entry.after_summary.get("operation_id")
        if isinstance(operation_id, str):
            return operation_id
    return None


def _audit_matches_reconciliation_proof(
    entry: AuditLog,
    *,
    actor_user_id: str,
    proof: dict[str, Any],
) -> bool:
    expected = {
        key: proof[key]
        for key in (
            "operation_id",
            "decision",
            "upstream_task_id",
            "verification_reference",
            "approved_by",
            "reason",
            "expected_route_id",
            "expected_submission_attempt",
            "expected_reconciliation_token",
        )
    }
    return entry.actor_user_id == actor_user_id and all(
        entry.after_summary.get(key) == value for key, value in expected.items()
    )


def _publication_job_summary(job) -> dict[str, Any]:
    return {
        "company_id": job.company_id,
        "task_artifact_id": job.task_artifact_id,
        "connection_id": job.connection_id,
        "status": job.status.value,
        "scheduled_at": (
            job.scheduled_at.isoformat() if job.scheduled_at is not None else None
        ),
        "timezone": job.timezone,
        "attempt_count": job.attempt_count,
        "external_post_id": job.external_post_id,
        "error_code": job.error_code,
    }


class BatchPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: list[EntitlementCellMutation] = Field(min_length=1, max_length=500)


class BatchExecuteRequest(BatchPreviewRequest):
    expected_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=3, max_length=240)
    idempotency_key: str = Field(min_length=1, max_length=120)


class CopyPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_company_id: str = Field(min_length=1, max_length=36)
    target_company_ids: list[str] = Field(min_length=1, max_length=100)
    mode: Literal["merge", "replace"] = "replace"
    include_models: bool = True
    include_resources: bool = True

    @field_validator("target_company_ids")
    @classmethod
    def unique_targets(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("target_company_ids must be unique")
        return value

    @model_validator(mode="after")
    def at_least_one_domain(self) -> "CopyPreviewRequest":
        if not self.include_models and not self.include_resources:
            raise ValueError("at least one entitlement domain must be copied")
        return self


class CopyExecuteRequest(CopyPreviewRequest):
    expected_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=3, max_length=240)
    idempotency_key: str = Field(min_length=1, max_length=120)


class TemplatePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_name: str = Field(min_length=1, max_length=120)
    template_version: int = Field(ge=1)
    target_company_ids: list[str] = Field(min_length=1, max_length=100)
    mode: Literal["merge", "replace"] = "replace"
    cells: list[TemplateCellMutation] = Field(min_length=1, max_length=500)


class TemplateExecuteRequest(TemplatePreviewRequest):
    expected_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=3, max_length=240)
    idempotency_key: str = Field(min_length=1, max_length=120)


def _dump_cells(cells: list[BaseModel]) -> list[dict[str, Any]]:
    # exclude_unset distinguishes "leave schedule unchanged" from explicit null.
    return [cell.model_dump(exclude_unset=True) for cell in cells]


@router.get("/analytics/operating-series")
def operating_series(
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    granularity: Literal["day", "week", "month"] = "day",
) -> dict[str, Any]:
    _no_store(response)
    start, end = _window(start_time=start_time, end_time=end_time, default_days=30)
    return AdminAnalyticsService.operating_series(
        session, start=start, end=end, granularity=granularity
    )


@router.get("/analytics/task-operations")
def task_operations(
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> dict[str, Any]:
    _no_store(response)
    start, end = _window(start_time=start_time, end_time=end_time, default_days=7)
    return AdminAnalyticsService.task_operations(session, start=start, end=end)


@router.get("/relay/channels")
def list_relay_channels(
    request: Request,
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=50, ge=1, le=100),
    status: RelayChannelStatus | None = Query(default=None),
) -> dict[str, Any]:
    _no_store(response)
    client = _relay_operations_client(request)
    try:
        result = client.list_channels(
            page=page,
            page_size=page_size,
            status=status,
            request_id=_request_id(request),
        )
    except RelayClientError as error:
        _raise_relay_channel_operations_error(error)
    return result.model_dump(mode="json")


@router.get("/relay/channels/{channel_id}")
def get_relay_channel(
    channel_id: Annotated[int, Path(gt=0)],
    request: Request,
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
) -> dict[str, Any]:
    _no_store(response)
    client = _relay_operations_client(request)
    try:
        result = client.get_channel(channel_id, request_id=_request_id(request))
    except (RelayClientError, ValueError) as error:
        if isinstance(error, RelayClientError):
            _raise_relay_channel_operations_error(error)
        raise HTTPException(
            status_code=422, detail="channel_id must be a positive integer"
        ) from error
    return result.model_dump(mode="json")


@router.get("/relay/channels/{channel_id}/operations/{operation_id}")
def get_relay_channel_operation(
    channel_id: Annotated[int, Path(gt=0)],
    operation_id: Annotated[
        str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
    ],
    request: Request,
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    _no_store(response)
    tenant_id = _relay_channel_tenant_id(request)
    journal = RelayChannelOperationJournalService.find(
        session, tenant_id=tenant_id, operation_id=operation_id
    )
    if journal is not None:
        if journal.channel_id != channel_id:
            raise HTTPException(
                status_code=404,
                detail="Relay channel operation does not exist for this channel",
            )
        stored = _journal_receipt(journal)
        if stored is not None:
            return stored.model_dump(mode="json")
    client = _relay_operations_client(request)
    try:
        result = client.get_channel_operation(
            channel_id,
            operation_id=operation_id,
            request_id=_request_id(request),
        )
    except (RelayClientError, ValueError) as error:
        if isinstance(error, RelayClientError):
            if journal is not None:
                _raise_relay_channel_outcome_unknown(error)
            _raise_relay_channel_operations_error(error)
        raise HTTPException(
            status_code=422, detail="Relay channel operation identity is invalid"
        ) from error
    if journal is not None:
        proof: dict[str, Any] = {
            "operation_id": journal.operation_id,
            "channel_id": journal.channel_id,
            "kind": journal.kind,
            "actor": journal.actor_user_id,
            "reason": journal.reason,
        }
        if journal.kind == "status":
            proof.update(
                {
                    "expected_revision": journal.expected_revision,
                    "target_status": journal.target_status,
                }
            )
        if not _relay_channel_receipt_matches_proof(result, proof=proof):
            _raise_relay_channel_outcome_unknown(
                message=(
                    "Relay channel operation readback conflicts with approved "
                    "evidence; do not submit another operation"
                )
            )
        try:
            persisted = RelayChannelOperationJournalService.record_receipt(
                session,
                journal_id=journal.id,
                receipt=result.model_dump(mode="json"),
                relay_intent_sha256=result.intent_sha256,
                result_summary=_relay_channel_operation_summary(result),
                request_id=_request_id(request),
            )
        except RelayChannelOperationConflict as error:
            _raise_relay_channel_outcome_unknown(
                error,
                message=(
                    "Relay channel operation receipt conflicts with the durable "
                    "Platform journal; do not submit another operation"
                ),
            )
        session.commit()
        stored = _journal_receipt(persisted)
        if stored is not None:
            result = stored
    return result.model_dump(mode="json")


@router.post("/relay/channels/{channel_id}/test")
def test_relay_channel(
    channel_id: Annotated[int, Path(gt=0)],
    body: AdminTestRelayChannelRequest,
    request: Request,
    response: Response,
    admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    _no_store(response)
    client = _relay_operations_client(request)
    tenant_id = _relay_channel_tenant_id(request)
    platform_request_id = _request_id(request)
    proof = _relay_channel_test_proof(
        channel_id=channel_id,
        body=body,
        actor=admin.user_id,
        request_id=platform_request_id,
    )
    readback_request_id = stable_request_id(
        "relay-channel-operation-read", f"{channel_id}:{body.operation_id}"
    )
    journal = RelayChannelOperationJournalService.find(
        session, tenant_id=tenant_id, operation_id=body.operation_id
    )
    created = False
    if journal is not None:
        try:
            RelayChannelOperationJournalService.assert_matches(
                journal,
                tenant_id=tenant_id,
                operation_id=body.operation_id,
                channel_id=channel_id,
                kind="test",
                actor_user_id=admin.user_id,
                reason=body.reason,
                expected_revision=None,
                target_status=None,
            )
        except RelayChannelOperationConflict as error:
            _raise_relay_channel_journal_conflict(error)
    else:
        try:
            current = client.get_channel(channel_id, request_id=platform_request_id)
        except RelayClientError as error:
            _raise_relay_channel_not_started(error)
        if not current.test_supported:
            raise HTTPException(
                status_code=409,
                detail="Relay channel does not support a generic connectivity test",
            )
        try:
            journal, created = RelayChannelOperationJournalService.claim(
                session,
                tenant_id=tenant_id,
                operation_id=body.operation_id,
                channel_id=channel_id,
                kind="test",
                actor_user_id=admin.user_id,
                reason=body.reason,
                expected_revision=None,
                target_status=None,
                before_summary=_relay_channel_summary(current),
                approval_proof=proof,
                request_id=platform_request_id,
            )
        except RelayChannelOperationConflict as error:
            _raise_relay_channel_journal_conflict(error)
        session.commit()

    stored = _journal_receipt(journal)
    if stored is not None:
        if not _relay_channel_receipt_matches_proof(stored, proof=proof):
            _raise_relay_channel_outcome_unknown(
                message=(
                    "Stored Relay channel test receipt conflicts with approved "
                    "evidence; do not submit another operation"
                )
            )
        return stored.model_dump(mode="json")

    submitted: RelayChannelOperation | None = None
    if created:
        try:
            submitted = client.test_channel(
                channel_id,
                operation_id=body.operation_id,
                actor=admin.user_id,
                reason=body.reason,
                request_id=platform_request_id,
            )
        except RelayClientError as error:
            _raise_relay_channel_outcome_unknown(error)
        if not _relay_channel_receipt_matches_proof(submitted, proof=proof):
            _raise_relay_channel_outcome_unknown(
                message=(
                    "Relay channel test receipt conflicts with approved evidence; "
                    "do not submit another operation"
                )
            )

    try:
        result = client.get_channel_operation(
            channel_id,
            operation_id=body.operation_id,
            request_id=readback_request_id,
        )
    except RelayPermanentError as error:
        if error.response_status == 404:
            _raise_relay_channel_outcome_unknown(
                error,
                message=(
                    "Relay channel test was approved but has no readable receipt; "
                    "do not submit another operation"
                ),
            )
        _raise_relay_channel_outcome_unknown(error)
    except RelayClientError as error:
        _raise_relay_channel_outcome_unknown(error)
    if (
        not _relay_channel_receipt_matches_proof(result, proof=proof)
        or (submitted is not None and result.intent_sha256 != submitted.intent_sha256)
    ):
        _raise_relay_channel_outcome_unknown(
            message=(
                "Relay channel test readback conflicts with approved evidence; "
                "do not submit another operation"
            )
        )
    try:
        persisted = RelayChannelOperationJournalService.record_receipt(
            session,
            journal_id=journal.id,
            receipt=result.model_dump(mode="json"),
            relay_intent_sha256=result.intent_sha256,
            result_summary=_relay_channel_operation_summary(result),
            request_id=platform_request_id,
        )
    except RelayChannelOperationConflict as error:
        _raise_relay_channel_outcome_unknown(
            error,
            message=(
                "Relay channel test receipt conflicts with the durable Platform "
                "journal; do not submit another operation"
            ),
        )
    session.commit()
    persisted_receipt = _journal_receipt(persisted)
    return (persisted_receipt or result).model_dump(mode="json")


@router.post("/relay/channels/{channel_id}/status")
def set_relay_channel_status(
    channel_id: Annotated[int, Path(gt=0)],
    body: AdminSetRelayChannelStatusRequest,
    request: Request,
    response: Response,
    admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    _no_store(response)
    client = _relay_operations_client(request)
    tenant_id = _relay_channel_tenant_id(request)
    platform_request_id = _request_id(request)
    proof = _relay_channel_status_proof(
        channel_id=channel_id,
        body=body,
        actor=admin.user_id,
        request_id=platform_request_id,
    )
    readback_request_id = stable_request_id(
        "relay-channel-operation-read", f"{channel_id}:{body.operation_id}"
    )
    journal = RelayChannelOperationJournalService.find(
        session, tenant_id=tenant_id, operation_id=body.operation_id
    )
    created = False
    if journal is not None:
        try:
            RelayChannelOperationJournalService.assert_matches(
                journal,
                tenant_id=tenant_id,
                operation_id=body.operation_id,
                channel_id=channel_id,
                kind="status",
                actor_user_id=admin.user_id,
                reason=body.reason,
                expected_revision=body.expected_revision,
                target_status=body.target_status,
            )
        except RelayChannelOperationConflict as error:
            _raise_relay_channel_journal_conflict(error)
    else:
        try:
            current = client.get_channel(channel_id, request_id=platform_request_id)
        except RelayClientError as error:
            _raise_relay_channel_not_started(error)
        if current.revision != body.expected_revision:
            raise HTTPException(
                status_code=409,
                detail="Relay channel revision changed before approval",
            )
        try:
            journal, created = RelayChannelOperationJournalService.claim(
                session,
                tenant_id=tenant_id,
                operation_id=body.operation_id,
                channel_id=channel_id,
                kind="status",
                actor_user_id=admin.user_id,
                reason=body.reason,
                expected_revision=body.expected_revision,
                target_status=body.target_status,
                before_summary=_relay_channel_summary(current),
                approval_proof=proof,
                request_id=platform_request_id,
            )
        except RelayChannelOperationConflict as error:
            _raise_relay_channel_journal_conflict(error)
        session.commit()

    stored = _journal_receipt(journal)
    if stored is not None:
        if not _relay_channel_receipt_matches_proof(stored, proof=proof):
            _raise_relay_channel_outcome_unknown(
                message=(
                    "Stored Relay channel status receipt conflicts with approved "
                    "evidence; do not submit another operation"
                )
            )
        return stored.model_dump(mode="json")

    submitted: RelayChannelOperation | None = None
    revision_conflict = False
    if created:
        try:
            submitted = client.set_channel_status(
                channel_id,
                operation_id=body.operation_id,
                actor=admin.user_id,
                reason=body.reason,
                expected_revision=body.expected_revision,
                target_status=body.target_status,
                request_id=platform_request_id,
            )
        except RelayPermanentError as error:
            if (
                error.response_status != 409
                or error.relay_error is None
                or error.relay_error.code != "CHANNEL_REVISION_CONFLICT"
            ):
                _raise_relay_channel_outcome_unknown(error)
            revision_conflict = True
        except RelayClientError as error:
            _raise_relay_channel_outcome_unknown(error)
        if submitted is not None and not _relay_channel_receipt_matches_proof(
            submitted, proof=proof
        ):
            _raise_relay_channel_outcome_unknown(
                message=(
                    "Relay channel status receipt conflicts with approved evidence; "
                    "do not submit another operation"
                )
            )

    try:
        result = client.get_channel_operation(
            channel_id,
            operation_id=body.operation_id,
            request_id=readback_request_id,
        )
    except RelayPermanentError as error:
        if error.response_status == 404:
            _raise_relay_channel_outcome_unknown(
                error,
                message=(
                    "Relay channel status change was approved but has no readable "
                    "receipt; verify the channel before taking another action"
                ),
            )
        _raise_relay_channel_outcome_unknown(error)
    except RelayClientError as error:
        _raise_relay_channel_outcome_unknown(error)
    if (
        not _relay_channel_receipt_matches_proof(result, proof=proof)
        or (submitted is not None and result.intent_sha256 != submitted.intent_sha256)
        or (revision_conflict and result.state != "failed")
    ):
        _raise_relay_channel_outcome_unknown(
            message=(
                "Relay channel status readback conflicts with approved evidence; "
                "do not submit another operation"
            )
        )
    try:
        persisted = RelayChannelOperationJournalService.record_receipt(
            session,
            journal_id=journal.id,
            receipt=result.model_dump(mode="json"),
            relay_intent_sha256=result.intent_sha256,
            result_summary=_relay_channel_operation_summary(result),
            request_id=platform_request_id,
        )
    except RelayChannelOperationConflict as error:
        _raise_relay_channel_outcome_unknown(
            error,
            message=(
                "Relay channel status receipt conflicts with the durable Platform "
                "journal; do not submit another operation"
            ),
        )
    session.commit()
    persisted_receipt = _journal_receipt(persisted)
    result = persisted_receipt or result
    return result.model_dump(mode="json")


@router.get("/relay/submission-unknown")
def list_relay_unknown_submissions(
    request: Request,
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    _no_store(response)
    client = _relay_operations_client(request)
    try:
        result = client.list_submission_unknown(
            page=page,
            page_size=page_size,
            request_id=_request_id(request),
        )
    except RelayClientError as error:
        _raise_relay_operations_error(error)
    return result.model_dump(mode="json")


@router.get("/relay/submission-unknown/{job_id}")
def get_relay_unknown_submission(
    job_id: str,
    request: Request,
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
) -> dict[str, Any]:
    _no_store(response)
    client = _relay_operations_client(request)
    try:
        result = client.get_submission_unknown(
            job_id,
            request_id=_request_id(request),
        )
    except (RelayClientError, ValueError) as error:
        if isinstance(error, RelayClientError):
            _raise_relay_operations_error(error)
        raise HTTPException(status_code=422, detail="job_id must be a UUID") from error
    return result.model_dump(mode="json")


@router.get("/relay/submission-unknown/{job_id}/result")
def get_relay_unknown_submission_result(
    job_id: str,
    request: Request,
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
    operation_id: str | None = Query(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$",
    ),
) -> dict[str, Any]:
    _no_store(response)
    try:
        canonical_job_id = str(UUID(job_id))
    except ValueError as error:
        raise HTTPException(status_code=422, detail="job_id must be a UUID") from error
    if operation_id is None:
        operation_id = _latest_relay_reconciliation_operation_id(
            session,
            job_id=canonical_job_id,
        )
        if operation_id is None:
            raise HTTPException(
                status_code=404,
                detail="No Platform reconciliation operation exists for this job",
            )
    client = _relay_operations_client(request)
    try:
        result = client.get_reconciliation_result(
            canonical_job_id,
            operation_id=operation_id,
            request_id=_request_id(request),
        )
    except RelayClientError as error:
        _raise_relay_operations_error(error)
    return result.model_dump(mode="json")


@router.post("/relay/submission-unknown/{job_id}/resolve")
def resolve_relay_unknown_submission(
    job_id: str,
    body: AdminResolveRelayUnknownSubmissionRequest,
    request: Request,
    response: Response,
    admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    _no_store(response)
    client = _relay_operations_client(request)
    try:
        canonical_job_id = str(UUID(job_id))
    except ValueError as error:
        raise HTTPException(status_code=422, detail="job_id must be a UUID") from error

    platform_request_id = _request_id(request)
    operation_id = body.operation_id or stable_request_id(
        "relay-reconcile-op",
        canonical_job_id,
    )
    proof = _relay_reconciliation_proof(
        body=body,
        operation_id=operation_id,
        approved_by=admin.user_id,
        request_id=platform_request_id,
    )
    readback_request_id = stable_request_id(
        "relay-result-read",
        f"{platform_request_id}:{operation_id}",
    )

    existing_result: RelayUnknownSubmissionResult | None = None
    try:
        existing_result = client.get_reconciliation_result(
            canonical_job_id,
            operation_id=operation_id,
            request_id=readback_request_id,
        )
    except RelayPermanentError as error:
        if error.response_status != 404:
            _raise_relay_operations_error(error)
    except RelayClientError as error:
        _raise_relay_operations_error(error)

    current: RelayUnknownSubmission | None = None
    if existing_result is not None:
        if not _relay_result_matches_proof(
            existing_result,
            job_id=canonical_job_id,
            proof=proof,
        ):
            raise HTTPException(
                status_code=409,
                detail="Relay operation_id is already bound to different evidence",
            )
        before_summary = _relay_result_summary(existing_result)
    else:
        try:
            current = client.get_submission_unknown(
                canonical_job_id,
                request_id=platform_request_id,
            )
        except RelayClientError as error:
            _raise_relay_operations_error(error)
        if (
            current.provider_route_id != body.expected_route_id
            or current.provider_submission_attempt != body.expected_submission_attempt
            or current.reconciliation_token != body.expected_reconciliation_token
        ):
            raise HTTPException(
                status_code=409,
                detail="Relay reconciliation fencing proof is stale or conflicts",
            )
        before_summary = _relay_unknown_summary(current)
        before_summary["operation_id"] = operation_id

    approval = _find_relay_reconciliation_audit(
        session,
        action="relay.submission_unknown.approve",
        job_id=canonical_job_id,
        operation_id=operation_id,
    )
    if approval is not None and not _audit_matches_reconciliation_proof(
        approval,
        actor_user_id=admin.user_id,
        proof=proof,
    ):
        raise HTTPException(
            status_code=409,
            detail="Platform operation_id is already bound to different approval evidence",
        )
    if approval is None:
        approval = AuditService.append(
            session,
            actor_user_id=admin.user_id,
            action="relay.submission_unknown.approve",
            target_type="relay_generation_job",
            target_id=canonical_job_id,
            before_summary=before_summary,
            after_summary=proof,
            request_id=platform_request_id,
        )
        # The approval and its stable operation_id must survive an ambiguous
        # network outcome from the following Relay side effect.
        session.commit()

    try:
        resolved = client.resolve_submission_unknown(
            canonical_job_id,
            operation_id=operation_id,
            outcome=body.outcome,
            upstream_task_id=body.upstream_task_id,
            expected_route_id=body.expected_route_id,
            expected_submission_attempt=body.expected_submission_attempt,
            expected_reconciliation_token=body.expected_reconciliation_token,
            verification_reference=body.verification_reference,
            approved_by=admin.user_id,
            approval_reason=body.reason,
            request_id=platform_request_id,
        )
    except RelayClientError as error:
        _raise_relay_operations_error(error)

    try:
        reconciliation_result = client.get_reconciliation_result(
            canonical_job_id,
            operation_id=operation_id,
            request_id=readback_request_id,
        )
    except RelayClientError as error:
        _raise_relay_operations_error(error)
    if not _relay_result_matches_proof(
        reconciliation_result,
        job_id=canonical_job_id,
        proof=proof,
    ):
        raise HTTPException(
            status_code=409,
            detail="Relay reconciliation receipt conflicts with approved evidence",
        )

    resolution_summary = {
        **proof,
        "status": resolved.status,
        "current_status": reconciliation_result.current_status,
        "resolved_status": reconciliation_result.resolved_status,
        "event_id": str(reconciliation_result.event_id),
        "payload_sha256": reconciliation_result.payload_sha256,
        "relay_request_id": reconciliation_result.request_id,
        "approval_audit_id": approval.id,
        "approval_key_id": reconciliation_result.approval_key_id,
        "approval_signature": reconciliation_result.approval_signature,
    }
    resolution_audit = _find_relay_reconciliation_audit(
        session,
        action="relay.submission_unknown.resolve",
        job_id=canonical_job_id,
        operation_id=operation_id,
    )
    if resolution_audit is not None and not _audit_matches_reconciliation_proof(
        resolution_audit,
        actor_user_id=admin.user_id,
        proof=proof,
    ):
        raise HTTPException(
            status_code=409,
            detail="Platform operation_id is already bound to a different resolution",
        )
    if resolution_audit is None:
        AuditService.append(
            session,
            actor_user_id=admin.user_id,
            action="relay.submission_unknown.resolve",
            target_type="relay_generation_job",
            target_id=canonical_job_id,
            before_summary=before_summary,
            after_summary=resolution_summary,
            request_id=platform_request_id,
        )
        session.commit()
    return {
        "operation_id": operation_id,
        "approval_audit_id": approval.id,
        "resolved": resolved.model_dump(mode="json"),
        "reconciliation_result": reconciliation_result.model_dump(mode="json"),
    }


@router.get("/relay/callback-dead-letters")
def list_relay_callback_dead_letters(
    request: Request,
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    _no_store(response)
    client = _relay_operations_client(request)
    try:
        result = client.list_callback_dead_letters(
            page=page,
            page_size=page_size,
            request_id=_request_id(request),
        )
    except RelayClientError as error:
        _raise_relay_operations_error(error)
    return result.model_dump(mode="json")


@router.get("/relay/callback-dead-letters/{event_id}")
def get_relay_callback_dead_letter(
    event_id: str,
    request: Request,
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
) -> dict[str, Any]:
    _no_store(response)
    client = _relay_operations_client(request)
    try:
        result = client.get_callback_dead_letter(
            event_id,
            request_id=_request_id(request),
        )
    except (RelayClientError, ValueError) as error:
        if isinstance(error, RelayClientError):
            _raise_relay_operations_error(error)
        raise HTTPException(status_code=422, detail="event_id must be a UUID") from error
    return result.model_dump(mode="json")


@router.get("/relay/callback-dead-letters/{event_id}/result")
def get_relay_callback_redrive_result(
    event_id: str,
    request: Request,
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
    operation_id: str | None = Query(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$",
    ),
) -> dict[str, Any]:
    _no_store(response)
    try:
        canonical_event_id = str(UUID(event_id))
    except ValueError as error:
        raise HTTPException(status_code=422, detail="event_id must be a UUID") from error
    if operation_id is None:
        operation_id = _latest_callback_redrive_operation_id(
            session, event_id=canonical_event_id
        )
        if operation_id is None:
            raise HTTPException(
                status_code=404,
                detail="No Platform callback redrive operation exists for this event",
            )
    client = _relay_operations_client(request)
    try:
        result = client.get_callback_redrive_result(
            canonical_event_id,
            operation_id=operation_id,
            request_id=_request_id(request),
        )
    except RelayClientError as error:
        _raise_relay_operations_error(error)
    return result.model_dump(mode="json")


@router.post("/relay/callback-dead-letters/{event_id}/redrive")
def redrive_relay_callback_dead_letter(
    event_id: str,
    body: AdminRedriveRelayCallbackRequest,
    request: Request,
    response: Response,
    admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    _no_store(response)
    try:
        canonical_event_id = str(UUID(event_id))
    except ValueError as error:
        raise HTTPException(status_code=422, detail="event_id must be a UUID") from error
    platform_request_id = _request_id(request)
    operation_id = body.operation_id or stable_request_id(
        "relay-callback-redrive-op", canonical_event_id
    )
    proof = {
        "operation_id": operation_id,
        "actor": body.actor,
        "reason": body.reason,
        "approved_by": admin.user_id,
        "request_id": platform_request_id,
    }
    readback_request_id = stable_request_id(
        "relay-callback-redrive-read", f"{platform_request_id}:{operation_id}"
    )
    client = _relay_operations_client(request)

    existing: RelayCallbackRedriveResult | None = None
    try:
        existing = client.get_callback_redrive_result(
            canonical_event_id,
            operation_id=operation_id,
            request_id=readback_request_id,
        )
    except RelayPermanentError as error:
        if error.response_status != 404:
            _raise_relay_operations_error(error)
    except RelayClientError as error:
        _raise_relay_operations_error(error)

    current: RelayCallbackDelivery | None = None
    if existing is not None:
        if existing.evidence.actor != body.actor or existing.evidence.reason != body.reason:
            raise HTTPException(
                status_code=409,
                detail="Relay operation_id is already bound to different callback evidence",
            )
        before_summary = _callback_redrive_summary(existing)
    else:
        try:
            current = client.get_callback_dead_letter(
                canonical_event_id,
                request_id=platform_request_id,
            )
        except RelayClientError as error:
            _raise_relay_operations_error(error)
        if current.state != "dead_letter":
            raise HTTPException(
                status_code=409,
                detail="Only a dead-lettered Relay callback can be redriven",
            )
        before_summary = _callback_delivery_summary(current)

    approval = _find_callback_redrive_audit(
        session,
        action="relay.callback_dead_letter.approve_redrive",
        event_id=canonical_event_id,
        operation_id=operation_id,
    )
    if approval is not None and (
        approval.actor_user_id != admin.user_id
        or any(approval.after_summary.get(key) != value for key, value in proof.items() if key != "request_id")
    ):
        raise HTTPException(
            status_code=409,
            detail="Platform operation_id is already bound to different approval evidence",
        )
    if approval is None:
        approval = AuditService.append(
            session,
            actor_user_id=admin.user_id,
            action="relay.callback_dead_letter.approve_redrive",
            target_type="relay_callback_delivery",
            target_id=canonical_event_id,
            before_summary=before_summary,
            after_summary=proof,
            request_id=platform_request_id,
        )
        session.commit()

    if existing is None:
        try:
            client.redrive_callback_dead_letter(
                canonical_event_id,
                operation_id=operation_id,
                actor=body.actor,
                reason=body.reason,
                request_id=platform_request_id,
            )
        except RelayClientError as error:
            _raise_relay_operations_error(error)
    else:
        # The Relay already owns an immutable receipt for this operation_id.
        # Never send the POST again, even if an operator repeats the Platform
        # request after the first response was lost.
        result = existing

    if existing is None:
        try:
            result = client.get_callback_redrive_result(
                canonical_event_id,
                operation_id=operation_id,
                request_id=readback_request_id,
            )
        except RelayClientError as error:
            _raise_relay_operations_error(error)
    if result.evidence.actor != body.actor or result.evidence.reason != body.reason:
        raise HTTPException(
            status_code=409,
            detail="Relay callback redrive receipt conflicts with approved evidence",
        )

    resolution = _find_callback_redrive_audit(
        session,
        action="relay.callback_dead_letter.redrive",
        event_id=canonical_event_id,
        operation_id=operation_id,
    )
    after_summary = {**proof, **_callback_redrive_summary(result), "approval_audit_id": approval.id}
    if resolution is None:
        AuditService.append(
            session,
            actor_user_id=admin.user_id,
            action="relay.callback_dead_letter.redrive",
            target_type="relay_callback_delivery",
            target_id=canonical_event_id,
            before_summary=before_summary,
            after_summary=after_summary,
            request_id=platform_request_id,
        )
        session.commit()
    return {
        "operation_id": operation_id,
        "approval_audit_id": approval.id,
        "redrive_result": result.model_dump(mode="json"),
    }


@router.get("/analytics/model-profitability")
def model_profitability(
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    include_inactive: bool = True,
) -> dict[str, Any]:
    _no_store(response)
    start, end = _window(start_time=start_time, end_time=end_time, default_days=30)
    return AdminAnalyticsService.model_profitability(
        session,
        start=start,
        end=end,
        include_inactive=include_inactive,
    )


@router.get("/analytics/company-health")
def company_health(
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    low_balance_threshold_cents: int = Query(default=0, ge=0),
    inactivity_days: int = Query(default=30, ge=1, le=365),
    stale_reservation_hours: int = Query(default=24, ge=1, le=720),
    failure_rate_threshold: float = Query(default=0.30, ge=0.0, le=1.0),
    minimum_terminal_tasks: int = Query(default=5, ge=1, le=10000),
    abnormal_spend_ratio: float = Query(default=3.0, gt=1.0, le=100.0),
) -> dict[str, Any]:
    _no_store(response)
    return AdminAnalyticsService.company_health(
        session,
        page=page,
        page_size=page_size,
        now=datetime.now(timezone.utc),
        low_balance_threshold_cents=low_balance_threshold_cents,
        inactivity_days=inactivity_days,
        stale_reservation_hours=stale_reservation_hours,
        failure_rate_threshold=failure_rate_threshold,
        minimum_terminal_tasks=minimum_terminal_tasks,
        abnormal_spend_ratio=abnormal_spend_ratio,
    )


@router.get("/analytics/channel-health")
def channel_health(
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> dict[str, Any]:
    _no_store(response)
    start, end = _window(start_time=start_time, end_time=end_time, default_days=7)
    return AdminAnalyticsService.channel_health_summary(session, start=start, end=end)


@router.get("/analytics/data-readiness")
def data_readiness(
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    _no_store(response)
    return AdminAnalyticsService.data_readiness(session, now=datetime.now(timezone.utc))


@router.get("/analytics/exceptions")
def exception_center(
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
    limit_per_category: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    _no_store(response)
    return AdminAnalyticsService.exception_center(
        session,
        now=datetime.now(timezone.utc),
        limit_per_category=limit_per_category,
    )


@router.post(
    "/analytics/exceptions/companies/{company_id}/publication-jobs/"
    "{job_id}/reconcile"
)
def reconcile_publication_exception(
    company_id: str,
    job_id: str,
    body: AdminReconcilePublicationJobRequest,
    request: Request,
    response: Response,
    admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    """Resolve an unknown provider submission after an external verification.

    This intentionally delegates to the same locked, idempotent reconciliation
    path used by company operators. It never retries an unknown submission and
    remains available after the auto-publish entitlement is removed so safety
    work cannot be stranded.
    """

    _no_store(response)
    job = PublishingService.get_job(session, company_id=company_id, job_id=job_id)
    before = _publication_job_summary(job)
    job, changed = PublishingService.reconcile_unknown_job(
        session,
        company_id=company_id,
        job_id=job_id,
        outcome=body.outcome,
        external_post_id=body.external_post_id,
        external_post_url=(
            str(body.external_post_url) if body.external_post_url is not None else None
        ),
        error_code=body.error_code,
        error_message=body.error_message,
    )
    after = {**_publication_job_summary(job), "change_reason": body.reason}
    if changed:
        AuditService.append(
            session,
            actor_user_id=admin.user_id,
            action="publishing.job.reconcile",
            target_type="publication_job",
            target_id=job.id,
            before_summary=before,
            after_summary=after,
            request_id=_request_id(request),
        )
    return {
        "changed": changed,
        "company_id": job.company_id,
        "job_id": job.id,
        "status": job.status.value,
        "external_post_id": job.external_post_id,
        "external_post_url": job.external_post_url,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "published_at": (
            job.published_at.isoformat() if job.published_at is not None else None
        ),
    }


@router.get("/entitlements/matrix")
def entitlement_matrix(
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
    company_page: int = Query(default=1, ge=1),
    company_page_size: int = Query(default=25, ge=1, le=100),
    catalog_page: int = Query(default=1, ge=1),
    catalog_page_size: int = Query(default=50, ge=1, le=100),
    company_query: str | None = Query(default=None, max_length=160),
    catalog_query: str | None = Query(default=None, max_length=160),
    catalog_kind: Literal["all", "model", "feature", "agent", "external_api"] = "all",
    include_retired: bool = True,
) -> dict[str, Any]:
    _no_store(response)
    return AdminEntitlementService.matrix(
        session,
        company_page=company_page,
        company_page_size=company_page_size,
        catalog_page=catalog_page,
        catalog_page_size=catalog_page_size,
        company_query=company_query,
        catalog_query=catalog_query,
        catalog_kind=catalog_kind,
        include_retired=include_retired,
    )


@router.get("/entitlements/coverage")
def entitlement_coverage(
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
    include_retired: bool = True,
) -> dict[str, Any]:
    _no_store(response)
    return AdminEntitlementService.coverage(session, include_retired=include_retired)


@router.post("/entitlements/batch/preview")
def preview_entitlement_batch(
    body: BatchPreviewRequest,
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    _no_store(response)
    return AdminEntitlementService.preview_changes(
        session, changes=_dump_cells(body.changes)
    )


@router.post("/entitlements/batch/execute")
def execute_entitlement_batch(
    body: BatchExecuteRequest,
    request: Request,
    response: Response,
    admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    _no_store(response)
    return AdminEntitlementService.execute_changes(
        session,
        changes=_dump_cells(body.changes),
        expected_snapshot=body.expected_snapshot,
        actor_user_id=admin.user_id,
        reason=body.reason,
        request_id=_request_id(request),
        idempotency_key=body.idempotency_key,
    )


@router.post("/entitlements/copy/preview")
def preview_entitlement_copy(
    body: CopyPreviewRequest,
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    _no_store(response)
    changes = AdminEntitlementService.changes_from_company(
        session,
        source_company_id=body.source_company_id,
        target_company_ids=body.target_company_ids,
        mode=body.mode,
        include_models=body.include_models,
        include_resources=body.include_resources,
    )
    return AdminEntitlementService.preview_changes(session, changes=changes)


@router.post("/entitlements/copy/execute")
def execute_entitlement_copy(
    body: CopyExecuteRequest,
    request: Request,
    response: Response,
    admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    _no_store(response)
    changes = AdminEntitlementService.changes_from_company(
        session,
        source_company_id=body.source_company_id,
        target_company_ids=body.target_company_ids,
        mode=body.mode,
        include_models=body.include_models,
        include_resources=body.include_resources,
    )
    return AdminEntitlementService.execute_changes(
        session,
        changes=changes,
        expected_snapshot=body.expected_snapshot,
        actor_user_id=admin.user_id,
        reason=body.reason,
        request_id=_request_id(request),
        idempotency_key=body.idempotency_key,
    )


@router.post("/entitlements/templates/preview")
def preview_entitlement_template(
    body: TemplatePreviewRequest,
    response: Response,
    _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    _no_store(response)
    changes = AdminEntitlementService.changes_from_template(
        session,
        template_cells=_dump_cells(body.cells),
        target_company_ids=body.target_company_ids,
        mode=body.mode,
    )
    preview = AdminEntitlementService.preview_changes(session, changes=changes)
    return {
        **preview,
        "template_name": body.template_name,
        "template_version": body.template_version,
    }


@router.post("/entitlements/templates/execute")
def execute_entitlement_template(
    body: TemplateExecuteRequest,
    request: Request,
    response: Response,
    admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    _no_store(response)
    changes = AdminEntitlementService.changes_from_template(
        session,
        template_cells=_dump_cells(body.cells),
        target_company_ids=body.target_company_ids,
        mode=body.mode,
    )
    result = AdminEntitlementService.execute_changes(
        session,
        changes=changes,
        expected_snapshot=body.expected_snapshot,
        actor_user_id=admin.user_id,
        reason=body.reason,
        request_id=_request_id(request),
        idempotency_key=body.idempotency_key,
    )
    return {
        **result,
        "template_name": body.template_name,
        "template_version": body.template_version,
    }
