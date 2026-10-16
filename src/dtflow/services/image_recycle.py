"""Explicit, durable Trash or permanent removal of committed managed originals.

No provider interaction, no permanent-delete fallback, and no mutation of the
attempt/queue audit tables. Trash operations and SQLite commits are not atomic.
"""

from __future__ import annotations

import logging
import os
import posixpath
import re
import stat
import threading
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from dtflow.core.gallery import GalleryQuery
from dtflow.persistence import gallery as gallery_storage
from dtflow.persistence import image_recycle as ledger
from dtflow.persistence import windows_original
from dtflow.persistence.original_io import original_images_lock, recycle_batch_lock
from dtflow.services.artifact_paths import ArtifactReadPaths, foreign_windows_path


class RecycleUnavailable(RuntimeError):
    """Sanitized system Trash failure; never includes a path or raw OS error."""

    def __init__(self, message: str, *, stage: str = "unknown"):
        super().__init__(message)
        self.stage = stage

    @property
    def diagnostic(self) -> str:
        return self.stage


_ATTEMPT_ID = re.compile(r"^[a-f0-9]{32}$")
log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RecycleTarget:
    """The exact original and file fingerprint validated at confirmation time."""

    identity: str
    path: str
    size: int
    mtime_ns: int
    inode: int
    device: int
    recorded_path: str = ""
    mounts: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class RecyclePlan:
    """An immutable, bounded target snapshot; never a live gallery query."""

    targets: tuple[RecycleTarget, ...]
    missing: int
    ineligible: int
    hidden: int
    requested: int
    action: str = "trash"


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
    failure_details: tuple[tuple[str, str], ...] = ()
    deleted: tuple[str, ...] = ()


def _managed_key(path: str) -> str:
    """Conservative alias-aware ownership count, without per-record filesystem I/O.

    Count even different-root references with the same managed attempt/output
    suffix: false ineligibility is safer than missing a Windows/Linux alias.
    """
    normalized = posixpath.normpath(path.replace("\\", "/")).casefold()
    match = re.search(r"(?:^|/)(outputs/[0-9a-f]{32}/final-[1-9][0-9]*\.png)$", normalized)
    return match[1] if match else normalized


def _regular_managed_file(
    state: Path, record: dict, *, reference_counts: dict[str, int] | None = None,
    permanent: bool = False, paths: ArtifactReadPaths | None = None,
) -> RecycleTarget | None:
    """Reject custom output roots, shared/redirected files and non-final images."""
    attempt = str(record["attempt_id"])
    ordinal = int(record["ordinal"])
    if not _ATTEMPT_ID.fullmatch(attempt) or ordinal < 0:
        return None
    root = Path(state).resolve() / "outputs"
    expected = root / attempt / f"final-{ordinal + 1}.png"
    metadata = root / attempt / "metadata.json"
    paths = paths or ArtifactReadPaths()
    try:
        candidate = Path(paths.resolve(record["path"]) if permanent else record["path"])
        metadata_path = paths.resolve(record["metadata_path"]) if permanent else record["metadata_path"]
    except OSError:
        return None
    # A managed original has the exact canonical path written by save_artifacts.
    if not candidate.is_absolute() or os.path.normcase(os.path.normpath(str(candidate))) != os.path.normcase(
        str(expected)
    ):
        return None
    if os.path.normcase(os.path.normpath(str(metadata_path))) != os.path.normcase(str(metadata)):
        return None
    if not expected.is_file():
        return None
    try:
        components = (root, expected.parent, expected, metadata)
        if permanent:
            components += tuple(Path(state).absolute().parents) + (Path(state).absolute(),)
        for component in components:
            if component.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(component)):
                return None
        if expected.resolve(strict=True) != expected or not metadata.is_file():
            return None
        info = expected.stat()
    except OSError:
        return None
    if not expected.is_file() or not info.st_size or info.st_nlink != 1:
        return None
    references = (
        gallery_storage.path_reference_count(state, record["path"], path_key=_managed_key if permanent else None)
        if reference_counts is None
        else reference_counts.get(_managed_key(record["path"]) if permanent else str(expected), 0)
    )
    if references != 1:
        return None
    return RecycleTarget(
        str(record["identity"]),
        str(expected),
        info.st_size,
        info.st_mtime_ns,
        info.st_ino,
        info.st_dev,
        record["path"] if permanent else "",
        paths.mounts if permanent else (),
    )


