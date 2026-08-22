from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from relay_service.models import WorkItem
from relay_service.queue import RedisWorkQueue


@pytest.mark.skipif(
    not os.getenv("RELAY_TEST_REDIS_URL"),
    reason="RELAY_TEST_REDIS_URL is not configured",
)
def test_redis_defer_is_durable_delayed_and_preserves_attempt() -> None:
    async def scenario() -> None:
        suffix = uuid4().hex
        stream = f"relay:test:defer:{suffix}"
        queue = RedisWorkQueue(
            os.environ["RELAY_TEST_REDIS_URL"],
            stream=stream,
            group=f"relay-test-{suffix}",
        )
        try:
            item = WorkItem(job_id=uuid4(), attempt=9)
            await queue.enqueue(item)
            delivery = await queue.dequeue()
            assert delivery is not None

            await queue.defer(
                delivery,
                delay_seconds=0.08,
                increment_attempt=False,
            )

            assert await queue.depth() == 1
            assert await queue.dequeue() is None
            await asyncio.sleep(0.11)

            redelivery = await queue.dequeue()
            assert redelivery is not None
            assert redelivery.item.job_id == item.job_id
            assert redelivery.item.attempt == 9
            await queue.ack(redelivery)
            assert await queue.depth() == 0
        finally:
            await queue._redis.delete(stream, queue.delayed_key)
            await queue.close()

    asyncio.run(scenario())
