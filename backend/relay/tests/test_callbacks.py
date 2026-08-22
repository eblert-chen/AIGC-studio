from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from relay_service.auth import ClientCredential, StaticClientAuthenticator
from relay_service.artifacts import InMemoryArtifactStore
from relay_service.callback import (
    AioHttpCallbackTransport,
    CallbackDispatcher,
    CallbackPolicy,
    CallbackRoute,
    CallbackTransportError,
    normalize_callback_url,
    parse_callback_routes,
)
from relay_service.callback_worker import consume as consume_callbacks
from relay_service.config import RelaySettings
from relay_service.errors import RelayError
from relay_service.downloader import DownloadedArtifact
from relay_service.main import create_app
from relay_service.models import (
    CallbackDeliveryStatus,
    GenerationInputs,
    GenerationMode,
    GenerationRequest,
)
from relay_service.outbox import OutboxDispatcher
from relay_service.providers.mock import MockProviderAdapter
from relay_service.providers.router import ProviderRouter
from relay_service.queue import InMemoryWorkQueue
from relay_service.repository import InMemoryJobRepository
from relay_service.service import GenerationService
from relay_service.sql_repository import SqlAlchemyJobRepository
from relay_service.transfer import ArtifactTransferService


SECRET = "callback-test-secret-that-is-at-least-32-bytes"
MOCK_CAPABILITY_REVISION = asyncio.run(
    ProviderRouter([MockProviderAdapter()]).model_catalog()
).data[0].capability_revision


@pytest.mark.asyncio
async def test_production_callback_timeout_includes_dns_resolution() -> None:
    async def blocked_resolver(_host: str) -> list[str]:
        await asyncio.Event().wait()
        return []

    transport = AioHttpCallbackTransport(
        timeout_seconds=0.01,
        resolve_host=blocked_resolver,
    )

    with pytest.raises(CallbackTransportError, match="request failed"):
        await asyncio.wait_for(
            transport.post(
                "https://alerts.example.com/relay",
                b"{}",
                {"Content-Type": "application/json"},
                production=True,
            ),
            timeout=0.5,
        )


class RecordingTransport:
    def __init__(self, statuses: list[int] | None = None) -> None:
        self.statuses = list(statuses or [204])
        self.requests: list[tuple[str, bytes, dict[str, str], bool]] = []

    async def post(self, url, body, headers, *, production):
        self.requests.append((url, body, headers, production))
        return self.statuses.pop(0)


class BlockingTransport:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.requests: list[tuple[str, bytes, dict[str, str], bool]] = []

    async def post(self, url, body, headers, *, production):
        self.requests.append((url, body, headers, production))
        self.entered.set()
        await self.release.wait()
        return 204


class StaticDownloader:
    async def download(self, url: str) -> DownloadedArtifact:
        content = b"generated-video"
        return DownloadedArtifact(
            content=BytesIO(content),
            content_type="video/mp4",
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )


def generation_request(callback_url: str | None = None) -> GenerationRequest:
    payload = {
        "model": "mock.video.v1",
        "expected_capability_revision": MOCK_CAPABILITY_REVISION,
        "mode": GenerationMode.TEXT_TO_VIDEO,
        "inputs": GenerationInputs(prompt="private prompt must not leak"),
        "metadata": {"private": "metadata must not leak"},
    }
    if callback_url is not None:
        payload["callback"] = {"url": callback_url}
    return GenerationRequest.model_validate(payload)


