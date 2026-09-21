from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from platform_api.director_canonical_json import director_canonical_json
from platform_api.models import (
    DirectorShotPackage,
    GenerationTask,
    InputAsset,
    ModelCapability,
    ModelDefinition,
    PersonalRetailModelGrant,
    TaskStatus,
)
from platform_api.models import (
    RelaySubmissionOutbox,
    TaskDirectorShotPackage,
    TaskInputAsset,
)
from platform_api.services.director_shot_packages import (
    DirectorShotManifest,
    DirectorShotPackageService,
    _go_director_float,
    _go_quote,
    seedance_director_shot_prompt_v1,
)
from platform_api.services.errors import ConflictError
from platform_api.services.input_assets import InputAssetService

from .conftest import bootstrap
from .legacy_commercial_isolation import isolate_legacy_commercial_gate
from .media_fixtures import valid_png_bytes
from .test_input_assets import CaptureRelayClient
from .test_personal_input_assets import _credit, _upload_personal
from .test_personal_workspace import _personal_user
from .test_wallet_and_tasks import seed_model


PNG_BYTES = valid_png_bytes(1280, 720)


def _canonical_sha256(value: object) -> str:
    encoded = director_canonical_json(value)
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def test_seedance_director_prompt_matches_shared_cross_language_fixture() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[3]
        / "contracts"
        / "director-shot-prompt-v1-fixtures.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert fixture["schema_version"] == 1
    for case in fixture["float_cases"]:
        assert _go_director_float(case["value"]) == case["expected"]
    for case in fixture["quote_cases"]:
        assert _go_quote(case["value"]) == case["expected"]
    for case in fixture["cases"]:
        # This frozen fixture records the earlier Python representation, not
        # authority to submit it under the corrected cross-language contract.
        historical_bytes = json.dumps(
            case["manifest"], allow_nan=False, ensure_ascii=False,
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        assert "sha256:" + hashlib.sha256(historical_bytes).hexdigest() == (
            case["manifest_sha256"]
        )
        compiled = seedance_director_shot_prompt_v1(
            case["prompt"],
            manifest=DirectorShotManifest.model_validate(case["manifest"]),
            manifest_sha256=case["manifest_sha256"],
            sealed_revision=case["sealed_revision"],
        )
        assert len(compiled) == case["expected_prompt_runes"]
        assert hashlib.sha256(compiled.encode("utf-8")).hexdigest() == (
            case["expected_prompt_sha256"]
        )


def _upload(client, tenant: dict[str, str], headers: dict[str, str]):
    response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/assets",
        headers={**headers, "Idempotency-Key": "director-composition-upload-0001"},
        files={"file": ("composition.png", PNG_BYTES, "image/png")},
        data={"media_type": "image"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload_frame(
    client,
    tenant: dict[str, str],
    headers: dict[str, str],
    *,
    filename: str,
    idempotency_key: str,
) -> dict:
    response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/assets",
        headers={**headers, "Idempotency-Key": idempotency_key},
        files={"file": (filename, PNG_BYTES, "image/png")},
        data={"media_type": "image"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _manifest(asset: dict) -> dict:
    return {
        "schema": "xutian.director-shot-package",
        "schemaVersion": 1,
        "source": {
            "editor": "storyai-3d-director-desk",
            "projectSchemaVersion": 1,
            "sceneRevision": "sha256:" + ("a" * 64),
            "coordinateSystem": {
                "handedness": "right",
                "upAxis": "Y",
                "cameraForwardAxis": "-Z",
                "distanceUnit": "meter",
                "rotationUnit": "radian",
            },
        },
        "camera": {
            "stableId": "camera-main",
            "name": "主机位",
            "projection": "perspective",
            "verticalFovDegrees": 45,
            "aspectRatio": 16 / 9,
            "view": {
                "positionMeters": [0, 1.6, 5],
                "targetMeters": [0, 1, 0],
                "up": [0, 1, 0],
            },
        },
        "objects": [
            {
                "stableId": "character-hero",
                "name": "主角",
                "kind": "character",
                "visible": True,
                "worldTransform": {
                    "positionMeters": [0, 0, 0],
                    "rotationQuaternion": [0, 0, 0, 1],
                    "scale": [1, 1, 1],
                },
                "pose": {
                    "rigType": "mannequin",
                    "presetId": "walk",
                    "controls": {"leftHip.pitch": -0.349066},
                },
            }
        ],
        "composition": {
            "role": "composition",
            "mediaType": "image/png",
            "fileName": "composition.png",
            "widthPx": 1280,
            "heightPx": 720,
            "byteLength": len(PNG_BYTES),
            "sha256": "sha256:" + asset["sha256"],
        },
        "analysis": {
            "depth": {"status": "not_provided"},
            "occlusion": {"status": "not_provided"},
        },
    }


def _seal(client, tenant, headers, asset, manifest, *, idempotency_key):
    return client.post(
        f"/api/v1/companies/{tenant['company_id']}/director-shot-packages",
        headers=headers,
        json={
            "idempotency_key": idempotency_key,
            "composition_asset_id": asset["id"],
            "manifest": manifest,
            "integrity": {
                "canonicalization": "xutian-json-sort-v1",
                "manifest_sha256": _canonical_sha256(manifest),
            },
        },
    )


def test_company_can_seal_and_replay_exact_director_shot_package(
    app, client, tenant, tenant_headers
):
    asset = _upload(client, tenant, tenant_headers)
    manifest = _manifest(asset)
    response = _seal(
        client,
        tenant,
        tenant_headers,
        asset,
        manifest,
        idempotency_key="director-package-seal-0001",
    )
    assert response.status_code == 201, response.text
    package = response.json()
    assert package["package_id"].startswith("dsp_")
    assert package["composition_asset_id"] == asset["id"]
    assert package["manifest"] == manifest
    assert package["manifest_sha256"] == _canonical_sha256(manifest)
    assert package["scene_revision"] == manifest["source"]["sceneRevision"]
    assert package["sealed_revision"].startswith("sha256:")

    replay = _seal(
        client,
        tenant,
        tenant_headers,
        asset,
        manifest,
        idempotency_key="director-package-seal-0001",
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["package_id"] == package["package_id"]

    fetched = client.get(
        f"/api/v1/companies/{tenant['company_id']}/director-shot-packages/"
        f"{package['package_id']}",
        headers=tenant_headers,
    )
    assert fetched.status_code == 200, fetched.text
    fetched_package = fetched.json()
    assert fetched_package["package_id"] == package["package_id"]
    assert fetched_package["manifest"] == package["manifest"]
    assert fetched_package["sealed_revision"] == package["sealed_revision"]

    with app.state.session_factory() as session:
        assert len(session.scalars(select(DirectorShotPackage)).all()) == 1


def test_seal_and_disable_are_serialized_without_permanently_pinning_asset(
    app, client, tenant, tenant_headers
):
    asset = _upload(client, tenant, tenant_headers)
    manifest = _manifest(asset)
    first = _seal(
        client,
        tenant,
        tenant_headers,
        asset,
        manifest,
        idempotency_key="director-package-disable-seal-0001",
    )
    assert first.status_code == 201, first.text

    # A package is immutable evidence, not a permanent pin. Once sealing has
    # committed, disabling may succeed. Exact package replay still works, but
    # any new seal/task must re-lock and re-check ACTIVE state.
    disabled = client.delete(
        f"/api/v1/companies/{tenant['company_id']}/assets/{asset['id']}",
        headers=tenant_headers,
    )
    assert disabled.status_code == 204, disabled.text
    replay = _seal(
        client,
        tenant,
        tenant_headers,
        asset,
        manifest,
        idempotency_key="director-package-disable-seal-0001",
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["package_id"] == first.json()["package_id"]
    rejected_new_seal = _seal(
        client,
        tenant,
        tenant_headers,
        asset,
        manifest,
        idempotency_key="director-package-after-disable-0001",
    )
    assert rejected_new_seal.status_code == 404, rejected_new_seal.text
    model_id = seed_model(
        app,
        tenant["company_id"],
        capability_key="generation",
        capability_config=_static_director_capability(),
    )
    with app.state.session_factory() as session:
        task_count = len(session.scalars(select(GenerationTask)).all())
    rejected_task = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "director-package-disabled-task-0001",
            "request_payload": {
                "mode": "image_to_video",
                "prompt": "禁用后的封存包不能穿过新任务准入",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "output_count": 1,
                "assets": [
                    {
                        "asset_id": asset["id"],
                        "media_type": "image",
                        "role": "reference_image",
                    }
                ],
                "director_shot_package": {
                    "package_id": first.json()["package_id"],
                    "manifest_sha256": first.json()["manifest_sha256"],
                    "sealed_revision": first.json()["sealed_revision"],
                },
            },
        },
    )
    assert rejected_task.status_code == 404, rejected_task.text
    with app.state.session_factory() as session:
        assert len(session.scalars(select(GenerationTask)).all()) == task_count


def test_package_seal_rejects_tampering_and_cross_scope_asset(
    client, tenant, tenant_headers
):
    asset = _upload(client, tenant, tenant_headers)
    manifest = _manifest(asset)
    tampered = client.post(
        f"/api/v1/companies/{tenant['company_id']}/director-shot-packages",
        headers=tenant_headers,
        json={
            "idempotency_key": "director-package-tampered-0001",
            "composition_asset_id": asset["id"],
            "manifest": manifest,
            "integrity": {
                "canonicalization": "xutian-json-sort-v1",
                "manifest_sha256": "sha256:" + ("0" * 64),
            },
        },
    )
    assert tampered.status_code == 409

    other = bootstrap(client, "director-package-other")
    other_headers = {
        "X-Company-ID": other["company_id"],
        "X-User-ID": other["user_id"],
    }
    foreign = _seal(
        client,
        other,
        other_headers,
        asset,
        manifest,
        idempotency_key="director-package-foreign-0001",
    )
    assert foreign.status_code == 404
    assert "不属于" in foreign.text or "不存在" in foreign.text


def test_package_seal_rejects_camera_composition_or_decoded_size_drift(
    client, tenant, tenant_headers
):
    asset = _upload(client, tenant, tenant_headers)
    camera_drift = _manifest(asset)
    camera_drift["camera"]["aspectRatio"] = 1.0
    rejected_camera = _seal(
        client,
        tenant,
        tenant_headers,
        asset,
        camera_drift,
        idempotency_key="director-camera-ratio-drift-0001",
    )
    assert rejected_camera.status_code == 422

    # This drift was inside the former 0.5% ratio tolerance. Camera aspect is
    # now allowed to differ from the exact decoded composition only by binary
    # float representation noise.
    near_camera_drift = _manifest(asset)
    near_camera_drift["camera"]["aspectRatio"] = (16 / 9) + 0.000001
    rejected_near_camera = _seal(
        client,
        tenant,
        tenant_headers,
        asset,
        near_camera_drift,
        idempotency_key="director-near-camera-ratio-drift-0001",
    )
    assert rejected_near_camera.status_code == 422

    decoded_size_drift = _manifest(asset)
    decoded_size_drift["composition"]["widthPx"] = 640
    decoded_size_drift["composition"]["heightPx"] = 360
    rejected_size = _seal(
        client,
        tenant,
        tenant_headers,
        asset,
        decoded_size_drift,
        idempotency_key="director-decoded-size-drift-0001",
    )
    assert rejected_size.status_code == 409
    assert "像素尺寸" in rejected_size.text


def _static_director_capability() -> dict:
    return {
        "schema_version": 3,
        "modes": {
            "image_to_video": {
                "input_media_types": ["image"],
                "input_roles": ["reference_image", "first_frame"],
                "temporal_controls": ["first_frame"],
                "structured_inputs": ["director_shot_v1"],
                "supports_face": False,
                "required_resource_keys": [],
                "conditional_required_resource_keys": {},
                "limits": {
                    "max_prompt_length": 10_000,
                    "max_images": 1,
                    "max_videos": 0,
                    "max_audio": 0,
                    "duration_seconds": [5],
                    "aspect_ratios": ["16:9"],
                    "resolutions": ["720p"],
                    "output_counts": [1],
                },
            }
        },
    }


def _temporal_director_capability() -> dict:
    capability = _static_director_capability()
    mode = capability["modes"]["image_to_video"]
    mode["input_roles"] = ["reference_image", "first_frame", "last_frame"]
    mode["temporal_controls"] = ["first_frame", "last_frame"]
    mode["limits"]["max_images"] = 3
    return capability


def _personal_temporal_director_model(app) -> str:
    # This synthetic capability exercises locked asset/package binding only;
    # it is not a commercially published or provider-accepted video model.
    isolate_legacy_commercial_gate(app)
    capability = _temporal_director_capability()
    capability["modes"]["image_to_video"]["limits"]["max_images"] = 2
    relay_revision = "sha256:" + ("8" * 64)
    with app.state.session_factory.begin() as session:
        model = ModelDefinition(
            slug="personal-temporal-director-v1",
            display_name="Personal Temporal Director",
            provider_key="test-provider",
            billing_mode="per_second",
            relay_capability_revision=relay_revision,
            relay_capability_candidate_revision=relay_revision,
            relay_capability_candidate=capability,
            relay_capability_approved_ceiling=capability,
        )
        session.add(model)
        session.flush()
        session.add(
            ModelCapability(
                model_id=model.id,
                capability_key="generation",
                config=capability,
            )
        )
        session.add(
            PersonalRetailModelGrant(
                model_id=model.id,
                enabled=True,
                price_per_second_points=3,
                price_per_item_points=None,
                config_override={},
            )
        )
        return model.id


def _temporal_binding(package: dict, manifest: dict, first: dict, last: dict) -> dict:
    return {
        "schema": "xutian.director-temporal-binding",
        "schema_version": 1,
        "package_id": package["package_id"],
        "manifest_sha256": package["manifest_sha256"],
        "sealed_revision": package["sealed_revision"],
        "scene_revision": package["scene_revision"],
        "camera_stable_id": manifest["camera"]["stableId"],
        "temporal_revision": "sha256:" + ("b" * 64),
        "frames": [
            {
                "role": "first_frame",
                "asset_id": first["id"],
                "sha256": "sha256:" + first["sha256"],
            },
            {
                "role": "last_frame",
                "asset_id": last["id"],
                "sha256": "sha256:" + last["sha256"],
            },
        ],
    }


def _composition_first_temporal_binding(
    package: dict, manifest: dict, last: dict
) -> dict:
    """Bind only the extra endpoint; the sealed composition is first_frame."""
    return {
        "schema": "xutian.director-temporal-binding",
        "schema_version": 1,
        "package_id": package["package_id"],
        "manifest_sha256": package["manifest_sha256"],
        "sealed_revision": package["sealed_revision"],
        "scene_revision": package["scene_revision"],
        "camera_stable_id": manifest["camera"]["stableId"],
        "temporal_revision": "sha256:" + ("c" * 64),
        "frames": [
            {
                "role": "last_frame",
                "asset_id": last["id"],
                "sha256": "sha256:" + last["sha256"],
            }
        ],
    }


def test_static_director_task_binds_exact_package_asset_role_and_outbox(
    app, client, tenant, tenant_headers
):
    asset = _upload(client, tenant, tenant_headers)
    manifest = _manifest(asset)
    sealed_response = _seal(
        client,
        tenant,
        tenant_headers,
        asset,
        manifest,
        idempotency_key="director-package-task-seal-0001",
    )
    assert sealed_response.status_code == 201, sealed_response.text
    package = sealed_response.json()
    model_id = seed_model(
        app,
        tenant["company_id"],
        capability_key="generation",
        capability_config=_static_director_capability(),
    )
    recharge = client.post(
        f"/api/v1/companies/{tenant['company_id']}/wallet/recharge",
        headers=tenant_headers,
        json={
            "amount_cents": 1_000,
            "idempotency_key": "director-static-recharge-0001",
        },
    )
    assert recharge.status_code == 200, recharge.text
    director_reference = {
        "package_id": package["package_id"],
        "manifest_sha256": package["manifest_sha256"],
        "sealed_revision": package["sealed_revision"],
    }

    created = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "director-static-task-0001",
            "request_payload": {
                "mode": "image_to_video",
                "prompt": "固定当前机位、人物站位与遮挡关系，生成一个静态起势镜头",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "output_count": 1,
                "assets": [
                    {
                        "asset_id": asset["id"],
                        "media_type": "image",
                        "role": "reference_image",
                    }
                ],
                "director_shot_package": director_reference,
            },
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["request_payload"]["director_shot_package"] == director_reference
    assert task["request_payload"]["assets"] == [
        {
            "asset_id": asset["id"],
            "media_type": "image",
            "role": "reference_image",
        }
    ]
    assert task["capability_snapshot"]["effective"]["structured_inputs"] == [
        "director_shot_v1"
    ]

    with app.state.session_factory() as session:
        task_package = session.get(TaskDirectorShotPackage, task["id"])
        assert task_package is not None
        assert task_package.package_id == package["package_id"]
        assert task_package.manifest_sha256 == package["manifest_sha256"].removeprefix(
            "sha256:"
        )
        linked_asset = session.get(TaskInputAsset, (task["id"], asset["id"]))
        assert linked_asset is not None and linked_asset.position == 0
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task["id"]
            )
        )
        assert outbox is not None
        assert outbox.relay_payload["metadata"]["_platform_input_assets"] == [
            {
                "asset_id": asset["id"],
                "media_type": "image",
                "role": "reference_image",
            }
        ]
        assert outbox.relay_payload["inputs"]["director_shot"] == {
            "manifest": manifest,
            "manifest_sha256": package["manifest_sha256"],
            "sealed_revision": package["sealed_revision"],
        }


def test_temporal_binding_revalidates_package_camera_roles_and_asset_digests(
    app, client, tenant, tenant_headers
):
    composition = _upload(client, tenant, tenant_headers)
    first = _upload_frame(
        client,
        tenant,
        tenant_headers,
        filename="first.png",
        idempotency_key="director-temporal-first-0001",
    )
    last = _upload_frame(
        client,
        tenant,
        tenant_headers,
        filename="last.png",
        idempotency_key="director-temporal-last-0001",
    )
    manifest = _manifest(composition)
    package = _seal(
        client,
        tenant,
        tenant_headers,
        composition,
        manifest,
        idempotency_key="director-temporal-package-0001",
    ).json()
    reference = {
        "package_id": package["package_id"],
        "manifest_sha256": package["manifest_sha256"],
        "sealed_revision": package["sealed_revision"],
    }
    payload = {
        "mode": "image_to_video",
        "prompt": "按同一封存机位在起止状态之间运动",
        "duration_seconds": 5,
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "output_count": 1,
        "assets": [
            {
                "asset_id": composition["id"],
                "media_type": "image",
                "role": "reference_image",
            },
            {
                "asset_id": first["id"],
                "media_type": "image",
                "role": "first_frame",
            },
            {
                "asset_id": last["id"],
                "media_type": "image",
                "role": "last_frame",
            },
        ],
        "director_shot_package": reference,
        "director_temporal_binding": _temporal_binding(
            package, manifest, first, last
        ),
    }
    canonical = DirectorShotPackageService.canonicalize_task_payload(payload)
    with app.state.session_factory() as session:
        normalized, _ = InputAssetService.normalize_task_payload(
            session,
            company_id=tenant["company_id"],
            request_payload=canonical,
        )
        assert DirectorShotPackageService.require_for_task(
            session,
            company_id=tenant["company_id"],
            personal_workspace_id=None,
            request_payload=normalized,
        ).id == package["package_id"]

        missing_binding = dict(normalized)
        missing_binding.pop("director_temporal_binding")
        with pytest.raises(ConflictError, match="时间绑定"):
            DirectorShotPackageService.require_for_task(
                session,
                company_id=tenant["company_id"],
                personal_workspace_id=None,
                request_payload=missing_binding,
            )

        bad_digest = json.loads(json.dumps(normalized))
        bad_digest["director_temporal_binding"]["frames"][0]["sha256"] = (
            "sha256:" + ("0" * 64)
        )
        with pytest.raises(ConflictError, match="摘要"):
            DirectorShotPackageService.require_for_task(
                session,
                company_id=tenant["company_id"],
                personal_workspace_id=None,
                request_payload=bad_digest,
            )

        bad_camera = json.loads(json.dumps(normalized))
        bad_camera["director_temporal_binding"]["camera_stable_id"] = "camera-other"
        with pytest.raises(ConflictError, match="约束包"):
            DirectorShotPackageService.require_for_task(
                session,
                company_id=tenant["company_id"],
                personal_workspace_id=None,
                request_payload=bad_camera,
            )

    reversed_frames = json.loads(json.dumps(payload))
    reversed_frames["director_temporal_binding"]["frames"].reverse()
    with pytest.raises(ConflictError, match="director_temporal_binding"):
        DirectorShotPackageService.canonicalize_task_payload(reversed_frames)

    composition_is_not_temporal = json.loads(json.dumps(payload))
    composition_is_not_temporal["assets"] = [
        {
            "asset_id": composition["id"],
            "media_type": "image",
            "role": "first_frame",
        },
        {
            "asset_id": last["id"],
            "media_type": "image",
            "role": "last_frame",
        },
    ]
    composition_is_not_temporal["director_temporal_binding"]["frames"][0] = {
        "role": "first_frame",
        "asset_id": composition["id"],
        "sha256": "sha256:" + composition["sha256"],
    }
    with app.state.session_factory() as session:
        normalized, _ = InputAssetService.normalize_task_payload(
            session,
            company_id=tenant["company_id"],
            request_payload=DirectorShotPackageService.canonicalize_task_payload(
                composition_is_not_temporal
            ),
        )
        with pytest.raises(ConflictError, match="角色"):
            DirectorShotPackageService.require_for_task(
                session,
                company_id=tenant["company_id"],
                personal_workspace_id=None,
                request_payload=normalized,
            )


def test_temporal_task_persists_exact_binding_and_replay_survives_disable(
    app, client, tenant, tenant_headers, internal_headers
):
    composition = _upload(client, tenant, tenant_headers)
    last = _upload_frame(
        client,
        tenant,
        tenant_headers,
        filename="last-task.png",
        idempotency_key="director-temporal-task-last-0001",
    )
    manifest = _manifest(composition)
    package = _seal(
        client,
        tenant,
        tenant_headers,
        composition,
        manifest,
        idempotency_key="director-temporal-task-package-0001",
    ).json()
    temporal_capability = _temporal_director_capability()
    temporal_capability["modes"]["image_to_video"]["limits"]["max_images"] = 2
    model_id = seed_model(
        app,
        tenant["company_id"],
        capability_key="generation",
        capability_config=temporal_capability,
    )
    assert client.post(
        f"/api/v1/companies/{tenant['company_id']}/wallet/recharge",
        headers=tenant_headers,
        json={
            "amount_cents": 1_000,
            "idempotency_key": "director-temporal-task-recharge-0001",
        },
    ).status_code == 200
    binding = _composition_first_temporal_binding(package, manifest, last)
    body = {
        "model_id": model_id,
        "idempotency_key": "director-temporal-task-create-0001",
        "request_payload": {
            "mode": "image_to_video",
            "prompt": "使用经服务端封存和校验的起止画面",
            "duration_seconds": 5,
            "aspect_ratio": "16:9",
            "resolution": "720p",
            "output_count": 1,
            "assets": [
                {
                    "asset_id": composition["id"],
                    "media_type": "image",
                    "role": "first_frame",
                },
                {
                    "asset_id": last["id"],
                    "media_type": "image",
                    "role": "last_frame",
                },
            ],
            "director_shot_package": {
                "package_id": package["package_id"],
                "manifest_sha256": package["manifest_sha256"],
                "sealed_revision": package["sealed_revision"],
            },
            "director_temporal_binding": binding,
        },
    }
    created = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json=body,
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["idempotency_key"] == body["idempotency_key"]
    assert task["request_payload"]["director_temporal_binding"] == binding
    listed = client.get(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
    )
    assert listed.status_code == 200, listed.text
    assert next(item for item in listed.json() if item["id"] == task["id"])[
        "idempotency_key"
    ] == body["idempotency_key"]
    detail = client.get(
        f"/api/v1/companies/{tenant['company_id']}/tasks/{task['id']}",
        headers=tenant_headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["idempotency_key"] == body["idempotency_key"]

    capture = CaptureRelayClient()
    app.state.relay_client = capture
    dispatched = client.post("/internal/relay/dispatch-once", headers=internal_headers)
    assert dispatched.status_code == 200, dispatched.text
    assert dispatched.json()["status"] == "sent", dispatched.text
    assert len(capture.calls) == 1
    relay_payload = capture.calls[0][0]
    assert relay_payload.output.aspect_ratio == body["request_payload"]["aspect_ratio"]
    assert [item.role for item in relay_payload.inputs.assets] == [
        "first_frame", "last_frame"
    ]
    assert all(
        str(item.url).startswith("http://platform-internal:8000/")
        for item in relay_payload.inputs.assets
    )
    assert "_platform_input_assets" not in relay_payload.metadata

    with app.state.session_factory.begin() as session:
        stored = session.get(GenerationTask, task["id"])
        assert stored is not None
        stored.status = TaskStatus.SUCCEEDED
    assert client.delete(
        f"/api/v1/companies/{tenant['company_id']}/assets/{last['id']}",
        headers=tenant_headers,
    ).status_code == 204

    replay = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json=body,
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == task["id"]

    rejected_new = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={**body, "idempotency_key": "director-temporal-task-new-0001"},
    )
    assert rejected_new.status_code == 404, rejected_new.text


def test_director_package_task_rejects_task_aspect_ratio_drift_before_creation(
    app, client, tenant, tenant_headers
):
    asset = _upload(client, tenant, tenant_headers)
    manifest = _manifest(asset)
    sealed = _seal(
        client,
        tenant,
        tenant_headers,
        asset,
        manifest,
        idempotency_key="director-ratio-task-seal-0001",
    ).json()
    capability = _static_director_capability()
    capability["modes"]["image_to_video"]["limits"]["aspect_ratios"] = [
        "16:9",
        "9:16",
        "1777:1000",
    ]
    model_id = seed_model(
        app,
        tenant["company_id"],
        capability_key="generation",
        capability_config=capability,
    )
    assert client.post(
        f"/api/v1/companies/{tenant['company_id']}/wallet/recharge",
        headers=tenant_headers,
        json={
            "amount_cents": 1_000,
            "idempotency_key": "director-ratio-recharge-0001",
        },
    ).status_code == 200
    rejected = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "director-ratio-task-0001",
            "request_payload": {
                "mode": "image_to_video",
                "prompt": "比例必须在创建任务之前一致",
                "duration_seconds": 5,
                "aspect_ratio": "9:16",
                "resolution": "720p",
                "output_count": 1,
                "assets": [
                    {
                        "asset_id": asset["id"],
                        "media_type": "image",
                        "role": "reference_image",
                    }
                ],
                "director_shot_package": {
                    "package_id": sealed["package_id"],
                    "manifest_sha256": sealed["manifest_sha256"],
                    "sealed_revision": sealed["sealed_revision"],
                },
            },
        },
    )
    assert rejected.status_code == 409, rejected.text
    assert "比例" in rejected.text

    near_ratio_payload = {
        "mode": "image_to_video",
        "prompt": "近似比例也必须在创建任务之前被拒绝",
        "duration_seconds": 5,
        "aspect_ratio": "1777:1000",
        "resolution": "720p",
        "output_count": 1,
        "assets": [
            {
                "asset_id": asset["id"],
                "media_type": "image",
                "role": "reference_image",
            }
        ],
        "director_shot_package": {
            "package_id": sealed["package_id"],
            "manifest_sha256": sealed["manifest_sha256"],
            "sealed_revision": sealed["sealed_revision"],
        },
    }
    rejected_near = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "director-near-ratio-task-0001",
            "request_payload": near_ratio_payload,
        },
    )
    assert rejected_near.status_code == 409, rejected_near.text
    assert "比例" in rejected_near.text


