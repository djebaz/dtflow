"""Ordered admission of provider generation calls under one dispatch owner (#557).

The scarce resource is an *active provider generation*, not a queue item. One :class:`ProviderPool` per backend key
(``cloud:-``, ``local:host:port``) is shared by every queue the owner dispatches, so the configured limit holds across
queues, Scripts and Workflows. A call holds one of ``limit`` slots while the provider samples it and frees it as soon as
the provider phase ends (sampling complete, stream closed); decoding, encoding and saving then continue without a slot.
Calls that already left the provider phase stay *open* until they settle, and open calls are capped at ``limit *
OVERLAP``, so a stuck download can never pile up unbounded paid requests behind the freed slots.

Ordinary queue items are admitted when the dispatcher claims them (:meth:`QueueAdmission.reserve`), so no claimed item
waits behind a gate; children of Scripts and Workflows and automatic retries wait in order (:meth:`acquire`). Older
queues come first: a call may take the next slot only if no waiting call or ready queue ranks ahead of it. Everything runs
on the owner's event loop; no locks. The limit is a utilization target: nothing here tries to remove a brief overshoot
that a race between claims could cause, only sustained excess.
"""

import asyncio
import heapq
import logging
from itertools import count
from time import monotonic

from collections.abc import Callable

from dtflow.core.provider_phase import OVERLAP
from dtflow.diagnostics import event as diagnostic_event

log = logging.getLogger(__name__)

# Rank classes: lower is served first. Ordinary queues share one class; a manual request class can sit before it.
PRIORITY_MANUAL, PRIORITY_QUEUE = 0, 1
# A call that waited this long for a slot is worth one record; shorter waits are routine scheduling.
SLOW_WAIT_SECONDS = 30.0

Rank = tuple[int, int]


class Permit:
    """One admitted call. ``provider_done`` frees its slot, ``close`` ends the call; both are idempotent."""

    def __init__(self, pool: "ProviderPool", rank: Rank):
        self.pool, self.rank = pool, rank
        self.held = True  # holds a slot
        self.open = True  # counts as an open call
        self.since = monotonic()  # when the slot was taken

    def provider_done(self, reason: str = "stream_end") -> None:
        """Free the slot (idempotent); ``reason`` says what ended the provider phase and is recorded once."""
        if self.held:
            self.held = False
            self.pool.active -= 1
            diagnostic_event(
                log,
                "admission.slot_released",
                level=logging.INFO,
                data={
                    "backend": self.pool.key,
                    "reason": reason,
                    "held_ms": round((monotonic() - self.since) * 1000),
                    "active": self.pool.active,
                    "open": self.pool.open,
                    "limit": self.pool.limit,
                },
            )
            self.pool.changed()

    def close(self) -> None:
        self.provider_done("closed")
        if self.open:
            self.open = False
            self.pool.open -= 1
            self.pool.changed()

    async def reacquire(self, on_wait: Callable[[], None] | None = None) -> None:
        """Take a slot again (a video that gave its slot up while it waited for a local video lease)."""
        if not self.held and self.open:
            await self.pool.wait_for_slot(self.rank, self, on_wait)


