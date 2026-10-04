"""Bounded, synchronous event admission without persistence or transport I/O."""

from collections import deque
from dataclasses import replace

from dtflow.core.models import BackendEvent


class EventBuffer:
    """Coalesce transient progress/previews; never silently drop critical events."""

    def __init__(self, capacity: int = 64, preview_bytes: int = 2 * 1024 * 1024):
        if capacity < 4:
            raise ValueError("Event capacity must be at least four.")
        self.capacity = capacity
        self.preview_bytes = preview_bytes
        self._events: deque[BackendEvent] = deque()

    def __call__(self, event: BackendEvent) -> None:
        if event.kind == "preview" and event.artifact and len(event.artifact.encoded) > self.preview_bytes:
            raise ValueError("Preview exceeds its admission limit.")
        if event.kind in ("progress", "preview"):
            self._events = deque(x for x in self._events if x.kind != event.kind)
        if len(self._events) >= self.capacity:
            transient = next((x for x in self._events if x.kind in ("progress", "preview")), None)
            if transient is None:
                raise ValueError("Critical event capacity exhausted.")
            self._events.remove(transient)
        # Artifact bytes stay on the result/failure path; critical events keep identity only.
        self._events.append(replace(event, artifact=None) if event.kind == "artifact_received" else event)

    def drain(self) -> tuple[BackendEvent, ...]:
        events = tuple(self._events)
        self._events.clear()
        return events
