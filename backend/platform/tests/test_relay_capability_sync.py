from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from platform_api.models import (
    AuditLog,
    CompanyModelGrant,
    ModelDefinition,
    PersonalRetailModelGrant,
    RelaySubmissionOutbox,
)
from platform_api.relay_client import (
    RelayModelCatalog,
    RelayModelCatalogRead,
    RelayModelReleaseEvidence,
)
from platform_api.services.relay_capabilities import RelayCapabilityService

from .test_model_capability_v1_contract import (
    _admin_headers,
    _create_model,
    _grant,
    _mode,
    _publish,
    canonical_capability,
)


REVISION = "sha256:" + ("a" * 64)
CATALOG_REVISION = "sha256:" + ("b" * 64)
PUBLISHED_ROUTE_REVISION = "sha256:" + ("c" * 64)


def _recharge(client, tenant, tenant_headers, *, suffix: str) -> None:
    response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/wallet/recharge",
        headers=tenant_headers,
        json={
            "amount_cents": 10_000,
            "idempotency_key": f"relay-sync-recharge-{suffix}",
            "note": "relay capability sync test",
        },
    )
    assert response.status_code == 200, response.text


# A customer-unit cost ceiling is denominated in the Platform billing unit, so
# a rate-set rectangle must carry per_second/per_item. Relay metric names are
# only valid on the legacy contract-rate shape. Callers that already pass a
# Platform unit are left untouched - this only translates a metric name.
_RELAY_TO_PLATFORM_BILLING_UNIT = {
    "output_second": "per_second",
    "output_item": "per_item",
}


