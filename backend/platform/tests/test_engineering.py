from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool

from platform_api.config import Settings
from platform_api.asset_storage import HuaweiObsInputAssetStore
from platform_api.database import Base
from platform_api.dependencies import get_db
from platform_api.main import create_app

VALID_PRODUCTION_SETTINGS = {
    "environment": "production",
    "database_url": ("postgresql+psycopg://platform:password@db.example.com/platform"),
    "auto_create_tables": False,
    "enable_bootstrap": False,
    "cors_origins": ["https://app.example.com"],
    "relay_backends": {
        "new-api-v1": {
            "base_url": "https://relay.example.com",
            "client_id": "platform-production-service",
            "api_key": "relay-api-key-32-bytes-minimum!!",
            "contract_revision": "generations.v1",
        }
    },
    "relay_default_backend_id": "new-api-v1",
    "relay_default_contract_revision": "generations.v1",
    "internal_service_token": "internal-token-32-bytes-minimum!!",
    "download_edge_completion_service_token": (
        "edge-completion-service-token-32-bytes!!"
    ),
    "channel_cost_signing_secret": "channel-cost-secret-32-bytes-minimum!!",
    "relay_telemetry_signing_secret": ("relay-telemetry-secret-32-bytes-minimum!!"),
    "provider_alert_signing_secret": ("provider-alert-inbound-secret-32-bytes!!"),
    "provider_alert_forward_webhook_url": (
        "https://alerts.example.com/platform/provider"
    ),
    "provider_alert_forward_signing_secret": (
        "provider-alert-outbound-secret-32-bytes!!"
    ),
    "download_completion_edge_gateway_signing_secret": (
        "edge-download-secret-32-bytes-minimum!!"
    ),
    "download_completion_obs_access_log_signing_secret": (
        "obs-download-secret-32-bytes-minimum!!!"
    ),
    "download_gateway_registration_url": (
        "https://download-gateway.example.com/internal/v1/download-tickets"
    ),
    "download_gateway_public_base_url": "https://downloads.example.com",
    "download_gateway_service_token": "gateway-service-token-32-bytes-minimum!!",
    "download_gateway_registration_signing_secret": (
        "gateway-registration-signing-32-bytes!!"
    ),
    "download_gateway_attempt_encryption_key_base64": (
        "sA7lnL/q4z+Wf+0koSVf8J/8lGUx8ZO8PBACk6WcD8c="
    ),
    "download_gateway_registration_worker_enabled": True,
    "jwt_signing_secret": "jwt-signing-secret-32-bytes-minimum!!",
    "jwt_issuer": "ai-video-platform",
    "jwt_audience": "ai-video-web",
    "oidc_enabled": True,
    "oidc_self_signup_enabled": False,
    "oidc_issuer": "https://idp.example.com/",
    "oidc_authorization_endpoint": "https://idp.example.com/oauth2/authorize",
    "oidc_token_endpoint": "https://idp.example.com/oauth2/token",
    "oidc_jwks_uri": "https://idp.example.com/.well-known/jwks.json",
    "oidc_client_id": "ai-video-platform",
    "oidc_redirect_uri": "https://platform.example.com/api/v1/auth/callback",
    "frontend_origin": "https://app.example.com",
    "account_management_url": "https://idp.example.com/account",
    "platform_owner_user_ids": ["production-owner-subject"],
    "input_asset_store": "huawei_obs",
    "input_asset_public_base_url": "https://platform.example.com",
    "huawei_obs_access_key_id": "production-obs-access-key",
    "huawei_obs_secret_access_key": "production-obs-secret-access-key-32-bytes!!",
    "huawei_obs_endpoint": "https://obs.cn-north-4.myhuaweicloud.com",
    "huawei_obs_bucket": "ai-video-input-assets",
}


def production_settings(**overrides: object) -> Settings:
    return Settings(**{**VALID_PRODUCTION_SETTINGS, **overrides})


def test_ready_returns_503_when_database_is_unavailable(app, client):
    original_factory = app.state.session_factory

    def unavailable():
        raise RuntimeError("database unavailable")

    app.state.session_factory = unavailable
    try:
        response = client.get("/health/ready")
    finally:
        app.state.session_factory = original_factory
    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"


def test_default_local_cors_origin_is_allowed(client):
    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-credentials" not in response.headers


def test_development_model_bootstrap_is_idempotent(client):
    payload = {
        "slug": "mock.video.v1",
        "display_name": "Mock Video V1",
        "provider_key": "mock-video",
        "billing_mode": "per_item",
        "capability_version": 1,
        "capabilities": [
            {
                "key": "generation",
                "config": {"durations": [5, 10], "max_outputs": 4},
            }
        ],
    }
    first = client.post("/api/v1/bootstrap/models", json=payload)
    replay = client.post("/api/v1/bootstrap/models", json=payload)
    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]


def test_database_transactions_finish_before_http_responses(app):
    database_dependencies = []

    def collect(dependant):
        for dependency in dependant.dependencies:
            if dependency.call is get_db:
                database_dependencies.append(dependency)
            collect(dependency)

    for route in app.routes:
        if isinstance(route, APIRoute):
            collect(route.dependant)

    assert database_dependencies
    assert all(dependency.scope == "function" for dependency in database_dependencies)


def test_production_requires_explicit_cors_and_disables_unsafe_bootstrap():
    with pytest.raises(ValidationError):
        production_settings(cors_origins=None)
    with pytest.raises(ValidationError):
        production_settings(auto_create_tables=True)
    with pytest.raises(ValidationError):
        production_settings(enable_bootstrap=True)


def test_create_all_is_off_for_valid_production_settings():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    settings = production_settings()
    create_app(
        settings=settings,
        engine=engine,
        input_asset_store=HuaweiObsInputAssetStore(object(), "test-bucket"),
    )
    assert inspect(engine).get_table_names() == []
    engine.dispose()


def test_initial_alembic_migration_covers_current_metadata(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database_path = tmp_path / "migration.db"
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    migrated_tables = set(inspect(engine).get_table_names())
    assert set(Base.metadata.tables).issubset(migrated_tables)
    assert "alembic_version" in migrated_tables
    engine.dispose()
