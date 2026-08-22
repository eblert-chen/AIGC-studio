from __future__ import annotations

from datetime import timedelta
from time import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from platform_api.auth import OidcIdTokenClaims
from platform_api.database import Base
from platform_api.models import (
    AuthSession,
    CompanyInvitation,
    CompanyInvitationStatus,
    CompanyMembership,
    ExternalIdentity,
    MembershipRole,
    User,
    UserStatus,
    utcnow,
)
from platform_api.services.authentication import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    INVITATION_HANDOFF_COOKIE_NAME,
    OIDC_STATE_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    AccountService,
    InvitationService,
    OwnerTransferService,
    SessionService,
)
from platform_api.services.access_lifecycle import AccessLifecycleService
from platform_api.services.companies import CompanyService
from platform_api.services.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
)
from tests.test_production_auth_lifecycle import _auth_app, _login


def _claims(subject: str, email: str) -> OidcIdTokenClaims:
    now = time()
    return OidcIdTokenClaims(
        issuer="https://identity.example.test",
        subject=subject,
        email=email,
        display_name=subject,
        issued_at=now,
        expires_at=now + 300,
        authentication_time=now,
        authentication_methods=("webauthn",),
    )


def _identity(
    session: Session,
    *,
    subject: str,
    email: str,
    platform_admin: bool = False,
) -> tuple[User, ExternalIdentity]:
    user = User(
        email=email,
        display_name=subject,
        status=UserStatus.ACTIVE,
        email_verified_at=utcnow(),
        is_platform_admin=platform_admin,
    )
    session.add(user)
    session.flush()
    identity = ExternalIdentity(
        user_id=user.id,
        issuer="https://identity.example.test",
        subject=subject,
        email_at_link=email,
    )
    session.add(identity)
    session.flush()
    return user, identity


def _new_session(
    session: Session,
    *,
    user: User,
    identity: ExternalIdentity,
    pepper: str,
) -> tuple[AuthSession, str, str]:
    return SessionService.create(
        session,
        user=user,
        identity=identity,
        claims=_claims(identity.subject, user.email),
        ttl_seconds=3600,
        user_agent="contract-test",
        pepper=pepper,
        ip_hash="1" * 64,
    )


@pytest.fixture
def auth_session_factory() -> sessionmaker[Session]:
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def test_global_status_change_increments_auth_version_and_kills_every_session(
    auth_session_factory: sessionmaker[Session],
) -> None:
    pepper = "global-status-contract-pepper"
    with auth_session_factory.begin() as session:
        actor, _ = _identity(
            session,
            subject="platform-owner",
            email="platform-owner@example.com",
            platform_admin=True,
        )
        target, identity = _identity(
            session,
            subject="target-user",
            email="target-user@example.com",
        )
        first, first_token, _ = _new_session(
            session, user=target, identity=identity, pepper=pepper
        )
        second, second_token, _ = _new_session(
            session, user=target, identity=identity, pepper=pepper
        )
        original_version = target.auth_version

        changed = AccountService.set_global_status(
            session,
            actor_user_id=actor.id,
            target_user_id=target.id,
            expected_status=UserStatus.ACTIVE,
            expected_auth_version=original_version,
            target_status=UserStatus.SUSPENDED,
            platform_owner_user_ids=set(),
            request_id="global-status-suspend",
            ip_hash="2" * 64,
            user_agent="contract-test",
        )

        assert changed.status == UserStatus.SUSPENDED
        assert changed.auth_version == original_version + 1
        assert first.revoked_at is not None
        assert second.revoked_at is not None
        assert first.revoked_reason == "account_status_changed"
        assert second.revoked_reason == "account_status_changed"
        assert SessionService.resolve(
            session,
            raw_token=first_token,
            pepper=pepper,
            idle_ttl_seconds=3600,
        ) is None
        assert SessionService.resolve(
            session,
            raw_token=second_token,
            pepper=pepper,
            idle_ttl_seconds=3600,
        ) is None

        restored = AccountService.set_global_status(
            session,
            actor_user_id=actor.id,
            target_user_id=target.id,
            expected_status=UserStatus.SUSPENDED,
            expected_auth_version=original_version + 1,
            target_status=UserStatus.ACTIVE,
            platform_owner_user_ids=set(),
            request_id="global-status-restore",
            ip_hash="2" * 64,
            user_agent="contract-test",
        )
        assert restored.auth_version == original_version + 2
        assert first.revoked_at is not None and second.revoked_at is not None


