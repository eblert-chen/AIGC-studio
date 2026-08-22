from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from platform_api.config import Settings
from platform_api.main import create_app


TEST_RELAY_CAPABILITY_REVISION = "sha256:" + ("1" * 64)
TEST_CHANNEL_COST_SIGNING_SECRET = "test-channel-cost-signing-secret"
TEST_RELAY_TELEMETRY_SIGNING_SECRET = (
    "test-relay-telemetry-signing-secret-32-bytes"
)
TEST_EDGE_DOWNLOAD_SIGNING_SECRET = "test-edge-download-signing-secret"
TEST_EDGE_COMPLETION_SERVICE_TOKEN = "test-edge-completion-service-token"
TEST_OBS_DOWNLOAD_SIGNING_SECRET = "test-obs-download-signing-secret"
TEST_DOWNLOAD_GATEWAY_ATTEMPT_KEY = (
    "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
)
TEST_BOOTSTRAP_TOKEN = "integration-bootstrap-secret-2026-08-14-aa"


@pytest.fixture
def app(tmp_path):
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    settings = Settings(
        database_url="sqlite+pysqlite://",
        auto_create_tables=True,
        development_header_auth_enabled=True,
        enable_bootstrap=True,
        bootstrap_token=TEST_BOOTSTRAP_TOKEN,
        # The shared in-process fixture intentionally swaps Relay doubles at
        # runtime.  Production never enables this explicit compatibility path.
        relay_legacy_compatibility_enabled=True,
        relay_allow_legacy_artifact_download_response=True,
        internal_service_token="test-internal-token",
        download_edge_completion_service_token=TEST_EDGE_COMPLETION_SERVICE_TOKEN,
        channel_cost_signing_secret=TEST_CHANNEL_COST_SIGNING_SECRET,
        relay_telemetry_signing_secret=TEST_RELAY_TELEMETRY_SIGNING_SECRET,
        download_completion_edge_gateway_signing_secret=(
            TEST_EDGE_DOWNLOAD_SIGNING_SECRET
        ),
        download_completion_obs_access_log_signing_secret=(
            TEST_OBS_DOWNLOAD_SIGNING_SECRET
        ),
        download_gateway_attempt_encryption_key_base64=(
            TEST_DOWNLOAD_GATEWAY_ATTEMPT_KEY
        ),
        input_asset_filesystem_root=str(tmp_path / "input-assets"),
        input_asset_public_base_url="http://testserver",
        input_asset_relay_base_url="http://platform-internal:8000",
        input_asset_signing_secret="test-input-asset-signing-secret",
    )
    app = create_app(settings=settings, engine=engine)
    yield app
    engine.dispose()


@pytest.fixture
def client(app):
    with TestClient(
        app,
        headers={"X-Bootstrap-Token": TEST_BOOTSTRAP_TOKEN},
    ) as test_client:
        yield test_client


def bootstrap(client: TestClient, suffix: str = "one") -> dict[str, str]:
    response = client.post(
        "/api/v1/bootstrap",
        json={
            "company_name": f"Company {suffix}",
            "owner_email": f"owner-{suffix}@example.com",
            "owner_display_name": f"Owner {suffix}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def tenant(client):
    return bootstrap(client)


@pytest.fixture
def tenant_headers(tenant):
    return {
        "X-Company-ID": tenant["company_id"],
        "X-User-ID": tenant["user_id"],
    }


@pytest.fixture
def internal_headers():
    return {"X-Internal-Service-Token": "test-internal-token"}


@pytest.fixture
def edge_completion_headers():
    return {"X-Internal-Service-Token": TEST_EDGE_COMPLETION_SERVICE_TOKEN}
