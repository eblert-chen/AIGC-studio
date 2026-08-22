from __future__ import annotations

import pytest
from sqlalchemy import func, select

from platform_api.models import (
    CompanyModelGrant,
    CompanyResourceGrant,
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    RelaySubmissionOutbox,
    ResourceDefinition,
    ResourceKind,
    TaskStatus,
    WalletAccount,
)
from platform_api.services.errors import ConflictError
from platform_api.services.tasks import TaskService

from .test_input_assets import upload_asset
from .test_wallet_and_tasks import seed_model


BASE_CAPABILITY = {
    "modes": ["text_to_video"],
    "input_media_types": [],
    "limits": {
        "max_images": 0,
        "max_videos": 0,
        "max_audio": 0,
        "duration_seconds": [5],
        "aspect_ratios": ["16:9"],
        "resolutions": ["720p"],
        "output_counts": [1],
    },
}


def _seed_with_override(
    app,
    company_id: str,
    *,
    capability: dict,
    override: dict | None = None,
) -> str:
    model_id = seed_model(
        app,
        company_id,
        capability_key="generation",
        capability_config=capability,
    )
    if override is not None:
        with app.state.session_factory.begin() as session:
            grant = session.scalar(
                select(CompanyModelGrant).where(
                    CompanyModelGrant.company_id == company_id,
                    CompanyModelGrant.model_id == model_id,
                )
            )
            assert grant is not None
            grant.config_override = override
    return model_id


def _recharge(client, tenant, headers, *, suffix: str) -> None:
    response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/wallet/recharge",
        headers=headers,
        json={
            "amount_cents": 1000,
            "idempotency_key": f"capability-recharge-{suffix}",
        },
    )
    assert response.status_code == 200, response.text


def _create(client, tenant, headers, *, model_id: str, suffix: str, payload: dict):
    return client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=headers,
        json={
            "model_id": model_id,
            "idempotency_key": f"capability-task-{suffix}",
            "request_payload": {
                "prompt": "capability admission",
                "duration_seconds": 5,
                **payload,
            },
        },
    )


def _assert_rejected_before_money_or_outbox(app, company_id: str) -> None:
    with app.state.session_factory() as session:
        wallet = session.get(WalletAccount, company_id)
        assert wallet is not None and wallet.available_cents == 1000
        assert wallet.reserved_cents == 0
        assert session.scalar(select(func.count(GenerationTask.id))) == 0
        assert session.scalar(select(func.count(RelaySubmissionOutbox.id))) == 0
        assert session.scalar(
            select(func.count(LedgerEntry.id)).where(
                LedgerEntry.company_id == company_id,
                LedgerEntry.kind == LedgerKind.RESERVE,
            )
        ) == 0


@pytest.mark.parametrize(
    ("suffix", "payload"),
    [
        ("prompt-empty", {"prompt": "   "}),
        ("prompt-type", {"prompt": 123}),
        ("prompt-default-limit", {"prompt": "x" * 10_001}),
        ("mode-alias", {"mode": "text-to-video"}),
        ("mode", {"mode": "text_to_image"}),
        ("ratio", {"aspect_ratio": "9:16"}),
        ("resolution", {"resolution": "1080p"}),
        ("duration", {"duration_seconds": 10}),
        ("outputs", {"output_count": 2}),
    ],
)
def test_model_capability_cannot_be_bypassed_before_reserve_or_outbox(
    app, client, tenant, tenant_headers, suffix, payload
):
    model_id = _seed_with_override(
        app,
        tenant["company_id"],
        capability=BASE_CAPABILITY,
    )
    _recharge(client, tenant, tenant_headers, suffix=suffix)

    response = _create(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix=suffix,
        payload=payload,
    )

    assert response.status_code == 409, response.text
    _assert_rejected_before_money_or_outbox(app, tenant["company_id"])


