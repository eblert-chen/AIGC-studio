from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from platform_api.models import AuditLog, User
from platform_api.relay_client import (
    RelayJobSnapshot,
    RelayPermanentError,
    RelaySynchronousResultReceipt,
    RelayTemporaryError,
    RelayUnknownSubmission,
    RelayUnknownSubmissionPage,
    RelayUnknownSubmissionResult,
)

CAPABILITY_REVISION = "sha256:" + "c" * 64
APPROVAL_KEY_ID = "platform-approval-v1"
APPROVAL_SIGNATURE = "hmac-sha256:" + "a" * 64


class FakeRelayOperationsClient:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.item = RelayUnknownSubmission(
            api_version="v1",
            schema_version=1,
            object="generation.reconciliation",
            job_id=uuid4(),
            tenant_id=uuid4(),
            client_reference_id="platform-task-1",
            model="provider.video.v1",
            mode="text_to_video",
            status="reconciliation_required",
            provider_route_id=19,
            provider_route_key="provider-account-one",
            provider_name="provider",
            provider_account_id="account-one",
            provider_channel_id=7,
            provider_key_index=0,
            provider_channel_class="official",
            provider_upstream_model="video-v1",
            provider_submission_attempt=2,
            unknown_at=now,
            reconciliation_token="sha256:" + "d" * 64,
            error_code="SUBMISSION_RECONCILIATION_REQUIRED",
            error_message="Provider response was lost",
            created_at=now,
            updated_at=now,
        )
        self.resolve_calls: list[dict] = []
        self.result_calls: list[dict] = []
        self.result: RelayUnknownSubmissionResult | None = None
        self.fail_after_resolution_once = False

    def list_submission_unknown(self, *, page=1, page_size=50, request_id=None):
        return RelayUnknownSubmissionPage(
            api_version="v1",
            schema_version=1,
            object="list",
            data=[self.item],
            page=page,
            page_size=page_size,
            total=1,
        )

    def get_submission_unknown(self, job_id, *, request_id=None):
        assert job_id == str(self.item.job_id)
        return self.item

    def get_reconciliation_result(self, job_id, *, operation_id, request_id=None):
        self.result_calls.append(
            {
                "job_id": job_id,
                "operation_id": operation_id,
                "request_id": request_id,
            }
        )
        if (
            self.result is None
            or job_id != str(self.result.job_id)
            or operation_id != self.result.operation_id
        ):
            raise RelayPermanentError(
                "result not found",
                response_status=404,
            )
        return self.result

    def resolve_submission_unknown(self, job_id, **kwargs):
        self.resolve_calls.append({"job_id": job_id, **kwargs})
        now = datetime.now(timezone.utc)
        created = kwargs["outcome"] == "created"
        synchronous_evidence = kwargs.get("synchronous_result")
        synchronous_receipt = (
            RelaySynchronousResultReceipt.model_validate(
                synchronous_evidence.receipt_summary()
            )
            if synchronous_evidence is not None
            else None
        )
        if self.result is None:
            self.result = RelayUnknownSubmissionResult(
                api_version="v1",
                schema_version=1,
                object="generation.reconciliation_result",
                event_id=uuid4(),
                operation_id=kwargs["operation_id"],
                request_id=kwargs["request_id"],
                tenant_id=self.item.tenant_id,
                job_id=self.item.job_id,
                outcome=kwargs["outcome"],
                upstream_task_id=kwargs["upstream_task_id"],
                expected_route_id=kwargs["expected_route_id"],
                expected_submission_attempt=kwargs["expected_submission_attempt"],
                expected_reconciliation_token=kwargs["expected_reconciliation_token"],
                verification_reference=kwargs["verification_reference"],
                approved_by=kwargs["approved_by"],
                approval_reason=kwargs["approval_reason"],
                approval_key_id=APPROVAL_KEY_ID,
                approval_signature=APPROVAL_SIGNATURE,
                synchronous_result=synchronous_receipt,
                resolved_status="processing" if created else "failed",
                current_status="processing" if created else "failed",
                payload_sha256="f" * 64,
                resolved_at=now,
            )
        if self.fail_after_resolution_once:
            self.fail_after_resolution_once = False
            raise RelayTemporaryError(
                "response was lost",
                submission_outcome_unknown=True,
            )
        return RelayJobSnapshot(
            api_version="v1",
            schema_version=1,
            object="generation",
            id=job_id,
            client_reference_id="platform-task-1",
            model="provider.video.v1",
            expected_capability_revision=CAPABILITY_REVISION,
            capability_revision=CAPABILITY_REVISION,
            mode=self.item.mode,
            inputs={"prompt": "test", "assets": []},
            output={
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "count": 1,
                "face_enabled": False,
            },
            metadata={},
            status="processing" if created else "failed",
            reservation_action="hold" if created else "release",
            progress=0 if created else 100,
            outputs=[],
            error=(
                None
                if created
                else {
                    "code": "SUBMISSION_CONFIRMED_NOT_CREATED",
                    "message": "Provider confirmed no task was created",
                    "retryable": False,
                    "details": {},
                }
            ),
            created_at=now,
            updated_at=now,
        )