class CatalogRelayClient:
    def __init__(
        self,
        catalog: RelayModelCatalog,
        *,
        evidence_status: str = "ready",
        evidence_revision: str | None = None,
        provider_cost_ready: bool = True,
        provider_cost_blocker: str = "provider_contract_rate_missing",
        provider_cost_billing_unit: str = "output_item",
        provider_cost_unit_amount_cents: int = 1,
        provider_cost_currency: str = "CNY",
        provider_cost_source_sha256: str = "c" * 64,
        provider_cost_rate_set: bool = False,
    ):
        self.catalog = catalog
        self.evidence_calls = 0
        generated_at = datetime.now(timezone.utc)
        ready = evidence_status == "ready"
        evidence_models = []
        for item in catalog.data:
            if not item.customer_callable:
                evidence_models.append(
                    {
                        "public_model_id": item.id,
                        "capability_revision": item.capability_revision,
                        "published_route_revision": None,
                        "routing_release_sha256": "sha256:" + "d" * 64,
                        "provider_cost_readiness_sha256": (
                            "sha256:" + "e" * 64
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
                )
                continue
            test_modes = sorted(item.capabilities.modes)
            cost_rectangles = []
            for mode_name in test_modes:
                mode = item.capabilities.modes[mode_name]
                for resolution in sorted(mode.limits.resolutions):
                    rectangle = {
                        "mode": mode_name,
                        "resolution": resolution,
                        "ready": provider_cost_ready,
                    }
                    if provider_cost_ready:
                        rate_id = str(
                            uuid5(
                                NAMESPACE_URL,
                                f"test-rate:{item.id}:{mode_name}:{resolution}",
                            )
                        )
                        rectangle.update(
                            cost_revision_sha256="sha256:" + "7" * 64,
                            billing_unit=provider_cost_billing_unit,
                            effective_from=(
                                generated_at - timedelta(days=1)
                            ),
                            source_document_sha256=provider_cost_source_sha256,
                        )
                        if provider_cost_rate_set:
                            rectangle["rate_set_id"] = rate_id
                            # A rate-set proof carries a customer-unit ceiling,
                            # which the evidence model only accepts on a Platform
                            # billing unit. Translate a Relay metric name; leave
                            # an already-Platform unit (the convention this suite
                            # uses when it asks for a rate set) alone.
                            rectangle["billing_unit"] = (
                                _RELAY_TO_PLATFORM_BILLING_UNIT.get(
                                    provider_cost_billing_unit,
                                    provider_cost_billing_unit,
                                )
                            )
                            rectangle["customer_unit_cost_ceiling_cny_micros"] = provider_cost_unit_amount_cents * 10_000
                            rectangle["customer_unit_cost_ceiling_revision_sha256"] = "sha256:" + "8" * 64
                        else:
                            rectangle.update(
                                contract_rate_id=rate_id,
                                unit_amount_cents=provider_cost_unit_amount_cents,
                                currency=provider_cost_currency,
                            )
                    else:
                        rectangle["blocker_code"] = provider_cost_blocker
                    cost_rectangles.append(rectangle)
            cost_count = len(cost_rectangles)
            cost_ready_count = cost_count if provider_cost_ready else 0
            evidence_models.append(
                {
                    "public_model_id": item.id,
                    "capability_revision": (
                        evidence_revision or item.capability_revision
                    ),
                    "model_release_id": f"release-{item.id}",
                    "model_release_revision": "release-revision-1",
                    "published_route_revision": (
                        item.published_route_revision or None
                    ),
                    "routing_release_sha256": "sha256:" + "d" * 64,
                    "provider_cost_readiness_sha256": (
                        "sha256:" + "e" * 64
                    ),
                    "provider_cost_ready": provider_cost_ready,
                    "provider_cost_rectangle_count": cost_count,
                    "provider_cost_ready_rectangle_count": cost_ready_count,
                    "route_count": 1,
                    "enabled_route_count": 1,
                    "accepted_route_count": 1 if ready else 0,
                    "fresh_test_count": 1 if ready else 0,
                    "latest_successful_test_at": (
                        generated_at - timedelta(seconds=10)
                        if ready
                        else None
                    ),
                    "status": evidence_status,
                    "routes": [
                        {
                            "route_id": f"route-{item.id}",
                            "channel_id": 17,
                            "provider_name": "test-provider",
                            "provider_account_id": "test-account-a",
                            "provider_key_index": 0,
                            "provider_key_fingerprint_prefix": "a" * 12,
                            "provider_credential_set_version": (
                                "11111111-1111-4111-8111-111111111111"
                            ),
                            "route_binding_sha256": "sha256:" + "f" * 64,
                            "upstream_model": f"provider-{item.id}",
                            "adapter_profile_id": "test-profile-v1",
                            "adapter_profile_revision": "profile-revision-1",
                            "enabled": True,
                            "accepted": ready,
                            "fresh": ready,
                            "latest_successful_test_at": (
                                generated_at - timedelta(seconds=10)
                                if ready
                                else None
                            ),
                            "fresh_until": (
                                generated_at + timedelta(seconds=890)
                                if ready
                                else None
                            ),
                            "required_test_modes": test_modes,
                            "fresh_test_modes": test_modes if ready else [],
                            "provider_cost_ready": provider_cost_ready,
                            "provider_cost_rectangle_count": cost_count,
                            "provider_cost_ready_rectangle_count": (
                                cost_ready_count
                            ),
                            "provider_cost_rectangles": cost_rectangles,
                        }
                    ],
                }
            )
        self.evidence = RelayModelReleaseEvidence.model_validate(
            {
                "schema_version": 1,
                "object": "relay.model_release_evidence",
                "catalog_revision": catalog.catalog_revision,
                "catalog_revision_scope": catalog.catalog_revision_scope,
                "published_route_revision": (
                    catalog.published_route_revision
                ),
                "generated_at": generated_at,
                "test_freshness_max_age_seconds": 900,
                "models": evidence_models,
            }
        )

    def get_model_catalog(self, **_) -> RelayModelCatalogRead:
        return RelayModelCatalogRead(
            catalog=self.catalog,
            etag=f'"{self.catalog.catalog_revision}"',
            not_modified=False,
        )

    def get_model_release_evidence(self, **_) -> RelayModelReleaseEvidence:
        self.evidence_calls += 1
        return self.evidence


def _catalog(
    model_id: str,
    capability: dict,
    *,
    capability_revision: str = REVISION,
    catalog_revision: str = CATALOG_REVISION,
    lifecycle: str = "published_route",
    managed_route: bool = True,
    customer_callable: bool = True,
    published_route_revision: str = PUBLISHED_ROUTE_REVISION,
) -> RelayModelCatalog:
    return RelayModelCatalog.model_validate(
        {
            "api_version": "v1",
            "schema_version": 1,
            "object": "list",
            "catalog_revision_scope": "transport_snapshot",
            "published_route_revision": PUBLISHED_ROUTE_REVISION,
            "catalog_revision": catalog_revision,
            "data": [
                {
                    "api_version": "v1",
                    "schema_version": 1,
                    "id": model_id,
                    "object": "model",
                    "capability_revision": capability_revision,
                    "lifecycle": lifecycle,
                    "managed_route": managed_route,
                    "customer_callable": customer_callable,
                    "published_route_revision": published_route_revision,
                    "capabilities": capability,
                }
            ],
        }
    )


def _face_capability_v2() -> dict:
    capability = canonical_capability(
        modes={"text_to_video": _mode(supports_face=True)}
    )
    capability["schema_version"] = 2
    capability["modes"]["text_to_video"][
        "conditional_required_resource_keys"
    ] = {"face_enabled": ["face.library"]}
    return capability


def test_relay_reconcile_materializes_an_unpublished_draft_idempotently(
    app, client
) -> None:
    headers = _admin_headers(client, "relay-auto-draft")
    capability = canonical_capability(
        modes={
            "text_to_image": _mode(
                max_images=4,
                max_videos=0,
                max_audio=0,
                input_media_types=["image"],
                output_counts=[1, 2],
            )
        }
    )
    app.state.relay_client = CatalogRelayClient(
        _catalog("seedream-auto-draft", capability)
    )

    # Discovery remains a read: the UI must explicitly request reconciliation.
    read_only = client.get(
        "/api/v1/platform-admin/relay-models", headers=headers
    )
    assert read_only.status_code == 200, read_only.text
    assert read_only.json()["items"][0]["status"] == "unmapped"
    with app.state.session_factory() as session:
        assert session.scalar(
            select(ModelDefinition).where(
                ModelDefinition.slug == "seedream-auto-draft"
            )
        ) is None

    reconciled = client.post(
        "/api/v1/platform-admin/relay-models/reconcile", headers=headers
    )
    assert reconciled.status_code == 200, reconciled.text
    payload = reconciled.json()
    assert payload["created_count"] == 1
    assert payload["synced_count"] == 1
    assert payload["unchanged_count"] == 0
    assert len(payload["created_model_ids"]) == 1
    assert payload["created_model_ids"] == payload["synced_model_ids"]
    assert payload["reconciliation_audit_id"]
    assert payload["items"][0]["status"] == "identical"
    assert payload["items"][0]["platform_active"] is False
    assert payload["items"][0]["requires_approval"] is True
    assert payload["items"][0]["provider_cost_ready"] is True
    assert payload["items"][0]["provider_cost_rectangle_count"] == 2
    assert payload["items"][0]["provider_cost_ready_rectangle_count"] == 2
    assert payload["items"][0]["provider_cost_readiness_sha256"] == (
        "sha256:" + "e" * 64
    )
    cost_rectangles = payload["items"][0]["routes"][0][
        "provider_cost_rectangles"
    ]
    assert len(cost_rectangles) == 2
    assert {rectangle["mode"] for rectangle in cost_rectangles} == {
        "text_to_image"
    }
    assert all(rectangle["ready"] is True for rectangle in cost_rectangles)
    assert all(
        rectangle["billing_unit"] == "output_item"
        for rectangle in cost_rectangles
    )
    assert all(rectangle["unit_amount_cents"] == 1 for rectangle in cost_rectangles)
    assert all(
        rectangle["source_document_sha256"] == "c" * 64
        for rectangle in cost_rectangles
    )
    assert reconciled.headers["Cache-Control"] == "private, no-store"

    model_id = payload["created_model_ids"][0]
    detail = client.get(
        f"/api/v1/platform-admin/models/{model_id}", headers=headers
    )
    assert detail.status_code == 200, detail.text
    model = detail.json()
    assert model["slug"] == "seedream-auto-draft"
    assert model["display_name"] == "seedream-auto-draft"
    assert model["provider_key"] == "relay"
    assert model["billing_mode"] == "per_item"
    assert model["status"] == "draft"
    assert model["active"] is False
    assert model["published_at"] is None
    assert model["relay_capability_revision"] is None
    assert model["relay_capability_approved_ceiling"] is None
    assert model["relay_capability_candidate_revision"] == REVISION
    assert model["relay_capability_candidate"] == capability
    assert model["capabilities"] == {"generation": capability}
    first_candidate_synced_at = model[
        "relay_capability_candidate_synced_at"
    ]

    repeated = client.post(
        "/api/v1/platform-admin/relay-models/reconcile", headers=headers
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["created_count"] == 0
    assert repeated.json()["synced_count"] == 0
    assert repeated.json()["unchanged_count"] == 1
    assert repeated.json()["created_model_ids"] == []
    assert repeated.json()["synced_model_ids"] == []
    assert repeated.json()["reconciliation_audit_id"] is None
    repeated_detail = client.get(
        f"/api/v1/platform-admin/models/{model_id}", headers=headers
    ).json()
    assert (
        repeated_detail["relay_capability_candidate_synced_at"]
        == first_candidate_synced_at
    )

    with app.state.session_factory() as session:
        models = session.scalars(
            select(ModelDefinition).where(
                ModelDefinition.slug == "seedream-auto-draft"
            )
        ).all()
        assert len(models) == 1
        assert session.scalars(
            select(CompanyModelGrant).where(
                CompanyModelGrant.model_id == model_id
            )
        ).all() == []
        assert session.scalars(
            select(PersonalRetailModelGrant).where(
                PersonalRetailModelGrant.model_id == model_id
            )
        ).all() == []
        actions = session.scalars(
            select(AuditLog.action).where(
                AuditLog.target_id.in_([model_id, CATALOG_REVISION])
            )
        ).all()
        assert actions.count("model.create") == 1
        assert actions.count("model.relay_capability.candidate_sync") == 1
        assert actions.count("model.relay_catalog.reconcile") == 1


def test_manual_reconcile_rejects_snapshot_mismatch_before_database_mutation(
    app, client
) -> None:
    headers = _admin_headers(client, "relay-snapshot-mismatch")
    capability = canonical_capability(
        modes={"text_to_image": _mode(output_counts=[1])}
    )
    relay = CatalogRelayClient(_catalog("snapshot-mismatch", capability))
    relay.evidence = relay.evidence.model_copy(
        update={"catalog_revision": "sha256:" + "9" * 64}
    )
    app.state.relay_client = relay
    with app.state.session_factory() as session:
        before_audit_ids = set(session.scalars(select(AuditLog.id)).all())

    rejected = client.post(
        "/api/v1/platform-admin/relay-models/reconcile", headers=headers
    )
    assert rejected.status_code == 502, rejected.text
    with app.state.session_factory() as session:
        assert session.scalars(select(ModelDefinition)).all() == []
        assert set(session.scalars(select(AuditLog.id)).all()) == before_audit_ids


def test_relay_reconcile_infers_single_output_video_billing_and_syncs_drift(
    app, client
) -> None:
    headers = _admin_headers(client, "relay-auto-video")
    capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=1,
                max_videos=0,
                max_audio=0,
                input_media_types=["image"],
                output_counts=[1],
            )
        }
    )
    app.state.relay_client = CatalogRelayClient(
        _catalog("video-auto-draft", capability)
    )
    created = client.post(
        "/api/v1/platform-admin/relay-models/reconcile", headers=headers
    )
    assert created.status_code == 200, created.text
    model_id = created.json()["created_model_ids"][0]
    detail = client.get(
        f"/api/v1/platform-admin/models/{model_id}", headers=headers
    ).json()
    assert detail["billing_mode"] == "per_second"

    drifted = deepcopy(capability)
    drifted["modes"]["text_to_video"]["limits"]["max_images"] = 2
    next_revision = "sha256:" + "c" * 64
    next_catalog_revision = "sha256:" + "d" * 64
    app.state.relay_client = CatalogRelayClient(
        _catalog(
            "video-auto-draft",
            drifted,
            capability_revision=next_revision,
            catalog_revision=next_catalog_revision,
        )
    )
    synced = client.post(
        "/api/v1/platform-admin/relay-models/reconcile", headers=headers
    )
    assert synced.status_code == 200, synced.text
    assert synced.json()["created_count"] == 0
    assert synced.json()["synced_count"] == 1
    assert synced.json()["unchanged_count"] == 0
    refreshed = client.get(
        f"/api/v1/platform-admin/models/{model_id}", headers=headers
    ).json()
    assert refreshed["relay_capability_candidate_revision"] == next_revision
    assert refreshed["relay_capability_candidate"] == drifted
    assert refreshed["relay_capability_revision"] is None
    # Automatic candidate sync never rewrites the administrator-owned draft.
    assert refreshed["capabilities"] == {"generation": capability}
    assert refreshed["status"] == "draft"


