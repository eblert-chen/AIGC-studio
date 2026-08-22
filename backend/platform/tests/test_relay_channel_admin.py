from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from platform_api.models import AuditLog, RelayChannelOperationJournal, User
from platform_api.relay_client import (
    RelayChannel,
    RelayChannelCredentialState,
    RelayChannelOperation,
    RelayChannelPage,
    RelayChannelStatusResult,
    RelayChannelTestResult,
    RelayErrorEnvelopeDetail,
    RelayPermanentError,
    RelayTemporaryError,
)

TENANT_ID = UUID("51bdf7c4-93a6-4b7c-a4a1-03f616a10f30")
CHANNEL_ID = 17
CHANNEL_REVISION = "sha256:" + "d" * 64
CHANNEL_RESULT_REVISION = "sha256:" + "e" * 64
TEST_OPERATION_ID = "channel-test-operation-0001"
STATUS_OPERATION_ID = "channel-status-operation-0001"


class FakeChannelRelay:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.channel = RelayChannel(
            id=CHANNEL_ID,
            name="Official video primary",
            type=1,
            type_label="OpenAI",
            test_supported=True,
            status="enabled",
            configured_models=["video-v1"],
            test_model="video-v1",
            weight=100,
            priority=10,
            auto_ban=True,
            tag="official",
            created_at=now,
            last_tested_at=None,
            response_time_ms=None,
            credential=RelayChannelCredentialState(configured=True, key_count=2),
            revision=CHANNEL_REVISION,
        )
        self.operations: dict[str, RelayChannelOperation] = {}
        self.test_calls: list[dict] = []
        self.status_calls: list[dict] = []
        self.fail_after_test_commit_once = False
        self.fail_status_with_revision_conflict_once = False
        self.fail_get_channel_once = False
        self.pending_operation_reads = 0
        self.mismatched_operation_reads = 0
        self.mismatch_submitted_test_receipt_once = False

    def list_channels(self, *, page=1, page_size=50, status=None, request_id=None):
        data = [self.channel] if status in {None, self.channel.status} else []
        return RelayChannelPage(
            api_version="v1",
            schema_version=1,
            object="list",
            data=data,
            page=page,
            page_size=page_size,
            total=len(data),
        )

    def get_channel(self, channel_id, *, request_id=None):
        if self.fail_get_channel_once:
            self.fail_get_channel_once = False
            raise RelayTemporaryError(
                "preflight unavailable", submission_outcome_unknown=False
            )
        if channel_id != self.channel.id:
            raise RelayPermanentError("not found", response_status=404)
        return self.channel

    def get_channel_operation(self, channel_id, *, operation_id, request_id=None):
        if channel_id != self.channel.id or operation_id not in self.operations:
            raise RelayPermanentError("not found", response_status=404)
        operation = self.operations[operation_id]
        if self.pending_operation_reads > 0:
            self.pending_operation_reads -= 1
            return operation.model_copy(
                update={"state": "pending", "result": None, "completed_at": None}
            )
        if self.mismatched_operation_reads > 0:
            self.mismatched_operation_reads -= 1
            return operation.model_copy(
                update={"reason": f"{operation.reason} (mismatched readback)"}
            )
        return operation

    def test_channel(
        self,
        channel_id,
        *,
        operation_id,
        actor,
        reason,
        request_id=None,
    ):
        self.test_calls.append(
            {
                "channel_id": channel_id,
                "operation_id": operation_id,
                "actor": actor,
                "reason": reason,
                "request_id": request_id,
            }
        )
        now = datetime.now(timezone.utc)
        result = RelayChannelOperation(
            api_version="v1",
            schema_version=1,
            object="relay.channel_control_operation",
            operation_id=operation_id,
            tenant_id=TENANT_ID,
            channel_id=channel_id,
            kind="test",
            state="succeeded",
            actor=actor,
            reason=reason,
            request_id=request_id,
            intent_sha256="f" * 64,
            previous_revision=None,
            result_revision=None,
            result=RelayChannelTestResult(
                success=True, response_time_ms=413, error_code=None
            ),
            created_at=now,
            completed_at=now,
            idempotent_replay=False,
        )
        self.operations[operation_id] = result
        if self.fail_after_test_commit_once:
            self.fail_after_test_commit_once = False
            raise RelayTemporaryError("lost response", submission_outcome_unknown=True)
        if self.mismatch_submitted_test_receipt_once:
            self.mismatch_submitted_test_receipt_once = False
            return result.model_copy(update={"actor": "mismatched-platform-admin"})
        return result

    def set_channel_status(
        self,
        channel_id,
        *,
        operation_id,
        actor,
        reason,
        expected_revision,
        target_status,
        request_id=None,
    ):
        self.status_calls.append(
            {
                "channel_id": channel_id,
                "operation_id": operation_id,
                "actor": actor,
                "reason": reason,
                "expected_revision": expected_revision,
                "target_status": target_status,
                "request_id": request_id,
            }
        )
        now = datetime.now(timezone.utc)
        if self.fail_status_with_revision_conflict_once:
            self.fail_status_with_revision_conflict_once = False
            current_revision = "sha256:" + "9" * 64
            self.operations[operation_id] = RelayChannelOperation(
                api_version="v1",
                schema_version=1,
                object="relay.channel_control_operation",
                operation_id=operation_id,
                tenant_id=TENANT_ID,
                channel_id=channel_id,
                kind="status",
                state="failed",
                actor=actor,
                reason=reason,
                request_id=request_id,
                intent_sha256="b" * 64,
                previous_revision=current_revision,
                result_revision=current_revision,
                expected_revision=expected_revision,
                target_status=target_status,
                result=RelayChannelStatusResult(
                    previous_status=self.channel.status,
                    current_status=self.channel.status,
                    changed=False,
                    error_code="CHANNEL_REVISION_CONFLICT",
                ),
                created_at=now,
                completed_at=now,
                idempotent_replay=False,
            )
            raise RelayPermanentError(
                "revision conflict",
                relay_error=RelayErrorEnvelopeDetail(
                    code="CHANNEL_REVISION_CONFLICT",
                    message="Channel revision changed",
                    retryable=False,
                    details={},
                    request_id=request_id,
                ),
                response_status=409,
            )
        result = RelayChannelOperation(
            api_version="v1",
            schema_version=1,
            object="relay.channel_control_operation",
            operation_id=operation_id,
            tenant_id=TENANT_ID,
            channel_id=channel_id,
            kind="status",
            state="succeeded",
            actor=actor,
            reason=reason,
            request_id=request_id,
            intent_sha256="a" * 64,
            previous_revision=expected_revision,
            result_revision=CHANNEL_RESULT_REVISION,
            expected_revision=expected_revision,
            target_status=target_status,
            result=RelayChannelStatusResult(
                previous_status=self.channel.status,
                current_status=target_status,
                changed=self.channel.status != target_status,
            ),
            created_at=now,
            completed_at=now,
            idempotent_replay=False,
        )
        self.channel = self.channel.model_copy(
            update={"status": target_status, "revision": CHANNEL_RESULT_REVISION}
        )
        self.operations[operation_id] = result
        return result


