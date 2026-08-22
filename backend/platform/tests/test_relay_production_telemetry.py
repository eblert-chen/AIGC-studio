from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import time
from uuid import uuid4

import pytest
from sqlalchemy import select

from platform_api.models import (
    Company,
    GenerationTask,
    ModelDefinition,
    RelayOperationsSnapshot,
    RelayRouteOperationsSnapshot,
    RelayTaskStage,
    RelayTaskStageEvent,
    TaskStatus,
    User,
)
from platform_api.services.admin_analytics import AdminAnalyticsService
from platform_api.services.relay_telemetry import (
    RelayTaskStagePayload,
    RelayTelemetryVerifier,
)

from .conftest import TEST_RELAY_TELEMETRY_SIGNING_SECRET


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


def _signed_headers(
    body: bytes,
    *,
    event_id: str | None = None,
    timestamp: int | None = None,
) -> dict[str, str]:
    event_id = event_id or str(uuid4())
    timestamp_text = str(timestamp or int(time.time()))
    digest = hmac.new(
        TEST_RELAY_TELEMETRY_SIGNING_SECRET.encode(),
        timestamp_text.encode() + b"." + event_id.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Internal-Service-Token": "test-internal-token",
        "X-Relay-Event-ID": event_id,
        "X-Relay-Timestamp": timestamp_text,
        "X-Relay-Signature": f"v1={digest}",
        "X-Request-ID": str(uuid4()),
    }


def _seed_bound_task(app, *, now: datetime) -> tuple[Company, GenerationTask]:
    with app.state.session_factory() as session:
        company = Company(name="Relay telemetry company")
        user = User(
            email=f"relay-telemetry-{uuid4()}@example.com",
            display_name="Relay Telemetry",
        )
        model = ModelDefinition(
            slug=f"relay-telemetry-{uuid4()}",
            display_name="Relay telemetry model",
            provider_key="relay-physical-model",
            billing_mode="per_item",
            active=True,
        )
        session.add_all((company, user, model))
        session.flush()
        task = GenerationTask(
            company_id=company.id,
            user_id=user.id,
            model_id=model.id,
            idempotency_key=f"relay-telemetry-{uuid4()}",
            request_fingerprint="a" * 64,
            status=TaskStatus.PROCESSING,
            request_payload={},
            quote_cents=1,
            pricing_snapshot={},
            capability_snapshot={},
            reserved_cents=1,
            relay_job_id=str(uuid4()),
            created_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=5),
        )
        session.add(task)
        session.commit()
        return company, task


def _task_stage_payload(
    company: Company,
    task: GenerationTask,
    *,
    occurred_at: datetime,
) -> dict:
    return {
        "schema_version": 1,
        "company_id": company.id,
        "task_id": task.id,
        "relay_job_id": task.relay_job_id,
        "stage": "artifact_stored",
        "occurred_at": occurred_at.isoformat(),
        "channel_key": "official-primary",
        "channel_type": "official",
        "route_id": 17,
        "provider_task_id": "provider-task-42",
        "duration_ms": 1250,
        "error_code": "",
    }


def _snapshot_payload(*, observed_at: datetime) -> dict:
    return {
        "schema_version": 1,
        "observed_at": observed_at.isoformat(),
        "expires_at": (observed_at + timedelta(minutes=10)).isoformat(),
        "window_started_at": (observed_at - timedelta(hours=1)).isoformat(),
        "monitor_fresh": True,
        "monitor_last_completed_at": (
            observed_at - timedelta(seconds=10)
        ).isoformat(),
        "routes": [
            {
                "route_id": 17,
                "channel_key": "official-primary",
                "channel_type": "official",
                "provider_name": "official-provider",
                "model": "relay-physical-model",
                "mode": "text_to_video",
                "enabled": True,
                "production_ready": True,
                "health_status": "healthy",
                "failure_code": "",
                "last_probe_at": (
                    observed_at - timedelta(seconds=10)
                ).isoformat(),
                "rpm_limit": 60,
                "rpm_used": 14,
                "active_task_count": 2,
                "task_capacity": 8,
                "cooling_account_count": 1,
                "invalid_account_count": 2,
                "busy_account_count": 1,
                "rate_limited_account_count": 0,
                "successful_task_count": 91,
                "failed_task_count": 9,
                "latency_p50_ms": 12000,
                "latency_p95_ms": 48000,
            }
        ],
        "account_pool": {
            "total_accounts": 10,
            "active_accounts": 7,
            "cooling_accounts": 1,
            "invalid_accounts": 2,
            "busy_accounts": 1,
            "rate_limited_accounts": 0,
            "active_task_count": 2,
            "task_capacity": 8,
        },
        "tasks": {
            "queued": 3,
            "submitting": 1,
            "submission_unknown": 1,
            "provider_processing": 2,
            "artifact_transferring": 1,
            "succeeded": 91,
            "failed": 9,
            "cancelled": 1,
            "rate_limited_count": 4,
            "failover_count": 5,
        },
        "deliveries": {
            "pending_alert_count": 6,
            "dead_letter_alert_count": 1,
            "oldest_pending_alert_at": (
                observed_at - timedelta(minutes=15)
            ).isoformat(),
            "pending_cost_count": 7,
            "dead_letter_cost_count": 2,
            "pending_task_stage_count": 8,
            "dead_letter_task_stage_count": 3,
            "pending_snapshot_count": 1,
            "dead_letter_snapshot_count": 0,
        },
        "costs": {
            "successful_relay_jobs": 91,
            "explicit_cost_relay_jobs": 88,
            "delivered_cost_relay_jobs": 86,
            "incomplete_relay_jobs": 3,
            "native_billing_reconciliation_jobs": 2,
            "reconciliation_complete": False,
        },
    }