def test_relay_reconcile_fails_closed_for_an_unmappable_public_model_id(
    app, client
) -> None:
    headers = _admin_headers(client, "relay-invalid-public-id")
    capability = canonical_capability(
        modes={"text_to_image": _mode(output_counts=[1])}
    )
    catalog = RelayModelCatalog.model_validate(
        {
            "api_version": "v1",
                "schema_version": 1,
                "object": "list",
                "catalog_revision_scope": "transport_snapshot",
                "published_route_revision": PUBLISHED_ROUTE_REVISION,
            "catalog_revision": CATALOG_REVISION,
            "data": [
                {
                    "api_version": "v1",
                    "schema_version": 1,
                    "id": "aaa-safe-draft",
                        "object": "model",
                        "capability_revision": REVISION,
                        "lifecycle": "published_route",
                        "managed_route": True,
                        "customer_callable": True,
                        "published_route_revision": PUBLISHED_ROUTE_REVISION,
                        "capabilities": capability,
                },
                {
                    "api_version": "v1",
                    "schema_version": 1,
                    "id": "zzz_invalid_model_id",
                        "object": "model",
                        "capability_revision": REVISION,
                        "lifecycle": "published_route",
                        "managed_route": True,
                        "customer_callable": True,
                        "published_route_revision": PUBLISHED_ROUTE_REVISION,
                        "capabilities": capability,
                },
            ],
        }
    )
    app.state.relay_client = CatalogRelayClient(catalog)

    rejected = client.post(
        "/api/v1/platform-admin/relay-models/reconcile", headers=headers
    )
    assert rejected.status_code == 409, rejected.text
    assert "无法安全映射" in rejected.text
    with app.state.session_factory() as session:
        assert session.scalars(
            select(ModelDefinition).where(
                ModelDefinition.slug.in_(
                    ["aaa-safe-draft", "zzz_invalid_model_id"]
                )
            )
        ).all() == []