def test_admin_discovers_approves_and_audits_unknown_submission(client, app) -> None:
    relay = FakeRelayOperationsClient()
    app.state.relay_operations_client = relay
    with app.state.session_factory.begin() as session:
        admin = User(
            email="relay-operations-admin@example.com",
            display_name="Relay Operations Admin",
            is_platform_admin=True,
        )
        session.add(admin)
        session.flush()
        admin_id = admin.id
    headers = {
        "X-Platform-Admin-User-ID": admin_id,
        "X-Request-ID": "relay-unknown-approval-1",
    }

    listed = client.get(
        "/api/v1/platform-admin/relay/submission-unknown",
        headers=headers,
    )
    assert listed.status_code == 200, listed.text
    assert listed.headers["cache-control"] == "private, no-store"
    assert listed.json()["total"] == 1

    stale = client.post(
        f"/api/v1/platform-admin/relay/submission-unknown/{relay.item.job_id}/resolve",
        headers=headers,
        json={
            "outcome": "not_created",
            "upstream_task_id": "",
            "expected_route_id": relay.item.provider_route_id,
            "expected_submission_attempt": relay.item.provider_submission_attempt,
            "expected_reconciliation_token": "sha256:" + "e" * 64,
            "verification_reference": "provider-console-case-42",
            "reason": "Provider console proves absence",
        },
    )
    assert stale.status_code == 409
    assert relay.resolve_calls == []

    resolved = client.post(
        f"/api/v1/platform-admin/relay/submission-unknown/{relay.item.job_id}/resolve",
        headers=headers,
        json={
            "outcome": "not_created",
            "upstream_task_id": "",
            "expected_route_id": relay.item.provider_route_id,
            "expected_submission_attempt": relay.item.provider_submission_attempt,
            "expected_reconciliation_token": relay.item.reconciliation_token,
            "verification_reference": "provider-console-case-42",
            "reason": "Provider console proves absence",
        },
    )
    assert resolved.status_code == 200, resolved.text
    operation_id = resolved.json()["operation_id"]
    assert operation_id == f"relay-reconcile-op-{relay.item.job_id}"
    assert resolved.json()["resolved"]["status"] == "failed"
    assert resolved.json()["reconciliation_result"]["operation_id"] == operation_id
    assert relay.resolve_calls[0]["expected_route_id"] == 19
    assert relay.resolve_calls[0]["approved_by"] == admin_id
    assert relay.resolve_calls[0]["operation_id"] == operation_id
    assert relay.resolve_calls[0]["request_id"] == "relay-unknown-approval-1"

    readback = client.get(
        f"/api/v1/platform-admin/relay/submission-unknown/{relay.item.job_id}/result",
        headers={**headers, "X-Request-ID": "relay-result-readback-1"},
    )
    assert readback.status_code == 200, readback.text
    assert readback.json()["operation_id"] == operation_id
    assert readback.headers["cache-control"] == "private, no-store"

    with app.state.session_factory() as session:
        entries = (
            session.query(AuditLog)
            .filter(AuditLog.target_id == str(relay.item.job_id))
            .order_by(AuditLog.created_at.asc())
            .all()
        )
        assert [entry.action for entry in entries] == [
            "relay.submission_unknown.approve",
            "relay.submission_unknown.resolve",
        ]
        assert entries[0].after_summary["verification_reference"] == (
            "provider-console-case-42"
        )
        for entry in entries:
            assert entry.after_summary["operation_id"] == operation_id
            assert entry.after_summary["verification_reference"] == (
                "provider-console-case-42"
            )
            assert entry.after_summary["approved_by"] == admin_id
            assert entry.after_summary["reason"] == ("Provider console proves absence")
        assert entries[0].after_summary["request_id"] == ("relay-unknown-approval-1")
        assert entries[1].after_summary["relay_request_id"] == (
            "relay-unknown-approval-1"
        )
        assert entries[1].after_summary["approval_key_id"] == APPROVAL_KEY_ID
        assert entries[1].after_summary["approval_signature"] == (APPROVAL_SIGNATURE)


