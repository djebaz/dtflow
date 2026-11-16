"""One kept DT+ backend for the Generate page, so a quick second generation skips the cold start.

A cold Generate builds a backend, opens the gRPC connection, exchanges the API key for a short token and reads the
inventory (an Echo) before it can send anything; owner traces measured about 1.6 s of that. This holder keeps the
backend (channel, cached token, inventory) after a generation that finished cleanly and hands it to the next one.

Safety rules, all enforced here:

* only after a clean success; any failure, cancellation or timeout discards the backend, so a possibly broken
  connection is never reused;
* only for the same key (opaque digest, re-checked at every use), the same concurrency setting and the same timeouts;
* only within ``idle`` seconds of the last use (then it is closed), on the event loop that created it, and one
  generation at a time (a second concurrent caller gets its own backend);
* it holds no state lock: the attempts store is opened and closed per generation exactly as before.

It changes no generation, retry or billing behavior; ``/authenticate`` is still sent for every generation.
"""

import asyncio
import logging
from collections.abc import Callable
from time import monotonic

from dtflow.diagnostics import event as diagnostic_event

log = logging.getLogger(__name__)

IDLE_SECONDS = 90.0
INVENTORY_REUSE_SECONDS = 300.0


class WarmLease:
    """One borrowed backend; ``reused`` tells whether it came from an earlier generation."""

    def __init__(self, backend, *, managed: bool, reused: bool):
        self.backend = backend
        self.managed = managed
        self.reused = reused


class WarmCloud:
    def __init__(self, *, idle: float = IDLE_SECONDS, clock: Callable[[], float] = monotonic):
        self._idle = idle
        self._clock = clock
        self._backend = None
        self._key: tuple | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ended = 0.0
        self._busy = False
        self._expiry: asyncio.Task | None = None

    @property
    def holding(self) -> bool:
        return self._backend is not None

    async def take(self, key: tuple, make: Callable[[], object]) -> WarmLease:
        """Hand out the kept backend when it still matches ``key`` and is fresh, else a new one."""
        loop = asyncio.get_running_loop()
        if self._busy:
            return WarmLease(make(), managed=False, reused=False)
        self._cancel_expiry()
        if self._backend is not None:
            reason = None
            if self._loop is not loop:
                reason = "other_loop"
            elif self._key != key:
                reason = "key_or_settings_changed"
            elif self._clock() - self._ended > self._idle:
                reason = "idle"
            if reason is None:
                self._busy = True
                diagnostic_event(
                    log,
                    "backend.warm_session",
                    level=logging.INFO,
                    data={"action": "reused", "idle_s": round(self._clock() - self._ended, 1)},
                )
                return WarmLease(self._backend, managed=True, reused=True)
            await self._discard(reason, close=self._loop is loop)
        backend = make()
        self._backend, self._key, self._loop, self._busy = backend, key, loop, True
        return WarmLease(backend, managed=True, reused=False)

    async def release(self, lease: WarmLease, *, keep: bool) -> None:
        """Give the backend back. ``keep`` is true only after a generation that finished cleanly."""
        if not lease.managed:
            await lease.backend.aclose()
            return
        self._busy = False
        if not keep:
            await self._discard("not_clean", close=True)
            return
        self._ended = self._clock()
        diagnostic_event(log, "backend.warm_session", level=logging.INFO, data={"action": "kept"})
        self._expiry = asyncio.get_running_loop().create_task(self._expire(self._backend))

    async def close(self) -> None:
        """Close any kept backend now (key switch, shutdown)."""
        if not self._busy:
            self._cancel_expiry()
            await self._discard("closed", close=True)

    def _cancel_expiry(self) -> None:
        task, self._expiry = self._expiry, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _expire(self, backend) -> None:
        await asyncio.sleep(self._idle)
        if self._backend is backend and not self._busy:
            self._expiry = None
            await self._discard("idle_expired", close=True)

    async def _discard(self, reason: str, *, close: bool) -> None:
        backend, self._backend, self._key, self._loop = self._backend, None, None, None
        if backend is None:
            return
        diagnostic_event(log, "backend.warm_session", level=logging.INFO, data={"action": "dropped", "reason": reason})
        if close:
            try:
                async with asyncio.timeout(5.0):
                    await backend.aclose()
            except Exception as exc:  # a failed close of an already discarded backend must never fail a generation
                log.warning("A kept DT+ connection could not be closed cleanly: %s", type(exc).__name__)
