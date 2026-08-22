from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..models import (
    DownloadCompletion,
    DownloadRecord,
    GenerationTask,
    ModelDefinition,
    TaskArtifact,
    TaskStatus,
    User,
    utcnow,
)
from .errors import ConflictError
from .download_completion_trust import verified_download_completion_clause


class TaskArtifactService:
    """Persist and query the Platform's safe, immutable artifact index."""

    @staticmethod
    def _snapshot(row: TaskArtifact) -> dict[str, Any]:
        return {
            "asset_id": row.asset_id,
            "media_type": row.media_type,
            "content_type": row.content_type,
            "size_bytes": row.size_bytes,
            "sha256": row.sha256,
        }

    @classmethod
    def persist_success_artifacts(
        cls,
        session: Session,
        *,
        task: GenerationTask,
        artifacts: list[dict[str, Any]],
    ) -> list[TaskArtifact]:
        existing = list(
            session.scalars(
                select(TaskArtifact)
                .where(TaskArtifact.task_id == task.id)
                .order_by(TaskArtifact.position)
            ).all()
        )
        if existing:
            if [cls._snapshot(row) for row in existing] != artifacts:
                raise ConflictError("任务产物索引与已保存的终态产物不一致")
            return existing

        created_at = utcnow()
        rows = [
            TaskArtifact(
                company_id=task.company_id,
                personal_workspace_id=task.personal_workspace_id,
                task_id=task.id,
                asset_id=artifact["asset_id"],
                position=position,
                media_type=artifact["media_type"],
                content_type=artifact["content_type"],
                size_bytes=artifact["size_bytes"],
                sha256=artifact["sha256"],
                created_at=created_at,
            )
            for position, artifact in enumerate(artifacts)
        ]
        session.add_all(rows)
        session.flush()
        return rows

    @staticmethod
    def _artifact_download_counts():
        return (
            select(
                DownloadRecord.task_id.label("download_task_id"),
                DownloadRecord.asset_id.label("download_asset_id"),
                func.count(DownloadRecord.id).label("download_issue_count"),
                func.count(DownloadCompletion.id).label(
                    "download_completed_count"
                ),
                func.max(DownloadRecord.created_at).label(
                    "last_download_issued_at"
                ),
                func.max(DownloadCompletion.completed_at).label(
                    "last_download_completed_at"
                ),
            )
            .outerjoin(
                DownloadCompletion,
                and_(
                    DownloadCompletion.download_record_id == DownloadRecord.id,
                    verified_download_completion_clause(),
                ),
            )
            .group_by(DownloadRecord.task_id, DownloadRecord.asset_id)
            .subquery("artifact_download_counts")
        )

    @staticmethod
    def _task_download_counts():
        return (
            select(
                DownloadRecord.task_id.label("download_task_id"),
                func.count(DownloadRecord.id).label("download_issue_count"),
                func.count(DownloadCompletion.id).label(
                    "download_completed_count"
                ),
                func.max(DownloadRecord.created_at).label(
                    "last_download_issued_at"
                ),
                func.max(DownloadCompletion.completed_at).label(
                    "last_download_completed_at"
                ),
            )
            .outerjoin(
                DownloadCompletion,
                and_(
                    DownloadCompletion.download_record_id == DownloadRecord.id,
                    verified_download_completion_clause(),
                ),
            )
            .group_by(DownloadRecord.task_id)
            .subquery("task_download_counts")
        )

    @classmethod
    def task_history_page(
        cls,
        session: Session,
        *,
        company_id: str,
        visible_user_id: str | None,
        page: int,
        page_size: int,
        employee_user_id: str | None = None,
        model_id: str | None = None,
        status: TaskStatus | None = None,
        media_type: str | None = None,
        query: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        artifact_counts = (
            select(
                TaskArtifact.task_id.label("artifact_task_id"),
                func.count(TaskArtifact.id).label("artifact_count"),
            )
            .group_by(TaskArtifact.task_id)
            .subquery("task_artifact_counts")
        )
        download_counts = cls._task_download_counts()
        statement = (
            select(
                GenerationTask.id,
                GenerationTask.company_id,
                GenerationTask.user_id,
                User.display_name.label("user_display_name"),
                User.email.label("user_email"),
                GenerationTask.model_id,
                ModelDefinition.display_name.label("model_display_name"),
                GenerationTask.status,
                GenerationTask.request_payload,
                GenerationTask.quote_cents,
                GenerationTask.pricing_snapshot,
                GenerationTask.capability_snapshot,
                GenerationTask.reserved_cents,
                GenerationTask.actual_cost_cents,
                GenerationTask.output_artifacts,
                func.coalesce(artifact_counts.c.artifact_count, 0).label(
                    "artifact_count"
                ),
                func.coalesce(download_counts.c.download_issue_count, 0).label(
                    "download_issue_count"
                ),
                func.coalesce(
                    download_counts.c.download_completed_count, 0
                ).label("download_completed_count"),
                download_counts.c.last_download_issued_at,
                download_counts.c.last_download_completed_at,
                GenerationTask.failure_reason,
                GenerationTask.created_at,
                GenerationTask.updated_at,
            )
            .join(User, User.id == GenerationTask.user_id)
            .join(ModelDefinition, ModelDefinition.id == GenerationTask.model_id)
            .outerjoin(
                artifact_counts,
                artifact_counts.c.artifact_task_id == GenerationTask.id,
            )
            .outerjoin(
                download_counts,
                download_counts.c.download_task_id == GenerationTask.id,
            )
            .where(GenerationTask.company_id == company_id)
        )
        if visible_user_id is not None:
            statement = statement.where(GenerationTask.user_id == visible_user_id)
        if employee_user_id is not None:
            statement = statement.where(GenerationTask.user_id == employee_user_id)
        if model_id is not None:
            statement = statement.where(GenerationTask.model_id == model_id)
        if status is not None:
            statement = statement.where(GenerationTask.status == status)
        if media_type is not None:
            expected_mode = "text_to_image"
            mode_expression = GenerationTask.request_payload["mode"].as_string()
            statement = statement.where(
                mode_expression == expected_mode
                if media_type == "image"
                else mode_expression != expected_mode
            )
        if query is not None:
            normalized_query = query.strip().lower()
            if normalized_query:
                search_pattern = f"%{normalized_query}%"
                statement = statement.where(
                    func.lower(GenerationTask.request_payload["prompt"].as_string()).like(search_pattern)
                    | func.lower(ModelDefinition.display_name).like(search_pattern)
                    | func.lower(GenerationTask.id).like(search_pattern)
                )
        if start_time is not None:
            statement = statement.where(GenerationTask.created_at >= start_time)
        if end_time is not None:
            statement = statement.where(GenerationTask.created_at < end_time)

        total = int(
            session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        )
        rows = list(session.execute(
            statement.order_by(
                GenerationTask.created_at.desc(), GenerationTask.id.desc()
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).mappings())
        canonical_by_task: dict[str, list[dict[str, Any]]] = {}
        task_ids = [row["id"] for row in rows]
        if task_ids:
            for artifact in session.scalars(
                select(TaskArtifact)
                .where(TaskArtifact.task_id.in_(task_ids))
                .order_by(TaskArtifact.task_id, TaskArtifact.position)
            ):
                canonical_by_task.setdefault(artifact.task_id, []).append(
                    {
                        "artifact_id": artifact.id,
                        **cls._snapshot(artifact),
                    }
                )
        items = []
        for row in rows:
            item = dict(row)
            item["artifact_count"] = int(item["artifact_count"] or 0)
            item["download_issue_count"] = int(
                item["download_issue_count"] or 0
            )
            item["download_completed_count"] = int(
                item["download_completed_count"] or 0
            )
            item["downloaded"] = item["download_completed_count"] > 0
            if item["id"] in canonical_by_task:
                item["output_artifacts"] = canonical_by_task[item["id"]]
            items.append(item)
        return total, items

    @classmethod
    def artwork_page(
        cls,
        session: Session,
        *,
        company_id: str,
        visible_user_id: str | None,
        page: int,
        page_size: int,
        employee_user_id: str | None = None,
        model_id: str | None = None,
        media_type: str | None = None,
        downloaded: bool | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        download_counts = cls._artifact_download_counts()
        completed_count = func.coalesce(
            download_counts.c.download_completed_count, 0
        )
        statement = (
            select(
                TaskArtifact.id.label("artifact_id"),
                TaskArtifact.task_id,
                TaskArtifact.company_id,
                TaskArtifact.asset_id,
                TaskArtifact.position.label("output_index"),
                TaskArtifact.media_type,
                TaskArtifact.content_type,
                TaskArtifact.size_bytes,
                TaskArtifact.sha256,
                GenerationTask.user_id.label("created_by_user_id"),
                User.display_name.label("created_by_display_name"),
                User.email.label("created_by_email"),
                GenerationTask.model_id,
                ModelDefinition.display_name.label("model_display_name"),
                GenerationTask.request_payload,
                GenerationTask.actual_cost_cents,
                func.coalesce(
                    download_counts.c.download_issue_count, 0
                ).label("download_issue_count"),
                completed_count.label("download_completed_count"),
                download_counts.c.last_download_issued_at,
                download_counts.c.last_download_completed_at,
                TaskArtifact.created_at,
            )
            .join(GenerationTask, GenerationTask.id == TaskArtifact.task_id)
            .join(User, User.id == GenerationTask.user_id)
            .join(ModelDefinition, ModelDefinition.id == GenerationTask.model_id)
            .outerjoin(
                download_counts,
                (download_counts.c.download_task_id == TaskArtifact.task_id)
                & (download_counts.c.download_asset_id == TaskArtifact.asset_id),
            )
            .where(
                TaskArtifact.company_id == company_id,
                GenerationTask.company_id == company_id,
                GenerationTask.status == TaskStatus.SUCCEEDED,
                GenerationTask.actual_cost_cents.is_not(None),
            )
        )
        if visible_user_id is not None:
            statement = statement.where(GenerationTask.user_id == visible_user_id)
        if employee_user_id is not None:
            statement = statement.where(GenerationTask.user_id == employee_user_id)
        if model_id is not None:
            statement = statement.where(GenerationTask.model_id == model_id)
        if media_type is not None:
            statement = statement.where(TaskArtifact.media_type == media_type)
        if downloaded is True:
            statement = statement.where(completed_count > 0)
        elif downloaded is False:
            statement = statement.where(completed_count == 0)
        if start_time is not None:
            statement = statement.where(TaskArtifact.created_at >= start_time)
        if end_time is not None:
            statement = statement.where(TaskArtifact.created_at < end_time)

        total = int(
            session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        )
        rows = session.execute(
            statement.order_by(
                TaskArtifact.created_at.desc(),
                TaskArtifact.task_id.desc(),
                TaskArtifact.position,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).mappings()
        items = []
        for row in rows:
            item = dict(row)
            item["actual_cost_cents"] = int(item["actual_cost_cents"])
            item["download_issue_count"] = int(
                item["download_issue_count"] or 0
            )
            item["download_completed_count"] = int(
                item["download_completed_count"] or 0
            )
            item["downloaded"] = item["download_completed_count"] > 0
            items.append(item)
        return total, items
