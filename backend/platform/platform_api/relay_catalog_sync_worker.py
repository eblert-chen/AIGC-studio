from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
import logging
import signal
from threading import Event, Lock
from typing import Iterator
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings, runtime_settings_are_protected
from .database import build_engine, build_session_factory
from .database_privileges import attest_platform_database
from .relay_backends import build_relay_backend_registry
from .relay_client import (
    RelayClient,
    RelayPermanentError,
    validate_model_catalog_release_evidence_pair,
    validate_protected_model_catalog_routes,
)
from .services.relay_catalog_reconciliation import (
    ReconciliationAuditActor,
    RelayCatalogReconciliationResult,
    RelayCatalogReconciliationService,
)
from .services.model_commercial_release import (
    CommercialReleaseReconcileResult,
    ModelCommercialReleaseService,
)


logger = logging.getLogger("platform.relay_catalog_sync_worker")

RELAY_CATALOG_SYNC_SYSTEM_ACTOR = "relay-catalog-sync"
# Frozen signed int64 derived from the worker's semantic identity.  A dedicated
# PostgreSQL session advisory lock makes the periodic mutation a single-leader
# action without adding an operational lease table or broadening table access.
RELAY_CATALOG_SYNC_ADVISORY_LOCK_KEY = 7_019_790_553_725_904_905
_local_leader_lock = Lock()


@dataclass(frozen=True)
class RelayCatalogSyncOutcome:
    leader: bool
    not_modified: bool
    catalog_revision: str | None
    reconciliation: RelayCatalogReconciliationResult | None
    commercial_release: CommercialReleaseReconcileResult | None = None


class RelayCatalogSyncWorker:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        relay_client: RelayClient,
        *,
        require_managed_routes: bool = False,
    ) -> None:
        self.session_factory = session_factory
        self.relay_client = relay_client
        self.require_managed_routes = require_managed_routes
        self._etag: str | None = None
        self._catalog = None

    @staticmethod
    def _postgres_leader(connection: Connection) -> bool:
        return bool(
            connection.scalar(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": RELAY_CATALOG_SYNC_ADVISORY_LOCK_KEY},
            )
        )

    @contextmanager
    def _leader(self) -> Iterator[bool]:
        bind = self.session_factory.kw.get("bind")
        if not isinstance(bind, Engine):
            raise RuntimeError("Relay catalog sync session factory has no Engine")
        if bind.dialect.name == "postgresql":
            # Use a session-level advisory lock on one dedicated connection.
            # Commit the lock query before doing Relay network I/O so no SQL
            # transaction is held open across that bounded external request.
            with bind.connect() as connection:
                acquired = self._postgres_leader(connection)
                connection.commit()
                try:
                    yield acquired
                finally:
                    if acquired:
                        unlocked = bool(
                            connection.scalar(
                                text("SELECT pg_advisory_unlock(:lock_key)"),
                                {
                                    "lock_key": (
                                        RELAY_CATALOG_SYNC_ADVISORY_LOCK_KEY
                                    )
                                },
                            )
                        )
                        connection.commit()
                        if not unlocked:
                            logger.critical(
                                "relay catalog leader advisory lock was lost"
                            )
            return
        acquired = _local_leader_lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                _local_leader_lock.release()

    def run_once(self) -> RelayCatalogSyncOutcome:
        request_id = f"relay-catalog-sync-{uuid4().hex}"
        committed_etag: str | None = None
        outcome: RelayCatalogSyncOutcome
        with self._leader() as leader:
            if not leader:
                return RelayCatalogSyncOutcome(
                    leader=False,
                    not_modified=False,
                    catalog_revision=None,
                    reconciliation=None,
                    commercial_release=None,
                )
            read = self.relay_client.get_model_catalog(
                if_none_match=self._etag,
                request_id=request_id,
            )
            if read.not_modified:
                if read.catalog is not None:
                    raise RelayPermanentError(
                        "Relay conditional model catalog response is invalid"
                    )
                catalog = self._catalog
                if catalog is None:
                    raise RelayPermanentError(
                        "Relay conditional catalog cannot seed a fresh worker"
                    )
            else:
                if read.catalog is None:
                    raise RelayPermanentError(
                        "Relay model catalog response is incomplete"
                    )
                catalog = read.catalog

            evidence_reader = getattr(
                self.relay_client, "get_model_release_evidence", None
            )
            if not callable(evidence_reader):
                raise RelayPermanentError(
                    "Relay model release evidence reader is unavailable"
                )
            release_evidence = evidence_reader(request_id=request_id)
            # Both Relay reads are independently authenticated. Even a
            # discovery-only, empty, or deletion-only catalog can revoke
            # customer callability, so prove that both projections describe
            # one exact snapshot before *any* Platform mutation.
            validate_model_catalog_release_evidence_pair(
                catalog=catalog,
                evidence=release_evidence,
            )
            if self.require_managed_routes:
                validate_protected_model_catalog_routes(catalog=catalog)
            with self.session_factory.begin() as session:
                has_reconcilable_plans = (
                    ModelCommercialReleaseService.has_unreleased_plans(session)
                )

            reconciliation = None
            if not read.not_modified:
                with self.session_factory.begin() as session:
                    reconciliation = RelayCatalogReconciliationService.reconcile(
                        session,
                        catalog=catalog,
                        actor=ReconciliationAuditActor.system(
                            RELAY_CATALOG_SYNC_SYSTEM_ACTOR
                        ),
                        request_id=request_id,
                        source="relay_catalog_periodic_reconcile",
                        trigger="periodic_worker",
                    )

            committed_etag = read.etag
            outcome = RelayCatalogSyncOutcome(
                leader=True,
                not_modified=read.not_modified,
                catalog_revision=(
                    None if read.not_modified else catalog.catalog_revision
                ),
                reconciliation=reconciliation,
                commercial_release=None,
            )
            if has_reconcilable_plans:
                with self.session_factory.begin() as session:
                    commercial_release = ModelCommercialReleaseService.reconcile(
                        session,
                        catalog=catalog,
                        release_evidence=release_evidence,
                        request_id=request_id,
                        trigger="periodic_worker",
                    )
                outcome = RelayCatalogSyncOutcome(
                    leader=outcome.leader,
                    not_modified=outcome.not_modified,
                    catalog_revision=(
                        outcome.catalog_revision or catalog.catalog_revision
                    ),
                    reconciliation=outcome.reconciliation,
                    commercial_release=commercial_release,
                )
        # Never retain an ETag for a catalog whose database transaction failed.
        self._etag = committed_etag
        self._catalog = catalog
        return outcome


