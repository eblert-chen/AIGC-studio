from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import time

from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_api.auth import OidcIdTokenClaims
from platform_api.database import Base, build_session_factory
from platform_api.models import AuditLog, AuthSession, ExternalIdentity, User
from platform_api.platform_admin_access_catalog import (
    PLATFORM_ADMIN_PERMISSION_BY_CODE,
    PLATFORM_ADMIN_PERMISSION_CODES,
)
from platform_api.platform_admin_access_models import (
    PlatformAdminPermissionEffect,
)
from platform_api.platform_admin_access_dependencies import (
    GranularPlatformAdminContext,
    require_platform_admin_permission,
)
from platform_api.services.errors import ConflictError, PermissionDeniedError
from platform_api.services.authentication import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    SessionService,
)
from platform_api.services.platform_admin_access import PlatformAdminAccessService

from .test_platform_admin import bootstrap_admin


OWNER_ID = "00000000-0000-4000-8000-000000000001"
ADMIN_ID = "00000000-0000-4000-8000-000000000002"
OTHER_ID = "00000000-0000-4000-8000-000000000003"
OWNER_IDS = frozenset({OWNER_ID})


@pytest.fixture
def access_session():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            (
                User(
                    id=OWNER_ID,
                    email="product-owner@example.com",
                    display_name="Product Owner",
                    is_platform_admin=True,
                ),
                User(
                    id=ADMIN_ID,
                    email="operations-admin@example.com",
                    display_name="Operations Admin",
                    is_platform_admin=True,
                ),
                User(
                    id=OTHER_ID,
                    email="ordinary-user@example.com",
                    display_name="Ordinary User",
                    is_platform_admin=False,
                ),
            )
        )
        session.commit()
        yield session
        session.rollback()
    engine.dispose()


def _create_role(
    session: Session,
    *,
    key: str,
    permission_codes: set[str],
):
    return PlatformAdminAccessService.create_role(
        session,
        actor_user_id=OWNER_ID,
        key=key,
        display_name=key.replace("-", " ").title(),
        description="Focused delegated role",
        permission_codes=permission_codes,
        platform_owner_user_ids=OWNER_IDS,
        request_id=f"create-{key}",
        change_reason="Initial least-privilege role",
    )


def test_catalog_has_read_manage_pair_for_every_required_domain():
    expected_domains = {
        "analytics",
        "companies",
        "entitlements",
        "models",
        "resources",
        "finance",
        "provider_costs",
        "publishing_exceptions",
        "asset_exceptions",
        "audit",
        "relay_health",
        "admin_access",
    }
    assert {
        permission.domain
        for permission in PLATFORM_ADMIN_PERMISSION_BY_CODE.values()
    } == expected_domains
    assert len(PLATFORM_ADMIN_PERMISSION_CODES) == len(expected_domains) * 2
    for domain in expected_domains:
        assert f"platform.{domain}.read" in PLATFORM_ADMIN_PERMISSION_CODES
        assert f"platform.{domain}.manage" in PLATFORM_ADMIN_PERMISSION_CODES


