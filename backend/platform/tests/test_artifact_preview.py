from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib

import httpx
from sqlalchemy import func, select

from platform_api.models import DownloadGatewayRegistrationAttempt, DownloadRecord
from platform_api.relay_client import HttpxRelayClient

from .conftest import bootstrap
from .test_artifact_bridge_and_production_safety import (
    ASSET_ID,
    RELAY_JOB_ID,
    make_task_downloadable,
)
from .test_relay_boundary import recharge_and_create


BUCKET = "relay-output-private"
ENDPOINT_HOST = "obs.cn-north-4.myhuaweicloud.com"
OBJECT_KEY = f"outputs/test-tenant/{RELAY_JOB_ID}/{ASSET_ID}"


def _bound_preview_payload(*, now: datetime | None = None) -> dict[str, object]:
    issued_at = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    url = (
        f"https://{ENDPOINT_HOST}/{BUCKET}/{OBJECT_KEY}"
        "?AccessKeyId=masked&Expires=300&Signature=masked"
    )
    return {
        "api_version": "v1",
        "schema_version": 1,
        "url": url,
        "expires_seconds": 300,
        "storage_binding": {
            "provider": "huawei_obs",
            "endpoint_host": ENDPOINT_HOST,
            "bucket": BUCKET,
            "object_key": OBJECT_KEY,
            "issued_at": issued_at.isoformat(),
            "expires_at": (issued_at + timedelta(seconds=300)).isoformat(),
            "url_sha256": hashlib.sha256(url.encode("utf-8")).hexdigest(),
        },
    }


def _set_relay(app, handler) -> None:
    app.state.relay_client = HttpxRelayClient(
        base_url="https://relay.example.test",
        client_id="platform-service",
        api_key="server-only-secret",
        transport=httpx.MockTransport(handler),
    )


def _make_previewable(app, client, tenant, tenant_headers, *, suffix: str):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix=f"preview-{suffix}"
    )
    make_task_downloadable(app, task["id"])
    return task


def _preview_path(company_id: str, task_id: str) -> str:
    return (
        f"/api/v1/companies/{company_id}/tasks/{task_id}"
        f"/artifacts/{ASSET_ID}/preview"
    )


def test_preview_issues_bound_inline_url_without_download_audit_or_gateway(
    app, client, tenant, tenant_headers
):
    task = _make_previewable(
        app, client, tenant, tenant_headers, suffix="success"
    )
    payload = _bound_preview_payload()
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=payload)

    _set_relay(app, handler)

    class GatewayMustNotRun:
        def register(self, **_):
            raise AssertionError("preview must not call the Download Gateway")

    app.state.download_gateway_client = GatewayMustNotRun()
    before = None
    with app.state.session_factory() as session:
        before = session.scalar(select(func.count(DownloadRecord.id)))

    response = client.get(
        _preview_path(tenant["company_id"], task["id"]),
        headers={**tenant_headers, "X-Request-ID": "preview-request-001"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "url": payload["url"],
        "expires_seconds": 300,
        "media_type": "video",
        "content_type": "video/mp4",
        "preview_status": "issued",
    }
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert len(captured) == 1
    assert captured[0].url.path == (
        f"/v1/generations/{RELAY_JOB_ID}/artifacts/{ASSET_ID}/download"
    )
    assert captured[0].headers["x-client-id"] == "platform-service"
    assert captured[0].headers["x-api-key"] == "server-only-secret"
    assert captured[0].headers["x-request-id"] == "preview-request-001"
    assert "server-only-secret" not in response.text

    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(DownloadRecord.id))) == before
        assert session.scalar(
            select(func.count(DownloadGatewayRegistrationAttempt.id))
        ) == 0


def test_preview_is_user_scoped_and_cross_tenant_ids_do_not_reveal_existence(
    app, client, tenant, tenant_headers
):
    task = _make_previewable(app, client, tenant, tenant_headers, suffix="scope")
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_bound_preview_payload())

    _set_relay(app, handler)
    member = client.post(
        f"/api/v1/companies/{tenant['company_id']}/members",
        headers=tenant_headers,
        json={"email": "preview-worker@example.com", "display_name": "Preview Worker"},
    ).json()
    member_headers = {
        "X-Company-ID": tenant["company_id"],
        "X-User-ID": member["user_id"],
    }

    own_scope = client.get(
        _preview_path(tenant["company_id"], task["id"]),
        headers=member_headers,
    )
    assert own_scope.status_code == 404

    company_scope = client.get(
        _preview_path(tenant["company_id"], task["id"]),
        headers=member_headers,
        params={"scope": "company"},
    )
    assert company_scope.status_code == 403

    other = bootstrap(client, "artifact-preview-other")
    foreign = client.get(
        _preview_path(other["company_id"], task["id"]),
        headers={
            "X-Company-ID": other["company_id"],
            "X-User-ID": other["user_id"],
        },
    )
    assert foreign.status_code == 404
    assert calls == 0


def test_preview_requires_identity_completed_artifact_and_relay_configuration(
    app, client, tenant, tenant_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="preview-incomplete"
    )
    path = _preview_path(tenant["company_id"], task["id"])

    assert client.get(path).status_code == 401
    assert client.get(path, headers=tenant_headers).status_code == 404

    make_task_downloadable(app, task["id"])
    app.state.relay_client = None
    unavailable = client.get(path, headers=tenant_headers)
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == "Relay client is not configured"


def test_preview_rejects_unbound_or_failed_relay_response_without_audit(
    app, client, tenant, tenant_headers
):
    task = _make_previewable(app, client, tenant, tenant_headers, suffix="errors")
    path = _preview_path(tenant["company_id"], task["id"])

    _set_relay(
        app,
        lambda _: httpx.Response(
            200,
            json={
                "api_version": "v1",
                "schema_version": 1,
                "url": "https://legacy.example.test/artifact?signature=masked",
                "expires_seconds": 300,
            },
        ),
    )
    unsafe = client.get(path, headers=tenant_headers)
    assert unsafe.status_code == 502
    assert unsafe.json()["detail"] == "Relay rejected the artifact preview request"

    _set_relay(app, lambda _: httpx.Response(503, json={"detail": "down"}))
    temporary = client.get(path, headers=tenant_headers)
    assert temporary.status_code == 503
    assert temporary.json()["detail"] == "Artifact preview is temporarily unavailable"

    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(DownloadRecord.id))) == 0
        assert session.scalar(
            select(func.count(DownloadGatewayRegistrationAttempt.id))
        ) == 0
