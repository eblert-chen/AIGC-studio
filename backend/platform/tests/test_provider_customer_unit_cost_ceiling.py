from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from platform_api.models import ModelCommercialReleasePlan
from platform_api.relay_client import RelayModelReleaseEvidence, RelayProviderCostRectangleEvidence

from .test_commercial_task_admission import _counts, _task_fixture
from .test_model_commercial_release import _admin_headers, _commercial_body, _prepare_video_draft


def _rectangle():
    return {
        "mode": "text_to_video", "resolution": "720p", "ready": True,
        "rate_set_id": "00000000-0000-4000-8000-000000000123",
        "billing_unit": "per_second", "effective_from": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "source_document_sha256": "1" * 64, "cost_revision_sha256": "sha256:" + "2" * 64,
        "customer_unit_cost_ceiling_cny_micros": 2_880_000,
        "customer_unit_cost_ceiling_revision_sha256": "sha256:" + "3" * 64,
    }


@pytest.mark.parametrize("mutation", ["legacy_currency", "half_ceiling", "blocked_metadata", "boolean_amount", "zero_amount"])
def test_cost_ceiling_contract_rejects_ambiguous_proofs(mutation):
    raw = _rectangle()
    if mutation == "legacy_currency":
        raw["currency"] = "CNY"
    elif mutation == "half_ceiling":
        del raw["customer_unit_cost_ceiling_revision_sha256"]
    elif mutation == "blocked_metadata":
        raw.update(ready=False, blocker_code="provider_customer_unit_cost_ceiling_unqualified")
    elif mutation == "boolean_amount":
        raw["customer_unit_cost_ceiling_cny_micros"] = True
    else:
        raw["customer_unit_cost_ceiling_cny_micros"] = 0
    with pytest.raises(ValidationError):
        RelayProviderCostRectangleEvidence.model_validate(raw)


def test_cost_ceiling_contract_reads_ready_and_truthful_blocked_rows():
    assert RelayProviderCostRectangleEvidence.model_validate(_rectangle()).customer_unit_cost_ceiling_cny_micros == 2_880_000
    blocked = RelayProviderCostRectangleEvidence.model_validate({
        "mode": "text_to_video", "resolution": "720p", "ready": False,
        "blocker_code": "provider_customer_unit_cost_ceiling_unqualified",
    })
    assert blocked.ready is False and blocked.rate_set_id is None
    assert blocked.customer_unit_cost_ceiling_cny_micros is None


@pytest.mark.parametrize("high_selling_price", [False, True])
def test_rate_set_approval_cannot_understate_cost_even_with_same_document(app, client, high_selling_price):
    headers = _admin_headers(client, "ceiling-approval")
    model = _prepare_video_draft(app, client, headers, provider_cost_rate_set=True, provider_cost_unit_amount_cents=81)
    body = _commercial_body(model=model)  # Formula claims 80 cents, live ceiling is 81.
    if high_selling_price:
        body.update(personal_price_points=100, enterprise_price_points=100)
    response = client.put(f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan", headers=headers, json=body)
    assert response.status_code == 409, response.text
    assert "成本上限" in response.json()["detail"]
    with app.state.session_factory() as session:
        assert session.scalar(select(ModelCommercialReleasePlan.id)) is None


@pytest.mark.parametrize("scope", ["company", "personal"])
@pytest.mark.parametrize("failure", ["missing_ceiling", "understated_ceiling"])
def test_released_rate_set_requires_live_ceiling_before_wallet_or_outbox(app, client, tenant, scope, failure):
    _, model, headers, path, body, wallet_type, wallet_id = _task_fixture(app, client, tenant, scope)
    before = _counts(app, wallet_type, wallet_id)
    evidence = deepcopy(app.state.relay_client.evidence.model_dump())
    # Deliberately retain the old source and outer release identity in this
    # isolated transport fixture: the cost comparison must itself fail closed,
    # independently of the normal outer revision-drift guard.
    for rectangle in evidence["models"][0]["routes"][0]["provider_cost_rectangles"]:
        rectangle["rate_set_id"] = rectangle.pop("contract_rate_id")
        rectangle.pop("unit_amount_cents")
        rectangle.pop("currency")
        rectangle["billing_unit"] = "per_second"
        if failure == "understated_ceiling":
            rectangle["customer_unit_cost_ceiling_cny_micros"] = 800_001
            rectangle["customer_unit_cost_ceiling_revision_sha256"] = "sha256:" + "3" * 64
    app.state.relay_client.evidence = RelayModelReleaseEvidence.model_validate(evidence)

    listed = client.get(path.rsplit("/", 1)[0] + "/models", headers=headers)
    assert listed.status_code == 200, listed.text
    current = next(row for row in listed.json() if row["id"] == model["id"])
    assert set(current["mode_readiness"]) == {"text_to_video"}
    readiness = current["mode_readiness"]["text_to_video"]["default"]
    assert readiness["ready"] is False
    assert any(item["code"] == "commercial_release_provider_cost_plan_mismatch" for item in readiness["blockers"]), readiness
    rejected = client.post(path, headers=headers, json=body)
    assert rejected.status_code == 409, rejected.text
    assert _counts(app, wallet_type, wallet_id) == before == (1000, 0, 0, 0)
