from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from platform_api.relay_client import (
    HttpxRelayClient,
    RelayGenerationRequest,
    RelayModelCatalog,
    RelayModelReleaseEvidence,
    RelayPermanentError,
    RelayTemporaryError,
    relay_sha256_revision,
    validate_model_catalog_release_evidence_pair,
)

def _capability_payload() -> dict:
    return {
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
    }


CAPABILITY_REVISION = relay_sha256_revision(_capability_payload())
PUBLISHED_ROUTE_REVISION = relay_sha256_revision([])
MODEL_PUBLISHED_ROUTE_REVISION = "sha256:" + "c" * 64
CATALOG_REVISION = relay_sha256_revision(
    {
        "models": [
            {
                "id": "mock.video.v1",
                "capability_revision": CAPABILITY_REVISION,
                "lifecycle": "published_route",
                "managed_route": True,
                "customer_callable": True,
                "published_route_revision": MODEL_PUBLISHED_ROUTE_REVISION,
            }
        ],
        "published_route_revision": PUBLISHED_ROUTE_REVISION,
    }
)
CATALOG_ETAG = f'"{CATALOG_REVISION}"'
ROUTE_ACCOUNT_IDENTITY_FIELDS = (
    "provider_name",
    "provider_account_id",
    "provider_key_index",
    "provider_key_fingerprint_prefix",
    "provider_credential_set_version",
    "route_binding_sha256",
)


def _stamp_catalog(payload: dict) -> dict:
    for model in payload["data"]:
        model["capability_revision"] = relay_sha256_revision(
            model["capabilities"]
        )
    payload.setdefault("published_route_revision", PUBLISHED_ROUTE_REVISION)
    payload["catalog_revision"] = relay_sha256_revision(
        {
            "models": [
                {
                    "id": model["id"],
                    "capability_revision": model["capability_revision"],
                    "lifecycle": model["lifecycle"],
                    "managed_route": model["managed_route"],
                    "customer_callable": model["customer_callable"],
                    "published_route_revision": model[
                        "published_route_revision"
                    ],
                }
                for model in sorted(
                    payload["data"], key=lambda item: item["id"]
                )
            ],
            "published_route_revision": payload[
                "published_route_revision"
            ],
        }
    )
    return payload


def catalog_payload() -> dict:
    return _stamp_catalog(
        {
            "api_version": "v1",
            "schema_version": 1,
            "object": "list",
            "catalog_revision_scope": "transport_snapshot",
            "published_route_revision": PUBLISHED_ROUTE_REVISION,
            "data": [
                {
                    "api_version": "v1",
                    "schema_version": 1,
                    "id": "mock.video.v1",
                    "object": "model",
                    "capability_revision": CAPABILITY_REVISION,
                    "lifecycle": "published_route",
                    "managed_route": True,
                    "customer_callable": True,
                    "published_route_revision": (
                        MODEL_PUBLISHED_ROUTE_REVISION
                    ),
                    "capabilities": _capability_payload(),
                }
            ],
            "catalog_revision": CATALOG_REVISION,
        }
    )


def make_client(handler) -> HttpxRelayClient:
    return HttpxRelayClient(
        base_url="https://relay.example.test",
        client_id="customer-platform",
        api_key="relay-secret",
        internal_admission_token="relay-internal-admission-secret",
        transport=httpx.MockTransport(handler),
    )


