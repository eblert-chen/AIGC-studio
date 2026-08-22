from __future__ import annotations

import json

import httpx
import pytest

from platform_api.relay_client import (
    HttpxRelayClient,
    RelayGenerationRequest,
    RelayPermanentError,
    RelayTemporaryError,
)

CATALOG_REVISION = "sha256:" + "a" * 64
CAPABILITY_REVISION = "sha256:" + "b" * 64
CATALOG_ETAG = f'"{CATALOG_REVISION}"'


def catalog_payload() -> dict:
    return {
        "api_version": "v1",
        "schema_version": 1,
        "object": "list",
        "data": [
            {
                "api_version": "v1",
                "schema_version": 1,
                "id": "mock.video.v1",
                "object": "model",
                "capability_revision": CAPABILITY_REVISION,
                "capabilities": {
                    "schema_version": 1,
                    "modes": {
                        "text_to_video": {
                            "input_media_types": ["image", "audio"],
                            "supports_face": True,
                            "required_resource_keys": [],
                            "limits": {
                                "max_prompt_length": 10_000,
                                "max_images": 9,
                                "max_videos": 0,
                                "max_audio": 3,
                                "duration_seconds": [5, 10],
                                "aspect_ratios": ["16:9", "9:16"],
                                "resolutions": ["720p", "1080p"],
                                "output_counts": [1, 2],
                            },
                        },
                        "text_to_image": {
                            "input_media_types": [],
                            "supports_face": False,
                            "required_resource_keys": [],
                            "limits": {
                                "max_prompt_length": 4_000,
                                "max_images": 0,
                                "max_videos": 0,
                                "max_audio": 0,
                                "duration_seconds": [1],
                                "aspect_ratios": ["1:1"],
                                "resolutions": ["1024x1024"],
                                "output_counts": [1, 4],
                            },
                        },
                    },
                },
            }
        ],
        "catalog_revision": CATALOG_REVISION,
    }


def make_client(handler) -> HttpxRelayClient:
    return HttpxRelayClient(
        base_url="https://relay.example.test",
        client_id="customer-platform",
        api_key="relay-secret",
        transport=httpx.MockTransport(handler),
    )


def test_generation_request_preserves_expected_capability_revision() -> None:
    request = RelayGenerationRequest(
        client_reference_id="platform-task-1",
        model="mock.video.v1",
        expected_capability_revision=CAPABILITY_REVISION,
        mode="text_to_video",
        inputs={"prompt": "catalog-pinned request", "assets": []},
        output={},
    )

    assert request.model_dump(mode="json")["expected_capability_revision"] == (
        CAPABILITY_REVISION
    )
    with pytest.raises(ValueError):
        RelayGenerationRequest(
            client_reference_id="platform-task-2",
            model="mock.video.v1",
            expected_capability_revision="revision-2",
            mode="text_to_video",
            inputs={"prompt": "invalid revision", "assets": []},
            output={},
        )


def test_submit_requires_and_sends_a_pinned_revision() -> None:
    submitted: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        submitted.append(json.loads(request.content))
        job_id = "11111111-1111-4111-8111-111111111111"
        return httpx.Response(
            202,
            json={
                "api_version": "v1",
                "schema_version": 1,
                "object": "generation",
                "id": job_id,
                "job_id": job_id,
                "status": "queued",
                "expected_capability_revision": CAPABILITY_REVISION,
                "capability_revision": CAPABILITY_REVISION,
                "reservation_action": "hold",
                "idempotent_replay": False,
                "created_at": "2030-01-01T00:00:00Z",
            },
        )

    def request(revision: str) -> RelayGenerationRequest:
        return RelayGenerationRequest(
            client_reference_id="platform-task-1",
            model="mock.video.v1",
            expected_capability_revision=revision,
            mode="text_to_video",
            inputs={"prompt": "submit contract", "assets": []},
            output={},
        )

    client = make_client(handler)
    try:
        with pytest.raises(ValueError):
            RelayGenerationRequest(
                client_reference_id="platform-task-without-revision",
                model="mock.video.v1",
                mode="text_to_video",
                inputs={"prompt": "missing revision", "assets": []},
                output={},
            )
        client.submit(
            request(CAPABILITY_REVISION),
            idempotency_key="submission-with-revision",
        )
    finally:
        client.close()

    assert len(submitted) == 1
    assert submitted[0]["expected_capability_revision"] == CAPABILITY_REVISION


def test_reads_versioned_model_catalog_with_service_identity() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=catalog_payload(),
            headers={"ETag": CATALOG_ETAG},
        )

    client = make_client(handler)
    try:
        result = client.get_model_catalog(request_id="catalog-read-001")
    finally:
        client.close()

    assert result.not_modified is False
    assert result.etag == CATALOG_ETAG
    assert result.catalog is not None
    assert result.catalog.catalog_revision == CATALOG_REVISION
    assert result.catalog.data[0].capability_revision == CAPABILITY_REVISION
    assert set(result.catalog.data[0].capabilities.modes) == {
        "text_to_video",
        "text_to_image",
    }
    assert len(captured) == 1
    request = captured[0]
    assert request.method == "GET"
    assert request.url.path == "/v1/models"
    assert request.headers["x-client-id"] == "customer-platform"
    assert request.headers["x-api-key"] == "relay-secret"
    assert request.headers["x-request-id"] == "catalog-read-001"
    assert request.headers["accept"] == "application/json"
    assert "if-none-match" not in request.headers


