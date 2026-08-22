from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json

import httpx
import pytest

from platform_api.config import Settings
from platform_api.relay_client import (
    HttpxRelayOperationsClient,
    RelayPermanentError,
    RelayTemporaryError,
)

TENANT_ID = "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30"
JOB_ID = "58775bb2-b6d2-4ad3-ab03-2f9d10854ba1"
TOKEN = "sha256:" + "a" * 64
CAPABILITY_REVISION = "sha256:" + "b" * 64
OPERATION_ID = "reconcile-operation-0001"
EVENT_ID = "47aeecb9-d741-4e0e-959a-3be857a3e74b"
CALLBACK_EVENT_ID = "b9b2537e-258c-4a98-af8a-6d23bdb135a4"
CALLBACK_REDRIVE_EVENT_ID = "69d581b7-b098-58fd-9609-193da707f3ed"
CALLBACK_OPERATION_ID = "callback-redrive-operation-0001"
CHANNEL_ID = 17
CHANNEL_OPERATION_ID = "channel-operation-0001"
CHANNEL_REVISION = "sha256:" + "d" * 64
CHANNEL_RESULT_REVISION = "sha256:" + "e" * 64
OPERATIONS_TOKEN = "operations-secret-32-bytes-long-value"
APPROVAL_KEY_ID = "platform-approval-v1"
APPROVAL_SECRET = "platform-approval-secret-32-bytes-long-value"


def approval_signature(*, approval_reason: str) -> str:
    payload = bytearray(b"platform-generation-reconciliation-approval-v1\x00")
    for value in (
        TENANT_ID,
        JOB_ID,
        OPERATION_ID,
        "not_created",
        "",
        "19",
        "2",
        TOKEN,
        "provider-console-case-42",
        "platform-admin-1",
        approval_reason,
        APPROVAL_KEY_ID,
    ):
        encoded = value.encode("utf-8")
        payload.extend(str(len(encoded)).encode("ascii"))
        payload.extend(b":")
        payload.extend(encoded)
    return (
        "hmac-sha256:"
        + hmac.new(
            APPROVAL_SECRET.encode("utf-8"),
            bytes(payload),
            hashlib.sha256,
        ).hexdigest()
    )


def unknown_payload() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "api_version": "v1",
        "schema_version": 1,
        "object": "generation.reconciliation",
        "job_id": JOB_ID,
        "tenant_id": TENANT_ID,
        "client_reference_id": "platform-task-1",
        "model": "provider.video.v1",
        "mode": "text_to_video",
        "status": "reconciliation_required",
        "provider_route_id": 19,
        "provider_route_key": "provider-account-one",
        "provider_name": "provider",
        "provider_account_id": "account-one",
        "provider_channel_id": 7,
        "provider_key_index": 0,
        "provider_channel_class": "official",
        "provider_upstream_model": "video-v1",
        "provider_submission_attempt": 2,
        "unknown_at": now,
        "reconciliation_token": TOKEN,
        "error_code": "SUBMISSION_RECONCILIATION_REQUIRED",
        "error_message": "Provider response was lost",
        "created_at": now,
        "updated_at": now,
    }


def failed_snapshot_payload() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "api_version": "v1",
        "schema_version": 1,
        "object": "generation",
        "id": JOB_ID,
        "client_reference_id": "platform-task-1",
        "model": "provider.video.v1",
        "expected_capability_revision": CAPABILITY_REVISION,
        "capability_revision": CAPABILITY_REVISION,
        "mode": "text_to_video",
        "inputs": {"prompt": "test", "assets": []},
        "output": {
            "duration_seconds": 5,
            "aspect_ratio": "16:9",
            "resolution": "720p",
            "count": 1,
            "face_enabled": False,
        },
        "metadata": {},
        "status": "failed",
        "reservation_action": "release",
        "progress": 100,
        "outputs": [],
        "error": {
            "code": "SUBMISSION_CONFIRMED_NOT_CREATED",
            "message": "Provider confirmed no task was created",
            "retryable": False,
            "details": {},
        },
        "created_at": now,
        "updated_at": now,
    }


