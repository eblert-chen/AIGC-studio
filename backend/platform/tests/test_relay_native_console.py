from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from platform_api.asset_storage import HuaweiObsInputAssetStore
from platform_api.auth import OidcIdTokenClaims
from platform_api.config import Settings
from platform_api.database import Base, build_session_factory
from platform_api.main import create_app
from platform_api.models import AuditLog, AuthSession, ExternalIdentity, User
from platform_api.platform_admin_access_policy import (
    resolve_platform_admin_route_permission,
)
from platform_api.services.platform_admin_access import PlatformAdminAccessService
from platform_api.services.authentication import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    SessionService,
)

from .test_engineering import production_settings
from .test_platform_admin import bootstrap_admin

PATH = "/api/v1/platform-admin/relay/native-console/open"
ACTION = "relay.native_console.launch_authorized"


def _grant_relay_manage(client, *, owner_headers, delegated_id: str) -> None:
    role = client.post(
        "/api/v1/platform-admin/access/roles",
        headers=owner_headers,
        json={
            "key": f"relay-native-console-{uuid4().hex}",
            "display_name": "Relay native console operator",
            "description": "Delegated non-production native console access",
            "permission_codes": ["platform.relay_health.manage"],
            "change_reason": "Grant controlled Relay native console access",
        },
    )
    assert role.status_code == 201, role.text
    assigned = client.put(
        f"/api/v1/platform-admin/access/users/{delegated_id}",
        headers=owner_headers,
        json={
            "role_ids": [role.json()["id"]],
            "permission_overrides": {},
            "expected_lock_version": 0,
            "change_reason": "Grant controlled Relay native console access",
        },
    )
    assert assigned.status_code == 200, assigned.text


def _jwt(*, user_id: str, auth_time: int) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user_id,
        "platform_admin": True,
        "iss": "ai-video-platform",
        "aud": "ai-video-web",
        "iat": now,
        "exp": now + 300,
        "auth_time": auth_time,
        "amr": ["webauthn"],
    }

    def encode(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    signing_input = f"{encode(header)}.{encode(payload)}"
    signature = hmac.new(
        b"jwt-signing-secret-32-bytes-minimum!!",
        signing_input.encode(),
        hashlib.sha256,
    ).digest()
    return (
        f"{signing_input}."
        f"{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"
    )


def test_native_console_route_has_explicit_manage_policy() -> None:
    assert (
        resolve_platform_admin_route_permission(
            method="POST",
            route_path=PATH,
        )
        == "platform.relay_health.manage"
    )


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("https://Relay-Admin.OPS.Example:443/", "https://relay-admin.ops.example"),
        ("http://localhost:5173/", "http://localhost:5173"),
        ("http://127.0.0.1:3000", "http://127.0.0.1:3000"),
        ("http://[::1]:3000/", "http://[::1]:3000"),
        ("", None),
    ],
)
def test_native_console_origin_is_normalized(
    configured: str, expected: str | None
) -> None:
    settings = Settings(relay_native_admin_console_origin=configured)
    assert settings.relay_native_admin_console_origin == expected


@pytest.mark.parametrize(
    "configured",
    [
        "http://relay-admin.ops.example",
        "https://user:password@relay-admin.ops.example",
        "https://relay-admin.ops.example/channels",
        "https://relay-admin.ops.example/../channels",
        "https://relay-admin.ops.example/%2e%2e/channels",
        "https://relay-admin.ops.example?next=evil",
        "https://relay-admin.ops.example?",
        "https://relay-admin.ops.example#fragment",
        "https://relay-admin.ops.example#",
        "https://relay-admin.ops.example\\@evil.example",
        "https://relay-admin.ops.example\n",
    ],
)
def test_native_console_origin_rejects_caller_navigation_surface(
    configured: str,
) -> None:
    with pytest.raises(ValidationError, match="RELAY_NATIVE_ADMIN_CONSOLE_ORIGIN"):
        Settings(relay_native_admin_console_origin=configured)


def test_production_native_console_origin_requires_https_dns_and_port_443() -> None:
    accepted = production_settings(
        relay_native_admin_console_origin="https://Relay-Admin.OPS.Example:443/"
    )
    assert (
        accepted.relay_native_admin_console_origin == "https://relay-admin.ops.example"
    )

    for configured in (
        "http://relay-admin.ops.example",
        "https://localhost",
        "https://127.0.0.1",
        "https://[::1]",
        "https://2130706433",
        "https://0x7f000001",
        "https://relay-admin.ops.example:8443",
    ):
        with pytest.raises(
            ValidationError,
            match="Production RELAY_NATIVE_ADMIN_CONSOLE_ORIGIN",
        ):
            production_settings(relay_native_admin_console_origin=configured)


