"""Thread-safe "latest value wins" hand-off from a worker thread to the GUI thread (shared by long jobs)."""

from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot


class LatestRelay(QObject):
    """Deliver the latest value reported from a worker thread to ``handler`` on the GUI thread (older ones drop)."""

    _ready = Signal()

    def __init__(self, handler: Callable[[object], None], parent: QObject, cancel: threading.Event | None = None):
        super().__init__(parent)
        self.cancel = cancel
        self._handler = handler
        self._lock = threading.Lock()
        self._value = None
        self._pending = False
        self.closed = False
        self._ready.connect(self._drain, Qt.ConnectionType.QueuedConnection)

    def __call__(self, value) -> None:
        with self._lock:
            self._value = value
            schedule, self._pending = not self._pending, True
        if schedule:
            self._ready.emit()

    def _drain(self) -> None:
        with self._lock:
            value, self._pending = self._value, False
        if not self.closed:
            self._handler(value)

    @Slot()
    def cancel_work(self) -> None:
        """Window-owned shutdown receiver: ask the worker to stop between chunks."""
        if self.cancel is not None:
            self.cancel.set()

    def close(self) -> None:
        """Stop delivering; late worker reports are dropped. The relay is deleted with its parent."""
        self.closed = True
