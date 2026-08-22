from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from platform_api.config import Settings
from platform_api.main import create_app as create_platform_app
from platform_api.models import (
    CompanyModelGrant,
    GenerationTask,
    ModelCapability,
    ModelDefinition,
    RelayCallbackEvent,
    RelaySubmissionOutbox,
    TaskStatus,
)
from relay_service.callback import (
    CallbackDispatcher,
    CallbackPolicy,
    CallbackRoute,
    normalize_callback_url,
)
from relay_service.models import GenerationRequest
from relay_service.providers.mock import MockProviderAdapter
from relay_service.providers.router import ProviderRouter
from relay_service.queue import InMemoryWorkQueue
from relay_service.repository import InMemoryJobRepository
from relay_service.service import GenerationService


CALLBACK_SECRET = "cross-service-callback-secret-with-at-least-32-bytes"
CALLBACK_ROOT_URL = "http://platform.test/internal/relay-callbacks"
RELAY_BACKEND_ID = "new-api-v1"
RELAY_CONTRACT_REVISION = "generations.v1"
CALLBACK_URL = f"{CALLBACK_ROOT_URL}/{RELAY_BACKEND_ID}"
BOOTSTRAP_TOKEN = "cross-service-bootstrap-secret-32-bytes"


class PlatformCallbackTransport:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.responses = []

    async def post(self, url, body, headers, *, production):
        assert url == CALLBACK_URL
        assert production is False
        response = self.client.post(
            f"/internal/relay-callbacks/{RELAY_BACKEND_ID}",
            content=body,
            headers=headers,
        )
        self.responses.append(response)
        return response.status_code


