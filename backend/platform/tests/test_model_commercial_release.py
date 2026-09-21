from __future__ import annotations

import pytest
from sqlalchemy import func, select

from platform_api.models import (
    Company,
    CompanyModelGrant,
    CompanyPointPriceVersion,
    ModelCommercialReleaseExecution,
    ModelCommercialReleasePlan,
    ModelDefinition,
    PersonalRetailModelGrant,
)
from platform_api.relay_catalog_sync_worker import RelayCatalogSyncWorker
from platform_api.relay_client import RelayModelCatalog
from platform_api.services.provider_route_identity import canonical_sha256

from .test_model_capability_v1_contract import (
    _admin_headers,
    _mode,
    canonical_capability,
)
from .test_relay_capability_sync import CatalogRelayClient, _catalog


def _commercial_body(
    *,
    model: dict,
    cost_kind: str = "resolution_output_second",
    formula_billing_unit: str = "per_second",
    assumptions: dict | None = None,
    components: list[dict] | None = None,
    expected_routing_release_sha256: str = "sha256:" + "d" * 64,
) -> dict:
    return {
        "expected_capability_version": model["capability_version"],
        "expected_candidate_revision": model[
            "relay_capability_candidate_revision"
        ],
        "expected_catalog_revision": model[
            "relay_capability_candidate_catalog_revision"
        ],
        "expected_routing_release_sha256": expected_routing_release_sha256,
        "provider_cost_currency": "CNY",
        "provider_cost_formula": {
            "schema_version": 1,
            "kind": cost_kind,
            "platform_billing_unit": formula_billing_unit,
            "source_capability_revision": model[
                "relay_capability_candidate_revision"
            ],
            "assumptions": assumptions
            or {
                "quantity_basis": "relay_effective_capability_ceiling",
                "enforced_limits": {"max_resolution": "2k"},
            },
            "components": components
            or [
                {
                    "component": "output_video_seconds",
                    "rate_micros": 800_000,
                    "quantity_numerator": 1,
                    "quantity_denominator": 1,
                }
            ],
        },
        "provider_cost_evidence_kind": "provider_price_list",
        "provider_cost_evidence_reference": (
            "https://platform.minimaxi.com/docs/guides/pricing-paygo"
        ),
        "provider_cost_evidence_sha256": "1" * 64,
        "provider_cost_effective_at": "2026-09-02T00:00:00Z",
        "fx_cny_micros_per_currency_unit": 1_000_000,
        "fx_source": "CNY identity rate",
        "fx_version": "cny-identity-v1",
        "fx_evidence_sha256": "2" * 64,
        "fx_effective_at": "2026-09-02T00:00:00Z",
        "personal_config_override": {},
        "enterprise_config_override": {},
        "approval_reason": "批准证据绑定的保守商业价格",
        "idempotency_key": f"commercial-plan-{model['id']}",
    }


def _prepare_video_draft(
    app,
    client,
    headers,
    *,
    evidence_status: str = "ready",
    provider_cost_ready: bool = True,
    provider_cost_unit_amount_cents: int = 1,
    provider_cost_source_sha256: str = "1" * 64,
    provider_cost_rate_set: bool = False,
):
    assert not getattr(app.state, "legacy_commercial_gate_isolated", False)
    capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=0,
                max_videos=0,
                max_audio=0,
                supports_face=False,
                durations=[5, 10],
                resolutions=["768p", "2k"],
                output_counts=[1],
                input_media_types=[],
            )
        }
    )
    catalog = _catalog("minimax-h3-max-commercial", capability)
    app.state.relay_client = CatalogRelayClient(
        catalog,
        evidence_status=evidence_status,
        provider_cost_ready=provider_cost_ready,
        provider_cost_billing_unit=(
            "per_second" if provider_cost_rate_set else "output_second"
        ),
        provider_cost_unit_amount_cents=provider_cost_unit_amount_cents,
        provider_cost_source_sha256=provider_cost_source_sha256,
        provider_cost_rate_set=provider_cost_rate_set,
    )
    reconciled = client.post(
        "/api/v1/platform-admin/relay-models/reconcile", headers=headers
    )
    assert reconciled.status_code == 200, reconciled.text
    model_id = reconciled.json()["created_model_ids"][0]
    detail = client.get(
        f"/api/v1/platform-admin/models/{model_id}", headers=headers
    )
    assert detail.status_code == 200, detail.text
    return detail.json()


