from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hmac
from typing import Annotated, Callable

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .auth import (
    JwtAuthenticationError,
    JwtPrincipal,
    extract_bearer_token,
    verify_hs256_jwt,
)
from .config import (
    PHISHING_RESISTANT_PLATFORM_ADMIN_AMR,
    runtime_settings_are_protected,
)
from .models import (
    Company,
    CompanyMembership,
    CompanyStatus,
    ExternalIdentity,
    MembershipStatus,
    PlatformAdminActivity,
    User,
    UserStatus,
)
from .services.authentication import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    CookieSessionPrincipal,
    SESSION_COOKIE_NAME,
    SessionService,
)
from .services.permissions import PermissionService
from .services.platform_admin_access import PlatformAdminAccessService
from .platform_owner_identity import is_platform_owner_identity


def get_db(request: Request) -> Generator[Session, None, None]:
    session = request.app.state.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@dataclass(frozen=True)
class TenantContext:
    company_id: str
    user_id: str
    membership_id: str
    auth_source: str = "development"
    external_issuer: str | None = None
    external_subject: str | None = None
    auth_session_id: str | None = None
    cookie_principal: CookieSessionPrincipal | None = None
    authentication_time: float | None = None
    authentication_methods: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlatformAdminContext:
    user_id: str
    is_platform_owner: bool = False


def _record_platform_admin_activity(session: Session, user: User) -> None:
    """Coalesce successful administrator activity on the authenticated user."""

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=30)
    values = {"user_id": user.id, "last_active_at": now}
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(PlatformAdminActivity).values(**values)
    elif dialect == "sqlite":
        statement = sqlite_insert(PlatformAdminActivity).values(**values)
    else:  # pragma: no cover - supported deployments use PostgreSQL/SQLite
        activity = session.get(PlatformAdminActivity, user.id)
        if activity is None:
            session.add(PlatformAdminActivity(**values))
        elif activity.last_active_at is None or activity.last_active_at <= cutoff:
            activity.last_active_at = now
        return
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[PlatformAdminActivity.user_id],
            set_={"last_active_at": now},
            where=(
                PlatformAdminActivity.last_active_at.is_(None)
                | (PlatformAdminActivity.last_active_at <= cutoff)
            ),
        )
    )


@dataclass(frozen=True)
class UserContext:
    user_id: str
    auth_source: str = "development"
    external_issuer: str | None = None
    external_subject: str | None = None
    auth_session_id: str | None = None
    cookie_principal: CookieSessionPrincipal | None = None
    authentication_time: float | None = None
    authentication_methods: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    user: User
    source: str
    issuer: str | None
    subject: str
    company_id: str | None
    authentication_time: float | None
    authentication_methods: tuple[str, ...]
    platform_admin_claim: bool
    cookie_principal: CookieSessionPrincipal | None = None

    @property
    def user_id(self) -> str:
        return self.user.id

    @property
    def auth_session_id(self) -> str | None:
        return (
            self.cookie_principal.auth_session.id
            if self.cookie_principal is not None
            else None
        )


