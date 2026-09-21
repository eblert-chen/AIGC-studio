from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from platform_api.models import (
    CompanyModelGrant,
    CompanyResourceGrant,
    GenerationTask,
    ResourceDefinition,
    ResourceKind,
    TaskStatus,
)
from platform_api.services.errors import ConflictError
from platform_api.services.task_admission import TaskCapabilityAdmission

from .test_generation_capability_v1 import (
    _create_task,
    _provision_model,
    _recharge,
    _side_effect_snapshot,
)


BASE_RESOURCE_KEY = "feature.base-generation"
FACE_RESOURCE_KEY = "face.library"


def _capability_v2(
    *,
    required_resource_keys: list[str] | None = None,
    face_required_resource_keys: list[str] | None = None,
) -> dict:
    return {
        "schema_version": 2,
        "modes": {
            "text_to_video": {
                "input_media_types": [],
                "supports_face": True,
                "required_resource_keys": required_resource_keys or [],
                "conditional_required_resource_keys": (
                    {"face_enabled": face_required_resource_keys}
                    if face_required_resource_keys
                    else {}
                ),
                "limits": {
                    "max_prompt_length": 2_000,
                    "max_images": 0,
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


def _add_resource(
    app,
    *,
    company_id: str,
    key: str,
    display_name: str,
    active: bool = True,
    granted: bool = True,
    enabled: bool = True,
    effective_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> tuple[str, str | None]:
    with app.state.session_factory.begin() as session:
        resource = ResourceDefinition(
            key=key,
            kind=ResourceKind.FEATURE,
            display_name=display_name,
            description="Generation readiness regression fixture",
            active=active,
        )
        session.add(resource)
        session.flush()
        if not granted:
            return resource.id, None
        grant = CompanyResourceGrant(
            company_id=company_id,
            resource_id=resource.id,
            enabled=enabled,
            effective_at=effective_at,
            expires_at=expires_at,
        )
        session.add(grant)
        session.flush()
        return resource.id, grant.id


def _available_model(client, tenant, tenant_headers, *, model_id: str) -> dict:
    response = client.get(
        f"/api/v1/companies/{tenant['company_id']}/models",
        headers=tenant_headers,
    )
    assert response.status_code == 200, response.text
    return next(item for item in response.json() if item["id"] == model_id)


def _assert_blocker(
    readiness: dict,
    *,
    code: str,
    resource_key: str,
    resource_name: str | None = None,
) -> None:
    assert readiness["ready"] is False
    assert readiness["status"] == "blocked"
    blocker = next(
        item
        for item in readiness["blockers"]
        if item["resource_key"] == resource_key
    )
    assert blocker["code"] == code
    assert isinstance(blocker["resource_name"], str)
    assert blocker["resource_name"].strip()
    if resource_name is not None:
        assert blocker["resource_name"] == resource_name


def test_available_model_separates_default_and_face_option_readiness(
    app, client, tenant, tenant_headers
):
    model_id, _ = _provision_model(
        client,
        tenant,
        suffix="readiness-default-face",
        capability=_capability_v2(
            required_resource_keys=[BASE_RESOURCE_KEY],
            face_required_resource_keys=[FACE_RESOURCE_KEY],
        ),
    )
    _add_resource(
        app,
        company_id=tenant["company_id"],
        key=BASE_RESOURCE_KEY,
        display_name="基础生成能力",
    )
    face_resource_id, _ = _add_resource(
        app,
        company_id=tenant["company_id"],
        key=FACE_RESOURCE_KEY,
        display_name="人物参考库",
        granted=False,
    )

    model = _available_model(
        client, tenant, tenant_headers, model_id=model_id
    )
    checked_at = datetime.fromisoformat(
        model["readiness_checked_at"].replace("Z", "+00:00")
    )
    assert checked_at.tzinfo is not None
    mode = model["mode_readiness"]["text_to_video"]
    assert mode["default"] == {
        "ready": True,
        "status": "ready",
        "blockers": [],
    }
    assert mode["options"]["face_enabled"]["supported"] is True
    _assert_blocker(
        mode["options"]["face_enabled"],
        code="resource_not_granted",
        resource_key=FACE_RESOURCE_KEY,
        resource_name="人物参考库",
    )

    with app.state.session_factory.begin() as session:
        session.add(
            CompanyResourceGrant(
                company_id=tenant["company_id"],
                resource_id=face_resource_id,
                enabled=True,
            )
        )

    refreshed = _available_model(
        client, tenant, tenant_headers, model_id=model_id
    )["mode_readiness"]["text_to_video"]
    assert refreshed["default"]["ready"] is True
    assert refreshed["options"]["face_enabled"] == {
        "supported": True,
        "ready": True,
        "status": "ready",
        "blockers": [],
    }


@pytest.mark.parametrize(
    ("case_name", "resource_kwargs", "expected_code"),
    [
        ("not-defined", None, "resource_not_defined"),
        (
            "inactive",
            {"active": False, "granted": True},
            "resource_inactive",
        ),
        (
            "not-granted",
            {"active": True, "granted": False},
            "resource_not_granted",
        ),
        (
            "not-yet-effective",
            {
                "active": True,
                "granted": True,
                "effective_at": datetime.now(timezone.utc)
                + timedelta(hours=1),
            },
            "resource_not_yet_effective",
        ),
        (
            "expired",
            {
                "active": True,
                "granted": True,
                "expires_at": datetime.now(timezone.utc)
                - timedelta(seconds=1),
            },
            "resource_grant_expired",
        ),
    ],
)
def test_available_model_reports_each_unconditional_resource_blocker(
    app,
    client,
    tenant,
    tenant_headers,
    case_name,
    resource_kwargs,
    expected_code,
):
    resource_key = f"feature.readiness-{case_name}"
    model_id, _ = _provision_model(
        client,
        tenant,
        suffix=f"readiness-{case_name}",
        capability=_capability_v2(required_resource_keys=[resource_key]),
    )
    if resource_kwargs is not None:
        _add_resource(
            app,
            company_id=tenant["company_id"],
            key=resource_key,
            display_name=f"就绪检查 {case_name}",
            **resource_kwargs,
        )

    model = _available_model(
        client, tenant, tenant_headers, model_id=model_id
    )
    _assert_blocker(
        model["mode_readiness"]["text_to_video"]["default"],
        code=expected_code,
        resource_key=resource_key,
        resource_name=(
            None if resource_kwargs is None else f"就绪检查 {case_name}"
        ),
    )


def test_face_resource_is_required_only_when_face_is_requested(
    app, client, tenant, tenant_headers
):
    model_id, _ = _provision_model(
        client,
        tenant,
        suffix="conditional-face",
        capability=_capability_v2(
            face_required_resource_keys=[FACE_RESOURCE_KEY]
        ),
    )
    face_resource_id, _ = _add_resource(
        app,
        company_id=tenant["company_id"],
        key=FACE_RESOURCE_KEY,
        display_name="人物参考库",
        granted=False,
    )
    _recharge(client, tenant, tenant_headers, suffix="conditional-face")

    without_face = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="conditional-face-off",
        face_enabled=False,
    )
    assert without_face.status_code == 201, without_face.text
    assert without_face.json()["capability_snapshot"]["resource_grants"] == []

    before = _side_effect_snapshot(app, tenant["company_id"])
    blocked_face = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="conditional-face-on-blocked",
        face_enabled=True,
    )
    assert blocked_face.status_code == 403, blocked_face.text
    assert _side_effect_snapshot(app, tenant["company_id"]) == before

    with app.state.session_factory.begin() as session:
        session.add(
            CompanyResourceGrant(
                company_id=tenant["company_id"],
                resource_id=face_resource_id,
                enabled=True,
            )
        )

    accepted_face = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="conditional-face-on-ready",
        face_enabled=True,
    )
    assert accepted_face.status_code == 201, accepted_face.text
    snapshot = accepted_face.json()["capability_snapshot"]
    assert snapshot["effective"]["conditional_required_resource_keys"] == {
        "face_enabled": [FACE_RESOURCE_KEY]
    }
    assert [item["key"] for item in snapshot["resource_grants"]] == [
        FACE_RESOURCE_KEY
    ]


def test_v2_mixed_modes_normalize_an_omitted_conditional_map(
    client, tenant, tenant_headers
):
    capability = _capability_v2(
        face_required_resource_keys=[FACE_RESOURCE_KEY]
    )
    capability["modes"]["text_to_image"] = {
        "input_media_types": [],
        "supports_face": False,
        "required_resource_keys": [],
        "limits": {
            "max_prompt_length": 2_000,
            "max_images": 0,
            "max_videos": 0,
            "max_audio": 0,
            "duration_seconds": [5],
            "aspect_ratios": ["1:1"],
            "resolutions": ["1024p"],
            "output_counts": [1],
        },
    }

    model_id, _ = _provision_model(
        client,
        tenant,
        suffix="readiness-v2-mixed-modes",
        capability=capability,
    )
    model = _available_model(
        client, tenant, tenant_headers, model_id=model_id
    )
    modes = model["effective_capabilities"]["modes"]

    assert modes["text_to_video"]["conditional_required_resource_keys"] == {
        "face_enabled": [FACE_RESOURCE_KEY]
    }
    assert modes["text_to_image"]["conditional_required_resource_keys"] == {}
    assert model["mode_readiness"]["text_to_image"]["options"][
        "face_enabled"
    ] == {
        "supported": False,
        "ready": False,
        "status": "unsupported",
        "blockers": [],
    }


def test_v1_capability_readiness_remains_compatible_without_face_resources(
    client, tenant, tenant_headers
):
    capability_v1 = _capability_v2()
    capability_v1["schema_version"] = 1
    capability_v1["modes"]["text_to_video"].pop(
        "conditional_required_resource_keys"
    )
    model_id, _ = _provision_model(
        client,
        tenant,
        suffix="readiness-v1-compatible",
        capability=capability_v1,
    )

    model = _available_model(
        client, tenant, tenant_headers, model_id=model_id
    )
    mode = model["mode_readiness"]["text_to_video"]
    assert mode["default"]["ready"] is True
    assert mode["options"]["face_enabled"] == {
        "supported": True,
        "ready": True,
        "status": "ready",
        "blockers": [],
    }


def test_full_restriction_cannot_drop_a_face_resource_requirement() -> None:
    ceiling = _capability_v2(
        face_required_resource_keys=[FACE_RESOURCE_KEY]
    )
    omitted = deepcopy(ceiling)
    omitted["modes"]["text_to_video"].pop(
        "conditional_required_resource_keys"
    )
    cleared = deepcopy(ceiling)
    cleared["modes"]["text_to_video"][
        "conditional_required_resource_keys"
    ] = {}

    for candidate in (omitted, cleared):
        with pytest.raises(ConflictError, match="remove face requirements"):
            TaskCapabilityAdmission.validate_full_restriction(
                ceiling=ceiling,
                candidate=candidate,
            )


def test_disabling_face_is_a_legal_company_and_full_document_restriction() -> None:
    ceiling = _capability_v2(
        face_required_resource_keys=[FACE_RESOURCE_KEY]
    )
    sparse_override = {
        "schema_version": 2,
        "modes": {"text_to_video": {"supports_face": False}},
    }
    effective = TaskCapabilityAdmission.validate_company_override(
        capability_map={"generation": ceiling},
        config_override=sparse_override,
    )
    assert effective["modes"]["text_to_video"]["supports_face"] is False
    assert effective["modes"]["text_to_video"][
        "conditional_required_resource_keys"
    ] == {}

    full_candidate = deepcopy(ceiling)
    full_candidate["modes"]["text_to_video"]["supports_face"] = False
    full_candidate["modes"]["text_to_video"].pop(
        "conditional_required_resource_keys"
    )
    normalized = TaskCapabilityAdmission.validate_full_restriction(
        ceiling=ceiling,
        candidate=full_candidate,
    )
    assert normalized["modes"]["text_to_video"]["supports_face"] is False
    assert normalized["modes"]["text_to_video"][
        "conditional_required_resource_keys"
    ] == {}


def _set_model_grant_limits(
    app,
    *,
    company_id: str,
    model_id: str,
    call_quota: int | None,
    concurrency_limit: int | None,
) -> None:
    with app.state.session_factory.begin() as session:
        grant = session.scalar(
            select(CompanyModelGrant).where(
                CompanyModelGrant.company_id == company_id,
                CompanyModelGrant.model_id == model_id,
            )
        )
        assert grant is not None
        grant.call_quota = call_quota
        grant.concurrency_limit = concurrency_limit


def test_model_call_quota_blocks_both_readiness_and_task_admission(
    app, client, tenant, tenant_headers
) -> None:
    model_id, _ = _provision_model(
        client,
        tenant,
        suffix="readiness-model-quota",
        capability=_capability_v2(),
    )
    _set_model_grant_limits(
        app,
        company_id=tenant["company_id"],
        model_id=model_id,
        call_quota=1,
        concurrency_limit=None,
    )
    _recharge(client, tenant, tenant_headers, suffix="readiness-model-quota")
    assert _available_model(
        client, tenant, tenant_headers, model_id=model_id
    )["mode_readiness"]["text_to_video"]["default"]["ready"] is True

    first = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="readiness-model-quota-first",
    )
    assert first.status_code == 201, first.text
    default = _available_model(
        client, tenant, tenant_headers, model_id=model_id
    )["mode_readiness"]["text_to_video"]["default"]
    _assert_blocker(
        default,
        code="model_call_quota_exhausted",
        resource_key=f"model:{model_id}",
    )

    before = _side_effect_snapshot(app, tenant["company_id"])
    rejected = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="readiness-model-quota-second",
    )
    assert rejected.status_code == 403, rejected.text
    assert _side_effect_snapshot(app, tenant["company_id"]) == before


