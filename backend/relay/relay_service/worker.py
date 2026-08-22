from __future__ import annotations

import asyncio
import logging
import signal

from .config import RelaySettings
from .providers.registry import build_provider_router
from .queue import RedisWorkQueue
from .service import GenerationService
from .sql_repository import SqlAlchemyJobRepository

logger = logging.getLogger("relay.worker")


async def consume(
    service: GenerationService,
    stop: asyncio.Event,
    *,
    idle_seconds: float = 0.25,
) -> None:
    """Consume until a shutdown signal, isolating failures per delivery."""

    while not stop.is_set():
        try:
            processed = await service.process_next()
        except Exception:
            # Driver exceptions may contain DSNs, so do not log exception text.
            logger.warning("Worker delivery failed; queue retry policy applied")
            processed = None
        if processed is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=idle_seconds)
            except TimeoutError:
                pass


async def run() -> None:
    settings = RelaySettings.from_environment()
    if settings.runtime_mode != "production":
        raise RuntimeError("Worker requires RELAY_RUNTIME_MODE=production")
    assert settings.database_url is not None
    assert settings.redis_url is not None
    repository = SqlAlchemyJobRepository.from_url(settings.database_url)
    router = build_provider_router(settings, account_pool=repository)
    await router.validate_configuration()
    queue = RedisWorkQueue(settings.redis_url)
    service = GenerationService(
        repository,
        queue,
        router,
        max_worker_attempts=settings.worker_max_attempts,
        submission_claim_lease_seconds=(
            settings.submission_claim_lease_seconds
        ),
        provider_admission_retry_seconds=(
            settings.provider_admission_retry_seconds
        ),
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await consume(service, stop)
    finally:
        await asyncio.gather(
            queue.close(),
            router.close(),
            repository.dispose(),
            return_exceptions=True,
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
