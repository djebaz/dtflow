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