def reconciliation_result_payload() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "api_version": "v1",
        "schema_version": 1,
        "object": "generation.reconciliation_result",
        "event_id": EVENT_ID,
        "operation_id": OPERATION_ID,
        "request_id": "platform-resolve-request",
        "tenant_id": TENANT_ID,
        "job_id": JOB_ID,
        "outcome": "not_created",
        "upstream_task_id": "",
        "expected_route_id": 19,
        "expected_submission_attempt": 2,
        "expected_reconciliation_token": TOKEN,
        "verification_reference": "provider-console-case-42",
        "approved_by": "platform-admin-1",
        "approval_reason": "Provider console proves absence",
        "approval_key_id": APPROVAL_KEY_ID,
        "approval_signature": approval_signature(
            approval_reason="Provider console proves absence"
        ),
        "resolved_status": "failed",
        "current_status": "failed",
        "payload_sha256": "c" * 64,
        "resolved_at": now,
    }


def callback_delivery_payload(*, state: str = "dead_letter", redrives=None) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "api_version": "v1",
        "schema_version": 1,
        "object": "generation.callback_delivery",
        "event_id": CALLBACK_EVENT_ID,
        "tenant_id": TENANT_ID,
        "job_id": JOB_ID,
        "source_client_id": "platform-client",
        "original_request_id": "callback-original-request",
        "payload_sha256": "1" * 64,
        "callback_url_sha256": "2" * 64,
        "state": state,
        "attempts": 8,
        "max_attempts": 8,
        "available_at": now,
        "response_status": 503,
        "last_error": "callback_rejected",
        "delivered_at": None,
        "dead_lettered_at": now if state == "dead_letter" else None,
        "created_at": now,
        "updated_at": now,
        "redrives": redrives or [],
    }


def callback_redrive_result_payload() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "api_version": "v1",
        "schema_version": 1,
        "object": "generation.callback_redrive_result",
        "delivery_event_id": CALLBACK_EVENT_ID,
        "tenant_id": TENANT_ID,
        "current_state": "pending",
        "evidence": {
            "event_id": CALLBACK_REDRIVE_EVENT_ID,
            "operation_id": CALLBACK_OPERATION_ID,
            "request_id": "callback-redrive-request",
            "actor": "platform-admin-1",
            "reason": "Destination incident is resolved",
            "previous_state": "dead_letter",
            "previous_attempts": 8,
            "previous_max_attempts": 8,
            "previous_response_status": 503,
            "previous_last_error": "callback_rejected",
            "previous_dead_lettered_at": now,
            "callback_url_sha256": "2" * 64,
            "payload_sha256": "1" * 64,
            "original_callback_request_id": "callback-original-request",
            "result_state": "pending",
            "receipt_sha256": "3" * 64,
            "redriven_at": now,
        },
    }


def channel_payload() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": CHANNEL_ID,
        "name": "Official video primary",
        "type": 1,
        "type_label": "OpenAI",
        "test_supported": True,
        "status": "enabled",
        "configured_models": ["video-v1"],
        "test_model": "video-v1",
        "weight": 100,
        "priority": 10,
        "auto_ban": True,
        "tag": "official",
        "created_at": now,
        "last_tested_at": None,
        "response_time_ms": None,
        "credential": {"configured": True, "key_count": 2},
        "revision": CHANNEL_REVISION,
    }


def channel_operation_payload(*, kind: str, replay: bool = False) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    if kind == "test":
        result = {"success": True, "response_time_ms": 413, "error_code": None}
        previous_revision = None
        result_revision = None
        reason = "Verify the official channel before enabling traffic"
    else:
        result = {
            "previous_status": "enabled",
            "current_status": "manually_disabled",
            "changed": True,
        }
        previous_revision = CHANNEL_REVISION
        result_revision = CHANNEL_RESULT_REVISION
        reason = "Disable the channel while provider credentials are reviewed"
    return {
        "api_version": "v1",
        "schema_version": 1,
        "object": "relay.channel_control_operation",
        "operation_id": CHANNEL_OPERATION_ID,
        "tenant_id": TENANT_ID,
        "channel_id": CHANNEL_ID,
        "kind": kind,
        "state": "succeeded",
        "actor": "platform-admin-1",
        "reason": reason,
        "request_id": f"channel-{kind}-request",
        "intent_sha256": "f" * 64,
        "previous_revision": previous_revision,
        "result_revision": result_revision,
        **(
            {
                "expected_revision": CHANNEL_REVISION,
                "target_status": "manually_disabled",
            }
            if kind == "status"
            else {}
        ),
        "result": result,
        "created_at": now,
        "completed_at": now,
        "idempotent_replay": replay,
    }


