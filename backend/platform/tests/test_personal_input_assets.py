from __future__ import annotations

import pytest
from sqlalchemy import select

from platform_api.models import (
    GenerationTask,
    InputAsset,
    ModelCapability,
    ModelDefinition,
    PersonalRetailModelGrant,
    RelaySubmissionOutbox,
    TaskInputAsset,
    TaskStatus,
)
from platform_api.services.errors import ConflictError
from platform_api.services.input_assets import InputAssetService

from .conftest import bootstrap
from .legacy_commercial_isolation import isolate_legacy_commercial_gate
from .media_fixtures import valid_mp4_bytes, valid_webm_bytes
from .test_input_assets import CaptureRelayClient, PNG_BYTES
from .test_personal_workspace import _personal_user


WEBM_BYTES = valid_webm_bytes()


def _personal_reference_model(app) -> str:
    # Synthetic media-admission fixture: retain real asset privacy, billing and
    # outbox assertions while isolating commercial publication qualification.
    isolate_legacy_commercial_gate(app)
    def mode(media_type: str) -> dict:
        return {
            "input_media_types": [media_type],
            "supports_face": False,
            "required_resource_keys": [],
            "limits": {
                "max_prompt_length": 500,
                "max_images": 1 if media_type == "image" else 0,
                "max_videos": 1 if media_type == "video" else 0,
                "max_audio": 0,
                "duration_seconds": [5],
                "aspect_ratios": ["16:9"],
                "resolutions": ["720p"],
                "output_counts": [1],
            },
        }

    capability = {
        "schema_version": 1,
        "modes": {
            "image_to_video": mode("image"),
            "video_to_video": mode("video"),
        },
    }
    relay_revision = "sha256:" + ("7" * 64)
    with app.state.session_factory.begin() as session:
        model = ModelDefinition(
            slug="personal-reference-video-v1",
            display_name="Personal Reference Video",
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


def _upload_personal(
    client,
    headers: dict[str, str],
    *,
    filename: str,
    content: bytes,
    content_type: str,
    media_type: str,
    idempotency_key: str,
    normalization_profile: str | None = None,
):
    data = {"media_type": media_type}
    if normalization_profile is not None:
        data["normalization_profile"] = normalization_profile
    return client.post(
        "/api/v1/personal/assets",
        headers={**headers, "Idempotency-Key": idempotency_key},
        files={"file": (filename, content, content_type)},
        data=data,
    )


def _credit(client, workspace_id: str) -> None:
    response = client.post(
        f"/internal/personal/wallets/{workspace_id}/credit",
        headers={"X-Internal-Service-Token": "test-internal-token"},
        json={
            "amount_points": 100,
            "idempotency_key": f"personal-asset-credit-{workspace_id}",
            "note": "personal input asset integration test",
        },
    )
    assert response.status_code == 200, response.text


def _task_body(model: dict, asset: dict, *, mode: str, key: str) -> dict:
    return {
        "model_id": model["id"],
        "expected_capability_version": model["capability_version"],
        "expected_quote_revision": model["quote_revision"],
        "idempotency_key": key,
        "request_payload": {
            "mode": mode,
            "prompt": "Use this exact private reference",
            "assets": [
                {
                    "asset_id": asset["id"],
                    "media_type": asset["media_type"],
                }
            ],
            "duration_seconds": 5,
            "aspect_ratio": "16:9",
            "resolution": "720p",
            "output_count": 1,
            "face_enabled": False,
        },
    }


def test_personal_assets_are_private_idempotent_and_reach_relay(
    app,
    client,
    internal_headers,
):
    user_id = _personal_user(app, "asset-owner")
    headers = {"X-User-ID": user_id}
    me = client.get("/api/v1/personal/me", headers=headers).json()
    assert me["capabilities"]["assets"] is True

    uploaded = _upload_personal(
        client,
        headers,
        filename="blocking.png",
        content=PNG_BYTES,
        content_type="image/png",
        media_type="image",
        idempotency_key="personal-image-upload-0001",
    )
    assert uploaded.status_code == 201, uploaded.text
    asset = uploaded.json()
    assert asset["company_id"] is None
    assert asset["personal_workspace_id"] == me["workspace_id"]
    assert "object_key" not in asset

    replay = _upload_personal(
        client,
        headers,
        filename="blocking.png",
        content=PNG_BYTES,
        content_type="image/png",
        media_type="image",
        idempotency_key="personal-image-upload-0001",
    )
    assert replay.status_code == 201
    assert replay.json()["id"] == asset["id"]

    listed = client.get("/api/v1/personal/assets", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [asset["id"]]
    preview = client.get(
        f"/api/v1/personal/assets/{asset['id']}/preview",
        headers=headers,
    )
    assert preview.status_code == 200
    assert client.get(preview.json()["url"]).content == PNG_BYTES

    other_id = _personal_user(app, "asset-other")
    other_headers = {"X-User-ID": other_id}
    assert client.get(
        f"/api/v1/personal/assets/{asset['id']}/preview",
        headers=other_headers,
    ).status_code == 404

    _personal_reference_model(app)
    models = client.get("/api/v1/personal/models", headers=headers).json()
    model = next(item for item in models if item["slug"] == "personal-reference-video-v1")
    assert set(model["effective_capabilities"]["modes"]) == {
        "image_to_video",
        "video_to_video",
    }
    _credit(client, me["workspace_id"])
    created = client.post(
        "/api/v1/personal/tasks",
        headers=headers,
        json=_task_body(
            model,
            asset,
            mode="image_to_video",
            key="personal-image-task-0001",
        ),
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["request_payload"]["assets"] == [
        {"asset_id": asset["id"], "media_type": "image"}
    ]
    with app.state.session_factory() as session:
        stored_asset = session.get(InputAsset, asset["id"])
        link = session.scalar(
            select(TaskInputAsset).where(TaskInputAsset.task_id == task["id"])
        )
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task["id"]
            )
        )
        assert stored_asset.company_id is None
        assert stored_asset.personal_workspace_id == me["workspace_id"]
        assert link.asset_id == asset["id"]
        assert outbox.personal_workspace_id == me["workspace_id"]
        assert outbox.relay_payload["metadata"]["_platform_input_assets"] == [
            {"asset_id": asset["id"], "media_type": "image"}
        ]

    capture = CaptureRelayClient()
    app.state.relay_client = capture
    dispatched = client.post("/internal/relay/dispatch-once", headers=internal_headers)
    assert dispatched.status_code == 200, dispatched.text
    payload, _, _ = capture.calls[0]
    assert len(payload.inputs.assets) == 1
    assert str(payload.inputs.assets[0].url).startswith(
        "http://platform-internal:8000/"
    )
    assert "_platform_input_assets" not in payload.metadata

    assert client.delete(
        f"/api/v1/personal/assets/{asset['id']}",
        headers=headers,
    ).status_code == 409


def test_director_previs_normalization_is_scoped_idempotent_and_explicit(
    app, client
):
    user_id = _personal_user(app, "previs-normalization-owner")
    headers = {"X-User-ID": user_id}
    personal = _upload_personal(
        client,
        headers,
        filename="storyai-director-white-model-previs.webm",
        content=WEBM_BYTES,
        content_type="video/webm;codecs=vp8",
        media_type="video",
        idempotency_key="shared-previs-normalize-0001",
        normalization_profile="director_previs_mp4_v1",
    )
    assert personal.status_code == 201, personal.text
    asset = personal.json()
    assert asset["content_type"] == "video/mp4"
    assert asset["normalization_profile"] == "director_previs_mp4_v1"
    assert asset["source_sha256"] != asset["sha256"]
    assert asset["media_metadata_version"] == 1
    assert asset["media_container"] == "mp4"
    assert asset["video_codec"] == "h264"
    assert abs(asset["video_fps"] - 24) <= 0.01
    assert asset["video_has_audio"] is False
    assert asset["width_px"] == 320
    assert asset["height_px"] == 180

    replay = _upload_personal(
        client,
        headers,
        filename="storyai-director-white-model-previs.webm",
        content=WEBM_BYTES,
        content_type="video/webm",
        media_type="video",
        idempotency_key="shared-previs-normalize-0001",
        normalization_profile="director_previs_mp4_v1",
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == asset["id"]

    company = bootstrap(client, "previs-scope-company")
    company_headers = {
        "X-Company-ID": company["company_id"],
        "X-User-ID": company["user_id"],
        "Idempotency-Key": "shared-previs-normalize-0001",
    }
    company_upload = client.post(
        f"/api/v1/companies/{company['company_id']}/assets",
        headers=company_headers,
        files={
            "file": (
                "storyai-director-white-model-previs.webm",
                WEBM_BYTES,
                "video/webm",
            )
        },
        data={
            "media_type": "video",
            "normalization_profile": "director_previs_mp4_v1",
        },
    )
    assert company_upload.status_code == 201, company_upload.text
    assert company_upload.json()["id"] != asset["id"]
    assert company_upload.json()["company_id"] == company["company_id"]
    assert client.get(
        f"/api/v1/personal/assets/{company_upload.json()['id']}/preview",
        headers=headers,
    ).status_code == 404

    ordinary = _upload_personal(
        client,
        headers,
        filename="ordinary-reference.webm",
        content=WEBM_BYTES,
        content_type="video/webm",
        media_type="video",
        idempotency_key="ordinary-webm-no-normalize-0001",
    )
    assert ordinary.status_code == 201, ordinary.text
    ordinary_asset = ordinary.json()
    assert ordinary_asset["content_type"] == "video/webm"
    assert ordinary_asset["media_container"] == "webm"
    assert ordinary_asset["video_codec"] == "vp8"
    assert ordinary_asset["normalization_profile"] is None
    assert ordinary_asset["source_sha256"] is None

    with app.state.session_factory() as session:
        for candidate in (asset, ordinary_asset):
            for role in (None, "director_previs", "reference_video"):
                reference = {
                    "asset_id": candidate["id"],
                    "media_type": "video",
                }
                if role is not None:
                    reference["role"] = role
                with pytest.raises(ConflictError, match="local-only"):
                    InputAssetService.normalize_task_payload(
                        session,
                        company_id=None,
                        personal_workspace_id=asset["personal_workspace_id"],
                        request_payload={
                            "aspect_ratio": "16:9",
                            "assets": [reference],
                        },
                    )
        normalized = session.scalars(
            select(InputAsset).where(
                InputAsset.normalization_profile == "director_previs_mp4_v1"
            )
        ).all()
        assert len(normalized) == 2


def test_personal_webm_stays_local_while_mp4_video_generation_remains_available(
    app, client
):
    owner_id = _personal_user(app, "video-owner")
    owner_headers = {"X-User-ID": owner_id}
    owner_me = client.get("/api/v1/personal/me", headers=owner_headers).json()
    uploaded = _upload_personal(
        client,
        owner_headers,
        filename="previs.webm",
        content=WEBM_BYTES,
        content_type="video/webm",
        media_type="video",
        idempotency_key="personal-video-upload-0001",
    )
    assert uploaded.status_code == 201, uploaded.text
    asset = uploaded.json()
    preview = client.get(
        f"/api/v1/personal/assets/{asset['id']}/preview",
        headers=owner_headers,
    )
    assert preview.status_code == 200, preview.text
    opened = client.get(preview.json()["url"])
    assert opened.status_code == 200
    assert opened.content == WEBM_BYTES
    assert opened.headers["content-type"].startswith("video/webm")
    _personal_reference_model(app)

    other_id = _personal_user(app, "video-intruder")
    other_headers = {"X-User-ID": other_id}
    other_me = client.get("/api/v1/personal/me", headers=other_headers).json()
    _credit(client, other_me["workspace_id"])
    model = next(
        item
        for item in client.get(
            "/api/v1/personal/models", headers=other_headers
        ).json()
        if item["slug"] == "personal-reference-video-v1"
    )
    denied = client.post(
        "/api/v1/personal/tasks",
        headers=other_headers,
        json=_task_body(
            model,
            asset,
            mode="video_to_video",
            key="personal-cross-scope-task-0001",
        ),
    )
    assert denied.status_code == 404

    _credit(client, owner_me["workspace_id"])
    owner_model = next(
        item
        for item in client.get(
            "/api/v1/personal/models",
            headers=owner_headers,
        ).json()
        if item["slug"] == "personal-reference-video-v1"
    )
    rejected = client.post(
        "/api/v1/personal/tasks",
        headers=owner_headers,
        json=_task_body(
            owner_model,
            asset,
            mode="video_to_video",
            key="personal-webm-task-0001",
        ),
    )
    assert rejected.status_code == 409, rejected.text
    assert (
        rejected.json()["detail"]
        == "WebM video is local-only and cannot be attached to a "
        "provider-bound generation task"
    )
    with app.state.session_factory() as session:
        assert session.scalar(
            select(TaskInputAsset).where(TaskInputAsset.asset_id == asset["id"])
        ) is None

    mp4_upload = _upload_personal(
        client,
        owner_headers,
        filename="ordinary-reference.mp4",
        content=valid_mp4_bytes(),
        content_type="video/mp4",
        media_type="video",
        idempotency_key="personal-mp4-upload-0001",
    )
    assert mp4_upload.status_code == 201, mp4_upload.text
    mp4_asset = mp4_upload.json()
    accepted = client.post(
        "/api/v1/personal/tasks",
        headers=owner_headers,
        json=_task_body(
            owner_model,
            mp4_asset,
            mode="video_to_video",
            key="personal-mp4-task-0001",
        ),
    )
    assert accepted.status_code == 201, accepted.text
    task = accepted.json()
    with app.state.session_factory.begin() as session:
        stored = session.get(GenerationTask, task["id"])
        stored.status = TaskStatus.FAILED

    assert client.delete(
        f"/api/v1/personal/assets/{mp4_asset['id']}",
        headers=owner_headers,
    ).status_code == 204
    assert client.delete(
        f"/api/v1/personal/assets/{asset['id']}",
        headers=owner_headers,
    ).status_code == 204
