from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select

from platform_api.models import (
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    RelayOutboxStatus,
    RelaySubmissionOutbox,
    TaskStatus,
    WalletAccount,
    utcnow,
)
from platform_api.relay_client import (
    HttpxRelayClient,
    RelayArtifact,
    RelayAccepted,
    RelayErrorDetail,
    RelayPermanentError,
    RelayJobSnapshot,
    RelayOutput,
    RelaySignedDownload,
    RelayTemporaryError,
)
from platform_api.relay_sync_worker import RelayStatusPoller
from platform_api.services.relay_outbox import RelayOutboxDispatcher

from .conftest import bootstrap
from .test_wallet_and_tasks import seed_model


REVISION = "sha256:" + ("1" * 64)
CONTRACT_TIME = datetime(2030, 1, 1, tzinfo=timezone.utc)


def relay_action(status: str) -> str:
    if status == "succeeded":
        return "settle"
    if status in {"failed", "cancelled"}:
        return "release"
    return "hold"


def accepted_response(job_id: str, *, status: str = "queued") -> RelayAccepted:
    return RelayAccepted(
        api_version="v1",
        schema_version=1,
        object="generation",
        id=job_id,
        job_id=job_id,
        status=status,
        expected_capability_revision=REVISION,
        capability_revision=REVISION,
        reservation_action=relay_action(status),
        idempotent_replay=False,
        created_at=CONTRACT_TIME,
    )


def job_snapshot(
    *,
    task_id: str,
    job_id: str,
    status: str,
    outputs: list[RelayArtifact] | None = None,
    error: dict | None = None,
    output_count: int = 1,
) -> RelayJobSnapshot:
    return RelayJobSnapshot(
        api_version="v1",
        schema_version=1,
        object="generation",
        id=job_id,
        client_reference_id=task_id,
        model="video-pro",
        expected_capability_revision=REVISION,
        capability_revision=REVISION,
        mode="text_to_video",
        inputs={"prompt": "contract test", "assets": []},
        output={"count": output_count},
        metadata={},
        status=status,
        reservation_action=relay_action(status),
        progress=100 if status == "succeeded" else 50,
        outputs=outputs or [],
        error=error,
        created_at=CONTRACT_TIME,
        updated_at=CONTRACT_TIME,
    )


class ScriptedRelayClient:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def submit(self, payload, *, idempotency_key, request_id=None):
        self.calls.append((payload, idempotency_key, request_id))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RotatingAssetResolver:
    def __init__(self):
        self.calls = 0

    def resolve(self, *, company_id, references):
        self.calls += 1
        return [
            {
                "url": (
                    "http://platform-internal:8000/private/input"
                    f"?signature={self.calls}"
                ),
                "media_type": reference["media_type"],
            }
            for reference in references
        ]


def recharge_and_create(
    app,
    client,
    tenant,
    tenant_headers,
    *,
    id_suffix: str,
    recharge_cents: int = 1000,
):
    model_id = seed_model(app, tenant["company_id"])
    recharge = client.post(
        f"/api/v1/companies/{tenant['company_id']}/wallet/recharge",
        headers=tenant_headers,
        json={
            "amount_cents": recharge_cents,
            "idempotency_key": f"relay-recharge-{id_suffix}",
        },
    )
    assert recharge.status_code == 200
    task = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": f"relay-reserve-{id_suffix}",
            "request_payload": {
                "prompt": "可靠提交测试",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
            },
        },
    )
    assert task.status_code == 201, task.text
    return task.json()


def test_task_reserve_and_outbox_are_committed_together(
    app, client, tenant, tenant_headers
):
    task = recharge_and_create(
        app,
        client,
        tenant,
        tenant_headers,
        id_suffix="atomic",
    )
    with app.state.session_factory() as session:
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task["id"]
            )
        )
        wallet = session.get(WalletAccount, tenant["company_id"])
        assert outbox is not None
        assert outbox.status == RelayOutboxStatus.PENDING
        assert outbox.relay_payload["client_reference_id"] == task["id"]
        assert outbox.relay_payload["inputs"]["prompt"] == "可靠提交测试"
        assert wallet.available_cents == 600
        assert wallet.reserved_cents == 400
    assert "api_key" not in str(task).lower()