def test_operations_client_discovers_details_and_sends_all_fencing_proof() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["x-relay-operations-token"] == OPERATIONS_TOKEN
        if request.method == "GET" and request.url.path.endswith("/submission-unknown"):
            assert request.url.params["tenant_id"] == TENANT_ID
            return httpx.Response(
                200,
                json={
                    "api_version": "v1",
                    "schema_version": 1,
                    "object": "list",
                    "data": [unknown_payload()],
                    "page": 1,
                    "page_size": 25,
                    "total": 1,
                },
            )
        if request.method == "GET" and request.url.path.endswith(
            "/reconciliation-result"
        ):
            assert request.url.params["tenant_id"] == TENANT_ID
            assert request.url.params["operation_id"] == OPERATION_ID
            assert request.headers["x-request-id"] == "platform-result-request"
            return httpx.Response(200, json=reconciliation_result_payload())
        if request.method == "GET":
            return httpx.Response(200, json=unknown_payload())
        body = json.loads(request.content)
        assert body == {
            "operation_id": OPERATION_ID,
            "tenant_id": TENANT_ID,
            "outcome": "not_created",
            "upstream_task_id": "",
            "expected_route_id": 19,
            "expected_submission_attempt": 2,
            "expected_reconciliation_token": TOKEN,
            "verification_reference": "provider-console-case-42",
            "approved_by": "platform-admin-1",
            "approval_reason": "Provider console proves absence",
            "approval_key_id": APPROVAL_KEY_ID,
            "approval_signature": approval_signature(
                approval_reason="Provider console proves absence"
            ),
        }
        assert request.headers["x-request-id"] == "platform-resolve-request"
        return httpx.Response(200, json=failed_snapshot_payload())

    client = HttpxRelayOperationsClient(
        base_url="https://relay.example.test",
        tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN,
        approval_key_id=APPROVAL_KEY_ID,
        approval_secret=APPROVAL_SECRET,
        transport=httpx.MockTransport(handler),
    )
    page = client.list_submission_unknown(page=1, page_size=25)
    detail = client.get_submission_unknown(JOB_ID)
    resolved = client.resolve_submission_unknown(
        JOB_ID,
        operation_id=OPERATION_ID,
        outcome="not_created",
        upstream_task_id="",
        expected_route_id=detail.provider_route_id,
        expected_submission_attempt=detail.provider_submission_attempt,
        expected_reconciliation_token=detail.reconciliation_token,
        verification_reference="provider-console-case-42",
        approved_by="platform-admin-1",
        approval_reason="Provider console proves absence",
        request_id="platform-resolve-request",
    )
    result = client.get_reconciliation_result(
        JOB_ID,
        operation_id=OPERATION_ID,
        request_id="platform-result-request",
    )

    assert page.total == 1
    assert detail.job_id == page.data[0].job_id
    assert resolved.status == "failed"
    assert result.event_id.hex == EVENT_ID.replace("-", "")
    assert result.operation_id == OPERATION_ID
    assert [request.method for request in seen] == ["GET", "GET", "POST", "GET"]


def test_operations_client_rejects_result_identity_drift() -> None:
    payload = reconciliation_result_payload()
    payload["tenant_id"] = "6b4f72d2-4d64-4ef9-adf2-7ead3e125b4f"

    client = HttpxRelayOperationsClient(
        base_url="https://relay.example.test",
        tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN,
        approval_key_id=APPROVAL_KEY_ID,
        approval_secret=APPROVAL_SECRET,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )

    with pytest.raises(RelayPermanentError, match="response is invalid"):
        client.get_reconciliation_result(JOB_ID, operation_id=OPERATION_ID)


