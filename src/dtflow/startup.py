"""Development-mode startup phase timings.

Marks are buffered cheaply and logged as one line once the window is up, because logging is configured after the first
imports. Only ``-x`` development logging records them; they change diagnostic detail, never behavior.
"""

import logging
import threading
import time

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
    parts = []
    for name, moment in marks:
        parts.append(f"{name}={(moment - previous) * 1000:.0f}ms")
        previous = moment
    log.debug("%s: %s total=%.0fms", title, " ".join(parts), (previous - STARTED) * 1000)
