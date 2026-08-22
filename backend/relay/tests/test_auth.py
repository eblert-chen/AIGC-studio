from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import pytest

from relay_service.artifacts import InMemoryArtifactStore
from relay_service.auth import (
    DEVELOPMENT_API_KEY,
    ClientCredential,
    StaticClientAuthenticator,
)
from relay_service.config import RelaySettings
from relay_service.main import create_app
from relay_service.providers.router import ProviderRouter
from relay_service.queue import InMemoryWorkQueue
from relay_service.repository import InMemoryJobRepository


def _production_settings() -> RelaySettings:
    return RelaySettings(
        environment="production",
        runtime_mode="production",
        database_url="postgresql+asyncpg://db/relay",
        redis_url="redis://queue",
        artifact_store="huawei_obs",
        provider_alert_webhook_url="https://alerts.example.com/relay",
        provider_alert_signing_secret="x" * 48,
    )


def _production_credentials() -> tuple[dict[str, object], UUID, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    return (
        {
            "customer-platform": {
                "tenant_id": str(tenant_a),
                "api_key": "a" * 32,
            },
            "internal-tiktok": {
                "tenant_id": str(tenant_b),
                "api_key": "b" * 48,
            },
        },
        tenant_a,
        tenant_b,
    )


def _create_production_app(**overrides):
    arguments = {
        "repository": InMemoryJobRepository(),
        "queue": InMemoryWorkQueue(),
        "transfer_queue": InMemoryWorkQueue(),
        "router": ProviderRouter([]),
        "artifact_store": InMemoryArtifactStore(),
        "settings": _production_settings(),
        "process_in_background": False,
    }
    arguments.update(overrides)
    return create_app(**arguments)


def test_production_json_supports_multiple_tenant_bound_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, tenant_a, tenant_b = _production_credentials()
    monkeypatch.setenv("RELAY_CLIENT_CREDENTIALS_JSON", json.dumps(payload))

    authenticator = StaticClientAuthenticator.from_environment(
        environment="production"
    )

    principal_a = asyncio.run(
        authenticator.authenticate("customer-platform", "a" * 32)
    )
    principal_b = asyncio.run(
        authenticator.authenticate("internal-tiktok", "b" * 48)
    )
    assert principal_a.tenant_id == tenant_a
    assert principal_b.tenant_id == tenant_b


def test_development_keeps_single_client_environment_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid4()
    monkeypatch.delenv("RELAY_CLIENT_CREDENTIALS_JSON", raising=False)
    monkeypatch.setenv("RELAY_CLIENT_ID", "legacy-compose-client")
    monkeypatch.setenv("RELAY_API_KEY", "short-development-key")
    monkeypatch.setenv("RELAY_TENANT_ID", str(tenant_id))

    authenticator = StaticClientAuthenticator.from_environment(
        environment="development"
    )

    principal = asyncio.run(
        authenticator.authenticate(
            "legacy-compose-client", "short-development-key"
        )
    )
    assert principal.tenant_id == tenant_id


def test_production_does_not_fall_back_to_development_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RELAY_CLIENT_CREDENTIALS_JSON", raising=False)
    monkeypatch.setenv("RELAY_CLIENT_ID", "looks-explicit-but-is-legacy")
    monkeypatch.setenv("RELAY_API_KEY", "x" * 64)
    monkeypatch.setenv("RELAY_TENANT_ID", str(uuid4()))

    with pytest.raises(
        RuntimeError,
        match="RELAY_CLIENT_CREDENTIALS_JSON is required",
    ):
        StaticClientAuthenticator.from_environment(environment="production")


@pytest.mark.parametrize(
    ("serialized", "message"),
    [
        ("not-json", "valid JSON"),
        ("{}", "non-empty object"),
        ("[]", "non-empty object"),
        (
            '{"duplicate":{"tenant_id":"00000000-0000-4000-8000-000000000001",'
            '"api_key":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},'
            '"duplicate":{"tenant_id":"00000000-0000-4000-8000-000000000002",'
            '"api_key":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}',
            "duplicate keys",
        ),
    ],
)
def test_explicit_credentials_json_fails_closed_on_invalid_shape(
    monkeypatch: pytest.MonkeyPatch,
    serialized: str,
    message: str,
) -> None:
    monkeypatch.setenv("RELAY_CLIENT_CREDENTIALS_JSON", serialized)

    with pytest.raises(RuntimeError, match=message):
        StaticClientAuthenticator.from_environment(environment="production")


@pytest.mark.parametrize(
    ("api_key", "message"),
    [
        ("too-short", "at least 32 bytes"),
        (DEVELOPMENT_API_KEY, "known development default"),
    ],
)
def test_production_rejects_weak_or_development_api_keys(
    monkeypatch: pytest.MonkeyPatch,
    api_key: str,
    message: str,
) -> None:
    monkeypatch.setenv(
        "RELAY_CLIENT_CREDENTIALS_JSON",
        json.dumps(
            {
                "client": {
                    "tenant_id": str(uuid4()),
                    "api_key": api_key,
                }
            }
        ),
    )

    with pytest.raises(RuntimeError, match=message):
        StaticClientAuthenticator.from_environment(environment="production")


@pytest.mark.parametrize(
    "api_key",
    [
        "replace-with-32-byte-random-secret",
        "replace-with-another-32-byte-random-secret",
        "please-CHANGE_ME-before-production-1234567890",
        "your_api_key_goes_here_12345678901234567890",
    ],
)
def test_production_rejects_obvious_placeholder_api_keys(
    monkeypatch: pytest.MonkeyPatch,
    api_key: str,
) -> None:
    monkeypatch.setenv(
        "RELAY_CLIENT_CREDENTIALS_JSON",
        json.dumps(
            {
                "client": {
                    "tenant_id": str(uuid4()),
                    "api_key": api_key,
                }
            }
        ),
    )

    with pytest.raises(RuntimeError, match="obvious placeholder"):
        StaticClientAuthenticator.from_environment(environment="production")


def test_production_rejects_api_key_reused_by_multiple_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_api_key = "same-secret-material-for-two-clients-1234567890"
    monkeypatch.setenv(
        "RELAY_CLIENT_CREDENTIALS_JSON",
        json.dumps(
            {
                "customer-platform": {
                    "tenant_id": str(uuid4()),
                    "api_key": shared_api_key,
                },
                "internal-tiktok": {
                    "tenant_id": str(uuid4()),
                    "api_key": shared_api_key,
                },
            }
        ),
    )

    with pytest.raises(RuntimeError, match="must use unique api_key values"):
        StaticClientAuthenticator.from_environment(environment="production")


def test_production_rejects_tenant_reused_by_multiple_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_tenant_id = uuid4()
    monkeypatch.setenv(
        "RELAY_CLIENT_CREDENTIALS_JSON",
        json.dumps(
            {
                "customer-platform": {
                    "tenant_id": str(shared_tenant_id),
                    "api_key": "a" * 32,
                },
                "internal-tiktok": {
                    "tenant_id": str(shared_tenant_id),
                    "api_key": "b" * 32,
                },
            }
        ),
    )

    with pytest.raises(RuntimeError, match="distinct tenant identities"):
        StaticClientAuthenticator.from_environment(environment="production")


def test_development_credentials_json_also_rejects_reused_api_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_api_key = "shared-development-key"
    monkeypatch.setenv(
        "RELAY_CLIENT_CREDENTIALS_JSON",
        json.dumps(
            {
                "development-a": {
                    "tenant_id": str(uuid4()),
                    "api_key": shared_api_key,
                },
                "development-b": {
                    "tenant_id": str(uuid4()),
                    "api_key": shared_api_key,
                },
            }
        ),
    )

    with pytest.raises(RuntimeError, match="must use unique api_key values"):
        StaticClientAuthenticator.from_environment(environment="development")


def test_create_app_builds_default_authenticator_for_settings_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = StaticClientAuthenticator(
        {
            "client": ClientCredential(
                tenant_id=uuid4(),
                api_key="injected-by-factory",
            )
        }
    )
    seen: list[str] = []

    def from_environment(*, environment: str):
        seen.append(environment)
        return credential

    monkeypatch.setattr(
        StaticClientAuthenticator,
        "from_environment",
        staticmethod(from_environment),
    )

    app = _create_production_app()

    assert app
    assert seen == ["production"]


def test_injected_authenticator_bypasses_environment_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authenticator = StaticClientAuthenticator(
        {
            "client": ClientCredential(
                tenant_id=uuid4(),
                api_key="explicit-test-authenticator",
            )
        }
    )

    def fail_if_called(*, environment: str):
        raise AssertionError(
            f"unexpected environment credential load: {environment}"
        )

    monkeypatch.setattr(
        StaticClientAuthenticator,
        "from_environment",
        staticmethod(fail_if_called),
    )

    app = _create_production_app(authenticator=authenticator)

    assert app
