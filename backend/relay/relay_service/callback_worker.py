from __future__ import annotations

import asyncio
import logging
import signal

from .callback import (
    AioHttpCallbackTransport,
    CallbackDispatcher,
    CallbackPolicy,
)
from .config import RelaySettings
from .sql_repository import SqlAlchemyJobRepository


logger = logging.getLogger("relay.callback")


async def consume(
    dispatcher: CallbackDispatcher,
    stop: asyncio.Event,
    *,
    idle_seconds: float = 0.5,
) -> None:
    while not stop.is_set():
        try:
            delivered = await dispatcher.dispatch_once()
        except Exception:
            # Driver failures may contain DSNs; callback errors may contain URLs.
            logger.warning("Callback dispatch cycle failed")
            delivered = 0
        if delivered == 0:
            try:
                await asyncio.wait_for(stop.wait(), timeout=idle_seconds)
            except TimeoutError:
                pass


async def run() -> None:
    settings = RelaySettings.from_environment()
    if settings.runtime_mode != "production":
        raise RuntimeError(
            "Callback worker requires RELAY_RUNTIME_MODE=production"
        )
    assert settings.database_url is not None
    repository = SqlAlchemyJobRepository.from_url(settings.database_url)
    policy = CallbackPolicy(
        settings.callback_routes,
        production=settings.environment == "production",
    )
    dispatcher = CallbackDispatcher(
        repository,
        policy,
        transport=AioHttpCallbackTransport(
            timeout_seconds=settings.callback_timeout_seconds
        ),
        max_attempts=settings.callback_max_attempts,
        base_delay_seconds=settings.callback_base_delay_seconds,
        max_delay_seconds=settings.callback_max_delay_seconds,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await consume(
            dispatcher,
            stop,
            idle_seconds=settings.callback_poll_seconds,
        )
    finally:
        await repository.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
