from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    GenerationTask,
    PersonalWalletAccount,
    TaskStatus,
    WalletAccount,
)
from ..relay_client import RelayArtifact, expected_reservation_action
from .billing import WalletService
from .personal_billing import PersonalWalletService
from .artifacts import TaskArtifactService
from .errors import ConflictError, NotFoundError


class RelayStatusService:
    _TERMINAL_STATUSES = frozenset(
        {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    )
    _OUTPUT_MEDIA_TYPE_BY_MODE = {
        "text_to_image": "image",
        "text_to_video": "video",
        "image_to_video": "video",
        "video_to_video": "video",
    }

    @classmethod
    def _validated_success_artifacts(
        cls,
        task: GenerationTask,
        outputs: list[RelayArtifact] | None,
    ) -> list[dict]:
        request_payload = task.request_payload or {}
        requested_count = request_payload.get("output_count", 1)
        if (
            isinstance(requested_count, bool)
            or not isinstance(requested_count, int)
            or requested_count <= 0
        ):
            raise ConflictError("Task output_count snapshot is invalid")

        delivered_outputs = outputs or []
        if len(delivered_outputs) != requested_count:
            raise ConflictError(
                "Relay succeeded artifact count does not match the task request"
            )

        mode = request_payload.get("mode", "text_to_video")
        expected_media_type = (
            cls._OUTPUT_MEDIA_TYPE_BY_MODE.get(mode) if isinstance(mode, str) else None
        )
        if expected_media_type is None:
            raise ConflictError("Task generation mode snapshot is invalid")
        if any(
            output.media_type != expected_media_type for output in delivered_outputs
        ):
            raise ConflictError(
                "Relay succeeded artifact media type does not match the task mode"
            )

        return [output.safe_metadata() for output in delivered_outputs]

    @staticmethod
    def target_status(status: str) -> TaskStatus:
        normalized_status = (
            "processing"
            if status in {"submitting", "reconciliation_required", "transferring"}
            else status
        )
        try:
            return TaskStatus(normalized_status)
        except ValueError as exc:
            raise ConflictError("不支持的中转站任务状态") from exc

    @classmethod
    def is_terminal_status(cls, status: TaskStatus) -> bool:
        return status in cls._TERMINAL_STATUSES

    @staticmethod
    def lock_task_for_scope(
        session: Session,
        *,
        company_id: str | None,
        personal_workspace_id: str | None,
        task_id: str,
    ) -> GenerationTask:
        if (company_id is None) == (personal_workspace_id is None):
            raise NotFoundError("任务账务范围无效")
        statement = select(GenerationTask).where(GenerationTask.id == task_id)
        if company_id is not None:
            statement = statement.where(
                GenerationTask.company_id == company_id,
                GenerationTask.personal_workspace_id.is_(None),
            )
        else:
            statement = statement.where(
                GenerationTask.company_id.is_(None),
                GenerationTask.personal_workspace_id == personal_workspace_id,
            )
        task = session.scalar(statement.with_for_update())
        if task is None:
            raise NotFoundError("当前账务范围下不存在匹配的中转站任务")
        return task

    @staticmethod
    def lock_task_for_update(
        session: Session, *, company_id: str, task_id: str
    ) -> GenerationTask:
        return RelayStatusService.lock_task_for_scope(
            session,
            company_id=company_id,
            personal_workspace_id=None,
            task_id=task_id,
        )

    @classmethod
    def lock_wallet_and_task_for_scope(
        cls,
        session: Session,
        *,
        company_id: str | None,
        personal_workspace_id: str | None,
        task_id: str,
    ) -> GenerationTask:
        """Acquire the applicable wallet then task using one global lock order."""
        if (company_id is None) == (personal_workspace_id is None):
            raise NotFoundError("任务账务范围无效")
        if company_id is not None:
            wallet = session.scalar(
                select(WalletAccount)
                .where(WalletAccount.company_id == company_id)
                .with_for_update()
            )
            if wallet is None:
                raise NotFoundError("公司钱包不存在")
        else:
            wallet = session.scalar(
                select(PersonalWalletAccount)
                .where(PersonalWalletAccount.workspace_id == personal_workspace_id)
                .with_for_update()
            )
            if wallet is None:
                raise NotFoundError("个人积分账户不存在")
        return cls.lock_task_for_scope(
            session,
            company_id=company_id,
            personal_workspace_id=personal_workspace_id,
            task_id=task_id,
        )

    @classmethod
    def lock_wallet_and_task_for_update(
        cls, session: Session, *, company_id: str, task_id: str
    ) -> GenerationTask:
        """Acquire billing locks in the single global wallet -> task order."""
        return cls.lock_wallet_and_task_for_scope(
            session,
            company_id=company_id,
            personal_workspace_id=None,
            task_id=task_id,
        )

    @classmethod
    def apply(
        cls,
        session: Session,
        *,
        company_id: str | None,
        personal_workspace_id: str | None = None,
        task_id: str,
        relay_job_id: str,
        status: str,
        outputs: list[RelayArtifact] | None = None,
        failure_reason: str = "",
        error_snapshot: dict | None = None,
        reservation_action: str | None = None,
    ) -> GenerationTask:
        target_status = cls.target_status(status)
        cls._validate_reservation_action(status, reservation_action)
        if cls.is_terminal_status(target_status):
            task = cls.lock_wallet_and_task_for_scope(
                session,
                company_id=company_id,
                personal_workspace_id=personal_workspace_id,
                task_id=task_id,
            )
        else:
            task = cls.lock_task_for_scope(
                session,
                company_id=company_id,
                personal_workspace_id=personal_workspace_id,
                task_id=task_id,
            )
        return cls.apply_to_locked_task(
            session,
            task=task,
            company_id=company_id,
            task_id=task_id,
            relay_job_id=relay_job_id,
            target_status=target_status,
            outputs=outputs,
            failure_reason=failure_reason,
            error_snapshot=error_snapshot,
            reservation_action=reservation_action,
            personal_workspace_id=personal_workspace_id,
        )

    @staticmethod
    def _validate_reservation_action(
        relay_status: str, reservation_action: str | None
    ) -> None:
        if reservation_action is None:
            return
        try:
            expected = expected_reservation_action(relay_status)
        except ValueError as exc:
            raise ConflictError("Unsupported Relay job status") from exc
        if reservation_action != expected:
            raise ConflictError(
                "Relay reservation_action does not match the job status"
            )

    @classmethod
    def apply_to_locked_task(
        cls,
        session: Session,
        *,
        task: GenerationTask,
        company_id: str | None,
        task_id: str,
        relay_job_id: str,
        target_status: TaskStatus,
        outputs: list[RelayArtifact] | None = None,
        failure_reason: str = "",
        error_snapshot: dict | None = None,
        reservation_action: str | None = None,
        personal_workspace_id: str | None = None,
    ) -> GenerationTask:
        """Apply a status after the caller acquired wallet (if terminal) then task."""
        if (company_id is None) == (personal_workspace_id is None):
            raise NotFoundError("任务账务范围无效")
        if (
            task.id != task_id
            or task.company_id != company_id
            or task.personal_workspace_id != personal_workspace_id
            or task.relay_job_id != relay_job_id
        ):
            raise NotFoundError("当前公司下不存在匹配的中转站任务")

        cls._validate_reservation_action(target_status.value, reservation_action)
        if task.status in cls._TERMINAL_STATUSES:
            if target_status not in cls._TERMINAL_STATUSES:
                # Polling and at-least-once callbacks can race. A late active
                # observation is acknowledged as stale without regressing the
                # authoritative terminal state or creating callback dead-letter.
                return task
            if task.status != target_status:
                raise ConflictError("任务已经进入不同的终态")
            if target_status == TaskStatus.SUCCEEDED and outputs is not None:
                repeated_artifacts = cls._validated_success_artifacts(task, outputs)
                if repeated_artifacts != task.output_artifacts:
                    raise ConflictError("任务终态产物与已保存的产物记录不一致")
                TaskArtifactService.persist_success_artifacts(
                    session, task=task, artifacts=repeated_artifacts
                )
            return task

        if target_status in {TaskStatus.QUEUED, TaskStatus.PROCESSING}:
            if (
                task.status == TaskStatus.PROCESSING
                and target_status == TaskStatus.QUEUED
            ):
                return task
            task.status = target_status
            task.relay_error_snapshot = error_snapshot
            session.flush()
            return task
        if target_status == TaskStatus.SUCCEEDED:
            if not outputs:
                raise ConflictError("中转站任务必须至少包含一个已转存产物才能成功")
            task.output_artifacts = cls._validated_success_artifacts(task, outputs)
            TaskArtifactService.persist_success_artifacts(
                session, task=task, artifacts=task.output_artifacts
            )
            task.relay_error_snapshot = None
            if company_id is not None:
                if task.quote_cents is None:
                    raise ConflictError("公司任务报价快照无效")
                WalletService.settle_success(
                    session,
                    company_id=company_id,
                    task_id=task_id,
                    actual_cost_cents=task.quote_cents,
                    idempotency_key=f"relay-terminal:{relay_job_id}:succeeded",
                )
            else:
                if task.quote_points is None or personal_workspace_id is None:
                    raise ConflictError("个人任务积分报价快照无效")
                PersonalWalletService.settle_success(
                    session,
                    workspace_id=personal_workspace_id,
                    task_id=task_id,
                    actual_cost_points=task.quote_points,
                    idempotency_key=f"relay-terminal:{relay_job_id}:succeeded",
                )
            return task
        if target_status in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            task.relay_error_snapshot = error_snapshot
            if company_id is not None:
                WalletService.release_failure(
                    session,
                    company_id=company_id,
                    task_id=task_id,
                    idempotency_key=f"relay-terminal:{relay_job_id}:{target_status.value}",
                    failure_reason=failure_reason or f"relay {target_status.value}",
                    terminal_status=target_status,
                )
            else:
                assert personal_workspace_id is not None
                PersonalWalletService.release_failure(
                    session,
                    workspace_id=personal_workspace_id,
                    task_id=task_id,
                    idempotency_key=f"relay-terminal:{relay_job_id}:{target_status.value}",
                    failure_reason=failure_reason or f"relay {target_status.value}",
                    terminal_status=target_status,
                )
            return task
        raise ConflictError("不支持的中转站任务状态")
