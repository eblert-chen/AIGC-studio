from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from platform_api.models import (
    AutoRechargeRule,
    PaymentAttempt, PaymentAttemptStatus, PaymentOrder, PaymentOrderStatus,
    PaymentProviderCommand, PaymentProviderCommandOperation, PaymentProviderCommandStatus,
    PaymentRefundStatus, PaymentTransaction, PaymentTransactionKind,
    PaymentWebhookOutcome, PersonalWalletAccount,
    PaymentWebhookInboxEvent, PaymentWebhookInboxStatus,
)
from platform_api.payment_providers import (
    DeterministicFakePaymentProvider, PaymentProviderRegistry,
    QueryPaymentResult, QueryRefundResult,
)
from platform_api.services.commercial_payments import (
    CommercialPaymentService, MAX_POINTS, MAX_PURCHASE_POINTS,
)
from platform_api.services.errors import ConflictError
from platform_api.services.payment_webhooks import (
    DisputeOpenedEvent, DisputeWonEvent, PaymentCapturedEvent,
    RefundSucceededEvent,
)

from .conftest import bootstrap
from .test_commercial_payments import (
    PROVIDER, MERCHANT, KEY_ID, OCCURRED_AT, _capture, _create_dispatched_order,
    _dispatch_order, _dispatch_refund, _evidence, _fake, _payment_session,
    _reconcile_payment, _reconcile_refund, _seed_personal, _active_mandate,
)
from .test_enterprise_billing_api import _enable_point_company


def _refund_success(order, refund):
    return RefundSucceededEvent.model_validate({
        "api_version": "v1", "schema_version": 1, "event_id": str(uuid4()),
        "provider": PROVIDER, "key_id": KEY_ID,
        "occurred_at": OCCURRED_AT + timedelta(minutes=1), "type": "refund.succeeded",
        "data": {
            "order_id": order.id, "refund_id": refund.id,
            "provider_payment_id": order.provider_order_id,
            "provider_refund_id": refund.provider_refund_id,
            "amount_cents": refund.amount_cents, "currency": "CNY",
        },
    })


def _query_due(session, order_id, operation):
    command = session.scalar(select(PaymentProviderCommand).where(
        PaymentProviderCommand.order_id == order_id,
        PaymentProviderCommand.operation == operation,
    ))
    assert command is not None
    command.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    session.flush()
    return command


@pytest.mark.parametrize("conflicting", [False, True])
def test_stale_payment_query_cannot_regress_verified_capture(app, conflicting):
    class QueryProvider(DeterministicFakePaymentProvider):
        async def query_payment(self, request):
            return QueryPaymentResult(
                provider=PROVIDER, found=True,
                provider_payment_id=request.provider_payment_id,
                status="failed" if conflicting else "pending",
                amount_cents=100, currency="CNY",
                occurred_at=OCCURRED_AT if conflicting else None,
                failure_code="provider_conflict" if conflicting else None,
            )

    with _payment_session(app) as session:
        user, workspace = _seed_personal(session)
        order = _create_dispatched_order(session, workspace_id=workspace.id, user_id=user.id)
        _capture(session, order)
        command = _query_due(session, order.id, PaymentProviderCommandOperation.QUERY_PAYMENT)
        asyncio.run(_reconcile_payment(session, order_id=order.id,
                                     provider=QueryProvider(enabled_for_tests=True)))
        assert order.status == (PaymentOrderStatus.RECONCILIATION_REQUIRED
                                if conflicting else PaymentOrderStatus.PAID)
        assert command.status == (PaymentProviderCommandStatus.UNKNOWN
                                  if conflicting else PaymentProviderCommandStatus.SUCCEEDED)
        attempt = session.scalar(select(PaymentAttempt).where(PaymentAttempt.order_id == order.id))
        assert attempt.status == PaymentAttemptStatus.SUCCEEDED
        assert session.get(PersonalWalletAccount, workspace.id).available_points == 10
        assert session.scalar(select(func.count(PaymentTransaction.id))) == 1