def test_task_creation_replay_returns_original_without_duplicate_side_effects(
    app, client, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    model_id = seed_model(app, company_id)
    client.post(
        f"/api/v1/companies/{company_id}/wallet/recharge",
        headers=tenant_headers,
        json={
            "amount_cents": 1000,
            "idempotency_key": "replay-recharge",
        },
    )
    request_body = {
        "model_id": model_id,
        "idempotency_key": "task-replay-key",
        "request_payload": {
            "prompt": "same request",
            "duration_seconds": 5,
            "metadata": {"campaign": "launch", "version": 1},
        },
    }

    first = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        json=request_body,
    )
    repeated = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        json={
            **request_body,
            "request_payload": {
                "metadata": {"version": 1, "campaign": "launch"},
                "duration_seconds": 5,
                "prompt": "same request",
            },
        },
    )

    assert first.status_code == 201, first.text
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["id"] == first.json()["id"]
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(GenerationTask.id))) == 1
        assert session.scalar(select(func.count(RelaySubmissionOutbox.id))) == 1
        assert (
            session.scalar(
                select(func.count(LedgerEntry.id)).where(
                    LedgerEntry.kind == LedgerKind.RESERVE
                )
            )
            == 1
        )
        wallet = session.get(WalletAccount, company_id)
        assert wallet.available_cents == 600
        assert wallet.reserved_cents == 400


def test_task_creation_rejects_same_key_for_different_request(
    app, client, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    model_id = seed_model(app, company_id)
    client.post(
        f"/api/v1/companies/{company_id}/wallet/recharge",
        headers=tenant_headers,
        json={
            "amount_cents": 1000,
            "idempotency_key": "conflict-recharge",
        },
    )
    base = {
        "model_id": model_id,
        "idempotency_key": "task-conflict-key",
        "request_payload": {
            "prompt": "first request",
            "duration_seconds": 5,
        },
    }
    assert (
        client.post(
            f"/api/v1/companies/{company_id}/tasks",
            headers=tenant_headers,
            json=base,
        ).status_code
        == 201
    )

    conflict = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        json={
            **base,
            "request_payload": {
                "prompt": "different request",
                "duration_seconds": 5,
            },
        },
    )

    assert conflict.status_code == 409
    assert conflict.json()["code"] == "conflict"
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(GenerationTask.id))) == 1
        assert session.scalar(select(func.count(RelaySubmissionOutbox.id))) == 1


def test_insufficient_balance_rolls_back_task_and_outbox(
    app, client, tenant, tenant_headers
):
    model_id = seed_model(app, tenant["company_id"])
    response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "relay-insufficient",
            "request_payload": {"prompt": "no funds", "duration_seconds": 5},
        },
    )
    assert response.status_code == 409
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(GenerationTask.id))) == 0
        assert session.scalar(select(func.count(RelaySubmissionOutbox.id))) == 0


def test_httpx_relay_client_sends_server_credentials_and_idempotency():
    captured = {}

    def handler(request: httpx.Request):
        captured["headers"] = request.headers
        captured["json"] = request.content
        return httpx.Response(
            202,
            json={
                "api_version": "v1",
                "schema_version": 1,
                "object": "generation",
                "id": "33333333-3333-3333-3333-333333333333",
                "job_id": "33333333-3333-3333-3333-333333333333",
                "status": "queued",
                "expected_capability_revision": REVISION,
                "capability_revision": REVISION,
                "reservation_action": "hold",
                "idempotent_replay": False,
                "created_at": CONTRACT_TIME.isoformat(),
            },
        )

    client = HttpxRelayClient(
        base_url="https://relay.example.test",
        client_id="platform-client",
        api_key="server-secret",
        transport=httpx.MockTransport(handler),
    )
    from platform_api.relay_client import RelayGenerationRequest

    accepted = client.submit(
        RelayGenerationRequest(
            client_reference_id="task-1",
            model="video-pro",
            expected_capability_revision=REVISION,
            mode="text_to_video",
            inputs={"prompt": "test", "assets": []},
            output={"duration_seconds": 5},
            metadata={},
        ),
        idempotency_key="stable-task-key",
    )
    assert accepted.job_id == "33333333-3333-3333-3333-333333333333"
    assert captured["headers"]["x-client-id"] == "platform-client"
    assert captured["headers"]["x-api-key"] == "server-secret"
    assert captured["headers"]["idempotency-key"] == "stable-task-key"
    assert captured["headers"]["x-request-id"] == "platform-submit-task-1"


