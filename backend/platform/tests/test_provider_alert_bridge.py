from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json
import time
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.pool import StaticPool

from platform_api.config import Settings
from platform_api.main import create_app
from platform_api.models import RelayProviderAlertEvent
from platform_api.services.provider_alerts import (
    ProviderAlertDownstreamError,
    ProviderAlertForwarder,
    ProviderAlertService,
    RelayProviderAlertPayload,
)


INBOUND_SECRET = "provider-alert-inbound-test-secret-32-bytes!!"
OUTBOUND_SECRET = "provider-alert-outbound-test-secret-32-bytes!"


def _body(event_id: str, *, reason_code: str = "success_rate_low") -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "event_id": event_id,
            "type": "provider_monitor.success_rate_drop.triggered",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "incident": {
                "kind": "success_rate_drop",
                "state": "triggered",
                "provider_name": "aliyun",
                "generation": 1,
                "reason_code": reason_code,
                "sample_size": 20,
                "success_count": 8,
                "affected_routes": 2,
                "total_routes": 3,
                "success_rate_basis_points": 4000,
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _headers(
    raw_body: bytes,
    *,
    event_id: str,
    timestamp: int | None = None,
    secret: str = INBOUND_SECRET,
) -> dict[str, str]:
    timestamp_text = str(timestamp if timestamp is not None else int(time.time()))
    signature = hmac.new(
        secret.encode("utf-8"),
        timestamp_text.encode("ascii")
        + b"."
        + event_id.encode("ascii")
        + b"."
        + raw_body,
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Relay-Event-ID": event_id,
        "X-Relay-Timestamp": timestamp_text,
        "X-Relay-Signature": f"v1={signature}",
    }


class RecordingForwarder:
    def __init__(self, *, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[tuple[bytes, str, str]] = []

    async def forward(
        self,
        raw_body: bytes,
        *,
        event_id: str,
        request_id: str,
    ) -> int:
        self.calls.append((raw_body, event_id, request_id))
        if len(self.calls) <= self.failures:
            raise ProviderAlertDownstreamError()
        return 204


def _test_app(forwarder: RecordingForwarder):
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    settings = Settings(
        database_url="sqlite+pysqlite://",
        auto_create_tables=True,
        enable_bootstrap=False,
        provider_alert_signing_secret=INBOUND_SECRET,
        provider_alert_forward_webhook_url="http://alerts.example.test/provider",
        provider_alert_forward_signing_secret=OUTBOUND_SECRET,
    )
    app = create_app(
        settings=settings,
        engine=engine,
        provider_alert_forwarder=forwarder,  # type: ignore[arg-type]
    )
    return app, engine


def test_signed_alert_is_forwarded_once_and_identical_replay_is_idempotent():
    forwarder = RecordingForwarder()
    app, engine = _test_app(forwarder)
    event_id = str(uuid4())
    raw_body = _body(event_id)
    headers = _headers(raw_body, event_id=event_id)
    with TestClient(app) as client:
        first = client.post(
            "/internal/relay/provider-alerts",
            content=raw_body,
            headers=headers,
        )
        replay = client.post(
            "/internal/relay/provider-alerts",
            content=raw_body,
            headers=headers,
        )
    assert first.status_code == 201, first.text
    assert first.headers["x-provider-alert-duplicate"] == "false"
    assert replay.status_code == 200, replay.text
    assert replay.headers["x-provider-alert-duplicate"] == "true"
    assert len(forwarder.calls) == 1
    assert forwarder.calls[0][0] == raw_body
    assert forwarder.calls[0][1] == event_id
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(RelayProviderAlertEvent.id))) == 1
    engine.dispose()