def release_evidence_payload(*, status: str = "ready") -> dict:
    generated_at = datetime.now(timezone.utc)
    ready = status == "ready"
    latest_successful_test_at = generated_at - timedelta(seconds=30)
    return {
        "schema_version": 1,
        "object": "relay.model_release_evidence",
        "catalog_revision": CATALOG_REVISION,
        "catalog_revision_scope": "transport_snapshot",
        "published_route_revision": PUBLISHED_ROUTE_REVISION,
        "generated_at": generated_at.isoformat(),
        "test_freshness_max_age_seconds": 900,
        "models": [
            {
                "public_model_id": "mock.video.v1",
                "capability_revision": CAPABILITY_REVISION,
                "model_release_id": "release-mock-video-v1",
                "model_release_revision": "release-revision-7",
                "published_route_revision": MODEL_PUBLISHED_ROUTE_REVISION,
                "routing_release_sha256": "sha256:" + "d" * 64,
                "provider_cost_readiness_sha256": "sha256:" + "e" * 64,
                "provider_cost_ready": True,
                "provider_cost_rectangle_count": 1,
                "provider_cost_ready_rectangle_count": 1,
                "route_count": 1,
                "enabled_route_count": 1,
                "accepted_route_count": 1 if ready else 0,
                "fresh_test_count": 1 if ready else 0,
                "latest_successful_test_at": (
                    (generated_at - timedelta(seconds=30)).isoformat()
                    if ready
                    else None
                ),
                "status": status,
                "routes": [
                    {
                        "route_id": "route-mock-video-primary",
                        "channel_id": 17,
                        "provider_name": "mock-provider",
                        "provider_account_id": "mock-account-a",
                        "provider_key_index": 0,
                        "provider_key_fingerprint_prefix": "a" * 12,
                        "provider_credential_set_version": (
                            "11111111-1111-4111-8111-111111111111"
                        ),
                        "route_binding_sha256": "sha256:" + "f" * 64,
                        "upstream_model": "provider-video-2026-08",
                        "adapter_profile_id": "async-video-v1",
                        "adapter_profile_revision": "profile-revision-3",
                        "enabled": True,
                        "accepted": ready,
                        "fresh": ready,
                        "latest_successful_test_at": (
                            latest_successful_test_at.isoformat()
                            if ready
                            else None
                        ),
                        "fresh_until": (
                            (
                                latest_successful_test_at
                                + timedelta(seconds=900)
                            ).isoformat()
                            if ready
                            else None
                        ),
                        "required_test_modes": ["text_to_video"],
                        "fresh_test_modes": ["text_to_video"] if ready else [],
                        "provider_cost_ready": True,
                        "provider_cost_rectangle_count": 1,
                        "provider_cost_ready_rectangle_count": 1,
                        "provider_cost_rectangles": [
                            {
                                "mode": "text_to_video",
                                "resolution": "720p",
                                "ready": True,
                                "contract_rate_id": (
                                    "00000000-0000-4000-8000-000000000017"
                                ),
                                "billing_unit": "output_second",
                                "unit_amount_cents": 17,
                                "currency": "CNY",
                                "effective_from": (
                                    generated_at - timedelta(days=1)
                                ).isoformat(),
                                "source_document_sha256": "c" * 64,
                            }
                        ],
                    }
                ],
            }
        ],
    }


def test_reads_secret_free_exact_route_release_evidence() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=release_evidence_payload())

    client = make_client(handler)
    try:
        evidence = client.get_model_release_evidence(request_id="release-proof-1")
    finally:
        client.close()

    assert evidence.models[0].status == "ready"
    assert evidence.models[0].fresh_test_count == 1
    assert evidence.models[0].routes[0].route_id == "route-mock-video-primary"
    assert evidence.models[0].routes[0].upstream_model == "provider-video-2026-08"
    assert evidence.models[0].routes[0].fresh_until is not None
    assert "credential" not in evidence.models[0].routes[0].model_dump()
    assert captured[0].url.path == "/internal/platform-relay/model-release-evidence"
    assert captured[0].headers["x-relay-internal-admission"] == (
        "relay-internal-admission-secret"
    )
    assert captured[0].headers["x-request-id"] == "release-proof-1"
    assert "x-api-key" not in captured[0].headers


