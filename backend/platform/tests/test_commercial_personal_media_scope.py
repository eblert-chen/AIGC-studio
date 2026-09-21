"""Real commercial guard: personal media must not disappear from cost checks."""
from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import func, select

from platform_api.models import (
    Company,
    GenerationTask,
    ModelCommercialReleasePlan,
    PersonalRetailModelGrant,
    PersonalWalletAccount,
    RelaySubmissionOutbox,
)
from platform_api.services.commercial_pricing import (
    CommercialPricingPolicy,
    PERSONAL_MEDIA_POLICY_EXPLICIT_IMAGE_V1,
)

from .test_commercial_pricing_policy import _company_payload, _personal_payload
from .test_model_commercial_release import _admin_headers, _commercial_body, _mode, canonical_capability
from .test_personal_input_assets import _credit, _task_body, _upload_personal
from .test_personal_workspace import _personal_user
from .test_input_assets import PNG_BYTES
from .test_relay_capability_sync import CatalogRelayClient, _catalog


def test_explicit_personal_image_policy_allows_declared_image_to_image_only():
    text_mode = _mode(
        max_images=0,
        max_videos=0,
        max_audio=0,
        supports_face=False,
        durations=[1],
        resolutions=["2k"],
        output_counts=[1],
        input_media_types=[],
    )
    image_mode = deepcopy(text_mode)
    image_mode["input_media_types"] = ["image"]
    image_mode["limits"]["max_images"] = 2
    capability = canonical_capability(
        modes={"text_to_image": text_mode, "image_to_image": image_mode}
    )
    formula = {
        "assumptions": {
            "personal_media_policy": PERSONAL_MEDIA_POLICY_EXPLICIT_IMAGE_V1
        }
    }

    projected = CommercialPricingPolicy.project_personal_capabilities(
        capability_map={"generation": capability},
        config_override=capability,
        provider_cost_formula=formula,
    )

    assert set(projected["modes"]) == {"text_to_image", "image_to_image"}
    assert projected["modes"]["image_to_image"]["limits"]["max_images"] == 2

    legacy_projection = CommercialPricingPolicy.project_personal_capabilities(
        capability_map={"generation": capability},
        config_override={},
        provider_cost_formula={"assumptions": {}},
    )
    assert set(legacy_projection["modes"]) == {"text_to_image"}


def _mixed_release(app, client):
    assert not getattr(app.state, "legacy_commercial_gate_isolated", False)
    headers = _admin_headers(client, "priced-personal-media-scope")
    text_mode = _mode(max_images=0, max_videos=0, max_audio=0, supports_face=False,
                      durations=[5], resolutions=["720p", "2k"], output_counts=[1], input_media_types=[])
    image_mode = deepcopy(text_mode)
    image_mode["input_media_types"] = ["image"]
    image_mode["limits"]["max_images"] = 1
    capability = canonical_capability(modes={"text_to_video": text_mode, "image_to_video": image_mode})
    text_scope = canonical_capability(modes={"text_to_video": text_mode})
    image_scope = canonical_capability(modes={"image_to_video": image_mode})
    app.state.relay_client = CatalogRelayClient(
        _catalog("commercial-personal-media-scope", capability),
        provider_cost_billing_unit="output_second", provider_cost_source_sha256="1" * 64,
    )
    discovered = client.post("/api/v1/platform-admin/relay-models/reconcile", headers=headers)
    assert discovered.status_code == 200, discovered.text
    model_id = discovered.json()["created_model_ids"][0]
    model = client.get(f"/api/v1/platform-admin/models/{model_id}", headers=headers).json()
    with app.state.session_factory.begin() as session:
        company = Company(name="Company media price scope", billing_version=2)
        session.add(company)
        session.flush()
        company_id = company.id
    body = _commercial_body(model=model, assumptions={
        "quantity_basis": "relay_effective_capability_ceiling",
        "enforced_limits": {"max_resolution": "2k", "max_reference_images": 1, "included_reference_images": 1},
    })
    # The historical commercial personal contract approves text only. Company
    # media has its own explicit cost assumptions and must remain usable.
    body["personal_config_override"] = text_scope
    approved = client.put(f"/api/v1/platform-admin/models/{model_id}/commercial-release-plan", headers=headers, json=body)
    assert approved.status_code == 200, approved.text
    released = client.post("/api/v1/platform-admin/model-commercial-releases/reconcile", headers=headers)
    assert released.status_code == 200 and released.json()["released_count"] == 1, released.text
    return headers, model, company_id, text_scope, image_scope