@pytest.mark.parametrize("conflicting", [False, True])
def test_stale_refund_query_cannot_regress_verified_refund(app, conflicting):
    class QueryProvider(DeterministicFakePaymentProvider):
        async def query_refund(self, request):
            return QueryRefundResult(
                provider=PROVIDER, found=True, provider_refund_id=request.provider_refund_id,
                status="failed" if conflicting else "pending", amount_cents=40, currency="CNY",
                occurred_at=OCCURRED_AT + timedelta(minutes=2) if conflicting else None,
                failure_code="provider_conflict" if conflicting else None,
            )

    with _payment_session(app) as session:
        user, workspace = _seed_personal(session)
        order = _create_dispatched_order(session, workspace_id=workspace.id, user_id=user.id)
        _capture(session, order)
        refund, _ = CommercialPaymentService.request_refund(
            session, order_id=order.id, user_id=user.id, amount_cents=40,
            reason="unused", idempotency_key="query-refund-0001",
        )
        asyncio.run(_dispatch_refund(session, refund_id=refund.id, provider=_fake()))
        event = _refund_success(order, refund)
        CommercialPaymentService.process_webhook(session, event=event, evidence=_evidence(event))
        command = _query_due(session, order.id, PaymentProviderCommandOperation.QUERY_REFUND)
        asyncio.run(_reconcile_refund(session, refund_id=refund.id,
                                    provider=QueryProvider(enabled_for_tests=True)))
        assert refund.status == PaymentRefundStatus.SUCCEEDED
        assert order.status == (PaymentOrderStatus.RECONCILIATION_REQUIRED
                                if conflicting else PaymentOrderStatus.PARTIALLY_REFUNDED)
        assert command.status == (PaymentProviderCommandStatus.UNKNOWN
                                  if conflicting else PaymentProviderCommandStatus.SUCCEEDED)
        wallet = session.get(PersonalWalletAccount, workspace.id)
        assert (wallet.available_points, wallet.reversal_reserved_points) == (6, 0)
        assert session.scalar(select(func.count(PaymentTransaction.id)).where(
            PaymentTransaction.kind == PaymentTransactionKind.REFUND)) == 1


@pytest.mark.parametrize("reconciliation_required", [False, True])
def test_multiple_disputes_and_refund_preserve_financial_status_priority(app, reconciliation_required):
    with _payment_session(app) as session:
        user, workspace = _seed_personal(session)
        order = _create_dispatched_order(session, workspace_id=workspace.id, user_id=user.id, points=20)
        _capture(session, order)
        refund, _ = CommercialPaymentService.request_refund(
            session, order_id=order.id, user_id=user.id, amount_cents=40,
            reason="unused", idempotency_key="multi-dispute-refund-0001",
        )
        asyncio.run(_dispatch_refund(session, refund_id=refund.id, provider=_fake()))
        if reconciliation_required:
            order.status = PaymentOrderStatus.RECONCILIATION_REQUIRED
        opened = []
        for _ in range(2):
            event = DisputeOpenedEvent.model_validate({
                "api_version": "v1", "schema_version": 1, "event_id": str(uuid4()),
                "provider": PROVIDER, "key_id": KEY_ID, "occurred_at": OCCURRED_AT,
                "type": "dispute.opened", "data": {
                    "order_id": order.id, "provider_payment_id": order.provider_order_id,
                    "provider_dispute_id": f"dispute_{uuid4().hex}",
                    "amount_cents": 40, "currency": "CNY", "reason_code": "test_dispute",
                },
            })
            receipt = CommercialPaymentService.process_webhook(session, event=event, evidence=_evidence(event))
            assert receipt.outcome == PaymentWebhookOutcome.PROCESSED
            opened.append(event)
        event = _refund_success(order, refund)
        CommercialPaymentService.process_webhook(session, event=event, evidence=_evidence(event))
        assert (order.status, order.disputed_amount_cents, order.refunded_amount_cents) == (
            PaymentOrderStatus.RECONCILIATION_REQUIRED if reconciliation_required
            else PaymentOrderStatus.DISPUTED, 80, 40)
        for index, previous in enumerate(opened):
            payload = previous.model_dump(mode="json")
            payload.update(type="dispute.won", event_id=str(uuid4()),
                           occurred_at=OCCURRED_AT + timedelta(minutes=3))
            won = DisputeWonEvent.model_validate(payload)
            CommercialPaymentService.process_webhook(session, event=won, evidence=_evidence(won))
            assert order.status == (PaymentOrderStatus.RECONCILIATION_REQUIRED
                                    if reconciliation_required else PaymentOrderStatus.DISPUTED
                                    if index == 0 else PaymentOrderStatus.PARTIALLY_REFUNDED)
        assert session.get(PersonalWalletAccount, workspace.id).available_points == 16


