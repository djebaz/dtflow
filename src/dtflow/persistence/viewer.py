"""Versioned durable viewer preferences, independent of disposable gallery derivatives."""

import json
from pathlib import Path

from dtflow.core.gallery import GalleryQuery
from dtflow.persistence.store import PersistenceError
from dtflow.services.artifacts import atomic_write

VERSION = 1


def load_viewer(state: Path) -> dict:
    path = Path(state) / "viewer.json"
    try:
        if path.stat().st_size > 65536:
            return {}
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("schema") != "dtflow.viewer" or type(doc.get("version")) is not int or doc["version"] != VERSION:
            return {}
        GalleryQuery(**doc.get("query", {}))
        return doc
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def save_viewer(state: Path, document: dict) -> None:
    """Atomically persist known preferences; never replace a newer or unsupported schema."""
    state = Path(state)
    path = state / "viewer.json"
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PersistenceError("Unreadable viewer preferences were preserved.") from exc
        if not isinstance(old, dict) or old.get("schema") != "dtflow.viewer" or old.get("version") != VERSION:
            raise PersistenceError("Unsupported viewer preferences were preserved.")
    GalleryQuery(**document.get("query", {}))
    value = {**document, "schema": "dtflow.viewer", "version": VERSION}
    encoded = (json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if len(encoded) > 65536:
        raise PersistenceError("Viewer preferences exceed their size limit.")
    state.mkdir(parents=True, exist_ok=True)
    atomic_write(path, encoded)
