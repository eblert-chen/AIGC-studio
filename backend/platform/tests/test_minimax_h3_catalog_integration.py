"""MiniMax H3 contracts in isolated Platform DB / synthetic Relay transport.

These tests never claim account access, provider generation or paid acceptance.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest
from sqlalchemy import select

from platform_api.models import (
    AuditLog, CompanyModelGrant, CompanyPointPriceVersion, ModelDefinition,
    PersonalRetailModelGrant, User,
)
from platform_api.relay_catalog_sync_worker import RelayCatalogSyncWorker
from platform_api.relay_client import HttpxRelayClient
from platform_api.schemas import AdminModelCreateRequest
from platform_api.services.models import ModelCatalogService
from platform_api.services.personal import PersonalWorkspaceService
from platform_api.services.errors import ConflictError
from platform_api.services.task_admission import TaskCapabilityAdmission
from scripts.prepare_minimax_h3_drafts import DEFAULT_MANIFEST_PATH, prepare_draft_preview

from .test_ark_video_catalog_integration import (
    blocked_release_evidence,
    synthetic_catalog,
)
from .test_model_capability_v1_contract import _admin_headers


@pytest.fixture
def h3_preview():
    return prepare_draft_preview()


@pytest.fixture
def h3_relay(app, h3_preview):
    catalog = synthetic_catalog(h3_preview)
    requests = []

    def handle(request):
        requests.append(request)
        assert request.method == "GET", "No provider or operational writes in this fixture"
        if request.url.path == "/internal/platform-relay/model-release-evidence":
            return httpx.Response(200, json=blocked_release_evidence(catalog))
        assert request.url.path == "/v1/models"
        etag = f'"{catalog.catalog_revision}"'
        if request.headers.get("If-None-Match") == etag:
            return httpx.Response(304, headers={"ETag": etag})
        payload = catalog.model_dump(mode="json")
        for raw, model in zip(payload["data"], catalog.data, strict=True):
            raw["capabilities"] = model.capabilities.contract_dump()
        return httpx.Response(200, headers={"ETag": etag}, json=payload)

    relay = HttpxRelayClient(
        base_url="https://minimax-contract.example.test",
        client_id="minimax-contract-fixture",
        api_key="synthetic-not-a-provider-key",
        internal_admission_token="synthetic-not-an-admission-token",
        transport=httpx.MockTransport(handle),
    )
    app.state.relay_client = relay
    try:
        yield relay, catalog, requests
    finally:
        relay.close()


@pytest.fixture
def h3_personal_headers(app):
    with app.state.session_factory.begin() as session:
        user = User(email="minimax-contract@example.test", display_name="MiniMax Contract Fixture")
        session.add(user)
        session.flush()
        PersonalWorkspaceService.ensure(session, user_id=user.id)
        return {"X-User-ID": user.id}


def test_h3_preview_uses_one_manifest_without_releasing_prices_or_access(h3_preview):
    raw = DEFAULT_MANIFEST_PATH.read_bytes()
    expected = {entry["public_model_id"]: entry for entry in json.loads(raw)["models"]}
    assert set(expected) == {"minimax-h3", "minimax-h3-max"}
    assert h3_preview["preview_only"] is True
    assert h3_preview["source_manifest_sha256"] == hashlib.sha256(raw).hexdigest()
    for field in (
        "provider_access_verified", "route_readiness_verified", "automatic_approval",
        "automatic_publish", "automatic_pricing", "automatic_distribution",
    ):
        assert h3_preview[field] is False
    for item in h3_preview["models"]:
        entry = expected[item["public_model_id"]]
        request = AdminModelCreateRequest.model_validate(item["admin_create_request"])
        assert request.slug == entry["public_model_id"]
        assert request.display_name == entry["display_name"]
        assert request.provider_key == "relay"
        assert request.billing_mode == "per_second"
        assert request.capabilities[0].config == entry["capability"]
        assert item["provider_model_id"] == entry["provider_model_id"]
        assert item["route_policy"] == "acceptance_required"
        assert "账号已开通" in item["warning"]


def test_h3_compatible_mode_limits_preserve_768p_and_2k(h3_preview):
    models = {item["public_model_id"]: item["admin_create_request"]["capabilities"][0]["config"]
              for item in h3_preview["models"]}
    h3_modes = models["minimax-h3"]["modes"]
    assert set(h3_modes) == {"text_to_video", "image_to_video", "video_to_video"}
    assert set(models["minimax-h3-max"]["modes"]) == {"text_to_video", "image_to_video"}
    counts = {"text_to_video": (0, 0, 0), "image_to_video": (9, 0, 3), "video_to_video": (6, 3, 3)}
    for mode, capability in h3_modes.items():
        limits = capability["limits"]
        assert tuple(limits[key] for key in ("max_images", "max_videos", "max_audio")) == counts[mode]
        assert sum(counts[mode]) <= 12
        assert limits["max_prompt_length"] == 7000
        assert limits["duration_seconds"] == list(range(4, 16))
        assert set(limits["resolutions"]) == {"768p", "2k"}
        assert "720p" not in limits["resolutions"]
        assert limits["output_counts"] == [1]
        assert capability["supports_face"] is False
    maximum = models["minimax-h3-max"]["modes"]["text_to_video"]
    assert maximum["input_media_types"] == []
    assert maximum["limits"]["duration_seconds"] == list(range(5, 16))
    assert set(maximum["limits"]["resolutions"]) == {"480p", "768p"}
    maximum_frames = models["minimax-h3-max"]["modes"]["image_to_video"]
    assert maximum_frames["input_media_types"] == ["image"]
    assert maximum_frames["input_roles"] == ["first_frame", "last_frame"]
    assert maximum_frames["temporal_controls"] == ["first_frame", "last_frame"]
    assert maximum_frames["structured_inputs"] == []
    assert maximum_frames["limits"] == {**maximum["limits"], "max_images": 2}


def test_h3_platform_task_admission_accepts_exact_model_modes_and_unicode_prompt(h3_preview):
    for entry in h3_preview["models"]:
        capability = entry["admin_create_request"]["capabilities"][0]["config"]
        for mode, mode_capability in capability["modes"].items():
            limits = mode_capability["limits"]
            payload = {
                "mode": mode,
                "prompt": "中" * 6999 + "🎬",
                "duration_seconds": limits["duration_seconds"][-1],
                "aspect_ratio": "16:9",
                "resolution": limits["resolutions"][-1],
                "output_count": 1,
                "face_enabled": False,
                "assets": [
                    {"asset_id": f"{kind}-fixture-{index}", "media_type": kind}
                    for kind, key in (("image", "max_images"), ("video", "max_videos"), ("audio", "max_audio"))
                    for index in range(limits[key])
                ],
            }
            admitted = TaskCapabilityAdmission.validate(
                capability_map={"generation": capability}, config_override={}, request_payload=payload,
            )
            assert set(admitted["modes"]) == {mode}
            assert admitted["modes"][mode]["limits"]["max_prompt_length"] == 7000


@pytest.mark.parametrize(("slug", "changes"), [
    ("minimax-h3", {"duration_seconds": 30}),
    ("minimax-h3", {"resolution": "4k"}),
    ("minimax-h3", {"resolution": "720p"}),
    ("minimax-h3", {"prompt": "🎬" * 7001}),
    ("minimax-h3", {"mode": "image_to_video", "assets": [{"media_type": "image"}, {"media_type": "video"}]}),
    ("minimax-h3", {"mode": "video_to_video", "assets": [{"media_type": "image"}] * 7 + [{"media_type": "video"}]}),
    ("minimax-h3-max", {"duration_seconds": 4}),
    ("minimax-h3-max", {"resolution": "2k"}),
    ("minimax-h3-max", {"mode": "video_to_video", "assets": [{"media_type": "video"}]}),
    ("minimax-h3-max", {"mode": "image_to_video", "assets": [{"media_type": "image", "role": "reference_image"}]}),
    ("minimax-h3-max", {"mode": "image_to_video", "assets": [{"media_type": "image"}] * 3}),
    ("minimax-h3-max", {"assets": [{"media_type": "audio"}]}),
], ids=["ark-30s", "ark-4k", "no-720p-alias", "unicode-over-limit", "i2v-video", "v2v-images", "max-4s", "max-2k", "max-v2v", "max-reference-image", "max-three-frames", "max-audio"])
def test_h3_platform_task_admission_rejects_stale_cross_model_values(h3_preview, slug, changes):
    entry = next(model for model in h3_preview["models"] if model["public_model_id"] == slug)
    payload = {"mode": "text_to_video", "prompt": "真实任务边界验证。", "duration_seconds": 5,
               "aspect_ratio": "16:9", "resolution": "768p", "output_count": 1, **changes}
    with pytest.raises(ConflictError):
        TaskCapabilityAdmission.validate(capability_map={"generation": entry["admin_create_request"]["capabilities"][0]["config"]},
                                         config_override={}, request_payload=payload)


def test_h3_max_admission_preserves_explicit_first_and_last_frame_roles(h3_preview):
    entry = next(item for item in h3_preview["models"] if item["public_model_id"] == "minimax-h3-max")
    admitted = TaskCapabilityAdmission.validate(
        capability_map={"generation": entry["admin_create_request"]["capabilities"][0]["config"]},
        config_override={},
        request_payload={
            "mode": "image_to_video", "prompt": "受控首尾帧镜头", "duration_seconds": 5,
            "aspect_ratio": "16:9", "resolution": "768p", "output_count": 1,
            "assets": [
                {"asset_id": "first-fixture", "media_type": "image", "role": "first_frame"},
                {"asset_id": "last-fixture", "media_type": "image", "role": "last_frame"},
            ],
        },
    )
    assert admitted["modes"]["image_to_video"]["input_roles"] == ["first_frame", "last_frame"]
    assert admitted["modes"]["image_to_video"]["structured_inputs"] == []


def test_h3_cli_is_stdout_only_and_has_no_apply_mode(h3_preview, tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts/prepare_minimax_h3_drafts.py"
    # Invalid runtime values make an accidental settings/DB dependency visible.
    monkeypatch.setenv("DATABASE_URL", "must-not-open-a-database")
    monkeypatch.setenv("RELAY_BACKENDS", "must-not-open-an-http-client")
    completed = subprocess.run([sys.executable, "-B", str(script)], cwd=tmp_path,
                               capture_output=True, text=True, encoding="utf-8", check=False)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == h3_preview
    assert list(tmp_path.iterdir()) == []
    rejected = subprocess.run([sys.executable, "-B", str(script), "--apply"], cwd=tmp_path,
                              capture_output=True, text=True, encoding="utf-8", check=False)
    assert rejected.returncode == 2
    assert rejected.stdout == ""
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("invalid", ["duplicate", "unreviewed", "multiple_outputs"])
def test_h3_preview_rejects_unsafe_manifest_candidates(invalid, tmp_path):
    document = json.loads(DEFAULT_MANIFEST_PATH.read_bytes())
    if invalid == "duplicate":
        document["models"].append(deepcopy(document["models"][0]))
    elif invalid == "unreviewed":
        document["models"][0]["evidence_status"] = "ready"
    else:
        document["models"][0]["capability"]["modes"]["text_to_video"]["limits"]["output_counts"] = [1, 2]
    path = tmp_path / "unsafe-manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError):
        prepare_draft_preview(path)


def test_h3_reviewed_candidates_stay_visible_without_materialization(
    app, client, tenant, tenant_headers, h3_personal_headers, h3_relay,
):
    relay, catalog, requests = h3_relay
    worker = RelayCatalogSyncWorker(app.state.session_factory, relay)
    first = worker.run_once()
    assert first.reconciliation.created_count == 0
    assert first.reconciliation.synced_count == 0
    assert first.reconciliation.unchanged_count == 0
    with app.state.session_factory() as session:
        audit_ids = set(session.scalars(select(AuditLog.id)))
    assert worker.run_once().not_modified is True
    restarted = RelayCatalogSyncWorker(app.state.session_factory, relay).run_once()
    assert restarted.reconciliation.created_count == 0
    assert restarted.reconciliation.synced_count == 0
    assert restarted.reconciliation.unchanged_count == 0
    catalog_requests = [
        request for request in requests if request.url.path == "/v1/models"
    ]
    assert [
        request.headers.get("If-None-Match") for request in catalog_requests
    ] == [None, f'"{catalog.catalog_revision}"', None]
    assert sum(
        request.url.path == "/internal/platform-relay/model-release-evidence"
        for request in requests
    ) == 3
    with app.state.session_factory() as session:
        models = session.scalars(select(ModelDefinition)).all()
        assert models == []
        for table in (CompanyModelGrant, PersonalRetailModelGrant, CompanyPointPriceVersion):
            assert session.scalars(select(table)).all() == []
        audits = session.scalars(select(AuditLog)).all()
        assert {audit.id for audit in audits} == audit_ids
        assert len(audits) == 1
        assert all(audit.actor_kind == "system" and audit.actor_key == "relay-catalog-sync" for audit in audits)
    assert client.get(f"/api/v1/companies/{tenant['company_id']}/models", headers=tenant_headers).json() == []
    assert client.get("/api/v1/personal/models", headers=h3_personal_headers).json() == []


def test_h3_manifest_cannot_substitute_for_live_acceptance_approval_or_publish(app, client, h3_relay):
    relay, catalog, _ = h3_relay
    headers = _admin_headers(client, "minimax-no-live-acceptance")
    RelayCatalogSyncWorker(app.state.session_factory, relay).run_once()
    with app.state.session_factory() as session:
        models = [(model.id, model.slug) for model in session.scalars(select(ModelDefinition))]
    for model_id, slug in models:
        item = next(item for item in catalog.data if item.id == slug)
        response = client.post(f"/api/v1/platform-admin/models/{model_id}/relay-capability", headers=headers, json={
            "expected_capability_version": 1,
            "expected_catalog_revision": catalog.catalog_revision,
            "expected_capability_revision": item.capability_revision,
            "expected_routing_release_sha256": "sha256:" + "0" * 64,
            "reason": "Negative contract test: MiniMax manifest is not provider acceptance",
        })
        assert response.status_code == 503, response.text
        assert client.post(f"/api/v1/platform-admin/models/{model_id}/publish", headers=headers).status_code == 409
    with app.state.session_factory() as session:
        assert session.scalars(select(AuditLog).where(AuditLog.action == "model.relay_capability.approve")).all() == []


def test_previously_released_h3_discovery_keeps_company_and_personal_boundaries(
    app, client, tenant, tenant_headers, h3_personal_headers, h3_preview, h3_relay,
):
    # Isolated fixture only: this is not the acceptance/approval implementation.
    _, catalog, _ = h3_relay
    with app.state.session_factory.begin() as session:
        for item in h3_preview["models"]:
            request = item["admin_create_request"]
            source = next(model for model in catalog.data if model.id == request["slug"])
            capability = source.capabilities.contract_dump()
            model = ModelCatalogService.create_model(session, slug=request["slug"],
                display_name=request["display_name"], provider_key="relay", billing_mode="per_second",
                capability_version=1, capabilities=[("generation", capability)])
            model.relay_capability_revision = source.capability_revision
            model.relay_capability_candidate_revision = source.capability_revision
            model.relay_capability_candidate = deepcopy(capability)
            model.relay_capability_approved_ceiling = deepcopy(capability)
            model.relay_capability_candidate_catalog_revision = catalog.catalog_revision
            model.relay_capability_approved_catalog_revision = catalog.catalog_revision
            session.add(CompanyModelGrant(company_id=tenant["company_id"], model_id=model.id,
                enabled=True, price_per_second_cents=125, config_override={}))
            session.add(PersonalRetailModelGrant(model_id=model.id,
                enabled=True, price_per_second_points=3, config_override={}))
    company = client.get(f"/api/v1/companies/{tenant['company_id']}/models", headers=tenant_headers)
    personal = client.get("/api/v1/personal/models", headers=h3_personal_headers)
    assert company.status_code == personal.status_code == 200
    company_models = {model["slug"]: model for model in company.json()}
    personal_models = {model["slug"]: model for model in personal.json()}
    assert set(company_models) == set(personal_models) == {item.id for item in catalog.data}
    for source in catalog.data:
        expected = source.capabilities.contract_dump()
        for mode in expected["modes"].values():
            # Platform's effective schema-v3 wire format makes the empty
            # conditional resource constraint explicit; no capability expands.
            mode["conditional_required_resource_keys"] = {}
        assert company_models[source.id]["effective_capabilities"] == expected
        assert company_models[source.id]["unit_price_cents"] == 125
        assert personal_models[source.id]["unit_price_points"] == 3
        personal_modes = personal_models[source.id]["effective_capabilities"]["modes"]
        assert personal_models[source.id]["effective_capabilities"] == expected
        assert personal_modes["text_to_video"]["input_media_types"] == []
        assert all(mode["supports_face"] is False for mode in personal_modes.values())
        assert all(mode["required_resource_keys"] == [] for mode in personal_modes.values())