def test_relay_accepted_requires_identical_ids_and_capability_revisions():
    payload = accepted_response(
        "34343434-3434-4343-8343-343434343434"
    ).model_dump()
    payload["id"] = "35353535-3535-4353-8353-353535353535"
    with pytest.raises(ValueError, match="id and job_id must match"):
        RelayAccepted.model_validate(payload)

    payload["id"] = payload["job_id"]
    payload["capability_revision"] = "sha256:" + ("2" * 64)
    with pytest.raises(ValueError, match="capability revisions must match"):
        RelayAccepted.model_validate(payload)


def test_relay_contract_consumer_rejects_coerced_numbers_and_booleans():
    with pytest.raises(ValueError):
        RelayOutput.model_validate({"duration_seconds": "5"})
    with pytest.raises(ValueError):
        RelayOutput.model_validate({"count": "1"})
    with pytest.raises(ValueError):
        RelayOutput.model_validate({"face_enabled": "false"})

    accepted = accepted_response(
        "36363636-3636-4363-8363-363636363636"
    ).model_dump(mode="json")
    accepted["idempotent_replay"] = "false"
    with pytest.raises(ValueError):
        RelayAccepted.model_validate(accepted)

    snapshot = job_snapshot(
        task_id="task-strict-response",
        job_id="37373737-3737-4373-8373-373737373737",
        status="processing",
    ).model_dump(mode="json")
    snapshot["progress"] = "50"
    with pytest.raises(ValueError):
        RelayJobSnapshot.model_validate(snapshot)

    with pytest.raises(ValueError):
        RelayArtifact.model_validate(
            {
                "asset_id": "01010101-0101-4101-8101-010101010101",
                "object_key": "tenant/task/output.mp4",
                "media_type": "video",
                "content_type": "video/mp4",
                "size_bytes": "10",
                "sha256": "a" * 64,
            }
        )
    with pytest.raises(ValueError):
        RelayArtifact.model_validate(
            {
                "asset_id": "02020202-0202-4202-8202-020202020202",
                "object_key": "tenant/task/empty.mp4",
                "media_type": "video",
                "content_type": "video/mp4",
                "size_bytes": 0,
                "sha256": "a" * 64,
            }
        )
    with pytest.raises(ValueError):
        RelayArtifact.model_validate(
            {
                "asset_id": "not-a-uuid",
                "object_key": "tenant/task/output.mp4",
                "media_type": "video",
                "content_type": "video/mp4",
                "size_bytes": 10,
                "sha256": "a" * 64,
            }
        )
    with pytest.raises(ValueError):
        RelayErrorDetail.model_validate(
            {
                "code": "GENERATION_FAILED",
                "message": "failed",
                "retryable": "false",
            }
        )
    with pytest.raises(ValueError):
        RelaySignedDownload.model_validate(
            {
                "api_version": "v1",
                "schema_version": 1,
                "url": "https://assets.example.test/download",
                "expires_seconds": "300",
            }
        )


