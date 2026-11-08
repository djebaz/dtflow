"""Shared application settings (the cloud concurrency maximum and the workflow concurrency defaults) in ``<state>/settings.json``.

The setting is a user-selected client policy, not verified cloud entitlement. The file is credential-free, versioned
independently of the application and of the SQLite store, and written atomically. A newer schema is rejected rather
than rewritten.
"""

import json
import os
import threading
from pathlib import Path
from tempfile import NamedTemporaryFile

from dtflow.core.models import MAX_QUEUE_CONCURRENCY, ValidationError
from dtflow.persistence.runtime_environment import environment_key

SCHEMA_VERSION = 1
DEFAULT_CLOUD_CONCURRENCY = 4
MAX_CLOUD_CONCURRENCY = MAX_QUEUE_CONCURRENCY


class SettingsError(ValidationError):
    pass


def validate_concurrency(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_CLOUD_CONCURRENCY:
        raise SettingsError(f"Cloud concurrency must be an integer in 1..{MAX_CLOUD_CONCURRENCY}.")
    return value


def _read(state: Path) -> dict:
    """The validated settings document ({} when there is no file); a newer or malformed file is refused."""
    path = Path(state) / "settings.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise SettingsError("Settings file could not be read.") from exc
    if not isinstance(document, dict) or type(document.get("schema_version")) is not int:
        raise SettingsError("Settings file is malformed.")
    if document["schema_version"] != SCHEMA_VERSION:
        raise SettingsError("Unsupported settings schema version; the file was left unchanged.")
    return document


def _write(state: Path, document: dict) -> Path:
    target = Path(state) / "settings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=".settings-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump({**document, "schema_version": SCHEMA_VERSION}, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def load_cloud_concurrency(state: Path) -> int:
    return validate_concurrency(_read(state).get("cloud_concurrency", DEFAULT_CLOUD_CONCURRENCY))


def save_cloud_concurrency(state: Path, value: int) -> Path:
    validate_concurrency(value)
    document = _read(state)  # refuse to overwrite an unreadable or newer file
    document["cloud_concurrency"] = value
    return _write(state, document)


# Workflow concurrency defaults (#286), used when a workflow file or node does not set its own.
WORKFLOW_DEFAULT_KEYS = {"cloud": "workflow_cloud", "local": "workflow_local", "segmentation": "workflow_segmentation"}
WORKFLOW_DEFAULTS = {"cloud": 10, "local": 2, "segmentation": 2}


def load_workflow_defaults(state: Path) -> dict[str, int]:
    """The workflow concurrency defaults (DT+, local, local segmentation nodes); a missing value is the built-in one."""
    document = _read(state)
    return {
        name: validate_concurrency(document.get(key, WORKFLOW_DEFAULTS[name]))
        for name, key in WORKFLOW_DEFAULT_KEYS.items()
    }


def save_workflow_defaults(state: Path, values: dict[str, int]) -> Path:
    document = _read(state)
    for name, key in WORKFLOW_DEFAULT_KEYS.items():
        if name in values:
            document[key] = validate_concurrency(values[name])
    return _write(state, document)


# Remembered file and folder dialog locations (#312): ``dialog_paths[runtime][key] = {"path", "title"}``. Windows and WSL
# can share one state directory, so each runtime keeps its own spelling and a path is never translated between them.
MAX_DIALOG_PATH = 4096
MAX_DIALOG_KEY = 128
MAX_DIALOG_ENTRIES = 128
MAX_DIALOG_TITLE = 200
MAX_DIALOG_RUNTIMES = 8
_DIALOG_LOCK = threading.Lock()


def _dialog_entries(document: dict, runtime: str) -> dict[str, dict]:
    """This runtime's valid entries in recency order (oldest first); anything malformed is dropped, not fatal."""
    runtimes = document.get("dialog_paths")
    entries = runtimes.get(runtime) if isinstance(runtimes, dict) else None
    if not isinstance(entries, dict):
        return {}
    valid = {}
    for key, entry in entries.items():
        path = entry.get("path") if isinstance(entry, dict) else None
        title = entry.get("title", "") if isinstance(entry, dict) else ""
        if (
            isinstance(key, str)
            and 0 < len(key) <= MAX_DIALOG_KEY
            and isinstance(path, str)
            and 0 < len(path) <= MAX_DIALOG_PATH
            and "\x00" not in path
            and isinstance(title, str)
        ):
            valid[key] = {"path": path, "title": title[:MAX_DIALOG_TITLE]}
    return valid


def load_dialog_paths(state: Path, *, runtime: str | None = None) -> dict[str, dict]:
    """Remembered dialog folders of this runtime as ``{key: {"path", "title"}}`` (oldest first)."""
    return _dialog_entries(_read(state), runtime or environment_key())


def save_dialog_path(state: Path, key: str, path: str, title: str = "", *, runtime: str | None = None) -> bool:
    """Remember ``path`` for ``key``; returns False when the value was already stored. Other keys are preserved."""
    runtime = runtime or environment_key()
    if not 0 < len(key) <= MAX_DIALOG_KEY or not 0 < len(path) <= MAX_DIALOG_PATH or "\x00" in path:
        raise SettingsError("Dialog folder cannot be remembered.")
    with _DIALOG_LOCK:
        document = _read(state)  # refuse to overwrite an unreadable or newer file
        entries = _dialog_entries(document, runtime)
        entry = {"path": path, "title": title[:MAX_DIALOG_TITLE]}
        if list(entries)[-1:] == [key] and entries[key] == entry:
            return False
        entries.pop(key, None)
        entries[key] = entry  # most recently used last
        while len(entries) > MAX_DIALOG_ENTRIES:
            entries.pop(next(iter(entries)))
        _store_dialog_entries(state, document, runtime, entries)
    return True


def forget_dialog_paths(state: Path, keys: list[str] | None = None, *, runtime: str | None = None) -> None:
    """Forget the given keys of this runtime, or every remembered folder of it when ``keys`` is None."""
    runtime = runtime or environment_key()
    with _DIALOG_LOCK:
        document = _read(state)
        entries = _dialog_entries(document, runtime)
        for key in list(entries) if keys is None else keys:
            entries.pop(key, None)
        _store_dialog_entries(state, document, runtime, entries)


def _store_dialog_entries(state: Path, document: dict, runtime: str, entries: dict[str, dict]) -> None:
    runtimes = document.get("dialog_paths")
    runtimes = dict(runtimes) if isinstance(runtimes, dict) else {}
    if entries:
        runtimes[runtime] = entries
    else:
        runtimes.pop(runtime, None)
    if len(runtimes) > MAX_DIALOG_RUNTIMES:
        raise SettingsError("Too many runtimes share remembered folders.")
    if runtimes:
        document["dialog_paths"] = runtimes
    else:
        document.pop("dialog_paths", None)
    _write(state, document)
