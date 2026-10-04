"""Shared exclusive-activity state between the Generate and Queues pages (Qt-free).

Queue dispatch holds the state ownership lock for its whole run, and a single generation holds it for its duration, so
the two never overlap in one window: each page disables its lock-taking actions while the other is active.
"""

from collections.abc import Callable


class Activity:
    def __init__(self) -> None:
        self._generating = False
        self._dispatching = False
        self._listeners: list[Callable[[], None]] = []

    @property
    def generating(self) -> bool:
        return self._generating

    @property
    def dispatching(self) -> bool:
        return self._dispatching

    def listen(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def set(self, *, generating: bool | None = None, dispatching: bool | None = None) -> None:
        if generating is not None:
            self._generating = generating
        if dispatching is not None:
            self._dispatching = dispatching
        for listener in self._listeners:
            listener()
