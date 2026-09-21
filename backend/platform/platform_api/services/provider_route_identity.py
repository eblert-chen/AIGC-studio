from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any, Mapping
from uuid import RFC_4122, UUID

from .errors import ConflictError


IDENTITY_UNASSIGNED = "unassigned"
IDENTITY_BOUND = "bound"
IDENTITY_LEGACY_UNKNOWN = "legacy_unknown"
IDENTITY_STATUSES = frozenset(
    {IDENTITY_UNASSIGNED, IDENTITY_BOUND, IDENTITY_LEGACY_UNKNOWN}
)
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")

IDENTITY_FIELDS = (
    "identity_status",
    "provider_name",
    "provider_account_id",
    "provider_channel_id",
    "provider_route_id",
    "provider_key_index",
    "provider_key_fingerprint",
    "provider_credential_version",
    "route_key",
    "routing_release_sha256",
)


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_provider_route_identity(
    values: Mapping[str, Any],
    *,
    route_assigned: bool,
    route_id: int | None,
    route_key: str,
) -> dict[str, Any]:
    status = values.get("identity_status")
    material = {field: values.get(field) for field in IDENTITY_FIELDS[1:]}
    present = any(value is not None and value != "" for value in material.values())
    if status not in IDENTITY_STATUSES:
        raise ValueError("provider route identity status is invalid")
    if status == IDENTITY_UNASSIGNED:
        if route_assigned or present:
            raise ValueError("unassigned provider route identity is inconsistent")
    elif status == IDENTITY_LEGACY_UNKNOWN:
        if present:
            raise ValueError("legacy provider route identity must not be inferred")
    else:
        required_text = (
            "provider_name",
            "provider_account_id",
            "provider_key_fingerprint",
            "provider_credential_version",
            "route_key",
            "routing_release_sha256",
        )
        if not route_assigned or any(not material[field] for field in required_text):
            raise ValueError("bound provider route identity is incomplete")
        if any(
            not isinstance(material[field], str)
            or _TOKEN.fullmatch(material[field]) is None
            for field in ("provider_name", "provider_account_id", "route_key")
        ):
            raise ValueError("provider route identity token is invalid")
        if (
            not isinstance(material["provider_channel_id"], int)
            or isinstance(material["provider_channel_id"], bool)
            or material["provider_channel_id"] <= 0
            or not isinstance(material["provider_route_id"], int)
            or isinstance(material["provider_route_id"], bool)
            or material["provider_route_id"] <= 0
            or not isinstance(material["provider_key_index"], int)
            or isinstance(material["provider_key_index"], bool)
            or material["provider_key_index"] < 0
        ):
            raise ValueError("provider route numeric identity is invalid")
        if _HEX64.fullmatch(str(material["provider_key_fingerprint"])) is None:
            raise ValueError("provider key fingerprint is invalid")
        try:
            parsed_credential_version = UUID(
                str(material["provider_credential_version"])
            )
            credential_version = str(parsed_credential_version)
        except ValueError as exc:
            raise ValueError("provider credential version is invalid") from exc
        if (
            credential_version != material["provider_credential_version"]
            or parsed_credential_version.version not in {1, 2, 3, 4, 5}
            or parsed_credential_version.variant != RFC_4122
        ):
            raise ValueError("provider credential version is not canonical")
        if _SHA256.fullmatch(str(material["routing_release_sha256"])) is None:
            raise ValueError("provider routing release digest is invalid")
        if material["route_key"] != route_key:
            raise ValueError("provider route key does not match channel key")
        if material["provider_route_id"] != route_id:
            raise ValueError("provider route id does not match route_id")
    return {"identity_status": status, **material}


def identity_from_payload(payload: Any) -> dict[str, Any]:
    return {field: getattr(payload, field, None) for field in IDENTITY_FIELDS}


def bind_task_provider_route_identity(
    task: Any,
    identity: Mapping[str, Any],
) -> None:
    if identity.get("identity_status") != IDENTITY_BOUND:
        return
    evidence = {
        "schema_version": 1,
        **{field: identity.get(field) for field in IDENTITY_FIELDS},
    }
    digest = canonical_sha256(evidence)
    if task.provider_route_evidence_sha256 is None:
        task.provider_route_evidence = evidence
        task.provider_route_evidence_sha256 = digest
        return
    if (
        not hmac.compare_digest(task.provider_route_evidence_sha256, digest)
        or task.provider_route_evidence != evidence
    ):
        raise ConflictError(
            "generation task is already bound to a different provider account route"
        )


_RELEASE_ROUTE_FIELDS = (
    "route_id",
    "channel_id",
    "provider_name",
    "provider_account_id",
    "provider_key_index",
    "provider_key_fingerprint_prefix",
    "provider_credential_set_version",
    "route_binding_sha256",
    "upstream_model",
    "adapter_profile_id",
    "adapter_profile_revision",
)


def commercial_route_release_snapshot(item: Any) -> tuple[dict[str, Any], str]:
    routes = [
        {field: getattr(route, field) for field in _RELEASE_ROUTE_FIELDS}
        for route in item.routes
    ]
    routes.sort(key=lambda row: (row["route_id"], row["channel_id"]))
    snapshot = {
        "schema_version": 1,
        "public_model_id": item.public_model_id,
        "capability_revision": item.capability_revision,
        "model_release_id": item.model_release_id,
        "model_release_revision": item.model_release_revision,
        "published_route_revision": item.published_route_revision,
        "routing_release_sha256": item.routing_release_sha256,
        "provider_cost_readiness_sha256": item.provider_cost_readiness_sha256,
        "routes": routes,
    }
    return snapshot, canonical_sha256(snapshot)


def commercial_route_publication_evidence(
    item: Any,
    *,
    route_release_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze the exact Relay route version used by a commercial release.

    ``route_release_evidence`` remains available as the detailed operational
    projection.  This compact value is suitable for the immutable commercial
    publication receipt: it carries the explicit Relay release identifiers,
    the complete secret-free route identity, and a digest of the accepted
    evidence document.  A later Relay key rotation or route replacement can
    therefore never be mistaken for the version that authorized pricing and
    distribution.
    """

    route_identity, route_identity_sha256 = commercial_route_release_snapshot(
        item
    )
    return {
        "schema_version": 1,
        "model_release_id": item.model_release_id,
        "model_release_revision": item.model_release_revision,
        "published_route_revision": item.published_route_revision,
        "routing_release_sha256": item.routing_release_sha256,
        "provider_cost_readiness_sha256": (
            item.provider_cost_readiness_sha256
        ),
        "route_identity": route_identity,
        "route_identity_sha256": route_identity_sha256,
        "route_release_evidence_sha256": canonical_sha256(
            route_release_evidence
        ),
    }