def _admin(app) -> str:
    with app.state.session_factory.begin() as session:
        user = User(
            email=f"relay-channel-admin-{uuid4()}@example.com",
            display_name="Relay Channel Admin",
            is_platform_admin=True,
        )
        session.add(user)
        session.flush()
        return user.id


def _headers(admin_id: str, request_id: str) -> dict[str, str]:
    return {
        "X-Platform-Admin-User-ID": admin_id,
        "X-Request-ID": request_id,
    }


def _test_body() -> dict:
    return {
        "operation_id": TEST_OPERATION_ID,
        "reason": "Verify the official channel before enabling traffic",
        "approved": True,
    }


def _status_body(*, revision: str = CHANNEL_REVISION) -> dict:
    return {
        "operation_id": STATUS_OPERATION_ID,
        "reason": "Disable the channel while provider credentials are reviewed",
        "approved": True,
        "expected_revision": revision,
        "target_status": "manually_disabled",
    }


def test_platform_admin_reads_tests_and_audits_secret_free_channels(
    client, app
) -> None:
    relay = FakeChannelRelay()
    app.state.relay_operations_client = relay
    app.state.settings.relay_tenant_id = str(TENANT_ID)
    admin_id = _admin(app)
    headers = _headers(admin_id, "platform-channel-test-request")

    listed = client.get(
        "/api/v1/platform-admin/relay/channels?status=enabled&page_size=25",
        headers=headers,
    )
    detail = client.get(
        f"/api/v1/platform-admin/relay/channels/{CHANNEL_ID}", headers=headers
    )
    tested = client.post(
        f"/api/v1/platform-admin/relay/channels/{CHANNEL_ID}/test",
        headers=headers,
        json=_test_body(),
    )
    receipt = client.get(
        "/api/v1/platform-admin/relay/channels/"
        f"{CHANNEL_ID}/operations/{TEST_OPERATION_ID}",
        headers=_headers(admin_id, "platform-channel-test-readback"),
    )

    assert listed.status_code == 200, listed.text
    assert detail.status_code == 200, detail.text
    assert tested.status_code == 200, tested.text
    assert receipt.status_code == 200, receipt.text
    for response in (listed, detail, tested, receipt):
        assert response.headers["cache-control"] == "private, no-store"
        for forbidden in ("base_url", "settings", "header", "proxy", '"key"'):
            assert forbidden not in response.text
    assert listed.json()["total"] == 1
    assert detail.json()["credential"] == {"configured": True, "key_count": 2}
    assert tested.json()["kind"] == "test"
    assert receipt.json()["operation_id"] == TEST_OPERATION_ID
    assert len(relay.test_calls) == 1
    assert relay.test_calls[0]["actor"] == admin_id
    assert relay.test_calls[0]["request_id"] == "platform-channel-test-request"

    with app.state.session_factory() as session:
        journal = session.query(RelayChannelOperationJournal).one()
        assert journal.tenant_id == str(TENANT_ID)
        assert journal.operation_id == TEST_OPERATION_ID
        assert journal.state == "completed"
        assert journal.relay_receipt["operation_id"] == TEST_OPERATION_ID
        audits = (
            session.query(AuditLog)
            .filter(
                AuditLog.target_type == "relay_channel",
                AuditLog.target_id == str(CHANNEL_ID),
            )
            .order_by(AuditLog.created_at, AuditLog.id)
            .all()
        )
        assert [row.action for row in audits] == [
            "relay.channel.test.approve",
            "relay.channel.test",
        ]
        assert audits[0].after_summary["operation_id"] == TEST_OPERATION_ID
        assert audits[1].after_summary["intent_sha256"] == "f" * 64
        serialized = str(
            [(row.before_summary, row.after_summary) for row in audits]
        ).casefold()
        for forbidden in ("base_url", "settings", "header", "proxy", "secret"):
            assert forbidden not in serialized


