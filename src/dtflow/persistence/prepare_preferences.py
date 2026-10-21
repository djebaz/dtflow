"""Runtime-specific Prepare picker folders, durable convenience state rather than cache."""

import json
from pathlib import Path

from dtflow.core.models import ValidationError
from dtflow.persistence.runtime_environment import environment_key
from dtflow.services.artifacts import atomic_write

FILENAME = "prepare_preferences.json"
SCHEMA_VERSION = 1
MAX_BYTES = 65536
MAX_RUNTIMES = 8
MAX_PATH = 4096


def _load(state: Path) -> dict[str, str]:
    try:
        with (Path(state) / FILENAME).open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return {}
    if len(raw) > MAX_BYTES:
        raise ValidationError("Prepare preferences exceed the size limit.")
    value = json.loads(raw)
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int:
        raise ValidationError("Invalid Prepare preferences; file left unchanged.")
    if value["schema_version"] != SCHEMA_VERSION:
        raise ValidationError("Unsupported Prepare preferences; file left unchanged.")
    folders = value.get("folders")
    if not isinstance(folders, dict) or len(folders) > MAX_RUNTIMES:
        raise ValidationError("Invalid Prepare folders; file left unchanged.")
    for runtime, folder in folders.items():
        if (
            not isinstance(runtime, str)
            or not 0 < len(runtime) <= 32
            or not isinstance(folder, str)
            or not 0 < len(folder) <= MAX_PATH
            or "\x00" in folder
        ):
            raise ValidationError("Invalid Prepare folder; file left unchanged.")
    return folders


def load_prepare_folder(state: Path, *, runtime: str | None = None) -> str:
    """Return only this runtime's spelling, without translating or creating directories."""
    return _load(state).get(runtime or environment_key(), "")


def save_prepare_folder(state: Path, folder: str, *, runtime: str | None = None) -> None:
    """Preserve other runtimes and refuse to overwrite unreadable/newer preferences."""
    runtime = runtime or environment_key()
    folders = _load(state)
    if not 0 < len(runtime) <= 32 or not 0 < len(folder) <= MAX_PATH or "\x00" in folder:
        raise ValidationError("Prepare folder cannot be remembered.")
    folders[runtime] = folder
    if len(folders) > MAX_RUNTIMES:
        raise ValidationError("Too many runtimes share Prepare preferences.")
    value = {"schema_version": SCHEMA_VERSION, "folders": folders}
    encoded = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise ValidationError("Prepare preferences exceed the size limit.")
    Path(state).mkdir(parents=True, exist_ok=True)
    atomic_write(Path(state) / FILENAME, encoded)