@pytest.mark.parametrize(
    ("suffix", "override", "payload"),
    [
        (
            "override-mode-expansion",
            {"modes": ["text_to_video", "text_to_image"]},
            {"mode": "text_to_image"},
        ),
        (
            "override-duration-expansion",
            {"duration_seconds": [5, 10]},
            {"duration_seconds": 10},
        ),
        (
            "override-output-expansion",
            {"max_outputs": 4},
            {"output_count": 2},
        ),
    ],
)
def test_company_override_can_never_expand_the_model_capability(
    app, client, tenant, tenant_headers, suffix, override, payload
):
    model_id = _seed_with_override(
        app,
        tenant["company_id"],
        capability=BASE_CAPABILITY,
        override=override,
    )
    _recharge(client, tenant, tenant_headers, suffix=suffix)

    response = _create(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix=suffix,
        payload=payload,
    )

    assert response.status_code == 409, response.text
    _assert_rejected_before_money_or_outbox(app, tenant["company_id"])


def test_company_override_restricts_an_allowed_model_value(
    app, client, tenant, tenant_headers
):
    capability = {
        **BASE_CAPABILITY,
        "limits": {
            **BASE_CAPABILITY["limits"],
            "aspect_ratios": ["16:9", "9:16"],
        },
    }
    model_id = _seed_with_override(
        app,
        tenant["company_id"],
        capability=capability,
        override={"aspect_ratios": ["16:9"]},
    )
    _recharge(client, tenant, tenant_headers, suffix="override-restrict")

    response = _create(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="override-restrict",
        payload={"aspect_ratio": "9:16"},
    )

    assert response.status_code == 409, response.text
    _assert_rejected_before_money_or_outbox(app, tenant["company_id"])


@pytest.mark.parametrize(
    ("suffix", "model_limit", "override_limit", "prompt_length"),
    [
        ("prompt-model-limit", 10, 100, 11),
        ("prompt-override-limit", 100, 10, 11),
    ],
)
def test_prompt_limit_uses_the_stricter_model_and_company_value(
    app,
    client,
    tenant,
    tenant_headers,
    suffix,
    model_limit,
    override_limit,
    prompt_length,
):
    capability = {**BASE_CAPABILITY, "max_prompt_length": model_limit}
    model_id = _seed_with_override(
        app,
        tenant["company_id"],
        capability=capability,
        override={"max_prompt_length": override_limit},
    )
    _recharge(client, tenant, tenant_headers, suffix=suffix)

    response = _create(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix=suffix,
        payload={"prompt": "x" * prompt_length},
    )

    assert response.status_code == 409, response.text
    _assert_rejected_before_money_or_outbox(app, tenant["company_id"])


def test_task_service_rejects_non_object_payload_before_fingerprinting(
    app, tenant
):
    model_id = _seed_with_override(
        app,
        tenant["company_id"],
        capability=BASE_CAPABILITY,
    )
    with app.state.session_factory.begin() as session:
        with pytest.raises(ConflictError, match="request_payload must be an object"):
            TaskService.create(
                session,
                company_id=tenant["company_id"],
                user_id=tenant["user_id"],
                model_id=model_id,
                request_payload=[],  # type: ignore[arg-type]
                idempotency_key="non-object-payload",
            )


@pytest.mark.parametrize("mutation", ["too_many_images", "unsupported_audio"])
def test_input_asset_type_and_count_cannot_bypass_model_capability(
    app, client, tenant, tenant_headers, mutation
):
    image_one = upload_asset(
        client,
        tenant,
        tenant_headers,
        idempotency_key=f"capability-{mutation}-image-one",
    ).json()
    assets = [{"asset_id": image_one["id"], "media_type": "image"}]
    if mutation == "too_many_images":
        image_two = upload_asset(
            client,
            tenant,
            tenant_headers,
            content=b"\x89PNG\r\n\x1a\nsecond-private-input",
            idempotency_key=f"capability-{mutation}-image-two",
        ).json()
        assets.append({"asset_id": image_two["id"], "media_type": "image"})
    else:
        audio = upload_asset(
            client,
            tenant,
            tenant_headers,
            filename="reference.mp3",
            content=b"ID3-private-audio",
            content_type="audio/mpeg",
            media_type="audio",
            idempotency_key=f"capability-{mutation}-audio",
        ).json()
        assets.append({"asset_id": audio["id"], "media_type": "audio"})

    capability = {
        "modes": ["image_to_video"],
        "input_media_types": ["image"],
        "limits": {
            "max_images": 1,
            "max_videos": 0,
            "max_audio": 0,
            "duration_seconds": [5],
            "aspect_ratios": ["16:9"],
            "resolutions": ["720p"],
            "output_counts": [1],
        },
    }
    model_id = _seed_with_override(
        app,
        tenant["company_id"],
        capability=capability,
    )
    _recharge(client, tenant, tenant_headers, suffix=mutation)

    response = _create(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix=mutation,
        payload={"mode": "image_to_video", "assets": assets},
    )

    assert response.status_code == 409, response.text
    _assert_rejected_before_money_or_outbox(app, tenant["company_id"])


