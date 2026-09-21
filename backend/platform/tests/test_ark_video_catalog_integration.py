"""Ark catalog integration against in-memory Platform and synthetic Relay HTTP.

These tests prove code contracts, not provider access or route acceptance. The
transport never produces ready route evidence and rejects all provider writes.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest
from sqlalchemy import select

from platform_api.models import (
    AuditLog,
    Company,
    CompanyModelGrant,
    CompanyPointPriceVersion,
    ModelDefinition,
    PersonalRetailModelGrant,
    User,
)
from platform_api.relay_catalog_sync_worker import RelayCatalogSyncWorker
from platform_api.relay_client import (
    HttpxRelayClient,
    RelayGenerationCapabilities,
    RelayModelCatalog,
    relay_sha256_revision,
)
from platform_api.schemas import AdminModelCreateRequest
from platform_api.services.models import ModelCatalogService
from platform_api.services.personal import PersonalWorkspaceService
from platform_api.services.task_admission import TaskCapabilityAdmission
from scripts.prepare_ark_video_drafts import (
    DEFAULT_MANIFEST_PATH,
    prepare_draft_preview,
)

from .test_model_capability_v1_contract import _admin_headers


REVIEW_AS_OF = datetime(2026, 8, 31, tzinfo=timezone.utc)


@pytest.fixture
def ark_preview():
    return prepare_draft_preview(as_of=REVIEW_AS_OF)


def synthetic_catalog(preview: dict) -> RelayModelCatalog:
    """Build correctly hashed wire data, without asserting a physical route."""

    data = []
    for entry in preview["models"]:
        request = entry["admin_create_request"]
        capability = TaskCapabilityAdmission.validate_catalog(
            {"generation": request["capabilities"][0]["config"]},
            require_usable=True,
        )
        relay_capability = RelayGenerationCapabilities.model_validate(capability)
        wire_capability = relay_capability.contract_dump()
        data.append(
            {
                "api_version": "v1",
                "schema_version": 1,
                "object": "model",
                "id": request["slug"],
                "capability_revision": relay_sha256_revision(wire_capability),
                "lifecycle": "reviewed_candidate",
                "managed_route": False,
                "customer_callable": False,
                "published_route_revision": "",
                "capabilities": wire_capability,
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


def blocked_release_evidence(catalog: RelayModelCatalog) -> dict:
    """Return a paired projection which truthfully marks every route unready."""

    return {
        "schema_version": 1,
        "object": "relay.model_release_evidence",
        "catalog_revision": catalog.catalog_revision,
        "catalog_revision_scope": catalog.catalog_revision_scope,
        "published_route_revision": catalog.published_route_revision,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "test_freshness_max_age_seconds": 900,
        "models": [
            {
                "public_model_id": item.id,
                "capability_revision": item.capability_revision,
                "published_route_revision": (
                    item.published_route_revision or None
                ),
                "routing_release_sha256": relay_sha256_revision(
                    {"model": item.id, "routes": []}
                ),
                "provider_cost_readiness_sha256": relay_sha256_revision(
                    {"model": item.id, "costs": []}
                ),
                "provider_cost_ready": False,
                "provider_cost_rectangle_count": 0,
                "provider_cost_ready_rectangle_count": 0,
                "route_count": 0,
                "enabled_route_count": 0,
                "accepted_route_count": 0,
                "fresh_test_count": 0,
                "latest_successful_test_at": None,
                "status": "blocked",
                "routes": [],
            }
            for item in catalog.data
        ],
    }


@pytest.fixture
def ark_relay(app, ark_preview):
    catalog = synthetic_catalog(ark_preview)
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET", "The catalog fixture forbids side effects"
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
        base_url="https://ark-catalog-fixture.example.test",
        client_id="platform-catalog-test",
        api_key="synthetic-test-only-key",
        internal_admission_token="synthetic-test-only-admission",
        transport=httpx.MockTransport(handle),
    )
    app.state.relay_client = relay
    try:
        yield relay, catalog, requests
    finally:
        relay.close()


@pytest.fixture
def ark_personal_headers(app):
    with app.state.session_factory.begin() as session:
        user = User(
            email="ark-catalog-personal@example.test",
            display_name="Ark Catalog Personal Test",
        )
        session.add(user)
        session.flush()
        PersonalWorkspaceService.ensure(session, user_id=user.id)
        return {"X-User-ID": user.id}


def test_ark_draft_preview_is_single_source_and_never_a_live_catalog(ark_preview):
    document = json.loads(DEFAULT_MANIFEST_PATH.read_bytes())
    expected = {
        entry["public_model_id"]: entry
        for entry in document["models"]
        if entry["lifecycle"] in {"acceptance_candidate", "deprecated"}
    }
    assert len(expected) == 7
    assert len(ark_preview["models"]) == 7
    assert ark_preview["preview_only"] is True
    assert ark_preview["as_of"] == "2026-08-31T00:00:00Z"
    assert ark_preview["excluded"] == []
    for field in (
        "route_readiness_verified",
        "automatic_approval",
        "automatic_publish",
        "automatic_pricing",
        "automatic_distribution",
    ):
        assert ark_preview[field] is False
    existing_only = []
    for item in ark_preview["models"]:
        entry = expected[item["public_model_id"]]
        request = AdminModelCreateRequest.model_validate(item["admin_create_request"])
        assert request.slug == entry["public_model_id"]
        assert request.display_name == entry["display_name"]
        assert request.provider_key == "relay"
        assert request.billing_mode == "per_second"
        assert request.capabilities[0].config == entry["capability"]
        assert item["provider_model_id"] == entry["provider_model_id"]
        assert item["new_routes_allowed"] is entry["new_routes_allowed"]
        if not item["new_routes_allowed"]:
            existing_only.append(item)
            assert item["lifecycle"] == "deprecated"
            assert item["route_policy"] == "existing_routes_only"
            assert "不得默认新建路由" in item["warning"]
    assert len(existing_only) == 1
    assert sum(item["new_routes_allowed"] for item in ark_preview["models"]) == 6


def test_ark_preview_cli_prints_requests_without_an_apply_mode(ark_preview, tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/prepare_ark_video_drafts.py"
    completed = subprocess.run(
        [sys.executable, "-B", str(script), "--as-of", ark_preview["as_of"]],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == ark_preview
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
    ("offset", "visible"),
    [
        (timedelta(microseconds=-1), True),
        (timedelta(0), False),
        (timedelta(microseconds=1), False),
    ],
    ids=["before-eos", "at-eos", "after-eos"],
)
def test_ark_preview_archives_existing_only_models_at_the_exact_eos(offset, visible):
    document = json.loads(DEFAULT_MANIFEST_PATH.read_bytes())
    existing = next(
        entry for entry in document["models"] if entry["lifecycle"] == "deprecated"
    )
    eos = datetime.strptime(existing["eos_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    preview = prepare_draft_preview(as_of=eos + offset)
    current = {entry["public_model_id"]: entry for entry in preview["models"]}
    archived = {entry["public_model_id"]: entry for entry in preview["excluded"]}
    assert (existing["public_model_id"] in current) is visible
    assert len(current) == (7 if visible else 6)
    if visible:
        assert existing["public_model_id"] not in archived
    else:
        record = archived[existing["public_model_id"]]
        assert record["archive_only"] is True
        assert record["excluded_reason"] == "end_of_service"
        assert record["new_routes_allowed"] is False
        assert record["route_policy"] == "archive_only"
        assert record["eos_at"] == existing["eos_at"]
        assert "admin_create_request" not in record
        assert "capability" not in record


def test_ark_preview_rejects_naive_review_time():
    with pytest.raises(ValueError, match="timezone-aware"):
        prepare_draft_preview(as_of=datetime(2026, 8, 31))


def test_ark_preview_cli_eos_and_canonical_review_time_are_read_only(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/prepare_ark_video_drafts.py"
    completed = subprocess.run(
        [sys.executable, "-B", str(script), "--as-of", "2026-09-21T06:00:00Z"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    preview = json.loads(completed.stdout)
    assert preview["as_of"] == "2026-09-21T06:00:00Z"
    assert len(preview["models"]) == 6
    assert len(preview["excluded"]) == 1
    assert "admin_create_request" not in preview["excluded"][0]
    rejected = subprocess.run(
        [
            sys.executable,
            "-B",
            str(script),
            "--as-of",
            "2026-09-21T14:00:00+08:00",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert rejected.returncode == 2
    assert rejected.stdout == ""
    assert list(tmp_path.iterdir()) == []


def test_ark_reviewed_candidates_remain_visible_without_platform_materialization(
    app, client, tenant, tenant_headers, ark_personal_headers, ark_relay
):
    relay, catalog, requests = ark_relay
    worker = RelayCatalogSyncWorker(app.state.session_factory, relay)
    first = worker.run_once()
    assert first.reconciliation.created_count == 0
    assert first.reconciliation.synced_count == 0
    assert first.reconciliation.unchanged_count == 0
    with app.state.session_factory() as session:
        first_audits = [row.id for row in session.scalars(select(AuditLog)).all()]
    second = worker.run_once()
    assert second.not_modified is True
    restarted = RelayCatalogSyncWorker(app.state.session_factory, relay).run_once()
    assert restarted.reconciliation.created_count == 0
    assert restarted.reconciliation.synced_count == 0
    assert restarted.reconciliation.unchanged_count == 0
    assert restarted.reconciliation.reconciliation_audit_id is None
    catalog_requests = [
        request for request in requests if request.url.path == "/v1/models"
    ]
    assert [
        request.headers.get("If-None-Match") for request in catalog_requests
    ] == [
        None,
        f'"{catalog.catalog_revision}"',
        None,
    ]
    assert sum(
        request.url.path == "/internal/platform-relay/model-release-evidence"
        for request in requests
    ) == 3

    with app.state.session_factory() as session:
        models = session.scalars(select(ModelDefinition)).all()
        assert models == []
        assert session.scalars(select(CompanyModelGrant)).all() == []
        assert session.scalars(select(PersonalRetailModelGrant)).all() == []
        assert session.scalars(select(CompanyPointPriceVersion)).all() == []
        audits = session.scalars(select(AuditLog)).all()
        assert {audit.id for audit in audits} == set(first_audits)
        model_audits = [audit for audit in audits if audit.action.startswith("model.")]
        assert len(model_audits) == 1
        assert all(audit.actor_kind == "system" for audit in model_audits)
        assert all(audit.actor_key == "relay-catalog-sync" for audit in model_audits)

    company = client.get(
        f"/api/v1/companies/{tenant['company_id']}/models", headers=tenant_headers
    )
    personal = client.get("/api/v1/personal/models", headers=ark_personal_headers)
    assert company.status_code == 200, company.text
    assert personal.status_code == 200, personal.text
    assert company.json() == personal.json() == []

    visible = client.get(
        "/api/v1/platform-admin/relay-models",
        headers=_admin_headers(client, "ark-reviewed-visible"),
    )
    assert visible.status_code == 200, visible.text
    assert len(visible.json()["items"]) == 7
    assert all(
        item["lifecycle"] == "reviewed_candidate"
        and item["customer_callable"] is False
        and item["platform_model_id"] is None
        for item in visible.json()["items"]
    )


def test_ark_manifest_support_does_not_satisfy_approval_or_publish_evidence(
    app, client, ark_relay
):
    relay, catalog, _ = ark_relay
    headers = _admin_headers(client, "ark-no-acceptance")
    RelayCatalogSyncWorker(app.state.session_factory, relay).run_once()
    with app.state.session_factory() as session:
        models = [
            (model.id, model.slug)
            for model in session.scalars(select(ModelDefinition))
        ]
    for model_id, slug in models:
        item = next(item for item in catalog.data if item.id == slug)
        rejected = client.post(
            f"/api/v1/platform-admin/models/{model_id}/relay-capability",
            headers=headers,
            json={
                "expected_capability_version": 1,
                "expected_catalog_revision": catalog.catalog_revision,
                "expected_capability_revision": item.capability_revision,
                "expected_routing_release_sha256": "sha256:" + "0" * 64,
                "reason": "Negative test: a manifest is not live route evidence",
            },
        )
        assert rejected.status_code == 503, rejected.text
        assert "路由发布测试证据暂时不可用" in rejected.json()["detail"]
        publish = client.post(
            f"/api/v1/platform-admin/models/{model_id}/publish", headers=headers
        )
        assert publish.status_code == 409, publish.text
    with app.state.session_factory() as session:
        assert all(
            model.relay_capability_revision is None and not model.active
            for model in session.scalars(select(ModelDefinition)).all()
        )
        assert session.scalars(
            select(AuditLog).where(AuditLog.action == "model.relay_capability.approve")
        ).all() == []


def test_existing_released_ark_models_reach_discovery_with_exact_personal_capabilities(
    app, client, tenant, tenant_headers, ark_personal_headers, ark_preview, ark_relay
):
    """Seed only an in-memory pre-existing release, not fake live acceptance.

    This fixture models previously approved company/retail distribution, which
    may include an existing-customer-only route. It never calls approval APIs or
    supplies success evidence. The preceding test covers that separate gate.
    """

    _, catalog, _ = ark_relay
    sources = {item.id: item for item in catalog.data}
    with app.state.session_factory.begin() as session:
        company = session.get(Company, tenant["company_id"])
        # This is a legacy-price test fixture, not a conversion to points.
        assert company.billing_version == 1
        for entry in ark_preview["models"]:
            request = entry["admin_create_request"]
            source = sources[request["slug"]]
            capability = source.capabilities.contract_dump()
            model = ModelCatalogService.create_model(
                session,
                slug=request["slug"],
                display_name=request["display_name"],
                provider_key="relay",
                billing_mode="per_second",
                capability_version=1,
                capabilities=[("generation", capability)],
            )
            model.relay_capability_revision = source.capability_revision
            model.relay_capability_candidate_revision = source.capability_revision
            model.relay_capability_candidate = deepcopy(capability)
            model.relay_capability_approved_ceiling = deepcopy(capability)
            model.relay_capability_candidate_catalog_revision = catalog.catalog_revision
            model.relay_capability_approved_catalog_revision = catalog.catalog_revision
            session.add(
                CompanyModelGrant(
                    company_id=tenant["company_id"],
                    model_id=model.id,
                    enabled=True,
                    price_per_second_cents=125,
                    config_override={},
                )
            )
            session.add(
                PersonalRetailModelGrant(
                    model_id=model.id,
                    enabled=True,
                    price_per_second_points=3,
                    config_override={},
                )
            )

    company = client.get(
        f"/api/v1/companies/{tenant['company_id']}/models", headers=tenant_headers
    )
    personal = client.get("/api/v1/personal/models", headers=ark_personal_headers)
    assert company.status_code == 200, company.text
    assert personal.status_code == 200, personal.text
    company_models = {item["slug"]: item for item in company.json()}
    personal_models = {item["slug"]: item for item in personal.json()}
    assert set(company_models) == set(personal_models) == set(sources)
    for entry in ark_preview["models"]:
        slug = entry["public_model_id"]
        source_capability = sources[slug].capabilities.contract_dump()
        effective_source_capability = TaskCapabilityAdmission.validate_catalog(
            {"generation": source_capability}, require_usable=True
        )
        assert (
            company_models[slug]["display_name"]
            == entry["admin_create_request"]["display_name"]
        )
        assert personal_models[slug]["display_name"] == company_models[slug]["display_name"]
        assert (
            company_models[slug]["effective_capabilities"]
            == effective_source_capability
        )
        assert company_models[slug]["unit_price_cents"] == 125
        assert personal_models[slug]["unit_price_points"] == 3
        modes = personal_models[slug]["effective_capabilities"]["modes"]
        assert set(modes) == set(effective_source_capability["modes"])
        assert (
            modes["text_to_video"]["limits"]
            == effective_source_capability["modes"]["text_to_video"]["limits"]
        )
        assert modes["text_to_video"]["input_media_types"] == []
        assert modes["text_to_video"]["supports_face"] is False
        image_mode = modes.get("image_to_video")
        if image_mode is not None:
            source_image_mode = effective_source_capability["modes"][
                "image_to_video"
            ]
            assert image_mode["input_roles"] == source_image_mode["input_roles"]
            assert (
                image_mode["structured_inputs"]
                == source_image_mode["structured_inputs"]
            )