def test_operations_client_lists_reads_redrives_and_reads_back_callback_dlq() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["x-relay-operations-token"] == OPERATIONS_TOKEN
        if request.method == "GET" and request.url.path.endswith("/callback-deliveries"):
            assert request.url.params["tenant_id"] == TENANT_ID
            assert request.url.params["state"] == "dead_letter"
            return httpx.Response(200, json={
                "api_version": "v1", "schema_version": 1, "object": "list",
                "data": [callback_delivery_payload()], "page": 1, "page_size": 25, "total": 1,
            })
        if request.method == "GET" and request.url.path.endswith("/redrive-result"):
            assert request.url.params["operation_id"] == CALLBACK_OPERATION_ID
            return httpx.Response(200, json=callback_redrive_result_payload())
        if request.method == "GET":
            return httpx.Response(200, json=callback_delivery_payload())
        assert json.loads(request.content) == {
            "operation_id": CALLBACK_OPERATION_ID,
            "tenant_id": TENANT_ID,
            "actor": "platform-admin-1",
            "reason": "Destination incident is resolved",
        }
        assert request.headers["x-request-id"] == "callback-redrive-request"
        return httpx.Response(200, json=callback_redrive_result_payload())

    client = HttpxRelayOperationsClient(
        base_url="https://relay.example.test", tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN, approval_key_id=APPROVAL_KEY_ID,
        approval_secret=APPROVAL_SECRET, transport=httpx.MockTransport(handler),
    )
    page = client.list_callback_dead_letters(page=1, page_size=25)
    detail = client.get_callback_dead_letter(CALLBACK_EVENT_ID)
    redriven = client.redrive_callback_dead_letter(
        CALLBACK_EVENT_ID, operation_id=CALLBACK_OPERATION_ID,
        actor="platform-admin-1", reason="Destination incident is resolved",
        request_id="callback-redrive-request",
    )
    result = client.get_callback_redrive_result(CALLBACK_EVENT_ID, operation_id=CALLBACK_OPERATION_ID)

    assert page.total == 1
    assert detail.state == "dead_letter"
    assert redriven.current_state == "pending"
    assert result.evidence.receipt_sha256 == "3" * 64
    assert [request.method for request in seen] == ["GET", "GET", "POST", "GET"]


def test_operations_client_uses_secret_free_channel_control_contract() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["x-relay-operations-token"] == OPERATIONS_TOKEN
        path = request.url.path
        if request.method == "GET" and path.endswith("/channels"):
            assert dict(request.url.params) == {
                "tenant_id": TENANT_ID,
                "page": "1",
                "page_size": "25",
                "status": "enabled",
            }
            return httpx.Response(
                200,
                json={
                    "api_version": "v1",
                    "schema_version": 1,
                    "object": "list",
                    "data": [channel_payload()],
                    "page": 1,
                    "page_size": 25,
                    "total": 1,
                },
            )
        if request.method == "GET" and "/operations/" in path:
            assert request.url.params["tenant_id"] == TENANT_ID
            return httpx.Response(
                200, json=channel_operation_payload(kind="status", replay=True)
            )
        if request.method == "GET":
            assert path.endswith(f"/channels/{CHANNEL_ID}")
            return httpx.Response(200, json=channel_payload())
        body = json.loads(request.content)
        assert request.headers["x-request-id"] in {
            "channel-test-request",
            "channel-status-request",
        }
        if path.endswith("/test"):
            assert body == {
                "operation_id": CHANNEL_OPERATION_ID,
                "tenant_id": TENANT_ID,
                "actor": "platform-admin-1",
                "reason": "Verify the official channel before enabling traffic",
            }
            return httpx.Response(200, json=channel_operation_payload(kind="test"))
        assert path.endswith("/status")
        assert body == {
            "operation_id": CHANNEL_OPERATION_ID,
            "tenant_id": TENANT_ID,
            "actor": "platform-admin-1",
            "reason": "Disable the channel while provider credentials are reviewed",
            "expected_revision": CHANNEL_REVISION,
            "target_status": "manually_disabled",
        }
        return httpx.Response(200, json=channel_operation_payload(kind="status"))

    client = HttpxRelayOperationsClient(
        base_url="https://relay.example.test",
        tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN,
        approval_key_id=APPROVAL_KEY_ID,
        approval_secret=APPROVAL_SECRET,
        transport=httpx.MockTransport(handler),
    )
    page = client.list_channels(page=1, page_size=25, status="enabled")
    detail = client.get_channel(CHANNEL_ID)
    tested = client.test_channel(
        CHANNEL_ID,
        operation_id=CHANNEL_OPERATION_ID,
        actor="platform-admin-1",
        reason="Verify the official channel before enabling traffic",
        request_id="channel-test-request",
    )
    changed = client.set_channel_status(
        CHANNEL_ID,
        operation_id=CHANNEL_OPERATION_ID,
        actor="platform-admin-1",
        reason="Disable the channel while provider credentials are reviewed",
        expected_revision=CHANNEL_REVISION,
        target_status="manually_disabled",
        request_id="channel-status-request",
    )
    receipt = client.get_channel_operation(
        CHANNEL_ID, operation_id=CHANNEL_OPERATION_ID
    )

    assert page.total == 1 and detail.id == CHANNEL_ID
    assert tested.kind == "test" and tested.result.success is True
    assert changed.kind == "status" and changed.result_revision == CHANNEL_RESULT_REVISION
    assert receipt.idempotent_replay is True
    assert [request.method for request in seen] == [
        "GET",
        "GET",
        "POST",
        "POST",
        "GET",
    ]