def test_session_revocation_is_user_scoped_and_idempotent(
    auth_session_factory: sessionmaker[Session],
) -> None:
    pepper = "session-revocation-contract-pepper"
    with auth_session_factory.begin() as session:
        user_a, identity_a = _identity(
            session, subject="user-a", email="user-a@example.com"
        )
        user_b, identity_b = _identity(
            session, subject="user-b", email="user-b@example.com"
        )
        session_a, token_a, _ = _new_session(
            session, user=user_a, identity=identity_a, pepper=pepper
        )
        session_b, token_b, _ = _new_session(
            session, user=user_b, identity=identity_b, pepper=pepper
        )
        principal_a = SessionService.resolve(
            session,
            raw_token=token_a,
            pepper=pepper,
            idle_ttl_seconds=3600,
        )
        assert principal_a is not None

        with pytest.raises(NotFoundError, match="Session does not exist") as denied:
            SessionService.revoke(
                session,
                principal=principal_a,
                target_session_id=session_b.id,
                reason="cross-user-attempt",
                ip_hash="3" * 64,
                user_agent="contract-test",
            )
        assert denied.value.code == "not_found"
        assert session_b.revoked_at is None

        _, changed = SessionService.revoke(
            session,
            principal=principal_a,
            target_session_id=session_a.id,
            reason="user_revoked",
            ip_hash="3" * 64,
            user_agent="contract-test",
        )
        _, replay_changed = SessionService.revoke(
            session,
            principal=principal_a,
            target_session_id=session_a.id,
            reason="user_revoked",
            ip_hash="3" * 64,
            user_agent="contract-test",
        )
        assert changed is True
        assert replay_changed is False
        assert SessionService.resolve(
            session,
            raw_token=token_a,
            pepper=pepper,
            idle_ttl_seconds=3600,
        ) is None
        assert SessionService.resolve(
            session,
            raw_token=token_b,
            pepper=pepper,
            idle_ttl_seconds=3600,
        ) is not None


def test_invitation_idempotency_cross_tenant_binding_and_replay(
    auth_session_factory: sessionmaker[Session],
) -> None:
    pepper = "invitation-contract-pepper"
    with auth_session_factory.begin() as session:
        company_a, owner_a, _ = CompanyService.bootstrap_company(
            session,
            company_name="Invitation Tenant A",
            owner_email="owner-a@example.com",
            owner_display_name="Owner A",
        )
        company_b, owner_b, _ = CompanyService.bootstrap_company(
            session,
            company_name="Invitation Tenant B",
            owner_email="owner-b@example.com",
            owner_display_name="Owner B",
        )
        invitation, token, created = InvitationService.create(
            session,
            company_id=company_a.id,
            actor_user_id=owner_a.id,
            email="Invited.User@example.com",
            display_name="Invited User",
            primary_role="operator",
            idempotency_key="invite-contract-001",
            expires_in_seconds=3600,
            pepper=pepper,
            request_id="invite-create",
        )
        assert created is True and token is not None

        replay, replay_token, replay_created = InvitationService.create(
            session,
            company_id=company_a.id,
            actor_user_id=owner_a.id,
            email="invited.user@example.com",
            display_name="Invited User",
            primary_role="operator",
            idempotency_key="invite-contract-001",
            expires_in_seconds=3600,
            pepper=pepper,
            request_id="invite-create-replay",
        )
        assert replay.id == invitation.id
        assert replay_token is None
        assert replay_created is False
        with pytest.raises(ConflictError) as mismatch:
            InvitationService.create(
                session,
                company_id=company_a.id,
                actor_user_id=owner_a.id,
                email="other@example.com",
                display_name="Other User",
                primary_role="operator",
                idempotency_key="invite-contract-001",
                expires_in_seconds=3600,
                pepper=pepper,
                request_id="invite-create-mismatch",
            )
        assert mismatch.value.code == "conflict"

        with pytest.raises(NotFoundError) as cross_reissue:
            InvitationService.reissue(
                session,
                company_id=company_b.id,
                invitation_id=invitation.id,
                actor_user_id=owner_b.id,
                expires_in_seconds=3600,
                pepper=pepper,
                request_id="cross-tenant-reissue",
            )
        assert cross_reissue.value.code == "not_found"
        with pytest.raises(NotFoundError) as cross_revoke:
            InvitationService.revoke(
                session,
                company_id=company_b.id,
                invitation_id=invitation.id,
                actor_user_id=owner_b.id,
                request_id="cross-tenant-revoke",
            )
        assert cross_revoke.value.code == "not_found"

        owner_b.email_verified_at = utcnow()
        with pytest.raises(DomainError) as wrong_account:
            InvitationService.accept(
                session,
                token=token,
                pepper=pepper,
                user=owner_b,
                actor_session_id=None,
                request_id="wrong-account-accept",
                ip_hash="4" * 64,
                user_agent="contract-test",
            )
        assert wrong_account.value.code == "invitation_email_mismatch"
        assert invitation.status == CompanyInvitationStatus.PENDING

        invited = session.scalar(
            select(User).where(func.lower(User.email) == "invited.user@example.com")
        )
        assert invited is not None
        invited.status = UserStatus.ACTIVE
        invited.email_verified_at = utcnow()
        membership = InvitationService.accept(
            session,
            token=token,
            pepper=pepper,
            user=invited,
            actor_session_id=None,
            request_id="invitation-accept",
            ip_hash="4" * 64,
            user_agent="contract-test",
        )
        assert membership.company_id == company_a.id
        assert invitation.status == CompanyInvitationStatus.ACCEPTED
        with pytest.raises(DomainError) as accepted_replay:
            InvitationService.accept(
                session,
                token=token,
                pepper=pepper,
                user=invited,
                actor_session_id=None,
                request_id="invitation-accept-replay",
                ip_hash="4" * 64,
                user_agent="contract-test",
            )
        assert accepted_replay.value.code == "invitation_used"
        assert session.scalar(
            select(func.count(CompanyMembership.id)).where(
                CompanyMembership.user_id == invited.id,
                CompanyMembership.company_id == company_a.id,
            )
        ) == 1
        assert session.scalar(
            select(func.count(CompanyMembership.id)).where(
                CompanyMembership.user_id == invited.id,
                CompanyMembership.company_id == company_b.id,
            )
        ) == 0


