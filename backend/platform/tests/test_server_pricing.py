from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from platform_api.config import Settings
from platform_api.main import create_app
from platform_api.models import CompanyModelGrant, ModelCapability, ModelDefinition
from platform_api.services.errors import ConflictError
from platform_api.services.quote_revision import model_grant_quote_revision
from platform_api.services.tasks import TaskService

from .conftest import TEST_RELAY_CAPABILITY_REVISION
from .test_generation_capability_v1 import _side_effect_snapshot


def add_model_and_grant(
    app,
    company_id: str,
    *,
    slug: str,
    second_price: int | None,
    item_price: int | None,
    capability_config: dict,
) -> str:
    with app.state.session_factory.begin() as session:
        model = ModelDefinition(
            slug=slug,
            display_name=slug,
            provider_key="test-provider",
            billing_mode=("per_second" if second_price is not None else "per_item"),
            capability_version=7,
            relay_capability_revision=TEST_RELAY_CAPABILITY_REVISION,
        )
        session.add(model)
        session.flush()
        session.add(
            ModelCapability(
                model_id=model.id,
                capability_key="generation",
                config=capability_config,
            )
        )
        session.add(
            CompanyModelGrant(
                company_id=company_id,
                model_id=model.id,
                enabled=True,
                price_per_second_cents=second_price,
                price_per_item_cents=item_price,
            )
        )
        return model.id


def recharge(client, company_id, headers, amount=10_000):
    response = client.post(
        f"/api/v1/companies/{company_id}/wallet/recharge",
        headers=headers,
        json={
            "amount_cents": amount,
            "idempotency_key": f"pricing-recharge-{company_id}",
        },
    )
    assert response.status_code == 200, response.text


def test_client_cannot_submit_or_hide_a_quote(
    app, client, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    model_id = add_model_and_grant(
        app,
        company_id,
        slug="secure-item",
        second_price=None,
        item_price=240,
        capability_config={"max_outputs": 2},
    )
    recharge(client, company_id, tenant_headers)
    before = _side_effect_snapshot(app, company_id)

    top_level_tamper = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "quote_cents": 1,
            "idempotency_key": "tampered-top-level",
            "request_payload": {"prompt": "test", "output_count": 1},
        },
    )
    assert top_level_tamper.status_code == 422
    assert _side_effect_snapshot(app, company_id) == before

    payload_tamper = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "tampered-in-payload",
            "request_payload": {
                "prompt": "test",
                "output_count": 1,
                "quote_cents": 1,
            },
        },
    )
    assert payload_tamper.status_code == 409, payload_tamper.text
    assert _side_effect_snapshot(app, company_id) == before


def test_stale_quote_revision_rejects_before_reserve_and_idempotent_replay_wins(
    app, client, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    model_id = add_model_and_grant(
        app,
        company_id,
        slug="quote-revision-stale",
        second_price=None,
        item_price=240,
        capability_config={"max_outputs": 2},
    )
    recharge(client, company_id, tenant_headers)
    available = client.get(
        f"/api/v1/companies/{company_id}/models",
        headers=tenant_headers,
    )
    assert available.status_code == 200, available.text
    listed = next(item for item in available.json() if item["id"] == model_id)
    old_revision = listed["quote_revision"]
    assert old_revision.startswith("sha256:")

    with app.state.session_factory.begin() as session:
        grant = session.query(CompanyModelGrant).filter_by(
            company_id=company_id,
            model_id=model_id,
        ).one()
        grant.price_per_item_cents = 360
        model = session.get(ModelDefinition, model_id)
        new_revision = model_grant_quote_revision(model=model, grant=grant)
    assert new_revision != old_revision

    before = _side_effect_snapshot(app, company_id)
    stale = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "expected_capability_version": 7,
            "expected_quote_revision": old_revision,
            "idempotency_key": "stale-quote-revision",
            "request_payload": {"prompt": "stale", "output_count": 1},
        },
    )
    assert stale.status_code == 409, stale.text
    assert _side_effect_snapshot(app, company_id) == before

    created = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "expected_capability_version": 7,
            "expected_quote_revision": new_revision,
            "idempotency_key": "fresh-quote-revision",
            "request_payload": {"prompt": "fresh", "output_count": 1},
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["quote_cents"] == 360
    assert created.json()["pricing_snapshot"]["quote_revision"] == new_revision

    # A retry confirms the already-created resource before consulting today's
    # quote, which preserves idempotency after any later administration edit.
    with app.state.session_factory.begin() as session:
        session.query(CompanyModelGrant).filter_by(
            company_id=company_id,
            model_id=model_id,
        ).one().price_per_item_cents = 480
    replay = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "expected_capability_version": 7,
            "expected_quote_revision": new_revision,
            "idempotency_key": "fresh-quote-revision",
            "request_payload": {"prompt": "fresh", "output_count": 1},
        },
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == created.json()["id"]
    assert replay.json()["quote_cents"] == 360

    # Production admission requires an explicit quote acknowledgement for a
    # first submission, but this must not break an exact idempotent replay.
    with app.state.session_factory.begin() as session:
        with pytest.raises(ConflictError, match="quote revision is required"):
            TaskService.create(
                session,
                company_id=company_id,
                user_id=tenant["user_id"],
                model_id=model_id,
                request_payload={"prompt": "missing", "output_count": 1},
                idempotency_key="missing-production-quote",
                require_quote_revision=True,
            )
        replayed_task, replayed_created = TaskService.create(
            session,
            company_id=company_id,
            user_id=tenant["user_id"],
            model_id=model_id,
            request_payload=created.json()["request_payload"],
            idempotency_key="fresh-quote-revision",
            require_quote_revision=True,
        )
    assert replayed_created is False
    assert replayed_task.id == created.json()["id"]

