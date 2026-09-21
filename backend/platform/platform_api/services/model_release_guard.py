from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from ..models import ModelDefinition
from .errors import ConflictError
from .provider_route_identity import canonical_sha256


_MODEL_RELEASE_FIELDS = (
    "model_id",
    "public_model_id",
    "capability_version",
    "candidate_revision",
    "candidate_catalog_revision",
    "candidate_capability",
    "approved_revision",
    "approved_catalog_revision",
    "approved_ceiling",
)

_ROUTE_EVIDENCE_IDENTITY_FIELDS = (
    "public_model_id",
    "capability_revision",
    "model_release_id",
    "model_release_revision",
    "published_route_revision",
    "routing_release_sha256",
    "provider_cost_readiness_sha256",
    "route_inventory_sha256",
)


@dataclass(frozen=True)
class ModelReleaseReadiness:
    """Relay evidence plus the exact Platform row state it authorized.

    The Relay projection cannot share a database transaction with Platform.
    This value therefore binds the already validated, secret-free route-release
    identity to an immutable snapshot of the Platform model row.  Every write
    path must compare that snapshot again after acquiring the model row lock.
    """

    evidence: dict[str, Any]
    expected_snapshot: dict[str, Any]


def _platform_snapshot(model: ModelDefinition) -> dict[str, Any]:
    return {
        "model_id": model.id,
        "public_model_id": model.slug,
        "capability_version": model.capability_version,
        "candidate_revision": model.relay_capability_candidate_revision,
        "candidate_catalog_revision": (
            model.relay_capability_candidate_catalog_revision
        ),
        "candidate_capability": deepcopy(model.relay_capability_candidate),
        "approved_revision": model.relay_capability_revision,
        "approved_catalog_revision": (
            model.relay_capability_approved_catalog_revision
        ),
        "approved_ceiling": deepcopy(model.relay_capability_approved_ceiling),
    }


def build_model_release_readiness(
    *,
    model: ModelDefinition,
    evidence: Mapping[str, Any],
) -> ModelReleaseReadiness:
    evidence_payload = deepcopy(dict(evidence))
    route_identity = {
        field: deepcopy(evidence_payload.get(field))
        for field in _ROUTE_EVIDENCE_IDENTITY_FIELDS
        if field != "route_inventory_sha256"
    }
    routes = deepcopy(evidence_payload.get("routes"))
    if not isinstance(routes, list):
        routes = []
    routes.sort(
        key=lambda route: (
            str(route.get("route_id", "")) if isinstance(route, Mapping) else "",
            int(route.get("channel_id", 0)) if isinstance(route, Mapping) else 0,
        )
    )
    route_identity["route_inventory_sha256"] = canonical_sha256(
        {"schema_version": 1, "routes": routes}
    )
    if (
        route_identity["public_model_id"] != model.slug
        or route_identity["capability_revision"]
        != model.relay_capability_revision
        or not route_identity["model_release_id"]
        or not route_identity["model_release_revision"]
        or not route_identity["published_route_revision"]
        or not route_identity["routing_release_sha256"]
        or not route_identity["provider_cost_readiness_sha256"]
        or not routes
        or evidence_payload.get("provider_cost_ready") is not True
        or not isinstance(
            evidence_payload.get("provider_cost_rectangle_count"), int
        )
        or evidence_payload.get("provider_cost_rectangle_count", 0) < 1
        or evidence_payload.get("provider_cost_ready_rectangle_count")
        != evidence_payload.get("provider_cost_rectangle_count")
    ):
        raise ConflictError(
            "Relay 路由发布或供应商成本证据与模型批准版本不一致"
        )
    return ModelReleaseReadiness(
        evidence=evidence_payload,
        expected_snapshot={
            **_platform_snapshot(model),
            "route_evidence_identity": route_identity,
            "route_release_evidence": evidence_payload,
        },
    )


def require_model_release_snapshot(
    *,
    model: ModelDefinition,
    expected_snapshot: Mapping[str, Any] | None,
) -> None:
    """Reject a release/distribution write if the locked model has drifted."""

    if expected_snapshot is None:
        return
    if any(field not in expected_snapshot for field in _MODEL_RELEASE_FIELDS):
        raise ConflictError("模型发布证据快照不完整，请刷新后重试")
    current = _platform_snapshot(model)
    if any(
        current[field] != expected_snapshot.get(field)
        for field in _MODEL_RELEASE_FIELDS
    ):
        raise ConflictError("模型发布证据已变化，请刷新后重试")

    route_identity = expected_snapshot.get("route_evidence_identity")
    if not isinstance(route_identity, Mapping) or any(
        field not in route_identity for field in _ROUTE_EVIDENCE_IDENTITY_FIELDS
    ):
        raise ConflictError("模型发布证据快照不完整，请刷新后重试")
    if (
        route_identity.get("public_model_id") != model.slug
        or route_identity.get("capability_revision")
        != model.relay_capability_revision
        or not route_identity.get("model_release_id")
        or not route_identity.get("model_release_revision")
        or not route_identity.get("published_route_revision")
        or not route_identity.get("routing_release_sha256")
        or not route_identity.get("provider_cost_readiness_sha256")
        or not route_identity.get("route_inventory_sha256")
    ):
        raise ConflictError("模型发布证据已变化，请刷新后重试")
