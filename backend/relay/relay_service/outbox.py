from __future__ import annotations

from .queue import WorkQueue
from .repository import OutboxRepository


class OutboxDispatcher:
    """At-least-once bridge from the database outbox to the work queue."""

    def __init__(
        self,
        repository: OutboxRepository,
        queues: WorkQueue | dict[str, WorkQueue],
    ) -> None:
        self.repository = repository
        self.queues = (
            {"generation.submit": queues}
            if isinstance(queues, WorkQueue)
            else queues
        )

    async def dispatch_once(self, *, batch_size: int = 100) -> int:
        messages = await self.repository.claim_outbox(batch_size=batch_size)
        published = 0
        for message in messages:
            try:
                queue = self.queues.get(message.topic)
                if queue is None:
                    raise RuntimeError("No queue is registered for outbox topic")
                await queue.enqueue(message.item)
            except Exception as exc:
                # Never include queue payload or credentials in the persisted error.
                await self.repository.release_outbox(
                    message.id, f"{type(exc).__name__}: publish failed"
                )
                continue
            await self.repository.mark_outbox_published(message.id)
            published += 1
        return published
