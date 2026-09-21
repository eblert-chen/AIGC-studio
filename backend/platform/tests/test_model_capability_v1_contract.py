from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

import pytest
from sqlalchemy import select

from platform_api.models import ModelCommercialReleasePlan, ModelDefinition
from platform_api.relay_client import (
    RelayModelCatalog,
    RelayModelCatalogRead,
    RelayModelReleaseEvidence,
    relay_sha256_revision,
)
from platform_api.services.models import ModelCatalogService
from .legacy_commercial_isolation import isolate_legacy_commercial_gate


class _FixtureDistributionEvidenceClient:
    """Explicit release/cost proof for tests unrelated to Relay onboarding."""

    def __init__(self, app):
        self.app = app

    def get_model_catalog(
        self,
        *,
        if_none_match: str | None = None,
        request_id: str | None = None,
    ) -> RelayModelCatalogRead:
        del request_id
        evidence = self.get_model_release_evidence()
        evidence_by_model = {
            item.public_model_id: item for item in evidence.models
        }
        data = []
        with self.app.state.session_factory() as session:
            rows = session.query(ModelDefinition).order_by(ModelDefinition.slug).all()
            for model in rows:
                route_evidence = evidence_by_model.get(model.slug)
                if (
                    route_evidence is None
                    or model.relay_capability_revision is None
                    or model.relay_capability_approved_ceiling is None
                ):
                    continue
                data.append(
                    {
                        "api_version": "v1",
                        "schema_version": 1,
                        "id": model.slug,
                        "object": "model",
                        "capability_revision": model.relay_capability_revision,
                        "lifecycle": "published_route",
                        "managed_route": True,
                        "customer_callable": True,
                        "published_route_revision": (
                            route_evidence.published_route_revision
                        ),
                        "capabilities": model.relay_capability_approved_ceiling,
                    }
                )
        catalog = RelayModelCatalog.model_validate(
            {
                "api_version": "v1",
                "schema_version": 1,
                "object": "list",
                "data": data,
                "catalog_revision_scope": evidence.catalog_revision_scope,
                "published_route_revision": evidence.published_route_revision,
                "catalog_revision": evidence.catalog_revision,
            }
        )
        etag = f'"{catalog.catalog_revision}"'
        if if_none_match == etag:
            return RelayModelCatalogRead(
                catalog=None,
                etag=etag,
                not_modified=True,
            )
        return RelayModelCatalogRead(
            catalog=catalog,
            etag=etag,
            not_modified=False,
        )

    def get_model_release_evidence(self, *, request_id: str | None = None):
        del request_id
        generated_at = datetime.now(timezone.utc)
        models = []
        catalog_revisions: set[str] = set()
        with self.app.state.session_factory() as session:
            rows = session.query(ModelDefinition).order_by(ModelDefinition.id).all()
            for model in rows:
                if (
                    model.relay_capability_revision is None
                    or model.relay_capability_approved_ceiling is None
                ):
                    continue
                if model.relay_capability_approved_catalog_revision is not None:
                    catalog_revisions.add(
                        model.relay_capability_approved_catalog_revision
                    )
                modes = sorted(
                    model.relay_capability_approved_ceiling.get("modes", {})
                )
                rectangles = []
                for mode in modes:
                    limits = model.relay_capability_approved_ceiling["modes"][mode][
                        "limits"
                    ]
                    for resolution in sorted(limits["resolutions"]):
                        rectangles.append(
                            {
                                "mode": mode,
                                "resolution": resolution,
                                "ready": True,
                                "contract_rate_id": str(
                                    uuid5(
                                        NAMESPACE_URL,
                                        f"fixture-rate:{model.id}:{mode}:{resolution}",
                                    )
                                ),
                                "billing_unit": (
                                    "output_second"
                                    if model.billing_mode == "per_second"
                                    else "output_item"
                                ),
                                "unit_amount_cents": 1,
                                "currency": "CNY",
                                "effective_from": generated_at - timedelta(days=1),
                                "source_document_sha256": "c" * 64,
                            }
                        )
                models.append(
                    {
                        "public_model_id": model.slug,
                        "capability_revision": model.relay_capability_revision,
                        "model_release_id": f"fixture-release-{model.id}",
                        "model_release_revision": "fixture-release-revision-1",
                        "published_route_revision": "sha256:" + "b" * 64,
                        "routing_release_sha256": "sha256:" + "d" * 64,
                        "provider_cost_readiness_sha256": "sha256:" + "e" * 64,
                        "provider_cost_ready": bool(rectangles),
                        "provider_cost_rectangle_count": len(rectangles),
                        "provider_cost_ready_rectangle_count": len(rectangles),
                        "route_count": 1,
                        "enabled_route_count": 1,
                        "accepted_route_count": 1,
                        "fresh_test_count": 1,
                        "latest_successful_test_at": generated_at
                        - timedelta(seconds=1),
                        "status": "ready",
                        "routes": [
                            {
                                "route_id": f"fixture-route-{model.id}",
                                "channel_id": 1,
                                "provider_name": "fixture-provider",
                                "provider_account_id": "fixture-account-a",
                                "provider_key_index": 0,
                                "provider_key_fingerprint_prefix": "a" * 12,
                                "provider_credential_set_version": (
                                    "11111111-1111-4111-8111-111111111111"
                                ),
                                "route_binding_sha256": "sha256:" + "f" * 64,
                                "upstream_model": f"fixture-{model.slug}",
                                "adapter_profile_id": "fixture-profile-v1",
                                "adapter_profile_revision": "fixture-profile-revision-1",
                                "enabled": True,
                                "accepted": True,
                                "fresh": True,
                                "latest_successful_test_at": generated_at
                                - timedelta(seconds=1),
                                "fresh_until": generated_at
                                + timedelta(seconds=899),
                                "required_test_modes": modes,
                                "fresh_test_modes": modes,
                                "provider_cost_ready": bool(rectangles),
                                "provider_cost_rectangle_count": len(rectangles),
                                "provider_cost_ready_rectangle_count": len(rectangles),
                                "provider_cost_rectangles": rectangles,
                            }
                        ],
                    }
                )
        models.sort(key=lambda item: item["public_model_id"])
        catalog_revision = (
            next(iter(catalog_revisions))
            if len(catalog_revisions) == 1
            else relay_sha256_revision(
                [
                    {
                        "id": item["public_model_id"],
                        "revision": item["capability_revision"],
                    }
                    for item in models
                ]
            )
        )
        return RelayModelReleaseEvidence.model_validate(
            {
                "schema_version": 1,
                "object": "relay.model_release_evidence",
                "catalog_revision": catalog_revision,
                "catalog_revision_scope": "transport_snapshot",
                "published_route_revision": "sha256:" + "b" * 64,
                "generated_at": generated_at,
                "test_freshness_max_age_seconds": 900,
                "models": models,
            }
        )


