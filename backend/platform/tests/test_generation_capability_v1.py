from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import func, select

from platform_api.models import (
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    ModelDefinition,
    RelaySubmissionOutbox,
    WalletAccount,
)

from .conftest import TEST_RELAY_CAPABILITY_REVISION
from .test_model_capability_v1_contract import (
    _admin_headers,
    _create_model,
    _mode,
    _publish,
    canonical_capability,
)


def _provision_model(
    client,
    tenant,
    *,
    suffix: str,
    capability: dict,
    billing_mode: str = "per_item",
    unit_price_cents: int = 25,
    config_override: dict | None = None,
) -> tuple[str, dict[str, str]]:
    normalized_suffix = suffix.replace("_", "-")
    admin_headers = _admin_headers(client, normalized_suffix)
    created = _create_model(
        client,
        admin_headers,
        suffix=normalized_suffix,
        capability=capability,
        billing_mode=billing_mode,
    )
    assert created.status_code == 201, created.text
    model_id = created.json()["id"]
    published = _publish(client, admin_headers, model_id)
    assert published.status_code == 200, published.text
    # Generation fixtures represent a catalog model whose physical Relay
    # capability revision was explicitly approved before task admission.
    with client.app.state.session_factory.begin() as session:
        model = session.get(ModelDefinition, model_id)
        assert model is not None
        model.relay_capability_revision = TEST_RELAY_CAPABILITY_REVISION
    price_field = (
        "price_per_second_cents"
        if billing_mode == "per_second"
        else "price_per_item_cents"
    )
    granted = client.put(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/model-grants",
        headers=admin_headers,
        json={
            "model_id": model_id,
            "enabled": True,
            price_field: unit_price_cents,
            "config_override": config_override or {},
        },
    )
    assert granted.status_code == 200, granted.text
    return model_id, admin_headers


def _recharge(client, tenant, tenant_headers, *, suffix: str) -> None:
    response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/wallet/recharge",
        headers=tenant_headers,
        json={
            "amount_cents": 100_000,
            "idempotency_key": f"cap-v1-recharge-{suffix}",
            "note": "capability v1 test",
        },
    )
    assert response.status_code == 200, response.text


def _upload_assets(
    client,
    tenant,
    tenant_headers,
    *,
    suffix: str,
    images: int,
    videos: int,
    audio: int,
) -> list[dict[str, str]]:
    media = {
        "image": (images, "image/png", ".png", b"\x89PNG\r\n\x1a\n"),
        "video": (videos, "video/mp4", ".mp4", b"\x00\x00\x00\x18ftypmp42"),
        "audio": (audio, "audio/mpeg", ".mp3", b"ID3"),
    }
    references: list[dict[str, str]] = []
    for media_type, (count, content_type, extension, prefix) in media.items():
        for index in range(count):
            uploaded = client.post(
                f"/api/v1/companies/{tenant['company_id']}/assets",
                headers={
                    **tenant_headers,
                    "Idempotency-Key": (
                        f"cap-v1-{suffix}-{media_type}-{index:02d}"
                    ),
                },
                files={
                    "file": (
                        f"{media_type}-{index}{extension}",
                        prefix + f"-{suffix}-{index}".encode(),
                        content_type,
                    )
                },
                data={"media_type": media_type},
            )
            assert uploaded.status_code == 201, uploaded.text
            references.append(
                {
                    "asset_id": uploaded.json()["id"],
                    "media_type": media_type,
                }
            )
    return references


def _create_task(
    client,
    tenant,
    tenant_headers,
    *,
    model_id: str,
    suffix: str,
    assets: list[dict[str, str]] | None = None,
    duration_seconds: int = 5,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    output_count: int = 1,
    face_enabled: bool = False,
):
    return client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": f"cap-v1-task-{suffix}",
            "request_payload": {
                "mode": "text_to_video",
                "prompt": "Capability v1 generation boundary test",
                "assets": assets or [],
                "duration_seconds": duration_seconds,
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "output_count": output_count,
                "face_enabled": face_enabled,
            },
        },
    )


def _side_effect_snapshot(app, company_id: str) -> dict[str, object]:
    with app.state.session_factory() as session:
        wallet = session.get(WalletAccount, company_id)
        assert wallet is not None
        return {
            "wallet": (wallet.available_cents, wallet.reserved_cents),
            "tasks": session.scalar(select(func.count(GenerationTask.id))),
            "outbox": session.scalar(select(func.count(RelaySubmissionOutbox.id))),
            "reserves": session.scalar(
                select(func.count(LedgerEntry.id)).where(
                    LedgerEntry.company_id == company_id,
                    LedgerEntry.kind == LedgerKind.RESERVE,
                )
            ),
        }


