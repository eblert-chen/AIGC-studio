from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.orm import Session

from .dependencies import _owner_local_user_ids, get_db
from .config import runtime_settings_are_protected
from .platform_admin_access_catalog import PLATFORM_ADMIN_PERMISSION_CATALOG
from .platform_admin_access_dependencies import (
    GranularPlatformAdminContext,
    require_platform_admin_permission,
)
from .platform_admin_access_models import PlatformAdminPermissionEffect
from .platform_admin_access_schemas import (
    PlatformAdministratorResponse,
    PlatformAdminAccessReplaceRequest,
    PlatformAdminAccessResponse,
    PlatformAdminPermissionResponse,
    PlatformAdminRoleCreateRequest,
    PlatformAdminRoleReplaceRequest,
    PlatformAdminRoleResponse,
    PlatformAdminStatusRequest,
    PlatformAdminStatusResponse,
)
from .request_ids import normalize_request_id
from .services.platform_admin_access import (
    PlatformAdminAccessService,
    PlatformAdminAccessSnapshot,
    PlatformAdminRoleSnapshot,
)


router = APIRouter(prefix="/api/v1/platform-admin/access", tags=["platform-admin-access"])

AdminAccessReader = Annotated[
    GranularPlatformAdminContext,
    Depends(require_platform_admin_permission("platform.admin_access.read")),
]
AdminAccessManager = Annotated[
    GranularPlatformAdminContext,
    Depends(require_platform_admin_permission("platform.admin_access.manage")),
]
DatabaseSession = Annotated[Session, Depends(get_db, scope="function")]


def _role_response(role: PlatformAdminRoleSnapshot) -> PlatformAdminRoleResponse:
    return PlatformAdminRoleResponse(
        id=role.id,
        key=role.key,
        display_name=role.display_name,
        description=role.description,
        active=role.active,
        lock_version=role.lock_version,
        permission_codes=list(role.permission_codes),
    )


def _access_response(
    access: PlatformAdminAccessSnapshot,
) -> PlatformAdminAccessResponse:
    return PlatformAdminAccessResponse(
        user_id=access.user_id,
        is_platform_owner=access.is_platform_owner,
        lock_version=access.lock_version,
        role_ids=list(access.role_ids),
        inherited_permissions=sorted(access.inherited_permissions),
        permission_overrides={
            code: effect.value
            for code, effect in sorted(access.permission_overrides.items())
        },
        effective_permissions=sorted(access.effective_permissions),
        snapshot=access.snapshot,
    )


@router.get("/permissions", response_model=list[PlatformAdminPermissionResponse])
def list_platform_admin_permissions(
    _: AdminAccessReader,
) -> list[PlatformAdminPermissionResponse]:
    return [
        PlatformAdminPermissionResponse(
            code=permission.code,
            domain=permission.domain,
            action=permission.action,
            description=permission.description,
        )
        for permission in PLATFORM_ADMIN_PERMISSION_CATALOG
    ]


@router.get("/roles", response_model=list[PlatformAdminRoleResponse])
def list_platform_admin_roles(
    _: AdminAccessReader, session: DatabaseSession
) -> list[PlatformAdminRoleResponse]:
    return [
        _role_response(role)
        for role in PlatformAdminAccessService.list_roles(session)
    ]


