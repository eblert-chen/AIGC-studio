"""Observe completed browser distribution and operator-controlled read faults.

Run solely after the operator has completed the simulation UI flow. This script
never approves, prices, publishes, migrates, grants, creates a task, or changes
the Relay phase/fault. Use --capture-fault while the operator holds that fault;
run without arguments after the operator restores the published live export.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
DIRECTORY = ROOT / "artifacts/model-distribution-simulation-20260908"
BASE = "http://127.0.0.1:18220"


def main(capture_fault=None):
    with httpx.Client(base_url=BASE, trust_env=False, timeout=30) as client:
        config = client.get("/__simulation__/config").json()
        assert config["isolated"] is True and config["paid_provider_calls"] is False
        identity = config["identity"]
        admin = {"X-Platform-Admin-User-ID": identity["owner"]}

        def read(path, headers=None):
            response = client.get(path, headers=headers)
            return {"path": path, "status": response.status_code,
                    "request_id": response.headers.get("x-request-id"), "body": response.json()}

        state = read("/__simulation__/state")["body"]
        assert state["relay"]["phase"] == "published" and state["fault"] == capture_fault
        models = read("/api/v1/platform-admin/models", admin)["body"]
        assert len(models) == 1, "The rehearsal must not create or authorize unrelated models"
        model = next(item for item in models if item["slug"] == "seedream-5")
        reviewed = json.loads((DIRECTORY / "platform/commercial-form-review.json").read_text(encoding="utf-8"))
        assert model["id"] == reviewed["model_id"]
        assert model["active"] is True and state["company_billing_version"] == 2, \
            "Wait for the browser to finish commercial release and company migration/grant"
        bundle = json.loads((DIRECTORY / "relay/bundle.json").read_text(encoding="utf-8"))
        relay = next(item for item in bundle["release_evidence"]["models"]
                     if item["public_model_id"] == model["slug"])
        assert model["relay_capability_revision"] == model["relay_capability_candidate_revision"] \
            == relay["capability_revision"] == reviewed["body"]["expected_candidate_revision"]
        assert model["capability_version"] == reviewed["body"]["expected_capability_version"]
        assert relay["routing_release_sha256"] == reviewed["body"]["expected_routing_release_sha256"]
        expected_modes = {"text_to_image", "image_to_image"}
        assert relay["status"] == "ready" and relay["provider_cost_ready"] is True
        assert len(relay["routes"]) == 1
        for route in relay["routes"]:
            assert set(route["required_test_modes"]) == set(route["fresh_test_modes"]) == expected_modes
            assert route["enabled"] is True and route["accepted"] is True and route["fresh"] is True

        def scopes(*, include_capabilities=True):
            result = {
                "personal": read("/api/v1/personal/models", {"X-User-ID": identity["personal"]}),
                "company": read(f"/api/v1/companies/{identity['company_id']}/models",
                                {"X-User-ID": identity["company"], "X-Company-ID": identity["company_id"]}),
            }
            if not include_capabilities:
                for response in result.values():
                    if response["status"] == 200:
                        response["body"] = [{key: row[key] for key in (
                            "id", "unit_price_points", "capability_version", "billing_unit", "billing_version",
                            "readiness_checked_at", "mode_readiness"
                        )} for row in response["body"]]
            return result

        def ready(result):
            assert result["status"] == 200
            assert isinstance(result["body"], list) and len(result["body"]) == 1
            row = result["body"][0]
            assert row["id"] == model["id"]
            assert row["unit_price_points"] == 4 and type(row["unit_price_points"]) is int
            assert row["capability_version"] == model["capability_version"]
            assert row["billing_unit"] == "POINT" and row["billing_version"] == 2
            assert set(row["mode_readiness"]) == expected_modes
            assert all(type(mode["default"]["ready"]) is bool for mode in row["mode_readiness"].values())
            if "effective_capabilities" in row:
                effective = row["effective_capabilities"]
                assert effective["schema_version"] == 3 and set(effective["modes"]) == expected_modes
                for mode, config in effective["modes"].items():
                    ceiling = model["relay_capability_candidate"]["modes"][mode]
                    for key in ("input_media_types", "input_roles", "temporal_controls", "structured_inputs",
                                "supports_face", "required_resource_keys", "limits"):
                        assert config[key] == ceiling[key]
            # Unsupported optional face processing is truthfully not ready and
            # must not be confused with readiness of the base generation mode.
            return all(mode["default"]["ready"] for mode in row["mode_readiness"].values())

        if capture_fault is not None:
            observed = scopes(include_capabilities=False)
            assert not any(ready(result) for result in observed.values()), \
                "A customer directory incorrectly claims ready under a Relay evidence fault"
            snapshot = {"simulation": True, "instance_id": config["instance_id"],
                        "model_id": model["id"], "fault": capture_fault,
                        "routing_release_sha256": relay["routing_release_sha256"],
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "state": state, "directories": observed}
            output = DIRECTORY / f"platform/fault-{capture_fault}.json"
            output.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
            print(json.dumps({"output": str(output), "read_only": True, "fault": capture_fault,
                              "both_directories_blocked": True}, ensure_ascii=False))
            return

        summary = {"simulation": True, "generated_at": datetime.now(timezone.utc).isoformat(),
                   "instance_id": config["instance_id"], "model_id": model["id"],
                   "public_model_id": model["slug"], "candidate_revision": relay["capability_revision"],
                   "routing_release_sha256": relay["routing_release_sha256"],
                   "published_route_revision": relay["published_route_revision"],
                   "provider_cost_readiness_sha256": relay["provider_cost_readiness_sha256"],
                   "route_evidence": relay,
                   "model": {key: model[key] for key in (
                       "id", "slug", "active", "published_at", "capability_version",
                       "relay_capability_revision", "relay_capability_candidate_revision",
                       "relay_capability_approval_status")}, "initial_state": state,
                   "commercial_releases": read("/api/v1/platform-admin/model-commercial-releases", admin),
                   "directories_ready": scopes(), "faults": {}}
        summary["commercial_releases"]["body"] = [{key: plan[key] for key in (
            "id", "revision", "model_id", "candidate_revision", "capability_version", "state",
            "provider_cost_evidence_sha256", "minimum_price_points", "personal_price_points",
            "enterprise_price_points", "approved_route_identity_sha256", "released_route_identity_sha256",
            "publication_receipt_sha256", "company_grant_count", "company_ids", "released_at",
        )} for plan in summary["commercial_releases"]["body"]]
        assert summary["commercial_releases"]["status"] == 200
        assert len(summary["commercial_releases"]["body"]) == 1
        plan = summary["commercial_releases"]["body"][0]
        assert plan["model_id"] == model["id"] and plan["state"] == "released"
        assert plan["candidate_revision"] == relay["capability_revision"]
        assert plan["capability_version"] == model["capability_version"]
        assert plan["personal_price_points"] == plan["enterprise_price_points"] == 4
        assert plan["provider_cost_evidence_sha256"] == reviewed["body"]["provider_cost_evidence_sha256"]
        assert all(ready(result) for result in summary["directories_ready"].values())
        assert summary["directories_ready"]["personal"]["body"][0]["effective_capabilities"] \
            == summary["directories_ready"]["company"]["body"][0]["effective_capabilities"]
        grants = {
            "personal": read("/api/v1/platform-admin/personal-model-grants", admin),
            "company": read(f"/api/v1/companies/{identity['company_id']}/model-grants",
                            {"X-User-ID": identity["company"], "X-Company-ID": identity["company_id"]}),
        }
        for scope, response in grants.items():
            assert response["status"] == 200 and len(response["body"]) == 1
            grant = response["body"][0]
            assert grant["model_id"] == model["id"] and grant["enabled"] is True
            assert grant["price_per_item_points"] == 4 and grant["price_per_second_points"] is None
            if scope == "personal":
                assert grant["capability_version"] == model["capability_version"]
                assert grant["relay_capability_revision"] == relay["capability_revision"]
            else:
                assert grant["company_id"] == identity["company_id"]
                assert grant["billing_unit"] == "POINT" and grant["billing_version"] == 2
        summary["enabled_grant_counts"] = {scope: 1 for scope in grants}
        summary["grants"] = {scope: {"path": response["path"], "request_id": response["request_id"],
            "status": response["status"], "body": [{key: value for key, value in response["body"][0].items()
            if key not in {"effective_capabilities", "config_override"}}]} for scope, response in grants.items()}
        for fault in ("expired", "unavailable"):
            snapshot = json.loads((DIRECTORY / f"platform/fault-{fault}.json").read_text(encoding="utf-8"))
            assert snapshot["instance_id"] == config["instance_id"] and snapshot["model_id"] == model["id"]
            assert snapshot["routing_release_sha256"] == relay["routing_release_sha256"]
            assert snapshot["fault"] == fault
            assert not any(ready(result) for result in snapshot["directories"].values())
            summary["faults"][fault] = snapshot
        denied = client.post("/api/v1/platform-admin/relay-models/reconcile",
                             headers={"X-Platform-Admin-User-ID": identity["non_owner"]})
        summary["non_owner_sync"] = {"status": denied.status_code,
                                     "request_id": denied.headers.get("x-request-id"), "body": denied.json()}
        assert denied.status_code == 403
        summary["directories_restored"] = scopes(include_capabilities=False)
        assert all(ready(result) for result in summary["directories_restored"].values())
        summary["final_state"] = read("/__simulation__/state")["body"]
        assert summary["final_state"]["counts"] == {
            "model_definitions": 1, "model_commercial_release_plans": 1,
            "company_model_grants": 1, "personal_retail_model_grants": 1,
            "generation_tasks": 0, "relay_submission_outbox": 0,
        }
        summary["passed"] = True
        summary["boundaries"] = {
            "paid_supplier_calls": False, "real_money_mutation": False, "production_data_mutation": False,
            "supplier_transport": "Relay Go test HTTPS provider and artifact transport",
            "cost_setup": "Explicit synthetic contract/rate configuration; not a Relay cost-entry UI",
            "configuration_writes": "Actual production Platform handlers driven by the browser",
            "faults": "Explicit disposable export-adapter read faults; production gate unchanged",
        }
        pricing_path = DIRECTORY / "platform/pricing-rejection.json"
        if pricing_path.exists():
            pricing = json.loads(pricing_path.read_text(encoding="utf-8"))
            assert pricing["passed"] is True and pricing["model_id"] == model["id"]
            current_grant = grants["company"]["body"][0]
            stored_grant = pricing["latest_grant"]["body"]
            assert current_grant["id"] == stored_grant["id"]
            assert current_grant["updated_at"] == stored_grant["updated_at"]
            assert current_grant["point_price_active_version_id"] == stored_grant["point_price_active_version_id"]
            summary["pricing_rejection"] = {
                "evidence_file": "pricing-rejection.json", "below_floor_points": 3,
                "minimum_price_points": 4, "before_grant_status": 409, "after_grant_status": 409,
                "request_ids": [item["request_id"] for item in pricing["rejections"]],
                "current_enabled": True, "current_points": 4, "grant_id": current_grant["id"],
                "grant_updated_at_unchanged": current_grant["updated_at"],
                "active_point_price_version_unchanged": current_grant["point_price_active_version_id"],
                "task_count": 0, "outbox_count": 0, "passed": True,
            }
        output = DIRECTORY / "platform/cross-service-summary.json"
        output.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"output": str(output), "passed": True, "model_id": model["id"],
                          "counts": summary["final_state"]["counts"], "non_owner_status": denied.status_code,
                          "fault_scenarios": list(summary["faults"])}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-fault", choices=("expired", "unavailable"))
    main(parser.parse_args().capture_fault)
