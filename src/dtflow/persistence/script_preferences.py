"""Bounded source-version authoring preferences; never queues or execution authority."""

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from dtflow.core.models import ValidationError

MAX_BYTES = 256 * 1024


def preferences(state: Path, source_sha256: str, *, value=None, write=False):
    if not isinstance(source_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None:
        raise ValidationError("Recorded source identity is required for script preferences.")
    path = Path(state) / "script_preferences.sqlite3"
    text = None
    if write:
        if not isinstance(value, dict):
            raise ValidationError("Script preferences must be a JSON object.")
        text = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        if len(text) > MAX_BYTES:
            raise ValidationError("Script preferences exceed 256 KiB.")
    if path.is_symlink() or (path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
        raise ValidationError("Script preferences must not be redirected.")
    if not path.exists() and not write:
        return None
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
    uri = path.resolve().as_uri() + ("?mode=rwc" if write else "?mode=ro")
    with closing(sqlite3.connect(uri, uri=True, timeout=2)) as connection:
        connection.execute("PRAGMA busy_timeout=2000")
        if write:
            connection.execute("BEGIN IMMEDIATE")
        else:
            connection.execute("PRAGMA query_only=ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1) or (version == 0 and not write):
            raise ValidationError("Unsupported script preference schema; left unchanged.")
        if write:
            if version == 0:
                existing = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if existing:
                    raise ValidationError("Unversioned script preferences are not overwritten.")
                connection.execute("CREATE TABLE preferences (source_sha256 TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
                connection.execute("PRAGMA user_version=1")
            count = connection.execute("SELECT COUNT(*) FROM preferences").fetchone()[0]
            present = connection.execute("SELECT 1 FROM preferences WHERE source_sha256=?", (source_sha256,)).fetchone()
            if count >= 128 and present is None:
                raise ValidationError("Script preferences already contain 128 versions; no existing settings were removed.")
            connection.execute(
                "INSERT INTO preferences VALUES (?,?) ON CONFLICT(source_sha256)"
                " DO UPDATE SET value_json=excluded.value_json",
                (source_sha256, text),
            )
            connection.commit()
            return True
        row = connection.execute("SELECT value_json FROM preferences WHERE source_sha256=?", (source_sha256,)).fetchone()
        if row is None:
            return None
        if len(row[0]) > MAX_BYTES:
            raise ValidationError("Stored script preferences exceed their size bound.")
        result = json.loads(row[0])
        if not isinstance(result, dict):
            raise ValidationError("Stored script preferences must be an object.")
        if len(json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(",", ":"))) > MAX_BYTES:
            raise ValidationError("Stored script preferences exceed their JSON reply bound.")
        return result
