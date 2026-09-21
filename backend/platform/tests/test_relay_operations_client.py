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
    RelayChannelOperation,
    RelayChannelTestResult,
    RelayPermanentError,
    RelaySynchronousResultEvidence,
    RelayTemporaryError,
    RelayUnknownSubmissionResult,
    validate_relay_channel_reconciliation_reason,
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
SYNCHRONOUS_JOB_ID = "6b4f72d2-4d64-4ef9-adf2-7ead3e125b4f"
SYNCHRONOUS_OPERATION_ID = "reconcile-seedream-created-0001"
SYNCHRONOUS_RESPONSE_SHA256 = "ab" * 32
SYNCHRONOUS_ARTIFACT_URL = (
    "https://provider.example.test/results/image.png?token=private"
)


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


@pytest.mark.parametrize(
    ("field", "exact_value", "overflow_value"),
    (
        ("verification_reference", "核" * 63 + "ab", "核" * 63 + "abc"),
        ("approved_by", "审" * 42 + "ab", "审" * 42 + "abc"),
        ("approval_reason", "因" * 80, "因" * 80 + "a"),
    ),
)
def test_reconciliation_result_uses_go_utf8_byte_limits(
    field: str,
    exact_value: str,
    overflow_value: str,
) -> None:
    exact_payload = reconciliation_result_payload()
    exact_payload[field] = exact_value
    result = RelayUnknownSubmissionResult.model_validate(exact_payload)
    assert getattr(result, field) == exact_value

    overflow_payload = reconciliation_result_payload()
    overflow_payload[field] = overflow_value
    with pytest.raises(ValueError, match=field):
        RelayUnknownSubmissionResult.model_validate(overflow_payload)


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
        **(
            {"provider_submission_state": "artifact_verified"}
            if kind == "test"
            else {}
        ),
        "created_at": now,
        "completed_at": now,
        "idempotent_replay": replay,
    }


def channel_reconciled_no_creation_payload() -> dict:
    completed_at = datetime.now(timezone.utc).isoformat()
    payload = channel_operation_payload(kind="test")
    payload.update(
        {
            "state": "failed",
            "result": {
                "success": False,
                "response_time_ms": 0,
                "error_code": "CHANNEL_TEST_RECONCILED_NO_CREATION",
            },
            "provider_submission_state": "reconciled_no_creation",
            "reconciliation_actor": "platform-owner-1",
            "reconciliation_reason": (
                "Provider console confirms that no provider task was created"
            ),
            "reconciled_at": completed_at,
            "created_at": completed_at,
            "completed_at": completed_at,
        }
    )
    return payload


def route_test_receipt_payload(*, state: str, submission_state: str) -> dict:
    payload = channel_operation_payload(kind="test")
    payload["state"] = state
    payload["provider_submission_state"] = submission_state
    if state == "pending":
        payload["result"] = None
        payload["completed_at"] = None
    elif state == "failed":
        payload["result"] = {
            "success": False,
            "response_time_ms": 12,
            "error_code": "CHANNEL_TEST_FAILED",
        }
    return payload


@pytest.mark.parametrize(
    ("state", "submission_state"),
    (
        ("pending", "not_started"),
        ("pending", "submission_unknown"),
        ("pending", "submitted"),
        ("succeeded", "artifact_verified"),
        ("failed", "pre_submit_failed"),
        ("failed", "provider_terminal"),
        ("failed", "provider_rejected"),
    ),
)
def test_route_test_receipt_accepts_only_matching_submission_state(
    state: str, submission_state: str
) -> None:
    receipt = RelayChannelOperation.model_validate(
        route_test_receipt_payload(
            state=state,
            submission_state=submission_state,
        )
    )
    assert receipt.state == state
    assert receipt.provider_submission_state == submission_state


