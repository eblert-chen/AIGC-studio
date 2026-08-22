from __future__ import annotations

from platform_api.models import CompanyModelGrant, ModelCapability, ModelDefinition
from platform_api.services.quote_revision import QUOTE_REVISION_PREFIX

from .conftest import bootstrap
from .test_relay_boundary import recharge_and_create


def add_catalog_model(
    app,
    *,
    company_id: str,
    slug: str,
    enabled: bool,
    price_cents: int,
):
    with app.state.session_factory.begin() as session:
        model = ModelDefinition(
            slug=slug,
            display_name=f"Model {slug}",
            provider_key="provider",
            capability_version=4,
        )
        session.add(model)
        session.flush()
        session.add(
            ModelCapability(
                model_id=model.id,
                capability_key="text-to-video",
                config={"durations": [5, 10]},
            )
        )
        session.add(
            CompanyModelGrant(
                company_id=company_id,
                model_id=model.id,
                enabled=enabled,
                price_per_second_cents=price_cents,
                config_override={"priority": "high"},
            )
        )
        return model.id


def test_available_models_only_returns_enabled_current_company_grants(
    app, client, tenant, tenant_headers
):
    enabled_id = add_catalog_model(
        app,
        company_id=tenant["company_id"],
        slug="enabled-model",
        enabled=True,
        price_cents=88,
    )
    add_catalog_model(
        app,
        company_id=tenant["company_id"],
        slug="disabled-model",
        enabled=False,
        price_cents=99,
    )
    other = bootstrap(client, "model-other")
    add_catalog_model(
        app,
        company_id=other["company_id"],
        slug="foreign-model",
        enabled=True,
        price_cents=77,
    )

    response = client.get(
        f"/api/v1/companies/{tenant['company_id']}/models",
        headers=tenant_headers,
    )
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    quote_revision = items[0].pop("quote_revision")
    assert quote_revision.startswith(QUOTE_REVISION_PREFIX)
    assert len(quote_revision) == len(QUOTE_REVISION_PREFIX) + 64
    assert items == [
        {
            "id": enabled_id,
            "slug": "enabled-model",
                "display_name": "Model enabled-model",
                "capability_version": 4,
                "relay_capability_revision": None,
                "relay_capability_synced_at": None,
                "capabilities": {"text-to-video": {"durations": [5, 10]}},
            "effective_capabilities": {
                "schema_version": 1,
                "modes": {
                    "text_to_video": {
                        "input_media_types": [],
                        "supports_face": False,
                        "required_resource_keys": [],
                        "limits": {
                            "max_prompt_length": 10_000,
                            "max_images": 0,
                            "max_videos": 0,
                            "max_audio": 0,
                            "duration_seconds": [5, 10],
                            "aspect_ratios": ["16:9"],
                            "resolutions": ["720p"],
                            "output_counts": [1],
                        },
                    }
                },
            },
            "pricing_mode": "per_second",
            "unit_price_cents": 88,
            "config_override": {"priority": "high"},
            "call_quota": None,
            "concurrency_limit": None,
            "effective_at": None,
            "expires_at": None,
        }
    ]

    foreign_context = client.get(
        f"/api/v1/companies/{tenant['company_id']}/models",
        headers={
            "X-Company-ID": other["company_id"],
            "X-User-ID": other["user_id"],
        },
    )
    assert foreign_context.status_code == 403

    member = client.post(
        f"/api/v1/companies/{tenant['company_id']}/members",
        headers=tenant_headers,
        json={"email": "no-model-access@example.com", "display_name": "No Access"},
    ).json()
    operator_access = client.get(
        f"/api/v1/companies/{tenant['company_id']}/models",
        headers={
            "X-Company-ID": tenant["company_id"],
            "X-User-ID": member["user_id"],
        },
    )
    assert operator_access.status_code == 200
    denied = client.put(
        (
            f"/api/v1/companies/{tenant['company_id']}/members/"
            f"{member['membership_id']}/permission"
        ),
        headers=tenant_headers,
        json={"permission_code": "models.read", "effect": "deny"},
    )
    assert denied.status_code == 200
    no_permission = client.get(
        f"/api/v1/companies/{tenant['company_id']}/models",
        headers={
            "X-Company-ID": tenant["company_id"],
            "X-User-ID": member["user_id"],
        },
    )
    assert no_permission.status_code == 403


def test_task_detail_is_strictly_company_scoped(
    app, client, tenant, tenant_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="detail"
    )
    own = client.get(
        f"/api/v1/companies/{tenant['company_id']}/tasks/{task['id']}",
        headers=tenant_headers,
    )
    assert own.status_code == 200
    assert own.json()["id"] == task["id"]
    assert "provider_task_id" not in own.json()

    other = bootstrap(client, "task-other")
    foreign = client.get(
        f"/api/v1/companies/{other['company_id']}/tasks/{task['id']}",
        headers={
            "X-Company-ID": other["company_id"],
            "X-User-ID": other["user_id"],
        },
    )
    assert foreign.status_code == 404
