"""Isolated API orchestration only; no network provider or deployment calls."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import delete, event, func, select

from platform_api.models import (
    AuditLog, AuthSession, Company, CompanyModelGrant, CompanyPointLedgerEntry,
    CompanyPointLot, CompanyPointPriceVersion, ExternalIdentity, GenerationTask,
    LedgerEntry,
    ModelCommercialReleaseExecution, ModelCommercialReleasePlan, ModelDefinition,
    PersonalPointLot, PersonalRetailModelGrant, PersonalWorkspace,
    PointLotSourceKind, RelaySubmissionOutbox, ResourceDefinition, ResourceKind,
    ShowcaseChannel, User, UserStatus,
)
from platform_api.services.authentication import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from platform_api.services.company_points_billing import (
    POINT_VALUE_CENTS,
    CompanyPointBillingService,
)
from platform_api.services.errors import ConflictError
from platform_api.services.model_commercial_release import (
    LOCAL_VIDEO_LAB_ACTIVATION_ACTION,
    ModelCommercialReleaseService,
)
from platform_api.services.personal_billing import PersonalWalletService
from platform_api.platform_admin_access_catalog import PLATFORM_ADMIN_PERMISSION_CATALOG
from platform_api.platform_admin_access_models import PlatformAdminPermission
from platform_api.relay_client import RelayModelCatalog, relay_sha256_revision
from scripts.local_video_lab import (
    ACTIVATION_PATH, IDENTITY_PATH, KIND, LAB_HEADERS, LOGIN_PATH,
    PERSONAL_IDENTITY_PATH, PERSONAL_POINTS_PATH, POINTS_PATH, TARGETS,
    LabApi, LabError, allowed_model_contracts, apply_lock, install_lab_endpoints,
    lab_browser_settings, load_manifest, manifest_sha256, provision_lab, validate_factory_settings,
    validate_manifest, verify_lab,
)

from .test_ark_video_catalog_integration import synthetic_catalog
from .test_director_shot_packages import (
    PNG_BYTES as DIRECTOR_PNG_BYTES,
    _canonical_sha256,
    _manifest,
)
from .test_input_assets import CaptureRelayClient
from .test_relay_capability_sync import CatalogRelayClient


TOKEN = "local-lab-unit-bootstrap-material-2026-08-31"


@pytest.fixture
def manifest():
    return {
        "schema_version": 1, "kind": KIND, "lab_id": "mock-0123456789ab",
        "instance_nonce": "0123456789abcdef0123456789abcdef", "provider_mode": "mock",
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "targets": dict(TARGETS),
        "isolation": {"compose_project": "ai-video-lab-mock-0123456789ab",
                      "platform_database": {"host": "platform-db-lab", "port": 5432,
                                            "database": "video_lab_mock_0123456789ab"},
                      "relay_service_base": "http://relay-lab:3000"},
        "budget": {"test_points": 2000, "unit_price_points_per_second": 1,
                   "call_quota": 20, "concurrency_limit": 1}, "paid_probe_approval": None,
    }


def _catalog(contracts, *, candidate_model_ids=frozenset()):
    candidate_catalog = synthetic_catalog({"models": [{"admin_create_request": {
        "slug": slug, "capabilities": [{"config": contract}],
    }} for slug, contract in contracts.items()]})
    data = []
    published_routes = []
    for resource in candidate_catalog.data:
        item = resource.model_dump(mode="json")
        item["capabilities"] = resource.capabilities.contract_dump()
        if resource.id not in candidate_model_ids:
            route_revision = relay_sha256_revision({
                "model": resource.id,
                "route": "local-video-lab-test-fixture",
            })
            item.update({
                "lifecycle": "published_route",
                "managed_route": True,
                "customer_callable": True,
                "published_route_revision": route_revision,
            })
            published_routes.append({
                "id": resource.id,
                "published_route_revision": route_revision,
            })
        data.append(item)
    published_route_revision = relay_sha256_revision(published_routes)
    catalog_revision = relay_sha256_revision({
        "models": [
            {
                "id": item["id"],
                "capability_revision": item["capability_revision"],
                "lifecycle": item["lifecycle"],
                "managed_route": item["managed_route"],
                "customer_callable": item["customer_callable"],
                "published_route_revision": item["published_route_revision"],
            }
            for item in data
        ],
        "published_route_revision": published_route_revision,
    })
    catalog = RelayModelCatalog.model_validate({
        "api_version": "v1",
        "schema_version": 1,
        "object": "list",
        "catalog_revision_scope": "transport_snapshot",
        "published_route_revision": published_route_revision,
        "catalog_revision": catalog_revision,
        "data": data,
    })
    catalog.validate_canonical_revisions()
    return catalog


def _ready_relay(contracts, *, candidate_model_ids=frozenset(), **changes):
    return CatalogRelayClient(
        _catalog(contracts, candidate_model_ids=candidate_model_ids),
        provider_cost_billing_unit="output_second",
        provider_cost_unit_amount_cents=1,
        provider_cost_source_sha256="c" * 64,
        **changes,
    )


@pytest.fixture
def lab(app, manifest):
    app.state.settings.bootstrap_token = TOKEN
    app.state.settings.frontend_origin = TARGETS["gateway_base"]
    contracts = allowed_model_contracts()
    app.state.relay_client = _ready_relay(contracts)
    install_lab_endpoints(app, manifest)
    with TestClient(app, base_url=TARGETS["platform_base"]) as client:
        yield app, LabApi(manifest, TOKEN, client), contracts


def test_manifest_is_exact_local_and_non_cash(manifest, tmp_path):
    assert validate_manifest(manifest) == manifest
    path = tmp_path / "lab.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert load_manifest(path) == manifest
    assert manifest_sha256(manifest).startswith("sha256:")
    with pytest.raises(LabError, match="absolute"):
        load_manifest("lab.json")


def test_cli_default_plan_does_not_load_database_or_emit_credentials(manifest, tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts/local_video_lab.py"
    environment = {**os.environ, "DATABASE_URL": "invalid-database-must-never-be-opened",
                   "LOCAL_VIDEO_LAB_BOOTSTRAP_TOKEN": TOKEN}
    result = subprocess.run([sys.executable, "-B", str(script), "--manifest", str(path)],
                            env=environment, capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["preview_only"] is True
    assert report["will_submit_provider_tasks"] is False
    assert len(report["eligible_model_ids"]) == 8
    assert TOKEN not in result.stdout + result.stderr
    assert not path.with_name(path.name + ".platform-apply.lock").exists()


@pytest.mark.parametrize("mutation", [
    lambda m: m["targets"].update(platform_base="http://127.0.0.1:8200"),
    lambda m: m["targets"].update(gateway_base="http://127.0.0.1:8180"),
    lambda m: m["targets"].update(relay_base="http://127.0.0.1:8300"),
    lambda m: m["targets"].update(platform_base="https://platform.example.com:18420"),
    lambda m: m["isolation"]["platform_database"].update(database="platform"),
    lambda m: m["isolation"].update(relay_service_base="http://relay:3000"),
    lambda m: m.update(provider_mode="live"),
    lambda m: m["budget"].update(test_points=0),
    lambda m: m["budget"].update(test_points=True),
    lambda m: m.update(api_key="not-accepted-even-as-a-field"),
])
def test_manifest_rejects_unbound_or_unsafe_targets(manifest, mutation):
    mutation(manifest)
    with pytest.raises(LabError):
        validate_manifest(manifest)


def _factory_settings(manifest):
    return SimpleNamespace(environment="development", protected_runtime=False, enable_bootstrap=True,
                           development_header_auth_enabled=True, bootstrap_token=TOKEN,
                           frontend_origin=TARGETS["gateway_base"],
                           database_url="postgresql+psycopg://video_lab:synthetic@platform-db-lab:5432/" + manifest["isolation"]["platform_database"]["database"],
                           relay_base_url="http://relay-lab:3000")


@pytest.mark.parametrize("changed", [
    {"environment": "production"}, {"environment": "staging"}, {"protected_runtime": True},
    {"enable_bootstrap": False}, {"development_header_auth_enabled": False},
    {"database_url": "postgresql+psycopg://video_lab:synthetic@platform-db-lab:5432/platform"},
    {"database_url": "postgresql+psycopg://video_lab:synthetic@platform-db:5432/video_lab_mock_0123456789ab"},
    {"database_url": "sqlite+pysqlite:///platform.db"}, {"relay_base_url": "http://relay:3000"},
    {"frontend_origin": "http://127.0.0.1:8180"}, {"oidc_enabled": True},
    {"platform_owner_user_ids": ["existing-owner-identity"]},
])
def test_factory_refuses_normal_database_and_protected_runtime(manifest, changed):
    settings = _factory_settings(manifest)
    validate_factory_settings(settings, manifest, TOKEN)
    for field, value in changed.items():
        setattr(settings, field, value)
    with pytest.raises(LabError):
        validate_factory_settings(settings, manifest, TOKEN)


def test_factory_accepts_only_one_bound_native_relay_backend(manifest):
    settings = _factory_settings(manifest)
    settings.relay_base_url = None
    settings.relay_default_backend_id = "new-api-main"
    settings.relay_backends = {"new-api-main": SimpleNamespace(base_url="http://relay-lab:3000")}
    validate_factory_settings(settings, manifest, TOKEN)
    settings.relay_backends["ordinary"] = SimpleNamespace(base_url="http://relay:3000")
    with pytest.raises(LabError, match="exactly one"):
        validate_factory_settings(settings, manifest, TOKEN)


def test_local_browser_origin_uses_an_app_copy_without_fabricated_oidc(app, manifest):
    assert app.state.settings.frontend_origin is None
    assert app.state.settings.oidc_enabled is False
    copied = lab_browser_settings(app.state.settings, manifest)
    assert copied.frontend_origin == TARGETS["gateway_base"]
    assert copied.oidc_enabled is False
    assert app.state.settings.frontend_origin is None
    with pytest.raises(LabError, match="exact lab origins"):
        lab_browser_settings(app.state.settings, manifest, "http://127.0.0.1:8180")


def test_factory_will_not_adopt_existing_storage(app, manifest):
    with app.state.session_factory.begin() as session:
        session.add(Company(name="Existing company"))
    with pytest.raises(LabError, match="non-empty"):
        install_lab_endpoints(app, manifest)
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(AuditLog.id))) == 0


def test_freshness_allows_only_known_empty_migration_seeds(app, manifest):
    with app.state.session_factory.begin() as session:
        session.add_all(PlatformAdminPermission(code=spec.code, domain=spec.domain, action=spec.action,
                                                description=spec.description)
                        for spec in PLATFORM_ADMIN_PERMISSION_CATALOG)
        session.add(ResourceDefinition(id="00000000-0000-4000-8000-000000000020",
                                       key="feature.auto_publish", kind=ResourceKind.FEATURE,
                                       display_name="Automatic publishing", active=True))
        session.add(ShowcaseChannel(id="home"))
    install_lab_endpoints(app, manifest)


@pytest.mark.parametrize("mutation", ["description", "domain", "action", "extra", "partial"])
def test_freshness_rejects_noncanonical_admin_permission_seed(app, manifest, mutation):
    rows = [{"code": spec.code, "domain": spec.domain, "action": spec.action, "description": spec.description}
            for spec in PLATFORM_ADMIN_PERMISSION_CATALOG]
    if mutation == "extra":
        rows.append({"code": "platform.unrecognized.manage", "domain": "unrecognized",
                     "action": "manage", "description": "Not a migration seed"})
    elif mutation == "partial":
        rows.pop()
    else:
        rows[0][mutation] = "modified"
    with app.state.session_factory.begin() as session:
        session.add_all(PlatformAdminPermission(**row) for row in rows)
    with pytest.raises(LabError, match="non-empty"):
        install_lab_endpoints(app, manifest)
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(AuditLog.id))) == 0


def test_freshness_rejects_changed_showcase_seed(app, manifest):
    with app.state.session_factory.begin() as session:
        session.add(ShowcaseChannel(id="home", draft_version=1))
    with pytest.raises(LabError, match="non-empty"):
        install_lab_endpoints(app, manifest)


def test_database_marker_is_durable_and_bound_to_mode_and_manifest(lab, manifest):
    app, api, _ = lab
    first = api.identity()
    install_lab_endpoints(app, manifest)
    assert api.identity() == first
    other = deepcopy(manifest)
    other["instance_nonce"] = "f" * 32
    with pytest.raises(LabError, match="different lab"):
        install_lab_endpoints(app, other)


def test_http_identity_requires_exact_origin_token_and_instance(lab):
    _, api, _ = lab
    assert api.identity()["storage_bound"]
    for changes, status in [({"X-Bootstrap-Token": "incorrect"}, 401),
                            ({LAB_HEADERS[1]: "f" * 32}, 409),
                            ({"Host": "127.0.0.1:8200"}, 403)]:
        response = api.client.get(IDENTITY_PATH, headers={**api.headers, **changes})
        assert response.status_code == status


@pytest.mark.parametrize(("peer", "expected"), [
    ("127.0.0.1", 200), ("::1", 200), ("11.254.93.1", 200), ("11.254.94.1", 200),
    ("11.254.93.5", 200), ("11.254.94.5", 200),
    ("11.254.93.4", 403), ("11.254.94.6", 403), ("172.18.0.1", 403), ("10.0.0.1", 403),
])
def test_private_lab_api_accepts_only_exact_loopback_bridge_and_edge_peers(app, manifest, peer, expected):
    app.state.settings.bootstrap_token = TOKEN
    install_lab_endpoints(app, manifest)
    with TestClient(app, base_url=TARGETS["platform_base"], client=(peer, 51000)) as client:
        api = LabApi(manifest, TOKEN, client)
        # Forwarded headers cannot convert an unrelated private peer into an edge.
        response = client.get(IDENTITY_PATH, headers={**api.headers, "X-Forwarded-For": "127.0.0.1",
                                                     "Forwarded": "for=127.0.0.1;host=127.0.0.1:18420"})
        assert response.status_code == expected
        if expected == 200:
            assert response.json()["storage_bound"] is True
            denied = client.get(IDENTITY_PATH, headers={**api.headers, "X-Bootstrap-Token": "incorrect"})
            assert denied.status_code == 401


def test_wrong_identity_or_redirect_never_authorizes_bootstrap(manifest):
    seen = []
    def transport(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(302, headers={"Location": "https://unrelated.example.com/collect"})
    with httpx.Client(base_url=TARGETS["platform_base"], transport=httpx.MockTransport(transport), follow_redirects=False) as client:
        api = LabApi(manifest, TOKEN, client)
        with pytest.raises(LabError, match="HTTP 302"):
            provision_lab(api, allowed_model_contracts())
    assert seen == [("GET", IDENTITY_PATH)]


def test_lab_api_principal_headers_are_mutually_exclusive(manifest):
    seen = []

    def transport(request):
        seen.append(dict(request.headers))
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(
        base_url=TARGETS["platform_base"],
        transport=httpx.MockTransport(transport),
        follow_redirects=False,
    ) as client:
        api = LabApi(manifest, TOKEN, client)
        api.request(
            "GET",
            "/api/v1/personal/models",
            identity={
                "user_id": "personal-user",
                "company_id": None,
                "personal_workspace_id": "personal-workspace",
            },
        )
        assert seen[-1]["x-user-id"] == "personal-user"
        assert "x-company-id" not in seen[-1]
        api.request(
            "GET",
            "/api/v1/companies/company-one/models",
            identity={
                "user_id": "company-user",
                "company_id": "company-one",
                "personal_workspace_id": None,
            },
        )
        assert seen[-1]["x-user-id"] == "company-user"
        assert seen[-1]["x-company-id"] == "company-one"
        with pytest.raises(LabError, match="exactly one"):
            api.request(
                "GET",
                "/api/v1/personal/models",
                identity={
                    "user_id": "ambiguous-user",
                    "company_id": "company-one",
                    "personal_workspace_id": "personal-workspace",
                },
            )
        with pytest.raises(LabError, match="exactly one"):
            api.request(
                "GET",
                "/api/v1/personal/models",
                identity={
                    "user_id": "unscoped-user",
                    "company_id": None,
                    "personal_workspace_id": None,
                },
            )


def _row_counts(app):
    with app.state.session_factory() as session:
        return {model.__name__: session.scalar(select(func.count()).select_from(model)) for model in (
            AuditLog, Company, User, CompanyModelGrant, CompanyPointPriceVersion, CompanyPointLot,
            CompanyPointLedgerEntry, ModelDefinition, ModelCommercialReleasePlan,
            ModelCommercialReleaseExecution, PersonalRetailModelGrant,
            PersonalWorkspace, PersonalPointLot,
        )}


def _assert_no_lab_activation_authority(
    app,
    *,
    company_point_lots: int = 0,
    personal_point_lots: int = 0,
):
    with app.state.session_factory() as session:
        models = session.scalars(select(ModelDefinition)).all()
        assert all(
            not model.active
            and model.published_at is None
            and model.relay_capability_revision is None
            for model in models
        )
        assert session.scalar(select(func.count(CompanyModelGrant.id))) == 0
        assert session.scalar(select(func.count(PersonalRetailModelGrant.id))) == 0
        assert session.scalar(select(func.count(CompanyPointLot.id))) == company_point_lots
        assert session.scalar(select(func.count(PersonalPointLot.id))) == personal_point_lots
        assert session.scalar(select(func.count(CompanyPointPriceVersion.id))) == 0
        assert session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == LOCAL_VIDEO_LAB_ACTIVATION_ACTION
            )
        ) == 0


def _stage_lab_plans_without_activation(api, contracts):
    original_request = api.request
    captured: dict[str, dict] = {}

    def stop_before_activation(method, path, body=None, **kwargs):
        if method == "POST" and path == ACTIVATION_PATH:
            captured["body"] = deepcopy(body)
            raise LabError("activation intentionally staged by test")
        return original_request(method, path, body, **kwargs)

    api.request = stop_before_activation
    try:
        with pytest.raises(LabError, match="intentionally staged"):
            provision_lab(api, contracts)
    finally:
        api.request = original_request
    assert captured.get("body")
    return captured["body"]


def test_all_eligible_models_use_real_api_approval_and_idempotent_non_cash_budget(lab):
    app, api, contracts = lab
    result = provision_lab(api, contracts)
    before = _row_counts(app)
    repeated = provision_lab(api, contracts)
    assert _row_counts(app) == before
    assert result == repeated
    assert set(model["slug"] for model in result["models"]) == set(contracts)
    assert len(result["models"]) == 8
    assert result["provider_tasks_submitted_by_cli"] == 0
    assert result["real_provider_acceptance"] is False
    assert result["test_data_only"] is True
    activation = result["commercial_activation"]
    assert activation["receipt"]["kind"] == (
        "local_video_lab_exact_commercial_activation_receipt"
    )
    assert activation["receipt"]["budget"] == api.manifest["budget"]
    assert len(activation["receipt"]["plans"]) == len(contracts)
    assert result["default_acceptance_scope_kind"] == "personal"
    assert result["acceptance_scopes"]["company"]["company_id"] == result["company_id"]
    assert result["acceptance_scopes"]["company"]["personal_workspace_id"] is None
    assert result["acceptance_scopes"]["personal"]["company_id"] is None
    assert (
        result["acceptance_scopes"]["personal"]["personal_workspace_id"]
        == result["personal_workspace_id"]
    )
    assert {
        item["slug"]
        for item in result["acceptance_scopes"]["personal"]["models"]
    } == set(contracts)
    for scope_kind, scope in result["acceptance_scopes"].items():
        assert scope["scope_kind"] == scope_kind
        assert bool(scope["company_id"]) != bool(scope["personal_workspace_id"])
        for item in scope["models"]:
            assert item["billing_unit"] == "POINT"
            assert item["billing_version"] == 2
            assert item["unit_price_points"] == 1
            assert item["expected_relay_capability_revision"].startswith("sha256:")
            assert item["mode_readiness"]["image_to_video"]["default"]["ready"]
            if scope_kind == "personal":
                assert item["call_quota"] == api.manifest["budget"]["call_quota"]
                assert (
                    item["concurrency_limit"]
                    == api.manifest["budget"]["concurrency_limit"]
                )
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(LedgerEntry.id))) == 0
        lots = session.scalars(select(CompanyPointLot)).all()
        assert len(lots) == 1
        assert lots[0].source_kind == PointLotSourceKind.PROMOTIONAL
        assert lots[0].cash_basis_cents == lots[0].receivable_basis_cents == 0
        assert lots[0].subsidy_cents == 20_000
        assert lots[0].original_points == 2000
        personal_lots = session.scalars(select(PersonalPointLot)).all()
        assert len(personal_lots) == 1
        assert personal_lots[0].source_kind == PointLotSourceKind.PROMOTIONAL
        assert personal_lots[0].cash_basis_cents == 0
        assert personal_lots[0].receivable_basis_cents == 0
        assert personal_lots[0].subsidy_cents == 20_000
        assert personal_lots[0].original_points == 2000
        plans = session.scalars(select(ModelCommercialReleasePlan)).all()
        executions = session.scalars(select(ModelCommercialReleaseExecution)).all()
        assert len(plans) == len(executions) == len(contracts)
        assert all(execution.state == "released" for execution in executions)
        assert all(
            plan.provider_cost_formula["assumptions"]["personal_media_policy"]
            == "explicit_image_input_v1"
            for plan in plans
        )
        assert session.scalar(select(func.count(PersonalRetailModelGrant.id))) == len(contracts)
        assert session.scalar(select(func.count(CompanyModelGrant.id))) == len(contracts)
        assert all(
            grant.call_quota == api.manifest["budget"]["call_quota"]
            and grant.concurrency_limit
            == api.manifest["budget"]["concurrency_limit"]
            for grant in session.scalars(select(CompanyModelGrant)).all()
        )
        for grant in session.scalars(select(PersonalRetailModelGrant)).all():
            assert grant.call_quota == api.manifest["budget"]["call_quota"]
            assert (
                grant.concurrency_limit
                == api.manifest["budget"]["concurrency_limit"]
            )
            modes = grant.config_override["modes"]
            assert set(modes) == {"text_to_video", "image_to_video"}
            assert modes["image_to_video"]["input_media_types"] == ["image"]
            assert modes["image_to_video"]["limits"]["max_images"] >= 1
            assert modes["image_to_video"]["limits"]["max_videos"] == 0
            assert modes["image_to_video"]["limits"]["max_audio"] == 0
        for action, count in (
            ("model.relay_capability.approve", 8),
            ("model.publish", 8),
            ("personal.model_grant.update", 8),
            # Final quota/concurrency are installed by the release itself.
            ("company.model_grant.upsert", 8),
        ):
            assert session.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == action)) == count
        assert session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == LOCAL_VIDEO_LAB_ACTIVATION_ACTION
            )
        ) == 1
        assert session.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "local_video_lab.test_points")) == 0
        assert session.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "local_video_lab.personal_test_points")) == 0
    assert verify_lab(api, contracts) == result


def test_candidate_only_catalog_entries_remain_relay_owned_and_ungranted(lab):
    app, api, contracts = lab
    candidate_slug = "reviewed-candidate-only"
    expanded = {**contracts, candidate_slug: deepcopy(next(iter(contracts.values())))}
    relay = _ready_relay(expanded, candidate_model_ids={candidate_slug})
    evidence = relay.evidence.model_dump()
    candidate = next(
        item
        for item in evidence["models"]
        if item["public_model_id"] == candidate_slug
    )
    candidate.update({
        "model_release_id": None,
        "model_release_revision": None,
        "published_route_revision": None,
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
    })
    relay.evidence = type(relay.evidence).model_validate(evidence)
    app.state.relay_client = relay

    result = provision_lab(api, contracts)

    assert {item["slug"] for item in result["models"]} == set(contracts)
    with app.state.session_factory() as session:
        candidate_model = session.scalar(
            select(ModelDefinition).where(ModelDefinition.slug == candidate_slug)
        )
        # A reviewed candidate remains Relay-owned.  It may appear in the
        # catalog audit, but it must not materialize in Platform until Relay
        # publishes a tested route for it.
        assert candidate_model is None
        assert session.scalar(select(func.count(CompanyModelGrant.id))) == len(
            contracts
        )
        assert session.scalar(
            select(func.count(PersonalRetailModelGrant.id))
        ) == len(contracts)


@pytest.mark.parametrize(("field", "unsafe_value"), [
    ("platform_active", True),
    ("approved_revision", "sha256:" + ("a" * 64)),
    ("requires_approval", False),
])
def test_candidate_only_extra_fails_closed_before_any_approval_action(
    lab, field, unsafe_value
):
    app, api, contracts = lab
    candidate_slug = f"unsafe-candidate-{field.replace('_', '-')}"
    expanded = {**contracts, candidate_slug: deepcopy(next(iter(contracts.values())))}
    relay = _ready_relay(expanded, candidate_model_ids={candidate_slug})
    evidence = relay.evidence.model_dump()
    candidate = next(
        item
        for item in evidence["models"]
        if item["public_model_id"] == candidate_slug
    )
    candidate.update({
        "model_release_id": None,
        "model_release_revision": None,
        "published_route_revision": None,
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
    })
    relay.evidence = type(relay.evidence).model_validate(evidence)
    app.state.relay_client = relay

    requests = []
    original_request = api.request

    def record_request(method, path, body=None, **kwargs):
        requests.append((method, path))
        response = original_request(method, path, body, **kwargs)
        if method == "POST" and path.endswith("/relay-models/reconcile"):
            extra = next(
                item
                for item in response["items"]
                if item["relay_model_id"] == candidate_slug
            )
            extra[field] = unsafe_value
        return response

    api.request = record_request
    with pytest.raises(LabError, match="no automatic expansion"):
        provision_lab(api, contracts)

    assert requests[-1] == (
        "POST",
        "/api/v1/platform-admin/relay-models/reconcile",
    )
    assert not any(
        (method == "POST" and path.endswith(("/relay-capability", "/publish")))
        or (method == "PUT" and path.endswith("/model-grants"))
        for method, path in requests
    )
    with app.state.session_factory() as session:
        for action in (
            "model.relay_capability.approve",
            "model.publish",
            "company.model_grant.upsert",
        ):
            assert session.scalar(
                select(func.count(AuditLog.id)).where(AuditLog.action == action)
            ) == 0


def test_unexpected_routable_catalog_entry_still_blocks_expansion(lab):
    app, api, contracts = lab
    expanded = {
        **contracts,
        "unexpected-routable": deepcopy(next(iter(contracts.values()))),
    }
    app.state.relay_client = _ready_relay(expanded)

    with pytest.raises(LabError, match="no automatic expansion"):
        provision_lab(api, contracts)

    with app.state.session_factory() as session:
        assert session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == "model.relay_capability.approve"
            )
        ) == 0


def test_missing_route_evidence_stops_after_unpublished_drafts(lab):
    app, api, contracts = lab
    app.state.relay_client = CatalogRelayClient(_catalog(contracts), evidence_status="blocked")
    with pytest.raises(LabError, match="route acceptance"):
        provision_lab(api, contracts)
    with app.state.session_factory() as session:
        models = session.scalars(select(ModelDefinition)).all()
        assert len(models) == 8
        assert all(not model.active and model.relay_capability_revision is None for model in models)
        assert session.scalar(select(func.count(CompanyModelGrant.id))) == 0
        assert session.scalar(select(func.count(CompanyPointLot.id))) == 0
        assert session.scalar(select(func.count(PersonalPointLot.id))) == 0
        assert session.scalar(select(func.count(ModelCommercialReleasePlan.id))) == 0


def test_one_model_route_drift_rolls_back_the_exact_activation_set(lab):
    app, api, contracts = lab
    request = api.request

    def drift_before_release(method, path, body=None, **kwargs):
        if method == "POST" and path == ACTIVATION_PATH:
            app.state.relay_client.evidence.models[0].routing_release_sha256 = (
                "sha256:" + "e" * 64
            )
        return request(method, path, body, **kwargs)

    api.request = drift_before_release
    with pytest.raises(LabError, match="HTTP 409"):
        provision_lab(api, contracts)
    _assert_no_lab_activation_authority(app)
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(ModelCommercialReleasePlan.id))) == len(contracts)
        assert all(
            execution.state == "approved"
            for execution in session.scalars(
                select(ModelCommercialReleaseExecution)
            ).all()
        )
        assert ModelCommercialReleaseService.has_unreleased_plans(session) is False

    # The generic operator reconciler is disabled by the durable lab marker,
    # so neither a periodic worker nor an administrator can consume one plan.
    with pytest.raises(LabError, match="HTTP 409"):
        request(
            "POST",
            "/api/v1/platform-admin/model-commercial-releases/reconcile",
            admin=api.identity()["admin_user_id"],
        )
    _assert_no_lab_activation_authority(app)


def test_activation_rejects_a_subset_before_any_authority_is_created(lab):
    app, api, contracts = lab
    body = _stage_lab_plans_without_activation(api, contracts)
    assert len(body["plans"]) == len(contracts)

    with pytest.raises(LabError, match="HTTP 409"):
        api.request(
            "POST",
            ACTIVATION_PATH,
            {"plans": body["plans"][:-1]},
            admin=api.identity()["admin_user_id"],
        )

    _assert_no_lab_activation_authority(app)


def test_historical_authority_outside_requested_models_blocks_activation(lab):
    app, api, contracts = lab
    body = _stage_lab_plans_without_activation(api, contracts)
    with app.state.session_factory.begin() as session:
        session.add(
            ModelDefinition(
                slug="historical-model-outside-exact-plan-set",
                display_name="Historical polluted model",
                provider_key="historical-test-only",
                billing_mode="per_second",
                active=True,
            )
        )

    with pytest.raises(LabError, match="HTTP 409"):
        api.request(
            "POST",
            ACTIVATION_PATH,
            body,
            admin=api.identity()["admin_user_id"],
        )

    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(CompanyModelGrant.id))) == 0
        assert session.scalar(select(func.count(PersonalRetailModelGrant.id))) == 0
        assert session.scalar(select(func.count(CompanyPointLot.id))) == 0
        assert session.scalar(select(func.count(PersonalPointLot.id))) == 0
        assert session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == LOCAL_VIDEO_LAB_ACTIVATION_ACTION
            )
        ) == 0


def test_activation_commit_failure_is_not_acknowledged_and_rolls_back(lab):
    app, api, contracts = lab
    body = _stage_lab_plans_without_activation(api, contracts)
    session_class = app.state.session_factory.class_

    def fail_activation_commit(session):
        if session.scalar(
            select(AuditLog.id)
            .where(AuditLog.action == LOCAL_VIDEO_LAB_ACTIVATION_ACTION)
            .limit(1)
        ) is not None:
            raise RuntimeError("injected activation commit failure")

    transport = api.client._transport
    previous_raise_server_exceptions = transport.raise_server_exceptions
    event.listen(session_class, "before_commit", fail_activation_commit)
    transport.raise_server_exceptions = False
    try:
        response = api.client.post(
            ACTIVATION_PATH,
            headers={
                **api.headers,
                "X-Platform-Admin-User-ID": api.identity()["admin_user_id"],
            },
            json=body,
        )
    finally:
        transport.raise_server_exceptions = previous_raise_server_exceptions
        event.remove(session_class, "before_commit", fail_activation_commit)

    assert response.status_code == 500
    _assert_no_lab_activation_authority(app)


@pytest.mark.parametrize("scope_target", ["company_user", "personal_workspace"])
def test_activation_locks_and_revalidates_exact_principals(lab, scope_target):
    app, api, contracts = lab
    body = _stage_lab_plans_without_activation(api, contracts)
    identity = api.identity()
    with app.state.session_factory.begin() as session:
        if scope_target == "company_user":
            company_user = session.get(User, identity["user_id"])
            assert company_user is not None
            company_user.status = UserStatus.SUSPENDED
        else:
            workspace = session.get(
                PersonalWorkspace, identity["personal_workspace_id"]
            )
            assert workspace is not None
            workspace.active = False

    with pytest.raises(LabError, match="HTTP 409"):
        api.request(
            "POST",
            ACTIVATION_PATH,
            body,
            admin=identity["admin_user_id"],
        )

    _assert_no_lab_activation_authority(app)


@pytest.mark.parametrize("credit_scope", ["company", "personal"])
def test_activation_rejects_any_credit_not_created_by_its_transaction(
    lab,
    monkeypatch,
    credit_scope,
):
    app, api, contracts = lab
    body = _stage_lab_plans_without_activation(api, contracts)
    service = (
        CompanyPointBillingService
        if credit_scope == "company"
        else PersonalWalletService
    )
    original_credit = service.credit

    def report_preexisting_credit(session, **kwargs):
        wallet, entry, _created = original_credit(session, **kwargs)
        return wallet, entry, False

    monkeypatch.setattr(service, "credit", staticmethod(report_preexisting_credit))
    with pytest.raises(LabError, match="HTTP 409"):
        api.request(
            "POST",
            ACTIVATION_PATH,
            body,
            admin=api.identity()["admin_user_id"],
        )

    _assert_no_lab_activation_authority(app)


def test_second_release_failure_rolls_back_every_authorization(
    lab,
    monkeypatch,
):
    app, api, contracts = lab
    original = ModelCommercialReleaseService._release_one
    calls = 0

    def fail_second(cls, session, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ConflictError("injected exact activation release failure")
        return original(session, **kwargs)

    monkeypatch.setattr(
        ModelCommercialReleaseService,
        "_release_one",
        classmethod(fail_second),
    )
    with pytest.raises(LabError, match="HTTP 409"):
        provision_lab(api, contracts)
    assert calls == 2
    _assert_no_lab_activation_authority(app)
    with app.state.session_factory() as session:
        assert all(
            execution.state == "approved" and execution.attempt_count == 0
            for execution in session.scalars(
                select(ModelCommercialReleaseExecution)
            ).all()
        )


def test_personal_credit_failure_rolls_back_releases_and_company_credit(
    lab,
    monkeypatch,
):
    app, api, contracts = lab

    def fail_personal_credit(*_args, **_kwargs):
        raise ConflictError("injected personal credit failure")

    monkeypatch.setattr(
        PersonalWalletService,
        "credit",
        staticmethod(fail_personal_credit),
    )
    with pytest.raises(LabError, match="HTTP 409"):
        provision_lab(api, contracts)
    _assert_no_lab_activation_authority(app)


def test_existing_unrelated_points_cannot_make_a_failed_partial_set_usable(lab):
    app, api, contracts = lab
    request = api.request
    injected = False

    def fund_then_drift(method, path, body=None, **kwargs):
        nonlocal injected
        if method == "POST" and path == ACTIVATION_PATH and not injected:
            injected = True
            identity = request("GET", IDENTITY_PATH)
            with app.state.session_factory.begin() as session:
                CompanyPointBillingService.credit(
                    session,
                    company_id=identity["company_id"],
                    amount_points=37,
                    source_kind=PointLotSourceKind.PROMOTIONAL,
                    cash_basis_cents=0,
                    receivable_basis_cents=0,
                    subsidy_cents=37 * POINT_VALUE_CENTS,
                    idempotency_key="unrelated-existing-company-points",
                    note="pre-existing isolated test balance",
                )
                PersonalWalletService.credit(
                    session,
                    workspace_id=identity["personal_workspace_id"],
                    amount_points=37,
                    idempotency_key="unrelated-existing-personal-points",
                    note="pre-existing isolated test balance",
                )
            app.state.relay_client.evidence.models[0].routing_release_sha256 = (
                "sha256:" + "e" * 64
            )
        return request(method, path, body, **kwargs)

    api.request = fund_then_drift
    with pytest.raises(LabError, match="HTTP 409"):
        provision_lab(api, contracts)
    assert injected is True
    _assert_no_lab_activation_authority(
        app,
        company_point_lots=1,
        personal_point_lots=1,
    )
    with app.state.session_factory() as session:
        assert session.scalar(select(CompanyPointLot)).available_points == 37
        assert session.scalar(select(PersonalPointLot)).available_points == 37


def test_exact_activation_replay_is_offline_noop_and_intent_drift_conflicts(lab):
    app, api, contracts = lab
    result = provision_lab(api, contracts)
    activation = result["commercial_activation"]
    body = {
        "plans": [
            {
                key: item[key]
                for key in (
                    "plan_id",
                    "model_id",
                    "model_slug",
                    "plan_content_sha256",
                )
            }
            for item in activation["receipt"]["plans"]
        ]
    }
    before = _row_counts(app)
    app.state.relay_client = None
    replayed = api.request(
        "POST",
        ACTIVATION_PATH,
        body,
        admin=result["admin_user_id"],
    )
    assert replayed == activation
    assert _row_counts(app) == before

    changed = deepcopy(body)
    changed["plans"][0]["plan_content_sha256"] = "0" * 64
    with pytest.raises(LabError, match="HTTP 409"):
        api.request(
            "POST",
            ACTIVATION_PATH,
            changed,
            admin=result["admin_user_id"],
        )
    assert _row_counts(app) == before


def test_historical_partial_authority_without_activation_receipt_is_rejected(lab):
    app, api, contracts = lab
    result = provision_lab(api, contracts)
    activation = result["commercial_activation"]
    body = {
        "plans": [
            {
                key: item[key]
                for key in (
                    "plan_id",
                    "model_id",
                    "model_slug",
                    "plan_content_sha256",
                )
            }
            for item in activation["receipt"]["plans"]
        ]
    }
    with app.state.session_factory.begin() as session:
        receipt_audit_id = session.scalar(
            select(AuditLog.id).where(
                AuditLog.action == LOCAL_VIDEO_LAB_ACTIVATION_ACTION
            )
        )
        assert receipt_audit_id is not None
        # Simulate a database created by the former, dispersed activation path.
        # AuditLog's ORM immutability hook is intentionally bypassed here.
        session.execute(delete(AuditLog).where(AuditLog.id == receipt_audit_id))
    before = _row_counts(app)
    with pytest.raises(LabError, match="HTTP 409"):
        api.request(
            "POST",
            ACTIVATION_PATH,
            body,
            admin=result["admin_user_id"],
        )
    assert _row_counts(app) == before
    with app.state.session_factory() as session:
        assert session.scalar(
            select(AuditLog.id).where(
                AuditLog.action == LOCAL_VIDEO_LAB_ACTIVATION_ACTION
            )
        ) is None


@pytest.mark.parametrize("relay_changes", [
    {"provider_cost_billing_unit": "output_item"},
    {"provider_cost_rate_set": True},
    {"provider_cost_ready": False},
])
def test_unqualified_provider_cost_never_releases_or_grants(lab, relay_changes):
    app, api, contracts = lab
    # Override the helper defaults explicitly because these cases are proving
    # that each non-sellable cost shape is rejected before points or pricing.
    options = {
        "provider_cost_billing_unit": "output_second",
        "provider_cost_unit_amount_cents": 1,
        "provider_cost_currency": "CNY",
        "provider_cost_source_sha256": "c" * 64,
        "provider_cost_rate_set": False,
        "provider_cost_ready": True,
    }
    options.update(relay_changes)
    app.state.relay_client = CatalogRelayClient(_catalog(contracts), **options)

    with pytest.raises(LabError, match="provider cost|CNY output-second"):
        provision_lab(api, contracts)

    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(ModelCommercialReleasePlan.id))) == 0
        assert session.scalar(select(func.count(CompanyModelGrant.id))) == 0
        assert session.scalar(select(func.count(PersonalRetailModelGrant.id))) == 0
        assert session.scalar(select(func.count(CompanyPointLot.id))) == 0
        assert session.scalar(select(func.count(PersonalPointLot.id))) == 0


def test_mixed_provider_cost_sources_never_release_or_grant(lab):
    app, api, contracts = lab
    relay = _ready_relay(contracts)
    evidence = relay.evidence.model_dump()
    rectangles = evidence["models"][0]["routes"][0][
        "provider_cost_rectangles"
    ]
    assert len(rectangles) > 1
    rectangles[-1]["source_document_sha256"] = "d" * 64
    relay.evidence = type(relay.evidence).model_validate(evidence)
    app.state.relay_client = relay

    with pytest.raises(LabError, match="one immutable source"):
        provision_lab(api, contracts)

    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(ModelCommercialReleasePlan.id))) == 0
        assert session.scalar(select(func.count(CompanyModelGrant.id))) == 0
        assert session.scalar(select(func.count(PersonalRetailModelGrant.id))) == 0
        assert session.scalar(select(func.count(CompanyPointLot.id))) == 0
        assert session.scalar(select(func.count(PersonalPointLot.id))) == 0


def test_verify_rechecks_current_route_evidence_and_never_submits(lab):
    app, api, contracts = lab
    provision_lab(api, contracts)
    app.state.relay_client = CatalogRelayClient(_catalog(contracts), evidence_status="blocked")
    with pytest.raises(LabError, match="Current route release evidence"):
        verify_lab(api, contracts)


def test_verify_rejects_ready_route_identity_drift_from_activation_receipt(lab):
    app, api, contracts = lab
    provision_lab(api, contracts)
    app.state.relay_client.evidence.models[0].routing_release_sha256 = (
        "sha256:" + "e" * 64
    )

    with pytest.raises(LabError, match="Current Relay account, key or route identity"):
        verify_lab(api, contracts)


def test_verify_rejects_ready_provider_cost_drift_from_activation_receipt(lab):
    app, api, contracts = lab
    provision_lab(api, contracts)
    evidence = app.state.relay_client.evidence.model_dump()
    rectangle = evidence["models"][0]["routes"][0][
        "provider_cost_rectangles"
    ][0]
    rectangle["unit_amount_cents"] += 1
    app.state.relay_client.evidence = type(
        app.state.relay_client.evidence
    ).model_validate(evidence)

    with pytest.raises(LabError, match="Released commercial plan differs"):
        verify_lab(api, contracts)


def test_live_never_reuses_mock_manifest_or_probes_without_explicit_approval(manifest):
    manifest["provider_mode"] = "live"
    manifest["lab_id"] = "live-0123456789ab"
    manifest["isolation"]["compose_project"] = "ai-video-lab-live-0123456789ab"
    manifest["isolation"]["platform_database"]["database"] = "video_lab_live_0123456789ab"
    validate_manifest(manifest)
    api = LabApi(manifest, TOKEN, object())
    with pytest.raises(LabError, match="explicit paid-probe"):
        provision_lab(api, allowed_model_contracts())


def test_live_personal_points_require_the_same_explicit_approval(app, manifest):
    manifest["provider_mode"] = "live"
    manifest["lab_id"] = "live-0123456789ab"
    manifest["isolation"]["compose_project"] = (
        "ai-video-lab-live-0123456789ab"
    )
    manifest["isolation"]["platform_database"]["database"] = (
        "video_lab_live_0123456789ab"
    )
    validate_manifest(manifest)
    app.state.settings.bootstrap_token = TOKEN
    app.state.settings.frontend_origin = TARGETS["gateway_base"]
    install_lab_endpoints(app, manifest)
    with TestClient(app, base_url=TARGETS["platform_base"]) as client:
        api = LabApi(manifest, TOKEN, client)
        api.request(
            "POST",
            "/api/v1/bootstrap/platform-admin",
            {
                "email": "live-0123456789ab-admin@local-video-lab.example.com",
                "display_name": "Live guard administrator",
            },
        )
        identity = api.identity()
        api.request(
            "POST",
            PERSONAL_IDENTITY_PATH,
            {},
            admin=identity["admin_user_id"],
        )
        identity = api.identity()
        response = client.post(
            PERSONAL_POINTS_PATH,
            headers={
                **api.headers,
                "X-Platform-Admin-User-ID": identity["admin_user_id"],
            },
            json={"user_id": identity["personal_user_id"]},
        )
        assert response.status_code == 409
        assert "explicit paid-probe approval" in response.json()["detail"]
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(PersonalPointLot.id))) == 0


def test_standalone_test_point_writes_are_disabled_before_activation(lab):
    app, api, contracts = lab
    _stage_lab_plans_without_activation(api, contracts)
    identity = api.identity()
    headers = {
        **api.headers,
        "X-Platform-Admin-User-ID": identity["admin_user_id"],
    }
    company = api.client.post(
        POINTS_PATH,
        headers=headers,
        json={"company_id": identity["company_id"]},
    )
    personal = api.client.post(
        PERSONAL_POINTS_PATH,
        headers=headers,
        json={"user_id": identity["personal_user_id"]},
    )
    assert company.status_code == personal.status_code == 409
    assert "exact commercial activation" in company.json()["detail"]
    assert "exact commercial activation" in personal.json()["detail"]
    extra = api.client.post(
        POINTS_PATH,
        headers=headers,
        json={"company_id": identity["company_id"], "amount_points": 99999},
    )
    assert extra.status_code == 422
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(CompanyPointLot.id))) == 0
        assert session.scalar(select(func.count(PersonalPointLot.id))) == 0


def test_local_login_is_real_cookie_session_and_preserves_csrf(lab):
    app, api, contracts = lab
    result = provision_lab(api, contracts)
    assert api.client.post(LOGIN_PATH, headers=api.headers, json={}).status_code == 422
    response = api.client.post(
        LOGIN_PATH, headers=api.headers, json={"scope_kind": "company"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["identity_provenance"] == "local_lab_bootstrap"
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 2 and all("Secure" in cookie and "SameSite=lax" in cookie for cookie in cookies)
    assert "HttpOnly" in next(cookie for cookie in cookies if cookie.startswith(SESSION_COOKIE_NAME + "="))
    cookie_header = "; ".join(cookie.split(";", 1)[0] for cookie in cookies)
    session_response = api.client.get("/api/v1/auth/session", headers={"Cookie": cookie_header})
    assert session_response.status_code == 200, session_response.text
    state = session_response.json()
    assert state["authenticated"] is True
    assert state["active_product_context"] == "company"
    assert state["platform_admin"] is False
    assert state["companies"][0]["company_id"] == result["company_id"]
    task = result["models"][0]
    payload = {"model_id": task["id"], "idempotency_key": "local-lab-session-task-one",
               "expected_capability_version": task["expected_capability_version"],
               "expected_quote_revision": task["expected_quote_revision"], "request_payload": task["request_payload"]}
    denied = api.client.post(f"/api/v1/companies/{result['company_id']}/tasks", headers={"Cookie": cookie_header}, json=payload)
    assert denied.status_code == 403
    headers = {"Cookie": cookie_header, "Origin": TARGETS["gateway_base"], "X-CSRF-Token": state["csrf_token"],
               "X-Company-ID": result["company_id"]}
    accepted = api.client.post(f"/api/v1/companies/{result['company_id']}/tasks", headers=headers, json=payload)
    assert accepted.status_code == 201, accepted.text
    replay = api.client.post(f"/api/v1/companies/{result['company_id']}/tasks", headers=headers, json=payload)
    assert replay.status_code == 201 and replay.json()["id"] == accepted.json()["id"]
    with app.state.session_factory() as session:
        auth_session = session.scalars(select(AuthSession)).one()
        external = session.scalars(select(ExternalIdentity)).one()
        assert auth_session.amr == ["local_lab_bootstrap"]
        assert external.issuer.startswith("urn:ai-video:local-video-lab:")
    arbitrary = api.client.post(LOGIN_PATH, headers=api.headers, json={"user_id": result["admin_user_id"]})
    assert arbitrary.status_code == 422


def test_personal_login_upload_and_static_i2v_task_keep_exact_scope(
    lab, internal_headers
):
    app, api, contracts = lab
    result = provision_lab(api, contracts)
    login = api.client.post(
        LOGIN_PATH,
        headers=api.headers,
        json={"scope_kind": "personal"},
    )
    assert login.status_code == 200, login.text
    assert login.json()["scope_kind"] == "personal"
    assert login.json()["company_id"] is None
    assert (
        login.json()["personal_workspace_id"]
        == result["personal_workspace_id"]
    )
    cookies = login.headers.get_list("set-cookie")
    cookie_header = "; ".join(cookie.split(";", 1)[0] for cookie in cookies)
    session_response = api.client.get(
        "/api/v1/auth/session", headers={"Cookie": cookie_header}
    )
    assert session_response.status_code == 200, session_response.text
    state = session_response.json()
    assert state["authenticated"] is True
    assert state["active_product_context"] == "personal"
    assert state["companies"] == []
    mutation_headers = {
        "Cookie": cookie_header,
        "Origin": TARGETS["gateway_base"],
        "X-CSRF-Token": state["csrf_token"],
    }
    upload = api.client.post(
        "/api/v1/personal/assets",
        headers={
            **mutation_headers,
            "Idempotency-Key": "local-lab-personal-static-asset-001",
        },
        files={
            "file": (
                "director-static.png",
                DIRECTOR_PNG_BYTES,
                "image/png",
            )
        },
        data={"media_type": "image"},
    )
    assert upload.status_code == 201, upload.text
    asset = upload.json()
    assert asset["company_id"] is None
    assert asset["personal_workspace_id"] == result["personal_workspace_id"]

    discovery = api.client.get(
        "/api/v1/personal/models", headers={"Cookie": cookie_header}
    )
    assert discovery.status_code == 200, discovery.text
    model = next(
        item
        for item in discovery.json()
        if (
            "image_to_video" in item["effective_capabilities"]["modes"]
            and "director_shot_v1"
            in item["effective_capabilities"]["modes"]["image_to_video"][
                "structured_inputs"
            ]
        )
    )
    mode = model["effective_capabilities"]["modes"]["image_to_video"]
    role = (
        "first_frame"
        if "first_frame" in mode["input_roles"]
        else "reference_image"
    )
    manifest = _manifest(asset)
    sealed = api.client.post(
        "/api/v1/personal/director-shot-packages",
        headers=mutation_headers,
        json={
            "idempotency_key": "local-lab-personal-director-package-001",
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
    assert package["personal_workspace_id"] == result["personal_workspace_id"]
    payload = {
        "model_id": model["id"],
        "expected_capability_version": model["capability_version"],
        "expected_quote_revision": model["quote_revision"],
        "idempotency_key": "local-lab-personal-static-task-001",
        "request_payload": {
            "mode": "image_to_video",
            "prompt": "保持导演台构图，生成一个稳定的静态机位镜头。",
            "assets": [
                {
                    "asset_id": asset["id"],
                    "media_type": "image",
                    "role": role,
                }
            ],
            "director_shot_package": {
                "package_id": package["package_id"],
                "manifest_sha256": package["manifest_sha256"],
                "sealed_revision": package["sealed_revision"],
            },
            "duration_seconds": min(mode["limits"]["duration_seconds"]),
            "resolution": mode["limits"]["resolutions"][0],
            "aspect_ratio": mode["limits"]["aspect_ratios"][0],
            "output_count": 1,
            "face_enabled": False,
        },
    }
    created = api.client.post(
        "/api/v1/personal/tasks", headers=mutation_headers, json=payload
    )
    assert created.status_code == 201, created.text
    replay = api.client.post(
        "/api/v1/personal/tasks", headers=mutation_headers, json=payload
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == created.json()["id"]

    with app.state.session_factory() as session:
        task = session.get(GenerationTask, created.json()["id"])
        assert task is not None
        assert task.company_id is None
        assert task.personal_workspace_id == result["personal_workspace_id"]
        assert task.user_id == result["personal_user_id"]
        assert task.idempotency_key == payload["idempotency_key"]
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task.id
            )
        )
        assert outbox is not None
        assert outbox.company_id is None
        assert outbox.personal_workspace_id == result["personal_workspace_id"]
        assert outbox.relay_payload["inputs"]["director_shot"] == {
            "manifest": manifest,
            "manifest_sha256": package["manifest_sha256"],
            "sealed_revision": package["sealed_revision"],
        }

    capture = CaptureRelayClient()
    # Preserve the exact route/cost release evidence that admitted the task;
    # only replace its transport method with a deterministic capture sink.
    def submit_with_execution_contract(payload, *, idempotency_key, request_id=None):
        accepted = capture.submit(
            payload,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
        return accepted.model_copy(
            update={
                "execution_contract_sha256": (
                    payload.execution_contract.content_sha256()
                    if payload.execution_contract is not None
                    else None
                )
            }
        )

    app.state.relay_client.submit = submit_with_execution_contract
    dispatched = api.client.post(
        "/internal/relay/dispatch-once", headers=internal_headers
    )
    assert dispatched.status_code == 200, dispatched.text
    assert dispatched.json()["status"] == "sent", dispatched.text
    assert len(capture.calls) == 1
    relay_payload = capture.calls[0][0]
    assert relay_payload.metadata["platform_billing_scope"] == "personal"
    assert (
        relay_payload.metadata["platform_billing_scope_id"]
        == result["personal_workspace_id"]
    )
    assert [item.role for item in relay_payload.inputs.assets] == [role]
    assert relay_payload.inputs.director_shot.manifest == manifest


def test_apply_lock_refuses_concurrent_and_preserves_foreign_owner(tmp_path):
    path = tmp_path / "manifest.json"
    lock = path.with_name(path.name + ".platform-apply.lock")
    with apply_lock(path):
        assert lock.is_file()
        with pytest.raises(LabError, match="lock"):
            with apply_lock(path):
                pytest.fail("must not enter")
        assert lock.is_file()
    assert not lock.exists()
