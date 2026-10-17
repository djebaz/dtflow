"""Environment-specific removal mode; never store consent or selected identities."""

import json
from pathlib import Path

from dtflow.persistence.runtime_environment import environment_key
from dtflow.persistence.store import PersistenceError
from dtflow.services.artifacts import atomic_write


def preference_path(state: Path) -> Path:
    return Path(state) / f"image_removal.{environment_key()}.json"


def load_mode(state: Path) -> str:
    path = preference_path(state)
    if not path.exists():
        return "trash"
    try:
        if path.stat().st_size > 4096:
            raise ValueError
        document = json.loads(path.read_text(encoding="utf-8"))
        if (
            document.get("schema") != "dtflow.image-removal"
            or type(document.get("version")) is not int
            or document["version"] != 1
            or document.get("mode") not in ("trash", "permanent")
        ):
            raise ValueError
        return document["mode"]
    except (OSError, ValueError, AttributeError) as exc:
        raise PersistenceError("Unsupported or unreadable removal preferences were preserved.") from exc


def save_mode(state: Path, mode: str) -> None:
    if mode not in ("trash", "permanent"):
        raise ValueError("Unknown removal mode")
    load_mode(state)  # Do not overwrite unreadable/newer state.
    document = dict(schema="dtflow.image-removal", version=1, mode=mode)
    path = preference_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, (json.dumps(document) + "\n").encode("utf-8"))
