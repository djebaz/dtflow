"""Per-model generation profiles from the bundled Draw Things preset dataset (``model_profiles.json``).

A profile carries the recommended sampler, steps and guidance, the version-specific fields a family needs (for Flux:
T5 encoder, guidance embed, shift) and the samplers offered for the model. Lookup is by checkpoint name with the
quantization suffix ignored, then by base family; unknown models get no profile and keep today's behavior.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from dtflow.core.models import Sampler

_DATASET = Path(__file__).parents[1] / "resources" / "catalogue" / "model_profiles.json"
_SUFFIX = re.compile(r"(?:_(?:f16|f32|bf16|q\d+p?|\d+bit|i8x|svd))+$")


def model_key(file: str) -> str:
    """Checkpoint file name without extension and quantization suffix, e.g. ``flux_1_dev_q5p.ckpt`` -> ``flux_1_dev``."""
    return _SUFFIX.sub("", file.lower().removesuffix(".ckpt"))


@dataclass(frozen=True, slots=True)
class ModelProfile:
    name: str
    sampler: Sampler
    steps: int
    guidance: float
    extras: tuple[tuple[str, float | bool], ...]
    samplers: tuple[Sampler, ...]


def _profile(raw: dict) -> ModelProfile:
    return ModelProfile(
        raw["name"],
        Sampler(raw["sampler"]),
        raw["steps"],
        raw["guidance"],
        tuple(sorted(raw["extras"].items())),
        tuple(Sampler(item) for item in raw["samplers"]),
    )


@cache
def _load() -> tuple[dict[str, ModelProfile], dict[str, ModelProfile]]:
    try:
        document = json.loads(_DATASET.read_text(encoding="utf-8"))
        models = {model_key(file): _profile(raw) for file, raw in document["models"].items()}
        families = {key: _profile(raw) for key, raw in document["families"].items()}
    except (OSError, ValueError, KeyError, TypeError):
        return {}, {}
    return models, families


def profile_for(model_id: str, family: str | None = None) -> ModelProfile | None:
    """The checkpoint's own profile, else its family's default, else ``None``."""
    models, families = _load()
    return models.get(model_key(model_id)) or (families.get(family) if family else None)


_SPECS = Path(__file__).parents[1] / "resources" / "catalogue" / "community_model_specs.json"


@cache
def _community_specs() -> dict[str, dict]:
    try:
        document = json.loads(_SPECS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def fallback_spec(model_id: str) -> dict | None:
    """The Draw Things community description of a checkpoint, for models DT+ cloud neither lists publicly nor advertises.

    Bundled from the public ``community-models`` repository (``tools/drawthings/build_model_specs.py``). An exact file
    match wins; otherwise another quantization of the same model supplies the description with this file name.
    """
    specs = _community_specs()
    exact = specs.get(model_id)
    if exact is not None:
        return dict(exact)
    key = model_key(model_id)
    for file, spec in specs.items():
        if model_key(file) == key:
            return {**spec, "file": model_id}
    return None