@pytest.mark.parametrize(
    ("state", "submission_state"),
    (
        ("pending", "artifact_verified"),
        ("pending", "provider_terminal"),
        ("succeeded", "submitted"),
        ("succeeded", "provider_rejected"),
        ("failed", "submitted"),
        ("failed", "artifact_verified"),
    ),
)
def test_route_test_receipt_rejects_contradictory_submission_state(
    state: str, submission_state: str
) -> None:
    with pytest.raises(ValueError, match="submission state"):
        RelayChannelOperation.model_validate(
            route_test_receipt_payload(
                state=state,
                submission_state=submission_state,
            )
        )


@pytest.mark.parametrize(
    "blocker_code",
    (
        "CHANNEL_TEST_FAILED",
        "CHANNEL_TEST_UNAVAILABLE",
        "CHANNEL_TEST_PROVIDER_VALIDATION",
        "CHANNEL_TEST_PROVIDER_AUTH",
        "CHANNEL_TEST_PROVIDER_QUOTA",
        "CHANNEL_TEST_PROVIDER_TERMINAL",
        "CHANNEL_TEST_ARTIFACT_INVALID",
        "CHANNEL_TEST_ROUTE_DRIFT",
    ),
)
def test_route_test_receipt_accepts_closed_provider_blocker_taxonomy_only_for_submitted_pending(
    blocker_code: str,
) -> None:
    payload = route_test_receipt_payload(
        state="pending",
        submission_state="submitted",
    )
    payload["provider_blocker_code"] = blocker_code

    receipt = RelayChannelOperation.model_validate(payload)

    assert receipt.provider_blocker_code == blocker_code


@pytest.mark.parametrize(
    ("state", "submission_state"),
    (
        ("pending", "not_started"),
        ("pending", "submission_unknown"),
        ("succeeded", "artifact_verified"),
        ("failed", "provider_terminal"),
        ("failed", "provider_rejected"),
        ("failed", "reconciled_no_creation"),
    ),
)
def test_route_test_receipt_rejects_provider_blocker_outside_submitted_pending(
    state: str,
    submission_state: str,
) -> None:
    payload = (
        channel_reconciled_no_creation_payload()
        if submission_state == "reconciled_no_creation"
        else route_test_receipt_payload(
            state=state,
            submission_state=submission_state,
        )
    )
    payload["provider_blocker_code"] = "CHANNEL_TEST_PROVIDER_QUOTA"

    with pytest.raises(ValueError, match="provider blocker"):
        RelayChannelOperation.model_validate(payload)


def test_route_test_receipt_rejects_unknown_provider_blocker_code() -> None:
    payload = route_test_receipt_payload(
        state="pending",
        submission_state="submitted",
    )
    payload["provider_blocker_code"] = "PROVIDER_RAW_QUOTA_MESSAGE"

    with pytest.raises(ValueError):
        RelayChannelOperation.model_validate(payload)


@pytest.mark.parametrize("state", ("pending", "succeeded", "failed"))
def test_legacy_generic_test_receipt_remains_readable_without_route_state(
    state: str,
) -> None:
    payload = route_test_receipt_payload(
        state=state,
        submission_state=(
            "not_started"
            if state == "pending"
            else "artifact_verified"
            if state == "succeeded"
            else "provider_terminal"
        ),
    )
    payload.pop("provider_submission_state")
    receipt = RelayChannelOperation.model_validate(payload)
    assert receipt.provider_submission_state is None


def test_new_route_bound_channel_test_write_requires_submission_state() -> None:
    legacy_receipt = channel_operation_payload(kind="test")
    legacy_receipt.pop("provider_submission_state")
    client = HttpxRelayOperationsClient(
        base_url="https://relay.example.test",
        tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN,
        approval_key_id=APPROVAL_KEY_ID,
        approval_secret=APPROVAL_SECRET,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=legacy_receipt)
        ),
    )

    with pytest.raises(RelayTemporaryError) as captured:
        client.test_channel(
            CHANNEL_ID,
            operation_id=CHANNEL_OPERATION_ID,
            actor="platform-admin-1",
            reason="Verify the official channel before enabling traffic",
            public_model_id="seedance-1.5-pro",
            route_id="volcengine-seedance-primary-01",
        )
    assert captured.value.submission_outcome_unknown is True


