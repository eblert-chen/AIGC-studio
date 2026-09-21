from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..dependencies import (
    TenantContext,
    UserContext,
    get_db,
    get_user_context,
    require_internal_service,
    require_permission,
)
from ..models import (
    AutoRechargeRule,
    PaymentMandate,
    PaymentOrder,
    PaymentProviderCommand,
    PaymentProviderCommandStatus,
    PaymentRefund,
    ProductContext,
    User,
    utcnow,
)
from ..payment_providers import PaymentProviderUnavailableError
from ..request_body import read_limited_request_body
from ..services.commercial_payments import CommercialPaymentService
from ..services.personal import PersonalWorkspaceService
from ..services.reservation_recovery import ReservationRecoveryService
from ..services.payment_webhooks import (
    MAX_PAYMENT_WEBHOOK_BODY_BYTES,
    PaymentWebhookVerificationError,
)


router = APIRouter(tags=["commercial-billing"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreatePointPaymentOrderRequest(StrictModel):
    points: int = Field(strict=True, gt=0, le=900_000_000_000_000)
    provider: str = Field(pattern=r"^[a-z][a-z0-9._-]{0,63}$")
    merchant_account: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=120)


class CreateInvoicePaymentOrderRequest(StrictModel):
    amount_cents: int = Field(strict=True, gt=0, le=9_000_000_000_000_000)
    provider: str = Field(pattern=r"^[a-z][a-z0-9._-]{0,63}$")
    merchant_account: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=120)


class PaymentOrderResponse(StrictModel):
    id: str
    scope: Literal["personal", "company"]
    purpose: str
    provider: str
    merchant_account: str
    status: str
    currency: Literal["CNY"]
    amount_cents: int
    points: int
    captured_amount_cents: int
    refunded_amount_cents: int
    disputed_amount_cents: int
    automatic: bool
    checkout_url: str | None
    expires_at: datetime | None
    captured_at: datetime | None
    created_at: datetime


class CreateRefundRequest(StrictModel):
    amount_cents: int = Field(strict=True, gt=0, le=9_000_000_000_000_000)
    reason: str = Field(min_length=1, max_length=240)
    idempotency_key: str = Field(min_length=8, max_length=120)


class PaymentRefundResponse(StrictModel):
    id: str
    order_id: str
    status: str
    amount_cents: int
    points: int
    currency: Literal["CNY"]
    reason: str
    created_at: datetime
    completed_at: datetime | None


class AutoRechargeRequest(StrictModel):
    mandate_id: str = Field(min_length=36, max_length=36)
    threshold_points: int = Field(strict=True, ge=0, le=9_000_000_000_000_000)
    top_up_points: int = Field(strict=True, gt=0, le=900_000_000_000_000)
    monthly_cap_cents: int = Field(strict=True, gt=0, le=9_000_000_000_000_000)
    cooldown_seconds: int = Field(strict=True, default=3600, ge=60, le=2_592_000)
    enabled: bool = Field(strict=True)


class CreatePaymentMandateRequest(StrictModel):
    provider: str = Field(pattern=r"^[a-z][a-z0-9._-]{0,63}$")
    merchant_account: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
    consent_version: str = Field(min_length=1, max_length=80)
    consent_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    consented_at: AwareDatetime
    idempotency_key: str = Field(min_length=8, max_length=160)


class ActivatePaymentMandateRequest(StrictModel):
    provider: str = Field(pattern=r"^[a-z][a-z0-9._-]{0,63}$")
    merchant_account: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
    consent_version: str = Field(min_length=1, max_length=80)
    consent_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_customer_reference: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    provider_payment_method_reference: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    provider_mandate_reference: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    verified_at: AwareDatetime


class PaymentMandateResponse(StrictModel):
    id: str
    scope: Literal["personal", "company"]
    provider: str
    merchant_account: str
    status: str
    consent_version: str
    consent_sha256: str
    consented_at: datetime | None
    verified_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class AutoRechargeResponse(StrictModel):
    id: str
    scope: Literal["personal", "company"]
    provider: str
    merchant_account: str
    mandate_id: str
    threshold_points: int
    top_up_points: int
    monthly_cap_cents: int
    cooldown_seconds: int
    enabled: bool
    last_attempt_at: datetime | None


