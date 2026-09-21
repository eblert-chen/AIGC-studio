"""Reviewed Google model identity metadata for Relay-discovered drafts.

This module deliberately does not contain model capabilities, route state,
credentials or customer prices.  Those boundaries remain, respectively, the
live Relay catalog, signed route evidence, the Relay credential vault and
explicit Platform administrator decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache
import json
from pathlib import Path
import re
from urllib.parse import urlparse


DEFAULT_GOOGLE_VIDEO_MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "catalog"
    / "google_video_models.v1.json"
)

_PUBLIC_MODEL_ID = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
_DOCUMENT_FIELDS = {
    "schema_version",
    "reviewed_at",
    "provider_key",
    "capability_source",
    "customer_pricing_policy",
    "models",
}
_MODEL_FIELDS = {
    "public_model_id",
    "display_name",
    "billing_mode",
    "lifecycle",
    "evidence_status",
    "new_routes_allowed",
    "provider_model_ids",
    "official_sources",
}
_GOOGLE_SOURCE_HOSTS = frozenset(
    {"ai.google.dev", "cloud.google.com", "docs.cloud.google.com"}
)
_EXECUTABLE_GEMINI_PROVIDER_IDS = {
    "gemini-omni-1.1-flash": ("gemini-omni-1.1-flash",),
    "veo-3.1": ("veo-3.1-generate-preview",),
    "veo-3.1-fast": ("veo-3.1-fast-generate-preview",),
}


@dataclass(frozen=True, slots=True)
class GoogleVideoDraftSpec:
    public_model_id: str
    display_name: str
    provider_key: str
    billing_mode: str
    provider_model_ids: tuple[str, ...]
    official_sources: tuple[str, ...]


def _required_string(value: object, *, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value) > maximum
    ):
        raise ValueError(f"Google model manifest field {field!r} is invalid")
    return value


@lru_cache(maxsize=8)
def load_google_video_draft_specs(
    manifest_path: Path = DEFAULT_GOOGLE_VIDEO_MANIFEST,
) -> tuple[GoogleVideoDraftSpec, ...]:
    """Load the reviewed identity allowlist and fail closed on any drift."""

    document = json.loads(manifest_path.read_bytes())
    if not isinstance(document, dict) or set(document) != _DOCUMENT_FIELDS:
        raise ValueError("Google model manifest document shape is invalid")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("Google model manifest must use schema_version 1")
    reviewed_at = document["reviewed_at"]
    if (
        not isinstance(reviewed_at, str)
        or date.fromisoformat(reviewed_at).isoformat() != reviewed_at
    ):
        raise ValueError("Google model manifest reviewed_at must be an ISO date")
    if document["provider_key"] != "google":
        raise ValueError("Google model manifest provider_key must be google")
    if document["capability_source"] != "relay_live_catalog":
        raise ValueError(
            "Google model capabilities must come from the live Relay catalog"
        )
    if document["customer_pricing_policy"] != "admin_approved_fixed_points":
        raise ValueError("Google customer pricing must require fixed-point approval")
    if not isinstance(document["models"], list) or not document["models"]:
        raise ValueError("Google model manifest has no candidates")

    specs: list[GoogleVideoDraftSpec] = []
    public_ids: set[str] = set()
    provider_ids: set[str] = set()
    for entry in document["models"]:
        if not isinstance(entry, dict) or set(entry) != _MODEL_FIELDS:
            raise ValueError("Google model manifest entry shape is invalid")
        public_id = _required_string(
            entry["public_model_id"], field="public_model_id", maximum=80
        )
        if _PUBLIC_MODEL_ID.fullmatch(public_id) is None or public_id in public_ids:
            raise ValueError("Google public model identities must be unique slugs")
        public_ids.add(public_id)
        display_name = _required_string(
            entry["display_name"], field="display_name", maximum=120
        )
        if (
            entry["billing_mode"] != "per_second"
            or entry["lifecycle"] != "acceptance_candidate"
            or entry["evidence_status"] != "relay_route_acceptance_required"
            or entry["new_routes_allowed"] is not True
        ):
            raise ValueError(
                "Google model candidate is not eligible for draft onboarding"
            )
        raw_provider_ids = entry["provider_model_ids"]
        if not isinstance(raw_provider_ids, list) or not raw_provider_ids:
            raise ValueError("Google model candidate needs provider model identities")
        normalized_provider_ids: list[str] = []
        for raw_provider_id in raw_provider_ids:
            provider_id = _required_string(
                raw_provider_id, field="provider_model_ids", maximum=191
            )
            if provider_id in provider_ids:
                raise ValueError("Google provider model identities must be unique")
            provider_ids.add(provider_id)
            normalized_provider_ids.append(provider_id)
        expected_provider_ids = _EXECUTABLE_GEMINI_PROVIDER_IDS.get(public_id)
        if tuple(normalized_provider_ids) != expected_provider_ids:
            raise ValueError(
                "Google public model candidate must bind only its reviewed "
                "Gemini Developer API provider identity"
            )
        raw_sources = entry["official_sources"]
        if not isinstance(raw_sources, list) or not raw_sources:
            raise ValueError("Google model candidate needs official sources")
        normalized_sources: list[str] = []
        for raw_source in raw_sources:
            source = _required_string(
                raw_source, field="official_sources", maximum=500
            )
            parsed = urlparse(source)
            if (
                parsed.scheme != "https"
                or parsed.hostname not in _GOOGLE_SOURCE_HOSTS
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
            ):
                raise ValueError(
                    "Google model sources must be canonical official HTTPS URLs"
                )
            normalized_sources.append(source)
        specs.append(
            GoogleVideoDraftSpec(
                public_model_id=public_id,
                display_name=display_name,
                provider_key="google",
                billing_mode="per_second",
                provider_model_ids=tuple(normalized_provider_ids),
                official_sources=tuple(normalized_sources),
            )
        )
    if [spec.public_model_id for spec in specs] != sorted(public_ids):
        raise ValueError(
            "Google model manifest entries must be ordered by public model id"
        )
    return tuple(specs)


def google_video_draft_spec(public_model_id: str) -> GoogleVideoDraftSpec | None:
    """Resolve only an exact reviewed public model identity."""

    return next(
        (
            spec
            for spec in load_google_video_draft_specs()
            if spec.public_model_id == public_model_id
        ),
        None,
    )
