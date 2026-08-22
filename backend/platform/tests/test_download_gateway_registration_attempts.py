from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError

from platform_api.download_gateway import DownloadGatewayClient
from platform_api.models import (
    DownloadGatewayRegistrationAttempt,
    DownloadGatewayRegistrationStatus,
    DownloadRecord,
)
from platform_api.relay_client import HttpxRelayClient
from platform_api.services.download_gateway_registrations import (
    DownloadGatewayAttemptCipher,
)
from platform_api.services.reports import DownloadRecordService

from .test_artifact_bridge_and_production_safety import (
    ASSET_ID,
    make_task_downloadable,
)
from .test_download_gateway_binding import (
    GATEWAY_SIGNING_SECRET,
    GATEWAY_TOKEN,
    _download_payload,
)
from .test_relay_boundary import recharge_and_create


def _ticket_response(request: httpx.Request, *, now: datetime) -> httpx.Response:
    payload = json.loads(request.content)
    ticket_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    token = base64.urlsafe_b64encode(
        hashlib.sha256(ticket_id.encode("utf-8")).digest()
    ).rstrip(b"=").decode("ascii")
    ticket_url = f"https://downloads.example.com/downloads/{token}"
    return httpx.Response(
        201,
        headers={"Location": ticket_url},
        json={
            "api_version": "v1",
            "schema_version": 1,
            "download_record_id": payload["download_record_id"],
            "company_id": payload["company_id"],
            "task_id": payload["task_id"],
            "asset_id": payload["asset_id"],
            "issuance_request_id": payload["issuance_request_id"],
            "transfer_reference": payload["transfer_reference"],
            "gateway_ticket_id": ticket_id,
            "one_time": True,
            "ticket_url": ticket_url,
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=120)).isoformat(),
            "expires_seconds": 120,
        },
    )


def _configure_download(
    app,
    *,
    now: datetime,
    gateway_handler,
) -> tuple[dict[str, object], list[httpx.Request]]:
    relay_payload = _download_payload(now=now)
    relay_requests: list[httpx.Request] = []

    def relay_handler(request: httpx.Request) -> httpx.Response:
        relay_requests.append(request)
        return httpx.Response(200, json=relay_payload)

    app.state.relay_client = HttpxRelayClient(
        base_url="https://relay.example.com",
        client_id="platform-service",
        api_key="server-only-secret",
        transport=httpx.MockTransport(relay_handler),
    )
    app.state.download_gateway_client = DownloadGatewayClient(
        registration_url=(
            "https://download-gateway.example.com/internal/v1/download-tickets"
        ),
        public_base_url="https://downloads.example.com",
        service_token=GATEWAY_TOKEN,
        signing_secret=GATEWAY_SIGNING_SECRET,
        transport=httpx.MockTransport(gateway_handler),
        clock=lambda: now.timestamp(),
    )
    app.state.download_gateway_registration_service = None
    return relay_payload, relay_requests


def _make_downloadable(app, client, tenant, tenant_headers, suffix: str):
    task = recharge_and_create(
        app,
        client,
        tenant,
        tenant_headers,
        id_suffix=suffix,
    )
    make_task_downloadable(app, task["id"])
    path = (
        f"/api/v1/companies/{tenant['company_id']}/tasks/{task['id']}"
        f"/artifacts/{ASSET_ID}/download"
    )
    return task, path


def _attempt(app) -> DownloadGatewayRegistrationAttempt:
    with app.state.session_factory() as session:
        attempt = session.scalar(select(DownloadGatewayRegistrationAttempt))
        assert attempt is not None
        session.expunge(attempt)
        return attempt


