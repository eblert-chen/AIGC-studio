from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..dependencies import get_db
from ..models import TaskStatus
from ..platform_admin_access_dependencies import (
    GranularPlatformAdminContext,
    require_platform_admin_permission,
)
from ..platform_admin_task_content_schemas import (
    PlatformAdminTaskContentItem,
    PlatformAdminTaskContentPage,
)
from ..services.platform_admin_task_content import (
    PlatformAdminTaskContentService,
)


router = APIRouter(
    prefix="/api/v1/platform-admin/task-content",
    tags=["platform-admin-task-content"],
)

TaskContentReader = Annotated[
    GranularPlatformAdminContext,
    Depends(require_platform_admin_permission("platform.task_content.read")),
]
DatabaseSession = Annotated[Session, Depends(get_db, scope="function")]


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"


def _validate_list_filters(
    *,
    workspace_type: Literal["all", "company", "personal"],
    company_id: str | None,
    personal_workspace_id: str | None,
    created_from: datetime | None,
    created_before: datetime | None,
) -> None:
    if company_id is not None and personal_workspace_id is not None:
        raise HTTPException(
            status_code=422,
            detail="company_id and personal_workspace_id cannot be combined",
        )
    if workspace_type == "company" and personal_workspace_id is not None:
        raise HTTPException(
            status_code=422,
            detail="personal_workspace_id conflicts with company workspace_type",
        )
    if workspace_type == "personal" and company_id is not None:
        raise HTTPException(
            status_code=422,
            detail="company_id conflicts with personal workspace_type",
        )
    for field_name, value in (
        ("created_from", created_from),
        ("created_before", created_before),
    ):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise HTTPException(
                status_code=422,
                detail=f"{field_name} must include an explicit timezone",
            )
    if (
        created_from is not None
        and created_before is not None
        and created_from >= created_before
    ):
        raise HTTPException(
            status_code=422,
            detail="created_from must be earlier than created_before",
        )


@router.get("", response_model=PlatformAdminTaskContentPage)
def list_task_content(
    _: TaskContentReader,
    session: DatabaseSession,
    response: Response,
    workspace_type: Literal["all", "company", "personal"] = Query(default="all"),
    task_id: str | None = Query(default=None, min_length=1, max_length=36),
    user_id: str | None = Query(default=None, min_length=1, max_length=36),
    company_id: str | None = Query(default=None, min_length=1, max_length=36),
    personal_workspace_id: str | None = Query(
        default=None, min_length=1, max_length=36
    ),
    model_id: str | None = Query(default=None, min_length=1, max_length=36),
    status: TaskStatus | None = Query(default=None),
    created_from: datetime | None = Query(default=None),
    created_before: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=25),
) -> PlatformAdminTaskContentPage:
    _no_store(response)
    _validate_list_filters(
        workspace_type=workspace_type,
        company_id=company_id,
        personal_workspace_id=personal_workspace_id,
        created_from=created_from,
        created_before=created_before,
    )
    total, records = PlatformAdminTaskContentService.page(
        session,
        workspace_type=workspace_type,
        task_id=task_id,
        user_id=user_id,
        company_id=company_id,
        personal_workspace_id=personal_workspace_id,
        model_id=model_id,
        status=status,
        created_from=created_from,
        created_before=created_before,
        page=page,
        page_size=page_size,
    )
    return PlatformAdminTaskContentPage(
        page=page,
        page_size=page_size,
        total=total,
        items=[
            PlatformAdminTaskContentItem(
                task_id=record.task_id,
                workspace_type=record.workspace_type,
                company_id=record.company_id,
                company_name=record.company_name,
                personal_workspace_id=record.personal_workspace_id,
                user_id=record.user_id,
                user_display_name=record.user_display_name,
                model_id=record.model_id,
                model_display_name=record.model_display_name,
                status=record.status,
                prompt=record.prompt,
                prompt_length=len(record.prompt) if record.prompt is not None else 0,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
            for record in records
        ],
    )