@pytest.mark.parametrize(
    ("status_code", "exception_type", "outcome_unknown"),
    [
        (422, RelayPermanentError, None),
        (429, RelayTemporaryError, False),
        (500, RelayTemporaryError, True),
    ],
)
def test_httpx_submit_parses_versioned_error_envelope_and_classifies_retry_safety(
    status_code, exception_type, outcome_unknown
):
    def handler(_request: httpx.Request):
        return httpx.Response(
            status_code,
            json={
                "api_version": "v1",
                "schema_version": 1,
                "error": {
                    "code": "REQUEST_VALIDATION_FAILED",
                    "message": "request rejected",
                    "retryable": status_code >= 429,
                    "details": {"field": "model"},
                    "request_id": "relay-contract-error",
                },
            },
        )

    relay_client = HttpxRelayClient(
        base_url="https://relay.example.test",
        client_id="platform-client",
        api_key="server-secret",
        transport=httpx.MockTransport(handler),
    )
    from platform_api.relay_client import RelayGenerationRequest

    request = RelayGenerationRequest(
        client_reference_id="task-error",
        model="video-pro",
        expected_capability_revision=REVISION,
        mode="text_to_video",
        inputs={"prompt": "test", "assets": []},
        output={"duration_seconds": 5},
        metadata={},
    )
    with pytest.raises(exception_type) as raised:
        relay_client.submit(request, idempotency_key="stable-error-key")

    error = raised.value
    assert error.relay_error is not None
    assert error.relay_error.request_id == "relay-contract-error"
    assert error.diagnostic_snapshot() == {
        "code": "REQUEST_VALIDATION_FAILED",
        "message": "request rejected",
        "retryable": status_code >= 429,
        "details": {"field": "model"},
        "request_id": "relay-contract-error",
        "http_status": status_code,
    }
    if outcome_unknown is not None:
        assert error.submission_outcome_unknown is outcome_unknown


def test_dispatch_is_idempotent_and_records_relay_job(
    app, client, tenant, tenant_headers, internal_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="dispatch"
    )
    fake = ScriptedRelayClient(
        accepted_response("44444444-4444-4444-4444-444444444444")
    )
    app.state.relay_client = fake
    first = client.post("/internal/relay/dispatch-once", headers=internal_headers)
    second = client.post("/internal/relay/dispatch-once", headers=internal_headers)
    assert first.status_code == 200
    assert first.json()["status"] == "sent"
    assert second.json()["processed"] is False
    assert len(fake.calls) == 1
    assert fake.calls[0][1] == f"platform-task-{task['id']}"
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        assert stored.relay_job_id == "44444444-4444-4444-4444-444444444444"


def test_temporary_failure_retries_without_releasing_balance(
    app, client, tenant, tenant_headers, internal_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="temporary"
    )
    fake = ScriptedRelayClient(
        RelayTemporaryError("timeout"),
        accepted_response("55555555-5555-5555-5555-555555555555"),
    )
    app.state.relay_client = fake
    first = client.post("/internal/relay/dispatch-once", headers=internal_headers)
    assert first.json()["status"] == "retry"
    with app.state.session_factory.begin() as session:
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task["id"]
            )
        )
        wallet = session.get(WalletAccount, tenant["company_id"])
        assert wallet.reserved_cents == 400
        outbox.next_attempt_at = utcnow() - timedelta(seconds=1)
    second = client.post("/internal/relay/dispatch-once", headers=internal_headers)
    assert second.json()["status"] == "sent"
    assert len(fake.calls) == 2