def test_downstream_failure_rolls_back_receipt_and_same_event_retries():
    forwarder = RecordingForwarder(failures=1)
    app, engine = _test_app(forwarder)
    event_id = str(uuid4())
    raw_body = _body(event_id)
    headers = _headers(raw_body, event_id=event_id)
    with TestClient(app) as client:
        failed = client.post(
            "/internal/relay/provider-alerts",
            content=raw_body,
            headers=headers,
        )
        with app.state.session_factory() as session:
            assert session.scalar(
                select(func.count(RelayProviderAlertEvent.id))
            ) == 0
        retried = client.post(
            "/internal/relay/provider-alerts",
            content=raw_body,
            headers=headers,
        )
    assert failed.status_code == 503, failed.text
    assert retried.status_code == 201, retried.text
    assert len(forwarder.calls) == 2
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(RelayProviderAlertEvent.id))) == 1
    engine.dispose()


def test_same_event_id_with_different_signed_payload_conflicts():
    forwarder = RecordingForwarder()
    app, engine = _test_app(forwarder)
    event_id = str(uuid4())
    first_body = _body(event_id)
    different_body = _body(event_id, reason_code="worse_success_rate")
    with TestClient(app) as client:
        accepted = client.post(
            "/internal/relay/provider-alerts",
            content=first_body,
            headers=_headers(first_body, event_id=event_id),
        )
        conflict = client.post(
            "/internal/relay/provider-alerts",
            content=different_body,
            headers=_headers(different_body, event_id=event_id),
        )
    assert accepted.status_code == 201
    assert conflict.status_code == 409, conflict.text
    assert len(forwarder.calls) == 1
    engine.dispose()


@pytest.mark.parametrize(
    "header_factory",
    [
        lambda body, event_id: {},
        lambda body, event_id: _headers(body, event_id=event_id, secret="x" * 40),
        lambda body, event_id: _headers(
            body,
            event_id=event_id,
            timestamp=int(time.time()) - 301,
        ),
        lambda body, event_id: _headers(
            body,
            event_id=event_id,
            timestamp=int(time.time()) + 301,
        ),
    ],
)
def test_missing_invalid_stale_and_future_signatures_are_rejected(header_factory):
    forwarder = RecordingForwarder()
    app, engine = _test_app(forwarder)
    event_id = str(uuid4())
    raw_body = _body(event_id)
    with TestClient(app) as client:
        response = client.post(
            "/internal/relay/provider-alerts",
            content=raw_body,
            headers=header_factory(raw_body, event_id),
        )
    assert response.status_code == 401, response.text
    assert forwarder.calls == []
    engine.dispose()


def test_body_event_id_must_match_the_signed_header():
    forwarder = RecordingForwarder()
    app, engine = _test_app(forwarder)
    header_event_id = str(uuid4())
    raw_body = _body(str(uuid4()))
    with TestClient(app) as client:
        response = client.post(
            "/internal/relay/provider-alerts",
            content=raw_body,
            headers=_headers(raw_body, event_id=header_event_id),
        )
    assert response.status_code == 422, response.text
    assert forwarder.calls == []
    engine.dispose()


def test_unconfigured_receiver_fails_closed():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    app = create_app(
        settings=Settings(
            database_url="sqlite+pysqlite://",
            auto_create_tables=True,
            enable_bootstrap=False,
        ),
        engine=engine,
    )
    event_id = str(uuid4())
    raw_body = _body(event_id)
    with TestClient(app) as client:
        response = client.post(
            "/internal/relay/provider-alerts",
            content=raw_body,
            headers=_headers(raw_body, event_id=event_id),
        )
    assert response.status_code == 503
    engine.dispose()


