from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from platform_api.payment_providers import (
    CreatePaymentRequest,
    CreateRefundRequest,
    DeterministicFakePaymentProvider,
    PaymentProviderConfigurationError,
    PaymentProviderRegistry,
    PaymentProviderRequestError,
    PaymentProviderUnavailableError,
    QueryPaymentRequest,
    QueryPaymentResult,
    QueryRefundRequest,
    QueryRefundResult,
)
from platform_api.services.payment_webhooks import (
    DisputeLostEvent,
    DisputeOpenedEvent,
    DisputeWonEvent,
    PaymentCancelledEvent,
    PaymentCapturedEvent,
    PaymentExpiredEvent,
    PaymentFailedEvent,
    PaymentWebhookPayloadError,
    PaymentWebhookVerificationError,
    PaymentWebhookVerifier,
    PaymentWebhookVerifierRegistry,
    RefundFailedEvent,
    RefundSucceededEvent,
    payment_webhook_signing_input,
)


SECRET = "payment-webhook-test-secret-with-more-than-32-bytes"
OTHER_SECRET = "another-payment-webhook-secret-with-32-bytes"
PROVIDER = "sandbox-pay"
MERCHANT = "merchant-cny-main"
KEY_ID = "key-2026-08"
NOW = 2_000_000_000
EVENT_ID = "00000000-0000-4000-8000-000000000001"
ORDER_ID = "00000000-0000-4000-8000-000000000002"
REFUND_ID = "00000000-0000-4000-8000-000000000003"


def _event(event_type: str) -> dict:
    if event_type == "payment.captured":
        data = {
            "order_id": ORDER_ID,
            "provider_payment_id": "pay_0001",
            "amount_cents": 1000,
            "currency": "CNY",
        }
    elif event_type.startswith("payment."):
        data = {
            "order_id": ORDER_ID,
            "provider_payment_id": "pay_0001",
        }
        if event_type == "payment.failed":
            data["failure_code"] = "declined"
    elif event_type.startswith("refund."):
        data = {
            "order_id": ORDER_ID,
            "refund_id": REFUND_ID,
            "provider_payment_id": "pay_0001",
            "provider_refund_id": "re_0001",
            "amount_cents": 500,
            "currency": "CNY",
        }
        if event_type == "refund.failed":
            data["failure_code"] = "provider_declined"
    else:
        data = {
            "order_id": ORDER_ID,
            "provider_payment_id": "pay_0001",
            "provider_dispute_id": "dp_0001",
            "amount_cents": 1000,
            "currency": "CNY",
            "reason_code": "fraudulent",
        }
    return {
        "api_version": "v1",
        "schema_version": 1,
        "event_id": EVENT_ID,
        "provider": PROVIDER,
        "key_id": KEY_ID,
        "occurred_at": "2033-05-18T03:33:20Z",
        "type": event_type,
        "data": data,
    }


