from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from uuid import uuid4

import httpx
from pydantic import ValidationError
import pytest
from sqlalchemy import func, select

from platform_api.download_gateway import (
    DownloadGatewayClient,
    DownloadGatewayOutcomeUnknownError,
)
from platform_api.models import DownloadRecord, PersonalLedgerEntry, PersonalWalletAccount
from platform_api.relay_client import (
    HttpxRelayClient,
    RelayPermanentError,
    RelaySignedDownload,
    validate_bound_artifact_download,
)

from .conftest import (
    TEST_EDGE_COMPLETION_SERVICE_TOKEN,
    TEST_EDGE_DOWNLOAD_SIGNING_SECRET,
)
from .test_artifact_bridge_and_production_safety import (
    ASSET_ID,
    RELAY_JOB_ID,
    make_task_downloadable,
)
from .test_relay_boundary import recharge_and_create


BUCKET = "relay-output-private"
ENDPOINT_HOST = "obs.cn-north-4.myhuaweicloud.com"
OBJECT_KEY = (
    "outputs/11111111-1111-4111-8111-111111111111/"
    f"{RELAY_JOB_ID}/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
)
GATEWAY_TOKEN = "gateway-service-token-for-binding-tests"
GATEWAY_SIGNING_SECRET = "gateway-registration-signing-secret-tests"


def _download_payload(
    *,
    now: datetime,
    host: str = ENDPOINT_HOST,
    object_key: str = OBJECT_KEY,
) -> dict[str, object]:
    url = f"https://{host}/{BUCKET}/{object_key}?AccessKeyId=masked&Expires=600&Signature=masked"
    return {
        "api_version": "v1",
        "schema_version": 1,
        "url": url,
        "expires_seconds": 600,
        "storage_binding": {
            "provider": "huawei_obs",
            "endpoint_host": host,
            "bucket": BUCKET,
            "object_key": object_key,
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=600)).isoformat(),
            "url_sha256": hashlib.sha256(url.encode("utf-8")).hexdigest(),
        },
    }


def test_relay_download_binding_requires_exact_public_obs_url_and_window():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    download = RelaySignedDownload.model_validate(_download_payload(now=now))
    binding = validate_bound_artifact_download(
        download,
        production=True,
        allow_legacy=False,
        now=now,
    )
    assert binding is not None
    assert binding.bucket == BUCKET
    assert binding.object_key == OBJECT_KEY

    wrong_path = _download_payload(now=now, object_key="outputs/wrong/object")
    wrong_path["storage_binding"]["object_key"] = OBJECT_KEY
    wrong_path["storage_binding"]["url_sha256"] = hashlib.sha256(
        str(wrong_path["url"]).encode("utf-8")
    ).hexdigest()
    with pytest.raises(RelayPermanentError, match="bucket and object key"):
        validate_bound_artifact_download(
            RelaySignedDownload.model_validate(wrong_path),
            production=True,
            allow_legacy=False,
            now=now,
        )

    private = RelaySignedDownload.model_validate(
        _download_payload(now=now, host="127.0.0.1")
    )
    with pytest.raises(RelayPermanentError, match="public Huawei OBS"):
        validate_bound_artifact_download(
            private,
            production=True,
            allow_legacy=False,
            now=now,
        )


def test_relay_download_binding_rejects_missing_or_tampered_evidence():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with pytest.raises(ValidationError, match="digest"):
        RelaySignedDownload.model_validate(
            {
                **_download_payload(now=now),
                "storage_binding": {
                    **_download_payload(now=now)["storage_binding"],
                    "url_sha256": "0" * 64,
                },
            }
        )
    legacy = RelaySignedDownload.model_validate(
        {
            "api_version": "v1",
            "schema_version": 1,
            "url": "https://legacy.example.com/artifact?signature=masked",
            "expires_seconds": 300,
        }
    )
    with pytest.raises(RelayPermanentError, match="missing its storage binding"):
        validate_bound_artifact_download(
            legacy,
            production=True,
            allow_legacy=False,
            now=now,
        )


def _gateway_client(
    handler,
    *,
    now: datetime,
) -> DownloadGatewayClient:
    return DownloadGatewayClient(
        registration_url=(
            "https://download-gateway.example.com/internal/v1/download-tickets"
        ),
        public_base_url="https://downloads.example.com",
        service_token=GATEWAY_TOKEN,
        signing_secret=GATEWAY_SIGNING_SECRET,
        transport=httpx.MockTransport(handler),
        clock=lambda: now.timestamp(),
    )


