"""Bounded source timings and one activity-scoped, window-owned GUI heartbeat."""

import logging
from functools import wraps
from datetime import UTC, datetime
from threading import Lock
from time import monotonic, monotonic_ns, thread_time_ns

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Slot

from dtflow.diagnostics import measurement, source_summary
from dtflow.logging_setup import dev_enabled

log = logging.getLogger(__name__)
SOURCE_SUMMARY_INTERVAL_S = 60
GUI_CALLBACK_SLOW_MS = 50


class PerfBatchStats:
    """Existing bounded thread-safe source timings; no sink or independent logger setup."""

    def __init__(self, logger, *, namespace="gallery") -> None:
        self.logger = logger
        self.namespace = namespace
        self._lock = Lock()
        self._pending: dict[str, dict] = {}
        self._emitted: dict[str, float] = {}

    def observe(
        self, phase: str, elapsed_ms: float, *, count: int = 1, cpu_ms: float = 0.0, success: bool = True
    ) -> None:
        observed_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        observed_mono = monotonic_ns()
        with self._lock:
            stats = self._pending.setdefault(
                phase,
                {
                    "samples": 0,
                    "items": 0,
                    "total": 0.0,
                    "maximum": 0.0,
                    "cpu": 0.0,
                    "failures": 0,
                    "window_start": observed_at,
                    "window_end": observed_at,
                },
            )
            stats["window_start"] = min(stats["window_start"], observed_at)
            stats["window_end"] = max(stats["window_end"], observed_at)
            stats["last_mono_ns"] = max(stats.get("last_mono_ns", 0), observed_mono)
            stats["samples"] += 1
            stats["items"] += count
            stats["total"] += elapsed_ms
            stats["maximum"] = max(stats["maximum"], elapsed_ms)
            stats["cpu"] += cpu_ms
            stats["failures"] += int(not success)

    def flush(self, phase: str | None = None, *, force: bool = False) -> None:
        now = monotonic()
        with self._lock:
            names = (phase,) if phase is not None else tuple(self._pending)
            due = [
                name
                for name in names
                if name in self._pending
                and (force or now - self._emitted.get(name, float("-inf")) >= SOURCE_SUMMARY_INTERVAL_S)
            ]
            completed = [(name, self._pending.pop(name)) for name in due]
            for name in due:
                self._emitted[name] = now
        for name, stats in completed:
            source_summary(self.logger, name, stats, namespace=self.namespace)


def profile_slow_gui_callback(name: str):
    """Report only slow, completed GUI callbacks; never alter their result or exception."""

    def decorate(callback):
        @wraps(callback)
        def wrapped(*args, **kwargs):
            if not dev_enabled():
                return callback(*args, **kwargs)
            wall_started = monotonic_ns()
            cpu_started = thread_time_ns()
            try:
                return callback(*args, **kwargs)
            finally:
                elapsed_ms = (monotonic_ns() - wall_started) / 1_000_000
                if elapsed_ms >= GUI_CALLBACK_SLOW_MS:
                    measurement(
                        log,
                        "ui.callback_slow",
                        callback=name,
                        elapsed_ms=elapsed_ms,
                        cpu_ms=(thread_time_ns() - cpu_started) / 1_000_000,
                    )

        return wrapped

    return decorate


class GuiHeartbeat(QObject):
    """Observe only Images browsing or existing in-window work; never control that work."""

    def __init__(self, window, activity, progress_strip, runner, context, flush_gallery) -> None:
        super().__init__(window)
        self._activity = activity
        self._context = context
        self._flush_gallery = flush_gallery
        self._closed = False
        self._scope = None
        self._last = monotonic()
        self._last_flush = self._last
        self._stats = PerfBatchStats(log, namespace="ui")
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._tick)
        activity.listen(self.sync)
        progress_strip.operations_changed.connect(self.sync)
        runner.shutdown_started.connect(self.stop)
        window.destroyed.connect(self.stop)
        window.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.WindowStateChange):
            self.sync()
        return False

    @Slot()
    def sync(self) -> None:
        if self._closed:
            return
        context = self._context() if dev_enabled() else None
        scope = (context["workspace"], context["operation"]) if context else None
        if scope != self._scope:
            self._stats.flush(force=True)
            self._scope = scope
            self._last = self._last_flush = monotonic()
        if context is None:
            self._timer.stop()
        elif not self._timer.isActive():
            self._last = self._last_flush = monotonic()
            self._timer.start()

    @Slot()
    def _tick(self) -> None:
        if self._closed:
            return
        context = self._context()
        if context is None or (context["workspace"], context["operation"]) != self._scope:
            self.sync()
            return
        now = monotonic()
        gap_ms = (now - self._last) * 1000
        self._last = now
        if gap_ms >= 50:
            self._stats.observe("gui_gaps", gap_ms)
        if gap_ms >= 100:
            measurement(log, "ui.gui_gap", elapsed_ms=gap_ms, **context)
        if now - self._last_flush >= 2:
            self._last_flush = now
            self._stats.flush()
            self._flush_gallery()

    @Slot()
    def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._activity.unlisten(self.sync)
        self._timer.stop()
        self._stats.flush(force=True)