def test_callback_route_configuration_is_exact_and_production_safe() -> None:
    tenant_id = uuid4()
    serialized = json.dumps(
        {
            str(tenant_id): {
                "url": "https://platform.example.com/internal/relay-callback",
                "signing_secret": SECRET,
            }
        }
    )
    routes = parse_callback_routes(serialized, production=True)
    policy = CallbackPolicy(routes, production=True)

    assert policy.authorize(
        tenant_id,
        "https://platform.example.com/internal/relay-callback",
    ) == "https://platform.example.com/internal/relay-callback"
    with pytest.raises(RelayError) as mismatch:
        policy.authorize(
            tenant_id,
            "https://platform.example.com/internal/other",
        )
    assert mismatch.value.code == "CALLBACK_URL_NOT_ALLOWED"
    with pytest.raises(RuntimeError, match="HTTPS port 443"):
        parse_callback_routes(
            json.dumps(
                {
                    str(tenant_id): {
                        "url": "http://platform.example.com/callback",
                        "signing_secret": SECRET,
                    }
                }
            ),
            production=True,
        )
    with pytest.raises(RuntimeError, match="not public"):
        parse_callback_routes(
            json.dumps(
                {
                    str(tenant_id): {
                        "url": "https://127.0.0.1/callback",
                        "signing_secret": SECRET,
                    }
                }
            ),
            production=True,
        )
    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        parse_callback_routes(
            json.dumps(
                {
                    str(tenant_id): {
                        "url": "https://platform.example.com/callback",
                        "signing_secret": "short",
                    }
                }
            ),
            production=True,
        )
    with pytest.raises(RuntimeError, match="obvious placeholder"):
        parse_callback_routes(
            json.dumps(
                {
                    str(tenant_id): {
                        "url": "https://platform.example.com/callback",
                        "signing_secret": "change-me-placeholder-secret-value-123456",
                    }
                }
            ),
            production=True,
        )
    settings = RelaySettings(
        environment="production",
        runtime_mode="production",
        database_url="postgresql+asyncpg://db/relay",
        redis_url="redis://queue",
        artifact_store="huawei_obs",
        callback_routes={
            tenant_id: CallbackRoute(
                url="http://127.0.0.1/callback",
                signing_secret=SECRET,
            )
        },
    )
    with pytest.raises(RuntimeError, match="HTTPS port 443"):
        settings.validate()


def test_callback_request_contains_only_url() -> None:
    with pytest.raises(ValueError):
        GenerationRequest.model_validate(
            {
                "model": "mock.video.v1",
                "mode": "text_to_video",
                "inputs": {"prompt": "test"},
                "callback": {
                    "url": "https://platform.example.com/callback",
                    "signing_secret": "must-never-be-accepted",
                },
            }
        )


def test_public_address_guard_rejects_private_and_mapped_addresses() -> None:
    with pytest.raises(Exception, match="non-public"):
        AioHttpCallbackTransport._assert_public(["10.0.0.1"])
    with pytest.raises(Exception, match="non-public"):
        AioHttpCallbackTransport._assert_public(["::ffff:127.0.0.1"])
    AioHttpCallbackTransport._assert_public(["93.184.216.34"])


def test_processing_callback_is_signed_minimal_and_idempotent() -> None:
    async def scenario() -> None:
        tenant_id = uuid4()
        url = "http://127.0.0.1:9000/internal/relay-callback"
        route = CallbackRoute(
            url=normalize_callback_url(url, production=False),
            signing_secret=SECRET,
        )
        policy = CallbackPolicy({tenant_id: route}, production=False)
        repository = InMemoryJobRepository()
        queue = InMemoryWorkQueue()
        service = GenerationService(
            repository,
            queue,
            ProviderRouter([MockProviderAdapter()]),
            callback_policy=policy,
        )
        accepted = await service.submit(
            generation_request(url), "callback-processing", tenant_id
        )
        processing = await service.process_next()
        assert processing is not None
        assert processing.status == "processing"

        pending = await repository.list_callback_deliveries(tenant_id)
        assert len(pending) == 1
        assert pending[0].job_status == "processing"
        assert pending[0].delivery_status == "pending"

        transport = RecordingTransport([204, 204])
        dispatcher = CallbackDispatcher(
            repository,
            policy,
            transport=transport,
            now=lambda: datetime(2026, 8, 3, tzinfo=timezone.utc),
        )
        assert await dispatcher.dispatch_once() == 1
        assert await dispatcher.dispatch_once() == 0
        assert len(transport.requests) == 1
        sent_url, body, headers, production = transport.requests[0]
        event = json.loads(body)
        assert sent_url == route.url
        assert production is False
        assert event["event_id"] == headers["X-Relay-Event-ID"]
        assert event["type"] == "generation.status_changed"
        assert event["job"]["id"] == str(accepted.job_id)
        assert event["job"]["status"] == "processing"
        assert "prompt" not in body.decode()
        assert "metadata" not in body.decode()
        expected = hmac.new(
            SECRET.encode(),
            (
                headers["X-Relay-Timestamp"].encode()
                + b"."
                + headers["X-Relay-Event-ID"].encode()
                + b"."
                + body
            ),
            hashlib.sha256,
        ).hexdigest()
        assert headers["X-Relay-Signature"] == f"v1={expected}"
        assert headers["X-Request-ID"] == (
            f"relay-callback-{headers['X-Relay-Event-ID']}"
        )

        delivered = await repository.list_callback_deliveries(
            tenant_id, status=CallbackDeliveryStatus.DELIVERED
        )
        assert len(delivered) == 1
        assert delivered[0].attempts == 1
        assert delivered[0].response_status == 204

        progress_event = {
            "event_id": "evt-progress-50",
            "provider_task_id": processing.provider_task_id,
            "status": "processing",
            "progress": 50,
        }
        repeated_progress_event = {
            **progress_event,
            "event_id": "evt-progress-50-again",
        }
        for provider_event in (progress_event, repeated_progress_event):
            await service.receive_webhook(
                "mock-video",
                json.dumps(provider_event).encode(),
                {"x-mock-webhook-secret": "development-only-secret"},
            )
        progress_deliveries = await repository.list_callback_deliveries(
            tenant_id
        )
        assert len(progress_deliveries) == 2
        assert len({item.event_id for item in progress_deliveries}) == 2
        assert await dispatcher.dispatch_once() == 1
        assert len(transport.requests) == 2

    asyncio.run(scenario())


