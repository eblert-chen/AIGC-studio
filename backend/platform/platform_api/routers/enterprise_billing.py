from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..dependencies import (
    TenantContext,
    get_db,
    require_internal_service,
    require_permission,
)
from ..models import (
    CompanyBillingAccount,
    CompanyBillingContractVersion,
    CompanyBillingCycle,
    CompanyInvoice,
    CompanyInvoiceLine,
    CompanyPointLot,
)
from ..services.enterprise_billing import EnterpriseBillingService
from ..services.errors import ConflictError, NotFoundError


router = APIRouter(tags=["enterprise-billing"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActivateContractRequest(StrictModel):
    company_id: str = Field(min_length=1, max_length=36)
    contract_reference: str = Field(min_length=1, max_length=160)
    currency: Literal["CNY"] = "CNY"
    timezone_name: str = Field(min_length=1, max_length=80)
    cycle_day: int = Field(ge=1, le=28)
    payment_terms_days: int = Field(ge=0, le=180)
    credit_limit_points: int = Field(gt=0, le=9_000_000_000_000_000)
    effective_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    created_by_user_id: str = Field(min_length=1, max_length=36)


class OpenCycleRequest(StrictModel):
    company_id: str = Field(min_length=1, max_length=36)
    period_start: AwareDatetime
    period_end: AwareDatetime


class CloseAndIssueCycleRequest(StrictModel):
    company_id: str = Field(min_length=1, max_length=36)
    issued_at: AwareDatetime | None = None


class RunDunningRequest(StrictModel):
    company_id: str = Field(min_length=1, max_length=36)
    as_of: AwareDatetime
    idempotency_key: str = Field(min_length=8, max_length=80)


class RunEnterpriseOnceRequest(StrictModel):
    pass


class RunEnterpriseOnceResponse(StrictModel):
    processed: bool
    company_id: str | None
    closed_cycle_ids: list[str]
    opened_cycle_id: str | None
    dunning_run_id: str | None


class ContractResponse(StrictModel):
    id: str
    company_id: str
    status: str
    contract_reference: str
    currency: Literal["CNY"]
    timezone_name: str
    cycle_day: int
    payment_terms_days: int
    credit_limit_points: int
    receivable_per_point_cents: int
    supersedes_version_id: str | None
    effective_at: datetime
    expires_at: datetime | None
    created_by_user_id: str
    created_at: datetime


class BillingAccountResponse(StrictModel):
    company_id: str
    active_contract_version_id: str
    unbilled_receivable_cents: int
    billing_hold: bool
    billing_hold_reason: str | None
    billing_hold_since: datetime | None
    dunning_level: int
    created_at: datetime
    updated_at: datetime


class ContractActivationResponse(StrictModel):
    created: bool
    contract: ContractResponse
    account: BillingAccountResponse


class ContractLotResponse(StrictModel):
    id: str
    company_id: str
    source_kind: str
    contract_version_id: str | None
    billing_cycle_id: str | None
    original_points: int
    available_points: int
    reserved_points: int
    reversal_reserved_points: int
    settled_points: int
    reversed_points: int
    cash_basis_cents: int
    receivable_basis_cents: int
    subsidy_cents: int
    expires_at: datetime | None
    created_at: datetime


class BillingCycleResponse(StrictModel):
    id: str
    company_id: str
    contract_version_id: str
    period_start: datetime
    period_end: datetime
    status: str
    frozen_at: datetime | None
    invoice_id: str | None
    created_at: datetime
    updated_at: datetime


class OpenCycleResponse(StrictModel):
    created: bool
    cycle: BillingCycleResponse
    credit_lot: ContractLotResponse | None


class BillingCyclePage(StrictModel):
    page: int
    page_size: int
    total: int
    items: list[BillingCycleResponse]


class InvoiceResponse(StrictModel):
    id: str
    company_id: str
    cycle_id: str
    invoice_number: str
    status: str
    currency: Literal["CNY"]
    subtotal_cents: int
    credit_cents: int
    tax_cents: int
    total_cents: int
    paid_cents: int
    outstanding_cents: int
    issued_at: datetime | None
    due_at: datetime | None
    created_at: datetime
    updated_at: datetime


class InvoiceLineResponse(StrictModel):
    id: str
    invoice_id: str
    task_id: str
    value_allocation_id: str
    contract_version_id: str
    points: int
    amount_cents: int
    created_at: datetime


class InvoicePage(StrictModel):
    page: int
    page_size: int
    total: int
    items: list[InvoiceResponse]


class InvoiceDetailResponse(StrictModel):
    invoice: InvoiceResponse
    lines: list[InvoiceLineResponse]


class CloseAndIssueCycleResponse(StrictModel):
    issued: bool
    cycle: BillingCycleResponse
    invoice: InvoiceResponse
    lines: list[InvoiceLineResponse]


class DunningActionResponse(StrictModel):
    id: str
    invoice_id: str
    action: Literal["mark_overdue", "apply_hold", "clear_hold"]
    stage: int
    occurred_at: datetime


class DunningRunResponse(StrictModel):
    created: bool
    run_id: str
    idempotency_key: str
    intent_sha256: str
    company_id: str
    as_of: datetime
    status: Literal["completed"]
    scanned_count: int
    overdue_count: int
    hold_count: int
    current_billing_hold: bool
    current_billing_hold_reason: str | None
    current_billing_hold_since: datetime | None
    current_dunning_level: int
    started_at: datetime
    completed_at: datetime
    actions: list[DunningActionResponse]


def _utc_output(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _contract_payload(contract: CompanyBillingContractVersion) -> dict:
    return {
        "id": contract.id,
        "company_id": contract.company_id,
        "status": contract.status.value,
        "contract_reference": contract.contract_reference,
        "currency": contract.currency,
        "timezone_name": contract.timezone_name,
        "cycle_day": contract.cycle_day,
        "payment_terms_days": contract.payment_terms_days,
        "credit_limit_points": contract.credit_limit_points,
        "receivable_per_point_cents": contract.receivable_per_point_cents,
        "supersedes_version_id": contract.supersedes_version_id,
        "effective_at": _utc_output(contract.effective_at),
        "expires_at": _utc_output(contract.expires_at),
        "created_by_user_id": contract.created_by_user_id,
        "created_at": _utc_output(contract.created_at),
    }


def _account_payload(account: CompanyBillingAccount) -> dict:
    return {
        "company_id": account.company_id,
        "active_contract_version_id": account.active_contract_version_id,
        "unbilled_receivable_cents": account.unbilled_receivable_cents,
        "billing_hold": account.billing_hold,
        "billing_hold_reason": account.billing_hold_reason,
        "billing_hold_since": _utc_output(account.billing_hold_since),
        "dunning_level": account.dunning_level,
        "created_at": _utc_output(account.created_at),
        "updated_at": _utc_output(account.updated_at),
    }


def _lot_payload(lot: CompanyPointLot) -> dict:
    return {
        "id": lot.id,
        "company_id": lot.company_id,
        "source_kind": lot.source_kind.value,
        "contract_version_id": lot.contract_version_id,
        "billing_cycle_id": lot.billing_cycle_id,
        "original_points": lot.original_points,
        "available_points": lot.available_points,
        "reserved_points": lot.reserved_points,
        "reversal_reserved_points": lot.reversal_reserved_points,
        "settled_points": lot.settled_points,
        "reversed_points": lot.reversed_points,
        "cash_basis_cents": lot.cash_basis_cents,
        "receivable_basis_cents": lot.receivable_basis_cents,
        "subsidy_cents": lot.subsidy_cents,
        "expires_at": _utc_output(lot.expires_at),
        "created_at": _utc_output(lot.created_at),
    }


def _cycle_payload(cycle: CompanyBillingCycle, *, invoice_id: str | None) -> dict:
    return {
        "id": cycle.id,
        "company_id": cycle.company_id,
        "contract_version_id": cycle.contract_version_id,
        "period_start": _utc_output(cycle.period_start),
        "period_end": _utc_output(cycle.period_end),
        "status": cycle.status.value,
        "frozen_at": _utc_output(cycle.frozen_at),
        "invoice_id": invoice_id,
        "created_at": _utc_output(cycle.created_at),
        "updated_at": _utc_output(cycle.updated_at),
    }


def _invoice_payload(invoice: CompanyInvoice) -> dict:
    outstanding = invoice.total_cents - invoice.paid_cents
    if outstanding < 0:
        raise ConflictError("企业发票已付金额超过发票总额")
    return {
        "id": invoice.id,
        "company_id": invoice.company_id,
        "cycle_id": invoice.cycle_id,
        "invoice_number": invoice.invoice_number,
        "status": invoice.status.value,
        "currency": invoice.currency,
        "subtotal_cents": invoice.subtotal_cents,
        "credit_cents": invoice.credit_cents,
        "tax_cents": invoice.tax_cents,
        "total_cents": invoice.total_cents,
        "paid_cents": invoice.paid_cents,
        "outstanding_cents": outstanding,
        "issued_at": _utc_output(invoice.issued_at),
        "due_at": _utc_output(invoice.due_at),
        "created_at": _utc_output(invoice.created_at),
        "updated_at": _utc_output(invoice.updated_at),
    }


def _line_payload(line: CompanyInvoiceLine) -> dict:
    return {
        "id": line.id,
        "invoice_id": line.invoice_id,
        "task_id": line.task_id,
        "value_allocation_id": line.value_allocation_id,
        "contract_version_id": line.contract_version_id,
        "points": line.points,
        "amount_cents": line.amount_cents,
        "created_at": _utc_output(line.created_at),
    }


@router.get(
    "/api/v1/companies/{company_id}/billing/cycles",
    response_model=BillingCyclePage,
)
def list_billing_cycles(
    company_id: str,
    _: Annotated[TenantContext, Depends(require_permission("billing.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    total = int(
        session.scalar(
            select(func.count(CompanyBillingCycle.id)).where(
                CompanyBillingCycle.company_id == company_id
            )
        )
        or 0
    )
    rows = session.execute(
        select(CompanyBillingCycle, CompanyInvoice.id)
        .outerjoin(CompanyInvoice, CompanyInvoice.cycle_id == CompanyBillingCycle.id)
        .where(CompanyBillingCycle.company_id == company_id)
        .order_by(CompanyBillingCycle.period_start.desc(), CompanyBillingCycle.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [
            _cycle_payload(cycle, invoice_id=invoice_id)
            for cycle, invoice_id in rows
        ],
    }


@router.get(
    "/api/v1/companies/{company_id}/billing/invoices",
    response_model=InvoicePage,
)
def list_invoices(
    company_id: str,
    _: Annotated[TenantContext, Depends(require_permission("billing.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    total = int(
        session.scalar(
            select(func.count(CompanyInvoice.id)).where(
                CompanyInvoice.company_id == company_id
            )
        )
        or 0
    )
    invoices = session.scalars(
        select(CompanyInvoice)
        .where(CompanyInvoice.company_id == company_id)
        .order_by(CompanyInvoice.created_at.desc(), CompanyInvoice.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [_invoice_payload(invoice) for invoice in invoices],
    }


@router.get(
    "/api/v1/companies/{company_id}/billing/invoices/{invoice_id}",
    response_model=InvoiceDetailResponse,
)
def get_invoice_detail(
    company_id: str,
    invoice_id: str,
    _: Annotated[TenantContext, Depends(require_permission("billing.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    invoice = session.scalar(
        select(CompanyInvoice).where(
            CompanyInvoice.id == invoice_id,
            CompanyInvoice.company_id == company_id,
        )
    )
    if invoice is None:
        raise NotFoundError("企业发票不存在")
    lines = session.scalars(
        select(CompanyInvoiceLine)
        .where(CompanyInvoiceLine.invoice_id == invoice.id)
        .order_by(CompanyInvoiceLine.created_at, CompanyInvoiceLine.id)
    ).all()
    return {
        "invoice": _invoice_payload(invoice),
        "lines": [_line_payload(line) for line in lines],
    }


@router.post(
    "/internal/billing/enterprise/contracts/activate",
    response_model=ContractActivationResponse,
)
def activate_contract(
    body: ActivateContractRequest,
    _: Annotated[None, Depends(require_internal_service)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    contract, account, created = EnterpriseBillingService.activate_contract(
        session,
        **body.model_dump(),
    )
    return {
        "created": created,
        "contract": _contract_payload(contract),
        "account": _account_payload(account),
    }


@router.post(
    "/internal/billing/enterprise/cycles/open",
    response_model=OpenCycleResponse,
)
def open_cycle(
    body: OpenCycleRequest,
    _: Annotated[None, Depends(require_internal_service)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    cycle, lot, created = EnterpriseBillingService.open_cycle(
        session,
        **body.model_dump(),
    )
    return {
        "created": created,
        "cycle": _cycle_payload(cycle, invoice_id=None),
        "credit_lot": _lot_payload(lot) if lot is not None else None,
    }


@router.post(
    "/internal/billing/enterprise/cycles/{cycle_id}/close-and-issue",
    response_model=CloseAndIssueCycleResponse,
)
def close_and_issue_cycle(
    cycle_id: Annotated[str, Path(min_length=1, max_length=36)],
    body: CloseAndIssueCycleRequest,
    _: Annotated[None, Depends(require_internal_service)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    cycle, invoice, lines, issued = EnterpriseBillingService.close_and_issue_cycle(
        session,
        company_id=body.company_id,
        cycle_id=cycle_id,
        issued_at=body.issued_at,
    )
    return {
        "issued": issued,
        "cycle": _cycle_payload(cycle, invoice_id=invoice.id),
        "invoice": _invoice_payload(invoice),
        "lines": [_line_payload(line) for line in lines],
    }


@router.post(
    "/internal/billing/enterprise/run-once",
    response_model=RunEnterpriseOnceResponse,
)
def run_enterprise_once(
    body: RunEnterpriseOnceRequest,
    _: Annotated[None, Depends(require_internal_service)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    return EnterpriseBillingService.run_once(session)


@router.post(
    "/internal/billing/enterprise/dunning/run",
    response_model=DunningRunResponse,
)
def run_dunning(
    body: RunDunningRequest,
    _: Annotated[None, Depends(require_internal_service)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    run, actions, created = EnterpriseBillingService.run_dunning(
        session,
        **body.model_dump(),
    )
    account = session.get(CompanyBillingAccount, body.company_id)
    if account is None:
        raise ConflictError("企业月结账户不存在")
    if run.company_id is None or run.completed_at is None:
        raise ConflictError("企业催收运行缺少完成投影")
    return {
        "created": created,
        "run_id": run.id,
        "idempotency_key": run.idempotency_key,
        "intent_sha256": run.intent_sha256,
        "company_id": run.company_id,
        "as_of": _utc_output(run.as_of),
        "status": run.status.value,
        "scanned_count": run.scanned_count,
        "overdue_count": run.overdue_count,
        "hold_count": run.hold_count,
        "current_billing_hold": account.billing_hold,
        "current_billing_hold_reason": account.billing_hold_reason,
        "current_billing_hold_since": _utc_output(account.billing_hold_since),
        "current_dunning_level": account.dunning_level,
        "started_at": _utc_output(run.started_at),
        "completed_at": _utc_output(run.completed_at),
        "actions": [
            {
                "id": action.id,
                "invoice_id": action.invoice_id,
                "action": action.action,
                "stage": action.stage,
                "occurred_at": _utc_output(action.occurred_at),
            }
            for action in actions
        ],
    }