def test_per_second_quote_and_capability_version_are_snapshotted(
    app, client, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    model_id = add_model_and_grant(
        app,
        company_id,
        slug="priced-by-second",
        second_price=75,
        item_price=None,
        capability_config={"durations": [4, 8]},
    )
    recharge(client, company_id, tenant_headers)
    response = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "second-priced-task",
            "request_payload": {"prompt": "test", "duration_seconds": 8},
        },
    )
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["quote_cents"] == 600
    assert task["pricing_snapshot"] == {
        **task["pricing_snapshot"],
        "mode": "per_second",
        "unit_price_cents": 75,
        "quantity": 8,
        "quote_cents": 600,
    }
    assert task["capability_snapshot"]["capability_version"] == 7
    assert task["capability_snapshot"]["capabilities"]["generation"] == {
        "durations": [4, 8]
    }


def test_per_item_quote_uses_server_price_and_requested_count(
    app, client, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    model_id = add_model_and_grant(
        app,
        company_id,
        slug="priced-by-item",
        second_price=None,
        item_price=125,
        capability_config={"max_outputs": 4},
    )
    recharge(client, company_id, tenant_headers)
    response = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "item-priced-task",
            "request_payload": {"prompt": "test", "output_count": 3},
        },
    )
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["quote_cents"] == 375
    assert task["pricing_snapshot"]["mode"] == "per_item"
    assert task["pricing_snapshot"]["quantity"] == 3


def test_model_grant_requires_exactly_one_effective_price(client, tenant):
    admin = client.post(
        "/api/v1/bootstrap/platform-admin",
        json={
            "email": "pricing-xor-admin@example.com",
            "display_name": "Pricing XOR Admin",
        },
    )
    assert admin.status_code == 201, admin.text
    admin_headers = {
        "X-Platform-Admin-User-ID": admin.json()["user_id"]
    }
    model = client.post(
        "/api/v1/bootstrap/models",
        json={
            "slug": "pricing-xor-model",
            "display_name": "Pricing XOR Model",
            "provider_key": "development",
            "capabilities": [
                {
                    "key": "generation",
                    "config": {"durations": [5], "max_outputs": 1},
                }
            ],
        },
    )
    assert model.status_code == 201, model.text
    grant_url = (
        f"/api/v1/platform-admin/companies/{tenant['company_id']}"
        "/model-grants"
    )
    base = {"model_id": model.json()["id"], "enabled": True}

    missing = client.put(grant_url, headers=admin_headers, json=base)
    assert missing.status_code == 409
    assert missing.json()["code"] == "conflict"

    ambiguous = client.put(
        grant_url,
        headers=admin_headers,
        json={
            **base,
            "price_per_second_cents": 75,
            "price_per_item_cents": 240,
        },
    )
    assert ambiguous.status_code == 409
    assert ambiguous.json()["code"] == "conflict"

    per_second = client.put(
        grant_url,
        headers=admin_headers,
        json={**base, "price_per_second_cents": 75},
    )
    assert per_second.status_code == 200, per_second.text
    assert per_second.json()["price_per_second_cents"] == 75
    assert per_second.json()["price_per_item_cents"] is None

    per_item = client.put(
        grant_url,
        headers=admin_headers,
        json={**base, "price_per_item_cents": 240},
    )
    assert per_item.status_code == 409
    assert per_item.json()["code"] == "conflict"


def test_bootstrap_model_seed_creates_versioned_capabilities(client):
    response = client.post(
        "/api/v1/bootstrap/models",
        json={
            "slug": "seeded-video-v1",
            "display_name": "Seeded Video",
            "provider_key": "development",
            "capability_version": 3,
            "capabilities": [
                {"key": "text-to-video", "config": {"durations": [5, 10]}}
            ],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["capability_version"] == 3


def test_model_seed_endpoint_is_hidden_when_bootstrap_is_disabled():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    app = create_app(
        settings=Settings(
            database_url="sqlite+pysqlite://",
            auto_create_tables=True,
            enable_bootstrap=False,
        ),
        engine=engine,
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/bootstrap/models",
            json={
                "slug": "must-not-exist",
                "display_name": "Disabled",
                "provider_key": "development",
                "capabilities": [],
            },
        )
    engine.dispose()
    assert response.status_code == 404
