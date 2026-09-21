from __future__ import annotations

import pytest
from pydantic import ValidationError

from platform_api.relay_client import (
    RelayAsset,
    RelayDirectorShotInput,
    RelayGenerationCapabilities,
)

from .test_director_shot_packages import _canonical_sha256, _manifest


def _limits() -> dict[str, object]:
    return {
        "max_prompt_length": 2_000,
        "max_images": 2,
        "max_videos": 0,
        "max_audio": 0,
        "duration_seconds": [5],
        "aspect_ratios": ["16:9"],
        "resolutions": ["720p"],
        "output_counts": [1],
    }


def test_relay_asset_accepts_legacy_input_and_validates_closed_role() -> None:
    assert (
        RelayAsset.model_validate(
            {
                "url": "https://assets.example.test/legacy.png",
                "media_type": "image",
            }
        ).role
        is None
    )
    assert (
        RelayAsset.model_validate(
            {
                "url": "https://assets.example.test/first.png",
                "media_type": "image",
                "role": "first_frame",
            }
        ).role
        == "first_frame"
    )
    with pytest.raises(ValidationError, match="requires video media"):
        RelayAsset.model_validate(
            {
                "url": "https://assets.example.test/previs.png",
                "media_type": "image",
                "role": "director_previs",
            }
        )


def test_relay_capability_v3_parses_semantic_and_structured_inputs() -> None:
    capability = RelayGenerationCapabilities.model_validate(
        {
            "schema_version": 3,
            "modes": {
                "image_to_video": {
                    "input_media_types": ["image"],
                    "input_roles": [
                        "reference_image",
                        "first_frame",
                        "last_frame",
                    ],
                    "temporal_controls": ["first_frame", "last_frame"],
                    "structured_inputs": ["director_shot_v1"],
                    "supports_face": False,
                    "required_resource_keys": [],
                    "limits": _limits(),
                }
            },
        }
    )

    mode = capability.modes["image_to_video"]
    assert mode.input_roles == [
        "reference_image",
        "first_frame",
        "last_frame",
    ]
    assert mode.temporal_controls == ["first_frame", "last_frame"]
    assert mode.structured_inputs == ["director_shot_v1"]


@pytest.mark.parametrize("schema_version", [1, 2])
def test_relay_capability_v1_v2_reject_v3_role_fields(
    schema_version: int,
) -> None:
    with pytest.raises(ValidationError, match="v3 capability fields"):
        RelayGenerationCapabilities.model_validate(
            {
                "schema_version": schema_version,
                "modes": {
                    "image_to_video": {
                        "input_media_types": ["image"],
                        "input_roles": ["first_frame"],
                        "temporal_controls": ["first_frame"],
                        "structured_inputs": [],
                        "supports_face": False,
                        "required_resource_keys": [],
                        "limits": _limits(),
                    }
                },
            }
        )


def test_relay_capability_v3_requires_explicit_empty_semantic_arrays() -> None:
    with pytest.raises(ValidationError, match="must explicitly contain"):
        RelayGenerationCapabilities.model_validate(
            {
                "schema_version": 3,
                "modes": {
                    "text_to_video": {
                        "input_media_types": [],
                        "supports_face": False,
                        "required_resource_keys": [],
                        "conditional_required_resource_keys": {},
                        "limits": {
                            **_limits(),
                            "max_images": 0,
                        },
                    }
                },
            }
        )


def test_relay_director_shot_revalidates_manifest_and_digest() -> None:
    manifest = _manifest({"sha256": "a" * 64})
    value = {
        "manifest": manifest,
        "manifest_sha256": _canonical_sha256(manifest),
        "sealed_revision": "sha256:" + ("b" * 64),
    }
    accepted = RelayDirectorShotInput.model_validate(value)
    assert accepted.manifest["camera"]["stableId"] == "camera-main"

    with pytest.raises(ValidationError, match="does not match manifest"):
        RelayDirectorShotInput.model_validate(
            {**value, "manifest_sha256": "sha256:" + ("0" * 64)}
        )