def test_safe_restriction_treats_relay_documents_as_complete() -> None:
    ceiling = _face_capability_v2()
    omitted = deepcopy(ceiling)
    omitted["modes"]["text_to_video"].pop(
        "conditional_required_resource_keys"
    )
    cleared = deepcopy(ceiling)
    cleared["modes"]["text_to_video"][
        "conditional_required_resource_keys"
    ] = {}
    face_disabled = deepcopy(omitted)
    face_disabled["modes"]["text_to_video"]["supports_face"] = False

    assert not RelayCapabilityService._is_safe_restriction(
        ceiling=ceiling,
        candidate=omitted,
    )
    assert not RelayCapabilityService._is_safe_restriction(
        ceiling=ceiling,
        candidate=cleared,
    )
    assert RelayCapabilityService._is_safe_restriction(
        ceiling=ceiling,
        candidate=face_disabled,
    )


def test_admin_model_update_cannot_drop_an_approved_face_requirement(
    app, client
) -> None:
    headers = _admin_headers(client, "relay-face-ceiling")
    ceiling = _face_capability_v2()
    created = _create_model(
        client,
        headers,
        suffix="relay-face-ceiling",
        capability=ceiling,
    )
    assert created.status_code == 201, created.text
    model = created.json()
    with app.state.session_factory.begin() as session:
        stored = session.get(ModelDefinition, model["id"])
        assert stored is not None
        stored.relay_capability_revision = REVISION
        stored.relay_capability_approved_ceiling = ceiling

    dropped = deepcopy(ceiling)
    dropped["modes"]["text_to_video"].pop(
        "conditional_required_resource_keys"
    )
    rejected = client.put(
        f"/api/v1/platform-admin/models/{model['id']}",
        headers=headers,
        json={
            "display_name": model["display_name"],
            "provider_key": model["provider_key"],
            "billing_mode": model["billing_mode"],
            "expected_capability_version": model["capability_version"],
            "capabilities": [{"key": "generation", "config": dropped}],
        },
    )
    assert rejected.status_code == 409, rejected.text

    face_disabled = deepcopy(dropped)
    face_disabled["modes"]["text_to_video"]["supports_face"] = False
    restricted = client.put(
        f"/api/v1/platform-admin/models/{model['id']}",
        headers=headers,
        json={
            "display_name": model["display_name"],
            "provider_key": model["provider_key"],
            "billing_mode": model["billing_mode"],
            "expected_capability_version": model["capability_version"],
            "capabilities": [
                {"key": "generation", "config": face_disabled}
            ],
        },
    )
    assert restricted.status_code == 200, restricted.text
    effective = restricted.json()["effective_capabilities"]["modes"][
        "text_to_video"
    ]
    assert effective["supports_face"] is False
    assert effective["conditional_required_resource_keys"] == {}


