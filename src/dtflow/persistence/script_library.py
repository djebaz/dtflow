"""Managed script library: immutable, content-addressed script versions under ``<state>/scripts/``.

Every version is a UTF-8 ``.js`` file named by the SHA-256 of its bytes (``versions/<sha256>.js``) plus an index row in
``library.sqlite3`` (``PRAGMA user_version`` schema, a newer schema is rejected, never rewritten). A version is never
modified by this module: editing a script creates a new version. Removal drops library membership only, retaining source
bytes for queued executions and history. The file bytes are re-hashed on every read, so a
file changed or deleted on disk behind the library is reported instead of being run or shown as the recorded version.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dtflow.scripting.engine import MAX_SOURCE_BYTES

SCHEMA_VERSION = 1
MAX_VERSIONS = 50


class ScriptLibraryError(RuntimeError):
    """A library problem that is safe to show to the user as written."""


@dataclass(frozen=True, slots=True)
class ScriptVersion:
    sha256: str
    name: str
    size: int
    created_at: str
    based_on: str  # SHA-256 of the version this one was edited from, or ""

    @property
    def short(self) -> str:
        return self.sha256[:7]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sanitize_name(name: str) -> str:
    """Display name only (never a path): last path component, control characters dropped, bounded length."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(ch for ch in base if ch.isprintable()).strip()
    return (base or "Untitled.js")[:120]


class ScriptLibrary:
    def __init__(self, state: Path) -> None:
        self.root = Path(state) / "scripts"
        self.versions_dir = self.root / "versions"
        self.path = self.root / "library.sqlite3"
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ScriptLibraryError("Unsupported script-library schema; no reset or migration was performed.")
            if version == 0:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS versions (
                        sha256 TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        size INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        based_on TEXT NOT NULL DEFAULT ''
                    );
                    """
                )
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=2.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _source_path(self, sha256: str) -> Path:
        if len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256):
            raise ScriptLibraryError("Invalid script version identifier.")
        return self.versions_dir / f"{sha256}.js"

    def list_versions(self) -> list[ScriptVersion]:
        """Oldest first (import order)."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT sha256, name, size, created_at, based_on FROM versions ORDER BY created_at, rowid"
            ).fetchall()
        return [ScriptVersion(*row) for row in rows]

    def get(self, sha256: str) -> ScriptVersion | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT sha256, name, size, created_at, based_on FROM versions WHERE sha256=?", (sha256,)
            ).fetchone()
        return ScriptVersion(*row) if row else None

    def remove_version(self, sha256: str) -> bool:
        """Remove index membership, retaining source bytes for queues and history."""
        self._source_path(sha256)  # validate identity; never accept an arbitrary path
        try:
            with closing(self._connect()) as connection, connection:
                cursor = connection.execute("DELETE FROM versions WHERE sha256=?", (sha256,))
                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise ScriptLibraryError(f"Cannot remove the script version: {exc}") from exc

    def read_source(self, sha256: str) -> str:
        """Verified source text. A missing or altered file raises; nothing is repaired or replaced."""
        path = self._source_path(sha256)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ScriptLibraryError(
                f"The stored file for version {sha256[:7]} cannot be read ({exc.strerror})."
            ) from exc
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ScriptLibraryError(f"The stored file for version {sha256[:7]} no longer matches its SHA-256.")
        try:
            return data.decode("utf-8")
        except UnicodeError as exc:
            raise ScriptLibraryError(f"Version {sha256[:7]} is not valid UTF-8.") from exc

    def verify(self, sha256: str) -> str | None:
        """``None`` when the stored file is present and matches its SHA-256, else the reason (Reload from disk)."""
        try:
            self.read_source(sha256)
        except ScriptLibraryError as exc:
            return str(exc)
        return None

    def import_file(self, path: Path) -> tuple[ScriptVersion, bool]:
        """Copy a local ``.js`` file into the library. Returns ``(version, already_present)``."""
        path = Path(path)
        try:
            with path.open("rb") as handle:
                data = handle.read(MAX_SOURCE_BYTES + 1)
        except OSError as exc:
            raise ScriptLibraryError(f"Cannot read {path.name}: {exc.strerror or type(exc).__name__}.") from exc
        return self._add(sanitize_name(path.name), data, "")

    def add_source(self, name: str, source: str, *, based_on: str = "") -> tuple[ScriptVersion, bool]:
        """Store edited text as a new immutable version."""
        return self._add(sanitize_name(name), source.encode("utf-8"), based_on)

    def _add(self, name: str, data: bytes, based_on: str) -> tuple[ScriptVersion, bool]:
        if not data:
            raise ScriptLibraryError("The script is empty.")
        if len(data) > MAX_SOURCE_BYTES:
            raise ScriptLibraryError("Script source is too large (limit 1 MiB).")
        try:
            data.decode("utf-8")
        except UnicodeError as exc:
            raise ScriptLibraryError("The script is not valid UTF-8 text.") from exc
        sha256 = hashlib.sha256(data).hexdigest()
        existing = self.get(sha256)
        if existing is not None:
            problem = self.verify(sha256)
            if problem is not None:
                raise ScriptLibraryError(problem)
            return existing, True
        if len(self.list_versions()) >= MAX_VERSIONS:
            raise ScriptLibraryError(f"The library holds {MAX_VERSIONS} versions, the limit. Nothing was imported.")
        target = self._source_path(sha256)
        temp = self.versions_dir / f".{sha256}.{uuid.uuid4().hex}.tmp"
        try:
            with open(temp, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)  # same bytes, so a pre-existing orphan file is replaced by identical content
        except OSError as exc:
            temp.unlink(missing_ok=True)
            raise ScriptLibraryError(f"Cannot store the script: {exc.strerror or type(exc).__name__}.") from exc
        version = ScriptVersion(sha256, name, len(data), _now(), based_on)
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    "INSERT OR IGNORE INTO versions (sha256, name, size, created_at, based_on) VALUES (?, ?, ?, ?, ?)",
                    (version.sha256, version.name, version.size, version.created_at, version.based_on),
                )
        except sqlite3.Error as exc:
            raise ScriptLibraryError(f"Cannot record the script version: {exc}") from exc
        return version, False