def _make_retry_due(app, attempt_id: str) -> None:
    with app.state.session_factory.begin() as session:
        session.execute(
            update(DownloadGatewayRegistrationAttempt)
            .where(DownloadGatewayRegistrationAttempt.id == attempt_id)
            .values(next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )


def test_unknown_submission_replays_exact_encrypted_body_and_request_id(
    app,
    client,
    tenant,
    tenant_headers,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    gateway_requests: list[httpx.Request] = []

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        gateway_requests.append(request)
        if len(gateway_requests) == 1:
            raise httpx.ReadTimeout("lost acknowledgement", request=request)
        return _ticket_response(request, now=now)

    relay_payload, relay_requests = _configure_download(
        app,
        now=now,
        gateway_handler=gateway_handler,
    )
    _, path = _make_downloadable(
        app, client, tenant, tenant_headers, "durable-unknown"
    )
    headers = {**tenant_headers, "X-Request-ID": "durable-download-request"}
    first = client.get(path, headers=headers)
    assert first.status_code == 503
    assert first.headers["cache-control"] == "private, no-store, max-age=0"
    assert first.headers["pragma"] == "no-cache"

    pending = _attempt(app)
    assert pending.status == DownloadGatewayRegistrationStatus.RETRY
    assert pending.request_ciphertext is not None
    assert pending.request_nonce is not None
    assert pending.response_ciphertext is None
    source_url = str(relay_payload["url"])
    assert source_url.encode("utf-8") not in bytes(pending.request_ciphertext)
    assert source_url not in repr(pending.__dict__)

    _make_retry_due(app, pending.id)
    second = client.get(path, headers=headers)
    assert second.status_code == 200, second.text
    assert second.headers["cache-control"] == "private, no-store, max-age=0"
    assert second.headers["pragma"] == "no-cache"
    assert len(relay_requests) == 1
    assert len(gateway_requests) == 2
    assert gateway_requests[0].content == gateway_requests[1].content
    assert (
        gateway_requests[0].headers["x-download-gateway-request-id"]
        == gateway_requests[1].headers["x-download-gateway-request-id"]
    )
    body = json.loads(gateway_requests[0].content)
    assert body["source_expires_at"].endswith("Z")

    attached = _attempt(app)
    assert attached.status == DownloadGatewayRegistrationStatus.ATTACHED
    assert attached.request_ciphertext is None
    assert attached.response_ciphertext is not None
    assert attached.body_sha256 == hashlib.sha256(
        gateway_requests[0].content
    ).hexdigest()

    third = client.get(path, headers=headers)
    fourth = client.get(path, headers=headers)
    assert third.status_code == fourth.status_code == 200
    assert third.headers["cache-control"] == "private, no-store, max-age=0"
    assert third.json()["url"] == second.json()["url"] == fourth.json()["url"]
    assert len(gateway_requests) == 2
    replayed = _attempt(app)
    # The initial synchronous success is also recovered from the encrypted
    # response, so it cannot bypass the same expiry and metadata checks used
    # by later idempotent replays.
    assert replayed.ticket_replay_count == 3
    assert replayed.response_ciphertext is not None


def test_malformed_201_is_unknown_and_replays_instead_of_dead_lettering(
    app,
    client,
    tenant,
    tenant_headers,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    gateway_requests: list[httpx.Request] = []

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        gateway_requests.append(request)
        if len(gateway_requests) == 1:
            return httpx.Response(201, content=b'{"truncated":')
        return _ticket_response(request, now=now)

    _configure_download(app, now=now, gateway_handler=gateway_handler)
    _, path = _make_downloadable(
        app, client, tenant, tenant_headers, "durable-malformed-201"
    )
    headers = {**tenant_headers, "X-Request-ID": "durable-malformed-201"}
    assert client.get(path, headers=headers).status_code == 503
    pending = _attempt(app)
    assert pending.status == DownloadGatewayRegistrationStatus.RETRY
    assert pending.request_ciphertext is not None
    _make_retry_due(app, pending.id)
    assert client.get(path, headers=headers).status_code == 200
    assert gateway_requests[0].content == gateway_requests[1].content


def test_max_unknown_attempts_preserve_request_for_manual_reconcile(
    app,
    client,
    tenant,
    tenant_headers,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    should_succeed = False
    gateway_requests: list[httpx.Request] = []

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        gateway_requests.append(request)
        if not should_succeed:
            raise httpx.ReadTimeout("all acknowledgements lost", request=request)
        return _ticket_response(request, now=now)

    _configure_download(app, now=now, gateway_handler=gateway_handler)
    _, path = _make_downloadable(
        app, client, tenant, tenant_headers, "durable-max-unknown"
    )
    headers = {**tenant_headers, "X-Request-ID": "durable-max-unknown"}
    assert client.get(path, headers=headers).status_code == 503
    service = app.state.download_gateway_registration_service
    attempt_id = _attempt(app).id
    for _ in range(7):
        _make_retry_due(app, attempt_id)
        result = service.run_once()
        assert result.processed is True
    unknown = _attempt(app)
    assert unknown.status == DownloadGatewayRegistrationStatus.UNKNOWN
    assert unknown.attempt_count == 8
    assert unknown.request_ciphertext is not None
    assert service.run_once().processed is False

    should_succeed = True
    reconciled = service.reconcile(attempt_id)
    assert reconciled.status == DownloadGatewayRegistrationStatus.ATTACHED.value
    assert reconciled.download_record_id is not None
    assert len(gateway_requests) == 9
    assert len({request.content for request in gateway_requests}) == 1
    assert len(
        {
            request.headers["x-download-gateway-request-id"]
            for request in gateway_requests
        }
    ) == 1


def test_expired_lease_token_cannot_write_before_or_after_reclaim(
    app,
    client,
    tenant,
    tenant_headers,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        return _ticket_response(request, now=now)

    relay_payload, _ = _configure_download(
        app, now=now, gateway_handler=gateway_handler
    )
    task, _ = _make_downloadable(
        app, client, tenant, tenant_headers, "durable-fencing"
    )
    service = app.state.resolve_download_gateway_registration_service()
    binding = relay_payload["storage_binding"]
    from platform_api.relay_client import RelayArtifactStorageBinding

    attempt_id = service.prepare(
        company_id=tenant["company_id"],
        task_id=task["id"],
        asset_id=ASSET_ID,
        requested_by_user_id=tenant["user_id"],
        platform_request_id="durable-fencing",
        expected_size_bytes=12345,
        artifact_sha256="a" * 64,
        source_url=str(relay_payload["url"]),
        storage_binding=RelayArtifactStorageBinding.model_validate(binding),
    )
    stale = service._claim_specific(attempt_id, manual=True)
    with app.state.session_factory.begin() as session:
        session.execute(
            update(DownloadGatewayRegistrationAttempt)
            .where(DownloadGatewayRegistrationAttempt.id == attempt_id)
            .values(
                lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
            )
        )
    assert service._mark_dead(stale, error_code="stale-before-reclaim").processed is False
    assert _attempt(app).status == DownloadGatewayRegistrationStatus.PROCESSING

    successor = service._claim_specific(attempt_id, manual=True)
    assert successor.lease_token != stale.lease_token
    assert service._mark_retry(stale, error_code="stale-after-reclaim").processed is False
    won = service._mark_retry(successor, error_code="successor")
    assert won.processed is True
    assert won.status == DownloadGatewayRegistrationStatus.RETRY.value


@pytest.mark.parametrize("corruption", ["cipher", "digest", "json", "canonical"])
def test_corrupt_encrypted_attempt_dead_letters_without_nested_lock(
    app,
    client,
    tenant,
    tenant_headers,
    corruption,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    relay_payload, _ = _configure_download(
        app,
        now=now,
        gateway_handler=lambda request: _ticket_response(request, now=now),
    )
    task, _ = _make_downloadable(
        app, client, tenant, tenant_headers, f"durable-corrupt-{corruption}"
    )
    service = app.state.resolve_download_gateway_registration_service()
    from platform_api.relay_client import RelayArtifactStorageBinding

    attempt_id = service.prepare(
        company_id=tenant["company_id"],
        task_id=task["id"],
        asset_id=ASSET_ID,
        requested_by_user_id=tenant["user_id"],
        platform_request_id=f"durable-corrupt-{corruption}",
        expected_size_bytes=12345,
        artifact_sha256="a" * 64,
        source_url=str(relay_payload["url"]),
        storage_binding=RelayArtifactStorageBinding.model_validate(
            relay_payload["storage_binding"]
        ),
    )
    cipher: DownloadGatewayAttemptCipher = service.cipher
    with app.state.session_factory.begin() as session:
        attempt = session.get(DownloadGatewayRegistrationAttempt, attempt_id)
        if corruption == "cipher":
            attempt.request_ciphertext = bytes(attempt.request_ciphertext[:-1]) + bytes(
                [attempt.request_ciphertext[-1] ^ 1]
            )
        else:
            raw = cipher.decrypt(
                bytes(attempt.request_ciphertext),
                bytes(attempt.request_nonce),
                aad=service._aad(attempt, "registration-request"),
            )
            if corruption == "digest":
                replacement = raw + b" "
            elif corruption == "json":
                replacement = b'{"truncated":'
                attempt.body_sha256 = hashlib.sha256(replacement).hexdigest()
            else:
                replacement = json.dumps(
                    json.loads(raw), sort_keys=True, indent=2
                ).encode("utf-8")
                attempt.body_sha256 = hashlib.sha256(replacement).hexdigest()
            encrypted, nonce = cipher.encrypt(
                replacement,
                aad=service._aad(attempt, "registration-request"),
            )
            attempt.request_ciphertext = encrypted
            attempt.request_nonce = nonce
    claim = service._claim_specific(attempt_id, manual=True)
    result, ticket = service._submit(claim)
    assert ticket is None
    assert result.status == DownloadGatewayRegistrationStatus.DEAD.value
    dead = _attempt(app)
    assert dead.request_ciphertext is None
    assert dead.response_ciphertext is None


def test_attach_database_failure_remains_registered_then_recovers(
    app,
    client,
    tenant,
    tenant_headers,
    monkeypatch,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _configure_download(
        app,
        now=now,
        gateway_handler=lambda request: _ticket_response(request, now=now),
    )
    _, path = _make_downloadable(
        app, client, tenant, tenant_headers, "durable-attach-failure"
    )
    original = DownloadRecordService.append_registered_gateway_attempt

    def unavailable(*args, **kwargs):
        raise OperationalError("insert", {}, RuntimeError("database unavailable"))

    monkeypatch.setattr(
        DownloadRecordService,
        "append_registered_gateway_attempt",
        unavailable,
    )
    headers = {**tenant_headers, "X-Request-ID": "durable-attach-failure"}
    failed = client.get(path, headers=headers)
    assert failed.status_code == 503
    registered = _attempt(app)
    assert registered.status == DownloadGatewayRegistrationStatus.REGISTERED
    assert registered.request_ciphertext is None
    assert registered.response_ciphertext is not None
    with app.state.session_factory() as session:
        assert session.get(DownloadRecord, registered.download_record_id) is None

    monkeypatch.setattr(
        DownloadRecordService,
        "append_registered_gateway_attempt",
        original,
    )
    with app.state.session_factory.begin() as session:
        session.execute(
            update(DownloadGatewayRegistrationAttempt)
            .where(DownloadGatewayRegistrationAttempt.id == registered.id)
            .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
    recovered = app.state.download_gateway_registration_service.run_once()
    assert recovered.status == DownloadGatewayRegistrationStatus.ATTACHED.value
    with app.state.session_factory() as session:
        assert session.get(DownloadRecord, registered.download_record_id) is not None


@pytest.mark.parametrize("attach_path", ["normal", "integrity-conflict"])
def test_registered_ticket_expiry_boundary_dead_letters_before_attach(
    app,
    client,
    tenant,
    tenant_headers,
    attach_path,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    relay_payload, _ = _configure_download(
        app,
        now=now,
        gateway_handler=lambda request: _ticket_response(request, now=now),
    )
    task, _ = _make_downloadable(
        app,
        client,
        tenant,
        tenant_headers,
        f"expired-before-attach-{attach_path}",
    )
    service = app.state.resolve_download_gateway_registration_service()
    from platform_api.relay_client import RelayArtifactStorageBinding

    attempt_id = service.prepare(
        company_id=tenant["company_id"],
        task_id=task["id"],
        asset_id=ASSET_ID,
        requested_by_user_id=tenant["user_id"],
        platform_request_id=f"expired-before-attach-{attach_path}",
        expected_size_bytes=12345,
        artifact_sha256="a" * 64,
        source_url=str(relay_payload["url"]),
        storage_binding=RelayArtifactStorageBinding.model_validate(
            relay_payload["storage_binding"]
        ),
    )
    claim = service._claim_specific(attempt_id, manual=True)
    registered, _ = service._submit(claim)
    assert registered.status == DownloadGatewayRegistrationStatus.REGISTERED.value

    # Use the database clock itself for the exact invalid boundary. Attachment
    # requires a strictly future ticket, not merely a non-null expiry.
    with app.state.session_factory.begin() as session:
        session.execute(
            update(DownloadGatewayRegistrationAttempt)
            .where(DownloadGatewayRegistrationAttempt.id == attempt_id)
            .values(gateway_expires_at=func.current_timestamp())
        )

    if attach_path == "normal":
        result = service._attach(claim)
    else:
        result = service._resolve_attach_integrity_conflict(claim)

    assert result.status == DownloadGatewayRegistrationStatus.DEAD.value
    dead = _attempt(app)
    assert dead.last_error_code == "registered_ticket_expired_before_attach"
    assert dead.request_ciphertext is None
    assert dead.response_ciphertext is None
    assert dead.gateway_ticket_id is not None
    assert dead.gateway_ticket_url_sha256 is not None
    with app.state.session_factory() as session:
        assert session.get(DownloadRecord, dead.download_record_id) is None


def test_attach_unique_key_collision_dead_letters_instead_of_retry_loop(
    app,
    client,
    tenant,
    tenant_headers,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    relay_payload, _ = _configure_download(
        app,
        now=now,
        gateway_handler=lambda request: _ticket_response(request, now=now),
    )
    task, _ = _make_downloadable(
        app, client, tenant, tenant_headers, "durable-attach-conflict"
    )
    service = app.state.resolve_download_gateway_registration_service()
    from platform_api.relay_client import RelayArtifactStorageBinding

    attempt_id = service.prepare(
        company_id=tenant["company_id"],
        task_id=task["id"],
        asset_id=ASSET_ID,
        requested_by_user_id=tenant["user_id"],
        platform_request_id="durable-attach-conflict",
        expected_size_bytes=12345,
        artifact_sha256="a" * 64,
        source_url=str(relay_payload["url"]),
        storage_binding=RelayArtifactStorageBinding.model_validate(
            relay_payload["storage_binding"]
        ),
    )
    claim = service._claim_specific(attempt_id, manual=True)
    registered, _ = service._submit(claim)
    assert registered.status == DownloadGatewayRegistrationStatus.REGISTERED.value
    attempt = _attempt(app)
    with app.state.session_factory.begin() as session:
        session.add(
            DownloadRecord(
                id=str(uuid4()),
                company_id=attempt.company_id,
                task_id=attempt.task_id,
                asset_id=attempt.asset_id,
                requested_by_user_id=attempt.requested_by_user_id,
                expires_seconds=attempt.gateway_expires_seconds,
                expires_at=attempt.gateway_expires_at,
                request_id="conflicting-download-record",
                storage_binding_version=1,
                storage_provider=attempt.storage_provider,
                storage_endpoint_host=attempt.storage_endpoint_host,
                storage_bucket=attempt.storage_bucket,
                storage_object_key=attempt.storage_object_key,
                storage_version_id=None,
                source_url_sha256=attempt.source_url_sha256,
                relay_issued_at=attempt.relay_issued_at,
                relay_expires_at=attempt.relay_expires_at,
                gateway_registration_request_id=str(uuid4()),
                gateway_ticket_id=attempt.gateway_ticket_id,
                gateway_ticket_url_sha256=attempt.gateway_ticket_url_sha256,
                gateway_issued_at=attempt.gateway_issued_at,
                gateway_expires_at=attempt.gateway_expires_at,
                gateway_transfer_reference=str(uuid4()),
                created_at=now,
            )
        )
    result = service._attach(claim)
    assert result.status == DownloadGatewayRegistrationStatus.DEAD.value
    dead = _attempt(app)
    assert dead.last_error_code == "gateway_download_record_conflict"
    assert dead.response_ciphertext is None
    assert service.run_once().processed is False


def test_unknown_reconciles_exact_expired_receipt_without_download_record(
    app,
    client,
    tenant,
    tenant_headers,
    monkeypatch,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    future = now + timedelta(seconds=700)
    requests: list[httpx.Request] = []
    acknowledgement_bodies: list[bytes] = []

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout("lost 201", request=request)
        payload = json.loads(request.content)
        receipt = {
            "api_version": "v1",
            "schema_version": 1,
            "outcome": "committed_expired",
            "registration_request_id": request.headers[
                "x-download-gateway-request-id"
            ],
            "registration_payload_sha256": hashlib.sha256(
                request.content
            ).hexdigest(),
            "gateway_ticket_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "download_record_id": payload["download_record_id"],
            "company_id": payload["company_id"],
            "task_id": payload["task_id"],
            "asset_id": payload["asset_id"],
            "issuance_request_id": payload["issuance_request_id"],
            "transfer_reference": payload["transfer_reference"],
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=120)).isoformat(),
        }
        response = httpx.Response(410, json=receipt)
        acknowledgement_bodies.append(response.content)
        return response

    relay_payload, _ = _configure_download(
        app, now=now, gateway_handler=gateway_handler
    )
    _, path = _make_downloadable(
        app, client, tenant, tenant_headers, "durable-expired-receipt"
    )
    service = app.state.resolve_download_gateway_registration_service()
    service.max_attempts = 1
    headers = {**tenant_headers, "X-Request-ID": "durable-expired-receipt"}
    first = client.get(path, headers=headers)
    assert first.status_code == 503
    unknown = _attempt(app)
    assert unknown.status == DownloadGatewayRegistrationStatus.UNKNOWN
    assert unknown.request_ciphertext is not None

    app.state.download_gateway_client._clock = lambda: future.timestamp()
    import platform_api.services.download_gateway_registrations as registrations

    monkeypatch.setattr(registrations, "_database_now", lambda session: future)
    result = service.reconcile(unknown.id)
    assert result.status == (
        DownloadGatewayRegistrationStatus.RECONCILED_EXPIRED.value
    )
    assert requests[0].content == requests[1].content
    assert (
        requests[0].headers["x-download-gateway-request-id"]
        == requests[1].headers["x-download-gateway-request-id"]
    )
    reconciled = _attempt(app)
    assert reconciled.request_ciphertext is None
    assert reconciled.response_ciphertext is None
    assert reconciled.reconciled_at is not None
    assert reconciled.reconciliation_ack_sha256 == hashlib.sha256(
        acknowledgement_bodies[0]
    ).hexdigest()
    assert reconciled.gateway_ticket_id == (
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    )
    with app.state.session_factory() as session:
        assert session.get(DownloadRecord, reconciled.download_record_id) is None
    assert str(relay_payload["url"]) not in repr(reconciled.__dict__)
    assert b"ticket_url" not in acknowledgement_bodies[0]
    assert str(relay_payload["url"]).encode("utf-8") not in acknowledgement_bodies[0]
    terminal = client.get(path, headers=headers)
    assert terminal.status_code == 410
    assert terminal.headers["cache-control"] == "private, no-store, max-age=0"
    assert len(requests) == 2


def test_mismatched_committed_expired_receipt_remains_unknown_with_exact_body(
    app,
    client,
    tenant,
    tenant_headers,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            410,
            json={
                "api_version": "v1",
                "schema_version": 1,
                "outcome": "committed_expired",
                "registration_request_id": request.headers[
                    "x-download-gateway-request-id"
                ],
                "registration_payload_sha256": "0" * 64,
                "gateway_ticket_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "download_record_id": payload["download_record_id"],
                "company_id": payload["company_id"],
                "task_id": payload["task_id"],
                "asset_id": payload["asset_id"],
                "issuance_request_id": payload["issuance_request_id"],
                "transfer_reference": payload["transfer_reference"],
                "issued_at": (now - timedelta(seconds=120)).isoformat(),
                "expires_at": (now - timedelta(seconds=1)).isoformat(),
            },
        )

    _configure_download(app, now=now, gateway_handler=gateway_handler)
    _, path = _make_downloadable(
        app, client, tenant, tenant_headers, "durable-bad-expired-receipt"
    )
    response = client.get(
        path,
        headers={**tenant_headers, "X-Request-ID": "durable-bad-expired-receipt"},
    )
    assert response.status_code == 503
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    attempt = _attempt(app)
    assert attempt.status == DownloadGatewayRegistrationStatus.RETRY
    assert attempt.request_ciphertext is not None
    assert attempt.reconciled_at is None
    assert attempt.reconciliation_ack_sha256 is None


def test_malformed_committed_expired_receipt_replays_exact_registration(
    app,
    client,
    tenant,
    tenant_headers,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    requests: list[httpx.Request] = []

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(410, content=b'{"truncated":')
        return _ticket_response(request, now=now)

    _configure_download(app, now=now, gateway_handler=gateway_handler)
    _, path = _make_downloadable(
        app, client, tenant, tenant_headers, "durable-malformed-410"
    )
    headers = {**tenant_headers, "X-Request-ID": "durable-malformed-410"}
    first = client.get(path, headers=headers)
    assert first.status_code == 503
    pending = _attempt(app)
    assert pending.status == DownloadGatewayRegistrationStatus.RETRY
    assert pending.request_ciphertext is not None
    assert pending.reconciled_at is None

    _make_retry_due(app, pending.id)
    second = client.get(path, headers=headers)
    assert second.status_code == 200
    assert len(requests) == 2
    assert requests[0].content == requests[1].content
    assert (
        requests[0].headers["x-download-gateway-request-id"]
        == requests[1].headers["x-download-gateway-request-id"]
    )


@pytest.mark.parametrize(
    ("issued_offset", "expires_offset"),
    [
        (0, 29),
        (0, 301),
        (250, 550),
    ],
)
def test_untrusted_committed_expired_window_remains_unknown(
    app,
    client,
    tenant,
    tenant_headers,
    issued_offset,
    expires_offset,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    future = now + timedelta(seconds=700)

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            410,
            json={
                "api_version": "v1",
                "schema_version": 1,
                "outcome": "committed_expired",
                "registration_request_id": request.headers[
                    "x-download-gateway-request-id"
                ],
                "registration_payload_sha256": hashlib.sha256(
                    request.content
                ).hexdigest(),
                "gateway_ticket_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "download_record_id": payload["download_record_id"],
                "company_id": payload["company_id"],
                "task_id": payload["task_id"],
                "asset_id": payload["asset_id"],
                "issuance_request_id": payload["issuance_request_id"],
                "transfer_reference": payload["transfer_reference"],
                "issued_at": (now + timedelta(seconds=issued_offset)).isoformat(),
                "expires_at": (now + timedelta(seconds=expires_offset)).isoformat(),
            },
        )

    _configure_download(app, now=now, gateway_handler=gateway_handler)
    app.state.download_gateway_client._clock = lambda: future.timestamp()
    _, path = _make_downloadable(
        app,
        client,
        tenant,
        tenant_headers,
        f"durable-bad-window-{issued_offset}",
    )
    response = client.get(
        path,
        headers={
            **tenant_headers,
            "X-Request-ID": f"durable-bad-window-{issued_offset}",
        },
    )
    assert response.status_code == 503
    attempt = _attempt(app)
    assert attempt.status == DownloadGatewayRegistrationStatus.RETRY
    assert attempt.request_ciphertext is not None
    assert attempt.reconciled_at is None


@pytest.mark.parametrize(
    ("field_name", "mutate"),
    [
        ("gateway_ticket_url_sha256", lambda value: "c" * 64),
        ("gateway_ticket_id", lambda value: "cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        ("gateway_issued_at", lambda value: value + timedelta(seconds=1)),
        ("gateway_expires_at", lambda value: value + timedelta(seconds=1)),
        ("gateway_expires_seconds", lambda value: value + 1),
    ],
)
def test_encrypted_ticket_replay_rejects_mutated_database_metadata(
    app,
    client,
    tenant,
    tenant_headers,
    field_name,
    mutate,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _configure_download(
        app,
        now=now,
        gateway_handler=lambda request: _ticket_response(request, now=now),
    )
    _, path = _make_downloadable(
        app, client, tenant, tenant_headers, f"durable-replay-{field_name}"
    )
    headers = {
        **tenant_headers,
        "X-Request-ID": f"durable-replay-{field_name}",
    }
    assert client.get(path, headers=headers).status_code == 200
    attempt = _attempt(app)
    with app.state.session_factory.begin() as session:
        current = session.get(DownloadGatewayRegistrationAttempt, attempt.id)
        setattr(current, field_name, mutate(getattr(current, field_name)))
    replay = client.get(path, headers=headers)
    assert replay.status_code == 502
    assert replay.headers["cache-control"] == "private, no-store, max-age=0"


def test_expired_ticket_response_is_crypto_shredded_idempotently(
    app,
    client,
    tenant,
    tenant_headers,
    monkeypatch,
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _configure_download(
        app,
        now=now,
        gateway_handler=lambda request: _ticket_response(request, now=now),
    )
    _, path = _make_downloadable(
        app, client, tenant, tenant_headers, "durable-ticket-expiry"
    )
    headers = {**tenant_headers, "X-Request-ID": "durable-ticket-expiry"}
    assert client.get(path, headers=headers).status_code == 200
    attached = _attempt(app)
    assert attached.response_ciphertext is not None
    future = now + timedelta(seconds=121)
    import platform_api.services.download_gateway_registrations as registrations

    monkeypatch.setattr(registrations, "_database_now", lambda session: future)
    service = app.state.download_gateway_registration_service
    cleaned = service.run_once()
    assert cleaned.status == DownloadGatewayRegistrationStatus.ATTACHED.value
    shredded = _attempt(app)
    assert shredded.response_ciphertext is None
    assert shredded.response_nonce is None
    assert service.run_once().processed is False
    expired = client.get(path, headers=headers)
    assert expired.status_code == 502
    assert expired.headers["cache-control"] == "private, no-store, max-age=0"


def test_postgres_fencing_and_corrupt_ciphertext_completion(monkeypatch):
    import os

    database_url = os.getenv("PLATFORM_TEST_DATABASE_URL") or os.getenv(
        "DATABASE_URL", ""
    )
    if not database_url.startswith("postgresql"):
        pytest.skip("requires a PostgreSQL test database")

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from platform_api.config import Settings
    from platform_api.main import create_app
    from .conftest import TEST_BOOTSTRAP_TOKEN, bootstrap

    schema_name = f"download_attempt_{uuid4().hex}"
    administration_engine = create_engine(database_url, pool_pre_ping=True)
    with administration_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema_name}"')
    schema_url = make_url(database_url).update_query_dict(
        {"options": f"-csearch_path={schema_name}"}
    ).render_as_string(hide_password=False)
    engine = create_engine(schema_url, pool_pre_ping=True)
    settings = Settings(
        database_url=schema_url,
        auto_create_tables=True,
        development_header_auth_enabled=True,
        enable_bootstrap=True,
        bootstrap_token=TEST_BOOTSTRAP_TOKEN,
        internal_service_token="postgres-internal-token",
        channel_cost_signing_secret="postgres-channel-cost-secret",
        download_gateway_attempt_encryption_key_base64=(
            "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
        ),
        input_asset_signing_secret="postgres-input-signing-secret",
    )
    test_app = create_app(settings=settings, engine=engine)
    try:
        with TestClient(
            test_app,
            headers={"X-Bootstrap-Token": TEST_BOOTSTRAP_TOKEN},
        ) as test_client:
            tenant = bootstrap(test_client, "postgres-download-attempt")
            headers = {
                "X-Company-ID": tenant["company_id"],
                "X-User-ID": tenant["user_id"],
            }
            now = datetime.now(timezone.utc).replace(microsecond=0)
            relay_payload, _ = _configure_download(
                test_app,
                now=now,
                gateway_handler=lambda request: _ticket_response(
                    request, now=now
                ),
            )
            task, _ = _make_downloadable(
                test_app,
                test_client,
                tenant,
                headers,
                "postgres-fencing",
            )
            from platform_api.relay_client import RelayArtifactStorageBinding

            service = test_app.state.resolve_download_gateway_registration_service()
            attempt_id = service.prepare(
                company_id=tenant["company_id"],
                task_id=task["id"],
                asset_id=ASSET_ID,
                requested_by_user_id=tenant["user_id"],
                platform_request_id="postgres-fencing",
                expected_size_bytes=12345,
                artifact_sha256="a" * 64,
                source_url=str(relay_payload["url"]),
                storage_binding=RelayArtifactStorageBinding.model_validate(
                    relay_payload["storage_binding"]
                ),
            )
            stale = service._claim_specific(attempt_id, manual=True)
            with test_app.state.session_factory.begin() as session:
                session.execute(
                    text(
                        "UPDATE download_gateway_registration_attempts "
                        "SET lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' "
                        "WHERE id = :attempt_id"
                    ),
                    {"attempt_id": attempt_id},
                )
            assert service._mark_dead(
                stale, error_code="expired-without-reclaim"
            ).processed is False
            successor = service._claim_specific(attempt_id, manual=True)
            assert successor.lease_token != stale.lease_token
            assert service._mark_retry(
                stale, error_code="expired-after-reclaim"
            ).processed is False
            assert service._mark_retry(
                successor, error_code="successor"
            ).status == DownloadGatewayRegistrationStatus.RETRY.value

            with test_app.state.session_factory.begin() as session:
                attempt = session.get(
                    DownloadGatewayRegistrationAttempt, attempt_id
                )
                attempt.next_attempt_at = now - timedelta(seconds=1)
                attempt.request_ciphertext = (
                    bytes(attempt.request_ciphertext[:-1])
                    + bytes([attempt.request_ciphertext[-1] ^ 1])
                )
            corrupt_claim = service._claim_specific(attempt_id, manual=True)
            corrupt, ticket = service._submit(corrupt_claim)
            assert ticket is None
            assert corrupt.status == DownloadGatewayRegistrationStatus.DEAD.value
    finally:
        engine.dispose()
        with administration_engine.begin() as connection:
            connection.exec_driver_sql(
                f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'
            )
        administration_engine.dispose()