def prepare_recycle(
    state: Path, identities: tuple[str, ...], hidden: int = 0, query: GalleryQuery | None = None,
    *, trash_check=None, action: str = "trash",
) -> RecyclePlan:
    """Resolve exactly the checked identities, with no filesystem mutation.

    Custom --out locations are deliberately not accepted until their ownership
    and root authorization have an independent, explicit product contract.
    """
    if action not in ("trash", "permanent"):
        raise ValueError("Unknown image-removal action.")
    permanent = action == "permanent"
    paths = ArtifactReadPaths.discover() if permanent else ArtifactReadPaths()
    if permanent:
        if Path(state).absolute() != Path(state).resolve():
            raise RecycleUnavailable("Redirected application state is ineligible.", stage="state_redirected")
        # Discovery also refuses a shared-drive operation without the native helper.
        windows_original.bridge(state)
    unique = tuple(dict.fromkeys(identities))
    if not 1 <= len(unique) <= 10_000:
        raise ValueError("Recycle selection must contain between 1 and 10,000 images.")
    # Derive the hidden count from the same explicit checked-ID snapshot, not
    # an eventually refreshed UI label that may reflect the previous filter.
    if query is not None:
        _added, hidden, _omitted, _matched = gallery_storage.selection_candidates(state, query, unique)
    states = ledger.outcomes(state)
    eligible = []
    missing = ineligible = 0
    for offset in range(0, len(unique), gallery_storage.MAX_RECYCLE_PREFLIGHT_BATCH):
        batch = unique[offset : offset + gallery_storage.MAX_RECYCLE_PREFLIGHT_BATCH]
        records, reference_counts = gallery_storage.recycle_preflight_batch(
            state, batch, path_key=_managed_key if permanent else None
        )
        for identity in batch:
            if identity in states and states[identity][0] != "failed":
                ineligible += 1
                continue
            record = records.get(identity)
            if record is None:
                missing += 1
                continue
            target = _regular_managed_file(
                state, record, reference_counts=reference_counts, permanent=permanent, paths=paths
            )
            if target is None:
                try:
                    present = Path(paths.resolve(record["path"]) if permanent else record["path"]).is_file()
                except OSError:
                    present = True  # Unverifiable mapping is ineligible, not missing.
                if not present:
                    missing += 1
                else:
                    ineligible += 1
                continue
            if not permanent and trash_check is not None:
                try:
                    trash_check(Path(target.path))
                except (OSError, RecycleUnavailable):
                    ineligible += 1
                    continue
            eligible.append(target)
    return RecyclePlan(tuple(eligible), missing, ineligible, min(hidden, len(unique)), len(unique), action)


def _permanent_remove(state: Path, target: RecycleTarget) -> None:
    """Only reached after fresh service checks and committed permanent intent."""
    if os.name == "nt" or windows_original.bridge(state) is not None:
        try:
            windows_original.delete_managed(state, target)
        except OSError as exc:
            # Child loss/timeout may outlive our observation. Never classify this
            # as retryable merely because the source still appears unchanged.
            raise RecycleUnavailable("Windows deletion was not confirmed.", stage="delete_uncertain") from exc
        return
    if foreign_windows_path(target.recorded_path):
        raise RecycleUnavailable("No safe Windows mutation boundary.", stage="delete_unsupported")
    source = Path(target.path)
    directory = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        descriptor = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        try:
            info = os.fstat(descriptor)
            named = os.stat(source.name, dir_fd=directory, follow_symlinks=False)
            def fingerprint(value):
                return value.st_size, value.st_mtime_ns, value.st_ino, value.st_dev

            expected = (target.size, target.mtime_ns, target.inode, target.device)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or fingerprint(info) != expected:
                raise RecycleUnavailable("Original changed.", stage="source_changed")
            if fingerprint(named) != expected:
                raise RecycleUnavailable("Original changed.", stage="source_changed")
            os.unlink(source.name, dir_fd=directory)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def _unchanged(source: RecycleTarget) -> bool:
    try:
        target = Path(source.path)
        if target.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(target)):
            return False
        info = target.stat()
        return (info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev) == (
            source.size,
            source.mtime_ns,
            source.inode,
            source.device,
        )
    except OSError:
        return False