@pytest.mark.parametrize("timeout_after_callback", [False, True])
def test_callback_before_create_response_or_timeout_preserves_paid_attempt(app, timeout_after_callback):
    class CallbackProvider(DeterministicFakePaymentProvider):
        async def create_payment(self, request):
            result = await super().create_payment(request)
            with _payment_session(app) as callback_session:
                event = PaymentCapturedEvent.model_validate({
                    "api_version": "v1", "schema_version": 1, "event_id": str(uuid4()),
                    "provider": PROVIDER, "key_id": KEY_ID, "occurred_at": OCCURRED_AT,
                    "type": "payment.captured", "data": {
                        "order_id": request.order_id, "provider_payment_id": result.provider_payment_id,
                        "amount_cents": request.amount_cents, "currency": request.currency,
                    },
                })
                inbox = CommercialPaymentService.ingest_webhook_event(
                    callback_session, event=event, evidence=_evidence(event))
                callback_session.commit()
                _, receipt = CommercialPaymentService.process_inbox_event(callback_session, inbox_id=inbox.id)
                assert receipt is not None and receipt.outcome == PaymentWebhookOutcome.PROCESSED
            if timeout_after_callback:
                raise TimeoutError("create response lost after verified callback")
            return result

    with _payment_session(app) as session:
        user, workspace = _seed_personal(session)
        order, _ = CommercialPaymentService.create_point_order(
            session, company_id=None, workspace_id=workspace.id, user_id=user.id,
            points=10, provider=PROVIDER, merchant_account=MERCHANT,
            idempotency_key="early-capture-0001",
        )
        asyncio.run(_dispatch_order(session, order_id=order.id,
                                   provider=CallbackProvider(enabled_for_tests=True)))
        assert order.status == PaymentOrderStatus.PAID
        attempt = session.scalar(select(PaymentAttempt).where(PaymentAttempt.order_id == order.id))
        assert attempt.status == PaymentAttemptStatus.SUCCEEDED
        assert session.get(PersonalWalletAccount, workspace.id).available_points == 10
        assert session.scalar(select(func.count(PaymentTransaction.id))) == 1


def test_payment_amount_bounds_and_invalid_frozen_command_fail_closed(app, client):
    app.state.payment_provider_registry = PaymentProviderRegistry([_fake()], allow_test_providers=True)
    with _payment_session(app) as session:
        user, workspace = _seed_personal(session)
        user_id = user.id
        request = dict(company_id=None, workspace_id=workspace.id, user_id=user.id,
                       provider=PROVIDER, merchant_account=MERCHANT, idempotency_key="bounds-0001")
        with pytest.raises(ConflictError, match="支付通道"):
            CommercialPaymentService.create_point_order(session, points=MAX_PURCHASE_POINTS + 1, **request)
        wallet = session.get(PersonalWalletAccount, workspace.id)
        wallet.available_points = MAX_POINTS
        with pytest.raises(ConflictError, match="钱包上限"):
            CommercialPaymentService.create_point_order(session, points=1, **request)
        wallet.available_points = 0
        order, _ = CommercialPaymentService.create_point_order(session, points=10, **request)
        # A legacy/imported invalid immutable query must be audited DEAD; do not
        # rewrite a persisted request just to exercise this safety boundary.
        payload = {"order_id": order.id, "create_idempotency_key": "x" * 300}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        invalid = PaymentProviderCommand(
            operation=PaymentProviderCommandOperation.QUERY_PAYMENT, order_id=order.id,
            provider=PROVIDER, merchant_account=MERCHANT, idempotency_key=f"query:{order.id}",
            dedupe_key=f"query_payment:{order.id}", request_payload=payload,
            request_sha256=hashlib.sha256(encoded).hexdigest(),
            status=PaymentProviderCommandStatus.PENDING,
        )
        session.add(invalid)
        session.flush()
        asyncio.run(_reconcile_payment(session, order_id=order.id, provider=_fake()))
        assert invalid.status == PaymentProviderCommandStatus.DEAD
        assert invalid.last_error_code == "provider_request_invalid"
        assert order.status == PaymentOrderStatus.RECONCILIATION_REQUIRED
    response = client.post("/api/v1/personal/payment-orders", headers={"X-User-ID": user_id}, json={
        "points": MAX_PURCHASE_POINTS + 1, "provider": PROVIDER,
        "merchant_account": MERCHANT, "idempotency_key": "api-bounds-0001",
    })
    assert response.status_code == 422


