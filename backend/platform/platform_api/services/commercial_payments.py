from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from ..models import (
    AutoRechargeExecution,
    AutoRechargeRule,
    Company,
    CompanyInvoice,
    CompanyPointLedgerEntry,
    CompanyPointLot,
    CompanyPointWalletAccount,
    LedgerKind,
    PaymentAttempt,
    PaymentAttemptStatus,
    PaymentDispute,
    PaymentDisputeDebtRecoveryAllocation,
    PaymentDisputeDebtRecoveryReversal,
    PaymentDisputeStatus,
    PaymentMandate,
    PaymentMandateStatus,
    PaymentOrder,
    PaymentOrderStatus,
    PaymentProviderCommand,
    PaymentProviderCommandOperation,
    PaymentProviderCommandStatus,
    PaymentPurpose,
    PaymentRefund,
    PaymentRefundStatus,
    PaymentTransaction,
    PaymentTransactionKind,
    PaymentWebhookOutcome,
    PaymentWebhookInboxEvent,
    PaymentWebhookInboxStatus,
    PaymentWebhookReceipt,
    PersonalLedgerEntry,
    PersonalPointLot,
    PersonalWalletAccount,
    PointLedgerKind,
    PointLotSourceKind,
    utcnow,
)
from ..payment_providers import (
    CreatePaymentRequest,
    CreatePaymentResult,
    CreateRefundRequest,
    CreateRefundResult,
    MAX_PAYMENT_AMOUNT_CENTS,
    PaymentProvider,
    PaymentProviderRequestError,
    QueryPaymentRequest,
    QueryPaymentResult,
    QueryRefundRequest,
    QueryRefundResult,
)
from .errors import ConflictError, DomainError, NotFoundError
from .enterprise_billing import EnterpriseBillingService
from .payment_webhooks import (
    DisputeLostEvent,
    DisputeOpenedEvent,
    DisputeWonEvent,
    PaymentCancelledEvent,
    PaymentCapturedEvent,
    PaymentExpiredEvent,
    PaymentFailedEvent,
    PaymentWebhookEvent,
    PaymentWebhookEvidence,
    RefundFailedEvent,
    RefundSucceededEvent,
    parse_payment_webhook_payload,
)
from .personal_billing import PersonalWalletService


POINT_VALUE_CENTS = 10
MAX_POINTS = 9_000_000_000_000_000
MAX_PURCHASE_POINTS = MAX_PAYMENT_AMOUNT_CENTS // POINT_VALUE_CENTS
_NONTERMINAL_ORDER_STATUSES = {
    PaymentOrderStatus.CREATED,
    PaymentOrderStatus.PENDING,
    PaymentOrderStatus.REQUIRES_ACTION,
    PaymentOrderStatus.RECONCILIATION_REQUIRED,
}
_UNRESOLVED_REFUND_STATUSES = {
    PaymentRefundStatus.REQUESTED,
    PaymentRefundStatus.PENDING,
    PaymentRefundStatus.RECONCILIATION_REQUIRED,
}


