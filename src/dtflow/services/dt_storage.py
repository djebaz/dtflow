"""Read-only DT+ storage probe identity and advisory Prepare storage presentation data."""

from decimal import Decimal, InvalidOperation

from dtflow.core.models import BackendError, RemoteOutcome


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
    # Public backend diagnostics are string pairs, even when the original response field was numeric.
    if type(free) not in (str, int, float) or len(str(free)) > 64:
        return None
    try:
        value = Decimal(str(free))
    except InvalidOperation:
        return None
    return int(value) if value.is_finite() and 0 <= value <= 2**63 - 1 else None


def storage_size(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB"