def test_approved_plan_releases_once_after_route_acceptance(
    app, client
) -> None:
    headers = _admin_headers(client, "commercial-release")
    model = _prepare_video_draft(
        app,
        client,
        headers,
        # Exercise the multi-component rate-set proof path end to end.
        provider_cost_rate_set=True,
    )
    with app.state.session_factory.begin() as session:
        session.add(
            Company(
                id="00000000-0000-4000-8000-000000000501",
                name="Points company",
                billing_version=2,
            )
        )

    approved = client.put(
        f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan",
        headers=headers,
        json=_commercial_body(model=model),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "approved"
    # ¥0.80 / (1 - 30%) * 10 points/CNY, rounded up.
    assert approved.json()["minimum_price_points"] == 12
    with app.state.session_factory() as session:
        draft = session.get(ModelDefinition, model["id"])
        assert draft is not None and draft.published_at is None
        assert session.scalar(select(PersonalRetailModelGrant.id)) is None

    worker = RelayCatalogSyncWorker(
        app.state.session_factory, app.state.relay_client
    )
    worker_outcome = worker.run_once()
    assert worker_outcome.commercial_release is not None
    assert worker_outcome.commercial_release.released_count == 1
    assert worker_outcome.commercial_release.blocked_count == 0
    assert worker_outcome.commercial_release.items[0]["state"] == "released"
    assert (
        worker_outcome.commercial_release.items[0]["company_grant_count"] == 1
    )
    released_item = worker_outcome.commercial_release.items[0]
    receipt = released_item["publication_receipt"]
    assert receipt["schema_version"] == 4
    route_release = receipt["relay_route_release"]
    assert route_release["model_release_id"] == (
        "release-minimax-h3-max-commercial"
    )
    assert route_release["model_release_revision"] == "release-revision-1"
    assert route_release["published_route_revision"] == (
        "sha256:" + "c" * 64
    )
    assert route_release["routing_release_sha256"] == "sha256:" + "d" * 64
    assert route_release["route_identity_sha256"] == (
        released_item["released_route_identity_sha256"]
    )
    assert route_release["route_identity"] == (
        released_item["approved_route_identity"]
    )
    assert route_release["route_release_evidence_sha256"] == canonical_sha256(
        released_item["route_release_evidence"]
    )

    original_catalog = app.state.relay_client.catalog
    unrelated = _catalog(
        "unrelated-published-route",
        original_catalog.data[0].capabilities.contract_dump(),
    ).data[0]
    expanded_catalog = RelayModelCatalog.model_validate(
        {
            "api_version": "v1",
            "schema_version": 1,
            "object": "list",
            "catalog_revision_scope": "transport_snapshot",
            "published_route_revision": "sha256:" + "8" * 64,
            "catalog_revision": "sha256:" + "9" * 64,
            "data": [original_catalog.data[0], unrelated],
        }
    )
    expanded_client = CatalogRelayClient(
        expanded_catalog,
        provider_cost_billing_unit="per_second",
        provider_cost_source_sha256="1" * 64,
        provider_cost_rate_set=True,
    )
    app.state.relay_client = expanded_client
    worker.relay_client = expanded_client
    continuous = worker.run_once()
    assert expanded_client.evidence_calls == 1
    assert continuous.commercial_release is not None
    assert continuous.commercial_release.released_count == 0
    assert continuous.commercial_release.items[0]["state"] == "released", (
        continuous.commercial_release.items[0]["last_blocker_code"]
    )
    assert continuous.commercial_release.unchanged_count == 1, (
        continuous.commercial_release.items
    )

    repeated = client.post(
        "/api/v1/platform-admin/model-commercial-releases/reconcile",
        headers=headers,
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["released_count"] == 0
    assert repeated.json()["unchanged_count"] == 1

    with app.state.session_factory() as session:
        stored = session.get(ModelDefinition, model["id"])
        assert stored is not None and stored.active is True
        personal = session.scalar(
            select(PersonalRetailModelGrant).where(
                PersonalRetailModelGrant.model_id == model["id"]
            )
        )
        assert personal is not None
        assert personal.enabled is True
        assert personal.price_per_second_points == 12
        company_grant = session.scalar(
            select(CompanyModelGrant).where(
                CompanyModelGrant.model_id == model["id"]
            )
        )
        assert company_grant is not None
        assert company_grant.enabled is True
        assert company_grant.price_per_second_points == 12
        assert session.scalar(
            select(func.count(CompanyPointPriceVersion.id))
        ) == 1
        assert session.scalar(
            select(func.count(ModelCommercialReleasePlan.id))
        ) == 1
        execution = session.scalar(select(ModelCommercialReleaseExecution))
        assert execution is not None
        assert execution.attempt_count == 1


@pytest.mark.parametrize(
    ("drift_kind", "expected_blocker"),
    [
        ("reviewed_candidate", "live_catalog_drift"),
        ("published_route_revision", "published_route_revision_drift"),
    ],
)
def test_worker_blocks_released_route_drift_and_never_auto_reactivates(
    app, client, drift_kind, expected_blocker
) -> None:
    headers = _admin_headers(
        client, f"commercial-release-drift-{drift_kind}"
    )
    model = _prepare_video_draft(
        app,
        client,
        headers,
        provider_cost_rate_set=True,
    )
    with app.state.session_factory.begin() as session:
        session.add(
            Company(
                id="00000000-0000-4000-8000-000000000502",
                name="Downgrade company",
                billing_version=2,
            )
        )
    approved = client.put(
        f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan",
        headers=headers,
        json=_commercial_body(model=model),
    )
    assert approved.status_code == 200, approved.text

    published_client = app.state.relay_client
    worker = RelayCatalogSyncWorker(
        app.state.session_factory, published_client
    )
    released = worker.run_once()
    assert released.commercial_release is not None
    assert released.commercial_release.items[0]["state"] == "released"

    original_catalog = published_client.catalog
    if drift_kind == "reviewed_candidate":
        drift_catalog = _catalog(
            original_catalog.data[0].id,
            original_catalog.data[0].capabilities.contract_dump(),
            capability_revision=original_catalog.data[0].capability_revision,
            catalog_revision="sha256:" + "4" * 64,
            lifecycle="reviewed_candidate",
            managed_route=False,
            customer_callable=False,
            published_route_revision="",
        )
        drift_client = CatalogRelayClient(drift_catalog)
    else:
        drift_item = original_catalog.data[0].model_copy(
            update={"published_route_revision": "sha256:" + "6" * 64}
        )
        drift_catalog = original_catalog.model_copy(
            update={
                "catalog_revision": "sha256:" + "4" * 64,
                "published_route_revision": "sha256:" + "7" * 64,
                "data": [drift_item],
            }
        )
        drift_client = CatalogRelayClient(
            drift_catalog,
            provider_cost_billing_unit="per_second",
            provider_cost_source_sha256="1" * 64,
            provider_cost_rate_set=True,
        )
    app.state.relay_client = drift_client
    worker.relay_client = drift_client
    invalidated = worker.run_once()
    assert invalidated.reconciliation is not None
    assert invalidated.reconciliation.invalidated_model_ids == (model["id"],)
    assert invalidated.commercial_release is not None
    blocked = invalidated.commercial_release.items[0]
    assert blocked["state"] == "blocked"
    assert blocked["last_blocker_code"] == expected_blocker
    assert blocked["publication_receipt"] is not None

    with app.state.session_factory() as session:
        stored = session.get(ModelDefinition, model["id"])
        assert stored is not None and stored.active is False
        assert all(
            grant.enabled is False
            for grant in session.scalars(
                select(CompanyModelGrant).where(
                    CompanyModelGrant.model_id == model["id"]
                )
            ).all()
        )
        personal = session.scalar(
            select(PersonalRetailModelGrant).where(
                PersonalRetailModelGrant.model_id == model["id"]
            )
        )
        assert personal is not None and personal.enabled is False

    restored_catalog = original_catalog.model_copy(
        update={"catalog_revision": "sha256:" + "5" * 64}
    )
    restored_client = CatalogRelayClient(
        restored_catalog,
        provider_cost_billing_unit="per_second",
        provider_cost_source_sha256="1" * 64,
        provider_cost_rate_set=True,
    )
    app.state.relay_client = restored_client
    worker.relay_client = restored_client
    restored = worker.run_once()
    assert restored.commercial_release is not None
    assert restored.commercial_release.released_count == 0
    assert restored.commercial_release.unchanged_count == 1
    still_blocked = restored.commercial_release.items[0]
    assert still_blocked["state"] == "blocked"
    assert still_blocked["last_blocker_code"] == expected_blocker
    with app.state.session_factory() as session:
        stored = session.get(ModelDefinition, model["id"])
        assert stored is not None and stored.active is False
        assert all(
            grant.enabled is False
            for grant in session.scalars(
                select(CompanyModelGrant).where(
                    CompanyModelGrant.model_id == model["id"]
                )
            ).all()
        )
        personal = session.scalar(
            select(PersonalRetailModelGrant).where(
                PersonalRetailModelGrant.model_id == model["id"]
            )
        )
        assert personal is not None and personal.enabled is False


def test_unaccepted_route_keeps_plan_and_model_blocked(app, client) -> None:
    headers = _admin_headers(client, "commercial-blocked")
    model = _prepare_video_draft(
        app, client, headers, evidence_status="blocked"
    )
    approved = client.put(
        f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan",
        headers=headers,
        json=_commercial_body(model=model),
    )
    assert approved.status_code == 200, approved.text

    blocked = client.post(
        "/api/v1/platform-admin/model-commercial-releases/reconcile",
        headers=headers,
    )
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["released_count"] == 0
    assert blocked.json()["blocked_count"] == 1
    item = blocked.json()["items"][0]
    assert item["state"] == "blocked"
    assert item["last_blocker_code"] == "route_acceptance_pending"
    with app.state.session_factory() as session:
        stored = session.get(ModelDefinition, model["id"])
        assert stored is not None and stored.published_at is None
        assert session.scalar(select(CompanyModelGrant.id)) is None
        assert session.scalar(select(PersonalRetailModelGrant.id)) is None


def test_route_acceptance_cannot_release_without_exact_provider_cost_coverage(
    app, client
) -> None:
    headers = _admin_headers(client, "commercial-cost-blocked")
    model = _prepare_video_draft(
        app,
        client,
        headers,
        provider_cost_ready=False,
    )
    approved = client.put(
        f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan",
        headers=headers,
        json=_commercial_body(model=model),
    )
    assert approved.status_code == 200, approved.text

    blocked = client.post(
        "/api/v1/platform-admin/model-commercial-releases/reconcile",
        headers=headers,
    )
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["released_count"] == 0
    assert blocked.json()["blocked_count"] == 1
    item = blocked.json()["items"][0]
    assert item["state"] == "blocked"
    assert item["last_blocker_code"] == "provider_cost_unready"
    assert "合同费率" in item["last_blocker_message"]
    with app.state.session_factory() as session:
        stored = session.get(ModelDefinition, model["id"])
        assert stored is not None and stored.published_at is None
        assert session.scalar(select(CompanyModelGrant.id)) is None
        assert session.scalar(select(PersonalRetailModelGrant.id)) is None


def test_ready_route_with_a_different_price_snapshot_cannot_release(
    app, client
) -> None:
    headers = _admin_headers(client, "commercial-cost-snapshot-drift")
    model = _prepare_video_draft(
        app,
        client,
        headers,
        provider_cost_source_sha256="c" * 64,
    )
    approved = client.put(
        f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan",
        headers=headers,
        json=_commercial_body(model=model),
    )
    assert approved.status_code == 200, approved.text

    blocked = client.post(
        "/api/v1/platform-admin/model-commercial-releases/reconcile",
        headers=headers,
    )
    assert blocked.status_code == 200, blocked.text
    item = blocked.json()["items"][0]
    assert item["state"] == "blocked"
    assert item["last_blocker_code"] == "provider_cost_plan_mismatch"
    with app.state.session_factory() as session:
        stored = session.get(ModelDefinition, model["id"])
        assert stored is not None and stored.published_at is None
        assert session.scalar(select(CompanyModelGrant.id)) is None
        assert session.scalar(select(PersonalRetailModelGrant.id)) is None


def test_ready_route_with_a_higher_exact_rate_than_the_plan_cannot_release(
    app, client
) -> None:
    headers = _admin_headers(client, "commercial-cost-understated")
    model = _prepare_video_draft(
        app,
        client,
        headers,
        # The approved formula below is CNY 0.80 per output second. A current
        # Relay rectangle at CNY 0.81 must block distribution even though it
        # is otherwise complete and cites the same source document.
        provider_cost_unit_amount_cents=81,
    )
    approved = client.put(
        f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan",
        headers=headers,
        json=_commercial_body(model=model),
    )
    assert approved.status_code == 200, approved.text

    blocked = client.post(
        "/api/v1/platform-admin/model-commercial-releases/reconcile",
        headers=headers,
    )
    assert blocked.status_code == 200, blocked.text
    item = blocked.json()["items"][0]
    assert item["state"] == "blocked"
    assert item["last_blocker_code"] == "provider_cost_plan_mismatch"
    with app.state.session_factory() as session:
        stored = session.get(ModelDefinition, model["id"])
        assert stored is not None and stored.published_at is None
        assert session.scalar(select(CompanyModelGrant.id)) is None
        assert session.scalar(select(PersonalRetailModelGrant.id)) is None


def test_h3_output_second_price_requires_a_real_input_cost_restriction(
    app, client
) -> None:
    headers = _admin_headers(client, "commercial-h3-restricted")
    capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=0,
                max_videos=0,
                max_audio=0,
                supports_face=False,
                durations=[4, 15],
                resolutions=["768p", "2k"],
                output_counts=[1],
                input_media_types=[],
            ),
            "image_to_video": _mode(
                max_images=9,
                max_videos=0,
                max_audio=3,
                supports_face=False,
                durations=[4, 15],
                resolutions=["768p", "2k"],
                output_counts=[1],
                input_media_types=["image", "audio"],
            ),
            "video_to_video": _mode(
                max_images=6,
                max_videos=3,
                max_audio=3,
                supports_face=False,
                durations=[4, 15],
                resolutions=["768p", "2k"],
                output_counts=[1],
                input_media_types=["image", "video", "audio"],
            ),
        }
    )
    catalog = _catalog("minimax-h3-commercial", capability)
    app.state.relay_client = CatalogRelayClient(
        catalog,
        provider_cost_billing_unit="per_second",
        provider_cost_source_sha256="1" * 64,
        provider_cost_rate_set=True,
    )
    reconciled = client.post(
        "/api/v1/platform-admin/relay-models/reconcile", headers=headers
    )
    assert reconciled.status_code == 200, reconciled.text
    model_id = reconciled.json()["created_model_ids"][0]
    model = client.get(
        f"/api/v1/platform-admin/models/{model_id}", headers=headers
    ).json()

    unsafe = _commercial_body(
        model=model,
        assumptions={
            "quantity_basis": "relay_effective_capability_ceiling",
            "enforced_limits": {"max_resolution": "2k"},
        },
    )
    rejected = client.put(
        f"/api/v1/platform-admin/models/{model_id}/commercial-release-plan",
        headers=headers,
        json=unsafe,
    )
    assert rejected.status_code == 409, rejected.text

    text_mode = capability["modes"]["text_to_video"]
    image_mode = {
        **capability["modes"]["image_to_video"],
        "input_media_types": ["image"],
        "limits": {
            **capability["modes"]["image_to_video"]["limits"],
            "max_images": 5,
            "max_audio": 0,
        },
    }
    enterprise_override = canonical_capability(
        modes={"text_to_video": text_mode, "image_to_video": image_mode}
    )
    safe = _commercial_body(
        model=model,
        assumptions={
            "quantity_basis": "relay_effective_capability_ceiling",
            "enforced_limits": {
                "max_resolution": "2k",
                "max_reference_images": 5,
                "included_reference_images": 5,
            },
        },
    )
    safe["enterprise_config_override"] = enterprise_override
    safe["personal_config_override"] = canonical_capability(
        modes={"text_to_video": text_mode}
    )
    approved = client.put(
        f"/api/v1/platform-admin/models/{model_id}/commercial-release-plan",
        headers=headers,
        json=safe,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["provider_cost_formula"]["components"][0][
        "component"
    ] == "output_second"


def test_output_item_price_list_plan_uses_relay_metric_identity(
    app, client
) -> None:
    headers = _admin_headers(client, "commercial-seedream-item")
    capability = canonical_capability(
        modes={
            "text_to_image": _mode(
                max_images=0,
                max_videos=0,
                max_audio=0,
                supports_face=False,
                durations=[1],
                resolutions=["2048x2048"],
                output_counts=[1, 2],
                input_media_types=[],
            )
        }
    )
    catalog = _catalog("seedream-5-commercial", capability)
    app.state.relay_client = CatalogRelayClient(
        catalog,
        provider_cost_billing_unit="output_item",
        provider_cost_source_sha256="1" * 64,
    )
    reconciled = client.post(
        "/api/v1/platform-admin/relay-models/reconcile", headers=headers
    )
    assert reconciled.status_code == 200, reconciled.text
    model_id = reconciled.json()["created_model_ids"][0]
    model = client.get(
        f"/api/v1/platform-admin/models/{model_id}", headers=headers
    ).json()
    body = _commercial_body(
        model=model,
        cost_kind="output_item",
        formula_billing_unit="per_item",
        assumptions={
            "quantity_basis": "relay_effective_capability_ceiling",
            "enforced_limits": {"max_output_count": 2},
        },
        components=[
            {
                # The compatibility alias is accepted at the edge but the
                # immutable plan stores Relay's canonical output_item metric.
                "component": "generated_images",
                "rate_micros": 220_000,
                "quantity_numerator": 1,
                "quantity_denominator": 1,
            }
        ],
    )
    body["provider_cost_evidence_reference"] = (
        "https://www.volcengine.com/product/ark"
    )
    approved = client.put(
        f"/api/v1/platform-admin/models/{model_id}/commercial-release-plan",
        headers=headers,
        json=body,
    )
    assert approved.status_code == 200, approved.text
    payload = approved.json()
    assert payload["provider_cost_formula"]["components"][0]["component"] == (
        "output_item"
    )
    assert payload["minimum_price_points"] == 4


def test_token_cost_without_relay_enforced_token_and_media_limits_is_rejected(
    app, client
) -> None:
    headers = _admin_headers(client, "commercial-omni-blocked")
    model = _prepare_video_draft(
        app,
        client,
        headers,
        # Runtime actual-cost coverage is deliberately ready. It cannot
        # substitute for a customer pre-reservation ceiling.
        provider_cost_rate_set=True,
    )
    body = _commercial_body(
        model=model,
        cost_kind="token_total",
        formula_billing_unit="per_second",
        assumptions={
            "quantity_basis": "relay_effective_capability_ceiling",
            "enforced_limits": {
                "max_total_tokens": 100_000,
                "media_resolution": "high",
            },
        },
        components=[
            {
                "component": "total_token",
                "rate_micros": 1_000_000,
                "quantity_numerator": 100_000,
                "quantity_denominator": 1_000_000,
            }
        ],
    )
    # The model and formula are both per-second. Rejection is specifically
    # caused by the missing executable token/media ceilings, not a unit typo.
    rejected = client.put(
        f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan",
        headers=headers,
        json=body,
    )
    assert rejected.status_code == 409, rejected.text
    with app.state.session_factory() as session:
        assert session.scalar(select(ModelCommercialReleasePlan.id)) is None
