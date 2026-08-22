from __future__ import annotations

from abc import ABC, abstractmethod
from asyncio import Lock
import heapq
import json
import math
from time import monotonic, time
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from .models import WorkDelivery, WorkItem


class WorkQueue(ABC):
    """Persistence boundary for asynchronous work delivery."""

    persistent: bool = False
    kind: str = "abstract"

    @abstractmethod
    async def enqueue(self, item: WorkItem) -> None: ...

    @abstractmethod
    async def dequeue(self) -> WorkDelivery | None: ...

    @abstractmethod
    async def ack(self, delivery: WorkDelivery) -> None: ...

    @abstractmethod
    async def nack(self, delivery: WorkDelivery) -> None: ...

    @abstractmethod
    async def defer(
        self,
        delivery: WorkDelivery,
        *,
        delay_seconds: float,
        increment_attempt: bool = False,
    ) -> None:
        """Atomically replace a delivery with one that becomes visible later.

        Account-pool admission waits are not provider attempts, so callers can
        leave ``increment_attempt`` false. Real provider retries set it true.
        """

    @abstractmethod
    async def depth(self) -> int: ...

    @abstractmethod
    async def healthcheck(self) -> bool: ...


class InMemoryWorkQueue(WorkQueue):
    """Development/test queue. It is intentionally not production durable."""

    persistent = False
    kind = "memory"

    def __init__(self) -> None:
        self._items: list[tuple[float, int, WorkItem]] = []
        self._inflight: dict[str, WorkItem] = {}
        self._lock = Lock()
        self._sequence = 0

    def _push(self, item: WorkItem, *, available_at: float) -> None:
        self._sequence += 1
        heapq.heappush(
            self._items,
            (available_at, self._sequence, item.model_copy(deep=True)),
        )

    async def enqueue(self, item: WorkItem) -> None:
        async with self._lock:
            self._push(item, available_at=monotonic())

    async def dequeue(self) -> WorkDelivery | None:
        async with self._lock:
            if not self._items or self._items[0][0] > monotonic():
                return None
            _, _, item = heapq.heappop(self._items)
            receipt = str(uuid4())
            self._inflight[receipt] = item
            return WorkDelivery(item=item.model_copy(deep=True), receipt=receipt)

    async def ack(self, delivery: WorkDelivery) -> None:
        async with self._lock:
            self._inflight.pop(delivery.receipt, None)

    async def nack(self, delivery: WorkDelivery) -> None:
        await self.defer(
            delivery,
            delay_seconds=0,
            increment_attempt=True,
        )

    async def defer(
        self,
        delivery: WorkDelivery,
        *,
        delay_seconds: float,
        increment_attempt: bool = False,
    ) -> None:
        if (
            isinstance(delay_seconds, bool)
            or not isinstance(delay_seconds, (int, float))
            or not math.isfinite(delay_seconds)
            or delay_seconds < 0
        ):
            raise ValueError("delay_seconds must be finite and non-negative")
        async with self._lock:
            item = self._inflight.pop(delivery.receipt, None)
            if item is not None:
                retry_item = item.model_copy(
                    update={
                        "attempt": (
                            item.attempt + 1
                            if increment_attempt
                            else item.attempt
                        )
                    }
                )
                self._push(
                    retry_item,
                    available_at=monotonic() + delay_seconds,
                )

    async def depth(self) -> int:
        async with self._lock:
            return len(self._items) + len(self._inflight)

    async def healthcheck(self) -> bool:
        return True