def test_platform_registers_gateway_ticket_and_binds_edge_receipt(
    app,
    client,
    tenant,
    tenant_headers,
):
    task = recharge_and_create(
        app,
        client,
        tenant,
        tenant_headers,
        id_suffix="gateway-binding",
    )
    make_task_downloadable(app, task["id"])
    now = datetime.now(timezone.utc).replace(microsecond=0)
    relay_payload = _download_payload(now=now)
    source_url = str(relay_payload["url"])

    def relay_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=relay_payload)

    app.state.relay_client = HttpxRelayClient(
        base_url="https://relay.example.com",
        client_id="platform-service",
        api_key="server-only-secret",
        transport=httpx.MockTransport(relay_handler),
    )
    captured_registration: dict[str, object] = {}

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/v1/download-tickets"
        assert request.headers["x-download-gateway-token"] == GATEWAY_TOKEN
        raw_body = request.content
        timestamp = request.headers["x-download-gateway-timestamp"]
        registration_id = request.headers["x-download-gateway-request-id"]
        signing_input = (
            b"download-edge-registration.v1\nPOST\n"
            b"/internal/v1/download-tickets\n"
            + timestamp.encode("ascii")
            + b"\n"
            + registration_id.encode("ascii")
            + b"\n"
            + raw_body
        )
        expected = hmac.new(
            GATEWAY_SIGNING_SECRET.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).hexdigest()
        assert request.headers["x-download-gateway-signature"] == (
            f"sha256={expected}"
        )
        payload = json.loads(raw_body)
        captured_registration.update(payload)
        ticket_id = str(uuid4())
        ticket_token = base64.urlsafe_b64encode(
            hashlib.sha256(ticket_id.encode("utf-8")).digest()
        ).rstrip(b"=").decode("ascii")
        ticket_url = f"https://downloads.example.com/downloads/{ticket_token}"
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

    app.state.download_gateway_client = _gateway_client(
        gateway_handler,
        now=now,
    )
    path = (
        f"/api/v1/companies/{tenant['company_id']}/tasks/{task['id']}"
        f"/artifacts/{ASSET_ID}/download"
    )
    issued = client.get(
        path,
        headers={**tenant_headers, "X-Request-ID": "gateway-issuance-request"},
    )
    assert issued.status_code == 200, issued.text
    issued_body = issued.json()
    assert issued_body["url"].startswith("https://downloads.example.com/downloads/")
    assert source_url not in issued.text
    assert OBJECT_KEY not in issued.text
    assert issued_body["expires_seconds"] == 120
    assert captured_registration["source_url"] == source_url
    assert captured_registration["obs_binding"] == {
        "bucket": BUCKET,
        "object_key": OBJECT_KEY,
        "version_id": None,
    }

    with app.state.session_factory() as session:
        record = session.get(DownloadRecord, issued_body["download_record_id"])
        assert record is not None
        assert record.storage_provider == "huawei_obs"
        assert record.storage_endpoint_host == ENDPOINT_HOST
        assert record.storage_bucket == BUCKET
        assert record.storage_object_key == OBJECT_KEY
        assert record.source_url_sha256 == hashlib.sha256(
            source_url.encode("utf-8")
        ).hexdigest()
        assert record.gateway_ticket_url_sha256
        assert record.request_id == "gateway-issuance-request"
        transfer_reference = record.gateway_transfer_reference
        assert transfer_reference == captured_registration["transfer_reference"]
        assert source_url not in repr(record.__dict__)

    completed_at = datetime.now(timezone.utc)
    completion_payload = {
        "download_record_id": issued_body["download_record_id"],
        "company_id": tenant["company_id"],
        "task_id": task["id"],
        "asset_id": ASSET_ID,
        "external_event_id": "gateway-external-event-0001",
        "bytes_sent": 12345,
        "completed_at": completed_at.isoformat(),
        "artifact_sha256": "a" * 64,
        "expected_size_bytes": 12345,
        "http_status": 200,
        "transfer_scope": "full_body",
        "gateway_request_id": "gateway-issuance-request",
        "gateway_transfer_reference": transfer_reference,
    }
    raw_completion = json.dumps(
        completion_payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    event_id = str(uuid4())
    event_timestamp = str(int(completed_at.timestamp()))
    completion_signing_input = (
        b"download-completion.v1\nedge_gateway\n"
        + event_timestamp.encode("ascii")
        + b"\n"
        + event_id.encode("ascii")
        + b"\n"
        + raw_completion
    )
    signature = hmac.new(
        TEST_EDGE_DOWNLOAD_SIGNING_SECRET.encode("utf-8"),
        completion_signing_input,
        hashlib.sha256,
    ).hexdigest()
    completed = client.post(
        "/internal/artifact-download-completions/edge-gateway",
        content=raw_completion,
        headers={
            "Content-Type": "application/json",
            "X-Internal-Service-Token": TEST_EDGE_COMPLETION_SERVICE_TOKEN,
            "X-Download-Event-ID": event_id,
            "X-Download-Timestamp": event_timestamp,
            "X-Download-Signature": f"v1={signature}",
        },
    )
    assert completed.status_code == 201, completed.text
    assert completed.json()["source_evidence"] == {
        "gateway_request_id": "gateway-issuance-request",
        "gateway_transfer_reference": transfer_reference,
    }
    assert source_url not in completed.text


def test_edge_completion_endpoint_rejects_global_or_missing_scoped_token(
    app,
    client,
    internal_headers,
    edge_completion_headers,
):
    edge_endpoint = "/internal/artifact-download-completions/edge-gateway"

    # The global internal-service credential is not accepted by the edge-only
    # completion endpoint, even though it remains valid for other dependencies.
    assert (
        client.post(edge_endpoint, headers=internal_headers, json={}).status_code
        == 401
    )

    expected = app.state.settings.download_edge_completion_service_token
    app.state.settings.download_edge_completion_service_token = None
    try:
        assert (
            client.post(
                edge_endpoint,
                headers=edge_completion_headers,
                json={},
            ).status_code
            == 503
        )
    finally:
        app.state.settings.download_edge_completion_service_token = expected


@pytest.mark.parametrize(
    "path",
    [
        "/internal/tasks/timeout-scan",
        "/internal/relay/dispatch-once",
        "/internal/channel-costs",
        "/internal/relay/task-stages",
        "/internal/artifact-download-completions/obs-access-log",
    ],
)
def test_edge_completion_service_token_cannot_access_other_internal_routes(
    client,
    edge_completion_headers,
    path: str,
):
    assert (
        client.post(path, headers=edge_completion_headers, content=b"{}").status_code
        == 401
    )


def test_edge_completion_service_token_cannot_credit_personal_wallet(
    app,
    client,
    edge_completion_headers,
):
    def counts() -> tuple[int, int]:
        with app.state.session_factory() as session:
            return (
                session.scalar(
                    select(func.count()).select_from(PersonalWalletAccount)
                ),
                session.scalar(select(func.count()).select_from(PersonalLedgerEntry)),
            )

    before = counts()
    response = client.post(
        f"/internal/personal/wallets/{uuid4()}/credit",
        headers=edge_completion_headers,
        json={
            "amount_points": 100,
            "idempotency_key": "edge-completion-token-must-not-credit-wallet",
            "note": "authorization isolation regression",
        },
    )
    assert response.status_code == 401
    assert counts() == before


def test_gateway_registration_rejects_redirect_and_never_follows_it():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            307,
            headers={"Location": "https://attacker.example/steal"},
        )

    gateway = _gateway_client(handler, now=now)
    download = RelaySignedDownload.model_validate(_download_payload(now=now))
    with pytest.raises(Exception, match="rejected"):
        gateway.register(
            registration_request_id=str(uuid4()),
            download_record_id=str(uuid4()),
            company_id=str(uuid4()),
            task_id=str(uuid4()),
            asset_id=ASSET_ID,
            expected_size_bytes=12345,
            artifact_sha256="a" * 64,
            source_url=str(download.url),
            storage_binding=download.storage_binding,
            issuance_request_id="redirect-test",
            transfer_reference=str(uuid4()),
        )
    assert calls == 1


