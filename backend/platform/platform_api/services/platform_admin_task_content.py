from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Company, GenerationTask, ModelDefinition, TaskStatus, User


@dataclass(frozen=True)
class TaskContentRecord:
    task_id: str
    workspace_type: Literal["company", "personal"]
    company_id: str | None
    company_name: str | None
    personal_workspace_id: str | None
    user_id: str
    user_display_name: str
    model_id: str
    model_display_name: str
    status: TaskStatus
    prompt: str | None
    created_at: datetime
    updated_at: datetime


class PlatformAdminTaskContentService:
    """Read-only collection of task prompts for authorized administrators."""

    @staticmethod
    def _prompt(prompt: object) -> str | None:
        if not isinstance(prompt, str) or not prompt.strip():
            return None
        return prompt

    @classmethod
    def page(
        cls,
        session: Session,
        *,
        workspace_type: Literal["all", "company", "personal"],
        task_id: str | None,
        user_id: str | None,
        company_id: str | None,
        personal_workspace_id: str | None,
        model_id: str | None,
        status: TaskStatus | None,
        created_from: datetime | None,
        created_before: datetime | None,
        page: int,
        page_size: int,
    ) -> tuple[int, list[TaskContentRecord]]:
        filters = []
        if workspace_type == "company":
            filters.extend(
                (
                    GenerationTask.company_id.is_not(None),
                    GenerationTask.personal_workspace_id.is_(None),
                )
            )
        elif workspace_type == "personal":
            filters.extend(
                (
                    GenerationTask.company_id.is_(None),
                    GenerationTask.personal_workspace_id.is_not(None),
                )
            )
        if task_id is not None:
            filters.append(GenerationTask.id == task_id)
        if user_id is not None:
            filters.append(GenerationTask.user_id == user_id)
        if company_id is not None:
            filters.append(GenerationTask.company_id == company_id)
        if personal_workspace_id is not None:
            filters.append(
                GenerationTask.personal_workspace_id == personal_workspace_id
            )
        if model_id is not None:
            filters.append(GenerationTask.model_id == model_id)
        if status is not None:
            filters.append(GenerationTask.status == status)
        if created_from is not None:
            filters.append(GenerationTask.created_at >= created_from)
        if created_before is not None:
            filters.append(GenerationTask.created_at < created_before)

        total = (
            session.scalar(select(func.count(GenerationTask.id)).where(*filters)) or 0
        )
        rows = session.execute(
            select(
                GenerationTask.id,
                GenerationTask.company_id,
                Company.name,
                GenerationTask.personal_workspace_id,
                GenerationTask.user_id,
                User.display_name,
                GenerationTask.model_id,
                ModelDefinition.display_name,
                GenerationTask.status,
                GenerationTask.request_payload["prompt"].as_string(),
                GenerationTask.created_at,
                GenerationTask.updated_at,
            )
            .join(User, User.id == GenerationTask.user_id)
            .join(ModelDefinition, ModelDefinition.id == GenerationTask.model_id)
            .outerjoin(Company, Company.id == GenerationTask.company_id)
            .where(*filters)
            .order_by(GenerationTask.created_at.desc(), GenerationTask.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return total, [
            TaskContentRecord(
                task_id=row[0],
                workspace_type="company" if row[1] is not None else "personal",
                company_id=row[1],
                company_name=row[2],
                personal_workspace_id=row[3],
                user_id=row[4],
                user_display_name=row[5],
                model_id=row[6],
                model_display_name=row[7],
                status=row[8],
                prompt=cls._prompt(row[9]),
                created_at=row[10],
                updated_at=row[11],
            )
            for row in rows
        ]
