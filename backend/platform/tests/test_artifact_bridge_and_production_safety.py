from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from platform_api.config import Settings
from platform_api.asset_storage import HuaweiObsInputAssetStore
from platform_api.main import create_app
from platform_api.models import GenerationTask, TaskStatus, WalletAccount
from platform_api.relay_client import (
    HttpxRelayClient,
    RelayArtifact,
    RelayPermanentError,
)
from platform_api.relay_sync_worker import RelayStatusPoller
from platform_api.services.artifacts import TaskArtifactService

from .conftest import bootstrap
from .test_relay_boundary import job_snapshot, recharge_and_create

RELAY_JOB_ID = "99999999-9999-4999-8999-999999999999"
ASSET_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def relay_artifact() -> RelayArtifact:
    return RelayArtifact(
        asset_id=ASSET_ID,
        object_key=f"outputs/tenant/{RELAY_JOB_ID}/{ASSET_ID}",
        media_type="video",
        content_type="video/mp4",
        size_bytes=12345,
        sha256="a" * 64,
    )


def make_task_downloadable(app, task_id: str) -> None:
    with app.state.session_factory.begin() as session:
        task = session.get(GenerationTask, task_id)
        task.status = TaskStatus.SUCCEEDED
        task.relay_job_id = RELAY_JOB_ID
        task.output_artifacts = [relay_artifact().safe_metadata()]
        TaskArtifactService.persist_success_artifacts(
            session,
            task=task,
            artifacts=task.output_artifacts,
        )


def test_transferring_keeps_polling_and_succeeded_persists_safe_outputs(
    app, client, tenant, tenant_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="artifact-poll"
    )
    with app.state.session_factory.begin() as session:
        stored = session.get(GenerationTask, task["id"])
        stored.relay_job_id = RELAY_JOB_ID

    class PollingRelayClient:
        def __init__(self):
            self.snapshots = [
                job_snapshot(
                    task_id=task["id"],
                    job_id=RELAY_JOB_ID,
                    status="transferring",
                    outputs=[],
                ),
                job_snapshot(
                    task_id=task["id"],
                    job_id=RELAY_JOB_ID,
                    status="succeeded",
                    outputs=[relay_artifact()],
                ),
            ]

        def get(self, relay_job_id):
            assert relay_job_id == RELAY_JOB_ID
            return self.snapshots.pop(0)

    poller = RelayStatusPoller(app.state.session_factory, PollingRelayClient())
    assert poller.poll_once() == 1
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        wallet = session.get(WalletAccount, tenant["company_id"])
        assert stored.status == TaskStatus.PROCESSING
        assert wallet.reserved_cents == 400

    assert poller.poll_once() == 1
    detail = client.get(
        f"/api/v1/companies/{tenant['company_id']}/tasks/{task['id']}",
        headers=tenant_headers,
    )
    assert detail.status_code == 200
    output = detail.json()["output_artifacts"][0]
    assert output["asset_id"] == ASSET_ID
    assert output["sha256"] == "a" * 64
    assert "object_key" not in output
    assert "url" not in output
    with app.state.session_factory() as session:
        wallet = session.get(WalletAccount, tenant["company_id"])
        assert wallet.reserved_cents == 0