class ProviderPool:
    def __init__(self, key: str, limit: int):
        self.key, self.limit = key, limit
        self.active = 0  # calls holding a slot
        self.open = 0  # calls admitted and not yet closed
        self._waiters: list[tuple[Rank, int, asyncio.Future, Permit | None]] = []
        self._hungry: dict[Rank, int] = {}  # queues with claimable work, by rank
        self._watchers: set[asyncio.Future] = set()
        self._seq = count()

    # -- state ---------------------------------------------------------------------------------------------------

    def widen(self, limit: int) -> None:
        """A Workflow run asked for a wider run than the shared default; the pool never narrows under a live run."""
        if limit > self.limit:
            self.limit = limit
            self.changed()

    def _best(self) -> Rank | None:
        ranks = [item[0] for item in self._waiters if not item[2].done()]
        ranks.extend(self._hungry)
        return min(ranks, default=None)

    def _free(self, *, new: bool = True) -> bool:
        return self.active < self.limit and (not new or self.open < self.limit * OVERLAP)

    def _may(self, rank: Rank) -> bool:
        best = self._best()
        return best is None or rank <= best

    def spare(self) -> bool:
        """Capacity no started queue or waiting call wants: the next Up next queue may start and use it."""
        return self._free() and self._best() is None

    def want(self, rank: Rank, ready: bool) -> None:
        """A dispatcher reports whether its queue has work it could claim now."""
        if ready:
            self._hungry[rank] = 1
        elif self._hungry.pop(rank, None) is not None:
            self.changed()

    def forget(self, rank: Rank) -> None:
        self.want(rank, False)

    # -- admission -----------------------------------------------------------------------------------------------

    def reserve(self, rank: Rank) -> Permit | None:
        """Admit a claimed item now, or None when no slot is free or an older caller is ahead of it."""
        if not (self._free() and self._may(rank)):
            return None
        self.active += 1
        self.open += 1
        return Permit(self, rank)

    async def acquire(self, rank: Rank, on_wait: Callable[[], None] | None = None) -> Permit:
        """Wait, in rank order, for a slot (Script/Workflow children and automatic retries)."""
        permit = self.reserve(rank) if not self._waiters else None
        if permit is not None:
            return permit
        return await self.wait_for_slot(rank, None, on_wait)

    async def wait_for_slot(self, rank: Rank, permit: Permit | None, on_wait: Callable[[], None] | None = None):
        started = monotonic()
        if on_wait is not None:
            on_wait()
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters, (rank, next(self._seq), future, permit))
        self._grant()
        try:
            granted = await future
        except BaseException:
            if future.done() and not future.cancelled():
                granted = future.result()  # granted while being cancelled: hand the slot back
                if permit is None:
                    granted.close()
                else:
                    granted.provider_done("cancelled")
            else:
                future.cancel()
                self._grant()
            raise
        waited = monotonic() - started
        if waited >= SLOW_WAIT_SECONDS:
            diagnostic_event(
                log,
                "admission.slot_wait",
                level=logging.INFO,
                data={"waited_ms": round(waited * 1000), "limit": self.limit, "active": self.active, "open": self.open},
            )
        return granted

    def _grant(self) -> None:
        while self._waiters:
            rank, _, future, permit = self._waiters[0]
            if future.done():
                heapq.heappop(self._waiters)
                continue
            hungry = min(self._hungry, default=None)
            if not self._free(new=permit is None) or (hungry is not None and hungry < rank):
                break
            heapq.heappop(self._waiters)
            self.active += 1
            if permit is None:
                self.open += 1
                permit = Permit(self, rank)
            else:
                permit.held = True
                permit.since = monotonic()
            future.set_result(permit)

    # -- wake-ups ------------------------------------------------------------------------------------------------

    def changed(self) -> None:
        """Capacity or demand changed: grant waiters, wake dispatchers."""
        self._grant()
        for future in self._watchers:
            if not future.done():
                future.set_result(None)
        self._watchers.clear()

    def watch(self) -> asyncio.Future:
        """A future resolved at the next change, for a dispatcher to wait on beside its own tasks."""
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._watchers.add(future)
        return future

    def unwatch(self, future: asyncio.Future) -> None:
        self._watchers.discard(future)
        if not future.done():
            future.cancel()


class QueueAdmission:
    """One queue's view of its backend's pool, with the queue's rank."""

    def __init__(self, pool: ProviderPool, rank: Rank):
        self.pool, self.rank = pool, rank

    def reserve(self) -> Permit | None:
        return self.pool.reserve(self.rank)

    async def acquire(self, on_wait: Callable[[], None] | None = None) -> Permit:
        return await self.pool.acquire(self.rank, on_wait)

    def want(self, ready: bool) -> None:
        self.pool.want(self.rank, ready)

    def forget(self) -> None:
        self.pool.forget(self.rank)


class ProviderAdmission:
    """The owner's pools, one per backend key, and the queue ranks (older queues first)."""

    def __init__(self):
        self._pools: dict[str, ProviderPool] = {}
        self._ranks = count()

    def next_rank(self, klass: int = PRIORITY_QUEUE) -> Rank:
        return klass, next(self._ranks)

    def pool(self, key: str, limit: int) -> ProviderPool:
        """The shared pool; the first queue to open a backend fixes its limit for this owner session (no live change)."""
        pool = self._pools.get(key)
        if pool is None:
            pool = self._pools[key] = ProviderPool(key, limit)
            diagnostic_event(
                log,
                "admission.pool_opened",
                level=logging.INFO,
                data={"backend": key, "limit": limit, "overlap": OVERLAP},
            )
        return pool

    def existing(self, key: str) -> ProviderPool | None:
        return self._pools.get(key)

    def spare(self, key: str) -> bool:
        pool = self._pools.get(key)
        return pool is not None and pool.spare()
