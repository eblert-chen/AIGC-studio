from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from relay_service.auth import (
    GENERATION_INVOKE_SCOPE,
    SUBMISSION_RECONCILIATION_SCOPE,
    ClientCredential,
    StaticClientAuthenticator,
)
from relay_service.main import create_app
from relay_service.models import GeneratedAsset, JobStatus
from relay_service.providers.mock import MockProviderAdapter
from relay_service.providers.router import ProviderRouter
from relay_service.queue import InMemoryWorkQueue
from relay_service.repository import InMemoryJobRepository


PLATFORM_TENANT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
TIKTOK_TENANT_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
PLATFORM_HEADERS = {
    "X-Client-ID": "customer-platform",
    "X-API-Key": "customer-platform-contract-key",
}
TIKTOK_HEADERS = {
    "X-Client-ID": "internal-tiktok",
    "X-API-Key": "internal-tiktok-contract-key",
}
TIKTOK_OPERATIONS_HEADERS = {
    "X-Client-ID": "internal-tiktok-operations",
    "X-API-Key": "internal-tiktok-operations-contract-key",
}


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


def test_internal_tiktok_polling_contract_is_tenant_isolated_and_scope_safe() -> None:
    repository = InMemoryJobRepository()
    provider = MockProviderAdapter()
    router = ProviderRouter([provider])
    app = create_app(
        repository=repository,
        queue=InMemoryWorkQueue(),
        transfer_queue=InMemoryWorkQueue(),
        router=router,
        authenticator=StaticClientAuthenticator(
            {
                "customer-platform": ClientCredential(
                    tenant_id=PLATFORM_TENANT_ID,
                    api_key=PLATFORM_HEADERS["X-API-Key"],
                    scopes=frozenset({GENERATION_INVOKE_SCOPE}),
                ),
                "internal-tiktok": ClientCredential(
                    tenant_id=TIKTOK_TENANT_ID,
                    api_key=TIKTOK_HEADERS["X-API-Key"],
                    scopes=frozenset({GENERATION_INVOKE_SCOPE}),
                ),
                "internal-tiktok-operations": ClientCredential(
                    tenant_id=TIKTOK_TENANT_ID,
                    api_key=TIKTOK_OPERATIONS_HEADERS["X-API-Key"],
                    scopes=frozenset({SUBMISSION_RECONCILIATION_SCOPE}),
                ),
            }
        ),
        artifact_store=HttpsArtifactStore(),
        process_in_background=False,
    )

    with TestClient(app) as client:
        catalog_response = client.get("/v1/models", headers=TIKTOK_HEADERS)
        assert catalog_response.status_code == 200, catalog_response.text
        catalog = catalog_response.json()
        assert catalog["api_version"] == "v1"
        assert catalog["schema_version"] == 1
        model = next(
            item for item in catalog["data"] if item["id"] == "mock.video.v1"
        )
        revision = model["capability_revision"]

        create_response = client.post(
            "/v1/generations",
            headers={
                **TIKTOK_HEADERS,
                "Idempotency-Key": "internal-tiktok-operation-0001",
                "X-Request-ID": "internal-tiktok-create-0001",
            },
            json={
                "client_reference_id": "tiktok-operation-0001",
                "model": "mock.video.v1",
                "expected_capability_revision": revision,
                "mode": "text_to_video",
                "inputs": {"prompt": "TikTok contract isolation smoke"},
                "output": {
                    "duration_seconds": 5,
                    "aspect_ratio": "9:16",
                    "resolution": "720p",
                    "count": 1,
                    "face_enabled": False,
                },
                "metadata": {"tiktok_operation_id": "tiktok-operation-0001"},
            },
        )
        assert create_response.status_code == 202, create_response.text
        accepted = create_response.json()
        assert accepted["api_version"] == "v1"
        assert accepted["schema_version"] == 1
        assert accepted["id"] == accepted["job_id"]
        assert accepted["expected_capability_revision"] == revision
        assert accepted["capability_revision"] == revision
        assert accepted["reservation_action"] == "hold"
        job_id = accepted["id"]

        replay = client.post(
            "/v1/generations",
            headers={
                **TIKTOK_HEADERS,
                "Idempotency-Key": "internal-tiktok-operation-0001",
            },
            json={
                "client_reference_id": "tiktok-operation-0001",
                "model": "mock.video.v1",
                "expected_capability_revision": revision,
                "mode": "text_to_video",
                "inputs": {"prompt": "TikTok contract isolation smoke"},
                "output": {
                    "duration_seconds": 5,
                    "aspect_ratio": "9:16",
                    "resolution": "720p",
                    "count": 1,
                    "face_enabled": False,
                },
                "metadata": {"tiktok_operation_id": "tiktok-operation-0001"},
            },
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["id"] == job_id

        cross_tenant_read = client.get(
            f"/v1/generations/{job_id}", headers=PLATFORM_HEADERS
        )
        assert cross_tenant_read.status_code == 404

        ordinary_ops_read = client.get(
            "/v1/operations/submission-reconciliations",
            headers=TIKTOK_HEADERS,
        )
        assert ordinary_ops_read.status_code == 403
        assert ordinary_ops_read.json()["error"]["code"] == (
            "INSUFFICIENT_CLIENT_SCOPE"
        )

        ops_generation_submit = client.post(
            "/v1/generations",
            headers={
                **TIKTOK_OPERATIONS_HEADERS,
                "Idempotency-Key": "ops-must-not-generate-0001",
            },
            json={
                "client_reference_id": "ops-must-not-generate-0001",
                "model": "mock.video.v1",
                "expected_capability_revision": revision,
                "mode": "text_to_video",
                "inputs": {"prompt": "must be rejected before creation"},
            },
        )
        assert ops_generation_submit.status_code == 403

        async def finish_job() -> UUID:
            processed = await app.state.generation_service.process_next()
            assert processed is not None
            assert processed.status == JobStatus.PROCESSING
            stored = await repository.get(UUID(job_id))
            assert stored is not None
            asset_id = uuid4()
            stored.status = JobStatus.SUCCEEDED
            stored.progress = 100
            stored.outputs = [
                GeneratedAsset(
                    asset_id=asset_id,
                    object_key=(
                        f"tenant/{TIKTOK_TENANT_ID}/jobs/{job_id}/{asset_id}.mp4"
                    ),
                    media_type="video",
                    content_type="video/mp4",
                    size_bytes=321,
                    sha256="b" * 64,
                )
            ]
            await repository.save(stored)
            return asset_id

        asset_id = asyncio.run(finish_job())

        status_response = client.get(
            f"/v1/generations/{job_id}", headers=TIKTOK_HEADERS
        )
        assert status_response.status_code == 200, status_response.text
        status = status_response.json()
        assert status["status"] == "succeeded"
        assert status["reservation_action"] == "settle"
        assert status["capability_revision"] == revision
        assert status["outputs"][0]["asset_id"] == str(asset_id)
        assert status["metadata"] == {
            "tiktok_operation_id": "tiktok-operation-0001",
            "relay_request_id": "internal-tiktok-create-0001",
        }
        assert "source_client" not in status["metadata"]
        assert "client_id" not in status

        download_response = client.get(
            f"/v1/generations/{job_id}/artifacts/{asset_id}/download",
            headers=TIKTOK_HEADERS,
        )
        assert download_response.status_code == 200, download_response.text
        download = download_response.json()
        assert download["api_version"] == "v1"
        assert download["schema_version"] == 1
        assert download["url"].startswith("https://artifacts.example.test/")

        cross_tenant_download = client.get(
            f"/v1/generations/{job_id}/artifacts/{asset_id}/download",
            headers=PLATFORM_HEADERS,
        )
        assert cross_tenant_download.status_code == 404
