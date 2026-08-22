from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import time
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from platform_api.models import (
    ChannelCostEntry,
    GenerationTask,
    ModelDefinition,
    TaskStatus,
    WalletAccount,
)

PLATFORM_URL = os.getenv("PLATFORM_CHANNEL_COST_IT_URL", "").rstrip("/")
PLATFORM_DATABASE_URL = os.getenv("PLATFORM_CHANNEL_COST_IT_DATABASE_URL", "")
NEW_API_DATABASE_URL = os.getenv("NEW_API_CHANNEL_COST_IT_DATABASE_URL", "")
INTERNAL_TOKEN = os.getenv("PLATFORM_CHANNEL_COST_IT_INTERNAL_TOKEN", "")
SIGNING_SECRET = os.getenv("PLATFORM_CHANNEL_COST_IT_SIGNING_SECRET", "")
BOOTSTRAP_TOKEN = os.getenv("BOOTSTRAP_TOKEN", "")
RELAY_URL = os.getenv("NEW_API_CHANNEL_COST_IT_URL", "").rstrip("/")
RELAY_CLIENT_ID = os.getenv("NEW_API_CHANNEL_COST_IT_CLIENT_ID", "")
RELAY_API_KEY = os.getenv("NEW_API_CHANNEL_COST_IT_API_KEY", "")
RELAY_SERVICE_TENANT_ID = os.getenv("NEW_API_CHANNEL_COST_IT_SERVICE_TENANT_ID", "")
CONTRACT_RATE_ID = os.getenv("NEW_API_CHANNEL_COST_IT_CONTRACT_RATE_ID", "")
PROVIDER_NAME = os.getenv("NEW_API_CHANNEL_COST_IT_PROVIDER_NAME", "")
PROVIDER_CHANNEL_ID = os.getenv("NEW_API_CHANNEL_COST_IT_PROVIDER_CHANNEL_ID", "")
PROVIDER_UPSTREAM_MODEL = os.getenv(
    "NEW_API_CHANNEL_COST_IT_PROVIDER_UPSTREAM_MODEL", ""
)
CONTRACT_RATE_SOURCE_REFERENCE = os.getenv(
    "NEW_API_CHANNEL_COST_IT_CONTRACT_RATE_SOURCE_REFERENCE", ""
)
CONTRACT_RATE_SOURCE_SHA256 = os.getenv(
    "NEW_API_CHANNEL_COST_IT_CONTRACT_RATE_SOURCE_SHA256", ""
)
RELAY_MODEL = "cost-acceptance.video.v1"
RELAY_MODE = "text_to_video"
PROVIDER_ROUTE_KEY = "official.integration-route"