def test_native_console_requires_exact_empty_object_and_owner_boundary(
    client,
    app,
) -> None:
    owner_id, owner_headers = bootstrap_admin(client, "native-console-owner")
    delegated_id, delegated_headers = bootstrap_admin(
        client, "native-console-delegated"
    )
    app.state.settings.relay_native_admin_console_origin = "http://127.0.0.1:3000"

    denied = client.post(PATH, headers=delegated_headers, json={})
    assert denied.status_code == 403
    assert "platform.relay_health.manage" in denied.json()["detail"]
    assert denied.json()["code"] == "permission_denied"
    assert denied.headers["cache-control"] == "private, no-store"
    assert denied.headers["pragma"] == "no-cache"
    assert denied.headers["referrer-policy"] == "no-referrer"

    _grant_relay_manage(
        client,
        owner_headers=owner_headers,
        delegated_id=delegated_id,
    )
    still_denied = client.post(PATH, headers=delegated_headers, json={})
    assert still_denied.status_code == 403
    assert still_denied.json()["detail"] == {
        "code": "RELAY_NATIVE_CONSOLE_OWNER_REQUIRED",
        "message": "Relay native console access is restricted to the platform owner",
    }
    assert still_denied.headers["cache-control"] == "private, no-store"
    assert still_denied.headers["pragma"] == "no-cache"
    assert still_denied.headers["referrer-policy"] == "no-referrer"

    for kwargs in (
        {},
        {"json": []},
        {"json": {"url": "https://evil.example"}},
    ):
        rejected = client.post(PATH, headers=owner_headers, **kwargs)
        assert rejected.status_code == 422
        assert rejected.headers["cache-control"] == "private, no-store"
        assert rejected.headers["pragma"] == "no-cache"
        assert rejected.headers["referrer-policy"] == "no-referrer"

    opened = client.post(
        PATH,
        headers={**owner_headers, "X-Request-ID": "native-console-open-001"},
        json={},
    )
    assert opened.status_code == 200, opened.text
    assert opened.json() == {
        "url": "http://127.0.0.1:3000/channels",
        "mode": "native_break_glass",
    }
    assert opened.headers["cache-control"] == "private, no-store"
    assert opened.headers["pragma"] == "no-cache"
    assert opened.headers["referrer-policy"] == "no-referrer"

    with app.state.session_factory() as session:
        audits = list(
            session.scalars(select(AuditLog).where(AuditLog.action == ACTION)).all()
        )
    assert len(audits) == 1
    audit = audits[0]
    assert audit.actor_user_id == owner_id
    assert audit.request_id == "native-console-open-001"
    assert audit.before_summary == {}
    assert audit.after_summary == {
        "mode": "native_break_glass",
        "destination_origin": "http://127.0.0.1:3000",
        "destination_path": "/channels",
    }


def test_native_console_missing_configuration_fails_closed_without_audit(
    client,
    app,
) -> None:
    _, owner_headers = bootstrap_admin(client, "native-console-unconfigured")
    response = client.post(PATH, headers=owner_headers, json={})
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "RELAY_NATIVE_CONSOLE_NOT_CONFIGURED",
        "message": "Relay native administrator console is not configured",
    }
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"
    with app.state.session_factory() as session:
        assert session.scalar(select(AuditLog).where(AuditLog.action == ACTION)) is None


