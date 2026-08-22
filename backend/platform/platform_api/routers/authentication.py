from __future__ import annotations

from datetime import datetime, timezone
import hmac
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth_schemas import (
    AccountDeactivateRequest,
    AccountResponse,
    AccountSessionPageResponse,
    AccountUpdateRequest,
    AuthSessionResponse,
    CompanyInvitationCreateRequest,
    CompanyInvitationResponse,
    CompanyInvitationPageResponse,
    CompanyOwnerTransferRequest,
    CompanyOwnerTransferResponse,
    InvitationAcceptResponse,
    InvitationAcceptRequest,
    InvitationPreviewResponse,
    InvitationTokenRequest,
    LogoutRequest,
    PlatformUserStatusResponse,
    PlatformUserPageResponse,
    PlatformUserStatusUpdateRequest,
    OwnerOnboardingInvitationResponse,
    OwnerOnboardingReissueRequest,
)
from ..dependencies import (
    PlatformAdminContext,
    TenantContext,
    UserContext,
    _authenticated_principal,
    _owner_local_user_ids,
    get_db,
    get_tenant_context,
    get_user_context,
    require_platform_admin,
    require_permission,
)
from ..models import AuditOutcome, AuthSession, Company, User, UserStatus
from ..services.authentication import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    INVITATION_HANDOFF_COOKIE_NAME,
    INVITATION_HANDOFF_TTL_SECONDS,
    OIDC_STATE_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    AccountService,
    InvitationService,
    OidcService,
    OwnerTransferService,
    SessionService,
    append_security_event,
    request_ip_hash,
)
from ..services.errors import DomainError
from ..services.personal import PersonalWorkspaceService


router = APIRouter(tags=["authentication"])
DatabaseSession = Annotated[Session, Depends(get_db, scope="function")]


def _client_ip(request: Request) -> str:
    # Proxy header trust belongs at the ingress/ASGI server boundary. Accepting
    # an arbitrary X-Forwarded-For value here would let callers evade rate limits.
    return request.client.host if request.client is not None else "unknown"


def _user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")[:512]


def _ip_digest(request: Request) -> str:
    return request_ip_hash(
        _client_ip(request), pepper=request.app.state.settings.jwt_signing_secret
    )


def _set_invitation_handoff_cookie(
    response: Response,
    *,
    invitation,
    pepper: str | None,
) -> None:
    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    remaining = max(
        1,
        min(
            INVITATION_HANDOFF_TTL_SECONDS,
            int((expires_at.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()),
        ),
    )
    response.set_cookie(
        INVITATION_HANDOFF_COOKIE_NAME,
        InvitationService.handoff_capability(invitation, pepper=pepper),
        max_age=remaining,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/api/v1/invitations",
    )


def _set_session_cookies(
    response: Response,
    *,
    raw_session: str,
    raw_csrf: str,
    max_age: int,
) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        raw_session,
        max_age=max_age,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        raw_csrf,
        max_age=max_age,
        secure=True,
        httponly=False,
        samesite="lax",
        path="/",
    )


def _clear_session_cookies(
    response: Response, *, preserve_invitation: bool = False
) -> None:
    for name, http_only in (
        (SESSION_COOKIE_NAME, True),
        (CSRF_COOKIE_NAME, False),
    ):
        response.set_cookie(
            name,
            "",
            max_age=0,
            expires=0,
            secure=True,
            httponly=http_only,
            samesite="lax",
            path="/",
        )
    if not preserve_invitation:
        response.set_cookie(
            INVITATION_HANDOFF_COOKIE_NAME,
            "",
            max_age=0,
            expires=0,
            secure=True,
            httponly=True,
            samesite="lax",
            path="/api/v1/invitations",
        )


def _auth_user(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "status": user.status,
        "email_verified_at": user.email_verified_at,
        "auth_version": user.auth_version,
    }


def _require_cookie_csrf(
    request: Request,
    context: UserContext | TenantContext,
    csrf_header: str | None,
) -> None:
    if context.auth_source != "cookie":
        return
    principal = context.cookie_principal
    if principal is None:  # pragma: no cover - dependency invariant
        raise DomainError("Session is unavailable", "session_invalid", 401)
    SessionService.require_csrf(
        principal,
        csrf_cookie=request.cookies.get(CSRF_COOKIE_NAME),
        csrf_header=csrf_header,
        origin=request.headers.get("origin"),
        expected_origin=request.app.state.settings.frontend_origin,
        pepper=request.app.state.settings.jwt_signing_secret,
    )