def test_private_asset_request_is_materialized_once_for_idempotent_retry(
    app, client, tenant, tenant_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="stable-private-url"
    )
    with app.state.session_factory.begin() as session:
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task["id"]
            )
        )
        relay_payload = dict(outbox.relay_payload)
        relay_payload["metadata"] = {
            **relay_payload["metadata"],
            "_platform_input_assets": [
                {"asset_id": "private-asset-1", "media_type": "image"}
            ],
        }
        relay_payload["inputs"] = {
            **relay_payload["inputs"],
            "assets": [],
        }
        outbox.relay_payload = relay_payload

    fake = ScriptedRelayClient(
        RelayTemporaryError("response lost"),
        accepted_response("56565656-5656-4565-8565-565656565656"),
    )
    resolver = RotatingAssetResolver()
    dispatcher = RelayOutboxDispatcher(
        app.state.session_factory,
        fake,
        asset_reference_resolver=resolver,
    )

    first = dispatcher.dispatch_once()
    assert first.status == "retry"
    with app.state.session_factory.begin() as session:
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task["id"]
            )
        )
        outbox.next_attempt_at = utcnow() - timedelta(seconds=1)
    second = dispatcher.dispatch_once()

    assert second.status == "sent"
    assert resolver.calls == 1
    assert len(fake.calls) == 2
    first_payload = fake.calls[0][0].model_dump(mode="json")
    second_payload = fake.calls[1][0].model_dump(mode="json")
    assert first_payload == second_payload
    assert first_payload["inputs"]["assets"][0]["url"].endswith(
        "signature=1"
    )
    assert "_platform_input_assets" not in first_payload["metadata"]
    with app.state.session_factory() as session:
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task["id"]
            )
        )
        assert outbox.materialized_relay_payload == first_payload


def test_stale_dispatch_worker_cannot_overwrite_newer_claim_or_release_balance(
    app, client, tenant, tenant_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="stale-dispatch"
    )
    dispatcher = RelayOutboxDispatcher(
        app.state.session_factory,
        ScriptedRelayClient(),
        stale_after_seconds=0,
    )

    first_claim = dispatcher._claim()
    second_claim = dispatcher._claim()
    assert first_claim is not None
    assert second_claim is not None
    assert first_claim.attempt_count == 1
    assert second_claim.attempt_count == 2

    stale_result = dispatcher._mark_permanent_failure(
        first_claim.id, first_claim.attempt_count, "stale worker failure"
    )
    assert stale_result.status == "processing"

    accepted = accepted_response("57575757-5757-4575-8575-575757575757")
    current_result = dispatcher._mark_sent(
        second_claim.id, second_claim.attempt_count, accepted
    )
    assert current_result.status == "sent"
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task["id"]
            )
        )
        wallet = session.get(WalletAccount, tenant["company_id"])
        assert stored.relay_job_id == accepted.job_id
        assert stored.reserved_cents == 400
        assert outbox.status == RelayOutboxStatus.SENT
        assert wallet.available_cents == 600
        assert wallet.reserved_cents == 400


def test_unknown_submit_outcome_at_attempt_limit_requires_reconciliation(
    app, client, tenant, tenant_headers, internal_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="attempt-limit"
    )
    app.state.settings.relay_dispatch_max_attempts = 2
    app.state.relay_client = ScriptedRelayClient(
        RelayTemporaryError("first timeout"),
        RelayTemporaryError("second timeout"),
    )

    first = client.post("/internal/relay/dispatch-once", headers=internal_headers)
    assert first.status_code == 200
    assert first.json()["status"] == "retry"
    with app.state.session_factory.begin() as session:
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task["id"]
            )
        )
        outbox.next_attempt_at = utcnow() - timedelta(seconds=1)

    exhausted = client.post(
        "/internal/relay/dispatch-once", headers=internal_headers
    )
    no_more_work = client.post(
        "/internal/relay/dispatch-once", headers=internal_headers
    )

    assert exhausted.status_code == 200
    assert exhausted.json()["status"] == "reconciliation_required"
    assert no_more_work.json()["processed"] is False
    assert len(app.state.relay_client.calls) == 2
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task["id"]
            )
        )
        wallet = session.get(WalletAccount, tenant["company_id"])
        assert stored.status == TaskStatus.PROCESSING
        assert stored.reserved_cents == 400
        assert stored.failure_reason is None
        assert outbox.status == RelayOutboxStatus.RECONCILIATION_REQUIRED
        assert outbox.attempt_count == 2
        assert outbox.submission_outcome_uncertain_at is not None
        assert "attempt limit (2) exhausted" in outbox.last_error
        assert wallet.available_cents == 600
        assert wallet.reserved_cents == 400