@router.post(
    "/roles",
    response_model=PlatformAdminRoleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_platform_admin_role(
    payload: PlatformAdminRoleCreateRequest,
    request: Request,
    admin: AdminAccessManager,
    session: DatabaseSession,
    x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> PlatformAdminRoleResponse:
    role = PlatformAdminAccessService.create_role(
        session,
        actor_user_id=admin.user_id,
        key=payload.key,
        display_name=payload.display_name,
        description=payload.description,
        permission_codes=set(payload.permission_codes),
        platform_owner_user_ids=_owner_ids(request, session),
        request_id=normalize_request_id(x_request_id),
        change_reason=payload.change_reason,
    )
    return _role_response(role)


def _owner_ids(request: Request, session: Session) -> frozenset[str]:
    settings = request.app.state.settings
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
    return frozenset(owner_ids)


@router.put("/roles/{role_id}", response_model=PlatformAdminRoleResponse)
def replace_platform_admin_role(
    role_id: str,
    payload: PlatformAdminRoleReplaceRequest,
    request: Request,
    admin: AdminAccessManager,
    session: DatabaseSession,
    x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> PlatformAdminRoleResponse:
    role = PlatformAdminAccessService.replace_role(
        session,
        actor_user_id=admin.user_id,
        role_id=role_id,
        display_name=payload.display_name,
        description=payload.description,
        active=payload.active,
        permission_codes=set(payload.permission_codes),
        expected_lock_version=payload.expected_lock_version,
        platform_owner_user_ids=_owner_ids(request, session),
        request_id=normalize_request_id(x_request_id),
        change_reason=payload.change_reason,
    )
    return _role_response(role)


@router.get("/users", response_model=list[PlatformAdministratorResponse])
def list_platform_administrators(
    request: Request,
    _: AdminAccessReader,
    session: DatabaseSession,
) -> list[PlatformAdministratorResponse]:
    return [
        PlatformAdministratorResponse(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            # list_administrators is selected from the authoritative
            # is_platform_admin flag. Returning that fact is preferable to a
            # UI adapter inventing an account status.
            status="active",
            last_active_at=last_active_at,
            access=_access_response(access),
        )
        for user, access, last_active_at in (
            PlatformAdminAccessService.list_administrators(
                session, platform_owner_user_ids=_owner_ids(request, session)
            )
        )
    ]


@router.get(
    "/users/{user_id}", response_model=PlatformAdminAccessResponse
)
def get_platform_administrator_access(
    user_id: str,
    request: Request,
    _: AdminAccessReader,
    session: DatabaseSession,
) -> PlatformAdminAccessResponse:
    return _access_response(
        PlatformAdminAccessService.access_snapshot(
            session,
            user_id=user_id,
            platform_owner_user_ids=_owner_ids(request, session),
        )
    )


@router.put(
    "/users/{user_id}", response_model=PlatformAdminAccessResponse
)
def replace_platform_administrator_access(
    user_id: str,
    payload: PlatformAdminAccessReplaceRequest,
    request: Request,
    admin: AdminAccessManager,
    session: DatabaseSession,
    x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> PlatformAdminAccessResponse:
    overrides = {
        code: PlatformAdminPermissionEffect(effect)
        for code, effect in payload.permission_overrides.items()
    }
    return _access_response(
        PlatformAdminAccessService.replace_user_access(
            session,
            actor_user_id=admin.user_id,
            target_user_id=user_id,
            role_ids=set(payload.role_ids),
            permission_overrides=overrides,
            expected_lock_version=payload.expected_lock_version,
            platform_owner_user_ids=_owner_ids(request, session),
            request_id=normalize_request_id(x_request_id),
            change_reason=payload.change_reason,
        )
    )


@router.put(
    "/users/{user_id}/status", response_model=PlatformAdminStatusResponse
)
def set_platform_administrator_status(
    user_id: str,
    payload: PlatformAdminStatusRequest,
    request: Request,
    admin: AdminAccessManager,
    session: DatabaseSession,
    x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> PlatformAdminStatusResponse:
    user = PlatformAdminAccessService.set_administrator_status(
        session,
        actor_user_id=admin.user_id,
        target_user_id=user_id,
        enabled=payload.enabled,
        expected_is_platform_admin=payload.expected_is_platform_admin,
        platform_owner_user_ids=_owner_ids(request, session),
        request_id=normalize_request_id(x_request_id),
        change_reason=payload.change_reason,
    )
    return PlatformAdminStatusResponse(
        user_id=user.id, is_platform_admin=user.is_platform_admin
    )