def _explicit_image_release(app, client):
    headers = _admin_headers(client, "priced-personal-explicit-image")
    text_mode = _mode(
        max_images=0,
        max_videos=0,
        max_audio=0,
        supports_face=False,
        durations=[5],
        resolutions=["720p", "2k"],
        output_counts=[1],
        input_media_types=[],
    )
    image_mode = deepcopy(text_mode)
    image_mode["input_media_types"] = ["image"]
    image_mode["limits"]["max_images"] = 2
    capability = canonical_capability(
        modes={"text_to_video": text_mode, "image_to_video": image_mode}
    )
    app.state.relay_client = CatalogRelayClient(
        _catalog("commercial-personal-explicit-image", capability),
        provider_cost_billing_unit="output_second",
        provider_cost_source_sha256="1" * 64,
    )
    discovered = client.post(
        "/api/v1/platform-admin/relay-models/reconcile", headers=headers
    )
    assert discovered.status_code == 200, discovered.text
    model_id = discovered.json()["created_model_ids"][0]
    model = client.get(
        f"/api/v1/platform-admin/models/{model_id}", headers=headers
    ).json()
    personal_scope = canonical_capability(
        modes={"text_to_video": text_mode, "image_to_video": image_mode}
    )
    body = _commercial_body(
        model=model,
        assumptions={
            "quantity_basis": "relay_effective_capability_ceiling",
            "personal_media_policy": PERSONAL_MEDIA_POLICY_EXPLICIT_IMAGE_V1,
            "enforced_limits": {
                "max_resolution": "2k",
                "max_reference_images": 2,
                "included_reference_images": 2,
            },
        },
    )
    body["personal_config_override"] = personal_scope
    body["enterprise_config_override"] = personal_scope
    approved = client.put(
        f"/api/v1/platform-admin/models/{model_id}/commercial-release-plan",
        headers=headers,
        json=body,
    )
    assert approved.status_code == 200, approved.text
    assert (
        approved.json()["provider_cost_formula"]["assumptions"][
            "personal_media_policy"
        ]
        == PERSONAL_MEDIA_POLICY_EXPLICIT_IMAGE_V1
    )
    released = client.post(
        "/api/v1/platform-admin/model-commercial-releases/reconcile",
        headers=headers,
    )
    assert released.status_code == 200, released.text
    assert released.json()["released_count"] == 1
    return headers, model, personal_scope


@pytest.mark.parametrize("scope", ["text", "image", "mixed"])
def test_personal_requested_media_is_rejected_not_silently_removed(app, client, scope):
    headers, model, _, text_scope, image_scope = _mixed_release(app, client)
    body = _personal_payload(client, headers, model, 20)
    body["config_override"] = text_scope if scope == "text" else image_scope if scope == "image" else {}
    response = client.put(f"/api/v1/platform-admin/personal-model-grants/{model['id']}", headers=headers, json=body)
    assert response.status_code == (200 if scope == "text" else 409), response.text
    if scope != "text":
        assert "尚未核价" in response.json()["detail"]
    with app.state.session_factory() as session:
        grant = session.scalar(select(PersonalRetailModelGrant).where(PersonalRetailModelGrant.model_id == model["id"]))
        assert grant.config_override == text_scope


