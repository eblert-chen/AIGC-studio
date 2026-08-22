from __future__ import annotations

import argparse
from collections.abc import Callable
import logging
import signal
from dataclasses import dataclass
from threading import Event

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings, runtime_settings_are_protected
from .database import build_engine, build_session_factory
from .database_privileges import attest_platform_database
from .models import GenerationTask, TaskStatus, utcnow
from .relay_client import (
    RelayClient,
    RelayClientError,
)
from .relay_backends import (
    RelayBackendRegistry,
    RelayBackendResolutionError,
    build_relay_backend_registry,
    coerce_relay_backend_registry,
)
from .services.relay_status import RelayStatusService


logger = logging.getLogger("platform.relay_sync_worker")


@dataclass(frozen=True)
class RelayTaskReference:
    company_id: str | None
    personal_workspace_id: str | None
    task_id: str
    relay_job_id: str
    capability_revision: str | None
    relay_backend_id: str
    relay_contract_revision: str


class RelayStatusPoller:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        relay_client: RelayClient | RelayBackendRegistry,
        *,
        batch_size: int = 100,
    ):
        self.session_factory = session_factory
        self.relay_backends = coerce_relay_backend_registry(relay_client)
        self.batch_size = batch_size

    def poll_once(self) -> int:
        with self.session_factory.begin() as session:
            rows = session.execute(
                select(
                    GenerationTask.company_id,
                    GenerationTask.personal_workspace_id,
                    GenerationTask.id,
                    GenerationTask.relay_job_id,
                    GenerationTask.capability_snapshot,
                    GenerationTask.relay_backend_id,
                    GenerationTask.relay_contract_revision,
                )
                .where(
                    GenerationTask.relay_job_id.is_not(None),
                    GenerationTask.status.in_(
                        [
                            TaskStatus.QUEUED,
                            TaskStatus.PROCESSING,
                        ]
                    ),
                )
                .order_by(GenerationTask.updated_at)
                .limit(self.batch_size)
            ).all()
            references = [
                RelayTaskReference(
                    company_id=company_id,
                    personal_workspace_id=personal_workspace_id,
                    task_id=task_id,
                    relay_job_id=relay_job_id,
                    capability_revision=(capability_snapshot or {}).get(
                        "relay_capability_revision"
                    ),
                    relay_backend_id=relay_backend_id,
                    relay_contract_revision=relay_contract_revision,
                )
                for (
                    company_id,
                    personal_workspace_id,
                    task_id,
                    relay_job_id,
                    capability_snapshot,
                    relay_backend_id,
                    relay_contract_revision,
                ) in rows
                if relay_job_id is not None
            ]
            if references:
                session.execute(
                    update(GenerationTask)
                    .where(
                        GenerationTask.id.in_(
                            [reference.task_id for reference in references]
                        ),
                        GenerationTask.status.in_(
                            [TaskStatus.QUEUED, TaskStatus.PROCESSING]
                        ),
                    )
                    .values(updated_at=utcnow())
                )
        applied = 0
        for reference in references:
            try:
                client = self.relay_backends.resolve(
                    backend_id=reference.relay_backend_id,
                    contract_revision=reference.relay_contract_revision,
                )
                snapshot = client.get(reference.relay_job_id)
            except (RelayClientError, RelayBackendResolutionError):
                continue
            if snapshot.id != reference.relay_job_id:
                logger.warning(
                    "relay returned a mismatched job id for task %s",
                    reference.task_id,
                )
                continue
            if snapshot.client_reference_id != reference.task_id:
                logger.warning(
                    "relay returned a mismatched client reference for task %s",
                    reference.task_id,
                )
                continue
            if snapshot.expected_capability_revision != reference.capability_revision:
                logger.warning(
                    "relay returned a mismatched capability revision for task %s",
                    reference.task_id,
                )
                continue
            error_message = ""
            error_snapshot = None
            if snapshot.error is not None:
                error_message = snapshot.error.message
                error_snapshot = {
                    **snapshot.error.model_dump(mode="json"),
                    "source": "poll",
                }
            with self.session_factory.begin() as session:
                RelayStatusService.apply(
                    session,
                    company_id=reference.company_id,
                    personal_workspace_id=reference.personal_workspace_id,
                    task_id=reference.task_id,
                    relay_job_id=reference.relay_job_id,
                    status=snapshot.status,
                    outputs=snapshot.outputs,
                    failure_reason=error_message,
                    error_snapshot=error_snapshot,
                    reservation_action=snapshot.reservation_action,
                )
            applied += 1
        return applied


def run_loop(
    poller: RelayStatusPoller,
    *,
    stop_event: Event,
    interval_seconds: float = 2.0,
    once: bool = False,
    preflight: Callable[[], None] | None = None,
) -> None:
    while not stop_event.is_set():
        if preflight is not None:
            preflight()
        try:
            poller.poll_once()
            if once:
                return
        except Exception as exc:
            logger.error("relay sync iteration failed: %s", type(exc).__name__)
            if once:
                raise
        stop_event.wait(interval_seconds)


def main() -> None:
    settings = get_settings("relay-sync")
    parser = argparse.ArgumentParser(description="Synchronize relay job statuses")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    args = parser.parse_args()
    engine = build_engine(settings.database_url)
    attest_platform_database(engine, "relay-sync")
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
    poller = RelayStatusPoller(build_session_factory(engine), relay_backends)
    stop_event = Event()

    def stop(*_) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        run_loop(
            poller,
            stop_event=stop_event,
            interval_seconds=max(args.interval_seconds, 0.2),
            once=args.once,
            preflight=lambda: attest_platform_database(engine, "relay-sync"),
        )
    finally:
        relay_backends.close()
        engine.dispose()


if __name__ == "__main__":
    main()