def test_gateway_rejects_malicious_201_ticket_capabilities_as_unknown():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    download = RelaySignedDownload.model_validate(_download_payload(now=now))
    valid_token = base64.urlsafe_b64encode(b"t" * 32).rstrip(b"=").decode("ascii")
    valid_url = f"https://downloads.example.com/downloads/{valid_token}"
    cases = [
        (valid_url + "?ticket=leak", valid_url + "?ticket=leak"),
        (valid_url + "?", valid_url + "?"),
        (valid_url + "#", valid_url + "#"),
        ("https://downloads.example.com/downloads/short", "https://downloads.example.com/downloads/short"),
        (valid_url + "=", valid_url + "="),
        (valid_url + "/extra", valid_url + "/extra"),
        (valid_url + "%2Fextra", valid_url + "%2Fextra"),
        (f"https://user@downloads.example.com/downloads/{valid_token}", f"https://user@downloads.example.com/downloads/{valid_token}"),
        (f"https://downloads.example.com.evil.invalid/downloads/{valid_token}", f"https://downloads.example.com.evil.invalid/downloads/{valid_token}"),
        (valid_url, valid_url + "-different"),
        (valid_url, None),
    ]
    for ticket_url, location in cases:
        registration_id = str(uuid4())
        transfer_reference = str(uuid4())
        record_id = str(uuid4())

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            headers = {"Location": location} if location is not None else {}
            return httpx.Response(
                201,
                headers=headers,
                json={
                    "api_version": "v1",
                    "schema_version": 1,
                    "download_record_id": payload["download_record_id"],
                    "company_id": payload["company_id"],
                    "task_id": payload["task_id"],
                    "asset_id": payload["asset_id"],
                    "issuance_request_id": payload["issuance_request_id"],
                    "transfer_reference": payload["transfer_reference"],
                    "gateway_ticket_id": str(uuid4()),
                    "one_time": True,
                    "ticket_url": ticket_url,
                    "issued_at": now.isoformat(),
                    "expires_at": (now + timedelta(seconds=120)).isoformat(),
                    "expires_seconds": 120,
                },
            )

        gateway = _gateway_client(handler, now=now)
        with pytest.raises(DownloadGatewayOutcomeUnknownError):
            gateway.register(
                registration_request_id=registration_id,
                download_record_id=record_id,
                company_id=str(uuid4()),
                task_id=str(uuid4()),
                asset_id=ASSET_ID,
                expected_size_bytes=12345,
                artifact_sha256="a" * 64,
                source_url=str(download.url),
                storage_binding=download.storage_binding,
                issuance_request_id="malicious-ticket-response",
                transfer_reference=transfer_reference,
            )
        gateway.close()


