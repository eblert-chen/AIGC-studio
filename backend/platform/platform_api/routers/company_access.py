from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import runtime_settings_are_protected
from ..dependencies import (
    TenantContext,
    get_db,
    get_tenant_context,
    require_permission,
)
from ..models import BillingUnit, Company, User
from ..schemas import (
    AssignRoleRequest,
    CompanyMeResponse,
    CreateMemberRequest,
    CreateRoleRequest,
    MemberPermissionDetailResponse,
    MemberResponse,
    MemberStatusRequest,
    PermissionCatalogResponse,
    PermissionOverrideRequest,
    PermissionOverrideResponse,
    ReplaceMemberAccessRequest,
    ReplaceMemberRolesRequest,
    ReplacePermissionOverridesRequest,
    RoleResponse,
    UpdateRoleRequest,
)
from ..services.access_lifecycle import AccessLifecycleService
from ..services.audit import AuditService
from ..services.companies import CompanyService
from ..services.errors import ConflictError, DomainError
from ..services.permissions import PermissionService


router = APIRouter()


def _member_response(
    session: Session, *, company_id: str, user: User, membership
) -> MemberResponse:
    roles = AccessLifecycleService.roles_for_membership(
        session, company_id=company_id, membership_id=membership.id
    )
    inherited_permissions = PermissionService.inherited_permissions(
        session, membership_id=membership.id
    )
    permission_overrides = PermissionService.permission_overrides(
        session, membership_id=membership.id
    )
    effective_permissions = PermissionService.apply_overrides(
        inherited_permissions, permission_overrides
    )
    return MemberResponse(
        user_id=user.id,
        membership_id=membership.id,
        email=user.email,
        display_name=user.display_name,
        status=membership.status,
        roles=[
            {
                "id": role.id,
                "name": role.name,
                "is_system": role.is_system,
                "system_key": role.system_key,
            }
            for role in roles
        ],
        inherited_permission_codes=sorted(inherited_permissions),
        effective_permission_codes=sorted(effective_permissions),
        permission_overrides=[
            {"permission_code": code, "effect": effect}
            for code, effect in permission_overrides.items()
        ],
    )


