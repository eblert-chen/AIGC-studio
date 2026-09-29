"""Platform-side automatic recovery for Relay reconciliation outboxes.

Polls ``RelaySubmissionOutbox`` rows stuck in ``RECONCILIATION_REQUIRED``,
probes the Relay job by id (when known), and drives them back to a terminal
state — either by binding a resolved Relay snapshot, resetting the outbox
to ``RETRY`` so the dispatcher can resubmit, or (after exhausting retries)
marking ``PERMANENTLY_FAILED`` and releasing the wallet reservation.

Runs as its own worker process (``python -m platform_api.relay_reconciliation_recovery``)
or can be invoked with ``--once`` from cron.  Uses ``SELECT ... FOR UPDATE SKIP LOCKED``
so multiple instances can compete safely.
"""
from __future__ import annotations

import argparse
import logging
import signal
from collections.abc import Callable
from datetime import datetime, timedelta
from threading import Event

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings, runtime_settings_are_protected
from .database import build_engine, build_session_factory
from .database_privileges import attest_platform_database
from .models import (
    GenerationTask,
    RelayOutboxStatus,
    RelaySubmissionOutbox,
    TaskStatus,
    utcnow,
)
from .relay_client import (
    RelayClient,
    RelayClientError,
    RelayPermanentError,
    RelayTemporaryError,
)
from .relay_backends import (
    RelayBackendRegistry,
    RelayBackendResolutionError,
    build_relay_backend_registry,
    coerce_relay_backend_registry,
)
from .services.errors import NotFoundError
from .services.relay_status import RelayStatusService


logger = logging.getLogger("platform.relay_reconciliation_recovery")


# ---------- Tunables ----------

_MAX_RECOVERY_ATTEMPTS_NO_JOB_ID = 24
"""Max probes before treating an outbox without ``relay_job_id`` as permanently lost."""

_MAX_RECOVERY_ATTEMPTS_WITH_JOB_ID = 288
"""Cap for probing a known ``relay_job_id`` before giving up (≈24h at the 300s backoff cap)."""

_JOB_LOST_WINDOW_HOURS = 24
"""Relay 404 older than this is treated as safe-to-resubmit."""

_BACKOFF_CAP_SECONDS = 300


def _backoff_seconds(attempt: int) -> int:
    # 2^attempt seconds, capped at _BACKOFF_CAP_SECONDS.
    return min(_BACKOFF_CAP_SECONDS, 2 ** min(attempt, 8))


def _apply_backoff_in_tx(ob: RelaySubmissionOutbox, attempt: int) -> None:
    """Write backoff fields on an already-locked row inside the current tx.

    Never open a nested transaction here: the caller holds this row's
    FOR UPDATE lock, so a second connection updating the same row would
    stall until lock timeout on Postgres.
    """
    ob.recovery_attempt_count = attempt
    ob.next_recovery_at = utcnow() + timedelta(seconds=_backoff_seconds(attempt))


# ---------- Claim ----------

class _ClaimedOutbox:
    __slots__ = (
        "id",
        "task_id",
        "company_id",
        "personal_workspace_id",
        "status",
        "relay_job_id",
        "relay_backend_id",
        "relay_contract_revision",
        "relay_submit_attempted_at",
        "submission_outcome_uncertain_at",
        "recovery_attempt_count",
        "next_recovery_at",
        "updated_at",
        "attempt_count",
    )

    def __init__(self, row):
        self.id = row[0]
        self.task_id = row[1]
        self.company_id = row[2]
        self.personal_workspace_id = row[3]
        self.status = row[4]
        self.relay_job_id = row[5]
        self.relay_backend_id = row[6]
        self.relay_contract_revision = row[7]
        self.relay_submit_attempted_at = row[8]
        self.submission_outcome_uncertain_at = row[9]
        self.recovery_attempt_count = row[10]
        self.next_recovery_at = row[11]
        self.updated_at = row[12]
        self.attempt_count = row[13]


