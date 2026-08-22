from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from ..models import (
    GenerationTask,
    LedgerEntry,
    PersonalLedgerEntry,
    RelayOutboxStatus,
    RelaySubmissionOutbox,
    TaskStatus,
    TaskTimeoutEvent,
    utcnow,
)
from ..relay_client import RelayClient, RelayClientError, RelayJobSnapshot
from ..relay_backends import (
    RelayBackendRegistry,
    RelayBackendResolutionError,
    coerce_relay_backend_registry,
)
from .billing import WalletService
from .personal_billing import PersonalWalletService
from .relay_status import RelayStatusService

logger = logging.getLogger("platform.task_timeouts")


def _as_utc(value: datetime) -> datetime:
    """Normalize timestamps loaded from dialects that discard timezone data."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class TimeoutCandidate:
    company_id: str | None
    personal_workspace_id: str | None
    task_id: str
    status: TaskStatus
    relay_job_id: str | None
    capability_revision: str | None
    relay_backend_id: str
    relay_contract_revision: str


@dataclass(frozen=True)
class TimeoutScanItem:
    task_id: str
    previous_status: str
    outcome: str
    reason: str
    final_status: str | None = None
    released_cents: int = 0
    released_points: int = 0


@dataclass
class TimeoutScanResult:
    scanned: int = 0
    compensated: int = 0
    reconciled: int = 0
    deferred: int = 0
    items: list[TimeoutScanItem] = field(default_factory=list)

    def add(self, item: TimeoutScanItem) -> None:
        self.scanned += 1
        self.items.append(item)
        if item.outcome == "timeout_released":
            self.compensated += 1
        elif item.outcome.startswith("relay_"):
            self.reconciled += 1
            if item.outcome in {"relay_failed", "relay_cancelled"}:
                self.compensated += 1
        else:
            self.deferred += 1


class TaskTimeoutService:
    """Safely reconcile or compensate tasks older than their runtime budget.

    A timeout is only released without consulting Relay when its outbox row is
    still PENDING with zero attempts and no relay job id exists. RETRY and
    PROCESSING rows may represent a request accepted upstream before a network
    failure, so their outcome is unknown and their reservation is preserved.
    Once a relay job id exists, Relay remains authoritative: succeeded jobs
    settle, failed/cancelled jobs release, and non-terminal/unreachable jobs
    defer.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        relay_client: RelayClient | RelayBackendRegistry | None,
        *,
        queued_timeout_seconds: int = 3600,
        processing_timeout_seconds: int = 21600,
        batch_size: int = 100,
    ):
        if queued_timeout_seconds < 1:
            raise ValueError("queued_timeout_seconds must be positive")
        if processing_timeout_seconds < 1:
            raise ValueError("processing_timeout_seconds must be positive")
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.session_factory = session_factory
        self.relay_backends = coerce_relay_backend_registry(relay_client)
        self.queued_timeout_seconds = queued_timeout_seconds
        self.processing_timeout_seconds = processing_timeout_seconds
        self.batch_size = batch_size

    def scan_once(self, *, now: datetime | None = None) -> TimeoutScanResult:
        observed_at = now or utcnow()
        queued_cutoff = observed_at - timedelta(seconds=self.queued_timeout_seconds)
        processing_cutoff = observed_at - timedelta(
            seconds=self.processing_timeout_seconds
        )
        with self.session_factory() as session:
            rows = session.execute(
                select(
                    GenerationTask.company_id,
                    GenerationTask.personal_workspace_id,
                    GenerationTask.id,
                    GenerationTask.status,
                    GenerationTask.relay_job_id,
                    GenerationTask.capability_snapshot,
                    GenerationTask.relay_backend_id,
                    GenerationTask.relay_contract_revision,
                )
                .where(
                    or_(
                        and_(
                            GenerationTask.status == TaskStatus.QUEUED,
                            GenerationTask.created_at <= queued_cutoff,
                        ),
                        and_(
                            GenerationTask.status == TaskStatus.PROCESSING,
                            GenerationTask.created_at <= processing_cutoff,
                        ),
                    )
                )
                .order_by(
                    GenerationTask.timeout_checked_at.asc().nulls_first(),
                    GenerationTask.created_at,
                    GenerationTask.id,
                )
                .limit(self.batch_size)
            ).all()
        candidates = [
            TimeoutCandidate(
                company_id,
                personal_workspace_id,
                task_id,
                status,
                relay_job_id,
                (capability_snapshot or {}).get("relay_capability_revision"),
                relay_backend_id,
                relay_contract_revision,
            )
            for (
                company_id,
                personal_workspace_id,
                task_id,
                status,
                relay_job_id,
                capability_snapshot,
                relay_backend_id,
                relay_contract_revision,
            ) in rows
        ]

        result = TimeoutScanResult()
        for candidate in candidates:
            try:
                if candidate.relay_job_id:
                    item = self._reconcile_relay(candidate)
                else:
                    item = self._release_undispatched(
                        candidate,
                        cutoff=(
                            queued_cutoff
                            if candidate.status == TaskStatus.QUEUED
                            else processing_cutoff
                        ),
                    )
            except Exception as exc:
                # Keep one corrupt or concurrently changing task from stopping
                # the whole batch. Do not expose third-party/DSN details.
                logger.error(
                    "task timeout scan failed task_id=%s error_type=%s",
                    candidate.task_id,
                    type(exc).__name__,
                )
                item = TimeoutScanItem(
                    task_id=candidate.task_id,
                    previous_status=candidate.status.value,
                    outcome="deferred_internal_error",
                    reason=f"timeout scan failed ({type(exc).__name__})",
                )
            result.add(item)
            if item.outcome.startswith("deferred_"):
                logger.warning(
                    "task timeout deferred task_id=%s outcome=%s",
                    item.task_id,
                    item.outcome,
                )

        if candidates:
            with self.session_factory.begin() as session:
                session.execute(
                    update(GenerationTask)
                    .where(
                        GenerationTask.id.in_(
                            [candidate.task_id for candidate in candidates]
                        ),
                        GenerationTask.status.in_(
                            [TaskStatus.QUEUED, TaskStatus.PROCESSING]
                        ),
                    )
                    .values(timeout_checked_at=observed_at)
                )

        logger.info(
            "task timeout scan completed scanned=%s compensated=%s "
            "reconciled=%s deferred=%s",
            result.scanned,
            result.compensated,
            result.reconciled,
            result.deferred,
        )
        return result

    def _release_undispatched(
        self, candidate: TimeoutCandidate, *, cutoff: datetime
    ) -> TimeoutScanItem:
        with self.session_factory.begin() as session:
            # Scope wallet -> task is the same lock order used by terminal
            # callbacks. Taking outbox last prevents release racing dispatch.
            task = RelayStatusService.lock_wallet_and_task_for_scope(
                session,
                company_id=candidate.company_id,
                personal_workspace_id=candidate.personal_workspace_id,
                task_id=candidate.task_id,
            )
            if (
                task.status != TaskStatus.QUEUED
                or task.relay_job_id is not None
                or _as_utc(task.created_at) > _as_utc(cutoff)
            ):
                return TimeoutScanItem(
                    task_id=candidate.task_id,
                    previous_status=candidate.status.value,
                    outcome="deferred_concurrent_change",
                    reason="task changed while timeout scan was running",
                )
            outbox = session.scalar(
                select(RelaySubmissionOutbox)
                .where(
                    RelaySubmissionOutbox.task_id == task.id,
                    RelaySubmissionOutbox.company_id == task.company_id,
                    RelaySubmissionOutbox.personal_workspace_id
                    == task.personal_workspace_id,
                )
                .with_for_update()
            )
            if (
                outbox is None
                or outbox.status != RelayOutboxStatus.PENDING
                or outbox.attempt_count != 0
            ):
                state = outbox.status.value if outbox is not None else "missing"
                attempts = outbox.attempt_count if outbox is not None else "n/a"
                return TimeoutScanItem(
                    task_id=task.id,
                    previous_status=task.status.value,
                    outcome="deferred_unsafe_submission",
                    reason=(
                        "relay submission outcome is not safe to compensate "
                        f"(outbox={state}, attempts={attempts})"
                    ),
                )

            previous_status = task.status
            reason = (
                "platform_timeout: queued task was never dispatched to Relay "
                f"within {self.queued_timeout_seconds} seconds"
            )
            if task.company_id is not None:
                _, ledger = WalletService.release_failure(
                    session,
                    company_id=task.company_id,
                    task_id=task.id,
                    idempotency_key=f"task-timeout:{task.id}:undispatched",
                    failure_reason=reason,
                )
                released_cents = ledger.amount_cents
                released_points = 0
                company_ledger_id = ledger.id
                personal_ledger_id = None
            else:
                if task.personal_workspace_id is None:
                    raise RuntimeError("personal timeout task has no workspace")
                _, ledger = PersonalWalletService.release_failure(
                    session,
                    workspace_id=task.personal_workspace_id,
                    task_id=task.id,
                    idempotency_key=f"task-timeout:{task.id}:undispatched",
                    failure_reason=reason,
                )
                released_cents = 0
                released_points = ledger.amount_points
                company_ledger_id = None
                personal_ledger_id = ledger.id
            outbox.status = RelayOutboxStatus.PERMANENTLY_FAILED
            outbox.last_error = reason
            outbox.next_attempt_at = utcnow()
            event = TaskTimeoutEvent(
                company_id=task.company_id,
                personal_workspace_id=task.personal_workspace_id,
                task_id=task.id,
                previous_status=previous_status.value,
                final_status=TaskStatus.FAILED.value,
                outcome="timeout_released",
                reason=reason,
                released_cents=released_cents,
                released_points=released_points,
                ledger_entry_id=company_ledger_id,
                personal_ledger_entry_id=personal_ledger_id,
                relay_job_id=None,
            )
            session.add(event)
            session.flush()
            return TimeoutScanItem(
                task_id=task.id,
                previous_status=previous_status.value,
                outcome=event.outcome,
                reason=reason,
                final_status=event.final_status,
                released_cents=event.released_cents,
                released_points=event.released_points,
            )

    def _reconcile_relay(self, candidate: TimeoutCandidate) -> TimeoutScanItem:
        try:
            client = self.relay_backends.resolve(
                backend_id=candidate.relay_backend_id,
                contract_revision=candidate.relay_contract_revision,
            )
        except RelayBackendResolutionError:
            return TimeoutScanItem(
                task_id=candidate.task_id,
                previous_status=candidate.status.value,
                outcome="deferred_relay_unavailable",
                reason="task-bound relay backend is not configured",
            )
        relay_job_id = candidate.relay_job_id
        assert relay_job_id is not None
        try:
            snapshot = client.get(relay_job_id)
        except RelayClientError as exc:
            return TimeoutScanItem(
                task_id=candidate.task_id,
                previous_status=candidate.status.value,
                outcome="deferred_relay_query",
                reason=f"relay status query failed ({type(exc).__name__})",
            )
        if snapshot.id != relay_job_id:
            return TimeoutScanItem(
                task_id=candidate.task_id,
                previous_status=candidate.status.value,
                outcome="deferred_relay_mismatch",
                reason="relay returned a mismatched job id",
            )
        if snapshot.client_reference_id != candidate.task_id:
            return TimeoutScanItem(
                task_id=candidate.task_id,
                previous_status=candidate.status.value,
                outcome="deferred_relay_mismatch",
                reason="relay returned a mismatched client reference",
            )
        if snapshot.expected_capability_revision != candidate.capability_revision:
            return TimeoutScanItem(
                task_id=candidate.task_id,
                previous_status=candidate.status.value,
                outcome="deferred_relay_mismatch",
                reason="relay returned a mismatched capability revision",
            )
        normalized_status = (
            "processing"
            if snapshot.status
            in {"submitting", "reconciliation_required", "transferring"}
            else snapshot.status
        )
        if normalized_status not in {"succeeded", "failed", "cancelled"}:
            return TimeoutScanItem(
                task_id=candidate.task_id,
                previous_status=candidate.status.value,
                outcome="deferred_relay_active",
                reason=f"relay job remains non-terminal ({normalized_status})",
            )
        return self._apply_relay_terminal(candidate, snapshot)

    def _apply_relay_terminal(
        self, candidate: TimeoutCandidate, snapshot: RelayJobSnapshot
    ) -> TimeoutScanItem:
        relay_job_id = candidate.relay_job_id
        assert relay_job_id is not None
        normalized_status = snapshot.status
        with self.session_factory.begin() as session:
            target_status = RelayStatusService.target_status(normalized_status)
            task = RelayStatusService.lock_wallet_and_task_for_scope(
                session,
                company_id=candidate.company_id,
                personal_workspace_id=candidate.personal_workspace_id,
                task_id=candidate.task_id,
            )
            if task.relay_job_id != relay_job_id:
                return TimeoutScanItem(
                    task_id=candidate.task_id,
                    previous_status=candidate.status.value,
                    outcome="deferred_concurrent_change",
                    reason="task relay identity changed during timeout scan",
                )
            if task.status in {
                TaskStatus.SUCCEEDED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }:
                return TimeoutScanItem(
                    task_id=task.id,
                    previous_status=candidate.status.value,
                    outcome="already_terminal",
                    reason="another worker completed the task first",
                    final_status=task.status.value,
                )
            previous_status = task.status
            error_message = ""
            error_snapshot = None
            if snapshot.error is not None:
                error_message = snapshot.error.message
                error_snapshot = {
                    **snapshot.error.model_dump(mode="json"),
                    "source": "poll",
                }
            RelayStatusService.apply_to_locked_task(
                session,
                task=task,
                company_id=task.company_id,
                task_id=task.id,
                relay_job_id=relay_job_id,
                target_status=target_status,
                outputs=snapshot.outputs,
                failure_reason=error_message,
                error_snapshot=error_snapshot,
                reservation_action=snapshot.reservation_action,
                personal_workspace_id=task.personal_workspace_id,
            )
            terminal_status = target_status
            ledger_key = f"relay-terminal:{relay_job_id}:{terminal_status.value}"
            if task.company_id is not None:
                ledger = session.scalar(
                    select(LedgerEntry).where(
                        LedgerEntry.company_id == task.company_id,
                        LedgerEntry.idempotency_key == ledger_key,
                    )
                )
                personal_ledger = None
                released_cents = (
                    ledger.amount_cents
                    if ledger is not None
                    and terminal_status in {TaskStatus.FAILED, TaskStatus.CANCELLED}
                    else 0
                )
                released_points = 0
            else:
                ledger = None
                personal_ledger = session.scalar(
                    select(PersonalLedgerEntry).where(
                        PersonalLedgerEntry.workspace_id == task.personal_workspace_id,
                        PersonalLedgerEntry.idempotency_key == ledger_key,
                    )
                )
                released_cents = 0
                released_points = (
                    personal_ledger.amount_points
                    if personal_ledger is not None
                    and terminal_status in {TaskStatus.FAILED, TaskStatus.CANCELLED}
                    else 0
                )
            reason = (
                "timeout scan reconciled authoritative Relay state "
                f"{terminal_status.value}"
            )
            event = TaskTimeoutEvent(
                company_id=task.company_id,
                personal_workspace_id=task.personal_workspace_id,
                task_id=task.id,
                previous_status=previous_status.value,
                final_status=terminal_status.value,
                outcome=f"relay_{terminal_status.value}",
                reason=reason,
                released_cents=released_cents,
                released_points=released_points,
                ledger_entry_id=ledger.id if ledger is not None else None,
                personal_ledger_entry_id=(
                    personal_ledger.id if personal_ledger is not None else None
                ),
                relay_job_id=relay_job_id,
            )
            session.add(event)
            session.flush()
            return TimeoutScanItem(
                task_id=task.id,
                previous_status=previous_status.value,
                outcome=event.outcome,
                reason=reason,
                final_status=terminal_status.value,
                released_cents=released_cents,
                released_points=released_points,
            )

    def page_events(
        self, *, page: int, page_size: int
    ) -> tuple[int, list[TaskTimeoutEvent]]:
        with self.session_factory() as session:
            total = session.scalar(select(func.count(TaskTimeoutEvent.id))) or 0
            items = list(
                session.scalars(
                    select(TaskTimeoutEvent)
                    .order_by(
                        TaskTimeoutEvent.created_at.desc(),
                        TaskTimeoutEvent.id.desc(),
                    )
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                ).all()
            )
            for item in items:
                session.expunge(item)
            return total, items
