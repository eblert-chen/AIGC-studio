from __future__ import annotations

import argparse
from collections.abc import Callable
import logging
import signal
from threading import Event

from .asset_storage import FilesystemInputAssetSigner, build_input_asset_store
from .config import get_settings, runtime_settings_are_protected
from .database import build_engine, build_session_factory
from .database_privileges import attest_platform_database
from .relay_backends import build_relay_backend_registry
from .services.relay_outbox import RelayOutboxDispatcher
from .services.input_assets import InputAssetRelayResolver


logger = logging.getLogger("platform.relay_dispatcher")


def run_loop(
    dispatcher: RelayOutboxDispatcher,
    *,
    stop_event: Event,
    idle_seconds: float = 1.0,
    once: bool = False,
    preflight: Callable[[], None] | None = None,
) -> None:
    while not stop_event.is_set():
        if preflight is not None:
            preflight()
        try:
            result = dispatcher.dispatch_once()
            if once:
                return
            if not result.processed:
                stop_event.wait(idle_seconds)
        except Exception as exc:
            # Only the exception type is logged so DSNs and credentials embedded in
            # third-party exception messages cannot leak into worker logs.
            logger.error("relay dispatch iteration failed: %s", type(exc).__name__)
            if once:
                raise
            stop_event.wait(idle_seconds)


def main() -> None:
    settings = get_settings("dispatcher")
    parser = argparse.ArgumentParser(description="Dispatch relay submission outbox")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--idle-seconds", type=float, default=1.0)
    args = parser.parse_args()
    engine = build_engine(settings.database_url)
    attest_platform_database(engine, "dispatcher")
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
    session_factory = build_session_factory(engine)
    input_asset_store = build_input_asset_store(settings)
    input_asset_signer = (
        FilesystemInputAssetSigner(
            public_base_url=(
                settings.input_asset_relay_base_url
                or settings.input_asset_public_base_url
            ),
            signing_secret=settings.input_asset_signing_secret,
        )
        if input_asset_store.kind == "filesystem"
        else None
    )
    dispatcher = RelayOutboxDispatcher(
        session_factory,
        relay_backends,
        max_attempts=settings.relay_dispatch_max_attempts,
        asset_reference_resolver=InputAssetRelayResolver(
            session_factory,
            store=input_asset_store,
            signer=input_asset_signer,
            expires_seconds=settings.input_asset_relay_signed_url_seconds,
        ),
    )
    stop_event = Event()

    def stop(*_) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        run_loop(
            dispatcher,
            stop_event=stop_event,
            idle_seconds=max(args.idle_seconds, 0.1),
            once=args.once,
            preflight=lambda: attest_platform_database(engine, "dispatcher"),
        )
    finally:
        relay_backends.close()
        close_store = getattr(input_asset_store, "close", None)
        if close_store:
            close_store()
        engine.dispose()


if __name__ == "__main__":
    main()