def test_company_priced_media_scope_remains_available(app, client):
    headers, model, company_id, _, image_scope = _mixed_release(app, client)
    body = _company_payload(app, model, company_id, 20)
    body["config_override"] = image_scope
    response = client.put(f"/api/v1/platform-admin/companies/{company_id}/model-grants", headers=headers, json=body)
    assert response.status_code == 200, response.text


def test_historical_mixed_personal_grant_cannot_create_an_unpriced_media_task(app, client):
    _, model, _, _, _ = _mixed_release(app, client)
    with app.state.session_factory.begin() as session:
        # Reproduce the pre-fix grant shape without rewriting its immutable
        # commercial plan or pretending it received a new media approval.
        grant = session.scalar(select(PersonalRetailModelGrant).where(PersonalRetailModelGrant.model_id == model["id"]))
        grant.config_override = {}
    user_id = _personal_user(app, "real-price-media-rejection")
    headers = {"X-User-ID": user_id}
    workspace_id = client.get("/api/v1/personal/me", headers=headers).json()["workspace_id"]
    _credit(client, workspace_id)
    uploaded = _upload_personal(client, headers, filename="scope.png", content=PNG_BYTES,
                                content_type="image/png", media_type="image", idempotency_key="price-media-asset-001")
    assert uploaded.status_code == 201, uploaded.text
    customer_model = client.get("/api/v1/personal/models", headers=headers).json()[0]
    assert "image_to_video" in customer_model["effective_capabilities"]["modes"]
    response = client.post("/api/v1/personal/tasks", headers=headers,
                           json=_task_body(customer_model, uploaded.json(), mode="image_to_video", key="unpriced-media-task-001"))
    assert response.status_code == 409 and "尚未核价" in response.json()["detail"], response.text
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(GenerationTask.id))) == 0
        assert session.scalar(select(func.count(RelaySubmissionOutbox.id))) == 0
        wallet = session.get(PersonalWalletAccount, workspace_id)
        assert wallet.available_points == 100 and wallet.reserved_points == 0


def test_explicit_image_policy_releases_personal_i2v_with_true_image_limit(
    app, client
):
    _, _, personal_scope = _explicit_image_release(app, client)
    user_id = _personal_user(app, "explicit-image-commercial")
    headers = {"X-User-ID": user_id}
    workspace_id = client.get("/api/v1/personal/me", headers=headers).json()[
        "workspace_id"
    ]
    _credit(client, workspace_id)
    uploaded = _upload_personal(
        client,
        headers,
        filename="director-static.png",
        content=PNG_BYTES,
        content_type="image/png",
        media_type="image",
        idempotency_key="explicit-image-asset-001",
    )
    assert uploaded.status_code == 201, uploaded.text
    model = client.get("/api/v1/personal/models", headers=headers).json()[0]
    assert model["effective_capabilities"]["modes"]["image_to_video"][
        "limits"
    ]["max_images"] == 2
    assert personal_scope["modes"]["image_to_video"]["limits"][
        "max_images"
    ] == 2

    created = client.post(
        "/api/v1/personal/tasks",
        headers=headers,
        json=_task_body(
            model,
            uploaded.json(),
            mode="image_to_video",
            key="explicit-image-task-001",
        ),
    )
    assert created.status_code == 201, created.text
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(GenerationTask.id))) == 1
        assert session.scalar(select(func.count(RelaySubmissionOutbox.id))) == 1


