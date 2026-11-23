"""Shared exclusive-activity state between the Generate and Queues pages (Qt-free).

Queue dispatch holds the state ownership lock for its whole run, and a single generation holds it for its duration, so
the two never overlap in one window: each page disables its lock-taking actions while the other is active.
"""

from collections.abc import Callable

from dtflow.ui.progress_model import ProgressModel


class Activity:
    def __init__(self) -> None:
        self._generating = False
        self._dispatching = False
        self.progress = ProgressModel()  # live per-item evidence shared by both pages (events only, no outcomes)
        self._listeners: list[Callable[[], None]] = []
        self._progress_listeners: list[Callable[[], None]] = []

    @property
    def generating(self) -> bool:
        return self._generating

    @property
    def dispatching(self) -> bool:
        return self._dispatching

    def listen(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def listen_progress(self, listener: Callable[[], None]) -> None:
        """Be told when the shared progress changed without a change of state (an event, a tick, a new read)."""
        self._progress_listeners.append(listener)

    def unlisten(self, listener: Callable[[], None]) -> None:
        """Idempotently detach a presentation listener before its Qt owner dies."""
        if listener in self._listeners:
            self._listeners.remove(listener)
        if listener in self._progress_listeners:
            self._progress_listeners.remove(listener)

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    def refresh(self) -> None:
        """Ask the progress listeners (the global bar) to read the shared progress again."""
        for listener in tuple(self._progress_listeners):
            listener()

    def set(
        self,
        *,
        generating: bool | None = None,
        dispatching: bool | None = None,
    ) -> None:
        changed = False
        if generating is not None and generating != self._generating:
            self._generating = generating
            changed = True
        if dispatching is not None and dispatching != self._dispatching:
            self._dispatching = dispatching
            changed = True
        if changed:
            self._notify()
