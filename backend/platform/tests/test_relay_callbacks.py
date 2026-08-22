from __future__ import annotations

import hashlib
import hmac
import json
from uuid import uuid4

from sqlalchemy import func, select

from platform_api.models import (
    GenerationTask,
    RelayCallbackEvent,
    RelayOutboxStatus,
    RelaySubmissionOutbox,
    TaskStatus,
    WalletAccount,
    utcnow,
)
from platform_api.relay_identity import NEW_API_RELAY_BACKEND_ID
from platform_api.services.relay_callbacks import (
    RelayCallbackVerifier,
    RelayCallbackVerifierRegistry,
)

from .test_relay_boundary import recharge_and_create
from .test_wallet_and_tasks import seed_model


SECRET = "relay-callback-test-secret-with-at-least-32-bytes"
NOW = 2_000_000_000
REVISION = "sha256:" + ("1" * 64)


def reservation_action(status: str) -> str:
    if status == "succeeded":
        return "settle"
    if status in {"failed", "cancelled"}:
        return "release"
    return "hold"


def configure_callback(app) -> None:
    verifier = RelayCallbackVerifier(
        SECRET,
        max_age_seconds=300,
        clock=lambda: NOW,
    )
    app.state.relay_callback_verifier_registry = RelayCallbackVerifierRegistry(
        {NEW_API_RELAY_BACKEND_ID: verifier}
    )


def signed_callback(
    client,
    body: dict,
    *,
    secret: str = SECRET,
    timestamp: int = NOW,
    request_id: str = "relay-original-request-001",
):
    raw_body = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    event_id = body["event_id"]
    signing_input = f"{timestamp}.{event_id}.".encode("ascii") + raw_body
    signature = "v1=" + hmac.new(
        secret.encode("utf-8"), signing_input, hashlib.sha256
    ).hexdigest()
    return client.post(
        f"/internal/relay-callbacks/{NEW_API_RELAY_BACKEND_ID}",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Relay-Event-ID": event_id,
            "X-Relay-Timestamp": str(timestamp),
            "X-Relay-Signature": signature,
            "X-Request-ID": request_id,
        },
    )


def callback_body(
    *,
    task_id: str,
    relay_job_id: str,
    status: str,
    event_id: str | None = None,
    progress: int = 50,
    outputs: list[dict] | None = None,
    error: dict | None = None,
) -> dict:
    return {
        "api_version": "v1",
        "schema_version": 1,
        "event_id": event_id or str(uuid4()),
        "type": "generation.status_changed",
        "occurred_at": "2033-05-18T03:33:20Z",
        "job": {
            "api_version": "v1",
            "id": relay_job_id,
            "client_reference_id": task_id,
            "status": status,
            "expected_capability_revision": REVISION,
            "capability_revision": REVISION,
            "reservation_action": reservation_action(status),
            "progress": progress,
            "outputs": outputs or [],
            "error": error,
        },
    }


def prepared_task(app, client, tenant, tenant_headers, suffix: str):
    task = recharge_and_create(
        app,
        client,
        tenant,
        tenant_headers,
        id_suffix=suffix,
    )
    relay_job_id = str(uuid4())
    with app.state.session_factory.begin() as session:
        stored = session.get(GenerationTask, task["id"])
        assert stored is not None
        stored.relay_job_id = relay_job_id
    return task, relay_job_id


def test_signed_callback_binds_an_uncertain_submission_before_applying_status(
    app, client, tenant, tenant_headers
) -> None:
    configure_callback(app)
    task = recharge_and_create(
        app,
        client,
        tenant,
        tenant_headers,
        id_suffix="callback-bind-uncertain",
    )
    relay_job_id = str(uuid4())
    with app.state.session_factory.begin() as session:
        stored = session.get(GenerationTask, task["id"])
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task["id"]
            )
        )
        assert stored is not None and stored.relay_job_id is None
        assert outbox is not None and outbox.relay_job_id is None
        stored.status = TaskStatus.PROCESSING
        outbox.status = RelayOutboxStatus.RECONCILIATION_REQUIRED
        outbox.relay_submit_attempted_at = utcnow()
        outbox.submission_outcome_uncertain_at = utcnow()
        outbox.last_error = "Relay accepted the request but the response was lost"

    body = callback_body(
        task_id=task["id"],
        relay_job_id=relay_job_id,
        status="processing",
    )
    response = signed_callback(client, body)

    assert response.status_code == 204, response.text
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task["id"]
            )
        )
        assert stored is not None
        assert stored.relay_job_id == relay_job_id
        assert stored.status == TaskStatus.PROCESSING
        assert outbox is not None
        assert outbox.relay_job_id == relay_job_id
        assert outbox.status == RelayOutboxStatus.SENT
        assert outbox.last_error is None
        assert session.get(RelayCallbackEvent, body["event_id"]) is not None