def test_channel_operations_client_fails_closed_on_secret_or_unknown_fields() -> None:
    exposed = channel_payload()
    exposed["key"] = "must-never-cross-the-boundary"
    list_payload = {
        "api_version": "v1",
        "schema_version": 1,
        "object": "list",
        "data": [exposed],
        "page": 1,
        "page_size": 50,
        "total": 1,
    }
    client = HttpxRelayOperationsClient(
        base_url="https://relay.example.test",
        tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN,
        approval_key_id=APPROVAL_KEY_ID,
        approval_secret=APPROVAL_SECRET,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=list_payload)
        ),
    )
    with pytest.raises(RelayPermanentError, match="channel list response is invalid"):
        client.list_channels()

    unsafe_receipt = channel_operation_payload(kind="test")
    unsafe_receipt["raw_error"] = "provider credential rejected: secret-value"
    client = HttpxRelayOperationsClient(
        base_url="https://relay.example.test",
        tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN,
        approval_key_id=APPROVAL_KEY_ID,
        approval_secret=APPROVAL_SECRET,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=unsafe_receipt)
        ),
    )
    with pytest.raises(RelayTemporaryError) as captured:
        client.test_channel(
            CHANNEL_ID,
            operation_id=CHANNEL_OPERATION_ID,
            actor="platform-admin-1",
            reason="Verify the official channel before enabling traffic",
        )
    assert captured.value.submission_outcome_unknown is True
    assert "secret-value" not in str(captured.value)


def test_channel_write_transport_and_server_failures_are_outcome_unknown() -> None:
    def network_failure(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("response lost")

    client = HttpxRelayOperationsClient(
        base_url="https://relay.example.test",
        tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN,
        approval_key_id=APPROVAL_KEY_ID,
        approval_secret=APPROVAL_SECRET,
        transport=httpx.MockTransport(network_failure),
    )
    with pytest.raises(RelayTemporaryError) as captured:
        client.test_channel(
            CHANNEL_ID,
            operation_id=CHANNEL_OPERATION_ID,
            actor="platform-admin-1",
            reason="Verify the official channel before enabling traffic",
        )
    assert captured.value.submission_outcome_unknown is True

    client = HttpxRelayOperationsClient(
        base_url="https://relay.example.test",
        tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN,
        approval_key_id=APPROVAL_KEY_ID,
        approval_secret=APPROVAL_SECRET,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(503, json={"detail": "unsafe upstream body"})
        ),
    )
    with pytest.raises(RelayTemporaryError) as captured:
        client.set_channel_status(
            CHANNEL_ID,
            operation_id=CHANNEL_OPERATION_ID,
            actor="platform-admin-1",
            reason="Disable the channel while provider credentials are reviewed",
            expected_revision=CHANNEL_REVISION,
            target_status="manually_disabled",
        )
    assert captured.value.submission_outcome_unknown is True


