"""Positive Seedream distribution through real Platform HTTP services.

Relay catalog, route tests and supplier prices are explicit local doubles.
This proves the software lifecycle, not a paid supplier run or live approval.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from platform_api.models import (
    CompanyModelGrant,
    GenerationTask,
    ModelDefinition,
    PersonalRetailModelGrant,
    RelaySubmissionOutbox,
)

from .test_model_capability_v1_contract import _admin_headers
from .test_model_commercial_release import _commercial_body
from .test_personal_workspace import _personal_user
from .test_relay_capability_sync import CatalogRelayClient, _catalog


@pytest.mark.parametrize("company_migration", ["before_release", "after_release"])
def test_seedream_exact_image_contract_reaches_both_customer_directories(
    app, client, tenant, company_migration
):
    admin = _admin_headers(client, f"seedream-positive-{company_migration}")
    # Exact shape of the historical Platform Seedream candidate observed in
    # the read-only audit. Do not infer new image-input modes from its name.
    capability = {
        "schema_version": 1,
        "modes": {
            "text_to_image": {
                "input_media_types": [],
                "supports_face": False,
                "required_resource_keys": [],
                "limits": {
                    "max_prompt_length": 1000,
                    "max_images": 0,
                    "max_videos": 0,
                    "max_audio": 0,
                    "duration_seconds": [1],
                    "aspect_ratios": ["1:1"],
                    "resolutions": ["2048x2048"],
                    "output_counts": [1],
                },
            },
        },
    }
    catalog = _catalog("seedream-5", capability)
    app.state.relay_client = CatalogRelayClient(
        catalog,
        provider_cost_billing_unit="output_item",
        provider_cost_unit_amount_cents=22,
        provider_cost_source_sha256="1" * 64,
    )
    synchronized = client.post(
        "/api/v1/platform-admin/relay-models/reconcile", headers=admin
    )
    assert synchronized.status_code == 200, synchronized.text
    model_id = synchronized.json()["created_model_ids"][0]
    model = client.get(
        f"/api/v1/platform-admin/models/{model_id}", headers=admin
    ).json()
    assert model["active"] is False
    assert model["relay_capability_revision"] is None

    def migrate_company():
        migration = client.post(
            f"/api/v1/platform-admin/companies/{tenant['company_id']}/billing/migrate-to-points",
            headers=admin,
            json={"expected_available_cents": 0, "idempotency_key": "seedream-company-points"},
        )
        assert migration.status_code == 200, migration.text
        assert migration.json()["billing_version"] == 2

    if company_migration == "before_release":
        migrate_company()
    body = _commercial_body(
        model=model,
        cost_kind="output_item",
        formula_billing_unit="per_item",
        assumptions={
            "quantity_basis": "relay_effective_capability_ceiling",
            "enforced_limits": {"max_output_count": 1},
        },
        components=[{
            "component": "output_item",
            "rate_micros": 220_000,
            "quantity_numerator": 1,
            "quantity_denominator": 1,
        }],
    )
    body["provider_cost_evidence_reference"] = "https://www.volcengine.com/product/ark"
    plan = client.put(
        f"/api/v1/platform-admin/models/{model_id}/commercial-release-plan",
        headers=admin, json=body,
    )
    assert plan.status_code == 200, plan.text
    assert plan.json()["minimum_price_points"] == 4
    released = client.post(
        "/api/v1/platform-admin/model-commercial-releases/reconcile", headers=admin
    )
    assert released.status_code == 200, released.text
    assert released.json()["released_count"] == 1
    assert released.json()["blocked_count"] == 0

    if company_migration == "after_release":
        with app.state.session_factory() as session:
            assert session.scalar(select(CompanyModelGrant).where(
                CompanyModelGrant.company_id == tenant["company_id"],
                CompanyModelGrant.model_id == model_id,
            )) is None
        migrate_company()
        # A later billing migration is not an implicit model authorization.
        grant = client.put(
            f"/api/v1/platform-admin/companies/{tenant['company_id']}/model-grants",
            headers=admin,
            json={
                "model_id": model_id,
                "enabled": True,
                "price_per_item_points": plan.json()["enterprise_price_points"],
                "config_override": {},
            },
        )
        assert grant.status_code == 200, grant.text

    company_catalog = client.get(
        f"/api/v1/companies/{tenant['company_id']}/models",
        headers={"X-User-ID": tenant["user_id"], "X-Company-ID": tenant["company_id"]},
    )
    personal_user_id = _personal_user(app, f"seedream-positive-{company_migration}")
    personal_catalog = client.get(
        "/api/v1/personal/models", headers={"X-User-ID": personal_user_id}
    )
    for response in (company_catalog, personal_catalog):
        assert response.status_code == 200, response.text
        row = next(item for item in response.json() if item["id"] == model_id)
        assert row["unit_price_points"] == 4
        assert set(row["effective_capabilities"]["modes"]) == {"text_to_image"}
        assert row["mode_readiness"]["text_to_image"]["default"] == {
            "ready": True, "status": "ready", "blockers": [],
        }
    with app.state.session_factory() as session:
        stored = session.get(ModelDefinition, model_id)
        assert stored.active is True and stored.published_at is not None
        assert stored.relay_capability_revision == stored.relay_capability_candidate_revision
        assert session.scalar(select(PersonalRetailModelGrant).where(
            PersonalRetailModelGrant.model_id == model_id
        )).enabled is True
        assert session.scalar(select(func.count(GenerationTask.id))) == 0
        assert session.scalar(select(func.count(RelaySubmissionOutbox.id))) == 0
