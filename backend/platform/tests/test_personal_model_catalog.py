from __future__ import annotations

from platform_api.models import (
    ModelCapability,
    ModelDefinition,
    PersonalRetailModelGrant,
    User,
    utcnow,
)
from platform_api.services.personal import PersonalWorkspaceService


APPROVED_REVISION = "sha256:" + ("1" * 64)
UNAPPROVED_REVISION = "sha256:" + ("2" * 64)


def _capability(mode: str = "text_to_video") -> dict:
    uses_image = mode == "image_to_video"
    return {
        "schema_version": 1,
        "modes": {
            mode: {
                "input_media_types": ["image"] if uses_image else [],
                "supports_face": False,
                "required_resource_keys": [],
                "limits": {
                    "max_prompt_length": 500,
                    "max_images": 1 if uses_image else 0,
                    "max_videos": 0,
                    "max_audio": 0,
                    "duration_seconds": [5],
                    "aspect_ratios": ["16:9"],
                    "resolutions": ["720p"],
                    "output_counts": [1],
                },
            }
        },
    }


def _personal_user(app) -> str:
    with app.state.session_factory.begin() as session:
        user = User(
            email="personal-catalog@example.com",
            display_name="Personal Catalog",
        )
        session.add(user)
        session.flush()
        PersonalWorkspaceService.ensure(session, user_id=user.id)
        return user.id


def _add_model(
    session,
    *,
    slug: str,
    billing_mode: str = "per_second",
    active: bool = True,
    published: bool = True,
    candidate_revision: str | None = APPROVED_REVISION,
    grant: str = "enabled",
    capability_mode: str = "text_to_video",
    required_resource_keys: list[str] | None = None,
) -> ModelDefinition:
    capability = _capability(capability_mode)
    if required_resource_keys is not None:
        capability["modes"][capability_mode]["required_resource_keys"] = required_resource_keys
    model = ModelDefinition(
        slug=slug,
        display_name=slug.replace("-", " ").title(),
        provider_key=f"private-route-{slug}",
        billing_mode=billing_mode,
        capability_version=7,
        active=active,
        published_at=utcnow(),
        relay_capability_revision=APPROVED_REVISION,
        relay_capability_candidate_revision=candidate_revision,
        relay_capability_candidate={"private_candidate": slug},
        relay_capability_approved_ceiling=capability,
    )
    session.add(model)
    session.flush()
    if not published:
        model.published_at = None
    session.add(
        ModelCapability(
            model_id=model.id,
            capability_key="generation",
            config=capability,
        )
    )
    if grant != "missing":
        wrong_price = grant == "wrong_price"
        session.add(
            PersonalRetailModelGrant(
                model_id=model.id,
                enabled=grant not in {"disabled"},
                price_per_second_points=(
                    3 if billing_mode == "per_second" and not wrong_price else None
                ),
                price_per_item_points=(
                    5 if billing_mode == "per_item" or wrong_price else None
                ),
                config_override={},
            )
        )
    return model


def test_personal_model_catalog_is_read_only_and_generation_list_stays_strict(
    app, client
):
    user_id = _personal_user(app)
    headers = {"X-User-ID": user_id}
    with app.state.session_factory.begin() as session:
        available = _add_model(session, slug="01-available")
        available_image = _add_model(
            session, slug="09-personal-image-available", capability_mode="image_to_video",
        )
        expected_reasons = {
            _add_model(session, slug="02-unpublished", published=False).id:
                "model_unpublished",
            _add_model(session, slug="03-disabled", active=False).id:
                "model_disabled",
            _add_model(
                session,
                slug="04-relay-unapproved",
                candidate_revision=UNAPPROVED_REVISION,
            ).id: "relay_capability_unapproved",
            _add_model(session, slug="05-unconfigured", grant="missing").id:
                "personal_distribution_unconfigured",
            _add_model(session, slug="06-retail-disabled", grant="disabled").id:
                "personal_distribution_disabled",
            _add_model(session, slug="07-price-mismatch", grant="wrong_price").id:
                "personal_price_unavailable",
            _add_model(
                session,
                slug="08-company-resource-unavailable",
                capability_mode="image_to_video",
                required_resource_keys=["company-style-guide"],
            ).id: "personal_capability_unavailable",
        }

    # Force the same release ceiling used by a real Relay-backed deployment.
    app.state.relay_client = object()
    catalog_response = client.get(
        "/api/v1/personal/model-catalog", headers=headers
    )
    assert catalog_response.status_code == 200, catalog_response.text
    catalog = catalog_response.json()
    assert len(catalog) == 9

    allowed_keys = {
        "id",
        "slug",
        "display_name",
        "billing_mode",
        "capability_version",
        "available",
        "unavailable_reason",
        "billing_unit",
        "billing_version",
    }
    by_id = {item["id"]: item for item in catalog}
    assert by_id[available.id]["available"] is True
    assert by_id[available.id]["unavailable_reason"] is None
    assert by_id[available_image.id]["available"] is True
    assert by_id[available_image.id]["unavailable_reason"] is None
    for model_id, reason_code in expected_reasons.items():
        item = by_id[model_id]
        assert item["available"] is False
        assert item["unavailable_reason"]["code"] == reason_code
        assert item["unavailable_reason"]["message"]
        assert set(item["unavailable_reason"]) == {"code", "message"}

    for item in catalog:
        assert set(item) == allowed_keys
        assert item["billing_unit"] == "POINT"
        assert item["billing_version"] == 2
        assert item["capability_version"] == 7
        assert "effective_capabilities" not in item
        assert "relay_capability_candidate" not in item
        assert "provider_key" not in item

    generation_response = client.get("/api/v1/personal/models", headers=headers)
    assert generation_response.status_code == 200, generation_response.text
    generation_models = generation_response.json()
    assert [item["id"] for item in generation_models] == [available.id, available_image.id]
    assert set(generation_models[0]["effective_capabilities"]["modes"]) == {"text_to_video"}
    assert set(generation_models[1]["effective_capabilities"]["modes"]) == {"image_to_video"}
    image_mode = generation_models[1]["effective_capabilities"]["modes"]["image_to_video"]
    assert image_mode["input_media_types"] == ["image"]
    assert image_mode["required_resource_keys"] == []