def test_personal_order_and_refund_readback_is_scoped_and_shows_checkout(app, client):
    class CheckoutProvider(DeterministicFakePaymentProvider):
        async def create_payment(self, request):
            result = await super().create_payment(request)
            return replace(result, checkout_url=f"https://checkout.example.test/{request.order_id}")

    app.state.payment_provider_registry = PaymentProviderRegistry(
        [CheckoutProvider(enabled_for_tests=True)], allow_test_providers=True)
    with _payment_session(app) as session:
        user, _ = _seed_personal(session)
        other, _ = _seed_personal(session)
        user_id, other_id = user.id, other.id
    headers = {"X-User-ID": user_id}
    created = client.post("/api/v1/personal/payment-orders", headers=headers, json={
        "points": 10, "provider": PROVIDER, "merchant_account": MERCHANT,
        "idempotency_key": "readback-0001",
    })
    assert created.status_code == 202
    order_id = created.json()["id"]
    endpoint = f"/api/v1/personal/payment-orders/{order_id}"
    assert client.get(endpoint, headers=headers).json() == created.json()
    assert client.get(endpoint, headers={"X-User-ID": other_id}).status_code == 404
    run = client.post("/internal/billing/payment-provider-commands/run-once",
                      headers={"X-Internal-Service-Token": "test-internal-token"})
    assert run.status_code == 200, run.text
    read = client.get(endpoint, headers=headers)
    assert read.status_code == 200
    assert read.json()["status"] == "requires_action"
    assert read.json()["checkout_url"] == f"https://checkout.example.test/{order_id}"
    with _payment_session(app) as session:
        order = session.get(PaymentOrder, order_id)
        _capture(session, order)
        refund, _ = CommercialPaymentService.request_refund(
            session, order_id=order_id, user_id=user_id, amount_cents=20,
            reason="unused", idempotency_key="readback-refund-0001")
        refund_id = refund.id
    refund_endpoint = f"{endpoint}/refunds/{refund_id}"
    read = client.get(refund_endpoint, headers=headers)
    assert read.status_code == 200 and read.json()["status"] == "requested"
    assert client.get(refund_endpoint, headers={"X-User-ID": other_id}).status_code == 404
    assert client.get(f"{endpoint}/refunds/{uuid4()}", headers=headers).status_code == 404
    assert client.get(endpoint, headers=headers).json()["status"] == "paid"


def test_company_order_readback_requires_billing_read_not_manage_and_matching_tenant(
    app, client, tenant, tenant_headers,
):
    _enable_point_company(app, tenant)
    app.state.payment_provider_registry = PaymentProviderRegistry([_fake()], allow_test_providers=True)
    company_id = tenant["company_id"]
    created = client.post(f"/api/v1/companies/{company_id}/payment-orders", headers=tenant_headers, json={
        "points": 10, "provider": PROVIDER, "merchant_account": MERCHANT,
        "idempotency_key": "company-readback-0001",
    })
    assert created.status_code == 202, created.text
    order_id = created.json()["id"]
    endpoint = f"/api/v1/companies/{company_id}/payment-orders/{order_id}"
    assert client.get(endpoint, headers=tenant_headers).json() == created.json()
    member = client.post(f"/api/v1/companies/{company_id}/members", headers=tenant_headers, json={
        "email": f"payment-reader-{uuid4().hex}@example.com", "display_name": "No billing access",
    })
    assert member.status_code == 201, member.text
    member = member.json()
    deny = client.put(f"/api/v1/companies/{company_id}/members/{member['membership_id']}/permission",
                      headers=tenant_headers, json={"permission_code": "billing.manage", "effect": "deny"})
    assert deny.status_code == 200
    denied = {"X-Company-ID": company_id, "X-User-ID": member["user_id"]}
    grant = client.put(f"/api/v1/companies/{company_id}/members/{member['membership_id']}/permission",
                      headers=tenant_headers, json={"permission_code": "billing.read", "effect": "allow"})
    assert grant.status_code == 200
    assert client.get(endpoint, headers=denied).status_code == 200
    assert client.post(f"/api/v1/companies/{company_id}/payment-orders", headers=denied, json={
        "points": 10, "provider": PROVIDER, "merchant_account": MERCHANT,
        "idempotency_key": "read-only-no-checkout-0001",
    }).status_code == 403
    deny = client.put(f"/api/v1/companies/{company_id}/members/{member['membership_id']}/permission",
                     headers=tenant_headers, json={"permission_code": "billing.read", "effect": "deny"})
    assert deny.status_code == 200
    assert client.get(endpoint, headers=denied).status_code == 403
    assert client.get(f"{endpoint}/refunds/{uuid4()}", headers=denied).status_code == 403
    other = bootstrap(client, suffix=f"payment-other-{uuid4().hex}")
    other_headers = {"X-Company-ID": other["company_id"], "X-User-ID": other["user_id"]}
    other_endpoint = f"/api/v1/companies/{other['company_id']}/payment-orders/{order_id}"
    assert client.get(other_endpoint, headers=other_headers).status_code == 404
    assert client.get(f"{other_endpoint}/refunds/{uuid4()}", headers=other_headers).status_code == 404