def test_company_scoped_artifact_download_proxies_relay_credentials(
    app, client, tenant, tenant_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="artifact-download"
    )
    make_task_downloadable(app, task["id"])
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request):
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "api_version": "v1",
                "schema_version": 1,
                "url": "http://127.0.0.1:8100/private/signed-token",
                "expires_seconds": 300,
            },
        )

    app.state.relay_client = HttpxRelayClient(
        base_url="http://relay.internal",
        client_id="platform-service",
        api_key="server-only-secret",
        transport=httpx.MockTransport(handler),
        allow_local_http=True,
    )
    path = (
        f"/api/v1/companies/{tenant['company_id']}/tasks/{task['id']}"
        f"/artifacts/{ASSET_ID}/download"
    )
    own = client.get(
        path,
        headers={**tenant_headers, "X-Request-ID": "download-trace-001"},
    )
    assert own.status_code == 200
    assert own.json()["url"] == ("http://127.0.0.1:8100/private/signed-token")
    assert own.json()["expires_seconds"] == 300
    assert own.json()["download_status"] == "issued"
    assert own.json()["download_record_id"]
    assert captured[0].url.path == (
        f"/v1/generations/{RELAY_JOB_ID}/artifacts/{ASSET_ID}/download"
    )
    assert captured[0].headers["x-client-id"] == "platform-service"
    assert captured[0].headers["x-api-key"] == "server-only-secret"
    assert captured[0].headers["x-request-id"] == "download-trace-001"
    assert "server-only-secret" not in own.text

    member = client.post(
        f"/api/v1/companies/{tenant['company_id']}/members",
        headers=tenant_headers,
        json={"email": "artifact-denied@example.com", "display_name": "Denied"},
    ).json()
    assert (
        client.put(
            (
                f"/api/v1/companies/{tenant['company_id']}/members/"
                f"{member['membership_id']}/permission"
            ),
            headers=tenant_headers,
            json={"permission_code": "tasks.read", "effect": "deny"},
        ).status_code
        == 200
    )
    denied = client.get(
        path,
        headers={
            "X-Company-ID": tenant["company_id"],
            "X-User-ID": member["user_id"],
        },
    )
    assert denied.status_code == 403

    other = bootstrap(client, "artifact-other")
    foreign = client.get(
        (
            f"/api/v1/companies/{other['company_id']}/tasks/{task['id']}"
            f"/artifacts/{ASSET_ID}/download"
        ),
        headers={
            "X-Company-ID": other["company_id"],
            "X-User-ID": other["user_id"],
        },
    )
    assert foreign.status_code == 404
    assert len(captured) == 1


def test_signed_download_http_is_only_allowed_for_local_development(
    app, client, tenant, tenant_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="artifact-http-policy"
    )
    make_task_downloadable(app, task["id"])

    def handler(_: httpx.Request):
        return httpx.Response(
            200,
            json={
                "api_version": "v1",
                "schema_version": 1,
                "url": "http://artifacts.example.test/private",
                "expires_seconds": 300,
            },
        )

    app.state.relay_client = HttpxRelayClient(
        base_url="http://relay.internal",
        client_id="platform-service",
        api_key="server-only-secret",
        transport=httpx.MockTransport(handler),
        allow_local_http=True,
    )
    response = client.get(
        (
            f"/api/v1/companies/{tenant['company_id']}/tasks/{task['id']}"
            f"/artifacts/{ASSET_ID}/download"
        ),
        headers=tenant_headers,
    )
    assert response.status_code == 502


def test_relay_client_rejects_loopback_http_unless_explicitly_enabled():
    def handler(_: httpx.Request):
        return httpx.Response(
            200,
            json={
                "api_version": "v1",
                "schema_version": 1,
                "url": "http://127.0.0.1:8100/private",
                "expires_seconds": 300,
            },
        )

    relay_client = HttpxRelayClient(
        base_url="https://relay.example.test",
        client_id="platform-service",
        api_key="server-only-secret",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RelayPermanentError):
        relay_client.get_artifact_download(RELAY_JOB_ID, ASSET_ID)


