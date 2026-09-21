"""Read-only commercial-form review data from the actual isolated Relay export.

This command never submits an approval, migration, grant, or task. Run after the
browser has synchronized the latest published Relay version into Platform.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import httpx

from platform_api.schemas import ModelCommercialReleasePlanRequest
from platform_api.services.commercial_pricing import CommercialPricingPolicy

ROOT = Path(__file__).resolve().parents[3]
DIRECTORY = ROOT / "artifacts/model-distribution-simulation-20260908"
BASE = "http://127.0.0.1:18220"


def main():
    bundle = json.loads((DIRECTORY / "relay/bundle.json").read_text(encoding="utf-8"))
    assert bundle["phase"] == "published", "Wait for the real Relay published phase"
    with httpx.Client(base_url=BASE, trust_env=False) as client:
        config = client.get("/__simulation__/config").json()
        assert config["isolated"] is True and config["paid_provider_calls"] is False
        client.headers["X-Platform-Admin-User-ID"] = config["identity"]["owner"]
        response = client.get("/api/v1/platform-admin/models")
        response.raise_for_status()
        model = next(item for item in response.json() if item["slug"] == "seedream-5")
    item = next(item for item in bundle["release_evidence"]["models"]
                if item["public_model_id"] == model["slug"])
    assert item["status"] == "ready" and item["provider_cost_ready"], "Real route/cost evidence is blocked"
    assert item["capability_revision"] == model["relay_capability_candidate_revision"]
    assert bundle["models"]["catalog_revision"] == model["relay_capability_candidate_catalog_revision"], \
        "Refresh/synchronize the new Relay catalog in the real browser first"
    rectangles = [rectangle for route in item["routes"] for rectangle in route["provider_cost_rectangles"]]
    assert rectangles and all(rectangle["ready"] and rectangle["billing_unit"] == "output_item"
                              and rectangle["currency"] == "CNY" for rectangle in rectangles)
    source_hash = hashlib.sha256((DIRECTORY / "relay/synthetic-cost-contract.txt").read_bytes()).hexdigest()
    assert all(rectangle["source_document_sha256"] == source_hash for rectangle in rectangles)
    fx_path = DIRECTORY / "platform/fx-cny-identity-simulation.json"
    fx_hash = hashlib.sha256(fx_path.read_bytes()).hexdigest()
    max_outputs = max(max(mode["limits"]["output_counts"])
                      for mode in model["relay_capability_candidate"]["modes"].values())
    rate_micros = max(rectangle["unit_amount_cents"] for rectangle in rectangles) * 10_000
    effective_at = max(rectangle["effective_from"] for rectangle in rectangles)
    source_effective = datetime.fromisoformat(effective_at.replace("Z", "+00:00"))
    # The real editor uses minute-resolution datetime-local fields. Do not make
    # the plan effective before its source; round the source instant upward.
    plan_effective = source_effective.replace(second=0, microsecond=0)
    if source_effective != plan_effective:
        plan_effective += timedelta(minutes=1)
    body = {
        "expected_capability_version": model["capability_version"],
        "expected_candidate_revision": model["relay_capability_candidate_revision"],
        "expected_catalog_revision": model["relay_capability_candidate_catalog_revision"],
        "expected_routing_release_sha256": item["routing_release_sha256"],
        "provider_cost_currency": "CNY",
        "provider_cost_formula": {
            "schema_version": 1, "kind": "output_item", "platform_billing_unit": "per_item",
            "source_capability_revision": model["relay_capability_candidate_revision"],
            "assumptions": {"quantity_basis": "relay_effective_capability_ceiling",
                            "personal_media_policy": "explicit_image_input_v1",
                            "enforced_limits": {"max_output_count": max_outputs}},
            "components": [{"component": "output_item", "rate_micros": rate_micros,
                            "quantity_numerator": 1, "quantity_denominator": 1}],
        },
        "provider_cost_evidence_kind": "contract_rate",
        "provider_cost_evidence_reference": "SIMULATION ONLY: relay/synthetic-cost-contract.txt",
        "provider_cost_evidence_sha256": source_hash,
        "provider_cost_effective_at": plan_effective.isoformat(),
        "fx_cny_micros_per_currency_unit": 1_000_000,
        "fx_source": "CNY identity rate (isolated simulation)",
        "fx_version": "simulation-cny-identity-20260908",
        "fx_evidence_sha256": fx_hash,
        "fx_effective_at": "2026-09-08T00:00:00Z",
        "personal_price_points": 4, "enterprise_price_points": 4,
        "personal_config_override": model["relay_capability_candidate"], "enterprise_config_override": {},
        "approval_reason": "仅独立模拟演练：明确批准个人和企业文生图、单张图片参考图生图，核对真实受管路由与合成成本证据",
        "idempotency_key": "simulation-commercial-review:" + model["id"],
    }
    validated = ModelCommercialReleasePlanRequest.model_validate(body)
    personal = CommercialPricingPolicy.project_personal_capabilities(
        capability_map={"generation": model["relay_capability_candidate"]},
        config_override=body["personal_config_override"], provider_cost_formula=body["provider_cost_formula"])
    CommercialPricingPolicy.validate_provider_cost_formula(
        body["provider_cost_formula"], billing_mode=model["billing_mode"],
        candidate_revision=model["relay_capability_candidate_revision"],
        effective_capabilities=(personal, model["relay_capability_candidate"]))
    review = {
        "simulation": True, "read_only_review": True, "submitted": False,
        "generated_at": datetime.now(timezone.utc).isoformat(), "model_id": model["id"],
        "relay_phase": bundle["phase"], "relay_exported_at": bundle["exported_at"],
        "request_path": "/api/v1/platform-admin/models/" + model["id"] + "/commercial-release-plan",
        "supplier_cost_cny": rate_micros / 1_000_000,
        "source_cost_effective_from": effective_at,
        "plan_effective_at_note": "Rounded up to the next whole minute for the actual browser editor",
        "expected_floor_points": (rate_micros * 10 + 699_999) // 700_000,
        "current_candidate_modes": sorted(model["relay_capability_candidate"]["modes"]),
        "body": validated.model_dump(mode="json", exclude_none=True),
    }
    output = DIRECTORY / "platform/commercial-form-review.json"
    output.write_text(json.dumps(review, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"output": str(output), "model_id": model["id"],
                      "submitted": False, "expected_floor_points": review["expected_floor_points"],
                      "source_sha256": source_hash, "fx_sha256": fx_hash}, ensure_ascii=False))


if __name__ == "__main__":
    main()