def test_release_evidence_fails_closed_without_admission_or_fresh_exact_proof() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        invalid = release_evidence_payload()
        invalid["models"][0]["fresh_test_count"] = 0
        return httpx.Response(200, json=invalid)

    no_admission = HttpxRelayClient(
        base_url="https://relay.example.test",
        client_id="customer-platform",
        api_key="relay-secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(RelayPermanentError, match="admission is not configured"):
            no_admission.get_model_release_evidence()
    finally:
        no_admission.close()
    assert calls == 0

    client = make_client(handler)
    try:
        with pytest.raises(RelayPermanentError, match="response is invalid"):
            client.get_model_release_evidence()
    finally:
        client.close()


@pytest.mark.parametrize(
    "missing_timestamp",
    ["latest_successful_test_at", "fresh_until"],
)
def test_fresh_route_evidence_requires_both_freshness_timestamps(
    missing_timestamp: str,
) -> None:
    payload = release_evidence_payload()
    payload["models"][0]["routes"][0][missing_timestamp] = None
    with pytest.raises(ValueError, match="freshness timestamps"):
        RelayModelReleaseEvidence.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda item: item.pop("lifecycle"),
        lambda item: item.update(lifecycle="reviewed_candidate"),
        lambda item: item.update(customer_callable=False),
        lambda item: item.update(published_route_revision=""),
    ],
)
def test_catalog_requires_consistent_route_lifecycle_contract(mutation) -> None:
    payload = catalog_payload()
    mutation(payload["data"][0])
    with pytest.raises(ValueError):
        RelayModelCatalog.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    ["catalog_revision", "published_route_revision", "model_route_revision"],
)
def test_catalog_and_release_evidence_must_be_one_exact_snapshot(
    field: str,
) -> None:
    catalog = RelayModelCatalog.model_validate(catalog_payload())
    raw_evidence = release_evidence_payload()
    if field == "model_route_revision":
        raw_evidence["models"][0]["published_route_revision"] = (
            "sha256:" + "9" * 64
        )
    else:
        raw_evidence[field] = "sha256:" + "9" * 64
    evidence = RelayModelReleaseEvidence.model_validate_json(
        json.dumps(raw_evidence)
    )

    with pytest.raises(RelayPermanentError):
        validate_model_catalog_release_evidence_pair(
            catalog=catalog,
            evidence=evidence,
        )


def test_release_evidence_rejects_a_ready_cost_rectangle_without_exact_rate() -> None:
    payload = release_evidence_payload()
    rectangle = payload["models"][0]["routes"][0][
        "provider_cost_rectangles"
    ][0]
    rectangle.pop("unit_amount_cents")
    client = make_client(lambda _: httpx.Response(200, json=payload))
    try:
        with pytest.raises(RelayPermanentError, match="response is invalid"):
            client.get_model_release_evidence()
    finally:
        client.close()


def test_blocked_release_can_retain_acceptance_for_a_now_disabled_route() -> None:
    payload = release_evidence_payload(status="blocked")
    payload["models"][0].update(
        route_count=1,
        enabled_route_count=0,
        accepted_route_count=1,
        fresh_test_count=0,
    )
    payload["models"][0]["routes"][0].update(
        enabled=False,
        accepted=True,
        fresh=False,
        fresh_test_modes=[],
    )
    client = make_client(lambda _: httpx.Response(200, json=payload))
    try:
        evidence = client.get_model_release_evidence()
    finally:
        client.close()
    assert evidence.models[0].status == "blocked"
    assert evidence.models[0].accepted_route_count == 1


def test_blocked_unaccepted_route_can_omit_the_entire_account_identity() -> None:
    payload = release_evidence_payload(status="blocked")
    route = payload["models"][0]["routes"][0]
    for field in ROUTE_ACCOUNT_IDENTITY_FIELDS:
        route.pop(field)

    evidence = RelayModelReleaseEvidence.model_validate_json(json.dumps(payload))

    assert evidence.models[0].routes[0].accepted is False
    assert all(
        getattr(evidence.models[0].routes[0], field) is None
        for field in ROUTE_ACCOUNT_IDENTITY_FIELDS
    )


@pytest.mark.parametrize("missing_field", ROUTE_ACCOUNT_IDENTITY_FIELDS)
def test_blocked_unaccepted_route_rejects_a_partial_account_identity(
    missing_field: str,
) -> None:
    payload = release_evidence_payload(status="blocked")
    payload["models"][0]["routes"][0].pop(missing_field)

    with pytest.raises(ValueError, match="route identity is incomplete"):
        RelayModelReleaseEvidence.model_validate_json(json.dumps(payload))


def test_blocked_legacy_release_can_omit_release_binding_but_ready_cannot() -> None:
    blocked = release_evidence_payload(status="blocked")
    blocked["models"][0].pop("model_release_id")
    blocked["models"][0].pop("model_release_revision")
    blocked["models"][0].pop("published_route_revision")
    client = make_client(lambda _: httpx.Response(200, json=blocked))
    try:
        evidence = client.get_model_release_evidence()
    finally:
        client.close()
    assert evidence.models[0].status == "blocked"
    assert evidence.models[0].model_release_id is None
    assert evidence.models[0].model_release_revision is None

    ready = release_evidence_payload()
    ready["models"][0].pop("model_release_id")
    ready["models"][0].pop("model_release_revision")
    client = make_client(lambda _: httpx.Response(200, json=ready))
    try:
        with pytest.raises(RelayPermanentError, match="response is invalid"):
            client.get_model_release_evidence()
    finally:
        client.close()