def test_provider_blocker_receipt_is_accepted_by_read_and_route_test_clients() -> None:
    receipt = route_test_receipt_payload(
        state="pending",
        submission_state="submitted",
    )
    receipt["provider_blocker_code"] = "CHANNEL_TEST_PROVIDER_QUOTA"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=receipt)

    client = HttpxRelayOperationsClient(
        base_url="https://relay.example.test",
        tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN,
        approval_key_id=APPROVAL_KEY_ID,
        approval_secret=APPROVAL_SECRET,
        transport=httpx.MockTransport(handler),
    )

    read = client.get_channel_operation(
        CHANNEL_ID,
        operation_id=CHANNEL_OPERATION_ID,
    )
    tested = client.test_channel(
        CHANNEL_ID,
        operation_id=CHANNEL_OPERATION_ID,
        actor="platform-admin-1",
        reason="Verify the official channel before enabling traffic",
        public_model_id="seedance-1.5-pro",
        route_id="volcengine-seedance-primary-01",
    )

    assert read.provider_blocker_code == "CHANNEL_TEST_PROVIDER_QUOTA"
    assert tested.provider_blocker_code == "CHANNEL_TEST_PROVIDER_QUOTA"
    assert [request.method for request in seen] == ["GET", "POST"]


@pytest.mark.parametrize(
    "error_code",
    [
        "CHANNEL_TEST_FAILED",
        "CHANNEL_TEST_UNAVAILABLE",
        "CHANNEL_TEST_PROVIDER_VALIDATION",
        "CHANNEL_TEST_PROVIDER_AUTH",
        "CHANNEL_TEST_PROVIDER_QUOTA",
        "CHANNEL_TEST_PROVIDER_TERMINAL",
        "CHANNEL_TEST_ARTIFACT_INVALID",
        "CHANNEL_TEST_ROUTE_DRIFT",
        "CHANNEL_TEST_RECONCILED_NO_CREATION",
    ],
)
def test_channel_test_result_accepts_only_secret_safe_failure_taxonomy(
    error_code: str,
) -> None:
    result = RelayChannelTestResult(
        success=False,
        response_time_ms=4045,
        error_code=error_code,
    )
    assert result.error_code == error_code


def test_channel_test_result_rejects_unknown_or_inconsistent_failure_code() -> None:
    with pytest.raises(ValueError):
        RelayChannelTestResult(
            success=False,
            response_time_ms=4045,
            error_code="PROVIDER_RAW_ERROR",
        )
    with pytest.raises(ValueError):
        RelayChannelTestResult(
            success=True,
            response_time_ms=4045,
            error_code="CHANNEL_TEST_PROVIDER_TERMINAL",
        )


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