@pytest.mark.parametrize(
    ("suffix", "max_images", "counts"),
    [
        ("nine-three-three", 9, (9, 3, 3)),
        ("four-three-three", 4, (4, 3, 3)),
    ],
)
def test_mixed_media_counts_at_documented_boundaries_are_accepted(
    app, client, tenant, tenant_headers, suffix, max_images, counts
):
    capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=max_images,
                max_videos=3,
                max_audio=3,
                output_counts=[1],
            )
        }
    )
    model_id, _ = _provision_model(
        client,
        tenant,
        suffix=suffix,
        capability=capability,
    )
    _recharge(client, tenant, tenant_headers, suffix=suffix)
    assets = _upload_assets(
        client,
        tenant,
        tenant_headers,
        suffix=suffix,
        images=counts[0],
        videos=counts[1],
        audio=counts[2],
    )

    response = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix=suffix,
        assets=assets,
    )
    assert response.status_code == 201, response.text
    assert response.json()["reserved_cents"] == 25
    with app.state.session_factory() as session:
        task = session.get(GenerationTask, response.json()["id"])
        assert task is not None
        assert len(task.request_payload["assets"]) == sum(counts)


@pytest.mark.parametrize(
    ("suffix", "max_images", "counts"),
    [
        ("nine-image-plus-one", 9, (10, 0, 0)),
        ("four-image-plus-one", 4, (5, 0, 0)),
        ("video-plus-one", 9, (0, 4, 0)),
        ("audio-plus-one", 9, (0, 0, 4)),
    ],
)
def test_each_media_type_plus_one_is_rejected_before_money_or_outbox(
    app, client, tenant, tenant_headers, suffix, max_images, counts
):
    capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=max_images,
                max_videos=3,
                max_audio=3,
                output_counts=[1],
            )
        }
    )
    model_id, _ = _provision_model(
        client,
        tenant,
        suffix=suffix,
        capability=capability,
    )
    _recharge(client, tenant, tenant_headers, suffix=suffix)
    assets = _upload_assets(
        client,
        tenant,
        tenant_headers,
        suffix=suffix,
        images=counts[0],
        videos=counts[1],
        audio=counts[2],
    )
    before = _side_effect_snapshot(app, tenant["company_id"])

    response = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix=suffix,
        assets=assets,
    )
    assert response.status_code == 409, response.text
    assert _side_effect_snapshot(app, tenant["company_id"]) == before


def test_sixteen_total_assets_are_rejected_even_when_per_type_limits_allow_them(
    app, client, tenant, tenant_headers
):
    capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=9,
                max_videos=3,
                max_audio=3,
                output_counts=[1],
            )
        }
    )
    model_id, _ = _provision_model(
        client,
        tenant,
        suffix="sixteen-total",
        capability=capability,
    )
    _recharge(client, tenant, tenant_headers, suffix="sixteen-total")
    assets = _upload_assets(
        client,
        tenant,
        tenant_headers,
        suffix="sixteen-total",
        images=9,
        videos=3,
        audio=4,
    )
    before = _side_effect_snapshot(app, tenant["company_id"])

    response = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="sixteen-total",
        assets=assets,
    )
    assert response.status_code == 409, response.text
    assert _side_effect_snapshot(app, tenant["company_id"]) == before


def test_face_capability_is_enforced_and_preserved_in_the_relay_outbox(
    app, client, tenant, tenant_headers
):
    disabled = canonical_capability(
        modes={
            "text_to_video": _mode(
                supports_face=False,
                max_images=0,
                max_videos=0,
                max_audio=0,
                input_media_types=[],
                output_counts=[1],
            )
        }
    )
    disabled_model_id, _ = _provision_model(
        client,
        tenant,
        suffix="face-disabled",
        capability=disabled,
    )
    _recharge(client, tenant, tenant_headers, suffix="face-disabled")
    before = _side_effect_snapshot(app, tenant["company_id"])
    rejected = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=disabled_model_id,
        suffix="face-disabled",
        face_enabled=True,
    )
    assert rejected.status_code == 409, rejected.text
    assert _side_effect_snapshot(app, tenant["company_id"]) == before

    enabled = canonical_capability(
        modes={
            "text_to_video": _mode(
                supports_face=True,
                max_images=0,
                max_videos=0,
                max_audio=0,
                input_media_types=[],
                output_counts=[1],
            )
        }
    )
    enabled_model_id, _ = _provision_model(
        client,
        tenant,
        suffix="face-enabled",
        capability=enabled,
    )
    accepted = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=enabled_model_id,
        suffix="face-enabled",
        face_enabled=True,
    )
    assert accepted.status_code == 201, accepted.text
    with app.state.session_factory() as session:
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == accepted.json()["id"]
            )
        )
        assert outbox is not None
        assert outbox.relay_payload["output"]["face_enabled"] is True


