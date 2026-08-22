from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from .models import (
    CapabilityLimits,
    GenerationCapabilityDocument,
    GenerationMode,
    ModelCapability,
    ModelListResponse,
    ModelResource,
    ModeCapabilityResponse,
)


_MEDIA_ORDER = ("image", "video", "audio")


def _revision(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _common_values(profiles: list[ModelCapability], field: str) -> list:
    values = set(getattr(profiles[0].limits, field))
    for profile in profiles[1:]:
        values.intersection_update(getattr(profile.limits, field))
    return sorted(values)


def _mode_contract(
    profiles: list[ModelCapability],
) -> ModeCapabilityResponse | None:
    """Return the capability guaranteed across every route for this alias.

    A model alias may be backed by several provider accounts. Advertising the
    union would let the browser create requests that cannot fail over. The
    public catalog therefore exposes the conservative intersection while the
    router keeps every provider-specific profile private.
    """

    durations = _common_values(profiles, "duration_seconds")
    aspect_ratios = _common_values(profiles, "aspect_ratios")
    resolutions = _common_values(profiles, "resolutions")
    output_counts = _common_values(profiles, "output_counts")
    if not durations or not aspect_ratios or not resolutions or not output_counts:
        return None

    common_media = set(profiles[0].input_media_types)
    for profile in profiles[1:]:
        common_media.intersection_update(profile.input_media_types)
    maxima = {
        "image": min(profile.limits.max_images for profile in profiles),
        "video": min(profile.limits.max_videos for profile in profiles),
        "audio": min(profile.limits.max_audio for profile in profiles),
    }
    for media_type in tuple(maxima):
        if media_type not in common_media:
            maxima[media_type] = 0
    input_media_types = [
        media_type
        for media_type in _MEDIA_ORDER
        if media_type in common_media and maxima[media_type] > 0
    ]
    return ModeCapabilityResponse(
        input_media_types=input_media_types,
        supports_face=all(profile.supports_face for profile in profiles),
        required_resource_keys=[],
        limits=CapabilityLimits(
            max_prompt_length=min(
                profile.limits.max_prompt_length for profile in profiles
            ),
            max_images=maxima["image"],
            max_videos=maxima["video"],
            max_audio=maxima["audio"],
            duration_seconds=durations,
            aspect_ratios=aspect_ratios,
            resolutions=resolutions,
            output_counts=output_counts,
        ),
    )


def build_model_catalog(profiles: list[ModelCapability]) -> ModelListResponse:
    by_model_mode: dict[
        str, dict[GenerationMode, list[ModelCapability]]
    ] = defaultdict(lambda: defaultdict(list))
    for profile in profiles:
        for mode in profile.modes:
            by_model_mode[profile.model][mode].append(profile)

    resources: list[ModelResource] = []
    for model_id in sorted(by_model_mode):
        modes: dict[GenerationMode, ModeCapabilityResponse] = {}
        for mode in sorted(by_model_mode[model_id], key=lambda item: item.value):
            contract = _mode_contract(by_model_mode[model_id][mode])
            if contract is not None:
                modes[mode] = contract
        if not modes:
            continue
        document = GenerationCapabilityDocument(modes=modes)
        serialized = document.model_dump(mode="json")
        resources.append(
            ModelResource(
                id=model_id,
                capability_revision=_revision(serialized),
                capabilities=document,
            )
        )

    catalog_basis = [
        {"id": resource.id, "revision": resource.capability_revision}
        for resource in resources
    ]
    return ModelListResponse(
        data=resources,
        catalog_revision=_revision(catalog_basis),
    )