@router.get(
    "/api/v1/companies/{company_id}/me",
    response_model=CompanyMeResponse,
)
def company_me(
    company_id: str,
    context: Annotated[TenantContext, Depends(get_tenant_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    identity = AccessLifecycleService.current_identity(
        session,
        company_id=company_id,
        membership_id=context.membership_id,
        user_id=context.user_id,
    )
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="公司不存在")
    return {
        **identity,
        "billing_unit": (
            BillingUnit.POINT
            if company.billing_version == 2
            else BillingUnit.CNY_CENT
        ),
        "billing_version": company.billing_version,
    }

@router.get(
    "/api/v1/companies/{company_id}/permissions",
    response_model=list[PermissionCatalogResponse],
)
def list_permission_catalog(
    company_id: str,
    _: Annotated[TenantContext, Depends(require_permission("users.read"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    return PermissionService.list_catalog(session)

@router.get(
    "/api/v1/companies/{company_id}/members",
    response_model=list[MemberResponse],
)
def list_members(
    company_id: str,
    _: Annotated[TenantContext, Depends(require_permission("users.read"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> list[MemberResponse]:
    return [
        _member_response(
            session,
            company_id=company_id,
            user=user,
            membership=membership,
        )
        for user, membership in CompanyService.list_members(
            session, company_id=company_id
        )
    ]

@router.get(
    "/api/v1/companies/{company_id}/members/{membership_id}/permissions",
    response_model=MemberPermissionDetailResponse,
)
def member_permission_detail(
    company_id: str,
    membership_id: str,
    _: Annotated[TenantContext, Depends(require_permission("users.read"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    membership = AccessLifecycleService.get_membership(
        session, company_id=company_id, membership_id=membership_id
    )
    return {
        "membership_id": membership.id,
        "items": PermissionService.permission_detail(
            session, membership_id=membership.id
        ),
    }

@router.post(
    "/api/v1/companies/{company_id}/members",
    response_model=MemberResponse,
    status_code=201,
)
def create_member(
    company_id: str,
    body: CreateMemberRequest,
    request: Request,
    context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> MemberResponse:
    if runtime_settings_are_protected(request.app.state.settings):
        raise DomainError(
            "Company invitations are required",
            "invitation_required",
            409,
        )
    user, membership, created = CompanyService.add_member(
        session,
        company_id=company_id,
        email=str(body.email),
        display_name=body.display_name,
    )
    if created:
        primary_role = AccessLifecycleService.system_role(
            session,
            company_id=company_id,
            system_key=body.primary_role,
        )
        AccessLifecycleService.assign_role(
            session,
            company_id=company_id,
            membership_id=membership.id,
            role_id=primary_role.id,
            actor_membership_id=context.membership_id,
        )
    else:
        existing_primary_keys = {
            role.system_key
            for role in AccessLifecycleService.roles_for_membership(
                session,
                company_id=company_id,
                membership_id=membership.id,
            )
            if role.system_key in {"operator", "team_lead"}
        }
        if existing_primary_keys != {body.primary_role}:
            raise ConflictError(
                "该成员已存在；基础级别不一致，请使用成员升降级接口"
            )
    if created:
        AuditService.append(
            session,
            actor_user_id=context.user_id,
            action="company.member.create",
            target_type="company_membership",
            target_id=membership.id,
            before_summary={},
            after_summary={
                "company_id": company_id,
                "user_id": user.id,
                "status": membership.status.value,
                "primary_role": body.primary_role,
            },
            request_id=request.state.request_id,
        )
    return _member_response(
        session,
        company_id=company_id,
        user=user,
        membership=membership,
    )

@router.patch(
    "/api/v1/companies/{company_id}/members/{membership_id}/status",
    response_model=MemberResponse,
)
def set_member_status(
    company_id: str,
    membership_id: str,
    body: MemberStatusRequest,
    request: Request,
    context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    before, membership, changed = AccessLifecycleService.set_member_status(
        session,
        company_id=company_id,
        membership_id=membership_id,
        status=body.status,
        actor_membership_id=context.membership_id,
    )
    user = session.get(User, membership.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="成员用户不存在")
    if changed:
        AuditService.append(
            session,
            actor_user_id=context.user_id,
            action="company.member.status.update",
            target_type="company_membership",
            target_id=membership.id,
            before_summary={"status": before.value},
            after_summary={"status": membership.status.value},
            request_id=request.state.request_id,
        )
    return _member_response(
        session,
        company_id=company_id,
        user=user,
        membership=membership,
    )

@router.get(
    "/api/v1/companies/{company_id}/roles",
    response_model=list[RoleResponse],
)
def list_roles(
    company_id: str,
    _: Annotated[TenantContext, Depends(require_permission("users.read"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    return [
        role.as_dict()
        for role in AccessLifecycleService.list_roles(
            session, company_id=company_id
        )
    ]

@router.post(
    "/api/v1/companies/{company_id}/roles",
    response_model=RoleResponse,
    status_code=201,
)
def create_role(
    company_id: str,
    body: CreateRoleRequest,
    request: Request,
    context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    role, created = AccessLifecycleService.create_role(
        session,
        company_id=company_id,
        name=body.name,
        description=body.description,
        permission_codes=body.permission_codes,
        actor_membership_id=context.membership_id,
    )
    snapshot = AccessLifecycleService.role_snapshot(session, role=role)
    if created:
        AuditService.append(
            session,
            actor_user_id=context.user_id,
            action="company.role.create",
            target_type="role",
            target_id=role.id,
            before_summary={},
            after_summary=snapshot.as_dict(),
            request_id=request.state.request_id,
        )
    return snapshot.as_dict()

@router.put(
    "/api/v1/companies/{company_id}/roles/{role_id}",
    response_model=RoleResponse,
)
def update_role(
    company_id: str,
    role_id: str,
    body: UpdateRoleRequest,
    request: Request,
    context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    before, after, changed = AccessLifecycleService.update_role(
        session,
        company_id=company_id,
        role_id=role_id,
        name=body.name,
        description=body.description,
        permission_codes=body.permission_codes,
        actor_membership_id=context.membership_id,
    )
    if changed:
        AuditService.append(
            session,
            actor_user_id=context.user_id,
            action="company.role.update",
            target_type="role",
            target_id=role_id,
            before_summary=before.as_dict(),
            after_summary=after.as_dict(),
            request_id=request.state.request_id,
        )
    return after.as_dict()

@router.delete(
    "/api/v1/companies/{company_id}/roles/{role_id}",
    status_code=204,
)
def delete_role(
    company_id: str,
    role_id: str,
    request: Request,
    context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> None:
    before = AccessLifecycleService.delete_role(
        session,
        company_id=company_id,
        role_id=role_id,
        actor_membership_id=context.membership_id,
    )
    AuditService.append(
        session,
        actor_user_id=context.user_id,
        action="company.role.delete",
        target_type="role",
        target_id=role_id,
        before_summary=before.as_dict(),
        after_summary={},
        request_id=request.state.request_id,
    )

@router.post(
    "/api/v1/companies/{company_id}/roles/{role_id}/assign",
    status_code=204,
)
def assign_role(
    company_id: str,
    role_id: str,
    body: AssignRoleRequest,
    request: Request,
    context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> None:
    before = AccessLifecycleService.roles_for_membership(
        session,
        company_id=company_id,
        membership_id=body.membership_id,
    )
    changed = AccessLifecycleService.assign_role(
        session,
        company_id=company_id,
        membership_id=body.membership_id,
        role_id=role_id,
        actor_membership_id=context.membership_id,
    )
    if changed:
        after = AccessLifecycleService.roles_for_membership(
            session,
            company_id=company_id,
            membership_id=body.membership_id,
        )
        AuditService.append(
            session,
            actor_user_id=context.user_id,
            action="company.member.role.assign",
            target_type="company_membership",
            target_id=body.membership_id,
            before_summary={"role_ids": [role.id for role in before]},
            after_summary={"role_ids": [role.id for role in after]},
            request_id=request.state.request_id,
        )

@router.delete(
    "/api/v1/companies/{company_id}/roles/{role_id}/assignments/{membership_id}",
    status_code=204,
)
def unassign_role(
    company_id: str,
    role_id: str,
    membership_id: str,
    request: Request,
    context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> None:
    before = AccessLifecycleService.roles_for_membership(
        session,
        company_id=company_id,
        membership_id=membership_id,
    )
    changed = AccessLifecycleService.unassign_role(
        session,
        company_id=company_id,
        membership_id=membership_id,
        role_id=role_id,
        actor_membership_id=context.membership_id,
    )
    if changed:
        after = AccessLifecycleService.roles_for_membership(
            session,
            company_id=company_id,
            membership_id=membership_id,
        )
        AuditService.append(
            session,
            actor_user_id=context.user_id,
            action="company.member.role.unassign",
            target_type="company_membership",
            target_id=membership_id,
            before_summary={"role_ids": [role.id for role in before]},
            after_summary={"role_ids": [role.id for role in after]},
            request_id=request.state.request_id,
        )

@router.put(
    "/api/v1/companies/{company_id}/members/{membership_id}/roles",
    response_model=MemberResponse,
)
def replace_member_roles(
    company_id: str,
    membership_id: str,
    body: ReplaceMemberRolesRequest,
    request: Request,
    context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    before, after, changed = AccessLifecycleService.replace_roles(
        session,
        company_id=company_id,
        membership_id=membership_id,
        role_ids=body.role_ids,
        actor_membership_id=context.membership_id,
        expected_role_ids=body.expected_role_ids,
    )
    if changed:
        AuditService.append(
            session,
            actor_user_id=context.user_id,
            action="company.member.roles.replace",
            target_type="company_membership",
            target_id=membership_id,
            before_summary={"role_ids": [role.id for role in before]},
            after_summary={"role_ids": [role.id for role in after]},
            request_id=request.state.request_id,
        )
    membership = AccessLifecycleService.get_membership(
        session, company_id=company_id, membership_id=membership_id
    )
    user = session.get(User, membership.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="成员用户不存在")
    return _member_response(
        session,
        company_id=company_id,
        user=user,
        membership=membership,
    )

@router.put(
    "/api/v1/companies/{company_id}/members/{membership_id}/access",
    response_model=MemberResponse,
)
def replace_member_access(
    company_id: str,
    membership_id: str,
    body: ReplaceMemberAccessRequest,
    request: Request,
    context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    (
        before_roles,
        after_roles,
        before_overrides,
        after_overrides,
        changed,
    ) = AccessLifecycleService.replace_member_access(
        session,
        company_id=company_id,
        membership_id=membership_id,
        role_ids=body.role_ids,
        permission_overrides=body.permission_overrides,
        actor_membership_id=context.membership_id,
        expected_role_ids=body.expected_role_ids,
        expected_permission_overrides=body.expected_permission_overrides,
    )
    if changed:
        AuditService.append(
            session,
            actor_user_id=context.user_id,
            action="company.member.access.replace",
            target_type="company_membership",
            target_id=membership_id,
            before_summary={
                "role_ids": [role.id for role in before_roles],
                "permission_overrides": {
                    code: effect.value for code, effect in before_overrides.items()
                },
            },
            after_summary={
                "role_ids": [role.id for role in after_roles],
                "permission_overrides": {
                    code: effect.value for code, effect in after_overrides.items()
                },
            },
            request_id=request.state.request_id,
        )
    membership = AccessLifecycleService.get_membership(
        session, company_id=company_id, membership_id=membership_id
    )
    user = session.get(User, membership.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="成员用户不存在")
    return _member_response(
        session,
        company_id=company_id,
        user=user,
        membership=membership,
    )

@router.put(
    "/api/v1/companies/{company_id}/members/{membership_id}/permissions",
    response_model=MemberResponse,
)
def replace_permission_overrides(
    company_id: str,
    membership_id: str,
    body: ReplacePermissionOverridesRequest,
    request: Request,
    context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    before, after, changed = AccessLifecycleService.replace_overrides(
        session,
        company_id=company_id,
        membership_id=membership_id,
        overrides=body.overrides,
        actor_membership_id=context.membership_id,
        expected_overrides=body.expected_overrides,
    )
    if changed:
        AuditService.append(
            session,
            actor_user_id=context.user_id,
            action="company.member.permissions.replace",
            target_type="company_membership",
            target_id=membership_id,
            before_summary={
                "overrides": {code: effect.value for code, effect in before.items()}
            },
            after_summary={
                "overrides": {code: effect.value for code, effect in after.items()}
            },
            request_id=request.state.request_id,
        )
    membership = AccessLifecycleService.get_membership(
        session, company_id=company_id, membership_id=membership_id
    )
    user = session.get(User, membership.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="成员用户不存在")
    return _member_response(
        session,
        company_id=company_id,
        user=user,
        membership=membership,
    )

@router.put(
    "/api/v1/companies/{company_id}/members/{membership_id}/permission",
    response_model=PermissionOverrideResponse,
)
def set_permission_override(
    company_id: str,
    membership_id: str,
    body: PermissionOverrideRequest,
    request: Request,
    context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    override, before_effect, changed = AccessLifecycleService.set_override(
        session,
        company_id=company_id,
        membership_id=membership_id,
        permission_code=body.permission_code,
        effect=body.effect,
        actor_membership_id=context.membership_id,
    )
    if changed:
        AuditService.append(
            session,
            actor_user_id=context.user_id,
            action="company.member.permission.set",
            target_type="company_membership",
            target_id=membership_id,
            before_summary={
                "permission_code": body.permission_code,
                "effect": before_effect.value if before_effect else None,
            },
            after_summary={
                "permission_code": body.permission_code,
                "effect": body.effect.value,
            },
            request_id=request.state.request_id,
        )
    return override

@router.delete(
    "/api/v1/companies/{company_id}/members/{membership_id}/permission/{permission_code}",
    status_code=204,
)
def clear_permission_override(
    company_id: str,
    membership_id: str,
    permission_code: str,
    request: Request,
    context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> None:
    before_effect, changed = AccessLifecycleService.clear_override(
        session,
        company_id=company_id,
        membership_id=membership_id,
        permission_code=permission_code,
        actor_membership_id=context.membership_id,
    )
    if changed:
        AuditService.append(
            session,
            actor_user_id=context.user_id,
            action="company.member.permission.clear",
            target_type="company_membership",
            target_id=membership_id,
            before_summary={
                "permission_code": permission_code,
                "effect": before_effect.value if before_effect else None,
            },
            after_summary={
                "permission_code": permission_code,
                "effect": None,
            },
            request_id=request.state.request_id,
        )