def test_resolution_duration_ratio_and_output_enums_are_enforced(
    app, client, tenant, tenant_headers
):
    capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=0,
                max_videos=0,
                max_audio=0,
                input_media_types=[],
                durations=[2, 15],
                resolutions=["1080p"],
                output_counts=[1, 3],
            )
        }
    )
    capability["modes"]["text_to_video"]["limits"]["aspect_ratios"] = [
        "1:1",
        "9:16",
    ]
    model_id, _ = _provision_model(
        client,
        tenant,
        suffix="1080p-duration",
        capability=capability,
    )
    _recharge(client, tenant, tenant_headers, suffix="1080p-duration")

    for duration, aspect_ratio, output_count in (
        (2, "1:1", 1),
        (15, "9:16", 3),
    ):
        accepted = _create_task(
            client,
            tenant,
            tenant_headers,
            model_id=model_id,
            suffix=f"1080p-{duration}",
            duration_seconds=duration,
            aspect_ratio=aspect_ratio,
            resolution="1080p",
            output_count=output_count,
        )
        assert accepted.status_code == 201, accepted.text
        assert accepted.json()["request_payload"]["resolution"] == "1080p"
        assert accepted.json()["request_payload"]["aspect_ratio"] == aspect_ratio
        assert accepted.json()["request_payload"]["output_count"] == output_count

    for suffix, duration, aspect_ratio, resolution, output_count in (
        ("duration-low", 1, "1:1", "1080p", 1),
        ("duration-high", 16, "1:1", "1080p", 1),
        ("ratio", 2, "16:9", "1080p", 1),
        ("resolution", 2, "1:1", "720p", 1),
        ("output", 2, "1:1", "1080p", 2),
    ):
        before = _side_effect_snapshot(app, tenant["company_id"])
        rejected = _create_task(
            client,
            tenant,
            tenant_headers,
            model_id=model_id,
            suffix=suffix,
            duration_seconds=duration,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            output_count=output_count,
        )
        assert rejected.status_code == 409, rejected.text
        assert _side_effect_snapshot(app, tenant["company_id"]) == before


def test_unsupported_media_type_is_rejected_before_money_or_outbox(
    app, client, tenant, tenant_headers
):
    capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=0,
                max_videos=0,
                max_audio=0,
                input_media_types=[],
                output_counts=[1],
            )
        }
    )
    model_id, _ = _provision_model(
        client,
        tenant,
        suffix="unsupported-media",
        capability=capability,
    )
    _recharge(client, tenant, tenant_headers, suffix="unsupported-media")
    assets = _upload_assets(
        client,
        tenant,
        tenant_headers,
        suffix="unsupported-media",
        images=1,
        videos=0,
        audio=0,
    )
    before = _side_effect_snapshot(app, tenant["company_id"])

    response = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="unsupported-media",
        assets=assets,
    )

    assert response.status_code == 409, response.text
    assert _side_effect_snapshot(app, tenant["company_id"]) == before


def test_available_model_and_task_snapshot_share_one_effective_contract(
    client, tenant, tenant_headers
):
    base = canonical_capability()
    restricted = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=4,
                max_videos=3,
                max_audio=3,
                supports_face=False,
                durations=[5],
                resolutions=["1080p"],
                output_counts=[1, 2],
            )
        }
    )
    model_id, _ = _provision_model(
        client,
        tenant,
        suffix="snapshot-source",
        capability=base,
        config_override=restricted,
    )
    _recharge(client, tenant, tenant_headers, suffix="snapshot-source")
    models = client.get(
        f"/api/v1/companies/{tenant['company_id']}/models",
        headers=tenant_headers,
    )
    assert models.status_code == 200, models.text
    effective = models.json()[0]["effective_capabilities"]
    assert effective == restricted

    task = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="snapshot-source",
        resolution="1080p",
        output_count=2,
    )
    assert task.status_code == 201, task.text
    assert task.json()["capability_snapshot"]["effective_capabilities"] == effective