def _require_recent_account_auth(
    request: Request, context: UserContext | TenantContext
) -> None:
    if context.auth_source == "development":
        return
    auth_time = context.authentication_time
    if auth_time is None:
        raise HTTPException(
            status_code=403,
            detail="Recent authentication is required",
            headers={"X-Auth-Required": "step-up"},
        )
    age = datetime.now(timezone.utc).timestamp() - auth_time
    if age < -30 or age > request.app.state.settings.auth_account_step_up_max_age_seconds:
        raise HTTPException(
            status_code=403,
            detail="Recent authentication is required",
            headers={"X-Auth-Required": "step-up"},
        )


@router.get("/api/v1/auth/login")
def begin_oidc_login(
    request: Request,
    session: DatabaseSession,
    return_to: Annotated[str | None, Query(max_length=2048)] = None,
    prompt: Annotated[
        Literal["login", "select_account", "consent", "none", "step_up"] | None,
        Query(),
    ] = None,
) -> RedirectResponse:
    location, state = OidcService.start_login(
        session,
        settings=request.app.state.settings,
        return_to=return_to,
        prompt=prompt,
        ip_address=_client_ip(request),
    )
    response = RedirectResponse(location, status_code=302)
    response.set_cookie(
        OIDC_STATE_COOKIE_NAME,
        state,
        max_age=request.app.state.settings.oidc_login_transaction_ttl_seconds,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/api/v1/auth/callback")
def oidc_callback(
    request: Request,
    session: DatabaseSession,
    state: Annotated[str, Query(min_length=1, max_length=512)],
    code: Annotated[str | None, Query(min_length=1, max_length=4096)] = None,
    error: Annotated[str | None, Query(max_length=200)] = None,
) -> RedirectResponse:
    settings = request.app.state.settings
    browser_state = request.cookies.get(OIDC_STATE_COOKIE_NAME)
    if (
        not browser_state
        or len(browser_state) > 512
        or not hmac.compare_digest(browser_state, state)
    ):
        raise DomainError("Sign-in callback is invalid", "oidc_callback_invalid", 400)
    transaction, exchange_context = OidcService.consume_transaction(
        session,
        settings=settings,
        state=state,
        ip_address=_client_ip(request),
    )
    # State is one-shot even if the provider is unavailable or returns an
    # invalid token. It must be durable before any outbound network call.
    session.commit()
    request_id = request.state.request_id
    ip_hash = _ip_digest(request)
    user_agent = _user_agent(request)
    return_to = transaction.return_to
    if error is not None or code is None:
        append_security_event(
            session,
            event_type="auth.login.failed",
            user_id=None,
            outcome=AuditOutcome.FAILED,
            request_id=request_id,
            issuer=settings.oidc_issuer,
            ip_hash=ip_hash,
            user_agent=user_agent,
            reason="provider_error",
        )
        session.commit()
        raise DomainError("Sign-in callback is invalid", "oidc_login_failed", 401)

    client = getattr(request.app.state, "oidc_http_client", None)
    try:
        if client is None:
            with httpx.Client(trust_env=False, follow_redirects=False) as owned_client:
                claims = OidcService.exchange_and_verify(
                    settings=settings,
                    transaction=exchange_context,
                    code=code,
                    http_client=owned_client,
                )
        else:
            claims = OidcService.exchange_and_verify(
                settings=settings,
                transaction=exchange_context,
                code=code,
                http_client=client,
            )
    except DomainError as exc:
        append_security_event(
            session,
            event_type="auth.login.failed",
            user_id=None,
            outcome=AuditOutcome.FAILED,
            request_id=request_id,
            issuer=settings.oidc_issuer,
            ip_hash=ip_hash,
            user_agent=user_agent,
            reason=exc.code,
        )
        session.commit()
        raise
    except Exception:
        # The state was already durably consumed and its raw nonce/verifier
        # scrubbed before the outbound call. Keep that fixed tombstone so an
        # unexpected client failure still contributes to the IP rate window.
        raise

    # The provider exchange no longer needs the nonce or verifier. Their fixed
    # tombstones were committed before network I/O and remain until bounded
    # expiry cleanup so successful callbacks also count toward the IP window.

    try:
        user, identity = OidcService.bind_identity(
            session,
            claims=claims,
            settings=settings,
            request_id=request_id,
            ip_hash=ip_hash,
            user_agent=user_agent,
        )
    except DomainError:
        # bind_identity constructs only whitelisted, token-free failure evidence.
        session.commit()
        raise
    except IntegrityError as exc:
        session.rollback()
        append_security_event(
            session,
            event_type="auth.login.failed",
            user_id=None,
            outcome=AuditOutcome.FAILED,
            request_id=request_id,
            issuer=settings.oidc_issuer,
            ip_hash=ip_hash,
            user_agent=user_agent,
            reason="identity_concurrency_conflict",
        )
        session.commit()
        raise DomainError(
            "Sign-in is unavailable", "oidc_login_failed", 409
        ) from exc
    auth_session, raw_session, raw_csrf = SessionService.create(
        session,
        user=user,
        identity=identity,
        claims=claims,
        ttl_seconds=settings.auth_session_ttl_seconds,
        user_agent=user_agent,
        pepper=settings.jwt_signing_secret,
        ip_hash=ip_hash,
        request_id=request_id,
    )
    session.commit()
    destination = (settings.frontend_origin or "").rstrip("/") + return_to
    response = RedirectResponse(destination, status_code=303)
    _set_session_cookies(
        response,
        raw_session=raw_session,
        raw_csrf=raw_csrf,
        max_age=settings.auth_session_ttl_seconds,
    )
    return response


@router.get("/api/v1/auth/session", response_model=AuthSessionResponse)
def auth_session_state(
    request: Request,
    response: Response,
    session: DatabaseSession,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> dict:
    if not request.cookies.get(SESSION_COOKIE_NAME) and authorization is None:
        return {"authenticated": False}
    try:
        principal = _authenticated_principal(request, session, authorization)
    except Exception as exc:
        # The session-discovery endpoint is intentionally enumeration-safe and
        # anonymous: malformed/revoked credentials collapse to the same shape.
        if not isinstance(exc, HTTPException):
            raise
        _clear_session_cookies(response)
        return {"authenticated": False}
    surfaces = PersonalWorkspaceService.surfaces(session, user_id=principal.user_id)
    raw_csrf = None
    if principal.cookie_principal is not None:
        candidate = request.cookies.get(CSRF_COOKIE_NAME)
        if SessionService.csrf_matches(
            principal.cookie_principal,
            raw_csrf=candidate,
            pepper=request.app.state.settings.jwt_signing_secret,
        ):
            raw_csrf = candidate
        else:
            raw_csrf = SessionService.rotate_csrf(
                session,
                principal=principal.cookie_principal,
                pepper=request.app.state.settings.jwt_signing_secret,
            )
            response.set_cookie(
                CSRF_COOKIE_NAME,
                raw_csrf,
                max_age=request.app.state.settings.auth_session_ttl_seconds,
                secure=True,
                httponly=False,
                samesite="lax",
                path="/",
            )
    return {
        "authenticated": True,
        "csrf_token": raw_csrf,
        "user": _auth_user(principal.user),
        "account_management_url": request.app.state.settings.account_management_url,
        "session_expires_at": (
            principal.cookie_principal.auth_session.expires_at
            if principal.cookie_principal is not None
            else None
        ),
        "personal": surfaces["personal"],
        "companies": surfaces["companies"],
        "platform_admin": surfaces["platform_admin"],
    }


@router.post("/api/v1/auth/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: DatabaseSession,
    body: LogoutRequest | None = None,
    csrf_header: Annotated[str | None, Header(alias=CSRF_HEADER_NAME)] = None,
) -> None:
    _require_cookie_csrf(request, context, csrf_header)
    if context.cookie_principal is not None:
        SessionService.revoke(
            session,
            principal=context.cookie_principal,
            target_session_id=context.cookie_principal.auth_session.id,
            reason="logout",
            ip_hash=_ip_digest(request),
            user_agent=_user_agent(request),
            request_id=request.state.request_id,
        )
    _clear_session_cookies(
        response,
        preserve_invitation=bool(body and body.preserve_invitation),
    )


@router.get("/api/v1/account", response_model=AccountResponse)
def get_account(
    context: Annotated[UserContext, Depends(get_user_context)],
    session: DatabaseSession,
) -> dict:
    user = session.get(User, context.user_id)
    assert user is not None
    return {**_auth_user(user), "last_login_at": user.last_login_at, "updated_at": user.updated_at}


@router.patch("/api/v1/account", response_model=AccountResponse)
def update_account(
    body: AccountUpdateRequest,
    request: Request,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: DatabaseSession,
    csrf_header: Annotated[str | None, Header(alias=CSRF_HEADER_NAME)] = None,
) -> dict:
    _require_cookie_csrf(request, context, csrf_header)
    user = AccountService.update_profile(
        session,
        user_id=context.user_id,
        display_name=body.display_name,
        expected_auth_version=body.expected_auth_version,
        expected_updated_at=body.expected_updated_at,
        auth_session_id=context.auth_session_id,
        ip_hash=_ip_digest(request),
        user_agent=_user_agent(request),
        request_id=request.state.request_id,
    )
    return {**_auth_user(user), "last_login_at": user.last_login_at, "updated_at": user.updated_at}


def _list_account_sessions(
    context: UserContext,
    session: Session,
    *,
    page: int,
    page_size: int,
) -> tuple[int, list[dict]]:
    current_id = context.auth_session_id
    total = int(
        session.scalar(
            select(func.count(AuthSession.id)).where(
                AuthSession.user_id == context.user_id
            )
        )
        or 0
    )
    items = [
        {
            "id": row.id,
            "current": row.id == current_id,
            "created_at": row.created_at,
            "last_seen_at": row.last_seen_at,
            "expires_at": row.expires_at,
            "revoked_at": row.revoked_at,
            "user_agent": row.user_agent,
            "amr": list(row.amr),
            "auth_time": row.auth_time,
        }
        for row in session.scalars(
            select(AuthSession)
            .where(AuthSession.user_id == context.user_id)
            .order_by(AuthSession.created_at.desc(), AuthSession.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    ]
    return total, items


@router.get("/api/v1/account/sessions", response_model=AccountSessionPageResponse)
@router.get("/api/v1/account/security/sessions", response_model=AccountSessionPageResponse)
def list_account_sessions(
    context: Annotated[UserContext, Depends(get_user_context)],
    session: DatabaseSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    total, items = _list_account_sessions(
        context, session, page=page, page_size=page_size
    )
    return {"items": items, "page": page, "page_size": page_size, "total": total}


@router.delete("/api/v1/account/sessions/{session_id}", status_code=204)
def revoke_account_session(
    session_id: str,
    request: Request,
    response: Response,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: DatabaseSession,
    csrf_header: Annotated[str | None, Header(alias=CSRF_HEADER_NAME)] = None,
) -> None:
    _require_cookie_csrf(request, context, csrf_header)
    if context.cookie_principal is None:
        raise DomainError("Session revocation requires a BFF session", "session_required", 409)
    SessionService.revoke(
        session,
        principal=context.cookie_principal,
        target_session_id=session_id,
        reason="user_revoked",
        ip_hash=_ip_digest(request),
        user_agent=_user_agent(request),
        request_id=request.state.request_id,
    )
    if session_id == context.auth_session_id:
        _clear_session_cookies(response)


@router.post("/api/v1/account/sessions/revoke-all", status_code=204)
def revoke_all_account_sessions(
    request: Request,
    response: Response,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: DatabaseSession,
    csrf_header: Annotated[str | None, Header(alias=CSRF_HEADER_NAME)] = None,
) -> None:
    _require_cookie_csrf(request, context, csrf_header)
    _require_recent_account_auth(request, context)
    SessionService.revoke_all(
        session,
        user_id=context.user_id,
        actor_session_id=context.auth_session_id,
        reason="user_revoke_all",
        ip_hash=_ip_digest(request),
        user_agent=_user_agent(request),
        request_id=request.state.request_id,
    )
    _clear_session_cookies(response)


@router.post("/api/v1/account/deactivate", status_code=204)
def deactivate_account(
    body: AccountDeactivateRequest,
    request: Request,
    response: Response,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: DatabaseSession,
    csrf_header: Annotated[str | None, Header(alias=CSRF_HEADER_NAME)] = None,
) -> None:
    _require_cookie_csrf(request, context, csrf_header)
    _require_recent_account_auth(request, context)
    AccountService.deactivate(
        session,
        user_id=context.user_id,
        external_subject=context.external_subject or "",
        owner_subjects=set(request.app.state.settings.platform_owner_user_ids),
        expected_auth_version=body.expected_auth_version,
        auth_session_id=context.auth_session_id,
        ip_hash=_ip_digest(request),
        user_agent=_user_agent(request),
        request_id=request.state.request_id,
    )
    _clear_session_cookies(response)


@router.post(
    "/api/v1/companies/{company_id}/owner-transfer",
    response_model=CompanyOwnerTransferResponse,
)
def transfer_company_owner(
    company_id: str,
    body: CompanyOwnerTransferRequest,
    request: Request,
    context: Annotated[TenantContext, Depends(get_tenant_context)],
    session: DatabaseSession,
) -> dict:
    _require_recent_account_auth(request, context)
    former, owner = OwnerTransferService.transfer(
        session,
        company_id=company_id,
        actor_user_id=context.user_id,
        actor_membership_id=context.membership_id,
        target_membership_id=body.target_membership_id,
        expected_current_owner_membership_id=body.expected_current_owner_membership_id,
        expected_current_owner_user_id=body.expected_current_owner_user_id,
        former_owner_primary_role=body.former_owner_primary_role,
        request_id=request.state.request_id,
    )
    return {
        "company_id": company_id,
        "owner_membership_id": owner.id,
        "owner_user_id": owner.user_id,
        "former_owner_membership_id": former.id,
        "former_owner_user_id": former.user_id,
        "former_owner_primary_role": body.former_owner_primary_role,
    }


def _require_owner(admin: PlatformAdminContext) -> None:
    if not admin.is_platform_owner:
        raise DomainError("Platform owner access is required", "platform_owner_required", 403)


@router.get(
    "/api/v1/platform-admin/users",
    response_model=PlatformUserPageResponse,
)
def list_global_users(
    admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: DatabaseSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    _require_owner(admin)
    total = int(session.scalar(select(func.count(User.id))) or 0)
    items = [
        {
            **_auth_user(user),
            "last_login_at": user.last_login_at,
            "deactivated_at": user.deactivated_at,
            "updated_at": user.updated_at,
        }
        for user in session.scalars(
            select(User)
            .order_by(User.created_at.desc(), User.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    ]
    return {"items": items, "page": page, "page_size": page_size, "total": total}


@router.patch(
    "/api/v1/platform-admin/users/{user_id}/status",
    response_model=PlatformUserStatusResponse,
)
def update_global_user_status(
    user_id: str,
    body: PlatformUserStatusUpdateRequest,
    request: Request,
    admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: DatabaseSession,
) -> dict:
    _require_owner(admin)
    settings = request.app.state.settings
    user = AccountService.set_global_status(
        session,
        actor_user_id=admin.user_id,
        target_user_id=user_id,
        expected_status=UserStatus(body.expected_status),
        expected_auth_version=body.expected_auth_version,
        target_status=UserStatus(body.target_status),
        platform_owner_user_ids=set(_owner_local_user_ids(session, settings)),
        request_id=request.state.request_id,
        ip_hash=_ip_digest(request),
        user_agent=_user_agent(request),
    )
    return {
        **_auth_user(user),
        "last_login_at": user.last_login_at,
        "deactivated_at": user.deactivated_at,
        "updated_at": user.updated_at,
    }


@router.post(
    "/api/v1/platform-admin/companies/{company_id}/owner-invitation/reissue",
    response_model=OwnerOnboardingInvitationResponse,
)
def reissue_owner_onboarding_invitation(
    company_id: str,
    body: OwnerOnboardingReissueRequest,
    request: Request,
    admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: DatabaseSession,
) -> dict:
    _require_owner(admin)
    settings = request.app.state.settings
    arguments = dict(
        company_id=company_id,
        expected_owner_membership_id=body.expected_owner_membership_id,
        expected_owner_user_id=body.expected_owner_user_id,
        actor_user_id=admin.user_id,
        expires_in_seconds=settings.invitation_ttl_seconds,
        pepper=settings.jwt_signing_secret,
        request_id=request.state.request_id,
        replacement_email=(
            str(body.replacement_email) if body.replacement_email is not None else None
        ),
        replacement_display_name=body.replacement_display_name,
    )
    try:
        invitation, token, membership = InvitationService.reissue_owner_onboarding(
            session, **arguments
        )
    except IntegrityError:
        session.rollback()
        invitation, token, membership = InvitationService.reissue_owner_onboarding(
            session, **arguments
        )
    payload = InvitationService.response(
        invitation,
        acceptance_token=token,
        frontend_origin=settings.frontend_origin,
        effective_primary_role="owner",
    )
    invitation_url = payload["invitation_url"]
    assert isinstance(invitation_url, str)
    return {
        "company_id": company_id,
        "owner_membership_id": membership.id,
        "owner_user_id": membership.user_id,
        "invitation_id": invitation.id,
        "invitation_url": invitation_url,
        "expires_at": invitation.expires_at,
    }


@router.post("/api/v1/invitations/preview", response_model=InvitationPreviewResponse)
def preview_invitation(
    body: InvitationTokenRequest,
    request: Request,
    response: Response,
    session: DatabaseSession,
) -> dict:
    pepper = request.app.state.settings.jwt_signing_secret
    try:
        if body.token is not None:
            invitation, company = InvitationService.preview(
                session,
                token=body.token,
                pepper=pepper,
            )
        else:
            invitation = InvitationService.by_handoff(
                session,
                capability=request.cookies.get(
                    INVITATION_HANDOFF_COOKIE_NAME, ""
                ),
                pepper=pepper,
                for_update=True,
            )
            company = session.get(Company, invitation.company_id)
            if company is None:
                raise DomainError(
                    "Invitation is unavailable", "invitation_invalid", 404
                )
        InvitationService.require_pending(invitation)
    except DomainError as exc:
        request.state.clear_invitation_handoff = True
        if exc.code == "not_found":
            raise DomainError(
                "Invitation is unavailable", "invitation_invalid", 404
            ) from exc
        raise
    _set_invitation_handoff_cookie(
        response, invitation=invitation, pepper=pepper
    )
    return {
        "company_name": company.name,
        "email": invitation.email,
        "display_name": invitation.display_name,
        "primary_role": InvitationService.effective_primary_role(
            session, invitation=invitation
        ),
        "status": InvitationService._effective_status(invitation),
        "expires_at": invitation.expires_at,
    }


@router.post("/api/v1/invitations/accept", response_model=InvitationAcceptResponse)
def accept_invitation(
    body: InvitationAcceptRequest,
    request: Request,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: DatabaseSession,
    csrf_header: Annotated[str | None, Header(alias=CSRF_HEADER_NAME)] = None,
) -> dict:
    _require_cookie_csrf(request, context, csrf_header)
    user = session.get(User, context.user_id)
    assert user is not None
    try:
        membership = InvitationService.accept(
            session,
            token=None,
            handoff=request.cookies.get(INVITATION_HANDOFF_COOKIE_NAME),
            pepper=request.app.state.settings.jwt_signing_secret,
            user=user,
            actor_session_id=context.auth_session_id,
            request_id=request.state.request_id,
            ip_hash=_ip_digest(request),
            user_agent=_user_agent(request),
        )
    except DomainError as exc:
        if exc.code in {
            "not_found",
            "invitation_invalid",
            "invitation_expired",
            "invitation_revoked",
            "invitation_used",
            "invitation_company_unavailable",
            "invitation_membership_conflict",
        }:
            request.state.clear_invitation_handoff = True
        if exc.code == "not_found":
            raise DomainError(
                "Invitation is unavailable", "invitation_invalid", 404
            ) from exc
        raise
    request.state.clear_invitation_handoff = True
    return {"company_id": membership.company_id, "membership_id": membership.id, "user_id": user.id}


@router.get(
    "/api/v1/companies/{company_id}/invitations",
    response_model=CompanyInvitationPageResponse,
)
def list_company_invitations(
    company_id: str,
    _: Annotated[TenantContext, Depends(require_permission("users.manage"))],
    session: DatabaseSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    items = [
        InvitationService.response(
            item,
            effective_primary_role=InvitationService.effective_primary_role(
                session, invitation=item
            ),
        )
        for item in InvitationService.list(
            session,
            company_id=company_id,
            page=page,
            page_size=page_size,
        )
    ]
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": InvitationService.count(session, company_id=company_id),
    }


@router.post(
    "/api/v1/companies/{company_id}/invitations",
    response_model=CompanyInvitationResponse,
    status_code=201,
)
def create_company_invitation(
    company_id: str,
    body: CompanyInvitationCreateRequest,
    request: Request,
    response: Response,
    context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
    session: DatabaseSession,
    csrf_header: Annotated[str | None, Header(alias=CSRF_HEADER_NAME)] = None,
) -> dict:
    _require_cookie_csrf(request, context, csrf_header)
    settings = request.app.state.settings
    lifetime = body.expires_in_hours * 3600 if body.expires_in_hours else settings.invitation_ttl_seconds
    create_arguments = {
        "company_id": company_id,
        "actor_user_id": context.user_id,
        "email": str(body.email),
        "display_name": body.display_name,
        "primary_role": body.primary_role,
        "idempotency_key": body.idempotency_key,
        "expires_in_seconds": lifetime,
        "pepper": settings.jwt_signing_secret,
        "request_id": request.state.request_id,
    }
    try:
        invitation, token, created = InvitationService.create(
            session, **create_arguments
        )
    except IntegrityError:
        # A same-email or same-idempotency-key winner may have committed while
        # this transaction waited on the unique index. Re-read and replay the
        # contract; the losing caller never receives the winner's raw token.
        session.rollback()
        invitation, token, created = InvitationService.create(
            session, **create_arguments
        )
    if not created:
        response.status_code = 200
    return InvitationService.response(
        invitation,
        acceptance_token=token,
        frontend_origin=settings.frontend_origin,
        effective_primary_role=InvitationService.effective_primary_role(
            session, invitation=invitation
        ),
    )


@router.post(
    "/api/v1/companies/{company_id}/invitations/{invitation_id}/reissue",
    response_model=CompanyInvitationResponse,
)
def reissue_company_invitation(
    company_id: str,
    invitation_id: str,
    request: Request,
    context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
    session: DatabaseSession,
    csrf_header: Annotated[str | None, Header(alias=CSRF_HEADER_NAME)] = None,
) -> dict:
    _require_cookie_csrf(request, context, csrf_header)
    settings = request.app.state.settings
    invitation, token = InvitationService.reissue(
        session,
        company_id=company_id,
        invitation_id=invitation_id,
        actor_user_id=context.user_id,
        expires_in_seconds=settings.invitation_ttl_seconds,
        pepper=settings.jwt_signing_secret,
        request_id=request.state.request_id,
    )
    return InvitationService.response(
        invitation,
        acceptance_token=token,
        frontend_origin=settings.frontend_origin,
        effective_primary_role=InvitationService.effective_primary_role(
            session, invitation=invitation
        ),
    )


@router.post(
    "/api/v1/companies/{company_id}/invitations/{invitation_id}/revoke",
    response_model=CompanyInvitationResponse,
)
def revoke_company_invitation(
    company_id: str,
    invitation_id: str,
    request: Request,
    context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
    session: DatabaseSession,
    csrf_header: Annotated[str | None, Header(alias=CSRF_HEADER_NAME)] = None,
) -> dict:
    _require_cookie_csrf(request, context, csrf_header)
    invitation = InvitationService.revoke(
        session,
        company_id=company_id,
        invitation_id=invitation_id,
        actor_user_id=context.user_id,
        request_id=request.state.request_id,
    )
    return InvitationService.response(
        invitation,
        effective_primary_role=InvitationService.effective_primary_role(
            session, invitation=invitation
        ),
    )
