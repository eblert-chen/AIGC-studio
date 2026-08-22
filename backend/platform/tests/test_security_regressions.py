from __future__ import annotations

import base64
from datetime import timedelta
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from platform_api.config import Settings
from platform_api.asset_storage import HuaweiObsInputAssetStore
from platform_api.database import Base, build_session_factory
from platform_api.main import create_app
from platform_api.models import (
    MembershipRole,
    GenerationTask,
    Role,
    TaskStatus,
    User,
    WalletAccount,
    utcnow,
)
from platform_api.relay_client import (
    RelayPermanentError,
    validate_signed_download_url,
)
from platform_api.relay_sync_worker import RelayStatusPoller
from platform_api.services.companies import CompanyService
from platform_api.services.permissions import PermissionService

from .conftest import bootstrap
from .test_relay_boundary import job_snapshot
from .test_wallet_and_tasks import seed_model


def _add_member(client, company_id, owner_headers, suffix):
    response = client.post(
        f"/api/v1/companies/{company_id}/members",
        headers=owner_headers,
        json={
            "email": f"{suffix}@example.com",
            "display_name": suffix.title(),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_role(client, company_id, headers, name, permissions):
    return client.post(
        f"/api/v1/companies/{company_id}/roles",
        headers=headers,
        json={"name": name, "permission_codes": permissions},
    )


def _assign_role(client, company_id, owner_headers, role_id, membership_id):
    return client.post(
        f"/api/v1/companies/{company_id}/roles/{role_id}/assign",
        headers=owner_headers,
        json={"membership_id": membership_id},
    )


def _create_task(client, company_id, headers, model_id, suffix):
    response = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=headers,
        json={
            "model_id": model_id,
            "idempotency_key": f"security-task-{suffix}",
            "request_payload": {
                "prompt": f"security {suffix}",
                "duration_seconds": 5,
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_company_member_cannot_write_model_grants(client, tenant, tenant_headers):
    model = client.post(
        "/api/v1/bootstrap/models",
        json={
            "slug": "admin-only-model",
            "display_name": "Admin Only",
            "provider_key": "development",
            "capabilities": [],
        },
    ).json()
    response = client.put(
        f"/api/v1/companies/{tenant['company_id']}/model-grants",
        headers=tenant_headers,
        json={
            "model_id": model["id"],
            "enabled": True,
            "price_per_item_cents": 1,
        },
    )
    assert response.status_code == 405


def test_permission_manager_cannot_escalate_or_assign_privileged_roles(
    client, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    manager = _add_member(client, company_id, tenant_headers, "limited-manager")
    worker = _add_member(client, company_id, tenant_headers, "delegate-worker")
    manager_role = _create_role(
        client,
        company_id,
        tenant_headers,
        "Limited manager",
        ["users.manage", "tasks.read"],
    ).json()
    assert (
        _assign_role(
            client,
            company_id,
            tenant_headers,
            manager_role["id"],
            manager["membership_id"],
        ).status_code
        == 204
    )
    manager_headers = {
        "X-Company-ID": company_id,
        "X-User-ID": manager["user_id"],
    }

    assert (
        _create_role(
            client, company_id, manager_headers, "Reader", ["tasks.read"]
        ).status_code
        == 201
    )
    denied_role = _create_role(
        client,
        company_id,
        manager_headers,
        "Billing reader",
        ["billing.read"],
    )
    assert denied_role.status_code == 403
    denied_override = client.put(
        (
            f"/api/v1/companies/{company_id}/members/"
            f"{worker['membership_id']}/permission"
        ),
        headers=manager_headers,
        json={"permission_code": "billing.read", "effect": "allow"},
    )
    assert denied_override.status_code == 403

    owner_role = next(
        role
        for role in client.get(
            f"/api/v1/companies/{company_id}/roles",
            headers=tenant_headers,
        ).json()
        if role["is_system"]
    )
    assert (
        _assign_role(
            client,
            company_id,
            manager_headers,
            owner_role["id"],
            manager["membership_id"],
        ).status_code
        == 403
    )
    billing_role = _create_role(
        client,
        company_id,
        tenant_headers,
        "Privileged billing",
        ["billing.read"],
    ).json()
    assert (
        _assign_role(
            client,
            company_id,
            manager_headers,
            billing_role["id"],
            manager["membership_id"],
        ).status_code
        == 403
    )


def test_effective_permissions_ignore_corrupt_cross_company_role_link(
    app, client, tenant, tenant_headers
):
    member = _add_member(
        client,
        tenant["company_id"],
        tenant_headers,
        "cross-company-role-link",
    )
    other = bootstrap(client, "cross-company-role-link-other")
    with app.state.session_factory() as session:
        foreign_owner_role = session.scalar(
            select(Role).where(
                Role.company_id == other["company_id"],
                Role.system_key == "owner",
            )
        )
        assert foreign_owner_role is not None
        session.add(
            MembershipRole(
                membership_id=member["membership_id"],
                role_id=foreign_owner_role.id,
            )
        )
        session.commit()
        effective = PermissionService.effective_permissions(
            session, membership_id=member["membership_id"]
        )
    assert "billing.read" not in effective
    assert "billing.manage" not in effective
    response = client.get(
        f"/api/v1/companies/{tenant['company_id']}/wallet",
        headers={
            "X-Company-ID": tenant["company_id"],
            "X-User-ID": member["user_id"],
        },
    )
    assert response.status_code == 403


def test_task_idempotency_key_cannot_be_replayed_by_another_user(
    app, client, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    model_id = seed_model(app, company_id)
    assert (
        client.post(
            f"/api/v1/companies/{company_id}/wallet/recharge",
            headers=tenant_headers,
            json={
                "amount_cents": 1000,
                "idempotency_key": "cross-user-security-recharge",
            },
        ).status_code
        == 200
    )
    first = _create_task(client, company_id, tenant_headers, model_id, "cross-user")
    member = _add_member(client, company_id, tenant_headers, "task-creator")
    role = _create_role(
        client,
        company_id,
        tenant_headers,
        "Task creator",
        ["tasks.create"],
    ).json()
    assert (
        _assign_role(
            client,
            company_id,
            tenant_headers,
            role["id"],
            member["membership_id"],
        ).status_code
        == 204
    )
    replay = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers={
            "X-Company-ID": company_id,
            "X-User-ID": member["user_id"],
        },
        json={
            "model_id": model_id,
            "idempotency_key": "security-task-cross-user",
            "request_payload": {
                "prompt": "security cross-user",
                "duration_seconds": 5,
            },
        },
    )
    assert replay.status_code == 409
    assert replay.json()["code"] == "conflict"
    assert first["user_id"] == tenant["user_id"]
    assert "request_payload" not in replay.json()


def test_status_poller_rotates_touched_processing_tasks(
    app, client, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    model_id = seed_model(app, company_id)
    assert (
        client.post(
            f"/api/v1/companies/{company_id}/wallet/recharge",
            headers=tenant_headers,
            json={
                "amount_cents": 1000,
                "idempotency_key": "poll-fairness-recharge",
            },
        ).status_code
        == 200
    )
    first = _create_task(client, company_id, tenant_headers, model_id, "fairness-first")
    second = _create_task(
        client, company_id, tenant_headers, model_id, "fairness-second"
    )
    first_job = "11111111-2222-4333-8444-555555555555"
    second_job = "66666666-7777-4888-8999-000000000000"
    now = utcnow()
    with app.state.session_factory.begin() as session:
        first_task = session.get(GenerationTask, first["id"])
        second_task = session.get(GenerationTask, second["id"])
        first_task.status = TaskStatus.PROCESSING
        first_task.relay_job_id = first_job
        first_task.updated_at = now - timedelta(minutes=10)
        second_task.status = TaskStatus.PROCESSING
        second_task.relay_job_id = second_job
        second_task.updated_at = now - timedelta(minutes=9)

    class ProcessingRelayClient:
        def __init__(self):
            self.calls = []

        def get(self, relay_job_id):
            self.calls.append(relay_job_id)
            task_id = first["id"] if relay_job_id == first_job else second["id"]
            return job_snapshot(
                task_id=task_id,
                job_id=relay_job_id,
                status="processing",
            )

    relay_client = ProcessingRelayClient()
    poller = RelayStatusPoller(app.state.session_factory, relay_client, batch_size=1)
    assert poller.poll_once() == 1
    assert poller.poll_once() == 1
    assert relay_client.calls == [first_job, second_job]


def test_succeeded_without_transferred_outputs_does_not_settle(
    app, client, tenant, tenant_headers, internal_headers
):
    company_id = tenant["company_id"]
    model_id = seed_model(app, company_id)
    assert (
        client.post(
            f"/api/v1/companies/{company_id}/wallet/recharge",
            headers=tenant_headers,
            json={
                "amount_cents": 1000,
                "idempotency_key": "empty-output-recharge",
            },
        ).status_code
        == 200
    )
    task = _create_task(client, company_id, tenant_headers, model_id, "empty-output")
    relay_job_id = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    with app.state.session_factory.begin() as session:
        session.get(GenerationTask, task["id"]).relay_job_id = relay_job_id

    response = client.post(
        "/internal/relay/status",
        headers=internal_headers,
        json={
            "company_id": company_id,
            "task_id": task["id"],
            "relay_job_id": relay_job_id,
            "status": "succeeded",
            "outputs": [],
        },
    )
    assert response.status_code == 409
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        wallet = session.get(WalletAccount, company_id)
        assert stored.status == TaskStatus.QUEUED
        assert stored.output_artifacts == []
        assert stored.reserved_cents == 400
        assert wallet.reserved_cents == 400


@pytest.mark.parametrize(
    ("url", "allow_local_http"),
    [
        ("https://user:password@artifacts.example.com/private", False),
        ("http://user:password@127.0.0.1/private", True),
    ],
)
def test_signed_download_urls_reject_embedded_credentials(url, allow_local_http):
    with pytest.raises(RelayPermanentError, match="credential-bearing"):
        validate_signed_download_url(url, allow_local_http=allow_local_http)


@pytest.fixture
def jwt_factory():
    secret = "jwt-signing-secret-32-bytes-minimum!!"

    def make_token(
        *,
        user_id,
        company_id=None,
        platform_admin=False,
        issuer="ai-video-platform",
        audience="ai-video-web",
        expires_in=300,
        auth_time=None,
        amr=None,
    ):
        now = int(time.time())
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "sub": user_id,
            "company_id": company_id,
            "platform_admin": platform_admin,
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + expires_in,
        }
        if auth_time is not None:
            payload["auth_time"] = auth_time
        if amr is not None:
            payload["amr"] = amr

        def encode(value):
            raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        signing_input = f"{encode(header)}.{encode(payload)}"
        signature = hmac.new(
            secret.encode(), signing_input.encode(), hashlib.sha256
        ).digest()
        encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
        return f"{signing_input}.{encoded_signature}"

    return make_token


@pytest.fixture
def production_client_and_identity():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = build_session_factory(engine)
    with session_factory.begin() as session:
        company, user, _ = CompanyService.bootstrap_company(
            session,
            company_name="Production JWT",
            owner_email="production-jwt@example.com",
            owner_display_name="Production JWT Owner",
        )
        user.is_platform_admin = True
        company_id = company.id
        user_id = user.id
    settings = Settings(
        environment="production",
        database_url=("postgresql+psycopg://platform:password@db.example.com/platform"),
        auto_create_tables=False,
        enable_bootstrap=False,
        cors_origins=["https://app.example.com"],
        relay_backends={
            "new-api-v1": {
                "base_url": "https://relay.example.com",
                "client_id": "platform-production-service",
                "api_key": "relay-api-key-32-bytes-minimum!!",
                "contract_revision": "generations.v1",
            }
        },
        relay_default_backend_id="new-api-v1",
        relay_default_contract_revision="generations.v1",
        internal_service_token="internal-token-32-bytes-minimum!!",
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
        download_gateway_service_token=("download-gateway-service-token-32-bytes!!"),
        download_gateway_registration_signing_secret=(
            "download-gateway-signing-secret-32-bytes!!"
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
        platform_owner_user_ids=[user_id],
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
    with TestClient(app) as client:
        yield client, company_id, user_id
    engine.dispose()


def test_production_disables_caller_authored_relay_status_updates(
    production_client_and_identity,
):
    client, company_id, _ = production_client_and_identity
    response = client.post(
        "/internal/relay/status",
        headers={"X-Internal-Service-Token": "internal-token-32-bytes-minimum!!"},
        json={
            "company_id": company_id,
            "task_id": "caller-authored-task",
            "relay_job_id": "caller-authored-relay-job",
            "status": "processing",
            "reservation_action": "hold",
        },
    )

    assert response.status_code == 404


def test_production_rejects_even_valid_legacy_signed_jwt(
    production_client_and_identity, jwt_factory
):
    client, company_id, user_id = production_client_and_identity
    tenant_token = jwt_factory(user_id=user_id, company_id=company_id)
    tenant_response = client.get(
        f"/api/v1/companies/{company_id}/members",
        headers={
            "Authorization": f"Bearer {tenant_token}",
            "X-Company-ID": "forged-company",
            "X-User-ID": "forged-user",
        },
    )
    assert tenant_response.status_code == 401
    assert tenant_response.json()["detail"] == "Legacy bearer authentication is disabled"
    assert tenant_response.headers["www-authenticate"] == "Bearer"
    admin_token = jwt_factory(
        user_id=user_id,
        company_id=None,
        platform_admin=True,
        auth_time=int(time.time()),
        amr=["webauthn"],
    )
    admin_response = client.get(
        "/api/v1/platform-admin/companies",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert admin_response.status_code == 401
    assert admin_response.json()["detail"] == "Legacy bearer authentication is disabled"


def test_production_rejects_invalid_or_wrong_issuer_jwt(
    production_client_and_identity, jwt_factory
):
    client, company_id, user_id = production_client_and_identity
    path = f"/api/v1/companies/{company_id}/members"
    assert client.get(path).status_code == 401
    wrong_issuer = jwt_factory(
        user_id=user_id,
        company_id=company_id,
        issuer="wrong-issuer",
    )
    response = client.get(
        path,
        headers={"Authorization": f"Bearer {wrong_issuer}"},
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"

    expired = jwt_factory(
        user_id=user_id,
        company_id=company_id,
        expires_in=-120,
    )
    valid = jwt_factory(user_id=user_id, company_id=company_id)
    signing_input = valid.rsplit(".", 1)[0]
    forged_signature = base64.urlsafe_b64encode(b"\x00" * 32).rstrip(b"=").decode()
    forged = f"{signing_input}.{forged_signature}"
    for token in (expired, forged):
        rejected = client.get(
            path,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert rejected.status_code == 401
        assert rejected.headers["www-authenticate"] == "Bearer"


def test_production_legacy_bearer_cannot_bypass_admin_authentication_gates(
    production_client_and_identity, jwt_factory
):
    client, _, user_id = production_client_and_identity
    missing_strong_auth = jwt_factory(
        user_id=user_id,
        platform_admin=True,
        auth_time=int(time.time()),
        amr=["pwd"],
    )
    rejected = client.get(
        "/api/v1/platform-admin/companies",
        headers={"Authorization": f"Bearer {missing_strong_auth}"},
    )
    assert rejected.status_code == 401
    assert rejected.json()["detail"] == "Legacy bearer authentication is disabled"

    stale_strong_auth = jwt_factory(
        user_id=user_id,
        platform_admin=True,
        auth_time=int(time.time()) - 301,
        amr=["webauthn"],
    )
    write_response = client.post(
        "/api/v1/platform-admin/companies",
        headers={"Authorization": f"Bearer {stale_strong_auth}"},
        json={
            "name": "Blocked stale step-up",
            "owner_email": "blocked-stale@example.com",
            "owner_display_name": "Blocked stale owner",
        },
    )
    assert write_response.status_code == 401
    assert write_response.json()["detail"] == "Legacy bearer authentication is disabled"
