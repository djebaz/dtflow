"""Shared application settings (the cloud concurrency maximum and the workflow concurrency defaults) in ``<state>/settings.json``.

The setting is a user-selected client policy, not verified cloud entitlement. The file is credential-free, versioned
independently of the application and of the SQLite store, and written atomically. A newer schema is rejected rather
than rewritten.
"""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from dtflow.core.models import MAX_QUEUE_CONCURRENCY, ValidationError

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