def test_conditional_catalog_read_normalizes_etag_and_accepts_304() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(304, headers={"ETag": CATALOG_ETAG})

    client = make_client(handler)
    try:
        result = client.get_model_catalog(if_none_match=CATALOG_REVISION)
    finally:
        client.close()

    assert result.not_modified is True
    assert result.catalog is None
    assert result.etag == CATALOG_ETAG
    assert captured[0].headers["if-none-match"] == CATALOG_ETAG
    assert captured[0].headers["x-client-id"] == "customer-platform"
    assert captured[0].headers["x-api-key"] == "relay-secret"


@pytest.mark.parametrize(
    ("headers", "message"),
    [
        ({}, "missing ETag"),
        ({"ETag": '"sha256:' + "c" * 64 + '"'}, "does not match"),
    ],
)
def test_conditional_304_fails_closed_for_invalid_etag(
    headers: dict[str, str], message: str
) -> None:
    client = make_client(lambda _: httpx.Response(304, headers=headers))
    try:
        with pytest.raises(RelayPermanentError, match=message):
            client.get_model_catalog(if_none_match=CATALOG_ETAG)
    finally:
        client.close()


def test_unconditional_304_is_a_permanent_protocol_error() -> None:
    client = make_client(lambda _: httpx.Response(304, headers={"ETag": CATALOG_ETAG}))
    try:
        with pytest.raises(RelayPermanentError, match="without a conditional"):
            client.get_model_catalog()
    finally:
        client.close()


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"ETag": '"sha256:' + "c" * 64 + '"'},
        {"ETag": "not-an-etag"},
    ],
)
def test_catalog_200_fails_closed_when_etag_is_missing_or_inconsistent(
    headers: dict[str, str],
) -> None:
    client = make_client(
        lambda _: httpx.Response(200, json=catalog_payload(), headers=headers)
    )
    try:
        with pytest.raises(RelayPermanentError):
            client.get_model_catalog()
    finally:
        client.close()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update(object="models"),
        lambda payload: payload.update(catalog_revision="revision-1"),
        lambda payload: payload["data"][0]["capabilities"].update(schema_version=2),
        lambda payload: payload["data"][0]["capabilities"]["modes"].update(
            unknown_mode=payload["data"][0]["capabilities"]["modes"]["text_to_video"]
        ),
        lambda payload: payload["data"][0]["capabilities"]["modes"]["text_to_video"][
            "limits"
        ].update(max_images=15, max_audio=15),
        lambda payload: payload["data"][0]["capabilities"]["modes"]["text_to_video"][
            "limits"
        ].update(max_images="9"),
        lambda payload: payload["data"].append(payload["data"][0].copy()),
    ],
)
def test_catalog_schema_is_strict(mutation) -> None:
    payload = catalog_payload()
    mutation(payload)
    client = make_client(
        lambda _: httpx.Response(
            200,
            json=payload,
            headers={"ETag": CATALOG_ETAG},
        )
    )
    try:
        with pytest.raises(
            RelayPermanentError, match="model catalog response is invalid"
        ):
            client.get_model_catalog()
    finally:
        client.close()


@pytest.mark.parametrize("status_code", [429, 500, 502, 503])
def test_catalog_transient_http_failures_are_retryable(status_code: int) -> None:
    client = make_client(lambda _: httpx.Response(status_code))
    try:
        with pytest.raises(RelayTemporaryError, match="temporarily unavailable"):
            client.get_model_catalog()
    finally:
        client.close()


def test_catalog_network_failure_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(handler)
    try:
        with pytest.raises(RelayTemporaryError, match="request failed"):
            client.get_model_catalog()
    finally:
        client.close()


@pytest.mark.parametrize("status_code", [301, 400, 401, 403, 404, 422])
def test_catalog_non_retryable_http_failures_are_permanent(
    status_code: int,
) -> None:
    client = make_client(lambda _: httpx.Response(status_code))
    try:
        with pytest.raises(RelayPermanentError, match=f"HTTP {status_code}"):
            client.get_model_catalog()
    finally:
        client.close()


@pytest.mark.parametrize(
    "invalid_etag",
    ["", "catalog-v1", 'W/"sha256:' + "a" * 64 + '"', '"sha256:abc"'],
)
def test_invalid_conditional_etag_is_rejected_before_network_call(
    invalid_etag: str,
) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(304)

    client = make_client(handler)
    try:
        with pytest.raises(RelayPermanentError, match="ETag is invalid"):
            client.get_model_catalog(if_none_match=invalid_etag)
    finally:
        client.close()
    assert calls == 0
