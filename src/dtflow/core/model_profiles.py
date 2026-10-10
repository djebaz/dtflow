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


# Draw Things' public model list has no entry for FLUX.1 [dev]; the app describes it to DT+ itself (``override.models``).
# Mirrors the public FLUX.1 [schnell] entry with the dev differences: guidance-distilled (``guidance_embed``), no
# consistency model, 512-token padded T5 context. Unverified approximation of the app's own entry.
_FLUX1_DEV_SPEC = {
    "name": "FLUX.1 [dev]",
    "prefix": "",
    "version": "flux1",
    "upcast_attention": False,
    "default_scale": 16,
    "text_encoder": "t5_xxl_encoder_q6p.ckpt",
    "autoencoder": "flux_1_vae_f16.ckpt",
    "clip_encoder": "clip_vit_l14_f16.ckpt",
    "high_precision_autoencoder": True,
    "guidance_embed": True,
    "padded_text_encoding_length": 512,
    "hires_fix_scale": 24,
}


def fallback_spec(model_id: str) -> dict | None:
    """A hand-built model description for a checkpoint the cloud neither lists in its public zoo nor advertises."""
    if model_key(model_id) == "flux_1_dev":
        return {**_FLUX1_DEV_SPEC, "file": model_id}
    return None
