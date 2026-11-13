"""Managed durable copies of converted custom LoRA files (``<state>/lora_files/``).

A retained copy is a durable asset, not disposable cache: it lets a LoRA that vanished from DT+ be restored even after the
user deleted the original file. Copies adopt their canonical managed filename when available; identity remains the measured
SHA-256 plus size in ``index.json`` and is re-verified whenever bytes are about to be used.
Nothing here talks to the network, and removal only touches files this store created.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dtflow.core.models import ValidationError
from dtflow.diagnostics import event as diagnostic_event
from dtflow.persistence.assets import _lock_file
from dtflow.services.lora_naming import display_label

log = logging.getLogger(__name__)
STORE_DIR = "lora_files"
INDEX_FILE = "index.json"
INDEX_VERSION = 1
CHUNK = 1024 * 1024
EDGE = 64 * 1024
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class RetainedFile:
    name: str
    sha256: str
    size: int
    path: Path


def _safe_name(name: str) -> str:
    cleaned = _UNSAFE.sub("_", Path(name).name).strip(" .")
    return cleaned or "lora.ckpt"


def _hash(path: Path) -> tuple[str, int]:
    digest, total = hashlib.sha256(), 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


class LoraStore:
    def __init__(self, state: Path):
        self.directory = Path(state) / STORE_DIR
        self._unreadable: set[str] = set()

    # -- index ----------------------------------------------------------------------------------------------------

    @property
    def _index_path(self) -> Path:
        return self.directory / INDEX_FILE

    def _read(self) -> list[dict]:
        try:
            document = json.loads(self._index_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            raise ValidationError("The retained LoRA index could not be read; nothing was changed.") from exc
        if not isinstance(document, dict) or document.get("version") != INDEX_VERSION:
            raise ValidationError("Unsupported retained LoRA index version; nothing was changed or reset.")
        files = document.get("files")
        if not isinstance(files, list):
            raise ValidationError("The retained LoRA index is malformed; nothing was changed.")
        return [item for item in files if isinstance(item, dict)]

    def _read_recovering(self) -> list[dict]:
        """Read the index under the store lock, first restoring a file a killed case-only rename left half-moved.

        ``.<id>.rename`` is the temporary name of an in-place rename; when the index still names a file that is
        missing, the temporary one is that file, so it goes back under its indexed name (nothing is lost).
        """
        files = self._read()
        try:
            leftovers = [p for p in self.directory.glob(".*.rename") if p.is_file()]
        except OSError:
            return files
        for temporary in leftovers:
            try:
                size = temporary.stat().st_size
                owner = next(
                    (
                        item
                        for item in files
                        if item.get("size") == size and not self._path(item).exists() and item.get("name")
                    ),
                    None,
                )
                if owner is not None:
                    os.replace(temporary, self._path(owner))
                    diagnostic_event(log, "assets.retained_rename_recovered", level=logging.WARNING, data={})
            except (OSError, ValidationError):
                continue
        return files

    def _write(self, files: list[dict]) -> None:
        temporary = self.directory / ".index.tmp"
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump({"version": INDEX_VERSION, "files": files}, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self._index_path)

    def _locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        return _lock_file(self.directory / ".store.lock")

    # -- operations (blocking; callers run them in a worker thread) -------------------------------------------------

    def _path(self, item: dict) -> Path:
        """Never use an indexed name to escape or redirect outside the managed store."""
        name = item.get("name")
        if not isinstance(name, str) or not name or _safe_name(name) != name:
            raise ValidationError("A retained LoRA index entry has an unsafe filename; nothing was changed.")
        path = self.directory / name
        if path.is_symlink():
            raise ValidationError("A retained LoRA points to a link; nothing was changed.")
        return path

    def find(self, sha256: str, size: int) -> RetainedFile | None:
        """Find a copy by recorded content identity; callers verify its bytes before uploading."""
        for item in self._read():
            if item.get("sha256") == sha256 and item.get("size") == size:
                path = self._path(item)
                if path.is_file():
                    return RetainedFile(path.name, sha256, size, path)
        return None

    def verify(self, retained: RetainedFile) -> None:
        """Re-measure before use; a missing, resized or altered file is never trusted."""
        try:
            digest, total = _hash(retained.path)
        except OSError as exc:
            raise ValidationError(f"Retained LoRA {display_label(retained.name)} could not be read.") from exc
        if (digest, total) != (retained.sha256, retained.size):
            raise ValidationError(
                f"Retained LoRA {display_label(retained.name)} no longer matches its recorded checksum."
            )

    def retain(
        self,
        source: Path,
        sha256: str,
        size: int,
        *,
        managed_name: str | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> RetainedFile:
        """Copy new content directly to its managed name, keeping one indexed copy per hash.

        ``on_progress(copied, size)`` is optional, called per chunk from the calling thread; it must not raise."""
        source = Path(source)
        if managed_name is not None and (
            _safe_name(managed_name) != managed_name or not managed_name.endswith(".ckpt")
        ):
            raise ValidationError("The managed LoRA filename is not portable.")
        handle = self._locked()
        try:
            files = self._read_recovering()
            matching = next(
                (item for item in files if item.get("sha256") == sha256 and item.get("size") == size),
                None,
            )
            if matching is not None:
                target = self._path(matching)
                name = target.name
                if target.is_file():
                    retained = RetainedFile(name, sha256, size, target)
                    try:
                        self.verify(retained)
                    except ValidationError:
                        # Source is rehashed below before atomically repairing this recorded copy.
                        pass
                    else:
                        return retained
                elif target.exists():
                    raise ValidationError("The retained LoRA destination is not a regular file.")
            else:
                original = _safe_name(source.name)
                stem, suffix = os.path.splitext(original)
                taken = {str(item.get("name", "")).casefold() for item in files}
                name = managed_name or original
                if managed_name is not None:
                    # The canonical allocator decides managed-name collisions.
                    # Never silently append a hash or overwrite another copy.
                    if name.casefold() in taken or os.path.lexists(self.directory / name):
                        raise ValidationError("The managed LoRA filename already exists; no copy was overwritten.")
                else:
                    counter = 0
                    while name.casefold() in taken or os.path.lexists(self.directory / name):
                        counter += 1
                        end = f"-{sha256[:8]}" + (f"-{counter}" if counter > 1 else "")
                        name = f"{stem}{end}{suffix}"
                target = self.directory / name

            temporary = None
            digest, total = hashlib.sha256(), 0
            try:
                with (
                    source.open("rb") as reader,
                    tempfile.NamedTemporaryFile(
                        mode="wb", dir=self.directory, prefix=".lora-", suffix=".part", delete=False
                    ) as writer,
                ):
                    temporary = Path(writer.name)
                    while chunk := reader.read(CHUNK):
                        digest.update(chunk)
                        total += len(chunk)
                        writer.write(chunk)
                        if on_progress is not None:
                            on_progress(total, size)
                    writer.flush()
                    os.fsync(writer.fileno())
                if (digest.hexdigest(), total) != (sha256, size):
                    raise ValidationError("The LoRA file changed while it was copied; no copy was kept.")
                if matching is None and os.path.lexists(target):
                    raise ValidationError(
                        "The retained LoRA destination appeared during copying; nothing was overwritten."
                    )
                os.replace(temporary, target)
            except OSError as exc:
                raise ValidationError("A copy of the LoRA file could not be kept in application data.") from exc
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            if matching is None:
                files.append(
                    {"name": name, "sha256": sha256, "size": size, "retained_at_utc": datetime.now(UTC).isoformat()}
                )
                try:
                    self._write(files)
                except OSError as exc:
                    # The source is still intact; don't leave a newly copied
                    # file outside the retained index if its write failed.
                    try:
                        target.unlink(missing_ok=True)
                    except OSError:
                        diagnostic_event(log, "assets.retained_unindexed_copy", level=logging.WARNING)
                    raise ValidationError("The retained LoRA index could not be saved.") from exc
            return RetainedFile(name, sha256, size, target)
        finally:
            handle.close()

    def find_converted(
        self, source_sha256: str, family: str, scale: float, converter_version: str
    ) -> RetainedFile | None:
        """Find verified converted bytes by source identity and converter settings, without contacting DT+."""
        handle = self._locked()
        try:
            for item in self._read_recovering():
                records = item.get("conversions")
                if not isinstance(records, list):
                    records = [item.get("conversion")]
                if not any(
                    isinstance(record, dict)
                    and isinstance(record.get("source"), dict)
                    and record["source"].get("sha256") == source_sha256
                    and record.get("family") == family
                    and record.get("scale") == scale
                    and record.get("converter_version") == converter_version
                    for record in records
                    if isinstance(record, dict)
                ):
                    continue
                # An unsafe name or a link is tampering, not damage: it stays an error here (before any reconversion).
                target = self._path(item)
                try:
                    retained = RetainedFile(target.name, item["sha256"], item["size"], target)
                    self.verify(retained)
                except (ValidationError, KeyError, TypeError):
                    # A missing or altered earlier output is not reusable; the caller converts again and
                    # retain_converted() repairs the entry from the fresh bytes. An unreadable or malformed index
                    # is still an error (_read).
                    diagnostic_event(log, "lora_store.converted_entry_skipped", level=logging.WARNING, data={})
                    continue
                return retained
        finally:
            handle.close()
        return None

    def _repair_damaged(self, target: Path, staged: Path, sha256: str, size: int) -> None:
        """Put freshly converted, hash-checked bytes where a missing or altered indexed copy was.

        An altered copy is moved aside to a hidden ``.<id>.damaged`` file in the store, never deleted; a link or
        another non-file is not touched. The staged file is consumed only after the target is free.
        """
        if _hash(staged) != (sha256, size):
            raise ValidationError("The converted LoRA changed before retention; nothing was kept.")
        aside: Path | None = None
        if os.path.lexists(target):
            if target.is_symlink() or not target.is_file():
                raise ValidationError("A retained LoRA is not a regular file; nothing was changed.")
            aside = self.directory / f".{uuid.uuid4().hex}.damaged"
            try:
                os.replace(target, aside)
            except OSError as exc:
                raise ValidationError("The damaged retained LoRA could not be set aside; nothing was changed.") from exc
        try:
            os.replace(staged, target)
        except OSError as exc:
            if aside is not None:
                try:
                    os.replace(aside, target)  # put the damaged copy back; nothing is lost
                except OSError:
                    pass
            raise ValidationError("The damaged retained LoRA could not be repaired; nothing was changed.") from exc
        diagnostic_event(log, "lora_store.converted_entry_repaired", level=logging.WARNING, data={})

    def retain_converted(
        self,
        staged: Path,
        sha256: str,
        size: int,
        *,
        managed_name: str,
        conversion: dict,
        source_metadata: dict | None = None,
    ) -> RetainedFile:
        """Adopt a completed conversion from the store volume without overwriting an existing retained file.

        A matching payload shares its existing managed copy; conversion provenance is additive
        and preserved when the store index is rewritten by existing Prepare/rename operations.
        """
        staged = Path(staged)
        if _safe_name(managed_name) != managed_name or not managed_name.lower().endswith(".ckpt"):
            raise ValidationError("The converted LoRA filename is not portable.")
        if staged.is_symlink() or not staged.is_file() or not staged.resolve().is_relative_to(self.directory.resolve()):
            raise ValidationError("The converted LoRA staging file is not owned by the retained store.")
        handle = self._locked()
        try:
            files = self._read_recovering()
            matching = next(
                (item for item in files if item.get("sha256") == sha256 and item.get("size") == size),
                None,
            )
            created = matching is None
            if matching is not None:
                target = self._path(matching)
                retained = RetainedFile(target.name, sha256, size, target)
                try:
                    self.verify(retained)
                except ValidationError:
                    # The same bytes were just converted again, so a missing or altered earlier copy can be repaired.
                    self._repair_damaged(target, staged, sha256, size)
            else:
                if any(str(item.get("name", "")).casefold() == managed_name.casefold() for item in files):
                    raise ValidationError("A different retained LoRA already uses the selected name.")
                target = self.directory / managed_name
                if os.path.lexists(target):
                    raise ValidationError("The converted LoRA filename already exists; no file was overwritten.")
                if _hash(staged) != (sha256, size):
                    raise ValidationError("The converted LoRA changed before retention; nothing was kept.")
                try:
                    os.replace(staged, target)
                except OSError as exc:
                    raise ValidationError("The converted LoRA could not be retained.") from exc
                matching = {
                    "name": managed_name,
                    "sha256": sha256,
                    "size": size,
                    "retained_at_utc": datetime.now(UTC).isoformat(),
                }
                files.append(matching)
                retained = RetainedFile(managed_name, sha256, size, target)
            records = matching.get("conversions")
            if not isinstance(records, list):
                previous = matching.get("conversion")
                records = [previous] if isinstance(previous, dict) else []
            if conversion not in records:
                records.append(conversion)
            matching["conversions"] = records
            matching["conversion"] = conversion
            if source_metadata is not None:
                matching["source_metadata"] = source_metadata  # one bounded copy per file, newest wins
            try:
                self._write(files)
            except OSError as exc:
                if created:
                    # Only a newly created copy is removed. Existing retained bytes must never be deleted.
                    target.unlink(missing_ok=True)
                raise ValidationError("The converted LoRA index could not be saved.") from exc
            return retained
        finally:
            handle.close()

    def rename_accepted(self, sha256: str, size: int, accepted_name: str) -> RetainedFile | None:
        """Give existing verified retained bytes their accepted canonical filename.

        Link the verified content first, commit the index atomically, and only
        then unlink the old name. An interruption cannot lose the indexed copy.
        No remote receipt, source file or origin stamp is renamed.
        """
        if _safe_name(accepted_name) != accepted_name or not accepted_name.endswith(".ckpt"):
            raise ValidationError("The accepted LoRA filename is not portable.")
        handle = self._locked()
        try:
            files = self._read_recovering()
            item = next((row for row in files if (row.get("sha256"), row.get("size")) == (sha256, size)), None)
            if item is None:
                return None
            original = self._path(item)
            if original.name == accepted_name:
                self.verify(RetainedFile(original.name, sha256, size, original))
                return RetainedFile(original.name, sha256, size, original)
            self.verify(RetainedFile(original.name, sha256, size, original))
            target = self.directory / accepted_name
            if original.name.casefold() == accepted_name.casefold() and os.path.lexists(target):
                # Case-insensitive filesystem: both spellings are one file, so linking and unlinking would delete
                # the only copy. Rename in place through a temporary name instead, then commit the index.
                if target.is_symlink() or not os.path.samefile(target, original):
                    raise ValidationError("The accepted LoRA filename is already occupied.")
                temporary = self.directory / f".{uuid.uuid4().hex}.rename"
                os.replace(original, temporary)
                try:
                    os.replace(temporary, target)
                except OSError as exc:
                    os.replace(temporary, original)  # put the original spelling back; nothing is lost
                    raise ValidationError("Could not rename the retained LoRA; its original copy is intact.") from exc
                item["name"] = accepted_name
                self._write(files)
                return RetainedFile(accepted_name, sha256, size, target)
            for other in files:
                if other is not item and str(other.get("name", "")).casefold() == accepted_name.casefold():
                    raise ValidationError("Another retained LoRA already uses the accepted filename.")
            if os.path.lexists(target):
                # A previous interruption may have created the new hard link
                # before the index commit. Adopt it only when its bytes match.
                if target == original or target.is_symlink() or not target.is_file():
                    raise ValidationError("The accepted LoRA filename is already occupied.")
                self.verify(RetainedFile(accepted_name, sha256, size, target))
            else:
                try:
                    os.link(original, target)  # Same filesystem; atomic no-overwrite name creation.
                except OSError as exc:
                    raise ValidationError("Could not name the retained LoRA; its original copy is intact.") from exc
            item["name"] = accepted_name
            self._write(files)
            try:
                original.unlink()
            except OSError as exc:
                # An extra unindexed hard link is safer than losing the canonical one.
                diagnostic_event(
                    log,
                    "assets.retained_old_name_left",
                    level=logging.WARNING,
                    data={"sha": sha256[:8], "reason": type(exc).__name__},
                )
            return RetainedFile(accepted_name, sha256, size, target)
        finally:
            handle.close()

    @staticmethod
    def _edge(path: Path, size: int) -> str:
        """Digest of the first and last 64 KB: catches a same-size replacement whose modification time was kept."""
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            digest.update(handle.read(EDGE))
            if size > EDGE:
                handle.seek(max(size - EDGE, EDGE))
                digest.update(handle.read(EDGE))
        return digest.hexdigest()[:16]

    def record_origin(
        self,
        source: Path,
        sha256: str,
        size: int,
        name: str,
        stat: os.stat_result | None = None,
        *,
        mark: bool = True,
    ) -> None:
        """Remember which file on disk this LoRA name was prepared from, so a later change under it is seen.

        Kept per logical name: identical bytes prepared under two names have two sources, and a change to one must
        never refresh the other. ``stat`` is the measurement taken with the hash, never a fresh one: a file replaced
        after it was hashed must still read as changed. ``mark`` records an explicit Prepare (or a refresh), which makes
        these bytes the name's current version even when older bytes were seen first."""
        try:
            stat = stat or Path(source).stat()
            edge = self._edge(Path(source), stat.st_size)
        except OSError:
            return
        handle = self._locked()
        try:
            files = self._read()
            for item in files:
                if item.get("sha256") == sha256 and item.get("size") == size:
                    origins = item.get("origins")
                    if not isinstance(origins, dict):
                        origins = item["origins"] = {}
                    previous = origins.get(name) if isinstance(origins.get(name), dict) else {}
                    stamp = {
                        "path": str(Path(source).absolute()),
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "edge": edge,
                        "prepared_at": datetime.now(UTC).isoformat() if mark else previous.get("prepared_at", ""),
                    }
                    if origins.get(name) != stamp:
                        origins[name] = stamp
                        self._write(files)
                    return
        finally:
            handle.close()

    def changed_origin(self, sha256: str, size: int, name: str) -> Path | None:
        """The file this LoRA name was prepared from when its size, modification time or edges changed, else None.

        Cheap: no full hash. The caller measures the file once and compares the digest."""
        for item in self._read():
            if item.get("sha256") != sha256 or item.get("size") != size:
                continue
            origins = item.get("origins")
            stamp = origins.get(name) if isinstance(origins, dict) else None
            if not isinstance(stamp, dict) or not isinstance(stamp.get("path"), str):
                return None
            path = Path(stamp["path"])
            try:
                stat = path.stat()
                if not path.is_file():
                    return None
                if (stat.st_size, stat.st_mtime_ns) == (stamp.get("size"), stamp.get("mtime_ns")) and (
                    "edge" not in stamp or self._edge(path, stat.st_size) == stamp["edge"]
                ):
                    return None
            except OSError as exc:
                # A locked or unreadable source keeps the old receipt, but never silently.
                if name not in self._unreadable:
                    self._unreadable.add(name)
                    diagnostic_event(
                        log, "assets.source_unreadable", level=logging.WARNING, data={"reason": type(exc).__name__}
                    )
                return None
            self._unreadable.discard(name)
            return path
        return None

    def origin_names(self) -> dict[tuple[str, int], tuple[str, ...]]:
        """Per retained content, the file names it was prepared from (folder stripped), for matching imported names."""
        try:
            files = self._read()
        except ValidationError:
            return {}
        result = {}
        for item in files:
            origins = item.get("origins")
            if not isinstance(origins, dict) or not isinstance(item.get("sha256"), str):
                continue
            names = {Path(name).name for name in origins} | {
                Path(stamp["path"]).name
                for stamp in origins.values()
                if isinstance(stamp, dict) and isinstance(stamp.get("path"), str)
            }
            result[(item["sha256"], item.get("size"))] = tuple(sorted(names))
        return result

    def current_by_name(self) -> dict[str, tuple[str, int]]:
        """Per LoRA name, the bytes most recently prepared on purpose (explicit Prepare or a refresh)."""
        latest: dict[str, tuple[str, str, int]] = {}
        try:
            files = self._read()
        except ValidationError:
            return {}
        for item in files:
            origins = item.get("origins")
            if not isinstance(origins, dict):
                continue
            for name, stamp in origins.items():
                moment = stamp.get("prepared_at") if isinstance(stamp, dict) else ""
                if moment and (name not in latest or moment > latest[name][0]):
                    latest[name] = (moment, item.get("sha256"), item.get("size"))
        return {name: (sha, size) for name, (_, sha, size) in latest.items()}

    def usage(self) -> tuple[int, int]:
        """(file count, total bytes) of indexed files that still exist."""
        count = total = 0
        for item in self._read():
            path = self._path(item)
            if path.is_file():
                count += 1
                total += path.stat().st_size
        return count, total

    def entries(self) -> list[dict]:
        return self._read()

    def remove(self, sha256: str, size: int) -> bool:
        """Delete only the managed copy; refuse removal while an asset writer may be uploading it."""
        self.directory.parent.mkdir(parents=True, exist_ok=True)
        # An active restoration holds this existing registry lock through upload.
        # Taking it before the store lock prevents deleting bytes being uploaded, across processes.
        with _lock_file(self.directory.parent / "assets.lock"):
            handle = self._locked()
            try:
                files = self._read()
                kept = [item for item in files if (item.get("sha256"), item.get("size")) != (sha256, size)]
                if len(kept) == len(files):
                    return False
                for item in files:
                    if (item.get("sha256"), item.get("size")) == (sha256, size):
                        self._path(item).unlink(missing_ok=True)
                self._write(kept)
                return True
            finally:
                handle.close()

    def clear(self) -> int:
        """Remove every retained copy; returns how many entries were removed."""
        handle = self._locked()
        try:
            files = self._read()
            for item in files:
                self._path(item).unlink(missing_ok=True)
            self._write([])
            return len(files)
        finally:
            handle.close()