def test_production_native_console_requires_recent_owner_step_up() -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = build_session_factory(engine)
    owner_id = str(uuid4())
    delegated_id = str(uuid4())
    with session_factory.begin() as session:
        session.add_all(
            (
                User(
                    id=owner_id,
                    email="native-console-owner@example.com",
                    display_name="Native Console Owner",
                    is_platform_admin=True,
                ),
                User(
                    id=delegated_id,
                    email="native-console-delegated@example.com",
                    display_name="Native Console Delegate",
                    is_platform_admin=True,
                ),
            )
        )

    settings = production_settings(
        platform_owner_user_ids=[owner_id],
        relay_native_admin_console_origin="https://relay-admin.ops.example",
    )
    app = create_app(
        settings=settings,
        engine=engine,
        input_asset_store=HuaweiObsInputAssetStore(object(), "test-bucket"),
    )
    with app.state.session_factory.begin() as session:
        role = PlatformAdminAccessService.create_role(
            session,
            actor_user_id=owner_id,
            key="native-console-production-operator",
            display_name="Native console production operator",
            description="Prove owner-only protection beyond delegated permission",
            permission_codes={"platform.relay_health.manage"},
            platform_owner_user_ids={owner_id},
            request_id="native-console-role-create",
            change_reason="Grant Relay health management for owner boundary test",
        )
        PlatformAdminAccessService.replace_user_access(
            session,
            actor_user_id=owner_id,
            target_user_id=delegated_id,
            role_ids={role.id},
            permission_overrides={},
            expected_lock_version=0,
            platform_owner_user_ids={owner_id},
            request_id="native-console-role-assign",
            change_reason="Grant Relay health management for owner boundary test",
        )

    now = int(time.time())
    raw_sessions = {}
    raw_csrf = {}
    with app.state.session_factory.begin() as session:
        for user_id in (owner_id, delegated_id):
            user = session.get(User, user_id)
            assert user is not None
            identity = ExternalIdentity(
                user_id=user.id,
                issuer=settings.oidc_issuer,
                subject=user.id,
                email_at_link=user.email,
            )
            session.add(identity)
            session.flush()
            _, raw_sessions[user.id], raw_csrf[user.id] = SessionService.create(
                session,
                user=user,
                identity=identity,
                claims=OidcIdTokenClaims(
                    issuer=identity.issuer,
                    subject=identity.subject,
                    email=user.email,
                    display_name=user.display_name,
                    issued_at=now,
                    expires_at=now + 3600,
                    authentication_time=now,
                    authentication_methods=("webauthn",),
                ),
                ttl_seconds=3600,
                user_agent="native-console-contract",
                pepper=settings.jwt_signing_secret,
                ip_hash="9" * 64,
            )
    try:
        with TestClient(app, base_url="https://testserver") as client:
            preflight = client.options(
                PATH,
                headers={
                    "Origin": "https://app.example.com",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": (
                        "content-type,x-csrf-token,x-request-id"
                    ),
                },
            )
            assert preflight.status_code == 200, preflight.text
            assert (
                preflight.headers["access-control-allow-origin"]
                == "https://app.example.com"
            )
            assert "POST" in preflight.headers["access-control-allow-methods"]
            assert preflight.headers["access-control-allow-credentials"] == "true"

            client.cookies.set(SESSION_COOKIE_NAME, raw_sessions[owner_id])
            client.cookies.set(CSRF_COOKIE_NAME, raw_csrf[owner_id])
            with app.state.session_factory.begin() as session:
                auth_session = session.scalar(
                    select(AuthSession).where(AuthSession.user_id == owner_id)
                )
                assert auth_session is not None
                auth_session.auth_time = datetime.now(timezone.utc) - timedelta(
                    seconds=301
                )

            stale = client.post(
                PATH,
                headers={
                    "Origin": "https://app.example.com",
                    CSRF_HEADER_NAME: raw_csrf[owner_id],
                    "X-Request-ID": "production-native-console-stale-001",
                },
                json={},
            )
            assert stale.status_code == 403
            assert stale.headers["x-auth-required"] == "step-up"
            assert (
                stale.headers["access-control-allow-origin"]
                == "https://app.example.com"
            )
            exposed_headers = {
                value.strip().casefold()
                for value in stale.headers["access-control-expose-headers"].split(",")
            }
            assert {"x-request-id", "x-auth-required"} <= exposed_headers
            assert stale.headers["access-control-allow-credentials"] == "true"
            assert stale.headers["cache-control"] == "private, no-store"
            assert stale.headers["pragma"] == "no-cache"
            assert stale.headers["referrer-policy"] == "no-referrer"

            client.cookies.set(SESSION_COOKIE_NAME, raw_sessions[delegated_id])
            client.cookies.set(CSRF_COOKIE_NAME, raw_csrf[delegated_id])
            delegated = client.post(
                PATH,
                headers={
                    "Origin": "https://app.example.com",
                    CSRF_HEADER_NAME: raw_csrf[delegated_id],
                },
                json={},
            )
            assert delegated.status_code == 403
            assert delegated.json()["detail"]["code"] == (
                "RELAY_NATIVE_CONSOLE_OWNER_REQUIRED"
            )

            with app.state.session_factory.begin() as session:
                auth_session = session.scalar(
                    select(AuthSession).where(AuthSession.user_id == owner_id)
                )
                assert auth_session is not None
                auth_session.auth_time = datetime.now(timezone.utc)
            client.cookies.set(SESSION_COOKIE_NAME, raw_sessions[owner_id])
            client.cookies.set(CSRF_COOKIE_NAME, raw_csrf[owner_id])
            opened = client.post(
                PATH,
                headers={
                    "Origin": "https://app.example.com",
                    CSRF_HEADER_NAME: raw_csrf[owner_id],
                    "X-Request-ID": "production-native-console-open-001",
                },
                json={},
            )
            assert opened.status_code == 200, opened.text
            assert opened.json() == {
                "url": "https://relay-admin.ops.example/channels",
                "mode": "native_break_glass",
            }
    finally:
        engine.dispose()
