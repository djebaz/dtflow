"""Shared application settings (currently the cloud concurrency maximum) in ``<state>/settings.json``.

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


def load_cloud_concurrency(state: Path) -> int:
    path = Path(state) / "settings.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DEFAULT_CLOUD_CONCURRENCY
    except (OSError, ValueError) as exc:
        raise SettingsError("Settings file could not be read.") from exc
    if not isinstance(document, dict) or type(document.get("schema_version")) is not int:
        raise SettingsError("Settings file is malformed.")
    if document["schema_version"] != SCHEMA_VERSION:
        raise SettingsError("Unsupported settings schema version; the file was left unchanged.")
    return validate_concurrency(document.get("cloud_concurrency", DEFAULT_CLOUD_CONCURRENCY))


def save_cloud_concurrency(state: Path, value: int) -> Path:
    validate_concurrency(value)
    load_cloud_concurrency(state)  # refuse to overwrite an unreadable or newer file
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
            json.dump({"schema_version": SCHEMA_VERSION, "cloud_concurrency": value}, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target