def test_model_concurrency_readiness_recovers_after_a_terminal_task(
    app, client, tenant, tenant_headers
) -> None:
    model_id, _ = _provision_model(
        client,
        tenant,
        suffix="readiness-model-concurrency",
        capability=_capability_v2(),
    )
    _set_model_grant_limits(
        app,
        company_id=tenant["company_id"],
        model_id=model_id,
        call_quota=None,
        concurrency_limit=1,
    )
    _recharge(
        client,
        tenant,
        tenant_headers,
        suffix="readiness-model-concurrency",
    )
    first = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="readiness-model-concurrency-first",
    )
    assert first.status_code == 201, first.text

    blocked = _available_model(
        client, tenant, tenant_headers, model_id=model_id
    )["mode_readiness"]["text_to_video"]["default"]
    _assert_blocker(
        blocked,
        code="model_concurrency_saturated",
        resource_key=f"model:{model_id}",
    )
    rejected = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="readiness-model-concurrency-second",
    )
    assert rejected.status_code == 403, rejected.text

    with app.state.session_factory.begin() as session:
        task = session.get(GenerationTask, first.json()["id"])
        assert task is not None
        task.status = TaskStatus.FAILED

    recovered = _available_model(
        client, tenant, tenant_headers, model_id=model_id
    )["mode_readiness"]["text_to_video"]["default"]
    assert recovered == {"ready": True, "status": "ready", "blockers": []}
