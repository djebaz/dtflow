"""Bounded source timings and one activity-scoped, window-owned GUI heartbeat."""

import logging
import sys
from functools import wraps
from threading import Event, Lock, Thread, enumerate as enumerate_threads, get_ident
from datetime import UTC, datetime
from time import monotonic, monotonic_ns, thread_time_ns

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Slot

from dtflow.diagnostics import measurement, source_summary
from dtflow.logging_setup import dev_enabled

log = logging.getLogger(__name__)
SOURCE_SUMMARY_INTERVAL_S = 60
GUI_CALLBACK_SLOW_MS = 50
GUI_STALL_SAMPLE_MS = 1000


def _safe_thread_stack(frame) -> str:
    """Retain only DTFlow function names, never stack locals or absolute paths."""
    parts = []
    for _ in range(12):
        if frame is None:
            break
        module = frame.f_globals.get("__name__", "")
        parts.append(
            f"{module}.{frame.f_code.co_name}" if isinstance(module, str) and module.startswith("dtflow.") else "external"
        )
        frame = frame.f_back
    return " > ".join(parts) if parts else "unavailable"



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
        self._gui_thread_id = get_ident()
        self._watch_stop = Event()
        self._watch_reported = False
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._tick)
        activity.listen(self.sync)
        progress_strip.operations_changed.connect(self.sync)
        runner.shutdown_started.connect(self.stop)
        window.destroyed.connect(self.stop)
        window.installEventFilter(self)
        if dev_enabled():
            # The Qt timer cannot diagnose its own long event-loop starvation.
            # This passive thread samples safe Python frame names once per stall.
            Thread(target=self._watch_gui, name="dtflow-gui-watchdog", daemon=True).start()

    def _watch_gui(self) -> None:
        """Record one sanitized main/runner stack sample per active GUI stall."""
        while not self._watch_stop.wait(0.25):
            scope = self._scope  # Qt updates this snapshot; never access Qt objects from this thread.
            if scope is None or self._watch_reported:
                continue
            elapsed_ms = (monotonic() - self._last) * 1000
            if elapsed_ms < GUI_STALL_SAMPLE_MS:
                continue
            self._watch_reported = True
            frames = sys._current_frames()
            runner_id = next((thread.ident for thread in enumerate_threads() if thread.name == "dtflow-qt-async"), None)
            measurement(
                log,
                "ui.gui_stall_sample",
                elapsed_ms=elapsed_ms,
                workspace=scope[0],
                operation=scope[1],
                gui_stack=_safe_thread_stack(frames.get(self._gui_thread_id)),
                runner_stack=_safe_thread_stack(frames.get(runner_id)),
            )

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
            self._watch_reported = False
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
        self._watch_reported = False
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
        self._watch_stop.set()
        self._activity.unlisten(self.sync)
        self._timer.stop()
        self._stats.flush(force=True)