def test_synchronous_approval_matches_go_vector_and_never_returns_temporary_url() -> None:
    approval_secret = "approval-secret-one-with-at-least-32-bytes"
    synchronous_result = RelaySynchronousResultEvidence(
        provider_model_id="doubao-seedream-5-0-260128",
        provider_response_sha256=SYNCHRONOUS_RESPONSE_SHA256,
        artifact_url=SYNCHRONOUS_ARTIFACT_URL,
        provider_created_at="2026-08-28T01:02:03Z",
        generated_images=1,
        output_tokens=144,
        total_tokens=160,
    )
    expected_signature = (
        "hmac-sha256:"
        "2af01beb61b2aecf7f7589a263258992b16243c7c048f0ff5a193e28882513f4"
    )
    seen_request: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen_request.update(json.loads(request.content))
            snapshot = failed_snapshot_payload()
            snapshot.update(
                {
                    "id": SYNCHRONOUS_JOB_ID,
                    "model": "seedream-5.0",
                    "mode": "text_to_image",
                    "status": "processing",
                    "reservation_action": "hold",
                    "progress": 0,
                    "error": None,
                }
            )
            return httpx.Response(200, json=snapshot)
        receipt = reconciliation_result_payload()
        receipt.update(
            {
                "job_id": SYNCHRONOUS_JOB_ID,
                "operation_id": SYNCHRONOUS_OPERATION_ID,
                "outcome": "created",
                "upstream_task_id": "seedream:" + SYNCHRONOUS_RESPONSE_SHA256,
                "expected_route_id": 41,
                "expected_submission_attempt": 3,
                "expected_reconciliation_token": "sha256:" + "cd" * 32,
                "verification_reference": "provider-console-case-seedream-42",
                "approval_reason": (
                    "Provider console and response archive prove one image was created"
                ),
                "approval_signature": expected_signature,
                "resolved_status": "processing",
                "current_status": "processing",
                "synchronous_result": {
                    **synchronous_result.receipt_summary(),
                },
            }
        )
        return httpx.Response(200, json=receipt)

    client = HttpxRelayOperationsClient(
        base_url="https://relay.example.test",
        tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN,
        approval_key_id=APPROVAL_KEY_ID,
        approval_secret=approval_secret,
        transport=httpx.MockTransport(handler),
    )
    signature = client._approval_signature(
        job_id=SYNCHRONOUS_JOB_ID,
        operation_id=SYNCHRONOUS_OPERATION_ID,
        outcome="created",
        upstream_task_id="seedream:" + SYNCHRONOUS_RESPONSE_SHA256,
        expected_route_id=41,
        expected_submission_attempt=3,
        expected_reconciliation_token="sha256:" + "cd" * 32,
        verification_reference="provider-console-case-seedream-42",
        approved_by="platform-admin-1",
        approval_reason=(
            "Provider console and response archive prove one image was created"
        ),
        synchronous_result=synchronous_result,
    )
    assert signature == expected_signature

    client.resolve_submission_unknown(
        SYNCHRONOUS_JOB_ID,
        operation_id=SYNCHRONOUS_OPERATION_ID,
        outcome="created",
        upstream_task_id="seedream:" + SYNCHRONOUS_RESPONSE_SHA256,
        expected_route_id=41,
        expected_submission_attempt=3,
        expected_reconciliation_token="sha256:" + "cd" * 32,
        verification_reference="provider-console-case-seedream-42",
        approved_by="platform-admin-1",
        approval_reason=(
            "Provider console and response archive prove one image was created"
        ),
        synchronous_result=synchronous_result,
        request_id="platform-seedream-resolve",
    )
    assert seen_request["approval_signature"] == expected_signature
    assert seen_request["synchronous_result"]["artifact_url"] == (
        SYNCHRONOUS_ARTIFACT_URL
    )

    receipt = client.get_reconciliation_result(
        SYNCHRONOUS_JOB_ID,
        operation_id=SYNCHRONOUS_OPERATION_ID,
    )
    serialized_receipt = receipt.model_dump(mode="json")
    assert serialized_receipt["synchronous_result"]["artifact_url_sha256"] == (
        hashlib.sha256(SYNCHRONOUS_ARTIFACT_URL.encode()).hexdigest()
    )
    assert SYNCHRONOUS_ARTIFACT_URL not in json.dumps(serialized_receipt)


