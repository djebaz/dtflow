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
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dtflow.core.models import ValidationError
from dtflow.diagnostics import event as diagnostic_event
from dtflow.persistence.assets import _lock_file

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
            raise ValidationError(f"Retained LoRA {retained.name} could not be read.") from exc
        if (digest, total) != (retained.sha256, retained.size):
            raise ValidationError(f"Retained LoRA {retained.name} no longer matches its recorded checksum.")

    def retain(self, source: Path, sha256: str, size: int, *, managed_name: str | None = None) -> RetainedFile:
        """Copy new content directly to its managed name, keeping one indexed copy per hash."""
        source = Path(source)
        if managed_name is not None and (
            _safe_name(managed_name) != managed_name or not managed_name.endswith(".ckpt")
        ):
            raise ValidationError("The managed LoRA filename is not portable.")
        handle = self._locked()
        try:
            files = self._read()
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
            files = self._read()
            item = next((row for row in files if (row.get("sha256"), row.get("size")) == (sha256, size)), None)
            if item is None:
                return None
            original = self._path(item)
            if original.name == accepted_name:
                self.verify(RetainedFile(original.name, sha256, size, original))
                return RetainedFile(original.name, sha256, size, original)
            self.verify(RetainedFile(original.name, sha256, size, original))
            if original.name.casefold() == accepted_name.casefold():
                # On case-insensitive filesystems both names refer to one file;
                # unlinking the old spelling would delete the accepted copy.
                raise ValidationError("A case-only retained LoRA rename needs explicit handling.")
            for other in files:
                if other is not item and str(other.get("name", "")).casefold() == accepted_name.casefold():
                    raise ValidationError("Another retained LoRA already uses the accepted filename.")
            target = self.directory / accepted_name
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