def _raw(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _signature(
    raw_body: bytes,
    *,
    secret: str = SECRET,
    provider: str = PROVIDER,
    key_id: str = KEY_ID,
    event_id: str = EVENT_ID,
    timestamp: str = str(NOW),
) -> str:
    signing_input = payment_webhook_signing_input(
        provider=provider,
        key_id=key_id,
        timestamp=timestamp,
        event_id=event_id,
        raw_body=raw_body,
    )
    return "v1=" + hmac.new(
        secret.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).hexdigest()


def _verifier(
    *,
    secret: str = SECRET,
    provider: str = PROVIDER,
    key_id: str = KEY_ID,
) -> PaymentWebhookVerifier:
    return PaymentWebhookVerifier(
        secret,
        provider=provider,
        merchant_account=MERCHANT,
        key_id=key_id,
        max_age_seconds=300,
        clock=lambda: NOW,
    )


def _verify(
    verifier: PaymentWebhookVerifier,
    raw_body: bytes,
    *,
    provider: str = PROVIDER,
    key_id: str = KEY_ID,
    event_id: str = EVENT_ID,
    timestamp: str = str(NOW),
    signature: str | None = None,
):
    return verifier.verify(
        raw_body,
        provider=provider,
        key_id=key_id,
        event_id=event_id,
        timestamp=timestamp,
        signature=signature
        or _signature(
            raw_body,
            provider=provider,
            key_id=key_id,
            event_id=event_id,
            timestamp=timestamp,
        ),
    )


def test_registry_has_no_default_and_fake_requires_two_explicit_test_opt_ins() -> None:
    with pytest.raises(PaymentProviderUnavailableError):
        PaymentProviderRegistry().resolve("deterministic-test")
    with pytest.raises(PaymentProviderConfigurationError, match="test-only"):
        DeterministicFakePaymentProvider()

    fake = DeterministicFakePaymentProvider(enabled_for_tests=True)
    with pytest.raises(PaymentProviderConfigurationError, match="test-only"):
        PaymentProviderRegistry([fake])
    registry = PaymentProviderRegistry([fake], allow_test_providers=True)
    assert registry.provider_keys == ("deterministic-test",)
    assert registry.resolve("deterministic-test", MERCHANT) is fake
    with pytest.raises(PaymentProviderUnavailableError):
        registry.resolve("missing-provider")


def test_fake_provider_is_deterministic_and_returns_no_sensitive_provider_body() -> None:
    fake = DeterministicFakePaymentProvider(enabled_for_tests=True)
    payment_request = CreatePaymentRequest(
        order_id=ORDER_ID,
        amount_cents=1000,
        points=100,
        currency="CNY",
        idempotency_key="payment-create-0001",
    )
    first_payment = asyncio.run(fake.create_payment(payment_request))
    repeated_payment = asyncio.run(fake.create_payment(payment_request))
    assert first_payment == repeated_payment
    assert first_payment.provider_payment_id.startswith("pay_")
    assert first_payment.checkout_url is None

    refund_request = CreateRefundRequest(
        refund_id=REFUND_ID,
        order_id=ORDER_ID,
        provider_payment_id=first_payment.provider_payment_id,
        amount_cents=500,
        currency="CNY",
        idempotency_key="refund-create-0001",
    )
    first_refund = asyncio.run(fake.create_refund(refund_request))
    repeated_refund = asyncio.run(fake.create_refund(refund_request))
    assert first_refund == repeated_refund
    assert first_refund.provider_refund_id.startswith("re_")
    assert set(asdict(first_payment)) == {
        "provider",
        "provider_payment_id",
        "status",
        "checkout_url",
    }

    payment_query = asyncio.run(
        fake.query_payment(
            QueryPaymentRequest(
                order_id=ORDER_ID,
                idempotency_key="payment-create-0001",
                provider_payment_id=first_payment.provider_payment_id,
            )
        )
    )
    assert payment_query.found is True
    assert payment_query.status == "pending"
    assert payment_query.amount_cents == 1000
    refund_query = asyncio.run(
        fake.query_refund(
            QueryRefundRequest(
                refund_id=REFUND_ID,
                order_id=ORDER_ID,
                provider_payment_id=first_payment.provider_payment_id,
                idempotency_key="refund-create-0001",
                provider_refund_id=first_refund.provider_refund_id,
            )
        )
    )
    assert refund_query.found is True
    assert refund_query.status == "pending"


def test_provider_query_results_fail_closed_on_incomplete_or_ambiguous_facts() -> None:
    occurred_at = datetime(2033, 5, 18, tzinfo=timezone.utc)
    with pytest.raises(PaymentProviderConfigurationError, match="financial data"):
        QueryPaymentResult(
            provider=PROVIDER,
            found=False,
            provider_payment_id="pay_0001",
        )
    with pytest.raises(PaymentProviderConfigurationError, match="occurred_at"):
        QueryPaymentResult(
            provider=PROVIDER,
            found=True,
            provider_payment_id="pay_0001",
            status="captured",
            amount_cents=1000,
            currency="CNY",
        )
    with pytest.raises(PaymentProviderConfigurationError, match="failure_code"):
        QueryRefundResult(
            provider=PROVIDER,
            found=True,
            provider_refund_id="re_0001",
            status="failed",
            amount_cents=500,
            currency="CNY",
            occurred_at=occurred_at,
        )
    with pytest.raises(PaymentProviderConfigurationError, match="checkout URL"):
        QueryPaymentResult(
            provider=PROVIDER,
            found=True,
            provider_payment_id="pay_0001",
            status="requires_action",
            amount_cents=1000,
            currency="CNY",
        )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"order_id": ORDER_ID.replace("-", "")}, "order_id"),
        ({"amount_cents": True}, "amount_cents"),
        ({"points": 0}, "points"),
        ({"currency": "cny"}, "currency"),
        ({"idempotency_key": "short"}, "idempotency_key"),
    ],
)
def test_outbound_payment_intent_is_strict(
    override: dict[str, object],
    message: str,
) -> None:
    body: dict[str, object] = {
        "order_id": ORDER_ID,
        "amount_cents": 1000,
        "points": 100,
        "currency": "CNY",
        "idempotency_key": "payment-create-0001",
    }
    body.update(override)
    with pytest.raises(PaymentProviderRequestError, match=message):
        CreatePaymentRequest(**body)  # type: ignore[arg-type]