def _sync_candidate(
    client,
    headers: dict[str, str],
    model: dict,
    *,
    reason: str,
    capability_revision: str = REVISION,
    catalog_revision: str = CATALOG_REVISION,
):
    return client.post(
        f"/api/v1/platform-admin/models/{model['id']}/relay-capability/sync",
        headers=headers,
        json={
            "expected_capability_version": model["capability_version"],
            "expected_catalog_revision": catalog_revision,
            "expected_capability_revision": capability_revision,
            "reason": reason,
        },
    )


def _approve_candidate(
    client,
    headers: dict[str, str],
    model: dict,
    *,
    reason: str,
    capability_revision: str = REVISION,
    catalog_revision: str = CATALOG_REVISION,
):
    return client.post(
        f"/api/v1/platform-admin/models/{model['id']}/relay-capability",
        headers=headers,
        json={
            "expected_capability_version": model["capability_version"],
            "expected_catalog_revision": catalog_revision,
            "expected_capability_revision": capability_revision,
            "expected_routing_release_sha256": "sha256:" + "d" * 64,
            "reason": reason,
        },
    )


def test_legacy_relay_revision_cannot_restore_company_distribution_before_approval(
    app,
    client,
    tenant,
) -> None:
    headers = _admin_headers(client, "legacy-relay-distribution-gate")
    capability = canonical_capability(
        modes={"text_to_video": _mode(max_images=1, output_counts=[1])}
    )
    created = _create_model(
        client,
        headers,
        suffix="legacy-relay-distribution-gate",
        capability=capability,
    )
    assert created.status_code == 201, created.text
    model = created.json()
    with app.state.session_factory.begin() as session:
        stored = session.get(ModelDefinition, model["id"])
        assert stored is not None
        stored.active = True
        stored.published_at = datetime.now(timezone.utc)
        stored.relay_capability_revision = REVISION
        # This is the exact 0040 -> 0041 legacy shape: the historical revision
        # survives, but no live candidate or approved ceiling is inferred.
        stored.relay_capability_candidate_revision = None
        stored.relay_capability_approved_ceiling = None
    app.state.relay_client = CatalogRelayClient(
        _catalog(model["slug"], capability)
    )

    blocked = client.put(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/model-grants",
        headers=headers,
        json={
            "model_id": model["id"],
            "enabled": True,
            "price_per_item_cents": 25,
            "config_override": {},
        },
    )
    assert blocked.status_code == 409, blocked.text
    assert "同步并批准" in blocked.text


