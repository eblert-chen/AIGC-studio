from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import get_args

from platform_api.relay_client import RelayAsyncErrorCode
from relay_service.main import create_app as create_relay_app
from relay_service.models import (
    JobStatus,
    PublicAsyncErrorCode,
    reservation_action_for,
)


ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = ROOT / "contracts" / "relay-generation-v1.openapi.yaml"
CALLBACK_SCHEMA_PATH = ROOT / "contracts" / "callback-event-v1.schema.json"
ERROR_CODES_PATH = ROOT / "contracts" / "error-codes-v1.json"
POLLING_EXAMPLE = ROOT / "examples" / "internal-tiktok" / "polling.py"
CALLBACK_EXAMPLE = (
    ROOT / "examples" / "internal-tiktok" / "callback_receiver.py"
)
CONTRACT_DOC = ROOT / "docs" / "generation-api-v1.md"
FREEZE_CHECKLIST = ROOT / "docs" / "generation-api-v1-freeze-checklist.md"


STATUS_ACTIONS = {
    "queued": "hold",
    "submitting": "hold",
    "reconciliation_required": "hold",
    "processing": "hold",
    "transferring": "hold",
    "succeeded": "settle",
    "failed": "release",
    "cancelled": "release",
}


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_openapi_is_parseable_versioned_and_service_authenticated() -> None:
    contract = load_json(OPENAPI_PATH)

    assert contract["openapi"] == "3.1.0"
    assert contract["info"]["version"] == "1.0.0"
    assert contract["x-contract-id"] == "relay-generation-v1"
    assert contract["security"] == [{"ClientId": [], "ApiKey": []}]
    schemes = contract["components"]["securitySchemes"]
    assert schemes["ClientId"] == {
        "type": "apiKey",
        "in": "header",
        "name": "X-Client-ID",
        "description": (
            "Use internal-tiktok for the internal TikTok integration. "
            "It must have its own tenant and key."
        ),
    }
    assert schemes["ApiKey"]["name"] == "X-API-Key"
    assert "operations:submission-reconciliation" in contract[
        "x-operations-scope"
    ]

    paths = contract["paths"]
    assert {
        "/v1/models",
        "/v1/models/{model_id}",
        "/v1/generations",
        "/v1/generations/{job_id}",
        "/v1/generations/{job_id}/artifacts/{asset_id}/download",
    }.issubset(paths)
    post = paths["/v1/generations"]["post"]
    assert post["responses"]["202"]["content"]["application/json"][
        "schema"
    ]["$ref"].endswith("/GenerationAccepted")
    assert any(
        parameter.get("$ref", "").endswith("/IdempotencyKey")
        for parameter in post["parameters"]
    )


def test_openapi_freezes_revision_versions_and_job_invariants() -> None:
    contract = load_json(OPENAPI_PATH)
    schemas = contract["components"]["schemas"]

    request = schemas["GenerationRequest"]
    assert request["additionalProperties"] is False
    assert "expected_capability_revision" in request["required"]
    assert request["properties"]["expected_capability_revision"]["$ref"].endswith(
        "/Revision"
    )

    for schema_name in (
        "ModelList",
        "ModelResource",
        "GenerationAccepted",
        "GenerationJob",
        "SignedDownload",
        "ErrorEnvelope",
    ):
        schema = schemas[schema_name]
        assert schema["additionalProperties"] is False
        assert {"api_version", "schema_version"}.issubset(schema["required"])
        assert schema["properties"]["schema_version"] == {
            "type": "integer",
            "const": 1,
        }

    for schema_name in ("GenerationAccepted", "GenerationJob"):
        required = set(schemas[schema_name]["required"])
        assert {
            "expected_capability_revision",
            "capability_revision",
            "reservation_action",
        }.issubset(required)

    assert contract["x-reservation-action-by-status"] == STATUS_ACTIONS
    accepted_refs = {
        item["$ref"] for item in schemas["GenerationAccepted"]["allOf"]
    }
    job_refs = {item["$ref"] for item in schemas["GenerationJob"]["allOf"]}
    assert accepted_refs == {"#/components/schemas/ReservationInvariant"}
    assert job_refs == {
        "#/components/schemas/ReservationInvariant",
        "#/components/schemas/GenerationTerminalInvariant",
    }

    terminal = json.dumps(schemas["GenerationTerminalInvariant"], sort_keys=True)
    assert '"const": 100' in terminal
    assert '"minItems": 1' in terminal
    assert '"maxItems": 0' in terminal
    assert '"$ref": "#/components/schemas/ErrorDetail"' in terminal