def test_legacy_route_policy_resolves_templates_and_unknown_routes_fail_closed(
    access_session: Session,
):
    assert PlatformAdminAccessService.permission_for_request(
        method="GET", route_path="/api/v1/platform-admin/companies"
    ) == "platform.companies.read"
    assert PlatformAdminAccessService.permission_for_request(
        method="PUT",
        route_path="/api/v1/platform-admin/companies/company-1/resources/resource-1",
    ) == "platform.entitlements.manage"
    assert PlatformAdminAccessService.permissions_for_request(
        method="GET",
        route_path="/api/v1/platform-admin/analytics/exceptions",
    ) == (
        "platform.publishing_exceptions.read",
        "platform.asset_exceptions.read",
        "platform.relay_health.read",
    )
    assert PlatformAdminAccessService.permission_for_request(
        method="GET",
        route_path="/api/v1/platform-admin/analytics/data-readiness",
    ) == "platform.analytics.read"
    callback_event_id = "b9b2537e-258c-4a98-af8a-6d23bdb135a4"
    assert PlatformAdminAccessService.permission_for_request(
        method="GET",
        route_path=f"/api/v1/platform-admin/relay/callback-dead-letters/{callback_event_id}",
    ) == "platform.relay_health.read"
    assert PlatformAdminAccessService.permission_for_request(
        method="POST",
        route_path=f"/api/v1/platform-admin/relay/callback-dead-letters/{callback_event_id}/redrive",
    ) == "platform.relay_health.manage"
    channel_id = "17"
    channel_operation_id = "channel-operation-0001"
    assert PlatformAdminAccessService.permission_for_request(
        method="GET",
        route_path="/api/v1/platform-admin/relay/channels",
    ) == "platform.relay_health.read"
    assert PlatformAdminAccessService.permission_for_request(
        method="GET",
        route_path=f"/api/v1/platform-admin/relay/channels/{channel_id}",
    ) == "platform.relay_health.read"
    assert PlatformAdminAccessService.permission_for_request(
        method="GET",
        route_path=(
            f"/api/v1/platform-admin/relay/channels/{channel_id}/operations/"
            f"{channel_operation_id}"
        ),
    ) == "platform.relay_health.read"
    assert PlatformAdminAccessService.permission_for_request(
        method="POST",
        route_path=f"/api/v1/platform-admin/relay/channels/{channel_id}/test",
    ) == "platform.relay_health.manage"
    assert PlatformAdminAccessService.permission_for_request(
        method="POST",
        route_path=f"/api/v1/platform-admin/relay/channels/{channel_id}/status",
    ) == "platform.relay_health.manage"
    assert PlatformAdminAccessService.permission_for_request(
        method="POST", route_path="/api/v1/platform-admin/future-unsafe-action"
    ) is None
    with pytest.raises(PermissionDeniedError, match="no delegated access policy"):
        PlatformAdminAccessService.authorize_request(
            access_session,
            user_id=ADMIN_ID,
            platform_owner_user_ids=OWNER_IDS,
            method="POST",
            route_path="/api/v1/platform-admin/future-unsafe-action",
        )
    exception_reader = _create_role(
        access_session,
        key="publishing-exception-reader",
        permission_codes={"platform.publishing_exceptions.read"},
    )
    PlatformAdminAccessService.replace_user_access(
        access_session,
        actor_user_id=OWNER_ID,
        target_user_id=ADMIN_ID,
        role_ids={exception_reader.id},
        permission_overrides={},
        expected_lock_version=0,
        platform_owner_user_ids=OWNER_IDS,
        request_id="assign-partial-exception-reader",
        change_reason="Verify conjunctive exception policy",
    )
    with pytest.raises(
        PermissionDeniedError, match="platform.asset_exceptions.read"
    ):
        PlatformAdminAccessService.authorize_request(
            access_session,
            user_id=ADMIN_ID,
            platform_owner_user_ids=OWNER_IDS,
            method="GET",
            route_path="/api/v1/platform-admin/analytics/exceptions",
        )
    # The protected owner boundary does not depend on mutable route policies.
    assert PlatformAdminAccessService.authorize_request(
        access_session,
        user_id=OWNER_ID,
        platform_owner_user_ids=OWNER_IDS,
        method="POST",
        route_path="/api/v1/platform-admin/future-unsafe-action",
    ) is None


def test_owner_is_immutable_full_access_and_non_owner_starts_fail_closed(
    access_session: Session,
):
    assert PlatformAdminAccessService.effective_permissions(
        access_session,
        user_id=OWNER_ID,
        platform_owner_user_ids=OWNER_IDS,
    ) == PLATFORM_ADMIN_PERMISSION_CODES
    assert PlatformAdminAccessService.effective_permissions(
        access_session,
        user_id=ADMIN_ID,
        platform_owner_user_ids=OWNER_IDS,
    ) == frozenset()
    assert PlatformAdminAccessService.effective_permissions(
        access_session,
        user_id=OTHER_ID,
        platform_owner_user_ids=OWNER_IDS,
    ) == frozenset()
    with pytest.raises(ConflictError, match="owner access is immutable"):
        PlatformAdminAccessService.replace_user_access(
            access_session,
            actor_user_id=OWNER_ID,
            target_user_id=OWNER_ID,
            role_ids=set(),
            permission_overrides={},
            expected_lock_version=0,
            platform_owner_user_ids=OWNER_IDS,
            request_id="owner-mutation",
            change_reason="Must be rejected",
        )