def test_outbound_invoice_payment_intent_carries_zero_points_explicitly() -> None:
    request = CreatePaymentRequest(
        order_id=ORDER_ID,
        amount_cents=1000,
        points=0,
        currency="CNY",
        idempotency_key="invoice-payment-create-0001",
        purpose="invoice_payment",
    )
    assert request.points == 0
    assert request.purpose == "invoice_payment"
    with pytest.raises(PaymentProviderRequestError, match="must be zero"):
        CreatePaymentRequest(
            order_id=ORDER_ID,
            amount_cents=1000,
            points=1,
            currency="CNY",
            idempotency_key="invoice-payment-create-0002",
            purpose="invoice_payment",
        )


def test_off_session_request_requires_all_verified_references_and_manual_has_none() -> None:
    body = dict(
        order_id=ORDER_ID,
        amount_cents=100,
        points=10,
        currency="CNY",
        idempotency_key="off-session-contract-0001",
    )
    with pytest.raises(PaymentProviderRequestError, match="mandate references"):
        CreatePaymentRequest(**body, off_session=True)
    with pytest.raises(PaymentProviderRequestError, match="interactive payment"):
        CreatePaymentRequest(**body, provider_customer_reference="cus_001")
    with pytest.raises(PaymentProviderRequestError, match="boolean"):
        CreatePaymentRequest(**body, off_session="true")
    request = CreatePaymentRequest(
        **body,
        off_session=True,
        provider_customer_reference="cus_001",
        provider_payment_method_reference="pm_001",
        provider_mandate_reference="mandate_001",
    )
    assert request.off_session is True
    assert request.provider_payment_method_reference == "pm_001"


def test_provider_and_webhook_registry_bind_one_trusted_merchant() -> None:
    fake = DeterministicFakePaymentProvider(enabled_for_tests=True)
    registry = PaymentProviderRegistry([fake], allow_test_providers=True)
    assert registry.identities == ((fake.provider_key, MERCHANT),)
    with pytest.raises(PaymentProviderUnavailableError):
        registry.resolve(fake.provider_key, "merchant-other")
    with pytest.raises(PaymentProviderUnavailableError):
        registry.resolve(fake.provider_key)
    first = _verifier()
    second = PaymentWebhookVerifier(
        SECRET,
        provider=PROVIDER,
        merchant_account="merchant-other",
        key_id="other-merchant-key",
        clock=lambda: NOW,
    )
    with pytest.raises(ValueError, match="shared across merchants"):
        PaymentWebhookVerifierRegistry([first, second])


def test_provider_registry_rejects_aliases_duplicates_and_test_doubles_by_default() -> None:
    fake = DeterministicFakePaymentProvider(enabled_for_tests=True)
    with pytest.raises(PaymentProviderConfigurationError, match="registry.*identity"):
        PaymentProviderRegistry(
            {"aliased-provider": fake},
            allow_test_providers=True,
        )
    with pytest.raises(PaymentProviderConfigurationError, match="more than once"):
        PaymentProviderRegistry(
            [fake, fake],
            allow_test_providers=True,
        )