def test_callback_schema_is_strict_versioned_and_action_safe() -> None:
    schema = load_json(CALLBACK_SCHEMA_PATH)

    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "api_version",
        "schema_version",
        "event_id",
        "type",
        "occurred_at",
        "job",
    }
    assert schema["properties"]["api_version"]["const"] == "v1"
    assert schema["properties"]["schema_version"]["const"] == 1

    job = schema["$defs"]["callbackJob"]
    assert job["additionalProperties"] is False
    assert {
        "api_version",
        "expected_capability_revision",
        "capability_revision",
        "reservation_action",
    }.issubset(job["required"])
    refs = {item["$ref"] for item in job["allOf"]}
    assert refs == {
        "#/$defs/reservationInvariant",
        "#/$defs/terminalInvariant",
    }
    terminal = json.dumps(schema["$defs"]["terminalInvariant"], sort_keys=True)
    assert '"const": 100' in terminal
    assert '"minItems": 1' in terminal
    assert '"maxItems": 0' in terminal
    assert '"$ref": "#/$defs/error"' in terminal


def test_error_registry_is_complete_unambiguous_and_matches_status_actions() -> None:
    registry = load_json(ERROR_CODES_PATH)

    assert registry["api_version"] == "v1"
    assert registry["schema_version"] == 1
    assert registry["reservation_policy"] == STATUS_ACTIONS
    entries = registry["errors"]
    codes = [entry["code"] for entry in entries]
    assert len(codes) == len(set(codes))
    assert {
        "CLIENT_AUTHENTICATION_REQUIRED",
        "INVALID_CLIENT_CREDENTIALS",
        "INSUFFICIENT_CLIENT_SCOPE",
        "REQUEST_VALIDATION_FAILED",
        "IDEMPOTENCY_KEY_REUSED",
        "CALLBACK_NOT_CONFIGURED",
        "CALLBACK_URL_NOT_ALLOWED",
        "JOB_NOT_FOUND",
        "MODEL_NOT_FOUND",
        "ARTIFACT_NOT_FOUND",
        "ROUTE_NOT_FOUND",
        "METHOD_NOT_ALLOWED",
        "INTERNAL_ERROR",
        "CAPABILITY_REVISION_MISMATCH",
        "REQUEST_NOT_SUPPORTED_BY_MODEL",
        "NO_PROVIDER_AVAILABLE",
        "SUBMISSION_RECONCILIATION_REQUIRED",
        "PROVIDER_POLL_RECONCILIATION_REQUIRED",
        "SUBMISSION_CONFIRMED_NOT_CREATED",
        "PROVIDER_RETRIES_EXHAUSTED",
        "WORKER_ATTEMPTS_EXHAUSTED",
        "ARTIFACT_TRANSFER_RETRYING",
        "ARTIFACT_TRANSFER_FAILED",
    }.issubset(codes)

    for entry in entries:
        assert {"code", "surfaces", "stage", "http_status", "retry", "caller_action"}.issubset(
            entry
        )
        assert isinstance(entry["retry"]["allowed"], bool)
        assert isinstance(entry["retry"]["strategy"], str)
        assert entry["caller_action"]
        if entry["surfaces"] == ["http"]:
            assert entry["create_reservation_action"] in {
                "hold",
                "release",
            }
            assert entry["existing_job_action"] == "unchanged"
            assert "reservation_action" not in entry
        else:
            assert entry["reservation_action"] in {
                "hold",
                "release",
            }

    by_code = {entry["code"]: entry for entry in entries}
    async_codes = {
        entry["code"]
        for entry in entries
        if {"job", "callback"}.intersection(entry["surfaces"])
    }
    openapi_codes = set(
        load_json(OPENAPI_PATH)["components"]["schemas"][
            "PublicAsyncErrorCode"
        ]["enum"]
    )
    callback_codes = set(
        load_json(CALLBACK_SCHEMA_PATH)["$defs"]["publicAsyncErrorCode"][
            "enum"
        ]
    )
    runtime_codes = {code.value for code in PublicAsyncErrorCode}
    platform_codes = set(get_args(RelayAsyncErrorCode))
    assert (
        async_codes
        == openapi_codes
        == callback_codes
        == runtime_codes
        == platform_codes
    )
    assert by_code["INSUFFICIENT_CLIENT_SCOPE"]["http_status"] == 403
    assert by_code["INSUFFICIENT_CLIENT_SCOPE"]["reservation_action"] == "hold"
    assert by_code["SUBMISSION_RECONCILIATION_REQUIRED"][
        "reservation_action"
    ] == "hold"
    assert by_code["SUBMISSION_CONFIRMED_NOT_CREATED"][
        "reservation_action"
    ] == "release"
    assert by_code["ARTIFACT_TRANSFER_RETRYING"]["reservation_action"] == "hold"
    assert by_code["ARTIFACT_TRANSFER_FAILED"]["reservation_action"] == "release"