def test_ready_release_requires_exact_published_route_revision() -> None:
    ready = release_evidence_payload()
    ready["models"][0].pop("published_route_revision")
    client = make_client(lambda _: httpx.Response(200, json=ready))
    try:
        with pytest.raises(RelayPermanentError, match="response is invalid"):
            client.get_model_release_evidence()
    finally:
        client.close()


def test_release_binding_id_and_revision_must_be_present_together() -> None:
    payload = release_evidence_payload(status="blocked")
    payload["models"][0].pop("model_release_revision")
    client = make_client(lambda _: httpx.Response(200, json=payload))
    try:
        with pytest.raises(RelayPermanentError, match="response is invalid"):
            client.get_model_release_evidence()
    finally:
        client.close()


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
    assert (
        result.catalog.published_route_revision
        == PUBLISHED_ROUTE_REVISION
    )
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


def test_catalog_rejects_a_self_reported_capability_revision_collision() -> None:
    payload = catalog_payload()
    payload["data"][0]["capabilities"]["modes"]["text_to_video"]["limits"][
        "max_images"
    ] = 8
    # Keep both self-reported revisions and ETag mutually consistent.  The
    # independent canonical digest is the only signal that exposes the lie.
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


def test_catalog_rejects_a_self_reported_catalog_revision_collision() -> None:
    payload = catalog_payload()
    forged_revision = "sha256:" + "c" * 64
    payload["catalog_revision"] = forged_revision
    client = make_client(
        lambda _: httpx.Response(
            200,
            json=payload,
            headers={"ETag": f'"{forged_revision}"'},
        )
    )
    try:
        with pytest.raises(
            RelayPermanentError, match="model catalog response is invalid"
        ):
            client.get_model_catalog()
    finally:
        client.close()


def test_catalog_revision_binds_published_route_revision() -> None:
    payload = catalog_payload()
    payload["published_route_revision"] = "sha256:" + "9" * 64
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


def test_catalog_requires_published_route_revision() -> None:
    payload = catalog_payload()
    payload.pop("published_route_revision")
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


def test_catalog_accepts_v2_conditional_resources_but_v1_rejects_the_field() -> None:
    v2 = catalog_payload()
    v2["data"][0]["capabilities"]["schema_version"] = 2
    v2["data"][0]["capabilities"]["modes"]["text_to_video"][
        "conditional_required_resource_keys"
    ] = {"face_enabled": ["face.library"]}
    _stamp_catalog(v2)
    v2_etag = f'"{v2["catalog_revision"]}"'
    client = make_client(
        lambda _: httpx.Response(200, json=v2, headers={"ETag": v2_etag})
    )
    try:
        result = client.get_model_catalog()
        assert result.catalog is not None
        assert result.catalog.data[0].capabilities.contract_dump()[
            "schema_version"
        ] == 2
    finally:
        client.close()

    v1 = catalog_payload()
    v1["data"][0]["capabilities"]["modes"]["text_to_video"][
        "conditional_required_resource_keys"
    ] = {"face_enabled": ["face.library"]}
    client = make_client(
        lambda _: httpx.Response(200, json=v1, headers={"ETag": CATALOG_ETAG})
    )
    try:
        with pytest.raises(
            RelayPermanentError, match="model catalog response is invalid"
        ):
            client.get_model_catalog()
    finally:
        client.close()


@pytest.mark.parametrize(
    "conditional",
    [
        "face.library",
        {"face_enabled": []},
        {"face_enabled": ["face.library", "face.library"]},
    ],
)
def test_catalog_rejects_malformed_v2_conditional_resources(
    conditional,
) -> None:
    payload = deepcopy(catalog_payload())
    payload["data"][0]["capabilities"]["schema_version"] = 2
    payload["data"][0]["capabilities"]["modes"]["text_to_video"][
        "conditional_required_resource_keys"
    ] = conditional
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
        lambda payload: payload["data"][0]["capabilities"].update(schema_version=3),
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
