from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    GenerationTask,
    RelayOutboxStatus,
    RelaySubmissionOutbox,
    TaskStatus,
)
from .billing import WalletService
from .errors import ConflictError, NotFoundError
from .relay_status import RelayStatusService

_CANCELLATION_REASON = "cancelled by creator before Relay submission"


@dataclass(frozen=True)
class GenerationCancellationResult:
    task: GenerationTask
    replayed: bool
    before_summary: dict[str, object]
    after_summary: dict[str, object]


class GenerationCancellationService:
    """Cancel only work that is provably still on the Platform side.

    The lock order intentionally matches callback/outbox terminal handling:
    wallet -> task -> outbox. A dispatcher claim that wins the outbox lock first
    changes it to PROCESSING, making cancellation fail closed before any money is
    released. A cancellation that wins makes the outbox terminal before a worker
    can claim it.
    """

    @staticmethod
    def _summary(
        task: GenerationTask, outbox: RelaySubmissionOutbox
    ) -> dict[str, object]:
        return {
            "task_status": task.status.value,
            "reserved_cents": task.reserved_cents,
            "relay_job_id": task.relay_job_id,
            "outbox_status": outbox.status.value,
            "relay_submit_attempted": outbox.relay_submit_attempted_at is not None,
            "submission_outcome_uncertain": (
                outbox.submission_outcome_uncertain_at is not None
            ),
        }

    @classmethod
    def cancel_unsubmitted(
        cls,
        session: Session,
        *,
        company_id: str,
        task_id: str,
        actor_user_id: str,
    ) -> GenerationCancellationResult:
        task = RelayStatusService.lock_wallet_and_task_for_update(
            session,
            company_id=company_id,
            task_id=task_id,
        )
        # Cancellation is deliberately creator-only. Company report access must
        # not silently become permission to mutate another member's spend.
        if task.user_id != actor_user_id:
            raise NotFoundError("Task does not exist in the current user scope")

        outbox = session.scalar(
            select(RelaySubmissionOutbox)
            .where(
                RelaySubmissionOutbox.company_id == company_id,
                RelaySubmissionOutbox.task_id == task_id,
            )
            .with_for_update()
        )
        if outbox is None:
            raise ConflictError("Task cancellation state is incomplete")

        before_summary = cls._summary(task, outbox)
        if (
            task.status == TaskStatus.CANCELLED
            and task.failure_reason == _CANCELLATION_REASON
            and task.reserved_cents == 0
            and outbox.status == RelayOutboxStatus.CANCELLED
        ):
            return GenerationCancellationResult(
                task=task,
                replayed=True,
                before_summary=before_summary,
                after_summary=before_summary,
            )

        safe_outbox_states = {
            RelayOutboxStatus.PENDING,
            RelayOutboxStatus.RETRY,
        }
        if (
            task.status != TaskStatus.QUEUED
            or task.relay_job_id is not None
            or outbox.status not in safe_outbox_states
            or outbox.relay_job_id is not None
            or outbox.relay_submit_attempted_at is not None
            or outbox.submission_outcome_uncertain_at is not None
        ):
            raise ConflictError(
                "Task may already have reached Relay or a provider; cancellation "
                "cannot safely release the reserved balance"
            )

        WalletService.release_failure(
            session,
            company_id=company_id,
            task_id=task_id,
            idempotency_key=f"generation-cancel:{task_id}",
            failure_reason=_CANCELLATION_REASON,
            terminal_status=TaskStatus.CANCELLED,
        )
        outbox.status = RelayOutboxStatus.CANCELLED
        outbox.last_error = _CANCELLATION_REASON
        session.flush()
        return GenerationCancellationResult(
            task=task,
            replayed=False,
            before_summary=before_summary,
            after_summary=cls._summary(task, outbox),
        )
