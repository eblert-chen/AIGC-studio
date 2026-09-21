from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from platform_api.dependencies import get_db
from platform_api.routers import payments
from platform_api.services.errors import DomainError
from platform_api.services.payment_webhooks import (
    MAX_PAYMENT_WEBHOOK_BODY_BYTES,
    PaymentWebhookVerifier,
    PaymentWebhookVerifierRegistry,
    payment_webhook_signing_input,
)


LIMIT = MAX_PAYMENT_WEBHOOK_BODY_BYTES
PROVIDER = "body-limit-test"
KEY_ID = "body-limit-key"
SECRET = "test-only-body-limit-signing-secret-32-bytes"
TIMESTAMP = "2000000000"
EVENT_ID = "8603d9e0-326b-4a79-81f2-c1e09319543a"


@pytest.fixture
def webhook_app(monkeypatch):
    """Exercise the real router without product startup, a DB, or PSP I/O."""
    app = FastAPI()
    app.include_router(payments.router)
    session = Mock(spec=["commit"])
    app.dependency_overrides[get_db] = lambda: session
    registry = Mock(spec=["verify"])
    registry.verify.return_value = (object(), object())
    app.state.payment_webhook_verifier_registry = registry
    inbox = SimpleNamespace(
        id="test-inbox",
        status=SimpleNamespace(value="received"),
        provider_event_id=EVENT_ID,
        event_type="payment.expired",
        last_error_code=None,
    )
    ingest = Mock(return_value=inbox)
    process = Mock(return_value=(inbox, None))
    monkeypatch.setattr(payments.CommercialPaymentService, "ingest_webhook_event", ingest)
    monkeypatch.setattr(payments.CommercialPaymentService, "process_inbox_event", process)

    @app.exception_handler(DomainError)
    async def domain_error_handler(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.code})

    return SimpleNamespace(
        app=app, session=session, registry=registry, ingest=ingest, process=process
    )


def _request(app, chunks, *, headers=(), disconnect=False):
    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": disconnect or index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
    ]
    if disconnect:
        messages.append({"type": "http.disconnect"})
    consumed = []
    sent = []

    async def receive():
        assert len(consumed) < len(messages), "Handler drained past the supplied body"
        message = messages[len(consumed)]
        consumed.append(message)
        return message

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": f"/internal/payment-webhooks/{PROVIDER}",
        "raw_path": f"/internal/payment-webhooks/{PROVIDER}".encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json"), *headers],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    return status, consumed


def _assert_no_verification_or_writes(fixture):
    fixture.registry.verify.assert_not_called()
    fixture.ingest.assert_not_called()
    fixture.process.assert_not_called()
    fixture.session.commit.assert_not_called()


