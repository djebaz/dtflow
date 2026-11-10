"""Managed durable copies of converted custom LoRA files (``<state>/lora_files/``).

A retained copy is a durable asset, not disposable cache: it lets a LoRA that vanished from DT+ be restored even after the
user deleted the original file. Files keep their original name for readability; identity is the measured SHA-256 plus
size, recorded in ``index.json`` and re-verified every time the bytes are about to be used. Nothing here talks to the
network, and removal only ever touches files this store created.
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
from dtflow.persistence.assets import _lock_file

log = logging.getLogger(__name__)
STORE_DIR = "lora_files"
INDEX_FILE = "index.json"
INDEX_VERSION = 1
CHUNK = 1024 * 1024
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

    def retain(self, source: Path, sha256: str, size: int) -> RetainedFile:
        """Keep verified source bytes, deduplicated by digest/size; repair a damaged managed copy if possible."""
        source = Path(source)
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
                name = original
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
                self._write(files)
            return RetainedFile(name, sha256, size, target)
        finally:
            handle.close()

    def record_origin(self, source: Path, sha256: str, size: int) -> None:
        """Remember which file on disk these bytes were prepared from, so a later change under the same name is seen."""
        try:
            stat = Path(source).stat()
        except OSError:
            return
        handle = self._locked()
        try:
            files = self._read()
            for item in files:
                if item.get("sha256") == sha256 and item.get("size") == size:
                    fields = {
                        "origin": str(Path(source).absolute()),
                        "origin_size": stat.st_size,
                        "origin_mtime_ns": stat.st_mtime_ns,
                    }
                    if any(item.get(key) != value for key, value in fields.items()):
                        item.update(fields)
                        self._write(files)
                    return
        finally:
            handle.close()

    def changed_origin(self, sha256: str, size: int) -> Path | None:
        """The file these bytes were prepared from when it now holds different bytes, else None.

        Size and modification time decide first; only a changed stamp costs a re-hash, and a touched but identical
        file is stamped again so it is not hashed twice."""
        for item in self._read():
            if item.get("sha256") != sha256 or item.get("size") != size:
                continue
            origin = item.get("origin")
            if not isinstance(origin, str):
                return None
            path = Path(origin)
            try:
                stat = path.stat()
                if not path.is_file() or (stat.st_size, stat.st_mtime_ns) == (
                    item.get("origin_size"),
                    item.get("origin_mtime_ns"),
                ):
                    return None
                if stat.st_size == size and _hash(path)[0] == sha256:
                    self.record_origin(path, sha256, size)
                    return None
            except OSError:
                return None
            return path
        return None

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
