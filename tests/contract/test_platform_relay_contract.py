from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from platform_api.relay_client import (
    HttpxRelayClient,
    RelayGenerationRequest,
    RelayJobSnapshot,
    RelayPermanentError,
)
from platform_api.request_ids import (
    normalize_request_id as normalize_platform_request_id,
)
from platform_api.services.relay_outbox import (
    RelayPayloadMapper,
)
from relay_service.auth import (
    ClientCredential,
    StaticClientAuthenticator,
)
from relay_service.main import create_app as create_relay_app
from relay_service.models import (
    GeneratedAsset,
    GenerationRequest,
    JobStatus,
)
from relay_service.providers.mock import MockProviderAdapter
from relay_service.providers.router import ProviderRouter
from relay_service.queue import InMemoryWorkQueue
from relay_service.repository import InMemoryJobRepository
from relay_service.request_ids import (
    normalize_request_id as normalize_relay_request_id,
)


TENANT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
RELAY_AUTH = {
    "X-Client-ID": "platform-service",
    "X-API-Key": "contract-secret",
}


def mock_capability_revision() -> str:
    catalog = asyncio.run(
        ProviderRouter([MockProviderAdapter()]).model_catalog()
    )
    model = next(item for item in catalog.data if item.id == "mock.video.v1")
    return model.capability_revision


MOCK_CAPABILITY_REVISION = mock_capability_revision()


class RecordingProvider(MockProviderAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.submitted_jobs = []

    async def submit(self, job):
        self.submitted_jobs.append(job.model_copy(deep=True))
        return await super().submit(job)


class HttpsArtifactStore:
    kind = "contract-https"
    persistent = True

    async def signed_download_url(
        self, object_key: str, *, expires_seconds: int = 300
    ) -> str:
        return (
            f"https://artifacts.example.test/{object_key}"
            f"?expires={expires_seconds}"
        )

    async def healthcheck(self) -> bool:
        return True


def mapped_request(
    *,
    mode: str = "text_to_video",
    assets: list[dict] | None = None,
    resolution: str = "720p",
    face_enabled: bool = False,
) -> RelayGenerationRequest:
    resolved_assets = assets or []
    asset_references = [
        {
            "asset_id": f"33333333-3333-3333-3333-{index:012d}",
            "media_type": asset["media_type"],
        }
        for index, asset in enumerate(resolved_assets, start=1)
    ]
    task = SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        company_id=str(TENANT_ID),
        user_id="22222222-2222-2222-2222-222222222222",
        request_payload={
            "mode": mode,
            "prompt": "contract payload",
            "assets": asset_references,
            "duration_seconds": 5,
            "aspect_ratio": "16:9",
            "resolution": resolution,
            "output_count": 2,
            "face_enabled": face_enabled,
            "metadata": {"campaign": "launch"},
        },
        capability_snapshot={
            "relay_capability_revision": MOCK_CAPABILITY_REVISION
        },
    )
    model = SimpleNamespace(slug="mock.video.v1")
    persisted_payload = RelayPayloadMapper.from_task(
        task,
        model,
        request_id="platform-create-contract-001",
        resolved_assets=resolved_assets or None,
    )

    materialized = persisted_payload.model_dump(mode="json")
    references = materialized["metadata"].pop("_platform_input_assets")
    assert references == asset_references
    materialized["inputs"]["assets"] = resolved_assets
    return RelayGenerationRequest.model_validate(materialized)


@pytest.mark.parametrize(
    ("mode", "assets"),
    [
        ("text_to_image", []),
        ("text_to_video", []),
        (
            "image_to_video",
            [
                {
                    "url": "https://inputs.example.test/reference.png",
                    "media_type": "image",
                }
            ],
        ),
        (
            "video_to_video",
            [
                {
                    "url": "https://inputs.example.test/source.mp4",
                    "media_type": "video",
                }
            ],
        ),
    ],
)
def test_platform_mapper_output_is_accepted_by_relay_models(
    mode: str, assets: list[dict]
) -> None:
    platform_payload = mapped_request(mode=mode, assets=assets)

    relay_payload = GenerationRequest.model_validate(
        platform_payload.model_dump(mode="json")
    )

    assert relay_payload.mode.value == mode
    assert relay_payload.output.count == 2
    assert relay_payload.metadata["platform_request_id"] == (
        "platform-create-contract-001"
    )


def test_platform_mapper_preserves_resolution_and_face_output_options() -> None:
    platform_payload = mapped_request(
        resolution="1080p",
        face_enabled=True,
    )
    persisted = platform_payload.model_dump(mode="json")

    assert persisted["output"]["resolution"] == "1080p"
    assert persisted["output"]["face_enabled"] is True
    relay_payload = GenerationRequest.model_validate(persisted)
    assert relay_payload.output.resolution == "1080p"
    assert relay_payload.output.face_enabled is True


def test_request_id_normalizers_replace_unsafe_values_instead_of_truncating() -> None:
    for normalize in (
        normalize_platform_request_id,
        normalize_relay_request_id,
    ):
        assert normalize("safe.Trace_01:part") == "safe.Trace_01:part"
        assert UUID(normalize("x" * 81))
        assert UUID(normalize("unsafe\r\nX-Forged: yes"))
        assert UUID(normalize("contains spaces"))


