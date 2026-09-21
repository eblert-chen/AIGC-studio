from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import inspect
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

from platform_api.models import (
    AccountsReceivableLedgerEntry,
    AutoRechargeExecution,
    AutoRechargeRule,
    BillingUnit,
    Company,
    CompanyBillingAccount,
    CompanyInvoiceLine,
    CompanyPointLedgerEntry,
    CompanyPointLot,
    CompanyPointWalletAccount,
    EnterpriseInvoiceStatus,
    GenerationTask,
    LedgerKind,
    ModelDefinition,
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
    PaymentRefund,
    PaymentRefundStatus,
    PaymentTransaction,
    PaymentTransactionKind,
    PaymentWebhookInboxEvent,
    PaymentWebhookInboxStatus,
    PaymentWebhookOutcome,
    PaymentWebhookReceipt,
    PersonalLedgerEntry,
    PersonalPointLot,
    PersonalWalletAccount,
    PointLedgerKind,
    PointLotSourceKind,
    PointLotSettlementValueAllocation,
    TaskPointLotAllocation,
    TaskStatus,
    User,
    UserAccountType,
    new_id,
)
from platform_api.payment_providers import (
    DeterministicFakePaymentProvider,
    PaymentProviderRegistry,
    QueryPaymentResult,
    QueryRefundResult,
)
from platform_api.services.commercial_payments import CommercialPaymentService
from platform_api.services.enterprise_billing import EnterpriseBillingService
from platform_api.services.errors import ConflictError
from platform_api.services.payment_webhooks import (
    DisputeOpenedEvent,
    DisputeWonEvent,
    PaymentCapturedEvent,
    PaymentExpiredEvent,
    PaymentWebhookEvidence,
    PaymentWebhookVerifier,
    PaymentWebhookVerifierRegistry,
    RefundFailedEvent,
    RefundSucceededEvent,
    payment_webhook_signing_input,
)
from platform_api.services.personal import PersonalWorkspaceService
from platform_api.services.personal_billing import PersonalWalletService


PROVIDER = "deterministic-test"
MERCHANT = "merchant-cny-main"
KEY_ID = "payment-key-2026-08"
SECRET = "commercial-payment-webhook-secret-with-32-bytes"
WEBHOOK_CLOCK = 2_000_000_000
OCCURRED_AT = datetime(2033, 5, 18, 3, 33, 20, tzinfo=timezone.utc)


def _seed_personal(session):
    suffix = uuid4().hex
    user = User(
        email=f"commercial-{suffix}@example.test",
        display_name="Commercial payer",
        account_type=UserAccountType.PERSONAL,
    )
    session.add(user)
    session.flush()
    workspace = PersonalWorkspaceService.ensure(session, user_id=user.id)
    session.flush()
    return user, workspace


def _fake() -> DeterministicFakePaymentProvider:
    return DeterministicFakePaymentProvider(enabled_for_tests=True)


def _active_mandate(
    session,
    *,
    user_id: str,
    company_id: str | None = None,
    workspace_id: str | None = None,
) -> PaymentMandate:
    suffix = uuid4().hex
    mandate = PaymentMandate(
        company_id=company_id,
        personal_workspace_id=workspace_id,
        provider=PROVIDER,
        merchant_account=MERCHANT,
        status=PaymentMandateStatus.ACTIVE,
        provider_customer_reference=f"customer-{suffix}",
        provider_payment_method_reference=f"method-{suffix}",
        provider_mandate_reference=f"mandate-{suffix}",
        consent_version="v1",
        consent_sha256=hashlib.sha256(f"consent-{suffix}".encode()).hexdigest(),
        consented_at=OCCURRED_AT,
        verified_at=OCCURRED_AT,
        revoked_at=None,
        created_by_user_id=user_id,
        idempotency_key=f"mandate:{suffix}",
    )
    session.add(mandate)
    session.flush()
    return mandate


class _AcceptedRefundThenTimeout:
    provider_key = PROVIDER
    merchant_account = MERCHANT
    test_only = True

    def __init__(self) -> None:
        self.requests = []
        self.query_requests = []

    async def create_payment(self, request):  # pragma: no cover - refund-only double.
        raise AssertionError("unexpected payment request")

    async def create_refund(self, request):
        self.requests.append(request)
        raise TimeoutError("provider accepted the refund but its response was lost")

    async def query_payment(self, request):  # pragma: no cover - refund-only double.
        raise AssertionError("unexpected payment query")

    async def query_refund(self, request):
        self.query_requests.append(request)
        original = self.requests[0]
        return QueryRefundResult(
            provider=self.provider_key,
            found=True,
            provider_refund_id="re_timeout_accepted",
            status="succeeded",
            amount_cents=original.amount_cents,
            currency=original.currency,
            occurred_at=OCCURRED_AT + timedelta(minutes=1),
        )


class _AcceptedPaymentThenTimeout:
    provider_key = PROVIDER
    merchant_account = MERCHANT
    test_only = True

    def __init__(self) -> None:
        self.requests = []
        self.query_requests = []

    async def create_payment(self, request):
        self.requests.append(request)
        raise TimeoutError("provider accepted the payment but its response was lost")

    async def query_payment(self, request):
        self.query_requests.append(request)
        original = self.requests[0]
        return QueryPaymentResult(
            provider=self.provider_key,
            found=True,
            provider_payment_id="pay_timeout_accepted",
            status="captured",
            amount_cents=original.amount_cents,
            currency=original.currency,
            occurred_at=OCCURRED_AT,
        )

    async def create_refund(self, request):  # pragma: no cover - payment-only double.
        raise AssertionError("unexpected refund request")

    async def query_refund(self, request):  # pragma: no cover - payment-only double.
        raise AssertionError("unexpected refund query")