def _canonical_sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class CommercialPaymentService:
    """Cash-to-entitlement state machine.

    Provider calls never credit a wallet.  Only a verified capture webhook can
    append a payment transaction and fulfill points in the same database
    transaction.
    """

    @staticmethod
    def _locked_order(session: Session, order_id: str) -> PaymentOrder:
        # FOR UPDATE does not refresh an already loaded ORM object. Flush our
        # own writes first, then replace the snapshot after acquiring the lock.
        session.flush()
        order = session.scalar(
            select(PaymentOrder)
            .where(PaymentOrder.id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if order is None:
            raise NotFoundError("支付订单不存在")
        return order

    @staticmethod
    def _project_order_financial_status(order: PaymentOrder) -> PaymentOrderStatus:
        """Project one truthful display state from immutable cash exposures."""

        if order.status == PaymentOrderStatus.RECONCILIATION_REQUIRED:
            return order.status
        if order.disputed_amount_cents > 0:
            order.status = PaymentOrderStatus.DISPUTED
        elif (
            order.captured_amount_cents > 0
            and order.refunded_amount_cents == order.captured_amount_cents
        ):
            order.status = PaymentOrderStatus.REFUNDED
        elif order.refunded_amount_cents > 0:
            order.status = PaymentOrderStatus.PARTIALLY_REFUNDED
        elif order.captured_amount_cents > 0:
            order.status = PaymentOrderStatus.PAID
        return order.status

    @staticmethod
    def _unresolved_refund_amount(
        session: Session,
        *,
        order_id: str,
        exclude_refund_id: str | None = None,
    ) -> int:
        statement = select(
            func.coalesce(func.sum(PaymentRefund.amount_cents), 0)
        ).where(
            PaymentRefund.order_id == order_id,
            PaymentRefund.status.in_(_UNRESOLVED_REFUND_STATUSES),
        )
        if exclude_refund_id is not None:
            statement = statement.where(PaymentRefund.id != exclude_refund_id)
        return int(session.scalar(statement) or 0)

    @classmethod
    def _ensure_provider_command(
        cls,
        session: Session,
        *,
        operation: PaymentProviderCommandOperation,
        order: PaymentOrder,
        refund: PaymentRefund | None,
        request_payload: dict[str, Any],
        idempotency_key: str,
    ) -> PaymentProviderCommand:
        target_id = refund.id if refund is not None else order.id
        dedupe_key = f"{operation.value}:{target_id}"
        request_sha256 = _canonical_sha256(request_payload)
        existing = session.scalar(
            select(PaymentProviderCommand).where(
                PaymentProviderCommand.dedupe_key == dedupe_key
            )
        )
        if existing is not None:
            if (
                existing.operation != operation
                or existing.order_id != order.id
                or existing.refund_id != (refund.id if refund is not None else None)
                or existing.provider != order.provider
                or existing.merchant_account != order.merchant_account
                or existing.idempotency_key != idempotency_key
                or existing.request_sha256 != request_sha256
                or existing.request_payload != request_payload
            ):
                raise ConflictError("支付通道命令幂等身份不一致")
            return existing
        command = PaymentProviderCommand(
            operation=operation,
            order_id=order.id,
            refund_id=refund.id if refund is not None else None,
            provider=order.provider,
            merchant_account=order.merchant_account,
            idempotency_key=idempotency_key,
            dedupe_key=dedupe_key,
            request_payload=request_payload,
            request_sha256=request_sha256,
            status=PaymentProviderCommandStatus.PENDING,
            attempt_count=0,
            next_attempt_at=utcnow(),
        )
        session.add(command)
        session.flush()
        return command

    @classmethod
    def _ensure_create_payment_command(
        cls,
        session: Session,
        order: PaymentOrder,
    ) -> PaymentProviderCommand:
        existing = session.scalar(
            select(PaymentProviderCommand).where(
                PaymentProviderCommand.dedupe_key
                == f"{PaymentProviderCommandOperation.CREATE_PAYMENT.value}:{order.id}"
            )
        )
        if existing is not None:
            # The immutable command is the source of truth after creation.  In
            # particular, a later mandate revocation or rule edit must not
            # rewrite an already committed PSP request identity.
            return cls._ensure_provider_command(
                session,
                operation=PaymentProviderCommandOperation.CREATE_PAYMENT,
                order=order,
                refund=None,
                idempotency_key=f"payment:{order.id}:1",
                request_payload=existing.request_payload,
            )

        mandate: PaymentMandate | None = None
        if order.automatic:
            mandate = session.scalar(
                select(PaymentMandate)
                .where(PaymentMandate.id == order.payment_mandate_id)
                .with_for_update()
            )
            if (
                mandate is None
                or mandate.status != PaymentMandateStatus.ACTIVE
                or mandate.company_id != order.company_id
                or mandate.personal_workspace_id != order.personal_workspace_id
                or mandate.provider != order.provider
                or mandate.merchant_account != order.merchant_account
                or mandate.provider_customer_reference
                != order.provider_customer_reference
                or not mandate.provider_payment_method_reference
                or not mandate.provider_mandate_reference
                or mandate.consented_at is None
                or mandate.verified_at is None
                or mandate.revoked_at is not None
            ):
                raise ConflictError("自动支付订单未绑定有效扣款授权")
        return cls._ensure_provider_command(
            session,
            operation=PaymentProviderCommandOperation.CREATE_PAYMENT,
            order=order,
            refund=None,
            idempotency_key=f"payment:{order.id}:1",
            request_payload={
                "order_id": order.id,
                "amount_cents": order.amount_cents,
                "points": order.points,
                "currency": order.currency,
                "purpose": order.purpose.value,
                "off_session": order.automatic,
                "payment_mandate_id": order.payment_mandate_id,
                "provider_customer_reference": (
                    mandate.provider_customer_reference if mandate is not None else None
                ),
                "provider_payment_method_reference": (
                    mandate.provider_payment_method_reference
                    if mandate is not None
                    else None
                ),
                "provider_mandate_reference": (
                    mandate.provider_mandate_reference if mandate is not None else None
                ),
            },
        )

    @staticmethod
    def _create_payment_request_from_command(
        *,
        order: PaymentOrder,
        command: PaymentProviderCommand,
    ) -> CreatePaymentRequest:
        payload = command.request_payload
        expected = {
            "order_id": order.id,
            "amount_cents": order.amount_cents,
            "points": order.points,
            "currency": order.currency,
            "purpose": order.purpose.value,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise ConflictError("支付通道命令与订单资金事实不一致")
        off_session = payload.get("off_session", False)
        if not isinstance(off_session, bool) or off_session != order.automatic:
            raise ConflictError("支付通道命令的自动扣款身份不一致")
        payment_mandate_id = payload.get("payment_mandate_id")
        if payment_mandate_id != order.payment_mandate_id:
            # Legacy interactive commands may predate the explicit null field.
            if order.automatic or payment_mandate_id is not None:
                raise ConflictError("支付通道命令的扣款授权身份不一致")
        if off_session and payload.get("provider_customer_reference") != (
            order.provider_customer_reference
        ):
            raise ConflictError("支付通道命令的客户引用不一致")
        return CreatePaymentRequest(
            order_id=order.id,
            amount_cents=order.amount_cents,
            points=order.points,
            currency=order.currency,
            idempotency_key=command.idempotency_key,
            purpose=order.purpose.value,
            off_session=off_session,
            provider_customer_reference=payload.get(
                "provider_customer_reference"
            ),
            provider_payment_method_reference=payload.get(
                "provider_payment_method_reference"
            ),
            provider_mandate_reference=payload.get("provider_mandate_reference"),
        )

    @classmethod
    def _build_provider_request(
        cls,
        *,
        command: PaymentProviderCommand,
        order: PaymentOrder,
        refund: PaymentRefund | None,
    ) -> object:
        if (
            not isinstance(command.request_payload, dict)
            or _canonical_sha256(command.request_payload) != command.request_sha256
        ):
            raise ConflictError("支付通道命令请求摘要不一致")
        if (
            command.order_id != order.id
            or command.provider != order.provider
            or command.merchant_account != order.merchant_account
        ):
            raise ConflictError("支付通道命令与订单身份不一致")
        payload = command.request_payload
        if command.operation == PaymentProviderCommandOperation.CREATE_PAYMENT:
            return cls._create_payment_request_from_command(order=order, command=command)
        if command.operation == PaymentProviderCommandOperation.CREATE_REFUND:
            if (
                refund is None
                or command.refund_id != refund.id
                or refund.order_id != order.id
                or payload.get("refund_id") != refund.id
                or payload.get("order_id") != order.id
                or payload.get("provider_payment_id") != order.provider_order_id
                or payload.get("amount_cents") != refund.amount_cents
                or payload.get("currency") != refund.currency
            ):
                raise ConflictError("退款通道命令与退款事实不一致")
            return CreateRefundRequest(
                **payload,
                idempotency_key=command.idempotency_key,
            )
        create_idempotency_key = payload.get("create_idempotency_key")
        if (
            not isinstance(create_idempotency_key, str)
            or payload.get("order_id") != order.id
        ):
            raise ConflictError("支付查询命令缺少原始幂等身份")
        if command.operation == PaymentProviderCommandOperation.QUERY_PAYMENT:
            return QueryPaymentRequest(
                order_id=order.id,
                idempotency_key=create_idempotency_key,
                provider_payment_id=order.provider_order_id,
            )
        if (
            command.operation != PaymentProviderCommandOperation.QUERY_REFUND
            or refund is None
            or command.refund_id != refund.id
            or refund.order_id != order.id
            or payload.get("refund_id") != refund.id
            or not order.provider_order_id
        ):
            raise ConflictError("退款查询命令缺少原始支付事实")
        return QueryRefundRequest(
            refund_id=refund.id,
            order_id=order.id,
            provider_payment_id=order.provider_order_id,
            idempotency_key=create_idempotency_key,
            provider_refund_id=refund.provider_refund_id,
        )

    @classmethod
    def _ensure_create_refund_command(
        cls,
        session: Session,
        *,
        order: PaymentOrder,
        refund: PaymentRefund,
    ) -> PaymentProviderCommand:
        if not order.provider_order_id:
            raise ConflictError("退款订单缺少支付通道交易编号")
        return cls._ensure_provider_command(
            session,
            operation=PaymentProviderCommandOperation.CREATE_REFUND,
            order=order,
            refund=refund,
            idempotency_key=f"refund:{refund.id}",
            request_payload={
                "refund_id": refund.id,
                "order_id": order.id,
                "provider_payment_id": order.provider_order_id,
                "amount_cents": refund.amount_cents,
                "currency": refund.currency,
            },
        )

    @classmethod
    def _ensure_query_payment_command(
        cls,
        session: Session,
        order: PaymentOrder,
        *,
        rearm_terminal: bool = False,
    ) -> PaymentProviderCommand:
        create_command = cls._ensure_create_payment_command(session, order)
        command = cls._ensure_provider_command(
            session,
            operation=PaymentProviderCommandOperation.QUERY_PAYMENT,
            order=order,
            refund=None,
            idempotency_key=f"query:payment:{order.id}",
            request_payload={
                "order_id": order.id,
                "create_idempotency_key": create_command.idempotency_key,
            },
        )
        if rearm_terminal and command.status in {
            PaymentProviderCommandStatus.SUCCEEDED,
            PaymentProviderCommandStatus.FAILED,
            PaymentProviderCommandStatus.DEAD,
        }:
            cls._rearm_provider_command(command)
        return command

    @classmethod
    def _ensure_query_refund_command(
        cls,
        session: Session,
        *,
        order: PaymentOrder,
        refund: PaymentRefund,
        rearm_terminal: bool = False,
    ) -> PaymentProviderCommand:
        create_command = cls._ensure_create_refund_command(
            session,
            order=order,
            refund=refund,
        )
        command = cls._ensure_provider_command(
            session,
            operation=PaymentProviderCommandOperation.QUERY_REFUND,
            order=order,
            refund=refund,
            idempotency_key=f"query:refund:{refund.id}",
            request_payload={
                "refund_id": refund.id,
                "order_id": order.id,
                "create_idempotency_key": create_command.idempotency_key,
            },
        )
        if rearm_terminal and command.status in {
            PaymentProviderCommandStatus.SUCCEEDED,
            PaymentProviderCommandStatus.FAILED,
            PaymentProviderCommandStatus.DEAD,
        }:
            cls._rearm_provider_command(command)
        return command

    @staticmethod
    def _rearm_provider_command(command: PaymentProviderCommand) -> None:
        command.status = PaymentProviderCommandStatus.PENDING
        command.next_attempt_at = utcnow()
        command.lease_token = None
        command.lease_expires_at = None
        command.provider_resource_id = None
        command.response_sha256 = None
        command.last_error_code = None
        command.completed_at = None

    @staticmethod
    def _claim_provider_command(command: PaymentProviderCommand) -> None:
        if command.status != PaymentProviderCommandStatus.PENDING:
            raise ConflictError("支付通道命令不是可领取状态")
        command.status = PaymentProviderCommandStatus.CLAIMED
        command.attempt_count += 1
        command.lease_token = uuid4().hex
        command.lease_expires_at = utcnow() + timedelta(minutes=2)
        command.last_error_code = None

    @staticmethod
    def _finish_provider_command(
        command: PaymentProviderCommand,
        *,
        status: PaymentProviderCommandStatus,
        provider_resource_id: str | None = None,
        response_payload: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        command.status = status
        command.provider_resource_id = provider_resource_id
        command.response_sha256 = (
            _canonical_sha256(response_payload) if response_payload is not None else None
        )
        command.last_error_code = error_code
        command.lease_token = None
        command.lease_expires_at = None
        command.completed_at = (
            utcnow()
            if status
            in {
                PaymentProviderCommandStatus.SUCCEEDED,
                PaymentProviderCommandStatus.FAILED,
                PaymentProviderCommandStatus.DEAD,
            }
            else None
        )

    @staticmethod
    def _require_finalize_claim(
        command: PaymentProviderCommand,
        *,
        lease_token: str,
        operation: PaymentProviderCommandOperation,
        order: PaymentOrder,
    ) -> None:
        if (
            command.status != PaymentProviderCommandStatus.CLAIMED
            or command.lease_token != lease_token
            or command.operation != operation
            or command.order_id != order.id
            or command.provider != order.provider
            or command.merchant_account != order.merchant_account
        ):
            raise ConflictError("支付通道结果缺少有效已领取命令租约")

    @staticmethod
    def _provider_command_claim_statement(now: datetime):
        query_operations = {
            PaymentProviderCommandOperation.QUERY_PAYMENT,
            PaymentProviderCommandOperation.QUERY_REFUND,
        }
        return (
            select(PaymentProviderCommand)
            .where(
                or_(
                    (
                        PaymentProviderCommand.status.in_(
                            {
                                PaymentProviderCommandStatus.PENDING,
                                PaymentProviderCommandStatus.UNKNOWN,
                            }
                        )
                        & (PaymentProviderCommand.next_attempt_at <= now)
                    ),
                    (
                        PaymentProviderCommand.status
                        == PaymentProviderCommandStatus.CLAIMED
                    )
                    & (PaymentProviderCommand.lease_expires_at <= now),
                )
            )
            .order_by(
                case(
                    (PaymentProviderCommand.operation.in_(query_operations), 0),
                    else_=1,
                ),
                PaymentProviderCommand.next_attempt_at,
                PaymentProviderCommand.created_at,
                PaymentProviderCommand.id,
            )
            .limit(1)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )

    @classmethod
    def claim_next_provider_command(
        cls,
        session: Session,
    ) -> PaymentProviderCommand | None:
        """Claim one due outbox row with a PostgreSQL SKIP LOCKED gate.

        The caller must commit this short transaction before invoking
        :meth:`execute_claimed_provider_command`.
        """

        session.flush()
        now = utcnow()
        for _ in range(8):
            command = session.scalar(cls._provider_command_claim_statement(now))
            if command is None:
                return None
            order = cls._locked_order(session, command.order_id)
            if (
                command.operation == PaymentProviderCommandOperation.CREATE_PAYMENT
                and order.captured_amount_cents > 0
            ):
                cls._finish_provider_command(
                    command,
                    status=PaymentProviderCommandStatus.SUCCEEDED,
                    provider_resource_id=order.provider_order_id,
                    response_payload={"source": "verified_capture_already_applied"},
                )
                session.flush()
                continue
            if (
                command.operation == PaymentProviderCommandOperation.CREATE_PAYMENT
                and command.status == PaymentProviderCommandStatus.PENDING
                and order.status
                in {
                    PaymentOrderStatus.FAILED,
                    PaymentOrderStatus.CANCELLED,
                    PaymentOrderStatus.EXPIRED,
                }
            ):
                cls._finish_provider_command(
                    command,
                    status=PaymentProviderCommandStatus.FAILED,
                    error_code="payment_order_terminal",
                )
                session.flush()
                continue
            if command.operation == PaymentProviderCommandOperation.CREATE_REFUND:
                known_refund = session.scalar(
                    select(PaymentRefund)
                    .where(PaymentRefund.id == command.refund_id)
                    .with_for_update()
                )
                if known_refund is not None and known_refund.status in {
                    PaymentRefundStatus.SUCCEEDED,
                    PaymentRefundStatus.FAILED,
                    PaymentRefundStatus.CANCELLED,
                }:
                    cls._finish_provider_command(
                        command,
                        status=PaymentProviderCommandStatus.SUCCEEDED,
                        provider_resource_id=known_refund.provider_refund_id,
                        response_payload={"source": "verified_refund_already_applied"},
                    )
                    session.flush()
                    continue
            if command.operation in {
                PaymentProviderCommandOperation.CREATE_PAYMENT,
                PaymentProviderCommandOperation.CREATE_REFUND,
            } and (
                command.status
                in {
                    PaymentProviderCommandStatus.UNKNOWN,
                    PaymentProviderCommandStatus.CLAIMED,
                }
                or (
                    command.operation == PaymentProviderCommandOperation.CREATE_PAYMENT
                    and order.status == PaymentOrderStatus.RECONCILIATION_REQUIRED
                )
            ):
                command.status = PaymentProviderCommandStatus.UNKNOWN
                command.lease_token = None
                command.lease_expires_at = None
                command.last_error_code = (
                    command.last_error_code or "provider_outcome_unknown"
                )
                command.next_attempt_at = now + timedelta(days=3650)
                if command.operation == PaymentProviderCommandOperation.CREATE_PAYMENT:
                    order.status = PaymentOrderStatus.RECONCILIATION_REQUIRED
                    cls._ensure_query_payment_command(
                        session,
                        order,
                        rearm_terminal=True,
                    )
                else:
                    refund = session.scalar(
                        select(PaymentRefund)
                        .where(PaymentRefund.id == command.refund_id)
                        .with_for_update()
                    )
                    if refund is None:
                        command.status = PaymentProviderCommandStatus.DEAD
                        command.last_error_code = "refund_missing"
                        command.completed_at = now
                        session.flush()
                        continue
                    refund.status = PaymentRefundStatus.RECONCILIATION_REQUIRED
                    cls._ensure_query_refund_command(
                        session,
                        order=order,
                        refund=refund,
                        rearm_terminal=True,
                    )
                session.flush()
                continue
            if command.status != PaymentProviderCommandStatus.PENDING:
                cls._rearm_provider_command(command)
            cls._claim_provider_command(command)
            session.flush()
            session.info[f"payment_command_claim:{command.id}"] = session.get_transaction()
            return command
        return None

    @classmethod
    async def execute_claimed_provider_command(
        cls,
        session: Session,
        *,
        command_id: str,
        lease_token: str,
        provider: PaymentProvider,
    ) -> PaymentProviderCommand:
        """Execute PSP I/O outside a DB transaction, then finalize by lease."""

        claim_transaction = session.info.get(f"payment_command_claim:{command_id}")
        if claim_transaction is not None and claim_transaction is session.get_transaction():
            raise ConflictError("支付通道命令租约必须先提交再调用支付通道")
        session.flush()
        command = session.scalar(
            select(PaymentProviderCommand)
            .where(PaymentProviderCommand.id == command_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            command is None
            or command.status != PaymentProviderCommandStatus.CLAIMED
            or command.lease_token != lease_token
        ):
            raise ConflictError("支付通道命令租约不存在或已失效")
        if (
            command.provider != provider.provider_key
            or command.merchant_account != getattr(provider, "merchant_account", None)
        ):
            raise ConflictError("支付通道命令与执行通道不一致")
        order = cls._locked_order(session, command.order_id)
        refund = (
            session.get(PaymentRefund, command.refund_id)
            if command.refund_id is not None
            else None
        )
        if order is None:
            raise ConflictError("支付通道命令对应订单不存在")
        if (
            command.operation == PaymentProviderCommandOperation.CREATE_PAYMENT
            and order.automatic
        ):
            mandate = session.get(PaymentMandate, order.payment_mandate_id)
            if mandate is None or mandate.status != PaymentMandateStatus.ACTIVE:
                order.status = PaymentOrderStatus.CANCELLED
                cls._finish_provider_command(
                    command,
                    status=PaymentProviderCommandStatus.FAILED,
                    error_code="payment_mandate_not_active",
                )
                session.flush()
                return command
        try:
            outbound_request = cls._build_provider_request(
                command=command,
                order=order,
                refund=refund,
            )
        except (DomainError, PaymentProviderRequestError, TypeError, ValueError):
            cls._finish_provider_command(
                command,
                status=PaymentProviderCommandStatus.DEAD,
                error_code="provider_request_invalid",
            )
            order.status = PaymentOrderStatus.RECONCILIATION_REQUIRED
            if refund is not None and refund.status in _UNRESOLVED_REFUND_STATUSES:
                refund.status = PaymentRefundStatus.RECONCILIATION_REQUIRED
            session.flush()
            return command
        operation = command.operation
        order_id = command.order_id
        refund_id = command.refund_id
        # End the read transaction before awaiting the external provider.
        session.rollback()
        outcome: object | None = None
        outcome_error: Exception | None = None
        try:
            if operation == PaymentProviderCommandOperation.CREATE_PAYMENT:
                outcome = await provider.create_payment(outbound_request)  # type: ignore[arg-type]
            elif operation == PaymentProviderCommandOperation.CREATE_REFUND:
                outcome = await provider.create_refund(outbound_request)  # type: ignore[arg-type]
            elif operation == PaymentProviderCommandOperation.QUERY_PAYMENT:
                outcome = await provider.query_payment(outbound_request)  # type: ignore[arg-type]
            else:
                outcome = await provider.query_refund(outbound_request)  # type: ignore[arg-type]
        except Exception as exc:  # ambiguous create/query result is finalized below.
            outcome_error = exc

        claimed = session.scalar(
            select(PaymentProviderCommand)
            .where(PaymentProviderCommand.id == command_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            claimed is None
            or claimed.status != PaymentProviderCommandStatus.CLAIMED
            or claimed.lease_token != lease_token
        ):
            raise ConflictError("支付通道命令租约已被另一执行器接管")
        # Global payment lock order is command -> order.  The claim path already
        # owns the command row before it ever touches the order; keeping the
        # same order here prevents a PG command/order deadlock with a reaper.
        cls._locked_order(session, order_id)
        if operation == PaymentProviderCommandOperation.CREATE_PAYMENT:
            cls._finalize_create_payment(
                session,
                order_id=order_id,
                command=claimed,
                lease_token=lease_token,
                provider_result=outcome,
                provider_error=outcome_error,
            )
        elif operation == PaymentProviderCommandOperation.CREATE_REFUND:
            assert refund_id is not None
            cls._finalize_create_refund(
                session,
                refund_id=refund_id,
                command=claimed,
                lease_token=lease_token,
                provider_result=outcome,
                provider_error=outcome_error,
            )
        elif operation == PaymentProviderCommandOperation.QUERY_PAYMENT:
            cls._finalize_query_payment(
                session,
                order_id=order_id,
                query_command=claimed,
                lease_token=lease_token,
                provider_result=outcome,
                provider_error=outcome_error,
            )
        else:
            assert refund_id is not None
            cls._finalize_query_refund(
                session,
                refund_id=refund_id,
                query_command=claimed,
                lease_token=lease_token,
                provider_result=outcome,
                provider_error=outcome_error,
            )
        finalized = session.get(PaymentProviderCommand, command_id)
        if finalized is None:
            raise ConflictError("支付通道命令在完成阶段丢失")
        return finalized

    @staticmethod
    def _scope_filter(*, company_id: str | None, workspace_id: str | None):
        if (company_id is None) == (workspace_id is None):
            raise ConflictError("支付订单必须且只能绑定一个钱包")
        if company_id is not None:
            return PaymentOrder.company_id == company_id
        return PaymentOrder.personal_workspace_id == workspace_id

    @classmethod
    def create_point_order(
        cls,
        session: Session,
        *,
        company_id: str | None,
        workspace_id: str | None,
        user_id: str,
        points: int,
        provider: str,
        merchant_account: str,
        idempotency_key: str,
        automatic: bool = False,
        provider_customer_reference: str | None = None,
        payment_mandate_id: str | None = None,
    ) -> tuple[PaymentOrder, bool]:
        if (
            isinstance(points, bool)
            or not isinstance(points, int)
            or not 0 < points <= MAX_PURCHASE_POINTS
        ):
            raise ConflictError("购买积分超出支付通道允许的整数范围")
        if not 8 <= len(idempotency_key) <= 120:
            raise ConflictError("支付订单幂等键无效")
        if automatic and (
            payment_mandate_id is None or provider_customer_reference is None
        ):
            raise ConflictError("自动支付订单必须绑定已验证扣款授权")
        if not automatic and payment_mandate_id is not None:
            raise ConflictError("人工支付订单不得绑定自动扣款授权")
        scope_filter = cls._scope_filter(
            company_id=company_id,
            workspace_id=workspace_id,
        )
        session.flush()
        if company_id is not None:
            company = session.scalar(
                select(Company)
                .where(Company.id == company_id)
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            )
            wallet = session.scalar(
                select(CompanyPointWalletAccount)
                .where(CompanyPointWalletAccount.company_id == company_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if company is None or company.billing_version != 2 or wallet is None:
                raise ConflictError("企业尚未启用 POINT/v2，不能购买积分")
        else:
            wallet = session.scalar(
                select(PersonalWalletAccount)
                .where(PersonalWalletAccount.workspace_id == workspace_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if wallet is None:
                raise NotFoundError("个人积分钱包不存在")

        amount_cents = points * POINT_VALUE_CENTS
        intent = {
            "schema_version": 1,
            "purpose": PaymentPurpose.POINT_PURCHASE.value,
            "company_id": company_id,
            "personal_workspace_id": workspace_id,
            "points": points,
            "amount_cents": amount_cents,
            "currency": "CNY",
            "provider": provider,
            "merchant_account": merchant_account,
            "automatic": automatic,
            "provider_customer_reference": provider_customer_reference,
            "payment_mandate_id": payment_mandate_id,
        }
        fingerprint = _canonical_sha256(intent)
        existing = session.scalar(
            select(PaymentOrder).where(
                scope_filter,
                PaymentOrder.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise ConflictError("支付订单幂等键已被另一笔不同请求使用")
            cls._ensure_create_payment_command(session, existing)
            return existing, False
        if (
            wallet.available_points
            + wallet.reserved_points
            + wallet.reversal_reserved_points
            + points
            > MAX_POINTS
        ):
            raise ConflictError("购买积分将超过钱包上限")
        if automatic:
            mandate = session.scalar(
                select(PaymentMandate)
                .where(PaymentMandate.id == payment_mandate_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                mandate is None
                or mandate.status != PaymentMandateStatus.ACTIVE
                or mandate.company_id != company_id
                or mandate.personal_workspace_id != workspace_id
                or mandate.provider != provider
                or mandate.merchant_account != merchant_account
                or mandate.provider_customer_reference
                != provider_customer_reference
                or not mandate.provider_payment_method_reference
                or not mandate.provider_mandate_reference
                or mandate.consented_at is None
                or mandate.verified_at is None
                or mandate.revoked_at is not None
            ):
                raise ConflictError("自动支付订单未绑定有效扣款授权")
        order = PaymentOrder(
            company_id=company_id,
            personal_workspace_id=workspace_id,
            created_by_user_id=user_id,
            purpose=PaymentPurpose.POINT_PURCHASE,
            purpose_reference_id=None,
            provider=provider,
            merchant_account=merchant_account,
            status=PaymentOrderStatus.CREATED,
            currency="CNY",
            amount_cents=amount_cents,
            points=points,
            captured_amount_cents=0,
            refunded_amount_cents=0,
            disputed_amount_cents=0,
            fee_amount_cents=0,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            automatic=automatic,
            provider_customer_reference=provider_customer_reference,
            payment_mandate_id=payment_mandate_id,
            expires_at=utcnow() + timedelta(minutes=30),
        )
        session.add(order)
        session.flush()
        cls._ensure_create_payment_command(session, order)
        return order, True

    @classmethod
    def create_invoice_payment_order(
        cls,
        session: Session,
        *,
        company_id: str,
        invoice_id: str,
        user_id: str,
        amount_cents: int,
        provider: str,
        merchant_account: str,
        idempotency_key: str,
        provider_customer_reference: str | None = None,
    ) -> tuple[PaymentOrder, bool]:
        """Create a cash-only invoice payment intent without minting points."""

        if (
            isinstance(amount_cents, bool)
            or not isinstance(amount_cents, int)
            or not 0 < amount_cents <= MAX_PAYMENT_AMOUNT_CENTS
        ):
            raise ConflictError("企业发票付款金额超出支付通道范围")
        if not 8 <= len(idempotency_key) <= 120:
            raise ConflictError("支付订单幂等键无效")
        session.flush()
        company = session.scalar(
            select(Company)
            .where(Company.id == company_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        if company is None or company.billing_version != 2:
            raise ConflictError("企业发票付款只支持 POINT/v2 企业")
        invoice = session.scalar(
            select(CompanyInvoice)
            .where(
                CompanyInvoice.id == invoice_id,
                CompanyInvoice.company_id == company_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if invoice is None:
            raise NotFoundError("企业发票不存在")
        intent = {
            "schema_version": 1,
            "purpose": PaymentPurpose.INVOICE_PAYMENT.value,
            "company_id": company_id,
            "invoice_id": invoice.id,
            "amount_cents": amount_cents,
            "currency": invoice.currency,
            "provider": provider,
            "merchant_account": merchant_account,
            "provider_customer_reference": provider_customer_reference,
        }
        fingerprint = _canonical_sha256(intent)
        existing = session.scalar(
            select(PaymentOrder).where(
                PaymentOrder.company_id == company_id,
                PaymentOrder.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise ConflictError("支付订单幂等键已被另一笔不同请求使用")
            cls._ensure_create_payment_command(session, existing)
            return existing, False
        if invoice.currency != "CNY":
            raise ConflictError("企业发票付款只支持 CNY")
        outstanding = invoice.total_cents - invoice.paid_cents
        held_by_other_orders = int(
            session.scalar(
                select(func.coalesce(func.sum(PaymentOrder.amount_cents), 0)).where(
                    PaymentOrder.company_id == company_id,
                    PaymentOrder.purpose == PaymentPurpose.INVOICE_PAYMENT,
                    PaymentOrder.purpose_reference_id == invoice.id,
                    PaymentOrder.status.in_(
                        {
                            PaymentOrderStatus.CREATED,
                            PaymentOrderStatus.PENDING,
                            PaymentOrderStatus.REQUIRES_ACTION,
                            PaymentOrderStatus.RECONCILIATION_REQUIRED,
                        }
                    ),
                )
            )
            or 0
        )
        if amount_cents > outstanding - held_by_other_orders:
            raise ConflictError("企业发票付款金额超过未付且未占用金额")
        order = PaymentOrder(
            company_id=company_id,
            personal_workspace_id=None,
            created_by_user_id=user_id,
            purpose=PaymentPurpose.INVOICE_PAYMENT,
            purpose_reference_id=invoice.id,
            provider=provider,
            merchant_account=merchant_account,
            status=PaymentOrderStatus.CREATED,
            currency=invoice.currency,
            amount_cents=amount_cents,
            points=0,
            captured_amount_cents=0,
            refunded_amount_cents=0,
            disputed_amount_cents=0,
            fee_amount_cents=0,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            automatic=False,
            provider_customer_reference=provider_customer_reference,
            expires_at=utcnow() + timedelta(minutes=30),
        )
        session.add(order)
        session.flush()
        cls._ensure_create_payment_command(session, order)
        return order, True

    @classmethod
    def _finalize_create_payment(
        cls,
        session: Session,
        *,
        order_id: str,
        command: PaymentProviderCommand,
        lease_token: str,
        provider_result: object | None,
        provider_error: Exception | None,
    ) -> PaymentOrder:
        """Pure DB finalizer; the PSP call already happened outside a transaction."""

        order = cls._locked_order(session, order_id)
        cls._require_finalize_claim(
            command,
            lease_token=lease_token,
            operation=PaymentProviderCommandOperation.CREATE_PAYMENT,
            order=order,
        )
        verified_terminal = order.captured_amount_cents > 0 or order.status in {
            PaymentOrderStatus.FAILED, PaymentOrderStatus.CANCELLED, PaymentOrderStatus.EXPIRED
        }
        response_matches_verified = (
            provider_error is not None
            or not isinstance(provider_result, CreatePaymentResult)
            or (
                provider_result.provider == order.provider
                and provider_result.provider_payment_id == order.provider_order_id
            )
        )
        if verified_terminal and order.provider_order_id and response_matches_verified:
            # The independently committed signed callback is stronger evidence
            # than a lost or stale create response. Never reset its attempt.
            order.checkout_url = None
            cls._project_order_financial_status(order)
            cls._finish_provider_command(
                command,
                status=PaymentProviderCommandStatus.SUCCEEDED,
                provider_resource_id=order.provider_order_id,
                response_payload={"source": "verified_terminal_already_applied"},
            )
            session.flush()
            return order
        existing = session.scalar(
            select(PaymentAttempt)
            .where(PaymentAttempt.order_id == order.id)
            .order_by(PaymentAttempt.sequence.desc())
            .limit(1)
        )
        sequence = 1 if existing is None else existing.sequence
        provider_key = command.idempotency_key
        cls._create_payment_request_from_command(
            order=order,
            command=command,
        )
        if existing is None:
            attempt = PaymentAttempt(
                order_id=order.id,
                provider=order.provider,
                sequence=sequence,
                idempotency_key=provider_key,
                status=PaymentAttemptStatus.PENDING,
                request_sha256=command.request_sha256,
            )
            session.add(attempt)
        else:
            if (
                existing.idempotency_key != provider_key
                or existing.request_sha256 != command.request_sha256
                or existing.provider != order.provider
            ):
                raise ConflictError("支付重试与原始支付尝试不一致")
            attempt = existing
            attempt.status = PaymentAttemptStatus.PENDING
            attempt.failure_code = None
            attempt.completed_at = None
        session.flush()
        if provider_error is not None or not isinstance(provider_result, CreatePaymentResult):
            attempt.status = PaymentAttemptStatus.UNKNOWN
            attempt.failure_code = "provider_outcome_unknown"
            order.status = PaymentOrderStatus.RECONCILIATION_REQUIRED
            cls._finish_provider_command(
                command,
                status=PaymentProviderCommandStatus.UNKNOWN,
                error_code="provider_outcome_unknown",
            )
            cls._ensure_query_payment_command(
                session,
                order,
                rearm_terminal=True,
            )
            command.next_attempt_at = utcnow() + timedelta(days=3650)
            session.flush()
            return order
        result = provider_result
        if result.provider != order.provider or (
            order.provider_order_id is not None
            and order.provider_order_id != result.provider_payment_id
        ):
            attempt.status = PaymentAttemptStatus.UNKNOWN
            attempt.failure_code = "provider_identity_mismatch"
            order.status = PaymentOrderStatus.RECONCILIATION_REQUIRED
            cls._finish_provider_command(
                command,
                status=PaymentProviderCommandStatus.UNKNOWN,
                error_code="provider_identity_mismatch",
            )
            cls._ensure_query_payment_command(
                session,
                order,
                rearm_terminal=True,
            )
            command.next_attempt_at = utcnow() + timedelta(days=3650)
            session.flush()
            return order
        order.provider_order_id = result.provider_payment_id
        order.checkout_url = result.checkout_url if not order.captured_amount_cents else None
        # A concurrent signed capture must never be regressed by a stale create
        # response.  Otherwise even synchronous success awaits cash evidence.
        if order.captured_amount_cents:
            cls._project_order_financial_status(order)
        else:
            order.status = (
                PaymentOrderStatus.REQUIRES_ACTION
                if result.checkout_url
                else PaymentOrderStatus.PENDING
            )
        attempt.provider_attempt_id = result.provider_payment_id
        attempt.status = (
            PaymentAttemptStatus.SUCCEEDED
            if order.captured_amount_cents
            else (
                PaymentAttemptStatus.REQUIRES_ACTION
                if result.checkout_url
                else PaymentAttemptStatus.PENDING
            )
        )
        if order.captured_amount_cents:
            attempt.completed_at = order.captured_at
        attempt.response_sha256 = _canonical_sha256(
            {
                "provider": result.provider,
                "provider_payment_id": result.provider_payment_id,
                "status": result.status,
                "checkout_url_present": result.checkout_url is not None,
            }
        )
        cls._finish_provider_command(
            command,
            status=PaymentProviderCommandStatus.SUCCEEDED,
            provider_resource_id=result.provider_payment_id,
            response_payload={
                "provider": result.provider,
                "provider_payment_id": result.provider_payment_id,
                "status": result.status,
                "checkout_url_present": result.checkout_url is not None,
            },
        )
        if not order.captured_amount_cents:
            query = cls._ensure_query_payment_command(session, order, rearm_terminal=True)
            query.next_attempt_at = utcnow() + timedelta(minutes=1)
        session.flush()
        return order

    @staticmethod
    def _payment_query_payload(result: QueryPaymentResult) -> dict[str, Any]:
        return {
            "provider": result.provider,
            "found": result.found,
            "provider_payment_id": result.provider_payment_id,
            "status": result.status,
            "amount_cents": result.amount_cents,
            "currency": result.currency,
            "checkout_url_present": result.checkout_url is not None,
            "occurred_at": (
                _utc(result.occurred_at).isoformat()
                if result.occurred_at is not None
                else None
            ),
            "failure_code": result.failure_code,
        }

    @staticmethod
    def _refund_query_payload(result: QueryRefundResult) -> dict[str, Any]:
        return {
            "provider": result.provider,
            "found": result.found,
            "provider_refund_id": result.provider_refund_id,
            "status": result.status,
            "amount_cents": result.amount_cents,
            "currency": result.currency,
            "occurred_at": (
                _utc(result.occurred_at).isoformat()
                if result.occurred_at is not None
                else None
            ),
            "failure_code": result.failure_code,
        }

    @staticmethod
    def _provider_query_evidence(
        *,
        command: PaymentProviderCommand,
        response_payload: dict[str, Any],
    ) -> tuple[str, PaymentWebhookEvidence]:
        payload_sha256 = _canonical_sha256(response_payload)
        event_id = str(
            uuid5(
                NAMESPACE_URL,
                f"ai-video:payment-provider-query:{command.id}:{payload_sha256}",
            )
        )
        return event_id, PaymentWebhookEvidence(
            provider=command.provider,
            merchant_account=command.merchant_account,
            key_id="provider-query",
            event_id=event_id,
            delivery_timestamp=utcnow(),
            payload_sha256=payload_sha256,
        )

    @classmethod
    def _attempt_for_provider_query(
        cls,
        session: Session,
        *,
        order: PaymentOrder,
        create_command: PaymentProviderCommand,
    ) -> PaymentAttempt:
        attempt = session.scalar(
            select(PaymentAttempt)
            .where(PaymentAttempt.order_id == order.id)
            .order_by(PaymentAttempt.sequence.desc())
            .limit(1)
            .with_for_update()
        )
        if attempt is None:
            attempt = PaymentAttempt(
                order_id=order.id,
                provider=order.provider,
                sequence=1,
                idempotency_key=create_command.idempotency_key,
                status=PaymentAttemptStatus.UNKNOWN,
                request_sha256=create_command.request_sha256,
                failure_code="provider_query_recovery",
            )
            session.add(attempt)
            session.flush()
            return attempt
        if (
            attempt.provider != order.provider
            or attempt.idempotency_key != create_command.idempotency_key
            or attempt.request_sha256 != create_command.request_sha256
        ):
            raise ConflictError("支付查询与原始支付尝试不一致")
        return attempt

    @classmethod
    def _finalize_query_payment(
        cls,
        session: Session,
        *,
        order_id: str,
        query_command: PaymentProviderCommand,
        lease_token: str,
        provider_result: object | None,
        provider_error: Exception | None,
    ) -> PaymentOrder:
        """Resolve an ambiguous payment create through an authenticated PSP lookup."""

        order = cls._locked_order(session, order_id)
        cls._require_finalize_claim(
            query_command,
            lease_token=lease_token,
            operation=PaymentProviderCommandOperation.QUERY_PAYMENT,
            order=order,
        )
        create_command = cls._ensure_create_payment_command(session, order)
        if provider_error is not None or not isinstance(provider_result, QueryPaymentResult):
            order.status = PaymentOrderStatus.RECONCILIATION_REQUIRED
            cls._finish_provider_command(
                query_command,
                status=PaymentProviderCommandStatus.UNKNOWN,
                error_code="provider_query_outcome_unknown",
            )
            query_command.next_attempt_at = utcnow() + timedelta(minutes=1)
            session.flush()
            return order

        result = provider_result
        response_payload = cls._payment_query_payload(result)
        if result.provider != order.provider:
            order.status = PaymentOrderStatus.RECONCILIATION_REQUIRED
            cls._finish_provider_command(
                query_command,
                status=PaymentProviderCommandStatus.UNKNOWN,
                response_payload=response_payload,
                error_code="provider_query_identity_mismatch",
            )
            session.flush()
            return order
        if not result.found:
            if order.provider_order_id or create_command.provider_resource_id:
                order.status = PaymentOrderStatus.RECONCILIATION_REQUIRED
                cls._finish_provider_command(
                    query_command,
                    status=PaymentProviderCommandStatus.UNKNOWN,
                    response_payload=response_payload,
                    error_code="provider_query_lost_known_payment",
                )
                session.flush()
                return order
            cls._finish_provider_command(
                query_command,
                status=PaymentProviderCommandStatus.SUCCEEDED,
                response_payload=response_payload,
            )
            cls._rearm_provider_command(create_command)
            order.status = PaymentOrderStatus.CREATED
            order.checkout_url = None
            attempt = cls._attempt_for_provider_query(
                session,
                order=order,
                create_command=create_command,
            )
            attempt.status = PaymentAttemptStatus.UNKNOWN
            attempt.failure_code = "provider_query_not_found"
            attempt.completed_at = utcnow()
            session.flush()
            return order

        if (
            result.provider_payment_id is None
            or result.status is None
            or result.amount_cents != order.amount_cents
            or result.currency != order.currency
            or (
                order.provider_order_id is not None
                and order.provider_order_id != result.provider_payment_id
            )
        ):
            order.status = PaymentOrderStatus.RECONCILIATION_REQUIRED
            cls._finish_provider_command(
                query_command,
                status=PaymentProviderCommandStatus.UNKNOWN,
                response_payload=response_payload,
                error_code="provider_query_intent_mismatch",
            )
            session.flush()
            return order

        if order.captured_amount_cents:
            if result.status not in {"pending", "requires_action", "captured"}:
                order.status = PaymentOrderStatus.RECONCILIATION_REQUIRED
                cls._finish_provider_command(
                    query_command,
                    status=PaymentProviderCommandStatus.UNKNOWN,
                    response_payload=response_payload,
                    error_code="payment_query_terminal_conflict",
                )
            else:
                cls._finish_provider_command(
                    query_command,
                    status=PaymentProviderCommandStatus.SUCCEEDED,
                    provider_resource_id=result.provider_payment_id,
                    response_payload=response_payload,
                )
                cls._finish_provider_command(
                    create_command,
                    status=PaymentProviderCommandStatus.SUCCEEDED,
                    provider_resource_id=result.provider_payment_id,
                    response_payload={"source": "verified_capture_already_applied"},
                )
                cls._project_order_financial_status(order)
            session.flush()
            return order
        if order.status in {
            PaymentOrderStatus.FAILED, PaymentOrderStatus.CANCELLED, PaymentOrderStatus.EXPIRED
        }:
            if result.status in {"pending", "requires_action", order.status.value}:
                cls._finish_provider_command(
                    query_command,
                    status=PaymentProviderCommandStatus.SUCCEEDED,
                    provider_resource_id=result.provider_payment_id,
                    response_payload=response_payload,
                )
            else:
                order.status = PaymentOrderStatus.RECONCILIATION_REQUIRED
                cls._finish_provider_command(
                    query_command,
                    status=PaymentProviderCommandStatus.UNKNOWN,
                    response_payload=response_payload,
                    error_code="payment_query_terminal_conflict",
                )
            session.flush()
            return order
        order.provider_order_id = result.provider_payment_id
        order.checkout_url = result.checkout_url
        attempt = cls._attempt_for_provider_query(
            session,
            order=order,
            create_command=create_command,
        )
        attempt.provider_attempt_id = result.provider_payment_id
        attempt.response_sha256 = _canonical_sha256(response_payload)
        attempt.failure_code = None
        cls._finish_provider_command(
            create_command,
            status=PaymentProviderCommandStatus.SUCCEEDED,
            provider_resource_id=result.provider_payment_id,
            response_payload={"source": "provider_query", **response_payload},
        )

        if result.status in {"pending", "requires_action"}:
            order.status = (
                PaymentOrderStatus.REQUIRES_ACTION
                if result.status == "requires_action"
                else PaymentOrderStatus.PENDING
            )
            attempt.status = (
                PaymentAttemptStatus.REQUIRES_ACTION
                if result.status == "requires_action"
                else PaymentAttemptStatus.PENDING
            )
            cls._finish_provider_command(
                query_command,
                status=PaymentProviderCommandStatus.PENDING,
                provider_resource_id=result.provider_payment_id,
                response_payload=response_payload,
            )
            query_command.next_attempt_at = utcnow() + timedelta(minutes=1)
            session.flush()
            return order

        if result.occurred_at is None:
            raise ConflictError("支付通道终态查询缺少发生时间")
        order.status = PaymentOrderStatus.PENDING
        attempt.status = PaymentAttemptStatus.PENDING
        event_id, evidence = cls._provider_query_evidence(
            command=query_command,
            response_payload=response_payload,
        )
        event_base = {
            "api_version": "v1",
            "schema_version": 1,
            "event_id": event_id,
            "provider": order.provider,
            "key_id": evidence.key_id,
            "occurred_at": result.occurred_at,
        }
        if result.status == "captured":
            event: PaymentWebhookEvent = PaymentCapturedEvent(
                **event_base,
                type="payment.captured",
                data={
                    "order_id": order.id,
                    "provider_payment_id": result.provider_payment_id,
                    "amount_cents": order.amount_cents,
                    "currency": order.currency,
                },
            )
        else:
            terminal_data = {
                "order_id": order.id,
                "provider_payment_id": result.provider_payment_id,
                "failure_code": result.failure_code,
            }
            terminal_class = {
                "failed": PaymentFailedEvent,
                "cancelled": PaymentCancelledEvent,
                "expired": PaymentExpiredEvent,
            }.get(result.status)
            if terminal_class is None:
                raise ConflictError("支付通道查询返回未知终态")
            event = terminal_class(
                **event_base,
                type=f"payment.{result.status}",
                data=terminal_data,
            )
        cls._finish_provider_command(
            query_command,
            status=PaymentProviderCommandStatus.SUCCEEDED,
            provider_resource_id=result.provider_payment_id,
            response_payload=response_payload,
        )
        cls.process_webhook(session, event=event, evidence=evidence)
        session.flush()
        return order

    @classmethod
    def _finalize_query_refund(
        cls,
        session: Session,
        *,
        refund_id: str,
        query_command: PaymentProviderCommand,
        lease_token: str,
        provider_result: object | None,
        provider_error: Exception | None,
    ) -> PaymentRefund:
        """Resolve an ambiguous refund without ever allocating a second hold."""

        refund = session.scalar(
            select(PaymentRefund)
            .where(PaymentRefund.id == refund_id)
            .with_for_update()
        )
        if refund is None:
            raise NotFoundError("退款申请不存在")
        order = cls._locked_order(session, refund.order_id)
        cls._require_finalize_claim(
            query_command,
            lease_token=lease_token,
            operation=PaymentProviderCommandOperation.QUERY_REFUND,
            order=order,
        )
        if query_command.refund_id != refund.id or refund.provider != order.provider:
            raise ConflictError("退款查询命令与退款申请不一致")
        if not order.provider_order_id:
            raise ConflictError("退款订单缺少支付通道交易编号")
        create_command = cls._ensure_create_refund_command(
            session,
            order=order,
            refund=refund,
        )
        if provider_error is not None or not isinstance(provider_result, QueryRefundResult):
            refund.status = PaymentRefundStatus.RECONCILIATION_REQUIRED
            cls._finish_provider_command(
                query_command,
                status=PaymentProviderCommandStatus.UNKNOWN,
                error_code="provider_query_outcome_unknown",
            )
            query_command.next_attempt_at = utcnow() + timedelta(minutes=1)
            session.flush()
            return refund

        result = provider_result
        response_payload = cls._refund_query_payload(result)
        if result.provider != order.provider:
            refund.status = PaymentRefundStatus.RECONCILIATION_REQUIRED
            cls._finish_provider_command(
                query_command,
                status=PaymentProviderCommandStatus.UNKNOWN,
                response_payload=response_payload,
                error_code="provider_query_identity_mismatch",
            )
            session.flush()
            return refund
        if not result.found:
            if refund.provider_refund_id or create_command.provider_resource_id:
                refund.status = PaymentRefundStatus.RECONCILIATION_REQUIRED
                cls._finish_provider_command(
                    query_command,
                    status=PaymentProviderCommandStatus.UNKNOWN,
                    response_payload=response_payload,
                    error_code="provider_query_lost_known_refund",
                )
                session.flush()
                return refund
            cls._finish_provider_command(
                query_command,
                status=PaymentProviderCommandStatus.SUCCEEDED,
                response_payload=response_payload,
            )
            cls._rearm_provider_command(create_command)
            refund.status = PaymentRefundStatus.REQUESTED
            session.flush()
            return refund
        if (
            result.provider_refund_id is None
            or result.status is None
            or result.amount_cents != refund.amount_cents
            or result.currency != refund.currency
            or (
                refund.provider_refund_id is not None
                and refund.provider_refund_id != result.provider_refund_id
            )
        ):
            refund.status = PaymentRefundStatus.RECONCILIATION_REQUIRED
            cls._finish_provider_command(
                query_command,
                status=PaymentProviderCommandStatus.UNKNOWN,
                response_payload=response_payload,
                error_code="provider_query_intent_mismatch",
            )
            session.flush()
            return refund

        if refund.status in {
            PaymentRefundStatus.SUCCEEDED,
            PaymentRefundStatus.FAILED,
            PaymentRefundStatus.CANCELLED,
        }:
            expected_status = {
                PaymentRefundStatus.SUCCEEDED: "succeeded",
                PaymentRefundStatus.FAILED: "failed",
            }.get(refund.status)
            if result.status != "pending" and result.status != expected_status:
                order.status = PaymentOrderStatus.RECONCILIATION_REQUIRED
                cls._finish_provider_command(
                    query_command,
                    status=PaymentProviderCommandStatus.UNKNOWN,
                    response_payload=response_payload,
                    error_code="refund_query_terminal_conflict",
                )
            else:
                cls._finish_provider_command(
                    query_command,
                    status=PaymentProviderCommandStatus.SUCCEEDED,
                    provider_resource_id=result.provider_refund_id,
                    response_payload=response_payload,
                )
                cls._finish_provider_command(
                    create_command,
                    status=PaymentProviderCommandStatus.SUCCEEDED,
                    provider_resource_id=result.provider_refund_id,
                    response_payload={"source": "verified_refund_already_applied"},
                )
            session.flush()
            return refund
        refund.provider_refund_id = result.provider_refund_id
        cls._finish_provider_command(
            create_command,
            status=PaymentProviderCommandStatus.SUCCEEDED,
            provider_resource_id=result.provider_refund_id,
            response_payload={"source": "provider_query", **response_payload},
        )
        if result.status == "pending":
            refund.status = PaymentRefundStatus.PENDING
            cls._finish_provider_command(
                query_command,
                status=PaymentProviderCommandStatus.PENDING,
                provider_resource_id=result.provider_refund_id,
                response_payload=response_payload,
            )
            query_command.next_attempt_at = utcnow() + timedelta(minutes=1)
            session.flush()
            return refund

        if result.occurred_at is None:
            raise ConflictError("退款通道终态查询缺少发生时间")
        event_id, evidence = cls._provider_query_evidence(
            command=query_command,
            response_payload=response_payload,
        )
        event_base = {
            "api_version": "v1",
            "schema_version": 1,
            "event_id": event_id,
            "provider": order.provider,
            "key_id": evidence.key_id,
            "occurred_at": result.occurred_at,
        }
        if result.status == "succeeded":
            event: PaymentWebhookEvent = RefundSucceededEvent(
                **event_base,
                type="refund.succeeded",
                data={
                    "order_id": order.id,
                    "refund_id": refund.id,
                    "provider_payment_id": order.provider_order_id,
                    "provider_refund_id": result.provider_refund_id,
                    "amount_cents": refund.amount_cents,
                    "currency": refund.currency,
                },
            )
        else:
            if not result.failure_code:
                raise ConflictError("退款失败查询缺少失败代码")
            event = RefundFailedEvent(
                **event_base,
                type="refund.failed",
                data={
                    "order_id": order.id,
                    "refund_id": refund.id,
                    "provider_payment_id": order.provider_order_id,
                    "provider_refund_id": result.provider_refund_id,
                    "amount_cents": refund.amount_cents,
                    "currency": refund.currency,
                    "failure_code": result.failure_code,
                },
            )
        cls._finish_provider_command(
            query_command,
            status=PaymentProviderCommandStatus.SUCCEEDED,
            provider_resource_id=result.provider_refund_id,
            response_payload=response_payload,
        )
        cls.process_webhook(session, event=event, evidence=evidence)
        session.flush()
        return refund

    @staticmethod
    def _existing_receipt(
        session: Session,
        *,
        evidence: PaymentWebhookEvidence,
        merchant_account: str,
    ) -> PaymentWebhookReceipt | None:
        if evidence.merchant_account != merchant_account:
            raise ConflictError("支付回调签名商户与订单商户不一致")
        receipt = session.scalar(
            select(PaymentWebhookReceipt).where(
                PaymentWebhookReceipt.provider == evidence.provider,
                PaymentWebhookReceipt.merchant_account == merchant_account,
                PaymentWebhookReceipt.provider_event_id == evidence.event_id,
            )
        )
        if receipt is not None and receipt.payload_sha256 != evidence.payload_sha256:
            raise ConflictError("支付回调事件编号对应了不同载荷")
        return receipt

    @staticmethod
    def _append_receipt(
        session: Session,
        *,
        event: PaymentWebhookEvent,
        evidence: PaymentWebhookEvidence,
        merchant_account: str,
        outcome: PaymentWebhookOutcome,
        order_id: str | None,
        refund_id: str | None = None,
        dispute_id: str | None = None,
        error_code: str | None = None,
    ) -> PaymentWebhookReceipt:
        if evidence.merchant_account != merchant_account:
            raise ConflictError("支付回调签名商户与订单商户不一致")
        receipt = PaymentWebhookReceipt(
            provider=evidence.provider,
            merchant_account=merchant_account,
            provider_event_id=evidence.event_id,
            event_type=event.type,
            payload_sha256=evidence.payload_sha256,
            signature_key_id=evidence.key_id,
            signature_timestamp=evidence.delivery_timestamp,
            provider_occurred_at=_utc(event.occurred_at),
            outcome=outcome,
            order_id=order_id,
            refund_id=refund_id,
            dispute_id=dispute_id,
            error_code=error_code,
        )
        session.add(receipt)
        session.flush()
        return receipt

    @classmethod
    def _allocate_debt_recovery(
        cls,
        session: Session,
        *,
        order: PaymentOrder,
        payment_transaction_id: str,
        recovered_points: int,
    ) -> None:
        if recovered_points <= 0:
            return
        scope_filter = (
            PaymentOrder.company_id == order.company_id
            if order.company_id is not None
            else PaymentOrder.personal_workspace_id == order.personal_workspace_id
        )
        disputes = list(
            session.scalars(
                select(PaymentDispute)
                .join(PaymentOrder, PaymentOrder.id == PaymentDispute.order_id)
                .where(
                    scope_filter,
                    PaymentDispute.status.in_(
                        {PaymentDisputeStatus.OPEN, PaymentDisputeStatus.LOST}
                    ),
                    PaymentDispute.debt_points > 0,
                )
                .order_by(PaymentDispute.opened_at, PaymentDispute.id)
                # The scope join is read-only.  Locking the old PaymentOrder
                # here would invert capture(order -> wallet) against a dispute
                # resolving that old order while it waits for this wallet.
                .with_for_update(of=PaymentDispute)
                .execution_options(populate_existing=True)
            ).all()
        )
        remaining = recovered_points
        for dispute in disputes:
            already_recovered = int(
                session.scalar(
                    select(
                        func.coalesce(
                            func.sum(
                                PaymentDisputeDebtRecoveryAllocation.recovered_points
                            ),
                            0,
                        )
                    ).where(
                        PaymentDisputeDebtRecoveryAllocation.dispute_id == dispute.id
                    )
                )
                or 0
            )
            outstanding = dispute.debt_points - already_recovered
            if outstanding <= 0:
                continue
            allocated = min(outstanding, remaining)
            session.add(
                PaymentDisputeDebtRecoveryAllocation(
                    dispute_id=dispute.id,
                    recovery_payment_transaction_id=payment_transaction_id,
                    company_id=order.company_id,
                    personal_workspace_id=order.personal_workspace_id,
                    recovered_points=allocated,
                    idempotency_key=(
                        f"dispute-debt-recovery:{dispute.id}:{payment_transaction_id}"
                    ),
                )
            )
            remaining -= allocated
            if remaining == 0:
                break
        if remaining:
            raise ConflictError("钱包拒付债务缺少逐争议恢复分配")

    @classmethod
    def _capture_personal_points(
        cls,
        session: Session,
        *,
        order: PaymentOrder,
        event_key: str,
        payment_transaction_id: str,
    ) -> None:
        assert order.personal_workspace_id is not None
        wallet = PersonalWalletService._locked_account(
            session, order.personal_workspace_id
        )
        PersonalWalletService._ensure_lot_projection(session, account=wallet)
        debt_recovery = min(wallet.debt_points, order.points)
        if (
            wallet.available_points
            + wallet.reserved_points
            + wallet.reversal_reserved_points
            + order.points
            - debt_recovery
            > MAX_POINTS
        ):
            raise ConflictError("支付入账将超过钱包积分上限")
        if debt_recovery:
            cls._allocate_debt_recovery(
                session,
                order=order,
                payment_transaction_id=payment_transaction_id,
                recovered_points=debt_recovery,
            )
            wallet.debt_points -= debt_recovery
            session.add(
                PersonalLedgerEntry(
                    workspace_id=order.personal_workspace_id,
                    kind=LedgerKind.DEBT_RECOVERY,
                    amount_points=debt_recovery,
                    available_delta_points=0,
                    reserved_delta_points=0,
                    reversal_reserved_delta_points=0,
                    debt_delta_points=-debt_recovery,
                    idempotency_key=f"{event_key}:debt",
                    payment_order_id=order.id,
                    note="payment capture recovered chargeback debt",
                )
            )
        granted = order.points - debt_recovery
        if granted:
            wallet.available_points += granted
            session.add(
                PersonalPointLot(
                    workspace_id=order.personal_workspace_id,
                    source_kind=PointLotSourceKind.PURCHASED,
                    original_points=granted,
                    available_points=granted,
                    reserved_points=0,
                    reversal_reserved_points=0,
                    settled_points=0,
                    reversed_points=0,
                    cash_basis_cents=granted * POINT_VALUE_CENTS,
                    receivable_basis_cents=0,
                    subsidy_cents=0,
                    refundable=True,
                    idempotency_key=f"{event_key}:credit",
                    payment_order_id=order.id,
                )
            )
            session.add(
                PersonalLedgerEntry(
                    workspace_id=order.personal_workspace_id,
                    kind=LedgerKind.RECHARGE,
                    amount_points=granted,
                    available_delta_points=granted,
                    reserved_delta_points=0,
                    reversal_reserved_delta_points=0,
                    debt_delta_points=0,
                    idempotency_key=f"{event_key}:credit",
                    payment_order_id=order.id,
                    note="verified payment capture",
                )
            )

    @classmethod
    def _capture_company_points(
        cls,
        session: Session,
        *,
        order: PaymentOrder,
        event_key: str,
        payment_transaction_id: str,
    ) -> None:
        assert order.company_id is not None
        wallet = cls._locked_point_wallet(session, order)
        debt_recovery = min(wallet.debt_points, order.points)
        if (
            wallet.available_points
            + wallet.reserved_points
            + wallet.reversal_reserved_points
            + order.points
            - debt_recovery
            > MAX_POINTS
        ):
            raise ConflictError("支付入账将超过钱包积分上限")
        if debt_recovery:
            cls._allocate_debt_recovery(
                session,
                order=order,
                payment_transaction_id=payment_transaction_id,
                recovered_points=debt_recovery,
            )
            wallet.debt_points -= debt_recovery
            session.add(
                CompanyPointLedgerEntry(
                    company_id=order.company_id,
                    kind=PointLedgerKind.DEBT_RECOVERY,
                    amount_points=debt_recovery,
                    available_delta_points=0,
                    reserved_delta_points=0,
                    reversal_reserved_delta_points=0,
                    debt_delta_points=-debt_recovery,
                    idempotency_key=f"{event_key}:debt",
                    payment_order_id=order.id,
                    note="payment capture recovered chargeback debt",
                )
            )
        granted = order.points - debt_recovery
        if granted:
            wallet.available_points += granted
            session.add(
                CompanyPointLot(
                    company_id=order.company_id,
                    source_kind=PointLotSourceKind.PURCHASED,
                    original_points=granted,
                    available_points=granted,
                    reserved_points=0,
                    reversal_reserved_points=0,
                    settled_points=0,
                    reversed_points=0,
                    cash_basis_cents=granted * POINT_VALUE_CENTS,
                    receivable_basis_cents=0,
                    subsidy_cents=0,
                    idempotency_key=f"{event_key}:credit",
                    payment_order_id=order.id,
                )
            )
            session.add(
                CompanyPointLedgerEntry(
                    company_id=order.company_id,
                    kind=PointLedgerKind.CREDIT,
                    amount_points=granted,
                    available_delta_points=granted,
                    reserved_delta_points=0,
                    reversal_reserved_delta_points=0,
                    debt_delta_points=0,
                    idempotency_key=f"{event_key}:credit",
                    payment_order_id=order.id,
                    note="verified payment capture",
                )
            )

    @classmethod
    def _process_capture(
        cls,
        session: Session,
        *,
        event: PaymentCapturedEvent,
        evidence: PaymentWebhookEvidence,
    ) -> PaymentWebhookReceipt:
        order = cls._locked_order(session, str(event.data.order_id))
        existing_receipt = cls._existing_receipt(
            session, evidence=evidence, merchant_account=order.merchant_account
        )
        if existing_receipt is not None:
            return existing_receipt
        mismatch = (
            order.provider != evidence.provider
            or order.provider_order_id != event.data.provider_payment_id
            or order.amount_cents != event.data.amount_cents
            or order.currency != event.data.currency
            or order.purpose
            not in {PaymentPurpose.POINT_PURCHASE, PaymentPurpose.INVOICE_PAYMENT}
        )
        if mismatch:
            order.status = PaymentOrderStatus.RECONCILIATION_REQUIRED
            return cls._append_receipt(
                session,
                event=event,
                evidence=evidence,
                merchant_account=order.merchant_account,
                outcome=PaymentWebhookOutcome.RECONCILIATION_REQUIRED,
                order_id=order.id,
                error_code="capture_intent_mismatch",
            )
        transaction = session.scalar(
            select(PaymentTransaction).where(
                PaymentTransaction.provider == evidence.provider,
                PaymentTransaction.provider_transaction_id
                == event.data.provider_payment_id,
            )
        )
        if transaction is not None:
            if (
                transaction.order_id != order.id
                or transaction.kind != PaymentTransactionKind.CAPTURE
                or transaction.amount_cents != order.amount_cents
            ):
                raise ConflictError("支付通道交易编号对应了不同订单")
            return cls._append_receipt(
                session,
                event=event,
                evidence=evidence,
                merchant_account=order.merchant_account,
                outcome=PaymentWebhookOutcome.PROCESSED,
                order_id=order.id,
            )
        if order.captured_amount_cents:
            order.status = PaymentOrderStatus.RECONCILIATION_REQUIRED
            return cls._append_receipt(
                session,
                event=event,
                evidence=evidence,
                merchant_account=order.merchant_account,
                outcome=PaymentWebhookOutcome.RECONCILIATION_REQUIRED,
                order_id=order.id,
                error_code="duplicate_capture_business_event",
            )
        receipt = cls._append_receipt(
            session,
            event=event,
            evidence=evidence,
            merchant_account=order.merchant_account,
            outcome=PaymentWebhookOutcome.PROCESSED,
            order_id=order.id,
        )
        transaction = PaymentTransaction(
            order_id=order.id,
            webhook_receipt_id=receipt.id,
            provider=evidence.provider,
            provider_transaction_id=event.data.provider_payment_id,
            kind=PaymentTransactionKind.CAPTURE,
            amount_cents=event.data.amount_cents,
            currency=event.data.currency,
            occurred_at=_utc(event.occurred_at),
        )
        session.add(transaction)
        session.flush()
        if order.purpose == PaymentPurpose.POINT_PURCHASE:
            event_key = f"payment:{order.id}:{evidence.event_id}"
            if order.personal_workspace_id is not None:
                cls._capture_personal_points(
                    session,
                    order=order,
                    event_key=event_key,
                    payment_transaction_id=transaction.id,
                )
            else:
                cls._capture_company_points(
                    session,
                    order=order,
                    event_key=event_key,
                    payment_transaction_id=transaction.id,
                )
        order.captured_amount_cents = event.data.amount_cents
        order.captured_at = _utc(event.occurred_at)
        order.status = PaymentOrderStatus.PAID
        order.checkout_url = None
        attempt = session.scalar(
            select(PaymentAttempt)
            .where(PaymentAttempt.order_id == order.id)
            .order_by(PaymentAttempt.sequence.desc())
            .limit(1)
            .with_for_update()
        )
        if attempt is not None:
            if (
                attempt.provider != order.provider
                or (
                    attempt.provider_attempt_id is not None
                    and attempt.provider_attempt_id != event.data.provider_payment_id
                )
            ):
                raise ConflictError("支付成功事件与支付尝试不一致")
            attempt.provider_attempt_id = event.data.provider_payment_id
            attempt.status = PaymentAttemptStatus.SUCCEEDED
            attempt.failure_code = None
            attempt.completed_at = _utc(event.occurred_at)
        session.flush()
        if order.purpose == PaymentPurpose.INVOICE_PAYMENT:
            assert order.purpose_reference_id is not None
            EnterpriseBillingService.apply_payment_transaction(
                session,
                invoice_id=order.purpose_reference_id,
                payment_transaction_id=transaction.id,
            )
        return receipt

    @classmethod
    def _process_payment_terminal(
        cls,
        session: Session,
        *,
        event: PaymentFailedEvent | PaymentCancelledEvent | PaymentExpiredEvent,
        evidence: PaymentWebhookEvidence,
    ) -> PaymentWebhookReceipt:
        order = cls._locked_order(session, str(event.data.order_id))
        existing = cls._existing_receipt(
            session,
            evidence=evidence,
            merchant_account=order.merchant_account,
        )
        if existing is not None:
            return existing
        if (
            order.provider != evidence.provider
            or order.provider_order_id != event.data.provider_payment_id
        ):
            order.status = PaymentOrderStatus.RECONCILIATION_REQUIRED
            return cls._append_receipt(
                session,
                event=event,
                evidence=evidence,
                merchant_account=order.merchant_account,
                outcome=PaymentWebhookOutcome.RECONCILIATION_REQUIRED,
                order_id=order.id,
                error_code="payment_terminal_intent_mismatch",
            )
        target = (
            PaymentOrderStatus.FAILED
            if isinstance(event, PaymentFailedEvent)
            else PaymentOrderStatus.CANCELLED
            if isinstance(event, PaymentCancelledEvent)
            else PaymentOrderStatus.EXPIRED
        )
        if order.status == target:
            return cls._append_receipt(
                session,
                event=event,
                evidence=evidence,
                merchant_account=order.merchant_account,
                outcome=PaymentWebhookOutcome.PROCESSED,
                order_id=order.id,
            )
        if order.status not in _NONTERMINAL_ORDER_STATUSES:
            order.status = PaymentOrderStatus.RECONCILIATION_REQUIRED
            return cls._append_receipt(
                session,
                event=event,
                evidence=evidence,
                merchant_account=order.merchant_account,
                outcome=PaymentWebhookOutcome.RECONCILIATION_REQUIRED,
                order_id=order.id,
                error_code="payment_terminal_state_conflict",
            )
        attempt = session.scalar(
            select(PaymentAttempt)
            .where(PaymentAttempt.order_id == order.id)
            .order_by(PaymentAttempt.sequence.desc())
            .limit(1)
            .with_for_update()
        )
        if attempt is not None:
            attempt.status = PaymentAttemptStatus.FAILED
            attempt.failure_code = event.data.failure_code or event.type.replace(".", "_")
            attempt.completed_at = _utc(event.occurred_at)
        order.status = target
        return cls._append_receipt(
            session,
            event=event,
            evidence=evidence,
            merchant_account=order.merchant_account,
            outcome=PaymentWebhookOutcome.PROCESSED,
            order_id=order.id,
        )

    @classmethod
    def request_refund(
        cls,
        session: Session,
        *,
        order_id: str,
        amount_cents: int,
        reason: str,
        user_id: str,
        idempotency_key: str,
    ) -> tuple[PaymentRefund, bool]:
        order = cls._locked_order(session, order_id)
        if amount_cents <= 0:
            raise ConflictError("退款金额必须大于 0")
        if order.purpose == PaymentPurpose.POINT_PURCHASE:
            if amount_cents % POINT_VALUE_CENTS:
                raise ConflictError("积分订单退款金额必须对应完整整数积分")
            points = amount_cents // POINT_VALUE_CENTS
        elif order.purpose == PaymentPurpose.INVOICE_PAYMENT:
            points = 0
        else:  # pragma: no cover - constrained database value.
            raise ConflictError("支付订单用途无效")
        fingerprint = _canonical_sha256(
            {
                "order_id": order.id,
                "amount_cents": amount_cents,
                "currency": order.currency,
                "reason": reason,
            }
        )
        existing = session.scalar(
            select(PaymentRefund).where(
                PaymentRefund.order_id == order.id,
                PaymentRefund.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise ConflictError("退款幂等键已被另一笔不同请求使用")
            cls._ensure_create_refund_command(
                session,
                order=order,
                refund=existing,
            )
            return existing, False
        if order.status not in {
            PaymentOrderStatus.PAID,
            PaymentOrderStatus.PARTIALLY_REFUNDED,
        }:
            raise ConflictError("支付订单当前状态不能退款")
        pending_amount = cls._unresolved_refund_amount(
            session,
            order_id=order.id,
        )
        if (
            order.refunded_amount_cents + pending_amount + amount_cents
            > order.captured_amount_cents - order.disputed_amount_cents
        ):
            raise ConflictError("退款金额超过订单可退款余额")
        lot = None
        wallet = None
        if order.purpose == PaymentPurpose.POINT_PURCHASE:
            lot, wallet = cls._refund_lot_wallet(session, order)
            if lot is None or wallet is None or lot.available_points < points:
                raise ConflictError("只有该订单尚未消费的付费积分可以主动退款")
        refund = PaymentRefund(
            order_id=order.id,
            provider=order.provider,
            status=PaymentRefundStatus.REQUESTED,
            amount_cents=amount_cents,
            points=points,
            currency=order.currency,
            reason=reason[:240],
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            requested_by_user_id=user_id,
        )
        session.add(refund)
        session.flush()
        cls._ensure_create_refund_command(
            session,
            order=order,
            refund=refund,
        )
        if order.purpose == PaymentPurpose.INVOICE_PAYMENT:
            return refund, True
        assert lot is not None and wallet is not None
        lot.available_points -= points
        lot.reversal_reserved_points += points
        wallet.available_points -= points
        wallet.reversal_reserved_points += points
        ledger_key = f"refund:{refund.id}:reserve"
        if order.company_id is not None:
            session.add(
                CompanyPointLedgerEntry(
                    company_id=order.company_id,
                    kind=PointLedgerKind.REFUND_RESERVE,
                    amount_points=points,
                    available_delta_points=-points,
                    reserved_delta_points=0,
                    reversal_reserved_delta_points=points,
                    debt_delta_points=0,
                    idempotency_key=ledger_key,
                    payment_order_id=order.id,
                    payment_refund_id=refund.id,
                    note=reason[:240],
                )
            )
        else:
            session.add(
                PersonalLedgerEntry(
                    workspace_id=order.personal_workspace_id,
                    kind=LedgerKind.REFUND_RESERVE,
                    amount_points=points,
                    available_delta_points=-points,
                    reserved_delta_points=0,
                    reversal_reserved_delta_points=points,
                    debt_delta_points=0,
                    idempotency_key=ledger_key,
                    payment_order_id=order.id,
                    payment_refund_id=refund.id,
                    note=reason[:240],
                )
            )
        session.flush()
        return refund, True

    @classmethod
    def _finalize_create_refund(
        cls,
        session: Session,
        *,
        refund_id: str,
        command: PaymentProviderCommand,
        lease_token: str,
        provider_result: object | None,
        provider_error: Exception | None,
    ) -> PaymentRefund:
        refund = session.scalar(
            select(PaymentRefund)
            .where(PaymentRefund.id == refund_id)
            .with_for_update()
        )
        if refund is None:
            raise NotFoundError("退款申请不存在")
        order = cls._locked_order(session, refund.order_id)
        cls._require_finalize_claim(
            command,
            lease_token=lease_token,
            operation=PaymentProviderCommandOperation.CREATE_REFUND,
            order=order,
        )
        if command.refund_id != refund.id or not order.provider_order_id:
            raise ConflictError("退款命令与退款申请不一致")
        if refund.status in {
            PaymentRefundStatus.SUCCEEDED, PaymentRefundStatus.FAILED, PaymentRefundStatus.CANCELLED
        } and refund.provider_refund_id:
            compatible = (
                provider_error is not None
                or not isinstance(provider_result, CreateRefundResult)
                or (
                    provider_result.provider == order.provider
                    and provider_result.provider_refund_id == refund.provider_refund_id
                    and provider_result.status in {"pending", refund.status.value}
                )
            )
            if compatible:
                cls._finish_provider_command(
                    command,
                    status=PaymentProviderCommandStatus.SUCCEEDED,
                    provider_resource_id=refund.provider_refund_id,
                    response_payload={"source": "verified_terminal_already_applied"},
                )
            else:
                order.status = PaymentOrderStatus.RECONCILIATION_REQUIRED
                cls._finish_provider_command(
                    command,
                    status=PaymentProviderCommandStatus.UNKNOWN,
                    error_code="refund_create_terminal_conflict",
                )
                cls._ensure_query_refund_command(session, order=order, refund=refund)
                command.next_attempt_at = utcnow() + timedelta(days=3650)
            session.flush()
            return refund
        if provider_error is not None or not isinstance(provider_result, CreateRefundResult):
            # The provider may have accepted the stable refund idempotency key
            # before the response was lost.  Keep the refund and its point hold
            # durable and quarantine it for an authenticated provider query;
            # never let an ambiguous network outcome recreate a new refund ID.
            refund.status = PaymentRefundStatus.RECONCILIATION_REQUIRED
            cls._finish_provider_command(
                command,
                status=PaymentProviderCommandStatus.UNKNOWN,
                error_code="provider_outcome_unknown",
            )
            cls._ensure_query_refund_command(
                session,
                order=order,
                refund=refund,
                rearm_terminal=True,
            )
            command.next_attempt_at = utcnow() + timedelta(days=3650)
            session.flush()
            return refund
        result = provider_result
        if result.provider != order.provider or (
            refund.provider_refund_id is not None
            and refund.provider_refund_id != result.provider_refund_id
        ):
            refund.status = PaymentRefundStatus.RECONCILIATION_REQUIRED
            cls._finish_provider_command(
                command,
                status=PaymentProviderCommandStatus.UNKNOWN,
                error_code="provider_identity_mismatch",
            )
            cls._ensure_query_refund_command(
                session,
                order=order,
                refund=refund,
            )
            command.next_attempt_at = utcnow() + timedelta(days=3650)
            session.flush()
            return refund
        refund.provider_refund_id = result.provider_refund_id
        if refund.status not in {
            PaymentRefundStatus.SUCCEEDED,
            PaymentRefundStatus.FAILED,
            PaymentRefundStatus.CANCELLED,
        }:
            refund.status = PaymentRefundStatus.PENDING
        cls._finish_provider_command(
            command,
            status=PaymentProviderCommandStatus.SUCCEEDED,
            provider_resource_id=result.provider_refund_id,
            response_payload={
                "provider": result.provider,
                "provider_refund_id": result.provider_refund_id,
                "status": result.status,
            },
        )
        # Signed webhook remains authoritative even for synchronous responses.
        if refund.status == PaymentRefundStatus.PENDING:
            query = cls._ensure_query_refund_command(
                session, order=order, refund=refund, rearm_terminal=True
            )
            query.next_attempt_at = utcnow() + timedelta(minutes=1)
        session.flush()
        return refund

    @classmethod
    def _locked_point_wallet(
        cls,
        session: Session,
        order: PaymentOrder,
    ):
        session.flush()
        if order.company_id is not None:
            wallet = session.scalar(
                select(CompanyPointWalletAccount)
                .where(CompanyPointWalletAccount.company_id == order.company_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        else:
            wallet = session.scalar(
                select(PersonalWalletAccount)
                .where(PersonalWalletAccount.workspace_id == order.personal_workspace_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        if wallet is None:
            raise ConflictError("支付订单积分钱包不存在")
        return wallet

    @classmethod
    def _refund_lot_wallet(
        cls,
        session: Session,
        order: PaymentOrder,
        *,
        require_lot: bool = True,
    ):
        # Match task admission/settlement: a wallet is always locked before any
        # of its lots, including the refund and chargeback projections.
        wallet = cls._locked_point_wallet(session, order)
        if order.company_id is not None:
            lot = session.scalar(
                select(CompanyPointLot)
                .where(CompanyPointLot.payment_order_id == order.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        else:
            lot = session.scalar(
                select(PersonalPointLot)
                .where(PersonalPointLot.payment_order_id == order.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        if wallet is None or (require_lot and lot is None):
            raise ConflictError("支付订单缺少积分履约批次")
        return lot, wallet

    @classmethod
    def _process_refund(
        cls,
        session: Session,
        *,
        event: RefundSucceededEvent | RefundFailedEvent,
        evidence: PaymentWebhookEvidence,
        succeeded: bool,
    ) -> PaymentWebhookReceipt:
        order = cls._locked_order(session, str(event.data.order_id))
        existing_receipt = cls._existing_receipt(
            session, evidence=evidence, merchant_account=order.merchant_account
        )
        if existing_receipt is not None:
            return existing_receipt
        refund = session.scalar(
            select(PaymentRefund)
            .where(
                PaymentRefund.id == str(event.data.refund_id),
                PaymentRefund.order_id == order.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        mismatch = (
            refund is None
            or order.provider != evidence.provider
            or order.provider_order_id != event.data.provider_payment_id
            or refund.provider_refund_id != event.data.provider_refund_id
            or refund.amount_cents != event.data.amount_cents
            or refund.currency != event.data.currency
        )
        if mismatch:
            return cls._append_receipt(
                session,
                event=event,
                evidence=evidence,
                merchant_account=order.merchant_account,
                outcome=PaymentWebhookOutcome.RECONCILIATION_REQUIRED,
                order_id=order.id,
                refund_id=refund.id if refund else None,
                error_code="refund_intent_mismatch",
            )
        assert refund is not None
        if refund.status in {PaymentRefundStatus.SUCCEEDED, PaymentRefundStatus.FAILED}:
            terminal_matches = (
                succeeded and refund.status == PaymentRefundStatus.SUCCEEDED
            ) or (
                not succeeded and refund.status == PaymentRefundStatus.FAILED
            )
            return cls._append_receipt(
                session,
                event=event,
                evidence=evidence,
                merchant_account=order.merchant_account,
                outcome=(
                    PaymentWebhookOutcome.PROCESSED
                    if terminal_matches
                    else PaymentWebhookOutcome.RECONCILIATION_REQUIRED
                ),
                order_id=order.id,
                refund_id=refund.id,
                error_code=None if terminal_matches else "refund_terminal_conflict",
            )
        if succeeded and (
            refund.amount_cents
            > order.captured_amount_cents
            - order.refunded_amount_cents
            - order.disputed_amount_cents
        ):
            refund.status = PaymentRefundStatus.RECONCILIATION_REQUIRED
            order.status = PaymentOrderStatus.RECONCILIATION_REQUIRED
            return cls._append_receipt(
                session,
                event=event,
                evidence=evidence,
                merchant_account=order.merchant_account,
                outcome=PaymentWebhookOutcome.RECONCILIATION_REQUIRED,
                order_id=order.id,
                refund_id=refund.id,
                error_code="refund_exceeds_net_capture",
            )
        if order.purpose == PaymentPurpose.INVOICE_PAYMENT:
            receipt = cls._append_receipt(
                session,
                event=event,
                evidence=evidence,
                merchant_account=order.merchant_account,
                outcome=PaymentWebhookOutcome.PROCESSED,
                order_id=order.id,
                refund_id=refund.id,
            )
            refund.completed_at = _utc(event.occurred_at)
            if not succeeded:
                refund.status = PaymentRefundStatus.FAILED
                session.flush()
                return receipt
            refund.status = PaymentRefundStatus.SUCCEEDED
            order.refunded_amount_cents += refund.amount_cents
            cls._project_order_financial_status(order)
            transaction = PaymentTransaction(
                order_id=order.id,
                refund_id=refund.id,
                webhook_receipt_id=receipt.id,
                provider=evidence.provider,
                provider_transaction_id=event.data.provider_refund_id,
                kind=PaymentTransactionKind.REFUND,
                amount_cents=event.data.amount_cents,
                currency=event.data.currency,
                occurred_at=_utc(event.occurred_at),
            )
            session.add(transaction)
            session.flush()
            assert order.purpose_reference_id is not None
            EnterpriseBillingService.apply_payment_reversal(
                session,
                invoice_id=order.purpose_reference_id,
                payment_transaction_id=transaction.id,
            )
            return receipt
        lot, wallet = cls._refund_lot_wallet(session, order)
        if lot.reversal_reserved_points < refund.points or wallet.reversal_reserved_points < refund.points:
            refund.status = PaymentRefundStatus.RECONCILIATION_REQUIRED
            return cls._append_receipt(
                session,
                event=event,
                evidence=evidence,
                merchant_account=order.merchant_account,
                outcome=PaymentWebhookOutcome.RECONCILIATION_REQUIRED,
                order_id=order.id,
                refund_id=refund.id,
                error_code="refund_hold_missing",
            )
        receipt = cls._append_receipt(
            session,
            event=event,
            evidence=evidence,
            merchant_account=order.merchant_account,
            outcome=PaymentWebhookOutcome.PROCESSED,
            order_id=order.id,
            refund_id=refund.id,
        )
        if succeeded:
            lot.reversal_reserved_points -= refund.points
            lot.reversed_points += refund.points
            wallet.reversal_reserved_points -= refund.points
            refund.status = PaymentRefundStatus.SUCCEEDED
            refund.completed_at = _utc(event.occurred_at)
            order.refunded_amount_cents += refund.amount_cents
            cls._project_order_financial_status(order)
            ledger_kind = (
                PointLedgerKind.REFUND_SETTLE
                if order.company_id is not None
                else LedgerKind.REFUND_SETTLE
            )
            transaction_kind = PaymentTransactionKind.REFUND
        else:
            lot.reversal_reserved_points -= refund.points
            lot.available_points += refund.points
            wallet.reversal_reserved_points -= refund.points
            wallet.available_points += refund.points
            refund.status = PaymentRefundStatus.FAILED
            refund.completed_at = _utc(event.occurred_at)
            ledger_kind = (
                PointLedgerKind.REFUND_RELEASE
                if order.company_id is not None
                else LedgerKind.REFUND_RELEASE
            )
            transaction_kind = None
        ledger_values = dict(
            kind=ledger_kind,
            amount_points=refund.points,
            available_delta_points=0 if succeeded else refund.points,
            reserved_delta_points=0,
            reversal_reserved_delta_points=-refund.points,
            debt_delta_points=0,
            idempotency_key=f"refund:{refund.id}:{'settle' if succeeded else 'release'}",
            payment_order_id=order.id,
            payment_refund_id=refund.id,
            note="verified refund outcome",
        )
        if order.company_id is not None:
            session.add(CompanyPointLedgerEntry(company_id=order.company_id, **ledger_values))
        else:
            session.add(
                PersonalLedgerEntry(
                    workspace_id=order.personal_workspace_id,
                    **ledger_values,
                )
            )
        if transaction_kind is not None:
            session.add(
                PaymentTransaction(
                    order_id=order.id,
                    refund_id=refund.id,
                    webhook_receipt_id=receipt.id,
                    provider=evidence.provider,
                    provider_transaction_id=event.data.provider_refund_id,
                    kind=transaction_kind,
                    amount_cents=event.data.amount_cents,
                    currency=event.data.currency,
                    occurred_at=_utc(event.occurred_at),
                )
            )
        session.flush()
        return receipt

    @classmethod
    def _process_dispute(
        cls,
        session: Session,
        *,
        event: DisputeOpenedEvent | DisputeWonEvent | DisputeLostEvent,
        evidence: PaymentWebhookEvidence,
    ) -> PaymentWebhookReceipt:
        order = cls._locked_order(session, str(event.data.order_id))
        existing_receipt = cls._existing_receipt(
            session, evidence=evidence, merchant_account=order.merchant_account
        )
        if existing_receipt is not None:
            return existing_receipt
        if (
            order.provider != evidence.provider
            or order.provider_order_id != event.data.provider_payment_id
            or order.currency != event.data.currency
            or (
                order.purpose == PaymentPurpose.POINT_PURCHASE
                and event.data.amount_cents % POINT_VALUE_CENTS
            )
        ):
            return cls._append_receipt(
                session,
                event=event,
                evidence=evidence,
                merchant_account=order.merchant_account,
                outcome=PaymentWebhookOutcome.RECONCILIATION_REQUIRED,
                order_id=order.id,
                error_code="dispute_intent_mismatch",
            )
        # Capture recovery owns wallet -> dispute, so every point-dispute path
        # must use order -> wallet -> dispute -> lots as well.
        wallet = (
            cls._locked_point_wallet(session, order)
            if order.purpose == PaymentPurpose.POINT_PURCHASE
            else None
        )
        dispute = session.scalar(
            select(PaymentDispute)
            .where(
                PaymentDispute.provider == evidence.provider,
                PaymentDispute.provider_dispute_id == event.data.provider_dispute_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if isinstance(event, DisputeOpenedEvent):
            if dispute is not None:
                if dispute.order_id != order.id or dispute.amount_cents != event.data.amount_cents:
                    raise ConflictError("拒付编号对应了不同支付事实")
                return cls._append_receipt(
                    session,
                    event=event,
                    evidence=evidence,
                    merchant_account=order.merchant_account,
                    outcome=PaymentWebhookOutcome.PROCESSED,
                    order_id=order.id,
                    dispute_id=dispute.id,
                )
            unresolved_refund_amount = cls._unresolved_refund_amount(
                session,
                order_id=order.id,
            )
            if (
                event.data.amount_cents
                > order.captured_amount_cents
                - order.refunded_amount_cents
                - order.disputed_amount_cents
                - unresolved_refund_amount
            ):
                order.status = PaymentOrderStatus.RECONCILIATION_REQUIRED
                return cls._append_receipt(
                    session,
                    event=event,
                    evidence=evidence,
                    merchant_account=order.merchant_account,
                    outcome=PaymentWebhookOutcome.RECONCILIATION_REQUIRED,
                    order_id=order.id,
                    error_code="dispute_exceeds_net_capture",
                )
            points = (
                event.data.amount_cents // POINT_VALUE_CENTS
                if order.purpose == PaymentPurpose.POINT_PURCHASE
                else 0
            )
            recovered = 0
            debt = 0
            if order.purpose == PaymentPurpose.POINT_PURCHASE:
                # A later purchase may have recovered existing chargeback debt
                # and therefore legitimately produced no (or only a partial)
                # point lot. Reopen that debt instead of rejecting the event.
                lot, wallet = cls._refund_lot_wallet(
                    session,
                    order,
                    require_lot=False,
                )
                recovered = min(
                    lot.available_points if lot is not None else 0,
                    points,
                )
                debt = points - recovered
                if lot is not None:
                    lot.available_points -= recovered
                    lot.reversed_points += recovered
                wallet.available_points -= recovered
                wallet.debt_points += debt
            dispute = PaymentDispute(
                order_id=order.id,
                provider=evidence.provider,
                provider_dispute_id=event.data.provider_dispute_id,
                status=PaymentDisputeStatus.OPEN,
                amount_cents=event.data.amount_cents,
                points=points,
                currency=event.data.currency,
                reason_code=event.data.reason_code or "unspecified",
                recovered_available_points=recovered,
                debt_points=debt,
                opened_at=_utc(event.occurred_at),
            )
            session.add(dispute)
            session.flush()
            receipt = cls._append_receipt(
                session,
                event=event,
                evidence=evidence,
                merchant_account=order.merchant_account,
                outcome=PaymentWebhookOutcome.PROCESSED,
                order_id=order.id,
                dispute_id=dispute.id,
            )
            if order.purpose == PaymentPurpose.POINT_PURCHASE:
                values = dict(
                    kind=(
                        PointLedgerKind.CHARGEBACK
                        if order.company_id is not None
                        else LedgerKind.CHARGEBACK
                    ),
                    amount_points=points,
                    available_delta_points=-recovered,
                    reserved_delta_points=0,
                    reversal_reserved_delta_points=0,
                    debt_delta_points=debt,
                    idempotency_key=f"dispute:{dispute.id}:open",
                    payment_order_id=order.id,
                    payment_dispute_id=dispute.id,
                    note="verified payment dispute opened",
                )
                if order.company_id is not None:
                    session.add(
                        CompanyPointLedgerEntry(company_id=order.company_id, **values)
                    )
                else:
                    session.add(
                        PersonalLedgerEntry(
                            workspace_id=order.personal_workspace_id,
                            **values,
                        )
                    )
            transaction = PaymentTransaction(
                order_id=order.id,
                dispute_id=dispute.id,
                webhook_receipt_id=receipt.id,
                provider=evidence.provider,
                provider_transaction_id=f"{event.data.provider_dispute_id}:opened",
                kind=PaymentTransactionKind.CHARGEBACK,
                amount_cents=event.data.amount_cents,
                currency=event.data.currency,
                occurred_at=_utc(event.occurred_at),
            )
            session.add(transaction)
            order.disputed_amount_cents += event.data.amount_cents
            cls._project_order_financial_status(order)
            session.flush()
            if order.purpose == PaymentPurpose.INVOICE_PAYMENT:
                assert order.purpose_reference_id is not None
                EnterpriseBillingService.apply_payment_reversal(
                    session,
                    invoice_id=order.purpose_reference_id,
                    payment_transaction_id=transaction.id,
                )
            return receipt
        if dispute is None or dispute.order_id != order.id:
            return cls._append_receipt(
                session,
                event=event,
                evidence=evidence,
                merchant_account=order.merchant_account,
                outcome=PaymentWebhookOutcome.RECONCILIATION_REQUIRED,
                order_id=order.id,
                error_code="dispute_open_event_missing",
            )
        if event.data.amount_cents != dispute.amount_cents:
            return cls._append_receipt(
                session,
                event=event,
                evidence=evidence,
                merchant_account=order.merchant_account,
                outcome=PaymentWebhookOutcome.RECONCILIATION_REQUIRED,
                order_id=order.id,
                dispute_id=dispute.id,
                error_code="dispute_resolution_amount_mismatch",
            )
        resolved_status = (
            PaymentDisputeStatus.LOST
            if isinstance(event, DisputeLostEvent)
            else PaymentDisputeStatus.WON
        )
        if dispute.status in {PaymentDisputeStatus.WON, PaymentDisputeStatus.LOST}:
            same_resolution = dispute.status == resolved_status
            return cls._append_receipt(
                session,
                event=event,
                evidence=evidence,
                merchant_account=order.merchant_account,
                outcome=(
                    PaymentWebhookOutcome.PROCESSED
                    if same_resolution
                    else PaymentWebhookOutcome.RECONCILIATION_REQUIRED
                ),
                order_id=order.id,
                dispute_id=dispute.id,
                error_code=None if same_resolution else "dispute_resolution_conflict",
            )
        receipt = cls._append_receipt(
            session,
            event=event,
            evidence=evidence,
            merchant_account=order.merchant_account,
            outcome=PaymentWebhookOutcome.PROCESSED,
            order_id=order.id,
            dispute_id=dispute.id,
        )
        if isinstance(event, DisputeLostEvent):
            dispute.status = PaymentDisputeStatus.LOST
            dispute.closed_at = _utc(event.occurred_at)
            session.flush()
            return receipt

        if order.purpose == PaymentPurpose.INVOICE_PAYMENT:
            transaction = PaymentTransaction(
                order_id=order.id,
                dispute_id=dispute.id,
                webhook_receipt_id=receipt.id,
                provider=evidence.provider,
                provider_transaction_id=f"{event.data.provider_dispute_id}:won",
                kind=PaymentTransactionKind.DISPUTE_REVERSAL,
                amount_cents=event.data.amount_cents,
                currency=event.data.currency,
                occurred_at=_utc(event.occurred_at),
            )
            session.add(transaction)
            dispute.status = PaymentDisputeStatus.WON
            dispute.closed_at = _utc(event.occurred_at)
            order.disputed_amount_cents -= dispute.amount_cents
            cls._project_order_financial_status(order)
            session.flush()
            assert order.purpose_reference_id is not None
            EnterpriseBillingService.apply_payment_transaction(
                session,
                invoice_id=order.purpose_reference_id,
                payment_transaction_id=transaction.id,
            )
            return receipt

        # A won dispute restores entitlement with a new explicit compensation
        # lot; the original chargeback and original lot remain immutable facts.
        if wallet is None:
            raise ConflictError("拒付订单钱包不存在")
        allocations = list(
            session.scalars(
                select(PaymentDisputeDebtRecoveryAllocation)
                .where(
                    PaymentDisputeDebtRecoveryAllocation.dispute_id == dispute.id
                )
                .order_by(PaymentDisputeDebtRecoveryAllocation.created_at, PaymentDisputeDebtRecoveryAllocation.id)
                .with_for_update()
            ).all()
        )
        recovered_after_dispute = sum(
            allocation.recovered_points for allocation in allocations
        )
        if recovered_after_dispute > dispute.debt_points:
            raise ConflictError("拒付债务恢复分配超过原始债务")
        outstanding_debt = dispute.debt_points - recovered_after_dispute
        if wallet.debt_points < outstanding_debt:
            raise ConflictError("钱包拒付债务与逐争议恢复分配无法对账")
        wallet.debt_points -= outstanding_debt
        restored = dispute.recovered_available_points + recovered_after_dispute
        wallet.available_points += restored
        point_lot = None
        if restored:
            lot_values = dict(
                source_kind=PointLotSourceKind.COMPENSATION,
                original_points=restored,
                available_points=restored,
                reserved_points=0,
                reversal_reserved_points=0,
                settled_points=0,
                reversed_points=0,
                cash_basis_cents=restored * POINT_VALUE_CENTS,
                receivable_basis_cents=0,
                subsidy_cents=0,
                idempotency_key=f"dispute:{dispute.id}:won",
            )
            if order.company_id is not None:
                point_lot = CompanyPointLot(company_id=order.company_id, **lot_values)
            else:
                point_lot = PersonalPointLot(
                    workspace_id=order.personal_workspace_id,
                    refundable=False,
                    **lot_values,
                )
            session.add(point_lot)
        values = dict(
            kind=(
                PointLedgerKind.DISPUTE_REVERSAL
                if order.company_id is not None
                else LedgerKind.DISPUTE_REVERSAL
            ),
            amount_points=restored + outstanding_debt,
            available_delta_points=restored,
            reserved_delta_points=0,
            reversal_reserved_delta_points=0,
            debt_delta_points=-outstanding_debt,
            idempotency_key=f"dispute:{dispute.id}:won",
            payment_order_id=order.id,
            payment_dispute_id=dispute.id,
            note="verified payment dispute won",
        )
        ledger_entry = None
        if values["amount_points"]:
            if order.company_id is not None:
                ledger_entry = CompanyPointLedgerEntry(
                    company_id=order.company_id,
                    **values,
                )
            else:
                ledger_entry = PersonalLedgerEntry(
                    workspace_id=order.personal_workspace_id,
                    **values,
                )
            session.add(ledger_entry)
        session.flush()
        if allocations:
            if point_lot is None or ledger_entry is None:
                raise ConflictError("拒付债务恢复补偿缺少积分批次或账本分录")
            for allocation in allocations:
                existing_reversal = session.scalar(
                    select(PaymentDisputeDebtRecoveryReversal).where(
                        PaymentDisputeDebtRecoveryReversal.allocation_id
                        == allocation.id
                    )
                )
                if existing_reversal is not None:
                    if (
                        existing_reversal.dispute_id != dispute.id
                        or existing_reversal.restored_points
                        != allocation.recovered_points
                    ):
                        raise ConflictError("拒付债务恢复反向分录不一致")
                    continue
                session.add(
                    PaymentDisputeDebtRecoveryReversal(
                        allocation_id=allocation.id,
                        dispute_id=dispute.id,
                        restored_points=allocation.recovered_points,
                        company_ledger_entry_id=(
                            ledger_entry.id if order.company_id is not None else None
                        ),
                        personal_ledger_entry_id=(
                            ledger_entry.id if order.company_id is None else None
                        ),
                        company_point_lot_id=(
                            point_lot.id if order.company_id is not None else None
                        ),
                        personal_point_lot_id=(
                            point_lot.id if order.company_id is None else None
                        ),
                        idempotency_key=f"dispute-debt-reversal:{allocation.id}",
                    )
                )
        session.add(
            PaymentTransaction(
                order_id=order.id,
                dispute_id=dispute.id,
                webhook_receipt_id=receipt.id,
                provider=evidence.provider,
                provider_transaction_id=f"{event.data.provider_dispute_id}:won",
                kind=PaymentTransactionKind.DISPUTE_REVERSAL,
                amount_cents=event.data.amount_cents,
                currency=event.data.currency,
                occurred_at=_utc(event.occurred_at),
            )
        )
        dispute.status = PaymentDisputeStatus.WON
        dispute.closed_at = _utc(event.occurred_at)
        order.disputed_amount_cents -= dispute.amount_cents
        cls._project_order_financial_status(order)
        session.flush()
        return receipt

    @classmethod
    def process_webhook(
        cls,
        session: Session,
        *,
        event: PaymentWebhookEvent,
        evidence: PaymentWebhookEvidence,
    ) -> PaymentWebhookReceipt:
        if isinstance(event, PaymentCapturedEvent):
            return cls._process_capture(session, event=event, evidence=evidence)
        if isinstance(
            event,
            (PaymentFailedEvent, PaymentCancelledEvent, PaymentExpiredEvent),
        ):
            return cls._process_payment_terminal(
                session,
                event=event,
                evidence=evidence,
            )
        if isinstance(event, RefundSucceededEvent):
            return cls._process_refund(
                session, event=event, evidence=evidence, succeeded=True
            )
        if isinstance(event, RefundFailedEvent):
            return cls._process_refund(
                session, event=event, evidence=evidence, succeeded=False
            )
        return cls._process_dispute(session, event=event, evidence=evidence)

    @classmethod
    def ingest_webhook_event(
        cls,
        session: Session,
        *,
        event: PaymentWebhookEvent,
        evidence: PaymentWebhookEvidence,
    ) -> PaymentWebhookInboxEvent:
        """Persist verified delivery evidence before any financial projection runs."""

        if (
            event.provider != evidence.provider
            or str(event.event_id) != evidence.event_id
            or event.key_id != evidence.key_id
        ):
            raise ConflictError("支付回调验证证据与事件身份不一致")
        order = session.get(PaymentOrder, str(event.data.order_id))
        if order is None:
            raise NotFoundError("支付回调对应订单不存在")
        if (
            order.provider != evidence.provider
            or order.merchant_account != evidence.merchant_account
        ):
            raise ConflictError("支付回调通道或签名商户与订单不一致")
        payload_json = event.model_dump(mode="json")
        existing = session.scalar(
            select(PaymentWebhookInboxEvent).where(
                PaymentWebhookInboxEvent.provider == evidence.provider,
                PaymentWebhookInboxEvent.merchant_account == order.merchant_account,
                PaymentWebhookInboxEvent.provider_event_id == evidence.event_id,
            )
        )
        if existing is not None:
            if (
                existing.payload_sha256 != evidence.payload_sha256
                or existing.event_type != event.type
                or existing.signature_key_id != evidence.key_id
                or existing.payload_json != payload_json
            ):
                raise ConflictError("支付回调事件编号对应了不同载荷")
            return existing
        inbox = PaymentWebhookInboxEvent(
            provider=evidence.provider,
            merchant_account=order.merchant_account,
            provider_event_id=evidence.event_id,
            event_type=event.type,
            payload_sha256=evidence.payload_sha256,
            payload_json=payload_json,
            signature_key_id=evidence.key_id,
            signature_timestamp=_utc(evidence.delivery_timestamp),
            signature_verified_at=utcnow(),
            provider_occurred_at=_utc(event.occurred_at),
            status=PaymentWebhookInboxStatus.RECEIVED,
            attempt_count=0,
            next_attempt_at=utcnow(),
            received_at=utcnow(),
        )
        session.add(inbox)
        session.flush()
        return inbox

    @classmethod
    def _adopt_verified_webhook_resource(
        cls,
        session: Session,
        *,
        event: PaymentWebhookEvent,
        order: PaymentOrder,
    ) -> None:
        """Close the callback-before-create-response race using signed identities."""

        if isinstance(
            event,
            (
                PaymentCapturedEvent,
                PaymentFailedEvent,
                PaymentCancelledEvent,
                PaymentExpiredEvent,
            ),
        ):
            provider_payment_id = event.data.provider_payment_id
            if order.provider_order_id is None:
                if isinstance(event, PaymentCapturedEvent) and (
                    event.data.amount_cents != order.amount_cents
                    or event.data.currency != order.currency
                ):
                    return
                order.provider_order_id = provider_payment_id
                order.status = PaymentOrderStatus.PENDING
                create_command = cls._ensure_create_payment_command(session, order)
                # Never UPDATE command after locking order: the worker owns
                # command -> order and absorbs this already-verified cash fact.
                attempt = cls._attempt_for_provider_query(
                    session,
                    order=order,
                    create_command=create_command,
                )
                attempt.provider_attempt_id = provider_payment_id
            return

        if isinstance(event, (RefundSucceededEvent, RefundFailedEvent)):
            refund = session.scalar(
                select(PaymentRefund)
                .where(PaymentRefund.id == str(event.data.refund_id))
                .with_for_update()
            )
            if (
                refund is None
                or refund.order_id != order.id
                or refund.provider != order.provider
                or event.data.provider_payment_id != order.provider_order_id
                or event.data.amount_cents != refund.amount_cents
                or event.data.currency != refund.currency
            ):
                return
            if refund.provider_refund_id is None:
                refund.provider_refund_id = event.data.provider_refund_id
                cls._ensure_create_refund_command(
                    session,
                    order=order,
                    refund=refund,
                )

    @staticmethod
    def _webhook_inbox_claim_statement(now: datetime):
        return (
            select(PaymentWebhookInboxEvent)
            .where(
                or_(
                    (
                        PaymentWebhookInboxEvent.status.in_(
                            {PaymentWebhookInboxStatus.RECEIVED, PaymentWebhookInboxStatus.BLOCKED}
                        )
                    )
                    & (PaymentWebhookInboxEvent.next_attempt_at <= now),
                    (
                        PaymentWebhookInboxEvent.status
                        == PaymentWebhookInboxStatus.PROCESSING
                    )
                    & (PaymentWebhookInboxEvent.lease_expires_at <= now),
                )
            )
            .order_by(
                PaymentWebhookInboxEvent.next_attempt_at,
                PaymentWebhookInboxEvent.received_at,
                PaymentWebhookInboxEvent.id,
            )
            .limit(1)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )

    @classmethod
    def claim_next_webhook_inbox_event(
        cls,
        session: Session,
    ) -> PaymentWebhookInboxEvent | None:
        session.flush()
        return session.scalar(cls._webhook_inbox_claim_statement(utcnow()))

    @classmethod
    def process_inbox_event(
        cls,
        session: Session,
        *,
        inbox_id: str,
    ) -> tuple[PaymentWebhookInboxEvent, PaymentWebhookReceipt | None]:
        session.flush()
        inbox = session.scalar(
            select(PaymentWebhookInboxEvent)
            .where(PaymentWebhookInboxEvent.id == inbox_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if inbox is None:
            raise NotFoundError("支付回调收件事件不存在")
        if inbox.status == PaymentWebhookInboxStatus.PROCESSED:
            receipt = (
                session.get(PaymentWebhookReceipt, inbox.processed_receipt_id)
                if inbox.processed_receipt_id is not None
                else None
            )
            if receipt is None:
                raise ConflictError("支付回调收件状态缺少不可变回执")
            return inbox, receipt
        if inbox.status == PaymentWebhookInboxStatus.DEAD:
            return inbox, None
        if inbox.status == PaymentWebhookInboxStatus.PROCESSING:
            if (
                inbox.lease_expires_at is not None
                and _utc(inbox.lease_expires_at) > utcnow()
            ):
                return inbox, None
        try:
            event = parse_payment_webhook_payload(inbox.payload_json)
        except DomainError as exc:
            inbox.status = PaymentWebhookInboxStatus.DEAD
            inbox.last_error_code = exc.code
            inbox.lease_token = None
            inbox.lease_expires_at = None
            inbox.processed_at = utcnow()
            session.flush()
            return inbox, None
        if (
            event.provider != inbox.provider
            or str(event.event_id) != inbox.provider_event_id
            or event.key_id != inbox.signature_key_id
            or event.type != inbox.event_type
        ):
            inbox.status = PaymentWebhookInboxStatus.DEAD
            inbox.last_error_code = "inbox_identity_mismatch"
            inbox.processed_at = utcnow()
            session.flush()
            return inbox, None

        if isinstance(event, (DisputeOpenedEvent, RefundSucceededEvent, RefundFailedEvent)):
            capture_amount = session.scalar(
                select(PaymentOrder.captured_amount_cents).where(
                    PaymentOrder.id == str(event.data.order_id)
                )
            )
            if not capture_amount:
                inbox.status = PaymentWebhookInboxStatus.BLOCKED
                inbox.blocked_on = f"capture:{event.data.order_id}"
                inbox.last_error_code = "payment_capture_event_missing"
                inbox.next_attempt_at = utcnow() + timedelta(minutes=1)
                inbox.lease_token = None
                inbox.lease_expires_at = None
                session.flush()
                return inbox, None
        if isinstance(event, (DisputeWonEvent, DisputeLostEvent)):
            dispute = session.scalar(
                select(PaymentDispute).where(
                    PaymentDispute.provider == inbox.provider,
                    PaymentDispute.provider_dispute_id
                    == event.data.provider_dispute_id,
                )
            )
            if dispute is None:
                inbox.status = PaymentWebhookInboxStatus.BLOCKED
                inbox.blocked_on = (
                    f"dispute:{inbox.provider}:{event.data.provider_dispute_id}"
                )
                inbox.last_error_code = "dispute_open_event_missing"
                inbox.next_attempt_at = utcnow() + timedelta(days=3650)
                inbox.lease_token = None
                inbox.lease_expires_at = None
                session.flush()
                return inbox, None

        inbox.status = PaymentWebhookInboxStatus.PROCESSING
        inbox.attempt_count += 1
        inbox.lease_token = uuid4().hex
        inbox.lease_expires_at = utcnow() + timedelta(minutes=2)
        inbox.blocked_on = None
        inbox.last_error_code = None
        session.flush()
        evidence = PaymentWebhookEvidence(
            provider=inbox.provider,
            merchant_account=inbox.merchant_account,
            key_id=inbox.signature_key_id,
            event_id=inbox.provider_event_id,
            delivery_timestamp=_utc(inbox.signature_timestamp),
            payload_sha256=inbox.payload_sha256,
        )
        try:
            with session.begin_nested():
                order = cls._locked_order(session, str(event.data.order_id))
                cls._adopt_verified_webhook_resource(
                    session,
                    event=event,
                    order=order,
                )
                receipt = cls.process_webhook(
                    session,
                    event=event,
                    evidence=evidence,
                )
        except DomainError as exc:
            inbox.status = PaymentWebhookInboxStatus.BLOCKED
            inbox.last_error_code = exc.code
            inbox.next_attempt_at = utcnow() + timedelta(minutes=1)
            inbox.lease_token = None
            inbox.lease_expires_at = None
            session.flush()
            return inbox, None

        inbox.status = PaymentWebhookInboxStatus.PROCESSED
        inbox.processed_receipt_id = receipt.id
        inbox.processed_at = utcnow()
        inbox.lease_token = None
        inbox.lease_expires_at = None
        inbox.last_error_code = receipt.error_code
        if isinstance(event, DisputeOpenedEvent):
            blocked_on = f"dispute:{inbox.provider}:{event.data.provider_dispute_id}"
            blocked_events = list(
                session.scalars(
                    select(PaymentWebhookInboxEvent)
                    .where(
                        PaymentWebhookInboxEvent.status
                        == PaymentWebhookInboxStatus.BLOCKED,
                        PaymentWebhookInboxEvent.blocked_on == blocked_on,
                    )
                    .with_for_update()
                ).all()
            )
            for blocked in blocked_events:
                blocked.status = PaymentWebhookInboxStatus.RECEIVED
                blocked.blocked_on = None
                blocked.last_error_code = None
                blocked.next_attempt_at = utcnow()
        session.flush()
        return inbox, receipt

    @staticmethod
    def _validate_mandate_consent(
        *,
        consent_version: str,
        consent_sha256: str,
        consented_at: datetime,
    ) -> datetime:
        if (
            not isinstance(consent_version, str)
            or not consent_version.strip()
            or consent_version != consent_version.strip()
            or len(consent_version) > 80
        ):
            raise ConflictError("自动扣款授权条款版本无效")
        if (
            not isinstance(consent_sha256, str)
            or len(consent_sha256) != 64
            or consent_sha256.lower() != consent_sha256
        ):
            raise ConflictError("自动扣款授权证据摘要无效")
        try:
            int(consent_sha256, 16)
        except ValueError:
            raise ConflictError("自动扣款授权证据摘要无效") from None
        if consented_at.tzinfo is None or consented_at.utcoffset() is None:
            raise ConflictError("自动扣款授权时间必须包含时区")
        normalized = _utc(consented_at)
        if normalized > utcnow() + timedelta(minutes=5):
            raise ConflictError("自动扣款授权时间不能位于未来")
        return normalized

    @classmethod
    def create_pending_payment_mandate(
        cls,
        session: Session,
        *,
        company_id: str | None,
        workspace_id: str | None,
        user_id: str,
        provider: str,
        merchant_account: str,
        consent_version: str,
        consent_sha256: str,
        consented_at: datetime,
        idempotency_key: str,
    ) -> tuple[PaymentMandate, bool]:
        """Persist owner consent as PENDING; only an internal verifier may activate it."""

        cls._scope_filter(company_id=company_id, workspace_id=workspace_id)
        if not 8 <= len(idempotency_key) <= 160:
            raise ConflictError("自动扣款授权幂等键无效")
        if not provider or len(provider) > 64:
            raise ConflictError("自动扣款支付通道无效")
        if not merchant_account or len(merchant_account) > 120:
            raise ConflictError("自动扣款商户账户无效")
        normalized_consented_at = cls._validate_mandate_consent(
            consent_version=consent_version,
            consent_sha256=consent_sha256,
            consented_at=consented_at,
        )
        if company_id is not None:
            company = session.scalar(
                select(Company)
                .where(Company.id == company_id)
                .with_for_update(key_share=True)
            )
            wallet = session.scalar(
                select(CompanyPointWalletAccount)
                .where(CompanyPointWalletAccount.company_id == company_id)
                .with_for_update()
            )
            if company is None or company.billing_version != 2 or wallet is None:
                raise ConflictError("企业尚未启用 POINT/v2 自动充值")
        else:
            wallet = session.scalar(
                select(PersonalWalletAccount)
                .where(PersonalWalletAccount.workspace_id == workspace_id)
                .with_for_update()
            )
            if wallet is None:
                raise NotFoundError("个人积分钱包不存在")
        existing = session.scalar(
            select(PaymentMandate).where(
                PaymentMandate.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            if (
                existing.company_id != company_id
                or existing.personal_workspace_id != workspace_id
                or existing.created_by_user_id != user_id
                or existing.provider != provider
                or existing.merchant_account != merchant_account
                or existing.consent_version != consent_version
                or existing.consent_sha256 != consent_sha256
                or existing.consented_at is None
                or _utc(existing.consented_at) != normalized_consented_at
            ):
                raise ConflictError("自动扣款授权幂等键已绑定不同意图")
            return existing, False
        mandate = PaymentMandate(
            company_id=company_id,
            personal_workspace_id=workspace_id,
            provider=provider,
            merchant_account=merchant_account,
            status=PaymentMandateStatus.PENDING,
            provider_customer_reference=None,
            provider_payment_method_reference=None,
            provider_mandate_reference=None,
            consent_version=consent_version,
            consent_sha256=consent_sha256,
            consented_at=normalized_consented_at,
            verified_at=None,
            revoked_at=None,
            created_by_user_id=user_id,
            idempotency_key=idempotency_key,
        )
        session.add(mandate)
        session.flush()
        return mandate, True

    @classmethod
    def activate_payment_mandate(
        cls,
        session: Session,
        *,
        mandate_id: str,
        provider: str,
        merchant_account: str,
        consent_version: str,
        consent_sha256: str,
        provider_customer_reference: str,
        provider_payment_method_reference: str,
        provider_mandate_reference: str,
        verified_at: datetime,
    ) -> tuple[PaymentMandate, bool]:
        """Apply a PSP/server-verified activation; customer APIs cannot call this."""

        session.flush()
        mandate = session.scalar(
            select(PaymentMandate)
            .where(PaymentMandate.id == mandate_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if mandate is None:
            raise NotFoundError("自动扣款授权不存在")
        if (
            mandate.provider != provider
            or mandate.merchant_account != merchant_account
            or mandate.consent_version != consent_version
            or mandate.consent_sha256 != consent_sha256
        ):
            raise ConflictError("自动扣款激活证据与待授权意图不一致")
        if verified_at.tzinfo is None or verified_at.utcoffset() is None:
            raise ConflictError("自动扣款验证时间必须包含时区")
        normalized_verified_at = _utc(verified_at)
        if (
            mandate.consented_at is None
            or normalized_verified_at < _utc(mandate.consented_at)
            or normalized_verified_at > utcnow() + timedelta(minutes=5)
        ):
            raise ConflictError("自动扣款验证时间无效")
        references = (
            provider_customer_reference,
            provider_payment_method_reference,
            provider_mandate_reference,
        )
        if any(
            not isinstance(reference, str)
            or not reference
            or len(reference) > 128
            or any(
                not (
                    character.isascii()
                    and (character.isalnum() or character in "._:-")
                )
                for character in reference
            )
            for reference in references
        ):
            raise ConflictError("自动扣款支付通道引用无效")
        if mandate.status == PaymentMandateStatus.ACTIVE:
            if references != (
                mandate.provider_customer_reference,
                mandate.provider_payment_method_reference,
                mandate.provider_mandate_reference,
            ):
                raise ConflictError("已激活授权不能绑定另一组支付通道引用")
            return mandate, False
        if mandate.status != PaymentMandateStatus.PENDING:
            raise ConflictError("终态自动扣款授权不能重新激活")
        mandate.provider_customer_reference = provider_customer_reference
        mandate.provider_payment_method_reference = provider_payment_method_reference
        mandate.provider_mandate_reference = provider_mandate_reference
        mandate.verified_at = normalized_verified_at
        mandate.status = PaymentMandateStatus.ACTIVE
        session.flush()
        return mandate, True

    @classmethod
    def revoke_payment_mandate(
        cls,
        session: Session,
        *,
        mandate_id: str,
        company_id: str | None,
        workspace_id: str | None,
    ) -> tuple[PaymentMandate, bool]:
        """Revoke an owner-scoped mandate and disable every bound recharge rule."""

        session.flush()
        cls._scope_filter(company_id=company_id, workspace_id=workspace_id)
        mandate = session.get(PaymentMandate, mandate_id)
        if (
            mandate is None
            or mandate.company_id != company_id
            or mandate.personal_workspace_id != workspace_id
        ):
            raise NotFoundError("自动扣款授权不存在")
        if company_id is not None:
            session.scalar(
                select(CompanyPointWalletAccount)
                .where(CompanyPointWalletAccount.company_id == company_id)
                .with_for_update()
            )
        else:
            session.scalar(
                select(PersonalWalletAccount)
                .where(PersonalWalletAccount.workspace_id == workspace_id)
                .with_for_update()
            )
        rules = list(
            session.scalars(
                select(AutoRechargeRule)
                .where(AutoRechargeRule.mandate_id == mandate.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        )
        mandate = session.scalar(
            select(PaymentMandate)
            .where(PaymentMandate.id == mandate_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert mandate is not None
        for rule in rules:
            rule.enabled = False
        if mandate.status == PaymentMandateStatus.REVOKED:
            session.flush()
            return mandate, False
        if mandate.status == PaymentMandateStatus.FAILED:
            raise ConflictError("失败的自动扣款授权不能撤销")
        mandate.status = PaymentMandateStatus.REVOKED
        mandate.revoked_at = utcnow()
        session.flush()
        return mandate, True

    @classmethod
    def configure_auto_recharge(
        cls,
        session: Session,
        *,
        company_id: str | None,
        workspace_id: str | None,
        mandate_id: str,
        user_id: str,
        threshold_points: int,
        top_up_points: int,
        monthly_cap_cents: int,
        cooldown_seconds: int,
        enabled: bool,
    ) -> AutoRechargeRule:
        if (
            isinstance(threshold_points, bool)
            or not isinstance(threshold_points, int)
            or not 0 <= threshold_points <= MAX_POINTS
            or isinstance(top_up_points, bool)
            or not isinstance(top_up_points, int)
            or not 0 < top_up_points <= MAX_PURCHASE_POINTS
        ):
            raise ConflictError("自动充值阈值或充值积分无效")
        if (
            isinstance(monthly_cap_cents, bool)
            or not isinstance(monthly_cap_cents, int)
            or not 0 < monthly_cap_cents <= MAX_PAYMENT_AMOUNT_CENTS
            or isinstance(cooldown_seconds, bool)
            or not isinstance(cooldown_seconds, int)
            or not 60 <= cooldown_seconds <= 2_592_000
            or not isinstance(enabled, bool)
        ):
            raise ConflictError("自动充值金额上限或冷却时间无效")
        if monthly_cap_cents < top_up_points * POINT_VALUE_CENTS:
            raise ConflictError("自动充值月上限低于单次充值金额")
        scope_filter = cls._scope_filter(
            company_id=company_id,
            workspace_id=workspace_id,
        )
        # Reuse scope validation and wallet locking without creating an order.
        session.flush()
        if company_id is not None:
            wallet = session.scalar(
                select(CompanyPointWalletAccount)
                .where(CompanyPointWalletAccount.company_id == company_id)
                .with_for_update()
            )
            rule_filter = AutoRechargeRule.company_id == company_id
        else:
            wallet = session.scalar(
                select(PersonalWalletAccount)
                .where(PersonalWalletAccount.workspace_id == workspace_id)
                .with_for_update()
            )
            rule_filter = AutoRechargeRule.personal_workspace_id == workspace_id
        del scope_filter
        if wallet is None:
            raise NotFoundError("积分钱包不存在")
        rule = session.scalar(
            select(AutoRechargeRule).where(rule_filter).with_for_update()
            .execution_options(populate_existing=True)
        )
        mandate = session.scalar(
            select(PaymentMandate)
            .where(PaymentMandate.id == mandate_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            mandate is None
            or mandate.company_id != company_id
            or mandate.personal_workspace_id != workspace_id
        ):
            raise NotFoundError("自动充值授权不存在")
        if enabled and (
            mandate.status != PaymentMandateStatus.ACTIVE
            or not mandate.provider_customer_reference
            or not mandate.provider_payment_method_reference
            or not mandate.provider_mandate_reference
            or mandate.consented_at is None
            or mandate.verified_at is None
            or mandate.revoked_at is not None
        ):
            raise ConflictError("自动充值授权不是可扣款状态")
        if not mandate.provider_customer_reference:
            raise ConflictError("自动充值授权缺少支付客户引用")
        if rule is None:
            rule = AutoRechargeRule(
                company_id=company_id,
                personal_workspace_id=workspace_id,
                provider=mandate.provider,
                merchant_account=mandate.merchant_account,
                provider_customer_reference=mandate.provider_customer_reference,
                mandate_id=mandate.id,
                created_by_user_id=user_id,
                threshold_points=threshold_points,
                top_up_points=top_up_points,
                monthly_cap_cents=monthly_cap_cents,
                cooldown_seconds=cooldown_seconds,
                enabled=enabled,
            )
            session.add(rule)
        else:
            rule.provider = mandate.provider
            rule.merchant_account = mandate.merchant_account
            rule.provider_customer_reference = mandate.provider_customer_reference
            rule.mandate_id = mandate.id
            rule.created_by_user_id = user_id
            rule.threshold_points = threshold_points
            rule.top_up_points = top_up_points
            rule.monthly_cap_cents = monthly_cap_cents
            rule.cooldown_seconds = cooldown_seconds
            rule.enabled = enabled
        rule.next_check_at = utcnow()
        session.flush()
        return rule

    @classmethod
    def trigger_auto_recharge(
        cls,
        session: Session,
        *,
        rule_id: str,
        user_id: str,
        now: datetime | None = None,
        scheduler_check: bool = False,
    ) -> tuple[PaymentOrder | None, bool]:
        session.flush()
        current = _utc(now or utcnow())
        snapshot = session.get(AutoRechargeRule, rule_id)
        if snapshot is None:
            raise NotFoundError("自动充值规则不存在")
        # Reconfiguration, triggering and revocation all lock wallet -> rule ->
        # mandate.  Company lifecycle serialization is non-key-changing.
        if snapshot.company_id is not None:
            company = session.scalar(
                select(Company)
                .where(Company.id == snapshot.company_id)
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            )
            if company is None or company.billing_version != 2:
                raise ConflictError("企业尚未启用 POINT/v2 自动充值")
            wallet = session.scalar(
                select(CompanyPointWalletAccount)
                .where(CompanyPointWalletAccount.company_id == snapshot.company_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            scope_filter = PaymentOrder.company_id == snapshot.company_id
        else:
            wallet = session.scalar(
                select(PersonalWalletAccount)
                .where(PersonalWalletAccount.workspace_id == snapshot.personal_workspace_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            scope_filter = PaymentOrder.personal_workspace_id == snapshot.personal_workspace_id
        if wallet is None:
            raise NotFoundError("自动充值钱包不存在")
        rule = session.scalar(
            select(AutoRechargeRule)
            .where(AutoRechargeRule.id == rule_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if rule is None:
            raise NotFoundError("自动充值规则不存在")
        if scheduler_check:
            if rule.next_check_at is not None and _utc(rule.next_check_at) > current:
                return None, False
            # Persist scheduling independently from an actual charge attempt;
            # otherwise permanently idle rules can starve the next batch.
            rule.next_check_at = current + timedelta(minutes=1)
        if not rule.enabled:
            return None, False
        mandate = session.scalar(
            select(PaymentMandate)
            .where(PaymentMandate.id == rule.mandate_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            mandate is None
            or mandate.status != PaymentMandateStatus.ACTIVE
            or mandate.company_id != rule.company_id
            or mandate.personal_workspace_id != rule.personal_workspace_id
            or mandate.provider != rule.provider
            or mandate.merchant_account != rule.merchant_account
            or mandate.provider_customer_reference
            != rule.provider_customer_reference
            or not mandate.provider_payment_method_reference
            or not mandate.provider_mandate_reference
            or mandate.consented_at is None
            or mandate.verified_at is None
            or mandate.revoked_at is not None
        ):
            rule.enabled = False
            session.flush()
            return None, False
        if wallet.available_points > rule.threshold_points:
            return None, False
        if (
            rule.last_attempt_at is not None
            and _utc(rule.last_attempt_at) + timedelta(seconds=rule.cooldown_seconds) > current
        ):
            return None, False
        pending = session.scalar(
            select(PaymentOrder.id)
            .where(
                scope_filter,
                PaymentOrder.automatic.is_(True),
                PaymentOrder.status.in_(_NONTERMINAL_ORDER_STATUSES),
            )
            .limit(1)
        )
        if pending is not None:
            return None, False
        month_start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        captured_this_month = int(
            session.scalar(
                select(func.coalesce(func.sum(PaymentOrder.captured_amount_cents), 0)).where(
                    scope_filter,
                    PaymentOrder.automatic.is_(True),
                    PaymentOrder.captured_at >= month_start,
                    PaymentOrder.captured_at < current + timedelta(microseconds=1),
                )
            )
            or 0
        )
        if captured_this_month + rule.top_up_points * POINT_VALUE_CENTS > rule.monthly_cap_cents:
            return None, False
        trigger_key = f"{rule.id}:{current.strftime('%Y%m%d%H')}:{wallet.available_points}"
        existing_execution = session.scalar(
            select(AutoRechargeExecution).where(
                AutoRechargeExecution.rule_id == rule.id,
                AutoRechargeExecution.trigger_key == trigger_key,
            )
        )
        if existing_execution is not None:
            return session.get(PaymentOrder, existing_execution.payment_order_id), False
        order, _ = cls.create_point_order(
            session,
            company_id=rule.company_id,
            workspace_id=rule.personal_workspace_id,
            user_id=user_id,
            points=rule.top_up_points,
            provider=rule.provider,
            merchant_account=rule.merchant_account,
            idempotency_key=f"auto:{trigger_key}"[:120],
            automatic=True,
            provider_customer_reference=rule.provider_customer_reference,
            payment_mandate_id=mandate.id,
        )
        session.add(
            AutoRechargeExecution(
                rule_id=rule.id,
                payment_order_id=order.id,
                trigger_key=trigger_key,
                observed_available_points=wallet.available_points,
            )
        )
        rule.last_attempt_at = current
        session.flush()
        return order, True