def run_loop(
    worker: RelayCatalogSyncWorker,
    *,
    stop_event: Event,
    interval_seconds: float,
    retry_base_seconds: float,
    retry_cap_seconds: float,
    once: bool = False,
    preflight: Callable[[], None] | None = None,
) -> None:
    consecutive_failures = 0
    while not stop_event.is_set():
        if preflight is not None:
            # Database-attestation failures are security boundary failures and
            # deliberately stop the process instead of entering a retry loop.
            preflight()
        try:
            worker.run_once()
            consecutive_failures = 0
            if once:
                return
            delay = interval_seconds
        except Exception as exc:
            logger.error(
                "relay catalog sync iteration failed: %s", type(exc).__name__
            )
            if once:
                raise
            consecutive_failures += 1
            delay = min(
                retry_cap_seconds,
                retry_base_seconds
                * (2 ** min(consecutive_failures - 1, 20)),
            )
        stop_event.wait(delay)


def main() -> None:
    settings = get_settings("relay-catalog-sync")
    parser = argparse.ArgumentParser(
        description="Materialize Relay model catalog drafts in Platform"
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    engine = build_engine(settings.database_url)
    attest_platform_database(engine, "relay-catalog-sync")
    logging.basicConfig(level=logging.INFO)
    if not settings.relay_catalog_sync_enabled:
        logger.info("relay catalog sync is disabled")
        engine.dispose()
        return

    relay_backends = build_relay_backend_registry(
        default_backend_id=settings.relay_default_backend_id,
        default_contract_revision=settings.relay_default_contract_revision,
        configurations=settings.relay_backends,
        legacy_base_url=settings.relay_base_url,
        legacy_client_id=settings.relay_client_id,
        legacy_api_key=settings.relay_api_key,
        allow_local_http=not runtime_settings_are_protected(settings),
        legacy_compatibility_enabled=(
            settings.relay_legacy_compatibility_enabled
        ),
    )
    relay_client = relay_backends.default_client_or_none()
    if relay_client is None:
        relay_backends.close()
        engine.dispose()
        raise SystemExit("relay client configuration is incomplete")

    worker = RelayCatalogSyncWorker(
        build_session_factory(engine),
        relay_client,
        require_managed_routes=runtime_settings_are_protected(settings),
    )
    stop_event = Event()

    def stop(*_) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        run_loop(
            worker,
            stop_event=stop_event,
            interval_seconds=settings.relay_catalog_sync_interval_seconds,
            retry_base_seconds=(
                settings.relay_catalog_sync_retry_base_seconds
            ),
            retry_cap_seconds=settings.relay_catalog_sync_retry_cap_seconds,
            once=args.once,
            preflight=lambda: attest_platform_database(
                engine, "relay-catalog-sync"
            ),
        )
    finally:
        relay_backends.close()
        engine.dispose()


if __name__ == "__main__":
    main()