@pytest.mark.parametrize(
    ("event_type", "event_class"),
    [
        ("payment.captured", PaymentCapturedEvent),
        ("payment.failed", PaymentFailedEvent),
        ("payment.cancelled", PaymentCancelledEvent),
        ("payment.expired", PaymentExpiredEvent),
        ("refund.succeeded", RefundSucceededEvent),
        ("refund.failed", RefundFailedEvent),
        ("dispute.opened", DisputeOpenedEvent),
        ("dispute.won", DisputeWonEvent),
        ("dispute.lost", DisputeLostEvent),
    ],
)
def test_hmac_v1_accepts_only_the_six_strict_financial_events(
    event_type: str,
    event_class: type,
) -> None:
    raw_body = _raw(_event(event_type))
    payload, evidence = _verify(_verifier(), raw_body)
    assert isinstance(payload, event_class)
    assert payload.type == event_type
    assert str(payload.event_id) == EVENT_ID
    assert payload.occurred_at.isoformat() == "2033-05-18T03:33:20+00:00"
    assert evidence == type(evidence)(
        provider=PROVIDER,
        merchant_account=MERCHANT,
        key_id=KEY_ID,
        event_id=EVENT_ID,
        delivery_timestamp=evidence.delivery_timestamp,
        payload_sha256=hashlib.sha256(raw_body).hexdigest(),
    )
    assert evidence.delivery_timestamp.isoformat() == "2033-05-18T03:33:20+00:00"
    assert set(asdict(evidence)) == {
        "provider",
        "merchant_account",
        "key_id",
        "event_id",
        "delivery_timestamp",
        "payload_sha256",
    }


@pytest.mark.parametrize(
    "missing_field",
    ["provider", "key_id", "event_id", "timestamp", "signature"],
)
def test_signature_headers_are_all_required(missing_field: str) -> None:
    raw_body = _raw(_event("payment.captured"))
    arguments = {
        "provider": PROVIDER,
        "key_id": KEY_ID,
        "event_id": EVENT_ID,
        "timestamp": str(NOW),
        "signature": _signature(raw_body),
    }
    arguments[missing_field] = None
    with pytest.raises(PaymentWebhookVerificationError):
        _verifier().verify(raw_body, **arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("override", "signature_kwargs"),
    [
        ({"provider": "other-pay"}, {}),
        ({"key_id": "other-key"}, {}),
        ({"event_id": "00000000-0000-4000-8000-000000000099"}, {}),
        ({"timestamp": str(NOW - 301)}, {}),
        ({"timestamp": str(NOW + 301)}, {}),
        ({"timestamp": f"0{NOW}"}, {}),
    ],
)
def test_signature_identity_and_replay_window_fail_closed(
    override: dict[str, str],
    signature_kwargs: dict[str, str],
) -> None:
    raw_body = _raw(_event("payment.captured"))
    arguments = {
        "provider": PROVIDER,
        "key_id": KEY_ID,
        "event_id": EVENT_ID,
        "timestamp": str(NOW),
    }
    arguments.update(override)
    signature = _signature(raw_body, **signature_kwargs)
    with pytest.raises(PaymentWebhookVerificationError):
        _verifier().verify(raw_body, signature=signature, **arguments)


def test_signature_binds_the_exact_raw_body_provider_key_event_and_timestamp() -> None:
    body = _event("payment.captured")
    compact = _raw(body)
    pretty = json.dumps(body, indent=2).encode("utf-8")
    compact_signature = _signature(compact)
    with pytest.raises(PaymentWebhookVerificationError):
        _verifier().verify(
            pretty,
            provider=PROVIDER,
            key_id=KEY_ID,
            event_id=EVENT_ID,
            timestamp=str(NOW),
            signature=compact_signature,
        )

    wrong_secret_signature = _signature(compact, secret=OTHER_SECRET)
    with pytest.raises(PaymentWebhookVerificationError):
        _verifier().verify(
            compact,
            provider=PROVIDER,
            key_id=KEY_ID,
            event_id=EVENT_ID,
            timestamp=str(NOW),
            signature=wrong_secret_signature,
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body.update({"extra": "not-allowed"}),
        lambda body: body.update({"type": "payment.pending"}),
        lambda body: body.update({"schema_version": 2}),
        lambda body: body.update({"occurred_at": "2033-05-18T03:33:20"}),
        lambda body: body["data"].update({"amount_cents": True}),
        lambda body: body["data"].update({"amount_cents": 10.0}),
        lambda body: body["data"].update({"card_number": "4111111111111111"}),
    ],
)
def test_signed_payload_model_forbids_unknown_types_coercion_and_sensitive_extras(
    mutate,
) -> None:
    body = _event("payment.captured")
    mutate(body)
    raw_body = _raw(body)
    with pytest.raises(PaymentWebhookPayloadError) as caught:
        _verify(_verifier(), raw_body)
    assert "4111111111111111" not in str(caught.value)