def test_signed_callback_will_not_bind_with_a_corrupt_outbox_identity(
    app, client, tenant, tenant_headers
) -> None:
    configure_callback(app)
    task = recharge_and_create(
        app,
        client,
        tenant,
        tenant_headers,
        id_suffix="callback-bind-corrupt-outbox",
    )
    relay_job_id = str(uuid4())
    with app.state.session_factory.begin() as session:
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task["id"]
            )
        )
        assert outbox is not None
        outbox.status = RelayOutboxStatus.RECONCILIATION_REQUIRED
        outbox.relay_submit_attempted_at = utcnow()
        outbox.idempotency_key = "platform-task-for-a-different-task"

    body = callback_body(
        task_id=task["id"],
        relay_job_id=relay_job_id,
        status="processing",
    )
    response = signed_callback(client, body)

    assert response.status_code == 404
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        assert stored is not None and stored.relay_job_id is None
        assert session.get(RelayCallbackEvent, body["event_id"]) is None


def test_signed_callback_will_not_replace_an_outbox_relay_job_identity(
    app, client, tenant, tenant_headers
) -> None:
    configure_callback(app)
    task = recharge_and_create(
        app,
        client,
        tenant,
        tenant_headers,
        id_suffix="callback-bind-conflicting-job",
    )
    relay_job_id = str(uuid4())
    existing_relay_job_id = str(uuid4())
    with app.state.session_factory.begin() as session:
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task["id"]
            )
        )
        assert outbox is not None
        outbox.status = RelayOutboxStatus.RECONCILIATION_REQUIRED
        outbox.relay_submit_attempted_at = utcnow()
        outbox.relay_job_id = existing_relay_job_id

    body = callback_body(
        task_id=task["id"],
        relay_job_id=relay_job_id,
        status="processing",
    )
    response = signed_callback(client, body)

    assert response.status_code == 404
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task["id"]
            )
        )
        assert stored is not None and stored.relay_job_id is None
        assert outbox is not None
        assert outbox.relay_job_id == existing_relay_job_id
        assert session.get(RelayCallbackEvent, body["event_id"]) is None


def test_signed_processing_callback_is_applied_and_replay_is_idempotent(
    app, client, tenant, tenant_headers, internal_headers
) -> None:
    configure_callback(app)
    task, relay_job_id = prepared_task(
        app, client, tenant, tenant_headers, "callback-processing"
    )
    body = callback_body(
        task_id=task["id"],
        relay_job_id=relay_job_id,
        status="processing",
    )

    first = signed_callback(client, body)
    repeated = signed_callback(client, body)

    assert first.status_code == 204, first.text
    assert first.headers["x-relay-callback-duplicate"] == "false"
    assert repeated.status_code == 204, repeated.text
    assert repeated.headers["x-relay-callback-duplicate"] == "true"
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        event = session.get(RelayCallbackEvent, body["event_id"])
        assert stored is not None and stored.status == TaskStatus.PROCESSING
        assert event is not None
        assert event.company_id == tenant["company_id"]
        assert event.relay_job_id == relay_job_id
        assert event.request_id == "relay-original-request-001"
        assert session.scalar(
            select(func.count()).select_from(RelayCallbackEvent)
        ) == 1
    assert client.get("/internal/relay-callback-events").status_code == 401
    events = client.get(
        "/internal/relay-callback-events",
        headers=internal_headers,
        params={
            "company_id": tenant["company_id"],
            "task_id": task["id"],
            "relay_status": "processing",
        },
    )
    assert events.status_code == 200, events.text
    assert events.json()["total"] == 1
    assert events.json()["items"][0]["id"] == body["event_id"]
    assert "payload_sha256" not in events.json()["items"][0]


def test_signed_reconciliation_callback_keeps_balance_reserved(
    app, client, tenant, tenant_headers, internal_headers
) -> None:
    configure_callback(app)
    task, relay_job_id = prepared_task(
        app, client, tenant, tenant_headers, "callback-reconciliation"
    )
    body = callback_body(
        task_id=task["id"],
        relay_job_id=relay_job_id,
        status="reconciliation_required",
    )

    response = signed_callback(client, body)

    assert response.status_code == 204, response.text
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        wallet = session.get(WalletAccount, tenant["company_id"])
        event = session.get(RelayCallbackEvent, body["event_id"])
        assert stored is not None and stored.status == TaskStatus.PROCESSING
        assert stored.actual_cost_cents is None
        assert stored.output_artifacts == []
        assert stored.reserved_cents == 400
        assert wallet is not None and wallet.available_cents == 600
        assert wallet.reserved_cents == 400
        assert event is not None
        assert event.relay_status == "reconciliation_required"

    events = client.get(
        "/internal/relay-callback-events",
        headers=internal_headers,
        params={"relay_status": "reconciliation_required"},
    )
    assert events.status_code == 200, events.text
    assert events.json()["total"] == 1
    assert events.json()["items"][0]["id"] == body["event_id"]