def test_two_models_expose_distinct_effective_capabilities(
    client, tenant, tenant_headers
):
    nine_capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=9,
                max_videos=3,
                max_audio=3,
                supports_face=True,
            )
        }
    )
    four_capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=4,
                max_videos=3,
                max_audio=3,
                supports_face=False,
                durations=[5],
                resolutions=["720p"],
                output_counts=[1],
            )
        }
    )
    nine_model_id, _ = _provision_model(
        client,
        tenant,
        suffix="distinct-nine",
        capability=nine_capability,
    )
    four_model_id, _ = _provision_model(
        client,
        tenant,
        suffix="distinct-four",
        capability=four_capability,
    )

    response = client.get(
        f"/api/v1/companies/{tenant['company_id']}/models",
        headers=tenant_headers,
    )
    assert response.status_code == 200, response.text
    by_id = {item["id"]: item for item in response.json()}

    assert by_id[nine_model_id]["effective_capabilities"] == nine_capability
    assert by_id[four_model_id]["effective_capabilities"] == four_capability
    assert (
        by_id[nine_model_id]["effective_capabilities"]["modes"]
        ["text_to_video"]["limits"]["max_images"]
        == 9
    )
    assert (
        by_id[four_model_id]["effective_capabilities"]["modes"]
        ["text_to_video"]["limits"]["max_images"]
        == 4
    )
    assert (
        by_id[nine_model_id]["effective_capabilities"]["modes"]
        ["text_to_video"]["supports_face"]
        is True
    )
    assert (
        by_id[four_model_id]["effective_capabilities"]["modes"]
        ["text_to_video"]["supports_face"]
        is False
    )


def test_task_snapshots_effective_capability_version_and_resource_grants(
    client, tenant, tenant_headers
):
    resource_key = "feature.capability.snapshot"
    capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=0,
                max_videos=0,
                max_audio=0,
                input_media_types=[],
                output_counts=[1],
            )
        }
    )
    capability["modes"]["text_to_video"]["required_resource_keys"] = [
        resource_key
    ]
    model_id, admin_headers = _provision_model(
        client,
        tenant,
        suffix="resource-snapshot",
        capability=capability,
    )
    resource = client.post(
        "/api/v1/platform-admin/resources",
        headers=admin_headers,
        json={
            "key": resource_key,
            "kind": "feature",
            "display_name": "Capability snapshot feature",
            "description": "Required by the selected generation mode",
            "active": True,
        },
    )
    assert resource.status_code == 201, resource.text
    resource_id = resource.json()["id"]
    resource_override = {"daily_limit": 7}
    grant = client.put(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}"
        f"/resources/{resource_id}",
        headers=admin_headers,
        json={"enabled": True, "config_override": resource_override},
    )
    assert grant.status_code == 200, grant.text
    _recharge(client, tenant, tenant_headers, suffix="resource-snapshot")

    available = client.get(
        f"/api/v1/companies/{tenant['company_id']}/models",
        headers=tenant_headers,
    )
    assert available.status_code == 200, available.text
    effective = next(
        item["effective_capabilities"]
        for item in available.json()
        if item["id"] == model_id
    )
    created = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="resource-snapshot",
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    original_snapshot = deepcopy(created.json()["capability_snapshot"])
    assert original_snapshot["capability_version"] == 1
    assert original_snapshot["effective_capabilities"] == effective
    assert original_snapshot["effective"]["required_resource_keys"] == [
        resource_key
    ]
    assert len(original_snapshot["resource_grants"]) == 1
    assert original_snapshot["resource_grants"][0]["key"] == resource_key
    assert original_snapshot["resource_grants"][0]["resource_id"] == resource_id
    assert original_snapshot["resource_grants"][0]["grant_id"] == grant.json()["id"]
    assert (
        original_snapshot["resource_grants"][0]["config_override"]
        == resource_override
    )

    disabled = client.put(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}"
        f"/resources/{resource_id}",
        headers=admin_headers,
        json={"enabled": False, "config_override": {}},
    )
    assert disabled.status_code == 200, disabled.text
    retired = client.put(
        f"/api/v1/platform-admin/resources/{resource_id}",
        headers=admin_headers,
        json={
            "display_name": "Capability snapshot feature",
            "description": "Retired after task admission",
            "active": False,
        },
    )
    assert retired.status_code == 200, retired.text

    stored = client.get(
        f"/api/v1/companies/{tenant['company_id']}/tasks/{task_id}",
        headers=tenant_headers,
    )
    assert stored.status_code == 200, stored.text
    assert stored.json()["capability_snapshot"] == original_snapshot


