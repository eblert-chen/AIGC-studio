from __future__ import annotations

import hashlib
import hmac
import json
import time
from uuid import uuid4

from sqlalchemy import select

from platform_api.models import ChannelCostEntry, GenerationTask, TaskStatus, WalletAccount
from platform_api.services.billing import WalletService
from platform_api.services.channel_cost_events import ChannelCostEventVerifier

from .conftest import TEST_CHANNEL_COST_SIGNING_SECRET
from .test_platform_admin_section5 import (
    _admin,
    _catalog_model,
    _cost_payload,
    _create_task,
    _grant_model,
    _recharge,
)


SIGNING_SECRET = TEST_CHANNEL_COST_SIGNING_SECRET
INTERNAL_SERVICE_TOKEN = "test-internal-token"


def _raw(payload: dict) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _signed_headers(
    raw_body: bytes,
    *,
    event_id: str,
    timestamp: int | None = None,
    secret: str = SIGNING_SECRET,
) -> dict[str, str]:
    timestamp_value = int(time.time()) if timestamp is None else timestamp
    signing_input = (
        f"{timestamp_value}.{event_id}.".encode("ascii") + raw_body
    )
    signature = hmac.new(
        secret.encode("utf-8"), signing_input, hashlib.sha256
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Internal-Service-Token": INTERNAL_SERVICE_TOKEN,
        "X-Relay-Event-ID": event_id,
        "X-Relay-Timestamp": str(timestamp_value),
        "X-Relay-Signature": f"v1={signature}",
    }


def _post_signed(client, payload: dict, *, event_id: str, timestamp: int | None = None):
    raw_body = _raw(payload)
    return client.post(
        "/internal/channel-costs",
        content=raw_body,
        headers=_signed_headers(
            raw_body,
            event_id=event_id,
            timestamp=timestamp,
        ),
    )


def _relay_cost_payload(**kwargs) -> dict:
    return _cost_payload(**kwargs, evidence_source="provider_reported")