def test_effective_capability_snapshot_records_the_stricter_intersection(
    app, client, tenant, tenant_headers
):
    capability = {
        **BASE_CAPABILITY,
        "limits": {
            **BASE_CAPABILITY["limits"],
            "duration_seconds": [5, 10],
            "aspect_ratios": ["16:9", "9:16"],
            "resolutions": ["720p", "1080p"],
        },
    }
    override = {
        "duration_seconds": [5],
        "aspect_ratios": ["16:9"],
        "resolutions": ["720p"],
    }
    model_id = _seed_with_override(
        app,
        tenant["company_id"],
        capability=capability,
        override=override,
    )
    _recharge(client, tenant, tenant_headers, suffix="effective-snapshot")

    response = _create(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="effective-snapshot",
        payload={
            "mode": "text_to_video",
            "duration_seconds": 5,
            "aspect_ratio": "16:9",
            "resolution": "720p",
            "output_count": 1,
        },
    )

    assert response.status_code == 201, response.text
    task = response.json()
    assert task["status"] == "queued"
    assert task["reserved_cents"] == 400
    assert task["capability_snapshot"]["grant_config_override"] == override
    assert task["capability_snapshot"]["effective"] == {
        "modes": ["text_to_video"],
        "input_media_types": [],
        "required_resource_keys": [],
        "limits": {
            "max_prompt_length": 10000,
            "max_images": 0,
            "max_videos": 0,
            "max_audio": 0,
            "duration_seconds": [5],
            "aspect_ratios": ["16:9"],
            "resolutions": ["720p"],
            "output_counts": [1],
        },
    }
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(GenerationTask.id))) == 1
        assert session.scalar(select(func.count(RelaySubmissionOutbox.id))) == 1