def test_production_rejects_forged_tenant_headers_and_self_recharge():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    settings = Settings(
        environment="production",
        database_url=("postgresql+psycopg://platform:password@db.example.com/platform"),
        auto_create_tables=False,
        enable_bootstrap=False,
        cors_origins=["https://app.example.com"],
        relay_backends={
            "new-api-v1": {
                "base_url": "https://relay.example.com",
                "client_id": "platform-service",
                "api_key": "server-only-relay-secret-32-bytes!!",
                "contract_revision": "generations.v1",
            }
        },
        relay_default_backend_id="new-api-v1",
        relay_default_contract_revision="generations.v1",
        internal_service_token="internal-service-secret-32-bytes!!",
        download_edge_completion_service_token=(
            "download-edge-completion-token-32-bytes!!"
        ),
        channel_cost_signing_secret="channel-cost-secret-32-bytes-minimum!!",
        relay_telemetry_signing_secret=("relay-telemetry-secret-32-bytes-minimum!!"),
        provider_alert_signing_secret=("provider-alert-inbound-secret-32-bytes!!"),
        provider_alert_forward_webhook_url=(
            "https://alerts.example.com/platform/provider"
        ),
        provider_alert_forward_signing_secret=(
            "provider-alert-outbound-secret-32-bytes!!"
        ),
        download_completion_edge_gateway_signing_secret=(
            "edge-download-secret-32-bytes-minimum!!"
        ),
        download_completion_obs_access_log_signing_secret=(
            "obs-download-secret-32-bytes-minimum!!!"
        ),
        download_gateway_registration_url=(
            "https://download-gateway.example.com/internal/v1/download-tickets"
        ),
        download_gateway_public_base_url="https://downloads.example.com",
        download_gateway_service_token=("gateway-service-token-32-bytes-minimum!!"),
        download_gateway_registration_signing_secret=(
            "gateway-registration-signing-32-bytes!!"
        ),
        download_gateway_attempt_encryption_key_base64=(
            "sA7lnL/q4z+Wf+0koSVf8J/8lGUx8ZO8PBACk6WcD8c="
        ),
        download_gateway_registration_worker_enabled=True,
        jwt_signing_secret="jwt-signing-secret-32-bytes-minimum!!",
        jwt_issuer="ai-video-platform",
        jwt_audience="ai-video-web",
        oidc_enabled=True,
        oidc_self_signup_enabled=False,
        oidc_issuer="https://idp.example.com/",
        oidc_authorization_endpoint="https://idp.example.com/oauth2/authorize",
        oidc_token_endpoint="https://idp.example.com/oauth2/token",
        oidc_jwks_uri="https://idp.example.com/.well-known/jwks.json",
        oidc_client_id="ai-video-platform",
        oidc_redirect_uri="https://platform.example.com/api/v1/auth/callback",
        frontend_origin="https://app.example.com",
        account_management_url="https://idp.example.com/account",
        platform_owner_user_ids=["production-owner-subject"],
        input_asset_store="huawei_obs",
        input_asset_public_base_url="https://platform.example.com",
        huawei_obs_access_key_id="production-obs-access-key",
        huawei_obs_secret_access_key="production-obs-secret-access-key-32-bytes!!",
        huawei_obs_endpoint="https://obs.cn-north-4.myhuaweicloud.com",
        huawei_obs_bucket="ai-video-input-assets",
    )
    app = create_app(
        settings=settings,
        engine=engine,
        input_asset_store=HuaweiObsInputAssetStore(object(), "test-bucket"),
    )
    with TestClient(app) as production_client:
        headers = {
            "X-Company-ID": "forged-company",
            "X-User-ID": "forged-user",
        }
        tenant_response = production_client.get(
            "/api/v1/companies/forged-company/tasks",
            headers=headers,
        )
        assert tenant_response.status_code == 401
        recharge = production_client.post(
            "/api/v1/companies/forged-company/wallet/recharge",
            headers=headers,
            json={
                "amount_cents": 100,
                "idempotency_key": "forged-recharge",
            },
        )
        assert recharge.status_code == 404
    engine.dispose()


def test_bootstrap_model_slug_accepts_relay_model_identifier(client):
    response = client.post(
        "/api/v1/bootstrap/models",
        json={
            "slug": "mock.video.v1",
            "display_name": "Mock Video V1",
            "provider_key": "mock-video",
            "capabilities": [],
        },
    )
    assert response.status_code == 201
    assert response.json()["slug"] == "mock.video.v1"