def _pin_fixture_distribution_evidence(client, model_id: str) -> bool:
    with client.app.state.session_factory.begin() as session:
        model = session.get(ModelDefinition, model_id)
        assert model is not None
        generation = ModelCatalogService.capabilities(
            session, model_id=model.id
        ).get("generation")
        if (
            not isinstance(generation, dict)
            or generation.get("schema_version") != 1
            or not isinstance(generation.get("modes"), dict)
            or not generation["modes"]
        ):
            # Legacy catalog and pricing tests predate the canonical generation
            # capability shape. Their explicit Relay fixture still needs a
            # structurally valid reviewed ceiling and route-test mode set; the
            # production path receives no such substitution.
            generation = canonical_capability()
        revision = relay_sha256_revision(generation)
        catalog_revision = relay_sha256_revision(
            [{"id": model.slug, "revision": revision}]
        )
        now = datetime.now(timezone.utc)
        model.relay_capability_candidate_revision = revision
        model.relay_capability_candidate_catalog_revision = catalog_revision
        model.relay_capability_candidate = deepcopy(generation)
        model.relay_capability_candidate_synced_at = now
        model.relay_capability_revision = revision
        model.relay_capability_approved_catalog_revision = catalog_revision
        model.relay_capability_approved_ceiling = deepcopy(generation)
        model.relay_capability_approved_at = now
    return True