def test_required_resources_default_deny_and_company_override_only_adds(
    app, client, tenant, tenant_headers
):
    base_key = "feature.base-generation"
    company_key = "feature.company-generation-policy"
    capability = {
        **BASE_CAPABILITY,
        "required_resource_keys": [base_key],
    }
    model_id = _seed_with_override(
        app,
        tenant["company_id"],
        capability=capability,
        override={"required_resource_keys": [company_key]},
    )
    _recharge(client, tenant, tenant_headers, suffix="required-resources")

    missing_definitions = _create(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="required-resources-missing-definitions",
        payload={},
    )
    assert missing_definitions.status_code == 403, missing_definitions.text
    _assert_rejected_before_money_or_outbox(app, tenant["company_id"])

    with app.state.session_factory.begin() as session:
        base_resource = ResourceDefinition(
            key=base_key,
            kind=ResourceKind.FEATURE,
            display_name="Base generation",
            active=True,
        )
        company_resource = ResourceDefinition(
            key=company_key,
            kind=ResourceKind.FEATURE,
            display_name="Company generation policy",
            active=True,
        )
        session.add_all([base_resource, company_resource])
        session.flush()
        session.add(
            CompanyResourceGrant(
                company_id=tenant["company_id"],
                resource_id=base_resource.id,
                enabled=True,
            )
        )

    missing_override_grant = _create(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="required-resources-missing-override",
        payload={},
    )
    assert missing_override_grant.status_code == 403, missing_override_grant.text
    _assert_rejected_before_money_or_outbox(app, tenant["company_id"])

    with app.state.session_factory.begin() as session:
        company_resource = session.scalar(
            select(ResourceDefinition).where(
                ResourceDefinition.key == company_key
            )
        )
        assert company_resource is not None
        session.add(
            CompanyResourceGrant(
                company_id=tenant["company_id"],
                resource_id=company_resource.id,
                enabled=False,
            )
        )

    disabled_override_grant = _create(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="required-resources-disabled-override",
        payload={},
    )
    assert disabled_override_grant.status_code == 403, disabled_override_grant.text
    _assert_rejected_before_money_or_outbox(app, tenant["company_id"])

    with app.state.session_factory.begin() as session:
        company_resource = session.scalar(
            select(ResourceDefinition).where(
                ResourceDefinition.key == company_key
            )
        )
        assert company_resource is not None
        company_grant = session.scalar(
            select(CompanyResourceGrant).where(
                CompanyResourceGrant.company_id == tenant["company_id"],
                CompanyResourceGrant.resource_id == company_resource.id,
            )
        )
        assert company_grant is not None
        company_grant.enabled = True

    accepted = _create(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="required-resources-accepted",
        payload={},
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["capability_snapshot"]["effective"][
        "required_resource_keys"
    ] == [base_key, company_key]


def test_required_resource_quota_and_concurrency_apply_to_generation_admission(
    app, client, tenant, tenant_headers
):
    resource_key = "feature.generation-limited"
    capability = {
        **BASE_CAPABILITY,
        "required_resource_keys": [resource_key],
    }
    model_id = _seed_with_override(
        app,
        tenant["company_id"],
        capability=capability,
    )
    with app.state.session_factory.begin() as session:
        resource = ResourceDefinition(
            key=resource_key,
            kind=ResourceKind.FEATURE,
            display_name="Generation limited feature",
            active=True,
        )
        session.add(resource)
        session.flush()
        session.add(
            CompanyResourceGrant(
                company_id=tenant["company_id"],
                resource_id=resource.id,
                enabled=True,
                call_quota=2,
                concurrency_limit=1,
            )
        )
    _recharge(client, tenant, tenant_headers, suffix="resource-policy")

    first = _create(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="resource-policy-first",
        payload={},
    )
    assert first.status_code == 201, first.text
    blocked_active = _create(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="resource-policy-active",
        payload={},
    )
    assert blocked_active.status_code == 403, blocked_active.text
    assert "resource concurrency" in blocked_active.json()["detail"]

    with app.state.session_factory.begin() as session:
        session.get(GenerationTask, first.json()["id"]).status = TaskStatus.FAILED
    second = _create(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="resource-policy-second",
        payload={},
    )
    assert second.status_code == 201, second.text

    with app.state.session_factory.begin() as session:
        session.get(GenerationTask, second.json()["id"]).status = TaskStatus.FAILED
    quota_blocked = _create(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="resource-policy-quota",
        payload={},
    )
    assert quota_blocked.status_code == 403, quota_blocked.text
    assert "resource call quota" in quota_blocked.json()["detail"]


def test_mode_capability_declares_its_required_resource_without_hardcoded_keys(
    app, client, tenant, tenant_headers
):
    resource_key = "feature.private-image-animation"
    image = upload_asset(
        client,
        tenant,
        tenant_headers,
        idempotency_key="mode-resource-image",
    ).json()
    capability = {
        "input_media_types": ["image"],
        "required_resource_keys": [resource_key],
        "limits": {
            "max_images": 1,
            "max_videos": 0,
            "max_audio": 0,
            "duration_seconds": [5],
            "aspect_ratios": ["16:9"],
            "resolutions": ["720p"],
            "output_counts": [1],
        },
    }
    model_id = seed_model(
        app,
        tenant["company_id"],
        capability_key="image-to-video",
        capability_config=capability,
    )
    _recharge(client, tenant, tenant_headers, suffix="mode-resource")
    payload = {
        "mode": "image_to_video",
        "assets": [{"asset_id": image["id"], "media_type": "image"}],
    }

    denied = _create(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="mode-resource-denied",
        payload=payload,
    )
    assert denied.status_code == 403, denied.text
    _assert_rejected_before_money_or_outbox(app, tenant["company_id"])

    with app.state.session_factory.begin() as session:
        resource = ResourceDefinition(
            key=resource_key,
            kind=ResourceKind.FEATURE,
            display_name="Private image animation",
            active=True,
        )
        session.add(resource)
        session.flush()
        session.add(
            CompanyResourceGrant(
                company_id=tenant["company_id"],
                resource_id=resource.id,
                enabled=True,
            )
        )

    accepted = _create(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="mode-resource-accepted",
        payload=payload,
    )
    assert accepted.status_code == 201, accepted.text
