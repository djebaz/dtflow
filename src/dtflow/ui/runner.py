"""Background asyncio execution for the Qt shell: coroutines never run on the GUI thread."""

import asyncio
import threading
from collections.abc import Callable, Coroutine

from PySide6.QtCore import QObject, Qt, Signal


class AsyncRunner(QObject):
    """One owned event-loop thread. Results return on the GUI thread through a queued signal."""

    _done = Signal(object, object, object)  # callback, result, exception

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._loop = asyncio.new_event_loop()
        self._pending: set = set()
        self._thread = threading.Thread(target=self._serve, name="dtflow-qt-async", daemon=True)
        self._done.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        self._thread.start()

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    @staticmethod
    def _deliver(callback, result, exception) -> None:
        callback(result, exception)

    def submit(self, coroutine: Coroutine, callback: Callable[[object, Exception | None], None]):
        """Run ``coroutine`` in the background; ``callback(result, exception)`` runs on the GUI thread."""

        async def wrapper():
            try:
                result, failure = await coroutine, None
            except Exception as exc:
                result, failure = None, exc
            self._done.emit(callback, result, failure)

        future = asyncio.run_coroutine_threadsafe(wrapper(), self._loop)
        self._pending.add(future)
        future.add_done_callback(self._pending.discard)
        return future

    def shutdown(self, timeout: float = 5.0) -> None:
        """Cancel outstanding work, stop the loop and join its thread."""
        for future in tuple(self._pending):
            future.cancel()
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout)
        if not self._thread.is_alive():
            self._loop.close()