@pytest.mark.parametrize(
    ("field", "overflow_value"),
    (
        ("verification_reference", "核" * 64),
        ("approved_by", "审" * 43),
        ("approval_reason", "因" * 81),
    ),
)
def test_operations_client_rejects_reconciliation_evidence_over_go_byte_limit(
    field: str,
    overflow_value: str,
) -> None:
    sent_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent_requests.append(request)
        return httpx.Response(500)

    client = HttpxRelayOperationsClient(
        base_url="https://relay.example.test",
        tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN,
        approval_key_id=APPROVAL_KEY_ID,
        approval_secret=APPROVAL_SECRET,
        transport=httpx.MockTransport(handler),
    )
    evidence = {
        "verification_reference": "provider-console-case-42",
        "approved_by": "platform-admin-1",
        "approval_reason": "Provider console proves absence",
    }
    evidence[field] = overflow_value

    with pytest.raises(ValueError, match=field):
        client.resolve_submission_unknown(
            JOB_ID,
            operation_id=OPERATION_ID,
            outcome="not_created",
            upstream_task_id="",
            expected_route_id=19,
            expected_submission_attempt=2,
            expected_reconciliation_token=TOKEN,
            **evidence,
        )
    assert sent_requests == []


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("artifact_url", "http://provider.example.test/result.png"),
        ("artifact_url", "https://user@provider.example.test/result.png"),
        ("artifact_url", "https://provider.example.test/result.png#secret"),
        ("artifact_url", "https://provider.example.test/" + "图" * 3_000),
        ("provider_model_id", "模" * 43),
        ("provider_created_at", "2026-08-28T01:02:03+00:00"),
        ("generated_images", 0),
        ("generated_images", 17),
        ("output_tokens", -1),
        ("total_tokens", 143),
        ("generated_images", "1"),
    ),
)
def test_synchronous_result_evidence_rejects_noncanonical_fields(
    field: str,
    value: object,
) -> None:
    payload = {
        "provider_model_id": "doubao-seedream-5-0-260128",
        "provider_response_sha256": SYNCHRONOUS_RESPONSE_SHA256,
        "artifact_url": SYNCHRONOUS_ARTIFACT_URL,
        "provider_created_at": "2026-08-28T01:02:03Z",
        "generated_images": 1,
        "output_tokens": 144,
        "total_tokens": 160,
    }
    payload[field] = value
    with pytest.raises(ValueError):
        RelaySynchronousResultEvidence.model_validate(payload)


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
                "public_model_id": "seedance-1.5-pro",
                "route_id": "volcengine-seedance-primary-01",
                "mode": "image_to_video",
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
        timeout_seconds=7,
        channel_test_timeout_seconds=660,
        transport=httpx.MockTransport(handler),
    )
    page = client.list_channels(page=1, page_size=25, status="enabled")
    detail = client.get_channel(CHANNEL_ID)
    tested = client.test_channel(
        CHANNEL_ID,
        operation_id=CHANNEL_OPERATION_ID,
        actor="platform-admin-1",
        reason="Verify the official channel before enabling traffic",
        public_model_id="seedance-1.5-pro",
        route_id="volcengine-seedance-primary-01",
        mode="image_to_video",
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
    channel_test_timeout = seen[2].extensions["timeout"]
    status_timeout = seen[3].extensions["timeout"]
    assert set(channel_test_timeout.values()) == {660.0}
    assert set(status_timeout.values()) == {7.0}


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
            public_model_id="seedance-1.5-pro",
            route_id="volcengine-seedance-primary-01",
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
            public_model_id="seedance-1.5-pro",
            route_id="volcengine-seedance-primary-01",
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


def test_channel_no_creation_reconciliation_uses_exact_one_way_contract() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "POST"
        assert request.url.path == (
            "/internal/platform-generation-operations/channels/17/operations/"
            f"{CHANNEL_OPERATION_ID}/reconcile-no-creation"
        )
        assert request.headers["x-relay-operations-token"] == OPERATIONS_TOKEN
        assert request.headers["x-request-id"] == "platform-reconcile-request"
        assert json.loads(request.content) == {
            "tenant_id": TENANT_ID,
            "actor": "platform-owner-1",
            "reason": "Provider console confirms that no provider task was created",
            "confirmed_no_provider_creation": True,
        }
        return httpx.Response(200, json=channel_reconciled_no_creation_payload())

    client = HttpxRelayOperationsClient(
        base_url="https://relay.example.test",
        tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN,
        approval_key_id=APPROVAL_KEY_ID,
        approval_secret=APPROVAL_SECRET,
        transport=httpx.MockTransport(handler),
    )
    result = client.reconcile_channel_test_no_creation(
        CHANNEL_ID,
        operation_id=CHANNEL_OPERATION_ID,
        actor="platform-owner-1",
        reason="Provider console confirms that no provider task was created",
        confirmed_no_provider_creation=True,
        request_id="platform-reconcile-request",
    )

    assert len(seen) == 1
    assert result.state == "failed"
    assert result.provider_submission_state == "reconciled_no_creation"
    assert result.provider_blocker_code is None
    assert result.result is not None
    assert result.result.error_code == "CHANNEL_TEST_RECONCILED_NO_CREATION"


def test_channel_no_creation_reconciliation_rejects_false_confirmation_without_post(
) -> None:
    seen: list[httpx.Request] = []
    client = HttpxRelayOperationsClient(
        base_url="https://relay.example.test",
        tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN,
        approval_key_id=APPROVAL_KEY_ID,
        approval_secret=APPROVAL_SECRET,
        transport=httpx.MockTransport(
            lambda request: seen.append(request) or httpx.Response(500)
        ),
    )

    with pytest.raises(ValueError, match="must be true"):
        client.reconcile_channel_test_no_creation(
            CHANNEL_ID,
            operation_id=CHANNEL_OPERATION_ID,
            actor="platform-owner-1",
            reason="Provider console confirms that no provider task was created",
            confirmed_no_provider_creation=False,  # type: ignore[arg-type]
        )
    assert seen == []


def test_channel_reconciliation_reason_allows_plain_chinese_evidence() -> None:
    reason = "供应商控制台与账单记录均确认未创建任何任务"
    assert validate_relay_channel_reconciliation_reason(reason) == reason


@pytest.mark.parametrize(
    "reason",
    (
        "确认\u202e未创建任务",
        "确认\u200b未创建任务",
        "确认\x1f未创建任务",
        "确认\x85未创建任务",
    ),
)
def test_channel_reconciliation_reason_rejects_unicode_format_and_control_characters_before_http_post(
    reason: str,
) -> None:
    seen: list[httpx.Request] = []
    client = HttpxRelayOperationsClient(
        base_url="https://relay.example.test",
        tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN,
        approval_key_id=APPROVAL_KEY_ID,
        approval_secret=APPROVAL_SECRET,
        transport=httpx.MockTransport(
            lambda request: seen.append(request) or httpx.Response(500)
        ),
    )

    with pytest.raises(ValueError, match="control characters"):
        client.reconcile_channel_test_no_creation(
            CHANNEL_ID,
            operation_id=CHANNEL_OPERATION_ID,
            actor="platform-owner-1",
            reason=reason,
            confirmed_no_provider_creation=True,
        )
    assert seen == []


@pytest.mark.parametrize("actor", ("owner\u202e1", "owner\u200b1"))
def test_channel_reconciliation_actor_rejects_unicode_format_characters_before_http_post(
    actor: str,
) -> None:
    seen: list[httpx.Request] = []
    client = HttpxRelayOperationsClient(
        base_url="https://relay.example.test",
        tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN,
        approval_key_id=APPROVAL_KEY_ID,
        approval_secret=APPROVAL_SECRET,
        transport=httpx.MockTransport(
            lambda request: seen.append(request) or httpx.Response(500)
        ),
    )

    with pytest.raises(ValueError, match="control characters"):
        client.reconcile_channel_test_no_creation(
            CHANNEL_ID,
            operation_id=CHANNEL_OPERATION_ID,
            actor=actor,
            reason="Provider console confirms no creation",
            confirmed_no_provider_creation=True,
        )
    assert seen == []


@pytest.mark.parametrize(
    "reason",
    (
        "Evidence is at ftp://provider.invalid/task",
        "Authorization: Bearer provider-secret-value",
        "Authorization = provider-secret-value",
        "Provider token = provider-secret-value",
        "Provider key: provider-secret-value",
        "Provider password=provider-secret-value",
        "Provider secret: provider-secret-value",
        "Provider credential was inspected",
        "Provider api_key was inspected",
        "Query contains X-Amz-Signature=provider-secret-value",
    ),
)
def test_channel_reconciliation_reason_rejects_secrets_before_http_post(
    reason: str,
) -> None:
    seen: list[httpx.Request] = []
    client = HttpxRelayOperationsClient(
        base_url="https://relay.example.test",
        tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN,
        approval_key_id=APPROVAL_KEY_ID,
        approval_secret=APPROVAL_SECRET,
        transport=httpx.MockTransport(
            lambda request: seen.append(request) or httpx.Response(500)
        ),
    )

    with pytest.raises(ValueError, match="sensitive material"):
        client.reconcile_channel_test_no_creation(
            CHANNEL_ID,
            operation_id=CHANNEL_OPERATION_ID,
            actor="platform-owner-1",
            reason=reason,
            confirmed_no_provider_creation=True,
        )
    assert seen == []


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("provider_submission_state", "submitted"),
        ("provider_blocker_code", "CHANNEL_TEST_PROVIDER_QUOTA"),
        ("api_version", "v2"),
        ("idempotent_replay", True),
        ("reconciliation_actor", "different-platform-owner"),
        ("reconciliation_reason", "Different confirmation evidence"),
        ("reconciled_at", "2026-08-28T02:03:04Z"),
        (
            "result",
            {
                "success": False,
                "response_time_ms": 1,
                "error_code": "CHANNEL_TEST_RECONCILED_NO_CREATION",
            },
        ),
        ("provider_task_id", "provider-secret-task-1"),
    ),
)
def test_channel_no_creation_reconciliation_rejects_ambiguous_receipt(
    field: str, value: object
) -> None:
    payload = channel_reconciled_no_creation_payload()
    payload[field] = value
    client = HttpxRelayOperationsClient(
        base_url="https://relay.example.test",
        tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN,
        approval_key_id=APPROVAL_KEY_ID,
        approval_secret=APPROVAL_SECRET,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=payload)
        ),
    )

    with pytest.raises(RelayTemporaryError) as captured:
        client.reconcile_channel_test_no_creation(
            CHANNEL_ID,
            operation_id=CHANNEL_OPERATION_ID,
            actor="platform-owner-1",
            reason="Provider console confirms that no provider task was created",
            confirmed_no_provider_creation=True,
        )
    assert captured.value.submission_outcome_unknown is True
    assert "provider-secret-task-1" not in str(captured.value)


def test_channel_no_creation_reconciliation_never_converts_provider_task_conflict(
) -> None:
    client = HttpxRelayOperationsClient(
        base_url="https://relay.example.test",
        tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN,
        approval_key_id=APPROVAL_KEY_ID,
        approval_secret=APPROVAL_SECRET,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                409,
                json={
                    "error": {
                        "code": "CHANNEL_OPERATION_CONFLICT",
                        "message": "provider-secret-task-1 exists",
                        "retryable": False,
                        "details": {},
                        "request_id": "relay-conflict-request",
                    }
                },
            )
        ),
    )

    with pytest.raises(RelayPermanentError) as captured:
        client.reconcile_channel_test_no_creation(
            CHANNEL_ID,
            operation_id=CHANNEL_OPERATION_ID,
            actor="platform-owner-1",
            reason="Provider console confirms that no provider task was created",
            confirmed_no_provider_creation=True,
        )
    assert captured.value.response_status == 409
    assert "provider-secret-task-1" not in str(captured.value)


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
