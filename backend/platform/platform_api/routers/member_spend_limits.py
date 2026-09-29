from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..dependencies import (
    TenantContext,
    get_db,
    require_permission,
)
from ..services.errors import ConflictError, DomainError
from ..services.member_spend_limits import (
    CompanyMemberSpendLimitService,
    UpsertMemberSpendLimitRequest,
)


router = APIRouter(tags=["member-spend-limits"])


def _domain_error(exc: DomainError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/api/v1/companies/{company_id}/members/spend-limits")
def list_member_spend_limits(
    company_id: str,
    context: Annotated[TenantContext, Depends(require_permission("billing.read"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    if context.company_id != company_id:
        raise HTTPException(status_code=404, detail="Company does not exist")
    limits = CompanyMemberSpendLimitService.list(session, company_id=company_id)
    return {
        "items": [
            CompanyMemberSpendLimitService.payload(limit) for limit in limits
        ]
    }


@router.put("/api/v1/companies/{company_id}/members/{user_id}/spend-limit")
def upsert_member_spend_limit(
    company_id: str,
    user_id: str,
    body: UpsertMemberSpendLimitRequest,
    context: Annotated[TenantContext, Depends(require_permission("billing.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    if context.company_id != company_id:
        raise HTTPException(status_code=404, detail="Company does not exist")
    try:
        limit, _created = CompanyMemberSpendLimitService.upsert(
            session,
            company_id=company_id,
            user_id=user_id,
            actor_user_id=context.user_id,
            body=body,
        )
    except (ConflictError, DomainError) as exc:
        raise _domain_error(exc) from exc
    return CompanyMemberSpendLimitService.payload(limit)


@router.get("/api/v1/companies/{company_id}/members/{user_id}/spend-usage")
def read_member_spend_usage(
    company_id: str,
    user_id: str,
    context: Annotated[TenantContext, Depends(require_permission("billing.read"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    if context.company_id != company_id:
        raise HTTPException(status_code=404, detail="Company does not exist")
    return CompanyMemberSpendLimitService.usage(
        session, company_id=company_id, user_id=user_id
    )
