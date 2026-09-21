from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..dependencies import get_db, get_finance_snapshot_db, require_internal_service
from ..services.financial_reconciliation import FinancialReconciliationService
from ..services.payment_settlements import (
    PaymentSettlementImportService,
    ProviderCostStatementImportService,
)


router = APIRouter(prefix="/internal/finance", tags=["finance-operations"])


def _response_utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SettlementImportRequest(StrictModel):
    provider: str = Field(pattern=r"^[a-z][a-z0-9._-]{0,63}$")
    merchant_account: str = Field(min_length=1, max_length=120)
    source_kind: Literal["psp_statement", "bank_statement"]
    period_start: AwareDatetime
    period_end: AwareDatetime
    provider_document_id: str | None = Field(default=None, max_length=160)
    source_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_document_base64: str = Field(min_length=4, max_length=70_000_000)
    source_object_key: str = Field(min_length=1, max_length=512)
    source_object_version: str = Field(min_length=1, max_length=160)
    source_size_bytes: int = Field(gt=0, le=52_428_800)
    parser_version: str = Field(min_length=1, max_length=80)


class SettlementEntryResponse(StrictModel):
    id: str
    provider_line_id: str
    line_type: str
    provider_transaction_id: str | None
    related_provider_reference: str | None
    gross_amount_cents: int
    fee_amount_cents: int
    net_amount_cents: int
    currency: str
    occurred_at: AwareDatetime


class SettlementImportResponse(StrictModel):
    batch_id: str
    created_batch: bool
    created_count: int
    total_count: int
    source_document_sha256: str
    verification_method: str
    source_authenticity: Literal["unverified"]
    batch_manifest: dict[str, Any]
    entries: list[SettlementEntryResponse]


class ProviderCostImportRequest(StrictModel):
    supplier: str = Field(min_length=1, max_length=120)
    supplier_account: str = Field(min_length=1, max_length=160)
    period_start: AwareDatetime
    period_end: AwareDatetime
    provider_document_id: str | None = Field(default=None, max_length=160)
    source_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_document_base64: str = Field(min_length=4, max_length=70_000_000)
    source_object_key: str = Field(min_length=1, max_length=512)
    source_object_version: str = Field(min_length=1, max_length=160)
    source_size_bytes: int = Field(gt=0, le=52_428_800)
    parser_version: str = Field(min_length=1, max_length=80)


class ProviderCostLineResponse(StrictModel):
    id: str
    provider_line_id: str
    provider_job_reference: str
    channel_key: str | None
    task_id: str | None
    amount_cents: int
    currency: str
    occurred_at: AwareDatetime


class ProviderCostImportResponse(StrictModel):
    batch_id: str
    created_batch: bool
    created_count: int
    total_count: int
    source_document_sha256: str
    verification_method: str
    source_authenticity: Literal["unverified"]
    lines_sha256: str
    total_cost_cents: int
    lines: list[ProviderCostLineResponse]


class ReconciliationRunRequest(StrictModel):
    run_kind: str = Field(min_length=1, max_length=24)
    period_start: AwareDatetime
    period_end: AwareDatetime
    idempotency_key: str = Field(min_length=1, max_length=160)
    provider: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9._-]{0,63}$"
    )
    merchant_account: str | None = Field(default=None, max_length=120)
    provider_statement_available: bool
    payment_settlement_batch_ids: list[str] = Field(
        default_factory=list, max_length=10_000
    )
    provider_cost_statement_available: bool
    provider_cost_batch_ids: list[str] = Field(
        default_factory=list, max_length=10_000
    )
    source_watermarks: dict[str, Any] = Field(default_factory=dict)


class ReconciliationSnapshotResponse(StrictModel):
    id: str
    dimension: str
    status: str
    totals: dict[str, Any]
    evidence_sha256: str


class ReconciliationExceptionResponse(StrictModel):
    id: str
    dimension: str
    code: str
    severity: str
    entity_type: str
    entity_id: str | None
    expected_amount: int | None
    actual_amount: int | None
    evidence_sha256: str
    details: dict[str, Any]


class ReconciliationRunResponse(StrictModel):
    id: str
    status: str
    created: bool
    period_start: AwareDatetime
    period_end: AwareDatetime
    provider: str | None
    merchant_account: str | None
    snapshot_sha256: str
    source_watermarks: dict[str, Any]
    control_totals: dict[str, Any]
    snapshots: list[ReconciliationSnapshotResponse]
    exceptions: list[ReconciliationExceptionResponse]


class ReconciliationResolutionRequest(StrictModel):
    action: Literal[
        "acknowledged",
        "source_corrected",
        "ledger_corrected",
        "accepted_adjustment",
        "escalated",
    ]
    note: str = Field(min_length=1, max_length=240)
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor_user_id: str = Field(min_length=36, max_length=36)
    idempotency_key: str = Field(min_length=1, max_length=120)


class ReconciliationResolutionResponse(StrictModel):
    id: str
    exception_id: str
    action: str
    note: str
    evidence_sha256: str
    actor_user_id: str
    created: bool