def test_role_inheritance_personal_override_and_stale_snapshot_protection(
    access_session: Session,
):
    role = _create_role(
        access_session,
        key="operations-reader",
        permission_codes={
            "platform.analytics.read",
            "platform.analytics.manage",
            "platform.finance.read",
        },
    )
    access = PlatformAdminAccessService.replace_user_access(
        access_session,
        actor_user_id=OWNER_ID,
        target_user_id=ADMIN_ID,
        role_ids={role.id},
        permission_overrides={
            "platform.analytics.manage": PlatformAdminPermissionEffect.DENY,
            "platform.audit.read": PlatformAdminPermissionEffect.ALLOW,
        },
        expected_lock_version=0,
        platform_owner_user_ids=OWNER_IDS,
        request_id="grant-operations",
        change_reason="Operations shift coverage",
    )
    assert access.lock_version == 1
    assert access.role_ids == (role.id,)
    assert access.inherited_permissions == {
        "platform.analytics.read",
        "platform.analytics.manage",
        "platform.finance.read",
    }
    assert access.effective_permissions == {
        "platform.analytics.read",
        "platform.finance.read",
        "platform.audit.read",
    }
    assert access.snapshot.startswith("sha256:")
    assert len(access.snapshot) == 71

    with pytest.raises(ConflictError, match="changed elsewhere"):
        PlatformAdminAccessService.replace_user_access(
            access_session,
            actor_user_id=OWNER_ID,
            target_user_id=ADMIN_ID,
            role_ids=set(),
            permission_overrides={},
            expected_lock_version=0,
            platform_owner_user_ids=OWNER_IDS,
            request_id="stale-grant",
            change_reason="Stale editor must lose",
        )

    audit = access_session.scalar(
        select(AuditLog).where(AuditLog.request_id == "grant-operations")
    )
    assert audit.before_summary["lock_version"] == 0
    assert audit.after_summary["lock_version"] == 1
    assert audit.after_summary["change_reason"] == "Operations shift coverage"


def test_role_replacement_uses_version_and_deactivation_revokes_inheritance(
    access_session: Session,
):
    role = _create_role(
        access_session,
        key="finance-reader",
        permission_codes={"platform.finance.read"},
    )
    PlatformAdminAccessService.replace_user_access(
        access_session,
        actor_user_id=OWNER_ID,
        target_user_id=ADMIN_ID,
        role_ids={role.id},
        permission_overrides={},
        expected_lock_version=0,
        platform_owner_user_ids=OWNER_IDS,
        request_id="assign-finance",
        change_reason="Finance reporting access",
    )
    updated = PlatformAdminAccessService.replace_role(
        access_session,
        actor_user_id=OWNER_ID,
        role_id=role.id,
        display_name="Finance reader retired",
        description="No longer assignable or effective",
        active=False,
        permission_codes={"platform.finance.read"},
        expected_lock_version=1,
        platform_owner_user_ids=OWNER_IDS,
        request_id="retire-finance",
        change_reason="Finance team reorganized",
    )
    assert updated.lock_version == 2
    assert not updated.active
    assert PlatformAdminAccessService.effective_permissions(
        access_session,
        user_id=ADMIN_ID,
        platform_owner_user_ids=OWNER_IDS,
    ) == frozenset()
    with pytest.raises(ConflictError, match="changed elsewhere"):
        PlatformAdminAccessService.replace_role(
            access_session,
            actor_user_id=OWNER_ID,
            role_id=role.id,
            display_name="Stale update",
            description="Must fail",
            active=True,
            permission_codes={"platform.finance.read"},
            expected_lock_version=1,
            platform_owner_user_ids=OWNER_IDS,
            request_id="stale-role",
            change_reason="Stale editor must lose",
        )


def test_non_owner_cannot_delegate_permissions_they_do_not_hold(
    access_session: Session,
):
    access_manager = _create_role(
        access_session,
        key="access-manager",
        permission_codes={
            "platform.admin_access.read",
            "platform.admin_access.manage",
        },
    )
    PlatformAdminAccessService.replace_user_access(
        access_session,
        actor_user_id=OWNER_ID,
        target_user_id=ADMIN_ID,
        role_ids={access_manager.id},
        permission_overrides={},
        expected_lock_version=0,
        platform_owner_user_ids=OWNER_IDS,
        request_id="assign-access-manager",
        change_reason="Delegate access administration only",
    )
    with pytest.raises(PermissionDeniedError, match="does not hold"):
        PlatformAdminAccessService.create_role(
            access_session,
            actor_user_id=ADMIN_ID,
            key="finance-manager",
            display_name="Finance manager",
            description="Privilege escalation attempt",
            permission_codes={"platform.finance.manage"},
            platform_owner_user_ids=OWNER_IDS,
            request_id="escalation",
            change_reason="Should not be possible",
        )


