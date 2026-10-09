"""Generate src/dtflow/resources/catalogue/model_profiles.json from Draw Things' community configs.json.

Usage: uv run python tools/drawthings/build_model_profiles.py [configs.json] [output.json]

Per-checkpoint settings (recommended sampler, steps, guidance, shift and version-specific fields) are copied from the
official presets. Compatible-sampler lists are inferred from the sampler family the presets use (flow models use the
Trailing samplers, older epsilon models do not); they are not Draw Things evidence and are marked as such.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dtflow.core.model_profiles import model_key  # noqa: E402

SOURCE = Path(__file__).with_name("draw_things_configs.json")
TARGET = ROOT / "src" / "dtflow" / "resources" / "catalogue" / "model_profiles.json"

# Draw Things sampler enum order (drawthings-py generated SamplerType).
SAMPLERS = (
    "dpmpp_2m_karras euler_a ddim plms dpmpp_sde_karras unipc lcm euler_a_substep dpmpp_sde_substep tcd "
    "euler_a_trailing dpmpp_sde_trailing dpmpp_2m_ays euler_a_ays dpmpp_sde_ays dpmpp_2m_trailing "
    "ddim_trailing unipc_trailing unipc_ays tcd_trailing"
).split()
FLOW_SAMPLERS = [x for x in SAMPLERS if x.endswith("_trailing")]
EPSILON_SAMPLERS = [x for x in SAMPLERS if not x.endswith("_trailing")]
# Fields that change how a model is run beyond DTFlow's own request fields; GenConfig keyword names.
EXTRAS = {
    "shift": "shift",
    "resolutionDependentShift": "resolution_dependent_shift",
    "guidanceEmbed": "guidance_embed",
    "speedUpWithGuidanceEmbed": "speed_up_with_guidance_embed",
    "t5TextEncoder": "t5_text_encoder",
    "clipSkip": "clip_skip",
    "teaCacheThreshold": "tea_cache_threshold",
    "teaCacheStart": "tea_cache_start",
    "teaCacheEnd": "tea_cache_end",
    "teaCacheMaxSkipSteps": "tea_cache_max_skip_steps",
}


def catalogue_families() -> dict[str, str]:
    families: dict[str, str] = {}
    for name in ("Models_prime.json", "Models_niche.json"):
        for item in json.loads((ROOT / "src/dtflow/resources/catalogue" / name).read_text(encoding="utf-8")):
            families[item["file"]] = item["base_model"]
    return families


def profile(preset: dict) -> dict:
    configuration = preset["configuration"]
    sampler = SAMPLERS[configuration["sampler"]]
    return {
        "name": preset["name"],
        "sampler": sampler,
        "steps": configuration["steps"],
        "guidance": configuration["guidanceScale"],
        # Epsilon models (SD 1.x/SDXL) run on the library defaults today; only flow models need extra fields.
        "extras": {EXTRAS[k]: v for k, v in configuration.items() if k in EXTRAS and sampler.endswith("_trailing")},
        "samplers": FLOW_SAMPLERS if sampler.endswith("_trailing") else EPSILON_SAMPLERS,
    }


def build(source: Path) -> dict:
    catalogue = catalogue_families()
    models: dict[str, dict] = {}
    families: dict[str, dict] = {}
    for preset in json.loads(source.read_text(encoding="utf-8")):
        file = preset["configuration"]["model"]
        entry = profile(preset)
        # Variant presets (Lightning, Turbo adapters) share a checkpoint; the plain preset describes the checkpoint.
        if " with " not in preset["name"]:
            models.setdefault(model_key(file), entry)
            keys = {preset.get("version")}
            if "SkyReels" not in preset["name"] and catalogue.get(file) not in (None, "unknown"):
                keys.add(catalogue[file])
            if not any(word in preset["name"] for word in ("Fill", "Edit")):
                for key in keys - {None}:
                    # The family default is its fullest (most-steps) preset: Flux 1 dev, not the 4-step schnell.
                    if key not in families or entry["steps"] > families[key]["steps"]:
                        families[key] = entry
    return {
        "schema": "dtflow.model-profiles",
        "version": 1,
        "source": "Draw Things community configs.json",
        "samplers_inferred": True,
        "families": dict(sorted(families.items())),
        "models": dict(sorted(models.items())),
    }


def main(argv: list[str]) -> int:
    source = Path(argv[1]) if len(argv) > 1 else SOURCE
    target = Path(argv[2]) if len(argv) > 2 else TARGET
    document = build(source)
    target.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {target}: {len(document['models'])} models, {len(document['families'])} families")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
