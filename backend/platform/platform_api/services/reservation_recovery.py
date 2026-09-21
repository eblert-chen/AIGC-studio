from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import BillingUnit, GenerationTask, TaskStatus
from .billing import WalletService
from .company_points_billing import CompanyPointBillingService
from .errors import ConflictError, NotFoundError
from .personal_billing import PersonalWalletService

TERMINAL_STATUSES = (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED)


class ReservationRecoveryService:
    """Dispose reservations left behind by already-terminal tasks.

    `GenerationTask.status` carries two meanings at once: the business outcome
    and whether the matching ledger write happened. When those two do not land
    together, every ordinary path refuses to touch the task again:

    * `RelayStatusService.apply_to_locked_task` returns early for terminal tasks.
    * the timeout sweep reports `already_terminal` and skips them.
    * `settle_success` / `release_failure` reject any non QUEUED/PROCESSING task.

    The reservation then stays held forever, and `point_income` silently
    refuses to recognise revenue for it. This sweep is the only path that can
    close such a reservation, and it reuses the ordinary billing methods with
    `allow_terminal=True` so no ledger arithmetic is duplicated here.
    """

    @classmethod
    def pending_tasks(cls, session: Session, *, limit: int = 100) -> list[GenerationTask]:
        return list(
            session.scalars(
                select(GenerationTask)
                .where(
                    GenerationTask.status.in_(TERMINAL_STATUSES),
                    or_(
                        GenerationTask.reserved_points > 0,
                        GenerationTask.reserved_cents > 0,
                    ),
                )
                .order_by(GenerationTask.created_at, GenerationTask.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )

    @staticmethod
    def _idempotency_key(task: GenerationTask) -> str:
        # Reuse the ordinary terminal key whenever the relay job id is known, so
        # a late successful callback finds the recovery entry through the normal
        # idempotency check instead of charging the task a second time.
        if task.relay_job_id:
            return f"relay-terminal:{task.relay_job_id}:{task.status.value}"
        return f"reservation-recovery:{task.id}"

    @classmethod
    def _dispose(cls, session: Session, task: GenerationTask) -> str:
        personal_workspace_id = task.personal_workspace_id
        company_id = task.company_id
        if personal_workspace_id and not company_id:
            return cls._dispose_personal(session, task)
        if company_id and not personal_workspace_id:
            return cls._dispose_company(session, task)
        raise ConflictError("任务账务范围无效")

    @classmethod
    def _dispose_personal(cls, session: Session, task: GenerationTask) -> str:
        idempotency_key = cls._idempotency_key(task)
        workspace_id = str(task.personal_workspace_id)
        if task.status == TaskStatus.SUCCEEDED:
            amount = task.actual_cost_points
            if amount is None:
                amount = task.quote_points
            if amount is None:
                raise ConflictError("个人任务缺少可结算的积分报价")
            PersonalWalletService.settle_success(
                session,
                workspace_id=workspace_id,
                task_id=task.id,
                actual_cost_points=amount,
                idempotency_key=idempotency_key,
                allow_terminal=True,
            )
            return f"settled:{amount}"
        PersonalWalletService.release_failure(
            session,
            workspace_id=workspace_id,
            task_id=task.id,
            idempotency_key=idempotency_key,
            failure_reason="reservation-recovery",
            terminal_status=task.status,
            allow_terminal=True,
        )
        return f"released:{task.reserved_points}"

    @classmethod
    def _dispose_company(cls, session: Session, task: GenerationTask) -> str:
        idempotency_key = cls._idempotency_key(task)
        company_id = str(task.company_id)
        if task.status == TaskStatus.SUCCEEDED:
            if task.billing_unit == BillingUnit.POINT:
                amount = task.actual_cost_points
                if amount is None:
                    amount = task.quote_points
                if amount is None:
                    raise ConflictError("积分任务缺少可结算的报价")
                CompanyPointBillingService.settle_success(
                    session,
                    company_id=company_id,
                    task_id=task.id,
                    actual_cost_points=amount,
                    idempotency_key=idempotency_key,
                    allow_terminal=True,
                )
                return f"settled:{amount}"
            amount = task.actual_cost_cents
            if amount is None:
                amount = task.quote_cents
            if amount is None:
                raise ConflictError("现金任务缺少可结算的报价")
            WalletService.settle_success(
                session,
                company_id=company_id,
                task_id=task.id,
                actual_cost_cents=amount,
                idempotency_key=idempotency_key,
                allow_terminal=True,
            )
            return f"settled:{amount}"
        if task.billing_unit == BillingUnit.POINT:
            CompanyPointBillingService.release_failure(
                session,
                company_id=company_id,
                task_id=task.id,
                idempotency_key=idempotency_key,
                failure_reason="reservation-recovery",
                terminal_status=task.status,
                allow_terminal=True,
            )
            return f"released:{task.reserved_points}"
        WalletService.release_failure(
            session,
            company_id=company_id,
            task_id=task.id,
            idempotency_key=idempotency_key,
            failure_reason="reservation-recovery",
            terminal_status=task.status,
            allow_terminal=True,
        )
        return f"released:{task.reserved_cents}"

    @classmethod
    def run_once(cls, session: Session, *, limit: int = 100) -> dict:
        """Dispose every stuck reservation found in this pass.

        Each task is recovered inside its own savepoint so one unrecoverable
        row cannot stop the rest from being closed.
        """
        recovered: list[dict] = []
        failed: list[dict] = []
        for task in cls.pending_tasks(session, limit=limit):
            task_id = task.id
            try:
                with session.begin_nested():
                    outcome = cls._dispose(session, task)
            except (ConflictError, NotFoundError, ValueError) as error:
                failed.append({"task_id": task_id, "reason": str(error)})
                continue
            recovered.append({"task_id": task_id, "outcome": outcome})
        scanned = len(recovered) + len(failed)
        return {
            "processed": scanned > 0,
            "scanned": scanned,
            "recovered": recovered,
            "failed": failed,
        }