def test_admin_deactivation_erases_grants_before_reactivation(
    access_session: Session,
):
    role = _create_role(
        access_session,
        key="analytics-reader",
        permission_codes={"platform.analytics.read"},
    )
    PlatformAdminAccessService.replace_user_access(
        access_session,
        actor_user_id=OWNER_ID,
        target_user_id=ADMIN_ID,
        role_ids={role.id},
        permission_overrides={},
        expected_lock_version=0,
        platform_owner_user_ids=OWNER_IDS,
        request_id="assign-analytics",
        change_reason="Temporary analytics assignment",
    )
    PlatformAdminAccessService.set_administrator_status(
        access_session,
        actor_user_id=OWNER_ID,
        target_user_id=ADMIN_ID,
        enabled=False,
        expected_is_platform_admin=True,
        platform_owner_user_ids=OWNER_IDS,
        request_id="disable-admin",
        change_reason="Administrator left the team",
    )
    PlatformAdminAccessService.set_administrator_status(
        access_session,
        actor_user_id=OWNER_ID,
        target_user_id=ADMIN_ID,
        enabled=True,
        expected_is_platform_admin=False,
        platform_owner_user_ids=OWNER_IDS,
        request_id="reactivate-admin",
        change_reason="Administrator returned in a new position",
    )
    snapshot = PlatformAdminAccessService.access_snapshot(
        access_session,
        user_id=ADMIN_ID,
        platform_owner_user_ids=OWNER_IDS,
    )
    assert snapshot.lock_version == 0
    assert snapshot.role_ids == ()
    assert snapshot.effective_permissions == frozenset()


def _jwt(*, user_id: str, auth_time: int, amr: list[str]) -> str:
    secret = "granular-admin-jwt-secret-32-bytes!!"
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user_id,
        "platform_admin": True,
        "iss": "granular-admin-tests",
        "aud": "granular-admin-web",
        "iat": now,
        "exp": now + 600,
        "auth_time": auth_time,
        "amr": amr,
    }

    def encode(value: dict) -> str:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    signing_input = f"{encode(header)}.{encode(payload)}"
    signature = hmac.new(
        secret.encode(), signing_input.encode(), hashlib.sha256
    ).digest()
    return (
        f"{signing_input}."
        + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    )