class PaymentWebhookResponse(StrictModel):
    inbox_id: str
    inbox_status: str
    receipt_id: str | None
    event_id: str
    event_type: str
    outcome: str | None
    error_code: str | None


class AutoRechargeRunResponse(StrictModel):
    processed: bool
    rule_id: str | None
    payment_order_id: str | None
    payment_order_status: str | None


class ReservationRecoveryItem(StrictModel):
    task_id: str
    outcome: str


class ReservationRecoveryFailure(StrictModel):
    task_id: str
    reason: str


class ReservationRecoveryRunResponse(StrictModel):
    processed: bool
    scanned: int
    recovered: list[ReservationRecoveryItem]
    failed: list[ReservationRecoveryFailure]


class ProviderCommandRunResponse(StrictModel):
    processed: bool
    command_id: str | None
    operation: str | None
    command_status: str | None
    order_id: str | None
    refund_id: str | None


class WebhookInboxRunResponse(StrictModel):
    processed: bool
    inbox_id: str | None
    inbox_status: str | None
    receipt_id: str | None
    error_code: str | None


def _response_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _order_payload(order: PaymentOrder) -> dict:
    return {
        "id": order.id,
        "scope": "company" if order.company_id is not None else "personal",
        "purpose": order.purpose.value,
        "provider": order.provider,
        "merchant_account": order.merchant_account,
        "status": order.status.value,
        "currency": order.currency,
        "amount_cents": order.amount_cents,
        "points": order.points,
        "captured_amount_cents": order.captured_amount_cents,
        "refunded_amount_cents": order.refunded_amount_cents,
        "disputed_amount_cents": order.disputed_amount_cents,
        "automatic": order.automatic,
        "checkout_url": order.checkout_url,
        "expires_at": _response_utc(order.expires_at),
        "captured_at": _response_utc(order.captured_at),
        "created_at": _response_utc(order.created_at),
    }


def _refund_payload(refund: PaymentRefund) -> dict:
    return {
        "id": refund.id,
        "order_id": refund.order_id,
        "status": refund.status.value,
        "amount_cents": refund.amount_cents,
        "points": refund.points,
        "currency": refund.currency,
        "reason": refund.reason,
        "created_at": _response_utc(refund.created_at),
        "completed_at": _response_utc(refund.completed_at),
    }


def _auto_payload(rule: AutoRechargeRule) -> dict:
    return {
        "id": rule.id,
        "scope": "company" if rule.company_id is not None else "personal",
        "provider": rule.provider,
        "merchant_account": rule.merchant_account,
        "mandate_id": rule.mandate_id,
        "threshold_points": rule.threshold_points,
        "top_up_points": rule.top_up_points,
        "monthly_cap_cents": rule.monthly_cap_cents,
        "cooldown_seconds": rule.cooldown_seconds,
        "enabled": rule.enabled,
        "last_attempt_at": _response_utc(rule.last_attempt_at),
    }


def _mandate_payload(mandate: PaymentMandate) -> dict:
    return {
        "id": mandate.id,
        "scope": "company" if mandate.company_id is not None else "personal",
        "provider": mandate.provider,
        "merchant_account": mandate.merchant_account,
        "status": mandate.status.value,
        "consent_version": mandate.consent_version,
        "consent_sha256": mandate.consent_sha256,
        "consented_at": _response_utc(mandate.consented_at),
        "verified_at": _response_utc(mandate.verified_at),
        "revoked_at": _response_utc(mandate.revoked_at),
        "created_at": _response_utc(mandate.created_at),
    }


def _personal_workspace(session: Session, context: UserContext):
    active_context = context.active_product_context
    if active_context is None:
        user = session.get(User, context.user_id)
        if user is not None and user.account_type.value == "personal":
            active_context = ProductContext.PERSONAL
    return PersonalWorkspaceService.require_for_product_context(
        session,
        user_id=context.user_id,
        active_product_context=active_context,
        external_identity_id=context.external_identity_id,
    )


