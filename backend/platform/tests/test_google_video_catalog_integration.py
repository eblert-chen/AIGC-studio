"""Google Omni/Veo draft boundaries against synthetic Relay catalog data.

These tests prove Platform catalog behavior only.  They do not assert Google
account access, paid generation, route acceptance or provider pricing.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest
from sqlalchemy import select

from platform_api.models import (
    AuditLog,
    CompanyModelGrant,
    CompanyPointPriceVersion,
    GenerationTask,
    ModelDefinition,
    PersonalRetailModelGrant,
    TaskStatus,
    utcnow,
)
from platform_api.relay_catalog_sync_worker import RelayCatalogSyncWorker
from platform_api.relay_client import (
    HttpxRelayClient,
    RelayModelCatalog,
    relay_sha256_revision,
)
from platform_api.services.errors import ConflictError
from platform_api.services.google_video_catalog import (
    DEFAULT_GOOGLE_VIDEO_MANIFEST,
    load_google_video_draft_specs,
)
from platform_api.services.models import ModelCatalogService
from platform_api.services.relay_capabilities import RelayCapabilityService
from platform_api.services.task_admission import TaskCapabilityAdmission
from scripts.prepare_google_video_drafts import prepare_draft_preview

from .test_ark_video_catalog_integration import blocked_release_evidence
from .test_model_capability_v1_contract import _admin_headers


def _video_capability(*, omni: bool) -> dict:
    durations = list(range(3, 11)) if omni else [8]
    resolutions = ["360p", "720p", "1080p", "4k"] if omni else ["720p", "1080p"]
    max_prompt_length = 4097 if omni else 1024

    def mode(*, with_image: bool) -> dict:
        return {
            "input_media_types": ["image"] if with_image else [],
            "supports_face": False,
            "required_resource_keys": [],
            "limits": {
                "max_prompt_length": max_prompt_length,
                "max_images": 2 if omni and with_image else (1 if with_image else 0),
                "max_videos": 0,
                "max_audio": 0,
                "duration_seconds": durations,
                "aspect_ratios": ["16:9", "9:16"],
                "resolutions": resolutions,
                "output_counts": [1],
            },
        }

    return TaskCapabilityAdmission.validate_catalog(
        {
            "generation": {
                "schema_version": 1,
                "modes": {
                    "text_to_video": mode(with_image=False),
                    "image_to_video": mode(with_image=True),
                },
            }
        },
        require_usable=True,
    )


def _google_catalog(*, include_unreviewed: bool = False) -> RelayModelCatalog:
    data = []
    for spec in load_google_video_draft_specs():
        capability = _video_capability(omni=spec.public_model_id.startswith("gemini-"))
        data.append(
            {
                "api_version": "v1",
                "schema_version": 1,
                "object": "model",
                "id": spec.public_model_id,
                "capability_revision": relay_sha256_revision(capability),
                "lifecycle": "published_route",
                "managed_route": True,
                "customer_callable": True,
                "published_route_revision": relay_sha256_revision(
                    {"model": spec.public_model_id, "route": "published"}
                ),
                "capabilities": capability,
            }
        )
    if include_unreviewed:
        capability = _video_capability(omni=False)
        data.append(
            {
                "api_version": "v1",
                "schema_version": 1,
                "object": "model",
                "id": "google-unreviewed-video",
                "capability_revision": relay_sha256_revision(capability),
                "lifecycle": "published_route",
                "managed_route": False,
                "customer_callable": True,
                "published_route_revision": relay_sha256_revision(
                    {"model": "google-unreviewed-video", "route": "published"}
                ),
                "capabilities": capability,
            }
        )
    data.sort(key=lambda item: item["id"])
    published_route_revision = relay_sha256_revision([])
    catalog = RelayModelCatalog.model_validate(
        {
            "api_version": "v1",
            "schema_version": 1,
            "object": "list",
            "catalog_revision_scope": "transport_snapshot",
            "published_route_revision": published_route_revision,
            "catalog_revision": relay_sha256_revision(
                {
                    "models": [
                        {
                            "id": item["id"],
                            "capability_revision": item[
                                "capability_revision"
                            ],
                            "lifecycle": item["lifecycle"],
                            "managed_route": item["managed_route"],
                            "customer_callable": item[
                                "customer_callable"
                            ],
                            "published_route_revision": item[
                                "published_route_revision"
                            ],
                        }
                        for item in data
                    ],
                    "published_route_revision": published_route_revision,
                }
            ),
            "data": data,
        }
    )
    catalog.validate_canonical_revisions()
    return catalog


@pytest.fixture
def google_relay(app):
    catalog = _google_catalog()
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET", "Google catalog fixture forbids writes"
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
        base_url="https://google-catalog-fixture.example.test",
        client_id="google-catalog-fixture",
        api_key="synthetic-not-a-google-key",
        internal_admission_token="synthetic-not-an-admission-token",
        transport=httpx.MockTransport(handle),
    )
    app.state.relay_client = relay
    try:
        yield relay, catalog, requests
    finally:
        relay.close()


def test_google_manifest_preview_has_identity_only_and_no_release_side_effects():
    raw = DEFAULT_GOOGLE_VIDEO_MANIFEST.read_bytes()
    document = json.loads(raw)
    preview = prepare_draft_preview()
    assert preview["source_manifest_sha256"] == hashlib.sha256(raw).hexdigest()
    assert preview["preview_only"] is True
    assert preview["live_relay_catalog_supplied"] is False
    for field in (
        "provider_access_verified",
        "route_readiness_verified",
        "automatic_approval",
        "automatic_publish",
        "automatic_pricing",
        "automatic_distribution",
    ):
        assert preview[field] is False
    assert [item["public_model_id"] for item in preview["models"]] == [
        "gemini-omni-1.1-flash",
        "veo-3.1",
        "veo-3.1-fast",
    ]
    assert document["capability_source"] == "relay_live_catalog"
    assert document["customer_pricing_policy"] == "admin_approved_fixed_points"
    assert {
        spec.public_model_id: spec.provider_model_ids
        for spec in load_google_video_draft_specs()
    } == {
        "gemini-omni-1.1-flash": ("gemini-omni-1.1-flash",),
        "veo-3.1": ("veo-3.1-generate-preview",),
        "veo-3.1-fast": ("veo-3.1-fast-generate-preview",),
    }
    serialized = json.dumps(document)
    assert "-001" not in serialized
    assert '"capability"' not in serialized
    assert '"capabilities"' not in serialized
    for item in preview["models"]:
        assert item["provider_key"] == "google"
        assert item["billing_mode"] == "per_second"
        assert item["customer_pricing_policy"] == "admin_approved_fixed_points"
        assert item["capability_source"] == "relay_live_catalog"


def test_google_preview_cli_is_stdout_only_and_has_no_apply_mode(tmp_path, monkeypatch):
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts/prepare_google_video_drafts.py"
    )
    monkeypatch.setenv("DATABASE_URL", "must-not-open-a-database")
    monkeypatch.setenv("RELAY_BACKENDS", "must-not-open-an-http-client")
    completed = subprocess.run(
        [sys.executable, "-B", str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == prepare_draft_preview()
    assert list(tmp_path.iterdir()) == []
    rejected = subprocess.run(
        [sys.executable, "-B", str(script), "--apply"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert rejected.returncode == 2
    assert rejected.stdout == ""
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "unsafe_field", ["capability", "price_points", "route_ready"]
)
def test_google_manifest_rejects_embedded_runtime_or_commercial_claims(
    unsafe_field, tmp_path
):
    document = json.loads(DEFAULT_GOOGLE_VIDEO_MANIFEST.read_bytes())
    document["models"][0][unsafe_field] = {} if unsafe_field == "capability" else 1
    path = tmp_path / f"unsafe-{unsafe_field}.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="entry shape"):
        load_google_video_draft_specs(path)


def test_google_manifest_rejects_vertex_ids_in_public_gemini_candidates(tmp_path):
    document = json.loads(DEFAULT_GOOGLE_VIDEO_MANIFEST.read_bytes())
    document["models"][1]["provider_model_ids"].insert(
        0, "veo-3.1-generate-001"
    )
    path = tmp_path / "vertex-merged.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="Gemini Developer API provider identity"):
        load_google_video_draft_specs(path)


def test_google_live_catalog_sync_creates_only_unreleased_unpriced_drafts(
    app, client, tenant, tenant_headers, google_relay
):
    relay, catalog, requests = google_relay
    outcome = RelayCatalogSyncWorker(app.state.session_factory, relay).run_once()
    assert outcome.reconciliation.created_count == 3
    assert outcome.reconciliation.synced_count == 3
    with app.state.session_factory() as session:
        models = session.scalars(
            select(ModelDefinition).order_by(ModelDefinition.slug)
        ).all()
        assert [model.slug for model in models] == [item.id for item in catalog.data]
        model_ids = [model.id for model in models]
        live_by_id = {item.id: item for item in catalog.data}
        names = {
            spec.public_model_id: spec.display_name
            for spec in load_google_video_draft_specs()
        }
        for model in models:
            assert model.display_name == names[model.slug]
            assert model.provider_key == "google"
            assert model.billing_mode == "per_second"
            assert model.active is False and model.published_at is None
            assert model.relay_capability_revision is None
            assert model.relay_capability_approved_ceiling is None
            live_model = live_by_id[model.slug]
            assert (
                model.relay_capability_candidate_revision
                == live_model.capability_revision
            )
            assert (
                model.relay_capability_candidate
                == live_model.capabilities.contract_dump()
            )
            assert ModelCatalogService.capabilities(
                session, model_id=model.id
            )["generation"] == live_model.capabilities.contract_dump()
        assert session.scalars(
            select(CompanyModelGrant).where(
                CompanyModelGrant.model_id.in_(model_ids)
            )
        ).all() == []
        assert session.scalars(
            select(PersonalRetailModelGrant).where(
                PersonalRetailModelGrant.model_id.in_(model_ids)
            )
        ).all() == []
        assert session.scalars(
            select(CompanyPointPriceVersion).where(
                CompanyPointPriceVersion.model_id.in_(model_ids)
            )
        ).all() == []
        assert session.scalars(
            select(AuditLog).where(
                AuditLog.action == "model.relay_capability.approve"
            )
        ).all() == []
    assert client.get(
        f"/api/v1/companies/{tenant['company_id']}/models",
        headers=tenant_headers,
    ).json() == []
    assert all(request.method == "GET" for request in requests)


def test_google_identity_allowlist_does_not_infer_provider_from_a_name(app):
    catalog = _google_catalog(include_unreviewed=True)
    with app.state.session_factory.begin() as session:
        results = RelayCapabilityService.reconcile_catalog_drafts(
            session, catalog=catalog
        )
        assert len(results) == 4
    with app.state.session_factory() as session:
        providers = {
            model.slug: model.provider_key
            for model in session.scalars(select(ModelDefinition)).all()
        }
    assert providers["google-unreviewed-video"] == "relay"
    assert {
        providers[spec.public_model_id]
        for spec in load_google_video_draft_specs()
    } == {"google"}


def test_google_provider_collision_fails_closed_before_any_candidate_sync(app):
    catalog = _google_catalog()
    first = catalog.data[0]
    with app.state.session_factory.begin() as session:
        ModelCatalogService.create_draft(
            session,
            slug=first.id,
            display_name=first.id,
            provider_key="relay",
            billing_mode="per_second",
            capabilities=[("generation", first.capabilities.contract_dump())],
        )
    with pytest.raises(ConflictError, match="供应商归属冲突"):
        with app.state.session_factory.begin() as session:
            RelayCapabilityService.reconcile_catalog_drafts(session, catalog=catalog)
    with app.state.session_factory() as session:
        models = session.scalars(select(ModelDefinition)).all()
        assert [(model.slug, model.provider_key) for model in models] == [
            (first.id, "relay")
        ]
        assert models[0].relay_capability_candidate_revision is None


def test_google_catalog_adopts_untouched_legacy_relay_created_drafts(
    app, google_relay, monkeypatch
):
    relay, catalog, _ = google_relay
    import platform_api.services.relay_capabilities as relay_capabilities

    reviewed_lookup = relay_capabilities.google_video_draft_spec
    monkeypatch.setattr(
        relay_capabilities,
        "google_video_draft_spec",
        lambda _: None,
    )
    legacy = RelayCatalogSyncWorker(app.state.session_factory, relay).run_once()
    assert legacy.reconciliation.created_count == 3

    monkeypatch.setattr(
        relay_capabilities,
        "google_video_draft_spec",
        reviewed_lookup,
    )
    adopted = RelayCatalogSyncWorker(app.state.session_factory, relay).run_once()
    assert adopted.reconciliation.created_count == 0
    assert adopted.reconciliation.synced_count == 3
    assert adopted.reconciliation.unchanged_count == 0

    expected_names = {
        spec.public_model_id: spec.display_name
        for spec in load_google_video_draft_specs()
    }
    with app.state.session_factory() as session:
        models = session.scalars(
            select(ModelDefinition).order_by(ModelDefinition.slug)
        ).all()
        assert {model.provider_key for model in models} == {"google"}
        assert all(model.capability_version == 2 for model in models)
        assert {
            model.slug: model.display_name for model in models
        } == expected_names
        assert all(model.active is False for model in models)
        assert all(model.published_at is None for model in models)
        adoptions = session.scalars(
            select(AuditLog).where(
                AuditLog.action
                == "model.relay_catalog.provider_ownership_adopt"
            )
        ).all()
        assert len(adoptions) == 3
        assert all(
            audit.before_summary["provider_key"] == "relay"
            and audit.before_summary["capability_version"] == 1
            and audit.after_summary["provider_key"] == "google"
            and audit.after_summary["capability_version"] == 2
            for audit in adoptions
        )


@pytest.mark.parametrize("protected_fact", ["published", "granted", "used"])
def test_google_catalog_never_adopts_legacy_drafts_with_customer_history(
    app, google_relay, tenant, monkeypatch, protected_fact
):
    relay, catalog, _ = google_relay
    import platform_api.services.relay_capabilities as relay_capabilities

    reviewed_lookup = relay_capabilities.google_video_draft_spec
    monkeypatch.setattr(
        relay_capabilities,
        "google_video_draft_spec",
        lambda _: None,
    )
    RelayCatalogSyncWorker(app.state.session_factory, relay).run_once()
    with app.state.session_factory.begin() as session:
        model = session.scalar(
            select(ModelDefinition).where(
                ModelDefinition.slug == catalog.data[0].id
            )
        )
        assert model is not None
        if protected_fact == "published":
            model.published_at = utcnow()
        elif protected_fact == "granted":
            session.add(
                CompanyModelGrant(
                    company_id=tenant["company_id"],
                    model_id=model.id,
                    enabled=False,
                    config_override={},
                )
            )
        else:
            session.add(
                GenerationTask(
                    company_id=tenant["company_id"],
                    user_id=tenant["user_id"],
                    model_id=model.id,
                    idempotency_key=f"legacy-google-{protected_fact}",
                    request_fingerprint="9" * 64,
                    status=TaskStatus.DRAFT,
                    request_payload={},
                    quote_cents=1,
                    pricing_snapshot={},
                    capability_snapshot={},
                    reserved_cents=0,
                )
            )

    monkeypatch.setattr(
        relay_capabilities,
        "google_video_draft_spec",
        reviewed_lookup,
    )
    with pytest.raises(ConflictError, match="供应商归属冲突"):
        RelayCatalogSyncWorker(app.state.session_factory, relay).run_once()
    with app.state.session_factory() as session:
        models = session.scalars(select(ModelDefinition)).all()
        assert {model.provider_key for model in models} == {"relay"}
        assert session.scalars(
            select(AuditLog).where(
                AuditLog.action
                == "model.relay_catalog.provider_ownership_adopt"
            )
        ).all() == []


def test_google_billing_shape_collision_fails_closed(app):
    catalog = _google_catalog()
    first = catalog.data[0]
    with app.state.session_factory.begin() as session:
        ModelCatalogService.create_draft(
            session,
            slug=first.id,
            display_name=first.id,
            provider_key="google",
            billing_mode="per_item",
            capabilities=[("generation", first.capabilities.contract_dump())],
        )
    with pytest.raises(ConflictError, match="计价方式冲突"):
        with app.state.session_factory.begin() as session:
            RelayCapabilityService.reconcile_catalog_drafts(
                session, catalog=catalog
            )
    with app.state.session_factory() as session:
        model = session.scalar(select(ModelDefinition))
        assert model.billing_mode == "per_item"
        assert model.relay_capability_candidate_revision is None


def test_google_manifest_and_live_catalog_cannot_approve_or_publish(
    app, client, google_relay
):
    relay, catalog, _ = google_relay
    RelayCatalogSyncWorker(app.state.session_factory, relay).run_once()
    headers = _admin_headers(client, "google-no-live-acceptance")
    with app.state.session_factory() as session:
        models = session.scalars(
            select(ModelDefinition).order_by(ModelDefinition.slug)
        ).all()
        model_rows = [(model.id, model.slug) for model in models]
    for model_id, slug in model_rows:
        live = next(item for item in catalog.data if item.id == slug)
        approval = client.post(
            f"/api/v1/platform-admin/models/{model_id}/relay-capability",
            headers=headers,
            json={
                "expected_capability_version": 1,
                "expected_catalog_revision": catalog.catalog_revision,
                "expected_capability_revision": live.capability_revision,
                "expected_routing_release_sha256": "sha256:" + "0" * 64,
                "reason": (
                    "Negative contract test: identity manifest is not Google "
                    "acceptance"
                ),
            },
        )
        assert approval.status_code == 409, approval.text
        assert client.post(
            f"/api/v1/platform-admin/models/{model_id}/publish", headers=headers
        ).status_code == 409
    with app.state.session_factory() as session:
        assert session.scalars(
            select(AuditLog).where(AuditLog.action == "model.relay_capability.approve")
        ).all() == []