def test_nested_output_counts_drive_per_item_quote(client, tenant, tenant_headers):
    capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=0,
                max_videos=0,
                max_audio=0,
                input_media_types=[],
                output_counts=[1, 2, 3],
            )
        }
    )
    model_id, _ = _provision_model(
        client,
        tenant,
        suffix="nested-output-counts",
        capability=capability,
        unit_price_cents=125,
    )
    _recharge(client, tenant, tenant_headers, suffix="nested-output-counts")

    task = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="nested-output-counts",
        output_count=3,
    )
    assert task.status_code == 201, task.text
    assert task.json()["quote_cents"] == 375
    assert task.json()["reserved_cents"] == 375
    assert task.json()["pricing_snapshot"]["quantity"] == 3
    assert task.json()["pricing_snapshot"]["unit_price_cents"] == 125


def test_model_and_grant_changes_do_not_mutate_an_existing_task_or_outbox(
    app, client, tenant, tenant_headers
):
    original_capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                supports_face=True,
                max_images=0,
                max_videos=0,
                max_audio=0,
                input_media_types=[],
                resolutions=["720p"],
                output_counts=[1, 2],
            )
        }
    )
    model_id, admin_headers = _provision_model(
        client,
        tenant,
        suffix="immutable-snapshot",
        capability=original_capability,
        unit_price_cents=100,
    )
    _recharge(client, tenant, tenant_headers, suffix="immutable-snapshot")
    task_request = {
        "model_id": model_id,
        "idempotency_key": "cap-v1-task-immutable-snapshot",
        "request_payload": {
            "mode": "text_to_video",
            "prompt": "Existing task must retain its exact configuration snapshot",
            "assets": [],
            "duration_seconds": 5,
            "aspect_ratio": "16:9",
            "resolution": "720p",
            "output_count": 2,
            "face_enabled": True,
        },
    }
    created = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json=task_request,
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    original_task_snapshot = deepcopy(created.json()["capability_snapshot"])
    original_pricing_snapshot = deepcopy(created.json()["pricing_snapshot"])
    with app.state.session_factory() as session:
        original_outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task_id
            )
        )
        assert original_outbox is not None
        original_relay_payload = deepcopy(original_outbox.relay_payload)

    disabled = client.post(
        f"/api/v1/platform-admin/models/{model_id}/disable",
        headers=admin_headers,
    )
    assert disabled.status_code == 200, disabled.text
    revised_capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                supports_face=False,
                max_images=0,
                max_videos=0,
                max_audio=0,
                input_media_types=[],
                resolutions=["1080p"],
                output_counts=[1],
            )
        }
    )
    updated = client.put(
        f"/api/v1/platform-admin/models/{model_id}",
        headers=admin_headers,
        json={
            "display_name": "Revised snapshot model",
            "provider_key": "relay-capability-v1",
            "billing_mode": "per_item",
            "expected_capability_version": 1,
            "capabilities": [
                {"key": "generation", "config": revised_capability}
            ],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["capability_version"] == 2
    assert _publish(client, admin_headers, model_id).status_code == 200
    revised_grant = client.put(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/model-grants",
        headers=admin_headers,
        json={
            "model_id": model_id,
            "enabled": True,
            "price_per_item_cents": 250,
            "config_override": {},
        },
    )
    assert revised_grant.status_code == 200, revised_grant.text

    stored = client.get(
        f"/api/v1/companies/{tenant['company_id']}/tasks/{task_id}",
        headers=tenant_headers,
    )
    assert stored.status_code == 200, stored.text
    assert stored.json()["capability_snapshot"] == original_task_snapshot
    assert stored.json()["pricing_snapshot"] == original_pricing_snapshot
    assert stored.json()["quote_cents"] == 200

    replay = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json=task_request,
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == task_id
    assert replay.json()["capability_snapshot"] == original_task_snapshot
    assert replay.json()["pricing_snapshot"] == original_pricing_snapshot

    with app.state.session_factory() as session:
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task_id
            )
        )
        assert outbox is not None
        assert outbox.relay_payload == original_relay_payload
        assert session.scalar(
            select(func.count(RelaySubmissionOutbox.id)).where(
                RelaySubmissionOutbox.task_id == task_id
            )
        ) == 1