def test_ambiguous_channel_test_is_read_back_and_never_reposted(client, app) -> None:
    relay = FakeChannelRelay()
    relay.fail_after_test_commit_once = True
    relay.pending_operation_reads = 1
    app.state.relay_operations_client = relay
    app.state.settings.relay_tenant_id = str(TENANT_ID)
    admin_id = _admin(app)
    path = f"/api/v1/platform-admin/relay/channels/{CHANNEL_ID}/test"

    first = client.post(
        path,
        headers=_headers(admin_id, "platform-channel-test-lost"),
        json=_test_body(),
    )
    assert first.status_code == 503
    assert first.json()["detail"]["code"] == (
        "RELAY_CHANNEL_OPERATION_OUTCOME_UNKNOWN"
    )
    assert len(relay.test_calls) == 1

    pending = client.get(
        "/api/v1/platform-admin/relay/channels/"
        f"{CHANNEL_ID}/operations/{TEST_OPERATION_ID}",
        headers=_headers(admin_id, "platform-channel-test-lost-readback"),
    )
    assert pending.status_code == 200, pending.text
    assert pending.json()["state"] == "pending"
    with app.state.session_factory() as session:
        journal = session.query(RelayChannelOperationJournal).one()
        assert journal.state == "approved"
        assert journal.relay_receipt["state"] == "pending"
        assert journal.result_audit_id is None

    readback = client.get(
        "/api/v1/platform-admin/relay/channels/"
        f"{CHANNEL_ID}/operations/{TEST_OPERATION_ID}",
        headers=_headers(admin_id, "platform-channel-test-terminal-readback"),
    )
    assert readback.status_code == 200, readback.text
    assert readback.json()["operation_id"] == TEST_OPERATION_ID
    assert readback.json()["state"] == "succeeded"

    replay = client.post(
        path,
        headers=_headers(admin_id, "platform-channel-test-replay"),
        json=_test_body(),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["operation_id"] == TEST_OPERATION_ID
    assert len(relay.test_calls) == 1

    with app.state.session_factory() as session:
        audits = (
            session.query(AuditLog)
            .filter(AuditLog.target_id == str(CHANNEL_ID))
            .order_by(AuditLog.created_at, AuditLog.id)
            .all()
        )
        assert [row.action for row in audits] == [
            "relay.channel.test.approve",
            "relay.channel.test",
        ]
        journal = session.query(RelayChannelOperationJournal).one()
        assert journal.state == "completed"
        assert journal.result_audit_id == audits[1].id


def test_claimed_channel_test_receipt_mismatch_is_outcome_unknown_and_replays(
    client, app
) -> None:
    relay = FakeChannelRelay()
    relay.mismatch_submitted_test_receipt_once = True
    app.state.relay_operations_client = relay
    app.state.settings.relay_tenant_id = str(TENANT_ID)
    admin_id = _admin(app)
    path = f"/api/v1/platform-admin/relay/channels/{CHANNEL_ID}/test"

    mismatched = client.post(
        path,
        headers=_headers(admin_id, "platform-channel-test-receipt-mismatch"),
        json=_test_body(),
    )
    assert mismatched.status_code == 503, mismatched.text
    assert mismatched.json()["detail"]["code"] == (
        "RELAY_CHANNEL_OPERATION_OUTCOME_UNKNOWN"
    )
    assert len(relay.test_calls) == 1
    with app.state.session_factory() as session:
        journal = session.query(RelayChannelOperationJournal).one()
        assert journal.state == "approved"
        assert journal.result_audit_id is None

    replay = client.post(
        path,
        headers=_headers(admin_id, "platform-channel-test-receipt-replay"),
        json=_test_body(),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["state"] == "succeeded"
    assert len(relay.test_calls) == 1


def test_claimed_channel_test_readback_mismatch_is_outcome_unknown_and_replays(
    client, app
) -> None:
    relay = FakeChannelRelay()
    relay.mismatched_operation_reads = 1
    app.state.relay_operations_client = relay
    app.state.settings.relay_tenant_id = str(TENANT_ID)
    admin_id = _admin(app)
    path = f"/api/v1/platform-admin/relay/channels/{CHANNEL_ID}/test"

    mismatched = client.post(
        path,
        headers=_headers(admin_id, "platform-channel-test-readback-mismatch"),
        json=_test_body(),
    )
    assert mismatched.status_code == 503, mismatched.text
    assert mismatched.json()["detail"]["code"] == (
        "RELAY_CHANNEL_OPERATION_OUTCOME_UNKNOWN"
    )
    assert len(relay.test_calls) == 1

    replay = client.post(
        path,
        headers=_headers(admin_id, "platform-channel-test-readback-replay"),
        json=_test_body(),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["state"] == "succeeded"
    assert len(relay.test_calls) == 1


def test_claimed_channel_get_readback_mismatch_is_outcome_unknown_and_recovers(
    client, app
) -> None:
    relay = FakeChannelRelay()
    relay.fail_after_test_commit_once = True
    app.state.relay_operations_client = relay
    app.state.settings.relay_tenant_id = str(TENANT_ID)
    admin_id = _admin(app)
    post_path = f"/api/v1/platform-admin/relay/channels/{CHANNEL_ID}/test"
    get_path = (
        "/api/v1/platform-admin/relay/channels/"
        f"{CHANNEL_ID}/operations/{TEST_OPERATION_ID}"
    )

    submitted = client.post(
        post_path,
        headers=_headers(admin_id, "platform-channel-test-get-mismatch-submit"),
        json=_test_body(),
    )
    assert submitted.status_code == 503, submitted.text
    relay.mismatched_operation_reads = 1

    mismatched = client.get(
        get_path,
        headers=_headers(admin_id, "platform-channel-test-get-mismatch"),
    )
    assert mismatched.status_code == 503, mismatched.text
    assert mismatched.json()["detail"]["code"] == (
        "RELAY_CHANNEL_OPERATION_OUTCOME_UNKNOWN"
    )
    assert len(relay.test_calls) == 1

    recovered = client.get(
        get_path,
        headers=_headers(admin_id, "platform-channel-test-get-recovered"),
    )
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["state"] == "succeeded"
    assert len(relay.test_calls) == 1


def test_channel_status_requires_current_revision_and_replays_receipt(
    client, app
) -> None:
    relay = FakeChannelRelay()
    app.state.relay_operations_client = relay
    app.state.settings.relay_tenant_id = str(TENANT_ID)
    admin_id = _admin(app)
    path = f"/api/v1/platform-admin/relay/channels/{CHANNEL_ID}/status"

    stale = client.post(
        path,
        headers=_headers(admin_id, "platform-channel-status-stale"),
        json=_status_body(revision="sha256:" + "0" * 64),
    )
    assert stale.status_code == 409
    assert relay.status_calls == []

    changed = client.post(
        path,
        headers=_headers(admin_id, "platform-channel-status-change"),
        json=_status_body(),
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["result"] == {
        "previous_status": "enabled",
        "current_status": "manually_disabled",
        "changed": True,
        "error_code": None,
    }
    assert len(relay.status_calls) == 1

    replay = client.post(
        path,
        headers=_headers(admin_id, "platform-channel-status-replay"),
        json=_status_body(),
    )
    assert replay.status_code == 200, replay.text
    assert len(relay.status_calls) == 1

    with app.state.session_factory() as session:
        audits = (
            session.query(AuditLog)
            .filter(AuditLog.target_id == str(CHANNEL_ID))
            .order_by(AuditLog.created_at, AuditLog.id)
            .all()
        )
        assert [row.action for row in audits] == [
            "relay.channel.status_change.approve",
            "relay.channel.status_change",
        ]
        assert audits[0].before_summary["revision"] == CHANNEL_REVISION
        assert audits[1].after_summary["result_revision"] == (CHANNEL_RESULT_REVISION)


def test_channel_status_cas_race_returns_and_audits_failed_receipt(client, app) -> None:
    relay = FakeChannelRelay()
    relay.fail_status_with_revision_conflict_once = True
    app.state.relay_operations_client = relay
    app.state.settings.relay_tenant_id = str(TENANT_ID)
    admin_id = _admin(app)
    path = f"/api/v1/platform-admin/relay/channels/{CHANNEL_ID}/status"

    conflicted = client.post(
        path,
        headers=_headers(admin_id, "platform-channel-status-race"),
        json=_status_body(),
    )
    assert conflicted.status_code == 200, conflicted.text
    assert conflicted.headers["cache-control"] == "private, no-store"
    assert conflicted.json()["state"] == "failed"
    assert conflicted.json()["result"] == {
        "previous_status": "enabled",
        "current_status": "enabled",
        "changed": False,
        "error_code": "CHANNEL_REVISION_CONFLICT",
    }
    assert len(relay.status_calls) == 1

    replay = client.post(
        path,
        headers=_headers(admin_id, "platform-channel-status-race-replay"),
        json=_status_body(),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["operation_id"] == STATUS_OPERATION_ID
    assert len(relay.status_calls) == 1

    with app.state.session_factory() as session:
        audits = (
            session.query(AuditLog)
            .filter(AuditLog.target_id == str(CHANNEL_ID))
            .order_by(AuditLog.created_at, AuditLog.id)
            .all()
        )
        assert [row.action for row in audits] == [
            "relay.channel.status_change.approve",
            "relay.channel.status_change",
        ]
        assert audits[1].after_summary["state"] == "failed"
        assert audits[1].after_summary["result"]["error_code"] == (
            "CHANNEL_REVISION_CONFLICT"
        )


def test_channel_facade_rejects_unapproved_or_expanded_control_input(
    client, app
) -> None:
    relay = FakeChannelRelay()
    app.state.relay_operations_client = relay
    app.state.settings.relay_tenant_id = str(TENANT_ID)
    admin_id = _admin(app)
    path = f"/api/v1/platform-admin/relay/channels/{CHANNEL_ID}/test"

    assert client.get("/api/v1/platform-admin/relay/channels").status_code == 401
    unapproved = client.post(
        path,
        headers=_headers(admin_id, "platform-channel-unapproved"),
        json={**_test_body(), "approved": False},
    )
    assert unapproved.status_code == 422
    expanded = client.post(
        path,
        headers=_headers(admin_id, "platform-channel-expanded"),
        json={**_test_body(), "model": "attacker-controlled-model"},
    )
    assert expanded.status_code == 422
    auto_disable = client.post(
        f"/api/v1/platform-admin/relay/channels/{CHANNEL_ID}/status",
        headers=_headers(admin_id, "platform-channel-auto-disable"),
        json={**_status_body(), "target_status": "auto_disabled"},
    )
    assert auto_disable.status_code == 422
    assert relay.test_calls == [] and relay.status_calls == []


def test_channel_operation_id_is_global_across_operation_kinds(client, app) -> None:
    relay = FakeChannelRelay()
    app.state.relay_operations_client = relay
    app.state.settings.relay_tenant_id = str(TENANT_ID)
    admin_id = _admin(app)

    tested = client.post(
        f"/api/v1/platform-admin/relay/channels/{CHANNEL_ID}/test",
        headers=_headers(admin_id, "platform-channel-global-operation-test"),
        json=_test_body(),
    )
    assert tested.status_code == 200, tested.text

    conflicted = client.post(
        f"/api/v1/platform-admin/relay/channels/{CHANNEL_ID}/status",
        headers=_headers(admin_id, "platform-channel-global-operation-status"),
        json={**_status_body(), "operation_id": TEST_OPERATION_ID},
    )
    assert conflicted.status_code == 409, conflicted.text
    assert len(relay.test_calls) == 1
    assert relay.status_calls == []
    with app.state.session_factory() as session:
        assert session.query(RelayChannelOperationJournal).count() == 1
        approvals = (
            session.query(AuditLog)
            .filter(AuditLog.action.like("relay.channel.%.approve"))
            .all()
        )
        assert len(approvals) == 1


def test_unsupported_channel_test_fails_before_approval_or_relay_post(
    client, app
) -> None:
    relay = FakeChannelRelay()
    relay.channel = relay.channel.model_copy(update={"test_supported": False})
    app.state.relay_operations_client = relay
    app.state.settings.relay_tenant_id = str(TENANT_ID)
    admin_id = _admin(app)

    response = client.post(
        f"/api/v1/platform-admin/relay/channels/{CHANNEL_ID}/test",
        headers=_headers(admin_id, "platform-channel-test-unsupported"),
        json=_test_body(),
    )
    assert response.status_code == 409, response.text
    assert relay.test_calls == []
    with app.state.session_factory() as session:
        assert session.query(RelayChannelOperationJournal).count() == 0
        assert (
            session.query(AuditLog)
            .filter(AuditLog.action == "relay.channel.test.approve")
            .count()
            == 0
        )


def test_channel_preflight_outage_is_machine_readable_and_never_claimed(
    client, app
) -> None:
    relay = FakeChannelRelay()
    relay.fail_get_channel_once = True
    app.state.relay_operations_client = relay
    app.state.settings.relay_tenant_id = str(TENANT_ID)
    admin_id = _admin(app)

    response = client.post(
        f"/api/v1/platform-admin/relay/channels/{CHANNEL_ID}/test",
        headers=_headers(admin_id, "platform-channel-preflight-outage"),
        json=_test_body(),
    )
    assert response.status_code == 503, response.text
    assert response.json()["detail"] == {
        "code": "RELAY_CHANNEL_OPERATION_NOT_STARTED",
        "message": "Relay channel preflight failed; no operation was approved or submitted",
    }
    assert relay.test_calls == []
    with app.state.session_factory() as session:
        assert session.query(RelayChannelOperationJournal).count() == 0
        assert (
            session.query(AuditLog)
            .filter(AuditLog.action == "relay.channel.test.approve")
            .count()
            == 0
        )
