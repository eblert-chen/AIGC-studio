from __future__ import annotations

import argparse
from collections.abc import Callable
import logging
import signal
from threading import Event

from .config import get_settings, runtime_settings_are_protected
from .database import build_engine, build_session_factory
from .database_privileges import attest_platform_database
from .relay_backends import RelayBackendRegistry, build_relay_backend_registry
from .services.task_timeouts import TaskTimeoutService


logger = logging.getLogger("platform.timeout_worker")


def run_loop(
    service: TaskTimeoutService,
    *,
    stop_event: Event,
    interval_seconds: float,
    once: bool = False,
    preflight: Callable[[], None] | None = None,
) -> None:
    while not stop_event.is_set():
        if preflight is not None:
            preflight()
        try:
            service.scan_once()
            if once:
                return
        except Exception as exc:
            logger.error(
                "task timeout iteration failed: %s", type(exc).__name__
            )
            if once:
                raise
        stop_event.wait(interval_seconds)


def _relay_backends(settings) -> RelayBackendRegistry:
    return build_relay_backend_registry(
        default_backend_id=settings.relay_default_backend_id,
        default_contract_revision=settings.relay_default_contract_revision,
        configurations=settings.relay_backends,
        legacy_base_url=settings.relay_base_url,
        legacy_client_id=settings.relay_client_id,
        legacy_api_key=settings.relay_api_key,
        allow_local_http=not runtime_settings_are_protected(settings),
        legacy_compatibility_enabled=settings.relay_legacy_compatibility_enabled,
    )


def main() -> None:
    settings = get_settings("timeout-worker")
    parser = argparse.ArgumentParser(
        description="Safely reconcile and compensate stale platform tasks"
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=None)
    args = parser.parse_args()
    engine = build_engine(settings.database_url)
    attest_platform_database(engine, "timeout-worker")
    relay_backends = _relay_backends(settings)
    logging.basicConfig(level=logging.INFO)
    service = TaskTimeoutService(
        build_session_factory(engine),
        relay_backends,
        queued_timeout_seconds=settings.task_queued_timeout_seconds,
        processing_timeout_seconds=settings.task_processing_timeout_seconds,
        batch_size=settings.task_timeout_batch_size,
    )
    stop_event = Event()

    def stop(*_) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        run_loop(
            service,
            stop_event=stop_event,
            interval_seconds=max(
                args.interval_seconds
                if args.interval_seconds is not None
                else settings.task_timeout_scan_interval_seconds,
                1.0,
            ),
            once=args.once,
            preflight=lambda: attest_platform_database(engine, "timeout-worker"),
        )
    finally:
        relay_backends.close()
        engine.dispose()


if __name__ == "__main__":
    main()