@pytest.mark.parametrize(
    ("drifted_field", "drifted_value"),
    (
        ("target_status", "enabled"),
        ("expected_revision", "sha256:" + "9" * 64),
    ),
)
def test_channel_status_client_rejects_wrong_intent_receipt(
    drifted_field: str, drifted_value: str
) -> None:
    malicious = channel_operation_payload(kind="status")
    malicious[drifted_field] = drifted_value
    if drifted_field == "target_status":
        malicious["result"] = {
            "previous_status": "enabled",
            "current_status": "enabled",
            "changed": False,
        }
    client = HttpxRelayOperationsClient(
        base_url="https://relay.example.test",
        tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN,
        approval_key_id=APPROVAL_KEY_ID,
        approval_secret=APPROVAL_SECRET,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=malicious)
        ),
    )
    with pytest.raises(RelayTemporaryError) as captured:
        client.set_channel_status(
            CHANNEL_ID,
            operation_id=CHANNEL_OPERATION_ID,
            actor="platform-admin-1",
            reason="Disable the channel while provider credentials are reviewed",
            expected_revision=CHANNEL_REVISION,
            target_status="manually_disabled",
        )
    assert captured.value.submission_outcome_unknown is True


def test_operations_configuration_is_explicit_and_tenant_scoped() -> None:
    with pytest.raises(ValueError, match="must be configured together"):
        Settings(relay_tenant_id=TENANT_ID)
    with pytest.raises(ValueError, match="canonical UUID"):
        Settings(
            relay_operations_base_url="https://relay.example.test",
            relay_tenant_id=TENANT_ID.upper(),
            relay_operations_token=OPERATIONS_TOKEN,
            relay_reconciliation_approval_key_id=APPROVAL_KEY_ID,
            relay_reconciliation_approval_secret=APPROVAL_SECRET,
        )

    settings = Settings(
        relay_operations_base_url="https://relay.example.test",
        relay_tenant_id=TENANT_ID,
        relay_operations_token=OPERATIONS_TOKEN,
        relay_reconciliation_approval_key_id=APPROVAL_KEY_ID,
        relay_reconciliation_approval_secret=APPROVAL_SECRET,
    )
    assert settings.relay_tenant_id == TENANT_ID


def test_empty_compose_operations_identity_is_treated_as_unconfigured() -> None:
    settings = Settings(
        relay_tenant_id="",
        relay_operations_token="",
        relay_reconciliation_approval_key_id="",
        relay_reconciliation_approval_secret="",
    )

    assert settings.relay_tenant_id is None
    assert settings.relay_operations_token is None
    assert settings.relay_reconciliation_approval_key_id is None
    assert settings.relay_reconciliation_approval_secret is None


def test_operations_configuration_does_not_fall_back_to_generation_url() -> None:
    with pytest.raises(ValueError, match="RELAY_OPERATIONS_BASE_URL is required"):
        Settings(
            relay_tenant_id=TENANT_ID,
            relay_operations_token=OPERATIONS_TOKEN,
            relay_reconciliation_approval_key_id=APPROVAL_KEY_ID,
            relay_reconciliation_approval_secret=APPROVAL_SECRET,
        )


def test_operations_base_url_cannot_be_configured_without_identity() -> None:
    with pytest.raises(ValueError, match="requires RELAY_TENANT_ID"):
        Settings(relay_operations_base_url="https://relay.example.test")


def test_operations_and_approval_secrets_must_be_independent() -> None:
    with pytest.raises(ValueError, match="must be distinct"):
        Settings(
            relay_operations_base_url="https://relay.example.test",
            relay_tenant_id=TENANT_ID,
            relay_operations_token=OPERATIONS_TOKEN,
            relay_reconciliation_approval_key_id=APPROVAL_KEY_ID,
            relay_reconciliation_approval_secret=OPERATIONS_TOKEN,
        )