def test_relay_catalog_audit_approves_a_platform_restriction_and_stamps_tasks(
    app, client, tenant, tenant_headers
) -> None:
    headers = _admin_headers(client, "relay-sync")
    platform_capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=4,
                max_videos=0,
                max_audio=0,
                input_media_types=["image"],
                output_counts=[1],
            )
        }
    )
    relay_capability = canonical_capability(
        modes={
            "image_to_video": _mode(
                max_images=9,
                max_videos=0,
                max_audio=0,
                input_media_types=["image"],
                output_counts=[1, 2],
            ),
            "text_to_video": _mode(
                max_images=9,
                max_videos=0,
                max_audio=0,
                input_media_types=["image"],
                output_counts=[1, 2],
            ),
        }
    )
    created = _create_model(
        client,
        headers,
        suffix="relay-sync",
        capability=platform_capability,
    )
    assert created.status_code == 201, created.text
    model = created.json()
    app.state.relay_client = CatalogRelayClient(
        _catalog(model["slug"], relay_capability)
    )

    audit = client.get(
        "/api/v1/platform-admin/relay-models", headers=headers
    )
    assert audit.status_code == 200, audit.text
    row = audit.json()["items"][0]
    assert row["status"] == "compatible_restriction"
    assert row["approved_revision"] is None
    assert audit.headers["Cache-Control"] == "private, no-store"
    blocked_publish = _publish(client, headers, model["id"])
    assert blocked_publish.status_code == 409
    assert "批准" in blocked_publish.text

    synced = _sync_candidate(
        client,
        headers,
        model,
        reason="Record verified Relay capability candidate",
    )
    assert synced.status_code == 200, synced.text
    assert synced.json()["requires_approval"] is True
    approved = _approve_candidate(
        client,
        headers,
        model,
        reason="Approve verified Relay capability ceiling",
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["compatibility"] == "compatible_restriction"
    assert approved.json()["capability_revision"] == REVISION
    assert approved.json()["model"]["relay_capability_revision"] == REVISION
    assert approved.json()["model"]["capability_version"] == 1
    assert approved.json()["approval_audit_id"]

    assert _publish(client, headers, model["id"]).status_code == 200
    assert _grant(
        client,
        headers,
        company_id=tenant["company_id"],
        model_id=model["id"],
        price_per_item_cents=100,
    ).status_code == 200
    _recharge(client, tenant, tenant_headers, suffix="relay-sync")
    created_task = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model["id"],
            "idempotency_key": "relay-revision-task",
            "expected_capability_version": 1,
            "request_payload": {
                "mode": "text_to_video",
                "prompt": "Version-pinned generation",
                "assets": [],
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "output_count": 1,
                "face_enabled": False,
                "metadata": {},
            },
        },
    )
    assert created_task.status_code == 201, created_task.text
    with app.state.session_factory() as session:
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == created_task.json()["id"]
            )
        )
        assert outbox is not None
        assert outbox.relay_payload["expected_capability_revision"] == REVISION


def test_provider_cost_coverage_gates_publish_personal_and_company_distribution(
    app, client, tenant
) -> None:
    headers = _admin_headers(client, "provider-cost-distribution")
    capability = canonical_capability(
        modes={
            "text_to_image": _mode(
                max_images=0,
                max_videos=0,
                max_audio=0,
                supports_face=False,
                durations=[1],
                resolutions=["1024x1024"],
                output_counts=[1],
                input_media_types=[],
            )
        }
    )
    created = _create_model(
        client,
        headers,
        suffix="provider-cost-distribution",
        capability=capability,
        billing_mode="per_item",
    )
    assert created.status_code == 201, created.text
    model = created.json()
    catalog = _catalog(model["slug"], capability)
    app.state.relay_client = CatalogRelayClient(
        catalog,
        provider_cost_ready=False,
    )
    assert _sync_candidate(
        client,
        headers,
        model,
        reason="Record the cost-gated provider candidate",
    ).status_code == 200
    approved = _approve_candidate(
        client,
        headers,
        model,
        reason="Approve capability without claiming provider-cost coverage",
    )
    assert approved.status_code == 200, approved.text

    blocked_publish = _publish(client, headers, model["id"])
    assert blocked_publish.status_code == 409
    assert "供应商成本" in blocked_publish.text

    app.state.relay_client = CatalogRelayClient(catalog)
    published = _publish(client, headers, model["id"])
    assert published.status_code == 200, published.text
    model = published.json()

    app.state.relay_client = CatalogRelayClient(
        catalog,
        provider_cost_ready=False,
    )
    blocked_personal = client.put(
        f"/api/v1/platform-admin/personal-model-grants/{model['id']}",
        headers=headers,
        json={
            "expected_capability_version": model["capability_version"],
            "expected_quote_revision": None,
            "enabled": True,
            "price_per_item_points": 9,
            "price_per_second_points": None,
            "config_override": {},
            "reason": "Provider cost must be covered first",
        },
    )
    assert blocked_personal.status_code == 409
    assert "供应商成本" in blocked_personal.text

    blocked_company = _grant(
        client,
        headers,
        company_id=tenant["company_id"],
        model_id=model["id"],
        price_per_item_cents=100,
    )
    assert blocked_company.status_code == 409
    assert "供应商成本" in blocked_company.text

    app.state.relay_client = CatalogRelayClient(catalog)
    enabled_company = _grant(
        client,
        headers,
        company_id=tenant["company_id"],
        model_id=model["id"],
        price_per_item_cents=100,
    )
    assert enabled_company.status_code == 200, enabled_company.text


