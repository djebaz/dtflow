"""Read-only DT+ storage probe identity and advisory Prepare storage presentation data."""

from decimal import Decimal, InvalidOperation

from collections.abc import Mapping, Sequence

from dtflow.core.models import BackendError, RemoteOutcome, StoredAsset


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


def shortfall(needed: int, free: int | None) -> int | None:
    """Bytes still missing for ``needed`` on a key with ``free`` bytes; None when the free space is unknown."""
    return None if free is None else max(0, needed - free)


def free_order(files: Sequence[StoredAsset], seen: Mapping[str, str]) -> tuple[StoredAsset, ...]:
    """Oldest-first candidates for freeing: files this computer has no record of, then oldest recorded confirmation.

    ``seen`` is when this computer last confirmed each file for the key. DTFlow stores no per-generation use time,
    so this is a recency proxy, never a claim about what was used last."""
    return tuple(sorted(files, key=lambda item: (item.key in seen, seen.get(item.key, ""), item.name)))


def select_to_free(files: Sequence[StoredAsset], seen: Mapping[str, str], missing: int) -> tuple[StoredAsset, ...]:
    """The first files of ``free_order`` whose sizes cover ``missing`` bytes; empty when nothing is missing."""
    chosen: list[StoredAsset] = []
    total = 0
    for item in free_order(files, seen):
        if total >= missing:
            break
        chosen.append(item)
        total += item.size
    return tuple(chosen) if missing > 0 else ()
