"""Durable, credential-free export convenience preferences, separate from cache.

Windows and WSL can share one state directory, so each runtime keeps its own destination while the privacy,
sidecar and routing choices stay common. Schema 1 stored a single destination; it is read conservatively and
never converted into a counterpart path for another runtime.
"""

import json
from pathlib import Path, PureWindowsPath

from dtflow.core.models import ValidationError
from dtflow.persistence.runtime_environment import environment_key
from dtflow.services.artifacts import atomic_write

FILENAME = "export_preferences.json"
SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1
MAX_BYTES = 16384
MAX_DESTINATION = 4096
MAX_RUNTIMES = 8
MAX_RUNTIME_KEY = 32


def _legacy_runtime(destination: str, runtime: str) -> str | None:
    """Attribute a schema-1 destination by its path form; never translate it to another runtime's spelling."""
    if PureWindowsPath(destination).is_absolute():
        return "windows"
    if destination.startswith("/"):
        # A POSIX path in state that Windows also reads can only have been chosen by WSL.
        return "wsl" if runtime == "windows" else runtime
    return None


def _validated_destinations(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > MAX_RUNTIMES:
        raise ValidationError("Invalid export destination preference.")
    for key, destination in value.items():
        if (
            not isinstance(key, str)
            or not 0 < len(key) <= MAX_RUNTIME_KEY
            or not isinstance(destination, str)
            or len(destination) > MAX_DESTINATION
        ):
            raise ValidationError("Invalid export destination preference.")
    return dict(value)


def load_export_preferences(state: Path, runtime: str | None = None) -> dict:
    """Return the shared options plus this runtime's ``destination`` ('' when it has none); {} when no file exists."""
    runtime = runtime or environment_key()
    try:
        with (Path(state) / FILENAME).open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return {}
    if len(raw) > MAX_BYTES:
        raise ValidationError("Export preferences exceed the size limit.")
    value = json.loads(raw)
    version = value.get("schema_version") if isinstance(value, dict) else None
    if version not in (LEGACY_SCHEMA_VERSION, SCHEMA_VERSION):
        raise ValidationError("Unsupported export preferences; file left unchanged.")
    if version == LEGACY_SCHEMA_VERSION:
        legacy = value.get("destination")
        if not isinstance(legacy, str) or len(legacy) > MAX_DESTINATION:
            raise ValidationError("Invalid export destination preference.")
        owner = _legacy_runtime(legacy, runtime) if legacy else None
        destinations = {} if owner is None else {owner: legacy}
    else:
        destinations = _validated_destinations(value.get("destinations"))
    for key in ("include_prompts", "json_sidecars"):
        if type(value.get(key)) is not bool:
            raise ValidationError("Invalid export privacy preference.")
    if type(value.get("separate_noise_blank", True)) is not bool:
        raise ValidationError("Invalid export routing preference.")
    return {
        "destinations": destinations,
        "destination": destinations.get(runtime, ""),
        "include_prompts": value["include_prompts"],
        "json_sidecars": value["json_sidecars"],
        "separate_noise_blank": value.get("separate_noise_blank", True),
    }


def save_export_preferences(
    state: Path,
    destination: str,
    include_prompts: bool,
    json_sidecars: bool,
    separate_noise_blank: bool = True,
    *,
    runtime: str | None = None,
) -> None:
    runtime = runtime or environment_key()
    existing = load_export_preferences(state, runtime)  # refuse unreadable/newer schemas, without reset
    if len(destination) > MAX_DESTINATION:
        raise ValidationError("Export destination is too long to remember.")
    destinations = {**existing.get("destinations", {}), runtime: destination}
    if len(destinations) > MAX_RUNTIMES or len(runtime) > MAX_RUNTIME_KEY:
        raise ValidationError("Too many runtimes share these export preferences.")
    value = {
        "schema_version": SCHEMA_VERSION,
        "destinations": destinations,
        "include_prompts": include_prompts,
        "json_sidecars": json_sidecars,
        "separate_noise_blank": separate_noise_blank,
    }
    encoded = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise ValidationError("Export preferences exceed the size limit.")
    Path(state).mkdir(parents=True, exist_ok=True)
    atomic_write(Path(state) / FILENAME, encoded)
