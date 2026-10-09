"""Durable, credential-free export convenience preferences, separate from cache."""

import json
from pathlib import Path

from dtflow.core.models import ValidationError
from dtflow.services.artifacts import atomic_write

FILENAME = "export_preferences.json"
SCHEMA_VERSION = 1


def load_export_preferences(state: Path) -> dict:
    try:
        with (Path(state) / FILENAME).open("rb") as handle:
            raw = handle.read(8193)
    except FileNotFoundError:
        return {}
    if len(raw) > 8192:
        raise ValidationError("Export preferences exceed the size limit.")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError("Unsupported export preferences; file left unchanged.")
    if not isinstance(value.get("destination"), str) or len(value["destination"]) > 4096:
        raise ValidationError("Invalid export destination preference.")
    for key in ("include_prompts", "json_sidecars"):
        if type(value.get(key)) is not bool:
            raise ValidationError("Invalid export privacy preference.")
    if type(value.get("separate_noise_blank", True)) is not bool:
        raise ValidationError("Invalid export routing preference.")
    return value


def save_export_preferences(
    state: Path, destination: str, include_prompts: bool, json_sidecars: bool, separate_noise_blank: bool = True
) -> None:
    load_export_preferences(state)  # refuse unreadable/newer schemas, without reset
    if len(destination) > 4096:
        raise ValidationError("Export destination is too long to remember.")
    value = {
        "schema_version": SCHEMA_VERSION,
        "destination": destination,
        "include_prompts": include_prompts,
        "json_sidecars": json_sidecars,
        "separate_noise_blank": separate_noise_blank,
    }
    encoded = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > 8192:
        raise ValidationError("Export preferences exceed the size limit.")
    Path(state).mkdir(parents=True, exist_ok=True)
    atomic_write(Path(state) / FILENAME, encoded)
