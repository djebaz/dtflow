"""Narrow producer API for the shared standard-library diagnostics pipeline.

Callers select safe fields at the observation site. They do not serialize records,
assign sequence numbers, parse messages, or own sinks. Disabled levels stay cheap.
"""

import logging
from contextvars import ContextVar
from time import monotonic_ns

ATTEMPT: ContextVar[str] = ContextVar("dtflow_attempt", default="-")


def event(
    logger: logging.Logger,
    name: str,
    message: str = "",
    *args,
    level: int = logging.INFO,
    data: dict | None = None,
    context: dict | None = None,
    kind: str = "event",
    aggregation: dict | None = None,
    source_timestamp: str | None = None,
    source_mono_ns: int | None = None,
    exc_info=False,
) -> None:
    """Emit explicit semantic fields; shared capture handles privacy and failures.

    ``data`` is the caller's deliberate safe-field projection, not a request,
    provider object, configuration dump or arbitrary metadata dictionary.
    """
    if not logger.isEnabledFor(level):
        return
    logger.log(
        level,
        message,
        *args,
        exc_info=exc_info,
        extra={
            "dtflow_event": name,
            "dtflow_data": data if data is not None else {},
            "dtflow_context": context if context is not None else {},
            "dtflow_kind": kind,
            "dtflow_aggregation": aggregation,
            "dtflow_mono_ns": monotonic_ns() if source_mono_ns is None else source_mono_ns,
            "dtflow_source_ts": source_timestamp,
        },
    )


def measurement(logger: logging.Logger, name: str, **data) -> None:
    """A source measurement, with original types and unrounded units."""
    event(logger, name, level=logging.DEBUG, data=data, kind="measurement")


def source_summary(logger: logging.Logger, phase: str, stats: dict) -> None:
    """Already-aggregated observations; no invented CPU maximum or p95."""
    count = stats["samples"]
    event(
        logger,
        "gallery.source_summary",
        level=logging.DEBUG,
        kind="aggregate",
        source_timestamp=stats["window_end"],
        source_mono_ns=stats["last_mono_ns"],
        data={
            "phase": phase,
            "samples": count,
            "items": stats["items"],
            "wall_total_ms": stats["total"],
            "wall_avg_ms": stats["total"] / count,
            "wall_max_ms": stats["maximum"],
            "cpu_total_ms": stats["cpu"],
            "failures": stats["failures"],
        },
        aggregation={
            "samples": count,
            "window_start": stats["window_start"],
            "window_end": stats["window_end"],
            "metrics": {
                "wall_ms": {
                    "count": count,
                    "mean": stats["total"] / count,
                    "max": stats["maximum"],
                    "total": stats["total"],
                }
            },
        },
    )
