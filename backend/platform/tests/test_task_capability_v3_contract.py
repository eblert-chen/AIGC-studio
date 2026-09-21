from __future__ import annotations

from copy import deepcopy

import pytest

from platform_api.services.errors import ConflictError
from platform_api.services.task_admission import TaskCapabilityAdmission


def _mode_v3() -> dict:
    return {
        "input_media_types": ["image", "video"],
        "input_roles": [
            "reference_image",
            "first_frame",
            "last_frame",
            "director_previs",
        ],
        "temporal_controls": ["first_frame", "last_frame"],
        "structured_inputs": ["director_shot_v1"],
        "supports_face": False,
        "required_resource_keys": [],
        "conditional_required_resource_keys": {},
        "limits": {
            "max_prompt_length": 10_000,
            "max_images": 2,
            "max_videos": 1,
            "max_audio": 0,
            "duration_seconds": [5],
            "aspect_ratios": ["16:9"],
            "resolutions": ["720p"],
            "output_counts": [1],
        },
    }


def _catalog_v3() -> dict:
    return {
        "generation": {
            "schema_version": 3,
            "modes": {"image_to_video": _mode_v3()},
        }
    }


def _request(*assets: dict) -> dict:
    return {
        "mode": "image_to_video",
        "prompt": "在已锁定的机位中让人物向前走一步",
        "duration_seconds": 5,
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "output_count": 1,
        "assets": list(assets),
    }


def test_v3_catalog_preserves_structured_roles_and_temporal_controls() -> None:
    document = TaskCapabilityAdmission.validate_catalog(
        _catalog_v3(), require_usable=True
    )

    mode = document["modes"]["image_to_video"]
    assert document["schema_version"] == 3
    assert mode["input_roles"] == [
        "director_previs",
        "first_frame",
        "last_frame",
        "reference_image",
    ]
    assert mode["temporal_controls"] == ["first_frame", "last_frame"]
    assert mode["structured_inputs"] == ["director_shot_v1"]


@pytest.mark.parametrize(
    "assets",
    [
        (
            {
                "asset_id": "asset-composition",
                "media_type": "image",
                "role": "reference_image",
            },
        ),
        (
            {
                "asset_id": "asset-first",
                "media_type": "image",
                "role": "first_frame",
            },
            {
                "asset_id": "asset-last",
                "media_type": "image",
                "role": "last_frame",
            },
        ),
        # Existing callers that omit role remain valid; ordering is not
        # reinterpreted as a semantic role.
        ({"asset_id": "legacy-image", "media_type": "image"},),
    ],
)
def test_v3_request_accepts_declared_roles_and_legacy_omission(
    assets: tuple[dict, ...]
) -> None:
    snapshot = TaskCapabilityAdmission.validate(
        capability_map=_catalog_v3(),
        config_override={},
        request_payload=_request(*assets),
    )

    assert snapshot["schema_version"] == 3


def test_v3_request_rejects_mixed_role_and_legacy_assets_before_admission() -> None:
    with pytest.raises(ConflictError, match="cannot mix explicit roles"):
        TaskCapabilityAdmission.validate(
            capability_map=_catalog_v3(),
            config_override={},
            request_payload=_request(
                {
                    "asset_id": "asset-composition",
                    "media_type": "image",
                    "role": "reference_image",
                },
                {"asset_id": "legacy-image", "media_type": "image"},
            ),
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda catalog, request: request["assets"][0].update(
                role="director_previs"
            ),
            "does not match media_type",
        ),
        (
            lambda catalog, request: request["assets"][0].update(
                role="driving_audio"
            ),
            "does not match media_type",
        ),
        (
            lambda catalog, request: (
                catalog["generation"]["modes"]["image_to_video"]
                ["input_roles"].remove("reference_image")
            ),
            "not allowed by the model capability",
        ),
        (
            lambda catalog, request: (
                catalog["generation"]["modes"]["image_to_video"]
                ["temporal_controls"].remove("first_frame"),
                request["assets"][0].update(role="first_frame"),
            ),
            "Temporal input role is not allowed",
        ),
    ],
)
def test_role_request_fails_closed_before_task_admission(mutate, message: str) -> None:
    catalog = _catalog_v3()
    request = _request(
        {
            "asset_id": "asset-composition",
            "media_type": "image",
            "role": "reference_image",
        }
    )
    mutate(catalog, request)

    with pytest.raises(ConflictError, match=message):
        TaskCapabilityAdmission.validate(
            capability_map=catalog,
            config_override={},
            request_payload=request,
        )


def test_v3_catalog_rejects_temporal_control_without_matching_input_role() -> None:
    catalog = _catalog_v3()
    mode = catalog["generation"]["modes"]["image_to_video"]
    mode["temporal_controls"] = ["reference_video"]

    with pytest.raises(ConflictError, match="must also be declared input roles"):
        TaskCapabilityAdmission.validate_catalog(catalog, require_usable=True)


def test_v1_and_v2_cannot_silently_advertise_v3_roles() -> None:
    for schema_version in (1, 2):
        catalog = _catalog_v3()
        document = catalog["generation"]
        document["schema_version"] = schema_version
        mode = document["modes"]["image_to_video"]
        mode.pop("input_roles")
        mode.pop("temporal_controls")
        mode.pop("structured_inputs")
        if schema_version == 1:
            mode.pop("conditional_required_resource_keys")
        accepted = TaskCapabilityAdmission.validate_catalog(
            catalog, require_usable=True
        )
        assert "input_roles" not in accepted["modes"]["image_to_video"]

        roleful_request = _request(
            {
                "asset_id": "asset-composition",
                "media_type": "image",
                "role": "reference_image",
            }
        )
        with pytest.raises(ConflictError, match="not allowed by the model capability"):
            TaskCapabilityAdmission.validate(
                capability_map=catalog,
                config_override={},
                request_payload=roleful_request,
            )


def test_complete_v3_restriction_cannot_expand_roles_or_temporal_controls() -> None:
    ceiling = deepcopy(_catalog_v3()["generation"])
    candidate = deepcopy(ceiling)
    candidate["modes"]["image_to_video"]["input_media_types"].append("audio")
    candidate["modes"]["image_to_video"]["input_roles"].append(
        "driving_audio"
    )
    candidate["modes"]["image_to_video"]["limits"]["max_audio"] = 1

    with pytest.raises(ConflictError, match="input_media_types cannot expand"):
        TaskCapabilityAdmission.validate_full_restriction(
            ceiling=ceiling,
            candidate=candidate,
        )


def test_v3_director_package_fails_closed_without_structured_input_gate() -> None:
    catalog = _catalog_v3()
    catalog["generation"]["modes"]["image_to_video"][
        "structured_inputs"
    ] = []
    request = _request(
        {
            "asset_id": "asset-composition",
            "media_type": "image",
            "role": "reference_image",
        }
    )
    request["director_shot_package"] = {
        "package_id": "dsp_" + ("a" * 32),
        "manifest_sha256": "sha256:" + ("b" * 64),
        "sealed_revision": "sha256:" + ("c" * 64),
    }

    with pytest.raises(ConflictError, match="director_shot_v1 is not allowed"):
        TaskCapabilityAdmission.validate(
            capability_map=catalog,
            config_override={},
            request_payload=request,
        )
