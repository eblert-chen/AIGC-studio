from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from sqlalchemy import select

from platform_api.config import Settings
from platform_api.models import AuditLog
from platform_api.platform_admin_access_policy import resolve_platform_admin_route_permission

from .test_platform_admin import bootstrap_admin
from .test_relay_native_console import _grant_relay_manage


PATH = "/api/v1/platform-admin/relay/provider-onboarding/open"
ACTION = "relay.provider_onboarding.launch_authorized"


def test_provider_onboarding_launcher_has_explicit_manage_permission():
    assert resolve_platform_admin_route_permission(method="POST", route_path=PATH) == "platform.relay_health.manage"


@pytest.mark.parametrize("configured,expected", [
    ("https://Relay-Admin.OPS.Example:443/", "https://relay-admin.ops.example/provider-onboarding"),
    ("http://127.0.0.1:8300", "http://127.0.0.1:8300/provider-onboarding"),
    ("http://localhost:8300/", "http://localhost:8300/provider-onboarding"),
    ("http://[::1]:8300", "http://[::1]:8300/provider-onboarding"),
])
def test_provider_onboarding_owner_gets_only_server_owned_origin_and_path(app, client, configured, expected):
    owner_id, headers = bootstrap_admin(client, "provider-onboarding-origin")
    app.state.settings.relay_native_admin_console_origin = Settings(
        relay_native_admin_console_origin=configured
    ).relay_native_admin_console_origin
    response = client.post(PATH, headers={**headers, "X-Request-ID": "provider-onboarding-open-001"}, json={})
    assert response.status_code == 200, response.text
    assert response.json() == {"mode": "provider_onboarding", "url": expected}
    parts = urlsplit(response.json()["url"])
    assert parts.path == "/provider-onboarding"
    assert not parts.query and not parts.fragment and not parts.username and not parts.password
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"
    with app.state.session_factory() as session:
        audits = session.scalars(select(AuditLog).where(AuditLog.action == ACTION)).all()
        assert len(audits) == 1
        assert audits[0].actor_user_id == owner_id
        assert audits[0].request_id == "provider-onboarding-open-001"
        assert audits[0].target_type == "relay_provider_onboarding"
        assert audits[0].after_summary == {
            "mode": "provider_onboarding",
            "destination_origin": app.state.settings.relay_native_admin_console_origin,
            "destination_path": "/provider-onboarding",
        }
        assert session.scalar(select(AuditLog).where(AuditLog.action == "relay.native_console.launch_authorized")) is None


def test_provider_onboarding_launcher_keeps_owner_boundary_even_with_relay_manage(app, client):
    _, owner_headers = bootstrap_admin(client, "provider-onboarding-owner")
    delegated_id, delegated_headers = bootstrap_admin(client, "provider-onboarding-delegate")
    app.state.settings.relay_native_admin_console_origin = "http://127.0.0.1:8300"
    assert client.post(PATH, json={}).status_code == 401
    denied = client.post(PATH, headers=delegated_headers, json={})
    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_denied"
    _grant_relay_manage(client, owner_headers=owner_headers, delegated_id=delegated_id)
    denied = client.post(PATH, headers=delegated_headers, json={})
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "RELAY_PROVIDER_ONBOARDING_OWNER_REQUIRED"
    assert denied.headers["cache-control"] == "private, no-store"
    assert denied.headers["referrer-policy"] == "no-referrer"
    with app.state.session_factory() as session:
        assert session.scalar(select(AuditLog).where(AuditLog.action == ACTION)) is None


@pytest.mark.parametrize("payload", [
    None,
    [],
    {"url": "https://untrusted.example/provider-onboarding"},
    {"path": "/channels"},
    {"path": "/provider-onboarding/../channels"},
    {"origin": "https://untrusted.example"},
    {"mode": "native_break_glass"},
    {"token": "caller-controlled"},
])
def test_provider_onboarding_launcher_rejects_navigation_or_credential_input(app, client, payload):
    _, headers = bootstrap_admin(client, "provider-onboarding-empty-intent")
    app.state.settings.relay_native_admin_console_origin = "http://127.0.0.1:8300"
    response = client.post(PATH, headers=headers, json=payload)
    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"
    with app.state.session_factory() as session:
        assert session.scalar(select(AuditLog).where(AuditLog.action == ACTION)) is None


def test_provider_onboarding_launcher_missing_origin_has_no_audit(app, client):
    _, headers = bootstrap_admin(client, "provider-onboarding-unconfigured")
    response = client.post(PATH, headers=headers, json={})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "RELAY_PROVIDER_ONBOARDING_NOT_CONFIGURED"
    assert response.headers["cache-control"] == "private, no-store"
    with app.state.session_factory() as session:
        assert session.scalar(select(AuditLog).where(AuditLog.action == ACTION)) is None
