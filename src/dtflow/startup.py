"""Development-mode startup phase timings.

Marks are buffered cheaply and logged as one line once the window is up, because logging is configured after the first
imports. Only ``-x`` development logging records them; they change diagnostic detail, never behavior.
"""

import logging
import threading
import time

from dtflow.diagnostics import event as diagnostic_event

STARTED = time.perf_counter()
log = logging.getLogger("dtflow.startup")
_marks: list[tuple[str, float]] = []
_lock = threading.Lock()
_awaited: tuple[str, ...] = ()
_deferred_title: str | None = None


def mark(name: str) -> None:
    global _awaited, _deferred_title
    with _lock:
        _marks.append((name, time.perf_counter()))
        title = None
        if name in _awaited:
            _awaited, title, _deferred_title = (), _deferred_title, None
    if title is not None:
        report(title)


def expect(*names: str) -> None:
    """Hold the next ``report`` until one of ``names`` is marked, so the report includes that milestone."""
    global _awaited
    with _lock:
        _awaited = names


def report(title: str) -> None:
    """Log each phase's duration since the previous mark, in milliseconds."""
    global _deferred_title
    with _lock:
        if _awaited:
            _deferred_title = title
            return
    if not log.isEnabledFor(logging.DEBUG):
        return
    with _lock:
        marks = list(_marks)
    previous = STARTED
    phase_timings = []
    for name, moment in marks:
        phase_timings.append({"phase": name, "elapsed_ms": (moment - previous) * 1000})
        previous = moment
    diagnostic_event(
        log,
        "app.startup_timings",
        level=logging.DEBUG,
        data={
            "title": title,
            "phases": phase_timings,
            "elapsed_ms": (previous - STARTED) * 1000,
        },
    )