def test_permanent_relay_rejection_releases_reserved_balance(
    app, client, tenant, tenant_headers, internal_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="permanent"
    )
    app.state.relay_client = ScriptedRelayClient(
        RelayPermanentError("HTTP 422")
    )
    response = client.post(
        "/internal/relay/dispatch-once", headers=internal_headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "permanently_failed"
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        wallet = session.get(WalletAccount, tenant["company_id"])
        assert stored.status == TaskStatus.FAILED
        assert wallet.available_cents == 1000
        assert wallet.reserved_cents == 0


def test_internal_auth_terminal_idempotency_and_cross_tenant_filtering(
    app, client, tenant, tenant_headers, internal_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="terminal"
    )
    relay_job_id = "66666666-6666-6666-6666-666666666666"
    with app.state.session_factory.begin() as session:
        stored = session.get(GenerationTask, task["id"])
        stored.relay_job_id = relay_job_id

    unauthorized = client.post("/internal/relay/dispatch-once")
    assert unauthorized.status_code == 401
    ordinary_user_terminal = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks/{task['id']}/settle",
        headers=tenant_headers,
        json={"actual_cost_cents": 1, "idempotency_key": "forbidden"},
    )
    assert ordinary_user_terminal.status_code == 404

    other = bootstrap(client, "relay-other")
    cross_tenant = client.post(
        "/internal/relay/status",
        headers=internal_headers,
        json={
            "company_id": other["company_id"],
            "task_id": task["id"],
            "relay_job_id": relay_job_id,
            "status": "succeeded",
        },
    )
    assert cross_tenant.status_code == 404

    body = {
        "company_id": tenant["company_id"],
        "task_id": task["id"],
        "relay_job_id": relay_job_id,
        "status": "succeeded",
        "outputs": [
            {
                "asset_id": "11111111-1111-4111-8111-111111111111",
                "object_key": f"outputs/{relay_job_id}/terminal-output",
                "media_type": "video",
                "content_type": "video/mp4",
                "size_bytes": 1234,
                "sha256": "c" * 64,
            }
        ],
    }
    first = client.post("/internal/relay/status", headers=internal_headers, json=body)
    repeated = client.post(
        "/internal/relay/status", headers=internal_headers, json=body
    )
    replacement_body = {
        **body,
        "outputs": [
            {
                **body["outputs"][0],
                "asset_id": "22222222-2222-4222-8222-222222222222",
                "object_key": f"outputs/{relay_job_id}/replacement-output",
            }
        ],
    }
    replacement = client.post(
        "/internal/relay/status",
        headers=internal_headers,
        json=replacement_body,
    )
    assert first.status_code == 200
    assert repeated.status_code == 200
    assert replacement.status_code == 409
    with app.state.session_factory() as session:
        wallet = session.get(WalletAccount, tenant["company_id"])
        stored = session.get(GenerationTask, task["id"])
        assert wallet.available_cents == 600
        assert wallet.reserved_cents == 0
        assert stored.output_artifacts == [
            RelayArtifact.model_validate(body["outputs"][0]).safe_metadata()
        ]