def test_production_guard_keeps_strong_auth_step_up_and_db_authorization():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = build_session_factory(engine)
    with session_factory.begin() as session:
        owner = User(
            id=OWNER_ID,
            email="guard-owner@example.com",
            display_name="Guard Owner",
            is_platform_admin=True,
        )
        admin = User(
            id=ADMIN_ID,
            email="guard-admin@example.com",
            display_name="Guard Admin",
            is_platform_admin=True,
        )
        session.add_all((owner, admin))
        session.flush()
        role = _create_role(
            session,
            key="guard-analytics-reader",
            permission_codes={"platform.analytics.read"},
        )
        PlatformAdminAccessService.replace_user_access(
            session,
            actor_user_id=OWNER_ID,
            target_user_id=ADMIN_ID,
            role_ids={role.id},
            permission_overrides={},
            expected_lock_version=0,
            platform_owner_user_ids=OWNER_IDS,
            request_id="guard-assignment",
            change_reason="Production guard coverage",
        )
        raw_sessions = {}
        raw_csrf = {}
        now = int(time.time())
        for user in (owner, admin):
            identity = ExternalIdentity(
                user_id=user.id,
                issuer="https://identity.example.test",
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
                user_agent="granular-admin-contract",
                pepper="granular-admin-cookie-pepper-32-bytes!!",
                ip_hash="8" * 64,
            )

    app = FastAPI()
    app.state.session_factory = session_factory
    app.state.settings = SimpleNamespace(
        environment="production",
        jwt_signing_secret="granular-admin-cookie-pepper-32-bytes!!",
        jwt_issuer="granular-admin-tests",
        jwt_audience="granular-admin-web",
        auth_legacy_bearer_enabled=False,
        auth_session_idle_ttl_seconds=3600,
        frontend_origin="https://app.example.com",
        oidc_issuer="https://identity.example.test",
        platform_admin_required_amr=["webauthn"],
        platform_admin_step_up_max_age_seconds=300,
        platform_owner_user_ids=[OWNER_ID],
    )

    @app.get("/analytics")
    def read_analytics(
        context: GranularPlatformAdminContext = Depends(
            require_platform_admin_permission("platform.analytics.read")
        ),
    ):
        return {"user_id": context.user_id}

    @app.post("/analytics")
    def manage_analytics(
        context: GranularPlatformAdminContext = Depends(
            require_platform_admin_permission("platform.analytics.manage")
        ),
    ):
        return {"user_id": context.user_id}

    with TestClient(app, base_url="https://testserver") as client:
        client.cookies.set(SESSION_COOKIE_NAME, raw_sessions[ADMIN_ID])
        client.cookies.set(CSRF_COOKIE_NAME, raw_csrf[ADMIN_ID])
        with session_factory.begin() as session:
            auth_session = session.scalar(
                select(AuthSession).where(AuthSession.user_id == ADMIN_ID)
            )
            assert auth_session is not None
            auth_session.amr = ["pwd"]
        assert client.get("/analytics").status_code == 403

        with session_factory.begin() as session:
            auth_session = session.scalar(
                select(AuthSession).where(AuthSession.user_id == ADMIN_ID)
            )
            assert auth_session is not None
            auth_session.amr = ["webauthn"]
            auth_session.auth_time = datetime.now(timezone.utc) - timedelta(seconds=301)
        assert client.get("/analytics").status_code == 200
        admin_proof = {
            "Origin": "https://app.example.com",
            CSRF_HEADER_NAME: raw_csrf[ADMIN_ID],
        }
        stale_write = client.post(
            "/analytics", headers=admin_proof
        )
        assert stale_write.status_code == 403
        assert stale_write.headers["x-auth-required"] == "step-up"

        with session_factory.begin() as session:
            auth_session = session.scalar(
                select(AuthSession).where(AuthSession.user_id == ADMIN_ID)
            )
            assert auth_session is not None
            auth_session.auth_time = datetime.now(timezone.utc)
        # Recent authentication cannot compensate for a missing server grant.
        assert client.post("/analytics", headers=admin_proof).status_code == 403

        client.cookies.set(SESSION_COOKIE_NAME, raw_sessions[OWNER_ID])
        client.cookies.set(CSRF_COOKIE_NAME, raw_csrf[OWNER_ID])
        assert client.post(
            "/analytics",
            headers={
                "Origin": "https://app.example.com",
                CSRF_HEADER_NAME: raw_csrf[OWNER_ID],
            },
        ).status_code == 200
    engine.dispose()