@pytest.mark.parametrize("unsafe_kind", ["missing_override", "video", "audio"])
def test_explicit_image_policy_rejects_implicit_video_or_audio_scope(
    app, client, unsafe_kind
):
    headers = _admin_headers(client, "unsafe-personal-" + unsafe_kind)
    unsafe_slug = unsafe_kind.replace("_", "-")
    text_mode = _mode(
        max_images=0,
        max_videos=0,
        max_audio=0,
        supports_face=False,
        durations=[5],
        resolutions=["720p", "2k"],
        output_counts=[1],
        input_media_types=[],
    )
    media_mode = deepcopy(text_mode)
    if unsafe_kind == "video":
        media_mode["input_media_types"] = ["video"]
        media_mode["limits"]["max_videos"] = 1
        media_name = "video_to_video"
    else:
        media_mode["input_media_types"] = ["audio", "image"]
        media_mode["limits"]["max_audio"] = 1
        media_mode["limits"]["max_images"] = 1
        media_name = "image_to_video"
    capability = canonical_capability(
        modes={"text_to_video": text_mode, media_name: media_mode}
    )
    app.state.relay_client = CatalogRelayClient(
        _catalog("unsafe-personal-" + unsafe_slug, capability),
        provider_cost_billing_unit="output_second",
        provider_cost_source_sha256="1" * 64,
    )
    discovered = client.post(
        "/api/v1/platform-admin/relay-models/reconcile", headers=headers
    )
    assert discovered.status_code == 200, discovered.text
    model_id = discovered.json()["created_model_ids"][0]
    model = client.get(
        f"/api/v1/platform-admin/models/{model_id}", headers=headers
    ).json()
    body = _commercial_body(
        model=model,
        assumptions={
            "quantity_basis": "relay_effective_capability_ceiling",
            "personal_media_policy": PERSONAL_MEDIA_POLICY_EXPLICIT_IMAGE_V1,
            "enforced_limits": {
                "max_resolution": "2k",
                "max_reference_images": 1,
                "included_reference_images": 1,
            },
        },
    )
    if unsafe_kind != "missing_override":
        body["personal_config_override"] = capability
    rejected = client.put(
        f"/api/v1/platform-admin/models/{model_id}/commercial-release-plan",
        headers=headers,
        json=body,
    )
    assert rejected.status_code == 409, rejected.text
    assert (
        "显式个人能力范围" in rejected.json()["detail"]
        if unsafe_kind == "missing_override"
        else "视频、音频" in rejected.json()["detail"]
        if unsafe_kind == "audio"
        else "仅允许" in rejected.json()["detail"]
    )
    with app.state.session_factory() as session:
        assert session.scalar(select(ModelCommercialReleasePlan.id)) is None


def test_price_revision_does_not_expand_existing_personal_grant(app, client):
    headers, model, _, text_scope, image_scope = _mixed_release(app, client)
    with app.state.session_factory() as session:
        predecessor = session.scalar(select(ModelCommercialReleasePlan))
        assert predecessor is not None
        predecessor_id = predecessor.id

    expanded_scope = canonical_capability(
        modes={
            "text_to_video": text_scope["modes"]["text_to_video"],
            "image_to_video": image_scope["modes"]["image_to_video"],
        }
    )
    body = _commercial_body(
        model=model,
        assumptions={
            "quantity_basis": "relay_effective_capability_ceiling",
            "personal_media_policy": PERSONAL_MEDIA_POLICY_EXPLICIT_IMAGE_V1,
            "enforced_limits": {
                "max_resolution": "2k",
                "max_reference_images": 1,
                "included_reference_images": 1,
            },
        },
    )
    body["supersedes_plan_id"] = predecessor_id
    body["idempotency_key"] += ":explicit-image-revision"
    body["personal_config_override"] = expanded_scope
    body["enterprise_config_override"] = expanded_scope
    approved = client.put(
        f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan",
        headers=headers,
        json=body,
    )
    assert approved.status_code == 200, approved.text
    released = client.post(
        "/api/v1/platform-admin/model-commercial-releases/reconcile",
        headers=headers,
    )
    assert released.status_code == 200, released.text
    assert released.json()["released_count"] == 1
    with app.state.session_factory() as session:
        grant = session.scalar(
            select(PersonalRetailModelGrant).where(
                PersonalRetailModelGrant.model_id == model["id"]
            )
        )
        assert grant is not None
        assert grant.config_override == text_scope