@pytest.mark.parametrize("status_code", [408, 425, 429, 500])
def test_gateway_ambiguous_http_statuses_are_retryable_unknown(status_code):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    download = RelaySignedDownload.model_validate(_download_payload(now=now))
    gateway = _gateway_client(
        lambda request: httpx.Response(status_code, json={"error": "ambiguous"}),
        now=now,
    )
    with pytest.raises(DownloadGatewayOutcomeUnknownError):
        gateway.register(
            registration_request_id=str(uuid4()),
            download_record_id=str(uuid4()),
            company_id=str(uuid4()),
            task_id=str(uuid4()),
            asset_id=ASSET_ID,
            expected_size_bytes=12345,
            artifact_sha256="a" * 64,
            source_url=str(download.url),
            storage_binding=download.storage_binding,
            issuance_request_id="ambiguous-status",
            transfer_reference=str(uuid4()),
        )


def test_gateway_201_duplicate_json_keys_are_unknown():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    download = RelaySignedDownload.model_validate(_download_payload(now=now))

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        token = base64.urlsafe_b64encode(b"d" * 32).rstrip(b"=").decode("ascii")
        ticket_url = f"https://downloads.example.com/downloads/{token}"
        body = json.dumps(
            {
                "schema_version": 1,
                "download_record_id": payload["download_record_id"],
                "company_id": payload["company_id"],
                "task_id": payload["task_id"],
                "asset_id": payload["asset_id"],
                "issuance_request_id": payload["issuance_request_id"],
                "transfer_reference": payload["transfer_reference"],
                "gateway_ticket_id": str(uuid4()),
                "one_time": True,
                "ticket_url": ticket_url,
                "issued_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=120)).isoformat(),
                "expires_seconds": 120,
            },
            separators=(",", ":"),
        )
        body = '{"api_version":"v1","api_version":"v1",' + body[1:]
        return httpx.Response(
            201,
            headers={"Location": ticket_url, "Content-Type": "application/json"},
            content=body,
        )

    gateway = _gateway_client(handler, now=now)
    with pytest.raises(DownloadGatewayOutcomeUnknownError):
        gateway.register(
            registration_request_id=str(uuid4()),
            download_record_id=str(uuid4()),
            company_id=str(uuid4()),
            task_id=str(uuid4()),
            asset_id=ASSET_ID,
            expected_size_bytes=12345,
            artifact_sha256="a" * 64,
            source_url=str(download.url),
            storage_binding=download.storage_binding,
            issuance_request_id="duplicate-json-key",
            transfer_reference=str(uuid4()),
        )