@pytest.mark.parametrize("declared_length", [None, b"0", b"1", str(LIMIT).encode()])
@pytest.mark.parametrize("chunks", [
    [b"x" * (LIMIT // 2)] * 20,
    [b"x" * (LIMIT + 1), b"must-not-be-received"],
])
def test_over_limit_stream_stops_at_first_excess_chunk(webhook_app, declared_length, chunks):
    headers = [] if declared_length is None else [(b"content-length", declared_length)]
    status, consumed = _request(webhook_app.app, chunks, headers=headers)
    assert status == 413
    assert len(consumed) == (3 if len(chunks) == 20 else 1)
    _assert_no_verification_or_writes(webhook_app)


@pytest.mark.parametrize("value", [b"", b"-1", b"+1", b" 1", b"1 ", b"1.0", b"1e3", b"1, 1", b"oops"])
def test_malformed_content_length_rejects_before_receiving(webhook_app, value):
    status, consumed = _request(
        webhook_app.app, [b"must-not-be-received"], headers=[(b"content-length", value)]
    )
    assert status == 400
    assert consumed == []
    _assert_no_verification_or_writes(webhook_app)


@pytest.mark.parametrize("values", [(b"1", b"1"), (b"1", b"999999")])
def test_duplicate_content_length_rejects_before_receiving(webhook_app, values):
    status, consumed = _request(
        webhook_app.app, [b"must-not-be-received"],
        headers=[(b"content-length", value) for value in values],
    )
    assert status == 400
    assert consumed == []
    _assert_no_verification_or_writes(webhook_app)


def test_declared_oversize_body_rejects_without_receiving(webhook_app):
    status, consumed = _request(
        webhook_app.app, [b"must-not-be-received"],
        headers=[(b"content-length", str(LIMIT + 1).encode())],
    )
    assert status == 413
    assert consumed == []
    _assert_no_verification_or_writes(webhook_app)


@pytest.mark.parametrize("size", [0, LIMIT - 1, LIMIT])
@pytest.mark.parametrize("declared", [False, True])
def test_within_limit_body_reaches_verifier_unchanged(webhook_app, size, declared):
    raw = b"a" * size
    headers = [(b"content-length", str(size).encode())] if declared else []
    status, consumed = _request(webhook_app.app, [raw[:17], raw[17:]], headers=headers)
    assert status == 202
    assert len(consumed) == 2
    assert webhook_app.registry.verify.call_args.args == (raw,)
    webhook_app.ingest.assert_called_once()
    webhook_app.session.commit.assert_called_once()
    webhook_app.process.assert_called_once()


def test_disconnected_body_rejects_without_verifying_or_writing(webhook_app):
    status, consumed = _request(webhook_app.app, [b'{"incomplete":'], disconnect=True)
    assert status == 400
    assert len(consumed) == 2
    _assert_no_verification_or_writes(webhook_app)


@pytest.mark.parametrize("tamper", [False, True])
def test_chunked_payment_signature_uses_exact_original_bytes(webhook_app, tamper):
    raw = json.dumps({
        "api_version": "v1",
        "schema_version": 1,
        "event_id": EVENT_ID,
        "provider": PROVIDER,
        "key_id": KEY_ID,
        "occurred_at": "2033-05-18T03:33:20Z",
        "type": "payment.expired",
        "data": {
            "order_id": "6303561d-454c-4114-b06a-88b1014c6066",
            "provider_payment_id": "test-payment-001",
            "failure_code": "expired",
        },
    }, indent=2).encode() + b"\r\n"
    signing_input = payment_webhook_signing_input(
        provider=PROVIDER, key_id=KEY_ID, timestamp=TIMESTAMP,
        event_id=EVENT_ID, raw_body=raw,
    )
    signature = "v1=" + hmac.new(SECRET.encode(), signing_input, hashlib.sha256).hexdigest()
    registry = PaymentWebhookVerifierRegistry([PaymentWebhookVerifier(
        SECRET, provider=PROVIDER, merchant_account="test-merchant", key_id=KEY_ID,
        clock=lambda: int(TIMESTAMP),
    )])
    webhook_app.app.state.payment_webhook_verifier_registry = registry
    transmitted = raw + b" " if tamper else raw
    headers = [
        (b"x-payment-key-id", KEY_ID.encode()),
        (b"x-payment-event-id", EVENT_ID.encode()),
        (b"x-payment-timestamp", TIMESTAMP.encode()),
        (b"x-payment-signature", signature.encode()),
    ]
    status, _ = _request(
        webhook_app.app, [transmitted[:11], transmitted[11:33], transmitted[33:]],
        headers=headers,
    )
    if tamper:
        assert status == 401
        webhook_app.ingest.assert_not_called()
        webhook_app.process.assert_not_called()
        webhook_app.session.commit.assert_not_called()
    else:
        assert status == 202
        evidence = webhook_app.ingest.call_args.kwargs["evidence"]
        assert evidence.payload_sha256 == hashlib.sha256(raw).hexdigest()
        webhook_app.session.commit.assert_called_once()
