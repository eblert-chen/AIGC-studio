from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from platform_api.models import AuditLog, User
from platform_api.relay_client import (
    RelayCallbackDelivery,
    RelayCallbackDeliveryPage,
    RelayCallbackRedriveEvidence,
    RelayCallbackRedriveResult,
    RelayPermanentError,
    RelayTemporaryError,
)


class FakeCallbackRelay:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.item = RelayCallbackDelivery(
            api_version="v1", schema_version=1, object="generation.callback_delivery",
            event_id=uuid4(), tenant_id=uuid4(), job_id=uuid4(),
            source_client_id="platform-client", original_request_id="callback-original-request",
            payload_sha256="1" * 64, callback_url_sha256="2" * 64,
            state="dead_letter", attempts=8, max_attempts=8, available_at=now,
            response_status=503, last_error="callback_rejected", delivered_at=None,
            dead_lettered_at=now, created_at=now, updated_at=now, redrives=[],
        )
        self.results: dict[str, RelayCallbackRedriveResult] = {}
        self.redrive_calls: list[dict] = []
        self.fail_after_commit_once = False

    def list_callback_dead_letters(self, *, page=1, page_size=50, request_id=None):
        return RelayCallbackDeliveryPage(
            api_version="v1", schema_version=1, object="list", data=[self.item],
            page=page, page_size=page_size, total=1,
        )

    def get_callback_dead_letter(self, event_id, *, request_id=None):
        if event_id != str(self.item.event_id):
            raise RelayPermanentError("not found", response_status=404)
        return self.item

    def get_callback_redrive_result(self, event_id, *, operation_id, request_id=None):
        result = self.results.get(operation_id)
        if result is None or event_id != str(self.item.event_id):
            raise RelayPermanentError("not found", response_status=404)
        return result

    def redrive_callback_dead_letter(self, event_id, **kwargs):
        self.redrive_calls.append({"event_id": event_id, **kwargs})
        now = datetime.now(timezone.utc)
        evidence = RelayCallbackRedriveEvidence(
            event_id=uuid4(), operation_id=kwargs["operation_id"], request_id=kwargs["request_id"],
            actor=kwargs["actor"], reason=kwargs["reason"], previous_state="dead_letter",
            previous_attempts=8, previous_max_attempts=8, previous_response_status=503,
            previous_last_error="callback_rejected", previous_dead_lettered_at=now,
            callback_url_sha256="2" * 64, payload_sha256="1" * 64,
            original_callback_request_id="callback-original-request", result_state="pending",
            receipt_sha256="3" * 64, redriven_at=now,
        )
        result = RelayCallbackRedriveResult(
            api_version="v1", schema_version=1, object="generation.callback_redrive_result",
            delivery_event_id=self.item.event_id, tenant_id=self.item.tenant_id,
            current_state="pending", evidence=evidence,
        )
        self.results[kwargs["operation_id"]] = result
        if self.fail_after_commit_once:
            self.fail_after_commit_once = False
            raise RelayTemporaryError("lost response", submission_outcome_unknown=True)
        return result


def _admin(app) -> str:
    with app.state.session_factory.begin() as session:
        user = User(email=f"callback-admin-{uuid4()}@example.com", display_name="Callback Admin", is_platform_admin=True)
        session.add(user)
        session.flush()
        return user.id


def test_platform_admin_lists_details_redrives_once_and_audits(client, app) -> None:
    relay = FakeCallbackRelay()
    app.state.relay_operations_client = relay
    admin_id = _admin(app)
    headers = {"X-Platform-Admin-User-ID": admin_id, "X-Request-ID": "callback-platform-redrive-1"}
    event_id = str(relay.item.event_id)

    listed = client.get("/api/v1/platform-admin/relay/callback-dead-letters", headers=headers)
    detail = client.get(f"/api/v1/platform-admin/relay/callback-dead-letters/{event_id}", headers=headers)
    body = {"operation_id": "callback-platform-operation-0001", "actor": "oncall-a", "reason": "Destination incident is resolved", "approved": True}
    redriven = client.post(f"/api/v1/platform-admin/relay/callback-dead-letters/{event_id}/redrive", headers=headers, json=body)
    result = client.get(f"/api/v1/platform-admin/relay/callback-dead-letters/{event_id}/result?operation_id={body['operation_id']}", headers=headers)

    assert listed.status_code == 200 and listed.json()["total"] == 1
    assert detail.status_code == 200
    assert "callbacks.example" not in detail.text
    assert detail.json()["callback_url_sha256"] == "2" * 64
    assert redriven.status_code == 200, redriven.text
    assert result.status_code == 200 and result.json()["evidence"]["operation_id"] == body["operation_id"]
    assert len(relay.redrive_calls) == 1
    with app.state.session_factory() as session:
        audits = session.query(AuditLog).filter(AuditLog.target_id == event_id).order_by(AuditLog.created_at).all()
        assert [row.action for row in audits] == ["relay.callback_dead_letter.approve_redrive", "relay.callback_dead_letter.redrive"]
        assert audits[0].before_summary["state"] == "dead_letter"
        assert audits[1].after_summary["current_state"] == "pending"


def test_ambiguous_redrive_is_only_read_back_and_never_reposted_by_result_api(client, app) -> None:
    relay = FakeCallbackRelay()
    relay.fail_after_commit_once = True
    app.state.relay_operations_client = relay
    admin_id = _admin(app)
    headers = {"X-Platform-Admin-User-ID": admin_id, "X-Request-ID": "callback-platform-lost-1"}
    event_id = str(relay.item.event_id)
    operation_id = "callback-platform-operation-lost-1"
    response = client.post(
        f"/api/v1/platform-admin/relay/callback-dead-letters/{event_id}/redrive",
        headers=headers,
        json={"operation_id": operation_id, "actor": "oncall-a", "reason": "Destination incident is resolved", "approved": True},
    )
    assert response.status_code == 503
    assert len(relay.redrive_calls) == 1

    readback = client.get(
        f"/api/v1/platform-admin/relay/callback-dead-letters/{event_id}/result?operation_id={operation_id}",
        headers={**headers, "X-Request-ID": "callback-platform-lost-read"},
    )
    assert readback.status_code == 200
    assert len(relay.redrive_calls) == 1

    # Even if a client mistakenly repeats the Platform POST with the same
    # stable operation_id, Platform reads the immutable Relay receipt and does
    # not forward a second POST.
    replay = client.post(
        f"/api/v1/platform-admin/relay/callback-dead-letters/{event_id}/redrive",
        headers={**headers, "X-Request-ID": "callback-platform-lost-replay"},
        json={"operation_id": operation_id, "actor": "oncall-a", "reason": "Destination incident is resolved", "approved": True},
    )
    assert replay.status_code == 200
    assert len(relay.redrive_calls) == 1


def test_callback_dead_letter_requires_platform_admin(client, app) -> None:
    app.state.relay_operations_client = FakeCallbackRelay()
    assert client.get("/api/v1/platform-admin/relay/callback-dead-letters").status_code == 401
