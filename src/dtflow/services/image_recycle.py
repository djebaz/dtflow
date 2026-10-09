"""Safety-first, durable Windows Recycle Bin operations for committed saved images.

No provider interaction, no permanent-delete fallback, and no mutation of the
attempt/queue audit tables. Windows operations and SQLite commits are not atomic.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from dtflow.persistence import gallery as gallery_storage
from dtflow.persistence import image_recycle as ledger
from dtflow.persistence.original_io import original_images_lock
from dtflow.services.windows_recycle import RecycleUnavailable, recycle_file

_ATTEMPT_ID = re.compile(r"^[a-f0-9]{32}$")


@dataclass(frozen=True, slots=True)
class RecycleTarget:
    """The exact original and file fingerprint validated at confirmation time."""

    identity: str
    path: str
    size: int
    mtime_ns: int
    inode: int
    device: int


@dataclass(frozen=True, slots=True)
class RecyclePlan:
    """An immutable, bounded target snapshot; never a live gallery query."""

    targets: tuple[RecycleTarget, ...]
    missing: int
    ineligible: int
    hidden: int
    requested: int


@dataclass(frozen=True, slots=True)
class RecycleResult:
    """Per-operation settled counts with explicitly uncertain outcomes."""

    recycled: tuple[str, ...]
    failed: tuple[str, ...]
    uncertain: tuple[str, ...]
    missing: int
    ineligible: int
    unprocessed: int
    canceled: bool


def _regular_managed_file(state: Path, record: dict) -> RecycleTarget | None:
    """Reject custom output roots, shared/redirected files and non-final images."""
    attempt = str(record["attempt_id"])
    ordinal = int(record["ordinal"])
    if not _ATTEMPT_ID.fullmatch(attempt) or ordinal < 0:
        return None
    root = Path(state).resolve() / "outputs"
    expected = root / attempt / f"final-{ordinal + 1}.png"
    candidate = Path(record["path"])
    # A managed original has the exact canonical path written by save_artifacts.
    if not candidate.is_absolute() or os.path.normcase(os.path.normpath(str(candidate))) != os.path.normcase(str(expected)):
        return None
    if not expected.is_file():
        return None
    try:
        for component in (root, expected.parent, expected):
            if component.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(component)):
                return None
        if expected.resolve(strict=True) != expected:
            return None
        info = expected.stat()
    except OSError:
        return None
    if not expected.is_file() or not info.st_size or info.st_nlink != 1:
        return None
    if gallery_storage.path_reference_count(state, str(expected)) != 1:
        return None
    return RecycleTarget(
        str(record["identity"]), str(expected), info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev,
    )


def prepare_recycle(state: Path, identities: tuple[str, ...], hidden: int = 0) -> RecyclePlan:
    """Resolve exactly the checked identities, with no filesystem mutation.

    Custom --out locations are deliberately not accepted until their ownership
    and root authorization have an independent, explicit product contract.
    """
    unique = tuple(dict.fromkeys(identities))
    if not 1 <= len(unique) <= 10_000:
        raise ValueError("Recycle selection must contain between 1 and 10,000 images.")
    if os.name != "nt":
        return RecyclePlan((), 0, len(unique), min(hidden, len(unique)), len(unique))
    states = ledger.outcomes(state)
    eligible = []
    missing = ineligible = 0
    for identity in unique:
        if identity in states and states[identity][0] in ("pending", "uncertain", "recycled"):
            ineligible += 1
            continue
        record = gallery_storage.get_record(state, identity)
        if record is None:
            missing += 1
            continue
        target = _regular_managed_file(state, record)
        if target is None:
            ineligible += 1
            continue
        eligible.append(target)
    return RecyclePlan(tuple(eligible), missing, ineligible, min(hidden, len(unique)), len(unique))


def _unchanged(source: RecycleTarget) -> bool:
    try:
        target = Path(source.path)
        if target.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(target)):
            return False
        info = target.stat()
        return (info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev) == (
            source.size, source.mtime_ns, source.inode, source.device
        )
    except OSError:
        return False


def recycle_outputs(
    state: Path,
    plan: RecyclePlan,
    stop: threading.Event,
    progress=None,
    *,
    recycle_adapter=recycle_file,
) -> RecycleResult:
    """Execute only confirmed targets; checkpoint each intent/outcome durably.

    The shared original-files lease excludes participating exports and other
    recycle processes. Revalidation occurs under that lease before each item.
    """
    recycled: list[str] = []
    failed: list[str] = []
    uncertain: list[str] = []
    missing = plan.missing
    ineligible = plan.ineligible
    processed = 0
    operation_id = uuid4().hex
    with original_images_lock(state):
        ledger.start_operation(state, operation_id, len(plan.targets))
        for target in plan.targets:
            if stop.is_set():
                break
            record = gallery_storage.get_record(state, target.identity)
            if record is None or not _unchanged(target):
                # A changed, replaced, shared or missing source must never be moved.
                if not Path(target.path).is_file():
                    missing += 1
                else:
                    ineligible += 1
                processed += 1
                if progress:
                    progress(processed, len(plan.targets))
                continue
            confirmed = _regular_managed_file(state, record)
            if confirmed != target:
                ineligible += 1
                processed += 1
                if progress:
                    progress(processed, len(plan.targets))
                continue
            ledger.record_outcome(
                state, operation_id, target.identity, target.path, "pending",
                target.size, target.mtime_ns,
            )
            try:
                recycle_adapter(Path(target.path))
            except (OSError, RecycleUnavailable):
                # The Shell may have completed before signaling an error.
                # Never assume a missing original was successfully recycled.
                state_value = "failed" if _unchanged(target) else "uncertain"
            else:
                state_value = "recycled" if not Path(target.path).exists() else "uncertain"
            ledger.record_outcome(
                state, operation_id, target.identity, target.path, state_value,
                target.size, target.mtime_ns,
            )
            if state_value == "recycled":
                recycled.append(target.identity)
            elif state_value == "uncertain":
                uncertain.append(target.identity)
            else:
                failed.append(target.identity)
            processed += 1
            if progress:
                progress(processed, len(plan.targets))
        canceled = stop.is_set()
        ledger.finish_operation(state, operation_id, "canceled" if canceled else "complete")
    return RecycleResult(
        tuple(recycled), tuple(failed), tuple(uncertain),
        missing, ineligible, len(plan.targets) - processed, canceled,
    )
