from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..dependencies import (
    TenantContext,
    UserContext,
    get_db,
    get_user_context,
    require_permission,
)
from ..models import ProductContext, User
from ..services.errors import ConflictError, NotFoundError
from ..services.personal import PersonalWorkspaceService
from ..services.subjects import (
    AddSubjectReferenceRequest,
    CreateSubjectRequest,
    SubjectService,
    UpdateSubjectRequest,
)


router = APIRouter(tags=["subjects"])


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


def _domain_error(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail=exc.message)
    if isinstance(exc, ConflictError):
        return HTTPException(status_code=409, detail=exc.message)
    raise exc


def _payload(session: Session, subject) -> dict[str, Any]:
    return SubjectService.payload(
        subject,
        reference_count=len(SubjectService.references(session, subject_id=subject.id)),
    )


@router.post("/api/v1/companies/{company_id}/subjects", status_code=201)
def create_company_subject(
    company_id: str,
    body: CreateSubjectRequest,
    context: Annotated[TenantContext, Depends(require_permission("assets.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    if context.company_id != company_id:
        raise HTTPException(status_code=404, detail="Company does not exist")
    try:
        subject = SubjectService.create(
            session,
            company_id=company_id,
            personal_workspace_id=None,
            user_id=context.user_id,
            body=body,
        )
    except (NotFoundError, ConflictError) as exc:
        raise _domain_error(exc) from exc
    return _payload(session, subject)


@router.get("/api/v1/companies/{company_id}/subjects")
def list_company_subjects(
    company_id: str,
    _: Annotated[TenantContext, Depends(require_permission("assets.read"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
    kind: str | None = None,
) -> dict[str, Any]:
    from ..models import SubjectKind

    resolved = SubjectKind(kind) if kind else None
    subjects = SubjectService.list(
        session, company_id=company_id, personal_workspace_id=None, kind=resolved
    )
    return {"items": [_payload(session, item) for item in subjects]}


@router.post(
    "/api/v1/companies/{company_id}/subjects/{subject_id}/references",
    status_code=201,
)
def add_company_subject_reference(
    company_id: str,
    subject_id: str,
    body: AddSubjectReferenceRequest,
    _: Annotated[TenantContext, Depends(require_permission("assets.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    try:
        reference = SubjectService.add_reference(
            session,
            subject_id=subject_id,
            company_id=company_id,
            personal_workspace_id=None,
            body=body,
        )
    except (NotFoundError, ConflictError) as exc:
        raise _domain_error(exc) from exc
    return SubjectService.reference_payload_row(reference)


@router.get("/api/v1/companies/{company_id}/subjects/{subject_id}/references")
def list_company_subject_references(
    company_id: str,
    subject_id: str,
    _: Annotated[TenantContext, Depends(require_permission("assets.read"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    try:
        SubjectService._require_subject(
            session,
            subject_id=subject_id,
            company_id=company_id,
            personal_workspace_id=None,
        )
    except NotFoundError as exc:
        raise _domain_error(exc) from exc
    references = SubjectService.references(session, subject_id=subject_id)
    return {
        "items": [
            SubjectService.reference_payload_row(item) for item in references
        ]
    }


@router.delete(
    "/api/v1/companies/{company_id}/subjects/{subject_id}/references/{asset_id}"
)
def remove_company_subject_reference(
    company_id: str,
    subject_id: str,
    asset_id: str,
    _: Annotated[TenantContext, Depends(require_permission("assets.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    try:
        SubjectService._require_subject(
            session,
            subject_id=subject_id,
            company_id=company_id,
            personal_workspace_id=None,
        )
        SubjectService.remove_reference(
            session, subject_id=subject_id, asset_id=asset_id
        )
    except (NotFoundError, ConflictError) as exc:
        raise _domain_error(exc) from exc
    return {"removed": True}


@router.post("/api/v1/companies/{company_id}/subjects/{subject_id}/archive")
def archive_company_subject(
    company_id: str,
    subject_id: str,
    _: Annotated[TenantContext, Depends(require_permission("assets.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    try:
        subject = SubjectService.archive(
            session,
            subject_id=subject_id,
            company_id=company_id,
            personal_workspace_id=None,
        )
    except (NotFoundError, ConflictError) as exc:
        raise _domain_error(exc) from exc
    return _payload(session, subject)


@router.post("/api/v1/personal/subjects", status_code=201)
def create_personal_subject(
    body: CreateSubjectRequest,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    workspace = _personal_workspace(session, context)
    try:
        subject = SubjectService.create(
            session,
            company_id=None,
            personal_workspace_id=workspace.id,
            user_id=context.user_id,
            body=body,
        )
    except (NotFoundError, ConflictError) as exc:
        raise _domain_error(exc) from exc
    return _payload(session, subject)


@router.get("/api/v1/personal/subjects")
def list_personal_subjects(
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    workspace = _personal_workspace(session, context)
    subjects = SubjectService.list(
        session, company_id=None, personal_workspace_id=workspace.id
    )
    return {"items": [_payload(session, item) for item in subjects]}


@router.post("/api/v1/personal/subjects/{subject_id}/references", status_code=201)
def add_personal_subject_reference(
    subject_id: str,
    body: AddSubjectReferenceRequest,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    workspace = _personal_workspace(session, context)
    try:
        reference = SubjectService.add_reference(
            session,
            subject_id=subject_id,
            company_id=None,
            personal_workspace_id=workspace.id,
            body=body,
        )
    except (NotFoundError, ConflictError) as exc:
        raise _domain_error(exc) from exc
    return SubjectService.reference_payload_row(reference)


@router.get("/api/v1/personal/subjects/{subject_id}/references")
def list_personal_subject_references(
    subject_id: str,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    workspace = _personal_workspace(session, context)
    try:
        SubjectService._require_subject(
            session,
            subject_id=subject_id,
            company_id=None,
            personal_workspace_id=workspace.id,
        )
    except NotFoundError as exc:
        raise _domain_error(exc) from exc
    references = SubjectService.references(session, subject_id=subject_id)
    return {
        "items": [
            SubjectService.reference_payload_row(item) for item in references
        ]
    }


@router.delete("/api/v1/personal/subjects/{subject_id}/references/{asset_id}")
def remove_personal_subject_reference(
    subject_id: str,
    asset_id: str,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    workspace = _personal_workspace(session, context)
    try:
        SubjectService._require_subject(
            session,
            subject_id=subject_id,
            company_id=None,
            personal_workspace_id=workspace.id,
        )
        SubjectService.remove_reference(
            session, subject_id=subject_id, asset_id=asset_id
        )
    except (NotFoundError, ConflictError) as exc:
        raise _domain_error(exc) from exc
    return {"removed": True}


@router.post("/api/v1/personal/subjects/{subject_id}/archive")
def archive_personal_subject(
    subject_id: str,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    workspace = _personal_workspace(session, context)
    try:
        subject = SubjectService.archive(
            session,
            subject_id=subject_id,
            company_id=None,
            personal_workspace_id=workspace.id,
        )
    except (NotFoundError, ConflictError) as exc:
        raise _domain_error(exc) from exc
    return _payload(session, subject)
