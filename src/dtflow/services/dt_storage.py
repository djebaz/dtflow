"""Read-only DT+ storage probe identity and advisory Prepare storage presentation data."""

import math
import secrets

from dtflow.core.models import AssetContent, BackendError, RemoteOutcome


def storage_probe_content(free_space: int | None = None) -> AssetContent:
    """A fresh made-up identity sized above observed headroom; never upload any returned URL."""
    size = free_space + 1024**2 if free_space is not None else 30 * 1024**3
    return AssetContent("lora", "dtflow-storage-probe.ckpt", secrets.token_hex(32), size, provenance="storage probe")


def storage_refusal(failure) -> bool:
    return (
        isinstance(failure, BackendError)
        and failure.failure.code == "cloud_storage_limit_exceeded"
        and failure.failure.remote_outcome == RemoteOutcome.NOT_STARTED
    )


def reported_free_space(failure) -> int | None:
    """Use only the provider's bounded numeric observation, never parse localized error text."""
    if not storage_refusal(failure):
        return None
    free = dict(failure.failure.diagnostics).get("storage_limit_free_space")
    return int(free) if type(free) in (int, float) and math.isfinite(free) and free >= 0 else None


def storage_size(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB"
