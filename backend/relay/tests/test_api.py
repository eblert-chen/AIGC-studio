from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from relay_service.artifacts import (
    ArtifactConfigurationError,
    InMemoryArtifactStore,
)
from relay_service.auth import (
    SUBMISSION_RECONCILIATION_SCOPE,
    ClientCredential,
    StaticClientAuthenticator,
)
from relay_service.config import RelaySettings
from relay_service.downloader import DownloadedArtifact
from relay_service.main import create_app
from relay_service.providers.mock import MockProviderAdapter
from relay_service.providers.base import ProviderError, ProviderSubmission
from relay_service.providers.router import ProviderRouter
from relay_service.queue import InMemoryWorkQueue
from relay_service.repository import InMemoryJobRepository


TENANT_A = uuid4()
TENANT_B = uuid4()
AUTH_A = {
    "X-Client-ID": "client-a",
    "X-API-Key": "test-key-a",
}
AUTH_B = {
    "X-Client-ID": "client-b",
    "X-API-Key": "test-key-b",
}

MOCK_CAPABILITY_REVISION = asyncio.run(
    ProviderRouter([MockProviderAdapter()]).model_catalog()
).data[0].capability_revision


def make_authenticator() -> StaticClientAuthenticator:
    return StaticClientAuthenticator(
        {
            "client-a": ClientCredential(tenant_id=TENANT_A, api_key="test-key-a"),
            "client-b": ClientCredential(tenant_id=TENANT_B, api_key="test-key-b"),
        }
    )


class StaticDownloader:
    async def download(self, url: str) -> DownloadedArtifact:
        is_image = str(url).endswith(".png")
        data = b"safe-image-bytes" if is_image else b"safe-video-bytes"
        return DownloadedArtifact(
            content=BytesIO(data),
            content_type="image/png" if is_image else "video/mp4",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )


def make_app(*, process_in_background: bool = False):
    return create_app(
        authenticator=make_authenticator(),
        artifact_downloader=StaticDownloader(),
        process_in_background=process_in_background,
    )


def submission_headers(key: str, auth: dict[str, str] = AUTH_A) -> dict[str, str]:
    return {**auth, "Idempotency-Key": key}


def payload(prompt: str = "A paper boat crossing a quiet lake") -> dict:
    return {
        "client_reference_id": "scene-001",
        "model": "mock.video.v1",
        "expected_capability_revision": MOCK_CAPABILITY_REVISION,
        "mode": "text_to_video",
        "inputs": {"prompt": prompt, "assets": []},
        "output": {
            "duration_seconds": 5,
            "aspect_ratio": "16:9",
            "resolution": "720p",
            "face_enabled": False,
            "count": 1,
        },
        "metadata": {"source": "contract-test"},
    }


