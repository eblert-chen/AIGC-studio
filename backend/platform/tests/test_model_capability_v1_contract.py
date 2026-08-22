from __future__ import annotations

from copy import deepcopy

import pytest


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
    return client.post(
        f"/api/v1/platform-admin/models/{model_id}/publish",
        headers=headers,
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
    return client.put(
        f"/api/v1/platform-admin/companies/{company_id}/model-grants",
        headers=headers,
        json={
            "model_id": model_id,
            "enabled": True,
            "price_per_item_cents": price_per_item_cents,
            "config_override": config_override or {},
        },
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
        ("unsupported-schema", {**canonical_capability(), "schema_version": 2}),
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
            "enabled": False,
            "price_per_item_cents": 125,
            "config_override": old_override,
        },
    )
    assert disabled_stale_grant.status_code == 200, disabled_stale_grant.text
    republished = _publish(client, headers, model_id)
    assert republished.status_code == 200, republished.text
    assert republished.json()["status"] == "published"

    stale_reenable = client.put(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/model-grants",
        headers=headers,
        json={
            "model_id": model_id,
            "enabled": True,
            "price_per_item_cents": 125,
            "config_override": old_override,
        },
    )
    assert stale_reenable.status_code == 409, stale_reenable.text
