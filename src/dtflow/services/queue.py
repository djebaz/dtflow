"""Rolling queue dispatch over the shared generation service.

One dispatcher owns the state directory (through the store's ownership lock), claims items one at a time in queue order,
and starts the next eligible item the moment a reservation is released: there is no group barrier. Reservations are
taken durably before an attempt exists. Nothing here replays an attempt; an item that reached the remote boundary and
came back unknown stays reserved and blocks its backend until an explicit, recorded override. No database transaction is
held while generating.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from dtflow.core.models import BackendError, ResolvedGenerationRequest
from dtflow.core.tasks import settle
from dtflow.persistence.store import PersistenceError, SQLiteAttempts
from dtflow.services.generation import GenerationService

log = logging.getLogger(__name__)


class QueueDispatcher:
    def __init__(
        self,
        store: SQLiteAttempts,
        service: GenerationService,
        *,
        poll: float = 0.5,
        on_settle: Callable[[str, str], None] | None = None,
    ):
        self.store, self.service, self.poll, self.on_settle = store, service, poll, on_settle

    async def run(self, queue_id: str) -> dict[str, Any]:
        """Dispatch until the queue is finished, cancelled, or blocked by uncertainty with nothing active.

        A paused queue with nothing active keeps polling so a posted Resume can continue it; interrupting the process
        (Ctrl-C) ends dispatch and records honest evidence for whatever was active.
        """
        limit = self.service.backend.capabilities.max_concurrency
        tasks: dict[asyncio.Task, str] = {}
        failure: BaseException | None = None
        await self.store.apply_controls(queue_id)
        await self.store.set_running(queue_id)
        try:
            while True:
                applied = await self.store.apply_controls(queue_id)
                if "cancel" in applied:
                    # Interruption of running attempts is local only; the service records any remote uncertainty.
                    for task in tasks:
                        task.cancel()
                while failure is None:
                    claim = await self.store.claim_next(queue_id, limit)
                    if claim is None:
                        break
                    item_id, request = claim
                    task = asyncio.create_task(self._execute(item_id, request))
                    tasks[task] = item_id
                if tasks:
                    done, _ = await asyncio.wait(tasks, timeout=self.poll, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        del tasks[task]
                        if not task.cancelled() and task.exception() is not None:
                            failure = failure or task.exception()
                    continue
                if failure is not None:
                    raise failure
                state = await self.store.inspect_queue(queue_id)
                if state["state"] == "cancelled" or not (state["summary"]["pending"] or state["summary"]["running"]):
                    return state
                if state["blocked_by_uncertainty"]:
                    return state
                await asyncio.sleep(self.poll)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))

    async def _execute(self, item_id: str, request: ResolvedGenerationRequest) -> None:
        failure: dict[str, Any] | None = None
        try:
            await self.service.generate(request)
        except asyncio.CancelledError:
            await settle(self.store.settle_item(item_id))
            raise
        except BackendError as exc:
            failure = {
                "code": exc.failure.code,
                "category": str(exc.failure.category),
                "submission_stage": str(exc.failure.submission_stage),
                "remote_outcome": str(exc.failure.remote_outcome),
            }
        except PersistenceError:
            # The state store itself failed: leave the durable reservation for the next owner's reconciliation.
            log.error("queue item %s could not be settled; reconciliation will classify it", item_id)
            raise
        except Exception as exc:
            failure = {"code": "unexpected", "category": type(exc).__name__}
        state = await settle(self.store.settle_item(item_id, failure))
        if self.on_settle is not None:
            self.on_settle(item_id, state)
        log.info("queue item %s settled state=%s", item_id, state)
