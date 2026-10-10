"""Base-model version fallback for backends whose inventory lists model files without metadata."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

_CATALOGUE = Path(__file__).parents[2] / "resources" / "catalogue"


@cache
def _bundled_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in ("Models_prime.json", "Models_niche.json"):
        try:
            records = json.loads((_CATALOGUE / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for item in records if isinstance(records, list) else ():
            if isinstance(item, dict) and isinstance(item.get("file"), str) and isinstance(item.get("base_model"), str):
                versions.setdefault(item["file"], item["base_model"])
    return versions


def bundled_base_version(model_id: str) -> str | None:
    """The bundled catalogue's base family for a checkpoint file; evidence-light, so only a fallback."""
    return _bundled_versions().get(model_id)


# The bundled catalogue names families (``flux_1``); the LoRA specification wants Draw Things' own version string.
_LORA_VERSIONS = {"flux_1": "flux1", "sd_1": "v1", "sdxl": "sdxl_base_v0.9"}


def lora_version(base_version: str | None, model_spec: dict | None = None) -> str | None:
    """Version string for a prepared LoRA's ``override.loras`` entry: the checkpoint description's own ``version`` when
    one is sent, else the Draw Things string for families whose catalogue name differs; other names pass through."""
    if model_spec and isinstance(model_spec.get("version"), str):
        return model_spec["version"]
    return _LORA_VERSIONS.get(base_version, base_version) if base_version else None
