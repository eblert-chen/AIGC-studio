from __future__ import annotations

import base64
import hashlib
import json
from datetime import timedelta
from time import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from platform_api.config import Settings
from platform_api.main import create_app
from platform_api.models import (
    CompanyInvitation,
    CompanyMembership,
    MembershipRole,
    MembershipStatus,
    OidcLoginTransaction,
    Role,
    User,
    UserStatus,
    utcnow,
)
from platform_api.services.access_lifecycle import AccessLifecycleService
from platform_api.services.companies import CompanyService
from platform_api.services.authentication import (
    CSRF_HEADER_NAME,
    INVITATION_HANDOFF_COOKIE_NAME,
    InvitationService,
    OidcService,
)
from platform_api.services.errors import DomainError, NotFoundError


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jwt(
    private_key: rsa.RSAPrivateKey,
    payload: dict,
    *,
    kid: str = "auth-key",
) -> str:
    header = _b64(
        json.dumps(
            {"alg": "RS256", "kid": kid, "typ": "JWT"},
            separators=(",", ":"),
        ).encode()
    )
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{body}.{_b64(signature)}"


def _jwk(private_key: rsa.RSAPrivateKey, *, kid: str = "auth-key") -> dict:
    numbers = private_key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "key_ops": ["verify"],
        "alg": "RS256",
        "kid": kid,
        "n": _b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }


