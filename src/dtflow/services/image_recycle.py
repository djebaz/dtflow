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
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from dtflow.core.gallery import GalleryQuery
from dtflow.diagnostics import event as diagnostic_event
from dtflow.persistence import gallery as gallery_storage
from dtflow.persistence import image_recycle as ledger
from dtflow.core.output_layout import companion_names, output_key, raw_of_first_final
from dtflow.persistence import thumbnail_cache, windows_original
from dtflow.persistence.original_io import (
    OriginalImagesUnavailableError,
    original_images_lock,
    recycle_batch_lock,
    require_originals_ready,
    windows_originals_bridge,
)
from dtflow.services.artifact_paths import ArtifactReadPaths, foreign_windows_path
from dtflow.services.managed_original_provenance import (
    declared_raw_artifact,
    declared_video_companion,
    file_sha256,
    matches_managed_state,
    verify_managed_sidecar,
)


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
    """The exact original and file fingerprint validated at confirmation time.

    A final carries its attempt's declared ``raw-1.png`` as ``raw`` (an image-operation attempt only), so one
    logical gallery image is confirmed, leased and reported together while each physical file keeps its own
    durable ledger row.
    """

    identity: str
    path: str
    size: int
    mtime_ns: int
    inode: int
    device: int
    recorded_path: str = ""
    mounts: tuple[tuple[str, str], ...] = ()
    kind: str = "final"
    digest: str = ""
    raw: RecycleTarget | None = None
    video: RecycleTarget | None = None

    @property
    def ledger_identity(self) -> str:
        return self.identity if self.kind == "final" else f"{self.identity}:{self.kind}"


@dataclass(frozen=True, slots=True)
class AbsentTarget:
    """A verified missing original and its immutable provenance snapshot."""

    identity: str
    path: str
    recorded_path: str
    metadata_fingerprint: tuple[int, int, int, int]
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
    absent_targets: tuple[AbsentTarget, ...] = ()

    @property
    def raw_count(self) -> int:
        return sum(target.raw is not None for target in self.targets)

    @property
    def video_count(self) -> int:
        return sum(target.video is not None for target in self.targets)


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
    absent: tuple[str, ...] = ()
    raw: tuple[str, ...] = ()  # images whose raw-1.png was disposed, whatever became of their final
    video: tuple[str, ...] = ()  # video clips disposed, even if their poster remains


def _managed_key(path: str) -> str:
    """Conservative alias-aware ownership count, without per-record filesystem I/O.

    Count even different-root references with the same managed attempt/output
    suffix: false ineligibility is safer than missing a Windows/Linux alias.
    """
    normalized = posixpath.normpath(path.replace("\\", "/")).casefold()
    match = output_key(normalized)
    return f"outputs/{match[1]}/{match[2]}" if match else normalized


def _recorded_tail(recorded: object) -> tuple[str, str] | None:
    """The run folder and file name a recorded path ends with (either separator), else None."""
    if not isinstance(recorded, str):
        return None
    parts = recorded.replace("\\", "/").rsplit("/", 2)
    return (parts[-2], parts[-1]) if len(parts) >= 2 else None