def test_reconciliation_required_maps_to_processing_and_keeps_balance_reserved(
    app, client, tenant, tenant_headers, internal_headers
):
    task = recharge_and_create(
        app,
        client,
        tenant,
        tenant_headers,
        id_suffix="reconciliation-required",
    )
    relay_job_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    with app.state.session_factory.begin() as session:
        stored = session.get(GenerationTask, task["id"])
        assert stored is not None
        stored.relay_job_id = relay_job_id

    response = client.post(
        "/internal/relay/status",
        headers=internal_headers,
        json={
            "company_id": tenant["company_id"],
            "task_id": task["id"],
            "relay_job_id": relay_job_id,
            "status": "reconciliation_required",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "processing"
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        wallet = session.get(WalletAccount, tenant["company_id"])
        assert stored is not None and stored.status == TaskStatus.PROCESSING
        assert stored.actual_cost_cents is None
        assert stored.output_artifacts == []
        assert stored.reserved_cents == 400
        assert wallet is not None and wallet.available_cents == 600
        assert wallet.reserved_cents == 400
        assert session.scalar(
            select(func.count(LedgerEntry.id)).where(
                LedgerEntry.task_id == task["id"],
                LedgerEntry.kind.in_([LedgerKind.SETTLE, LedgerKind.RELEASE]),
            )
        ) == 0


def test_success_output_count_mismatch_keeps_balance_reserved(
    app, client, tenant, tenant_headers, internal_headers
):
    company_id = tenant["company_id"]
    model_id = seed_model(
        app,
        company_id,
        price_per_second_cents=None,
        price_per_item_cents=125,
        capability_config={"max_outputs": 4},
    )
    assert client.post(
        f"/api/v1/companies/{company_id}/wallet/recharge",
        headers=tenant_headers,
        json={
            "amount_cents": 1000,
            "idempotency_key": "delivery-count-recharge",
        },
    ).status_code == 200
    task_response = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "delivery-count-task",
            "request_payload": {
                "mode": "text_to_video",
                "prompt": "delivery count contract",
                "output_count": 2,
            },
        },
    )
    assert task_response.status_code == 201, task_response.text
    task = task_response.json()
    relay_job_id = "99999999-9999-4999-8999-999999999999"
    with app.state.session_factory.begin() as session:
        stored = session.get(GenerationTask, task["id"])
        assert stored is not None
        stored.relay_job_id = relay_job_id

    response = client.post(
        "/internal/relay/status",
        headers=internal_headers,
        json={
            "company_id": company_id,
            "task_id": task["id"],
            "relay_job_id": relay_job_id,
            "status": "succeeded",
            "outputs": [
                {
                    "asset_id": "33333333-3333-4333-8333-333333333333",
                    "object_key": f"outputs/{relay_job_id}/only-one-output",
                    "media_type": "video",
                    "content_type": "video/mp4",
                    "size_bytes": 1234,
                    "sha256": "e" * 64,
                }
            ],
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "conflict"
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        wallet = session.get(WalletAccount, company_id)
        assert stored is not None and stored.status == TaskStatus.QUEUED
        assert stored.actual_cost_cents is None
        assert stored.output_artifacts == []
        assert stored.reserved_cents == 250
        assert wallet is not None and wallet.available_cents == 750
        assert wallet.reserved_cents == 250
        assert session.scalar(
            select(func.count(LedgerEntry.id)).where(
                LedgerEntry.task_id == task["id"],
                LedgerEntry.kind == LedgerKind.SETTLE,
            )
        ) == 0


@pytest.mark.parametrize(
    ("mode", "unexpected_media_type"),
    [
        ("text_to_image", "video"),
        ("text_to_video", "image"),
    ],
)
def test_success_output_media_mismatch_keeps_balance_reserved(
    app,
    client,
    tenant,
    tenant_headers,
    internal_headers,
    mode,
    unexpected_media_type,
):
    task = recharge_and_create(
        app,
        client,
        tenant,
        tenant_headers,
        id_suffix=f"media-{mode}",
    )
    relay_job_id = (
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        if mode == "text_to_image"
        else "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    )
    with app.state.session_factory.begin() as session:
        stored = session.get(GenerationTask, task["id"])
        assert stored is not None
        stored.relay_job_id = relay_job_id
        stored.request_payload = {**stored.request_payload, "mode": mode}

    content_type = (
        "image/png" if unexpected_media_type == "image" else "video/mp4"
    )
    response = client.post(
        "/internal/relay/status",
        headers=internal_headers,
        json={
            "company_id": tenant["company_id"],
            "task_id": task["id"],
            "relay_job_id": relay_job_id,
            "status": "succeeded",
            "outputs": [
                {
                    "asset_id": "44444444-4444-4444-8444-444444444444",
                    "object_key": (
                        f"outputs/{relay_job_id}/wrong-{unexpected_media_type}"
                    ),
                    "media_type": unexpected_media_type,
                    "content_type": content_type,
                    "size_bytes": 1234,
                    "sha256": "f" * 64,
                }
            ],
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "conflict"
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        wallet = session.get(WalletAccount, tenant["company_id"])
        assert stored is not None and stored.status == TaskStatus.QUEUED
        assert stored.actual_cost_cents is None
        assert stored.output_artifacts == []
        assert stored.reserved_cents == 400
        assert wallet is not None and wallet.available_cents == 600
        assert wallet.reserved_cents == 400
        assert session.scalar(
            select(func.count(LedgerEntry.id)).where(
                LedgerEntry.task_id == task["id"],
                LedgerEntry.kind == LedgerKind.SETTLE,
            )
        ) == 0


def test_status_poller_applies_terminal_state_without_manual_endpoint(
    app, client, tenant, tenant_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="poller"
    )
    relay_job_id = "77777777-7777-7777-7777-777777777777"
    with app.state.session_factory.begin() as session:
        stored = session.get(GenerationTask, task["id"])
        stored.relay_job_id = relay_job_id

    class PollingRelayClient:
        def get(self, requested_job_id):
            assert requested_job_id == relay_job_id
            return job_snapshot(
                task_id=task["id"],
                job_id=relay_job_id,
                status="succeeded",
                outputs=[
                    RelayArtifact(
                        asset_id="55555555-5555-4555-8555-555555555555",
                        object_key=f"outputs/{relay_job_id}/poller-output",
                        media_type="video",
                        content_type="video/mp4",
                        size_bytes=1234,
                        sha256="d" * 64,
                    )
                ],
            )

    applied = RelayStatusPoller(
        app.state.session_factory, PollingRelayClient()
    ).poll_once()
    assert applied == 1
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        wallet = session.get(WalletAccount, tenant["company_id"])
        assert stored.status == TaskStatus.SUCCEEDED
        assert wallet.reserved_cents == 0


def test_status_poller_ignores_a_snapshot_for_a_different_relay_job(
    app, client, tenant, tenant_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="poller-mismatched-job"
    )
    relay_job_id = "77777777-7777-4777-8777-777777777771"
    mismatched_job_id = "77777777-7777-4777-8777-777777777772"
    with app.state.session_factory.begin() as session:
        stored = session.get(GenerationTask, task["id"])
        stored.relay_job_id = relay_job_id

    class MismatchedPollingRelayClient:
        def get(self, requested_job_id):
            assert requested_job_id == relay_job_id
            return job_snapshot(
                task_id=task["id"],
                job_id=mismatched_job_id,
                status="failed",
                error={
                    "code": "GENERATION_FAILED",
                    "message": "must not be applied",
                    "retryable": False,
                    "details": {},
                },
            )

    applied = RelayStatusPoller(
        app.state.session_factory, MismatchedPollingRelayClient()
    ).poll_once()

    assert applied == 0
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        wallet = session.get(WalletAccount, tenant["company_id"])
        assert stored.status == TaskStatus.QUEUED
        assert stored.failure_reason is None
        assert wallet.available_cents == 600
        assert wallet.reserved_cents == 400


def test_cancelled_relay_job_releases_balance_idempotently(
    app, client, tenant, tenant_headers, internal_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="cancelled"
    )
    relay_job_id = "88888888-8888-8888-8888-888888888888"
    with app.state.session_factory.begin() as session:
        stored = session.get(GenerationTask, task["id"])
        stored.relay_job_id = relay_job_id
    body = {
        "company_id": tenant["company_id"],
        "task_id": task["id"],
        "relay_job_id": relay_job_id,
        "status": "cancelled",
    }
    assert (
        client.post(
            "/internal/relay/status", headers=internal_headers, json=body
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/internal/relay/status", headers=internal_headers, json=body
        ).status_code
        == 200
    )
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        wallet = session.get(WalletAccount, tenant["company_id"])
        assert stored.status == TaskStatus.CANCELLED
        assert wallet.available_cents == 1000
        assert wallet.reserved_cents == 0