def test_relay_capability_drift_pauses_company_distribution_until_reapproved(
    app, client, tenant, tenant_headers
) -> None:
    headers = _admin_headers(client, "relay-drift-gate")
    platform_capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=1,
                max_videos=0,
                max_audio=0,
                input_media_types=["image"],
                output_counts=[1],
            )
        }
    )
    relay_capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=2,
                max_videos=0,
                max_audio=0,
                input_media_types=["image"],
                output_counts=[1],
            )
        }
    )
    created = _create_model(
        client,
        headers,
        suffix="relay-drift-gate",
        capability=platform_capability,
    )
    assert created.status_code == 201, created.text
    model = created.json()
    app.state.relay_client = CatalogRelayClient(
        _catalog(model["slug"], relay_capability)
    )
    assert _sync_candidate(
        client, headers, model, reason="Record initial Relay ceiling"
    ).status_code == 200
    assert _approve_candidate(
        client, headers, model, reason="Approve initial Relay ceiling"
    ).status_code == 200
    assert _publish(client, headers, model["id"]).status_code == 200
    assert _grant(
        client,
        headers,
        company_id=tenant["company_id"],
        model_id=model["id"],
        price_per_item_cents=100,
    ).status_code == 200
    _recharge(client, tenant, tenant_headers, suffix="relay-drift-gate")

    available_before = client.get(
        f"/api/v1/companies/{tenant['company_id']}/models",
        headers=tenant_headers,
    )
    assert available_before.status_code == 200, available_before.text
    assert any(item["id"] == model["id"] for item in available_before.json())

    next_revision = "sha256:" + ("c" * 64)
    next_catalog_revision = "sha256:" + ("d" * 64)
    next_relay_capability = deepcopy(relay_capability)
    next_relay_capability["modes"]["text_to_video"]["limits"][
        "max_images"
    ] = 3
    app.state.relay_client = CatalogRelayClient(
        _catalog(
            model["slug"],
            next_relay_capability,
            capability_revision=next_revision,
            catalog_revision=next_catalog_revision,
        )
    )
    synced = _sync_candidate(
        client,
        headers,
        model,
        reason="Record changed Relay capability for review",
        capability_revision=next_revision,
        catalog_revision=next_catalog_revision,
    )
    assert synced.status_code == 200, synced.text
    assert synced.json()["requires_approval"] is True

    paused = client.get(
        f"/api/v1/companies/{tenant['company_id']}/models",
        headers=tenant_headers,
    )
    assert paused.status_code == 200, paused.text
    assert all(item["id"] != model["id"] for item in paused.json())
    rejected = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model["id"],
            "idempotency_key": "relay-drift-gate-rejected",
            "expected_capability_version": model["capability_version"],
            "request_payload": {
                "mode": "text_to_video",
                "prompt": "Must wait for capability approval",
                "assets": [],
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "output_count": 1,
                "face_enabled": False,
                "metadata": {},
            },
        },
    )
    assert rejected.status_code == 409, rejected.text
    assert "待批准" in rejected.text

    approved = _approve_candidate(
        client,
        headers,
        model,
        reason="Approve reviewed Relay capability change",
        capability_revision=next_revision,
        catalog_revision=next_catalog_revision,
    )
    assert approved.status_code == 200, approved.text
    restored = client.get(
        f"/api/v1/companies/{tenant['company_id']}/models",
        headers=tenant_headers,
    )
    assert restored.status_code == 200, restored.text
    assert any(item["id"] == model["id"] for item in restored.json())