def test_slow_callback_does_not_preclaim_later_batch_items() -> None:
    async def scenario() -> None:
        tenant_id = uuid4()
        url = "http://127.0.0.1:9000/internal/relay-callback"
        route = CallbackRoute(
            url=normalize_callback_url(url, production=False),
            signing_secret=SECRET,
        )
        policy = CallbackPolicy({tenant_id: route}, production=False)
        repository = InMemoryJobRepository()
        service = GenerationService(
            repository,
            InMemoryWorkQueue(),
            ProviderRouter([MockProviderAdapter()]),
            callback_policy=policy,
        )
        for index in range(2):
            await service.submit(
                generation_request(url),
                f"slow-callback-batch-{index}",
                tenant_id,
            )
            processing = await service.process_next()
            assert processing is not None
            assert processing.status == "processing"

        slow_transport = BlockingTransport()
        slow_dispatcher = CallbackDispatcher(
            repository,
            policy,
            transport=slow_transport,
        )
        slow_attempt = asyncio.create_task(
            slow_dispatcher.dispatch_once(batch_size=2)
        )
        await asyncio.wait_for(slow_transport.entered.wait(), timeout=1)

        # Only the callback currently inside the HTTP call owns a lease. The
        # later item remains immediately available to another worker instead of
        # aging behind the slow request until the 60-second reclaim timeout.
        during_slow_request = await repository.list_callback_deliveries(
            tenant_id
        )
        assert sum(
            item.delivery_status == CallbackDeliveryStatus.DELIVERING
            for item in during_slow_request
        ) == 1
        assert sum(
            item.delivery_status == CallbackDeliveryStatus.PENDING
            for item in during_slow_request
        ) == 1

        fast_transport = RecordingTransport([204])
        fast_dispatcher = CallbackDispatcher(
            repository,
            policy,
            transport=fast_transport,
        )
        assert await fast_dispatcher.dispatch_once(batch_size=1) == 1
        assert len(fast_transport.requests) == 1

        slow_transport.release.set()
        assert await asyncio.wait_for(slow_attempt, timeout=1) == 1
        assert len(slow_transport.requests) == 1

        delivered = await repository.list_callback_deliveries(
            tenant_id,
            status=CallbackDeliveryStatus.DELIVERED,
        )
        assert len(delivered) == 2
        assert {item.attempts for item in delivered} == {1}
        sent_event_ids = {
            slow_transport.requests[0][2]["X-Relay-Event-ID"],
            fast_transport.requests[0][2]["X-Relay-Event-ID"],
        }
        assert len(sent_event_ids) == 2

    asyncio.run(scenario())