def _raw_companion(
    expected: Path, metadata: Path, record: dict, *, keep_recorded: bool, mounts: tuple[tuple[str, str], ...]
) -> RecycleTarget | None:
    """The attempt's declared raw provider output, None when absent; OSError when present but not provably ours.

    Only the canonical sibling of the final is considered, never a directory scan, and an undeclared file
    named raw-1.png is ignored. A declared file must be plain, single-link and named by the sidecar.
    """
    raw_file = raw_of_first_final(expected.name, str(record["attempt_id"]))
    if raw_file is None:
        return None
    raw = expected.parent / raw_file
    try:
        attrs = raw.lstat()
    except FileNotFoundError:
        return None
    declared = declared_raw_artifact(metadata, record)
    if declared is None:
        return None
    if (
        raw.is_symlink()
        or (hasattr(os.path, "isjunction") and os.path.isjunction(raw))
        or getattr(attrs, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        or not stat.S_ISREG(attrs.st_mode)
        or attrs.st_nlink != 1
        or not attrs.st_size
        or raw.resolve(strict=True) != raw
    ):
        raise OSError("Raw output is not a plain regular file.")
    recorded_raw, digest = declared
    return RecycleTarget(
        str(record["identity"]),
        str(raw),
        attrs.st_size,
        attrs.st_mtime_ns,
        attrs.st_ino,
        attrs.st_dev,
        recorded_raw if keep_recorded else "",
        mounts,
        kind="raw",
        digest=digest,
    )


def _video_companion(
    expected: Path, metadata: Path, record: dict, *, keep_recorded: bool, mounts: tuple[tuple[str, str], ...]
) -> RecycleTarget | None:
    """Authorize a present MP4 only through the sidecar's exact video declaration."""
    clip = expected.with_suffix(".mp4")
    try:
        attrs = clip.lstat()
    except FileNotFoundError:
        return None
    declared = declared_video_companion(metadata, record)
    if declared is None:
        # A matching filename without a video declaration is not ours to remove.
        return None
    name, recorded_size = declared
    if (
        clip.name != name
        or attrs.st_size != recorded_size
        or clip.is_symlink()
        or (hasattr(os.path, "isjunction") and os.path.isjunction(clip))
        or getattr(attrs, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        or not stat.S_ISREG(attrs.st_mode)
        or attrs.st_nlink != 1
        or clip.resolve(strict=True) != clip
    ):
        raise OSError("Video companion is not the declared regular MP4.")
    recorded_video = str(record["path"])[:-4] + ".mp4"
    return RecycleTarget(
        str(record["identity"]),
        str(clip),
        attrs.st_size,
        attrs.st_mtime_ns,
        attrs.st_ino,
        attrs.st_dev,
        recorded_video if keep_recorded else "",
        mounts,
        kind="video",
    )


def _regular_managed_file(
    state: Path,
    record: dict,
    *,
    reference_counts: dict[str, int] | None = None,
    permanent: bool = False,
    paths: ArtifactReadPaths | None = None,
) -> RecycleTarget | None:
    """Reject custom output roots, shared/redirected files and non-final images."""
    attempt = str(record["attempt_id"])
    ordinal = int(record["ordinal"])
    if not _ATTEMPT_ID.fullmatch(attempt) or ordinal < 0:
        return None
    root = Path(state).resolve() / "outputs"
    tail_parts = _recorded_tail(record.get("path"))
    names = companion_names(*tail_parts, attempt, ordinal) if tail_parts else None
    if names is None:
        return None
    folder, image_name = tail_parts
    expected = root / folder / image_name
    metadata = root / folder / names[0]
    # A foreign spelling is never a filesystem path to delete directly.
    # Derive the sole eligible native target from saved state and DB identity,
    # rather than authorizing deletion through resolve_output_read().
    foreign_posix = os.name == "nt" and isinstance(record.get("path"), str) and record["path"].startswith("/")
    windows_wsl_alias = foreign_posix and record["path"].startswith("/mnt/")
    if foreign_posix:
        tail = ("outputs", folder)
        if (
            not windows_wsl_alias
            or record.get("identity") != f"{attempt}:{ordinal}"
            or Path(state).absolute() != Path(state).resolve()
            or not matches_managed_state(state, record["path"], (*tail, image_name))
            or not matches_managed_state(state, record["metadata_path"], (*tail, names[0]))
        ):
            return None
    paths = paths or ArtifactReadPaths()
    try:
        candidate = (
            expected if windows_wsl_alias else Path(paths.resolve(record["path"]) if permanent else record["path"])
        )
        metadata_path = (
            metadata
            if windows_wsl_alias
            else Path(paths.resolve(record["metadata_path"]) if permanent else record["metadata_path"])
        )
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
        if permanent or os.name == "nt":
            components += tuple(Path(state).absolute().parents) + (Path(state).absolute(),)
        for component in components:
            if component.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(component)):
                return None
            if os.name == "nt" and getattr(component.lstat(), "st_file_attributes", 0) & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
            ):
                return None
        if (
            expected.resolve(strict=True) != expected
            or metadata.resolve(strict=True) != metadata
            or not metadata.is_file()
        ):
            return None
        if windows_wsl_alias:
            # Bound sidecar provenance to the saved attempt/path. The content hash is a separate step
            # (_alias_content_verified) done before the original-file lease; ledger and fingerprint checks
            # remain independent, fresh removal-authorization gates.
            verify_managed_sidecar(expected, metadata, record, check_digest=False)
        info = expected.stat()
    except OSError:
        return None
    if not expected.is_file() or not info.st_size or info.st_nlink != 1:
        return None
    alias_aware = permanent or os.name == "nt"
    references = (
        gallery_storage.path_reference_count(state, record["path"], path_key=_managed_key if alias_aware else None)
        if reference_counts is None
        else reference_counts.get(_managed_key(record["path"]) if alias_aware else str(expected), 0)
    )
    if references != 1:
        return None
    keep_recorded, mounts = permanent or windows_wsl_alias, paths.mounts if permanent else ()
    try:
        raw = (
            _raw_companion(expected, metadata, record, keep_recorded=keep_recorded, mounts=mounts)
            if ordinal == 0
            else None
        )
        video = (
            _video_companion(expected, metadata, record, keep_recorded=keep_recorded, mounts=mounts)
            if ordinal == 0
            else None
        )
    except OSError:
        return None
    return RecycleTarget(
        str(record["identity"]),
        str(expected),
        info.st_size,
        info.st_mtime_ns,
        info.st_ino,
        info.st_dev,
        record["path"] if keep_recorded else "",
        mounts,
        raw=raw,
        video=video,
    )