def test_auto_recharge_due_schedule_advances_past_one_hundred_idle_rules(app, client):
    app.state.payment_provider_registry = PaymentProviderRegistry([_fake()], allow_test_providers=True)
    due = datetime.now(timezone.utc) - timedelta(days=1)
    with _payment_session(app) as session:
        for index in range(101):
            user, workspace = _seed_personal(session)
            if index < 100:
                session.get(PersonalWalletAccount, workspace.id).available_points = 5
            mandate = _active_mandate(session, user_id=user.id, workspace_id=workspace.id)
            rule = CommercialPaymentService.configure_auto_recharge(
                session, company_id=None, workspace_id=workspace.id, mandate_id=mandate.id,
                user_id=user.id, threshold_points=0, top_up_points=10,
                monthly_cap_cents=1000, cooldown_seconds=60, enabled=True,
            )
            rule.next_check_at = due + timedelta(seconds=index)
        final_rule_id = rule.id
    endpoint = "/internal/billing/auto-recharge/run-once"
    headers = {"X-Internal-Service-Token": "test-internal-token"}
    first = client.post(endpoint, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["processed"] is False
    with app.state.session_factory() as session:
        idle_rules = list(session.scalars(select(AutoRechargeRule).where(
            AutoRechargeRule.id != final_rule_id)).all())
        assert len(idle_rules) == 100
        assert all(rule.last_attempt_at is None for rule in idle_rules)
        assert all(rule.next_check_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
                   for rule in idle_rules)
    second = client.post(endpoint, headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["processed"] is True
    assert second.json()["rule_id"] == final_rule_id
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(PaymentOrder.id))) == 1


def test_dispute_before_capture_is_durably_blocked_and_replayed_when_due(app, client):
    with _payment_session(app) as session:
        user, workspace = _seed_personal(session)
        order = _create_dispatched_order(session, workspace_id=workspace.id, user_id=user.id)
        event = DisputeOpenedEvent.model_validate({
            "api_version": "v1", "schema_version": 1, "event_id": str(uuid4()),
            "provider": PROVIDER, "key_id": KEY_ID, "occurred_at": OCCURRED_AT,
            "type": "dispute.opened", "data": {
                "order_id": order.id, "provider_payment_id": order.provider_order_id,
                "provider_dispute_id": f"dispute_{uuid4().hex}",
                "amount_cents": 40, "currency": "CNY", "reason_code": "late_capture_webhook",
            },
        })
        inbox = CommercialPaymentService.ingest_webhook_event(session, event=event, evidence=_evidence(event))
        session.commit()
        inbox, receipt = CommercialPaymentService.process_inbox_event(session, inbox_id=inbox.id)
        assert receipt is None and inbox.status == PaymentWebhookInboxStatus.BLOCKED
        assert inbox.last_error_code == "payment_capture_event_missing"
        _capture(session, order)
        inbox.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        inbox_id, order_id = inbox.id, order.id
    response = client.post("/internal/billing/payment-webhook-inbox/run-once",
                           headers={"X-Internal-Service-Token": "test-internal-token"})
    assert response.status_code == 200, response.text
    assert response.json()["inbox_id"] == inbox_id
    assert response.json()["inbox_status"] == "processed"
    with app.state.session_factory() as session:
        assert session.get(PaymentWebhookInboxEvent, inbox_id).attempt_count == 1
        order = session.get(PaymentOrder, order_id)
        assert (order.status, order.disputed_amount_cents) == (PaymentOrderStatus.DISPUTED, 40)
