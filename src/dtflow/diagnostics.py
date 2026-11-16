"""Narrow producer API for the shared standard-library diagnostics pipeline.

Callers select safe fields at the observation site. They do not serialize records,
assign sequence numbers, parse messages, or own sinks. Disabled levels stay cheap.
"""

import hashlib
import logging
import re
from contextvars import ContextVar
from pathlib import PureWindowsPath
from time import monotonic_ns


def cloud_key_data(
    identity: tuple[str, str | None] | None, source: str | None = None, *, alias: str | None = None
) -> dict:
    """Identify the pinned cloud key with a safe human label, never its secret."""
    if identity is None:
        return {}
    credential_id, hint = identity
    safe_alias = (
        alias
        if type(alias) is str
        and 0 < len(alias) <= 40
        and alias.isprintable()
        and not re.search(r"dk_[A-Za-z0-9]{6,}", alias)
        else None
    )
    label = safe_alias or (f"Key {hint}••••" if hint else f"Key {credential_id[:4].upper()}")
    return {
        "credential_id": credential_id,
        "key_alias": label,
        **({"key_source": source} if source in ("saved", "environment") else {}),
    }


def asset_ref_data(
    *,
    sha256: str | None = None,
    source_sha256: str | None = None,
    asset_id: str | None = None,
    operation_id: str | None = None,
    filename: str | None = None,
) -> dict:
    """Project known asset identities into safe, consistent diagnostic fields.

    SHA identifies content; asset and operation IDs identify local records and
    separate attempts. This helper does not generate identities or persist state.
    Full filenames and paths never enter its returned fields.
    """
    return {
        **({"asset_sha_prefix": sha256[:12]} if sha256 else {}),
        **({"source_sha_prefix": source_sha256[:12]} if source_sha256 else {}),
        **({"asset_id": asset_id} if asset_id else {}),
        **({"operation_id": operation_id} if operation_id else {}),
        **({"filename_prefix": PureWindowsPath(filename).name[:3]} if filename else {}),
    }


_TIMING = False


def set_timing(enabled: bool) -> None:
    """Switch the opt-in ``-time`` timing detail for this process; it never changes generation, retry or billing."""
    global _TIMING
    _TIMING = bool(enabled)


_PERF = False


def set_perf(enabled: bool) -> None:
    """Switch the opt-in ``-perf`` performance measurements; they are written only together with ``-x``."""
    global _PERF
    _PERF = bool(enabled)


def perf_requested() -> bool:
    return _PERF


def timing_enabled() -> bool:
    return _TIMING


def timing_level() -> int:
    """INFO when ``-time`` is on (so the record needs no ``-x``), else DEBUG as before."""
    return logging.INFO if _TIMING else logging.DEBUG


def request_fingerprint(request) -> dict:
    """Short one-way digests that tell whether two generations repeated the prompt or the other settings.

    Eight hex characters of SHA-256 each; the text itself is never logged. Only used with ``-time``.
    """

    def digest(*parts) -> str:
        return hashlib.sha256("\x1f".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:8]

    return {
        "text_fp": digest(request.prompt),
        "neg_fp": digest(request.negative_prompt),
        "settings_fp": digest(
            request.model_id,
            request.seed,
            request.width_px,
            request.height_px,
            request.steps,
            request.guidance,
            request.sampler_id,
            request.operation,
            *(f"{item.identifier}:{item.weight}" for item in request.loras),
        ),
        "steps": request.steps,
        "width": request.width_px,
        "height": request.height_px,
        "loras": len(request.loras),
    }


ATTEMPT: ContextVar[str] = ContextVar("dtflow_attempt", default="-")


def api_error_data(*, message=None, body=None, headers=(), private=(), truncated=False) -> dict:
    """Bounded API error evidence, safe for both public failures and shared logs.

    Privacy stays owned by the shared schema module; integration callers supply
    only response primitives and known request-specific private strings.
    """
    from dtflow._diagnostic_schema import api_error_data as project

    return project(message=message, body=body, headers=headers, private=private, truncated=truncated)


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
    """A source measurement, with original types and unrounded units; written only with ``-x -perf``."""
    if not _PERF:
        return
    event(logger, name, level=logging.DEBUG, data=data, kind="measurement")


def source_summary(logger: logging.Logger, phase: str, stats: dict, *, namespace: str = "gallery") -> None:
    """Already-aggregated observations; no invented CPU maximum or p95. Written only with ``-x -perf``."""
    if not _PERF:
        return
    count = stats["samples"]
    event(
        logger,
        f"{namespace}.source_summary",
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