def test_integrated_legacy_routes_enforce_delegated_database_permissions(
    client,
):
    owner_id, owner_headers = bootstrap_admin(client, "granular-owner")
    delegated_id, delegated_headers = bootstrap_admin(client, "granular-delegated")

    role_response = client.post(
        "/api/v1/platform-admin/access/roles",
        headers=owner_headers,
        json={
            "key": "dashboard-observer",
            "display_name": "Dashboard observer",
            "description": "Can view operations but cannot mutate companies",
            "permission_codes": [
                "platform.admin_access.read",
                "platform.analytics.read",
            ],
            "change_reason": "Create read-only operations coverage",
        },
    )
    assert role_response.status_code == 201, role_response.text
    role_id = role_response.json()["id"]

    assigned = client.put(
        f"/api/v1/platform-admin/access/users/{delegated_id}",
        headers=owner_headers,
        json={
            "role_ids": [role_id],
            "permission_overrides": {},
            "expected_lock_version": 0,
            "change_reason": "Delegate read-only dashboard monitoring",
        },
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["effective_permissions"] == [
        "platform.admin_access.read",
        "platform.analytics.read",
    ]

    assert client.get(
        "/api/v1/platform-admin/dashboard", headers=delegated_headers
    ).status_code == 200
    assert client.get(
        "/api/v1/platform-admin/analytics/data-readiness",
        headers=delegated_headers,
    ).status_code == 200
    assert client.get(
        "/api/v1/platform-admin/access/permissions", headers=delegated_headers
    ).status_code == 200
    denied_read = client.get(
        "/api/v1/platform-admin/companies", headers=delegated_headers
    )
    assert denied_read.status_code == 403
    assert "platform.companies.read" in denied_read.json()["detail"]
    denied_write = client.post(
        "/api/v1/platform-admin/companies",
        headers=delegated_headers,
        json={
            "name": "Must not be created",
            "owner_email": "blocked@example.com",
            "owner_display_name": "Blocked",
        },
    )
    assert denied_write.status_code == 403
    assert "platform.companies.manage" in denied_write.json()["detail"]

    # The product owner remains complete and cannot be narrowed by an access row.
    owner_access = client.get(
        f"/api/v1/platform-admin/access/users/{owner_id}",
        headers=owner_headers,
    )
    assert owner_access.status_code == 200
    assert owner_access.json()["is_platform_owner"] is True
    assert set(owner_access.json()["effective_permissions"]) == set(
        PLATFORM_ADMIN_PERMISSION_CODES
    )


def test_integrated_channel_facade_splits_delegated_read_and_manage(
    client,
    app,
):
    _, owner_headers = bootstrap_admin(client, "channel-policy-owner")
    delegated_id, delegated_headers = bootstrap_admin(
        client, "channel-policy-delegated"
    )
    role_response = client.post(
        "/api/v1/platform-admin/access/roles",
        headers=owner_headers,
        json={
            "key": "relay-channel-reader",
            "display_name": "Relay channel reader",
            "description": "Can inspect channels but cannot invoke provider controls",
            "permission_codes": [
                "platform.admin_access.read",
                "platform.relay_health.read",
            ],
            "change_reason": "Delegate secret-free Relay channel monitoring",
        },
    )
    assert role_response.status_code == 201, role_response.text
    assigned = client.put(
        f"/api/v1/platform-admin/access/users/{delegated_id}",
        headers=owner_headers,
        json={
            "role_ids": [role_response.json()["id"]],
            "permission_overrides": {},
            "expected_lock_version": 0,
            "change_reason": "Grant Relay channel read-only coverage",
        },
    )
    assert assigned.status_code == 200, assigned.text

    app.state.relay_operations_client = SimpleNamespace(
        list_channels=lambda **_: SimpleNamespace(
            model_dump=lambda **__: {
                "api_version": "v1",
                "schema_version": 1,
                "object": "list",
                "data": [],
                "page": 1,
                "page_size": 50,
                "total": 0,
            }
        )
    )
    read = client.get(
        "/api/v1/platform-admin/relay/channels", headers=delegated_headers
    )
    assert read.status_code == 200, read.text
    denied = client.post(
        "/api/v1/platform-admin/relay/channels/17/test",
        headers=delegated_headers,
        json={
            "operation_id": "channel-operation-0001",
            "reason": "Verify the official channel before enabling traffic",
            "approved": True,
        },
    )
    assert denied.status_code == 403
    assert "platform.relay_health.manage" in denied.json()["detail"]


def _migration_config(project_root: Path, database_path: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url", f"sqlite:///{database_path.as_posix()}"
    )
    return config


def test_0024_migration_seeds_catalog_and_downgrades_cleanly(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "platform-admin-access.db"
    config = _migration_config(project_root, database_path)
    command.upgrade(config, "0023_download_gateway_attempts")
    command.upgrade(config, "0024_platform_admin_access")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT version_num FROM alembic_version")
        ) == "0024_platform_admin_access"
        assert connection.scalar(
            text("SELECT COUNT(*) FROM platform_admin_permissions")
        ) == 24
        assert connection.scalar(
            text(
                "SELECT COUNT(*) FROM platform_admin_permissions "
                "WHERE action = 'read'"
            )
        ) == 12
        tables = set(inspect(engine).get_table_names())
        assert {
            "platform_admin_permissions",
            "platform_admin_roles",
            "platform_admin_role_permissions",
            "platform_admin_access_profiles",
            "platform_admin_role_assignments",
            "platform_admin_user_permission_overrides",
        } <= tables
    engine.dispose()

    command.downgrade(config, "0023_download_gateway_attempts")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT version_num FROM alembic_version")
        ) == "0023_download_gateway_attempts"
        assert "platform_admin_permissions" not in inspect(engine).get_table_names()
    engine.dispose()
