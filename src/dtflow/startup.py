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


def mark(name: str) -> None:
    with _lock:
        _marks.append((name, time.perf_counter()))


def report(title: str) -> None:
    """Log each phase's duration since the previous mark, in milliseconds."""
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
        log, "app.startup_timings",
        level=logging.DEBUG,
        data={
            "title": title,
            "phases": phase_timings,
            "elapsed_ms": (previous - STARTED) * 1000,
        },
    )
