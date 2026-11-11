"""Bundled checkpoint catalogue grouping for backends missing model metadata."""

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

# Only these bundled catalogue family IDs also name one unambiguous Draw Things
# version. Broad groups such as flux_2, qwen_image and sdxl are NOT exact.
_EXACT_BUNDLED_FAMILIES = frozenset(("z_image", "krea_2", "moss_image"))


def bundled_exact_version(model_id: str) -> str | None:
    """Exact checkpoint version only when its bundled family identifies one model."""
    family = bundled_base_version(model_id)
    return family if family in _EXACT_BUNDLED_FAMILIES else None