class ReconciliationRecovery:
    """Scans ``RECONCILIATION_REQUIRED`` outbox rows and drives them home."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        relay_client: RelayClient | RelayBackendRegistry,
        *,
        batch_size: int = 50,
    ):
        self.session_factory = session_factory
        self.relay_backends = coerce_relay_backend_registry(relay_client)
        self.batch_size = batch_size

    # ----- public entrypoint -----

    def run_once(self) -> int:
        """Claim one batch and attempt recovery; returns count of rows touched."""
        claimed = self._claim_batch()
        if not claimed:
            return 0
        touched = 0
        for outbox in claimed:
            try:
                if self._recover_one(outbox):
                    touched += 1
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception(
                    "recovery worker failed for outbox %s: %s", outbox.id, exc
                )
                try:
                    self._bump_attempt(outbox.id)
                except Exception:
                    pass
        return touched

    # ----- claim -----

    def _claim_batch(self) -> list[_ClaimedOutbox]:
        now = utcnow()
        with self.session_factory.begin() as session:
            # Postgres/MySQL: FOR UPDATE SKIP LOCKED; SQLite: FOR UPDATE degenerates
            # to a plain row lock and skips the SKIP LOCKED clause.
            stmt = (
                select(
                    RelaySubmissionOutbox.id,
                    RelaySubmissionOutbox.task_id,
                    RelaySubmissionOutbox.company_id,
                    RelaySubmissionOutbox.personal_workspace_id,
                    RelaySubmissionOutbox.status,
                    RelaySubmissionOutbox.relay_job_id,
                    RelaySubmissionOutbox.relay_backend_id,
                    RelaySubmissionOutbox.relay_contract_revision,
                    RelaySubmissionOutbox.relay_submit_attempted_at,
                    RelaySubmissionOutbox.submission_outcome_uncertain_at,
                    RelaySubmissionOutbox.recovery_attempt_count,
                    RelaySubmissionOutbox.next_recovery_at,
                    RelaySubmissionOutbox.updated_at,
                    RelaySubmissionOutbox.attempt_count,
                )
                .where(
                    RelaySubmissionOutbox.status
                    == RelayOutboxStatus.RECONCILIATION_REQUIRED,
                )
                .where(
                    (RelaySubmissionOutbox.next_recovery_at.is_(None))
                    | (RelaySubmissionOutbox.next_recovery_at <= now)
                )
                .order_by(RelaySubmissionOutbox.updated_at)
                .limit(self.batch_size)
            )
            # SQLite doesn't support FOR UPDATE / SKIP LOCKED; GORM/SQLAlchemy
            # dialect helpers handle this elsewhere in the Go Relay codebase.
            # For the Python side we accept a best-effort claim (single writer
            # process is the expected deployment model).
            try:
                stmt = stmt.with_for_update(skip_locked=True)
            except Exception:
                pass
            rows = session.execute(stmt).all()
            session.expunge_all()
        return [_ClaimedOutbox(r) for r in rows]

    # ----- per-outbox recovery -----

    def _recover_one(self, outbox: _ClaimedOutbox) -> bool:
        """Return True if the row reached a terminal state this call."""
        attempt = outbox.recovery_attempt_count + 1
        if outbox.relay_job_id is None:
            return self._recover_no_job_id(outbox, attempt)
        return self._recover_with_job_id(outbox, attempt)

    # --- Case A/B: relay_job_id known ---

    def _recover_with_job_id(
        self, outbox: _ClaimedOutbox, attempt: int
    ) -> bool:
        if attempt >= _MAX_RECOVERY_ATTEMPTS_WITH_JOB_ID:
            return self._fail_permanently(
                outbox,
                attempt,
                reason=(
                    f"relay_job_id {outbox.relay_job_id} remained unresolved "
                    f"(attempt cap {_MAX_RECOVERY_ATTEMPTS_WITH_JOB_ID})"
                ),
            )
        try:
            client = self.relay_backends.resolve(
                backend_id=outbox.relay_backend_id,
                contract_revision=outbox.relay_contract_revision,
            )
        except RelayBackendResolutionError as exc:
            logger.warning(
                "recovery: Relay backend unavailable for outbox %s: %s",
                outbox.id,
                exc,
            )
            self._schedule_backoff(outbox.id, attempt)
            return False

        try:
            snapshot = client.get(outbox.relay_job_id)
        except RelayPermanentError as exc:
            if exc.response_status == 404:
                return self._handle_relay_404(outbox)
            logger.warning(
                "recovery: Relay permanent error for outbox %s: %s",
                outbox.id,
                exc,
            )
            self._schedule_backoff(outbox.id, attempt)
            return False
        except RelayTemporaryError as exc:
            logger.debug(
                "recovery: Relay temp error for outbox %s: %s",
                outbox.id,
                exc,
            )
            self._schedule_backoff(outbox.id, attempt)
            return False
        except RelayClientError as exc:  # pragma: no cover
            logger.warning(
                "recovery: Relay client error for outbox %s: %s",
                outbox.id,
                exc,
            )
            self._schedule_backoff(outbox.id, attempt)
            return False

        # Relay returned a live snapshot.
        status = snapshot.status
        if status in {"processing", "submitting", "reconciliation_required", "transferring"}:
            # Still in-flight — schedule next probe.
            self._schedule_backoff(outbox.id, attempt)
            return False

        # Terminal state — bind outbox and drive the task home.
        return self._bind_and_apply(outbox, snapshot)

    def _handle_relay_404(self, outbox: _ClaimedOutbox) -> bool:
        """Relay has no such job_id — it was either never created or already purged.

        If the submission was long ago we can safely reset to RETRY so the
        dispatcher re-submits with the original idempotency key.  Otherwise
        we wait longer — the job may simply not have been persisted yet.
        """
        old_boundary = outbox.relay_submit_attempted_at or outbox.updated_at
        cutoff = old_boundary + timedelta(hours=_JOB_LOST_WINDOW_HOURS)
        if utcnow() < cutoff:
            self._schedule_backoff(outbox.id, outbox.recovery_attempt_count + 1)
            return False
        logger.info(
            "recovery: Relay 404 for outbox %s older than %dh → reset to RETRY",
            outbox.id,
            _JOB_LOST_WINDOW_HOURS,
        )
        with self.session_factory.begin() as session:
            ob = session.get(RelaySubmissionOutbox, outbox.id)
            if ob is None or ob.status != RelayOutboxStatus.RECONCILIATION_REQUIRED:
                return False
            ob.status = RelayOutboxStatus.RETRY
            ob.relay_job_id = None
            ob.next_recovery_at = None
            ob.next_attempt_at = utcnow()
            ob.last_error = (
                "recovery worker: Relay returned 404 for relay_job_id "
                f"{outbox.relay_job_id}; reset to RETRY for re-submission"
            )[:2000]
            # Leave recovery_attempt_count untouched so future reconcile
            # calls (if it lands back here) still have a sense of history.
        return False  # outbox no longer needs recovery; dispatcher owns it now

    def _bind_and_apply(self, outbox: _ClaimedOutbox, snapshot) -> bool:
        """Lock task + outbox, bind the relay_job_id, drive RelayStatusService.apply()."""
        with self.session_factory.begin() as session:
            task, ob = self._lock_pair(session, outbox)
            if ob is None or ob.status != RelayOutboxStatus.RECONCILIATION_REQUIRED:
                return False  # callback / dispatcher beat us
            if snapshot.id != outbox.relay_job_id:
                logger.warning(
                    "recovery: snapshot id %s ≠ outbox relay_job_id %s for outbox %s",
                    snapshot.id,
                    outbox.relay_job_id,
                    outbox.id,
                )
                _apply_backoff_in_tx(ob, outbox.recovery_attempt_count + 1)
                return False

            # Idempotent-safe if a callback also bound it concurrently.
            if ob.relay_job_id != snapshot.id and ob.relay_job_id is not None:
                _apply_backoff_in_tx(ob, outbox.recovery_attempt_count + 1)
                return False

            # apply_to_locked_task 硬校验 task.relay_job_id == relay_job_id。
            # task 尚未绑定时（遗留数据/未来写入路径）在此补绑；已绑到
            # 其他 job 说明数据冲突，退避等待人工核查，绝不覆盖。
            if task.relay_job_id is None:
                task.relay_job_id = snapshot.id
            elif task.relay_job_id != snapshot.id:
                logger.warning(
                    "recovery: task %s bound to %s but snapshot says %s (outbox %s)",
                    task.id,
                    task.relay_job_id,
                    snapshot.id,
                    outbox.id,
                )
                _apply_backoff_in_tx(ob, outbox.recovery_attempt_count + 1)
                return False

            # Bind outbox → SENT (identical path RelayCallbackService uses).
            ob.relay_job_id = snapshot.id
            ob.status = RelayOutboxStatus.SENT
            ob.last_error = None

            error_snapshot = None
            error_message = ""
            if snapshot.error is not None:
                error_message = snapshot.error.message
                error_snapshot = {
                    **snapshot.error.model_dump(mode="json"),
                    "source": "reconciliation_recovery",
                }

            RelayStatusService.apply_to_locked_task(
                session,
                task=task,
                company_id=outbox.company_id,
                task_id=outbox.task_id,
                relay_job_id=snapshot.id,
                target_status=RelayStatusService.target_status(snapshot.status),
                outputs=snapshot.outputs,
                failure_reason=error_message,
                error_snapshot=error_snapshot,
                reservation_action=snapshot.reservation_action,
                execution_contract_sha256=snapshot.execution_contract_sha256,
                personal_workspace_id=outbox.personal_workspace_id,
            )
        logger.info(
            "recovery: outbox %s bound + applied → task %s status %s",
            outbox.id,
            outbox.task_id,
            snapshot.status,
        )
        return True

    # --- Case C: no relay_job_id ---

    def _recover_no_job_id(
        self, outbox: _ClaimedOutbox, attempt: int
    ) -> bool:
        """Best-effort: no known relay_job_id means we cannot probe Relay.

        After ``_MAX_RECOVERY_ATTEMPTS_NO_JOB_ID`` probes we mark the row as
        ``PERMANENTLY_FAILED`` and release the reservation so the wallet
        stays consistent.  An operator alert should accompany this transition.
        """
        if attempt < _MAX_RECOVERY_ATTEMPTS_NO_JOB_ID:
            self._schedule_backoff(outbox.id, attempt)
            return False
        return self._fail_permanently(
            outbox,
            attempt,
            reason=(
                "outbox had no relay_job_id "
                f"(attempt cap {_MAX_RECOVERY_ATTEMPTS_NO_JOB_ID})"
            ),
        )

    def _fail_permanently(
        self, outbox: _ClaimedOutbox, attempt: int, *, reason: str
    ) -> bool:
        """Terminal transition: PERMANENTLY_FAILED + release reservation + fail task."""
        logger.error(
            "recovery: outbox %s → PERMANENTLY_FAILED after %d probes: %s",
            outbox.id,
            attempt,
            reason,
        )
        with self.session_factory.begin() as session:
            task, ob = self._lock_pair(session, outbox)
            if ob is None or ob.status != RelayOutboxStatus.RECONCILIATION_REQUIRED:
                return False
            ob.status = RelayOutboxStatus.PERMANENTLY_FAILED
            ob.next_recovery_at = None
            ob.last_error = (
                f"recovery worker: {reason}; submission outcome remains uncertain. "
                f"manual review required (submission_outcome_uncertain_at="
                f"{outbox.submission_outcome_uncertain_at})"
            )[:2000]

            if outbox.company_id is not None:
                from .services.billing import WalletService

                WalletService.release_failure(
                    session,
                    company_id=outbox.company_id,
                    task_id=outbox.task_id,
                    idempotency_key=f"relay-recovery-exhausted:{ob.id}",
                    failure_reason=ob.last_error,
                )
            else:
                from .services.personal_billing import PersonalWalletService

                PersonalWalletService.release_failure(
                    session,
                    workspace_id=outbox.personal_workspace_id,
                    task_id=outbox.task_id,
                    idempotency_key=f"relay-recovery-exhausted:{ob.id}",
                    failure_reason=ob.last_error,
                )

            # Push the task itself to a terminal failed state so it doesn't
            # linger in an active state forever (task has no RECONCILIATION_REQUIRED
            # enum — that is the Relay job state; task is QUEUED/PROCESSING while
            # waiting for recovery to drive it home).
            if task.status in (TaskStatus.QUEUED, TaskStatus.PROCESSING):
                task.status = TaskStatus.FAILED
                task.relay_error_snapshot = {
                    "source": "reconciliation_recovery",
                    "reason": reason[:500],
                    "attempts": attempt,
                }
        return True

    # ----- helpers -----

    def _lock_pair(
        self, session: Session, outbox: _ClaimedOutbox
    ) -> tuple[GenerationTask | None, RelaySubmissionOutbox | None]:
        """Lock task + outbox in the admission order used by dispatcher/callback."""
        # Resolve tenant scope first — no lock yet.
        scope_row = session.execute(
            select(
                GenerationTask.company_id,
                GenerationTask.personal_workspace_id,
            ).where(GenerationTask.id == outbox.task_id)
        ).one_or_none()
        if scope_row is None or ((scope_row[0] is None) == (scope_row[1] is None)):
            return None, None
        company_id, workspace_id = scope_row
        # Lock task via RelayStatusService — wallet→task order on terminal,
        # task-only on non-terminal.  We're driving a terminal transition here
        # so go through the guarded scope path.
        try:
            task = RelayStatusService.lock_wallet_and_task_for_scope(
                session,
                company_id=company_id,
                personal_workspace_id=workspace_id,
                task_id=outbox.task_id,
            )
        except NotFoundError:
            return None, None
        ob_stmt = (
            select(RelaySubmissionOutbox)
            .where(RelaySubmissionOutbox.id == outbox.id)
        )
        try:
            ob_stmt = ob_stmt.with_for_update()
        except Exception:
            pass
        ob = session.scalar(ob_stmt)
        if ob is None or ob.task_id != task.id:
            return None, None
        return task, ob

    def _schedule_backoff(self, outbox_id: str, attempt: int) -> None:
        delay = _backoff_seconds(attempt)
        with self.session_factory.begin() as session:
            # Row lock so a competing worker instance cannot interleave its
            # own read-modify-write of the recovery counters.
            ob = session.scalar(
                select(RelaySubmissionOutbox)
                .where(RelaySubmissionOutbox.id == outbox_id)
                .with_for_update()
            )
            if ob is None or ob.status != RelayOutboxStatus.RECONCILIATION_REQUIRED:
                return
            ob.recovery_attempt_count = attempt
            ob.next_recovery_at = utcnow() + timedelta(seconds=delay)

    def _bump_attempt(self, outbox_id: str) -> None:
        with self.session_factory.begin() as session:
            # Same row lock as _schedule_backoff: the += 1 below is a
            # read-modify-write and would lose updates without it.
            ob = session.scalar(
                select(RelaySubmissionOutbox)
                .where(RelaySubmissionOutbox.id == outbox_id)
                .with_for_update()
            )
            if ob is None or ob.status != RelayOutboxStatus.RECONCILIATION_REQUIRED:
                return
            ob.recovery_attempt_count += 1
            ob.next_recovery_at = utcnow() + timedelta(
                seconds=_backoff_seconds(ob.recovery_attempt_count)
            )


# ---------- Run loop ----------

def run_loop(
    recovery: ReconciliationRecovery,
    *,
    stop_event: Event,
    interval_seconds: float = 10.0,
    once: bool = False,
    preflight: Callable[[], None] | None = None,
) -> None:
    while not stop_event.is_set():
        if preflight is not None:
            preflight()
        try:
            recovery.run_once()
            if once:
                return
        except Exception as exc:
            logger.error("recovery iteration failed: %s: %s", type(exc).__name__, exc)
            if once:
                raise
        stop_event.wait(interval_seconds)


def main() -> None:
    settings = get_settings("relay-reconciliation-recovery")
    parser = argparse.ArgumentParser(
        description="Auto-recover Relay RECONCILIATION_REQUIRED outboxes"
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=10.0)
    parser.add_argument("--batch-size", type=int, default=50)
    args = parser.parse_args()

    engine = build_engine(settings.database_url)
    attest_platform_database(engine, "relay-reconciliation-recovery")
    relay_backends = build_relay_backend_registry(
        default_backend_id=settings.relay_default_backend_id,
        default_contract_revision=settings.relay_default_contract_revision,
        configurations=settings.relay_backends,
        legacy_base_url=settings.relay_base_url,
        legacy_client_id=settings.relay_client_id,
        legacy_api_key=settings.relay_api_key,
        allow_local_http=not runtime_settings_are_protected(settings),
        legacy_compatibility_enabled=settings.relay_legacy_compatibility_enabled,
    )
    if relay_backends.default_client_or_none() is None:
        raise SystemExit("relay client configuration is incomplete")

    logging.basicConfig(level=logging.INFO)
    recovery = ReconciliationRecovery(
        build_session_factory(engine),
        relay_backends,
        batch_size=max(args.batch_size, 1),
    )
    stop_event = Event()

    def stop(*_) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        run_loop(
            recovery,
            stop_event=stop_event,
            interval_seconds=max(args.interval_seconds, 0.5),
            once=args.once,
            preflight=lambda: attest_platform_database(
                engine, "relay-reconciliation-recovery"
            ),
        )
    finally:
        relay_backends.close()
        engine.dispose()


if __name__ == "__main__":
    main()