def test_legacy_canonical_package_is_readable_but_requires_a_new_seal(
    app, client, tenant, tenant_headers
):
    asset = _upload(client, tenant, tenant_headers)
    manifest = _manifest(asset)
    manifest["camera"]["view"]["positionMeters"][0] = 0.00001
    historical_hash = hashlib.sha256(json.dumps(
        manifest, allow_nan=False, ensure_ascii=False,
        separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")).hexdigest()
    assert "sha256:" + historical_hash != _canonical_sha256(manifest)
    package_id = "dsp_" + "f" * 32
    scene_revision = manifest["source"]["sceneRevision"].removeprefix("sha256:")
    seal = DirectorShotPackageService._sealed_revision(
        package_id=package_id,
        composition_asset_id=asset["id"],
        manifest_sha256=historical_hash,
        scene_revision_sha256=scene_revision,
    )
    # Import a pre-upgrade record without updating or re-signing any existing
    # row; historical seals remain independently readable after the upgrade.
    with app.state.session_factory.begin() as session:
        session.add(DirectorShotPackage(
            id=package_id,
            company_id=tenant["company_id"],
            created_by_user_id=tenant["user_id"],
            composition_asset_id=asset["id"],
            manifest=manifest,
            manifest_sha256=historical_hash,
            scene_revision_sha256=scene_revision,
            sealed_revision_sha256=seal,
            idempotency_key="legacy-canonical-import",
            request_fingerprint="f" * 64,
        ))
    detail_url = (
        f"/api/v1/companies/{tenant['company_id']}/director-shot-packages/{package_id}"
    )
    historical = client.get(detail_url, headers=tenant_headers)
    assert historical.status_code == 200, historical.text
    assert historical.json()["manifest_sha256"] == "sha256:" + historical_hash
    model_id = seed_model(
        app, tenant["company_id"], capability_key="generation",
        capability_config=_static_director_capability(),
    )
    recharge = client.post(
        f"/api/v1/companies/{tenant['company_id']}/wallet/recharge",
        headers=tenant_headers,
        json={"amount_cents": 1000, "idempotency_key": "legacy-canonical-credit"},
    )
    assert recharge.status_code == 200, recharge.text
    body = {
        "model_id": model_id,
        "idempotency_key": "legacy-canonical-task",
        "request_payload": {
            "mode": "image_to_video", "prompt": "保留历史封存，明确生成新版本",
            "duration_seconds": 5, "aspect_ratio": "16:9", "resolution": "720p",
            "output_count": 1,
            "assets": [{"asset_id": asset["id"], "media_type": "image", "role": "reference_image"}],
            "director_shot_package": {
                "package_id": package_id,
                "manifest_sha256": "sha256:" + historical_hash,
                "sealed_revision": "sha256:" + seal,
            },
        },
    }
    task_url = f"/api/v1/companies/{tenant['company_id']}/tasks"
    rejected = client.post(task_url, headers=tenant_headers, json=body)
    assert rejected.status_code == 409, rejected.text
    assert "重新封存" in rejected.text
    with app.state.session_factory() as session:
        assert session.scalar(select(GenerationTask.id)) is None
        assert session.scalar(select(RelaySubmissionOutbox.id)) is None
    wallet = client.get(
        f"/api/v1/companies/{tenant['company_id']}/wallet", headers=tenant_headers
    ).json()
    assert wallet["reserved_cents"] == 0
    resealed = _seal(
        client, tenant, tenant_headers, asset, manifest,
        idempotency_key="explicit-canonical-reseal",
    )
    assert resealed.status_code == 201, resealed.text
    current = resealed.json()
    assert current["package_id"] != package_id
    body["idempotency_key"] = "resealed-canonical-task"
    body["request_payload"]["director_shot_package"] = {
        key: current[key] for key in ("package_id", "manifest_sha256", "sealed_revision")
    }
    created = client.post(task_url, headers=tenant_headers, json=body)
    assert created.status_code == 201, created.text
    assert client.get(detail_url, headers=tenant_headers).json() == historical.json()


def test_legacy_package_without_trusted_composition_metadata_cannot_enter_task(
    app, client, tenant, tenant_headers
):
    asset = _upload(client, tenant, tenant_headers)
    manifest = _manifest(asset)
    sealed = _seal(
        client,
        tenant,
        tenant_headers,
        asset,
        manifest,
        idempotency_key="director-legacy-metadata-seal-0001",
    ).json()
    with app.state.session_factory() as session:
        stored = session.get(InputAsset, asset["id"])
        assert stored is not None
        stored.media_metadata_version = None
        stored.width_px = None
        stored.height_px = None
        session.commit()

    model_id = seed_model(
        app,
        tenant["company_id"],
        capability_key="generation",
        capability_config=_static_director_capability(),
    )
    with app.state.session_factory() as session:
        before = len(session.scalars(select(GenerationTask)).all())
    rejected = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "director-legacy-metadata-task-0001",
            "request_payload": {
                "mode": "image_to_video",
                "prompt": "旧包必须重新取得服务端可信构图尺寸",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "output_count": 1,
                "assets": [
                    {
                        "asset_id": asset["id"],
                        "media_type": "image",
                        "role": "reference_image",
                    }
                ],
                "director_shot_package": {
                    "package_id": sealed["package_id"],
                    "manifest_sha256": sealed["manifest_sha256"],
                    "sealed_revision": sealed["sealed_revision"],
                },
            },
        },
    )
    assert rejected.status_code == 409, rejected.text
    assert "可信解码尺寸" in rejected.text
    with app.state.session_factory() as session:
        assert len(session.scalars(select(GenerationTask)).all()) == before