def test_signed_payload_rejects_duplicate_keys_at_any_depth_and_nonfinite_numbers() -> None:
    duplicate_top = (
        b'{"api_version":"v1","api_version":"v1","schema_version":1,'
        b'"event_id":"' + EVENT_ID.encode("ascii") + b'","provider":"sandbox-pay",'
        b'"key_id":"key-2026-08","occurred_at":"2033-05-18T03:33:20Z",'
        b'"type":"payment.captured","data":{}}'
    )
    duplicate_nested = _raw(_event("payment.captured")).replace(
        b'"amount_cents":1000',
        b'"amount_cents":1000,"amount_cents":1000',
    )
    nonfinite = _raw(_event("payment.captured")).replace(
        b'"amount_cents":1000',
        b'"amount_cents":NaN',
    )
    for raw_body in (duplicate_top, duplicate_nested, nonfinite):
        with pytest.raises(PaymentWebhookPayloadError):
            _verify(_verifier(), raw_body)


def test_signed_payload_identity_must_match_headers() -> None:
    for field, changed in (
        ("event_id", "00000000-0000-4000-8000-000000000099"),
        ("provider", "other-pay"),
        ("key_id", "other-key"),
    ):
        body = _event("payment.captured")
        body[field] = changed
        raw_body = _raw(body)
        with pytest.raises(PaymentWebhookPayloadError, match="headers"):
            _verify(_verifier(), raw_body)


def test_registry_supports_key_rotation_without_provider_or_key_fallback() -> None:
    old = _verifier(key_id="key-old")
    current = _verifier(key_id=KEY_ID)
    registry = PaymentWebhookVerifierRegistry([old, current])
    assert registry.identities == ((PROVIDER, "key-2026-08"), (PROVIDER, "key-old"))
    assert registry.resolve(PROVIDER, "key-old") is old
    assert registry.resolve(PROVIDER, KEY_ID) is current
    with pytest.raises(PaymentWebhookVerificationError):
        registry.resolve(PROVIDER, "key-unknown")
    with pytest.raises(PaymentWebhookVerificationError):
        registry.resolve("unknown-pay", KEY_ID)


def test_registry_verifies_without_retaining_or_returning_the_raw_body() -> None:
    verifier = _verifier()
    registry = PaymentWebhookVerifierRegistry({(PROVIDER, KEY_ID): verifier})
    raw_body = _raw(_event("dispute.opened"))
    payload, evidence = registry.verify(
        raw_body,
        provider=PROVIDER,
        key_id=KEY_ID,
        event_id=EVENT_ID,
        timestamp=str(NOW),
        signature=_signature(raw_body),
    )
    assert isinstance(payload, DisputeOpenedEvent)
    assert not hasattr(evidence, "raw_body")
    assert not hasattr(evidence, "signature")
    assert raw_body.decode("utf-8") not in repr(evidence)


def test_verifier_configuration_and_body_size_are_bounded() -> None:
    with pytest.raises(ValueError, match="at least 32 bytes"):
        PaymentWebhookVerifier(
            "short", provider=PROVIDER, merchant_account=MERCHANT, key_id=KEY_ID
        )
    with pytest.raises(ValueError, match="provider"):
        PaymentWebhookVerifier(
            SECRET, provider="Bad Provider", merchant_account=MERCHANT, key_id=KEY_ID
        )
    with pytest.raises(ValueError, match="key id"):
        PaymentWebhookVerifier(
            SECRET, provider=PROVIDER, merchant_account=MERCHANT, key_id="BAD KEY"
        )
    with pytest.raises(ValueError, match="replay window"):
        PaymentWebhookVerifier(
            SECRET,
            provider=PROVIDER,
            merchant_account=MERCHANT,
            key_id=KEY_ID,
            max_age_seconds=29,
        )

    verifier = PaymentWebhookVerifier(
        SECRET,
        provider=PROVIDER,
        merchant_account=MERCHANT,
        key_id=KEY_ID,
        max_body_bytes=1024,
        clock=lambda: NOW,
    )
    oversized = b"{" + (b" " * 1024) + b"}"
    with pytest.raises(PaymentWebhookPayloadError):
        verifier.verify(
            oversized,
            provider=PROVIDER,
            key_id=KEY_ID,
            event_id=EVENT_ID,
            timestamp=str(NOW),
            signature="v1=" + ("0" * 64),
        )