def test_signed_success_callback_atomically_settles_and_records_outputs(
    app, client, tenant, tenant_headers
) -> None:
    configure_callback(app)
    task, relay_job_id = prepared_task(
        app, client, tenant, tenant_headers, "callback-success"
    )
    asset_id = str(uuid4())
    body = callback_body(
        task_id=task["id"],
        relay_job_id=relay_job_id,
        status="succeeded",
        progress=100,
        outputs=[
            {
                "asset_id": asset_id,
                "object_key": (
                    f"outputs/{tenant['company_id']}/{relay_job_id}/{asset_id}"
                ),
                "media_type": "video",
                "content_type": "video/mp4",
                "size_bytes": 4096,
                "sha256": "a" * 64,
            }
        ],
    )

    response = signed_callback(client, body)

    assert response.status_code == 204, response.text
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        wallet = session.get(WalletAccount, tenant["company_id"])
        assert stored is not None and stored.status == TaskStatus.SUCCEEDED
        assert stored.actual_cost_cents == stored.quote_cents
        assert stored.output_artifacts == [
            {
                "asset_id": asset_id,
                "media_type": "video",
                "content_type": "video/mp4",
                "size_bytes": 4096,
                "sha256": "a" * 64,
            }
        ]
        assert wallet is not None and wallet.reserved_cents == 0
        assert session.get(RelayCallbackEvent, body["event_id"]) is not None


def test_callback_rejects_bad_signature_expiry_and_event_id_collision(
    app, client, tenant, tenant_headers
) -> None:
    configure_callback(app)
    task, relay_job_id = prepared_task(
        app, client, tenant, tenant_headers, "callback-reject"
    )
    body = callback_body(
        task_id=task["id"],
        relay_job_id=relay_job_id,
        status="processing",
    )

    bad_signature = signed_callback(client, body, secret="x" * 40)
    expired = signed_callback(client, body, timestamp=NOW - 301)
    accepted = signed_callback(client, body)
    changed = {
        **body,
        "job": {**body["job"], "progress": 80},
    }
    collision = signed_callback(client, changed)

    assert bad_signature.status_code == 401
    assert expired.status_code == 401
    assert accepted.status_code == 204
    assert collision.status_code == 409
    with app.state.session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(RelayCallbackEvent)
        ) == 1


def test_callback_requires_configuration_and_matching_task(
    app, client, tenant, tenant_headers
) -> None:
    task, relay_job_id = prepared_task(
        app, client, tenant, tenant_headers, "callback-config"
    )
    body = callback_body(
        task_id=task["id"],
        relay_job_id=relay_job_id,
        status="processing",
    )
    unavailable = signed_callback(client, body)
    assert unavailable.status_code == 503

    configure_callback(app)
    body["job"]["id"] = str(uuid4())
    mismatched = signed_callback(client, body)
    assert mismatched.status_code == 404


def test_failed_callback_persists_typed_error_snapshot_and_releases_once(
    app, client, tenant, tenant_headers
) -> None:
    configure_callback(app)
    task, relay_job_id = prepared_task(
        app, client, tenant, tenant_headers, "callback-failed-error"
    )
    body = callback_body(
        task_id=task["id"],
        relay_job_id=relay_job_id,
        status="failed",
        error={
            "code": "CONTENT_POLICY_REJECTED",
            "message": "Content was rejected",
            "retryable": False,
            "details": {"category": "policy"},
        },
    )

    response = signed_callback(client, body)
    assert response.status_code == 204, response.text
    task_response = client.get(
        f"/api/v1/companies/{tenant['company_id']}/tasks/{task['id']}",
        headers=tenant_headers,
    )
    assert task_response.status_code == 200
    assert task_response.json()["relay_error_snapshot"] == {
        **body["job"]["error"],
        "request_id": None,
        "http_status": None,
        "source": "callback",
    }
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        wallet = session.get(WalletAccount, tenant["company_id"])
        assert stored is not None and stored.status == TaskStatus.FAILED
        assert wallet is not None and wallet.reserved_cents == 0


