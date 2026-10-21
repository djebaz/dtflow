"""Shared, Qt-independent queue remaining-time estimator."""

import math
from datetime import datetime, timezone
from statistics import median


def estimate_queue_seconds(
    counts: dict,
    durations: tuple[float, ...] | list[float],
    running_starts: tuple[str, ...] | list[str] | None = None,
    *,
    now: datetime | None = None,
) -> float | None:
    """Estimate remaining wall time using settled durations and active reservations."""
    running = counts.get("running", 0)
    pending = counts.get("pending", 0)
    samples = [float(v) for v in durations if type(v) in (int, float) and math.isfinite(v) and v >= 0]
    if running <= 0 or not samples:
        return None
    typical = median(samples)
    if running_starts is not None and len(running_starts) == running:
        current = now or datetime.now(timezone.utc)
        remaining = []
        for stamp in running_starts:
            try:
                started = datetime.fromisoformat(stamp)
                if started.tzinfo is None:
                    break
                age = max(0.0, (current - started).total_seconds())
            except (TypeError, ValueError, OverflowError):
                break
            remaining.append(max(0.0, typical - age))
        if len(remaining) == running:
            seconds = (pending * typical + sum(remaining)) / running
            return seconds if seconds > 0 else None
    seconds = typical * (pending + running) / running
    return seconds if seconds > 0 else None
