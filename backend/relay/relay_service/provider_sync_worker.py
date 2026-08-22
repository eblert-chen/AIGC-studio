from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import signal

from .config import RelaySettings
from .providers.registry import build_provider_router
from .queue import RedisWorkQueue
from .service import GenerationService
from .sql_repository import SqlAlchemyJobRepository

logger = logging.getLogger("relay.provider_sync")


async def reconcile(
    service: GenerationService,
    stop: asyncio.Event,
    *,
    poll_seconds: float,
    batch_size: int,
) -> None:
    while not stop.is_set():
        poll_task = asyncio.create_task(service.poll_provider_jobs(limit=batch_size))
        stopped = asyncio.create_task(stop.wait())
        try:
            done, _ = await asyncio.wait(
                {poll_task, stopped},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stopped in done:
                poll_task.cancel()
                with suppress(asyncio.CancelledError):
                    await poll_task
                return
            stopped.cancel()
            with suppress(asyncio.CancelledError):
                await stopped
            summary = poll_task.result()
            if summary.failures:
                logger.warning(
                    "Provider reconciliation completed with %d isolated failures",
                    summary.failures,
                )
        except Exception:
            # Provider payloads and driver exceptions can contain sensitive data.
            logger.warning("Provider reconciliation batch failed")
        finally:
            if not stopped.done():
                stopped.cancel()
                with suppress(asyncio.CancelledError):
                    await stopped
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except TimeoutError:
            pass


async def run() -> None:
    settings = RelaySettings.from_environment()
    if settings.runtime_mode != "production":
        raise RuntimeError("Provider sync worker requires production mode")
    assert settings.database_url is not None
    assert settings.redis_url is not None
    repository = SqlAlchemyJobRepository.from_url(settings.database_url)
    generation_queue = RedisWorkQueue(settings.redis_url)
    transfer_queue = RedisWorkQueue(
        settings.redis_url,
        stream="relay:artifact:transfer",
        group="relay-transfer-workers",
    )
    router = build_provider_router(settings, account_pool=repository)
    await router.validate_configuration()
    service = GenerationService(
        repository,
        generation_queue,
        router,
        max_worker_attempts=settings.worker_max_attempts,
        submission_claim_lease_seconds=(settings.submission_claim_lease_seconds),
        transfer_queue=transfer_queue,
        provider_poll_concurrency=settings.provider_poll_concurrency,
        provider_poll_claim_lease_seconds=(settings.provider_poll_claim_lease_seconds),
        provider_poll_error_base_seconds=(settings.provider_poll_error_base_seconds),
        provider_poll_error_max_seconds=(settings.provider_poll_error_max_seconds),
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
        await reconcile(
            service,
            stop,
            poll_seconds=settings.provider_poll_seconds,
            batch_size=settings.provider_poll_batch_size,
        )
    finally:
        await asyncio.gather(
            generation_queue.close(),
            transfer_queue.close(),
            router.close(),
            repository.dispose(),
            return_exceptions=True,
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