def test_csrf_double_submit_contract_rejects_every_partial_proof(
    auth_session_factory: sessionmaker[Session],
) -> None:
    pepper = "csrf-contract-pepper"
    origin = "https://frontend.example.test"
    with auth_session_factory.begin() as session:
        user, identity = _identity(
            session, subject="csrf-user", email="csrf-user@example.com"
        )
        _, raw_token, raw_csrf = _new_session(
            session, user=user, identity=identity, pepper=pepper
        )
        principal = SessionService.resolve(
            session,
            raw_token=raw_token,
            pepper=pepper,
            idle_ttl_seconds=3600,
        )
        assert principal is not None

        invalid = (
            (None, raw_csrf, raw_csrf),
            ("https://evil.example.test", raw_csrf, raw_csrf),
            (origin, None, raw_csrf),
            (origin, raw_csrf, None),
            (origin, raw_csrf, "different"),
            (origin, "different", "different"),
        )
        for supplied_origin, cookie, header in invalid:
            with pytest.raises(Exception) as rejected:
                SessionService.require_csrf(
                    principal,
                    csrf_cookie=cookie,
                    csrf_header=header,
                    origin=supplied_origin,
                    expected_origin=origin,
                    pepper=pepper,
                )
            assert getattr(rejected.value, "code", "").startswith("csrf_")

        SessionService.require_csrf(
            principal,
            csrf_cookie=raw_csrf,
            csrf_header=raw_csrf,
            origin=origin,
            expected_origin=origin,
            pepper=pepper,
        )


def _cookie_header(headers, name: str) -> str:
    matches = [
        value
        for value in headers.get_list("set-cookie")
        if value.startswith(name + "=")
    ]
    assert len(matches) == 1
    return matches[0]


def test_host_cookie_attributes_and_logout_clearing_contract() -> None:
    app, engine, provider = _auth_app()
    try:
        with TestClient(app, base_url="https://testserver") as browser:
            started = browser.get("/api/v1/auth/login", follow_redirects=False)
            state_cookie = _cookie_header(started.headers, OIDC_STATE_COOKIE_NAME)
            assert "Secure" in state_cookie
            assert "HttpOnly" in state_cookie
            assert "SameSite=lax" in state_cookie
            assert "Path=/" in state_cookie
            assert "Domain=" not in state_cookie

            from urllib.parse import parse_qs, urlsplit

            query = parse_qs(urlsplit(started.headers["location"]).query)
            provider["nonce"] = query["nonce"][0]
            completed = browser.get(
                "/api/v1/auth/callback"
                f"?state={query['state'][0]}&code=provider-code",
                follow_redirects=False,
            )
            assert completed.status_code == 303
            session_cookie = _cookie_header(completed.headers, SESSION_COOKIE_NAME)
            csrf_cookie = _cookie_header(completed.headers, CSRF_COOKIE_NAME)
            for value in (session_cookie, csrf_cookie):
                assert "Secure" in value
                assert "SameSite=lax" in value
                assert "Path=/" in value
                assert "Domain=" not in value
            assert "HttpOnly" in session_cookie
            assert "HttpOnly" not in csrf_cookie

            state = browser.get("/api/v1/auth/session").json()
            logout = browser.post(
                "/api/v1/auth/logout",
                headers={
                    "Origin": "https://frontend.example.test",
                    CSRF_HEADER_NAME: state["csrf_token"],
                },
            )
            assert logout.status_code == 204
            for name in (SESSION_COOKIE_NAME, CSRF_COOKIE_NAME):
                cleared = _cookie_header(logout.headers, name)
                assert "Max-Age=0" in cleared
                assert "Secure" in cleared
                assert "Path=/" in cleared
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


