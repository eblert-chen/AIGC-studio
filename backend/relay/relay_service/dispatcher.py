from __future__ import annotations

import asyncio
import logging

from .config import RelaySettings
from .outbox import OutboxDispatcher
from .queue import RedisWorkQueue
from .sql_repository import SqlAlchemyJobRepository

logger = logging.getLogger("relay.outbox")


async def run() -> None:
    settings = RelaySettings.from_environment()
    if settings.runtime_mode != "production":
        raise RuntimeError("Outbox dispatcher requires RELAY_RUNTIME_MODE=production")
    assert settings.database_url is not None
    assert settings.redis_url is not None
    repository = SqlAlchemyJobRepository.from_url(settings.database_url)
    queue = RedisWorkQueue(settings.redis_url, consumer="outbox-dispatcher")
    transfer_queue = RedisWorkQueue(
        settings.redis_url,
        stream="relay:artifact:transfer",
        group="relay-transfer-workers",
        consumer="outbox-transfer-dispatcher",
    )
    dispatcher = OutboxDispatcher(
        repository,
        {
            "generation.submit": queue,
            "artifact.transfer": transfer_queue,
        },
    )
    try:
        while True:
            try:
                published = await dispatcher.dispatch_once()
            except Exception:
                # Do not log exception values: driver errors can contain DSNs.
                logger.warning("Outbox dispatch cycle failed")
                published = 0
            await asyncio.sleep(0.05 if published else 0.5)
    finally:
        await queue.close()
        await transfer_queue.close()
        await repository.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