def test_callback_retries_then_moves_to_dead_letter() -> None:
    async def scenario() -> None:
        tenant_id = uuid4()
        url = "http://127.0.0.1:9000/callback"
        policy = CallbackPolicy(
            {
                tenant_id: CallbackRoute(
                    url=normalize_callback_url(url, production=False),
                    signing_secret=SECRET,
                )
            },
            production=False,
        )
        repository = InMemoryJobRepository()
        service = GenerationService(
            repository,
            InMemoryWorkQueue(),
            ProviderRouter([MockProviderAdapter()]),
            callback_policy=policy,
        )
        await service.submit(generation_request(url), "callback-retry", tenant_id)
        await service.process_next()
        transport = RecordingTransport([503, 503, 503])
        dispatcher = CallbackDispatcher(
            repository,
            policy,
            transport=transport,
            max_attempts=3,
            base_delay_seconds=0.001,
            max_delay_seconds=0.001,
        )

        assert await dispatcher.dispatch_once() == 0
        await asyncio.sleep(0.005)
        assert await dispatcher.dispatch_once() == 0
        await asyncio.sleep(0.005)
        assert await dispatcher.dispatch_once() == 0

        dead = await repository.list_callback_deliveries(
            tenant_id, status=CallbackDeliveryStatus.DEAD_LETTER
        )
        assert len(dead) == 1
        assert dead[0].attempts == 3
        assert dead[0].response_status == 503
        assert dead[0].last_error == "HTTP 503"

    asyncio.run(scenario())


def test_failed_cancelled_and_transferred_success_create_terminal_events() -> None:
    async def scenario() -> None:
        url = "http://127.0.0.1:9000/callback"

        failed_tenant = uuid4()
        failed_policy = CallbackPolicy(
            {
                failed_tenant: CallbackRoute(
                    url=normalize_callback_url(url, production=False),
                    signing_secret=SECRET,
                )
            },
            production=False,
        )
        failed_repository = InMemoryJobRepository()
        failed_service = GenerationService(
            failed_repository,
            InMemoryWorkQueue(),
            ProviderRouter([MockProviderAdapter(fail_submit=True)]),
            max_worker_attempts=1,
            callback_policy=failed_policy,
        )
        await failed_service.submit(
            generation_request(url), "callback-failed", failed_tenant
        )
        failed = await failed_service.process_next()
        assert failed is not None and failed.status == "failed"
        failed_events = await failed_repository.list_callback_deliveries(
            failed_tenant
        )
        assert [event.job_status for event in failed_events] == ["failed"]

        terminal_tenant = uuid4()
        terminal_policy = CallbackPolicy(
            {
                terminal_tenant: CallbackRoute(
                    url=normalize_callback_url(url, production=False),
                    signing_secret=SECRET,
                )
            },
            production=False,
        )
        terminal_repository = InMemoryJobRepository()
        generation_queue = InMemoryWorkQueue()
        transfer_queue = InMemoryWorkQueue()
        terminal_service = GenerationService(
            terminal_repository,
            generation_queue,
            ProviderRouter([MockProviderAdapter()]),
            transfer_queue=transfer_queue,
            callback_policy=terminal_policy,
        )
        await terminal_service.submit(
            generation_request(url), "callback-cancelled", terminal_tenant
        )
        cancelled_processing = await terminal_service.process_next()
        assert cancelled_processing is not None
        cancelled_event = {
            "event_id": "evt-cancelled",
            "provider_task_id": cancelled_processing.provider_task_id,
            "status": "cancelled",
        }
        await terminal_service.receive_webhook(
            "mock-video",
            json.dumps(cancelled_event).encode(),
            {"x-mock-webhook-secret": "development-only-secret"},
        )

        await terminal_service.submit(
            generation_request(url), "callback-succeeded", terminal_tenant
        )
        succeeded_processing = await terminal_service.process_next()
        assert succeeded_processing is not None
        success_event = {
            "event_id": "evt-succeeded",
            "provider_task_id": succeeded_processing.provider_task_id,
            "status": "succeeded",
            "outputs": [
                {
                    "url": "https://provider.example.com/output.mp4",
                    "media_type": "video",
                    "content_type": "video/mp4",
                }
            ],
        }
        await terminal_service.receive_webhook(
            "mock-video",
            json.dumps(success_event).encode(),
            {"x-mock-webhook-secret": "development-only-secret"},
        )
        transfer = ArtifactTransferService(
            terminal_repository,
            transfer_queue,
            StaticDownloader(),
            InMemoryArtifactStore(),
        )
        transferred = await transfer.process_next()
        assert transferred is not None and transferred.status == "succeeded"
        # A repeated save of the same state cannot create a second event.
        await terminal_repository.save(transferred)

        terminal_events = await terminal_repository.list_callback_deliveries(
            terminal_tenant
        )
        statuses = [event.job_status for event in terminal_events]
        assert statuses.count("cancelled") == 1
        assert statuses.count("succeeded") == 1
        assert statuses.count("processing") == 2

    asyncio.run(scenario())