def _production_principal(
    request: Request, authorization: str | None
) -> JwtPrincipal:
    settings = request.app.state.settings
    if not getattr(settings, "auth_legacy_bearer_enabled", False):
        raise HTTPException(
            status_code=401,
            detail="Legacy bearer authentication is disabled",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        token = extract_bearer_token(authorization)
        return verify_hs256_jwt(
            token,
            secret=settings.jwt_signing_secret,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )
    except JwtAuthenticationError:
        raise HTTPException(
            status_code=401,
            detail="Bearer token is missing or invalid",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


def _authenticated_principal(
    request: Request,
    session: Session,
    authorization: str | None,
) -> AuthenticatedPrincipal:
    settings = request.app.state.settings
    raw_cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_cookie:
        cookie_principal = SessionService.resolve(
            session,
            raw_token=raw_cookie,
            pepper=settings.jwt_signing_secret,
            idle_ttl_seconds=settings.auth_session_idle_ttl_seconds,
        )
        if cookie_principal is None:
            raise HTTPException(status_code=401, detail="Session is missing or invalid")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            SessionService.require_csrf(
                cookie_principal,
                csrf_cookie=request.cookies.get(CSRF_COOKIE_NAME),
                csrf_header=request.headers.get(CSRF_HEADER_NAME),
                origin=request.headers.get("origin"),
                expected_origin=settings.frontend_origin,
                pepper=settings.jwt_signing_secret,
            )
        return AuthenticatedPrincipal(
            user=cookie_principal.user,
            source="cookie",
            issuer=cookie_principal.issuer,
            subject=cookie_principal.subject,
            company_id=None,
            authentication_time=cookie_principal.authentication_time,
            authentication_methods=cookie_principal.authentication_methods,
            platform_admin_claim=cookie_principal.user.is_platform_admin,
            cookie_principal=cookie_principal,
        )
    principal = _production_principal(request, authorization)
    identity = session.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.issuer == principal.issuer,
            ExternalIdentity.subject == principal.user_id,
        )
    )
    user = session.get(User, identity.user_id) if identity is not None else None
    if user is None:
        # Deliberate migration-only compatibility: this fallback is reachable
        # solely while AUTH_LEGACY_BEARER_ENABLED is explicitly true.
        user = session.get(User, principal.user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        raise HTTPException(status_code=401, detail="Authenticated account is unavailable")
    return AuthenticatedPrincipal(
        user=user,
        source="bearer",
        issuer=principal.issuer,
        subject=principal.user_id,
        company_id=principal.company_id,
        authentication_time=principal.authentication_time,
        authentication_methods=principal.authentication_methods,
        platform_admin_claim=principal.platform_admin,
    )


def _owner_local_user_ids(session: Session, settings) -> frozenset[str]:
    subjects = set(settings.platform_owner_user_ids)
    oidc_issuer = getattr(settings, "oidc_issuer", None)
    if not subjects or not oidc_issuer:
        return frozenset()
    return frozenset(
        session.scalars(
            select(ExternalIdentity.user_id).where(
                ExternalIdentity.issuer == oidc_issuer,
                ExternalIdentity.subject.in_(subjects),
            )
        ).all()
    )


def get_tenant_context(
    request: Request,
    company_id: str,
    session: Annotated[Session, Depends(get_db, scope="function")],
    authorization: Annotated[
        str | None, Header(alias="Authorization")
    ] = None,
    x_company_id: Annotated[
        str | None, Header(alias="X-Company-ID")
    ] = None,
    x_user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
) -> TenantContext:
    settings = request.app.state.settings
    authenticated: AuthenticatedPrincipal | None = None
    if request.cookies.get(SESSION_COOKIE_NAME) or authorization is not None:
        authenticated = _authenticated_principal(request, session, authorization)
        x_user_id = authenticated.user_id
        if authenticated.source == "bearer":
            x_company_id = authenticated.company_id
    elif runtime_settings_are_protected(settings):
        raise HTTPException(status_code=401, detail="Session is missing or invalid")
    elif not settings.development_header_auth_enabled:
        raise HTTPException(status_code=401, detail="Development header authentication is disabled")
    if not x_company_id or not x_user_id:
        raise HTTPException(status_code=401, detail="Missing company-user identity")
    if x_company_id != company_id:
        raise HTTPException(status_code=403, detail="请求公司与租户上下文不一致")
    company = session.get(Company, company_id)
    if company is None or company.status != CompanyStatus.ACTIVE:
        raise HTTPException(status_code=404, detail="公司不存在或不可用")
    membership = session.scalar(
        select(CompanyMembership).where(
            CompanyMembership.company_id == company_id,
            CompanyMembership.user_id == x_user_id,
            CompanyMembership.status == MembershipStatus.ACTIVE,
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="用户不属于当前公司")
    return TenantContext(
        company_id=company_id,
        user_id=x_user_id,
        membership_id=membership.id,
        auth_source=authenticated.source if authenticated else "development",
        external_issuer=authenticated.issuer if authenticated else None,
        external_subject=authenticated.subject if authenticated else None,
        auth_session_id=authenticated.auth_session_id if authenticated else None,
        cookie_principal=(authenticated.cookie_principal if authenticated else None),
        authentication_time=(authenticated.authentication_time if authenticated else None),
        authentication_methods=(authenticated.authentication_methods if authenticated else ()),
    )


def require_permission(permission_code: str) -> Callable:
    def dependency(
        context: Annotated[TenantContext, Depends(get_tenant_context)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ) -> TenantContext:
        PermissionService.require(
            session,
            membership_id=context.membership_id,
            permission_code=permission_code,
        )
        return context

    return dependency


def require_internal_service(
    request: Request,
    x_internal_service_token: Annotated[
        str | None, Header(alias="X-Internal-Service-Token")
    ] = None,
) -> None:
    expected = request.app.state.settings.internal_service_token
    if not expected:
        raise HTTPException(status_code=503, detail="内部服务令牌尚未配置")
    if not x_internal_service_token or not hmac.compare_digest(
        x_internal_service_token, expected
    ):
        raise HTTPException(status_code=401, detail="内部服务认证失败")


def require_download_edge_completion_service(
    request: Request,
    x_internal_service_token: Annotated[
        str | None, Header(alias="X-Internal-Service-Token")
    ] = None,
) -> None:
    settings = request.app.state.settings
    expected = settings.download_edge_completion_service_token
    if not expected:
        raise HTTPException(status_code=503, detail="下载边缘完成服务令牌尚未配置")
    if not x_internal_service_token or not hmac.compare_digest(
        x_internal_service_token, expected
    ):
        raise HTTPException(status_code=401, detail="下载边缘完成服务认证失败")


def require_bootstrap_token(
    request: Request,
    x_bootstrap_token: Annotated[
        str | None, Header(alias="X-Bootstrap-Token")
    ] = None,
) -> None:
    settings = request.app.state.settings
    if runtime_settings_are_protected(settings) or not settings.enable_bootstrap:
        raise HTTPException(status_code=404, detail="Not found")
    expected = settings.bootstrap_token
    if (
        not expected
        or not x_bootstrap_token
        or not hmac.compare_digest(x_bootstrap_token, expected)
    ):
        raise HTTPException(
            status_code=401,
            detail="Bootstrap authentication failed",
        )


def require_platform_admin(
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    authorization: Annotated[
        str | None, Header(alias="Authorization")
    ] = None,
    x_platform_admin_user_id: Annotated[
        str | None, Header(alias="X-Platform-Admin-User-ID")
    ] = None,
) -> PlatformAdminContext:
    settings = request.app.state.settings
    authenticated: AuthenticatedPrincipal | None = None
    if request.cookies.get(SESSION_COOKIE_NAME) or authorization is not None:
        authenticated = _authenticated_principal(request, session, authorization)
        if not authenticated.user.is_platform_admin:
            raise HTTPException(status_code=403, detail="不是平台管理员")
        if runtime_settings_are_protected(settings):
            accepted_methods = {
                method.casefold() for method in settings.platform_admin_required_amr
            }.intersection(PHISHING_RESISTANT_PLATFORM_ADMIN_AMR)
            if not accepted_methods.intersection(
                method.casefold() for method in authenticated.authentication_methods
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Platform administrator strong authentication is required",
                )
            if authenticated.authentication_time is None:
                raise HTTPException(
                    status_code=403,
                    detail="Platform administrator authentication time is required",
                )
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                authentication_age = (
                    datetime.now(timezone.utc).timestamp()
                    - authenticated.authentication_time
                )
                if (
                    authentication_age < -30
                    or authentication_age
                    > settings.platform_admin_step_up_max_age_seconds
                ):
                    raise HTTPException(
                        status_code=403,
                        detail="Recent platform administrator authentication is required",
                        headers={"X-Auth-Required": "step-up"},
                    )
        x_platform_admin_user_id = authenticated.user_id
    elif runtime_settings_are_protected(settings):
        raise HTTPException(status_code=401, detail="Session is missing or invalid")
    elif not settings.development_header_auth_enabled:
        raise HTTPException(
            status_code=401,
            detail="Development header authentication is disabled",
        )
    if not x_platform_admin_user_id:
        raise HTTPException(status_code=401, detail="缺少平台管理员身份")
    user = session.get(User, x_platform_admin_user_id)
    if user is None or user.status != UserStatus.ACTIVE or not user.is_platform_admin:
        raise HTTPException(status_code=403, detail="不是平台管理员")
    owner_user_ids = set(_owner_local_user_ids(session, settings))
    if (
        not runtime_settings_are_protected(settings)
        and settings.development_header_auth_enabled
        and settings.enable_bootstrap
        and not owner_user_ids
    ):
        development_owner_ids = request.app.state.development_platform_owner_user_ids
        if not development_owner_ids:
            first_admin_id = session.scalar(
                select(User.id)
                .where(User.is_platform_admin.is_(True))
                .order_by(User.created_at.asc(), User.id.asc())
                .limit(1)
            )
            if first_admin_id:
                development_owner_ids.add(first_admin_id)
        owner_user_ids.update(development_owner_ids)
    is_platform_owner = (
        authenticated is not None
        and is_platform_owner_identity(
            issuer=authenticated.issuer,
            subject=authenticated.subject,
            configured_issuer=settings.oidc_issuer,
            configured_subjects=settings.platform_owner_user_ids,
        )
    ) or (authenticated is None and user.id in owner_user_ids)
    if not is_platform_owner:
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        PlatformAdminAccessService.authorize_request(
            session,
            user_id=user.id,
            platform_owner_user_ids=frozenset(owner_user_ids),
            method=request.method,
            route_path=route_path,
        )
    # This is activity evidence, not authentication evidence. Write it only
    # after the complete server-side authorization boundary succeeds and
    # coalesce bursts to avoid turning read-heavy consoles into write storms.
    _record_platform_admin_activity(session, user)
    return PlatformAdminContext(
        user_id=user.id,
        is_platform_owner=is_platform_owner,
    )


def get_user_context(
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    authorization: Annotated[
        str | None, Header(alias="Authorization")
    ] = None,
    x_user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
) -> UserContext:
    """Authenticate a natural person without requiring a company context.

    A real bearer token is accepted in every environment.  Development header
    identity remains available only when the same explicit test/demo switch as
    company routes is enabled; production can never trust that header.
    """

    settings = request.app.state.settings
    authenticated: AuthenticatedPrincipal | None = None
    if request.cookies.get(SESSION_COOKIE_NAME) or authorization is not None:
        authenticated = _authenticated_principal(request, session, authorization)
        user_id = authenticated.user_id
    elif runtime_settings_are_protected(settings):
        raise HTTPException(
            status_code=401,
            detail="Session is missing or invalid",
        )
    elif settings.development_header_auth_enabled:
        user_id = x_user_id
    else:
        raise HTTPException(
            status_code=401,
            detail="Development header authentication is disabled",
        )
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing user identity")
    user = session.get(User, user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        raise HTTPException(status_code=401, detail="Authenticated user does not exist")
    return UserContext(
        user_id=user_id,
        auth_source=authenticated.source if authenticated else "development",
        external_issuer=authenticated.issuer if authenticated else None,
        external_subject=authenticated.subject if authenticated else None,
        auth_session_id=authenticated.auth_session_id if authenticated else None,
        cookie_principal=authenticated.cookie_principal if authenticated else None,
        authentication_time=authenticated.authentication_time if authenticated else None,
        authentication_methods=authenticated.authentication_methods if authenticated else (),
    )