def test_task_stage_is_signed_bound_append_only_and_idempotent(app, client):
    now = datetime.now(timezone.utc)
    company, task = _seed_bound_task(app, now=now)
    body = _json_bytes(_task_stage_payload(company, task, occurred_at=now))
    event_id = str(uuid4())
    headers = _signed_headers(body, event_id=event_id)

    created = client.post(
        "/internal/relay/task-stages", content=body, headers=headers
    )
    assert created.status_code == 201, created.text
    assert created.json()["duplicate"] is False
    assert created.headers["X-Relay-Telemetry-Duplicate"] == "false"

    replay = client.post(
        "/internal/relay/task-stages", content=body, headers=headers
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["duplicate"] is True

    conflicting_body = body.replace(b'"duration_ms":1250', b'"duration_ms":1251')
    conflict = client.post(
        "/internal/relay/task-stages",
        content=conflicting_body,
        headers=_signed_headers(conflicting_body, event_id=event_id),
    )
    assert conflict.status_code == 409, conflict.text

    with app.state.session_factory() as session:
        entry = session.get(RelayTaskStageEvent, event_id)
        assert entry is not None
        assert entry.stage == RelayTaskStage.ARTIFACT_STORED
        assert entry.duration_ms == 1250
        entry.error_code = "MUTATION_MUST_FAIL"
        with pytest.raises(RuntimeError, match="immutable"):
            session.flush()
        session.rollback()


def test_task_stage_rejects_unbound_relay_job(app, client):
    now = datetime.now(timezone.utc)
    company, task = _seed_bound_task(app, now=now)
    payload = _task_stage_payload(company, task, occurred_at=now)
    payload["relay_job_id"] = str(uuid4())
    body = _json_bytes(payload)
    response = client.post(
        "/internal/relay/task-stages",
        content=body,
        headers=_signed_headers(body),
    )
    assert response.status_code == 404, response.text
    with app.state.session_factory() as session:
        assert session.scalar(select(RelayTaskStageEvent.id)) is None


def test_operations_snapshot_drives_real_channel_health_and_readiness(app, client):
    now = datetime.now(timezone.utc)
    company, task = _seed_bound_task(app, now=now)
    stage_body = _json_bytes(_task_stage_payload(company, task, occurred_at=now))
    assert client.post(
        "/internal/relay/task-stages",
        content=stage_body,
        headers=_signed_headers(stage_body),
    ).status_code == 201

    with app.state.session_factory() as session:
        before = AdminAnalyticsService.channel_health_summary(
            session,
            start=now - timedelta(hours=2),
            end=now + timedelta(hours=1),
        )
        assert before["source_status"] == "unavailable"
        assert before["account_pool_metrics"]["rpm_used"] is None

    snapshot_body = _json_bytes(
        _snapshot_payload(observed_at=now - timedelta(seconds=1))
    )
    response = client.post(
        "/internal/relay/operations-snapshots",
        content=snapshot_body,
        headers=_signed_headers(snapshot_body),
    )
    assert response.status_code == 201, response.text
    snapshot_id = response.json()["event_id"]

    with app.state.session_factory() as session:
        snapshot = session.get(RelayOperationsSnapshot, snapshot_id)
        assert snapshot is not None
        route = session.scalar(
            select(RelayRouteOperationsSnapshot).where(
                RelayRouteOperationsSnapshot.snapshot_id == snapshot_id
            )
        )
        assert route is not None
        assert route.latency_p95_ms == 48000

        health = AdminAnalyticsService.channel_health_summary(
            session,
            start=now - timedelta(hours=2),
            end=now + timedelta(hours=1),
        )
        assert health["source_status"] == "available"
        assert health["freshness"] == "fresh"
        assert health["last_snapshot_at"] is not None
        assert health["account_pool_metrics"]["active_account_count"] == 7
        assert health["account_pool_metrics"]["cooling_account_count"] == 1
        assert health["account_pool_metrics"]["invalid_account_count"] == 2
        assert health["account_pool_metrics"]["rpm_limit"] == 60
        assert health["account_pool_metrics"]["rpm_used"] == 14
        assert health["account_pool_metrics"]["failover_count"] == 5
        assert health["account_pool_metrics"]["pending_alert_count"] == 6
        assert health["channels"][0]["observed_success_rate"] == 0.91
        assert health["channels"][0]["latency_p50_ms"] == 12000
        assert health["channels"][0]["latency_p95_ms"] == 48000
        assert health["channels"][0]["provider_cost_cents"] is None
        assert (
            health["channels"][0]["provider_cost_data_status"]
            == "unavailable"
        )

        operations = AdminAnalyticsService.task_operations(
            session,
            start=now - timedelta(hours=2),
            end=now + timedelta(hours=1),
        )
        assert operations["relay_stage_source_status"] == "available"
        assert operations["relay_stage_task_counts"]["artifact_stored"] == 1
        assert operations["artifact_pipeline"]["stored_duration_ms"]["p95"] == 1250

        readiness = AdminAnalyticsService.data_readiness(session, now=now)
        assert readiness["sources"]["relay_telemetry"]["source_status"] == "available"
        assert readiness["sources"]["task_stages"]["source_status"] == "available"
        assert readiness["sources"]["channel_costs"]["source_status"] != "available"
        assert readiness["production_data_ready"] is False


def test_operations_snapshot_contract_rejects_drift(app, client):
    now = datetime.now(timezone.utc)
    payload = _snapshot_payload(observed_at=now)
    payload["routes"][0]["route_id"] = 0
    body = _json_bytes(payload)
    assert client.post(
        "/internal/relay/operations-snapshots",
        content=body,
        headers=_signed_headers(body),
    ).status_code == 422

    payload = _snapshot_payload(observed_at=now - timedelta(minutes=6))
    body = _json_bytes(payload)
    assert client.post(
        "/internal/relay/operations-snapshots",
        content=body,
        headers=_signed_headers(body),
    ).status_code == 409

    payload = _snapshot_payload(observed_at=now)
    payload["expires_at"] = (now + timedelta(minutes=16)).isoformat()
    body = _json_bytes(payload)
    assert client.post(
        "/internal/relay/operations-snapshots",
        content=body,
        headers=_signed_headers(body),
    ).status_code == 409

    payload = _snapshot_payload(observed_at=now)
    payload["window_started_at"] = payload["observed_at"]
    body = _json_bytes(payload)
    assert client.post(
        "/internal/relay/operations-snapshots",
        content=body,
        headers=_signed_headers(body),
    ).status_code == 422


def test_cross_language_signature_vector_is_stable():
    secret = "cross-language-telemetry-secret-32-bytes!!"
    event_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    timestamp = "1786320000"
    body = (
        b'{"schema_version":1,"company_id":"11111111-1111-4111-8111-111111111111",'
        b'"task_id":"22222222-2222-4222-8222-222222222222",'
        b'"relay_job_id":"33333333-3333-4333-8333-333333333333",'
        b'"stage":"provider_processing","occurred_at":"2026-08-10T00:00:00Z",'
        b'"channel_key":"official-primary","channel_type":"official",'
        b'"route_id":17,"provider_task_id":"provider-42","duration_ms":1250,'
        b'"error_code":""}'
    )
    expected = "9184d12ad56c9b0aaf9e862cdc7d677826e22f1186b4bf29386d12ccdedaca76"
    actual = hmac.new(
        secret.encode(),
        timestamp.encode() + b"." + event_id.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    assert actual == expected

    verifier = RelayTelemetryVerifier(
        secret,
        clock=lambda: float(timestamp),
    )
    payload, digest, delivered_at, verified_event_id = verifier.verify(
        body,
        event_id=event_id,
        timestamp=timestamp,
        signature=f"v1={expected}",
        payload_type=RelayTaskStagePayload,
    )
    assert payload.route_id == 17
    assert verified_event_id == event_id
    assert digest == hashlib.sha256(body).hexdigest()
    assert delivered_at == datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