def _request_with_fixture_distribution_evidence(client, model_id: str, request):
    # Legacy capability/cents integration tests isolate the commercial-price
    # dependency. Any genuine commercial-plan fixture keeps the full policy.
    with client.app.state.session_factory() as session:
        has_commercial_plan = session.scalar(select(ModelCommercialReleasePlan.id).limit(1)) is not None
    if not has_commercial_plan:
        isolate_legacy_commercial_gate(client.app)
    if client.app.state.relay_client is not None:
        return request()
    if not _pin_fixture_distribution_evidence(client, model_id):
        return request()
    client.app.state.relay_client = _FixtureDistributionEvidenceClient(client.app)
    try:
        return request()
    finally:
        client.app.state.relay_client = None


def _admin_headers(client, suffix: str) -> dict[str, str]:
    existing = getattr(client, "_capability_owner_headers", None)
    if existing is not None:
        return existing
    response = client.post(
        "/api/v1/bootstrap/platform-admin",
        json={
            "email": f"capability-{suffix}@example.com",
            "display_name": f"Capability {suffix}",
        },
    )
    assert response.status_code == 201, response.text
    headers = {"X-Platform-Admin-User-ID": response.json()["user_id"]}
    # The first bootstrapped administrator is the protected product owner in
    # test mode. Reuse it instead of manufacturing additional zero-permission
    # delegated administrators for each catalog fixture.
    client._capability_owner_headers = headers
    return headers


def _mode(
    *,
    max_images: int = 9,
    max_videos: int = 3,
    max_audio: int = 3,
    supports_face: bool = True,
    durations: list[int] | None = None,
    resolutions: list[str] | None = None,
    output_counts: list[int] | None = None,
    input_media_types: list[str] | None = None,
) -> dict:
    return {
        "input_media_types": input_media_types
        if input_media_types is not None
        else ["audio", "image", "video"],
        "supports_face": supports_face,
        "required_resource_keys": [],
        "limits": {
            "max_prompt_length": 2_000,
            "max_images": max_images,
            "max_videos": max_videos,
            "max_audio": max_audio,
            "duration_seconds": durations or [5, 10],
            "aspect_ratios": ["16:9", "9:16"],
            # Canonical capability responses sort string sets lexicographically.
            "resolutions": resolutions or ["1080p", "720p"],
            "output_counts": output_counts or [1, 2, 3],
        },
    }


def canonical_capability(*, modes: dict[str, dict] | None = None) -> dict:
    return {
        "schema_version": 1,
        "modes": modes or {"text_to_video": _mode()},
    }


def _create_model(
    client,
    headers: dict[str, str],
    *,
    suffix: str,
    capability: dict,
    billing_mode: str = "per_item",
):
    return client.post(
        "/api/v1/platform-admin/models",
        headers=headers,
        json={
            "slug": f"capability-v1-{suffix}",
            "display_name": f"Capability V1 {suffix}",
            "provider_key": "relay-capability-v1",
            "billing_mode": billing_mode,
            "capabilities": [{"key": "generation", "config": capability}],
        },
    )


def _publish(client, headers: dict[str, str], model_id: str):
    return _request_with_fixture_distribution_evidence(
        client,
        model_id,
        lambda: client.post(
            f"/api/v1/platform-admin/models/{model_id}/publish",
            headers=headers,
        ),
    )


def _grant(
    client,
    headers: dict[str, str],
    *,
    company_id: str,
    model_id: str,
    config_override: dict | None = None,
    price_per_item_cents: int = 125,
):
    entitlements = client.get(
        f"/api/v1/platform-admin/companies/{company_id}/entitlements",
        headers=headers,
    )
    expected_updated_at = None
    if entitlements.status_code == 200:
        current = next(
            (
                item
                for item in entitlements.json()["models"]
                if item["model_id"] == model_id and item["grant_id"] is not None
            ),
            None,
        )
        if current is not None:
            expected_updated_at = current["grant_updated_at"]
    return _request_with_fixture_distribution_evidence(
        client,
        model_id,
        lambda: client.put(
            f"/api/v1/platform-admin/companies/{company_id}/model-grants",
            headers=headers,
            json={
                "model_id": model_id,
                "expected_updated_at": expected_updated_at,
                "enabled": True,
                "price_per_item_cents": price_per_item_cents,
                "config_override": config_override or {},
            },
        ),
    )