def _require_integration_environment() -> None:
    missing = [
        name
        for name, value in (
            ("PLATFORM_CHANNEL_COST_IT_URL", PLATFORM_URL),
            ("PLATFORM_CHANNEL_COST_IT_DATABASE_URL", PLATFORM_DATABASE_URL),
            ("NEW_API_CHANNEL_COST_IT_DATABASE_URL", NEW_API_DATABASE_URL),
            ("PLATFORM_CHANNEL_COST_IT_INTERNAL_TOKEN", INTERNAL_TOKEN),
            ("PLATFORM_CHANNEL_COST_IT_SIGNING_SECRET", SIGNING_SECRET),
            ("BOOTSTRAP_TOKEN", BOOTSTRAP_TOKEN),
            ("NEW_API_CHANNEL_COST_IT_URL", RELAY_URL),
            ("NEW_API_CHANNEL_COST_IT_CLIENT_ID", RELAY_CLIENT_ID),
            ("NEW_API_CHANNEL_COST_IT_API_KEY", RELAY_API_KEY),
            (
                "NEW_API_CHANNEL_COST_IT_SERVICE_TENANT_ID",
                RELAY_SERVICE_TENANT_ID,
            ),
            ("NEW_API_CHANNEL_COST_IT_CONTRACT_RATE_ID", CONTRACT_RATE_ID),
            ("NEW_API_CHANNEL_COST_IT_PROVIDER_NAME", PROVIDER_NAME),
            ("NEW_API_CHANNEL_COST_IT_PROVIDER_CHANNEL_ID", PROVIDER_CHANNEL_ID),
            (
                "NEW_API_CHANNEL_COST_IT_PROVIDER_UPSTREAM_MODEL",
                PROVIDER_UPSTREAM_MODEL,
            ),
            (
                "NEW_API_CHANNEL_COST_IT_CONTRACT_RATE_SOURCE_REFERENCE",
                CONTRACT_RATE_SOURCE_REFERENCE,
            ),
            (
                "NEW_API_CHANNEL_COST_IT_CONTRACT_RATE_SOURCE_SHA256",
                CONTRACT_RATE_SOURCE_SHA256,
            ),
        )
        if not value
    ]
    if missing:
        pytest.skip(
            "requires running Platform/new-api integration services: "
            + ", ".join(missing)
        )
    if hmac.compare_digest(INTERNAL_TOKEN, SIGNING_SECRET):
        raise AssertionError(
            "channel-cost integration signing secret must differ from the "
            "internal token"
        )
    if str(UUID(RELAY_SERVICE_TENANT_ID)) != RELAY_SERVICE_TENANT_ID:
        raise AssertionError("Relay service tenant must be a canonical UUID")
    if str(UUID(CONTRACT_RATE_ID)) != CONTRACT_RATE_ID:
        raise AssertionError("Relay contract rate id must be a canonical UUID")
    if not PROVIDER_CHANNEL_ID.isdigit() or int(PROVIDER_CHANNEL_ID) <= 0:
        raise AssertionError("Relay provider channel id must be positive")
    if len(CONTRACT_RATE_SOURCE_SHA256) != 64 or any(
        character not in "0123456789abcdef" for character in CONTRACT_RATE_SOURCE_SHA256
    ):
        raise AssertionError("Relay contract source digest must be lowercase SHA-256")