def _configured_provider(request: Request, *, provider: str, merchant_account: str):
    try:
        return request.app.state.payment_provider_registry.resolve(
            provider,
            merchant_account,
        )
    except PaymentProviderUnavailableError:
        raise HTTPException(status_code=503, detail="支付通道或商户未配置") from None


def _scoped_order(
    session: Session,
    *,
    order_id: str,
    company_id: str | None = None,
    workspace_id: str | None = None,
) -> PaymentOrder:
    scope = CommercialPaymentService._scope_filter(
        company_id=company_id, workspace_id=workspace_id
    )
    order = session.scalar(
        select(PaymentOrder).where(PaymentOrder.id == order_id, scope)
    )
    if order is None:
        raise HTTPException(status_code=404, detail="支付订单不存在")
    return order


def _scoped_refund(session: Session, *, order: PaymentOrder, refund_id: str) -> PaymentRefund:
    refund = session.scalar(
        select(PaymentRefund).where(
            PaymentRefund.id == refund_id, PaymentRefund.order_id == order.id
        )
    )
    if refund is None:
        raise HTTPException(status_code=404, detail="退款不存在")
    return refund


@router.get(
    "/api/v1/personal/payment-orders/{order_id}",
    response_model=PaymentOrderResponse,
)
def get_personal_payment_order(
    order_id: str,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    workspace = _personal_workspace(session, context)
    return _order_payload(_scoped_order(session, order_id=order_id, workspace_id=workspace.id))


@router.get(
    "/api/v1/companies/{company_id}/payment-orders/{order_id}",
    response_model=PaymentOrderResponse,
)
def get_company_payment_order(
    company_id: str,
    order_id: str,
    context: Annotated[TenantContext, Depends(require_permission("billing.read"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    return _order_payload(_scoped_order(session, order_id=order_id, company_id=company_id))


@router.get(
    "/api/v1/personal/payment-orders/{order_id}/refunds/{refund_id}",
    response_model=PaymentRefundResponse,
)
def get_personal_payment_refund(
    order_id: str,
    refund_id: str,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    workspace = _personal_workspace(session, context)
    order = _scoped_order(session, order_id=order_id, workspace_id=workspace.id)
    return _refund_payload(_scoped_refund(session, order=order, refund_id=refund_id))


@router.get(
    "/api/v1/companies/{company_id}/payment-orders/{order_id}/refunds/{refund_id}",
    response_model=PaymentRefundResponse,
)
def get_company_payment_refund(
    company_id: str,
    order_id: str,
    refund_id: str,
    context: Annotated[TenantContext, Depends(require_permission("billing.read"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    order = _scoped_order(session, order_id=order_id, company_id=company_id)
    return _refund_payload(_scoped_refund(session, order=order, refund_id=refund_id))


@router.post(
    "/api/v1/personal/payment-orders",
    response_model=PaymentOrderResponse,
    status_code=202,
)
async def create_personal_payment_order(
    request: Request,
    body: CreatePointPaymentOrderRequest,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    workspace = _personal_workspace(session, context)
    _configured_provider(
        request, provider=body.provider, merchant_account=body.merchant_account
    )
    order, _ = CommercialPaymentService.create_point_order(
        session,
        company_id=None,
        workspace_id=workspace.id,
        user_id=context.user_id,
        **body.model_dump(),
    )
    session.commit()
    return _order_payload(order)


@router.post(
    "/api/v1/companies/{company_id}/payment-orders",
    response_model=PaymentOrderResponse,
    status_code=202,
)
async def create_company_payment_order(
    request: Request,
    company_id: str,
    body: CreatePointPaymentOrderRequest,
    context: Annotated[TenantContext, Depends(require_permission("billing.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    _configured_provider(
        request, provider=body.provider, merchant_account=body.merchant_account
    )
    order, _ = CommercialPaymentService.create_point_order(
        session,
        company_id=company_id,
        workspace_id=None,
        user_id=context.user_id,
        **body.model_dump(),
    )
    session.commit()
    return _order_payload(order)


@router.post(
    "/api/v1/companies/{company_id}/invoices/{invoice_id}/payment-orders",
    response_model=PaymentOrderResponse,
    status_code=202,
)
async def create_company_invoice_payment_order(
    request: Request,
    company_id: str,
    invoice_id: str,
    body: CreateInvoicePaymentOrderRequest,
    context: Annotated[TenantContext, Depends(require_permission("billing.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    _configured_provider(
        request, provider=body.provider, merchant_account=body.merchant_account
    )
    order, _ = CommercialPaymentService.create_invoice_payment_order(
        session,
        company_id=company_id,
        invoice_id=invoice_id,
        user_id=context.user_id,
        **body.model_dump(),
    )
    session.commit()
    return _order_payload(order)


@router.post(
    "/api/v1/personal/payment-orders/{order_id}/refunds",
    response_model=PaymentRefundResponse,
    status_code=202,
)
async def refund_personal_payment_order(
    request: Request,
    order_id: str,
    body: CreateRefundRequest,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    workspace = _personal_workspace(session, context)
    order = session.get(PaymentOrder, order_id)
    if order is None or order.personal_workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="支付订单不存在")
    _configured_provider(
        request, provider=order.provider, merchant_account=order.merchant_account
    )
    refund, _ = CommercialPaymentService.request_refund(
        session,
        order_id=order_id,
        user_id=context.user_id,
        **body.model_dump(),
    )
    session.commit()
    return _refund_payload(refund)


@router.post(
    "/api/v1/companies/{company_id}/payment-orders/{order_id}/refunds",
    response_model=PaymentRefundResponse,
    status_code=202,
)
async def refund_company_payment_order(
    request: Request,
    company_id: str,
    order_id: str,
    body: CreateRefundRequest,
    context: Annotated[TenantContext, Depends(require_permission("billing.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    order = session.get(PaymentOrder, order_id)
    if order is None or order.company_id != company_id:
        raise HTTPException(status_code=404, detail="支付订单不存在")
    _configured_provider(
        request, provider=order.provider, merchant_account=order.merchant_account
    )
    refund, _ = CommercialPaymentService.request_refund(
        session,
        order_id=order_id,
        user_id=context.user_id,
        **body.model_dump(),
    )
    session.commit()
    return _refund_payload(refund)


@router.put(
    "/api/v1/personal/auto-recharge",
    response_model=AutoRechargeResponse,
)
def configure_personal_auto_recharge(
    request: Request,
    body: AutoRechargeRequest,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    workspace = _personal_workspace(session, context)
    rule = CommercialPaymentService.configure_auto_recharge(
        session,
        company_id=None,
        workspace_id=workspace.id,
        user_id=context.user_id,
        **body.model_dump(),
    )
    _configured_provider(
        request, provider=rule.provider, merchant_account=rule.merchant_account
    )
    return _auto_payload(rule)


@router.put(
    "/api/v1/companies/{company_id}/auto-recharge",
    response_model=AutoRechargeResponse,
)
def configure_company_auto_recharge(
    request: Request,
    company_id: str,
    body: AutoRechargeRequest,
    context: Annotated[TenantContext, Depends(require_permission("billing.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    rule = CommercialPaymentService.configure_auto_recharge(
        session,
        company_id=company_id,
        workspace_id=None,
        user_id=context.user_id,
        **body.model_dump(),
    )
    _configured_provider(
        request, provider=rule.provider, merchant_account=rule.merchant_account
    )
    return _auto_payload(rule)


@router.post(
    "/api/v1/personal/payment-mandates",
    response_model=PaymentMandateResponse,
    status_code=201,
)
def create_personal_payment_mandate(
    request: Request,
    body: CreatePaymentMandateRequest,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    workspace = _personal_workspace(session, context)
    _configured_provider(
        request, provider=body.provider, merchant_account=body.merchant_account
    )
    mandate, _ = CommercialPaymentService.create_pending_payment_mandate(
        session,
        company_id=None,
        workspace_id=workspace.id,
        user_id=context.user_id,
        **body.model_dump(),
    )
    return _mandate_payload(mandate)


@router.post(
    "/api/v1/companies/{company_id}/payment-mandates",
    response_model=PaymentMandateResponse,
    status_code=201,
)
def create_company_payment_mandate(
    request: Request,
    company_id: str,
    body: CreatePaymentMandateRequest,
    context: Annotated[TenantContext, Depends(require_permission("billing.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    _configured_provider(
        request, provider=body.provider, merchant_account=body.merchant_account
    )
    mandate, _ = CommercialPaymentService.create_pending_payment_mandate(
        session,
        company_id=company_id,
        workspace_id=None,
        user_id=context.user_id,
        **body.model_dump(),
    )
    return _mandate_payload(mandate)


@router.post(
    "/internal/billing/payment-mandates/{mandate_id}/activate",
    response_model=PaymentMandateResponse,
)
def activate_verified_payment_mandate(
    request: Request,
    mandate_id: str,
    body: ActivatePaymentMandateRequest,
    _: Annotated[None, Depends(require_internal_service)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    """Adapter contract: call only after verifying the PSP setup/consent event."""

    _configured_provider(
        request, provider=body.provider, merchant_account=body.merchant_account
    )
    mandate, _ = CommercialPaymentService.activate_payment_mandate(
        session,
        mandate_id=mandate_id,
        **body.model_dump(),
    )
    return _mandate_payload(mandate)


@router.post(
    "/api/v1/personal/payment-mandates/{mandate_id}/revoke",
    response_model=PaymentMandateResponse,
)
def revoke_personal_payment_mandate(
    mandate_id: str,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    workspace = _personal_workspace(session, context)
    mandate, _ = CommercialPaymentService.revoke_payment_mandate(
        session,
        mandate_id=mandate_id,
        company_id=None,
        workspace_id=workspace.id,
    )
    return _mandate_payload(mandate)


@router.post(
    "/api/v1/companies/{company_id}/payment-mandates/{mandate_id}/revoke",
    response_model=PaymentMandateResponse,
)
def revoke_company_payment_mandate(
    company_id: str,
    mandate_id: str,
    context: Annotated[TenantContext, Depends(require_permission("billing.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    mandate, _ = CommercialPaymentService.revoke_payment_mandate(
        session,
        mandate_id=mandate_id,
        company_id=company_id,
        workspace_id=None,
    )
    return _mandate_payload(mandate)


@router.post(
    "/internal/payment-webhooks/{provider}",
    response_model=PaymentWebhookResponse,
)
async def payment_webhook(
    request: Request,
    response: Response,
    provider: Annotated[str, Path(pattern=r"^[a-z][a-z0-9._-]{0,63}$")],
    session: Annotated[Session, Depends(get_db, scope="function")],
    x_payment_key_id: Annotated[str | None, Header(alias="X-Payment-Key-ID")] = None,
    x_payment_event_id: Annotated[str | None, Header(alias="X-Payment-Event-ID")] = None,
    x_payment_timestamp: Annotated[str | None, Header(alias="X-Payment-Timestamp")] = None,
    x_payment_signature: Annotated[str | None, Header(alias="X-Payment-Signature")] = None,
):
    raw_body = await read_limited_request_body(
        request, max_bytes=MAX_PAYMENT_WEBHOOK_BODY_BYTES
    )
    registry = request.app.state.payment_webhook_verifier_registry
    try:
        event, evidence = registry.verify(
            raw_body,
            provider=provider,
            key_id=x_payment_key_id,
            event_id=x_payment_event_id,
            timestamp=x_payment_timestamp,
            signature=x_payment_signature,
        )
    except PaymentWebhookVerificationError:
        raise
    inbox = CommercialPaymentService.ingest_webhook_event(
        session,
        event=event,
        evidence=evidence,
    )
    inbox_id = inbox.id
    session.commit()
    inbox, receipt = CommercialPaymentService.process_inbox_event(
        session,
        inbox_id=inbox_id,
    )
    if receipt is None:
        response.status_code = 202
    return {
        "inbox_id": inbox.id,
        "inbox_status": inbox.status.value,
        "receipt_id": receipt.id if receipt is not None else None,
        "event_id": inbox.provider_event_id,
        "event_type": inbox.event_type,
        "outcome": receipt.outcome.value if receipt is not None else None,
        "error_code": (
            receipt.error_code if receipt is not None else inbox.last_error_code
        ),
    }


@router.post(
    "/internal/billing/reservation-recovery/run-once",
    response_model=ReservationRecoveryRunResponse,
)
def run_reservation_recovery_once(
    _: Annotated[None, Depends(require_internal_service)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    """Close reservations left behind by tasks that already reached a terminal status."""
    return ReservationRecoveryService.run_once(session)


@router.post(
    "/internal/billing/auto-recharge/run-once",
    response_model=AutoRechargeRunResponse,
)
async def run_auto_recharge_once(
    request: Request,
    _: Annotated[None, Depends(require_internal_service)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    rules = list(session.scalars(
        select(AutoRechargeRule)
        .where(
            AutoRechargeRule.enabled.is_(True),
            or_(AutoRechargeRule.next_check_at.is_(None), AutoRechargeRule.next_check_at <= utcnow()),
        )
        .order_by(AutoRechargeRule.next_check_at.asc().nulls_first(), AutoRechargeRule.id)
        .limit(100)
    ).all())
    if not rules:
        return AutoRechargeRunResponse(
            processed=False,
            rule_id=None,
            payment_order_id=None,
            payment_order_status=None,
        )
    for rule in rules:
        order, created = CommercialPaymentService.trigger_auto_recharge(
            session,
            rule_id=rule.id,
            user_id=rule.created_by_user_id,
            scheduler_check=True,
        )
        # Commit even an idle check so the next invocation advances beyond this
        # bounded batch without changing the semantic last charge attempt.
        session.commit()
        if order is None or not created:
            continue
        return AutoRechargeRunResponse(
            processed=True,
            rule_id=rule.id,
            payment_order_id=order.id,
            payment_order_status=order.status.value,
        )
    return AutoRechargeRunResponse(
        processed=False,
        rule_id=None,
        payment_order_id=None,
        payment_order_status=None,
    )


@router.post(
    "/internal/billing/payment-provider-commands/run-once",
    response_model=ProviderCommandRunResponse,
)
async def run_payment_provider_command_once(
    request: Request,
    _: Annotated[None, Depends(require_internal_service)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    command = CommercialPaymentService.claim_next_provider_command(session)
    if command is None:
        return ProviderCommandRunResponse(
            processed=False,
            command_id=None,
            operation=None,
            command_status=None,
            order_id=None,
            refund_id=None,
        )
    command_id = command.id
    operation = command.operation
    order_id = command.order_id
    refund_id = command.refund_id
    lease_token = command.lease_token
    assert lease_token is not None
    try:
        provider = request.app.state.payment_provider_registry.resolve(
            command.provider,
            command.merchant_account,
        )
    except PaymentProviderUnavailableError:
        command.status = PaymentProviderCommandStatus.PENDING
        command.lease_token = None
        command.lease_expires_at = None
        command.last_error_code = "provider_unavailable"
        session.commit()
        raise HTTPException(status_code=503, detail="支付通道未配置") from None
    # The durable lease is committed before any external network call.
    session.commit()
    await CommercialPaymentService.execute_claimed_provider_command(
        session,
        command_id=command_id,
        lease_token=lease_token,
        provider=provider,
    )
    refreshed = session.get(PaymentProviderCommand, command_id)
    return ProviderCommandRunResponse(
        processed=True,
        command_id=command_id,
        operation=operation.value,
        command_status=refreshed.status.value if refreshed is not None else None,
        order_id=order_id,
        refund_id=refund_id,
    )


@router.post(
    "/internal/billing/payment-webhook-inbox/run-once",
    response_model=WebhookInboxRunResponse,
)
def run_payment_webhook_inbox_once(
    _: Annotated[None, Depends(require_internal_service)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    inbox = CommercialPaymentService.claim_next_webhook_inbox_event(session)
    if inbox is None:
        return WebhookInboxRunResponse(
            processed=False,
            inbox_id=None,
            inbox_status=None,
            receipt_id=None,
            error_code=None,
        )
    inbox, receipt = CommercialPaymentService.process_inbox_event(
        session,
        inbox_id=inbox.id,
    )
    return WebhookInboxRunResponse(
        processed=True,
        inbox_id=inbox.id,
        inbox_status=inbox.status.value,
        receipt_id=receipt.id if receipt is not None else None,
        error_code=receipt.error_code if receipt is not None else inbox.last_error_code,
    )