def test_forwarder_signs_exact_bytes_and_does_not_follow_redirects():
    event_id = str(uuid4())
    raw_body = _body(event_id)
    received: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(204)

    forwarder = ProviderAlertForwarder(
        "https://alerts.example.com/provider",
        OUTBOUND_SECRET,
        transport=httpx.MockTransport(handler),
        clock=lambda: 1_786_377_600,
    )
    assert (
        asyncio.run(
            forwarder.forward(
                raw_body,
                event_id=event_id,
                request_id="request-one",
            )
        )
        == 204
    )
    request = received[0]
    assert request.content == raw_body
    assert request.headers["idempotency-key"] == event_id
    assert request.headers["x-alert-event-id"] == event_id
    assert request.headers["x-request-id"] == "request-one"
    timestamp = request.headers["x-alert-timestamp"]
    expected = hmac.new(
        OUTBOUND_SECRET.encode("utf-8"),
        timestamp.encode("ascii")
        + b"."
        + event_id.encode("ascii")
        + b"."
        + raw_body,
        hashlib.sha256,
    ).hexdigest()
    assert request.headers["x-alert-signature"] == f"v1={expected}"
    asyncio.run(forwarder.aclose())


def test_forwarder_turns_any_non_2xx_into_retryable_platform_failure():
    event_id = str(uuid4())
    raw_body = _body(event_id)
    requests: list[httpx.Request] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example/"})

    forwarder = ProviderAlertForwarder(
        "https://alerts.example.com/provider",
        OUTBOUND_SECRET,
        transport=httpx.MockTransport(redirect),
    )
    with pytest.raises(ProviderAlertDownstreamError):
        asyncio.run(
            forwarder.forward(
                raw_body,
                event_id=event_id,
                request_id="request-two",
            )
        )
    assert len(requests) == 1
    asyncio.run(forwarder.aclose())


def test_production_forwarder_pins_public_dns_and_blocks_private_rebinding_before_post():
    event_id = str(uuid4())
    transport_calls: list[httpx.Request] = []
    answers = iter((("8.8.8.8",), ("8.8.8.8", "127.0.0.1")))

    async def resolver(host: str, port: int) -> tuple[str, ...]:
        assert host == "alerts.example.com"
        assert port == 443
        return next(answers)

    async def handler(request: httpx.Request) -> httpx.Response:
        transport_calls.append(request)
        return httpx.Response(204)

    forwarder = ProviderAlertForwarder(
        "https://alerts.example.com/provider",
        OUTBOUND_SECRET,
        production=True,
        transport=httpx.MockTransport(handler),
        resolver=resolver,
    )

    async def exercise() -> None:
        assert await forwarder.forward(
            _body(event_id), event_id=event_id, request_id="request-public"
        ) == 204
        rebinding_event_id = str(uuid4())
        with pytest.raises(ProviderAlertDownstreamError):
            await forwarder.forward(
                _body(rebinding_event_id),
                event_id=rebinding_event_id,
                request_id="request-rebound-private",
            )
        await forwarder.aclose()

    asyncio.run(exercise())
    # The private answer is rejected in the protected transport, before the
    # inner transport can create a downstream HTTP request.
    assert len(transport_calls) == 1
    assert transport_calls[0].headers["host"] == "alerts.example.com"


