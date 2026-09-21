from __future__ import annotations

from types import SimpleNamespace

import pytest

from platform_api.services.errors import ConflictError
from platform_api.services.model_release_guard import build_model_release_readiness
from platform_api.services.relay_capabilities import RelayCapabilityService


def _rectangle(*, ready: bool, blocker_code: str = "") -> SimpleNamespace:
    return SimpleNamespace(ready=ready, blocker_code=blocker_code)


def _item(
    *,
    ready: bool,
    required_count: int,
    ready_count: int,
    rectangles: list[SimpleNamespace],
) -> SimpleNamespace:
    return SimpleNamespace(
        provider_cost_ready=ready,
        provider_cost_rectangle_count=required_count,
        provider_cost_ready_rectangle_count=ready_count,
        routes=[SimpleNamespace(provider_cost_rectangles=rectangles)],
    )


def test_provider_cost_gate_requires_complete_exact_rectangle_coverage() -> None:
    state = RelayCapabilityService.provider_cost_evidence_state(
        _item(
            ready=True,
            required_count=2,
            ready_count=2,
            rectangles=[_rectangle(ready=True), _rectangle(ready=True)],
        )
    )

    assert state == {
        "provider_cost_status": "ready",
        "provider_cost_blocker_codes": [],
        "provider_cost_rectangle_count": 2,
        "provider_cost_ready_rectangle_count": 2,
    }


def test_provider_cost_gate_explains_missing_rate_and_unqualified_omni() -> None:
    state = RelayCapabilityService.provider_cost_evidence_state(
        _item(
            ready=False,
            required_count=2,
            ready_count=0,
            rectangles=[
                _rectangle(
                    ready=False,
                    blocker_code="provider_contract_rate_missing",
                ),
                _rectangle(
                    ready=False,
                    blocker_code=(
                        "provider_usage_cost_materialization_unqualified"
                    ),
                ),
            ],
        )
    )

    assert state["provider_cost_status"] == "blocked"
    assert state["provider_cost_blocker_codes"] == [
        "provider_contract_rate_missing",
        "provider_usage_cost_materialization_unqualified",
    ]
    assert any("合同费率" in message for message in state["provider_cost_blockers"])
    assert any("多组件" in message for message in state["provider_cost_blockers"])


def test_legacy_route_evidence_without_cost_projection_fails_closed() -> None:
    state = RelayCapabilityService.provider_cost_evidence_state(
        SimpleNamespace(routes=[])
    )

    assert state["provider_cost_status"] == "blocked"
    assert state["provider_cost_rectangle_count"] == 0
    assert state["provider_cost_ready_rectangle_count"] == 0
    assert state["provider_cost_blockers"] == [
        "Relay 尚未提供精确到路由、模式和清晰度的成本覆盖证据"
    ]


def _model_row() -> SimpleNamespace:
    return SimpleNamespace(
        id="model-id",
        slug="provider-model",
        capability_version=7,
        relay_capability_candidate_revision="sha256:" + "1" * 64,
        relay_capability_candidate_catalog_revision="sha256:" + "2" * 64,
        relay_capability_candidate={"generation": "candidate"},
        relay_capability_revision="sha256:" + "1" * 64,
        relay_capability_approved_catalog_revision="sha256:" + "2" * 64,
        relay_capability_approved_ceiling={"generation": "candidate"},
    )


def _release_evidence(*, cost_ready: bool) -> dict:
    return {
        "public_model_id": "provider-model",
        "capability_revision": "sha256:" + "1" * 64,
        "model_release_id": "release-provider-model",
        "model_release_revision": "sha256:" + "3" * 64,
        "published_route_revision": "sha256:" + "6" * 64,
        "routing_release_sha256": "sha256:" + "4" * 64,
        "provider_cost_readiness_sha256": "sha256:" + "5" * 64,
        "provider_cost_ready": cost_ready,
        "provider_cost_rectangle_count": 2,
        "provider_cost_ready_rectangle_count": 2 if cost_ready else 1,
        "routes": [{"route_id": "route-provider-model", "channel_id": 71}],
    }


def test_release_snapshot_cannot_bypass_provider_cost_gate() -> None:
    with pytest.raises(ConflictError, match="供应商成本证据"):
        build_model_release_readiness(
            model=_model_row(),
            evidence=_release_evidence(cost_ready=False),
        )

    readiness = build_model_release_readiness(
        model=_model_row(),
        evidence=_release_evidence(cost_ready=True),
    )
    assert readiness.expected_snapshot["route_evidence_identity"][
        "provider_cost_readiness_sha256"
    ] == ("sha256:" + "5" * 64)
