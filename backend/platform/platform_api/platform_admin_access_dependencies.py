from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Callable

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from .config import (
    PHISHING_RESISTANT_PLATFORM_ADMIN_AMR,
    runtime_settings_are_protected,
)
from .dependencies import (
    _authenticated_principal,
    _owner_local_user_ids,
    _record_platform_admin_activity,
    get_db,
)
from .models import User, UserStatus
from .services.authentication import SESSION_COOKIE_NAME
from .platform_admin_access_catalog import validate_platform_admin_permission_code
from .services.platform_admin_access import PlatformAdminAccessService
from .platform_owner_identity import is_platform_owner_identity


@dataclass(frozen=True)
class GranularPlatformAdminContext:
    user_id: str
    is_platform_owner: bool
    effective_permissions: frozenset[str]


def _authenticate_platform_administrator(
    request: Request,
    session: Session,
    *,
    authorization: str | None,
    development_user_id: str | None,
) -> GranularPlatformAdminContext:
    settings = request.app.state.settings
    authenticated = None
    if request.cookies.get(SESSION_COOKIE_NAME) or authorization is not None:
        authenticated = _authenticated_principal(request, session, authorization)
        if not authenticated.user.is_platform_admin:
            raise HTTPException(status_code=403, detail="Not a platform administrator")
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
        user_id = authenticated.user_id
    elif runtime_settings_are_protected(settings):
        raise HTTPException(status_code=401, detail="Session is missing or invalid")
    elif settings.development_header_auth_enabled:
        user_id = development_user_id
    else:
        raise HTTPException(
            status_code=401,
            detail="Development header authentication is disabled",
        )

    if not user_id:
        raise HTTPException(
            status_code=401, detail="Missing platform administrator identity"
        )
    user = session.get(User, user_id)
    if user is None or user.status != UserStatus.ACTIVE or not user.is_platform_admin:
        raise HTTPException(status_code=403, detail="Not a platform administrator")

    owner_ids = set(_owner_local_user_ids(session, settings))
    if (
        not runtime_settings_are_protected(settings)
        and settings.development_header_auth_enabled
        and settings.enable_bootstrap
    ):
        owner_ids.update(
            getattr(
                request.app.state,
                "development_platform_owner_user_ids",
                set(),
            )
        )
    owner_ids = frozenset(owner_ids)
    is_owner = (
        authenticated is not None
        and is_platform_owner_identity(
            issuer=authenticated.issuer,
            subject=authenticated.subject,
            configured_issuer=settings.oidc_issuer,
            configured_subjects=settings.platform_owner_user_ids,
        )
    ) or (authenticated is None and user_id in owner_ids)
    permissions = PlatformAdminAccessService.effective_permissions(
        session,
        user_id=user_id,
        platform_owner_user_ids=owner_ids,
    )
    return GranularPlatformAdminContext(
        user_id=user_id,
        is_platform_owner=is_owner,
        effective_permissions=permissions,
    )


def require_platform_admin_permission(permission_code: str) -> Callable:
    """Build a route guard for one server-owned platform permission.

    Authentication and recent step-up requirements are identical for owners and
    delegated administrators. Only authorization differs: owners obtain the
    catalog from the server allowlist, while every non-owner is evaluated from
    durable role assignments and explicit overrides.
    """

    validate_platform_admin_permission_code(permission_code)

    def dependency(
        request: Request,
        session: Annotated[Session, Depends(get_db, scope="function")],
        authorization: Annotated[
            str | None, Header(alias="Authorization")
        ] = None,
        x_platform_admin_user_id: Annotated[
            str | None, Header(alias="X-Platform-Admin-User-ID")
        ] = None,
    ) -> GranularPlatformAdminContext:
        context = _authenticate_platform_administrator(
            request,
            session,
            authorization=authorization,
            development_user_id=x_platform_admin_user_id,
        )
        if permission_code not in context.effective_permissions:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Platform administrator permission required: "
                    f"{permission_code}"
                ),
            )
        user = session.get(User, context.user_id)
        if user is None:  # pragma: no cover - authenticated above in same transaction
            raise HTTPException(status_code=403, detail="Not a platform administrator")
        _record_platform_admin_activity(session, user)
        return context

    return dependency