def test_director_prompt_budget_fails_before_task_creation_or_reservation(
    app, client, tenant, tenant_headers
):
    asset = _upload(client, tenant, tenant_headers)
    manifest = _manifest(asset)
    sealed = _seal(
        client,
        tenant,
        tenant_headers,
        asset,
        manifest,
        idempotency_key="director-budget-seal-0001",
    ).json()
    model_id = seed_model(
        app,
        tenant["company_id"],
        capability_key="generation",
        capability_config=_static_director_capability(),
    )
    assert client.post(
        f"/api/v1/companies/{tenant['company_id']}/wallet/recharge",
        headers=tenant_headers,
        json={
            "amount_cents": 1_000,
            "idempotency_key": "director-budget-recharge-0001",
        },
    ).status_code == 200
    with app.state.session_factory() as session:
        before = len(session.scalars(select(GenerationTask)).all())
    rejected = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "director-budget-task-0001",
            "request_payload": {
                "mode": "image_to_video",
                "prompt": "镜" * 1_500,
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "output_count": 1,
                "assets": [
                    {
                        "asset_id": asset["id"],
                        "media_type": "image",
                        "role": "reference_image",
                    }
                ],
                "director_shot_package": {
                    "package_id": sealed["package_id"],
                    "manifest_sha256": sealed["manifest_sha256"],
                    "sealed_revision": sealed["sealed_revision"],
                },
            },
        },
    )
    assert rejected.status_code == 409, rejected.text
    assert "2000" in rejected.text
    with app.state.session_factory() as session:
        assert len(session.scalars(select(GenerationTask)).all()) == before