def _auth_app(*, input_asset_filesystem_root: str = "./data/platform-input-assets"):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = "https://identity.example.test"
    frontend = "https://frontend.example.test"
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    settings = Settings(
        database_url="sqlite+pysqlite://",
        auto_create_tables=True,
        jwt_signing_secret="auth-test-pepper-with-at-least-thirty-two-bytes",
        oidc_enabled=True,
        oidc_self_signup_enabled=True,
        oidc_issuer=issuer,
        oidc_authorization_endpoint=f"{issuer}/authorize",
        oidc_token_endpoint=f"{issuer}/token",
        oidc_jwks_uri=f"{issuer}/jwks",
        oidc_client_id="browser-public-client",
        oidc_redirect_uri="https://testserver/api/v1/auth/callback",
        frontend_origin=frontend,
        cors_origins=[frontend],
        platform_owner_user_ids=["owner-subject"],
        input_asset_filesystem_root=input_asset_filesystem_root,
    )
    app = create_app(settings=settings, engine=engine)
    provider: dict[str, str | int] = {"token_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            provider["token_calls"] = int(provider["token_calls"]) + 1
            form = parse_qs(request.content.decode("ascii"), strict_parsing=True)
            provider["code_verifier"] = form["code_verifier"][0]
            now = int(time())
            token = _jwt(
                private_key,
                {
                    "iss": issuer,
                    "sub": "owner-subject",
                    "aud": settings.oidc_client_id,
                    "exp": now + 300,
                    "iat": now,
                    "nonce": provider["nonce"],
                    "email": "owner@example.com",
                    "email_verified": True,
                    "name": "Owner",
                    "amr": ["webauthn"],
                    "auth_time": now,
                },
            )
            return httpx.Response(200, json={"id_token": token})
        if request.url.path == "/jwks":
            return httpx.Response(
                200,
                headers={"content-type": "application/jwk-set+json"},
                content=json.dumps({"keys": [_jwk(private_key)]}).encode(),
            )
        raise AssertionError(f"unexpected provider request: {request.url}")

    app.state.oidc_http_client = httpx.Client(transport=httpx.MockTransport(handler))
    return app, engine, provider


def _login(client: TestClient, provider: dict[str, str | int]):
    started = client.get(
        "/api/v1/auth/login?return_to=%2Fsettings&prompt=step_up",
        follow_redirects=False,
    )
    assert started.status_code == 302
    query = parse_qs(urlsplit(started.headers["location"]).query)
    assert query["prompt"] == ["login"]
    assert query["max_age"] == ["0"]
    assert query["code_challenge_method"] == ["S256"]
    provider["nonce"] = query["nonce"][0]
    callback = f"/api/v1/auth/callback?state={query['state'][0]}&code=provider-code"
    return query, callback


def test_oidc_state_browser_binding_pkce_cookie_session_and_replay():
    app, engine, provider = _auth_app()
    try:
        with TestClient(app, base_url="https://testserver") as browser:
            query, callback = _login(browser, provider)
            with TestClient(app, base_url="https://testserver") as other_browser:
                rejected = other_browser.get(callback, follow_redirects=False)
                assert rejected.status_code == 400
                assert provider["token_calls"] == 0

            completed = browser.get(callback, follow_redirects=False)
            assert completed.status_code == 303
            with app.state.session_factory() as database:
                tombstones = list(
                    database.scalars(select(OidcLoginTransaction)).all()
                )
                assert len(tombstones) == 1
                assert tombstones[0].consumed_at is not None
                assert tombstones[0].nonce == "consumed"
                assert tombstones[0].code_verifier == "consumed"
            assert completed.headers["location"] == "https://frontend.example.test/settings"
            verifier = str(provider["code_verifier"])
            challenge = _b64(hashlib.sha256(verifier.encode("ascii")).digest())
            assert challenge == query["code_challenge"][0]
            assert "__Host-ai_video_session" in browser.cookies
            assert "__Host-ai_video_csrf" in browser.cookies

            state = browser.get("/api/v1/auth/session").json()
            assert state["authenticated"] is True
            assert state["user"]["email"] == "owner@example.com"
            assert state["platform_admin"] is True
            assert state["csrf_token"]
            global_users = browser.get("/api/v1/platform-admin/users")
            assert global_users.status_code == 200
            assert global_users.headers["Cache-Control"] == "no-store"
            assert global_users.headers["Referrer-Policy"] == "no-referrer"

            replay = browser.get(callback, follow_redirects=False)
            assert replay.status_code == 400
            assert provider["token_calls"] == 1
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


@pytest.mark.parametrize("publish_rotated_key", [True, False])
def test_oidc_unknown_kid_refreshes_jwks_once_and_callback_state_stays_one_shot(
    publish_rotated_key: bool,
) -> None:
    app, engine, provider = _auth_app()
    old_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rotated_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    calls = {"token": 0, "jwks": 0}

    def rotating_provider(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            calls["token"] += 1
            now = int(time())
            token = _jwt(
                rotated_key,
                {
                    "iss": app.state.settings.oidc_issuer,
                    "sub": "owner-subject",
                    "aud": app.state.settings.oidc_client_id,
                    "exp": now + 300,
                    "iat": now,
                    "nonce": provider["nonce"],
                    "email": "owner@example.com",
                    "email_verified": True,
                    "name": "Owner",
                    "amr": ["webauthn"],
                    "auth_time": now,
                },
                kid="rotated-key",
            )
            return httpx.Response(200, json={"id_token": token})
        if request.url.path == "/jwks":
            calls["jwks"] += 1
            keys = [_jwk(old_key, kid="old-key")]
            if calls["jwks"] == 2 and publish_rotated_key:
                keys.append(_jwk(rotated_key, kid="rotated-key"))
            return httpx.Response(
                200,
                headers={"content-type": "application/jwk-set+json"},
                content=json.dumps({"keys": keys}).encode(),
            )
        raise AssertionError(f"unexpected provider request: {request.url}")

    try:
        app.state.oidc_http_client.close()
        app.state.oidc_http_client = httpx.Client(
            transport=httpx.MockTransport(rotating_provider)
        )
        with TestClient(app, base_url="https://testserver") as browser:
            _, callback = _login(browser, provider)
            completed = browser.get(callback, follow_redirects=False)

            if publish_rotated_key:
                assert completed.status_code == 303
                assert completed.headers["location"] == (
                    "https://frontend.example.test/settings"
                )
                assert browser.get("/api/v1/auth/session").json()["authenticated"] is True
            else:
                assert completed.status_code == 401
                assert completed.json()["code"] == "oidc_token_invalid"
                assert (
                    browser.get("/api/v1/auth/session").json()["authenticated"]
                    is False
                )
            assert calls == {"token": 1, "jwks": 2}

            replay = browser.get(callback, follow_redirects=False)
            assert replay.status_code == 400
            assert replay.json()["code"] == "oidc_callback_invalid"
            assert calls == {"token": 1, "jwks": 2}
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


def test_oidc_provider_failure_retains_only_scrubbed_tombstone_before_replay():
    app, engine, provider = _auth_app()
    calls = {"token": 0}

    def unavailable_provider(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/token"
        calls["token"] += 1
        return httpx.Response(
            503,
            headers={"content-type": "application/json"},
            json={"error": "temporarily_unavailable"},
        )

    try:
        app.state.oidc_http_client.close()
        app.state.oidc_http_client = httpx.Client(
            transport=httpx.MockTransport(unavailable_provider)
        )
        with TestClient(app, base_url="https://testserver") as browser:
            _, callback = _login(browser, provider)
            failed = browser.get(callback, follow_redirects=False)
            assert failed.status_code == 502
            assert failed.json()["code"] == "oidc_provider_unavailable"
            with app.state.session_factory() as database:
                tombstones = list(
                    database.scalars(select(OidcLoginTransaction)).all()
                )
                assert len(tombstones) == 1
                assert tombstones[0].consumed_at is not None
                assert tombstones[0].nonce == "consumed"
                assert tombstones[0].code_verifier == "consumed"

            replay = browser.get(callback, follow_redirects=False)
            assert replay.status_code == 400
            assert replay.json()["code"] == "oidc_callback_invalid"
            assert calls == {"token": 1}
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


def test_consumed_oidc_failures_enforce_ip_window_before_more_provider_calls():
    app, engine, provider = _auth_app()
    calls = {"token": 0}

    def unavailable_provider(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/token"
        calls["token"] += 1
        return httpx.Response(
            503,
            headers={"content-type": "application/json"},
            json={"error": "temporarily_unavailable"},
        )

    try:
        app.state.settings.oidc_login_ip_max_attempts = 2
        # Prove consumed rows outlive a shorter authorization-transaction TTL
        # and cover the entire configured limiter window.
        app.state.settings.oidc_login_transaction_ttl_seconds = 60
        app.state.settings.oidc_login_ip_window_seconds = 900
        app.state.oidc_http_client.close()
        app.state.oidc_http_client = httpx.Client(
            transport=httpx.MockTransport(unavailable_provider)
        )
        with TestClient(app, base_url="https://testserver") as browser:
            for _ in range(2):
                _, callback = _login(browser, provider)
                failed = browser.get(callback, follow_redirects=False)
                assert failed.status_code == 502
                assert failed.json()["code"] == "oidc_provider_unavailable"

            with app.state.session_factory() as database:
                tombstones = list(
                    database.scalars(
                        select(OidcLoginTransaction).order_by(
                            OidcLoginTransaction.created_at,
                            OidcLoginTransaction.id,
                        )
                    ).all()
                )
                assert len(tombstones) == 2
                assert all(item.consumed_at is not None for item in tombstones)
                assert all(item.nonce == "consumed" for item in tombstones)
                assert all(item.code_verifier == "consumed" for item in tombstones)
                assert all(
                    item.expires_at - item.created_at >= timedelta(seconds=900)
                    for item in tombstones
                )

            blocked = browser.get("/api/v1/auth/login", follow_redirects=False)
            assert blocked.status_code == 429
            assert blocked.json()["code"] == "auth_rate_limited"
            assert calls == {"token": 2}
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


def test_abandoned_oidc_start_survives_short_state_ttl_for_ip_rate_limit():
    app, engine, _ = _auth_app()
    try:
        app.state.settings.oidc_login_ip_max_attempts = 1
        app.state.settings.oidc_login_transaction_ttl_seconds = 60
        app.state.settings.oidc_login_ip_window_seconds = 600
        with TestClient(app, base_url="https://testserver") as browser:
            assert browser.get(
                "/api/v1/auth/login", follow_redirects=False
            ).status_code == 302
            with app.state.session_factory.begin() as database:
                abandoned = database.scalar(select(OidcLoginTransaction))
                assert abandoned is not None
                raw_nonce = abandoned.nonce
                raw_verifier = abandoned.code_verifier
                abandoned.expires_at = utcnow() - timedelta(seconds=1)

            blocked = browser.get("/api/v1/auth/login", follow_redirects=False)
            assert blocked.status_code == 429
            assert blocked.json()["code"] == "auth_rate_limited"
            with app.state.session_factory() as database:
                retained = list(
                    database.scalars(select(OidcLoginTransaction)).all()
                )
                assert len(retained) == 1
                assert retained[0].consumed_at is not None
                assert retained[0].nonce == "consumed"
                assert retained[0].code_verifier == "consumed"
                assert raw_nonce not in {
                    retained[0].nonce,
                    retained[0].code_verifier,
                }
                assert raw_verifier not in {
                    retained[0].nonce,
                    retained[0].code_verifier,
                }
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


def test_start_login_bounded_cleanup_removes_expired_temporary_secrets():
    app, engine, _ = _auth_app()
    try:
        with TestClient(app, base_url="https://testserver") as browser:
            assert browser.get(
                "/api/v1/auth/login", follow_redirects=False
            ).status_code == 302
            with app.state.session_factory.begin() as database:
                expired = database.scalar(select(OidcLoginTransaction))
                assert expired is not None
                expired_id = expired.id
                now = utcnow()
                expired.created_at = now - timedelta(
                    seconds=app.state.settings.oidc_login_ip_window_seconds + 1
                )
                expired.expires_at = now - timedelta(seconds=1)

            assert browser.get(
                "/api/v1/auth/login", follow_redirects=False
            ).status_code == 302
            with app.state.session_factory() as database:
                remaining = list(
                    database.scalars(select(OidcLoginTransaction)).all()
                )
                assert len(remaining) == 1
                assert remaining[0].id != expired_id
                assert remaining[0].nonce
                assert remaining[0].code_verifier
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


def test_consumed_oidc_state_scrubs_nonce_and_verifier_before_provider_work():
    app, engine, _ = _auth_app()
    try:
        with TestClient(app, base_url="https://testserver") as browser:
            started = browser.get("/api/v1/auth/login", follow_redirects=False)
            query = parse_qs(urlsplit(started.headers["location"]).query)
            state = query["state"][0]
            with app.state.session_factory() as database:
                original = database.scalar(select(OidcLoginTransaction))
                assert original is not None
                original_nonce = original.nonce
                original_verifier = original.code_verifier

            try:
                with app.state.session_factory() as database:
                    transaction, exchange = OidcService.consume_transaction(
                        database,
                        settings=app.state.settings,
                        state=state,
                        ip_address="testclient",
                    )
                    database.commit()
                    assert exchange.nonce == original_nonce
                    assert exchange.code_verifier == original_verifier
                    assert transaction.nonce == "consumed"
                    assert transaction.code_verifier == "consumed"
                    raise RuntimeError("simulated process exit before provider call")
            except RuntimeError:
                pass

            with app.state.session_factory() as database:
                persisted = database.scalar(select(OidcLoginTransaction))
                assert persisted is not None
                assert persisted.consumed_at is not None
                assert persisted.nonce == "consumed"
                assert persisted.code_verifier == "consumed"
                assert original_nonce not in {persisted.nonce, persisted.code_verifier}
                assert original_verifier not in {
                    persisted.nonce,
                    persisted.code_verifier,
                }
                with pytest.raises(DomainError) as replay:
                    OidcService.consume_transaction(
                        database,
                        settings=app.state.settings,
                        state=state,
                        ip_address="testclient",
                    )
                assert replay.value.code == "oidc_callback_invalid"
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


@pytest.mark.parametrize("reuse_active_user", [False, True])
def test_owner_onboarding_reissue_can_atomically_correct_owner_email(
    reuse_active_user: bool,
):
    app, engine, _ = _auth_app()
    pepper = app.state.settings.jwt_signing_secret
    try:
        with app.state.session_factory.begin() as database:
            actor = User(
                email="platform-actor@example.com",
                display_name="Platform Actor",
                status=UserStatus.ACTIVE,
            )
            database.add(actor)
            database.flush()
            company, original_user, membership = CompanyService.bootstrap_company(
                database,
                company_name="Owner Correction",
                owner_email="typo-owner@example.com",
                owner_display_name="Typo Owner",
                owner_activation_required=True,
            )
            invitation, old_token, created = InvitationService.create(
                database,
                company_id=company.id,
                actor_user_id=actor.id,
                email=original_user.email,
                display_name=original_user.display_name,
                primary_role="operator",
                idempotency_key=f"owner-bootstrap:{company.id}",
                expires_in_seconds=3600,
                pepper=pepper,
                request_id="owner-bootstrap-create",
                allow_existing_owner_membership=True,
            )
            assert created is True and old_token is not None
            replacement = None
            if reuse_active_user:
                replacement = User(
                    email="correct-owner@example.com",
                    display_name="Existing Correct Owner",
                    status=UserStatus.ACTIVE,
                    email_verified_at=utcnow(),
                )
                database.add(replacement)
                database.flush()

            updated, new_token, rebound = (
                InvitationService.reissue_owner_onboarding(
                    database,
                    company_id=company.id,
                    expected_owner_membership_id=membership.id,
                    expected_owner_user_id=original_user.id,
                    actor_user_id=actor.id,
                    expires_in_seconds=3600,
                    pepper=pepper,
                    request_id="owner-bootstrap-correct",
                    replacement_email="correct-owner@example.com",
                    replacement_display_name="Correct Owner",
                )
            )
            assert updated.id == invitation.id
            assert updated.email == "correct-owner@example.com"
            assert rebound.id == membership.id
            assert rebound.user_id != original_user.id
            if replacement is not None:
                assert rebound.user_id == replacement.id
            assert original_user.email == "typo-owner@example.com"
            assert old_token != new_token
            with pytest.raises(NotFoundError):
                InvitationService.by_token(
                    database,
                    token=old_token,
                    pepper=pepper,
                    for_update=False,
                )
            assert (
                InvitationService.by_token(
                    database,
                    token=new_token,
                    pepper=pepper,
                    for_update=False,
                ).id
                == invitation.id
            )
            owner_assignments = int(
                database.scalar(
                    select(func.count(MembershipRole.membership_id))
                    .join(Role, Role.id == MembershipRole.role_id)
                    .where(
                        MembershipRole.membership_id == membership.id,
                        Role.system_key == "owner",
                    )
                )
                or 0
            )
            assert owner_assignments == 1
            assert membership.status == MembershipStatus.DISABLED
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


def test_invitation_handoff_is_derived_revocable_and_logout_scoped():
    app, engine, provider = _auth_app()
    pepper = app.state.settings.jwt_signing_secret
    try:
        with TestClient(app, base_url="https://testserver") as browser:
            _, callback = _login(browser, provider)
            assert browser.get(callback, follow_redirects=False).status_code == 303
            state = browser.get("/api/v1/auth/session").json()
            with app.state.session_factory.begin() as database:
                company, actor, _ = CompanyService.bootstrap_company(
                    database,
                    company_name="Handoff Company",
                    owner_email="handoff-actor@example.com",
                    owner_display_name="Handoff Actor",
                )
                mismatch, mismatch_token, _ = InvitationService.create(
                    database,
                    company_id=company.id,
                    actor_user_id=actor.id,
                    email="different-account@example.com",
                    display_name="Different Account",
                    primary_role="operator",
                    idempotency_key="handoff-mismatch-001",
                    expires_in_seconds=3600,
                    pepper=pepper,
                    request_id="handoff-mismatch-create",
                )
                matching, matching_token, _ = InvitationService.create(
                    database,
                    company_id=company.id,
                    actor_user_id=actor.id,
                    email=state["user"]["email"],
                    display_name=state["user"]["display_name"],
                    primary_role="operator",
                    idempotency_key="handoff-match-001",
                    expires_in_seconds=3600,
                    pepper=pepper,
                    request_id="handoff-match-create",
                )
                assert mismatch_token is not None and matching_token is not None

            preview = browser.post(
                "/api/v1/invitations/preview", json={"token": mismatch_token}
            )
            assert preview.status_code == 200
            handoff = browser.cookies.get(INVITATION_HANDOFF_COOKIE_NAME)
            assert handoff and mismatch_token not in handoff
            headers = {
                "Origin": "https://frontend.example.test",
                CSRF_HEADER_NAME: state["csrf_token"],
            }
            wrong_user = browser.post(
                "/api/v1/invitations/accept", json={}, headers=headers
            )
            assert wrong_user.status_code == 403
            assert wrong_user.json()["code"] == "invitation_email_mismatch"
            assert browser.cookies.get(INVITATION_HANDOFF_COOKIE_NAME) == handoff

            with app.state.session_factory.begin() as database:
                _, mismatch_reissue_token = InvitationService.reissue(
                    database,
                    company_id=company.id,
                    invitation_id=mismatch.id,
                    actor_user_id=actor.id,
                    expires_in_seconds=3600,
                    pepper=pepper,
                    request_id="handoff-mismatch-reissue",
                )
            stale = browser.post("/api/v1/invitations/preview", json={})
            assert stale.status_code == 404
            assert stale.json()["code"] == "invitation_invalid"
            assert browser.cookies.get(INVITATION_HANDOFF_COOKIE_NAME) is None
            assert mismatch_reissue_token

            matching_preview = browser.post(
                "/api/v1/invitations/preview", json={"token": matching_token}
            )
            assert matching_preview.status_code == 200
            missing_csrf = browser.post("/api/v1/invitations/accept", json={})
            assert missing_csrf.status_code == 403
            assert browser.cookies.get(INVITATION_HANDOFF_COOKIE_NAME)

            logout = browser.post("/api/v1/auth/logout", headers=headers)
            assert logout.status_code == 204
            assert browser.cookies.get(INVITATION_HANDOFF_COOKIE_NAME) is None

            _, callback = _login(browser, provider)
            assert browser.get(callback, follow_redirects=False).status_code == 303
            state = browser.get("/api/v1/auth/session").json()
            assert browser.post(
                "/api/v1/invitations/preview", json={"token": matching_token}
            ).status_code == 200
            preserve = browser.post(
                "/api/v1/auth/logout",
                json={"preserve_invitation": True},
                headers={
                    "Origin": "https://frontend.example.test",
                    CSRF_HEADER_NAME: state["csrf_token"],
                },
            )
            assert preserve.status_code == 204
            assert browser.cookies.get(INVITATION_HANDOFF_COOKIE_NAME)
            assert browser.post(
                "/api/v1/invitations/preview", json={}
            ).status_code == 200

            _, callback = _login(browser, provider)
            assert browser.get(callback, follow_redirects=False).status_code == 303
            state = browser.get("/api/v1/auth/session").json()
            accepted = browser.post(
                "/api/v1/invitations/accept",
                json={},
                headers={
                    "Origin": "https://frontend.example.test",
                    CSRF_HEADER_NAME: state["csrf_token"],
                },
            )
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["status"] == "accepted"
            assert browser.cookies.get(INVITATION_HANDOFF_COOKIE_NAME) is None
            with app.state.session_factory() as database:
                membership = database.scalar(
                    select(CompanyMembership).where(
                        CompanyMembership.company_id == company.id,
                        CompanyMembership.user_id == state["user"]["id"],
                    )
                )
                assert membership is not None
                assert membership.status == MembershipStatus.ACTIVE
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


def test_first_oidc_link_canonicalizes_legacy_email_for_invites_and_owner_reissue():
    app, engine, provider = _auth_app()
    pepper = app.state.settings.jwt_signing_secret
    try:
        with app.state.session_factory.begin() as database:
            normal_company, actor, _ = CompanyService.bootstrap_company(
                database,
                company_name="Canonical Invite Company",
                owner_email="canonical-actor@example.com",
                owner_display_name="Canonical Actor",
            )
            normal_invitation, normal_token, _ = InvitationService.create(
                database,
                company_id=normal_company.id,
                actor_user_id=actor.id,
                email="owner@example.com",
                display_name="Legacy Mixed Case Owner",
                primary_role="operator",
                idempotency_key="canonical-normal-invite",
                expires_in_seconds=3600,
                pepper=pepper,
                request_id="canonical-normal-create",
            )
            legacy_user = database.scalar(
                select(User).where(func.lower(User.email) == "owner@example.com")
            )
            assert legacy_user is not None and normal_token is not None
            owner_company, owner_user, owner_membership = (
                CompanyService.bootstrap_company(
                    database,
                    company_name="Canonical Owner Company",
                    owner_email="owner@example.com",
                    owner_display_name="Legacy Mixed Case Owner",
                    owner_activation_required=True,
                )
            )
            assert owner_user.id == legacy_user.id
            owner_invitation, owner_token, _ = InvitationService.create(
                database,
                company_id=owner_company.id,
                actor_user_id=actor.id,
                email="owner@example.com",
                display_name="Legacy Mixed Case Owner",
                primary_role="operator",
                idempotency_key=f"owner-bootstrap:{owner_company.id}",
                expires_in_seconds=3600,
                pepper=pepper,
                request_id="canonical-owner-create",
                allow_existing_owner_membership=True,
            )
            assert owner_token is not None
            legacy_user.email = "Owner@Example.com"
            legacy_user_id = legacy_user.id

        with TestClient(app, base_url="https://testserver") as browser:
            _, callback = _login(browser, provider)
            assert browser.get(callback, follow_redirects=False).status_code == 303
            state = browser.get("/api/v1/auth/session").json()
            assert state["user"]["id"] == legacy_user_id
            assert state["user"]["email"] == "owner@example.com"
            headers = {
                "Origin": "https://frontend.example.test",
                CSRF_HEADER_NAME: state["csrf_token"],
            }

            reissued = browser.post(
                f"/api/v1/platform-admin/companies/{owner_company.id}"
                "/owner-invitation/reissue",
                json={
                    "expected_owner_membership_id": owner_membership.id,
                    "expected_owner_user_id": legacy_user_id,
                },
                headers=headers,
            )
            assert reissued.status_code == 200, reissued.text
            reissued_token = parse_qs(
                urlsplit(reissued.json()["invitation_url"]).fragment
            )["token"][0]
            assert reissued_token != owner_token

            for token, expected_company_id in (
                (normal_token, normal_company.id),
                (reissued_token, owner_company.id),
            ):
                preview = browser.post(
                    "/api/v1/invitations/preview", json={"token": token}
                )
                assert preview.status_code == 200, preview.text
                accepted = browser.post(
                    "/api/v1/invitations/accept", json={}, headers=headers
                )
                assert accepted.status_code == 200, accepted.text
                assert accepted.json()["company_id"] == expected_company_id

            with app.state.session_factory() as database:
                canonical_user = database.get(User, legacy_user_id)
                assert canonical_user is not None
                assert canonical_user.email == "owner@example.com"
                memberships = list(
                    database.scalars(
                        select(CompanyMembership).where(
                            CompanyMembership.user_id == legacy_user_id,
                            CompanyMembership.company_id.in_(
                                [normal_company.id, owner_company.id]
                            ),
                        )
                    ).all()
                )
                assert len(memberships) == 2
                assert all(
                    item.status == MembershipStatus.ACTIVE for item in memberships
                )
                stored_invitations = list(
                    database.scalars(
                        select(CompanyInvitation).where(
                            CompanyInvitation.id.in_(
                                [normal_invitation.id, owner_invitation.id]
                            )
                        )
                    ).all()
                )
                assert len(stored_invitations) == 2
                assert all(
                    item.status.value == "accepted" for item in stored_invitations
                )
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


def test_cookie_csrf_is_shared_by_personal_tenant_and_platform_admin_writes():
    app, engine, provider = _auth_app()
    try:
        with TestClient(app, base_url="https://testserver") as browser:
            _, callback = _login(browser, provider)
            assert browser.get(callback, follow_redirects=False).status_code == 303
            session_state = browser.get("/api/v1/auth/session").json()
            csrf = session_state["csrf_token"]
            origin_headers = {
                "Origin": "https://frontend.example.test",
                "X-CSRF-Token": csrf,
            }

            personal_body = {
                "model_id": "missing-model",
                "idempotency_key": "personal-csrf-test",
                "request_payload": {},
            }
            missing = browser.post("/api/v1/personal/tasks", json=personal_body)
            assert missing.status_code == 403
            assert missing.json()["code"] == "csrf_origin_invalid"
            wrong = browser.post(
                "/api/v1/personal/tasks",
                json=personal_body,
                headers={**origin_headers, "Origin": "https://evil.example.test"},
            )
            assert wrong.status_code == 403
            accepted_boundary = browser.post(
                "/api/v1/personal/tasks", json=personal_body, headers=origin_headers
            )
            assert accepted_boundary.status_code == 404

            with app.state.session_factory() as database:
                company, _, owner_membership = CompanyService.bootstrap_company(
                    database,
                    company_name="CSRF Company",
                    owner_email="owner@example.com",
                    owner_display_name="Owner",
                )
                company_id = company.id
                owner_membership_id = owner_membership.id
                target_user, target_membership, _ = CompanyService.add_member(
                    database,
                    company_id=company_id,
                    email="next-owner@example.com",
                    display_name="Next Owner",
                )
                operator = AccessLifecycleService.system_role(
                    database, company_id=company_id, system_key="operator"
                )
                AccessLifecycleService.assign_role(
                    database,
                    company_id=company_id,
                    membership_id=target_membership.id,
                    role_id=operator.id,
                    actor_membership_id=owner_membership.id,
                )
                target_membership_id = target_membership.id
                target_user_id = target_user.id
                database.commit()
            invitation_body = {
                "email": "new-member@example.com",
                "display_name": "New Member",
                "primary_role": "operator",
                "idempotency_key": "tenant-csrf-test",
            }
            tenant_headers = {**origin_headers, "X-Company-ID": company_id}
            tenant_missing = browser.post(
                f"/api/v1/companies/{company_id}/invitations",
                json=invitation_body,
                headers={"X-Company-ID": company_id},
            )
            assert tenant_missing.status_code == 403
            tenant_ok = browser.post(
                f"/api/v1/companies/{company_id}/invitations",
                json=invitation_body,
                headers=tenant_headers,
            )
            assert tenant_ok.status_code == 201, tenant_ok.text
            assert "#token=" in tenant_ok.json()["invitation_url"]
            assert "acceptance_token" not in tenant_ok.json()

            transfer = browser.post(
                f"/api/v1/companies/{company_id}/owner-transfer",
                json={
                    "target_membership_id": target_membership_id,
                    "expected_current_owner_membership_id": owner_membership_id,
                    "expected_current_owner_user_id": session_state["user"]["id"],
                    "former_owner_primary_role": "team_lead",
                },
                headers=tenant_headers,
            )
            assert transfer.status_code == 200, transfer.text
            assert transfer.json()["owner_user_id"] == target_user_id
            stale_transfer = browser.post(
                f"/api/v1/companies/{company_id}/owner-transfer",
                json={
                    "target_membership_id": target_membership_id,
                    "expected_current_owner_membership_id": owner_membership_id,
                    "expected_current_owner_user_id": session_state["user"]["id"],
                    "former_owner_primary_role": "operator",
                },
                headers=tenant_headers,
            )
            assert stale_transfer.status_code in {403, 409}

            admin_missing = browser.patch(
                "/api/v1/platform-admin/users/missing-user/status",
                json={
                    "expected_status": "active",
                    "expected_auth_version": 1,
                    "target_status": "suspended",
                },
            )
            assert admin_missing.status_code == 403
            admin_boundary = browser.patch(
                "/api/v1/platform-admin/users/missing-user/status",
                json={
                    "expected_status": "active",
                    "expected_auth_version": 1,
                    "target_status": "suspended",
                },
                headers=origin_headers,
            )
            assert admin_boundary.status_code == 404
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()
