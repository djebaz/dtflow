"""Which provider previews are worth decoding right now (#560): a thread-safe, Qt-free visibility policy.

The Generate grid names the requests whose tiles are on screen; the backend asks before it decodes a preview, so a
hidden or scrolled-away tile costs no decoding, and a visible one at most one preview per interval. Frames the policy
refuses are dropped, never queued.
"""

import threading
import time
from collections.abc import Callable, Iterable

PREVIEW_INTERVAL = 1.0  # seconds between decoded previews for one request


class PreviewGate:
    def __init__(self, interval: float = PREVIEW_INTERVAL, clock: Callable[[], float] = time.monotonic):
        self._interval, self._clock = interval, clock
        self._lock = threading.Lock()
        self._wanted: frozenset[str] = frozenset()
        self._last: dict[str, float] = {}

    def set_wanted(self, request_ids: Iterable[str]) -> None:
        """The requests whose tiles are visible now (an empty set while the grid is hidden)."""
        wanted = frozenset(request_ids)
        with self._lock:
            self._wanted = wanted
            self._last = {key: value for key, value in self._last.items() if key in wanted}

    def visible(self, request_id: str) -> bool:
        """Whether the request's tile is in view now; spends nothing (a later check of a preview already admitted)."""
        with self._lock:
            return request_id in self._wanted

    def wants(self, request_id: str) -> bool:
        """True at most once per interval for a visible request; this call is the one that spends the interval."""
        now = self._clock()
        with self._lock:
            if request_id not in self._wanted:
                return False
            last = self._last.get(request_id)
            if last is not None and now - last < self._interval:
                return False
            self._last[request_id] = now
            return True