def _evidence(event, *, payload_sha256: str | None = None) -> PaymentWebhookEvidence:
    return PaymentWebhookEvidence(
        provider=PROVIDER,
        merchant_account=MERCHANT,
        key_id=KEY_ID,
        event_id=str(event.event_id),
        delivery_timestamp=OCCURRED_AT,
        payload_sha256=payload_sha256
        or hashlib.sha256(
            json.dumps(
                event.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
    )


def _captured_event(
    order: PaymentOrder,
    *,
    event_id: str | None = None,
    amount_cents: int | None = None,
    occurred_at: datetime = OCCURRED_AT,
):
    return PaymentCapturedEvent.model_validate(
        {
            "api_version": "v1",
            "schema_version": 1,
            "event_id": event_id or str(uuid4()),
            "provider": PROVIDER,
            "key_id": KEY_ID,
            "occurred_at": occurred_at,
            "type": "payment.captured",
            "data": {
                "order_id": order.id,
                "provider_payment_id": order.provider_order_id,
                "amount_cents": amount_cents or order.amount_cents,
                "currency": "CNY",
            },
        }
    )


@contextmanager
def _payment_session(app):
    # Provider workers commit a lease before network I/O, so tests must not wrap
    # that lifecycle in Session.begin()'s single fixed transaction context.
    with app.state.session_factory() as session:
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise


async def _run_command(session, *, operation, provider, order_id=None, refund_id=None):
    session.commit()
    statement = select(PaymentProviderCommand).where(
        PaymentProviderCommand.operation == operation
    )
    if refund_id is not None:
        statement = statement.where(PaymentProviderCommand.refund_id == refund_id)
    else:
        statement = statement.where(PaymentProviderCommand.order_id == order_id)
    command = session.scalar(statement)
    assert command is not None
    target_order_id = command.order_id
    if command.status == PaymentProviderCommandStatus.SUCCEEDED:
        return (
            session.get(PaymentRefund, refund_id)
            if refund_id is not None
            else session.get(PaymentOrder, target_order_id)
        )
    target_command_id = command.id
    session.rollback()
    claimed = CommercialPaymentService.claim_next_provider_command(session)
    assert claimed is not None and claimed.id == target_command_id
    command_id = claimed.id
    lease_token = claimed.lease_token
    assert lease_token is not None
    session.commit()
    await CommercialPaymentService.execute_claimed_provider_command(
        session,
        command_id=command_id,
        lease_token=lease_token,
        provider=provider,
    )
    session.commit()
    return (
        session.get(PaymentRefund, refund_id)
        if refund_id is not None
        else session.get(PaymentOrder, target_order_id)
    )


async def _dispatch_order(session, *, order_id, provider):
    return await _run_command(
        session,
        operation=PaymentProviderCommandOperation.CREATE_PAYMENT,
        order_id=order_id,
        provider=provider,
    )


async def _dispatch_refund(session, *, refund_id, provider):
    return await _run_command(
        session,
        operation=PaymentProviderCommandOperation.CREATE_REFUND,
        refund_id=refund_id,
        provider=provider,
    )


async def _reconcile_payment(session, *, order_id, provider):
    return await _run_command(
        session,
        operation=PaymentProviderCommandOperation.QUERY_PAYMENT,
        order_id=order_id,
        provider=provider,
    )


async def _reconcile_refund(session, *, refund_id, provider):
    return await _run_command(
        session,
        operation=PaymentProviderCommandOperation.QUERY_REFUND,
        refund_id=refund_id,
        provider=provider,
    )


def _create_dispatched_order(session, *, workspace_id: str, user_id: str, points: int = 10):
    order, created = CommercialPaymentService.create_point_order(
        session,
        company_id=None,
        workspace_id=workspace_id,
        user_id=user_id,
        points=points,
        provider=PROVIDER,
        merchant_account=MERCHANT,
        idempotency_key=f"manual-order-{uuid4()}",
    )
    assert created is True
    asyncio.run(_dispatch_order(session, order_id=order.id, provider=_fake()))
    assert order.provider_order_id
    return order


def _capture(
    session,
    order: PaymentOrder,
    *,
    occurred_at: datetime = OCCURRED_AT,
):
    event = _captured_event(order, occurred_at=occurred_at)
    receipt = CommercialPaymentService.process_webhook(
        session,
        event=event,
        evidence=_evidence(event),
    )
    assert receipt.outcome == PaymentWebhookOutcome.PROCESSED
    return event, receipt


def _signed_headers(raw: bytes, *, event_id: str) -> dict[str, str]:
    timestamp = str(WEBHOOK_CLOCK)
    signing_input = payment_webhook_signing_input(
        provider=PROVIDER,
        key_id=KEY_ID,
        timestamp=timestamp,
        event_id=event_id,
        raw_body=raw,
    )
    signature = "v1=" + hmac.new(
        SECRET.encode("utf-8"), signing_input, hashlib.sha256
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Payment-Key-ID": KEY_ID,
        "X-Payment-Event-ID": event_id,
        "X-Payment-Timestamp": timestamp,
        "X-Payment-Signature": signature,
    }


def test_payment_order_and_signed_capture_route_are_end_to_end_idempotent(app, client) -> None:
    fake = _fake()
    app.state.payment_provider_registry = PaymentProviderRegistry(
        [fake], allow_test_providers=True
    )
    app.state.payment_webhook_verifier_registry = PaymentWebhookVerifierRegistry(
        [
            PaymentWebhookVerifier(
                SECRET,
                provider=PROVIDER,
                merchant_account=MERCHANT,
                key_id=KEY_ID,
                max_age_seconds=300,
                clock=lambda: WEBHOOK_CLOCK,
            )
        ]
    )
    with _payment_session(app) as session:
        user, workspace = _seed_personal(session)
        user_id = user.id
        workspace_id = workspace.id

    response = client.post(
        "/api/v1/personal/payment-orders",
        headers={"X-User-ID": user_id},
        json={
            "points": 10,
            "provider": PROVIDER,
            "merchant_account": MERCHANT,
            "idempotency_key": "checkout-idempotency-0001",
        },
    )
    assert response.status_code == 202, response.text
    order_id = response.json()["id"]
    assert response.json()["amount_cents"] == 100
    assert response.json()["status"] == "created"
    # Creating a checkout persists only intent/outbox; a committed worker lease
    # is the only entry that may perform provider I/O.
    assert fake._payments_by_key == {}
    dispatched = client.post(
        "/internal/billing/payment-provider-commands/run-once",
        headers={"X-Internal-Service-Token": "test-internal-token"},
    )
    assert dispatched.status_code == 200, dispatched.text
    assert dispatched.json()["operation"] == "create_payment"
    assert dispatched.json()["command_status"] == "succeeded"

    with app.state.session_factory() as session:
        order = session.get(PaymentOrder, order_id)
        assert order is not None
        assert order.status == PaymentOrderStatus.PENDING
        event_id = str(uuid4())
        body = _captured_event(order, event_id=event_id).model_dump(mode="json")
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    headers = _signed_headers(raw, event_id=event_id)

    first = client.post(
        f"/internal/payment-webhooks/{PROVIDER}", content=raw, headers=headers
    )
    assert first.status_code == 200, first.text
    assert first.json()["outcome"] == "processed"
    replay = client.post(
        f"/internal/payment-webhooks/{PROVIDER}", content=raw, headers=headers
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["receipt_id"] == first.json()["receipt_id"]

    conflicting_body = {**body, "data": {**body["data"], "amount_cents": 90}}
    conflicting_raw = json.dumps(
        conflicting_body, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    conflicting = client.post(
        f"/internal/payment-webhooks/{PROVIDER}",
        content=conflicting_raw,
        headers=_signed_headers(conflicting_raw, event_id=event_id),
    )
    assert conflicting.status_code == 409

    with app.state.session_factory() as session:
        wallet = session.get(PersonalWalletAccount, workspace_id)
        assert wallet is not None
        assert (wallet.available_points, wallet.reserved_points, wallet.debt_points) == (
            10,
            0,
            0,
        )
        assert session.scalar(select(func.count(PersonalPointLot.id))) == 1
        assert session.scalar(select(func.count(PersonalLedgerEntry.id))) == 1
        assert session.scalar(select(func.count(PaymentTransaction.id))) == 1
        assert session.scalar(select(func.count(PaymentWebhookReceipt.id))) == 1


def test_refund_success_and_failure_use_holds_and_immutable_reverse_entries(app) -> None:
    with _payment_session(app) as session:
        user, workspace = _seed_personal(session)
        order = _create_dispatched_order(
            session, workspace_id=workspace.id, user_id=user.id, points=10
        )
        _capture(session, order)
        refund, created = CommercialPaymentService.request_refund(
            session,
            order_id=order.id,
            amount_cents=40,
            reason="unused points",
            user_id=user.id,
            idempotency_key="refund-request-0001",
        )
        assert created is True
        assert session.get(PersonalWalletAccount, workspace.id).reversal_reserved_points == 4
        asyncio.run(
            _dispatch_refund(
                session, refund_id=refund.id, provider=_fake()
            )
        )
        success = RefundSucceededEvent.model_validate(
            {
                "api_version": "v1",
                "schema_version": 1,
                "event_id": str(uuid4()),
                "provider": PROVIDER,
                "key_id": KEY_ID,
                "occurred_at": OCCURRED_AT + timedelta(minutes=1),
                "type": "refund.succeeded",
                "data": {
                    "order_id": order.id,
                    "refund_id": refund.id,
                    "provider_payment_id": order.provider_order_id,
                    "provider_refund_id": refund.provider_refund_id,
                    "amount_cents": 40,
                    "currency": "CNY",
                },
            }
        )
        CommercialPaymentService.process_webhook(
            session, event=success, evidence=_evidence(success)
        )
        assert refund.status == PaymentRefundStatus.SUCCEEDED
        assert order.status == PaymentOrderStatus.PARTIALLY_REFUNDED
        wallet = session.get(PersonalWalletAccount, workspace.id)
        lot = session.scalar(
            select(PersonalPointLot).where(PersonalPointLot.payment_order_id == order.id)
        )
        assert wallet is not None and lot is not None
        assert (wallet.available_points, wallet.reversal_reserved_points) == (6, 0)
        assert (lot.available_points, lot.reversed_points) == (6, 4)

        second = _create_dispatched_order(
            session, workspace_id=workspace.id, user_id=user.id, points=5
        )
        _capture(session, second)
        failed_refund, _ = CommercialPaymentService.request_refund(
            session,
            order_id=second.id,
            amount_cents=20,
            reason="provider decline",
            user_id=user.id,
            idempotency_key="refund-request-0002",
        )
        asyncio.run(
            _dispatch_refund(
                session, refund_id=failed_refund.id, provider=_fake()
            )
        )
        failed = RefundFailedEvent.model_validate(
            {
                "api_version": "v1",
                "schema_version": 1,
                "event_id": str(uuid4()),
                "provider": PROVIDER,
                "key_id": KEY_ID,
                "occurred_at": OCCURRED_AT + timedelta(minutes=2),
                "type": "refund.failed",
                "data": {
                    "order_id": second.id,
                    "refund_id": failed_refund.id,
                    "provider_payment_id": second.provider_order_id,
                    "provider_refund_id": failed_refund.provider_refund_id,
                    "amount_cents": 20,
                    "currency": "CNY",
                    "failure_code": "provider_declined",
                },
            }
        )
        CommercialPaymentService.process_webhook(
            session, event=failed, evidence=_evidence(failed)
        )
        assert failed_refund.status == PaymentRefundStatus.FAILED
        assert wallet.available_points == 11
        assert wallet.reversal_reserved_points == 0
        kinds = set(
            session.scalars(
                select(PersonalLedgerEntry.kind).where(
                    PersonalLedgerEntry.payment_refund_id.in_(
                        [refund.id, failed_refund.id]
                    )
                )
            ).all()
        )
        assert kinds == {
            LedgerKind.REFUND_RESERVE,
            LedgerKind.REFUND_SETTLE,
            LedgerKind.REFUND_RELEASE,
        }


def test_payment_provider_timeout_queries_before_same_key_retry_and_captures_once(app) -> None:
    provider = _AcceptedPaymentThenTimeout()
    with _payment_session(app) as session:
        user, workspace = _seed_personal(session)
        order, created = CommercialPaymentService.create_point_order(
            session,
            company_id=None,
            workspace_id=workspace.id,
            user_id=user.id,
            points=10,
            provider=PROVIDER,
            merchant_account=MERCHANT,
            idempotency_key="payment-timeout-stable-0001",
        )
        assert created is True
        returned = asyncio.run(
            _dispatch_order(
                session,
                order_id=order.id,
                provider=provider,
            )
        )
        assert returned.status == PaymentOrderStatus.RECONCILIATION_REQUIRED
        wallet = session.get(PersonalWalletAccount, workspace.id)
        assert wallet is not None and wallet.available_points == 0

        reconciled = asyncio.run(
            _reconcile_payment(
                session,
                order_id=order.id,
                provider=provider,
            )
        )
        assert reconciled.status == PaymentOrderStatus.PAID
        assert reconciled.provider_order_id == "pay_timeout_accepted"
        assert wallet.available_points == 10
        replay = asyncio.run(
            _reconcile_payment(
                session,
                order_id=order.id,
                provider=provider,
            )
        )
        assert replay.id == order.id
        assert session.scalar(
            select(func.count(PaymentTransaction.id)).where(
                PaymentTransaction.order_id == order.id,
                PaymentTransaction.kind == PaymentTransactionKind.CAPTURE,
            )
        ) == 1
        assert session.scalar(
            select(func.count(PaymentWebhookReceipt.id)).where(
                PaymentWebhookReceipt.order_id == order.id
            )
        ) == 1
    assert len(provider.requests) == 1
    assert len(provider.query_requests) == 1
    assert provider.requests[0].idempotency_key.startswith("payment:")


def test_refund_provider_timeout_keeps_one_durable_refund_and_hold(app) -> None:
    provider = _AcceptedRefundThenTimeout()
    with _payment_session(app) as session:
        user, workspace = _seed_personal(session)
        order = _create_dispatched_order(
            session, workspace_id=workspace.id, user_id=user.id, points=10
        )
        _capture(session, order)
        refund, created = CommercialPaymentService.request_refund(
            session,
            order_id=order.id,
            amount_cents=100,
            reason="provider response lost",
            user_id=user.id,
            idempotency_key="refund-timeout-stable-0001",
        )
        assert created is True
        returned = asyncio.run(
            _dispatch_refund(
                session, refund_id=refund.id, provider=provider
            )
        )
        assert returned.status == PaymentRefundStatus.RECONCILIATION_REQUIRED
        refund_id = refund.id
        order_id = order.id
        workspace_id = workspace.id

    with _payment_session(app) as session:
        refund = session.get(PaymentRefund, refund_id)
        wallet = session.get(PersonalWalletAccount, workspace_id)
        assert refund is not None and wallet is not None
        assert refund.status == PaymentRefundStatus.RECONCILIATION_REQUIRED
        assert (wallet.available_points, wallet.reversal_reserved_points) == (0, 10)
        replay, created = CommercialPaymentService.request_refund(
            session,
            order_id=order_id,
            amount_cents=100,
            reason="provider response lost",
            user_id=refund.requested_by_user_id,
            idempotency_key="refund-timeout-stable-0001",
        )
        assert created is False and replay.id == refund_id
        with pytest.raises(ConflictError, match="超过订单可退款余额"):
            CommercialPaymentService.request_refund(
                session,
                order_id=order_id,
                amount_cents=100,
                reason="unsafe second refund",
                user_id=refund.requested_by_user_id,
                idempotency_key="refund-timeout-stable-0002",
            )
        reconciled = asyncio.run(
            _reconcile_refund(
                session,
                refund_id=refund_id,
                provider=provider,
            )
        )
        assert reconciled.status == PaymentRefundStatus.SUCCEEDED
        assert (wallet.available_points, wallet.reversal_reserved_points) == (0, 0)
        assert session.scalar(
            select(func.count(PaymentTransaction.id)).where(
                PaymentTransaction.refund_id == refund_id,
                PaymentTransaction.kind == PaymentTransactionKind.REFUND,
            )
        ) == 1
        query_command = session.scalar(
            select(PaymentProviderCommand).where(
                PaymentProviderCommand.refund_id == refund_id,
                PaymentProviderCommand.operation
                == PaymentProviderCommandOperation.QUERY_REFUND,
            )
        )
        assert query_command is not None
        assert query_command.status == PaymentProviderCommandStatus.SUCCEEDED
    assert len(provider.requests) == 1
    assert len(provider.query_requests) == 1
    assert provider.requests[0].idempotency_key == f"refund:{refund_id}"


def test_pending_refund_blocks_overlapping_dispute_and_opposite_terminal_fails_closed(
    app,
) -> None:
    with _payment_session(app) as session:
        user, workspace = _seed_personal(session)
        order = _create_dispatched_order(
            session, workspace_id=workspace.id, user_id=user.id, points=10
        )
        _capture(session, order)
        refund, _ = CommercialPaymentService.request_refund(
            session,
            order_id=order.id,
            amount_cents=100,
            reason="full pending refund",
            user_id=user.id,
            idempotency_key="refund-dispute-exposure-0001",
        )
        asyncio.run(
            _dispatch_refund(
                session, refund_id=refund.id, provider=_fake()
            )
        )
        dispute_opened = DisputeOpenedEvent.model_validate(
            {
                "api_version": "v1",
                "schema_version": 1,
                "event_id": str(uuid4()),
                "provider": PROVIDER,
                "key_id": KEY_ID,
                "occurred_at": OCCURRED_AT + timedelta(days=1),
                "type": "dispute.opened",
                "data": {
                    "order_id": order.id,
                    "provider_payment_id": order.provider_order_id,
                    "provider_dispute_id": "dp_pending_refund_overlap",
                    "amount_cents": 100,
                    "currency": "CNY",
                    "reason_code": "fraudulent",
                },
            }
        )
        blocked = CommercialPaymentService.process_webhook(
            session, event=dispute_opened, evidence=_evidence(dispute_opened)
        )
        assert blocked.outcome == PaymentWebhookOutcome.RECONCILIATION_REQUIRED
        assert blocked.error_code == "dispute_exceeds_net_capture"
        assert session.scalar(
            select(func.count(PaymentDispute.id)).where(
                PaymentDispute.provider_dispute_id == "dp_pending_refund_overlap"
            )
        ) == 0

        failed = RefundFailedEvent.model_validate(
            {
                "api_version": "v1",
                "schema_version": 1,
                "event_id": str(uuid4()),
                "provider": PROVIDER,
                "key_id": KEY_ID,
                "occurred_at": OCCURRED_AT + timedelta(days=2),
                "type": "refund.failed",
                "data": {
                    "order_id": order.id,
                    "refund_id": refund.id,
                    "provider_payment_id": order.provider_order_id,
                    "provider_refund_id": refund.provider_refund_id,
                    "amount_cents": 100,
                    "currency": "CNY",
                    "failure_code": "provider_declined",
                },
            }
        )
        CommercialPaymentService.process_webhook(
            session, event=failed, evidence=_evidence(failed)
        )
        succeeded = RefundSucceededEvent.model_validate(
            {
                "api_version": "v1",
                "schema_version": 1,
                "event_id": str(uuid4()),
                "provider": PROVIDER,
                "key_id": KEY_ID,
                "occurred_at": OCCURRED_AT + timedelta(days=3),
                "type": "refund.succeeded",
                "data": {
                    "order_id": order.id,
                    "refund_id": refund.id,
                    "provider_payment_id": order.provider_order_id,
                    "provider_refund_id": refund.provider_refund_id,
                    "amount_cents": 100,
                    "currency": "CNY",
                },
            }
        )
        conflict = CommercialPaymentService.process_webhook(
            session, event=succeeded, evidence=_evidence(succeeded)
        )
        assert conflict.outcome == PaymentWebhookOutcome.RECONCILIATION_REQUIRED
        assert conflict.error_code == "refund_terminal_conflict"
        assert refund.status == PaymentRefundStatus.FAILED
        wallet = session.get(PersonalWalletAccount, workspace.id)
        assert wallet is not None
        assert (wallet.available_points, wallet.reversal_reserved_points) == (10, 0)


def test_chargeback_creates_debt_blocks_new_work_and_won_reversal_is_idempotent(app) -> None:
    with _payment_session(app) as session:
        user, workspace = _seed_personal(session)
        order = _create_dispatched_order(
            session, workspace_id=workspace.id, user_id=user.id, points=10
        )
        _capture(session, order)
        wallet = session.get(PersonalWalletAccount, workspace.id)
        lot = session.scalar(
            select(PersonalPointLot).where(PersonalPointLot.payment_order_id == order.id)
        )
        assert wallet is not None and lot is not None
        # Represent eight already-consumed points; a chargeback can recover only
        # what remains and must turn the rest into explicit wallet debt.
        wallet.available_points = 2
        lot.available_points = 2
        lot.settled_points = 8
        opened = DisputeOpenedEvent.model_validate(
            {
                "api_version": "v1",
                "schema_version": 1,
                "event_id": str(uuid4()),
                "provider": PROVIDER,
                "key_id": KEY_ID,
                "occurred_at": OCCURRED_AT + timedelta(days=1),
                "type": "dispute.opened",
                "data": {
                    "order_id": order.id,
                    "provider_payment_id": order.provider_order_id,
                    "provider_dispute_id": "dp_chargeback_0001",
                    "amount_cents": 100,
                    "currency": "CNY",
                    "reason_code": "fraudulent",
                },
            }
        )
        CommercialPaymentService.process_webhook(
            session, event=opened, evidence=_evidence(opened)
        )
        assert (wallet.available_points, wallet.debt_points) == (0, 8)
        with pytest.raises(ConflictError, match="拒付债务"):
            PersonalWalletService.reserve(
                session,
                workspace_id=workspace.id,
                task_id=str(uuid4()),
                amount_points=1,
                idempotency_key="blocked-by-chargeback",
            )

        won = DisputeWonEvent.model_validate(
            {
                "api_version": "v1",
                "schema_version": 1,
                "event_id": str(uuid4()),
                "provider": PROVIDER,
                "key_id": KEY_ID,
                "occurred_at": OCCURRED_AT + timedelta(days=2),
                "type": "dispute.won",
                "data": {
                    "order_id": order.id,
                    "provider_payment_id": order.provider_order_id,
                    "provider_dispute_id": "dp_chargeback_0001",
                    "amount_cents": 100,
                    "currency": "CNY",
                    "reason_code": "merchant_won",
                },
            }
        )
        CommercialPaymentService.process_webhook(
            session, event=won, evidence=_evidence(won)
        )
        dispute = session.scalar(
            select(PaymentDispute).where(
                PaymentDispute.provider_dispute_id == "dp_chargeback_0001"
            )
        )
        assert dispute is not None and dispute.status == PaymentDisputeStatus.WON
        assert (wallet.available_points, wallet.debt_points) == (2, 0)

        duplicate_won = won.model_copy(
            update={"event_id": uuid4(), "occurred_at": OCCURRED_AT + timedelta(days=3)}
        )
        receipt = CommercialPaymentService.process_webhook(
            session, event=duplicate_won, evidence=_evidence(duplicate_won)
        )
        assert receipt.outcome == PaymentWebhookOutcome.PROCESSED
        assert (wallet.available_points, wallet.debt_points) == (2, 0)
        assert (
            session.scalar(
                select(func.count(PersonalPointLot.id)).where(
                    PersonalPointLot.source_kind == PointLotSourceKind.COMPENSATION
                )
            )
            == 1
        )


def test_later_purchase_debt_recovery_is_allocated_and_restored_when_dispute_wins(
    app,
) -> None:
    with _payment_session(app) as session:
        user, workspace = _seed_personal(session)
        disputed_order = _create_dispatched_order(
            session, workspace_id=workspace.id, user_id=user.id, points=10
        )
        _capture(session, disputed_order)
        wallet = session.get(PersonalWalletAccount, workspace.id)
        original_lot = session.scalar(
            select(PersonalPointLot).where(
                PersonalPointLot.payment_order_id == disputed_order.id
            )
        )
        assert wallet is not None and original_lot is not None
        wallet.available_points = 2
        original_lot.available_points = 2
        original_lot.settled_points = 8
        opened = DisputeOpenedEvent.model_validate(
            {
                "api_version": "v1",
                "schema_version": 1,
                "event_id": str(uuid4()),
                "provider": PROVIDER,
                "key_id": KEY_ID,
                "occurred_at": OCCURRED_AT + timedelta(days=1),
                "type": "dispute.opened",
                "data": {
                    "order_id": disputed_order.id,
                    "provider_payment_id": disputed_order.provider_order_id,
                    "provider_dispute_id": "dp_debt_allocation_0001",
                    "amount_cents": 100,
                    "currency": "CNY",
                    "reason_code": "fraudulent",
                },
            }
        )
        CommercialPaymentService.process_webhook(
            session, event=opened, evidence=_evidence(opened)
        )
        dispute = session.scalar(
            select(PaymentDispute).where(
                PaymentDispute.provider_dispute_id == "dp_debt_allocation_0001"
            )
        )
        assert dispute is not None
        assert (wallet.available_points, wallet.debt_points) == (0, 8)

        recovery_order = _create_dispatched_order(
            session, workspace_id=workspace.id, user_id=user.id, points=5
        )
        _capture(session, recovery_order)
        allocation = session.scalar(
            select(PaymentDisputeDebtRecoveryAllocation).where(
                PaymentDisputeDebtRecoveryAllocation.dispute_id == dispute.id
            )
        )
        assert allocation is not None
        assert allocation.recovered_points == 5
        assert (wallet.available_points, wallet.debt_points) == (0, 3)

        won = DisputeWonEvent.model_validate(
            {
                "api_version": "v1",
                "schema_version": 1,
                "event_id": str(uuid4()),
                "provider": PROVIDER,
                "key_id": KEY_ID,
                "occurred_at": OCCURRED_AT + timedelta(days=2),
                "type": "dispute.won",
                "data": {
                    "order_id": disputed_order.id,
                    "provider_payment_id": disputed_order.provider_order_id,
                    "provider_dispute_id": "dp_debt_allocation_0001",
                    "amount_cents": 100,
                    "currency": "CNY",
                    "reason_code": "merchant_won",
                },
            }
        )
        receipt = CommercialPaymentService.process_webhook(
            session, event=won, evidence=_evidence(won)
        )
        assert receipt.outcome == PaymentWebhookOutcome.PROCESSED
        assert (wallet.available_points, wallet.debt_points) == (7, 0)
        reversal = session.scalar(
            select(PaymentDisputeDebtRecoveryReversal).where(
                PaymentDisputeDebtRecoveryReversal.allocation_id == allocation.id
            )
        )
        assert reversal is not None and reversal.restored_points == 5
        compensation_lot = session.scalar(
            select(PersonalPointLot).where(
                PersonalPointLot.source_kind == PointLotSourceKind.COMPENSATION,
                PersonalPointLot.id == reversal.personal_point_lot_id,
            )
        )
        assert compensation_lot is not None
        assert compensation_lot.original_points == 7


def test_webhook_inbox_blocks_out_of_order_dispute_resolution_then_replays(app) -> None:
    with _payment_session(app) as session:
        user, workspace = _seed_personal(session)
        order = _create_dispatched_order(
            session,
            workspace_id=workspace.id,
            user_id=user.id,
            points=10,
        )
        _capture(session, order)
        order_id = order.id
        provider_payment_id = order.provider_order_id
        workspace_id = workspace.id

    dispute_id = "dp_out_of_order_replay"
    won = DisputeWonEvent.model_validate(
        {
            "api_version": "v1",
            "schema_version": 1,
            "event_id": str(uuid4()),
            "provider": PROVIDER,
            "key_id": KEY_ID,
            "occurred_at": OCCURRED_AT + timedelta(days=2),
            "type": "dispute.won",
            "data": {
                "order_id": order_id,
                "provider_payment_id": provider_payment_id,
                "provider_dispute_id": dispute_id,
                "amount_cents": 100,
                "currency": "CNY",
                "reason_code": "fraudulent",
            },
        }
    )
    with _payment_session(app) as session:
        won_inbox = CommercialPaymentService.ingest_webhook_event(
            session,
            event=won,
            evidence=_evidence(won),
        )
        won_inbox_id = won_inbox.id
    with _payment_session(app) as session:
        blocked, receipt = CommercialPaymentService.process_inbox_event(
            session,
            inbox_id=won_inbox_id,
        )
        assert receipt is None
        assert blocked.status == PaymentWebhookInboxStatus.BLOCKED
        assert blocked.last_error_code == "dispute_open_event_missing"
        assert session.scalar(select(func.count(PaymentWebhookReceipt.id))) == 1

    opened = DisputeOpenedEvent.model_validate(
        {
            "api_version": "v1",
            "schema_version": 1,
            "event_id": str(uuid4()),
            "provider": PROVIDER,
            "key_id": KEY_ID,
            "occurred_at": OCCURRED_AT + timedelta(days=1),
            "type": "dispute.opened",
            "data": {
                "order_id": order_id,
                "provider_payment_id": provider_payment_id,
                "provider_dispute_id": dispute_id,
                "amount_cents": 100,
                "currency": "CNY",
                "reason_code": "fraudulent",
            },
        }
    )
    with _payment_session(app) as session:
        opened_inbox = CommercialPaymentService.ingest_webhook_event(
            session,
            event=opened,
            evidence=_evidence(opened),
        )
        opened_inbox, opened_receipt = CommercialPaymentService.process_inbox_event(
            session,
            inbox_id=opened_inbox.id,
        )
        assert opened_receipt is not None
        assert opened_inbox.status == PaymentWebhookInboxStatus.PROCESSED
        woken = session.get(PaymentWebhookInboxEvent, won_inbox_id)
        assert woken is not None
        assert woken.status == PaymentWebhookInboxStatus.RECEIVED

    with _payment_session(app) as session:
        processed, won_receipt = CommercialPaymentService.process_inbox_event(
            session,
            inbox_id=won_inbox_id,
        )
        assert won_receipt is not None
        assert processed.status == PaymentWebhookInboxStatus.PROCESSED
        wallet = session.get(PersonalWalletAccount, workspace_id)
        dispute = session.scalar(
            select(PaymentDispute).where(
                PaymentDispute.provider_dispute_id == dispute_id
            )
        )
        assert wallet is not None and dispute is not None
        assert wallet.available_points == 10
        assert wallet.debt_points == 0
        assert dispute.status == PaymentDisputeStatus.WON


def test_auto_recharge_has_one_pending_order_cooldown_and_monthly_cap(app) -> None:
    now = datetime(2033, 5, 18, 3, 0, tzinfo=timezone.utc)
    with _payment_session(app) as session:
        user, workspace = _seed_personal(session)
        mandate = _active_mandate(
            session,
            user_id=user.id,
            workspace_id=workspace.id,
        )
        rule = CommercialPaymentService.configure_auto_recharge(
            session,
            company_id=None,
            workspace_id=workspace.id,
            mandate_id=mandate.id,
            user_id=user.id,
            threshold_points=1,
            top_up_points=10,
            monthly_cap_cents=100,
            cooldown_seconds=3600,
            enabled=True,
        )
        assert rule.mandate_id == mandate.id
        assert (rule.provider, rule.merchant_account) == (PROVIDER, MERCHANT)
        first, created = CommercialPaymentService.trigger_auto_recharge(
            session, rule_id=rule.id, user_id=user.id, now=now
        )
        assert first is not None and created is True and first.automatic is True
        duplicate, duplicate_created = CommercialPaymentService.trigger_auto_recharge(
            session, rule_id=rule.id, user_id=user.id, now=now
        )
        assert duplicate is None and duplicate_created is False
        assert session.scalar(select(func.count(AutoRechargeExecution.id))) == 1

        asyncio.run(
            _dispatch_order(
                session, order_id=first.id, provider=_fake()
            )
        )
        _capture(session, first)
        wallet = session.get(PersonalWalletAccount, workspace.id)
        lot = session.scalar(
            select(PersonalPointLot).where(PersonalPointLot.payment_order_id == first.id)
        )
        assert wallet is not None and lot is not None
        wallet.available_points = 0
        lot.available_points = 0
        lot.settled_points = 10
        capped, capped_created = CommercialPaymentService.trigger_auto_recharge(
            session,
            rule_id=rule.id,
            user_id=user.id,
            now=now + timedelta(hours=2),
        )
        assert capped is None and capped_created is False
        assert session.scalar(select(func.count(PaymentOrder.id))) == 1
        mandate.status = PaymentMandateStatus.REVOKED
        mandate.revoked_at = now + timedelta(hours=3)
        rule.enabled = True
        revoked, revoked_created = CommercialPaymentService.trigger_auto_recharge(
            session,
            rule_id=rule.id,
            user_id=user.id,
            now=now + timedelta(hours=4),
        )
        assert revoked is None and revoked_created is False
        assert rule.enabled is False


def test_invoice_capture_refund_and_dispute_reconcile_ar_without_minting_points(app) -> None:
    period_start = datetime(2033, 5, 1, tzinfo=timezone.utc)
    period_end = datetime(2033, 6, 1, tzinfo=timezone.utc)
    with _payment_session(app) as session:
        suffix = uuid4().hex
        company = Company(name=f"Invoice company {suffix}", billing_version=2)
        user = User(
            email=f"invoice-{suffix}@example.test",
            display_name="Invoice payer",
            account_type=UserAccountType.COMPANY,
        )
        model = ModelDefinition(
            slug=f"invoice-payment-{suffix}",
            display_name="Invoice payment model",
            provider_key="invoice-payment-test",
            billing_mode="per_item",
        )
        session.add_all([company, user, model])
        session.flush()
        wallet = CompanyPointWalletAccount(
            company_id=company.id,
            available_points=0,
            reserved_points=0,
            reversal_reserved_points=0,
            debt_points=0,
            migration_idempotency_key=f"native-v2:{company.id}",
            migrated_from_available_cents=0,
            migration_remainder_cents=0,
            migration_rounding_grant_points=0,
        )
        session.add(wallet)
        session.flush()
        EnterpriseBillingService.activate_contract(
            session,
            company_id=company.id,
            contract_reference="MSA-INVOICE-TEST",
            currency="CNY",
            timezone_name="Asia/Shanghai",
            cycle_day=1,
            payment_terms_days=30,
            credit_limit_points=100,
            effective_at=period_start,
            created_by_user_id=user.id,
        )
        cycle, lot, _ = EnterpriseBillingService.open_cycle(
            session,
            company_id=company.id,
            period_start=period_start,
            period_end=period_end,
        )
        assert lot is not None
        task = GenerationTask(
            id=new_id(),
            company_id=company.id,
            personal_workspace_id=None,
            user_id=user.id,
            model_id=model.id,
            idempotency_key=f"invoice-settle-{suffix}",
            request_fingerprint="d" * 64,
            status=TaskStatus.SUCCEEDED,
            request_payload={"prompt": "invoice settlement", "output_count": 1},
            billing_unit=BillingUnit.POINT,
            billing_version=2,
            quote_cents=None,
            quote_points=10,
            pricing_snapshot={
                "schema_version": 2,
                "billing_unit": "point",
                "billing_version": 2,
                "charge_policy": "FIXED_QUOTE_ON_SUCCESS",
                "unit_price_points": 10,
                "quantity": 1,
                "quote_points": 10,
            },
            capability_snapshot={},
            reserved_cents=0,
            reserved_points=0,
            actual_cost_cents=None,
            actual_cost_points=10,
        )
        session.add(task)
        session.flush()
        allocation = TaskPointLotAllocation(
            id=new_id(),
            company_id=company.id,
            task_id=task.id,
            lot_id=lot.id,
            allocated_points=10,
            reserved_points=0,
            settled_points=10,
            released_points=0,
        )
        session.add(allocation)
        session.flush()
        settled_at = period_start + timedelta(days=1)
        reserve = CompanyPointLedgerEntry(
            company_id=company.id,
            kind=PointLedgerKind.RESERVE,
            amount_points=10,
            available_delta_points=-10,
            reserved_delta_points=10,
            reversal_reserved_delta_points=0,
            debt_delta_points=0,
            idempotency_key=f"reserve:invoice-settle-{suffix}",
            task_id=task.id,
            created_at=settled_at,
        )
        settle = CompanyPointLedgerEntry(
            id=new_id(),
            company_id=company.id,
            kind=PointLedgerKind.SETTLE,
            amount_points=10,
            available_delta_points=0,
            reserved_delta_points=-10,
            reversal_reserved_delta_points=0,
            debt_delta_points=0,
            idempotency_key=f"settle:invoice-settle-{suffix}",
            task_id=task.id,
            created_at=settled_at,
        )
        session.add_all([reserve, settle])
        session.flush()
        session.add(
            PointLotSettlementValueAllocation(
                company_id=company.id,
                personal_workspace_id=None,
                task_id=task.id,
                company_task_allocation_id=allocation.id,
                personal_task_allocation_id=None,
                company_settle_ledger_id=settle.id,
                personal_settle_ledger_id=None,
                settled_points=10,
                cash_basis_cents=0,
                receivable_basis_cents=100,
                subsidy_cents=0,
                created_at=settled_at,
            )
        )
        account = session.get(CompanyBillingAccount, company.id)
        assert account is not None
        lot.available_points -= 10
        lot.settled_points += 10
        wallet.available_points -= 10
        account.unbilled_receivable_cents += 100
        session.flush()
        _, invoice, _, issued = EnterpriseBillingService.close_and_issue_cycle(
            session,
            company_id=company.id,
            cycle_id=cycle.id,
            issued_at=period_end,
        )
        assert issued is True
        assert session.scalar(
            select(func.count(CompanyInvoiceLine.id)).where(
                CompanyInvoiceLine.invoice_id == invoice.id
            )
        ) == 1
        wallet_points_before_payment = wallet.available_points

        order, created = CommercialPaymentService.create_invoice_payment_order(
            session,
            company_id=company.id,
            invoice_id=invoice.id,
            user_id=user.id,
            amount_cents=100,
            provider=PROVIDER,
            merchant_account=MERCHANT,
            idempotency_key="invoice-payment-0001",
        )
        assert created is True and order.points == 0
        asyncio.run(
            _dispatch_order(
                session, order_id=order.id, provider=_fake()
            )
        )
        invoice_event_time = period_end + timedelta(hours=1)
        _capture(session, order, occurred_at=invoice_event_time)
        assert invoice.paid_cents == 100
        assert invoice.status == EnterpriseInvoiceStatus.PAID
        assert wallet.available_points == wallet_points_before_payment
        assert session.scalar(
            select(func.count(CompanyPointLot.id)).where(
                CompanyPointLot.payment_order_id == order.id
            )
        ) == 0

        refund, _ = CommercialPaymentService.request_refund(
            session,
            order_id=order.id,
            amount_cents=40,
            reason="invoice credit correction",
            user_id=user.id,
            idempotency_key="invoice-refund-0001",
        )
        assert refund.points == 0
        asyncio.run(
            _dispatch_refund(
                session, refund_id=refund.id, provider=_fake()
            )
        )
        refund_event = RefundSucceededEvent.model_validate(
            {
                "api_version": "v1",
                "schema_version": 1,
                "event_id": str(uuid4()),
                "provider": PROVIDER,
                "key_id": KEY_ID,
                "occurred_at": invoice_event_time + timedelta(days=1),
                "type": "refund.succeeded",
                "data": {
                    "order_id": order.id,
                    "refund_id": refund.id,
                    "provider_payment_id": order.provider_order_id,
                    "provider_refund_id": refund.provider_refund_id,
                    "amount_cents": 40,
                    "currency": "CNY",
                },
            }
        )
        CommercialPaymentService.process_webhook(
            session, event=refund_event, evidence=_evidence(refund_event)
        )
        assert invoice.paid_cents == 60
        assert invoice.status == EnterpriseInvoiceStatus.PARTIALLY_PAID

        opened = DisputeOpenedEvent.model_validate(
            {
                "api_version": "v1",
                "schema_version": 1,
                "event_id": str(uuid4()),
                "provider": PROVIDER,
                "key_id": KEY_ID,
                "occurred_at": invoice_event_time + timedelta(days=2),
                "type": "dispute.opened",
                "data": {
                    "order_id": order.id,
                    "provider_payment_id": order.provider_order_id,
                    "provider_dispute_id": "dp_invoice_0001",
                    "amount_cents": 60,
                    "currency": "CNY",
                    "reason_code": "fraudulent",
                },
            }
        )
        CommercialPaymentService.process_webhook(
            session, event=opened, evidence=_evidence(opened)
        )
        assert invoice.paid_cents == 0
        assert invoice.status == EnterpriseInvoiceStatus.DISPUTED
        dispute = session.scalar(
            select(PaymentDispute).where(
                PaymentDispute.provider_dispute_id == "dp_invoice_0001"
            )
        )
        assert dispute is not None and dispute.points == 0

        won = DisputeWonEvent.model_validate(
            {
                "api_version": "v1",
                "schema_version": 1,
                "event_id": str(uuid4()),
                "provider": PROVIDER,
                "key_id": KEY_ID,
                "occurred_at": invoice_event_time + timedelta(days=3),
                "type": "dispute.won",
                "data": {
                    "order_id": order.id,
                    "provider_payment_id": order.provider_order_id,
                    "provider_dispute_id": "dp_invoice_0001",
                    "amount_cents": 60,
                    "currency": "CNY",
                    "reason_code": "merchant_won",
                },
            }
        )
        CommercialPaymentService.process_webhook(
            session, event=won, evidence=_evidence(won)
        )
        assert invoice.paid_cents == 60
        assert invoice.status == EnterpriseInvoiceStatus.PARTIALLY_PAID
        assert wallet.available_points == wallet_points_before_payment
        kinds = set(
            session.scalars(
                select(AccountsReceivableLedgerEntry.kind).where(
                    AccountsReceivableLedgerEntry.invoice_id == invoice.id
                )
            ).all()
        )
        assert kinds == {
            "INVOICE_ISSUED",
            "INVOICE_PAYMENT_CAPTURE",
            "INVOICE_PAYMENT_REFUND",
            "INVOICE_PAYMENT_CHARGEBACK",
            "INVOICE_PAYMENT_DISPUTE_REVERSAL",
        }


def test_signed_payment_expiry_closes_attempt_without_fulfilling_points(app) -> None:
    with _payment_session(app) as session:
        user, workspace = _seed_personal(session)
        order = _create_dispatched_order(
            session, workspace_id=workspace.id, user_id=user.id, points=10
        )
        expired = PaymentExpiredEvent.model_validate(
            {
                "api_version": "v1",
                "schema_version": 1,
                "event_id": str(uuid4()),
                "provider": PROVIDER,
                "key_id": KEY_ID,
                "occurred_at": OCCURRED_AT,
                "type": "payment.expired",
                "data": {
                    "order_id": order.id,
                    "provider_payment_id": order.provider_order_id,
                    "failure_code": "checkout_expired",
                },
            }
        )
        receipt = CommercialPaymentService.process_webhook(
            session, event=expired, evidence=_evidence(expired)
        )
        assert receipt.outcome == PaymentWebhookOutcome.PROCESSED
        assert order.status == PaymentOrderStatus.EXPIRED
        attempt = session.scalar(
            select(PaymentAttempt).where(PaymentAttempt.order_id == order.id)
        )
        assert attempt is not None
        assert attempt.status == PaymentAttemptStatus.FAILED
        assert attempt.failure_code == "checkout_expired"
        assert session.scalar(
            select(func.count(PersonalPointLot.id)).where(
                PersonalPointLot.payment_order_id == order.id
            )
        ) == 0


def test_auto_recharge_scheduler_skips_ineligible_rule_without_starving_next(app, client) -> None:
    app.state.payment_provider_registry = PaymentProviderRegistry(
        [_fake()], allow_test_providers=True
    )
    with _payment_session(app) as session:
        first_user, first_workspace = _seed_personal(session)
        second_user, second_workspace = _seed_personal(session)
        first_wallet = session.get(PersonalWalletAccount, first_workspace.id)
        assert first_wallet is not None
        first_wallet.available_points = 5
        first_mandate = _active_mandate(
            session,
            user_id=first_user.id,
            workspace_id=first_workspace.id,
        )
        second_mandate = _active_mandate(
            session,
            user_id=second_user.id,
            workspace_id=second_workspace.id,
        )
        first_rule = CommercialPaymentService.configure_auto_recharge(
            session,
            company_id=None,
            workspace_id=first_workspace.id,
            mandate_id=first_mandate.id,
            user_id=first_user.id,
            threshold_points=1,
            top_up_points=10,
            monthly_cap_cents=1000,
            cooldown_seconds=60,
            enabled=True,
        )
        second_rule = CommercialPaymentService.configure_auto_recharge(
            session,
            company_id=None,
            workspace_id=second_workspace.id,
            mandate_id=second_mandate.id,
            user_id=second_user.id,
            threshold_points=1,
            top_up_points=10,
            monthly_cap_cents=1000,
            cooldown_seconds=60,
            enabled=True,
        )
        current = datetime.now(timezone.utc)
        first_rule.last_attempt_at = current - timedelta(days=3)
        second_rule.last_attempt_at = current - timedelta(days=2)
        second_rule_id = second_rule.id

    response = client.post(
        "/internal/billing/auto-recharge/run-once",
        headers={"X-Internal-Service-Token": "test-internal-token"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["processed"] is True
    assert response.json()["rule_id"] == second_rule_id


def test_outbox_and_inbox_claims_compile_to_skip_locked_and_only_worker_awaits_psp() -> None:
    now = datetime.now(timezone.utc)
    for statement in (
        CommercialPaymentService._provider_command_claim_statement(now),
        CommercialPaymentService._webhook_inbox_claim_statement(now),
    ):
        assert "FOR UPDATE SKIP LOCKED" in str(
            statement.compile(dialect=postgresql.dialect())
        )
    source = inspect.getsource(CommercialPaymentService)
    assert source.count("async def ") == 1
    assert source.count("await provider.") == 4
    assert not hasattr(CommercialPaymentService, "dispatch_order")
    assert not hasattr(CommercialPaymentService, "dispatch_refund")
    worker = inspect.getsource(CommercialPaymentService.execute_claimed_provider_command)
    finalizer = worker[worker.index("claimed = session.scalar"):]
    assert finalizer.index("select(PaymentProviderCommand)") < finalizer.index(
        "cls._locked_order(session, order_id)"
    )


def test_worker_requires_committed_lease_and_has_no_transaction_during_network(app) -> None:
    with _payment_session(app) as session:
        user, workspace = _seed_personal(session)
        order, _ = CommercialPaymentService.create_point_order(
            session,
            company_id=None,
            workspace_id=workspace.id,
            user_id=user.id,
            points=10,
            provider=PROVIDER,
            merchant_account=MERCHANT,
            idempotency_key="durable-network-gate-0001",
        )
        order_id = order.id
        session.commit()
        command = CommercialPaymentService.claim_next_provider_command(session)
        assert command is not None and command.lease_token is not None
        command_id, lease_token = command.id, command.lease_token

        class TransactionCheckingProvider(DeterministicFakePaymentProvider):
            async def create_payment(self, request):
                assert session.in_transaction() is False
                with app.state.session_factory() as observer:
                    durable = observer.get(PaymentProviderCommand, command_id)
                    assert durable is not None
                    assert durable.status == PaymentProviderCommandStatus.CLAIMED
                    assert durable.lease_token == lease_token
                return await super().create_payment(request)

        provider = TransactionCheckingProvider(enabled_for_tests=True)
        with pytest.raises(ConflictError, match="必须先提交"):
            asyncio.run(
                CommercialPaymentService.execute_claimed_provider_command(
                    session,
                    command_id=command_id,
                    lease_token=lease_token,
                    provider=provider,
                )
            )
        assert provider._payments_by_key == {}
        session.commit()
        finalized = asyncio.run(
            CommercialPaymentService.execute_claimed_provider_command(
                session,
                command_id=command_id,
                lease_token=lease_token,
                provider=provider,
            )
        )
        assert finalized.status == PaymentProviderCommandStatus.SUCCEEDED
        assert session.get(PaymentOrder, order_id).status == PaymentOrderStatus.PENDING
        with pytest.raises(ConflictError, match="租约"):
            asyncio.run(
                CommercialPaymentService.execute_claimed_provider_command(
                    session,
                    command_id=command_id,
                    lease_token=lease_token,
                    provider=provider,
                )
            )
        assert len(provider._payments_by_key) == 1


def test_unconfigured_merchant_and_cross_merchant_signed_callback_fail_closed(app, client) -> None:
    with _payment_session(app) as session:
        user, workspace = _seed_personal(session)
        user_id, workspace_id = user.id, workspace.id
    body = {
        "points": 10,
        "provider": PROVIDER,
        "merchant_account": MERCHANT,
        "idempotency_key": "merchant-bound-order-0001",
    }
    response = client.post(
        "/api/v1/personal/payment-orders", headers={"X-User-ID": user_id}, json=body
    )
    assert response.status_code == 503
    app.state.payment_provider_registry = PaymentProviderRegistry(
        [_fake()], allow_test_providers=True
    )
    response = client.post(
        "/api/v1/personal/payment-orders",
        headers={"X-User-ID": user_id},
        json={**body, "merchant_account": "merchant-other"},
    )
    assert response.status_code == 503
    with _payment_session(app) as session:
        assert session.scalar(select(func.count(PaymentOrder.id))) == 0
        order = _create_dispatched_order(session, workspace_id=workspace_id, user_id=user_id)
        event = _captured_event(order)
        with pytest.raises(ConflictError, match="商户"):
            CommercialPaymentService.process_webhook(
                session,
                event=event,
                evidence=replace(_evidence(event), merchant_account="merchant-other"),
            )
    app.state.payment_webhook_verifier_registry = PaymentWebhookVerifierRegistry(
        [PaymentWebhookVerifier(
            SECRET,
            provider=PROVIDER,
            merchant_account="merchant-other",
            key_id=KEY_ID,
            clock=lambda: WEBHOOK_CLOCK,
        )]
    )
    raw = json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    response = client.post(
        f"/internal/payment-webhooks/{PROVIDER}",
        content=raw,
        headers=_signed_headers(raw, event_id=str(event.event_id)),
    )
    assert response.status_code == 409, response.text
    with app.state.session_factory() as session:
        assert session.get(PersonalWalletAccount, workspace_id).available_points == 0
        assert session.scalar(select(func.count(PaymentWebhookInboxEvent.id))) == 0
        assert session.scalar(select(func.count(PaymentTransaction.id))) == 0


def test_mandate_api_only_internal_verified_activation_and_owner_revocation(app, client) -> None:
    app.state.payment_provider_registry = PaymentProviderRegistry(
        [_fake()], allow_test_providers=True
    )
    with _payment_session(app) as session:
        user, _ = _seed_personal(session)
        other_user, _ = _seed_personal(session)
        user_id, other_id = user.id, other_user.id
    consented_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    body = {
        "provider": PROVIDER,
        "merchant_account": MERCHANT,
        "consent_version": "auto-recharge-v1",
        "consent_sha256": "a" * 64,
        "consented_at": consented_at.isoformat(),
        "idempotency_key": "pending-mandate-lifecycle-0001",
    }
    endpoint = "/api/v1/personal/payment-mandates"
    headers = {"X-User-ID": user_id}
    rejected = client.post(endpoint, headers=headers, json={**body, "status": "active"})
    assert rejected.status_code == 422
    rejected = client.post(
        endpoint, headers=headers,
        json={**body, "consented_at": consented_at.replace(tzinfo=None).isoformat()},
    )
    assert rejected.status_code == 422
    created = client.post(endpoint, headers=headers, json=body)
    assert created.status_code == 201, created.text
    mandate_id = created.json()["id"]
    assert created.json()["status"] == "pending"
    replay = client.post(endpoint, headers=headers, json=body)
    assert replay.json()["id"] == mandate_id
    activation = {
        key: body[key]
        for key in ("provider", "merchant_account", "consent_version", "consent_sha256")
    }
    activation.update(
        provider_customer_reference="cus_mandate_001",
        provider_payment_method_reference="pm_mandate_001",
        provider_mandate_reference="mandate_verified_001",
        verified_at=datetime.now(timezone.utc).isoformat(),
    )
    activate_endpoint = f"/internal/billing/payment-mandates/{mandate_id}/activate"
    assert client.post(activate_endpoint, headers=headers, json=activation).status_code == 401
    internal = {"X-Internal-Service-Token": "test-internal-token"}
    mismatch = client.post(
        activate_endpoint, headers=internal,
        json={**activation, "consent_sha256": "b" * 64},
    )
    assert mismatch.status_code == 409
    activated = client.post(activate_endpoint, headers=internal, json=activation)
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "active"
    assert client.post(activate_endpoint, headers=internal, json=activation).json() == activated.json()
    configured = client.put(
        "/api/v1/personal/auto-recharge", headers=headers,
        json={
            "mandate_id": mandate_id,
            "threshold_points": 1,
            "top_up_points": 10,
            "monthly_cap_cents": 1000,
            "cooldown_seconds": 60,
            "enabled": True,
        },
    )
    assert configured.status_code == 200, configured.text
    revoke_endpoint = f"{endpoint}/{mandate_id}/revoke"
    assert client.post(revoke_endpoint, headers={"X-User-ID": other_id}).status_code == 404
    revoked = client.post(revoke_endpoint, headers=headers)
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    assert client.post(revoke_endpoint, headers=headers).json() == revoked.json()
    assert client.post(activate_endpoint, headers=internal, json=activation).status_code == 409
    with app.state.session_factory() as session:
        assert session.get(AutoRechargeRule, configured.json()["id"]).enabled is False


def test_automatic_command_freezes_mandate_and_unknown_blocks_another_charge(app) -> None:
    provider = _AcceptedPaymentThenTimeout()
    now = datetime.now(timezone.utc)
    with _payment_session(app) as session:
        user, workspace = _seed_personal(session)
        original = _active_mandate(session, user_id=user.id, workspace_id=workspace.id)
        replacement = _active_mandate(session, user_id=user.id, workspace_id=workspace.id)
        rule = CommercialPaymentService.configure_auto_recharge(
            session, company_id=None, workspace_id=workspace.id, mandate_id=original.id,
            user_id=user.id, threshold_points=1, top_up_points=10,
            monthly_cap_cents=1000, cooldown_seconds=60, enabled=True,
        )
        order, created = CommercialPaymentService.trigger_auto_recharge(
            session, rule_id=rule.id, user_id=user.id, now=now
        )
        assert order is not None and created
        assert order.payment_mandate_id == original.id
        frozen_reference = original.provider_mandate_reference
        CommercialPaymentService.configure_auto_recharge(
            session, company_id=None, workspace_id=workspace.id, mandate_id=replacement.id,
            user_id=user.id, threshold_points=1, top_up_points=10,
            monthly_cap_cents=1000, cooldown_seconds=60, enabled=True,
        )
        returned = asyncio.run(_dispatch_order(session, order_id=order.id, provider=provider))
        assert returned.status == PaymentOrderStatus.RECONCILIATION_REQUIRED
        assert provider.requests[0].off_session is True
        assert provider.requests[0].provider_mandate_reference == frozen_reference
        assert provider.requests[0].provider_payment_method_reference == original.provider_payment_method_reference
        blocked, created = CommercialPaymentService.trigger_auto_recharge(
            session, rule_id=rule.id, user_id=user.id, now=now + timedelta(hours=2)
        )
        assert blocked is None and created is False
        assert session.scalar(select(func.count(PaymentOrder.id))) == 1
        command = CommercialPaymentService.claim_next_provider_command(session)
        assert command is not None
        assert command.operation == PaymentProviderCommandOperation.QUERY_PAYMENT
        assert len(provider.requests) == 1


def test_revoked_mandate_cancels_unsent_automatic_command_without_psp_call(app) -> None:
    provider = _fake()
    with _payment_session(app) as session:
        user, workspace = _seed_personal(session)
        mandate = _active_mandate(session, user_id=user.id, workspace_id=workspace.id)
        rule = CommercialPaymentService.configure_auto_recharge(
            session, company_id=None, workspace_id=workspace.id, mandate_id=mandate.id,
            user_id=user.id, threshold_points=1, top_up_points=10,
            monthly_cap_cents=1000, cooldown_seconds=60, enabled=True,
        )
        order, _ = CommercialPaymentService.trigger_auto_recharge(
            session, rule_id=rule.id, user_id=user.id
        )
        assert order is not None
        CommercialPaymentService.revoke_payment_mandate(
            session, mandate_id=mandate.id, company_id=None, workspace_id=workspace.id
        )
        returned = asyncio.run(_dispatch_order(session, order_id=order.id, provider=provider))
        assert returned.status == PaymentOrderStatus.CANCELLED
        assert provider._payments_by_key == {}
        command = session.scalar(select(PaymentProviderCommand).where(
            PaymentProviderCommand.order_id == order.id,
            PaymentProviderCommand.operation == PaymentProviderCommandOperation.CREATE_PAYMENT,
        ))
        assert command.last_error_code == "payment_mandate_not_active"