def test_frozen_openapi_matches_runtime_version_auth_and_wallet_semantics() -> None:
    frozen = load_json(OPENAPI_PATH)
    runtime = create_relay_app().openapi()

    assert runtime["info"]["version"] == frozen["info"]["version"]
    runtime_security = runtime["paths"]["/v1/generations"]["post"]["security"]
    assert len(runtime_security) == 1
    runtime_schemes = runtime["components"]["securitySchemes"]
    required_headers = {
        runtime_schemes[name]["name"] for name in runtime_security[0]
    }
    assert required_headers == {"X-Client-ID", "X-API-Key"}

    runtime_schemas = runtime["components"]["schemas"]
    assert "expected_capability_revision" in runtime_schemas[
        "GenerationRequest"
    ]["required"]
    for name in (
        "GenerationAccepted",
        "GenerationResponse",
        "ModelListResponse",
        "ModelResource",
        "SignedDownload",
        "ErrorEnvelope",
    ):
        assert {"api_version", "schema_version"}.issubset(
            runtime_schemas[name]["required"]
        )
        assert runtime_schemas[name]["additionalProperties"] is False

    runtime_actions = {
        status.value: reservation_action_for(status).value for status in JobStatus
    }
    assert runtime_actions == frozen["x-reservation-action-by-status"]


def _constant_secret_assignments(source: str) -> list[tuple[str, str]]:
    tree = ast.parse(source)
    suspicious: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name) and any(
                marker in target.id.casefold()
                for marker in ("secret", "api_key", "signing_key")
            ):
                suspicious.append((target.id, value.value))
    return suspicious


def test_internal_tiktok_examples_compile_use_environment_secrets_and_safe_flows() -> None:
    polling = POLLING_EXAMPLE.read_text(encoding="utf-8")
    receiver = CALLBACK_EXAMPLE.read_text(encoding="utf-8")

    compile(polling, str(POLLING_EXAMPLE), "exec")
    compile(receiver, str(CALLBACK_EXAMPLE), "exec")
    assert _constant_secret_assignments(polling) == []
    assert _constant_secret_assignments(receiver) == []
    lowered = (polling + receiver).casefold()
    for banned in ("change-me", "development-api-key", "local-customer-platform"):
        assert banned not in lowered

    assert "INTERNAL_TIKTOK_RELAY_API_KEY" in polling
    assert '"If-None-Match"' in polling
    assert '"Idempotency-Key"' in polling
    assert "data=body" in polling
    assert "expected_capability_revision" in polling
    assert "assert_reservation_action" in polling
    assert "download_and_verify" in polling

    assert "INTERNAL_TIKTOK_RELAY_CALLBACK_SECRET" in receiver
    assert "hmac.compare_digest" in receiver
    assert 'f"{timestamp}.{event_id}."' in receiver
    assert "reject_duplicate_keys" in receiver
    assert "body_sha256" in receiver
    assert "BEGIN IMMEDIATE" in receiver


def test_human_contract_and_freeze_checklist_match_fail_closed_policy() -> None:
    contract_doc = CONTRACT_DOC.read_text(encoding="utf-8")
    checklist = FREEZE_CHECKLIST.read_text(encoding="utf-8")

    assert "additionalProperties=false" in contract_doc
    assert "operations:submission-reconciliation" in contract_doc
    assert "Accepted 只反映当前状态，不能单独作为结算证据" in contract_doc
    assert "v1 可以新增可选字段" not in contract_doc
    assert "调用方必须忽略未识别的响应字段" not in contract_doc
    assert "TikTok 运营系统负责人" in checklist
    assert "RPM" in checklist
    assert "SHA-256" in checklist
    assert "operations:submission-reconciliation" in checklist