def test_late_nonterminal_callback_after_success_is_stale_noop(
    app, client, tenant, tenant_headers
) -> None:
    configure_callback(app)
    task, relay_job_id = prepared_task(
        app, client, tenant, tenant_headers, "callback-stale-after-success"
    )
    asset_id = str(uuid4())
    succeeded = callback_body(
        task_id=task["id"],
        relay_job_id=relay_job_id,
        status="succeeded",
        progress=100,
        outputs=[
            {
                "asset_id": asset_id,
                "object_key": f"outputs/{relay_job_id}/{asset_id}",
                "media_type": "video",
                "content_type": "video/mp4",
                "size_bytes": 10,
                "sha256": "b" * 64,
            }
        ],
    )
    stale = callback_body(
        task_id=task["id"],
        relay_job_id=relay_job_id,
        status="processing",
    )

    assert signed_callback(client, succeeded).status_code == 204
    stale_response = signed_callback(client, stale)
    assert stale_response.status_code == 204, stale_response.text
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        wallet = session.get(WalletAccount, tenant["company_id"])
        assert stored is not None and stored.status == TaskStatus.SUCCEEDED
        assert wallet is not None and wallet.reserved_cents == 0
        assert session.scalar(
            select(func.count(RelayCallbackEvent.id)).where(
                RelayCallbackEvent.task_id == task["id"]
            )
        ) == 2


def test_callback_rejects_reservation_action_status_mismatch(
    app, client, tenant, tenant_headers
) -> None:
    configure_callback(app)
    task, relay_job_id = prepared_task(
        app, client, tenant, tenant_headers, "callback-action-mismatch"
    )
    body = callback_body(
        task_id=task["id"], relay_job_id=relay_job_id, status="processing"
    )
    body["job"]["reservation_action"] = "release"

    response = signed_callback(client, body)
    assert response.status_code == 422
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        assert stored is not None and stored.status == TaskStatus.QUEUED


def test_signed_callback_rejects_string_progress_instead_of_coercing(
    app, client, tenant, tenant_headers
) -> None:
    configure_callback(app)
    task, relay_job_id = prepared_task(
        app, client, tenant, tenant_headers, "callback-strict-progress"
    )
    body = callback_body(
        task_id=task["id"], relay_job_id=relay_job_id, status="processing"
    )
    body["job"]["progress"] = "50"

    response = signed_callback(client, body)

    assert response.status_code == 422
    assert response.json()["code"] == "relay_callback_invalid"
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        assert stored is not None and stored.status == TaskStatus.QUEUED
        assert session.get(RelayCallbackEvent, body["event_id"]) is None


def test_signed_callback_rejects_non_uuid_artifact_id(
    app, client, tenant, tenant_headers
) -> None:
    configure_callback(app)
    task, relay_job_id = prepared_task(
        app, client, tenant, tenant_headers, "callback-invalid-asset-id"
    )
    body = callback_body(
        task_id=task["id"],
        relay_job_id=relay_job_id,
        status="succeeded",
        progress=100,
        outputs=[
            {
                "asset_id": "not-a-uuid",
                "object_key": f"outputs/{relay_job_id}/invalid",
                "media_type": "video",
                "content_type": "video/mp4",
                "size_bytes": 10,
                "sha256": "b" * 64,
            }
        ],
    )

    response = signed_callback(client, body)

    assert response.status_code == 422
    assert response.json()["code"] == "relay_callback_invalid"
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        assert stored is not None and stored.status == TaskStatus.QUEUED
        assert session.get(RelayCallbackEvent, body["event_id"]) is None


def test_success_callback_accepts_sixteen_outputs(
    app, client, tenant, tenant_headers
) -> None:
    configure_callback(app)
    company_id = tenant["company_id"]
    model_id = seed_model(
        app,
        company_id,
        price_per_second_cents=None,
        price_per_item_cents=1,
        capability_config={"max_outputs": 16},
    )
    assert client.post(
        f"/api/v1/companies/{company_id}/wallet/recharge",
        headers=tenant_headers,
        json={"amount_cents": 100, "idempotency_key": "callback-16-recharge"},
    ).status_code == 200
    created = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "callback-16-task",
            "request_payload": {
                "mode": "text_to_video",
                "prompt": "sixteen outputs",
                "output_count": 16,
            },
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    relay_job_id = str(uuid4())
    with app.state.session_factory.begin() as session:
        session.get(GenerationTask, task["id"]).relay_job_id = relay_job_id
    outputs = [
        {
            "asset_id": str(uuid4()),
            "object_key": f"outputs/{relay_job_id}/{index}",
            "media_type": "video",
            "content_type": "video/mp4",
            "size_bytes": index + 1,
            "sha256": f"{index:064x}",
        }
        for index in range(16)
    ]
    body = callback_body(
        task_id=task["id"],
        relay_job_id=relay_job_id,
        status="succeeded",
        progress=100,
        outputs=outputs,
    )

    response = signed_callback(client, body)
    assert response.status_code == 204, response.text
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        assert stored is not None and stored.status == TaskStatus.SUCCEEDED
        assert len(stored.output_artifacts) == 16
