"""Shared exclusive-activity state between the Generate and Queues pages (Qt-free).

Queue dispatch holds the state ownership lock for its whole run, and a single generation holds it for its duration, so
the two never overlap in one window: each page disables its lock-taking actions while the other is active.
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunState:
    """In-window execution presentation; durable truth stays in the shared services."""

    owner: str = "none"
    phase: str = "idle"
    action: str | None = None
    completed: int | None = None
    total: int | None = None
    queue_id: str | None = None
    detail: str = "No runnable queue selected."


class Activity:
    def __init__(self) -> None:
        self._generating = False
        self._dispatching = False
        self._run_state = RunState()
        self._listeners: list[Callable[[], None]] = []

    @property
    def generating(self) -> bool:
        return self._generating

    @property
    def dispatching(self) -> bool:
        return self._dispatching

    @property
    def run_state(self) -> RunState:
        return self._run_state

    def listen(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    def present_run(self, state: RunState) -> None:
        if state == self._run_state:
            return
        self._run_state = state
        self._notify()

    def set(
        self,
        *,
        generating: bool | None = None,
        dispatching: bool | None = None,
        run_state: RunState | None = None,
    ) -> None:
        changed = False
        if generating is not None and generating != self._generating:
            self._generating = generating
            changed = True
        if dispatching is not None and dispatching != self._dispatching:
            self._dispatching = dispatching
            changed = True
        if run_state is not None and run_state != self._run_state:
            self._run_state = run_state
            changed = True
        if changed:
            self._notify()