def test_personal_owner_can_seal_package_without_company_asset_authority(
    app, client
):
    user_id = _personal_user(app, "director-package-owner")
    headers = {"X-User-ID": user_id}
    workspace = client.get("/api/v1/personal/me", headers=headers).json()
    uploaded = _upload_personal(
        client,
        headers,
        filename="personal-composition.png",
        content=PNG_BYTES,
        content_type="image/png",
        media_type="image",
        idempotency_key="personal-director-composition-0001",
    )
    assert uploaded.status_code == 201, uploaded.text
    asset = uploaded.json()
    manifest = _manifest(asset)

    sealed = client.post(
        "/api/v1/personal/director-shot-packages",
        headers=headers,
        json={
            "idempotency_key": "personal-director-package-0001",
            "composition_asset_id": asset["id"],
            "manifest": manifest,
            "integrity": {
                "canonicalization": "xutian-json-sort-v1",
                "manifest_sha256": _canonical_sha256(manifest),
            },
        },
    )
    assert sealed.status_code == 201, sealed.text
    package = sealed.json()
    assert package["company_id"] is None
    assert package["personal_workspace_id"] == workspace["workspace_id"]

    other_user_id = _personal_user(app, "director-package-other-owner")
    hidden = client.get(
        f"/api/v1/personal/director-shot-packages/{package['package_id']}",
        headers={"X-User-ID": other_user_id},
    )
    assert hidden.status_code == 404

    assert client.delete(
        f"/api/v1/personal/assets/{asset['id']}",
        headers=headers,
    ).status_code == 204
    replay_body = {
        "idempotency_key": "personal-director-package-0001",
        "composition_asset_id": asset["id"],
        "manifest": manifest,
        "integrity": {
            "canonicalization": "xutian-json-sort-v1",
            "manifest_sha256": _canonical_sha256(manifest),
        },
    }
    replay = client.post(
        "/api/v1/personal/director-shot-packages",
        headers=headers,
        json=replay_body,
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["package_id"] == package["package_id"]
    rejected_new = client.post(
        "/api/v1/personal/director-shot-packages",
        headers=headers,
        json={
            **replay_body,
            "idempotency_key": "personal-director-package-after-disable-0001",
        },
    )
    assert rejected_new.status_code == 404, rejected_new.text


def test_personal_temporal_task_uses_the_same_locked_binding_admission(
    app, client, internal_headers
):
    user_id = _personal_user(app, "personal-temporal-director-owner")
    headers = {"X-User-ID": user_id}
    workspace = client.get("/api/v1/personal/me", headers=headers).json()

    def upload(filename: str, key: str) -> dict:
        response = _upload_personal(
            client,
            headers,
            filename=filename,
            content=PNG_BYTES,
            content_type="image/png",
            media_type="image",
            idempotency_key=key,
        )
        assert response.status_code == 201, response.text
        return response.json()

    composition = upload(
        "personal-temporal-composition.png",
        "personal-temporal-composition-0001",
    )
    last = upload("personal-temporal-last.png", "personal-temporal-last-0001")
    manifest = _manifest(composition)
    sealed_response = client.post(
        "/api/v1/personal/director-shot-packages",
        headers=headers,
        json={
            "idempotency_key": "personal-temporal-package-0001",
            "composition_asset_id": composition["id"],
            "manifest": manifest,
            "integrity": {
                "canonicalization": "xutian-json-sort-v1",
                "manifest_sha256": _canonical_sha256(manifest),
            },
        },
    )
    assert sealed_response.status_code == 201, sealed_response.text
    package = sealed_response.json()
    model_id = _personal_temporal_director_model(app)
    model = next(
        item
        for item in client.get("/api/v1/personal/models", headers=headers).json()
        if item["id"] == model_id
    )
    _credit(client, workspace["workspace_id"])
    binding = _composition_first_temporal_binding(package, manifest, last)
    created = client.post(
        "/api/v1/personal/tasks",
        headers=headers,
        json={
            "model_id": model_id,
            "expected_capability_version": model["capability_version"],
            "expected_quote_revision": model["quote_revision"],
            "idempotency_key": "personal-temporal-task-0001",
            "request_payload": {
                "mode": "image_to_video",
                "prompt": "个人空间也要经过同一组服务端行锁和摘要校验",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "output_count": 1,
                "assets": [
                    {
                        "asset_id": composition["id"],
                        "media_type": "image",
                        "role": "first_frame",
                    },
                    {
                        "asset_id": last["id"],
                        "media_type": "image",
                        "role": "last_frame",
                    },
                ],
                "director_shot_package": {
                    "package_id": package["package_id"],
                    "manifest_sha256": package["manifest_sha256"],
                    "sealed_revision": package["sealed_revision"],
                },
                "director_temporal_binding": binding,
            },
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["idempotency_key"] == "personal-temporal-task-0001"
    assert created.json()["request_payload"]["director_temporal_binding"] == binding
    task_id = created.json()["id"]
    listed = client.get("/api/v1/personal/tasks", headers=headers)
    assert listed.status_code == 200, listed.text
    assert next(item for item in listed.json()["items"] if item["id"] == task_id)[
        "idempotency_key"
    ] == "personal-temporal-task-0001"
    detail = client.get(f"/api/v1/personal/tasks/{task_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["idempotency_key"] == "personal-temporal-task-0001"

    capture = CaptureRelayClient()
    app.state.relay_client = capture
    dispatched = client.post("/internal/relay/dispatch-once", headers=internal_headers)
    assert dispatched.status_code == 200, dispatched.text
    assert dispatched.json()["status"] == "sent", dispatched.text
    assert len(capture.calls) == 1
    relay_payload = capture.calls[0][0]
    assert relay_payload.output.aspect_ratio == "16:9"
    assert [item.role for item in relay_payload.inputs.assets] == [
        "first_frame", "last_frame"
    ]
    assert all(
        str(item.url).startswith("http://platform-internal:8000/")
        for item in relay_payload.inputs.assets
    )
    assert "_platform_input_assets" not in relay_payload.metadata