class RedisWorkQueue(WorkQueue):
    """Redis Streams queue with consumer acknowledgements and idle reclaim."""

    persistent = True
    kind = "redis-stream"

    def __init__(
        self,
        url: str,
        *,
        stream: str = "relay:generation:work",
        group: str = "relay-workers",
        consumer: str | None = None,
        reclaim_idle_ms: int = 60_000,
    ) -> None:
        self._redis = Redis.from_url(url, decode_responses=True)
        self.stream = stream
        self.group = group
        self.consumer = consumer or f"worker-{uuid4()}"
        self.reclaim_idle_ms = reclaim_idle_ms
        self.delayed_key = f"{stream}:delayed"
        self._group_ready = False
        self._group_lock = Lock()

    _PROMOTE_DUE_SCRIPT = """
local members = redis.call(
  'ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, ARGV[2]
)
for _, member in ipairs(members) do
  local decoded_ok, envelope = pcall(cjson.decode, member)
  if decoded_ok and type(envelope) == 'table' and envelope['payload'] then
    redis.call('XADD', KEYS[2], '*', 'payload', envelope['payload'])
  end
  redis.call('ZREM', KEYS[1], member)
end
return #members
"""

    async def _ensure_group(self) -> None:
        if self._group_ready:
            return
        async with self._group_lock:
            if self._group_ready:
                return
            try:
                await self._redis.xgroup_create(
                    self.stream, self.group, id="0", mkstream=True
                )
            except ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise
            self._group_ready = True

    async def enqueue(self, item: WorkItem) -> None:
        await self._ensure_group()
        await self._redis.xadd(
            self.stream, {"payload": item.model_dump_json()}
        )

    async def dequeue(self) -> WorkDelivery | None:
        await self._ensure_group()
        await self._promote_due()
        messages = await self._redis.xreadgroup(
            self.group,
            self.consumer,
            {self.stream: ">"},
            count=1,
            block=1,
        )
        if not messages:
            reclaimed = await self._redis.xautoclaim(
                self.stream,
                self.group,
                self.consumer,
                min_idle_time=self.reclaim_idle_ms,
                start_id="0-0",
                count=1,
            )
            entries = reclaimed[1] if len(reclaimed) > 1 else []
            if not entries:
                return None
            message_id, fields = entries[0]
        else:
            _, entries = messages[0]
            message_id, fields = entries[0]
        return WorkDelivery(
            item=WorkItem.model_validate_json(fields["payload"]),
            receipt=message_id,
        )

    async def ack(self, delivery: WorkDelivery) -> None:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.xack(self.stream, self.group, delivery.receipt)
            pipe.xdel(self.stream, delivery.receipt)
            await pipe.execute()

    async def nack(self, delivery: WorkDelivery) -> None:
        await self.defer(
            delivery,
            delay_seconds=0,
            increment_attempt=True,
        )

    async def defer(
        self,
        delivery: WorkDelivery,
        *,
        delay_seconds: float,
        increment_attempt: bool = False,
    ) -> None:
        if (
            isinstance(delay_seconds, bool)
            or not isinstance(delay_seconds, (int, float))
            or not math.isfinite(delay_seconds)
            or delay_seconds < 0
        ):
            raise ValueError("delay_seconds must be finite and non-negative")
        await self._ensure_group()
        retry_item = delivery.item.model_copy(
            update={
                "attempt": (
                    delivery.item.attempt + 1
                    if increment_attempt
                    else delivery.item.attempt
                )
            }
        )
        envelope = json.dumps(
            {
                "id": str(uuid4()),
                "payload": retry_item.model_dump_json(),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.zadd(
                self.delayed_key,
                {envelope: time() + delay_seconds},
            )
            pipe.xack(self.stream, self.group, delivery.receipt)
            pipe.xdel(self.stream, delivery.receipt)
            await pipe.execute()

    async def _promote_due(self, *, limit: int = 100) -> int:
        promoted = await self._redis.eval(
            self._PROMOTE_DUE_SCRIPT,
            2,
            self.delayed_key,
            self.stream,
            time(),
            limit,
        )
        return int(promoted)

    async def depth(self) -> int:
        await self._ensure_group()
        # redis-py pipelines keep the two reads on one connection and avoid a
        # misleading half-snapshot when operations are moving due work.
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.xlen(self.stream)
            pipe.zcard(self.delayed_key)
            stream_depth, delayed_depth = await pipe.execute()
        return int(stream_depth) + int(delayed_depth)

    async def healthcheck(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:
            return False

    async def close(self) -> None:
        await self._redis.aclose()