def test_platform_client_and_live_relay_share_contract_and_trace_headers() -> None:
    repository = InMemoryJobRepository()
    queue = InMemoryWorkQueue()
    provider = RecordingProvider()
    relay_app = create_relay_app(
        repository=repository,
        queue=queue,
        transfer_queue=InMemoryWorkQueue(),
        router=ProviderRouter([provider]),
        authenticator=StaticClientAuthenticator(
            {
                "platform-service": ClientCredential(
                    tenant_id=TENANT_ID,
                    api_key="contract-secret",
                )
            }
        ),
        artifact_store=HttpsArtifactStore(),
        process_in_background=False,
    )
    captured: list[dict] = []

    with TestClient(relay_app) as relay_http:

        def bridge(request: httpx.Request) -> httpx.Response:
            relay_response = relay_http.request(
                request.method,
                request.url.raw_path.decode("ascii"),
                headers=dict(request.headers),
                content=request.content,
            )
            try:
                body = relay_response.json()
            except json.JSONDecodeError:
                body = None
            captured.append(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "request_id": request.headers.get("x-request-id"),
                    "response_request_id": relay_response.headers.get(
                        "x-request-id"
                    ),
                    "body": body,
                }
            )
            return httpx.Response(
                relay_response.status_code,
                headers=dict(relay_response.headers),
                content=relay_response.content,
            )

        platform_client = HttpxRelayClient(
            base_url="https://relay.internal.test",
            client_id=RELAY_AUTH["X-Client-ID"],
            api_key=RELAY_AUTH["X-API-Key"],
            transport=httpx.MockTransport(bridge),
        )
        payload = mapped_request()
        accepted = platform_client.submit(
            payload,
            idempotency_key="platform-task-contract-001",
            request_id=payload.metadata["platform_request_id"],
        )

        assert captured[-1]["request_id"] == "platform-create-contract-001"
        assert captured[-1]["response_request_id"] == captured[-1]["request_id"]
        replayed = platform_client.submit(
            payload,
            idempotency_key="platform-task-contract-001",
            request_id="platform-replay-contract-002",
        )
        assert replayed.job_id == accepted.job_id
        assert captured[-1]["response_request_id"] == (
            "platform-replay-contract-002"
        )

        async def submit_to_provider_and_mark_success():
            processed = await relay_app.state.generation_service.process_next()
            assert processed is not None
            assert provider.submitted_jobs[0].metadata["platform_request_id"] == (
                "platform-create-contract-001"
            )
            # The initial trace is durable worker/provider context. A later
            # idempotent replay gets its own HTTP trace without rewriting it.
            assert provider.submitted_jobs[0].metadata["relay_request_id"] == (
                "platform-create-contract-001"
            )
            assert "_platform_input_assets" not in (
                provider.submitted_jobs[0].metadata
            )
            processed.status = JobStatus.SUCCEEDED
            processed.progress = 100
            asset_ids = [uuid4(), uuid4()]
            processed.outputs = [
                GeneratedAsset(
                    asset_id=asset_id,
                    object_key=f"tenant/{TENANT_ID}/jobs/{processed.id}/{asset_id}.mp4",
                    media_type="video",
                    content_type="video/mp4",
                    size_bytes=123 + index,
                    sha256=("a" if index == 0 else "b") * 64,
                )
                for index, asset_id in enumerate(asset_ids)
            ]
            await repository.save(processed)
            return asset_ids[0]

        asset_id = asyncio.run(submit_to_provider_and_mark_success())

        snapshot = platform_client.get(
            accepted.job_id, request_id="platform-status-contract-001"
        )
        assert isinstance(snapshot, RelayJobSnapshot)
        assert snapshot.id == accepted.job_id
        assert snapshot.status == "succeeded"
        assert snapshot.outputs[0].asset_id == str(asset_id)
        assert captured[-1]["response_request_id"] == (
            "platform-status-contract-001"
        )

        download = platform_client.get_artifact_download(
            accepted.job_id,
            str(asset_id),
            request_id="platform-download-contract-001",
        )
        assert str(download.url).startswith("https://artifacts.example.test/")
        assert captured[-1]["response_request_id"] == (
            "platform-download-contract-001"
        )

        with pytest.raises(RelayPermanentError):
            platform_client.get(
                "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                request_id="platform-error-contract-001",
            )
        assert captured[-1]["body"]["error"]["request_id"] == (
            "platform-error-contract-001"
        )
        assert captured[-1]["response_request_id"] == (
            "platform-error-contract-001"
        )
        platform_client.close()


def test_relay_replaces_oversized_request_id_on_success_and_error() -> None:
    relay_app = create_relay_app(
        router=ProviderRouter([MockProviderAdapter()]),
        authenticator=StaticClientAuthenticator(
            {
                "platform-service": ClientCredential(
                    tenant_id=TENANT_ID,
                    api_key="contract-secret",
                )
            }
        ),
        process_in_background=False,
    )
    client = TestClient(relay_app)
    unsafe = "r" * 512

    success = client.get("/health/live", headers={"X-Request-ID": unsafe})
    error = client.get(
        "/v1/generations/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        headers={**RELAY_AUTH, "X-Request-ID": unsafe},
    )

    assert success.status_code == 200
    assert UUID(success.headers["X-Request-ID"])
    assert error.status_code == 404
    assert UUID(error.headers["X-Request-ID"])
    assert error.json()["error"]["request_id"] == error.headers["X-Request-ID"]
