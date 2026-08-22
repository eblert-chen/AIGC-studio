from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import json
import re
import secrets
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..auth import (
    JwtAuthenticationError,
    OidcIdTokenClaims,
    OidcUnknownKeyId,
    normalize_email_address,
    strict_json_object,
    verify_oidc_id_token,
)
from ..models import (
    AccountSecurityEvent,
    AuditOutcome,
    AuthSession,
    Company,
    CompanyInvitation,
    CompanyInvitationStatus,
    CompanyMembership,
    CompanyStatus,
    ExternalIdentity,
    MembershipRole,
    MembershipStatus,
    OidcLoginTransaction,
    Role,
    User,
    UserStatus,
    utcnow,
)
from ..platform_owner_identity import is_platform_owner_identity
from .access_lifecycle import AccessLifecycleService
from .audit import AuditService
from .errors import ConflictError, DomainError, NotFoundError, PermissionDeniedError
from .personal import PersonalWorkspaceService


SESSION_COOKIE_NAME = "__Host-ai_video_session"
CSRF_COOKIE_NAME = "__Host-ai_video_csrf"
OIDC_STATE_COOKIE_NAME = "__Host-ai_video_oidc_state"
INVITATION_HANDOFF_COOKIE_NAME = "__Secure-ai_video_invitation"
INVITATION_HANDOFF_TTL_SECONDS = 1800
CSRF_HEADER_NAME = "X-CSRF-Token"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _digest(value: str, *, pepper: str | None) -> str:
    raw = value.encode("utf-8")
    if pepper:
        return hmac.new(pepper.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return hashlib.sha256(raw).hexdigest()


def _ip_hash(ip_address: str, *, pepper: str | None) -> str:
    return _digest("ip:" + ip_address.strip(), pepper=pepper)


def request_ip_hash(ip_address: str, *, pepper: str | None) -> str:
    return _ip_hash(ip_address, pepper=pepper)


def _acquire_oidc_ip_rate_limit_lock(session: Session, *, ip_hash: str) -> None:
    """Serialize one IP's OIDC admission decision in production PostgreSQL."""

    if session.get_bind().dialect.name != "postgresql":
        return
    lock_material = bytes.fromhex(ip_hash[:16])
    first_key = int.from_bytes(lock_material[:4], "big", signed=True)
    second_key = int.from_bytes(lock_material[4:], "big", signed=True)
    session.execute(select(func.pg_advisory_xact_lock(first_key, second_key)))


def _request_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _safe_return_to(value: str | None) -> str:
    candidate = value or "/"
    if (
        not candidate.startswith("/")
        or candidate.startswith("//")
        or "\\" in candidate
        or len(candidate) > 2048
        or any(ord(character) < 32 or ord(character) == 127 for character in candidate)
    ):
        raise DomainError("Unable to start sign-in", "oidc_login_invalid", 400)
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise DomainError("Unable to start sign-in", "oidc_login_invalid", 400)
    return candidate


def _security_details(**values: Any) -> dict[str, Any]:
    """Construct a deliberately small, token-free security-event payload."""

    allowed = {
        "reason",
        "invitation_id",
        "company_id",
        "target_session_id",
        "revoked_count",
        "previous_auth_version",
        "new_auth_version",
    }
    result: dict[str, Any] = {}
    for key, value in values.items():
        if key not in allowed or value is None:
            continue
        if isinstance(value, (str, int, bool)):
            result[key] = value[:256] if isinstance(value, str) else value
    return result


def append_security_event(
    session: Session,
    *,
    event_type: str,
    user_id: str | None,
    outcome: AuditOutcome = AuditOutcome.SUCCEEDED,
    auth_session_id: str | None = None,
    request_id: str = "system",
    issuer: str | None = None,
    subject_hash: str | None = None,
    ip_hash: str | None = None,
    user_agent: str = "",
    **details: Any,
) -> AccountSecurityEvent:
    event = AccountSecurityEvent(
        user_id=user_id,
        event_type=event_type,
        outcome=outcome,
        session_id=auth_session_id,
        request_id=request_id[:128],
        issuer=issuer[:512] if issuer else None,
        subject_hash=subject_hash,
        ip_hash=ip_hash,
        user_agent=user_agent.strip()[:512],
        details=_security_details(**details),
    )
    session.add(event)
    session.flush()
    return event


@dataclass(frozen=True)
class CookieSessionPrincipal:
    user: User
    auth_session: AuthSession
    identity: ExternalIdentity

    @property
    def user_id(self) -> str:
        return self.user.id

    @property
    def issuer(self) -> str:
        return self.identity.issuer

    @property
    def subject(self) -> str:
        return self.identity.subject

    @property
    def authentication_time(self) -> float | None:
        return (
            _as_utc(self.auth_session.auth_time).timestamp()
            if self.auth_session.auth_time is not None
            else None
        )

    @property
    def authentication_methods(self) -> tuple[str, ...]:
        return tuple(self.auth_session.amr)


@dataclass(frozen=True)
class OidcExchangeContext:
    transaction_id: str
    nonce: str
    code_verifier: str


class SessionService:
    @staticmethod
    def resolve(
        session: Session,
        *,
        raw_token: str | None,
        pepper: str | None,
        idle_ttl_seconds: int,
        touch: bool = True,
        now: datetime | None = None,
    ) -> CookieSessionPrincipal | None:
        if not raw_token or len(raw_token) > 512:
            return None
        current = now or utcnow()
        auth_session = session.scalar(
            select(AuthSession).where(
                AuthSession.token_digest == _digest(raw_token, pepper=pepper)
            )
        )
        if auth_session is None or auth_session.revoked_at is not None:
            return None
        user = session.get(User, auth_session.user_id)
        identity = (
            session.get(ExternalIdentity, auth_session.external_identity_id)
            if auth_session.external_identity_id
            else None
        )
        invalid = (
            user is None
            or identity is None
            or identity.user_id != auth_session.user_id
            or user.status != UserStatus.ACTIVE
            or auth_session.auth_version != user.auth_version
            or _as_utc(auth_session.expires_at) <= current
            or _as_utc(auth_session.last_seen_at)
            + timedelta(seconds=idle_ttl_seconds)
            <= current
        )
        if invalid:
            auth_session.revoked_at = auth_session.revoked_at or current
            auth_session.revoked_reason = auth_session.revoked_reason or "invalidated"
            session.flush()
            return None
        if touch and _as_utc(auth_session.last_seen_at) <= current - timedelta(seconds=60):
            auth_session.last_seen_at = current
            session.flush()
        return CookieSessionPrincipal(
            user=user, auth_session=auth_session, identity=identity
        )

    @staticmethod
    def create(
        session: Session,
        *,
        user: User,
        identity: ExternalIdentity,
        claims: OidcIdTokenClaims,
        ttl_seconds: int,
        user_agent: str,
        pepper: str | None,
        ip_hash: str | None,
        request_id: str = "system",
    ) -> tuple[AuthSession, str, str]:
        raw_token = secrets.token_urlsafe(48)
        raw_csrf = secrets.token_urlsafe(32)
        now = utcnow()
        authentication_time = (
            datetime.fromtimestamp(claims.authentication_time, timezone.utc)
            if claims.authentication_time is not None
            else None
        )
        auth_session = AuthSession(
            token_digest=_digest(raw_token, pepper=pepper),
            csrf_digest=_digest(raw_csrf, pepper=pepper),
            user_id=user.id,
            external_identity_id=identity.id,
            auth_version=user.auth_version,
            amr=list(claims.authentication_methods),
            auth_time=authentication_time,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            user_agent=user_agent.strip()[:512],
        )
        session.add(auth_session)
        session.flush()
        append_security_event(
            session,
            event_type="auth.session.created",
            user_id=user.id,
            auth_session_id=auth_session.id,
            ip_hash=ip_hash,
            user_agent=user_agent,
            issuer=identity.issuer,
            request_id=request_id,
        )
        return auth_session, raw_token, raw_csrf

    @staticmethod
    def rotate_csrf(
        session: Session,
        *,
        principal: CookieSessionPrincipal,
        pepper: str | None,
    ) -> str:
        raw_csrf = secrets.token_urlsafe(32)
        principal.auth_session.csrf_digest = _digest(raw_csrf, pepper=pepper)
        session.flush()
        return raw_csrf

    @staticmethod
    def csrf_matches(
        principal: CookieSessionPrincipal,
        *,
        raw_csrf: str | None,
        pepper: str | None,
    ) -> bool:
        return bool(
            raw_csrf
            and len(raw_csrf) <= 512
            and hmac.compare_digest(
                _digest(raw_csrf, pepper=pepper),
                principal.auth_session.csrf_digest,
            )
        )

    @staticmethod
    def require_csrf(
        principal: CookieSessionPrincipal,
        *,
        csrf_cookie: str | None,
        csrf_header: str | None,
        origin: str | None,
        expected_origin: str | None,
        pepper: str | None,
    ) -> None:
        if not expected_origin or origin != expected_origin:
            raise DomainError("Request origin is not allowed", "csrf_origin_invalid", 403)
        if (
            not csrf_cookie
            or not csrf_header
            or len(csrf_cookie) > 512
            or not hmac.compare_digest(csrf_cookie, csrf_header)
            or not hmac.compare_digest(
                _digest(csrf_header, pepper=pepper),
                principal.auth_session.csrf_digest,
            )
        ):
            raise DomainError("CSRF validation failed", "csrf_invalid", 403)

    @staticmethod
    def revoke(
        session: Session,
        *,
        principal: CookieSessionPrincipal,
        target_session_id: str,
        reason: str,
        ip_hash: str | None,
        user_agent: str,
        request_id: str = "system",
    ) -> tuple[AuthSession, bool]:
        target = session.scalar(
            select(AuthSession)
            .where(
                AuthSession.id == target_session_id,
                AuthSession.user_id == principal.user_id,
            )
            .with_for_update()
        )
        if target is None:
            raise NotFoundError("Session does not exist")
        changed = target.revoked_at is None
        if changed:
            target.revoked_at = utcnow()
            target.revoked_reason = reason[:120]
            session.flush()
            append_security_event(
                session,
                event_type="auth.session.revoked",
                user_id=principal.user_id,
                auth_session_id=principal.auth_session.id,
                ip_hash=ip_hash,
                user_agent=user_agent,
                target_session_id=target.id,
                reason=reason,
                request_id=request_id,
            )
        return target, changed

    @staticmethod
    def revoke_all(
        session: Session,
        *,
        user_id: str,
        actor_session_id: str | None,
        reason: str,
        ip_hash: str | None,
        user_agent: str,
        request_id: str = "system",
    ) -> int:
        rows = list(
            session.scalars(
                select(AuthSession)
                .where(AuthSession.user_id == user_id)
                .order_by(AuthSession.id)
                .with_for_update()
            ).all()
        )
        now = utcnow()
        changed = 0
        for row in rows:
            if row.revoked_at is None:
                row.revoked_at = now
                row.revoked_reason = reason[:120]
                changed += 1
        session.flush()
        append_security_event(
            session,
            event_type="auth.session.revoke_all",
            user_id=user_id,
            auth_session_id=actor_session_id,
            ip_hash=ip_hash,
            user_agent=user_agent,
            revoked_count=changed,
            reason=reason,
            request_id=request_id,
        )
        return changed


class OidcService:
    @staticmethod
    def start_login(
        session: Session,
        *,
        settings: Any,
        return_to: str | None,
        prompt: str | None,
        ip_address: str,
    ) -> tuple[str, str]:
        if not settings.oidc_enabled:
            raise DomainError("Sign-in is unavailable", "oidc_unavailable", 503)
        normalized_return_to = _safe_return_to(return_to)
        if prompt is not None and prompt not in {
            "login",
            "select_account",
            "consent",
            "none",
            "step_up",
        }:
            raise DomainError("Unable to start sign-in", "oidc_login_invalid", 400)
        pepper = settings.jwt_signing_secret
        hashed_ip = _ip_hash(ip_address, pepper=pepper)
        _acquire_oidc_ip_rate_limit_lock(session, ip_hash=hashed_ip)
        now = utcnow()
        rate_limit_cutoff = now - timedelta(
            seconds=settings.oidc_login_ip_window_seconds
        )
        expired_sensitive_rows = list(
            session.scalars(
                select(OidcLoginTransaction)
                .where(
                    OidcLoginTransaction.expires_at <= now,
                    OidcLoginTransaction.created_at >= rate_limit_cutoff,
                    OidcLoginTransaction.consumed_at.is_(None),
                )
                .order_by(OidcLoginTransaction.expires_at, OidcLoginTransaction.id)
                .limit(100)
                .with_for_update()
            ).all()
        )
        for expired in expired_sensitive_rows:
            # The authorization state is no longer usable, but the row still
            # participates in the IP window. Remove its raw nonce and PKCE
            # verifier before retaining only the fixed counting tombstone.
            expired.consumed_at = now
            expired.nonce = "consumed"
            expired.code_verifier = "consumed"
        expired_ids = list(
            session.scalars(
                select(OidcLoginTransaction.id)
                .where(
                    OidcLoginTransaction.expires_at <= now,
                    OidcLoginTransaction.created_at < rate_limit_cutoff,
                )
                .order_by(OidcLoginTransaction.expires_at, OidcLoginTransaction.id)
                .limit(100)
            ).all()
        )
        if expired_ids:
            session.execute(
                delete(OidcLoginTransaction).where(
                    OidcLoginTransaction.id.in_(expired_ids)
                )
            )
        recent = session.scalar(
            select(func.count(OidcLoginTransaction.id)).where(
                OidcLoginTransaction.ip_hash == hashed_ip,
                OidcLoginTransaction.created_at >= rate_limit_cutoff,
            )
        ) or 0
        if recent >= settings.oidc_login_ip_max_attempts:
            if expired_sensitive_rows or expired_ids:
                # Persist secret minimization before raising the public 429.
                # This commit also releases the per-IP transaction lock only
                # after the complete cleanup/count decision has been made.
                session.commit()
            raise DomainError("Unable to start sign-in", "auth_rate_limited", 429)

        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        session.add(
            OidcLoginTransaction(
                state_digest=_digest(state, pepper=pepper),
                nonce=nonce,
                code_verifier=code_verifier,
                return_to=normalized_return_to,
                prompt=prompt,
                created_at=now,
                expires_at=now
                + timedelta(seconds=settings.oidc_login_transaction_ttl_seconds),
                ip_hash=hashed_ip,
            )
        )
        session.flush()
        query = {
            "response_type": "code",
            "client_id": settings.oidc_client_id,
            "redirect_uri": settings.oidc_redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if prompt == "step_up":
            query["prompt"] = "login"
            query["max_age"] = "0"
        elif prompt:
            query["prompt"] = prompt
        return f"{settings.oidc_authorization_endpoint}?{urlencode(query)}", state

    @staticmethod
    def consume_transaction(
        session: Session,
        *,
        settings: Any,
        state: str,
        ip_address: str,
    ) -> tuple[OidcLoginTransaction, OidcExchangeContext]:
        if not state or len(state) > 512:
            raise DomainError("Sign-in callback is invalid", "oidc_callback_invalid", 400)
        transaction = session.scalar(
            select(OidcLoginTransaction)
            .where(
                OidcLoginTransaction.state_digest
                == _digest(state, pepper=settings.jwt_signing_secret)
            )
            .with_for_update()
        )
        now = utcnow()
        valid = (
            transaction is not None
            and transaction.consumed_at is None
            and _as_utc(transaction.expires_at) > now
        )
        if not valid:
            raise DomainError("Sign-in callback is invalid", "oidc_callback_invalid", 400)
        exchange_context = OidcExchangeContext(
            transaction_id=transaction.id,
            nonce=transaction.nonce,
            code_verifier=transaction.code_verifier,
        )
        transaction.consumed_at = now
        rate_limit_retention = _as_utc(transaction.created_at) + timedelta(
            seconds=settings.oidc_login_ip_window_seconds
        )
        if _as_utc(transaction.expires_at) < rate_limit_retention:
            transaction.expires_at = rate_limit_retention
        # Scrub raw login secrets in the same durable transition that consumes
        # state. The fixed tombstone remains through the complete IP rate-limit
        # window, even when that window is longer than the login transaction
        # TTL, and bounded expiry cleanup deletes it afterwards.
        transaction.nonce = "consumed"
        transaction.code_verifier = "consumed"
        session.flush()
        return transaction, exchange_context

    @staticmethod
    def _json_response(
        response: httpx.Response,
        *,
        label: str,
        maximum: int,
        content_types: frozenset[str] = frozenset({"application/json"}),
    ) -> dict:
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if response.status_code < 200 or response.status_code >= 300:
            raise DomainError(f"{label} failed", "oidc_provider_unavailable", 502)
        if content_type not in content_types:
            raise DomainError(f"{label} failed", "oidc_provider_invalid", 502)
        try:
            return strict_json_object(response.content, maximum_bytes=maximum)
        except JwtAuthenticationError as exc:
            raise DomainError(f"{label} failed", "oidc_provider_invalid", 502) from exc

    @classmethod
    def exchange_and_verify(
        cls,
        *,
        settings: Any,
        transaction: OidcExchangeContext,
        code: str,
        http_client: httpx.Client,
    ) -> OidcIdTokenClaims:
        if not code or len(code) > 4096 or any(ord(c) < 32 for c in code):
            raise DomainError("Sign-in callback is invalid", "oidc_callback_invalid", 400)
        try:
            token_response = http_client.post(
                settings.oidc_token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "client_id": settings.oidc_client_id,
                    "redirect_uri": settings.oidc_redirect_uri,
                    "code": code,
                    "code_verifier": transaction.code_verifier,
                },
                headers={"Accept": "application/json"},
                timeout=10.0,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise DomainError(
                "Identity provider is unavailable", "oidc_provider_unavailable", 502
            ) from exc
        body = cls._json_response(
            token_response, label="OIDC token exchange", maximum=262_144
        )
        id_token = body.get("id_token")
        if not isinstance(id_token, str):
            raise DomainError(
                "OIDC token exchange failed", "oidc_provider_invalid", 502
            )

        def fetch_jwks() -> dict[str, Any]:
            try:
                response = http_client.get(
                    settings.oidc_jwks_uri,
                    headers={"Accept": "application/json"},
                    timeout=10.0,
                    follow_redirects=False,
                )
            except httpx.HTTPError as exc:
                raise DomainError(
                    "Identity provider is unavailable",
                    "oidc_provider_unavailable",
                    502,
                ) from exc
            return cls._json_response(
                response,
                label="OIDC JWKS read",
                maximum=1_048_576,
                content_types=frozenset(
                    {"application/json", "application/jwk-set+json"}
                ),
            )

        jwks = fetch_jwks()
        try:
            return verify_oidc_id_token(
                id_token,
                jwks=jwks,
                issuer=settings.oidc_issuer,
                audience=settings.oidc_client_id,
                nonce=transaction.nonce,
                maximum_lifetime_seconds=settings.oidc_id_token_max_lifetime_seconds,
            )
        except OidcUnknownKeyId:
            jwks = fetch_jwks()
            try:
                return verify_oidc_id_token(
                    id_token,
                    jwks=jwks,
                    issuer=settings.oidc_issuer,
                    audience=settings.oidc_client_id,
                    nonce=transaction.nonce,
                    maximum_lifetime_seconds=(
                        settings.oidc_id_token_max_lifetime_seconds
                    ),
                )
            except JwtAuthenticationError as exc:
                raise DomainError(
                    "Sign-in callback is invalid", "oidc_token_invalid", 401
                ) from exc
        except JwtAuthenticationError as exc:
            raise DomainError(
                "Sign-in callback is invalid", "oidc_token_invalid", 401
            ) from exc

    @staticmethod
    def bind_identity(
        session: Session,
        *,
        claims: OidcIdTokenClaims,
        settings: Any,
        request_id: str,
        ip_hash: str | None,
        user_agent: str,
    ) -> tuple[User, ExternalIdentity]:
        subject_hash = _digest(
            "subject:" + claims.issuer + "\x00" + claims.subject,
            pepper=settings.jwt_signing_secret,
        )
        owner_subject = is_platform_owner_identity(
            issuer=claims.issuer,
            subject=claims.subject,
            configured_issuer=settings.oidc_issuer,
            configured_subjects=settings.platform_owner_user_ids,
        )
        identity = session.scalar(
            select(ExternalIdentity)
            .where(
                ExternalIdentity.issuer == claims.issuer,
                ExternalIdentity.subject == claims.subject,
            )
            .with_for_update()
        )
        now = utcnow()
        if identity is not None:
            user = session.scalar(
                select(User).where(User.id == identity.user_id).with_for_update()
            )
            if user is None:
                raise DomainError("Sign-in is unavailable", "account_unavailable", 403)
            if user.status != UserStatus.ACTIVE:
                append_security_event(
                    session,
                    event_type="auth.login.blocked",
                    user_id=user.id,
                    outcome=AuditOutcome.FAILED,
                    reason="account_not_active",
                    issuer=claims.issuer,
                    subject_hash=subject_hash,
                    request_id=request_id,
                    ip_hash=ip_hash,
                    user_agent=user_agent,
                )
                raise DomainError("Sign-in is unavailable", "account_unavailable", 403)
            if user.email != claims.email:
                conflicting_user = session.scalar(
                    select(User.id)
                    .where(
                        func.lower(User.email) == claims.email.lower(),
                        User.id != user.id,
                    )
                    .limit(1)
                )
                if conflicting_user is None:
                    previous_auth_version = user.auth_version
                    user.email = claims.email
                    identity.email_at_link = claims.email
                    user.auth_version += 1
                    SessionService.revoke_all(
                        session,
                        user_id=user.id,
                        actor_session_id=None,
                        reason="identity_email_changed",
                        ip_hash=ip_hash,
                        user_agent=user_agent,
                        request_id=request_id,
                    )
                    append_security_event(
                        session,
                        event_type="auth.identity.email_changed",
                        user_id=user.id,
                        issuer=claims.issuer,
                        subject_hash=subject_hash,
                        request_id=request_id,
                        ip_hash=ip_hash,
                        user_agent=user_agent,
                        previous_auth_version=previous_auth_version,
                        new_auth_version=user.auth_version,
                    )
                    AuditService.append(
                        session,
                        actor_user_id=user.id,
                        action="auth.identity.email_change",
                        target_type="external_identity",
                        target_id=identity.id,
                        before_summary={"auth_version": previous_auth_version},
                        after_summary={"auth_version": user.auth_version},
                        request_id=request_id,
                    )
                else:
                    append_security_event(
                        session,
                        event_type="auth.login.blocked",
                        user_id=user.id,
                        outcome=AuditOutcome.FAILED,
                        reason="identity_email_conflict",
                        issuer=claims.issuer,
                        subject_hash=subject_hash,
                        request_id=request_id,
                        ip_hash=ip_hash,
                        user_agent=user_agent,
                    )
                    raise DomainError("Sign-in is unavailable", "oidc_login_failed", 409)
            if owner_subject and not user.is_platform_admin:
                user.is_platform_admin = True
                append_security_event(
                    session,
                    event_type="platform_owner.bootstrap",
                    user_id=user.id,
                    issuer=claims.issuer,
                    subject_hash=subject_hash,
                    request_id=request_id,
                    ip_hash=ip_hash,
                    user_agent=user_agent,
                )
            identity.last_login_at = now
            user.last_login_at = now
            user.email_verified_at = user.email_verified_at or now
            PersonalWorkspaceService.ensure(session, user_id=user.id)
            session.flush()
            return user, identity

        user = session.scalar(
            select(User)
            .where(func.lower(User.email) == claims.email.lower())
            .with_for_update()
        )
        if user is not None:
            existing_identity = session.scalar(
                select(ExternalIdentity.id)
                .where(ExternalIdentity.user_id == user.id)
                .limit(1)
            )
            if existing_identity is not None:
                append_security_event(
                    session,
                    event_type="auth.login.blocked",
                    user_id=user.id,
                    outcome=AuditOutcome.FAILED,
                    reason="identity_binding_conflict",
                    issuer=claims.issuer,
                    subject_hash=subject_hash,
                    request_id=request_id,
                    ip_hash=ip_hash,
                    user_agent=user_agent,
                )
                raise DomainError("Sign-in is unavailable", "oidc_login_failed", 409)
            if user.status in {UserStatus.SUSPENDED, UserStatus.DEACTIVATED}:
                raise DomainError("Sign-in is unavailable", "account_unavailable", 403)
            if user.status == UserStatus.PENDING and not settings.oidc_self_signup_enabled:
                valid_invitation = session.scalar(
                    select(CompanyInvitation.id)
                    .where(
                        CompanyInvitation.email == claims.email,
                        CompanyInvitation.status == CompanyInvitationStatus.PENDING,
                        CompanyInvitation.expires_at > now,
                    )
                    .limit(1)
                )
                if valid_invitation is None:
                    append_security_event(
                        session,
                        event_type="auth.login.blocked",
                        user_id=user.id,
                        outcome=AuditOutcome.FAILED,
                        reason="signup_not_allowed",
                        issuer=claims.issuer,
                        subject_hash=subject_hash,
                        request_id=request_id,
                        ip_hash=ip_hash,
                        user_agent=user_agent,
                    )
                    raise DomainError("Sign-in is unavailable", "oidc_login_failed", 403)
            if user.email != claims.email:
                conflicting_user = session.scalar(
                    select(User.id)
                    .where(
                        func.lower(User.email) == claims.email.lower(),
                        User.id != user.id,
                    )
                    .limit(1)
                )
                if conflicting_user is not None:
                    raise DomainError(
                        "Sign-in is unavailable", "oidc_login_failed", 409
                    )
                # This is a first identity link, so the NOT NULL identity FK on
                # AuthSession guarantees there are no production BFF sessions
                # to invalidate. Canonicalize the legacy case/IDNA spelling
                # before the new identity/session becomes visible.
                user.email = claims.email
                append_security_event(
                    session,
                    event_type="auth.identity.email_canonicalized",
                    user_id=user.id,
                    issuer=claims.issuer,
                    subject_hash=subject_hash,
                    request_id=request_id,
                    ip_hash=ip_hash,
                    user_agent=user_agent,
                )
            user.status = UserStatus.ACTIVE
        else:
            if not settings.oidc_self_signup_enabled and not owner_subject:
                append_security_event(
                    session,
                    event_type="auth.login.blocked",
                    user_id=None,
                    outcome=AuditOutcome.FAILED,
                    reason="signup_not_allowed",
                    issuer=claims.issuer,
                    subject_hash=subject_hash,
                    request_id=request_id,
                    ip_hash=ip_hash,
                    user_agent=user_agent,
                )
                raise DomainError("Sign-in is unavailable", "oidc_login_failed", 403)
            user = User(
                email=claims.email,
                display_name=claims.display_name,
                status=UserStatus.ACTIVE,
                email_verified_at=now,
                last_login_at=now,
                is_platform_admin=owner_subject,
            )
            session.add(user)
            session.flush()
        identity = ExternalIdentity(
            user_id=user.id,
            issuer=claims.issuer,
            subject=claims.subject,
            email_at_link=claims.email,
            last_login_at=now,
        )
        user.email_verified_at = user.email_verified_at or now
        user.last_login_at = now
        if owner_subject and not user.is_platform_admin:
            user.is_platform_admin = True
        PersonalWorkspaceService.ensure(session, user_id=user.id)
        session.add(identity)
        session.flush()
        append_security_event(
            session,
            event_type="auth.identity.linked",
            user_id=user.id,
            issuer=claims.issuer,
            subject_hash=subject_hash,
            request_id=request_id,
            ip_hash=ip_hash,
            user_agent=user_agent,
        )
        if owner_subject:
            append_security_event(
                session,
                event_type="platform_owner.bootstrap",
                user_id=user.id,
                issuer=claims.issuer,
                subject_hash=subject_hash,
                request_id=request_id,
                ip_hash=ip_hash,
                user_agent=user_agent,
            )
        return user, identity


class AccountService:
    @staticmethod
    def update_profile(
        session: Session,
        *,
        user_id: str,
        display_name: str,
        expected_auth_version: int,
        expected_updated_at: datetime,
        auth_session_id: str | None,
        ip_hash: str | None,
        user_agent: str,
        request_id: str = "system",
    ) -> User:
        user = session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None or user.status != UserStatus.ACTIVE:
            raise PermissionDeniedError("Account is not active")
        if (
            user.auth_version != expected_auth_version
            or _as_utc(user.updated_at) != _as_utc(expected_updated_at)
        ):
            raise ConflictError("Account profile was changed elsewhere")
        normalized = display_name.strip()
        if not normalized or any(ord(c) < 32 or ord(c) == 127 for c in normalized):
            raise DomainError("Display name is invalid", "profile_invalid", 422)
        user.display_name = normalized
        session.flush()
        append_security_event(
            session,
            event_type="account.profile.updated",
            user_id=user.id,
            auth_session_id=auth_session_id,
            ip_hash=ip_hash,
            user_agent=user_agent,
            request_id=request_id,
        )
        return user

    @staticmethod
    def set_global_status(
        session: Session,
        *,
        actor_user_id: str,
        target_user_id: str,
        expected_status: UserStatus,
        expected_auth_version: int,
        target_status: UserStatus,
        platform_owner_user_ids: set[str],
        request_id: str,
        ip_hash: str | None,
        user_agent: str,
    ) -> User:
        if target_status not in {UserStatus.ACTIVE, UserStatus.SUSPENDED}:
            raise DomainError("Account status transition is invalid", "status_invalid", 422)
        user = session.scalar(
            select(User).where(User.id == target_user_id).with_for_update()
        )
        if user is None:
            raise NotFoundError("Account does not exist")
        if user.status in {UserStatus.PENDING, UserStatus.DEACTIVATED}:
            raise ConflictError("Account cannot be changed through this operation")
        if user.status != expected_status or user.auth_version != expected_auth_version:
            raise ConflictError("Account security state was changed elsewhere")
        if user.id == actor_user_id and target_status == UserStatus.SUSPENDED:
            raise ConflictError("Platform owner cannot suspend the current account")
        if user.id in platform_owner_user_ids and target_status == UserStatus.SUSPENDED:
            raise ConflictError("Platform owner allowlist must be changed before suspension")
        if target_status == UserStatus.SUSPENDED:
            active_company_owner = session.scalar(
                select(CompanyMembership.id)
                .join(MembershipRole, MembershipRole.membership_id == CompanyMembership.id)
                .join(Role, Role.id == MembershipRole.role_id)
                .where(
                    CompanyMembership.user_id == user.id,
                    CompanyMembership.status == MembershipStatus.ACTIVE,
                    Role.system_key == "owner",
                )
                .limit(1)
            )
            if active_company_owner is not None:
                raise ConflictError(
                    "Company ownership must be transferred before suspension"
                )
        if user.status == target_status:
            return user
        before = user.status
        previous_auth_version = user.auth_version
        user.status = target_status
        user.auth_version += 1
        SessionService.revoke_all(
            session,
            user_id=user.id,
            actor_session_id=None,
            reason="account_status_changed",
            ip_hash=ip_hash,
            user_agent=user_agent,
            request_id=request_id,
        )
        append_security_event(
            session,
            event_type="account.status.changed",
            user_id=user.id,
            request_id=request_id,
            ip_hash=ip_hash,
            user_agent=user_agent,
            reason=target_status.value,
            previous_auth_version=previous_auth_version,
            new_auth_version=user.auth_version,
        )
        AuditService.append(
            session,
            actor_user_id=actor_user_id,
            action="platform.user.status.update",
            target_type="user",
            target_id=user.id,
            before_summary={"status": before.value, "auth_version": previous_auth_version},
            after_summary={"status": target_status.value, "auth_version": user.auth_version},
            request_id=request_id,
        )
        session.flush()
        return user

    @staticmethod
    def deactivate(
        session: Session,
        *,
        user_id: str,
        external_subject: str,
        owner_subjects: set[str],
        expected_auth_version: int,
        auth_session_id: str | None,
        ip_hash: str | None,
        user_agent: str,
        request_id: str = "system",
    ) -> User:
        user = session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None or user.status != UserStatus.ACTIVE:
            raise ConflictError("Account is not active")
        if user.auth_version != expected_auth_version:
            raise ConflictError("Account security state was changed elsewhere")
        if external_subject in owner_subjects:
            raise ConflictError("Platform owner must be transferred before deactivation")
        company_owner = session.scalar(
            select(CompanyMembership.id)
            .join(MembershipRole, MembershipRole.membership_id == CompanyMembership.id)
            .join(Role, Role.id == MembershipRole.role_id)
            .where(
                CompanyMembership.user_id == user_id,
                CompanyMembership.status == MembershipStatus.ACTIVE,
                Role.system_key == "owner",
            )
            .limit(1)
        )
        if company_owner is not None:
            raise ConflictError("Company ownership must be transferred before deactivation")
        previous = user.auth_version
        now = utcnow()
        user.status = UserStatus.DEACTIVATED
        user.deactivated_at = now
        user.auth_version += 1
        SessionService.revoke_all(
            session,
            user_id=user.id,
            actor_session_id=auth_session_id,
            reason="account_deactivated",
            ip_hash=ip_hash,
            user_agent=user_agent,
            request_id=request_id,
        )
        append_security_event(
            session,
            event_type="account.deactivated",
            user_id=user.id,
            auth_session_id=auth_session_id,
            ip_hash=ip_hash,
            user_agent=user_agent,
            previous_auth_version=previous,
            new_auth_version=user.auth_version,
            request_id=request_id,
        )
        AuditService.append(
            session,
            actor_user_id=user.id,
            action="account.deactivate",
            target_type="user",
            target_id=user.id,
            before_summary={"status": UserStatus.ACTIVE.value, "auth_version": previous},
            after_summary={
                "status": UserStatus.DEACTIVATED.value,
                "auth_version": user.auth_version,
            },
            request_id=request_id,
        )
        session.flush()
        return user


class OwnerTransferService:
    @staticmethod
    def transfer(
        session: Session,
        *,
        company_id: str,
        actor_user_id: str,
        actor_membership_id: str,
        target_membership_id: str,
        expected_current_owner_membership_id: str,
        expected_current_owner_user_id: str,
        former_owner_primary_role: str,
        request_id: str,
    ) -> tuple[CompanyMembership, CompanyMembership]:
        if former_owner_primary_role not in {"operator", "team_lead"}:
            raise DomainError("Owner transfer request is invalid", "owner_transfer_invalid", 422)
        company = session.scalar(
            select(Company).where(Company.id == company_id).with_for_update()
        )
        if company is None or company.status != CompanyStatus.ACTIVE:
            raise NotFoundError("Company does not exist")
        if actor_membership_id != expected_current_owner_membership_id:
            raise ConflictError("Company owner changed; refresh and retry")
        if target_membership_id == expected_current_owner_membership_id:
            raise ConflictError("New owner must be a different active member")
        membership_ids = sorted(
            {expected_current_owner_membership_id, target_membership_id}
        )
        locked_memberships = list(
            session.scalars(
                select(CompanyMembership)
                .where(
                    CompanyMembership.company_id == company_id,
                    CompanyMembership.id.in_(membership_ids),
                )
                .order_by(CompanyMembership.id)
                .with_for_update()
            ).all()
        )
        memberships = {membership.id: membership for membership in locked_memberships}
        current = memberships.get(expected_current_owner_membership_id)
        target = memberships.get(target_membership_id)
        if (
            current is None
            or target is None
            or current.user_id != expected_current_owner_user_id
            or current.user_id != actor_user_id
        ):
            raise ConflictError("Company owner changed; refresh and retry")
        if target.status != MembershipStatus.ACTIVE:
            raise ConflictError("New owner must be an active member")
        user_ids = sorted({current.user_id, target.user_id})
        locked_users = {
            user.id: user
            for user in session.scalars(
                select(User)
                .where(User.id.in_(user_ids))
                .order_by(User.id)
                .with_for_update()
            ).all()
        }
        current_user = locked_users.get(current.user_id)
        target_user = locked_users.get(target.user_id)
        if current_user is None or current_user.status != UserStatus.ACTIVE:
            raise ConflictError("Company owner changed; refresh and retry")
        if target_user is None or target_user.status != UserStatus.ACTIVE:
            raise ConflictError("New owner must be an active account")
        owner_role = AccessLifecycleService.system_role(
            session, company_id=company_id, system_key="owner"
        )
        fallback_role = AccessLifecycleService.system_role(
            session, company_id=company_id, system_key=former_owner_primary_role
        )
        primary_roles = list(
            session.scalars(
                select(Role).where(
                    Role.company_id == company_id,
                    Role.system_key.in_({"operator", "team_lead"}),
                )
            ).all()
        )
        primary_role_ids = {role.id for role in primary_roles}
        assignments = list(
            session.scalars(
                select(MembershipRole)
                .where(MembershipRole.membership_id.in_(membership_ids))
                .order_by(MembershipRole.membership_id, MembershipRole.role_id)
                .with_for_update()
            ).all()
        )
        owner_assignments = list(
            session.scalars(
                select(MembershipRole)
                .join(CompanyMembership, CompanyMembership.id == MembershipRole.membership_id)
                .where(
                    CompanyMembership.company_id == company_id,
                    MembershipRole.role_id == owner_role.id,
                )
                .order_by(MembershipRole.membership_id)
                .with_for_update()
            ).all()
        )
        if len(owner_assignments) != 1 or owner_assignments[0].membership_id != current.id:
            raise ConflictError("Company owner changed; refresh and retry")
        target_primary = [
            assignment
            for assignment in assignments
            if assignment.membership_id == target.id
            and assignment.role_id in primary_role_ids
        ]
        if len(target_primary) != 1:
            raise ConflictError("New owner must have exactly one primary company role")
        for assignment in assignments:
            if (
                assignment.membership_id == current.id
                and assignment.role_id in primary_role_ids | {owner_role.id}
            ) or (
                assignment.membership_id == target.id
                and assignment.role_id in primary_role_ids
            ):
                session.delete(assignment)
        session.flush()
        session.add(MembershipRole(membership_id=current.id, role_id=fallback_role.id))
        session.add(MembershipRole(membership_id=target.id, role_id=owner_role.id))
        session.flush()
        AuditService.append(
            session,
            actor_user_id=actor_user_id,
            action="company.owner.transfer",
            target_type="company",
            target_id=company_id,
            before_summary={
                "owner_membership_id": current.id,
                "owner_user_id": current.user_id,
            },
            after_summary={
                "owner_membership_id": target.id,
                "owner_user_id": target.user_id,
                "former_owner_primary_role": former_owner_primary_role,
            },
            request_id=request_id,
        )
        append_security_event(
            session,
            event_type="company.owner.transferred_out",
            user_id=current.user_id,
            request_id=request_id,
            company_id=company_id,
            reason=former_owner_primary_role,
        )
        append_security_event(
            session,
            event_type="company.owner.transferred_in",
            user_id=target.user_id,
            request_id=request_id,
            company_id=company_id,
        )
        return current, target


class InvitationService:
    @staticmethod
    def handoff_capability(
        invitation: CompanyInvitation, *, pepper: str | None
    ) -> str:
        if not pepper:
            raise DomainError(
                "Invitation handoff is unavailable", "invitation_unavailable", 503
            )
        message = (
            f"invitation-handoff:v1\0{invitation.id}\0{invitation.token_digest}"
        ).encode("utf-8")
        signature = base64.urlsafe_b64encode(
            hmac.new(pepper.encode("utf-8"), message, hashlib.sha256).digest()
        ).rstrip(b"=").decode("ascii")
        return f"v1.{invitation.id}.{signature}"

    @staticmethod
    def by_handoff(
        session: Session,
        *,
        capability: str,
        pepper: str | None,
        for_update: bool,
    ) -> CompanyInvitation:
        if not pepper or not capability or len(capability) > 160:
            raise NotFoundError("Invitation is unavailable")
        parts = capability.split(".")
        if (
            len(parts) != 3
            or parts[0] != "v1"
            or re.fullmatch(r"[0-9a-fA-F-]{36}", parts[1]) is None
            or re.fullmatch(r"[A-Za-z0-9_-]{43}", parts[2]) is None
        ):
            raise NotFoundError("Invitation is unavailable")
        statement = select(CompanyInvitation).where(
            CompanyInvitation.id == parts[1]
        )
        if for_update:
            statement = statement.with_for_update()
        invitation = session.scalar(statement)
        if invitation is None:
            raise NotFoundError("Invitation is unavailable")
        expected = InvitationService.handoff_capability(
            invitation, pepper=pepper
        )
        if not hmac.compare_digest(expected, capability):
            raise NotFoundError("Invitation is unavailable")
        if (
            invitation.status == CompanyInvitationStatus.PENDING
            and _as_utc(invitation.expires_at) <= utcnow()
        ):
            invitation.status = CompanyInvitationStatus.EXPIRED
            session.flush()
        return invitation

    @staticmethod
    def require_pending(invitation: CompanyInvitation) -> None:
        if invitation.status == CompanyInvitationStatus.ACCEPTED:
            raise DomainError("Invitation was already used", "invitation_used", 409)
        if invitation.status == CompanyInvitationStatus.EXPIRED:
            raise DomainError("Invitation has expired", "invitation_expired", 410)
        if invitation.status == CompanyInvitationStatus.REVOKED:
            raise DomainError("Invitation was revoked", "invitation_revoked", 409)
        if invitation.status != CompanyInvitationStatus.PENDING:
            raise DomainError("Invitation is unavailable", "invitation_invalid", 404)

    @staticmethod
    def _effective_status(invitation: CompanyInvitation) -> CompanyInvitationStatus:
        if (
            invitation.status == CompanyInvitationStatus.PENDING
            and _as_utc(invitation.expires_at) <= utcnow()
        ):
            return CompanyInvitationStatus.EXPIRED
        return invitation.status

    @staticmethod
    def response(
        invitation: CompanyInvitation,
        *,
        acceptance_token: str | None = None,
        frontend_origin: str | None = None,
        effective_primary_role: str | None = None,
    ) -> dict[str, Any]:
        invitation_url = None
        if acceptance_token:
            prefix = (frontend_origin or "").rstrip("/")
            invitation_url = f"{prefix}/invite#token={quote(acceptance_token, safe='')}"
        return {
            "id": invitation.id,
            "company_id": invitation.company_id,
            "email": invitation.email,
            "display_name": invitation.display_name,
            "primary_role": effective_primary_role or invitation.primary_role,
            "status": InvitationService._effective_status(invitation),
            "expires_at": invitation.expires_at,
            "created_by_user_id": invitation.created_by_user_id,
            "accepted_by_user_id": invitation.accepted_by_user_id,
            "accepted_at": invitation.accepted_at,
            "revoked_at": invitation.revoked_at,
            "created_at": invitation.created_at,
            "updated_at": invitation.updated_at,
            "invitation_url": invitation_url,
        }

    @staticmethod
    def effective_primary_role(
        session: Session, *, invitation: CompanyInvitation
    ) -> str:
        try:
            canonical_email = normalize_email_address(invitation.email)
        except JwtAuthenticationError as exc:
            raise DomainError(
                "Invitation is unavailable", "invitation_invalid", 404
            ) from exc
        owner_membership = session.scalar(
            select(CompanyMembership.id)
            .join(User, User.id == CompanyMembership.user_id)
            .join(MembershipRole, MembershipRole.membership_id == CompanyMembership.id)
            .join(Role, Role.id == MembershipRole.role_id)
            .where(
                CompanyMembership.company_id == invitation.company_id,
                func.lower(User.email) == canonical_email,
                Role.system_key == "owner",
            )
            .limit(1)
        )
        return "owner" if owner_membership is not None else invitation.primary_role

    @staticmethod
    def create(
        session: Session,
        *,
        company_id: str,
        actor_user_id: str,
        email: str,
        display_name: str,
        primary_role: str,
        idempotency_key: str,
        expires_in_seconds: int,
        pepper: str | None,
        request_id: str,
        allow_existing_owner_membership: bool = False,
    ) -> tuple[CompanyInvitation, str | None, bool]:
        try:
            normalized_email = normalize_email_address(email)
        except JwtAuthenticationError as exc:
            raise DomainError("Invitation request is invalid", "invitation_invalid", 422) from exc
        normalized_name = display_name.strip()
        if (
            primary_role not in {"operator", "team_lead"}
            or not normalized_name
            or any(ord(character) < 32 or ord(character) == 127 for character in normalized_name)
            or len(normalized_name) > 120
            or re.fullmatch(r"[A-Za-z0-9._:-]{8,120}", idempotency_key) is None
        ):
            raise DomainError("Invitation request is invalid", "invitation_invalid", 422)
        payload = {
            "email": normalized_email,
            "display_name": normalized_name,
            "primary_role": primary_role,
            "expires_in_seconds": expires_in_seconds,
        }
        fingerprint = _request_fingerprint(payload)
        existing = session.scalar(
            select(CompanyInvitation)
            .where(
                CompanyInvitation.company_id == company_id,
                CompanyInvitation.idempotency_key == idempotency_key,
            )
            .with_for_update()
        )
        if existing is not None:
            if not hmac.compare_digest(existing.request_fingerprint, fingerprint):
                raise ConflictError("Invitation idempotency key was reused")
            return existing, None, False
        invited_user = session.scalar(
            select(User).where(func.lower(User.email) == normalized_email).with_for_update()
        )
        if invited_user is None:
            invited_user = User(
                email=normalized_email,
                display_name=normalized_name,
                status=UserStatus.PENDING,
            )
            session.add(invited_user)
            session.flush()
            append_security_event(
                session,
                event_type="account.invitation.preprovisioned",
                user_id=invited_user.id,
                request_id=request_id,
            )
        elif invited_user.status in {UserStatus.SUSPENDED, UserStatus.DEACTIVATED}:
            raise ConflictError("Invitation cannot be created")
        existing_membership = session.scalar(
            select(CompanyMembership.id)
            .where(
                CompanyMembership.company_id == company_id,
                CompanyMembership.user_id == invited_user.id,
            )
            .limit(1)
        )
        if existing_membership is not None:
            owner_assignment = session.scalar(
                select(MembershipRole)
                .join(Role, Role.id == MembershipRole.role_id)
                .where(
                    MembershipRole.membership_id == existing_membership,
                    Role.company_id == company_id,
                    Role.system_key == "owner",
                )
                .limit(1)
            )
            if not allow_existing_owner_membership or owner_assignment is None:
                raise ConflictError("Invitation cannot be created")
        raw_token = secrets.token_urlsafe(48)
        invitation = CompanyInvitation(
            token_digest=_digest(raw_token, pepper=pepper),
            company_id=company_id,
            email=normalized_email,
            display_name=normalized_name,
            primary_role=primary_role,
            status=CompanyInvitationStatus.PENDING,
            expires_at=utcnow() + timedelta(seconds=expires_in_seconds),
            created_by_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
        )
        session.add(invitation)
        session.flush()
        AuditService.append(
            session,
            actor_user_id=actor_user_id,
            action="company.invitation.create",
            target_type="company_invitation",
            target_id=invitation.id,
            before_summary={},
            after_summary={
                "company_id": company_id,
                "primary_role": primary_role,
                "status": invitation.status.value,
                "expires_at": invitation.expires_at.isoformat(),
            },
            request_id=request_id,
        )
        return invitation, raw_token, True

    @staticmethod
    def list(
        session: Session,
        *,
        company_id: str,
        page: int = 1,
        page_size: int = 100,
    ) -> list[CompanyInvitation]:
        return list(
            session.scalars(
                select(CompanyInvitation)
                .where(CompanyInvitation.company_id == company_id)
                .order_by(CompanyInvitation.created_at.desc(), CompanyInvitation.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )

    @staticmethod
    def count(session: Session, *, company_id: str) -> int:
        return int(
            session.scalar(
                select(func.count(CompanyInvitation.id)).where(
                    CompanyInvitation.company_id == company_id
                )
            )
            or 0
        )

    @staticmethod
    def reissue(
        session: Session,
        *,
        company_id: str,
        invitation_id: str,
        actor_user_id: str,
        expires_in_seconds: int,
        pepper: str | None,
        request_id: str,
        allow_owner_invitation: bool = False,
    ) -> tuple[CompanyInvitation, str]:
        invitation = session.scalar(
            select(CompanyInvitation)
            .where(
                CompanyInvitation.id == invitation_id,
                CompanyInvitation.company_id == company_id,
            )
            .with_for_update()
        )
        if invitation is None:
            raise NotFoundError("Invitation does not exist")
        if (
            InvitationService.effective_primary_role(
                session, invitation=invitation
            )
            == "owner"
            and not allow_owner_invitation
        ):
            raise PermissionDeniedError(
                "Owner onboarding invitations are managed by the platform"
            )
        if invitation.status == CompanyInvitationStatus.ACCEPTED:
            raise ConflictError("Accepted invitation cannot be reissued")
        raw_token = secrets.token_urlsafe(48)
        before = InvitationService._effective_status(invitation)
        invitation.token_digest = _digest(raw_token, pepper=pepper)
        invitation.status = CompanyInvitationStatus.PENDING
        invitation.expires_at = utcnow() + timedelta(seconds=expires_in_seconds)
        invitation.revoked_at = None
        invitation.accepted_at = None
        invitation.accepted_by_user_id = None
        session.flush()
        AuditService.append(
            session,
            actor_user_id=actor_user_id,
            action="company.invitation.reissue",
            target_type="company_invitation",
            target_id=invitation.id,
            before_summary={"status": before.value},
            after_summary={
                "status": invitation.status.value,
                "expires_at": invitation.expires_at.isoformat(),
            },
            request_id=request_id,
        )
        return invitation, raw_token

    @staticmethod
    def revoke(
        session: Session,
        *,
        company_id: str,
        invitation_id: str,
        actor_user_id: str,
        request_id: str,
        allow_owner_invitation: bool = False,
    ) -> CompanyInvitation:
        invitation = session.scalar(
            select(CompanyInvitation)
            .where(
                CompanyInvitation.id == invitation_id,
                CompanyInvitation.company_id == company_id,
            )
            .with_for_update()
        )
        if invitation is None:
            raise NotFoundError("Invitation does not exist")
        if (
            InvitationService.effective_primary_role(
                session, invitation=invitation
            )
            == "owner"
            and not allow_owner_invitation
        ):
            raise PermissionDeniedError(
                "Owner onboarding invitations are managed by the platform"
            )
        if invitation.status == CompanyInvitationStatus.ACCEPTED:
            raise ConflictError("Accepted invitation cannot be revoked")
        if invitation.status != CompanyInvitationStatus.REVOKED:
            before = InvitationService._effective_status(invitation)
            invitation.status = CompanyInvitationStatus.REVOKED
            invitation.revoked_at = utcnow()
            session.flush()
            AuditService.append(
                session,
                actor_user_id=actor_user_id,
                action="company.invitation.revoke",
                target_type="company_invitation",
                target_id=invitation.id,
                before_summary={"status": before.value},
                after_summary={"status": invitation.status.value},
                request_id=request_id,
            )
        return invitation

    @staticmethod
    def reissue_owner_onboarding(
        session: Session,
        *,
        company_id: str,
        expected_owner_membership_id: str,
        expected_owner_user_id: str,
        actor_user_id: str,
        expires_in_seconds: int,
        pepper: str | None,
        request_id: str,
        replacement_email: str | None = None,
        replacement_display_name: str | None = None,
    ) -> tuple[CompanyInvitation, str, CompanyMembership]:
        company = session.scalar(
            select(Company).where(Company.id == company_id).with_for_update()
        )
        if company is None:
            raise NotFoundError("Company does not exist")
        membership = session.scalar(
            select(CompanyMembership)
            .where(
                CompanyMembership.id == expected_owner_membership_id,
                CompanyMembership.company_id == company_id,
            )
            .with_for_update()
        )
        if (
            membership is None
            or membership.user_id != expected_owner_user_id
            or membership.status != MembershipStatus.DISABLED
        ):
            raise DomainError(
                "Company owner onboarding state changed",
                "owner_onboarding_changed",
                409,
            )
        current_user = session.scalar(
            select(User).where(User.id == expected_owner_user_id).with_for_update()
        )
        owner_assignments = list(
            session.scalars(
                select(MembershipRole.membership_id)
                .join(
                    CompanyMembership,
                    CompanyMembership.id == MembershipRole.membership_id,
                )
                .join(Role, Role.id == MembershipRole.role_id)
                .where(
                    CompanyMembership.company_id == company_id,
                    Role.company_id == company_id,
                    Role.system_key == "owner",
                )
                .order_by(MembershipRole.membership_id)
                .with_for_update()
            ).all()
        )
        if (
            current_user is None
            or current_user.status
            not in {UserStatus.PENDING, UserStatus.ACTIVE}
            or owner_assignments != [membership.id]
        ):
            raise DomainError(
                "Company owner onboarding state changed",
                "owner_onboarding_changed",
                409,
            )
        try:
            canonical_current_email = normalize_email_address(current_user.email)
        except JwtAuthenticationError as exc:
            raise DomainError(
                "Company owner onboarding state changed",
                "owner_onboarding_changed",
                409,
            ) from exc
        candidates = list(
            session.scalars(
                select(CompanyInvitation)
                .where(
                    CompanyInvitation.company_id == company_id,
                    func.lower(CompanyInvitation.email) == canonical_current_email,
                )
                .order_by(CompanyInvitation.created_at.desc(), CompanyInvitation.id)
                .with_for_update()
            ).all()
        )
        invitation = next(
            (
                item
                for item in candidates
                if InvitationService.effective_primary_role(
                    session, invitation=item
                )
                == "owner"
            ),
            None,
        )
        if invitation is None:
            raise DomainError(
                "Company owner onboarding invitation is unavailable",
                "owner_onboarding_unavailable",
                404,
            )

        target_user = current_user
        before_email = current_user.email
        if replacement_email is not None:
            try:
                normalized_email = normalize_email_address(replacement_email)
            except JwtAuthenticationError as exc:
                raise DomainError(
                    "Owner replacement is invalid",
                    "owner_onboarding_replacement_invalid",
                    422,
                ) from exc
            normalized_name = (
                replacement_display_name.strip()
                if replacement_display_name is not None
                else invitation.display_name
            )
            if (
                not normalized_name
                or len(normalized_name) > 120
                or any(
                    ord(character) < 32 or ord(character) == 127
                    for character in normalized_name
                )
            ):
                raise DomainError(
                    "Owner replacement is invalid",
                    "owner_onboarding_replacement_invalid",
                    422,
                )

            if current_user.status == UserStatus.PENDING:
                current_identity_count = int(
                    session.scalar(
                        select(func.count(ExternalIdentity.id)).where(
                            ExternalIdentity.user_id == current_user.id
                        )
                    )
                    or 0
                )
                current_membership_count = int(
                    session.scalar(
                        select(func.count(CompanyMembership.id)).where(
                            CompanyMembership.user_id == current_user.id
                        )
                    )
                    or 0
                )
                if current_identity_count or current_membership_count != 1:
                    raise DomainError(
                        "Company owner onboarding state changed",
                        "owner_onboarding_changed",
                        409,
                    )

            target_user = session.scalar(
                select(User)
                .where(func.lower(User.email) == normalized_email.lower())
                .with_for_update()
            )
            if target_user is None:
                target_user = User(
                    email=normalized_email,
                    display_name=normalized_name,
                    status=UserStatus.PENDING,
                )
                session.add(target_user)
                session.flush()
            elif target_user.status in {
                UserStatus.SUSPENDED,
                UserStatus.DEACTIVATED,
            }:
                raise DomainError(
                    "Owner replacement account is unavailable",
                    "owner_onboarding_replacement_conflict",
                    409,
                )

            if target_user.id != current_user.id:
                same_company_membership = session.scalar(
                    select(CompanyMembership.id)
                    .where(
                        CompanyMembership.company_id == company_id,
                        CompanyMembership.user_id == target_user.id,
                    )
                    .limit(1)
                )
                if same_company_membership is not None:
                    raise DomainError(
                        "Owner replacement account is unavailable",
                        "owner_onboarding_replacement_conflict",
                        409,
                    )
                if target_user.status == UserStatus.PENDING:
                    target_identity_count = int(
                        session.scalar(
                            select(func.count(ExternalIdentity.id)).where(
                                ExternalIdentity.user_id == target_user.id
                            )
                        )
                        or 0
                    )
                    target_membership_count = int(
                        session.scalar(
                            select(func.count(CompanyMembership.id)).where(
                                CompanyMembership.user_id == target_user.id
                            )
                        )
                        or 0
                    )
                    if target_identity_count or target_membership_count:
                        raise DomainError(
                            "Owner replacement account is unavailable",
                            "owner_onboarding_replacement_conflict",
                            409,
                        )
                membership.user_id = target_user.id
            elif target_user.status == UserStatus.PENDING:
                target_user.display_name = normalized_name

            invitation.email = normalized_email
            invitation.display_name = normalized_name
            invitation.request_fingerprint = _request_fingerprint(
                {
                    "email": normalized_email,
                    "display_name": normalized_name,
                    "primary_role": invitation.primary_role,
                    "expires_in_seconds": expires_in_seconds,
                }
            )
            session.flush()
            AuditService.append(
                session,
                actor_user_id=actor_user_id,
                action="company.owner.onboarding.replace",
                target_type="company",
                target_id=company_id,
                before_summary={
                    "owner_membership_id": membership.id,
                    "owner_user_id": current_user.id,
                    "owner_email": before_email,
                },
                after_summary={
                    "owner_membership_id": membership.id,
                    "owner_user_id": target_user.id,
                    "owner_email": normalized_email,
                },
                request_id=request_id,
            )
            append_security_event(
                session,
                event_type="company.owner.onboarding_replaced",
                user_id=target_user.id,
                request_id=request_id,
                invitation_id=invitation.id,
                company_id=company_id,
            )
        invitation, token = InvitationService.reissue(
            session,
            company_id=company_id,
            invitation_id=invitation.id,
            actor_user_id=actor_user_id,
            expires_in_seconds=expires_in_seconds,
            pepper=pepper,
            request_id=request_id,
            allow_owner_invitation=True,
        )
        return invitation, token, membership

    @staticmethod
    def by_token(
        session: Session,
        *,
        token: str,
        pepper: str | None,
        for_update: bool,
    ) -> CompanyInvitation:
        if not token or len(token) > 512:
            raise NotFoundError("Invitation is unavailable")
        statement = select(CompanyInvitation).where(
            CompanyInvitation.token_digest == _digest(token, pepper=pepper)
        )
        if for_update:
            statement = statement.with_for_update()
        invitation = session.scalar(statement)
        if invitation is None:
            raise NotFoundError("Invitation is unavailable")
        if (
            invitation.status == CompanyInvitationStatus.PENDING
            and _as_utc(invitation.expires_at) <= utcnow()
        ):
            invitation.status = CompanyInvitationStatus.EXPIRED
            session.flush()
        return invitation

    @staticmethod
    def preview(
        session: Session, *, token: str, pepper: str | None
    ) -> tuple[CompanyInvitation, Company]:
        invitation = InvitationService.by_token(
            session, token=token, pepper=pepper, for_update=True
        )
        company = session.get(Company, invitation.company_id)
        if company is None:
            raise NotFoundError("Invitation is unavailable")
        return invitation, company

    @staticmethod
    def accept(
        session: Session,
        *,
        token: str | None,
        pepper: str | None,
        user: User,
        actor_session_id: str | None,
        request_id: str,
        ip_hash: str | None,
        user_agent: str,
        handoff: str | None = None,
    ) -> CompanyMembership:
        if handoff is not None:
            invitation = InvitationService.by_handoff(
                session,
                capability=handoff,
                pepper=pepper,
                for_update=True,
            )
        elif token is not None:
            # Kept for trusted service-level callers and migration tests. The
            # public accept route deliberately never accepts a raw token.
            invitation = InvitationService.by_token(
                session, token=token, pepper=pepper, for_update=True
            )
        else:
            raise NotFoundError("Invitation is unavailable")
        InvitationService.require_pending(invitation)
        if user.status != UserStatus.ACTIVE or user.email_verified_at is None:
            raise DomainError(
                "Invitation cannot be accepted by this account",
                "invitation_account_unavailable",
                403,
            )
        try:
            user_email = normalize_email_address(user.email)
            invitation_email = normalize_email_address(invitation.email)
        except JwtAuthenticationError as exc:
            raise DomainError(
                "Invitation is unavailable", "invitation_invalid", 404
            ) from exc
        if user_email != invitation_email:
            raise DomainError(
                "Invitation belongs to another account",
                "invitation_email_mismatch",
                403,
            )
        company = session.scalar(
            select(Company)
            .where(Company.id == invitation.company_id)
            .with_for_update()
        )
        if company is None or company.status != CompanyStatus.ACTIVE:
            raise DomainError(
                "Invitation company is unavailable",
                "invitation_company_unavailable",
                409,
            )
        membership = session.scalar(
            select(CompanyMembership)
            .where(
                CompanyMembership.company_id == company.id,
                CompanyMembership.user_id == user.id,
            )
            .with_for_update()
        )
        if membership is None:
            membership = CompanyMembership(
                company_id=company.id,
                user_id=user.id,
                status=MembershipStatus.ACTIVE,
            )
            session.add(membership)
            session.flush()
            role = AccessLifecycleService.system_role(
                session,
                company_id=company.id,
                system_key=invitation.primary_role,
            )
            session.add(MembershipRole(membership_id=membership.id, role_id=role.id))
        elif membership.status != MembershipStatus.ACTIVE:
            owner_assignment = session.scalar(
                select(MembershipRole.membership_id)
                .join(Role, Role.id == MembershipRole.role_id)
                .where(
                    MembershipRole.membership_id == membership.id,
                    Role.company_id == company.id,
                    Role.system_key == "owner",
                )
                .limit(1)
            )
            if membership.status != MembershipStatus.DISABLED or owner_assignment is None:
                raise DomainError(
                    "Invitation conflicts with the existing membership",
                    "invitation_membership_conflict",
                    409,
                )
            membership.status = MembershipStatus.ACTIVE
        invitation.status = CompanyInvitationStatus.ACCEPTED
        invitation.accepted_by_user_id = user.id
        invitation.accepted_at = utcnow()
        invitation.revoked_at = None
        session.flush()
        AuditService.append(
            session,
            actor_user_id=user.id,
            action="company.invitation.accept",
            target_type="company_invitation",
            target_id=invitation.id,
            before_summary={"status": CompanyInvitationStatus.PENDING.value},
            after_summary={
                "status": invitation.status.value,
                "company_id": company.id,
                "membership_id": membership.id,
            },
            request_id=request_id,
        )
        append_security_event(
            session,
            event_type="account.invitation.accepted",
            user_id=user.id,
            auth_session_id=actor_session_id,
            ip_hash=ip_hash,
            user_agent=user_agent,
            invitation_id=invitation.id,
            company_id=company.id,
            request_id=request_id,
        )
        return membership