def test_async_forwarder_does_not_block_the_event_loop_while_downstream_is_slow():
    event_id = str(uuid4())
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_handler(_: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return httpx.Response(204)

    forwarder = ProviderAlertForwarder(
        "https://alerts.example.com/provider",
        OUTBOUND_SECRET,
        transport=httpx.MockTransport(slow_handler),
    )

    async def exercise() -> None:
        delivery = asyncio.create_task(
            forwarder.forward(
                _body(event_id), event_id=event_id, request_id="request-slow"
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        # The downstream transport yielded control instead of pinning the
        # event loop; a synchronous Client.post would already be complete here.
        assert not delivery.done()
        release.set()
        assert await delivery == 204
        await forwarder.aclose()

    asyncio.run(exercise())


def test_slow_forward_releases_the_read_transaction_and_pool_connection(tmp_path):
    database_path = tmp_path / "provider-alert-transaction-boundary.db"
    engine = create_engine(
        f"sqlite+pysqlite:///{database_path}",
        connect_args={"check_same_thread": False},
        pool_size=1,
        max_overflow=0,
    )
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+pysqlite:///{database_path}",
            auto_create_tables=True,
            enable_bootstrap=False,
        ),
        engine=engine,
    )
    event_id = str(uuid4())
    raw_body = _body(event_id)
    payload = RelayProviderAlertPayload.model_validate_json(raw_body)
    session = app.state.session_factory()

    class TransactionProbeForwarder:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def forward(self, *_args, **_kwargs) -> int:
            assert not session.in_transaction()
            # With a single-connection pool this would time out if the read
            # transaction still owned its connection during the slow webhook.
            with engine.connect() as connection:
                assert connection.execute(text("SELECT 1")).scalar_one() == 1
            self.started.set()
            await self.release.wait()
            return 204

    forwarder = TransactionProbeForwarder()

    async def exercise() -> None:
        delivery = asyncio.create_task(
            ProviderAlertService.record_and_forward(
                session,
                payload=payload,
                raw_body=raw_body,
                event_id=event_id,
                payload_sha256=hashlib.sha256(raw_body).hexdigest(),
                delivery_timestamp=datetime.now(timezone.utc),
                request_id="request-transaction-boundary",
                forwarder=forwarder,  # type: ignore[arg-type]
            )
        )
        await asyncio.wait_for(forwarder.started.wait(), timeout=1)
        assert not session.in_transaction()
        forwarder.release.set()
        entry, duplicate = await delivery
        assert entry.id == event_id
        assert not duplicate
        session.commit()

    asyncio.run(exercise())
    session.close()
    engine.dispose()


def test_concurrent_same_alert_does_not_hold_a_database_lock_or_duplicate_receipt(tmp_path):
    database_path = tmp_path / "provider-alert-concurrent-replay.db"
    engine = create_engine(
        f"sqlite+pysqlite:///{database_path}",
        connect_args={"check_same_thread": False},
        pool_size=2,
        max_overflow=0,
    )
    app = create_app(
        settings=Settings(
            database_url=f"sqlite+pysqlite:///{database_path}",
            auto_create_tables=True,
            enable_bootstrap=False,
        ),
        engine=engine,
    )
    event_id = str(uuid4())
    raw_body = _body(event_id)
    payload = RelayProviderAlertPayload.model_validate_json(raw_body)

    class ConcurrentForwarder:
        def __init__(self) -> None:
            self.calls = 0
            self.both_started = asyncio.Event()
            self.release = asyncio.Event()

        async def forward(self, *_args, **_kwargs) -> int:
            self.calls += 1
            if self.calls == 2:
                self.both_started.set()
            await self.release.wait()
            return 204

    forwarder = ConcurrentForwarder()

    async def invoke(request_id: str) -> tuple[RelayProviderAlertEvent, bool]:
        session = app.state.session_factory()
        try:
            result = await ProviderAlertService.record_and_forward(
                session,
                payload=payload,
                raw_body=raw_body,
                event_id=event_id,
                payload_sha256=hashlib.sha256(raw_body).hexdigest(),
                delivery_timestamp=datetime.now(timezone.utc),
                request_id=request_id,
                forwarder=forwarder,  # type: ignore[arg-type]
            )
            # This mirrors the function-scoped FastAPI dependency committing
            # immediately after the endpoint coroutine returns.
            session.commit()
            return result
        finally:
            session.close()

    async def exercise() -> list[tuple[RelayProviderAlertEvent, bool]]:
        first = asyncio.create_task(invoke("request-concurrent-one"))
        second = asyncio.create_task(invoke("request-concurrent-two"))
        await asyncio.wait_for(forwarder.both_started.wait(), timeout=1)
        forwarder.release.set()
        return list(await asyncio.wait_for(asyncio.gather(first, second), timeout=2))

    results = asyncio.run(exercise())
    assert forwarder.calls == 2
    assert sorted(duplicate for _, duplicate in results) == [False, True]
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(RelayProviderAlertEvent.id))) == 1
    engine.dispose()