def test_canonical_capability_round_trips_and_effective_api_is_versioned(
    client, tenant, tenant_headers
):
    headers = _admin_headers(client, "round-trip")
    capability = canonical_capability(
        modes={
            "image_to_video": _mode(
                max_images=4,
                max_videos=0,
                input_media_types=["audio", "image"],
                output_counts=[1, 2],
            ),
            "text_to_video": _mode(),
        }
    )

    created = _create_model(
        client,
        headers,
        suffix="round-trip",
        capability=capability,
    )
    assert created.status_code == 201, created.text
    model = created.json()
    assert model["capabilities"] == {"generation": capability}
    assert model["effective_capabilities"] == capability
    assert model["capability_version"] == 1

    updated = client.put(
        f"/api/v1/platform-admin/models/{model['id']}",
        headers=headers,
        json={
            "display_name": "Capability V1 renamed",
            "provider_key": "relay-capability-v1",
            "billing_mode": "per_item",
            "expected_capability_version": 1,
            "capabilities": [{"key": "generation", "config": capability}],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["capability_version"] == 2
    assert updated.json()["capabilities"] == {"generation": capability}
    assert updated.json()["effective_capabilities"] == capability

    assert _publish(client, headers, model["id"]).status_code == 200
    granted = _grant(
        client,
        headers,
        company_id=tenant["company_id"],
        model_id=model["id"],
    )
    assert granted.status_code == 200, granted.text
    available = client.get(
        f"/api/v1/companies/{tenant['company_id']}/models",
        headers=tenant_headers,
    )
    assert available.status_code == 200, available.text
    assert available.json()[0]["effective_capabilities"] == capability


def test_empty_capability_draft_cannot_be_published(client):
    headers = _admin_headers(client, "empty")
    created = client.post(
        "/api/v1/platform-admin/models",
        headers=headers,
        json={
            "slug": "capability-v1-empty",
            "display_name": "Empty capability draft",
            "provider_key": "relay-capability-v1",
            "billing_mode": "per_item",
            "capabilities": [],
        },
    )
    assert created.status_code == 201, created.text

    published = _publish(client, headers, created.json()["id"])
    assert published.status_code == 409, published.text


def test_per_second_model_requires_single_output_capability(client):
    headers = _admin_headers(client, "per-second-output-count")
    invalid = _create_model(
        client,
        headers,
        suffix="per-second-multi-output",
        capability=canonical_capability(
            modes={"text_to_video": _mode(output_counts=[1, 2])}
        ),
        billing_mode="per_second",
    )
    assert invalid.status_code == 409, invalid.text

    valid = _create_model(
        client,
        headers,
        suffix="per-second-single-output",
        capability=canonical_capability(
            modes={"text_to_video": _mode(output_counts=[1])}
        ),
        billing_mode="per_second",
    )
    assert valid.status_code == 201, valid.text


def _invalid_capabilities() -> list[tuple[str, dict]]:
    unknown_limit = canonical_capability()
    unknown_limit["modes"]["text_to_video"]["limits"]["max_imagez"] = 9

    misspelled_resolution = canonical_capability()
    limits = misspelled_resolution["modes"]["text_to_video"]["limits"]
    limits["resolutons"] = limits.pop("resolutions")

    negative_count = canonical_capability()
    negative_count["modes"]["text_to_video"]["limits"]["max_images"] = -1

    empty_duration = canonical_capability()
    empty_duration["modes"]["text_to_video"]["limits"][
        "duration_seconds"
    ] = []

    empty_resolution = canonical_capability()
    empty_resolution["modes"]["text_to_video"]["limits"]["resolutions"] = []

    contradictory_image_mode = canonical_capability(
        modes={
            "image_to_video": _mode(
                max_images=0,
                input_media_types=["audio"],
            )
        }
    )

    undeclared_video_limit = canonical_capability()
    undeclared_video_limit["modes"]["text_to_video"][
        "input_media_types"
    ] = ["audio", "image"]

    declared_zero_audio = canonical_capability()
    declared_zero_audio["modes"]["text_to_video"]["limits"]["max_audio"] = 0

    unknown_top_level = canonical_capability()
    unknown_top_level["provider_guess"] = "must-not-be-silently-ignored"

    v1_conditional = canonical_capability()
    v1_conditional["modes"]["text_to_video"][
        "conditional_required_resource_keys"
    ] = {"face_enabled": ["face.library"]}

    v2_empty_condition = canonical_capability()
    v2_empty_condition["schema_version"] = 2
    v2_empty_condition["modes"]["text_to_video"][
        "conditional_required_resource_keys"
    ] = {"face_enabled": []}

    v2_overlap = canonical_capability()
    v2_overlap["schema_version"] = 2
    v2_overlap["modes"]["text_to_video"]["required_resource_keys"] = [
        "face.library"
    ]
    v2_overlap["modes"]["text_to_video"][
        "conditional_required_resource_keys"
    ] = {"face_enabled": ["face.library"]}

    v2_duplicate = canonical_capability()
    v2_duplicate["schema_version"] = 2
    v2_duplicate["modes"]["text_to_video"][
        "conditional_required_resource_keys"
    ] = {"face_enabled": ["face.library", "face.library"]}

    v2_non_object = canonical_capability()
    v2_non_object["schema_version"] = 2
    v2_non_object["modes"]["text_to_video"][
        "conditional_required_resource_keys"
    ] = "face.library"

    return [
        ("empty-modes", {"schema_version": 1, "modes": {}}),
        ("unknown-limit", unknown_limit),
        ("misspelled-resolution", misspelled_resolution),
        ("negative-count", negative_count),
        ("empty-duration", empty_duration),
        ("empty-resolution", empty_resolution),
        ("contradictory-image-mode", contradictory_image_mode),
        ("undeclared-video-limit", undeclared_video_limit),
        ("declared-zero-audio", declared_zero_audio),
        ("unknown-top-level", unknown_top_level),
        ("v1-conditional-field", v1_conditional),
        ("v2-empty-condition", v2_empty_condition),
        ("v2-overlapping-resource", v2_overlap),
        ("v2-duplicate-resource", v2_duplicate),
        ("v2-non-object-condition", v2_non_object),
        ("unsupported-schema", {**canonical_capability(), "schema_version": 3}),
    ]


@pytest.mark.parametrize(
    ("case_name", "capability"),
    _invalid_capabilities(),
    ids=[case_name for case_name, _ in _invalid_capabilities()],
)
def test_invalid_capability_is_rejected_when_saved(
    client, case_name: str, capability: dict
):
    headers = _admin_headers(client, case_name)
    response = _create_model(
        client,
        headers,
        suffix=case_name,
        capability=capability,
    )
    assert response.status_code in {409, 422}, response.text


def test_company_override_is_canonical_and_can_only_restrict(
    client, tenant, tenant_headers
):
    headers = _admin_headers(client, "override")
    base = canonical_capability()
    created = _create_model(
        client,
        headers,
        suffix="override",
        capability=base,
    )
    assert created.status_code == 201, created.text
    model_id = created.json()["id"]
    assert _publish(client, headers, model_id).status_code == 200

    restricted_mode = _mode(
        max_images=4,
        max_videos=2,
        max_audio=1,
        supports_face=False,
        durations=[5],
        resolutions=["1080p"],
        output_counts=[1, 2],
    )
    restricted = canonical_capability(modes={"text_to_video": restricted_mode})
    grant = _grant(
        client,
        headers,
        company_id=tenant["company_id"],
        model_id=model_id,
        config_override=restricted,
    )
    assert grant.status_code == 200, grant.text

    available = client.get(
        f"/api/v1/companies/{tenant['company_id']}/models",
        headers=tenant_headers,
    )
    assert available.status_code == 200, available.text
    assert available.json()[0]["effective_capabilities"] == restricted

    expansion = deepcopy(restricted)
    expansion["modes"]["text_to_video"]["limits"]["max_images"] = 10
    expansion["modes"]["text_to_video"]["limits"]["duration_seconds"] = [5, 15]
    rejected = _grant(
        client,
        headers,
        company_id=tenant["company_id"],
        model_id=model_id,
        config_override=expansion,
    )
    assert rejected.status_code == 409, rejected.text

    after = client.get(
        f"/api/v1/companies/{tenant['company_id']}/models",
        headers=tenant_headers,
    )
    assert after.status_code == 200
    assert after.json()[0]["effective_capabilities"] == restricted


def test_sparse_override_can_remove_media_types_and_zero_their_limits(
    client, tenant, tenant_headers
):
    headers = _admin_headers(client, "sparse-media-override")
    created = _create_model(
        client,
        headers,
        suffix="sparse-media-override",
        capability=canonical_capability(),
    )
    assert created.status_code == 201, created.text
    model_id = created.json()["id"]
    assert _publish(client, headers, model_id).status_code == 200

    override = {
        "schema_version": 1,
        "modes": {
            "text_to_video": {
                "input_media_types": ["image"],
                "limits": {"max_images": 4},
            }
        },
    }
    granted = _grant(
        client,
        headers,
        company_id=tenant["company_id"],
        model_id=model_id,
        config_override=override,
    )
    assert granted.status_code == 200, granted.text

    available = client.get(
        f"/api/v1/companies/{tenant['company_id']}/models",
        headers=tenant_headers,
    )
    assert available.status_code == 200, available.text
    effective = available.json()[0]["effective_capabilities"]["modes"]["text_to_video"]
    assert effective["input_media_types"] == ["image"]
    assert effective["limits"]["max_images"] == 4
    assert effective["limits"]["max_videos"] == 0
    assert effective["limits"]["max_audio"] == 0


def test_company_override_cannot_enable_face_support(client, tenant):
    headers = _admin_headers(client, "face-expansion")
    base = canonical_capability(
        modes={"text_to_video": _mode(supports_face=False)}
    )
    created = _create_model(
        client,
        headers,
        suffix="face-expansion",
        capability=base,
    )
    assert created.status_code == 201, created.text
    model_id = created.json()["id"]
    assert _publish(client, headers, model_id).status_code == 200

    expansion = canonical_capability(
        modes={"text_to_video": _mode(supports_face=True)}
    )
    rejected = _grant(
        client,
        headers,
        company_id=tenant["company_id"],
        model_id=model_id,
        config_override=expansion,
    )
    assert rejected.status_code == 409, rejected.text


def test_republish_requires_existing_company_overrides_to_match_new_capability(
    client, tenant
):
    headers = _admin_headers(client, "republish-override")
    original = canonical_capability(
        modes={"text_to_video": _mode(max_images=9, supports_face=True)}
    )
    created = _create_model(
        client,
        headers,
        suffix="republish-override",
        capability=original,
    )
    assert created.status_code == 201, created.text
    model_id = created.json()["id"]
    assert _publish(client, headers, model_id).status_code == 200

    old_override = canonical_capability(
        modes={"text_to_video": _mode(max_images=8, supports_face=True)}
    )
    granted = _grant(
        client,
        headers,
        company_id=tenant["company_id"],
        model_id=model_id,
        config_override=old_override,
    )
    assert granted.status_code == 200, granted.text

    disabled = client.post(
        f"/api/v1/platform-admin/models/{model_id}/disable",
        headers=headers,
    )
    assert disabled.status_code == 200, disabled.text
    revised = canonical_capability(
        modes={"text_to_video": _mode(max_images=4, supports_face=False)}
    )
    updated = client.put(
        f"/api/v1/platform-admin/models/{model_id}",
        headers=headers,
        json={
            "display_name": "Republish override revised",
            "provider_key": "relay-capability-v1",
            "billing_mode": "per_item",
            "expected_capability_version": 1,
            "capabilities": [{"key": "generation", "config": revised}],
        },
    )
    assert updated.status_code == 200, updated.text

    rejected_publish = _publish(client, headers, model_id)
    assert rejected_publish.status_code == 409, rejected_publish.text
    detail = client.get(
        f"/api/v1/platform-admin/models/{model_id}", headers=headers
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == "disabled"

    disabled_stale_grant = client.put(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/model-grants",
        headers=headers,
        json={
                "model_id": model_id,
                "expected_updated_at": granted.json()["updated_at"],
                "enabled": False,
            "price_per_item_cents": 125,
            "config_override": old_override,
        },
    )
    assert disabled_stale_grant.status_code == 200, disabled_stale_grant.text
    republished = _publish(client, headers, model_id)
    assert republished.status_code == 200, republished.text
    assert republished.json()["status"] == "published"

    stale_reenable = _request_with_fixture_distribution_evidence(
        client,
        model_id,
        lambda: client.put(
            f"/api/v1/platform-admin/companies/{tenant['company_id']}/model-grants",
            headers=headers,
            json={
                "model_id": model_id,
                "enabled": True,
                "price_per_item_cents": 125,
                "config_override": old_override,
            },
        ),
    )
    assert stale_reenable.status_code == 409, stale_reenable.text
