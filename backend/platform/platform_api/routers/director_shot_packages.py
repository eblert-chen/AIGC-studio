from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..dependencies import (
    TenantContext,
    UserContext,
    get_db,
    get_user_context,
    require_permission,
)
from ..models import ProductContext, User
from ..services.director_shot_packages import (
    CreateDirectorShotPackageRequest,
    DirectorShotPackageResponse,
    DirectorShotPackageService,
)
from ..services.personal import PersonalWorkspaceService


router = APIRouter(tags=["director-shot-packages"])


def _personal_workspace(session: Session, context: UserContext):
    active_context = context.active_product_context
    if active_context is None:
        user = session.get(User, context.user_id)
        if user is not None and user.account_type.value == "personal":
            active_context = ProductContext.PERSONAL
    return PersonalWorkspaceService.require_for_product_context(
        session,
        user_id=context.user_id,
        active_product_context=active_context,
        external_identity_id=context.external_identity_id,
    )


@router.post(
    "/api/v1/companies/{company_id}/director-shot-packages",
    response_model=DirectorShotPackageResponse,
    status_code=201,
)
def create_company_director_shot_package(
    company_id: str,
    body: CreateDirectorShotPackageRequest,
    _: Request,
    context: Annotated[TenantContext, Depends(require_permission("assets.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    package, _created = DirectorShotPackageService.create(
        session,
        company_id=company_id,
        personal_workspace_id=None,
        user_id=context.user_id,
        body=body,
    )
    return DirectorShotPackageService.response(package)


@router.get(
    "/api/v1/companies/{company_id}/director-shot-packages/{package_id}",
    response_model=DirectorShotPackageResponse,
)
def get_company_director_shot_package(
    company_id: str,
    package_id: str,
    _: Annotated[TenantContext, Depends(require_permission("assets.read"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    package = DirectorShotPackageService.get(
        session,
        package_id=package_id,
        company_id=company_id,
        personal_workspace_id=None,
    )
    return DirectorShotPackageService.response(package)


@router.post(
    "/api/v1/personal/director-shot-packages",
    response_model=DirectorShotPackageResponse,
    status_code=201,
)
def create_personal_director_shot_package(
    body: CreateDirectorShotPackageRequest,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    workspace = _personal_workspace(session, context)
    package, _created = DirectorShotPackageService.create(
        session,
        company_id=None,
        personal_workspace_id=workspace.id,
        user_id=context.user_id,
        body=body,
    )
    return DirectorShotPackageService.response(package)


@router.get(
    "/api/v1/personal/director-shot-packages/{package_id}",
    response_model=DirectorShotPackageResponse,
)
def get_personal_director_shot_package(
    package_id: str,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    workspace = _personal_workspace(session, context)
    package = DirectorShotPackageService.get(
        session,
        package_id=package_id,
        company_id=None,
        personal_workspace_id=workspace.id,
    )
    return DirectorShotPackageService.response(package)