def test_signed_relay_costs_cover_all_terminal_outcomes_and_preserve_wallet(
    client, app, tenant
):
    app.state.channel_cost_event_verifier = ChannelCostEventVerifier(
        SIGNING_SECRET,
        signature_required=True,
    )
    _, admin_headers = _admin(client, "relay-cost-contract")
    model = _catalog_model(client, admin_headers, "relay-cost-contract")
    _grant_model(
        client,
        admin_headers,
        company_id=tenant["company_id"],
        model_id=model["id"],
        enabled=True,
        price_cents=100,
    )
    _recharge(
        client,
        admin_headers,
        company_id=tenant["company_id"],
        suffix="relay-cost-contract",
    )

    tasks = {
        status: _create_task(
            client,
            tenant,
            model_id=model["id"],
            suffix=f"relay-cost-{status}",
        )
        for status in ("succeeded", "failed", "cancelled")
    }
    relay_jobs = {
        "succeeded": "11111111-1111-4111-8111-111111111111",
        "failed": "22222222-2222-4222-8222-222222222222",
        "cancelled": "33333333-3333-4333-8333-333333333333",
    }
    with app.state.session_factory.begin() as session:
        for status, task_data in tasks.items():
            session.get(GenerationTask, task_data["id"]).relay_job_id = relay_jobs[status]
        WalletService.settle_success(
            session,
            company_id=tenant["company_id"],
            task_id=tasks["succeeded"]["id"],
            actual_cost_cents=90,
            idempotency_key="relay-cost-contract-settle",
        )
        WalletService.release_failure(
            session,
            company_id=tenant["company_id"],
            task_id=tasks["failed"]["id"],
            idempotency_key="relay-cost-contract-failed-release",
            failure_reason="provider billed before failure",
        )
        WalletService.release_failure(
            session,
            company_id=tenant["company_id"],
            task_id=tasks["cancelled"]["id"],
            idempotency_key="relay-cost-contract-cancelled-release",
            failure_reason="provider billed before cancellation",
            terminal_status=TaskStatus.CANCELLED,
        )
        wallet = session.get(WalletAccount, tenant["company_id"])
        wallet_before = (wallet.available_cents, wallet.reserved_cents)

    results: dict[str, tuple[dict, dict, str]] = {}
    for index, status in enumerate(("succeeded", "failed", "cancelled"), start=1):
        payload = _relay_cost_payload(
            suffix=f"relay-signed-{status}",
            amount_cents=index * 10,
            channel_key="official.route-a",
            channel_type="official",
            company_id=tenant["company_id"],
            task_id=tasks[status]["id"],
            relay_job_id=relay_jobs[status],
        )
        event_id = str(uuid4())
        response = _post_signed(client, payload, event_id=event_id)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["relay_event_id"] == event_id
        assert body["relay_payload_sha256"] == hashlib.sha256(
            _raw(payload)
        ).hexdigest()
        assert body["relay_event_timestamp"] is not None
        results[status] = (payload, body, event_id)

    with app.state.session_factory() as session:
        wallet = session.get(WalletAccount, tenant["company_id"])
        assert (wallet.available_cents, wallet.reserved_cents) == wallet_before
        assert session.get(GenerationTask, tasks["failed"]["id"]).status == TaskStatus.FAILED
        assert session.get(GenerationTask, tasks["cancelled"]["id"]).status == TaskStatus.CANCELLED

    payload, first, event_id = results["failed"]
    replay = _post_signed(
        client,
        payload,
        event_id=event_id,
        timestamp=int(time.time()) + 1,
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == first["id"]
    assert replay.json()["relay_event_timestamp"] == first["relay_event_timestamp"]
    with app.state.session_factory() as session:
        assert session.query(ChannelCostEntry).count() == 3


def test_signed_relay_cost_is_recorded_while_artifact_transfer_is_still_processing(
    client, app, tenant
):
    app.state.channel_cost_event_verifier = ChannelCostEventVerifier(
        SIGNING_SECRET,
        signature_required=True,
    )
    _, admin_headers = _admin(client, "relay-cost-processing")
    model = _catalog_model(client, admin_headers, "relay-cost-processing")
    _grant_model(
        client,
        admin_headers,
        company_id=tenant["company_id"],
        model_id=model["id"],
        enabled=True,
        price_cents=100,
    )
    _recharge(
        client,
        admin_headers,
        company_id=tenant["company_id"],
        suffix="relay-cost-processing",
    )
    task = _create_task(
        client,
        tenant,
        model_id=model["id"],
        suffix="relay-cost-processing",
    )
    relay_job_id = str(uuid4())
    with app.state.session_factory.begin() as session:
        stored_task = session.get(GenerationTask, task["id"])
        stored_task.status = TaskStatus.PROCESSING
        stored_task.relay_job_id = relay_job_id
        wallet = session.get(WalletAccount, tenant["company_id"])
        wallet_before = (wallet.available_cents, wallet.reserved_cents)

    payload = _relay_cost_payload(
        suffix="relay-signed-processing",
        amount_cents=23,
        channel_key="official.slow-artifact-transfer",
        channel_type="official",
        company_id=tenant["company_id"],
        task_id=task["id"],
        relay_job_id=relay_job_id,
    )
    response = _post_signed(client, payload, event_id=str(uuid4()))
    assert response.status_code == 201, response.text

    with app.state.session_factory() as session:
        stored_task = session.get(GenerationTask, task["id"])
        assert stored_task.status == TaskStatus.PROCESSING
        wallet = session.get(WalletAccount, tenant["company_id"])
        assert (wallet.available_cents, wallet.reserved_cents) == wallet_before
        entry = session.scalar(
            select(ChannelCostEntry).where(
                ChannelCostEntry.task_id == task["id"]
            )
        )
        assert entry is not None
        assert entry.company_id == tenant["company_id"]
        assert entry.relay_job_id == relay_job_id


def test_signed_relay_cost_rejects_conflicts_bad_evidence_and_cross_tenant_links(
    client, app, tenant
):
    app.state.channel_cost_event_verifier = ChannelCostEventVerifier(
        SIGNING_SECRET,
        signature_required=True,
    )
    payload = _relay_cost_payload(
        suffix="signed-conflict",
        amount_cents=42,
        channel_key="third-party.route-a",
        channel_type="third_party_api",
        company_id=tenant["company_id"],
    )
    event_id = str(uuid4())
    first = _post_signed(client, payload, event_id=event_id)
    assert first.status_code == 201, first.text

    unsigned = client.post(
        "/internal/channel-costs",
        headers={"X-Internal-Service-Token": INTERNAL_SERVICE_TOKEN},
        json={**payload, "idempotency_key": "unsigned-cost-key"},
    )
    assert unsigned.status_code == 401

    raw_body = _raw({**payload, "idempotency_key": "bad-signature-key"})
    bad_signature = client.post(
        "/internal/channel-costs",
        content=raw_body,
        headers={
            **_signed_headers(raw_body, event_id=str(uuid4())),
            "X-Relay-Signature": "v1=" + ("0" * 64),
        },
    )
    assert bad_signature.status_code == 401

    payload_conflict = _post_signed(
        client,
        {**payload, "amount_cents": 43},
        event_id=str(uuid4()),
    )
    assert payload_conflict.status_code == 409

    reused_event = _post_signed(
        client,
        {**payload, "idempotency_key": "different-idempotency-key"},
        event_id=event_id,
    )
    assert reused_event.status_code == 409

    bad_channel = _post_signed(
        client,
        {
            **payload,
            "idempotency_key": "bad-channel-type-key",
            "channel_type": "unclassified",
        },
        event_id=str(uuid4()),
    )
    assert bad_channel.status_code == 422


def test_signed_relay_cannot_report_success_for_an_operator_first_entry(
    client, app
):
    app.state.channel_cost_event_verifier = ChannelCostEventVerifier(
        SIGNING_SECRET,
        signature_required=True,
    )
    payload = _cost_payload(
        suffix="operator-first-signed-collision",
        amount_cents=17,
        channel_key="official.operator-first",
        channel_type="official",
    )
    admin_id, admin_headers = _admin(client, "operator-first-signed-collision")
    operator_entry = client.post(
        "/api/v1/platform-admin/channel-costs",
        headers=admin_headers,
        json=payload,
    )
    assert operator_entry.status_code == 201, operator_entry.text
    assert operator_entry.json()["recorded_by_user_id"] == admin_id
    assert operator_entry.json()["relay_event_id"] is None

    relay = _post_signed(
        client,
        {**payload, "evidence_source": "provider_reported"},
        event_id=str(uuid4()),
    )
    assert relay.status_code == 409, relay.text
    with app.state.session_factory() as session:
        stored = session.get(ChannelCostEntry, operator_entry.json()["id"])
        assert stored is not None
        assert stored.relay_event_id is None
        assert stored.relay_event_timestamp is None
        assert stored.relay_payload_sha256 is None


def test_optional_development_mode_still_verifies_partial_or_present_headers(client):
    payload = _relay_cost_payload(
        suffix="development-unsigned",
        amount_cents=1,
        channel_key="reverse.route-a",
        channel_type="reverse",
    )
    unsigned = client.post(
        "/internal/channel-costs",
        headers={"X-Internal-Service-Token": INTERNAL_SERVICE_TOKEN},
        json=payload,
    )
    assert unsigned.status_code == 201, unsigned.text

    partial = client.post(
        "/internal/channel-costs",
        headers={
            "X-Internal-Service-Token": INTERNAL_SERVICE_TOKEN,
            "X-Relay-Event-ID": str(uuid4()),
        },
        json={**payload, "idempotency_key": "partial-header-key"},
    )
    assert partial.status_code == 401


def test_signed_relay_cost_rejects_expired_signatures_and_duplicate_json_keys(
    client, app
):
    app.state.channel_cost_event_verifier = ChannelCostEventVerifier(
        SIGNING_SECRET,
        signature_required=True,
    )
    payload = _relay_cost_payload(
        suffix="signature-envelope",
        amount_cents=1,
        channel_key="official.route-a",
        channel_type="official",
    )
    expired = _post_signed(
        client,
        payload,
        event_id=str(uuid4()),
        timestamp=int(time.time()) - 301,
    )
    assert expired.status_code == 401

    regular_body = _raw(payload)
    duplicate_body = regular_body.replace(
        b'"amount_cents":1,',
        b'"amount_cents":1,"amount_cents":2,',
        1,
    )
    event_id = str(uuid4())
    duplicate = client.post(
        "/internal/channel-costs",
        content=duplicate_body,
        headers=_signed_headers(duplicate_body, event_id=event_id),
    )
    assert duplicate.status_code == 422


def test_channel_cost_event_body_size_is_bounded_before_verification(client):
    response = client.post(
        "/internal/channel-costs",
        content=b"x" * (64 * 1024 + 1),
        headers={
            "Content-Type": "application/json",
            "X-Internal-Service-Token": INTERNAL_SERVICE_TOKEN,
        },
    )
    assert response.status_code == 413