def test_relay_sync_rejects_platform_capability_expansion(app, client) -> None:
    headers = _admin_headers(client, "relay-unsafe")
    platform_capability = canonical_capability(
        modes={"text_to_video": _mode(max_images=9)}
    )
    relay_capability = deepcopy(platform_capability)
    relay_capability["modes"]["text_to_video"]["limits"]["max_images"] = 4
    created = _create_model(
        client,
        headers,
        suffix="relay-unsafe",
        capability=platform_capability,
    )
    assert created.status_code == 201, created.text
    model = created.json()
    app.state.relay_client = CatalogRelayClient(
        _catalog(model["slug"], relay_capability)
    )

    audit = client.get(
        "/api/v1/platform-admin/relay-models", headers=headers
    )
    assert audit.status_code == 200
    assert audit.json()["items"][0]["status"] == "unsafe_expansion"
    synced = _sync_candidate(
        client,
        headers,
        model,
        reason="Record unsafe Relay capability candidate",
    )
    assert synced.status_code == 200, synced.text
    assert synced.json()["compatibility"] == "unsafe_expansion"
    rejected = _approve_candidate(
        client,
        headers,
        model,
        reason="Reject unsafe capability expansion",
    )
    assert rejected.status_code == 409
    detail = client.get(
        f"/api/v1/platform-admin/models/{model['id']}", headers=headers
    )
    assert detail.json()["relay_capability_revision"] is None


def test_relay_approval_requires_a_previously_synced_exact_candidate(
    app, client
) -> None:
    headers = _admin_headers(client, "relay-two-phase")
    capability = canonical_capability(
        modes={"text_to_video": _mode(max_images=1, output_counts=[1])}
    )
    created = _create_model(
        client,
        headers,
        suffix="relay-two-phase",
        capability=capability,
    )
    assert created.status_code == 201, created.text
    model = created.json()
    app.state.relay_client = CatalogRelayClient(_catalog(model["slug"], capability))

    rejected = _approve_candidate(
        client,
        headers,
        model,
        reason="Must not approve an unseen candidate",
    )
    assert rejected.status_code == 409
    assert "先同步并审阅" in rejected.text

    synced = _sync_candidate(
        client,
        headers,
        model,
        reason="Record candidate before independent approval",
    )
    assert synced.status_code == 200, synced.text

    changed_catalog = _catalog(model["slug"], capability)
    changed_catalog.catalog_revision = "sha256:" + ("c" * 64)
    app.state.relay_client = CatalogRelayClient(changed_catalog)
    drifted = _approve_candidate(
        client,
        headers,
        model,
        reason="Reject approval after Relay catalog drift",
    )
    assert drifted.status_code == 409
    assert "目录版本已变化" in drifted.text


def test_relay_approval_rejects_same_revision_with_different_live_content(
    app, client
) -> None:
    headers = _admin_headers(client, "relay-approval-collision")
    candidate = canonical_capability(
        modes={"text_to_video": _mode(max_images=1, output_counts=[1])}
    )
    created = _create_model(
        client,
        headers,
        suffix="relay-approval-collision",
        capability=candidate,
    )
    assert created.status_code == 201, created.text
    model = created.json()
    app.state.relay_client = CatalogRelayClient(
        _catalog(model["slug"], candidate)
    )
    synced = _sync_candidate(
        client,
        headers,
        model,
        reason="Store the exact candidate content before approval",
    )
    assert synced.status_code == 200, synced.text

    collided = deepcopy(candidate)
    collided["modes"]["text_to_video"]["limits"]["max_images"] = 2
    # Simulate a compromised/non-conforming Relay that reuses both claimed
    # revisions.  Approval must compare the full live document under the model
    # row lock, not accept matching strings alone.
    app.state.relay_client = CatalogRelayClient(
        _catalog(model["slug"], collided)
    )
    rejected = _approve_candidate(
        client,
        headers,
        model,
        reason="Must reject a revision collision",
    )
    assert rejected.status_code == 409, rejected.text
    assert "revision collision" in rejected.text
    with app.state.session_factory() as session:
        stored = session.get(ModelDefinition, model["id"])
        assert stored is not None
        assert stored.relay_capability_candidate == candidate
        assert stored.relay_capability_approved_ceiling is None


def test_configured_relay_rejects_publish_for_an_unpinned_legacy_model(
    app, client
) -> None:
    headers = _admin_headers(client, "relay-unpinned")
    capability = canonical_capability(
        modes={"text_to_video": _mode(max_images=1, output_counts=[1])}
    )
    created = _create_model(
        client,
        headers,
        suffix="relay-unpinned",
        capability=capability,
    )
    assert created.status_code == 201, created.text
    model = created.json()
    app.state.relay_client = CatalogRelayClient(
        _catalog(model["slug"], capability)
    )

    rejected = _publish(client, headers, model["id"])
    assert rejected.status_code == 409
    assert "中转站模型能力版本" in rejected.text
