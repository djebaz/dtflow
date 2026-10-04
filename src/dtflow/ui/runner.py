"""Background asyncio execution for the Qt shell: coroutines never run on the GUI thread."""

import asyncio
import threading
from collections.abc import Callable, Coroutine

from PySide6.QtCore import QObject, Qt, Signal

TRANSIENT_EVENTS = ("progress", "preview")


class AsyncRunner(QObject):
    """One owned event-loop thread. Results return on the GUI thread through a queued signal."""

    _done = Signal(object, object, object)  # callback, result, exception

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._loop = asyncio.new_event_loop()
        self._pending: set = set()
        self._coroutines: list = []
        self._stop = asyncio.Event()
        self._closed = False
        self._thread = threading.Thread(target=self._serve, name="dtflow-qt-async", daemon=True)
        self._done.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        self._thread.start()

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self) -> None:
        # A stop requested before this loop starts is already set, so shutdown can never be lost.
        await self._stop.wait()
        current = asyncio.current_task()
        tasks = [task for task in asyncio.all_tasks() if task is not current]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _deliver(callback, result, exception) -> None:
        callback(result, exception)

    def submit(self, coroutine: Coroutine, callback: Callable[[object, Exception | None], None]):
        """Run ``coroutine`` in the background; ``callback(result, exception)`` runs on the GUI thread."""

        if self._closed:
            coroutine.close()
            raise RuntimeError("AsyncRunner is shut down.")
        self._coroutines.append(coroutine)

        started = False

        async def wrapper():
            nonlocal started
            started = True
            try:
                result, failure = await coroutine, None
            except Exception as exc:
                result, failure = None, exc
            except asyncio.CancelledError as exc:
                # A cancel requested through the returned future still reports back, so the GUI can show the outcome.
                if not self._closed:
                    self._done.emit(callback, None, exc)
                raise
            self._done.emit(callback, result, failure)

        future = asyncio.run_coroutine_threadsafe(wrapper(), self._loop)
        self._pending.add(future)
        future.add_done_callback(self._pending.discard)

        def cancelled_before_start(done) -> None:
            if done.cancelled() and not started and not self._closed:
                coroutine.close()
                self._done.emit(callback, None, asyncio.CancelledError())

        future.add_done_callback(cancelled_before_start)
        return future

    def shutdown(self, timeout: float = 5.0) -> None:
        """Cancel outstanding work, stop the loop and join its thread; safe at any time and idempotent."""
        self._closed = True
        if self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(timeout)
        if not self._thread.is_alive():
            for coroutine in self._coroutines:
                coroutine.close()  # a coroutine cancelled before it ever started is closed, not leaked
            self._loop.close()


class EventRelay(QObject):
    """Hand backend events from the background loop to the GUI thread without flooding it.

    Progress and previews are transient: only the latest of each kind is kept between GUI deliveries. Every other kind
    is critical; all kept events are delivered in sequence order. Delivery runs ``handler`` on the GUI thread through one queued signal.
    """

    _ready = Signal()

    def __init__(self, handler: Callable, parent: QObject | None = None):
        super().__init__(parent)
        self._handler = handler
        self._lock = threading.Lock()
        self._critical: list = []
        self._transient: dict = {}
        self._scheduled = False
        self._ready.connect(self._drain, Qt.ConnectionType.QueuedConnection)

    def __call__(self, event) -> None:
        """Event sink for the session service; safe to call from any thread."""
        with self._lock:
            if event.kind in TRANSIENT_EVENTS:
                self._transient[event.kind] = event
            else:
                self._critical.append(event)
            schedule, self._scheduled = not self._scheduled, True
        if schedule:
            self._ready.emit()

    def _drain(self) -> None:
        with self._lock:
            events = sorted((*self._critical, *self._transient.values()), key=lambda event: event.sequence)
            self._critical, self._transient, self._scheduled = [], {}, False
        for event in events:
            self._handler(event)
