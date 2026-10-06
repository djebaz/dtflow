"""Durable Draw Things preset library and conservative DTFlow adapter.

The durable library stores the native Draw Things custom_configs.json array unchanged apart from JSON formatting. DTFlow
applies only fields whose semantics are verified and supported by the current generation form; every other field remains
in the native configuration for re-export.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from dtflow.core.models import LoRASelection, ValidationError

PRESETS_DIR = "presets"
PRESETS_FILE = "custom_configs.json"
MAX_PRESET_BYTES = 4 * 1024 * 1024
SAMPLER_FROM_DRAW_THINGS = {0: "dpmpp_2m_karras", 1: "euler_a"}
SAMPLER_TO_DRAW_THINGS = {value: key for key, value in SAMPLER_FROM_DRAW_THINGS.items()}


@dataclass(frozen=True, slots=True)
class PresetApplication:
    values: dict
    applied: tuple[str, ...]
    preserved: tuple[str, ...]


def preset_path(state: Path) -> Path:
    return Path(state) / PRESETS_DIR / PRESETS_FILE


def _read_json(path: Path):
    try:
        if path.stat().st_size > MAX_PRESET_BYTES:
            raise ValidationError("Preset file is larger than 4 MiB.")
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except ValidationError:
        raise
    except (OSError, ValueError) as exc:
        raise ValidationError(f"Preset JSON could not be read: {type(exc).__name__}.") from exc


def validate_native_presets(document: object) -> list[dict]:
    if not isinstance(document, list):
        raise ValidationError("Draw Things presets must be a JSON array.")
    result = []
    names = set()
    for index, item in enumerate(document, start=1):
        if not isinstance(item, dict):
            raise ValidationError(f"Preset {index} must be an object.")
        name, configuration = item.get("name"), item.get("configuration")
        if not isinstance(name, str) or not name.strip():
            raise ValidationError(f"Preset {index} requires a non-empty name.")
        if name in names:
            raise ValidationError(f"Preset name is duplicated: {name}")
        if not isinstance(configuration, dict):
            raise ValidationError(f"Preset {name} requires a configuration object.")
        names.add(name)
        result.append(dict(item))
    return result


def _atomic_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent, prefix=".presets-", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_presets(state: Path) -> tuple[dict, ...]:
    path = preset_path(state)
    if not path.exists():
        return ()
    return tuple(validate_native_presets(_read_json(path)))


def import_presets(source: Path, state: Path) -> int:
    presets = validate_native_presets(_read_json(Path(source)))
    _atomic_json(preset_path(state), presets)
    return len(presets)


def export_presets(state: Path, target: Path) -> int:
    presets = list(load_presets(state))
    _atomic_json(Path(target), presets)
    return len(presets)


def save_preset(state: Path, name: str, values: dict) -> None:
    name = name.strip()
    if not name:
        raise ValidationError("Preset name is required.")
    sampler = values.get("sampler")
    if sampler not in SAMPLER_TO_DRAW_THINGS:
        raise ValidationError("The selected sampler has no verified Draw Things preset mapping.")
    configuration = {
        "model": values["model"],
        "width": values["width"],
        "height": values["height"],
        "steps": values["steps"],
        "guidanceScale": values["guidance"],
        "seed": int(values["seed"]),
        "sampler": SAMPLER_TO_DRAW_THINGS[sampler],
        "loras": [
            {"file": item["identifier"], "mode": "all", "weight": item["weight"]} for item in values.get("loras", ())
        ],
    }
    presets = list(load_presets(state))
    replacement = {"name": name, "configuration": configuration}
    for index, item in enumerate(presets):
        if item["name"] == name:
            # Editing supported DTFlow fields must not discard Draw Things fields DTFlow does not currently apply.
            merged_configuration = dict(item["configuration"])
            merged_configuration.update(configuration)
            replacement = {**item, "name": name, "configuration": merged_configuration}
            presets[index] = replacement
            break
    else:
        presets.append(replacement)
    _atomic_json(preset_path(state), presets)


def apply_preset(preset: dict) -> PresetApplication:
    configuration = preset["configuration"]
    values = {}
    applied = []
    def take(key: str, target: str, kind):
        value = configuration.get(key)
        if type(value) is kind:
            values[target] = value
            applied.append(key)

    take("model", "model", str)
    take("width", "width", int)
    take("height", "height", int)
    take("steps", "steps", int)
    guidance = configuration.get("guidanceScale")
    if type(guidance) in (int, float):
        values["guidance"] = float(guidance)
        applied.append("guidanceScale")
    seed = configuration.get("seed")
    if type(seed) is int and 0 <= seed <= 4294967295:
        values["seed"] = str(seed)
        applied.append("seed")
    sampler = configuration.get("sampler")
    if type(sampler) is int and sampler in SAMPLER_FROM_DRAW_THINGS:
        values["sampler"] = SAMPLER_FROM_DRAW_THINGS[sampler]
        applied.append("sampler")

    loras = []
    raw_loras = configuration.get("loras")
    if isinstance(raw_loras, list):
        all_supported = True
        for item in raw_loras:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("file"), str)
                or type(item.get("weight")) not in (int, float)
                or item.get("mode", "all") != "all"
            ):
                all_supported = False
                break
            loras.append(LoRASelection(item["file"], float(item["weight"])))
        if all_supported:
            values["loras"] = tuple(loras)
            applied.append("loras")

    preserved = tuple(sorted(key for key in configuration if key not in applied))
    return PresetApplication(values, tuple(applied), preserved)