@router.post("/payment-settlements/import", response_model=SettlementImportResponse)
def import_payment_settlement(
    body: SettlementImportRequest,
    _: Annotated[None, Depends(require_internal_service)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    try:
        source_document_bytes = base64.b64decode(
            body.source_document_base64, validate=True
        )
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="支付结算原始文件 base64 无效") from exc
    result = PaymentSettlementImportService.import_document(
        session,
        provider=body.provider,
        merchant_account=body.merchant_account,
        source_kind=body.source_kind,
        period_start=body.period_start,
        period_end=body.period_end,
        provider_document_id=body.provider_document_id,
        source_document_sha256=body.source_document_sha256,
        source_document_bytes=source_document_bytes,
        source_object_key=body.source_object_key,
        source_object_version=body.source_object_version,
        source_size_bytes=body.source_size_bytes,
        parser_version=body.parser_version,
    )
    return {
        "batch_id": result.batch.id,
        "created_batch": result.created_batch,
        "created_count": result.created_count,
        "total_count": len(result.entries),
        "source_document_sha256": result.batch.source_document_sha256,
        "verification_method": result.batch.verification_method,
        "source_authenticity": "unverified",
        "batch_manifest": result.manifest.evidence_payload(),
        "entries": [
            {
                "id": entry.id,
                "provider_line_id": entry.provider_line_id,
                "line_type": entry.line_type,
                "provider_transaction_id": entry.provider_transaction_id,
                "related_provider_reference": entry.related_provider_reference,
                "gross_amount_cents": entry.gross_amount_cents,
                "fee_amount_cents": entry.fee_amount_cents,
                "net_amount_cents": entry.net_amount_cents,
                "currency": entry.currency,
                "occurred_at": _response_utc(entry.occurred_at),
            }
            for entry in result.entries
        ],
    }


@router.post(
    "/provider-cost-statements/import", response_model=ProviderCostImportResponse
)
def import_provider_cost_statement(
    body: ProviderCostImportRequest,
    _: Annotated[None, Depends(require_internal_service)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    try:
        source_document_bytes = base64.b64decode(
            body.source_document_base64, validate=True
        )
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="供应商账单原始文件 base64 无效") from exc
    result = ProviderCostStatementImportService.import_document(
        session,
        supplier=body.supplier,
        supplier_account=body.supplier_account,
        period_start=body.period_start,
        period_end=body.period_end,
        provider_document_id=body.provider_document_id,
        source_document_sha256=body.source_document_sha256,
        source_document_bytes=source_document_bytes,
        source_object_key=body.source_object_key,
        source_object_version=body.source_object_version,
        source_size_bytes=body.source_size_bytes,
        parser_version=body.parser_version,
    )
    return {
        "batch_id": result.batch.id,
        "created_batch": result.created_batch,
        "created_count": result.created_count,
        "total_count": len(result.lines),
        "source_document_sha256": result.batch.source_document_sha256,
        "verification_method": result.batch.verification_method,
        "source_authenticity": "unverified",
        "lines_sha256": result.batch.lines_sha256,
        "total_cost_cents": result.batch.total_cost_cents,
        "lines": [
            {
                "id": line.id,
                "provider_line_id": line.provider_line_id,
                "provider_job_reference": line.provider_job_reference,
                "channel_key": line.channel_key,
                "task_id": line.task_id,
                "amount_cents": line.amount_cents,
                "currency": line.currency,
                "occurred_at": _response_utc(line.occurred_at),
            }
            for line in result.lines
        ],
    }


@router.post("/reconciliations", response_model=ReconciliationRunResponse)
def run_financial_reconciliation(
    body: ReconciliationRunRequest,
    _: Annotated[None, Depends(require_internal_service)],
    session: Annotated[Session, Depends(get_finance_snapshot_db, scope="function")],
):
    if body.provider_statement_available and not body.payment_settlement_batch_ids:
        raise HTTPException(
            status_code=422,
            detail="结算单可用时必须绑定 PSP 与银行结算批次",
        )
    if (
        body.provider_cost_statement_available
        and not body.provider_cost_batch_ids
    ):
        raise HTTPException(
            status_code=422,
            detail="供应商成本账单可用时必须绑定账单批次",
        )
    result = FinancialReconciliationService.run(
        session,
        **body.model_dump(),
    )
    run = result.run
    assert run.snapshot_sha256 is not None
    return {
        "id": run.id,
        "status": run.status.value,
        "created": result.created,
        "period_start": _response_utc(run.period_start),
        "period_end": _response_utc(run.period_end),
        "provider": run.provider,
        "merchant_account": run.merchant_account,
        "snapshot_sha256": run.snapshot_sha256,
        "source_watermarks": run.source_watermarks,
        "control_totals": run.control_totals,
        "snapshots": [
            {
                "id": snapshot.id,
                "dimension": snapshot.dimension,
                "status": snapshot.status.value,
                "totals": snapshot.totals,
                "evidence_sha256": snapshot.evidence_sha256,
            }
            for snapshot in result.snapshots
        ],
        "exceptions": [
            {
                "id": exception.id,
                "dimension": exception.dimension,
                "code": exception.code,
                "severity": exception.severity,
                "entity_type": exception.entity_type,
                "entity_id": exception.entity_id,
                "expected_amount": exception.expected_amount,
                "actual_amount": exception.actual_amount,
                "evidence_sha256": exception.evidence_sha256,
                "details": exception.details,
            }
            for exception in result.exceptions
        ],
    }


@router.post(
    "/reconciliations/exceptions/{exception_id}/resolutions",
    response_model=ReconciliationResolutionResponse,
)
def record_reconciliation_resolution(
    exception_id: str,
    body: ReconciliationResolutionRequest,
    _: Annotated[None, Depends(require_internal_service)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    resolution, created = FinancialReconciliationService.record_resolution(
        session,
        exception_id=exception_id,
        **body.model_dump(),
    )
    return {
        "id": resolution.id,
        "exception_id": resolution.exception_id,
        "action": resolution.action,
        "note": resolution.note,
        "evidence_sha256": resolution.evidence_sha256,
        "actor_user_id": resolution.actor_user_id,
        "created": created,
    }