def recycle_outputs(
    state: Path,
    plan: RecyclePlan,
    stop: threading.Event,
    progress=None,
    *,
    recycle_adapter,
) -> RecycleResult:
    """Perform only the explicitly confirmed action with durable item checkpoints.

    Batch ownership prevents simultaneous Delete flows while the original-file
    lock is held only around each item. This allows generation saves between
    items instead of blocking them for an entire large recycle batch.
    """
    if plan.action not in ("trash", "permanent"):
        raise ValueError("Unknown image-removal action.")
    permanent = plan.action == "permanent"
    recycled: list[str] = []
    deleted: list[str] = []
    failed: list[str] = []
    uncertain: list[str] = []
    failure_details: list[tuple[str, str]] = []
    missing = plan.missing
    ineligible = plan.ineligible
    processed = 0
    operation_id = ("delete-" if permanent else "trash-") + uuid4().hex
    with recycle_batch_lock(state):
        ledger.start_operation(state, operation_id, len(plan.targets))
        for target in plan.targets:
            if stop.is_set():
                break
            with original_images_lock(state):
                record = gallery_storage.get_record(state, target.identity)
                if record is None or not _unchanged(target):
                    if not Path(target.path).is_file():
                        missing += 1
                    else:
                        ineligible += 1
                else:
                    paths = ArtifactReadPaths.discover() if permanent else ArtifactReadPaths()
                    confirmed = _regular_managed_file(state, record, permanent=permanent, paths=paths)
                    if confirmed != target:
                        ineligible += 1
                    elif not ledger.claim_recycle_intent(
                        state, operation_id, target.identity, target.recorded_path or target.path,
                        target.size, target.mtime_ns, permanent=permanent,
                    ):
                        # A preflight plan may predate another process's intent.
                        # Never overwrite pending/uncertain/removed evidence or
                        # submit a second move, even if the file looks unchanged.
                        ineligible += 1
                        if len(failure_details) < 100:
                            failure_details.append((target.identity, "ledger_requires_review"))
                    else:
                        try:
                            if permanent:
                                _permanent_remove(state, target)
                            else:
                                recycle_adapter(Path(target.path))
                        except (OSError, RecycleUnavailable) as exc:
                            # A Trash error after a disappearing original is uncertain.
                            # Never assume that it was successfully recycled.
                            state_value = "failed" if _unchanged(target) else "uncertain"
                            if isinstance(exc, RecycleUnavailable) and exc.stage == "delete_uncertain":
                                state_value = "uncertain"
                            if isinstance(exc, RecycleUnavailable):
                                detail = exc.diagnostic
                            else:
                                windows_code = getattr(exc, "winerror", None)
                                detail = f"os_error_{windows_code}" if isinstance(windows_code, int) else "os_error"
                            # Stable output identities and sanitized adapter stages
                            # are safe; do not log full paths or raw OS exceptions.
                            log.warning(
                                "image recycle outcome=%s identity=%s reason=%s",
                                state_value,
                                target.identity,
                                detail,
                            )
                            if len(failure_details) < 100:
                                failure_details.append((target.identity, detail))
                        else:
                            state_value = ("deleted" if permanent else "recycled") if not Path(target.path).exists() else "uncertain"
                        ledger.record_outcome(
                            state,
                            operation_id,
                            target.identity,
                            target.recorded_path or target.path,
                            state_value,
                            target.size,
                            target.mtime_ns,
                        )
                        if state_value == "recycled":
                            recycled.append(target.identity)
                        elif state_value == "deleted":
                            deleted.append(target.identity)
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
        tuple(recycled),
        tuple(failed),
        tuple(uncertain),
        missing,
        ineligible,
        len(plan.targets) - processed,
        canceled,
        tuple(failure_details),
        tuple(deleted),
    )
