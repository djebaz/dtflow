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

    def find(self, sha256: str, size: int) -> RetainedFile | None:
        """The retained copy of exactly these bytes, if the index lists it and the file is still there."""
        for item in self._read():
            if item.get("sha256") == sha256 and item.get("size") == size:
                path = self.directory / str(item.get("name", ""))
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
        """Keep a verified copy of ``source`` (already measured as ``sha256``/``size``); identical bytes are not duplicated."""
        source = Path(source)
        handle = self._locked()
        try:
            files = self._read()
            existing = self.find(sha256, size)
            if existing is not None:
                return existing
            name = _safe_name(source.name)
            taken = {item.get("name") for item in files}
            if name in taken or (self.directory / name).exists():
                stem, dot, extension = name.rpartition(".")
                name = f"{stem}-{sha256[:8]}.{extension}" if dot else f"{name}-{sha256[:8]}"
            target = self.directory / name
            part = self.directory / f".{name}.part"
            digest, total = hashlib.sha256(), 0
            try:
                with source.open("rb") as reader, part.open("wb") as writer:
                    while chunk := reader.read(CHUNK):
                        digest.update(chunk)
                        total += len(chunk)
                        writer.write(chunk)
                    writer.flush()
                    os.fsync(writer.fileno())
                if (digest.hexdigest(), total) != (sha256, size):
                    raise ValidationError("The LoRA file changed while it was copied; no copy was kept.")
                os.replace(part, target)
            except OSError as exc:
                raise ValidationError("A copy of the LoRA file could not be kept in application data.") from exc
            finally:
                part.unlink(missing_ok=True)
            files.append(
                {"name": name, "sha256": sha256, "size": size, "retained_at_utc": datetime.now(UTC).isoformat()}
            )
            self._write(files)
            return RetainedFile(name, sha256, size, target)
        finally:
            handle.close()

    def usage(self) -> tuple[int, int]:
        """(file count, total bytes) of indexed files that still exist."""
        count = total = 0
        for item in self._read():
            path = self.directory / str(item.get("name", ""))
            if path.is_file():
                count += 1
                total += path.stat().st_size
        return count, total

    def entries(self) -> list[dict]:
        return self._read()

    def remove(self, sha256: str, size: int) -> bool:
        """Delete one retained copy and its index entry; unrelated state and outputs are never touched."""
        handle = self._locked()
        try:
            files = self._read()
            kept = [item for item in files if (item.get("sha256"), item.get("size")) != (sha256, size)]
            if len(kept) == len(files):
                return False
            for item in files:
                if (item.get("sha256"), item.get("size")) == (sha256, size):
                    (self.directory / _safe_name(str(item.get("name", "")))).unlink(missing_ok=True)
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
                (self.directory / _safe_name(str(item.get("name", "")))).unlink(missing_ok=True)
            self._write([])
            return len(files)
        finally:
            handle.close()