def test_admin_can_read_back_and_replay_after_lost_relay_response(client, app) -> None:
    relay = FakeRelayOperationsClient()
    relay.fail_after_resolution_once = True
    app.state.relay_operations_client = relay
    with app.state.session_factory.begin() as session:
        admin = User(
            email="relay-retry-admin@example.com",
            display_name="Relay Retry Admin",
            is_platform_admin=True,
        )
        session.add(admin)
        session.flush()
        admin_id = admin.id
    body = {
        "outcome": "not_created",
        "upstream_task_id": "",
        "expected_route_id": relay.item.provider_route_id,
        "expected_submission_attempt": relay.item.provider_submission_attempt,
        "expected_reconciliation_token": relay.item.reconciliation_token,
        "verification_reference": "provider-console-lost-response",
        "reason": "Provider console proves no task exists",
    }
    path = (
        f"/api/v1/platform-admin/relay/submission-unknown/"
        f"{relay.item.job_id}/resolve"
    )
    first = client.post(
        path,
        headers={
            "X-Platform-Admin-User-ID": admin_id,
            "X-Request-ID": "relay-lost-response-1",
        },
        json=body,
    )
    assert first.status_code == 503

    readback = client.get(
        f"/api/v1/platform-admin/relay/submission-unknown/{relay.item.job_id}/result",
        headers={
            "X-Platform-Admin-User-ID": admin_id,
            "X-Request-ID": "relay-lost-response-readback",
        },
    )
    assert readback.status_code == 200, readback.text
    operation_id = readback.json()["operation_id"]

    replay = client.post(
        path,
        headers={
            "X-Platform-Admin-User-ID": admin_id,
            "X-Request-ID": "relay-lost-response-2",
        },
        json=body,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["operation_id"] == operation_id
    assert relay.resolve_calls[0]["operation_id"] == operation_id
    assert relay.resolve_calls[1]["operation_id"] == operation_id

    with app.state.session_factory() as session:
        entries = (
            session.query(AuditLog)
            .filter(AuditLog.target_id == str(relay.item.job_id))
            .order_by(AuditLog.created_at.asc())
            .all()
        )
        assert [entry.action for entry in entries] == [
            "relay.submission_unknown.approve",
            "relay.submission_unknown.resolve",
        ]
        assert all(
            entry.after_summary["operation_id"] == operation_id for entry in entries
        )


@pytest.mark.parametrize("image_mode", ["text_to_image", "image_to_image"])
def test_admin_resolves_synchronous_image_without_auditing_temporary_url(
    client,
    app,
    image_mode,
) -> None:
    relay = FakeRelayOperationsClient()
    relay.item.mode = image_mode
    relay.item.model = "seedream-5.0"
    relay.item.provider_upstream_model = "doubao-seedream-5-0-260128"
    app.state.relay_operations_client = relay
    with app.state.session_factory.begin() as session:
        admin = User(
            email=f"relay-{image_mode}-reconcile-admin@example.com",
            display_name="Relay Image Reconcile Admin",
            is_platform_admin=True,
        )
        session.add(admin)
        session.flush()
        admin_id = admin.id

    provider_digest = "ab" * 32
    temporary_url = "https://provider.example.test/image.png?token=private"
    provider_created_at = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    body = {
        "operation_id": "reconcile-seedream-created-0001",
        "outcome": "created",
        "upstream_task_id": "seedream:" + provider_digest,
        "expected_route_id": relay.item.provider_route_id,
        "expected_submission_attempt": relay.item.provider_submission_attempt,
        "expected_reconciliation_token": relay.item.reconciliation_token,
        "verification_reference": "provider-console-case-seedream-42",
        "reason": "Provider response archive proves one image was created",
        "synchronous_result": {
            "provider_model_id": relay.item.provider_upstream_model,
            "provider_response_sha256": provider_digest,
            "artifact_url": temporary_url,
            "provider_created_at": provider_created_at,
            "generated_images": 1,
            "output_tokens": 144,
            "total_tokens": 160,
        },
    }
    response = client.post(
        f"/api/v1/platform-admin/relay/submission-unknown/{relay.item.job_id}/resolve",
        headers={
            "X-Platform-Admin-User-ID": admin_id,
            "X-Request-ID": "relay-image-reconcile-1",
        },
        json=body,
    )
    assert response.status_code == 200, response.text
    receipt = response.json()["reconciliation_result"]["synchronous_result"]
    assert receipt["artifact_url_sha256"] == hashlib.sha256(
        temporary_url.encode("utf-8")
    ).hexdigest()
    assert "artifact_url" not in receipt
    assert temporary_url not in response.text
    assert relay.resolve_calls[0]["synchronous_result"].artifact_url == temporary_url

    with app.state.session_factory() as session:
        entries = (
            session.query(AuditLog)
            .filter(AuditLog.target_id == str(relay.item.job_id))
            .order_by(AuditLog.created_at.asc())
            .all()
        )
        assert [entry.action for entry in entries] == [
            "relay.submission_unknown.approve",
            "relay.submission_unknown.resolve",
        ]
        serialized_audits = json.dumps(
            [
                {
                    "before": entry.before_summary,
                    "after": entry.after_summary,
                }
                for entry in entries
            ],
            sort_keys=True,
        )
        assert temporary_url not in serialized_audits
        assert "token=private" not in serialized_audits
        assert receipt["artifact_url_sha256"] in serialized_audits


def test_admin_rejects_synchronous_result_for_non_image_and_requires_it_for_image(
    client,
    app,
) -> None:
    relay = FakeRelayOperationsClient()
    app.state.relay_operations_client = relay
    with app.state.session_factory.begin() as session:
        admin = User(
            email="relay-mode-reconcile-admin@example.com",
            display_name="Relay Mode Reconcile Admin",
            is_platform_admin=True,
        )
        session.add(admin)
        session.flush()
        admin_id = admin.id
    headers = {"X-Platform-Admin-User-ID": admin_id}
    digest = "ab" * 32
    body = {
        "outcome": "created",
        "upstream_task_id": "seedream:" + digest,
        "expected_route_id": relay.item.provider_route_id,
        "expected_submission_attempt": relay.item.provider_submission_attempt,
        "expected_reconciliation_token": relay.item.reconciliation_token,
        "verification_reference": "provider-console-case-seedream-42",
        "reason": "Provider response archive proves creation",
        "synchronous_result": {
            "provider_model_id": "doubao-seedream-5-0-260128",
            "provider_response_sha256": digest,
            "artifact_url": "https://provider.example.test/image.png?token=private",
            "provider_created_at": "2026-08-28T01:02:03Z",
            "generated_images": 1,
            "output_tokens": 144,
            "total_tokens": 160,
        },
    }
    path = (
        f"/api/v1/platform-admin/relay/submission-unknown/"
        f"{relay.item.job_id}/resolve"
    )
    wrong_mode = client.post(path, headers=headers, json=body)
    assert wrong_mode.status_code == 409
    assert relay.resolve_calls == []

    relay.item.mode = "text_to_image"
    missing_result = client.post(
        path,
        headers=headers,
        json={key: value for key, value in body.items() if key != "synchronous_result"},
    )
    assert missing_result.status_code == 409
    assert relay.resolve_calls == []


def test_admin_rejects_reconciliation_evidence_over_go_byte_limit_before_relay(
    client,
    app,
) -> None:
    relay = FakeRelayOperationsClient()
    app.state.relay_operations_client = relay
    with app.state.session_factory.begin() as session:
        admin = User(
            email="relay-byte-limit-admin@example.com",
            display_name="Relay Byte Limit Admin",
            is_platform_admin=True,
        )
        session.add(admin)
        session.flush()
        admin_id = admin.id

    path = (
        f"/api/v1/platform-admin/relay/submission-unknown/"
        f"{relay.item.job_id}/resolve"
    )
    base_body = {
        "outcome": "not_created",
        "upstream_task_id": "",
        "expected_route_id": relay.item.provider_route_id,
        "expected_submission_attempt": relay.item.provider_submission_attempt,
        "expected_reconciliation_token": relay.item.reconciliation_token,
        "verification_reference": "provider-console-case-42",
        "reason": "Provider console proves absence",
    }
    for field, overflow_value in (
        ("verification_reference", "核" * 64),
        ("reason", "因" * 81),
    ):
        response = client.post(
            path,
            headers={"X-Platform-Admin-User-ID": admin_id},
            json={**base_body, field: overflow_value},
        )
        assert response.status_code == 422, response.text

    assert relay.resolve_calls == []