def test_sql_callback_outbox_survives_restart_and_is_tenant_scoped(tmp_path) -> None:
    async def scenario() -> None:
        tenant_id = uuid4()
        other_tenant = uuid4()
        url = "http://127.0.0.1:9000/callback"
        policy = CallbackPolicy(
            {
                tenant_id: CallbackRoute(
                    url=normalize_callback_url(url, production=False),
                    signing_secret=SECRET,
                )
            },
            production=False,
        )
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'callbacks.db'}"
        repository = SqlAlchemyJobRepository.from_url(database_url)
        await repository.create_schema()
        queue = InMemoryWorkQueue()
        service = GenerationService(
            repository,
            queue,
            ProviderRouter([MockProviderAdapter()]),
            callback_policy=policy,
        )
        outbox = OutboxDispatcher(repository, queue)
        accepted = await service.submit(
            generation_request(url), "sql-callback", tenant_id
        )
        assert await outbox.dispatch_once() == 1
        await service.process_next()
        assert len(await repository.list_callback_deliveries(tenant_id)) == 1
        assert await repository.list_callback_deliveries(other_tenant) == []
        await repository.dispose()

        reopened = SqlAlchemyJobRepository.from_url(database_url)
        persisted = await reopened.list_callback_deliveries(tenant_id)
        assert len(persisted) == 1
        assert persisted[0].job_id == accepted.job_id
        assert persisted[0].job_status == "processing"
        await reopened.dispose()

    asyncio.run(scenario())


def test_callback_operations_endpoint_is_authenticated_and_tenant_scoped() -> None:
    tenant_id = uuid4()
    other_tenant = uuid4()
    url = "http://127.0.0.1:9000/callback"
    settings = RelaySettings(
        callback_routes={
            tenant_id: CallbackRoute(
                url=normalize_callback_url(url, production=False),
                signing_secret=SECRET,
            )
        }
    )
    authenticator = StaticClientAuthenticator(
        {
            "owner": ClientCredential(tenant_id=tenant_id, api_key="owner-key"),
            "other": ClientCredential(tenant_id=other_tenant, api_key="other-key"),
        }
    )
    app = create_app(
        settings=settings,
        authenticator=authenticator,
        process_in_background=False,
    )
    client = TestClient(app)
    body = generation_request(url).model_dump(mode="json", exclude_none=True)
    submitted = client.post(
        "/v1/generations",
        json=body,
        headers={
            "X-Client-ID": "owner",
            "X-API-Key": "owner-key",
            "Idempotency-Key": "ops-callback",
        },
    )
    assert submitted.status_code == 202
    asyncio.run(app.state.generation_service.process_next())

    missing = client.get("/v1/operations/callback-deliveries")
    owner = client.get(
        "/v1/operations/callback-deliveries",
        headers={"X-Client-ID": "owner", "X-API-Key": "owner-key"},
    )
    other = client.get(
        "/v1/operations/callback-deliveries",
        headers={"X-Client-ID": "other", "X-API-Key": "other-key"},
    )
    assert missing.status_code == 401
    assert owner.status_code == 200
    assert len(owner.json()["items"]) == 1
    assert owner.json()["items"][0]["request_id"] == (
        submitted.headers["X-Request-ID"]
    )
    assert "callback_url" not in owner.text
    assert other.json()["items"] == []


def test_callback_worker_loop_stops_at_delivery_boundary() -> None:
    async def scenario() -> None:
        stop = asyncio.Event()

        class OneCycleDispatcher:
            calls = 0

            async def dispatch_once(self):
                self.calls += 1
                stop.set()
                return 1

        dispatcher = OneCycleDispatcher()
        await consume_callbacks(dispatcher, stop, idle_seconds=0.001)
        assert dispatcher.calls == 1

    asyncio.run(scenario())
