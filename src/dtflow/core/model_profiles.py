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
_SUFFIX = re.compile(r"(?:_(?:f16|f32|bf16|q\d+p?|\d+bit|i\d+x|svd))+$")


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


def community_model_ids() -> frozenset[str]:
    """Exact checkpoint files the bundled Draw Things community descriptions cover (models only, never LoRAs/controls)."""
    return frozenset(_community_specs())


def fallback_spec(model_id: str) -> dict | None:
    """The Draw Things community description of this exact checkpoint file, for models DT+ cloud neither lists nor advertises.

    Bundled from the public ``community-models`` repository (``tools/drawthings/build_model_specs.py``). Only an exact
    file match is used: another quantization's description can carry different fields (``is_bf16``, ``mmdit``) and
    made the cloud drop the connection, so a file without its own entry sends no description.
    """
    spec = _community_specs().get(model_id) or _derived_spec(model_id)
    return dict(spec) if spec is not None else None


# Files the cloud serves but the community repository has no description for. Each is derived from a sibling checkpoint of
# the same model whose description is identical across all its published quantizations (only file, name and T5 encoder
# differ, as for flux_1_dev f16/q5p/q8p). A single sibling is not enough: a Qwen description copied from one bf16 sibling
# made the cloud drop the connection. Derived, not published by Draw Things. file -> (base file, name or None, T5 encoder).
_T5_F16 = "t5_xxl_encoder_f16.ckpt"
_T5_Q6P = "t5_xxl_encoder_q6p.ckpt"
_DERIVED: dict[str, tuple[str, str | None, str]] = {
    # Official Schnell is not in the repository; PixelWave Schnell 04 shares its architecture and fields.
    "flux_1_schnell_f16.ckpt": ("pixelwave_flux_1_schnell_04_q8p.ckpt", "FLUX.1 [schnell]", _T5_F16),
    "flux_1_schnell_q4p.ckpt": ("pixelwave_flux_1_schnell_04_q8p.ckpt", "FLUX.1 [schnell]", _T5_Q6P),
    "flux_1_dev_q6p.ckpt": ("flux_1_dev_q8p.ckpt", None, _T5_Q6P),
    "flux_1_dev_q5p_svd.ckpt": ("flux_1_dev_q5p.ckpt", None, _T5_Q6P),
    "flux_1_dev_de_distill_f16.ckpt": ("flux_1_dev_de_distill_q8p.ckpt", None, _T5_F16),
}


def _derived_spec(model_id: str) -> dict | None:
    derived = _DERIVED.get(model_id)
    base = _community_specs().get(derived[0]) if derived else None
    if derived is None or base is None:
        return None
    return {**base, "name": derived[1] or base["name"], "file": model_id, "text_encoder": derived[2]}