def _canonical_body(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _relay_headers(
    *, request_id: str, idempotency_key: str | None = None
) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "X-Client-ID": RELAY_CLIENT_ID,
        "X-API-Key": RELAY_API_KEY,
        "X-Request-ID": request_id,
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _relay_capability_revision(client: httpx.Client) -> str:
    response = client.get(
        "/v1/models",
        headers=_relay_headers(request_id="cost-it-model-catalog"),
    )
    assert response.status_code == 200, response.text
    model = next(item for item in response.json()["data"] if item["id"] == RELAY_MODEL)
    revision = model["capability_revision"]
    assert revision.startswith("sha256:") and len(revision) == 71
    return revision


def _submit_relay_job(
    client: httpx.Client,
    *,
    unique: str,
    status_name: str,
    company_id: str,
    task_id: str,
    capability_revision: str,
) -> str:
    payload = {
        "client_reference_id": task_id,
        "model": RELAY_MODEL,
        "expected_capability_revision": capability_revision,
        "mode": RELAY_MODE,
        "inputs": {"prompt": f"runtime materializer {status_name}", "assets": []},
        "output": {
            "duration_seconds": 5,
            "aspect_ratio": "16:9",
            "resolution": "720p",
            "count": 1,
            "face_enabled": False,
        },
        "metadata": {
            "platform_company_id": company_id,
            "platform_task_id": task_id,
            "cost_acceptance_case": status_name,
        },
    }
    response = client.post(
        "/v1/generations",
        content=_canonical_body(payload),
        headers={
            **_relay_headers(
                request_id=f"cost-it-{status_name}-{unique}",
                idempotency_key=f"cost-it-{status_name}-{unique}",
            ),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 202, response.text
    accepted = response.json()
    assert accepted["id"] == accepted["job_id"]
    assert accepted["status"] == "queued"
    assert accepted["capability_revision"] == capability_revision
    return accepted["job_id"]


def _record_provider_success_outcomes(
    session: Session,
    *,
    unique: str,
    company_id: str,
    task_ids: dict[str, str],
    relay_job_ids: dict[str, str],
) -> dict[str, str]:
    """Record the provider fact that currently has no external test API.

    The positive path never inserts a cost event or delivery. The running
    Relay must discover these immutable outcomes, select its configured
    contract rate, materialize each cost, and deliver it to Platform.
    """
    route = (
        session.execute(
            text("""
            SELECT id, provider_name, channel_id, channel_class,
                   upstream_model, production_ready, model, mode
            FROM platform_generation_provider_routes
            WHERE route_key = :route_key AND model = :model AND mode = :mode
            """),
            {
                "route_key": PROVIDER_ROUTE_KEY,
                "model": RELAY_MODEL,
                "mode": RELAY_MODE,
            },
        )
        .mappings()
        .one()
    )
    route_id = route["id"]
    assert isinstance(route_id, int) and route_id > 0
    assert route["provider_name"] == PROVIDER_NAME
    assert route["channel_id"] == int(PROVIDER_CHANNEL_ID)
    assert route["channel_class"] == "official"
    assert route["upstream_model"] == PROVIDER_UPSTREAM_MODEL
    assert route["production_ready"] is True
    assert route["model"] == RELAY_MODEL
    assert route["mode"] == RELAY_MODE

    rate = (
        session.execute(
            text("""
            SELECT id, provider_name, channel_id, upstream_model, mode,
                   resolution, billing_unit, unit_amount_cents,
                   currency, effective_from, source_reference,
                   source_document_sha256
            FROM platform_provider_contract_rates
            WHERE id = :rate_id
            """),
            {"rate_id": CONTRACT_RATE_ID},
        )
        .mappings()
        .one()
    )
    assert rate["provider_name"] == PROVIDER_NAME
    assert rate["channel_id"] == int(PROVIDER_CHANNEL_ID)
    assert rate["upstream_model"] == PROVIDER_UPSTREAM_MODEL
    assert rate["mode"] == RELAY_MODE
    assert rate["resolution"] == "720p"
    assert rate["billing_unit"] == "output_second"
    assert rate["unit_amount_cents"] == 5
    assert rate["currency"] == "CNY"
    effective_from = rate["effective_from"]
    if effective_from.tzinfo is None:
        effective_from = effective_from.replace(tzinfo=timezone.utc)
    assert effective_from.astimezone(timezone.utc) == datetime(
        2026, 8, 7, 11, 0, tzinfo=timezone.utc
    )
    assert rate["source_reference"] == CONTRACT_RATE_SOURCE_REFERENCE
    assert rate["source_document_sha256"] == CONTRACT_RATE_SOURCE_SHA256

    relay_statuses = {
        "processing": "transferring",
        "succeeded": "succeeded",
        "failed": "failed",
        "cancelled": "cancelled",
    }
    outcome_ids: dict[str, str] = {}
    for status_name, relay_job_id in relay_job_ids.items():
        job = (
            session.execute(
                text("""
                SELECT tenant_id, source_client_id, client_reference_id,
                       request_json
                FROM platform_generation_jobs
                WHERE id = :job_id
                """),
                {"job_id": relay_job_id},
            )
            .mappings()
            .one()
        )
        request_snapshot = json.loads(job["request_json"])
        assert job["tenant_id"] == RELAY_SERVICE_TENANT_ID
        assert job["tenant_id"] != company_id
        assert job["source_client_id"] == RELAY_CLIENT_ID
        assert job["client_reference_id"] == task_ids[status_name]
        assert request_snapshot["client_reference_id"] == task_ids[status_name]
        assert request_snapshot["metadata"]["platform_company_id"] == company_id
        assert request_snapshot["metadata"]["platform_task_id"] == task_ids[status_name]
        assert request_snapshot["metadata"]["cost_acceptance_case"] == status_name

        updated = session.execute(
            text("""
                UPDATE platform_generation_jobs
                SET status = :status, progress = :progress,
                    provider_route_id = :route_id,
                    provider_channel_id = :channel_id,
                    provider_key_index = 0,
                    provider_submission_attempt = 1,
                    upstream_task_id = :upstream_task_id,
                    updated_at = now()
                WHERE id = :job_id AND tenant_id = :tenant_id
                  AND client_reference_id = :task_id
                """),
            {
                "status": relay_statuses[status_name],
                "progress": 95 if status_name == "processing" else 100,
                "route_id": route_id,
                "channel_id": int(PROVIDER_CHANNEL_ID),
                "upstream_task_id": f"provider-task-{status_name}-{unique}",
                "job_id": relay_job_id,
                "tenant_id": RELAY_SERVICE_TENANT_ID,
                "task_id": task_ids[status_name],
            },
        )
        assert updated.rowcount == 1

        outcome_id = str(uuid4())
        outcome_ids[status_name] = outcome_id
        session.execute(
            text("""
                INSERT INTO platform_provider_terminal_outcomes
                    (id, route_id, route_key, provider_name, channel_class,
                     relay_job_id, outcome, failure_owner, failure_code,
                     account_invalidated, occurred_at, external_reference,
                     created_at)
                VALUES
                    (:id, :route_id, :route_key, :provider_name, 'official',
                     :relay_job_id, 'succeeded', 'none', '', false,
                     :occurred_at, :external_reference, now())
                """),
            {
                "id": outcome_id,
                "route_id": route_id,
                "route_key": PROVIDER_ROUTE_KEY,
                "provider_name": PROVIDER_NAME,
                "relay_job_id": relay_job_id,
                "occurred_at": datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc),
                "external_reference": f"provider-task:{status_name}-{unique}",
            },
        )
    return outcome_ids


def _wait_for_materialized_cost(
    session_factory,
    *,
    relay_job_id: str,
    timeout_seconds: float = 20,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with session_factory() as session:
            row = (
                session.execute(
                    text("""
                    SELECT costs.*, queue.state AS reconciliation_state,
                           deliveries.state AS delivery_state
                    FROM platform_channel_cost_events AS costs
                    JOIN platform_channel_cost_reconciliations AS queue
                      ON queue.relay_job_id = costs.relay_job_id
                    JOIN platform_relay_external_deliveries AS deliveries
                      ON deliveries.event_kind = 'channel_cost'
                     AND deliveries.event_id = costs.id
                    WHERE costs.relay_job_id = :relay_job_id
                    """),
                    {"relay_job_id": relay_job_id},
                )
                .mappings()
                .one_or_none()
            )
            if row is not None and row["reconciliation_state"] == "completed":
                return dict(row)
        time.sleep(0.1)
    raise AssertionError(
        f"Relay runtime did not materialize cost for job {relay_job_id}"
    )


def _signature_headers(
    raw_body: bytes,
    *,
    event_id: str,
    timestamp: int | None = None,
    secret: str | None = None,
) -> dict[str, str]:
    timestamp_value = int(time.time()) if timestamp is None else timestamp
    signing_input = f"{timestamp_value}.{event_id}.".encode("ascii") + raw_body
    digest = hmac.new(
        (secret or SIGNING_SECRET).encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Internal-Service-Token": INTERNAL_TOKEN,
        "X-Relay-Event-ID": event_id,
        "X-Relay-Timestamp": str(timestamp_value),
        "X-Relay-Signature": f"v1={digest}",
    }


def _insert_rejection_delivery_fixture(
    session: Session,
    *,
    payload: dict,
    event_id: str,
    max_attempts: int = 8,
) -> None:
    """Seed only deliberately forged deliveries used by rejection tests.

    Positive costs must come from the running Relay's runtime materializer.
    """
    event_payload = {
        **payload,
        "evidence_source": payload.get("evidence_source", "provider_reported"),
    }
    raw_body = _canonical_body(event_payload)
    session.execute(
        text("""
            INSERT INTO platform_channel_cost_events
                (id, amount_cents, idempotency_key, channel_key, channel_type,
                 occurred_at, external_reference, company_id, task_id,
                 relay_job_id, note, evidence_source, evidence_reference,
                 source_document_sha256, payload_json, payload_sha256,
                 created_at)
            VALUES
                (:id, :amount_cents, :idempotency_key, :channel_key,
                 :channel_type, :occurred_at, :external_reference,
                 :company_id, :task_id, :relay_job_id, :note,
                 'provider_reported', '', '', :payload_json,
                 :payload_sha256, now())
            """),
        {
            **event_payload,
            "id": event_id,
            "company_id": payload.get("company_id", ""),
            "task_id": payload.get("task_id", ""),
            "relay_job_id": payload.get("relay_job_id", ""),
            "payload_json": raw_body.decode("utf-8"),
            "payload_sha256": hashlib.sha256(raw_body).hexdigest(),
        },
    )
    session.execute(
        text("""
            INSERT INTO platform_relay_external_deliveries
                (event_kind, event_id, request_id, state, attempts,
                 max_attempts, available_at, claim_token, claimed_at,
                 claim_expires_at, response_status, last_error, delivered_at,
                 dead_lettered_at, created_at, updated_at)
            VALUES
                ('channel_cost', :event_id, :request_id, 'pending', 0,
                 :max_attempts, now(), '', NULL, NULL, 0, '', NULL, NULL,
                 now(), now())
            """),
        {
            "event_id": event_id,
            "request_id": f"channel-cost-it-{event_id}",
            "max_attempts": max_attempts,
        },
    )


def _wait_for_delivery(
    session_factory,
    *,
    event_id: str,
    expected_state: str,
    timeout_seconds: float = 15,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with session_factory() as session:
            row = (
                session.execute(
                    text("""
                    SELECT state, attempts, response_status, last_error,
                           delivered_at, dead_lettered_at
                    FROM platform_relay_external_deliveries
                    WHERE event_kind = 'channel_cost' AND event_id = :event_id
                    """),
                    {"event_id": event_id},
                )
                .mappings()
                .one_or_none()
            )
            if row is not None and row["state"] == expected_state:
                return dict(row)
        time.sleep(0.1)
    raise AssertionError(f"new-api delivery {event_id} did not reach {expected_state}")


def _cost_payload(
    *,
    suffix: str,
    company_id: str,
    task_id: str,
    relay_job_id: str,
    channel_type: str = "official",
) -> dict:
    return {
        "amount_cents": 25,
        "idempotency_key": f"new-api-platform-it-{suffix}",
        "channel_key": "official.integration-route",
        "channel_type": channel_type,
        "occurred_at": "2026-08-07T12:00:00Z",
        "external_reference": f"provider-charge-{suffix}",
        "company_id": company_id,
        "task_id": task_id,
        "relay_job_id": relay_job_id,
        "note": "provider terminal charge",
        "evidence_source": "provider_reported",
    }


def test_real_new_api_runtime_materializes_and_delivers_signed_costs():
    _require_integration_environment()
    platform_engine = create_engine(PLATFORM_DATABASE_URL, pool_pre_ping=True)
    new_api_engine = create_engine(NEW_API_DATABASE_URL, pool_pre_ping=True)
    platform_session_factory = lambda: Session(platform_engine)
    new_api_session_factory = lambda: Session(new_api_engine)
    unique = uuid4().hex

    try:
        with httpx.Client(base_url=PLATFORM_URL, timeout=5) as client:
            bootstrap = client.post(
                "/api/v1/bootstrap",
                headers={"X-Bootstrap-Token": BOOTSTRAP_TOKEN},
                json={
                    "company_name": f"Channel Cost IT {unique}",
                    "owner_email": f"channel-cost-it-{unique}@example.com",
                    "owner_display_name": "Channel Cost IT Owner",
                },
            )
            assert bootstrap.status_code == 201, bootstrap.text
            identity = bootstrap.json()

        model_id = str(uuid4())
        task_ids: dict[str, str] = {}
        relay_job_ids: dict[str, str] = {}
        first_evidence_timestamps: dict[str, datetime] = {}
        status_specs = (
            ("processing", TaskStatus.PROCESSING),
            ("succeeded", TaskStatus.SUCCEEDED),
            ("failed", TaskStatus.FAILED),
            ("cancelled", TaskStatus.CANCELLED),
        )
        with platform_session_factory() as session:
            session.add(
                ModelDefinition(
                    id=model_id,
                    slug=f"channel-cost-it-{unique}",
                    display_name="Channel Cost Integration Model",
                    provider_key="integration-provider",
                    billing_mode="per_item",
                    capability_version=1,
                    active=True,
                    published_at=datetime.now(timezone.utc),
                )
            )
            session.flush()
            for status_name, status in status_specs:
                task_id = str(uuid4())
                task_ids[status_name] = task_id
                session.add(
                    GenerationTask(
                        id=task_id,
                        company_id=identity["company_id"],
                        user_id=identity["user_id"],
                        model_id=model_id,
                        idempotency_key=f"channel-cost-task-{status_name}-{unique}",
                        request_fingerprint=hashlib.sha256(
                            f"{status_name}-{unique}".encode()
                        ).hexdigest(),
                        status=status,
                        request_payload={"prompt": "integration"},
                        quote_cents=100,
                        pricing_snapshot={"mode": "per_item", "unit_price_cents": 100},
                        capability_snapshot={"version": 1},
                        reserved_cents=0,
                        actual_cost_cents=(
                            90 if status == TaskStatus.SUCCEEDED else None
                        ),
                        relay_job_id=None,
                        failure_reason=(
                            status.value
                            if status in {TaskStatus.FAILED, TaskStatus.CANCELLED}
                            else None
                        ),
                    )
                )
            session.commit()
            wallet = session.get(WalletAccount, identity["company_id"])
            wallet_before = (wallet.available_cents, wallet.reserved_cents)

        with httpx.Client(base_url=RELAY_URL, timeout=5) as relay_client:
            capability_revision = _relay_capability_revision(relay_client)
            for status_name, _ in status_specs:
                relay_job_ids[status_name] = _submit_relay_job(
                    relay_client,
                    unique=unique,
                    status_name=status_name,
                    company_id=identity["company_id"],
                    task_id=task_ids[status_name],
                    capability_revision=capability_revision,
                )

        with platform_session_factory() as session:
            for status_name, _ in status_specs:
                task = session.get(GenerationTask, task_ids[status_name])
                assert task is not None
                task.relay_job_id = relay_job_ids[status_name]
            session.commit()

        with new_api_session_factory() as session:
            outcome_ids = _record_provider_success_outcomes(
                session,
                unique=unique,
                company_id=identity["company_id"],
                task_ids=task_ids,
                relay_job_ids=relay_job_ids,
            )
            session.commit()

        delivered: dict[str, tuple[str, dict]] = {}
        for status_name, _ in status_specs:
            materialized = _wait_for_materialized_cost(
                new_api_session_factory,
                relay_job_id=relay_job_ids[status_name],
            )
            outcome_id = outcome_ids[status_name]
            event_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"relay-contract-cost:{outcome_id}:{CONTRACT_RATE_ID}",
                )
            )
            assert materialized["id"] == event_id
            assert materialized["reconciliation_state"] == "completed"
            assert materialized["amount_cents"] == 25
            assert materialized["idempotency_key"] == (
                f"relay-contract-cost-{outcome_id}-{CONTRACT_RATE_ID}"
            )
            assert materialized["channel_key"] == PROVIDER_ROUTE_KEY
            assert materialized["channel_type"] == "official"
            assert materialized["external_reference"] == (
                f"provider-task:{status_name}-{unique}"
            )
            assert materialized["company_id"] == identity["company_id"]
            assert materialized["task_id"] == task_ids[status_name]
            assert materialized["relay_job_id"] == relay_job_ids[status_name]
            assert materialized["evidence_source"] == "contract_rate"
            assert materialized["evidence_reference"] == CONTRACT_RATE_SOURCE_REFERENCE
            assert materialized["source_document_sha256"] == CONTRACT_RATE_SOURCE_SHA256
            payload = json.loads(materialized["payload_json"])
            assert payload["amount_cents"] == 25
            assert payload["company_id"] == identity["company_id"]
            assert payload["task_id"] == task_ids[status_name]
            assert payload["relay_job_id"] == relay_job_ids[status_name]
            assert payload["evidence_source"] == "contract_rate"
            assert payload["evidence_reference"] == CONTRACT_RATE_SOURCE_REFERENCE
            assert payload["source_document_sha256"] == CONTRACT_RATE_SOURCE_SHA256
            assert hashlib.sha256(_canonical_body(payload)).hexdigest() == (
                materialized["payload_sha256"]
            )
            delivered[status_name] = (event_id, payload)

        for event_id, _ in delivered.values():
            delivery = _wait_for_delivery(
                new_api_session_factory,
                event_id=event_id,
                expected_state="delivered",
            )
            assert delivery["attempts"] == 1
            assert delivery["response_status"] == 201

        with platform_session_factory() as session:
            entries = list(
                session.scalars(
                    select(ChannelCostEntry).where(
                        ChannelCostEntry.relay_event_id.in_(
                            [event_id for event_id, _ in delivered.values()]
                        )
                    )
                )
            )
            assert len(entries) == 4
            for entry in entries:
                _, payload = next(
                    value
                    for value in delivered.values()
                    if value[0] == entry.relay_event_id
                )
                assert entry.source.value == "relay"
                assert (
                    entry.relay_payload_sha256
                    == hashlib.sha256(_canonical_body(payload)).hexdigest()
                )
                assert entry.relay_event_timestamp is not None
                assert entry.evidence_source == "contract_rate"
                assert entry.evidence_reference == CONTRACT_RATE_SOURCE_REFERENCE
                assert entry.source_document_sha256 == CONTRACT_RATE_SOURCE_SHA256
                first_evidence_timestamps[entry.relay_event_id] = (
                    entry.relay_event_timestamp
                )
            wallet = session.get(WalletAccount, identity["company_id"])
            assert (wallet.available_cents, wallet.reserved_cents) == wallet_before
            assert (
                session.get(GenerationTask, task_ids["processing"]).status
                == TaskStatus.PROCESSING
            )
            assert (
                session.get(GenerationTask, task_ids["succeeded"]).status
                == TaskStatus.SUCCEEDED
            )
            assert (
                session.get(GenerationTask, task_ids["failed"]).status
                == TaskStatus.FAILED
            )
            assert (
                session.get(GenerationTask, task_ids["cancelled"]).status
                == TaskStatus.CANCELLED
            )

        replay_event_id, _ = delivered["failed"]
        with new_api_session_factory() as session:
            session.execute(
                text("""
                    UPDATE platform_relay_external_deliveries
                    SET state = 'pending', available_at = now(),
                        delivered_at = NULL, response_status = 0,
                        updated_at = now()
                    WHERE event_kind = 'channel_cost' AND event_id = :event_id
                    """),
                {"event_id": replay_event_id},
            )
            session.commit()
        replay = _wait_for_delivery(
            new_api_session_factory,
            event_id=replay_event_id,
            expected_state="delivered",
        )
        assert replay["attempts"] == 2
        with platform_session_factory() as session:
            replay_entries = list(
                session.scalars(
                    select(ChannelCostEntry).where(
                        ChannelCostEntry.relay_event_id == replay_event_id
                    )
                )
            )
            assert len(replay_entries) == 1
            assert (
                replay_entries[0].relay_event_timestamp
                == first_evidence_timestamps[replay_event_id]
            )

        wrong_company_payload = _cost_payload(
            suffix=f"wrong-company-{unique}",
            company_id=str(uuid4()),
            task_id=task_ids["failed"],
            relay_job_id=relay_job_ids["failed"],
        )
        wrong_company_event_id = str(uuid4())
        wrong_relay_payload = _cost_payload(
            suffix=f"wrong-relay-{unique}",
            company_id=identity["company_id"],
            task_id=task_ids["failed"],
            relay_job_id=str(uuid4()),
        )
        wrong_relay_event_id = str(uuid4())
        invalid_channel_payload = _cost_payload(
            suffix=f"invalid-channel-{unique}",
            company_id=identity["company_id"],
            task_id=task_ids["failed"],
            relay_job_id=relay_job_ids["failed"],
            channel_type="unknown",
        )
        invalid_channel_event_id = str(uuid4())
        with new_api_session_factory() as session:
            _insert_rejection_delivery_fixture(
                session,
                payload=wrong_company_payload,
                event_id=wrong_company_event_id,
                max_attempts=1,
            )
            _insert_rejection_delivery_fixture(
                session,
                payload=invalid_channel_payload,
                event_id=invalid_channel_event_id,
                max_attempts=1,
            )
            _insert_rejection_delivery_fixture(
                session,
                payload=wrong_relay_payload,
                event_id=wrong_relay_event_id,
                max_attempts=1,
            )
            session.commit()

        wrong_company = _wait_for_delivery(
            new_api_session_factory,
            event_id=wrong_company_event_id,
            expected_state="dead_letter",
        )
        assert wrong_company["response_status"] == 409, wrong_company
        assert wrong_company["last_error"] == "idempotency_conflict", wrong_company
        wrong_relay = _wait_for_delivery(
            new_api_session_factory,
            event_id=wrong_relay_event_id,
            expected_state="dead_letter",
        )
        assert wrong_relay["response_status"] == 409, wrong_relay
        assert wrong_relay["last_error"] == "idempotency_conflict", wrong_relay
        invalid_channel = _wait_for_delivery(
            new_api_session_factory,
            event_id=invalid_channel_event_id,
            expected_state="dead_letter",
        )
        assert invalid_channel["response_status"] == 422, invalid_channel
        assert invalid_channel["last_error"] == "endpoint_rejected", invalid_channel
        with platform_session_factory() as session:
            rejected_event_ids = {
                wrong_company_event_id,
                wrong_relay_event_id,
                invalid_channel_event_id,
            }
            assert not list(
                session.scalars(
                    select(ChannelCostEntry).where(
                        ChannelCostEntry.relay_event_id.in_(rejected_event_ids)
                    )
                )
            )

        operator_payload = {
            "amount_cents": 19,
            "idempotency_key": f"new-api-platform-it-operator-first-{unique}",
            "channel_key": "official.operator-first",
            "channel_type": "official",
            "occurred_at": "2026-08-07T12:30:00Z",
            "external_reference": f"provider-charge-operator-first-{unique}",
            "note": "operator first signed collision",
        }
        with httpx.Client(base_url=PLATFORM_URL, timeout=5) as client:
            admin = client.post(
                "/api/v1/bootstrap/platform-admin",
                headers={"X-Bootstrap-Token": BOOTSTRAP_TOKEN},
                json={
                    "email": f"channel-cost-admin-{unique}@example.com",
                    "display_name": "Channel Cost Integration Admin",
                },
            )
            assert admin.status_code == 201, admin.text
            operator_entry = client.post(
                "/api/v1/platform-admin/channel-costs",
                headers={"X-Platform-Admin-User-ID": admin.json()["user_id"]},
                json=operator_payload,
            )
            assert operator_entry.status_code == 201, operator_entry.text
            assert operator_entry.json()["relay_event_id"] is None

        operator_collision_event_id = str(uuid4())
        with new_api_session_factory() as session:
            _insert_rejection_delivery_fixture(
                session,
                payload=operator_payload,
                event_id=operator_collision_event_id,
                max_attempts=1,
            )
            session.commit()
        operator_collision = _wait_for_delivery(
            new_api_session_factory,
            event_id=operator_collision_event_id,
            expected_state="dead_letter",
        )
        assert operator_collision["response_status"] == 409
        assert operator_collision["last_error"] == "idempotency_conflict"
        with platform_session_factory() as session:
            operator_entries = list(
                session.scalars(
                    select(ChannelCostEntry).where(
                        ChannelCostEntry.idempotency_key
                        == operator_payload["idempotency_key"]
                    )
                )
            )
            assert len(operator_entries) == 1
            assert operator_entries[0].relay_event_id is None
            assert operator_entries[0].relay_event_timestamp is None
            assert operator_entries[0].relay_payload_sha256 is None

        conflict_payload = {
            **delivered["succeeded"][1],
            "amount_cents": 26,
        }
        conflict_event_id = str(uuid4())
        conflict_raw = _canonical_body(conflict_payload)
        with httpx.Client(base_url=PLATFORM_URL, timeout=5) as client:
            conflict = client.post(
                "/internal/channel-costs",
                content=conflict_raw,
                headers=_signature_headers(
                    conflict_raw,
                    event_id=conflict_event_id,
                ),
            )
            assert conflict.status_code == 409, conflict.text

            unsigned = client.post(
                "/internal/channel-costs",
                content=conflict_raw,
                headers={
                    "Content-Type": "application/json",
                    "X-Internal-Service-Token": INTERNAL_TOKEN,
                },
            )
            assert unsigned.status_code == 401

            bad_signature = client.post(
                "/internal/channel-costs",
                content=conflict_raw,
                headers={
                    **_signature_headers(
                        conflict_raw,
                        event_id=str(uuid4()),
                    ),
                    "X-Relay-Signature": "v1=" + ("0" * 64),
                },
            )
            assert bad_signature.status_code == 401

            internal_token_as_hmac = client.post(
                "/internal/channel-costs",
                content=conflict_raw,
                headers=_signature_headers(
                    conflict_raw,
                    event_id=str(uuid4()),
                    secret=INTERNAL_TOKEN,
                ),
            )
            assert internal_token_as_hmac.status_code == 401
    finally:
        platform_engine.dispose()
        new_api_engine.dispose()