@pytest.mark.parametrize(
    "state_error",
    ["missing_query", "wrong_browser_cookie", "unknown_server_state"],
)
def test_oidc_callback_state_errors_fail_before_provider_network(
    state_error: str,
) -> None:
    app, engine, provider = _auth_app()
    try:
        with TestClient(app, base_url="https://testserver") as browser:
            query, callback = _login(browser, provider)
            if state_error == "missing_query":
                rejected = browser.get(
                    "/api/v1/auth/callback?code=provider-code",
                    follow_redirects=False,
                )
                assert rejected.status_code == 422
            elif state_error == "wrong_browser_cookie":
                rejected = browser.get(
                    callback,
                    headers={
                        "Cookie": f"{OIDC_STATE_COOKIE_NAME}=wrong-browser-state"
                    },
                    follow_redirects=False,
                )
                assert rejected.status_code == 400
                assert rejected.json()["code"] == "oidc_callback_invalid"
            else:
                fabricated = "fabricated-state-that-is-not-server-side"
                rejected = browser.get(
                    f"/api/v1/auth/callback?state={fabricated}&code=provider-code",
                    headers={"Cookie": f"{OIDC_STATE_COOKIE_NAME}={fabricated}"},
                    follow_redirects=False,
                )
                assert rejected.status_code == 400
                assert rejected.json()["code"] == "oidc_callback_invalid"
            assert query["state"][0]
            assert provider["token_calls"] == 0
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