def _is_wsl_alias(recorded: str) -> bool:
    return os.name == "nt" and recorded.startswith("/")


def _redirected(component: Path) -> bool:
    attrs = component.lstat()
    return (
        component.is_symlink()
        or (hasattr(os.path, "isjunction") and os.path.isjunction(component))
        or bool(getattr(attrs, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    )


def _content_verified(target: RecycleTarget) -> bool:
    """Hash provenance digests before taking the original-file lease, bound to the frozen fingerprint.

    Covers a WSL1-recorded final's image-operation digest and the sidecar-declared digest of its raw output.
    The result requires the fingerprint to be identical before and after hashing; the lease-held revalidation
    then proves the file still has it, so hashing never lengthens the lease.
    """
    if target.raw is not None and not _content_verified(target.raw):
        return False
    if target.video is not None and not _content_verified(target.video):
        return False
    source = Path(target.path)
    frozen = (target.size, target.mtime_ns, target.inode, target.device)
    if target.kind == "video":
        # The sidecar declaration and frozen fingerprint are checked in
        # _regular_managed_file and again under the mutation lease.
        return True
    if target.kind == "raw":
        if not target.digest:
            return False

        def check() -> None:
            if file_sha256(source) != target.digest:
                raise OSError("Raw output does not match its declared digest.")

    elif _is_wsl_alias(target.recorded_path):
        attempt, _, ordinal = target.identity.partition(":")
        row = {"attempt_id": attempt, "path": target.recorded_path}
        names = companion_names(source.parent.name, source.name, attempt, int(ordinal)) if ordinal.isdigit() else None
        if names is None:
            return False

        def check() -> None:
            verify_managed_sidecar(source, source.parent / names[0], row)

    else:
        return True
    try:
        before = source.stat()
        check()
        after = source.stat()
    except OSError:
        return False
    return all((info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev) == frozen for info in (before, after))


def _confirmed_absent_final(
    state: Path,
    record: dict,
    *,
    reference_counts: dict[str, int] | None = None,
    paths: ArtifactReadPaths,
) -> AbsentTarget | None:
    """Verify a missing final under a canonical managed state.

    With the attempt folder present, its matching sidecar must still prove the record. If the whole folder is
    gone (for example deleted in a file manager) absence is trusted only while the managed outputs directory
    is intact and listable. A failed path mapping, unavailable parent or unreadable file is not absence.
    Never use the read-only gallery alias as deletion authority.
    """
    attempt, ordinal = record.get("attempt_id"), record.get("ordinal")
    if (
        not isinstance(attempt, str)
        or not _ATTEMPT_ID.fullmatch(attempt)
        or type(ordinal) is not int
        or ordinal < 0
        or record.get("identity") != f"{attempt}:{ordinal}"
    ):
        return None
    root = Path(state).absolute()
    try:
        if root.resolve(strict=True) != root:
            return None
    except OSError:
        return None
    recorded, metadata = record.get("path"), record.get("metadata_path")
    if not isinstance(recorded, str) or not isinstance(metadata, str):
        return None
    tail_parts = _recorded_tail(recorded)
    names = companion_names(*tail_parts, attempt, ordinal) if tail_parts else None
    if names is None:
        return None
    image = root / "outputs" / tail_parts[0] / tail_parts[1]
    sidecar = image.parent / names[0]
    if _is_wsl_alias(recorded):
        tail = ("outputs", tail_parts[0])
        if not matches_managed_state(root, recorded, (*tail, image.name)) or not matches_managed_state(
            root, metadata, (*tail, names[0])
        ):
            return None
    else:
        try:
            native, native_metadata = Path(paths.resolve(recorded)), Path(paths.resolve(metadata))
        except (OSError, ValueError, TypeError):
            return None
        if not native.is_absolute() or os.path.normcase(os.path.normpath(str(native))) != os.path.normcase(str(image)):
            return None
        if os.path.normcase(os.path.normpath(str(native_metadata))) != os.path.normcase(str(sidecar)):
            return None
    outputs = root / "outputs"
    try:
        for component in (*reversed(root.parents), root, outputs):
            if _redirected(component):
                return None
        if not stat.S_ISDIR(outputs.stat().st_mode):
            return None
        try:
            image.parent.lstat()
        except FileNotFoundError:
            # No sidecar remains to verify: require a readable outputs directory, so an unreachable or
            # unlistable location is never mistaken for a deleted attempt folder.
            with os.scandir(outputs):
                pass
            fingerprint = (0, 0, 0, 0)
        else:
            if _redirected(image.parent) or _redirected(sidecar):
                return None
            if not stat.S_ISDIR(image.parent.stat().st_mode):
                return None
            info = sidecar.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or sidecar.resolve(strict=True) != sidecar:
                return None
            try:
                image.lstat()
            except FileNotFoundError:
                pass
            else:
                return None
            # A missing poster does not prove a declared video companion is gone.
            # Never hide the record as absent while any sibling MP4 remains.
            try:
                image.with_suffix(".mp4").lstat()
            except FileNotFoundError:
                pass
            else:
                return None
            verify_managed_sidecar(image, sidecar, record, check_digest=False, check_size=False)
            after = sidecar.stat()
            fingerprint = (info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev)
            if fingerprint != (after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev):
                return None
    except (OSError, ValueError):
        return None
    count = (
        gallery_storage.path_reference_count(state, recorded, path_key=_managed_key)
        if reference_counts is None
        else reference_counts.get(_managed_key(recorded), 0)
    )
    if count != 1:
        return None
    return AbsentTarget(str(record["identity"]), str(image), recorded, fingerprint, paths.mounts)


def prepare_recycle(
    state: Path,
    identities: tuple[str, ...],
    hidden: int = 0,
    query: GalleryQuery | None = None,
    *,
    trash_check=None,
    action: str = "trash",
) -> RecyclePlan:
    """Resolve exactly the checked identities, with no filesystem mutation.

    Custom --out locations are deliberately not accepted until their ownership
    and root authorization have an independent, explicit product contract.
    """
    if action not in ("trash", "permanent"):
        raise ValueError("Unknown image-removal action.")
    require_originals_ready(state)
    permanent = action == "permanent"
    paths = ArtifactReadPaths.discover() if permanent else ArtifactReadPaths()
    if permanent:
        if Path(state).absolute() != Path(state).resolve():
            diagnostic_event(
                log,
                "images.recycle.preflight_refused",
                level=logging.DEBUG,
                data={
                    "action": action,
                    "stage": "state_redirected",
                },
            )
            raise RecycleUnavailable("Redirected application state is ineligible.", stage="state_redirected")
    started = time.monotonic()
    unique = tuple(dict.fromkeys(identities))
    if not 1 <= len(unique) <= 10_000:
        raise ValueError("Recycle selection must contain between 1 and 10,000 images.")
    # Derive the hidden count from the same explicit checked-ID snapshot, not
    # an eventually refreshed UI label that may reflect the previous filter.
    if query is not None:
        _added, hidden, _omitted, _matched = gallery_storage.selection_candidates(state, query, unique)
    states = ledger.outcomes(state)
    eligible = []
    absent_targets = []
    missing = ineligible = 0
    for offset in range(0, len(unique), gallery_storage.MAX_RECYCLE_PREFLIGHT_BATCH):
        batch = unique[offset : offset + gallery_storage.MAX_RECYCLE_PREFLIGHT_BATCH]
        records, reference_counts = gallery_storage.recycle_preflight_batch(
            state, batch, path_key=_managed_key if permanent or os.name == "nt" else None
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
                if permanent:
                    absent_target = _confirmed_absent_final(
                        state, record, reference_counts=reference_counts, paths=paths
                    )
                    if absent_target is not None:
                        absent_targets.append(absent_target)
                    else:
                        ineligible += 1
                    continue
                if os.name == "nt" and isinstance(record.get("path"), str) and record["path"].startswith("/"):
                    # Unverified WSL aliases are ineligible, not proven absent.
                    # Durable confirmed-absence handling is tracked separately.
                    ineligible += 1
                    continue
                try:
                    present = Path(paths.resolve(record["path"]) if permanent else record["path"]).is_file()
                except OSError:
                    present = True  # Unverifiable mapping is ineligible, not missing.
                if not present:
                    missing += 1
                else:
                    ineligible += 1
                continue
            if target.raw is not None:
                # A present raw file with an unsettled or terminal ledger row is ambiguous: refuse the image.
                raw_state = states.get(f"{target.identity}:raw")
                if raw_state is not None and raw_state[0] != "failed":
                    ineligible += 1
                    continue
            if target.video is not None:
                video_state = states.get(f"{target.identity}:video")
                if video_state is not None and video_state[0] != "failed":
                    ineligible += 1
                    continue
            if not permanent and trash_check is not None:
                try:
                    trash_check(Path(target.path))
                    if target.raw is not None:
                        trash_check(Path(target.raw.path))
                    if target.video is not None:
                        trash_check(Path(target.video.path))
                except (OSError, RecycleUnavailable):
                    ineligible += 1
                    continue
            eligible.append(target)
    plan = RecyclePlan(
        tuple(eligible), missing, ineligible, min(hidden, len(unique)), len(unique), action, tuple(absent_targets)
    )
    diagnostic_event(
        log,
        "images.recycle.preflight",
        level=logging.DEBUG,
        data={
            "action": action,
            "requested": plan.requested,
            "eligible": len(plan.targets),
            "raw": plan.raw_count,
            "video": plan.video_count,
            "absent": len(plan.absent_targets),
            "missing": plan.missing,
            "ineligible": plan.ineligible,
            "hidden": plan.hidden,
            "elapsed_ms": (time.monotonic() - started) * 1000,
        },
    )
    return plan


def _permanent_remove(state: Path, target: RecycleTarget) -> None:
    """Only reached after fresh service checks and committed permanent intent."""
    try:
        windows_boundary = os.name == "nt" or windows_originals_bridge(state) is not None
    except (OriginalImagesUnavailableError, OSError, UnicodeError, ValueError) as exc:
        # No helper or disposition call could have been reached.
        raise RecycleUnavailable("Shared Windows deletion boundary is unavailable.", stage="delete_refused") from exc
    if windows_boundary:
        try:
            windows_original.delete_managed(state, target)
        except windows_original.RefusedBeforeMutation as exc:
            raise RecycleUnavailable("Windows deletion was refused before mutation.", stage="delete_refused") from exc
        except (OSError, ValueError) as exc:
            # Disposition, helper loss or acknowledgment may be unconfirmed.
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


def _refused(identity: str, stage: str) -> None:
    """A confirmed image refused by its fresh execution checks; nothing was changed."""
    diagnostic_event(
        log,
        "images.recycle.item_refused",
        level=logging.DEBUG,
        data={
            "stage": stage,
        },
    )


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


def _dispose(
    state: Path,
    operation_id: str,
    target: RecycleTarget,
    *,
    permanent: bool,
    recycle_adapter,
    failure_details: list[tuple[str, str]],
) -> str:
    """Claim, perform and settle one physical file; return its settled state, or ``review`` if the claim is refused.

    Called under the batch and original-file leases. A refused claim leaves the file untouched.
    """
    path = target.recorded_path or target.path
    if not ledger.claim_recycle_intent(
        state, operation_id, target.ledger_identity, path, target.size, target.mtime_ns, permanent=permanent
    ):
        # A preflight plan may predate another process's intent. Never overwrite
        # pending/uncertain/removed evidence or submit a second move.
        return "review"
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
        diagnostic_event(
            log,
            "images.recycle.image_recycle",
            level=logging.WARNING,
            data={
                "outcome": state_value,
                "kind": target.kind,
                "reason": detail,
            },
        )
        if len(failure_details) < 100:
            failure_details.append((target.identity, detail if target.kind == "final" else f"{target.kind}_{detail}"))
    else:
        state_value = ("deleted" if permanent else "recycled") if not Path(target.path).exists() else "uncertain"
    ledger.record_outcome(state, operation_id, target.ledger_identity, path, state_value, target.size, target.mtime_ns)
    return state_value


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
    absent: list[str] = []
    failed: list[str] = []
    uncertain: list[str] = []
    failure_details: list[tuple[str, str]] = []
    missing = plan.missing
    ineligible = plan.ineligible
    processed = 0
    operation_id = ("delete-" if permanent else "trash-") + uuid4().hex
    targets = (*plan.targets, *plan.absent_targets)
    raw_disposed: list[str] = []
    video_disposed: list[str] = []
    started = time.monotonic()
    with recycle_batch_lock(state):
        # Requested counts are physical files, matching the per-file outcomes that complete them.
        ledger.start_operation(state, operation_id, len(targets) + plan.raw_count + plan.video_count)
        diagnostic_event(
            log,
            "images.recycle.started",
            level=logging.DEBUG,
            data={
                "operation": operation_id,
                "images": len(plan.targets),
                "raw": plan.raw_count,
                "video": plan.video_count,
                "absent": len(plan.absent_targets),
            },
        )
        for target in targets:
            if stop.is_set():
                break
            # Provenance hashing happens here, outside the lease, bound to the frozen fingerprint.
            content_verified = isinstance(target, AbsentTarget) or _content_verified(target)
            # This lease MUST precede the per-image ledger claim and external mutation.
            # UI zero-processed refusal wording relies on this ordering: a failed
            # first lease cannot have modified an original.
            with original_images_lock(state):
                record = gallery_storage.get_record(state, target.identity)
                if isinstance(target, AbsentTarget):
                    paths = ArtifactReadPaths.discover()
                    fresh = _confirmed_absent_final(state, record, paths=paths) if record is not None else None
                    if fresh != target:
                        ineligible += 1
                        _refused(target.identity, "absence_changed")
                    elif not ledger.record_confirmed_absent(
                        state, operation_id, target.identity, target.recorded_path
                    ):
                        ineligible += 1
                        if len(failure_details) < 100:
                            failure_details.append((target.identity, "ledger_requires_review"))
                    else:
                        absent.append(target.identity)
                elif record is None or not _unchanged(target):
                    if not Path(target.path).is_file():
                        missing += 1
                        _refused(target.identity, "missing")
                    else:
                        ineligible += 1
                        _refused(target.identity, "source_changed")
                elif not content_verified:
                    ineligible += 1
                    _refused(target.identity, "content_unverified")
                else:
                    paths = ArtifactReadPaths.discover() if permanent else ArtifactReadPaths()
                    confirmed = _regular_managed_file(state, record, permanent=permanent, paths=paths)
                    if confirmed != target:
                        ineligible += 1
                        _refused(target.identity, "ownership_changed")
                    else:
                        # Dispose verified secondary files before the poster, preserving
                        # the visible result if a companion fails or is uncertain.
                        video_state = None
                        if target.video is not None:
                            video_state = _dispose(
                                state, operation_id, target.video,
                                permanent=permanent, recycle_adapter=recycle_adapter,
                                failure_details=failure_details,
                            )
                            if video_state in ("recycled", "deleted"):
                                video_disposed.append(target.identity)
                        raw_state = None
                        if video_state in (None, "recycled", "deleted") and target.raw is not None:
                            raw_state = _dispose(
                                state,
                                operation_id,
                                target.raw,
                                permanent=permanent,
                                recycle_adapter=recycle_adapter,
                                failure_details=failure_details,
                            )
                            if raw_state in ("recycled", "deleted"):
                                raw_disposed.append(target.identity)
                        if video_state == "review" or raw_state == "review":
                            ineligible += 1
                            if len(failure_details) < 100:
                                failure_details.append((target.identity, "ledger_requires_review"))
                        elif video_state in ("failed", "uncertain"):
                            (failed if video_state == "failed" else uncertain).append(target.identity)
                        elif raw_state in ("failed", "uncertain"):
                            (failed if raw_state == "failed" else uncertain).append(target.identity)
                        else:
                            state_value = _dispose(
                                state,
                                operation_id,
                                target,
                                permanent=permanent,
                                recycle_adapter=recycle_adapter,
                                failure_details=failure_details,
                            )
                            if state_value == "review":
                                ineligible += 1
                                if len(failure_details) < 100:
                                    failure_details.append((target.identity, "ledger_requires_review"))
                            elif state_value == "recycled":
                                recycled.append(target.identity)
                            elif state_value == "deleted":
                                deleted.append(target.identity)
                            elif state_value == "uncertain":
                                uncertain.append(target.identity)
                            else:
                                failed.append(target.identity)
            processed += 1
            if progress:
                progress(processed, len(targets))
        canceled = stop.is_set()
        ledger.finish_operation(state, operation_id, "canceled" if canceled else "complete")
    # Cached previews of removed (or possibly removed) outputs go with them; best effort.
    forgotten = thumbnail_cache.forget(state, (*recycled, *deleted, *uncertain, *absent))
    diagnostic_event(
        log,
        "images.recycle.thumbnail_entries_removed",
        level=logging.DEBUG,
        data={
            "removed": forgotten,
        },
    )
    # One line per operation in normal mode; per-item detail stays in warnings and -x.
    diagnostic_event(
        log,
        "images.recycle.finished",
        level=logging.INFO,
        data={
            "operation": operation_id,
            "outcome": "canceled" if canceled else "complete",
            "removed": len(recycled) + len(deleted),
            "raw": len(raw_disposed),
            "video": len(video_disposed),
            "absent": len(absent),
            "failed": len(failed),
            "uncertain": len(uncertain),
            "missing": missing,
            "ineligible": ineligible,
            "unprocessed": len(targets) - processed,
            "elapsed_ms": (time.monotonic() - started) * 1000,
        },
    )
    return RecycleResult(
        tuple(recycled),
        tuple(failed),
        tuple(uncertain),
        missing,
        ineligible,
        len(targets) - processed,
        canceled,
        tuple(failure_details),
        tuple(deleted),
        tuple(absent),
        tuple(raw_disposed),
        tuple(video_disposed),
    )
