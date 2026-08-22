from __future__ import annotations

import argparse
from collections.abc import Callable
import logging
import os
import signal
import socket
from threading import Event
from uuid import uuid4

from sqlalchemy.engine import Engine

from .config import Settings, get_settings
from .database import build_engine, build_session_factory
from .database_privileges import attest_platform_database
from .download_gateway import DownloadGatewayClient
from .services.download_gateway_registrations import (
    DownloadGatewayAttemptCipher,
    DownloadGatewayRegistrationService,
)


logger = logging.getLogger("platform.download_gateway_registration_worker")


def build_service(settings: Settings, *, engine: Engine | None = None) -> tuple[
    DownloadGatewayRegistrationService,
    DownloadGatewayClient,
    Engine,
]:
    if not settings.download_gateway_configured:
        raise RuntimeError("Download Gateway configuration is incomplete")
    encryption_key = settings.download_gateway_attempt_encryption_key_base64
    if not encryption_key:
        raise RuntimeError("Download Gateway attempt encryption key is missing")
    engine = engine or build_engine(settings.database_url)
    client = DownloadGatewayClient(
        registration_url=settings.download_gateway_registration_url or "",
        public_base_url=settings.download_gateway_public_base_url or "",
        service_token=settings.download_gateway_service_token or "",
        signing_secret=(
            settings.download_gateway_registration_signing_secret or ""
        ),
        timeout_seconds=settings.download_gateway_timeout_seconds,
        max_ticket_ttl_seconds=settings.download_gateway_ticket_ttl_seconds,
        source_ttl_margin_seconds=(
            settings.download_gateway_source_ttl_margin_seconds
        ),
    )
    service = DownloadGatewayRegistrationService(
        build_session_factory(engine),
        client,
        DownloadGatewayAttemptCipher.from_base64(encryption_key),
        lease_owner=(
            f"platform-download-gateway:{socket.gethostname()}:{os.getpid()}:{uuid4()}"
        ),
        lease_seconds=settings.download_gateway_registration_lease_seconds,
        max_attempts=settings.download_gateway_registration_max_attempts,
        retry_base_seconds=(
            settings.download_gateway_registration_retry_base_seconds
        ),
        retry_cap_seconds=(
            settings.download_gateway_registration_retry_cap_seconds
        ),
        gateway_ticket_ttl_seconds=settings.download_gateway_ticket_ttl_seconds,
        source_ttl_margin_seconds=(
            settings.download_gateway_source_ttl_margin_seconds
        ),
    )
    return service, client, engine


def run_loop(
    service: DownloadGatewayRegistrationService,
    *,
    stop_event: Event,
    interval_seconds: float,
    batch_size: int,
    once: bool = False,
    preflight: Callable[[], None] | None = None,
) -> None:
    while not stop_event.is_set():
        processed = 0
        try:
            for _ in range(batch_size):
                if preflight is not None:
                    preflight()
                result = service.run_once()
                if not result.processed:
                    break
                processed += 1
                logger.info(
                    "download Gateway registration transition attempt=%s status=%s",
                    result.attempt_id,
                    result.status,
                )
            if once:
                return
        except Exception as exc:
            logger.error(
                "download Gateway registration iteration failed: %s",
                type(exc).__name__,
            )
            if once:
                raise
        if processed == 0:
            stop_event.wait(interval_seconds)


def main() -> None:
    settings = get_settings("download-gateway-registration-worker")
    parser = argparse.ArgumentParser(
        description="Reconcile durable Download Gateway registrations"
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=None)
    args = parser.parse_args()
    engine = build_engine(settings.database_url)
    attest_platform_database(
        engine, "download-gateway-registration-worker"
    )
    if not settings.download_gateway_registration_worker_enabled:
        raise RuntimeError("Download Gateway registration worker is disabled")
    logging.basicConfig(level=logging.INFO)
    service, client, engine = build_service(settings, engine=engine)
    stop_event = Event()

    def stop(*_) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        run_loop(
            service,
            stop_event=stop_event,
            interval_seconds=(
                args.interval_seconds
                if args.interval_seconds is not None
                else settings.download_gateway_registration_poll_interval_seconds
            ),
            batch_size=settings.download_gateway_registration_batch_size,
            once=args.once,
            preflight=lambda: attest_platform_database(
                engine, "download-gateway-registration-worker"
            ),
        )
    finally:
        client.close()
        engine.dispose()


if __name__ == "__main__":
    main()