@pytest.mark.parametrize(
    "provider_result",
    [
        "missing_code",
        "access_denied",
        "access_denied_with_code",
    ],
)
def test_oidc_provider_callback_errors_consume_state_without_token_exchange(
    provider_result: str,
) -> None:
    app, engine, provider = _auth_app()
    try:
        with TestClient(app, base_url="https://testserver") as browser:
            query, success_callback = _login(browser, provider)
            state = query["state"][0]
            if provider_result == "missing_code":
                callback = f"/api/v1/auth/callback?state={state}"
            elif provider_result == "access_denied":
                callback = (
                    f"/api/v1/auth/callback?state={state}&error=access_denied"
                )
            else:
                callback = (
                    f"/api/v1/auth/callback?state={state}"
                    "&error=access_denied&code=must-not-be-exchanged"
                )
            failed = browser.get(callback, follow_redirects=False)
            assert failed.status_code == 401
            assert failed.json()["code"] == "oidc_login_failed"
            assert provider["token_calls"] == 0

            replay = browser.get(success_callback, follow_redirects=False)
            assert replay.status_code == 400
            assert replay.json()["code"] == "oidc_callback_invalid"
            assert provider["token_calls"] == 0
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("provider_failure", "expected_status", "expected_code", "jwks_calls"),
    [
        ("token_unavailable", 502, "oidc_provider_unavailable", 0),
        ("token_wrong_content_type", 502, "oidc_provider_invalid", 0),
        ("token_missing_id_token", 502, "oidc_provider_invalid", 0),
        ("malformed_id_token", 401, "oidc_token_invalid", 1),
    ],
)
def test_oidc_token_exchange_error_matrix_is_one_shot_and_secret_free(
    provider_failure: str,
    expected_status: int,
    expected_code: str,
    jwks_calls: int,
) -> None:
    app, engine, provider = _auth_app()
    calls = {"token": 0, "jwks": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            calls["token"] += 1
            if provider_failure == "token_unavailable":
                return httpx.Response(503, json={"error": "temporarily_unavailable"})
            if provider_failure == "token_wrong_content_type":
                return httpx.Response(
                    200,
                    headers={"content-type": "text/html"},
                    content=b"<html>not-json</html>",
                )
            if provider_failure == "token_missing_id_token":
                return httpx.Response(200, json={"access_token": "must-not-leak"})
            return httpx.Response(
                200,
                json={"id_token": "malformed.secret.token"},
            )
        if request.url.path == "/jwks":
            calls["jwks"] += 1
            return httpx.Response(
                200,
                headers={"content-type": "application/jwk-set+json"},
                json={"keys": []},
            )
        raise AssertionError(f"unexpected provider request: {request.url}")

    try:
        app.state.oidc_http_client.close()
        app.state.oidc_http_client = httpx.Client(
            transport=httpx.MockTransport(handler)
        )
        with TestClient(app, base_url="https://testserver") as browser:
            _, callback = _login(browser, provider)
            failed = browser.get(callback, follow_redirects=False)
            assert failed.status_code == expected_status
            assert failed.json()["code"] == expected_code
            assert "must-not-leak" not in failed.text
            assert "malformed.secret.token" not in failed.text
            assert calls == {"token": 1, "jwks": jwks_calls}

            replay = browser.get(callback, follow_redirects=False)
            assert replay.status_code == 400
            assert replay.json()["code"] == "oidc_callback_invalid"
            assert calls == {"token": 1, "jwks": jwks_calls}
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


def test_owner_transfer_requires_csrf_and_recent_authentication() -> None:
    app, engine, provider = _auth_app()
    try:
        with TestClient(app, base_url="https://testserver") as browser:
            _, callback = _login(browser, provider)
            assert browser.get(callback, follow_redirects=False).status_code == 303
            auth_state = browser.get("/api/v1/auth/session").json()
            csrf = auth_state["csrf_token"]
            with app.state.session_factory.begin() as session:
                company, _, owner_membership = CompanyService.bootstrap_company(
                    session,
                    company_name="Recent Auth Transfer",
                    owner_email="owner@example.com",
                    owner_display_name="Current Owner",
                )
                target_user, target_membership, _ = CompanyService.add_member(
                    session,
                    company_id=company.id,
                    email="next-owner-recent@example.com",
                    display_name="Next Owner",
                )
                operator = AccessLifecycleService.system_role(
                    session, company_id=company.id, system_key="operator"
                )
                AccessLifecycleService.assign_role(
                    session,
                    company_id=company.id,
                    membership_id=target_membership.id,
                    role_id=operator.id,
                    actor_membership_id=owner_membership.id,
                )
                auth_session = session.scalar(select(AuthSession))
                assert auth_session is not None
                auth_session.auth_time = utcnow() - timedelta(seconds=301)
                company_id = company.id
                owner_membership_id = owner_membership.id
                target_membership_id = target_membership.id
                target_user_id = target_user.id

            body = {
                "target_membership_id": target_membership_id,
                "expected_current_owner_membership_id": owner_membership_id,
                "expected_current_owner_user_id": auth_state["user"]["id"],
                "former_owner_primary_role": "operator",
            }
            missing_csrf = browser.post(
                f"/api/v1/companies/{company_id}/owner-transfer",
                json=body,
                headers={"X-Company-ID": company_id},
            )
            assert missing_csrf.status_code == 403
            assert missing_csrf.json()["code"].startswith("csrf_")

            proof = {
                "Origin": "https://frontend.example.test",
                CSRF_HEADER_NAME: csrf,
                "X-Company-ID": company_id,
            }
            stale = browser.post(
                f"/api/v1/companies/{company_id}/owner-transfer",
                json=body,
                headers=proof,
            )
            assert stale.status_code == 403
            assert stale.headers["X-Auth-Required"] == "step-up"

            with app.state.session_factory.begin() as session:
                auth_session = session.scalar(select(AuthSession))
                assert auth_session is not None
                auth_session.auth_time = utcnow()
            transferred = browser.post(
                f"/api/v1/companies/{company_id}/owner-transfer",
                json=body,
                headers=proof,
            )
            assert transferred.status_code == 200, transferred.text
            assert transferred.json()["owner_user_id"] == target_user_id
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


def test_auth_list_endpoints_enforce_bounded_pagination() -> None:
    app, engine, provider = _auth_app()
    try:
        with TestClient(app, base_url="https://testserver") as browser:
            _, callback = _login(browser, provider)
            assert browser.get(callback, follow_redirects=False).status_code == 303
            auth_state = browser.get("/api/v1/auth/session").json()
            with app.state.session_factory.begin() as session:
                company, _, _ = CompanyService.bootstrap_company(
                    session,
                    company_name="Pagination Company",
                    owner_email="owner@example.com",
                    owner_display_name="Owner",
                )
                company_id = company.id
                owner_id = auth_state["user"]["id"]
                for index in range(3):
                    InvitationService.create(
                        session,
                        company_id=company_id,
                        actor_user_id=owner_id,
                        email=f"page-{index}@example.com",
                        display_name=f"Page {index}",
                        primary_role="operator",
                        idempotency_key=f"pagination-{index:03d}",
                        expires_in_seconds=3600,
                        pepper=app.state.settings.jwt_signing_secret,
                        request_id=f"pagination-{index}",
                    )
                for index in range(3):
                    session.add(
                        User(
                            email=f"platform-page-{index}@example.com",
                            display_name=f"Platform Page {index}",
                        )
                    )

            sessions = browser.get("/api/v1/account/sessions?page=1&page_size=1")
            assert sessions.status_code == 200
            assert sessions.json()["page"] == 1
            assert sessions.json()["page_size"] == 1
            assert sessions.json()["total"] == 1
            assert len(sessions.json()["items"]) == 1
            assert browser.get(
                "/api/v1/account/sessions?page=2&page_size=1"
            ).json()["items"] == []

            invitations = browser.get(
                f"/api/v1/companies/{company_id}/invitations?page=2&page_size=1",
                headers={"X-Company-ID": company_id},
            )
            assert invitations.status_code == 200
            assert invitations.json()["total"] == 3
            assert len(invitations.json()["items"]) == 1
            assert browser.get(
                f"/api/v1/companies/{company_id}/invitations?page=4&page_size=1",
                headers={"X-Company-ID": company_id},
            ).json()["items"] == []

            users = browser.get("/api/v1/platform-admin/users?page=2&page_size=2")
            assert users.status_code == 200
            assert users.json()["total"] >= 7
            assert len(users.json()["items"]) == 2

            for path in (
                "/api/v1/account/sessions?page=0",
                "/api/v1/account/sessions?page_size=101",
                "/api/v1/platform-admin/users?page_size=101",
            ):
                assert browser.get(path).status_code == 422
            assert browser.get(
                f"/api/v1/companies/{company_id}/invitations?page_size=0",
                headers={"X-Company-ID": company_id},
            ).status_code == 422
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


def test_global_suspend_rejects_an_active_company_owner(
    auth_session_factory: sessionmaker[Session],
) -> None:
    with auth_session_factory.begin() as session:
        actor, _ = _identity(
            session,
            subject="platform-owner-status-guard",
            email="platform-owner-status-guard@example.com",
            platform_admin=True,
        )
        _, company_owner, _ = CompanyService.bootstrap_company(
            session,
            company_name="Owner Suspension Guard",
            owner_email="active-company-owner@example.com",
            owner_display_name="Active Company Owner",
        )
        with pytest.raises(ConflictError) as blocked:
            AccountService.set_global_status(
                session,
                actor_user_id=actor.id,
                target_user_id=company_owner.id,
                expected_status=UserStatus.ACTIVE,
                expected_auth_version=company_owner.auth_version,
                target_status=UserStatus.SUSPENDED,
                platform_owner_user_ids=set(),
                request_id="active-owner-suspend",
                ip_hash="5" * 64,
                user_agent="contract-test",
            )
        assert blocked.value.code == "conflict"
        assert company_owner.status == UserStatus.ACTIVE
        assert company_owner.auth_version == 1


def test_session_without_auth_time_is_persisted_as_null_not_iat(
    auth_session_factory: sessionmaker[Session],
) -> None:
    pepper = "missing-auth-time-contract-pepper"
    now = time()
    claims = OidcIdTokenClaims(
        issuer="https://identity.example.test",
        subject="no-auth-time",
        email="no-auth-time@example.com",
        display_name="No Auth Time",
        issued_at=now,
        expires_at=now + 300,
        authentication_time=None,
        authentication_methods=("webauthn",),
    )
    with auth_session_factory.begin() as session:
        user, identity = _identity(
            session,
            subject="no-auth-time",
            email="no-auth-time@example.com",
            platform_admin=True,
        )
        auth_session, raw_token, _ = SessionService.create(
            session,
            user=user,
            identity=identity,
            claims=claims,
            ttl_seconds=3600,
            user_agent="contract-test",
            pepper=pepper,
            ip_hash="6" * 64,
        )
        assert auth_session.auth_time is None
        principal = SessionService.resolve(
            session,
            raw_token=raw_token,
            pepper=pepper,
            idle_ttl_seconds=3600,
        )
        assert principal is not None
        assert principal.authentication_time is None


@pytest.mark.parametrize(
    "target_status",
    [UserStatus.SUSPENDED, UserStatus.DEACTIVATED],
)
def test_owner_transfer_rejects_inactive_target_account_without_role_writes(
    auth_session_factory: sessionmaker[Session],
    target_status: UserStatus,
) -> None:
    with auth_session_factory.begin() as session:
        company, current_owner, owner_membership = CompanyService.bootstrap_company(
            session,
            company_name="Inactive Owner Target",
            owner_email="current-owner@example.com",
            owner_display_name="Current Owner",
        )
        target_user, target_membership, _ = CompanyService.add_member(
            session,
            company_id=company.id,
            email=f"{target_status.value}-target@example.com",
            display_name="Inactive Target",
        )
        operator = AccessLifecycleService.system_role(
            session, company_id=company.id, system_key="operator"
        )
        AccessLifecycleService.assign_role(
            session,
            company_id=company.id,
            membership_id=target_membership.id,
            role_id=operator.id,
            actor_membership_id=owner_membership.id,
        )
        membership_ids = (owner_membership.id, target_membership.id)
        before = tuple(
            session.execute(
                select(MembershipRole.membership_id, MembershipRole.role_id)
                .where(MembershipRole.membership_id.in_(membership_ids))
                .order_by(MembershipRole.membership_id, MembershipRole.role_id)
            ).all()
        )
        target_user.status = target_status
        target_user.deactivated_at = (
            utcnow() if target_status == UserStatus.DEACTIVATED else None
        )

        with pytest.raises(ConflictError, match="active account") as blocked:
            OwnerTransferService.transfer(
                session,
                company_id=company.id,
                actor_user_id=current_owner.id,
                actor_membership_id=owner_membership.id,
                target_membership_id=target_membership.id,
                expected_current_owner_membership_id=owner_membership.id,
                expected_current_owner_user_id=current_owner.id,
                former_owner_primary_role="operator",
                request_id=f"inactive-target-{target_status.value}",
            )
        assert blocked.value.code == "conflict"
        after = tuple(
            session.execute(
                select(MembershipRole.membership_id, MembershipRole.role_id)
                .where(MembershipRole.membership_id.in_(membership_ids))
                .order_by(MembershipRole.membership_id, MembershipRole.role_id)
            ).all()
        )
        assert after == before


def test_missing_auth_time_rejects_platform_owner_and_high_risk_account_actions(
) -> None:
    app, engine, provider = _auth_app()
    try:
        with TestClient(app, base_url="https://testserver") as browser:
            _, callback = _login(browser, provider)
            assert browser.get(callback, follow_redirects=False).status_code == 303
            state = browser.get("/api/v1/auth/session").json()
            csrf_headers = {
                "Origin": "https://frontend.example.test",
                CSRF_HEADER_NAME: state["csrf_token"],
            }
            with app.state.session_factory.begin() as session:
                auth_session = session.scalar(select(AuthSession))
                assert auth_session is not None
                auth_session.auth_time = None
            # Switch only the request-time authorization boundary. The app was
            # already constructed with its isolated local provider harness.
            app.state.settings.environment = "production"

            ordinary_read = browser.get("/api/v1/account")
            assert ordinary_read.status_code == 200
            session_list = browser.get("/api/v1/account/sessions")
            assert session_list.status_code == 200
            assert session_list.json()["items"][0]["auth_time"] is None

            owner_read = browser.get("/api/v1/platform-admin/users")
            assert owner_read.status_code == 403
            assert "authentication time" in owner_read.json()["detail"].lower()
            owner_write = browser.patch(
                "/api/v1/platform-admin/users/missing-user/status",
                headers=csrf_headers,
                json={
                    "expected_status": "active",
                    "expected_auth_version": 1,
                    "target_status": "suspended",
                },
            )
            assert owner_write.status_code == 403
            assert "authentication time" in owner_write.json()["detail"].lower()

            revoke_all = browser.post(
                "/api/v1/account/sessions/revoke-all",
                headers=csrf_headers,
            )
            assert revoke_all.status_code == 403
            assert revoke_all.headers["X-Auth-Required"] == "step-up"
            deactivate = browser.post(
                "/api/v1/account/deactivate",
                headers=csrf_headers,
                json={
                    "expected_auth_version": state["user"]["auth_version"],
                    "confirmation": "DEACTIVATE",
                },
            )
            assert deactivate.status_code == 403
            assert deactivate.headers["X-Auth-Required"] == "step-up"
            assert browser.get("/api/v1/account").status_code == 200
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


def test_invitation_acceptance_failures_publish_stable_codes() -> None:
    app, engine, provider = _auth_app()
    try:
        with TestClient(app, base_url="https://testserver") as browser:
            _, callback = _login(browser, provider)
            assert browser.get(callback, follow_redirects=False).status_code == 303
            state = browser.get("/api/v1/auth/session").json()
            pepper = app.state.settings.jwt_signing_secret
            with app.state.session_factory.begin() as session:
                company, actor, _ = CompanyService.bootstrap_company(
                    session,
                    company_name="Stable Invitation Codes",
                    owner_email="invitation-actor@example.com",
                    owner_display_name="Invitation Actor",
                )
                current_user = session.get(User, state["user"]["id"])
                assert current_user is not None

                def create_invitation(
                    *, email: str, key: str
                ) -> tuple[CompanyInvitation, str]:
                    invitation, token, created = InvitationService.create(
                        session,
                        company_id=company.id,
                        actor_user_id=actor.id,
                        email=email,
                        display_name="Stable Invitee",
                        primary_role="operator",
                        idempotency_key=key,
                        expires_in_seconds=3600,
                        pepper=pepper,
                        request_id=key,
                    )
                    assert created is True and token is not None
                    return invitation, token

                _, mismatched_token = create_invitation(
                    email="another-account@example.com",
                    key="stable-email-mismatch",
                )
                revoked, revoked_token = create_invitation(
                    email=current_user.email,
                    key="stable-revoked",
                )
                expired, expired_token = create_invitation(
                    email=current_user.email,
                    key="stable-expired",
                )
                accepted, accepted_token = create_invitation(
                    email=current_user.email,
                    key="stable-accepted",
                )
                InvitationService.revoke(
                    session,
                    company_id=company.id,
                    invitation_id=revoked.id,
                    actor_user_id=actor.id,
                    request_id="stable-revoke",
                )
                expired.expires_at = utcnow() - timedelta(seconds=1)
                InvitationService.accept(
                    session,
                    token=accepted_token,
                    pepper=pepper,
                    user=current_user,
                    actor_session_id=None,
                    request_id="stable-first-accept",
                    ip_hash="7" * 64,
                    user_agent="contract-test",
                )
                assert accepted.status == CompanyInvitationStatus.ACCEPTED

            headers = {
                "Origin": "https://frontend.example.test",
                CSRF_HEADER_NAME: state["csrf_token"],
            }
            preview = browser.post(
                "/api/v1/invitations/preview",
                json={"token": mismatched_token},
            )
            assert preview.status_code == 200
            assert INVITATION_HANDOFF_COOKIE_NAME in preview.headers["set-cookie"]
            response = browser.post(
                "/api/v1/invitations/accept",
                headers=headers,
                json={},
            )
            assert response.status_code == 403
            assert response.json()["code"] == "invitation_email_mismatch"

            terminal_cases = (
                ("x" * 48, 404, "invitation_invalid"),
                (revoked_token, 409, "invitation_revoked"),
                (expired_token, 410, "invitation_expired"),
                (accepted_token, 409, "invitation_used"),
            )
            for token, expected_status, expected_code in terminal_cases:
                response = browser.post(
                    "/api/v1/invitations/preview",
                    json={"token": token},
                )
                assert response.status_code == expected_status
                assert response.json()["code"] == expected_code
                assert INVITATION_HANDOFF_COOKIE_NAME in response.headers["set-cookie"]
                assert "Max-Age=0" in response.headers["set-cookie"]

            raw_token_accept = browser.post(
                "/api/v1/invitations/accept",
                headers=headers,
                json={"token": mismatched_token},
            )
            assert raw_token_accept.status_code == 422
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


def test_platform_owner_cookie_requires_strong_amr_and_recent_write_step_up() -> None:
    app, engine, provider = _auth_app()
    try:
        with TestClient(app, base_url="https://testserver") as browser:
            _, callback = _login(browser, provider)
            assert browser.get(callback, follow_redirects=False).status_code == 303
            state = browser.get("/api/v1/auth/session").json()
            app.state.settings.environment = "production"
            with app.state.session_factory.begin() as session:
                auth_session = session.scalar(select(AuthSession))
                assert auth_session is not None
                auth_session.amr = ["pwd"]

            weak = browser.get("/api/v1/platform-admin/companies")
            assert weak.status_code == 403
            assert "strong authentication" in weak.json()["detail"].lower()

            with app.state.session_factory.begin() as session:
                auth_session = session.scalar(select(AuthSession))
                assert auth_session is not None
                auth_session.amr = ["webauthn"]
                auth_session.auth_time = utcnow() - timedelta(seconds=301)
            historical_read = browser.get("/api/v1/platform-admin/companies")
            assert historical_read.status_code == 200
            stale_write = browser.post(
                "/api/v1/platform-admin/companies",
                headers={
                    "Origin": "https://frontend.example.test",
                    CSRF_HEADER_NAME: state["csrf_token"],
                },
                json={
                    "name": "Must Not Be Created",
                    "owner_email": "blocked-owner@example.com",
                    "owner_display_name": "Blocked Owner",
                },
            )
            assert stale_write.status_code == 403
            assert stale_write.headers["X-Auth-Required"] == "step-up"
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()