def test_relay_generated_signed_callback_is_accepted_by_platform(
    tmp_path,
) -> None:
    relay_router = ProviderRouter([MockProviderAdapter()])
    relay_catalog = asyncio.run(relay_router.model_catalog())
    relay_revision = next(
        item.capability_revision
        for item in relay_catalog.data
        if item.id == "mock.video.v1"
    )
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    platform = create_platform_app(
        settings=Settings(
            database_url="sqlite+pysqlite://",
            auto_create_tables=True,
            development_header_auth_enabled=True,
            enable_bootstrap=True,
            bootstrap_token=BOOTSTRAP_TOKEN,
            internal_service_token="contract-internal-token",
            relay_default_backend_id=RELAY_BACKEND_ID,
            relay_default_contract_revision=RELAY_CONTRACT_REVISION,
            relay_backends={
                RELAY_BACKEND_ID: {
                    "base_url": "http://relay.test",
                    "client_id": "customer-platform",
                    "api_key": "cross-service-relay-api-key",
                    "contract_revision": RELAY_CONTRACT_REVISION,
                }
            },
            relay_callback_public_url=CALLBACK_ROOT_URL,
            relay_callback_signing_secrets={
                RELAY_BACKEND_ID: CALLBACK_SECRET
            },
            input_asset_filesystem_root=str(tmp_path / "input-assets"),
            input_asset_public_base_url="http://127.0.0.1:8200",
        ),
        engine=engine,
    )

    try:
        with TestClient(platform) as platform_client:
            bootstrap = platform_client.post(
                "/api/v1/bootstrap",
                headers={"X-Bootstrap-Token": BOOTSTRAP_TOKEN},
                json={
                    "company_name": "Contract Company",
                    "owner_email": "contract-owner@example.com",
                    "owner_display_name": "Contract Owner",
                },
            )
            assert bootstrap.status_code == 201, bootstrap.text
            identity = bootstrap.json()
            tenant_headers = {
                "X-Company-ID": identity["company_id"],
                "X-User-ID": identity["user_id"],
            }
            with platform.state.session_factory.begin() as session:
                model = ModelDefinition(
                    slug="mock.video.v1",
                    display_name="Mock Video V1",
                    provider_key="mock-video",
                    relay_capability_revision=relay_revision,
                )
                session.add(model)
                session.flush()
                session.add(
                    ModelCapability(
                        model_id=model.id,
                        capability_key="text-to-video",
                        config={"durations": [5], "max_outputs": 1},
                    )
                )
                session.add(
                    CompanyModelGrant(
                        company_id=identity["company_id"],
                        model_id=model.id,
                        enabled=True,
                        price_per_second_cents=10,
                    )
                )
                model_id = model.id

            recharged = platform_client.post(
                f"/api/v1/companies/{identity['company_id']}/wallet/recharge",
                headers=tenant_headers,
                json={
                    "amount_cents": 1000,
                    "idempotency_key": "contract-callback-recharge",
                },
            )
            assert recharged.status_code == 200, recharged.text
            created = platform_client.post(
                f"/api/v1/companies/{identity['company_id']}/tasks",
                headers={**tenant_headers, "X-Request-ID": "callback-origin-001"},
                json={
                    "model_id": model_id,
                    "idempotency_key": "contract-callback-task",
                    "request_payload": {
                        "mode": "text_to_video",
                        "prompt": "cross service callback contract",
                        "duration_seconds": 5,
                        "output_count": 1,
                    },
                },
            )
            assert created.status_code == 201, created.text
            task_id = created.json()["id"]
            with platform.state.session_factory() as session:
                task = session.get(GenerationTask, task_id)
                outbox = session.scalar(
                    select(RelaySubmissionOutbox).where(
                        RelaySubmissionOutbox.task_id == task_id
                    )
                )
                assert task is not None
                assert outbox is not None
                assert task.relay_backend_id == RELAY_BACKEND_ID
                assert task.relay_contract_revision == RELAY_CONTRACT_REVISION
                assert outbox.relay_backend_id == RELAY_BACKEND_ID
                assert outbox.relay_contract_revision == RELAY_CONTRACT_REVISION
                assert outbox.relay_payload["callback"] == {
                    "url": CALLBACK_URL
                }

            async def scenario() -> PlatformCallbackTransport:
                tenant_id = UUID(identity["company_id"])
                route = CallbackRoute(
                    url=normalize_callback_url(
                        CALLBACK_URL, production=False
                    ),
                    signing_secret=CALLBACK_SECRET,
                )
                policy = CallbackPolicy(
                    {tenant_id: route}, production=False
                )
                repository = InMemoryJobRepository()
                queue = InMemoryWorkQueue()
                service = GenerationService(
                    repository,
                    queue,
                    relay_router,
                    callback_policy=policy,
                )
                accepted = await service.submit(
                    GenerationRequest.model_validate(
                        {
                            "client_reference_id": task_id,
                            "model": "mock.video.v1",
                            "expected_capability_revision": relay_revision,
                            "mode": "text_to_video",
                            "inputs": {
                                "prompt": "cross service callback contract"
                            },
                            "callback": {"url": CALLBACK_URL},
                            "metadata": {
                                "platform_request_id": "callback-origin-001"
                            },
                        }
                    ),
                    "contract-callback-relay",
                    tenant_id,
                    request_id="callback-origin-001",
                )
                with platform.state.session_factory.begin() as session:
                    task = session.get(GenerationTask, task_id)
                    assert task is not None
                    task.relay_job_id = str(accepted.job_id)
                processed = await service.process_next()
                assert processed is not None
                assert processed.status == "processing"

                transport = PlatformCallbackTransport(platform_client)
                dispatcher = CallbackDispatcher(
                    repository,
                    policy,
                    transport=transport,
                )
                assert await dispatcher.dispatch_once() == 1
                return transport

            transport = asyncio.run(scenario())
            assert len(transport.responses) == 1
            assert transport.responses[0].status_code == 204
            assert transport.responses[0].headers[
                "x-relay-callback-duplicate"
            ] == "false"
            with platform.state.session_factory() as session:
                task = session.get(GenerationTask, task_id)
                assert task is not None and task.status == TaskStatus.PROCESSING
                assert session.scalar(
                    select(func.count()).select_from(RelayCallbackEvent)
                ) == 1
                event = session.scalar(select(RelayCallbackEvent))
                assert event is not None
                assert event.request_id == "callback-origin-001"
    finally:
        engine.dispose()