def test_submit_is_idempotent_and_enqueued_once() -> None:
    app = make_app()
    client = TestClient(app)
    body = payload()
    headers = submission_headers("stable-key-001")

    first = client.post("/v1/generations", json=body, headers=headers)
    second = client.post("/v1/generations", json=body, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["object"] == "generation"
    assert first.json()["api_version"] == "v1"
    assert first.json()["schema_version"] == 1
    assert first.json()["expected_capability_revision"] == (
        MOCK_CAPABILITY_REVISION
    )
    assert first.json()["capability_revision"] == MOCK_CAPABILITY_REVISION
    assert first.json()["reservation_action"] == "hold"
    assert first.json()["id"] == first.json()["job_id"]
    assert first.json()["job_id"] == second.json()["job_id"]
    assert first.json()["idempotent_replay"] is False
    assert second.json()["idempotent_replay"] is True
    service = app.state.generation_service
    assert client.get(
        f"/v1/generations/{first.json()['job_id']}", headers=AUTH_A
    ).json()[
        "status"
    ] == "queued"
    assert __import__("asyncio").run(service.queue.depth()) == 1


def test_submit_and_read_preserve_face_enabled_output_option() -> None:
    app = make_app()
    client = TestClient(app)
    body = payload()
    body["output"]["resolution"] = "1080p"
    body["output"]["face_enabled"] = True

    submitted = client.post(
        "/v1/generations",
        json=body,
        headers=submission_headers("face-output-contract-001"),
    )
    assert submitted.status_code == 202, submitted.text
    stored = client.get(
        f"/v1/generations/{submitted.json()['job_id']}",
        headers=AUTH_A,
    )
    assert stored.status_code == 200, stored.text
    assert stored.json()["output"]["resolution"] == "1080p"
    assert stored.json()["output"]["face_enabled"] is True


def test_idempotency_key_rejects_different_payload() -> None:
    app = make_app()
    client = TestClient(app)
    headers = submission_headers("stable-key-002")
    first_payload = payload("first")
    assert client.post(
        "/v1/generations", json=first_payload, headers=headers
    ).status_code == 202
    different_payload = payload("different")

    response = client.post(
        "/v1/generations", json=different_payload, headers=headers
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert response.headers["X-Request-ID"]


def test_idempotency_keys_are_isolated_by_tenant() -> None:
    app = make_app()
    client = TestClient(app)

    first = client.post(
        "/v1/generations",
        json=payload(),
        headers=submission_headers("tenant-local-key", AUTH_A),
    )
    second = client.post(
        "/v1/generations",
        json=payload(),
        headers=submission_headers("tenant-local-key", AUTH_B),
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] != second.json()["job_id"]


def test_authenticated_source_client_is_persisted_but_not_exposed() -> None:
    app = make_app()
    client = TestClient(app)
    submitted = client.post(
        "/v1/generations",
        json=payload(),
        headers=submission_headers("trusted-source-client", AUTH_A),
    )

    assert submitted.status_code == 202
    assert "source_client_id" not in submitted.json()
    job_id = UUID(submitted.json()["job_id"])
    stored = __import__("asyncio").run(
        app.state.generation_service.repository.get(job_id)
    )
    assert stored is not None
    assert stored.source_client_id == "client-a"

    visible = client.get(f"/v1/generations/{job_id}", headers=AUTH_A)
    assert visible.status_code == 200
    assert "source_client_id" not in visible.json()

    spoofed = payload()
    spoofed["source_client_id"] = "client-b"
    rejected = client.post(
        "/v1/generations",
        json=spoofed,
        headers=submission_headers("spoofed-source-client", AUTH_A),
    )
    assert rejected.status_code == 422


def test_worker_and_signed_webhook_complete_job_once() -> None:
    app = make_app()
    client = TestClient(app)
    submitted = client.post(
        "/v1/generations",
        json=payload(),
        headers=submission_headers("stable-key-003"),
    ).json()
    service = app.state.generation_service

    processed = __import__("asyncio").run(service.process_next())
    assert processed is not None
    assert processed.status == "processing"

    event = {
        "event_id": "evt-001",
        "provider_task_id": processed.provider_task_id,
        "status": "succeeded",
        "progress": 100,
        "outputs": [
            {
                "url": "https://assets.example.test/output.mp4",
                "media_type": "video",
                "content_type": "video/mp4",
            }
        ],
    }
    headers = {"X-Mock-Webhook-Secret": "development-only-secret"}
    first = client.post(
        "/v1/providers/mock-video/webhooks", json=event, headers=headers
    )
    second = client.post(
        "/v1/providers/mock-video/webhooks", json=event, headers=headers
    )

    assert first.status_code == 200
    assert first.json()["status"] == "transferring"
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True
    assert second.json()["status"] == "transferring"
    transferred = __import__("asyncio").run(
        app.state.transfer_service.process_next()
    )
    assert transferred is not None
    assert transferred.status == "succeeded"
    job = client.get(
        f"/v1/generations/{submitted['job_id']}", headers=AUTH_A
    ).json()
    assert job["status"] == "succeeded"
    assert "url" not in job["outputs"][0]
    assert job["outputs"][0]["object_key"].startswith(
        f"outputs/{TENANT_A}/{submitted['job_id']}/"
    )
    signed = client.get(
        (
            f"/v1/generations/{submitted['job_id']}/artifacts/"
            f"{job['outputs'][0]['asset_id']}/download"
        ),
        headers=AUTH_A,
    )
    assert signed.status_code == 200
    assert signed.json()["expires_seconds"] == 300


@pytest.mark.parametrize(
    ("mode", "assets", "output_url", "media_type", "content_type"),
    [
        ("text_to_image", [], "https://assets.example.test/output.png", "image", "image/png"),
        (
            "image_to_video",
            [
                {
                    "url": "https://inputs.example.test/reference.png",
                    "media_type": "image",
                }
            ],
            "https://assets.example.test/output.mp4",
            "video",
            "video/mp4",
        ),
    ],
)
def test_each_required_mode_has_an_http_to_artifact_contract(
    mode: str,
    assets: list[dict[str, str]],
    output_url: str,
    media_type: str,
    content_type: str,
) -> None:
    app = make_app()
    client = TestClient(app)
    body = payload(f"contract for {mode}")
    body["mode"] = mode
    body["inputs"]["assets"] = assets
    submitted = client.post(
        "/v1/generations",
        json=body,
        headers=submission_headers(f"mode-contract-{mode}"),
    )
    assert submitted.status_code == 202, submitted.text
    processed = __import__("asyncio").run(
        app.state.generation_service.process_next()
    )
    event = {
        "event_id": f"event-{mode}",
        "provider_task_id": processed.provider_task_id,
        "status": "succeeded",
        "progress": 100,
        "outputs": [
            {
                "url": output_url,
                "media_type": media_type,
                "content_type": content_type,
            }
        ],
    }
    webhook = client.post(
        "/v1/providers/mock-video/webhooks",
        json=event,
        headers={"X-Mock-Webhook-Secret": "development-only-secret"},
    )
    assert webhook.status_code == 200, webhook.text
    __import__("asyncio").run(app.state.transfer_service.process_next())

    visible = client.get(
        f"/v1/generations/{submitted.json()['job_id']}", headers=AUTH_A
    )
    assert visible.status_code == 200
    assert visible.json()["object"] == "generation"
    assert visible.json()["mode"] == mode
    assert visible.json()["status"] == "succeeded"
    assert visible.json()["outputs"][0]["media_type"] == media_type
    asset_id = visible.json()["outputs"][0]["asset_id"]
    signed = client.get(
        f"/v1/generations/{submitted.json()['job_id']}/artifacts/{asset_id}/download",
        headers=AUTH_A,
    )
    assert signed.status_code == 200


def test_webhook_requires_adapter_signature_verification() -> None:
    app = make_app()
    client = TestClient(app)
    response = client.post(
        "/v1/providers/mock-video/webhooks",
        json={
            "event_id": "evt-bad",
            "provider_task_id": "missing",
            "status": "processing",
        },
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "WEBHOOK_SIGNATURE_INVALID"


def test_versioned_capabilities_and_health_are_exposed() -> None:
    client = TestClient(make_app())
    capabilities = client.get("/v1/models/capabilities", headers=AUTH_A)
    catalog = client.get("/v1/models", headers=AUTH_A)
    ready = client.get("/health/ready")

    assert capabilities.status_code == 200
    assert capabilities.headers["deprecation"] == "true"
    assert capabilities.headers["link"] == (
        '</v1/models>; rel="successor-version"'
    )
    capability = next(
        item
        for item in capabilities.json()
        if item["model"] == "mock.video.v1"
        and item["modes"] == ["text_to_image"]
    )
    assert capability["model"] == "mock.video.v1"
    assert capability["modes"] == ["text_to_image"]
    assert capability["limits"]["max_images"] == 9
    assert capability["limits"]["max_videos"] == 3
    assert capability["limits"]["max_audio"] == 3
    assert "available_providers" not in capability
    assert catalog.status_code == 200
    assert catalog.json()["object"] == "list"
    assert catalog.json()["catalog_revision"].startswith("sha256:")
    model = catalog.json()["data"][0]
    assert model["id"] == "mock.video.v1"
    assert model["object"] == "model"
    assert model["capability_revision"].startswith("sha256:")
    assert set(model["capabilities"]["modes"]) == {
        "text_to_image",
        "text_to_video",
        "image_to_video",
        "video_to_video",
    }
    assert "available_providers" not in catalog.text
    etag = catalog.headers["ETag"]
    unchanged = client.get(
        "/v1/models", headers={**AUTH_A, "If-None-Match": etag}
    )
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    detail = client.get("/v1/models/mock.video.v1", headers=AUTH_A)
    assert detail.status_code == 200
    assert detail.json() == model
    assert detail.headers["ETag"] == f'"{model["capability_revision"]}"'
    unchanged_detail = client.get(
        "/v1/models/mock.video.v1",
        headers={**AUTH_A, "If-None-Match": detail.headers["ETag"]},
    )
    assert unchanged_detail.status_code == 304
    assert unchanged_detail.content == b""
    assert ready.status_code == 200
    assert ready.json()["state"] == "healthy"
    dependencies = {
        item["name"]: item for item in ready.json()["dependencies"]
    }
    assert dependencies["repository"]["details"]["persistent"] is False
    assert dependencies["repository"]["details"]["outbox"] is False
    assert dependencies["queue"]["details"]["persistent"] is False


def test_capability_catalog_requires_auth_and_does_not_flap_with_health() -> None:
    unauthenticated = TestClient(make_app())
    assert unauthenticated.get("/v1/models").status_code == 401
    assert unauthenticated.get("/v1/models/capabilities").status_code == 401

    app = create_app(
        router=ProviderRouter([MockProviderAdapter(healthy=False)]),
        authenticator=make_authenticator(),
        artifact_downloader=StaticDownloader(),
        process_in_background=False,
    )
    response = TestClient(app).get("/v1/models", headers=AUTH_A)
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == [
        "mock.video.v1"
    ]


def test_capability_revision_is_checked_before_provider_submission() -> None:
    class CountingProvider(MockProviderAdapter):
        calls = 0

        async def submit(self, job) -> ProviderSubmission:
            self.calls += 1
            return await super().submit(job)

    provider = CountingProvider()
    app = create_app(
        router=ProviderRouter([provider]),
        authenticator=make_authenticator(),
        artifact_downloader=StaticDownloader(),
        process_in_background=False,
    )
    client = TestClient(app)
    body = payload()
    body["expected_capability_revision"] = "sha256:" + ("0" * 64)
    submitted = client.post(
        "/v1/generations",
        json=body,
        headers=submission_headers("stale-capability-revision"),
    )
    assert submitted.status_code == 202
    processed = __import__("asyncio").run(
        app.state.generation_service.process_next()
    )
    assert processed.status == "failed"
    assert processed.error.code == "CAPABILITY_REVISION_MISMATCH"
    assert provider.calls == 0

    current = client.get("/v1/models", headers=AUTH_A).json()["data"][0]
    fresh = payload("fresh revision")
    fresh["expected_capability_revision"] = current["capability_revision"]
    accepted = client.post(
        "/v1/generations",
        json=fresh,
        headers=submission_headers("fresh-capability-revision"),
    )
    assert accepted.status_code == 202
    processed = __import__("asyncio").run(
        app.state.generation_service.process_next()
    )
    assert processed.status == "processing"
    assert provider.calls == 1


def test_provider_identity_is_removed_from_public_job_errors() -> None:
    class VendorFailure(MockProviderAdapter):
        async def submit(self, job) -> ProviderSubmission:
            raise ProviderError(
                "MYSTERY_VENDOR_ACCOUNT_IN_ARREARS",
                "Mystery vendor account secret diagnostic",
                retryable=False,
                account_unavailable=False,
            )

    app = create_app(
        router=ProviderRouter([VendorFailure()]),
        authenticator=make_authenticator(),
        artifact_downloader=StaticDownloader(),
        process_in_background=False,
    )
    client = TestClient(app)
    submitted = client.post(
        "/v1/generations",
        json=payload(),
        headers=submission_headers("vendor-error-redaction"),
    )
    assert submitted.status_code == 202
    __import__("asyncio").run(app.state.generation_service.process_next())

    visible = client.get(
        f"/v1/generations/{submitted.json()['job_id']}", headers=AUTH_A
    )
    assert visible.status_code == 200
    assert visible.json()["error"]["code"] == "GENERATION_CHANNEL_UNAVAILABLE"
    assert "mystery" not in visible.text.lower()


def test_framework_and_internal_errors_use_the_standard_envelope() -> None:
    client = TestClient(make_app())
    missing = client.get("/v1/does-not-exist")
    wrong_method = client.post("/health/live")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "ROUTE_NOT_FOUND"
    assert wrong_method.status_code == 405
    assert wrong_method.json()["error"]["code"] == "METHOD_NOT_ALLOWED"

    class BrokenCatalogProvider(MockProviderAdapter):
        async def capabilities(self):
            raise RuntimeError("sensitive provider diagnostic")

    broken = create_app(
        router=ProviderRouter([BrokenCatalogProvider()]),
        authenticator=make_authenticator(),
        artifact_downloader=StaticDownloader(),
        process_in_background=False,
    )
    response = TestClient(broken, raise_server_exceptions=False).get(
        "/v1/models", headers=AUTH_A
    )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "sensitive provider diagnostic" not in response.text


def test_submit_and_get_require_client_authentication() -> None:
    client = TestClient(make_app())
    missing = client.post(
        "/v1/generations",
        json=payload(),
        headers={"Idempotency-Key": "auth-required-key"},
    )
    invalid_secret = "do-not-echo-this-secret"
    invalid = client.post(
        "/v1/generations",
        json=payload(),
        headers={
            "Idempotency-Key": "invalid-auth-key",
            "X-Client-ID": "client-a",
            "X-API-Key": invalid_secret,
        },
    )
    get_missing = client.get(f"/v1/generations/{uuid4()}")

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "CLIENT_AUTHENTICATION_REQUIRED"
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "INVALID_CLIENT_CREDENTIALS"
    assert invalid_secret not in invalid.text
    assert get_missing.status_code == 401


def test_cross_tenant_job_lookup_is_hidden_as_not_found() -> None:
    client = TestClient(make_app())
    submitted = client.post(
        "/v1/generations",
        json=payload(),
        headers=submission_headers("tenant-bound-key", AUTH_A),
    )
    job_id = submitted.json()["job_id"]

    owner = client.get(f"/v1/generations/{job_id}", headers=AUTH_A)
    outsider = client.get(f"/v1/generations/{job_id}", headers=AUTH_B)

    assert owner.status_code == 200
    assert {
        "tenant_id",
        "provider",
        "provider_task_id",
        "transfer_sources",
    }.isdisjoint(owner.json())
    assert owner.json()["inputs"] == payload()["inputs"]
    assert owner.json()["output"] == payload()["output"]
    assert outsider.status_code == 404
    assert outsider.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_validation_errors_use_error_envelope_without_echoing_input() -> None:
    client = TestClient(make_app())
    invalid = payload()
    invalid["inputs"]["prompt"] = "sensitive prompt that must not echo"
    invalid["unexpected"] = "also-sensitive"
    response = client.post(
        "/v1/generations",
        json=invalid,
        headers=submission_headers("validation-key"),
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    assert body["error"]["request_id"]
    assert "sensitive prompt" not in response.text
    assert "also-sensitive" not in response.text


@pytest.mark.parametrize(
    ("field", "coerced_value"),
    [
        ("duration_seconds", "5"),
        ("count", "1"),
        ("face_enabled", "false"),
    ],
)
def test_generation_output_rejects_string_coercion(
    field: str, coerced_value: str
) -> None:
    client = TestClient(make_app())
    invalid = payload()
    invalid["output"][field] = coerced_value

    response = client.post(
        "/v1/generations",
        json=invalid,
        headers=submission_headers(f"strict-output-{field}"),
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    assert body["error"]["retryable"] is False


def test_production_environment_cannot_downgrade_artifact_store() -> None:
    settings = RelaySettings(
        environment="production",
        runtime_mode="production",
        database_url="postgresql+asyncpg://db/relay",
        redis_url="redis://queue",
        artifact_store="memory",
    )
    with pytest.raises(RuntimeError, match="huawei_obs"):
        settings.validate()


def test_production_cannot_start_when_huawei_obs_credentials_are_missing(
    monkeypatch,
) -> None:
    for name in (
        "HUAWEI_OBS_ACCESS_KEY_ID",
        "HUAWEI_OBS_SECRET_ACCESS_KEY",
        "HUAWEI_OBS_ENDPOINT",
        "HUAWEI_OBS_BUCKET",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = RelaySettings(
        environment="production",
        runtime_mode="production",
        database_url="postgresql+asyncpg://db/relay",
        redis_url="redis://queue",
        artifact_store="huawei_obs",
        provider_alert_webhook_url="https://alerts.example.com/relay",
        provider_alert_signing_secret="x" * 48,
    )
    with pytest.raises(
        ArtifactConfigurationError,
        match="Huawei OBS configuration is incomplete",
    ):
        create_app(
            repository=InMemoryJobRepository(),
            queue=InMemoryWorkQueue(),
            transfer_queue=InMemoryWorkQueue(),
            router=ProviderRouter([MockProviderAdapter()]),
            authenticator=make_authenticator(),
            settings=settings,
            process_in_background=False,
        )


def test_persistent_development_with_memory_store_is_degraded() -> None:
    class PersistentMemoryRepository(InMemoryJobRepository):
        persistent = True
        has_outbox = True
        kind = "fake-persistent"

    class PersistentMemoryQueue(InMemoryWorkQueue):
        persistent = True
        kind = "fake-persistent"

    settings = RelaySettings(
        environment="development",
        runtime_mode="production",
        database_url="postgresql+asyncpg://db/relay",
        redis_url="redis://queue",
        artifact_store="memory",
        enable_mock_provider=True,
    )
    client = TestClient(
        create_app(
            repository=PersistentMemoryRepository(),
            queue=PersistentMemoryQueue(),
            transfer_queue=PersistentMemoryQueue(),
            artifact_store=InMemoryArtifactStore(),
            artifact_downloader=StaticDownloader(),
            router=ProviderRouter([MockProviderAdapter()]),
            authenticator=make_authenticator(),
            settings=settings,
            process_in_background=False,
        )
    )

    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["state"] == "degraded"
    dependencies = {
        item["name"]: item for item in ready.json()["dependencies"]
    }
    assert dependencies["artifact_store"]["state"] == "degraded"
    assert (
        dependencies["runtime"]["details"]["production_controls_enforced"]
        is False
    )


def test_openapi_requires_both_service_auth_headers() -> None:
    schema = make_app().openapi()
    assert schema["info"]["version"] == "1.0.0"
    assert schema["paths"]["/v1/generations"]["post"]["security"] == [
        {"RelayClientId": [], "RelayApiKey": []}
    ]
    assert schema["paths"]["/v1/models"]["get"]["security"] == [
        {"RelayClientId": [], "RelayApiKey": []}
    ]
    for name in (
        "GenerationAccepted",
        "GenerationResponse",
        "ModelListResponse",
        "ModelResource",
        "SignedDownload",
        "ErrorEnvelope",
    ):
        component = schema["components"]["schemas"][name]
        assert {"api_version", "schema_version"}.issubset(
            component["required"]
        )
        assert component["additionalProperties"] is False


def test_ops_only_credential_cannot_submit_generation() -> None:
    ops_authenticator = StaticClientAuthenticator(
        {
            "ops": ClientCredential(
                tenant_id=TENANT_A,
                api_key="ops-key",
                scopes=frozenset({SUBMISSION_RECONCILIATION_SCOPE}),
            )
        }
    )
    client = TestClient(create_app(authenticator=ops_authenticator))
    response = client.post(
        "/v1/generations",
        json=payload(),
        headers={
            "X-Client-ID": "ops",
            "X-API-Key": "ops-key",
            "Idempotency-Key": "ops-cannot-submit",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INSUFFICIENT_CLIENT_SCOPE"
